# RC Seal — Executive Radar Functional MVP (P0-D3J)

이 문서는 발표 자료가 아니라 **운영 증거 패킷(operating evidence packet)**이다.
HDEC Executive Radar MVP가 하루를 end-to-end로 실제 운영 가능한지 리허설하고 그 증거를 봉인한다.

- **기준 커밋(리허설 실행 시점)**: `c287701 fix: harden human review gate for alerts` (D3A–D3I 완료)
- **live 스냅샷 커밋**: `0469c31 chore: update live daily report for rc rehearsal`
- **리허설 일시**: 2026-06-18 14:28 KST (Thu)
- **실행자**: 로컬 운영 리허설 (네트워크 있음 · 비밀값 없음 · 실제 Telegram 발송 없음)
- **mock 데모 기준 숫자(불변)**: 수집·분석 28 / 신호 21 / 즉시 3 / 중요 4 / 관찰 14 / 참고·제외 7

> 모든 명령은 실제로 실행해 결과를 확인한 것이다. 추측/조작 결과는 없다.
> 실제 Telegram 발송은 **하지 않았다** — send 게이트는 자격증명 없음/미승인에서 멈춘다.

---

## 1. 무엇을 리허설했는가 (Scope)

| # | 리허설 항목 | 결과 |
|---|---|---|
| 1 | live 일일 리포트 빌드 (`news_data_mode=live`) | PASS |
| 2 | 생성 리포트 로컬 검증 (static / honesty / quality) | PASS |
| 3 | 공개 스냅샷 캐시버스트 검증 (HTTP 200 · live 마커) | PASS |
| 4 | Telegram 다이제스트 후보 생성 (발송 없음) | PASS |
| 5 | 발송 게이트 — 자격증명 없음 fail-fast | PASS |
| 6 | 발송 게이트 — manual 모드 비발송 | PASS |
| 7 | 발송 게이트 — send 모드 미승인 차단 | PASS |
| 8 | 전체 회귀 게이트 (py_compile + 12 verifier + diff) | PASS |

---

## 2. 단계별 증거 (Exact commands + evidence)

### Slice 0 — 베이스라인
```bash
git status --short --branch          # ## main...origin/main (clean)
git log --oneline -1                 # c287701 (HEAD at rehearsal start)
python3 -m py_compile scripts/*.py app/*.py      # PASS
python3 scripts/verify_human_review_gate.py      # RESULT: PASS
python3 scripts/verify_telegram_digest.py        # RESULT: PASS
python3 scripts/verify_static_report.py          # RESULT: PASS
git diff --check                                 # CLEAN
```
`.claude/settings.local.json`는 추적/스테이지하지 않았다(작업 내내 미접촉).

### Slice 1 — live 리포트 빌드 + 로컬 검증
```bash
APP_MODE=live NEWS_MODE=live MACRO_MODE=live NAVER_NEWS_ENABLED=false \
  python3 scripts/build_static_report.py --output docs/daily/latest.html
# -> report written: docs/daily/latest.html (106801 chars) news_data_mode=live

python3 scripts/verify_static_report.py            # PASS
python3 scripts/verify_data_source_honesty.py      # PASS
python3 scripts/verify_live_article_quality_gate.py # PASS
git diff --check                                   # CLEAN
```
로컬 HTML 마커(실측): `news-data-mode:live`=1 · `운영자 점검`=3 · `노출 품질`=3 ·
`중복 제어`=3 · `현대건설`=73 · `AI 관련`=3 · `리스크`=31 · generic 문구
"수주 경쟁력·시장 포지션 영향권"=**0**(부재가 정상). 빌드 결과는 live 수집 성공이라
`0469c31`로 별도 커밋했다(가짜 live 게시 아님).

### Slice 2 — 공개 스냅샷 캐시버스트 검증
```bash
COMMIT="$(git rev-parse --short HEAD)"
curl -L -H "Cache-Control: no-cache" -H "Pragma: no-cache" -D headers.txt \
  "https://guides.playground-aidesignlab.co.kr/HDEC-News-Sensor/daily/latest.html?v=${COMMIT}-$(date +%s)" \
  -o public.html
```
실측: `HTTP/1.1 200 OK` · `Cache-Control: max-age=600` · `Age: 0`(캐시버스트 적중) ·
크기 123003 bytes · `news-data-mode:live`=1 · `운영자 점검`=3 · generic 문구=0.
이 검증은 **직전 게시본**을 대상으로 한 것이고, 이번 `0469c31` 스냅샷은 push 후
재검증한다(Pages CDN max-age=600 반영 지연 가능 → §4 참고).

### Slice 3 — Telegram 후보 + 발송 게이트 (실제 발송 없음)
```bash
python3 scripts/build_telegram_digest.py > candidate.txt   # 후보만 생성(발송 없음)
```
후보 실측: mock 데이터 기반 라벨 · 현황판 28·3·4·14·7 · 섹션[현대건설 연관/AI 관련/
수주·해외/리스크·규제/카테고리 요약] · 링크는 기사 href만 · token 모양 0건.

발송 게이트 3종(전부 오프라인 — POST 경로 미도달):
```bash
# A) 자격증명 없음
python3 scripts/send_telegram.py
#   -> rc=1 · stderr "ERROR: TELEGRAM_BOT_TOKEN is missing" · 발송됨 주장 없음

# B) manual(기본) + 가짜 자격증명
TELEGRAM_BOT_TOKEN=123456:ABC TELEGRAM_CHAT_IDS=111 python3 scripts/send_telegram.py
#   -> rc=0 · "Send mode: manual" · "send_allowed=false" · review_required · 발송하지 않음

# C) send 모드 + 가짜 자격증명 + 승인 없음
TELEGRAM_BOT_TOKEN=123456:ABC TELEGRAM_CHAT_IDS=111 TELEGRAM_SEND_MODE=send \
  python3 scripts/send_telegram.py
#   -> rc=2 · approval_required · 발송하지 않음
```
세 경우 모두 `delivered=` / `Send status: approved` / `실제 발송 진행` 흔적 **0건**.
승인+자격증명을 동시에 주는 실발송 경로는 **의도적으로 실행하지 않았다**(검증기가
소스 정적 검사로 게이트-우선-순서를 잠근다 → `verify_human_review_gate.py` F 섹션).

### Slice 4 — 전체 회귀 게이트
```bash
python3 -m py_compile scripts/*.py app/*.py
python3 scripts/verify_human_review_gate.py
python3 scripts/verify_telegram_digest.py
python3 scripts/verify_live_article_quality_gate.py
python3 scripts/verify_executive_surface_dedup_audit.py
python3 scripts/verify_executive_cluster_exposure.py
python3 scripts/verify_executive_top_exposure_quality.py
python3 scripts/verify_executive_reason_text_specificity.py
python3 scripts/verify_executive_topic_classification.py
python3 scripts/verify_executive_decision_relevance.py
python3 scripts/verify_executive_final_live_routing.py
python3 scripts/verify_static_report.py
python3 scripts/verify_data_source_honesty.py
git diff --check
```
결과: **py_compile + 12 verifier 전부 PASS · git diff --check CLEAN**. mock 불변
숫자 재확인: `{detected:28, immediate:3, daily:4, weekly:14, excluded:7}` · signals=21.

---

## 3. PASS/FAIL 요약 (RC Result)

- 전체: **PASS (RC 봉인 가능)**
- py_compile: PASS
- 12 verifier: 전부 PASS (human_review_gate · telegram_digest · live_article_quality_gate ·
  surface_dedup_audit · cluster_exposure · top_exposure_quality · reason_text_specificity ·
  topic_classification · decision_relevance · final_live_routing · static_report · data_source_honesty)
- `git diff --check`: CLEAN
- mock 불변 28/21/3/4/14/7: 유지
- **docs/daily/latest.html 변경?** 예 — live 재빌드로 변경, `0469c31`로 별도 커밋(검증 통과본).
- **실제 Telegram 발송 발생?** **아니오(No)** — 게이트가 자격증명/승인 단계에서 차단.

---

## 4. 남은 리스크 (Remaining Risks)

1. **첫 실제 승인 발송(GitHub Actions `approve_send=true`)은 직접 관찰한다.** 워크플로
   발송 경로는 로컬에서 실행 불가(비밀값/CI 필요)라 정적·오프라인으로만 검증됐다.
   첫 수동 승인 dispatch의 로그(`delivered=N, failed=0`)를 사람이 확인할 것.
2. **Naver 보조 provider는 자격증명 의존.** 로컬 리허설은 `NAVER_NEWS_ENABLED=false`였다.
   CI는 repo secrets로 주입하며, 없으면 정직하게 skip한다(가짜 성공 없음).
3. **공개 Pages 캐시 지연.** `Cache-Control: max-age=600` 관측 — 새 스냅샷이 즉시
   반영되지 않을 수 있다. 캐시버스트(`?v=<COMMIT>-<epoch>`)로 재요청해 확인한다(런북 §D).
4. **시맨틱 클러스터/라우팅 튜닝은 향후 유지보수 대상.** live 기사 분포가 바뀌면
   클러스터 캡·섹션 멤버십이 미세 조정될 수 있다(랭킹 도메인, 별도 스프린트).

---

## 5. 다음 권장 작업 (Next Recommended Work)

- **D3K / P1 — 발송 승인 이력 또는 사람 검토 큐 UI** — **MVP RC가 봉인된 뒤에만** 착수한다.
  notification 경계(승인 기록/큐)이지 랭킹/분류가 아니다(런북 §F·핸드오프 §7 원칙 유지).
- 착수 전: provider/수집/scoring/리포트 UI를 건드리지 않고 끝내는 것이 성공 기준이다.

---

> 봉인 근거: 위 8개 리허설 항목과 전체 회귀 게이트가 실측으로 PASS했고, 실제 발송은
> 발생하지 않았으며, mock 불변 숫자가 유지됐다. 관련 메모리: `hdec-radar-p0d3i-human-review-gate`,
> 운영 문서: `EXECUTIVE_RADAR_RUNBOOK.md` §G, `NEXT_OPERATOR_HANDOFF.md`.
