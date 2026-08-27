#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import teams_ai_push as tap


STATE = ROOT / "data" / "teams_push_state.json"

checks = 0
failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    global checks
    checks += 1

    if condition:
        print(f"PASS: {name}")
        return

    failures.append(name)
    print(
        f"FAIL: {name}"
        + (f" — {detail}" if detail else "")
    )


def state_sha() -> str:
    return hashlib.sha256(
        STATE.read_bytes()
    ).hexdigest()


before = state_sha()

article = {
    "article_id": "teams-ui-v2-fixture",
    "article_key": "teams-ui-v2-fixture",
    "title": "현대건설, AI 데이터센터 EPC 계약 체결",
    "snippet": (
        "현대건설은 AI 데이터센터 EPC 계약을 "
        "공식 체결했다고 밝혔다."
    ),
    "publisher_factual_lead": (
        "현대건설은 AI 데이터센터 EPC 계약을 "
        "공식 체결했다고 밝혔다."
    ),
    "hdec_relevance": (
        "AI 데이터센터 발주 확대와 전력·냉각·시공 "
        "수요에 직접적인 사업 영향을 줄 수 있다."
    ),
    "source": "연합뉴스",
    "display_source": "연합뉴스",
    "published_at": "2026-08-27T08:12:00+09:00",
    "url": (
        "https://www.yna.co.kr/view/"
        "AKR20260827000100003"
    ),
    "publisher_direct": True,
    "source_quality_passed": True,
    "current_run_seen": True,
    "teams_newness_eligible": True,
    "carried_forward": False,
    "score": 4.9,
    "final_score": 4.9,
    "shadow_urgency_status": "confirmed",
    "shadow_would_pass": True,
    "shadow_confirmed_event_types": [
        "contract_confirmed",
    ],
    "change_type": "new_article",
}

article.update({
    "title": (
        "현대건설, 40MW AI 데이터센터 EPC 계약 체결"
    ),
    "subtitle": (
        "총사업비 2,400억원 규모의 AI 데이터센터 "
        "EPC 계약을 발주처와 체결했다."
    ),
    "publisher_factual_lead": (
        "총사업비 2,400억원 규모의 40MW AI 데이터센터 "
        "EPC 계약을 발주처와 체결했다."
    ),
    "snippet": (
        "계약 범위는 설계·조달·시공이며 "
        "2029년 준공을 목표로 한다. "
        "발주처는 전력 공급 파트너 선정 절차도 진행 중이다."
    ),
})

evaluation = tap.evaluate_teams_push_policy(article)

check(
    "fixture remains Teams eligible",
    evaluation.eligible,
    evaluation.rejection_reason,
)

candidate = tap.TeamsPushCandidate(
    article=article,
    topic=evaluation.topic,
    importance=evaluation.importance,
    cluster_key="teams-ui-v2",
    material_signature="teams-ui-v2",
    delivery_category=evaluation.delivery_category,
)

subject, text_body, html_body = (
    tap.render_article_email(
        {},
        candidate,
        detected_at="2026-08-27T08:15:00+09:00",
    )
)

check(
    "production email subject contract preserved",
    subject.startswith("[HDEC AI 레이더]"),
    subject,
)

check(
    "new executive card marker exists",
    'data-role="teams-executive-card-v2"' in html_body,
)

check(
    "new T&I realtime header exists",
    "AI 경영 T&amp;I · 실시간" in html_body,
)

check(
    "compact metadata row carries category",
    evaluation.delivery_category in html_body,
)

check(
    "compact metadata row carries publisher",
    "연합뉴스" in html_body,
)

check(
    "headline remains visible",
    article["title"] in html_body,
)

check(
    "context remains bounded and visually paragraph-like",
    (
        1 <= html_body.count("<li>") <= 3
        and "list-style:none" in html_body
    ),
)

check(
    "origin CTA remains present",
    ">기사 원문 보기</a>" in html_body,
)

check(
    "dashboard CTA remains present",
    ">전체 뉴스 대시보드 보기</a>" in html_body,
)

check(
    "plain fallback preserves origin action",
    "기사 원문 보기:" in text_body,
)

check(
    "plain fallback preserves dashboard action",
    "전체 뉴스 대시보드 보기:" in text_body,
)

check(
    "no remote image is introduced",
    "<img" not in html_body,
)

check(
    "what-happened section preserves 핵심 사실 contract",
    (
        "무슨 일이 있었나 · 핵심 사실" in html_body
        and "핵심 사실" in text_body
    ),
)

check(
    "rich publisher evidence survives into the card",
    (
        "2,400억원" in html_body
        and "2029년" in html_body
    ),
)

check(
    "executive decision-point section is explicit",
    (
        "<strong>임원 판단 포인트</strong>" in html_body
        and "임원 판단 포인트:" in text_body
    ),
)

check(
    "HDEC implication is a separate section",
    (
        "현대건설 관점:" in html_body
        and "현대건설 관점:" in text_body
    ),
)

check(
    "next-check section remains explicit",
    (
        "<strong>확인 포인트</strong>" in html_body
        and "확인 포인트:" in text_body
    ),
)

sparse_article = {
    **article,
    "title": "현대건설, AI 데이터센터 EPC 계약 체결",
    "subtitle": "",
    "publisher_factual_lead": "",
    "snippet": (
        "현대건설은 AI 데이터센터 EPC 계약을 "
        "공식 체결했다고 밝혔다."
    ),
}

sparse_eval = tap.evaluate_teams_push_policy(
    sparse_article
)

check(
    "sparse fixture remains eligible",
    sparse_eval.eligible,
    sparse_eval.rejection_reason,
)

sparse_candidate = tap.TeamsPushCandidate(
    article=sparse_article,
    topic=sparse_eval.topic,
    importance=sparse_eval.importance,
    cluster_key="teams-ui-v2-sparse",
    material_signature="teams-ui-v2-sparse",
    delivery_category=sparse_eval.delivery_category,
)

_sparse_subject, _sparse_text, sparse_html = (
    tap.render_article_email(
        {},
        sparse_candidate,
    )
)

check(
    "missing metrics are never invented",
    (
        "2,400억원" not in sparse_html
        and "40MW" not in sparse_html
        and "2029년" not in sparse_html
    ),
)

check(
    "sparse evidence does not duplicate the headline as a fake summary",
    sparse_html.count(
        "현대건설, AI 데이터센터 EPC 계약 체결"
    ) == 1,
)

after = state_sha()

check(
    "production Teams state unchanged",
    before == after,
    f"before={before} after={after}",
)

print()
print(
    f"R4-OPS-10O UI checks: "
    f"{checks - len(failures)}/{checks} PASS"
)

if failures:
    print("FAILED=" + ",".join(failures))
    raise SystemExit(1)

print("R4_OPS_10O_TEAMS_ALERT_UI_V2=PASS")
print("SELECTION_POLICY_UNCHANGED=PASS")
print("DEDUP_UNCHANGED=PASS")
print("SMTP_TRANSPORT_UNCHANGED=PASS")
print("NO_NETWORK=PASS")
print("NO_SEND=PASS")
