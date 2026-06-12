---
name: hdec-executive-brief-verify
description: Use when changing or verifying the Executive Brief layer (app/briefing.py, scripts/build_executive_brief.py, scripts/build_telegram_digest.py, the brief section of templates/index.html, /api/brief route, data/mock_macro_snapshot.json). Runs the P0-B2 regression checks without network or secrets.
---

# HDEC Executive Brief Verify (P0-B2)

## When to use

- After any edit to `app/briefing.py`, `scripts/build_executive_brief.py`,
  `scripts/build_telegram_digest.py`, `scripts/verify_executive_brief.py`,
  the EXECUTIVE BRIEF section of `templates/index.html`, the `/api/brief`
  route in `app/main.py`, or `data/mock_macro_snapshot.json`.
- Before committing anything in the briefing / digest presentation domain.

## Domain boundaries

The Briefing domain derives only:
- It READS stored scores/insights via `db.py` helpers — never writes DB,
  never recalculates scores/grades, never regenerates insight text
  (categories come from reverse-mapping stored `hdec_implication` against
  `insight.IMPLICATION_TEMPLATES`).
- CLI paths run the pipeline on a throwaway temp-dir SQLite DB; the repo
  `radar.db` is never touched (the verifier asserts this).
- It must NOT add network calls, pip installs, schedulers, or new external
  sources, and must NOT grow into a news-portal layout — the product is a
  focused executive signal radar, not broad article aggregation.

## Exact commands

```bash
python3 scripts/build_executive_brief.py --dry-run   # human-readable brief
python3 scripts/build_executive_brief.py --json      # machine-checkable brief
python3 scripts/build_telegram_digest.py --dry-run   # telegram text, no send
python3 scripts/verify_executive_brief.py            # full P0-B2 suite
python3 scripts/verify_telegram_digest.py            # P0-B1 suite must stay green
```

All run offline: no network, no `TELEGRAM_*` secrets, no `.env` needed.

## Benchmark-inspired checks (enforced by the verifier)

- `executive_one_liner`: non-empty Korean, 20–240 chars, 1–2 sentences,
  NOT a concatenation of article titles (synthesized risk+opportunity).
- `top_immediate_signals` 1–3 / `top_new_issues` 1–5 / `theme_rankings` 1–5
  / `category_counts` >= 1, each with required fields.
- Every signal carries spread indicators (`related_count`, `source_count`,
  `label`) — labeled as topic-overlap estimates, never as confirmed
  multi-outlet coverage.
- Telegram digest contains: "HDEC Executive Radar", "오늘의 Executive Signal",
  "즉시 알림 후보", "주요 테마", a mock marker — and stays <= 3000 chars.
- Macro snapshot (if present) must be explicitly marked mock/static.

## Banned checks

- Forbidden field/term names in the code tree (`app/ data/ scripts/
  templates/ .github/`): the rules.md §3 body-storage field names and all
  X/Twitter API terms (kept as runtime-assembled fragments in the verifier).
- No hardcoded bot-token shape or chat IDs anywhere; workflow secrets only.
- `sqlite3` import stays exclusive to `app/db.py`.
- Length contract: digest budget (3000) <= sender cap (3500) < Telegram 4096.
- Frontend template contains no telegram/webhook strings (rules.md §4).

## Expected pass criteria

- `verify_executive_brief.py` prints `RESULT: PASS` and exits 0
  (this transitively runs `verify_telegram_digest.py` and the sender-path
  checks: env-message, mock-digest, fail-fast without secrets).
- Expected mock numbers: 감지 28 / 즉시 3 / 일간 5 / 주간 13 / 제외 7,
  digest ~1.2k chars.
- repo `radar.db` unchanged after the whole suite (temp-DB isolation).

## On failure

1. Fix only inside the briefing/digest domain files; do not patch scoring,
   insight, collector, or db to make checks pass.
2. Re-run `python3 scripts/verify_executive_brief.py` until `RESULT: PASS`.
3. If the failure revealed a new pitfall, record it under
   `.claude/skills/lessons/<slug>/SKILL.md` (real issues only).
4. Never commit with a failing verifier — the Telegram workflow runs both
   verifiers before sending and will block delivery.
