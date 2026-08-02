#!/usr/bin/env python3
"""Build the standalone HDEC News Censor static surface.

The page reuses the sealed executive-brief and publisher-direct policies.  It
does not fetch in the browser, send notifications, mutate production state, or
add itself to an existing page's navigation.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys
import tempfile
import unicodedata
from collections import Counter
from difflib import SequenceMatcher
from datetime import date, datetime, timedelta, timezone
from html import escape
from pathlib import Path
from typing import Iterable, Mapping
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
for item in (ROOT, SCRIPTS):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from app import publisher_direct  # noqa: E402
from build_executive_brief import load_brief_json  # noqa: E402

TEMPLATE = ROOT / "templates" / "news_censor.html"
LOGO = ROOT / "docs" / "assets" / "brand" / "hdec-logo.svg"
DEFAULT_OUTPUT_ROOT = ROOT / "docs" / "news-censor"
REFERENCE_SHA256 = "c4a1d129a9e8b6d824b961e2042f345cfc2eb405dcbc488a542e5bc6cee14804"
CONTRACT = "D7-AK-6E-R4-STANDALONE-NEWS-CENSOR"
ARTIFACT_CONTRACT = "HDEC_VALIDATED_EXECUTIVE_BRIEF_V1"
DISPLAY_CONTRACT = "HDEC_NEWS_CENSOR_DISPLAY_V1"
DISPLAY_ARTICLE_CONTRACT = "HDEC_NEWS_CENSOR_DISPLAY_ARTICLE_V1"
DISPLAY_FIELD = "news_censor_display_articles"
KST = timezone(timedelta(hours=9))

LIVE_HEALTHY_WITH_ARTICLES = "LIVE_HEALTHY_WITH_ARTICLES"
LIVE_HEALTHY_NO_ELIGIBLE_ARTICLES = "LIVE_HEALTHY_NO_ELIGIBLE_ARTICLES"
LIVE_COLLECTION_FAILED = "LIVE_COLLECTION_FAILED"
LIVE_FALLBACK_REJECTED = "LIVE_FALLBACK_REJECTED"
HEALTHY_LIVE_STATUSES = {
    LIVE_HEALTHY_WITH_ARTICLES,
    LIVE_HEALTHY_NO_ELIGIBLE_ARTICLES,
}
FAILED_LIVE_STATUSES = {LIVE_COLLECTION_FAILED, LIVE_FALLBACK_REJECTED}


class LiveBriefRejected(RuntimeError):
    """The explicit input artifact cannot authorize a production write."""

CATEGORY_LABELS = {
    "all": "홈",
    "biz": "사업영역",
    "peers": "동종사",
    "hdec": "현대그룹",
    "safety": "안전품질",
    "global": "해외지정학",
    "ai": "AI",
}
PRIMARY_CATEGORY_IDS = tuple(key for key in CATEGORY_LABELS if key != "all")
FRESH_MAX_HOURS = 72
BACKFILL_MAX_HOURS = 7 * 24
CATEGORY_TARGET = 3
PUBLIC_HARD_MAX = 40
PUBLIC_TARGET_MIN = 20
PUBLISHER_SHARE_PREFERENCE = 0.40
STAGE_LOSS_KEYS = (
    "source_quality_rejected",
    "publisher_authority_rejected",
    "portal_or_discovery_url_rejected",
    "missing_published_at",
    "category_unresolved",
    "relevance_rejected",
    "stale_outside_primary_window",
    "stale_outside_backfill_window",
    "canonical_duplicate",
    "event_duplicate",
    "publisher_share_deprioritized",
    "category_quota_deprioritized",
    "hard_cap_truncated",
    "renderer_missing_required_field",
    "template_filter_excluded",
    "serialization_loss",
    "duplicate_dom_key",
    "other_explicit_reason",
)

VERDICT_STYLE = {
    "기회": ("기회", "#1E5F8A"),
    "리스크": ("리스크", "#B3372B"),
    "위험": ("리스크", "#B3372B"),
    "관찰": ("관찰", "#68716A"),
}


def _parse_datetime(value: object) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=KST)
    return parsed.astimezone(KST)


def _edition_date(value: str) -> date:
    if value:
        return date.fromisoformat(value)
    return datetime.now(KST).date()


def _metadata(row: Mapping) -> dict:
    value = row.get("source_metadata") or row.get("source_metadata_json") or {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            value = {}
    return dict(value) if isinstance(value, Mapping) else {}


def _category_tokens(row: Mapping) -> set[str]:
    raw = row.get("category_memberships")
    if not isinstance(raw, (list, tuple, set)):
        return {"all"}
    valid = {str(value) for value in raw if str(value) in PRIMARY_CATEGORY_IDS}
    return {"all", *valid}


def _candidate_rows(brief: Mapping) -> Iterable[dict]:
    """Consume the one canonical display field; legacy surfaces are never fallback."""
    for item in brief.get(DISPLAY_FIELD) or []:
        if isinstance(item, Mapping):
            yield dict(item)


def _verdict(row: Mapping) -> tuple[str, str]:
    raw = str(row.get("opportunity_or_risk") or "").strip()
    if raw in VERDICT_STYLE:
        return VERDICT_STYLE[raw]
    action = str(row.get("action_label") or row.get("alert_grade") or "")
    if "즉시" in action or "중요" in action:
        return "중요", "#0B6B3A"
    return "관찰", "#68716A"


def _initials(value: object) -> str:
    text = re.sub(r"[^0-9A-Za-z가-힣]", "", str(value or ""))
    return (text[:2] or "HDEC").upper()


def _published_rank(value: object) -> float:
    parsed = _parse_datetime(value)
    return parsed.timestamp() if parsed else 0.0


def validate_brief_artifact(brief: Mapping, *, require_live: bool) -> None:
    """Validate explicit handoff provenance; never infer live from process env."""
    if brief.get("artifact_contract") != ARTIFACT_CONTRACT:
        raise LiveBriefRejected("validated executive brief artifact contract missing")
    display_contract = brief.get("news_censor_display_contract") or {}
    display_rows = brief.get(DISPLAY_FIELD)
    if (
        not isinstance(display_contract, Mapping)
        or display_contract.get("contract") != DISPLAY_CONTRACT
        or display_contract.get("field") != DISPLAY_FIELD
        or not isinstance(display_rows, list)
        or int(display_contract.get("candidate_count") or 0) != len(display_rows)
    ):
        raise LiveBriefRejected("canonical News Censor display contract missing or inconsistent")
    if not require_live:
        return

    status = str(brief.get("collection_status") or "")
    health = brief.get("collector_health") or {}
    delivery = brief.get("publisher_direct_delivery") or {}
    if status in FAILED_LIVE_STATUSES:
        category = (
            health.get("failure_category")
            or brief.get("collection_failure_category")
            or "live_collection_failed"
        )
        raise LiveBriefRejected(f"{status}: {category}")
    if status not in HEALTHY_LIVE_STATUSES:
        raise LiveBriefRejected("collector health is absent or indeterminate")
    if brief.get("news_data_mode") != "live":
        raise LiveBriefRejected("healthy status conflicts with non-live artifact mode")
    if brief.get("news_fallback_used") is True:
        raise LiveBriefRejected("live artifact reports fallback_used=true")
    if str(health.get("status") or "") != status:
        raise LiveBriefRejected("collector health status conflicts with artifact status")
    if int(health.get("successful_source_count") or 0) <= 0:
        raise LiveBriefRejected("no successful live source response was proven")
    portal_count = max(
        int(health.get("final_portal_url_count") or 0),
        int(delivery.get("final_portal_urls") or 0),
    )
    if portal_count:
        raise LiveBriefRejected("publisher delivery contains portal URLs")
    eligible = int(
        health.get("publisher_direct_eligible_count")
        or delivery.get("eligible_count")
        or 0
    )
    if status == LIVE_HEALTHY_WITH_ARTICLES and eligible <= 0:
        raise LiveBriefRejected("with-articles health has zero eligible publishers")
    if status == LIVE_HEALTHY_NO_ELIGIBLE_ARTICLES and eligible != 0:
        raise LiveBriefRejected("healthy-empty health conflicts with eligible article count")


def _subfilter_labels(row: Mapping) -> list[str]:
    labels: list[str] = []
    values: list[object] = [
        row.get("topic"),
        row.get("category_label"),
        row.get("radar_label"),
        row.get("executive_label"),
        *(row.get("secondary_labels") or []),
    ]
    for value in values:
        label = re.sub(r"\s+", " ", str(value or "")).strip()
        if label and label not in labels and label not in CATEGORY_LABELS.values():
            labels.append(label[:40])
    return labels[:6]


def _market_pane(brief: Mapping) -> dict:
    snapshot = brief.get("market_snapshot") or {}
    rows = []
    for item in (snapshot.get("items") or [])[:8]:
        if not isinstance(item, Mapping):
            continue
        rows.append({
            "id": str(item.get("id") or ""),
            "label": str(item.get("label_kr") or item.get("id") or "지표"),
            "value": item.get("value"),
            "unit": str(item.get("unit") or ""),
            "data_mode": str(item.get("data_mode") or "unavailable"),
            "is_stale": bool(item.get("is_stale", True)),
            "proxy_for": str(item.get("proxy_for") or ""),
            "source": str(item.get("source_provider") or ""),
            "as_of": str(item.get("as_of") or ""),
        })
    return {
        "status": str(snapshot.get("mode") or "unavailable"),
        "source": str(snapshot.get("source_summary") or "시장지표 미연동"),
        "as_of": str(snapshot.get("as_of") or snapshot.get("updated_at") or ""),
        "disclaimer": str(snapshot.get("disclaimer") or ""),
        "items": rows,
    }


def _weather_rail(brief: Mapping) -> dict:
    snapshot = brief.get("weather_snapshot") or {}
    rows = []
    for item in (snapshot.get("weather_rows") or []):
        if not isinstance(item, Mapping):
            continue
        rows.append({
            "region": str(item.get("region") or ""),
            "basis": str(item.get("basis") or ""),
            "forecast_at": str(item.get("target_local") or ""),
            "grade": str(item.get("risk_grade") or "확인 필요"),
            "temperature_c": item.get("temp_c"),
            "precipitation_probability": item.get("precip_prob"),
            "gust_ms": item.get("gust_ms"),
            "status": str(item.get("row_status") or "unavailable"),
            "status_note": str(item.get("status_note") or ""),
        })
    return {
        "status": str(snapshot.get("weather_data_mode") or "unavailable"),
        "source": str(snapshot.get("weather_source") or ""),
        "updated_at": str(snapshot.get("weather_updated_at") or ""),
        "forecast_at": str(snapshot.get("weather_target_time") or ""),
        "unavailable_reason": str(
            snapshot.get("weather_unavailable_reason")
            or "기상 데이터 미수신 — 값을 만들지 않습니다."
        ),
        "rows": rows,
    }


def _safety_rail(brief: Mapping) -> dict:
    items = []
    for cluster in (brief.get("risk_event_clusters") or [])[:4]:
        if not isinstance(cluster, Mapping):
            continue
        support = [
            article for article in (cluster.get("supporting_articles") or [])
            if isinstance(article, Mapping)
            and bool(str(article.get("title") or "").strip())
            and bool(str(article.get("source") or "").strip())
            and bool(str(article.get("published_at") or "").strip())
            and bool(publisher_direct.normalize_publisher_canonical_url(article.get("url")))
        ]
        if not support:
            continue
        items.append({
            "title": str(cluster.get("event_title") or "검증 안전 신호"),
            "severity": str(cluster.get("severity_label") or "모니터링"),
            "article_count": len(support),
            "source_count": len({str(row.get("source") or "") for row in support}),
            "latest_at": str(cluster.get("latest_published_at") or ""),
        })
    timestamps = [item["latest_at"] for item in items if item["latest_at"]]
    return {
        "status": "verified" if items else "unavailable",
        "source": "검증된 publisher-direct 기사 클러스터" if items else "",
        "as_of": max(timestamps) if timestamps else "",
        "unavailable_reason": (
            "검증 가능한 안전·사고 지표가 없습니다."
            if not items else ""
        ),
        "items": items,
    }


def _fallback_image_data(article: Mapping) -> str:
    label = escape(str(article.get("initials") or "HDEC"))
    tint = str(article.get("tint") or "#0B6B3A").replace("#", "%23")
    svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 640 360'>"
        f"<rect width='640' height='360' fill='{tint}'/>"
        "<path d='M0 260L180 150l130 82 150-140 180 145v123H0z' fill='%23ffffff' fill-opacity='.14'/>"
        f"<text x='40' y='315' fill='white' font-size='52' font-family='sans-serif' font-weight='700'>{label}</text>"
        "</svg>"
    )
    return "data:image/svg+xml," + svg.replace(" ", "%20").replace("'", "%27")


def _freshness_info(row: Mapping, reference: datetime) -> dict:
    published = _parse_datetime(row.get("published_at"))
    if published is None:
        return {
            "status": "missing",
            "label": "발행시각 없음",
            "rank": 9,
            "age_hours": None,
            "is_backfill": False,
        }
    age_hours = (reference - published).total_seconds() / 3600
    if age_hours < -6:
        return {
            "status": "future_outlier",
            "label": "발행시각 검증 필요",
            "rank": 9,
            "age_hours": round(age_hours, 1),
            "is_backfill": False,
        }
    age_hours = max(0.0, age_hours)
    if age_hours <= FRESH_MAX_HOURS:
        status, label, rank = "primary", "최신 72시간", 0
    elif age_hours <= BACKFILL_MAX_HOURS:
        status, label, rank = "backfill", "7일 이내 카테고리 보강", 1
    else:
        status, label, rank = "outside_backfill", "7일 초과 제외", 2
    return {
        "status": status,
        "label": label,
        "rank": rank,
        "age_hours": round(age_hours, 1),
        "is_backfill": status == "backfill",
    }


def _publisher_key(row: Mapping) -> str:
    url = publisher_direct.publisher_url(row)
    try:
        host = (urlparse(url).hostname or "").casefold().removeprefix("www.")
    except ValueError:
        host = ""
    return host or str(row.get("source") or "unknown").strip().casefold()


def _coverage_sort_key(item: Mapping) -> tuple:
    row = item["row"]
    return (
        int(item["freshness"]["rank"]),
        int(bool(row.get("carried_forward"))),
        -float(row.get("final_score") or 0),
        -_published_rank(row.get("published_at")),
        str(row.get("title") or ""),
    )


_EVENT_ENTITY_TERMS = (
    "현대건설", "현대엔지니어링", "현대자동차그룹", "삼성물산", "gs건설",
    "대우건설", "dl이앤씨", "포스코이앤씨", "롯데건설", "sk에코플랜트",
    "국토교통부", "고용노동부", "행정안전부", "과학기술정보통신부",
)
_EVENT_TYPES = {
    "contract": ("수주", "계약", "낙찰", "우선협상"),
    "incident": ("사고", "붕괴", "사망", "중대재해", "화재"),
    "regulatory": ("제재", "처분", "과징금", "영업정지", "법안", "규제"),
    "milestone": ("착공", "준공", "개통", "완공", "상업운전"),
    "investment": ("투자", "증설", "출자", "인수"),
    "announcement": ("발표", "공식", "공고", "보도자료"),
    "partnership": ("협약", "mou", "파트너십", "협력"),
}
_EVENT_STOPWORDS = {
    "관련", "대한", "위한", "통해", "추진", "사업", "건설", "건설사", "산업",
    "정부", "기업", "기술", "시장", "글로벌", "해외", "프로젝트", "뉴스", "발표",
    "ai", "인공지능", "안전", "에너지", "전력", "데이터센터",
}
_EVENT_TOKEN_RE = re.compile(r"[0-9a-z가-힣]+", re.IGNORECASE)
_MATERIAL_MARKER_RE = re.compile(
    r"\d[\d,.]*(?:조|억|만|%|mw|gw|km|명|건|원|달러|개월|년)",
    re.IGNORECASE,
)


def _normalized_event_title(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = re.sub(r"\[[^]]*]|\([^)]*\)", " ", text)
    return " ".join(_EVENT_TOKEN_RE.findall(text))


def _event_evidence(row: Mapping) -> dict:
    title = _normalized_event_title(row.get("title"))
    tokens = {
        token for token in title.split()
        if len(token) >= 2 and token not in _EVENT_STOPWORDS
    }
    entities = {term for term in _EVENT_ENTITY_TERMS if term.casefold() in title}
    types = {
        key for key, markers in _EVENT_TYPES.items()
        if any(marker in title for marker in markers)
    }
    return {
        "title": title,
        "tokens": tokens,
        "entities": entities,
        "types": types,
        "material": set(_MATERIAL_MARKER_RE.findall(title)),
    }


def _same_material_event(left: Mapping, right: Mapping) -> bool:
    a = _event_evidence(left)
    b = _event_evidence(right)
    if not a["title"] or not b["title"]:
        return False
    if a["title"] == b["title"]:
        return True
    left_time = _parse_datetime(left.get("published_at"))
    right_time = _parse_datetime(right.get("published_at"))
    if not left_time or not right_time:
        return False
    if abs((left_time - right_time).total_seconds()) > 36 * 3600:
        return False
    if a["material"] and b["material"] and a["material"] != b["material"]:
        return False
    shared_types = a["types"] & b["types"]
    shared_entities = a["entities"] & b["entities"]
    shared_tokens = a["tokens"] & b["tokens"]
    union = a["tokens"] | b["tokens"]
    jaccard = len(shared_tokens) / max(1, len(union))
    similarity = SequenceMatcher(None, a["title"], b["title"]).ratio()
    if shared_types and shared_entities:
        return len(shared_tokens) >= 3 and (jaccard >= 0.58 or similarity >= 0.82)
    return len(shared_tokens) >= 4 and jaccard >= 0.75 and similarity >= 0.90


def _safe_article_id(row: Mapping, canonical: str, index: int) -> str:
    identity = str(row.get("article_id") or row.get("id") or canonical or index)
    return "a_" + hashlib.sha256(
        f"{identity}|{canonical}|{index}".encode("utf-8")
    ).hexdigest()[:16]


def _rank_with_preferences(rows: list[dict]) -> tuple[list[dict], dict]:
    """Order all rows for category/publisher balance without discarding any."""
    remaining = sorted(rows, key=_coverage_sort_key)
    ordered: list[dict] = []
    publisher_counts: dict[str, int] = {}
    category_counts = {key: 0 for key in PRIMARY_CATEGORY_IDS}
    base_index = {item["record_id"]: index for index, item in enumerate(remaining)}
    while remaining:
        def preference(item: Mapping) -> tuple:
            new_publisher = publisher_counts.get(item["publisher_key"], 0) == 0
            category_gain = sum(
                category_counts[key] < CATEGORY_TARGET
                for key in item["categories"] if key in category_counts
            )
            return (
                int(item["freshness"]["rank"]),
                int(bool(item["row"].get("carried_forward"))),
                -int(new_publisher and len(publisher_counts) < 6),
                -category_gain,
                publisher_counts.get(item["publisher_key"], 0),
                base_index[item["record_id"]],
            )

        chosen = min(remaining, key=preference)
        remaining.remove(chosen)
        ordered.append(chosen)
        publisher = chosen["publisher_key"]
        publisher_counts[publisher] = publisher_counts.get(publisher, 0) + 1
        for category in chosen["categories"]:
            if category in category_counts:
                category_counts[category] += 1
    largest = max(publisher_counts.values(), default=0)
    share = largest / len(ordered) if ordered else 0.0
    return ordered, {
        "distinct_publishers": len(publisher_counts),
        "largest_publisher_share": round(share, 4),
        "diversity_relaxed": bool(ordered and share > PUBLISHER_SHARE_PREFERENCE),
    }


def select_display_articles(
    brief: Mapping,
    *,
    limit: int,
    reference: datetime,
) -> tuple[list[dict], dict]:
    """Select the canonical public edition and return private, ID-safe audit data."""
    rows = list(_candidate_rows(brief))
    health = brief.get("collector_health") or {}
    resolution = health.get("publisher_resolution") or {}
    loss_details = {
        key: {"count": 0, "article_ids": []} for key in STAGE_LOSS_KEYS
    }
    other_reasons: dict[str, int] = {}

    def lose(reason: str, article_id: str = "", count: int = 1) -> None:
        loss_details[reason]["count"] += max(0, int(count))
        if article_id:
            loss_details[reason]["article_ids"].append(article_id)

    upstream_source_rejected = int(health.get("source_quality_rejected_count") or 0)
    lose("source_quality_rejected", count=upstream_source_rejected)
    upstream_duplicate = int(health.get("pre_resolution_duplicate_count") or 0)
    lose("canonical_duplicate", count=upstream_duplicate)
    budget_exhausted = int(resolution.get("budget_exhausted_count") or 0)
    if budget_exhausted:
        lose("other_explicit_reason", count=budget_exhausted)
        other_reasons["publisher_resolution_budget_exhausted"] = budget_exhausted
    explicit_outcomes = resolution.get("outcomes") or {}
    explicit_skip_reasons = {
        key
        for key in explicit_outcomes
        if str(key).startswith("skipped_")
    }
    for reason in sorted(explicit_skip_reasons):
        count = max(0, int(explicit_outcomes.get(reason) or 0))
        if count:
            other_reasons[reason] = count
    quarantine_reasons = health.get("quarantine_reason_counts") or {}
    resolution_failed = 0
    for reason, raw_count in quarantine_reasons.items():
        count = max(0, int(raw_count or 0))
        if reason in {
            "publisher_resolution_budget_exhausted",
            "source_quality_filtered_before_publisher_resolution",
        } or reason in explicit_skip_reasons:
            continue
        resolution_failed += count
        if "PORTAL" in str(reason).upper() or "portal" in str(reason).casefold():
            lose("portal_or_discovery_url_rejected", count=count)
        else:
            lose("publisher_authority_rejected", count=count)

    decisions: dict[str, dict] = {}
    eligible: list[dict] = []
    source_quality_rows = 0
    display_policy_rows = 0
    primary_rows = 0
    backfill_rows = 0
    for index, raw in enumerate(rows):
        assessment = publisher_direct.assess_delivery_eligibility(
            raw, relevance_qualified=True
        )
        canonical = assessment.publisher_url
        safe_id = _safe_article_id(raw, canonical, index)
        categories = _category_tokens(raw)
        freshness = _freshness_info(raw, reference)
        record = {
            "safe_article_id": safe_id,
            "canonical_url_host": (urlparse(canonical).hostname or "").casefold().removeprefix("www."),
            "source": str(raw.get("display_source") or raw.get("source") or ""),
            "publication_timestamp": str(raw.get("published_at") or ""),
            "assigned_categories": sorted(categories - {"all"}),
            "display_eligibility": False,
            "freshness_status": freshness["status"],
            "backfill_status": "not_applicable",
            "carried_forward": bool(raw.get("carried_forward")),
            "current_run_seen": bool(raw.get("current_run_seen", True)),
            "canonical_cluster": (
                "c_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
                if canonical else ""
            ),
            "event_cluster": "",
            "selected": False,
            "final_rejection_reason": "",
        }
        decisions[safe_id] = record

        def reject(reason: str) -> None:
            record["final_rejection_reason"] = reason
            lose(reason, safe_id)

        if raw.get("display_article_contract") != DISPLAY_ARTICLE_CONTRACT:
            reject("serialization_loss")
            continue
        if raw.get("source_quality_passed") is not True:
            reject("source_quality_rejected")
            continue
        source_quality_rows += 1
        if not assessment.eligible:
            if assessment.reason == "published_at_and_fallback_missing":
                reject("missing_published_at")
            elif not canonical and publisher_direct.portal_provider(raw.get("url")):
                reject("portal_or_discovery_url_rejected")
            else:
                reject("publisher_authority_rejected")
            continue
        if raw.get("display_relevance_qualified") is not True:
            reject("relevance_rejected")
            continue
        if len(categories) <= 1:
            reject("category_unresolved")
            continue
        if freshness["status"] == "missing":
            reject("missing_published_at")
            continue
        if freshness["status"] == "future_outlier":
            record["final_rejection_reason"] = "other_explicit_reason"
            lose("other_explicit_reason", safe_id)
            other_reasons["published_at_future_outlier"] = (
                other_reasons.get("published_at_future_outlier", 0) + 1
            )
            continue
        display_policy_rows += 1
        record["display_eligibility"] = True
        if freshness["status"] == "outside_backfill":
            lose("stale_outside_primary_window", safe_id)
            reject("stale_outside_backfill_window")
            continue
        if freshness["status"] == "backfill":
            backfill_rows += 1
            lose("stale_outside_primary_window", safe_id)
            record["backfill_status"] = "candidate"
        else:
            primary_rows += 1
        eligible.append({
            "row": dict(raw),
            "categories": set(categories),
            "subfilters": set(_subfilter_labels(raw)),
            "freshness": freshness,
            "publisher_key": _publisher_key(raw),
            "record_id": safe_id,
        })

    canonical_survivors: list[dict] = []
    by_canonical: dict[str, dict] = {}
    for item in sorted(eligible, key=_coverage_sort_key):
        canonical = publisher_direct.publisher_url(item["row"]).casefold().rstrip("/")
        survivor = by_canonical.get(canonical)
        if survivor is None:
            by_canonical[canonical] = item
            canonical_survivors.append(item)
            continue
        survivor["categories"].update(item["categories"])
        survivor["subfilters"].update(item["subfilters"])
        decisions[item["record_id"]]["canonical_cluster"] = decisions[
            survivor["record_id"]
        ]["canonical_cluster"]
        decisions[item["record_id"]]["final_rejection_reason"] = "canonical_duplicate"
        lose("canonical_duplicate", item["record_id"])

    event_survivors: list[dict] = []
    for item in canonical_survivors:
        duplicate = next(
            (
                survivor for survivor in event_survivors
                if _same_material_event(item["row"], survivor["row"])
            ),
            None,
        )
        if duplicate is None:
            event_survivors.append(item)
            event_key = "e_" + hashlib.sha256(
                _normalized_event_title(item["row"].get("title")).encode("utf-8")
            ).hexdigest()[:16]
            decisions[item["record_id"]]["event_cluster"] = event_key
            continue
        duplicate["categories"].update(item["categories"])
        duplicate["subfilters"].update(item["subfilters"])
        event_key = decisions[duplicate["record_id"]]["event_cluster"]
        decisions[item["record_id"]]["event_cluster"] = event_key
        decisions[item["record_id"]]["final_rejection_reason"] = "event_duplicate"
        lose("event_duplicate", item["record_id"])

    primary = [row for row in event_survivors if row["freshness"]["status"] == "primary"]
    backfill = [row for row in event_survivors if row["freshness"]["status"] == "backfill"]
    category_counts = {key: 0 for key in PRIMARY_CATEGORY_IDS}
    for item in primary:
        for category in item["categories"]:
            if category in category_counts:
                category_counts[category] += 1
    admitted_backfill: list[dict] = []
    for item in sorted(backfill, key=_coverage_sort_key):
        deficits = [
            category for category in item["categories"]
            if category in category_counts and category_counts[category] < CATEGORY_TARGET
        ]
        # R4-R4 edition stability: category deficits determine ordering purpose,
        # not destructive eligibility.  Every relevant, unique publisher article
        # inside seven days remains available until the explicit public hard cap.
        # The real timestamp and the existing 7-day backfill label remain intact.
        decisions[item["record_id"]]["backfill_status"] = (
            "category_seed" if deficits else "stability_pool"
        )
        admitted_backfill.append(item)
        for category in item["categories"]:
            if category in category_counts:
                category_counts[category] += 1

    diversity_input = primary + admitted_backfill
    ranked, diversity = _rank_with_preferences(diversity_input)
    cap = max(0, min(PUBLIC_HARD_MAX, int(limit)))
    selected = ranked[:cap]
    for item in ranked[cap:]:
        decisions[item["record_id"]]["final_rejection_reason"] = "hard_cap_truncated"
        lose("hard_cap_truncated", item["record_id"])
    for item in selected:
        decisions[item["record_id"]]["selected"] = True
        decisions[item["record_id"]]["final_rejection_reason"] = "selected"
    selected_publishers = Counter(item["publisher_key"] for item in selected)
    selected_largest = max(selected_publishers.values(), default=0)
    diversity.update({
        "distinct_publishers": len(selected_publishers),
        "largest_publisher_share": round(
            selected_largest / len(selected) if selected else 0.0,
            4,
        ),
        "diversity_relaxed": bool(
            selected
            and selected_largest / len(selected) > PUBLISHER_SHARE_PREFERENCE
        ),
    })

    publisher_eligible = int(
        health.get("publisher_direct_eligible_count")
        or (brief.get("publisher_direct_delivery") or {}).get("eligible_count")
        or len(rows)
    )
    if publisher_eligible != len(rows):
        delta = abs(publisher_eligible - len(rows))
        lose("serialization_loss", count=delta)
        other_reasons["publisher_eligible_display_contract_mismatch"] = delta
    final_rejected = sum(not item["selected"] for item in decisions.values())
    selected_ids = [item["record_id"] for item in selected]
    stage_counts = {
        "raw_candidates": int(health.get("raw_candidate_count") or 0),
        "publisher_resolution_attempted": int(resolution.get("attempted_count") or 0),
        "publisher_resolution_successful": int(resolution.get("resolved_count") or 0),
        "publisher_direct_eligible": publisher_eligible,
        "source_quality_passed": source_quality_rows,
        "display_policy_eligible": display_policy_rows,
        "freshness_primary_eligible": primary_rows,
        "freshness_backfill_eligible": backfill_rows,
        "category_classified": len(eligible),
        "canonical_dedup_survivors": len(canonical_survivors),
        "event_dedup_survivors": len(event_survivors),
        "diversity_selector_survivors": len(ranked),
        "hard_cap_survivors": len(selected),
        "renderer_input_count": len(selected),
        "rendered_card_count": len(selected),
        "public_html_card_count": len(selected),
    }
    audit = {
        "artifact_contract": str(brief.get("artifact_contract") or ""),
        "display_contract": DISPLAY_CONTRACT,
        "artifact_generated_at": str(brief.get("generated_at") or ""),
        "artifact_fingerprint": hashlib.sha256(
            json.dumps(brief, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "same_artifact_all_stages": True,
        "stage_counts": stage_counts,
        "stage_losses": loss_details,
        "stage_loss_reason_counts": {
            key: value["count"] for key, value in loss_details.items()
        },
        "other_explicit_reasons": dict(sorted(other_reasons.items())),
        "article_decisions": list(decisions.values()),
        "selector_input_ids": list(decisions),
        "selector_output_ids": selected_ids,
        "eligible_to_final_reconciliation": {
            "publisher_eligible": publisher_eligible,
            "selector_input_count": len(rows),
            "selected": len(selected),
            "final_rejected": final_rejected,
            "balanced": len(rows) == len(selected) + final_rejected,
        },
        "diversity": diversity,
        "primary_window_hours": FRESH_MAX_HOURS,
        "backfill_window_hours": BACKFILL_MAX_HOURS,
        "category_target": CATEGORY_TARGET,
        "public_hard_cap": PUBLIC_HARD_MAX,
        "resolution_failed_count": resolution_failed,
        "state_contract": str(
            (health.get("verified_state") or {}).get("state_contract") or ""
        ),
        "state_entries_loaded": int(
            (health.get("verified_state") or {}).get("entries_loaded") or 0
        ),
        "state_entries_valid": int(
            (health.get("verified_state") or {}).get("entries_valid") or 0
        ),
        "state_entries_invalid": int(
            (health.get("verified_state") or {}).get("entries_invalid") or 0
        ),
        "state_entries_pruned": int(
            (health.get("verified_state") or {}).get("entries_pruned") or 0
        ),
        "cache_hits": int(resolution.get("cache_hits") or 0),
        "cache_misses": int(resolution.get("cache_misses") or 0),
        "cache_reverification_required": int(
            resolution.get("cache_reverification_required") or 0
        ),
        "current_verified_new": int(health.get("current_verified_new_count") or 0),
        "current_verified_reused": int(
            health.get("current_verified_reused_count") or 0
        ),
        "current_verified_failed": int(resolution.get("failed_count") or 0),
        "carry_forward_candidates": int(
            health.get("carry_forward_candidate_count") or 0
        ),
        "carry_forward_selected": int(
            health.get("carry_forward_selected_count") or 0
        ),
        "carry_forward_expired": int(
            health.get("carry_forward_expired_count") or 0
        ),
        "carry_forward_invalidated": int(
            health.get("carry_forward_invalidated_count") or 0
        ),
        "resolution_queue_size": int(resolution.get("queue_size") or 0),
        "resolution_attempted": int(resolution.get("attempted_count") or 0),
        "resolution_successful": int(resolution.get("resolved_count") or 0),
        "resolution_failed": int(resolution.get("failed_count") or 0),
        "resolution_timeout": int(resolution.get("timeout_count") or 0),
        "resolution_global_deadline_skipped": int(
            (resolution.get("outcomes") or {}).get("skipped_global_deadline") or 0
        ),
        "resolution_item_budget_skipped": int(
            (resolution.get("outcomes") or {}).get("skipped_item_budget") or 0
        ),
        "resolution_per_host_skipped": int(
            (resolution.get("outcomes") or {}).get("skipped_per_host_limit") or 0
        ),
        "per_category_resolution_metrics": dict(
            resolution.get("per_category") or {}
        ),
        "per_source_lane_resolution_metrics": dict(
            resolution.get("per_source_lane") or {}
        ),
        "p50_resolution_latency_seconds": float(
            resolution.get("p50_latency_seconds") or 0
        ),
        "p95_resolution_latency_seconds": float(
            resolution.get("p95_latency_seconds") or 0
        ),
        "final_verified_union": publisher_eligible,
        "public_selected": len(selected),
        "state_hash_before": str(
            (health.get("verified_state") or {}).get("state_hash_before") or ""
        ),
        "state_hash_after": str(
            (health.get("verified_state") or {}).get("state_hash_after") or ""
        ),
    }
    return selected, audit


def _quarantine_diagnostics(health: Mapping) -> list[dict]:
    groups = {
        "budget": ["검증 예산 대기", 0],
        "access": ["발행사 응답·본문 검증 실패", 0],
        "quality": ["출처 품질 제외", 0],
        "unsafe": ["안전하지 않은 URL 차단", 0],
        "internal": ["내부 검증 오류", 0],
        "other": ["기타 원문 검증 실패", 0],
    }
    for raw_reason, raw_count in (
        health.get("quarantine_reason_counts") or {}
    ).items():
        reason = str(raw_reason).casefold()
        count = max(0, int(raw_count or 0))
        if "budget_exhausted" in reason:
            key = "budget"
        elif "source_quality" in reason:
            key = "quality"
        elif "unsafe" in reason or "redirect_rejected" in reason:
            key = "unsafe"
        elif "internal_error" in reason or "pass_failed" in reason:
            key = "internal"
        elif any(token in reason for token in (
            "metadata", "body", "timeout", "dns", "content_type", "canonical"
        )):
            key = "access"
        else:
            key = "other"
        groups[key][1] += count
    return [
        {"key": key, "label": label, "count": count}
        for key, (label, count) in groups.items()
        if count > 0
    ]


def build_model(
    brief: Mapping,
    *,
    edition: date,
    article_limit: int = PUBLIC_HARD_MAX,
    audit_sink: dict | None = None,
) -> dict:
    """Derive a browser-safe, publisher-only model from the shared brief."""
    generated = _parse_datetime(brief.get("generated_at")) or datetime.now(KST)
    ranked, selection_audit = select_display_articles(
        brief,
        limit=article_limit,
        reference=generated,
    )
    if audit_sink is not None:
        audit_sink.clear()
        audit_sink.update(selection_audit)

    articles = []
    article_ids: set[str] = set()
    for index, item in enumerate(ranked):
        row = item["row"]
        published = _parse_datetime(row.get("published_at"))
        verdict, color = _verdict(row)
        url = publisher_direct.publisher_url(row)
        source = str(row.get("display_source") or row.get("source") or "발행처")
        reason = str(
            row.get("why_it_matters")
            or row.get("one_line_reason")
            or row.get("implication")
            or "현대건설 사업 영향과 대응 필요성을 확인합니다."
        )
        article_id = "nc_" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
        if article_id in article_ids:
            raise RuntimeError("duplicate DOM article identity collision")
        article_ids.add(article_id)
        articles.append({
            "id": article_id,
            "title": str(row.get("title") or "").strip(),
            "summary": str(row.get("snippet") or "").strip(),
            "source": source,
            "published_at": published.isoformat() if published else "",
            "published_label": published.strftime("%m-%d %H:%M") if published else "시각 미상",
            "verdict": verdict,
            "verdict_color": color,
            "why": reason,
            "url": url,
            "publisher_direct": True,
            "authority_label": "Publisher Direct",
            "categories": sorted(item["categories"], key=lambda token: tuple(CATEGORY_LABELS).index(token)),
            "subfilters": sorted(item["subfilters"]),
            "initials": _initials(source),
            "tint": ("#0B6B3A", "#1E5F8A", "#8F6A2E", "#455B73", "#68716A")[index % 5],
            "score": round(float(row.get("final_score") or 0), 2),
            "freshness_status": item["freshness"]["status"],
            "freshness_label": item["freshness"]["label"],
            "age_hours": item["freshness"]["age_hours"],
            "is_backfill": item["freshness"]["is_backfill"],
            "publisher_key": item["publisher_key"],
            "current_run_seen": bool(row.get("current_run_seen", True)),
            "carried_forward": bool(row.get("carried_forward", False)),
            "carry_forward_reason": str(row.get("carry_forward_reason") or ""),
            "teams_newness_eligible": bool(
                row.get("teams_newness_eligible", True)
            ),
            "verification_cache_status": str(
                row.get("verification_cache_status") or "network_verified"
            ),
        })

    status = str(brief.get("collection_status") or "FIXTURE_DEMO")
    if status == LIVE_HEALTHY_NO_ELIGIBLE_ARTICLES and articles:
        raise LiveBriefRejected(
            "validated healthy-empty artifact unexpectedly produced articles"
        )

    themes = []
    for theme in (brief.get("theme_rankings") or [])[:5]:
        if isinstance(theme, Mapping):
            themes.append({
                "label": str(theme.get("theme") or "").strip(),
                "count": int(theme.get("count") or 0),
            })

    subfilter_counts: dict[str, int] = {}
    for article in articles:
        for label in article["subfilters"]:
            subfilter_counts[label] = subfilter_counts.get(label, 0) + 1
    subfilters = [
        {
            "id": "sub_" + hashlib.sha256(label.encode("utf-8")).hexdigest()[:10],
            "label": label,
            "count": count,
        }
        for label, count in sorted(
            subfilter_counts.items(),
            key=lambda item: (-item[1], item[0]),
        )[:12]
        if count > 0
    ]
    subfilter_id = {item["label"]: item["id"] for item in subfilters}
    for article in articles:
        article["subfilter_ids"] = [
            subfilter_id[label]
            for label in article.pop("subfilters")
            if label in subfilter_id
        ]
        article["image_src"] = _fallback_image_data(article)
        article["image_status"] = "deterministic_fallback"

    health = brief.get("collector_health") or {}
    live_mode = brief.get("news_data_mode") == "live"
    query_coverage = health.get("category_query_coverage") or {}
    categories = [
        {
            "id": key,
            "label": label,
            "count": sum(key in row["categories"] for row in articles),
            "query_attempted_count": int(
                (query_coverage.get(key) or {}).get("attempted_count") or 0
            ),
            "query_successful_count": int(
                (query_coverage.get(key) or {}).get("successful_count") or 0
            ),
            "query_added_count": int(
                (query_coverage.get(key) or {}).get("added_count") or 0
            ),
        }
        for key, label in CATEGORY_LABELS.items()
    ]
    for item in categories:
        item["coverage_status"] = (
            "covered" if item["id"] == "all" or item["count"] > 0 else "gap"
        )
    covered_categories = sum(
        item["count"] > 0 for item in categories if item["id"] != "all"
    )
    resolution = health.get("publisher_resolution") or {}
    verified_state = health.get("verified_state") or {}
    quarantine_diagnostics = _quarantine_diagnostics(health)
    verified_supply_count = int(
        selection_audit["stage_counts"]["publisher_direct_eligible"]
    )
    coverage = {
        "category_target_count": len(PRIMARY_CATEGORY_IDS),
        "category_covered_count": covered_categories,
        "category_gap_count": len(PRIMARY_CATEGORY_IDS) - covered_categories,
        "publisher_count": len({row["publisher_key"] for row in articles}),
        "displayed_article_count": len(articles),
        "display_eligible_count": int(
            selection_audit["stage_counts"]["display_policy_eligible"]
        ),
        "primary_window_count": sum(
            row["freshness_status"] == "primary" for row in articles
        ),
        "fresh_article_count": sum(
            row["freshness_status"] == "primary" for row in articles
        ),
        "backfill_article_count": sum(row["is_backfill"] for row in articles),
        "verified_supply_count": verified_supply_count,
        "current_verified_count": int(health.get("current_verified_count") or 0),
        "current_verified_new_count": int(
            health.get("current_verified_new_count") or 0
        ),
        "current_verified_reused_count": int(
            health.get("current_verified_reused_count") or 0
        ),
        "carried_verified_count": int(
            health.get("carry_forward_selected_count") or 0
        ),
        "cache_reuse_count": int(resolution.get("cache_hits") or 0),
        "state_contract": str(verified_state.get("state_contract") or ""),
        "state_entry_count": int(verified_state.get("entries_after") or 0),
        "verified_supply_shortage": verified_supply_count < PUBLIC_TARGET_MIN,
        "verified_supply_shortage_count": max(
            0, PUBLIC_TARGET_MIN - verified_supply_count
        ),
        "largest_publisher_share": selection_audit["diversity"]["largest_publisher_share"],
        "diversity_relaxed": selection_audit["diversity"]["diversity_relaxed"],
        "resolution_attempted_count": int(resolution.get("attempted_count") or 0),
        "resolution_resolved_count": int(resolution.get("resolved_count") or 0),
        "resolution_budget_exhausted_count": int(
            resolution.get("budget_exhausted_count") or 0
        ),
        "resolution_global_deadline_skipped": int(
            (resolution.get("outcomes") or {}).get("skipped_global_deadline") or 0
        ),
        "resolution_item_budget_skipped": int(
            (resolution.get("outcomes") or {}).get("skipped_item_budget") or 0
        ),
        "resolution_per_host_skipped": int(
            (resolution.get("outcomes") or {}).get("skipped_per_host_limit") or 0
        ),
        "quarantine_count": int(health.get("quarantine_count") or 0),
        "quarantine_diagnostics": quarantine_diagnostics,
        "display_policy": "publisher_direct_relevance+72h_primary+7d_category_backfill",
        "teams_policy": "ai_topic+executive_relevance+importance+sender_gate",
    }
    accounting = {
        "public_count_definition": (
            "unique selected article IDs visible in the 홈 edition; lead counted once; "
            "market/weather/safety/non-article cards excluded"
        ),
        "selector_input_count": len(list(_candidate_rows(brief))),
        "selector_output_count": len(articles),
        "renderer_input_count": len(articles),
        "lead_count": 1 if articles else 0,
        "grid_count": max(0, len(articles) - 1),
        "total_unique_visible_article_count": len(articles),
        "dom_article_card_count": len(articles),
        "distinct_dom_article_ids": len(article_ids),
        "category_filter_counts": {
            item["id"]: item["count"] for item in categories
        },
    }
    return {
        "contract": CONTRACT,
        "reference_sha256": REFERENCE_SHA256,
        "edition": edition.isoformat(),
        "edition_label": edition.strftime("%Y.%m.%d"),
        "generated_at": generated.isoformat(),
        "generated_label": generated.strftime("%Y-%m-%d %H:%M KST"),
        "news_data_mode": str(brief.get("news_data_mode") or "mock"),
        "collection_status": status,
        "artifact_fingerprint": selection_audit["artifact_fingerprint"],
        "source_label": "LIVE · publisher-direct" if live_mode else "DEMO · deterministic fixture",
        "article_count": len(articles),
        "rejected_count": max(0, accounting["selector_input_count"] - len(articles)),
        "published_quarantine_count": 0,
        "collector_quarantine_count": int(health.get("quarantine_count") or 0),
        "collector_request_count": int(health.get("request_count") or 0),
        "collector_source_count": int(health.get("source_count") or 0),
        "collector_successful_source_count": int(
            health.get("successful_source_count") or 0
        ),
        "raw_candidate_count": int(health.get("raw_candidate_count") or 0),
        "publisher_direct_eligible_count": int(
            health.get("publisher_direct_eligible_count") or len(articles)
        ),
        "portal_url_count": 0,
        "empty_state": "" if articles else "현재 조건을 충족한 신규 기사가 없습니다",
        "articles": articles,
        "themes": themes,
        "subfilters": subfilters,
        "market": _market_pane(brief),
        "weather": _weather_rail(brief),
        "safety": _safety_rail(brief),
        "categories": categories,
        "coverage": coverage,
        "accounting": accounting,
        "selector_stage_counts": dict(selection_audit["stage_counts"]),
    }


def _json_island(value: object) -> str:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _article_card(article: Mapping, *, lead: bool = False) -> str:
    kind = "lead" if lead else "news-card"
    categories = " ".join(escape(token) for token in article["categories"])
    subfilters = " ".join(escape(token) for token in article.get("subfilter_ids") or [])
    title_tag = "h2" if lead else "h3"
    summary = ""
    if lead and article.get("summary"):
        summary = f'<p class="summary">{escape(str(article["summary"]))}</p>'
    return (
        f'<article id="article-{escape(str(article["id"]))}" class="{kind}" '
        f'data-news-censor-role="selected-article" '
        f'data-article-id="{escape(str(article["id"]))}" '
        f'data-categories="{categories}" data-subfilters="{subfilters}" '
        f'tabindex="0" role="button" '
        f'aria-label="기사 읽기: {escape(str(article["title"]))}">'
        f'<span class="thumb" style="--tint:{escape(str(article["tint"]))}" aria-hidden="true">'
        f'<img src="{escape(str(article["image_src"]), quote=True)}" alt="" loading="lazy"></span>'
        '<div class="card-body">'
        f'<span class="verdict" style="--verdict:{escape(str(article["verdict_color"]))}">'
        f'{escape(str(article["verdict"]))}</span>'
        f'<{title_tag}>{escape(str(article["title"]))}</{title_tag}>'
        f'{summary}'
        f'<p class="why"><b>Why</b> {escape(str(article["why"]))}</p>'
        f'<p class="source">{escape(str(article["source"]))} · '
        f'{escape(str(article["published_label"]))} · '
        f'<span class="freshness {escape(str(article["freshness_status"]))}">'
        f'{escape(str(article["freshness_label"]))}</span> · Publisher Direct</p>'
        '</div></article>'
    )


def render_html(model: Mapping) -> str:
    template = TEMPLATE.read_text(encoding="utf-8")
    logo_uri = "data:image/svg+xml;base64," + base64.b64encode(LOGO.read_bytes()).decode("ascii")
    articles = model["articles"]
    lead = _article_card(articles[0], lead=True) if articles else ""
    cards = "\n".join(_article_card(row) for row in articles[1:]) if articles else ""
    filters = "".join(
        f'<button class="filter{(" active" if item["id"] == "all" else "")}'
        f'{(" coverage-gap" if item.get("coverage_status") == "gap" else "")}" '
        f'type="button" data-filter="{escape(item["id"])}" aria-pressed="'
        f'{str(item["id"] == "all").lower()}" '
        f'data-coverage-status="{escape(str(item.get("coverage_status") or "covered"))}">'
        f'{escape(item["label"])}<small>{item["count"]}'
        f'{" · 미관측" if item.get("coverage_status") == "gap" else ""}</small></button>'
        for item in model["categories"]
    )
    themes = "".join(
        f'<li><span>{escape(item["label"])}</span><b>{item["count"]}</b></li>'
        for item in model["themes"]
    ) or '<li><span>관측 테마 없음</span><b>0</b></li>'
    subfilters = "".join(
        f'<button class="subfilter" type="button" data-subfilter="{escape(item["id"])}" '
        f'aria-pressed="false">{escape(item["label"])}<small>{item["count"]}</small></button>'
        for item in model.get("subfilters") or []
        if int(item.get("count") or 0) > 0
    )
    market_rows = "".join(
        '<li>'
        f'<span>{escape(str(item["label"]))}</span>'
        f'<b>{"N/A" if item.get("value") is None else escape(str(item["value"])) + (" " + escape(str(item.get("unit") or "")) if item.get("unit") else "")}</b>'
        f'<small>{escape(str(item.get("data_mode") or "unavailable"))}'
        f'{" · stale" if item.get("is_stale") else ""}'
        f'{" · proxy" if item.get("proxy_for") else ""}</small>'
        '</li>'
        for item in model["market"]["items"]
    ) or '<li class="unavailable">N/A · 시장지표 미연동</li>'
    weather_rows = "".join(
        '<li>'
        f'<span>{escape(str(item["region"]))}<small>{escape(str(item["basis"]))}</small></span>'
        f'<b>{escape(str(item["grade"]))}</b>'
        f'<small>{escape(str(item["forecast_at"] or item["status_note"] or "예보시각 미수신"))}</small>'
        '</li>'
        for item in model["weather"]["rows"]
    ) or (
        '<li class="unavailable">'
        + escape(str(model["weather"]["unavailable_reason"]))
        + '</li>'
    )
    safety_rows = "".join(
        '<li>'
        f'<span>{escape(str(item["title"]))}</span>'
        f'<b>{escape(str(item["severity"]))}</b>'
        f'<small>검증 기사 {int(item["article_count"])}건 · 출처 {int(item["source_count"])}곳</small>'
        '</li>'
        for item in model["safety"]["items"]
    ) or (
        '<li class="unavailable">'
        + escape(str(model["safety"]["unavailable_reason"]))
        + '</li>'
    )
    coverage = model["coverage"]
    category_coverage_rows = "".join(
        '<li>'
        f'<span>{escape(str(item["label"]))}</span>'
        f'<b class="coverage-{escape(str(item["coverage_status"]))}">'
        f'{int(item["count"])}건</b>'
        f'<small>{"관측" if item["coverage_status"] == "covered" else "이번 판 미관측"}'
        f' · 검색 성공 {int(item["query_successful_count"])}/'
        f'{int(item["query_attempted_count"])}</small>'
        '</li>'
        for item in model["categories"]
        if item["id"] != "all"
    )
    quarantine_rows = "".join(
        '<li>'
        f'<span>{escape(str(item["label"]))}</span>'
        f'<b>{int(item["count"])}</b>'
        '</li>'
        for item in coverage["quarantine_diagnostics"]
    ) or '<li class="unavailable">격리 진단 0건</li>'
    mode_class = "live" if model["news_data_mode"] == "live" else "demo"
    warning = (
        "실시간 수집 · 게시자 원문 검증 완료"
        if mode_class == "live"
        else "검증용 데모 기사 · 현재 뉴스가 아님"
    )
    replacements = {
        "{{CONTRACT}}": CONTRACT,
        "{{PAGE_TITLE}}": escape(f'HDEC News Censor · {model["edition_label"]}'),
        "{{LOGO_DATA_URI}}": logo_uri,
        "{{EDITION_LABEL}}": escape(str(model["edition_label"])),
        "{{GENERATED_LABEL}}": escape(str(model["generated_label"])),
        "{{MODE_CLASS}}": mode_class,
        "{{SOURCE_LABEL}}": escape(str(model["source_label"])),
        "{{MODE_WARNING}}": escape(warning),
        "{{COVERAGE_SUMMARY}}": escape(
            f'검증 표시 {model["article_count"]}건 · '
            f'기본 {coverage["primary_window_count"]} · '
            f'보강 {coverage["backfill_article_count"]} · '
            f'카테고리 {coverage["category_covered_count"]}/'
            f'{coverage["category_target_count"]}'
        ),
        "{{ARTICLE_COUNT}}": str(model["article_count"]),
        "{{COVERED_CATEGORY_COUNT}}": str(coverage["category_covered_count"]),
        "{{CATEGORY_TARGET_COUNT}}": str(coverage["category_target_count"]),
        "{{PUBLISHER_COUNT}}": str(coverage["publisher_count"]),
        "{{DISPLAY_ELIGIBLE_COUNT}}": str(coverage["display_eligible_count"]),
        "{{CURRENT_VERIFIED_COUNT}}": str(coverage["current_verified_count"]),
        "{{CARRIED_VERIFIED_COUNT}}": str(coverage["carried_verified_count"]),
        "{{CACHE_REUSE_COUNT}}": str(coverage["cache_reuse_count"]),
        "{{PRIMARY_WINDOW_COUNT}}": str(coverage["primary_window_count"]),
        "{{FRESH_ARTICLE_COUNT}}": str(coverage["fresh_article_count"]),
        "{{BACKFILL_ARTICLE_COUNT}}": str(coverage["backfill_article_count"]),
        "{{SUPPLY_SHORTAGE}}": (
            f'검증 공급 {coverage["verified_supply_count"]}건 · '
            f'최소 기준 대비 부족 {coverage["verified_supply_shortage_count"]}건'
            if coverage["verified_supply_shortage"] else "충분"
        ),
        "{{RESOLUTION_ATTEMPTED_COUNT}}": str(coverage["resolution_attempted_count"]),
        "{{RESOLUTION_RESOLVED_COUNT}}": str(coverage["resolution_resolved_count"]),
        "{{RESOLUTION_BUDGET_COUNT}}": str(coverage["resolution_budget_exhausted_count"]),
        "{{QUARANTINE_COUNT}}": str(coverage["quarantine_count"]),
        "{{CATEGORY_COVERAGE_ROWS}}": category_coverage_rows,
        "{{QUARANTINE_DIAGNOSTIC_ROWS}}": quarantine_rows,
        "{{FILTER_BUTTONS}}": filters,
        "{{SUBFILTER_BUTTONS}}": subfilters,
        "{{SUBFILTER_HIDDEN}}": "" if subfilters else " hidden",
        "{{LEAD_ARTICLE}}": lead,
        "{{ARTICLE_CARDS}}": cards,
        "{{PRODUCTION_EMPTY_HIDDEN}}": "" if model.get("empty_state") else " hidden",
        "{{PRODUCTION_EMPTY_TEXT}}": escape(str(model.get("empty_state") or "")),
        "{{THEME_ROWS}}": themes,
        "{{MARKET_ROWS}}": market_rows,
        "{{MARKET_SOURCE}}": escape(str(model["market"]["source"])),
        "{{MARKET_AS_OF}}": escape(str(model["market"]["as_of"] or "기준시각 미수신")),
        "{{WEATHER_ROWS}}": weather_rows,
        "{{WEATHER_SOURCE}}": escape(str(model["weather"]["source"] or "미연동")),
        "{{WEATHER_UPDATED_AT}}": escape(str(model["weather"]["updated_at"] or "수집시각 미수신")),
        "{{SAFETY_ROWS}}": safety_rows,
        "{{SAFETY_SOURCE}}": escape(str(model["safety"]["source"] or "미연동")),
        "{{SAFETY_AS_OF}}": escape(str(model["safety"]["as_of"] or "기준시각 미수신")),
        "{{MODEL_JSON}}": _json_island(model),
        "{{REFERENCE_SHA256}}": REFERENCE_SHA256,
    }
    for marker, value in replacements.items():
        template = template.replace(marker, value)
    unresolved = re.findall(r"\{\{[A-Z0-9_]+\}\}", template)
    if unresolved:
        raise RuntimeError(f"unresolved template markers: {unresolved}")
    return template


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="wb",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def materialize_article_images(
    model: dict,
    *,
    output_root: Path,
    image_limit: int = 8,
) -> dict:
    """Use the existing bounded image resolver/quality gate, then copy only local bytes."""
    from app import editorial_briefings

    articles = model.get("articles") or []
    if not articles or image_limit <= 0:
        return {"attempted": 0, "materialized": 0, "fallback": len(articles)}

    candidates: list[editorial_briefings.EditorialArticle] = []
    selected_rows = articles[: min(len(articles), image_limit)]
    for article in selected_rows:
        published = _parse_datetime(article.get("published_at")) or datetime.now(KST)
        image_input = {
            "selected_url": article["url"],
            "url": article["url"],
            "title": article["title"],
            "source": article["source"],
            "published_at": article.get("published_at"),
        }
        resolution = editorial_briefings.resolve_article_image(
            image_input,
            allow_network=True,
        )
        candidates.append(editorial_briefings.EditorialArticle(
            title=str(article["title"]),
            summary=str(article.get("summary") or ""),
            source=str(article.get("source") or "발행처"),
            published_at=published,
            selected_url=str(article["url"]),
            link_kind="publisher_direct",
            link_label="Publisher Direct",
            category="News Censor",
            publisher_article_url=str(article["url"]),
            publisher_url_source_kind="validated_brief_artifact",
            publisher_url_reason="shared_live_brief_publisher_authority",
            image_url=resolution.url,
            image_remote_url=resolution.url,
            image_source_kind=resolution.source_kind,
            image_source_page_url=resolution.source_page_url,
            image_width=resolution.width,
            image_height=resolution.height,
            image_fallback_used=resolution.fallback_used,
            image_reason=resolution.reason,
            image_candidates=resolution.candidates,
        ))

    with tempfile.TemporaryDirectory(
        prefix="hdec-news-censor-images-",
        dir="/tmp",
    ) as stage_name:
        stage = Path(stage_name)
        materialized, counters = editorial_briefings.materialize_preview_images(
            candidates,
            stage,
            html_dir=stage,
        )
        materialized_count = 0
        for model_article, image_article in zip(selected_rows, materialized):
            asset = str(image_article.image_local_asset or "")
            source = stage / "assets" / "images" / asset
            if (
                image_article.image_quality_accepted
                and asset
                and source.is_file()
            ):
                destination = output_root / "assets" / "images" / asset
                _atomic_write_bytes(destination, source.read_bytes())
                model_article["image_src"] = f"assets/images/{asset}"
                model_article["image_status"] = "local_materialized"
                materialized_count += 1
        model["image_materialization"] = {
            "attempted": len(selected_rows),
            "materialized": materialized_count,
            "fallback": len(articles) - materialized_count,
            "quality_rejections": int(counters.image_quality_rejections),
        }
        return dict(model["image_materialization"])


def build(
    brief: Mapping,
    *,
    edition: date,
    article_limit: int = PUBLIC_HARD_MAX,
) -> tuple[dict, str]:
    model = build_model(brief, edition=edition, article_limit=article_limit)
    return model, render_html(model)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the standalone HDEC News Censor page")
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--output-root", type=Path, help="write latest.html and a dated archive")
    output.add_argument("--json", action="store_true", help="print build metadata without writing")
    output.add_argument("--dry-run", action="store_true", help="print a build summary without writing")
    parser.add_argument(
        "--brief-json",
        type=Path,
        required=True,
        help="explicit HDEC_VALIDATED_EXECUTIVE_BRIEF_V1 input artifact",
    )
    parser.add_argument("--edition-date", default="", help="archive date (YYYY-MM-DD; default KST today)")
    parser.add_argument("--article-limit", type=int, default=PUBLIC_HARD_MAX)
    parser.add_argument("--require-live", action="store_true", help="fail closed unless collection mode is live")
    parser.add_argument(
        "--image-mode",
        choices=("off", "live"),
        default="off",
        help="off=deterministic local-data fallback; live=bounded local image materialization",
    )
    parser.add_argument("--image-limit", type=int, default=8)
    args = parser.parse_args(argv)

    edition = _edition_date(args.edition_date)
    try:
        brief = load_brief_json(args.brief_json.resolve())
        validate_brief_artifact(brief, require_live=args.require_live)
        model = build_model(brief, edition=edition, article_limit=args.article_limit)
        if args.image_mode == "live":
            if not args.output_root:
                raise ValueError("--image-mode live requires --output-root")
            materialize_article_images(
                model,
                output_root=args.output_root.resolve(),
                image_limit=max(0, min(24, args.image_limit)),
            )
        html = render_html(model)
    except LiveBriefRejected as exc:
        print(f"ERROR: News Censor live artifact rejected: {exc}", file=sys.stderr)
        return 3
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: News Censor build failed closed: {exc}", file=sys.stderr)
        return 2

    metadata = {
        "contract": CONTRACT,
        "news_data_mode": model["news_data_mode"],
        "edition": model["edition"],
        "article_count": model["article_count"],
        "public_displayed_count": model["accounting"]["total_unique_visible_article_count"],
        "selector_input_count": model["accounting"]["selector_input_count"],
        "selector_output_count": model["accounting"]["selector_output_count"],
        "renderer_input_count": model["accounting"]["renderer_input_count"],
        "primary_window_count": model["coverage"]["primary_window_count"],
        "backfill_count": model["coverage"]["backfill_article_count"],
        "category_counts": model["accounting"]["category_filter_counts"],
        "artifact_fingerprint": model["artifact_fingerprint"],
        "rejected_count": model["rejected_count"],
        "collection_status": model["collection_status"],
        "raw_candidate_count": model["raw_candidate_count"],
        "collector_request_count": model["collector_request_count"],
        "collector_source_count": model["collector_source_count"],
        "collector_successful_source_count": model["collector_successful_source_count"],
        "collector_quarantine_count": model["collector_quarantine_count"],
        "published_quarantine_count": model["published_quarantine_count"],
        "publisher_direct_count": sum(row["publisher_direct"] for row in model["articles"]),
        "portal_url_count": publisher_direct.count_portal_urls(model["articles"]),
        "market_status": model["market"]["status"],
        "weather_status": model["weather"]["status"],
        "safety_status": model["safety"]["status"],
        "image_materialization": model.get("image_materialization") or {
            "attempted": 0,
            "materialized": 0,
            "fallback": model["article_count"],
        },
        "subfilter_count": len(model["subfilters"]),
        "html_chars": len(html),
        "reference_sha256": REFERENCE_SHA256,
    }
    if args.json:
        print(json.dumps(metadata, ensure_ascii=False, indent=2))
        return 0
    if args.output_root:
        root = args.output_root.resolve()
        latest = root / "latest.html"
        archive = root / f"{model['edition']}.html"
        _atomic_write(archive, html)
        _atomic_write(latest, html)
        print(
            f"news censor written: {latest} + {archive} ({len(html)} chars) "
            f"news_data_mode={model['news_data_mode']} articles={model['article_count']}"
        )
        return 0

    print(
        f"{CONTRACT} edition={model['edition']} mode={model['news_data_mode']} "
        f"articles={model['article_count']} portal_urls=0 html_chars={len(html)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
