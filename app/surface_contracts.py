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
    - career/event items stay out of the executive AI tab even when they mention
      smart construction
    """
    title = article.get("title") or ""
    snippet = article.get("snippet") or ""
    combined = f"{title} {snippet}"

    strong_title = _strong_title_matches(title)
    title_exclusions = _matches(title, TITLE_EXCLUSION_PATTERNS)
    career_event = _matches(title, CAREER_EVENT_PATTERNS)

    if career_event:
        return _decision(
            eligible=False,
            reason_code="ai_tab.reject.title_career_event",
            public_reason="임원 AI 탭 대신 운영자 참고 기사로 분리",
            operator_note="career/event-centered title is operator/reference, not executive AI tab",
            severity="review",
            matched_terms=career_event,
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
