# HDEC News Sensor — R4-OPS-6C Final Watch Quality Handoff

## Mandatory startup

Before material work, read and obey `AI_PROJECT_EXECUTION_STANDARD.md`, then
`docs/acceptance/PROJECT_ACCEPTANCE.md`. Tests are evidence; operator-visible
article quality is acceptance. Do not merge, deploy, dispatch workflows, send
Teams/email/Telegram, or mutate production delivery state during this audit.

```text
PROJECT=HDEC News Sensor
TASK=R4-OPS-6C final Watch quality + R4-OPS-6B restack
TASK_BRANCH=fix/r4-ops-6b-major-media-recall
ORIGINAL_R4_OPS_6B_HEAD=40f4997302cdb95085e4d51a19ab94af5f914d01
CURRENT_ORIGIN_MAIN=ad9568c905e5f7519c457d127a053c1124728525
FINAL_RESTACK_MERGE=75eca815546f34ebb041682d36a73baf98b07fcc
MAIN_RUNTIME_POLICY_DRIFT=false
DELIVERY_HEAD=COMMIT_CONTAINING_THIS_HANDOFF
DRAFT_PR_NUMBER=41
PR_STATE=DRAFT

CURRENT_STATUS=CODE_COMPLETE_PRODUCTION_UNPROVEN
CODE_COMPLETE=true
PRODUCTION_COMPLETE=false
SYSTEM_LAUNCHED=false
```

## Restack truth

- Current `origin/main` was merged into the task branch twice as main advanced;
  no reset/rebase/force operation was used.
- Every intervening main commit was authored by `github-actions[bot]` and
  changed only generated state/public Daily/Weekly artifacts.
- No non-bot runtime or policy drift appeared on main.
- Main's production-generated state/public artifacts are byte-identical to
  `origin/main` after the final merge. R4-OPS-6B runtime changes remain on the
  task branch.

## R4-OPS-6C product contract

- Tier A13 is the primary realtime default after substantive gates.
- Tier B16 is bounded secondary supply: TOP or confirmed HDEC-direct material
  events may be immediate; normal IMPORTANT rows wait 30 minutes.
- A same-event Tier-A arrival permanently replaces a held Tier-B copy. Tier B
  cannot fill unused capacity. Tier C remains non-sendable standalone supply.
- The sender exposes the complete Tier-A/B source funnel plus
  `tier_b_held`, `tier_b_replaced_by_tier_a`, `tier_b_holdback_expired`, and
  `tier_b_selected_after_holdback`.
- Proposal/discussion/local-political activity requires a separate hard factual
  material signal. Generic semiconductor is not AI centrality.
- AI financial/derivative products are Watch-rejected unless publisher evidence
  proves a separate physical/industrial business event; Editor/Daily remains
  available.
- Generated relevance/category/query text cannot manufacture executive
  materiality or IMPORTANT status.

## Exact production replay

Permanent evidence: `data/r4_ops6c_production_watch_replay.json`.

```text
KHAN 5b202fee03c23c1f 2026-08-12T06:17:21+09:00 KEEP
  BlackRock + construction labor + material AI-infrastructure capacity/workforce
FN   32c6c6ccd64378e0 2026-08-12T04:30:42+09:00 WATCH_REJECT
  AI-compute futures; excluded_financial_ai_product; Editor/Daily allowed
ET   f9d7840ab7297c93 2026-08-11T19:40:06+09:00 WATCH_REJECT
  regional semiconductor-cluster support request; AI not central/no hard event
FN   67d8faa06b353d2f 2026-08-11T16:44:43+09:00 WATCH_REJECT
  제주지사 KDD 기업 AI-pilot proposal; excluded_proposal_only
```

The fourth publisher page says `KDD 기업`, not KDDI. Observed production/page
facts and deterministic test controls are explicitly separated; unknown
production metadata is not invented.

## Preserved acceptance evidence

```text
R4_OPS_6C=PASS 42/42
R4_OPS_6B=PASS 28/28
R4_OPS_5=PASS 125/125
PUBLISHER_DIRECT=PASS 91/91
VERIFIED_STATE=PASS 50/50
NAVER_ADAPTER_AND_TARGETED_DISCOVERY=PASS
TEAMS_MAJOR_MEDIA=PASS 38/38
TEAMS_STRICT_SOURCE=PASS 62/62
TEAMS_SENDER_DRY_RUN=PASS
TEAMS_PRODUCTION_SENDER_VERIFIER=PASS 232/232
```

Permanent preserved invariants include:

```text
GOOGLE_RESOLUTION_GLOBAL_STARVATION=0
GOOGLE_RESOLUTION_PER_PUBLISHER_BOUND=PASS
SCHEDULING_HINT_AUTHORITY_LEAK=0
DONGA_KEEP_CLASS_REACHABLE_TO_POLICY=PASS
CHOSUNBIZ_KEEP_CLASS_REACHABLE_TO_POLICY=PASS
YONHAP_AI_INFRA_KEEP_CLASS_REACHABLE_TO_POLICY=PASS
CROSS_PUBLISHER_ALIAS_URL_ELEVATION=0
SBS_PREMIUM_REALTIME_AUTO_SEND=false
SPECIALIST_STANDALONE_SENDS=0
SEARCH_QUERY_CAUSED_QUALIFICATION=0
```

## Live read-only reality check

The final sensor run used the production bounded public resolver path with its
artifact and verified-state under `/tmp`. A credential-free Tier-A13 discovery
pass is derived from canonical source policy; its hints affect scheduling only,
never authority. The resolver remains capped at 60 global attempts and 12 per
publisher. A separate GET-only 24-hour Tier-A sweep returned 260 raw rows across
13 publishers; 17 normalized titles overlapped the sensor's verified Tier-A
surface.

```text
LIVE_TIER_A_DISCOVERED=48
LIVE_TIER_A_VERIFIED=23
LIVE_TIER_A_POLICY_ELIGIBLE=0
LIVE_TIER_B_DISCOVERED=16
LIVE_TIER_B_VERIFIED=11
LIVE_TIER_B_HELD=0
LIVE_TIER_B_SELECTED=0
COLLECTION_STATUS=LIVE_HEALTHY_WITH_ARTICLES
```

Zero selection was correct for that window: no verified row passed the stronger
Watch materiality/importance contract. Weak Tier-B proposal/political supply did
not reach policy. Live artifacts remain outside the repository:

- `/tmp/r4_ops6c_live_run3.json`
- `/tmp/r4_ops6c_verified_run3.json`
- `/tmp/r4_ops6c_external_run1.json`
- `/tmp/r4_ops6c_live_report_final.txt`

## Production boundary

```text
PRODUCTION_WORKFLOW_DISPATCHES=0
PRODUCTION_TEAMS_SENDS=0
PRODUCTION_SMTP_SENDS=0
PRODUCTION_TELEGRAM_SENDS=0
PRODUCTION_STATE_WRITES=0
PRODUCTION_SECRET_VARIABLE_CHANGES=0
MAIN_MERGES=0
```

No production canary was run. Keep Draft PR #41 unmerged.

`NEXT_SINGLE_ACTION=Independent Claude Code audit, then merge + real production canary.`
