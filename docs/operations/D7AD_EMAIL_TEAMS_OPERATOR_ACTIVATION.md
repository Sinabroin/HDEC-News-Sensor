# D7-AD — Gmail email alert, Teams channel email, Operator API activation

이 문서는 1차 구현의 운영 경계와 활성화 절차를 정의한다.

- 메일은 GitHub 자체 알림 기능이 아니라 **GitHub Actions runner가 Gmail SMTP를 호출**해 보낸다.
- Outlook connector, Power Automate, Teams webhook은 사용하지 않는다.
- 기본 실행은 dry-run이다. 실제 SMTP 연결은 `approve_send_email=true` 수동 승인 때만 허용한다.
- 이메일 주소·SMTP 비밀번호·Teams 채널 주소는 GitHub Secrets에만 저장한다. 저장소 파일,
  Actions artifact, 공개 대시보드에는 넣지 않는다.
- Operator API는 이번 작업에서 배포하지 않는다. `OPERATOR_API_BASE`가 비어 있어 공개
  대시보드 버튼이 비활성인 현재 상태가 정상이다.

## 1. GitHub Secrets

Repository Settings → Secrets and variables → Actions → Repository secrets에 아래 값을 설정한다.
값은 로컬 `.env`, 문서, workflow input, issue에 복사하지 않는다.

| Secret | 용도 | 필수 |
|---|---|---|
| `GMAIL_SMTP_USER` | Gmail SMTP 로그인 계정 | 실제 발송 |
| `GMAIL_SMTP_APP_PASSWORD` | 2단계 인증 후 만든 Gmail 앱 비밀번호 | 권장·실제 발송 |
| `GMAIL_SMTP_PASSWORD` | 앱 비밀번호를 쓸 수 없는 환경의 대체 후보 | 선택 |
| `ALERT_EMAIL_FROM` | From 주소. 우선 SMTP 로그인 계정과 동일하게 설정 | 실제 발송 |
| `ALERT_EMAIL_TO` | 테스트 수신 주소. 여러 주소는 쉼표로 구분 | 실제 발송 |
| `TEAMS_CHANNEL_EMAIL` | Teams 채널의 Get email address로 확보한 주소 | Teams 테스트 |

sender는 `GMAIL_SMTP_APP_PASSWORD`를 먼저 사용한다. Google은 앱 비밀번호 사용에 2단계
인증이 필요하다고 안내하며, 회사/학교 계정이나 Advanced Protection 등에서는 앱 비밀번호
메뉴가 없을 수 있다.

- Google 앱 비밀번호: <https://support.google.com/accounts/answer/185833>
- Gmail SMTP 설정: <https://support.google.com/mail/answer/7104828>
- GitHub Actions Secrets: <https://docs.github.com/en/actions/concepts/security/secrets>

## 2. Dry-run과 실제 발송

Actions → **Email Alert via Gmail SMTP** → Run workflow에서 실행한다.

### Dry-run

1. `approve_send_email`을 끈다.
2. `send_to_teams` 값은 발송에 영향을 주지 않는다.
3. 실행 로그에서 subject/body 후보와 `smtp_connections=0`을 확인한다.

이 경로는 GitHub Secrets를 sender step에 주입하지 않고 SMTP 연결도 만들지 않는다.

### 실제 이메일 테스트

1. `ALERT_EMAIL_TO`를 검증할 개인 또는 회사 수신함 한 곳으로 시작한다.
2. `approve_send_email=true`, `send_to_teams=false`로 실행한다.
3. 로그에서 `smtp_status=accepted`와 SMTP 2xx 코드를 확인한다.
4. 수신함에서 실제 수신 여부와 스팸 격리를 별도로 확인한다.

`smtp_status=accepted`는 Gmail relay가 메시지를 수락했다는 뜻일 뿐, 최종 수신함 도착을
증명하지 않는다. sender는 이를 `recipient_policy_status=unverified`로 기록한다.

## 3. Teams 채널 이메일 테스트

1. Teams 데스크톱/웹에서 채널 → More options → **Get email address**로 주소를 얻는다.
2. 채널의 Advanced settings에서 외부 Gmail 발신 도메인이 허용되는지 확인한다.
3. 주소를 `TEAMS_CHANNEL_EMAIL` GitHub Secret에 저장한다.
4. 먼저 `approve_send_email=true`, `send_to_teams=false`로 회사 이메일 수신을 검증한다.
5. 다음 실행에서 `approve_send_email=true`, `send_to_teams=true`로 같은 executive brief를
   일반 수신함과 Teams 채널 주소에 각각 보낸다.
6. Actions 로그의 `teams_channel` SMTP 결과와 실제 Teams 채널 게시 여부를 따로 확인한다.

Microsoft 문서상 채널 이메일은 IT 관리자가 기능을 켜야 하며, 채널 발신자/도메인 제한,
moderation, anti-spam 정책으로 외부 Gmail 발신이 차단될 수 있다.

- `smtp_status=rejected`: SMTP 단계에서 거절됐다. 코드와 `detail`로 인증·수신자·전송 오류를
  구분한다.
- `smtp_status=accepted`, Teams 게시 없음: SMTP 전송은 수락됐지만 최종 회사/Teams 정책,
  스팸 격리 또는 채널 설정을 확인해야 한다. 이 경우를 발송 성공 또는 Teams 게시 성공으로
  단정하지 않는다.
- `recipient_policy_status=possible_recipient_policy_rejection`: Teams 수신 주소가 SMTP 5xx로
  거절돼 회사/Teams 정책 가능성이 있는 상태다. 정책 원인이 확정됐다는 뜻은 아니다.

Teams 공식 안내:

- 채널 이메일 사용·실패 사유: <https://support.microsoft.com/en-US/teams/teams-channels/send-an-email-to-a-channel-in-microsoft-teams>
- 채널별 허용 발신자/도메인: <https://support.microsoft.com/en-us/office/manage-who-can-send-email-to-a-channel-in-microsoft-teams-4f1a1224-e71b-45de-8f68-8e08f7874fa9>

## 4. Executive digest 계약

`app/executive_digest.py`가 채널 중립 모델과 renderer를 소유한다.

- `headline`: 첫 문장 한 줄 결론
- `situation`: 현재 상황 한 문장
- `hdec_angle`: 현대건설 관점 한 문장
- `watch`: 오늘 확인할 액션 한 문장
- `links`: 핵심 근거 1~3개

Telegram, 일반 이메일, Teams 채널 이메일이 같은 네 문장과 링크를 사용한다. Telegram은
짧은 HTML text, 이메일과 Teams는 동일한 plain text + 단순 HTML multipart로 렌더한다.

### 4.1 대시보드·리포트 CTA (D7-AD-K)

- 이메일/Teams 본문에는 **요약 대시보드 보기**(`.../daily/dashboard-latest.html`)와
  **전체 리포트 보기**(`.../daily/latest.html`) CTA가 포함된다.
- HTML 본문은 두 링크를 inline-style 버튼으로 보여주고, 버튼이 깨질 때를 대비해 같은 두
  주소를 plain URL로도 함께 노출한다. plain text 본문은 `바로 보기:` 아래에 두 URL을 둔다.
- Teams 채널 이메일은 클라이언트에서 HTML 버튼이 깨질 수 있으므로, plain text의 URL
  fallback만으로도 대시보드/전체 리포트에 접근할 수 있어야 한다.
- CTA 주소는 비밀값이 아니다. 기본값은 `app/executive_digest.py`의 공개 fallback 상수이며,
  운영자가 `REPORT_URL`/`DASHBOARD_URL`(repo `vars`, Telegram과 동일 계약)을 설정하면 그
  값이 우선한다. 본문에는 SMTP 계정·수신자·토큰 등 어떤 secret도 넣지 않는다.
- 메일 발송 step의 `smtp_status=accepted`는 Gmail relay 수락만 의미한다. 실제 수신함 도착,
  Teams 채널 게시, CTA 버튼이 클라이언트에서 제대로 보이는지는 사람이 별도로 확인한다.

## 5. Operator API 현재 준비 상태

이미 구현된 항목:

- `app/main.py`: `/api/operator/collect`, `/api/operator/send-telegram` POST route와 CORS
- `app/operator_gateway.py`: PIN 상수시간 비교, 서버측 GitHub `workflow_dispatch`, 오류 응답
- `app/config.py`: `GH_OPERATOR_TOKEN`, `OPERATOR_SHARED_SECRET`/`OPERATOR_PIN`,
  `OPERATOR_ALLOWED_ORIGINS`
- `build_static_dashboard.py`: `--operator-api-base`를 JSON island에만 주입
- 공개 대시보드: base 미설정 시 버튼 비활성, 설정 시 Operator API에만 POST

GitHub Pages는 정적 호스팅이므로 서버측 secret을 보관하거나 GitHub Actions API를 안전하게
호출할 수 없다. 브라우저 HTML에 GitHub token을 넣어 `workflow_dispatch`를 직접 호출하는
구조는 금지한다. GitHub의 workflow dispatch REST endpoint에는 Actions repository
permission(write)이 필요하다.

- GitHub workflow dispatch API: <https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event>

## 6. 가장 가벼운 배포 후보 비교

| 후보 | 현재 코드 재사용 | 추가 작업 | 판단 |
|---|---|---|---|
| Vercel Function | FastAPI를 단일 Function으로 지원 | entrypoint 지정, env/CORS 설정, cold-start 확인 | 요청형 Operator API에 가장 가벼운 1순위 |
| Render / Railway | `uvicorn app.main:app` 형태로 직접 실행 | service env, start command, health/rate-limit 설정 | 기존 앱 그대로 검증하기 쉬운 2순위 |
| Fly.io | FastAPI 컨테이너 실행 가능 | Dockerfile, `fly.toml`, machine 운영 설정 | 장기 실행·네트워크 제어가 필요할 때 적합 |
| Cloudflare Worker | Python Workers가 FastAPI를 지원 | Python Workers beta, WorkerEntrypoint/ASGI adapter, 현재 `urllib` 호출 호환 검증 | 단순 gateway를 별도 재작성할 때 후보 |
| 기존 FastAPI 앱 배포 | route 변경 없이 가능 | 불필요한 API/DB 초기화도 함께 노출됨 | 빠른 검증용, 운영 전 route 축소 권장 |

공식 배포 문서:

- Vercel FastAPI: <https://vercel.com/docs/frameworks/backend/fastapi>
- Render FastAPI: <https://render.com/docs/deploy-fastapi>
- Railway FastAPI: <https://docs.railway.com/guides/fastapi>
- Fly.io FastAPI: <https://fly.io/docs/python/frameworks/fastapi/>
- Cloudflare Python Workers FastAPI: <https://developers.cloudflare.com/workers/languages/python/packages/fastapi/>

## 7. Activation checklist

### 배포 전

- [ ] Operator API 호스트를 Vercel 또는 Render/Railway 중 하나로 결정
- [ ] 외부에 필요한 operator route만 배포할지, 전체 `app.main`을 배포할지 결정
- [ ] GitHub fine-grained token 생성: 대상 repository 한정, Actions write 최소 권한
- [ ] 충분히 긴 `OPERATOR_SHARED_SECRET` 생성. 공개 HTML·repo variable에 저장하지 않음
- [ ] 공개 대시보드 exact origin을 `OPERATOR_ALLOWED_ORIGINS`에 설정
- [ ] public endpoint rate limit과 실패 모니터링 설정

### Operator API 서비스 env

- [ ] `GH_OPERATOR_TOKEN` = 배포 서비스 secret
- [ ] `OPERATOR_SHARED_SECRET` 또는 `OPERATOR_PIN` = 배포 서비스 secret
- [ ] `OPERATOR_REPO` = owner/repository
- [ ] `OPERATOR_ALLOWED_ORIGINS` = 실제 GitHub Pages/custom-domain origin

### GitHub repository

- [ ] Repository variable `OPERATOR_API_BASE` = 배포된 HTTPS base URL
- [ ] `scheduled-live-refresh.yml`, `telegram-notify.yml`이 default branch에 존재
- [ ] dashboard workflow를 다시 실행해 `operator_api_base`가 JSON island에 주입된 공개본 게시

### 검증 순서

- [ ] `python3 scripts/verify_operator_api_activation_readiness.py`
- [ ] 승인 PIN 없이 POST → HTTP 401, workflow run 0건
- [ ] 잘못된 PIN 반복 요청 → rate limit 또는 차단 확인
- [ ] 올바른 PIN으로 collect POST → HTTP 2xx `status=dispatched`
- [ ] GitHub Actions에 `Scheduled Live Refresh` run이 실제 생성됐는지 별도 확인
- [ ] 대시보드가 GitHub로 이동하지 않고 진행/성공/실패 상태를 표시하는지 확인
- [ ] 브라우저 개발자 도구와 공개 HTML에 token/secret이 없는지 재확인

Operator API의 `dispatched` 응답은 GitHub가 실행 요청을 수락했다는 뜻이다. 실제 수집 성공과
Pages 반영은 해당 Actions run의 결과와 공개 페이지에서 별도로 확인한다.

## 8. 로컬 검증

```bash
python3 -m py_compile app/*.py scripts/*.py
python3 scripts/verify_email_teams_alert.py
python3 scripts/verify_operator_api_activation_readiness.py
git diff --check
```

이 검증은 네트워크 호출과 실제 이메일/Teams/Telegram 발송을 하지 않는다.
