#!/usr/bin/env python3
"""로컬 Operator API 스모크 (D7-AD-V) — uvicorn/네트워크 없이 endpoint 계약을 검증한다.

배경: `python3 -m uvicorn app.main:app ...`는 회사망/오프라인에서 `No module named uvicorn`으로
실패할 수 있고, curl 스모크는 서버가 안 떠 connection refused가 된다. 이 스크립트는 서버를 띄우지
않고 도메인 게이트웨이(app/operator_gateway.py)를 직접 호출해 **무설정=fail-closed**를 확인하고,
`_dispatch`를 스텁으로 가로채 **실제 GitHub workflow_dispatch(네트워크)는 절대 하지 않는다**.

이 스모크가 하는 것 / 절대 안 하는 것:
- 한다: (1) 무설정 시 collect/telegram/teams 모두 not_configured, (2) 설정+PIN 불일치 시 unauthorized,
  (3) 설정+PIN 일치 시 '올바른 워크플로+승인 입력'으로 dispatch를 *스텁* 호출(네트워크 없음),
  (4) app/main.py에 3개 POST 라우트가 존재하고 trigger_*에 배선됐는지 소스 계약 확인,
  (5) 응답 dict에 비밀값이 실리지 않는지 확인.
- 안 한다: 실제 발송/dispatch, GitHub/Gmail/Telegram 네트워크 호출, 비밀값 출력.

종속성: 표준 라이브러리 + app.config/app.operator_gateway만 필요(uvicorn/fastapi/httpx 불필요).
fastapi가 설치돼 있으면 app.main의 라우트 등록까지 추가 확인한다(없으면 소스 계약으로 대체).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MAIN_PY = ROOT / "app" / "main.py"

_failures: list[str] = []

_SECRET_VALUES = ("smoke-token-DO-NOT-USE", "smoke-pin-1234", "smoke/owner-repo")


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        _failures.append(name)
    return ok


def _no_secret_in(result: dict) -> bool:
    """응답 dict 어디에도 비밀 '값'이 실리지 않아야 한다(상태/액션/워크플로 코드만 허용)."""
    blob = repr(result)
    return not any(sv in blob for sv in _SECRET_VALUES)


def run() -> int:
    from app import config, operator_gateway as og

    # --- 원상복구용 백업 (env가 이미 설정돼 있을 수 있으므로 config 속성을 직접 제어) ---
    saved = {
        "GH_OPERATOR_TOKEN": config.GH_OPERATOR_TOKEN,
        "OPERATOR_REPO": config.OPERATOR_REPO,
        "OPERATOR_SHARED_SECRET": config.OPERATOR_SHARED_SECRET,
    }
    real_dispatch = og._dispatch
    dispatched: list[tuple] = []

    def stub_dispatch(workflow, inputs=None):
        # 네트워크 대신 호출을 기록만 한다 — 실제 workflow_dispatch는 절대 하지 않는다.
        dispatched.append((workflow, dict(inputs or {})))
        return {"status": "dispatched", "http_status": 204, "workflow": workflow}

    actions = {
        "collect": og.trigger_collect,
        "telegram": og.trigger_telegram,
        "teams": og.trigger_teams,
    }

    try:
        og._dispatch = stub_dispatch  # 이후 어떤 경로도 실제 네트워크로 가지 않는다

        # 1) 무설정(fail-closed) — 세 액션 모두 not_configured, dispatch 미도달
        config.GH_OPERATOR_TOKEN = ""
        config.OPERATOR_REPO = ""
        config.OPERATOR_SHARED_SECRET = ""
        dispatched.clear()
        check("게이트웨이 무설정 = is_configured() False", og.is_configured() is False)
        for name, fn in actions.items():
            r = fn("")  # PIN 없이 호출
            check(f"무설정 {name} → not_configured (fail-closed)",
                  r.get("status") == "not_configured" and r.get("action") == name, str(r))
            check(f"무설정 {name} 응답에 비밀값 없음", _no_secret_in(r))
        check("무설정 경로에서 dispatch 미호출(네트워크 0건)", not dispatched,
              f"{len(dispatched)}회 호출됨")

        # 2) 설정 + PIN 불일치 → unauthorized, dispatch 미도달
        config.GH_OPERATOR_TOKEN = _SECRET_VALUES[0]
        config.OPERATOR_REPO = _SECRET_VALUES[2]
        config.OPERATOR_SHARED_SECRET = _SECRET_VALUES[1]
        dispatched.clear()
        check("게이트웨이 설정 완료 = is_configured() True", og.is_configured() is True)
        for name, fn in actions.items():
            r = fn("wrong-pin")
            check(f"설정+오PIN {name} → unauthorized",
                  r.get("status") == "unauthorized" and r.get("action") == name, str(r))
            check(f"설정+오PIN {name} 응답에 비밀값 없음", _no_secret_in(r))
        check("PIN 불일치 경로에서 dispatch 미호출(네트워크 0건)", not dispatched,
              f"{len(dispatched)}회 호출됨")

        # 3) 설정 + PIN 일치 → dispatch(스텁)까지 도달 · 워크플로/승인 입력 계약 확인
        #    (스텁이므로 실제 발송/네트워크는 없음 — 승인된 경로가 '무엇을' 트리거하는지만 검증)
        dispatched.clear()
        r_collect = actions["collect"](_SECRET_VALUES[1])
        r_tg = actions["telegram"](_SECRET_VALUES[1])
        r_teams = actions["teams"](_SECRET_VALUES[1])
        by_wf = {wf: inp for wf, inp in dispatched}
        check("승인 후 collect → scheduled-live-refresh.yml dispatch(승인 입력 없음)",
              "scheduled-live-refresh.yml" in by_wf
              and by_wf.get("scheduled-live-refresh.yml") == {}, str(dispatched))
        check("승인 후 telegram → telegram-notify.yml + approve_send=true",
              by_wf.get("telegram-notify.yml") == {"approve_send": "true"}, str(dispatched))
        check("승인 후 teams → email-alert.yml + approve_send_email=true·send_to_teams=true",
              by_wf.get("email-alert.yml")
              == {"approve_send_email": "true", "send_to_teams": "true"}, str(dispatched))
        for name, r in (("collect", r_collect), ("telegram", r_tg), ("teams", r_teams)):
            check(f"승인 후 {name} 응답에 비밀값 없음", _no_secret_in(r))
        check("승인 경로도 실제 네트워크 대신 스텁만 호출(발송 0건)", len(dispatched) == 3,
              f"{len(dispatched)}회")

    finally:
        og._dispatch = real_dispatch
        for k, v in saved.items():
            setattr(config, k, v)

    # 4) 소스 계약 — app/main.py에 3개 POST 라우트가 존재하고 게이트웨이에 배선됐는지
    main_src = MAIN_PY.read_text(encoding="utf-8")
    routes = ('@app.post("/api/operator/collect")',
              '@app.post("/api/operator/send-telegram")',
              '@app.post("/api/operator/send-teams")')
    for rt in routes:
        check(f"라우트 존재: {rt}", rt in main_src)
    check("collect 라우트가 trigger_collect에 배선",
          "operator_gateway.trigger_collect(" in main_src)
    check("send-telegram 라우트가 trigger_telegram에 배선",
          "operator_gateway.trigger_telegram(" in main_src)
    check("send-teams 라우트가 trigger_teams에 배선",
          "operator_gateway.trigger_teams(" in main_src)

    # 5) (선택) fastapi가 있으면 실제 라우트 등록까지 확인 — 없으면 소스 계약으로 대체
    try:
        import fastapi  # noqa: F401
        from app.main import app as fastapi_app
        paths = {getattr(r, "path", None) for r in fastapi_app.routes}
        for p in ("/api/operator/collect", "/api/operator/send-telegram",
                  "/api/operator/send-teams"):
            check(f"FastAPI 등록 라우트: {p}", p in paths)
    except ImportError:
        print("[INFO] fastapi 미설치 — 라우트 등록 확인은 소스 계약으로 대체(스모크 통과에 영향 없음).")

    print()
    if _failures:
        print(f"RESULT: FAIL ({len(_failures)} 항목)")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("RESULT: PASS — Operator API 계약 정상(무설정 fail-closed · 승인 경로만 dispatch) · "
          "실제 발송/네트워크 0건 · uvicorn 불필요")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
