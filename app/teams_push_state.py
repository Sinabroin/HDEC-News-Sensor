"""Persistent dedup state for article-level Teams AI pushes (D7-AK-5B).

The state is intentionally separate from watch_state.json. It is written atomically and
must be mutated only after the Teams webhook reports success. Missing state starts empty;
malformed existing state fails closed to avoid accidental resend storms.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from app import config
from app.news_access import choose_article_link
from app.watch_state import normalize_url, title_fingerprint

KST = timezone(timedelta(hours=9))
STATE_VERSION = 1
DEFAULT_STATE_PATH = config.DATA_DIR / "teams_push_state.json"
# D7-AK-6E R4-R9A / R4-OPS-6C — optional held-secondary section. The historical
# key name is retained for state compatibility; records are differentiated by
# ``source_tier`` and may now contain normal Tier-B holdback observations as
# well as specialist supporting evidence. A held article is never marked sent.
HELD_SPECIALISTS_KEY = "held_specialists"

_ENTITY_TERMS = (
    "현대건설", "hyundai e&c", "openai", "오픈ai", "microsoft", "마이크로소프트",
    "google", "구글", "alphabet", "meta", "메타", "anthropic", "앤트로픽",
    "nvidia", "엔비디아", "amazon", "아마존", "aws", "oracle", "오라클",
    "삼성전자", "samsung", "sk하이닉스", "sk hynix", "삼성물산", "대우건설",
    "gs건설", "dl이앤씨", "포스코이앤씨", "sk에코플랜트",
)
_PRODUCT_RE = re.compile(
    r"\b(?:gpt[- ]?\d+(?:\.\d+)?|claude(?:[- ]?\d+(?:\.\d+)?)?|gemini(?:[- ]?\d+(?:\.\d+)?)?|"
    r"blackwell|rubin|h100|h200|b100|b200|mi300x|smr)\b",
    re.IGNORECASE,
)


class InvalidTeamsPushState(ValueError):
    """Existing persistent state is malformed; callers must stop rather than resend."""


@dataclass(frozen=True)
class DedupDecision:
    send_allowed: bool
    reason: str
    is_update: bool = False
    matched_key: str = ""


def now_iso() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


def resolve_state_path(path: str | Path | None = None) -> Path:
    raw = path or os.environ.get("TEAMS_PUSH_STATE_PATH") or DEFAULT_STATE_PATH
    return Path(raw)


def empty_state() -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "article_ids": {},
        "normalized_urls": {},
        "title_fingerprints": {},
        "cluster_keys": {},
        "last_successful_send_at": None,
        # R4-OPS-5 normal-card pacing is independent from TOP/HDEC-direct
        # delivery. Only a successfully accepted normal/important card advances
        # this rolling-window authority.
        "last_normal_send_at": None,
    }


def _validate_map(data: Mapping[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise InvalidTeamsPushState(f"{key} must be an object")
    if any(not isinstance(k, str) or not isinstance(v, dict) for k, v in value.items()):
        raise InvalidTeamsPushState(f"{key} contains invalid entries")
    return copy.deepcopy(value)


def validate_state(data: object) -> dict[str, Any]:
    if not isinstance(data, Mapping):
        raise InvalidTeamsPushState("state root must be an object")
    if data.get("version") != STATE_VERSION:
        raise InvalidTeamsPushState(f"unsupported state version: {data.get('version')!r}")
    state = empty_state()
    for key in ("article_ids", "normalized_urls", "title_fingerprints", "cluster_keys"):
        state[key] = _validate_map(data, key)
    last = data.get("last_successful_send_at")
    if last is not None and not isinstance(last, str):
        raise InvalidTeamsPushState("last_successful_send_at must be a string or null")
    state["last_successful_send_at"] = last
    last_normal = data.get("last_normal_send_at")
    if last_normal is not None and not isinstance(last_normal, str):
        raise InvalidTeamsPushState("last_normal_send_at must be a string or null")
    state["last_normal_send_at"] = last_normal
    held = data.get(HELD_SPECIALISTS_KEY)
    if held is not None:
        if not isinstance(held, dict) or any(
            not isinstance(key, str) or not isinstance(value, dict)
            for key, value in held.items()
        ):
            raise InvalidTeamsPushState(
                f"{HELD_SPECIALISTS_KEY} contains invalid entries"
            )
        # Backward-compatible by construction: the key is preserved only when
        # non-empty, so legacy states (and states whose last hold was cleared)
        # round-trip byte-identically without it.
        if held:
            state[HELD_SPECIALISTS_KEY] = copy.deepcopy(held)
    return state


def load_state(path: str | Path | None = None) -> dict[str, Any]:
    state_path = resolve_state_path(path)
    if not state_path.exists():
        return empty_state()
    try:
        raw = state_path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InvalidTeamsPushState("existing Teams push state is unreadable or invalid") from exc
    return validate_state(data)


def save_state(state: Mapping[str, Any], path: str | Path | None = None) -> Path:
    validated = validate_state(state)
    state_path = resolve_state_path(path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(validated, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=state_path.parent, delete=False
    ) as tmp:
        tmp.write(payload)
        tmp_path = Path(tmp.name)
    tmp_path.replace(state_path)
    return state_path


def _value(obj: object, key: str, default: Any = "") -> Any:
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _mapping(obj: object, key: str) -> Mapping[str, Any]:
    value = _value(obj, key, {})
    return value if isinstance(value, Mapping) else {}


def _clean(value: object) -> str:
    return " ".join(str(value or "").split())


def article_identity(article: object) -> dict[str, str]:
    article_id = _clean(
        _value(article, "article_key") or _value(article, "article_id") or _value(article, "id")
    )
    url_contract = article if isinstance(article, Mapping) else {
        "canonical_url": _value(article, "canonical_url"),
        "external_url": _value(article, "external_url"),
        "original_url": _value(article, "original_url"),
        "publisher_url": _value(article, "publisher_url"),
        "source_metadata": _value(article, "source_metadata"),
        "source_metadata_json": _value(article, "source_metadata_json"),
        "url": _value(article, "url"),
    }
    return {
        "article_id": article_id,
        # Canonical/resolved publisher URL is preferred. A normalized aggregator hop
        # remains a stable identity when publisher resolution is unavailable.
        "normalized_url": normalize_url(choose_article_link(url_contract).url),
        "title_fingerprint": title_fingerprint(_clean(_value(article, "title"))),
    }


def _confirmed_event_types(article: object) -> tuple[str, ...]:
    raw = _value(article, "shadow_confirmed_event_types", ())
    if isinstance(raw, str):
        raw = (raw,)
    if not isinstance(raw, (list, tuple, set)):
        return ()
    return tuple(sorted({_clean(item).lower() for item in raw if _clean(item)}))


def derive_event_cluster_key(article: object, topic_key: str = "") -> str:
    explicit = _clean(
        _value(article, "cluster_key")
        or _value(article, "evidence_cluster_key")
        or _mapping(article, "after").get("cluster_key")
        or _mapping(article, "provenance").get("cluster_key")
    )
    if explicit:
        return explicit

    title = _clean(_value(article, "title"))
    text = title.lower()
    entities = sorted({term.lower() for term in _ENTITY_TERMS if term.lower() in text})
    products = sorted({match.group(0).lower().replace(" ", "-") for match in _PRODUCT_RE.finditer(text)})
    event_types = _confirmed_event_types(article)
    published = _clean(_value(article, "published_at") or _value(article, "published_kst"))[:10]

    # Cross-publisher event clustering is used only with sufficiently specific anchors.
    # Otherwise fall back to the conservative normalized-title identity.
    anchors = entities + products
    if anchors and event_types:
        # A broad topic/entity/date tuple is an observation cluster, not proof that
        # every later article is the same delivered event. Include the normalized
        # title identity so distinct follow-ups remain independently alertable.
        raw = "|".join((
            published,
            topic_key,
            ",".join(event_types),
            ",".join(anchors),
            title_fingerprint(title),
        ))
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
        return f"ai_event:{digest}"

    fingerprint = title_fingerprint(title)
    return f"title:{fingerprint}" if fingerprint else ""


def material_signature(article: object) -> str:
    after = _mapping(article, "after")
    fields = {
        "title": _clean(_value(article, "title") or after.get("title")),
        "summary": _clean(
            _value(article, "summary") or _value(article, "snippet")
            or after.get("snippet") or after.get("summary")
        ),
        "impact": _clean(
            _value(article, "hdec_relevance") or _value(article, "radarReason")
            or _value(article, "whyImportant") or after.get("radarReason")
            or after.get("whyImportant")
        ),
        "confirmed_event_types": _confirmed_event_types(article),
        "score": _value(article, "score", after.get("score")),
        "amount": _value(article, "amount", after.get("amount")),
        "contract_value": _value(article, "contract_value", after.get("contract_value")),
        "project": _value(article, "project", after.get("project")),
    }
    canonical = json.dumps(fields, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _lookup(state: Mapping[str, Any], map_name: str, key: str) -> dict[str, Any] | None:
    if not key:
        return None
    mapping = state.get(map_name, {})
    entry = mapping.get(key)
    if isinstance(entry, dict):
        return entry
    if map_name == "normalized_urls" and isinstance(mapping, Mapping):
        # R4-R11: ledger keys recorded before URL canonicalization preserved
        # scheme and the www. prefix (the TTL production entry is keyed
        # ``http://www.ttlnews.com/…``). The stored ledger is never rewritten;
        # instead every legacy key still matches its canonical identity here,
        # so an http/https or www/non-www variant can never evade dedup.
        for legacy_key, legacy_entry in mapping.items():
            if isinstance(legacy_entry, dict) and normalize_url(str(legacy_key)) == key:
                return legacy_entry
    return None


def evaluate_dedup(
    state: Mapping[str, Any],
    article: object,
    *,
    cluster_key: str,
    signature: str,
    is_material_update: bool,
) -> DedupDecision:
    validated = validate_state(state)
    identity = article_identity(article)
    matches = (
        ("article_id", "article_ids", identity["article_id"]),
        ("normalized_url", "normalized_urls", identity["normalized_url"]),
        ("title_fingerprint", "title_fingerprints", identity["title_fingerprint"]),
        ("cluster_key", "cluster_keys", cluster_key),
    )
    for label, map_name, key in matches:
        entry = _lookup(validated, map_name, key)
        if not entry:
            continue
        previous_signature = _clean(entry.get("material_signature"))
        if is_material_update and signature and signature != previous_signature:
            return DedupDecision(True, "material_update", is_update=True, matched_key=label)
        return DedupDecision(False, f"duplicate:{label}", matched_key=label)
    return DedupDecision(True, "new_article_or_event")


def filter_unsent_candidates(
    state: Mapping[str, Any], candidates: tuple[Any, ...] | list[Any]
) -> tuple[tuple[Any, ...], tuple[DedupDecision, ...]]:
    """Apply persistent dedup decisions without mutating or writing state."""
    current = validate_state(state)
    accepted: list[Any] = []
    decisions: list[DedupDecision] = []
    for candidate in candidates:
        decision = evaluate_dedup(
            current,
            candidate.article,
            cluster_key=candidate.cluster_key,
            signature=candidate.material_signature,
            is_material_update=bool(candidate.is_update),
        )
        decisions.append(decision)
        if decision.send_allowed:
            accepted.append(replace(candidate, is_update=decision.is_update))
    return tuple(accepted), tuple(decisions)


def _entry(
    *, sent_at: str, cluster_key: str, signature: str, importance: str,
    source: str, is_update: bool, delivery_id: str,
) -> dict[str, Any]:
    return {
        "sent_at": sent_at,
        "last_material_update_at": sent_at if is_update else None,
        "cluster_key": cluster_key,
        "material_signature": signature,
        "importance": importance,
        "source": source,
        "delivery_id": delivery_id,
    }


def mark_sent_after_success(
    state: Mapping[str, Any],
    article: object,
    *,
    cluster_key: str,
    signature: str,
    importance: str,
    source: str,
    send_succeeded: bool,
    sent_at: str | None = None,
    is_update: bool = False,
    delivery_id: str = "",
    advances_normal_pace: bool | None = None,
) -> dict[str, Any]:
    """Return updated state only when delivery succeeded; otherwise return unchanged state."""
    current = validate_state(state)
    if not send_succeeded:
        return current

    ts = sent_at or now_iso()
    identity = article_identity(article)
    entry = _entry(
        sent_at=ts,
        cluster_key=cluster_key,
        signature=signature,
        importance=importance,
        source=source,
        is_update=is_update,
        delivery_id=_clean(delivery_id),
    )
    for map_name, key in (
        ("article_ids", identity["article_id"]),
        ("normalized_urls", identity["normalized_url"]),
        ("title_fingerprints", identity["title_fingerprint"]),
        ("cluster_keys", cluster_key),
    ):
        if key:
            prior = current[map_name].get(key, {})
            first_sent = prior.get("first_sent_at") if isinstance(prior, dict) else None
            current[map_name][key] = {**entry, "first_sent_at": first_sent or ts}
    current["last_successful_send_at"] = ts
    if advances_normal_pace is None:
        advances_normal_pace = _clean(importance).lower() == "important"
    if advances_normal_pace:
        current["last_normal_send_at"] = ts
    return current


def persist_after_success(
    state: Mapping[str, Any],
    article: object,
    *,
    path: str | Path | None,
    cluster_key: str,
    signature: str,
    importance: str,
    source: str,
    send_succeeded: bool,
    sent_at: str | None = None,
    is_update: bool = False,
    delivery_id: str = "",
    advances_normal_pace: bool | None = None,
) -> dict[str, Any]:
    """Persist only after success. Failed delivery performs no filesystem write."""
    if not send_succeeded:
        return validate_state(state)
    updated = mark_sent_after_success(
        state,
        article,
        cluster_key=cluster_key,
        signature=signature,
        importance=importance,
        source=source,
        send_succeeded=True,
        sent_at=sent_at,
        is_update=is_update,
        delivery_id=delivery_id,
        advances_normal_pace=advances_normal_pace,
    )
    save_state(updated, path)
    return updated


# ---------------------------------------------------------------------------
# D7-AK-6E R4-R9A — held-specialist holdback records.
#
# These helpers are pure state transforms (no filesystem I/O): the production
# sender applies them in send mode only and saves through ``save_state``, so a
# dry run can evaluate holdback without writing anything. A held record never
# marks an article as sent — the accepted ledger maps stay the only send
# authority.


def holdback_identity_key(article: object) -> str:
    """Single stable held-record key: normalized URL, then id, then title."""
    identity = article_identity(article)
    return (
        identity["normalized_url"]
        or identity["article_id"]
        or identity["title_fingerprint"]
    )


def get_held_record(
    state: Mapping[str, Any], article: object
) -> dict[str, Any] | None:
    key = holdback_identity_key(article)
    if not key:
        return None
    held = state.get(HELD_SPECIALISTS_KEY)
    entry = held.get(key) if isinstance(held, Mapping) else None
    return dict(entry) if isinstance(entry, dict) else None


def minutes_between(earlier_iso: object, later_iso: object) -> float:
    """Signed minutes from ``earlier`` to ``later``; malformed input yields 0.0.

    A malformed timestamp therefore reads as "not aged", which keeps the
    holdback fail-closed (held, never released early)."""
    try:
        earlier = datetime.fromisoformat(
            _clean(earlier_iso).replace("Z", "+00:00")
        )
        later = datetime.fromisoformat(_clean(later_iso).replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if earlier.tzinfo is None:
        earlier = earlier.replace(tzinfo=KST)
    if later.tzinfo is None:
        later = later.replace(tzinfo=KST)
    return (later - earlier).total_seconds() / 60.0


def observe_held_specialist(
    state: Mapping[str, Any],
    article: object,
    *,
    cluster_key: str,
    source: str,
    source_tier: str,
    holdback_reason: str,
    fallback_eligible: bool,
    now: str | None = None,
) -> dict[str, Any]:
    """Create or refresh one held-specialist observation; returns new state.

    ``first_seen_at`` is set once on the first observation and preserved on
    every later one; ``last_seen_at`` always advances. Prior
    replaced-by-major evidence is preserved."""
    current = validate_state(state)
    key = holdback_identity_key(article)
    if not key:
        return current
    identity = article_identity(article)
    ts = _clean(now) or now_iso()
    held = dict(current.get(HELD_SPECIALISTS_KEY) or {})
    prior = held.get(key)
    prior = prior if isinstance(prior, dict) else {}
    held[key] = {
        "first_seen_at": _clean(prior.get("first_seen_at")) or ts,
        "last_seen_at": ts,
        "article_id": identity["article_id"],
        "normalized_url": identity["normalized_url"],
        "title_fingerprint": identity["title_fingerprint"],
        "cluster_key": _clean(cluster_key),
        "source": _clean(source),
        "source_tier": _clean(source_tier),
        "holdback_reason": _clean(holdback_reason),
        "fallback_eligible": bool(fallback_eligible),
        "representative_publisher": _clean(prior.get("representative_publisher")),
        "replaced_by_major_media": _clean(prior.get("replaced_by_major_media")),
        "replaced_by_tier_a": _clean(prior.get("replaced_by_tier_a")),
    }
    current[HELD_SPECIALISTS_KEY] = held
    return current


def mark_held_replaced_by_major(
    state: Mapping[str, Any],
    cluster_key: str,
    *,
    major_identity: str,
    major_source: str,
) -> tuple[dict[str, Any], int]:
    """Mark held records of one event as replaced by a delivered major card.

    The held specialist becomes supporting evidence: it stays recorded, loses
    fallback eligibility, and names the representative publisher. Returns the
    new state and how many records were marked."""
    current = validate_state(state)
    cluster = _clean(cluster_key)
    held = dict(current.get(HELD_SPECIALISTS_KEY) or {})
    if not cluster or not held:
        return current, 0
    changed = 0
    for key, entry in held.items():
        if not isinstance(entry, dict):
            continue
        if _clean(entry.get("cluster_key")) != cluster:
            continue
        # R4-OPS-6C: Tier-B replacement authority belongs only to an actual
        # Tier-A delivery and is handled by mark_held_replaced_by_tier_a.
        if _clean(entry.get("source_tier")) == "major_secondary":
            continue
        if _clean(entry.get("replaced_by_major_media")):
            continue
        held[key] = {
            **entry,
            "replaced_by_major_media": _clean(major_identity),
            "representative_publisher": _clean(major_source),
            "fallback_eligible": False,
            "holdback_reason": "replaced_by_major_media",
        }
        changed += 1
    if changed:
        current[HELD_SPECIALISTS_KEY] = held
    return current, changed


def mark_held_replaced_by_tier_a(
    state: Mapping[str, Any],
    cluster_key: str,
    *,
    tier_a_identity: str,
    tier_a_source: str,
) -> tuple[dict[str, Any], int]:
    """Permanently suppress held same-event Tier-B rows after a Tier-A send.

    This is intentionally narrower than :func:`mark_held_replaced_by_major`:
    only ``major_secondary`` observations are counted, and only a caller that
    actually delivered a Tier-A representative may invoke it. The accepted
    cluster ledger independently prevents later duplicate delivery; this held
    record makes the source-preference outcome observable and durable.
    """
    current = validate_state(state)
    cluster = _clean(cluster_key)
    held = dict(current.get(HELD_SPECIALISTS_KEY) or {})
    if not cluster or not held:
        return current, 0
    changed = 0
    for key, entry in held.items():
        if not isinstance(entry, dict):
            continue
        if _clean(entry.get("cluster_key")) != cluster:
            continue
        if _clean(entry.get("source_tier")) != "major_secondary":
            continue
        if _clean(entry.get("replaced_by_tier_a")):
            continue
        held[key] = {
            **entry,
            "replaced_by_tier_a": _clean(tier_a_identity),
            "replaced_by_major_media": _clean(tier_a_identity),
            "representative_publisher": _clean(tier_a_source),
            "fallback_eligible": False,
            "holdback_reason": "replaced_by_tier_a",
        }
        changed += 1
    if changed:
        current[HELD_SPECIALISTS_KEY] = held
    return current, changed


def clear_held_record(
    state: Mapping[str, Any], article: object
) -> dict[str, Any]:
    """Drop one held record (used after its article is actually delivered)."""
    current = validate_state(state)
    key = holdback_identity_key(article)
    held = dict(current.get(HELD_SPECIALISTS_KEY) or {})
    if key and key in held:
        del held[key]
        if held:
            current[HELD_SPECIALISTS_KEY] = held
        else:
            current.pop(HELD_SPECIALISTS_KEY, None)
    return current
