#!/usr/bin/env python3
"""Publish-proof, claim, and send one immutable Review Console notification."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from email.message import EmailMessage
from email.utils import parseaddr
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
for path in (ROOT, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from app import editor_delivery, editorial_briefings, public_urls  # noqa: E402
from app.editorial_briefings import KST, valid_http_url  # noqa: E402


class EditorDeliveryRunnerError(RuntimeError):
    pass


def _now() -> datetime:
    return datetime.now(KST)


def _github_output(name: str, value: str) -> None:
    target = os.environ.get("GITHUB_OUTPUT", "").strip()
    if target:
        with Path(target).open("a", encoding="utf-8") as handle:
            handle.write(f"{name}={value}\n")


def _require_production_gate() -> None:
    if os.environ.get("EDITORIAL_PRODUCTION") != "1":
        raise EditorDeliveryRunnerError("EDITORIAL_PRODUCTION=1 is required")
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise EditorDeliveryRunnerError("Editor production delivery is runner-only")
    if os.environ.get("GITHUB_REF") != "refs/heads/main":
        raise EditorDeliveryRunnerError("Editor production delivery is main-only")


def _claim_owner() -> str:
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "")
    owner = f"github-run:{run_id}:attempt:{attempt}"
    if not re.fullmatch(r"github-run:[1-9][0-9]*:attempt:[1-9][0-9]*", owner):
        raise EditorDeliveryRunnerError("GitHub claim identity is missing")
    return owner


def resolve_editor_public_root(report_url: str | None) -> str:
    """Resolve production REPORT_URL through the shared Daily root contract."""
    configured = public_urls.PUBLIC_ROOT if report_url is None else report_url
    try:
        root_url = editorial_briefings.derive_public_root(configured)
    except editorial_briefings.EditorialError as exc:
        raise EditorDeliveryRunnerError("Editor public root is invalid") from exc
    if root_url != public_urls.PUBLIC_ROOT:
        raise EditorDeliveryRunnerError("Editor public root is not canonical")
    return root_url


def snapshot_manifest_path(snapshot_id: str, *, root: Path = ROOT) -> Path:
    if not public_urls.parse_editor_snapshot_id(snapshot_id):
        raise EditorDeliveryRunnerError("invalid review snapshot id")
    return (
        root
        / "docs"
        / "editorial"
        / "review"
        / "snapshots"
        / snapshot_id
        / "manifest.json"
    )


def load_identity(
    snapshot_id: str,
    *,
    root_url: str,
    root: Path = ROOT,
) -> tuple[dict, dict]:
    path = snapshot_manifest_path(snapshot_id, root=root)
    manifest = editor_delivery.load_snapshot_manifest(path, snapshot_id)
    public_url = public_urls.editor_snapshot_url(snapshot_id, root_url=root_url)
    if not public_url or "/latest" in public_url:
        raise EditorDeliveryRunnerError("immutable Editor public URL is invalid")
    identity = {
        "review_snapshot_id": snapshot_id,
        "edition_key": manifest["edition_key"],
        "editor_public_url": public_url,
        "manifest_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    # Reuse the strict state identity validator without mutating state.
    editor_delivery._identity(identity)
    return manifest, identity


def verify_local_snapshot(
    manifest: dict,
    *,
    root: Path = ROOT,
) -> None:
    snapshot_id = manifest["review_snapshot_id"]
    directory = snapshot_manifest_path(snapshot_id, root=root).parent
    expected = {
        "index.html": manifest["console_html_sha256"],
        "candidates.json": manifest["candidate_bundle_sha256"],
    }
    for name, digest in expected.items():
        try:
            payload = (directory / name).read_bytes()
        except OSError as exc:
            raise EditorDeliveryRunnerError("Editor snapshot resource missing") from exc
        if hashlib.sha256(payload).hexdigest() != digest:
            raise EditorDeliveryRunnerError("Editor snapshot resource digest mismatch")
    for asset in manifest["assets"]:
        try:
            payload = (directory / asset["relative_path"]).read_bytes()
        except OSError as exc:
            raise EditorDeliveryRunnerError("Editor snapshot asset missing") from exc
        if (
            hashlib.sha256(payload).hexdigest() != asset["sha256"]
            or len(payload) != asset["byte_size"]
        ):
            raise EditorDeliveryRunnerError("Editor snapshot asset digest mismatch")


def _response_bytes(url: str, *, opener: Callable | None = None) -> bytes:
    if not valid_http_url(url) or "/latest" in url:
        raise EditorDeliveryRunnerError("mutable or invalid Editor resource URL")
    open_url = opener or urllib.request.urlopen
    try:
        response = open_url(url, timeout=15)
        with response:
            status = int(getattr(response, "status", response.getcode()))
            if status != 200:
                raise EditorDeliveryRunnerError("Editor resource did not return 200")
            return response.read(5_000_001)
    except (OSError, TimeoutError, urllib.error.URLError, ValueError) as exc:
        raise EditorDeliveryRunnerError("Editor public resource unavailable") from exc


def verify_public_snapshot_once(
    snapshot_id: str,
    *,
    root_url: str,
    opener: Callable | None = None,
    root: Path = ROOT,
) -> bool:
    manifest, identity = load_identity(snapshot_id, root_url=root_url, root=root)
    verify_local_snapshot(manifest, root=root)
    base = identity["editor_public_url"].rsplit("/", 1)[0]
    try:
        public_manifest_bytes = _response_bytes(
            public_urls.editor_snapshot_manifest_url(snapshot_id, root_url=root_url),
            opener=opener,
        )
        if hashlib.sha256(public_manifest_bytes).hexdigest() != identity["manifest_sha256"]:
            return False
        public_manifest = editor_delivery.validate_snapshot_manifest(
            json.loads(public_manifest_bytes.decode("utf-8"))
        )
        if public_manifest["review_snapshot_id"] != snapshot_id:
            return False
        resources = (
            (identity["editor_public_url"], manifest["console_html_sha256"]),
            (base + "/candidates.json", manifest["candidate_bundle_sha256"]),
        )
        for url, digest in resources:
            if hashlib.sha256(_response_bytes(url, opener=opener)).hexdigest() != digest:
                return False
        for asset in manifest["assets"]:
            payload = _response_bytes(base + "/" + asset["relative_path"], opener=opener)
            if (
                hashlib.sha256(payload).hexdigest() != asset["sha256"]
                or len(payload) != asset["byte_size"]
            ):
                return False
    except (EditorDeliveryRunnerError, editor_delivery.EditorDeliveryError, ValueError):
        return False
    return True


def verify_public_snapshot(
    snapshot_id: str,
    *,
    root_url: str,
    opener: Callable | None = None,
    root: Path = ROOT,
    timeout_seconds: int = 300,
    interval_seconds: int = 10,
    sleeper: Callable[[float], None] = time.sleep,
) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while True:
        if verify_public_snapshot_once(
            snapshot_id,
            root_url=root_url,
            opener=opener,
            root=root,
        ):
            return True
        if time.monotonic() >= deadline:
            return False
        sleeper(min(interval_seconds, max(0.0, deadline - time.monotonic())))


def run_claim(
    snapshot_id: str,
    *,
    root_url: str,
    state_path: Path | None = None,
    opener: Callable | None = None,
    root: Path = ROOT,
    now: datetime | None = None,
    timeout_seconds: int = 300,
    interval_seconds: int = 10,
    sleeper: Callable[[float], None] = time.sleep,
) -> tuple[dict, bool, str]:
    _require_production_gate()
    manifest, identity = load_identity(snapshot_id, root_url=root_url, root=root)
    verify_local_snapshot(manifest, root=root)
    if not verify_public_snapshot(
        snapshot_id,
        root_url=root_url,
        opener=opener,
        root=root,
        timeout_seconds=timeout_seconds,
        interval_seconds=interval_seconds,
        sleeper=sleeper,
    ):
        raise EditorDeliveryRunnerError("exact Editor snapshot public verification failed")
    state = editor_delivery.load_state(state_path)
    updated, authorized, status = editor_delivery.claim_snapshot(
        state,
        identity,
        claim_owner=_claim_owner(),
        claimed_at=now or _now(),
    )
    if updated != state:
        editor_delivery.atomic_write_state(updated, state_path)
    status_snapshot_id = snapshot_id
    if status == editor_delivery.STATUS_AMBIGUOUS:
        ambiguous_claim = updated["delivery_claims"].get(identity["edition_key"], {})
        status_snapshot_id = str(
            ambiguous_claim.get("review_snapshot_id") or snapshot_id
        )
    _github_output("state_changed", str(updated != state).lower())
    _github_output("send_authorized", str(authorized).lower())
    _github_output("editor_snapshot_id", status_snapshot_id)
    _github_output("edition", identity["edition_key"])
    _github_output("delivery_status", status)
    print(
        f"editor_claim status={status} generated=true published=true "
        f"public_verified=true claimed={str(authorized).lower()} sent=false"
    )
    if status == editor_delivery.STATUS_AMBIGUOUS:
        print(
            "editor_delivery_status=ambiguous_reconciliation_required "
            f"edition={identity['edition_key']} snapshot_id={status_snapshot_id} "
            "automatic_resend=false smtp_connections=0"
        )
        raise EditorDeliveryRunnerError(
            "armed Editor delivery requires explicit operator reconciliation: "
            f"edition={identity['edition_key']} snapshot_id={status_snapshot_id}"
        )
    return updated, authorized, status


def build_editor_message(identity: dict, from_address: str, recipient: str) -> EmailMessage:
    if "/latest" in identity["editor_public_url"]:
        raise EditorDeliveryRunnerError("mutable latest Editor URL rejected")
    message = EmailMessage()
    message["Subject"] = f"[HDEC AI Daily Brief 편집기] {identity['edition_key']}"
    message["From"] = from_address
    message["To"] = recipient
    message.set_content(
        "Daily Brief 편집기에서 열기\n" + identity["editor_public_url"]
    )
    message.add_alternative(
        '<p><strong>Daily Brief 편집기에서 열기</strong></p>'
        f'<p><a href="{identity["editor_public_url"]}">정확한 Editor 스냅샷 열기</a></p>',
        subtype="html",
    )
    return message


def run_arm(
    snapshot_id: str,
    *,
    root_url: str,
    state_path: Path | None = None,
    root: Path = ROOT,
    now: datetime | None = None,
) -> dict:
    """Persist the no-auto-resend boundary before any SMTP connection."""
    _require_production_gate()
    _manifest, identity = load_identity(snapshot_id, root_url=root_url, root=root)
    state = editor_delivery.load_state(state_path)
    updated = editor_delivery.arm_claim(
        state,
        identity,
        claim_owner=_claim_owner(),
        armed_at=now or _now(),
    )
    if updated != state:
        editor_delivery.atomic_write_state(updated, state_path)
    _github_output("state_changed", str(updated != state).lower())
    _github_output("send_authorized", "true")
    _github_output("delivery_status", editor_delivery.STATUS_TRANSPORT_ARMED)
    print("editor_transport_armed=true smtp_connections=0")
    return updated


def _address(name: str) -> str:
    value = os.environ.get(name, "").strip()
    _display, address = parseaddr(value)
    if not address or address != value or "@" not in address:
        raise EditorDeliveryRunnerError(f"{name} is missing or invalid")
    return address


def run_send(
    snapshot_id: str,
    *,
    root_url: str,
    state_path: Path | None = None,
    smtp_factory=None,
    root: Path = ROOT,
    now: datetime | None = None,
) -> dict:
    _require_production_gate()
    _manifest, identity = load_identity(snapshot_id, root_url=root_url, root=root)
    state = editor_delivery.load_state(state_path)
    if editor_delivery.has_success(state, identity["edition_key"]):
        _github_output("state_changed", "false")
        _github_output("delivery_status", "duplicate_skipped")
        print("editor_send status=duplicate_skipped smtp_connections=0")
        return state
    owner = _claim_owner()
    claim = state["delivery_claims"].get(identity["edition_key"])
    if not claim or claim.get("claim_owner") != owner or any(
        claim.get(field) != identity[field]
        for field in ("review_snapshot_id", "edition_key", "editor_public_url", "manifest_sha256")
    ):
        raise EditorDeliveryRunnerError("exact Editor delivery claim missing")
    if claim.get("transport_armed") is not True:
        raise EditorDeliveryRunnerError("Editor transport is not durably armed")
    if claim.get("delivery_status") != editor_delivery.STATUS_TRANSPORT_ARMED:
        raise EditorDeliveryRunnerError(
            "Editor delivery is not automatically sendable: "
            f"status={claim.get('delivery_status') or 'unknown'}"
        )
    smtp_user = _address("GMAIL_SMTP_USER")
    from_address = _address("ALERT_EMAIL_FROM")
    recipient = _address("TEAMS_CHANNEL_EMAIL")
    password = os.environ.get("GMAIL_SMTP_APP_PASSWORD", "")
    if not password:
        raise EditorDeliveryRunnerError("GMAIL_SMTP_APP_PASSWORD is missing")
    from send_email_alert import DeliveryTarget, deliver_email_message

    result = deliver_email_message(
        build_editor_message(identity, from_address, recipient),
        DeliveryTarget("editor_review_snapshot", recipient, "teams_channel"),
        smtp_user,
        password,
        from_address,
        smtp_factory=smtp_factory,
    )
    if result.smtp_status != "accepted" or result.smtp_code != 250:
        rejected = editor_delivery.record_rejected(
            state,
            identity,
            claim_owner=owner,
            rejected_at=now or _now(),
            smtp_status=result.smtp_status,
            smtp_code=(
                result.smtp_code if type(result.smtp_code) is int else 0
            ),
        )
        editor_delivery.atomic_write_state(rejected, state_path)
        _github_output("state_changed", "true")
        _github_output("delivery_status", editor_delivery.STATUS_REJECTED)
        print(
            f"editor_send status=rejected smtp_status={result.smtp_status} "
            f"smtp_code={result.smtp_code if result.smtp_code is not None else 'none'}"
        )
        raise EditorDeliveryRunnerError("Editor SMTP delivery rejected")
    updated = editor_delivery.record_success(
        state,
        identity,
        claim_owner=owner,
        sent_at=now or _now(),
        smtp_status=result.smtp_status,
        smtp_code=result.smtp_code,
    )
    editor_delivery.atomic_write_state(updated, state_path)
    _github_output("state_changed", "true")
    _github_output("delivery_status", "sent")
    print(
        "editor_send status=sent generated=true published=true public_verified=true "
        "claimed=true sent=true duplicate_skipped=false failed=false "
        "smtp_status=accepted smtp_code=250"
    )
    return updated


def run_reconcile(
    snapshot_id: str,
    *,
    root_url: str,
    action: str,
    authorized_by: str,
    evidence_file: Path,
    state_path: Path | None = None,
    root: Path = ROOT,
    now: datetime | None = None,
) -> dict:
    """Operator-only ambiguity resolution; scheduled workflows never call it.

    Safe production usage is intentionally two-step: run this command with an
    explicit authorization environment and evidence file, inspect the exact
    state diff, then commit/push that state through the operator change path.
    This command never opens SMTP or sends any message itself.
    """
    _require_production_gate()
    if os.environ.get("EDITOR_RECONCILIATION_AUTHORIZED") != "1":
        raise EditorDeliveryRunnerError(
            "EDITOR_RECONCILIATION_AUTHORIZED=1 is required"
        )
    _manifest, identity = load_identity(snapshot_id, root_url=root_url, root=root)
    try:
        evidence = evidence_file.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise EditorDeliveryRunnerError("operator evidence file is unreadable") from exc
    state = editor_delivery.load_state(state_path)
    updated = editor_delivery.reconcile_ambiguous(
        state,
        identity,
        action=action,
        authorized_by=authorized_by,
        operator_evidence=evidence,
        reconciled_at=now or _now(),
    )
    editor_delivery.atomic_write_state(updated, state_path)
    _github_output("state_changed", "true")
    _github_output("delivery_status", f"reconciled_{action}")
    _github_output("editor_snapshot_id", snapshot_id)
    _github_output("edition", identity["edition_key"])
    print(
        f"editor_reconciliation status=reconciled_{action} "
        f"edition={identity['edition_key']} snapshot_id={snapshot_id} "
        "smtp_connections=0 automatic_send=false"
    )
    return updated


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot-id", required=True)
    parser.add_argument("--state-path", type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--verify-public", action="store_true")
    mode.add_argument("--claim", action="store_true")
    mode.add_argument("--arm", action="store_true")
    mode.add_argument("--send", action="store_true")
    mode.add_argument(
        "--reconcile-mark-delivered",
        action="store_true",
        help="operator-only: record externally evidenced delivery",
    )
    mode.add_argument(
        "--reconcile-release-retry",
        action="store_true",
        help="operator-only: release ambiguity for a later retry",
    )
    parser.add_argument("--authorized-by", default="")
    parser.add_argument("--operator-evidence-file", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    _require_production_gate()
    root_url = resolve_editor_public_root(os.environ.get("REPORT_URL"))
    if args.verify_public:
        if not verify_public_snapshot(args.snapshot_id, root_url=root_url):
            raise EditorDeliveryRunnerError("exact Editor snapshot public verification failed")
        _github_output("public_verified", "true")
        print("editor_public_verified=true generated=true published=true public_verified=true")
        return 0
    if args.claim:
        run_claim(args.snapshot_id, root_url=root_url, state_path=args.state_path)
        return 0
    if args.arm:
        run_arm(args.snapshot_id, root_url=root_url, state_path=args.state_path)
        return 0
    if args.send:
        run_send(args.snapshot_id, root_url=root_url, state_path=args.state_path)
        return 0
    if not args.authorized_by or args.operator_evidence_file is None:
        raise EditorDeliveryRunnerError(
            "reconciliation requires --authorized-by and --operator-evidence-file"
        )
    run_reconcile(
        args.snapshot_id,
        root_url=root_url,
        action=(
            "mark_delivered"
            if args.reconcile_mark_delivered
            else "release_retry"
        ),
        authorized_by=args.authorized_by,
        evidence_file=args.operator_evidence_file,
        state_path=args.state_path,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
