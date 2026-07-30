# D7-AK-6F Runtime Contract — Shadow Core C1-R2

## Status

- Gate: `D7-AK-6F-C1-R2`
- Mode: **shadow-only**
- Production sender variable: `TEAMS_AI_NEWS_WATCH=0`
- Network delivery: disabled
- Existing GitHub Actions workflows: unchanged
- Existing Git JSON state: read-only source for optional import

This document defines the first persistence and policy boundary for replacing the
current GitHub Actions/Git-commit runtime without discarding working collectors,
source-quality logic, renderers, or SMTP helpers.

## Why this boundary exists

The current repository mixes scheduled production work, large verifier suites,
article collection, HTML rendering, delivery, and runtime state commits. A failure in
any verifier or Git push can block unrelated production behavior. GitHub Actions is
retained for CI, manual recovery, and deployment, but it is not the authoritative
10-minute production scheduler in the target architecture.

C1 adds only a reference domain model and SQLite store. It does not alter any workflow,
send any message, or mutate `data/teams_push_state.json`.

## Canonical flow

```text
scheduler heartbeat
    -> collector observation
    -> canonical article
    -> event resolver
    -> policy decision
    -> delivery_outbox
    -> channel worker claim
    -> delivery attempt
    -> delivered / retryable_failed / terminal_failed
```

Dashboard, Daily, and Weekly outputs consume the same canonical articles and events.
They must not independently recollect or use rendered HTML as the primary delta store.

## Data contracts

### `canonical_articles`

One row per canonical article identity. Re-observation updates metadata without creating
another article row. If different providers supply different article ids for the same
canonical URL, the URL resolves to the existing row and `upsert_article()` returns its
canonical article id. That returned identity is propagated end-to-end into the event primary
article foreign key, default event-cluster key, policy evidence, outbox payload, and replay
result. If article id and URL resolve to different existing rows, the store fails closed rather
than merging unrelated identities.

### `news_events`

One row per cross-source event cluster. `material_signature` identifies a meaningful
content revision for outbox uniqueness.

### `policy_decisions`

Immutable decision evidence produced by a versioned policy. Topic relevance and
urgency are separate dimensions.

### `delivery_outbox`

The authoritative request to deliver a material event to a channel. Its unique key is:

```text
channel + event_cluster_key + material_signature + delivery_class
```

This prevents duplicate delivery creation before any SMTP or Teams call is attempted.

### `delivery_attempts`

Append-only records for provider outcomes. A successful provider result and the outbox
transition to `delivered` occur in one database transaction.

### `runtime_heartbeats`

Each scheduler or worker records a component, run id, status, timestamp, and details.
This distinguishes “scheduler did not run” from “ran with zero articles,” “policy
rejected all,” and “delivery failed.”

### `legacy_delivery_imports`

Read-only provenance imported from the legacy `teams_push_state.json`. C1 does not
convert these rows into live deliveries and never modifies the source JSON file.

## Delivery classes

- `p0_immediate` -> `immediate`
- `p1_priority_digest` -> `priority_digest`
- `p2_hourly_digest` -> `hourly_digest`
- `p3_dashboard_only` -> no outbox delivery
- `reject` -> no output

P0/P1 require explicit event evidence, including every Hyundai E&C direct-impact promotion.
An action phrase without independent source evidence or confirmed event metadata remains at
most P2. Keyword co-occurrence cannot promote an article above P2. Incidental AI references
in competitor or construction articles remain P3 for dashboard review only.

## Claim and authoritative clock contract

The store owns an authoritative clock. Production uses the UTC system clock; deterministic
verification injects a controlled clock at store construction. `claim_outbox()` does not accept
a caller-supplied current time, and provider `attempted_at` is metadata only. Lease validity is
checked against the store clock, so a stale worker cannot backdate provider metadata to finish an
expired claim.

1. A worker claims eligible outbox rows with a random claim token and finite lease.
2. Only the exact claim token may complete the row.
3. Expired leases may be reclaimed by another worker.
4. `retryable_failed` may be claimed after `not_before`.
5. A completion is accepted only while its exact claim token is still inside the active lease;
   an expired lease token fails closed even before another worker reclaims the row.
6. `delivered` and `terminal_failed` are not claimable.
7. Every completion writes a `delivery_attempts` row in the same transaction.

## Timestamp contract

Every timestamp stored or compared by the reference runtime is timezone-aware. The runtime
policy version recorded by C1-R2 remains exactly `d7-ak-6f-c1-r1-shadow-v1`; R2 changes identity
and clock plumbing, not policy classification behavior.

Every timestamp stored or compared by the reference runtime is timezone-aware. Inputs with
`Z` or an explicit offset are normalized to a fixed second-precision UTC `Z` representation
before persistence or SQL comparison. Naive timestamps fail closed. This keeps `not_before`,
claim time, lease expiry, completion time, retry time, event time, decision time, and heartbeat
time comparable across KST and UTC inputs.

## Migration constraints

C1 explicitly does **not**:

- edit `.github/workflows/*`;
- rearm `TEAMS_AI_NEWS_WATCH`;
- import SMTP, Teams, Telegram, HTTP, or webhook clients;
- modify `data/teams_push_state.json`;
- make the SQLite reference database production authority;
- delete or bypass existing renderers and collectors.

A later gate may add a production database adapter only after replay precision,
idempotency, claim recovery, and no-send parallel operation have passed.

## Acceptance evidence

`scripts/verify_runtime_core.py` must prove:

- schema creation, idempotent upserts, and canonical-URL convergence across provider ids
  with end-to-end canonical identity propagation;
- conservative replay of the actual received article examples and evidence-gated HDEC P0;
- transactional outbox uniqueness;
- exact-token claims, mixed-timezone normalization, authoritative clock enforcement,
  backdated-provider-time rejection after lease expiry, and lease recovery;
- success, retryable failure, and terminal failure transitions;
- heartbeat upsert behavior;
- idempotent legacy-state import;
- zero network clients, SMTP connections, Teams sends, Telegram sends, and production
  state writes.
