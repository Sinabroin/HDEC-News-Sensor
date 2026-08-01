# D7-AK-6E R3 Stack Integration Audit

## Scope and integration order

This branch replays the three open Draft PR stacks on the unchanged common base
`f8edbc19913c0641b9c524c87f8b99fcdbaa9b89` in this order:

1. PR #13, `5cf5daa0496279cdc6a99013ed56aba737da9222`
   (`fix: restore refresh and prioritize trusted sources`)
2. PR #14, oldest first:
   `bf1942326cc068d46c5d42834a5fb2ee0a5e643e`,
   `c338ed705697f5c01fa233dd7326aad19811e9b2`,
   `9ec62cf26c0e38cf970716eceef395bc3fc87f79`
3. PR #15, `d916885195a4713475b669f080f24db2474a8d34`
   (`feat: require publisher-direct links for executive news delivery`)

Exact commit cherry-picks were used. No branch merge, force operation, or
duplicate replay of PR #14 occurred.

## Conflict resolution

The only textual conflict was the import list in `app/briefing.py`. PR #13
imports `source_priority`; PR #15 imports `publisher_direct`. The resolution
keeps both imports because the brief pipeline first partitions publisher
authority/quarantine and then uses trusted-source priority when ordering
delivery-eligible rows. No whole-file `ours` or `theirs` resolution was used.

The following overlap-sensitive paths were reviewed as one policy chain:

- `app/briefing.py`: publisher partition precedes source ranking.
- `app/collector.py`: verified canonical ingestion and separate quarantine
  identity remain intact.
- `app/live_collector.py`: direct-source first collection and strict publisher
  page verification remain intact.
- `app/source_priority.py`: live-only trusted/major/official ordering is
  unchanged.
- `app/publisher_direct.py`: the common final-authority gate is unchanged.
- `app/teams_ai_push.py`: publisher authority is checked before card/email
  rendering.
- `scripts/build_static_dashboard.py`: PR #13 trusted-slot behavior is retained;
  final rows have already passed the common publisher gate.
- `scripts/run_editorial_briefing.py`: approved-review priority and AI fallback
  stay unchanged while live production input uses publisher-direct partitioning.

Two existing Editorial regression fixtures enabled network-capable resolution
without supplying all of their fixture leaves. The integration network guard
correctly caught latent Google News and fixture-image connection attempts.
`scripts/verify_editorial_briefings.py` now injects deterministic publisher-page,
image-page, image-probe, and raster-download responses in those test cases; the
production resolver is not changed and the 335-check regression contract is
preserved.

## Authority and source-priority order

Every delivery surface follows one order:

1. verified publisher-direct authority;
2. quarantine exclusion;
3. normalized publisher canonical deduplication;
4. Hyundai E&C and AI executive relevance;
5. importance and confirmed-event status;
6. trusted/major/official source priority;
7. Daily, Dashboard, Teams, or Telegram exposure.

Source reputation never upgrades a portal URL into an authority URL. Conversely,
a verified publisher URL is not sufficient when executive relevance or
importance fails.

## Quarantine lifecycle and canonical migration

Portal discovery provenance is retained with
`publisher_direct=false`, `status=quarantine`, its discovery provider/URL, and a
resolution reason. Quarantine rows are stored for audit but are removed by the
shared delivery partition before scoring surfaces or sender candidate
selection.

A quarantine storage identity is based on its unresolved discovery identity.
When a later run verifies the publisher page, the normalized publisher
canonical receives a separate verified identity. The older quarantine row can
therefore remain auditable without blocking insertion of the verified article.
Portal and direct discoveries that already resolve to the same canonical are
clustered to one delivery article while preserving discovery provenance.

## Teams transport preservation

The production transport remains Gmail SMTP to `TEAMS_CHANNEL_EMAIL`, one email
per article, owned by `teams-ai-news-watch.yml` on its ten-minute schedule.
The maximum ten-article limit, canary cap, and successful-send-only dedup state
write contract are unchanged. Publisher authority is checked before a candidate
can reach SMTP and again before card/email rendering. No webhook or digest
transport is introduced.

## Offline integration verification

`scripts/verify_r3_stack_integration.py` runs all required component verifiers
under a temporary `sitecustomize` network guard. It also builds temporary Daily,
Dashboard, static report, Editorial Review Console, Teams card/email, and
Telegram previews below `/tmp`. Its integrated fixtures cover:

- major-media AI data-center publisher authority and trusted priority;
- Daum discovery clustered with the same publisher canonical;
- unresolved portal quarantine with zero Teams candidate/SMTP attempt;
- official smart-construction press-release eligibility;
- major-media consumer AI gossip rejected at relevance;
- quarantine and later verified canonical coexistence;
- zero portal URLs across all final delivery surfaces.

No public RSS, Naver, Google, Daum, SMTP, Teams, or Telegram endpoint is used.
Workflow files, the Daily template, and production state files are hashed before
and after the audit.

## R4-R1 validated live artifact handoff

The static refresh and Teams watch no longer treat a rendered dashboard mode
string as collector health. Each workflow first creates one
`HDEC_VALIDATED_EXECUTIVE_BRIEF_V1` JSON artifact and validates its explicit
collector status, successful-source count, fallback flag, publisher-direct
eligible count, quarantine count, and final portal count. Static consumers and
the Teams delta dashboard reuse that exact artifact instead of independently
collecting news. `LIVE_HEALTHY_NO_ELIGIBLE_ARTICLES` is a successful live
no-send; `LIVE_COLLECTION_FAILED` and `LIVE_FALLBACK_REJECTED` remain closed.

## Canary prerequisites

Keep `TEAMS_AI_NEWS_WATCH=0` until all of the following occur in a separate,
explicitly approved step:

1. integrated visual acceptance for the Editorial Review Console;
2. authenticated URL-import acceptance against the configured operator API;
3. shadow-live collection with portal-resolution/quarantine metrics reviewed;
4. canonical migration and dedup-state backup review;
5. Teams payload canary review with recipient and cap explicitly approved;
6. PR integration audit and the separate PR #13 compatibility review completed.

This integration PR neither dispatches a workflow nor activates a sender.

## Rollback

Before merge, rollback is simply closing the integration PR and deleting only
the integration branch; PR #13, #14, and #15 remain untouched. If a later,
separately approved merge must be undone, revert the integration audit commit
and the replayed feature commits in reverse order. Do not reset or overwrite
`main`, production state, or the three source PR branches.

Hermes long-term editorial-feedback learning is explicitly outside this stack
audit and remains follow-up work.

`NEXT=R3_INTEGRATED_VISUAL_AND_SHADOW_LIVE_ACCEPTANCE`
