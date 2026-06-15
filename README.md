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

- **수동**: Actions → **Telegram Notify** → Run workflow.
  `message` 입력을 비워 두면 mock daily digest를, 입력하면 그 메시지를 발송한다.
- **스케줄**: 매일 UTC 23:00 (KST 08:00)에 digest 자동 발송 (`cron: "0 23 * * *"`).
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

- **데일리 현황판** — 감지 신호 / 즉시 알림 후보 / 일간 요약 / 주간 리포트 후보 / 제외·참고
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
  단, 점수의 신선도 감점이 날짜에 따라 작동하므로 일간/주간 분포는 조금씩 변할 수 있다.
- **spread 지표는 추정치다** — topic 후보가 겹치는 신호 수 기반 휴리스틱이며,
  동일 사건 클러스터링이 아니다. dedup으로 제거된 중복 기사는 집계되지 않는다.
  라벨도 보수적으로 "토픽상 관련 추정 신호 n건"으로 표기한다.
- macro 지표는 **표시하지 않는다** — §13 Data Source Honesty 참조.

## 12. Static Report Page + Telegram Link Card (P0-B5)

Telegram은 **짧은 알림 진입점**, 시각적 일일 브리프는 **정적 HTML 리포트 페이지**가
담당한다. 다이제스트 메시지에 "**오늘 브리프 보기**" inline 버튼이 붙고,
버튼이 게시된 `docs/daily/latest.html`로 연결된다.

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

# 요약/기계 검증
python3 scripts/build_static_report.py --dry-run
python3 scripts/build_static_report.py --json

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
- repo **Variables**(권장) 또는 Secrets에 `REPORT_URL`이 있으면 메시지에
  "오늘 브리프 보기" 버튼이 붙는다. 로그에는 URL 값 대신
  `Report link enabled: true/false`만 출력된다.
- `REPORT_URL`이 없으면 기존 **텍스트 전용** 다이제스트를 발송하며 실패하지 않는다.

**REPORT_URL 등록 절차 (1회 수동 설정):**

1. GitHub 저장소 → **Settings** → **Secrets and variables** → **Actions**
2. **Variables** 탭 → **New repository variable**
3. Name: `REPORT_URL`
4. Value: `https://guides.playground-aidesignlab.co.kr/HDEC-News-Sensor/daily/latest.html`
   (Pages 게시 주소 — 커스텀 도메인 기준. 기본 도메인이면
   `https://<owner>.github.io/<repo>/daily/latest.html`)

### GitHub Pages 설정 (브랜치 게시 — 소스 설정만 1회 수동)

리포트 **commit/publish는 워크플로가 자동화**하지만(P0-C1.5), Pages **소스 설정**은 1회 수동이다:

Settings → **Pages** → Source: **Deploy from a branch** → Branch: **main** /
Folder: **/docs** → 게시 주소 예: `https://<owner>.github.io/<repo>/daily/latest.html`
→ 이 주소를 `REPORT_URL` Variable로 등록. 상세 절차는 `docs/daily/README.md`.

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
| 점수대 라벨 | 4.5+ 즉시 확인 · 3.5+ 검토 필요 · 2.0+ 주간 모니터링 · 그 미만 참고/제외 |
| **점수 구성요소** | 현대건설 관련성·사업기회·리스크/규제·긴급도·출처 신뢰도·반복/확산 신호 (각 0~5 막대) |
| **판정 신뢰도 NN%** | 옛 `confidence 0.90` 표기를 백분율로 (규칙 매칭 신뢰도) |
| **상대 강도 0~100** | 옛 `강도 30.7`을 대체. 가장 강한 테마=100 기준 상대 지표 (원시 가중합은 JSON에만) |
| **유사 주제 기사 n건 · 출처 m곳** | 옛 `토픽상 관련 추정 신호`를 대체. 제목·토픽 기준 참고 묶음(추정)이며 동일 사건 클러스터 확정값이 아님 |

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
    알림으로 띄우지 않고 "오늘은 즉시 확인급 신호 없음 · 주간 모니터링 후보 중심"으로 표기.

### mock 영향 없음

mock 데모 숫자(감지 28 / 즉시 3 / 일간 4 / 주간 14 / 제외 7 · 다이제스트 747자)는
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

## 16. 다음 스프린트 — P0-C2 Real Macro Snapshot Integration

시장지표(거시) 실시간 연동. 반드시 다음을 만족해야 한다:

- `source`(출처)와 `updated_at`(기준 시각)을 항상 제공한다.
- 데이터가 오래되면 `stale` 플래그를 세운다.
- 조회 실패 시 가짜 fallback 값을 만들지 않고 `unavailable`로 강등한다.
- `app/macro_snapshot.py`의 `get_macro_snapshot()` 경계를 통해서만 들어온다.
- `macro_data_mode=live`일 때만 표시 레이어가 수치를 렌더링한다 (§13 데이터 정직성 유지).
