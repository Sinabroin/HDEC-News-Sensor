"""뉴스 신선도 정책 (D7-AD-X) — 표시 레이어 전용.

즉시확인(now)과 일반 뉴스 후보의 recency window를 단일 소스로 정의한다.
mock/demo에서는 오래된 fixture가 보일 수 있으나 live에서는 stale immediate를 차단한다.
점수/등급/DB를 바꾸지 않는다 — build_static_dashboard 표시 필터만 담당.
"""

from __future__ import annotations

from datetime import datetime, timezone

# 즉시확인: 최근 72시간(3일) 우선 — live에서 그 이전은 now 뱅크에서 제외
IMMEDIATE_MAX_AGE_HOURS = 72
# 일반 뉴스 후보 soft window (라벨/정렬용 — 31일 초과는 '최근' 라벨 생략과 동일 철학)
GENERAL_RECENCY_DAYS = 30


def _parse_pub(raw) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def hours_since_published(published_at, ref_dt: datetime | None = None) -> float | None:
    """published_at → ref_dt까지 경과 시간(시간). 파싱 불가면 None."""
    pub = _parse_pub(published_at)
    if pub is None:
        return None
    ref = ref_dt or datetime.now(timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    return (ref - pub).total_seconds() / 3600.0


def passes_immediate_recency(published_at, news_data_mode: str,
                             ref_dt: datetime | None = None) -> bool:
    """live: 즉시확인에 stale(>72h) 기사를 올리지 않는다. mock: 필터 없음(데모 badge로 구분)."""
    if (news_data_mode or "mock").strip().lower() != "live":
        return True
    age = hours_since_published(published_at, ref_dt)
    if age is None:
        return False  # live에서 시각 불명 — 즉시확인에 올리지 않음(보수적)
    return age <= IMMEDIATE_MAX_AGE_HOURS


def general_recency_label(published_at, ref_date) -> str:
    """ref_date(brief date) 기준 일반 신선도 라벨 — build_static_dashboard._freshness와 동일 창."""
    if not published_at or ref_date is None:
        return ""
    pub = _parse_pub(published_at)
    if pub is None:
        return ""
    days = (ref_date - pub.astimezone(timezone.utc).date()).days
    if days <= 0:
        return "오늘"
    if days <= 7:
        return "최근 7일"
    if days <= GENERAL_RECENCY_DAYS:
        return "최근 30일"
    return ""
