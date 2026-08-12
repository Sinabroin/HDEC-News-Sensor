#!/usr/bin/env python3
"""Report Tier-A/Tier-B Watch quality from a read-only live brief artifact."""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import publisher_direct, source_priority, teams_push_state  # noqa: E402
from app.teams_ai_push import (  # noqa: E402
    SOURCE_GATE_MAJOR_SECONDARY,
    SOURCE_GATE_PRIMARY_10,
    SOURCE_GATE_SECONDARY_3,
    apply_major_media_first_gate,
    select_teams_push_from_artifact_with_audit,
)

TIER_A = {"primary_10", "secondary_3"}
TIER_B = {"major_secondary"}


def _tier(article: dict) -> str:
    return str(source_priority.publisher_delivery_tier(
        str(article.get("source") or ""),
        publisher_direct.publisher_url(article),
    ).get("tier") or "")


def _title_key(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[^0-9a-z가-힣]+", "", text)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--external-sweep", type=Path)
    args = parser.parse_args()
    payload = json.loads(args.artifact.read_text(encoding="utf-8"))
    health = payload.get("collector_health") or {}
    rows = [
        row for row in payload.get("news_censor_display_articles") or []
        if isinstance(row, dict)
    ]

    candidates, _policy_audit = select_teams_push_from_artifact_with_audit(
        payload, max_articles=None
    )
    policy_a = sum(_tier(dict(item.article)) in TIER_A for item in candidates)
    policy_b = sum(_tier(dict(item.article)) in TIER_B for item in candidates)
    gate = apply_major_media_first_gate(
        candidates,
        state=teams_push_state.empty_state(),
        run_cap=5,
        now_iso_value=str(payload.get("generated_at") or ""),
    )
    selected_a = sum(
        item.gate.gate_class in {SOURCE_GATE_PRIMARY_10, SOURCE_GATE_SECONDARY_3}
        for item in gate.selected
    )
    selected_b = sum(
        item.gate.gate_class == SOURCE_GATE_MAJOR_SECONDARY
        for item in gate.selected
    )

    values = {
        "LIVE_TIER_A_DISCOVERED": int(health.get("tier_a_raw_count") or 0),
        "LIVE_TIER_A_VERIFIED": int(health.get("tier_a_verified_count") or 0),
        "LIVE_TIER_A_POLICY_ELIGIBLE": policy_a,
        "LIVE_TIER_A_SELECTED": selected_a,
        "LIVE_TIER_B_DISCOVERED": int(health.get("tier_b_raw_count") or 0),
        "LIVE_TIER_B_VERIFIED": int(health.get("tier_b_verified_count") or 0),
        "LIVE_TIER_B_POLICY_ELIGIBLE": policy_b,
        "LIVE_TIER_B_HELD": int(gate.audit.get("tier_b_held") or 0),
        "LIVE_TIER_B_SELECTED": selected_b,
    }
    for key, value in values.items():
        print(f"{key}={value}")
    if args.external_sweep:
        external = json.loads(args.external_sweep.read_text(encoding="utf-8"))
        external_rows = [
            row for row in external.get("articles") or [] if isinstance(row, dict)
        ]
        sensor_tier_a_titles = {
            _title_key(row.get("title")) for row in rows
            if _tier(row) in TIER_A and _title_key(row.get("title"))
        }
        external_titles = {
            _title_key(row.get("title")) for row in external_rows
            if _title_key(row.get("title"))
        }
        print(f"EXTERNAL_TIER_A_ARTICLES={len(external_rows)}")
        print(
            "EXTERNAL_TIER_A_PUBLISHERS_WITH_SUPPLY="
            f"{sum(int(value or 0) > 0 for value in (external.get('publisher_counts') or {}).values())}"
        )
        print(
            "SENSOR_EXTERNAL_TIER_A_TITLE_OVERLAP="
            f"{len(sensor_tier_a_titles & external_titles)}"
        )
    print(f"COLLECTION_STATUS={payload.get('collection_status') or 'unknown'}")
    print("PRODUCTION_WORKFLOW_DISPATCHES=0")
    print("PRODUCTION_TEAMS_SENDS=0")
    print("PRODUCTION_SMTP_SENDS=0")
    print("PRODUCTION_STATE_WRITES=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
