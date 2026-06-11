---
name: hdec-boundary-check
description: Use after each HDEC Executive Radar domain step. Checks that file ownership boundaries from CLAUDE.md were not violated.
---

# HDEC Boundary Check

After each domain task:

1. Read CLAUDE.md section 4 domain ownership rules.
2. Run:
   - `git diff --stat`
   - `git diff --name-only`
3. Check whether the current domain edited only its owned files.
4. If another domain file changed:
   - decide whether it was a necessary interface change
   - otherwise revert or refactor
5. Confirm:
   - only app/db.py imports sqlite3
   - app/main.py only orchestrates
   - scoring does not collect articles
   - insight does not recompute alert_grade
   - notification does not auto-send
   - feedback does not update keyword weight in P0-A
6. If a mistake occurred, create `.claude/skills/lessons/<domain>-<summary>/SKILL.md`.
