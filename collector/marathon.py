"""Vergelijkt marathon_plan.json met je Strava-loopjes en maakt inzichten."""
import json
import os
from datetime import date, datetime, timedelta

DAGEN = ["maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag", "zondag"]
TYPE_NL = {"rustig": "Rustige loop", "lang": "Lange duurloop", "tempo": "Tempoloop", "interval": "Intervaltraining",
           "mt": "Marathontempo", "test": "Test", "race": "Marathon"}


def load_plan(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _d(s):
    return date.fromisoformat(str(s)[:10])


def _n(v, d=1):
    return f"{v:.{d}f}".replace(".", ",")


def _pace(sec):
    if not sec:
        return None
    return f"{int(sec // 60)}:{int(round(sec % 60)):02d} /km"


def evaluate(plan, activities, today=None, recovery=None):
    if not plan:
        return {"status": "off", "message": "Geen marathon_plan.json gevonden"}
    today = today or date.today()
    race = _d(plan["race"]["date"])
    paces = plan.get("paces", {})
    runs = []
    for a in activities or []:
        if "Run" not in (a.get("sport") or "") or not a.get("distance_km"):
            continue
        d = _d(a["start_local"])
        pace_s = a.get("pace_s") or (a["moving_min"] * 60 / a["distance_km"] if a.get("moving_min") else None)
        runs.append({"date": d.isoformat(), "d": d, "name": a.get("name"), "km": a["distance_km"], "pace_s": pace_s})

    weeks_out, cur_week = [], None
    for w in plan["weeks"]:
        ws = _d(w["start"]); we = ws + timedelta(days=7)
        wruns = sorted([r for r in runs if ws <= r["d"] < we], key=lambda r: r["d"])
        sessions = [dict(s) for s in w["sessions"]]
        # koppel elke loop aan de dichtstbijzijnde nog open sessie (dagen zijn flexibel; bij gelijke afstand de vroegere = inhalen)
        pool = []
        for r in wruns:
            cands = [i for i, s in enumerate(sessions) if s["km"] and "run" not in s]
            if not cands:
                pool.append(r)
                continue
            i = min(cands, key=lambda i: (abs((_d(sessions[i]["date"]) - r["d"]).days), _d(sessions[i]["date"]) > r["d"]))
            s = sessions[i]
            s["run"] = {"date": r["date"], "name": r["name"], "km": r["km"], "pace": _pace(r["pace_s"])}
            ratio = r["km"] / s["km"]
            s["status"] = "done" if ratio >= 0.9 else "partial" if ratio >= 0.6 else "short"
            p = paces.get(s["type"], {})
            if s["type"] in ("rustig", "lang") and r["pace_s"] and p.get("min_s") and r["pace_s"] < p["min_s"] - 15:
                s["note_pace"] = "sneller dan gepland voor een rustige loop"
        for s in sessions:
            if not s["km"]:
                s["status"] = "info" if _d(s["date"]) > today else "done"
            elif "run" not in s:
                sd = _d(s["date"])
                s["status"] = "missed" if (we <= today or sd < today - timedelta(days=1)) else ("open" if sd <= today else "planned")
        actual = round(sum(r["km"] for r in wruns), 1)
        wo = {"week": w["week"], "start": w["start"], "phase": w["phase"], "focus": w.get("focus"),
              "planned_km": w["planned_km"], "actual_km": actual,
              "done": sum(1 for s in sessions if s.get("status") == "done"),
              "count": sum(1 for s in sessions if s.get("km")), "sessions": sessions,
              "extra_runs": [{"date": r["date"], "km": r["km"]} for r in pool]}
        weeks_out.append(wo)
        if ws <= today < we:
            cur_week = wo

    first = _d(plan["weeks"][0]["start"])
    if today < first:
        ran_today = [r for r in runs if r["d"] == today]
        today_out = {"type": "rust", "label": "Voor de start", "km": 0, "status": "done" if ran_today else "rest",
                     "notes": f"Het plan start op maandag {first.day} {['jan','feb','mrt','apr','mei','jun','jul','aug','sep','okt','nov','dec'][first.month-1]}. Tot dan: rustig lopen om de 2 dagen.",
                     "advice": None}
        recent = [r for r in runs if r["d"] >= today - timedelta(days=28)]
        insights = [f"Het plan start over {(first - today).days} dag(en). Week 1 = 3 rustige loopjes, samen 15 km.",
                    f"Laatste 4 weken: {len(recent)} loopje(s), {_n(sum(r['km'] for r in recent))} km.",
                    f"Nog {(race - today).days} dagen tot Kortrijk. Doeltempo voor sub 4: {plan['race'].get('goal_pace', '5:41 /km')}."]
        return {"status": "ok", "plan_status": plan.get("status"), "race": plan["race"], "days_left": (race - today).days,
                "phase": None, "week_no": 0, "today": today_out, "weeks": weeks_out, "insights": insights,
                "long_runs": [{"date": r["date"], "km": r["km"]} for r in runs if r["km"] >= 10 and r["d"] >= first - timedelta(days=56)],
                "paces": {k: v.get("txt") for k, v in paces.items()}}

    # vandaag
    today_s = None
    if cur_week:
        for s in cur_week["sessions"]:
            if s["date"] == today.isoformat():
                today_s = s
        ran_today = [r for r in runs if r["d"] == today]
    else:
        ran_today = []
    if today_s:
        today_out = {"type": today_s["type"], "label": TYPE_NL.get(today_s["type"], today_s["type"]), "km": today_s["km"],
                     "pace": today_s.get("pace"), "notes": today_s.get("notes"),
                     "status": today_s["status"] if today_s.get("run") else ("partial" if ran_today else "todo"),
                     "run": today_s.get("run")}
    else:
        nxt = None
        if cur_week:
            nxt = next((s for s in cur_week["sessions"] if s.get("status") in ("open", "planned") and s["km"]), None)
        today_out = {"type": "rust", "label": "Rustdag", "km": 0,
                     "notes": (f"Gemiste training inhalen? Volgende: {TYPE_NL.get(nxt['type'])} {nxt['km']} km" if nxt and nxt["status"] == "open"
                               else "Geen loop gepland. Badminton, fietsen of wandelen mag."),
                     "status": "done" if ran_today else "rest"}

    # advies op basis van herstel
    rec = (recovery or {}).get("score")
    advice = None
    if rec is not None and today_out["type"] not in ("rust",):
        if rec < 34:
            advice = f"Herstel is laag ({rec:.0f}%). Maak er een rustige korte loop van of neem rust."
        elif rec < 67 and today_out["type"] in ("tempo", "interval", "mt", "test"):
            advice = f"Herstel is matig ({rec:.0f}%). Doe de training, maar stop als het zwaar voelt."
        elif rec >= 67:
            advice = f"Herstel is goed ({rec:.0f}%). Ga ervoor!"
    today_out["advice"] = advice

    # inzichten
    insights = []
    days_left = (race - today).days
    past = [w for w in weeks_out if _d(w["start"]) + timedelta(days=7) <= today]
    if cur_week:
        insights.append(f"Week {cur_week['week']} ({cur_week['phase']}): {cur_week['done']} van {cur_week['count']} trainingen, "
                        f"{_n(cur_week['actual_km'])} van {_n(cur_week['planned_km'])} km.")
    if past:
        last = past[-1]
        if last["done"] < last["count"]:
            insights.append(f"Vorige week: {last['done']} van {last['count']} trainingen gehaald ({_n(last['actual_km'])} van {_n(last['planned_km'])} km).")
        else:
            insights.append(f"Vorige week volledig gehaald ({_n(last['actual_km'])} km). Sterk!")
        recent = past[-4:]
        tot_p = sum(w["planned_km"] for w in recent); tot_a = sum(w["actual_km"] for w in recent)
        if tot_p and len(recent) >= 2:
            insights.append(f"Laatste {len(recent)} weken: {round(tot_a / tot_p * 100)}% van de geplande kilometers.")
    fast = [s for w in weeks_out[-6:] for s in w["sessions"] if s.get("note_pace")]
    if len(fast) >= 2:
        insights.append(f"{len(fast)} rustige loopjes liep je te snel. Rustig = 6:50 of trager: zo herstel je beter en word je op termijn sneller.")
    longest = max((r["km"] for r in runs if r["d"] >= today - timedelta(days=28)), default=0)
    nxt_long = next((s["km"] for w in weeks_out for s in w["sessions"] if s["type"] == "lang" and _d(s["date"]) >= today), None)
    if nxt_long:
        insights.append(f"Langste loop laatste 4 weken: {_n(longest)} km. Volgende lange duurloop: {_n(nxt_long, 0)} km.")
    if days_left > 0:
        insights.append(f"Nog {days_left} dagen tot Kortrijk. Doeltempo voor sub 4: {plan['race'].get('goal_pace', '5:41 /km')}.")

    long_runs = [{"date": r["date"], "km": r["km"]} for r in runs if r["km"] >= 10 and r["d"] >= _d(plan["weeks"][0]["start"]) - timedelta(days=28)]
    return {
        "status": "ok", "plan_status": plan.get("status"), "race": plan["race"], "days_left": days_left,
        "phase": cur_week["phase"] if cur_week else None, "week_no": cur_week["week"] if cur_week else None,
        "today": today_out, "weeks": weeks_out, "insights": insights, "long_runs": long_runs,
        "paces": {k: v.get("txt") for k, v in paces.items()},
    }
