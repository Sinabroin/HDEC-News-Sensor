"""Domain models for the D7-AK-6F shadow news runtime.

The models are transport-agnostic and contain no network or filesystem side effects.
They deliberately separate article observations, canonical events, policy decisions,
outbox messages, delivery attempts, and scheduler heartbeats.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Mapping


class RuntimeModelError(ValueError):
    """Raised when a runtime domain object violates its contract."""


class DecisionClass(StrEnum):
    P0 = "p0_immediate"
    P1 = "p1_priority_digest"
    P2 = "p2_hourly_digest"
    P3 = "p3_dashboard_only"
    REJECT = "reject"


class OutboxStatus(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    DELIVERED = "delivered"
    RETRYABLE_FAILED = "retryable_failed"
    TERMINAL_FAILED = "terminal_failed"


class AttemptStatus(StrEnum):
    DELIVERED = "delivered"
    RETRYABLE_FAILED = "retryable_failed"
    TERMINAL_FAILED = "terminal_failed"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def clean_text(value: object) -> str:
    return " ".join(str(value or "").split())


def stable_json(value: Mapping[str, Any] | list[Any] | tuple[Any, ...]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _require(value: object, field_name: str) -> str:
    cleaned = clean_text(value)
    if not cleaned:
        raise RuntimeModelError(f"{field_name} must be non-empty")
    return cleaned


@dataclass(frozen=True)
class CanonicalArticle:
    article_id: str
    canonical_url: str
    title: str
    source: str
    published_at: str
    summary: str = ""
    observed_at: str = field(default_factory=utc_now_iso)
    source_type: str = "publisher"
    raw_payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "article_id", _require(self.article_id, "article_id"))
        object.__setattr__(self, "canonical_url", _require(self.canonical_url, "canonical_url"))
        object.__setattr__(self, "title", _require(self.title, "title"))
        object.__setattr__(self, "source", _require(self.source, "source"))
        object.__setattr__(self, "published_at", _require(self.published_at, "published_at"))
        object.__setattr__(self, "observed_at", _require(self.observed_at, "observed_at"))
        object.__setattr__(self, "summary", clean_text(self.summary))
        object.__setattr__(self, "source_type", clean_text(self.source_type) or "unknown")

    @property
    def content_signature(self) -> str:
        return sha256_text(stable_json({
            "title": self.title,
            "summary": self.summary,
            "canonical_url": self.canonical_url,
            "published_at": self.published_at,
        }))

    def as_record(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["raw_payload"] = dict(self.raw_payload)
        payload["content_signature"] = self.content_signature
        return payload


@dataclass(frozen=True)
class NewsEvent:
    event_cluster_key: str
    primary_article_id: str
    event_type: str
    headline: str
    material_signature: str
    first_seen_at: str
    last_seen_at: str
    status: str = "active"
    attributes: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in (
            "event_cluster_key",
            "primary_article_id",
            "event_type",
            "headline",
            "material_signature",
            "first_seen_at",
            "last_seen_at",
        ):
            object.__setattr__(self, name, _require(getattr(self, name), name))
        object.__setattr__(self, "status", clean_text(self.status) or "active")

    def as_record(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["attributes"] = dict(self.attributes)
        return payload


@dataclass(frozen=True)
class PolicyDecision:
    decision_id: str
    event_cluster_key: str
    policy_version: str
    decision_class: DecisionClass
    topic_key: str
    confidence: float
    should_enqueue: bool
    delivery_class: str
    reasons: tuple[str, ...]
    decided_at: str = field(default_factory=utc_now_iso)
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("decision_id", "event_cluster_key", "policy_version", "topic_key", "decided_at"):
            object.__setattr__(self, name, _require(getattr(self, name), name))
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise RuntimeModelError("confidence must be between 0 and 1")
        if not isinstance(self.decision_class, DecisionClass):
            object.__setattr__(self, "decision_class", DecisionClass(str(self.decision_class)))
        object.__setattr__(self, "delivery_class", clean_text(self.delivery_class) or "none")
        cleaned_reasons = tuple(clean_text(item) for item in self.reasons if clean_text(item))
        if not cleaned_reasons:
            raise RuntimeModelError("reasons must contain at least one entry")
        object.__setattr__(self, "reasons", cleaned_reasons)

    def as_record(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["decision_class"] = self.decision_class.value
        payload["reasons"] = list(self.reasons)
        payload["evidence"] = dict(self.evidence)
        return payload


@dataclass(frozen=True)
class OutboxMessage:
    outbox_id: str
    channel: str
    event_cluster_key: str
    material_signature: str
    delivery_class: str
    payload: Mapping[str, Any]
    status: OutboxStatus = OutboxStatus.PENDING
    created_at: str = field(default_factory=utc_now_iso)
    not_before: str | None = None
    attempt_count: int = 0
    claim_token: str | None = None
    lease_until: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "outbox_id",
            "channel",
            "event_cluster_key",
            "material_signature",
            "delivery_class",
            "created_at",
        ):
            object.__setattr__(self, name, _require(getattr(self, name), name))
        if not isinstance(self.status, OutboxStatus):
            object.__setattr__(self, "status", OutboxStatus(str(self.status)))
        if self.attempt_count < 0:
            raise RuntimeModelError("attempt_count must be non-negative")

    @property
    def unique_key(self) -> tuple[str, str, str, str]:
        return (
            self.channel,
            self.event_cluster_key,
            self.material_signature,
            self.delivery_class,
        )


@dataclass(frozen=True)
class DeliveryAttempt:
    attempt_id: str
    outbox_id: str
    attempted_at: str
    status: AttemptStatus
    provider: str
    provider_code: str = ""
    error_class: str = ""
    error_message: str = ""

    def __post_init__(self) -> None:
        for name in ("attempt_id", "outbox_id", "attempted_at", "provider"):
            object.__setattr__(self, name, _require(getattr(self, name), name))
        if not isinstance(self.status, AttemptStatus):
            object.__setattr__(self, "status", AttemptStatus(str(self.status)))
        object.__setattr__(self, "provider_code", clean_text(self.provider_code))
        object.__setattr__(self, "error_class", clean_text(self.error_class))
        object.__setattr__(self, "error_message", clean_text(self.error_message))


@dataclass(frozen=True)
class RuntimeHeartbeat:
    component: str
    run_id: str
    status: str
    observed_at: str = field(default_factory=utc_now_iso)
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("component", "run_id", "status", "observed_at"):
            object.__setattr__(self, name, _require(getattr(self, name), name))


def deterministic_id(prefix: str, *parts: object, length: int = 32) -> str:
    cleaned = [clean_text(part) for part in parts]
    digest = sha256_text("|".join(cleaned))[:length]
    return f"{_require(prefix, 'prefix')}:{digest}"
