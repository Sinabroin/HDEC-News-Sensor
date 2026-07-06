# D7-AD-V 카테고리 탐색을 기존 좌측 목차로 흡수 (구현 노트)

D7-AD-U를 실제 UI로 확인한 뒤의 course-correction이다. D7-AD-U가 만든 **새 상단 목차(railNav "전체 탐색")**
와 **본문 위 뉴스 카테고리 칩 바(newsCatFilter)**는 사용자가 원한 것이 아니어서 제거하고, **기존 왼쪽
목차(`#lensnav`) 자체를 필터/탐색 장치로 개선**했다. 화면은 이제 **왼쪽=필터/탐색/운영, 오른쪽=선택 결과**다.

## 바뀐 것

1. **`#railNav`("전체 탐색", 오늘/뉴스/현장/시장/기상/운영) 제거.** 별도 상단 목차를 만들지 않는다.
   좌측 목차 하나가 단일 진입점이다(관련 CSS `.railtitle`/`.railnav*`도 제거).
2. **좌측 목차(`#lensnav`)에 탐색 그룹 흡수** — 클릭 라우팅은 `#lensnav` 핸들러가 분기한다.
   - 기본 렌즈(전체 종합/즉시/신규/AI) · 사업영역 렌즈(토목…안전·품질) → 기존 카드 필터(`data-filter`).
   - **뉴스 분류(`.nav.navcat` `data-acc=order|finance|policy|competitor|brand|global_press`)** →
     `openNewsCategory`가 뉴스 탭 전환 + 해당 브리핑 `<details class="acc-sec" data-acc>`만 펼쳐 본문
     기사 목록을 그 분류로 바꾼다. data-acc 키는 서버 렌더 아코디언 키(`app/briefing.py`)와 1:1.
   - **시장 모니터링(`.nav.navmkt` `data-market=base_metals|steel_materials|oil_refined|gas_lng|coal|rates_inflation`)** →
     `openMarketCategory`가 시장 탭 전환 + 해당 카테고리 카드(`#mcat-<cat>`)로 스크롤(환율은 금리·환율 카드 인접).
   - **기상(`.nav.navwx`)** → `openWeatherRisk`가 시장 탭 전환 + `#siteWeatherCard`(명일 정오 시공 리스크)로
     스크롤. 기상은 뉴스 카테고리 필터에서 제외.
   - **운영자 실행**은 별도 '운영' 목차 항목을 만들지 않고 좌측 목차 하단 compact 카드(`#opctl`)로 유지.
3. **본문 위 `#newsCatFilter` 칩 바 제거 + 헤더 축소.** "뉴스 탐색 필터/카테고리를 누르면…" 큰 안내를
   없애고 조용한 맥락 라벨만 남긴다(카테고리 탐색은 좌측 목차가 담당).
4. **운영자 버튼 로컬 계약 검증(smoke).** `scripts/smoke_operator_api_local.py` — uvicorn 없이
   `app/operator_gateway.py`를 직접 호출해 무설정=fail-closed, 승인 경로만 dispatch(스텁, 네트워크 0건)를
   검증한다. `requirements.txt`에는 이미 `fastapi`/`uvicorn`이 선언돼 있으나 회사망/오프라인에서 미설치일
   수 있어, 스모크는 표준 라이브러리 + `app.config`/`app.operator_gateway`만으로 동작한다.
5. **시장 지표 재조사.** "무료 소스 없음" 단정을 반려하고 항목별 후보·한계·다음 액션을
   `MARKET_REASON`(상태 보드)과 `docs/operations/D7ADV_MARKET_SOURCE_DISCOVERY.md`에 기록(값 생성 없음).
6. **기상 계약 유지 + 조사 보강.** UI는 명일 정오 시공 리스크 그대로. 실연동은 D7-AD-W로 분리하고
   확인된 엔드포인트/필드를 `docs/operations/D7ADS_SITE_WEATHER_INTEGRATION_PLAN.md`에 보강.

## 로컬 검증 (오프라인 · 네트워크/비밀값 0건)

```bash
python3 -m py_compile app/*.py scripts/*.py
python3 scripts/verify_dashboard_ia_consolidation.py
python3 scripts/verify_dashboard_existing_rail_navigation.py   # D7-AD-V 신규(워크플로 등록 → 26번째)
python3 scripts/verify_site_watch_nav_tree.py
python3 scripts/verify_operator_api_activation_readiness.py
python3 scripts/verify_operator_controls.py
python3 scripts/verify_dashboard_accordion_sections.py
python3 scripts/verify_email_teams_alert.py
GITHUB_ACTIONS=true python3 scripts/verify_email_teams_alert.py
python3 scripts/smoke_operator_api_local.py
git diff --check
```

## 운영자 버튼 브라우저 클릭 테스트 (로컬 HTML 빌드)

빌더는 env를 직접 읽지 않고 `--operator-api-base`(공개값 · 비밀값 아님)로만 base를 받는다.

```bash
# 1) 공개(기본) — base 미설정: 세 버튼 disabled + '운영자 서버 미연결' 한 줄
python3 scripts/build_static_dashboard.py --output /tmp/dash_public.html

# 2) 운영자/로컬 활성화 — base 주입: 세 버튼(수집/텔레그램/Teams) 활성화(운영 API로 POST)
python3 scripts/build_static_dashboard.py --output /tmp/dash_operator.html \
    --operator-api-base https://<operator-api-host>

# 활성화 확인(가짜 없이 계약만)
grep -o 'collectBtn.disabled = false\|sendBtn.disabled = false\|teamsBtn.disabled = false' /tmp/dash_operator.html
grep -o '/api/operator/collect\|/api/operator/send\|/api/operator/send-teams' /tmp/dash_operator.html
```

실제 클릭 실행까지 가려면 운영 API 서버(app.main)를 배포하고 서버 env
(`GH_OPERATOR_TOKEN`·`OPERATOR_SHARED_SECRET`|`OPERATOR_PIN`·`OPERATOR_REPO`·`OPERATOR_ALLOWED_ORIGINS`)와
repo variable `OPERATOR_API_BASE`, GitHub Secrets(Gmail SMTP·`TEAMS_CHANNEL_EMAIL`)를 설정해야 한다.
서버가 없을 때는 위 smoke가 uvicorn 없이 계약(무설정 fail-closed·승인 경로만 dispatch)을 대신 검증한다.
**실제 브라우저 클릭 테스트**(collect/telegram/Teams POST)는 `fastapi`/`uvicorn` 로컬 설치 또는
운영 API 배포 후 `--operator-api-base` 빌드로 진행한다.

`docs/daily/*.html`은 hand-edit하지 않는다(빌더/live publish 워크플로가 재생성). 이 작업은
템플릿·빌더 앵커·운영 게이트웨이 스모크·시장/기상 조사 문서·검증기만 수정한다.
