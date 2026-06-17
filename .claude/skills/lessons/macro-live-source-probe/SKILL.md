---
name: macro-live-source-probe
description: live 외부 데이터 소스(시세/뉴스 등)를 연동할 때 적용. 파서를 먼저 쓰지 말고 실제 엔드포인트를 프로빙하고, 네트워크 IO를 leaf로 격리하라. P0-C2 macro 연동에서 도출.
---
## 실수
P0-C2 live macro 첫 구현을 Stooq CSV(`https://stooq.com/q/l/?s=...&e=csv`) 기준으로
파서까지 다 작성했는데, 실제 엔드포인트는 HTTP 404를 반환했고 historical 경로는
"This site requires JavaScript" 봇 차단 HTML을 돌려줬다. 검증기가 SKIP-friendly여서
오프라인에선 전부 PASS였지만, live 실데이터는 0건이었다(가짜 PASS 위험).

## 원인
"공개 무인증 CSV"라는 과거 가정에 의존해 **실제 프로빙 없이 파서를 먼저** 작성했다.
외부 공개 엔드포인트는 차단/포맷이 수시로 바뀐다.

## 재발 방지 규칙
1. live 외부 소스는 파서 작성 **전에** `dangerouslyDisableSandbox:true`로 실제 GET을
   몇 개 변형으로 프로빙해 status/스키마를 눈으로 확인한다. 200 코드만 보지 말고 body가
   기대한 데이터인지(봇 차단 HTML/JS 월이 아닌지) 확인한다. (이 repo는 네트워크가 열려 있다.)
2. 네트워크 IO는 반드시 leaf 모듈에 격리한다(app/live_collector.py·app/live_macro.py).
   소유 도메인 파일(collector.py·macro_snapshot.py)은 네트워크를 모듈 레벨에서 import하지
   않고 leaf를 지연 import해 호출만 한다. 그러면 공급자 교체가 **leaf + data/live_*_sources.json
   2파일**로 끝난다(Stooq→Yahoo 전환이 실제로 그랬다 — 도메인/렌더/검증 계약 무변경).
3. 실패는 항상 unavailable/빈 리스트로 강등한다(가짜 값 0건). 기본 모드는 mock(네트워크 0건).
4. 검증기는 주입 fetcher로 계약을 **결정적**으로 검사하고(성공→live+stale, 실패→unavailable),
   실수집은 SKIP-friendly로 둔다. leaf의 파서 헬퍼(_to_value/_meta_from_chart 등)는
   네트워크 없이 단위 검사할 수 있게 작은 순수 함수로 쪼갠다.
5. 검증기 라벨에 "live 미구현" 같은 시점 가정을 넣지 말 것 — 다음 스프린트에서 거짓이 된다.

## 후속 (P0-C2 pre-commit smoke에서 추가로 드러난 3가지)
6. **provenance footer는 표시 게이팅과 같은 조건으로 묶어라.** 섹션은 macro_data_mode=="live"일 때만
   수치를 렌더하는데 footer/헤더 출처 고지가 항상 "시장지표 미연동"이면 같은 리포트가 live 수치와
   "미연동"을 동시에 보여주는 모순이 난다. 고지 한 줄도 같은 게이트(live면 "출처·기준", 아니면 "미연동")를
   거치게 하고, 렌더된 HTML 전체에 "live 값 + 미연동"이 공존하지 않음을 e2e로 검증하라.
7. **실제 fetch된 값이라도 라벨 스케일과 안 맞으면 보여주지 말고 결측 처리하라.** 심볼이 맞아도
   (Yahoo meta.shortName으로 ^KS11=KOSPI Composite 확인) 환경에 따라 비현실적 값(KOSPI 8726)이 올 수 있다.
   지표별 sane_min/sane_max(실세계 스케일 기준, 넓게)로 자릿수·심볼 오류를 결측 처리한다 — 가짜로 보여주는
   것보다 정직하게 빠지는 게 낫다. 정상값 오탐을 피하려 범위는 넓게, 단 ÷10 누락/지수 혼동은 잡게.
8. **같은 publish 실행이 여러 프로세스로 나뉘면 각자 fetch가 값/시각을 어긋나게 한다.** 리포트 빌드와
   다이제스트 발송이 별도 프로세스라 WTI가 서로 달랐다. 프로세스 내 캐시(leaf 모듈 레벨)로 중복 fetch를
   막고, 프로세스 간에는 1회용 공유 파일(MACRO_SNAPSHOT_FILE: 먼저 도는 쪽이 쓰고 뒤가 읽음, CI는 runner
   temp 휘발 경로)로 단일 snapshot을 강제한다. is_stale은 읽은 뒤 now 기준으로 다시 계산(가짜 신선도 금지).
