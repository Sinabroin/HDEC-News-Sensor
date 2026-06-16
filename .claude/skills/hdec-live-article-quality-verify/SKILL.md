---
name: hdec-live-article-quality-verify
description: Use when changing or verifying the P0-C1.11 live article quality gate (data/article_quality_rules.json, app/article_quality.py stock-hype + HDEC-direct detection, the stock-hype cap + HDEC grade floor in app/scoring.py, the risk-classifier tightening + HDEC routing in app/radar.py, source_quality.normalize_display_source + aggregator_display in data/source_quality_rules.json, the display_source wiring in app/briefing.py + app/main.py + scripts/build_static_report.py + templates/index.html, scripts/verify_live_article_quality_gate.py, scripts/audit_live_article_quality.py). Runs the P0-C1.11 regression checks without network or secrets.
---

# HDEC Live Article Quality Gate Verify (P0-C1.11)

## When to use

- After editing the quality policy `data/article_quality_rules.json` (stock-hype
  strong/weak/equity patterns, HDEC contract/enforcement patterns, score cap).
- After changing the classifier `app/article_quality.py`
  (`assess` / `is_hdec_direct` / `STOCKHYPE_SCORE_CAP`).
- After touching where the gate is applied: the stock-hype cap + `_max_grade` floor in
  `app/scoring.py`, the `RISK_ACTION_STRONG` / `RISK_REG_WEAK` taxonomy + stock-hype/HDEC
  routing + `_SEVERITY_FLOOR` in `app/radar.py`.
- After changing source display normalization: `source_quality.normalize_display_source`,
  `aggregator_display` in `data/source_quality_rules.json`, or the `display_source`
  consumers (`app/briefing.py`, `app/main.py`, `scripts/build_static_report.py`,
  `templates/index.html`).
- Before committing anything in the article-quality / source-display domain.

## Quality gate rules (intent)

- **stock-hype / equity-research demotion** — `assess(source, title)` looks at **title +
  source only** (not the 500-char snippet, so 수혜/급등 don't accumulate and over-demote a
  legit article). `strong` indicators (머니무브·테마주·목표가·파운드리 거물·증권가…) fire
  alone; `weak` indicators (수혜·급등·성장 기대…) need **≥2**. Equity-research sources
  (리서치알음…) fire on source alone. A title that directly names 현대건설 is **exempt**
  (the HDEC protection path handles it). Effect: `scoring` caps to **2.4 + 제외 grade**,
  `radar` routes to **other** → out of every executive section; appears only in the
  참고/제외 audit. **bare `주가` is banned** (it substring-matches 발주가/수주가) — use
  `주가 급등`/`목표주가`; the verifier regression-checks this.
- **risk classifier tightening** — risk requires a real risk-action keyword
  (`RISK_ACTION_STRONG`: 중대재해·벌점·영업정지·과징금·특별감독·사전통보…), or a weak
  regulation keyword (`RISK_REG_WEAK`) **with an industry anchor**. `국토부/고용노동부/
  정부/안전/점검` alone is **not** risk anymore (innovation/policy ≠ risk).
- **HDEC direct protection** — 현대건설 + AI + 계약/하도급/협력사/상생펀드/불공정 →
  `hdec_ai_contract` → radar **ai**, scoring floor **≥ 추적 필요**. 현대건설 + 벌점/제재/
  영업정지/입찰제한 → `hdec_enforcement` → radar **risk_regulation** (+ risk_priority),
  floor ≥ 추적 필요 (검토 필요 if severe). Generic 현대건설 articles are **not** boosted.
- **aggregator source display** — `normalize_display_source` maps `v.daum.net`/
  `n.news.naver.com`/`news.google.com`… → `Daum 경유`/`Naver 경유`/`Google News 경유`
  (other host-shaped sources → `원문 경유`); real publisher names pass through. The raw
  source + URL(href) are preserved; only the visible label changes.

## Domain boundaries

- One owner: `app/article_quality.py` is a **pure leaf** (no DB/network, imports only
  `config`); scoring/radar **call** it (single source of truth, like `scoring.GRADE_*`).
  Keyword policy lives in `data/article_quality_rules.json` only.
- The stock-hype gate **only lowers** (cap 2.4 + 제외); the HDEC floor **only raises**
  grade via `_max_grade` (never below the computed grade). Both are no-ops on mock data,
  so mock numbers stay **28 / 21 / 3 / 4 / 14 / 7**.
- `normalize_display_source` lives in `source_quality` (source domain); briefing/main/
  report attach `display_source` and render `display_source || source`. Do **not** mutate
  the raw `source` field — keep it honest for audit.
- Forbidden body-field names (`raw_payload`/`full_text`/`article_body`/`full_rss_content`)
  and X/Twitter tokens must not appear anywhere.

## Commands

```bash
# the focused P0-C1.11 regression (fixture-based, no network, no secrets)
python3 scripts/verify_live_article_quality_gate.py

# full regression context (keep green)
python3 scripts/verify_source_quality_filter.py
python3 scripts/verify_executive_ia_polish.py
python3 scripts/verify_live_news_ingestion.py
python3 scripts/verify_executive_brief.py
python3 scripts/verify_static_report.py

# real public RSS with the gates applied (needs network — manual, read-only, not a verifier)
NEWS_MODE=live python3 scripts/audit_live_article_quality.py \
  --output /tmp/hdec_article_quality_audit.md
NEWS_MODE=live python3 scripts/build_static_report.py --output /tmp/hdec_live_report.html
```

## Watch-outs (lessons)

- **bare `주가`** substring-matches 발주가/수주가 → never a pattern; use `주가 급등`/`목표주가`.
- **snippet in stock-hype** inflates false positives (long body accumulates weak hits) →
  title+source only.
- **forcing 제외 grade** (not just the 2.4 cap) is required: the strategic gate could
  otherwise promote a capped stock-hype article to 추적 필요 and back into a radar.
- **HDEC floor must be scoped** to AI-contract / enforcement — a blanket 현대건설 boost
  distorts AI/risk sections (mission: do not blindly boost every Hyundai article).
- **mock counts are the canary** — if `verify_live_article_quality_gate.py` reports the
  mock baseline changed, a gate leaked into mock; fix the keyword scope, don't update the
  baseline unless the change is intentional and explained.
- The audit helper is **not** a verifier — live Google News RSS varies per fetch and still
  needs periodic query/source tuning; it reduces known false positives, not all of them.
