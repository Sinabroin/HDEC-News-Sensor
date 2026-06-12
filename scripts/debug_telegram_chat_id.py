import json
import os
import urllib.request

token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()

if not token:
    raise SystemExit("TELEGRAM_BOT_TOKEN is missing")

def call(method):
    url = f"https://api.telegram.org/bot{token}/{method}"
    with urllib.request.urlopen(url, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))

me = call("getMe")
print("BOT CHECK")
print("ok:", me.get("ok"))
if me.get("ok"):
    result = me.get("result", {})
    print("bot_id:", result.get("id"))
    print("bot_username:", result.get("username"))
    print("bot_first_name:", result.get("first_name"))

print("---")

updates = call("getUpdates")
print("UPDATES")
print("ok:", updates.get("ok"))
print("result_count:", len(updates.get("result", [])))

for item in updates.get("result", []):
    print("update_id:", item.get("update_id"))
    msg = item.get("message") or item.get("channel_post") or {}
    chat = msg.get("chat", {})
    text = msg.get("text")
    if chat:
        print("chat_id:", chat.get("id"))
        print("type:", chat.get("type"))
        print("title/name:", chat.get("title") or chat.get("username") or chat.get("first_name"))
        print("text:", text)
        print("---")
