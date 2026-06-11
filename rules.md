# rules.md — HDEC Executive Radar Day-1 하드 규칙

이 파일의 규칙은 CLAUDE.md, PRD, 사용자 지시 어느 것과 충돌하더라도 **항상 우선**한다.
이 규칙을 위반하는 코드는 "동작해도 실패"로 간주하고 즉시 되돌린다.

---

## 1. 절대 금지 (Day-1)

코드, 주석, env, 문서 어디에도 만들지 않는다:

- X API 호출 / `api.x.com` / Twitter·X bearer token 로직 / Filtered Stream / X Developer Console 안내
- 실제 카카오 비즈메시지 연동
- 임원 자동 발송 (운영자 Send 클릭 없이 `sent` 상태 notification log 생성 금지)
- Keyword/Topic Settings UI (조회 전용 `GET /api/settings/topics`만 허용)
- 모바일 앱, SSO, 권한 시스템, ML 파인튜닝, 유료 뉴스 연동, 원문 크롤링
- 마이크로서비스 분리, 필수 기능으로서의 백그라운드 스케줄러/폴링
- `.env` git commit

## 2. Mock Mode 규칙

- 기본값: `APP_MODE=mock` (`.env.example`에 명시)
- `APP_MODE=mock`일 때 어떤 코드 경로에서도 네트워크 호출 금지: RSS, Naver, OpenAI, Teams, X 전부.
- 외부 호출 분기는 반드시 `if settings.APP_MODE != "mock"` 가드 안에만 존재 (P0-B 단계에서만).
- mock mode 데이터 소스는 `data/mock_articles.json` 단 하나.

## 3. 데이터 / 저작권 규칙

- 원문 본문 저장 금지. 다음 필드명 생성 금지 (DB, JSON, 변수 어디에도):
  `raw_payload`, `body`, `content`, `full_text`, `article_body`, `full_rss_content`
- articles 허용 필드: `title, normalized_title, source, published_at, collected_at, url, url_hash, snippet, topic_candidates, signal_origin, source_metadata_json, status` (+ `id`)
- `snippet` 최대 500자 — 저장 직전 반드시 절단.
- `source_metadata_json` 허용 키: `provider, query, source_url, collected_at, provider_response_id`만.
- `thumbnail_url` 저장 금지.

## 4. 보안 규칙

- API key, webhook URL을 로그/print/응답 JSON에 절대 출력하지 않는다.
- `TEAMS_WEBHOOK_URL`은 backend 전용. `templates/`와 프론트 JS에 등장 금지.
- CORS는 localhost 최소 허용.
- `.gitignore`에 `.env`, `*.db` 포함.
- X 관련 env 변수는 `.env.example`에도 넣지 않는다.

## 5. DB 규칙

필수 테이블 6개: `articles, article_scores, article_insights, feedback, keyword_rules, notification_logs`

- `article_scores` 필수 컬럼: 9개 항목 점수 + `rule_bonus, rule_penalty, final_score, alert_grade, confidence, scoring_reason, evidence_basis, why_not_higher, why_not_lower`
- `article_insights`에 `alert_grade` 중복 저장 금지.
- `keyword_rules`는 P0-A에서 seed/읽기 전용. feedback이 weight를 수정하지 않는다.
- Day-2 테이블(`x_signals` 등)은 schema.sql 주석으로만 허용.

## 6. Scoring 규칙

- P0-A는 rule-based only. `OPENAI_API_KEY` 부재 시에도 전 flow 통과.
- 9개 항목 각 0~5점: hdec_relevance, executive_importance, business_opportunity, risk_potential, urgency, source_reliability, trend_repeat, competitor_relevance, macro_impact

```text
final_score =
  hdec_relevance*0.20 + executive_importance*0.18 + business_opportunity*0.13
+ risk_potential*0.13 + urgency*0.12 + source_reliability*0.08
+ trend_repeat*0.06 + competitor_relevance*0.05 + macro_impact*0.05
+ rule_bonus - rule_penalty
```

- `final_score` clamp 0~5, `confidence` clamp 0~1.
- alert_grade: `>=4.5 즉시 알림 후보` / `>=3.5 일간 요약` / 전략·반복 트렌드 `주간 리포트 후보` / 저관련 `제외`
- 1회 sensing당 즉시 알림 후보 최대 3건 (초과 시 점수순 상위 3건만 후보 표기).

### 가점
| 조건 | 가점 |
|---|---|
| 데이터센터 + 전력 + 건설/EPC | +0.7 |
| AI 전력수요 + 원전/SMR/송배전 | +0.7 |
| 경쟁 건설사 + AI/스마트건설/데이터센터 | +0.6 |
| 중동/원자재/환율 + 해외수주/플랜트 | +0.6 |
| 정부 정책 + 인프라/에너지/건설투자 | +0.5 |
| 현대건설 직접 언급 | +0.8 |
| 원전/SMR + 데이터센터 전력수요 | +0.7 |

### 감점
| 조건 | 감점 |
|---|---|
| HDEC 사업과 무관한 일반 AI 제품 출시 | -0.8 |
| 소비자 앱 중심 AI 기사 | -0.7 |
| 단순 테마주/주가 기사 | -0.7 |
| 출처 불명확 블로그성 | -0.6 |
| 제목에 AI만 있고 건설/전력/인프라/원전/DC/거시 연결 없음 | -0.8 |
| 동일 내용 24시간 내 재수집 | -1.0 |

## 7. Mock Data 규칙

`data/mock_articles.json` 30개 (최소 25), dedup 후 ≥20 유지.
구성: 고득점 5 (AI DC+전력+EPC/원전) / 중간 5 (거시·원자재·환율·유가) / 저득점 5 (일반 AI 앱·소비자·스타트업) / 경쟁사 3 (삼성물산·GS건설·DL이앤씨·대우건설 중) / 중복 2 (동일 URL 또는 유사 제목) / 안전·중대재해 2 / fallback 3.

## 8. API 규칙 (FastAPI `{param}` 표기, `:id` 금지)

```text
POST /api/sense/run
GET  /api/articles
GET  /api/articles/{article_id}
POST /api/articles/{article_id}/feedback
POST /api/articles/{article_id}/notify
POST /api/notify/test
GET  /api/settings/topics
```

`/api/sense/run` 응답: `collected, deduplicated, scored, alert_candidates, mode, fallback_used`.

## 9. UI 규칙

- 화면은 Today Signals 하나. Detail은 모달/우측 패널.
- 5개 상태 필수: Empty / Loading / Success / Error / No Candidates.
- 목록: 제목, source, published_at, signal_origin, final_score, alert_grade, confidence, scoring_reason, affected_units, opportunity_or_risk, Send 버튼, Feedback 버튼.
- Detail: 원문 링크, 3줄 요약, HDEC implication, affected units, 점수 breakdown, evidence_basis, why_not_higher/lower, executive checkpoints, digest preview.
- Notification Log 영역: sent_at, 기사 제목, channel, send_status, message_preview.
- Feedback 버튼 9종: 좋음/불필요/즉시알림급/주간보고급/이 주제 강화/이 언론사 제외/이 키워드 제외/경쟁사 동향으로 분류/거시경제로 분류.
- 디자인 polish는 P0-A 기능 검증 통과 후 단 1회. Today Signals만. polish 후 P0-A 재검증.

## 10. 도메인별 검수 체크리스트

각 도메인 완료 직후 실행. 하나라도 실패하면 수정 → lessons skill 기록 → 재검수.

**D1 DB**: schema.sql 6테이블 / `grep -E "raw_payload|full_text|article_body|full_rss_content" app/schema.sql` 0건 / `body`·`content` 컬럼명 없음 / init 함수 2회 실행해도 에러 없음(idempotent)
**D2 Data**: json 30건 파싱 성공 / 모든 snippet ≤500자 / 7개 유형 충족 / 본문성 필드 없음
**D3 Collector**: 30 로드 → dedup 후 ≥20 / 동일 url_hash·유사 제목 제거 확인 / 재실행 시 중복 INSERT 없음 / 네트워크 import(requests, httpx 등) 없음
**D4 Scoring**: 전 기사 final_score 0~5 / confidence 0~1 / 고득점 mock이 4.5 이상, 저득점 mock이 제외/일간 / 즉시 후보 ≤3 / why_not_higher·why_not_lower 비어있지 않음
**D5 Insight**: 전 기사 insight row 존재 / alert_grade 컬럼 없음 / digest_message 생성됨
**D6 API**: 7개 endpoint curl 전부 2xx / `:id` 표기 0건
**D7 Notify+Feedback**: Send 전 `SELECT count(*) FROM notification_logs WHERE send_status='sent'` = 0 / Send 후 1건 생성 / feedback 9종 값 모두 저장 가능
**D8 UI**: 5상태 수동 확인 / 프론트 코드에 webhook·key 문자열 0건
**D9 통합**: §11 전체 검증 명령 실행

## 11. 최종 검증 명령 (P0-A 통과 조건)

```bash
curl -X POST http://localhost:8000/api/sense/run
curl http://localhost:8000/api/articles
curl http://localhost:8000/api/articles/{article_id}
curl -X POST http://localhost:8000/api/notify/test
curl -X POST http://localhost:8000/api/articles/{article_id}/feedback -H 'Content-Type: application/json' -d '{"feedback_type":"boost_topic","feedback_value":"AI-003","operator_id":"operator"}'
curl -X POST http://localhost:8000/api/articles/{article_id}/notify -H 'Content-Type: application/json' -d '{"channel":"mock","operator_id":"operator"}'

grep -R "raw_payload" . --exclude-dir=.git
grep -RiE "api\.x\.com|twitter|x bearer token" . --exclude-dir=.git
grep -E "body|content|full_text|article_body" app/schema.sql
```

기대 결과: raw_payload 0건 / X 관련 0건 / schema에 본문 필드 0건 / OPENAI_API_KEY 없이 sense/run 성공 / 네트워크 차단 상태에서 전 flow 동작.

## 12. Final Completion Report Format (이 순서 그대로)

```text
1. P0-A status: PASS / FAIL
2. P0-A verification results
3. What works in mock mode
4. Screens implemented
5. APIs implemented
6. DB tables implemented
7. Security / copyright checks
8. Forbidden checks:
   - raw_payload absent
   - X API absent
   - external calls absent in APP_MODE=mock
   - no auto-send before operator Send
9. P0-B implemented, if any
10. Known limitations
11. How to run locally
```

P0-A 미통과 시 완료 주장 금지. 블로커만 명확히 보고하고 중단한다.
