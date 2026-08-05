"""Canonical AI-centrality decision for every AI-branded delivery surface.

D7-AK-6E R4-R6/R4-R7 content repair — one shared, deterministic answer to "is
AI the material subject of this article?", reused by the Teams individual
push, the Daily AI Brief, the dashboard AI subcategory, and the Weekly T&I
wherever an AI category is asserted.

Allowed evidence (and nothing else):

* the article title;
* a publisher-supplied subtitle;
* the first factual lead sentence of the article's own summary/snippet;
* explicit publisher section metadata;
* confirmed event fields derived directly from the article.

Generated metadata is forbidden evidence by contract: why_it_matters,
whyImportant, radarReason, generated category labels, executive implications,
provider query strings, lens assignments from other classifiers, and
incidental mentions later in a long snippet must never establish
AI-centrality.

Market articles use two separate decisions (R4-R7 §2), learned from the human
final Briefs: ``surface_market_article`` describes the visible form (price,
earnings, guidance, valuation), while ``structural_ai_causal_event`` detects
an independently material AI industry change (budget reallocation, talent
loss, infrastructure investment, regulation, organizational change). A
surface-market article stays eligible only when a structural AI causal event
is proven from the title and publisher lead — the human-precedent pattern
(IBM AI budget displacement, Google AI talent exodus) — and is excluded when
AI demand is merely market commentary (Onsemi after-hours rebound pattern).

This module is a pure leaf: stdlib only, no I/O, no network, no state.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

LEVEL_EXPLICIT_AI_CORE = "explicit_ai_core"
LEVEL_ENABLING_INFRASTRUCTURE_CORE = "enabling_infrastructure_core"
LEVEL_INCIDENTAL_AI_MENTION = "incidental_ai_mention"
LEVEL_NON_AI = "non_ai"

#: Levels that qualify for Teams alerts and the Daily AI Brief.
CENTRAL_LEVELS = frozenset(
    {LEVEL_EXPLICIT_AI_CORE, LEVEL_ENABLING_INFRASTRUCTURE_CORE}
)

EXCLUSION_STOCK_MARKET = "stock_market_title"
EXCLUSION_POLITICAL = "political_title"
EXCLUSION_REAL_ESTATE = "real_estate_transaction_title"
EXCLUSION_CIVIC_PUBLICITY = "civic_campaign_or_publicity_title"

_LEAD_SENTENCE_LIMIT = 140

# ---------------------------------------------------------------------------
# Surface market form — title-level signals (R4-R6 §3).
# These describe the visible article structure only; exclusion is decided
# together with the structural-AI-causal-event evidence below.
# ---------------------------------------------------------------------------
STOCK_MARKET_TITLE_TERMS: tuple[str, ...] = (
    "특징주", "급등", "급락", "시간 외", "시간외", "장중", "주가", "증시", "증권",
    "목표주가", "투자의견", "매수", "매도", "상한가", "하한가", "수혜주", "관련주",
    "테마주", "종목", "주식", "밸류에이션", "시가총액", "상장", "ipo", "실적발표",
    "실적 발표", "가이던스", "컨센서스", "영업익", "영업이익", "순이익", "어닝쇼크",
    "어닝 쇼크", "어닝서프라이즈", "어닝 서프라이즈", "흑자전환", "적자전환", "배당",
    "목표가", "마켓인사이드",
)

# Corporate-name noise that must not trigger the bare "주식"/"상장" tokens.
_STOCK_TERM_NEUTRAL_CONTEXTS: tuple[str, ...] = (
    "주식회사",
    "상장식",
)

_POLITICAL_TITLE_TERMS: tuple[str, ...] = (
    "출마", "선거", "공천", "총선", "대선 후보", "당대표", "당 대표", "개각",
    "여야", "국회의원", "의원직", "정당 지지율", "전당대회", "탈당", "입당",
    "당직", "위원장 선거",
)

_REAL_ESTATE_TITLE_TERMS: tuple[str, ...] = (
    "매물로", "매물", "재매각", "매각", "청약", "전세", "월세", "임대료",
    "집값", "아파트값", "재건축 조합", "분양권", "부동산 경기",
)

_CIVIC_PUBLICITY_TITLE_TERMS: tuple[str, ...] = (
    "캠페인", "공모전", "봉사활동", "봉사단", "걷기대회", "사생대회", "그림대회",
    "시민사회", "주민참여", "공청회", "궐기대회", "결의대회", "바자회",
    "시상식", "표창", "감사패", "수료식", "장학금 전달", "체험행사", "체험 행사",
)

_DOMAIN_EXCLUSIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (EXCLUSION_POLITICAL, _POLITICAL_TITLE_TERMS),
    (EXCLUSION_REAL_ESTATE, _REAL_ESTATE_TITLE_TERMS),
    (EXCLUSION_CIVIC_PUBLICITY, _CIVIC_PUBLICITY_TITLE_TERMS),
)

# Confirmed material corporate/institutional actions (title-zone evidence).
_CONFIRMED_ACTION_TITLE_TERMS: tuple[str, ...] = (
    "계약 체결", "계약을 체결", "공급계약", "공급 계약", "체결", "착공", "준공",
    "착수", "인수 완료", "인수한다", "인수 확정", "인수 추진", "투자 확정",
    "투자한다", "규제 시행", "시행", "발효", "승인", "수주", "낙찰", "매각 완료",
    "전환", "조성", "구축", "첫 삽",
)

# ---------------------------------------------------------------------------
# AI subject evidence.
# ---------------------------------------------------------------------------
_AI_CORE_TERMS: tuple[str, ...] = (
    "ai", "인공지능", "artificial intelligence", "생성형 ai", "generative ai",
    "llm", "대규모 언어모델", "파운데이션 모델", "머신러닝", "machine learning",
    "딥러닝", "deep learning", "ai 에이전트", "ai agent", "agentic ai",
    "에이전틱 ai", "openai", "오픈ai", "챗gpt", "chatgpt", "gpt", "클로드",
    "claude", "제미나이", "gemini", "코파일럿", "copilot", "휴머노이드",
    "소버린 ai",
    # Named AI-accelerator technologies count as AI subjects (§3 treats a
    # "GPU 공급계약 체결" title as a confirmed material AI corporate action).
    "gpu", "hbm", "npu", "gpu 클러스터",
)

# Enabling-infrastructure subjects: the title/lead subject may be the physical
# or industrial layer, but only together with an explicitly stated AI
# relationship (title or lead).
_ENABLING_INFRA_TERMS: tuple[str, ...] = (
    "데이터센터", "데이터 센터", "datacenter", "data center", "idc",
    "gpu", "hbm", "npu", "반도체", "파운드리", "슈퍼컴퓨터", "컴퓨팅센터",
    "전력망", "전력 인프라", "전력인프라", "전력 수요", "전력수요", "송전",
    "변전", "송배전", "발전소", "원전", "원자력", "smr", "소형모듈원자로",
    "냉각", "용수", "전기자재",
    "로봇", "로보틱스", "자율주행", "자율 시공", "스마트건설", "스마트 건설",
    "bim", "디지털 트윈", "digital twin", "스마트팩토리", "스마트 팩토리",
    "산업 자동화", "공장 자동화",
)

_OPINION_TITLE_MARKERS: tuple[str, ...] = (
    "[사설]", "[칼럼]", "[기고]", "[시론]", "[오피니언]", "사설]", "〈사설〉",
)

# Publisher section metadata values that assert a finance/markets desk.
_STOCK_SECTION_MARKERS: tuple[str, ...] = (
    "증권", "마켓", "주식", "stock", "markets",
)

# Publisher section metadata values that assert an AI/tech desk.
_AI_SECTION_MARKERS: tuple[str, ...] = (
    "ai", "인공지능", "테크", "tech", "it",
)

# ---------------------------------------------------------------------------
# Structural AI causal events (R4-R7 §2) — an independently material AI
# industry change proven from title + publisher lead. Each class encodes a
# human-precedent selection pattern from the final Weekly Briefs; a demand
# narrative ("AI 수요 확대 속에…") is deliberately NOT a causal event.
# ---------------------------------------------------------------------------
_STRUCTURAL_EVENT_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "ai_budget_reallocation",
        (
            "예산 잠식", "예산을 잠식", "예산 전환", "예산 재배분", "지출 재편",
            "지출이 대체", "잠식", "밀어내", "대체하", "budget displacement",
        ),
    ),
    (
        "ai_talent_change",
        (
            "인재 이탈", "인재 유출", "인력 유출", "연쇄 이탈", "엑소더스",
            "인재 영입", "인재 확보 경쟁", "이직 러시", "talent exodus",
        ),
    ),
    (
        "ai_infrastructure_investment",
        # Definite event forms only — bare "건설"/"수주"/"체결" would turn a
        # demand narrative ("AI 데이터센터 건설용 철강 수요…") into an event.
        (
            "투자 확정", "투자한다", "구축 착수", "구축에 착수", "건설 계약",
            "건설에 착수", "착공", "준공", "증설", "조성", "첫 삽", "인수 추진",
            "인수 완료", "수주했", "수주 계약", "공급계약", "공급 계약",
            "계약 체결", "계약을 체결",
        ),
    ),
    (
        "ai_product_platform_strategy",
        (
            "출시", "상용화", "플랫폼 전환", "신모델", "모델 공개",
        ),
    ),
    (
        "ai_regulation",
        (
            "규제 시행", "법 시행", "법안", "의무화", "행정명령", "시행령",
            "본격 시행", "고지 의무",
        ),
    ),
    (
        "ai_org_labor_change",
        (
            "감원", "구조조정", "조직 개편", "일자리 대체", "인력 재배치",
            "채용 축소", "업무 방식",
        ),
    ),
    (
        "ai_supply_chain_change",
        (
            "공급망 재편", "공급망 전환", "조달 전환", "수급 재편", "수출통제",
            "수출 통제",
        ),
    ),
    (
        "ai_business_structure_adoption",
        (
            "전면 도입", "도입 확정", "전사 도입", "사업 재편", "부지를", "부지 매입",
            "전환 착수",
        ),
    ),
)

# ---------------------------------------------------------------------------
# Delivery category taxonomy (R4-R6 §7) — mutually meaningful categories whose
# evidence must come from the same title/lead evidence map.
# ---------------------------------------------------------------------------
CATEGORY_AI_DATACENTER = "AI 데이터센터"
CATEGORY_AI_POWER_ENERGY = "AI 전력·에너지"
CATEGORY_AI_SEMICONDUCTOR = "AI 반도체·컴퓨팅"
CATEGORY_PHYSICAL_AI = "산업·피지컬 AI"
CATEGORY_SMART_CONSTRUCTION = "스마트건설"
CATEGORY_AI_POLICY = "AI 정책·규제"
CATEGORY_AI_CORPORATE = "AI 기업전략"
CATEGORY_AI_RISK_SECURITY = "AI 위험·보안"

DELIVERY_CATEGORIES: tuple[str, ...] = (
    CATEGORY_AI_DATACENTER,
    CATEGORY_AI_POWER_ENERGY,
    CATEGORY_AI_SEMICONDUCTOR,
    CATEGORY_PHYSICAL_AI,
    CATEGORY_SMART_CONSTRUCTION,
    CATEGORY_AI_POLICY,
    CATEGORY_AI_CORPORATE,
    CATEGORY_AI_RISK_SECURITY,
)

# Priority-ordered: the first group whose evidence appears in the title wins;
# lead evidence is consulted only when no group matches the title.
_CATEGORY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        CATEGORY_AI_DATACENTER,
        (
            "데이터센터", "데이터 센터", "datacenter", "data center", "idc",
            "gpu 클러스터", "ai 인프라", "하이퍼스케일", "컴퓨팅센터",
        ),
    ),
    (
        CATEGORY_AI_POWER_ENERGY,
        (
            "전력망", "전력 인프라", "전력인프라", "전력 수요", "전력수요",
            "송전", "변전", "송배전", "발전소", "원전", "원자력", "smr",
            "소형모듈원자로", "에너지", "냉각", "용수", "그리드", "전기자재",
        ),
    ),
    (
        CATEGORY_AI_SEMICONDUCTOR,
        (
            "반도체", "gpu", "hbm", "npu", "칩", "파운드리", "웨이퍼",
            "슈퍼컴퓨터", "컴퓨팅", "매개변수", "신모델", "파운데이션 모델",
        ),
    ),
    (
        CATEGORY_SMART_CONSTRUCTION,
        (
            "스마트건설", "스마트 건설", "bim", "디지털 트윈", "digital twin",
            "건설 로봇", "건설로봇", "자율 시공", "건설 자동화", "시공 자동화",
        ),
    ),
    (
        CATEGORY_PHYSICAL_AI,
        (
            "피지컬 ai", "physical ai", "로봇", "로보틱스", "휴머노이드",
            "자율주행", "스마트팩토리", "스마트 팩토리", "제조 ai", "산업 ai",
            "공장 자동화", "산업 자동화", "물류 자동화",
        ),
    ),
    (
        CATEGORY_AI_POLICY,
        (
            "규제", "정책", "법안", "법률", "법 시행", "시행령", "행정명령",
            "가이드라인", "지침", "국가 전략", "국가전략", "수출통제",
            "수출 통제", "의무화",
        ),
    ),
    (
        CATEGORY_AI_RISK_SECURITY,
        (
            "보안", "해킹", "딥페이크", "오남용", "저작권", "윤리", "안전성",
            "취약점", "유출", "사기", "판례", "과몰입", "커닝",
        ),
    ),
    (
        CATEGORY_AI_CORPORATE,
        (
            "투자", "인수", "합병", "계약", "제휴", "파트너십", "협력", "출시",
            "공개", "상용화", "전략", "사업 확장", "수주", "실적", "시가총액",
            "인재",
        ),
    ),
)


@dataclass(frozen=True)
class AICentralityDecision:
    """One canonical AI-centrality outcome for a single article."""

    level: str
    exclusion: str = ""
    exclusion_terms: tuple[str, ...] = ()
    surface_market: bool = False
    structural_event: str = ""
    structural_event_terms: tuple[str, ...] = ()
    title_ai_terms: tuple[str, ...] = ()
    lead_ai_terms: tuple[str, ...] = ()
    title_infra_terms: tuple[str, ...] = ()
    lead_infra_terms: tuple[str, ...] = ()
    opinion_labeled: bool = False
    evidence_zones: tuple[str, ...] = ()
    reason: str = ""

    @property
    def is_central(self) -> bool:
        return not self.exclusion and self.level in CENTRAL_LEVELS


def _value(obj: object, key: str, default: Any = "") -> Any:
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _mapping(obj: object, key: str) -> Mapping[str, Any]:
    value = _value(obj, key, {})
    return value if isinstance(value, Mapping) else {}


def _clean(value: object) -> str:
    return " ".join(str(value or "").split())


def _lower(value: object) -> str:
    return _clean(value).lower()


def _contains_term(text: str, term: str) -> bool:
    needle = term.lower()
    if re.fullmatch(r"[a-z0-9.&-]+", needle):
        pattern = rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])"
        return re.search(pattern, text) is not None
    return needle in text


def _has(text: str, terms: Sequence[str]) -> tuple[str, ...]:
    return tuple(term for term in terms if _contains_term(text, term))


def _first_sentence(value: object) -> str:
    text = _lower(value)
    if not text:
        return ""
    first = re.split(r"[.!?。！？]\s*|\n+", text, maxsplit=1)[0]
    return first[:_LEAD_SENTENCE_LIMIT]


def article_title(article: object) -> str:
    after = _mapping(article, "after")
    return _clean(_value(article, "title") or after.get("title"))


def article_subtitle(article: object) -> str:
    """Publisher-supplied subtitle only; never a generated summary."""
    after = _mapping(article, "after")
    for owner in (article, after):
        for key in ("subtitle", "publisher_subtitle"):
            text = _clean(_value(owner, key))
            if text:
                return text
    return ""


def article_lead_sentence(article: object) -> str:
    """First factual lead sentence from the article's own summary/snippet."""
    after = _mapping(article, "after")
    sentences = []
    for owner in (article, after):
        for key in ("summary", "snippet"):
            value = _value(owner, key)
            if _clean(value):
                sentences.append(_first_sentence(value))
    return " ".join(dict.fromkeys(sentence for sentence in sentences if sentence))


def publisher_section(article: object) -> str:
    after = _mapping(article, "after")
    metadata = _mapping(article, "metadata") or _mapping(after, "metadata")
    for owner in (article, after, metadata):
        for key in ("publisher_section", "section"):
            text = _clean(_value(owner, key))
            if text:
                return text
    return ""


def confirmed_event_types(article: object) -> tuple[str, ...]:
    raw = _value(article, "shadow_confirmed_event_types", ())
    if isinstance(raw, str):
        raw = (raw,)
    if not isinstance(raw, Iterable):
        return ()
    return tuple(_clean(item).lower() for item in raw if _clean(item))


def _neutralized_title(title_lower: str) -> str:
    """Remove corporate-name noise before stock-token matching."""
    text = title_lower
    for context in _STOCK_TERM_NEUTRAL_CONTEXTS:
        text = text.replace(context, " ")
    return text


def _title_lead_zone(article: object) -> str:
    """The only zone where AI-centrality/causal evidence may be established."""
    return " ".join(
        part
        for part in (
            _lower(article_title(article)),
            _lower(article_subtitle(article)),
            article_lead_sentence(article),
        )
        if part
    )


def surface_market_article(article: object) -> tuple[bool, tuple[str, ...]]:
    """R4-R7 §2 decision 1 — visible market/earnings/valuation article form."""
    title = f" {_lower(article_title(article))} "
    hits = _has(_neutralized_title(title), STOCK_MARKET_TITLE_TERMS)
    if not hits:
        section = _lower(publisher_section(article))
        if section:
            hits = tuple(
                f"section:{marker}"
                for marker in _has(f" {section} ", _STOCK_SECTION_MARKERS)
            )
    return bool(hits), hits


def structural_ai_causal_event(article: object) -> tuple[str, tuple[str, ...]]:
    """R4-R7 §2 decision 2 — independently material AI causal event.

    Proven only from title + publisher-supplied subtitle + first lead
    sentence, and only together with an AI term in the same zone. A demand
    narrative ('AI 수요 확대 속에…') never qualifies. Generated
    why-it-matters text can never create this evidence."""
    zone = f" {_title_lead_zone(article)} "
    if not zone.strip():
        return "", ()
    ai_hits = _has(zone, _AI_CORE_TERMS)
    if not ai_hits:
        return "", ()
    for event_class, terms in _STRUCTURAL_EVENT_RULES:
        hits = _has(zone, terms)
        if hits:
            return event_class, tuple(dict.fromkeys(hits + ai_hits[:2]))
    return "", ()


def domain_exclusion(article: object) -> tuple[str, tuple[str, ...]]:
    """Political / real-estate / civic-publicity title classes (R4-R6 §4).

    The only exception is an explicit AI/data-center central event: an AI or
    data-center subject term in the title together with a confirmed material
    action ('AI' occurring elsewhere in a provider snippet is not an
    exception; a property sale stays excluded unless data-center conversion
    is the explicit central event)."""
    title = f" {_lower(article_title(article))} "
    for exclusion, terms in _DOMAIN_EXCLUSIONS:
        hits = _has(title, terms)
        if not hits:
            continue
        subject = _has(title, _AI_CORE_TERMS) or _has(
            title, ("데이터센터", "데이터 센터", "datacenter", "data center")
        )
        action = _has(title, _CONFIRMED_ACTION_TITLE_TERMS)
        if subject and action:
            continue
        return exclusion, hits
    return "", ()


def opinion_labeled(article: object) -> bool:
    title = article_title(article).lower()
    return any(marker.lower() in title for marker in _OPINION_TITLE_MARKERS)


def classify(article: object) -> AICentralityDecision:
    """Canonical AI-centrality decision from allowed evidence zones only."""
    title_raw = article_title(article)
    title = f" {_lower(title_raw)} "
    subtitle = f" {_lower(article_subtitle(article))} "
    lead = f" {article_lead_sentence(article)} "
    section = _lower(publisher_section(article))
    events = confirmed_event_types(article)

    if not title.strip():
        return AICentralityDecision(LEVEL_NON_AI, reason="empty_title")

    surface, surface_terms = surface_market_article(article)
    structural_class, structural_terms = structural_ai_causal_event(article)

    title_ai = _has(title, _AI_CORE_TERMS)
    subtitle_ai = _has(subtitle, _AI_CORE_TERMS) if subtitle.strip() else ()
    lead_ai = tuple(dict.fromkeys(subtitle_ai + _has(lead, _AI_CORE_TERMS)))
    title_infra = _has(title, _ENABLING_INFRA_TERMS)
    lead_infra = _has(lead, _ENABLING_INFRA_TERMS)
    section_ai = bool(section) and bool(_has(f" {section} ", _AI_SECTION_MARKERS))
    ai_events = tuple(
        event for event in events if "ai" in event or "인공지능" in event
    )
    opinion = opinion_labeled(article)

    if surface and not structural_class:
        return AICentralityDecision(
            LEVEL_INCIDENTAL_AI_MENTION
            if (title_ai or lead_ai)
            else LEVEL_NON_AI,
            exclusion=EXCLUSION_STOCK_MARKET,
            exclusion_terms=surface_terms,
            surface_market=True,
            title_ai_terms=title_ai,
            lead_ai_terms=lead_ai,
            opinion_labeled=opinion,
            reason="surface_market_article_without_structural_ai_causal_event",
        )

    exclusion, domain_hits = domain_exclusion(article)
    if exclusion:
        return AICentralityDecision(
            LEVEL_INCIDENTAL_AI_MENTION
            if (title_ai or lead_ai)
            else LEVEL_NON_AI,
            exclusion=exclusion,
            exclusion_terms=domain_hits,
            surface_market=surface,
            title_ai_terms=title_ai,
            lead_ai_terms=lead_ai,
            opinion_labeled=opinion,
            reason=f"{exclusion}_without_ai_material_action",
        )

    zones: list[str] = []
    if title_ai or title_infra:
        zones.append("title")
    if subtitle_ai:
        zones.append("subtitle")
    if lead_ai or lead_infra:
        zones.append("lead")
    if section_ai:
        zones.append("publisher_section")
    if ai_events:
        zones.append("confirmed_event")

    common = dict(
        surface_market=surface,
        structural_event=structural_class,
        structural_event_terms=structural_terms,
        title_ai_terms=title_ai,
        lead_ai_terms=lead_ai,
        title_infra_terms=title_infra,
        lead_infra_terms=lead_infra,
        opinion_labeled=opinion,
        evidence_zones=tuple(dict.fromkeys(zones)),
    )

    if title_ai:
        return AICentralityDecision(
            LEVEL_EXPLICIT_AI_CORE,
            reason="ai_direct_title_subject",
            **common,
        )

    if title_infra and (lead_ai or section_ai or ai_events):
        return AICentralityDecision(
            LEVEL_ENABLING_INFRASTRUCTURE_CORE,
            reason="infrastructure_title_subject_with_explicit_ai_relationship",
            **common,
        )

    if (lead_ai or section_ai) and structural_class:
        # No AI subject in the title, but the title+lead zone proves an
        # independently material structural AI event (human-precedent
        # pattern: market-cap article whose causal event is AI budget
        # displacement or AI talent loss).
        return AICentralityDecision(
            LEVEL_ENABLING_INFRASTRUCTURE_CORE,
            reason=f"structural_ai_causal_event:{structural_class}",
            **common,
        )

    if lead_ai or section_ai:
        return AICentralityDecision(
            LEVEL_INCIDENTAL_AI_MENTION,
            reason="ai_only_as_context_or_driver",
            **common,
        )

    return AICentralityDecision(
        LEVEL_NON_AI,
        reason="no_material_ai_subject_in_title_or_lead",
        **common,
    )


def delivery_category(
    article: object,
    decision: AICentralityDecision | None = None,
) -> tuple[str, tuple[str, ...], str]:
    """(category, evidence terms, evidence zone) from the title/lead map only.

    Returns ("", (), "") when no category has title/lead evidence — such an
    article can never be delivered with a category (R4-R6 §7)."""
    decision = decision or classify(article)
    if not decision.is_central:
        return "", (), ""
    title = f" {_lower(article_title(article))} "
    lead = f" {article_lead_sentence(article)} {_lower(article_subtitle(article))} "
    for zone_name, zone_text in (("title", title), ("lead", lead)):
        for category, terms in _CATEGORY_RULES:
            hits = _has(zone_text, terms)
            if hits:
                return category, hits, zone_name
    if decision.title_ai_terms:
        # Pure-AI subject with no sharper axis: corporate/strategy is the
        # honest general bucket, evidenced by the title AI subject itself.
        return CATEGORY_AI_CORPORATE, decision.title_ai_terms, "title"
    return "", (), ""
