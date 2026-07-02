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
