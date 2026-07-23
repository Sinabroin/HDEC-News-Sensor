#!/usr/bin/env python3
"""Prepare article-level Teams AI cards without sending or mutating state (D7-AK-5C).

This entrypoint consumes the same raw DELTA_ARTIFACT_FILE used by the scheduled
alert senders. It performs Teams-only AI selection, persistent dedup evaluation,
and one-card-per-article rendering, then writes preview JSON only to an explicit
output directory outside the repository.

It intentionally contains no webhook/SMTP client and never writes Teams push state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.teams_ai_push import build_candidate_card, select_teams_push_from_artifact
from app.teams_push_state import (
    InvalidTeamsPushState,
    article_identity,
    filter_unsent_candidates,
    load_state,
    resolve_state_path,
)

MANIFEST_SCHEMA_VERSION = 1


def _sha256(path: Path) -> str:
    if not path.exists():
        return "absent"
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_artifact(path: Path) -> Mapping[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("delta artifact is unreadable or invalid JSON") from exc
    if not isinstance(payload, Mapping):
        raise ValueError("delta artifact root must be an object")
    return payload


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _prepare_output_dir(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    repo = REPO_ROOT.resolve()
    if _is_within(resolved, repo):
        raise ValueError("dry-run output directory must be outside the repository")
    if resolved.exists() and any(resolved.iterdir()):
        raise ValueError("dry-run output directory must be empty")
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def _write_json_atomic(path: Path, payload: object) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as tmp:
        tmp.write(text)
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)


def _candidate_record(candidate, decision, card_file: str = "") -> dict[str, Any]:
    article = candidate.article
    identity = article_identity(article)
    return {
        "article_key": identity["article_id"],
        "title": str(article.get("title") or ""),
        "url": str(article.get("url") or ""),
        "source": str(article.get("source") or article.get("display_source") or ""),
        "topic_key": candidate.topic.topic_key,
        "topic_label": candidate.topic.topic_label,
        "importance": candidate.importance.level,
        "importance_label": candidate.importance.label,
        "importance_reason": candidate.importance.reason,
        "score": candidate.importance.score,
        "hdec_direct": candidate.importance.hdec_direct,
        "cluster_key": candidate.cluster_key,
        "material_signature": candidate.material_signature,
        "dedup_reason": decision.reason,
        "dedup_matched_key": decision.matched_key,
        "is_update": decision.is_update,
        "card_file": card_file,
    }


def build_dry_run(
    *,
    artifact_path: Path,
    state_path: Path,
    output_dir: Path,
    dashboard_url: str = "",
    report_url: str = "",
    detected_at: str = "",
) -> dict[str, Any]:
    artifact_hash_before = _sha256(artifact_path)
    state_hash_before = _sha256(state_path)
    payload = _read_artifact(artifact_path)
    try:
        state = load_state(state_path)
    except InvalidTeamsPushState:
        raise

    output = _prepare_output_dir(output_dir)
    candidates = select_teams_push_from_artifact(payload)
    accepted, decisions = filter_unsent_candidates(state, candidates)

    # Build cards from the original candidate/decision pairs so a material-update
    # decision can set the visible [업데이트] label without mutating persistent state.
    cards: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    card_index = 0
    alert_context = dict(payload)
    alert_context["dashboard_url"] = dashboard_url
    alert_context["report_url"] = report_url

    for candidate, decision in zip(candidates, decisions):
        if not decision.send_allowed:
            blocked.append(_candidate_record(candidate, decision))
            continue
        card_index += 1
        rendered_candidate = replace(candidate, is_update=decision.is_update)
        card = build_candidate_card(
            alert_context,
            rendered_candidate,
            detected_at=detected_at,
        )
        card_name = f"card-{card_index:02d}.json"
        _write_json_atomic(output / card_name, card)
        cards.append(_candidate_record(rendered_candidate, decision, card_name))

    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "mode": "dry_run_no_send",
        "artifact_source": str(payload.get("source") or ""),
        "artifact_shadow_alert_delta": payload.get("shadow_alert_delta") is True,
        "generated_at": str(payload.get("generated_at") or payload.get("generated_kst") or ""),
        "detected_at": detected_at or str(payload.get("generated_at") or payload.get("generated_kst") or ""),
        "artifact_article_count": len(payload.get("articles") or [])
        if isinstance(payload.get("articles"), list)
        else 0,
        "candidate_count_before_dedup": len(candidates),
        "card_count": len(cards),
        "dedup_blocked_count": len(blocked),
        "cards": cards,
        "blocked": blocked,
        "safety": {
            "send_count": 0,
            "webhook_calls": 0,
            "smtp_connections": 0,
            "state_write": False,
            "artifact_write": False,
        },
        "integrity": {
            "artifact_sha256_before": artifact_hash_before,
            "state_sha256_before": state_hash_before,
        },
    }
    manifest_path = output / "manifest.json"
    _write_json_atomic(manifest_path, manifest)

    artifact_hash_after = _sha256(artifact_path)
    state_hash_after = _sha256(state_path)
    if artifact_hash_after != artifact_hash_before or state_hash_after != state_hash_before:
        raise RuntimeError("input artifact or persistent state changed during dry-run")

    # Keep accepted referenced so the function verifies the helper contract and
    # static analyzers can see that the returned accepted count was considered.
    if len(accepted) != len(cards):
        raise RuntimeError("dedup/card count mismatch")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Prepare article-level Teams AI Adaptive Cards (dry-run only)"
    )
    parser.add_argument(
        "--artifact",
        default=os.environ.get("DELTA_ARTIFACT_FILE", ""),
        help="raw dashboard delta artifact JSON (default: DELTA_ARTIFACT_FILE)",
    )
    parser.add_argument(
        "--state",
        default=os.environ.get("TEAMS_PUSH_STATE_PATH", ""),
        help="persistent Teams dedup state to read only",
    )
    parser.add_argument("--output-dir", required=True)
    parser.add_argument(
        "--dashboard-url", default=os.environ.get("DASHBOARD_URL", "")
    )
    parser.add_argument("--report-url", default=os.environ.get("REPORT_URL", ""))
    parser.add_argument("--detected-at", default="")
    args = parser.parse_args(argv)

    if not args.artifact:
        print("ERROR: DELTA_ARTIFACT_FILE/--artifact is required", file=sys.stderr)
        return 2

    artifact_path = Path(args.artifact).expanduser().resolve()
    state_path = resolve_state_path(args.state or None).expanduser().resolve()
    output_dir = Path(args.output_dir)

    try:
        manifest = build_dry_run(
            artifact_path=artifact_path,
            state_path=state_path,
            output_dir=output_dir,
            dashboard_url=args.dashboard_url,
            report_url=args.report_url,
            detected_at=args.detected_at,
        )
    except (ValueError, InvalidTeamsPushState, OSError, RuntimeError) as exc:
        print(
            f"ERROR: Teams AI push dry-run failed closed: {type(exc).__name__}",
            file=sys.stderr,
        )
        return 2

    print(
        "Teams AI push dry-run: "
        f"source={manifest['artifact_source'] or 'unknown'} "
        f"candidates={manifest['candidate_count_before_dedup']} "
        f"cards={manifest['card_count']} "
        f"dedup_blocked={manifest['dedup_blocked_count']} "
        "webhook_calls=0 smtp_connections=0 state_writes=0"
    )
    print(f"manifest={Path(args.output_dir).expanduser().resolve() / 'manifest.json'}")
    print("RESULT=D7-AK-5C_TEAMS_AI_PUSH_DRY_RUN_COMPLETE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
