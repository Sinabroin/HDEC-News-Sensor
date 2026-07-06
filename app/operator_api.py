"""배포용 최소 Operator API.

GitHub Pages 브라우저는 이 API에만 POST한다. GitHub token, Telegram token, Teams 주소, 운영자
신원 검증은 서버 환경변수/경계(edge) 인증에만 있으며 응답·로그·정적 HTML에 포함하지 않는다.

D7-AG-3 — 브라우저 승인 PIN을 제거하고 보호를 **서버 앞단(edge SSO/Access) + 서버측 인가**로
이관했다. 배포 권장: 이 호스트를 Cloudflare Access / Vercel Protection / 사내 SSO / Basic Auth 뒤에
두어 경계가 인증된 운영자 신원 헤더를 주입하게 한다. 서버(operator_gateway)는 그 신원이
허용목록에 있고 Origin·레이트리밋을 통과할 때만 workflow_dispatch를 호출한다(fail-closed).
전체 radar 앱/DB를 외부에 노출하지 않고 운영 실행 route만 제공한다.
"""

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import config, operator_gateway

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
