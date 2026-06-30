"""Operator Gateway 도메인 (leaf, P0-D7-AA) — 운영자 액션을 서버측에서 실행한다.

공개 정적 대시보드(GitHub Pages)에는 서버가 없으므로, "데이터 새로고침"/"텔레그램 전송"
버튼은 이 게이트웨이가 노출하는 Operator API(app/main.py의 POST 라우트)를 호출한다. 이 leaf는
운영자 승인(PIN)을 검증한 뒤 GitHub Actions의 workflow_dispatch를 호출해 기존 워크플로를
재사용한다 — 수집은 scheduled-live-refresh.yml, 발송은 telegram-notify.yml.

경계(이 파일만 한다 / 절대 안 한다):
- 한다: PIN 검증, GitHub workflow_dispatch 호출(이 파일이 네트워크 단일 소유 · urllib).
- 안 한다: 점수/insight/수집/발송 로직 자체, DB 접근, 비밀값 노출.

안전 계약 (rules.md §1/§4):
- 비밀값(GH_OPERATOR_TOKEN, OPERATOR_SHARED_SECRET)은 config(=env)에서만 읽는다. 어떤 응답/
  로그/예외 메시지에도 비밀값을 싣지 않는다(상태 코드/중립 메시지만 반환).
- fail-closed: 토큰·repo·PIN 셋 중 하나라도 비어 있으면 어떤 트리거도 하지 않고 not_configured.
- PIN 불일치 → unauthorized (트리거 없음). 상수시간 비교(hmac.compare_digest).
- TELEGRAM_AUTO_SEND 같은 자동발송 하드코딩은 없다. 텔레그램 발송은 PIN 검증을 통과한
  명시적 운영자 호출에서만 approve_send="true"를 워크플로 입력으로 넘긴다(워크플로 기본값은
  빈 값 = 비발송). 즉 "버튼 → 즉시 발송"은 인증된 이 경로에서만 일어난다.
"""

import hmac
import json
import urllib.error
import urllib.request

from app import config

_API_BASE = "https://api.github.com"
_COLLECT_WORKFLOW = "scheduled-live-refresh.yml"
_TELEGRAM_WORKFLOW = "telegram-notify.yml"
_DISPATCH_REF = "main"
_TIMEOUT_SECONDS = 20


def is_configured() -> bool:
    """트리거에 필요한 서버측 설정이 모두 있는가 (토큰·repo·PIN). 비밀값을 노출하지 않는다."""
    return bool(config.GH_OPERATOR_TOKEN
                and config.OPERATOR_REPO
                and config.OPERATOR_SHARED_SECRET)


def _pin_ok(pin: str) -> bool:
    secret = config.OPERATOR_SHARED_SECRET
    if not secret:
        return False
    return hmac.compare_digest(str(pin or ""), str(secret))


def _dispatch(workflow: str, inputs: dict | None = None) -> dict:
    """GitHub Actions workflow_dispatch 호출. 성공 시 2xx. 비밀값은 반환하지 않는다."""
    url = f"{_API_BASE}/repos/{config.OPERATOR_REPO}/actions/workflows/{workflow}/dispatches"
    body = {"ref": _DISPATCH_REF}
    if inputs:
        body["inputs"] = inputs
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Authorization", f"Bearer {config.GH_OPERATOR_TOKEN}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
            return {"status": "dispatched", "http_status": resp.getcode(),
                    "workflow": workflow}
    except urllib.error.HTTPError as exc:
        # 토큰/권한/입력 문제 — 상태 코드만 노출하고 본문(비밀값 가능)은 싣지 않는다.
        return {"status": "error", "http_status": exc.code,
                "detail": "GitHub workflow_dispatch 거부 (권한·입력 확인 필요)"}
    except (urllib.error.URLError, OSError, ValueError):
        return {"status": "error", "detail": "GitHub API 연결 실패"}


def trigger_collect(pin: str) -> dict:
    """뉴스 수집 워크플로(scheduled-live-refresh.yml)를 운영자 승인 후 트리거한다."""
    if not is_configured():
        return {"action": "collect", "status": "not_configured"}
    if not _pin_ok(pin):
        return {"action": "collect", "status": "unauthorized"}
    return {"action": "collect", **_dispatch(_COLLECT_WORKFLOW)}


def trigger_telegram(pin: str) -> dict:
    """텔레그램 발송 워크플로(telegram-notify.yml)를 운영자 승인 후 트리거한다.

    PIN 검증을 통과한 인증 호출에서만 approve_send="true"를 명시 전달해 실제 발송까지 간다.
    워크플로 입력 기본값은 빈 값(=검토만)이며, 자동발송 하드코딩은 없다.
    """
    if not is_configured():
        return {"action": "telegram", "status": "not_configured"}
    if not _pin_ok(pin):
        return {"action": "telegram", "status": "unauthorized"}
    return {"action": "telegram",
            **_dispatch(_TELEGRAM_WORKFLOW, {"approve_send": "true"})}
