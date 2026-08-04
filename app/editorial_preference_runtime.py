"""Deterministic runtime for human editorial preference memory (R4-R7).

This is the single service the production selection paths (Teams push,
Daily Brief, Weekly T&I) call to consult the versioned human-editorial
corpus. The HDEC repository remains the source of truth; every decision is
deterministic, explainable, and fail-closed:

* The committed profile pointer is loaded and verified (pointer version,
  profile digest, corpus digest, builder version, activation status). Any
  verification failure disables memory — selection proceeds on the
  deterministic rules alone, never on unverified evidence.
* While the committed profile is inactive (``active=false``) every decision
  is an audit-only shadow: ``memory_active`` is false, the deterministic
  ranking stays authoritative, and memory can neither approve nor reject an
  article. The bounded adjustment each decision carries is what *would*
  apply, so callers can expose the would-change ranking.
* Activation is never implicit. Production call sites always load the
  committed pointer; :func:`preview_runtime` is the only way to load a
  different profile and it demands the exact file path *and* expected
  digest, which no production path supplies.
* Memory output is advisory ranking evidence only. It is applied strictly
  after the deterministic safety, publisher-direct, AI-centrality,
  hard-exclusion, relevance, importance, duplicate, and evidence gates in
  the owning modules, and the reorder helper is bounded so no adjustment
  can cross a gate it never sees.

No network, no sends, no state writes. Hermes participation is optional,
read-only, and injected (see :mod:`app.hermes_adapter`); any Hermes failure
falls back to local deterministic retrieval.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from app import editorial_memory, hermes_adapter

ROOT = Path(__file__).resolve().parents[1]

RUNTIME_VERSION = "editorial-preference-runtime-v1"
PRODUCT_TEAMS = editorial_memory.PRODUCT_TEAMS
PRODUCT_DAILY = editorial_memory.PRODUCT_DAILY
PRODUCT_WEEKLY = editorial_memory.PRODUCT_WEEKLY
SUPPORTED_POINTER_VERSIONS = frozenset({1})
SUPPORTED_BUILDER_VERSIONS = frozenset({"editorial-profile-builder-v1"})
DEFAULT_POINTER_PATH = (
    editorial_memory.DEFAULT_CORPUS_ROOT / "profiles" / "latest.json"
)

RECOMMEND_PREFER = "prefer"
RECOMMEND_NEUTRAL = "neutral"
RECOMMEND_AVOID = "avoid"

#: Bounded adjustment contract: the per-article preference adjustment is
#: clamped to ±MAX_PREFERENCE_ADJUSTMENT and, when a profile is active, moves
#: an already-eligible article by at most MEMORY_RANK_WEIGHT effective
#: positions relative to its deterministic neighbours — never across a gate.
MAX_PREFERENCE_ADJUSTMENT = 1.0
MEMORY_RANK_WEIGHT = 2.0

_GOLD_WEIGHT = 1.0
_SILVER_WEIGHT = 0.25
_NEAR_MISS_WEIGHT = -0.35
_HARD_NEGATIVE_WEIGHT = -1.0
_RECOMMEND_THRESHOLD = 0.15


@dataclass(frozen=True)
class PrecedentRef:
    """One committed-corpus precedent (sanitized short fields only)."""

    article_id: str
    evidence_level: str
    similarity: float
    title: str
    edition_key: str


@dataclass(frozen=True)
class PreferenceDecision:
    """Immutable product-specific preference decision (R4-R7 §2)."""

    product: str
    runtime_version: str
    profile_version: str
    profile_digest: str
    corpus_digest: str
    memory_active: bool
    local_retrieval_used: bool
    hermes_retrieval_used: bool
    approved_precedents: tuple[PrecedentRef, ...]
    rejected_precedents: tuple[PrecedentRef, ...]
    near_miss_precedents: tuple[PrecedentRef, ...]
    silver_precedents: tuple[PrecedentRef, ...]
    decisive_differences: tuple[str, ...]
    preference_score: float
    preference_adjustment: float
    recommendation: str
    reason_codes: tuple[str, ...]

    @property
    def approved_precedent_ids(self) -> tuple[str, ...]:
        return tuple(ref.article_id for ref in self.approved_precedents)

    @property
    def rejected_precedent_ids(self) -> tuple[str, ...]:
        return tuple(ref.article_id for ref in self.rejected_precedents)

    @property
    def near_miss_precedent_ids(self) -> tuple[str, ...]:
        return tuple(ref.article_id for ref in self.near_miss_precedents)

    @property
    def silver_precedent_ids(self) -> tuple[str, ...]:
        return tuple(ref.article_id for ref in self.silver_precedents)


@dataclass(frozen=True)
class RuntimeStatus:
    available: bool
    active: bool
    profile_version: str
    profile_digest: str
    corpus_digest: str
    detail: str


def _neutral_decision(product: str, status: RuntimeStatus, code: str) -> PreferenceDecision:
    return PreferenceDecision(
        product=product,
        runtime_version=RUNTIME_VERSION,
        profile_version=status.profile_version,
        profile_digest=status.profile_digest,
        corpus_digest=status.corpus_digest,
        memory_active=False,
        local_retrieval_used=False,
        hermes_retrieval_used=False,
        approved_precedents=(),
        rejected_precedents=(),
        near_miss_precedents=(),
        silver_precedents=(),
        decisive_differences=(),
        preference_score=0.0,
        preference_adjustment=0.0,
        recommendation=RECOMMEND_NEUTRAL,
        reason_codes=(code,),
    )


def _profile_digest(payload: Mapping[str, Any]) -> str:
    stable = {
        key: value
        for key, value in payload.items()
        if key not in {"build_timestamp", "profile_digest"}
    }
    return hashlib.sha256(
        json.dumps(stable, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _verify(
    pointer: Mapping[str, Any],
    profile: Mapping[str, Any],
    corpus: editorial_memory.Corpus,
) -> tuple[bool, bool, str]:
    """(verified, active, detail) — every check fails closed."""
    if int(pointer.get("pointer_version") or 0) not in SUPPORTED_POINTER_VERSIONS:
        return False, False, "unsupported_pointer_version"
    if str(profile.get("builder_version")) not in SUPPORTED_BUILDER_VERSIONS:
        return False, False, "unsupported_builder_version"
    recomputed = _profile_digest(profile)
    if recomputed != str(profile.get("profile_digest")):
        return False, False, "profile_digest_mismatch"
    if str(pointer.get("profile_digest")) != recomputed:
        return False, False, "pointer_digest_mismatch"
    if str(profile.get("corpus_digest")) != corpus.digest:
        return False, False, "corpus_digest_mismatch"
    active = bool(pointer.get("active")) and bool(profile.get("active"))
    return True, active, "verified"


def _precedents(
    matches: Sequence[editorial_memory.RetrievalMatch],
) -> tuple[PrecedentRef, ...]:
    return tuple(
        PrecedentRef(
            article_id=match.record.article_id,
            evidence_level=match.record.evidence_level,
            similarity=match.similarity,
            title=match.record.title[:80],
            edition_key=match.record.edition_key,
        )
        for match in matches
    )


def _best_similarity(matches: Sequence[editorial_memory.RetrievalMatch]) -> float:
    return matches[0].similarity if matches else 0.0


class EditorialPreferenceRuntime:
    """Verified, read-only preference memory for one process."""

    def __init__(
        self,
        *,
        corpus: editorial_memory.Corpus | None,
        profile: Mapping[str, Any] | None,
        status: RuntimeStatus,
        adapter: hermes_adapter.HermesEditorialMemoryAdapter | None = None,
    ) -> None:
        self._corpus = corpus
        self._profile = dict(profile) if profile else None
        self.status = status
        self._adapter = adapter

    @property
    def memory_active(self) -> bool:
        return self.status.available and self.status.active

    @property
    def profile_version(self) -> str:
        return self.status.profile_version

    def decide(
        self, product: str, article: Mapping[str, Any]
    ) -> PreferenceDecision:
        """One immutable, deterministic, fail-closed preference decision.

        Never raises into a selection path: internal errors return a neutral
        decision whose reason code names the failure."""
        if product not in editorial_memory.PRODUCTS:
            return _neutral_decision(product, self.status, "unknown_product")
        if not self.status.available or self._corpus is None:
            return _neutral_decision(
                product, self.status, f"memory_unavailable:{self.status.detail}"
            )
        try:
            return self._decide(product, article)
        except Exception as exc:  # noqa: BLE001 — fail closed, never fail open
            return _neutral_decision(
                product, self.status, f"memory_error_fail_closed:{type(exc).__name__}"
            )

    def _decide(self, product: str, article: Mapping[str, Any]) -> PreferenceDecision:
        corpus = self._corpus
        assert corpus is not None
        hermes_used = False
        reason_codes: list[str] = []
        if self._adapter is not None:
            adapter_result = self._adapter.retrieve(
                article,
                corpus,
                query_label=product,
            )
            retrieval = adapter_result.result
            hermes_used = adapter_result.mode == "hermes"
            if adapter_result.mode == "local_fallback":
                reason_codes.append("hermes_local_fallback")
            elif hermes_used:
                reason_codes.append("hermes_retrieval_merged")
        else:
            retrieval = editorial_memory.retrieve(article, corpus)

        assessment = editorial_memory.score_article(
            product, article, corpus, profile=self._profile, retrieval=retrieval
        )

        gold_sim = max(
            _best_similarity(retrieval.gold_plus),
            _best_similarity(retrieval.gold_selected),
        )
        silver_sim = _best_similarity(retrieval.silver)
        near_sim = _best_similarity(retrieval.near_miss)
        negative_sim = _best_similarity(retrieval.hard_negative)
        adjustment = (
            _GOLD_WEIGHT * gold_sim
            + _SILVER_WEIGHT * silver_sim
            + _NEAR_MISS_WEIGHT * near_sim
            + _HARD_NEGATIVE_WEIGHT * negative_sim
        )
        adjustment = round(
            max(-MAX_PREFERENCE_ADJUSTMENT, min(MAX_PREFERENCE_ADJUSTMENT, adjustment)),
            4,
        )

        if gold_sim > 0.0:
            reason_codes.append("gold_precedent_support")
        if silver_sim > 0.0:
            reason_codes.append("silver_weak_support")
        if near_sim > 0.0:
            # Near-miss similarity is contextual evidence, never terminal.
            reason_codes.append("near_miss_context")
        if negative_sim > 0.0:
            reason_codes.append("hard_negative_similarity")
        if not (gold_sim or silver_sim or near_sim or negative_sim):
            reason_codes.append("no_similar_precedent")
        if not self.memory_active:
            reason_codes.append("memory_shadow_only")

        if adjustment >= _RECOMMEND_THRESHOLD:
            recommendation = RECOMMEND_PREFER
        elif negative_sim > 0.0 and adjustment <= -_RECOMMEND_THRESHOLD:
            recommendation = RECOMMEND_AVOID
        else:
            # A Near-miss can lower bounded rank, but cannot by itself become
            # a terminal avoid recommendation. Only reviewed Hard-negative
            # evidence carries that stronger meaning.
            recommendation = RECOMMEND_NEUTRAL

        approved = _precedents(retrieval.gold_plus) + _precedents(
            retrieval.gold_selected
        )
        return PreferenceDecision(
            product=product,
            runtime_version=RUNTIME_VERSION,
            profile_version=self.status.profile_version,
            profile_digest=self.status.profile_digest,
            corpus_digest=self.status.corpus_digest,
            memory_active=self.memory_active,
            local_retrieval_used=True,
            hermes_retrieval_used=hermes_used,
            approved_precedents=approved,
            rejected_precedents=_precedents(retrieval.hard_negative),
            near_miss_precedents=_precedents(retrieval.near_miss),
            silver_precedents=_precedents(retrieval.silver),
            decisive_differences=(assessment.decisive_difference,),
            preference_score=assessment.preference_score,
            preference_adjustment=adjustment,
            recommendation=recommendation,
            reason_codes=tuple(reason_codes),
        )


def _load_runtime(
    pointer_path: Path,
    corpus_root: Path | None,
    *,
    env: Mapping[str, str] | None,
    transport: hermes_adapter.Transport | None,
    require_digest: str | None = None,
    pointer_override: Mapping[str, Any] | None = None,
) -> EditorialPreferenceRuntime:
    unavailable = RuntimeStatus(False, False, "", "", "", "")
    try:
        if pointer_override is not None:
            pointer = dict(pointer_override)
        else:
            pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
        profile_root = pointer_path.parent.resolve()
        profile_path = (
            pointer_path.parent / str(pointer.get("profile") or "")
        ).resolve()
        if profile_path.parent != profile_root:
            raise ValueError("profile pointer escaped its directory")
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        corpus = editorial_memory.load_corpus(corpus_root)
    except Exception as exc:  # noqa: BLE001 — unreadable memory is disabled memory
        status = RuntimeStatus(
            False, False, "", "", "", f"load_failed:{type(exc).__name__}"
        )
        return EditorialPreferenceRuntime(corpus=None, profile=None, status=status)

    verified, active, detail = _verify(pointer, profile, corpus)
    if verified and require_digest is not None and (
        str(profile.get("profile_digest")) != require_digest
    ):
        verified, active, detail = False, False, "preview_digest_mismatch"
    status = RuntimeStatus(
        available=verified,
        active=active,
        profile_version=str(profile.get("profile_version") or ""),
        profile_digest=str(profile.get("profile_digest") or ""),
        corpus_digest=corpus.digest,
        detail=detail,
    )
    adapter = hermes_adapter.HermesEditorialMemoryAdapter(
        transport=transport, env=env
    )
    return EditorialPreferenceRuntime(
        corpus=corpus if verified else None,
        profile=profile if verified else None,
        status=status,
        adapter=adapter if verified else None,
    )


def load_runtime(
    *,
    pointer_path: Path | None = None,
    corpus_root: Path | None = None,
    env: Mapping[str, str] | None = None,
    transport: hermes_adapter.Transport | None = None,
) -> EditorialPreferenceRuntime:
    """Load and verify the committed profile pointer (production entrypoint).

    The committed pointer currently carries ``active=false``, so this returns
    a shadow-only runtime; activation requires a separately reviewed commit
    that changes the pointer — never a code default."""
    return _load_runtime(
        pointer_path or DEFAULT_POINTER_PATH,
        corpus_root,
        env=env,
        transport=transport,
    )


def preview_runtime(
    profile_path: Path,
    *,
    expected_digest: str,
    corpus_root: Path | None = None,
    env: Mapping[str, str] | None = None,
    transport: hermes_adapter.Transport | None = None,
) -> EditorialPreferenceRuntime:
    """Explicit preview/test override (R4-R7 §3) — never a production path.

    Demands the exact profile file path *and* its expected digest; a mismatch
    fails closed. Production call sites only ever use the committed pointer
    via :func:`default_runtime`, so an active fixture profile can influence
    ranking exclusively inside tests and previews that opted in twice."""
    profile_path = Path(profile_path)
    pointer = {
        "pointer_version": 1,
        "profile": profile_path.name,
        "profile_digest": expected_digest,
        "active": True,
    }
    return _load_runtime(
        profile_path.parent / "__preview__.json",
        corpus_root,
        env=env,
        transport=transport,
        require_digest=expected_digest,
        pointer_override=pointer,
    )


_DEFAULT_RUNTIME: EditorialPreferenceRuntime | None = None


def default_runtime() -> EditorialPreferenceRuntime:
    """Process-wide runtime over the committed pointer (lazy, cached)."""
    global _DEFAULT_RUNTIME
    if _DEFAULT_RUNTIME is None:
        _DEFAULT_RUNTIME = load_runtime()
    return _DEFAULT_RUNTIME


def reset_default_runtime_cache() -> None:
    global _DEFAULT_RUNTIME
    _DEFAULT_RUNTIME = None


def memory_adjusted_order(
    count: int,
    adjustments: Sequence[float],
    *,
    group_of: Callable[[int], object] | None = None,
) -> list[int]:
    """Bounded memory reorder of already-eligible, already-ranked items.

    Returns the index order after applying each item's bounded adjustment as
    ``base_index - adjustment * MEMORY_RANK_WEIGHT`` (ties resolve to the
    deterministic base order). With ``group_of``, reordering is confined to
    contiguous runs sharing the same group key — the Teams path uses the
    importance level so memory can never move an article across an importance
    tier, only reorder peers below every deterministic gate."""
    indices = list(range(count))

    def reorder(block: list[int]) -> list[int]:
        position = {index: rank for rank, index in enumerate(block)}
        return sorted(
            block,
            key=lambda index: (
                position[index] - adjustments[index] * MEMORY_RANK_WEIGHT,
                position[index],
            ),
        )

    if group_of is None:
        return reorder(indices)
    ordered: list[int] = []
    start = 0
    while start < count:
        end = start
        key = group_of(start)
        while end < count and group_of(end) == key:
            end += 1
        ordered.extend(reorder(indices[start:end]))
        start = end
    return ordered
