"""Watch-only dominant-subject semantic precision gate (R4-OPS-8).

The realtime Teams Watch needs a narrower answer than broad AI relevance:
"is the article's dominant subject a material AI industrial event?"  This
pure leaf rejects investor guidance, generic AI-caused earnings/tailwind
commentary, multi-item roundup contamination, and incidental AI mentions.

Authoritative evidence is deliberately bounded to publisher-owned fields:

* title;
* genuine publisher subtitle;
* the first factual publisher ``snippet`` sentence.

``summary`` is intentionally absent because the validated Brief may contain
generated editorial text under that name.  Provider queries, generated
why-it-matters/category fields, source prestige, network content, and full
bodies are never inputs.  The module performs no I/O, network, or state work.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from app import ai_centrality

AI_MATERIAL_EVENT = "AI_MATERIAL_EVENT"
INVESTOR_MARKET_COMMENTARY = "INVESTOR_MARKET_COMMENTARY"
GENERIC_INDUSTRY_AI_TAILWIND = "GENERIC_INDUSTRY_AI_TAILWIND"
ROUNDUP_MULTI_TOPIC = "ROUNDUP_MULTI_TOPIC"
AI_INCIDENTAL = "AI_INCIDENTAL"
OTHER_NONEXECUTIVE = "OTHER_NONEXECUTIVE"


@dataclass(frozen=True)
class WatchSemanticPrecisionDecision:
    """One bounded, deterministic dominant-subject verdict."""

    semantic_class: str
    eligible: bool
    reason: str
    evidence_terms: tuple[str, ...] = ()
    title_ai_evidence: bool = False
    lead_ai_evidence: bool = False
    title_subject_aligned: bool = False
    material_event: bool = False
    roundup: bool = False
    investor_dominant: bool = False


_AI_TERMS: tuple[str, ...] = (
    "ai", "인공지능", "artificial intelligence", "생성형 ai", "generative ai",
    "llm", "대규모 언어모델", "파운데이션 모델", "머신러닝", "딥러닝",
    "openai", "오픈ai", "챗gpt", "chatgpt", "gpt", "클로드", "claude",
    "제미나이", "gemini", "코파일럿", "copilot", "휴머노이드", "소버린 ai",
    "gpu", "hbm", "npu", "ai 가속기", "ai 반도체",
)

_ENABLING_INFRA_TERMS: tuple[str, ...] = (
    "데이터센터", "데이터 센터", "datacenter", "data center", "idc",
    "반도체", "파운드리", "칩", "가속기", "gpu", "hbm", "npu",
    "전력망", "전력 인프라", "전력인프라", "전력 수요", "전력수요",
    "송전", "변전", "송배전", "발전소", "발전 단지", "발전단지", "ess",
    "원전", "원자력", "smr", "냉각", "용수", "공조", "전기자재",
    "스마트건설", "스마트 건설", "bim", "디지털 트윈", "digital twin",
    "건설 로봇", "건설로봇", "피지컬 ai", "로봇", "로보틱스",
)

_DEFINITE_EVENT_TERMS: tuple[str, ...] = (
    "투자 확정", "투자한다", "투자했다", "투자하기로", "투입한다",
    "출자", "신규 자금", "자금 조달", "신용지원", "신용 지원",
    "공급계약", "공급 계약", "계약 체결", "계약을 체결", "계약 확정",
    "계약 공식화", "본계약", "체결", "협약 체결",
    "mou 체결", "업무협약", "수주했", "수주 계약", "수주 확정", "낙찰",
    "착공", "준공", "증설", "가동 개시", "구축 착수", "건설에 착수",
    "인수 완료", "인수를 완료", "인수를 마무리", "인수 확정", "인수한다",
    "합병 계약", "출시", "공개", "상용화", "도입 확정", "전면 도입",
    "구축", "고도화", "사업 협력", "협력 확정", "협력체계", "협력을 확대", "동맹",
    "구축했다",
    "전환 선언", "ai 선언", "투자를 확대", "동맹 확대", "참여 확대", "참여를 확대",
    "지원한다", "지어지고", "건설 중",
    "규제 시행", "법 시행", "발효", "법안 통과", "승인", "의무화",
    "가입 금지", "참여 자제", "수출통제", "수출 통제",
)

_MATERIAL_RISK_OR_CONSTRAINT_TERMS: tuple[str, ...] = (
    "해킹", "침해", "유출", "탈취", "취약점", "랜섬웨어", "딥페이크",
    "중대 사고", "장애 사태", "금지", "차단", "제재", "수출통제",
    "전력 부족", "전력난", "물 부족", "용수 부족", "계통 포화", "병목",
    "연결 중단", "승인 중단", "건설 중단", "장기계약", "장기 계약",
    "투자위험", "투자 위험", "공급망 재편", "인재 이탈", "생물학 무기",
)

# A broad action noun such as ``구축`` can occur inside earnings/tailwind or
# investor framing ("구축 수혜로 실적 개선").  Only these stronger confirmed
# forms override an otherwise dominant commentary class.
_INDEPENDENT_HARD_EVENT_TERMS: tuple[str, ...] = (
    "투자 확정", "투자한다", "투자했다", "투자하기로", "출자",
    "공급계약", "공급 계약", "계약 체결", "계약을 체결", "계약 확정",
    "계약 공식화", "본계약", "협약 체결", "업무협약", "수주 확정",
    "착공", "준공", "증설 확정", "가동 개시", "구축 착수",
    "인수 완료", "인수를 완료", "인수를 마무리", "인수 확정",
    "합병 계약", "도입 확정", "전면 도입", "규제 시행", "법 시행",
    "발효", "법안 통과", "의무화", "해킹", "침해", "유출", "탈취",
    "취약점", "랜섬웨어", "연결 중단", "승인 중단", "건설 중단",
)

_EVENT_HEADLINE_CUES: tuple[str, ...] = (
    "투자", "계약", "협약", "협력", "손잡고", "수주", "착공", "준공", "증설",
    "인수", "합병", "출시", "공개", "조달", "지원", "뒷받침", "승인", "발효",
    "시행", "의무화", "해킹", "유출", "침해", "중단", "부족", "프로젝트",
    "발전 단지", "발전단지", "협력체계", "구축", "공동 공략", "공식화",
    "확대", "확장", "전환", "동맹", "고도화",
)

# Ceremonial construction language is common in Korean headlines, but is not
# independently proof that construction started.  It is handled by a narrow
# title/lead bridge below instead of being added to either generic event list.
_GROUNDBREAKING_HEADLINE_CUES: tuple[str, ...] = (
    "첫 삽", "첫삽", "기공식", "착공식", "기공",
)
_HISTORICAL_GROUNDBREAKING_CONTEXT: tuple[str, ...] = (
    "지난해", "작년", "과거", "당시", "예전에",
    "첫 삽 뜬 지", "첫삽 뜬 지", "첫 삽 이후", "첫삽 이후",
    "기공식 이후", "착공식 이후",
)
_CURRENT_CONSTRUCTION_CONTEXT: tuple[str, ...] = (
    "이날", "오늘", "금일", "이번", "본격",
)
_CONFIRMED_CONSTRUCTION_START_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"착공(?:했다|했(?:으며|고|다고)?|해(?:서|\s|,|[.!?]|$)|됐다|되었다|"
        r"에\s*(?:들어갔다|돌입했다)|을\s*시작했다)"
    ),
    re.compile(
        r"(?:건설|건립|구축|공사)(?:(?:을|를|에)\s*)?"
        r"(?:시작했다|시작했(?:으며|고)?|착수했다|착수했(?:으며|고)?|"
        r"돌입했다|돌입했(?:으며|고)?|개시했다|개시했(?:으며|고)?)"
    ),
    re.compile(
        r"기공(?:했다|했(?:으며|고|다고)?|해(?:서|\s|,|[.!?]|$)|됐다|되었다)"
    ),
    re.compile(
        r"(?:기공|기공식|착공식)(?:을)?\s*"
        r"(?:개최했다|개최했(?:으며|고)?|열었다|열었(?:으며|고)?|"
        r"거행했다|거행했(?:으며|고)?)"
    ),
    re.compile(r"첫\s*삽(?:을)?\s*(?:떴다|떴으며|뜨며)"),
)

# The actor bridge exists for material corporate events whose title names the
# actor/action while the factual lead identifies the AI target.  A bare AI
# feature mention (voice assistant, chatbot, predictive maintenance) is not a
# sufficient target.  These terms preserve the validated acquisition and
# AI-infrastructure recall cases without allowing any generated field.
_ACTOR_BRIDGE_AI_TARGET_TERMS: tuple[str, ...] = (
    "스타트업", "ai 코딩", "인공지능 코딩", "ai 기업", "인공지능 기업",
    "ai 플랫폼", "인공지능 플랫폼", "ai 모델", "인공지능 모델",
)

_CONCRETE_SCALE_RE = re.compile(
    r"[0-9][0-9,.]*\s*(?:조|억|천억|백억|만)\s*(?:원|달러)"
)

_HDEC_OR_COMPETITOR_TERMS: tuple[str, ...] = (
    "현대건설", "현대엔지니어링", "hyundai e&c",
    "삼성물산", "대우건설", "gs건설", "dl이앤씨", "포스코이앤씨",
    "sk에코플랜트",
)

_MAJOR_AI_ACTOR_TERMS: tuple[str, ...] = (
    "엔비디아", "nvidia", "오픈ai", "openai", "마이크로소프트", "microsoft",
    "구글", "google", "알파벳", "alphabet", "메타", "meta", "아마존",
    "amazon", "aws", "앤트로픽", "anthropic", "xai", "x.ai", "오라클",
    "oracle", "삼성전자", "sk하이닉스", "네이버", "카카오", "현대차",
    "현대차그룹", "포스코dx", "젠슨 황",
    "스페이스x", "spacex", "커서", "cursor", "그록", "groq",
)

_INVESTOR_STRONG_PHRASES: tuple[str, ...] = (
    "투자가 보인다", "투자 전략", "투자전략", "추천 종목", "추천종목",
    "수혜 종목", "수혜주", "목표주가", "목표가", "투자의견", "증권가 전망",
    "매수 전략", "매도 전략", "매수 추천", "매도 추천", "포트폴리오",
    "밸류에이션", "주가 전망", "실적 모멘텀", "종목 고르", "종목 선택",
    "어디에 투자", "무엇에 투자", "투자해야 하나", "투자 체크",
)
_INVESTOR_AUDIENCE_TERMS: tuple[str, ...] = (
    "투자자", "투자를 위해", "투자하려면", "투자할 때", "투자 포인트",
    "주식 투자", "주식투자", "증권사", "애널리스트", "리서치",
)
_MARKET_GUIDANCE_TERMS: tuple[str, ...] = (
    "종목", "주가", "증시", "매수", "매도", "수혜", "목표가", "목표주가",
    "밸류", "가이던스", "컨센서스", "전망", "기대", "모멘텀", "시장 방향",
)
_INVESTOR_SERIES_MARKERS: tuple[str, ...] = (
    "주末머니", "주말머니", "마켓인사이드", "재테크", "투자노트",
)

_TAILWIND_CAUSE_TERMS: tuple[str, ...] = (
    "특수", "수혜", "힘입어", "덕분", "발판 삼아", "열풍을 타고", "붐을 타고",
    "호황", "훈풍", "낙수효과", "성장세", "수요 증가로", "수요 확대로",
    "수요가 늘", "수요가 증가", "수요가 확대",
)
_PERFORMANCE_OR_SECTOR_OUTCOME_TERMS: tuple[str, ...] = (
    "실적", "매출", "영업익", "영업이익", "순이익", "흑자", "적자", "반등",
    "가동률", "업황", "불황", "호황", "성장축", "성장동력", "수익원",
    "성장률", "물가", "주가", "몸값", "시장 전망", "투자시장", "부상",
    "신성장", "돈 된다",
)
_GENERIC_CAUSAL_COMMENTARY_TERMS: tuple[str, ...] = (
    "올려", "낮춰", "밀어올", "끌어올", "좋아졌", "개선됐", "증가했다",
    "확대될 전망", "기회가 확대", "시장 열린다", "판 키우는", "더 큰다",
    "성장축으로", "수익원으로",
)

_ROUNDUP_MARKERS: tuple[str, ...] = (
    "경제 단신", "산업 단신", "기업 단신", "단신", "뉴스 브리핑", "뉴스브리핑",
    "뉴스 모음", "소식 모음", "이모저모", "주요 뉴스", "오늘의 뉴스",
)
_ROUNDUP_SUFFIX_RE = re.compile(r"(?:…|\.{2,}|·|,|/)[^\n]{0,80}\s외(?:\s|$|[\]］】)])")

_NONEXECUTIVE_COMMENTARY_TERMS: tuple[str, ...] = (
    "알아야", "왜 ", "왜?", "어떻게", "누가 웃", "서둘러야", "해야 하나",
    "시대 준비", "버티기", "거를 수도", "선별", "가능성", "확대되나",
    "전망", "예상", "관측", "분석", "해설", "가이드", "체크리스트",
    "열쇠는", "벼락부자", "속도전만", "제안 거절한", "몸값 반토막",
    "으로 본", "가동시", "추가 배출", "이유", "ai와 뉴비즈",
)


def _mapping(obj: object, key: str) -> Mapping[str, Any]:
    if isinstance(obj, Mapping):
        value = obj.get(key, {})
    else:
        value = getattr(obj, key, {})
    return value if isinstance(value, Mapping) else {}


def _clean(value: object) -> str:
    return " ".join(str(value or "").split())


def _contains(text: str, term: str) -> bool:
    needle = term.casefold()
    if re.fullmatch(r"[a-z0-9.&-]+", needle):
        return re.search(
            rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", text
        ) is not None
    return needle in text


def _hits(text: str, terms: Sequence[str]) -> tuple[str, ...]:
    return tuple(term for term in terms if _contains(text, term))


def _publisher_evidence(article: Mapping[str, Any]) -> tuple[str, str, str]:
    """Return title/subtitle/first factual snippet sentence; never summary."""
    after = _mapping(article, "after")
    title = _clean(article.get("title") or after.get("title"))
    subtitle = ""
    for owner in (article, after):
        subtitle = _clean(owner.get("subtitle") or owner.get("publisher_subtitle"))
        if subtitle:
            break
    snippet = ""
    for owner in (article, after):
        snippet = _clean(owner.get("snippet"))
        if snippet:
            break
    factual = {"title": title, "subtitle": subtitle, "snippet": snippet}
    lead = ai_centrality.article_lead_sentence(factual)
    return title, subtitle, lead


def _roundup_title(title_lower: str) -> tuple[bool, tuple[str, ...]]:
    hits = _hits(title_lower, _ROUNDUP_MARKERS)
    suffix = bool(_ROUNDUP_SUFFIX_RE.search(title_lower))
    evidence = hits + (("roundup_suffix:외",) if suffix else ())
    return bool(evidence), evidence


def _confirmed_groundbreaking_event(
    title_lower: str,
    lead_lower: str,
    *,
    title_aligned: bool,
) -> tuple[bool, tuple[str, ...]]:
    """Bridge a current groundbreaking headline to a proved lead action."""
    cue_hits = _hits(title_lower, _GROUNDBREAKING_HEADLINE_CUES)
    historical_title_hits = _hits(
        title_lower, _HISTORICAL_GROUNDBREAKING_CONTEXT
    )
    action_matches = tuple(
        match
        for pattern in _CONFIRMED_CONSTRUCTION_START_PATTERNS
        for match in pattern.finditer(lead_lower)
    )
    lead_confirmed = any(
        not _hits(
            lead_lower[max(0, match.start() - 40):match.start()],
            _HISTORICAL_GROUNDBREAKING_CONTEXT,
        )
        or _hits(
            lead_lower[max(0, match.start() - 40):match.start()],
            _CURRENT_CONSTRUCTION_CONTEXT,
        )
        for match in action_matches
    )
    confirmed = bool(
        title_aligned
        and cue_hits
        and not historical_title_hits
        and lead_confirmed
    )
    evidence = cue_hits + (
        ("confirmed_construction_start_in_publisher_lead",)
        if lead_confirmed else ()
    )
    return confirmed, evidence


def _actor_bridge_ai_nexus(lead_lower: str) -> tuple[bool, tuple[str, ...]]:
    """Require the publisher lead to identify a material AI event target."""
    ai_hits = _hits(lead_lower, _AI_TERMS)
    infra_hits = _hits(lead_lower, _ENABLING_INFRA_TERMS)
    target_hits = _hits(lead_lower, _ACTOR_BRIDGE_AI_TARGET_TERMS)
    return bool(ai_hits and (infra_hits or target_hits)), tuple(
        dict.fromkeys(ai_hits + infra_hits + target_hits)
    )


def _material_event(
    title_lower: str,
    factual_zone: str,
    lead_lower: str,
    *,
    title_aligned: bool,
    lead_ai: bool,
) -> tuple[bool, tuple[str, ...]]:
    event_hits = _hits(factual_zone, _DEFINITE_EVENT_TERMS)
    lead_event_hits = _hits(lead_lower, _DEFINITE_EVENT_TERMS)
    risk_hits = _hits(factual_zone, _MATERIAL_RISK_OR_CONSTRAINT_TERMS)
    title_actor = _hits(
        title_lower, _HDEC_OR_COMPETITOR_TERMS + _MAJOR_AI_ACTOR_TERMS
    )
    groundbreaking_cues = _hits(title_lower, _GROUNDBREAKING_HEADLINE_CUES)
    title_event = tuple(
        hit for hit in _hits(title_lower, _DEFINITE_EVENT_TERMS)
        # ``착공`` is a substring of ``착공식``.  A ceremony headline must use
        # the confirmed publisher-lead bridge, not qualify as a bare action.
        if not (groundbreaking_cues and hit == "착공")
    )
    title_risk = _hits(title_lower, _MATERIAL_RISK_OR_CONSTRAINT_TERMS)
    headline_cues = tuple(
        hit for hit in _hits(title_lower, _EVENT_HEADLINE_CUES)
        if not (groundbreaking_cues and hit == "착공")
    )
    scaled_corporate_investment = bool(
        title_actor
        and _contains(title_lower, "투자")
        and _CONCRETE_SCALE_RE.search(title_lower)
        and _hits(title_lower, ("확대", "확정", "투입", "투자한다", "투자했다"))
    )
    actor_ai_nexus, actor_nexus_hits = _actor_bridge_ai_nexus(lead_lower)
    actor_bridge = bool(
        title_actor and lead_event_hits and lead_ai and actor_ai_nexus
    )
    groundbreaking_bridge, groundbreaking_hits = _confirmed_groundbreaking_event(
        title_lower,
        lead_lower,
        title_aligned=title_aligned,
    )
    aligned_event = bool(
        title_aligned
        and (
            title_event
            or title_risk
            or ((event_hits or risk_hits) and headline_cues)
        )
    )
    material = (
        aligned_event
        or actor_bridge
        or groundbreaking_bridge
        or scaled_corporate_investment
    )
    evidence = tuple(dict.fromkeys(
        event_hits
        + risk_hits
        + title_actor
        + title_event
        + title_risk
        + headline_cues
        + actor_nexus_hits
        + groundbreaking_hits
        + (("scaled_corporate_investment",) if scaled_corporate_investment else ())
    ))
    return material, evidence


def classify(article: Mapping[str, Any]) -> WatchSemanticPrecisionDecision:
    """Classify one Watch candidate from bounded publisher factual evidence."""
    title, subtitle, lead = _publisher_evidence(article)
    title_lower = title.casefold()
    factual_zone = " ".join(
        part.casefold() for part in (title, subtitle, lead) if part
    )
    title_ai_hits = _hits(title_lower, _AI_TERMS)
    lead_ai_hits = _hits(" ".join((subtitle, lead)).casefold(), _AI_TERMS)
    title_infra_hits = _hits(title_lower, _ENABLING_INFRA_TERMS)
    title_aligned = bool(title_ai_hits or title_infra_hits)
    roundup, roundup_hits = _roundup_title(title_lower)
    material, material_hits = _material_event(
        title_lower,
        factual_zone,
        lead.casefold(),
        title_aligned=title_aligned,
        lead_ai=bool(lead_ai_hits),
    )

    common = dict(
        title_ai_evidence=bool(title_ai_hits),
        lead_ai_evidence=bool(lead_ai_hits),
        title_subject_aligned=title_aligned,
        material_event=material,
        roundup=roundup,
    )

    if roundup and not title_aligned and lead_ai_hits:
        return WatchSemanticPrecisionDecision(
            ROUNDUP_MULTI_TOPIC,
            False,
            "non_ai_roundup_title_with_ai_secondary_item",
            tuple(dict.fromkeys(roundup_hits + lead_ai_hits)),
            **common,
        )

    if not title_ai_hits and not lead_ai_hits:
        return WatchSemanticPrecisionDecision(
            AI_INCIDENTAL,
            False,
            "no_bounded_ai_evidence_in_title_subtitle_or_factual_lead",
            title_infra_hits,
            **common,
        )

    if lead_ai_hits and not title_aligned and not material:
        return WatchSemanticPrecisionDecision(
            AI_INCIDENTAL,
            False,
            "title_subject_unrelated_to_ai_secondary_evidence",
            lead_ai_hits,
            **common,
        )

    investor_hits = _hits(title_lower, _INVESTOR_STRONG_PHRASES)
    audience_hits = _hits(factual_zone, _INVESTOR_AUDIENCE_TERMS)
    guidance_hits = _hits(factual_zone, _MARKET_GUIDANCE_TERMS)
    series_hits = _hits(title_lower, _INVESTOR_SERIES_MARKERS)
    investor_dominant = bool(
        investor_hits
        or series_hits
        or (audience_hits and guidance_hits)
    )
    independent_event_hits = _hits(factual_zone, _INDEPENDENT_HARD_EVENT_TERMS)
    common["investor_dominant"] = investor_dominant
    if investor_dominant and not independent_event_hits:
        return WatchSemanticPrecisionDecision(
            INVESTOR_MARKET_COMMENTARY,
            False,
            "investor_audience_or_market_guidance_dominates",
            tuple(dict.fromkeys(investor_hits + audience_hits + guidance_hits + series_hits)),
            **common,
        )

    tailwind_hits = _hits(factual_zone, _TAILWIND_CAUSE_TERMS)
    outcome_hits = _hits(title_lower, _PERFORMANCE_OR_SECTOR_OUTCOME_TERMS)
    causal_hits = _hits(title_lower, _GENERIC_CAUSAL_COMMENTARY_TERMS)
    generic_tailwind = bool(
        (tailwind_hits and outcome_hits)
        or (outcome_hits and causal_hits)
        or (outcome_hits and title_ai_hits)
    )
    if generic_tailwind and not independent_event_hits:
        return WatchSemanticPrecisionDecision(
            GENERIC_INDUSTRY_AI_TAILWIND,
            False,
            "ai_is_a_tailwind_or_cause_not_a_new_material_event",
            tuple(dict.fromkeys(tailwind_hits + outcome_hits + causal_hits)),
            **common,
        )

    commentary_hits = _hits(title_lower, _NONEXECUTIVE_COMMENTARY_TERMS)
    if commentary_hits and not material:
        return WatchSemanticPrecisionDecision(
            OTHER_NONEXECUTIVE,
            False,
            "analysis_speculation_or_human_interest_without_material_event",
            commentary_hits,
            **common,
        )

    if material:
        return WatchSemanticPrecisionDecision(
            AI_MATERIAL_EVENT,
            True,
            "bounded_factual_evidence_proves_material_ai_event",
            tuple(dict.fromkeys(material_hits + title_ai_hits + title_infra_hits)),
            **common,
        )

    return WatchSemanticPrecisionDecision(
        OTHER_NONEXECUTIVE,
        False,
        "ai_subject_without_new_material_executive_event",
        tuple(dict.fromkeys(title_ai_hits + lead_ai_hits + title_infra_hits)),
        **common,
    )


__all__ = [
    "AI_INCIDENTAL",
    "AI_MATERIAL_EVENT",
    "GENERIC_INDUSTRY_AI_TAILWIND",
    "INVESTOR_MARKET_COMMENTARY",
    "OTHER_NONEXECUTIVE",
    "ROUNDUP_MULTI_TOPIC",
    "WatchSemanticPrecisionDecision",
    "classify",
]
