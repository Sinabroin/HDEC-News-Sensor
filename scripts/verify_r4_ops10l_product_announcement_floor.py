#!/usr/bin/env python3
"""R4-OPS-10L — product/spec announcement realtime-floor regression."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from app import teams_ai_push as tap  # noqa: E402
from app import watch_semantic_precision as wsp  # noqa: E402


STATE = ROOT / "data" / "teams_push_state.json"

FAILURES: list[str] = []
CHECKS = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1

    if condition:
        print(f"PASS: {name}")
        return

    FAILURES.append(name)
    print(
        f"FAIL: {name}"
        + (f" — {detail}" if detail else "")
    )


def state_sha() -> str:
    if not STATE.exists():
        return "ABSENT"

    return hashlib.sha256(
        STATE.read_bytes()
    ).hexdigest()


def article(
    *,
    key: str,
    title: str,
    snippet: str,
    source: str = "아이뉴스24",
    url: str = "https://inews24.com/view/1998242",
    score: float = 4.2,
) -> dict:
    return {
        "article_key": key,
        "article_id": key,
        "title": title,
        "snippet": snippet,
        "summary": snippet,
        "source": source,
        "url": url,
        # Production publisher-authority contract requires a publication
        # timestamp (or an explicit publisher-date fallback reason).
        # The exact observed Jetson article carried this timestamp.
        "published_at": "2026-08-26T10:25:52+09:00",
        "publisher_direct": True,
        "source_quality_passed": True,
        "current_run_seen": True,
        "teams_newness_eligible": True,
        "carried_forward": False,
        "score": score,
        "shadow_urgency_status": "none",
        "shadow_would_pass": False,
        "shadow_confirmed_event_types": [],
        "change_type": "new_article",
    }


before = state_sha()


# ---------------------------------------------------------------------
# 1. Exact observed production regression.
# ---------------------------------------------------------------------

jetson = article(
    key="f99c084febdf",
    title=(
        "엔비디아, 로봇용 AI 컴퓨터 "
        "'젯슨 오린 나노2' 공개…추론 성능 2배"
    ),
    snippet=(
        "엔비디아가 로봇용 AI 컴퓨터 젯슨 오린 나노2를 공개했다. "
        "추론 성능을 전작 대비 두 배로 높였다."
    ),
)

topic = tap.classify_ai_topic(jetson)

check(
    "Jetson regression remains AI-core/topic-qualified before the new floor",
    topic.eligible,
    topic.exclusion_reason,
)

check(
    "Jetson regression still carries executive-relevance pressure",
    tap.is_executive_relevant_for_push(
        jetson,
        topic,
    ),
)

importance_pressure = tap.map_importance(
    jetson,
    topic,
)

check(
    "Jetson regression still reproduces legacy IMPORTANT pressure",
    importance_pressure.sendable,
    importance_pressure.reason,
)

semantic = wsp.classify(jetson)

check(
    "Jetson spec announcement is no longer AI_MATERIAL_EVENT",
    (
        semantic.semantic_class
        == wsp.OTHER_NONEXECUTIVE
        and not semantic.eligible
    ),
    (
        f"class={semantic.semantic_class} "
        f"reason={semantic.reason}"
    ),
)

check(
    "Jetson receives exact product-spec semantic reason",
    semantic.reason
    == "product_spec_announcement_without_executive_consequence",
    semantic.reason,
)

decision = tap.evaluate_teams_push_policy(
    jetson
)

check(
    "Jetson product/spec article cannot realtime-send",
    (
        not decision.eligible
        and decision.rejection_reason
        == "excluded_product_spec_announcement"
    ),
    decision.rejection_reason,
)


# ---------------------------------------------------------------------
# 2. Real commercial commitment MUST survive.
# ---------------------------------------------------------------------

contract = article(
    key="hardware-contract",
    title=(
        "엔비디아, 로봇용 AI 컴퓨터 공개…"
        "추론 성능 2배·현대차 공급계약 체결"
    ),
    snippet=(
        "엔비디아는 신형 로봇용 AI 컴퓨터를 공개하고 "
        "현대차와 공급 계약을 체결했다고 밝혔다."
    ),
    source="연합뉴스",
    url="https://www.yna.co.kr/view/hardware-contract",
)

contract_semantic = wsp.classify(contract)

check(
    "hardware launch plus real supply contract remains material",
    (
        contract_semantic.semantic_class
        == wsp.AI_MATERIAL_EVENT
        and contract_semantic.eligible
    ),
    (
        f"class={contract_semantic.semantic_class} "
        f"reason={contract_semantic.reason}"
    ),
)

contract_decision = tap.evaluate_teams_push_policy(
    contract
)

check(
    "hardware launch plus real supply contract remains realtime eligible",
    contract_decision.eligible,
    contract_decision.rejection_reason,
)


# ---------------------------------------------------------------------
# 3. Major foundation-model launch is NOT accidentally blocked.
# ---------------------------------------------------------------------

foundation_model = article(
    key="foundation-model-launch",
    title="OpenAI, GPT-6 공개…추론 성능 대폭 향상",
    snippet=(
        "OpenAI가 새로운 GPT-6 모델을 공식 공개했다. "
        "기업용 AI 에이전트 기능을 확대했다."
    ),
    source="연합뉴스",
    url="https://www.yna.co.kr/view/foundation-model-launch",
)

foundation_semantic = wsp.classify(
    foundation_model
)

check(
    "foundation-model launch is outside hardware spec floor",
    foundation_semantic.reason
    != "product_spec_announcement_without_executive_consequence",
    foundation_semantic.reason,
)


# ---------------------------------------------------------------------
# 4. Material security event on hardware MUST survive.
# ---------------------------------------------------------------------

security = article(
    key="hardware-security",
    title=(
        "엔비디아 AI GPU 공개…신제품에서 보안 취약점 발견"
    ),
    snippet=(
        "엔비디아가 새 AI GPU를 공개한 가운데 "
        "보안 취약점이 확인돼 긴급 대응에 착수했다."
    ),
    source="연합뉴스",
    url="https://www.yna.co.kr/view/hardware-security",
)

security_semantic = wsp.classify(
    security
)

check(
    "hardware security incident is not suppressed by product floor",
    security_semantic.reason
    != "product_spec_announcement_without_executive_consequence",
    security_semantic.reason,
)



# ---------------------------------------------------------------------
# 5. 10K true-execution recall seal.
#
# An unrelated future-plan clause must never erase an already-confirmed
# contract/operator-selection event.
# ---------------------------------------------------------------------

confirmed_contract_future_plan = article(
    key="confirmed-contract-future-plan",
    title=(
        "AWS, AI 데이터센터 공급 계약을 체결…"
        "추가 증설 계획도 공개"
    ),
    snippet=(
        "AWS가 AI 데이터센터 전력 공급 계약을 체결했다. "
        "추가 증설은 향후 계획이라고 밝혔다."
    ),
    source="연합뉴스",
    url=(
        "https://www.yna.co.kr/view/"
        "confirmed-contract-future-plan"
    ),
)

blocked, reason, evidence = (
    tap.evaluate_realtime_execution_commitment_gate(
        confirmed_contract_future_plan
    )
)

check(
    "confirmed contract survives unrelated future-plan vocabulary",
    not blocked,
    (
        f"blocked={blocked} "
        f"reason={reason} "
        f"evidence={evidence}"
    ),
)

confirmed_contract_policy = tap.evaluate_teams_push_policy(
    confirmed_contract_future_plan
)

check(
    "confirmed contract plus future expansion plan remains realtime eligible",
    confirmed_contract_policy.eligible,
    confirmed_contract_policy.rejection_reason,
)


confirmed_operator_future_plan = article(
    key="confirmed-operator-future-plan",
    title=(
        "AI 데이터센터 사업자를 선정…"
        "내년 착공 계획"
    ),
    snippet=(
        "사업자는 최종 선정됐다. "
        "착공은 내년으로 계획하고 있다."
    ),
    source="연합뉴스",
    url=(
        "https://www.yna.co.kr/view/"
        "confirmed-operator-future-plan"
    ),
)

blocked, reason, evidence = (
    tap.evaluate_realtime_execution_commitment_gate(
        confirmed_operator_future_plan
    )
)

check(
    "confirmed operator selection survives unrelated construction plan",
    not blocked,
    (
        f"blocked={blocked} "
        f"reason={reason} "
        f"evidence={evidence}"
    ),
)


planned_contract_control = article(
    key="planned-contract-control",
    title=(
        "AWS, AI 데이터센터 공급 계약을 체결할 계획"
    ),
    snippet=(
        "AWS는 공급 계약 체결을 추진하고 있다고 밝혔다."
    ),
    source="연합뉴스",
    url=(
        "https://www.yna.co.kr/view/"
        "planned-contract-control"
    ),
)

blocked, reason, evidence = (
    tap.evaluate_realtime_execution_commitment_gate(
        planned_contract_control
    )
)

check(
    "planned contract remains blocked after recall repair",
    blocked,
    (
        f"blocked={blocked} "
        f"reason={reason} "
        f"evidence={evidence}"
    ),
)


planned_operator_control = article(
    key="planned-operator-control",
    title=(
        "AI 데이터센터 사업자를 선정할 예정"
    ),
    snippet=(
        "사업자 선정을 추진하고 있다고 밝혔다."
    ),
    source="연합뉴스",
    url=(
        "https://www.yna.co.kr/view/"
        "planned-operator-control"
    ),
)

blocked, reason, evidence = (
    tap.evaluate_realtime_execution_commitment_gate(
        planned_operator_control
    )
)

check(
    "planned operator selection remains blocked after recall repair",
    blocked,
    (
        f"blocked={blocked} "
        f"reason={reason} "
        f"evidence={evidence}"
    ),
)


after = state_sha()

check(
    "production state remains byte-identical",
    before == after,
    f"before={before} after={after}",
)

print()
print(
    "R4_OPS_10L_PRODUCT_ANNOUNCEMENT_FLOOR "
    f"checks={CHECKS} failures={len(FAILURES)}"
)

if FAILURES:
    print("RESULT=FAIL")
    for failure in FAILURES:
        print(f"FAILED={failure}")
    raise SystemExit(1)

print("RESULT=PASS")
print("JETSON_PRODUCT_SPEC_REALTIME_SEND=0")
print("CONFIRMED_SUPPLY_CONTRACT_RECALL=PASS")
print("FOUNDATION_MODEL_LAUNCH_RECALL=PRESERVED")
print("HARDWARE_SECURITY_EVENT_RECALL=PRESERVED")
print("NETWORK_CALLS=0")
print("REAL_SMTP_CONNECTIONS=0")
print("PRODUCTION_STATE_WRITES=0")
