# D7-P — 예약 라이브 갱신 + Telegram 정기 자동 발송 (계획)

> 상태: **구현됨** (D7-P). 아래 §1~§8은 원래 계획이고, 실제 구현은 **§9 구현 메모**를 따른다.
> (원안은 `telegram-notify.yml` 확장이었으나, 구현은 독립 워크플로로 분리했다 — §9 참고.)
> 선행: D3I 사람 검토 게이트(`scripts/send_telegram.py`), D3R/D3J 라이브 게시 경로
> (`.github/workflows/telegram-notify.yml`), D7-N freshness `!`(생성 시각 기준이라 잦은
> 갱신이 `최근 매칭` 정확도를 높인다).

---

## 1. 목표

하루 6회, KST 정해진 시각에:
1. 라이브 뉴스 리포트(`docs/daily/latest.html`)·운영자 스냅샷·요약 대시보드
   (`docs/daily/dashboard-latest.html`)를 갱신·게시한다.
2. Telegram 다이제스트를 발송한다.

발송 시각(KST): **07:00 · 11:00 · 13:00 · 16:00 · 21:00 · 00:00**.

핵심 안전 규칙:
- **기본은 dry-run / review (발송 안 함).** 실제 발송은 `TELEGRAM_AUTO_SEND=1` 명시 opt-in일 때만.
- 라이브 수집 성공(`live_ok`)일 때만 발송한다. mock fallback은 **절대** 라이브로 발송하지 않는다.
- 가짜 발송 0건, 토큰/시크릿 값 노출 0건(기존 규약 보존).

## 2. 비목표 (D7-P 범위 밖)

- inbound 봇 명령/대화 수신·응답 (P1).
- 사용자별 맞춤 발송·구독 관리.
- 스케줄 시각을 UI에서 동적으로 바꾸는 기능.
- 새 수집 소스 추가(스케줄링·발송만 다룬다).

## 3. 스케줄 설계 (KST → UTC cron)

KST = UTC+9. 6개 KST 시각을 UTC cron으로 환산:

| KST 슬롯 | UTC 시각 |
|---|---|
| 07:00 | 22:00 (전일) |
| 11:00 | 02:00 |
| 13:00 | 04:00 |
| 16:00 | 07:00 |
| 21:00 | 12:00 |
| 00:00 | 15:00 (전일) |

cron (UTC, 분=0): **`0 2,4,7,12,15,22 * * *`**

- 기존 단일 cron `0 23 * * *`(KST 08:00)는 **제거하고 위 6슬롯으로 대체**한다
  (사용자 명세에 08:00이 없고 07:00이 있으므로 07:00로 통합).
- GitHub Actions의 `schedule`은 best-effort라 수분 지연될 수 있다(정시 보장 아님).
  운영 문서(RUNBOOK)에 "정시 ±수분" 명시.
- `concurrency` 그룹으로 슬롯 실행을 직렬화해 겹침/중복 발송을 방지한다.

## 4. 발송 게이트 (D3I 확장 — fail-closed 유지)

현재(D3I) `send_telegram.py`:
```
will_send = (TELEGRAM_SEND_MODE == "send") AND (REVIEW_APPROVED 또는 CONFIRM_SEND 승인)
기본 send_mode = manual  →  발송 안 함
```

D7-P가 추가하는 opt-in 플래그: **`TELEGRAM_AUTO_SEND`** (repo Variable, 값 `'1'`).

권장 설계 — **워크플로 주도(게이트 로직 불변)**:
- 예약 트리거 + `live_ok == true` + `vars.TELEGRAM_AUTO_SEND == '1'` 일 때만,
  워크플로가 send step에 `TELEGRAM_SEND_MODE=send` + `REVIEW_APPROVED=true`를 주입한다.
- `send_telegram.py`의 게이트 판정 로직은 **그대로 둔다**(단일 진실원천 · fail-closed).
- 보강(선택): 감사 로그에서 `auto` vs `manual`을 구분하기 위해 `send_telegram.py`가
  `TELEGRAM_AUTO_SEND=1`을 예약 컨텍스트 승인 출처로 인식하도록 추가할 수 있다. 단,
  미설정/공백/`≠1`은 **미승인**으로 유지(기본 안전).

기본(미설정 또는 `≠1`): 예약 실행은 리포트만 게시하고 다이제스트는 **빌드·로깅만**, 발송 0건.
수동 `workflow_dispatch` + `approve_send=true`는 그대로 사람 override로 동작한다.

진리표:

| 트리거 | live_ok | TELEGRAM_AUTO_SEND | approve_send | 발송? |
|---|---|---|---|---|
| schedule | true | `1` | — | ✅ 자동 발송 |
| schedule | true | 미설정/`0` | — | ❌ 게시만(dry-run) |
| schedule | false | `1` | — | ❌ mock fallback 발송 금지 |
| dispatch | true | — | `true` | ✅ 사람 승인 발송 |
| dispatch | true | — | (빈값) | ❌ review only |

## 5. 정직성·안전 불변

- **live_ok 게이트**: 뉴스 라이브 수집 실패/빈 결과면 작업트리 복원 + 발송 스킵(기존 경로 재사용).
- **중복 발송 방지**: 슬롯당 1회. `concurrency: group=telegram-autosend, cancel-in-progress=false`로
  직렬화하고, 재시도가 동일 슬롯을 재발송하지 않게 가드한다.
- **토큰/시크릿 비노출**: 값 출력 0건(기존 규약). `TELEGRAM_AUTO_SEND`는 평문 플래그(`1`)라 Secret이
  아닌 repo Variable 권장(감사 가시성 · 비밀값 아님).
- **슬롯 라벨**: 다이제스트 헤더에 발송 회차(예: `정기 브리핑 · 07:00 KST`)를 표기해 운영자가
  어느 회차인지 식별하게 한다.
- **심야(00:00) 발송**: 사용자 명세대로 유지하되, RUNBOOK에 특정 슬롯을 끄는 quiet-hours 옵션 안내.

## 6. 구현 작업 (파일별 · D7-P 착수 시)

1. `.github/workflows/telegram-notify.yml`
   - `schedule` cron을 §3의 6슬롯으로 확장(기존 `0 23` 대체).
   - 예약 트리거에서 `vars.TELEGRAM_AUTO_SEND == '1'` && `live_ok`면 send step에
     `TELEGRAM_SEND_MODE=send` + `REVIEW_APPROVED=true` 주입.
   - `concurrency` 그룹 추가(겹침 방지).
   - 현재 UTC를 KST 슬롯 라벨로 환산해 다이제스트 step에 env로 전달.
2. `scripts/send_telegram.py`
   - (선택) `TELEGRAM_AUTO_SEND`를 승인 출처로 인식하고 감사 로그에 `auto`/`manual` 구분.
     게이트는 여전히 fail-closed(미설정=미승인).
   - 슬롯 라벨이 있으면 메시지/헤더에 반영.
3. `scripts/build_telegram_digest.py`
   - 슬롯 라벨/시각 표기 옵션(회차 식별). 콘텐츠 계약은 불변.
4. `scripts/verify_scheduled_autosend.py` (신규 verifier · 완전 오프라인 · 실제 POST 0건)
   - cron 6필드가 §3의 6 KST 슬롯과 정확히 매핑되는지 단위 검증(UTC→KST 환산).
   - 기본(미설정/`0`) 예약 → 발송 0건(게시만): 게이트 결정적 테스트.
   - `TELEGRAM_AUTO_SEND=1` + `live_ok` + schedule → `will_send=true`(주입 경로) 검증.
   - `live_ok=false`(mock fallback) → 발송 안 함.
   - 토큰/시크릿 비노출(TOKEN_SHAPE 0건).
5. 운영 문서 갱신: `EXECUTIVE_RADAR_RUNBOOK.md` · `NEXT_OPERATOR_HANDOFF.md`에
   `TELEGRAM_AUTO_SEND` opt-in 절차 + 슬롯표 + 롤백(=Variable 제거) 안내.
   - (선택) 새 verifier를 CI 게이트 큐레이션에 추가할지 결정.

## 7. 수용 기준 (Acceptance)

- 예약 실행이 6 KST 슬롯에 트리거되고 라이브 리포트·대시보드를 갱신한다.
- `TELEGRAM_AUTO_SEND` 미설정: 발송 0건(리포트만 게시) — 기본 안전.
- `TELEGRAM_AUTO_SEND=1`: 각 슬롯에 다이제스트 자동 발송(`live_ok`일 때만).
- mock fallback(`live_ok=false`) 시 발송 0건.
- `verify_scheduled_autosend.py` 전부 PASS(오프라인).
- 기존 D3I 게이트 및 D7-N 트리/freshness 회귀 0건(CI 게이트 그린 유지).

## 8. 결정 / 열린 질문

- `TELEGRAM_AUTO_SEND`을 repo **Variable**(평문 `1`)로 둘지 Secret로 둘지 — 단순 플래그라
  Variable 권장(감사 가시성, 비밀값 아님).
- 기존 08:00(KST) 슬롯 폐지 — 사용자 명세에 07:00가 있으므로 08:00 제거·07:00로 통합 권장.
- 슬롯별 콘텐츠 차등(예: 00:00은 축약본) 여부 — 기본은 동일 다이제스트, 차후 검토.
- 슬롯 라벨을 다이제스트 본문에 노출할지, 헤더 메타에만 둘지 — 운영자 식별 편의 vs 임원 화면 간결성.

## 9. 구현 메모 (D7-P 착수 — 실제 구현과 원안의 차이)

원안(§6)은 기존 `telegram-notify.yml`을 확장하는 안이었으나, 실제 구현은 예약 자동화를
**독립 워크플로로 분리**했다 (관심사 분리 · 기존 수동/검토 경로 무간섭).

구현 산출물:
- `.github/workflows/scheduled-live-refresh.yml` — 6 KST 슬롯 cron(§3 그대로),
  live 리포트/대시보드 생성 → `live_ok`일 때만 commit/push(공개 Pages 갱신) → 게이트된
  예약 다이제스트. 공개 빌드라 비공개 감시 목록 경로(env)는 설정하지 않는다(사생활).
- `scripts/send_scheduled_telegram.py` — 예약 발송 오케스트레이터. 기본 dry-run,
  `TELEGRAM_AUTO_SEND=1` opt-in일 때만 발송 시도. freshness(생성 시각 stale) · live
  정직성(`news_data_mode != live`는 `TELEGRAM_ALLOW_MOCK=1` 없이 거부) 게이트를 통과해야
  `send_telegram.py`(D3I 단일 진실원)에 위임한다 — 실제 POST·토큰 처리는 재구현하지 않는다.
- `scripts/verify_scheduled_refresh_and_telegram.py` — 완전 오프라인 가드(36검사):
  cron→KST 매핑, 기본 dry-run, opt-in 게이트, stale/mock 거부, 자격증명 없으면 가짜 발송 0,
  토큰 모양 0, 공개 워크플로 비공개 경로 0.

기존 `telegram-notify.yml`은 **그대로 둔다**(수동 dispatch + 08:00 KST 게시 경로 유지,
D3I 검증기 계약 보존). 두 워크플로는 발송 시각이 겹치지 않으며, 양쪽 다 명시 승인/opt-in
없이는 발송 0건이다. 08:00 게시 중복이 신경 쓰이면 차후 telegram-notify의 schedule을 정리할
수 있으나, 그 경우 `verify_human_review_gate.py`가 요구하는 schedule/cron 존재 계약을 유지해야 한다.

정직성·rules.md §1/§4 보존: mock fallback 발송 금지 · 토큰/시크릿 값 출력 0 · 비공개 감시
목록 미노출 · 가짜 '발송됨' 위장 0 · 실제 발송은 운영자 opt-in + 자격증명이 모두 있을 때만.
