"""
Maakt een EERSTE VERSIE (draft) van marathon_plan.json voor de Skylux Marathon Kortrijk (25 april 2027).
Het bestand is bedoeld om verder bij te werken: zelf, of in een gesprek met Claude.
Het dashboard en het ochtendbericht lezen het plan en vergelijken het met je Strava-loopjes.

Opbouw (zoals besproken in oktober), herstart vanaf je huidige niveau eind november 2026:
  1. Basis herstarten        23 nov - 3 jan   (6 weken)
  2. Sneller worden          4 jan - 14 feb   (6 weken)  - lactaattest half januari, 10 km-test
  3. Marathonspecifiek       15 feb - 4 apr   (7 weken)  - halve marathon-test half maart
  4. Afbouwen + race         5 apr - 25 apr   (3 weken)
"""
import json
import os
import sys
from datetime import date, timedelta

START = date(2026, 11, 23)          # maandag
RACE = date(2027, 4, 25)            # zondag

PACES = {
    "rustig":   {"label": "Rustig (zone 2)",          "min_s": 410, "max_s": 440, "txt": "6:50-7:20 /km"},
    "lang":     {"label": "Lange duurloop",           "min_s": 415, "max_s": 445, "txt": "6:55-7:25 /km"},
    "tempo":    {"label": "Tempo",                    "min_s": 325, "max_s": 340, "txt": "5:25-5:40 /km"},
    "mt":       {"label": "Marathontempo",            "min_s": 335, "max_s": 345, "txt": "5:35-5:45 /km"},
    "interval": {"label": "Interval",                 "min_s": 300, "max_s": 310, "txt": "5:00-5:10 /km (800 m)"},
    "race":     {"label": "Wedstrijd",                "min_s": 335, "max_s": 341, "txt": "5:35-5:41 /km"},
}

# per week: (fase, focus, [(dag, type, km, uitleg), ...])
W = []
def wk(phase, focus, *sessions):
    W.append((phase, focus, sessions))

# ---- Fase 1: basis herstarten ----
wk("Basis", "Rustig herstarten na de pauze: 3 korte loopjes, alles rustig.",
   ("di", "rustig", 4, "Rustig, mag met wandelpauzes"), ("do", "rustig", 5, "Rustig"), ("zo", "lang", 6, "Rustig en ontspannen"))
wk("Basis", "Iets langer, voeg 4 versnellingen van 100 m toe na de dinsdagloop.",
   ("di", "rustig", 5, "Rustig + 4x100 m versnelling"), ("do", "rustig", 5, "Rustig"), ("za", "rustig", 4, "Heel rustig"), ("zo", "lang", 7, "Rustig"))
wk("Basis", "Vier loopjes. Eerste korte tempoblokjes.",
   ("di", "rustig", 5, "Rustig + 6x100 m versnelling"), ("do", "tempo", 6, "2 km inlopen, 2x1 km tempo (2' wandelen), uitlopen"), ("za", "rustig", 4, "Rustig"), ("zo", "lang", 8, "Rustig"))
wk("Basis", "Rustweek: minder volume zodat je lichaam kan bijkomen.",
   ("di", "rustig", 5, "Rustig"), ("do", "rustig", 5, "Rustig + 4x100 m"), ("zo", "lang", 8, "Rustig"))
wk("Basis", "Opbouw: langere duurloop.",
   ("di", "rustig", 6, "Rustig + 6x100 m"), ("do", "tempo", 7, "2 km in, 3x1 km tempo (2' rust), uitlopen"), ("za", "rustig", 5, "Rustig"), ("zo", "lang", 10, "Rustig"))
wk("Basis", "Feestweek: flexibel, maar hou de lange duurloop.",
   ("di", "rustig", 5, "Rustig"), ("do", "rustig", 6, "Rustig + 6x100 m"), ("zo", "lang", 11, "Rustig"))

# ---- Fase 2: sneller worden ----
wk("Sneller", "Eerste echte intervallen.",
   ("di", "interval", 7, "2 km in, 6x400 m vlot (200 m wandel/drafrust), uitlopen"), ("do", "rustig", 6, "Rustig"), ("za", "rustig", 5, "Rustig"), ("zo", "lang", 12, "Rustig"))
wk("Sneller", "Lactaattest deze week (uitgerust). Daarna tempo's bijstellen in dit bestand.",
   ("di", "rustig", 6, "Rustig - dag voor de test rustig houden"), ("wo", "test", 0, "Lactaattest in het labo"), ("za", "tempo", 7, "2 km in, 2x2 km tempo, uitlopen"), ("zo", "lang", 13, "Rustig"))
wk("Sneller", "Rustweek.",
   ("di", "interval", 7, "2 km in, 5x800 m (400 m draf), uitlopen"), ("do", "rustig", 6, "Rustig"), ("zo", "lang", 12, "Rustig"))
wk("Sneller", "Opbouw snelheid en duur.",
   ("di", "interval", 8, "2 km in, 6x800 m (400 m draf), uitlopen"), ("do", "rustig", 7, "Rustig"), ("za", "tempo", 8, "2 km in, 4 km tempo, uitlopen"), ("zo", "lang", 15, "Rustig"))
wk("Sneller", "Zwaarste week van fase 2.",
   ("di", "interval", 9, "2 km in, 5x1 km (2' rust), uitlopen"), ("do", "rustig", 7, "Rustig"), ("za", "rustig", 5, "Rustig + 6x100 m"), ("zo", "lang", 17, "Rustig"))
wk("Sneller", "Rustweek met 10 km-test op zaterdag. Doel: onder 52-53 minuten.",
   ("di", "rustig", 6, "Rustig + 4x100 m"), ("do", "rustig", 5, "Heel rustig"), ("za", "test", 10, "10 km-test (of wedstrijd) volle bak"), ("zo", "rustig", 6, "Uitlopen, heel rustig"))

# ---- Fase 3: marathonspecifiek ----
wk("Marathon", "Marathontempo komt erbij in de lange duurloop.",
   ("di", "tempo", 9, "2 km in, 5 km tempo, uitlopen"), ("do", "rustig", 8, "Rustig"), ("za", "rustig", 6, "Rustig"), ("zo", "lang", 18, "Rustig, laatste 4 km op marathontempo"))
wk("Marathon", "Opbouw.",
   ("di", "interval", 10, "2 km in, 6x1 km (90\" rust), uitlopen"), ("do", "rustig", 8, "Rustig"), ("za", "rustig", 6, "Rustig"), ("zo", "lang", 21, "Rustig, 2x4 km marathontempo"))
wk("Marathon", "Opbouw.",
   ("di", "tempo", 10, "2 km in, 6 km tempo, uitlopen"), ("do", "rustig", 9, "Rustig"), ("za", "rustig", 6, "Rustig"), ("zo", "lang", 24, "Rustig, laatste 6 km marathontempo"))
wk("Marathon", "Rustweek met halve marathon-test op zondag. Onder 1:55 = sub 4 realistisch.",
   ("di", "rustig", 8, "Rustig + 6x100 m"), ("do", "mt", 8, "2 km in, 5 km marathontempo, uitlopen"), ("za", "rustig", 4, "Heel rustig"), ("zo", "test", 21.1, "Halve marathon-test of wedstrijd"))
wk("Marathon", "Herstel van de test, daarna verder bouwen.",
   ("di", "rustig", 8, "Rustig"), ("do", "interval", 10, "2 km in, 5x1,5 km (2' rust), uitlopen"), ("za", "rustig", 7, "Rustig"), ("zo", "lang", 26, "Rustig, 3x5 km marathontempo"))
wk("Marathon", "Piekweek 1.",
   ("di", "tempo", 11, "2 km in, 7 km tempo, uitlopen"), ("do", "rustig", 9, "Rustig"), ("za", "rustig", 7, "Rustig"), ("zo", "lang", 29, "Rustig, laatste 10 km marathontempo"))
wk("Marathon", "Piekweek 2: langste duurloop. Oefen je racevoeding en -drank.",
   ("di", "mt", 11, "2 km in, 8 km marathontempo, uitlopen"), ("do", "rustig", 8, "Rustig"), ("za", "rustig", 6, "Rustig"), ("zo", "lang", 32, "Rustig, racevoeding testen"))

# ---- Fase 4: afbouwen ----
wk("Afbouw", "Minder kilometers, wel nog wat tempo.",
   ("di", "interval", 9, "2 km in, 5x1 km (2' rust), uitlopen"), ("do", "rustig", 8, "Rustig"), ("za", "rustig", 6, "Rustig"), ("zo", "lang", 22, "Rustig, 8 km marathontempo"))
wk("Afbouw", "Verder afbouwen.",
   ("di", "mt", 9, "2 km in, 5 km marathontempo, uitlopen"), ("do", "rustig", 7, "Rustig"), ("za", "rustig", 5, "Rustig + 4x100 m"), ("zo", "lang", 15, "Rustig"))
wk("Race", "Raceweek. Veel slapen, goed eten, niets nieuws proberen.",
   ("di", "mt", 6, "2 km in, 3 km marathontempo, uitlopen"), ("do", "rustig", 4, "Heel rustig + 3x100 m"), ("za", "rustig", 3, "Kort losmaken"), ("zo", "race", 42.2, "Skylux Marathon Kortrijk - start rustig, 5:38-5:41 /km"))

DAYS = {"ma": 0, "di": 1, "wo": 2, "do": 3, "vr": 4, "za": 5, "zo": 6}


def build():
    assert START + timedelta(weeks=len(W) - 1, days=6) == RACE, "plan sluit niet aan op de racedag"
    weeks = []
    for i, (phase, focus, sessions) in enumerate(W):
        ws = START + timedelta(weeks=i)
        out = []
        for day, typ, km, notes in sessions:
            out.append({
                "date": (ws + timedelta(days=DAYS[day])).isoformat(),
                "day": day, "type": typ, "km": km,
                "pace": PACES.get(typ, {}).get("txt") if typ != "test" else None,
                "notes": notes,
            })
        weeks.append({"week": i + 1, "start": ws.isoformat(), "phase": phase, "focus": focus,
                      "planned_km": round(sum(s[2] for s in sessions), 1), "sessions": out})
    return {
        "version": 1,
        "status": "draft",
        "note": "Eerste versie. Dagen zijn een voorstel: het dashboard telt een loop mee voor de sessie die er het best bij past binnen dezelfde week. Tempo's bijstellen na de lactaattest.",
        "race": {"name": "Skylux Marathon Kortrijk", "date": RACE.isoformat(), "goal": "onder 4:00:00", "goal_pace": "5:41 /km"},
        "paces": PACES,
        "weeks": weeks,
    }


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "marathon_plan.json")
    if os.path.exists(out) and "--overschrijf" not in sys.argv:
        raise SystemExit(f"{out} bestaat al. Gebruik --overschrijf om het te vervangen.")
    plan = build()
    with open(out, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=1)
    print(f"Geschreven: {out} ({len(plan['weeks'])} weken, piek {max(w['planned_km'] for w in plan['weeks'])} km/week)")
