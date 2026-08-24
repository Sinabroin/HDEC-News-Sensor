#!/usr/bin/env python3
"""Fail closed when a pre-publication Watch send is absent from Daily radar.

Read-only production gate: no collection, send, state mutation, docs write, or
git operation.  The workflow supplies a freshly fetched Watch ledger copy.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import editorial_radar, teams_push_state  # noqa: E402


def _load(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"morning bridge artifact unavailable: {path}") from exc
    if not isinstance(value, dict):
        raise SystemExit(f"morning bridge artifact is not an object: {path}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-manifest", type=Path, required=True)
    parser.add_argument("--review-bundle", type=Path, required=True)
    parser.add_argument("--watch-state", type=Path, required=True)
    parser.add_argument("--checked-at", default="")
    args = parser.parse_args()

    runtime = _load(args.runtime_manifest)
    bundle = _load(args.review_bundle)
    radar = editorial_radar.normalize_audit(runtime.get("radar_audit"))
    window = radar.get("bridge_window")
    if not isinstance(window, dict):
        raise SystemExit("morning bridge window missing from runtime manifest")
    try:
        snapshot_at = datetime.fromisoformat(str(window.get("snapshot_at") or ""))
        finalization_at = (
            datetime.fromisoformat(args.checked_at)
            if args.checked_at
            else datetime.now(snapshot_at.tzinfo)
        )
        coverage_start = datetime.fromisoformat(str(runtime["coverage_start"]))
        coverage_end = datetime.fromisoformat(str(runtime["coverage_end"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise SystemExit("morning bridge timestamps are malformed") from exc
    state = teams_push_state.load_state(args.watch_state)
    candidates = [
        item for item in bundle.get("candidates", []) if isinstance(item, dict)
    ]
    current = editorial_radar.watch_bridge(
        state,
        snapshot_at=snapshot_at,
        finalization_at=finalization_at,
        coverage_start=coverage_start,
        coverage_end=coverage_end,
        existing_article_ids=(
            str(item.get("article_id") or item.get("candidate_id") or "")
            for item in candidates
        ),
        existing_urls=(str(item.get("selected_url") or "") for item in candidates),
    )
    embedded = {
        str(value)
        for value in window.get("watch_delivery_ids", [])
        if str(value)
    }
    required = {
        str(value)
        for value in current.get("bridge_delivery_ids", [])
        if str(value)
    }
    missing = sorted(required - embedded)
    print(f"snapshot_at={snapshot_at.isoformat()}")
    print(f"freshness_checked_at={finalization_at.isoformat()}")
    print(f"embedded_watch_bridge_count={len(embedded)}")
    print(f"required_watch_bridge_count={current['bridge_count']}")
    print(f"missing_watch_bridge_delivery_ids={','.join(missing) or '-'}")
    print("network_sends=0 state_writes=0 docs_writes=0")
    if missing:
        print("RESULT=FAIL_MORNING_WATCH_BRIDGE_STALE")
        return 1
    print("RESULT=PASS_MORNING_WATCH_BRIDGE_FRESH")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
