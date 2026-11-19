"""
Telegram-bot van het dashboard: beantwoordt je vragen met de gegevens uit dashboard.json.

- Antwoordt ENKEL op jouw chat (chat_id uit telegram.json); andere berichten worden genegeerd.
- Leest alleen: verwijdert of verandert niets (geen trades, geen opnames).
- Draait op de achtergrond op de Mac mini. collector.py start hem vanzelf opnieuw als hij stilvalt.

Commando's: /vandaag /agenda /morgen /jarig /training /herstel /forex /telenet /ochtend /help
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import telegram_bericht as tb  # noqa: E402

HEARTBEAT = os.path.join(tb.SECRETS_DIR, "bot_heartbeat.txt")
OFFSET = os.path.join(tb.SECRETS_DIR, "bot_offset.txt")
LOG = os.path.join(HERE, "telegram_bot.log")
DAGEN = ["ma", "di", "wo", "do", "vr", "za", "zo"]
TYPE = {"rustig": "rustige loop", "lang": "lange duurloop", "tempo": "tempoloop", "interval": "intervallen",
        "mt": "marathontempo", "test": "test", "race": "marathon"}


def log(msg):
    try:
        if os.path.exists(LOG) and os.path.getsize(LOG) > 100_000:
            os.replace(LOG, LOG + ".oud")
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}\n")
    except OSError:
        pass


def api(cfg, method, params, timeout=30):
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{cfg['bot_token']}/{method}", data=data)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def load_data():
    with open(os.path.join(HERE, "dashboard.json"), "r", encoding="utf-8") as f:
        return json.load(f)


def _d(iso):
    return date.fromisoformat(iso[:10])


def _dag(d):
    t = date.today()
    if d == t:
        return "vandaag"
    if d == t + timedelta(days=1):
        return "morgen"
    return f"{DAGEN[d.weekday()]} {d.day} {tb.MAANDEN[d.month - 1]}"


def _ev_line(e):
    tijd = "hele dag" if e["all_day"] else e["start"][11:16]
    extra = f" ({tb._esc(e['location'])})" if e.get("location") else ""
    return f"   {tijd} · {tb._esc(e['title'])}{extra}"


def _events_on(ag, d):
    iso = d.isoformat()
    out = []
    for e in ag.get("events", []):
        s = e["start"][:10]
        if e["all_day"]:
            end = (e.get("end") or s)[:10]
            if s <= iso and (end > iso or (s == iso and end == s)):
                out.append(e)
        elif s == iso:
            out.append(e)
    return sorted(out, key=lambda e: (not e["all_day"], e["start"]))


def _stamp(data):
    try:
        return f"\n<i>bijgewerkt {datetime.fromisoformat(data['generated_at']):%H:%M}</i>"
    except (KeyError, ValueError):
        return ""


# ---------- antwoorden ----------

def r_agenda(data, dagen=7):
    ag = data.get("agenda") or {}
    if ag.get("status") != "ok":
        return "📅 Agenda niet beschikbaar: " + tb._esc(ag.get("message") or "nog niet gekoppeld")
    lines = [f"📅 <b>Agenda, komende {dagen} dagen</b>"]
    t = date.today()
    found = False
    for i in range(dagen):
        d = t + timedelta(days=i)
        evs = _events_on(ag, d)
        if evs:
            found = True
            lines.append(f"\n<b>{_dag(d).capitalize()}</b>")
            lines += [_ev_line(e) for e in evs[:8]]
    if not found:
        lines.append("Niets gepland.")
    return "\n".join(lines)


def r_dag(data, d, titel):
    ag = data.get("agenda") or {}
    lines = [f"📅 <b>{titel}</b> · {DAGEN[d.weekday()]} {d.day} {tb.MAANDEN[d.month - 1]}"]
    if ag.get("status") == "ok":
        evs = _events_on(ag, d)
        lines += [_ev_line(e) for e in evs] if evs else ["   Niets gepland."]
        jarig = [b for b in ag.get("birthdays", []) if b["date"] == d.isoformat()]
        for b in jarig:
            lines.append(f"🎂 {tb._esc(b['name'])} is jarig" + (f" ({b['age']})" if b.get("age") else ""))
    else:
        lines.append("   Agenda niet beschikbaar.")
    m = data.get("marathon") or {}
    if m.get("status") == "ok":
        for w in m.get("weeks", []):
            for s in w.get("sessions", []):
                if s["date"] == d.isoformat() and s.get("km"):
                    lines.append(f"🏃 Training: {TYPE.get(s['type'], s['type'])} {tb._num(s['km'], 0 if float(s['km']).is_integer() else 1)} km"
                                 + (f" @ {s['pace']}" if s.get("pace") else "")
                                 + (" ✓ gehaald" if s.get("status") == "done" else ""))
    return "\n".join(lines)


def r_jarig(data):
    ag = data.get("agenda") or {}
    if ag.get("status") != "ok":
        return "🎂 Verjaardagen niet beschikbaar."
    bd = ag.get("birthdays", [])[:12]
    if not bd:
        return "🎂 Geen verjaardagen in de komende 60 dagen."
    lines = ["🎂 <b>Verjaardagen</b>"]
    for b in bd:
        wanneer = "vandaag!" if b["days"] == 0 else "morgen" if b["days"] == 1 else f"{_dag(_d(b['date']))} (over {b['days']} d)"
        lines.append(f"   {tb._esc(b['name'])}" + (f" wordt {b['age']}" if b.get("age") else "") + f" · {wanneer}")
    return "\n".join(lines)


def r_training(data):
    m = data.get("marathon") or {}
    if m.get("status") != "ok":
        return "🏃 Geen marathonplan gevonden."
    td = m.get("today") or {}
    lines = [f"🏃 <b>Marathon Kortrijk</b> · nog {m.get('days_left')} dagen"]
    if td.get("type") == "rust":
        lines.append(f"Vandaag: {tb._esc(td.get('label', 'rustdag'))}. {tb._esc(td.get('notes') or '')}")
    else:
        lines.append(f"Vandaag: <b>{tb._esc(td.get('label'))} {tb._num(td.get('km'), 0 if float(td.get('km') or 0).is_integer() else 1)} km</b>"
                     + (f" @ {td['pace']}" if td.get("pace") else ""))
        if td.get("run"):
            lines.append(f"   gelopen: {tb._num(td['run']['km'])} km aan {td['run'].get('pace') or '?'}")
        if td.get("advice"):
            lines.append(f"   💡 {tb._esc(td['advice'])}")
    wk = m.get("week_no")
    if wk:
        w = m["weeks"][wk - 1]
        lines.append(f"\n<b>Week {w['week']} · {w['phase']}</b>: {w['done']}/{w['count']} trainingen, {tb._num(w['actual_km'])} van {tb._num(w['planned_km'], 0)} km")
        st = {"done": "✓", "partial": "◐", "short": "◔", "missed": "✗", "open": "•", "planned": "·"}
        for s in w["sessions"]:
            if s.get("km"):
                lines.append(f"   {st.get(s.get('status'), '·')} {DAGEN[_d(s['date']).weekday()]} {TYPE.get(s['type'], s['type'])} {tb._num(s['km'], 0 if float(s['km']).is_integer() else 1)} km")
    for i in (m.get("insights") or [])[1:3]:
        lines.append(f"• {tb._esc(i)}")
    return "\n".join(lines)


def r_herstel(data):
    w = data.get("whoop") or {}
    if w.get("status") != "ok":
        return "❤️ Whoop niet beschikbaar: " + tb._esc(w.get("message") or "")
    td = w.get("today") or {}
    rec = td.get("recovery")
    dot = "🟢" if rec is not None and rec >= 67 else "🟡" if rec is not None and rec >= 34 else "🔴" if rec is not None else "⚪"
    lines = [f"{dot} <b>Herstel {tb._num(rec, 0)}%</b>",
             f"   HRV {tb._num(td.get('hrv'), 0)} ms · rustpols {td.get('rhr', '–')}"]
    if td.get("sleep_h"):
        h = td["sleep_h"]
        lines.append(f"😴 Slaap {int(h)}u{int(round((h % 1) * 60)):02d}" + (f" ({tb._num(td.get('sleep_perf'), 0)}%)" if td.get("sleep_perf") else ""))
    wt = w.get("weight") or {}
    if wt.get("kg"):
        lines.append(f"⚖️ {tb._num(wt['kg'])} kg · doel 95 kg (nog {tb._num(max(0, wt['kg'] - 95))} kg)")
    return "\n".join(lines)


def r_forex(data):
    accs = data.get("accounts") or []
    if not accs:
        return "📈 Geen forex-accounts."
    lines = ["📈 <b>Forex</b>"]
    for a in accs:
        if a.get("status") != "ok":
            lines.append(f"   {tb._esc(a.get('name'))}: geen data ({tb._esc(a.get('message') or '')})")
            continue
        lines.append(f"<b>{tb._esc(a['name'])}</b>: {tb._money(a.get('equity'), a.get('currency'))}")
        lines.append(f"   vandaag {'+' if (a.get('day_pl') or 0) >= 0 else ''}{tb._money(a.get('day_pl'), a.get('currency'))} · {a.get('open_positions', 0)} open")
        for p in (a.get("positions") or [])[:5]:
            lines.append(f"   {p['side']} {p['symbol']} {p['volume']} lot · {tb._money(p.get('profit'), a.get('currency'))}")
    return "\n".join(lines)


def r_telenet(data):
    t = data.get("telenet") or {}
    if t.get("status") != "ok":
        return "📺 Telenet niet beschikbaar."
    can = 0.0
    n = 0
    now = time.time()
    for it in t.get("library", []):
        for e in it.get("episodes", []):
            if e.get("status") == "planned":
                continue
            try:
                old = now - datetime.fromisoformat(str(e.get("start")).replace("Z", "+00:00")).timestamp() > 182.5 * 86400
            except ValueError:
                old = False
            if e.get("watched") == "alreadyWatched" or (e.get("watched") == "notWatched" and old):
                can += e.get("hours") or 0
                n += 1
    return (f"📺 <b>Telenet {tb._num(t.get('used_pct'), 0)}% vol</b>\n"
            f"   {tb._num(t.get('free_hours'))} u vrij van {tb._num(t.get('total_hours'), 0)} u\n"
            f"   {n} opnames ({tb._num(can)} u) kunnen weg. Verwijderen doe je via het dashboard.")


def r_vandaag(data):
    parts = [r_dag(data, date.today(), "Vandaag")]
    w = data.get("whoop") or {}
    if w.get("status") == "ok":
        parts.append(r_herstel(data))
    return "\n\n".join(parts)


HELP = ("Wat wil je weten?\n"
        "/vandaag – afspraken, training en herstel van vandaag\n"
        "/morgen – wat staat er morgen op de planning\n"
        "/agenda – de komende 7 dagen\n"
        "/jarig – verjaardagen die eraan komen\n"
        "/training – marathon: vandaag en deze week\n"
        "/herstel – Whoop: herstel, slaap, gewicht\n"
        "/forex – je accounts en open posities\n"
        "/telenet – opnameruimte\n"
        "/ochtend – het ochtendbericht nu")


def answer(text, cfg):
    cmd = (text or "").strip().split()[0].lower().split("@")[0] if text else ""
    woorden = (text or "").lower()
    try:
        data = load_data()
    except (OSError, ValueError):
        return "Ik kan dashboard.json nu niet lezen. Draait de collector op de Mac mini?"
    if cmd in ("/start", "/help", "help", "?"):
        return HELP
    if cmd == "/ochtend":
        return tb.compose(data, cfg.get("dashboard_url"))
    route = {"/vandaag": r_vandaag, "/agenda": r_agenda, "/jarig": r_jarig, "/training": r_training,
             "/herstel": r_herstel, "/forex": r_forex, "/telenet": r_telenet}
    if cmd == "/morgen":
        return r_dag(data, date.today() + timedelta(days=1), "Morgen") + _stamp(data)
    if cmd in route:
        return route[cmd](data) + _stamp(data)
    # eenvoudige herkenning van gewone zinnen
    for keys, fn in ((("verjaard", "jarig"), r_jarig), (("morgen",), lambda d: r_dag(d, date.today() + timedelta(days=1), "Morgen")),
                     (("agenda", "afspra", "gepland", "week"), r_agenda), (("train", "lopen", "marathon", "loop"), r_training),
                     (("herstel", "slaap", "gewicht", "whoop", "hrv"), r_herstel), (("forex", "trade", "account", "equity"), r_forex),
                     (("telenet", "opname"), r_telenet), (("vandaag",), r_vandaag)):
        if any(k in woorden for k in keys):
            return fn(data) + _stamp(data)
    return "Dat begrijp ik nog niet.\n\n" + HELP


def main():
    cfg = tb.load()
    if not cfg or not cfg.get("bot_token"):
        log("geen telegram.json: start telegram_koppelen.bat")
        return
    chat = str(cfg["chat_id"])
    os.makedirs(tb.SECRETS_DIR, exist_ok=True)
    try:
        offset = int(open(OFFSET).read().strip())
    except (OSError, ValueError):
        offset = 0
    try:
        api(cfg, "setMyCommands", {"commands": json.dumps([
            {"command": c, "description": d} for c, d in [
                ("vandaag", "Afspraken, training en herstel van vandaag"), ("morgen", "Planning voor morgen"),
                ("agenda", "Komende 7 dagen"), ("jarig", "Verjaardagen"), ("training", "Marathon: vandaag en deze week"),
                ("herstel", "Whoop: herstel, slaap, gewicht"), ("forex", "Accounts en open posities"),
                ("telenet", "Opnameruimte"), ("ochtend", "Ochtendbericht nu"), ("help", "Overzicht")]])})
    except Exception as e:
        log(f"setMyCommands: {e}")
    log("bot gestart")
    fouten = 0
    while True:
        try:
            with open(HEARTBEAT, "w") as f:
                f.write(str(time.time()))
            res = api(cfg, "getUpdates", {"offset": offset, "timeout": 50, "allowed_updates": json.dumps(["message"])}, timeout=65)
            fouten = 0
            for u in res.get("result", []):
                offset = u["update_id"] + 1
                with open(OFFSET, "w") as f:
                    f.write(str(offset))
                msg = u.get("message") or {}
                if str((msg.get("chat") or {}).get("id")) != chat:
                    continue  # enkel jij
                if time.time() - msg.get("date", 0) > 600:
                    continue  # oude berichten (bv. toen de Mac mini uit stond) niet meer beantwoorden
                text = msg.get("text") or ""
                try:
                    reply = answer(text, cfg)
                except Exception as e:
                    log(f"fout bij '{text[:40]}': {e}")
                    reply = "Er ging iets mis bij het opzoeken. Probeer het zo nog eens."
                api(cfg, "sendMessage", {"chat_id": chat, "text": reply[:4000], "parse_mode": "HTML",
                                         "disable_web_page_preview": "true"})
        except urllib.error.HTTPError as e:
            if e.code == 409:
                log("een andere bot-instantie draait al; ik stop")
                return
            if e.code == 401:
                log("token ongeldig; start telegram_koppelen.bat opnieuw")
                return
            fouten += 1
            log(f"HTTP {e.code}")
            time.sleep(min(60, 5 * fouten))
        except Exception as e:
            fouten += 1
            log(f"netwerk: {e}")
            time.sleep(min(60, 5 * fouten))


if __name__ == "__main__":
    main()
