"""iCloud-agenda en verjaardagen uit je iCloud-contacten (CalDAV/CardDAV, enkel lezen).

Aanmelden gebeurt met je Apple ID en een app-specifiek wachtwoord (account.apple.com).
Die staan in %USERPROFILE%\\.personal-dashboard\\icloud.json, nooit in Drive of GitHub.
In dashboard.json komen enkel titel, tijd, plaats en agendanaam van afspraken,
en voor verjaardagen enkel naam, datum en leeftijd.
"""
import base64
import json
import os
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, time as dtime, timedelta
from urllib.parse import urljoin

SECRETS_DIR = os.path.join(os.path.expanduser("~"), ".personal-dashboard")
SECRETS = os.path.join(SECRETS_DIR, "icloud.json")
UA = "PersonalDashboard/1.0 (+https://dlyg93.github.io/PersonalDashboard/)"
CAL_ROOT = "https://caldav.icloud.com/"
CARD_ROOT = "https://contacts.icloud.com/"
NS = {"d": "DAV:", "c": "urn:ietf:params:xml:ns:caldav", "card": "urn:ietf:params:xml:ns:carddav",
      "ic": "http://apple.com/ns/ical/"}
XMLNS = 'xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav" xmlns:card="urn:ietf:params:xml:ns:carddav" xmlns:ic="http://apple.com/ns/ical/"'


def load():
    try:
        with open(SECRETS, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


class Dav:
    def __init__(self, user, password):
        self.auth = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()

    def request(self, method, url, body, depth="0"):
        for _ in range(4):
            req = urllib.request.Request(url, data=body.encode("utf-8"), method=method, headers={
                "Authorization": self.auth, "Depth": depth, "User-Agent": UA,
                "Content-Type": "application/xml; charset=utf-8"})
            try:
                with urllib.request.urlopen(req, timeout=45) as r:
                    return r.geturl(), ET.fromstring(r.read())
            except urllib.error.HTTPError as e:
                if e.code in (301, 302, 307, 308) and e.headers.get("Location"):
                    url = urljoin(url, e.headers["Location"])
                    continue
                if e.code == 401:
                    raise PermissionError("iCloud weigert de aanmelding. Klopt je Apple ID en het app-specifieke wachtwoord?")
                raise
        raise RuntimeError("te veel doorverwijzingen")

    def propfind(self, url, props, depth="0"):
        body = f'<?xml version="1.0" encoding="utf-8"?><d:propfind {XMLNS}><d:prop>{props}</d:prop></d:propfind>'
        return self.request("PROPFIND", url, body, depth)

    def href(self, url, props, path):
        base, tree = self.propfind(url, props)
        el = tree.find(path, NS)
        if el is None or not (el.text or "").strip():
            raise RuntimeError(f"iCloud gaf geen {props} terug")
        return urljoin(base, el.text.strip())


def _home(dav, root, kind):
    principal = dav.href(root, "<d:current-user-principal/>", ".//d:current-user-principal/d:href")
    if kind == "cal":
        return dav.href(principal, "<c:calendar-home-set/>", ".//c:calendar-home-set/d:href")
    return dav.href(principal, "<card:addressbook-home-set/>", ".//card:addressbook-home-set/d:href")


def list_calendars(dav):
    home = _home(dav, CAL_ROOT, "cal")
    base, tree = dav.propfind(home, "<d:resourcetype/><d:displayname/><c:supported-calendar-component-set/><ic:calendar-color/>", "1")
    out = []
    for resp in tree.findall("d:response", NS):
        rt = resp.find(".//d:resourcetype", NS)
        if rt is None or rt.find("c:calendar", NS) is None:
            continue
        comps = [c.get("name") for c in resp.findall(".//c:supported-calendar-component-set/c:comp", NS)]
        if comps and "VEVENT" not in comps:
            continue
        name = (resp.findtext(".//d:displayname", default="", namespaces=NS) or "").strip() or "Agenda"
        color = (resp.findtext(".//ic:calendar-color", default="", namespaces=NS) or "").strip()[:7] or None
        out.append({"name": name, "color": color, "url": urljoin(base, resp.findtext("d:href", namespaces=NS))})
    return out


def _fmt_utc(dt):
    return dt.strftime("%Y%m%dT%H%M%SZ")


def _local(v):
    """icalendar-waarde -> (iso-tekst, hele dag?)"""
    if isinstance(v, datetime):
        if v.tzinfo is None:
            v = v.astimezone()
        return v.astimezone().isoformat(timespec="minutes"), False
    return v.isoformat(), True


def fetch_events(dav, cal, start, end):
    import icalendar
    import recurring_ical_events
    body = (f'<?xml version="1.0" encoding="utf-8"?><c:calendar-query {XMLNS}><d:prop><c:calendar-data/></d:prop>'
            f'<c:filter><c:comp-filter name="VCALENDAR"><c:comp-filter name="VEVENT">'
            f'<c:time-range start="{_fmt_utc(start)}" end="{_fmt_utc(end)}"/></c:comp-filter></c:comp-filter></c:filter></c:calendar-query>')
    _, tree = dav.request("REPORT", cal["url"], body, "1")
    out = []
    for el in tree.iter("{urn:ietf:params:xml:ns:caldav}calendar-data"):
        if not (el.text or "").strip():
            continue
        try:
            ical = icalendar.Calendar.from_ical(el.text)
            for ev in recurring_ical_events.of(ical).between(start, end):
                if str(ev.get("STATUS", "")).upper() == "CANCELLED":
                    continue
                s, all_day = _local(ev.decoded("DTSTART"))
                e = None
                if ev.get("DTEND") is not None:
                    e, _ = _local(ev.decoded("DTEND"))
                out.append({"title": str(ev.get("SUMMARY", "(zonder titel)"))[:120], "start": s, "end": e,
                            "all_day": all_day, "location": str(ev.get("LOCATION", ""))[:80] or None,
                            "calendar": cal["name"], "color": cal["color"]})
        except Exception:
            continue
    return out


def _unfold(text):
    return re.sub(r"\r?\n[ \t]", "", text)


def parse_birthdays(vcards, today=None, days=60):
    today = today or date.today()
    out = []
    for card in vcards:
        t = _unfold(card)
        m = re.search(r"^BDAY([^:\r\n]*):([^\r\n]+)", t, re.M)
        if not m:
            continue
        params, val = m.group(1).upper(), m.group(2).strip()
        name = (re.search(r"^FN[^:\r\n]*:([^\r\n]+)", t, re.M) or [None, ""])[1].strip().replace("\\,", ",")
        if not name:
            continue
        digits = re.sub(r"[^0-9]", "", val.split("T")[0])
        if val.startswith("--"):
            y, mo, d = None, int(digits[0:2]), int(digits[2:4])
        elif len(digits) >= 8:
            y, mo, d = int(digits[0:4]), int(digits[4:6]), int(digits[6:8])
        else:
            continue
        if y == 1604 or "OMIT-YEAR" in params:
            y = None
        nxt = None
        for yr in (today.year, today.year + 1):
            try:
                cand = date(yr, mo, d)
            except ValueError:
                cand = date(yr, 2, 28)  # 29 februari
            if cand >= today:
                nxt = cand
                break
        if not nxt or (nxt - today).days > days:
            continue
        out.append({"name": name[:60], "date": nxt.isoformat(), "days": (nxt - today).days,
                    "age": (nxt.year - y) if y else None})
    out.sort(key=lambda b: (b["days"], b["name"]))
    return out[:40]


def fetch_birthdays(dav, days=60):
    home = _home(dav, CARD_ROOT, "card")
    base, tree = dav.propfind(home, "<d:resourcetype/>", "1")
    books = [urljoin(base, r.findtext("d:href", namespaces=NS)) for r in tree.findall("d:response", NS)
             if r.find(".//d:resourcetype/card:addressbook", NS) is not None]
    cards = []
    body = f'<?xml version="1.0" encoding="utf-8"?><card:addressbook-query {XMLNS}><d:prop><card:address-data/></d:prop></card:addressbook-query>'
    for b in books:
        _, t = dav.request("REPORT", b, body, "1")
        cards += [el.text for el in t.iter("{urn:ietf:params:xml:ns:carddav}address-data") if el.text]
    return parse_birthdays(cards, days=days), len(cards)


def _still_relevant(e):
    """Laat afgelopen afspraken weg (hele-dag-afspraken: de einddatum telt niet mee)."""
    try:
        if e["all_day"]:
            end = date.fromisoformat((e.get("end") or e["start"])[:10])
            return end > date.today() or (not e.get("end") and end == date.today())
        end = datetime.fromisoformat(e.get("end") or e["start"])
        return end >= datetime.now().astimezone()
    except ValueError:
        return True


def collect(cfg, cache_path):
    """Afspraken (om de 15 min) en verjaardagen (om de 12 uur), met een cache buiten Drive."""
    import time
    out = {"status": "error", "message": None}
    s = load()
    if not s:
        out["message"] = "iCloud nog niet gekoppeld: start icloud_koppelen.bat"
        return out
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            cache = json.load(f)
    except (OSError, ValueError):
        cache = {}
    now = time.time()
    dav = Dav(s["user"], s["password"])
    try:
        if now - cache.get("events_at", 0) > cfg.get("every_minutes", 15) * 60:
            days = cfg.get("days", 14)
            start = datetime.combine(date.today(), dtime.min).astimezone()
            end = start + timedelta(days=days + 1)
            wanted = s.get("calendars")
            events = []
            for cal in list_calendars(dav):
                if wanted and cal["name"] not in wanted:
                    continue
                events += fetch_events(dav, cal, start, end)
            events.sort(key=lambda e: (e["start"][:10], not e["all_day"], e["start"]))
            cache.update(events=events[:150], events_at=now)
        if now - cache.get("bdays_at", 0) > cfg.get("birthday_hours", 12) * 3600:
            try:
                bdays, n = fetch_birthdays(dav, cfg.get("birthday_days", 60))
                cache.update(birthdays=bdays, contacts=n, bdays_at=now)
            except Exception as e:
                cache["bday_error"] = str(e)[:200]
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(cache, f)
    except PermissionError as e:
        out["message"] = str(e)
        return out
    except Exception as e:
        if "events" not in cache:
            out["message"] = f"iCloud: {e}"
            return out
        out["message"] = f"iCloud tijdelijk niet bereikbaar, vorige stand: {e}"[:200]

    # verjaardagen: dagen opnieuw tellen (cache kan van gisteren zijn)
    today = date.today()
    bd = []
    for b in cache.get("birthdays", []):
        d = (date.fromisoformat(b["date"]) - today).days
        if d >= 0:
            bd.append(dict(b, days=d))
    out.update(status="ok", events=[e for e in cache.get("events", []) if _still_relevant(e)], birthdays=bd)
    return out
