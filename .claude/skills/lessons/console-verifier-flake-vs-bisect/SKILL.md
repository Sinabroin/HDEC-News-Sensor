---
name: console-verifier-flake-vs-bisect
description: verify_editorial_review_console 헤드리스 타임아웃을 코드 원인으로 bisect하기 전에 반드시 동일 조건 A/B 재실행으로 플레이크부터 배제. 브라우저 하네스 verifier 실패를 조사할 때 항상 참조.
---
## 실수
템플릿 편집 후 verify_editorial_review_console이 "headless browser timeout after 45s"로
실패하자 stash A/B가 두 번 일치한 것을 근거로 코드 원인으로 단정, 헛된 hunk bisect에
장시간 소모. 최종적으로 동일 전체 편집으로 3회 연속 200/0 — 배경 부하(R3 스택·수집·
E2E 서버) 잔여가 만든 플레이크였다.

## 원인
- Windows Chrome --headless=new --virtual-time-budget --dump-dom 하네스는 시스템
  부하에 매우 민감(기존 lesson의 "부하 타임아웃 오탐"과 동일 계열).
- "지금은 idle"이라는 감각은 신뢰 불가: 직전 백그라운드 작업 직후는 여전히 오염.
- stash A/B도 각 1회씩이면 플레이크와 구분 불가 — 우연히 일치할 수 있다.

## 재발 방지 규칙
- 브라우저 하네스 verifier 실패는 먼저 같은 조건으로 2회 이상 재실행해 플레이크를
  배제한 뒤에만 코드 bisect에 들어간다.
- bisect 각 변형도 최소 2회 실행 일치로만 판정한다.
- 백그라운드 작업(R3 스택 등)과 브라우저 하네스 verifier를 절대 병행하지 않는다.
