#!/usr/bin/env python3
"""Audit one calibration artifact without network, sends, or state writes.

The R4-R20 artifact intentionally omits article leads and row-level decisions.
This tool never guesses those missing decisions: it reports the exact aggregate
funnel, annotates only title-level canonical signals, and optionally compares
the artifact with the read-only Teams Watch held ledger to expose supply seams.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import ai_centrality, editorial_briefings, executive_materiality, source_priority  # noqa: E402


class AuditError(RuntimeError):
    pass


def _object(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AuditError(f"cannot read JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise AuditError(f"JSON root is not an object: {path}")
    return payload


def _with_sibling_generated_at(path: Path, calibration: dict) -> dict:
    """Use the dated artifact's wall-clock generation time when v1 omitted it."""
    if calibration.get("generated_at"):
        return calibration
    edition = str(calibration.get("edition_key") or "")
    if not edition:
        return calibration
    for name in ("manifest.json", "candidates.json"):
        sibling = path.parent / edition / name
        if not sibling.is_file():
            continue
        try:
            generated_at = _object(sibling).get("generated_at")
        except AuditError:
            continue
        if generated_at:
            enriched = dict(calibration)
            enriched["generated_at"] = generated_at
            return enriched
    return calibration


def _time(value: object, *, fallback_tz=None) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or ""))
    except ValueError:
        return None
    if parsed.tzinfo is None and fallback_tz is not None:
        parsed = parsed.replace(tzinfo=fallback_tz)
    return parsed


def _title_signals(title: str, source: str, url: str) -> dict:
    evidence = {"title": title, "snippet": "", "subtitle": "", "publisher_section": ""}
    centrality = ai_centrality.classify(evidence)
    qualification = executive_materiality.executive_qualification(evidence)
    materiality_score, materiality_reasons = executive_materiality.materiality_score(
        title, ""
    )
    tier = source_priority.publisher_delivery_tier(source, url)
    return {
        "evidence_scope": "title_only",
        "ai_central": centrality.level in ai_centrality.CENTRAL_LEVELS,
        "ai_centrality_level": centrality.level,
        "ai_centrality_exclusion": centrality.exclusion,
        "ai_centrality_reason": centrality.reason,
        "hdec_relevance_score": editorial_briefings._hdec_relevance_score(title, title),
        "materiality_score": materiality_score,
        "materiality_reasons": list(materiality_reasons),
        "executive_qualified": qualification.qualified,
        "executive_qualification_reason": qualification.reason,
        "source_tier": tier["tier"],
        "source_tier_label": tier["label"],
        "teams_source_gate_possible": tier["tier"] in {
            "primary_10", "secondary_3", "official_institution"
        },
    }


def build_audit(calibration: Mapping, watch_state: Mapping | None = None) -> dict:
    if calibration.get("mode") != "live_editorial_calibration":
        raise AuditError("not a live_editorial_calibration artifact")
    collection = calibration.get("collection_audit")
    selection = calibration.get("selection_audit")
    raw_rows = calibration.get("raw_articles")
    if not isinstance(collection, Mapping) or not isinstance(selection, Mapping):
        raise AuditError("collection_audit/selection_audit is missing")
    if not isinstance(raw_rows, list):
        raise AuditError("raw_articles is missing")

    coverage_start = _time(calibration.get("coverage_start"))
    coverage_end = _time(calibration.get("coverage_end"))
    if coverage_start is None or coverage_end is None:
        raise AuditError("coverage window is invalid")
    generated_at = _time(calibration.get("generated_at")) or _time(
        calibration.get("run_at")
    )

    collected_total = sum(
        int(collection.get(key) or 0)
        for key in (
            "naver_articles_collected",
            "google_news_articles_collected",
            "publisher_direct_rss_articles_collected",
        )
    )
    in_coverage = (
        int(selection.get("official_rows_seen") or 0)
        + int(selection.get("naver_articles_in_coverage") or 0)
        + int(selection.get("google_articles_in_coverage") or 0)
    )
    funnel = {
        "raw_collected_provider_sum": collected_total,
        "publisher_direct_eligible": int(
            collection.get("publisher_direct_eligible_count") or 0
        ),
        "raw_bundle_rows": int(calibration.get("raw_article_count") or 0),
        "in_coverage_rows": in_coverage,
        "relevance_qualified_direct_rows": int(
            selection.get("direct_candidates_before_selection") or 0
        ),
        "ai_central": int(selection.get("ai_central_qualified_count") or 0),
        "main_candidate_lane": int(selection.get("main_candidate_lane_count") or 0),
        "executive_materiality_rejected": int(
            selection.get("executive_materiality_rejected_count") or 0
        ),
        "qualified": int(selection.get("qualified_candidates") or 0),
        "final_editor_candidates": int(selection.get("selected_candidates") or 0),
        "normalized_selected_output": int(
            calibration.get("normalized_candidate_count") or 0
        ),
    }

    raw_urls: set[str] = set()
    rows: list[dict] = []
    for raw in raw_rows:
        if not isinstance(raw, Mapping):
            continue
        title = str(raw.get("title") or "")
        source = str(raw.get("source") or "")
        url = str(raw.get("url") or "")
        raw_urls.add(url)
        published = _time(raw.get("published_at"), fallback_tz=coverage_start.tzinfo)
        in_window = bool(
            published is not None and coverage_start <= published <= coverage_end
        )
        system_stage = "not_recorded_by_calibration_v1"
        system_reason = "sanitized_artifact_omits_lead_and_row_decision"
        if published is None:
            system_stage, system_reason = "normalization", "invalid_published_at"
        elif not in_window:
            system_stage, system_reason = "coverage", "outside_exact_window"
        rows.append(
            {
                "title": title,
                "source": source,
                "url": url,
                "published_at": str(raw.get("published_at") or ""),
                "in_coverage": in_window,
                **_title_signals(title, source, url),
                "system_rejection_stage": system_stage,
                "system_rejection_reason": system_reason,
                "teams_eligibility": "unknown_without_full_runtime_row",
                "editor_eligibility": (
                    "false_outside_coverage"
                    if not in_window
                    else "unknown_without_sanitized_lead_and_row_decision"
                ),
            }
        )
    rows.sort(
        key=lambda row: (
            row["in_coverage"],
            row["ai_central"],
            row["materiality_score"],
            row["published_at"],
        ),
        reverse=True,
    )

    missing_held: list[dict] = []
    held = (watch_state or {}).get("held_specialists") or {}
    if isinstance(held, Mapping):
        for url, record in held.items():
            if not isinstance(record, Mapping):
                continue
            first_seen = _time(record.get("first_seen_at"))
            if first_seen is None or not (coverage_start <= first_seen <= coverage_end):
                continue
            if generated_at is not None and first_seen > generated_at:
                continue
            if str(url) in raw_urls:
                continue
            fingerprint = str(record.get("title_fingerprint") or "")
            source = str(record.get("source") or "")
            missing_held.append(
                {
                    "title": "",
                    "title_fingerprint": fingerprint,
                    "source": source,
                    "url": str(url),
                    "first_seen_at": first_seen.isoformat(),
                    **_title_signals(fingerprint, source, str(url)),
                    "system_rejection_stage": "watch_source_gate_holdback",
                    "system_rejection_reason": str(
                        record.get("holdback_reason") or "specialist_held"
                    ),
                    "teams_eligibility": "false_automatic_source_gate",
                    "editor_eligibility": "not_evaluated_missing_from_calibration_supply",
                }
            )
    missing_held.sort(key=lambda row: row["first_seen_at"], reverse=True)

    return {
        "version": 1,
        "mode": "network_free_calibration_audit",
        "edition_key": calibration.get("edition_key"),
        "coverage_start": coverage_start.isoformat(),
        "coverage_end": coverage_end.isoformat(),
        "calibration_generated_at": generated_at.isoformat() if generated_at else "",
        "row_decision_evidence": "aggregate_exact_rows_title_only",
        "funnel": funnel,
        "in_coverage_raw_rows": [row for row in rows if row["in_coverage"]],
        "watch_held_missing_from_calibration": missing_held,
        "watch_held_missing_count": len(missing_held),
        "limitations": [
            "calibration v1 omits factual leads and row-level rejection decisions",
            "title-only annotations never replace the aggregate production decision",
            "Watch held comparison proves a supply seam, not automatic send eligibility",
        ],
        "network_calls": 0,
        "sends": 0,
        "production_state_writes": 0,
    }


def self_test() -> None:
    calibration = {
        "mode": "live_editorial_calibration",
        "edition_key": "2026-08-11",
        "run_at": "2026-08-11T07:20:00+09:00",
        "generated_at": "2026-08-10T23:30:00+09:00",
        "coverage_start": "2026-08-10T07:00:00+09:00",
        "coverage_end": "2026-08-11T06:40:00+09:00",
        "raw_article_count": 1,
        "normalized_candidate_count": 0,
        "collection_audit": {
            "naver_articles_collected": 2,
            "google_news_articles_collected": 3,
            "publisher_direct_rss_articles_collected": 1,
            "publisher_direct_eligible_count": 1,
        },
        "selection_audit": {
            "official_rows_seen": 0,
            "naver_articles_in_coverage": 1,
            "google_articles_in_coverage": 0,
            "direct_candidates_before_selection": 1,
            "ai_central_qualified_count": 1,
            "main_candidate_lane_count": 1,
            "executive_materiality_rejected_count": 1,
            "qualified_candidates": 0,
            "selected_candidates": 0,
        },
        "raw_articles": [{
            "title": "AI 데이터센터 전망",
            "source": "연합뉴스",
            "url": "https://example.org/in-artifact",
            "published_at": "2026-08-10T08:00:00+09:00",
        }],
    }
    state = {"held_specialists": {
        "https://example.org/missing": {
            "title_fingerprint": "gs건설ai데이터센터협력",
            "source": "대한경제",
            "first_seen_at": "2026-08-10T09:00:00+09:00",
            "holdback_reason": "holdback_active",
        },
        "https://example.org/after-calibration": {
            "title_fingerprint": "ai인프라투자",
            "source": "서울경제",
            "first_seen_at": "2026-08-11T00:00:00+09:00",
            "holdback_reason": "holdback_active",
        },
    }}
    result = build_audit(calibration, state)
    assert result["funnel"]["raw_collected_provider_sum"] == 6
    assert result["funnel"]["ai_central"] == 1
    assert result["watch_held_missing_count"] == 1
    assert result["network_calls"] == result["sends"] == 0
    print("PASS: network-free calibration rejection audit self-test")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("calibration", nargs="?", type=Path)
    parser.add_argument("--watch-state", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if args.calibration is None:
        parser.error("calibration JSON path is required")
    try:
        calibration = _with_sibling_generated_at(
            args.calibration, _object(args.calibration)
        )
        result = build_audit(
            calibration,
            _object(args.watch_state) if args.watch_state else None,
        )
    except AuditError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
