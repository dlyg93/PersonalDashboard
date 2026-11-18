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


def collect_telenet(cfg):
    out = {"status": "error", "message": None}
    if not cfg.get("enabled", True):
        out.update(status="off", message="uitgeschakeld in config.json")
        return out
    if cfg.get("command"):
        found = (cfg["command"], cfg.get("args", []), cfg.get("cwd"), cfg.get("env", {}), "config.json")
    else:
        found = find_telenet_server()
    if not found:
        out["message"] = "Telenet MCP-server niet gevonden. Vul 'telenet.command' en 'args' in config.json in."
        return out
    command, args, cwd, env, source = found
    client = None
    try:
        client = McpStdioClient(command, args, cwd, env, timeout=cfg.get("timeout_seconds", 90))
        client.request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "claude-personal-dashboard", "version": str(VERSION)},
        })
        client.notify("notifications/initialized")
        res = client.request("tools/call", {"name": cfg.get("tool", "telenet_quota"), "arguments": {}})
        if res.get("isError"):
            raise RuntimeError(" ".join(c.get("text", "") for c in res.get("content", [])))
        data = res.get("structuredContent")
        if not data:
            text = next((c.get("text") for c in res.get("content", []) if c.get("type") == "text"), "{}")
            data = json.loads(text)
        out.update({
            "status": "ok",
            "used_hours": r2(data.get("gebruiktUur")),
            "total_hours": r2(data.get("totaalUur")),
            "free_hours": r2(data.get("vrijUur")),
            "used_pct": r2(data.get("procentGebruikt")),
        })
        return out
    except Exception as e:
        out["message"] = f"{e} (server uit {source})"
        return out
    finally:
        if client:
            client.close()


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


def collect_telenet_cached(cfg):
    """Telenet verandert traag: standaard maar 1x per 30 min echt opvragen."""
    every = cfg.get("every_minutes", 30)
    try:
        with open(TELENET_CACHE, "r", encoding="utf-8") as f:
            cached = json.load(f)
        age_min = (time.time() - cached["_fetched"]) / 60
        if cached.get("status") == "ok" and age_min < every:
            log(f"Telenet: hergebruik meting van {age_min:.0f} min geleden")
            return {k: v for k, v in cached.items() if k != "_fetched"}
    except (OSError, ValueError, KeyError):
        pass
    log("Telenet: opnameruimte ophalen...")
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
    data = collect(config)
    for acc in data["accounts"]:
        if acc["status"] == "ok":
            log(f"  {acc['name']}: equity {acc['equity']} {acc['currency']}, vandaag {acc['day_pl']}, {acc['open_positions']} open")
        else:
            log(f"  {acc['name']}: FOUT - {acc['message']}")
    t = data["telenet"]
    log(f"  Telenet: {t.get('used_pct')}% gebruikt" if t["status"] == "ok" else f"  Telenet: {t['status']} - {t.get('message')}")
    s = data["strava"]
    log(f"  Strava: {len(s['activities'])} activiteiten" if s["status"] == "ok" else f"  Strava: {s['status']} - {s.get('message')}")

    if "--test" in args:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    path = write_output(data, config)
    log(f"Geschreven: {path} ({data['duration_s']} s)")


if __name__ == "__main__":
    main()
