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

`.env`가 없어도 기본값(`APP_MODE=mock`, `NEWS_MODE=mock`)으로 동작한다. `.env`는 절대 commit하지 않는다.

뉴스 수집 모드는 `NEWS_MODE`로 고른다 (P0-C1 — 자세한 내용은 §14):

| 값 | 동작 |
|---|---|
| `NEWS_MODE=mock` (기본) | `data/mock_articles.json`만 사용 · 네트워크 0건 · 비밀값 0건 |
| `NEWS_MODE=live` | 공개 RSS(Google News RSS)에서 실제 뉴스 수집 · 인증 불필요 · 실패 시 mock fallback |

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
- 작업 방식 체크: `docs/operations/AGENT_WORKFLOW_RULES.md`와
  `docs/operations/LESS_CODE_REVIEW_CHECKLIST.md`를 따른다. 핵심은 기존 소유 경로를
  먼저 찾고, 불필요한 코드는 줄이며, 실제 회귀를 잡는 좁은 verifier로 증명하는 것이다.

## 8. 프로젝트 구조 (도메인 소유 — CLAUDE.md §4)

```text
app/main.py          API — 라우팅/요청·응답 변환만 (orchestration)
app/db.py            DB — sqlite3를 import하는 유일한 파일
app/collector.py     Collector — mock 로드, snippet 500자 절단, dedup, 저장
app/scoring.py       Scoring — rule-based 9항목 + 가점/감점 + alert_grade
app/insight.py       Insight — template mock insight + digest (alert_grade 저장 안 함)
app/notification.py  Notification — 운영자 Send 시에만 mock 발송
app/feedback.py      Feedback — feedback row 저장만 (가중치 변경 없음)
app/briefing.py      Briefing — (P0-B2) 저장된 score/insight 읽기 전용 executive brief 파생
app/macro_snapshot.py  Macro — (P0-B6) macro 데이터 출처 판별 (mock_static/unavailable,
                     live 미구현 시 가짜 fallback 금지)
app/schema.sql       6개 테이블 (articles, article_scores, article_insights,
                     feedback, keyword_rules, notification_logs)
app/config.py        .env 로더 (APP_MODE 기본 mock)
data/mock_articles.json  mock 기사 30건 (dedup 후 28건 유지)
data/mock_macro_snapshot.json  데모용 mock macro 고정값 (mode=mock_static — 수치는 표시 안 함)
data/topics.json         키워드 seed 15건 (keyword_rules로 seed, 읽기 전용)
templates/index.html     Today Signals 단일 화면 (Vanilla JS)
docs/index.html          GitHub Pages 루트 랜딩 (최신 브리프 링크)
docs/daily/latest.html   커밋된 정적 Executive Daily Brief 스냅샷
```

## 9. Day-1 범위 메모

- Scoring: rule-based only (가중치·가점·감점은 rules.md §6 표 그대로).
  즉시 알림 후보는 1회 sensing당 최대 3건 — 초과분은 점수순 상위 3건만 후보로 표기.
- Insight: template 기반 mock (LLM 미사용).
- P0-B(RSS / Naver / OpenAI / Teams / Re-score)는 **구현하지 않았다** —
  P0-A 안정성을 우선했고, mock 경로를 깨뜨리지 않기 위해 Day-1 범위에서 보류.
  (예외: P0-B1 Telegram mock daily digest — §10. 외부 뉴스 API 없이 mock 데이터만 사용.)
- 원문 본문은 어떤 형태로도 저장하지 않는다 (snippet 최대 500자).

## 10. Telegram Mock Daily Digest (P0-B1)

GitHub Actions가 mock 데이터 기반 임원용 일일 다이제스트(Top 3 시그널)를
Telegram으로 발송한다. **외부 뉴스 API는 호출하지 않는다** — 기존 P0-A 파이프라인
(collector → scoring → insight)을 임시 SQLite DB 위에서 재사용하며, 저장소의
`radar.db`는 건드리지 않는다.

### 필요한 GitHub repository secrets (이름만 — 값은 어디에도 기록 금지)

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_IDS` (콤마 구분 복수 가능)

### 로컬 dry-run (네트워크·비밀값 불필요)

```bash
# 다이제스트 메시지 생성 (발송 없음)
python3 scripts/build_telegram_digest.py --dry-run

# 기계 검증용 JSON 출력
python3 scripts/build_telegram_digest.py --json

# 도메인 회귀 검증 (RESULT: PASS / exit 0 이 통과 조건)
python3 scripts/verify_telegram_digest.py
```

### GitHub Actions 실행

- **수동**: Actions → **Telegram Notify** → Run workflow. `message`를 비우면 live 리포트를
  게시하고 다이제스트 후보를 만든다. **실제 발송하려면 `approve_send`에 `true`를 넣어야 한다**
  (P0-D3I 사람 검토 게이트 — 비우면 후보만 보류하고 발송하지 않는다).
- **스케줄**: 매일 UTC 23:00 (KST 08:00)에 live 리포트를 **자동 게시**한다(`cron: "0 23 * * *"`).
  단 **다이제스트 발송은 자동이 아니다** — 예약 실행은 발송 게이트가 `manual`로 닫혀 후보만
  보류하고, 운영자가 `approve_send=true`로 승인할 때만 발송된다(런북 `docs/operations` §G).
- 발송 전에 워크플로가 `scripts/verify_telegram_digest.py`,
  `scripts/verify_executive_brief.py`, `scripts/verify_executive_brief_quality.py`,
  `scripts/verify_static_report.py`, `scripts/verify_data_source_honesty.py`를
  먼저 실행해 회귀가 있으면 발송 자체를 차단한다.
- 성공 로그 기대값: `Telegram delivery summary: delivered=N, failed=0`
  (token·chat id는 로그에 출력되지 않는다).

## 11. Executive Brief Layer (P0-B2)

벤치마크(일간 건설 브리프)에서 착안한 **임원용 브리핑 레이어**.
다만 포지셔닝은 다르다 — 벤치마크가 *광범위 뉴스 수집형 일일 브리프*라면,
이 제품은 *선별된 임원 시그널 레이더*다. 기사 목록을 늘리는 대신
이미 채점된 신호를 집계·종합해 "오늘 무엇이 중요한가"를 한 화면/한 메시지로 줄인다.

`app/briefing.py`가 저장된 score/insight를 **읽기만** 해서 파생 데이터를 만든다
(점수 재계산·DB 쓰기 없음):

- **데일리 현황판** — 수집·분석 기사 / 즉시 알림 후보 / 검토 필요 / 추적 필요 / 참고·제외
  (현황판 버킷 의미는 리포트·대시보드에 설명 캡션으로 함께 노출 — §16.1)
- **오늘의 Executive Signal** — 기회·리스크를 종합한 1~2문장 한국어 one-liner
  (제목 이어붙이기가 아니라 카테고리 표현 사전으로 조립)
- **신규 이슈 Top 5** / **즉시 알림 후보 Top 3** (각 신호에 spread 지표 포함)
- **주요 테마 Top 5** — topic 후보를 점수 가중으로 랭킹
- **카테고리 요약** — insight 카테고리 분포 (즉시 후보 수 표시)
- **Macro Snapshot** — 시장지표 **미연동** 상태 표시 (P0-B6).
  `data/mock_macro_snapshot.json`은 데모용 mock 고정값 fixture일 뿐이며,
  live 연동 전까지 어떤 표시 레이어에도 수치를 노출하지 않는다

### 로컬 실행 (네트워크·비밀값 불필요)

```bash
# 사람용 brief 텍스트 (발송 없음)
python3 scripts/build_executive_brief.py --dry-run

# 기계 검증용 JSON
python3 scripts/build_executive_brief.py --json

# 도메인 회귀 검증 (RESULT: PASS / exit 0 이 통과 조건)
python3 scripts/verify_executive_brief.py
```

### 소비처

- **대시보드**: `GET /api/brief` — Run Sensing 후 상단 EXECUTIVE BRIEF 패널에 표시.
  수집 전(빈 DB)에는 패널이 숨겨진다.
- **Telegram**: `scripts/build_telegram_digest.py`가 같은 brief 데이터로
  다이제스트를 조립한다. Actions → **Telegram Notify** → Run workflow에서
  `message`를 비워 두면 이 brief 기반 다이제스트가 발송된다 (§10과 동일 경로).

### mock 모드 한계

- 신호가 mock 30건 고정이라 brief 내용이 결정적이다 (Top 3 = mock_001/002/003).
  단, 점수의 신선도 감점이 날짜에 따라 작동하므로 검토 필요/주간 등급 분포는 조금씩 변할 수 있다.
- **spread 지표는 추정치다** — topic 후보가 겹치는 신호 수 기반 휴리스틱이며,
  동일 사건 클러스터링이 아니다. dedup으로 제거된 중복 기사는 집계되지 않는다.
  라벨도 보수적으로 "토픽상 관련 추정 신호 n건"으로 표기한다.
- macro 지표는 **표시하지 않는다** — §13 Data Source Honesty 참조.

## 12. Static Report Page + Telegram Link Card (P0-B5)

Telegram은 **짧은 알림 진입점**, 시각적 요약은 **정적 대시보드 페이지**, 상세 근거는
**정적 HTML 리포트 페이지**가 담당한다. 다이제스트 메시지에
"**대시보드 보기**"와 "**상세 리포트 보기**" inline 버튼이 붙고, 각각
`docs/daily/dashboard-latest.html`과 `docs/daily/latest.html`로 연결된다.

리포트 페이지는 §11의 brief 데이터를 그대로 렌더링한 **standalone HTML**이다 —
외부 CDN/스크립트/폰트 참조 0건(Pretendard 우선 로컬 폰트 스택), 한국어,
executive memo 톤의 모바일 우선 레이아웃
(현황판 / 오늘의 Executive Signal / Top 3 시그널(액션 라벨·왜 중요한가·권장 워치
액션·spread) / 추가 관찰 이슈 / 주요 테마 / 카테고리 요약 /
Macro Snapshot 미연동 placeholder / mock·데이터 출처 고지).

Pages 루트에는 `docs/index.html` 랜딩 페이지가 있어 `/`로 들어와도
최신 브리프(`./daily/latest.html`)로 이동할 수 있다 (404 방지).

### 로컬 실행 (네트워크·비밀값 불필요)

```bash
# 리포트 생성 → docs/daily/latest.html
python3 scripts/build_static_report.py --output docs/daily/latest.html

# 요약 대시보드 export → docs/daily/dashboard-latest.html
python3 scripts/build_static_dashboard.py --output docs/daily/dashboard-latest.html

# 요약/기계 검증
python3 scripts/build_static_report.py --dry-run
python3 scripts/build_static_report.py --json
python3 scripts/build_static_dashboard.py --dry-run
python3 scripts/build_static_dashboard.py --json

# 도메인 회귀 검증 (RESULT: PASS / exit 0 이 통과 조건)
python3 scripts/verify_static_report.py
```

로컬에서 열기: `explorer.exe "$(wslpath -w docs/daily/latest.html)"` (WSL) 또는
`python3 -m http.server 8088 --directory docs` → http://localhost:8088/daily/latest.html

### Telegram 워크플로 동작 (REPORT_URL · live 게시)

- 워크플로는 발송 전에 모든 verifier(P0-B1/B2/B5/B6 + P0-C1 + P0-C1.5 `verify_live_publish_path.py`)와
  리포트 생성이 깨지지 않는지 mock 안전 모드로 확인한다.
- **빈 메시지/예약 실행 (P0-C1.5 자동 게시)**: `NEWS_MODE=live`로 `docs/daily/latest.html`을
  생성하고, **live 수집이 성공한 경우에만** `github-actions[bot]`이 main에
  `chore: update live daily report`로 auto-commit·push한 뒤 `NEWS_MODE=live` 다이제스트를
  발송한다. live 수집이 실패하면 가짜 live를 게시하지 않고(작업 트리 복원) mock(데모)
  라벨된 fallback 다이제스트를 발송한다. 자동 commit을 위해 `permissions: contents: write`가 필요하다.
- **커스텀 메시지 실행**: 입력 메시지를 그대로 발송하며 live를 강제하지 않는다.
- repo **Variables**(권장) 또는 Secrets에 `DASHBOARD_URL`이 있으면 메시지에
  "대시보드 보기" 버튼이 붙는다. 없고 `REPORT_URL`이 `/daily/latest.html`이면
  `/daily/dashboard-latest.html`로 파생한다.
- repo **Variables**(권장) 또는 Secrets에 `REPORT_URL`이 있으면 메시지에
  "상세 리포트 보기" 버튼이 붙는다. 로그에는 URL 값 대신
  `Summary dashboard link enabled: true/false`, `Report link enabled: true/false`만 출력된다.
- `REPORT_URL`이 없으면 기존 **텍스트 전용** 다이제스트를 발송하며 실패하지 않는다.

**REPORT_URL / DASHBOARD_URL 등록 절차 (1회 수동 설정):**

1. GitHub 저장소 → **Settings** → **Secrets and variables** → **Actions**
2. **Variables** 탭 → **New repository variable**
3. Name: `REPORT_URL`
4. Value: `https://guides.playground-aidesignlab.co.kr/HDEC-News-Sensor/daily/latest.html`
   (Pages 게시 주소 — 커스텀 도메인 기준. 기본 도메인이면
   `https://<owner>.github.io/<repo>/daily/latest.html`)
5. 선택: Name `DASHBOARD_URL`, Value
   `https://guides.playground-aidesignlab.co.kr/HDEC-News-Sensor/daily/dashboard-latest.html`
   (`REPORT_URL`이 표준 `/daily/latest.html`이면 생략 가능 — sender가 파생)

### GitHub Pages 설정 (브랜치 게시 — 소스 설정만 1회 수동)

리포트 **commit/publish는 워크플로가 자동화**하지만(P0-C1.5), Pages **소스 설정**은 1회 수동이다:

Settings → **Pages** → Source: **Deploy from a branch** → Branch: **main** /
Folder: **/docs** → 게시 주소 예: `https://<owner>.github.io/<repo>/daily/latest.html`
→ 이 주소를 `REPORT_URL` Variable로 등록. 요약 대시보드는
`https://<owner>.github.io/<repo>/daily/dashboard-latest.html`.
상세 절차는 `docs/daily/README.md`.

### 보안 주의

- 무료 플랜의 GitHub Pages는 **공개**다 — 게시 대상은 mock 데모 데이터 또는
  **공개 RSS 뉴스의 제목·요약·원문 URL과 public-safe 점수 언어**뿐이다.
  **비공개 유료 본문·내부 민감 신호·운영자 메모는 공개 Pages에 게시하지 않는다**.
  그런 데이터 단계에서는 사내/비공개 호스팅을 사용한다.
- 리포트 HTML에는 비밀값·토큰·chat id·본문 전문이 포함되지 않는다
  (`verify_static_report.py`가 mock·live 모드 모두, 외부 리소스 0건과 함께 기계 검사한다).

## 13. Data Source Honesty (P0-B6)

mock 데이터가 실제 조사/실시간 데이터로 오인되는 것을 구조적으로 막는 규칙.
`scripts/verify_data_source_honesty.py`가 전부 기계 검사하며, 워크플로가
발송 전에 실행해 위반 시 발송을 차단한다.

- **뉴스 출처는 provenance로 정직하게 표기된다** — `NEWS_MODE=mock`(기본)이면
  `news_data_mode=mock`, `NEWS_MODE=live`이면 공개 RSS 수집 결과에 따라 `live`다.
  live 수집이 실패하면 `mock`으로 fallback하고 `news_fallback_used=true`로 표기한다
  (가짜 live 주장 금지 — §14 참조).
- **macro 데이터는 mock_static 또는 unavailable이다** — live 시세 연동이
  구현되기 전까지 `macro_data_mode`는 `live`가 될 수 없다.
- **Telegram/정적 리포트/대시보드는 mock 고정값을 현재 시장값처럼 표시하지
  않는다** — live가 아닌 한 수치 자체를 렌더링하지 않고
  "시장지표 미연동" placeholder만 표시한다.
- **시장지표 문맥에서 "실시간/현재/최신/live" 표현은 부정(미연동·아님 등)과
  함께일 때만 허용**된다 — verifier가 라인 단위로 검사한다.
- **미래의 live macro 연동은 `source`와 `updated_at`을 반드시 제공**해야 하며,
  `app/macro_snapshot.py`의 `get_macro_snapshot()` 경계를 통해서만 들어온다.
- **live 조회가 실패하면 `unavailable`로 강등**한다 — 가짜 숫자/이전 mock
  값으로 silent fallback 하지 않는다.

brief JSON에는 데이터 출처 provenance 필드가 항상 포함된다:
`news_data_mode` / `news_source` / `news_fallback_used` / `macro_data_mode` /
`macro_source` / `macro_updated_at` / `macro_is_stale` / `data_warning`.

```bash
# 데이터 정직성 회귀 검증 (RESULT: PASS / exit 0 이 통과 조건)
python3 scripts/verify_data_source_honesty.py

# 시그널 품질 규칙(액션 라벨·보수적 spread 표현·이슈 다양성) 검증
python3 scripts/verify_executive_brief_quality.py
```

## 14. Live News MVP + 점수 설명 UX (P0-C1)

"보기 좋은 mock 리포트"에서 "쓸 수 있는 실뉴스 MVP"로 넘어가는 단계.

### 14.1 실제 공개 뉴스 수집 (`NEWS_MODE=live`)

- **출처**: 공개 RSS(Google News 검색 RSS). 설정 파일은 `data/live_news_sources.json`.
- **무인증**: API key/비밀값이 전혀 필요 없다. Naver API 자격증명도 불필요.
- **본문 미저장**: 기사 페이지를 크롤링하지 않는다. RSS가 주는 **제목·요약(snippet)·
  출처·원문 링크·게시 시각**만 저장한다 (`raw_payload`/`full_text`/`article_body` 등
  본문 필드는 생성 자체가 금지).
- **원문 링크**: 저장한 `url`은 실제 기사로 연결되는 클릭 가능한 링크다. 대시보드/정적
  리포트에서 `target="_blank" rel="noopener noreferrer"`로 새 탭에 열린다.
- **X(엑스) 차단**: x.com 등 X 계열 소스는 수집 단계에서 제외한다 (rules.md §1).
- **정직한 fallback**: live 수집이 0건/실패면 `mock`으로 fallback하고
  `news_fallback_used=true`로 표기한다 — mock을 live로 위장하지 않는다.
- 경계: 네트워크 IO는 `app/live_collector.py`만 소유하고, `app/collector.py`는 live
  경로에서만 이를 **지연 import**한다 (mock 경로는 네트워크 import조차 하지 않는다).

```bash
# 실제 공개 RSS로 brief/리포트 생성 (네트워크 필요, 비밀값 불필요)
NEWS_MODE=live python3 scripts/build_executive_brief.py --dry-run
NEWS_MODE=live python3 scripts/build_static_report.py --output docs/daily/latest.html

# 수집 경로 회귀 검증 (네트워크 없으면 SKIP, 가짜 성공 주장 안 함)
python3 scripts/verify_live_news_ingestion.py

# live 게시 경로 회귀 검증 (워크플로 auto-commit/발송 정직성 — P0-C1.5)
python3 scripts/verify_live_publish_path.py
```

> 공개 GitHub Pages에 게시하는 `docs/daily/latest.html`은 저장소에 **mock 스냅샷**으로
> 커밋돼 있고(재현 가능·검증 가능), **Telegram Notify 워크플로의 빈 메시지/예약 실행이
> `NEWS_MODE=live`로 실뉴스 리포트를 생성해 live 수집 성공 시 자동으로 commit·게시한다**
> (P0-C1.5 — `git log`의 `chore: update live daily report`). 운영자가 로컬에서
> `NEWS_MODE=live`로 직접 생성해 commit해도 된다. 공개 게시에는 공개 뉴스
> 제목/요약/URL과 public-safe 점수 언어만 싣는다.

### 14.2 점수 표시 척도 (직관화)

| 표현 | 의미 |
|---|---|
| **중요도 X.X / 5.0** | rule-based 9항목 가중합(0~5). 분모를 항상 명시하고 미터 막대로 표시 |
| 점수대 라벨 | 4.5+ 즉시 확인 · 3.5+ 검토 필요 · 2.0+ 추적 필요 · 그 미만 참고/제외 |
| **점수 구성요소** | 현대건설 관련성·사업기회·리스크/규제·긴급도·출처 신뢰도·반복/확산 신호 (각 0~5 막대) |
| **판정 신뢰도 NN%** | 옛 `confidence 0.90` 표기를 백분율로 (규칙 매칭 신뢰도) |
| **테마 비중 0~100** | 옛 `강도 30.7`을 대체. 가장 강한 테마=100 기준 상대 지표 (원시 가중합은 JSON에만) |
| **관련 기사 n건 · 출처 m곳** | 옛 `토픽상 관련 추정 신호`를 대체. 제목·토픽 기준 자동 묶음(추정)이며 동일 사건 클러스터 확정값이 아님 |

```bash
# 점수 설명/시각화 UX 회귀 검증
python3 scripts/verify_score_explanation_ui.py
```

### 14.3 남은 한계

- **시장지표(macro)는 아직 미연동**이다. 가짜 USD/KRW·KOSPI·WTI·VIX 수치를 절대
  표시하지 않고 "시장지표 미연동"만 노출한다 (§13).

## 15. Source Quality Filter (P0-C1.6)

공개 RSS(Google News 등)는 언론 보도뿐 아니라 **네이버 블로그·카페, 티스토리,
유튜브, 커뮤니티(디시·클리앙 등), 재전송/홍보성 결과**를 섞어 돌려준다.
임원용 리포트에 이런 비-뉴스 결과가 Top 3로 올라오지 않도록 출처 품질 가드레일을 둔다.

- **공개 RSS에는 블로그·커뮤니티가 섞일 수 있다.** 이는 출처의 특성이지 버그가 아니다.
- **임원용 리포트는 낮은 품질 출처를 제외하거나 캡한다.** 블로그/카페/커뮤니티/
  티스토리/유튜브성 출처는 `live` 수집 단계에서 제외되고, 그래도 들어온 경우
  점수를 즉시 알림 임계(4.5) 아래로 캡해 상위 노출을 막는다.
- **Top 3 주요 신호는 블로그·카페·커뮤니티를 피한다.** 신뢰 매체/공공기관 출처가
  우선되고, 알 수 없는 출처는 자동 차단하지 않고 중립으로 둔다.
- **출처 품질은 사실 보증이 아니라 랭킹/필터 가드레일이다.** 출처가 신뢰 매체라는 것이
  기사 내용의 진위를 보장하지는 않는다 — 어떤 결과를 임원에게 먼저 보여줄지 정하는 신호다.

### 정책 위치와 분류

- **정책 데이터**: `data/source_quality_rules.json` (제외/신뢰/공공기관/재전송 패턴 + 점수 캡).
- **분류 도메인**: `app/source_quality.py` — `(source, title)`만 보는 순수 함수.
  결과: `source_quality ∈ {trusted, neutral, low, excluded}`,
  `source_type ∈ {news, institution, blog, cafe, community, video, aggregator, unknown}`.
- **적용 지점** (단일 분류 함수를 각 도메인이 호출):
  - `app/live_collector.py` — `excluded` 출처를 수집 단계에서 제외 (raw dict엔 품질 필드 미부착).
  - `app/scoring.py` — `excluded`→1.0, `low`→3.4로 점수 캡 (즉시 알림 임계 4.5 미만 보장).
  - `app/briefing.py` — 시그널에 출처 품질 라벨(신뢰/일반/낮은 신뢰도) 부착 + Top 3에서 `excluded` 배제.
  - 정적 리포트/대시보드 — 신뢰·낮은 신뢰도일 때만 작은 라벨 노출(일반 출처는 생략) + 하단 고지.
  - Telegram — `live` 수집일에 즉시 확인급(신뢰 출처) 신호가 없으면 약한 출처를 임원
    알림으로 띄우지 않고 "오늘은 즉시 확인급 신호 없음 · 추적 필요 신호 중심"으로 표기.

### mock 영향 없음

mock 데모 숫자(수집·분석 28 / 즉시 3 / 검토 필요 4 / 추적 14 / 참고·제외 7 · 다이제스트 ~795자)는
그대로 유지된다 — 캡은 점수를 낮추기만 하고, mock의 낮은 품질 항목은 이미 0.0/제외라
캡이 동작하지 않기 때문이다. live 약한-출처 안내도 `live` 모드에서만 붙는다.

```bash
# 출처 품질 필터 회귀 검증 (네트워크 없이 결정적 — RESULT: PASS / exit 0)
python3 scripts/verify_source_quality_filter.py

# 실제 공개 RSS로 품질 필터 적용된 리포트 생성 (네트워크 필요)
NEWS_MODE=live python3 scripts/build_static_report.py --output /tmp/hdec_live_quality_report.html
```

> 한계: 출처 분류는 출처/제목 패턴 휴리스틱이라 새로운 블로그/커뮤니티 도메인은
> 패턴에 추가해야 잡힌다. Google News 리다이렉트 URL은 MVP에서 그대로 허용하며
> (발행사 해석은 불안정·불필요한 위험), 발행사 명확화는 RSS가 주는 출처명 노출로 대신한다.

## 16. Category Drilldown & Evidence Explorer (P0-C1.7)

리포트가 "Top 3 요약 + 카테고리 건수"만 보여주던 단계에서, **수집·분석된 30여 건을
카테고리별로 펼쳐 근거를 직접 감사**할 수 있는 임원용 evidence brief로 확장했다.
이제 "AI 데이터센터·전력 인프라 13건"이 **어떤 기사들로 구성됐는지** 클릭해 확인할 수 있다.

- **수집 총량을 카테고리별로 감사할 수 있다.** brief의 `category_sections`는 채점된 전 기사를
  카테고리로 묶으며, 각 섹션의 `total_count` 합계는 카테고리 요약 카운트 합계와 정확히 일치한다.
- **카테고리 기사 목록은 '근거 목록'이지 본문 아카이브가 아니다.** 각 항목은 제목·출처·출처 품질·
  발행 시각·**중요도(/5.0)**·등급·원문 링크·시사점(왜 중요한가)만 담는다.
- **본문 전문은 저장하지 않는다.** `category_sections` 항목에 `snippet`/본문 계열 필드는 없으며,
  rules.md §3(본문 전문 미저장) 계약을 그대로 따른다 — RSS가 준 메타데이터만 파생 요약한다.
- **비-뉴스 출처는 근거 목록에서 제외하되 건수에는 포함한다.** 블로그·카페·커뮤니티성(excluded)
  출처는 근거 목록(`top_articles`)에 노출하지 않지만 카테고리 총건수에는 남겨 "외 n건"으로
  정직하게 표기한다 (P0-C1.6 Top-3 가드와 동일 정책).
- **표시 레이어:**
  - 정적 리포트 — "카테고리별 근거 기사" 섹션을 **네이티브 `<details>`/`<summary>`**로 렌더
    (외부 JS/CDN 0건). 원문 링크는 새 탭 + `rel="noopener noreferrer"`.
  - 대시보드 — Today Signals 아래 "카테고리별 근거 기사" 섹션. 카테고리 칩을 누르면 해당
    카테고리의 근거 기사가 인라인으로 표시되고, 기사를 누르면 기존 상세 패널이 열린다.
  - Telegram — 드릴다운을 넣지 않고 "원문·점수·카테고리별 근거 기사는 리포트에서 확인" 한 줄로만
    가리킨다 (다이제스트는 간결 유지).
- **용어 정리(Phase 5):** `오늘 감지 신호`→`수집·분석 기사`, `제외/참고`→`참고/제외`,
  즉시 확인급이 없는 날의 헤딩은 `주요 관찰 신호` + "즉시 확인급 신호 없음" 안내(낮은 점수도
  숨기지 않고 이유를 설명).
- **시장지표는 여전히 미연동이다.** 거시 지표 실시간 연동은 다음 스프린트(§17 P0-C2)다.

```bash
# 카테고리 드릴다운 회귀 검증 (네트워크 없이 결정적 — RESULT: PASS / exit 0)
python3 scripts/verify_category_drilldown.py

# 실제 공개 RSS로 카테고리 드릴다운 리포트 생성 (네트워크 필요, 비밀값 불필요)
NEWS_MODE=live python3 scripts/build_static_report.py --output /tmp/hdec_live_category_report.html
```

> 한계: 카테고리당 노출 근거 기사는 상위 6건(`TOP_CATEGORY_ARTICLES`)이며 나머지는 "외 n건"으로
> 집계만 한다. 카테고리 분류는 저장된 insight implication의 역매핑이라 새 카테고리는 정책에
> 추가해야 잡힌다. 카테고리 필터는 단일 선택(칩)이며 다중 필터/검색은 범위 밖이다.

### 16.1 드릴다운 기본 접힘 + 참고/제외 가시성 + 용어 명확화 (P0-C1.8)

드릴다운/현황판의 UX 혼란을 바로잡는 보정 스프린트:

- **카테고리 드릴다운은 기본 접힘이다.** 정적 리포트는 더 이상 첫 카테고리를 자동으로
  펼치지 않는다 — 모든 `<details class="cat-drill">`이 닫힌 상태로 렌더되고, "카테고리를
  펼쳐 근거 기사를 확인하세요." 안내만 둔다. 대시보드도 카테고리를 자동 선택하지 않고
  ("카테고리를 선택하면 근거 기사가 표시됩니다.") 칩을 눌러야 해당 근거가 표시된다.
- **참고/제외 기사를 직접 들여다볼 수 있다.** 카운트만 보이던 "참고/제외" 버킷을
  리포트 하단 **"참고/제외 · 출처 품질 감사"** 접이식 섹션에서 확인한다. 각 항목은
  제목·출처·카테고리·중요도(/5.0)·사유·원문 링크를 담는다(본문 전문 없음).
- **두 기준을 분리한다 (섞지 않는다):**
  - **참고/제외 기사** = 정상 뉴스 출처이지만 현대건설 관련성·우선순위가 **낮은** 기사
    (brief `review_excluded_evidence`).
  - **출처 품질 제외 결과** = 블로그·카페·커뮤니티 등 **비뉴스성/낮은 신뢰 출처**
    (brief `source_filtered_evidence`). live 수집에서 이 출처는 수집 단계에서 걸러져
    DB에 남지 않으므로, collector provenance(`source_filtered`: 제목/출처/URL — 본문 없음)로
    **감사 투명성**을 위해서만 surface한다. Top 3·근거 목록에는 절대 노출하지 않는다.
- **혼란스럽던 "일간 요약" 라벨을 "검토 필요"로 바꿨다.** 이 버킷은 "일간 요약이 생성됐다"가
  아니라 "일간 검토가 필요한 후보(중요도 3.5~4.4)"라는 뜻이다. 현황판·대시보드 배지·Telegram
  현황판이 모두 **검토 필요**로 통일된다(내부 등급 상수 값도 동일하게 정리). 현황판 버킷 의미는
  리포트·대시보드에 설명 캡션으로 함께 노출한다:
  - 즉시 알림 후보: 중요도 4.5 이상 — 운영자 즉시 확인 후보
  - 검토 필요: 중요도 3.5~4.4 — 일간 검토 후보
  - 추적 필요: 전략·반복 트렌드 — 지속 추적 대상
  - 참고/제외: 낮은 관련성 또는 제외 판단
- **Telegram은 간결 유지** — 현황판 한 줄이 "검토 필요 N"으로 표시되고, "참고/제외 기사도
  리포트 하단에서 확인 가능" 한 마디만 덧붙인다(드릴다운 미포함).
- **시장지표는 여전히 미연동이다** (다음 스프린트 §17).

```bash
# 드릴다운/참고제외/용어 회귀 검증 (네트워크 없이 결정적 — RESULT: PASS / exit 0)
python3 scripts/verify_category_drilldown.py
```

## 17. Executive IA Simplification + AI/Risk Radar Focus (P0-C1.9)

리포트가 기능적으로는 동작하지만 임원에게 **기술적·산만**했던 문제를 바로잡는
정보구조(IA) 보정 스프린트. "디버그/리포팅 UI"가 아니라 **깔끔한 임원용 레이더**로
재구성한다 — AI를 전면에, 리스크·규제를 분명히, 거시경제는 보조(접힘)로.

### 새 리포트 IA (AI-first)

정적 리포트(`docs/daily/latest.html`)와 대시보드가 다음 순서로 구성된다:

1. **Executive Signal** — 한 줄 시그널 + 현황판.
2. **상단 목차 버튼** — `[AI 관련] [리스크·규제] [수주·해외] [거시경제] [전체 근거]`
   (네이티브 앵커, JS 없음).
3. **AI 관련 (주력)** — Executive Signal 직후 첫 신호 섹션. AI 데이터센터·전력·SMR·
   냉각·스마트건설·건설 AI 신호가 첫 화면을 차지한다.
4. **리스크·규제 레이더** — 중대재해·안전·법규·입찰제한·처벌·규제 리스크. AI-heavy가
   아니라는 이유로 묻히지 않는다.
5. **수주·해외 신호** — 수주·발주·중동·플랜트·해외 프로젝트.
6. **거시경제 — 기본 접힘** (`<details class="radar-section macro-section">`, `open` 없음).
   FX·금리·원자재 등. 첫 화면을 점유하지 않는다.
7. **전체 근거 — 기본 접힘** — 테마·카테고리 요약·카테고리별 근거 기사·참고/제외·
   출처 품질 감사를 한 서랍에 모은다.

### 레이더 분류 (`app/radar.py`)

- 채점된 각 기사를 `radar_section ∈ {ai, risk_regulation, business_overseas,
  macro_economy, other}` 중 **하나**로 분류한다 (제목·스니펫·토픽 키워드 + insight 카테고리).
- **AI 인프라 신호는 전력/SMR/거시를 언급해도 `ai`로 둔다** (사용자 정의 AI 우선).
- **순수 거시 변수(수주·해외 이벤트 없는 FX·유가·금리)는 `macro_economy`**로 두고,
  더 나은 AI/리스크/사업 신호가 있으면 기본 Top에서 빠진다 → 거시가 화면을 지배하지 않는다.
- 경계: `briefing.py`와 동일한 **파생 전용** — DB 쓰기/네트워크/점수·등급 재계산 없음.

### 리스크 레이더는 AI 기회 점수와 분리한다

문제: 중대재해·중대재해처벌법·안전 규제는 9항목 **가중합에서 희석**돼 종합 중요도가
낮게(예: 2.25/5.0) 나올 수 있는데, 건설 임원에겐 치명적일 수 있다.

- 리스크 신호에 **`risk_priority_score`**(0~5)를 부여한다 — 저장된 `risk_potential`과
  심각도 floor(중대재해 4.3 / 제재 3.8 / 규제 3.3 / 안전 2.8) 중 큰 값. 종합 중요도가
  낮아도 리스크 레이더 **상단**에 노출돼 **묻히지 않는다**.
- 리스크 레이더는 broader pool(채점된 전 기사, 비뉴스 출처만 제외)에서 `risk_priority`순
  으로 뽑으므로, 제외/주간 등급의 안전·규제 기사도 surface된다.
- 부가 필드: `risk_reason`, `risk_radar_label`(중대재해/제재/규제/안전), `regulatory_relevance`.

### 점수 상세는 펼쳐서 본다 (아코디언)

- 신호 카드는 **중요도 점수 + 컴팩트 미터**만 보여준다.
- 9항목 중 표시 6종(현대건설 관련성/사업기회/리스크·규제/긴급도/출처 신뢰도/반복·확산)은
  **`<details>` '중요도' summary** 뒤에 기본 접힘 — JS 없이 클릭하면 펼쳐진다.

### 용어 정리 (임원 친화적)

| 이전 | 이후 |
|---|---|
| 상대 강도 | 테마 비중 |
| 유사 주제 기사 / 참고 묶음 추정 | 관련 기사 (n건 · 출처 m곳) |
| 권장 워치 액션 | (제거 — 왜 중요한가 한 줄로 통합) |
| 관찰 / 기회 (카드 라벨) | (제거 — 노이즈 라벨 삭제) |
| 주요 관찰 신호 Top 3 | 섹션별 레이더 (AI 관련 등) |
| 헤더의 "뉴스 공개 RSS 수집 · 시장지표 미연동" | (헤더에서 제거 → 작은 footer 고지) |
| 거시 섹션 "시장지표 미연동" | "시장지표 준비 중 / 다음 단계 연동 예정" (정직성 `미연동` 유지) |

> **데이터 정직성은 그대로다.** 시장지표는 여전히 미연동이며 가짜 수치를 만들지 않는다 —
> 노이즈 표현만 메인 화면에서 빼고 거시 섹션/footer에 정직하게 남긴다(§13 가드 유지).

### Telegram도 AI-first

AI 관련 Top 3 → 리스크·규제 한 줄 → 주요 테마 → 카테고리 → (맨 뒤) Macro Snapshot
"시장지표 미연동 · 거시경제 신호는 리포트에서 확인". 거시로 시작하지 않는다.

```bash
# IA 보정 회귀 검증 (네트워크 없이 결정적 — RESULT: PASS / exit 0)
python3 scripts/verify_executive_ia_polish.py

# 실제 공개 RSS로 AI-first 리포트 생성 (네트워크 필요, 비밀값 불필요)
NEWS_MODE=live python3 scripts/build_static_report.py --output /tmp/hdec_ia_report.html
```

> 한계: 레이더 분류는 키워드 + insight 카테고리 휴리스틱이라 새 토픽은 `app/radar.py`
> 키워드에 추가해야 정확히 잡힌다. 거시경제 앵커(`#macro`) 클릭 시 자동 펼침은 브라우저의
> 네이티브 `<details>` 동작에 따른다(요약을 탭하면 항상 펼쳐짐). 시장지표 실시간 연동은
> 다음 스프린트(§19)다.

## 18. Executive Label Cleanup + AI Query Rebalance + Telegram Rollout (P0-C1.10)

P0-C1.9의 IA 위에 **임원 친화 언어 정리 + AI 편중 수집 + 채널→1:1 봇 진입**을 얹는
폴리시 스프린트. 핵심 도메인(scoring/collector/db/insight 점수·등급)은 그대로다.

### 임원용 라벨 정리

| 이전 | 이후 | 이유 |
|---|---|---|
| AI 레이더 주요 신호 | **AI 관련** | "레이더 주요 신호"가 장황 — 섹션 제목/Telegram/대시보드 통일 |
| 주간 리포트 후보 / 주간 보고 후보 / 주간 모니터링 | **추적 필요** | 임원은 리포트 **독자**(작성자 아님). "긴급하진 않지만 계속 봐야 하는 신호" 뜻 |
| LIVE · 공개 RSS (헤더 배지) | **자동 수집** | 기술 용어 제거. live/mock 판별은 보이지 않는 HTML 마커로 |
| 헤더/footer "공개 RSS 수집" | **자동 수집** | 임원 화면에 수집 채널 기술 표기를 두지 않음 |

- **공개 등급 체계(4단계):** 즉시 확인 · 검토 필요 · **추적 필요** · 참고.
  내부 등급 상수 `scoring.GRADE_WEEKLY = "추적 필요"`로 통일 — 현황판·대시보드 배지·
  Telegram·점수대 라벨이 모두 같은 표현을 쓴다(이중 관리 없음).
- **데이터 정직성은 그대로다.** `news_data_mode`/`news_source`/`data_warning` 등 내부
  provenance와 JSON 필드, 검증기 로직은 유지한다. 사용자 화면에서 `LIVE`·`공개 RSS`
  기술 표기만 빼고, live/mock 구분은 본문 상단 **보이지 않는 주석 마커**
  (`<!--news-data-mode:live-->` / `mock`)로 한다 → 검증기/CI가 결정적으로 판별한다.
- 시장지표는 여전히 **미연동**(가짜 수치 없음) — 거시 섹션/footer에 정직하게 표기.

### 생성 요약은 명사형 메모 스타일

임원 메모처럼 읽히게 **종결어미를 쓰지 않는다**(`~입니다/합니다/됩니다/있습니다/신호다/
필요하다/예상된다/감지됩니다` 금지). 기사 제목(raw 원문)과 footer 고지문은 대상이 아니다.

- 적용: `executive_one_liner`, 각 신호의 `one_line_reason`(= `insight` implication 또는
  `radar` risk_reason). 예) "AI 데이터센터 전력·냉각 인프라 수요 확대", "중대재해·안전
  규제 대응 — 입찰 자격·평판 리스크 점검 대상".
- 검증: `verify_executive_ia_polish.py`가 brief의 생성 요약/사유 줄을 스캔한다.

### AI 편중 live 수집 (`data/live_news_sources.json`)

- `max_per_query=5`, `max_total=70`, 쿼리 26개로 재조정 — **AI 관련 ≥ 12** (데이터센터·
  전력·냉각·SMR·스마트건설·건설 로봇·현대건설 AI), 리스크/규제 ≥ 5, 거시 단독 ≤ 2.
- provider는 `google_news_rss` 고정(무인증 공개 RSS), X(엑스) 쿼리 0건.
- 검증: `verify_live_news_ingestion.py`가 분류 카운트를 결정적으로 확인한다.

### Telegram Executive Rollout (채널 + 1:1 봇 진입)

**권장 운영 패턴 — 비공개 채널로 공식 브리프 전달 + 1:1 봇으로 개인 후속 질의.**

1. 비공개 Telegram 채널 생성 (예: `HDEC Executive Radar`).
2. 봇을 채널 관리자로 추가 (가능하면 "메시지 게시" 권한만).
3. 임원을 채널에 초대 — 임원은 **채널 참여만** 하면 된다(개인 chat id 공유 불필요).
4. GitHub 설정 (Secrets/Variables):
   - `TELEGRAM_CHAT_IDS` = 비공개 채널 chat id 또는 채널 username
   - `TELEGRAM_BOT_TOKEN` = 봇 토큰
   - `DASHBOARD_URL` = Pages 요약 대시보드 URL ("대시보드 보기" 버튼, 선택)
   - `REPORT_URL` = Pages 리포트 URL ("상세 리포트 보기" 버튼)
   - `TELEGRAM_BOT_USERNAME` **또는** `TELEGRAM_PERSONAL_BOT_URL` = 1:1 봇 deep link 대상
5. 채널 메시지 버튼:
   - **대시보드 보기** → 정적 대시보드(`DASHBOARD_URL`, 없으면 `REPORT_URL`에서 파생)
   - **상세 리포트 보기** → 정적 리포트(`REPORT_URL`)
   - **개인 질의하기** → 봇 1:1 대화창 deep link `https://t.me/<bot_username>?start=ask_today`
6. 현재 봇 username 예: `hdec_executive_rader_bot` (철자가 `rader`인 점에 주의 — 실제
   username을 그대로 쓴다). 미설정 시 "개인 질의하기" 버튼은 **안전하게 생략**된다.
7. 개인 chat_id DM 모드도 가능하지만 권장 기본 경로는 아니다 — 임원마다 봇을 먼저
   start 해야 하고 chat_id를 수집해야 해서 비공개 채널보다 운영이 취약하다.

> **정직성:** "개인 질의하기"는 1:1 대화창 **진입**(deep link) 버튼이다. 초기 단계에서는
> 1:1 대화창 진입/명령어 UX 계약이며, 실제 **자연어 질의 응답은 P1 webhook/polling 구현
> 후 활성화**된다. webhook을 구현·검증하기 전에는 "1:1 질의 응답 가능"을 주장하지 않는다.

- **P0 (현재):** 채널 브리프 + 개인 봇 deep-link 진입 버튼.
- **P1 (이후):** inbound 1:1 봇 명령(`/start ask_today`, `/today`, `/ai`, `/risk`,
  `/macro`, `/help`)과 자연어 질의 응답 — `ENABLE_TELEGRAM_WEBHOOK=false` 기본,
  `TELEGRAM_WEBHOOK_SECRET` 필요, 토큰/업데이트 본문 미로깅, production `setWebhook`
  자동 실행 없음. 안전한 배포 대상이 확정되기 전까지 구현하지 않는다.

```bash
# 버튼 payload 미리보기 (발송/비밀값 없음 — 검증/문서용)
TELEGRAM_BOT_USERNAME=hdec_executive_rader_bot REPORT_URL=https://example.com/daily/latest.html \
  DASHBOARD_URL=https://example.com/daily/dashboard-latest.html \
  python3 scripts/send_telegram.py --dry-run-payload "test"

# 라벨/쿼리/봇 진입 회귀 검증 (네트워크 없이 결정적 — RESULT: PASS / exit 0)
python3 scripts/verify_telegram_channel_to_personal_entry.py
python3 scripts/verify_executive_ia_polish.py
```

### Executive Telegram Preferences (D6-C foundation)

Executive preferences are a **personal receiving/filtering layer**, not operator
settings. They live in `data/executive_preferences.json` and are loaded through
`app/executive_preferences.py`.

- Schema: `chat_id`, `user_label`, `lens_preferences`, `delivery_mode`,
  `created_at`, `updated_at`.
- Default behavior: an unknown `chat_id` gets `delivery_mode="all"` with empty
  lens lists, meaning no personal filter is applied yet.
- Scope boundary: preferences may later filter/reroute an individual recipient's
  Telegram delivery, but they do **not** alter `app/topic_profiles.py`, live
  search queries, business lens catalogs, scoring, source rules, or operator
  settings.
- Safety: malformed/missing preference JSON loads as an empty store, so callers
  fall back to `default_preference(chat_id)`. The preference module does not read
  secrets or env files.
- Future work: a `/settings` command can write this store only after inbound bot
  handling is explicitly implemented and gated. It is not implemented yet.

```bash
python3 scripts/verify_executive_preferences.py
```

## 19. Live Article Quality Gate & Classifier Tuning (P0-C1.11)

AI 편중 수집(§18) 이후 임원용 분류 품질을 끌어올리는 결정적 품질 게이트. 외부 API·매크로·
inbound 봇은 건드리지 않는다. 정책 키워드는 `data/article_quality_rules.json` 한 곳이 단일
소스이고, 판정 로직은 새 leaf 모듈 `app/article_quality.py`(순수 함수, DB/네트워크 없음)가
소유한다 — scoring/radar/briefing이 소비만 한다.

### 주식 테마/증권 리서치성(stock-hype) 강등

- `app/article_quality.assess(source, title)`가 **제목·출처만** 보고 결정적으로 판정한다
  (500자 본문은 보지 않는다 — '수혜/급등'이 누적돼 정상 기사가 오강등되는 것을 막는다).
- strong 지표(머니무브·테마주·목표가·파운드리 거물·증권가 등)는 단독으로, weak 지표
  (수혜·급등·성장 기대 등)는 **2개 이상**일 때만 stock-hype로 본다 → 신뢰 매체의 광범위
  산업 기사가 시장 용어 1개 때문에 강등되지 않는다.
- `리서치알음` 등 증권 리서치성 출처는 출처만으로 강등한다.
- bare `주가`는 `발주가/수주가`(정상 건설 기사)에 substring으로 걸리므로 **쓰지 않는다**
  (`주가 급등`/`목표주가` 같은 명시 구절만). verifier가 이 오탐을 회귀 검사한다.
- 현대건설 직접 언급(제목) 기사는 stock-hype 강등에서 제외한다(아래 보호 로직이 따로 다룸).
- 효과: `scoring`이 stock-hype를 **2.4점 캡 + 제외 등급**으로 강등 → AI 관련 Top·신규 이슈·
  즉시 후보·수주·해외·리스크·규제 어디에도 안 들어가고, `radar`도 `other`로 라우팅해 레이더에서
  뺀다. 참고/제외 감사 섹션에만 투명하게 남는다. (데일리머니 SMR·일진파워 리서치알음 회귀)

### 리스크/규제 분류 강화

- `app/radar.py`가 실제 **risk-action 키워드**(중대재해·사망사고·벌점·영업정지·입찰제한·
  과징금·특별감독·사전통보 등 `RISK_ACTION_STRONG`)가 있을 때만 단독 리스크로 본다.
- 약한 규제 신호(제재·처벌·조사·규제·시행령 등 `RISK_REG_WEAK`)는 **산업 anchor**가 함께
  있을 때만 리스크. `국토부/고용노동부/정부/안전/점검` 단독으로는 더 이상 리스크가 아니다.
- 효과: "국토부, 건설산업 대전환 이끌 혁신기술 발굴한다"는 더 이상 `risk_regulation`이
  아니다(혁신/정책/스마트건설). `_SEVERITY_FLOOR`도 부처명·'안전' 단독 floor를 제거했다.

### 현대건설 직접 관련성 보호

- 현대건설 + AI + 계약/하도급/협력사/상생펀드/불공정 → `hdec_ai_contract`:
  `radar`가 **ai**로 라우팅, `scoring`이 **최소 '추적 필요'**로 floor(제외 금지).
- 현대건설 + 벌점/사전통보/제재/영업정지/입찰제한 → `hdec_enforcement`:
  `radar`가 **risk_regulation**으로 라우팅(+`risk_priority_score`), `scoring`이 최소
  '추적 필요'(고심각 제재는 '검토 필요')로 floor. (서울시 벌점 사전통보 회귀)
- 단, 모든 현대건설 기사를 무조건 승격하지 않는다 — AI/리스크 관련성이 있을 때만.

### 집계 호스트 출처 표시 정규화

- `app/source_quality.normalize_display_source()`가 `v.daum.net`·`n.news.naver.com`·
  `news.google.com` 등 집계/재전송 호스트를 **`Daum 경유`/`Naver 경유`/`Google News 경유`**
  (그 외 호스트형은 `원문 경유`)로 정규화한다. 정상 매체명(연합뉴스 등)은 그대로 둔다.
- `briefing`/`main`이 entry에 `display_source`를 부착하고, 정적 리포트·대시보드·드릴다운·감사
  카드가 이를 우선 노출한다. **원문 URL(href)은 그대로 보존** — 신뢰 매체처럼 보이지 않게
  표시 라벨만 바꾼다.

### mock 영향 없음

- mock 데모 숫자 불변: **28 수집·분석 / 21 신호 / 즉시 3 / 검토 4 / 추적 14 / 참고/제외 7**.
- mock의 유일한 stock-hype 기사(테마주·증권가)는 이미 제외 등급이라 카운트가 바뀌지 않는다.

### 로컬 실행 (검증은 네트워크·비밀값 불필요)

```bash
# 결정적 회귀 검증 (fixture 기반, RESULT: PASS / exit 0)
python3 scripts/verify_live_article_quality_gate.py

# 수동 라이브 품질 감사 (운영자용, 읽기 전용 · DB 쓰기/commit 없음 · 라이브 네트워크 필요)
NEWS_MODE=live python3 scripts/audit_live_article_quality.py \
  --output /tmp/hdec_article_quality_audit.md
```

`audit_live_article_quality.py`는 brief를 마크다운 표로 떨어뜨리고 의심 행(stock-hype·집계
호스트·현대건설인데 제외·리스크 오분류)을 모아 수동 점검을 돕는다. **검증기가 아니다**
(라이브 결과는 PASS/FAIL을 내지 않는다). 라이브 Google News RSS는 주기적 쿼리/출처 튜닝이
계속 필요하다 — 이 게이트는 알려진 오탐을 줄일 뿐 완벽을 주장하지 않는다.

## 20. Executive Decision Relevance Reframe (P0-C1.12)

제품 목표를 **'AI 뉴스 수집'에서 '현대건설 임원 의사결정 레이더'로** 재정렬한다(AI-우선
강조는 유지). 분류/티어 로직은 새 leaf 모듈 `app/decision_relevance.py`(순수 함수,
DB/네트워크 없음)가 `radar`/`article_quality` 위에 얹혀 소유한다 — briefing/scoring이
소비만 한다. **생성된 사유/카테고리 라벨('현대건설 직접 연관성 낮음' 등)을 분류 입력으로
쓰지 않는다** — raw 제목/출처/스니펫/토픽만 본다(self-fulfilling 오탐 방지).

### 의사결정 관련성 레이어

- `decision_relevance.classify(row)`가 기사를 임원 섹션 멤버십(primary + secondary)으로
  나누고, 의사결정 관련성 **점수(0~5)·티어(A/A-/B+/B/B-/C/exclude)·사유**를 부여한다.
- 임원 섹션: **현대건설 직접 영향 / AI 관련 / 수주·해외 / 리스크·규제 / 경쟁사·공급망 /
  거시경제 / 기타**. 같은 기사가 여러 섹션에 멤버로 들어갈 수 있다(multi-section).
- 상단 항목을 AI 관련성만이 아니라 **임원 유용성**으로 고른다. stock-hype/증권 리서치성은
  exclude(§19 유지), 리스크·규제 품질 게이트도 그대로 유지된다.

### 현대건설 직접 영향 (신규 최상위 섹션)

- 현대건설/현대ENG/HMG건설기술연구원이 주체이고 전략/사업/조직/기술/리스크 신호가 있는
  기사 → `hdec_direct`. 리포트·대시보드에서 **AI보다 먼저** 노출한다.
- 정렬: 리스크/제재 → 수주·DC·SMR·뉴에너지 전략 → AI·계약 → R&D·조직 → 그 외 직접.
- 벌점/제재는 primary `리스크·규제` + secondary `현대건설 직접`(둘 다 노출).
- **무차별 승격 금지**: 헬스케어/생활성 현대건설 기사는 `is_hdec_strategic`이 걸러낸다.

### 수주·해외 broadening + 경쟁사·공급망 (신규)

- 수주·해외를 확정 계약뿐 아니라 **발주 환경**(중동·재건·사우디·네옴·EPC·플랜트·LNG·원전·
  SMR·데이터센터·글로벌 수주)까지 넓혀 'business 0건' 회귀를 막는다. 현대건설 직접(primary)은
  현대건설 섹션에 이미 노출되므로, 수주·해외에선 순수 발주/경쟁사 수주 신호를 먼저 보인다.
- `scoring`에 등급 floor 추가: 현대건설 전략(`is_hdec_strategic`)·신뢰 출처의 발주 환경
  (`is_order_environment`, 섹터/지역 신호 요구)은 **최소 '추적 필요'**로 surface(제외 금지).
  섹터/지역 신호를 요구하므로 '주택 착공 통계' 같은 generic 건설 기사는 floor되지 않는다.
- 경쟁사·공급망: 삼성물산·GS건설·SK에코플랜트·DL이앤씨 등 경쟁 건설사 + 전선·버스덕트·
  변압기 등 구별되는 전력 공급망 신호.

### Telegram 정리 + Top 다양성

- 구성: `[현대건설 직접]` → `[AI 관련]` → `[수주·해외]` → `[리스크·규제]` → 버튼.
- 같은 회사/공급사(예: 가온전선 2건)가 Top을 도배하지 않도록 회사 단위 dedup.
- **Macro Snapshot/'시장지표 미연동' placeholder 제거** — 거시경제는 리포트로 위임한다
  (live 시장지표가 연동될 때만 노출). 가짜 macro 수치 금지(§13)는 그대로 유지된다.

### mock 영향 없음 / 검증

- mock 데모 숫자 불변: **28 / 21 / 3 / 4 / 14 / 7**. 등급 floor는 WEEKLY까지만 올리고
  섹터/지역·현대건설 전략으로 스코프돼 mock의 제외 7건을 건드리지 않는다(연합뉴스 '주택
  착공 감소'는 floor 안 됨).
- `scripts/verify_executive_decision_relevance.py`(fixture 13 시나리오) + 기존 13개 = **14/14 PASS**.

```bash
python3 scripts/verify_executive_decision_relevance.py   # 결정적 회귀 (RESULT: PASS)
# 수동 라이브 의사결정 관련성 감사 (읽기 전용 · 라이브 네트워크 필요)
NEWS_MODE=live python3 scripts/audit_live_article_quality.py \
  --output /tmp/hdec_decision_relevance_audit.md
```

라이브 Google News RSS는 주기적 쿼리/출처 튜닝이 계속 필요하다 — 이 레이어는 제품을
현대건설 임원 의사결정 지원에 더 가깝게 옮길 뿐 라이브 피드의 완벽을 주장하지 않는다.

## 21. Executive Telegram & Section Routing Polish (P0-C1.13)

> **D7-AD 표시 계약:** 아래 항목의 selection/routing 규칙은 유지하지만 Telegram 표시는
> `app/executive_digest.py`의 결론형 4문장 + 핵심 링크 1~3개로 대체됐다. 과거의 현황판·점수·
> 섹션별 제목 나열은 더 이상 채널 본문에 렌더하지 않는다.

§20의 의사결정 레이어 위에서, Telegram 다이제스트가 'AI 뉴스 요약'이 아니라 **현대건설
임원 의사결정 브리프**로 보이게 라우팅·표현을 다듬는다. 변경은 Telegram 도메인
(`build_telegram_digest.py`)과 `decision_relevance` 라우팅에 한정한다(스코어/등급/외부 API
불변, stock-hype·리스크 게이트 불변).

### 현대건설 직접 — implication 그룹핑

- `[현대건설 직접]`을 헤드라인 5줄 나열이 아니라 **리스크/전략/운영/(기술·조직)/재무**
  implication별로 묶어 **≤3줄** 메모형으로 보여준다(`_hdec_grouped_bullets`,
  `hdec_bucket` 1:1 매핑). 한 줄에 대표 제목 최대 2건.
- 벌점·전략(도시정비·DC·SMR)·운영(AI 하도급·계약)이 동시에 있으면 세 implication이 모두
  대표된다. 신호가 하나면 한 줄.

### 재무·자금조달 라우팅 (AI 오분류 교정)

- `전환사채/회사채/금리/유동성/PF/자금조달/투자자/신용등급` 등 → **재무·자금조달**로 본다.
  현대건설 + 재무는 primary `현대건설 직접 영향` + secondary `거시경제`, 사유 '자금조달·금리
  환경'. **AI Top에 넣지 않는다**(투자자/현대건설 언급만으로 AI 금지).
- 단, 명시적 AI/데이터센터/SMR/EPC/수주 전략 맥락이 함께 있으면 AI/전략으로 둔다.
- 분류 입력은 **raw 제목+스니펫만** — 생성된 `topic_candidates`(캔드 토픽 '현대건설
  데이터센터'를 느슨하게 매칭)는 재무 기사를 DC로 오인시키므로 제외한다.

### AI Top·수주·해외 — 공급사 후순위 + 발주 우선

- 부품·전선 공급사 단독(가온전선·솔루엠·성광벤드 등, `is_supplier_only`)은 더 강한
  AI/EPC/현대건설 신호가 있으면 **AI Top에서 뒤로** 정렬한다. 공급사는 경쟁사·공급망/수주·해외
  보조로 남는다(임원 AI Top을 공급사가 차지하지 않게).
- Telegram에 `[수주·해외]` 블록(≤2줄)을 추가 — 발주/EPC/프로젝트를 공급사 단독보다 먼저.
  현대건설 직접에 이미 나온 주체는 키 선점으로 중복 제외.

### 노이즈/중복 정리

- `[주요 테마]`를 Telegram에서 **제거**(부풀어 보이는 '40건' 카운트 = 임원용 노이즈).
  테마는 리포트/대시보드/감사 헬퍼에 유지된다.
- `[AI 관련 Top 3]`(리포트형) → `[AI 관련]`(간결).
- 현대건설 벌점이 `[현대건설 직접]`에 이미 나오면 `[리스크·규제]`는 **헤드라인을 반복하지
  않고** 다음 리스크 또는 '직접 영향 항목 참고' 포인터로 대체한다.
- 감사 헬퍼에 'AI 후보인데 재무·자금조달 신호(AI→재무 라우팅 점검)' 섹션 추가.

### mock 영향 없음 / 검증

- mock 데모 숫자 불변: **28 / 21 / 3 / 4 / 14 / 7**(라우팅은 섹션 멤버십만 바꾸고 등급을
  재계산하지 않는다).
- `scripts/verify_executive_telegram_polish.py`(fixture, temp DB 시뮬) + 기존 14개 = **15/15 PASS**.

```bash
python3 scripts/verify_executive_telegram_polish.py   # 결정적 회귀 (RESULT: PASS)
NEWS_MODE=live python3 scripts/build_telegram_digest.py --dry-run   # 라이브 다이제스트 확인
```

## 22. Final Live Routing Cleanup (P0-C1.14)

> D7-AD 이후 `[수주·해외]` 후보는 selection 데이터와 상세 리포트에 유지된다. Telegram의
> 핵심 링크 3개가 현대건설·AI·리스크로 채워진 날에는 링크 cap 때문에 본문에서 생략될 수 있다.

§21의 라이브 점검에서 남은 라우팅 갭 3개를 닫는다. 제품 원칙은 **"AI 뉴스 수집기가 아니라
현대건설 임원 의사결정 레이더"** — 분류는 "이 신호가 수주/해외/리스크/재무/기술/경쟁사/
공급망/거시 의사결정에 도움이 되는가"에 답해야 한다. 오늘 제목에 과적합하지 않고 **카테고리
규칙과 그 이유**를 구현한다. 변경은 `decision_relevance`/`briefing`/Telegram/감사에 한정한다.

### A. 수주·해외 Telegram 블록 — 항상 노출

- `order_class`(0 경쟁사·EPC/DC/SMR 발주 > 1 해외·중동·재건 환경 > 2 현대건설 직접 발주 >
  3 공급사 단독)로 정렬한다. 발주/EPC/해외를 공급사 단독보다 먼저.
- 현대건설 회사 키 선점이 블록을 **통째로 지우지 않게** AI Top용 선점과 분리한다 — 수주·해외는
  `[현대건설 직접]`에 **이미 노출된 같은 기사(article_id)**만 중복 제외하고, 후보가 모두 직접에
  나왔으면 헤드라인 대신 포인터만 둔다.

### B. 재무·자금조달 하드 오버라이드

- raw 제목+스니펫이 재무 신호인데 전략 맥락이 없으면 `override_radar_section`이 AI radar_section을
  **거시(MACRO)로 되돌린다**(`radar.classify_section`은 불변 — 게이트 verifier/스코어 floor 호환).
- 결과: 현대건설 전환사채 기사가 `ai_radar_signals`/AI Top/신규이슈·즉시 신호에 **'AI'로 남지
  않고** 현대건설 직접 + 거시로 라우팅된다. 생성 `topic_candidates`는 오버라이드 입력에서 제외.
- 단, 명시적 데이터센터/사업/SMR/EPC 전략이면 현대건설 전략(AI)으로 유지한다.

### C. 공급사 단독 — 클래스 단위 후순위

- 공급사 단독은 더 강한 비공급사 AI/EPC 신호가 cap 안에 들도록 `ai_radar_signals`에서 **뒤로
  정렬**한다(briefing). Telegram AI Top은 **클래스 단위 dedup**(`_dedup_key`)으로 서로 다른
  전선/버스덕트 회사라도 1슬롯만 — 공급사 도배 차단. 공급사는 **경쟁사·공급망**에 그대로 남는다.

### D. 감사 0건 + 검증

- 감사 헬퍼의 'AI 섹션에 남은 재무 신호'는 오버라이드를 거쳐 **0건(통과)**이어야 한다.
- mock 데모 숫자 불변: **28 / 21 / 3 / 4 / 14 / 7**(섹션 멤버십만 바뀜, 등급 재계산 없음).
- `scripts/verify_executive_final_live_routing.py`(fixture, temp DB 시뮬 + 감사 마크다운) +
  기존 15개 = **16/16 PASS**.

```bash
python3 scripts/verify_executive_final_live_routing.py   # 결정적 회귀 (RESULT: PASS)
NEWS_MODE=live python3 scripts/audit_live_article_quality.py --output /tmp/audit.md   # 재무 0건 확인
```

## 23. 다음 스프린트 — P0-C2 Real Macro Snapshot Integration

시장지표(거시) 실시간 연동. 반드시 다음을 만족해야 한다:

- `source`(출처)와 `updated_at`(기준 시각)을 항상 제공한다.
- 데이터가 오래되면 `stale` 플래그를 세운다.
- 조회 실패 시 가짜 fallback 값을 만들지 않고 `unavailable`로 강등한다.
- `app/macro_snapshot.py`의 `get_macro_snapshot()` 경계를 통해서만 들어온다.
- `macro_data_mode=live`일 때만 표시 레이어가 수치를 렌더링한다 (§13 데이터 정직성 유지).

## 24. 운영 핸드오프 문서 (Operations)

이 제품을 임원용 뉴스 센서 MVP로 **매일 운영·시연·인계**하기 위한 문서는
`docs/operations/`에 모았다(코드 변경 아님 — 운영 절차서).

- **[Executive Radar 운영 런북](docs/operations/EXECUTIVE_RADAR_RUNBOOK.md)** —
  매일 운영 흐름, 전체/빠른 검증 게이트, 공개 리포트 캐시버스트 검증, 실패 트리아지,
  절대 깨면 안 되는 제품 원칙.
- **[임원 데모 스크립트](docs/operations/EXECUTIVE_DEMO_SCRIPT.md)** —
  5–7분 시연 대본(섹션 설명·신뢰성·과대주장 금지·임원 토크 트랙).
- **[다음 운영자 핸드오프](docs/operations/NEXT_OPERATOR_HANDOFF.md)** —
  기준 커밋, D3A–D3I 완료 내역, 게이트 팩, stale 복구, 과거 실수 반복 금지, 다음 권장 작업.
- **[RC 봉인 — MVP 운영 증거 패킷 (P0-D3J)](docs/operations/RC_SEAL_D3J.md)** —
  1일 운영 리허설(live 빌드·로컬/공개 검증·다이제스트 후보·발송 게이트·전체 회귀) 실측 증거,
  실제 발송 0건 확인, 남은 리스크.
- **[D7-AD Gmail/Teams 알림 + Operator API activation](docs/operations/D7AD_EMAIL_TEAMS_OPERATOR_ACTIVATION.md)** —
  GitHub Actions Gmail SMTP 수동 승인 발송, Teams 채널 이메일 수신정책 검증, Operator API
  배포 후보 비교와 activation checklist.
