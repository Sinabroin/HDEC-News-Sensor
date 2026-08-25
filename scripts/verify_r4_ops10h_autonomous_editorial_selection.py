#!/usr/bin/env python3
"""Offline verifier for R4-OPS-10H bounded autonomous Watch selection."""

from __future__ import annotations

import hashlib
import json
import smtplib
import socket
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from app import ai_centrality, watch_semantic_precision  # noqa: E402
from app.teams_ai_push import (  # noqa: E402
    IMPORTANCE_OBSERVATION,
    IMPORTANCE_TOP,
    evaluate_teams_push_policy,
    render_article_email,
    select_teams_push_from_artifact_with_audit,
)
import send_teams_ai_push as sender  # noqa: E402

FIXTURE = ROOT / "data" / "r4_ops10h_human_gold_replay.json"
WORKFLOW = ROOT / ".github" / "workflows" / "teams-ai-news-watch.yml"
PROTECTED = (
    ROOT / "data" / "teams_push_state.json",
    ROOT / "data" / "editor_delivery_state.json",
    ROOT / "data" / "editorial_daily_state.json",
    ROOT / "data" / "editorial_weekly_state.json",
    ROOT / "docs" / "editorial" / "daily" / "2026-08-25.html",
)
PROTECTED_GLOBS = (
    "docs/editorial/daily/editions/daily-2026-08-25-*.json",
    "docs/editorial/review/snapshots/review-2026-08-25-*/*",
)

CHECKS = 0
FAILURES: list[str] = []
NETWORK_CALLS = 0
SMTP_CONNECTIONS = 0


def check(name: str, condition: bool, detail: object = "") -> None:
    global CHECKS
    CHECKS += 1
    if condition:
        print(f"PASS: {name}")
    else:
        FAILURES.append(name)
        suffix = f" — {detail}" if detail else ""
        print(f"FAIL: {name}{suffix}")


def _blocked_network(*_args: object, **_kwargs: object) -> None:
    global NETWORK_CALLS
    NETWORK_CALLS += 1
    raise RuntimeError("R4-OPS-10H verifier blocks network access")


class _BlockedSMTP:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        global SMTP_CONNECTIONS
        SMTP_CONNECTIONS += 1
        raise RuntimeError("R4-OPS-10H verifier blocks SMTP access")


def _install_guards() -> None:
    socket.getaddrinfo = _blocked_network
    socket.create_connection = _blocked_network
    urllib.request.urlopen = _blocked_network
    smtplib.SMTP = _BlockedSMTP
    smtplib.SMTP_SSL = _BlockedSMTP


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "MISSING"


def _protected_paths() -> tuple[Path, ...]:
    paths = list(PROTECTED)
    for pattern in PROTECTED_GLOBS:
        paths.extend(path for path in ROOT.glob(pattern) if path.is_file())
    return tuple(sorted(set(paths)))


def _protected_hashes() -> dict[str, str]:
    return {str(path.relative_to(ROOT)): _sha(path) for path in _protected_paths()}


def _row(raw: dict, *, index: int) -> dict:
    row = dict(raw)
    row.update(
        {
            "article_id": raw["case_id"],
            "article_key": raw["case_id"],
            "publisher_direct": True,
            "source_quality_passed": True,
            "current_run_seen": True,
            "teams_newness_eligible": True,
            "carried_forward": False,
            "score": 2.0,
            "final_score": 2.0,
            "shadow_urgency_status": "none",
            "shadow_would_pass": False,
            "shadow_confirmed_event_types": [],
            "published_at": f"2026-08-24T{10 + index:02d}:00:00+09:00",
            "change_type": "new_article",
        }
    )
    return row


def _tier_1() -> dict:
    return {
        "article_id": "tier-1-executive-headline",
        "article_key": "tier-1-executive-headline",
        "title": "현대건설, AI 데이터센터 EPC 공급계약 체결",
        "snippet": "현대건설이 AI 데이터센터 EPC 공급계약을 공식 체결했다.",
        "source": "연합뉴스",
        "url": "https://www.yna.co.kr/view/R4OPS10HTIER1",
        "publisher_direct": True,
        "source_quality_passed": True,
        "current_run_seen": True,
        "teams_newness_eligible": True,
        "carried_forward": False,
        "score": 4.8,
        "final_score": 4.8,
        "shadow_urgency_status": "confirmed",
        "shadow_would_pass": True,
        "shadow_confirmed_event_types": ["investment_confirmed"],
        "published_at": "2026-08-24T15:00:00+09:00",
        "change_type": "new_article",
    }


def _payload(rows: list[dict]) -> dict:
    return {
        "schema_version": 1,
        "source": "live-delta",
        "generated_at": "2026-08-25T09:00:00+09:00",
        "articles": rows,
    }


def _dry_summary(tmp: Path, payload: dict, name: str) -> dict:
    artifact = tmp / f"{name}.json"
    state = tmp / f"{name}-state.json"
    artifact.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    summary = sender.deliver(
        artifact_path=artifact,
        state_path=state,
        credentials=sender.EmailChannelCredentials(),
        should_send=False,
        dashboard_url="https://example.invalid/dashboard",
        max_articles=5,
        now_iso_value="2026-08-25T00:00:00+00:00",
    )
    check(f"{name}: dry run writes no state", not state.exists())
    return summary


def main() -> int:
    _install_guards()
    before = _protected_hashes()
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    check(
        "human-gold fixture contract",
        fixture.get("fixture_contract") == "R4_OPS10H_HUMAN_GOLD_REPLAY_V1",
    )
    workflow = WORKFLOW.read_text(encoding="utf-8")
    check(
        "natural Watch workflow runs the 10H verifier exactly once",
        workflow.count(
            "python3 scripts/verify_r4_ops10h_autonomous_editorial_selection.py"
        ) == 1,
    )
    positives = [_row(raw, index=i) for i, raw in enumerate(fixture["positive"])]
    negatives = [
        _row(raw, index=i + len(positives))
        for i, raw in enumerate(fixture["hard_negative"])
    ]

    positive_evaluations = [evaluate_teams_push_policy(row) for row in positives]
    for raw, evaluation in zip(fixture["positive"], positive_evaluations):
        check(
            f"{raw['case_id']}: publisher-factual strong observation",
            evaluation.eligible
            and evaluation.importance.level == IMPORTANCE_OBSERVATION
            and evaluation.semantic_precision is not None
            and evaluation.semantic_precision.semantic_class
            == watch_semantic_precision.AI_STRONG_OBSERVATION,
            evaluation,
        )
        check(
            f"{raw['case_id']}: expected evidence category",
            evaluation.delivery_category == raw["expected_category"],
            evaluation.delivery_category,
        )

    negative_evaluations = [evaluate_teams_push_policy(row) for row in negatives]
    check(
        "hard negatives never enter Tier 2",
        all(
            not evaluation.eligible
            and evaluation.importance.level != IMPORTANCE_OBSERVATION
            for evaluation in negative_evaluations
        ),
        [(row["case_id"], ev.rejection_reason) for row, ev in zip(negatives, negative_evaluations)],
    )
    check(
        "portal-only human example fails publisher authority",
        next(
            ev for row, ev in zip(negatives, negative_evaluations)
            if row["case_id"] == "portal-only"
        ).rejection_reason == "source_authority_failed",
    )
    check(
        "stock and opinion hard exclusions preserved",
        {
            row["case_id"]: ev.rejection_reason
            for row, ev in zip(negatives, negative_evaluations)
        }.get("stock-ai-target") == "excluded_stock_market_dominant"
        and {
            row["case_id"]: ev.rejection_reason
            for row, ev in zip(negatives, negative_evaluations)
        }.get("opinion-physical-ai") == "excluded_opinion_content",
    )

    with tempfile.TemporaryDirectory(prefix="hdec-10h-verify-") as td:
        tmp = Path(td)
        tier2_summary = _dry_summary(tmp, _payload(positives), "tier2-only")
        check(
            "Tier-2-only supply selects exactly one bounded card",
            tier2_summary["tier_1_immediate_rows"] == 0
            and tier2_summary["tier_2_immediate_rows"] == len(positives)
            and tier2_summary["tier_2_selected_rows"] == 1
            and tier2_summary["selected_rows"] == 1
            and tier2_summary["smtp_attempted_rows"] == 0,
            tier2_summary,
        )
        check(
            "policy trace covers every positive row without titles or URLs",
            len(tier2_summary["policy_row_decision_trace"]) == len(positives)
            and all(
                trace["selection_tier"] == "tier_2_strong_observation"
                and "title" not in trace
                and "url" not in trace
                for trace in tier2_summary["policy_row_decision_trace"]
            ),
            tier2_summary["policy_row_decision_trace"],
        )
        check(
            "granular rejection vocabulary keeps final policy reasons explicit",
            "no_evidenced_delivery_category" in sender.REJECTION_COUNTER_KEYS
            and "public_institution_not_promoted" in sender.REJECTION_COUNTER_KEYS
            and "shadow_blocked" in sender.REJECTION_COUNTER_KEYS,
        )
        negative_summary = _dry_summary(
            tmp,
            _payload(negatives),
            "hard-negative-rejections",
        )
        check(
            "known hard negatives never collapse into other_policy_reason",
            negative_summary["rejection_breakdown"]["other_policy_reason"] == 0
            and negative_summary["rejected_rows"] == len(negatives)
            and negative_summary["rejection_reconciled"] is True,
            negative_summary["rejection_breakdown"],
        )

        tier1_summary = _dry_summary(
            tmp,
            _payload([_tier_1(), *positives]),
            "tier1-precedence",
        )
        check(
            "Tier 1 suppresses Tier 2 instead of adding volume",
            tier1_summary["tier_1_immediate_rows"] == 1
            and tier1_summary["tier_2_immediate_rows"] == len(positives)
            and tier1_summary["tier_2_selected_rows"] == 0
            and tier1_summary["tier_2_suppressed_by_tier_1_rows"]
            == len(positives)
            and tier1_summary["selected_rows"] == 1,
            tier1_summary,
        )

        candidates, _audit = select_teams_push_from_artifact_with_audit(
            _payload(positives), max_articles=None
        )
        observation = next(
            candidate for candidate in candidates
            if candidate.importance.level == IMPORTANCE_OBSERVATION
        )
        subject, text_body, html_body = render_article_email(
            {"dashboard_url": "https://example.invalid/dashboard"}, observation
        )
        check(
            "Teams card visibly labels the Tier-2 decision",
            "강한 관찰 신호" in subject + text_body + html_body,
        )
        check(
            "Teams card carries no funnel or classification diagnostics",
            all(
                token not in text_body + html_body
                for token in (
                    "policy-row", "semantic_class", "AI_core=", "수집·판단 레이더"
                )
            ),
        )

    after = _protected_hashes()
    check("production state and immutable 2026-08-25 artifacts preserved", before == after)
    check("external network calls are zero", NETWORK_CALLS == 0, NETWORK_CALLS)
    check("real SMTP connections are zero", SMTP_CONNECTIONS == 0, SMTP_CONNECTIONS)
    check(
        "all human-gold URLs used for delivery are publisher-direct",
        all(
            not row["url"].startswith("https://v.daum.net/")
            and ai_centrality.classify(row).is_central
            for row in positives
        ),
    )

    print(f"checks={CHECKS} failures={len(FAILURES)}")
    if FAILURES:
        print("RESULT=R4_OPS_10H_AUTONOMOUS_EDITORIAL_SELECTION_FAIL")
        return 1
    print("TIER_1_EXECUTIVE_HEADLINE=PASS")
    print("TIER_2_STRONG_OBSERVATION_SIGNAL=PASS")
    print("TIER_2_MAX_ONE_PER_RUN=PASS")
    print("NO_FILLER_HARD_EXCLUSIONS_PRESERVED=PASS")
    print("POLICY_ROW_OBSERVABILITY=PASS")
    print("PRODUCTION_STATE_ARTIFACT_PRESERVATION=PASS")
    print("REAL_SENDS=0")
    print("RESULT=R4_OPS_10H_AUTONOMOUS_EDITORIAL_SELECTION_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
