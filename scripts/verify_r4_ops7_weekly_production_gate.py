#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import editorial_briefings
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
    injected_fault_result("weekly", args.inject_broken_domain)
    assert_scoped_workflow(
        "weekly",
        required=(
            "verify_weekly_tni_reference_parity.py",
            "verify_r4_ops7_real_photo_gate.py",
            "verify_r4_ops7_weekly_production_gate.py",
            "Verify public exact Weekly page and image assets before claim",
        ),
        forbidden=(
            "verify_daily_editor_deep_link.py",
            "verify_teams_ai_push_production.py",
            "verify_dashboard_freshness.py",
        ),
    )
    all_fallback = {
        "article_count": 12,
        "real_article_photo_count": 0,
        "fallback_visual_count": 12,
        "image_materialization_failed_count": 0,
        "image_quality_rejected_count": 0,
    }
    if not editorial_briefings.production_image_gate_error(all_fallback):
        raise AssertionError("Weekly all-fallback production gate was weakened")
    print("WEEKLY_GATE=PASS WEEKLY_REAL_PHOTO_GATE=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
