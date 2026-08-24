"""Independent exact-snapshot Editor notification identity and state."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping

from app import config, public_urls


STATE_VERSION = 2
STATE_PATH = config.DATA_DIR / "editor_delivery_state.json"
CLAIM_TTL = timedelta(minutes=10)
_OWNER_RE = re.compile(r"github-run:[1-9][0-9]*:attempt:[1-9][0-9]*")
_SHA_RE = re.compile(r"[0-9a-f]{64}")
STATUS_CLAIMED = "claimed"
STATUS_TRANSPORT_ARMED = "transport_armed"
STATUS_SENT = "sent"
STATUS_REJECTED = "rejected"
STATUS_AMBIGUOUS = "ambiguous_reconciliation_required"


class EditorDeliveryError(RuntimeError):
    """Malformed identity/state fails closed."""


def canonical_snapshot_manifest_bytes(manifest: Mapping) -> bytes:
    core = {
        key: value
        for key, value in manifest.items()
        if key not in {"review_snapshot_id", "integrity"}
    }
    return json.dumps(
        core,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def validate_snapshot_manifest(manifest: object) -> dict:
    if not isinstance(manifest, dict):
        raise EditorDeliveryError("editor snapshot manifest is not an object")
    snapshot_id = str(manifest.get("review_snapshot_id") or "")
    edition_key = public_urls.parse_editor_snapshot_id(snapshot_id)
    if (
        manifest.get("version") != 1
        or manifest.get("product") != "editor_review_snapshot"
        or not edition_key
        or manifest.get("edition_key") != edition_key
    ):
        raise EditorDeliveryError("editor snapshot identity mismatch")
    integrity = manifest.get("integrity")
    digest = str(integrity.get("digest") or "") if isinstance(integrity, dict) else ""
    if not _SHA_RE.fullmatch(digest) or not snapshot_id.endswith(digest[:16]):
        raise EditorDeliveryError("editor snapshot integrity malformed")
    recomputed = hashlib.sha256(canonical_snapshot_manifest_bytes(manifest)).hexdigest()
    if recomputed != digest:
        raise EditorDeliveryError("editor snapshot integrity mismatch")
    for field in ("candidate_bundle_sha256", "console_html_sha256"):
        if not _SHA_RE.fullmatch(str(manifest.get(field) or "")):
            raise EditorDeliveryError("editor snapshot resource digest malformed")
    radar_digest = manifest.get("radar_audit_sha256")
    if radar_digest is not None and not _SHA_RE.fullmatch(str(radar_digest)):
        raise EditorDeliveryError("editor radar audit digest malformed")
    assets = manifest.get("assets")
    if not isinstance(assets, list):
        raise EditorDeliveryError("editor snapshot assets malformed")
    for asset in assets:
        if not isinstance(asset, dict) or set(asset) != {
            "relative_path", "sha256", "byte_size"
        }:
            raise EditorDeliveryError("editor snapshot asset record malformed")
        relative = str(asset.get("relative_path") or "")
        name = relative.removeprefix("assets/images/")
        if (
            not relative.startswith("assets/images/")
            or not name
            or "/" in name
            or "\\" in name
            or not _SHA_RE.fullmatch(str(asset.get("sha256") or ""))
            or type(asset.get("byte_size")) is not int
            or asset["byte_size"] < 0
        ):
            raise EditorDeliveryError("editor snapshot asset identity malformed")
    expected_flags = {
        "generated": True,
        "published": False,
        "public_verified": False,
        "claimed": False,
        "sent": False,
        "duplicate_skipped": False,
        "failed": False,
    }
    if any(manifest.get(key) is not value for key, value in expected_flags.items()):
        raise EditorDeliveryError("editor snapshot generation evidence malformed")
    return deepcopy(manifest)


def load_snapshot_manifest(path: Path, expected_snapshot_id: str = "") -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EditorDeliveryError("editor snapshot manifest missing or malformed") from exc
    validated = validate_snapshot_manifest(payload)
    if expected_snapshot_id and validated["review_snapshot_id"] != expected_snapshot_id:
        raise EditorDeliveryError("editor snapshot requested identity mismatch")
    return validated


def empty_state() -> dict:
    return {
        "version": STATE_VERSION,
        "successful_deliveries": [],
        "delivery_claims": {},
        "reconciliation_history": [],
    }


def _timestamp(value: object) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise EditorDeliveryError("editor delivery timestamp malformed") from exc
    if parsed.tzinfo is None:
        raise EditorDeliveryError("editor delivery timestamp must be aware")
    return parsed


def _identity(record: Mapping) -> tuple[str, str, str, str]:
    snapshot_id = str(record.get("review_snapshot_id") or "")
    edition_key = public_urls.parse_editor_snapshot_id(snapshot_id)
    public_url = str(record.get("editor_public_url") or "")
    manifest_sha = str(record.get("manifest_sha256") or "")
    if (
        not edition_key
        or record.get("edition_key") != edition_key
        or "/latest" in public_url
        or not public_url.endswith(f"/snapshots/{snapshot_id}/index.html")
        or not _SHA_RE.fullmatch(manifest_sha)
    ):
        raise EditorDeliveryError("editor delivery identity mismatch")
    return snapshot_id, edition_key, public_url, manifest_sha


def validate_state(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != {
        "version", "successful_deliveries", "delivery_claims",
        "reconciliation_history",
    }:
        raise EditorDeliveryError("editor delivery state fields malformed")
    if value.get("version") != STATE_VERSION:
        raise EditorDeliveryError("editor delivery state version mismatch")
    successful = value.get("successful_deliveries")
    claims = value.get("delivery_claims")
    reconciliations = value.get("reconciliation_history")
    if (
        not isinstance(successful, list)
        or not isinstance(claims, dict)
        or not isinstance(reconciliations, list)
    ):
        raise EditorDeliveryError("editor delivery state collections malformed")
    seen_dates: set[str] = set()
    for record in successful:
        if not isinstance(record, dict):
            raise EditorDeliveryError("editor success record malformed")
        _snapshot, edition_key, _url, _sha = _identity(record)
        if edition_key in seen_dates:
            raise EditorDeliveryError("duplicate successful Editor date")
        seen_dates.add(edition_key)
        if (
            record.get("generated") is not True
            or record.get("published") is not True
            or record.get("public_verified") is not True
            or record.get("claimed") is not True
            or record.get("sent") is not True
            or record.get("duplicate_skipped") is not False
            or record.get("failed") is not False
            or record.get("delivery_status") != STATUS_SENT
        ):
            raise EditorDeliveryError("editor success evidence is false-green")
        evidence_kind = record.get("delivery_evidence_kind")
        if evidence_kind == "smtp_250":
            if (
                record.get("smtp_status") != "accepted"
                or record.get("smtp_code") != 250
            ):
                raise EditorDeliveryError("editor SMTP success is not exact 250")
        elif evidence_kind == "operator_reconciled":
            if (
                record.get("smtp_status") != "operator_reconciled"
                or record.get("smtp_code") is not None
                or not _SHA_RE.fullmatch(
                    str(record.get("operator_evidence_sha256") or "")
                )
                or not str(record.get("reconciled_by") or "").strip()
            ):
                raise EditorDeliveryError("editor operator reconciliation malformed")
        else:
            raise EditorDeliveryError("editor delivery evidence kind malformed")
        _timestamp(record.get("claimed_at"))
        _timestamp(record.get("sent_at"))
    for edition_key, claim in claims.items():
        if not isinstance(claim, dict):
            raise EditorDeliveryError("editor claim malformed")
        _snapshot, identity_key, _url, _sha = _identity(claim)
        if edition_key != identity_key or edition_key in seen_dates:
            raise EditorDeliveryError("editor claim date mismatch")
        if not _OWNER_RE.fullmatch(str(claim.get("claim_owner") or "")):
            raise EditorDeliveryError("editor claim owner malformed")
        _timestamp(claim.get("claimed_at"))
        armed = claim.get("transport_armed")
        armed_at = claim.get("transport_armed_at")
        if type(armed) is not bool or (armed and armed_at is None) or (
            not armed and armed_at is not None
        ):
            raise EditorDeliveryError("editor transport-arm evidence malformed")
        if armed:
            _timestamp(armed_at)
        status = claim.get("delivery_status")
        if status not in {
            STATUS_CLAIMED,
            STATUS_TRANSPORT_ARMED,
            STATUS_REJECTED,
            STATUS_AMBIGUOUS,
        }:
            raise EditorDeliveryError("editor claim delivery status malformed")
        if (status == STATUS_CLAIMED) != (armed is False):
            raise EditorDeliveryError("editor claim arm/status mismatch")
        ambiguous_at = claim.get("ambiguous_detected_at")
        rejected_at = claim.get("rejected_at")
        if status == STATUS_AMBIGUOUS:
            _timestamp(ambiguous_at)
        elif ambiguous_at is not None:
            raise EditorDeliveryError("unexpected Editor ambiguity timestamp")
        if status == STATUS_REJECTED:
            _timestamp(rejected_at)
            if (
                not str(claim.get("smtp_status") or "")
                or claim.get("smtp_status") in {"accepted", "not_attempted"}
                or type(claim.get("smtp_code")) is not int
                or claim.get("smtp_code") == 250
            ):
                raise EditorDeliveryError("Editor rejection evidence malformed")
        elif (
            rejected_at is not None
            or claim.get("smtp_status") != "not_attempted"
            or claim.get("smtp_code") is not None
        ):
            raise EditorDeliveryError("unexpected Editor SMTP result evidence")
        if (
            claim.get("generated") is not True
            or claim.get("published") is not True
            or claim.get("public_verified") is not True
            or claim.get("claimed") is not True
            or claim.get("sent") is not False
            or claim.get("duplicate_skipped") is not False
            or claim.get("failed") is not (
                status in {STATUS_REJECTED, STATUS_AMBIGUOUS}
            )
            or claim.get("reconciliation_required") is not (
                status == STATUS_AMBIGUOUS
            )
        ):
            raise EditorDeliveryError("editor claim evidence malformed")
    for record in reconciliations:
        if not isinstance(record, dict):
            raise EditorDeliveryError("editor reconciliation record malformed")
        _identity(record)
        if (
            record.get("action") not in {"mark_delivered", "release_retry"}
            or not str(record.get("authorized_by") or "").strip()
            or not _SHA_RE.fullmatch(
                str(record.get("operator_evidence_sha256") or "")
            )
            or not _OWNER_RE.fullmatch(str(record.get("prior_claim_owner") or ""))
        ):
            raise EditorDeliveryError("editor reconciliation evidence malformed")
        _timestamp(record.get("reconciled_at"))
        _timestamp(record.get("prior_transport_armed_at"))
    return deepcopy(value)


def load_state(path: Path | None = None) -> dict:
    target = Path(path) if path is not None else STATE_PATH
    if not target.exists():
        return empty_state()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EditorDeliveryError("editor delivery state unreadable") from exc
    return validate_state(payload)


def atomic_write_state(state: Mapping, path: Path | None = None) -> None:
    validated = validate_state(dict(state))
    target = Path(path) if path is not None else STATE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_tmp = tempfile.mkstemp(prefix=f".{target.name}.", dir=str(target.parent))
    temporary = Path(raw_tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(validated, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()


def has_success(state: Mapping, edition_key: str) -> bool:
    return any(
        record.get("edition_key") == edition_key
        for record in state.get("successful_deliveries", [])
        if isinstance(record, Mapping)
    )


def claim_snapshot(
    state: Mapping,
    identity: Mapping,
    *,
    claim_owner: str,
    claimed_at: datetime,
) -> tuple[dict, bool, str]:
    current = validate_state(dict(state))
    _snapshot_id, edition_key, _url, _sha = _identity(identity)
    if not _OWNER_RE.fullmatch(claim_owner):
        raise EditorDeliveryError("editor claim owner malformed")
    if has_success(current, edition_key):
        return current, False, "duplicate_skipped"
    existing = current["delivery_claims"].get(edition_key)
    now = claimed_at.astimezone(timezone.utc)
    if existing:
        if existing.get("delivery_status") == STATUS_REJECTED:
            del current["delivery_claims"][edition_key]
            existing = None
        elif existing.get("delivery_status") == STATUS_AMBIGUOUS:
            return current, False, STATUS_AMBIGUOUS
    if existing:
        if existing.get("transport_armed") is True:
            if (
                existing["review_snapshot_id"] == identity["review_snapshot_id"]
                and existing["claim_owner"] == claim_owner
            ):
                return current, True, STATUS_TRANSPORT_ARMED
            existing["delivery_status"] = STATUS_AMBIGUOUS
            existing["ambiguous_detected_at"] = claimed_at.isoformat(
                timespec="seconds"
            )
            existing["failed"] = True
            existing["reconciliation_required"] = True
            return validate_state(current), False, STATUS_AMBIGUOUS
        existing_at = _timestamp(existing["claimed_at"]).astimezone(timezone.utc)
        if now - existing_at < CLAIM_TTL:
            if (
                existing["review_snapshot_id"] == identity["review_snapshot_id"]
                and existing["claim_owner"] == claim_owner
            ):
                return current, True, STATUS_CLAIMED
            return current, False, "claimed_elsewhere"
        del current["delivery_claims"][edition_key]
    claim = {
        **dict(identity),
        "generated": True,
        "published": True,
        "public_verified": True,
        "claimed": True,
        "sent": False,
        "duplicate_skipped": False,
        "failed": False,
        "claim_owner": claim_owner,
        "claimed_at": claimed_at.isoformat(timespec="seconds"),
        "delivery_status": STATUS_CLAIMED,
        "transport_armed": False,
        "transport_armed_at": None,
        "ambiguous_detected_at": None,
        "rejected_at": None,
        "smtp_status": "not_attempted",
        "smtp_code": None,
        "reconciliation_required": False,
    }
    current["delivery_claims"][edition_key] = claim
    return validate_state(current), True, STATUS_CLAIMED


def arm_claim(
    state: Mapping,
    identity: Mapping,
    *,
    claim_owner: str,
    armed_at: datetime,
) -> dict:
    """Durably mark the point after which automatic resend is forbidden."""
    current = validate_state(dict(state))
    _snapshot_id, edition_key, _url, _sha = _identity(identity)
    claim = current["delivery_claims"].get(edition_key)
    if (
        not claim
        or claim.get("claim_owner") != claim_owner
        or any(
            claim.get(field) != identity.get(field)
            for field in (
                "review_snapshot_id",
                "edition_key",
                "editor_public_url",
                "manifest_sha256",
            )
        )
    ):
        raise EditorDeliveryError("exact Editor claim is missing or mismatched")
    if claim.get("delivery_status") == STATUS_TRANSPORT_ARMED:
        return current
    if claim.get("delivery_status") != STATUS_CLAIMED:
        raise EditorDeliveryError("Editor claim cannot be armed in current status")
    claim["transport_armed"] = True
    claim["transport_armed_at"] = armed_at.isoformat(timespec="seconds")
    claim["delivery_status"] = STATUS_TRANSPORT_ARMED
    return validate_state(current)


def record_rejected(
    state: Mapping,
    identity: Mapping,
    *,
    claim_owner: str,
    rejected_at: datetime,
    smtp_status: str,
    smtp_code: int,
) -> dict:
    """Persist explicit non-250 evidence; a later claim may safely retry."""
    current = validate_state(dict(state))
    _snapshot_id, edition_key, _url, _sha = _identity(identity)
    claim = current["delivery_claims"].get(edition_key)
    if (
        not claim
        or claim.get("claim_owner") != claim_owner
        or claim.get("delivery_status") != STATUS_TRANSPORT_ARMED
        or not smtp_status
        or smtp_status in {"accepted", "not_attempted"}
        or type(smtp_code) is not int
        or smtp_code == 250
    ):
        raise EditorDeliveryError("rejected Editor claim owner mismatch")
    claim["delivery_status"] = STATUS_REJECTED
    claim["rejected_at"] = rejected_at.isoformat(timespec="seconds")
    claim["smtp_status"] = smtp_status
    claim["smtp_code"] = smtp_code
    claim["failed"] = True
    return validate_state(current)


def record_success(
    state: Mapping,
    identity: Mapping,
    *,
    claim_owner: str,
    sent_at: datetime,
    smtp_status: str,
    smtp_code: int,
) -> dict:
    if smtp_status != "accepted" or type(smtp_code) is not int or smtp_code != 250:
        raise EditorDeliveryError("only exact SMTP DATA 250 records Editor success")
    current = validate_state(dict(state))
    _snapshot_id, edition_key, _url, _sha = _identity(identity)
    claim = current["delivery_claims"].get(edition_key)
    if (
        not claim
        or claim.get("claim_owner") != claim_owner
        or claim.get("transport_armed") is not True
        or claim.get("delivery_status") != STATUS_TRANSPORT_ARMED
        or any(claim.get(field) != identity.get(field) for field in (
            "review_snapshot_id", "edition_key", "editor_public_url", "manifest_sha256"
        ))
    ):
        raise EditorDeliveryError("exact Editor claim is missing or mismatched")
    success = {
        **dict(identity),
        "generated": True,
        "published": True,
        "public_verified": True,
        "claimed": True,
        "sent": True,
        "duplicate_skipped": False,
        "failed": False,
        "delivery_status": STATUS_SENT,
        "delivery_evidence_kind": "smtp_250",
        "claim_owner": claim_owner,
        "claimed_at": claim["claimed_at"],
        "sent_at": sent_at.isoformat(timespec="seconds"),
        "smtp_status": smtp_status,
        "smtp_code": smtp_code,
    }
    del current["delivery_claims"][edition_key]
    current["successful_deliveries"].append(success)
    return validate_state(current)


def reconcile_ambiguous(
    state: Mapping,
    identity: Mapping,
    *,
    action: str,
    authorized_by: str,
    operator_evidence: str,
    reconciled_at: datetime,
) -> dict:
    """Explicitly resolve an armed ambiguity; never called by scheduled runs.

    ``mark_delivered`` requires external operator evidence that the message was
    accepted/delivered. ``release_retry`` authorizes a future automatic claim
    while preserving an immutable audit record of that human decision.
    """
    current = validate_state(dict(state))
    _snapshot_id, edition_key, _url, _sha = _identity(identity)
    if action not in {"mark_delivered", "release_retry"}:
        raise EditorDeliveryError("Editor reconciliation action invalid")
    actor = str(authorized_by or "").strip()
    evidence = str(operator_evidence or "").strip()
    if len(actor) < 3 or len(evidence) < 12:
        raise EditorDeliveryError("Editor reconciliation authorization incomplete")
    claim = current["delivery_claims"].get(edition_key)
    if (
        not claim
        or claim.get("delivery_status") != STATUS_AMBIGUOUS
        or any(
            claim.get(field) != identity.get(field)
            for field in (
                "review_snapshot_id",
                "edition_key",
                "editor_public_url",
                "manifest_sha256",
            )
        )
    ):
        raise EditorDeliveryError("ambiguous exact Editor claim is missing")
    evidence_sha = hashlib.sha256(evidence.encode("utf-8")).hexdigest()
    reconciliation = {
        **dict(identity),
        "action": action,
        "authorized_by": actor,
        "operator_evidence_sha256": evidence_sha,
        "reconciled_at": reconciled_at.isoformat(timespec="seconds"),
        "prior_claim_owner": claim["claim_owner"],
        "prior_transport_armed_at": claim["transport_armed_at"],
    }
    current["reconciliation_history"].append(reconciliation)
    del current["delivery_claims"][edition_key]
    if action == "mark_delivered":
        current["successful_deliveries"].append(
            {
                **dict(identity),
                "generated": True,
                "published": True,
                "public_verified": True,
                "claimed": True,
                "sent": True,
                "duplicate_skipped": False,
                "failed": False,
                "delivery_status": STATUS_SENT,
                "delivery_evidence_kind": "operator_reconciled",
                "claim_owner": claim["claim_owner"],
                "claimed_at": claim["claimed_at"],
                "sent_at": reconciled_at.isoformat(timespec="seconds"),
                "smtp_status": "operator_reconciled",
                "smtp_code": None,
                "operator_evidence_sha256": evidence_sha,
                "reconciled_by": actor,
            }
        )
    return validate_state(current)
