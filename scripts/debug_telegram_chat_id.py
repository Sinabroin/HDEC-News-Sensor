import json
import os
import urllib.request

token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()

if not token:
    raise SystemExit("TELEGRAM_BOT_TOKEN is missing")

url = f"https://api.telegram.org/bot{token}/getUpdates"

with urllib.request.urlopen(url, timeout=30) as response:
    data = json.loads(response.read().decode("utf-8"))

print("ok:", data.get("ok"))
print("result_count:", len(data.get("result", [])))

for item in data.get("result", []):
    msg = item.get("message") or item.get("channel_post") or {}
    chat = msg.get("chat", {})
    if chat:
        print("chat_id:", chat.get("id"))
        print("type:", chat.get("type"))
        print("title/name:", chat.get("title") or chat.get("username") or chat.get("first_name"))
        print("---")
