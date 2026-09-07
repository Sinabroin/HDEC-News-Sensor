"""D7-AK-6F-C2 collector-to-outbox shadow orchestration.

This module connects the repository's existing collector/scoring/insight pipeline to
the C1 canonical runtime and SQLite outbox. It never imports a delivery transport,
never claims an outbox row, and never mutates the legacy production state.

Collector modules are imported lazily only after the caller has selected an isolated
collector DB and an explicit mock/live collection mode.
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from app.runtime_models import (
    CanonicalArticle,
    NewsEvent,
    RuntimeHeartbeat,
    deterministic_id,
    sha256_text,
    stable_json,
    utc_now_iso,
)
from app.runtime_policy import RuntimePolicyEngine
from app.runtime_sqlite import SQLiteRuntimeStore


SHADOW_CHANNEL = "shadow_teams_email"
_COLLECTOR_MODULES = (
    "app.config",
    "app.db",
    "app.collector",
    "app.scoring",
    "app.insight",
)


class ShadowOrchestrationError(RuntimeError):
    """Raised when the shadow-only orchestration contract is violated."""


def _clean(value: object) -> str:
    return " ".join(str(value or "").split())


def _metadata(row: Mapping[str, Any]) -> dict[str, Any]:
    raw = row.get("source_metadata_json")
    if isinstance(raw, Mapping):
        return dict(raw)
    try:
        decoded = json.loads(str(raw or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return dict(decoded) if isinstance(decoded, Mapping) else {}


def _event_for(article: CanonicalArticle, canonical_article_id: str) -> NewsEvent:
    event_key = deterministic_id("event", canonical_article_id, length=24)
    material_signature = sha256_text(
        stable_json(
            {
                "canonical_article_id": canonical_article_id,
                "revision": "initial",
            }
        )
    )
    return NewsEvent(
        event_cluster_key=event_key,
        primary_article_id=canonical_article_id,
        event_type="collector_article_signal",
        headline=article.title,
        material_signature=material_signature,
        first_seen_at=article.observed_at,
        last_seen_at=article.observed_at,
        attributes={
            "canonical_article_id": canonical_article_id,
            "provider_article_id": article.article_id,
            "canonical_url": article.canonical_url,
            "source": article.source,
            "source_type": article.source_type,
            "resolver_authoritative": False,
            "shadow_only": True,
        },
    )


def _prepare_collector_environment(
    *,
    collector_mode: str,
    collector_db_path: Path,
    allow_live_collector: bool,
) -> None:
    normalized_mode = _clean(collector_mode).lower()
    if normalized_mode not in {"mock", "live"}:
        raise ShadowOrchestrationError("collector_mode must be mock or live")
    if normalized_mode == "live" and not allow_live_collector:
        raise ShadowOrchestrationError(
            "live collector requires explicit allow_live_collector=True"
        )

    already_loaded = [name for name in _COLLECTOR_MODULES if name in sys.modules]
    if already_loaded:
        raise ShadowOrchestrationError(
            "collector configuration modules loaded before isolated environment: "
            + ",".join(already_loaded)
        )

    collector_db_path.parent.mkdir(parents=True, exist_ok=True)
    os.environ["APP_MODE"] = "mock"
    os.environ["NEWS_MODE"] = normalized_mode
    os.environ["DB_PATH"] = str(collector_db_path)


def _collector_stats_summary(value: Mapping[str, Any]) -> dict[str, Any]:
    provider_status = value.get("provider_status")
    return {
        "collected": int(value.get("collected") or 0),
        "deduplicated": int(value.get("deduplicated") or 0),
        "inserted": int(value.get("inserted") or 0),
        "news_data_mode": _clean(value.get("news_data_mode")) or "unknown",
        "news_source": _clean(value.get("news_source")) or "unknown",
        "attempted_mode": _clean(value.get("attempted_mode")) or "unknown",
        "fallback_used": bool(value.get("fallback_used")),
        "provider_status": (
            dict(provider_status) if isinstance(provider_status, Mapping) else {}
        ),
    }


def run_shadow_orchestration(
    *,
    collector_mode: str,
    collector_db_path: Path,
    shadow_db_path: str | Path,
    run_id: str,
    allow_live_collector: bool = False,
    shadow_channel: str = SHADOW_CHANNEL,
) -> dict[str, Any]:
    """Run the existing collector and write only to a separate shadow SQLite DB."""

    normalized_run_id = _clean(run_id)
    if not normalized_run_id:
        raise ShadowOrchestrationError("run_id must be non-empty")
    if _clean(shadow_channel) != SHADOW_CHANNEL:
        raise ShadowOrchestrationError(
            f"shadow channel must remain exactly {SHADOW_CHANNEL}"
        )

    _prepare_collector_environment(
        collector_mode=collector_mode,
        collector_db_path=Path(collector_db_path),
        allow_live_collector=allow_live_collector,
    )

    # Delayed import is part of the safety contract: DB_PATH and NEWS_MODE are now fixed.
    from app import collector, db, insight, scoring  # noqa: PLC0415

    db.init_db()
    collected = collector.run(collector_mode)
    score_stats = scoring.score_all()
    insight.generate_all()
    observations = db.fetch_articles_with_scores()

    collector_summary = _collector_stats_summary(collected)
    policy = RuntimePolicyEngine()
    decision_counts: Counter[str] = Counter()
    processed = 0
    skipped_invalid = 0
    outbox_created = 0
    results: list[dict[str, Any]] = []

    shadow_path_text = str(shadow_db_path)
    if shadow_path_text != ":memory:":
        Path(shadow_path_text).parent.mkdir(parents=True, exist_ok=True)

    with SQLiteRuntimeStore(shadow_path_text) as store:
        for row in observations:
            metadata = _metadata(row)
            provider = _clean(metadata.get("provider")) or "unknown"
            article_id = _clean(row.get("id"))
            canonical_url = _clean(row.get("url"))
            title = _clean(row.get("title"))
            published_at = _clean(row.get("published_at"))
            observed_at = _clean(row.get("collected_at")) or utc_now_iso()

            if not all((article_id, canonical_url, title, published_at)):
                skipped_invalid += 1
                continue

            article = CanonicalArticle(
                article_id=article_id,
                canonical_url=canonical_url,
                title=title,
                source=_clean(row.get("source")) or "unknown",
                published_at=published_at,
                summary=_clean(row.get("snippet")),
                observed_at=observed_at,
                source_type=f"collector:{provider}",
                raw_payload={
                    "collector_row": dict(row),
                    "source_metadata": metadata,
                    "shadow_only": True,
                },
            )
            canonical_article_id = store.upsert_article(article)
            event = _event_for(article, canonical_article_id)
            store.upsert_event(event)

            candidate = policy.decide(
                {
                    "article_id": canonical_article_id,
                    "provider_article_id": article.article_id,
                    "event_cluster_key": event.event_cluster_key,
                    "material_signature": event.material_signature,
                    "title": article.title,
                    "summary": article.summary,
                    "source": article.source,
                    "published_at": article.published_at,
                    "attributes": {
                        "collector_provider": provider,
                        "final_score": row.get("final_score"),
                        "alert_grade": row.get("alert_grade"),
                        "shadow_only": True,
                    },
                }
            )
            decision = store.record_policy_decision(candidate)
            decision_counts[decision.decision_class.value] += 1

            created = False
            if decision.should_enqueue:
                outcome = store.enqueue_outbox(
                    channel=SHADOW_CHANNEL,
                    event_cluster_key=event.event_cluster_key,
                    material_signature=event.material_signature,
                    delivery_class=decision.delivery_class,
                    payload={
                        "article_id": canonical_article_id,
                        "provider_article_id": article.article_id,
                        "title": article.title,
                        "summary": article.summary,
                        "source": article.source,
                        "canonical_url": article.canonical_url,
                        "decision_id": decision.decision_id,
                        "decision_class": decision.decision_class.value,
                        "policy_version": decision.policy_version,
                        "collector_provider": provider,
                        "shadow_only": True,
                    },
                )
                created = outcome.created
                outbox_created += int(created)

            processed += 1
            results.append(
                {
                    "provider_article_id": article.article_id,
                    "canonical_article_id": canonical_article_id,
                    "event_cluster_key": event.event_cluster_key,
                    "material_signature": event.material_signature,
                    "decision_id": decision.decision_id,
                    "decision_class": decision.decision_class.value,
                    "delivery_class": decision.delivery_class,
                    "should_enqueue": decision.should_enqueue,
                    "outbox_created": created,
                    "collector_provider": provider,
                }
            )

        heartbeat_status = (
            "success"
            if processed and not skipped_invalid
            else "degraded"
            if processed
            else "empty"
        )
        store.record_heartbeat(
            RuntimeHeartbeat(
                component="collector_shadow_orchestration",
                run_id=normalized_run_id,
                status=heartbeat_status,
                details={
                    "collector_entrypoint": "app.collector.run",
                    "collector_mode_requested": _clean(collector_mode).lower(),
                    "collector_mode_effective": collector_summary["news_data_mode"],
                    "observations": len(observations),
                    "processed": processed,
                    "skipped_invalid": skipped_invalid,
                    "outbox_created": outbox_created,
                    "shadow_channel": SHADOW_CHANNEL,
                    "shadow_only": True,
                },
            )
        )
        store_stats = store.stats()

    return {
        "gate": "D7-AK-6F-C2",
        "mode": "shadow_only",
        "collector_entrypoint": "app.collector.run",
        "collector_mode_requested": _clean(collector_mode).lower(),
        "collector_mode_effective": collector_summary["news_data_mode"],
        "collector_stats": collector_summary,
        "score_stats": {
            "scored": int(score_stats.get("scored") or 0),
            "alert_candidates": int(score_stats.get("alert_candidates") or 0),
        },
        "run_id": normalized_run_id,
        "shadow_channel": SHADOW_CHANNEL,
        "observations": len(observations),
        "processed": processed,
        "skipped_invalid": skipped_invalid,
        "decision_summary": dict(sorted(decision_counts.items())),
        "outbox_created": outbox_created,
        "store_stats": store_stats,
        "results": results,
        "collector_network_mode": (
            "live_public_news"
            if _clean(collector_mode).lower() == "live"
            else "offline_mock"
        ),
        "channel_sends": 0,
        "smtp_connections": 0,
        "teams_sends": 0,
        "telegram_sends": 0,
        "production_state_writes": 0,
    }
