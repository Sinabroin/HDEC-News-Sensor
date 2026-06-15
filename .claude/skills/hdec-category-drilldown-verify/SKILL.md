---
name: hdec-category-drilldown-verify
description: Use when changing or verifying the category drilldown / evidence explorer layer (the category_sections + review_excluded_evidence + source_filtered_evidence builders in app/briefing.py, the "카테고리별 근거 기사" and "참고/제외 · 출처 품질 감사" sections in scripts/build_static_report.py, the category chips / audit area / status-board legend in templates/index.html, the source_filtered surfacing in app/live_collector.py + app/collector.py, the category-evidence pointer line in scripts/build_telegram_digest.py, scripts/verify_category_drilldown.py). Runs the P0-C1.7 + P0-C1.8 regression checks without network or secrets (live build is SKIP-friendly).
---

# HDEC Category Drilldown & Evidence Explorer Verify (P0-C1.7 + P0-C1.8)

## When to use

- After editing the `category_sections` builder in `app/briefing.py`
  (`_build_category_sections`, `_category_article_entry`, `CATEGORY_DRILLDOWN_NOTE`,
  `TOP_CATEGORY_ARTICLES`).
- After changing the static report drilldown in `scripts/build_static_report.py`
  (`_render_category_drilldown`, `_render_category_article`, the `cat-drill` CSS,
  the `category_drilldown` section wiring).
- After touching the dashboard category section in `templates/index.html`
  (`renderCategorySection` / `renderCategoryChips` / `renderCategoryArticles` /
  `selectCategory`, `#cat-section`, the audit area `renderAuditArea` / `#audit-area`,
  the `#brief-legend` status-board legend).
- After editing the Telegram category-evidence pointer line in
  `scripts/build_telegram_digest.py`.
- **P0-C1.8** — after changing: drilldown default-open behavior; the
  `review_excluded_evidence` / `source_filtered_evidence` builders + `STATUS_BOARD_LEGEND`
  in `app/briefing.py`; the `source_filtered` capture in `app/live_collector.py`
  (`fetch_all(filtered_out=...)`, `_parse_items(..., filtered_sink=...)`) and
  `app/collector.py` (`_run_live`); the audit sections in `scripts/build_static_report.py`
  (`_render_audit_sections` / `_render_audit_article`); the `GRADE_DAILY` label
  (`app/scoring.py`) or any user-facing "검토 필요" rename touchpoint.
- Before committing anything in the category drilldown / review-evidence domain.

## Intent

Turn the report from a Top-3 summary into an **auditable executive evidence brief**:
the 30+ collected articles can be reviewed **by category**, each with source,
importance score, quality label, and original link.

- **`category_sections` makes the collected count auditable.** It groups every scored
  article by category; `sum(section.total_count)` equals `sum(category_counts.count)`.
  So "AI 데이터센터·전력 인프라 13건" can be opened to see the 13 articles behind it.
- **Category lists are evidence lists, not full-text archives.** Each item carries only
  title / source / source_quality / published_at / importance (/5.0) / grade / url /
  why_it_matters. **No article body is stored** — `snippet`/body fields never appear in
  `category_sections` (rules.md §3).
- **Excluded (blog/cafe/community) sources are dropped from the evidence list but kept in
  the count.** `top_articles` filters `source_quality == "excluded"`; the difference shows
  as "외 n건". This preserves the P0-C1.6 Top-3 guard.

## P0-C1.8 — default-closed drilldown + review/excluded evidence + label clarity

- **Drilldown is closed by default.** No `<details class="cat-drill">` carries `open`; the
  static report shows a "카테고리를 펼쳐 근거 기사를 확인하세요." helper. The dashboard does
  **not** auto-select a category (`state.activeCat` stays null → "카테고리를 선택하면 근거
  기사가 표시됩니다."); chips drive selection.
- **The "참고/제외" bucket is now inspectable** via two separated, collapsed audit sections:
  - `review_excluded_evidence` = **valid-news articles graded out for low relevance/priority**
    (alert_grade `제외` AND source_quality ≠ `excluded`). Items carry title/source/category/
    importance(/5.0)/reason/url.
  - `source_filtered_evidence` = **non-news / low-trust sources** (blog/cafe/community),
    each labeled **"비뉴스성/낮은 신뢰 출처"**. Because live drops these at collection, they
    are surfaced from collector provenance (`source_filtered`: title/source/url — **no body**)
    for audit transparency only. They must appear **only inside the audit section**, never in
    Top 3 / drilldown. The live-sim verifier asserts `테크블로그`/`부동산 카페` appear in the
    audit section but **not** outside it.
- **Two criteria are never mixed:** low relevance (review_excluded) ≠ source-quality problem
  (source_filtered).
- **Label clarity:** the old "일간 요약" grade label is renamed to **"검토 필요"** everywhere
  user-facing (status board, dashboard badges, Telegram 현황판). `"일간 요약"` must be absent
  from the rendered report / dashboard template / digest; `"검토 필요"` must be present. A
  `status_board_legend` caption explains each bucket on the report and dashboard.

## Domain boundaries

- `app/briefing.py` stays **derivation-only**: it reads stored score/insight/grade and
  aggregates them — no score/grade recompute, no DB writes (no `upsert_`/`insert_`/
  `executescript`/`DELETE`/`UPDATE`), no network. Category keys come from the stored
  implication reverse-map (same as the rest of briefing).
- Static report drilldown uses **native `<details>`/`<summary>` only** — zero external
  JS/CDN. Article links are the only external hrefs and must carry
  `target="_blank" rel="noopener noreferrer"`. No `<script>/<img>/<iframe>/<link>/<object>/<embed>`.
- Dashboard reuses the existing `/api/brief` payload (no API change needed) and the
  existing `selectArticle` detail path / `srcQualityChip`. `index.html` must not contain
  `telegram`/`webhook`/`confidence `/`정적 스냅샷`/`mock_static`.
- Telegram stays concise: **one line** pointing to the report's category evidence; no
  per-category drilldown in the digest text.

## What the verifier checks

`python3 scripts/verify_category_drilldown.py` (RESULT: PASS / exit 0):

- brief JSON has non-empty `category_sections`; section count and per-category
  `total_count` match `category_counts`; sums match (auditable).
- evidence items have title/source/score/url/quality/why; no `excluded`-quality source
  leaks into `top_articles`; no body fields in `category_sections`.
- static report (mock) has "카테고리별 근거 기사" + `details/summary` + `cat-drill` rows +
  중요도 /5.0, safe anchors, no `<script>`/external resource tags, no body terms.
- static report (NEWS_MODE=live) has the drilldown with real hrefs and **no
  example.com/mock** links — SKIP-friendly when offline (no fake live claim).
- dashboard consumes `category_sections`, has `selectCategory`/`cat-section` markers,
  keeps `selectArticle` + `srcQualityChip` + the Top-3 grade gate.
- digest mentions category evidence exactly once and delegates detail to the report (no
  HTML markup in the plain-text digest).
- **P0-C1.8:** no `<details ... open>` in the report (all closed) + "카테고리를 펼쳐" helper;
  "참고/제외 기사" + "출처 품질 제외" audit sections present; "비뉴스성/낮은 신뢰 출처"
  label confined to the audit section; brief `review_excluded_evidence` items have
  title/source/score/url and are never source-quality `excluded`; `source_filtered_evidence`
  items carry the audit label; "일간 요약" absent and "검토 필요" present across report /
  dashboard / digest; dashboard has no auto-select (`state.activeCat = sections[0]` gone),
  the no-selection prompt, `renderAuditArea` + `#audit-area`, and the `status_board_legend`.
- workflow publish path intact (NEWS_MODE: live → docs/daily/latest.html, live_ok gate,
  send_telegram.py).
- runs `verify_source_quality_filter.py` + `verify_static_report.py` as a regression gate
  (transitively covers the Top-3 source-quality guard and the telegram/brief/quality chain).
- repo `radar.db` is never touched (all pipelines run in a temp DB subprocess).

## Notes

- Per-category display cap is `TOP_CATEGORY_ARTICLES` (6); the rest is counted as "외 n건".
- Category filter on the dashboard is single-select chips; multi-filter/search is out of scope.
- Market indicators remain **시장지표 미연동** (next sprint: P0-C2 Real Macro Snapshot).
