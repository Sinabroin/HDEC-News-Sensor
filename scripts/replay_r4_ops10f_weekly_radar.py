#!/usr/bin/env python3
"""Deterministic R4-OPS-10F Weekly-gold / Morning-bridge replay report."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import editorial_radar, teams_push_state, watch_semantic_precision  # noqa: E402

FIXTURE = ROOT / "data" / "r4_ops10f_weekly_radar_replay.json"


def _load() -> dict:
    value = json.loads(FIXTURE.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("version") != 1:
        raise SystemExit("R4-OPS-10F replay fixture malformed")
    return value


def _gold_recalled(row: dict) -> bool:
    dimensions = editorial_radar.strategic_dimensions(row)
    return bool(
        dimensions["ai_central"]
        or dimensions["executive_materiality"]
        or dimensions["decision_relevance_score"] > 0
    )


def _construction_recalled(row: dict) -> bool:
    dimensions = editorial_radar.strategic_dimensions(row)
    return bool(
        dimensions["ai_central"]
        and dimensions["executive_materiality"]
        and dimensions["hdec_strategic_relevance"] >= 2
        and dimensions["infrastructure_project_specificity"] >= 1
    )


def _watch_false_positive(row: dict) -> bool:
    return watch_semantic_precision.classify(row).eligible


def _bridge_metrics(rows: list[dict]) -> tuple[int, int, list[str]]:
    snapshot = datetime.fromisoformat("2026-08-24T07:45:00+09:00")
    finalization = datetime.fromisoformat("2026-08-24T08:02:00+09:00")
    coverage_start = datetime.fromisoformat("2026-08-23T07:00:00+09:00")
    coverage_end = datetime.fromisoformat("2026-08-24T06:40:00+09:00")
    state = teams_push_state.empty_state()
    sent_times = (
        "2026-08-24T07:59:00+09:00",
        "2026-08-24T08:00:00+09:00",
    )
    for index, row in enumerate(rows, 1):
        article = dict(row)
        article["article_id"] = f"bridge-fixture-{index}"
        article["published_at"] = (
            "2026-08-19T14:13:00+09:00"
            if index == 1
            else "2026-08-24T06:20:00+09:00"
        )
        article["first_seen_at"] = "2026-08-24T07:58:00+09:00"
        article["first_material_discovery_at"] = "2026-08-24T07:59:00+09:00"
        state = teams_push_state.mark_sent_after_success(
            state,
            article,
            cluster_key=f"bridge:fixture:{index}",
            signature=f"signature-{index}",
            importance="important",
            source=row["source"],
            send_succeeded=True,
            sent_at=sent_times[index - 1],
            delivery_id=f"teams_ai_push:bridge-fixture-{index}",
        )
    bridge = editorial_radar.watch_bridge(
        state,
        snapshot_at=snapshot,
        finalization_at=finalization,
        coverage_start=coverage_start,
        coverage_end=coverage_end,
    )
    missing = [
        row["title"]
        for row in rows
        if row["title"] not in {
            item.get("title") for item in bridge["bridge_rows"]
        }
    ]
    return len(rows), int(bridge["observable_count"]), missing


def main() -> int:
    fixture = _load()
    gold = list(fixture.get("gold_positive") or [])
    negatives = list(fixture.get("known_negative") or [])
    construction = list(fixture.get("construction_infra_positive") or [])

    gold_misses = [row["title"] for row in gold if not _gold_recalled(row)]
    negative_misses = [row["title"] for row in negatives if _watch_false_positive(row)]
    construction_misses = [
        row["title"] for row in construction if not _construction_recalled(row)
    ]
    generic_false_positives = sum(_watch_false_positive(row) for row in negatives)
    bridge_cases, bridge_observable, bridge_misses = _bridge_metrics(construction[:2])

    metrics = {
        "reference_fetched_read_only": bool(
            fixture.get("reference", {}).get("fetched_read_only")
        ),
        "gold_positive_count": len(gold),
        "gold_positive_recalled": len(gold) - len(gold_misses),
        "known_negative_count": len(negatives),
        "known_negative_rejected": len(negatives) - len(negative_misses),
        "construction_infra_positive_recall": (
            f"{len(construction) - len(construction_misses)}/{len(construction)}"
        ),
        "generic_ai_false_positive_count": generic_false_positives,
        "watch_bridge_cases": bridge_cases,
        "watch_bridge_observable_count": bridge_observable,
        "gold_misses": gold_misses,
        "known_negative_false_positives": negative_misses,
        "construction_infra_misses": construction_misses,
        "watch_bridge_misses": bridge_misses,
        "sample_size_note": "Small deterministic editorial fixture; counts are regression evidence, not population-quality estimates.",
    }
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    passed = (
        not negative_misses
        and not construction_misses
        and not bridge_misses
        and bridge_observable == bridge_cases
    )
    print(f"RESULT={'PASS' if passed else 'FAIL'}_R4_OPS10F_RADAR_REPLAY")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
