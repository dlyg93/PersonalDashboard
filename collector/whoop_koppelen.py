"""
Eenmalig: koppel je Whoop aan het dashboard.

Vooraf op https://developer.whoop.com een app aanmaken (Dashboard > Create App):
  - Redirect URL:  http://localhost:8766/callback
  - Scopes: read:recovery, read:cycles, read:sleep, read:workout, read:profile, read:body_measurement
Daarna dit script starten en Client ID + Client Secret plakken.
De sleutels komen in %USERPROFILE%\\.personal-dashboard\\whoop.json (NIET in Drive of GitHub).
"""
import http.server
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

PORT = 8766
REDIRECT = f"http://localhost:{PORT}/callback"
SCOPES = "offline read:recovery read:cycles read:sleep read:workout read:profile read:body_measurement"
SECRETS_DIR = os.path.join(os.path.expanduser("~"), ".personal-dashboard")
SECRETS = os.path.join(SECRETS_DIR, "whoop.json")

client_id = input("Whoop Client ID: ").strip()
client_secret = input("Whoop Client Secret: ").strip().strip("'\"")
state = secrets.token_urlsafe(16)
auth_url = "https://api.prod.whoop.com/oauth/oauth2/auth?" + urllib.parse.urlencode({
    "client_id": client_id, "redirect_uri": REDIRECT, "response_type": "code", "scope": SCOPES, "state": state})

result = {}


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if q.get("state", [None])[0] != state:
            result["error"] = "state klopt niet"
        result["code"] = (q.get("code") or [None])[0]
        result.setdefault("error", (q.get("error") or [None])[0])
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        ok = result["code"] and not result.get("error")
        self.wfile.write(f"<h2 style='font-family:sans-serif'>{'Gekoppeld! Je mag dit venster sluiten.' if ok else 'Koppelen mislukt: ' + str(result.get('error'))}</h2>".encode())

    def log_message(self, *a):
        pass


server = http.server.HTTPServer(("localhost", PORT), Handler)
threading.Thread(target=server.handle_request, daemon=True).start()
print("\nJe browser opent nu Whoop. Log in en klik op 'Allow'...")
webbrowser.open(auth_url)
for _ in range(300):
    if "code" in result:
        break
    time.sleep(1)
server.server_close()
if not result.get("code") or result.get("error"):
    raise SystemExit("Geen toestemming ontvangen van Whoop: " + str(result.get("error")))

data = urllib.parse.urlencode({"grant_type": "authorization_code", "code": result["code"], "redirect_uri": REDIRECT,
                               "client_id": client_id, "client_secret": client_secret}).encode()
try:
    req = urllib.request.Request("https://api.prod.whoop.com/oauth/oauth2/token", data=data,
                                 headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "PersonalDashboard/1.0 (+https://dlyg93.github.io/PersonalDashboard/)", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        tok = json.loads(r.read().decode("utf-8"))
except urllib.error.HTTPError as e:
    raise SystemExit(f"Whoop weigerde de koppeling (HTTP {e.code}): {e.read().decode('utf-8', 'replace')}")

if not tok.get("refresh_token"):
    print("Let op: geen refresh-token ontvangen. Staat de scope 'offline' aan in je Whoop-app?")
os.makedirs(SECRETS_DIR, exist_ok=True)
with open(SECRETS, "w", encoding="utf-8") as f:
    json.dump({"client_id": client_id, "client_secret": client_secret, "access_token": tok["access_token"],
               "refresh_token": tok.get("refresh_token"), "expires_at": time.time() + int(tok.get("expires_in", 3600))}, f)
print(f"\nGelukt! Whoop is gekoppeld. Sleutels bewaard in {SECRETS}")
