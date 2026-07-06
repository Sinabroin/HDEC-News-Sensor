#!/usr/bin/env python3
"""로컬 Operator API 스모크 (D7-AG-3) — uvicorn/네트워크 없이 인가 계약을 검증한다.

배경: `python3 -m uvicorn ...`는 회사망/오프라인에서 실패할 수 있고, curl 스모크는 서버가 안 떠
connection refused가 된다. 이 스크립트는 서버를 띄우지 않고 게이트웨이(app/operator_gateway.py)를
직접 호출해 **무설정=fail-closed**와 **서버 앞단 인가(edge 신원·Origin·레이트리밋)**를 확인하고,
`_dispatch`를 스텁으로 가로채 **실제 GitHub workflow_dispatch(네트워크)는 절대 하지 않는다**.

D7-AG-3: 브라우저 PIN을 제거하고 보호를 서버측 인가로 이관했다. 이 스모크는 그 인가 계약을 잠근다.

이 스모크가 하는 것 / 절대 안 하는 것:
- 한다: (1) 무설정 시 collect/telegram/teams 모두 not_configured, (2) edge 모드에서 허용목록에 없는
  신원=unauthorized·허용 origin 아님=forbidden, (3) 허용 신원+허용 origin일 때만 '올바른 워크플로+
  승인 입력'으로 dispatch를 *스텁* 호출(네트워크 없음), (4) 레이트리밋 동작, (5) shared_secret 레거시
  경로, (6) app/operator_api.py 라우트/배선 소스 계약, (7) 응답에 비밀값 미포함.
- 안 한다: 실제 발송/dispatch, GitHub/Gmail/Telegram 네트워크 호출, 비밀값 출력.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

API_PY = ROOT / "app" / "operator_api.py"

_failures: list[str] = []

_TOKEN = "smoke-token-DO-NOT-USE"
_REPO = "smoke/owner-repo"
_SECRET = "smoke-secret-1234"
_SECRET_VALUES = (_TOKEN, _SECRET, _REPO)
_ALLOWED_ORIGIN = "https://sinabroin.github.io"
_USER = "ops@hdec.co.kr"
_HDR = "cf-access-authenticated-user-email"


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        _failures.append(name)
    return ok


def _no_secret_in(result: dict) -> bool:
    blob = repr(result)
    return not any(sv in blob for sv in _SECRET_VALUES)


def run() -> int:
    from app import config, operator_gateway as og

    saved = {k: getattr(config, k) for k in (
        "GH_OPERATOR_TOKEN", "OPERATOR_REPO", "OPERATOR_ACCESS_MODE",
        "OPERATOR_ACCESS_HEADER", "OPERATOR_ALLOWED_USERS", "OPERATOR_SHARED_SECRET",
        "OPERATOR_LOCAL_DEV", "OPERATOR_DRY_RUN", "OPERATOR_ALLOWED_ORIGINS",
        "OPERATOR_RATE_LIMIT_PER_MIN")}
    real_dispatch = og._dispatch
    dispatched: list[tuple] = []

    def stub_dispatch(workflow, inputs=None):
        dispatched.append((workflow, dict(inputs or {})))
        return {"status": "dispatched", "http_status": 204, "workflow": workflow}

    actions = {"collect": og.trigger_collect, "telegram": og.trigger_telegram,
               "teams": og.trigger_teams}
    allow_hdr = {_HDR: _USER}

    def reset(**over):
        config.GH_OPERATOR_TOKEN = over.get("token", _TOKEN)
        config.OPERATOR_REPO = over.get("repo", _REPO)
        config.OPERATOR_ACCESS_MODE = over.get("mode", "edge")
        config.OPERATOR_ACCESS_HEADER = _HDR
        config.OPERATOR_ALLOWED_USERS = over.get("users", [_USER])
        config.OPERATOR_SHARED_SECRET = over.get("secret", "")
        config.OPERATOR_LOCAL_DEV = over.get("local_dev", False)
        config.OPERATOR_DRY_RUN = False
        config.OPERATOR_ALLOWED_ORIGINS = [_ALLOWED_ORIGIN, "http://127.0.0.1:8088"]
        config.OPERATOR_RATE_LIMIT_PER_MIN = over.get("rate", 100)
        og._recent_triggers.clear()
        dispatched.clear()

    try:
        og._dispatch = stub_dispatch

        # 1) 무설정(fail-closed) — 토큰/repo/정책 없음 → 세 액션 not_configured, dispatch 미도달
        reset(token="", repo="", mode="", users=[])
        check("무설정 = is_configured() False", og.is_configured() is False)
        for name, fn in actions.items():
            r = fn(allow_hdr, _ALLOWED_ORIGIN)
            check(f"무설정 {name} → not_configured", r.get("status") == "not_configured", str(r))
            check(f"무설정 {name} 비밀값 없음", _no_secret_in(r))
        check("무설정 dispatch 미호출", not dispatched, f"{len(dispatched)}회")

        # 2) edge 설정 + 허용 신원 없음 → unauthorized (dispatch 미도달)
        reset()
        check("edge 설정 완료 = is_configured() True", og.is_configured() is True)
        for name, fn in actions.items():
            r = fn({}, _ALLOWED_ORIGIN)
            check(f"신원 없음 {name} → unauthorized", r.get("status") == "unauthorized", str(r))
        r = actions["teams"]({_HDR: "attacker@evil.com"}, _ALLOWED_ORIGIN)
        check("허용목록 밖 신원 → unauthorized", r.get("status") == "unauthorized", str(r))
        check("인가 실패 dispatch 미호출", not dispatched, f"{len(dispatched)}회")

        # 3) edge 설정 + 허용 origin 아님 → forbidden
        reset()
        r = actions["collect"](allow_hdr, "https://evil.example")
        check("허용 origin 아님 → forbidden", r.get("status") == "forbidden", str(r))
        check("forbidden dispatch 미호출", not dispatched, f"{len(dispatched)}회")

        # 4) edge 설정 + 허용 신원 + 허용 origin → dispatch(스텁) · 워크플로/승인 입력 계약
        reset()
        r_c = actions["collect"](allow_hdr, _ALLOWED_ORIGIN)
        r_t = actions["telegram"](allow_hdr, _ALLOWED_ORIGIN)
        r_m = actions["teams"](allow_hdr, _ALLOWED_ORIGIN)
        by_wf = {wf: inp for wf, inp in dispatched}
        check("허용 신원 collect → scheduled-live-refresh.yml (승인 입력 없음)",
              by_wf.get("scheduled-live-refresh.yml") == {}, str(dispatched))
        check("허용 신원 telegram → telegram-notify.yml + approve_send=true",
              by_wf.get("telegram-notify.yml") == {"approve_send": "true"}, str(dispatched))
        check("허용 신원 teams → email-alert.yml + approve_send_email·send_to_teams",
              by_wf.get("email-alert.yml") == {"approve_send_email": "true", "send_to_teams": "true"},
              str(dispatched))
        for name, r in (("collect", r_c), ("telegram", r_t), ("teams", r_m)):
            check(f"인가 후 {name} status dispatched", r.get("status") == "dispatched", str(r))
            check(f"인가 후 {name} 비밀값 없음(신원/토큰 미노출)",
                  _no_secret_in(r) and _USER not in repr(r))

        # 5) 레이트리밋 — 상한 초과 시 rate_limited (dispatch 미도달)
        reset(rate=2)
        actions["collect"](allow_hdr, _ALLOWED_ORIGIN)
        actions["collect"](allow_hdr, _ALLOWED_ORIGIN)
        r = actions["collect"](allow_hdr, _ALLOWED_ORIGIN)
        check("레이트리밋 초과 → rate_limited", r.get("status") == "rate_limited", str(r))

        # 6) shared_secret 레거시 경로 — 올바른 토큰=dispatch, 틀린 토큰=unauthorized
        reset(mode="shared_secret", users=[], secret=_SECRET)
        check("shared_secret 설정 = is_configured() True", og.is_configured() is True)
        r_ok = actions["collect"]({"x-operator-token": _SECRET}, _ALLOWED_ORIGIN)
        check("shared_secret 올바른 토큰 → dispatched", r_ok.get("status") == "dispatched", str(r_ok))
        r_bad = actions["collect"]({"x-operator-token": "wrong"}, _ALLOWED_ORIGIN)
        check("shared_secret 틀린 토큰 → unauthorized", r_bad.get("status") == "unauthorized", str(r_bad))

        # 7) dispatch 204 뒤 run 목록에서 run_id/run_url 조회 계약(스텁 응답)
        class _RunResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_a):
                return None

            def read(self):
                return json.dumps({"workflow_runs": [{
                    "id": 987654,
                    "html_url": "https://github.com/Sinabroin/HDEC-News-Sensor/actions/runs/987654",
                    "status": "queued",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }]}).encode("utf-8")

        real_request = og._github_request
        real_attempts = og._RUN_LOOKUP_ATTEMPTS
        smoke_repo = config.OPERATOR_REPO
        try:
            config.OPERATOR_REPO = "Sinabroin/HDEC-News-Sensor"
            og._github_request = lambda _url: _RunResponse()
            og._RUN_LOOKUP_ATTEMPTS = 1
            run = og._find_dispatched_run("scheduled-live-refresh.yml", datetime.now(timezone.utc))
            check("dispatch 후 run_id/run_url 조회",
                  run.get("run_id") == 987654 and str(run.get("run_url") or "").endswith("/987654"),
                  str(run))
        finally:
            og._github_request = real_request
            og._RUN_LOOKUP_ATTEMPTS = real_attempts
            config.OPERATOR_REPO = smoke_repo

    finally:
        og._dispatch = real_dispatch
        for k, v in saved.items():
            setattr(config, k, v)
        og._recent_triggers.clear()

    # 8) 소스 계약 — 3개 POST 라우트 + 게이트웨이 배선
    api_src = API_PY.read_text(encoding="utf-8")
    for rt in ('@router.post("/api/operator/collect")',
               '@router.post("/api/operator/send")',
               '@router.post("/api/operator/send-teams")'):
        check(f"라우트 존재: {rt}", rt in api_src)
    check("legacy send-telegram alias 유지",
          '@router.post("/api/operator/send-telegram"' in api_src)
    for wired in ("operator_gateway.trigger_collect(", "operator_gateway.trigger_telegram(",
                  "operator_gateway.trigger_teams("):
        check(f"배선: {wired}", wired in api_src)
    check("요청 헤더/Origin을 게이트웨이에 전달", "request.headers" in api_src)

    # 9) (선택) fastapi가 있으면 실제 라우트 등록 확인 — 없으면 소스 계약으로 대체.
    #    신버전 fastapi는 include_router를 지연(lazy) 처리해 app.routes에 _IncludedRouter
    #    placeholder만 두고 실제 라우트는 original_router.routes에 있다(구버전은 flat). 두 표현
    #    모두에서 등록을 확인하도록 재귀 수집한다 — 계약(4개 라우트 등록)은 동일, fastapi 버전 불변.
    try:
        import fastapi  # noqa: F401
        from app.operator_api import app as fastapi_app

        def _registered_paths(app_obj) -> set:
            seen: set[int] = set()
            found: set = set()

            def walk(routes) -> None:
                for r in routes or ():
                    if id(r) in seen:
                        continue
                    seen.add(id(r))
                    path = getattr(r, "path", None)
                    if isinstance(path, str):
                        found.add(path)
                    walk(getattr(r, "routes", None))
                    included = getattr(r, "original_router", None)
                    if included is not None:
                        walk(getattr(included, "routes", None))

            walk(getattr(app_obj, "routes", []))
            return found

        paths = _registered_paths(fastapi_app)
        for p in ("/api/operator/health", "/api/operator/collect", "/api/operator/send",
                  "/api/operator/send-teams"):
            check(f"FastAPI 등록 라우트: {p}", p in paths)
    except ImportError:
        print("[INFO] fastapi 미설치 — 라우트 등록 확인은 소스 계약으로 대체.")

    print()
    if _failures:
        print(f"RESULT: FAIL ({len(_failures)} 항목)")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("RESULT: PASS — Operator API 인가 계약 정상(무설정 fail-closed · edge 신원/Origin/레이트 · "
          "인가 경로만 dispatch) · 실제 발송/네트워크 0건 · uvicorn 불필요")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
