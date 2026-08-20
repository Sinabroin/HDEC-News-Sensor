"""Explainable, bounded editorial feedback and human-link learning profile."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse, urlunparse

PROFILE_VERSION = 2
HUMAN_EXEMPLAR_VERSION = 1
HUMAN_EXEMPLAR_PREFIX = "exemplar-"
HUMAN_EXEMPLAR_MAX_KEYWORDS = 12
MAX_ABS_ADJUSTMENT = 0.85
MANUAL_DOMAIN_SEED_CAP = 0.18
MANUAL_KEYWORD_SEED_CAP = 0.12
_WORD_RE = re.compile(r"[0-9A-Za-z가-힣]{2,}")
_STOPWORDS = {
    "관련", "대한", "위한", "통해", "이번", "주요", "발표", "확대", "추진",
    "전망", "시장", "기업", "기술", "산업", "경영", "뉴스",
    "ai", "the", "and", "for", "with",
}
_NON_PUBLISHER_DOMAINS = frozenset(
    {
        "news.daum.net",
        "v.daum.net",
        "media.daum.net",
        "news.naver.com",
        "n.news.naver.com",
        "m.news.naver.com",
        "news.google.com",
        "msn.com",
        "news.yahoo.com",
        "bit.ly",
        "t.co",
        "tinyurl.com",
        "goo.gl",
        "han.gl",
        "me2.do",
        "vo.la",
        "url.kr",
        "teams.public.onecdn.static.microsoft",
        "safelinks.protection.outlook.com",
    }
)


def _key(value: object) -> str:
    return " ".join(str(value or "").casefold().split())


def _domain(value: object) -> str:
    try:
        host = (urlparse(str(value or "")).hostname or "").casefold().rstrip(".")
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def _keywords(value: object) -> list[str]:
    words = {
        item.casefold()
        for item in _WORD_RE.findall(str(value or ""))
        if item.casefold() not in _STOPWORDS and not item.isdigit()
    }
    return sorted(words)[:HUMAN_EXEMPLAR_MAX_KEYWORDS]


def _non_publisher_domain(domain: str) -> bool:
    return bool(
        any(domain == item or domain.endswith("." + item) for item in _NON_PUBLISHER_DOMAINS)
        or domain.endswith(".onecdn.static.microsoft")
        or domain.endswith(".safelinks.protection.outlook.com")
    )


def canonical_learning_url(value: object) -> str:
    """Canonical publisher URL eligible for bounded human learning, or ``""``.

    Durable review validation owns the SSRF/literal-IP boundary.  This second,
    deterministic pass strips fragments/default ports and rejects known portal,
    shortener, search, and Microsoft wrapper authorities so those hosts can
    never become preferred news domains.
    """
    raw = str(value or "").strip()
    try:
        parsed = urlparse(raw)
        port = parsed.port
    except (TypeError, ValueError):
        return ""
    if (
        parsed.scheme.casefold() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        return ""
    domain = parsed.hostname.casefold().rstrip(".").removeprefix("www.")
    if (
        not domain
        or "." not in domain
        or _non_publisher_domain(domain)
        or any(
            domain.endswith(suffix)
            for suffix in (".internal", ".intranet", ".local", ".localhost", ".home", ".lan")
        )
    ):
        return ""
    try:
        ipaddress.ip_address(domain)
    except ValueError:
        pass
    else:
        return ""
    if "/search" in parsed.path.casefold():
        return ""
    scheme = parsed.scheme.casefold()
    default_port = 443 if scheme == "https" else 80
    if port is not None and port != default_port:
        return ""
    netloc = parsed.hostname.casefold().rstrip(".")
    return urlunparse(
        (scheme, netloc, parsed.path or "/", parsed.params, parsed.query, "")
    )


def _exemplar_identity(
    *, edition_key: str, review_snapshot_id: str, canonical_url: str
) -> str:
    stable = "\x1f".join((edition_key, review_snapshot_id, canonical_url))
    return HUMAN_EXEMPLAR_PREFIX + hashlib.sha256(stable.encode("utf-8")).hexdigest()


def confirmed_human_exemplars(approved_review: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Derive unique confirmed positive exemplars from one approved review.

    Draft/local/abandoned items cannot enter: the record must explicitly be an
    approved Daily review and each item must be selected ``origin=human_link``.
    The exemplar intentionally contains no body, headers, cookies, or secrets.
    """
    if (
        str(approved_review.get("product") or "") != "daily"
        or str(approved_review.get("review_status") or "") != "approved"
    ):
        return []
    edition_key = str(approved_review.get("edition_key") or "")
    snapshot_id = str(approved_review.get("review_snapshot_id") or "")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", edition_key):
        return []
    if not re.fullmatch(
        rf"review-{re.escape(edition_key)}-[0-9a-f]{{16}}", snapshot_id
    ):
        return []
    items = approved_review.get("selected_items")
    if not isinstance(items, list):
        return []
    output: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, Mapping) or str(item.get("origin") or "") != "human_link":
            continue
        canonical_url = canonical_learning_url(item.get("selected_url"))
        domain = _domain(canonical_url)
        source = str(item.get("source") or "").strip()[:160]
        category = str(item.get("category") or "").strip()[:80]
        title = str(item.get("title") or "").strip()[:500]
        if not canonical_url or not domain or not source or not category or not title:
            continue
        exemplar_id = _exemplar_identity(
            edition_key=edition_key,
            review_snapshot_id=snapshot_id,
            canonical_url=canonical_url,
        )
        output.setdefault(
            exemplar_id,
            {
                "version": HUMAN_EXEMPLAR_VERSION,
                "exemplar_id": exemplar_id,
                "edition_key": edition_key,
                "review_snapshot_id": snapshot_id,
                "canonical_publisher_url": canonical_url,
                "publisher_domain": domain,
                "source": source,
                "category": category,
                "title": title,
                "topic_signals": _keywords(title),
                "provenance": "human_link",
                "selected": True,
                "approved": True,
            },
        )
    return [output[key] for key in sorted(output)]


def valid_human_exemplar(value: object) -> bool:
    """Validate the exact safe, bounded persisted exemplar contract."""
    if not isinstance(value, Mapping):
        return False
    allowed = {
        "version",
        "exemplar_id",
        "edition_key",
        "review_snapshot_id",
        "canonical_publisher_url",
        "publisher_domain",
        "source",
        "category",
        "title",
        "topic_signals",
        "provenance",
        "selected",
        "approved",
    }
    if set(value) != allowed:
        return False
    rebuilt = confirmed_human_exemplars(
        {
            "product": "daily",
            "review_status": "approved",
            "edition_key": value.get("edition_key"),
            "review_snapshot_id": value.get("review_snapshot_id"),
            "selected_items": [
                {
                    "origin": "human_link",
                    "selected_url": value.get("canonical_publisher_url"),
                    "source": value.get("source"),
                    "category": value.get("category"),
                    "title": value.get("title"),
                }
            ],
        }
    )
    return bool(
        len(rebuilt) == 1
        and dict(value) == rebuilt[0]
        and value.get("version") == HUMAN_EXEMPLAR_VERSION
        and value.get("publisher_domain") == _domain(value.get("canonical_publisher_url"))
        and isinstance(value.get("topic_signals"), list)
        and len(value.get("topic_signals") or []) <= HUMAN_EXEMPLAR_MAX_KEYWORDS
        and value.get("provenance") == "human_link"
        and value.get("selected") is True
        and value.get("approved") is True
    )


def compile_profile_from_exemplars(
    exemplars: Iterable[Mapping[str, Any]],
    *,
    minimum_samples: int = 3,
) -> dict[str, Any]:
    """Rebuild the active bounded profile deterministically from the corpus."""
    records = []
    seen: set[str] = set()
    for exemplar in exemplars:
        if not valid_human_exemplar(exemplar):
            raise ValueError("invalid human exemplar")
        exemplar_id = str(exemplar["exemplar_id"])
        if exemplar_id in seen:
            continue
        seen.add(exemplar_id)
        records.append(
            {
                "origin": "human_link",
                "selected": True,
                "selected_url": exemplar["canonical_publisher_url"],
                "source": exemplar["source"],
                "category": exemplar["category"],
                "title": exemplar["title"],
            }
        )
    return compile_profile(records, minimum_samples=max(1, int(minimum_samples)))


def empty_profile() -> dict[str, Any]:
    return {
        "version": PROFILE_VERSION,
        "minimum_samples": 3,
        "max_abs_adjustment": MAX_ABS_ADJUSTMENT,
        "source_adjustments": {},
        "category_adjustments": {},
        "domain_adjustments": {},
        "keyword_adjustments": {},
        "manual_domain_seeds": {},
        "manual_keyword_seeds": {},
        "sample_counts": {
            "source": {}, "category": {}, "domain": {}, "keyword": {},
            "manual_domain": {}, "manual_keyword": {},
        },
    }


def load_profile(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return empty_profile()
    if not isinstance(value, dict) or value.get("version") != PROFILE_VERSION:
        return empty_profile()
    return value


COLLECTION_QUERY_MINIMUM_SAMPLES = 3
COLLECTION_QUERY_LIMIT = 6


def collection_queries(profile: Mapping[str, Any]) -> list[str]:
    """Return bounded editorial-only supplemental queries from repeated human links.

    One-off links affect ranking through manual seeds, but query expansion requires
    at least three approved human selections for the same domain or keyword.
    """
    counts = profile.get("sample_counts") or {}
    domain_counts = counts.get("manual_domain") or {}
    keyword_counts = counts.get("manual_keyword") or {}
    queries: list[str] = []

    ranked_domains = sorted(
        (
            (str(domain), int(count))
            for domain, count in domain_counts.items()
            if str(domain) and int(count) >= COLLECTION_QUERY_MINIMUM_SAMPLES
        ),
        key=lambda item: (-item[1], item[0]),
    )
    for domain, _count in ranked_domains:
        if re.fullmatch(r"[a-z0-9.-]+", domain) and "." in domain:
            queries.append(f"site:{domain} AI")
        if len(queries) >= COLLECTION_QUERY_LIMIT:
            return queries

    ranked_keywords = sorted(
        (
            (str(keyword), int(count))
            for keyword, count in keyword_counts.items()
            if str(keyword) and int(count) >= COLLECTION_QUERY_MINIMUM_SAMPLES
        ),
        key=lambda item: (-item[1], item[0]),
    )
    for keyword, _count in ranked_keywords:
        cleaned = " ".join(keyword.split())
        if (
            2 <= len(cleaned) <= 30
            and cleaned.casefold() not in _STOPWORDS
            and re.fullmatch(r"[0-9A-Za-z가-힣 .&+_-]+", cleaned)
        ):
            query = f"AI {cleaned}"
            if query not in queries:
                queries.append(query)
        if len(queries) >= COLLECTION_QUERY_LIMIT:
            break
    return queries


def adjustment(candidate: Mapping[str, Any], profile: Mapping[str, Any]) -> float:
    source = _key(candidate.get("source"))
    category = _key(candidate.get("category"))
    domain = _domain(candidate.get("selected_url"))
    title_keywords = _keywords(candidate.get("title"))

    total = float((profile.get("source_adjustments") or {}).get(source) or 0.0)
    total += float((profile.get("category_adjustments") or {}).get(category) or 0.0)
    total += float((profile.get("domain_adjustments") or {}).get(domain) or 0.0)
    total += float((profile.get("manual_domain_seeds") or {}).get(domain) or 0.0)

    keyword_values = [
        float((profile.get("keyword_adjustments") or {}).get(word) or 0.0)
        + float((profile.get("manual_keyword_seeds") or {}).get(word) or 0.0)
        for word in title_keywords
    ]
    if keyword_values:
        total += min(0.3, sum(sorted(keyword_values, reverse=True)[:3]))

    cap = float(profile.get("max_abs_adjustment") or MAX_ABS_ADJUSTMENT)
    return round(max(-cap, min(cap, total)), 4)


def _record_score(record: Mapping[str, Any]) -> float | None:
    selected = bool(record.get("selected"))
    origin = str(record.get("origin") or "")
    try:
        overall = int(record.get("overall_rating"))
    except (TypeError, ValueError):
        overall = 0

    if 1 <= overall <= 5:
        tags = record.get("exclusion_tags") or []
        penalty = min(0.5, 0.1 * len(tags)) if isinstance(tags, list) else 0.0
        return (overall - 3) * 0.22 + (0.12 if selected else -0.04) - penalty

    if origin == "human_link" and selected:
        return 0.45
    return None


def compile_profile(
    records: Iterable[Mapping[str, Any]],
    *,
    minimum_samples: int = 3,
    max_abs_adjustment: float = MAX_ABS_ADJUSTMENT,
) -> dict[str, Any]:
    source_scores: dict[str, list[float]] = defaultdict(list)
    category_scores: dict[str, list[float]] = defaultdict(list)
    domain_scores: dict[str, list[float]] = defaultdict(list)
    keyword_scores: dict[str, list[float]] = defaultdict(list)
    manual_domain_counts: dict[str, int] = defaultdict(int)
    manual_keyword_counts: dict[str, int] = defaultdict(int)

    for record in records:
        score = _record_score(record)
        if score is None:
            continue
        source = _key(record.get("source"))
        category = _key(record.get("category"))
        domain = _domain(record.get("selected_url"))
        words = _keywords(record.get("title"))
        if source:
            source_scores[source].append(score)
        if category:
            category_scores[category].append(score)
        if domain:
            domain_scores[domain].append(score)
        for word in words:
            keyword_scores[word].append(score)

        if record.get("origin") == "human_link" and bool(record.get("selected")):
            if domain:
                manual_domain_counts[domain] += 1
            for word in words:
                manual_keyword_counts[word] += 1

    def compile_bucket(values):
        out = {}
        counts = {}
        for key, scores in sorted(values.items()):
            counts[key] = len(scores)
            if len(scores) >= minimum_samples:
                out[key] = round(
                    max(-max_abs_adjustment, min(max_abs_adjustment, mean(scores))),
                    4,
                )
        return out, counts

    source_adjustments, source_counts = compile_bucket(source_scores)
    category_adjustments, category_counts = compile_bucket(category_scores)
    domain_adjustments, domain_counts = compile_bucket(domain_scores)
    keyword_adjustments, keyword_counts = compile_bucket(keyword_scores)
    manual_domain_seeds = {
        key: round(min(MANUAL_DOMAIN_SEED_CAP, 0.06 * count), 4)
        for key, count in sorted(manual_domain_counts.items())
    }
    manual_keyword_seeds = {
        key: round(min(MANUAL_KEYWORD_SEED_CAP, 0.04 * count), 4)
        for key, count in sorted(manual_keyword_counts.items())
    }

    return {
        "version": PROFILE_VERSION,
        "minimum_samples": int(minimum_samples),
        "max_abs_adjustment": float(max_abs_adjustment),
        "source_adjustments": source_adjustments,
        "category_adjustments": category_adjustments,
        "domain_adjustments": domain_adjustments,
        "keyword_adjustments": keyword_adjustments,
        "manual_domain_seeds": manual_domain_seeds,
        "manual_keyword_seeds": manual_keyword_seeds,
        "sample_counts": {
            "source": source_counts,
            "category": category_counts,
            "domain": domain_counts,
            "keyword": keyword_counts,
            "manual_domain": dict(sorted(manual_domain_counts.items())),
            "manual_keyword": dict(sorted(manual_keyword_counts.items())),
        },
    }
