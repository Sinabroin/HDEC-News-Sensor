# D7-AD-X 시장 지표 소스 조사·연동 결과

작성: D7-AD-X corrective. **검증하지 않은 소스를 연결했다고 단정하지 않는다.**
live 연동은 `--market-mode live` 빌드 + leaf 네트워크 성공 시에만 값이 채워진다.

## 이번 커밋에서 실제 연동 시도(live 전용)

| id | 소스 | data_mode | 차트 | 비고 |
|---|---|---|---|---|
| `us_2y` | FRED `DGS2` 공개 CSV | delayed_market | 없음(단일값) | API key 불필요 · 영업일만 |
| `kr_10y` | FRED `IRLTLT01KRM156N` (OECD 월간) | delayed_market | 없음 | **일간 국고 10Y 아님** — proxy_note 부착 |

구현: `app/fred_market.py` → `scripts/build_static_dashboard._overlay_fred_live_quotes`

## 항목별 조사표

| 항목 | current_status | current_source | why_unlinked_or_chartless | source_candidate | source_type | update_frequency | chart_possible | limitation | next_action | can_implement_now |
|---|---|---|---|---|---|---|---|---|---|---|
| 니켈 | unlinked | — | 무료 실시간 LME 부재 | World Bank Pink Sheet `PNICKUSDM` | public_free / delayed | 월간(~1개월 지연) | yes(월간) | 실시간 아님·월평균 | Pink Sheet CSV 어댑터 검토 | no(미구현) |
| 철스크랩 | unlinked | — | Yahoo 심볼 없음 | FRED `WPU1012` (BLS PPI 철스크랩) | official / delayed | 월간 | yes(지수) | ₩/톤 아님·미국 PPI | 지수 표기로 FRED 연동 검토 | no |
| 국내 시멘트 | unlinked | — | 현물가 공개 API 없음 | KOSIS PPI / 한은 ECOS PPI | official / delayed | 월간 | yes(지수) | 계약가 아님 | 보고·지수 병기 | no |
| 아스팔트/역청 | unlinked | — | 국내 현물 부재 | FRED `WPU05810212` (BLS 아스팔트 PPI) | official / delayed | 월간 | yes(지수) | 미국 기준 | 지수/보고 병기 | no |
| 항공유/등유 | unlinked | — | 아시아 spot 부재 | FRED `DJFUELUSGULF` (미 걸프) | official / delayed | 일간(영업일) | yes | 미 걸프·아시아 아님 | FRED CSV 어댑터(D7-AD-X2) | no |
| 벙커유 | unlinked | — | VLSFO/380 유료 | EIA 잔사유 No.6 대용 | report_manual / delayed | 월간 | partial | 항만 bunker 아님 | 대용 표기 검토 | no |
| 원료탄 | unlinked | — | Platts/Argus 유료 | 공개 무료 벤치마크 미확인 | paid_only | — | no | Pink Sheet 연료탄은 대체 부적합 | 보고 기반 수동 | no |
| 미 국채 2Y | **live 연동 시도** | FRED DGS2 | chartless(단일값) | (동일) | official | 일간 | yes(FRED history) | 영업일·ND 결측 | history CSV 확장(D7-AD-X2) | **yes(live point)** |
| 한국 국고 10Y | **live 연동 시도(월간)** | FRED OECD | chartless | ECOS 일간(키·코드 필요) | official / delayed | FRED 월간 | yes(FRED) | 월간·지연 | ECOS 코드 확정 | **yes(live point, proxy)** |
| TWD/KRW | unlinked | — | 직접 cross thin | USD/KRW ÷ USD/TWD | public_free / delayed | EOD | yes(교차) | USD/TWD leg 미연동 | Yahoo `TWD=X` leg 추가 후 교차 | no(leg 없음) |
| 호르무즈 해협 API | unlinked (조사 완료) | — | aisstream.io 무료 WebSocket | 후보: `yasumorishima/hormuz-ship-tracker` 수집기 이식 (실체 확인 완료 — D7-AE-RC3) | public_free(키 필요) | 상시 스트림 | yes(러너 확보 시) | 상주 프로세스 필요·지상 AIS 하한 | **HORMUZ_LIVE_INTEGRATION.md 계획 따라 진행** | no |

## Hormuz GitHub 링크

- (당시) repo 전역 검색(Hormuz/hormuz/호르무즈/github): **사용자가 언급한 GitHub API 링크 없음**
- 존재하는 것: 뉴스 렌즈(`lens:hormuz`), 데모 운영 카드(AIS 미연동), `verify_hormuz_lens_relevance.py`
- ~~**다음**: 사용자에게 원본 GitHub URL 재요청~~ → **해소(D7-AE-RC3)**: 사용자가
  `yasumorishima/hormuz-ship-tracker`를 제공 — 실체 확인·실연동 조사 완료.
  상세: `docs/operations/HORMUZ_LIVE_INTEGRATION.md`

### D7-AE-RC1 재확인 (repo reference not found) — ※ D7-AE-RC3에서 해소됨(아래는 당시 기록)

사용자가 다시 "이전에 알려준 GitHub repo/reference"를 조사하라고 요청해 재검색했다 —
`.agents/`, `design/`, `tmp/`(미커밋 포함) 전체와 `docs/`, `app/`, `scripts/`, `data/`,
커밋 히스토리를 재스캔했으나 **AIS/해상교통(MarineTraffic·VesselFinder·AISStream·
AISHub·Spire·Datalastic 등) 관련 GitHub repo/API 링크는 여전히 발견되지 않았다.** 위
결론은 그대로 유효하다: **repo reference not found.**

실 라이브 소스가 없는 상태에서 데모 카드(선박 수·시간대별 통과·선종 분포·해협 모식도)가
공개 요약 대시보드(`docs/daily/dashboard-latest.html`)에 실측처럼 노출된 것이 사용자
실사용 QA 실패 사유였다 — 카드 자체가 preview 전용(`/dashboard-preview`)으로 설계됐으나
공개 빌더가 같은 템플릿을 공유해 그대로 새어나갔다. 조치: `scripts/build_static_dashboard.py`
의 `_strip_hormuz_demo_card`가 공개 정적 산출물에서만 이 카드를 통째로 제거한다. 내부
`/dashboard-preview` 디자인 도구는 원본 템플릿을 그대로 서빙하므로 데모 카드가 계속
보인다(그 라우트는 비프로덕션·미공개이므로 문제 없음). 실 AIS 소스가 확보되면(사용자가
GitHub URL을 다시 제공하거나 유료 소스를 승인하면) `_strip_hormuz_demo_card` 호출을
제거하고 source/mode/timestamp가 있는 실측 카드로 교체한다 — 검증은
`scripts/verify_hormuz_demo_removed.py`.

## 운영 버튼(uvicorn)

- `requirements.txt`에 `fastapi`, `uvicorn` 포함
- 회사망 pip 제한 시: `scripts/smoke_operator_api_local.py`(TestClient) 유지
- 실서버: `python -m uvicorn app.main:app --host 127.0.0.1 --port 8000` (선택 경로)
- 상세: `docs/operations/D7ADV_INTERNAL_RAIL_FOLD.md`
