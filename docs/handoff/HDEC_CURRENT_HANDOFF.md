# HDEC News Sensor — Current Cross-Machine Handoff

PROJECT=HDEC News Sensor
REPO=Sinabroin/HDEC-News-Sensor
TASK=R4-OPS-4 overnight final article-selection zeroing and durable home-to-company handoff

BASE_MAIN=0418478f1d5389067f01bbc0ba38cdb7630ed5f6
CURRENT_ORIGIN_MAIN=9e51bdc415aa293067804a439f33eb8d317523b4

TASK_BRANCH=wip/d7ak6e-final-readiness-zeroing-handoff
REMOTE_BRANCH_HEAD=58a1e08540b44062ed32c6514b19b391043c0fa7
LOCAL_HEAD=WORKTREE_CHECKPOINT_ON_58a1e08540b44062ed32c6514b19b391043c0fa7

DRAFT_PR_NUMBER=40
DRAFT_PR_URL=https://github.com/Sinabroin/HDEC-News-Sensor/pull/40

CURRENT_STATUS=SAFE_NAVER_VERIFIER_FIX_CHECKPOINT_IN_PROGRESS

VERIFIED_WORK_COMPLETED=
- Fresh origin/main fetched and verified at `0418478f1d5389067f01bbc0ba38cdb7630ed5f6`.
- Starting main worktree was clean with divergence `0/0`.
- Dedicated task branch created from fresh origin/main; no pre-existing remote task branch was found.
- Codex CLI help confirms that `codex exec -C . - < prompt-file` reads the continuation prompt from stdin.
- First durable checkpoint `58a1e08540b44062ed32c6514b19b391043c0fa7` is on GitHub and Draft PR #40 is open.
- Latest scheduled Watch run #278 succeeded with live articles and then advanced main only through `data/teams_push_state.json` at `9e51bdc415aa293067804a439f33eb8d317523b4`.
- Reproduced the Naver adapter verifier failure on source byte-identical to origin/main: its allowlist omitted the intentional R4-R17 `discovery_lane` provenance field.
- Applied the smallest verifier-only fix and added a deterministic assertion that the top-level and metadata lanes remain `general`, without changing provider or selection behavior.

KNOWN_GOOD_BEHAVIOR=
- R4-R21 honest-empty behavior and PR #39 Watch executive-materiality/fund-product hardening are on main.
- TEAMS_AI_NEWS_WATCH was restored before this task; Watch live collection had produced articles.
- Public exact and latest Editor pages for 2026-08-11 were published from an honest-empty calibration.

KNOWN_BLOCKERS=
- GitHub CLI has no persisted login, but the existing credential-store token works when passed process-locally without printing it; SSH handles normal branch pushes.
- The detailed Watch/Editor/Daily evidence audit is still in progress.
- Existing 2026-08-11 calibration diagnostics contain aggregate selection counters and sanitized raw supply, but not row-level rejection decisions; near-miss reconstruction is in progress.
- Any production-policy tuning or controlled production Daily send requires operator approval.

PRODUCTION_BOUNDARIES=
- No main merge, production Teams/SMTP/Telegram send, production workflow dispatch, repository variable/secret change, production-state write, force push, or rebase.
- Only the dedicated WIP branch may receive source/document commits and normal pushes.

VERIFIERS_RUN=
- `bash -n scripts/hdec_cross_machine_status.sh`
- `./scripts/hdec_cross_machine_status.sh` (read-only status/fetch helper)
- `python3 scripts/verify_naver_news_adapter.py` — PASS after reproducing one identical origin/main failure.

FAILING_VERIFIERS=
- None for the changed surface at this checkpoint.

FILES_CHANGED=
- `docs/handoff/HDEC_CURRENT_HANDOFF.md`
- `docs/handoff/COMPANY_RESUME_PROMPT.md`
- `scripts/hdec_cross_machine_status.sh`
- `scripts/verify_naver_news_adapter.py`

COMMITS_CREATED=
- `58a1e08540b44062ed32c6514b19b391043c0fa7 chore: establish durable cross-machine handoff`
- Naver verifier fix checkpoint pending.

PRODUCTION_SENDS=0
PRODUCTION_STATE_WRITES=0

REQUIRES_OPERATOR_APPROVAL=true
- Required for any production-policy tuning and for the eventual controlled production Daily/Teams proof.

NEXT_SINGLE_ACTION=Complete the read-only Watch and 2026-08-11 Editor calibration funnel audit from GitHub Actions evidence and local artifacts.

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

The live branch and commit facts printed by `scripts/hdec_cross_machine_status.sh` are authoritative. The `LOCAL_HEAD` and `REMOTE_BRANCH_HEAD` labels above describe the checkpoint being prepared because a Git commit cannot contain its own resulting SHA.
