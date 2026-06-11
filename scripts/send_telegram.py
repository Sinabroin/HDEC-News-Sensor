import json
import os
import sys
import urllib.parse
import urllib.request


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
chat_ids_raw = os.environ.get("TELEGRAM_CHAT_IDS", "").strip()
message = os.environ.get("MESSAGE", "HDEC Executive Radar test").strip()

if not token:
    fail("TELEGRAM_BOT_TOKEN is missing")

if not chat_ids_raw:
    fail("TELEGRAM_CHAT_IDS is missing")

chat_ids = [item.strip() for item in chat_ids_raw.split(",") if item.strip()]

if not chat_ids:
    fail("No valid chat ids found")

if len(message) > 800:
    message = message[:797] + "..."

url = f"https://api.telegram.org/bot{token}/sendMessage"

delivered = 0
failed = 0

for chat_id in chat_ids:
    payload = {
        "chat_id": chat_id,
        "text": message,
        "disable_web_page_preview": "true",
    }

    data = urllib.parse.urlencode(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST")

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            result = json.loads(response.read().decode("utf-8"))

        if result.get("ok"):
            delivered += 1
        else:
            failed += 1
            print("Telegram send failed for one recipient", file=sys.stderr)

    except Exception as exc:
        failed += 1
        print(f"Telegram send exception for one recipient: {type(exc).__name__}", file=sys.stderr)

print(f"Telegram delivery summary: delivered={delivered}, failed={failed}")

if delivered == 0:
    fail("No Telegram messages were delivered")
