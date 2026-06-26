"""Deterministic AI value-chain classification for HDEC executive surfaces.

Engine vs policy (P0-D7-S2): this module is the ENGINE — deterministic
classification logic only. The domain keyword lists (POLICY) live in
``data/ai_value_chain_policy.json`` and are loaded once at import. The module is
intentionally pure: no network, no DB, no scoring writes. It only classifies raw
article metadata into a bounded AI value-chain layer and a Hyundai E&C relevance
tier that downstream dashboard/report code can sort on.

Fail-safe: if the policy file is missing or malformed, every term group loads
empty and classification degrades to minimal no-match behavior (everything
``irrelevant``). It never fabricates matches. The loud signal that the policy is
missing is ``scripts/verify_ai_value_chain_coverage.py`` (it asserts the file and
its required groups/layers/lens-mappings exist).
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

# Layer identifiers are part of this module's stable API (consumers compare
# against ai_value_chain.LAYER_*). They are structural enum values, not search
# keywords, so they stay in code; data/ai_value_chain_policy.json["layers"]
# declares the same manifest for the verifier and the lens/tier policy keys.
LAYER_HYPERSCALER = "hyperscaler_model"
LAYER_CUSTOM_CHIP = "custom_ai_chip"
LAYER_SEMI_SUPPLY = "ai_semiconductor_supply"
LAYER_DC_POWER = "ai_datacenter_power"
LAYER_DC_COOLING = "ai_datacenter_cooling"
LAYER_DC_CONSTRUCTION = "ai_datacenter_construction"
LAYER_SEMI_CLUSTER = "semiconductor_cluster_infra"
LAYER_DEV_FINANCE = "development_finance"
LAYER_SMART_CONSTRUCTION = "smart_construction_ai"
LAYER_GENERIC_AI = "generic_ai"
LAYER_IRRELEVANT = "irrelevant"

# Small standalone token detectors stay in code (regex helpers, not term lists):
# bare "AI"/"MS" tokens that substring keyword matching cannot express safely.
_AI_TOKEN_RE = re.compile(r"(?<![A-Za-z가-힣])AI(?![A-Za-z가-힣])")
_MS_TOKEN_RE = re.compile(r"(?<![A-Za-z])MS(?![A-Za-z])")

_POLICY_PATH = Path(__file__).resolve().parent.parent / "data" / "ai_value_chain_policy.json"


def _load_policy() -> dict:
    """Load the value-chain policy JSON. Returns {} if missing/malformed.

    No silent faking: an empty policy yields empty term groups, so the engine
    matches nothing rather than inventing matches.
    """
    try:
        data = json.loads(_POLICY_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


_POLICY = _load_policy()
_TERM_GROUPS = _POLICY.get("term_groups") if isinstance(_POLICY.get("term_groups"), dict) else {}
_LENS_MAPPING_RAW = _POLICY.get("lens_mapping") if isinstance(_POLICY.get("lens_mapping"), dict) else {}


def _terms(group: str) -> tuple[str, ...]:
    """Return a term group from policy as a tuple of non-empty strings."""
    vals = _TERM_GROUPS.get(group)
    if not isinstance(vals, (list, tuple)):
        return ()
    return tuple(v for v in vals if isinstance(v, str) and v)


# Named bindings to policy term groups — keep the classification logic readable
# while the actual keywords live in data/ai_value_chain_policy.json.
_HDEC_TERMS = _terms("hdec_terms")
_HYPERSCALER_TERMS = _terms("hyperscaler_terms")
_CHIP_TERMS = _terms("chip_terms")
_CUSTOM_CHIP_TERMS = _terms("custom_chip_terms")
_DC_TERMS = _terms("datacenter_terms")
_POWER_TERMS = _terms("power_terms")
_COOLING_TERMS = _terms("cooling_terms")
_CONSTRUCTION_TERMS = _terms("construction_terms")
_ENERGY_PLANT_TERMS = _terms("energy_plant_terms")
_CABLE_TERMS = _terms("cable_terms")
_SEMI_CLUSTER_TERMS = _terms("semiconductor_cluster_terms")
_DEV_TERMS = _terms("developer_terms")
_DEVELOPER_LENS_TERMS = _terms("developer_lens_terms")
_TRUST_TERMS = _terms("trust_terms")
_PIPELINE_TERMS = _terms("pipeline_terms")
_SMART_TERMS = _terms("smart_construction_terms")
_GENERIC_AI_TERMS = _terms("generic_ai_terms")
_STOCK_NOISE_TERMS = _terms("stock_noise_terms")
_HOUSING_TERMS = _terms("housing_terms")
_GLOBAL_TERMS = _terms("global_terms")
# Branch-local qualifier groups (also policy-driven, not embedded literals).
_CUSTOM_CHIP_MARKERS = _terms("custom_chip_markers")
_SEMI_INFRA_MARKERS = _terms("semi_infra_markers")
_SMART_AI_MARKERS = _terms("smart_ai_markers")
_CHIP_SUPPLY_MARKERS = _terms("chip_supply_markers")
_MODEL_MARKERS = _terms("model_markers")
_DC_POWER_PLANT_MARKERS = _terms("dc_power_plant_markers")
_DC_COOLING_PLANT_MARKERS = _terms("dc_cooling_plant_markers")
_DC_CONSTRUCTION_DEV_MARKERS = _terms("dc_construction_dev_markers")
_DC_CONSTRUCTION_EPC_MARKERS = _terms("dc_construction_epc_markers")
_SEMI_CLUSTER_PLANT_MARKERS = _terms("semi_cluster_plant_markers")
_SAFETY_QUALITY_MARKERS = _terms("safety_quality_markers")

# Per-layer base dashboard lenses (policy). Context-conditional lenses are added
# by recommended_lenses() below using the term groups.
_LENS_MAPPING = {
    layer: tuple(v for v in lenses if isinstance(v, str) and v)
    for layer, lenses in _LENS_MAPPING_RAW.items()
    if isinstance(lenses, list)
}


def _norm(text: str | None) -> str:
    return unicodedata.normalize("NFKC", text or "").casefold()


def _raw(title: str | None, source: str | None = "", snippet: str | None = "") -> str:
    return " ".join(p for p in (title or "", source or "", snippet or "") if p)


def _contains(text: str, terms: tuple[str, ...]) -> bool:
    low = _norm(text)
    return any(_norm(term) in low for term in terms)


def _hits(text: str, terms: tuple[str, ...]) -> list[str]:
    low = _norm(text)
    return [term for term in terms if _norm(term) in low]


def _has_ai_token(text: str) -> bool:
    return bool(_AI_TOKEN_RE.search(text or "")) or "인공지능" in (text or "")


def _has_hyperscaler(text: str) -> bool:
    if _contains(text, _HYPERSCALER_TERMS):
        return True
    return bool(_MS_TOKEN_RE.search(text or ""))


def _result(layer: str, tier: int, reason: str, *, is_ai: bool) -> dict:
    return {
        "is_ai_value_chain": bool(is_ai),
        "ai_value_chain_layer": layer,
        "hdec_relevance_tier": int(tier),
        "reason": reason,
    }


def classify_ai_value_chain(title: str, source: str = "", snippet: str = "") -> dict:
    """Classify one article into AI value-chain layer + HDEC relevance tier.

    Tier semantics: 1=HDEC direct, 2=construction-orderable AI infrastructure,
    3=AI semiconductor supply chain with construction impact potential,
    4=developer/trust/PF construction pipeline, 5=generic/low relevance.
    """
    text = _raw(title, source, snippet)
    if not text.strip():
        return _result(LAYER_IRRELEVANT, 5, "empty article metadata", is_ai=False)

    has_hdec = _contains(text, _HDEC_TERMS)
    has_hyper = _has_hyperscaler(text)
    has_ai = _has_ai_token(text) or _contains(text, _GENERIC_AI_TERMS)
    has_dc = _contains(text, _DC_TERMS)
    has_power = _contains(text, _POWER_TERMS) or _contains(text, _CABLE_TERMS)
    has_cooling = _contains(text, _COOLING_TERMS)
    has_build = _contains(text, _CONSTRUCTION_TERMS)
    has_energy_plant = _contains(text, _ENERGY_PLANT_TERMS)
    has_chip = _contains(text, _CHIP_TERMS)
    has_custom_chip = _contains(text, _CUSTOM_CHIP_TERMS) or (
        has_chip and has_hyper and _contains(text, _CUSTOM_CHIP_MARKERS)
    )
    has_semi_cluster = _contains(text, _SEMI_CLUSTER_TERMS)
    has_smart = _contains(text, _SMART_TERMS)
    has_dev = _contains(text, _DEV_TERMS)
    has_trust = _contains(text, _TRUST_TERMS)
    has_pipeline = _contains(text, _PIPELINE_TERMS)
    has_stock_noise = _contains(text, _STOCK_NOISE_TERMS)
    infra_context = has_dc or has_power or has_cooling or has_build or has_energy_plant
    semi_infra_context = has_semi_cluster and (has_power or has_build or _contains(text, _SEMI_INFRA_MARKERS))

    if has_stock_noise and not (infra_context or has_custom_chip or semi_infra_context):
        return _result(LAYER_IRRELEVANT, 5, "stock/listicle/coin-style AI mention without infrastructure context", is_ai=False)

    if has_dev or has_trust:
        if has_dc or has_semi_cluster or has_ai:
            if has_power:
                return _result(LAYER_DC_POWER, 2, "development/PF signal tied to AI data-center power infrastructure", is_ai=True)
            return _result(LAYER_DC_CONSTRUCTION, 2, "development/PF signal tied to AI data-center or semiconductor project pipeline", is_ai=True)
        if has_pipeline or has_hdec:
            return _result(LAYER_DEV_FINANCE, 4, "developer/trust/PF construction pipeline signal", is_ai=False)

    if has_hdec and (has_ai or has_dc or has_chip or has_smart or has_semi_cluster):
        if has_smart:
            return _result(LAYER_SMART_CONSTRUCTION, 1, "HDEC direct smart-construction or field-AI signal", is_ai=True)
        if has_cooling:
            return _result(LAYER_DC_COOLING, 1, "HDEC direct AI/data-center cooling signal", is_ai=True)
        if has_power or has_energy_plant:
            return _result(LAYER_DC_POWER, 1, "HDEC direct AI/data-center power or energy infrastructure signal", is_ai=True)
        if has_dc or has_build:
            return _result(LAYER_DC_CONSTRUCTION, 1, "HDEC direct AI/data-center construction or EPC signal", is_ai=True)
        if has_chip or has_semi_cluster:
            return _result(LAYER_SEMI_SUPPLY, 1, "HDEC direct semiconductor/AI supply-chain signal", is_ai=True)

    if has_smart and (has_ai or _contains(text, _SMART_AI_MARKERS)):
        return _result(LAYER_SMART_CONSTRUCTION, 2, "smart-construction or field-AI operating signal", is_ai=True)

    if has_dc and (has_hyper or has_ai or has_power or has_cooling or has_build or has_energy_plant):
        if has_power or has_energy_plant or _contains(text, _CABLE_TERMS):
            return _result(LAYER_DC_POWER, 2, "AI/hyperscaler data-center power, grid, cable, or generation signal", is_ai=True)
        if has_cooling:
            return _result(LAYER_DC_COOLING, 2, "AI/hyperscaler data-center cooling signal", is_ai=True)
        return _result(LAYER_DC_CONSTRUCTION, 2, "AI/hyperscaler data-center capex, site, lease, construction, or EPC signal", is_ai=True)

    if semi_infra_context:
        return _result(LAYER_SEMI_CLUSTER, 3, "semiconductor cluster/fab infrastructure signal", is_ai=True)

    if has_custom_chip:
        return _result(LAYER_CUSTOM_CHIP, 3, "custom AI chip or hyperscaler silicon supply-chain signal", is_ai=True)

    if has_chip and (has_ai or has_hyper or _contains(text, _CHIP_SUPPLY_MARKERS)):
        return _result(LAYER_SEMI_SUPPLY, 3, "AI semiconductor supply-chain signal", is_ai=True)

    if has_hyper and (has_ai or _contains(text, _MODEL_MARKERS)):
        return _result(LAYER_GENERIC_AI, 5, "generic hyperscaler/model AI signal without infrastructure link", is_ai=True)

    if has_ai:
        return _result(LAYER_GENERIC_AI, 5, "generic AI signal without construction, infrastructure, or semiconductor link", is_ai=True)

    return _result(LAYER_IRRELEVANT, 5, "no AI value-chain or construction-pipeline signal", is_ai=False)


def is_executive_ai_candidate(classification: dict) -> bool:
    """True when an AI value-chain item belongs on executive AI surfaces."""
    if not classification or not classification.get("is_ai_value_chain"):
        return False
    if classification.get("ai_value_chain_layer") == LAYER_GENERIC_AI:
        return False
    return int(classification.get("hdec_relevance_tier") or 5) <= 3


def hdec_relevance_sort_key(classification: dict) -> tuple:
    """Sort key: lower HDEC relevance tier first, generic/irrelevant last."""
    layer = (classification or {}).get("ai_value_chain_layer") or LAYER_IRRELEVANT
    tier = int((classification or {}).get("hdec_relevance_tier") or 5)
    generic_penalty = 1 if layer in {LAYER_GENERIC_AI, LAYER_IRRELEVANT} else 0
    return (tier, generic_penalty, layer)


def recommended_lenses(title: str, source: str = "", snippet: str = "") -> set[str]:
    """Return dashboard lens hints implied by the classifier.

    Base lenses per layer come from policy (lens_mapping); context-conditional
    lenses (global_business/plant/developers/trust_companies/building_housing/
    safety_quality) are refined here using the policy term groups. The returned
    keys intentionally use the existing dashboard lens vocabulary.
    """
    text = _raw(title, source, snippet)
    cls = classify_ai_value_chain(title, source, snippet)
    layer = cls["ai_value_chain_layer"]
    lenses: set[str] = set(_LENS_MAPPING.get(layer, ()))

    if layer in {LAYER_DC_POWER, LAYER_DC_COOLING}:
        if _contains(text, _GLOBAL_TERMS):
            lenses.add("global_business")
    if layer == LAYER_DC_POWER and (
            _contains(text, _ENERGY_PLANT_TERMS) or _contains(text, _DC_POWER_PLANT_MARKERS)):
        lenses.add("plant")
    if layer == LAYER_DC_COOLING and _contains(text, _DC_COOLING_PLANT_MARKERS):
        lenses.add("plant")
    if layer == LAYER_DC_CONSTRUCTION:
        if _contains(text, _GLOBAL_TERMS):
            lenses.add("global_business")
        if _contains(text, _DC_CONSTRUCTION_DEV_MARKERS):
            lenses.add("development_business")
        if _contains(text, _DC_CONSTRUCTION_EPC_MARKERS):
            lenses.add("global_business" if _contains(text, _GLOBAL_TERMS) else "development_business")

    if layer == LAYER_SEMI_CLUSTER:
        if _contains(text, _SEMI_CLUSTER_PLANT_MARKERS):
            lenses.add("plant")
    if layer in {LAYER_CUSTOM_CHIP, LAYER_SEMI_SUPPLY}:
        if _contains(text, _POWER_TERMS):
            lenses.add("new_energy")
        if _contains(text, _SEMI_CLUSTER_TERMS):
            lenses.update({"civil_infrastructure", "plant"})

    if layer == LAYER_DEV_FINANCE or _contains(text, _DEV_TERMS + _TRUST_TERMS):
        if _contains(text, _DEVELOPER_LENS_TERMS):
            lenses.add("developers")
        if _contains(text, _TRUST_TERMS):
            lenses.add("trust_companies")
        lenses.add("development_business")

    if layer == LAYER_SMART_CONSTRUCTION:
        if _contains(text, _HOUSING_TERMS):
            lenses.add("building_housing")
        if _contains(text, _SAFETY_QUALITY_MARKERS):
            lenses.add("safety_quality")

    return lenses


# Custom AI chip is inherently hyperscaler silicon (Broadcom/ASIC custom designs),
# so it qualifies on the layer alone. Every other value-chain layer must actually
# name a hyperscaler or a chip VENDOR to count as US big-tech / AI-chip coverage —
# a bare domestic data-center or semiconductor-cluster infrastructure story (no
# named vendor) is NOT the hyperscaler gap and stays in the primary radar / civil
# banks. Uses the precise chip_vendor_terms group (not the generic chip_terms,
# which includes words like "반도체"/"메모리") to avoid over-flagging.
_VALUE_CHAIN_NAMED_LAYERS = frozenset({
    LAYER_SEMI_SUPPLY, LAYER_SEMI_CLUSTER, LAYER_DC_POWER, LAYER_DC_COOLING,
    LAYER_DC_CONSTRUCTION, LAYER_HYPERSCALER,
})
_CHIP_VENDOR_TERMS = _terms("chip_vendor_terms")


def is_hyperscaler_value_chain(title: str, source: str = "", snippet: str = "") -> bool:
    """Policy-driven: is this a US big-tech / named-AI-chip-vendor value-chain signal?

    True for the custom AI chip layer (inherently hyperscaler silicon) and for the
    AI-semiconductor-supply / cluster / data-center power/cooling/construction layers
    WHEN a hyperscaler (OpenAI·Anthropic·MS·AWS·Google·Meta·Oracle·xAI) or a named
    AI-chip vendor (NVIDIA·Broadcom·TSMC·HBM·SK하이닉스·삼성전자·Micron·AMD) is named.
    Reads only the policy term groups already loaded above — no hardcoded domain
    keywords (D7-S2 engine/policy split). The dashboard uses this to reserve AI-bank
    slots so domestic construction stories cannot crowd out hyperscaler / chip
    coverage (D7-U). Generic model/app, irrelevant, and unnamed domestic data-center
    or semiconductor-cluster stories return False — they are already covered by the
    primary AI radar / civil banks and must not be treated as reserved value chain.
    """
    cls = classify_ai_value_chain(title, source, snippet)
    layer = cls["ai_value_chain_layer"]
    if layer == LAYER_CUSTOM_CHIP:
        return True
    if layer in _VALUE_CHAIN_NAMED_LAYERS:
        # The hyperscaler / vendor name must be in the STORY (title/snippet), not the
        # source — Google News reports source="Google", which would otherwise
        # false-match a domestic cluster story to the hyperscaler term "Google".
        text = _raw(title, "", snippet)
        return _has_hyperscaler(text) or _contains(text, _CHIP_VENDOR_TERMS)
    return False
