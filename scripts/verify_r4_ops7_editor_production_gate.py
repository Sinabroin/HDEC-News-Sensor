#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import public_urls
import run_editor_delivery as delivery_runner

from r4_ops7_gate_contracts import (
    assert_no_broad_runtime_kill_switch,
    assert_scoped_workflow,
    injected_fault_result,
)


OBSERVED_SNAPSHOT_ID = "review-2026-08-18-0752211bdb36c38e"


def assert_editor_public_root_recovery() -> None:
    report_root = delivery_runner.resolve_editor_public_root(
        public_urls.CANONICAL_DASHBOARD_URL
    )
    canonical_root = delivery_runner.resolve_editor_public_root(
        public_urls.PUBLIC_ROOT
    )
    compatible_report_root = delivery_runner.resolve_editor_public_root(
        public_urls.PUBLIC_ROOT + "/daily/latest.html"
    )
    if report_root != public_urls.PUBLIC_ROOT:
        raise AssertionError("Editor dashboard report page did not derive public root")
    if canonical_root != report_root or compatible_report_root != report_root:
        raise AssertionError("Editor public-root inputs did not converge")

    expected_url = public_urls.editor_snapshot_url(
        OBSERVED_SNAPSHOT_ID,
        root_url=public_urls.PUBLIC_ROOT,
    )
    _manifest, report_identity = delivery_runner.load_identity(
        OBSERVED_SNAPSHOT_ID,
        root_url=report_root,
        root=ROOT,
    )
    _manifest, root_identity = delivery_runner.load_identity(
        OBSERVED_SNAPSHOT_ID,
        root_url=canonical_root,
        root=ROOT,
    )
    manifest_path = delivery_runner.snapshot_manifest_path(
        OBSERVED_SNAPSHOT_ID,
        root=ROOT,
    )
    if (
        report_identity != root_identity
        or report_identity["edition_key"] != "2026-08-18"
        or report_identity["review_snapshot_id"] != OBSERVED_SNAPSHOT_ID
        or report_identity["editor_public_url"] != expected_url
        or report_identity["manifest_sha256"]
        != hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    ):
        raise AssertionError("observed Editor snapshot identity changed")
    if "/latest" in expected_url:
        raise AssertionError("mutable latest leaked into immutable Editor URL")

    rejected = {}
    unsafe_inputs = {
        "wrong_product": (
            "https://guides.playground-aidesignlab.co.kr/Other-Product/"
            "daily/latest.html"
        ),
        "external": "https://attacker.example/HDEC-News-Sensor/daily/latest.html",
        "malformed": "not-a-public-url",
    }
    for label, value in unsafe_inputs.items():
        try:
            delivery_runner.resolve_editor_public_root(value)
        except delivery_runner.EditorDeliveryRunnerError:
            rejected[label] = True
        else:
            rejected[label] = False
    if not all(rejected.values()):
        raise AssertionError(f"unsafe Editor public root accepted: {rejected}")

    print("EDITOR_REPORT_PAGE_TO_ROOT=PASS")
    print("EDITOR_CANONICAL_ROOT_INPUT=PASS")
    print("EDITOR_IMMUTABLE_SNAPSHOT_URL=PASS")
    print("EDITOR_LATEST_PATH_LEAK=0")
    print("EDITOR_WRONG_PRODUCT_ROOT_REJECTED=PASS")
    print("EDITOR_MALFORMED_ROOT_REJECTED=PASS")
    print("EDITOR_UNSAFE_ROOT_FAIL_CLOSED=PASS")
    print("EDITOR_SNAPSHOT_IDENTITY_UNCHANGED=PASS")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inject-broken-domain", default="")
    args = parser.parse_args()
    assert_no_broad_runtime_kill_switch()
    injected_fault_result("editor", args.inject_broken_domain)
    assert_editor_public_root_recovery()
    assert_scoped_workflow(
        "editor",
        required=(
            "verify_editorial_review_console.py",
            "verify_r4_ops7_editor_production_gate.py",
            "run_editor_delivery.py",
            "--verify-public",
            "--claim",
            "--arm",
            "--send",
            "editor_delivery_state.json",
            "steps.arm.outputs.send_authorized == 'true'",
            "if: always() && steps.send.outcome != 'skipped'",
            "ambiguous_reconciliation_required",
            "automatic_resend=false smtp_connections=0",
            "if: always() && steps.claim.outputs.state_changed == 'true'",
        ),
        forbidden=(
            "verify_teams_ai_push_production.py",
            "verify_dashboard_freshness.py",
            "verify_daily_editor_deep_link.py",
        ),
    )
    runner_source = (
        Path(__file__).resolve().parent / "run_editor_delivery.py"
    ).read_text(encoding="utf-8")
    for token in (
        "--reconcile-mark-delivered",
        "--reconcile-release-retry",
        "EDITOR_RECONCILIATION_AUTHORIZED",
        "--operator-evidence-file",
    ):
        if token not in runner_source:
            raise AssertionError(f"Editor reconciliation mechanism missing: {token}")
    print("EDITOR_GATE=PASS EDITOR_INDEPENDENT_DELIVERY_PATH=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
