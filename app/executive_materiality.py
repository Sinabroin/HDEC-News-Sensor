"""Shared executive-materiality contract (R4-OPS-2).

Home of the shared executive-materiality primitives used by two intentionally
*different* products — the R4-R17 Daily editorial curation surface and the
real-time Teams AI News Watch.  The two do NOT share one whole gate: the Watch
is deliberately broader (D7-AK-6C §8) and must keep alerting on strategic
real-time AI events the strict Daily whitelist drops.  What they share is the
materiality *primitives* and one **fund-product noise invariant**, so neither
surface can regress on the financial-product noise class that leaked into
production.

* `executive_qualification(evidence)` — the canonical R4-R17 Daily gate,
  byte-for-byte the logic Daily already ships.  Daily delegates to it (pure
  refactor).  As of R4-OPS-2A it rejects a financial-product launch first (see
  below), before any structural-AI-event acceptance.  The Watch does NOT apply
  this whole gate as its floor — that would drop the strategic real-time events
  it must keep.
* `is_fund_product_launch_noise(evidence)` — the shared fund-product noise
  invariant, applied on BOTH surfaces.  An ETF / fund / REIT product launch is a
  financial-product event, not a structural AI-industry event; it is executive
  noise UNLESS the same title/lead independently carries a real material
  industrial event (a non-launch confirmed corporate action in an
  industrial/strategic context, an HDEC-direct entity PAIRED WITH such an
  action, or a material AI-security incident).  Neither fund SIZE / offering
  scale (R4-OPS-2A) nor a bare HDEC mention as a theme constituent / holding
  (R4-OPS-2B) ever rescues it.  The fund vehicle is detected across the title
  AND the first factual lead sentence, so a vehicle named only in the lead is
  still caught (R4-OPS-2B).  Mirrors the observed production leak (연합뉴스
  "…전략산업 ETF 출시", 2026-08-10) that the Watch sent as important while the
  Daily surface would never publish it.

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

# R4-OPS-6C — Watch-only materiality vocabulary.  These terms are deliberately
# separate from ``MATERIAL_ACTION_TERMS``: a generic announcement, meeting,
# proposal, or product launch must not become an executive realtime event just
# because AI is central.  Daily/Editor curation remains broader and continues
# to use ``executive_qualification`` above.
WATCH_PROPOSAL_DISCUSSION_TERMS: tuple[str, ...] = (
    "방문", "제안", "건의", "논의", "모색", "검토", "희망", "환영",
    "관심 표명", "협력 요청", "실증 제안",
)

WATCH_LOCAL_POLITICAL_TERMS: tuple[str, ...] = (
    "시장", "도지사", "지사", "국회의원", "의원", "국회", "예결위원장",
    "시의회", "도의회", "군수", "구청장", "지자체", "지방정부",
)
WATCH_LOCAL_ADMIN_ACTIVITY_TERMS: tuple[str, ...] = (
    "지역 현안", "국비", "예산 확보", "예산 반영", "지역 발전", "지역사업",
    "지역 사업", "지방 현안", "클러스터 지원",
)

WATCH_SIGNED_CONTRACT_TERMS: tuple[str, ...] = (
    "본계약 체결", "계약 체결", "계약을 체결", "공급계약", "공급 계약",
    "수주 계약", "사업 협약 체결", "업무협약 체결", "협약을 체결",
    "signed contract", "contract signed", "binding agreement",
)
WATCH_CONFIRMED_INVESTMENT_TERMS: tuple[str, ...] = (
    "투자 확정", "투자를 확정", "투자하기로", "투자한다", "출자 확정",
    "출자를 확정", "출자한다", "committed investment", "will invest",
)
WATCH_AWARD_ORDER_TERMS: tuple[str, ...] = (
    "수주 확정", "수주했", "수주했다", "낙찰", "발주 확정", "발주했다",
    "우선협상대상자", "awarded", "order secured",
)
WATCH_CONSTRUCTION_START_TERMS: tuple[str, ...] = (
    "착공했다", "착공한다", "착공에 들어", "착공 확정", "착공이 확정", "첫 삽을",
    "건설에 착수", "구축에 착수", "구축 착수", "construction began",
    "broke ground",
)
WATCH_FINAL_APPROVAL_TERMS: tuple[str, ...] = (
    "최종 승인", "본승인", "사업 승인", "인허가 승인", "허가를 승인",
    "final approval", "finally approved",
)
WATCH_ENACTED_REGULATION_TERMS: tuple[str, ...] = (
    "법 시행", "규제 시행", "시행령 공포", "법안 통과", "법률 공포",
    "발효됐다", "발효한다", "enacted", "takes effect",
)
WATCH_COMMITTED_BUDGET_TERMS: tuple[str, ...] = (
    "예산 확정", "예산을 확정", "예산 배정", "예산을 배정", "국비 확정",
    "국비 반영 확정", "committed budget", "budget approved",
)
WATCH_BINDING_MOU_TERMS: tuple[str, ...] = (
    "양해각서", "mou", "memorandum of understanding",
)
WATCH_LAUNCH_TERMS: tuple[str, ...] = (
    "정식 출시", "서비스 개시", "서비스를 개시", "상용화", "생산 개시",
    "가동 시작", "가동했다", "launched", "entered production",
)
WATCH_LAUNCH_IMPACT_TERMS: tuple[str, ...] = (
    "전사", "생산", "산업", "데이터센터", "데이터 센터", "전력망",
    "스마트건설", "건설", "bim", "설계 자동화", "규제", "공공서비스",
    "enterprise", "production",
    "industrial", "data center", "grid",
)
WATCH_WORKFORCE_CONSTRAINT_TERMS: tuple[str, ...] = (
    "인력 부족", "인력난", "숙련공 부족", "인력 확보에 어려움",
    "인력을 확보하는 데 어려움", "인력 확보가 어렵",
    "미충원", "workforce shortage", "labor shortage", "skills shortage",
)

WATCH_FINANCIAL_PRODUCT_TERMS: tuple[str, ...] = (
    "선물", "옵션", "파생상품", "파생 상품", "스왑", "etf", "상장지수펀드",
    "상장지수 펀드", "리츠", "reits", "구조화 상품", "토큰화 상품",
    "futures", "option contract", "derivative", "swap contract",
)
WATCH_FINANCIAL_FRAMING_TERMS: tuple[str, ...] = (
    "거래", "상장", "선물시장", "선물 시장", "가격 변동성", "헤지", "지수",
    "투자자", "표준가격", "reference price", "trading", "listed", "hedge",
    "price exposure",
)
WATCH_FINANCIAL_INDUSTRIAL_ACTION_TERMS: tuple[str, ...] = (
    "epc", "건설 공급", "건설 계약", "시공 계약", "구축 계약", "구축에 착수",
    "전력 인프라", "전력망 건설", "변전", "송전", "냉각", "용수",
    "착공", "수주", "발주", "투자 확정", "투자를 확정",
) + HDEC_DIRECT_TERMS

WATCH_CAPACITY_RE = re.compile(
    r"[0-9][0-9,.]*\s*(?:GW|MW|기가와트|메가와트|kW|킬로와트|"
    r"GPU|개\s*GPU|랙|서버|가구|명|만명|만\s*명)",
    re.IGNORECASE,
)
WATCH_TIMELINE_RE = re.compile(
    r"(?:20[2-9][0-9]년|[0-9]{1,2}월\s*[0-9]{1,2}일|"
    r"[0-9]+년\s*(?:간|내)|by\s+20[2-9][0-9])",
    re.IGNORECASE,
)
WATCH_MONEY_RE = re.compile(
    r"[0-9][0-9,.]*\s*(?:조|억|천억|백억|만)\s*(?:원|달러)|"
    r"(?:USD|KRW|\$)\s*[0-9][0-9,.]*\s*(?:billion|million)?",
    re.IGNORECASE,
)
WATCH_CAPEX_CONTEXT_TERMS: tuple[str, ...] = (
    "투자", "출자", "자본지출", "capex", "건설비", "사업비", "investment",
)

# The live-delta contract also carries a bounded, source-derived categorical
# event field.  It is useful corroboration, but arbitrary labels have no
# authority: proposal/meeting/financial-product/analysis/market labels are
# deliberately absent.  Launch categories still require strategic operating
# context, while an MOU still requires factual scale or timeline evidence.
WATCH_CONFIRMED_HARD_EVENT_TYPES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("signed_contract", (
        "contract_signed", "contract_confirmed", "agreement_signed",
        "partnership_signed",
    )),
    ("confirmed_investment", (
        "investment_confirmed", "investment_committed", "funding_confirmed",
    )),
    ("award_or_order", (
        "award_confirmed", "order_confirmed", "order_secured", "contract_awarded",
    )),
    ("construction_start", (
        "construction_started", "groundbreaking_confirmed",
        "national_ai_infrastructure_construction_started",
    )),
    ("final_government_approval", (
        "approval_confirmed", "final_approval", "permit_approved",
    )),
    ("enacted_regulation", (
        "regulation_enacted", "law_enacted", "regulation_effective",
    )),
    ("committed_budget", ("budget_committed", "budget_approved")),
    ("confirmed_acquisition", (
        "acquisition_announced", "acquisition_confirmed", "acquisition_completed",
        "merger_confirmed", "merger_completed",
    )),
)
WATCH_CONFIRMED_LAUNCH_EVENT_TYPES: tuple[str, ...] = (
    "launch_announced", "product_available", "service_launched",
    "production_started", "commercial_operation_started",
)
WATCH_CONFIRMED_MOU_EVENT_TYPES: tuple[str, ...] = (
    "mou_signed", "binding_mou_signed",
)


@dataclass(frozen=True)
class WatchMaterialityDecision:
    """Watch-only executive materiality decision from publisher evidence."""

    qualified: bool
    reason: str
    hard_signal: str = ""
    proposal_discussion: bool = False
    local_political_activity: bool = False
    financial_product_framing: bool = False


def _watch_zone(evidence: Mapping[str, Any]) -> tuple[str, str, str]:
    """Return lower-cased title, factual lead, and their bounded union.

    The evidence mapping is built by the Watch caller and contains only title,
    publisher subtitle/section, and factual publisher snippet.  Search queries,
    generated summaries, relevance explanations, and scoring metadata are not
    accepted here.
    """
    title = ai_centrality.article_title(evidence).lower()
    subtitle = ai_centrality.article_subtitle(evidence).lower()
    lead = ai_centrality.article_lead_sentence(evidence)
    return title, lead, " ".join(part for part in (title, subtitle, lead) if part)


def watch_hard_material_signal(evidence: Mapping[str, Any]) -> str:
    """Return the first independently hard Watch signal, or ``""``.

    Bare proposal/discussion wording and bare monetary figures are excluded.
    An MOU requires a meaningful capacity/scale/timeline in the same factual
    evidence zone; product/service launch requires strategic operating impact.
    """
    _title, _lead, zone = _watch_zone(evidence)
    if not zone:
        return ""
    raw_events = evidence.get("shadow_confirmed_event_types", ())
    if isinstance(raw_events, str):
        raw_events = (raw_events,)
    if not isinstance(raw_events, (list, tuple, set, frozenset)):
        raw_events = ()
    confirmed_events = {
        str(event).strip().lower() for event in raw_events if str(event).strip()
    }
    for label, event_types in WATCH_CONFIRMED_HARD_EVENT_TYPES:
        if confirmed_events.intersection(event_types):
            return label
    groups: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("signed_contract", WATCH_SIGNED_CONTRACT_TERMS),
        ("confirmed_investment", WATCH_CONFIRMED_INVESTMENT_TERMS),
        ("award_or_order", WATCH_AWARD_ORDER_TERMS),
        ("construction_start", WATCH_CONSTRUCTION_START_TERMS),
        ("final_government_approval", WATCH_FINAL_APPROVAL_TERMS),
        ("enacted_regulation", WATCH_ENACTED_REGULATION_TERMS),
        ("committed_budget", WATCH_COMMITTED_BUDGET_TERMS),
    )
    for label, terms in groups:
        if any(term in zone for term in terms):
            return label
    if WATCH_CAPACITY_RE.search(zone):
        return "quantified_capacity"
    if WATCH_MONEY_RE.search(zone) and any(
        term in zone for term in WATCH_CAPEX_CONTEXT_TERMS
    ):
        return "quantified_capex"
    if (
        any(term in zone for term in WATCH_BINDING_MOU_TERMS)
        and (
            "체결" in zone
            or "signed" in zone
            or bool(confirmed_events.intersection(WATCH_CONFIRMED_MOU_EVENT_TYPES))
        )
        and (
            WATCH_CAPACITY_RE.search(zone)
            or WATCH_MONEY_RE.search(zone)
            or WATCH_TIMELINE_RE.search(zone)
        )
    ):
        return "binding_mou_with_scale_or_timeline"
    if (
        (
            any(term in zone for term in WATCH_LAUNCH_TERMS)
            or bool(confirmed_events.intersection(WATCH_CONFIRMED_LAUNCH_EVENT_TYPES))
        )
        and any(term in zone for term in WATCH_LAUNCH_IMPACT_TERMS)
    ):
        return "strategic_production_or_service_launch"
    return ""


def watch_executive_materiality(
    evidence: Mapping[str, Any],
) -> WatchMaterialityDecision:
    """Stronger realtime Watch gate; Daily/Editor policy is unchanged.

    The Watch accepts an independently hard event, a material AI security/risk
    incident, or a strategic physical-infrastructure constraint. Proposal and
    local-political classes require a hard signal. Financial/derivative product
    framing is rejected unless a separate hard industrial event is present.
    """
    title, _lead, zone = _watch_zone(evidence)
    hard_signal = watch_hard_material_signal(evidence)
    proposal = any(term in zone for term in WATCH_PROPOSAL_DISCUSSION_TERMS)
    local_political = (
        any(term in title for term in WATCH_LOCAL_POLITICAL_TERMS)
        and proposal
        and any(term in zone for term in WATCH_LOCAL_ADMIN_ACTIVITY_TERMS)
    )
    financial_product = (
        any(term in zone for term in WATCH_FINANCIAL_PRODUCT_TERMS)
        and any(term in zone for term in WATCH_FINANCIAL_FRAMING_TERMS)
    )
    industrial_context = any(
        term in zone for term in EXEC_STRATEGIC_DOMAIN_TERMS
    )
    separate_industrial_action = any(
        term in zone for term in WATCH_FINANCIAL_INDUSTRIAL_ACTION_TERMS
    )

    if financial_product and not (
        hard_signal and industrial_context and separate_industrial_action
    ):
        return WatchMaterialityDecision(
            False,
            "financial_ai_product_without_industrial_event",
            hard_signal,
            proposal,
            local_political,
            True,
        )
    if local_political and not hard_signal:
        return WatchMaterialityDecision(
            False,
            "local_political_ai_without_hard_material_signal",
            "",
            proposal,
            True,
            financial_product,
        )
    if proposal and not hard_signal:
        return WatchMaterialityDecision(
            False,
            "proposal_discussion_without_hard_material_signal",
            "",
            True,
            local_political,
            financial_product,
        )
    if hard_signal:
        return WatchMaterialityDecision(
            True,
            f"hard_material_signal:{hard_signal}",
            hard_signal,
            proposal,
            local_political,
            financial_product,
        )

    security_hit = next(
        (term for term in EXEC_AI_SECURITY_TERMS if term in zone), ""
    )
    if security_hit:
        return WatchMaterialityDecision(
            True,
            f"material_ai_security:{security_hit}",
            "",
            proposal,
            local_political,
            financial_product,
        )

    domain_hit = next(
        (term for term in EXEC_STRATEGIC_DOMAIN_TERMS if term in zone), ""
    )
    impact_terms = EXEC_IMPACT_SIGNAL_TERMS + WATCH_WORKFORCE_CONSTRAINT_TERMS
    # "무중단 전력" is a reliability feature, not the negative event "중단".
    # Remove that compound before matching the material interruption signal.
    impact_zone = zone.replace("무중단", "")
    impact_hit = next((term for term in impact_terms if term in impact_zone), "")
    if domain_hit and impact_hit:
        return WatchMaterialityDecision(
            True,
            f"strategic_infra_impact:{domain_hit}->{impact_hit}",
            "",
            proposal,
            local_political,
            financial_product,
        )

    return WatchMaterialityDecision(
        False,
        "no_independent_watch_material_signal",
        "",
        proposal,
        local_political,
        financial_product,
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

    This is the canonical R4-R17 Daily materiality gate.  As of R4-OPS-2A it
    first rejects a financial-product (ETF/fund/REIT) launch that carries no
    independent material industrial event, so an "AI + 출시" fund launch cannot
    qualify via the structural-AI-event signal below.
    """
    # R4-OPS-2A — shared fund-product noise invariant, evaluated BEFORE any
    # acceptance signal.  is_fund_product_launch_noise never calls back into
    # executive_qualification, so the dependency stays one-directional.
    if is_fund_product_launch_noise(evidence):
        return ExecutiveQualification(
            False, "fund_product_launch_without_industrial_event"
        )

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
    """R4-OPS-2 / R4-OPS-2A — is this a financial-product (ETF/fund/REIT) story
    with no independent material industrial event?

    The shared fund-product noise invariant, applied on BOTH the Daily gate and
    the real-time Watch.  Returns True (→ reject) when a fund-product vehicle is
    the subject of the title and the title/lead carries no *independent material
    industrial event*.

    A financial product's SIZE is not industrial materiality: fund AUM /
    offering scale ALONE never rescues the story (R4-OPS-2A).  Neither does a
    bare HDEC mention — the company as a mere theme constituent / index holding
    is not an industrial event (R4-OPS-2B).  A fund story is rescued ONLY by a
    real, separate event proven in the title + first factual lead:

      * a NON-LAUNCH confirmed corporate action/risk (MATERIAL_ACTION_TERMS /
        MATERIAL_RISK_TERMS — note "출시" is deliberately absent) set in an
        industrial/strategic context (EXEC_STRATEGIC_DOMAIN_TERMS), OR
      * an HDEC-direct entity PAIRED WITH such a material action/risk, OR
      * a material AI-security incident (EXEC_AI_SECURITY_TERMS).

    The fund vehicle itself is detected across the title AND the first factual
    lead sentence (never the provider query or a generated summary), so a
    vehicle named only in the lead is still caught (R4-OPS-2B).

    e.g. KEPT — "AI 데이터센터 펀드, 5,000억원 출자해 변전소 EPC 공급계약 체결"
    (출자/계약/체결 in a 데이터센터/변전 context) and "현대건설, AI 데이터센터
    펀드에 5,000억원 출자 계약 체결" (HDEC + 출자/계약).  REJECTED — "AI 데이터센터
    리츠 1조원 규모 출시" (scale only), "AI 현대건설 ETF 신규 출시" (HDEC name as
    theme constituent, no action), and title "AI 전략산업 상품 출시" whose lead is
    "…ETF 신규 출시" (lead-only vehicle) — R4-OPS-2A §4/§9, R4-OPS-2B §2/§6.

    Dependency is one-directional: this never calls executive_qualification.
    """
    title = ai_centrality.article_title(evidence)
    lead = ai_centrality.article_lead_sentence(evidence)  # factual, lowercased
    zone = f"{title.lower()} {lead}"  # title + factual lead only (no query/summary)
    # Fund-product SUBJECT detection spans the title AND the factual lead, so a
    # vehicle named only in the lead is still caught (R4-OPS-2B Gap D).
    if not any(term in zone for term in FUND_PRODUCT_TERMS):
        return False  # not a financial-product story
    text = f"{title} {lead}"
    material_action = any(term in text for term in MATERIAL_ACTION_TERMS)
    material_risk = any(term in text for term in MATERIAL_RISK_TERMS)
    industrial_context = any(term in zone for term in EXEC_STRATEGIC_DOMAIN_TERMS)
    hdec_hit = any(term in title or term in lead for term in HDEC_DIRECT_TERMS)
    security_hit = any(term in zone for term in EXEC_AI_SECURITY_TERMS)
    # A financial-product launch is rescued ONLY by a real, separate event. A
    # bare scale figure (fund AUM) and a bare HDEC mention (theme constituent /
    # holding) never rescue: the HDEC route additionally requires a material
    # action/risk (R4-OPS-2B Gap C).
    industrial_event = (material_action or material_risk) and industrial_context
    hdec_event = hdec_hit and (material_action or material_risk)
    security_event = security_hit
    return not (industrial_event or hdec_event or security_event)
