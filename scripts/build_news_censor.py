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
RECENT_MAX_HOURS = 7 * 24
BACKFILL_MAX_HOURS = 30 * 24
PUBLISHER_DIVERSITY_SOFT_CAP = 3

SURFACE_CATEGORIES = (
    ("news_censor_display_articles", ("biz",)),
    ("top_immediate_signals", ("all",)),
    ("top_new_issues", ("all",)),
    ("hdec_direct_signals", ("hdec",)),
    ("business_signals", ("biz",)),
    ("competitor_contractor_signals", ("peers",)),
    ("competitor_supply_signals", ("peers",)),
    ("risk_regulation_signals", ("safety",)),
    ("ai_radar_signals", ("ai",)),
    ("ai_value_chain_pool", ("ai",)),
    ("macro_economy_signals", ("biz",)),
    ("hyundai_group_signals", ("hdec",)),
    ("trust_company_signals", ("biz",)),
    ("developer_signals", ("biz",)),
)

SECTION_CATEGORY = {
    "hdec_direct": "hdec",
    "ai": "ai",
    "risk_regulation": "safety",
    "order_overseas": "global",
    "competitor_supply": "peers",
    "macro_economy": "biz",
}

KEYWORD_CATEGORIES = {
    "hdec": ("현대건설", "현대엔지니어링", "현대 그룹", "현대그룹"),
    "peers": ("gs건설", "대우건설", "dl이앤씨", "롯데건설", "포스코이앤씨", "삼성물산"),
    "safety": ("안전", "중대재해", "사고", "품질", "하자", "감독", "규제"),
    "global": ("해외", "글로벌", "중동", "사우디", "네옴", "미국", "유럽", "지정학"),
    "ai": (" ai ", "인공지능", "데이터센터", "data center", "smr", "bim", "로봇", "디지털 트윈"),
}

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


def _category_tokens(row: Mapping, seeds: Iterable[str]) -> set[str]:
    tokens = {"all", *seeds}
    for section in (
        row.get("executive_section"),
        row.get("radar_section"),
        *(row.get("secondary_sections") or []),
    ):
        mapped = SECTION_CATEGORY.get(str(section or ""))
        if mapped:
            tokens.add(mapped)
    text = (
        f" {row.get('title') or ''} {row.get('snippet') or ''} "
        f"{row.get('source') or ''} "
    ).casefold()
    for category, keywords in KEYWORD_CATEGORIES.items():
        if any(keyword in text for keyword in keywords):
            tokens.add(category)
    return tokens


def _candidate_rows(brief: Mapping) -> Iterable[tuple[dict, tuple[str, ...]]]:
    for key, categories in SURFACE_CATEGORIES:
        for item in brief.get(key) or []:
            if isinstance(item, Mapping):
                yield dict(item), categories
    for section in brief.get("category_sections") or []:
        if not isinstance(section, Mapping):
            continue
        for item in section.get("top_articles") or []:
            if isinstance(item, Mapping):
                yield dict(item), ("biz",)
    for section in brief.get("accordion_sections") or []:
        if not isinstance(section, Mapping):
            continue
        category = SECTION_CATEGORY.get(str(section.get("key") or ""), "all")
        for item in section.get("articles") or []:
            if isinstance(item, Mapping):
                yield dict(item), (category,)


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
            "status": "unknown",
            "label": "시각 확인 필요",
            "rank": 3,
            "age_hours": None,
            "is_backfill": True,
        }
    age_hours = max(0.0, (reference - published).total_seconds() / 3600)
    if age_hours <= FRESH_MAX_HOURS:
        status, label, rank = "fresh", "최신 72시간", 0
    elif age_hours <= RECENT_MAX_HOURS:
        status, label, rank = "recent", "최근 7일", 1
    elif age_hours <= BACKFILL_MAX_HOURS:
        status, label, rank = "backfill", "최근 30일 보강", 2
    else:
        status, label, rank = "archive", "30일 초과 보관", 3
    return {
        "status": status,
        "label": label,
        "rank": rank,
        "age_hours": round(age_hours, 1),
        "is_backfill": status not in {"fresh", "recent"},
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
        -float(row.get("final_score") or 0),
        -_published_rank(row.get("published_at")),
        str(row.get("title") or ""),
    )


def _select_coverage_rows(
    candidates: Iterable[dict],
    *,
    limit: int,
    reference: datetime,
) -> list[dict]:
    enriched = []
    for item in candidates:
        value = dict(item)
        value["freshness"] = _freshness_info(item["row"], reference)
        value["publisher_key"] = _publisher_key(item["row"])
        enriched.append(value)
    ranked = sorted(enriched, key=_coverage_sort_key)
    cap = max(0, min(40, int(limit)))
    if cap == 0:
        return []

    selected: list[dict] = []
    selected_urls: set[str] = set()
    publisher_counts: dict[str, int] = {}

    def add(item: dict) -> None:
        canonical = publisher_direct.publisher_url(item["row"]).casefold().rstrip("/")
        if not canonical or canonical in selected_urls or len(selected) >= cap:
            return
        selected.append(item)
        selected_urls.add(canonical)
        publisher = item["publisher_key"]
        publisher_counts[publisher] = publisher_counts.get(publisher, 0) + 1

    # Seed one row for every observable category before ordinary ranking.  A row
    # may satisfy multiple categories; do not add a second merely to tick a box.
    for category in PRIMARY_CATEGORY_IDS:
        if any(category in item["categories"] for item in selected):
            continue
        for item in ranked:
            if category in item["categories"]:
                add(item)
                break

    # Soft publisher cap keeps alternative publishers visible.  Relax it only to
    # fill unused capacity; coverage never fabricates a missing category/source.
    for item in ranked:
        if publisher_counts.get(item["publisher_key"], 0) < PUBLISHER_DIVERSITY_SOFT_CAP:
            add(item)
    for item in ranked:
        add(item)

    selected.sort(key=_coverage_sort_key)
    return selected


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


def build_model(brief: Mapping, *, edition: date, article_limit: int = 24) -> dict:
    """Derive a browser-safe, publisher-only model from the shared brief."""
    merged: dict[str, dict] = {}
    rejected = 0
    for raw, seeds in _candidate_rows(brief):
        assessment = publisher_direct.assess_delivery_eligibility(
            raw,
            relevance_qualified=True,
        )
        if not assessment.eligible:
            rejected += 1
            continue
        canonical = assessment.publisher_url
        key = canonical.casefold().rstrip("/")
        if key in merged:
            merged[key]["categories"].update(_category_tokens(raw, seeds))
            merged[key]["subfilters"].update(_subfilter_labels(raw))
            continue
        row = dict(raw)
        row["url"] = canonical
        merged[key] = {
            "row": row,
            "categories": _category_tokens(row, seeds),
            "subfilters": set(_subfilter_labels(row)),
        }

    generated = _parse_datetime(brief.get("generated_at")) or datetime.now(KST)
    ranked = _select_coverage_rows(
        merged.values(),
        limit=article_limit,
        reference=generated,
    )

    articles = []
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
        article_id = "nc_" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
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
        })

    status = str(brief.get("collection_status") or "FIXTURE_DEMO")
    if status == LIVE_HEALTHY_WITH_ARTICLES and not articles:
        raise LiveBriefRejected(
            "validated live artifact has eligible collection but no publishable surface rows"
        )
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
    quarantine_diagnostics = _quarantine_diagnostics(health)
    coverage = {
        "category_target_count": len(PRIMARY_CATEGORY_IDS),
        "category_covered_count": covered_categories,
        "category_gap_count": len(PRIMARY_CATEGORY_IDS) - covered_categories,
        "publisher_count": len({row["publisher_key"] for row in articles}),
        "fresh_article_count": sum(
            row["freshness_status"] in {"fresh", "recent"} for row in articles
        ),
        "backfill_article_count": sum(row["is_backfill"] for row in articles),
        "resolution_attempted_count": int(resolution.get("attempted_count") or 0),
        "resolution_resolved_count": int(resolution.get("resolved_count") or 0),
        "resolution_budget_exhausted_count": int(
            resolution.get("budget_exhausted_count") or 0
        ),
        "quarantine_count": int(health.get("quarantine_count") or 0),
        "quarantine_diagnostics": quarantine_diagnostics,
        "display_policy": "publisher_direct_coverage",
        "teams_policy": "ai_topic+executive_relevance+importance+sender_gate",
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
        "source_label": "LIVE · publisher-direct" if live_mode else "DEMO · deterministic fixture",
        "article_count": len(articles),
        "rejected_count": rejected,
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
        "empty_state": (
            "현재 조건을 충족한 신규 기사가 없습니다"
            if status == LIVE_HEALTHY_NO_ELIGIBLE_ARTICLES
            else ""
        ),
        "articles": articles,
        "themes": themes,
        "subfilters": subfilters,
        "market": _market_pane(brief),
        "weather": _weather_rail(brief),
        "safety": _safety_rail(brief),
        "categories": categories,
        "coverage": coverage,
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
        f'<article class="{kind}" data-article-id="{escape(str(article["id"]))}" '
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
            f'카테고리 {coverage["category_covered_count"]}/'
            f'{coverage["category_target_count"]} · '
            f'발행사 {coverage["publisher_count"]}곳'
        ),
        "{{ARTICLE_COUNT}}": str(model["article_count"]),
        "{{COVERED_CATEGORY_COUNT}}": str(coverage["category_covered_count"]),
        "{{CATEGORY_TARGET_COUNT}}": str(coverage["category_target_count"]),
        "{{PUBLISHER_COUNT}}": str(coverage["publisher_count"]),
        "{{FRESH_ARTICLE_COUNT}}": str(coverage["fresh_article_count"]),
        "{{BACKFILL_ARTICLE_COUNT}}": str(coverage["backfill_article_count"]),
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
    article_limit: int = 24,
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
    parser.add_argument("--article-limit", type=int, default=24)
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
