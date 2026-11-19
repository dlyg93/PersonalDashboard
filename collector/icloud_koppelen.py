"""
Eenmalig: koppel je iCloud-agenda en -contacten (voor verjaardagen) aan het dashboard.

Vooraf: maak op https://account.apple.com een app-specifiek wachtwoord
(Inloggen en beveiliging > App-specifieke wachtwoorden), bv. met de naam "Dashboard".
De gegevens komen in %USERPROFILE%\\.personal-dashboard\\icloud.json (NIET in Drive of GitHub).
Het dashboard leest enkel: het verandert niets in je agenda of contacten.
"""
import json
import os
import sys

try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agenda  # noqa: E402

old = agenda.load() or {}
user = input(f"Apple ID (e-mailadres){' [' + old['user'] + ']' if old.get('user') else ''}: ").strip() or old.get("user")
print("Plak het app-specifieke wachtwoord met een RECHTERMUISKLIK in dit venster (Ctrl+V werkt hier niet altijd).")
pw = input("App-specifiek wachtwoord (xxxx-xxxx-xxxx-xxxx): ").strip().replace(" ", "")
if not pw and old.get("password"):
    pw = old["password"]
if any(ord(ch) < 32 for ch in pw):
    raise SystemExit("Er zitten onzichtbare tekens in het wachtwoord (Ctrl+V?). Start opnieuw en plak met een rechtermuisklik.")
if "@" not in user:
    print("Let op: gebruik het e-mailadres van je Apple ID, geen telefoonnummer.")

print("\nAgenda's ophalen...")
cals = None
# Apple toont het wachtwoord met streepjes; we proberen met en zonder.
for kandidaat in dict.fromkeys([pw, pw.replace("-", ""), "-".join(pw.replace("-", "")[i:i + 4] for i in range(0, 16, 4)) if len(pw.replace("-", "")) == 16 else pw]):
    dav = agenda.Dav(user, kandidaat)
    try:
        cals = agenda.list_calendars(dav)
        pw = kandidaat
        break
    except PermissionError:
        continue
    except Exception as e:
        raise SystemExit(f"Kon iCloud-agenda niet lezen: {e}")
if cals is None:
    raise SystemExit(
        "iCloud weigert de aanmelding.\n"
        f"  - Apple ID gebruikt: {user}  (moet het e-mailadres zijn waarmee je op je iPhone inlogt)\n"
        f"  - Wachtwoord: {len(pw.replace('-', ''))} tekens (moet 16 letters zijn, niet je gewone Apple-wachtwoord)\n"
        "Maak eventueel een nieuw app-specifiek wachtwoord op account.apple.com en probeer opnieuw.")
for i, c in enumerate(cals, 1):
    print(f"  {i}. {c['name']}")
keuze = input("Welke agenda's tonen? Nummers met komma's, Enter = allemaal: ").strip()
wanted = None
if keuze:
    try:
        wanted = [cals[int(x) - 1]["name"] for x in keuze.replace(" ", "").split(",") if x]
    except (ValueError, IndexError):
        print("Niet begrepen, ik neem ze allemaal.")

print("\nContacten nakijken voor verjaardagen...")
try:
    bdays, n = agenda.fetch_birthdays(dav, 60)
    print(f"  {n} contacten gelezen, {len(bdays)} verjaardag(en) in de komende 60 dagen.")
except Exception as e:
    print(f"  Contacten lukten niet ({e}). De agenda werkt wel.")

os.makedirs(agenda.SECRETS_DIR, exist_ok=True)
with open(agenda.SECRETS, "w", encoding="utf-8") as f:
    json.dump({"user": user, "password": pw, "calendars": wanted}, f)
cache = os.path.join(agenda.SECRETS_DIR, "icloud_cache.json")
try:
    os.remove(cache)
except OSError:
    pass
print(f"\nGelukt! iCloud is gekoppeld. Gegevens bewaard in {agenda.SECRETS}")
print("Binnen 5 minuten verschijnt je agenda op het dashboard.")
