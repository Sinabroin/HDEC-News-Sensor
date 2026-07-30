"""Store contract for the D7-AK-6F shadow runtime.

The interface isolates domain code from the concrete persistence engine. C1 ships a
SQLite reference implementation; a future production adapter can implement the same
contract without changing collectors, policy, or channel workers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, runtime_checkable

from app.runtime_models import (
    CanonicalArticle,
    NewsEvent,
    OutboxMessage,
    PolicyDecision,
    RuntimeHeartbeat,
)


class RuntimeStoreError(RuntimeError):
    """Base error for runtime persistence failures."""


class ClaimConflict(RuntimeStoreError):
    """Raised when a worker attempts to finish a claim it does not own."""


class InvalidStateTransition(RuntimeStoreError):
    """Raised when an outbox state transition is not allowed."""


@dataclass(frozen=True)
class EnqueueResult:
    message: OutboxMessage
    created: bool


@dataclass(frozen=True)
class LegacyImportResult:
    scanned: int
    inserted: int
    duplicates: int


@runtime_checkable
class RuntimeStore(Protocol):
    def initialize(self) -> None: ...

    def close(self) -> None: ...

    def upsert_article(self, article: CanonicalArticle) -> str: ...

    def upsert_event(self, event: NewsEvent) -> None: ...

    def record_policy_decision(self, decision: PolicyDecision) -> PolicyDecision: ...

    def enqueue_outbox(
        self,
        *,
        channel: str,
        event_cluster_key: str,
        material_signature: str,
        delivery_class: str,
        payload: Mapping[str, Any],
        not_before: str | None = None,
    ) -> EnqueueResult: ...

    def claim_outbox(
        self,
        *,
        channel: str,
        worker_id: str,
        limit: int = 10,
        lease_seconds: int = 300,
    ) -> tuple[OutboxMessage, ...]: ...

    def mark_delivery_succeeded(
        self,
        *,
        outbox_id: str,
        claim_token: str,
        provider: str,
        provider_code: str,
        attempted_at: str | None = None,
    ) -> None: ...

    def mark_delivery_failed(
        self,
        *,
        outbox_id: str,
        claim_token: str,
        provider: str,
        retryable: bool,
        provider_code: str = "",
        error_class: str = "",
        error_message: str = "",
        attempted_at: str | None = None,
        retry_not_before: str | None = None,
    ) -> None: ...

    def record_heartbeat(self, heartbeat: RuntimeHeartbeat) -> None: ...

    def import_legacy_teams_state(self, state: Mapping[str, Any]) -> LegacyImportResult: ...

    def get_outbox(self, outbox_id: str) -> OutboxMessage | None: ...

    def stats(self) -> dict[str, int]: ...
