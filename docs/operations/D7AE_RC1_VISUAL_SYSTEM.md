# D7-AE-RC1 시각 시스템 조사 결과

## 로고 asset

**repo 내부에 현대건설 로고 asset이 없다.** 전수 검색(`find . -iname "*logo*" -o -iname
"*hyundai*" -o -iname "*hdec*.png" -o -iname "*hdec*.svg" -o -iname "*hdec*.jpg"`,
`.agents/`·`design/`·`tmp/` 미커밋 디렉터리 포함) 결과 이미지 로고 파일이 존재하지 않는다.

방침(사용자 지시 — "현대건설 로고는 repo 내부 asset이 있을 때만 사용한다. 없으면
인터넷에서 긁어오지 말고 'logo asset needed'를 docs에 기록한다"):

- **인터넷에서 로고를 가져오지 않았다.** 가짜/추정 로고를 만들지 않는다.
- 현재 마스트헤드는 추상 방사형 아이콘(동심원 SVG, `#5B7FA6`/`#3E5C80`)이다 — 현대건설
  브랜드 마크를 사칭하지 않는 중립 아이콘이며, 텍스트 워드마크("HDEC EXECUTIVE RADAR")가
  주 식별자다. 로고 asset이 없다는 사실과 무관하게 안전하다(가짜 로고가 아니라 애초에
  로고가 아닌 장식 아이콘).
- **logo asset needed**: 공식 현대건설 로고(SVG/PNG, 투명 배경)를 운영자가 제공하면
  `templates/dashboard_preview.html`의 `.brand .mark` SVG를 `<img>`로 교체한다. 그 전까지는
  현재 추상 아이콘을 유지한다.

## 폰트

- `templates/dashboard_preview.html`의 `body` font-family에 `Pretendard`를 최우선으로
  추가했다 — `scripts/build_static_report.py`가 이미 쓰는 것과 동일한 "CDN 없는 font-family
  이름 선언" 패턴(로컬에 폰트가 설치돼 있으면 쓰고, 없으면 다음 시스템 폰트로 조용히
  폴백 — 네트워크 요청 0건, `@import`/`<link>` 0건).
- `--serif`(Georgia/Times New Roman/Noto Serif KR) 변수와 그 두 사용처(마스트헤드 브랜드명,
  featured 기사 `<h2>`)를 제거했다 — 임원 보고판 톤에 맞지 않는 신문 헤드라인풍 세리프
  대신 Pretendard 산세리프로 통일한다.

## 헤더 문구

- 부제를 "현대건설 임원용 외부 신호 브리프"에서 "현대건설 임원용 신호 브리프"로
  정리했다(사용자 지정 문구, "외부" 제거).

## 색상/그림자 (조사만 — 대규모 변경 없음)

`:root` 팔레트는 D7ADY(결재문서 팔레트) 작업에서 이미 흰 카드(`--card:#FFFFFF`)·얇은
경계선(`--line`/`--hair`, 1px)·최소 그림자(`--shadow: 0 1px 2px rgba(...,0.05)`, "큰
그림자" 아님)·`--sky:#3E5C80` 남색 계열 강조로 재작업돼 있었다 — 이번 라운드에서 추가
색상 시스템 전면 개편은 하지 않았다(과잉 범위 방지). 세리프 마스트헤드가 이 팔레트와
어긋나 보이는 게 "구리다"는 인상의 핵심 원인으로 보고 그 부분만 교정했다.

## D7-AE-RC3 갱신 — 로고 확보로 'logo asset needed' 해소

위 "logo asset needed" 결론은 **해소**됐다. 사용자 지시(D7-AE-RC3)에 따라 공식 배포
채널에서 현대건설 CI를 확보해 커밋했다:

- asset: `docs/assets/brand/hdec-logo.svg` — hdec.kr **공식 사이트 헤더가 실제 사용하는
  SVG**(`https://www.hdec.kr/common/img/logo.svg`, 2026-07-03 확인). 마스터 벡터(.ai,
  8개 변형)의 공식 다운로드 경로 포함 상세 출처는 `docs/assets/brand/README.md`.
- 적용: `scripts/build_static_dashboard.py`의 `_embed_brand_logo()`가 빌드 시 data-URI로
  임베드(외부 요청 0건). asset이 없으면 여전히 로고를 생성하지 않는다(가짜 금지 원칙 불변).
- 템플릿의 placeholder 추상 SVG 아이콘(.mark)은 제거했다(사용자 지시).
