"""Telegram 발송 스크립트 (GitHub Actions 전용).

MESSAGE env가 비어 있지 않으면 그 메시지를, 비어 있으면
scripts/build_telegram_digest.py로 mock daily digest를 생성해 발송한다.

비밀값(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_IDS)은 어떤 경우에도 출력하지 않는다 —
출력은 메시지 출처/길이와 발송 집계뿐이다 (rules.md §4).
"""

import json
import os
import sys
import urllib.parse
import urllib.request

# Telegram sendMessage 한도(4096자)보다 여유를 둔 발송 상한.
# build_telegram_digest.MESSAGE_BUDGET(3000)보다 항상 크거나 같아야 한다
# (verify_telegram_digest.py가 이 관계를 검사한다).
MAX_MESSAGE_LEN = 3500


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def resolve_message() -> tuple[str, str]:
    """발송할 메시지와 그 출처 라벨을 결정한다."""
    message = os.environ.get("MESSAGE", "").strip()
    if message:
        return message, "env-message"
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from build_telegram_digest import build_digest_message

    return build_digest_message(), "mock-digest"


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_ids_raw = os.environ.get("TELEGRAM_CHAT_IDS", "").strip()

    if not token:
        fail("TELEGRAM_BOT_TOKEN is missing")
    if not chat_ids_raw:
        fail("TELEGRAM_CHAT_IDS is missing")

    chat_ids = [item.strip() for item in chat_ids_raw.split(",") if item.strip()]
    if not chat_ids:
        fail("No valid chat ids found")

    message, message_source = resolve_message()
    if len(message) > MAX_MESSAGE_LEN:
        message = message[: MAX_MESSAGE_LEN - 3] + "..."
    print(f"Message source: {message_source} ({len(message)} chars)")

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
            print(f"Telegram send exception for one recipient: {type(exc).__name__}",
                  file=sys.stderr)

    print(f"Telegram delivery summary: delivered={delivered}, failed={failed}")

    if delivered == 0:
        fail("No Telegram messages were delivered")


if __name__ == "__main__":
    main()
