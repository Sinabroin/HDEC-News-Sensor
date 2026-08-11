"""Article-level Teams AI news selection and Adaptive Card rendering (D7-AK-5B).

This leaf module owns only three concerns:

* AI-topic classification for Teams push (the broader dashboard remains unchanged).
* Importance mapping from the existing confirmed-event and scoring contracts.
* One Adaptive Card message per article, ordered highest importance first.

It never reads environment variables, writes state, calls a webhook, or sends email.
A caller must perform delivery and then record success through ``app.teams_push_state``.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence

from app import (
    ai_centrality,
    editorial_preference_runtime,
    executive_materiality,
    public_institution_routing,
    publisher_direct,
    source_priority,
)
from app.public_urls import CANONICAL_DASHBOARD_URL
from app.scoring import DAILY_THRESHOLD, INSTANT_THRESHOLD

KST = timezone(timedelta(hours=9))
# D7-AK-6C: up to ten important/top-priority AI articles per run (was three).
MAX_TEAMS_ARTICLES = 10
# D7-AK-6E R4-R6: the production sender delivers 0-5 articles per natural run.
# DEFAULT applies when no cap is configured; HARD is the ceiling any
# configuration clamps to. Never invent filler to reach a minimum — "minimum
# one" applies only when at least one qualified unsent article exists.
DEFAULT_TEAMS_BATCH_MAX = 5
HARD_TEAMS_BATCH_MAX = 5

# D7-AK-6E R4-R9A / R4-R9D — Teams major-media-first source gate (canonical
# constants).  R4-R9D removes the automatic specialist/trusted-other fallback
# entirely: a specialist article must never become a standalone Teams card,
# even after the holdback elapses, even when the event is TOP and directly
# relevant to Hyundai E&C.  The system prefers zero delivery over a
# specialist-only delivery.  The 120-minute window survives only as
# operator/audit metadata (age is still computed) — it can never select.
TEAMS_SPECIALIST_HOLDBACK_MINUTES = 120
# R4-R9D: 0 — automatic specialist fallback removed. Selection hard-clamps this
# to 0 regardless of any caller-supplied override (see apply_major_media_first_gate).
TEAMS_SPECIALIST_MAX_PER_BATCH = 0
TEAMS_NORMAL_PACING_MINUTES = 60

SOURCE_GATE_PRIMARY_10 = "primary_10"
SOURCE_GATE_SECONDARY_3 = "secondary_3"
SOURCE_GATE_MAJOR_SECONDARY = "major_secondary"
SOURCE_GATE_PROMOTED_OFFICIAL = "promoted_official"
SOURCE_GATE_SPECIALIST_HOLDBACK = "specialist_holdback"
SOURCE_GATE_NEVER_AUTOMATIC = "never_automatic"

SELECTION_MODE_IMMEDIATE = "immediate"
SELECTION_MODE_FALLBACK = "fallback"

IMPORTANCE_TOP = "top"
IMPORTANCE_IMPORTANT = "important"
IMPORTANCE_LABELS = {
    IMPORTANCE_TOP: "🔴 최우선",
    IMPORTANCE_IMPORTANT: "🟠 중요",
}
IMPORTANCE_RANK = {IMPORTANCE_TOP: 0, IMPORTANCE_IMPORTANT: 1}

# Each rule is intentionally explicit. Dashboard taxonomy is not changed; these labels
# are only for the Teams push surface.
_TOPIC_RULES: tuple[tuple[str, str, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        "ai_datacenter",
        "AI 데이터센터",
        ("데이터센터", "데이터 센터", "data center", "datacenter", "idc", "gpu 클러스터", "gpu cluster"),
        ("ai", "인공지능", "gpu", "nvidia", "엔비디아", "hbm", "가속기", "accelerator"),
    ),
    (
        "ai_power_infrastructure",
        "AI 전력 인프라",
        ("전력망", "전력 인프라", "송전", "변전", "송배전", "grid", "power infrastructure", "전력 수요"),
        ("ai", "인공지능", "gpu", "nvidia", "엔비디아"),
    ),
    (
        "nuclear_smr_ai_power",
        "원전·SMR과 AI 전력수요",
        ("원전", "원자력", "smr", "소형모듈원자로", "small modular reactor"),
        ("ai", "인공지능", "전력 수요", "전력수요", "gpu"),
    ),
    (
        "smart_construction",
        "스마트건설",
        ("스마트건설", "스마트 건설", "contech", "construction tech"),
        (),
    ),
    (
        "bim_digital_twin",
        "BIM·디지털 트윈",
        ("bim", "building information modeling", "디지털 트윈", "digital twin"),
        (),
    ),
    (
        "construction_robotics",
        "건설 로봇·자율화",
        ("건설 로봇", "건설로봇", "construction robot", "현장 로봇", "자율 시공", "자율화", "무인 시공"),
        (),
    ),
    (
        "generative_ai_work",
        "생성형 AI 업무혁신",
        ("생성형 ai", "generative ai", "llm", "대규모 언어모델", "copilot", "코파일럿", "업무 자동화", "ai agent", "ai 에이전트"),
        (),
    ),
    (
        "physical_ai_industrial",
        "피지컬 AI·산업 전환",
        (
            "피지컬 ai",
            "physical ai",
            "제조 ai",
            "산업 ai",
            "ai 네이티브",
            "ai-native",
            "인텔리전트 팩토리",
        ),
        (),
    ),
    (
        "ai_national_strategy_supply_chain",
        "AI 국가전략·패권·공급망",
        (
            "ai",
            "인공지능",
            "artificial intelligence",
        ),
        (
            "동맹",
            "공급망",
            "패권",
            "제재",
            "수출통제",
            "수출 통제",
            "미중",
            "미·중",
            "중국",
            "메가프로젝트",
            "메가 프로젝트",
            "국가 전략",
            "서밋",
            "선언",
            "정상회담",
        ),
    ),
    (
        "ai_risk_governance",
        "AI 위험·안전·거버넌스",
        (
            "ai",
            "인공지능",
            "생성형 ai",
            "generative ai",
            "오픈웨이트",
            "open-weight",
        ),
        (
            "안전",
            "위험",
            "보안",
            "생물학",
            "무기",
            "오남용",
            "저품질",
            "가짜",
            "사기",
            "윤리",
            "책임",
            "저작권",
            "통제",
            "규제",
            "논쟁",
            "논란",
            "일자리",
            "고용",
        ),
    ),
    (
        "ai_research_industry_application",
        "AI 연구·산업 적용",
        (
            "ai",
            "인공지능",
            "머신러닝",
            "machine learning",
        ),
        (
            "연구",
            "논문",
            "학회",
            "icml",
            "실증",
            "산학",
            "산학협력",
            "솔루션",
            "도시관제",
            "군중",
        ),
    ),
    (
        "ai_devices_wearables",
        "AI 디바이스·웨어러블",
        (
            "ai",
            "인공지능",
            "artificial intelligence",
        ),
        (
            "스마트글래스",
            "스마트 글래스",
            "웨어러블",
            "wearable",
            "xr",
            "확장현실",
            "스마트 안경",
            "ai 안경",
        ),
    ),
    (
        "ai_policy_regulation",
        "AI 규제·정책",
        ("ai", "인공지능", "artificial intelligence"),
        ("규제", "정책", "법안", "법률", "시행령", "가이드라인", "의무화", "regulation", "policy", "act"),
    ),
    (
        "hdec_competitor_ai",
        "현대건설·경쟁사 AI 사업",
        ("현대건설", "삼성물산", "대우건설", "gs건설", "dl이앤씨", "포스코이앤씨", "sk에코플랜트"),
        ("ai", "인공지능", "스마트건설", "bim", "디지털 트윈", "로봇", "자율화"),
    ),
    (
        "major_ai_company_move",
        "주요 AI 기업 투자·계약·출시",
        (
            "openai", "오픈ai", "microsoft", "마이크로소프트", "google", "구글",
            "alphabet", "meta", "메타", "anthropic", "앤트로픽", "nvidia", "엔비디아",
            "amazon", "아마존", "aws", "xai", "x.ai", "oracle", "오라클",
            "samsung", "삼성전자", "sk hynix", "sk하이닉스",
        ),
        ("ai", "인공지능", "gpu", "llm", "데이터센터", "data center"),
    ),
)

_AI_GENERAL_TERMS = (
    " ai ", "ai", "인공지능", "artificial intelligence", "생성형 ai", "generative ai",
    "llm", "대규모 언어모델", "머신러닝", "machine learning", "gpu", "npu",
)

_AI_CORE_TERMS = (
    "ai",
    "인공지능",
    "artificial intelligence",
    "생성형 ai",
    "generative ai",
    "llm",
    "대규모 언어모델",
    "머신러닝",
    "machine learning",
    "딥러닝",
    "deep learning",
    "컴퓨터 비전",
    "computer vision",
    "ai agent",
    "ai 에이전트",
    "agentic ai",
    "에이전틱 ai",
    "openai",
    "오픈ai",
    "chatgpt",
    "챗gpt",
    "gpt",
    "claude",
    "클로드",
    "gemini",
    "제미나이",
    "gpu",
    "npu",
    "hbm",
)

_AI_LEAD_PREFIX_LIMIT = 96

_HDEC_RELEVANT_TOPIC_KEYS = frozenset(
    {
        "ai_datacenter",
        "ai_power_infrastructure",
        "nuclear_smr_ai_power",
        "smart_construction",
        "bim_digital_twin",
        "construction_robotics",
        "generative_ai_work",
        "hdec_competitor_ai",
    }
)

_AI_ALWAYS_STRATEGIC_TOPIC_KEYS = frozenset(
    {
        "physical_ai_industrial",
        "ai_national_strategy_supply_chain",
        "ai_risk_governance",
        "ai_policy_regulation",
    }
)

_AI_CONDITIONAL_STRATEGIC_TOPIC_KEYS = frozenset(
    {
        "major_ai_company_move",
        "ai_material_event",
        "ai_research_industry_application",
        "ai_devices_wearables",
    }
)

_AI_STRATEGIC_SIGNALS = (
    "투자",
    "자본지출",
    "capex",
    "데이터센터",
    "데이터 센터",
    "data center",
    "반도체",
    "첨단 칩",
    "ai 칩",
    "gpu",
    "hbm",
    "전력",
    "용수",
    "공급망",
    "동맹",
    "패권",
    "제재",
    "수출통제",
    "수출 통제",
    "피지컬 ai",
    "physical ai",
    "제조 ai",
    "로봇",
    "자율주행",
    "자율 시공",
    "ai 네이티브",
    "ai-native",
    "인텔리전트 팩토리",
    "국가 전략",
    "메가프로젝트",
    "메가 프로젝트",
    "서밋",
    "선언",
    "안전",
    "위험",
    "보안",
    "생물학",
    "무기",
    "오픈웨이트",
    "open-weight",
    "오픈소스",
    "연구",
    "논문",
    "학회",
    "icml",
    "실증",
    "도시관제",
    "군중",
    "스마트글래스",
    "스마트 글래스",
    "스마트 안경",
    "웨어러블",
    "wearable",
    "xr",
    "확장현실",
    "일자리",
    "고용",
    "저작권",
    "윤리",
    "사기",
    "저품질",
)

_CONSUMER_AI_ONLY_SIGNALS = (
    "사진 꾸미기",
    "사진 필터",
    "셀카 필터",
    "개인용 ai 사진",
    "개인용 사진 앱",
    "게임 캐릭터",
    "연예인 합성",
)

_HDEC_CONTEXT_TERMS = (
    "현대건설",
    "hyundai e&c",
    "hyundai engineering & construction",
    "건설",
    "construction",
    "epc",
    "데이터센터",
    "데이터 센터",
    "data center",
    "전력 인프라",
    "전력망",
    "송전",
    "변전",
    "송배전",
    "원전",
    "원자력",
    "smr",
    "플랜트",
    "스마트건설",
    "스마트 건설",
    "bim",
    "디지털 트윈",
    "건설 로봇",
    "자율 시공",
    "설계",
    "시공",
    "현장",
    "안전",
    "조달",
    "엔지니어링",
    "도시정비",
)

_HDEC_SECTION_VALUES = {
    "hdec_direct",
    "order_overseas",
    "competitor",
    "competitor_supplier",
}

_STOCK_TERMS = (
    "주가", "목표주가", "투자의견", "테마주", "관련주", "수혜주", "대장주", "급등주",
    "상한가", "증권가", "증권사", "stock price", "price target",
)
_PROMO_REVIEW_TERMS = (
    "협찬", "광고", "프로모션", "할인", "최저가", "사용 후기", "직접 써본", "리뷰",
    "체험기", "구매 가이드", "sponsored", "review",
)
# 채용·도서 출간·게시판 공지 등 사건이 아닌 콘텐츠. 제목이 이런 성격이면 Teams 발송에서
# 제외한다(rules §E). 판정은 제목만 본다 — 집계 스니펫의 잡음(예: 다른 기사의 '수주')이 채용
# 공지를 뉴스로 둔갑시키지 못하게 한다. 단, 제목 자체에 확정 행위(착공/계약 등)가 함께 있으면
# 실제 사건으로 보고 배제하지 않는다.
_NONNEWS_TERMS = (
    "채용", "구인", "모집", "인재 영입", "경력직", "신입 공채", "공채", "hiring", "recruit",
    # 인재/인력을 '찾는다·뽑는다·구한다·모집·채용·선발' 형태로 구인하는 HR PR (사건 아님).
    "인재 찾", "인재를 찾", "인재 채용", "인재 선발", "인재 모집", "인재 확보 나서",
    "인력 채용", "인력 모집", "인력 충원", "채용 공고", "채용설명회", "채용 설명회",
    "출간", "신간", "도서 출간", "book launch", "저자 인터뷰", "게시판",
)
_SPECULATION_TERMS = (
    "전망", "예상", "관측", "가능성", "수혜 기대", "기대감", "추측", "할 수도",
    "could", "may", "might", "expected to", "forecast", "outlook",
)
_CONFIRMED_ACTION_TERMS = (
    "확정", "체결", "계약", "수주", "낙찰", "선정", "승인", "통과", "시행", "발효",
    "출시", "공개", "상용화", "착공", "준공", "투자한다", "투자 확정", "인수", "합병",
    "signed", "awarded", "selected", "approved", "launched", "released", "effective",
    "will invest", "acquired", "completed",
)
_LOW_SOURCE_VALUES = {"low", "excluded", "blocked"}

_MAJOR_CONFIRMED_EVENT_TOKENS = (
    "contract", "agreement", "order", "award", "investment", "funding", "acquisition",
    "launch", "release", "regulation", "policy", "law", "approval", "construction",
    "계약", "협약", "수주", "낙찰", "투자", "인수", "합병", "출시", "공개", "규제",
    "정책", "법", "승인", "착공", "선정",
)

# ---------------------------------------------------------------------------
# R4-R9B §4 — Teams stock-market dominant-subject hard exclusion.
#
# Market-commentary title forms the canonical ai_centrality vocabulary does
# not carry: rally / index / sector-rotation / profit-taking / sentiment /
# strategy / valuation-debate framing.  Observed-production evidence: the
# NewsPim "[5일 중국증시] AI 랠리 훈풍…순환매 장세" delivery would still pass
# every layer once "증시" were absent — "랠리/순환매/코스피" alone had no rule.
# Deliberately absent: bare "투자" (strategic action signal), bare
# "강세/약세" (industry demand phrasing), bare "수혜" (policy-benefit
# phrasing) — those would reject legitimate industry events.
# ---------------------------------------------------------------------------
_STOCK_DOMINANT_EXTENDED_TERMS = (
    "랠리", "순환매", "차익실현", "차익 실현", "투자심리", "투자 심리",
    "투자전략", "투자 전략", "매수 추천", "매도 추천", "비중 확대", "비중확대",
    "저가 매수", "매수세", "매도세", "순매수", "순매도", "공매도",
    "코스피", "코스닥", "나스닥", "다우지수", "다우존스", "s&p500", "s&p 500",
    "주식시장", "주식 시장", "장세", "시황", "폭등", "폭락", "신고가", "신저가",
    "수혜 종목", "고평가 논란", "저평가 논란", "거품 논란", "버블 논란",
)

#: Named Hyundai E&C entities — the §5 exception is entity-strict; broad
#: industry context ("건설", "EPC") can never create it.
_HDEC_ENTITY_TITLE_TERMS = (
    "현대건설",
    "현대엔지니어링",
    "hyundai e&c",
    "hyundai engineering & construction",
)

#: Confirmed material HDEC event forms (title/lead zone).  Definite forms
#: only — bare "수주"/"계약" would let "수주 기대감" speculation qualify.
_HDEC_MATERIAL_EVENT_TERMS = (
    "계약 체결", "계약을 체결", "계약 공시", "공급계약", "공급 계약",
    "수주했", "수주 확정", "수주 계약", "수주 성공", "낙찰", "우선협상대상자",
    "착공", "준공", "공시", "제재", "과징금", "행정처분", "영업정지",
    "중대재해", "사고", "인수 완료", "인수 확정", "투자 확정", "투자한다",
    "승인", "발효", "시행", "협약 체결", "mou 체결",
)

STOCK_MARKET_EXCLUSION_REASON = "stock_market_dominant_no_hdec_material_event"


@dataclass(frozen=True)
class StockMarketGateDecision:
    """R4-R9B §4/§5 — one evidence-based stock-market gate decision.

    ``dominant`` is the §4 dominant-subject verdict; ``eligible`` is False
    only for the hard rejection (dominant without the §5 HDEC material-event
    exception).  ``blunt_stock_evidence`` records legacy full-text stock-term
    hits so the §5 exception can also lift the pre-existing blunt text
    rejection for a materially HDEC article (fixture: EPC contract with a
    secondary share-price mention).  Generated why-it-matters text is never
    an evidence zone here.
    """

    dominant: bool
    hdec_material_event: bool
    eligible: bool
    exclusion_reason: str = ""
    exception_reason: str = ""
    evidence_terms: tuple[str, ...] = ()
    blunt_stock_evidence: bool = False


@dataclass(frozen=True)
class TopicDecision:
    eligible: bool
    topic_key: str = ""
    topic_label: str = ""
    matched_terms: tuple[str, ...] = ()
    exclusion_reason: str = ""


@dataclass(frozen=True)
class OpinionGateDecision:
    """Deterministic realtime exclusion using publisher-owned evidence only."""

    excluded: bool
    reason: str = ""
    evidence: str = ""


@dataclass(frozen=True)
class ImportanceDecision:
    sendable: bool
    level: str = ""
    label: str = ""
    reason: str = ""
    score: float | None = None
    hdec_direct: bool = False


@dataclass(frozen=True)
class TeamsPushCandidate:
    article: Mapping[str, Any]
    topic: TopicDecision
    importance: ImportanceDecision
    cluster_key: str
    material_signature: str
    is_update: bool = False
    # R4-R6 §7 — evidence-based delivery category from the canonical
    # title/lead map; a sendable candidate always carries one.
    delivery_category: str = ""
    # R4-R7 §4 stage 8 — human-memory preference decision. Audit-only shadow
    # while the committed profile is inactive: ranks expose the would-change
    # ordering; the bounded adjustment reorders equal-importance peers only
    # when an explicitly activated (or preview-fixture) profile is verified.
    editorial_memory_profile: str = ""
    editorial_memory_active: bool = False
    approved_precedent_ids: tuple[str, ...] = ()
    rejected_precedent_ids: tuple[str, ...] = ()
    near_miss_precedent_ids: tuple[str, ...] = ()
    silver_precedent_ids: tuple[str, ...] = ()
    memory_preference_score: float = 0.0
    memory_preference_adjustment: float = 0.0
    memory_rank_before: int = 0
    memory_rank_after: int = 0
    memory_changed_selection: bool = False
    source_class: str = public_institution_routing.SOURCE_CLASS_OTHER
    editorial_lane: str = public_institution_routing.LANE_MAIN
    public_institution_type: str = ""
    official_source_name: str = ""
    default_surface: str = public_institution_routing.SURFACE_MAIN
    main_surface_eligible: bool = True
    teams_alert_eligible: bool = True
    tni_brief_eligible: bool = True
    tni_report_topic_eligible: bool = False
    promotion_reason: str = "not_public_institution"
    final_category: str = ""


@dataclass(frozen=True)
class TeamsPolicyEvaluation:
    """One deterministic policy decision with a single aggregate-safe outcome."""

    article: Mapping[str, Any]
    topic: TopicDecision
    hdec_relevant: bool
    importance: ImportanceDecision
    source_authority_passed: bool
    eligible: bool
    rejection_reason: str = ""
    delivery_category: str = ""
    public_routing: (
        public_institution_routing.PublicInstitutionRoutingDecision
    ) = public_institution_routing.PublicInstitutionRoutingDecision()
    # R4-R9B — stock-market gate decision; None only on the transport-level
    # early rejections (carry-forward / freshness / authority / malformed)
    # that never reach any delivery lane.
    stock_market: StockMarketGateDecision | None = None


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


def _article_text(article: object) -> str:
    after = _mapping(article, "after")
    provenance = _mapping(article, "provenance") or _mapping(after, "provenance")
    values = (
        _value(article, "title"), _value(article, "summary"), _value(article, "snippet"),
        _value(article, "hdec_relevance"), _value(article, "whyImportant"),
        _value(article, "why_it_matters"),
        _value(article, "radarReason"), _value(article, "category"),
        _value(article, "category_label"), _value(article, "source"),
        after.get("title"), after.get("snippet"), after.get("whyImportant"),
        after.get("why_it_matters"),
        after.get("radarReason"), after.get("category_label"),
        provenance.get("ai_topic"), provenance.get("ai_category"),
    )
    return " ".join(_lower(v) for v in values if _clean(v))


def _core_article_text(article: object) -> str:
    """기사 제목·리드 요약만 반환한다."""
    after = _mapping(article, "after")
    values = (
        _value(article, "title"),
        _value(article, "summary"),
        _value(article, "snippet"),
        after.get("title"),
        after.get("summary"),
        after.get("snippet"),
    )
    return " ".join(
        _lower(value)
        for value in values
        if _clean(value)
    )


def _first_sentence(value: object) -> str:
    text = _lower(value)
    if not text:
        return ""

    first = re.split(
        r"[.!?。！？]\s*|\n+",
        text,
        maxsplit=1,
    )[0]

    return first[:_AI_LEAD_PREFIX_LIMIT]


def _ai_core_evidence(article: object) -> tuple[str, ...]:
    title = f" {_lower(_value(article, 'title'))} "
    title_hits = _has(title, _AI_CORE_TERMS)

    if title_hits:
        return title_hits

    after = _mapping(article, "after")
    lead_values = (
        _value(article, "summary"),
        _value(article, "snippet"),
        after.get("summary"),
        after.get("snippet"),
    )

    first_sentences = " ".join(
        _first_sentence(value)
        for value in lead_values
        if _clean(value)
    )

    return _has(
        f" {first_sentences} ",
        _AI_CORE_TERMS,
    )


def _hdec_context_text(article: object) -> str:
    after = _mapping(article, "after")
    provenance = (
        _mapping(article, "provenance")
        or _mapping(after, "provenance")
    )

    values = (
        _value(article, "hdec_relevance"),
        _value(article, "whyImportant"),
        _value(article, "why_it_matters"),
        _value(article, "radarReason"),
        _value(article, "executive_section"),
        _value(article, "radar_section"),
        after.get("hdec_relevance"),
        after.get("whyImportant"),
        after.get("why_it_matters"),
        after.get("radarReason"),
        after.get("executive_section"),
        after.get("radar_section"),
        provenance.get("hdec_relevance"),
        provenance.get("whyImportant"),
        provenance.get("why_it_matters"),
        provenance.get("executive_section"),
        provenance.get("radar_section"),
    )

    return " ".join(
        _lower(value)
        for value in values
        if _clean(value)
    )


def _contains_term(text: str, term: str) -> bool:
    needle = term.lower()
    if re.fullmatch(r"[a-z0-9.&-]+", needle):
        pattern = rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])"
        return re.search(pattern, text) is not None
    return needle in text


def _has(text: str, terms: Sequence[str]) -> tuple[str, ...]:
    return tuple(term for term in terms if _contains_term(text, term))


def _has_confirmed_action(article: object, text: str) -> bool:
    confirmed_types = _confirmed_event_types(article)
    if confirmed_types:
        return True
    return bool(_has(text, _CONFIRMED_ACTION_TERMS))


def _confirmed_event_types(article: object) -> tuple[str, ...]:
    raw = _value(article, "shadow_confirmed_event_types", ())
    if isinstance(raw, str):
        raw = (raw,)
    if not isinstance(raw, Iterable):
        return ()
    return tuple(_clean(item).lower() for item in raw if _clean(item))


# The isolated hourly shadow contract (app.radar_signals) emits exactly these five
# categorical statuses. Missing/malformed is a schema error; explicit ``unavailable``
# remains the distinct fail-closed result of a genuine evaluation failure.
_SHADOW_KNOWN_STATUSES = ("confirmed", "ambiguous", "blocked", "none", "unavailable")
_DECISION_RELEVANCE_TIERS = {"A", "A-", "B+", "B", "B-", "C", "exclude"}
_DECISION_RELEVANT_TIERS = {"A", "A-", "B+"}


def _shadow_status(article: object) -> str:
    """Return an explicit status, or ``malformed`` for a missing/unknown value."""
    raw = _value(article, "shadow_urgency_status", None)
    status = _lower(raw)
    return status if status in _SHADOW_KNOWN_STATUSES else "malformed"


def _required_shadow_fields_valid(article: object) -> bool:
    """Validated/live sender rows require explicit categorical urgency evidence."""
    if _shadow_status(article) == "malformed":
        return False
    confirmed = _value(article, "shadow_confirmed_event_types", None)
    return isinstance(confirmed, list) and all(
        isinstance(item, str) for item in confirmed
    )


def normalize_teams_article_fields(
    article: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Normalize validated-Brief/live-delta aliases without inventing evidence."""
    normalized = dict(article)
    owners = (
        article,
        _mapping(article, "after"),
        _mapping(article, "provenance"),
    )

    normalized_tier: int | str | None = None
    for owner in owners:
        for key in ("hdec_relevance_tier", "decision_relevance_tier"):
            raw = _value(owner, key, None)
            if raw in (None, "", "-"):
                continue
            text = _clean(raw)
            try:
                normalized_tier = int(text)
            except (TypeError, ValueError):
                normalized_tier = (
                    text if text in _DECISION_RELEVANCE_TIERS else None
                )
            if normalized_tier is not None:
                break
        if normalized_tier is not None:
            break
    if normalized_tier is not None:
        normalized["hdec_relevance_tier"] = normalized_tier
        normalized["decision_relevance_tier"] = normalized_tier

    why = ""
    for owner in owners:
        for key in ("whyImportant", "why_it_matters"):
            why = _clean(_value(owner, key))
            if why:
                break
        if why:
            break
    if why:
        normalized["whyImportant"] = why
        normalized["why_it_matters"] = why
    return normalized


def _validated_alias_fields_valid(article: object) -> bool:
    """Validated Brief rows must carry one well-formed value from each alias group."""
    tier_valid = False
    for key in ("hdec_relevance_tier", "decision_relevance_tier"):
        raw = _value(article, key, None)
        if raw in (None, "", "-"):
            continue
        text = _clean(raw)
        try:
            int(text)
            tier_valid = True
        except (TypeError, ValueError):
            tier_valid = text in _DECISION_RELEVANCE_TIERS
        if tier_valid:
            break
    why_valid = any(
        _clean(_value(article, key))
        for key in ("whyImportant", "why_it_matters")
    )
    return tier_valid and why_valid


def _source_quality(article: object) -> str:
    for owner in (article, _mapping(article, "after"), _mapping(article, "provenance")):
        value = _value(owner, "source_quality") or _value(owner, "quality")
        if value:
            return _lower(value)
    return ""



def classify_ai_topic(article: object) -> TopicDecision:
    """기사 자체에서 AI가 핵심일 때만 Teams AI 주제로 인정한다."""
    text = f" {_core_article_text(article)} "

    if not text.strip():
        return TopicDecision(
            False,
            exclusion_reason="empty_article_text",
        )

    stock_hits = _has(text, _STOCK_TERMS)
    if stock_hits:
        return TopicDecision(
            False,
            matched_terms=stock_hits,
            exclusion_reason="stock_or_theme_article",
        )

    promo_hits = _has(text, _PROMO_REVIEW_TERMS)
    if promo_hits:
        return TopicDecision(
            False,
            matched_terms=promo_hits,
            exclusion_reason="promo_or_product_review",
        )

    title_text = f" {_lower(_value(article, 'title'))} "
    nonnews_hits = _has(title_text, _NONNEWS_TERMS)

    if nonnews_hits and not _has(
        title_text,
        _CONFIRMED_ACTION_TERMS,
    ):
        return TopicDecision(
            False,
            matched_terms=nonnews_hits,
            exclusion_reason="non_news_recruit_or_book",
        )

    if _source_quality(article) in _LOW_SOURCE_VALUES:
        return TopicDecision(
            False,
            exclusion_reason="low_or_excluded_source",
        )

    # R4-R6/R4-R7 — canonical AI-centrality hard gate. Title-level stock /
    # political / real-estate / civic exclusions and the
    # explicit-or-enabling-core requirement come from app.ai_centrality;
    # a summary AI keyword can never rescue an excluded or incidental
    # article, while a structural AI causal event (budget reallocation,
    # talent loss, infrastructure investment) keeps the human-precedent
    # market articles eligible.
    centrality = ai_centrality.classify(article)
    if centrality.exclusion:
        return TopicDecision(
            False,
            matched_terms=centrality.exclusion_terms,
            exclusion_reason=f"excluded_{centrality.exclusion}",
        )
    if centrality.level not in ai_centrality.CENTRAL_LEVELS:
        return TopicDecision(
            False,
            matched_terms=tuple(
                dict.fromkeys(
                    centrality.title_ai_terms + centrality.lead_ai_terms
                )
            ),
            exclusion_reason=f"ai_not_central_{centrality.level}",
        )

    speculative_hits = _has(text, _SPECULATION_TERMS)

    if speculative_hits and not _has_confirmed_action(
        article,
        text,
    ):
        return TopicDecision(
            False,
            matched_terms=speculative_hits,
            exclusion_reason="speculation_without_confirmed_event",
        )

    ai_core_hits = _ai_core_evidence(article)

    if not ai_core_hits:
        return TopicDecision(
            False,
            exclusion_reason="ai_not_core_topic",
        )

    for (
        topic_key,
        topic_label,
        primary_terms,
        required_terms,
    ) in _TOPIC_RULES:
        primary_hits = _has(text, primary_terms)

        if not primary_hits:
            continue

        required_hits = _has(text, required_terms)

        if required_terms and not required_hits:
            continue

        return TopicDecision(
            True,
            topic_key=topic_key,
            topic_label=topic_label,
            matched_terms=tuple(
                dict.fromkeys(
                    ai_core_hits
                    + primary_hits
                    + required_hits
                )
            ),
        )

    generic_hits = tuple(
        dict.fromkeys(
            ai_core_hits
            + _has(text, _AI_GENERAL_TERMS)
        )
    )

    if generic_hits and _has_confirmed_action(
        article,
        text,
    ):
        return TopicDecision(
            True,
            topic_key="ai_material_event",
            topic_label="AI 주요 확정 이벤트",
            matched_terms=generic_hits,
        )

    return TopicDecision(
        False,
        matched_terms=ai_core_hits,
        exclusion_reason="not_in_teams_ai_topics",
    )

def _parse_score(article: object) -> float | None:
    owners = (article, _mapping(article, "after"), _mapping(article, "provenance"))
    for owner in owners:
        for key in ("score", "urgency_score", "final_score", "executive_score"):
            value = _value(owner, key, None)
            if value in (None, "", "-"):
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    return None


def _hdec_direct(article: object) -> bool:
    for owner in (article, _mapping(article, "after"), _mapping(article, "provenance")):
        value = _value(owner, "hdec_direct", None)
        if isinstance(value, bool):
            return value
        section = _lower(_value(owner, "executive_section") or _value(owner, "radar_section"))
        if section == "hdec_direct":
            return True
    text = _article_text(article)
    return (
        "현대건설" in text
        or "hyundai e&c" in text
        or "hyundai engineering & construction" in text
    )


def is_hdec_relevant_for_push(
    article: object,
    topic: TopicDecision | None = None,
) -> bool:
    """Teams 발송에 필요한 현대건설 사업·기술 연관성을 판정한다."""
    topic = topic or classify_ai_topic(article)

    if not topic.eligible:
        return False

    if _hdec_direct(article):
        return True

    if topic.topic_key in _HDEC_RELEVANT_TOPIC_KEYS:
        return True

    for owner in (
        article,
        _mapping(article, "after"),
        _mapping(article, "provenance"),
    ):
        section = _lower(
            _value(owner, "executive_section")
            or _value(owner, "radar_section")
        )

        if section in _HDEC_SECTION_VALUES:
            return True

        for key in (
            "hdec_relevance_score",
            "decision_relevance_score",
        ):
            raw = _value(owner, key, None)

            if raw in (None, "", "-"):
                continue

            try:
                if float(raw) >= DAILY_THRESHOLD:
                    return True
            except (TypeError, ValueError):
                pass

        for tier_key in ("hdec_relevance_tier", "decision_relevance_tier"):
            tier = _value(owner, tier_key, None)
            if tier in (None, "", "-"):
                continue
            try:
                if int(tier) <= 3:
                    return True
            except (TypeError, ValueError):
                if _clean(tier) in _DECISION_RELEVANT_TIERS:
                    return True

    context = (
        f" {_core_article_text(article)} "
        f"{_hdec_context_text(article)} "
    )

    return bool(
        _has(
            context,
            _HDEC_CONTEXT_TERMS,
        )
    )


# D7-AK-6E-R2N-1-R4: strong strategic signal override
#
# These signals are evaluated only after ``classify_ai_topic`` has confirmed
# that AI is the article's core subject. They therefore cannot promote a
# non-AI LNG, nuclear, turbine, grid, order, or construction article.
_AI_STRONG_BIOLOGICAL_PRIMARY_SIGNALS = (
    "생물학",
    "생물학적",
    "병원체",
    "바이오안보",
    "biological",
    "biosecurity",
    "pathogen",
)
_AI_STRONG_RISK_CONTROL_SIGNALS = (
    "무기",
    "제조",
    "살포",
    "위험",
    "안전",
    "통제",
    "오남용",
    "규제",
    "weapon",
    "manufacture",
    "dissemination",
    "risk",
    "safety",
    "control",
    "misuse",
)
_AI_STRONG_NATIONAL_STRATEGY_SIGNALS = (
    "국가전략",
    "국가 전략",
    "동맹",
    "공급망",
    "패권",
    "제재",
    "수출통제",
    "수출 통제",
    "미중",
    "미·중",
    "정상회담",
    "메가프로젝트",
    "메가 프로젝트",
    "national strategy",
    "alliance",
    "supply chain",
    "export control",
)
_AI_STRONG_INFRASTRUCTURE_SIGNALS = (
    "데이터센터",
    "데이터 센터",
    "data center",
    "datacenter",
    "gpu 클러스터",
    "전력망",
    "전력 인프라",
    "전력 수요",
    "전력수요",
    "송전",
    "변전",
    "용수",
    "power infrastructure",
    "power demand",
    "water demand",
)
_AI_STRONG_INVESTMENT_ACTION_SIGNALS = (
    "투자",
    "자본지출",
    "capex",
    "investment",
)
_AI_STRONG_INVESTMENT_SCALE_SIGNALS = (
    "대규모",
    "메가",
    "조원",
    "억달러",
    "billion",
    "trillion",
)
_AI_STRONG_PHYSICAL_INDUSTRIAL_SIGNALS = (
    "피지컬 ai",
    "physical ai",
    "제조 ai",
    "manufacturing ai",
    "산업 ai",
    "로봇",
    "robot",
    "robotics",
    "자율주행",
    "자율 시공",
    "인텔리전트 팩토리",
)
_AI_STRONG_OPEN_WEIGHT_SIGNALS = (
    "오픈웨이트",
    "오픈 웨이트",
    "open-weight",
    "open weight",
)
_AI_STRONG_OPEN_WEIGHT_CONTROL_SIGNALS = (
    "규제",
    "수출통제",
    "수출 통제",
    "제재",
    "통제",
    "안전",
    "위험",
    "법안",
    "regulation",
    "export control",
    "sanction",
    "control",
    "safety",
    "risk",
)


def _has_strong_ai_strategic_override(text: str) -> bool:
    """Return a topic-key-independent strong AI strategic signal decision."""
    biological_risk = bool(
        _has(text, _AI_STRONG_BIOLOGICAL_PRIMARY_SIGNALS)
    ) and bool(
        _has(text, _AI_STRONG_RISK_CONTROL_SIGNALS)
    )

    national_strategy = bool(
        _has(text, _AI_STRONG_NATIONAL_STRATEGY_SIGNALS)
    )

    infrastructure = bool(
        _has(text, _AI_STRONG_INFRASTRUCTURE_SIGNALS)
    )

    large_investment = bool(
        _has(text, _AI_STRONG_INVESTMENT_ACTION_SIGNALS)
    ) and bool(
        _has(text, _AI_STRONG_INVESTMENT_SCALE_SIGNALS)
    )

    physical_industrial = bool(
        _has(text, _AI_STRONG_PHYSICAL_INDUSTRIAL_SIGNALS)
    )

    open_weight_control = bool(
        _has(text, _AI_STRONG_OPEN_WEIGHT_SIGNALS)
    ) and bool(
        _has(text, _AI_STRONG_OPEN_WEIGHT_CONTROL_SIGNALS)
    )

    return any(
        (
            biological_risk,
            national_strategy,
            infrastructure,
            large_investment,
            physical_industrial,
            open_weight_control,
        )
    )

def is_ai_strategically_significant(
    article: object,
    topic: TopicDecision | None = None,
) -> bool:
    """현대건설 직접 언급이 없어도 임원이 알아야 할 AI 전략 변화를 판정한다."""
    topic = topic or classify_ai_topic(article)

    if not topic.eligible:
        return False

    # 전략성은 기사 제목·리드 요약에서만 판정한다.
    # 대시보드 해설, category, provenance의 AI 문구는 사용하지 않는다.
    text = f" {_core_article_text(article)} "
    strategic_hits = _has(text, _AI_STRATEGIC_SIGNALS)
    consumer_only_hits = _has(text, _CONSUMER_AI_ONLY_SIGNALS)

    if consumer_only_hits and not strategic_hits:
        return False

    # Topic precedence must not suppress a strong strategic article. For
    # example, a biological-weapons risk story may classify first as
    # ``generative_ai_work`` while still carrying an independently sufficient
    # executive strategy signal.
    if _has_strong_ai_strategic_override(text):
        return True

    if topic.topic_key in _AI_ALWAYS_STRATEGIC_TOPIC_KEYS:
        return True

    if (
        topic.topic_key in _AI_CONDITIONAL_STRATEGIC_TOPIC_KEYS
        and strategic_hits
    ):
        return True

    return False


def is_executive_relevant_for_push(
    article: object,
    topic: TopicDecision | None = None,
) -> bool:
    """Teams 자격 = 현대건설 연관성 또는 독립적인 AI 전략 중요성."""
    topic = topic or classify_ai_topic(article)

    if not topic.eligible:
        return False

    return (
        is_hdec_relevant_for_push(article, topic)
        or is_ai_strategically_significant(article, topic)
    )


def map_importance(article: object, topic: TopicDecision | None = None) -> ImportanceDecision:
    """Map importance from existing scoring/confirmed-event signals; shadow status is a signal.

    D7-AK-6C — the hourly shadow-confirmed status is no longer a hard send gate. Its role
    now depends on its category (rules.md-approved policy):

    * ``blocked``     — hard block (title-level negative / score-crossing-only). Never sent.
    * ``unavailable`` — fail-closed (policy missing / malformed status). Never sent.
    * ``confirmed``   — strongest positive: a top-priority basis and a ranking boost.
    * ``ambiguous``   — never an automatic block; may still send on another importance basis.
    * ``none``        — never an automatic block; may still send on another importance basis.

    Existing thresholds are reused verbatim (INSTANT 4.5 / DAILY 3.5) — no new numeric
    threshold is invented. Stock/theme, promo/review, speculation-only, and low-source
    articles are already excluded upstream in :func:`classify_ai_topic`.
    """
    topic = topic or classify_ai_topic(article)
    if not topic.eligible:
        return ImportanceDecision(False, reason=topic.exclusion_reason)

    if not _required_shadow_fields_valid(article):
        return ImportanceDecision(False, reason="malformed_required_field")

    if not is_executive_relevant_for_push(article, topic):
        return ImportanceDecision(
            False,
            reason="insufficient_executive_relevance",
        )

    shadow = _shadow_status(article)
    if shadow == "blocked":
        return ImportanceDecision(False, reason="shadow_blocked")
    if shadow == "unavailable":
        return ImportanceDecision(False, reason="shadow_unavailable")

    score = _parse_score(article)
    hdec_direct = _hdec_direct(article)
    confirmed = shadow == "confirmed"
    confirmed_types = _confirmed_event_types(article)
    major_confirmed = confirmed and any(
        token in event_type
        for event_type in confirmed_types
        for token in _MAJOR_CONFIRMED_EVENT_TOKENS
    )
    has_confirmed_action = _has_confirmed_action(article, f" {_article_text(article)} ")
    is_material_update = _lower(_value(article, "change_type")) == "material_content_update"

    # 최우선(TOP): 기존 INSTANT 이상 · 현대건설 직접 영향 · confirmed 대형 이벤트 · 중대한 material update
    top_reasons: list[str] = []
    if hdec_direct:
        top_reasons.append("현대건설 직접 영향")
    if score is not None and score >= INSTANT_THRESHOLD:
        top_reasons.append("기존 INSTANT 기준 통과")
    if major_confirmed:
        top_reasons.append("대규모 계약·투자·출시·규제 등 확정 이벤트")
    if is_material_update and confirmed and (score is None or score >= DAILY_THRESHOLD):
        top_reasons.append("중대한 내용 업데이트")
    if top_reasons:
        return ImportanceDecision(
            True, IMPORTANCE_TOP, IMPORTANCE_LABELS[IMPORTANCE_TOP],
            " · ".join(top_reasons), score, hdec_direct,
        )

    # 중요(IMPORTANT): 기존 DAILY 이상 · confirmed 이벤트 · 발표·계약·출시 등 사실 기반 사건
    important_reasons: list[str] = []
    if score is not None and score >= DAILY_THRESHOLD:
        important_reasons.append("기존 DAILY 기준 통과")
    if confirmed:
        important_reasons.append("확정 이벤트 + AI 핵심 주제")
    if has_confirmed_action:
        important_reasons.append("발표·계약·출시 등 사실 기반 사건")
    if important_reasons:
        return ImportanceDecision(
            True, IMPORTANCE_IMPORTANT, IMPORTANCE_LABELS[IMPORTANCE_IMPORTANT],
            " · ".join(important_reasons), score, hdec_direct,
        )

    return ImportanceDecision(
        False, reason="insufficient_importance_basis", score=score, hdec_direct=hdec_direct
    )


_ALL_STOCK_MARKET_TERMS: tuple[str, ...] = tuple(
    dict.fromkeys(
        _STOCK_TERMS
        + ai_centrality.STOCK_MARKET_TITLE_TERMS
        + _STOCK_DOMINANT_EXTENDED_TERMS
    )
)


def _hdec_direct_material_event(article: object) -> str:
    """R4-R9B §5 — matched confirmed-HDEC-event term, or "".

    Entity evidence must be a named Hyundai E&C entity in the *title*; the
    confirmed material action may come from title, publisher subtitle, or the
    first lead sentence.  Generated why-it-matters/relevance fields are never
    consulted, so they can never create the exception.
    """
    title = f" {_lower(ai_centrality.article_title(article))} "
    if not _has(title, _HDEC_ENTITY_TITLE_TERMS):
        return ""
    zone = " ".join(
        part
        for part in (
            title,
            _lower(ai_centrality.article_subtitle(article)),
            ai_centrality.article_lead_sentence(article),
        )
        if part.strip()
    )
    hits = _has(f" {zone} ", _HDEC_MATERIAL_EVENT_TERMS)
    return hits[0] if hits else ""


def evaluate_stock_market_gate(article: object) -> StockMarketGateDecision:
    """R4-R9B §4 — semantic, evidence-based stock-market hard gate.

    Dominance lanes (title first, per the P0-C1.11 precedent):

    1. canonical — :func:`app.ai_centrality.classify` surface-market form
       (title tokens or finance-desk section) without a structural AI causal
       event;
    2. extended — a :data:`_STOCK_DOMINANT_EXTENDED_TERMS` commentary form in
       the title, again without a structural AI causal event;
    3. summary — two or more distinct market signals in the publisher factual
       summary while the title carries no confirmed action.

    A dominant article is hard-rejected unless the §5 HDEC material-event
    exception applies; the fact that a stock moved is never itself the event.
    The decision uses title / publisher subtitle / factual summary / explicit
    classification fields only.
    """
    decision = ai_centrality.classify(article)
    structural = bool(decision.structural_event)
    canonical_dominant = decision.surface_market and not structural
    evidence: tuple[str, ...] = decision.exclusion_terms if canonical_dominant else ()

    title = f" {_lower(ai_centrality.article_title(article))} "
    extended_hits = _has(title, _STOCK_DOMINANT_EXTENDED_TERMS)
    extended_dominant = bool(extended_hits) and not structural
    if extended_dominant:
        evidence = tuple(dict.fromkeys(evidence + extended_hits))

    summary_dominant = False
    if not (canonical_dominant or extended_dominant or structural):
        after = _mapping(article, "after")
        summary = _lower(
            _value(article, "summary")
            or _value(article, "snippet")
            or after.get("summary")
            or after.get("snippet")
        )
        if summary:
            summary_hits = _has(f" {summary} ", _ALL_STOCK_MARKET_TERMS)
            distinct = {term.replace(" ", "") for term in summary_hits}
            if len(distinct) >= 2 and not _has(title, _CONFIRMED_ACTION_TERMS):
                summary_dominant = True
                evidence = tuple(dict.fromkeys(evidence + summary_hits))

    dominant = canonical_dominant or extended_dominant or summary_dominant
    blunt = bool(_has(f" {_core_article_text(article)} ", _STOCK_TERMS))
    material_term = (
        _hdec_direct_material_event(article) if (dominant or blunt) else ""
    )
    if dominant and not material_term:
        return StockMarketGateDecision(
            dominant=True,
            hdec_material_event=False,
            eligible=False,
            exclusion_reason=STOCK_MARKET_EXCLUSION_REASON,
            evidence_terms=evidence,
            blunt_stock_evidence=blunt,
        )
    exception_reason = (
        f"hdec_material_event:{material_term}" if material_term else ""
    )
    return StockMarketGateDecision(
        dominant=dominant,
        hdec_material_event=bool(material_term),
        eligible=True,
        exception_reason=exception_reason,
        evidence_terms=evidence,
        blunt_stock_evidence=blunt,
    )


def _stock_neutralized_article(article: Mapping[str, Any]) -> dict[str, Any]:
    """Copy with stock-market vocabulary scrubbed from factual text fields.

    Used only for the §5 exception re-classification: the remaining evidence
    (AI centrality, confirmed event, relevance) must independently qualify,
    so the exception can never bypass any other gate.
    """
    ordered = sorted(_ALL_STOCK_MARKET_TERMS, key=len, reverse=True)

    def scrub(value: object) -> str:
        text = _clean(value)
        for term in ordered:
            text = re.sub(re.escape(term), " ", text, flags=re.IGNORECASE)
        return " ".join(text.split())

    neutralized = dict(article)
    for key in ("title", "summary", "snippet"):
        if _clean(neutralized.get(key)):
            neutralized[key] = scrub(neutralized.get(key))
    after = _mapping(article, "after")
    if after:
        after_copy = dict(after)
        for key in ("title", "summary", "snippet"):
            if _clean(after_copy.get(key)):
                after_copy[key] = scrub(after_copy.get(key))
        neutralized["after"] = after_copy
    return neutralized


def _watch_executive_evidence(article: Mapping[str, Any]) -> dict:
    """R4-OPS-2 — allowed factual evidence for the executive-materiality floor.

    Mirrors the Daily gate's evidence contract exactly: title, publisher
    subtitle, and the first factual snippet sentence only. The generated
    summary/why-it-matters and the provider query string are deliberately
    excluded (no ``summary`` key is passed), so no generated text or
    search-query metadata can qualify a Watch article for send
    (SEARCH_QUERY_CAUSED_WATCH_QUALIFICATION=0)."""
    after = _mapping(article, "after")

    def _pick(*keys: str) -> str:
        for owner in (article, after):
            for key in keys:
                text = _clean(_value(owner, key))
                if text:
                    return text
        return ""

    return {
        "title": _pick("title"),
        "snippet": _pick("snippet"),
        "subtitle": _pick("subtitle", "publisher_subtitle"),
        "publisher_section": _pick("publisher_section", "section"),
    }


def is_watch_send_noise(article: Mapping[str, Any]) -> tuple[bool, str]:
    """R4-OPS-2 — the real-time Watch executive-materiality noise floor.

    The Teams AI News Watch is deliberately BROADER than the Daily editorial
    digest (D7-AK-6C §8): it must keep alerting on real-time major AI events —
    infrastructure investment/contract, datacenter/grid/power, enterprise
    adoption, regulation, security incidents, HDEC-direct events, and major
    competitor moves — even when they carry no confirmed-action term. So the
    Watch does NOT apply the Daily whitelist gate (which would drop those
    strategic events); it applies a narrow NOISE floor keyed to the observed
    production defect.

    Observed leak (연합뉴스 "…전략산업 ETF 출시", 2026-08-10, sent as important):
    a financial-PRODUCT launch qualified only because the Watch importance path
    treats a bare "출시" as a confirmed action. An ETF/fund/REIT product launch
    is a financial-product event, not an AI-industry event, and the Daily
    surface would never publish it. Using the shared executive-materiality
    contract (app.executive_materiality — one rule, no drift), this floor
    rejects such a story UNLESS the same title/lead independently carries a real
    material industrial event (a non-launch confirmed corporate action in an
    industrial context, an HDEC-direct entity PAIRED WITH such an action, or a
    material AI-security incident) — neither fund SIZE / offering scale nor a
    bare HDEC mention ever rescues it, and the fund vehicle is detected in the
    title + factual lead (R4-OPS-2A §4/§9, R4-OPS-2B §2/§6). Returns
    (is_noise, reason)."""
    evidence = _watch_executive_evidence(article)
    if executive_materiality.is_fund_product_launch_noise(evidence):
        return True, "fund_product_launch_without_material_event"
    return False, ""


_OPINION_SECTION_MARKERS = (
    "칼럼", "오피니언", "사설", "논설", "기고", "기고문", "전문가칼럼", "시론",
)
_OPINION_TITLE_RE = re.compile(
    r"^\s*[\[［【]\s*(칼럼|기고|기고문|사설|오피니언|논설|전문가\s*칼럼|시론)\s*[\]］】]",
    re.IGNORECASE,
)


def evaluate_realtime_opinion_gate(
    article: Mapping[str, Any],
) -> OpinionGateDecision:
    """Exclude explicit opinion/contributed content from realtime auto-send.

    Only the authoritative title and publisher section are inspected. Generated
    summaries, search queries, inferred body text, and ranking metadata are not
    inputs, so neither discovery nor generation can manufacture this verdict.
    """
    evidence = _watch_executive_evidence(article)
    section = re.sub(r"\s+", "", _clean(evidence.get("publisher_section"))).casefold()
    for marker in _OPINION_SECTION_MARKERS:
        normalized = re.sub(r"\s+", "", marker).casefold()
        if normalized and normalized in section:
            return OpinionGateDecision(
                True, "explicit_opinion_publisher_section", f"publisher_section:{marker}"
            )
    title = _clean(evidence.get("title"))
    match = _OPINION_TITLE_RE.search(title)
    if match:
        return OpinionGateDecision(
            True, "explicit_opinion_title_marker", f"title_marker:{match.group(1)}"
        )
    return OpinionGateDecision(False)


def evaluate_teams_push_policy(
    article: Mapping[str, Any],
    *,
    require_validated_fields: bool = False,
) -> TeamsPolicyEvaluation:
    """Evaluate one row once and assign one mutually exclusive rejection reason.

    The ordering is deliberate: public carry-forward/freshness and publisher
    authority are independent transport contracts; normalized validated-Brief
    fields are then required before the unchanged AI/HDEC/importance policy runs.
    """
    article = normalize_teams_article_fields(article)
    public_route = public_institution_routing.classify(article)
    empty_topic = TopicDecision(False)
    empty_importance = ImportanceDecision(False)
    if (
        _value(article, "carried_forward") is True
        or _value(article, "teams_newness_eligible") is False
    ):
        return TeamsPolicyEvaluation(
            article, empty_topic, False, empty_importance, False, False,
            "carry_forward_excluded",
        )
    if _value(article, "current_run_seen") is False:
        return TeamsPolicyEvaluation(
            article, empty_topic, False, empty_importance, False, False,
            "freshness_failed",
        )

    authority = publisher_direct.assess_delivery_eligibility(
        article,
        relevance_qualified=True,
    )
    if (
        _value(article, "source_quality_passed") is False
        or not authority.eligible
    ):
        return TeamsPolicyEvaluation(
            article, empty_topic, False, empty_importance, False, False,
            "source_authority_failed",
        )

    if (
        not _required_shadow_fields_valid(article)
        or (
            require_validated_fields
            and not _validated_alias_fields_valid(article)
        )
    ):
        return TeamsPolicyEvaluation(
            article, empty_topic, False,
            ImportanceDecision(False, reason="malformed_required_field"),
            True, False, "malformed_required_field",
        )

    opinion_gate = evaluate_realtime_opinion_gate(article)
    if opinion_gate.excluded:
        return TeamsPolicyEvaluation(
            article,
            empty_topic,
            False,
            ImportanceDecision(False, reason=opinion_gate.reason),
            True,
            False,
            "excluded_opinion_content",
        )

    # R4-R9B §4 — the stock-market hard gate is decided before topic
    # classification, ranking, the ledger, the major-media source gate, the
    # specialist holdback, and every fallback: a hard-rejected article never
    # becomes a candidate at all.  The five decision fields are stamped on
    # the normalized row so every downstream audit sees the same verdict.
    stock_gate = evaluate_stock_market_gate(article)
    article["stock_market_dominant_subject"] = stock_gate.dominant
    article["hdec_direct_material_event"] = stock_gate.hdec_material_event
    article["stock_market_exclusion_reason"] = stock_gate.exclusion_reason
    article["stock_market_exception_reason"] = stock_gate.exception_reason
    article["teams_stock_market_eligible"] = stock_gate.eligible

    topic = classify_ai_topic(article)
    if not topic.eligible:
        stock_class_reasons = {
            "stock_or_theme_article",
            f"excluded_{ai_centrality.EXCLUSION_STOCK_MARKET}",
        }
        if (
            topic.exclusion_reason in stock_class_reasons
            and stock_gate.eligible
            and stock_gate.hdec_material_event
        ):
            # R4-R9B §5 — the dominant confirmed event is independently
            # material to Hyundai E&C, so a market reference must not itself
            # reject the article.  Re-classify on the stock-neutralized
            # evidence: every other gate (AI centrality, speculation,
            # relevance, importance, source gate, ledger) still applies to
            # the remaining evidence and can still reject it.
            topic = classify_ai_topic(_stock_neutralized_article(article))
    if not topic.eligible:
        if topic.exclusion_reason == "speculation_without_confirmed_event":
            reason = "speculation_only"
        elif topic.exclusion_reason.startswith(
            ("excluded_", "ai_not_central_")
        ):
            # Canonical AI-centrality rejections stay granular so the audit
            # can distinguish stock/political/real-estate/civic exclusions
            # from incidental-AI and non-AI subjects.
            reason = topic.exclusion_reason
        else:
            reason = "not_ai_core"
        if not stock_gate.eligible and not reason.startswith(
            ("excluded_", "ai_not_central_")
        ):
            # R4-R9B §4 — a hard-rejected dominant market article records the
            # stock exclusion rather than a vague legacy bucket; canonical
            # granular reasons above keep their existing vocabulary.
            reason = "excluded_stock_market_dominant"
        return TeamsPolicyEvaluation(
            article, topic, False,
            ImportanceDecision(False, reason=topic.exclusion_reason),
            True, False, reason,
            stock_market=stock_gate,
        )

    if not stock_gate.eligible:
        # R4-R9B §4 — extended/summary-lane dominant market article that the
        # canonical title vocabulary missed (e.g. "AI 랠리…코스피" forms).
        return TeamsPolicyEvaluation(
            article, topic, False,
            ImportanceDecision(False, reason=STOCK_MARKET_EXCLUSION_REASON),
            True, False, "excluded_stock_market_dominant",
            stock_market=stock_gate,
        )

    hdec_relevant = is_executive_relevant_for_push(article, topic)
    if not hdec_relevant:
        return TeamsPolicyEvaluation(
            article, topic, False,
            ImportanceDecision(False, reason="insufficient_executive_relevance"),
            True, False, "insufficient_hdec_relevance",
            stock_market=stock_gate,
        )

    # R4-OPS-2 — executive-materiality noise floor. An AI-central, HDEC-relevant
    # article is still not sent to executives if it is executive noise: an
    # ETF/fund/REIT product-launch story with no independent material industrial
    # event. This closes the observed production leak (연합뉴스 "…전략산업 ETF
    # 출시", 2026-08-10) the importance path admitted via a bare "출시" confirmed
    # action, while keeping the Watch broader than the Daily digest for genuine
    # real-time AI events (D7-AK-6C §8 recall preserved).
    is_noise, noise_reason = is_watch_send_noise(article)
    if is_noise:
        return TeamsPolicyEvaluation(
            article, topic, True,
            ImportanceDecision(False, reason=noise_reason),
            True, False, "excluded_fund_product_noise",
            stock_market=stock_gate,
        )

    importance = map_importance(article, topic)
    if not importance.sendable:
        reason_map = {
            "shadow_unavailable": "shadow_unavailable",
            "insufficient_importance_basis": "insufficient_importance",
        }
        return TeamsPolicyEvaluation(
            article, topic, True, importance, True, False,
            reason_map.get(importance.reason, "other_policy_reason"),
            stock_market=stock_gate,
        )

    # R4-R6 §7 — an article cannot be sent with a category whose evidence is
    # absent from the title/lead evidence map.
    category, _category_terms, _category_zone = ai_centrality.delivery_category(
        article
    )
    if not category:
        return TeamsPolicyEvaluation(
            article, topic, True, importance, True, False,
            "no_evidenced_delivery_category",
            stock_market=stock_gate,
        )

    # R4-R8: authority is not priority. A verified official article is a
    # default no-send candidate unless a material promotion condition was
    # independently proven from its title/lead. This runs after every existing
    # deterministic hard gate and cannot rescue any rejected article.
    if public_route.is_public_lane and not public_route.teams_alert_eligible:
        return TeamsPolicyEvaluation(
            article,
            topic,
            True,
            importance,
            True,
            False,
            "public_institution_not_promoted",
            delivery_category=category,
            public_routing=public_route,
            stock_market=stock_gate,
        )

    return TeamsPolicyEvaluation(
        article, topic, True, importance, True, True, "",
        delivery_category=category,
        public_routing=public_route,
        stock_market=stock_gate,
    )


def _published_sort_value(article: object) -> float:
    text = _clean(_value(article, "published_at") or _value(article, "published_kst"))
    if not text:
        return 0.0
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def select_teams_push_from_artifact_with_audit(
    payload: object,
    *,
    max_articles: int | None = MAX_TEAMS_ARTICLES,
    preference_runtime: (
        "editorial_preference_runtime.EditorialPreferenceRuntime | None"
    ) = None,
    memory_batch_cap: int | None = None,
) -> tuple[tuple[TeamsPushCandidate, ...], dict[str, int | bool | str]]:
    """Fail-closed entrypoint for a raw delta artifact, with selection audit.

    D7-AK-6C — the artifact-level ``shadow_alert_delta`` flag is no longer required: a
    live-delta artifact can produce candidates even when no article is shadow-confirmed,
    because importance now derives from the reused scoring/confirmed-event signals per
    article (see :func:`map_importance`). Only the live-source guard remains, so
    mock/fallback artifacts and malformed article collections always return zero.
    """
    empty_audit = {
        "policy_eligible": 0,
        "event_duplicates": 0,
        "distinct_events": 0,
        "stock_market_dominant_rows": 0,
        "stock_market_hard_rejected_rows": 0,
        "stock_market_hdec_exception_rows": 0,
        "stock_market_fallback_blocked_rows": 0,
    }
    if not isinstance(payload, Mapping):
        return (), empty_audit
    validated_brief = False
    if _clean(payload.get("source")) == "live-delta":
        articles = payload.get("articles")
    elif (
        _clean(payload.get("artifact_contract")) == "HDEC_VALIDATED_EXECUTIVE_BRIEF_V1"
        and _clean(payload.get("news_data_mode")) == "live"
        and payload.get("news_fallback_used") is not True
        and _clean(payload.get("collection_status"))
        in {"LIVE_HEALTHY_WITH_ARTICLES", "LIVE_HEALTHY_NO_ELIGIBLE_ARTICLES"}
    ):
        articles = payload.get("news_censor_display_articles")
        validated_brief = True
    else:
        return (), empty_audit
    if not isinstance(articles, list):
        return (), empty_audit
    return select_teams_push_candidates_with_audit(
        articles,
        max_articles=max_articles,
        require_validated_fields=validated_brief,
        preference_runtime=preference_runtime,
        memory_batch_cap=memory_batch_cap,
    )


def select_teams_push_from_artifact(
    payload: object,
    *,
    max_articles: int | None = MAX_TEAMS_ARTICLES,
    preference_runtime: (
        "editorial_preference_runtime.EditorialPreferenceRuntime | None"
    ) = None,
    memory_batch_cap: int | None = None,
) -> tuple[TeamsPushCandidate, ...]:
    """Compatibility wrapper over :func:`select_teams_push_from_artifact_with_audit`."""
    selected, _audit = select_teams_push_from_artifact_with_audit(
        payload,
        max_articles=max_articles,
        preference_runtime=preference_runtime,
        memory_batch_cap=memory_batch_cap,
    )
    return selected


def publisher_delivery_priority(article: object) -> tuple[int, int]:
    """Canonical delivery-priority sort key: (tier rank, locked publisher rank).

    Tier order is the shared contract in :mod:`app.source_priority` —
    primary_10 → secondary_3 → official_institution → specialist →
    trusted_other → neutral → low → excluded."""
    tier = source_priority.publisher_delivery_tier(
        _clean(_value(article, "source") or _value(article, "display_source")),
        publisher_direct.publisher_url(article),
    )
    return int(tier["tier_rank"]), int(tier["publisher_rank"])


def _distinct_event_rank_key(item: "TeamsPushCandidate") -> tuple:
    """Distinct-event ranking: importance tier first, then publisher priority,
    then 현대건설 direct impact, score, and recency (§7)."""
    return (
        IMPORTANCE_RANK.get(item.importance.level, 9),
        *publisher_delivery_priority(item.article),
        -int(item.importance.hdec_direct),
        -(item.importance.score if item.importance.score is not None else -1.0),
        -_published_sort_value(item.article),
    )


def _event_representative_key(item: "TeamsPushCandidate") -> tuple:
    """Same-event representative choice: locked publisher tier first (primary
    ten, then secondary three, then official/specialist/trusted), then
    importance, 현대건설 direct impact, score, and recency (§7)."""
    return (
        *publisher_delivery_priority(item.article),
        IMPORTANCE_RANK.get(item.importance.level, 9),
        -int(item.importance.hdec_direct),
        -(item.importance.score if item.importance.score is not None else -1.0),
        -_published_sort_value(item.article),
    )


def collapse_event_duplicates(
    candidates: Sequence["TeamsPushCandidate"],
) -> tuple[tuple["TeamsPushCandidate", ...], tuple["TeamsPushCandidate", ...]]:
    """Keep one representative per event cluster; return (kept, dropped)."""
    representatives: dict[str, TeamsPushCandidate] = {}
    order: list[str] = []
    dropped: list[TeamsPushCandidate] = []
    for index, candidate in enumerate(candidates):
        key = candidate.cluster_key or f"__solo__:{index}"
        existing = representatives.get(key)
        if existing is None:
            representatives[key] = candidate
            order.append(key)
        elif _event_representative_key(candidate) < _event_representative_key(existing):
            dropped.append(existing)
            representatives[key] = candidate
        else:
            dropped.append(candidate)
    return (
        tuple(representatives[key] for key in order),
        tuple(dropped),
    )


# ---------------------------------------------------------------------------
# D7-AK-6E R4-R9A — Teams major-media-first source gate.
#
# The gate runs strictly AFTER every existing hard gate (required fields,
# publisher-direct safety, AI centrality, hard exclusions, executive
# relevance, importance, event dedup, the accepted ledger): it partitions
# already-eligible unsent candidates and can therefore never rescue a
# rejected article. It applies to the Teams push surface only — Daily,
# Weekly, News Censor, operator review, Report evidence, and editorial
# memory never consume it.

# Title-only filler screen for the exceptional specialist fallback lane
# (rules §6 condition 7). Stock/theme, promo/review, and recruit/book
# content is already excluded upstream by :func:`classify_ai_topic`; this
# adds ordinary-earnings, award, event-publicity, and press-release-filler
# markers. Judged on the title only, so aggregate-snippet noise cannot flip
# the decision. "수상태양광" (floating solar) is exempt from the award rule.
_SPECIALIST_FALLBACK_FILLER_TERMS = (
    "실적 발표", "영업이익", "순이익", "분기 실적", "어닝", "earnings",
    "수상", "시상", "어워드", "award", "기념식", "축하",
    "개최", "참가", "참석", "부스", "전시회", "박람회", "세미나", "웨비나",
    "포럼", "컨퍼런스",
    "보도자료", "press release", "후원", "협찬",
)


def _specialist_fallback_filler_reason(article: object) -> str:
    title = f" {_lower(_value(article, 'title'))} "
    if "수상태양광" in title:
        title = title.replace("수상태양광", " ")
    hits = _has(title, _SPECIALIST_FALLBACK_FILLER_TERMS)
    return f"specialist_filler:{hits[0]}" if hits else ""


@dataclass(frozen=True)
class SourceGateDecision:
    """Teams-only source-gate class for one already-eligible candidate."""

    gate_class: str
    tier: str
    tier_rank: int
    publisher_rank: int
    immediate: bool
    fallback_blocked: bool = False
    reason: str = ""


@dataclass(frozen=True)
class HoldbackEvaluation:
    """Deterministic §6 fallback evaluation for one held specialist."""

    first_seen_at: str
    age_minutes: float
    holdback_expired: bool
    importance_top: bool
    material_relevance: bool
    filler_reason: str
    same_event_major_available: bool
    replaced_by_major_media: str
    fallback_eligible: bool
    holdback_reason: str


@dataclass(frozen=True)
class GatedCandidate:
    candidate: TeamsPushCandidate
    gate: SourceGateDecision
    holdback: HoldbackEvaluation | None = None
    selection_mode: str = ""


@dataclass(frozen=True)
class SourceGateBatchResult:
    """One deterministic gate application over the ledger-filtered batch."""

    selected: tuple[GatedCandidate, ...]
    immediate: tuple[GatedCandidate, ...]
    immediate_selected: tuple[GatedCandidate, ...]
    deferred_major: tuple[GatedCandidate, ...]
    held: tuple[GatedCandidate, ...]
    rejected: tuple[GatedCandidate, ...]
    fallback_selected: tuple[GatedCandidate, ...]
    holdback_observations: tuple[Mapping[str, Any], ...]
    now_iso_value: str
    audit: dict[str, int]


def evaluate_source_gate(candidate: TeamsPushCandidate) -> SourceGateDecision:
    """Classify one candidate into its Teams source-gate class.

    Publisher-direct safety and Teams editorial eligibility stay separate: a
    safe direct link is not automatically Teams-send eligible. Promotion for
    an official institution is decided by the existing independently proven
    material-event policy (:mod:`app.public_institution_routing`) — this gate
    only consumes that verdict and never re-derives it.
    """
    article = candidate.article
    source = _clean(
        _value(article, "source") or _value(article, "display_source")
    )
    policy = source_priority.teams_delivery_source_policy(
        source, publisher_direct.publisher_url(article)
    )
    tier = str(policy["tier"])
    tier_rank = int(policy["tier_rank"])
    publisher_rank = int(policy["publisher_rank"])
    # R4-R11: the explicit never_automatic pin outranks every other gate
    # class including promoted-official — a spoofed institutional lane or
    # label combined with an excluded publisher's URL must never become an
    # immediate Teams card.
    if policy.get("explicit_never_automatic"):
        return SourceGateDecision(
            gate_class=SOURCE_GATE_NEVER_AUTOMATIC,
            tier=tier,
            tier_rank=tier_rank,
            publisher_rank=publisher_rank,
            immediate=False,
            reason="explicit_never_automatic_publisher",
        )
    if (
        candidate.editorial_lane == public_institution_routing.LANE_PUBLIC
        and candidate.teams_alert_eligible
    ):
        return SourceGateDecision(
            gate_class=SOURCE_GATE_PROMOTED_OFFICIAL,
            tier=tier,
            tier_rank=tier_rank,
            publisher_rank=publisher_rank,
            immediate=True,
            reason="promoted_official_institution",
        )
    lane = str(policy["teams_lane"])
    if lane == source_priority.TEAMS_LANE_IMMEDIATE_MAJOR:
        gate_class = {
            "primary_10": SOURCE_GATE_PRIMARY_10,
            "secondary_3": SOURCE_GATE_SECONDARY_3,
            "major_secondary": SOURCE_GATE_MAJOR_SECONDARY,
        }.get(tier, SOURCE_GATE_NEVER_AUTOMATIC)
        if gate_class == SOURCE_GATE_NEVER_AUTOMATIC:
            return SourceGateDecision(
                gate_class=gate_class,
                tier=tier,
                tier_rank=tier_rank,
                publisher_rank=publisher_rank,
                immediate=False,
                reason="unrecognized_immediate_tier",
            )
        return SourceGateDecision(
            gate_class=gate_class,
            tier=tier,
            tier_rank=tier_rank,
            publisher_rank=publisher_rank,
            immediate=True,
            reason=f"immediate_{tier}",
        )
    if lane == source_priority.TEAMS_LANE_SPECIALIST_HOLDBACK:
        fallback_blocked = bool(policy["fallback_blocked"])
        return SourceGateDecision(
            gate_class=SOURCE_GATE_SPECIALIST_HOLDBACK,
            tier=tier,
            tier_rank=tier_rank,
            publisher_rank=publisher_rank,
            immediate=False,
            fallback_blocked=fallback_blocked,
            reason=(
                "fallback_blocked_publisher"
                if fallback_blocked
                else "specialist_holdback"
            ),
        )
    return SourceGateDecision(
        gate_class=SOURCE_GATE_NEVER_AUTOMATIC,
        tier=tier,
        tier_rank=tier_rank,
        publisher_rank=publisher_rank,
        immediate=False,
        reason=(
            "explicit_never_automatic_publisher"
            if policy.get("explicit_never_automatic")
            else "official_institution_not_promoted"
            if lane == source_priority.TEAMS_LANE_OFFICIAL_INSTITUTION
            else "source_tier_not_eligible"
        ),
    )


def apply_major_media_first_gate(
    accepted: Sequence[TeamsPushCandidate],
    *,
    state: Mapping[str, Any] | None,
    run_cap: int,
    now_iso_value: str = "",
    holdback_minutes: int = TEAMS_SPECIALIST_HOLDBACK_MINUTES,
    max_specialist_per_batch: int = TEAMS_SPECIALIST_MAX_PER_BATCH,
) -> SourceGateBatchResult:
    """Partition the ledger-filtered ranked batch by the Teams source gate.

    Pure transform: reads held-record state, never writes it. The caller
    (production sender) applies ``holdback_observations`` through
    ``app.teams_push_state`` in send mode only, so a dry run changes nothing.

    Selection: immediate-class candidates (locked primary ten, secondary
    three, promoted official institutions) fill the batch in existing rank
    order; a specialist/trusted-other candidate is selected only through the
    §6 exceptional fallback (holdback expired · unique TOP event · direct
    HDEC or independently proven material strategic relevance · no filler ·
    publisher not fallback-blocked), capped at
    :data:`TEAMS_SPECIALIST_MAX_PER_BATCH` and never displacing an available
    major candidate. Ordinary specialist supply never fills unused capacity.
    """
    from app import teams_push_state as push_state

    now_value = _clean(now_iso_value) or push_state.now_iso()
    state_map: Mapping[str, Any] = state if isinstance(state, Mapping) else {}
    ledger_clusters = {
        _clean(key)
        for key in (state_map.get("cluster_keys") or {})
        if _clean(key)
    }

    immediate: list[GatedCandidate] = []
    holdback_lane: list[GatedCandidate] = []
    rejected: list[GatedCandidate] = []
    stock_gate_rejected = 0
    for candidate in accepted:
        # R4-R9B §4 — defensive re-check at the gate boundary.  Policy-built
        # candidates can never be stock-ineligible (the policy rejects them
        # first), so this only guards directly-constructed batches: a
        # dominant market article must not become immediate, held, or
        # fallback-eligible regardless of age, importance, or publisher.
        stock_decision = evaluate_stock_market_gate(candidate.article)
        if not stock_decision.eligible:
            stock_gate_rejected += 1
            rejected.append(
                GatedCandidate(
                    candidate=candidate,
                    gate=SourceGateDecision(
                        gate_class=SOURCE_GATE_NEVER_AUTOMATIC,
                        tier="",
                        tier_rank=99,
                        publisher_rank=99,
                        immediate=False,
                        fallback_blocked=True,
                        reason="stock_market_hard_excluded",
                    ),
                )
            )
            continue
        gate = evaluate_source_gate(candidate)
        item = GatedCandidate(candidate=candidate, gate=gate)
        if gate.immediate:
            immediate.append(item)
        elif gate.gate_class == SOURCE_GATE_SPECIALIST_HOLDBACK:
            holdback_lane.append(item)
        else:
            rejected.append(item)

    cap = max(0, int(run_cap))
    urgent = [
        item for item in immediate
        if item.candidate.importance.level == IMPORTANCE_TOP
        or item.candidate.importance.hdec_direct
    ]
    normal = [item for item in immediate if item not in urgent]
    last_normal_send_at = _clean(state_map.get("last_normal_send_at"))
    normal_pacing_age = (
        push_state.minutes_between(last_normal_send_at, now_value)
        if last_normal_send_at
        else float(TEAMS_NORMAL_PACING_MINUTES)
    )
    normal_window_open = (
        not last_normal_send_at
        or normal_pacing_age >= float(TEAMS_NORMAL_PACING_MINUTES)
    )
    chosen: list[GatedCandidate] = urgent[:cap]
    if normal_window_open and normal and len(chosen) < cap:
        chosen.append(normal[0])
    chosen_ids = {id(item) for item in chosen}
    immediate_selected = tuple(
        replace(item, selection_mode=SELECTION_MODE_IMMEDIATE)
        for item in immediate
        if id(item) in chosen_ids
    )
    deferred_major = tuple(
        item for item in immediate if id(item) not in chosen_ids
    )
    selected_clusters = {
        _clean(item.candidate.cluster_key)
        for item in immediate_selected
        if _clean(item.candidate.cluster_key)
    }

    evaluated: list[GatedCandidate] = []
    for item in holdback_lane:
        candidate = item.candidate
        prior = push_state.get_held_record(state_map, candidate.article) or {}
        first_seen = _clean(prior.get("first_seen_at")) or now_value
        age_minutes = max(
            0.0, push_state.minutes_between(first_seen, now_value)
        )
        holdback_expired = age_minutes >= float(holdback_minutes)
        importance_top = candidate.importance.level == IMPORTANCE_TOP
        material_relevance = bool(
            candidate.importance.hdec_direct
        ) or _has_strong_ai_strategic_override(
            f" {_core_article_text(candidate.article)} "
        )
        filler_reason = _specialist_fallback_filler_reason(candidate.article)
        replaced_by = _clean(prior.get("replaced_by_major_media"))
        cluster = _clean(candidate.cluster_key)
        same_event_major_available = bool(
            cluster
            and (cluster in selected_clusters or cluster in ledger_clusters)
        ) or bool(replaced_by)
        if item.gate.fallback_blocked:
            block_reason = "fallback_blocked_publisher"
        elif same_event_major_available:
            block_reason = "same_event_major_available"
        elif not holdback_expired:
            block_reason = "holdback_active"
        elif not importance_top:
            block_reason = "importance_not_top"
        elif not material_relevance:
            block_reason = "no_material_relevance"
        elif filler_reason:
            block_reason = filler_reason
        else:
            # R4-R9D — even a holdback-expired, TOP, directly-relevant specialist
            # article is refused automatic selection. The diagnostic sub-reasons
            # above still populate when they apply (operator/audit metadata); this
            # branch is the residual "would have been eligible under the old
            # policy" case, which the strict source gate now blocks outright.
            block_reason = "specialist_automatic_fallback_removed"
        evaluated.append(
            replace(
                item,
                holdback=HoldbackEvaluation(
                    first_seen_at=first_seen,
                    age_minutes=round(age_minutes, 1),
                    holdback_expired=holdback_expired,
                    importance_top=importance_top,
                    material_relevance=material_relevance,
                    filler_reason=filler_reason,
                    same_event_major_available=same_event_major_available,
                    replaced_by_major_media=replaced_by,
                    fallback_eligible=not block_reason,
                    holdback_reason=block_reason or "fallback_eligible",
                ),
            )
        )

    # R4-R9D — automatic specialist fallback removed. No specialist/trusted-other
    # article is ever selected for automatic Teams delivery, regardless of
    # holdback age, TOP importance, direct Hyundai E&C relevance, or any
    # caller-supplied ``max_specialist_per_batch`` override (retained only for
    # signature back-compatibility). Every holdback-lane row stays held —
    # available as supporting evidence / Daily/Weekly review, never sent or
    # accepted. The system prefers zero delivery over a specialist-only card.
    fallback_room = 0
    fallback_selected: list[GatedCandidate] = []
    held: list[GatedCandidate] = []
    for item in evaluated:
        if (
            len(fallback_selected) < fallback_room
            and item.holdback is not None
            and item.holdback.fallback_eligible
        ):
            fallback_selected.append(
                replace(item, selection_mode=SELECTION_MODE_FALLBACK)
            )
        else:
            held.append(item)

    holdback_observations = tuple(
        {
            "article": item.candidate.article,
            "cluster_key": item.candidate.cluster_key,
            "source": _clean(
                _value(item.candidate.article, "source")
                or _value(item.candidate.article, "display_source")
            ),
            "source_tier": item.gate.tier,
            "holdback_reason": (
                item.holdback.holdback_reason if item.holdback else ""
            ),
            "fallback_eligible": bool(
                item.holdback and item.holdback.fallback_eligible
            ),
        }
        for item in evaluated
    )

    selected = tuple(immediate_selected) + tuple(fallback_selected)
    specialist_rows_automatic_rejected = sum(
        bool(item.holdback and not item.holdback.same_event_major_available)
        for item in held
    )
    # R4-R10 — the "specialist or neutral" rejection family the strict source
    # gate refuses to auto-send: rejected rows whose publisher tier is
    # neutral/low, or an explicitly excluded publisher (e.g. S저널, traced from
    # a real 2026-08-05 production auto-send). Stock hard-exclusion
    # (stock_market_hard_excluded) and un-promoted official rejections are
    # counted by their own dedicated counters, not here.
    never_automatic_rejected_rows = sum(
        item.gate.gate_class == SOURCE_GATE_NEVER_AUTOMATIC
        and item.gate.reason in (
            "source_tier_not_eligible",
            "explicit_never_automatic_publisher",
        )
        for item in rejected
    )
    specialist_or_neutral_rejected_rows = (
        specialist_rows_automatic_rejected + never_automatic_rejected_rows
    )
    audit = {
        "teams_immediate_major_rows": len(immediate),
        "teams_specialist_held_rows": len(held),
        "teams_specialist_holdback_expired_rows": sum(
            bool(item.holdback and item.holdback.holdback_expired)
            for item in evaluated
        ),
        "teams_specialist_fallback_eligible_rows": sum(
            bool(item.holdback and item.holdback.fallback_eligible)
            for item in evaluated
        ),
        "teams_specialist_selected_rows": len(fallback_selected),
        # R4-R9D strict-source-gate audit counters. specialist_rows_selected is
        # the authoritative invariant and is always 0 (automatic specialist
        # fallback removed). "supporting_evidence" = held specialist rows that
        # back a same-event major/official card; "automatic_rejected" = the
        # specialist-only rows the gate refuses to auto-send (prefer zero).
        "specialist_rows_seen": len(holdback_lane),
        "specialist_rows_supporting_evidence": sum(
            bool(item.holdback and item.holdback.same_event_major_available)
            for item in held
        ),
        "specialist_rows_automatic_rejected": specialist_rows_automatic_rejected,
        "specialist_rows_selected": len(fallback_selected),
        "specialist_automatic_fallback_removed": True,
        "source_gate_rejected_rows": len(rejected),
        # R4-R10 — neutral/low + explicitly-excluded publisher rejections, and
        # the combined "specialist or neutral" rejection total.
        "never_automatic_rejected_rows": never_automatic_rejected_rows,
        "specialist_or_neutral_rejected_rows": specialist_or_neutral_rejected_rows,
        "selected_primary_10_rows": sum(
            item.gate.gate_class == SOURCE_GATE_PRIMARY_10
            for item in selected
        ),
        "selected_secondary_3_rows": sum(
            item.gate.gate_class == SOURCE_GATE_SECONDARY_3
            for item in selected
        ),
        "selected_major_secondary_rows": sum(
            item.gate.gate_class == SOURCE_GATE_MAJOR_SECONDARY
            for item in selected
        ),
        "selected_promoted_official_rows": sum(
            item.gate.gate_class == SOURCE_GATE_PROMOTED_OFFICIAL
            for item in selected
        ),
        "selected_specialist_rows": sum(
            item.gate.gate_class == SOURCE_GATE_SPECIALIST_HOLDBACK
            for item in selected
        ),
        # R4-R9B §4 — normally zero; non-zero means a stock-dominant article
        # reached the gate boundary directly and was force-rejected there.
        "stock_market_gate_rejected_rows": stock_gate_rejected,
        "normal_pacing_window_open": int(normal_window_open),
        "normal_pacing_age_minutes": int(max(0.0, normal_pacing_age)),
        "normal_rows_selected": sum(
            item.candidate.importance.level == IMPORTANCE_IMPORTANT
            for item in selected
        ),
        "normal_rows_deferred_by_pacing": sum(
            item.candidate.importance.level == IMPORTANCE_IMPORTANT
            for item in deferred_major
        ),
        "urgent_rows_selected": sum(
            item.candidate.importance.level == IMPORTANCE_TOP
            or item.candidate.importance.hdec_direct
            for item in selected
        ),
    }
    return SourceGateBatchResult(
        selected=selected,
        immediate=tuple(immediate),
        immediate_selected=immediate_selected,
        deferred_major=deferred_major,
        held=tuple(held),
        rejected=tuple(rejected),
        fallback_selected=tuple(fallback_selected),
        holdback_observations=holdback_observations,
        now_iso_value=now_value,
        audit=audit,
    )


def _apply_editorial_memory_stage(
    ranked: Sequence["TeamsPushCandidate"],
    *,
    batch_cap: int | None,
    runtime: "editorial_preference_runtime.EditorialPreferenceRuntime | None" = None,
) -> list["TeamsPushCandidate"]:
    """R4-R7 §4 stage 8 — human-memory preference adjustment.

    Runs strictly after publisher-direct/safety, AI-centrality, hard
    exclusion, executive relevance, importance, event deduplication, and
    publisher-priority ranking, over already-eligible candidates only — so
    memory can never resurrect a rejected article, bypass the importance
    minimum, or unhide previously sent state. While the committed profile is
    inactive (production today) the deterministic order is returned
    unchanged and each candidate carries its audit-only shadow decision:
    would-be rank and whether the capped batch membership would change. An
    explicitly activated profile (test fixture / preview) applies the
    bounded reorder to equal-importance peers only."""
    if not ranked:
        return list(ranked)
    if runtime is None:
        runtime = editorial_preference_runtime.default_runtime()
    decisions = [
        runtime.decide(editorial_preference_runtime.PRODUCT_TEAMS, item.article)
        for item in ranked
    ]
    order = editorial_preference_runtime.memory_adjusted_order(
        len(ranked),
        [decision.preference_adjustment for decision in decisions],
        group_of=lambda index: IMPORTANCE_RANK.get(
            ranked[index].importance.level, 9
        ),
    )
    member_count = len(ranked) if batch_cap is None else min(batch_cap, len(ranked))
    baseline_members = set(range(member_count))
    adjusted_members = set(order[:member_count])
    rank_after = {index: position + 1 for position, index in enumerate(order)}
    final_indices = order if runtime.memory_active else list(range(len(ranked)))
    return [
        replace(
            ranked[index],
            editorial_memory_profile=decisions[index].profile_version,
            editorial_memory_active=decisions[index].memory_active,
            approved_precedent_ids=decisions[index].approved_precedent_ids,
            rejected_precedent_ids=decisions[index].rejected_precedent_ids,
            near_miss_precedent_ids=(
                decisions[index].near_miss_precedent_ids
            ),
            silver_precedent_ids=decisions[index].silver_precedent_ids,
            memory_preference_score=decisions[index].preference_score,
            memory_preference_adjustment=(
                decisions[index].preference_adjustment
            ),
            memory_rank_before=index + 1,
            memory_rank_after=rank_after[index],
            memory_changed_selection=(
                (index in baseline_members) != (index in adjusted_members)
            ),
        )
        for index in final_indices
    ]


def select_teams_push_candidates_with_audit(
    articles: Iterable[Mapping[str, Any]],
    *,
    max_articles: int | None = MAX_TEAMS_ARTICLES,
    require_validated_fields: bool = False,
    preference_runtime: (
        "editorial_preference_runtime.EditorialPreferenceRuntime | None"
    ) = None,
    memory_batch_cap: int | None = None,
) -> tuple[tuple[TeamsPushCandidate, ...], dict[str, int | bool | str]]:
    """Filter, collapse same-event duplicates, rank, and cap Teams candidates.

    Policy eligibility is unchanged. Same-event multi-publisher clusters keep
    exactly one representative (locked primary ten first, secondary three next,
    then official/specialist/trusted). Distinct events rank importance-first,
    then publisher priority, 현대건설 direct impact, score, and recency. The
    production sender applies its own 0-5 batch cap only after accepted-ledger
    filtering, so lower-priority rows remain deferred, never lost."""
    from app.teams_push_state import derive_event_cluster_key, material_signature

    candidates: list[TeamsPushCandidate] = []
    public_routes: list[
        public_institution_routing.PublicInstitutionRoutingDecision
    ] = []
    # R4-R9B §6 — stock-market gate counters over every policy-reached row.
    stock_dominant_rows = 0
    stock_hard_rejected_rows = 0
    stock_exception_rows = 0
    stock_fallback_blocked_rows = 0
    for article in articles:
        if not isinstance(article, Mapping):
            continue
        public_routes.append(public_institution_routing.classify(article))
        evaluation = evaluate_teams_push_policy(
            article,
            require_validated_fields=require_validated_fields,
        )
        stock_gate = evaluation.stock_market
        if stock_gate is not None:
            stock_dominant_rows += stock_gate.dominant
            stock_exception_rows += bool(stock_gate.exception_reason)
            if stock_gate.dominant and not stock_gate.hdec_material_event:
                stock_hard_rejected_rows += 1
                # A hard-rejected row can never re-enter through the §6
                # specialist fallback; count the rows whose source lane
                # would otherwise have been the holdback/fallback lane.
                policy = source_priority.teams_delivery_source_policy(
                    _clean(
                        _value(article, "source")
                        or _value(article, "display_source")
                    ),
                    publisher_direct.publisher_url(article),
                )
                if (
                    str(policy["teams_lane"])
                    == source_priority.TEAMS_LANE_SPECIALIST_HOLDBACK
                ):
                    stock_fallback_blocked_rows += 1
        if not evaluation.eligible:
            continue
        candidates.append(
            TeamsPushCandidate(
                article=evaluation.article,
                topic=evaluation.topic,
                importance=evaluation.importance,
                cluster_key=derive_event_cluster_key(
                    article, evaluation.topic.topic_key
                ),
                material_signature=material_signature(article),
                is_update=_lower(_value(article, "change_type")) == "material_content_update",
                delivery_category=evaluation.delivery_category,
                source_class=evaluation.public_routing.source_class,
                editorial_lane=evaluation.public_routing.editorial_lane,
                public_institution_type=(
                    evaluation.public_routing.public_institution_type
                ),
                official_source_name=evaluation.public_routing.official_source_name,
                default_surface=evaluation.public_routing.default_surface,
                main_surface_eligible=(
                    evaluation.public_routing.main_surface_eligible
                ),
                teams_alert_eligible=(
                    evaluation.public_routing.teams_alert_eligible
                ),
                tni_brief_eligible=evaluation.public_routing.tni_brief_eligible,
                tni_report_topic_eligible=(
                    evaluation.public_routing.tni_report_topic_eligible
                ),
                promotion_reason=evaluation.public_routing.promotion_reason,
                final_category=evaluation.public_routing.final_category,
            )
        )

    representatives, event_duplicates = collapse_event_duplicates(candidates)
    ranked = sorted(representatives, key=_distinct_event_rank_key)
    effective_cap = (
        None
        if max_articles is None
        else max(0, min(int(max_articles), MAX_TEAMS_ARTICLES))
    )
    audit_cap = memory_batch_cap
    if audit_cap is None:
        audit_cap = effective_cap
    if audit_cap is None:
        audit_cap = MAX_TEAMS_ARTICLES
    audit_cap = max(0, min(int(audit_cap), MAX_TEAMS_ARTICLES))
    ranked = _apply_editorial_memory_stage(
        ranked,
        batch_cap=audit_cap,
        runtime=preference_runtime,
    )
    audit = {
        "policy_eligible": len(candidates),
        "event_duplicates": len(event_duplicates),
        "distinct_events": len(ranked),
        "editorial_memory_invoked": bool(ranked),
        "editorial_memory_profile": (
            ranked[0].editorial_memory_profile if ranked else ""
        ),
        "editorial_memory_active": (
            ranked[0].editorial_memory_active if ranked else False
        ),
        "public_institution_lane_count": sum(
            route.is_public_lane for route in public_routes
        ),
        "promoted_public_candidate_count": sum(
            route.is_public_lane and route.main_surface_eligible
            for route in public_routes
        ),
        "non_promoted_public_candidate_count": sum(
            route.is_public_lane and not route.main_surface_eligible
            for route in public_routes
        ),
        "teams_public_candidate_count": sum(
            candidate.editorial_lane == public_institution_routing.LANE_PUBLIC
            for candidate in ranked
        ),
        # R4-R9B §6 — reconciliation contract: dominant_rows equals
        # hard_rejected_rows plus the dominant subset of exception rows;
        # fallback_blocked_rows is the specialist-lane subset of the hard
        # rejections (they may never re-enter through the §6 fallback).
        "stock_market_dominant_rows": stock_dominant_rows,
        "stock_market_hard_rejected_rows": stock_hard_rejected_rows,
        "stock_market_hdec_exception_rows": stock_exception_rows,
        "stock_market_fallback_blocked_rows": stock_fallback_blocked_rows,
    }
    if effective_cap is None:
        return tuple(ranked), audit
    return tuple(ranked[:effective_cap]), audit


def select_teams_push_candidates(
    articles: Iterable[Mapping[str, Any]],
    *,
    max_articles: int | None = MAX_TEAMS_ARTICLES,
    require_validated_fields: bool = False,
    preference_runtime: (
        "editorial_preference_runtime.EditorialPreferenceRuntime | None"
    ) = None,
    memory_batch_cap: int | None = None,
) -> tuple[TeamsPushCandidate, ...]:
    """Compatibility wrapper over :func:`select_teams_push_candidates_with_audit`."""
    selected, _audit = select_teams_push_candidates_with_audit(
        articles,
        max_articles=max_articles,
        require_validated_fields=require_validated_fields,
        preference_runtime=preference_runtime,
        memory_batch_cap=memory_batch_cap,
    )
    return selected


def _fmt_kst(value: object) -> str:
    text = _clean(value)
    if not text:
        return ""
    if len(text) >= 16 and text[4] == "-" and text[10] in (" ", "T"):
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return text[:16].replace("T", " ")
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(KST).strftime("%Y-%m-%d %H:%M")
    return text


def _safe_http(value: object) -> str:
    text = _clean(value)
    return text if text.lower().startswith(("https://", "http://")) else ""


def _text_block(text: str, **kwargs: Any) -> dict[str, Any]:
    block: dict[str, Any] = {"type": "TextBlock", "text": text, "wrap": True}
    block.update(kwargs)
    return block


def _article_field(article: object, *keys: str) -> str:
    for key in keys:
        value = _clean(_value(article, key))
        if value:
            return value
    after = _mapping(article, "after")
    for key in keys:
        value = _clean(after.get(key))
        if value:
            return value
    return ""


def _compact_summary(value: object, *, max_chars: int = 320) -> str:
    """Keep the email summary brief enough for roughly two or three display lines."""
    text = _clean(value)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def build_teams_article_card(
    alert: object,
    article: object,
    *,
    topic: TopicDecision,
    importance: ImportanceDecision,
    detected_at: str = "",
    is_update: bool = False,
    delivery_category: str = "",
) -> dict[str, Any]:
    """Build exactly one Teams Workflows Adaptive Card message for one article."""
    if not topic.eligible or not importance.sendable:
        raise ValueError("non-sendable article cannot be rendered as a Teams push card")
    category = _clean(delivery_category) or ai_centrality.delivery_category(article)[0]
    if not category:
        raise ValueError(
            "article has no evidenced delivery category in its title/lead map"
        )

    title = _article_field(article, "title") or "제목 없음"
    summary = _article_field(article, "summary", "snippet") or "핵심 요약이 제공되지 않았습니다."
    hdec_impact = _article_field(
        article, "hdec_relevance", "radarReason", "whyImportant", "why_it_matters"
    ) or "현대건설 영향은 원문과 대시보드에서 추가 확인이 필요합니다."
    source = _article_field(article, "source", "display_source") or "출처 미상"
    published = _fmt_kst(_value(article, "published_at") or _value(article, "published_kst")) or "시각 미상"
    detected = _fmt_kst(detected_at or _value(alert, "generated_at") or _value(alert, "generated_kst")) or "시각 미상"
    authority = publisher_direct.assess_delivery_eligibility(
        article,
        relevance_qualified=True,
    )
    if not authority.eligible:
        raise ValueError("publisher-direct article authority is required")
    article_url = authority.publisher_url
    dashboard_url = CANONICAL_DASHBOARD_URL

    title_prefix = "[업데이트] " if is_update else ""
    importance_color = "Attention" if importance.level == IMPORTANCE_TOP else "Warning"
    body: list[dict[str, Any]] = [
        _text_block(importance.label, weight="Bolder", color=importance_color, size="Medium"),
        _text_block(category, isSubtle=True, spacing="None"),
        _text_block(f"{title_prefix}{title}", weight="Bolder", size="Large", spacing="Medium"),
        _text_block("핵심 요약", weight="Bolder", spacing="Medium"),
        _text_block(summary, spacing="Small"),
        _text_block("현대건설 영향", weight="Bolder", spacing="Medium"),
        _text_block(hdec_impact, spacing="Small"),
        {
            "type": "FactSet",
            "spacing": "Medium",
            "facts": [
                {"title": "출처", "value": source},
                {"title": "게시시각", "value": f"{published} KST" if published != "시각 미상" else published},
                {"title": "감지시각", "value": f"{detected} KST" if detected != "시각 미상" else detected},
            ],
        },
    ]

    actions: list[dict[str, str]] = []
    if article_url:
        actions.append({
            "type": "Action.OpenUrl",
            "title": "기사 원문 보기",
            "url": article_url,
        })
    if dashboard_url:
        actions.append({"type": "Action.OpenUrl", "title": "전체 뉴스 대시보드 보기", "url": dashboard_url})

    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": body,
                    "actions": actions,
                },
            }
        ],
    }


def build_candidate_card(alert: object, candidate: TeamsPushCandidate, *, detected_at: str = "") -> dict[str, Any]:
    return build_teams_article_card(
        alert,
        candidate.article,
        topic=candidate.topic,
        importance=candidate.importance,
        detected_at=detected_at,
        is_update=candidate.is_update,
        delivery_category=candidate.delivery_category,
    )


def _render_article_email_legacy(
    alert: object,
    candidate: TeamsPushCandidate,
    *,
    detected_at: str = "",
) -> tuple[str, str, str]:
    """Render one article as ``(subject, text_body, html_body)`` for the Teams channel email.

    This is the compact, direct-link-first message body for the email_channel production
    transport (Gmail SMTP → Teams channel email). Callers send one email per article and
    never merge a digest. The body uses no external CSS or JavaScript. A collected
    representative image is optional; no placeholder image is invented."""
    if not candidate.topic.eligible or not candidate.importance.sendable:
        raise ValueError("non-sendable article cannot be rendered as a Teams push email")

    article = candidate.article
    importance = candidate.importance
    topic = candidate.topic

    title = _article_field(article, "title") or "제목 없음"
    summary = _compact_summary(
        _article_field(article, "summary", "snippet")
        or "핵심 요약이 제공되지 않았습니다."
    )
    source = _article_field(article, "source", "display_source") or "출처 미상"
    published = _fmt_kst(_value(article, "published_at") or _value(article, "published_kst")) or "시각 미상"
    authority = publisher_direct.assess_delivery_eligibility(
        article,
        relevance_qualified=True,
    )
    article_url = authority.publisher_url
    if not authority.eligible:
        raise ValueError("publisher-direct article authority is required for a Teams push email")
    image_url = _safe_http(
        _article_field(
            article,
            "image_url",
            "representative_image_url",
            "thumbnail_url",
            "og_image_url",
        )
    )
    dashboard_url = _safe_http(_value(alert, "dashboard_url"))
    report_url = _safe_http(_value(alert, "report_url"))

    importance_label = importance.label or IMPORTANCE_LABELS.get(importance.level, "중요")
    title_prefix = "[업데이트] " if candidate.is_update else ""
    published_line = f"{published} KST" if published != "시각 미상" else published

    subject = f"[HDEC AI 레이더] {importance_label} · {title_prefix}{title}".strip()

    text_lines: list[str] = [
        f"[원문] {article_url}",
        "",
        f"{importance_label}" + (f" · {topic.topic_label}" if topic.topic_label else ""),
        "",
        f"{title_prefix}{title}",
        "",
        summary,
        "",
        f"{source} · {published_line}",
        "",
        "────────────────────",
        "보조 링크",
    ]
    for label, url in (
        ("대시보드 보기", dashboard_url),
        ("전체 리포트 보기", report_url),
    ):
        if url:
            if text_lines[-1] != "보조 링크":
                text_lines.append("")
            text_lines.append(f"- {label}: {url}")
    text_body = "\n".join(text_lines).rstrip() + "\n"

    def _p(text: str) -> str:
        return html.escape(text).replace("\n", "<br>")

    escaped_article_url = html.escape(article_url, quote=True)
    html_links = []
    if dashboard_url:
        html_links.append(
            f'<div style="margin:8px 0;"><a href="{html.escape(dashboard_url, quote=True)}">'
            "대시보드 보기</a></div>"
        )
    if report_url:
        html_links.append(
            f'<div style="margin:8px 0;"><a href="{html.escape(report_url, quote=True)}">'
            "전체 리포트 보기</a></div>"
        )
    links_html = "".join(html_links)
    image_html = (
        f'<a href="{escaped_article_url}" style="display:block;margin:16px 0;">'
        f'<img src="{html.escape(image_url, quote=True)}" alt="" '
        'style="display:block;max-width:100%;height:auto;border:0;"></a>'
        if image_url
        else ""
    )
    badge_color = "#b42318" if importance.level == IMPORTANCE_TOP else "#b54708"
    badge_background = "#fef3f2" if importance.level == IMPORTANCE_TOP else "#fffaeb"

    html_body = (
        "<div style=\"font-family:Segoe UI,Apple SD Gothic Neo,Malgun Gothic,sans-serif;"
        "max-width:640px;line-height:1.55;color:#101828;\">"
        + f'<p style="margin:0 0 14px;word-break:break-all;">'
        + '<strong>[원문]</strong> '
        + f'<a href="{escaped_article_url}">{escaped_article_url}</a></p>'
        + f'<span style="display:inline-block;font-size:12px;font-weight:600;color:{badge_color};'
        + f'background:{badge_background};border-radius:12px;padding:3px 8px;">'
        + f"{_p(importance_label)}</span>"
        + (
            f'<span style="font-size:12px;color:#667085;margin-left:8px;">'
            f"{_p(topic.topic_label)}</span>"
            if topic.topic_label
            else ""
        )
        + image_html
        + f'<h2 style="font-size:22px;line-height:1.35;margin:16px 0 12px;">'
        + f'<a href="{escaped_article_url}" style="color:#101828;text-decoration:none;">'
        + f"{_p(title_prefix + title)}</a></h2>"
        + f'<p style="margin:0 0 14px;max-height:4.65em;overflow:hidden;">{_p(summary)}</p>'
        + f'<p style="font-size:13px;color:#667085;margin:0;">'
        + f"{_p(source)} · {_p(published_line)}</p>"
        + (
            '<hr style="border:0;border-top:1px solid #e4e7ec;margin:22px 0 14px;">'
            '<p style="font-size:12px;color:#667085;margin:0 0 6px;">보조 링크</p>'
            + links_html
            if links_html
            else ""
        )
        + "</div>"
    )

    return subject, text_body, html_body


def render_article_email(
    alert: object,
    candidate: TeamsPushCandidate,
    *,
    detected_at: str = "",
) -> tuple[str, str, str]:
    """Render one concise, email-safe Teams article with two mandatory actions."""
    del alert, detected_at
    if not candidate.topic.eligible or not candidate.importance.sendable:
        raise ValueError("non-sendable article cannot be rendered as a Teams push email")

    article = candidate.article
    topic = candidate.topic
    importance = candidate.importance
    category = (
        _clean(candidate.delivery_category)
        or ai_centrality.delivery_category(article)[0]
    )
    if not category:
        raise ValueError(
            "article has no evidenced delivery category in its title/lead map"
        )
    title = _article_field(article, "title") or "제목 없음"
    summary = _compact_summary(
        _article_field(article, "summary", "snippet")
        or "핵심 요약이 제공되지 않았습니다."
    )
    why = _compact_summary(
        _article_field(
            article,
            "hdec_relevance",
            "radarReason",
            "whyImportant",
            "why_it_matters",
        )
        or importance.reason
    )
    source = _article_field(article, "source", "display_source") or "출처 미상"
    published = _fmt_kst(
        _value(article, "published_at") or _value(article, "published_kst")
    ) or "시각 미상"
    authority = publisher_direct.assess_delivery_eligibility(
        article,
        relevance_qualified=True,
    )
    if not authority.eligible:
        raise ValueError("publisher-direct article authority is required for a Teams push email")
    article_url = authority.publisher_url
    dashboard_url = CANONICAL_DASHBOARD_URL
    prefix = "[업데이트] " if candidate.is_update else ""
    importance_label = importance.label or IMPORTANCE_LABELS.get(importance.level, "중요")
    published_line = f"{published} KST" if published != "시각 미상" else published
    subject = f"[HDEC AI 레이더] {importance_label} · {prefix}{title}".strip()

    text_body = "\n".join((
        f"카테고리: {category}",
        f"제목: {prefix}{title}",
        f"요약: {summary}",
        f"왜 중요한가: {why}",
        f"발행: {source} · {published_line}",
        "",
        f"기사 원문 보기: {article_url}",
        f"전체 뉴스 대시보드 보기: {dashboard_url}",
    )) + "\n"

    def escaped(value: str) -> str:
        return html.escape(value).replace("\n", "<br>")

    origin_href = html.escape(article_url, quote=True)
    dashboard_href = html.escape(dashboard_url, quote=True)
    badge_color = "#b42318" if importance.level == IMPORTANCE_TOP else "#b54708"
    badge_background = "#fef3f2" if importance.level == IMPORTANCE_TOP else "#fffaeb"
    button_style = (
        "display:inline-block;padding:10px 14px;border-radius:6px;text-decoration:none;"
        "font-weight:700;margin:4px 8px 4px 0;"
    )
    html_body = (
        '<div style="font-family:Segoe UI,Apple SD Gothic Neo,Malgun Gothic,sans-serif;'
        'max-width:640px;line-height:1.55;color:#101828;">'
        f'<span style="display:inline-block;font-size:12px;font-weight:700;color:{badge_color};'
        f'background:{badge_background};border-radius:12px;padding:3px 8px;">'
        f'{escaped(importance_label)}</span>'
        f'<p style="font-size:13px;color:#667085;margin:12px 0 6px;">'
        f'<strong>카테고리</strong> {escaped(category)}</p>'
        f'<h2 style="font-size:22px;line-height:1.35;margin:8px 0 12px;">'
        f'{escaped(prefix + title)}</h2>'
        f'<p style="margin:0 0 14px;"><strong>요약</strong><br>{escaped(summary)}</p>'
        f'<p style="margin:0 0 14px;"><strong>왜 중요한가</strong><br>{escaped(why)}</p>'
        f'<p style="font-size:13px;color:#667085;margin:0 0 16px;">'
        f'{escaped(source)} · {escaped(published_line)}</p>'
        f'<a href="{origin_href}" style="{button_style}background:#0B2F4F;color:#fff;">'
        '기사 원문 보기</a>'
        f'<a href="{dashboard_href}" style="{button_style}background:#F0F4F7;color:#0B2F4F;'
        'border:1px solid #CCD6DE;">전체 뉴스 대시보드 보기</a>'
        '<p style="font-size:12px;color:#667085;margin:16px 0 0;word-break:break-all;">'
        f'기사 원문: <a href="{origin_href}">{origin_href}</a><br>'
        f'뉴스 대시보드: <a href="{dashboard_href}">{dashboard_href}</a></p>'
        '</div>'
    )
    return subject, text_body, html_body
