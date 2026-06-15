---
name: hdec-source-quality-verify
description: Use when changing or verifying the source-quality filter layer (data/source_quality_rules.json, app/source_quality.py, the excluded-source drop in app/live_collector.py, the score cap in app/scoring.py, the source_quality labels/Top-3 guard in app/briefing.py, the quality label/note in scripts/build_static_report.py + templates/index.html, the live weak-source line in scripts/build_telegram_digest.py, scripts/verify_source_quality_filter.py). Runs the P0-C1.6 regression checks without network or secrets.
---

# HDEC Source Quality Filter Verify (P0-C1.6)

## When to use

- After editing the source-quality policy `data/source_quality_rules.json`
  (excluded / trusted / institution / low-trust patterns, score caps).
- After changing the classifier `app/source_quality.py` (`classify` / `is_excluded` / `cap_for`).
- After touching where the filter is applied: the excluded-source drop in
  `app/live_collector.py`, the score cap in `app/scoring.py`, the label + Top-3 guard
  in `app/briefing.py`, the report/dashboard labels (`scripts/build_static_report.py`,
  `templates/index.html`), or the live weak-source line in `scripts/build_telegram_digest.py`.
- Before committing anything in the source-quality domain.

## Source quality rules (intent)

- Public RSS mixes news with **Naver Blog/Cafe, Tistory, YouTube, communities
  (디시/클리앙/…), and re-transmission/PR**. Those must not reach the executive Top 3.
- Classification is a pure function of `(source, title)` →
  `source_quality ∈ {trusted, neutral, low, excluded}`,
  `source_type ∈ {news, institution, blog, cafe, community, video, aggregator, unknown}`.
- Precedence: excluded-source → institution → trusted → low-source → title patterns
  (only for neutral sources) → neutral. **Trusted/institution ignore title patterns**
  so real reporting like "네이버 블로그 규제" is not mis-downgraded.
- **Unknown sources are neutral, never auto-banned** (no fake credibility).
- Source quality is a **ranking/filter guardrail, not a truth guarantee.**

## Domain boundaries

- One owner: `app/source_quality.py` holds the logic; every other domain **calls**
  `classify` / `is_excluded` / `cap_for` (single source of truth, like `scoring.GRADE_*`).
- `live_collector` drops `excluded` items at parse time but **must not add quality
  fields to the raw dict** — its rows stay `{id,title,source,published_at,url,snippet,
  source_metadata}` (pinned by `verify_live_news_ingestion.py`).
- `scoring` applies caps that **only lower** a score (`excluded`→1.0, `low`→3.4); it does
  not change tiers/penalties, so mock numbers and trusted-news scores are untouched.
- `briefing` attaches display labels and filters `excluded` from the top lists —
  no re-grading, no DB writes.
- Forbidden body-field names (`raw_payload`/`full_text`/`article_body`/`full_rss_content`)
  must not appear anywhere; in verifiers assemble them from fragments
  (lesson: banned-term-literal-in-defensive-code).

## Commands

```bash
# the focused source-quality regression (no network, no secrets)
python3 scripts/verify_source_quality_filter.py

# full regression context (keep green)
python3 scripts/verify_live_news_ingestion.py
python3 scripts/verify_executive_brief.py
python3 scripts/verify_executive_brief_quality.py
python3 scripts/verify_static_report.py

# real public RSS with the filter applied (needs network)
NEWS_MODE=live python3 scripts/build_static_report.py --output /tmp/hdec_live_quality_report.html
```

## Failure examples (what this catches)

- A blog/cafe/community source reaching `즉시 알림 후보` or appearing in Top 3.
- A neutral or trusted source being auto-banned (over-filtering real news).
- `classify` losing one of the four required keys, or `is_excluded` not dropping
  `네이버 블로그`/`네이버 카페`/`tistory`/`youtube`/`디시`.
- Score cap missing → a high-relevance blog item keeps an instant-grade score.
- Mock drift: digest length ≠ 747 / detected ≠ 28 / immediate ≠ 3 (cap must be a no-op for mock).
- Body-field name leaking into the code tree.

## Pass criteria

- `verify_source_quality_filter.py` → `RESULT: PASS` (classify cases, parse drop,
  end-to-end live sim cap, brief fields, mock intact, dashboard/report labels,
  no body fields, workflow intact, `radar.db` unchanged).
- Live sim: trusted source keeps `≥4.5`/instant; blog & cafe are capped `<4.5` to `제외`
  and absent from Top 3 and the rendered report.
- Mock unchanged: `28 / 3 / 4 / 14 / 7`, digest 747 chars; live weak-source line only in `live`.
- All existing P0-A/B/C verifiers still print `RESULT: PASS`.

## On failure

1. Fix only inside the source-quality domain (policy JSON, classifier, or the call sites).
2. Never edit scoring tiers/penalties or briefing grading to force a UI check.
3. If mock numbers drift, the cap is wrong — caps must only lower, and mock low/excluded
   items are already 0.0/제외 (so the cap must be a no-op there).
4. Re-run the verifiers until all print `RESULT: PASS`.
5. Record any real new pitfall under `.claude/skills/lessons/<slug>/SKILL.md`.
