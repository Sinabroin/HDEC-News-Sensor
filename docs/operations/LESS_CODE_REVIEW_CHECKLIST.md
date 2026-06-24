# Less-Code Review Checklist

이 체크리스트는 코드 리뷰나 자체 점검에서 "더 적은 변경, 더 강한 증거"를 확인하기 위한
운영 문서다. 목적은 기능을 덜 만드는 것이 아니라, 불필요한 코드와 불필요한 범위 확장을
막고 실제 회귀를 잡는 verifier를 남기는 것이다.

## A. Baseline First

- `git fetch origin --prune`을 실행했는가.
- `git status --short --branch`에서 tracked dirty가 없는가.
- `git log --oneline --decorate -8`로 기준 커밋을 확인했는가.
- untracked 보호 경로(`.agents/`, `.claude/settings.local.json`, `design/`)를 stage하지 않았는가.

## B. Existing Owner Before New File

- `rg`로 기존 구현, 호출자, verifier를 찾았는가.
- 새 helper/file을 만들기 전에 기존 owner 파일을 보강할 수 있는지 확인했는가.
- 같은 계약을 여러 곳에 patch하지 않고 공유 경계에서 고쳤는가.
- 기존 pattern과 naming을 따랐는가.

## C. Prefer One-Domain Patch

- report builder 작업이 collector/scoring/sender를 건드리지 않았는가.
- preference 작업이 topic profile, source rule, scoring rule을 건드리지 않았는가.
- Telegram 작업이 report layout을 같이 바꾸지 않았는가.
- 문서 작업이 runtime app behavior를 바꾸지 않았는가.

## D. Prefer Source Generator Over Generated Artifact

- `docs/daily/latest.html` 변경은 generator/template/CSS 변경 뒤 재생성한 결과인가.
- `dashboard-latest.html`과 `latest.html`을 혼동하거나 교체하지 않았는가.
- `operator-latest.html`을 삭제/교체하지 않았는가.
- final report에 재생성 여부와 명령을 남겼는가.

## E. Avoid Verifier Theater

- verifier가 실제 사용자/운영 회귀를 잡는가.
- 기존 verifier 보강으로 충분한데 새 파일을 만들지는 않았는가.
- 단순 문자열 존재만 확인하고 구조적 실패를 놓치지는 않는가.
- stale expectation을 제품 버그로 오판하지 않았는가.
- verifier가 secrets, network, sender side effect에 의존하지 않는가.

## F. Mobile Structure Proof

모바일/UI 변경이면 CSS token만 보지 말고 구조를 본다.

- mobile media query가 two-column/split-pane을 1-column 또는 normal flow로 바꾸는가.
- sticky/fixed side panel이 mobile에서 꺼지는가.
- lens/filter UI가 collapsed drawer, compact chips, 또는 짧은 top block으로 축소되는가.
- article/content stream이 filter 다음에 바로 나타나는가.
- long title, URL, table/list가 wrap 또는 contained scroll을 갖는가.
- `latest.html`은 full report이고 `dashboard-latest.html`은 summary dashboard임을 verifier가 확인하는가.

## G. Sender Gate Proof

Telegram 또는 workflow를 만졌다면 다음 증거가 필요하다.

- dry-run payload에 `"요약 대시보드 보기"`가 있고 target이 `dashboard-latest.html`인가.
- dry-run payload에 `"전체 리포트 보기"`가 있고 target이 `latest.html`인가.
- human review/manual approval gate가 유지되는가.
- 예약 workflow가 실제 send를 자동 실행하지 않는가.
- 실제 Telegram 발송을 하지 않았는가.

## H. Exact Staging

- `git add .`를 사용하지 않았는가.
- stage 대상 파일을 명시했는가.
- `git diff --cached --name-status`가 의도한 파일만 보여주는가.
- `.agents/`, `.claude/settings.local.json`, `design/`, secrets/env 파일이 staged에 없는가.
- `git diff --cached --check`가 통과했는가.

## I. Final Report Format

완료 보고는 아래 순서로 짧게 쓴다.

1. Baseline status.
2. Files changed.
3. Ponytail-inspired principle or checklist adopted, if relevant.
4. Runtime behavior changed: yes/no.
5. Generated artifacts changed: yes/no, with builder command if yes.
6. Sender gate proof, if Telegram touched.
7. Mobile structure proof, if UI touched.
8. Gates passed.
9. Commit hash.

## J. Review Tags

복잡도 리뷰를 할 때는 한 줄 finding으로 남긴다.

- `delete`: 죽은 코드, speculative feature, 사용하지 않는 flexibility.
- `reuse`: 이미 있는 helper/pattern으로 대체 가능.
- `stdlib`: 표준 라이브러리로 대체 가능.
- `native`: HTML/CSS/browser/platform 기능으로 대체 가능.
- `domain`: 다른 도메인을 불필요하게 건드림.
- `proof`: verifier가 실제 회귀를 못 잡음.

문제가 없으면 "Lean already. Ship."이라고 적고 끝낸다.
