# PROMPT — Claude Code에 그대로 붙여넣는 one-shot 지시문

아래 전체를 Claude Code 첫 메시지로 입력한다.
(전제: 저장소 루트에 CLAUDE.md, rules.md, PRD가 이미 존재)

---

너는 HDEC Executive Radar Day-1 Safe Slim MVP의 구현 에이전트다.
시작 전에 반드시 `CLAUDE.md` → `rules.md` → PRD 순서로 읽어라. 충돌 시 우선순위는 rules.md > CLAUDE.md > PRD다.

## GOAL (이것 하나가 완료 기준)

`APP_MODE=mock`, 인터넷 없음, API key 없음 상태에서 아래 flow가 한 번에 작동한다:

```text
Run Sensing
→ data/mock_articles.json 로드 (30건)
→ URL hash + normalized title dedup (≥20건 유지)
→ SQLite 저장
→ rule-based scoring (final_score 0~5, confidence 0~1, alert_grade)
→ template 기반 mock insight 생성
→ Today Signals 화면 표시 (Empty/Loading/Success/Error/No Candidates)
→ 즉시 알림 후보 최대 3건 표시 (final_score >= 4.5)
→ 운영자 Send 버튼 클릭 시에만 mock notification → notification_logs 저장
→ Feedback 버튼 9종 → feedback 테이블 저장
```

## 작업 방식

1. **P0-A만 먼저 완주한다.** P0-A 검증(rules.md §11)이 전부 통과하기 전에는 RSS/Naver/OpenAI/Teams 코드를 한 줄도 작성하지 마라. X API는 Day-1 전체 금지다.
2. **도메인 격리.** CLAUDE.md §4의 도메인-파일 소유 표를 따른다. 한 도메인을 수정할 때 소유 파일 밖을 건드리면 그 변경은 잘못된 것이다. main.py는 orchestration만, db.py만 sqlite3를 import한다.
3. **도메인 순서대로 작업한다 (CLAUDE.md §5):**
   D0 스캐폴딩 → D1 DB → D2 mock data → D3 collector → D4 scoring → D5 insight → D6 API → D7 notification+feedback → D8 UI → D9 통합 검증 → D10 디자인 polish 1회 + 재검증 → D11 README + 최종 보고.
4. **도메인별 자동 검수 루프 (필수).** 각 도메인 완료 직후:
   - rules.md §10의 해당 도메인 체크리스트를 실제로 실행하고 결과를 출력하라.
   - 금지어 grep 스캔을 실행하라.
   - `git diff --stat`으로 소유 파일 외 수정 여부를 확인하라.
   - 실수를 발견하면: 수정 → `.claude/skills/lessons/<도메인>-<요약>/SKILL.md`에 원인·재발 방지 규칙을 기록 → 재검수. 다음 도메인 시작 전 lessons 폴더를 먼저 읽어라.
   - 체크리스트 전부 통과 전에는 다음 도메인으로 넘어가지 마라.
5. **질문 금지.** 나에게 확인을 요청하지 마라. PRD 기본값을 따르고, 모호하면 더 단순하고 안전한 쪽을 선택하라(예: static HTML + Vanilla JS, 인증 없음, CORS localhost 최소).
6. **디자인은 P0-A 기능 검증 통과 후 단 1회 polish.** Today Signals 화면만, executive signal desk 톤으로. polish 후 P0-A 재검증을 다시 실행하라.
7. **P0-B는 P0-A PASS 이후 시간이 남을 때만.** 구현하더라도 `APP_MODE=mock` 경로를 절대 깨뜨리지 마라. 불안정하면 비활성화하고 P0-A 동작을 보존하라.

## 필수 산출 파일

```text
README.md  .env.example  .gitignore
app/main.py  app/db.py  app/collector.py  app/scoring.py
app/insight.py  app/notification.py  app/feedback.py  app/schema.sql
data/mock_articles.json  data/topics.json
templates/index.html
```

README에는 반드시 포함: 설치 명령, DB 초기화, mock mode 실행, 대시보드 URL(http://localhost:8000), curl 테스트 명령 전체, "P0-A는 인터넷 없이 동작", "X API는 Day-1에서 의도적으로 미구현".

## 하드 제약 요약 (전체는 rules.md)

- 금지 필드: raw_payload, body, content, full_text, article_body, full_rss_content
- snippet ≤500자, source_metadata_json 허용 키 5개만, thumbnail_url 금지
- article_insights에 alert_grade 중복 저장 금지
- keyword_rules는 seed/읽기 전용
- 자동 발송 금지: 운영자 Send 전 notification_logs에 sent 상태 0건이어야 함
- FastAPI `{article_id}` 표기, Express식 `:id` 금지
- API key/webhook URL 로그·프론트 노출 금지, .env commit 금지
- mock mode에서 외부 네트워크 호출 0건

## 최종 보고

모든 작업 완료 후, rules.md §12 "Final Completion Report Format"의 11개 항목을 그 순서 그대로 작성하라.
P0-A가 PASS가 아니면 완료를 주장하지 말고, 실패 항목과 블로커만 명확히 보고하고 중단하라.
