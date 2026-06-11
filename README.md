# HDEC Executive Radar — Day-1 Safe Slim MVP

현대건설 임원진용 AI·거시경제·건설산업 뉴스 Sensing Agent의 **로컬 mock 기반 walking skeleton**.

- **P0-A는 인터넷 없이 동작한다.** API key, 외부 네트워크, LLM 없이 전체 데모가 가능하다.
- **X API는 Day-1에서 의도적으로 미구현이다.** (rules.md §1 — Day-2 Global Signal Layer에서 별도 검토)
- 기본 실행 모드는 `APP_MODE=mock`이며, mock 모드에서 외부 호출은 0건이다.
- 알림은 자동 발송되지 않는다. 운영자가 **Send 버튼을 눌렀을 때만** mock 발송된다.

---

## 1. 설치

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

(Windows WSL의 /mnt 경로에서는 `python3 -m venv .venv --copies` 권장)

## 2. 환경 설정 (선택)

```bash
cp .env.example .env
```

`.env`가 없어도 기본값(`APP_MODE=mock`)으로 동작한다. `.env`는 절대 commit하지 않는다.

## 3. DB 초기화

서버 시작 시 자동 초기화되지만, 수동 실행도 가능하다 (몇 번을 실행해도 안전):

```bash
.venv/bin/python -m app.db
```

SQLite 파일은 저장소 루트의 `radar.db`에 생성된다 (`.gitignore` 포함).

## 4. mock mode 실행

```bash
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

대시보드: **http://localhost:8000**

## 5. 5분 데모 흐름

1. 대시보드에서 **Run Sensing** 클릭 → collected 30 / deduplicated 28 / scored 28 / alert candidates 3
2. 즉시 알림 후보(최대 3건) 및 전체 신호 목록 확인
3. 기사 클릭 → 우측 Detail에서 3줄 요약, 점수 breakdown, why_not_higher/lower, digest preview 확인
4. **Send** 클릭(운영자 승인) → 하단 Notification Log에 mock 발송 기록 생성
5. **Feedback** 버튼(9종) 클릭 → feedback 테이블 저장

UI 상태 5종: Empty(수집 전) / Loading(실행 중) / Success(목록 표시) /
Error(서버 미기동 등) / No Candidates(4.5점 이상 없음).

## 6. curl 테스트 명령 전체

```bash
# 1) sensing 실행 (mock 로드 → dedup → scoring → insight)
curl -X POST http://localhost:8000/api/sense/run

# 2) 기사 목록 (+ notification log 포함)
curl http://localhost:8000/api/articles
curl "http://localhost:8000/api/articles?min_score=3.5&alert_grade=instant_candidate"

# 3) 기사 상세 (점수 breakdown + insight + digest)
curl http://localhost:8000/api/articles/mock_001

# 4) 테스트 알림 (mock)
curl -X POST http://localhost:8000/api/notify/test \
  -H 'Content-Type: application/json' \
  -d '{"channel":"mock","message":"HDEC Executive Radar test"}'

# 5) 피드백 저장 (9종: positive / irrelevant / instant_alert / weekly_report /
#    boost_topic / exclude_source / exclude_keyword / classify_competitor / classify_macro)
curl -X POST http://localhost:8000/api/articles/mock_001/feedback \
  -H 'Content-Type: application/json' \
  -d '{"feedback_type":"boost_topic","feedback_value":"AI-003","operator_id":"operator"}'

# 6) 운영자 승인 발송 (이 호출 전에는 sent 상태 로그가 생기지 않는다)
curl -X POST http://localhost:8000/api/articles/mock_001/notify \
  -H 'Content-Type: application/json' \
  -d '{"channel":"mock","operator_id":"operator"}'

# 7) 토픽 조회 (Day-1: 읽기 전용)
curl http://localhost:8000/api/settings/topics
```

## 7. 검증

- 기능 검증: 위 curl 7종이 전부 2xx면 P0-A flow 정상.
- 금지 사항 검증(원문 본문 필드·X API 코드·자동 발송 부재 등):
  **`rules.md` §11의 검증 명령을 그대로 실행**한다.
  코드 트리(`app/ data/ templates/`) 기준 금지 문자열 0건이 통과 조건이며,
  스펙 문서(rules.md, PRD.md 등)에 등장하는 금지어 정의 자체는 검출 대상이 아니다.
- `OPENAI_API_KEY`가 없어도 `/api/sense/run`이 성공해야 한다 (P0-A는 rule-based only).

## 8. 프로젝트 구조 (도메인 소유 — CLAUDE.md §4)

```text
app/main.py          API — 라우팅/요청·응답 변환만 (orchestration)
app/db.py            DB — sqlite3를 import하는 유일한 파일
app/collector.py     Collector — mock 로드, snippet 500자 절단, dedup, 저장
app/scoring.py       Scoring — rule-based 9항목 + 가점/감점 + alert_grade
app/insight.py       Insight — template mock insight + digest (alert_grade 저장 안 함)
app/notification.py  Notification — 운영자 Send 시에만 mock 발송
app/feedback.py      Feedback — feedback row 저장만 (가중치 변경 없음)
app/schema.sql       6개 테이블 (articles, article_scores, article_insights,
                     feedback, keyword_rules, notification_logs)
app/config.py        .env 로더 (APP_MODE 기본 mock)
data/mock_articles.json  mock 기사 30건 (dedup 후 28건 유지)
data/topics.json         키워드 seed 15건 (keyword_rules로 seed, 읽기 전용)
templates/index.html     Today Signals 단일 화면 (Vanilla JS)
```

## 9. Day-1 범위 메모

- Scoring: rule-based only (가중치·가점·감점은 rules.md §6 표 그대로).
  즉시 알림 후보는 1회 sensing당 최대 3건 — 초과분은 점수순 상위 3건만 후보로 표기.
- Insight: template 기반 mock (LLM 미사용).
- P0-B(RSS / Naver / OpenAI / Teams / Re-score)는 **구현하지 않았다** —
  P0-A 안정성을 우선했고, mock 경로를 깨뜨리지 않기 위해 Day-1 범위에서 보류.
- 원문 본문은 어떤 형태로도 저장하지 않는다 (snippet 최대 500자).
