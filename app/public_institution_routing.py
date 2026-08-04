"""Deterministic public-institution editorial routing.

The lane is upstream operator metadata, never a publication category.  This
module performs no network I/O and never reads generated executive commentary
when deciding authority or promotion.  Verified official status requires both
an explicit organization identity and a publisher-direct domain registered in
``data/public_institution_sources.json``.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import Any, Mapping
from urllib.parse import urlsplit

from app import config, publisher_direct, source_quality

SOURCE_CLASS_OTHER = "other"
SOURCE_CLASS_OFFICIAL = "official_institution"
SOURCE_CLASS_UNVERIFIED = "institution_authority_unverified"
LANE_MAIN = "main_candidate"
LANE_PUBLIC = "public_institution"
SURFACE_MAIN = "main_candidate_lane"
SURFACE_PUBLIC = "secondary_public_lane"

PUBLIC_INSTITUTION_TYPES = frozenset(
    {
        "central_government",
        "local_government",
        "public_agency",
        "research_institute",
        "public_corporation",
        "regulator",
        "international_public_body",
    }
)
FINAL_CATEGORIES = ("투자·산업", "기업동향", "기술정보")
REGISTRY_PATH = config.DATA_DIR / "public_institution_sources.json"


@dataclass(frozen=True)
class PublicInstitutionRoutingDecision:
    source_class: str = SOURCE_CLASS_OTHER
    editorial_lane: str = LANE_MAIN
    public_institution_type: str = ""
    official_source_name: str = ""
    source_registry_id: str = ""
    source_domain: str = ""
    default_surface: str = SURFACE_MAIN
    main_surface_eligible: bool = True
    teams_alert_eligible: bool = True
    tni_brief_eligible: bool = True
    tni_report_topic_eligible: bool = False
    promotion_reason: str = "not_public_institution"
    promotion_condition: str = ""
    final_category: str = ""
    headline_eligible: bool = True
    authority_verified: bool = False

    @property
    def is_public_lane(self) -> bool:
        return self.editorial_lane == LANE_PUBLIC

    @property
    def is_verified_official(self) -> bool:
        return self.source_class == SOURCE_CLASS_OFFICIAL and self.authority_verified

    def metadata(self) -> dict[str, Any]:
        return asdict(self)


@lru_cache(maxsize=1)
def _registry() -> tuple[dict[str, Any], ...]:
    try:
        payload = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    if (
        not isinstance(payload, Mapping)
        or payload.get("version") != 1
        or payload.get("schema_contract")
        != "HDEC_PUBLIC_INSTITUTION_SOURCE_REGISTRY_V1"
        or not isinstance(payload.get("sources"), list)
    ):
        return ()
    output: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in payload["sources"]:
        if not isinstance(item, Mapping):
            return ()
        source_id = _clean(item.get("source_id"))
        display_name = _clean(item.get("display_name"))
        institution_type = _clean(item.get("public_institution_type"))
        aliases = tuple(
            value
            for value in (_clean(alias) for alias in item.get("aliases") or [])
            if value
        )
        domains = tuple(
            value
            for value in (_domain(domain) for domain in item.get("domains") or [])
            if value
        )
        if (
            not source_id
            or source_id in seen_ids
            or not display_name
            or institution_type not in PUBLIC_INSTITUTION_TYPES
            or not aliases
            or not domains
        ):
            return ()
        seen_ids.add(source_id)
        output.append(
            {
                "source_id": source_id,
                "display_name": display_name,
                "public_institution_type": institution_type,
                "aliases": aliases,
                "domains": domains,
                "title_identity_allowed": item.get("title_identity_allowed")
                is True,
            }
        )
    return tuple(output)


def _clean(value: object) -> str:
    return " ".join(str(value or "").split())


def _identity_key(value: object) -> str:
    return re.sub(
        r"[^0-9a-z가-힣]+",
        "",
        unicodedata.normalize("NFKC", _clean(value)).casefold(),
    )


def _domain(value: object) -> str:
    raw = _clean(value).casefold().rstrip(".")
    if "://" not in raw:
        raw = "https://" + raw
    try:
        host = (urlsplit(raw).hostname or "").casefold().rstrip(".")
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def _domain_matches(host: str, registered: str) -> bool:
    return bool(host and registered) and (
        host == registered or host.endswith("." + registered)
    )


def _publisher_url(article: Mapping[str, Any]) -> str:
    return publisher_direct.publisher_url(article)


def _source_identity(article: Mapping[str, Any]) -> str:
    metadata = article.get("source_metadata") or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except (TypeError, ValueError):
            metadata = {}
    explicit = metadata if isinstance(metadata, Mapping) else {}
    return _clean(
        article.get("official_source_name")
        or article.get("source")
        or article.get("publisher")
        or explicit.get("official_source_name")
        or explicit.get("source_name")
    )


def _registry_match(
    article: Mapping[str, Any], source_name: str, publisher_url: str
) -> dict[str, Any] | None:
    host = _domain(publisher_url)
    source_key = _identity_key(source_name)
    title_key = _identity_key(article.get("title"))
    explicit_id = _clean(article.get("source_registry_id"))
    matches: list[tuple[int, dict[str, Any]]] = []
    for entry in _registry():
        domain_match = any(
            _domain_matches(host, domain) for domain in entry["domains"]
        )
        if not domain_match:
            continue
        if explicit_id:
            if explicit_id == entry["source_id"]:
                matches.append((3, entry))
            continue
        if entry["title_identity_allowed"] and any(
            _identity_key(alias) in title_key for alias in entry["aliases"]
        ):
            matches.append((2, entry))
        elif any(
            _identity_key(alias) == source_key for alias in entry["aliases"]
        ):
            matches.append((1, entry))
    return max(matches, key=lambda item: item[0])[1] if matches else None


_AI_TERMS = (
    "ai", "인공지능", "artificial intelligence", "생성형", "llm", "gpu",
    "컴퓨팅", "데이터센터", "데이터 센터", "머신러닝", "machine learning",
)
_PUBLICITY_TERMS = (
    "캠페인", "홍보", "체험행사", "체험 행사", "공모전", "기념행사",
    "교육 개최", "교육 실시", "교육생", "연수", "세미나", "워크숍",
)
_INDUSTRY_SUPPORT_TERMS = (
    "기업 지원", "산업 지원", "전환 지원", "지원포털", "지원 포털",
    "ax360", "산업 육성", "기업 전환",
)
_TECH_TERMS = (
    "공공데이터", "데이터", "플랫폼", "포털", "서비스", "모델", "클라우드",
    "안전", "보안", "거버넌스 기술", "운영 기술", "디지털 아카이빙",
)
_POLICY_INVEST_TERMS = (
    "법", "법률", "규제", "정책", "국가전략", "국가 전략", "예산", "투자",
    "조달", "입찰", "발주", "인프라", "컴퓨팅센터", "데이터센터", "지원",
    "procurement", "tender", "regulation", "act", "budget", "infrastructure",
)
_MATERIAL_POLICY_INVEST_TERMS = (
    "법", "법률", "규제", "국가전략", "국가 전략", "예산", "투자",
    "조달", "입찰", "발주", "컴퓨팅센터", "데이터센터",
    "procurement", "tender", "regulation", "act", "budget",
)
_PUBLIC_TECH_SERVICE_TERMS = (
    "공공데이터", "데이터 포털", "데이터포털", "ai 서비스", "ai서비스",
    "서비스 개시", "플랫폼", "포털", "모델", "대화형 검색",
)


def _contains(text: str, terms: tuple[str, ...]) -> bool:
    lowered = text.casefold()
    return any(term.casefold() in lowered for term in terms)


def _promotion(text: str) -> tuple[str, str]:
    lowered = text.casefold()
    conditions: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
        (
            "binding_law_in_force",
            ("법", "법률", "규정", "의무", "ai act", "regulation"),
            ("시행", "발효", "효력", "의무화", "enters into force", "effective"),
        ),
        (
            "mandatory_enterprise_compliance_change",
            ("기업", "사업자", "enterprise", "provider", "compliance", "준수"),
            ("의무", "필수", "mandatory", "shall", "must comply"),
        ),
        (
            "national_ai_strategy_adopted",
            ("국가 ai 전략", "국가ai전략", "national ai strategy"),
            ("채택", "확정", "의결", "adopted", "approved"),
        ),
        (
            "major_budget_approved",
            ("예산", "budget"),
            ("승인", "확정", "의결", "approved"),
        ),
        (
            "procurement_or_tender_opened",
            ("조달", "입찰", "발주", "procurement", "tender"),
            ("공고", "개시", "착수", "opened", "issued"),
        ),
        (
            "national_ai_infrastructure_construction_started",
            (
                "국가ai컴퓨팅센터", "국가 ai 컴퓨팅센터", "ai 데이터센터",
                "ai data center", "national ai computing", "국가 컴퓨팅",
            ),
            ("착공", "첫 삽", "공사 시작", "construction start", "groundbreaking"),
        ),
        (
            "major_ai_compute_resource_operational",
            (
                "국가ai컴퓨팅센터", "국가 ai 컴퓨팅센터", "국가 ai 모델",
                "국가ai모델", "gpu 클러스터", "national ai computing",
            ),
            ("가동", "준공", "운영 개시", "상용화", "operational", "commissioned"),
        ),
        (
            "binding_agreement_or_contract_signed",
            ("협약", "계약", "agreement", "contract"),
            ("체결", "서명", "signed"),
        ),
        (
            "direct_hdec_material_impact",
            ("현대건설", "hyundai e&c", "hyundai engineering & construction"),
            (
                "건설", "안전", "에너지", "조달", "입찰", "규제", "계약",
                "construction", "safety", "energy", "procurement", "regulation",
            ),
        ),
    )
    for key, subject_terms, action_terms in conditions:
        if any(term in lowered for term in subject_terms) and any(
            term in lowered for term in action_terms
        ):
            return key, f"material_condition_proven:{key}"
    return "", "no_independently_proven_material_promotion_condition"


def _brief_eligibility(text: str, promoted: bool) -> bool:
    if _contains(text, _PUBLICITY_TERMS):
        return False
    if promoted:
        return True
    return _contains(text, _AI_TERMS) and (
        _contains(text, _TECH_TERMS)
        or _contains(text, _INDUSTRY_SUPPORT_TERMS)
        or _contains(text, _POLICY_INVEST_TERMS)
    )


def _final_category(
    text: str, institution_type: str, *, brief_eligible: bool
) -> str:
    if not brief_eligible:
        return ""
    if institution_type == "public_corporation" and _contains(
        text, ("전략", "조직", "경영", "도입", "전환", "strategy", "organization")
    ):
        return "기업동향"
    if _contains(text, _INDUSTRY_SUPPORT_TERMS) or _contains(
        text, _MATERIAL_POLICY_INVEST_TERMS
    ):
        return "투자·산업"
    # A public AI service remains technical even when its factual lead names
    # the cloud infrastructure used to operate it. Infrastructure vocabulary
    # alone must not turn a portal/service launch into public investment.
    if _contains(text, _PUBLIC_TECH_SERVICE_TERMS):
        return "기술정보"
    if _contains(text, _POLICY_INVEST_TERMS):
        return "투자·산업"
    if _contains(text, _TECH_TERMS):
        return "기술정보"
    return ""


def classify(article: Mapping[str, Any]) -> PublicInstitutionRoutingDecision:
    """Classify one article into operator routing and product eligibility.

    Only title and factual lead fields are read for content policy.  Generated
    fields such as ``why_it_matters`` and ``executive_implication`` are
    intentionally ignored.
    """
    if not isinstance(article, Mapping):
        return PublicInstitutionRoutingDecision()
    source_name = _source_identity(article)
    publisher_url = _publisher_url(article)
    match = _registry_match(article, source_name, publisher_url)
    source_looks_institutional = (
        source_quality.classify(source_name).get("source_type") == "institution"
    )
    if match is None and not source_looks_institutional:
        return PublicInstitutionRoutingDecision()
    if match is None:
        return PublicInstitutionRoutingDecision(
            source_class=SOURCE_CLASS_UNVERIFIED,
            editorial_lane=LANE_PUBLIC,
            official_source_name=source_name,
            source_domain=_domain(publisher_url),
            default_surface=SURFACE_PUBLIC,
            main_surface_eligible=False,
            teams_alert_eligible=False,
            tni_brief_eligible=False,
            tni_report_topic_eligible=False,
            promotion_reason="institution_identity_or_domain_not_verified",
            headline_eligible=False,
            authority_verified=False,
        )

    title = _clean(article.get("title"))
    lead = _clean(
        article.get("snippet")
        or article.get("summary")
        or article.get("description")
        or article.get("subtitle")
    )
    factual_text = f"{title} {lead}".strip()
    condition, reason = _promotion(factual_text)
    promoted = bool(condition)
    brief_eligible = _brief_eligibility(factual_text, promoted)
    category = _final_category(
        factual_text,
        str(match["public_institution_type"]),
        brief_eligible=brief_eligible,
    )
    # A category is required before a public article can enter the immutable
    # three-category Brief.  No category is invented merely to fill supply.
    brief_eligible = brief_eligible and category in FINAL_CATEGORIES
    report_eligible = promoted and condition in {
        "binding_law_in_force",
        "mandatory_enterprise_compliance_change",
        "national_ai_strategy_adopted",
        "major_budget_approved",
        "procurement_or_tender_opened",
        "national_ai_infrastructure_construction_started",
        "major_ai_compute_resource_operational",
        "binding_agreement_or_contract_signed",
        "direct_hdec_material_impact",
    }
    return PublicInstitutionRoutingDecision(
        source_class=SOURCE_CLASS_OFFICIAL,
        editorial_lane=LANE_PUBLIC,
        public_institution_type=str(match["public_institution_type"]),
        official_source_name=str(match["display_name"]),
        source_registry_id=str(match["source_id"]),
        source_domain=_domain(publisher_url),
        default_surface=SURFACE_MAIN if promoted else SURFACE_PUBLIC,
        main_surface_eligible=promoted,
        teams_alert_eligible=promoted,
        tni_brief_eligible=brief_eligible,
        tni_report_topic_eligible=report_eligible,
        promotion_reason=reason,
        promotion_condition=condition,
        final_category=category if brief_eligible else "",
        headline_eligible=promoted,
        authority_verified=True,
    )


def validate_placement_override(
    base: Mapping[str, Any], override: Mapping[str, Any] | None
) -> tuple[bool, str, str]:
    """Validate an operator placement override and return its final values.

    A changed surface or final category requires both an explicit override flag
    and a written reason.  The returned tuple is
    ``(human_override, final_surface, final_category)``.
    """
    override = override if isinstance(override, Mapping) else {}
    default_surface = _clean(base.get("default_surface")) or SURFACE_MAIN
    default_category = _clean(base.get("final_category"))
    final_surface = _clean(override.get("final_surface")) or default_surface
    final_category = _clean(override.get("final_category")) or default_category
    changed = final_surface != default_surface or final_category != default_category
    explicit = override.get("human_placement_override") is True
    reason = _clean(override.get("human_placement_reason"))
    if final_surface not in {SURFACE_MAIN, SURFACE_PUBLIC}:
        raise ValueError("unknown final_surface")
    if final_category and final_category not in FINAL_CATEGORIES:
        raise ValueError("unknown final_category")
    if changed and (not explicit or not reason):
        raise ValueError("placement change requires explicit override and written reason")
    if explicit and not reason:
        raise ValueError("human placement override requires written reason")
    return explicit, final_surface, final_category
