#!/usr/bin/env python3
"""Deterministic verifier for the D7-AK-6C Teams AI selection/importance policy.

Covers the leaf-owned half of the approved fixtures: shadow status is a signal, not a
hard gate (confirmed → top basis + rank boost; ambiguous/none never auto-block; blocked/
unavailable fail closed); importance reuses the existing INSTANT/DAILY thresholds; the
retained exclusions (stock/theme, promo/review, speculation-only, recruit/book, low
source) still drop articles even at a high score; the per-run cap is up to ten; and the
artifact-level ``shadow_alert_delta`` flag no longer gates candidate generation. The
delivery/SMTP/persist half lives in verify_teams_ai_push_production.py and the dedup half
in verify_teams_push_state.py.
"""

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
    MAX_TEAMS_ARTICLES,
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


def _sendable(art):
    return map_importance(art, classify_ai_topic(art))


def main() -> int:
    assert MAX_TEAMS_ARTICLES == 10, MAX_TEAMS_ARTICLES

    # 1. confirmed + INSTANT score → 최우선(TOP), 발송.
    positive = article()
    topic = classify_ai_topic(positive)
    assert topic.eligible and topic.topic_key == "ai_datacenter"
    assert _sendable(positive).level == IMPORTANCE_TOP

    # 2. ambiguous 이지만 사실 기반 중요 기사 → 발송. shadow ambiguous 는 자동 차단이 아니다.
    #    (a) ambiguous + INSTANT score → TOP  (b) ambiguous + DAILY + 확정 행위 → IMPORTANT
    amb_top = article(
        article_key="a-2a", shadow_urgency_status="ambiguous", shadow_would_pass=False,
        shadow_confirmed_event_types=[], score=4.9,
    )
    assert _sendable(amb_top).sendable and _sendable(amb_top).level == IMPORTANCE_TOP
    amb_important = article(
        article_key="a-2b",
        title="삼성전자, AI 반도체 파운드리 증설 계약 체결",
        summary="AI 반도체 파운드리 증설 계약을 체결했다.",
        shadow_urgency_status="ambiguous", shadow_would_pass=False,
        shadow_confirmed_event_types=[], score=3.9,
    )
    d = _sendable(amb_important)
    assert d.sendable and d.level == IMPORTANCE_IMPORTANT, d

    # 3. none 이지만 DAILY 이상인 실제 출시 → 발송(중요). shadow none 도 자동 차단이 아니다.
    none_launch = article(
        article_key="a-3",
        title="삼성전자, 생성형 AI 반도체 설계 자동화 솔루션 정식 출시",
        summary="설계 자동화 솔루션을 정식 출시했다.",
        shadow_urgency_status="none", shadow_would_pass=False,
        shadow_confirmed_event_types=[], score=3.7,
    )
    d = _sendable(none_launch)
    assert d.sendable and d.level == IMPORTANCE_IMPORTANT, d

    # 4. blocked → 미발송.
    blocked = article(
        article_key="a-4", shadow_urgency_status="blocked", shadow_would_pass=False,
        shadow_confirmed_event_types=[],
    )
    assert _sendable(blocked).reason == "shadow_blocked"
    assert not _sendable(blocked).sendable

    # 5. unavailable → fail-closed 미발송. 상태 필드가 아예 없는 malformed 도 unavailable 로 닫힌다.
    unavailable = article(
        article_key="a-5", shadow_urgency_status="unavailable", shadow_would_pass=False,
        shadow_confirmed_event_types=[],
    )
    assert _sendable(unavailable).reason == "shadow_unavailable"
    malformed = article(article_key="a-5b")
    del malformed["shadow_urgency_status"]
    assert _sendable(malformed).reason == "shadow_unavailable"

    # 6. 전망뿐인 기사 → 확정 행위가 없으므로 classify 단계에서 제외.
    forecast = article(
        article_key="a-6", title="AI 전력 수요가 늘어날 전망",
        summary="향후 전력망 투자가 확대될 가능성", shadow_urgency_status="none",
        shadow_would_pass=False, shadow_confirmed_event_types=[],
    )
    assert classify_ai_topic(forecast).exclusion_reason == "speculation_without_confirmed_event"

    # 7. 주가·테마주 → 제외(점수가 높아도).
    stock = article(article_key="a-7", title="AI 데이터센터 관련주 급등, 목표주가 상향", score=4.9)
    assert classify_ai_topic(stock).exclusion_reason == "stock_or_theme_article"

    # E. 채용·도서 등 사건 아님(확정 행위 없음) → 제외. 착공 등 확정 행위가 함께면 뉴스로 통과.
    recruit = article(
        article_key="a-8", title="현대건설, AI 데이터센터 운영 인력 대규모 채용 공고",
        summary="데이터센터 운영 인력을 대규모로 채용한다.", score=4.8,
        shadow_confirmed_event_types=[],
    )
    assert classify_ai_topic(recruit).exclusion_reason == "non_news_recruit_or_book", classify_ai_topic(recruit)
    # Regression (D7-AK-6C canary): talent-seeking HR PR must not reach 최우선 via hdec_direct.
    # Aggregated-snippet action noise (e.g. another article's '수주') must not rescue it either.
    seek_talent = article(
        article_key="a-8b", title="현대건설, AI·디지털 역량 갖춘 스마트건설 인재 찾는다",
        summary="현대건설이 스마트건설 수주 확대를 위해 인재를 찾는다.", source="아시아투데이",
        score=4.2, shadow_urgency_status="none", shadow_would_pass=False,
        shadow_confirmed_event_types=[],
    )
    assert classify_ai_topic(seek_talent).exclusion_reason == "non_news_recruit_or_book", classify_ai_topic(seek_talent)
    assert not map_importance(seek_talent, classify_ai_topic(seek_talent)).sendable

    # confirmed 대형 이벤트(정책 확정)는 점수와 무관하게 최우선.
    policy = article(
        article_key="a-9", title="정부, AI 기본법 시행령 확정",
        summary="AI 규제 세부 기준이 확정됐다.", score=3.8,
        shadow_confirmed_event_types=["policy_confirmed"],
    )
    assert _sendable(policy).level == IMPORTANCE_TOP

    # boundary: AI 신호가 전혀 없는 데이터센터 계약 → 미적격.
    boundary = article(
        article_key="a-boundary",
        title="Company said data center construction contract was signed",
        summary="No machine terms are present.",
    )
    assert not classify_ai_topic(boundary).eligible

    # 16. shadow_alert_delta=false 여도 중요 후보가 있으면 후보를 만든다(플래그는 더 이상 게이트가 아님).
    live_payload = {
        "source": "live-delta",
        "shadow_alert_delta": True,
        "articles": [none_launch, positive, policy, amb_top],
    }
    assert len(select_teams_push_from_artifact(live_payload)) == 4
    no_flag = {**live_payload, "shadow_alert_delta": False}
    assert len(select_teams_push_from_artifact(no_flag)) == 4
    del no_flag["shadow_alert_delta"]
    assert len(select_teams_push_from_artifact(no_flag)) == 4

    # 19. mock/fallback 아티팩트 → 0 (라이브 소스 가드는 유지).
    assert select_teams_push_from_artifact({**live_payload, "source": "mock-delta"}) == ()

    # ranking: 최우선 먼저, 동일 등급이면 현대건설 직접 영향 → score → 최신성.
    candidates = select_teams_push_candidates([
        none_launch,
        article(article_key="a-hdec", title="현대건설, AI 데이터센터 EPC 계약 체결", score=4.8),
        positive,
        policy,
        amb_top,
    ])
    assert len(candidates) == 5
    assert all(c.importance.sendable for c in candidates)
    assert candidates[0].importance.level == IMPORTANCE_TOP
    assert candidates[0].importance.hdec_direct is True  # 현대건설 직접 영향이 최우선 내 최상단

    # 12. 12건 후보 → 상위 10건만 선택.
    twelve = [
        article(article_key=f"m-{i}", url=f"https://example.com/m/{i}",
                title=f"OpenAI, AI 데이터센터 투자 계약 체결 {i}", score=4.6)
        for i in range(12)
    ]
    capped = select_teams_push_candidates(twelve, max_articles=10)
    assert len(capped) == 10, len(capped)
    # 하드 상한을 넘기려 해도 10으로 고정된다.
    assert len(select_teams_push_candidates(twelve, max_articles=99)) == 10

    # card render (unchanged contract): 7 fields, single 원문 보기 action, no webhook secret,
    # only the selected article's URL is present.
    alert = {
        "generated_at": "2026-07-23T09:31:00+09:00",
        "dashboard_url": "https://guides.playground-aidesignlab.co.kr/HDEC-News-Sensor/daily/dashboard-latest.html",
        "report_url": "https://guides.playground-aidesignlab.co.kr/HDEC-News-Sensor/daily/latest.html",
    }
    card = build_candidate_card(alert, candidates[0])
    assert card["type"] == "message" and len(card["attachments"]) == 1
    content = card["attachments"][0]["content"]
    assert content["type"] == "AdaptiveCard" and content["version"] == "1.4"
    rendered = json.dumps(card, ensure_ascii=False)
    for required in ("핵심 요약", "현대건설 영향", "출처", "게시시각", "감지시각", "원문 보기", "대시보드 보기"):
        assert required in rendered
    assert "TEAMS_WORKFLOW_WEBHOOK_URL" not in rendered
    selected_url = candidates[0].article["url"]
    assert selected_url in rendered
    nonselected = {c.article["url"] for c in candidates[1:]} - {selected_url}
    assert all(url not in rendered for url in nonselected)
    assert [a["title"] for a in content["actions"]].count("원문 보기") == 1

    print("RESULT=D7-AK-6C_TEAMS_AI_PUSH_VERIFIER_PASS")
    print(f"cap={MAX_TEAMS_ARTICLES} selected={len(candidates)} "
          f"top={sum(c.importance.level == IMPORTANCE_TOP for c in candidates)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
