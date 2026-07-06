# D7-AG-3 — 운영자 실행 버튼 활성화 + 호르무즈 통과 선박량 실연동

D7-AG-2를 대체한다(그 문서는 PIN 기반·호르무즈 미연동 상태 기록). 본 문서는 최종 계약이다.

## A. 운영자 실행 버튼 (실제 fetch · PIN 제거 · 서버 앞단 인증)

### 동작 구조
```
브라우저 버튼 클릭
  → fetch(operator_api_base + /api/operator/{collect|send|send-teams}, {method:POST, credentials:include})
  → Operator API(app.operator_api:app)가 서버측 인가(신원·Origin·레이트)
  → 통과 시 GitHub workflow_dispatch (수집=scheduled-live-refresh.yml · 텔레그램=telegram-notify.yml ·
    Teams=email-alert.yml)
  → run_id/run_url/workflow_url 반환 → UI에 '실행 요청 접수' + run 링크
```
- 링크 fallback('GitHub Actions 열기' 등) 없음. 미연결이어도 3개 실행 버튼 유지 + '미연결' 명시.
- public HTML에는 token/PIN/secret이 없다. 브라우저는 base+경로로만 POST한다.

### PIN 재검토 결론 — 브라우저 PIN 제거, 보호를 서버 앞단으로 이관
공개 페이지에 승인 PIN을 입력받는 설계는 약하다(어깨너머·피싱·단일 공유값·신원 없음·마찰). 사용자도
불필요하다고 판단했다. 대체 보호(우선순위 A):
1. **Operator API 호스트를 인증된 경계 뒤에 배포** — Cloudflare Access / Vercel Protection / 사내 SSO /
   Basic Auth. 경계가 인증된 운영자 신원 헤더(`OPERATOR_ACCESS_HEADER`, 기본 `Cf-Access-Authenticated-User-Email`)를 주입.
2. **서버측 인가**(`app/operator_gateway.authorize`) — 신원이 `OPERATOR_ALLOWED_USERS` 허용목록에 있어야
   하고, `Origin`이 `OPERATOR_ALLOWED_ORIGINS`에 있어야 하며, 분당 레이트리밋(`OPERATOR_RATE_LIMIT_PER_MIN`)을
   통과해야 한다. 어느 하나라도 미충족이면 트리거하지 않는다(fail-closed).
3. 레거시 단순 배포용 `shared_secret` 모드(Basic Auth 뒤 등)도 지원하되 브라우저 UI에는 노출하지 않는다.

> 보호 장치 없이 공개 URL에서 누구나 실행/발송하게 두지 않는다. `OPERATOR_ACCESS_MODE`가 미설정이면
> `is_configured()`가 False → 모든 액션 not_configured(fail-closed).

### 프로덕션 배포 (인증된 운영자 호스트가 필요 — 외부 상태 변경이므로 운영자 승인 필요)
```bash
# 1) 최소 FastAPI 엔트리포인트 배포 (pyproject.toml: entrypoint = "app.operator_api:app")
npx vercel@latest login && npx vercel@latest --prod       # 또는 사내 호스트 + Cloudflare Access

# 2) 서버 환경변수 (shared shell history에 값 직접 노출 금지)
#    GH_OPERATOR_TOKEN      = Actions write 권한만 가진 fine-grained PAT/GitHub App 토큰
#    OPERATOR_REPO          = Sinabroin/HDEC-News-Sensor
#    OPERATOR_ACCESS_MODE   = edge
#    OPERATOR_ACCESS_HEADER = cf-access-authenticated-user-email   # 경계가 주입하는 신원 헤더
#    OPERATOR_ALLOWED_USERS = ops1@hdec.co.kr,ops2@hdec.co.kr      # 허용 운영자 신원
#    OPERATOR_ALLOWED_ORIGINS = https://sinabroin.github.io

# 3) 호스트 앞단을 Cloudflare Access / Vercel Protection / SSO로 보호(운영자만 세션 획득).

# 4) 공개 base URL(비밀값 아님)만 저장소 변수에 넣고 대시보드 재빌드:
gh variable set OPERATOR_API_BASE -R Sinabroin/HDEC-News-Sensor --body "https://YOUR-OPERATOR-HOST"
gh workflow run scheduled-live-refresh.yml -R Sinabroin/HDEC-News-Sensor --ref main
```
공개 health 확인: `curl -fsS https://YOUR-OPERATOR-HOST/api/operator/health`
→ `{"status":"ok","operator_api_enabled":true,"access_mode":"edge"}`

### 로컬 활성 빌드 검증(배포 없이 fetch 경로 증명)
```bash
# 게이트웨이 인가 계약(무설정·신원·Origin·레이트·레거시) — 네트워크/발송 0건
python3 scripts/smoke_operator_api_local.py
# 실제 HTTP 경로(로컬 stdlib 서버, loopback 인가 + dry-run — 실제 dispatch 없음)
OPERATOR_LOCAL_DEV=1 OPERATOR_DRY_RUN=1 GH_OPERATOR_TOKEN=x OPERATOR_REPO=owner/repo \
  python3 <local stdlib server> ; curl -X POST -H "Origin: http://127.0.0.1:8088" .../api/operator/collect
# 연결 빌드 UI(버튼 활성·PIN 없음·fetch 경로) 계약
python3 scripts/verify_operator_actual_buttons.py
```
> `OPERATOR_API_BASE` 미설정 상태는 '완료'가 아니다 — '코드 준비 완료 / 배포 미완료'로만 보고한다.

## B. 호르무즈 해협 통과 선박량 (실연동 · 기사 카드 아님)

- **소스: IMF PortWatch — Daily Chokepoint Transit Calls**(위성 AIS 기반 일 단위 통항, portid `chokepoint6`).
  공개 ArcGIS FeatureServer(키 불필요 GET) — GitHub Actions 배치와 호환. 상세: `HORMUZ_LIVE_INTEGRATION.md` §8.
- 새 leaf `app/hormuz_transit.py`(네트워크 전용). `--market-mode live`일 때만 실측, 아니면/실패면
  `unavailable`(가짜 수치·모식도 금지). 시장 패널에 통과 선박 수·유조선/화물선·7일 대비 변화율·관측일·출처를
  표시하고, 미연동이면 '연동 미설정'만 표시한다(기사 카드로 대체하지 않음).
- 검증: `python3 scripts/verify_hormuz_ais_traffic.py` (오프라인이면 unavailable로 정직 강등, 둘 다 계약 충족).
- 준실시간 게이트 IN/OUT(aisstream) 업그레이드는 상주 러너/키 확보 시 병기(`HORMUZ_LIVE_INTEGRATION.md` §6/§8).
