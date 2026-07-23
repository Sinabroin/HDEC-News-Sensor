#!/usr/bin/env python3
"""Deterministic verifier for D7-AK-5B Teams AI topic/importance/card behavior."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.teams_ai_push import (
    IMPORTANCE_IMPORTANT,
    IMPORTANCE_TOP,
    build_candidate_card,
    classify_ai_topic,
    map_importance,
    select_teams_push_candidates,
    select_teams_push_from_artifact,
)


def article(**overrides):
    base = {
        "article_key": "a-1",
        "title": "OpenAI, 한국 AI 데이터센터에 50억달러 투자 확정",
        "summary": "GPU 기반 데이터센터 투자를 공식 확정했다.",
        "hdec_relevance": "데이터센터 EPC와 전력 인프라 사업기회에 직접 영향",
        "source": "Reuters",
        "published_at": "2026-07-23T00:20:00+00:00",
        "url": "https://example.com/news/1?utm_source=test",
        "score": 4.7,
        "shadow_urgency_status": "confirmed",
        "shadow_would_pass": True,
        "shadow_confirmed_event_types": ["investment_confirmed"],
        "change_type": "new_article",
    }
    base.update(overrides)
    return base


def main() -> int:
    positive = article()
    topic = classify_ai_topic(positive)
    assert topic.eligible and topic.topic_key == "ai_datacenter"
    importance = map_importance(positive, topic)
    assert importance.sendable and importance.level == IMPORTANCE_TOP

    ambiguous = article(
        article_key="a-2", shadow_urgency_status="ambiguous", shadow_would_pass=False,
        shadow_confirmed_event_types=[], score=4.9,
    )
    assert not map_importance(ambiguous, classify_ai_topic(ambiguous)).sendable

    stock = article(article_key="a-3", title="AI 데이터센터 관련주 급등, 목표주가 상향")
    assert classify_ai_topic(stock).exclusion_reason == "stock_or_theme_article"

    boundary = article(
        article_key="a-boundary",
        title="Company said data center construction contract was signed",
        summary="No artificial-intelligence terms are present.",
    )
    assert not classify_ai_topic(boundary).eligible

    forecast = article(
        article_key="a-4", title="AI 전력 수요가 늘어날 전망",
        summary="향후 전력망 투자가 확대될 가능성", shadow_would_pass=False,
        shadow_confirmed_event_types=[],
    )
    assert classify_ai_topic(forecast).exclusion_reason == "speculation_without_confirmed_event"

    important = article(
        article_key="a-5", title="정부, AI 기본법 시행령 확정",
        summary="AI 규제 세부 기준이 확정됐다.", score=3.8,
        shadow_confirmed_event_types=["policy_confirmed"],
    )
    decision = map_importance(important, classify_ai_topic(important))
    # confirmed policy is a major event and therefore top priority under the approved rule.
    assert decision.level == IMPORTANCE_TOP

    low_daily = article(
        article_key="a-6", title="BIM 기반 설계 자동화 솔루션 정식 출시",
        summary="건설 BIM 자동화 제품이 출시됐다.", score=3.6,
        shadow_confirmed_event_types=["product_available"],
    )
    low_decision = map_importance(low_daily, classify_ai_topic(low_daily))
    assert low_decision.sendable and low_decision.level == IMPORTANCE_IMPORTANT

    live_payload = {
        "source": "live-delta",
        "shadow_alert_delta": True,
        "articles": [low_daily, positive, important, ambiguous],
    }
    assert len(select_teams_push_from_artifact(live_payload)) == 3
    assert select_teams_push_from_artifact({**live_payload, "source": "mock-delta"}) == ()
    assert select_teams_push_from_artifact({**live_payload, "shadow_alert_delta": False}) == ()

    candidates = select_teams_push_candidates([
        low_daily,
        article(article_key="a-7", title="현대건설, AI 데이터센터 EPC 계약 체결", score=4.8),
        positive,
        important,
        ambiguous,
    ])
    assert len(candidates) == 3
    assert all(candidate.importance.sendable for candidate in candidates)
    assert candidates[0].importance.level == IMPORTANCE_TOP

    alert = {
        "generated_at": "2026-07-23T09:31:00+09:00",
        "dashboard_url": "https://guides.playground-aidesignlab.co.kr/HDEC-News-Sensor/daily/dashboard-latest.html",
        "report_url": "https://guides.playground-aidesignlab.co.kr/HDEC-News-Sensor/daily/latest.html",
    }
    card = build_candidate_card(alert, candidates[0])
    assert card["type"] == "message"
    assert len(card["attachments"]) == 1
    content = card["attachments"][0]["content"]
    assert content["type"] == "AdaptiveCard" and content["version"] == "1.4"
    rendered = json.dumps(card, ensure_ascii=False)
    for required in ("핵심 요약", "현대건설 영향", "출처", "게시시각", "감지시각", "원문 보기", "대시보드 보기"):
        assert required in rendered
    assert "TEAMS_WORKFLOW_WEBHOOK_URL" not in rendered
    selected_url = candidates[0].article["url"]
    assert selected_url in rendered
    nonselected_urls = {item["url"] for item in [low_daily, positive, important] if item["url"] != selected_url}
    assert all(url not in rendered for url in nonselected_urls)
    action_titles = [action["title"] for action in content["actions"]]
    assert action_titles.count("원문 보기") == 1

    print("RESULT=D7-AK-5B_TEAMS_AI_PUSH_VERIFIER_PASS")
    print(f"selected={len(candidates)} top={sum(c.importance.level == IMPORTANCE_TOP for c in candidates)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
