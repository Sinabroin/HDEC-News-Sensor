# Brand Assets — 현대건설 공식 CI

이 폴더는 대시보드 마스트헤드에 임베드되는 **공식 현대건설 CI** asset을 보관한다.
임의 제작 로고(가짜 로고)는 두지 않는다 — 공식 배포 채널에서 받은 원본만 커밋한다.

## 파일

| 파일 | 내용 | 출처 |
|---|---|---|
| `hdec-logo.svg` | 영문 가로형(심볼+HYUNDAI ENGINEERING & CONSTRUCTION 워드마크), 벡터, viewBox 155×27 | hdec.kr 공식 사이트 헤더가 실제 사용하는 파일 |

## 출처·확보 경로 (2026-07-03 확인)

- **공식 CI 배포 페이지**: `https://www.hdec.kr/kr/company/vision.aspx` (회사소개 > 비전, "[CI 다운로드]")
  - 마스터 벡터 원본(.ai, 가로/세로 × Positive/Negative × 국문/영문 8개 변형 수록):
    `https://www.hdec.kr/downloadfile/HDEC_CI_AI/현대건설CI.ai`
- **사이트 헤더 SVG(이 폴더의 `hdec-logo.svg` 원본)**: `https://www.hdec.kr/common/img/logo.svg`
  - 현행 공식 브랜드 컬러 기준값: 심볼 노랑 `#FFB71B` · 초록 `#00953B` · 워드마크 남색 `#002D74`
- 국문 가로형 래스터(참고): `https://www.hdec.kr/common/img/company/k-logo.png` (232×51 — 저해상도라 미사용)
- hdec.kr은 기본 curl User-Agent를 차단한다 — 브라우저 UA 헤더를 붙이면 정상 수신된다.
- 자사(현대건설) CI의 사내 시스템 사용이므로 권리 이슈 없음. 외부 배포물에 쓸 경우 CI 가이드 준수.

## 적용 방식

`scripts/build_static_dashboard.py`의 `_embed_brand_logo()`가 빌드 시 이 SVG를
**data-URI(base64)** 로 마스트헤드에 임베드한다:

- asset이 없으면 로고를 생성하지 않고 텍스트 브랜드만 남긴다(경고 출력 · 가짜 금지).
- data-URI라 산출물은 외부 요청 0건(self-contained · 기사 href 외 외부 URL 0건 정책 유지).
- 교체 시 이 폴더의 `hdec-logo.svg`만 바꾸고 대시보드를 재생성하면 된다.
