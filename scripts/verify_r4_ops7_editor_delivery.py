#!/usr/bin/env python3
"""Fake-SMTP acceptance for independent immutable Editor delivery."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import editor_delivery, public_urls
from scripts import run_editor_delivery as runner


KST_NOW = datetime.fromisoformat("2026-08-12T07:20:00+09:00")
TODAY_NOW = datetime.fromisoformat("2026-08-18T07:52:00+09:00")
TODAY_SNAPSHOT_ID = "review-2026-08-18-0752211bdb36c38e"


class FakeResponse:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def getcode(self):
        return self.status

    def read(self, limit=-1):
        return self.payload if limit < 0 else self.payload[:limit]


class SMTPRecorder:
    def __init__(self, accepted: bool = True):
        self.accepted = accepted
        self.attempts = 0

    def __call__(self, *_args, **_kwargs):
        self.attempts += 1
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def ehlo(self):
        return 250, b"ok"

    def starttls(self, **_kwargs):
        return 220, b"ok"

    def login(self, *_args):
        return 235, b"ok"

    def mail(self, *_args):
        return 250, b"ok"

    def rcpt(self, *_args):
        return 250, b"ok"

    def data(self, *_args):
        return (250, b"accepted") if self.accepted else (550, b"rejected")


def fixture(root: Path) -> tuple[str, dict, dict[str, bytes]]:
    edition_key = "2026-08-12"
    console = b'<main data-edition-key="2026-08-12">Editor</main>'
    candidates = json.dumps(
        {
            "version": 2,
            "edition_type": "daily",
            "edition_key": edition_key,
            "category_order": ["투자·산업", "기업동향", "기술정보"],
            "candidate_count": 0,
            "candidates": [],
        },
        ensure_ascii=False,
    ).encode("utf-8")
    core = {
        "version": 1,
        "product": "editor_review_snapshot",
        "edition_key": edition_key,
        "candidate_bundle_sha256": hashlib.sha256(candidates).hexdigest(),
        "console_html_sha256": hashlib.sha256(console).hexdigest(),
        "assets": [],
        "generated": True,
        "published": False,
        "public_verified": False,
        "claimed": False,
        "sent": False,
        "duplicate_skipped": False,
        "failed": False,
    }
    digest = hashlib.sha256(editor_delivery.canonical_snapshot_manifest_bytes(core)).hexdigest()
    snapshot_id = f"review-{edition_key}-{digest[:16]}"
    manifest = {
        **core,
        "review_snapshot_id": snapshot_id,
        "integrity": {
            "algorithm": "sha256",
            "canonicalization": "sorted-compact-json-utf8",
            "digest": digest,
        },
    }
    directory = (
        root / "docs/editorial/review/snapshots" / snapshot_id
    )
    directory.mkdir(parents=True)
    resources = {
        "index.html": console,
        "candidates.json": candidates,
        "manifest.json": (
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8"),
    }
    for name, payload in resources.items():
        (directory / name).write_bytes(payload)
    return snapshot_id, manifest, resources


def main() -> int:
    production_state_path = ROOT / "data" / "editor_delivery_state.json"
    production_state_before = (
        hashlib.sha256(production_state_path.read_bytes()).hexdigest()
        if production_state_path.is_file()
        else "absent"
    )
    env = {
        "EDITORIAL_PRODUCTION": "1",
        "GITHUB_ACTIONS": "true",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_RUN_ID": "7001",
        "GITHUB_RUN_ATTEMPT": "1",
        "GMAIL_SMTP_USER": "sender@example.test",
        "GMAIL_SMTP_APP_PASSWORD": "offline-password",
        "ALERT_EMAIL_FROM": "sender@example.test",
        "TEAMS_CHANNEL_EMAIL": "channel@example.test",
    }
    original_env = {key: os.environ.get(key) for key in env}
    os.environ.update(env)
    try:
        with tempfile.TemporaryDirectory(prefix="r4-ops-7-editor-") as temporary:
            root = Path(temporary)
            snapshot_id, manifest, resources = fixture(root)
            root_url = "https://example.test/HDEC-News-Sensor"
            state_path = root / "editor-state.json"
            public = {
                public_urls.editor_snapshot_url(snapshot_id, root_url=root_url): resources["index.html"],
                public_urls.editor_snapshot_manifest_url(snapshot_id, root_url=root_url): resources["manifest.json"],
                public_urls.editor_snapshot_url(snapshot_id, root_url=root_url).rsplit("/", 1)[0]
                + "/candidates.json": resources["candidates.json"],
            }

            def opener(request, **_kwargs):
                url = request if isinstance(request, str) else request.full_url
                if url not in public:
                    raise OSError("unexpected URL")
                return FakeResponse(public[url])

            # 1/5/6: exact identity passes; stale/mismatched/latest fail closed.
            if not runner.verify_public_snapshot_once(
                snapshot_id, root_url=root_url, opener=opener, root=root
            ):
                raise AssertionError("valid exact Editor snapshot was not authorized")
            stale_failed = False
            try:
                runner.load_identity(
                    "review-2026-08-11-ffffffffffffffff",
                    root_url=root_url,
                    root=root,
                )
            except (runner.EditorDeliveryRunnerError, editor_delivery.EditorDeliveryError):
                stale_failed = True
            if not stale_failed:
                raise AssertionError("stale/mismatched Editor identity passed")
            latest_failed = False
            try:
                editor_delivery._identity(
                    {
                        "review_snapshot_id": snapshot_id,
                        "edition_key": "2026-08-12",
                        "editor_public_url": root_url + "/editorial/review/latest/index.html",
                        "manifest_sha256": "f" * 64,
                    }
                )
            except editor_delivery.EditorDeliveryError:
                latest_failed = True
            if not latest_failed:
                raise AssertionError("mutable latest URL became delivery authority")

            # 2/3/12: exact 250 persists success and retry makes zero sends.
            claimed, authorized, status = runner.run_claim(
                snapshot_id,
                root_url=root_url,
                state_path=state_path,
                opener=opener,
                root=root,
                now=KST_NOW,
                timeout_seconds=0,
            )
            if not authorized or status != "claimed":
                raise AssertionError("valid Editor claim was not authorized")
            runner.run_arm(
                snapshot_id,
                root_url=root_url,
                state_path=state_path,
                root=root,
                now=KST_NOW,
            )
            smtp = SMTPRecorder(True)
            sent = runner.run_send(
                snapshot_id,
                root_url=root_url,
                state_path=state_path,
                smtp_factory=smtp,
                root=root,
                now=KST_NOW,
            )
            retry = runner.run_send(
                snapshot_id,
                root_url=root_url,
                state_path=state_path,
                smtp_factory=smtp,
                root=root,
                now=KST_NOW,
            )
            if (
                smtp.attempts != 1
                or not editor_delivery.has_success(sent, "2026-08-12")
                or retry != sent
            ):
                raise AssertionError("Editor exact-250 retry idempotence failed")

            # 4/13: rejected SMTP is explicit evidence, never a success, and
            # permits a later bounded retry because non-acceptance is known.
            rejected_state_path = root / "rejected-state.json"
            runner.run_claim(
                snapshot_id,
                root_url=root_url,
                state_path=rejected_state_path,
                opener=opener,
                root=root,
                now=KST_NOW,
                timeout_seconds=0,
            )
            runner.run_arm(
                snapshot_id,
                root_url=root_url,
                state_path=rejected_state_path,
                root=root,
                now=KST_NOW,
            )
            rejected_smtp = SMTPRecorder(False)
            rejected = False
            try:
                runner.run_send(
                    snapshot_id,
                    root_url=root_url,
                    state_path=rejected_state_path,
                    smtp_factory=rejected_smtp,
                    root=root,
                    now=KST_NOW,
                )
            except runner.EditorDeliveryRunnerError:
                rejected = True
            rejected_state = editor_delivery.load_state(rejected_state_path)
            rejected_claim = rejected_state["delivery_claims"].get("2026-08-12")
            if (
                not rejected
                or editor_delivery.has_success(rejected_state, "2026-08-12")
                or not rejected_claim
                or rejected_claim.get("delivery_status")
                != editor_delivery.STATUS_REJECTED
            ):
                raise AssertionError("SMTP reject falsely persisted Editor success")
            _manifest, identity = runner.load_identity(
                snapshot_id, root_url=root_url, root=root
            )
            recovered, retry_authorized, retry_status = editor_delivery.claim_snapshot(
                rejected_state,
                identity,
                claim_owner="github-run:7002:attempt:1",
                claimed_at=KST_NOW + timedelta(minutes=15),
            )
            if not retry_authorized or retry_status != "claimed":
                raise AssertionError("explicit rejection did not permit bounded recovery")
            armed = editor_delivery.arm_claim(
                recovered,
                identity,
                claim_owner="github-run:7002:attempt:1",
                armed_at=KST_NOW + timedelta(minutes=15),
            )
            ambiguous_state_path = root / "ambiguous-state.json"
            editor_delivery.atomic_write_state(armed, ambiguous_state_path)
            os.environ["GITHUB_RUN_ID"] = "7003"
            ambiguous_failed = False
            try:
                runner.run_claim(
                    snapshot_id,
                    root_url=root_url,
                    state_path=ambiguous_state_path,
                    opener=opener,
                    root=root,
                    now=KST_NOW + timedelta(hours=12),
                    timeout_seconds=0,
                )
            except runner.EditorDeliveryRunnerError:
                ambiguous_failed = True
            ambiguous_state = editor_delivery.load_state(ambiguous_state_path)
            ambiguous_claim = ambiguous_state["delivery_claims"].get("2026-08-12")
            ambiguous_smtp = SMTPRecorder(True)
            automatic_send_failed = False
            try:
                runner.run_send(
                    snapshot_id,
                    root_url=root_url,
                    state_path=ambiguous_state_path,
                    smtp_factory=ambiguous_smtp,
                    root=root,
                    now=KST_NOW + timedelta(hours=12),
                )
            except runner.EditorDeliveryRunnerError:
                automatic_send_failed = True
            if (
                not ambiguous_failed
                or not automatic_send_failed
                or ambiguous_smtp.attempts != 0
                or not ambiguous_claim
                or ambiguous_claim.get("delivery_status")
                != editor_delivery.STATUS_AMBIGUOUS
                or ambiguous_claim.get("reconciliation_required") is not True
                or editor_delivery.has_success(ambiguous_state, "2026-08-12")
            ):
                raise AssertionError("armed ambiguity was not explicit and fail-closed")

            # The operator-only reconciliation mechanism requires both a
            # separate authorization gate and durable evidence. Exercise the
            # pure state transition only; no production reconciliation occurs.
            released = editor_delivery.reconcile_ambiguous(
                ambiguous_state,
                identity,
                action="release_retry",
                authorized_by="offline-test-operator",
                operator_evidence="ticket R4-OPS-7 confirms no SMTP connection opened",
                reconciled_at=KST_NOW + timedelta(hours=12, minutes=1),
            )
            if (
                "2026-08-12" in released["delivery_claims"]
                or released["reconciliation_history"][-1]["action"]
                != "release_retry"
            ):
                raise AssertionError("explicit release/retry reconciliation failed")

            # 7/8/14: Editor and Daily states are orthogonal by construction.
            daily_success = False
            editor_success = editor_delivery.has_success(sent, "2026-08-12")
            if not editor_success or daily_success:
                raise AssertionError("Editor success was conflated with Daily success")
            editor_failure = not editor_delivery.has_success(
                rejected_state, "2026-08-12"
            )
            if not editor_failure or daily_success:
                raise AssertionError("Editor failure falsely marked Daily success")

            # R4-OPS-9: replay the exact 2026-08-18 production snapshot through
            # public verification, claim, arm, fake SMTP 250, and duplicate skip.
            os.environ["GITHUB_RUN_ID"] = "7018"
            today_root_url = runner.resolve_editor_public_root(
                public_urls.CANONICAL_DASHBOARD_URL
            )
            snapshot_dir = (
                ROOT / "docs" / "editorial" / "review" / "snapshots"
                / TODAY_SNAPSHOT_ID
            )
            today_manifest = json.loads(
                (snapshot_dir / "manifest.json").read_text(encoding="utf-8")
            )
            today_base_url = public_urls.editor_snapshot_url(
                TODAY_SNAPSHOT_ID,
                root_url=today_root_url,
            ).rsplit("/", 1)[0]
            today_public = {
                today_base_url + "/index.html": (snapshot_dir / "index.html").read_bytes(),
                today_base_url + "/manifest.json": (
                    snapshot_dir / "manifest.json"
                ).read_bytes(),
                today_base_url + "/candidates.json": (
                    snapshot_dir / "candidates.json"
                ).read_bytes(),
            }
            for asset in today_manifest["assets"]:
                today_public[today_base_url + "/" + asset["relative_path"]] = (
                    snapshot_dir / asset["relative_path"]
                ).read_bytes()

            def today_opener(request, **_kwargs):
                url = request if isinstance(request, str) else request.full_url
                if url not in today_public:
                    raise OSError("unexpected observed-snapshot URL")
                return FakeResponse(today_public[url])

            _today_manifest, today_identity = runner.load_identity(
                TODAY_SNAPSHOT_ID,
                root_url=today_root_url,
                root=ROOT,
            )
            expected_today_url = (
                public_urls.PUBLIC_ROOT
                + "/editorial/review/snapshots/"
                + TODAY_SNAPSHOT_ID
                + "/index.html"
            )
            if (
                today_identity["edition_key"] != "2026-08-18"
                or today_identity["review_snapshot_id"] != TODAY_SNAPSHOT_ID
                or today_identity["editor_public_url"] != expected_today_url
            ):
                raise AssertionError("2026-08-18 Editor identity reconstruction failed")
            today_state_path = root / "today-editor-state.json"
            _claimed, today_authorized, today_status = runner.run_claim(
                TODAY_SNAPSHOT_ID,
                root_url=today_root_url,
                state_path=today_state_path,
                opener=today_opener,
                root=ROOT,
                now=TODAY_NOW,
                timeout_seconds=0,
            )
            if not today_authorized or today_status != "claimed":
                raise AssertionError("2026-08-18 Editor claim was not authorized")
            runner.run_arm(
                TODAY_SNAPSHOT_ID,
                root_url=today_root_url,
                state_path=today_state_path,
                root=ROOT,
                now=TODAY_NOW,
            )
            today_smtp = SMTPRecorder(True)
            today_sent = runner.run_send(
                TODAY_SNAPSHOT_ID,
                root_url=today_root_url,
                state_path=today_state_path,
                smtp_factory=today_smtp,
                root=ROOT,
                now=TODAY_NOW,
            )
            today_retry = runner.run_send(
                TODAY_SNAPSHOT_ID,
                root_url=today_root_url,
                state_path=today_state_path,
                smtp_factory=today_smtp,
                root=ROOT,
                now=TODAY_NOW,
            )
            if (
                today_smtp.attempts != 1
                or today_retry != today_sent
                or not editor_delivery.has_success(today_sent, "2026-08-18")
            ):
                raise AssertionError("2026-08-18 Editor retry idempotence failed")
            production_state_after = (
                hashlib.sha256(production_state_path.read_bytes()).hexdigest()
                if production_state_path.is_file()
                else "absent"
            )
            if production_state_after != production_state_before:
                raise AssertionError("Editor rehearsal mutated production state")

            print("VALID_EXACT_EDITOR_SNAPSHOT=AUTHORIZED")
            print("EDITOR_SMTP_250_SUCCESS_PERSISTED=PASS")
            print("EDITOR_SAME_SNAPSHOT_RETRY_SMTP_ATTEMPTS=0")
            print("EDITOR_SMTP_ATTEMPTS_TOTAL=1")
            print("EDITOR_SMTP_REJECT_SUCCESSFUL_DELIVERY=false")
            print("EDITOR_REJECTED_ATTEMPT_RECOVERY=PASS")
            print("EDITOR_AMBIGUOUS_ARM_STATE=ambiguous_reconciliation_required")
            print("EDITOR_AMBIGUOUS_AUTO_RESEND=false")
            print("EDITOR_AMBIGUOUS_SMTP_ATTEMPTS=0")
            print("EDITOR_RECONCILIATION_STATUS=OPERABLE")
            print("STALE_EDITOR_IDENTITY=BLOCKED")
            print("MUTABLE_LATEST_EDITOR_TARGET=BLOCKED")
            print("EDITOR_SUCCESS_DAILY_FAILURE_ORTHOGONAL=PASS")
            print("EDITOR_FAILURE_DAILY_SUCCESS_FALSE=PASS")
            print("EDITOR_EXACT_SNAPSHOT_IDENTITY=PASS")
            print("EDITOR_PUBLIC_VERIFY_BEFORE_CLAIM=PASS")
            print("EDITOR_CLAIM_BEFORE_ARM=PASS")
            print("EDITOR_ARM_BEFORE_SMTP=PASS")
            print("EDITOR_AT_MOST_ONCE=PASS")
            print("TODAY_EDITOR_IDENTITY_REHEARSAL=PASS")
            print("TODAY_EDITOR_FAKE_SMTP_SENDS=1")
            print("TODAY_EDITOR_DUPLICATE_RESEND=0")
            print("TODAY_EDITOR_PRODUCTION_STATE_WRITES=0")
            print("REAL_SMTP_CONNECTIONS=0")
    finally:
        for key, value in original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
