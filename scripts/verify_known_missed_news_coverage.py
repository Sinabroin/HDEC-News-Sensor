#!/usr/bin/env python3
"""D7-AF known-missed-news offline coverage fixture 검증."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import deal_watch, news_coverage  # noqa: E402

FIXTURE_PATH = ROOT / "data" / "known_missed_news.fixtures.json"
FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}"
          + (f" — {detail}" if detail else ""))
    if not condition:
        FAILURES.append(label)


def main() -> int:
    data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    fixtures = data.get("fixtures") or []
    check("누락 후보 A/B 19건 fixture", len(fixtures) == 19, str(len(fixtures)))
    required_fields = {
        "id", "title_hint", "must_match_any", "expected_query_group",
        "expected_deal_watch_label", "expected_source_family", "required", "notes",
    }
    configured = {group["name"] for group in news_coverage.collection_query_groups()}
    matched = 0
    for item in fixtures:
        fixture_id = item.get("id") or "(id 없음)"
        schema_ok = required_fields <= set(item)
        groups = news_coverage.query_groups_for_text(item.get("title_hint") or "")
        labels = deal_watch.classify_labels(item.get("title_hint") or "")
        group_ok = item.get("expected_query_group") in groups
        label_ok = item.get("expected_deal_watch_label") in labels
        source_ok = bool(item.get("expected_source_family"))
        terms_ok = bool(item.get("must_match_any"))
        check(f"{fixture_id}: schema", schema_ok)
        check(f"{fixture_id}: query group", group_ok,
              f"expected={item.get('expected_query_group')} got={groups}")
        check(f"{fixture_id}: deal label", label_ok,
              f"expected={item.get('expected_deal_watch_label')} got={labels}")
        check(f"{fixture_id}: source/term hints", source_ok and terms_ok)
        check(f"{fixture_id}: configured group",
              item.get("expected_query_group") in configured)
        if schema_ok and group_ok and label_ok and source_ok and terms_ok:
            matched += 1

    print(f"[SUMMARY] fixtures={len(fixtures)} matched={matched} not_matched={len(fixtures)-matched}")
    if FAILURES:
        print(f"FAIL: {len(FAILURES)} checks")
        return 1
    print("PASS: known-missed-news coverage")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
