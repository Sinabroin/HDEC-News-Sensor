# Executive Radar — 운영 런북 (Operator Runbook)

HDEC Executive Radar를 **임원용 뉴스 센서 MVP**로 매일 운영하기 위한 절차서다.
대상 독자는 일일 운영자(운영 담당)다. 제품 로직 변경 문서가 아니라 **운영 절차**다.

- 기준 커밋: `5d5a543 fix: restore live article quality gate` (D3A–D3G 완료 상태)
- 공개 리포트 주소:
  `https://guides.playground-aidesignlab.co.kr/HDEC-News-Sensor/daily/latest.html`
- 자동 게시 워크플로: `.github/workflows/telegram-notify.yml`
  (예약 실행 `cron: "0 23 * * *"` = UTC 23:00 = **KST 08:00**)
- mock 데모 기준 숫자(불변): **수집·분석 28 / 신호 21 / 즉시 3 / 검토 4 / 추적 14 / 참고·제외 7**

> 이 문서의 모든 명령은 `5d5a543` 시점에서 실제 실행해 통과를 확인한 것이다.
> 명령이 실패하면 추측으로 메우지 말고 §E 실패 트리아지로 간다.

---

## A. 매일 운영 흐름 (Daily Operating Flow)

평상시 **리포트 게시**는 GitHub Actions가 자동으로 한다(KST 08:00 예약, 빈 메시지 실행 →
`NEWS_MODE=live` 리포트 생성 → live 수집 성공 시 `github-actions[bot]`이 main에 auto-commit).
단 **Telegram 다이제스트 발송은 자동이 아니다 (P0-D3I — 사람 검토 게이트)**: 예약/일반
실행은 발송 게이트가 `manual`로 닫혀 후보 다이제스트만 보류하고, 운영자가 **명시적으로
승인**할 때만 실제 발송된다(§G). 아래는 **운영자가 수동으로 갱신/점검**할 때의 절차다.

1. **최신 main 동기화**

   ```bash
   git fetch origin main
   git status --short --branch        # '## main...origin/main' (ahead/behind 0) 확인
   git pull --ff-only origin main
   ```

2. **작업 트리 청결 확인** (커밋되지 않은 변경이 없어야 한다 — `.claude/settings.local.json` 제외)

   ```bash
   git status --short
   ```

3. **live 리포트 생성** (공개 RSS만 호출 · 비밀값 불필요 · Naver는 로컬에서 끔)

   ```bash
   APP_MODE=live NEWS_MODE=live MACRO_MODE=live NAVER_NEWS_ENABLED=false \
     python3 scripts/build_static_report.py --output docs/daily/latest.html
   ```

   - 출력 끝에 `news_data_mode=live`가 보여야 **live 수집 성공**이다.
   - `news_data_mode=mock`(또는 `news_fallback_used=true`)이면 live 수집이 실패/0건이라
     mock으로 정직하게 fallback한 것이다 → **그 결과를 live처럼 commit하지 않는다**
     (`git checkout -- docs/daily/latest.html`로 되돌리고 §E로 간다).
   - Naver 보조 provider는 자격증명이 있을 때만 CI에서 켠다(아래 참고). 로컬 수동
     갱신에서는 `NAVER_NEWS_ENABLED=false`가 안전 기본값이다.

4. **핵심 검증 게이트 실행** (§B 전체 게이트 또는 §C 빠른 게이트)

5. **변경분이 있을 때만 `docs/daily/latest.html` 커밋**

   ```bash
   git diff --stat -- docs/daily/latest.html      # 변경이 있을 때만 진행
   git add docs/daily/latest.html                 # ⚠ git add . 금지 — 파일 단위로만
   git commit -m "chore: update live daily report"
   ```

   - 변경이 없으면 커밋하지 않는다(자동 워크플로도 동일하게 skip한다).
   - `.claude/settings.local.json`은 **절대 stage하지 않는다**.

6. **푸시**

   ```bash
   git push origin main
   ```

   HTTPS 인증이 막히면 SSH로 폴백한다(§E의 GitHub 인증 항목).

7. **공개 캐시 무력화 검증** (§D)

---

## A-1. Watch Mode 긴급 신호 큐 (Review-Only)

Watch mode는 최신 기사에서 **지난 실행 이후 새로 보이는 임원 긴급 후보**를 감지해
로컬 JSON 검토 큐에 적재한다. 이 경로는 Telegram을 호출하지 않으며, 모든 큐 항목은
기본값이 `send_allowed=false`, `review_required=true`다. 실제 발송은 기존 §G의
사람 검토/`approve_send=true` 게이트를 통해서만 가능하다.

```bash
# 쓰기 없는 점검
APP_MODE=live NEWS_MODE=live MACRO_MODE=live NAVER_NEWS_ENABLED=false \
  python3 scripts/watch_urgent_signals.py --dry-run

# 상태/큐 기록 (기본 경로: data/watch_state.json)
APP_MODE=live NEWS_MODE=live MACRO_MODE=live NAVER_NEWS_ENABLED=false \
  python3 scripts/watch_urgent_signals.py --write

# 기계 판독 출력
python3 scripts/watch_urgent_signals.py --json
```

- 임시 상태로 리허설할 때는 `WATCH_STATE_PATH=/tmp/hdec_watch_state.json`을 지정한다.
- 큐는 신규 현대건설 직접 리스크, 직접 수주/프로젝트, 정책·규제 충격, 주요 경쟁사 수주
  움직임을 대상으로 한다.
- 주가/목표가/종목/수혜주, 분양·청약 홍보, 스포츠/선수 랭킹, generic roundup, 약한
  출처의 hype 기사는 긴급 큐에서 제외한다.
- 같은 기사 id/URL/제목 fingerprint 또는 이미 본 클러스터는 중복 알림하지 않는다. 단,
  새로운 심각 리스크 유형이나 다른 신뢰 출처의 severe 현대건설 리스크는 검토 후보로 남긴다.

검증:

```bash
python3 scripts/verify_watch_urgent_signals.py
```

---

## B. 전체 게이트 세트 (Full Gate Set)

릴리스/핸드오프/제품 로직 변경 후에는 **전체 게이트**를 돌린다.
모두 `RESULT: PASS` / exit 0 이 통과 조건이다. **11/11 PASS** 확인(D3I 발송 게이트 포함).

```bash
python3 -m py_compile scripts/*.py app/*.py
python3 scripts/verify_human_review_gate.py
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

> 이 게이트들은 네트워크 없이 결정적으로 돈다(fixture 기반 + temp DB 시뮬, 저장소
> `radar.db` 미접촉). 비밀값도 필요 없다. CI 워크플로도 발송 전에 mock 안전 모드로
> 동일 계열 게이트를 돌려 회귀 시 발송 자체를 차단한다.

---

## C. 빠른 일일 게이트 세트 (Fast Daily Gate Set)

평상시 아침 운영 점검용 최소 세트. `5d5a543` 기준 PASS 확인.

```bash
python3 scripts/verify_static_report.py
python3 scripts/verify_live_article_quality_gate.py
python3 scripts/verify_executive_final_live_routing.py
python3 scripts/verify_data_source_honesty.py
git diff --check
```

- `verify_static_report` — 리포트 HTML 구조·외부 리소스 0건·비밀값 0건.
- `verify_live_article_quality_gate` — stock-hype/집계 호스트/현대건설 보호 분류 가드.
- `verify_executive_final_live_routing` — 재무·공급사·수주 라우팅 + 감사 0건.
- `verify_data_source_honesty` — mock/live provenance·시장지표 정직성.

---

## D. 공개 리포트 검증 (Public Verification)

게시 후 GitHub Pages CDN 캐시 때문에 즉시 반영이 안 될 수 있다. **캐시 무력화**로 받는다.
`<COMMIT>`은 방금 푸시한 커밋 해시(예: `5d5a543`)를 넣는다.

```bash
curl -L -H "Cache-Control: no-cache" -H "Pragma: no-cache" \
  "https://guides.playground-aidesignlab.co.kr/HDEC-News-Sensor/daily/latest.html?v=<COMMIT>-$(date +%s)" \
  -o /tmp/hdec_public_latest.html
```

받은 페이지를 grep으로 점검한다(전부 기대대로여야 한다):

```bash
grep -c "news-data-mode:live" /tmp/hdec_public_latest.html   # 1 (live 게시 상태)
grep -c "운영자 점검"          /tmp/hdec_public_latest.html   # ≥1 (노출 품질·중복 제어 감사 섹션)
grep -c "AI 관련"             /tmp/hdec_public_latest.html   # ≥1 (AI 섹션 존재)
grep -c "리스크·규제"          /tmp/hdec_public_latest.html   # ≥1 (리스크 섹션 존재)
# 아래 generic 문구는 반드시 '없어야' 한다(있으면 D3B 사유 특화 회귀):
grep -c "수주 경쟁력·시장 포지션 영향권" /tmp/hdec_public_latest.html   # 0 (부재가 정상)
```

- `news-data-mode:live`가 **0**이면 공개본이 아직 mock 스냅샷이거나 캐시가 안 풀린 것이다.
- generic 문구 "수주 경쟁력·시장 포지션 영향권"이 **검출되면** 사유 특화(D3B) 회귀다 → §E.
- 캐시 지연이 의심되면 1–3분 후 `?v=` 값을 바꿔 재요청한다.

> 참고: GitHub Pages 무료 플랜은 **공개**다. 게시 대상은 공개 RSS 뉴스의 제목·요약·원문
> URL과 public-safe 점수 언어뿐이다. 비공개 본문·내부 민감 신호·운영자 메모·비밀값은
> 공개 Pages에 올리지 않는다(상세: `docs/daily/README.md`).

---

## E. 실패 트리아지 (Failure Triage)

| 증상 | 원인 분류 | 조치 |
|---|---|---|
| 빌드 끝에 `news_data_mode=mock`/`news_fallback_used=true` | **소스/네트워크 실패** — 공개 RSS 0건/타임아웃 | 가짜 live 게시 금지. `git checkout -- docs/daily/latest.html`로 복원. 잠시 후 재시도. 반복되면 `data/live_news_sources.json` 쿼리/출처 점검 |
| live 의도인데 mock으로 떨어짐 | **모드/fallback 불일치** | `NEWS_MODE=live`(+`MACRO_MODE=live`) env가 실제로 들어갔는지 확인. mock fallback은 버그가 아니라 정직성 설계다(§13) |
| 공개 페이지가 옛 내용 | **공개 캐시 지연** | §D의 `?v=<COMMIT>-$(date +%s)` 캐시 버스트로 재요청. Actions가 commit·push까지 끝났는지 `git log origin/main` 확인 |
| generic 사유 문구 검출 / AI Top에 재무·공급사 도배 | **오탐 라우팅 회귀** | 해당 verifier 재실행(`verify_executive_final_live_routing`, `verify_executive_reason_text_specificity`). 라이브 수동 감사: `NEWS_MODE=live python3 scripts/audit_live_article_quality.py --output /tmp/audit.md` |
| verifier만 FAIL인데 제품 출력은 정상 | **검증기 기대 stale** | 제품 버그인지 검증기 기대가 옛 동작에 묶였는지 먼저 구분(§F). stale이면 검증기를 **좁게** 갱신하고 이유 주석을 남긴다. 제품을 함부로 약화하지 않는다 |
| `verify_static_report` FAIL / 리포트가 예상과 다르게 변함 | **정적 리포트 예기치 변경** | `git diff -- docs/daily/latest.html`로 무엇이 바뀌었는지 확인. 의도치 않은 변경이면 되돌린다. 외부 리소스/비밀값이 들어갔는지 검사 |
| `git push` 실패 (HTTPS 인증) | **GitHub 인증 실패** | SSH 폴백: `GIT_SSH_COMMAND="ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new" git push git@github.com:Sinabroin/HDEC-News-Sensor.git main` |

---

## F. 절대 깨면 안 되는 제품 원칙 (Non-Negotiable Principles)

다음 운영자가 랭킹/분류를 손볼 때 반드시 지킨다. 위반은 D3A–D3G 회귀다.

1. **커버리지/리콜과 랭킹/정밀도를 분리한다.** 많이 모으는 것(coverage)과 임원에게
   먼저 보여줄 것을 고르는 것(precision)은 별개 단계다. 한쪽을 고치려고 다른 쪽을
   훼손하지 않는다.
2. **의심스러운 기사는 근거·감사 영역에 남길 수 있으나 임원 Top 노출을 지배하면 안 된다.**
   참고/제외·출처 품질 감사 섹션은 투명성용이다.
3. **집계 호스트(Daum/Naver/Google 경유)라는 이유만으로 정상 기사를 하드 제외하지 않는다.**
   표시 라벨만 'Daum 경유' 등으로 정규화하고 원문 URL은 보존한다. 특히 Daum 경유로
   라우팅된 **현대건설 직접 기사**를 제거하면 안 된다(D3G 회귀).
4. **테마 레이더 탭은 설계상 멀티렌즈다.** 같은 사안을 여러 관점에서 보여주는 구조이므로
   일괄(blanket) dedup하지 않는다. cross-surface dedup은 **항상 보이는 digest/top_new**가
   테마 카드를 반복하지 않게 하는 데만 적용한다.
5. **항상 보이는 digest/top_new는 테마 카드와 중복을 피한다.** 신규 이슈만 양보(cross-surface)한다.
6. **알림 전 사람 검토 게이트를 유지한다 (P0-D3I).** 임원 다이제스트는 운영자의 명시적
   승인 없이는 발송되지 않는다. `send_telegram.py`의 기본 모드는 `manual`(비발송)이고,
   실제 발송은 `TELEGRAM_SEND_MODE=send` + 운영자 승인(`REVIEW_APPROVED=true`) +
   자격증명이 **모두** 있을 때만 일어난다. 이 게이트(기본 비발송·승인 필수·자격증명 없으면
   발송 아님)를 제거하거나 기본값을 `send`로 바꾸지 않는다 — 위반은 D3I 회귀다(§G).

---

## G. 다이제스트 발송 — 사람 검토 게이트 (Manual Send Gate)

임원 다이제스트는 **기본적으로 발송되지 않는다.** 후보를 만들어 운영자가 검토하고,
명시적으로 승인할 때만 실제 Telegram 발송이 일어난다(human-on-the-loop).

- **기본(manual)·예약 실행**: 발송 게이트가 닫혀 있다. 워크플로의 예약/일반 실행은
  리포트만 게시하고 다이제스트는 **보류**한다(`Send status: review_required`).
- **자격증명이 없으면**: 모드와 무관하게 `TELEGRAM_BOT_TOKEN is missing`으로 즉시 실패한다
  — 절대 '발송됨'으로 위장하지 않는다.
- **후보 미리보기**(발송 없음, 로컬):

  ```bash
  python3 scripts/build_telegram_digest.py            # 후보 다이제스트 본문만 출력
  TELEGRAM_BOT_TOKEN=<set> TELEGRAM_CHAT_IDS=<set> \
    python3 scripts/send_telegram.py                  # manual → 검토 보류(발송 0건)
  ```

- **실제 발송**(운영자 승인): 세 조건이 모두 필요하다.

  1. GitHub Actions에서 워크플로를 **수동 실행**(`workflow_dispatch`)하고 입력
     `approve_send`에 `true`를 넣는다. 예약(cron)·빈 입력은 발송하지 않는다.
  2. (로컬 동등) `TELEGRAM_SEND_MODE=send` + `REVIEW_APPROVED=true`(또는 `CONFIRM_SEND=yes`)
     + 자격증명(`TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_IDS`)을 모두 설정한다.
  3. 승인 없이 `TELEGRAM_SEND_MODE=send`만 주면 `approval_required`로 막히고 발송하지 않는다.

- **검증**: `python3 scripts/verify_human_review_gate.py` (기본 비발송·승인 필수·자격증명
  없으면 발송 아님·예약 자동발송 차단을 모두 네트워크 없이 검사).

> 비밀값/토큰/chat id는 어떤 로그·리포트에도 출력하지 않는다. 위 명령의 `<set>`은
> 실제 비밀값을 셸에 직접 노출하지 말고 환경/secret store에서 주입한다.

---

## 부록 — 모드/환경 변수 빠른 참조

| 변수 | 기본값 | 의미 |
|---|---|---|
| `NEWS_MODE` | `mock` | `live`면 공개 RSS(Google News) 실수집. 실패 시 mock fallback(정직 표기) |
| `MACRO_MODE` | `mock` | `live`면 공개 시세 조회(무인증). 실패 시 `unavailable` 강등(가짜 값 금지) |
| `NAVER_NEWS_ENABLED` | off | 보조 provider. 자격증명(`NAVER_CLIENT_ID/SECRET`) 있을 때만 CI에서 켬. 로컬 수동 갱신은 `false` 권장 |
| `APP_MODE` | `mock` | P0-A 안전 기본값. 리포트 데이터 소싱은 `NEWS_MODE`/`MACRO_MODE`가 결정 |
| `TELEGRAM_SEND_MODE` | `manual` | 발송 게이트(P0-D3I). `send`일 때만 실제 발송 시도. `manual`/`dry_run`/빈 값은 비발송(후보 보류) |
| `REVIEW_APPROVED` / `CONFIRM_SEND` | (없음) | 운영자 승인 플래그. `send` 모드에서 `true`/`yes`일 때만 발송 허용 |
| `approve_send` (워크플로 입력) | `""` | `workflow_dispatch`에서 `true`로 줄 때만 CI가 발송. 예약(cron)·빈 입력은 비발송 |
| `WATCH_STATE_PATH` | `data/watch_state.json` | Watch mode 상태/긴급 검토 큐 JSON 경로. 테스트·리허설은 `/tmp/...` 사용 |

> 자동 워크플로(CI)는 `APP_MODE=mock` 환경에서 게시 step만 `NEWS_MODE=live`·`MACRO_MODE=live`로
> 켜고, repo secrets로 Naver를 안전 주입한다(값 미출력). 비밀값/토큰/chat id는 어떤 로그·리포트에도
> 출력하지 않는다. 자세한 게시·Pages 설정은 `docs/daily/README.md`와 README.md §12를 참조한다.
