# D7-AK-6E R3-V8 Publisher-Direct Production Collector

## Decision

An article is eligible for an executive delivery surface only when its final
authority is a normalized publisher URL. A publisher is the news organization
that produced the article, an official government or public-institution
release, a company newsroom, or an official research/technology organization.
Daum, Naver, Google News, MSN/Yahoo redistribution, search pages, URL
shorteners, and redirect intermediaries are discovery provenance only.

`app/publisher_direct.py` owns this common deterministic contract. It separates
`discovery_url` from `publisher_url`, rejects non-authority hosts, normalizes
publisher canonical URLs, assigns quarantine status and reasons, applies the
executive relevance gate, and deduplicates on the publisher canonical. Daily,
Dashboard, report/Telegram, and Teams selection call the same eligibility
policy rather than maintaining surface-specific URL rules.

An eligible record has:

- `publisher_direct=true`
- a public HTTP(S) `publisher_url`/canonical on a non-portal host
- a title and publisher source
- a publisher date or an explicit preserved-date fallback reason
- existing AI/현대건설 relevance qualification
- no quarantine status

Discovery fields remain available for audit:

- `discovery_url` and `discovery_provider`
- `publisher_url`, `publisher_domain`, and `publisher_direct`
- `portal_resolution_status` and `portal_resolution_reason`
- `raw_provenance_urls`

Raw discovery provenance is never promoted to a final link. Unresolved records
are retained with `quarantine=true` and a quarantine status in the collection
database and run audit; they are not inserted into the delivery article set.

## Collection order and resolution

Production collection performs these stages:

1. Read enabled, repository-reviewed official feeds from
   `data/publisher_direct_sources.json`.
2. Collect Google News and Naver API discovery rows.
3. Prefer a valid Naver `originallink`; a Naver portal fallback remains
   provenance and is quarantined.
4. For Google/Daum/other portal discovery, resolve a candidate publisher URL
   and fetch the publisher page through the R3-V7 bounded article-import
   network boundary.
5. Confirm source, publication date, body metadata, and canonical URL from the
   publisher page.
6. Partition into delivery-eligible and quarantine sets, then deduplicate
   eligible rows by normalized publisher canonical URL.

Portal resolution reuses the authenticated importer’s safety properties:
HTTP(S) only, public DNS addresses only, validation on every redirect, a
three-redirect ceiling, bounded time and response size, and HTML-only parsing.
Private destinations, loops, ambiguous/no publisher originals, budget
exhaustion, and extraction failures all fail closed to quarantine. There is no
portal fallback delivery path.

The source registry contains only verified official endpoints already checked
for this change: 국토교통부, 과학기술정보통신부, and 행정안전부 press-release
feeds. 현대건설’s official newsroom is recorded as a disabled candidate because
no RSS endpoint was verified; the registry does not guess one. Registry feeds
are fixed reviewed configuration, while every discovered article authority is
still subject to publisher validation.

## Canonical clustering and relevance

Article identity and URL hashes now use the normalized publisher canonical
where available. A portal discovery and direct publisher row for the same
canonical become one cluster, with discovery URLs merged into raw provenance.
Quarantine rows use a separate discovery-derived storage identity, so a failed
verification cannot block a later successful publisher canonical insert.
Verified publisher rows bypass the legacy title/source suppression that would
otherwise allow an old portal row to shadow the migrated canonical.
The publisher page is authoritative for the displayed source and publication
date. When it provides no date, an existing feed/API date may be preserved only
with an explicit fallback reason.

Publisher authority is necessary but not sufficient. Existing importance,
confirmed-event, AI-topic, and 현대건설 relevance policies still determine
executive significance. The new pre-delivery policy recognizes construction and
infrastructure AI, data centers, smart construction, robotics, digital twins,
BIM, engineering/safety/quality AI, energy/nuclear/plant AI, enterprise
generative AI, regulation, investment, and material corporate change. Consumer
AI gossip, theme-stock coverage, promotions, and unrelated low-value material
remain excluded by the existing scoring and delivery gates.

## Delivery boundaries

`app/briefing.py` partitions legacy database rows before building Dashboard,
HTML report, and Telegram artifacts. `app/teams_ai_push.py` applies the same
publisher-direct decision immediately before candidate creation and again
before rendering a card or channel email. Therefore a malformed caller cannot
render or send an unresolved portal fallback by bypassing collection.

The official Teams transport remains unchanged:

- `.github/workflows/teams-ai-news-watch.yml`
- ten-minute best-effort schedule
- Gmail SMTP to `TEAMS_CHANNEL_EMAIL`
- one message per article
- existing maximum and dedup state

R3-V8 does not execute the sender, connect to SMTP, write production state,
dispatch a workflow, or alter any workflow. Fixture verification stops after
payload construction. Teams verifier fixtures that previously expected a
truthfully labelled Google fallback now require zero candidates and zero SMTP
attempts for that input.

The Editorial Daily production runner applies the same collection partition
before normalization. Its approved-review priority and 08:14 AI fallback
contract are unchanged, and `templates/editorial_daily.html` is byte-identical.

## Verification and rollout boundary

`scripts/verify_publisher_direct_collector.py` uses injected resolver, opener,
and RSS fixtures only. It covers direct official RSS, Naver originallink success
and absence, Daum/Google publisher discovery, canonical and outbound recovery,
no-original quarantine, private redirect, redirect loop, shortener rejection,
publisher canonical changes and deduplication, official newsroom eligibility,
executive relevance, and zero portal final links in Daily, Dashboard,
Teams-email/card, and Telegram/report payloads. It also records zero external
network calls, sends, SMTP attempts, workflow dispatches, and production writes.

The separate PR #13 operations recovery is neither copied nor cherry-picked.
This stacked branch is based on the exact PR #14 console head. A later
integration audit must compare both stacks, resolve ownership deliberately, run
their complete verifier sets together, and review shadow candidate volume,
quarantine reasons, publisher diversity, and canonical migration before any
watch activation or merge.

`NEXT=R3_STACK_INTEGRATION_AUDIT`
