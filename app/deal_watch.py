"""D7-AF Deal Watch 분류와 compact dashboard row 생성.

사업영역 렌즈와 독립적인 다중 label이다. 저장된 기사 metadata만 파생하며 점수·등급,
수집 결과 또는 기사 원문을 만들지 않는다.
"""

from __future__ import annotations

import json
from urllib.parse import urlparse

from app import thebell_watch

LABELS = {
    "project": "프로젝트",
    "construction_industry": "건설산업",
    "hmg": "HMG",
    "major_groups": "주요 그룹",
    "global_issues": "글로벌 이슈",
    "ai_infra": "AI 인프라",
    "capital_markets": "자본시장",
}

_RULES = {
    "project": (
        "pf", "본pf", "본 pf", "개발사업", "오피스 개발", "선매각", "시행사",
        "프로젝트 금융", "데이터센터 캠퍼스", "복합개발", "인허가",
    ),
    "construction_industry": (
        "스마트건설", "시설안전진단", "시설 안전진단", "드론", "유지보수",
        "공공 레퍼런스", "건설 테크", "건설테크", "코매퍼",
    ),
    "hmg": (
        "현대차그룹", "현대오토에버", "현대로템", "kai", "aam", "sdv", "sdf",
        "rx사업실", "rx 사업실", "ax 조직",
    ),
    "major_groups": (
        "삼성", "lg", "ls그룹", "ls 그룹", "gs그룹", "gs 그룹", "sk그룹", "sk 그룹",
        "한화", "그룹 조직개편", "cfo 조직", "cfo 산하", "로보틱스사업센터",
    ),
    "global_issues": (
        "수출통제", "중동", "호르무즈", "유가", "공급망", "지정학", "글로벌 정책",
        "anthropic", "앤트로픽", "meta", "메타", "fable", "mythos", "kpmg",
        "ai 환각", "claude sonnet", "virtue ai",
    ),
    "ai_infra": (
        "ai 데이터센터", "gpu", "전력망", "전력기기", "냉각", "반도체 클러스터",
        "반도체 메가투자", "ai compute", "ai 컴퓨팅", "ai칩", "ai chip",
        "18.4gw", "15gw", "데이터센터 전력",
    ),
    "capital_markets": (
        "pf", "본pf", "본 pf", "자금조달", "kkr", "투자 유치", "지분 매각",
        "jv", "m&a", "인수합병", "조달 구조",
    ),
}

_PRIMARY_ORDER = (
    "project", "capital_markets", "hmg", "construction_industry",
    "ai_infra", "major_groups", "global_issues",
)

_IMPLICATION = {
    "project": "사업성·인허가·프로젝트 금융 구조 점검 대상입니다.",
    "construction_industry": "시공·안전·유지관리 경쟁력 변화 점검 대상입니다.",
    "hmg": "현대차그룹 기술·조직·사업 포트폴리오 변화 신호입니다.",
    "major_groups": "주요 그룹의 투자·조직 변화가 발주·협업 구도에 미칠 영향 점검 대상입니다.",
    "global_issues": "글로벌 정책·기술·공급망 변화의 국내 사업 영향 점검 대상입니다.",
    "ai_infra": "데이터센터·반도체·전력 인프라 발주와 공급망 영향 점검 대상입니다.",
    "capital_markets": "자금조달·지분·투자 구조가 프로젝트 실행력에 미칠 영향 점검 대상입니다.",
}


def classify_labels(title: str, snippet: str = "", source: str = "") -> list[str]:
    haystack = f"{title} {snippet} {source}".casefold()
    return [label for label, patterns in _RULES.items()
            if any(pattern.casefold() in haystack for pattern in patterns)]


def primary_label(labels: list[str]) -> str:
    return next((label for label in _PRIMARY_ORDER if label in labels), "")


def _metadata(row: dict) -> dict:
    raw = row.get("source_metadata_json") or row.get("source_metadata") or {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            raw = {}
    return raw if isinstance(raw, dict) else {}


def _domain(url: str) -> str:
    try:
        return (urlparse(str(url or "")).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def row_for_dashboard(row: dict) -> dict | None:
    labels = classify_labels(
        str(row.get("title") or ""), str(row.get("snippet") or ""),
        str(row.get("source") or ""))
    primary = primary_label(labels)
    url = str(row.get("url") or "").strip()
    if not primary or not url.startswith(("http://", "https://")):
        return None
    metadata = _metadata(row)
    provider = str(metadata.get("provider") or "")
    is_thebell = thebell_watch.is_thebell_url(url)
    access_type = "subscription_required" if is_thebell else "unknown"
    collection_method = (
        "naver_search_api" if "naver_news_api" in provider
        else "google_news_rss" if "google_news_rss" in provider
        else "public_preview"
    )
    return {
        "article_id": str(row.get("id") or row.get("article_id") or ""),
        "title": str(row.get("title") or ""),
        "source": "더벨" if is_thebell else str(
            row.get("display_source") or row.get("source") or "출처 미상"),
        "deal_watch_label": primary,
        "deal_watch_label_name": LABELS[primary],
        "deal_watch_labels": labels,
        "published_at": str(row.get("published_at") or ""),
        "implication": _IMPLICATION[primary],
        "url": url,
        "source_domain": _domain(url),
        "access_type": access_type,
        "access_limited": is_thebell,
        "subscription_required": is_thebell,
        "collection_method": collection_method,
        "copyright_note": thebell_watch.COPYRIGHT_NOTE,
    }


def build_dashboard_rows(rows: list[dict], limit: int = 14) -> list[dict]:
    """전 scored pool에서 최신 Deal Watch 후보를 고른다. 서로 다른 URL은 보존한다."""
    candidates, seen = [], set()
    for row in rows or []:
        item = row_for_dashboard(row)
        if not item:
            continue
        key = item["url"]
        if key in seen:
            continue
        seen.add(key)
        candidates.append(item)
    candidates.sort(
        key=lambda item: (item.get("published_at") or "", item.get("article_id") or ""),
        reverse=True,
    )
    return candidates[:max(0, int(limit))]
