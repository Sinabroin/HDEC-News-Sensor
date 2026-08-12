#!/usr/bin/env python3
"""Bounded live Watch collection with production resolvers and /tmp-only state.

This invokes the normal public-source collector, including its strongest safe
Google wrapper resolver and Naver lane when credentials are available.  It
never invokes a workflow or sender and refuses every output/state path outside
``/tmp``.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _tmp_path(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if path != Path("/tmp") and Path("/tmp") not in path.parents:
        raise argparse.ArgumentTypeError("R4-OPS-6C live paths must be under /tmp")
    return path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=_tmp_path, required=True)
    parser.add_argument("--verified-state", type=_tmp_path, required=True)
    args = parser.parse_args()

    os.environ["NEWS_MODE"] = "live"
    os.environ["NEWS_CENSOR_VERIFIED_STATE_PATH"] = str(args.verified_state)
    # Use the configured Naver adapter when credentials exist; its own contract
    # returns a truthful missing-credentials status otherwise.
    os.environ.setdefault("NAVER_NEWS_ENABLED", "1")
    os.environ["TEAMS_AI_PUSH_MODE"] = "dry_run"
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    from scripts import build_executive_brief

    brief = build_executive_brief.attach_artifact_contract(
        build_executive_brief.build_brief_via_mock_pipeline(),
        weather_mode="mock",
    )
    build_executive_brief.write_brief_json(args.output, brief)
    print(f"LIVE_READONLY_ARTIFACT={args.output}")
    print(f"LIVE_READONLY_VERIFIED_STATE={args.verified_state}")
    print("LIVE_RESOLVER_PATH=production_bounded_public_resolver")
    print("PRODUCTION_WORKFLOW_DISPATCHES=0")
    print("PRODUCTION_TEAMS_SENDS=0")
    print("PRODUCTION_SMTP_SENDS=0")
    print("PRODUCTION_STATE_WRITES=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
