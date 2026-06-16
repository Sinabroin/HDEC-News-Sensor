---
name: hdec-executive-ia-polish-verify
description: Use when changing or verifying the Executive IA / AI·Risk radar layer (app/radar.py classifier; the ai_radar_signals/risk_regulation_signals/business_signals/macro_economy_signals builders + radar fields in app/briefing.py; the AI-first IA, top-nav, collapsed 거시경제/전체 근거 details, score accordion, and terminology in scripts/build_static_report.py; the AI-first reorder in scripts/build_telegram_digest.py; the radar tabs in templates/index.html; scripts/verify_executive_ia_polish.py). Runs the P0-C1.9 regression checks without network or secrets (live build is optional).
---

# HDEC Executive IA Simplification + AI/Risk Radar Focus (P0-C1.9) 검수

임원용 리포트를 **AI-first, 노이즈 최소** IA로 재구성한 레이어를 바꿨거나 검증할 때 사용한다.
모든 검사는 결정적이다 (네트워크/비밀값 없음, repo의 `radar.db`를 건드리지 않고 임시 DB 격리).

## 무엇을 보장하는가

- **레이더 분류**(`app/radar.py`): 기사를 `ai / risk_regulation / business_overseas /
  macro_economy / other` 중 하나로 분류. AI 인프라는 전력/SMR/거시를 언급해도 `ai`,
  순수 거시는 `macro_economy`(기본 Top에서 빠짐). 파생 전용 — DB 쓰기/네트워크/점수 재계산 없음.
- **리스크 surface**: 중대재해·규제 기사가 종합 중요도(가중합 희석)로 낮아도
  `risk_priority_score`(심각도 floor)로 리스크 레이더 상단에 노출 — 버려지지 않는다.
- **정적 리포트 IA**: 상단 목차 → AI 레이더(주력) → 리스크·규제 → 수주·해외 →
  거시경제(접힘 `<details ...macro-section>`, `open` 없음) → 전체 근거(접힘). 열린
  `<details open>` 0건. 점수 구성요소는 '중요도' summary 아코디언 뒤에 접힘.
- **honesty 유지**: 거시 섹션의 `<section aria-label="Macro Snapshot">`를 접힘 details
  **안에 중첩**해 `verify_data_source_honesty`의 macro 위치 정규식이 그대로 통과한다.
  헤더에서 `공개 RSS 수집·시장지표 미연동` 노이즈를 빼되 footer/거시 섹션에 정직하게 남긴다.
- **용어**: `상대 강도→테마 비중`, `유사 주제 기사→관련 기사`, `권장 워치 액션/관찰/기회` 제거.
- **Telegram**: AI 레이더가 거시경제/시장지표보다 먼저. 노이즈 용어 없음.
- **대시보드**: 레이더 탭(AI/리스크·규제/수주·해외/거시경제/전체), 기본 선택 AI.

## 검수 절차

```bash
cd /mnt/d/HDEC-Projects/AI-DesignLab-Sensor
python3 -m py_compile scripts/*.py app/*.py

# P0-C1.9 전용 회귀 (RESULT: PASS / exit 0 이 통과 조건)
python3 scripts/verify_executive_ia_polish.py

# 인접 레이어 회귀 (전부 통과해야 한다 — IA 변경이 기존 계약을 깨지 않았는지)
python3 scripts/verify_static_report.py
python3 scripts/verify_data_source_honesty.py
python3 scripts/verify_score_explanation_ui.py
python3 scripts/verify_category_drilldown.py
python3 scripts/verify_executive_brief.py
python3 scripts/verify_executive_brief_quality.py
python3 scripts/verify_telegram_digest.py
python3 scripts/verify_source_quality_filter.py
python3 scripts/verify_live_publish_path.py
```

## 자주 밟는 함정 (lessons)

- **committed 스냅샷 동기화**: 리포트 빌더의 구조 마커(CORE_HTML_MARKERS)를 바꾸면
  `docs/daily/latest.html`(committed)도 새 빌더로 재생성해야 `verify_static_report`가
  통과한다 — `NEWS_MODE=live python3 scripts/build_static_report.py --output docs/daily/latest.html`.
- **macro details 중첩**: 거시경제를 `<details>`로 바꿀 때 기존 `<section aria-label="Macro
  Snapshot">`를 그 **안에 중첩**해야 `verify_data_source_honesty`(업데이트 비대상)의 정규식이
  깨지지 않는다. macro details에 `open`을 절대 달지 않는다.
- **honesty 잠금 문자열 유지**: Telegram은 `주요 테마`/`카테고리`/`시장지표 미연동`,
  정적 리포트는 live일 때 `공개 RSS 수집`을 반드시 포함해야 한다(업데이트 비대상 verifier가 검사).
  메인에서 빼더라도 footer/거시 섹션/현황판에 남긴다.
- **CSS 주석 한글 누수**: 본문 순서 검사가 깨지지 않도록 `<head>`/CSS 주석에 `거시경제`/
  `AI 레이더` 같은 섹션명을 쓰지 않는다 (앵커 id 기반 순서 검사를 우선).
- **단위 불명 '강도' 금지**: `상대 강도`를 지운 뒤 `강도` 단독 표기가 남지 않게 한다
  (`verify_score_explanation_ui`가 `강도` 부재를 검사).
