# HDEC News Sensor — Current Cross-Machine Handoff

PROJECT=HDEC News Sensor
REPO=Sinabroin/HDEC-News-Sensor
TASK=R4-OPS-4 overnight final article-selection zeroing and durable home-to-company handoff

BASE_MAIN=0418478f1d5389067f01bbc0ba38cdb7630ed5f6
CURRENT_ORIGIN_MAIN=0418478f1d5389067f01bbc0ba38cdb7630ed5f6

TASK_BRANCH=wip/d7ak6e-final-readiness-zeroing-handoff
REMOTE_BRANCH_HEAD=PENDING_FIRST_CHECKPOINT
LOCAL_HEAD=WORKTREE_CHECKPOINT_BASE_0418478f1d5389067f01bbc0ba38cdb7630ed5f6

DRAFT_PR_NUMBER=PENDING
DRAFT_PR_URL=PENDING

CURRENT_STATUS=HANDOFF_FRAMEWORK_CHECKPOINT_IN_PROGRESS

VERIFIED_WORK_COMPLETED=
- Fresh origin/main fetched and verified at `0418478f1d5389067f01bbc0ba38cdb7630ed5f6`.
- Starting main worktree was clean with divergence `0/0`.
- Dedicated task branch created from fresh origin/main; no pre-existing remote task branch was found.
- Codex CLI help confirms that `codex exec -C . - < prompt-file` reads the continuation prompt from stdin.

KNOWN_GOOD_BEHAVIOR=
- R4-R21 honest-empty behavior and PR #39 Watch executive-materiality/fund-product hardening are on main.
- TEAMS_AI_NEWS_WATCH was restored before this task; Watch live collection had produced articles.
- Public exact and latest Editor pages for 2026-08-11 were published from an honest-empty calibration.

KNOWN_BLOCKERS=
- GitHub CLI is installed but not authenticated on the HOME machine; Draft PR creation may require an available Git credential or GitHub web/API authentication.
- The detailed Watch/Editor/Daily evidence audit is still in progress.
- Any production-policy tuning or controlled production Daily send requires operator approval.

PRODUCTION_BOUNDARIES=
- No main merge, production Teams/SMTP/Telegram send, production workflow dispatch, repository variable/secret change, production-state write, force push, or rebase.
- Only the dedicated WIP branch may receive source/document commits and normal pushes.

VERIFIERS_RUN=
- `bash -n scripts/hdec_cross_machine_status.sh`
- `./scripts/hdec_cross_machine_status.sh` (read-only status/fetch helper)

FAILING_VERIFIERS=
- None at this checkpoint.

FILES_CHANGED=
- `docs/handoff/HDEC_CURRENT_HANDOFF.md`
- `docs/handoff/COMPANY_RESUME_PROMPT.md`
- `scripts/hdec_cross_machine_status.sh`

COMMITS_CREATED=
- First handoff-framework checkpoint pending.

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
