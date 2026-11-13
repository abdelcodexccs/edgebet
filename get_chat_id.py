"""Finds your private Telegram chat id from the messages you sent your bot,
and writes it into .env as TELEGRAM_CHAT_ID. Send your bot "hi" first."""
import json
import re
import urllib.request

from daily import load_env
import os

load_env()
token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
if not token:
    print("TELEGRAM_BOT_TOKEN is empty in .env. Paste your bot token there first.")
    raise SystemExit(1)
try:
    with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/getUpdates", timeout=20) as r:
        data = json.loads(r.read())
except Exception as e:
    print("Telegram refused the token:", e, "\nCheck you pasted the NEWEST token from BotFather.")
    raise SystemExit(1)
ids = {}
for u in data.get("result", []):
    msg = u.get("message") or u.get("edited_message") or {}
    chat = msg.get("chat") or {}
    if chat.get("type") == "private":
        ids[chat["id"]] = chat.get("first_name", "")
    # when you add the bot to your channel, Telegram tells it who did it
    who = (u.get("my_chat_member") or u.get("channel_post") or {}).get("from") or {}
    if who.get("id") and not who.get("is_bot"):
        ids[who["id"]] = who.get("first_name", "")
if not ids:
    print("No messages found. Open your bot in Telegram, send it 'hi', then run this again.")
    raise SystemExit(1)
chat_id, name = list(ids.items())[-1]
text = open(".env", encoding="utf-8").read()
text = re.sub(r"(?m)^TELEGRAM_CHAT_ID=.*$", f"TELEGRAM_CHAT_ID={chat_id}", text)
open(".env", "w", encoding="utf-8").write(text)
print(f"Found your chat id ({name}) and saved it to .env")
