---
name: hdec-executive-label-query-telegram-verify
description: Use when changing or verifying the P0-C1.10 executive-label/query/Telegram-rollout layer — the AI 관련 section rename, the 추적 필요 grade (scoring.GRADE_WEEKLY + all maps), removal of user-facing LIVE/공개 RSS via the invisible news-data-mode HTML marker (scripts/build_static_report.py _mode_pill + marker; app/briefing.py _data_warning), the noun-phrase memo summaries (app/insight.IMPLICATION_TEMPLATES, app/radar._RISK_REASON, app/briefing._compose_one_liner), the AI-weighted data/live_news_sources.json query set, and the Telegram channel→1:1 personal-bot deep-link button in scripts/send_telegram.py. Runs scripts/verify_telegram_channel_to_personal_entry.py + verify_executive_ia_polish.py + verify_live_news_ingestion.py without network or secrets.
---

# HDEC Executive Label Cleanup + AI Query Rebalance + Telegram Rollout (P0-C1.10) 검수

임원 친화 언어 정리 + AI 편중 수집 + 채널→1:1 봇 진입 레이어를 바꿨거나 검증할 때 사용한다.
모든 검사는 결정적이다 (네트워크/비밀값 없음, repo의 `radar.db`를 건드리지 않음).

## 무엇을 보장하는가

- **AI 라벨**: 섹션 제목/Telegram/대시보드가 `AI 관련`(옛 `AI 레이더 주요 신호`). 내부 변수명
  (`ai_radar_signals` 등)은 그대로.
- **추적 필요**: `scoring.GRADE_WEEKLY = "추적 필요"`. 공개 등급 4단계 = 즉시 확인 · 검토 필요 ·
  추적 필요 · 참고. 현황판·대시보드 배지·Telegram·점수대 라벨이 모두 같은 표현.
- **LIVE/공개 RSS 제거**: 임원 화면(리포트/Telegram)에 `LIVE`·`공개 RSS` 기술 표기 0건.
  live 헤더 배지/footer는 중립 `자동 수집`. live/mock 판별은 본문 상단의 **보이지 않는 주석
  마커** `<!--news-data-mode:live-->` / `<!--news-data-mode:mock-->`로 한다 (검증기/CI용).
- **명사형 요약**: 생성 요약/사유 줄에 금지 종결어미(`입니다/합니다/됩니다/있습니다/신호다/
  필요하다/예상된다/감지됩니다`) 없음. 기사 제목·footer 고지문은 대상 아님.
- **AI 편중 수집**: `data/live_news_sources.json` — `max_per_query=5`, `max_total=70`,
  AI≥12, 리스크/규제≥5, 거시 단독≤2, provider=`google_news_rss`, X 쿼리 0건.
- **Telegram 채널→1:1 봇**: `send_telegram.py`가 `오늘 브리프 보기` + `개인 질의하기`
  (`https://t.me/<bot>?start=ask_today`) 버튼을 붙인다. `TELEGRAM_BOT_USERNAME` 또는
  `TELEGRAM_PERSONAL_BOT_URL` 미설정 시 개인 버튼은 안전하게 생략(발송 실패 안 함).
  정직성: deep link는 1:1 **진입**일 뿐, 자연어 질의 응답은 P1 webhook/polling 후 활성화.
- **비밀 누출 0**: 정상 발송 경로는 enabled true/false만 출력. `--dry-run-payload`는 버튼
  text/url만 출력하고 토큰은 읽지도 출력하지도 않는다.

## 검증 절차

```bash
python3 -m py_compile scripts/*.py app/*.py
python3 scripts/verify_telegram_channel_to_personal_entry.py   # 신규 (버튼 계약)
python3 scripts/verify_executive_ia_polish.py                  # AI 관련 + 명사형 + IA
python3 scripts/verify_live_news_ingestion.py                  # 쿼리 재조정
python3 scripts/verify_static_report.py                        # 마커 기반 live/mock + 라벨
python3 scripts/verify_data_source_honesty.py                  # LIVE/공개 RSS 부재 + 정직성
python3 scripts/verify_score_explanation_ui.py                 # 추적 필요 밴드
python3 scripts/verify_executive_brief_quality.py              # 추적 필요 액션 라벨

# 버튼 payload 미리보기 (발송/비밀값 없음)
TELEGRAM_BOT_USERNAME=hdec_executive_rader_bot REPORT_URL=https://example.com/x.html \
  python3 scripts/send_telegram.py --dry-run-payload "test"
```

커밋 전 반드시: `docs/daily/latest.html`를 **새 빌더로 재생성**해야 한다 (옛 스냅샷엔
`LIVE · 공개 RSS`/`AI 레이더 주요 신호`가 남아 있어 verify_static_report/honesty가 막힌다):
`NEWS_MODE=live python3 scripts/build_static_report.py --output docs/daily/latest.html`
(네트워크 없으면 mock fallback으로 재생성 — 그래도 새 라벨/마커가 들어간다).

## 교훈 (재발 방지)

- **live/mock 판별 문자열 결합도**: 옛 검증기는 `공개 RSS 수집`/`LIVE · 공개 RSS`로 live를
  판별했다. 그 표현을 지우려면 **보이지 않는 마커**를 새로 심고 `_is_live_report` 류를 전부
  마커 기준으로 바꿔야 한다 (verify_static_report / verify_data_source_honesty /
  verify_live_publish_path 3곳). 마커는 HTML 주석(`<[^>]+>`로 stripped)이라 claim/visible-text
  검사에 안 걸린다.
- **t.me URL 리터럴**: `verify_static_report`가 sender의 URL 리터럴을 Telegram API 호스트로만
  제한한다. deep link를 추가하면 `https://t.me/` prefix를 허용 목록에 넣어야 한다.
- **등급 상수 결합도**: `GRADE_WEEKLY` 값을 바꾸면 DB에 저장되는 alert_grade가 바뀌므로
  대시보드 `GRADE_CLASS` **키**(raw alert_grade 렌더)와 `insight.RECOMMENDED_ACTION_BY_GRADE`
  키, `main.ALERT_GRADE_ALIASES["weekly"]` 값도 같이 바꿔야 한다.
- **명사형 검사 스코프**: brief의 `executive_one_liner` + `one_line_reason`만 본다. footer
  고지문(`OPERATOR_NOTE` 등 "아닙니다")과 기사 제목은 검사하면 안 된다(오탐).
- **IMPLICATION_TEMPLATES 역매핑**: `briefing._CATEGORY_BY_IMPLICATION`이 implication 텍스트→
  카테고리 키 역매핑을 같은 dict에서 만든다. 문구를 바꿔도 값이 **고유**하면 안전하다.
- **mock 디지스트 'mock 데이터 기반' 유지**: live source_line만 `자동 수집`으로 바꾸고 mock은
  그대로 둬야 verify_telegram_digest/honesty의 "mock 표기" 검사가 통과한다.
