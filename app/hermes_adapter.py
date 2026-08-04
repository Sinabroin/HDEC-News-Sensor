"""Optional Hermes retrieval/reporting adapter (D7-AK-6E R4-R7 §9).

Hermes may index the sanitized human-editorial corpus and answer similarity
retrievals or drift reports, but it is **never a production executor**:

* it never approves an article;
* it never sends a message;
* it never modifies production sent state;
* it never merges or deploys code.

The HDEC repository remains the authoritative store. When Hermes is disabled
(default: ``HERMES_EDITORIAL_MEMORY_ENABLED=0``), unset, unreachable, or
returns a malformed response, retrieval falls back to the local versioned
corpus and deterministic local retrieval — selection always works with no
fail-open external dependency. The adapter performs no live Hermes writes;
transports are injected, so development/testing uses a fake local transport.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from app import editorial_memory

ENV_FLAG = "HERMES_EDITORIAL_MEMORY_ENABLED"

#: A transport receives one JSON-safe request dict and returns a response
#: dict. It must be read-only on the Hermes side; this adapter never exposes
#: a write path.
Transport = Callable[[Mapping[str, Any]], Mapping[str, Any]]


@dataclass(frozen=True)
class AdapterRetrieval:
    mode: str  # "hermes" | "local_fallback" | "local_disabled"
    result: editorial_memory.RetrievalResult
    detail: str = ""


def hermes_enabled(env: Mapping[str, str] | None = None) -> bool:
    env = env if env is not None else os.environ
    return str(env.get(ENV_FLAG, "0")).strip() == "1"


class HermesEditorialMemoryAdapter:
    """Read-only retrieval adapter with a mandatory local fallback."""

    def __init__(
        self,
        *,
        enabled: bool | None = None,
        transport: Transport | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self.enabled = hermes_enabled(env) if enabled is None else bool(enabled)
        self.transport = transport
        self.live_writes = 0  # structurally always zero; asserted by verifiers

    def retrieve(
        self,
        article: Mapping[str, Any],
        corpus: editorial_memory.Corpus,
        *,
        k: int = 3,
        query_label: str = "",
    ) -> AdapterRetrieval:
        local = editorial_memory.retrieve(article, corpus, k=k)
        if not self.enabled:
            return AdapterRetrieval("local_disabled", local, "hermes disabled")
        if self.transport is None:
            return AdapterRetrieval(
                "local_fallback", local, "no transport configured"
            )
        try:
            features = editorial_memory.deterministic_features(article)
            sanitized_features = {
                key: (sorted(value) if isinstance(value, frozenset) else value)
                for key, value in features.items()
            }
            response = self.transport(
                {
                    "action": "retrieve",
                    "read_only": True,
                    "corpus_digest": corpus.digest,
                    "query_labels": [query_label] if query_label else [],
                    "query_features": sanitized_features,
                    "k": k,
                }
            )
            if not isinstance(response, Mapping):
                raise TypeError("Hermes response must be a mapping")
            ids = response["matched_article_ids"]
            if (
                not isinstance(ids, list)
                or not ids
                or len(ids) > k * len(editorial_memory.EVIDENCE_LEVELS)
                or any(not isinstance(item, str) or not item for item in ids)
            ):
                raise TypeError("matched_article_ids must be a bounded string list")
        except Exception as exc:  # noqa: BLE001 — any failure → local fallback
            return AdapterRetrieval(
                "local_fallback", local, f"hermes error: {type(exc).__name__}"
            )
        by_id = {record.article_id: record for record in corpus.records}
        unknown = sorted(set(ids) - set(by_id))
        if unknown:
            return AdapterRetrieval(
                "local_fallback", local, "hermes returned unknown precedent ids"
            )
        matched = [by_id[item] for item in ids]

        def bucket(level: str) -> tuple[editorial_memory.RetrievalMatch, ...]:
            local_bucket = {
                editorial_memory.EVIDENCE_GOLD_PLUS: local.gold_plus,
                editorial_memory.EVIDENCE_GOLD_SELECTED: local.gold_selected,
                editorial_memory.EVIDENCE_NEAR_MISS: local.near_miss,
                editorial_memory.EVIDENCE_HARD_NEGATIVE: local.hard_negative,
                editorial_memory.EVIDENCE_SILVER: local.silver,
            }[level]
            merged: dict[str, editorial_memory.RetrievalMatch] = {
                item.record.article_id: item for item in local_bucket
            }
            for record in matched:
                if record.evidence_level == level:
                    merged[record.article_id] = editorial_memory.RetrievalMatch(
                        record, 1.0
                    )
            return tuple(
                sorted(
                    merged.values(),
                    key=lambda item: (-item.similarity, item.record.article_id),
                )[:k]
            )

        merged = editorial_memory.RetrievalResult(
            gold_plus=bucket(editorial_memory.EVIDENCE_GOLD_PLUS),
            gold_selected=bucket(editorial_memory.EVIDENCE_GOLD_SELECTED),
            near_miss=bucket(editorial_memory.EVIDENCE_NEAR_MISS),
            hard_negative=bucket(editorial_memory.EVIDENCE_HARD_NEGATIVE),
            mode="hermes",
            silver=bucket(editorial_memory.EVIDENCE_SILVER),
        )
        return AdapterRetrieval("hermes", merged, "hermes retrieval merged")


def build_weekly_learning_report(
    corpus: editorial_memory.Corpus,
    *,
    decisions: list[dict] | None = None,
) -> str:
    """Human-readable weekly learning report (Hermes reporting duty).

    Pure text over the sanitized corpus: evidence-level counts, category
    distribution, and edition coverage — safe to publish internally."""
    decisions = decisions if decisions is not None else []
    lines = [
        "HDEC Editorial Memory — Weekly Learning Report",
        f"corpus_digest={corpus.digest[:16]} records={len(corpus.records)}",
    ]
    for level in editorial_memory.EVIDENCE_LEVELS:
        lines.append(f"- {level}: {len(corpus.by_level(level))}")
    categories: dict[str, int] = {}
    for record in corpus.records:
        if record.category:
            categories[record.category] = categories.get(record.category, 0) + 1
    for name in sorted(categories):
        lines.append(f"- category {name}: {categories[name]}")
    lines.append(f"- editions: {', '.join(corpus.editions)}")
    lines.append(f"- decision_records: {len(decisions)}")
    lines.append("- hermes_role: retrieval/reporting only (no approve/send/state)")
    return "\n".join(lines) + "\n"
