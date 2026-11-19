"""Dagelijks ochtendbericht via Telegram, met inzichten van de dag."""
import json
import os
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

SECRETS_DIR = os.path.join(os.path.expanduser("~"), ".personal-dashboard")
SECRETS = os.path.join(SECRETS_DIR, "telegram.json")
DAGEN = ["maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag", "zondag"]
MAANDEN = ["jan", "feb", "mrt", "apr", "mei", "jun", "jul", "aug", "sep", "okt", "nov", "dec"]


def load():
    try:
        with open(SECRETS, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def send(cfg, text):
    data = urllib.parse.urlencode({"chat_id": cfg["chat_id"], "text": text, "parse_mode": "HTML",
                                   "disable_web_page_preview": "true"}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{cfg['bot_token']}/sendMessage", data=data)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def _num(v, d=1):
    return f"{v:.{d}f}".replace(".", ",") if isinstance(v, (int, float)) else "–"


def _money(v, cur="USD"):
    if v is None:
        return "–"
    s = f"{abs(v):,.2f}".replace(",", " ").replace(".", ",").replace(" ", ".")
    sym = "$" if cur == "USD" else "€" if cur == "EUR" else cur + " "
    return ("−" if v < 0 else "") + sym + s


def compose(data, dashboard_url=None):
    t = date.today()
    lines = [f"☀️ <b>Goeiemorgen Laurenz</b> — {DAGEN[t.weekday()]} {t.day} {MAANDEN[t.month - 1]}"]

    w = data.get("whoop") or {}
    if w.get("status") == "ok":
        td = w.get("today") or {}
        rec = td.get("recovery")
        dot = "🟢" if rec is not None and rec >= 67 else "🟡" if rec is not None and rec >= 34 else "🔴" if rec is not None else "⚪"
        parts = [f"{dot} Herstel <b>{_num(rec, 0)}%</b>" if rec is not None else "⚪ Herstel nog niet berekend"]
        if td.get("hrv"): parts.append(f"HRV {_num(td['hrv'], 0)} ms")
        if td.get("rhr"): parts.append(f"rustpols {td['rhr']}")
        lines.append(" · ".join(parts))
        if td.get("sleep_h"):
            h = td["sleep_h"]; lines.append(f"😴 Slaap {int(h)}u{int(round((h % 1) * 60)):02d}" + (f" ({_num(td.get('sleep_perf'), 0)}% van wat je nodig had)" if td.get("sleep_perf") else ""))
        # trend HRV
        ser = [d for d in w.get("series", []) if d.get("hrv")]
        if len(ser) >= 8 and td.get("hrv"):
            avg = sum(d["hrv"] for d in ser[-8:-1]) / 7
            diff = (td["hrv"] - avg) / avg * 100
            if abs(diff) >= 10:
                lines.append(f"{'📈' if diff > 0 else '📉'} HRV {_num(abs(diff), 0)}% {'boven' if diff > 0 else 'onder'} je weekgemiddelde")
        wt = (w.get("weight") or {})
        hist = wt.get("history") or []
        if wt.get("kg"):
            msg = f"⚖️ Gewicht {_num(wt['kg'])} kg"
            older = [h for h in hist if h["date"] <= (t - timedelta(days=30)).isoformat()]
            old = older[-1] if older else None
            if old:
                d = wt["kg"] - old["kg"]
                msg += f" ({'+' if d > 0 else '−'}{_num(abs(d))} kg op 30 dagen)"
            msg += " · doel 95 kg"
            lines.append(msg)

    m = data.get("marathon") or {}
    if m.get("status") == "ok":
        td = m.get("today") or {}
        lines.append("")
        if td.get("type") == "rust":
            lines.append(f"🏃 Vandaag: <b>rustdag</b>. {td.get('notes') or ''}")
        else:
            lines.append(f"🏃 Vandaag: <b>{td.get('label')} {_num(td.get('km'), 0 if float(td.get('km') or 0).is_integer() else 1)} km</b>" + (f" @ {td['pace']}" if td.get("pace") else ""))
            if td.get("notes"): lines.append(f"   {td['notes']}")
            if td.get("advice"): lines.append(f"   💡 {td['advice']}")
        for i in (m.get("insights") or [])[:3]:
            lines.append(f"• {i}")

    accs = [a for a in (data.get("accounts") or []) if a.get("status") == "ok"]
    if accs:
        lines.append("")
        for a in accs:
            tot = f" ({'+' if (a.get('total_pl') or 0) >= 0 else '−'}{_num(abs(a['total_pl'] / a['start_balance'] * 100))}% sinds start)" if a.get("total_pl") is not None and a.get("start_balance") else ""
            lines.append(f"📈 {a['name']}: {_money(a.get('equity'), a.get('currency'))}{tot}" + (f", {a['open_positions']} open" if a.get("open_positions") else ""))

    if dashboard_url:
        lines.append("")
        lines.append(f'<a href="{dashboard_url}">Open je dashboard</a>')
    return "\n".join(lines)


def maybe_send(data, state, now=None):
    """Stuurt 1x per dag, vanaf het ingestelde uur. Geeft een korte status terug."""
    cfg = load()
    if not cfg:
        return None
    now = now or datetime.now()
    hh, mm = (cfg.get("time") or "07:30").split(":")
    if (now.hour, now.minute) < (int(hh), int(mm)):
        return None
    if state.get("telegram_sent") == now.date().isoformat():
        return None
    send(cfg, compose(data, cfg.get("dashboard_url")))
    state["telegram_sent"] = now.date().isoformat()
    return "verstuurd"
