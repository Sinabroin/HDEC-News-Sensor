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
    is_hdec_relevant_for_push,
    map_importance,
    render_article_email,
    select_teams_push_candidates,
    select_teams_push_from_artifact,
)
from app.news_access import (
    LINK_KIND_GOOGLE_NEWS_FALLBACK,
    LINK_KIND_PORTAL_FALLBACK,
    LINK_KIND_PUBLISHER_DIRECT,
    choose_article_link,
    choose_direct_article_url,
)

GOOGLE_AGGREGATOR_URL = "https://news.google.com/rss/articles/teams-fixture"


def article(**overrides):
    base = {
        "article_key": "a-1",
        "title": "OpenAI, 한국 AI 데이터센터에 50억달러 투자 확정",
        "summary": "GPU 기반 데이터센터 투자를 공식 확정했다.",
        "hdec_relevance": "데이터센터 EPC와 전력 인프라 사업기회에 직접 영향",
        "source": "Reuters",
        "published_at": "2026-07-23T00:20:00+00:00",
        "url": "https://publisher.example.test/news/1?utm_source=test",
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

    # R2M — 대시보드 해설에 AI가 있어도 기사 자체가 비AI이면 미발송.
    historical_non_ai_titles = (
        "OCI홀딩스 1.4조 증설 승부수, 차입 5000억",
        "건설을 넘어 에너지 인프라 선도, 대우건설 K원전 주역으로 뛴다",
        "원전·가스터빈 앞세운 두산에너빌리티, 수주잔고 26조 쌓여",
        "두산에너빌리티, 라이양원전 핵심소재 공급계약 체결",
        "대미투자 1호로 가스복합발전소 건설사업 유력",
        "초과이윤 분배, 우리가 착각하는 것들",
        "전기산업계 유휴 시험설비 공유로 시험 적체 해소",
        "변동성 증시 의식했나, 대우건설 실적발표 연기",
        "호남 반도체산단 전력망 구축 본격화",
        "엔비디아 네이버 3대주주 합류, SK이터닉스는 폭락",
        "가평 데이터센터 개발사업 추진",
        "제12차 전기본에 원전 4기·SMR 2기 추가해야",
    )

    for index, title in enumerate(historical_non_ai_titles):
        contaminated = article(
            article_key=f"historical-non-ai-{index}",
            title=title,
            summary="건설·에너지·투자 관련 확정 소식이다.",
            whyImportant=(
                "AI 데이터센터와 현대건설 사업 관점에서 참고할 수 있다."
            ),
            radarReason="AI 전력 인프라 레이더 분류",
            category_label="AI 관련",
            provenance={
                "ai_topic": "ai_power_infrastructure",
                "ai_category": "ai",
            },
            score=4.9,
            shadow_urgency_status="confirmed",
            shadow_confirmed_event_types=[
                "contract_confirmed",
            ],
        )

        decision = classify_ai_topic(contaminated)

        assert not decision.eligible, (title, decision)
        assert decision.exclusion_reason == "ai_not_core_topic", (
            title,
            decision,
        )
        assert not _sendable(contaminated).sendable

    # 기사 뒤쪽의 부수적 AI 언급은 핵심 주제가 아니다.
    minor_ai_mention = article(
        article_key="minor-ai-mention",
        title="가스복합발전소 건설 투자계획 확정",
        summary=(
            "가스복합발전소 건설과 자금조달, 설비 공급 및 착공 일정이 "
            "구체적으로 공개됐다. 사업비와 지분구조, 준공 시점도 발표됐다. "
            "프로젝트 관계자는 장기적으로 일부 AI 활용 가능성도 검토한다고 밝혔다."
        ),
        score=4.9,
    )

    minor_decision = classify_ai_topic(minor_ai_mention)

    assert not minor_decision.eligible, minor_decision
    assert minor_decision.exclusion_reason == "ai_not_core_topic"

    # LNG는 전면 차단하지 않는다. AI가 핵심이고 HDEC 적용성이 있으면 발송 가능.
    ai_lng = article(
        article_key="ai-lng-core",
        title="AI 기반 LNG 플랜트 운영 최적화 시스템 계약 체결",
        summary=(
            "AI가 LNG 플랜트 에너지 사용량과 설비 이상을 "
            "실시간으로 예측하는 시스템 계약이 체결됐다."
        ),
        hdec_relevance=(
            "현대건설 LNG 플랜트 EPC와 운영기술에 직접 적용 가능"
        ),
        score=4.6,
        shadow_urgency_status="confirmed",
        shadow_confirmed_event_types=[
            "contract_confirmed",
        ],
    )

    ai_lng_topic = classify_ai_topic(ai_lng)

    assert ai_lng_topic.eligible, ai_lng_topic
    assert is_hdec_relevant_for_push(
        ai_lng,
        ai_lng_topic,
    )
    assert _sendable(ai_lng).sendable

    # AI 핵심 기사라도 현대건설 사업 관련성이 없으면 대시보드만.
    consumer_ai = article(
        article_key="consumer-ai-no-hdec",
        title="OpenAI, 개인용 AI 사진 꾸미기 앱 정식 출시",
        summary=(
            "개인 소비자가 사진 필터를 만드는 AI 앱을 출시했다."
        ),
        hdec_relevance="",
        whyImportant="",
        radarReason="",
        category="",
        category_label="",
        provenance={},
        score=4.9,
        shadow_urgency_status="confirmed",
        shadow_confirmed_event_types=[
            "product_launch_confirmed",
        ],
    )

    consumer_topic = classify_ai_topic(consumer_ai)

    assert consumer_topic.eligible, consumer_topic
    assert not is_hdec_relevant_for_push(
        consumer_ai,
        consumer_topic,
    )

    consumer_importance = map_importance(
        consumer_ai,
        consumer_topic,
    )

    assert not consumer_importance.sendable
    assert (
        consumer_importance.reason
        == "insufficient_hdec_relevance"
    )

    # D7-AK-6D labeled-link contract: publisher URLs retain precedence, while a usable
    # aggregator hop no longer causes an important article to disappear.
    canonical_direct = "https://canonical.publisher.example.test/story/1"
    external_direct = "https://external.publisher.example.test/story/1"
    original_direct = "https://original.publisher.example.test/story/1"
    naver_originallink = "https://www.yna.co.kr/view/AKR202607270001"
    assert choose_direct_article_url({
        "url": GOOGLE_AGGREGATOR_URL,
        "canonical_url": canonical_direct,
        "external_url": external_direct,
        "original_url": original_direct,
    }) == canonical_direct
    assert choose_direct_article_url({
        "url": GOOGLE_AGGREGATOR_URL,
        "external_url": external_direct,
    }) == external_direct
    assert choose_direct_article_url({
        "url": GOOGLE_AGGREGATOR_URL,
        "original_url": original_direct,
    }) == original_direct
    assert choose_direct_article_url({
        "url": GOOGLE_AGGREGATOR_URL,
        "source_metadata": {
            "provider": "naver_news_api",
            "originallink": naver_originallink,
        },
    }) == naver_originallink
    for fixture, expected_url in (
        ({"url": GOOGLE_AGGREGATOR_URL, "canonical_url": canonical_direct}, canonical_direct),
        ({"url": GOOGLE_AGGREGATOR_URL, "external_url": external_direct}, external_direct),
        ({"url": GOOGLE_AGGREGATOR_URL, "original_url": original_direct}, original_direct),
        ({"url": GOOGLE_AGGREGATOR_URL,
          "source_metadata": {"originallink": naver_originallink}}, naver_originallink),
    ):
        selected = choose_article_link(fixture)
        assert (
            selected.url == expected_url
            and selected.kind == LINK_KIND_PUBLISHER_DIRECT
            and selected.label == "원문"
            and selected.is_direct
        ), selected

    google_link = choose_article_link({"url": GOOGLE_AGGREGATOR_URL})
    assert (
        google_link.url == GOOGLE_AGGREGATOR_URL
        and google_link.kind == LINK_KIND_GOOGLE_NEWS_FALLBACK
        and google_link.label == "Google News 경유"
        and not google_link.is_direct
    ), google_link
    google_candidates = select_teams_push_candidates([
        article(article_key="aggregator-only", url=GOOGLE_AGGREGATOR_URL)
    ])
    assert len(google_candidates) == 1

    naver_portal = "https://n.news.naver.com/article/001/0012345678"
    daum_portal = "https://v.daum.net/v/20260727090000123"
    for portal_url in (naver_portal, daum_portal):
        portal_link = choose_article_link({"url": portal_url})
        assert (
            portal_link.url == portal_url
            and portal_link.kind == LINK_KIND_PORTAL_FALLBACK
            and portal_link.label == "포털 경유"
            and not portal_link.is_direct
        ), portal_link
        assert len(select_teams_push_candidates([
            article(article_key=f"portal-{portal_url}", url=portal_url)
        ])) == 1

    assert select_teams_push_candidates([
        article(article_key="no-url", url="", canonical_url="")
    ]) == ()

    alert = {
        "generated_at": "2026-07-23T09:31:00+09:00",
        "dashboard_url": "https://example.com/dashboard",
        "report_url": "https://example.com/report",
    }
    _subject, fallback_text, fallback_html = render_article_email(
        alert, google_candidates[0]
    )
    assert fallback_text.splitlines()[0] == (
        f"[Google News 경유] {GOOGLE_AGGREGATOR_URL}"
    )
    assert f'href="{GOOGLE_AGGREGATOR_URL}"' in fallback_html
    assert "[Google News 경유]" in fallback_html
    assert "[원문]" not in fallback_text and "[원문]" not in fallback_html

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
        article(article_key=f"m-{i}", url=f"https://publisher.example.test/m/{i}",
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
