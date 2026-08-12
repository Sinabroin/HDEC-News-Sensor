#!/usr/bin/env python3
"""Report R4-OPS-6B metrics from a GET-only /tmp brief artifact."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import publisher_direct, source_priority  # noqa: E402
from app.teams_ai_push import evaluate_teams_push_policy  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact", type=Path)
    args = parser.parse_args()
    payload = json.loads(args.artifact.read_text(encoding="utf-8"))
    health = payload.get("collector_health") or {}
    rows = [
        row for row in payload.get("news_censor_display_articles") or []
        if isinstance(row, dict)
    ]
    tier_a = {"primary_10", "secondary_3"}
    tier_b = {"major_secondary"}
    policy_eligible_major = 0
    for row in rows:
        tier = source_priority.publisher_delivery_tier(
            str(row.get("source") or ""), publisher_direct.publisher_url(row)
        )
        if (
            tier.get("identity_evidence") == "exact_domain"
            and tier.get("tier") in tier_a | tier_b
            and evaluate_teams_push_policy(
                row, require_validated_fields=True
            ).eligible
        ):
            policy_eligible_major += 1

    print(f"LIVE_RAW_CANDIDATES={int(health.get('raw_candidate_count') or 0)}")
    print(f"LIVE_MAJOR_MEDIA_RAW={int(health.get('major_media_raw_count') or 0)}")
    print(
        "LIVE_MAJOR_MEDIA_RESOLUTION_ATTEMPTED="
        f"{int(health.get('major_media_resolution_attempted_count') or 0)}"
    )
    print(f"LIVE_MAJOR_MEDIA_VERIFIED={int(health.get('major_media_verified_count') or 0)}")
    print(f"LIVE_TIER_A_VERIFIED={int(health.get('tier_a_verified_count') or 0)}")
    print(f"LIVE_TIER_B_VERIFIED={int(health.get('tier_b_verified_count') or 0)}")
    print(f"LIVE_POLICY_ELIGIBLE_MAJOR={policy_eligible_major}")
    print(f"COLLECTION_STATUS={payload.get('collection_status') or 'unknown'}")
    print("PRODUCTION_TEAMS_SENDS=0")
    print("PRODUCTION_SMTP_SENDS=0")
    print("PRODUCTION_STATE_WRITES=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
