"""Policy-driven signal extraction for AI radar eligibility.

Article title/snippet values are untrusted inputs.  The classifier never keeps
or compares full article titles; it extracts bounded actor/event/infra/exclusion
signals from text and applies the cross-axis policy in
``data/radar_signal_policy.json``.

This module is pure and offline: no DB, network, dashboard, or fixture access.
If the policy is missing or malformed, extraction yields no positive evidence
and classification fails closed.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path


_POLICY_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "radar_signal_policy.json"
)
_AXES = ("actor", "event", "infra", "exclusion")
_ASCII_TOKEN_RE = re.compile(r"^[a-z0-9+&.-]+$", re.IGNORECASE)


def _load_policy() -> dict:
    try:
        data = json.loads(_POLICY_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


_POLICY = _load_policy()
_POLICY_AXES = (
    _POLICY.get("axes") if isinstance(_POLICY.get("axes"), dict) else {}
)
_DECISION = (
    _POLICY.get("decision")
    if isinstance(_POLICY.get("decision"), dict)
    else {}
)
_HOURLY_URGENCY_SHADOW = (
    _POLICY.get("hourly_urgency_shadow")
    if isinstance(_POLICY.get("hourly_urgency_shadow"), dict)
    else {}
)

_SHADOW_GROUP_KEYS = (
    "positive_event_groups",
    "ambiguous_event_groups",
    "negative_context_groups",
    "actor_or_target_groups",
)
_SHADOW_STATUSES = frozenset(
    {"confirmed", "ambiguous", "blocked", "none", "unavailable"}
)


def policy_loaded() -> bool:
    """Return whether all four required signal axes loaded from policy."""
    return all(isinstance(_POLICY_AXES.get(axis), dict) for axis in _AXES)


def _norm(value: str | None) -> str:
    return unicodedata.normalize("NFKC", value or "").casefold()


def _contains(text: str, term: str) -> bool:
    """Substring-match domain phrases and token-match short ASCII identifiers."""
    needle = _norm(term).strip()
    if not needle:
        return False
    if _ASCII_TOKEN_RE.fullmatch(needle):
        return bool(
            re.search(
                rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])",
                text,
                re.IGNORECASE,
            )
        )
    return needle in text


def _matched_terms(text: str, spec: dict) -> list[str]:
    hits: list[str] = []
    for term in spec.get("any") or []:
        if isinstance(term, str) and _contains(text, term):
            hits.append(term)

    clauses = spec.get("all_of") or []
    clause_hits: list[str] = []
    valid_clauses = bool(clauses)
    for clause in clauses:
        if not isinstance(clause, list):
            valid_clauses = False
            break
        hit = next(
            (
                term
                for term in clause
                if isinstance(term, str) and _contains(text, term)
            ),
            None,
        )
        if hit is None:
            valid_clauses = False
            break
        clause_hits.append(hit)
    if valid_clauses:
        hits.extend(clause_hits)

    # Preserve policy order but do not repeat terms that matched two clauses.
    return list(dict.fromkeys(hits))


def _shadow_policy_valid(policy: object) -> bool:
    """Validate only the isolated shadow namespace, never the radar policy."""
    def term_spec_valid(spec: dict) -> bool:
        any_terms = spec.get("any")
        clauses = spec.get("all_of")
        any_valid = (
            isinstance(any_terms, list)
            and bool(any_terms)
            and all(isinstance(term, str) and term.strip() for term in any_terms)
        )
        clauses_valid = (
            isinstance(clauses, list)
            and bool(clauses)
            and all(
                isinstance(clause, list)
                and bool(clause)
                and all(isinstance(term, str) and term.strip() for term in clause)
                for clause in clauses
            )
        )
        return any_valid or clauses_valid

    if not isinstance(policy, dict):
        return False
    for key in _SHADOW_GROUP_KEYS:
        groups = policy.get(key)
        if not isinstance(groups, dict) or not groups:
            return False
        if not all(
            isinstance(group_id, str)
            and isinstance(spec, dict)
            and term_spec_valid(spec)
            for group_id, spec in groups.items()
        ):
            return False
    decision = policy.get("decision")
    if not isinstance(decision, dict):
        return False
    negatives = policy["negative_context_groups"]
    for key in ("blocking_negative_groups", "conflicting_negative_groups"):
        values = decision.get(key)
        if not isinstance(values, list) or not all(
            isinstance(value, str) and value in negatives for value in values
        ):
            return False
    contexts = policy["actor_or_target_groups"]
    ambiguous = policy["ambiguous_event_groups"]
    for spec in policy["positive_event_groups"].values():
        required = spec.get("required_context_groups")
        if not isinstance(required, list) or not required or not all(
            isinstance(value, str) and value in contexts for value in required
        ):
            return False
        conflicts = spec.get("ambiguous_conflicts")
        if not isinstance(conflicts, list) or not all(
            isinstance(value, str) and value in ambiguous for value in conflicts
        ):
            return False
    return True


def shadow_urgency_policy_loaded() -> bool:
    """Return whether the isolated hourly shadow contract is available."""
    return _shadow_policy_valid(_HOURLY_URGENCY_SHADOW)


def _shadow_group_matches(text: str, groups: dict) -> dict[str, list[str]]:
    matches: dict[str, list[str]] = {}
    for group_id, spec in groups.items():
        terms = _matched_terms(text, spec)
        if terms:
            matches[group_id] = terms
    return matches


def evaluate_hourly_urgency_shadow(
    article: dict,
    *,
    change_type: str = "",
    change_reasons: tuple[str, ...] | list[str] = (),
    policy_override: dict | None = None,
) -> dict:
    """Extract title-first confirmed-event evidence for hourly shadow telemetry.

    The result is categorical and pure.  It does not inspect score, HDEC tier,
    section, freshness, environment, clock, network, or persistent history.
    Snippet matches are retained separately but can never open ``confirmed``.
    ``policy_override`` exists for deterministic malformed-policy verification;
    normal callers omit it and use the isolated policy namespace loaded above.
    """
    policy = _HOURLY_URGENCY_SHADOW if policy_override is None else policy_override
    empty = {
        "shadow_urgency_status": "unavailable",
        "shadow_would_pass": False,
        "shadow_confirmed_event_types": [],
        "shadow_ambiguous_event_types": [],
        "shadow_negative_contexts": [],
        "shadow_evidence_source": "none",
        "title_positive_groups": [],
        "snippet_positive_groups": [],
        "title_ambiguous_groups": [],
        "snippet_ambiguous_groups": [],
        "title_negative_groups": [],
        "snippet_negative_groups": [],
        "title_actor_or_target_groups": [],
    }
    if not _shadow_policy_valid(policy):
        return empty

    title = _norm(str(article.get("title") or ""))
    snippet = _norm(str(article.get("snippet") or article.get("whyImportant") or ""))
    positive_groups = policy["positive_event_groups"]
    ambiguous_groups = policy["ambiguous_event_groups"]
    negative_groups = policy["negative_context_groups"]
    context_groups = policy["actor_or_target_groups"]

    title_positive_matches = _shadow_group_matches(title, positive_groups)
    snippet_positive_matches = _shadow_group_matches(snippet, positive_groups)
    title_ambiguous_matches = _shadow_group_matches(title, ambiguous_groups)
    snippet_ambiguous_matches = _shadow_group_matches(snippet, ambiguous_groups)
    title_negative_matches = _shadow_group_matches(title, negative_groups)
    snippet_negative_matches = _shadow_group_matches(snippet, negative_groups)
    title_context_matches = _shadow_group_matches(title, context_groups)

    qualifying_title_positive: list[str] = []
    for group_id in title_positive_matches:
        required = positive_groups[group_id]["required_context_groups"]
        if any(context in title_context_matches for context in required):
            qualifying_title_positive.append(group_id)

    title_positive = list(title_positive_matches)
    snippet_positive = list(snippet_positive_matches)
    title_ambiguous = list(title_ambiguous_matches)
    snippet_ambiguous = list(snippet_ambiguous_matches)
    title_negative = list(title_negative_matches)
    snippet_negative = list(snippet_negative_matches)

    title_has_evidence = bool(title_positive or title_ambiguous or title_negative)
    snippet_has_evidence = bool(
        snippet_positive or snippet_ambiguous or snippet_negative
    )
    if title_has_evidence and snippet_has_evidence:
        evidence_source = "title+snippet"
    elif title_has_evidence:
        evidence_source = "title"
    elif snippet_has_evidence:
        evidence_source = "snippet_only"
    else:
        evidence_source = "none"

    decision = policy["decision"]
    blocking = set(decision["blocking_negative_groups"])
    conflicting = set(decision["conflicting_negative_groups"])
    title_blocking = blocking.intersection(title_negative)
    title_conflicting = conflicting.intersection(title_negative)
    ambiguous_conflicts: set[str] = set()
    for group_id in qualifying_title_positive:
        ambiguous_conflicts.update(
            positive_groups[group_id]["ambiguous_conflicts"]
        )
    title_event_conflicts = ambiguous_conflicts.intersection(title_ambiguous)
    reasons = tuple(str(reason) for reason in change_reasons)
    score_crossing_only = (
        change_type == "priority_upgrade"
        and bool(reasons)
        and all("score crossed" in reason for reason in reasons)
    )

    if title_blocking:
        status = "blocked"
    elif score_crossing_only and not qualifying_title_positive:
        status = "blocked"
    elif qualifying_title_positive:
        status = (
            "ambiguous"
            if title_event_conflicts or title_conflicting
            else "confirmed"
        )
    elif title_positive or title_ambiguous or title_conflicting:
        status = "ambiguous"
    elif snippet_positive:
        # Aggregated snippets may contain a different article's action words.
        status = "ambiguous"
    else:
        status = "none"

    if status not in _SHADOW_STATUSES:  # defensive categorical contract
        status = "unavailable"
    confirmed_event_types = (
        qualifying_title_positive if status == "confirmed" else []
    )
    ambiguous_event_types = list(title_ambiguous)
    if status == "ambiguous":
        ambiguous_event_types.extend(qualifying_title_positive)
        if not title_has_evidence:
            ambiguous_event_types.extend(snippet_positive)
    ambiguous_event_types = list(dict.fromkeys(ambiguous_event_types))
    return {
        "shadow_urgency_status": status,
        "shadow_would_pass": status == "confirmed",
        "shadow_confirmed_event_types": confirmed_event_types,
        "shadow_ambiguous_event_types": ambiguous_event_types,
        "shadow_negative_contexts": title_negative,
        "shadow_evidence_source": evidence_source,
        "title_positive_groups": title_positive,
        "snippet_positive_groups": snippet_positive,
        "title_ambiguous_groups": title_ambiguous,
        "snippet_ambiguous_groups": snippet_ambiguous,
        "title_negative_groups": title_negative,
        "snippet_negative_groups": snippet_negative,
        "title_actor_or_target_groups": list(title_context_matches),
    }


def extract_ai_radar_signals(article: dict) -> dict:
    """Extract actor/event/infra/exclusion group IDs from title + snippet.

    ``source`` and generated ``topic_candidates`` are deliberately excluded:
    publisher names and collector-generated topics are not article evidence.
    """
    text = _norm(
        " ".join(
            part
            for part in (
                article.get("title") or "",
                article.get("snippet") or "",
            )
            if part
        )
    )
    signals: dict[str, list[str]] = {axis: [] for axis in _AXES}
    matches: dict[str, dict[str, list[str]]] = {axis: {} for axis in _AXES}
    for axis in _AXES:
        groups = _POLICY_AXES.get(axis)
        if not isinstance(groups, dict):
            continue
        for group_id, spec in groups.items():
            if not isinstance(group_id, str) or not isinstance(spec, dict):
                continue
            terms = _matched_terms(text, spec)
            if terms:
                signals[axis].append(group_id)
                matches[axis][group_id] = terms
    return {"signals": signals, "matches": matches}


def _tier(signals: dict, *, supplement: bool) -> int:
    actors = set(signals["actor"])
    events = set(signals["event"])
    infra = set(signals["infra"])
    if "hdec" in actors:
        return 1
    if supplement:
        if "ai_datacenter" in infra and events & {
            "project_delivery",
            "investment_expansion",
            "enabling_policy",
        }:
            return 2
        if infra & {"smart_construction"}:
            return 3
        return 4
    if infra & {"ai_compute", "semiconductor_infra"}:
        return 3
    if infra & {
        "ai_datacenter",
        "power_grid",
        "cooling_water",
        "smart_construction",
        "advanced_energy",
    }:
        return 2
    return 4


def classify_ai_radar(
    article: dict, *, supplement: bool = False, section: bool = False
) -> dict:
    """Classify one article using only cross-axis signal evidence."""
    if supplement and section:
        raise ValueError("supplement and section profiles are mutually exclusive")
    extracted = extract_ai_radar_signals(article)
    signals = extracted["signals"]
    infra = set(signals["infra"])
    exclusions = set(signals["exclusion"])

    hard_exclusions = set(_DECISION.get("hard_exclusions") or ())
    blocked = sorted(exclusions & hard_exclusions)
    if supplement:
        allowed_key = "supplement_infra"
        anchor_key = "supplement_anchor_infra"
    elif section:
        allowed_key = "section_infra"
        anchor_key = "section_anchor_infra"
    else:
        allowed_key = "primary_infra"
        anchor_key = "primary_anchor_infra"
    allowed_infra = set(_DECISION.get(allowed_key) or ())
    anchor_infra = set(_DECISION.get(anchor_key) or ())
    qualifying_infra = sorted(infra & allowed_infra)
    qualifying_anchors = sorted(infra & anchor_infra)

    if not policy_loaded():
        eligible = False
        reason = "policy_unavailable"
    elif blocked:
        eligible = False
        reason = "exclusion_signal"
    elif not qualifying_infra:
        eligible = False
        reason = "no_qualifying_infra_signal"
    elif not qualifying_anchors:
        eligible = False
        reason = "no_ai_infra_anchor"
    elif not any(
        signals.get(axis)
        for axis in (_DECISION.get("supporting_axes") or ("actor", "event"))
    ):
        eligible = False
        reason = "no_actor_or_event_support"
    else:
        eligible = True
        reason = "cross_axis_signal"

    return {
        "eligible": eligible,
        "operator_reference": not eligible,
        "reason": reason,
        "tier": _tier(signals, supplement=supplement) if eligible else 0,
        "signals": signals,
        "matches": extracted["matches"],
        "blocked_exclusions": blocked,
        "qualifying_infra": qualifying_infra,
        "qualifying_anchors": qualifying_anchors,
    }
