# 호르무즈 해협 실선박 통항 — 실연동 조사 (D7-AE-RC3)

작성: 2026-07-03 · 상태: **조사 완료 · 어댑터 구현 보류(사유 명시)** · 공개 대시보드에는 데모 카드를 다시 넣지 않는다.

## 0. 결론 요약

- 사용자 제공 참조 repo **`yasumorishima/hormuz-ship-tracker`는 실존**하며(공개, default branch `master`),
  Strait of Hormuz / Persian Gulf / Gulf of Oman의 AIS 기반 선박 통항 모니터링 프로젝트가 맞다.
- 데이터 소스는 **aisstream.io 무료 WebSocket API**(키 1개, GitHub OAuth로 즉시 발급) — 기술적으로 재사용 가능한
  파이썬 수집기(단일 파일 수준)와 게이트 통항 판정 로직을 갖고 있다.
- 다만 **우리 파이프라인(GitHub Actions 배치)에는 상주 WebSocket 프로세스가 없어 즉시 연동 불가**다.
  연동하려면 상주 러너(사내 PC/서버) 또는 저자의 Hugging Face 데이터셋 주기 pull 방식이 필요하다(§5).
- 실행 순서(제안): ① aisstream.io 계정·ToS 확인 → ② 상주 러너 확보 여부 결정 → ③ leaf 어댑터 구현(§6 계획).

## 1. 참조 repo 사실 관계 (2026-07-03 GitHub API·raw 소스 직접 확인)

| 항목 | 값 |
|---|---|
| repo | `github.com/yasumorishima/hormuz-ship-tracker` (public 원본, fork 아님) |
| 설명 | "Real-time vessel tracking in the Strait of Hormuz using AIS data. Runs on Raspberry Pi 5." |
| default branch / 최종 push | `master` / 2026-05-27 (마지막 실가동 자동 커밋은 2026-03-18 — 이후 휴면 추정) |
| 구성 | Python 13모듈(src/) + Leaflet 지도 템플릿 + SQLite + Docker Compose 2컨테이너. GitHub Actions 미사용 |
| 라이선스 | README에 MIT 선언, **LICENSE 파일 부재**(GitHub 메타데이터 미인식) — 차용 시 출처 표기 권장 |
| 부산물 | 수집 데이터가 Hugging Face Dataset `yasumorishima/hormuz-ais`로 공개(176,033행 · 2026년 3~4월분) |

## 2. 데이터 소스와 필요 입력

- 제공자: **aisstream.io** — 지상 AIS 수신국 네트워크 기반 무료 WebSocket 스트림(위성 AIS 아님).
  엔드포인트 `wss://stream.aisstream.io/v0/stream` (repo `src/collector.py`에서 확인).
- 필요 자격증명: **API 키 1개**(`AISSTREAM_API_KEY`). aisstream.io에 GitHub 등 OAuth 로그인 후 발급 — 무료.
- 구독 파라미터: bounding box `[[22.0, 48.0], [30.5, 60.0]]`(페르시아만 전역+오만만),
  메시지 타입 `PositionReport`/`ShipStaticData`.
- 공식 문서상 제약: 접속 3초 내 구독 필수 · 구독 갱신 ≤1회/초 · 평균 ~300msg/s 처리 권장 · 베타 서비스로 SLA 없음.
- 재배포 ToS: 문서상 제한 조항 미발견이나 **계정 생성 전 약관 원문 확인 필요**(미확인 항목).

## 3. 참조 repo의 동작 방식 (재사용 관점 요약)

- 수집: 상시 WebSocket → 육지 필터(Natural Earth 폴리곤) → 선박당 120초 스로틀 → 5초 배치 SQLite INSERT.
- 통항 판정: 가상 게이트 3개(호르무즈 해협 (26.05,56.50)~(26.65,56.10) · Dubai/Jebel Ali · Fujairah)와
  연속 위치쌍의 **선분 교차 + 외적 부호로 IN/OUT 방향 판정**, 동일 MMSI+게이트 6시간 dedup.
- 배포: Raspberry Pi 5 위 Docker Compose 상주(수집 컨테이너 + 6시간 cron 스냅샷 push 컨테이너).
- 선박 분류: AIS type code 구간(70-79 Cargo / 80-89 Tanker 등) — LNG선 별도 구분 없음(Tanker 포함).

## 4. 한계 (저자 본인이 README에 명시 — 우리 표기에도 그대로 승계해야 함)

- **해협 중앙 ~35nm 지상 AIS 사각지대** — 위성 AIS 없이는 해협 횡단 선박이 안 보일 수 있다.
  → "통항 0척"을 봉쇄의 단독 증거로 쓰면 안 된다(하한 집계임을 항상 병기).
- 위치 데이터의 **~17%가 이상치**(102.3kn 프로토콜 센티널 + 40-99kn 수신 글리치) — 필터 로직 동반 이식 필수.
- 조사 중 발견한 정합성 이슈 2건: README의 40kn 임계 서술과 코드(≥102.3kn) 불일치 ·
  스냅샷 통계가 게이트 구분 없이 전체 통항을 합산(실제 호르무즈 게이트 통항 0인데 IN 17/OUT 9로 표기).
  → 차용 시 게이트별 분해 값(STATS.md 방식)만 신뢰한다.

## 5. 우리 환경에서의 운영 방식 검토

| 방식 | 내용 | 판정 |
|---|---|---|
| A. GitHub Actions 단독 | 배치 실행이라 상시 WebSocket 수집 불가(잡 시간 제한·상주 불가) | **불가** |
| B. 사내 상주 러너 | 사내 PC/서버에 수집기(Docker) 상주 → 주기 산출물(JSON)을 저장소/스토리지에 push → 대시보드 빌더가 읽음 | 가능(러너 확보 시) |
| C. HF 데이터셋 주기 pull | 저자의 `hormuz-ais` 데이터셋을 Actions에서 주기 다운로드 | 저코스트지만 **저자 갱신 중단 상태(3~4월분)** — 현재로선 신선도 불충족 |
| D. 보류(현행) | 뉴스 신호(hormuz 렌즈: 직접 언급 ∨ 지정학∧해상리스크 앵커)로만 관측 | **현행 유지** |

**즉시 연동 불가 사유(정확히):** ① 상주 프로세스 인프라 부재(Actions 배치 모델) ② aisstream 키 발급·ToS 확인은
운영자 계정 행위라 승인 필요 ③ 지상 AIS 하한 특성상 단독 지표로 쓰면 오독 위험(뉴스 신호와 병기 설계 필요).
셋 다 기술 난이도가 아니라 운영 결정 사항이다 — 러너와 키가 확보되면 §6 계획으로 진행한다.

## 6. 어댑터 구현 계획 (러너·키 확보 후 착수)

- 새 leaf `app/hormuz_ais.py`(가칭) — CLAUDE.md 경계 규칙 준수: 네트워크는 이 leaf만, DB/점수/발송 접근 없음.
  참조 repo의 `collector.py`(248줄, 의존성 websockets+aiosqlite 수준)와 게이트 판정(표준 수학만)을 이식.
- 산출 계약: `{gate_transits_24h: {in,out}, coverage_note: "지상 AIS 하한·해협 중앙 사각", updated_at, source}`
  — 값이 없으면 만들지 않는다(unavailable-on-failure, 기존 macro/weather leaf와 동일 정직성 계약).
- 대시보드 카드: 데모 재삽입이 아니라 **실측이 붙을 때만** 렌더(호르무즈 렌즈 뉴스와 병기, AIS 하한 캡션 필수).
- 검증기 계약 갱신 필요(구현 시점에): `verify_hormuz_maritime_snapshot`의 "app/에 AIS 모듈 없음"(E4)과
  "외부 AIS 제공자 엔드포인트 미호출"(E3)은 현행 미연동 상태를 잠근 계약이므로, 어댑터 커밋에서
  의도 문서와 함께 갱신한다(몰래 우회 금지).
- 커버리지 개선 옵션(후순위): 위성 AIS(유료) 병용 검토 — 참조 repo 로드맵 1순위와 동일 결론.

## 7. 이력

- D7-AD-X/D7-AE-RC1: repo 링크가 대화에서 유실돼 "사용자 링크 재요청 필요"로 기록·데모 카드 공개 제거.
- **D7-AE-RC3(본 문서): 사용자가 `yasumorishima/hormuz-ship-tracker`를 재제공 — 실체 확인 완료, 위 조사로 해소.**
