# D7-AG-5 — 공개 대시보드 운영자 버튼 실운영 활성화

D7-AG-3/4에서 **코드·검증은 완료**했다(버튼 → Operator API → workflow_dispatch 경로 로컬 증명, 3개
워크플로 Verify green). 남은 것은 **외부 상태 변경**뿐이다: Operator API 를 HTTPS 로 배포하고,
공개 base URL 을 저장소 변수에 넣어 대시보드를 재빌드하는 것. 이 단계들은 운영자 계정/자격증명이
필요하므로 사람이 실행한다(자동화하지 않는다).

> 상태 규칙: `OPERATOR_API_BASE` 미설정 = '코드 준비 완료 / 배포 미완료'. 공개 대시보드는 이때
> 버튼을 **비활성**으로 두고 '미연결'을 명시한다(fail-closed, 링크로 대체하지 않음).

> **Cloudflare 없이 순수 Vercel로 수집 버튼만 먼저 열려면** → `D7AG5B_OPERATOR_VERCEL_HYBRID.md`
> (origin 모드 하이브리드: 수집 공개 · 발송은 인증잠금 · 발송 활성화는 D7-AG-5C).

## 0. 사전 로컬 증명 (이미 통과 — 배포 없이 확인 가능)

```bash
# 게이트웨이 인가 계약 + FastAPI 라우트 등록(무설정 fail-closed · edge 신원/Origin/레이트)
python3 scripts/verify_operator_api_activation_readiness.py     # → PASS
# 실행 버튼이 실제 fetch(base+경로) 액션이고 링크 fallback/공개 secret 이 없는지
python3 scripts/verify_operator_actual_buttons.py               # → PASS
python3 scripts/verify_operator_controls.py                     # → PASS
```

배포될 앱은 `app.operator_api:app`(ASGI) 하나다. 로컬 부팅 시 health 는 비밀값 없이
`{"status":"ok","operator_api_enabled":true,"access_mode":"edge"}` 를 반환하고, 신원 헤더가 없으면
401, 허용 Origin 아니면 403 으로 fail-closed 한다.

## 1. Operator API 배포 (HTTPS · 운영자 신원 경계 뒤)

운영자 신원 보호는 **호스트 앞단(edge)**이 담당한다. config 기본 신원 헤더가
`cf-access-authenticated-user-email` 이므로 **Cloudflare Access** 를 우선 권장한다(커스텀 도메인과도 일치).

### 권장: 컨테이너 + Cloudflare Access

```bash
# 이 저장소의 Dockerfile 은 operator API leaf 만 담아 uvicorn 으로 서빙한다(수집기/DB 미포함).
docker build -t hdec-operator-api .
docker run -p 8000:8000 --env-file <(printf '%s\n' \
  'OPERATOR_ACCESS_MODE=edge' \
  'OPERATOR_ACCESS_HEADER=cf-access-authenticated-user-email' \
  'OPERATOR_ALLOWED_USERS=ops1@hdec.co.kr,ops2@hdec.co.kr' \
  'OPERATOR_REPO=Sinabroin/HDEC-News-Sensor' \
  'OPERATOR_ALLOWED_ORIGINS=https://guides.playground-aidesignlab.co.kr,https://sinabroin.github.io' \
  'GH_OPERATOR_TOKEN=***server-only***') hdec-operator-api
```

- 이 컨테이너를 Cloud Run / Fly / Render / 사내 VM(+cloudflared) 어디에 올려도 된다.
- 그 호스트를 **Cloudflare Access 애플리케이션**으로 감싸 운영자만 세션을 얻게 하고, Access 가
  인증 신원을 `Cf-Access-Authenticated-User-Email` 헤더로 주입하게 한다.
- 브라우저 fetch 는 `credentials:"include"` 로 Access 세션 쿠키를 실어 보낸다(교차 출처).

### 대안: Vercel

`pyproject.toml` 의 `entrypoint = "app.operator_api:app"` 를 사용한다. Vercel Python 런타임에 ASGI
`app` 을 노출하고(예: `api/index.py` 에서 `from app.operator_api import app`), 라우팅을 함수로
rewrite 한 뒤 첫 배포에서 경로가 그대로 전달되는지 확인한다. 보호는 Vercel Protection 또는 앞단
Cloudflare Access 로 둔다(그 경우 `OPERATOR_ACCESS_HEADER` 를 실제 주입 헤더에 맞춘다).

### 서버 환경변수 계약 (비밀값은 shell history 에 직접 노출 금지)

| 변수 | 값 | 비고 |
|---|---|---|
| `GH_OPERATOR_TOKEN` | Actions: write 만 가진 fine-grained PAT / GitHub App 토큰 | **서버 env 전용**. 응답/로그/HTML 에 절대 안 실림 |
| `OPERATOR_REPO` | `Sinabroin/HDEC-News-Sensor` | |
| `OPERATOR_ACCESS_MODE` | `edge` | 미설정이면 모든 액션 not_configured(fail-closed) |
| `OPERATOR_ACCESS_HEADER` | `cf-access-authenticated-user-email` | 경계가 주입하는 신원 헤더 이름(비밀값 아님) |
| `OPERATOR_ALLOWED_USERS` | 허용 운영자 이메일 CSV | edge 모드에서 비면 fail-closed |
| `OPERATOR_ALLOWED_ORIGINS` | 두 공개 대시보드 origin | 코드 기본값에도 포함되나 명시 권장 |

> `guides.playground-aidesignlab.co.kr` 와 `sinabroin.github.io` 는 config 기본 허용 origin 이라
> env 를 빠뜨려도 CORS·게이트웨이가 두 대시보드를 인식한다(fail-open 아님 — 나머지 origin 은 차단).

배포 후 공개 health 확인:

```bash
curl -fsS https://YOUR-OPERATOR-HOST/api/operator/health
# 기대: {"status":"ok","operator_api_enabled":true,"access_mode":"edge"}
```

## 2. 공개 base URL 을 저장소 변수에 주입 (비밀값 아님)

```bash
gh variable set OPERATOR_API_BASE -R Sinabroin/HDEC-News-Sensor -b "https://YOUR-OPERATOR-HOST"
# localhost 금지. HTTPS 공개 URL 만.
```

## 3. 대시보드 재빌드/배포

```bash
gh workflow run scheduled-live-refresh.yml -R Sinabroin/HDEC-News-Sensor --ref main
# 워크플로가 vars.OPERATOR_API_BASE 를 --operator-api-base 로 빌더에 전달 →
# preview-model.operator_api_base = HTTPS URL, operator_api_enabled = true 로 재생성 후 Pages 배포.
```

## 4. 공개 스모크 10/10 (읽기 전용 GET · 발송/дispatch 없음)

```bash
python3 scripts/smoke_public_operator_hormuz.py \
  --dashboard-url https://guides.playground-aidesignlab.co.kr/HDEC-News-Sensor/daily/dashboard-latest.html
```

배포 전에는 `operator_api_enabled`·HTTPS base·health 항목이 정직하게 FAIL 한다. 배포 + 변수 설정 +
재빌드 후 전 항목 PASS(그중 마지막 두 항목 health 200 / server credentials 는 실제 배포된 API 가
있어야 통과).

## 5. 실제 버튼 테스트 순서 (외부 발송은 별도 승인 후)

버튼은 인가를 통과한 호출에서만 워크플로 입력으로 승인 플래그를 넘긴다(워크플로 기본값은 비발송).

| 순서 | 버튼 | POST | 워크플로 | 승인 입력 |
|---|---|---|---|---|
| A | 데이터 새로고침 | `/api/operator/collect` | `scheduled-live-refresh.yml` | 없음(수집만) |
| B | 텔레그램 전송 | `/api/operator/send` | `telegram-notify.yml` | `approve_send=true` |
| C | Teams 채널 전송 | `/api/operator/send-teams` | `email-alert.yml` | `approve_send_email=true` · `send_to_teams=true` |

- B: 텔레그램 발송에는 별도 사람-검토 게이트가 있다(`TELEGRAM_SEND_MODE`=manual 기본 = 검토만, POST 없음).
  실제 발송은 그 게이트까지 승인됐을 때만 일어난다.
- 동시에 여러 버튼을 누르지 않는다 — collect/telegram 워크플로가 같은 파일을 main 에 auto-commit 하므로
  동시 dispatch 는 push race 로 한 쪽 commit 단계가 실패할 수 있다(Verify 는 통과). 순차로 누른다.

## 6. 불변 보안 계약

- 공개 HTML 에 token / PAT / Telegram / Gmail / Teams / Naver 비밀값을 넣지 않는다.
- 브라우저는 `base + 경로` 로만 POST 한다(GitHub/Telegram/Teams 로 직접 호출하지 않음).
- 서버 인가 미설정/토큰 부재 = fail-closed(not_configured). 가짜 성공을 만들지 않는다.
- 비밀값은 서버 env 에서만 읽고 응답·로그·예외 메시지에 싣지 않는다(상태 코드/중립 메시지만).
