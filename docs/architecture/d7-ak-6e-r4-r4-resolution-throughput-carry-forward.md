# D7-AK-6E R4-R4 — Resolution Throughput and Verified Carry-Forward

## Measured bottleneck and result

The production page observed before implementation displayed 7 articles from 8
verified rows. Its exact public model recorded 10 publisher-resolution attempts,
8 successes, 497 budget-waiting rows, six publishers, and no 현대그룹 card.
The immutable local pre-change live artifact (`b4a53603…`) contained 328 raw and
post-discovery rows (40 official-direct, 288 Google discovery, Naver unavailable
in the local environment). The serial resolver attempted 20, verified 18, failed
2 non-article pages, and collapsed the remaining 308 into one generic budget
reason. It exposed no cache, latency, host, lane, category, or deadline/item/host
outcome metrics.

The R4-R4 cold shadow attempted 36 publisher resolutions, verified 33, and timed
out 0 within a 31.98-second resolution pass. Latency was 1.26 seconds p50 and
3.98 seconds p95. Its unchanged brief fingerprint was `1e3e9814…`; after the
non-destructive seven-day selector admitted every relevant unique backfill row,
the same brief rendered 23 cards (10 primary, 13 honestly labelled backfill),
five publishers, all six topical categories, and zero portal links.

The warm shadow loaded 28 state entries, reused 23 without publisher fetches,
attempted 32 uncached rows, and verified 28 new rows. Its final verified union was
51; 38 unique relevant rows rendered (13 primary, 25 backfill), across eight
publishers and all six topical categories. No carry-forward-only row happened to
be necessary because every still-useful cold entry was rediscovered and became a
current-run cache hit. Offline fixtures separately prove a 20-row carry-forward
display increase with zero additional Teams candidates. Cold and warm Teams
candidate counts were both zero, portal links were zero, accounting passed, and
the protected Teams/editorial state hashes remained byte-identical.

## State contract

`data/news_censor_verified_state.json` implements
`HDEC_NEWS_CENSOR_VERIFIED_STATE_V1`, version 1. It is opt-in through
`NEWS_CENSOR_VERIFIED_STATE_PATH`; only `scheduled-live-refresh.yml` sets the
production path. The independent Teams watch does not set it and therefore never
loads display carry-forward.

Entries contain only a stable public identity, publisher-direct canonical URL and
host, public title/source/snippet, real publication time, verification/seen/expiry
times, safe category and policy decisions, explicit policy versions, bounded
invalidation/backoff evidence, and an optional already-local image reference.
Discovery/search/portal URLs, redirect evidence, fetched markup, article text,
headers, cookies, credentials, recipients, and sender configuration are rejected
by the schema.

The reuse TTL is 24 hours. Public carry-forward is limited to seven days and
requires matching canonicalization, publisher-authority, and source-quality
policy versions. State retention is 14 days, with at most 300 active and 100
invalid entries and a two-megabyte serialized limit. Entries are canonical-sorted
and duplicate canonicals merge deterministically. Writes validate before and
after a flushed/fsynced temporary file, atomically replace the target, fsync the
directory where supported, and leave the previous valid file untouched on any
failure. Parse/schema/version failure supplies zero carried rows and blocks state
replacement while current live verification remains available.

404/410, missing article metadata/body, unsafe redirects, or a non-publisher
canonical invalidate a prior entry after bounded revalidation. Authority failures
have a 24-hour retry delay; unsafe/canonical failures have seven days. Transient
timeout/DNS failures are recorded with a one-hour retry delay and do not erase an
unexpired prior proof.

## Bounded resolution and scheduling

The production envelope is 60 attempts, a 120-second scheduling deadline, four
global workers, one worker per known target host, and at most 12 attempts per
candidate host. The article fetch timeout remains 8 seconds; Google decode calls
remain 3.5 seconds. The existing two-megabyte article response, three-redirect,
DNS/public-IP checks, redirect revalidation, no-script/no-browser behavior, and
publisher canonical authority checks are unchanged. A second process-local lock
is acquired after Google decoding so a direct lane and decoded discovery lane
cannot fetch the same actual publisher concurrently.

Scheduling is deterministic and seeds topical coverage inside the official,
Naver-originallink, and Google lanes before their ordinary weighted remainder.
Official direct rows remain first, followed by Naver originallinks and bounded
Google discovery. 현대그룹 has strategic infrastructure, energy, data-center,
robotics, smart-city, overseas-project, steel, and logistics aliases; a mandatory
strategic anchor prevents ordinary vehicle launch, sales, trim, or design news
from qualifying.

Every queue row ends in one explicit outcome: cache hit, scheduled, success,
authority rejection, non-article, timeout, network error, unsafe target, global
deadline, item budget, per-host limit, duplicate, fair-scheduling exhaustion, or
another explicit reason. Private audits include per-host/lane/category attempts
and successes and p50/p95 latency. Public Coverage Health exposes only safe
aggregates.

## Edition and sender boundary

The canonical `HDEC_NEWS_CENSOR_DISPLAY_V1` input is the canonical-deduplicated
union of current verified rows and unexpired state rows. Current data wins. Rows
carry explicit `current_run_seen`, `carried_forward`, `carry_forward_reason`, and
`teams_newness_eligible` fields. Ranking is current primary, carried primary,
current seven-day backfill, then carried backfill; category/publisher preferences
only order valid rows, and the sole total cap remains 40.

The Teams candidate selector explicitly rejects carry-forward-only rows, while
all existing current-delta, AI-topic, relevance, importance, urgency, novelty,
dedup, sender, and maximum-one rollout controls remain unchanged. Tests and both
live shadows used dry-run sender controls and temporary state. No Teams,
Telegram, SMTP, production sender-state, Pages, or public-output mutation occurred.

Rollback is a normal revert of the R4-R4 merge commit. Do not reset or force-push
main, delete Teams state, enable Telegram, or repair malformed state by weakening
validation. A state validation failure falls back only to currently verified live
articles, never mock or malformed carry-forward.
