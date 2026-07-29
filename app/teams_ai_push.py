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
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence

from app.news_access import choose_article_link
from app.scoring import DAILY_THRESHOLD, INSTANT_THRESHOLD

KST = timezone(timedelta(hours=9))
# D7-AK-6C: up to ten important/top-priority AI articles per run (was three).
MAX_TEAMS_ARTICLES = 10

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


@dataclass(frozen=True)
class TopicDecision:
    eligible: bool
    topic_key: str = ""
    topic_label: str = ""
    matched_terms: tuple[str, ...] = ()
    exclusion_reason: str = ""


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
        _value(article, "radarReason"), _value(article, "category"),
        _value(article, "category_label"), _value(article, "source"),
        after.get("title"), after.get("snippet"), after.get("whyImportant"),
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
        _value(article, "radarReason"),
        _value(article, "executive_section"),
        _value(article, "radar_section"),
        after.get("hdec_relevance"),
        after.get("whyImportant"),
        after.get("radarReason"),
        after.get("executive_section"),
        after.get("radar_section"),
        provenance.get("hdec_relevance"),
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
# categorical statuses. Anything else (missing field, malformed value) is treated as
# ``unavailable`` so a malformed article always fails closed rather than sending.
_SHADOW_KNOWN_STATUSES = ("confirmed", "ambiguous", "blocked", "none", "unavailable")


def _shadow_status(article: object) -> str:
    """Return the hourly shadow-confirmed status; missing/unknown → ``unavailable``."""
    status = _lower(_value(article, "shadow_urgency_status"))
    return status if status in _SHADOW_KNOWN_STATUSES else "unavailable"


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

        tier = _value(owner, "hdec_relevance_tier", None)

        if tier not in (None, "", "-"):
            try:
                if int(tier) <= 3:
                    return True
            except (TypeError, ValueError):
                pass

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

    if not is_hdec_relevant_for_push(article, topic):
        return ImportanceDecision(
            False,
            reason="insufficient_hdec_relevance",
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


def select_teams_push_from_artifact(
    payload: object,
    *,
    max_articles: int = MAX_TEAMS_ARTICLES,
) -> tuple[TeamsPushCandidate, ...]:
    """Fail-closed entrypoint for a raw delta artifact.

    D7-AK-6C — the artifact-level ``shadow_alert_delta`` flag is no longer required: a
    live-delta artifact can produce candidates even when no article is shadow-confirmed,
    because importance now derives from the reused scoring/confirmed-event signals per
    article (see :func:`map_importance`). Only the live-source guard remains, so
    mock/fallback artifacts and malformed article collections always return zero.
    """
    if not isinstance(payload, Mapping):
        return ()
    if _clean(payload.get("source")) != "live-delta":
        return ()
    articles = payload.get("articles")
    if not isinstance(articles, list):
        return ()
    return select_teams_push_candidates(articles, max_articles=max_articles)


def select_teams_push_candidates(
    articles: Iterable[Mapping[str, Any]],
    *,
    max_articles: int = MAX_TEAMS_ARTICLES,
) -> tuple[TeamsPushCandidate, ...]:
    """Filter, rank, and cap important Teams AI push candidates (default: up to ten).

    Ranking is highest-importance first, then 현대건설 직접 영향, then score, then recency —
    so the cap keeps the most decision-relevant articles when more than ``max_articles``
    qualify."""
    from app.teams_push_state import derive_event_cluster_key, material_signature

    candidates: list[TeamsPushCandidate] = []
    for article in articles:
        topic = classify_ai_topic(article)
        importance = map_importance(article, topic)
        # Publisher resolution is preferred but not mandatory. A truthful labeled
        # Google News/portal hop is safer than silently dropping an important article.
        if (
            not topic.eligible
            or not is_hdec_relevant_for_push(article, topic)
            or not importance.sendable
            or not choose_article_link(article).url
        ):
            continue
        candidates.append(
            TeamsPushCandidate(
                article=article,
                topic=topic,
                importance=importance,
                cluster_key=derive_event_cluster_key(article, topic.topic_key),
                material_signature=material_signature(article),
                is_update=_lower(_value(article, "change_type")) == "material_content_update",
            )
        )

    candidates.sort(
        key=lambda item: (
            IMPORTANCE_RANK.get(item.importance.level, 9),
            -int(item.importance.hdec_direct),
            -(item.importance.score if item.importance.score is not None else -1.0),
            -_published_sort_value(item.article),
        )
    )
    return tuple(candidates[: max(0, min(int(max_articles), MAX_TEAMS_ARTICLES))])


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
) -> dict[str, Any]:
    """Build exactly one Teams Workflows Adaptive Card message for one article."""
    if not topic.eligible or not importance.sendable:
        raise ValueError("non-sendable article cannot be rendered as a Teams push card")

    title = _article_field(article, "title") or "제목 없음"
    summary = _article_field(article, "summary", "snippet") or "핵심 요약이 제공되지 않았습니다."
    hdec_impact = _article_field(
        article, "hdec_relevance", "radarReason", "whyImportant"
    ) or "현대건설 영향은 원문과 대시보드에서 추가 확인이 필요합니다."
    source = _article_field(article, "source", "display_source") or "출처 미상"
    published = _fmt_kst(_value(article, "published_at") or _value(article, "published_kst")) or "시각 미상"
    detected = _fmt_kst(detected_at or _value(alert, "generated_at") or _value(alert, "generated_kst")) or "시각 미상"
    article_link = choose_article_link(article)
    article_url = article_link.url
    dashboard_url = _safe_http(_value(alert, "dashboard_url"))
    report_url = _safe_http(_value(alert, "report_url"))

    title_prefix = "[업데이트] " if is_update else ""
    importance_color = "Attention" if importance.level == IMPORTANCE_TOP else "Warning"
    body: list[dict[str, Any]] = [
        _text_block(importance.label, weight="Bolder", color=importance_color, size="Medium"),
        _text_block(topic.topic_label, isSubtle=True, spacing="None"),
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
            "title": f"{article_link.label} 보기",
            "url": article_url,
        })
    if dashboard_url:
        actions.append({"type": "Action.OpenUrl", "title": "대시보드 보기", "url": dashboard_url})
    if report_url:
        actions.append({"type": "Action.OpenUrl", "title": "전체 리포트 보기", "url": report_url})

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
    )


def render_article_email(
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
    article_link = choose_article_link(article)
    article_url = article_link.url
    if not article_url:
        raise ValueError("a valid article URL is required for a Teams push email")
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
        f"[{article_link.label}] {article_url}",
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
        + f'<strong>[{_p(article_link.label)}]</strong> '
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
