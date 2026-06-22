"""Deterministic editorial surface contracts.

This module owns no-LLM display eligibility decisions for executive/report
surfaces. D4-A only migrates the AI tab; the other surface functions are
explicit placeholders so future migrations have a central contract boundary.
"""

from dataclasses import asdict, dataclass, field


@dataclass
class SurfaceDecision:
    surface: str
    eligible: bool
    reason_code: str
    public_reason: str
    operator_note: str
    severity: str = "info"
    matched_terms: list[str] = field(default_factory=list)
    # D4-E: AI-tab supplement priority (1=best). 0 means "not applicable".
    tier: int = 0


STRONG_AI_TITLE_TOPICS = (
    "AI 데이터센터",
    "데이터센터",
    "데이터 센터",
    "IDC",
    "AI 인프라",
    "스마트건설",
    "스마트 건설",
    "건설 AI",
    "AI 로봇",
    "건설로봇",
    "건설 로봇",
    "BIM",
    "디지털트윈",
    "영상인식",
    "자율시공",
    "SMR",
    "소형모듈원자로",
)

EXECUTION_ANCHORS = (
    "현대건설",
    "건설",
    "건설사",
    "EPC",
    "시공",
    "수주",
    "발주",
    "프로젝트",
    "플랜트",
    "인프라",
    "전력 인프라",
    "전력망",
    "부지",
    "냉각",
    "계통",
    "송전",
    "변전",
    "현장",
    "안전관리",
    "R&D",
    "연구원",
)

TITLE_EXCLUSION_PATTERNS = (
    "재생에너지 금융",
    "해상풍력 PF",
    "PF 주선",
    "완도 해상풍력",
    "도시정비",
    "재건축",
    "성수벨트",
    "목동",
    "여의도",
    "주가",
    "ETF",
    "수혜주",
    "사세요",
    "삼전",
    "하닉",
    "건설주",
    "株",
    "대학생 현장견학",
    "진로 모색",
    "채용",
    "취업 기회",
)

CAREER_EVENT_PATTERNS = (
    "대학생 현장견학",
    "진로 모색",
    "채용",
    "취업 기회",
)

# Mixed HDEC business / urban-redevelopment / order-portfolio title terms.
# When a title is built around these AND carries no AI/DC execution-primary
# phrase, a side mention of "데이터센터" must not pull the item into the
# executive AI tab — e.g. "현대건설, 도시정비 12조·데이터센터 양 축 강화" is an
# order/portfolio story where the data center is only one of several axes.
# NOTE: keep these specific. Bare "12조" is intentionally excluded — it is a
# substring of legitimate DC-investment headlines (e.g. "데이터센터에 12조원
# 투자"), so the redevelopment-bound "도시정비 12조" is used instead.
MIXED_BUSINESS_TITLE_PATTERNS = (
    "도시정비",
    "도시정비 12조",
    "정비사업",
    "정비사업 대어급",
    "재건축",
    "재개발",
    "수주",
    "매출",
    "실적",
    "포트폴리오",
    "사업 포트폴리오",
    "분양",
    "양 축",
)

# Strong AI/DC/smart-construction/SMR *execution-primary* title phrases. A title
# carrying one of these is AI/DC-primary even if it also names a business axis,
# so it stays eligible. This is a STRICTER subset than STRONG_AI_TITLE_TOPICS
# (which accepts a bare "데이터센터"): the bare topic alone cannot rescue a
# mixed-business title. Data center is bound to an execution noun
# (EPC/신축/공사/시공/건설/발주) so genuine data-center build orders such as
# "데이터센터 신축 공사 수주" are never mistaken for portfolio/order narrative.
STRONG_AI_PRIMARY_TITLE_PATTERNS = (
    "AI 데이터센터",
    "데이터센터 EPC",
    "데이터 센터 EPC",
    "데이터센터 신축",
    "데이터센터 공사",
    "데이터센터 시공",
    "데이터센터 건설",
    "데이터센터 발주",
    "전력·냉각",
    "전력망·냉각",
    "냉각 기술",
    "AI 인프라",
    "하이테크 EPC",
    "스마트건설",
    "스마트 건설",
    "건설 AI",
    "AI 로봇",
    "AI 필드로봇",
    "건설로봇",
    "BIM",
    "디지털트윈",
    "SMR",
    "소형모듈원자로",
)

# --- D4-E AI-tab supplement contract --------------------------------------
# When the executive AI tab is under-filled (<3 items), high-quality AI/DC/
# smart-construction stories the radar routed into OTHER executive sections
# (HDEC-direct, order/overseas, immediate) may *supplement* it. This gate is
# title-first and intentionally BROADER than decide_ai_tab — the candidates are
# already executive-surfaced, so we only re-confirm the title is genuinely an
# AI/DC/smart-construction story (not a side mention) and free of mixed-business
# or stock noise. Generic 원전/SMR is deliberately NOT a supplement signal
# (unlike decide_ai_tab): a nuclear title belongs in the AI tab only when it
# also carries a data-center/AI/power/cooling/robot/EPC hook, which the primary
# patterns below already require, so a pure "원전 부지 선정" never supplements.
AI_SUPPLEMENT_PRIMARY_PATTERNS = (
    "AI 데이터센터", "데이터센터", "데이터 센터", "IDC", "AI 인프라",
    "AI 필드로봇", "필드로봇", "건설로봇", "건설 로봇", "AI 로봇",
    "스마트건설", "스마트 건설", "건설 AI", "건설사 AI", "건설현장 AI",
    "건설 현장 AI", "BIM", "디지털트윈", "자율시공",
)
# Mixed business / urban-redevelopment / stock noise that must never enter the
# AI tab even as a supplement (D4-D contract terms + stock-hype tickers).
AI_SUPPLEMENT_REJECT_PATTERNS = (
    "도시정비 12조", "데이터센터 양 축 강화", "사업 포트폴리오",
    "정비사업 대어급", "도시정비", "재건축", "재개발", "분양",
    "ETF", "삼전", "하닉", "진로", "현장견학",
)
# Data-center primary tokens (tier classification).
AI_SUPPLEMENT_DC_TERMS = ("AI 데이터센터", "데이터센터", "데이터 센터", "IDC")
# Order/contract/policy hooks that mark a data-center story execution-grade
# (tier-2) rather than a generic mention.
AI_SUPPLEMENT_DC_ORDER_HOOKS = (
    "수주", "계약", "본계약", "EPC", "특별법", "전력", "냉각", "발주", "투자",
)
# Robot / field-automation / smart-construction execution hooks (tier-3).
AI_SUPPLEMENT_ROBOT_HOOKS = (
    "필드로봇", "건설로봇", "건설 로봇", "로봇", "자동화",
    "스마트건설", "스마트 건설",
)
# HDEC / Hyundai construction-group names (tier-1 with an AI/DC primary signal).
AI_SUPPLEMENT_HDEC_NAMES = ("현대건설", "현대엔지니어링", "현대ENG", "현대 ENG")


def _norm(text: str | None) -> str:
    return (text or "").casefold()


def _matches(text: str | None, terms: tuple[str, ...]) -> list[str]:
    low = _norm(text)
    return [term for term in terms if _norm(term) in low]


def _strong_title_matches(title: str | None) -> list[str]:
    matches = _matches(title, STRONG_AI_TITLE_TOPICS)
    low = _norm(title)
    # Korean title variant of "건설 AI": "건설사 AI ..." is a direct
    # construction-AI title, not a snippet rescue.
    if "건설사 ai" in low and "건설 AI" not in matches:
        matches.append("건설 AI")
    if "건설현장 ai" in low and "건설 AI" not in matches:
        matches.append("건설 AI")
    if "건설 현장 ai" in low and "건설 AI" not in matches:
        matches.append("건설 AI")
    return matches


def _decision(
    *,
    eligible: bool,
    reason_code: str,
    public_reason: str,
    operator_note: str,
    severity: str = "info",
    matched_terms: list[str] | None = None,
) -> SurfaceDecision:
    return SurfaceDecision(
        surface="ai_tab",
        eligible=eligible,
        reason_code=reason_code,
        public_reason=public_reason,
        operator_note=operator_note,
        severity=severity,
        matched_terms=matched_terms or [],
    )


def decide_ai_tab(article: dict) -> SurfaceDecision:
    """Return the deterministic AI-tab eligibility decision for one article.

    Contract:
    - title-first: a strong AI/DC/smart-construction/SMR topic must be in title
    - snippet may provide execution context, but cannot rescue a non-AI title
    - title-centered finance/PF/stock/redevelopment noise is rejected unless the
      title itself has a strong topic
    - title-primary: a mixed business/urban-redevelopment/order title (도시정비·
      수주·포트폴리오·양 축 …) is rejected when it lacks an AI/DC execution-primary
      phrase, so a side mention of "데이터센터" cannot make it AI-eligible
    - career/event items stay out of the executive AI tab even when they mention
      smart construction
    """
    title = article.get("title") or ""
    snippet = article.get("snippet") or ""
    combined = f"{title} {snippet}"

    strong_title = _strong_title_matches(title)
    title_exclusions = _matches(title, TITLE_EXCLUSION_PATTERNS)
    career_event = _matches(title, CAREER_EVENT_PATTERNS)
    mixed_business = _matches(title, MIXED_BUSINESS_TITLE_PATTERNS)
    ai_primary = _matches(title, STRONG_AI_PRIMARY_TITLE_PATTERNS)

    if career_event:
        return _decision(
            eligible=False,
            reason_code="ai_tab.reject.title_career_event",
            public_reason="임원 AI 탭 대신 운영자 참고 기사로 분리",
            operator_note="career/event-centered title is operator/reference, not executive AI tab",
            severity="review",
            matched_terms=career_event,
        )

    # Title-primary guard: an order/portfolio/urban-redevelopment title whose
    # only AI hook is a bare "데이터센터" mention is operator/reference, not the
    # executive AI tab. An AI/DC execution-primary phrase (incl. DC bound to a
    # build noun) keeps genuine data-center execution stories eligible.
    if mixed_business and not ai_primary:
        return _decision(
            eligible=False,
            reason_code="ai_tab.reject.mixed_business_title_not_ai_primary",
            public_reason="제목이 도시정비·수주·포트폴리오 등 사업 축 중심이며 AI·DC 실행이 핵심이 아님",
            operator_note="mixed business/urban-redevelopment/order title; data center is only a side axis, not AI/DC execution-primary",
            severity="review",
            matched_terms=mixed_business,
        )

    if title_exclusions and not strong_title:
        return _decision(
            eligible=False,
            reason_code="ai_tab.reject.title_exclusion_without_strong_topic",
            public_reason="제목 중심 주제가 AI 인프라·건설 AI가 아님",
            operator_note="title exclusion matched and snippet cannot rescue a clearly non-AI title",
            severity="review",
            matched_terms=title_exclusions,
        )

    if not strong_title:
        return _decision(
            eligible=False,
            reason_code="ai_tab.reject.no_strong_title_topic",
            public_reason="AI 탭 제목 기준 미충족",
            operator_note="no strong AI/DC/smart-construction/SMR topic in title",
            severity="review",
        )

    execution = _matches(combined, EXECUTION_ANCHORS)
    if not execution:
        return _decision(
            eligible=False,
            reason_code="ai_tab.reject.no_execution_anchor",
            public_reason="건설 실행 맥락 부족",
            operator_note="strong title topic present, but no construction/project/execution anchor",
            severity="review",
            matched_terms=strong_title,
        )

    matched = []
    for term in strong_title + execution:
        if term not in matched:
            matched.append(term)
    return _decision(
        eligible=True,
        reason_code="ai_tab.accept.strong_title_with_execution_anchor",
        public_reason="AI 인프라·건설 AI 실행 맥락",
        operator_note="strong title topic plus construction/project/execution anchor",
        matched_terms=matched,
    )


def decide_ai_supplement(article: dict) -> SurfaceDecision:
    """Decide whether an already-surfaced non-AI item may supplement the AI tab.

    Title-first and broader than decide_ai_tab (the candidate is already
    executive-surfaced). Eligible iff the TITLE carries a genuine AI/DC/smart-
    construction signal AND no mixed-business/stock-noise term. Sets `tier` for
    ordering: 1=HDEC + AI/DC, 2=data center + order/policy hook, 3=robot/smart-
    construction, 4=other AI/DC. Generic 원전/SMR alone never qualifies — it is
    not a primary supplement term (see AI_SUPPLEMENT_PRIMARY_PATTERNS).
    """
    title = article.get("title") or ""
    primary = _matches(title, AI_SUPPLEMENT_PRIMARY_PATTERNS)
    reject = _matches(title, AI_SUPPLEMENT_REJECT_PATTERNS)

    if reject or not primary:
        return SurfaceDecision(
            surface="ai_tab_supplement",
            eligible=False,
            reason_code=("ai_tab.supplement.reject.mixed_or_stock_noise"
                         if reject else
                         "ai_tab.supplement.reject.not_ai_primary"),
            public_reason="AI 탭 보충 기준 미충족",
            operator_note=("mixed business / stock noise in title"
                           if reject else
                           "no AI/DC/smart-construction primary signal in title"),
            severity="review",
            matched_terms=reject or [],
        )

    has_dc = bool(_matches(title, AI_SUPPLEMENT_DC_TERMS))
    if _matches(title, AI_SUPPLEMENT_HDEC_NAMES):
        tier = 1
    elif has_dc and _matches(title, AI_SUPPLEMENT_DC_ORDER_HOOKS):
        tier = 2
    elif _matches(title, AI_SUPPLEMENT_ROBOT_HOOKS):
        tier = 3
    else:
        tier = 4
    return SurfaceDecision(
        surface="ai_tab_supplement",
        eligible=True,
        reason_code=f"ai_tab.supplement.accept.tier{tier}",
        public_reason="AI 인프라·데이터센터·스마트건설 보충 신호",
        operator_note="AI/DC/smart-construction title surfaced in another section",
        severity="info",
        matched_terms=primary,
        tier=tier,
    )


def _not_migrated(surface: str) -> SurfaceDecision:
    return SurfaceDecision(
        surface=surface,
        eligible=True,
        reason_code=f"{surface}.not_migrated",
        public_reason="기존 표시 로직 유지",
        operator_note="surface contract placeholder; behavior not migrated in D4-A",
    )


def decide_executive_top(article: dict) -> SurfaceDecision:
    return _not_migrated("executive_top")


def decide_risk_event_candidate(article_or_event: dict) -> SurfaceDecision:
    return _not_migrated("risk_event_candidate")


def decide_category_evidence(article: dict, category_key: str) -> SurfaceDecision:
    return _not_migrated(f"category_evidence.{category_key or 'unknown'}")


def summarize_surface_decisions(article: dict) -> dict:
    return {
        "ai_tab": asdict(decide_ai_tab(article)),
        "executive_top": asdict(decide_executive_top(article)),
        "risk_event_candidate": asdict(decide_risk_event_candidate(article)),
    }
