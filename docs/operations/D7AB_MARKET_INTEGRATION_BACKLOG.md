# D7-AB 시장 지표 미연동 백로그 (조사·계획 전용)

작성 시점: D7-AA 작업 중. 이 문서는 **조사/계획 전용**이며, D7-AA에서는 아래 지표를
**연동하지 않는다**. 가짜 값/가짜 차트를 만들지 않는다(rules.md §1, 사용자 요구).

## 원칙 (변경 없음)

- **no fake values, no fake charts.** 공개 무료 실시간 시세가 없는 지표는 값을 만들지
  않고 `data_mode`로 성격을 정직하게 선언한다. (`app/market_profiles.py` 상단 참조)
- `data_mode` 5종: `delayed_market`(지연 시세) · `proxy_market`(대용/방향성) ·
  `manual_or_reported`(협회·통계 보고, 수동 입력) · `unavailable`(공개 소스 없음, 값 미생성).
- 미연동(`unavailable`)·보고(`manual_or_reported`)는 **버그가 아니라 설계된 정직 상태**다.
  대시보드는 이들을 "미연동"/value=null로 표기하고 클릭(상세 차트)을 막는다.

## 사용자가 지적한 7개 항목의 현재 상태 (코드 기준)

`app/market_profiles.py` · `app/market_history.py` 인스펙트 결과. "값 동작"은 런타임에
실제로 무엇이 표시되는지를 뜻한다(가짜 값은 어디에도 없음).

| 지표 | 코드 상의 현재 상태 | data_mode | 값 동작 | 왜 미연동인가 |
|---|---|---|---|---|
| 철스크랩 (Scrap steel) | **미모델** — 전용 instrument 없음 | — | 표시 안 됨 | 공개 무료 실시간 철스크랩 지수 부재. 철강 관련은 `steel_rebar_proxy`(SLX ETF)·`hot_rolled_coil_proxy`(HRC=F)로 방향성만 있고 스크랩 직접가는 없음 |
| 국내 시멘트 (Cement) | `cement_proxy` 존재 | `manual_or_reported` | value=null (미입력) | 국내 시멘트 가격은 공개 시세 API가 없음 — 고시·협회 통계 기반(수동 입력 필요) |
| 아스팔트·역청 (Bitumen) | `asphalt_bitumen_proxy` 존재 | `proxy_market` (WTI `CL=F`) | WTI 방향성만(가격 아님) | 무료 역청 직접가 부재. 현재는 원유(WTI) 정제부산물 방향성 대용일 뿐 실제 역청 가격이 아님 |
| 미 국채 2Y (US 2Y) | `market_history.SOURCE_NEEDED_IDS`에 `us_2y` 등록(소스 필요). 금리 유니버스 만기 집합은 1Y/3Y/5Y/10Y라 2Y 만기는 미등록 | `unavailable` | 비클릭(차트 없음) | 공개 무료 Yahoo 2Y 금리 심볼 부재. 매크로 레이어가 검증한 무료 금리 심볼은 US 5Y(`^FVX`)·10Y(`^TNX`)뿐 |
| 한국 국고 10Y (KR 10Y) | `_yield_instrument("kr","10y")` → `source_symbol=None`. `market_history.SOURCE_NEEDED_IDS`에 `kr_10y` 등록 | `unavailable` | value=null·비클릭 | 공개 무료 실시간 한국 금리 시세 부재 — 중앙은행/유료 등 별도 소스 필요 |
| 항공유·등유 (Jet/Kerosene) | **미모델** — 전용 instrument 없음 | — | 표시 안 됨 | 무료 항공유 직접가 부재. 연료 계열은 `diesel_proxy`(HO=F)만 있고 항공유/등유 전용은 없음 |
| 벙커유 (Bunker fuel) | **미모델** — 전용 instrument 없음 | — | 표시 안 됨 | 무료 선박용 벙커유(VLSFO/380cst) 직접가 부재 |

요약: 7개 중 **3개(시멘트·아스팔트·일부 금리)는 이미 정직하게 모델링**되어 있고(보고/대용/미연동),
**4개(철스크랩·US 2Y·항공유·벙커유)는 아예 instrument가 없거나 만기 미등록**이다.

## D7-AB 조사 후보 (제안만 — 이번에 구현하지 않음)

각 후보는 "연동 가능 여부"와 "어느 `data_mode`가 정직한가"를 먼저 검증한 뒤에만 착수한다.
실측 GET 프로빙으로 소스가 실제 응답하는지 확인하기 전에는 파서를 쓰지 않는다
(lessons: live 소스 프로빙).

1. **US 2Y / KR 10Y (금리)** — 가장 가치가 높고 후보가 명확.
   - US 2Y: FRED(미 연준 경제데이터)의 `DGS2`가 무료(키 필요). 일별 종가이므로 `delayed_market`이
     아니라 "일별 보고값" 성격 → `manual_or_reported` 또는 신규 `daily_reported` 모드 검토.
   - KR 10Y: 한국은행 ECOS(무료, 키 필요) 또는 KOFIA 채권 시가평가. 마찬가지로 일별 보고값 성격.
   - 구현 시: 네트워크는 `app/live_market.py`류 leaf에 격리(공급자 교체 = leaf + sources 2파일),
     매크로 레이어의 fetcher 주입 패턴 재사용. 키는 env로만 읽고 출력/직렬화 금지.

2. **국내 시멘트 (Cement)** — 실시간 API가 **존재하지 않음**.
   - 한국시멘트협회/통계 기반 주기 보고값을 수동/배치 입력하는 경로만 가능 → `manual_or_reported` 유지.
   - 제안: 운영자가 분기/월 단위 보고값을 입력하는 별도 입력 파일(가짜 값 아님, 출처 명시) 설계.

3. **아스팔트·역청 (Bitumen)** — 무료 직접가 부재.
   - 현재 WTI 방향성 proxy를 유지하되, 국내 아스팔트 고시가(보고)가 확보되면 `manual_or_reported`로 승격.
   - 직접가(Argus/Platts 등)는 유료 — 무료 경로 확정 전까지 proxy/보고로만.

4. **철스크랩 (Scrap steel)** — 무료 직접가 부재.
   - 국내 철스크랩 유통가는 협회/업계 보고 성격 → 신규 `manual_or_reported` instrument 후보.
   - 글로벌(터키 HMS 등)은 유료 지수 — 무료 경로 없음.

5. **항공유·등유 / 벙커유 (Jet·Kerosene / Bunker)** — 무료 직접가 부재.
   - 방향성만 필요하면 HO=F(난방유)·Brent 기반 `proxy_market` 신규 instrument로 검토(가격 아님 명시).
   - 직접가는 유료(Platts/Ship&Bunker 등) — 무료 경로 확정 전까지 미연동 유지.

## D7-AA 범위에서 하지 않은 것 (명시)

- 위 7개 지표에 대한 **값/차트 생성 0건**. instrument 추가, 심볼 매핑, 소스 연동 모두 안 함.
- 이미 존재하는 정직 상태(시멘트=보고/미입력, 아스팔트=WTI 방향성, US 2Y·KR 10Y=미연동)를
  바꾸지 않았다. 가짜로 채우지 않았다.
- 실제 연동은 위 후보 검증(실측 프로빙 + data_mode 정합성)을 거쳐 **D7-AB에서** 진행한다.
