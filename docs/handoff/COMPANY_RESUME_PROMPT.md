# Codex continuation prompt — HDEC News Sensor R4-OPS-4

Continue the HDEC News Sensor R4-OPS-4 final article-selection zeroing and cross-machine handoff task from the COMPANY machine. This file is the complete continuation context; do not assume access to the HOME chat.

Repository and branch:

- GitHub: `Sinabroin/HDEC-News-Sensor`
- COMPANY checkout: `/mnt/d/HDEC-Projects/AI-DesignLab-Sensor`
- Durable task branch: `wip/d7ak6e-final-readiness-zeroing-handoff`
- Base main at task start: `0418478f1d5389067f01bbc0ba38cdb7630ed5f6`
- Draft PR: `#40` (`https://github.com/Sinabroin/HDEC-News-Sensor/pull/40`)
- Teams Watch can autonomously advance `origin/main` with state-only commits.

Begin exactly as follows:

```bash
cd /mnt/d/HDEC-Projects/AI-DesignLab-Sensor || exit 1
git fetch origin --prune
git status --short --branch
git switch wip/d7ak6e-final-readiness-zeroing-handoff \
  || git switch --track origin/wip/d7ak6e-final-readiness-zeroing-handoff
git pull --ff-only origin wip/d7ak6e-final-readiness-zeroing-handoff
./scripts/hdec_cross_machine_status.sh
```

Read `docs/handoff/HDEC_CURRENT_HANDOFF.md` before doing any work. Treat the live status helper output and remote Git refs as authoritative. Inspect whether `origin/main` moved. Merge with `git merge --no-edit origin/main` only if integration is necessary. Never rebase, reset shared work, force push, or force-with-lease. If a merge conflicts, stop conflict resolution, preserve the last clean remote checkpoint, and document the conflict.

Continue `NEXT_SINGLE_ACTION` from the handoff. Keep working on the same task branch and update the existing Draft PR; do not create a second PR. Before every meaningful checkpoint: inspect the diff, confirm no secret or production-state change, update both handoff files, run relevant offline verifiers, commit, push normally, and verify local and remote branch heads match.

Product context to preserve:

- R4-R21 honest-empty behavior is merged.
- PR #39 Watch executive-materiality/fund/ETF noise hardening is merged.
- `TEAMS_AI_NEWS_WATCH` was restored to production before this task.
- Watch live collection has previously proven healthy with articles and intentionally favors precision.
- Specialist/neutral publishers do not automatically fill unused send capacity.
- Public Editor exact and latest pages for 2026-08-11 exist and are intentionally empty.
- Calibration Actions run `31398204844` succeeded with `candidate_count=0`, `qualified_candidates=0`, and zero sends/state writes.
- The public Editor reports article import API unconfigured; this is separate from live news collection.
- The stale Naver adapter verifier was reproduced against origin/main and minimally repaired on this branch by allowing and asserting the intentional `discovery_lane` provenance contract; provider and selection semantics were not changed.
- Do not auto-tune publisher tiers, executive-materiality thresholds, AI centrality, stock/ETF exclusions, specialist send policy, or Teams volume. Those require operator review.

Safe autonomous scope:

- Git/GitHub reads, Actions log/artifact and public HTTP GET inspection.
- Local source/docs changes on this WIP branch.
- Network-free tests and diagnostics.
- Low-risk objective engineering fixes that do not broaden send eligibility and have deterministic regression coverage.
- Commits, normal branch pushes, Draft PR updates, and handoff maintenance.

Hard production boundary:

```text
PRODUCTION_TEAMS_SENDS=0
SMTP_ATTEMPTS=0
TELEGRAM_SENDS=0
PRODUCTION_VARIABLE_CHANGES=0
PRODUCTION_WORKFLOW_DISPATCHES=0
PRODUCTION_STATE_WRITES=0
MAIN_MERGES=0
```

Never merge to main, send to production, dispatch a mutating/sending workflow, alter variables/secrets, directly edit production ledgers, rebase, or force push. Record any such next step as `REQUIRES_OPERATOR_APPROVAL=true` and continue other safe work.

Primary remaining objectives are to audit the latest post-PR #39 Watch scheduled funnel and most relevant rejected rows; reconstruct the 2026-08-11 Editor calibration funnel and near misses; audit the import API and Daily exact-dated CTA/E2E state; reproduce and, only if objectively stale, minimally fix the Naver `discovery_lane` verifier contract; add a network-free rejection diagnostic only if existing tooling is insufficient; run all relevant offline verifiers; integrate fresh origin/main if needed; then update/push the same Draft PR and final handoff.

To start Codex from this file after the bootstrap commands, use the CLI form verified from `codex exec --help`:

```bash
codex exec -C . - < docs/handoff/COMPANY_RESUME_PROMPT.md
```
