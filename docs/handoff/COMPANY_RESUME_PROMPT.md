# Codex continuation prompt — HDEC News Sensor R4-OPS-5

R4-OPS-5 is code-complete on its remote WIP checkpoint unless live refs prove otherwise. Before material work, read and obey `AI_PROJECT_EXECUTION_STANDARD.md`, then read `docs/acceptance/PROJECT_ACCEPTANCE.md`, and state the user-visible acceptance matrix. After both governing documents, read `docs/handoff/HDEC_CURRENT_HANDOFF.md` completely, then treat live Git refs and `scripts/hdec_cross_machine_status.sh` as authoritative.

Repository:

- GitHub: `Sinabroin/HDEC-News-Sensor`
- clean R4-OPS-5 worktree: `/mnt/d/HDEC-Projects/HDEC-News-Sensor-R4OPS5`
- dirty company checkout (must not modify/reset/clean/stash): `/mnt/d/HDEC-Projects/AI-DesignLab-Sensor`
- branch: `wip/d7ak6e-final-readiness-zeroing-handoff`
- Draft PR: #40

Bootstrap:

```bash
cd /mnt/d/HDEC-Projects/HDEC-News-Sensor-R4OPS5 || exit 1
git fetch origin --prune
git status --short --branch
git switch wip/d7ak6e-final-readiness-zeroing-handoff
git pull --ff-only origin wip/d7ak6e-final-readiness-zeroing-handoff
./scripts/hdec_cross_machine_status.sh
```

R4-OPS-5 implementation contract already present on the branch:

- exact-by-default publisher domain authority; explicit aliases and publication domains only;
- IT조선 is not 조선일보 and is Tier C; 조선비즈 is distinct explicit Tier B; sports.chosun.com is never 조선일보;
- exact Tier A (13) / Tier B (16) / Tier C specialist policy;
- article semantics still control all eligibility; publisher name and query text never qualify;
- deterministic opinion/contribution realtime exclusion from authoritative title/section evidence;
- one best normal `important` card per rolling 60 minutes with unsent backlog preservation; TOP/HDEC-direct bypass and do not consume the normal window;
- real-corpus replay including IT조선, 연합뉴스 ETF, stock/theme, 한국일보 LS Electric×GS E&C, 서울경제 material infrastructure, specialist context, and weak Tier-B publisher-alone cases;
- truthful immutable empty Daily status and normal non-empty Daily, exact dated Editor/reader actions, version-3 idempotent delivery-kind ledger;
- Review empty bundle loading and exact empty-edition reconstruction;
- unavailable article-import UI visibly disabled while the API remains fail-closed;
- 07:20 Review followed by 07:50/08:05/08:15 Daily target/retries;
- repaired PR #40 Naver/live-ingestion/publisher-direct verifier chain and calibration/handoff tooling.

Production boundaries remain absolute:

```text
PRODUCTION_TEAMS_SENDS=0
PRODUCTION_SMTP_ATTEMPTS=0
PRODUCTION_TELEGRAM_SENDS=0
PRODUCTION_STATE_WRITES=0
PRODUCTION_VARIABLE_CHANGES=0
PRODUCTION_WORKFLOW_DISPATCHES=0
MAIN_MERGES=0
```

Never rebase, force push, merge PR #40, write main, dispatch a production workflow, change variables/secrets, send SMTP/Teams/Telegram, or mutate production ledgers. If current `origin/main` moved, merge it into the WIP branch preserving history; stop on a genuine conflict.

The final offline seal is scheduled preflight 36/36, R4-OPS-5 replay 86/86, Editor 200/200, and all required Watch/Daily suites green with zero external test network, production sends, or direct production-state writes. Do not redo implementation when the remote branch contains this seal.

Before any additional stop, update both handoff files, commit coherently, push normally, verify remote/local equality, and update Draft PR #40. `SYSTEM_LAUNCHED=false` remains mandatory until an operator merges and real production evidence proves Watch send, Daily send, exact Editor link, and immutable dated reader link.

If the remote seal is already complete, do not redo implementation. The next single action is operator review/merge of Draft PR #40 followed by observation of natural production runs.
