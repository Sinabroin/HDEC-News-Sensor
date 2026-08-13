#!/usr/bin/env python3
from __future__ import annotations

import argparse

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
    injected_fault_result("watch", args.inject_broken_domain)
    assert_scoped_workflow(
        "watch",
        required=(
            "verify_teams_ai_push_production.py",
            "verify_teams_major_media_gate.py",
            "verify_teams_strict_source_gate.py",
            "verify_stock_market_hard_exclusion.py",
            "verify_r4_ops7_watch_production_gate.py",
        ),
        forbidden=(
            "verify_daily_editor_deep_link.py",
            "verify_editorial_review_console.py",
            "verify_dashboard_freshness.py",
        ),
    )
    print("WATCH_GATE=PASS WATCH_GATE_FAULT_ISOLATED=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
