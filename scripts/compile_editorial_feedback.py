#!/usr/bin/env python3
"""Compile exported editorial feedback JSONL into a bounded learning profile."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.editorial_feedback import compile_profile  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--minimum-samples", type=int, default=3)
    args = parser.parse_args()

    records = []
    for path in args.inputs:
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except ValueError as exc:
                raise SystemExit(f"{path}:{lineno}: malformed JSON") from exc
            if isinstance(value, dict):
                records.append(value)

    profile = compile_profile(records, minimum_samples=max(1, args.minimum_samples))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(profile, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"feedback_records={len(records)}")
    for key in (
        "source_adjustments", "category_adjustments", "domain_adjustments",
        "keyword_adjustments", "manual_domain_seeds", "manual_keyword_seeds",
    ):
        print(f"{key}={len(profile[key])}")
    print(f"output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
