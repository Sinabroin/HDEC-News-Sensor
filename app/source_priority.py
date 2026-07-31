"""Live-only executive surface source-priority policy."""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any, Mapping, Sequence

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
