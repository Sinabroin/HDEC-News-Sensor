# CLAUDE.md — HDEC Executive Radar (Day-1 Safe Slim MVP)

이 파일은 이 저장소에서 작업하는 Claude Code의 영구 컨텍스트다.
모든 작업 전에 이 파일과 `rules.md`를 먼저 읽고, 충돌 시 `rules.md`의 금지 규칙이 항상 우선한다.

---

## 1. 프로젝트 한 줄 정의

현대건설 임원진용 AI·거시경제·건설산업 뉴스 Sensing Agent의 **로컬 mock 기반 walking skeleton**.
Day-1 목표는 production이 아니라 "외부 API 없이 5분 데모가 한 번에 도는 것"이다.

## 2. 절대 우선순위

```text
P0-A 완주 → P0-A 검증 통과 → (시간 남으면) P0-B
```

- 기본 실행은 반드시 `APP_MODE=mock`.
- P0-A는 인터넷 없이, API key 없이 통과해야 한다.
- P0-A가 통과하기 전에는 RSS / Naver / OpenAI / Teams 코드를 작성하지 않는다.
- X API는 Day-1 전체에서 금지 (코드, env, 주석 내 토큰 로직 모두 금지).

P0-A 완료 flow (이 한 줄이 완료 기준):

```text
Run Sensing → mock_articles.json 로드 → dedup → SQLite 저장
→ rule-based scoring → mock insight → Today Signals 표시
→ 즉시 알림 후보(최대 3건) 표시 → 운영자 Send → mock notification log → feedback 저장
```

## 3. 기술 스택 (변경 금지)

| 영역 | 선택 |
|---|---|
| Backend | Python FastAPI |
| Frontend | static HTML + Vanilla JS (`templates/index.html`, FastAPI가 서빙) |
| Storage | SQLite (`app/schema.sql` 기준) |
| Scoring | P0-A rule-based only |
| Insight | P0-A template 기반 mock |
| Notification | P0-A mock/console only |
| Config | `.env` (commit 금지) + `.env.example` (commit 필수) |
| 실행 | 수동 Run Sensing only. 스케줄러 없음 |

## 4. 도메인 구조와 경계 (가장 중요한 코딩 원칙)

각 파일은 하나의 도메인을 **소유**한다. 다른 도메인의 책임을 절대 침범하지 않는다.
"A 도메인의 a 기능을 수정할 때 B 파일을 고치게 되는 구조"가 생기면 그 자체가 버그다.

| 도메인 | 소유 파일 | 책임 (이 파일만 한다) | 금지 (이 파일은 절대 안 한다) |
|---|---|---|---|
| DB | `app/db.py` | SQLite 연결, schema 초기화, 공용 CRUD 헬퍼. **sqlite3를 import하는 유일한 파일** | 비즈니스 로직, 점수 계산, HTTP |
| Collector | `app/collector.py` | mock_articles.json 로드, snippet 500자 절단, url_hash/normalized_title 생성, dedup, articles 저장 호출 | 점수 계산, insight, 알림 |
| Scoring | `app/scoring.py` | 9개 항목 점수, 가중합, bonus/penalty, clamp, alert_grade, scoring_reason/why_not_* 생성, article_scores 저장 호출 | 기사 수집, insight 텍스트, 발송 |
| Insight | `app/insight.py` | template 기반 mock insight, digest_message 생성, article_insights 저장 호출 | 점수 계산, alert_grade 재계산(중복 저장 금지) |
| Notification | `app/notification.py` | Send 처리, mock/console 발송, notification_logs 저장 호출 | 점수/insight 생성, 자동 발송 트리거 |
| Feedback | `app/feedback.py` | feedback row 저장 | scoring 가중치 변경 (Day-2 영역) |
| API | `app/main.py` | FastAPI 라우팅과 요청/응답 변환만. 각 도메인 함수를 **호출만** 한다 | 도메인 로직 inline 구현 |
| UI | `templates/index.html` | Today Signals 화면, Detail 패널, 5개 상태, fetch 호출 | 점수 계산 로직 복제, webhook URL 등 비밀값 |
| Data | `data/*.json` | mock 기사 30개, topics seed | 본문/full_text 포함 금지 |

도메인 간 통신 규칙:
- 도메인 → 도메인 직접 import 최소화. 데이터는 dict/dataclass로 전달.
- DB 접근은 반드시 `db.py`의 헬퍼를 통해서만 한다.
- `main.py`는 orchestration만 한다: `collector.run()` → `scoring.score_all()` → `insight.generate_all()` 순서로 호출하는 한 함수가 `/api/sense/run`의 전부다.

## 5. 작업 순서 (도메인 단위로 완결)

```text
D0  스캐폴딩: 폴더, .env.example, requirements.txt, schema.sql
D1  DB 도메인        → 검수 루프
D2  Data (mock 30개 + topics.json) → 검수 루프
D3  Collector (로드+dedup+저장)    → 검수 루프
D4  Scoring                         → 검수 루프
D5  Insight                         → 검수 루프
D6  API (7개 endpoint 전부)         → 검수 루프 (curl)
D7  Notification + Feedback         → 검수 루프
D8  UI (Today Signals, 5상태)       → 검수 루프
D9  P0-A 전체 통합 검증 (rules.md §검증)
D10 디자인 polish 1회 → P0-A 재검증
D11 README 최종화 → 최종 보고서
(이후에만 P0-B 검토)
```

각 도메인이 끝나기 전에는 다음 도메인을 시작하지 않는다.

## 6. 도메인별 자동 검수 루프 (필수)

각 도메인 Dn 완료 직후, 반드시 아래 루프를 실행한다:

```text
1. SELF-CHECK: 해당 도메인의 검수 체크리스트 실행 (rules.md §도메인 체크리스트)
2. FORBIDDEN-SCAN: grep으로 금지어 스캔 (rules.md §검증 명령)
3. BOUNDARY-CHECK: 이번 변경이 소유 파일 밖을 수정했는지 git diff로 확인
   - 소유 파일 외 수정이 있으면: 정당한 이유(인터페이스 변경)인지 검토, 아니면 되돌리고 재구현
4. 실수 발견 시: 수정 후 .claude/skills/lessons/ 아래에 skill 파일로 기록
5. 체크리스트 전부 통과해야 도메인 완료 선언
```

### 실수 기록 (skill) 형식

실수를 발견할 때마다 `.claude/skills/lessons/<도메인>-<요약>/SKILL.md` 생성:

```markdown
---
name: scoring-clamp-missing
description: scoring 도메인에서 final_score clamp 누락 시 적용. 점수 계산 코드를 수정할 때 항상 참조.
---
## 실수
final_score가 5.2로 저장됨 (clamp 누락)

## 원인
bonus 적용 후 clamp를 호출하지 않음

## 재발 방지 규칙
- 점수를 DB에 쓰기 직전 단일 함수 `clamp_score()`를 반드시 통과시킨다
- 검수 시 SELECT MAX(final_score), MIN(final_score)로 범위 확인
```

이후 도메인 작업 시 lessons 폴더를 먼저 훑고 시작한다.

## 7. 모호함 처리 원칙

- 사용자에게 질문하지 않는다. PRD 기본값을 따른다.
- PRD에 없으면 **더 단순하고 안전한 쪽**을 선택한다 (예: Jinja2 vs static HTML → static HTML, 인증 → 없음, CORS → localhost만).
- P0-B 관련 고민이 생기면 전부 보류하고 P0-A를 진행한다.

## 8. 최종 보고

완료 시 `rules.md` 맨 아래의 "Final Completion Report Format" 순서를 그대로 따라 보고한다.
P0-A가 실패 상태면 절대 완료를 주장하지 않고, 블로커를 명시하고 중단한다.
