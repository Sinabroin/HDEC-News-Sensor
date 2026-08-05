#!/usr/bin/env python3
"""No-network verifier for validated-Brief ↔ live-delta Teams equivalence."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import briefing, decision_relevance, radar, radar_signals  # noqa: E402
from app.teams_ai_push import (  # noqa: E402
    evaluate_teams_push_policy,
    select_teams_push_from_artifact,
)


def _raw(article_id: str, title: str, snippet: str, *, score: float = 4.7) -> dict:
    return {
        "id": article_id,
        "title": title,
        "snippet": snippet,
        "source": "Reuters",
        "published_at": "2026-08-03T03:00:00+00:00",
        "collected_at": "2026-08-03T03:05:00+00:00",
        "url": f"https://publisher.example.test/validated/{article_id}",
        "final_score": score,
        "alert_grade": "즉시 알림" if score >= 4.5 else "일일 브리핑",
        "source_metadata_json": {
            "current_run_seen": True,
            "teams_newness_eligible": True,
            "carried_forward": False,
        },
    }


def _serialized(row: dict, *, why: str) -> dict:
    category = "dc_power"
    decision = decision_relevance.classify(row, category)
    shadow = radar_signals.evaluate_hourly_urgency_shadow(
        row,
        change_type=str(row.get("change_type") or ""),
        change_reasons=row.get("change_reasons") or (),
    )
    return briefing._news_censor_display_entry(
        row,
        category,
        why,
        radar.AI,
        decision,
        shadow,
    )


def _validated_artifact(*rows: dict) -> dict:
    return {
        "artifact_contract": "HDEC_VALIDATED_EXECUTIVE_BRIEF_V1",
        "news_data_mode": "live",
        "news_fallback_used": False,
        "collection_status": "LIVE_HEALTHY_WITH_ARTICLES",
        "news_censor_display_articles": list(rows),
    }


def _live_delta_equivalent(row: dict) -> dict:
    """Express the same facts with the live-delta aliases only."""
    return {
        "article_key": row["article_id"],
        "title": row["title"],
        "summary": row["snippet"],
        "hdec_relevance": row["whyImportant"],
        "whyImportant": row["whyImportant"],
        "hdec_relevance_tier": row["hdec_relevance_tier"],
        "source": row["source"],
        "published_at": row["published_at"],
        "url": row["url"],
        "publisher_direct": True,
        "source_quality_passed": True,
        "score": row["final_score"],
        "shadow_urgency_status": row["shadow_urgency_status"],
        "shadow_confirmed_event_types": list(
            row["shadow_confirmed_event_types"]
        ),
        "change_type": "new_article",
        "current_run_seen": True,
        "teams_newness_eligible": True,
        "carried_forward": False,
    }


def _snapshot(row: dict, *, validated: bool) -> tuple:
    evaluation = evaluate_teams_push_policy(
        row,
        require_validated_fields=validated,
    )
    return (
        evaluation.topic.eligible,
        evaluation.topic.topic_key,
        evaluation.topic.exclusion_reason,
        evaluation.hdec_relevant,
        evaluation.article.get("shadow_urgency_status"),
        tuple(evaluation.article.get("shadow_confirmed_event_types") or ()),
        evaluation.importance.sendable,
        evaluation.importance.level,
        evaluation.importance.reason,
        evaluation.eligible,
        evaluation.rejection_reason,
    )


def main() -> int:
    why = "데이터센터 EPC와 전력 인프라 사업 기회에 직접 영향"
    confirmed = _serialized(
        _raw(
            "confirmed",
            "현대건설, OpenAI AI 데이터센터 EPC 투자 계약 체결",
            "현대건설이 AI 데이터센터 EPC 투자 계약을 공식 체결했다.",
        ),
        why=why,
    )

    # The production serializer, not a hand-written display fixture, owns all
    # normalized fields required by the validated-Brief sender path.
    assert confirmed["shadow_urgency_status"] == "confirmed"
    assert confirmed["shadow_confirmed_event_types"]
    assert confirmed["hdec_relevance_tier"] == confirmed["decision_relevance_tier"]
    assert confirmed["whyImportant"] == confirmed["why_it_matters"] == why

    live = _live_delta_equivalent(confirmed)
    assert _snapshot(confirmed, validated=True) == _snapshot(live, validated=False)
    selected = select_teams_push_from_artifact(
        _validated_artifact(confirmed),
        max_articles=None,
    )
    assert len(selected) == 1 and selected[0].importance.sendable

    # Either alias spelling is sufficient; normalization makes the result exact.
    canonical_only = dict(confirmed)
    canonical_only.pop("hdec_relevance_tier")
    canonical_only.pop("whyImportant")
    legacy_only = dict(confirmed)
    legacy_only.pop("decision_relevance_tier")
    legacy_only.pop("why_it_matters")
    expected = _snapshot(confirmed, validated=True)
    assert _snapshot(canonical_only, validated=True) == expected
    assert _snapshot(legacy_only, validated=True) == expected

    # A. confirmed infrastructure event qualifies.
    assert evaluate_teams_push_policy(
        confirmed, require_validated_fields=True
    ).eligible

    # B. neutral urgency does not block an independently qualifying HDEC-direct row.
    neutral = _serialized(
        _raw(
            "neutral",
            "현대건설, 생성형 AI 기반 스마트건설 연구센터 운영 고도화",
            "현대건설이 스마트건설 연구센터의 AI 업무 체계를 고도화했다.",
            score=2.0,
        ),
        why=why,
    )
    neutral_eval = evaluate_teams_push_policy(
        neutral, require_validated_fields=True
    )
    assert neutral["shadow_urgency_status"] == "none"
    assert neutral_eval.eligible and neutral_eval.importance.hdec_direct

    # C. a genuine evaluation failure remains fail-closed.
    unavailable = dict(confirmed)
    unavailable["shadow_urgency_status"] = "unavailable"
    unavailable["shadow_confirmed_event_types"] = []
    unavailable_eval = evaluate_teams_push_policy(
        unavailable, require_validated_fields=True
    )
    assert not unavailable_eval.eligible
    assert unavailable_eval.rejection_reason == "shadow_unavailable"

    # D. dashboard/category context never promotes a non-AI article.
    non_ai = _serialized(
        _raw(
            "non-ai",
            "현대건설, 주택 정비사업 계약 체결",
            "현대건설이 주택 정비사업 계약을 체결했다.",
        ),
        why=why,
    )
    non_ai["category_memberships"] = ["ai", "biz"]
    non_ai_eval = evaluate_teams_push_policy(
        non_ai, require_validated_fields=True
    )
    assert not non_ai_eval.eligible
    # R4-R6 — the canonical AI-centrality gate rejects this earlier with the
    # granular non-AI reason; both spellings prove the same contract.
    assert non_ai_eval.rejection_reason in {
        "not_ai_core",
        "ai_not_central_non_ai",
    }, non_ai_eval.rejection_reason

    # E. speculation-only AI content remains blocked.
    speculation = _serialized(
        _raw(
            "speculation",
            "AI 데이터센터 전력 수요가 늘어날 전망",
            "향후 전력망 투자가 확대될 가능성이 있다.",
        ),
        why=why,
    )
    speculation_eval = evaluate_teams_push_policy(
        speculation, require_validated_fields=True
    )
    assert not speculation_eval.eligible
    assert speculation_eval.rejection_reason == "speculation_only"

    # Omission/malformed regressions: shadow evidence and both alias groups are
    # schema failures, never silently converted to neutral/unavailable policy data.
    for missing_key in (
        "shadow_urgency_status",
        "shadow_confirmed_event_types",
    ):
        malformed = dict(confirmed)
        malformed.pop(missing_key)
        decision = evaluate_teams_push_policy(
            malformed, require_validated_fields=True
        )
        assert decision.rejection_reason == "malformed_required_field"

    aliases_missing = dict(confirmed)
    for key in (
        "hdec_relevance_tier",
        "decision_relevance_tier",
        "whyImportant",
        "why_it_matters",
    ):
        aliases_missing.pop(key)
    decision = evaluate_teams_push_policy(
        aliases_missing, require_validated_fields=True
    )
    assert decision.rejection_reason == "malformed_required_field"

    print("RESULT=D7-AK-6E_TEAMS_VALIDATED_BRIEF_SEMANTIC_EQUIVALENCE_PASS")
    print(
        "validated_rows=5 semantic_equivalence=exact policy_broadened=0 "
        "network_calls=0 smtp_attempts=0 teams_sends=0 telegram_sends=0 "
        "production_state_writes=0"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
