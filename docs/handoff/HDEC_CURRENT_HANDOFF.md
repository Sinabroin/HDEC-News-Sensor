# HDEC News Sensor — Current Cross-Machine Handoff

PROJECT=HDEC News Sensor
REPO=Sinabroin/HDEC-News-Sensor
TASK=R4-OPS-4 overnight final article-selection zeroing and durable home-to-company handoff

BASE_MAIN=0418478f1d5389067f01bbc0ba38cdb7630ed5f6
CURRENT_ORIGIN_MAIN=9e51bdc415aa293067804a439f33eb8d317523b4

TASK_BRANCH=wip/d7ak6e-final-readiness-zeroing-handoff
REMOTE_BRANCH_HEAD=e9a4ab0bfb1932d1b8216f349adfd55edc8d0017
LOCAL_HEAD=WORKTREE_CHECKPOINT_ON_e9a4ab0bfb1932d1b8216f349adfd55edc8d0017

DRAFT_PR_NUMBER=40
DRAFT_PR_URL=https://github.com/Sinabroin/HDEC-News-Sensor/pull/40

CURRENT_STATUS=ZEROING_AUDIT_VERIFIER_CONTRACT_CHECKPOINT_IN_PROGRESS

VERIFIED_WORK_COMPLETED=
- Fresh task base was `origin/main=0418478f1d5389067f01bbc0ba38cdb7630ed5f6`; starting worktree was clean with divergence `0/0`.
- Created and pushed the durable task branch and Draft PR #40. Checkpoints `58a1e08540b44062ed32c6514b19b391043c0fa7`, `0212c2a83dad4cac93130c4b5f50fa73c5e814a1`, and `e9a4ab0bfb1932d1b8216f349adfd55edc8d0017` are on GitHub.
- Current `TEAMS_AI_NEWS_WATCH=1` was read directly from the repository variable API; it was last updated 2026-08-10 17:43:27 KST.
- Latest scheduled Watch #278 (`31402388302`) completed successfully at 2026-08-11 00:15 KST with `LIVE_HEALTHY_WITH_ARTICLES`.
- Watch #278 funnel: raw 548; publisher-direct eligible/current 44; AI core 10; HDEC relevant 10; importance-qualified 6; alert-policy eligible 6; raw primary-10 4; secondary-3 0; specialist 3; immediate major 0; specialist held 1; source-gate rejected 5; selected 0; SMTP attempted/accepted 0/0.
- Watch #278 persisted only one new held row (`아시아경제`, Intel 15B share issuance for AI funding) through autonomous state-only main commit `9e51bdc415aa293067804a439f33eb8d317523b4`; this task did not write that state.
- Downloaded calibration run `31398204844` artifact to `/tmp` only. Its exact/latest candidates and manifest report zero sends/state writes and intentional candidate_count 0.
- Calibration exact funnel: provider collected sum 548 (Naver 220 + Google 288 + publisher-direct RSS 40); publisher-direct eligible/raw bundle 35; in-coverage/relevance-qualified direct rows 10; AI-central 2; main lane 2; executive-materiality rejected 2; qualified 0; final Editor candidates 0.
- Added `scripts/audit_editorial_calibration.py`, a network-free/read-only diagnostic with an in-process self-test. It reports exact aggregates, explicitly title-only row annotations, and never guesses decisions omitted by calibration v1.
- Cross-surface diagnostic proves that five Watch-held rows already seen before calibration generation (23:30:17 KST) and inside its coverage window were absent from all 35 calibration raw rows. These include the LS Electric–GS E&C AI data-center DC-distribution partnership and HD Hyundai's KRW 956B data-center generator order.
- Reproduced the Naver adapter verifier failure on source byte-identical to origin/main. R4-R17 intentionally added `discovery_lane`; the old verifier allowlist omitted it. Applied a verifier-only allowlist/assertion fix; provider and selection behavior are unchanged.
- Reproduced `verify_naver_provider_operational_wiring.py` failures identically in a detached clean `origin/main=9e51bdc415aa293067804a439f33eb8d317523b4` worktree. Its fixed June timestamp had expired, its provider stubs did not emit the now-required source/query health audit, and publisher resolution remained network-capable; this caused a dishonest mock fallback. The verifier-only fixture now uses a current timestamp, emits deterministic audit success, supplies verified publisher authority, and stubs every live outbound boundary. All operational-wiring checks pass in 1.7 seconds without changing collector/provider behavior.
- Public exact/latest Editor 2026-08-11 both return HTTP 200 and their three core files are SHA-identical. Public Daily 2026-08-11 is HTTP 404; Daily latest and 2026-08-06 are HTTP 200.
- Latest scheduled refresh #522 (`31402738697`) fails only in `verify_naver_news_adapter.py` before live build; its log shows the same stale allowlist failure fixed on this branch.
- Last successful Daily edition/send is `2026-08-06` / `2026-08-06T08:06:20+09:00`, SMTP 250. Exact-Editor CTA commit `4b8ad57` is not its ancestor and PR #31 merged at 08:28:56 KST, after that send. No post-contract production Daily/Teams proof exists.
- Article import route exists at authenticated `POST /api/editorial/import-article`, but the public Editor bundle has empty URL and `article_import_api_configured=false`; the Editor workflow injects no `ARTICLE_IMPORT_API_URL`.

KNOWN_GOOD_BEHAVIOR=
- Post-PR #39 Watch precision gates are active: ETF/fund launch noise is blocked, major-row rejection is logged, specialists never fill unused capacity, and no post-hardening bad card is observed.
- Live collection itself is healthy; zero delivery is not collection failure.
- R4-R21 honest-empty Editor behavior is correct and public exact/latest 2026-08-11 are truthful.
- Daily exact-edition identity, immutable manifest, exact dated Editor CTA, exact dated reader CTA, and fail-closed reconstruction gate are implemented and offline-verifiable.
- Calibration-only Actions mode writes outside tracked docs, uploads an artifact, and proves zero sender/state mutation.

KNOWN_BLOCKERS=
- WATCH_RECALL_BLOCKER: strict primary-10/secondary-3 source gating held materially useful rows. Human audit marks the LS Electric–GS E&C AI data-center partnership `KEEP_FOR_TEAMS`; its specialist duplicate is supporting evidence only. This is editorial-policy evidence, not authorization to promote a source or loosen the gate.
- EDITOR_RECALL_BLOCKER: all five pre-calibration Watch-held rows were absent from the Editor calibration inventory. The Editor and Watch collection surfaces are not recall-convergent even though the exact window overlaps.
- CALIBRATION_OBSERVABILITY_BLOCKER: v1 omits the factual lead and row-level rejection decision. The new CLI refuses to infer those fields and labels title-only evidence explicitly.
- SCHEDULED_REFRESH_BLOCKER: production main still has the stale Naver verifier, so hourly refresh remains red until this branch fix is reviewed/merged and a later schedule proves green.
- DAILY_E2E_BLOCKER: no non-empty post-R4-R21 Daily has been published/sent with an exact dated Editor CTA; the current edition is empty and the last send predates the merged contract.
- ARTICLE_IMPORT_BLOCKER: no authenticated HTTPS Operator API deployment/URL wiring exists for the public Editor.

HUMAN_ARTICLE_AUDIT=
- `KEEP_FOR_TEAMS` — 한국일보: LS일렉트릭, GS건설과 AI 데이터센터 직류배전 사업 협력. System: trusted_other holdback; absent from Editor calibration. Strong construction/AI-infrastructure relevance, but any source-policy exception requires operator review.
- `KEEP_FOR_EDITOR_ONLY` — 서울경제: HD현대, 빅테크 데이터센터 발전엔진 9,560억 수주. System: trusted_other holdback; absent from Editor calibration. Material industrial order but not HDEC-direct.
- `KEEP_FOR_EDITOR_ONLY` — 파이낸셜뉴스: AI 데이터센터 가치의 핵심인 전력권. System: trusted_other holdback; absent from Editor calibration. Strategically useful context, not a confirmed HDEC event.
- `BORDERLINE` — 서울경제: Meta open-weight AI model. System: trusted_other holdback; absent from Editor calibration. AI-central but title carries no hard executive event.
- `BORDERLINE` — 아시아경제: Intel 15B share issuance for AI funding. System: trusted_other holdback in Watch #278. Material financing but weak HDEC connection; calibration occurred before this row appeared.
- `REJECT` — 애플경제: 국내 전 산업 부문, AI 데이터센터 구축에 참여. System aggregate: one of two AI-central rows rejected at executive materiality; title-only evidence has no confirmed action/scale/risk and source is neutral.
- `KEEP_FOR_EDITOR_ONLY` — 중앙일보: 일본 semiconductor hidden champion analysis. Watch major trace: AI core/HDEC relevant but insufficient importance. Calibration aggregate implies the second AI-central materiality near-miss; no real-time Teams event is proven.
- `KEEP_FOR_EDITOR_ONLY` — 연합뉴스: 3대 메가 프로젝트 초스피드 가동. Watch major trace rejects incidental AI mention; broad policy context may be useful in Editor, but not as an AI-only Teams alert.
- `REJECT` — seven in-window MOIS rows: official authority but unrelated to the AI/HDEC brief; dedicated official semantic gate correctly excludes them.

ROOT_CAUSE_OF_ZERO_EDITOR_CANDIDATES=The point-in-time Editor bundle collapsed to 10 in-window/relevance-qualified rows; only two were AI-central and both lacked a hard executive-material signal. Separately, five policy-eligible Watch-held rows already known inside the same window never entered the calibration raw inventory, proving a cross-surface supply/retention recall seam rather than a live-collection outage.

EDITOR_OVER_FILTERING_DETECTED=true
UNDER_FILTERING_DETECTED=false

RECOMMENDED_PATCH_REQUIRING_OPERATOR_REVIEW=
- Do not lower thresholds or promote publishers automatically.
- Review a bounded, Editor-only union of current collection with Watch-held rows inside the exact Daily window. It must remain non-sending, preserve authoritative source tiers, deduplicate same-event variants, and expose row rejection stages.
- In a future calibration schema, emit allowlisted row-level decisions generated during the real selection pass (never body/HTML/query/secret data). The current standalone diagnostic intentionally exposes that v1 cannot reconstruct them.

ARTICLE_IMPORT_API_CURRENT_STATUS=UNCONFIGURED_FAIL_CLOSED
ARTICLE_IMPORT_API_REQUIRED_COMPONENT=Deploy `app.operator_api:app` at authenticated HTTPS behind Cloudflare Access/Vercel Protection/company SSO (or configured GitHub OAuth session), allow the Pages origins, and inject the exact `/api/editorial/import-article` URL into the Editor build.
ARTICLE_IMPORT_API_RECOMMENDED_NEXT_STEP=Operator provisions the authenticated API and server-side secrets, wires a non-secret repository variable into `editorial-review-console.yml`, rebuilds, then performs an authenticated import smoke. Do not deploy or change variables in this task.

DAILY_STATUS=
- LAST_SUCCESSFUL_DAILY_EDITION=2026-08-06
- LAST_SUCCESSFUL_DAILY_SEND_AT=2026-08-06T08:06:20+09:00
- DAILY_EDITOR_CTA_IMPLEMENTED=true
- DAILY_EDITOR_CTA_EXACT_DATED=true
- DAILY_READER_CTA_IMPLEMENTED=true
- FIRST_POST_R4R21_NONEMPTY_DAILY_PROVEN=false
- FIRST_CONTROLLED_DAILY_TEAMS_SEND_PROVEN=false

PRODUCTION_BOUNDARIES=
- No main merge, production Teams/SMTP/Telegram send, production workflow dispatch, repository variable/secret change, production-state write, force push, or rebase.
- Only the dedicated WIP branch receives source/document commits and normal pushes.

VERIFIERS_RUN=
- `bash -n scripts/hdec_cross_machine_status.sh` — PASS
- `./scripts/hdec_cross_machine_status.sh` — PASS for Git/fetch/status behavior
- `python3 scripts/verify_naver_news_adapter.py` — origin/main-identical baseline FAIL 1; branch PASS 14
- `python3 scripts/verify_naver_provider_operational_wiring.py` — clean origin/main baseline FAIL 2; branch PASS (all checks, temp DB, network-free provider boundaries)
- `python3 scripts/audit_editorial_calibration.py --self-test` — PASS
- Actual calibration audit — PASS with network_calls=0, sends=0, production_state_writes=0

FAILING_VERIFIERS=
- Production scheduled refresh #522: stale origin/main `verify_naver_news_adapter.py` allowlist, fixed only on this WIP branch pending review/merge.
- No failing branch-local verifier is known at this checkpoint. Complete the remaining offline matrix before the final seal.

FILES_CHANGED=
- `docs/handoff/HDEC_CURRENT_HANDOFF.md`
- `docs/handoff/COMPANY_RESUME_PROMPT.md`
- `scripts/hdec_cross_machine_status.sh`
- `scripts/verify_naver_news_adapter.py`
- `scripts/verify_naver_provider_operational_wiring.py`
- `scripts/audit_editorial_calibration.py`

COMMITS_CREATED=
- `58a1e08540b44062ed32c6514b19b391043c0fa7 chore: establish durable cross-machine handoff`
- `0212c2a83dad4cac93130c4b5f50fa73c5e814a1 fix: align Naver discovery lane verifier contract`
- `e9a4ab0bfb1932d1b8216f349adfd55edc8d0017 feat: add network-free calibration rejection audit`
- Naver operational-wiring verifier checkpoint pending.

PRODUCTION_SENDS=0
PRODUCTION_STATE_WRITES=0

REQUIRES_OPERATOR_APPROVAL=true
- Required for source-tier/recall policy, Editor-only cross-lane supply changes, Operator API deployment/variables, and the first controlled non-empty Daily → Teams → exact Editor CTA proof.

NEXT_SINGLE_ACTION=Run the complete relevant offline verifier matrix, classify any failure against clean origin/main, then integrate fresh origin/main if conflict-free and finalize the Draft PR/handoff seal.

COMPANY_RESUME_COMMANDS=
```bash
cd /mnt/d/HDEC-Projects/AI-DesignLab-Sensor || exit 1
git fetch origin --prune
git status --short --branch
git switch wip/d7ak6e-final-readiness-zeroing-handoff \
  || git switch --track origin/wip/d7ak6e-final-readiness-zeroing-handoff
git pull --ff-only origin wip/d7ak6e-final-readiness-zeroing-handoff
./scripts/hdec_cross_machine_status.sh
codex exec -C . - < docs/handoff/COMPANY_RESUME_PROMPT.md
```

The live branch and commit facts printed by `scripts/hdec_cross_machine_status.sh` are authoritative. A Git commit cannot contain its own resulting SHA, so `LOCAL_HEAD` identifies the parent on which the in-progress checkpoint is being prepared.
