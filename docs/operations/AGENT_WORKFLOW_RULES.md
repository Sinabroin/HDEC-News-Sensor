# Agent Workflow Rules — Less Code, Stronger Proof

이 문서는 HDEC Executive Radar에서 Claude/Codex 계열 에이전트가 작업할 때 따르는
로컬 운영 규칙이다. Ponytail 저장소의 "작게 만들되, 이해와 검증은 줄이지 않는다"는
아이디어를 참고했지만, 외부 코드를 vendoring하거나 플러그인·훅을 설치하지 않는다.

## 1. 작업 전 기준선

모든 변경은 기준선을 먼저 확인한다.

```bash
git fetch origin --prune
git status --short --branch
git log --oneline --decorate -8
```

- tracked dirty가 있으면 내 작업인지, 사용자 작업인지, 이전 생성물인지 먼저 구분한다.
- `.agents/`, `.claude/settings.local.json`, `design/`은 로컬/보호 대상이다. stage하지 않는다.
- 작업 목표가 문서라면 런타임 파일을 건드리지 않는다. 목표가 verifier라면 제품 동작을 바꾸지 않는다.

## 2. 먼저 소유 파일을 찾는다

새 파일을 만들기 전에 기존 소유 경로를 찾는다.

- 리포트 생성: `scripts/build_static_report.py`와 관련 verifier를 먼저 본다.
- 대시보드 export: `scripts/build_static_dashboard.py`, `/dashboard-preview` 경계, dashboard verifier를 먼저 본다.
- Telegram: `scripts/send_telegram.py`, digest builder, `.github/workflows/telegram-notify.yml`,
  human review gate verifier를 먼저 본다.
- executive preference: 개인 수신/필터링 경계만 다룬다. topic/lens catalog 같은 전역 sensing 기준과 섞지 않는다.
- 문서/운영 규칙: `docs/operations/`가 기본 소유 위치다.

`rg`로 호출자와 렌더링 경로를 확인한 뒤, 가장 가까운 소유 파일에서 고친다. 같은 문제를
여러 호출자에 반복 patch하지 말고 공유 경계에서 한 번 고친다.

## 3. 작은 변경 판단 순서

구현 전에는 다음 순서로 멈출 수 있는지 확인한다.

1. 이 변경이 실제로 필요한가.
2. 이미 이 코드베이스에 있는 helper, pattern, verifier로 해결되는가.
3. 표준 라이브러리나 네이티브 HTML/CSS 기능으로 충분한가.
4. 이미 설치된 의존성으로 충분한가.
5. 한 파일, 한 도메인 안에서 끝낼 수 있는가.
6. 그래도 필요할 때만 최소 코드를 추가한다.

줄 수를 줄이기 위해 validation, data-loss 방지, security, accessibility, send gate 검증을
제거하지 않는다. 작은 diff가 잘못된 경계에 있으면 두 번째 버그다.

## 4. 도메인 경계를 섞지 않는다

- 개인 executive preference는 개인 수신/필터링 foundation이다. 전역 sensing keyword,
  topic profile, business lens catalog를 바꾸지 않는다.
- operator settings는 전역 source/keyword/scoring 기준이다. 개인 preference 구현에 끌어오지 않는다.
- Telegram sender를 건드리는 작업은 실제 발송 금지, `approve_send=true` 금지,
  human review gate 유지 증명을 포함한다.
- secrets, API key, `.env` 파일은 작업 대상이 아니다.
- 공개 report output은 source of truth가 아니다. 생성물은 필요한 경우에만 generator로 재생성한다.

## 5. Generated Artifact 원칙

생성물이 깨졌으면 생성기를 고친다.

- `docs/daily/latest.html`은 full report output이다. 임의 hand-edit하지 않는다.
- `docs/daily/dashboard-latest.html`은 summary dashboard output이다. full report와 서로 바꾸지 않는다.
- `docs/daily/operator-latest.html`은 operator report 경로다. 삭제하거나 교체하지 않는다.
- 생성물이 task 결과물일 때는 어떤 builder 명령으로 재생성했는지 final report에 남긴다.

## 6. Verifier는 실제 회귀를 잡아야 한다

검증기는 "있어 보이는 문자열 검사"보다 실제 깨지는 계약을 잡아야 한다.

- 기존 verifier가 같은 계약을 이미 잠그면 새 파일을 만들지 말고 좁게 보강한다.
- stale verifier는 제품을 약화하기 전에 stale인지 먼저 증명하고, 기대값만 좁게 고친다.
- verifier가 runtime behavior를 바꾸면 안 된다.
- smoke 수준이면 smoke라고 이름과 범위를 분명히 한다.

좋은 verifier는 실패했을 때 어떤 제품 계약이 깨졌는지 바로 말해준다. 나쁜 verifier는
구현 디테일만 많이 세고 실제 사용자 회귀를 놓친다.

## 7. Mobile/UI 작업은 구조를 증명한다

모바일 UI 회귀는 CSS token 존재만으로 막지 못한다. 특히 report layout 작업은 다음을
구조적으로 확인한다.

- 390px 폭에서 주요 content가 정상 document flow에 있는가.
- side panel, sticky/fixed panel, overflow scroll pane이 본문을 밀어내거나 가리지 않는가.
- filter/lens UI가 compact drawer, chips, top block 중 하나로 축소되는가.
- article/content stream이 filter 다음에 바로 보이는가.
- table/list는 container 안에서 scroll 또는 wrap되는가.
- `latest.html`과 `dashboard-latest.html`이 서로 swap되지 않았는가.

가능하면 DOM order와 mobile media query를 함께 본다. CSS 문자열 하나만 보는 검증은 보조
신호일 뿐이다.

## 8. Telegram 경계 작업

Telegram 관련 파일을 만졌다면 최소한 다음을 증명한다.

- dry-run payload에서 `"대시보드 보기"`는 `dashboard-latest.html`로 간다.
- dry-run payload에서 `"상세 리포트 보기"`는 `latest.html`로 간다.
- send path는 여전히 human review/manual gate 뒤에 있다.
- workflow가 예약 실행에서 무조건 send하지 않는다.
- 실제 Telegram 발송은 하지 않는다.

## 9. Exact Staging

stage는 항상 파일 단위로 한다.

```bash
git add docs/operations/AGENT_WORKFLOW_RULES.md
git add docs/operations/LESS_CODE_REVIEW_CHECKLIST.md
```

금지:

```bash
git add .
```

commit 전에는 다음을 확인한다.

```bash
git diff --cached --name-status
git diff --cached --check
git status --short --branch
```

보호 대상이 staged에 있으면 commit하지 않는다.

## 10. Final Report

작업 종료 보고에는 사용자가 판단할 증거만 남긴다.

- baseline status
- files changed
- runtime behavior changed 여부
- generated artifact를 건드렸다면 builder 명령과 결과 path
- Telegram을 건드렸다면 A/B link mapping과 send gate proof
- mobile/UI를 건드렸다면 structure proof
- gates passed
- commit hash
