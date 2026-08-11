# HDEC News Sensor — R4-OPS-6B Major-Media Recall Repair Handoff

## Mandatory startup

Before material work, read and obey `AI_PROJECT_EXECUTION_STANDARD.md`, then
`docs/acceptance/PROJECT_ACCEPTANCE.md`. Existing tests are evidence, not product
authority. Do not merge, deploy, dispatch workflows, send Teams/email/Telegram,
or mutate production delivery state during this repair/audit.

PROJECT=HDEC News Sensor
TASK=R4-OPS-6B major-media discovery / verification recall repair
TASK_BRANCH=fix/r4-ops-6b-major-media-recall
BASE_SHA=e271065c3399751af15080503d4e38792154ea6b
AUDITED_R4_OPS_5_MERGE=66ce8cea03fa64216f2b41d56fce2a2f966ba26f
MAIN_RUNTIME_DRIFT=false
IMPLEMENTATION_HEAD=LIVE_TASK_BRANCH_REF_MATCHES_LOCAL_HEAD
REMOTE_BRANCH_HEAD=LIVE_TASK_BRANCH_REF_MATCHES_LOCAL_HEAD
DRAFT_PR_NUMBER=PENDING_COMMIT_PUSH
PR_STATE=DRAFT_REQUIRED

CURRENT_STATUS=CODE_COMPLETE_PRODUCTION_UNPROVEN
CODE_COMPLETE=true
PRODUCTION_COMPLETE=false
SYSTEM_LAUNCHED=false

## Governing acceptance contract

- `AI_PROJECT_EXECUTION_STANDARD.md` is unchanged.
- The HDEC overlay is version 1.2 and seals HDEC-DEFECT-006.
- Discovery/provider metadata may schedule or prioritise URL resolution, but it
  never grants publisher authority, Tier A/B, or Teams eligibility.
- Final source authority remains exact resolved/canonical URL identity under the
  R4-OPS-5 source contract. Unknown children, sibling publications, aliases over
  foreign URLs, unresolved wrappers, IT조선-as-조선일보, and SBS Premium remain
  non-authoritative/non-realtime.

## Implemented repair

- Google RSS resolution uses a non-authoritative scheduling key derived from an
  already-resolved host, Google `<source url>`, or a hashed source reference.
  It no longer treats every wrapper as one `news.google.com` publisher. The
  global limit remains 60, the per-publisher hint limit remains 12, actual
  publisher fetches retain a one-at-a-time exact-host lock, and exact configured
  A/B hints receive scheduling priority without gaining authority.
- Structured extraction now covers JSON-LD `articleBody` plus bounded exact-host
  body containers for the affected A/B publisher matrix. A strict fallback can
  prove article identity only for an exact configured A/B host, article-shaped
  fetched URL, strongly agreeing page/feed headline, non-error page, and a
  canonical URL that does not escape the same publication.
- `publisher_verification_strength` is explicit:
  `full_body`, `structured_metadata`, `metadata_only_exact_host`, or
  `official_registry_feed`. Authority cache policy is bumped to v2 so prior
  proof is revalidated. Metadata-only identity never supplies AI centrality,
  materiality, executive relevance, importance, or query evidence.
- The legacy Naver `primary_publisher_lane` configuration now targets all
  operator Tier-A13 (`primary_10` + `secondary_3`) and Tier-B16
  (`major_secondary`) publishers. It uses 58 bounded queries, at most 2 accepted
  rows per query and 80 total, with exact configured destination matching only.
  Topic coverage includes AI data centres/power, GPU/HBM infrastructure,
  investment/contracts/MOU, physical AI, smart construction/BIM/robotics,
  Hyundai E&C/construction peers, and national AI policy/regulation.
- The overly broad `t.co` substring ban was corrected to exact host matching;
  `mt.co.kr` (머니투데이) and `dt.co.kr` (디지털타임스) no longer disappear.
- Every alert-policy-eligible Teams row now emits a safe categorical trace with
  a non-reversible reference, sanitized display source, exact resolved identity
  or `unresolved_or_unconfigured`, source tier, Teams lane, content state, and
  source-gate result/reason. It never prints a URL, body, credential, or address.
  Zero-send logs also print `QUARANTINE_REASON_COUNTS`,
  `QUARANTINED_TIER_A_ROWS`, `QUARANTINED_TIER_B_ROWS`, and
  `POLICY_ELIGIBLE_BY_TIER`.
- The three recovered audit classes (동아일보 GS건설×LS일렉트릭, 조선비즈
  NVIDIA×Wall Street AI infrastructure, 연합뉴스 canonical copy of the same
  financing-platform event) are committed with exact observed metadata. Missing
  resolved URLs remain null; separately labelled synthetic destinations are used
  only for deterministic end-to-end adversarial replay.

## Deterministic evidence

- `scripts/verify_r4_ops6b_major_media_recall.py`: PASS 28/28 on the final
  implementation; 275-row Google distribution, one-publisher bound,
  no scheduling authority leak, A13/B16 JSON-LD matrices, 12-outlet DOM matrix,
  metadata-only bounds, all-29 Naver coverage, exact destination matching,
  recall classes, R4-OPS-5 precision rows, and safe zero-send traces.
- `scripts/verify_r4_ops5_production_acceptance.py`: PASS 124/124;
  `CROSS_PUBLISHER_ALIAS_URL_ELEVATION=0`, SBS Premium realtime false, ETF and
  stock/theme REJECT, 한국일보 LS×GS KEEP, 서울경제 HD현대 DC engine KEEP, and
  `SEARCH_QUERY_CAUSED_QUALIFICATION=0`.
- Publisher-direct collector: PASS 91/91. Verified-state/concurrency suite:
  PASS 50/50. Expanded Naver discovery lane: PASS 41/41.
- Teams major-media gate: PASS 38/38. Strict source gate: PASS 62/62. Primary
  trace: PASS 23/23. Teams AI selector: PASS.
- All deterministic tests used zero external network, SMTP, Teams, Telegram,
  workflow dispatch, or production-state writes unless explicitly identified as
  the separate GET-only live probe below.

## GET-only live evidence (`/tmp` only)

The first post-repair sample showed 328 raw candidates, 28 major-media hints,
36 Google attempts across 38 active scheduling buckets, but only 4 major-hint
attempts. A scheduling-priority correction raised the same-window major-hint
attempts to 14 while keeping Google at 36 attempts and 38 active buckets.

Final bounded sample:

```text
LIVE_RAW_CANDIDATES=328
LIVE_MAJOR_MEDIA_RAW=28
LIVE_MAJOR_MEDIA_RESOLUTION_ATTEMPTED=14
LIVE_MAJOR_MEDIA_VERIFIED=0
LIVE_TIER_A_VERIFIED=0
LIVE_TIER_B_VERIFIED=0
LIVE_POLICY_ELIGIBLE_MAJOR=0
COLLECTION_STATUS=LIVE_HEALTHY_WITH_ARTICLES
```

Google batchexecute decoding is POST-based, so it was explicitly disabled for
the mandated GET-only check. Consequently Google wrapper rows could exercise
fair scheduling but could not truthfully become publisher-authoritative in that
sample. A separate GET-only probe of three exact A/B publisher URLs recovered
from the audit verified all 3 as `full_body` (한국경제 2, 이데일리 1), showing that
configured major pages no longer disappear solely through body extraction.

Artifacts are outside the repository:

- `/tmp/r4_ops6b_live_brief_after.json`
- `/tmp/r4_ops6b_verified_state_after.json`

## Production boundary

PRODUCTION_WORKFLOW_DISPATCHES=0
PRODUCTION_TEAMS_SENDS=0
PRODUCTION_SMTP_SENDS=0
PRODUCTION_TELEGRAM_SENDS=0
PRODUCTION_STATE_WRITES=0
PRODUCTION_SECRET_VARIABLE_CHANGES=0
MAIN_MERGES=0

No live production send/canary has been performed. The next action is an
independent focused Claude Code audit of R4-OPS-6B; keep the Draft PR unmerged.

NEXT_SINGLE_ACTION=Independent focused Claude Code audit of R4-OPS-6B.
