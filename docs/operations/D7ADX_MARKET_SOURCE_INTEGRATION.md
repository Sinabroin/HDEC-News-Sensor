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
| 호르무즈 해협 API | unlinked (데모 카드) | 템플릿 데모 | — | 후보: 사용자 원본 GitHub URL 재제공 필요(repo 내 링크 없음) | 미확정 | 미확정 | 미확정 | AIS/API 키·정책 한계 | **사용자 링크 재요청** → D7-AD-X2 | no |

## Hormuz GitHub 링크

- repo 전역 검색(Hormuz/hormuz/호르무즈/github): **사용자가 언급한 GitHub API 링크 없음**
- 존재하는 것: 뉴스 렌즈(`lens:hormuz`), 데모 운영 카드(AIS 미연동), `verify_hormuz_lens_relevance.py`
- **다음**: 사용자에게 원본 GitHub URL 재요청 → `docs/operations/D7ADX_HORMUZ_API_CANDIDATE.md`에 기록 후 D7-AD-X2

## 운영 버튼(uvicorn)

- `requirements.txt`에 `fastapi`, `uvicorn` 포함
- 회사망 pip 제한 시: `scripts/smoke_operator_api_local.py`(TestClient) 유지
- 실서버: `python -m uvicorn app.main:app --host 127.0.0.1 --port 8000` (선택 경로)
- 상세: `docs/operations/D7ADV_INTERNAL_RAIL_FOLD.md`
