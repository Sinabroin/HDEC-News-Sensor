# D7-AE 시장 지표 소스 감사 (Market Source Audit)

작성: 2026-07-02 · 대상: 요약 대시보드 `market_items` 48종 (docs/daily/dashboard-latest.html)
원칙: **삭제·강등 결정 전에 소스 사실부터 확정한다.** 모든 "무료 소스 있음" 판정은
2026-07-02 실제 GET 프로빙으로 확인했다(추정 금지 · lesson: macro-live-source-probe).

## 0. 판정 기준

| 최종 판단 | 의미 |
|---|---|
| `keep_live` | 공개·무료(지연) 시세 소스가 실측 검증됨 — 자동 갱신 + 기간 차트 |
| `keep_proxy_with_caveat` | 글로벌 벤치마크 **대용**(정확한 국내/현물가 아님)을 라벨과 함께 유지 |
| `keep_manual_report_only` | 무료 소스 없음 — 값 없이(보고 대기) 유지, 보고 입력 시 as-of 필수 |
| `remove_from_dashboard` | 임원 대시보드 사업 질문이 약함 — 제거 대상 |
| `move_to_backlog` | 소스 후보는 있으나 미구현/미검증 — 관찰 후보(값 생성 금지) |

구분해서 기록한다: ① 기술적으로 불가(소스 자체 없음) ② 유료 전용 ③ **무료 소스가
존재하나 미구현** ④ proxy만 가능(진짜 가격 아님) ⑤ 대시보드 가치 부족.

## 1. 감사에서 드러난 핵심 결함 (D7-AE에서 수정)

1. **정적 표시값이 연동값처럼 보였다.** 템플릿 데모 값(예: CHF/KRW 1,712.3)이 live
   게시본에서도 그대로 남아 배지(대용/보고)만으로는 "갱신되는 값"처럼 읽혔다.
   실측 프로빙 결과 실제 CHF/KRW는 1,919 — **12% 괴리의 죽은 숫자**였다.
   → 수정 A: 무료 소스가 검증된 5종(CHF/SGD/HKD/INR/AED·KRW)은 실측 연동으로 승격
     (`app/market_history.py` D7-AE spec — 심볼 실 GET 검증).
   → 수정 B: 소스가 없는 정적 값(아연·두바이유·JKM·SAR/QAR·미 CPI/기준금리)은 live
     빌드에서 값을 제거하고 미연동 관찰 후보로 강등(`_demote_unbacked_market_values`).
     값은 만들지 않는다 — 제거만 한다.
2. **미연동·수동 지표의 급이 섞여 있었다.** 연동(값 보유) / 보고 대기(값 없음·수동
   확인) / 미연동(소스 필요)을 아래 표의 최종 판단으로 고정한다.

## 2. 지표별 감사표

표기: 소스열의 ✔=이 저장소에서 실측 검증된 무료 소스, ▲=무료지만 대용(proxy),
✖=무료 소스 없음(유료 전용 또는 부재). "자동"=워크플로 live 빌드에서 자동 갱신.

### 주식 (equities · 1종, D7-AE-RC1 신규)

사용자 실사용 QA: "현대건설 주가도 자동 갱신 그래프가 있어야 한다." KRX 상장주는
공개·무료 실시간 소스가 없어 지연 시세(Yahoo chart, 무인증)로 연동한다 — 다른
delayed_market 종목과 동일 정직성 계약(체결값 아님 명시).

| id | 지표 | 사업 질문 | 소스 사실 | 성격 | 자동/차트 | 중요도 | 최종 판단 |
|---|---|---|---|---|---|---|---|
| hdec_stock | 현대건설(000720) | 자사주가·시가총액·시장 신뢰도 프록시 | ✔ Yahoo `000720.KS`(프로브 실측 2026-07-02 115,200원, currency=KRW) | delayed | ○/○ | 상 | keep_live |

### 환율 (fx · 19종)

| id | 지표 | 사업 질문 | 소스 사실 | 성격 | 자동/차트 | 중요도 | 최종 판단 |
|---|---|---|---|---|---|---|---|
| usdkrw | USD/KRW | 해외 매출·원가 환산, 외화 PF | ✔ Yahoo `USDKRW=X` | delayed | ○/○ | 상 | keep_live |
| jpykrw | JPY/KRW | 일제 자재·장비 조달 | ✔ Yahoo `JPYKRW=X` | delayed | ○/○ | 중 | keep_live |
| eurkrw | EUR/KRW | 유럽 장비·라이선스 | ✔ Yahoo `EURKRW=X` | delayed | ○/○ | 중 | keep_live |
| cnykrw | CNY/KRW | 중국 자재 조달 | ✔ Yahoo `CNYKRW=X` | delayed | ○/○ | 중 | keep_live |
| gbpkrw | GBP/KRW | 영국권 계약 | ✔ Yahoo `GBPKRW=X` | delayed | ○/○ | 하 | keep_live |
| audkrw | AUD/KRW | 호주 원자재권 | ✔ Yahoo `AUDKRW=X` | delayed | ○/○ | 하 | keep_live |
| cadkrw | CAD/KRW | 북미권 | ✔ Yahoo `CADKRW=X` | delayed | ○/○ | 하 | keep_live |
| chfkrw | CHF/KRW | 스위스 장비·보험 | ✔ Yahoo `CHFKRW=X` (프로브 1,919.2) | delayed | ○/○ | 하 | **keep_live (D7-AE 승격)** |
| sgdkrw | SGD/KRW | 싱가포르 허브 거래 | ✔ Yahoo `SGDKRW=X` (프로브 1,197.1) | delayed | ○/○ | 중 | **keep_live (D7-AE 승격)** |
| hkdkrw | HKD/KRW | 홍콩 금융 거래 | ✔ Yahoo `HKDKRW=X` (프로브 197.07) | delayed | ○/○ | 하 | **keep_live (D7-AE 승격)** |
| inrkrw | INR/KRW | 인도 현장 인건·조달 | ✔ Yahoo `INRKRW=X` (프로브 16.27) | delayed | ○/○ | 중 | **keep_live (D7-AE 승격)** |
| aedkrw | AED/KRW | UAE 현장 수금·지출 | ✔ Yahoo `AEDKRW=X` (프로브 421.4) | delayed | ○/○ | **상(중동)** | **keep_live (D7-AE 승격)** |
| sarkrw | SAR/KRW | 사우디 현장 수금·지출 | ✖ Yahoo 심볼 없음(프로브 Not Found) · USD 페그 3.75 → USDKRW÷3.75 파생 가능 | 파생 proxy 후보 | ✖(현재) | **상(중동)** | move_to_backlog(페그 파생 proxy 구현 대기 · 정적값은 live에서 제거) |
| qarkrw | QAR/KRW | 카타르 현장 | ✖ Yahoo 심볼 없음(프로브 Not Found) · USD 페그 3.64 파생 가능 | 파생 proxy 후보 | ✖(현재) | 중 | move_to_backlog(동일) |
| twdkrw | TWD/KRW | 대만(반도체 EPC권) | ✔ Yahoo `TWDKRW=X` (프로브 48.53) — **무료 소스 존재·미구현** | delayed 후보 | ✖(현재) | 하 | move_to_backlog(중요도 낮아 승격 보류 · 값 없음 정직 유지) |
| dxy | 달러 인덱스 | 달러 강세 방향성 | ✔ Yahoo `DX-Y.NYB` | delayed | ○/○ | 중 | keep_live |
| eurusd | EUR/USD | 글로벌 crosscheck | ✔ Yahoo `EURUSD=X` | delayed | ○/○ | 하 | keep_live |
| usdjpy | USD/JPY | 엔저·조달 타이밍 | ✔ Yahoo `USDJPY=X` | delayed | ○/○ | 하 | keep_live |
| usdcny | USD/CNY | 위안 방향성 | ✔ Yahoo `USDCNH=X` | delayed | ○/○ | 하 | keep_live |

### 유가·정제유 (oil_refined · 7종)

| id | 지표 | 사업 질문 | 소스 사실 | 성격 | 자동/차트 | 중요도 | 최종 판단 |
|---|---|---|---|---|---|---|---|
| wti | WTI | 발주환경(중동 재정력)·연료비 | ✔ Yahoo `CL=F` | delayed | ○/○ | 상 | keep_live |
| brent | 브렌트 | 상동(국제 기준) | ✔ Yahoo `BZ=F` | delayed | ○/○ | 상 | keep_live |
| dubai_crude | 두바이유 | 중동 판가 기준 | ✖ Platts 유료 전용 · 무료 현재성 소스 없음 | manual | ✖/✖ | 중 | keep_manual_report_only(값 null · live 정적값 제거) |
| diesel_gasoil | 경유(가스오일) | 장비 연료비 | ▲ Yahoo `HO=F`(난방유)×42 환산 — 아시아 가스오일 아님 | proxy | ○/○ | 중 | keep_proxy_with_caveat |
| gasoline | 휘발유(RBOB) | 연료비 방향성 | ✔ Yahoo `RB=F` | delayed | ○/○ | 하 | keep_live |
| jet_kerosene | 항공유(등유) | 물류 항공비(간접) | ▲ FRED `DJFUELUSGULF`(미 걸프·일간) 후보 — 아시아 아님 | proxy 후보 | ✖(현재) | **하** | remove_from_dashboard(사업 질문 약함 · 물리 제거는 rail_navigation MARKET_IDS 계약 갱신과 함께 D7-AF — 현재 값 0·하단 접힘) |
| bunker_fuel | 벙커유 | 해운 물류비(간접) | ✖ 항만 VLSFO 유료 · EIA 잔사유 월간 대용뿐 | proxy 후보 | ✖(현재) | **하** | remove_from_dashboard(동일 처리) |

### 가스·LNG (gas_lng · 3종)

| id | 지표 | 사업 질문 | 소스 사실 | 성격 | 자동/차트 | 중요도 | 최종 판단 |
|---|---|---|---|---|---|---|---|
| lng_jkm | LNG JKM | 아시아 LNG 도입가 → LNG 플랜트 발주환경 | ✖ Platts JKM 유료 전용 | manual | ✖/✖ | 상 | keep_manual_report_only(값 null · live 정적값 제거 · TTF/헨리허브가 방향성 대체) |
| ttf_gas | TTF 가스 | 유럽 가스 → 글로벌 LNG 방향성 | ✔ Yahoo `TTF=F` | delayed | ○/○ | 중 | keep_live |
| henry_hub | 헨리허브 | 미국 가스 기준 | ✔ Yahoo `NG=F` | delayed | ○/○ | 중 | keep_live |

### 원자재·금속 (base_metals · 4종)

| id | 지표 | 사업 질문 | 소스 사실 | 성격 | 자동/차트 | 중요도 | 최종 판단 |
|---|---|---|---|---|---|---|---|
| copper | 구리 | 전기·배관 자재 원가 | ✔ Yahoo `HG=F`(COMEX×2204.62→USD/t) | delayed | ○/○ | 상 | keep_live |
| aluminum | 알루미늄 | 커튼월·창호 원가 | ✔ Yahoo `ALI=F` | delayed | ○/○ | 중 | keep_live |
| nickel | 니켈 | 스테인리스·특수강 | ✖ LME 유료 · Yahoo 신뢰 가능한 무료 심볼 없음 | — | ✖/✖ | 하 | move_to_backlog(값 없음 정직 유지) |
| zinc | 아연 | 아연도금 강재 | ✖ LME 유료 · 무료 심볼 미검증 — **기존 정적 2,710은 죽은 값** | — | ✖/✖ | 하 | move_to_backlog(live 정적값 제거) |

### 철강·건자재 (steel_materials · 7종)

| id | 지표 | 사업 질문 | 소스 사실 | 성격 | 자동/차트 | 중요도 | 최종 판단 |
|---|---|---|---|---|---|---|---|
| iron_ore | 철광석 | 철강 원가 선행 | ▲ Yahoo `TIO=F`(62% CFR China) — 글로벌 벤치마크 | proxy | ○/○ | 중 | keep_proxy_with_caveat |
| hrc_steel | 열연강판 | 강재 원가 | ▲ Yahoo `HRC=F`(미 Midwest) — 국내 유통가 아님 | proxy | ○/○ | 중 | keep_proxy_with_caveat |
| rebar | 철근 | 골조 원가 핵심 | ▲ Yahoo `SLX`(철강 ETF pt) — **가격 아닌 방향성 대용** | proxy | ○/○ | **상** | keep_proxy_with_caveat(국내 유통가는 유료 — 백로그: 스틸데일리/KOSIS 보고 입력) |
| scrap_steel | 철스크랩 | 전기로 원가 | ✖ 국내 유통가 무료 시세 없음(업계지 유료) | — | ✖/✖ | 중 | move_to_backlog |
| cement | 국내 시멘트 | 콘크리트 원가 | ✖ 고시·보고 기반(협회/양회) — 시세 아님 | manual | ✖/✖ | 상 | keep_manual_report_only(현행 값 null 유지 — 보고 입력 시 as-of 필수) |
| lumber | 목재 | 가설·형틀 자재 | ✔ Yahoo `LBR=F` | delayed | ○/○ | 하 | keep_live |
| bitumen | 아스팔트(역청) | 도로 포장 원가 | ✖ 무료 현재성 소스 없음(유가 연동 파생은 별도 모델 필요) | — | ✖/✖ | 중 | move_to_backlog |

### 석탄 (coal · 2종)

| id | 지표 | 사업 질문 | 소스 사실 | 성격 | 자동/차트 | 중요도 | 최종 판단 |
|---|---|---|---|---|---|---|---|
| thermal_coal | 연료탄 | 발전 연료 → 전력단가 | ▲ Yahoo `MTF=F`(API2 ARA·유럽 인도) | proxy | ○/○ | 하 | keep_proxy_with_caveat |
| coking_coal | 원료탄 | 제철 원가 → 강재가 선행 | ✖ 무료 심볼 미검증 | — | ✖/✖ | 하 | move_to_backlog |

### 금리·물가 (rates_inflation · 6종)

| id | 지표 | 사업 질문 | 소스 사실 | 성격 | 자동/차트 | 중요도 | 최종 판단 |
|---|---|---|---|---|---|---|---|
| us_10y | 미 국채 10Y | 글로벌 할인율·PF 금리 방향 | ✔ Yahoo `^TNX` | delayed | ○/○ | 상 | keep_live |
| us_5y | 미 국채 5Y | 중기 금리 | ✔ Yahoo `^FVX` | delayed | ○/○ | 중 | keep_live |
| us_2y | 미 국채 2Y | 통화정책 기대 | ✔ FRED `DGS2` 공개 CSV(일간·단일값) | delayed(차트 없음) | ○/✖ | 중 | keep_live(단일값 — 차트는 백로그) |
| kr_10y | 한국 국고 10Y | 국내 PF·조달 금리 | ▲ FRED `IRLTLT01KRM156N`(OECD 월간 장기금리) — 일간 국고 10Y 아님 | proxy(월간) | ○/✖ | **상** | keep_proxy_with_caveat(백로그: KOFIA 일간 무료 소스 검토) |
| us_cpi_row | 미 CPI(YoY) | 인플레 → 연준 경로 | ✔ FRED `CPIAUCSL`로 YoY 파생 가능 — **무료 소스 존재·미구현** | manual(현재) | ✖(현재) | 중 | move_to_backlog(FRED 파생 구현 대기 · live 정적값 제거) |
| us_base_rate | 미 기준금리 | 글로벌 금리 기준 | ✔ FRED `DFEDTARU` — **무료 소스 존재·미구현** | manual(현재) | ✖(현재) | 중 | move_to_backlog(동일) |

## 3. 집계

- keep_live: **28** (기존 22 + D7-AE 승격 5: chf/sgd/hkd/inr/aed·KRW + D7-AE-RC1 hdec_stock)
- keep_proxy_with_caveat: **6** (철광석·열연·철근SLX·경유HO환산·연료탄API2·KR10Y OECD)
- keep_manual_report_only: **3** (시멘트·두바이유·JKM — 전부 값 null·as-of 없는 정적값 금지)
- move_to_backlog: **10** (SAR/QAR/TWD KRW·니켈·아연·철스크랩·역청·원료탄·미 CPI·미 기준금리)
- remove_from_dashboard: **2** (항공유·벙커유 — 물리 제거는 verifier 유니버스 계약 갱신과 함께 D7-AF)

## 4. 정직성 규칙 (검증기 `verify_market_source_audit.py`가 잠금)

1. proxy를 현재가처럼 표시하지 않는다 — proxy_market 라벨 + "글로벌 벤치마크 대용" 명시.
2. live 게시본에서 값이 있는 행은 반드시 소스 근거(history_source 또는 value_source)를
   갖는다. 근거 없는 정적 값은 빌드 단계에서 강등된다(값 제거 · 생성 금지).
3. unlinked(값 없음) 지표는 linked와 같은 급으로 노출하지 않는다 — 하단 "미연동 관찰
   후보" 접힘 영역 유지(D7-U 계약).
4. 이 문서의 최종 판단 열거값과 대시보드 지표 id 집합이 어긋나면 검증기가 FAIL한다
   (감사표 없는 지표 추가 금지).
