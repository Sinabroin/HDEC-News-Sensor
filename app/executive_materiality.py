"""Shared executive-materiality contract (R4-OPS-2).

Single source of truth for "is this AI-central article materially useful to a
Hyundai E&C executive?".  Extracted verbatim from the R4-R17 Daily Executive
Qualification Gate (`app/editorial_briefings._executive_qualification`) so the
Daily editorial curation surface and the real-time Teams AI News Watch judge
send-eligibility materiality by the *same* deterministic rule and can never
drift apart.

Two products, one floor:

* `executive_qualification(evidence)` — the canonical R4-R17 gate, byte-for-byte
  the logic Daily already ships.  Daily delegates to it (pure refactor);
  the Teams Watch adds it as its send-eligibility materiality floor.
* `is_fund_product_launch_noise(evidence)` — an additional precision gate the
  real-time Watch applies (Daily does not: its editorial relevance floor
  already drops fund/ETF rows upstream).  An ETF / fund / REIT product launch
  is a financial-product event, not a structural AI-industry event; it is
  executive noise UNLESS the same title/lead independently carries a hard
  material signal (confirmed corporate action + scale, HDEC-direct, or a
  material AI-security incident).  Mirrors the observed production leak
  (연합뉴스 "…전략산업 ETF 출시", 2026-08-10) that the Watch sent as important
  while the Daily gate would never publish it.

Allowed evidence — title, publisher subtitle, and the first factual publisher
lead/snippet sentence only (never a generated summary/why-it-matters and never
the provider query string), so no search-query metadata can qualify a row.
Evidence is supplied by the caller as a mapping with the keys ``title``,
``snippet``, ``subtitle``, ``publisher_section`` — each caller builds it from
its own factual fields, keeping generated text out of the gate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from app import ai_centrality

# --------------------------------------------------------------------------- #
# Material corporate/industrial event vocabulary (R4-R17 §11 factor 2).
# NOTE: "출시" (bare product launch) is deliberately absent — a launch is not a
# confirmed *material* corporate action.  The Teams Watch importance path treats
# "출시" as a confirmed action, which is exactly the leak this floor closes.
# --------------------------------------------------------------------------- #
MATERIAL_ACTION_TERMS: tuple[str, ...] = (
    "체결", "확정", "수주", "선정", "승인", "착공", "준공", "인수", "합병",
    "발주", "계약", "출자", "증설", "가동", "타결", "발효", "시행",
)
MATERIAL_SCALE_RE = re.compile(
    r"[0-9][0-9,.]*\s*(?:조|억|천억|백억|만)\s*(?:원|달러)|[0-9][0-9,.]*\s*(?:GW|MW|기가와트|메가와트)"
)
MATERIAL_RISK_TERMS: tuple[str, ...] = (
    "중대재해", "붕괴", "제재", "소송", "규제", "행정처분", "영업정지", "리콜",
)

HDEC_DIRECT_TERMS: tuple[str, ...] = (
    "현대건설", "현대엔지니어링", "힐스테이트", "디에이치",
)

# Strategic HDEC infrastructure domains (physical/industrial layer HDEC builds
# or operates).  Mirrors ai_centrality._ENABLING_INFRA_TERMS in spirit.
EXEC_STRATEGIC_DOMAIN_TERMS: tuple[str, ...] = (
    "데이터센터", "데이터 센터", "datacenter", "data center", "idc",
    "전력망", "전력 인프라", "전력인프라", "송전", "변전", "송배전", "발전소",
    "원전", "원자력", "smr", "소형모듈원전", "소형모듈원자로", "그리드",
    "냉각", "용수", "전기자재",
    "스마트건설", "스마트 건설", "bim", "디지털 트윈", "digital twin",
    "건설로봇", "건설 로봇", "건설 자동화", "시공 자동화",
)

# Actual impact / constraint signals — the material consequence that turns a
# bare strategic-domain noun into an executive-relevant story.  A domain noun
# alone is never enough.
EXEC_IMPACT_SIGNAL_TERMS: tuple[str, ...] = (
    # power demand / capacity
    "전력 수요", "전력수요", "수요 급증", "수요 폭증", "전력 부족", "용량 부족",
    "전력 확보", "전력난",
    # grid constraint / bottleneck
    "계통 제약", "계통 포화", "병목", "제약", "포화",
    # shortage / supply constraint
    "부족", "공급난", "품귀", "수급", "조달 차질", "공급 차질",
    # expansion / siting / permitting
    "증설", "확충", "부지", "입지", "인허가", "인허가 지연", "허가 지연",
    # local opposition
    "반대", "반발", "민원", "주민 반발", "갈등", "저항", "논란",
    # delay / cost
    "지연", "차질", "중단", "비용 급등", "원가 부담", "비용 부담",
    # cooling / water requirement
    "냉각", "용수", "물 부족",
    # regulation entering the picture
    "규제", "의무화", "가이드라인",
)

# Material AI security / risk incidents (factual central AI incident).
EXEC_AI_SECURITY_TERMS: tuple[str, ...] = (
    "해킹", "침해", "유출", "탈취", "취약점", "익스플로잇", "악용", "랜섬웨어",
    "딥페이크", "위조", "사칭", "금지", "차단", "제재", "단속", "처분",
    "리콜", "안전사고", "오작동", "장애 사태",
)

# Financial-product vehicles (R4-OPS-2).  A launch/offering of one of these is a
# financial-product event, not a structural AI-industry event.  "펀드" is safe
# to match as a substring: "펀더멘털" (fundamental) does not contain it, and a
# genuine fund-linked material event (e.g. "…펀드 통해 5000억 출자 계약") is
# rescued by the hard-material-signal check in is_fund_product_launch_noise.
FUND_PRODUCT_TERMS: tuple[str, ...] = (
    "etf", "상장지수펀드", "상장지수 펀드", "펀드", "리츠", "reits", "랩어카운트",
)


@dataclass(frozen=True)
class ExecutiveQualification:
    """Deterministic Executive Qualification Gate verdict for one candidate."""

    qualified: bool
    reason: str


def materiality_score(title: str, summary: str) -> tuple[float, tuple[str, ...]]:
    """R4-R17 §11 factor 2 — confirmed action, concrete scale, or material risk."""
    text = f"{title} {summary}"
    score = 0.0
    reasons: list[str] = []
    action = next((term for term in MATERIAL_ACTION_TERMS if term in text), "")
    if action:
        score += 1.0
        reasons.append(f"confirmed_action:{action}")
    if MATERIAL_SCALE_RE.search(text):
        score += 0.5
        reasons.append("concrete_scale_figure")
    risk = next((term for term in MATERIAL_RISK_TERMS if term in text), "")
    if risk:
        score += 0.5
        reasons.append(f"material_risk:{risk}")
    return round(min(score, 2.0), 3), tuple(reasons)


def executive_qualification(evidence: Mapping[str, Any]) -> ExecutiveQualification:
    """R4-R17 §B — is this AI-central candidate materially useful to an executive?

    Returns qualified=True only when the title / subtitle / factual lead carry a
    strong material signal (structural AI event, HDEC-direct AI event, confirmed
    corporate/industrial event, AI security incident, or a strategic HDEC
    infrastructure domain paired with an actual impact/constraint signal).
    Opinion-labelled pieces require a hard factual signal (1/2/3/5) and never
    qualify on a strategic-domain+impact pairing alone.

    This is the canonical materiality floor shared by the Daily editorial
    curation surface and the real-time Teams AI News Watch.
    """
    title = ai_centrality.article_title(evidence)
    subtitle = ai_centrality.article_subtitle(evidence)
    lead = ai_centrality.article_lead_sentence(evidence)  # factual, lowercased
    zone = " ".join(
        part for part in (title.lower(), subtitle.lower(), lead) if part
    )
    opinion = ai_centrality.opinion_labeled(evidence)

    # Signal 1 — structural AI causal event (canonical leaf, title+lead only).
    event_class, _terms = ai_centrality.structural_ai_causal_event(evidence)
    if event_class:
        return ExecutiveQualification(True, f"structural_ai_event:{event_class}")

    # Signal 3 — HDEC-direct AI event (highest executive priority).
    hdec_hit = next(
        (term for term in HDEC_DIRECT_TERMS
         if term in title or term in subtitle or term in lead),
        "",
    )
    if hdec_hit:
        return ExecutiveQualification(True, f"hdec_direct_ai:{hdec_hit}")

    # Signal 2 — material corporate/industrial event (confirmed action, concrete
    # KRW/USD or MW/GW scale, or material risk) proven from title + factual lead.
    _mscore, mreasons = materiality_score(title, lead)
    if mreasons:
        return ExecutiveQualification(True, f"material_event:{mreasons[0]}")

    # Signal 5 — material AI security / risk incident.
    security_hit = next((term for term in EXEC_AI_SECURITY_TERMS if term in zone), "")
    if security_hit:
        return ExecutiveQualification(True, f"ai_security_event:{security_hit}")

    # Signal 4 — strategic HDEC infrastructure domain WITH an actual impact /
    # constraint signal.  A bare strategic-domain noun is not enough, and an
    # opinion piece never qualifies on this pairing alone.
    if not opinion:
        domain_hit = next(
            (term for term in EXEC_STRATEGIC_DOMAIN_TERMS if term in zone), ""
        )
        impact_hit = next(
            (term for term in EXEC_IMPACT_SIGNAL_TERMS if term in zone), ""
        )
        if domain_hit and impact_hit:
            return ExecutiveQualification(
                True, f"strategic_infra_impact:{domain_hit}->{impact_hit}"
            )

    return ExecutiveQualification(
        False,
        "opinion_without_hard_material_signal"
        if opinion
        else "no_material_executive_signal",
    )


def is_fund_product_launch_noise(evidence: Mapping[str, Any]) -> bool:
    """R4-OPS-2 — is this a financial-product (ETF/fund/REIT) story with no
    independent material industrial event?

    Real-time Watch precision gate only (Daily's editorial relevance floor
    already drops these upstream).  Returns True (→ reject) when a fund-product
    vehicle is the subject and the title/lead carries no hard material signal:
    no confirmed material action / concrete scale / material risk (Signal 2),
    no HDEC-direct entity (Signal 3), and no material AI-security incident
    (Signal 5).  A fund-linked story that DOES carry such a signal (e.g. a
    scaled 출자/계약 or an HDEC-direct deal) is a separate material event and is
    NOT treated as noise — matching R4-OPS-2 §9 ("REJECT unless the article
    itself contains a separate material industrial event").
    """
    title = ai_centrality.article_title(evidence)
    lead = ai_centrality.article_lead_sentence(evidence)  # factual, lowercased
    title_l = title.lower()
    if not any(term in title_l for term in FUND_PRODUCT_TERMS):
        return False
    zone = f"{title.lower()} {lead}"
    _mscore, mreasons = materiality_score(title, lead)
    hdec_hit = any(term in title or term in lead for term in HDEC_DIRECT_TERMS)
    security_hit = any(term in zone for term in EXEC_AI_SECURITY_TERMS)
    # Fund product WITHOUT any hard material signal → executive noise.
    return not (mreasons or hdec_hit or security_hit)
