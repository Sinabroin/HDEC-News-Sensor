# D7-AK-6E R4-R3 — Eligible-to-Public Selection Bottleneck Repair

## Measured diagnosis

The reported `19 publisher eligible -> 8 public` comparison did not describe
one funnel. The R4-R2 19-item result came from its temporary pre-merge shadow.
After PR #24 merged, the scheduled refresh collected again. The deployed
edition was generated at `2026-08-02T15:20:29+09:00`; its own artifact recorded
508 raw candidates, 9 resolution attempts, 8 resolution successes, and 8
publisher-direct eligible rows. The selector accepted 8, the renderer received
8, and the committed DOM contained 8 unique article IDs.

The apparent eleven-item loss was therefore `workflow artifact mismatch`, not
an eight-card selector, renderer, CSS, image, Teams-cap, or serialization limit.
R4-R3 prevents that comparison error by fingerprinting the exact brief and
emitting one audit from that artifact through selection and final DOM parsing.

The post-repair bounded shadow used artifact fingerprint
`15e40ea5a414b32cfbb836e348a5b392a4ce921b3c77c55b35b6a59931b6a35c`.
It measured 328 raw candidates, 20 resolution attempts, 18 resolution
successes, 18 publisher-direct rows, 16 display-policy rows, 10 primary rows,
5 seven-day backfill candidates, and 11 final cards. The seven final exclusions
were exactly two relevance rejections, one row older than seven days, and four
backfill rows not needed by a below-target category. Canonical, event,
diversity, hard-cap, renderer, template, serialization, and DOM-key losses were
all zero. The selector, renderer, parsed DOM, and public-count definition each
reported 11 unique IDs. The shadow produced no Teams candidates before or
after the public-display change.

## Canonical display contract

`news_censor_display_articles` is the only News Censor candidate field. It is
declared by `HDEC_NEWS_CENSOR_DISPLAY_V1`; every row declares
`HDEC_NEWS_CENSOR_DISPLAY_ARTICLE_V1`, publisher authority, source-quality and
display-relevance decisions, timestamp, and multi-category memberships.

The builder never reads Executive surface lists, category sections, accordion
sections, or the Teams delta artifact as a fallback. The template renders the
selector result without another eligibility pass. Latest and dated editions
are byte-identical outputs from that one rendered model. Teams retains its
independent delta, AI relevance, importance, sender-gate, and state policies.

## Selection policy

- Publisher authority, source quality, relevance, timestamp, and at least one
  fixed topical category are mandatory.
- The primary window is 72 hours relative to the artifact `generated_at` in
  KST-normalized time.
- Rows from 72 hours through seven days are candidates only for topical
  categories that remain below the target of three. Their real timestamps are
  preserved and they are visibly labelled `7일 이내 카테고리 보강`.
- Missing timestamps, future outliers beyond six hours, and rows older than
  seven days are excluded with explicit reasons.
- Canonical URL dedup and conservative material-event dedup are independent.
  A shared entity or broad theme is insufficient. Different amounts, event
  types, and material follow-ups survive.
- Category and publisher diversity change ordering only. They never discard a
  primary-window row. A share above 40% is reported as a relaxation when valid
  supply requires it.
- The only total public article cap is 40.

## Active limits audit

| Limit | Value | Effect on News Censor article count |
|---|---:|---|
| Public selected hard cap | 40 | The sole final article-count truncation; every loss is `hard_cap_truncated`. |
| Canonical brief display pool | 120 | Upstream serialization safety ceiling, above the public hard cap. |
| Publisher resolution network budget | 20 items / 25 seconds | Authority-verification supply bound, reported as budget exhaustion; not a display cap. |
| Category backfill target | 3 per topical category | Admits seven-day backfill only; never removes a primary row or duplicates a card. |
| Publisher share preference | 40% | Ranking preference only; automatically relaxed to retain valid supply. |
| Preferred publisher diversity | 6 | Ranking preference only when supply exists. |
| Article image attempts | 8 by default, 24 CLI ceiling | Images only. Unattempted/failed images use local deterministic fallback and retain text cards. |
| Market rows | 8 | Market rail only. |
| Safety clusters | 4 | Safety rail only. |
| Theme rows | 5 | Theme rail only. |
| Dynamic tag chips | 12 | Filter-chip display only; does not remove articles or memberships. |
| Lead story | 1 | Presentation extraction; it remains visible and is not repeated in the grid. |
| Teams max articles | repository value 1 | Teams sender only; never read by the News Censor selector. |

There is no total cap of eight, per-category article cap, publisher hard cap,
grid cap, sticky-rail quota, lead removal, archive quota, mobile CSS cutoff, or
image-to-article coupling.

## Accounting and rollout

`PUBLIC_DISPLAYED_COUNT` means unique selected article IDs visible in the 홈
edition, counting the lead once and excluding market, weather, safety, and all
non-article cards. The build model, audit, workflow log, Coverage Health rail,
desktop/mobile verifier, and production check use that definition.

`scripts/audit_news_censor_funnel.py` writes only aggregate counters, safe
article hashes, publisher hosts for already verified rows, and cluster hashes.
It does not expose discovery URLs, unresolved portal URLs, credentials,
recipients, or sender state. The scheduled refresh writes the audit only to
runner temporary storage and publishes no internal rejection record.

Rollback is a normal revert of the R4-R3 merge commit. Do not reset `main`,
force-push, modify Teams watch, send a canary, or enable Telegram.
