---
name: hdec-live-news-mvp-verify
description: Use when changing or verifying the live news ingestion + score-explanation UX layer (app/live_collector.py, the NEWS_MODE branch of app/collector.py, data/live_news_sources.json, score meters / 상대 강도 / 유사 주제 기사 wording in build_static_report.py and templates/index.html, scripts/verify_live_news_ingestion.py, scripts/verify_score_explanation_ui.py). Runs the P0-C1 regression checks; live fetch is SKIP-friendly when offline.
---

# HDEC Live News MVP + Score Explanation Verify (P0-C1)

## When to use

- After editing `app/live_collector.py`, the `NEWS_MODE` branch of `app/collector.py`,
  `data/live_news_sources.json`, `app/config.py` (NEWS_MODE), or the news-provenance
  fields in `app/briefing.py` (`news_data_mode` / `news_source` / `news_fallback_used`).
- After changing score visualization / term wording in `scripts/build_static_report.py`
  or `templates/index.html` (중요도 X.X/5.0 미터, 점수 구성요소, 판정 신뢰도, 상대 강도,
  유사 주제 기사).
- Before committing anything in the live-news or score-explanation domain.

## Domain boundaries

- **Network IO is owned only by `app/live_collector.py`.** `collector.py` imports it
  **lazily inside `_run_live()`**, so the mock path makes zero network imports.
  `mock` mode (default) is fully offline and secret-free (rules.md §2 intent preserved).
- **No secrets, ever.** Public RSS (Google News RSS) needs no auth. `live_collector`
  must not read `os.environ` for secrets, must contain no token-shaped strings.
- **No article bodies.** Only `title / snippet(≤500) / source / url / published_at /
  source_metadata` are stored. The names `raw_payload`/`full_text`/`article_body`/
  `full_rss_content` are forbidden anywhere (assemble forbidden host tokens from
  fragments so the code-tree grep stays clean — see lesson banned-term-literal-in-defensive-code).
- **No fake live.** If live fetch returns 0 / raises → fallback to mock with
  `news_fallback_used=true`; never claim `news_data_mode=live` on mock data.
- **X(엑스) is excluded** at collection (x.com / api.x). Day-1 X ban still holds.
- Briefing/scoring/insight are **not** edited to make UI checks pass — score meters,
  bands, components, and term captions are presentation derived from stored values.

## Commands

```bash
# mock (offline, deterministic)
NEWS_MODE=mock python3 scripts/build_executive_brief.py --json
NEWS_MODE=mock python3 scripts/build_static_report.py --output docs/daily/latest.html

# live (needs network; no secrets) — real public RSS
NEWS_MODE=live python3 scripts/build_executive_brief.py --dry-run
NEWS_MODE=live python3 scripts/build_static_report.py --output docs/daily/latest.html
```

## Verification

```bash
python3 scripts/verify_live_news_ingestion.py     # ingestion contract (SKIP if offline)
python3 scripts/verify_score_explanation_ui.py     # /5 meter, 상대 강도, 유사 주제, 판정 신뢰도
python3 scripts/verify_data_source_honesty.py      # provenance + no fake macro + no dev wording
python3 scripts/verify_static_report.py            # URL policy: article href OK, other external blocked
```

## Pass criteria

- `verify_live_news_ingestion.py` → `RESULT: PASS`. Live fetch either reports
  `[LIVE] ...건` with valid http(s) URLs OR `[SKIP] ...` when offline — never fakes success.
- `verify_score_explanation_ui.py` → `RESULT: PASS`. Report shows `중요도 X.X / 5.0`
  + `class="meter"` + `class="comps"`, `상대 강도` (no bare `강도`), `유사 주제 기사`,
  `판정 신뢰도`. Brief entries carry `score_band` + `score_components`; themes carry
  `relative_strength`.
- Static report URL policy: external `http(s)` allowed **only** in article `href`
  (with `target="_blank" rel="noopener noreferrer"`); no external script/css/img/iframe/CDN.
- No user-facing `정적 스냅샷` / `mock_static`; market section says `시장지표 미연동`
  with zero fake numbers.
- repo `radar.db` unchanged after the suite (temp-DB isolation).

## On failure

1. Fix only inside the live-news / score-presentation domain.
2. Keep the committed `docs/daily/latest.html` as the **mock** snapshot
   (`NEWS_MODE=mock ... --output docs/daily/latest.html`) — verifiers assume mock there.
3. Re-run the verifiers until all print `RESULT: PASS`.
4. Record any real new pitfall under `.claude/skills/lessons/<slug>/SKILL.md`.
