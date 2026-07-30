"""SQLite reference store for the D7-AK-6F shadow runtime.

This module is intentionally local-first and network-free. It demonstrates the
transactional contracts that a production database adapter must preserve:

* canonical articles and events are idempotently upserted;
* policy decisions are immutable by deterministic decision id;
* outbox uniqueness blocks duplicate delivery creation;
* claims are leased and ownership-checked;
* delivery attempts and outbox state change in one transaction;
* legacy Git JSON state can be imported without mutating the legacy file.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

from app.runtime_models import (
    AttemptStatus,
    CanonicalArticle,
    NewsEvent,
    OutboxMessage,
    OutboxStatus,
    PolicyDecision,
    RuntimeHeartbeat,
    clean_text,
    deterministic_id,
    stable_json,
    utc_now_iso,
)
from app.runtime_store import (
    ClaimConflict,
    EnqueueResult,
    InvalidStateTransition,
    LegacyImportResult,
    RuntimeStoreError,
)


_SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS canonical_articles (
    article_id TEXT PRIMARY KEY,
    canonical_url TEXT NOT NULL,
    title TEXT NOT NULL,
    source TEXT NOT NULL,
    source_type TEXT NOT NULL,
    published_at TEXT NOT NULL,
    summary TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    content_signature TEXT NOT NULL,
    raw_payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_canonical_articles_url
    ON canonical_articles(canonical_url);

CREATE TABLE IF NOT EXISTS news_events (
    event_cluster_key TEXT PRIMARY KEY,
    primary_article_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    headline TEXT NOT NULL,
    material_signature TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    status TEXT NOT NULL,
    attributes_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(primary_article_id) REFERENCES canonical_articles(article_id)
);

CREATE TABLE IF NOT EXISTS policy_decisions (
    decision_id TEXT PRIMARY KEY,
    event_cluster_key TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    decision_class TEXT NOT NULL,
    topic_key TEXT NOT NULL,
    confidence REAL NOT NULL CHECK(confidence >= 0.0 AND confidence <= 1.0),
    should_enqueue INTEGER NOT NULL CHECK(should_enqueue IN (0, 1)),
    delivery_class TEXT NOT NULL,
    reasons_json TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    decided_at TEXT NOT NULL,
    FOREIGN KEY(event_cluster_key) REFERENCES news_events(event_cluster_key)
);
CREATE INDEX IF NOT EXISTS ix_policy_event
    ON policy_decisions(event_cluster_key, decided_at);

CREATE TABLE IF NOT EXISTS delivery_outbox (
    outbox_id TEXT PRIMARY KEY,
    channel TEXT NOT NULL,
    event_cluster_key TEXT NOT NULL,
    material_signature TEXT NOT NULL,
    delivery_class TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    not_before TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    claim_token TEXT,
    claimed_by TEXT,
    claimed_at TEXT,
    lease_until TEXT,
    delivered_at TEXT,
    last_error_class TEXT,
    last_error_message TEXT,
    UNIQUE(channel, event_cluster_key, material_signature, delivery_class),
    FOREIGN KEY(event_cluster_key) REFERENCES news_events(event_cluster_key)
);
CREATE INDEX IF NOT EXISTS ix_outbox_claim
    ON delivery_outbox(channel, status, not_before, lease_until, created_at);

CREATE TABLE IF NOT EXISTS delivery_attempts (
    attempt_id TEXT PRIMARY KEY,
    outbox_id TEXT NOT NULL,
    attempted_at TEXT NOT NULL,
    status TEXT NOT NULL,
    provider TEXT NOT NULL,
    provider_code TEXT NOT NULL,
    error_class TEXT NOT NULL,
    error_message TEXT NOT NULL,
    FOREIGN KEY(outbox_id) REFERENCES delivery_outbox(outbox_id)
);
CREATE INDEX IF NOT EXISTS ix_attempt_outbox
    ON delivery_attempts(outbox_id, attempted_at);

CREATE TABLE IF NOT EXISTS runtime_heartbeats (
    component TEXT NOT NULL,
    run_id TEXT NOT NULL,
    status TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    details_json TEXT NOT NULL,
    PRIMARY KEY(component, run_id)
);
CREATE INDEX IF NOT EXISTS ix_heartbeat_component
    ON runtime_heartbeats(component, observed_at);

CREATE TABLE IF NOT EXISTS legacy_delivery_imports (
    map_name TEXT NOT NULL,
    legacy_key TEXT NOT NULL,
    entry_json TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    PRIMARY KEY(map_name, legacy_key)
);
"""


def _parse_utc(value: str, *, field_name: str = "timestamp") -> datetime:
    raw = clean_text(value)
    if not raw:
        raise RuntimeStoreError(f"{field_name} must be a non-empty timezone-aware timestamp")
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise RuntimeStoreError(f"{field_name} is not a valid ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RuntimeStoreError(f"{field_name} must include a timezone offset")
    return parsed.astimezone(timezone.utc)


def _utc_iso(value: str, *, field_name: str = "timestamp") -> str:
    return _parse_utc(value, field_name=field_name).isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")


def _optional_utc_iso(value: str | None, *, field_name: str) -> str | None:
    raw = clean_text(value)
    return _utc_iso(raw, field_name=field_name) if raw else None


def _future_iso(now: str, seconds: int) -> str:
    return (_parse_utc(now, field_name="now") + timedelta(seconds=seconds)).isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")


class SQLiteRuntimeStore:
    """Transactional SQLite implementation of the runtime store contract."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self.connection = sqlite3.connect(
            self.path,
            isolation_level=None,
            timeout=30.0,
        )
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            self.connection.execute("PRAGMA journal_mode = WAL")
            self.connection.execute("PRAGMA synchronous = NORMAL")

    def initialize(self) -> None:
        self.connection.executescript(_SCHEMA)

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "SQLiteRuntimeStore":
        self.initialize()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    @contextmanager
    def _transaction(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        begin = "BEGIN IMMEDIATE" if immediate else "BEGIN"
        self.connection.execute(begin)
        try:
            yield self.connection
        except Exception:
            self.connection.execute("ROLLBACK")
            raise
        else:
            self.connection.execute("COMMIT")

    def upsert_article(self, article: CanonicalArticle) -> str:
        """Upsert one canonical article and return the authoritative article id.

        Different providers may assign different ids to the same publisher URL. The
        canonical URL wins in that case, so callers can use the returned id for event
        foreign keys without creating a duplicate row or hitting the URL unique index.
        """
        record = article.as_record()
        now = utc_now_iso()
        published_at = _utc_iso(record["published_at"], field_name="published_at")
        observed_at = _utc_iso(record["observed_at"], field_name="observed_at")
        with self._transaction(immediate=True):
            by_id = self.connection.execute(
                "SELECT article_id, canonical_url FROM canonical_articles WHERE article_id = ?",
                (record["article_id"],),
            ).fetchone()
            by_url = self.connection.execute(
                "SELECT article_id, canonical_url FROM canonical_articles WHERE canonical_url = ?",
                (record["canonical_url"],),
            ).fetchone()
            if by_id is not None and by_url is not None and by_id["article_id"] != by_url["article_id"]:
                raise RuntimeStoreError(
                    "article identity conflict: article_id and canonical_url resolve to different rows"
                )
            canonical_id = (
                by_url["article_id"]
                if by_url is not None
                else record["article_id"]
            )
            self.connection.execute(
                """
                INSERT INTO canonical_articles (
                    article_id, canonical_url, title, source, source_type,
                    published_at, summary, observed_at, content_signature,
                    raw_payload_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(article_id) DO UPDATE SET
                    canonical_url = excluded.canonical_url,
                    title = excluded.title,
                    source = excluded.source,
                    source_type = excluded.source_type,
                    published_at = excluded.published_at,
                    summary = excluded.summary,
                    observed_at = excluded.observed_at,
                    content_signature = excluded.content_signature,
                    raw_payload_json = excluded.raw_payload_json,
                    updated_at = excluded.updated_at
                """,
                (
                    canonical_id,
                    record["canonical_url"],
                    record["title"],
                    record["source"],
                    record["source_type"],
                    published_at,
                    record["summary"],
                    observed_at,
                    record["content_signature"],
                    stable_json(record["raw_payload"]),
                    now,
                    now,
                ),
            )
        return str(canonical_id)

    def upsert_event(self, event: NewsEvent) -> None:
        record = event.as_record()
        now = utc_now_iso()
        with self._transaction():
            self.connection.execute(
                """
                INSERT INTO news_events (
                    event_cluster_key, primary_article_id, event_type, headline,
                    material_signature, first_seen_at, last_seen_at, status,
                    attributes_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_cluster_key) DO UPDATE SET
                    primary_article_id = excluded.primary_article_id,
                    event_type = excluded.event_type,
                    headline = excluded.headline,
                    material_signature = excluded.material_signature,
                    last_seen_at = excluded.last_seen_at,
                    status = excluded.status,
                    attributes_json = excluded.attributes_json,
                    updated_at = excluded.updated_at
                """,
                (
                    record["event_cluster_key"],
                    record["primary_article_id"],
                    record["event_type"],
                    record["headline"],
                    record["material_signature"],
                    _utc_iso(record["first_seen_at"], field_name="first_seen_at"),
                    _utc_iso(record["last_seen_at"], field_name="last_seen_at"),
                    record["status"],
                    stable_json(record["attributes"]),
                    now,
                    now,
                ),
            )

    def record_policy_decision(self, decision: PolicyDecision) -> None:
        record = decision.as_record()
        with self._transaction():
            self.connection.execute(
                """
                INSERT INTO policy_decisions (
                    decision_id, event_cluster_key, policy_version,
                    decision_class, topic_key, confidence, should_enqueue,
                    delivery_class, reasons_json, evidence_json, decided_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(decision_id) DO NOTHING
                """,
                (
                    record["decision_id"],
                    record["event_cluster_key"],
                    record["policy_version"],
                    record["decision_class"],
                    record["topic_key"],
                    record["confidence"],
                    1 if record["should_enqueue"] else 0,
                    record["delivery_class"],
                    stable_json(record["reasons"]),
                    stable_json(record["evidence"]),
                    _utc_iso(record["decided_at"], field_name="decided_at"),
                ),
            )

    def enqueue_outbox(
        self,
        *,
        channel: str,
        event_cluster_key: str,
        material_signature: str,
        delivery_class: str,
        payload: Mapping[str, Any],
        not_before: str | None = None,
    ) -> EnqueueResult:
        channel = clean_text(channel)
        event_cluster_key = clean_text(event_cluster_key)
        material_signature = clean_text(material_signature)
        delivery_class = clean_text(delivery_class)
        if not all((channel, event_cluster_key, material_signature, delivery_class)):
            raise RuntimeStoreError("outbox identity fields must be non-empty")
        outbox_id = deterministic_id(
            "outbox",
            channel,
            event_cluster_key,
            material_signature,
            delivery_class,
        )
        created_at = utc_now_iso()
        normalized_not_before = _optional_utc_iso(not_before, field_name="not_before")
        created = False
        with self._transaction(immediate=True):
            cursor = self.connection.execute(
                """
                INSERT INTO delivery_outbox (
                    outbox_id, channel, event_cluster_key, material_signature,
                    delivery_class, payload_json, status, created_at, not_before
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(channel, event_cluster_key, material_signature, delivery_class)
                DO NOTHING
                """,
                (
                    outbox_id,
                    channel,
                    event_cluster_key,
                    material_signature,
                    delivery_class,
                    stable_json(dict(payload)),
                    OutboxStatus.PENDING.value,
                    created_at,
                    normalized_not_before,
                ),
            )
            created = cursor.rowcount == 1
            row = self.connection.execute(
                """
                SELECT * FROM delivery_outbox
                WHERE channel = ? AND event_cluster_key = ?
                  AND material_signature = ? AND delivery_class = ?
                """,
                (channel, event_cluster_key, material_signature, delivery_class),
            ).fetchone()
        if row is None:
            raise RuntimeStoreError("outbox insert succeeded but row could not be read")
        return EnqueueResult(self._row_to_outbox(row), created)

    def claim_outbox(
        self,
        *,
        channel: str,
        worker_id: str,
        limit: int = 10,
        lease_seconds: int = 300,
        now: str | None = None,
    ) -> tuple[OutboxMessage, ...]:
        channel = clean_text(channel)
        worker_id = clean_text(worker_id)
        if not channel or not worker_id:
            raise RuntimeStoreError("channel and worker_id must be non-empty")
        if limit < 1 or limit > 100:
            raise RuntimeStoreError("limit must be between 1 and 100")
        if lease_seconds < 1:
            raise RuntimeStoreError("lease_seconds must be positive")
        current = _utc_iso(now, field_name="now") if clean_text(now) else utc_now_iso()
        lease_until = _future_iso(current, lease_seconds)
        claimed: list[OutboxMessage] = []
        with self._transaction(immediate=True):
            rows = self.connection.execute(
                """
                SELECT * FROM delivery_outbox
                WHERE channel = ?
                  AND (not_before IS NULL OR not_before <= ?)
                  AND (
                      status IN (?, ?)
                      OR (status = ? AND lease_until IS NOT NULL AND lease_until <= ?)
                  )
                ORDER BY created_at, outbox_id
                LIMIT ?
                """,
                (
                    channel,
                    current,
                    OutboxStatus.PENDING.value,
                    OutboxStatus.RETRYABLE_FAILED.value,
                    OutboxStatus.CLAIMED.value,
                    current,
                    limit,
                ),
            ).fetchall()
            for row in rows:
                token = uuid.uuid4().hex
                updated = self.connection.execute(
                    """
                    UPDATE delivery_outbox
                    SET status = ?, claim_token = ?, claimed_by = ?, claimed_at = ?,
                        lease_until = ?, attempt_count = attempt_count + 1
                    WHERE outbox_id = ?
                    """,
                    (
                        OutboxStatus.CLAIMED.value,
                        token,
                        worker_id,
                        current,
                        lease_until,
                        row["outbox_id"],
                    ),
                )
                if updated.rowcount != 1:
                    raise RuntimeStoreError("failed to claim selected outbox row")
                fresh = self.connection.execute(
                    "SELECT * FROM delivery_outbox WHERE outbox_id = ?",
                    (row["outbox_id"],),
                ).fetchone()
                if fresh is None:
                    raise RuntimeStoreError("claimed outbox row disappeared")
                claimed.append(self._row_to_outbox(fresh))
        return tuple(claimed)

    def _require_claim(
        self,
        outbox_id: str,
        claim_token: str,
        *,
        completed_at: str,
    ) -> sqlite3.Row:
        row = self.connection.execute(
            "SELECT * FROM delivery_outbox WHERE outbox_id = ?",
            (clean_text(outbox_id),),
        ).fetchone()
        if row is None:
            raise RuntimeStoreError("outbox row not found")
        if row["status"] != OutboxStatus.CLAIMED.value:
            raise InvalidStateTransition(
                f"outbox {outbox_id} is not claimed: {row['status']}"
            )
        if row["claim_token"] != clean_text(claim_token):
            raise ClaimConflict("claim token does not own the outbox row")
        lease_until = clean_text(row["lease_until"])
        if not lease_until:
            raise ClaimConflict("claimed outbox row has no active lease")
        if _parse_utc(completed_at, field_name="completed_at") >= _parse_utc(
            lease_until, field_name="lease_until"
        ):
            raise ClaimConflict("claim lease expired before completion")
        return row

    def mark_delivery_succeeded(
        self,
        *,
        outbox_id: str,
        claim_token: str,
        provider: str,
        provider_code: str,
        attempted_at: str | None = None,
    ) -> None:
        attempted = (
            _utc_iso(attempted_at, field_name="attempted_at")
            if clean_text(attempted_at)
            else utc_now_iso()
        )
        provider = clean_text(provider)
        if not provider:
            raise RuntimeStoreError("provider must be non-empty")
        with self._transaction(immediate=True):
            self._require_claim(outbox_id, claim_token, completed_at=attempted)
            attempt_id = f"attempt:{uuid.uuid4().hex}"
            self.connection.execute(
                """
                INSERT INTO delivery_attempts (
                    attempt_id, outbox_id, attempted_at, status, provider,
                    provider_code, error_class, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, '', '')
                """,
                (
                    attempt_id,
                    outbox_id,
                    attempted,
                    AttemptStatus.DELIVERED.value,
                    provider,
                    clean_text(provider_code),
                ),
            )
            self.connection.execute(
                """
                UPDATE delivery_outbox
                SET status = ?, delivered_at = ?, claim_token = NULL,
                    claimed_by = NULL, claimed_at = NULL, lease_until = NULL,
                    last_error_class = NULL, last_error_message = NULL
                WHERE outbox_id = ?
                """,
                (OutboxStatus.DELIVERED.value, attempted, outbox_id),
            )

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
    ) -> None:
        attempted = (
            _utc_iso(attempted_at, field_name="attempted_at")
            if clean_text(attempted_at)
            else utc_now_iso()
        )
        provider = clean_text(provider)
        if not provider:
            raise RuntimeStoreError("provider must be non-empty")
        status = (
            OutboxStatus.RETRYABLE_FAILED
            if retryable
            else OutboxStatus.TERMINAL_FAILED
        )
        attempt_status = (
            AttemptStatus.RETRYABLE_FAILED
            if retryable
            else AttemptStatus.TERMINAL_FAILED
        )
        with self._transaction(immediate=True):
            self._require_claim(outbox_id, claim_token, completed_at=attempted)
            attempt_id = f"attempt:{uuid.uuid4().hex}"
            self.connection.execute(
                """
                INSERT INTO delivery_attempts (
                    attempt_id, outbox_id, attempted_at, status, provider,
                    provider_code, error_class, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt_id,
                    outbox_id,
                    attempted,
                    attempt_status.value,
                    provider,
                    clean_text(provider_code),
                    clean_text(error_class),
                    clean_text(error_message),
                ),
            )
            self.connection.execute(
                """
                UPDATE delivery_outbox
                SET status = ?, not_before = ?, claim_token = NULL,
                    claimed_by = NULL, claimed_at = NULL, lease_until = NULL,
                    last_error_class = ?, last_error_message = ?
                WHERE outbox_id = ?
                """,
                (
                    status.value,
                    _optional_utc_iso(retry_not_before, field_name="retry_not_before"),
                    clean_text(error_class) or None,
                    clean_text(error_message) or None,
                    outbox_id,
                ),
            )

    def record_heartbeat(self, heartbeat: RuntimeHeartbeat) -> None:
        with self._transaction():
            self.connection.execute(
                """
                INSERT INTO runtime_heartbeats (
                    component, run_id, status, observed_at, details_json
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(component, run_id) DO UPDATE SET
                    status = excluded.status,
                    observed_at = excluded.observed_at,
                    details_json = excluded.details_json
                """,
                (
                    heartbeat.component,
                    heartbeat.run_id,
                    heartbeat.status,
                    _utc_iso(heartbeat.observed_at, field_name="heartbeat.observed_at"),
                    stable_json(dict(heartbeat.details)),
                ),
            )

    def import_legacy_teams_state(self, state: Mapping[str, Any]) -> LegacyImportResult:
        if state.get("version") != 1:
            raise RuntimeStoreError("legacy Teams state version must be 1")
        maps = (
            "article_ids",
            "normalized_urls",
            "title_fingerprints",
            "cluster_keys",
        )
        scanned = 0
        inserted = 0
        imported_at = utc_now_iso()
        with self._transaction(immediate=True):
            for map_name in maps:
                value = state.get(map_name)
                if not isinstance(value, Mapping):
                    raise RuntimeStoreError(f"legacy {map_name} must be an object")
                for legacy_key, entry in value.items():
                    if not isinstance(legacy_key, str) or not isinstance(entry, Mapping):
                        raise RuntimeStoreError(f"legacy {map_name} contains invalid entry")
                    scanned += 1
                    cursor = self.connection.execute(
                        """
                        INSERT INTO legacy_delivery_imports (
                            map_name, legacy_key, entry_json, imported_at
                        ) VALUES (?, ?, ?, ?)
                        ON CONFLICT(map_name, legacy_key) DO NOTHING
                        """,
                        (map_name, legacy_key, stable_json(dict(entry)), imported_at),
                    )
                    inserted += 1 if cursor.rowcount == 1 else 0
        return LegacyImportResult(scanned, inserted, scanned - inserted)

    def get_outbox(self, outbox_id: str) -> OutboxMessage | None:
        row = self.connection.execute(
            "SELECT * FROM delivery_outbox WHERE outbox_id = ?",
            (clean_text(outbox_id),),
        ).fetchone()
        return self._row_to_outbox(row) if row is not None else None

    def stats(self) -> dict[str, int]:
        tables = (
            "canonical_articles",
            "news_events",
            "policy_decisions",
            "delivery_outbox",
            "delivery_attempts",
            "runtime_heartbeats",
            "legacy_delivery_imports",
        )
        return {
            table: int(
                self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            )
            for table in tables
        }

    @staticmethod
    def _row_to_outbox(row: sqlite3.Row) -> OutboxMessage:
        payload = json.loads(row["payload_json"])
        if not isinstance(payload, Mapping):
            raise RuntimeStoreError("stored outbox payload must be a JSON object")
        return OutboxMessage(
            outbox_id=row["outbox_id"],
            channel=row["channel"],
            event_cluster_key=row["event_cluster_key"],
            material_signature=row["material_signature"],
            delivery_class=row["delivery_class"],
            payload=dict(payload),
            status=OutboxStatus(row["status"]),
            created_at=row["created_at"],
            not_before=row["not_before"],
            attempt_count=int(row["attempt_count"]),
            claim_token=row["claim_token"],
            lease_until=row["lease_until"],
        )
