"""Live-only executive surface source-priority policy.

This module is also the canonical publisher-priority authority for delivery
surfaces (Teams / Daily / Weekly): the operator-locked ordered primary ten and
secondary three live in ``data/source_priority_rules.json`` and are resolved by
:func:`publisher_delivery_tier`. Publisher authority (publisher-direct safety)
stays a separate concern — a publisher-direct article can be safe while still
being lower priority.
"""

from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

from app import config, source_quality

_RULES_PATH = config.DATA_DIR / "source_priority_rules.json"
_RANK = {
    "official": 0,
    "major": 1,
    "specialist": 2,
    "trusted_other": 2,
    "neutral": 3,
    "low": 4,
    "excluded": 5,
}

# One shared delivery-priority order (§ D7-AK-6E R4-R6). Same-event
# representative selection and within-importance ordering both use this order.
PUBLISHER_DELIVERY_TIER_ORDER = (
    "primary_10",
    "secondary_3",
    "official_institution",
    "specialist",
    "trusted_other",
    "neutral",
    "low",
    "excluded",
)
PUBLISHER_DELIVERY_TIER_RANK = {
    tier: index for index, tier in enumerate(PUBLISHER_DELIVERY_TIER_ORDER)
}


@lru_cache(maxsize=1)
def _rules() -> dict[str, Any]:
    try:
        loaded = json.loads(_RULES_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        loaded = {}
    return loaded if isinstance(loaded, dict) else {}


def _clean(value: object) -> str:
    return " ".join(str(value or "").split())


def _matches(source: str, patterns: Sequence[str]) -> bool:
    lowered = _clean(source).casefold()
    return any(_clean(pattern).casefold() in lowered for pattern in patterns if _clean(pattern))


def is_live(row: Mapping[str, Any]) -> bool:
    return "live" in _clean(row.get("signal_origin")).casefold()


def classify(source: str, title: str = "") -> dict[str, Any]:
    base = source_quality.classify(source, title)
    rules = _rules()
    quality = base.get("source_quality")
    source_type = base.get("source_type")

    if source_type == "institution":
        bucket = "official"
    elif quality == "trusted":
        if _matches(source, rules.get("major_source_patterns") or []):
            bucket = "major"
        elif _matches(source, rules.get("specialist_source_patterns") or []):
            bucket = "specialist"
        else:
            bucket = "trusted_other"
    elif quality == "low":
        bucket = "low"
    elif quality == "excluded":
        bucket = "excluded"
    else:
        bucket = "neutral"

    labels = rules.get("labels") or {}
    return {
        "source_priority_bucket": bucket,
        "source_priority_rank": _RANK[bucket],
        "source_priority_label": labels.get(bucket, bucket),
        "trusted_slot_eligible": bucket in {
            "official", "major", "specialist", "trusted_other"
        },
    }


@lru_cache(maxsize=1)
def _publisher_delivery_policies() -> tuple[tuple[str, int, str, tuple[str, ...], tuple[str, ...]], ...]:
    policies: list[tuple[str, int, str, tuple[str, ...], tuple[str, ...]]] = []
    for entry in _rules().get("publisher_delivery_policies") or []:
        if not isinstance(entry, Mapping):
            continue
        tier = _clean(entry.get("tier"))
        if tier not in {"primary_10", "secondary_3"}:
            continue
        policies.append((
            tier,
            int(entry.get("rank") or 0),
            _clean(entry.get("name")),
            tuple(_clean(alias) for alias in entry.get("aliases") or [] if _clean(alias)),
            tuple(_clean(domain).casefold() for domain in entry.get("domains") or [] if _clean(domain)),
        ))
    return tuple(policies)


def locked_publisher_names(tier: str) -> tuple[str, ...]:
    """Locked publisher display names for one tier, in configured rank order."""
    return tuple(
        name
        for policy_tier, _rank, name, _aliases, _domains in sorted(
            _publisher_delivery_policies(), key=lambda policy: policy[1]
        )
        if policy_tier == tier
    )


def _delivery_source_key(source: str) -> str:
    return re.sub(
        r"\s+", "", unicodedata.normalize("NFKC", str(source or "")).casefold()
    )


def _delivery_url_host(url: str) -> str:
    try:
        host = (urlsplit(str(url or "")).hostname or "").casefold()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def publisher_delivery_tier(source: str, selected_url: str = "") -> dict[str, Any]:
    """Canonical delivery-priority tier for one publisher.

    Tier order is :data:`PUBLISHER_DELIVERY_TIER_ORDER`; the locked primary ten
    and secondary three carry their configured ``publisher_rank`` (1-based),
    every other tier reports ``publisher_rank`` 0. ``sort_key`` sorts best-first.
    """
    quality = source_quality.classify(source)
    tier = ""
    publisher_rank = 0
    if quality.get("source_type") == "institution":
        tier = "official_institution"
    else:
        source_key = _delivery_source_key(source)
        host = _delivery_url_host(selected_url)
        for policy_tier, rank, _name, aliases, domains in _publisher_delivery_policies():
            alias_match = any(
                re.sub(r"\s+", "", alias.casefold()) in source_key
                for alias in aliases
            )
            domain_match = any(
                host == domain or host.endswith("." + domain)
                for domain in domains
            )
            if alias_match or domain_match:
                tier = policy_tier
                publisher_rank = rank
                break
    if not tier:
        source_quality_value = quality.get("source_quality")
        if source_quality_value == "excluded":
            tier = "excluded"
        elif source_quality_value == "low":
            tier = "low"
        elif source_quality_value == "trusted":
            rules = _rules()
            if _matches(source, rules.get("specialist_source_patterns") or []):
                tier = "specialist"
            else:
                tier = "trusted_other"
        else:
            tier = "neutral"
    tier_rank = PUBLISHER_DELIVERY_TIER_RANK[tier]
    labels = _rules().get("publisher_delivery_tier_labels") or {}
    return {
        "tier": tier,
        "tier_rank": tier_rank,
        "publisher_rank": publisher_rank,
        "label": labels.get(tier, tier),
        "sort_key": (tier_rank, publisher_rank),
    }


def effective_rank(row: Mapping[str, Any]) -> int:
    if not is_live(row):
        return 0
    return int(classify(row.get("source"), row.get("title"))["source_priority_rank"])


def trusted_slot_eligible(row: Mapping[str, Any]) -> bool:
    return is_live(row) and bool(
        classify(row.get("source"), row.get("title"))["trusted_slot_eligible"]
    )


def surface_minimum(surface: str, limit: int) -> int:
    configured = int(
        ((_rules().get("surface_trusted_minimums") or {}).get(surface) or 0)
    )
    return max(0, min(int(limit), configured))


def reserve_trusted_slots(
    rows: Sequence[Mapping[str, Any]],
    *,
    surface: str,
    limit: int,
) -> list:
    """Move enough trusted live candidates to the front without dropping rows."""
    ordered = list(rows)
    if not ordered or not any(is_live(row) for row in ordered):
        return ordered
    target = min(
        surface_minimum(surface, limit),
        sum(1 for row in ordered if trusted_slot_eligible(row)),
        int(limit),
    )
    if target <= 0:
        return ordered
    trusted = [row for row in ordered if trusted_slot_eligible(row)][:target]
    selected = {str(row.get("id") or id(row)) for row in trusted}
    return trusted + [
        row for row in ordered
        if str(row.get("id") or id(row)) not in selected
    ]
