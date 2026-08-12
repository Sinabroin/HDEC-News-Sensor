#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from r4_ops7_gate_contracts import (
    assert_no_broad_runtime_kill_switch,
    assert_scoped_workflow,
    injected_fault_result,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inject-broken-domain", default="")
    args = parser.parse_args()
    assert_no_broad_runtime_kill_switch()
    injected_fault_result("editor", args.inject_broken_domain)
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
