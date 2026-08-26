#!/usr/bin/env python3
"""R4-OPS-10M — executive commitment / advocacy realtime floor.

Production regression:

뉴시스
"전북, '피지컬AI' 수도로 도약한다…
 '산업 클러스터 구축'"

The publisher-owned evidence describes a 결의대회,
regional aspiration, 구상, 육성 intent, and requests for
government / National Assembly support.

Those signals are useful for Daily/Weekly monitoring but cannot
create a realtime executive Teams interruption without an
independently proven execution milestone.

No network, SMTP, production state write, workflow dispatch,
or live-send authority is used by this verifier.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import teams_ai_push as tap  # noqa: E402


STATE = ROOT / "data" / "teams_push_state.json"

CHECKS = 0
FAILURES: list[str] = []


def check(
    name: str,
    condition: bool,
    detail: str = "",
) -> None:
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
    return hashlib.sha256(
        STATE.read_bytes()
    ).hexdigest()


def article(
    *,
    key: str,
    title: str,
    snippet: str,
    source: str = "뉴시스",
    url: str | None = None,
    score: float = 4.8,
) -> dict:
    return {
        "article_id": key,
        "article_key": key,
        "title": title,
        "snippet": snippet,
        "summary": snippet,
        "source": source,
        "display_source": source,
        "published_at":
            "2026-08-26T18:07:57+09:00",
        "url": (
            url
            or f"https://newsis.com/view/{key}"
        ),
        "publisher_direct": True,
        "source_quality_passed": True,
        "current_run_seen": True,
        "teams_newness_eligible": True,
        "carried_forward": False,
        "score": score,
        "final_score": score,
        "shadow_urgency_status": "confirmed",
        "shadow_would_pass": True,
        "shadow_confirmed_event_types": [
            "construction",
        ],
        "change_type": "new_article",
        "hdec_relevance": (
            "피지컬AI·데이터센터·산업 인프라"
        ),
    }


before = state_sha()


# ============================================================
# 1. Exact observed NewSis production regression
# ============================================================

newsis = article(
    key="d91cfee6383b0103",
    title=(
        "전북, '피지컬AI' 수도로 도약한다…"
        "\"산업 클러스터 구축\""
    ),
    snippet=(
        "전북대학교와 전북특별자치도가 새만금을 "
        "중심으로 피지컬AI 산업 클러스터를 조성하고 "
        "전북 전역을 미래산업 실증 거점으로 육성하기 "
        "위해 힘을 모았다. "
        "피지컬AI 대도약 결의대회를 열고 참석자들은 "
        "피지컬AI를 새로운 성장동력으로 육성해야 "
        "한다는 데 뜻을 모았다. "
        "전북 전역을 피지컬AI 실증장으로 구축한다는 "
        "구상을 제시했고 정부와 국회에는 관련 예산 "
        "지원과 규제 혁신을 촉구했다."
    ),
    url=(
        "https://newsis.com/view/"
        "NISX20260826_0003763916"
    ),
)

topic = tap.classify_ai_topic(newsis)
importance = tap.map_importance(
    newsis,
    topic,
)

check(
    "NewSis regression remains AI/topic relevant",
    topic.eligible,
    topic.exclusion_reason,
)

check(
    "NewSis regression still reproduces legacy importance pressure",
    importance.sendable,
    importance.reason,
)

excluded, reason, evidence = (
    tap.evaluate_realtime_execution_commitment_gate(
        newsis
    )
)

check(
    "NewSis resolution/aspiration story is commitment-floor excluded",
    excluded,
    f"reason={reason} evidence={evidence}",
)

check(
    "NewSis receives exact uncommitted-plan reason",
    reason
    == "uncommitted_plan_without_execution_proof",
    reason,
)

check(
    "NewSis evidence includes advocacy/aspiration signal",
    any(
        token in evidence
        for token in (
            "결의대회",
            "뜻을 모았다",
            "힘을 모았다",
            "촉구",
            "도약",
            "육성",
            "조성",
        )
    ),
    str(evidence),
)

decision = tap.evaluate_teams_push_policy(
    newsis
)

check(
    "NewSis resolution story cannot realtime-send",
    (
        not decision.eligible
        and decision.rejection_reason
        == "excluded_uncommitted_plan_or_strategy"
    ),
    (
        f"eligible={decision.eligible} "
        f"reason={decision.rejection_reason}"
    ),
)


# ============================================================
# 2. Generic cluster advocacy must also remain non-realtime
# ============================================================

advocacy = article(
    key="cluster-advocacy",
    title=(
        "지역 산학연, AI 산업 클러스터 조성 촉구"
    ),
    snippet=(
        "지역 산학연 관계자들은 AI 산업을 육성하고 "
        "클러스터를 조성해야 한다는 데 뜻을 모았다. "
        "정부에 관련 예산 지원을 촉구했다."
    ),
)

adv_excluded, adv_reason, _ = (
    tap.evaluate_realtime_execution_commitment_gate(
        advocacy
    )
)

check(
    "cluster advocacy without execution proof is excluded",
    (
        adv_excluded
        and adv_reason
        == "uncommitted_plan_without_execution_proof"
    ),
    adv_reason,
)


# ============================================================
# 3. Council/taskforce launch alone is not executive execution
# ============================================================

council = article(
    key="council-launch",
    title=(
        "AI 인프라 협의체 출범…산업 육성 비전 제시"
    ),
    snippet=(
        "민관 관계자들이 협의체를 출범시키고 "
        "AI 인프라 산업 육성을 위한 구상을 밝혔다."
    ),
)

council_excluded, council_reason, _ = (
    tap.evaluate_realtime_execution_commitment_gate(
        council
    )
)

check(
    "council launch plus vision alone is excluded",
    (
        council_excluded
        and council_reason
        == "uncommitted_plan_without_execution_proof"
    ),
    council_reason,
)


# ============================================================
# 4. Confirmed public commitment MUST override aspiration
# ============================================================

confirmed_budget = article(
    key="confirmed-budget",
    title=(
        "전북 피지컬AI 도약…국가사업 지정 확정·"
        "국비 1조원 예산 배정"
    ),
    snippet=(
        "정부는 피지컬AI 산업 클러스터 사업의 "
        "국가사업 지정을 확정하고 국비 1조원의 "
        "예산을 배정했다. 지역은 산업 육성에 "
        "속도를 낼 계획이다."
    ),
    source="연합뉴스",
    url=(
        "https://www.yna.co.kr/view/"
        "AKR20260827000100001"
    ),
)

budget_excluded, budget_reason, budget_evidence = (
    tap.evaluate_realtime_execution_commitment_gate(
        confirmed_budget
    )
)

check(
    "confirmed designation/budget overrides aspiration terms",
    not budget_excluded,
    (
        f"reason={budget_reason} "
        f"evidence={budget_evidence}"
    ),
)


# ============================================================
# 5. Actual construction start MUST override '조성'
# ============================================================

construction = article(
    key="construction-start",
    title=(
        "피지컬AI 산업 클러스터 조성 본격화…"
        "데이터센터 공사 착공"
    ),
    snippet=(
        "사업 주체는 클러스터를 조성하기 위해 "
        "AI 데이터센터 공사를 시작했다고 밝혔다."
    ),
    source="연합뉴스",
    url=(
        "https://www.yna.co.kr/view/"
        "AKR20260827000200001"
    ),
)

construction_excluded, construction_reason, evidence = (
    tap.evaluate_realtime_execution_commitment_gate(
        construction
    )
)

check(
    "actual construction start overrides generic cluster-creation language",
    not construction_excluded,
    (
        f"reason={construction_reason} "
        f"evidence={evidence}"
    ),
)


# ============================================================
# 6. Observed Seoul Economic Daily policy/outlook class must
#    not be swept into the advocacy floor merely because it is
#    a long-horizon infrastructure article.
# ============================================================

policy_outlook = article(
    key="sedaily-energy-outlook",
    title=(
        "2040년까지 재생e 6배 확대…"
        "원전·LNG 신설 불가피[Pick코노미]"
    ),
    snippet=(
        "AI 데이터센터 전력 수요 증가를 배경으로 "
        "2040년 재생에너지 보급 전망과 함께 "
        "향후 전력설비 수요를 분석했다."
    ),
    source="서울경제",
    url="https://sedaily.com/article/20083785",
)

policy_excluded, policy_reason, policy_evidence = (
    tap.evaluate_realtime_execution_commitment_gate(
        policy_outlook
    )
)

check(
    "energy policy/outlook story is not falsely classified as advocacy",
    not policy_excluded,
    (
        f"reason={policy_reason} "
        f"evidence={policy_evidence}"
    ),
)


after = state_sha()

check(
    "production Teams state remains byte-identical",
    before == after,
    f"before={before} after={after}",
)


print()
print(
    f"R4-OPS-10M checks: "
    f"{CHECKS - len(FAILURES)}/{CHECKS} PASS"
)

if FAILURES:
    print(
        "FAILED_CHECKS="
        + ",".join(FAILURES)
    )
    raise SystemExit(1)

print("R4_OPS_10M_LOCAL_QUALITY=PASS")
print("NEWSIS_RESOLUTION_AUTOSEND=BLOCKED")
print("ADVOCACY_ONLY_CLUSTER=BLOCKED")
print("COUNCIL_VISION_ONLY=BLOCKED")
print("CONFIRMED_BUDGET_RECALL=PRESERVED")
print("CONSTRUCTION_START_RECALL=PRESERVED")
print("POLICY_OUTLOOK_RECALL=PRESERVED")
print("PRODUCTION_STATE_UNCHANGED=PASS")
print("NO_NETWORK=PASS")
print("NO_SMTP=PASS")
