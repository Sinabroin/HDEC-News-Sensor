# D7-AK-6E R4-R2 — Publisher Resolution and Coverage

## Decision

R4-R2 expands the standalone News Censor without weakening publisher authority
or any sender gate. The collector still publishes only a normalized, verified
publisher canonical URL. Portal, search, redirect, unsafe, unresolved, and
quarantined URLs remain ineligible for every public or delivery surface.

The implementation separates two policies that previously shared too narrow a
candidate surface:

- News Censor display: publisher-direct authority, bounded freshness/backfill,
  category coverage, canonical deduplication, and publisher diversity;
- Teams delivery: publisher-direct authority plus AI topic eligibility,
  executive relevance, confirmed importance, live-delta provenance, watch/canary
  gates, and the configured per-run cap.

A row visible in News Censor does not become a Teams candidate merely because it
is visible. Teams continues to own its existing selection and sender state.

## Bounded publisher resolution

The resolution budget remains 20 items and 25 seconds per collection pass. Rows
are ordered publisher-round-robin inside a weighted provider schedule: two
official direct-feed rows, two Naver origin candidates, and one Google discovery
row per round when those streams are present. A large first feed can no longer
consume the entire bounded budget.

Unexpected parser or optional-runtime failures are isolated to the affected row
with a stable quarantine reason. They cannot erase publisher authority already
proved for another row. Image-only dependencies are loaded only on image paths;
text metadata/body verification remains available without Pillow.

## Coverage and freshness

The central bounded query policy declares all six substantive News Censor
categories: 사업영역, 동종사, 현대그룹, 안전품질, 해외지정학, and AI. Query
attempt/success/addition counts are aggregated by category without publishing
query text.

The browser-safe display pool contains presentation fields only. It excludes
discovery URLs, raw source metadata, bodies, credentials, and sender state. The
selector:

1. seeds observable category coverage;
2. ranks current articles before recent, 30-day backfill, and archive rows;
3. applies a soft cap of three rows per publisher;
4. relaxes the cap only to fill otherwise unused capacity;
5. never invents a missing category, source, date, or article.

Every visible article carries one freshness label: `fresh`, `recent`,
`backfill`, `archive`, or `unknown`. The public Coverage Health rail shows
covered/category targets, publisher diversity, current versus backfill counts,
category query health, and grouped quarantine counts. It exposes no quarantined
URL, exception text, response body, credential, or recipient.

## Audit evidence

The pre-change bounded local real-network/no-send audit used a temporary brief
artifact, a 240-second outer timeout, `TEAMS_AI_NEWS_WATCH=0`, and
`TELEGRAM_AUTO_SEND=0`. It observed:

- source requests: 156;
- successful source responses: 155;
- raw candidates: 312;
- publisher-direct eligible: 0;
- quarantine: 314;
- final portal URLs: 0.

The local runtime lacked the image extra, revealing the batch-wide import
coupling. Production installs Pillow, but R4-R2 removes that coupling and also
protects the batch from any other unexpected per-row failure.

The post-change candidate used the same no-send gates and a 300-second outer
bound. It completed as `LIVE_HEALTHY_WITH_ARTICLES` with:

- source requests: 165;
- successful source responses: 164;
- raw candidates: 328;
- publisher-direct eligible and published: 19;
- publisher resolution: 20 attempted, 19 resolved, 307 budget-waiting;
- News Censor coverage: 5/6 categories, 7 publishers, 15 current/recent and
  4 bounded backfill articles;
- quarantine: 311;
- final portal URLs: 0.

All six category query families were attempted successfully. The peer category
was truthfully shown as a content gap for this edition instead of being filled
with an unverified or synthetic row.

## Verification and rollback

Focused gates cover fair ordering, per-row failure isolation, publisher-only
authority, display/Teams separation, category indicators, freshness/backfill,
publisher diversity, aggregate-only quarantine diagnostics, zero network in
offline fixtures, and zero sender/state mutation.

Rollback is a normal revert of the R4-R2 merge commit. Do not rewrite `main`,
delete sender state, or change repository variables. `TEAMS_AI_NEWS_WATCH` is
preserved byte-for-byte as an external rollout control throughout this task.
