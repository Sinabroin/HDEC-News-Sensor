# HDEC News Sensor — R4-OPS-5 Production Acceptance Handoff

PROJECT=HDEC News Sensor
REPO=Sinabroin/HDEC-News-Sensor
TASK=R4-OPS-5 final production acceptance closure

BASE_ORIGIN_MAIN=8e70f3921c744eaaee6b76c600bbd8fd2636db96
FINAL_ORIGIN_MAIN=10b2ce7754d06a3a08702960c96ab94368ae422d
TASK_BRANCH=wip/d7ak6e-final-readiness-zeroing-handoff
IMPLEMENTATION_HEAD=d33d12df1d71249525592ac9f1dc539ffa45130d
FINAL_MAIN_INTEGRATION_HEAD=590df412239fce36e4de545b2796fbc86e9b27ef
REMOTE_BRANCH_HEAD=LIVE_TASK_BRANCH_REF_MATCHES_LOCAL_HEAD

PR_NUMBER=40
PR_URL=https://github.com/Sinabroin/HDEC-News-Sensor/pull/40
PR_STATE=DRAFT

CURRENT_STATUS=CODE_COMPLETE_REMOTE_SEAL_VERIFIED
CODE_COMPLETE=true
SYSTEM_LAUNCHED=false
REMOTE_CHECKPOINT_AVAILABLE=true
CROSS_MACHINE_HANDOFF_READY=true

## Implemented production behavior

- Publisher identity is exact-domain by default. `www` is the conventional apex alias; every other publication subdomain must be explicitly enumerated. Display-name aliases are exact and an authoritative sibling URL defeats a parent-publication alias.
- `chosun.com` / `www.chosun.com` resolve to 조선일보. `it.chosun.com`, `biz.chosun.com`, and `sports.chosun.com` cannot inherit 조선일보. IT조선 is explicit Tier C; 조선비즈 is explicit Tier B; sports.chosun.com is not automatic.
- Teams source policy is operator-explicit: Tier A is the existing 13 core publishers, Tier B is the exact 16-publisher general/economic/industrial allowlist, and the named specialist/niche publishers are Tier C. Tier B never bypasses article semantics, authority, freshness, materiality, stock/theme, dedup, or importance gates.
- Realtime Teams deterministically excludes explicit publisher sections `칼럼/오피니언/사설/논설/기고/기고문/전문가칼럼` and equivalent bracketed title markers. Only title and publisher-section evidence are read; generated summary and provider query text are excluded.
- Normal `important` cards are paced to the best one per rolling 60-minute window when supply exists. Unsent eligible rows remain out of the sent ledger and are reconsidered later. TOP and HDEC-direct rows bypass the pace; urgent sends do not consume the normal window. Failed SMTP never advances pacing or dedup. Filler remains zero.
- The observed IT조선 contribution is rejected independently by the opinion gate and Tier-C standalone gate. The observed 연합뉴스 ETF and stock/theme regressions remain rejected. The 한국일보 LS일렉트릭×GS건설 직류배전 case and 서울경제 HD현대 9,560억 발전엔진 order are Tier-B eligible after substantive gates.
- A committed deterministic real-corpus replay prints the required title/source/identity/tier/semantic/materiality/opinion/policy/final-reason fields for every row. `SEARCH_QUERY_CAUSED_QUALIFICATION=0` is asserted.
- Daily immutable manifests are version 2 and explicitly record `edition_status` and `article_count`. Truthful empty editions render and publish with the exact no-qualified-news status, exact dated Editor CTA, and exact dated reader CTA. Mutable latest is never a Teams action authority.
- Daily delivery state is version 3 and records `delivery_kind=nonempty_digest|empty_status` plus article count. v1/v2 state is upgraded in memory; exact legacy claims can complete. Empty status succeeds once and retries fail before transport.
- The real Daily publish/send orchestrator is exercised offline against the committed zero-candidate 2026-08-11 Editor bundle: temp-only publication, one fake SMTP 250, one `empty_status` success, and retry transport count unchanged.
- Daily schedule is Review Editor 07:20 KST, Daily primary 07:50, retry 08:05, final retry 08:15. GitHub cron remains best-effort; state is duplicate authority.
- Empty Review candidate bundles are now valid, loadable exact-edition inputs instead of operational errors. Exact-edition browser reconstruction accepts a verified version-2 empty manifest.
- Article import remains fail-closed because no authenticated public API URL is configured. The committed exact/latest 2026-08-11 Editor and template now start visibly disabled and accurately say unavailable; configured builds explicitly re-enable the control. Edit, drag/order, preview, and exact-edition functions remain active.
- Scheduled live refresh now runs the repaired Naver adapter, deterministic operational wiring, live-ingestion provenance, and publisher-direct verifiers before live build. The complete 36-command scheduled preflight reached all downstream gates locally.
- PR #40's existing Naver discovery-lane, Naver operational fixture, publisher-direct pin/mutable-ledger, live-ingestion provenance, calibration CLI, and cross-machine handoff repairs are preserved.

## Final post-merge acceptance evidence

- `scripts/verify_r4_ops5_production_acceptance.py` — PASS 86/86; external network 0; production SMTP/Teams/state writes 0.
- Exact replay: IT조선 contribution REJECT; 연합뉴스 ETF REJECT; stock/theme REJECT; LS Electric×GS E&C KEEP; HD현대 Tier-B material event KEEP; specialist opinion context Editor-only; weak Tier-B publisher-alone row REJECT.
- Scheduled-live-refresh exact offline preflight — 36 commands, 0 failures, from News Censor through repaired Naver/provider gates to dashboard freshness. The stale AI-market verifier was aligned with the canonical `AI centrality + title materiality` contract; runtime semantic policy was not weakened.
- Naver adapter PASS; Naver operational wiring PASS; live ingestion PASS; publisher-direct 91/91.
- Watch materiality PASS; executive qualification hardening PASS; material AI infrastructure recall PASS; observed false-positive regression 100/100.
- Teams selector PASS; Teams production 232/232; major-media gate 38/38; strict-source gate 62/62; stock-market exclusion 61/61; state/dedup PASS.
- Editor console 200/200; editorial briefings 344/344; Daily deep link 118/118; Daily lead source 24/24; immutable manifest 31/31. One grouped Editor invocation hit the known Windows headless-Chrome 45-second startup timeout; the immediate isolated final run passed all 200 checks with zero external requests.
- Production ledgers remained byte-identical throughout the final post-merge verification: Teams SHA `76c5932017ff2f5f7a46917fa7b14315b29f44fed0c7d4d2fc423efaec4583f4`; Daily SHA `68838c384c07ce87a711277850cff1087bc6cac526cfdce3a61f1e05f1fe7155`.
- `VERIFIERS_FAIL=0`, `EXTERNAL_TEST_NETWORK_CALLS=0`, `PRODUCTION_SMTP_ATTEMPTS=0`, `PRODUCTION_TEAMS_SENDS=0`, and `PRODUCTION_STATE_WRITES=0` in the final accepted runs.

## Final integration status

`origin/main` advanced after the initial fetch only through state commit `10b2ce7754d06a3a08702960c96ab94368ae422d` (`data/teams_push_state.json`). The implementation was committed at `d33d12df1d71249525592ac9f1dc539ffa45130d`, then current main was merged without rebase or conflict at `590df412239fce36e4de545b2796fbc86e9b27ef`. The full post-merge seal above passed. The final handoff commit is pushed normally and local/remote equality is verified after push; use the live task-branch ref for its self-referential SHA.

## Production boundary and remaining proof

PRODUCTION_TEAMS_SENDS=0
PRODUCTION_SMTP_ATTEMPTS=0
PRODUCTION_TELEGRAM_SENDS=0
PRODUCTION_STATE_WRITES=0
PRODUCTION_VARIABLE_CHANGES=0
PRODUCTION_WORKFLOW_DISPATCHES=0
MAIN_MERGES=0

`CODE_COMPLETE=true` is the offline engineering verdict. It is not launch proof. A later operator-authorized merge and real-world evidence are still required:

- REAL_WATCH_SEND_PASS
- REAL_DAILY_SEND_PASS (empty or non-empty natural edition)
- REAL_EDITOR_LINK_PASS
- REAL_PUBLIC_DAILY_LINK_PASS
- natural scheduled-live-refresh green proof on deployed main

ARTICLE_IMPORT_UI_STATUS=DISABLED_ACCURATELY_LABELLED_API_UNCONFIGURED
ARTICLE_IMPORT_API_STATUS=FAIL_CLOSED_NO_NEW_AUTH_DEPLOYMENT
REQUIRES_OPERATOR_ACTION=Review and merge Draft PR #40, then observe natural production runs and validate the real Teams/Editor/dated-Daily links.
NEXT_SINGLE_ACTION=Operator review and merge Draft PR #40; then observe the next natural Watch, Daily, Editor, dated-reader, and scheduled-refresh paths.

## Company-machine resume

Do not modify, reset, clean, or stash the dirty company checkout. Use the existing task branch/worktree or create a separate clean worktree. The live refs and `scripts/hdec_cross_machine_status.sh` output are authoritative.

```bash
cd /mnt/d/HDEC-Projects/HDEC-News-Sensor-R4OPS5 || exit 1
git fetch origin --prune
git status --short --branch
git switch wip/d7ak6e-final-readiness-zeroing-handoff
git pull --ff-only origin wip/d7ak6e-final-readiness-zeroing-handoff
./scripts/hdec_cross_machine_status.sh
```
