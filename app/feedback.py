"""Feedback 도메인 — feedback row 저장만 한다.

P0-A에서 피드백은 저장 성공이 목표다 (PRD §16.1).
scoring 가중치나 keyword_rules를 변경하지 않는다 — 그것은 Day-2 영역이다.
"""

import uuid

from app import db

FEEDBACK_TYPES = {
    "positive",             # 좋음
    "irrelevant",           # 불필요
    "instant_alert",        # 즉시알림급
    "weekly_report",        # 주간보고급
    "boost_topic",          # 이 주제 강화
    "exclude_source",       # 이 언론사 제외
    "exclude_keyword",      # 이 키워드 제외
    "classify_competitor",  # 경쟁사 동향으로 분류
    "classify_macro",       # 거시경제로 분류
}


def save(article_id: str, feedback_type: str, feedback_value: str = None,
         operator_id: str = "operator") -> dict:
    if feedback_type not in FEEDBACK_TYPES:
        raise ValueError(
            f"허용되지 않는 feedback_type: {feedback_type} (허용: {sorted(FEEDBACK_TYPES)})"
        )
    row = {
        "id": f"fb_{uuid.uuid4().hex[:12]}",
        "article_id": article_id,
        "feedback_type": feedback_type,
        "feedback_value": feedback_value,
        "operator_id": operator_id or "operator",
        "created_at": db.now_iso(),
        "applied_to_rules": 0,
    }
    db.insert_feedback(row)
    return row
