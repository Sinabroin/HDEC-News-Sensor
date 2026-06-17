"""Telegram 발송 스크립트 (GitHub Actions 전용).

MESSAGE env가 비어 있지 않으면 그 메시지를, 비어 있으면
scripts/build_telegram_digest.py로 mock daily digest를 생성해 발송한다.

REPORT_URL env가 설정돼 있으면 메시지에 "오늘 브리프 보기" inline URL 버튼을
붙인다 (정적 리포트 페이지 — P0-B5). 없으면 기존 텍스트 전용 발송 그대로이며
실패하지 않는다.

P0-C1.10 — 채널→1:1 봇 진입: TELEGRAM_BOT_USERNAME(또는 TELEGRAM_PERSONAL_BOT_URL)이
설정돼 있으면 "개인 질의하기" inline URL 버튼을 추가로 붙인다. 이 버튼은 봇과의
1:1 대화창을 deep link(https://t.me/<bot>?start=ask_today)로 연다 — 진입 UX 계약일
뿐, 실제 자연어 질의 응답은 inbound webhook/polling 구현(P1) 후 활성화된다.
설정이 없으면 이 버튼은 안전하게 생략되고 발송은 실패하지 않는다.

비밀값(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_IDS)과 REPORT_URL/deep link 값은 어떤
경우에도 출력하지 않는다 (정상 발송 경로) — 출력은 메시지 출처/길이, 링크 버튼
사용 여부(true/false), 발송 집계뿐이다 (rules.md §4). --dry-run-payload만 검증용으로
버튼 text/url을 출력하며, 이때도 토큰은 절대 읽거나 출력하지 않는다.
"""

import json
import os
import re
import sys
import urllib.parse
import urllib.request
from html import escape

# Telegram sendMessage 한도(4096자)보다 여유를 둔 발송 상한.
# build_telegram_digest.MESSAGE_BUDGET(3000)보다 항상 크거나 같아야 한다
# (verify_telegram_digest.py가 이 관계를 검사한다).
MAX_MESSAGE_LEN = 3500

# 정적 리포트로 연결되는 inline 버튼 라벨 (REPORT_URL이 있을 때만 사용)
BUTTON_TEXT = "오늘 브리프 보기"
# 1:1 봇 진입 deep link 버튼 (TELEGRAM_BOT_USERNAME/URL이 있을 때만 사용)
PERSONAL_BUTTON_TEXT = "개인 질의하기"
# deep link start 파라미터 — ASCII-safe 고정값 (비밀값 아님)
PERSONAL_START_PARAM = "ask_today"
TELEGRAM_DEEP_LINK_PREFIX = "https://t.me/"


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def resolve_message() -> tuple[str, str]:
    """발송할 메시지와 그 출처 라벨을 결정한다."""
    message = os.environ.get("MESSAGE", "").strip()
    if message:
        # Payloads are sent with Telegram HTML parse mode. Treat MESSAGE env as
        # plain text so literal '<'/'&' cannot break the fallback send path.
        return escape(message, quote=False), "env-message"
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from build_telegram_digest import build_digest_message

    return build_digest_message(), "mock-digest"


def resolve_report_url() -> str:
    """REPORT_URL env에서 리포트 링크를 읽는다. 비어 있거나 http(s)가 아니면
    빈 문자열을 반환해 텍스트 전용 발송으로 동작한다 (실패하지 않는다)."""
    value = os.environ.get("REPORT_URL", "").strip()
    if not value:
        return ""
    if not value.lower().startswith(("https://", "http://")):
        print("WARN: REPORT_URL format not recognized — report link disabled",
              file=sys.stderr)
        return ""
    return value


def resolve_personal_bot_url() -> str:
    """1:1 봇 진입 deep link을 결정한다 (P0-C1.10).

    우선순위: TELEGRAM_PERSONAL_BOT_URL(완성된 t.me 링크) > TELEGRAM_BOT_USERNAME로 조립.
    안전하지 않은 값(잘못된 형식·비-t.me)이면 빈 문자열을 반환해 버튼을 생략한다
    (발송은 실패하지 않는다). 어떤 비밀값도 URL에 넣지 않는다.
    """
    url = os.environ.get("TELEGRAM_PERSONAL_BOT_URL", "").strip()
    if url:
        if url.lower().startswith(TELEGRAM_DEEP_LINK_PREFIX):
            return url
        print("WARN: TELEGRAM_PERSONAL_BOT_URL format not recognized — "
              "personal button disabled", file=sys.stderr)
        return ""
    username = os.environ.get("TELEGRAM_BOT_USERNAME", "").strip().lstrip("@")
    if not username:
        return ""
    # Telegram 봇 username 규칙(영문/숫자/_)만 허용 — deep link에 비ASCII/비밀값 유입 차단.
    if not re.fullmatch(r"[A-Za-z0-9_]{3,64}", username):
        print("WARN: TELEGRAM_BOT_USERNAME format not recognized — "
              "personal button disabled", file=sys.stderr)
        return ""
    return f"{TELEGRAM_DEEP_LINK_PREFIX}{username}?start={PERSONAL_START_PARAM}"


def build_payload(chat_id: str, message: str, report_url: str,
                  personal_url: str = "") -> dict:
    """sendMessage payload. report_url/personal_url이 있으면 inline URL 버튼을 붙인다.

    버튼 순서: [오늘 브리프 보기][개인 질의하기]. 둘 다 없으면 reply_markup을 넣지 않는다
    (기존 텍스트 전용 동작 보존). 채널 메시지에서 임원이 리포트로 가거나 1:1 봇으로 진입한다.
    """
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }
    buttons = []
    if report_url:
        buttons.append({"text": BUTTON_TEXT, "url": report_url})
    if personal_url:
        buttons.append({"text": PERSONAL_BUTTON_TEXT, "url": personal_url})
    if buttons:
        payload["reply_markup"] = json.dumps(
            {"inline_keyboard": [buttons]}, ensure_ascii=False)
    return payload


def dry_run_payload(message: str) -> None:
    """발송 없이 inline 버튼 payload를 구성해 출력한다 (검증/문서용, 비밀값 불필요).

    토큰·chat id는 읽지도 출력하지도 않는다 — 버튼 text/url과 enabled 플래그만 출력한다.
    REPORT_URL/deep link는 비밀값이 아니며, 이 모드는 버튼 계약을 눈으로/검증기로
    확인하기 위한 것이다.
    """
    report_url = resolve_report_url()
    personal_url = resolve_personal_bot_url()
    payload = build_payload("DRY_RUN", message, report_url, personal_url)
    print(f"Report link enabled: {'true' if report_url else 'false'}")
    print(f"Personal bot link enabled: {'true' if personal_url else 'false'}")
    markup = payload.get("reply_markup")
    if markup:
        for btn in json.loads(markup)["inline_keyboard"][0]:
            print(f"button: {btn['text']} -> {btn['url']}")
    else:
        print("button: (none)")


def main() -> None:
    # --dry-run-payload <message>: 발송/비밀값 없이 버튼 payload만 출력 (검증/문서용).
    argv = sys.argv[1:]
    if argv and argv[0] == "--dry-run-payload":
        dry_run_payload(argv[1] if len(argv) > 1 else "dry-run")
        return

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
    report_url = resolve_report_url()
    personal_url = resolve_personal_bot_url()
    if len(message) > MAX_MESSAGE_LEN:
        message = message[: MAX_MESSAGE_LEN - 3] + "..."
    print(f"Message source: {message_source} ({len(message)} chars)")
    print(f"Report link enabled: {'true' if report_url else 'false'}")
    print(f"Personal bot link enabled: {'true' if personal_url else 'false'}")

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    delivered = 0
    failed = 0

    for chat_id in chat_ids:
        payload = build_payload(chat_id, message, report_url, personal_url)
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
