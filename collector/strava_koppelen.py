"""
Eenmalig: koppel je eigen Strava API-app aan het dashboard.

Vooraf op https://www.strava.com/settings/api een app aanmaken:
  - Website: je GitHub-adres (mag ook http://localhost)
  - Authorization Callback Domain: localhost
Daarna dit script starten en Client ID + Client Secret plakken.
De sleutels worden bewaard in %USERPROFILE%\\.personal-dashboard\\strava.json
(dus NIET in je Google Drive-map).
"""

import http.server
import json
import os
import threading
import urllib.parse
import urllib.request
import webbrowser

PORT = 8765
SECRETS_DIR = os.path.join(os.path.expanduser("~"), ".personal-dashboard")
SECRETS = os.path.join(SECRETS_DIR, "strava.json")

client_id = input("Strava Client ID: ").strip()
client_secret = input("Strava Client Secret: ").strip()

redirect = f"http://localhost:{PORT}/"
auth_url = "https://www.strava.com/oauth/authorize?" + urllib.parse.urlencode({
    "client_id": client_id, "redirect_uri": redirect, "response_type": "code",
    "approval_prompt": "auto", "scope": "read,activity:read_all",
})

result = {}


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        result["code"] = (q.get("code") or [None])[0]
        result["error"] = (q.get("error") or [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        msg = "Gekoppeld! Je mag dit venster sluiten." if result["code"] else "Koppelen mislukt: " + str(result["error"])
        self.wfile.write(f"<h2 style='font-family:sans-serif'>{msg}</h2>".encode("utf-8"))

    def log_message(self, *a):
        pass


server = http.server.HTTPServer(("localhost", PORT), Handler)
threading.Thread(target=server.handle_request, daemon=True).start()
print("\nJe browser opent nu Strava. Klik op 'Toestaan'...")
webbrowser.open(auth_url)

import time
for _ in range(300):
    if "code" in result:
        break
    time.sleep(1)
server.server_close()

if not result.get("code"):
    raise SystemExit("Geen toestemming ontvangen van Strava: " + str(result.get("error")))

data = urllib.parse.urlencode({
    "client_id": client_id, "client_secret": client_secret,
    "code": result["code"], "grant_type": "authorization_code",
}).encode()
with urllib.request.urlopen(urllib.request.Request("https://www.strava.com/oauth/token", data=data), timeout=30) as r:
    tok = json.loads(r.read().decode("utf-8"))

os.makedirs(SECRETS_DIR, exist_ok=True)
with open(SECRETS, "w", encoding="utf-8") as f:
    json.dump({
        "client_id": client_id, "client_secret": client_secret,
        "access_token": tok["access_token"], "refresh_token": tok["refresh_token"],
        "expires_at": tok["expires_at"],
    }, f)
name = (tok.get("athlete") or {}).get("firstname", "")
print(f"\nGelukt{', ' + name if name else ''}! Strava is gekoppeld. Sleutels bewaard in {SECRETS}")
