#!/usr/bin/env python3
"""Inspect or import legacy Teams delivery JSON into a shadow runtime database.

Default mode is dry-run: the legacy file is validated and counted without opening a
SQLite database. ``--apply`` writes only to the explicitly supplied shadow DB path.
The source JSON is never modified.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.runtime_sqlite import SQLiteRuntimeStore  # noqa: E402


def load_legacy(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"legacy_state_invalid: {exc}") from exc
    if not isinstance(value, Mapping):
        raise SystemExit("legacy_state_invalid: root must be an object")
    return value


def count_entries(state: Mapping[str, Any]) -> int:
    if state.get("version") != 1:
        raise SystemExit("legacy_state_invalid: version must be 1")
    total = 0
    for name in (
        "article_ids",
        "normalized_urls",
        "title_fingerprints",
        "cluster_keys",
    ):
        value = state.get(name)
        if not isinstance(value, Mapping):
            raise SystemExit(f"legacy_state_invalid: {name} must be an object")
        if any(not isinstance(key, str) or not isinstance(entry, Mapping) for key, entry in value.items()):
            raise SystemExit(f"legacy_state_invalid: {name} contains invalid entries")
        total += len(value)
    return total


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--teams-state", type=Path, required=True)
    result.add_argument("--db", type=Path)
    result.add_argument("--apply", action="store_true")
    return result


def main() -> int:
    args = parser().parse_args()
    state = load_legacy(args.teams_state)
    scanned = count_entries(state)
    print(f"legacy_state_path={args.teams_state}")
    print(f"legacy_entries_scanned={scanned}")

    if not args.apply:
        print("mode=dry_run_no_write")
        print("legacy_entries_inserted=0")
        print("RESULT=D7-AK-6F-C1_LEGACY_IMPORT_DRY_RUN_PASS")
        return 0

    if args.db is None:
        raise SystemExit("--db is required with --apply")
    args.db.parent.mkdir(parents=True, exist_ok=True)
    with SQLiteRuntimeStore(args.db) as store:
        outcome = store.import_legacy_teams_state(state)
        stats = store.stats()
    print("mode=apply_shadow_db")
    print(f"legacy_entries_inserted={outcome.inserted}")
    print(f"legacy_entries_duplicates={outcome.duplicates}")
    print(f"shadow_legacy_rows={stats['legacy_delivery_imports']}")
    print("source_state_modified=false")
    print("network_calls=0")
    print("RESULT=D7-AK-6F-C1_LEGACY_IMPORT_APPLY_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
