"""Strict, independent delivery state for Daily and Weekly editorial editions."""

from __future__ import annotations

import json
import os
import re
import tempfile
from copy import deepcopy
from datetime import date
from pathlib import Path
from typing import Mapping

from app import config

STATE_VERSION = 2
STATE_PATHS = {
    "daily": config.DATA_DIR / "editorial_daily_state.json",
    "weekly": config.DATA_DIR / "editorial_weekly_state.json",
}
_SUCCESS_FIELDS = {
    "edition_key",
    "coverage_start",
    "coverage_end",
    "html_sha256",
    "public_url",
    "smtp_status",
    "smtp_code",
    "sent_at",
}
_CLAIM_FIELDS = {
    "edition_key",
    "coverage_start",
    "coverage_end",
    "html_sha256",
    "public_url",
    "claim_owner",
    "claimed_at",
}
_IDENTITY_FIELDS = (
    "edition_key",
    "coverage_start",
    "coverage_end",
    "html_sha256",
    "public_url",
)
_V1_STATE_FIELDS = {
    "version",
    "edition_type",
    "successful_editions",
    "last_successful_edition",
    "last_successful_send_at",
}
_STATE_FIELDS = _V1_STATE_FIELDS | {"delivery_claims"}
_FORBIDDEN_SHARED_STATE_NAME = "teams" + "_push_state.json"
_CLAIM_OWNER_RE = re.compile(r"github-run:[1-9][0-9]*:attempt:[1-9][0-9]*")
_DAILY_KEY_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_WEEKLY_KEY_RE = re.compile(r"(\d{4})-W(\d{2})")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")


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
        "delivery_claims": {},
        "last_successful_edition": None,
        "last_successful_send_at": None,
    }


def _valid_nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and value == value.strip()


def _valid_edition_key(edition_type: str, value: object) -> bool:
    if not isinstance(value, str):
        return False
    if edition_type == "daily":
        if not _DAILY_KEY_RE.fullmatch(value):
            return False
        try:
            return date.fromisoformat(value).isoformat() == value
        except ValueError:
            return False
    match = _WEEKLY_KEY_RE.fullmatch(value)
    if not match:
        return False
    try:
        parsed = date.fromisocalendar(int(match.group(1)), int(match.group(2)), 1)
    except ValueError:
        return False
    iso_year, iso_week, _weekday = parsed.isocalendar()
    return value == f"{iso_year:04d}-W{iso_week:02d}"


def _valid_claim_owner(value: object) -> bool:
    return isinstance(value, str) and _CLAIM_OWNER_RE.fullmatch(value) is not None


def _validate_identity(record: Mapping, edition_type: str, kind: str) -> None:
    if not _valid_edition_key(edition_type, record.get("edition_key")):
        raise StateError(f"{kind} edition key is malformed or cross-edition")
    for field in ("coverage_start", "coverage_end", "public_url"):
        if not _valid_nonempty(record.get(field)):
            raise StateError(f"{kind} contains empty identity fields")
    if (
        not isinstance(record.get("html_sha256"), str)
        or _SHA256_RE.fullmatch(record["html_sha256"]) is None
    ):
        raise StateError(f"{kind} HTML SHA256 is malformed")


def _validate_success_records(value: object, edition_type: str) -> tuple[list, set[str]]:
    if not isinstance(value, list):
        raise StateError("successful_editions must be a list")
    seen: set[str] = set()
    for record in value:
        if not isinstance(record, dict) or set(record) != _SUCCESS_FIELDS:
            raise StateError("successful edition record is malformed")
        _validate_identity(record, edition_type, "successful edition record")
        if not _valid_nonempty(record.get("smtp_status")) or not _valid_nonempty(
            record.get("sent_at")
        ):
            raise StateError("successful edition record contains empty fields")
        if (
            record.get("smtp_status") != "accepted"
            or type(record.get("smtp_code")) is not int
            or record.get("smtp_code") != 250
        ):
            raise StateError("state contains a non-250 success record")
        key = record["edition_key"]
        if key in seen:
            raise StateError("duplicate successful edition")
        seen.add(key)
    return value, seen


def _validate_last_success(value: Mapping, records: list, seen: set[str]) -> None:
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


def _upgrade_v1(value: Mapping, edition_type: str) -> dict:
    if set(value) != _V1_STATE_FIELDS or value.get("edition_type") != edition_type:
        raise StateError("legacy state fields or identity are malformed")
    records, seen = _validate_success_records(value.get("successful_editions"), edition_type)
    _validate_last_success(value, records, seen)
    upgraded = deepcopy(dict(value))
    upgraded["version"] = STATE_VERSION
    upgraded["delivery_claims"] = {}
    return upgraded


def validate_state(value: object, edition_type: str) -> dict:
    state_path(edition_type)
    if not isinstance(value, dict):
        raise StateError("state fields are malformed")
    if type(value.get("version")) is int and value.get("version") == 1:
        return _upgrade_v1(value, edition_type)
    if set(value) != _STATE_FIELDS:
        raise StateError("state fields are malformed")
    if (
        type(value.get("version")) is not int
        or value.get("version") != STATE_VERSION
        or value.get("edition_type") != edition_type
    ):
        raise StateError("state identity mismatch")
    records, successful_keys = _validate_success_records(
        value.get("successful_editions"), edition_type
    )
    claims = value.get("delivery_claims")
    if not isinstance(claims, dict):
        raise StateError("delivery_claims must be a mapping")
    for edition_key, claim in claims.items():
        if not isinstance(claim, dict) or set(claim) != _CLAIM_FIELDS:
            raise StateError("delivery claim is malformed")
        _validate_identity(claim, edition_type, "delivery claim")
        if edition_key != claim["edition_key"]:
            raise StateError("delivery claim key does not match its edition")
        if edition_key in successful_keys:
            raise StateError("successful edition cannot retain a delivery claim")
        if not _valid_claim_owner(claim.get("claim_owner")):
            raise StateError("delivery claim owner is malformed")
        if not _valid_nonempty(claim.get("claimed_at")):
            raise StateError("delivery claim timestamp is malformed")
    _validate_last_success(value, records, successful_keys)
    return deepcopy(value)


def _object_without_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    value: dict = {}
    for key, item in pairs:
        if key in value:
            raise StateError("state contains duplicate object keys")
        value[key] = item
    return value


def load_state(edition_type: str, path: Path | None = None) -> dict:
    target = Path(path) if path is not None else state_path(edition_type)
    if target.name == _FORBIDDEN_SHARED_STATE_NAME:
        raise StateError("cross-workflow state access forbidden")
    if not target.exists():
        return empty_state(edition_type)
    try:
        raw = json.loads(
            target.read_text(encoding="utf-8"),
            object_pairs_hook=_object_without_duplicate_keys,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StateError("state is unreadable or malformed") from exc
    return validate_state(raw, edition_type)


def has_success(state: Mapping, edition_key: str) -> bool:
    return any(
        isinstance(record, Mapping) and record.get("edition_key") == edition_key
        for record in state.get("successful_editions", [])
    )


def has_claim(state: Mapping, edition_key: str) -> bool:
    claims = state.get("delivery_claims", {})
    return isinstance(claims, Mapping) and edition_key in claims


def _same_identity(left: Mapping, right: Mapping) -> bool:
    return all(left.get(field) == right.get(field) for field in _IDENTITY_FIELDS)


def add_claim(state: Mapping, edition_type: str, claim: Mapping) -> dict:
    current = validate_state(dict(state), edition_type)
    candidate = dict(claim)
    if set(candidate) != _CLAIM_FIELDS:
        raise StateError("delivery claim fields are malformed")
    _validate_identity(candidate, edition_type, "delivery claim")
    if not _valid_claim_owner(candidate.get("claim_owner")):
        raise StateError("delivery claim owner is malformed")
    if not _valid_nonempty(candidate.get("claimed_at")):
        raise StateError("delivery claim timestamp is malformed")
    edition_key = candidate["edition_key"]
    if has_success(current, edition_key):
        raise StateError("cannot claim an already successful edition")
    existing = current["delivery_claims"].get(edition_key)
    if existing is not None:
        if (
            _same_identity(existing, candidate)
            and existing["claim_owner"] == candidate["claim_owner"]
        ):
            return current
        raise StateError("delivery claim already belongs to another identity or owner")
    current["delivery_claims"][edition_key] = candidate
    return validate_state(current, edition_type)


def require_claim_owner(
    state: Mapping,
    edition_type: str,
    edition_key: str,
    claim_owner: str,
    identity: Mapping | None = None,
) -> dict:
    current = validate_state(dict(state), edition_type)
    if not _valid_claim_owner(claim_owner):
        raise StateError("required delivery claim owner is malformed")
    claim = current["delivery_claims"].get(edition_key)
    if claim is None:
        raise StateError("edition has no active delivery claim")
    if claim["claim_owner"] != claim_owner:
        raise StateError("delivery claim belongs to another owner")
    if identity is not None and not _same_identity(claim, identity):
        raise StateError("delivery claim identity mismatch")
    return deepcopy(claim)


def convert_claim_to_success(
    state: Mapping,
    edition_type: str,
    record: Mapping,
    claim_owner: str,
) -> dict:
    current = validate_state(dict(state), edition_type)
    candidate = dict(record)
    if set(candidate) != _SUCCESS_FIELDS:
        raise StateError("success record fields are malformed")
    _validate_identity(candidate, edition_type, "success record")
    if not _valid_nonempty(candidate.get("sent_at")):
        raise StateError("success record contains an empty timestamp")
    if (
        candidate.get("smtp_status") != "accepted"
        or type(candidate.get("smtp_code")) is not int
        or candidate.get("smtp_code") != 250
    ):
        raise StateError("only exact SMTP DATA 250 may change state")
    edition_key = candidate["edition_key"]
    require_claim_owner(
        current,
        edition_type,
        edition_key,
        claim_owner,
        identity=candidate,
    )
    current["successful_editions"].append(candidate)
    del current["delivery_claims"][edition_key]
    current["last_successful_edition"] = edition_key
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
