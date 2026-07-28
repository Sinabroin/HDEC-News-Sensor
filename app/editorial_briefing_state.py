"""Strict, independent success state for Daily and Weekly editorial editions."""

from __future__ import annotations

import json
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Mapping

from app import config

STATE_VERSION = 1
STATE_PATHS = {
    "daily": config.DATA_DIR / "editorial_daily_state.json",
    "weekly": config.DATA_DIR / "editorial_weekly_state.json",
}
_RECORD_FIELDS = {
    "edition_key",
    "coverage_start",
    "coverage_end",
    "html_sha256",
    "public_url",
    "smtp_status",
    "smtp_code",
    "sent_at",
}
_STATE_FIELDS = {
    "version",
    "edition_type",
    "successful_editions",
    "last_successful_edition",
    "last_successful_send_at",
}
_FORBIDDEN_SHARED_STATE_NAME = "teams" + "_push_state.json"


class StateError(RuntimeError):
    """Missing state is allowed; malformed or cross-wired state fails closed."""


def state_path(edition_type: str) -> Path:
    try:
        return STATE_PATHS[edition_type]
    except KeyError as exc:
        raise StateError("unsupported edition type") from exc


def empty_state(edition_type: str) -> dict:
    state_path(edition_type)
    return {
        "version": STATE_VERSION,
        "edition_type": edition_type,
        "successful_editions": [],
        "last_successful_edition": None,
        "last_successful_send_at": None,
    }


def _valid_nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_state(value: object, edition_type: str) -> dict:
    if not isinstance(value, dict) or set(value) != _STATE_FIELDS:
        raise StateError("state fields are malformed")
    if value.get("version") != STATE_VERSION or value.get("edition_type") != edition_type:
        raise StateError("state identity mismatch")
    records = value.get("successful_editions")
    if not isinstance(records, list):
        raise StateError("successful_editions must be a list")
    seen: set[str] = set()
    for record in records:
        if not isinstance(record, dict) or set(record) != _RECORD_FIELDS:
            raise StateError("successful edition record is malformed")
        if any(not _valid_nonempty(record.get(field)) for field in _RECORD_FIELDS - {"smtp_code"}):
            raise StateError("successful edition record contains empty fields")
        if record.get("smtp_status") != "accepted" or record.get("smtp_code") != 250:
            raise StateError("state contains a non-250 success record")
        key = record["edition_key"]
        if key in seen:
            raise StateError("duplicate successful edition")
        seen.add(key)
    last_key = value.get("last_successful_edition")
    last_sent = value.get("last_successful_send_at")
    if records:
        if not _valid_nonempty(last_key) or last_key not in seen or not _valid_nonempty(last_sent):
            raise StateError("last successful edition metadata mismatch")
        matching = next(record for record in records if record["edition_key"] == last_key)
        if matching["sent_at"] != last_sent:
            raise StateError("last successful send timestamp mismatch")
    elif last_key is not None or last_sent is not None:
        raise StateError("empty state cannot have last-success metadata")
    return deepcopy(value)


def load_state(edition_type: str, path: Path | None = None) -> dict:
    target = Path(path) if path is not None else state_path(edition_type)
    if target.name == _FORBIDDEN_SHARED_STATE_NAME:
        raise StateError("cross-workflow state access forbidden")
    if not target.exists():
        return empty_state(edition_type)
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StateError("state is unreadable or malformed") from exc
    return validate_state(raw, edition_type)


def has_success(state: Mapping, edition_key: str) -> bool:
    return any(
        isinstance(record, Mapping) and record.get("edition_key") == edition_key
        for record in state.get("successful_editions", [])
    )


def add_success(state: Mapping, edition_type: str, record: Mapping) -> dict:
    current = validate_state(dict(state), edition_type)
    candidate = dict(record)
    if set(candidate) != _RECORD_FIELDS:
        raise StateError("success record fields are malformed")
    if candidate.get("smtp_status") != "accepted" or candidate.get("smtp_code") != 250:
        raise StateError("only exact SMTP DATA 250 may change state")
    if any(not _valid_nonempty(candidate.get(field)) for field in _RECORD_FIELDS - {"smtp_code"}):
        raise StateError("success record contains empty fields")
    if has_success(current, candidate["edition_key"]):
        return current
    current["successful_editions"].append(candidate)
    current["last_successful_edition"] = candidate["edition_key"]
    current["last_successful_send_at"] = candidate["sent_at"]
    return validate_state(current, edition_type)


def atomic_write_state(edition_type: str, state: Mapping, path: Path | None = None) -> None:
    target = Path(path) if path is not None else state_path(edition_type)
    if target.name == _FORBIDDEN_SHARED_STATE_NAME:
        raise StateError("cross-workflow state access forbidden")
    payload = validate_state(dict(state), edition_type)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=str(target.parent))
    temporary_path = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, target)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()
