"""Whoop: herstel, HRV, rustpols, slaap, strain en gewicht (officiële WHOOP API v2)."""
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import date, datetime

API = "https://api.prod.whoop.com/developer"
TOKEN_URL = "https://api.prod.whoop.com/oauth/oauth2/token"
SECRETS_DIR = os.path.join(os.path.expanduser("~"), ".personal-dashboard")
SECRETS = os.path.join(SECRETS_DIR, "whoop.json")


def _post(url, data):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(),
                                 headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "PersonalDashboard/1.0 (+https://dlyg93.github.io/PersonalDashboard/)", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def _get(path, token):
    req = urllib.request.Request(API + path, headers={"Authorization": f"Bearer {token}", "User-Agent": "PersonalDashboard/1.0 (+https://dlyg93.github.io/PersonalDashboard/)", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def _token():
    with open(SECRETS, "r", encoding="utf-8") as f:
        s = json.load(f)
    if s.get("expires_at", 0) < time.time() + 120:
        t = _post(TOKEN_URL, {"grant_type": "refresh_token", "refresh_token": s["refresh_token"],
                              "client_id": s["client_id"], "client_secret": s["client_secret"], "scope": "offline"})
        s.update(access_token=t["access_token"], refresh_token=t.get("refresh_token", s["refresh_token"]),
                 expires_at=time.time() + int(t.get("expires_in", 3600)))
        with open(SECRETS, "w", encoding="utf-8") as f:
            json.dump(s, f)
    return s["access_token"]


def _h(ms):
    return round((ms or 0) / 3600000, 2)


def collect(weight_log_path):
    out = {"status": "error", "message": None}
    if not os.path.exists(SECRETS):
        out["message"] = "Whoop nog niet gekoppeld: start whoop_koppelen.bat"
        return out
    try:
        tok = _token()
        rec = _get("/v2/recovery?limit=25", tok).get("records", [])
        slp = _get("/v2/activity/sleep?limit=25", tok).get("records", [])
        cyc = _get("/v2/cycle?limit=25", tok).get("records", [])
        try:
            body = _get("/v2/user/measurement/body", tok)
        except Exception:
            body = {}

        days = {}
        def slot(ts):
            return days.setdefault(str(ts)[:10], {"date": str(ts)[:10]})
        for r in rec:
            sc = r.get("score") or {}
            if r.get("score_state") != "SCORED":
                continue
            d = slot(r.get("created_at"))
            d.update(recovery=sc.get("recovery_score"), hrv=round(sc.get("hrv_rmssd_milli") or 0, 1),
                     rhr=sc.get("resting_heart_rate"), spo2=sc.get("spo2_percentage"), skin_temp=sc.get("skin_temp_celsius"))
        for s in slp:
            if s.get("nap") or s.get("score_state") != "SCORED":
                continue
            sc = s.get("score") or {}
            st = sc.get("stage_summary") or {}
            asleep = (st.get("total_in_bed_time_milli") or 0) - (st.get("total_awake_time_milli") or 0)
            d = slot(s.get("end"))
            d.update(sleep_h=_h(asleep), sleep_perf=sc.get("sleep_performance_percentage"),
                     sleep_eff=round(sc.get("sleep_efficiency_percentage") or 0), deep_h=_h(st.get("total_slow_wave_sleep_time_milli")),
                     rem_h=_h(st.get("total_rem_sleep_time_milli")), resp=round(sc.get("respiratory_rate") or 0, 1))
        for c in cyc:
            sc = c.get("score") or {}
            if c.get("score_state") != "SCORED":
                continue
            d = slot(c.get("start"))
            d.update(strain=round(sc.get("strain") or 0, 1), kcal=round((sc.get("kilojoule") or 0) / 4.184))

        series = sorted(days.values(), key=lambda x: x["date"])[-21:]

        # gewicht: Whoop geeft enkel de huidige waarde; we houden zelf een dagelijkse lijst bij
        weight = body.get("weight_kilogram")
        hist = []
        try:
            with open(weight_log_path, "r", encoding="utf-8") as f:
                hist = json.load(f)
        except (OSError, ValueError):
            pass
        if weight:
            today = date.today().isoformat()
            hist = [h for h in hist if h["date"] != today] + [{"date": today, "kg": round(weight, 1)}]
            hist = sorted(hist, key=lambda h: h["date"])[-400:]
            try:
                with open(weight_log_path, "w", encoding="utf-8") as f:
                    json.dump(hist, f)
            except OSError:
                pass

        latest = next((d for d in reversed(series) if d.get("recovery") is not None), {})
        last_sleep = next((d for d in reversed(series) if d.get("sleep_h") is not None), {})
        out.update({
            "status": "ok",
            "today": {"recovery": latest.get("recovery"), "hrv": latest.get("hrv"), "rhr": latest.get("rhr"),
                      "spo2": latest.get("spo2"), "skin_temp": latest.get("skin_temp"), "date": latest.get("date"),
                      "sleep_h": last_sleep.get("sleep_h"), "sleep_perf": last_sleep.get("sleep_perf"),
                      "deep_h": last_sleep.get("deep_h"), "rem_h": last_sleep.get("rem_h")},
            "series": series,
            "weight": {"kg": round(weight, 1) if weight else None, "height_m": body.get("height_meter"),
                       "max_hr": body.get("max_heart_rate"), "history": hist[-120:]},
        })
        return out
    except Exception as e:
        out["message"] = f"Whoop: {e}"
        return out
