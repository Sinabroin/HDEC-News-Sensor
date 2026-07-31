"""Explainable, bounded editorial feedback and human-link learning profile."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse

PROFILE_VERSION = 2
MAX_ABS_ADJUSTMENT = 0.85
MANUAL_DOMAIN_SEED_CAP = 0.18
MANUAL_KEYWORD_SEED_CAP = 0.12
_WORD_RE = re.compile(r"[0-9A-Za-z가-힣]{2,}")
_STOPWORDS = {
    "관련", "대한", "위한", "통해", "이번", "주요", "발표", "확대", "추진",
    "전망", "시장", "기업", "기술", "산업", "경영", "뉴스",
    "ai", "the", "and", "for", "with",
}


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
    return sorted(words)[:12]


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
