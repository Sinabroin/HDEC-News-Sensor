#!/usr/bin/env python3
"""Run one bounded, read-only public-institution editorial shadow.

The only network operation is the existing collector bundle invocation. The
script never renders or publishes a product, calls a sender, writes production
state, invokes Hermes, or mutates repository data. Its output must live under
``/tmp``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from app import (  # noqa: E402
    ai_centrality,
    editorial_briefings,
    editorial_preference_runtime,
    public_institution_routing,
    publisher_direct,
    teams_ai_push,
)
import run_editorial_briefing  # noqa: E402


CONTRACT = "D7_AK_6E_R4_R8_PUBLIC_INSTITUTION_NO_SEND_SHADOW_V1"


def _clean(value: object) -> str:
    return " ".join(str(value or "").split())


def _article_id(row: Mapping[str, Any]) -> str:
    for key in ("article_id", "candidate_id", "id", "article_key"):
        value = _clean(row.get(key))
        if value:
            return value
    stable = "\x1f".join(
        (
            _clean(row.get("title")),
            _clean(row.get("source") or row.get("publisher")),
            _clean(publisher_direct.publisher_url(row)),
        )
    )
    return "shadow-" + hashlib.sha256(stable.encode("utf-8")).hexdigest()[:16]


def _public_row(row: Mapping[str, Any]) -> dict[str, Any]:
    route = public_institution_routing.classify(row)
    centrality = ai_centrality.classify(row)
    return {
        "article_id": _article_id(row),
        "title": _clean(row.get("title")),
        "source": _clean(row.get("source") or row.get("publisher")),
        "publisher_direct_url": publisher_direct.publisher_url(row),
        "ai_centrality": centrality.level,
        "ai_central": centrality.is_central,
        **route.metadata(),
    }


def _editorial_article_id(article: editorial_briefings.EditorialArticle) -> str:
    stable = "\x1f".join((article.title, article.source, article.selected_url))
    return "product-" + hashlib.sha256(stable.encode("utf-8")).hexdigest()[:16]


def _product_row(article: editorial_briefings.EditorialArticle, selected: set[str]) -> dict[str, Any]:
    article_id = _editorial_article_id(article)
    return {
        "article_id": article_id,
        "title": article.title,
        "source": article.source,
        "publisher_direct_url": article.selected_url,
        "selected": article_id in selected,
        "selection_reason": article.selection_reason,
        "source_class": article.source_class,
        "editorial_lane": article.editorial_lane,
        "public_institution_type": article.public_institution_type,
        "official_source_name": article.official_source_name,
        "default_surface": article.default_surface,
        "main_surface_eligible": article.main_surface_eligible,
        "teams_alert_eligible": article.teams_alert_eligible,
        "tni_brief_eligible": article.tni_brief_eligible,
        "tni_report_topic_eligible": article.tni_report_topic_eligible,
        "promotion_reason": article.promotion_reason,
        "final_category": article.final_category,
        "supporting_evidence_only": article.supporting_evidence_only,
    }


def _select_product(
    rows: list[dict[str, Any]],
    *,
    product: str,
    coverage: editorial_briefings.CoverageWindow,
    cap: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    production_audit = editorial_briefings.SelectionAuditCounters()
    selected = editorial_briefings.normalize_articles(
        rows,
        coverage,
        limit=cap,
        resolve_images=False,
        selection_audit=production_audit,
        selection_mode=editorial_briefings.SELECTION_MODE_EDITORIAL_PRIORITY,
        edition_type=product,
    )
    review_audit = editorial_briefings.SelectionAuditCounters()
    review = editorial_briefings.normalize_articles(
        rows,
        coverage,
        limit=max(cap, len(rows)),
        resolve_images=False,
        selection_audit=review_audit,
        selection_mode=editorial_briefings.SELECTION_MODE_EDITORIAL_PRIORITY,
        edition_type=product,
        operator_review=True,
    )
    selected_ids = {_editorial_article_id(article) for article in selected}
    return (
        [_product_row(article, selected_ids) for article in review],
        {
            "coverage_start": coverage.start.isoformat(),
            "coverage_end": coverage.end.isoformat(),
            "selected_ids": sorted(selected_ids),
            "production_audit": production_audit.manifest_fields(),
            "operator_audit": review_audit.manifest_fields(),
        },
    )


def _explicit_duplicate_clusters(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        cluster = _clean(row.get("event_cluster_id") or row.get("duplicate_event_cluster"))
        if cluster:
            grouped[cluster].append(row)
    output: list[dict[str, Any]] = []
    for cluster, members in sorted(grouped.items()):
        routed = [_public_row(member) for member in members]
        if any(item["editorial_lane"] == public_institution_routing.LANE_PUBLIC for item in routed) and any(
            item["editorial_lane"] != public_institution_routing.LANE_PUBLIC for item in routed
        ):
            output.append({"event_cluster_id": cluster, "members": routed})
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("/tmp/d7ak6e-r4r8-public-institution-shadow.json"),
    )
    args = parser.parse_args()
    output = args.output.resolve()
    tmp_root = Path("/tmp").resolve()
    if output == tmp_root or tmp_root not in output.parents:
        raise SystemExit("output must be a file below /tmp")
    if os.environ.get("HERMES_EDITORIAL_MEMORY_ENABLED", "0").strip() not in {"", "0"}:
        raise SystemExit("Hermes must be disabled for the no-send shadow")

    runtime = editorial_preference_runtime.default_runtime()
    if runtime.memory_active:
        raise SystemExit("editorial-memory profile must remain inactive")

    run_at = datetime.now(editorial_briefings.KST)
    # Exactly one collector invocation in this process.
    collected, collection_audit = run_editorial_briefing.collect_live_article_bundle()
    public_rows = [
        item
        for item in (_public_row(row) for row in collected)
        if item["editorial_lane"] == public_institution_routing.LANE_PUBLIC
        and item["ai_central"]
    ]
    main_rows = [
        item
        for item in (_public_row(row) for row in collected)
        if item["ai_central"]
        and (
            item["editorial_lane"] == public_institution_routing.LANE_MAIN
            or item["main_surface_eligible"]
        )
    ]

    teams, teams_audit = teams_ai_push.select_teams_push_candidates_with_audit(
        collected,
        max_articles=teams_ai_push.MAX_TEAMS_ARTICLES,
    )
    daily_rows, daily_audit = _select_product(
        collected,
        product="daily",
        coverage=editorial_briefings.daily_coverage(run_at),
        cap=editorial_briefings.DAILY_MAX_ARTICLES,
    )
    weekly_supply, weekly_carry_forward = run_editorial_briefing.supplement_weekly_verified_supply(
        collected,
        run_at=run_at,
    )
    weekly_rows, weekly_audit = _select_product(
        weekly_supply,
        product="weekly",
        coverage=editorial_briefings.weekly_coverage(run_at),
        cap=editorial_briefings.WEEKLY_MAX_ARTICLES,
    )

    promoted = [item for item in public_rows if item["main_surface_eligible"]]
    non_promoted = [item for item in public_rows if not item["main_surface_eligible"]]
    tni_brief = [
        item for item in weekly_rows if item["tni_brief_eligible"]
    ]
    tni_report_topics = [
        item for item in public_rows if item["tni_report_topic_eligible"]
    ]
    result = {
        "contract": CONTRACT,
        "generated_at": run_at.isoformat(),
        "collector_invocations": 1,
        "collection_audit": collection_audit,
        "collected_count": len(collected),
        "main_candidate_lane": main_rows,
        "public_institution_lane": public_rows,
        "promoted_public_candidates": promoted,
        "non_promoted_public_candidates": non_promoted,
        "teams_candidates": [
            {
                "article_id": _article_id(candidate.article),
                "title": _clean(candidate.article.get("title")),
                "source": _clean(candidate.article.get("source")),
                "publisher_direct_url": publisher_direct.publisher_url(candidate.article),
                "editorial_lane": candidate.editorial_lane,
                "promotion_reason": candidate.promotion_reason,
                "final_category": candidate.final_category,
            }
            for candidate in teams
        ],
        "teams_audit": teams_audit,
        "daily_candidates": daily_rows,
        "daily_audit": daily_audit,
        "tni_brief_candidates": tni_brief,
        "weekly_audit": {**weekly_audit, "carry_forward_rows_read": weekly_carry_forward},
        "tni_report_topic_candidates": tni_report_topics,
        "duplicate_official_media_event_clusters": _explicit_duplicate_clusters(weekly_supply),
        "profile": {
            "version": runtime.profile_version,
            "active": False,
            "mode": "shadow_only",
        },
        "safety": {
            "smtp_attempts": 0,
            "teams_sends": 0,
            "telegram_sends": 0,
            "production_state_writes": 0,
            "workflow_dispatches": 0,
            "hermes_live_calls": 0,
            "hermes_live_writes": 0,
            "repository_variable_changes": 0,
            "profile_activation_writes": 0,
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    editorial_briefings.atomic_write_bytes(
        output,
        (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )
    print(
        "shadow_ok "
        f"output={output} collected={len(collected)} main={len(main_rows)} "
        f"public={len(public_rows)} promoted={len(promoted)} "
        f"teams={len(teams)} daily={len(daily_rows)} weekly={len(weekly_rows)} "
        f"report_topics={len(tni_report_topics)} collector_invocations=1 "
        "smtp_attempts=0 teams_sends=0 telegram_sends=0 "
        "production_state_writes=0 workflow_dispatches=0 hermes_live_calls=0"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
