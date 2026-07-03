# 시장 지표 미연동 백로그 — 4분류 재감사 (D7-AE-RC3)

작성: 2026-07-03 · 목적: 공개 UI의 "연동 후보"를 임원 화면에서 **한 줄 카운트로 강등**하고
(상세는 이 내부 문서로), 미연동 지표 전수를 사용자 질문 그대로 4가지로 재분류한다:

- **A. 무료/공개 데이터 자체가 없음** — 어떤 공개 채널로도 그 지표의 값이 배포되지 않음
- **B. 유료 전용** — 데이터는 존재하나 유료 벤더(Platts/Argus/LME 등) 독점
- **C. 어댑터 미구현** — 무료/공개 소스가 확인됐고 우리가 아직 안 만든 것(키/코드 확정 포함)
- **D. proxy로만 가능** — 정확한 국내/현물 값은 없고 글로벌 벤치마크·교차 산출 대용만 가능

이번 라운드 실행: `us_2y`를 C에서 **연동 완료로 승격**(FRED DGS2 일별 CSV → 기간 차트,
`app/market_history.py` `_FRED_HISTORY`). 나머지는 값을 만들지 않는다(가짜 금지).

## 분류표 (2026-07-03 기준 · 대시보드 유니버스 49종 중 자동연동 제외 전수)

| id | 지표 | 분류 | 근거·후보 소스 | 다음 액션 |
|---|---|---|---|---|
| us_2y | 미 국채 2Y | **C→연동 완료** | FRED DGS2(무인증 CSV·일별) 실측 프로빙 OK | 완료(D7-AE-RC3) — 차트 연동 |
| kr_10y | 한국 국고 10Y | C | 일간은 한은 ECOS(무료·**키 필요**)/KOFIA(로그인) · 현재 FRED OECD 월간 proxy 단일값 | ECOS 키 발급+코드 확정 시 일간 교체 |
| us_cpi_row | 미 CPI(YoY) | C | FRED CPIAUCSL(무료·월간) | FRED 단일값 어댑터 확장(저난도) |
| us_base_rate | 미 기준금리 | C | FRED DFEDTARU(무료·일별 상단) | FRED 단일값 어댑터 확장(저난도) |
| scrap_steel | 철스크랩 | C(지수)·A(현물가) | FRED WPU1012(BLS PPI 지수·월간) — 국내 현물가 무료 없음 | 지수 표기로 연동 검토 |
| cement | 국내 시멘트 | C(지수)·A(실계약가) | KOSIS/ECOS PPI 품목 지수(월간) — 실계약가 공개 없음 | 보고 병기 + 지수 연동 검토 |
| bitumen | 아스팔트(역청) | C(지수)·A(국내가) | FRED WPU05810212(BLS 지수·월간) | 지수/보고 병기 검토 |
| nickel | 니켈 | C(월간)·B(실시간) | World Bank Pink Sheet(무료·월평균·~1개월 지연) / 실시간은 LME 유료 | Pink Sheet 월간 적재 검토 |
| zinc | 아연 | C(월간)·B(실시간) | 동일(Pink Sheet 아연) / Yahoo ZNC=F는 신뢰 불가 판정(기존 감사) | Pink Sheet 월간 적재 검토 |
| jet_kerosene | 항공유(등유) | C+D | EIA/FRED DJFUELUSGULF(무료·일간·미 걸프 기준 — 아시아 아님) | 지역 대용 표기로 연동 검토 |
| bunker_fuel | 벙커유 | B·D | 항만 VLSFO/380은 유료(Platts) · EIA 잔사유(No.6)로 월간 대용 가능 | 대용 표기 검토 |
| dubai_crude | 두바이유 | B | Platts(S&P) 유료 독점 — 무료 공식 시세 없음 | 보고 기반 유지 |
| lng_jkm | LNG JKM | B | Platts JKM 유료 독점 | 보고 기반 유지 |
| coking_coal | 원료탄 | B | Platts/Argus 유료 — Pink Sheet 연료탄은 대체 부적합(기존 감사) | 보고 기반 수동 확인 |
| twdkrw | TWD/KRW | D | 직접 무료 피드 없음 — USD/KRW ÷ USD/TWD 교차 산출만 가능 | Yahoo TWD=X leg 추가 후 교차(대용 표기) |
| sarkrw | SAR/KRW | D | Yahoo 심볼 부재(기존 감사) — USD 교차 산출만 가능 | 교차 산출 검토(대용 표기) |
| qarkrw | QAR/KRW | D | 동일 | 동일 |
| iron_ore | 철광석 | D(연동됨) | TIO=F 글로벌 벤치마크 실측 — 국내 도입가 아님(영구 proxy 분류) | 유지 |
| hrc_steel | 열연강판 | D(연동됨) | HRC=F 미 Midwest 벤치마크 | 유지 |
| rebar | 철근 | D(연동됨) | SLX 대용 | 유지 |
| thermal_coal | 연료탄 | D(연동됨) | MTF=F(API2 ARA) 유럽 벤치마크 | 유지 |
| diesel_gasoil | 경유 | D(연동됨) | HO=F 환산 대용 | 유지 |
| chf/cad/aud/sgd/hkd/inr-krw | KRW 교차환율 6종 | D(연동됨) | Yahoo cross 실측이나 대용 분류 유지(기존 감사 정책) | 유지 |
| cnykrw·usdcny·aedkrw | 환율 3종 | C(재시도) | 어댑터 존재 — 당일 fetch 실패 시 데모 폴백으로 정직 표기됨 | 재시도/심볼 점검 |
| (참고) 호르무즈 통항 | — | C(운영 결정 대기) | aisstream.io 무료 스트림 — 상주 러너 필요 | HORMUZ_LIVE_INTEGRATION.md |

## 공개 UI 반영 (D7-AE-RC3)

- 카테고리 카드의 "연동 후보 N개 보기" 접힘 목록과 상태 보드의 "미연동/후보 관리" 상세는
  공개 산출물에서 **한 줄 카운트**("미연동 N종 — 내부 백로그로 관리")로 강등된다
  (`market_backlog_visible=false`, 빌더 주입). 내부 `/dashboard-preview`는 기존 그대로다.
- 어떤 분류에서도 값을 생성하지 않는다 — 연동 전 지표는 값 없음(unavailable/보고 대기)으로만 표기.
