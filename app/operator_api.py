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

import json
import urllib.error

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from app import (
    config,
    editorial_article_import,
    operator_auth,
    operator_gateway,
)

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
_IMPORT_REQUEST_MAX_BYTES = 4_096


def _operator_response(result: dict) -> JSONResponse:
    status = result.get("status", "error")
    return JSONResponse(status_code=_OPERATOR_HTTP.get(status, 502), content=result)


def _article_import_error(
    code: str,
    *,
    status: int | None = None,
    message: str = "",
) -> JSONResponse:
    error = editorial_article_import.ArticleImportError(
        code,
        status=status,
        message=message,
    )
    return JSONResponse(status_code=error.status, content=error.response_payload())


def _authorize_article_import(request: Request) -> JSONResponse | None:
    """Require the existing operator identity/session and Origin allowlist."""
    auth = operator_gateway.authorize(
        request.headers,
        request.headers.get("origin", ""),
        action="import_article",
    )
    if auth["ok"]:
        return None
    reason = auth["reason"]
    if reason == "forbidden_origin":
        return _article_import_error("FORBIDDEN", status=403)
    if reason == "rate_limited":
        return _article_import_error(
            "FORBIDDEN",
            status=429,
            message="기사 불러오기 요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.",
        )
    if reason == "not_configured":
        return _article_import_error(
            "AUTH_REQUIRED",
            status=503,
            message="운영자 인증이 설정되지 않았습니다.",
        )
    return _article_import_error("AUTH_REQUIRED", status=401)


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
def github_login(request: Request):
    if not operator_auth.oauth_configured():
        return JSONResponse(
            status_code=503,
            content={"status": "not_configured", "authenticated": False},
        )
    state = operator_auth.generate_state()
    response = RedirectResponse(operator_auth.github_authorize_url(state), status_code=302)
    operator_auth.set_state_cookie(response, state)
    # R4-R9C — preserve only the validated local edition identity across the
    # OAuth round-trip (never a caller-supplied URL); invalid input clears any
    # stale return so the callback falls back to the fixed success URL.
    editor_return = operator_auth.encode_editor_return(
        request.query_params.get("product"),
        request.query_params.get("edition_id"),
        request.query_params.get("source"),
    )
    if editor_return:
        operator_auth.set_editor_return_cookie(response, editor_return)
    else:
        operator_auth.clear_editor_return_cookie(response)
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
        operator_auth.clear_editor_return_cookie(response)
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
        operator_auth.clear_editor_return_cookie(response)
        return response
    if not operator_auth.allowed_github_login(login):
        response = JSONResponse(
            status_code=403,
            content={"status": "forbidden", "authenticated": False},
        )
        operator_auth.clear_state_cookie(response)
        operator_auth.clear_editor_return_cookie(response)
        return response
    # R4-R9C — the post-login destination is re-validated from scratch and
    # reconstructed from canonical constants; anything unknown or malformed
    # fails closed to the fixed success URL (no open redirect surface).
    success_url = (
        operator_auth.editor_return_url_from_cookie(
            request.cookies.get(config.OPERATOR_EDITOR_RETURN_COOKIE)
        )
        or config.OPERATOR_AUTH_SUCCESS_URL
    )
    response = RedirectResponse(success_url, status_code=302)
    operator_auth.clear_state_cookie(response)
    operator_auth.clear_editor_return_cookie(response)
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


@router.post("/api/editorial/import-article")
async def editorial_import_article(request: Request):
    """Import one public article for an authenticated editorial operator.

    The route never returns fetched HTML or a full article body and is not an
    arbitrary URL proxy. Every redirect and image fetch is revalidated by the
    domain module before bounded bytes are parsed.
    """
    blocked = _authorize_article_import(request)
    if blocked is not None:
        return blocked
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type != "application/json":
        return _article_import_error("UNSUPPORTED_CONTENT_TYPE", status=415)
    content_length = request.headers.get("content-length", "")
    try:
        advertised_length = int(content_length) if content_length else None
    except ValueError:
        advertised_length = None
    if advertised_length is not None and advertised_length > _IMPORT_REQUEST_MAX_BYTES:
        return _article_import_error("RESPONSE_TOO_LARGE", status=413)
    body = await request.body()
    if len(body) > _IMPORT_REQUEST_MAX_BYTES:
        return _article_import_error("RESPONSE_TOO_LARGE", status=413)
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        return _article_import_error("INVALID_URL", status=400)
    if not isinstance(payload, dict) or set(payload) != {"url"}:
        return _article_import_error("INVALID_URL", status=400)
    try:
        result = await run_in_threadpool(
            editorial_article_import.import_article,
            payload.get("url"),
        )
    except editorial_article_import.ArticleImportError as exc:
        return JSONResponse(status_code=exc.status, content=exc.response_payload())
    except Exception:
        # Do not expose exception types, messages, fetched content, or stack traces.
        return _article_import_error("INTERNAL_ERROR", status=500)
    return JSONResponse(status_code=200, content=result)


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
