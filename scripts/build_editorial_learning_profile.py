#!/usr/bin/env python3
"""D7-AK-6E R4-R7 §10/§11 — deterministic learning-profile builder.

Builds a versioned preference profile from the sanitized human-editorial
corpus and materializes the append-only decision ledger for every ingested
human artifact (final Briefs, candidate pools, reconciliations, production
hard negatives). Building a profile NEVER activates it: activation requires a
historical backtest, a regression pass, explicit human approval, and a
committed pointer change through normal PR review.

Offline only — no network, no sends, no production state writes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import editorial_memory  # noqa: E402

BUILDER_VERSION = "editorial-profile-builder-v1"


def _profile_digest(payload: dict) -> str:
    stable = {k: v for k, v in payload.items() if k not in {"build_timestamp", "profile_digest"}}
    return hashlib.sha256(
        json.dumps(stable, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def materialize_decisions(corpus, root: Path, recorded_at: str) -> int:
    """Idempotent append-only ingest ledger; existing lines never change."""
    added = 0
    manifest_path = root / "final_briefs" / "manifest.json"
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.exists()
        else {"extracted": [], "fail_closed": []}
    )
    before = {r.get("decision_id") for r in editorial_memory.load_decisions(root)}

    def append(record: dict) -> None:
        nonlocal added
        if record["decision_id"] not in before:
            editorial_memory.append_decision(record, root)
            added += 1

    for entry in manifest.get("extracted", []):
        append(
            {
                "decision_id": f"ingest-{entry['edition_key']}-{entry['source_sha256'][:12]}",
                "record_version": 1,
                "recorded_at": recorded_at,
                "record_type": (
                    "candidate_pool_ingest"
                    if entry["kind"] == "candidate_pool"
                    else "final_brief_ingest"
                ),
                "edition_key": entry["edition_key"],
                "provenance_reference": entry["source_filename"],
                "source_sha256": entry["source_sha256"],
                "extracted_article_count": entry["extracted_article_count"],
                "parser_version": entry["parser_version"],
            }
        )
    for failed in manifest.get("fail_closed", []):
        append(
            {
                "decision_id": "ingest-fail-closed-"
                + hashlib.sha256(failed["source_filename"].encode()).hexdigest()[:12],
                "record_version": 1,
                "recorded_at": recorded_at,
                "record_type": "final_brief_ingest",
                "edition_key": "unparseable",
                "provenance_reference": failed["source_filename"],
                "status": "fail_closed",
                "reason": failed["reason"],
            }
        )

    seed_path = root / "candidate_pools" / "daily-2026-08-03.json"
    if seed_path.exists():
        seed = json.loads(seed_path.read_text(encoding="utf-8"))
        append(
            {
                "decision_id": "ingest-daily-2026-08-03-first-stage",
                "record_version": 1,
                "recorded_at": recorded_at,
                "record_type": "candidate_pool_ingest",
                "edition_key": seed["edition_key"],
                "candidate_origin": "human_first_stage",
                "extracted_article_count": seed["article_count"],
                "reconciliation": "pending_final_brief",
            }
        )

    reconciliation = editorial_memory.reconcile_pool_with_finals(
        "tni-weekly-2026-07-23", corpus
    )
    append(
        {
            "decision_id": "reconcile-tni-weekly-2026-07-23-v1",
            "record_version": 1,
            "recorded_at": recorded_at,
            "record_type": "candidate_final_reconciliation",
            "edition_key": "tni-weekly-2026-07-23",
            **reconciliation,
        }
    )

    hard_negatives = corpus.by_level(editorial_memory.EVIDENCE_HARD_NEGATIVE)
    append(
        {
            "decision_id": "ingest-observed-production-hard-negatives-v1",
            "record_version": 1,
            "recorded_at": recorded_at,
            "record_type": "hard_negative_ingest",
            "edition_key": "observed-production",
            "count": len(hard_negatives),
            "articles": [
                {
                    "article_id": record.article_id,
                    "title": record.title,
                    "exclusion_tags": [t for t in record.topic_labels if t],
                }
                for record in hard_negatives
            ],
        }
    )
    return added


def build_profile(corpus, decisions: list[dict], build_timestamp: str) -> dict:
    categories: dict[str, int] = {}
    for record in corpus.records:
        if record.category:
            categories[record.category] = categories.get(record.category, 0) + 1
    payload = {
        "profile_version": "profile-v001",
        "builder_version": BUILDER_VERSION,
        "corpus_digest": corpus.digest,
        "source_record_count": len(corpus.records),
        "gold_plus_count": len(corpus.by_level("gold_plus")),
        "gold_selected_count": len(corpus.by_level("gold_selected")),
        "silver_count": len(corpus.by_level("silver_candidate")),
        "near_miss_count": len(corpus.by_level("near_miss")),
        "hard_negative_count": len(corpus.by_level("hard_negative")),
        "decision_record_count": len(decisions),
        "category_distribution": dict(sorted(categories.items())),
        "product_heads": {
            product: {"weights": dict(editorial_memory._head_weights(product, None))}  # noqa: SLF001
            for product in editorial_memory.PRODUCTS
        },
        "prior_profile": None,
        "rollback_target": None,
        "active": False,
        "activation_requirements": [
            "historical backtest",
            "regression pass",
            "explicit human approval",
            "committed profile pointer change",
            "normal PR review",
        ],
        "build_timestamp": build_timestamp,
    }
    payload["profile_digest"] = _profile_digest(payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--corpus-root", default=str(editorial_memory.DEFAULT_CORPUS_ROOT)
    )
    parser.add_argument(
        "--recorded-at",
        default=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        help="Deterministic timestamp override for reproducible rebuilds.",
    )
    args = parser.parse_args()
    root = Path(args.corpus_root)
    corpus = editorial_memory.load_corpus(root)

    added = materialize_decisions(corpus, root, args.recorded_at)
    decisions = editorial_memory.load_decisions(root)

    profile = build_profile(corpus, decisions, args.recorded_at)
    profiles_dir = root / "profiles"
    profiles_dir.mkdir(parents=True, exist_ok=True)
    profile_path = profiles_dir / f"{profile['profile_version']}.json"
    profile_path.write_text(
        json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    pointer = {
        "pointer_version": 1,
        "profile": profile_path.name,
        "profile_digest": profile["profile_digest"],
        "active": False,
        "activation_note": (
            "Built profiles never activate automatically; activation is a "
            "committed pointer change after backtest + regression + human "
            "approval through normal PR review."
        ),
    }
    (profiles_dir / "latest.json").write_text(
        json.dumps(pointer, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print(
        f"PROFILE {profile_path.relative_to(ROOT)} digest={profile['profile_digest'][:16]} "
        f"records={profile['source_record_count']} gold+={profile['gold_plus_count']} "
        f"gold={profile['gold_selected_count']} silver={profile['silver_count']} "
        f"near_miss={profile['near_miss_count']} hard_neg={profile['hard_negative_count']}"
    )
    print(f"DECISIONS appended={added} total={len(decisions)}")
    print("ACTIVE=false (activation requires human-approved pointer change)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
