# D7-AG-5B — 공개 대시보드 운영자 버튼: Vercel 하이브리드 활성화

Vercel(신원 edge 없이)에 Operator API를 배포하고, 공개 대시보드의 **'데이터 새로고침'(수집)
버튼만** 실제로 작동시킨다. 발송(텔레그램·Teams) 2버튼은 **인증 필요(auth-locked)** 상태로
안전하게 막아둔다 — 실제 운영자 인증 경로는 후속 **D7-AG-5C**에서 연다.

> Cloudflare Access(edge 신원) 경로는 `D7AG5_OPERATOR_PUBLIC_ACTIVATION.md`에 있다. 이 문서는
> Cloudflare를 쓰지 않는 순수 Vercel 배포를 다룬다.

## 0. 왜 하이브리드인가 (설계 근거)

공개 정적 페이지 버튼은 브라우저에서 `fetch(base+경로)`로만 POST한다(공개 HTML에 secret 금지).
bare Vercel에는 신원을 주입하는 edge가 없으므로 브라우저는 `cf-access` 신원 헤더도, `X-Operator-
Token` secret도 실을 수 없다. 따라서:

- **수집(collect)** 은 저위험(뉴스 재수집 + 대시보드 재빌드)이라 `origin` 모드로 연다 —
  허용목록 **Origin**(브라우저가 위조 불가) + **레이트리밋**만으로 인가한다.
- **발송(telegram/teams)** 은 서버가 `origin` 모드에서 `auth_required`로 거부한다. 버튼도
  fetch에 배선하지 않고 '인증 필요'만 안내한다(이중 방어). 공개 URL은 대시보드 소스에
  노출되므로, 발송을 origin만으로 열면 Origin을 위조한 스크립트가 발송을 트리거할 수 있다 —
  그래서 발송은 실제 인증(D7-AG-5C) 전까지 열지 않는다.

## 1. Vercel 배포 (순수 Vercel · Cloudflare 없음)

배포되는 ASGI 앱은 `app.operator_api:app` 하나다. 이 저장소는 그대로 Vercel Python 프로젝트가
되도록 어댑터를 포함한다:

- `api/index.py` — `from app.operator_api import app` (Vercel Python이 ASGI 핸들러로 감지).
- `vercel.json` — 모든 경로를 이 함수로 rewrite(FastAPI 내부 라우터가 `/api/operator/*` 처리).
- `.vercelignore` — operator leaf만 업로드(수집기/DB/문서/스크립트/mock 데이터·`.env` 제외).
- `requirements.txt` — fastapi(런타임 의존성). Vercel이 루트에서 자동 설치.

```bash
npm i -g vercel        # 또는 Vercel 대시보드에서 이 repo를 Import
vercel login
vercel --prod          # 저장소 루트에서. 첫 배포에서 프로젝트를 새로 만든다.
```

배포 후 함수가 경로를 그대로 받는지 확인한다(rewrite는 원본 경로를 보존):

```bash
curl -fsS https://<vercel-project>.vercel.app/api/operator/health
# 기대: {"status":"ok","operator_api_enabled":true,"access_mode":"origin","dry_run":false}
```

## 2. Vercel Environment Variables (Production) — 비밀값은 서버 env 전용

Vercel 대시보드 → Project → Settings → Environment Variables 에 넣는다(값은 로그/HTML에 안 실림).

| 변수 | 값 | 비고 |
|---|---|---|
| `GH_OPERATOR_TOKEN` | Actions: write 만 가진 fine-grained PAT / GitHub App 토큰 | **서버 env 전용**. 응답·로그·HTML 금지 |
| `OPERATOR_REPO` | `Sinabroin/HDEC-News-Sensor` | |
| `OPERATOR_ACCESS_MODE` | `origin` | 수집만 인가 · 발송은 auth_required |
| `OPERATOR_ALLOWED_ORIGINS` | `https://guides.playground-aidesignlab.co.kr,https://sinabroin.github.io` | 두 공개 대시보드 origin만 |
| `OPERATOR_RATE_LIMIT_PER_MIN` | `6` | 낮게 — 수집만 열리므로 충분 |
| `OPERATOR_DRY_RUN` | `false` | 실제 workflow_dispatch |

> 두 origin은 코드 기본 허용목록에도 있어(app/config.py) env를 빠뜨려도 CORS·게이트웨이가
> 인식한다. 그 밖의 origin은 차단(fail-closed). `OPERATOR_LOCAL_DEV`는 프로덕션에서 설정하지 않는다.

`health`가 `operator_api_enabled:true` · `access_mode:"origin"` · `dry_run:false`를 반환하면 준비 완료.

## 3. 공개 base URL 을 저장소 변수에 주입 (비밀값 아님)

```bash
gh variable set OPERATOR_API_BASE -R Sinabroin/HDEC-News-Sensor -b "https://<vercel-project>.vercel.app"
# localhost 금지. HTTPS 공개 URL 만.
```

## 4. 대시보드 재빌드/배포

```bash
gh workflow run scheduled-live-refresh.yml -R Sinabroin/HDEC-News-Sensor --ref main
# vars.OPERATOR_API_BASE → --operator-api-base → preview-model.operator_api_base = HTTPS URL,
# operator_api_enabled = true 로 재생성 후 Pages 배포. 수집 버튼 활성 · 발송 버튼 인증잠금.
```

## 5. 공개 스모크 (읽기 전용 GET · 발송/dispatch 없음)

```bash
python3 scripts/smoke_public_operator_hormuz.py \
  --dashboard-url https://guides.playground-aidesignlab.co.kr/HDEC-News-Sensor/daily/dashboard-latest.html
```

배포 전에는 `operator_api_enabled`·HTTPS base·health 항목이 정직하게 FAIL한다. 배포 + 변수 +
재빌드 후 전 항목 PASS(마지막 두 health 항목은 실제 배포된 API가 있어야 통과). 스모크는
`authlocked`/`showSendLocked` 앵커로 **발송 버튼이 공개에서 잠겨 있음**도 함께 확인한다.

## 6. 실제 버튼 테스트 (하이브리드 범위)

| 순서 | 버튼 | 공개 동작 | 결과 |
|---|---|---|---|
| A | 데이터 새로고침 | `POST /api/operator/collect` (Origin 인가) | 200 dispatched → `scheduled-live-refresh.yml` success |
| B | 텔레그램 전송 | **fetch 없음** — '인증 필요' 안내 | 공개에서 미발송(서버도 401 auth_required) |
| C | Teams 채널 전송 | **fetch 없음** — '인증 필요' 안내 | 공개에서 미발송(서버도 401 auth_required) |

완료 기준: A 클릭 → collect 200 → `scheduled-live-refresh.yml` run 생성·success → 대시보드 재배포 →
B/C는 인증잠금으로 안전하게 막힘.

## 7. 후속 — D7-AG-5C (발송 버튼 활성화)

Vercel-only로 발송까지 열려면 운영자 로그인(예: GitHub OAuth → 세션 쿠키 → 서버가 신원 확인)을
추가하거나, 앞단에 Cloudflare Access / Vercel 보호 / 사내 SSO 중 하나를 붙여 `OPERATOR_ACCESS_MODE`
를 `edge`로 올린다. 그때 telegram/teams 버튼을 공개 대시보드에서 활성화한다(발송은 별도 승인 후).

## 8. 불변 보안 계약

- 공개 HTML 에 token / PAT / Telegram / Gmail / Teams / Naver 비밀값을 넣지 않는다.
- 브라우저는 `base + 경로`로만 POST한다(GitHub/Telegram/Teams 직접 호출 금지).
- `origin` 모드는 **collect만** 인가한다. 발송은 서버(auth_required)와 버튼(fetch 미배선) 이중 차단.
- 서버 인가 미설정/토큰 부재 = fail-closed(not_configured). 가짜 성공을 만들지 않는다.
- 비밀값은 서버 env 에서만 읽고 응답·로그·예외 메시지에 싣지 않는다(상태 코드/중립 메시지만).
