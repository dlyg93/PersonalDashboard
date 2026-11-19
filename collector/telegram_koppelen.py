"""
Eenmalig: stel het dagelijkse ochtendbericht via Telegram in.
Gebruik hiervoor een APARTE bot (niet @DollyG_bot): dezelfde bot beantwoordt ook je vragen
(/vandaag, /agenda, ...), en dat mag niet botsen met je MT5-bot.

Nodig:
  - Bot token van je nieuwe bot (via @BotFather: /newbot)
  - Chat ID (jouw Telegram-ID; hetzelfde nummer als bij je MT5-bot)
Stuur eerst zelf /start naar je nieuwe bot, anders mag hij je geen berichten sturen.
De gegevens komen in %USERPROFILE%\\.personal-dashboard\\telegram.json (NIET in Drive of GitHub).
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
import telegram_bericht as tb  # noqa: E402

cfg = tb.load() or {}
print("Tip: stuur eerst /start naar je nieuwe bot in Telegram.")
token = input(f"Bot token{' (Enter = behouden)' if cfg.get('bot_token') else ''}: ").strip() or cfg.get("bot_token")
chat = input(f"Chat ID{' (Enter = ' + str(cfg.get('chat_id')) + ')' if cfg.get('chat_id') else ''}: ").strip() or cfg.get("chat_id")
uur = input(f"Uur van het ochtendbericht [{cfg.get('time', '07:30')}]: ").strip() or cfg.get("time", "07:30")
cfg.update(bot_token=token, chat_id=chat, time=uur, dashboard_url=cfg.get("dashboard_url", "https://dlyg93.github.io/PersonalDashboard/"))

# Vragen beantwoorden kan enkel met een aparte bot: anders botst het met je MT5-bot.
try:
    me = tb.urllib.request.urlopen(f"https://api.telegram.org/bot{token}/getMe", timeout=20)
    naam = json.loads(me.read().decode("utf-8"))["result"].get("username", "")
except Exception as e:
    raise SystemExit(f"Dit bot-token werkt niet ({e}). Kopieer het opnieuw van @BotFather.")
print(f"Bot gevonden: @{naam}")
if naam.lower() == "dollyg_bot":
    print("Dit is je MT5-bot. Die gebruik ik enkel voor het ochtendbericht; vragen stellen kan dan NIET.")
    cfg["bot"] = False
else:
    cfg["bot"] = input(f"Mag @{naam} ook je vragen beantwoorden (/vandaag, /agenda, ...)? (j/n) [j]: ").strip().lower() in ("", "j", "ja", "y")

tb.send(cfg, "✅ Je dashboard is gekoppeld. Vanaf morgen krijg je hier om " + uur + " je ochtendoverzicht.\nStel me ook gerust een vraag: /vandaag, /agenda, /training ... (/help voor alles)")
os.makedirs(tb.SECRETS_DIR, exist_ok=True)
with open(tb.SECRETS, "w", encoding="utf-8") as f:
    json.dump(cfg, f)
print("Gelukt! Je kreeg net een testbericht in Telegram.")
if cfg.get("bot"):
    print("Binnen een minuut start de Mac mini de bot; daarna kan je hem vragen stellen, bv. /vandaag.")

here = os.path.dirname(os.path.abspath(__file__))
try:
    with open(os.path.join(here, "dashboard.json"), "r", encoding="utf-8") as f:
        data = json.load(f)
    if input("Wil je nu al een voorbeeld van het ochtendbericht? (j/n) ").strip().lower().startswith("j"):
        tb.send(cfg, tb.compose(data, cfg.get("dashboard_url")))
        print("Voorbeeld verstuurd.")
except (OSError, ValueError):
    pass
