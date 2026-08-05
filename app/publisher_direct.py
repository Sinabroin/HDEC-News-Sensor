"""Publisher-direct authority and delivery policy for executive news.

This module is deterministic and performs no network I/O. Collectors attach
publisher authority/provenance after bounded resolution; every delivery surface
uses the same eligibility decision before exposing an article URL.
"""

from __future__ import annotations

import ipaddress
import json
import re
from dataclasses import dataclass
from typing import Iterable, Mapping
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

PORTAL_HOSTS = (
    "news.daum.net",
    "v.daum.net",
    "media.daum.net",
    "news.naver.com",
    "n.news.naver.com",
    "m.news.naver.com",
    "news.google.com",
    "msn.com",
    "yahoo.com",
    "yahoo.co.jp",
    "yahoo.co.kr",
)
SEARCH_HOSTS = (
    "google.com",
    "bing.com",
    "search.naver.com",
    "search.daum.net",
)
SHORTENER_HOSTS = (
    "bit.ly",
    "t.co",
    "tinyurl.com",
    "goo.gl",
    "han.gl",
    "me2.do",
    "vo.la",
    "url.kr",
    "naver.me",
)
SECURITY_INTERMEDIARY_HOSTS = (
    "safelinks.protection.outlook.com",
    "teams.public.onecdn.static.microsoft",
)
INTERNAL_SUFFIXES = (
    ".internal",
    ".intranet",
    ".local",
    ".localhost",
    ".home",
    ".lan",
)
TRACKING_QUERY_KEYS = frozenset(
    {
        "fbclid",
        "gclid",
        "oc",
        "ref",
        "referrer",
        "source",
        "spm",
        "cmpid",
        "ncid",
        "output",
    }
)
QUARANTINE_STATUSES = frozenset(
    {
        "quarantine",
        "quarantined",
        "publisher_resolution_failed",
        "publisher_resolution_pending",
        "pending_verification",
    }
)

EXECUTIVE_SIGNALS = (
    "현대건설",
    "건설",
    "인프라",
    "데이터센터",
    "데이터 센터",
    "스마트건설",
    "로봇",
    "자동화",
    "디지털트윈",
    "디지털 트윈",
    "bim",
    "엔지니어링",
    "안전",
    "품질",
    "에너지",
    "원전",
    "smr",
    "플랜트",
    "생성형 ai",
    "인공지능",
    "ai ",
    " ai",
    "llm",
    "규제",
    "정책",
    "투자",
    "반도체",
    "gpu",
    "클라우드",
    "전력망",
    "송전",
    "수주",
    "epc",
)
LOW_VALUE_SIGNALS = (
    "주가 전망",
    "목표주가",
    "급등주",
    "테마주",
    "셀카",
    "연예",
    "게임 공략",
    "스마트폰 루머",
    "소비자 가십",
)


def _host_matches(host: str, candidates: Iterable[str]) -> bool:
    return any(host == item or host.endswith("." + item) for item in candidates)


def _host(value: object) -> str:
    try:
        return (urlparse(str(value or "")).hostname or "").casefold().rstrip(".")
    except ValueError:
        return ""


def portal_provider(value: object) -> str:
    """Return discovery provider name for known non-authority URLs."""
    host = _host(value)
    if not host:
        return ""
    if _host_matches(host, ("news.daum.net", "v.daum.net", "media.daum.net")):
        return "daum"
    if _host_matches(host, ("news.naver.com", "n.news.naver.com", "m.news.naver.com")):
        return "naver"
    if _host_matches(host, ("news.google.com",)):
        return "google_news"
    if _host_matches(host, ("msn.com",)):
        return "msn"
    if _host_matches(host, ("yahoo.com", "yahoo.co.jp", "yahoo.co.kr")):
        return "yahoo"
    if _host_matches(host, SEARCH_HOSTS):
        return "search"
    if _host_matches(host, SHORTENER_HOSTS):
        return "shortener"
    if _host_matches(host, SECURITY_INTERMEDIARY_HOSTS):
        return "security_intermediary"
    return ""


def normalize_publisher_canonical_url(value: object) -> str:
    """Return a stable publisher URL or ``""`` for unsafe/non-authority input.

    DNS and redirect safety belong to the network-owning collector. This helper
    deliberately performs only deterministic URL/host normalization.
    """
    raw = str(value or "").strip()
    if not raw or len(raw) > 2048 or any(ord(char) < 32 for char in raw):
        return ""
    try:
        parsed = urlparse(raw)
        port = parsed.port
    except (TypeError, ValueError):
        return ""
    scheme = parsed.scheme.casefold()
    host = (parsed.hostname or "").casefold().rstrip(".")
    if (
        scheme not in {"http", "https"}
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or (port is not None and port != (443 if scheme == "https" else 80))
        or host == "localhost"
        or "." not in host
        or any(host.endswith(suffix) for suffix in INTERNAL_SUFFIXES)
        or portal_provider(raw)
    ):
        return ""
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        return ""
    try:
        ascii_host = host.encode("idna").decode("ascii")
    except UnicodeError:
        return ""
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    query = [
        (key, item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_")
        and key.casefold() not in TRACKING_QUERY_KEYS
    ]
    # Standard ports are semantically identical to their implicit form and must
    # not create a second canonical/dedup identity.
    netloc = ascii_host
    return urlunparse(
        (scheme, netloc, path, parsed.params, urlencode(query, doseq=True), "")
    )


def unwrap_security_intermediary_url(value: object) -> str:
    """R4-R9B §3 — extract the destination from a security-intermediary wrapper.

    Audit/feedback-input helper only: a Microsoft Teams/Outlook SafeLinks
    wrapper is never a publisher and never canonical article identity.
    Returns the normalized publisher destination carried in the wrapper's
    ``url`` query parameter, or ``""`` when the input is not a known
    security-intermediary URL or carries no safe http(s) destination.
    Nested wrappers unwrap at most twice; anything deeper is rejected.
    """
    current = str(value or "").strip()
    for _ in range(2):
        if portal_provider(current) != "security_intermediary":
            return ""
        try:
            parsed = urlparse(current)
        except ValueError:
            return ""
        destinations = [
            item
            for key, item in parse_qsl(parsed.query, keep_blank_values=False)
            if key.casefold() == "url"
        ]
        if len(destinations) != 1:
            return ""
        current = destinations[0].strip()
        if portal_provider(current) == "security_intermediary":
            continue
        return normalize_publisher_canonical_url(current)
    return ""


def _metadata(article: Mapping) -> dict:
    value = article.get("source_metadata") or article.get("source_metadata_json") or {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            value = {}
    return dict(value) if isinstance(value, Mapping) else {}


def publisher_url(article: Mapping) -> str:
    """Choose only a publisher canonical URL; never return a portal fallback."""
    metadata = _metadata(article)
    for value in (
        article.get("publisher_url"),
        article.get("canonical_url"),
        article.get("external_url"),
        article.get("original_url"),
        metadata.get("publisher_url"),
        metadata.get("canonical_url"),
        metadata.get("originallink"),
        metadata.get("resolved_url"),
        metadata.get("source_url"),
        article.get("resolved_publisher_url"),
        article.get("url"),
    ):
        normalized = normalize_publisher_canonical_url(value)
        if normalized:
            return normalized
    return ""


def discovery_url(article: Mapping) -> str:
    metadata = _metadata(article)
    for value in (
        article.get("discovery_url"),
        metadata.get("discovery_url"),
        article.get("input_url"),
        article.get("url"),
    ):
        text = str(value or "").strip()
        if text.startswith(("http://", "https://")):
            return text
    return ""


@dataclass(frozen=True)
class RelevanceDecision:
    relevant: bool
    matched_signals: tuple[str, ...]
    reason: str


def executive_relevance(article: Mapping) -> RelevanceDecision:
    """Conservative pre-score relevance gate for live collection."""
    explicit = article.get("delivery_relevance_qualified")
    if isinstance(explicit, bool):
        return RelevanceDecision(
            explicit,
            ("explicit_existing_policy",) if explicit else (),
            "existing_relevance_policy_passed" if explicit else "existing_relevance_policy_failed",
        )
    text = " ".join(
        str(article.get(key) or "")
        for key in ("title", "snippet", "summary", "source")
    ).casefold()
    matched = tuple(dict.fromkeys(signal for signal in EXECUTIVE_SIGNALS if signal in text))
    low = tuple(signal for signal in LOW_VALUE_SIGNALS if signal in text)
    relevant = bool(matched) and not (low and len(matched) < 2)
    if relevant:
        reason = "matched_executive_relevance:" + ",".join(matched[:6])
    elif low:
        reason = "low_value_consumer_or_market_noise:" + ",".join(low[:3])
    else:
        reason = "no_ai_hdec_industry_relevance_signal"
    return RelevanceDecision(relevant, matched, reason)


@dataclass(frozen=True)
class DeliveryEligibility:
    eligible: bool
    publisher_url: str
    reason: str
    relevance: RelevanceDecision


def assess_delivery_eligibility(
    article: Mapping,
    *,
    relevance_qualified: bool | None = None,
) -> DeliveryEligibility:
    metadata = _metadata(article)
    direct_url = publisher_url(article)
    relevance = (
        RelevanceDecision(True, ("upstream_relevance_policy",), "upstream_relevance_policy_passed")
        if relevance_qualified is True
        else RelevanceDecision(False, (), "upstream_relevance_policy_failed")
        if relevance_qualified is False
        else executive_relevance(article)
    )
    status = str(
        article.get("portal_resolution_status")
        or metadata.get("portal_resolution_status")
        or article.get("status")
        or ""
    ).casefold()
    if bool(article.get("quarantine")) or status in QUARANTINE_STATUSES:
        return DeliveryEligibility(False, direct_url, "article_is_quarantined", relevance)
    direct_flag = article.get("publisher_direct")
    if direct_flag is None:
        direct_flag = metadata.get("publisher_direct")
    if direct_flag is not True:
        return DeliveryEligibility(False, direct_url, "publisher_direct_not_verified", relevance)
    if not direct_url:
        return DeliveryEligibility(False, "", "publisher_canonical_url_missing", relevance)
    if not str(article.get("title") or "").strip():
        return DeliveryEligibility(False, direct_url, "title_missing", relevance)
    if not str(article.get("source") or article.get("display_source") or "").strip():
        return DeliveryEligibility(False, direct_url, "source_missing", relevance)
    if not (
        str(article.get("published_at") or article.get("published_kst") or "").strip()
        or str(metadata.get("published_at_fallback_reason") or "").strip()
        or str(article.get("published_at_fallback_reason") or "").strip()
    ):
        return DeliveryEligibility(False, direct_url, "published_at_and_fallback_missing", relevance)
    if not relevance.relevant:
        return DeliveryEligibility(False, direct_url, relevance.reason, relevance)
    return DeliveryEligibility(True, direct_url, "publisher_direct_delivery_eligible", relevance)


def is_publisher_direct_delivery_eligible(
    article: Mapping,
    *,
    relevance_qualified: bool | None = None,
) -> bool:
    return assess_delivery_eligibility(
        article,
        relevance_qualified=relevance_qualified,
    ).eligible


def apply_publisher_authority(
    article: Mapping,
    *,
    publisher_canonical_url: str,
    source: str,
    published_at: str | None,
    resolution_reason: str,
) -> dict:
    """Return an article whose delivery URL is the verified publisher canonical."""
    normalized = normalize_publisher_canonical_url(publisher_canonical_url)
    if not normalized:
        return quarantine_article(article, "resolved_url_is_not_publisher_direct")
    output = dict(article)
    metadata = _metadata(output)
    raw_discovery = discovery_url(output)
    provider = (
        str(metadata.get("discovery_provider") or "").strip()
        or portal_provider(raw_discovery)
        or str(metadata.get("provider") or "").strip()
        or "publisher_direct"
    )
    metadata.update(
        {
            "discovery_url": raw_discovery or normalized,
            "discovery_provider": provider,
            "publisher_url": normalized,
            "publisher_domain": _host(normalized),
            "publisher_direct": True,
            "portal_resolution_status": "resolved",
            "portal_resolution_reason": resolution_reason,
        }
    )
    output.update(
        {
            "url": normalized,
            "canonical_url": normalized,
            "publisher_url": normalized,
            "publisher_domain": _host(normalized),
            "publisher_direct": True,
            "discovery_url": raw_discovery or normalized,
            "discovery_provider": provider,
            "portal_resolution_status": "resolved",
            "portal_resolution_reason": resolution_reason,
            "quarantine": False,
            "status": "collected",
            "source": str(source or output.get("source") or "").strip(),
            "source_metadata": metadata,
        }
    )
    if published_at:
        output["published_at"] = str(published_at)
    else:
        metadata["published_at_fallback_reason"] = "publisher_page_date_unavailable"
    return output


def quarantine_article(article: Mapping, reason: str) -> dict:
    """Preserve discovery provenance without making it delivery eligible."""
    output = dict(article)
    metadata = _metadata(output)
    raw_discovery = discovery_url(output)
    provider = (
        str(metadata.get("discovery_provider") or "").strip()
        or portal_provider(raw_discovery)
        or str(metadata.get("provider") or "").strip()
        or "unknown"
    )
    metadata.update(
        {
            "discovery_url": raw_discovery,
            "discovery_provider": provider,
            "publisher_url": "",
            "publisher_domain": "",
            "publisher_direct": False,
            "portal_resolution_status": "quarantined",
            "portal_resolution_reason": str(reason or "publisher_resolution_failed"),
        }
    )
    output.update(
        {
            "discovery_url": raw_discovery,
            "discovery_provider": provider,
            "publisher_url": "",
            "publisher_domain": "",
            "publisher_direct": False,
            "portal_resolution_status": "quarantined",
            "portal_resolution_reason": str(reason or "publisher_resolution_failed"),
            "quarantine": True,
            "status": "quarantine",
            "source_metadata": metadata,
        }
    )
    return output


def partition_delivery_articles(
    articles: Iterable[Mapping],
    *,
    relevance_qualified: bool | None = None,
) -> tuple[list[dict], list[dict]]:
    """Split and canonical-deduplicate delivery rows from quarantine rows."""
    eligible: list[dict] = []
    quarantined: list[dict] = []
    by_canonical: dict[str, int] = {}
    for article in articles:
        row = dict(article)
        assessment = assess_delivery_eligibility(
            row,
            relevance_qualified=relevance_qualified,
        )
        if not assessment.eligible:
            if not row.get("quarantine"):
                row = quarantine_article(row, assessment.reason)
            quarantined.append(row)
            continue
        canonical = assessment.publisher_url.casefold().rstrip("/")
        row["url"] = assessment.publisher_url
        row["canonical_url"] = assessment.publisher_url
        row["publisher_url"] = assessment.publisher_url
        row["publisher_direct"] = True
        if canonical in by_canonical:
            existing = eligible[by_canonical[canonical]]
            existing_metadata = _metadata(existing)
            incoming_metadata = _metadata(row)
            providers = {
                token
                for value in (
                    existing_metadata.get("provider"),
                    incoming_metadata.get("provider"),
                )
                for token in str(value or "").split("+")
                if token
            }
            if providers:
                existing_metadata["provider"] = "+".join(sorted(providers))
            existing_discoveries = existing_metadata.get("discovery_urls")
            incoming_discoveries = incoming_metadata.get("discovery_urls")
            discoveries = [
                value
                for value in (
                    *(
                        existing_discoveries
                        if isinstance(existing_discoveries, list)
                        else []
                    ),
                    *(
                        incoming_discoveries
                        if isinstance(incoming_discoveries, list)
                        else []
                    ),
                    existing_metadata.get("discovery_url"),
                    incoming_metadata.get("discovery_url"),
                    discovery_url(existing),
                    discovery_url(row),
                )
                if value
            ]
            if discoveries:
                unique_discoveries = list(dict.fromkeys(discoveries))
                existing_metadata["discovery_url"] = unique_discoveries[0]
                existing_metadata["discovery_urls"] = unique_discoveries
            existing["source_metadata"] = existing_metadata
            continue
        by_canonical[canonical] = len(eligible)
        eligible.append(row)
    return eligible, quarantined


def count_portal_urls(value: object) -> int:
    """Recursively count portal/search/shortener URLs in a payload."""
    if isinstance(value, Mapping):
        return sum(count_portal_urls(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return sum(count_portal_urls(item) for item in value)
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        return int(bool(portal_provider(value)))
    return 0


__all__ = [
    "DeliveryEligibility",
    "RelevanceDecision",
    "apply_publisher_authority",
    "assess_delivery_eligibility",
    "count_portal_urls",
    "discovery_url",
    "executive_relevance",
    "is_publisher_direct_delivery_eligible",
    "normalize_publisher_canonical_url",
    "partition_delivery_articles",
    "portal_provider",
    "publisher_url",
    "quarantine_article",
]
