"""Notification 도메인 — 운영자 Send 처리, mock/console 발송, notification_logs 저장.

자동 발송 트리거는 존재하지 않는다 (rules.md §1): 이 모듈의 함수는
운영자의 명시적 액션(API 요청)에 의해서만 호출된다.
P0-A 채널은 mock/console뿐이며 외부 네트워크 호출이 없다.
점수·insight를 생성하지 않고, 저장된 값을 읽어 발송 로그만 만든다.
"""

import uuid

from app import db

ALLOWED_CHANNELS = {"mock", "console"}
PREVIEW_MAX_LEN = 200


def _new_id() -> str:
    return f"notif_{uuid.uuid4().hex[:12]}"


def _print_mock_notification(title: str, channel: str, preview: str) -> None:
    # 비밀값(API key, webhook URL)은 어떤 경우에도 출력하지 않는다 (rules.md §4).
    print("=" * 56)
    print(f"[MOCK NOTIFICATION] channel={channel}")
    print(f"  {title}")
    print(f"  {preview}")
    print("=" * 56)


def send(article_id: str, channel: str = "mock", operator_id: str = "operator") -> dict:
    """운영자 Send: 저장된 digest를 mock 발송하고 notification_logs에 기록한다."""
    if channel not in ALLOWED_CHANNELS:
        raise ValueError(f"P0-A 허용 채널은 {sorted(ALLOWED_CHANNELS)}뿐이다: {channel}")
    detail = db.fetch_article_detail(article_id)
    if detail is None:
        raise LookupError(f"기사를 찾을 수 없음: {article_id}")

    article = detail["article"]
    score = detail["score"] or {}
    insight = detail["insight"] or {}
    digest = insight.get("digest_message") or article["title"]
    preview = digest.replace("\n", " ").strip()[:PREVIEW_MAX_LEN]

    _print_mock_notification(article["title"], channel, preview)

    row = {
        "id": _new_id(),
        "article_id": article_id,
        "channel": channel,
        "alert_grade": score.get("alert_grade"),
        "message_preview": preview,
        "send_status": "sent",
        "error_message": None,
        "sent_at": db.now_iso(),
    }
    db.insert_notification_log(row)
    return row


def send_test(channel: str = "mock", message: str = "HDEC Executive Radar test") -> dict:
    """발송 경로 점검용 테스트 알림. 기사와 연결되지 않는다."""
    if channel not in ALLOWED_CHANNELS:
        raise ValueError(f"P0-A 허용 채널은 {sorted(ALLOWED_CHANNELS)}뿐이다: {channel}")
    preview = (message or "").strip()[:PREVIEW_MAX_LEN]
    _print_mock_notification("(test notification)", channel, preview)
    row = {
        "id": _new_id(),
        "article_id": None,
        "channel": channel,
        "alert_grade": None,
        "message_preview": preview,
        "send_status": "sent",
        "error_message": None,
        "sent_at": db.now_iso(),
    }
    db.insert_notification_log(row)
    return row
