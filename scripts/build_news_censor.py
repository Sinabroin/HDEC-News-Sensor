#!/usr/bin/env python3
"""Build the standalone HDEC News Censor static surface.

The page reuses the sealed executive-brief and publisher-direct policies.  It
does not fetch in the browser, send notifications, or add itself to an existing
page's navigation.  Live image mode may update only the explicitly supplied
verified-image association fields after same-origin materialization succeeds.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import sys
import tempfile
import threading
import time as monotonic_time
import unicodedata
from collections import Counter
from difflib import SequenceMatcher
from datetime import date, datetime, timedelta, timezone
from html import escape
from io import BytesIO
from pathlib import Path
from typing import Callable, Iterable, Mapping
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
for item in (ROOT, SCRIPTS):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from app import (  # noqa: E402
    ai_centrality,
    ai_value_chain,
    news_censor_verified_state,
    publisher_direct,
    radar_signals,
    topic_profiles,
)
from build_executive_brief import load_brief_json  # noqa: E402

TEMPLATE = ROOT / "templates" / "news_censor.html"
WEATHER_MAP_TEMPLATE = ROOT / "templates" / "news_censor_kr_map.svg"
DEFAULT_OUTPUT_ROOT = ROOT / "docs" / "news-censor"
DEFAULT_CANONICAL_OUTPUT = ROOT / "docs" / "daily" / "dashboard-latest.html"
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
APPROVED_SUBFILTERS = {
    "all": (("전체", "magazine", False),),
    "biz": (
        ("전체", "all", False),
        ("플랜트", "lens:plant", False),
        ("토목", "lens:civil_infrastructure", False),
        ("건축·주택", "lens:building_housing", False),
        ("시행사", "lens:developers", False),
        ("개발사업", "lens:development_business", False),
    ),
    "peers": (
        ("전체", "all", False),
        ("경쟁 시공사", "lens:competitor_contractors", False),
        ("GS건설", "sub:4e7d40c0", True),
        ("대우건설", "sub:fd3376dd", True),
        ("롯데건설", "sub:e8a249a3", True),
    ),
    "hdec": (
        ("전체", "all", False),
        ("현대 그룹사", "lens:hyundai_group", False),
        ("현대엔지니어링", "sub:d914e406", True),
        ("국내현장", "lens:domestic_site", False),
    ),
    "safety": (
        ("전체", "all", False),
        ("안전·품질", "lens:safety_quality", False),
    ),
    "global": (
        ("전체", "all", False),
        ("해외수주", "lens:global_business", False),
    ),
    "ai": (
        ("전체", "all", False),
        ("AI", "lens:ai", False),
        ("신재생·전력", "lens:new_energy", False),
    ),
}
COMPANY_SUBFILTERS = {
    "GS건설": "sub:4e7d40c0",
    "대우건설": "sub:fd3376dd",
    "롯데건설": "sub:e8a249a3",
    "현대엔지니어링": "sub:d914e406",
}
FRESH_MAX_HOURS = 72
BACKFILL_MAX_HOURS = 7 * 24
CATEGORY_TARGET = 3
PUBLIC_HARD_MAX = 40
IMAGE_GLOBAL_CONCURRENCY = 4
IMAGE_PER_HOST_CONCURRENCY = 1
IMAGE_TOTAL_DEADLINE_SECONDS = 180
IMAGE_PUBLIC_SRC_PREFIX = "/HDEC-News-Sensor/news-censor/assets/images/"
IMAGE_FALLBACK_REASONS = frozenset({
    "no_image_candidate",
    "publisher_blocked",
    "timeout",
    "invalid_mime",
    "invalid_magic",
    "dimensions_too_small",
    "logo_or_banner_rejected",
    "duplicate_image_rejected",
    "unsafe_url_rejected",
    "download_failed",
    "materialization_failed",
    "total_deadline_exhausted_after_attempt",
})
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


def _semantic_text(value: object) -> str:
    return re.sub(
        r"\s+",
        " ",
        unicodedata.normalize("NFKC", str(value or "")).casefold(),
    ).strip()


def _first_semantic_hit(text: str, terms: Iterable[str]) -> str:
    return next(
        (term for term in terms if _semantic_text(term) in text),
        "",
    )


def _strict_business_lens_reason(
    lens_id: str,
    article: Mapping,
    base_reason: str,
) -> str | None:
    """Reject weak substring and metaphor matches before public lens tagging.

    The shared topic-profile classifier is intentionally broad enough for
    collection.  A visible dashboard filter is a stronger claim: the selected
    lens must be a material subject of the article.  This second-stage guard is
    local to News Censor and never changes collection or Teams policy.
    """
    title = _semantic_text(article.get("title"))
    snippet = _semantic_text(article.get("snippet"))
    text = f"{title} {snippet}".strip()
    direct_terms = {
        "civil_infrastructure": (
            "토목", "soc", "도로", "철도", "gtx", "항만", "공항", "교량",
            "터널", "지하공간", "수자원", "댐", "하천", "공공공사",
        ),
        "building_housing": (
            "국내건축", "주택", "아파트", "힐스테이트", "디에이치", "도시정비",
            "재건축", "재개발", "리모델링", "분양", "미분양", "공사비",
        ),
        "plant": (
            "플랜트", "원전", "lng", "epc", "발전소", "정유", "석유화학",
            "산업설비", "수처리",
        ),
        "new_energy": (
            "smr", "수소", "재생에너지", "해상풍력", "풍력", "태양광", "ess",
            "전력망", "송전", "배전", "데이터센터 전력", "데이터센터 냉각",
            "전력 인프라", "전력인프라", "냉각", "에너지 인프라", "탄소중립",
            "ccus",
        ),
        "development_business": (
            "개발사업", "도시개발사업", "복합개발", "브릿지론", "본pf", "pf사업",
            "pf 사업", "토지확보", "토지매입", "시공사 선정",
        ),
    }

    if lens_id in direct_terms:
        hit = _first_semantic_hit(text, direct_terms[lens_id])
        return f"material {lens_id} subject: {hit}; {base_reason}" if hit else None

    if lens_id == "safety_quality":
        strong = _first_semantic_hit(text, (
            "중대재해", "특별감독", "산업안전", "철근누락", "철근 누락",
            "부실시공", "사망사고", "안전사고", "산재", "품질 결함",
            "품질결함", "하자", "안전점검", "안전 점검", "안전대책",
            "안전 대책", "현장점검", "행정처분", "벌점", "d·e등급",
        ))
        if strong:
            return f"material construction safety/quality subject: {strong}; {base_reason}"
        if "붕괴" in text:
            financial_metaphor = _first_semantic_hit(title, (
                "레버리지", "펀드", "월가", "증시", "주가", "채권", "몰락",
                "수익률", "가상자산", "코인",
            ))
            physical_context = _first_semantic_hit(text, (
                "붕괴사고", "건물 붕괴", "교량 붕괴", "터널 붕괴", "구조물 붕괴",
                "현장 붕괴", "공사장 붕괴", "해체공사", "시설물",
            ))
            if physical_context and not financial_metaphor:
                return f"physical collapse evidence: {physical_context}; {base_reason}"
        return None

    if lens_id == "global_business":
        geography = _first_semantic_hit(text, (
            "해외수주", "해외사업", "해외현장", "해외지사", "해외법인", "중동",
            "사우디", "카타르", "uae", "이라크", "호르무즈", "지정학", "북미",
            "미국", "유럽", "아시아", "글로벌",
        ))
        project = _first_semantic_hit(text, (
            "수주", "발주", "계약", "프로젝트", "건설", "epc", "투자", "공급망",
            "규제", "인프라", "데이터센터", "플랜트", "현장", "생산",
        ))
        if geography and project:
            return f"foreign project/exposure: {geography} + {project}; {base_reason}"
        return None

    return base_reason


def _material_ai_reason(article: Mapping) -> str | None:
    """Return explicit title-level evidence that AI is a material subject."""
    title = _semantic_text(article.get("title"))
    if not title:
        return None
    hit = _first_semantic_hit(title, (
        " ai ", "ai·", "ai-", "ai가", "ai는", "ai를", "ai의", "ai와", "ai 투자",
        "인공지능", "생성형 ai", "피지컬 ai", "데이터센터", "데이터 센터",
        "스마트건설", "스마트 건설", "bim", "디지털 트윈", "건설로봇", "로봇",
        "ai 팩토리",
    ))
    # Padding makes a bare leading/trailing ASCII AI token deterministic without
    # treating an arbitrary substring inside another word as evidence.
    padded = f" {title} "
    if not hit and re.search(r"(?<![0-9a-z])ai(?![0-9a-z])", padded):
        hit = "AI"
    return f"material title subject: {hit}" if hit else None


def _semantic_filter_contract(row: Mapping) -> dict:
    """Derive every public filter token from explicit article-level evidence."""
    article = {
        "title": str(row.get("title") or ""),
        "snippet": str(row.get("snippet") or ""),
        "source": str(row.get("source") or row.get("display_source") or ""),
    }
    categories = {"all", "biz"}
    lens_tokens: set[str] = set()
    evidence: dict[str, list[str]] = {
        "all": ["canonical verified display article"],
        "biz": [
            "display relevance: "
            + str(row.get("display_relevance_reason") or "qualified")
        ],
    }

    def add(token: str, reason: str) -> None:
        evidence.setdefault(token, [])
        if reason not in evidence[token]:
            evidence[token].append(reason)

    business_lenses = set(topic_profiles.classify_business_lenses(article))
    for lens_id in sorted(business_lenses):
        profile = topic_profiles.get_business_lens(lens_id)
        reason = (
            topic_profiles.business_lens_reason(article, profile)
            if profile is not None
            else None
        )
        if reason:
            reason = _strict_business_lens_reason(lens_id, article, reason)
        if reason:
            token = f"lens:{lens_id}"
            lens_tokens.add(token)
            add(token, reason)

    # The collection profiles intentionally omit some country names and exact
    # safety phrases to keep query fan-out bounded.  These two visible filters
    # may be established independently when the stricter material-subject guard
    # itself has complete evidence.
    for lens_id in ("global_business", "safety_quality"):
        token = f"lens:{lens_id}"
        if token in lens_tokens:
            continue
        reason = _strict_business_lens_reason(
            lens_id,
            article,
            "strict dashboard material-subject evidence",
        )
        if reason:
            lens_tokens.add(token)
            add(token, reason)

    profile_reasons: dict[str, str] = {}
    for profile_id in (
        "hdec_direct",
        "hyundai_group",
        "competitor_contractors",
        "developers",
    ):
        profile = topic_profiles.get_topic_profile(profile_id)
        reason = (
            topic_profiles.topic_profile_reason(article, profile)
            if profile is not None
            else None
        )
        if reason:
            profile_reasons[profile_id] = reason

    if "competitor_contractors" in profile_reasons:
        categories.add("peers")
        lens_tokens.add("lens:competitor_contractors")
        add("peers", profile_reasons["competitor_contractors"])
        add("lens:competitor_contractors", profile_reasons["competitor_contractors"])
    if "hdec_direct" in profile_reasons or "hyundai_group" in profile_reasons:
        categories.add("hdec")
        for reason in (
            profile_reasons.get("hdec_direct"),
            profile_reasons.get("hyundai_group"),
        ):
            if reason:
                add("hdec", reason)
    if "hyundai_group" in profile_reasons:
        lens_tokens.add("lens:hyundai_group")
        add("lens:hyundai_group", profile_reasons["hyundai_group"])
    if "developers" in profile_reasons:
        lens_tokens.add("lens:developers")
        add("lens:developers", profile_reasons["developers"])

    if "lens:safety_quality" in lens_tokens:
        categories.add("safety")
        add("safety", evidence["lens:safety_quality"][0])
    if "lens:global_business" in lens_tokens:
        categories.add("global")
        add("global", evidence["lens:global_business"][0])

    value_chain = ai_value_chain.classify_ai_value_chain(
        article["title"], article["source"], article["snippet"]
    )
    radar = radar_signals.classify_ai_radar(article, section=True)
    ai_material = _material_ai_reason(article)
    # R4-R6 §2 — the dashboard AI subcategory reuses the canonical
    # AI-centrality decision as a conjunctive gate: a stock/political/
    # incidental-AI article never earns the AI filter even when a legacy
    # radar/value-chain signal fires.
    centrality = ai_centrality.classify(article)
    if ai_material and centrality.is_central and (
        radar.get("eligible") or ai_value_chain.is_executive_ai_candidate(value_chain)
    ):
        categories.add("ai")
        lens_tokens.add("lens:ai")
        ai_reasons = [ai_material, f"AI centrality: {centrality.level}"]
        if radar.get("eligible"):
            ai_reasons.append(
                "AI radar: "
                + ", ".join(
                    str(value)
                    for value in (
                        *(radar.get("qualifying_infra") or []),
                        *(radar.get("qualifying_anchors") or []),
                    )
                )
            )
        if ai_value_chain.is_executive_ai_candidate(value_chain):
            ai_reasons.append("AI value chain: " + str(value_chain.get("reason") or "qualified"))
        for reason in ai_reasons:
            add("ai", reason)
            add("lens:ai", reason)

    text = f'{article["title"]} {article["snippet"]}'
    company_parents = {
        "GS건설": "competitor_contractors",
        "대우건설": "competitor_contractors",
        "롯데건설": "competitor_contractors",
        "현대엔지니어링": "hyundai_group",
    }
    for company, token in COMPANY_SUBFILTERS.items():
        parent = company_parents[company]
        if company in text and parent in profile_reasons:
            lens_tokens.add(token)
            add(token, f"material subject: {company}; {profile_reasons[parent]}")

    domestic = topic_profiles.get_execution_scope_tag("domestic_site")
    if (
        domestic is not None
        and "hdec" in categories
        and topic_profiles.match_lens_tag(article, domestic)
    ):
        lens_tokens.add("lens:domestic_site")
        add("lens:domestic_site", "explicit domestic construction-site phrase")

    approved = {
        token
        for rows in APPROVED_SUBFILTERS.values()
        for _label, token, _sub2 in rows
        if token not in {"all", "magazine"}
    }
    lens_tokens &= approved
    evidence = {
        token: reasons
        for token, reasons in evidence.items()
        if token in categories or token in lens_tokens
    }
    return {
        "categories": categories,
        "lens_tokens": lens_tokens,
        "evidence": evidence,
        "upstream_category_memberships": sorted({
            str(value)
            for value in row.get("category_memberships") or []
            if str(value) in PRIMARY_CATEGORY_IDS
        }),
    }


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


def _market_pane(brief: Mapping) -> dict:
    snapshot = brief.get("market_snapshot") or {}
    rows = []
    for item in (snapshot.get("items") or []):
        if not isinstance(item, Mapping):
            continue
        rows.append({
            "id": str(item.get("id") or ""),
            "label": str(item.get("label_kr") or item.get("id") or "지표"),
            "category": str(item.get("category") or "other"),
            "value": item.get("value"),
            "unit": str(item.get("unit") or ""),
            "change_1d_pct": item.get("change_1d_pct"),
            "change_5d_pct": item.get("change_5d_pct"),
            "data_mode": str(item.get("data_mode") or "unavailable"),
            "is_stale": bool(item.get("is_stale", True)),
            "proxy_for": str(item.get("proxy_for") or ""),
            "source": str(item.get("source_provider") or ""),
            "as_of": str(item.get("as_of") or ""),
            "note": str(item.get("note_kr") or ""),
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
            "source": str(support[0].get("source") or ""),
            "url": publisher_direct.publisher_url(support[0]),
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
        semantic = _semantic_filter_contract(raw)
        categories = set(semantic["categories"])
        freshness = _freshness_info(raw, reference)
        record = {
            "safe_article_id": safe_id,
            "canonical_url_host": (urlparse(canonical).hostname or "").casefold().removeprefix("www."),
            "source": str(raw.get("display_source") or raw.get("source") or ""),
            "publication_timestamp": str(raw.get("published_at") or ""),
            "assigned_categories": sorted(categories - {"all"}),
            "semantic_filter_evidence": dict(semantic["evidence"]),
            "upstream_category_memberships": list(
                semantic["upstream_category_memberships"]
            ),
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
            "subfilters": set(semantic["lens_tokens"]),
            "semantic": semantic,
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
        semantic = item["semantic"]
        categories_for_row = set(semantic["categories"])
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
            "categories": sorted(categories_for_row, key=lambda token: tuple(CATEGORY_LABELS).index(token)),
            "subfilter_ids": sorted(semantic["lens_tokens"]),
            "semantic_filter_evidence": dict(semantic["evidence"]),
            "upstream_category_memberships": list(
                semantic["upstream_category_memberships"]
            ),
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
            "magazine": index < 12,
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

    subfilter_counts = Counter(
        token
        for article in articles
        for token in article.get("subfilter_ids", ())
    )
    subfilters = [
        {
            "id": token,
            "label": label,
            "count": subfilter_counts[token],
        }
        for category in CATEGORY_LABELS
        for label, token, _sub2 in APPROVED_SUBFILTERS[category]
        if token not in {"all", "magazine"}
    ]
    for article in articles:
        article["image_src"] = _fallback_image_data(article)
        article["image_status"] = "deterministic_fallback"
        article["image_source_kind"] = "fallback"
        article["image_source_page_url"] = article["url"]
        article["image_width"] = None
        article["image_height"] = None
        article["image_quality_accepted"] = False
        article["image_reason"] = "no_image_candidate"
        article["image_attempted"] = False
        article["image_cache_hit"] = False
        article["image_materialized"] = False
        article["image_retry_after"] = ""

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


def _article_tokens(article: Mapping) -> str:
    values = [*article.get("categories", ()), *article.get("subfilter_ids", ())]
    if article.get("magazine") is True:
        values.append("magazine")
    return " ".join(dict.fromkeys(escape(str(value)) for value in values if value))


def _article_thumbnail(article: Mapping, wrapper: str) -> str:
    source = str(article.get("image_src") or "")
    is_local = bool(source) and not source.lower().startswith(("http://", "https://", "//"))
    if article.get("image_status") == "local_materialized" and is_local:
        safe = escape(source, quote=True).replace("'", "&#x27;")
        thumb = f'<span class="thumb" style="background-image:url(\'{safe}\')"></span>'
    else:
        thumb = (
            f'<span class="thumb ph" style="--tint:{escape(str(article["tint"]))}">'
            f'<b>{escape(str(article["initials"]))}</b></span>'
        )
    return f'<span class="{wrapper}">{thumb}</span>'


def _article_card(article: Mapping, *, lead: bool = False) -> str:
    tokens = _article_tokens(article)
    article_id = escape(str(article["id"]), quote=True)
    carried = " · 검증 이월" if article.get("carried_forward") else ""
    if lead:
        return (
            f'<article class="lead" data-t="{tokens}" data-article="{article_id}" tabindex="0" role="button">'
            f'{_article_thumbnail(article, "lead-thumb")}<div class="lead-body">'
            f'<span class="verdict" style="color:{escape(str(article["verdict_color"]))};border-color:{escape(str(article["verdict_color"]))}">{escape(str(article["verdict"]))}</span>'
            f'<h2>{escape(str(article["title"]))}</h2>'
            f'<p class="lead-sum">{escape(str(article.get("summary") or ""))}</p>'
            f'<p class="src">{escape(str(article["source"]))} · {escape(str(article["published_label"]))}{carried}</p>'
            '</div></article>'
        )
    return (
        f'<article class="nitem" data-t="{tokens}" data-article="{article_id}" tabindex="0" role="button">'
        f'{_article_thumbnail(article, "nthumb")}<div class="nbody">'
        f'<h3>{escape(str(article["title"]))}</h3>'
        f'<p class="why"><span class="verdict sm" style="color:{escape(str(article["verdict_color"]))};border-color:{escape(str(article["verdict_color"]))}">{escape(str(article["verdict"]))}</span> {escape(str(article["why"]))}</p>'
        f'<p class="src">{escape(str(article["source"]))} · {escape(str(article["published_label"]))}{carried}</p>'
        '</div></article>'
    )


def _empty_lead() -> str:
    """Keep the immutable lead shell while reporting an honestly empty edition."""
    return (
        '<article class="lead" aria-disabled="true">'
        '<span class="lead-thumb"><span class="thumb ph" style="--tint:#68716A"><b>AI</b></span></span>'
        '<div class="lead-body"><span class="verdict" style="color:#68716A;border-color:#68716A">관찰</span>'
        '<h2>현재 조건을 충족한 신규 기사가 없습니다</h2>'
        '<p class="lead-sum">검증된 발행사 원문이 확보되면 이 위치에 표시됩니다.</p>'
        '<p class="src">현재 판 · 검증 대기</p></div></article>'
    )


def _subbars(model: Mapping) -> str:
    articles = list(model.get("articles") or [])
    counts = Counter(
        token
        for article in articles
        for token in article.get("subfilter_ids", ())
    )
    rows = []
    for category in CATEGORY_LABELS:
        buttons = []
        for index, (label, token, sub2) in enumerate(APPROVED_SUBFILTERS[category]):
            classes = "sub active" if index == 0 else "sub"
            if sub2:
                classes += " sub2"
            count = "" if token in {"all", "magazine"} else f" <b>{counts[token]}</b>"
            buttons.append(
                f'<button class="{classes}" data-filter="{escape(token, quote=True)}">'
                f'{escape(label)}{count}</button>'
            )
        rows.append(
            f'<div class="subbar" data-for="{category}">{"".join(buttons)}</div>'
        )
    return "".join(rows)


def _market_value(item: Mapping) -> tuple[str, str]:
    value = item.get("value")
    if value is None:
        return "N/A", escape(str(item.get("unit") or ""))
    if isinstance(value, float):
        label = f"{value:,.2f}".rstrip("0").rstrip(".")
    elif isinstance(value, int):
        label = f"{value:,}"
    else:
        label = str(value)
    return escape(label), escape(str(item.get("unit") or ""))


def _market_delta(item: Mapping) -> tuple[str, str]:
    raw = item.get("change_1d_pct")
    if raw is None:
        return "", "—"
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return "", "—"
    return ("up", f"▲ +{value:.1f}%") if value >= 0 else ("down", f"▼ {abs(value):.1f}%")


def _market_groups(model: Mapping) -> str:
    groups = (("원자재·철강", "construction_commodities", False), ("에너지", "construction_commodities", True), ("금리", "sovereign_yields", False), ("환율", "fx", False))
    energy_tokens = ("crude", "gas", "coal", "oil", "wti", "brent", "lng", "diesel")
    group_limits = {"원자재·철강": 7, "에너지": 6, "금리": 4, "환율": 16}
    rate_priority = ("us_10y", "us_5y", "us_3y", "us_2y", "kr_10y")
    output = []
    for label, category, energy_only in groups:
        items = []
        for item in model["market"]["items"]:
            if item.get("category") != category:
                continue
            is_energy = any(token in str(item.get("id") or "").casefold() for token in energy_tokens)
            if category == "construction_commodities" and is_energy != energy_only:
                continue
            items.append(item)
        if category == "sovereign_yields":
            priorities = {value: index for index, value in enumerate(rate_priority)}
            items.sort(key=lambda item: priorities.get(str(item.get("id") or ""), len(priorities)))
        items = items[:group_limits[label]]
        if not items:
            items = [{"label": "데이터 미수신", "value": None, "unit": "", "change_1d_pct": None}]
        rows = []
        for item in items:
            value, unit = _market_value(item)
            delta_class, delta = _market_delta(item)
            rows.append(
                '<div class="mrow">'
                f'<span class="mlabel">{escape(str(item["label"]))}</span>'
                '<svg class="spark" width="64" height="20" viewBox="0 0 64 20" aria-hidden="true"></svg>'
                f'<span class="mval">{value}<small>{unit}</small></span>'
                f'<em class="delta{(" " + delta_class) if delta_class else ""}">{escape(delta)}</em></div>'
            )
        visible = rows
        overflow: list[str] = []
        if label == "환율" and len(rows) > 4:
            visible, overflow = rows[:4], rows[4:]
        more = (
            '<details class="fx-more"><summary>기타 주요 환율 보기</summary>'
            + "".join(overflow) + "</details>"
            if overflow else ""
        )
        output.append(
            f'<div class="mgroup"><h3 class="mgroup-h">{label}</h3>'
            f'{"".join(visible)}{more}</div>'
        )
    return "".join(output)


def _rail_market_rows(model: Mapping) -> str:
    items = sorted(model["market"]["items"], key=lambda item: (item.get("value") is None, str(item.get("id"))))[:5]
    rows = []
    for item in items:
        value, unit = _market_value(item)
        delta_class, delta = _market_delta(item)
        rows.append(
            f'<li><span class="ml">{escape(str(item["label"]))}</span>'
            f'<span class="mv">{value}<small>{unit}</small></span>'
            f'<em class="delta{(" " + delta_class) if delta_class else ""}">{escape(delta)}</em></li>'
        )
    return "".join(rows) or '<li><span class="ml">시장지표 미연동</span><span class="mv">N/A</span><em class="delta">—</em></li>'


def _weather_map(model: Mapping) -> str:
    template = WEATHER_MAP_TEMPLATE.read_text(encoding="utf-8")
    region_keys = ["capital"] * 3 + ["central"] * 5 + ["honam"] * 3 + ["yeongnam"] * 5 + ["jeju"]
    aliases = {"capital": ("수도", "서울", "경기", "인천"), "central": ("중부", "충청", "강원"), "honam": ("호남", "전라", "광주"), "yeongnam": ("영남", "경상", "부산", "울산", "대구"), "jeju": ("제주",)}
    rows = list(model["weather"].get("rows") or [])
    for index, key in enumerate(region_keys):
        row = next((item for item in rows if any(alias in str(item.get("region") or "") for alias in aliases[key])), None)
        if row is None:
            fill, title = "#9AA0A0", "기상 데이터 미수신"
        else:
            grade = str(row.get("grade") or "확인 필요")
            fill = "#C24A3D" if "위험" in grade else "#D9A62E" if "주의" in grade else "#9DB8A0"
            details = [str(row.get("region") or "권역"), grade]
            if row.get("precipitation_probability") is not None:
                details.append(f'강수 {row["precipitation_probability"]}%')
            if row.get("gust_ms") is not None:
                details.append(f'돌풍 {row["gust_ms"]}㎧')
            if row.get("temperature_c") is not None:
                details.append(f'{row["temperature_c"]}℃')
            title = " · ".join(details)
        template = template.replace(f"{{{{FILL_{index}}}}}", fill).replace(f"{{{{TITLE_{index}}}}}", escape(title))
    return template


def _weather_notes(model: Mapping) -> tuple[str, str, str]:
    weather = model["weather"]
    rows = list(weather.get("rows") or [])
    basis = str(weather.get("forecast_at") or weather.get("updated_at") or "기준시각 미수신")
    if not rows:
        reason = str(weather.get("unavailable_reason") or "기상 데이터 미수신")
        return basis, reason, '<li class="warn"><b>전 권역</b> <span>확인 필요</span></li>'
    notable = [row for row in rows if any(token in str(row.get("grade") or "") for token in ("주의", "위험"))]
    impact = " · ".join(f'{row["region"]} {row["grade"]}' for row in notable[:2]) or "공개 예보 기준 특이 위험 신호 없음"
    notes = "".join(
        f'<li class="{"risk" if "위험" in str(row["grade"]) else "warn"}"><b>{escape(str(row["region"]))}</b> '
        f'<span>{escape(str(row["grade"]))}</span><small>{escape(str(row.get("basis") or row.get("status_note") or ""))}</small></li>'
        for row in (notable or rows)[:3]
    )
    return basis, impact, notes


def _safety_content(model: Mapping) -> str:
    safety = model["safety"]
    items = list(safety.get("items") or [])
    if not items:
        return (
            '<p class="num">검증 신호 <b>0건</b> <em class="delta">—</em><small>현재 판</small></p>'
            f'<small class="src">{escape(str(safety.get("unavailable_reason") or "검증 정보 미수신"))}</small>'
            '<p class="art">현재 검증된 안전·지정학 신호가 없습니다.<small>publisher-direct 검증 대기</small></p>'
        )
    first = items[0]
    link = escape(str(first.get("url") or ""), quote=True)
    title = escape(str(first.get("title") or "검증 안전 신호"))
    article = f'<a href="{link}" target="_blank" rel="noopener">{title}</a>' if link else title
    return (
        f'<p class="num">검증 신호 <b>{len(items)}건</b> <em class="delta">{escape(str(first.get("severity") or "모니터링"))}</em><small>현재 판</small></p>'
        f'<small class="src">{escape(str(safety.get("source") or "검증 기사"))} · {escape(str(safety.get("as_of") or "기준시각 미수신"))}</small>'
        f'<p class="art">{article}<small>{escape(str(first.get("source") or ""))} · 검증 기사 {int(first.get("article_count") or 0)}건</small></p>'
    )


def _article_data(model: Mapping) -> dict:
    return {
        str(article["id"]): {
            "title": article["title"], "source": article["source"],
            "time": article["published_label"], "published": article["published_at"],
            "byline": "", "verdict": article["verdict"],
            "verdictColor": article["verdict_color"], "why": article["why"],
            "snippet": article["summary"], "body": "", "bodyImages": [],
            "url": article["url"], "sourceUrl": article["url"], "tint": article["tint"],
            "categories": list(article.get("categories") or []),
            "subfilterIds": list(article.get("subfilter_ids") or []),
            "magazine": bool(article.get("magazine")),
            "semanticFilterEvidence": dict(
                article.get("semantic_filter_evidence") or {}
            ),
        }
        for article in model.get("articles") or []
    }


def render_html(model: Mapping) -> str:
    template = TEMPLATE.read_text(encoding="utf-8")
    articles = list(model.get("articles") or [])
    generated = _parse_datetime(model.get("generated_at")) or datetime.now(KST)
    weekdays = "월화수목금토일"
    basis, weather_impact, weather_notes = _weather_notes(model)
    market_note = str(model["market"].get("disclaimer") or "지연·대용(proxy) 시세 기준 — 현재 체결값이 아닙니다.")
    replacements = {
        "{{PAGE_TITLE}}": escape(f'HDEC News Sensor · {model["edition_label"]}'),
        "{{WHEN_LABEL}}": escape(f'{generated:%Y.%m.%d} ({weekdays[generated.weekday()]}) · 발행 {generated:%H:%M} · 생성 {model["generated_label"]}'),
        "{{SUBBARS}}": _subbars(model),
        "{{LEAD_ARTICLE}}": _article_card(articles[0], lead=True) if articles else _empty_lead(),
        "{{ARTICLE_CARDS}}": "\n".join(_article_card(row) for row in articles[1:]),
        "{{MARKET_COUNT}}": str(len(model["market"]["items"])),
        "{{MARKET_GROUPS}}": _market_groups(model),
        "{{MARKET_NOTE}}": escape(market_note),
        "{{WEATHER_BASIS}}": escape(basis),
        "{{WEATHER_MAP}}": _weather_map(model),
        "{{WEATHER_IMPACT}}": escape(weather_impact),
        "{{WEATHER_NOTES}}": weather_notes,
        "{{RAIL_MARKET_ROWS}}": _rail_market_rows(model),
        "{{SAFETY_CONTENT}}": _safety_content(model),
        "{{ARTICLE_DATA}}": _json_island(_article_data(model)),
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


def _fallback_reason(raw_reason: object, *, deadline_expired: bool = False) -> str:
    """Map resolver/materializer detail to the sealed public-image audit vocabulary."""
    if deadline_expired:
        return "total_deadline_exhausted_after_attempt"
    reason = str(raw_reason or "").casefold()
    if "timeout" in reason:
        return "timeout"
    if "content_type" in reason or "svg" in reason:
        return "invalid_mime"
    if "magic" in reason or "decode" in reason:
        return "invalid_magic"
    if "dimension" in reason or "too_small" in reason:
        return "dimensions_too_small"
    if any(marker in reason for marker in (
        "logo", "default_image", "site_default", "banner", "representative"
    )):
        return "logo_or_banner_rejected"
    if "duplicate" in reason:
        return "duplicate_image_rejected"
    if any(marker in reason for marker in (
        "unsafe", "redirect", "invalid_url", "non_https", "aggregator"
    )):
        return "unsafe_url_rejected"
    if any(marker in reason for marker in (
        "no_safe", "no_image", "had_no_safe", "not_attempted"
    )):
        return "no_image_candidate"
    if "publisher_page_unavailable" in reason or "publisher_blocked" in reason:
        return "publisher_blocked"
    if any(marker in reason for marker in (
        "http_", "httperror", "urlerror", "connectionerror", "gaierror",
        "download", "tls", "empty_body", "oversized",
    )):
        return "download_failed"
    return "materialization_failed"


def _retry_after(reason: str, reference: datetime) -> str:
    delay = (
        timedelta(hours=1)
        if reason in {"timeout", "download_failed", "total_deadline_exhausted_after_attempt"}
        else timedelta(hours=24)
        if reason in {
            "invalid_mime", "invalid_magic", "dimensions_too_small",
            "logo_or_banner_rejected", "duplicate_image_rejected",
            "unsafe_url_rejected", "materialization_failed",
        }
        else timedelta(hours=6)
    )
    return (reference + delay).isoformat(timespec="seconds")


def _future_retry(value: object, reference: datetime) -> bool:
    try:
        retry = datetime.fromisoformat(str(value or ""))
    except (TypeError, ValueError):
        return False
    if retry.tzinfo is None:
        retry = retry.replace(tzinfo=KST)
    return retry.astimezone(KST) > reference


def _previous_html_image_paths(output_root: Path) -> dict[str, str]:
    latest = output_root / "latest.html"
    if not latest.is_file():
        return {}
    try:
        html = latest.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return {}
    output: dict[str, str] = {}
    for attributes, body in re.findall(
        r"<article\b([^>]*)>(.*?)</article>", html, re.S | re.I
    ):
        identity = re.search(r'data-article="([^"]+)"', attributes)
        source = re.search(
            r'class="thumb"[^>]+background-image:url\(\'([^\']+)\'\)',
            body,
            re.I,
        )
        if identity and source:
            output[identity.group(1)] = source.group(1)
    return output


def _valid_cached_asset(
    output_root: Path,
    raw_path: object,
    *,
    editorial_briefings,
) -> tuple[Path, int, int] | None:
    text = str(raw_path or "").strip()
    if text.startswith(IMAGE_PUBLIC_SRC_PREFIX):
        name = text.removeprefix(IMAGE_PUBLIC_SRC_PREFIX)
    elif text.startswith("assets/images/"):
        name = text.removeprefix("assets/images/")
    else:
        return None
    if not name or Path(name).name != name:
        return None
    candidate = output_root / "assets" / "images" / name
    if candidate.parent.resolve() != (output_root / "assets" / "images").resolve():
        return None
    try:
        payload = candidate.read_bytes()
        extension, rejection = editorial_briefings._image_magic_extension(
            payload, "image/octet-stream"
        )
        if rejection or not extension or editorial_briefings.Image is None:
            return None
        with editorial_briefings.Image.open(BytesIO(payload)) as decoded:
            decoded.load()
            width, height = decoded.size
        if width < editorial_briefings.IMAGE_MIN_WIDTH or height < editorial_briefings.IMAGE_MIN_HEIGHT:
            return None
        if _dashboard_image_quality_rejection(
            payload,
            editorial_briefings=editorial_briefings,
        ):
            return None
    except (OSError, ValueError):
        return None
    return candidate, width, height


class _ImageNetworkBudget:
    """Shared global deadline and one-request-per-host guard for image work."""

    def __init__(self, *, deadline: float, downloader: Callable | None = None):
        self.deadline = deadline
        self.downloader = downloader
        self._lock = threading.Lock()
        self._host_locks: dict[str, threading.BoundedSemaphore] = {}

    def expired(self) -> bool:
        return monotonic_time.monotonic() >= self.deadline

    def _run(self, url: str, operation: Callable):
        remaining = self.deadline - monotonic_time.monotonic()
        if remaining <= 0:
            raise TimeoutError("image total deadline exhausted")
        host = (urlparse(str(url or "")).hostname or "").casefold()
        if not host:
            raise ValueError("image URL host missing")
        with self._lock:
            semaphore = self._host_locks.setdefault(
                host, threading.BoundedSemaphore(IMAGE_PER_HOST_CONCURRENCY)
            )
        if not semaphore.acquire(timeout=remaining):
            raise TimeoutError("image host deadline exhausted")
        try:
            if self.expired():
                raise TimeoutError("image total deadline exhausted")
            return operation()
        finally:
            semaphore.release()

    def fetch_page(self, url: str):
        from app import editorial_briefings

        return self._run(
            url,
            lambda: editorial_briefings._fetch_publisher_html(
                url,
                counters=editorial_briefings.ImageResolutionCounters(),
            ),
        )

    def probe(self, url: str) -> bool:
        from app import editorial_briefings

        return self._run(
            url,
            lambda: editorial_briefings._probe_image_mime(
                url,
                counters=editorial_briefings.ImageResolutionCounters(),
            ),
        )

    def download(self, url: str, *, referer_url: str = "", opener=None):
        from app import editorial_briefings

        download = self.downloader or editorial_briefings._download_image_bytes
        return self._run(
            url,
            lambda: download(url, referer_url=referer_url, opener=opener),
        )


def _image_task(
    index: int,
    article: Mapping,
    *,
    stage_root: Path,
    network: _ImageNetworkBudget,
    resolver: Callable,
):
    from app import editorial_briefings

    if network.expired():
        return index, None, None, None, "total_deadline_exhausted_after_attempt", 0
    image_input = {
        "selected_url": article["url"],
        "url": article["url"],
        "title": article["title"],
        "source": article["source"],
        "published_at": article.get("published_at"),
    }
    try:
        resolution = resolver(
            image_input,
            allow_network=True,
            page_fetcher=network.fetch_page,
            image_probe=network.probe,
        )
        if resolution.fallback_used:
            reason = _fallback_reason(
                resolution.reason,
                deadline_expired=network.expired(),
            )
            return index, resolution, None, None, reason, 0
        published = _parse_datetime(article.get("published_at")) or datetime.now(KST)
        candidate = editorial_briefings.EditorialArticle(
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
            image_fallback_used=False,
            image_reason=resolution.reason,
            image_candidates=resolution.candidates,
        )
        article_stage = stage_root / f"article-{index:03d}"
        dashboard_quality_rejections = 0

        def quality_guarded_download(url: str, **kwargs):
            nonlocal dashboard_quality_rejections
            download = network.download(url, **kwargs)
            if 200 <= download.status < 400:
                _extension, byte_rejection = editorial_briefings._image_magic_extension(
                    download.payload,
                    download.content_type,
                )
                if not byte_rejection:
                    rejection = _dashboard_image_quality_rejection(
                        download.payload,
                        editorial_briefings=editorial_briefings,
                    )
                    if rejection:
                        dashboard_quality_rejections += 1
                        raise editorial_briefings.ImageDownloadError(
                            rejection,
                            status=download.status,
                            content_type=download.content_type,
                            byte_size=len(download.payload),
                        )
            return download

        materialized, counters = editorial_briefings.materialize_preview_images(
            [candidate],
            article_stage,
            html_dir=article_stage,
            downloader=quality_guarded_download,
        )
        result = materialized[0]
        reason = "" if result.image_quality_accepted else _fallback_reason(
            result.image_materialization_reason or result.image_reason,
            deadline_expired=network.expired(),
        )
        return (
            index,
            resolution,
            result,
            counters,
            reason,
            dashboard_quality_rejections,
        )
    except Exception as exc:  # Resolver/download errors are fail-closed per article.
        return (
            index,
            None,
            None,
            None,
            _fallback_reason(type(exc).__name__, deadline_expired=network.expired()),
            0,
        )


def _dashboard_image_quality_rejection(payload: bytes, *, editorial_briefings) -> str:
    """Apply dashboard thumbnail dimensions and conservative logo/banner checks."""
    try:
        signals, width, height = editorial_briefings._decoded_image_quality_signals(
            payload
        )
        if (
            width < editorial_briefings.IMAGE_MIN_WIDTH
            or height < editorial_briefings.IMAGE_MIN_HEIGHT
        ):
            return "dimensions_too_small"
        with editorial_briefings.Image.open(BytesIO(payload)) as decoded:
            decoded.load()
            sample = editorial_briefings._flatten_for_quality(decoded)
        sample.thumbnail((192, 192))
        rgb = sample.convert("RGB")
        pixels = list(
            getattr(rgb, "get_flattened_data", rgb.getdata)()
        )
        dominant, _count = Counter(pixels).most_common(1)[0]
        active = [
            (index % sample.width, index // sample.width)
            for index, pixel in enumerate(pixels)
            if sum(abs(pixel[channel] - dominant[channel]) for channel in range(3)) > 35
        ]
        if active:
            active_width = (max(x for x, _y in active) - min(x for x, _y in active) + 1) / sample.width
            active_height = (max(y for _x, y in active) - min(y for _x, y in active) + 1) / sample.height
            active_ratio = len(active) / len(pixels)
        else:
            active_width = active_height = active_ratio = 0.0
        signal_set = set(signals)
        aspect_ratio = width / height
        centered_logo = (
            "small_effective_content_area" in signal_set
            and active_ratio < 0.22
            and active_width < 0.90
            and active_height < 0.60
        )
        banner_like = (
            "logo_like_dimensions" in signal_set
            and aspect_ratio >= 4.0
        )
        if centered_logo or banner_like:
            return "logo_or_banner_rejected"
    except (OSError, ValueError, editorial_briefings.ImageDownloadError):
        return "invalid_magic"
    return ""


def _persist_image_associations(
    state_path: Path | None,
    state: dict | None,
    articles: list[dict],
    *,
    reference: datetime,
) -> None:
    if state_path is None or state is None or not state_path.exists():
        return
    by_url = {
        publisher_direct.normalize_publisher_canonical_url(article["url"])
        or str(article["url"]): article
        for article in articles
    }
    changed = False
    for entry in state.get("entries") or []:
        article = by_url.get(str(entry.get("canonical_url") or ""))
        if article is None:
            continue
        src = str(article.get("image_src") or "")
        local_path = (
            "assets/images/" + src.removeprefix(IMAGE_PUBLIC_SRC_PREFIX)
            if src.startswith(IMAGE_PUBLIC_SRC_PREFIX)
            else ""
        )
        source_page = publisher_direct.normalize_publisher_canonical_url(
            article["image_source_page_url"]
        ) or str(article["url"])
        values = {
            "image_local_path": local_path,
            "image_status": article["image_status"],
            "image_source_kind": article["image_source_kind"],
            "image_source_page_url": source_page,
            "image_width": article["image_width"],
            "image_height": article["image_height"],
            "image_quality_accepted": article["image_quality_accepted"],
            "image_reason": article["image_reason"],
            "image_attempted": article["image_attempted"],
            "image_cache_hit": article["image_cache_hit"],
            "image_materialized": article["image_materialized"],
            "image_retry_after": article["image_retry_after"],
        }
        changed = changed or any(entry.get(key) != value for key, value in values.items())
        entry.update(values)
    if not changed:
        return
    state["generated_at"] = reference.isoformat(timespec="seconds")
    state["entries"] = sorted(
        state.get("entries") or [],
        key=lambda entry: str(entry["canonical_url"]).casefold(),
    )
    news_censor_verified_state.atomic_write_state(state_path, state)


def materialize_article_images(
    model: dict,
    *,
    output_root: Path,
    verified_state_path: Path | None = None,
    total_deadline_seconds: int = IMAGE_TOTAL_DEADLINE_SECONDS,
    resolver: Callable | None = None,
    downloader: Callable | None = None,
    now: datetime | None = None,
) -> dict:
    """Consider every rendered article with cache-first, bounded image work."""
    from app import editorial_briefings

    articles: list[dict] = list(model.get("articles") or [])
    reference = (now or datetime.now(KST)).astimezone(KST)
    state = None
    state_by_url: dict[str, dict] = {}
    if verified_state_path is not None:
        loaded = news_censor_verified_state.load_state(
            verified_state_path,
            now=reference,
        )
        state = loaded.state
        state_by_url = {
            str(entry["canonical_url"]): entry
            for entry in state.get("entries") or []
        }
    previous_paths = _previous_html_image_paths(output_root)
    pending: list[tuple[int, dict]] = []
    used_digests: set[str] = set()
    used_asset_names: set[str] = set()
    valid_cached = negative_cache_hits = 0

    for index, article in enumerate(articles):
        canonical_url = (
            publisher_direct.normalize_publisher_canonical_url(article["url"])
            or str(article["url"])
        )
        entry = state_by_url.get(canonical_url) or {}
        cached_path = entry.get("image_local_path") or previous_paths.get(article["id"])
        cached = _valid_cached_asset(
            output_root,
            cached_path,
            editorial_briefings=editorial_briefings,
        )
        if cached is not None:
            asset, width, height = cached
            digest = hashlib.sha256(asset.read_bytes()).hexdigest()
            if digest not in used_digests and asset.name not in used_asset_names:
                used_digests.add(digest)
                used_asset_names.add(asset.name)
                article.update({
                    "image_src": IMAGE_PUBLIC_SRC_PREFIX + asset.name,
                    "image_status": "local_materialized",
                    "image_source_kind": str(entry.get("image_source_kind") or "cached_local"),
                    "image_source_page_url": str(entry.get("image_source_page_url") or article["url"]),
                    "image_width": int(entry.get("image_width") or width),
                    "image_height": int(entry.get("image_height") or height),
                    "image_quality_accepted": True,
                    "image_reason": "cached_local_image",
                    "image_attempted": False,
                    "image_cache_hit": True,
                    "image_materialized": True,
                    "image_retry_after": "",
                })
                valid_cached += 1
                continue
        cached_reason = str(entry.get("image_reason") or "")
        if (
            entry.get("image_status") == "deterministic_fallback"
            and cached_reason in IMAGE_FALLBACK_REASONS
            and _future_retry(entry.get("image_retry_after"), reference)
        ):
            article.update({
                "image_source_kind": str(entry.get("image_source_kind") or "fallback"),
                "image_source_page_url": str(entry.get("image_source_page_url") or article["url"]),
                "image_width": None,
                "image_height": None,
                "image_quality_accepted": False,
                "image_reason": cached_reason,
                "image_attempted": False,
                "image_cache_hit": True,
                "image_materialized": False,
                "image_retry_after": str(entry.get("image_retry_after") or ""),
            })
            negative_cache_hits += 1
            continue
        pending.append((index, article))

    deadline = monotonic_time.monotonic() + max(1, int(total_deadline_seconds))
    network = _ImageNetworkBudget(deadline=deadline, downloader=downloader)
    image_resolver = resolver or editorial_briefings.resolve_article_image
    task_results: list[tuple] = []
    with tempfile.TemporaryDirectory(
        prefix="hdec-news-censor-images-",
        dir="/tmp",
    ) as stage_name:
        stage = Path(stage_name)
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=IMAGE_GLOBAL_CONCURRENCY,
            thread_name_prefix="news-censor-image",
        ) as executor:
            futures = [
                executor.submit(
                    _image_task,
                    index,
                    article,
                    stage_root=stage,
                    network=network,
                    resolver=image_resolver,
                )
                for index, article in pending
            ]
            task_results = [future.result() for future in futures]

        resolution_successes = download_attempts = quality_rejections = 0
        newly_materialized = 0
        for (
            index,
            resolution,
            image_article,
            counters,
            reason,
            dashboard_quality_rejections,
        ) in task_results:
            article = articles[index]
            article["image_attempted"] = True
            resolution_successes += int(
                resolution is not None and not resolution.fallback_used
            )
            if counters is not None:
                download_attempts += int(counters.image_download_attempts)
                quality_rejections += int(counters.image_quality_rejections)
            quality_rejections += dashboard_quality_rejections
            asset = str(
                image_article.image_local_asset
                if image_article is not None
                else ""
            )
            source = stage / f"article-{index:03d}" / "assets" / "images" / asset
            accepted = bool(
                image_article is not None
                and image_article.image_quality_accepted
                and asset
                and source.is_file()
            )
            if accepted:
                payload = source.read_bytes()
                digest = hashlib.sha256(payload).hexdigest()
                if accepted and (digest in used_digests or asset in used_asset_names):
                    accepted = False
                    reason = "duplicate_image_rejected"
                elif accepted:
                    used_digests.add(digest)
                    used_asset_names.add(asset)
            if accepted:
                destination = output_root / "assets" / "images" / asset
                _atomic_write_bytes(destination, payload)
                with editorial_briefings.Image.open(BytesIO(payload)) as decoded:
                    decoded.load()
                    width, height = decoded.size
                article.update({
                    "image_src": IMAGE_PUBLIC_SRC_PREFIX + asset,
                    "image_status": "local_materialized",
                    "image_source_kind": str(image_article.image_source_kind or "publisher_page"),
                    "image_source_page_url": str(image_article.image_source_page_url or article["url"]),
                    "image_width": width,
                    "image_height": height,
                    "image_quality_accepted": True,
                    "image_reason": "image_materialized",
                    "image_cache_hit": False,
                    "image_materialized": True,
                    "image_retry_after": "",
                })
                newly_materialized += 1
                continue
            final_reason = reason if reason in IMAGE_FALLBACK_REASONS else _fallback_reason(reason)
            article.update({
                "image_status": "deterministic_fallback",
                "image_source_kind": str(
                    getattr(resolution, "source_kind", "fallback") or "fallback"
                ),
                "image_source_page_url": str(
                    getattr(resolution, "source_page_url", "") or article["url"]
                ),
                "image_width": None,
                "image_height": None,
                "image_quality_accepted": False,
                "image_reason": final_reason,
                "image_cache_hit": False,
                "image_materialized": False,
                "image_retry_after": _retry_after(final_reason, reference),
            })

    local_positions = [
        index + 1 for index, article in enumerate(articles)
        if article["image_status"] == "local_materialized"
    ]
    fallback_positions = [
        index + 1 for index, article in enumerate(articles)
        if article["image_status"] == "deterministic_fallback"
    ]
    fallback_reasons = Counter(
        article["image_reason"]
        for article in articles
        if article["image_status"] == "deterministic_fallback"
    )
    fallback_count = len(fallback_positions)
    local_count = len(local_positions)
    counters = {
        "displayed_articles": len(articles),
        "valid_cached_local_images": valid_cached,
        "negative_cache_hits": negative_cache_hits,
        "image_resolution_attempted": len(pending),
        "image_resolution_successes": resolution_successes,
        "image_download_attempts": download_attempts,
        "image_materialized": local_count,
        "new_image_materialized": newly_materialized,
        "local_materialized": local_count,
        "deterministic_fallbacks": fallback_count,
        "quality_rejections": quality_rejections,
        "unsafe_url_rejections": fallback_reasons["unsafe_url_rejected"],
        "duplicate_image_rejections": fallback_reasons["duplicate_image_rejected"],
        "deadline_exhausted": fallback_reasons["total_deadline_exhausted_after_attempt"],
        "not_attempted_due_to_cap": 0,
        "fallback_reason_counts": dict(sorted(fallback_reasons.items())),
        "real_image_positions": local_positions,
        "fallback_positions": fallback_positions,
        "accounting_pass": len(articles) == local_count + fallback_count,
        "all_displayed_considered": (
            len(articles) == valid_cached + negative_cache_hits + len(pending)
        ),
    }
    if not counters["accounting_pass"] or not counters["all_displayed_considered"]:
        raise RuntimeError("News Censor image accounting mismatch")
    if any(
        article["image_status"] == "deterministic_fallback"
        and article["image_reason"] not in IMAGE_FALLBACK_REASONS
        for article in articles
    ):
        raise RuntimeError("News Censor fallback reason outside sealed vocabulary")
    model["image_materialization"] = counters
    _persist_image_associations(
        verified_state_path,
        state,
        articles,
        reference=reference,
    )
    return dict(counters)


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
    parser.add_argument(
        "--verified-state",
        type=Path,
        help=(
            "optional verified-state file used only for cached local image "
            "associations and retry TTLs"
        ),
    )
    parser.add_argument(
        "--canonical-output",
        type=Path,
        help="write the same latest HTML bytes to the canonical compatibility path",
    )
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
                verified_state_path=(
                    args.verified_state.resolve()
                    if args.verified_state is not None
                    else None
                ),
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
            "displayed_articles": model["article_count"],
            "valid_cached_local_images": 0,
            "image_resolution_attempted": 0,
            "image_resolution_successes": 0,
            "image_download_attempts": 0,
            "image_materialized": 0,
            "deterministic_fallbacks": model["article_count"],
            "not_attempted_due_to_cap": 0,
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
        if args.canonical_output:
            canonical = args.canonical_output.resolve()
            _atomic_write(canonical, html)
            if canonical.read_bytes() != latest.read_bytes():
                raise RuntimeError("canonical and compatibility dashboard bytes differ")
        print(
            f"news censor written: {latest} + {archive} ({len(html)} chars) "
            f"news_data_mode={model['news_data_mode']} articles={model['article_count']} "
            f"canonical_mirror={'yes' if args.canonical_output else 'no'}"
        )
        print(
            "news censor image accounting: "
            + json.dumps(
                model.get("image_materialization") or {},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0

    print(
        f"{CONTRACT} edition={model['edition']} mode={model['news_data_mode']} "
        f"articles={model['article_count']} portal_urls=0 html_chars={len(html)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
