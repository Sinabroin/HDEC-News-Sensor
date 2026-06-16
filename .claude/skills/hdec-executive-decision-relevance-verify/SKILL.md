---
name: hdec-executive-decision-relevance-verify
description: Use when changing or verifying the P0-C1.12 executive decision relevance reframe (app/decision_relevance.py executive-section memberships + tiers, the hdec_direct_signals / broadened business_signals / competitor_supply_signals in app/briefing.py, the is_hdec_strategic + is_order_environment grade floors in app/scoring.py, the 현대건설 직접/수주·해외·발주 환경/경쟁사·공급망 sections + HDEC-first order in scripts/build_static_report.py, the radar tabs + dynamic default in templates/index.html, the [현대건설 직접] line + company dedup + Macro Snapshot removal in scripts/build_telegram_digest.py, scripts/verify_executive_decision_relevance.py, scripts/audit_live_article_quality.py). Runs the P0-C1.12 regression checks without network or secrets.
---

# HDEC Executive Decision Relevance Verify (P0-C1.12)

## When to use

- After editing the new leaf `app/decision_relevance.py` (`classify` / `is_hdec_strategic`
  / `is_order_environment` / executive-section constants / tier bands / keyword lists).
- After changing how briefing builds `hdec_direct_signals` / `business_signals` (broadened)
  / `competitor_supply_signals`, or attaches `decision_relevance_*` / `executive_section`
  / `secondary_sections` to entries.
- After touching the `is_hdec_strategic` / `is_order_environment` grade floors in
  `app/scoring.py`.
- After changing the report IA (현대건설 직접 → AI → 수주·해외 → 리스크·규제 →
  경쟁사·공급망 → 거시 → 근거), the dashboard radar tabs / dynamic default, or the
  Telegram digest (`[현대건설 직접]` line, company dedup, Macro Snapshot removal).
- Before committing anything in the executive-decision-relevance domain.

## Intent (what the layer guarantees)

- **Reframe, not rewrite.** Product goal moved from 'collect AI news' to 'Hyundai E&C
  executive decision radar'. `decision_relevance` sits **on top of** `radar` +
  `article_quality` — it does not recompute `final_score`/`alert_grade` and does not change
  `radar.classify_section`. AI section stays `radar_section==ai` (IA regression guard).
- **Raw inputs only.** `classify` reads raw title/source/snippet/topics. It must NOT read
  generated reason/category-label text (those contain '현대건설' and cause self-fulfilling
  false positives — the mission called this out explicitly).
- **현대건설 직접 영향** is a new top-level section shown **before** AI. Members = 현대건설
  family subject + a strategy/business/org/tech/risk signal. Ordering: 리스크/제재 →
  수주·DC·SMR·뉴에너지 → AI·계약 → R&D·조직 → 기타. **Do not blindly promote every Hyundai
  article** — `is_hdec_strategic` filters healthcare/lifestyle (헬스케어 협약 stays low).
- **Multi-section.** A 현대건설 데이터센터 article is in hdec_direct (primary) + ai + order
  (secondary); a 현대건설 벌점 is primary 리스크·규제 + secondary 현대건설 직접 (both lists).
- **수주·해외 broadening.** Order/overseas includes the **발주 환경** (중동·재건·사우디·네옴·
  EPC·플랜트·LNG·원전·SMR·데이터센터·글로벌 수주) and competitor order strategy, not just
  signed contracts. Business-section sort puts non-HDEC-primary order signals first so
  Middle-East/competitor signals aren't buried under HDEC items (already in HDEC section).
- **Grade floors (only raise, to WEEKLY).** `is_hdec_strategic` and credible-source
  `is_order_environment` floor to 추적 필요 so relevant items aren't grade-excluded.
  `is_order_environment` **requires a sector/geo signal** (EPC/DC/SMR/플랜트/중동/재건) — a
  generic 착공 statistic (연합뉴스 '주택 착공 감소') is NOT floored. stock-hype still wins
  → 제외 (§19).
- **Telegram.** `[현대건설 직접]` line first; company-level dedup (no two 가온전선 in Top);
  **no Macro Snapshot / '시장지표 미연동' placeholder** (delegated to report; live values
  only). Fake macro values are still banned (§13).

## Domain boundaries

- `app/decision_relevance.py` is a **pure leaf** (no DB/network; imports article_quality /
  radar / source_quality / config). briefing/scoring **call** it (single source for
  executive sections, like `radar.classify_section`). Keyword policy lives in the module.
- briefing/report build section lists from membership (`decision_relevance.in_section`);
  they never reclassify. The AI list stays `radar_section==ai`; macro stays
  `radar_section==macro_economy` (existing IA verifier).
- The floors **only raise** grade via `_max_grade` (never below the computed grade) and are
  no-ops on mock → mock numbers stay **28 / 21 / 3 / 4 / 14 / 7**.
- Forbidden body-field names (`raw_payload`/`full_text`/`article_body`/`full_rss_content`)
  and X/Twitter tokens must not appear anywhere.

## Commands

```bash
# the focused P0-C1.12 regression (fixture-based, no network, no secrets)
python3 scripts/verify_executive_decision_relevance.py

# full regression context (keep green)
python3 scripts/verify_live_article_quality_gate.py
python3 scripts/verify_executive_ia_polish.py
python3 scripts/verify_telegram_digest.py
python3 scripts/verify_data_source_honesty.py
python3 scripts/verify_static_report.py

# real public RSS with the layer applied (network — manual, read-only, not a verifier)
NEWS_MODE=live python3 scripts/audit_live_article_quality.py \
  --output /tmp/hdec_decision_relevance_audit.md
NEWS_MODE=live python3 scripts/build_static_report.py --output /tmp/hdec_live_report.html
```

## Watch-outs (lessons)

- **mock counts are the canary** — if a floor or membership leaks into mock, the baseline
  changes. Floors target WEEKLY only and require sector/geo or 현대건설-strategic scope; the
  7 mock excluded (housing-decline + consumer AI + stock-hype + blog) must stay excluded.
- **business sort** — without putting non-HDEC-primary order signals first, the HDEC items
  (also in the HDEC section) crowd out Middle-East/competitor order signals at the top-N cap.
- **competitor over-inclusion** — generic DC terms (냉각/조달) drag plain AI-infra articles
  into 경쟁사·공급망; keep `SUPPLY_TOKENS` distinctive (버스덕트/전선/변압기) + competitor names.
- **digest macro removal** broke `verify_telegram_digest` / `verify_data_source_honesty`
  (they asserted `[Macro Snapshot]` present). Updated to the honest new contract: absent
  unless live; fake-value bans preserved. Don't re-introduce the placeholder.
- **never feed generated text to the classifier** — the audit helper must classify from
  title/source/snippet, not from `category_label`/`reason` strings that contain '현대건설'.
- The audit helper is **not** a verifier — live Google News RSS varies per fetch and still
  needs periodic query/source tuning.
