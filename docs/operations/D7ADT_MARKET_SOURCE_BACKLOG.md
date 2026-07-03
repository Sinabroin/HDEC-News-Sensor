# D7-AD-T 시장 소스 연동 백로그 · 화면 상태 정리 (조사·계획 전용)

작성 시점: D7-AD-R(단일 임원 대시보드 IA 정리) 작업 중. **조사/계획 전용**이며 새 소스를
연동하지 않는다. 가짜 값/차트를 만들지 않는다(rules.md §1). 소스별 상세 조사는 기존
[`D7AB_MARKET_INTEGRATION_BACKLOG.md`](./D7AB_MARKET_INTEGRATION_BACKLOG.md)를 그대로 계승한다 —
이 문서는 그 위에 **화면 표기(상태 보드)와 우선순위**만 얹는다.

## 배경 — D7-AD-R에서 바뀐 것

- 기존: 미연동 지표가 카테고리 카드 하단에 "미연동 관찰 후보"로만 흩어져 방치된 인상.
- D7-AD-R: 시장 탭 상단에 **시장 소스 연동 현황 보드**(`#marketStatusBoard`,
  `renderMarketStatusBoard()`)를 추가해 전체 유니버스를 4개 상태로 요약한다. 미연동 후보를 길게
  늘어놓지 않고 상태로 구분한다. 카테고리 카드(상세 드릴다운)는 그대로 유지된다.

## 4개 상태 정의 (표기 계약)

`MODEL.market_items`의 `data_mode`/값으로 분류(가짜 값 생성 없음):

| 상태 | 판정 | 의미 |
|---|---|---|
| 연동 완료 | 값 보유 + `delayed_market`/`proxy_market` | 지연/대용 현재값 노출(현재 체결값 아님) |
| 보고·수동 확인 | `data_mode == manual_or_reported` | 협회·통계·고시 기반, 수동 확인 필요 |
| 미연동 후보 | 값 없음(`unavailable`) · 비우선 | 공개 소스 없음 — 값 미생성(정직 표기) |
| 우선 연동 필요 | 아래 지정 목록 | 운영 backlog 최우선(건설 투입재·핵심 금리) |

## "우선 연동 필요" 목록 (`MARKET_PRIORITY`)

건설 투입재(철·시멘트)와 조달·환헤지 판단에 직결되는 핵심 금리를 최우선으로 지정한다.

- `scrap_steel` 철스크랩, `cement` 국내 시멘트(보고), `coking_coal` 원료탄 — 철강·시멘트 투입 원가
- `us_2y` 미 국채 2Y, `kr_10y` 한국 국고 10Y — 정책금리 방향·국내 조달금리 벤치마크

나머지 사용자 지정 백로그(니켈·아스팔트/역청·항공유(등유)·벙커유·TWD/KRW)는 **미연동 후보**로 두고,
소스가 검증되면 순차 승격한다. (소스 후보·`data_mode` 판정은 D7-AB 문서 참조.)

## 원칙 (변경 없음)

- **no fake values, no fake charts.** 공개 무료 실시간 시세가 없으면 값을 만들지 않고 `data_mode`로
  성격을 선언한다. 미연동/보고는 버그가 아니라 설계된 정직 상태다.
- 상태 보드는 **라벨 칩만** 노출한다(값/차트 없음) — 분류/우선순위 도구이지 시세 표시가 아니다.

## D7-AD-W Phase 1C UI (접힘 · anchor 보존)

- **메인 화면:** `연동 완료` · `보고·수동 확인`만 기본 노출 (`mktstatus-main`).
- **접힘:** `미연동 후보` · `우선 연동 필요` → `<details class="ms-collapse">` (`미연동/후보 관리 · N개`).
- **카테고리 카드:** 미연동 행 → `<details class="mcat-backlog">` + `<div class="mcat-unlinked">` (기본 닫힘).
- **회귀 anchor (verifier):** `marketLinked`, `mcat-unlinked`, `mcat-backlog` — 템플릿 JS에 보존.

## D7-AD-X 실연동 우선순위 (adapter · 별도 Phase)

실제 fetch adapter는 Cursor/CI에서 e2e 검증 가능할 때 **별도 커밋**으로 진행한다.
가짜 값/차트 생성 금지.

| 순위 | id / 항목 | 후보(문서) |
|---:|---|---|
| 1 | `us_2y` | FRED DGS2 |
| 2 | `kr_10y` | FRED OECD / 한은 ECOS |
| 3 | `twdkrw` | USD/KRW × USD/TWD 교차 |
| 4 | Hormuz | **해소(D7-AE-RC3)**: `yasumorishima/hormuz-ship-tracker` 확인 — aisstream.io 무료 스트림, 상주 러너 필요. 계획: HORMUZ_LIVE_INTEGRATION.md |
| 5 | `jet_kerosene` | EIA/FRED DJFUELUSGULF |
| 6 | `nickel` | World Bank Pink Sheet |
| 7 | `scrap_steel` | FRED WPU1012 |
| 8 | `bitumen` | FRED WPU05810212 |
| 9 | `cement` | KOSIS/ECOS PPI |
| 10 | `coking_coal` / `bunker_fuel` | 보고·대용 검토 |

상세 조사: [`D7ADV_MARKET_SOURCE_DISCOVERY.md`](./D7ADV_MARKET_SOURCE_DISCOVERY.md)

## 착수 전 체크리스트

- [ ] 우선 목록부터 실측 프로빙(US 2Y=FRED `DGS2`, KR 10Y=한은/유료, 스크랩/원료탄=협회·보고 등 — D7-AB 참조)
- [ ] 승격 시 `data_mode` 정직 판정(지연/대용/보고) 후에만 값/차트 노출
- [ ] `MARKET_PRIORITY` 갱신 시 이 문서와 동기화 · 상태 보드 카운트 회귀 확인
