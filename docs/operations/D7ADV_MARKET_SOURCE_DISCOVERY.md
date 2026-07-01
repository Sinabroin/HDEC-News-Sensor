# D7-AD-V 시장 지표 무료/대체 소스 재조사 (Source Discovery)

D7-AD-U가 사용자 지정 10개 지표를 "공개 무료 실시간 소스 없음"으로 너무 빨리 결론내린 것을 반려하고,
repo 내부 소스 구조 + 공개 API 후보를 **재조사**한 결과다. 이 문서는 후속 조사 문서이며 기존
[`D7ADT_MARKET_SOURCE_BACKLOG.md`](D7ADT_MARKET_SOURCE_BACKLOG.md)를 덮어쓰지 않는다.

원칙: **확인하지 않은 소스를 "있다"고 단정하지 않는다. 가짜 값/차트를 만들지 않는다.** 아래 후보는
조사 시점에 존재를 확인한 것과 미확인(파생/코드 미상)을 구분해 표기한다. 실제 연동은 항목별
어댑터 단위 후속 커밋(예: D7-AD-W)에서 진행하며, 이번 커밋은 **조사·계약(문서/모델 사유)**만 반영한다.

## 조사 방법 · 검증 상태

- **repo 내부(코드) 확인:** 시장 소스 레이어는 현재 **Yahoo Finance chart API** 단일 계열이다.
  - `app/live_market.py` · `app/live_macro.py` · `app/market_history.py` → `https://query1.finance.yahoo.com/v8/finance/chart/`
  - 소스 레지스트리: `data/live_macro_sources.json`(`provider: yahoo_chart`, 심볼 `USDKRW=X`/`CL=F`/`^KS11`/`^VIX`/`^TNX`/`^GSPC`).
  - `app/market_profiles.py`가 52개 지표 메타(라벨/카테고리/data_mode/심볼)를 선언. 10개 지표는 모두
    `unavailable`(값 없음) 또는 `manual_or_reported`(cement) 또는 proxy(bitumen=WTI `CL=F` 방향성).
  - `app/market_history.py`의 `SOURCE_NEEDED_IDS = ("us_2y", "kr_10y")` — 두 금리는 무료 Yahoo 심볼 없음.
  - **FRED/ECOS/EIA 공급자는 코드에 없음** — 주석/백로그에만 언급.
- **공개 API 후보 확인:** 네트워크 조사로 아래 시리즈 ID의 **존재를 확인**(verified)했다. 단, 이 오프라인
  빌드에서 실제 값 fetch를 end-to-end로 검증할 수는 없어, 값 생성 없이 **후보로만** 기록한다.

## 분류 규칙 (4-status, 상태 보드와 정합)

- `linked` — 현재값/차트 보유(연동 완료). · `report_manual` — 보고·수동 확인. · `candidate` — 붙일 수 있는
  무료/지연/보고 후보가 있으나 연동 미구성(미연동 후보). · `missing` — 확인된 무료 소스 없음(재조사 대기).

## 지표별 소스 후보 표 (사용자 지정 10개)

| # | 지표 (id) | current_status | source_candidate | source_type | update_freq | chart_possible | limitation | next_action |
|---|-----------|----------------|------------------|-------------|-------------|----------------|------------|-------------|
| 1 | 니켈 (`nickel`) | candidate | World Bank Pink Sheet 니켈(LME 표준등급); Stooq 니켈선물(티커 미확인) | public_free(Pink Sheet) / unofficial(Stooq) | 월간(Pink Sheet) | yes | 월평균·~1개월 지연, 실시간 LME 아님(유료) | Pink Sheet 월간 시리즈 어댑터 검토(월간·지연 명시) |
| 2 | 철스크랩 (`scrap_steel`) | candidate | FRED `WPU1012`(철스크랩 PPI), `PCU4299304299301` | official(BLS 지수) | 월간 | yes | 지수(₩/톤 아님)·미국 기준; HMS 80/20 CFR Turkey 실물가는 유료 | FRED CSV 지수 어댑터(지수 명시) 또는 국내 협회 보고 병기 |
| 3 | 국내 시멘트 (`cement`) | report_manual | KOSIS PPI `DT_404Y016`(시멘트) / 한은 ECOS PPI; 한국시멘트협회 통계 | report_manual / public_free(지수) | 월간(PPI)·협회 비정기 | yes(지수) | 생산자물가'지수'(실 ₩/톤 계약가 아님); 실거래가는 트레이드 프레스·수동 | KOSIS/ECOS PPI 지수 연동 + 보고값 병기(현 manual 유지) |
| 4 | 아스팔트/역청 (`bitumen`) | candidate | FRED `WPU05810212`(아스팔트 PPI), `PCU324121324121P` | official(BLS 지수) | 월간 | yes | 미국 지수·국내/아시아 ₩ 현물 아님; OPIS 역청은 유료. 현재는 WTI(`CL=F`) 방향 proxy만 | 지수/보고 병기 검토 |
| 5 | 항공유/등유 (`jet_kerosene`) | candidate | EIA US Gulf Coast Jet Fuel Spot; FRED `DJFUELUSGULF`(일간)/`WJFUELUSGULF`(주간) | official(US gov) | 일간/주간(수일 지연) | yes | 미 걸프 기준·아시아(싱가포르) 제트유 아님; USD/gallon | FRED `DJFUELUSGULF` CSV 일간 어댑터 검토 |
| 6 | 벙커유 (`bunker_fuel`) | candidate | EIA 잔사유(No.6 / Bunker C) — 대용 | official(US gov)·proxy | 월간(일부 주간) | yes | 항만 VLSFO/380 CST 실 벙커가 아님(지수는 유료); IMO-2020 이후 No.6 ≠ VLSFO | 대용(잔사유) 표기로 월간 적재 검토 |
| 7 | 원료탄 (`coking_coal`) | missing | 공개 무료 강점탄 벤치마크 **미확인**; Pink Sheet는 '연료탄(thermal)'만 확인(대체 부적합); Platts PLV HCC/Argus/Fastmarkets 유료 | paid_only / report_manual | (유료) 일간/주간 | no(무료 미확인) | 연료탄으로 대체 불가(다른 시장); 무료 met-coal 피드 미발견 | 제철사·협회 보고 기반 수동 확인 + 무료 벤치마크 재조사 |
| 8 | 미 국채 2Y (`us_2y`) | candidate(우선) | FRED `DGS2`(일간 CMT), `GS2`(월평균) | official(Fed via FRED) | 일간(영업일) | yes | 영업일만·휴일 ND 결측; FRED JSON API는 키 필요(CSV는 불필요) | **FRED `DGS2` CSV 어댑터 추가(우선)** — 기존 `^TNX`(10Y)와 동일 % 스케일 |
| 9 | 한국 국고 10Y (`kr_10y`) | candidate(우선) | FRED `IRLTLT01KRM156N`(월간·OECD); 한은 ECOS 일간(무료 키·국고채 10년 stat 코드 확정 필요); KOFIA | official(FRED 월간 / ECOS 일간) | FRED 월간·ECOS 일간 | yes | FRED는 월간·지연; ECOS는 무료 키 발급 + 정확 stat/item 코드 확정 필요 | **ECOS 키 발급 후 국고 10Y 코드 확정 연동(우선)**, 임시 FRED 월간 병기 가능 |
| 10 | TWD/KRW (`twdkrw`) | candidate | USD 레그 교차 산출 — Stooq `usdkrw`÷`usdtwd`(또는 Yahoo `USDKRW=X`+`USDTWD=X`); 직접 TWDKRW 무료 피드 미확인 | public_free / delayed | 일간/EOD(지연) | yes(교차 산출) | TWD·KRW 모두 major 아님 — 직접 크로스 thin/부재라 산출 필요; 중앙은행 공식환율 아님 | 기존 FX provider에 USD 레그 교차 산출 추가 검토 |

## 우선 연동 후보 (작은 단위부터)

1. **US 2Y — FRED `DGS2`** (일간·공개·CSV 키불필요). 기존 금리 스케일(`^TNX`)과 동일 % 표기 → 가장 낮은 리스크.
2. **KR 10Y — 한은 ECOS 일간**(무료 키). 임시로 FRED `IRLTLT01KRM156N`(월간) 병기 가능.
3. **항공유 — FRED `DJFUELUSGULF`**(일간·현물). 건설/물류 원가 민감 지표.
> 위 3개는 FRED CSV/ECOS 어댑터 1개씩으로 붙일 수 있는 후보다. 실제 연동은 fetch 검증이 필요한
> 후속 커밋(D7-AD-W)에서 어댑터+가드(sane_min/sane_max)와 함께 진행한다 — 이번 커밋은 값 생성 없음.

## 아키텍처 메모 (연동 시 붙일 위치)

- 새 공급자(FRED/ECOS)는 Yahoo와 병렬로 `app/live_macro.py`/`app/live_market.py` 계열에 **provider
  분기**로 추가하고, `data/live_macro_sources.json`에 시리즈 ID를 선언하는 방식이 기존 패턴과 정합.
- 지수(PPI)·월간·대용 소스는 실시간 현물가가 아니므로, 붙이더라도 `data_mode`를 `delayed`/`proxy`/
  `manual_or_reported`로 **정직 표기**하고 대시보드 정직성 스트립("현재 체결값 아님")을 유지한다.
- 값이 없거나 fetch 실패 시 **미연동으로 두고 값을 만들지 않는다**(sane_min/sane_max 가드로 이상치 결측 처리).

## 제약 준수

- 확인 안 된 소스는 "미확인"으로 표기(원료탄 무료 벤치마크, ECOS 코드, Stooq 직접 티커).
- 가짜 값/차트 생성 없음. 실제 연동은 어댑터+검증이 필요한 후속 단계로 분리.
- 대시보드 상태 보드(`MARKET_REASON`)에 항목별 **후보·한계·다음 액션**을 노출해 방치하지 않는다.
