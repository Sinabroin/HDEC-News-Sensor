---
name: hdec-static-report-verify
description: Use when changing or verifying the static report page / Telegram link card layer (scripts/build_static_report.py, docs/daily/latest.html, the REPORT_URL handling in scripts/send_telegram.py, the report steps of .github/workflows/telegram-notify.yml). Runs the P0-B5 regression checks without network or secrets.
---

# HDEC Static Report Verify (P0-B5)

## When to use

- After any edit to `scripts/build_static_report.py`, `scripts/verify_static_report.py`,
  the REPORT_URL/button handling in `scripts/send_telegram.py`,
  the report/verify steps of `.github/workflows/telegram-notify.yml`,
  or anything under `docs/daily/`.
- Before committing anything in the static report / Telegram link card domain.

## Domain boundaries

- The report builder only RENDERS the shared brief data
  (`build_brief_via_mock_pipeline()` on a throwaway temp-dir DB) —
  it never recalculates scores, never touches `radar.db`, never reads secrets,
  and makes zero network calls.
- The generated page must stay a **focused executive signal radar snapshot** —
  no portal layout, no article bodies, only derived summaries.
- The page is standalone: **no external CDN/script/font/link** — the verifier
  asserts zero `http://`/`https://` occurrences and zero `<script>` in the HTML.
- Publishing is manual: CI only checks generation; the served page is the
  **committed** `docs/daily/latest.html` (GitHub Pages from `main`/`docs`).

## Report generation commands

```bash
python3 scripts/build_static_report.py --dry-run                        # summary only
python3 scripts/build_static_report.py --json                           # machine metadata
python3 scripts/build_static_report.py --output docs/daily/latest.html  # regenerate page
```

All run offline: no network, no `TELEGRAM_*`/`REPORT_URL` secrets, no `.env`.

## Telegram REPORT_URL behavior (send_telegram.py)

- `REPORT_URL` set (repo Variable preferred, Secret accepted) →
  message gets an inline URL button **"오늘 브리프 보기"** via `reply_markup`.
- `REPORT_URL` missing/invalid → text-only digest, **no failure** (fallback preserved).
- Applies to both paths: custom `MESSAGE` and empty-MESSAGE mock digest.
- Logs may contain only `Report link enabled: true/false` — never the URL value,
  never token/chat ids.

## GitHub Pages / public visibility caution

- Pages on the free plan is **public**. Only mock/demo data may be published.
- Never publish real internal news or sensitive signal data to public Pages —
  use private/internal hosting for real data.
- Enabling Pages is a documented manual step (`docs/daily/README.md`), never automated.

## Verification

```bash
python3 scripts/verify_static_report.py   # full P0-B5 suite
```

This transitively runs `verify_telegram_digest.py` and `verify_executive_brief.py`
(and the optional cluster/quality verifiers if present).

## Pass criteria

- `verify_static_report.py` prints `RESULT: PASS` and exits 0.
- Generated and committed HTML both contain: HDEC Executive Radar /
  Executive Daily Brief / 오늘의 Executive Signal / 즉시 알림 후보 / 주요 테마 /
  카테고리 요약 / 권장 워치 액션 / mock note — and zero external URLs.
- Sender paths: env-message, mock-digest, REPORT_URL on/off/invalid,
  fail-fast without secrets (exit 1, no value leaks).
- repo `radar.db` unchanged after the whole suite (temp-DB isolation).

## On failure

1. Fix only inside the report/sender presentation domain — do not patch
   briefing/scoring/insight/collector/db to make checks pass.
2. Re-run `python3 scripts/verify_static_report.py` until `RESULT: PASS`.
3. If markers changed intentionally, regenerate the committed page:
   `python3 scripts/build_static_report.py --output docs/daily/latest.html`.
4. Record any real new pitfall under `.claude/skills/lessons/<slug>/SKILL.md`.
5. Never commit with a failing verifier — the Telegram workflow runs it
   before sending and will block delivery.
