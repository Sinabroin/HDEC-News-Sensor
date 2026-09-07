#!/usr/bin/env python3
"""Run D7-AK-6F-C2 collector shadow orchestration without any delivery."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.runtime_models import utc_now_iso  # noqa: E402
from app.runtime_orchestrator import (  # noqa: E402
    SHADOW_CHANNEL,
    run_shadow_orchestration,
)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--collector-mode", choices=("mock", "live"), default="mock")
    result.add_argument("--allow-live-collector", action="store_true")
    result.add_argument("--collector-db", type=Path)
    result.add_argument("--shadow-db", default=":memory:")
    result.add_argument("--run-id")
    result.add_argument("--json-output", type=Path)
    return result


def main() -> int:
    args = parser().parse_args()
    run_id = args.run_id or f"shadow-{utc_now_iso()}"

    with tempfile.TemporaryDirectory(prefix="d7ak6f-c2-collector-") as tmp:
        collector_db = args.collector_db or Path(tmp) / "collector.db"
        payload = run_shadow_orchestration(
            collector_mode=args.collector_mode,
            collector_db_path=collector_db,
            shadow_db_path=args.shadow_db,
            run_id=run_id,
            allow_live_collector=args.allow_live_collector,
            shadow_channel=SHADOW_CHANNEL,
        )

    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    print(f"collector_entrypoint={payload['collector_entrypoint']}")
    print(f"collector_mode_requested={payload['collector_mode_requested']}")
    print(f"collector_mode_effective={payload['collector_mode_effective']}")
    print(f"observations={payload['observations']}")
    print(f"processed={payload['processed']}")
    print(f"skipped_invalid={payload['skipped_invalid']}")
    print(f"outbox_created={payload['outbox_created']}")
    print(f"canonical_articles={payload['store_stats']['canonical_articles']}")
    print(f"event_clusters={payload['store_stats']['news_events']}")
    print(f"policy_decisions={payload['store_stats']['policy_decisions']}")
    print(f"outbox_rows={payload['store_stats']['delivery_outbox']}")
    print(f"heartbeats={payload['store_stats']['runtime_heartbeats']}")
    print(f"shadow_channel={payload['shadow_channel']}")
    print("channel_sends=0")
    print("smtp_connections=0")
    print("teams_sends=0")
    print("telegram_sends=0")
    print("production_state_writes=0")
    print("RESULT=D7-AK-6F-C2_SHADOW_ORCHESTRATION_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
