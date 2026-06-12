---
name: hdec-telegram-digest-verify
description: Use when changing or verifying the Telegram mock daily digest domain (scripts/build_telegram_digest.py, scripts/send_telegram.py, scripts/verify_telegram_digest.py, .github/workflows/telegram-notify.yml). Runs the P0-B1 regression checks without network or secrets.
---

# HDEC Telegram Digest Verify (P0-B1)

## When to use

- After any edit to `scripts/build_telegram_digest.py`, `scripts/send_telegram.py`,
  `scripts/verify_telegram_digest.py`, or `.github/workflows/telegram-notify.yml`.
- Before committing anything in the Telegram digest / notification automation domain.

## Domain boundaries

This domain owns ONLY:
- digest building (`scripts/build_telegram_digest.py`)
- Telegram sending (`scripts/send_telegram.py`)
- the GitHub Actions workflow (`.github/workflows/telegram-notify.yml`)
- its verifier (`scripts/verify_telegram_digest.py`)

It must NOT modify `app/*` domains, `templates/`, `data/`, or the DB schema.
It reuses `app.collector` / `app.scoring` / `app.insight` read-only via a
throwaway temp-dir SQLite DB — the repo `radar.db` is never touched.

## Exact commands

```bash
python3 scripts/build_telegram_digest.py --dry-run   # message + summary, no send
python3 scripts/build_telegram_digest.py --json      # machine-checkable output
python3 scripts/verify_telegram_digest.py            # full regression suite
```

All three run offline: no network, no `TELEGRAM_*` secrets, no `.env` needed.

## Banned checks (enforced by the verifier)

- Forbidden field/term names anywhere in the digest output or code tree
  (`app/ data/ scripts/ templates/ .github/`): raw_payload, full_text,
  article_body, full_rss_content, api.x.com, twitter, "x bearer token".
- No hardcoded bot token shape (`digits:secret`) or chat IDs in any script
  or workflow — secrets must come only from `${{ secrets.* }}`.
- `send_telegram.py` must never print token, chat id, or the request url.
- Workflow `MESSAGE` fallback and `workflow_dispatch` input default must be
  empty strings, otherwise the digest path becomes unreachable.
- Sender cap `MAX_MESSAGE_LEN` must be >= builder `MESSAGE_BUDGET`.

## Expected pass criteria

- `verify_telegram_digest.py` prints `RESULT: PASS` and exits 0.
- Dry-run digest: contains "HDEC Executive Radar", a mock-mode marker,
  3 top signals (1 minimum), and stays <= 3000 chars.
- `--json`: `mode == "mock"`, `top_signals` length 3 (expected with the
  30-article mock set: collected 30 / inserted 28 / alert candidates 3).

## On failure

1. Fix only inside this domain's files; do not patch `app/*` to make checks pass.
2. Re-run `python3 scripts/verify_telegram_digest.py` until `RESULT: PASS`.
3. If the failure revealed a new pitfall, record it under
   `.claude/skills/lessons/<slug>/SKILL.md` (real issues only).
4. Never commit with a failing verifier — the workflow runs it before sending.
