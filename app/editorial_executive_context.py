"""Bounded executive context derived after editorial qualification.

This leaf never selects, ranks, qualifies, sends, learns, or writes state.  It
reads only bounded article facts and a versioned company-baseline authority,
then returns one structured object that channel renderers may shorten.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any


CONTEXT_VERSION = 1
BASELINE_SCHEMA_VERSION = 1
DEFAULT_BASELINE_PATH = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "editorial"
    / "company_baselines"
    / "2026-h1-construction.json"
)

MATCHED = "matched"
NONE = "none"
AMBIGUOUS = "ambiguous"
SUPPORTED = "supported"
NOT_SUPPORTED = "not_supported"

MAX_FACT_POINTS = 3
MAX_FACT_LENGTH = 180
MAX_CONTEXT_LENGTH = 500
MAX_IMPLICATION_LENGTH = 600
MAX_WATCH_LENGTH = 320

ALLOWED_DIMENSIONS = frozenset(
    {
        "strategy",
        "order_backlog",
        "revenue",
        "operating_profit",
        "operating_cash_flow",
        "net_cash_or_debt",
        "pf_guarantee",
        "completion_guarantee",
        "data_center_strategy",
        "ai_transformation",
        "nuclear",
        "overseas",
        "portfolio_shift",
    }
)
VERIFIED_FACT_STATUSES = frozenset({"verified", "stale_verified"})

_SENTENCE_RE = re.compile(r"(?<=[.!?。！？])\s+|\n+|\s*[•▪]\s*")
_WORD_RE = re.compile(r"[0-9A-Za-z가-힣]+")
_NUMBER_RE = re.compile(
    r"(?:\d[\d,.]*\s*(?:조|억|만|천|백)?\s*(?:원|달러|%|건|개|GW|MW|명|km|㎡))",
    re.IGNORECASE,
)
_ACTION_TERMS = (
    "수주", "발주", "입찰", "계약", "협력", "투자", "출자", "인수", "매각",
    "개발", "착공", "기공", "준공", "구축", "확대", "증설", "가동", "운영",
    "허가", "승인", "공급", "전환", "조성", "추진", "확충",
)
_AI_TERMS = ("ai", "인공지능", "생성형", "gpu", "npu", "피지컬 ai", "로봇")
_DIMENSION_TERMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("data_center", ("데이터센터", "데이터 센터", "data center", "datacenter", "idc")),
    ("power_grid", ("전력", "전력망", "그리드", "송전", "변전", "배전", "전력계약", "ppa")),
    ("industrial_site", ("산업단지", "산업 단지", "산업거점", "부지", "용지", "새만금", "토지")),
    ("energy_hydrogen", ("수소", "재생에너지", "에너지", "발전", "ess")),
    ("nuclear_smr", ("원전", "원자력", "smr", "소형모듈원전")),
    ("automation_physical_ai", ("피지컬 ai", "건설로봇", "건설 로봇", "스마트건설", "bim", "디지털 트윈")),
    ("semiconductor_fab", ("반도체 팹", "반도체 공장", "fab", "파운드리", "클러스터")),
    ("capital_pf", ("프로젝트금융", "프로젝트 금융", "pf", "보증", "자금조달", "투자")),
)
_INFRA_CONSEQUENCE_TERMS = (
    "프로젝트", "사업", "개발", "부지", "용지", "전력", "용수", "공장", "팹",
    "건설", "시공", "epc", "투자", "수주", "발주", "계약", "착공", "준공",
    "증설", "용량", "인허가", "조성", "공급", "가동", "운영",
)
_BASELINE_DIMENSION_LINKS = {
    "data_center": frozenset({"data_center_strategy", "portfolio_shift", "strategy"}),
    "power_grid": frozenset({"data_center_strategy", "portfolio_shift", "strategy"}),
    "industrial_site": frozenset({"portfolio_shift", "strategy", "overseas"}),
    "energy_hydrogen": frozenset({"portfolio_shift", "strategy"}),
    "nuclear_smr": frozenset({"nuclear", "portfolio_shift", "strategy"}),
    "automation_physical_ai": frozenset({"ai_transformation", "strategy"}),
    "semiconductor_fab": frozenset({"portfolio_shift", "strategy"}),
    "capital_pf": frozenset(
        {"pf_guarantee", "completion_guarantee", "operating_cash_flow", "net_cash_or_debt"}
    ),
}
_EDITABLE_FIELDS = frozenset(
    {
        "fact_points",
        "baseline_context_text",
        "delta_text",
        "hdec_implication_text",
        "watch_point_text",
    }
)


class ExecutiveContextError(ValueError):
    """Malformed or untrusted baseline/context input."""


def _clean(value: object, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _plain(value: object, limit: int) -> str:
    text = re.sub(r"<[^>]*>", " ", str(value or ""))
    return _clean(text, limit)


def _require_text(value: object, *, field: str, limit: int) -> str:
    text = _clean(value, limit + 1)
    if not text or len(text) > limit:
        raise ExecutiveContextError(f"{field} is missing or too long")
    return text


def _optional_text(value: object, *, field: str, limit: int) -> str:
    text = _plain(value, limit + 1)
    if len(text) > limit:
        raise ExecutiveContextError(f"{field} is too long")
    return text


def validate_baseline_authority(value: object) -> dict[str, Any]:
    """Strictly validate the bounded, versioned company-baseline authority."""
    if not isinstance(value, Mapping) or value.get("schema_version") != BASELINE_SCHEMA_VERSION:
        raise ExecutiveContextError("baseline schema version mismatch")
    baseline = {
        "schema_version": BASELINE_SCHEMA_VERSION,
        "baseline_version": _require_text(
            value.get("baseline_version"), field="baseline_version", limit=100
        ),
        "baseline_id": _require_text(value.get("baseline_id"), field="baseline_id", limit=100),
        "reporting_period": _require_text(
            value.get("reporting_period"), field="reporting_period", limit=80
        ),
        "as_of": _require_text(value.get("as_of"), field="as_of", limit=32),
        "status": _require_text(value.get("status"), field="status", limit=40),
    }
    source = value.get("source_document")
    if not isinstance(source, Mapping):
        raise ExecutiveContextError("baseline source_document is malformed")
    sha256 = _clean(source.get("sha256"), 64).casefold()
    if sha256 and not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise ExecutiveContextError("baseline source sha256 is malformed")
    baseline["source_document"] = {
        "filename": _require_text(source.get("filename"), field="source filename", limit=240),
        "source_document_date": _require_text(
            source.get("source_document_date"), field="source_document_date", limit=32
        ),
        "sha256": sha256,
        "extraction_status": _require_text(
            source.get("extraction_status"), field="extraction_status", limit=60
        ),
        "page_reference_scheme": _clean(source.get("page_reference_scheme"), 80),
    }
    entities = value.get("entities")
    if not isinstance(entities, list) or len(entities) > 50:
        raise ExecutiveContextError("baseline entities are malformed")
    normalized_entities: list[dict[str, Any]] = []
    entity_ids: set[str] = set()
    fact_ids: set[str] = set()
    alias_owners: dict[str, str] = {}
    for raw_entity in entities:
        if not isinstance(raw_entity, Mapping):
            raise ExecutiveContextError("baseline entity is malformed")
        entity_id = _require_text(raw_entity.get("entity_id"), field="entity_id", limit=80)
        canonical_name = _require_text(
            raw_entity.get("canonical_name"), field="canonical_name", limit=120
        )
        if entity_id in entity_ids:
            raise ExecutiveContextError("duplicate baseline entity_id")
        entity_ids.add(entity_id)
        raw_aliases = raw_entity.get("aliases")
        if not isinstance(raw_aliases, list) or len(raw_aliases) > 20:
            raise ExecutiveContextError("baseline aliases are malformed")
        aliases: list[str] = []
        for raw_alias in [canonical_name, *raw_aliases]:
            alias = _require_text(raw_alias, field="entity alias", limit=120)
            if len(re.sub(r"\W+", "", alias, flags=re.UNICODE)) < 3:
                raise ExecutiveContextError("baseline alias is too broad")
            key = unicodedata.normalize("NFKC", alias).casefold()
            owner = alias_owners.get(key)
            if owner and owner != entity_id:
                raise ExecutiveContextError("baseline alias belongs to multiple entities")
            alias_owners[key] = entity_id
            if alias not in aliases:
                aliases.append(alias)
        raw_facts = raw_entity.get("facts")
        if not isinstance(raw_facts, list) or len(raw_facts) > 50:
            raise ExecutiveContextError("baseline facts are malformed")
        facts: list[dict[str, Any]] = []
        for raw_fact in raw_facts:
            if not isinstance(raw_fact, Mapping):
                raise ExecutiveContextError("baseline fact is malformed")
            fact_id = _require_text(raw_fact.get("fact_id"), field="fact_id", limit=100)
            dimension = _require_text(raw_fact.get("dimension"), field="dimension", limit=60)
            if fact_id in fact_ids or dimension not in ALLOWED_DIMENSIONS:
                raise ExecutiveContextError("baseline fact identity/dimension is invalid")
            fact_ids.add(fact_id)
            status = _require_text(raw_fact.get("status"), field="fact status", limit=40)
            numeric_value = raw_fact.get("value")
            if numeric_value is not None and (type(numeric_value) not in {int, float}):
                raise ExecutiveContextError("baseline numeric value is malformed")
            facts.append(
                {
                    "fact_id": fact_id,
                    "dimension": dimension,
                    "value": numeric_value,
                    "unit": _clean(raw_fact.get("unit"), 40),
                    "as_of": _require_text(raw_fact.get("as_of"), field="fact as_of", limit=32),
                    "source_reference": _require_text(
                        raw_fact.get("source_reference"), field="source_reference", limit=120
                    ),
                    "evidence_paraphrase": _require_text(
                        raw_fact.get("evidence_paraphrase"),
                        field="evidence_paraphrase",
                        limit=280,
                    ),
                    "status": status,
                    "freshness": _require_text(
                        raw_fact.get("freshness"), field="fact freshness", limit=40
                    ),
                }
            )
        normalized_entities.append(
            {
                "entity_id": entity_id,
                "canonical_name": canonical_name,
                "aliases": aliases,
                "facts": facts,
            }
        )
    baseline["entities"] = normalized_entities
    return baseline


def load_baseline_authority(path: Path | str | None = None) -> dict[str, Any]:
    baseline_path = Path(path) if path is not None else DEFAULT_BASELINE_PATH
    try:
        payload = json.loads(baseline_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ExecutiveContextError("baseline authority is unavailable or malformed") from exc
    return validate_baseline_authority(payload)


def _alias_present(text: str, alias: str) -> bool:
    normalized_text = unicodedata.normalize("NFKC", text).casefold()
    normalized_alias = unicodedata.normalize("NFKC", alias).casefold()
    pattern = rf"(?<![0-9a-z가-힣]){re.escape(normalized_alias)}(?![0-9a-z가-힣])"
    return re.search(pattern, normalized_text) is not None


def match_baseline_entity(text: object, baseline: Mapping[str, Any]) -> dict[str, Any]:
    """Exact-name/alias match; multiple entities always fail closed."""
    haystack = _clean(text, 5000)
    matched: list[Mapping[str, Any]] = []
    for entity in baseline.get("entities", []):
        if not isinstance(entity, Mapping):
            continue
        aliases = entity.get("aliases") if isinstance(entity.get("aliases"), list) else []
        if any(_alias_present(haystack, str(alias)) for alias in aliases):
            matched.append(entity)
    if not matched:
        return {"status": NONE, "entity_ids": [], "entity": None}
    if len(matched) != 1:
        return {
            "status": AMBIGUOUS,
            "entity_ids": [str(entity.get("entity_id") or "") for entity in matched],
            "entity": None,
        }
    return {
        "status": MATCHED,
        "entity_ids": [str(matched[0].get("entity_id") or "")],
        "entity": matched[0],
    }


def _fingerprint(value: str) -> set[str]:
    return {word.casefold() for word in _WORD_RE.findall(value) if len(word) >= 2}


def _too_similar(first: str, second: str) -> bool:
    a, b = _fingerprint(first), _fingerprint(second)
    if not a or not b:
        return first.casefold() == second.casefold()
    return len(a & b) / max(1, min(len(a), len(b))) >= 0.82


def _executive_tone(sentence: str) -> str:
    text = _clean(sentence, MAX_FACT_LENGTH).rstrip(" .")
    substitutions = (
        (r"했다고 밝혔다$", "했다고 밝힘"),
        (r"했다고 한다$", "한 것으로 전해짐"),
        (r"했다$", "함"),
        (r"됐다$", "됨"),
        (r"되었다$", "됨"),
        (r"한다$", "함"),
        (r"이다$", "임"),
        (r"있다$", "있음"),
        (r"없다$", "없음"),
        (r"전망된다$", "전망임"),
    )
    for pattern, replacement in substitutions:
        if re.search(pattern, text):
            text = re.sub(pattern, replacement, text)
            break
    return text + "." if text else ""


def _authorized_evidence(article: Mapping[str, Any]) -> tuple[list[tuple[str, str]], list[str]]:
    evidence: list[tuple[str, str]] = []
    used_fields: list[str] = []
    title = _plain(article.get("title"), MAX_FACT_LENGTH)
    if title:
        evidence.append(("title", title))
        used_fields.append("title")
    for field, limit in (("subtitle", 500), ("snippet", 1200)):
        value = _plain(article.get(field), limit)
        if value:
            evidence.append((field, value))
            used_fields.append(field)
    source_kind = _clean(article.get("collection_source_kind"), 80)
    summary_authorized = article.get("summary_authorized") is True or source_kind in {
        "url_import",
        "human_link",
        "team_link",
        "human_supplied_link",
    }
    if summary_authorized:
        summary = _plain(article.get("summary"), 1200)
        if summary:
            evidence.append(("validated_import_summary", summary))
            used_fields.append("validated_import_summary")
    return evidence, list(dict.fromkeys(used_fields))


def fact_points(article: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    evidence, fields = _authorized_evidence(article)
    candidates: list[str] = []
    for _field, value in evidence[1:]:
        parts = [part for part in _SENTENCE_RE.split(value) if _clean(part, 20)]
        candidates.extend(parts or [value])
    if evidence and len(candidates) < 2:
        candidates.append(evidence[0][1])
    output: list[str] = []
    for candidate in candidates:
        point = _executive_tone(candidate)
        if not point or any(_too_similar(point, existing) for existing in output):
            continue
        output.append(point)
        if len(output) == MAX_FACT_POINTS:
            break
    return output, fields


def _detected_dimensions(text: str) -> list[str]:
    low = text.casefold()
    return [
        dimension
        for dimension, terms in _DIMENSION_TERMS
        if any(term.casefold() in low for term in terms)
    ]


def _baseline_facts_for_dimensions(
    entity: Mapping[str, Any], dimensions: Sequence[str]
) -> list[dict[str, Any]]:
    allowed = set().union(*(_BASELINE_DIMENSION_LINKS.get(item, ()) for item in dimensions))
    output = []
    for fact in entity.get("facts", []):
        if (
            isinstance(fact, Mapping)
            and fact.get("status") in VERIFIED_FACT_STATUSES
            and fact.get("dimension") in allowed
        ):
            output.append(dict(fact))
    return output[:2]


def _implication(text: str, dimensions: Sequence[str], *, qualified: bool) -> tuple[str, list[str]]:
    low = text.casefold()
    concrete = any(term.casefold() in low for term in _ACTION_TERMS) or bool(_NUMBER_RE.search(text))
    ai_present = any(term.casefold() in low for term in _AI_TERMS)
    dimension_set = set(dimensions)
    if not qualified or not concrete or not ai_present:
        return "", []
    if "semiconductor_fab" in dimension_set and not any(
        term.casefold() in low for term in _INFRA_CONSEQUENCE_TERMS
    ):
        return "", []
    if "industrial_site" in dimension_set:
        return (
            "AI 산업거점 조성이 부지 개발과 데이터센터·에너지 기반시설 발주로 이어질 수 있어 "
            "현대건설 관점에서 산업단지 조성 및 인프라 사업화 구조를 점검할 필요가 있음.",
            [item for item in dimensions if item in {"industrial_site", "energy_hydrogen", "power_grid", "data_center"}],
        )
    if "data_center" in dimension_set:
        if "power_grid" in dimension_set or any(term in low for term in ("개발", "운영")):
            return (
                "AI 데이터센터 경쟁이 시공을 넘어 개발·전력·운영 구조로 확장되는 흐름으로, "
                "현대건설 관점에서 사업 발굴과 전력 파트너·EPC 참여 구조를 함께 점검할 필요가 있음.",
                [item for item in dimensions if item in {"data_center", "power_grid", "capital_pf"}],
            )
        return (
            "AI 데이터센터 프로젝트의 발주·전력·EPC 구조가 구체화되는지 현대건설 관점에서 점검할 필요가 있음.",
            ["data_center"],
        )
    if "semiconductor_fab" in dimension_set:
        return (
            "반도체 팹 투자가 부지·전력·용수·EPC 수요로 연결되는 사안으로, "
            "현대건설 관점에서 인허가와 기반시설 발주 구조를 점검할 필요가 있음.",
            [item for item in dimensions if item in {"semiconductor_fab", "power_grid", "industrial_site"}],
        )
    if "nuclear_smr" in dimension_set:
        return (
            "원전·SMR 사업의 파트너와 발주 구조가 현대건설 EPC 포트폴리오에 미칠 영향을 점검할 필요가 있음.",
            ["nuclear_smr"],
        )
    if "automation_physical_ai" in dimension_set:
        return (
            "건설 자동화·피지컬 AI의 현장 적용 범위와 생산성 검증이 현대건설의 기술 경쟁력에 미칠 영향을 점검할 필요가 있음.",
            ["automation_physical_ai"],
        )
    if "power_grid" in dimension_set:
        return (
            "AI 인프라 확대에 따른 전력망·발전 설비 수요가 EPC 기회와 사업 제약에 미칠 영향을 현대건설 관점에서 점검할 필요가 있음.",
            ["power_grid"],
        )
    return "", []


def _watch_point(text: str, dimensions: Sequence[str], *, qualified: bool) -> str:
    if not qualified:
        return ""
    low = text.casefold()
    dimension_set = set(dimensions)
    if "industrial_site" in dimension_set:
        return "산업용지 조성 일정과 데이터센터·에너지 기반시설의 인허가·착공·조달 공고를 확인할 필요가 있음."
    if "data_center" in dimension_set:
        return "후속 프로젝트의 실제 발주·수주, 부지별 용량 및 전력 파트너 확정 여부를 확인할 필요가 있음."
    if "semiconductor_fab" in dimension_set:
        return "팹 투자 확정 규모와 부지·전력·용수 인허가, EPC 발주 시점을 확인할 필요가 있음."
    if "nuclear_smr" in dimension_set:
        return "후속 사업의 파트너 선정, 인허가 및 실제 발주 일정이 구체화되는지 확인할 필요가 있음."
    if "automation_physical_ai" in dimension_set:
        return "현장 실증 범위와 생산성·안전 지표, 상용 배치 여부를 확인할 필요가 있음."
    if "power_grid" in dimension_set:
        return "전력 공급계약, 계통연계 승인 및 관련 설비 발주가 구체화되는지 확인할 필요가 있음."
    if any(term in low for term in ("정책", "규제", "정부")):
        return "후속 예산·시행계획과 사업·인허가 기준이 구체화되는지 확인할 필요가 있음."
    return ""


def derive_executive_context(
    article: Mapping[str, Any],
    *,
    baseline: Mapping[str, Any] | None = None,
    article_already_qualified: bool = True,
) -> dict[str, Any]:
    """Derive context without changing any upstream qualification decision."""
    trusted_baseline = validate_baseline_authority(
        baseline if baseline is not None else load_baseline_authority()
    )
    points, article_fields = fact_points(article)
    evidence, _fields = _authorized_evidence(article)
    current_text = " ".join(value for _field, value in evidence)
    dimensions = _detected_dimensions(current_text)
    match = match_baseline_entity(current_text, trusted_baseline)
    entity = match.get("entity") if match["status"] == MATCHED else None
    baseline_facts = (
        _baseline_facts_for_dimensions(entity, dimensions)
        if isinstance(entity, Mapping)
        else []
    )
    baseline_supported = bool(baseline_facts)
    period = trusted_baseline["reporting_period"]
    baseline_text = (
        f"{period} 기준선: "
        + " ".join(str(fact["evidence_paraphrase"]) for fact in baseline_facts)
        if baseline_supported
        else ""
    )
    action_supported = any(
        term.casefold() in current_text.casefold() for term in _ACTION_TERMS
    )
    delta_supported = bool(
        article_already_qualified
        and match["status"] == MATCHED
        and baseline_facts
        and action_supported
    )
    delta_text = ""
    if delta_supported:
        entity_name = str(entity.get("canonical_name") or "")
        signal = {
            "data_center": "AI 데이터센터 사업 움직임",
            "power_grid": "전력 인프라 사업 움직임",
            "industrial_site": "산업거점 개발 움직임",
            "nuclear_smr": "원전·SMR 사업 움직임",
            "automation_physical_ai": "AI 전환 움직임",
            "semiconductor_fab": "반도체 인프라 사업 움직임",
        }.get(dimensions[0] if dimensions else "", "사업 움직임")
        delta_text = (
            f"{entity_name}의 이번 {signal}은 {period} 기준선에 기록된 "
            f"{baseline_facts[0]['evidence_paraphrase']} 방향의 후속 신호임."
        )
    implication_text, implication_dimensions = _implication(
        current_text, dimensions, qualified=article_already_qualified
    )
    watch_text = _watch_point(
        current_text, dimensions, qualified=article_already_qualified
    )
    return {
        "version": CONTEXT_VERSION,
        "fact_points": points,
        "baseline_match": {
            "status": match["status"],
            "entity_id": str(entity.get("entity_id") or "") if entity else "",
            "canonical_name": str(entity.get("canonical_name") or "") if entity else "",
        },
        "baseline_context": {
            "status": SUPPORTED if baseline_supported else NOT_SUPPORTED,
            "text": baseline_text,
            "reporting_period": period if baseline_supported else "",
            "as_of": trusted_baseline["as_of"] if baseline_supported else "",
        },
        "delta_vs_baseline": {
            "status": SUPPORTED if delta_supported else NOT_SUPPORTED,
            "text": delta_text,
        },
        "hdec_implication": {
            "status": SUPPORTED if implication_text else NOT_SUPPORTED,
            "label": "현대건설 관점 · 분석",
            "text": implication_text,
            "dimensions": implication_dimensions,
        },
        "watch_point": {
            "status": SUPPORTED if watch_text else NOT_SUPPORTED,
            "text": watch_text,
        },
        "provenance": {
            "article_fields": article_fields,
            "baseline_id": trusted_baseline["baseline_id"] if baseline_supported else "",
            "baseline_version": trusted_baseline["baseline_version"] if baseline_supported else "",
            "baseline_fact_ids": [str(fact["fact_id"]) for fact in baseline_facts],
            "source_document_date": (
                trusted_baseline["source_document"]["source_document_date"]
                if baseline_supported
                else ""
            ),
        },
        "confidence": {
            "status": "bounded" if points else "insufficient",
            "baseline_status": trusted_baseline["status"],
            "article_already_qualified": bool(article_already_qualified),
            "selection_authority": False,
        },
    }


def normalize_editor_edits(value: object) -> dict[str, Any]:
    """Validate only fields an editor may change; provenance is never accepted."""
    if value in (None, ""):
        return {}
    if not isinstance(value, Mapping) or set(value) - _EDITABLE_FIELDS:
        raise ExecutiveContextError("executive context editable fields mismatch")
    output: dict[str, Any] = {}
    if "fact_points" in value:
        raw_points = value.get("fact_points")
        if not isinstance(raw_points, list) or not 1 <= len(raw_points) <= MAX_FACT_POINTS:
            raise ExecutiveContextError("fact_points are malformed")
        points = [
            _optional_text(point, field="fact point", limit=MAX_FACT_LENGTH)
            for point in raw_points
        ]
        if any(not point for point in points) or len(points) != len(set(points)):
            raise ExecutiveContextError("fact_points are empty or duplicated")
        output["fact_points"] = points
    for key, limit in (
        ("baseline_context_text", MAX_CONTEXT_LENGTH),
        ("delta_text", MAX_CONTEXT_LENGTH),
        ("hdec_implication_text", MAX_IMPLICATION_LENGTH),
        ("watch_point_text", MAX_WATCH_LENGTH),
    ):
        if key in value:
            output[key] = _optional_text(value.get(key), field=key, limit=limit)
    return output


def apply_editor_edits(
    trusted_context: Mapping[str, Any], edits: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Apply bounded text edits without accepting status or provenance claims."""
    normalized = normalize_editor_edits(edits)
    output = deepcopy(dict(trusted_context))
    if normalized.get("fact_points"):
        output["fact_points"] = normalized["fact_points"]
    if (
        output.get("baseline_context", {}).get("status") == SUPPORTED
        and "baseline_context_text" in normalized
    ):
        output["baseline_context"]["text"] = normalized["baseline_context_text"]
    if (
        output.get("delta_vs_baseline", {}).get("status") == SUPPORTED
        and "delta_text" in normalized
    ):
        output["delta_vs_baseline"]["text"] = normalized["delta_text"]
    if (
        output.get("hdec_implication", {}).get("status") == SUPPORTED
        and "hdec_implication_text" in normalized
    ):
        output["hdec_implication"]["text"] = normalized["hdec_implication_text"]
    if (
        output.get("watch_point", {}).get("status") == SUPPORTED
        and "watch_point_text" in normalized
    ):
        output["watch_point"]["text"] = normalized["watch_point_text"]
    output["confidence"]["editor_modified"] = bool(normalized)
    return output


def editable_context_payload(context: Mapping[str, Any]) -> dict[str, Any]:
    """Return the non-authoritative fields safe for an Editor save payload."""
    baseline = context.get("baseline_context") if isinstance(context, Mapping) else {}
    delta = context.get("delta_vs_baseline") if isinstance(context, Mapping) else {}
    implication = context.get("hdec_implication") if isinstance(context, Mapping) else {}
    watch = context.get("watch_point") if isinstance(context, Mapping) else {}
    return {
        "fact_points": list(context.get("fact_points") or [])[:MAX_FACT_POINTS],
        "baseline_context_text": str((baseline or {}).get("text") or ""),
        "delta_text": str((delta or {}).get("text") or ""),
        "hdec_implication_text": str((implication or {}).get("text") or ""),
        "watch_point_text": str((watch or {}).get("text") or ""),
    }


__all__ = [
    "AMBIGUOUS",
    "BASELINE_SCHEMA_VERSION",
    "CONTEXT_VERSION",
    "DEFAULT_BASELINE_PATH",
    "ExecutiveContextError",
    "MATCHED",
    "NONE",
    "NOT_SUPPORTED",
    "SUPPORTED",
    "apply_editor_edits",
    "derive_executive_context",
    "editable_context_payload",
    "fact_points",
    "load_baseline_authority",
    "match_baseline_entity",
    "normalize_editor_edits",
    "validate_baseline_authority",
]
