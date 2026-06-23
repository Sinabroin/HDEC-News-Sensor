"""Executive Telegram preference store.

This module owns local recipient preferences only. It does not change sensing
criteria, source rules, topic catalogs, business lens catalogs, Telegram send
gates, or global operator settings.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STORE_PATH = ROOT / "data" / "executive_preferences.json"
STORE_VERSION = 1

DELIVERY_MODE_ALL = "all"
DELIVERY_MODE_PRIORITY = "priority_only"
DELIVERY_MODE_MUTED = "muted"
DELIVERY_MODES = (DELIVERY_MODE_ALL, DELIVERY_MODE_PRIORITY, DELIVERY_MODE_MUTED)

LENS_PREFERENCE_KEYS = (
    "topic_profiles",
    "business_lenses",
    "org_units",
    "execution_scopes",
)


@dataclass(frozen=True)
class ExecutivePreference:
    """Personal Telegram receiving/filtering preferences for one recipient."""

    chat_id: str
    user_label: str = ""
    lens_preferences: dict[str, list[str]] = field(default_factory=dict)
    delivery_mode: str = DELIVERY_MODE_ALL
    created_at: str = ""
    updated_at: str = ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _chat_id(value: Any) -> str:
    return str(value or "").strip()


def _empty_lens_preferences() -> dict[str, list[str]]:
    return {key: [] for key in LENS_PREFERENCE_KEYS}


def _catalog_ids() -> dict[str, set[str]]:
    """Allowed ids for personal filters. Reading catalogs must not mutate them."""
    from app import topic_profiles

    return {
        "topic_profiles": {p.id for p in topic_profiles.all_topic_profiles()},
        "business_lenses": {p.id for p in topic_profiles.all_business_lenses()},
        "org_units": {t.id for t in topic_profiles.all_org_unit_tags()},
        "execution_scopes": {t.id for t in topic_profiles.all_execution_scope_tags()},
    }


def _clean_id_list(value: Any, allowed: set[str]) -> list[str]:
    if not isinstance(value, list):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for item in value:
        normalized = str(item or "").strip()
        if not normalized or normalized not in allowed or normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
    return out


def _clean_lens_preferences(value: Any) -> dict[str, list[str]]:
    catalogs = _catalog_ids()
    source = value if isinstance(value, dict) else {}
    return {
        key: _clean_id_list(source.get(key), allowed)
        for key, allowed in catalogs.items()
    }


def default_preference(chat_id: str, now: str | None = None) -> dict:
    """Return a safe default preference for an unknown recipient.

    Empty lens lists mean no personal filter is applied yet. Global sensing
    criteria are unaffected.
    """
    recipient_id = _chat_id(chat_id)
    if not recipient_id:
        raise ValueError("chat_id is required")
    timestamp = now or _now_iso()
    return asdict(ExecutivePreference(
        chat_id=recipient_id,
        lens_preferences=_empty_lens_preferences(),
        delivery_mode=DELIVERY_MODE_ALL,
        created_at=timestamp,
        updated_at=timestamp,
    ))


def _normalize_preference(raw: Any, fallback_now: str | None = None) -> dict | None:
    if not isinstance(raw, dict):
        return None
    recipient_id = _chat_id(raw.get("chat_id"))
    if not recipient_id:
        return None
    now = fallback_now or _now_iso()
    created_at = str(raw.get("created_at") or now).strip() or now
    updated_at = str(raw.get("updated_at") or created_at).strip() or created_at
    delivery_mode = str(raw.get("delivery_mode") or DELIVERY_MODE_ALL).strip()
    if delivery_mode not in DELIVERY_MODES:
        delivery_mode = DELIVERY_MODE_ALL
    user_label = str(raw.get("user_label") or "").strip()
    return asdict(ExecutivePreference(
        chat_id=recipient_id,
        user_label=user_label,
        lens_preferences=_clean_lens_preferences(raw.get("lens_preferences")),
        delivery_mode=delivery_mode,
        created_at=created_at,
        updated_at=updated_at,
    ))


def _read_store(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    entries = raw.get("recipients")
    if not isinstance(entries, list):
        return {}
    preferences: dict[str, dict] = {}
    for item in entries:
        normalized = _normalize_preference(item)
        if normalized is None:
            continue
        preferences[normalized["chat_id"]] = normalized
    return preferences


def load_preferences(path: str | Path = DEFAULT_STORE_PATH) -> dict[str, dict]:
    """Load preferences keyed by chat_id.

    Missing or malformed files return an empty map so callers can safely fall
    back to default_preference(chat_id).
    """
    return _read_store(Path(path))


def get_preference(chat_id: str, path: str | Path = DEFAULT_STORE_PATH) -> dict:
    """Return a stored preference or a safe default for an unknown chat_id."""
    recipient_id = _chat_id(chat_id)
    if not recipient_id:
        raise ValueError("chat_id is required")
    preferences = load_preferences(path)
    return preferences.get(recipient_id) or default_preference(recipient_id)


def _merge_patch(current: dict, patch: dict, updated_at: str) -> dict:
    merged = dict(current)
    if "user_label" in patch:
        merged["user_label"] = str(patch.get("user_label") or "").strip()
    if "delivery_mode" in patch:
        mode = str(patch.get("delivery_mode") or "").strip()
        merged["delivery_mode"] = mode if mode in DELIVERY_MODES else DELIVERY_MODE_ALL
    if "lens_preferences" in patch:
        existing = dict(merged.get("lens_preferences") or {})
        incoming = patch.get("lens_preferences")
        if isinstance(incoming, dict):
            existing.update(incoming)
        merged["lens_preferences"] = _clean_lens_preferences(existing)
    merged["updated_at"] = updated_at
    return merged


def _write_store(path: Path, preferences: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": STORE_VERSION,
        "recipients": [
            preferences[key]
            for key in sorted(preferences)
        ],
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                           prefix=f".{path.name}.", delete=False) as tmp:
        tmp.write(text)
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)


def upsert_preference(chat_id: str, patch: dict,
                      path: str | Path = DEFAULT_STORE_PATH) -> dict:
    """Create or update one recipient preference and persist the JSON store."""
    recipient_id = _chat_id(chat_id)
    if not recipient_id:
        raise ValueError("chat_id is required")
    if not isinstance(patch, dict):
        raise TypeError("patch must be a dict")
    store_path = Path(path)
    preferences = load_preferences(store_path)
    now = _now_iso()
    current = preferences.get(recipient_id) or default_preference(recipient_id, now=now)
    updated = _normalize_preference(_merge_patch(current, patch, now), fallback_now=now)
    if updated is None:
        raise ValueError("invalid preference patch")
    preferences[recipient_id] = updated
    _write_store(store_path, preferences)
    return updated
