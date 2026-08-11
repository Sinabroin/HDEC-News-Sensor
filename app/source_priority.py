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
    "major_secondary",
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
        if tier not in {"primary_10", "secondary_3", "major_secondary"}:
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


_LOCKED_DOMAIN_MAP_TIERS = frozenset(
    {"primary_10", "secondary_3", "major_secondary"}
)


def locked_publisher_domain_map(
    tiers: Sequence[str] = ("primary_10", "secondary_3"),
) -> dict[str, str]:
    """Return ``{normalized domain: canonical publisher name}`` for the locked tiers.

    ``data/source_priority_rules.json`` (via :func:`_publisher_delivery_policies`)
    is the single source of truth: this reads the operator-locked primary-ten /
    secondary-three policy and never a duplicated publisher list. Only the
    ``primary_10`` and ``secondary_3`` tiers are addressable — any other tier is
    rejected — and the result is a freshly built dict so a caller can never
    mutate the cached policy through it. Requesting ``("primary_10",)`` yields
    every domain of the ten primary publishers; requesting both tiers yields the
    full thirteen-publisher policy.
    """
    requested = (tiers,) if isinstance(tiers, str) else tuple(tiers)
    if not requested:
        raise ValueError("at least one tier is required")
    wanted: set[str] = set()
    for tier in requested:
        cleaned = _clean(tier)
        if cleaned not in _LOCKED_DOMAIN_MAP_TIERS:
            raise ValueError(f"unsupported tier: {tier!r}")
        wanted.add(cleaned)
    mapping: dict[str, str] = {}
    for policy_tier, _rank, name, _aliases, domains in _publisher_delivery_policies():
        if policy_tier not in wanted:
            continue
        for domain in domains:
            normalized = _clean(domain).casefold()
            if normalized.startswith("www."):
                normalized = normalized[4:]
            if normalized:
                mapping[normalized] = name
    return mapping


def _delivery_source_key(source: str) -> str:
    return re.sub(
        r"\s+", "", unicodedata.normalize("NFKC", str(source or "")).casefold()
    )


def _delivery_url_host(url: str) -> str:
    try:
        host = (urlsplit(str(url or "")).hostname or "").casefold().rstrip(".")
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def _normalized_policy_domain(domain: object) -> str:
    value = _clean(domain).casefold().strip(".")
    return value[4:] if value.startswith("www.") else value


def _exact_domain_match(host: str, domains: Sequence[str]) -> bool:
    """Publisher-domain authority is exact by default.

    ``www`` is treated as the conventional alias of the apex. Every other
    subdomain must be enumerated in policy. This deliberately prevents sibling
    publications such as ``it.chosun.com`` / ``biz.chosun.com`` /
    ``sports.chosun.com`` from inheriting the authority of ``chosun.com``.
    """
    return bool(host) and any(
        host == _normalized_policy_domain(domain)
        for domain in domains
        if _normalized_policy_domain(domain)
    )


@lru_cache(maxsize=1)
def _authoritative_publisher_identities() -> tuple[
    tuple[str, int, str, tuple[str, ...], tuple[str, ...], str, str], ...
]:
    """Return every explicitly configured publication identity.

    URL authority is exact-host-only.  The table is derived from the shared
    Tier-A/B delivery policy plus the explicit Teams specialist and
    never-automatic publication entries.  It is intentionally not a registrable
    domain/suffix map: an unlisted child never inherits its parent publication.
    """
    identities: list[
        tuple[str, int, str, tuple[str, ...], tuple[str, ...], str, str]
    ] = []
    locked_names: set[str] = set()
    for tier, rank, name, aliases, domains in _publisher_delivery_policies():
        identities.append((
            tier, rank, name,
            tuple(_delivery_source_key(alias) for alias in aliases),
            tuple(_normalized_policy_domain(domain) for domain in domains),
            "locked_delivery", "hard_news",
        ))
        locked_names.add(name)
    for (
        kind, name, aliases, domains, operator_surface
    ) in _teams_source_policy_entries():
        # Tier-B entries deliberately repeat the canonical delivery table. Keep
        # only one identity row while retaining their Teams-lane metadata in
        # _teams_source_policy_entries().
        if name in locked_names:
            continue
        if kind in {"specialist", "fallback_blocked"}:
            tier = "specialist"
        else:
            tier = "neutral"
        identities.append((
            tier, 0, name, aliases,
            tuple(_normalized_policy_domain(domain) for domain in domains),
            kind, operator_surface or "publication",
        ))
    return tuple(identities)


def _identity_for_host(
    host: str,
) -> tuple[str, int, str, tuple[str, ...], tuple[str, ...], str, str] | None:
    if not host:
        return None
    matches = [
        identity
        for identity in _authoritative_publisher_identities()
        if host in identity[4]
    ]
    if len(matches) > 1:
        names = ", ".join(identity[2] for identity in matches)
        raise ValueError(f"conflicting authoritative publisher host {host!r}: {names}")
    return matches[0] if matches else None


def _identity_for_alias(
    source_key: str,
) -> tuple[str, int, str, tuple[str, ...], tuple[str, ...], str, str] | None:
    if not source_key:
        return None
    matches = [
        identity
        for identity in _authoritative_publisher_identities()
        if source_key in identity[3]
    ]
    if len(matches) > 1:
        names = ", ".join(identity[2] for identity in matches)
        raise ValueError(f"conflicting authoritative publisher alias {source_key!r}: {names}")
    return matches[0] if matches else None


def publisher_delivery_tier(source: str, selected_url: str = "") -> dict[str, Any]:
    """Canonical delivery-priority tier for one publisher.

    Tier order is :data:`PUBLISHER_DELIVERY_TIER_ORDER`; the locked primary ten
    and secondary three carry their configured ``publisher_rank`` (1-based),
    every other tier reports ``publisher_rank`` 0. ``sort_key`` sorts best-first.

    Resolution contract: an exact selected-URL host in the configured identity
    table is authoritative and wins over the display alias. With no URL host,
    an exact configured alias may identify the publication. If a configured
    alias is paired with an unknown host, the pair fails closed as unlisted;
    neither an unrelated host nor an unenumerated child inherits authority.
    """
    quality = source_quality.classify(source)
    tier = ""
    publisher_rank = 0
    publisher_identity = ""
    identity_evidence = ""
    operator_surface = ""
    source_key = _delivery_source_key(source)
    host = _delivery_url_host(selected_url)
    identity = _identity_for_host(host) if host else _identity_for_alias(source_key)
    if identity is not None:
        (
            tier, publisher_rank, publisher_identity,
            _aliases, _domains, _kind, operator_surface,
        ) = identity
        identity_evidence = "exact_domain" if host else "exact_alias"
    elif host and _identity_for_alias(source_key) is not None:
        # A URL was selected, but it does not resolve to any explicitly known
        # publication identity. Do not let a display alias confer authority on
        # an unknown foreign host or unenumerated sibling property.
        tier = "neutral"
        publisher_identity = host
        identity_evidence = "unrecognized_url_host"
        operator_surface = "unlisted"
    elif quality.get("source_type") == "institution":
        tier = "official_institution"
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
        "publisher_identity": publisher_identity or _clean(source),
        "identity_evidence": identity_evidence or "source_quality_fallback",
        "operator_surface": operator_surface or "publication",
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


# ---------------------------------------------------------------------------
# D7-AK-6E R4-R9A — Teams-only delivery source-gate lanes.
#
# For the Teams push surface, source priority is an ELIGIBILITY input, not only
# a sort order: the locked primary ten / secondary three are immediately
# sendable, a verified official institution stays subject to the caller-owned
# material-promotion decision, specialist/trusted-other publishers enter a
# holdback lane, and neutral/low/excluded publishers are never automatically
# sendable. Explicit per-publisher Teams policy (specialist membership and
# fallback blocking) lives in ``data/source_priority_rules.json`` under
# ``teams_delivery_source_policy``. These lanes apply to Teams delivery only —
# Daily / Weekly / News-Censor / operator-review eligibility never reads them.

TEAMS_LANE_IMMEDIATE_MAJOR = "immediate_major"
TEAMS_LANE_OFFICIAL_INSTITUTION = "official_institution_review"
TEAMS_LANE_SPECIALIST_HOLDBACK = "specialist_holdback"
TEAMS_LANE_NEVER_AUTOMATIC = "never_automatic"

TEAMS_IMMEDIATE_TIERS = frozenset({"primary_10", "secondary_3"})
TEAMS_SPECIALIST_LANE_TIERS = frozenset({"specialist", "trusted_other"})


@lru_cache(maxsize=1)
def _teams_source_policy_entries() -> tuple[
    tuple[str, str, tuple[str, ...], tuple[str, ...], str], ...
]:
    policy = _rules().get("teams_delivery_source_policy")
    if not isinstance(policy, Mapping):
        return ()
    entries: list[tuple[str, str, tuple[str, ...], tuple[str, ...], str]] = []
    for kind, key in (
        ("major_secondary", "major_secondary_publishers"),
        ("specialist", "specialist_publishers"),
        ("fallback_blocked", "fallback_blocked_publishers"),
        ("never_automatic", "never_automatic_publishers"),
    ):
        for entry in policy.get(key) or []:
            if not isinstance(entry, Mapping):
                continue
            entries.append((
                kind,
                _clean(entry.get("name")),
                tuple(
                    re.sub(r"\s+", "", _clean(alias).casefold())
                    for alias in entry.get("aliases") or []
                    if _clean(alias)
                ),
                tuple(
                    _clean(domain).casefold()
                    for domain in entry.get("domains") or []
                    if _clean(domain)
                ),
                _clean(entry.get("operator_surface")),
            ))
    return tuple(entries)


def teams_delivery_source_policy(source: str, selected_url: str = "") -> dict[str, Any]:
    """Teams-only source-gate lane for one publisher.

    Returns the canonical delivery tier plus the Teams lane, whether the
    publisher is an explicitly configured specialist, and whether automatic
    specialist fallback is blocked for it. A fallback-blocked publisher can
    never resolve to the immediate lane regardless of tier.
    """
    tier_info = publisher_delivery_tier(source, selected_url)
    source_key = _delivery_source_key(source)
    host = _delivery_url_host(selected_url)
    explicit_major_secondary = ""
    explicit_specialist = ""
    explicit_never_automatic = ""
    fallback_blocked = False
    operator_surface = str(tier_info.get("operator_surface") or "publication")
    for (
        kind, name, aliases, domains, configured_surface
    ) in _teams_source_policy_entries():
        # Once a URL host is present, only that exact host has identity authority.
        # Alias matching remains available solely for URL-less legacy records.
        alias_match = not host and any(source_key == alias for alias in aliases)
        domain_match = _exact_domain_match(host, domains)
        if not (alias_match or domain_match):
            continue
        if configured_surface:
            operator_surface = configured_surface
        if kind == "never_automatic":
            explicit_never_automatic = explicit_never_automatic or name
            continue
        if kind == "major_secondary":
            # Tier B is an explicit allowlist. It is immediate only after all
            # article-level substantive gates have passed in teams_ai_push.
            explicit_major_secondary = explicit_major_secondary or name
            continue
        explicit_specialist = explicit_specialist or name
        if kind == "fallback_blocked":
            fallback_blocked = True
    tier = str(tier_info["tier"])
    # R4-R10: an explicitly excluded publisher (e.g. S저널 / s-journal.co.kr,
    # traced from a real production auto-send) is pinned to never_automatic
    # ahead of every other lane — it can never resolve to immediate/major,
    # official, or specialist regardless of tier, alias, or domain drift.
    if explicit_never_automatic:
        lane = TEAMS_LANE_NEVER_AUTOMATIC
    elif fallback_blocked:
        lane = TEAMS_LANE_SPECIALIST_HOLDBACK
    elif tier in TEAMS_IMMEDIATE_TIERS or explicit_major_secondary or tier == "major_secondary":
        lane = TEAMS_LANE_IMMEDIATE_MAJOR
    elif tier == "official_institution":
        lane = TEAMS_LANE_OFFICIAL_INSTITUTION
    elif explicit_specialist or tier in TEAMS_SPECIALIST_LANE_TIERS:
        lane = TEAMS_LANE_SPECIALIST_HOLDBACK
    else:
        lane = TEAMS_LANE_NEVER_AUTOMATIC
    return {
        **tier_info,
        "teams_lane": lane,
        "explicit_specialist": explicit_specialist,
        "explicit_major_secondary": explicit_major_secondary,
        "explicit_never_automatic": explicit_never_automatic,
        "fallback_blocked": fallback_blocked,
        "operator_surface": operator_surface,
        "realtime_auto_send": lane == TEAMS_LANE_IMMEDIATE_MAJOR,
        "operator_tier": (
            "tier_a_core_major"
            if tier in TEAMS_IMMEDIATE_TIERS
            else "tier_b_major_secondary"
            if explicit_major_secondary or tier == "major_secondary"
            else "tier_c_specialist_niche"
            if explicit_specialist or fallback_blocked
            else "unlisted"
        ),
    }


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
