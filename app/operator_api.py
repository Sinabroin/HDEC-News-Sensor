"""배포용 최소 Operator API.

GitHub Pages 브라우저는 이 API에만 POST한다. GitHub token, Telegram token, Teams 주소, 운영자
신원 검증은 서버 환경변수/경계(edge) 인증에만 있으며 응답·로그·정적 HTML에 포함하지 않는다.

D7-AG-3 — 브라우저 승인 PIN을 제거하고 보호를 **서버 앞단(edge SSO/Access) + 서버측 인가**로
이관했다. 배포 권장: 이 호스트를 Cloudflare Access / Vercel Protection / 사내 SSO / Basic Auth 뒤에
두어 경계가 인증된 운영자 신원 헤더를 주입하게 한다. 서버(operator_gateway)는 그 신원이
허용목록에 있고 Origin·레이트리밋을 통과할 때만 workflow_dispatch를 호출한다(fail-closed).
전체 radar 앱/DB를 외부에 노출하지 않고 운영 실행 route만 제공한다. D7-AG-5C에서는 GitHub OAuth
로그인과 HttpOnly signed session cookie를 같은 최소 앱에 추가해, public dashboard의 발송 버튼만
운영자 세션 뒤에 둔다. OAuth client secret/session secret은 server-side env에서만 읽는다.
"""

import urllib.error

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse

from app import config, operator_auth, operator_gateway

_OPERATOR_HTTP = {
    "dispatched": 200,
    "not_configured": 503,
    "unauthorized": 401,
    "auth_required": 401,  # origin 모드에서 발송(telegram/teams)은 인증 필요 → 401
    "forbidden": 403,
    "rate_limited": 429,
    "error": 502,
}

router = APIRouter()


def _operator_response(result: dict) -> JSONResponse:
    status = result.get("status", "error")
    return JSONResponse(status_code=_OPERATOR_HTTP.get(status, 502), content=result)


@router.get("/api/operator/health")
def operator_health():
    """비밀값 없이 배포/설정/인가모드 여부만 확인하는 smoke endpoint."""
    return {
        "status": "ok",
        "operator_api_enabled": operator_gateway.is_configured(),
        "access_mode": operator_gateway.public_access_mode(),
        "dry_run": bool(config.OPERATOR_DRY_RUN),
    }


@router.get("/api/auth/github/login")
def github_login():
    if not operator_auth.oauth_configured():
        return JSONResponse(
            status_code=503,
            content={"status": "not_configured", "authenticated": False},
        )
    state = operator_auth.generate_state()
    response = RedirectResponse(operator_auth.github_authorize_url(state), status_code=302)
    operator_auth.set_state_cookie(response, state)
    return response


@router.get("/api/auth/github/callback")
def github_callback(request: Request):
    if not operator_auth.oauth_configured():
        return JSONResponse(
            status_code=503,
            content={"status": "not_configured", "authenticated": False},
        )
    expected_state = request.cookies.get(config.OPERATOR_OAUTH_STATE_COOKIE) or ""
    actual_state = request.query_params.get("state") or ""
    code = request.query_params.get("code") or ""
    if not expected_state or not actual_state or not code or expected_state != actual_state:
        response = JSONResponse(
            status_code=401,
            content={"status": "auth_required", "authenticated": False},
        )
        operator_auth.clear_state_cookie(response)
        return response
    try:
        token = operator_auth.exchange_code_for_token(code)
        login = operator_auth.fetch_github_login(token)
    except (urllib.error.HTTPError, urllib.error.URLError, OSError, ValueError):
        response = JSONResponse(
            status_code=401,
            content={"status": "auth_required", "authenticated": False},
        )
        operator_auth.clear_state_cookie(response)
        return response
    if not operator_auth.allowed_github_login(login):
        response = JSONResponse(
            status_code=403,
            content={"status": "forbidden", "authenticated": False},
        )
        operator_auth.clear_state_cookie(response)
        return response
    response = RedirectResponse(config.OPERATOR_AUTH_SUCCESS_URL, status_code=302)
    operator_auth.clear_state_cookie(response)
    operator_auth.set_session_cookie(response, login)
    return response


@router.get("/api/auth/session")
def auth_session(request: Request):
    session = operator_auth.session_from_cookie_header(
        request.headers.get("cookie", "")
    )
    if not session:
        return {"authenticated": False}
    return {"authenticated": True, "login": session["login"]}


@router.post("/api/auth/logout")
def auth_logout():
    response = JSONResponse(content={"authenticated": False})
    operator_auth.clear_session_cookie(response)
    operator_auth.clear_state_cookie(response)
    return response


@router.post("/api/operator/collect")
def operator_collect(request: Request):
    return _operator_response(operator_gateway.trigger_collect(
        request.headers, request.headers.get("origin", "")))


@router.post("/api/operator/send")
@router.post("/api/operator/send-telegram", include_in_schema=False)
def operator_telegram(request: Request):
    return _operator_response(operator_gateway.trigger_telegram(
        request.headers, request.headers.get("origin", "")))


@router.post("/api/operator/send-teams")
def operator_teams(request: Request):
    return _operator_response(operator_gateway.trigger_teams(
        request.headers, request.headers.get("origin", "")))


app = FastAPI(
    title="HDEC Operator API",
    version="d7-ag-3",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
# credentials=True — 경계(edge SSO/Access) 세션 쿠키가 cross-origin fetch에 실릴 수 있게 한다.
# allow_origins는 명시적 목록만(와일드카드 금지) — 자격증명 허용과 함께 안전하게 쓰기 위함.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        *config.OPERATOR_ALLOWED_ORIGINS,
    ],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-Operator-Token"],
    allow_credentials=True,
)
app.include_router(router)
