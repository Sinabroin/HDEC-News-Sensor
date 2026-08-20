#!/usr/bin/env python3
"""Rebuild the bounded editorial feedback profile from confirmed exemplars.

Offline/local only: reads content-addressed JSON files, validates every record,
and writes one deterministic profile.  It performs no network, send, dispatch,
or production-state operation.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import editorial_feedback  # noqa: E402

DEFAULT_CORPUS = ROOT / "data" / "editorial_feedback" / "human_exemplars"
DEFAULT_OUTPUT = ROOT / "data" / "editorial_feedback" / "profile.json"


def load_corpus(path: Path) -> list[dict]:
    records = []
    for source in sorted(path.glob("exemplar-*.json")):
        try:
            value = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"malformed exemplar: {source}") from exc
        if not editorial_feedback.valid_human_exemplar(value):
            raise ValueError(f"invalid exemplar contract: {source}")
        if source.name != f"{value['exemplar_id']}.json":
            raise ValueError(f"exemplar filename mismatch: {source}")
        records.append(value)
    return records


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    try:
        records = load_corpus(args.corpus)
        profile = editorial_feedback.compile_profile_from_exemplars(records)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(profile, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"confirmed_exemplars={len(records)}")
    print(f"collection_queries={len(editorial_feedback.collection_queries(profile))}")
    print(f"output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
