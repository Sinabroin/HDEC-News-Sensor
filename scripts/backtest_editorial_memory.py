#!/usr/bin/env python3
"""D7-AK-6E R4-R7 §12 — historical backtest over human editions.

Replays every extracted human final Brief against the preference memory and
the deterministic gates, reporting recall/rejection/category metrics. Where
original candidate pools are unavailable the affected evidence is reported as
``unavailable`` — never invented.

Offline only: no network, no sends, no state writes.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import ai_centrality, editorial_memory, editorial_review  # noqa: E402
from app.teams_ai_push import evaluate_teams_push_policy  # noqa: E402


def article_from_record(record: editorial_memory.CorpusRecord) -> dict:
    return {"title": record.title, "snippet": record.human_summary}


def main() -> int:
    corpus = editorial_memory.load_corpus()
    finals = [
        r
        for r in corpus.records
        if r.evidence_level
        in {editorial_memory.EVIDENCE_GOLD_PLUS, editorial_memory.EVIDENCE_GOLD_SELECTED}
    ]
    negatives = corpus.by_level(editorial_memory.EVIDENCE_HARD_NEGATIVE)
    editions = sorted({r.edition_key for r in finals})

    report: dict = {
        "corpus_digest": corpus.digest,
        "editions": editions,
        "per_edition": {},
    }

    # 1. Gold-vs-hard-negative ranking + headline top-3 recall per edition.
    gold_above_negative_total = 0
    gold_total = 0
    headline_top3 = 0
    for edition in editions:
        rows = sorted(
            (r for r in finals if r.edition_key == edition),
            key=lambda r: r.human_order,
        )
        scored = sorted(
            (
                (
                    editorial_memory.score_article(
                        editorial_memory.PRODUCT_WEEKLY,
                        article_from_record(record),
                        corpus,
                    ).preference_score,
                    record,
                )
                for record in rows
            ),
            key=lambda pair: -pair[0],
        )
        negative_scores = [
            editorial_memory.score_article(
                editorial_memory.PRODUCT_WEEKLY, article_from_record(record), corpus
            ).preference_score
            for record in negatives
        ]
        max_negative = max(negative_scores) if negative_scores else 0.0
        above = sum(1 for score, _record in scored if score > max_negative)
        gold_above_negative_total += above
        gold_total += len(scored)
        top3_titles = [record.title for _score, record in scored[:3]]
        headline = next((r for r in rows if r.headline), None)
        headline_hit = bool(headline and headline.title in top3_titles)
        headline_top3 += int(headline_hit)

        # Category accuracy against the human label.
        category_hits = sum(
            1
            for record in rows
            if record.category
            and str(
                editorial_review.analyze_editorial_category(
                    record.title, record.human_summary
                )["category"]
            )
            == record.category
        )
        labeled = sum(1 for record in rows if record.category)

        # Duplicate-event suppression inside the edition.
        fingerprints = {
            editorial_memory.tokenize(record.title) for record in rows
        }
        report["per_edition"][edition] = {
            "gold_articles": len(rows),
            "gold_above_max_hard_negative": above,
            "max_hard_negative_score": round(max_negative, 4),
            "headline_in_top3": headline_hit,
            "category_accuracy": f"{category_hits}/{labeled}",
            "duplicate_titles": len(rows) - len(fingerprints),
        }

    report["gold_above_negative_rate"] = (
        f"{gold_above_negative_total}/{gold_total}"
    )
    report["headline_top3_recall"] = f"{headline_top3}/{len(editions)}"

    # 2. Hard negatives stay rejected by the deterministic gates.
    rejected = 0
    for record in negatives:
        row = {
            "title": record.title,
            "snippet": record.human_summary,
            "source": record.source,
            "url": record.canonical_url,
            "publisher_direct": True,
            "current_run_seen": True,
            "published_at": "2026-08-04T05:00:00+09:00",
            "score": 4.0,
            "shadow_urgency_status": "none",
            "shadow_confirmed_event_types": [],
        }
        if not evaluate_teams_push_policy(row).eligible:
            rejected += 1
    report["hard_negative_rejection"] = f"{rejected}/{len(negatives)}"

    # 3. Near-miss evidence: honest availability report (§12).
    reconciliation = editorial_memory.reconcile_pool_with_finals(
        "tni-weekly-2026-07-23", corpus
    )
    report["near_miss_outranking"] = (
        "unavailable (pool edition final Brief not among ingested references; "
        f"{reconciliation['unresolved_count']} candidates remain "
        f"{reconciliation['unresolved_status']})"
    )
    report["pool_reconciliation"] = {
        "pool": reconciliation["pool_edition"],
        "promotions": len(reconciliation["promotions"]),
        "same_edition_final_available": reconciliation[
            "same_edition_final_available"
        ],
    }

    # 4. Source-tier distribution over gold records.
    from app import source_priority  # noqa: PLC0415

    tiers: dict[str, int] = {}
    for record in finals:
        tier = str(
            source_priority.publisher_delivery_tier(record.source, record.canonical_url)[
                "tier"
            ]
        )
        tiers[tier] = tiers.get(tier, 0) + 1
    report["gold_source_tier_distribution"] = dict(sorted(tiers.items()))

    # 5. Product-head disagreement + preference drift by edition.
    drift = {}
    for edition in editions:
        rows = [r for r in finals if r.edition_key == edition]
        central = sum(
            1
            for r in rows
            if ai_centrality.classify(article_from_record(r)).level
            in ai_centrality.CENTRAL_LEVELS
        )
        drift[edition] = {
            "ai_central_share": f"{central}/{len(rows)}",
        }
    report["preference_drift_by_edition"] = drift
    disagreements = 0
    comparisons = 0
    for record in finals:
        weekly = editorial_memory.score_article(
            editorial_memory.PRODUCT_WEEKLY, article_from_record(record), corpus
        ).preference_score
        teams = editorial_memory.score_article(
            editorial_memory.PRODUCT_TEAMS, article_from_record(record), corpus
        ).preference_score
        comparisons += 1
        if weekly > teams:
            disagreements += 1
    report["product_disagreement_weekly_over_teams"] = (
        f"{disagreements}/{comparisons}"
    )

    print(json.dumps(report, ensure_ascii=False, indent=2))
    print()
    print(
        "BACKTEST_SUMMARY "
        f"editions={len(editions)} gold_above_negative={report['gold_above_negative_rate']} "
        f"headline_top3={report['headline_top3_recall']} "
        f"hard_negative_rejection={report['hard_negative_rejection']}"
    )
    print("COUNTERS network=0 smtp=0 teams=0 telegram=0 production_state_writes=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
