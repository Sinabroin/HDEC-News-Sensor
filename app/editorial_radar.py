"""Shared Morning Radar observability and Watch-to-Daily bridge contracts.

This module is deliberately a read-only leaf over the existing AI-centrality,
executive-materiality, decision-relevance, source-quality, and Watch-ledger
contracts.  It does not collect, rank, publish, send, or mutate Watch state.

Only lightweight metadata is emitted: identity, title, source, safe URL,
timestamps, stage, bounded reasons, and explicit decision dimensions.  Article
bodies, generated full summaries, provider queries, credentials, and headers
are never persisted in the radar artifact.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from datetime import datetime
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse

from app import (
    ai_centrality,
    decision_relevance,
    executive_materiality,
    news_access,
    source_priority,
    source_quality,
    teams_push_state,
)

RADAR_VERSION = 1
MAX_RADAR_ROWS = 750
MAX_PUBLIC_NEAR_MISSES = 3
DAILY_SAFE_SOURCE_TIERS = frozenset(
    {"primary_10", "secondary_3", "major_secondary", "official_institution"}
)

STAGE_COLLECTED = "collected"
STAGE_AI_CENTRAL = "ai_central"
STAGE_EXECUTIVE_CANDIDATE = "executive_candidate"
STAGE_SELECTED = "selected"
STAGE_EXCLUDED = "excluded"
STAGE_WATCH_BRIDGE = "watch_bridge"

STAGE_LABELS = {
    STAGE_COLLECTED: "전체 수집",
    STAGE_AI_CENTRAL: "AI 중심",
    STAGE_EXECUTIVE_CANDIDATE: "임원 후보",
    STAGE_SELECTED: "선정",
    STAGE_EXCLUDED: "제외",
    STAGE_WATCH_BRIDGE: "아침 레이더 추가 포착",
}

_INFRA_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("data_center", ("데이터센터", "데이터 센터", "data center", "datacenter", "idc")),
    ("power_grid", ("전력", "전력망", "그리드", "grid", "송전", "변전", "송배전", "전기")),
    ("cooling_water", ("냉각", "공조", "용수", "물 인프라", "water infrastructure")),
    ("energy", ("에너지", "발전", "원전", "smr", "재생에너지", "태양광", "풍력", "ess")),
    ("site_industrial", ("부지", "입지", "산업단지", "산업 단지", "토지", "site development")),
    ("epc_construction", ("epc", "건설", "시공", "착공", "준공", "엔지니어링", "engineering")),
    ("smart_construction", ("스마트건설", "스마트 건설", "bim", "디지털 트윈", "건설 ai")),
    ("physical_ai", ("피지컬 ai", "physical ai", "건설로봇", "건설 로봇", "로보틱스")),
    ("semiconductor_fab", ("반도체 팹", "반도체 공장", "fab", "파운드리", "클러스터")),
)

_PROJECT_CONSEQUENCE_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("project", ("프로젝트", "개발사업", "개발 사업", "건설사업", "건설 사업", "발주")),
    ("delivery", ("수주", "계약", "착공", "준공", "구축", "증설", "확충", "가동")),
    ("constraint", ("병목", "부족", "포화", "지연", "차질", "인허가", "규제", "반발")),
    ("capital", ("투자", "출자", "프로젝트금융", "프로젝트 금융", "pf", "펀딩", "자금")),
    ("strategy", ("진출", "공략", "영토 넓", "사업 확대", "사업 확장", "포트폴리오 확대", "사업 강화")),
)

_COMPETITOR_TERMS = (
    "삼성물산", "대우건설", "gs건설", "dl이앤씨", "포스코이앤씨", "sk에코플랜트",
)
_CAPITAL_RE = re.compile(
    r"[0-9][0-9,.]*\s*(?:조|억|천억|백억|만)\s*(?:원|달러)|"
    r"[0-9][0-9,.]*\s*(?:gw|mw|기가와트|메가와트)",
    re.IGNORECASE,
)
_PRIVATE_SUFFIXES = (".local", ".internal", ".localhost", ".intranet", ".home", ".lan")
_DIMENSION_KEYS = frozenset(
    {
        "ai_centrality",
        "ai_central",
        "ai_reason",
        "executive_materiality",
        "executive_reason",
        "hdec_strategic_relevance",
        "infrastructure_project_specificity",
        "capital_investment_consequence",
        "competitor_relevance",
        "source_quality",
        "source_type",
        "source_delivery_tier",
        "decision_relevance_score",
        "decision_relevance_tier",
        "business_actionability",
        "relevant_dimensions",
    }
)


def _clean(value: object, *, limit: int = 500) -> str:
    return " ".join(str(value or "").split())[:limit]


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _parse_time(value: object) -> datetime | None:
    raw = _clean(value, limit=80)
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def safe_public_url(value: object) -> str:
    """Return a bounded public http(s) URL, otherwise ``""``."""
    raw = _clean(value, limit=2048)
    try:
        parsed = urlparse(raw)
        port = parsed.port
    except (TypeError, ValueError):
        return ""
    host = (parsed.hostname or "").casefold().rstrip(".")
    if (
        parsed.scheme not in {"http", "https"}
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 80, 443}
        or host == "localhost"
        or host.endswith(_PRIVATE_SUFFIXES)
    ):
        return ""
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None and not literal.is_global:
        return ""
    return raw


def _provider(raw: Mapping[str, Any]) -> str:
    metadata = _mapping(raw.get("source_metadata") or raw.get("source_metadata_json"))
    return _clean(
        metadata.get("provider")
        or raw.get("provider")
        or raw.get("collection_source_kind"),
        limit=80,
    )


def _factual_evidence(raw: Mapping[str, Any]) -> dict[str, str]:
    """Bounded publisher-owned evidence; generated summaries/query text excluded."""
    metadata = _mapping(raw.get("source_metadata") or raw.get("source_metadata_json"))
    return {
        "title": _clean(raw.get("title"), limit=300),
        "subtitle": _clean(raw.get("subtitle") or metadata.get("subtitle"), limit=300),
        "snippet": _clean(raw.get("snippet"), limit=600),
        "publisher_section": _clean(
            raw.get("publisher_section") or metadata.get("publisher_section"),
            limit=80,
        ),
    }


def _group_hits(text: str, groups: Iterable[tuple[str, tuple[str, ...]]]) -> tuple[str, ...]:
    low = text.casefold()
    return tuple(
        name for name, terms in groups if any(term.casefold() in low for term in terms)
    )


def strategic_dimensions(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Explain shared evidence dimensions without creating a new selection gate."""
    evidence = _factual_evidence(raw)
    text = " ".join(evidence.values())
    centrality = ai_centrality.classify(evidence)
    materiality = executive_materiality.executive_qualification(evidence)
    relevance = decision_relevance.classify(
        {
            "title": evidence["title"],
            "snippet": evidence["snippet"],
            "source": _clean(raw.get("source"), limit=120),
        }
    )
    quality = source_quality.classify(
        _clean(raw.get("source"), limit=120), evidence["title"]
    )
    selected_link = news_access.choose_article_link(raw)
    delivery_tier = source_priority.publisher_delivery_tier(
        _clean(raw.get("source"), limit=120),
        safe_public_url(selected_link.url or raw.get("url")),
    )
    infrastructure = _group_hits(text, _INFRA_GROUPS)
    consequences = _group_hits(text, _PROJECT_CONSEQUENCE_GROUPS)
    competitor_hits = tuple(
        term for term in _COMPETITOR_TERMS if term.casefold() in text.casefold()
    )
    concrete_capital = bool(_CAPITAL_RE.search(text))
    hdec_score = 3 if relevance.get("hdec_strategic") else 0
    if not hdec_score and competitor_hits and infrastructure:
        hdec_score = 3
    elif not hdec_score and (infrastructure and consequences):
        hdec_score = 2
    elif not hdec_score and relevance.get("decision_relevance_score", 0) > 0:
        hdec_score = 1
    infra_score = 2 if infrastructure and consequences else 1 if infrastructure else 0
    capital_score = (
        2 if concrete_capital and "capital" in consequences
        else 1 if concrete_capital or "capital" in consequences
        else 0
    )
    competitor_score = 2 if competitor_hits and infrastructure else 1 if competitor_hits else 0
    actionability = min(
        3,
        int(materiality.qualified)
        + int(bool(infrastructure and consequences))
        + int(bool(competitor_hits and infrastructure)),
    )
    relevant_dimensions = list(infrastructure) + list(consequences)
    if competitor_hits:
        relevant_dimensions.append("competitor_strategy")
    if concrete_capital:
        relevant_dimensions.append("concrete_capital_or_capacity")
    return {
        "ai_centrality": centrality.level,
        "ai_central": bool(centrality.is_central),
        "ai_reason": _clean(centrality.reason, limit=180),
        "executive_materiality": bool(materiality.qualified),
        "executive_reason": _clean(materiality.reason, limit=180),
        "hdec_strategic_relevance": hdec_score,
        "infrastructure_project_specificity": infra_score,
        "capital_investment_consequence": capital_score,
        "competitor_relevance": competitor_score,
        "source_quality": _clean(quality.get("source_quality"), limit=40),
        "source_type": _clean(quality.get("source_type"), limit=40),
        "source_delivery_tier": _clean(delivery_tier.get("tier"), limit=40),
        "decision_relevance_score": float(
            relevance.get("decision_relevance_score") or 0
        ),
        "decision_relevance_tier": _clean(
            relevance.get("decision_relevance_tier"), limit=20
        ),
        "business_actionability": actionability,
        "relevant_dimensions": list(dict.fromkeys(relevant_dimensions))[:12],
    }


def _sanitized_dimensions(value: object) -> dict[str, Any]:
    raw = _mapping(value)
    output: dict[str, Any] = {}
    for key in _DIMENSION_KEYS:
        item = raw.get(key)
        if key in {"ai_central", "executive_materiality"}:
            output[key] = item is True
        elif key in {
            "hdec_strategic_relevance",
            "infrastructure_project_specificity",
            "capital_investment_consequence",
            "competitor_relevance",
            "business_actionability",
        }:
            try:
                output[key] = max(0, min(10, int(item or 0)))
            except (TypeError, ValueError):
                output[key] = 0
        elif key == "decision_relevance_score":
            try:
                output[key] = float(item or 0)
            except (TypeError, ValueError):
                output[key] = 0.0
        elif key == "relevant_dimensions":
            output[key] = [
                _clean(entry, limit=60)
                for entry in item[:12]
                if _clean(entry, limit=60)
            ] if isinstance(item, list) else []
        else:
            output[key] = _clean(item, limit=180)
    return output


def _sanitized_row(value: object) -> dict[str, Any] | None:
    """Re-allowlist a persisted row, even when its input is untrusted JSON."""
    raw = _mapping(value)
    if not raw:
        return None
    stage = _clean(raw.get("stage"), limit=40)
    if stage not in STAGE_LABELS:
        stage = STAGE_COLLECTED
    try:
        sequence = max(0, int(raw.get("sequence") or 0))
    except (TypeError, ValueError):
        sequence = 0
    row: dict[str, Any] = {
        "article_id": _clean(raw.get("article_id"), limit=120),
        "title": _clean(raw.get("title"), limit=300),
        "source": _clean(raw.get("source"), limit=120),
        "url": safe_public_url(raw.get("url")),
        "published_at": _clean(raw.get("published_at"), limit=80),
        "first_seen_at": _clean(raw.get("first_seen_at"), limit=80),
        "first_material_discovery_at": _clean(
            raw.get("first_material_discovery_at"), limit=80
        ),
        "collection_provider": _clean(raw.get("collection_provider"), limit=80),
        "category": _clean(raw.get("category"), limit=60),
        "stage": stage,
        "stage_label": STAGE_LABELS[stage],
        "qualification_reason": _clean(raw.get("qualification_reason"), limit=240),
        "rejection_reason": _clean(raw.get("rejection_reason"), limit=240),
        "selected": stage == STAGE_SELECTED,
        "sequence": sequence,
        "dimensions": _sanitized_dimensions(raw.get("dimensions")),
    }
    for key, limit in (
        ("watch_sent_at", 80),
        ("watch_importance", 30),
        ("watch_delivery_id", 100),
        ("daily_disposition", 80),
        ("temporal_distinction", 100),
    ):
        if raw.get(key) is not None:
            row[key] = _clean(raw.get(key), limit=limit)
    if "title_available" in raw:
        row["title_available"] = raw.get("title_available") is True
    return row


def lightweight_row(raw: Mapping[str, Any], *, sequence: int = 0) -> dict[str, Any]:
    """Allowlist-only row for persistence and browser review."""
    evidence = _factual_evidence(raw)
    selected = news_access.choose_article_link(raw)
    url = safe_public_url(selected.url or raw.get("url"))
    source = _clean(raw.get("source"), limit=120)
    explicit_id = _clean(
        raw.get("article_id") or raw.get("article_key") or raw.get("id"),
        limit=120,
    )
    if explicit_id:
        article_id = explicit_id
    else:
        stable = "\x1f".join((url, evidence["title"], source))
        article_id = "radar-" + hashlib.sha256(stable.encode("utf-8")).hexdigest()[:20]
    metadata = _mapping(raw.get("source_metadata") or raw.get("source_metadata_json"))
    first_seen = (
        raw.get("first_seen_at")
        or metadata.get("first_seen_at")
        or raw.get("collected_at")
        or metadata.get("collected_at")
    )
    material_discovery = (
        raw.get("first_material_discovery_at")
        or metadata.get("first_material_discovery_at")
        or first_seen
    )
    return {
        "article_id": article_id,
        "title": evidence["title"],
        "source": source,
        "url": url,
        "published_at": _clean(raw.get("published_at"), limit=80),
        "first_seen_at": _clean(first_seen, limit=80),
        "first_material_discovery_at": _clean(material_discovery, limit=80),
        "collection_provider": _provider(raw),
        "category": _clean(raw.get("category"), limit=60),
        "stage": STAGE_COLLECTED,
        "stage_label": STAGE_LABELS[STAGE_COLLECTED],
        "qualification_reason": "collection_metadata_normalized",
        "rejection_reason": "",
        "selected": False,
        "sequence": max(0, int(sequence)),
        "dimensions": strategic_dimensions(raw),
    }


def set_stage(
    row: dict[str, Any],
    stage: str,
    *,
    qualification_reason: str = "",
    rejection_reason: str = "",
) -> None:
    if stage not in STAGE_LABELS:
        raise ValueError(f"unsupported radar stage: {stage}")
    row["stage"] = stage
    row["stage_label"] = STAGE_LABELS[stage]
    row["qualification_reason"] = _clean(qualification_reason, limit=240)
    row["rejection_reason"] = _clean(rejection_reason, limit=240)
    row["selected"] = stage == STAGE_SELECTED


def collection_total(collection_audit: Mapping[str, Any], normalized_count: int) -> int:
    provider_keys = (
        "naver_articles_collected",
        "google_news_articles_collected",
        "publisher_direct_rss_articles_collected",
    )
    provider_values = [max(0, int(collection_audit.get(key) or 0)) for key in provider_keys]
    return sum(provider_values) if any(provider_values) else max(0, int(normalized_count))


def build_audit(
    rows: Iterable[Mapping[str, Any]],
    *,
    collection_audit: Mapping[str, Any],
    selection_audit: Mapping[str, Any],
    selected_count: int,
) -> dict[str, Any]:
    """Build the one Editor/Daily radar artifact with exact funnel semantics."""
    normalized = [
        sanitized
        for row in rows
        if (sanitized := _sanitized_row(row)) is not None
    ]
    priority = {
        STAGE_SELECTED: 0,
        STAGE_EXECUTIVE_CANDIDATE: 1,
        STAGE_AI_CENTRAL: 2,
        STAGE_WATCH_BRIDGE: 3,
        STAGE_EXCLUDED: 4,
        STAGE_COLLECTED: 5,
    }
    normalized.sort(
        key=lambda row: (
            priority.get(str(row.get("stage")), 9),
            -int(bool(row.get("published_at"))),
            int(row.get("sequence") or 0),
        )
    )
    emitted = normalized[:MAX_RADAR_ROWS]
    stage_counts = {
        stage: sum(str(row.get("stage")) == stage for row in normalized)
        for stage in STAGE_LABELS
    }
    selected = max(0, int(selected_count))
    ai_count = max(0, int(selection_audit.get("ai_central_qualified_count") or 0))
    executive_count = max(
        0, int(selection_audit.get("executive_qualified_count") or 0)
    )
    return {
        "version": RADAR_VERSION,
        "funnel": {
            "collection_count": collection_total(collection_audit, len(normalized)),
            "normalized_row_count": len(normalized),
            "ai_central_count": ai_count,
            "executive_candidate_count": executive_count,
            "selected_count": selected,
            "watch_bridge_count": 0,
            "late_watch_count": 0,
        },
        "provider_counts": {
            "naver": max(0, int(collection_audit.get("naver_articles_collected") or 0)),
            "google_news": max(
                0, int(collection_audit.get("google_news_articles_collected") or 0)
            ),
            "publisher_direct": max(
                0,
                int(collection_audit.get("publisher_direct_rss_articles_collected") or 0),
            ),
        },
        "stage_counts": stage_counts,
        "rows": emitted,
        "rows_emitted": len(emitted),
        "rows_truncated": max(0, len(normalized) - len(emitted)),
        "row_body_fields_persisted": False,
        "dom_page_size": 50,
    }


def normalize_audit(value: object, *, selected_count: int | None = None) -> dict[str, Any]:
    """Fail-soft reader for an embedded radar artifact."""
    if not isinstance(value, Mapping) or value.get("version") != RADAR_VERSION:
        count = max(0, int(selected_count or 0))
        return build_audit(
            (),
            collection_audit={},
            selection_audit={
                "ai_central_qualified_count": count,
                "executive_qualified_count": count,
            },
            selected_count=count,
        )
    output = json.loads(json.dumps(value, ensure_ascii=False))
    funnel = output.get("funnel")
    if not isinstance(funnel, dict):
        funnel = {}
        output["funnel"] = funnel
    for key in (
        "collection_count",
        "normalized_row_count",
        "ai_central_count",
        "executive_candidate_count",
        "selected_count",
        "watch_bridge_count",
        "late_watch_count",
    ):
        funnel[key] = max(0, int(funnel.get(key) or 0))
    if selected_count is not None:
        funnel["selected_count"] = max(0, int(selected_count))
    rows = output.get("rows")
    output["rows"] = [
        sanitized
        for row in (rows[:MAX_RADAR_ROWS] if isinstance(rows, list) else [])
        if (sanitized := _sanitized_row(row)) is not None
    ]
    for key in ("morning_bridge_rows", "late_watch_rows"):
        raw_rows = output.get(key)
        output[key] = [
            sanitized
            for row in (
                raw_rows[:MAX_PUBLIC_NEAR_MISSES]
                if isinstance(raw_rows, list)
                else []
            )
            if (sanitized := _sanitized_row(row)) is not None
        ]
    window = output.get("bridge_window")
    if isinstance(window, Mapping):
        delivery_ids = window.get("watch_delivery_ids")
        output["bridge_window"] = {
            "snapshot_at": _clean(window.get("snapshot_at"), limit=80),
            "finalization_at": _clean(window.get("finalization_at"), limit=80),
            "watch_delivery_ids": [
                _clean(item, limit=100)
                for item in (delivery_ids[:MAX_RADAR_ROWS] if isinstance(delivery_ids, list) else [])
                if _clean(item, limit=100)
            ],
        }
    output["rows_emitted"] = len(output["rows"])
    output["dom_page_size"] = 50
    output["row_body_fields_persisted"] = False
    return output


def _reverse_ledger_value(
    state: Mapping[str, Any], map_name: str, delivery_id: str
) -> str:
    rows = _mapping(state.get(map_name))
    for key, value in rows.items():
        if _clean(_mapping(value).get("delivery_id"), limit=100) == delivery_id:
            return _clean(key, limit=2048)
    return ""


def watch_bridge(
    state: Mapping[str, Any],
    *,
    snapshot_at: datetime,
    finalization_at: datetime,
    coverage_start: datetime,
    coverage_end: datetime,
    existing_article_ids: Iterable[str] = (),
    existing_urls: Iterable[str] = (),
    delivered_at: datetime | None = None,
) -> dict[str, Any]:
    """Read important Watch sends into a bounded, non-sending morning bridge.

    A pre-finalization item becomes observable, never automatically selected.
    A post-delivery item is counted separately and cannot mutate or resend the
    already-delivered Daily.
    """
    if any(value.tzinfo is None for value in (snapshot_at, finalization_at, coverage_start, coverage_end)):
        raise ValueError("morning bridge timestamps must be timezone-aware")
    if delivered_at is not None and delivered_at.tzinfo is None:
        raise ValueError("Daily delivery timestamp must be timezone-aware")
    if finalization_at < snapshot_at or coverage_end < coverage_start:
        raise ValueError("morning bridge timestamp order is invalid")
    validated = teams_push_state.validate_state(state)
    known_ids = {_clean(value, limit=120) for value in existing_article_ids if _clean(value)}
    known_urls = {
        teams_push_state.normalize_url(value)
        for value in existing_urls
        if teams_push_state.normalize_url(value)
    }
    bridge_rows: list[dict[str, Any]] = []
    late_rows: list[dict[str, Any]] = []
    seen_deliveries: set[str] = set()
    for article_id, raw_entry in validated.get("article_ids", {}).items():
        entry = _mapping(raw_entry)
        delivery_id = _clean(entry.get("delivery_id"), limit=100)
        if not delivery_id or delivery_id in seen_deliveries:
            continue
        seen_deliveries.add(delivery_id)
        importance = _clean(entry.get("importance"), limit=30).casefold()
        first_sent = _parse_time(entry.get("first_sent_at") or entry.get("sent_at"))
        if importance not in {"important", "top"} or first_sent is None:
            continue
        # A Morning Bridge belongs only to the Editor snapshot's local edition
        # day.  Historical republish/verification must never sweep later Watch
        # sends into an already-frozen old Daily merely because it runs today.
        if first_sent.astimezone(snapshot_at.tzinfo).date() != snapshot_at.date():
            continue
        url = safe_public_url(
            entry.get("url")
            or _reverse_ledger_value(validated, "normalized_urls", delivery_id)
        )
        if _clean(article_id, limit=120) in known_ids or (
            url and teams_push_state.normalize_url(url) in known_urls
        ):
            continue
        if first_sent <= snapshot_at:
            continue
        title = _clean(entry.get("title"), limit=300)
        title_fingerprint = _reverse_ledger_value(
            validated, "title_fingerprints", delivery_id
        )
        title_available = bool(title)
        if not title:
            title = _clean(title_fingerprint, limit=300) or (
                f"Watch 중요 기사 · {_clean(entry.get('source'), limit=120)}"
            )
        bridge_raw = {
            "article_id": _clean(article_id, limit=120),
            "title": title,
            "source": _clean(entry.get("source"), limit=120),
            "url": url,
            "published_at": _clean(entry.get("published_at"), limit=80),
            "first_seen_at": _clean(entry.get("first_seen_at"), limit=80),
            "first_material_discovery_at": _clean(
                entry.get("first_material_discovery_at") or first_sent.isoformat(),
                limit=80,
            ),
            "collection_source_kind": "watch_delivery_ledger",
        }
        row = lightweight_row(bridge_raw)
        row.update(
            {
                "article_id": _clean(article_id, limit=120),
                "watch_sent_at": first_sent.isoformat(),
                "watch_importance": importance,
                "watch_delivery_id": delivery_id,
                "title_available": title_available,
            }
        )
        published = _parse_time(row.get("published_at"))
        temporal_reason = ""
        if published is None:
            temporal_reason = "publication_time_unavailable_new_watch_discovery"
        elif not (coverage_start <= published <= coverage_end):
            temporal_reason = "old_publication_new_morning_discovery"
        dimensions = row["dimensions"]
        source_ok = bool(
            url
            and dimensions.get("source_quality") != "excluded"
            and dimensions.get("source_delivery_tier") in DAILY_SAFE_SOURCE_TIERS
        )
        daily_safe = bool(
            source_ok
            and dimensions.get("ai_central")
            and dimensions.get("executive_materiality")
        )
        if daily_safe and not temporal_reason:
            reason = "pre_daily_important_watch_requires_daily_reconsideration"
            disposition = "daily_reconsideration_eligible"
        elif temporal_reason:
            reason = temporal_reason
            disposition = "morning_radar_only"
        elif not url:
            reason = "watch_bridge_safe_url_unavailable"
            disposition = "morning_radar_only"
        elif not dimensions.get("ai_central"):
            reason = "watch_bridge_not_ai_central_under_daily_gate"
            disposition = "morning_radar_only"
        elif not dimensions.get("executive_materiality"):
            reason = "watch_bridge_failed_daily_executive_materiality"
            disposition = "morning_radar_only"
        else:
            reason = "watch_bridge_failed_daily_source_quality"
            disposition = "morning_radar_only"
        row["daily_disposition"] = disposition
        row["temporal_distinction"] = temporal_reason or "published_within_daily_coverage"
        set_stage(
            row,
            STAGE_WATCH_BRIDGE,
            qualification_reason="important_watch_first_sent_after_editor_snapshot",
            rejection_reason=reason if disposition != "daily_reconsideration_eligible" else "",
        )
        if delivered_at is not None and first_sent > delivered_at:
            row["daily_disposition"] = "future_cycle_only"
            row["rejection_reason"] = "watch_sent_after_daily_delivery_no_retroactive_mutation"
            late_rows.append(row)
        elif first_sent <= finalization_at:
            bridge_rows.append(row)
        else:
            late_rows.append(row)
    return {
        "bridge_rows": bridge_rows[:MAX_PUBLIC_NEAR_MISSES],
        "bridge_count": len(bridge_rows),
        "bridge_delivery_ids": [
            str(row.get("watch_delivery_id") or "") for row in bridge_rows
        ],
        "late_rows": late_rows[:MAX_PUBLIC_NEAR_MISSES],
        "late_count": len(late_rows),
        "late_delivery_ids": [
            str(row.get("watch_delivery_id") or "") for row in late_rows
        ],
        "observable_count": len(bridge_rows),
        "delivery_side_effects": 0,
    }


def merge_watch_bridge(base_audit: object, bridge: Mapping[str, Any]) -> dict[str, Any]:
    output = normalize_audit(base_audit)
    rows = [
        sanitized
        for row in bridge.get("bridge_rows", [])
        if (sanitized := _sanitized_row(row)) is not None
    ]
    output["morning_bridge_rows"] = rows[:MAX_PUBLIC_NEAR_MISSES]
    output["late_watch_rows"] = [
        sanitized
        for row in bridge.get("late_rows", [])
        if (sanitized := _sanitized_row(row)) is not None
    ][:MAX_PUBLIC_NEAR_MISSES]
    output["funnel"]["watch_bridge_count"] = max(
        0, int(bridge.get("bridge_count") or 0)
    )
    output["funnel"]["late_watch_count"] = max(
        0, int(bridge.get("late_count") or 0)
    )
    output["morning_truth_absolute_empty_allowed"] = not bool(
        output["funnel"]["selected_count"] == 0
        and output["funnel"]["watch_bridge_count"] > 0
    )
    return output
