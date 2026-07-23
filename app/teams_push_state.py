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
from app.watch_state import normalize_url, title_fingerprint

KST = timezone(timedelta(hours=9))
STATE_VERSION = 1
DEFAULT_STATE_PATH = config.DATA_DIR / "teams_push_state.json"

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
    return {
        "article_id": article_id,
        "normalized_url": normalize_url(_clean(_value(article, "url"))),
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
        raw = "|".join((published, topic_key, ",".join(event_types), ",".join(anchors)))
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
    entry = state.get(map_name, {}).get(key)
    return entry if isinstance(entry, dict) else None


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
    )
    save_state(updated, path)
    return updated
