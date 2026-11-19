"""
Claude Personal Dashboard - collector
=====================================

Draait op de Mac mini (Windows). Verzamelt elke paar minuten:
  * de cijfers van je MT5-accounts (Blue Guardian, Leveraged, ...)
  * je Telenet TV-opnameruimte (via je bestaande Telenet MCP-server)
en schrijft alles naar dashboard.json in deze map. Google Drive voor desktop
zet dat bestand online; het dashboard op claude.ai leest het daar.

Gebruik:
  python collector.py            -> verzamel 1x en schrijf dashboard.json
  python collector.py --test     -> verzamel 1x, toon alles in de console, schrijf niets
  python collector.py --find-telenet -> zoek waar je Telenet MCP-server staat
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone

# Gebruik de certificaten van Windows zelf (zoals je browser). Nodig als een antivirus of
# netwerkfilter HTTPS-verkeer scant met een eigen certificaat. De beveiliging blijft aan.
try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

VERSION = 1
HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")
LOG_PATH = os.path.join(HERE, "collector.log")


# --------------------------------------------------------------------------
# Hulpfuncties
# --------------------------------------------------------------------------

def log(msg):
    line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  {msg}"
    print(line)
    try:
        # log klein houden: max ~200 KB
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > 200_000:
            with open(LOG_PATH, "r", encoding="utf-8", errors="ignore") as f:
                tail = f.readlines()[-500:]
            with open(LOG_PATH, "w", encoding="utf-8") as f:
                f.writelines(tail)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def now_local():
    return datetime.now().astimezone()


def iso(dt):
    return dt.isoformat(timespec="seconds")


def r2(x):
    try:
        return round(float(x), 2)
    except (TypeError, ValueError):
        return None


def load_config():
    if not os.path.exists(CONFIG_PATH):
        example = os.path.join(HERE, "config.example.json")
        raise SystemExit(
            f"config.json ontbreekt. Kopieer {example} naar config.json en vul je gegevens in."
        )
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


# --------------------------------------------------------------------------
# MetaTrader 5
# --------------------------------------------------------------------------

def _server_offset_seconds(mt5, symbols):
    """MT5 geeft tijden in servertijd. Schat het verschil met UTC (afgerond op een half uur)."""
    for sym in symbols:
        try:
            mt5.symbol_select(sym, True)
            tick = mt5.symbol_info_tick(sym)
            if tick and tick.time:
                diff = tick.time - time.time()
                # alleen bruikbaar als de tick recent is (markt open)
                return int(round(diff / 1800.0)) * 1800
        except Exception:
            pass
    return None


def collect_mt5_account(acc):
    """Leest 1 MT5-terminal uit. Logt NIET in en verandert niets: alleen lezen."""
    result = {
        "id": acc.get("id") or acc.get("name", "account").lower().replace(" ", "_"),
        "name": acc.get("name", "MT5"),
        "status": "error",
        "message": None,
        "positions": [],
        "trades": [],
        "curve": [],
    }
    try:
        import MetaTrader5 as mt5  # pip install MetaTrader5
    except ImportError:
        result["message"] = "Python-pakket MetaTrader5 ontbreekt (pip install MetaTrader5)"
        return result

    path = acc.get("terminal_path")
    try:
        ok = mt5.initialize(path=path, timeout=20000) if path else mt5.initialize(timeout=20000)
        if not ok:
            result["message"] = f"MT5 niet bereikbaar: {mt5.last_error()}"
            return result

        info = mt5.account_info()
        if info is None:
            result["message"] = "MT5 draait, maar er is geen account ingelogd"
            return result

        positions = mt5.positions_get() or []
        symbols = [p.symbol for p in positions] + ["XAUUSD", "EURUSD", "XAUUSD.", "EURUSD."]
        offset = _server_offset_seconds(mt5, symbols)
        if offset is None:
            offset = int(acc.get("server_utc_offset_hours", 2) * 3600)

        # Alle deals van het account (prop accounts zijn klein, dus dit is snel)
        deals = mt5.history_deals_get(datetime(2015, 1, 1), datetime.now() + timedelta(days=3)) or []

        # "vandaag" volgens de servertijd van de broker (zo rekenen prop firms de dagelijkse limiet)
        server_now = datetime.fromtimestamp(time.time() + offset, tz=timezone.utc)
        server_midnight = server_now.replace(hour=0, minute=0, second=0, microsecond=0)
        midnight_ts = server_midnight.timestamp()  # in "servertijd-epoch", vergelijkbaar met deal.time

        DEAL_BALANCE = getattr(mt5, "DEAL_TYPE_BALANCE", 2)
        DEAL_ENTRY_IN = getattr(mt5, "DEAL_ENTRY_IN", 0)

        day_pl = 0.0
        balance_running = 0.0
        curve = []
        trades = []
        for d in sorted(deals, key=lambda x: x.time_msc):
            amount = (d.profit or 0) + (d.commission or 0) + (d.swap or 0) + (getattr(d, "fee", 0) or 0)
            balance_running += amount
            if d.type == DEAL_BALANCE:
                curve.append({"t": d.time - offset, "balance": r2(balance_running)})
                continue
            if d.entry == DEAL_ENTRY_IN:
                # openen kost hooguit commissie; telt mee voor dag-P/L maar is geen gesloten trade
                if d.time >= midnight_ts:
                    day_pl += amount
                continue
            if d.time >= midnight_ts:
                day_pl += amount
            curve.append({"t": d.time - offset, "balance": r2(balance_running)})
            trades.append({
                "symbol": d.symbol,
                # bij een sluitende deal is het type tegengesteld aan de positie
                "side": "BUY" if d.type == 1 else "SELL",
                "volume": r2(d.volume),
                "profit": r2(amount),
                "closed_at": iso(datetime.fromtimestamp(d.time - offset, tz=timezone.utc).astimezone()),
            })

        start_balance = acc.get("start_balance")
        equity = float(info.equity)
        balance = float(info.balance)

        result.update({
            "status": "ok",
            "login": str(info.login),
            "server": info.server,
            "company": info.company,
            "currency": info.currency,
            "balance": r2(balance),
            "equity": r2(equity),
            "open_pl": r2(info.profit),
            "margin_free": r2(info.margin_free),
            # dag-P/L = gerealiseerd vandaag + wat nu openstaat
            "day_pl": r2(day_pl + float(info.profit)),
            "start_balance": start_balance,
            "total_pl": r2(equity - start_balance) if start_balance else None,
            "open_positions": len(positions),
            "profit_target_pct": acc.get("profit_target_pct"),
            "max_daily_loss_pct": acc.get("max_daily_loss_pct"),
            "max_drawdown_pct": acc.get("max_drawdown_pct"),
        })

        for p in positions:
            result["positions"].append({
                "symbol": p.symbol,
                "side": "BUY" if p.type == 0 else "SELL",
                "volume": r2(p.volume),
                "open_price": p.price_open,
                "current_price": p.price_current,
                "sl": p.sl or None,
                "tp": p.tp or None,
                "profit": r2(p.profit + (p.swap or 0)),
                "opened_at": iso(datetime.fromtimestamp(p.time - offset, tz=timezone.utc).astimezone()),
            })

        result["trades"] = trades[-25:][::-1]   # laatste 25, nieuwste eerst
        # balanscurve: max 100 punten, plus het huidige equity-punt
        curve = curve[-100:]
        curve.append({"t": int(time.time()), "balance": r2(equity)})
        result["curve"] = [
            {"t": iso(datetime.fromtimestamp(c["t"], tz=timezone.utc).astimezone()), "balance": c["balance"]}
            for c in curve
        ]
        return result
    except Exception as e:
        result["message"] = f"Fout bij uitlezen: {e}"
        log(traceback.format_exc())
        return result
    finally:
        try:
            mt5.shutdown()  # sluit enkel de verbinding van dit script, niet de terminal
        except Exception:
            pass


# --------------------------------------------------------------------------
# Telenet (via je bestaande Telenet MCP-server, over stdio)
# --------------------------------------------------------------------------

def _candidate_roots():
    home = os.path.expanduser("~")
    appdata = os.environ.get("APPDATA", os.path.join(home, "AppData", "Roaming"))
    local = os.environ.get("LOCALAPPDATA", os.path.join(home, "AppData", "Local"))
    roots = [os.path.join(appdata, "Claude")]
    pkgs = os.path.join(local, "Packages")
    if os.path.isdir(pkgs):
        for name in os.listdir(pkgs):
            if name.lower().startswith("claude"):
                roots.append(os.path.join(pkgs, name, "LocalCache", "Roaming", "Claude"))
    return roots


SKIP_DIRS = {"cache", "code cache", "gpucache", "vm_bundles", "blob_storage", "indexeddb",
             "local storage", "session storage", "logs", "crashpad", "dawncache", "shared dictionary"}


def find_telenet_server():
    """Zoekt de opstartopdracht van je Telenet MCP-server. Geeft (command, args, cwd, env, bron) of None."""
    # 1) claude_desktop_config.json (handmatig toegevoegde servers)
    for root in _candidate_roots():
        cfg = os.path.join(root, "claude_desktop_config.json")
        if os.path.exists(cfg):
            try:
                with open(cfg, "r", encoding="utf-8") as f:
                    servers = json.load(f).get("mcpServers", {})
                for name, s in servers.items():
                    if "telenet" in name.lower() and s.get("command"):
                        return s["command"], s.get("args", []), s.get("cwd"), s.get("env", {}), cfg
            except Exception:
                pass

    # 2) plugins: .mcp.json / plugin.json waarin "telenet" voorkomt
    for root in _candidate_roots():
        if not os.path.isdir(root):
            continue
        base_depth = root.rstrip("\\/").count(os.sep)
        for dirpath, dirnames, filenames in os.walk(root):
            depth = dirpath.count(os.sep) - base_depth
            dirnames[:] = [d for d in dirnames
                           if d.lower() not in SKIP_DIRS and d != "node_modules" and depth < 8]
            for fn in filenames:
                if fn not in (".mcp.json", "plugin.json", "mcp.json"):
                    continue
                full = os.path.join(dirpath, fn)
                try:
                    with open(full, "r", encoding="utf-8") as f:
                        text = f.read()
                    if "telenet" not in text.lower():
                        continue
                    data = json.loads(text)
                    servers = data.get("mcpServers", data)
                    if not isinstance(servers, dict):
                        continue
                    plugin_root = dirpath
                    if os.path.basename(dirpath) == ".claude-plugin":
                        plugin_root = os.path.dirname(dirpath)
                    for name, s in servers.items():
                        if isinstance(s, dict) and s.get("command") and "telenet" in (name + json.dumps(s)).lower():
                            def sub(v):
                                return v.replace("${CLAUDE_PLUGIN_ROOT}", plugin_root) if isinstance(v, str) else v
                            env = {k: sub(v) for k, v in (s.get("env") or {}).items()}
                            env.setdefault("CLAUDE_PLUGIN_ROOT", plugin_root)
                            return sub(s["command"]), [sub(a) for a in s.get("args", [])], sub(s.get("cwd")) or plugin_root, env, full
                except Exception:
                    pass

    # 3) je eigen projectmap
    proj = os.path.join(os.path.expanduser("~"), "Documents", "telenet-mcp")
    pkg = os.path.join(proj, "package.json")
    if os.path.exists(pkg):
        try:
            with open(pkg, "r", encoding="utf-8") as f:
                main = json.load(f).get("main") or "index.js"
            return "node", [os.path.join(proj, main)], proj, {}, pkg
        except Exception:
            pass
    return None


class McpStdioClient:
    """Minimale MCP-client: start de server, roept 1 tool aan, stopt de server."""

    def __init__(self, command, args, cwd=None, env=None, timeout=60):
        exe = command
        if os.name == "nt" and not os.path.splitext(command)[1]:
            exe = shutil.which(command) or command  # vindt node.exe / npx.cmd
        full_env = dict(os.environ)
        full_env.update(env or {})
        self.timeout = timeout
        self.proc = subprocess.Popen(
            [exe] + list(args), cwd=cwd or None, env=full_env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self._lines = []
        self._cv = threading.Condition()
        self._stderr = []
        threading.Thread(target=self._reader, daemon=True).start()
        threading.Thread(target=self._err_reader, daemon=True).start()
        self._id = 0

    def _reader(self):
        for raw in self.proc.stdout:
            with self._cv:
                self._lines.append(raw.decode("utf-8", errors="replace"))
                self._cv.notify_all()

    def _err_reader(self):
        for raw in self.proc.stderr:
            self._stderr.append(raw.decode("utf-8", errors="replace"))
            self._stderr = self._stderr[-30:]

    def _send(self, obj):
        self.proc.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))
        self.proc.stdin.flush()

    def request(self, method, params):
        self._id += 1
        my_id = self._id
        self._send({"jsonrpc": "2.0", "id": my_id, "method": method, "params": params})
        deadline = time.time() + self.timeout
        with self._cv:
            while True:
                for i, line in enumerate(self._lines):
                    try:
                        msg = json.loads(line)
                    except ValueError:
                        continue
                    if msg.get("id") == my_id:
                        del self._lines[i]
                        if "error" in msg:
                            raise RuntimeError(msg["error"].get("message", str(msg["error"])))
                        return msg.get("result")
                left = deadline - time.time()
                if left <= 0 or self.proc.poll() is not None:
                    err = "".join(self._stderr[-5:]).strip()
                    raise TimeoutError(f"geen antwoord van Telenet-server op {method}" + (f" ({err})" if err else ""))
                self._cv.wait(timeout=min(left, 1.0))

    def notify(self, method, params=None):
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def close(self):
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.terminate()
            self.proc.wait(timeout=5)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass


def _telenet_command(cfg):
    if cfg.get("command"):
        return cfg["command"], cfg.get("args", []), cfg.get("cwd"), cfg.get("env", {}), "config.json"
    return find_telenet_server()


class TelenetSession:
    """Start de Telenet MCP-server 1x en laat meerdere tools aanroepen."""

    def __init__(self, cfg):
        found = _telenet_command(cfg)
        if not found:
            raise RuntimeError("Telenet MCP-server niet gevonden. Vul 'telenet.command' en 'args' in config.json in.")
        command, args, cwd, env, self.source = found
        self.client = McpStdioClient(command, args, cwd, env, timeout=cfg.get("timeout_seconds", 90))
        self.client.request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "claude-personal-dashboard", "version": str(VERSION)},
        })
        self.client.notify("notifications/initialized")

    def call(self, tool, arguments=None):
        res = self.client.request("tools/call", {"name": tool, "arguments": arguments or {}})
        text = " ".join(c.get("text", "") for c in res.get("content", []) if c.get("type") == "text")
        if res.get("isError"):
            raise RuntimeError(text or f"{tool} gaf een fout")
        data = res.get("structuredContent")
        if data is None:
            try:
                data = json.loads(text) if text else {}
            except ValueError:
                data = {"text": text}
        return data

    def close(self):
        self.client.close()


def _episode_row(e):
    return {
        "eventId": e.get("eventId"),
        "title": e.get("titel"),
        "season": e.get("seizoen"),
        "episode": e.get("aflevering"),
        "start": e.get("start"),
        "hours": r2(e.get("uurOngeveer", e.get("uur"))),
        "watched": e.get("bekeken"),
        "status": e.get("status"),
        "expires": e.get("verlooptOp"),
    }


def collect_telenet(cfg):
    """Opnameruimte + volledige bibliotheek (reeksen met hun afleveringen)."""
    out = {"status": "error", "message": None}
    if not cfg.get("enabled", True):
        out.update(status="off", message="uitgeschakeld in config.json")
        return out
    sess = None
    try:
        sess = TelenetSession(cfg)
        rec = sess.call("telenet_list_recordings", {"limiet": 200, "sorteer": "ruimte"})
        q = rec.get("quota") or sess.call(cfg.get("tool", "telenet_quota"))
        out.update({
            "status": "ok",
            "used_hours": r2(q.get("gebruiktUur")),
            "total_hours": r2(q.get("totaalUur")),
            "free_hours": r2(q.get("vrijUur")),
            "used_pct": r2(q.get("procentGebruikt")),
        })
        library, problems = [], 0
        for o in rec.get("opnames", []):
            item = {
                "id": o.get("id"), "type": o.get("type"), "title": o.get("titel"),
                "channel": o.get("zender"), "hours": r2(o.get("uur")), "count": o.get("afleveringen"),
                "watched": o.get("bekeken"), "start": o.get("start"), "expires": o.get("verlooptOp"),
                "episodes": [],
            }
            if o.get("eventId"):  # losse opname (film, special, 1 aflevering)
                item["episodes"] = [_episode_row(dict(o, uurOngeveer=o.get("uur")))]
            elif o.get("showId") and cfg.get("library", True):
                try:
                    eps = sess.call("telenet_list_episodes", {"showId": o["showId"], "zender": o.get("zender")})
                    item["episodes"] = [_episode_row(e) for e in eps.get("afleveringen", [])]
                except Exception as e:
                    problems += 1
                    item["error"] = str(e)[:200]
            library.append(item)
        out["library"] = library
        if problems:
            out["message"] = f"{problems} reeks(en) konden niet volledig gelezen worden"
        return out
    except Exception as e:
        out["message"] = f"{e}"
        return out
    finally:
        if sess:
            sess.close()


# --------------------------------------------------------------------------
# Strava (eigen Strava API-app; tokens staan BUITEN de Drive-map)
# --------------------------------------------------------------------------

SECRETS_DIR = os.path.join(os.path.expanduser("~"), ".personal-dashboard")
STRAVA_SECRETS = os.path.join(SECRETS_DIR, "strava.json")
STRAVA_CACHE = os.path.join(SECRETS_DIR, "strava_cache.json")


def _http_json(url, data=None, headers=None, timeout=30):
    import urllib.parse
    import urllib.request
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _strava_token():
    with open(STRAVA_SECRETS, "r", encoding="utf-8") as f:
        s = json.load(f)
    if s.get("expires_at", 0) < time.time() + 120:
        t = _http_json("https://www.strava.com/oauth/token", {
            "client_id": s["client_id"], "client_secret": s["client_secret"],
            "grant_type": "refresh_token", "refresh_token": s["refresh_token"],
        })
        s.update(access_token=t["access_token"], refresh_token=t["refresh_token"], expires_at=t["expires_at"])
        with open(STRAVA_SECRETS, "w", encoding="utf-8") as f:
            json.dump(s, f)
    return s["access_token"]


PACE_SPORTS = {"Run", "TrailRun", "VirtualRun", "Walk", "Hike"}


def collect_strava(cfg):
    out = {"status": "error", "message": None, "activities": []}
    if not cfg.get("enabled", True):
        out.update(status="off", message="uitgeschakeld in config.json")
        return out
    if not os.path.exists(STRAVA_SECRETS):
        out["message"] = "Strava nog niet gekoppeld: start strava_koppelen.bat"
        return out
    try:
        token = _strava_token()
        acts = _http_json(f"https://www.strava.com/api/v3/athlete/activities?per_page={int(cfg.get('activities', 100))}",
                          headers={"Authorization": f"Bearer {token}"})
        rows = []
        for a in acts:
            dist_km = (a.get("distance") or 0) / 1000
            mov = a.get("moving_time") or 0
            sport = a.get("sport_type") or a.get("type")
            pace = None
            if sport in PACE_SPORTS and dist_km > 0.05:
                sec = mov / dist_km
                pace = f"{int(sec // 60)}:{int(round(sec % 60)):02d} /km"
            rows.append({
                "id": str(a.get("id")),
                "name": a.get("name"),
                "sport": sport,
                # Strava zet een Z achter de lokale tijd; die hoort er niet
                "start_local": (a.get("start_date_local") or "").rstrip("Z"),
                "distance_km": round(dist_km, 2),
                "moving_min": round(mov / 60),
                "pace": pace,
                "elevation_m": round(a.get("total_elevation_gain") or 0),
            })
        out.update(status="ok", activities=rows)
        return out
    except Exception as e:
        out["message"] = f"Strava: {e}"
        return out


def collect_strava_cached(cfg):
    every = cfg.get("every_minutes", 15)
    try:
        with open(STRAVA_CACHE, "r", encoding="utf-8") as f:
            cached = json.load(f)
        if cached.get("status") == "ok" and (time.time() - cached["_fetched"]) / 60 < every:
            log("Strava: hergebruik vorige meting")
            return {k: v for k, v in cached.items() if k != "_fetched"}
    except (OSError, ValueError, KeyError):
        pass
    log("Strava: activiteiten ophalen...")
    out = collect_strava(cfg)
    out["measured_at"] = iso(now_local())
    if out["status"] == "ok":
        try:
            os.makedirs(SECRETS_DIR, exist_ok=True)
            with open(STRAVA_CACHE, "w", encoding="utf-8") as f:
                json.dump(dict(out, _fetched=time.time()), f)
        except OSError:
            pass
    return out


TELENET_CACHE = os.path.join(HERE, ".telenet_cache.json")


def load_telenet_cache():
    try:
        with open(TELENET_CACHE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def collect_telenet_cached(cfg, force=False):
    """Telenet verandert traag: standaard maar 1x per 30 min echt opvragen."""
    every = cfg.get("every_minutes", 30)
    cached = load_telenet_cache()
    if cached and not force and cached.get("status") == "ok" and "library" in cached:
        age_min = (time.time() - cached.get("_fetched", 0)) / 60
        if age_min < every:
            log(f"Telenet: hergebruik meting van {age_min:.0f} min geleden")
            return {k: v for k, v in cached.items() if k != "_fetched"}
    log("Telenet: opnameruimte en bibliotheek ophalen...")
    out = collect_telenet(cfg)
    out["measured_at"] = iso(now_local())
    if out["status"] == "ok":
        try:
            with open(TELENET_CACHE, "w", encoding="utf-8") as f:
                json.dump(dict(out, _fetched=time.time()), f)
        except OSError:
            pass
    return out


# --------------------------------------------------------------------------
# Opdrachten vanuit het dashboard
# --------------------------------------------------------------------------
# Het dashboard zet een bestand cmd-*.json in de map "Dashboard-opdrachten" in je Drive.
# Dit script voert ENKEL de acties uit die hieronder toegestaan zijn, enkel voor opnames
# die echt in je bibliotheek staan, en enkel als de opdracht minder dan 15 minuten oud is.

ACTIONS_LOG = os.path.join(HERE, ".actions_log.json")
ALLOWED_ACTIONS = {"telenet_delete"}
MAX_COMMAND_AGE_MIN = 15


def commands_dir(config):
    return config.get("commands_dir") or os.path.join(os.path.dirname(HERE), "Dashboard-opdrachten")


def load_actions_log():
    try:
        with open(ACTIONS_LOG, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def save_action(entry):
    log_list = load_actions_log()
    log_list.insert(0, entry)
    try:
        with open(ACTIONS_LOG, "w", encoding="utf-8") as f:
            json.dump(log_list[:30], f)
    except OSError:
        pass


class Rejected(ValueError):
    """Opdracht geweigerd door de veiligheidscontroles (er werd niets uitgevoerd)."""


def _parse_time(value):
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def process_commands(config):
    """Voert opdrachten uit. Geeft True terug als er iets uitgevoerd werd."""
    folder = commands_dir(config)
    if not os.path.isdir(folder):
        return False
    files = sorted(f for f in os.listdir(folder) if f.startswith("cmd-") and f.endswith(".json"))
    if not files:
        return False
    tcfg = config.get("telenet", {})
    did_something = False
    for fn in files:
        path = os.path.join(folder, fn)
        entry = {"id": fn[:-5], "action": None, "status": "rejected", "message": None,
                 "titles": [], "hours": None, "count": 0, "done_at": None}
        try:
            with open(path, "r", encoding="utf-8") as f:
                cmd = json.load(f)
            entry["id"] = str(cmd.get("id") or entry["id"])
            entry["action"] = action = cmd.get("action")
            created = _parse_time(cmd.get("created_at"))
            if action not in ALLOWED_ACTIONS:
                raise Rejected(f"actie '{action}' is niet toegestaan")
            if not cmd.get("confirmed"):
                raise Rejected("opdracht was niet bevestigd")
            if not created or (datetime.now(timezone.utc) - created).total_seconds() > MAX_COMMAND_AGE_MIN * 60:
                raise Rejected(f"opdracht is ouder dan {MAX_COMMAND_AGE_MIN} minuten en werd niet uitgevoerd")

            if action == "telenet_delete":
                ids = [str(i) for i in (cmd.get("eventIds") or [])]
                if not ids or len(ids) > 100:
                    raise Rejected("geen of te veel opnames in de opdracht (max 100)")
                lib = (load_telenet_cache() or {}).get("library")
                if lib is None:
                    lib = collect_telenet_cached(tcfg, force=True).get("library") or []
                known = {}
                for item in lib:
                    for e in item.get("episodes", []):
                        if e.get("eventId") and e.get("status") != "planned":
                            known[e["eventId"]] = (item.get("title"), e)
                unknown = [i for i in ids if i not in known]
                if unknown:
                    raise Rejected(f"{len(unknown)} opname(s) staan niet (meer) in je bibliotheek")
                entry["count"] = len(ids)
                entry["hours"] = r2(sum((known[i][1].get("hours") or 0) for i in ids))
                titles = []
                for i in ids:
                    show, e = known[i]
                    t = show if not e.get("episode") else f"{show} S{e.get('season')}E{e.get('episode')}"
                    if t not in titles:
                        titles.append(t)
                entry["titles"] = titles[:30]
                log(f"Opdracht {entry['id']}: {len(ids)} opname(s) verwijderen ({entry['hours']} u)")
                sess = TelenetSession(tcfg)
                try:
                    res = sess.call("telenet_delete_recordings", {"eventIds": ids, "bevestigd": True})
                finally:
                    sess.close()
                entry["status"] = "ok"
                msg = res.get("text") if isinstance(res, dict) else None
                entry["message"] = (msg or json.dumps(res, ensure_ascii=False))[:300]
                try:
                    os.remove(TELENET_CACHE)  # bij de volgende meting alles opnieuw ophalen
                except OSError:
                    pass
                did_something = True
        except Rejected as e:
            entry["status"] = "rejected"
            entry["message"] = str(e)[:300]
        except Exception as e:
            entry["status"] = "error"
            entry["message"] = str(e)[:300]
        if entry["status"] != "ok":
            log(f"Opdracht {entry['id']}: {entry['status']} - {entry['message']}")
        entry["done_at"] = iso(now_local())
        save_action(entry)
        try:
            os.remove(path)
        except OSError:
            try:
                os.replace(path, path + ".done")
            except OSError:
                pass
        did_something = True
    return did_something


# --------------------------------------------------------------------------
# Hoofdprogramma
# --------------------------------------------------------------------------

def collect(config):
    started = now_local()
    accounts = []
    for acc in config.get("accounts", []):
        if acc.get("enabled", True) is False:
            continue
        log(f"MT5: {acc.get('name')} uitlezen...")
        accounts.append(collect_mt5_account(acc))

    telenet = collect_telenet_cached(config.get("telenet", {}))
    strava = collect_strava_cached(config.get("strava", {}))

    return {
        "strava": strava,
        "version": VERSION,
        "generated_at": iso(now_local()),
        "duration_s": round((now_local() - started).total_seconds(), 1),
        "host": socket.gethostname(),
        "interval_minutes": config.get("interval_minutes", 5),
        "accounts": accounts,
        "telenet": telenet,
        "actions": load_actions_log()[:10],
    }


def write_output(data, config):
    path = config.get("output_file") or "dashboard.json"
    if not os.path.isabs(path):
        path = os.path.join(HERE, path)
    # Google Drive geeft het bestand aan het dashboard terug als (markdown-)tekst.
    # Daarom: geen backslashes of dubbele aanhalingstekens in teksten, en alles op 1 regel.
    def clean(v):
        if isinstance(v, str):
            return v.replace("\\", "/").replace('"', "'").replace("\n", " ")
        if isinstance(v, list):
            return [clean(x) for x in v]
        if isinstance(v, dict):
            return {k: clean(x) for k, x in v.items()}
        return v
    text = json.dumps(clean(data), ensure_ascii=False, separators=(",", ":"))
    # bewust "in place" schrijven (niet via een tijdelijk bestand + hernoemen):
    # zo houdt Google Drive hetzelfde bestand (en dezelfde file-ID) die het dashboard leest
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


STATE_PATH = os.path.join(HERE, ".collector_state.json")
LOCK_PATH = os.path.join(HERE, ".collector.lock")


def acquire_lock():
    """Voorkomt dat twee runs tegelijk lopen (de taak start elke minuut)."""
    try:
        if os.path.exists(LOCK_PATH) and time.time() - os.path.getmtime(LOCK_PATH) > 600:
            os.remove(LOCK_PATH)  # achtergebleven van een vastgelopen run
        fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True
    except OSError:
        return False


def release_lock():
    try:
        os.remove(LOCK_PATH)
    except OSError:
        pass


def main():
    args = sys.argv[1:]
    if "--find-telenet" in args:
        found = find_telenet_server()
        if found:
            cmd, a, cwd, env, src = found
            print("Gevonden in:", src)
            print(json.dumps({"command": cmd, "args": a, "cwd": cwd}, indent=2))
        else:
            print("Niet gevonden. Vul telenet.command / args in config.json zelf in.")
        return

    config = load_config()
    if not acquire_lock():
        return
    try:
        executed = False
        if "--test" not in args:
            executed = process_commands(config)

        # De taak draait elke minuut (voor snelle opdrachten); een volledige meting
        # gebeurt enkel om de interval_minutes, of meteen na een uitgevoerde opdracht.
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                state = json.load(f)
        except (OSError, ValueError):
            state = {}
        interval = config.get("interval_minutes", 5)
        due = time.time() - state.get("last_full", 0) >= interval * 60 - 20
        if not (due or executed or "--test" in args or "--now" in args):
            return

        data = collect(config)
        for acc in data["accounts"]:
            if acc["status"] == "ok":
                log(f"  {acc['name']}: equity {acc['equity']} {acc['currency']}, vandaag {acc['day_pl']}, {acc['open_positions']} open")
            else:
                log(f"  {acc['name']}: FOUT - {acc['message']}")
        t = data["telenet"]
        if t["status"] == "ok":
            n = sum(len(i.get("episodes", [])) for i in t.get("library", []))
            log(f"  Telenet: {t.get('used_pct')}% gebruikt, {len(t.get('library', []))} titels / {n} opnames")
        else:
            log(f"  Telenet: {t['status']} - {t.get('message')}")
        s = data["strava"]
        log(f"  Strava: {len(s['activities'])} activiteiten" if s["status"] == "ok" else f"  Strava: {s['status']} - {s.get('message')}")

        if "--test" in args:
            print(json.dumps(data, ensure_ascii=False, indent=2))
            return
        path = write_output(data, config)
        state["last_full"] = time.time()
        try:
            with open(STATE_PATH, "w", encoding="utf-8") as f:
                json.dump(state, f)
        except OSError:
            pass
        log(f"Geschreven: {path} ({data['duration_s']} s)")
    finally:
        release_lock()


if __name__ == "__main__":
    main()
