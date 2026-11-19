"""
Eenmalig: stel het dagelijkse ochtendbericht via Telegram in.
Je kan je bestaande bot (@DollyG_bot) gebruiken: het bericht wordt enkel VERSTUURD,
het script leest geen berichten, dus je MT5-bot wordt niet gestoord.

Nodig:
  - Bot token (van @BotFather, of uit de instellingen van je MT5-bot)
  - Chat ID (jouw chat met de bot; staat ook in de instellingen van je MT5-bot)
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
token = input(f"Bot token{' (Enter = behouden)' if cfg.get('bot_token') else ''}: ").strip() or cfg.get("bot_token")
chat = input(f"Chat ID{' (Enter = ' + str(cfg.get('chat_id')) + ')' if cfg.get('chat_id') else ''}: ").strip() or cfg.get("chat_id")
uur = input(f"Uur van het ochtendbericht [{cfg.get('time', '07:30')}]: ").strip() or cfg.get("time", "07:30")
cfg.update(bot_token=token, chat_id=chat, time=uur, dashboard_url=cfg.get("dashboard_url", "https://dlyg93.github.io/PersonalDashboard/"))

tb.send(cfg, "✅ Je dashboard is gekoppeld. Vanaf morgen krijg je hier om " + uur + " je ochtendoverzicht.")
os.makedirs(tb.SECRETS_DIR, exist_ok=True)
with open(tb.SECRETS, "w", encoding="utf-8") as f:
    json.dump(cfg, f)
print("Gelukt! Je kreeg net een testbericht in Telegram.")

here = os.path.dirname(os.path.abspath(__file__))
try:
    with open(os.path.join(here, "dashboard.json"), "r", encoding="utf-8") as f:
        data = json.load(f)
    if input("Wil je nu al een voorbeeld van het ochtendbericht? (j/n) ").strip().lower().startswith("j"):
        tb.send(cfg, tb.compose(data, cfg.get("dashboard_url")))
        print("Voorbeeld verstuurd.")
except (OSError, ValueError):
    pass
