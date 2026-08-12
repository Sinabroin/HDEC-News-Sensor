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
    injected_fault_result("refresh", args.inject_broken_domain)
    assert_scoped_workflow(
        "refresh",
        required=(
            "verify_news_censor.py",
            "verify_news_censor_image_coverage.py",
            "verify_news_censor_verified_state.py",
            "verify_dashboard_freshness.py",
            "verify_scheduled_refresh_and_telegram.py",
            "verify_r4_ops7_refresh_production_gate.py",
        ),
        forbidden=(
            "verify_daily_editor_deep_link.py",
            "verify_editorial_review_console.py",
            "verify_teams_ai_push_production.py",
        ),
    )
    print("REFRESH_GATE=PASS REFRESH_GATE_FAULT_ISOLATED=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
