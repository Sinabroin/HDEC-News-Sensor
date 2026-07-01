# D7-AD-U 단일 내부 대시보드 운영화 (구현 노트)

D7-AD-R까지의 레이아웃을 실제 UI로 확인한 뒤 방향을 수정한 작업이다. 제품은 **내부용 단일
대시보드 1개**이고, 공개 GitHub Pages는 sanitized 배포 산출물일 뿐이다. 공개 산출물에 실제
현장명·secret·token이 노출되지 않는 안전장치는 그대로 유지한다(값을 만들지 않는 정직 상태 포함).

## 바뀐 것

1. **왼쪽 rail = 1차 IA 네비.** `전체 탐색`(오늘/뉴스/현장/시장/기상/운영, `#railNav`)을 왼쪽 rail
   상단에 두고, 그 아래 렌즈 필터·현장 워치리스트가 세부 탐색으로 이어진다. 클릭 시 해당 본문
   영역으로 탭 전환 + 스크롤한다(네트워크/난수 0건).
2. **운영자 실행을 화면 하단 → 왼쪽 rail 하단(`railcol`)으로 이동.** 하단에 덩그러니 두지 않는다.
   미설정 시 `운영자 서버 미연결` 한 줄만 보이고, 장문 안내/PIN은 접이식(`opctl-more`)으로 접는다.
3. **Teams 채널 전송을 실제 배선.** 운영 API에 `POST /api/operator/send-teams`가 생겼고
   (`app/main.py` → `operator_gateway.trigger_teams`), 승인 PIN 검증 후
   `email-alert.yml`을 `approve_send_email=true`·`send_to_teams=true`로 `workflow_dispatch`한다.
   대시보드 Teams 버튼은 collect/telegram과 동일하게 운영 API base 주입 시 활성화된다. 정적 HTML은
   GitHub Actions를 직접 호출하지 않는다(서버가 dispatch 소유).
4. **카테고리별 브리핑을 뉴스 탐색 필터로 통합.** `뉴스 탐색 필터`(`#newsCatFilter`) 칩을 누르면
   해당 카테고리 섹션만 펼쳐 본문 뉴스 목록이 바뀐다. 브리핑 8섹션 `<details data-acc>` 구조는
   이메일/Teams 본문 파리티·검증기 계약을 위해 유지한다.
5. **기상 = 명일 정오 시공 리스크(`#siteWeatherCard`).** 뉴스 accordion이 아니라 명일(D+1) 정오
   12:00 기준 지역×항목 표다(강수확률/예상 강수량/풍속/돌풍/폭염·한파/특보/작업 리스크 등급 +
   시공 영향: 우천·강풍·고소작업·타워크레인·콘크리트 타설·외장·방수·토공). 실제 소스가 없으므로
   전 항목 `미연동` — 가짜 날씨 값을 만들지 않는다. **UI·데이터 계약만** 만들고 실제 기상청/Open-Meteo
   연동은 **D7-AD-V**로 분리한다(계획: `D7ADS_SITE_WEATHER_INTEGRATION_PLAN.md`).
6. **시장 지표 상태 + 미연동 사유.** `#marketStatusBoard`가 4개 상태(연동 완료 / 보고·수동 확인 /
   미연동 후보 / 우선 연동 필요)로 정리하고, 우선/미연동 항목은 `왜 미연동인지`(`MARKET_REASON`)를
   함께 노출한다. 사용자 지정 10개 항목(니켈·철스크랩·국내 시멘트·아스팔트/역청·항공유(등유)·벙커유·
   원료탄·미 국채 2Y·한국 국고 10Y·TWD/KRW)은 공개 무료 실시간 소스가 없어 값을 만들지 않는다
   (상세 계획: `D7ADT_MARKET_SOURCE_BACKLOG.md`).

## 운영 버튼 활성화 테스트 (로컬/운영자 빌드)

빌더는 env를 직접 읽지 않고 `--operator-api-base`(공개값 · 비밀값 아님)로만 base를 받는다.
base를 주입하면 세 버튼(수집/텔레그램/Teams)이 활성화된 대시보드를 산출한다.

```bash
# 1) 공개(기본) — base 미설정: 세 버튼 disabled + '운영자 서버 미연결' 한 줄
python3 scripts/build_static_dashboard.py --output /tmp/dash_public.html

# 2) 운영자/로컬 활성화 테스트 — base 주입: 세 버튼 활성화(운영 API로 POST)
python3 scripts/build_static_dashboard.py --output /tmp/dash_operator.html \
    --operator-api-base https://<operator-api-host>

# 활성화 확인(가짜 없이 계약만): 세 버튼 활성화 코드 + 세 endpoint 주입
grep -o 'collectBtn.disabled = false\|sendBtn.disabled = false\|teamsBtn.disabled = false' /tmp/dash_operator.html
grep -o '/api/operator/collect\|/api/operator/send-telegram\|/api/operator/send-teams' /tmp/dash_operator.html
```

실제 클릭 실행까지 가려면 운영 API 서버(app.main)를 배포하고 서버 env
(`GH_OPERATOR_TOKEN`·`OPERATOR_SHARED_SECRET`|`OPERATOR_PIN`·`OPERATOR_REPO`·`OPERATOR_ALLOWED_ORIGINS`)와
repo variable `OPERATOR_API_BASE`, GitHub Secrets(Gmail SMTP·`TEAMS_CHANNEL_EMAIL`)를 설정해야 한다
(절차: `D7AD_EMAIL_TEAMS_OPERATOR_ACTIVATION.md`). `dispatched` 응답은 GitHub가 실행 요청을 수락했다는
뜻이며, 실제 수집/발송·Teams 채널 도착은 해당 Actions run과 채널에서 별도로 확인한다.

## 오프라인 검증

```bash
python3 -m py_compile app/*.py scripts/*.py
python3 scripts/verify_dashboard_ia_consolidation.py
python3 scripts/verify_site_watch_nav_tree.py
python3 scripts/verify_operator_api_activation_readiness.py
python3 scripts/verify_operator_controls.py
python3 scripts/verify_dashboard_accordion_sections.py
python3 scripts/verify_email_teams_alert.py
GITHUB_ACTIONS=true python3 scripts/verify_email_teams_alert.py
git diff --check
```

`docs/daily/*.html`은 hand-edit하지 않는다(빌더/live publish 워크플로가 재생성). 이 작업은
템플릿·빌더·운영 게이트웨이·검증기만 수정한다.
