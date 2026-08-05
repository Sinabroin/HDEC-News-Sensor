#!/usr/bin/env python3
"""Reviewed, append-only ingestion of one editorial feedback proposal.

Dry-run is the default.  The command validates the proposal, its candidate
pool digest, the committed corpus schema, and the existing decision ledger,
then prints the exact JSONL addition and resulting ledger digest.  Only an
explicit ``--write`` appends the line; no profile pointer or profile file is
ever touched.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import editorial_memory, editorial_review  # noqa: E402

DEFAULT_CORPUS_ROOT = editorial_memory.DEFAULT_CORPUS_ROOT


class FeedbackIngestionError(ValueError):
    """A proposal or target corpus failed closed validation."""


def _canonical_digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _load_mapping(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise FeedbackIngestionError(f"{label} is missing or malformed") from exc
    if not isinstance(value, dict):
        raise FeedbackIngestionError(f"{label} must be a JSON object")
    return value


def validate_proposal(proposal: Mapping[str, Any]) -> None:
    required = {
        "proposal_version",
        "record_type",
        "proposed_decision_id",
        "edition_key",
        "candidate_pool_digest",
        "candidate_count",
        "candidate_ids",
        "candidate_pool",
        "final_selected_ids",
        "excluded_ids",
        "selection_order",
        "headline",
        "title_edits",
        "summary_edits",
        "category_edits",
        "executive_implication_edits",
        "placement_decisions",
        "exclusion_tags",
        "ratings",
        "operator_approval_time",
        "generated_at",
    }
    missing = sorted(required - set(proposal))
    if missing:
        raise FeedbackIngestionError(f"proposal missing fields: {missing}")
    if (
        proposal.get("proposal_version") != editorial_review.FEEDBACK_PROPOSAL_VERSION
        or proposal.get("record_type") != "post_editor_feedback_proposal"
        or not str(proposal.get("proposed_decision_id") or "").strip()
        or not str(proposal.get("edition_key") or "").strip()
    ):
        raise FeedbackIngestionError("proposal identity mismatch")
    pool = proposal.get("candidate_pool")
    ids = proposal.get("candidate_ids")
    if not isinstance(pool, list) or not isinstance(ids, list):
        raise FeedbackIngestionError("candidate pool fields must be lists")
    if any(not isinstance(item, Mapping) for item in pool):
        raise FeedbackIngestionError("candidate pool record is malformed")
    pool_ids = [str(item.get("candidate_id") or "") for item in pool]
    if (
        int(proposal.get("candidate_count") or -1) != len(pool)
        or ids != pool_ids
        or any(not item for item in pool_ids)
        or len(pool_ids) != len(set(pool_ids))
    ):
        raise FeedbackIngestionError("candidate pool identity mismatch")
    if editorial_review.candidate_pool_digest(pool) != proposal.get(
        "candidate_pool_digest"
    ):
        raise FeedbackIngestionError("candidate pool digest mismatch")
    selected = proposal.get("final_selected_ids")
    excluded = proposal.get("excluded_ids")
    order = proposal.get("selection_order")
    if (
        not isinstance(selected, list)
        or not selected
        or not isinstance(excluded, list)
        or order != selected
        or any(not isinstance(item, str) or not item for item in selected + excluded)
        or len(selected) != len(set(selected))
    ):
        raise FeedbackIngestionError("proposal selection identity mismatch")
    if set(pool_ids) != (set(selected) & set(pool_ids)) | set(excluded):
        raise FeedbackIngestionError("proposal selected/excluded pool does not reconcile")
    if set(selected) & set(excluded):
        raise FeedbackIngestionError("proposal selected/excluded IDs overlap")
    placements = proposal.get("placement_decisions")
    if (
        not isinstance(placements, list)
        or len(placements) != len(pool_ids)
        or [str(item.get("candidate_id") or "") for item in placements]
        != pool_ids
        or any(not isinstance(item, Mapping) for item in placements)
    ):
        raise FeedbackIngestionError("proposal placement decisions mismatch")
    for item in placements:
        default_surface = str(item.get("default_surface") or "")
        recommended_category = str(
            item.get("recommended_final_category") or ""
        )
        final_category = str(item.get("final_category") or "")
        final_surface = str(item.get("final_surface") or "")
        override = item.get("human_placement_override") is True
        reason = str(item.get("human_placement_reason") or "").strip()
        if final_category and final_category not in editorial_review.CATEGORY_ORDER:
            raise FeedbackIngestionError("proposal final category is invalid")
        if final_surface not in {
            "main_candidate_lane",
            "secondary_public_lane",
        }:
            raise FeedbackIngestionError("proposal final surface is invalid")
        changed = (
            final_surface != default_surface
            or final_category != recommended_category
        )
        if changed and (not override or not reason):
            raise FeedbackIngestionError(
                "placement change requires explicit override and written reason"
            )
        if override and not reason:
            raise FeedbackIngestionError(
                "human placement override requires written reason"
            )


def _validate_corpus(corpus_root: Path) -> tuple[dict[str, Any], list[dict[str, Any]], bytes]:
    schema = _load_mapping(corpus_root / "schema.json", "corpus schema")
    if (
        schema.get("version") != 1
        or schema.get("schema_contract") != "HDEC_EDITORIAL_LEARNING_CORPUS_V1"
        or schema.get("decision_record_fields", {}).get("append_only") is not True
        or "editor_approval_feedback"
        not in schema.get("decision_record_fields", {}).get("record_types", [])
        or "supersede"
        not in schema.get("decision_record_fields", {}).get("record_types", [])
    ):
        raise FeedbackIngestionError("existing corpus schema contract mismatch")
    ledger_path = corpus_root / "decisions.jsonl"
    try:
        ledger_bytes = ledger_path.read_bytes()
        text = ledger_bytes.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise FeedbackIngestionError("decision ledger is missing or malformed") from exc
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except ValueError as exc:
            raise FeedbackIngestionError(
                f"decision ledger line {line_number} is malformed"
            ) from exc
        if not isinstance(value, dict):
            raise FeedbackIngestionError(
                f"decision ledger line {line_number} is not an object"
            )
        records.append(value)
    ids = [str(item.get("decision_id") or "") for item in records]
    if any(not item for item in ids) or len(ids) != len(set(ids)):
        raise FeedbackIngestionError("decision ledger IDs are invalid")
    return schema, records, ledger_bytes


def proposed_record(proposal: Mapping[str, Any]) -> dict[str, Any]:
    supersedes = str(proposal.get("supersedes") or "").strip()
    return {
        "decision_id": str(proposal["proposed_decision_id"]),
        "record_version": 1,
        "recorded_at": str(
            proposal.get("operator_approval_time") or proposal["generated_at"]
        ),
        "record_type": "supersede" if supersedes else "editor_approval_feedback",
        "edition_key": str(proposal["edition_key"]),
        "proposal_digest": _canonical_digest(proposal),
        "candidate_pool_digest": str(proposal["candidate_pool_digest"]),
        "candidate_ids": list(proposal["candidate_ids"]),
        "final_selected_ids": list(proposal["final_selected_ids"]),
        "excluded_ids": list(proposal["excluded_ids"]),
        "selection_order": list(proposal["selection_order"]),
        "headline": str(proposal["headline"]),
        "title_edits": list(proposal["title_edits"]),
        "summary_edits": list(proposal["summary_edits"]),
        "category_edits": list(proposal["category_edits"]),
        "executive_implication_edits": list(
            proposal["executive_implication_edits"]
        ),
        "placement_decisions": list(proposal["placement_decisions"]),
        "exclusion_tags": dict(proposal["exclusion_tags"]),
        "ratings": dict(proposal["ratings"]),
        **({"supersedes": supersedes} if supersedes else {}),
    }


def ingest_proposal(
    proposal_path: Path,
    *,
    corpus_root: Path = DEFAULT_CORPUS_ROOT,
    write: bool = False,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    proposal = _load_mapping(Path(proposal_path), "feedback proposal")
    validate_proposal(proposal)
    corpus_root = Path(corpus_root)
    _schema, records, ledger_bytes = _validate_corpus(corpus_root)
    record = proposed_record(proposal)
    existing_ids = {str(item["decision_id"]) for item in records}
    if record["decision_id"] in existing_ids:
        raise FeedbackIngestionError("decision ID already exists")
    supersedes = str(record.get("supersedes") or "")
    if supersedes and supersedes not in existing_ids:
        raise FeedbackIngestionError("superseded decision ID does not exist")
    line = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    separator = b"" if not ledger_bytes or ledger_bytes.endswith(b"\n") else b"\n"
    proposed_bytes = ledger_bytes + separator + line.encode("utf-8") + b"\n"
    digest = hashlib.sha256(proposed_bytes).hexdigest()

    print(f"--- {corpus_root / 'decisions.jsonl'}")
    print(f"+++ {corpus_root / 'decisions.jsonl'} (proposed append)")
    print("+" + line)
    print(f"proposed_decisions_sha256={digest}")
    print(f"proposal_sha256={record['proposal_digest']}")

    active_env = os.environ if env is None else env
    if write:
        if str(active_env.get("GITHUB_ACTIONS", "")).lower() == "true":
            raise FeedbackIngestionError(
                "repository mutation is forbidden from GitHub Actions delivery"
            )
        ledger_path = corpus_root / "decisions.jsonl"
        with ledger_path.open("ab") as handle:
            handle.write(separator + line.encode("utf-8") + b"\n")
        if ledger_path.read_bytes() != proposed_bytes:
            raise FeedbackIngestionError("decision append verification failed")
    return {
        "written": write,
        "decision_id": record["decision_id"],
        "record": record,
        "proposed_digest": digest,
        "previous_record_count": len(records),
        "result_record_count": len(records) + 1,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proposal", type=Path, required=True)
    parser.add_argument("--corpus-root", type=Path, default=DEFAULT_CORPUS_ROOT)
    parser.add_argument(
        "--write",
        action="store_true",
        help="append exactly one reviewed record (default is dry-run)",
    )
    args = parser.parse_args(argv)
    try:
        result = ingest_proposal(
            args.proposal,
            corpus_root=args.corpus_root,
            write=args.write,
        )
    except FeedbackIngestionError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"mode={'write' if result['written'] else 'dry_run'}")
    print(f"decision_id={result['decision_id']}")
    print("profile_activation_writes=0")
    print("production_delivery_writes=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
