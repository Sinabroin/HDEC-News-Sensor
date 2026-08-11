#!/usr/bin/env python3
"""Bounded GET-only live recall probe; outputs and verified state must be /tmp."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _tmp_path(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if path != Path("/tmp") and Path("/tmp") not in path.parents:
        raise argparse.ArgumentTypeError("R4-OPS-6B live-check paths must be under /tmp")
    return path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=_tmp_path, required=True)
    parser.add_argument("--verified-state", type=_tmp_path, required=True)
    args = parser.parse_args()

    os.environ["NEWS_MODE"] = "live"
    os.environ["NEWS_CENSOR_VERIFIED_STATE_PATH"] = str(args.verified_state)
    os.environ["NAVER_NEWS_ENABLED"] = os.environ.get("NAVER_NEWS_ENABLED", "0")
    os.environ["TEAMS_AI_PUSH_MODE"] = "dry_run"
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    from app import live_collector
    from scripts import build_executive_brief

    # Google batchexecute decoding is POST-based and therefore outside this
    # explicitly GET-only verification. Wrappers are still fairly scheduled and
    # fetched as GET; production decoder behavior remains covered offline.
    live_collector._decode_google_news_url = lambda _url, timeout=0: None

    brief = build_executive_brief.attach_artifact_contract(
        build_executive_brief.build_brief_via_mock_pipeline(),
        weather_mode="mock",
    )
    build_executive_brief.write_brief_json(args.output, brief)
    print(f"GET_ONLY_LIVE_ARTIFACT={args.output}")
    print(f"GET_ONLY_LIVE_STATE={args.verified_state}")
    print("PRODUCTION_WORKFLOW_DISPATCHES=0")
    print("PRODUCTION_TEAMS_SENDS=0")
    print("PRODUCTION_SMTP_SENDS=0")
    print("PRODUCTION_STATE_WRITES=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
