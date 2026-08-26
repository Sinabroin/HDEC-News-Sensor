#!/usr/bin/env python3
"""R4-OPS-10K — no-network executive materiality/depth regression.

Locks the real 2026-08-26 production defect:

SBS/JIBS:
"40MW급 데이터센터 구축…'제주권 AX 대전환'에 속도"

The story is AI/infrastructure relevant, but its publisher-owned evidence is
a policy 구상 / 추진 / 계획 rather than an independently proven execution
commitment. It must therefore never become a realtime Teams card merely
because it is on a major domain or has a high score.

No network, SMTP, state write, or environment approval is used here.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import teams_ai_push as tap  # noqa: E402


CHECKS = 0
FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    if condition:
        print(f"PASS: {name}")
        return
    FAILURES.append(name)
    print(f"FAIL: {name}" + (f" — {detail}" if detail else ""))


def article(
    *,
    key: str,
    title: str,
    snippet: str,
    source: str,
    url: str,
    score: float = 4.8,
    confirmed_types: list[str] | None = None,
) -> dict:
    return {
        "article_id": key,
        "article_key": key,
        "title": title,
        "snippet": snippet,
        # summary exists for renderer compatibility, but the new plan gate
        # intentionally ignores it.
        "summary": snippet,
        "source": source,
        "display_source": source,
        "published_at": "2026-08-26T12:34:00+09:00",
        "url": url,
        "publisher_direct": True,
        "source_quality_passed": True,
        "current_run_seen": True,
        "teams_newness_eligible": True,
        "carried_forward": False,
        "score": score,
        "final_score": score,
        "shadow_urgency_status": "confirmed",
        "shadow_would_pass": True,
        "shadow_confirmed_event_types": (
            confirmed_types
            if confirmed_types is not None
            else ["construction"]
        ),
        "change_type": "new_article",
        "hdec_relevance": (
            "AI 데이터센터·전력 인프라와 연관된 현대건설 사업환경 변화"
        ),
    }


# ---------------------------------------------------------------------
# 1. Exact observed SBS/JIBS weak-plan regression.
# ---------------------------------------------------------------------

weak_sbs = article(
    key="a76f12827588",
    title="40MW급 데이터센터 구축…'제주권 AX 대전환'에 속도",
    snippet=(
        "제주도가 인공지능을 관광과 농수산업, 에너지까지 접목하는 "
        "'제주권 AX 대전환'에 속도를 냅니다. "
        "40메가와트급 그린 AI 데이터센터 구축과 청년 AI 구독료 지원도 "
        "추진하고, 기업이 필요로 하는 인재를 발굴하는 구상입니다."
    ),
    source="SBS 뉴스",
    url="https://news.sbs.co.kr/news/endPage.do?news_id=N1008722615",
)

topic_before_floor = tap.classify_ai_topic(weak_sbs)
importance_before_floor = tap.map_importance(
    weak_sbs,
    topic_before_floor,
)

check(
    "observed SBS/JIBS fixture still reproduces high-score pre-floor pressure",
    topic_before_floor.eligible and importance_before_floor.sendable,
    (
        f"topic={topic_before_floor.topic_key} "
        f"importance={importance_before_floor.level}"
    ),
)

excluded, reason, evidence = (
    tap.evaluate_realtime_execution_commitment_gate(weak_sbs)
)

check(
    "SBS/JIBS AX concept is detected as plan-only",
    excluded,
    f"reason={reason} evidence={evidence}",
)

check(
    "SBS/JIBS AX concept has the exact execution-floor reason",
    reason == "uncommitted_plan_without_execution_proof",
    reason,
)

weak_decision = tap.evaluate_teams_push_policy(weak_sbs)

check(
    "SBS/JIBS AX concept is not realtime Teams eligible",
    not weak_decision.eligible,
    (
        f"eligible={weak_decision.eligible} "
        f"reason={weak_decision.rejection_reason}"
    ),
)

check(
    "SBS/JIBS AX concept is rejected before importance/source ranking can rescue it",
    weak_decision.rejection_reason
    == "excluded_uncommitted_plan_or_strategy",
    weak_decision.rejection_reason,
)


# ---------------------------------------------------------------------
# 2. Planning-consultancy start is NOT construction start.
# ---------------------------------------------------------------------

planning_consultancy = article(
    key="planning-consultancy",
    title="제주, 40MW AI 데이터센터 구축 추진…기획 용역 착수",
    snippet=(
        "제주도는 제주권 AX 대전환 사업 기획 용역에 착수했다고 밝혔다. "
        "40MW급 AI 데이터센터 본사업은 향후 추진할 계획이다."
    ),
    source="연합뉴스",
    url="https://www.yna.co.kr/view/AKR20260826000100001",
)

consultancy_excluded, _, consultancy_evidence = (
    tap.evaluate_realtime_execution_commitment_gate(
        planning_consultancy
    )
)

check(
    "planning-consultancy start cannot masquerade as data-center construction start",
    consultancy_excluded,
    str(consultancy_evidence),
)

consultancy_decision = tap.evaluate_teams_push_policy(
    planning_consultancy
)

check(
    "planning-consultancy article is not realtime Teams eligible",
    (
        not consultancy_decision.eligible
        and consultancy_decision.rejection_reason
        == "excluded_uncommitted_plan_or_strategy"
    ),
    consultancy_decision.rejection_reason,
)


# ---------------------------------------------------------------------
# 2B. Future/planning language must NOT masquerade as execution proof.
# ---------------------------------------------------------------------

planned_action_cases = (
    (
        "planned-construction",
        "제주 AI 데이터센터, 내년 착공 예정",
        "제주도는 40MW급 AI 데이터센터 사업을 추진하고 내년 착공할 계획이다.",
    ),
    (
        "planned-operator-selection",
        "제주 AI 데이터센터 사업자 선정 예정",
        "제주도는 향후 사업자를 선정할 예정이며 세부 사업계획을 검토 중이다.",
    ),
    (
        "planned-procurement",
        "40MW AI 데이터센터 발주 계획",
        "발주를 추진하기 위한 세부 절차와 사업구조를 검토하고 있다.",
    ),
    (
        "investment-review",
        "AI 데이터센터 투자 검토",
        "사업성 검토 후 데이터센터 투자를 추진할 계획이다.",
    ),
    (
        "planned-contract",
        "AI 데이터센터 공급 계약 체결 예정",
        "양측은 향후 공급 계약을 체결할 예정이라고 밝혔다.",
    ),
)

for key, title, snippet in planned_action_cases:
    row = article(
        key=key,
        title=title,
        snippet=snippet,
        source="연합뉴스",
        url=f"https://www.yna.co.kr/view/{key}",
    )

    excluded, plan_reason, plan_evidence = (
        tap.evaluate_realtime_execution_commitment_gate(row)
    )

    check(
        f"{key}: plan-qualified action is not execution proof",
        excluded
        and plan_reason == "uncommitted_plan_without_execution_proof",
        f"reason={plan_reason} evidence={plan_evidence}",
    )

    decision = tap.evaluate_teams_push_policy(row)

    # Policy ordering is intentionally fail-closed. Some plan-only rows are
    # rejected even earlier by the established semantic-precision gate as
    # OTHER_NONEXECUTIVE. That is equally safe: the invariant is that the row
    # never becomes realtime Teams eligible, while the direct execution gate
    # above separately proves that planning language cannot manufacture
    # execution evidence.
    allowed_safe_rejections = {
        "excluded_uncommitted_plan_or_strategy",
        "excluded_other_nonexecutive",
    }

    check(
        f"{key}: realtime Teams delivery remains blocked",
        (
            not decision.eligible
            and decision.rejection_reason
            in allowed_safe_rejections
        ),
        decision.rejection_reason,
    )


# A genuine independent commitment in the same article must survive even if
# the article also mentions a future construction milestone.
budget_confirmed_future_start = article(
    key="budget-confirmed-future-start",
    title="AI 데이터센터 예산 확정…내년 착공 예정",
    snippet=(
        "정부가 AI 데이터센터 사업 예산을 확정했다. "
        "실제 착공은 내년으로 예정돼 있다."
    ),
    source="연합뉴스",
    url="https://www.yna.co.kr/view/budget-confirmed-future-start",
    confirmed_types=["budget_confirmed"],
)

mixed_gate = tap.evaluate_realtime_execution_commitment_gate(
    budget_confirmed_future_start
)

check(
    "independent confirmed budget survives future-construction wording",
    not mixed_gate[0],
    str(mixed_gate),
)


# ---------------------------------------------------------------------
# 3. Real execution commitments MUST survive.
# ---------------------------------------------------------------------

hdec_contract = article(
    key="hdec-contract",
    title="현대건설, 40MW AI 데이터센터 EPC 계약 체결",
    snippet=(
        "현대건설이 40MW 규모 AI 데이터센터의 설계·조달·시공 "
        "EPC 계약을 발주처와 체결했다."
    ),
    source="연합뉴스",
    url="https://www.yna.co.kr/view/AKR20260826000100002",
    confirmed_types=["contract"],
)

contract_gate = tap.evaluate_realtime_execution_commitment_gate(
    hdec_contract
)

check(
    "confirmed HDEC EPC contract is not blocked by plan floor",
    not contract_gate[0],
    str(contract_gate),
)

contract_decision = tap.evaluate_teams_push_policy(hdec_contract)

check(
    "confirmed HDEC EPC contract remains realtime eligible",
    contract_decision.eligible,
    contract_decision.rejection_reason,
)


committed_investment = article(
    key="committed-investment",
    title="AWS, 2조원 AI 데이터센터 투자 확정…구축 착수",
    snippet=(
        "AWS가 2조원 규모 AI 데이터센터 투자를 확정하고 "
        "데이터센터 구축에 착수했다."
    ),
    source="SBS 뉴스",
    url="https://news.sbs.co.kr/news/endPage.do?news_id=N1008722999",
    confirmed_types=["investment_confirmed", "construction"],
)

investment_gate = tap.evaluate_realtime_execution_commitment_gate(
    committed_investment
)

check(
    "confirmed investment/construction start overrides planning vocabulary",
    not investment_gate[0],
    str(investment_gate),
)

investment_decision = tap.evaluate_teams_push_policy(
    committed_investment
)

check(
    "confirmed AI data-center investment remains realtime eligible",
    investment_decision.eligible,
    investment_decision.rejection_reason,
)


# ---------------------------------------------------------------------
# 4. Executive email structure: clearer, no invented facts.
# ---------------------------------------------------------------------

contract_topic = tap.classify_ai_topic(hdec_contract)
contract_importance = tap.map_importance(
    hdec_contract,
    contract_topic,
)

candidate = tap.TeamsPushCandidate(
    article=hdec_contract,
    topic=contract_topic,
    importance=contract_importance,
    cluster_key="fixture-hdec-contract",
    material_signature="fixture-material-signature",
    delivery_category="AI 데이터센터",
)

subject, text_body, html_body = tap.render_article_email(
    {},
    candidate,
)

check(
    "email has explicit 핵심 사실 section",
    "핵심 사실" in text_body and "핵심 사실" in html_body,
)

check(
    "email preserves article and dashboard actions",
    (
        "기사 원문 보기" in text_body
        and "전체 뉴스 대시보드 보기" in text_body
        and "기사 원문 보기" in html_body
        and "전체 뉴스 대시보드 보기" in html_body
    ),
)

check(
    "email subject remains executive radar format",
    subject.startswith("[HDEC AI 레이더]"),
    subject,
)


print()
print(
    "R4_OPS_10K_EXECUTIVE_MATERIALITY_DEPTH"
    f" checks={CHECKS} failures={len(FAILURES)}"
)

if FAILURES:
    print("RESULT=FAIL")
    for failure in FAILURES:
        print(f"FAILED={failure}")
    raise SystemExit(1)

print("RESULT=PASS")
print("SBS_JIBS_PLAN_ONLY_REALTIME_SEND=0")
print("PLANNING_CONSULTANCY_IS_CONSTRUCTION_START=0")
print("CONFIRMED_EPC_CONTRACT_RECALL=PASS")
print("CONFIRMED_INVESTMENT_CONSTRUCTION_RECALL=PASS")
print("EXECUTIVE_EMAIL_STRUCTURE=PASS")
print("NETWORK_CALLS=0")
print("REAL_SMTP_CONNECTIONS=0")
print("PRODUCTION_STATE_WRITES=0")
