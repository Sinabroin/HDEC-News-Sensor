"""Operator Gateway 도메인 (leaf, P0-D7-AA) — 운영자 액션을 서버측에서 실행한다.

공개 정적 대시보드(GitHub Pages)에는 서버가 없으므로, "데이터 새로고침"/"텔레그램 전송"/"Teams
전송" 버튼은 이 게이트웨이가 노출하는 Operator API(app/operator_api.py의 POST 라우트)를 호출한다.
이 leaf는 요청을 서버측에서 **인가**한 뒤 GitHub Actions의 workflow_dispatch를 호출해 기존
워크플로를 재사용한다 — 수집은 scheduled-live-refresh.yml, 발송은 telegram-notify.yml, Teams는
email-alert.yml.

D7-AG-3 — 보호를 브라우저 PIN에서 **서버 앞단(edge) 인증**으로 이관했다. 공개 페이지에 PIN을
입력받는 설계는 약하고 사용자도 불필요하다고 판단했다. 대신:
- 우선순위 A: Operator API 호스트를 인증된 경계 뒤에 배포(Cloudflare Access / Vercel Protection /
  사내 SSO / Basic Auth). 경계가 인증된 운영자 신원 헤더(OPERATOR_ACCESS_HEADER)를 주입한다.
- 서버측 인가: 신원 헤더가 OPERATOR_ALLOWED_USERS 허용목록에 있어야 하고(edge 모드),
  Origin 허용목록 + 분당 레이트리밋을 통과해야 한다. 어떤 조건이든 미충족이면 트리거하지 않는다.
- 브라우저는 base+경로로만 POST하며 PIN 정답/secret을 보유·전송하지 않는다.

D7-AG-5B — edge(신원 경계)가 없는 배포(bare Vercel 등)에서도 공개 대시보드의 **저위험 수집
버튼만** 열 수 있도록 `origin` 인가 모드를 추가했다. 허용목록 Origin(브라우저가 위조 불가) +
레이트리밋만으로 collect를 인가하고, 발송(telegram/teams)은 auth_required로 막는다 — 실제 운영자
인증 경로가 생기면 D7-AG-5C에서 발송을 연다.

경계(이 파일만 한다 / 절대 안 한다):
- 한다: 인가(신원/Origin/레이트), GitHub workflow_dispatch 호출(이 파일이 네트워크 단일 소유 · urllib).
- 안 한다: 점수/insight/수집/발송 로직 자체, DB 접근, 비밀값 노출.

안전 계약 (rules.md §1/§4):
- 비밀값(GH_OPERATOR_TOKEN, OPERATOR_SHARED_SECRET)은 config(=env)에서만 읽는다. 어떤 응답/
  로그/예외 메시지에도 비밀값을 싣지 않는다(상태 코드/중립 메시지만 반환).
- fail-closed: 토큰·repo가 없거나 인가 정책이 미설정이면 어떤 트리거도 하지 않고 not_configured.
- 인가 실패 → unauthorized/forbidden/rate_limited (트리거 없음). shared_secret 비교는 상수시간(hmac).
- 자동발송 하드코딩은 없다. 발송은 인가를 통과한 명시적 운영자 호출에서만 approve_send="true"를
  워크플로 입력으로 넘긴다(워크플로 기본값은 빈 값 = 비발송).
"""

import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from datetime import datetime, timedelta, timezone

from app import config

_API_BASE = "https://api.github.com"
_WEB_BASE = "https://github.com"
_COLLECT_WORKFLOW = "scheduled-live-refresh.yml"
_TELEGRAM_WORKFLOW = "telegram-notify.yml"
_TEAMS_WORKFLOW = "email-alert.yml"
_DISPATCH_REF = "main"
_TIMEOUT_SECONDS = 20
_RUN_LOOKUP_ATTEMPTS = 4
_RUN_LOOKUP_DELAY_SECONDS = 0.75

# D7-AG-5B — 신원 없는 `origin` 인가 모드가 실행을 허용하는 액션 집합. 공개 Origin 게이트는
# 저위험 수집(collect)만 연다. 발송(telegram/teams)은 절대 여기 넣지 않는다(auth_required로 차단).
_ORIGIN_MODE_ACTIONS = frozenset({"collect"})


def _access_policy_ready() -> bool:
    """실행을 인가할 서버측 정책이 하나라도 설정됐는가 (비밀값 노출 없음)."""
    mode = config.OPERATOR_ACCESS_MODE
    if mode == "edge" and config.OPERATOR_ALLOWED_USERS:
        return True
    if mode == "shared_secret" and config.OPERATOR_SHARED_SECRET:
        return True
    if mode == "origin" and config.OPERATOR_ALLOWED_ORIGINS:
        return True
    if config.OPERATOR_LOCAL_DEV:
        return True
    return False


def is_configured() -> bool:
    """트리거에 필요한 서버측 설정이 모두 있는가 (토큰·repo·인가 정책). 비밀값을 노출하지 않는다."""
    return bool(config.GH_OPERATOR_TOKEN
                and config.OPERATOR_REPO
                and _access_policy_ready())


def public_access_mode() -> str:
    """비밀값 없이 공개 가능한 인가 모드 라벨 (health 응답용)."""
    if config.OPERATOR_LOCAL_DEV:
        return "local_dev"
    return config.OPERATOR_ACCESS_MODE or "unset"


def _is_loopback_origin(origin: str) -> bool:
    o = (origin or "").strip().lower()
    return o.startswith("http://127.0.0.1") or o.startswith("http://localhost")


def _origin_allowed(origin: str) -> bool:
    """Origin 헤더가 있으면 허용목록/loopback에 있어야 한다. 없으면(서버-대-서버) 통과."""
    o = (origin or "").strip()
    if not o:
        return True
    if o in set(config.OPERATOR_ALLOWED_ORIGINS):
        return True
    return config.OPERATOR_LOCAL_DEV and _is_loopback_origin(o)


# 프로세스-로컬 레이트리밋(분당). 운영자 트리거는 드물어 단순 슬라이딩윈도로 충분하다.
_RATE_WINDOW_SECONDS = 60.0
_recent_triggers: deque[float] = deque()


def _rate_ok() -> bool:
    now = time.monotonic()
    while _recent_triggers and now - _recent_triggers[0] > _RATE_WINDOW_SECONDS:
        _recent_triggers.popleft()
    if len(_recent_triggers) >= max(1, config.OPERATOR_RATE_LIMIT_PER_MIN):
        return False
    _recent_triggers.append(now)
    return True


def _headers_get(headers, name: str) -> str:
    """대소문자 무관 헤더 조회 (dict 또는 items() 가능한 매핑 모두 지원)."""
    if not headers:
        return ""
    target = name.lower()
    try:
        items = headers.items()
    except AttributeError:
        items = headers
    for key, value in items:
        if str(key).lower() == target:
            return str(value or "")
    return ""


def authorize(headers=None, origin: str = "", action: str = "") -> dict:
    """요청을 서버측에서 인가한다. 비밀값을 응답/로그에 싣지 않는다.

    반환: {"ok": bool, "who": str|None, "reason": str}
      reason ∈ {"", "forbidden_origin", "rate_limited", "unauthorized", "auth_required",
                "not_configured"}
    action은 origin 모드가 수집(collect)만 열고 발송은 막기 위해 쓰인다(다른 모드는 무시).
    """
    if not _origin_allowed(origin):
        return {"ok": False, "who": None, "reason": "forbidden_origin"}
    if not _rate_ok():
        return {"ok": False, "who": None, "reason": "rate_limited"}

    # 로컬 개발 우회 — loopback(또는 서버 로컬 curl) 한정. 프로덕션에서는 비활성(OPERATOR_LOCAL_DEV 미설정).
    if config.OPERATOR_LOCAL_DEV and (not origin or _is_loopback_origin(origin)):
        return {"ok": True, "who": "local-dev", "reason": ""}

    mode = config.OPERATOR_ACCESS_MODE
    if mode == "edge":
        ident = _headers_get(headers, config.OPERATOR_ACCESS_HEADER).strip().lower()
        if ident and ident in set(config.OPERATOR_ALLOWED_USERS):
            return {"ok": True, "who": ident, "reason": ""}
        return {"ok": False, "who": None, "reason": "unauthorized"}
    if mode == "shared_secret":
        secret = config.OPERATOR_SHARED_SECRET
        token = _headers_get(headers, "x-operator-token")
        if secret and token and hmac.compare_digest(str(token), str(secret)):
            return {"ok": True, "who": "shared-secret", "reason": ""}
        return {"ok": False, "who": None, "reason": "unauthorized"}
    if mode == "origin":
        # D7-AG-5B 하이브리드 — 신원 없는 공개 Origin 게이트. 브라우저가 위조할 수 없는 허용목록
        # Origin만으로 **저위험 수집(collect)**만 인가한다. 발송(telegram/teams)은 여기서 열지 않고
        # auth_required로 막는다(실제 인증 경로가 생기면 D7-AG-5C에서 연다). 서버-대-서버(빈 Origin)도
        # 허용하지 않는다 — 반드시 허용목록의 브라우저 Origin이어야 한다.
        o = (origin or "").strip()
        if not (o and o in set(config.OPERATOR_ALLOWED_ORIGINS)):
            return {"ok": False, "who": None, "reason": "unauthorized"}
        if action in _ORIGIN_MODE_ACTIONS:
            return {"ok": True, "who": "origin:" + o, "reason": ""}
        return {"ok": False, "who": None, "reason": "auth_required"}
    return {"ok": False, "who": None, "reason": "not_configured"}


def _github_request(url: str, method: str = "GET", data: bytes | None = None):
    """GitHub API 요청을 만든다. 인증값은 서버 env에서만 읽고 응답에 반환하지 않는다."""
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {config.GH_OPERATOR_TOKEN}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    return urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS)


def _workflow_url(workflow: str) -> str:
    return f"{_WEB_BASE}/{config.OPERATOR_REPO}/actions/workflows/{workflow}"


def _parse_github_time(raw: str):
    try:
        return datetime.fromisoformat(str(raw or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _find_dispatched_run(workflow: str, dispatched_at: datetime) -> dict:
    """방금 접수된 workflow_dispatch run을 짧게 조회한다.

    GitHub dispatch 응답은 204이며 run id를 직접 주지 않는다. 실행 생성이 API 목록에
    반영될 때까지 짧게 조회하고, 찾지 못해도 dispatch 성공 자체는 유지한다.
    """
    params = urllib.parse.urlencode({
        "event": "workflow_dispatch",
        "branch": _DISPATCH_REF,
        "per_page": 10,
    })
    url = (f"{_API_BASE}/repos/{config.OPERATOR_REPO}/actions/workflows/"
           f"{urllib.parse.quote(workflow, safe='')}/runs?{params}")
    earliest = dispatched_at - timedelta(seconds=5)
    for attempt in range(_RUN_LOOKUP_ATTEMPTS):
        if attempt:
            time.sleep(_RUN_LOOKUP_DELAY_SECONDS)
        try:
            with _github_request(url) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.HTTPError, urllib.error.URLError, OSError,
                ValueError, json.JSONDecodeError):
            continue
        for run in payload.get("workflow_runs") or []:
            created_at = _parse_github_time(run.get("created_at"))
            if not created_at or created_at < earliest:
                continue
            run_id = run.get("id")
            run_url = run.get("html_url")
            if run_id and isinstance(run_url, str) and run_url.startswith(
                    f"{_WEB_BASE}/{config.OPERATOR_REPO}/actions/runs/"):
                return {
                    "run_id": run_id,
                    "run_url": run_url,
                    "run_status": run.get("status") or "queued",
                }
    return {}


def _dispatch(workflow: str, inputs: dict | None = None) -> dict:
    """GitHub Actions workflow_dispatch 호출. 성공 시 2xx. 비밀값은 반환하지 않는다."""
    # 로컬 검증 모드 — 실제 네트워크(workflow_dispatch) 없이 접수 응답만 반환한다(발송 0건).
    if config.OPERATOR_DRY_RUN:
        return {"status": "dispatched", "http_status": 202, "workflow": workflow,
                "workflow_url": _workflow_url(workflow), "dry_run": True}
    url = f"{_API_BASE}/repos/{config.OPERATOR_REPO}/actions/workflows/{workflow}/dispatches"
    body = {"ref": _DISPATCH_REF}
    if inputs:
        body["inputs"] = inputs
    data = json.dumps(body).encode("utf-8")
    dispatched_at = datetime.now(timezone.utc)
    try:
        with _github_request(url, method="POST", data=data) as resp:
            result = {
                "status": "dispatched",
                "http_status": resp.getcode(),
                "workflow": workflow,
                "workflow_url": _workflow_url(workflow),
            }
        result.update(_find_dispatched_run(workflow, dispatched_at))
        return result
    except urllib.error.HTTPError as exc:
        # 토큰/권한/입력 문제 — 상태 코드만 노출하고 본문(비밀값 가능)은 싣지 않는다.
        return {"status": "error", "http_status": exc.code,
                "detail": "GitHub workflow_dispatch 거부 (권한·입력 확인 필요)"}
    except (urllib.error.URLError, OSError, ValueError):
        return {"status": "error", "detail": "GitHub API 연결 실패"}


def _guard(action: str, headers, origin: str) -> dict | None:
    """공통 인가 게이트. 통과하면 None, 실패하면 액션+상태 dict(트리거 없음)를 반환한다."""
    if not is_configured():
        return {"action": action, "status": "not_configured"}
    auth = authorize(headers, origin, action)
    if not auth["ok"]:
        reason = auth["reason"]
        status = {"forbidden_origin": "forbidden",
                  "rate_limited": "rate_limited",
                  "auth_required": "auth_required",
                  "not_configured": "not_configured"}.get(reason, "unauthorized")
        return {"action": action, "status": status}
    return None


def trigger_collect(headers=None, origin: str = "") -> dict:
    """뉴스 수집 워크플로(scheduled-live-refresh.yml)를 서버측 인가 후 트리거한다."""
    blocked = _guard("collect", headers, origin)
    if blocked:
        return blocked
    return {"action": "collect", **_dispatch(_COLLECT_WORKFLOW)}


def trigger_telegram(headers=None, origin: str = "") -> dict:
    """텔레그램 발송 워크플로(telegram-notify.yml)를 서버측 인가 후 트리거한다.

    인가를 통과한 호출에서만 approve_send="true"를 명시 전달해 실제 발송까지 간다. 워크플로 입력
    기본값은 빈 값(=검토만)이며, 자동발송 하드코딩은 없다.
    """
    blocked = _guard("telegram", headers, origin)
    if blocked:
        return blocked
    return {"action": "telegram",
            **_dispatch(_TELEGRAM_WORKFLOW, {"approve_send": "true"})}


def trigger_teams(headers=None, origin: str = "") -> dict:
    """Teams 채널 이메일 발송 워크플로(email-alert.yml)를 서버측 인가 후 트리거한다.

    인가를 통과한 호출에서만 approve_send_email="true"+send_to_teams="true"를 명시 전달해 executive
    brief를 Gmail SMTP로 Teams 채널 이메일 주소에 보낸다. 워크플로 입력 기본값은 false(=dry-run ·
    미발송)이며 자동발송 하드코딩은 없다 — 실제 발송은 이 인가 경로에서만 일어난다.
    """
    blocked = _guard("teams", headers, origin)
    if blocked:
        return blocked
    return {"action": "teams",
            **_dispatch(_TEAMS_WORKFLOW,
                        {"approve_send_email": "true", "send_to_teams": "true"})}
