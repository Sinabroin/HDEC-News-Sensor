"""Versioned human editorial preference memory (D7-AK-6E R4-R7).

One shared sanitized corpus (``data/editorial_learning/``) of human editorial
decisions — final Weekly Briefs (gold), first-stage candidate screens
(silver), near-misses, and production hard negatives — powering
retrieval-based cumulative preference learning with three product-specific
heads (Teams / Daily / Weekly T&I).

Hard contract (R4-R7 §7/§8):

* Retrieval and preference scores are **advisory ranking evidence only**.
  They can never bypass the deterministic safety, AI-centrality,
  publisher-direct, freshness, duplicate, or evidence gates — those remain
  the sole gatekeepers in their owning modules.
* No fine-tuning, no weight rewriting: learning is versioned retrieval over
  an append-only corpus, and every output is explainable (approved
  precedent / rejected precedent / decisive difference).

This module is a pure leaf: stdlib + app.ai_centrality only, read-only I/O
on the committed corpus, no network, no state writes.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from app import ai_centrality, public_institution_routing

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CORPUS_ROOT = ROOT / "data" / "editorial_learning"

PRODUCT_TEAMS = "teams"
PRODUCT_DAILY = "daily"
PRODUCT_WEEKLY = "weekly_tni"
PRODUCTS = (PRODUCT_TEAMS, PRODUCT_DAILY, PRODUCT_WEEKLY)

EVIDENCE_GOLD_PLUS = "gold_plus"
EVIDENCE_GOLD_SELECTED = "gold_selected"
EVIDENCE_SILVER = "silver_candidate"
EVIDENCE_NEAR_MISS = "near_miss"
EVIDENCE_HARD_NEGATIVE = "hard_negative"
EVIDENCE_LEVELS = (
    EVIDENCE_GOLD_PLUS,
    EVIDENCE_GOLD_SELECTED,
    EVIDENCE_SILVER,
    EVIDENCE_NEAR_MISS,
    EVIDENCE_HARD_NEGATIVE,
)

_TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣]{2,}")
_STOP_TOKENS = {
    "관련", "대한", "위한", "통해", "이번", "위해", "있다", "한다", "했다",
    "하는", "것은", "것이", "까지", "부터", "라며", "면서", "으로", "에서",
    "and", "the", "for", "with",
}

_SENSATIONAL_MARKERS = ("충격", "경악", "발칵", "초비상", "‘", "’", "…", "?!", "단독")
_SOCIETAL_WEEKLY_MARKERS = (
    "교육", "대학", "노동", "일자리", "고용", "규제", "법", "판례", "법조",
    "사회", "공공", "정부", "제도", "소비자", "물가", "과몰입", "윤리", "행정",
)
_CONFIRMED_ACTION_MARKERS = (
    "체결", "확정", "승인", "착공", "준공", "착수", "시행", "출시", "공개",
    "인수", "합병", "수주", "가동", "발효", "첫 삽", "신설", "투자",
)


@dataclass(frozen=True)
class CorpusRecord:
    article_id: str
    evidence_level: str
    product: str
    edition_key: str
    title: str
    human_summary: str
    category: str
    source: str
    canonical_url: str
    headline: bool
    human_order: int
    topic_labels: tuple[str, ...] = ()
    tokens: frozenset[str] = frozenset()
    source_class: str = public_institution_routing.SOURCE_CLASS_OTHER
    editorial_lane: str = public_institution_routing.LANE_MAIN
    public_institution_type: str = ""
    main_surface_eligible: bool = True
    teams_alert_eligible: bool = True
    tni_brief_eligible: bool = True
    tni_report_topic_eligible: bool = False
    default_surface: str = public_institution_routing.SURFACE_MAIN
    final_surface: str = ""
    final_category: str = ""
    promotion_reason: str = ""
    human_placement_override: bool = False
    human_placement_reason: str = ""


@dataclass(frozen=True)
class Corpus:
    records: tuple[CorpusRecord, ...]
    digest: str
    editions: tuple[str, ...]

    def by_level(self, level: str) -> tuple[CorpusRecord, ...]:
        return tuple(r for r in self.records if r.evidence_level == level)


@dataclass(frozen=True)
class RetrievalMatch:
    record: CorpusRecord
    similarity: float


@dataclass(frozen=True)
class RetrievalResult:
    gold_plus: tuple[RetrievalMatch, ...]
    gold_selected: tuple[RetrievalMatch, ...]
    near_miss: tuple[RetrievalMatch, ...]
    hard_negative: tuple[RetrievalMatch, ...]
    mode: str = "local"
    # R4-R7 runtime integration — silver first-stage candidates are weak
    # advisory evidence (weaker than gold, never terminal).
    silver: tuple[RetrievalMatch, ...] = ()


@dataclass(frozen=True)
class PreferenceAssessment:
    product: str
    preference_score: float
    rationale: tuple[str, ...]
    retrieval: RetrievalResult
    approved_precedent: str
    rejected_precedent: str
    decisive_difference: str
    deterministic_gates_bypassed: bool = field(default=False, init=False)


def _clean(value: object) -> str:
    return " ".join(str(value or "").split())


def tokenize(*parts: object) -> frozenset[str]:
    tokens = set()
    for part in parts:
        for token in _TOKEN_RE.findall(_clean(part).lower()):
            if token not in _STOP_TOKENS:
                tokens.add(token)
    return frozenset(tokens)


def article_identity(title: str, source: str, edition_key: str) -> str:
    payload = "\x1f".join((_clean(title), _clean(source), _clean(edition_key)))
    return "art-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _record_from_article(
    edition: Mapping[str, Any], article: Mapping[str, Any], product: str
) -> CorpusRecord:
    title = _clean(article.get("title"))
    return CorpusRecord(
        article_id=article_identity(
            title, _clean(article.get("source")), _clean(edition.get("edition_key"))
        ),
        evidence_level=_clean(article.get("evidence_level")) or EVIDENCE_SILVER,
        product=product,
        edition_key=_clean(edition.get("edition_key")),
        title=title,
        human_summary=_clean(article.get("human_summary")),
        category=_clean(article.get("category") or article.get("human_category")),
        source=_clean(article.get("source")),
        canonical_url=_clean(article.get("canonical_url")),
        headline=bool(article.get("headline")),
        human_order=int(article.get("order") or 0),
        topic_labels=tuple(article.get("topic_labels") or ()),
        tokens=tokenize(article.get("title"), article.get("human_summary")),
        source_class=_clean(article.get("source_class"))
        or public_institution_routing.SOURCE_CLASS_OTHER,
        editorial_lane=_clean(article.get("editorial_lane"))
        or public_institution_routing.LANE_MAIN,
        public_institution_type=_clean(article.get("public_institution_type")),
        main_surface_eligible=bool(article.get("main_surface_eligible", True)),
        teams_alert_eligible=bool(article.get("teams_alert_eligible", True)),
        tni_brief_eligible=bool(article.get("tni_brief_eligible", True)),
        tni_report_topic_eligible=bool(
            article.get("tni_report_topic_eligible", False)
        ),
        default_surface=_clean(article.get("default_surface"))
        or public_institution_routing.SURFACE_MAIN,
        final_surface=_clean(article.get("final_surface")),
        final_category=_clean(
            article.get("final_category") or article.get("category")
        ),
        promotion_reason=_clean(article.get("promotion_reason")),
        human_placement_override=bool(article.get("human_placement_override")),
        human_placement_reason=_clean(article.get("human_placement_reason")),
    )


def _hard_negative_records() -> list[CorpusRecord]:
    fixture_path = ROOT / "data" / "observed_false_positive_fixtures.json"
    if not fixture_path.exists():
        return []
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    records = []
    for article in payload.get("articles", []):
        if article.get("expected", {}).get("teams_eligible"):
            continue
        title = _clean(article.get("title"))
        records.append(
            CorpusRecord(
                article_id=article_identity(
                    title, _clean(article.get("source")), "observed-production"
                ),
                evidence_level=EVIDENCE_HARD_NEGATIVE,
                product=PRODUCT_TEAMS,
                edition_key="observed-production",
                title=title,
                human_summary=_clean(article.get("snippet")),
                category="",
                source=_clean(article.get("source")),
                canonical_url=_clean(article.get("url")),
                headline=False,
                human_order=0,
                topic_labels=(
                    _clean(article.get("expected", {}).get("reason_class")),
                    _clean(article.get("expected", {}).get("reason_detail")),
                ),
                tokens=tokenize(article.get("title"), article.get("snippet")),
            )
        )
    return records


def load_corpus(root: Path | None = None) -> Corpus:
    """Load every committed corpus record deterministically (read-only)."""
    root = Path(root) if root else DEFAULT_CORPUS_ROOT
    records: list[CorpusRecord] = []
    for directory, default_product in (
        (root / "final_briefs", PRODUCT_WEEKLY),
        (root / "candidate_pools", PRODUCT_WEEKLY),
    ):
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*.json")):
            if path.name == "manifest.json":
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
            product = _clean(payload.get("product")) or default_product
            for article in payload.get("articles", []):
                records.append(_record_from_article(payload, article, product))
    records.extend(_hard_negative_records())
    digest = hashlib.sha256(
        "\n".join(
            sorted(f"{r.article_id}|{r.evidence_level}|{r.title}" for r in records)
        ).encode("utf-8")
    ).hexdigest()
    editions = tuple(sorted({r.edition_key for r in records}))
    return Corpus(records=tuple(records), digest=digest, editions=editions)


# ---------------------------------------------------------------------------
# Deterministic features (R4-R7 §7).
# ---------------------------------------------------------------------------
def deterministic_features(article: Mapping[str, Any]) -> dict[str, Any]:
    decision = ai_centrality.classify(article)
    title = _clean(article.get("title"))
    summary = _clean(article.get("summary") or article.get("snippet"))
    text = f"{title} {summary}"
    public_route = public_institution_routing.classify(article)
    return {
        "ai_centrality": decision.level,
        "ai_centrality_excluded": decision.exclusion,
        "surface_market_article": decision.surface_market,
        "structural_ai_causal_event": decision.structural_event,
        "confirmed_action": any(m in text for m in _CONFIRMED_ACTION_MARKERS),
        "factual_specificity": min(
            1.0, len(re.findall(r"[0-9][0-9,.]*\s*(?:조|억|만|%|mw|gw|명|건|달러|원|개)", text.lower())) / 3.0
        ),
        "title_sensationalism": min(
            1.0, sum(text.count(m) for m in _SENSATIONAL_MARKERS) / 4.0
        ),
        "societal_regulatory_weight": min(
            1.0, sum(1 for m in _SOCIETAL_WEEKLY_MARKERS if m in text) / 3.0
        ),
        "tokens": tokenize(title, summary),
        # Placement evidence is preserved for product heads and audit, but
        # official/public status itself contributes no positive or negative
        # score. Authority and editorial preference remain separate.
        "source_class": public_route.source_class,
        "editorial_lane": public_route.editorial_lane,
        "public_institution_type": public_route.public_institution_type,
        "main_surface_eligible": public_route.main_surface_eligible,
        "teams_alert_eligible": public_route.teams_alert_eligible,
        "tni_brief_eligible": public_route.tni_brief_eligible,
        "tni_report_topic_eligible": public_route.tni_report_topic_eligible,
        "default_surface": public_route.default_surface,
        "final_category": public_route.final_category,
        "promotion_reason": public_route.promotion_reason,
    }


def _similarity(tokens: frozenset[str], record: CorpusRecord) -> float:
    if not tokens or not record.tokens:
        return 0.0
    overlap = len(tokens & record.tokens)
    union = len(tokens | record.tokens)
    return round(overlap / union, 4) if union else 0.0


def retrieve(
    article: Mapping[str, Any],
    corpus: Corpus,
    *,
    k: int = 3,
    features: Mapping[str, Any] | None = None,
) -> RetrievalResult:
    features = features or deterministic_features(article)
    tokens = features["tokens"]

    def top(level: str) -> tuple[RetrievalMatch, ...]:
        matches = sorted(
            (
                RetrievalMatch(record, _similarity(tokens, record))
                for record in corpus.by_level(level)
            ),
            key=lambda m: (-m.similarity, m.record.article_id),
        )
        return tuple(m for m in matches[:k] if m.similarity > 0.0)

    return RetrievalResult(
        gold_plus=top(EVIDENCE_GOLD_PLUS),
        gold_selected=top(EVIDENCE_GOLD_SELECTED),
        near_miss=top(EVIDENCE_NEAR_MISS),
        hard_negative=top(EVIDENCE_HARD_NEGATIVE),
        mode="local",
        silver=top(EVIDENCE_SILVER),
    )


def _best(matches: tuple[RetrievalMatch, ...]) -> RetrievalMatch | None:
    return matches[0] if matches else None


def explain(
    features: Mapping[str, Any], retrieval: RetrievalResult
) -> tuple[str, str, str]:
    """(approved precedent, rejected precedent, decisive difference)."""
    approved = _best(retrieval.gold_plus) or _best(retrieval.gold_selected)
    rejected = _best(retrieval.hard_negative)
    approved_line = (
        f"{approved.record.title} ({approved.record.edition_key}, "
        f"sim={approved.similarity})"
        if approved
        else "(no similar approved precedent)"
    )
    rejected_line = (
        f"{rejected.record.title} (observed production false positive, "
        f"sim={rejected.similarity})"
        if rejected
        else "(no similar rejected precedent)"
    )
    if features.get("structural_ai_causal_event"):
        difference = (
            "current article proves an independent structural AI causal event "
            f"({features['structural_ai_causal_event']}) rather than a "
            "market-price reaction"
        )
    elif features.get("surface_market_article"):
        difference = (
            "current article is a surface market/earnings form without an "
            "independent structural AI causal event"
        )
    elif features.get("ai_centrality") in ai_centrality.CENTRAL_LEVELS:
        difference = (
            "current article carries direct AI title/lead evidence "
            f"({features['ai_centrality']})"
        )
    else:
        difference = (
            "current article lacks material AI evidence in its title/lead "
            f"({features.get('ai_centrality')})"
        )
    return approved_line, rejected_line, difference


# ---------------------------------------------------------------------------
# Product-specific heads (R4-R7 §6) — advisory preference only.
# ---------------------------------------------------------------------------
def _head_weights(product: str, profile: Mapping[str, Any] | None) -> Mapping[str, float]:
    defaults = {
        PRODUCT_TEAMS: {
            "central": 2.0, "confirmed": 2.0, "structural": 1.5,
            "gold_sim": 1.5, "negative_sim": -2.5, "sensational": -1.0,
            "societal": 0.0, "specificity": 1.0,
        },
        PRODUCT_DAILY: {
            "central": 2.0, "confirmed": 1.0, "structural": 1.0,
            "gold_sim": 1.5, "negative_sim": -2.0, "sensational": -0.5,
            "societal": 0.5, "specificity": 1.0,
        },
        PRODUCT_WEEKLY: {
            "central": 1.5, "confirmed": 0.5, "structural": 1.0,
            "gold_sim": 2.0, "negative_sim": -2.0, "sensational": -0.5,
            "societal": 1.5, "specificity": 0.5,
        },
    }[product]
    if profile:
        overrides = (
            profile.get("product_heads", {}).get(product, {}).get("weights", {})
        )
        merged = dict(defaults)
        for key, value in overrides.items():
            if key in merged:
                merged[key] = float(value)
        return merged
    return defaults


def score_article(
    product: str,
    article: Mapping[str, Any],
    corpus: Corpus,
    *,
    profile: Mapping[str, Any] | None = None,
    retrieval: RetrievalResult | None = None,
) -> PreferenceAssessment:
    """Advisory preference score for one product. NEVER an eligibility gate.

    Teams optimizes confirmed-new-event precision; Daily optimizes the prior
    24-hour AI agenda with diversity; Weekly optimizes strategic weekly change
    across policy/society/education/labor/organization/technology — a valid
    Weekly topic may not be urgent enough for Teams, so the Teams
    immediate-alert hard gate is deliberately NOT reused here."""
    if product not in PRODUCTS:
        raise ValueError(f"unknown product: {product}")
    features = deterministic_features(article)
    retrieval = retrieval or retrieve(article, corpus, features=features)
    weights = _head_weights(product, profile)

    gold = _best(retrieval.gold_plus) or _best(retrieval.gold_selected)
    negative = _best(retrieval.hard_negative)
    central = features["ai_centrality"] in ai_centrality.CENTRAL_LEVELS

    score = 0.0
    rationale: list[str] = []
    if central:
        score += weights["central"]
        rationale.append(f"ai_centrality:{features['ai_centrality']}")
    else:
        rationale.append(
            f"fails_deterministic_ai_gate:{features['ai_centrality']}"
            f"{'/' + features['ai_centrality_excluded'] if features['ai_centrality_excluded'] else ''}"
        )
    if features["confirmed_action"]:
        score += weights["confirmed"]
        rationale.append("confirmed_action")
    if features["structural_ai_causal_event"]:
        score += weights["structural"]
        rationale.append(f"structural:{features['structural_ai_causal_event']}")
    if gold:
        score += weights["gold_sim"] * gold.similarity
        rationale.append(f"gold_precedent_sim:{gold.similarity}")
    if negative:
        score += weights["negative_sim"] * negative.similarity
        rationale.append(f"hard_negative_sim:{negative.similarity}")
    score += weights["societal"] * features["societal_regulatory_weight"]
    if features["societal_regulatory_weight"] and weights["societal"]:
        rationale.append(
            f"societal_regulatory:{features['societal_regulatory_weight']}"
        )
    score += weights["specificity"] * features["factual_specificity"]
    score += weights["sensational"] * features["title_sensationalism"]

    approved_line, rejected_line, difference = explain(features, retrieval)
    return PreferenceAssessment(
        product=product,
        preference_score=round(score, 4),
        rationale=tuple(rationale),
        retrieval=retrieval,
        approved_precedent=approved_line,
        rejected_precedent=rejected_line,
        decisive_difference=difference,
    )


# ---------------------------------------------------------------------------
# Append-only decisions (R4-R7 §11).
# ---------------------------------------------------------------------------
def load_decisions(root: Path | None = None) -> list[dict]:
    root = Path(root) if root else DEFAULT_CORPUS_ROOT
    path = root / "decisions.jsonl"
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))
    return records


def append_decision(record: Mapping[str, Any], root: Path | None = None) -> None:
    """Append one decision record; existing lines are never modified.

    Corrections must append a superseding record referencing the original
    ``decision_id`` via ``supersedes`` — never rewrite history."""
    root = Path(root) if root else DEFAULT_CORPUS_ROOT
    path = root / "decisions.jsonl"
    required = {"decision_id", "record_version", "recorded_at", "record_type", "edition_key"}
    missing = required - set(record)
    if missing:
        raise ValueError(f"decision record missing fields: {sorted(missing)}")
    existing_ids = {r.get("decision_id") for r in load_decisions(root)}
    if record["decision_id"] in existing_ids:
        return
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(record), ensure_ascii=False) + "\n")


def reconcile_pool_with_finals(
    pool_edition: str, corpus: Corpus
) -> dict[str, Any]:
    """R4-R7 §1-C — reconcile a silver candidate pool against final Briefs.

    A silver candidate found in any final becomes gold (headline → gold_plus);
    when the pool's own final edition is unavailable the remainder stays
    silver and is reported honestly as ``final_unavailable`` — never invented
    near-misses."""
    pool = [
        r
        for r in corpus.records
        if r.edition_key == pool_edition and r.evidence_level == EVIDENCE_SILVER
    ]
    finals = [
        r
        for r in corpus.records
        if r.evidence_level in {EVIDENCE_GOLD_PLUS, EVIDENCE_GOLD_SELECTED}
    ]
    same_edition_final = any(r.edition_key == pool_edition for r in finals)
    promotions = []
    for candidate in pool:
        best, best_sim = None, 0.0
        for final in finals:
            sim = _similarity(candidate.tokens, final)
            if sim > best_sim:
                best, best_sim = final, sim
        if best is not None and best_sim >= 0.6:
            promotions.append(
                {
                    "article_id": candidate.article_id,
                    "title": candidate.title,
                    "promoted_to": best.evidence_level,
                    "matched_final_edition": best.edition_key,
                    "similarity": best_sim,
                }
            )
    unresolved = len(pool) - len(promotions)
    return {
        "pool_edition": pool_edition,
        "pool_size": len(pool),
        "same_edition_final_available": same_edition_final,
        "promotions": promotions,
        "unresolved_status": (
            "near_miss" if same_edition_final else "silver_final_unavailable"
        ),
        "unresolved_count": unresolved,
    }
