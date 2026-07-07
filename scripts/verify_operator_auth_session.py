#!/usr/bin/env python3
"""D7-AG-5C GitHub OAuth operator session verifier (offline, no network/dispatch)."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

API = ROOT / "app" / "operator_api.py"
AUTH = ROOT / "app" / "operator_auth.py"
GATEWAY = ROOT / "app" / "operator_gateway.py"
DASHBOARD = ROOT / "docs" / "daily" / "dashboard-latest.html"
TEMPLATE = ROOT / "templates" / "dashboard_preview.html"

ALLOWED_ORIGIN = "https://guides.playground-aidesignlab.co.kr"
LOGIN = "Sinabroin"
SECRET_SHAPES = (
    re.compile(r"\b(?:ghp_|github_pat_)[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\b\d{8,}:[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"https://[^\"'\s]*(?:webhook\.office\.com|powerautomate)[^\"'\s]*", re.I),
)
SERVER_ONLY_STRINGS = (
    "GITHUB_OAUTH_CLIENT_SECRET",
    "OPERATOR_SESSION_SECRET",
    "api.telegram.org",
    "webhook.office.com",
    "X-Naver-Client-Secret",
)

_failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        _failures.append(name)
    return ok


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _model(html: str) -> dict:
    m = re.search(r'<script type="application/json" id="preview-model">(.*?)</script>',
                  html, re.S)
    try:
        return json.loads(m.group(1)) if m else {}
    except json.JSONDecodeError:
        return {}


def check_sources() -> None:
    api = read(API)
    auth = read(AUTH)
    gateway = read(GATEWAY)
    for route in (
        '@router.get("/api/auth/github/login")',
        '@router.get("/api/auth/github/callback")',
        '@router.get("/api/auth/session")',
        '@router.post("/api/auth/logout")',
    ):
        check(f"auth route exists: {route}", route in api)
    check("OAuth authorize uses GitHub and state cookie",
          "github_authorize_url(state)" in api
          and "OPERATOR_OAUTH_STATE_COOKIE" in auth
          and "state" in auth)
    check("OAuth token exchange uses Accept application/json",
          "_GITHUB_TOKEN_URL" in auth and 'req.add_header("Accept", "application/json")' in auth)
    check("GitHub user API login check",
          "_GITHUB_USER_URL" in auth and "fetch_github_login" in api
          and "allowed_github_login" in api)
    check("session cookie contract",
          "httponly=True" in auth and "secure=True" in auth and 'samesite="none"' in auth
          and "path=\"/\"" in auth and "OPERATOR_SESSION_MAX_AGE_SECONDS" in auth)
    check("stateless HMAC session",
          "hmac.new" in auth and "hashlib.sha256" in auth and "hmac.compare_digest" in auth
          and "login" in auth and "exp" in auth)
    check("gateway origin send checks session",
          "operator_auth.session_from_headers" in gateway and '"auth_required"' in gateway)
    check("CORS credentials enabled",
          "allow_credentials=True" in api and '"OPTIONS"' in api)


def check_session_crypto() -> None:
    from app import config, operator_auth

    saved = {k: getattr(config, k) for k in (
        "OPERATOR_SESSION_SECRET", "OPERATOR_ALLOWED_GITHUB_LOGINS",
        "OPERATOR_SESSION_MAX_AGE_SECONDS",
    )}
    try:
        config.OPERATOR_SESSION_SECRET = "verify-session-secret"
        config.OPERATOR_ALLOWED_GITHUB_LOGINS = [LOGIN.lower()]
        config.OPERATOR_SESSION_MAX_AGE_SECONDS = 8 * 60 * 60
        token = operator_auth.create_session_token(LOGIN, now=1000)
        ok = operator_auth.verify_session_token(token, now=1001)
        check("valid session verifies login/exp", ok and ok.get("login") == LOGIN, repr(ok))
        check("tampered session rejected",
              operator_auth.verify_session_token(token + "x", now=1001) is None)
        check("expired session rejected",
              operator_auth.verify_session_token(token, now=1000 + 8 * 60 * 60 + 1) is None)
        config.OPERATOR_ALLOWED_GITHUB_LOGINS = ["someone-else"]
        check("login allowlist enforced",
              operator_auth.verify_session_token(token, now=1001) is None)
    finally:
        for key, value in saved.items():
            setattr(config, key, value)


def check_gateway_contract() -> None:
    from app import config, operator_auth, operator_gateway as og

    saved = {k: getattr(config, k) for k in (
        "GH_OPERATOR_TOKEN", "OPERATOR_REPO", "OPERATOR_ACCESS_MODE",
        "OPERATOR_ALLOWED_ORIGINS", "OPERATOR_RATE_LIMIT_PER_MIN",
        "OPERATOR_LOCAL_DEV", "OPERATOR_DRY_RUN",
        "OPERATOR_SESSION_SECRET", "OPERATOR_ALLOWED_GITHUB_LOGINS",
        "OPERATOR_SESSION_MAX_AGE_SECONDS",
    )}
    real_dispatch = og._dispatch
    dispatched: list[tuple[str, dict]] = []

    def stub_dispatch(workflow, inputs=None):
        dispatched.append((workflow, dict(inputs or {})))
        return {"status": "dispatched", "workflow": workflow, "http_status": 202}

    try:
        config.GH_OPERATOR_TOKEN = "verify-token"
        config.OPERATOR_REPO = "Sinabroin/HDEC-News-Sensor"
        config.OPERATOR_ACCESS_MODE = "origin"
        config.OPERATOR_ALLOWED_ORIGINS = [ALLOWED_ORIGIN]
        config.OPERATOR_RATE_LIMIT_PER_MIN = 100
        config.OPERATOR_LOCAL_DEV = False
        config.OPERATOR_DRY_RUN = False
        config.OPERATOR_SESSION_SECRET = "verify-session-secret"
        config.OPERATOR_ALLOWED_GITHUB_LOGINS = [LOGIN.lower()]
        config.OPERATOR_SESSION_MAX_AGE_SECONDS = 8 * 60 * 60
        og._dispatch = stub_dispatch
        og._recent_triggers.clear()

        r_collect = og.trigger_collect({}, ALLOWED_ORIGIN)
        check("origin collect remains public allowed",
              r_collect.get("status") == "dispatched"
              and ("scheduled-live-refresh.yml", {}) in dispatched,
              repr(r_collect))
        before = len(dispatched)
        r_send = og.trigger_telegram({}, ALLOWED_ORIGIN)
        r_teams = og.trigger_teams({}, ALLOWED_ORIGIN)
        check("origin telegram without session is auth_required",
              r_send.get("status") == "auth_required", repr(r_send))
        check("origin teams without session is auth_required",
              r_teams.get("status") == "auth_required", repr(r_teams))
        check("unauthenticated sends do not dispatch", len(dispatched) == before,
              repr(dispatched))

        token = operator_auth.create_session_token(LOGIN)
        headers = {"cookie": f"{config.OPERATOR_SESSION_COOKIE}={token}"}
        r_send_ok = og.trigger_telegram(headers, ALLOWED_ORIGIN)
        r_teams_ok = og.trigger_teams(headers, ALLOWED_ORIGIN)
        by_wf = {wf: inp for wf, inp in dispatched}
        check("origin telegram with valid session dispatches",
              r_send_ok.get("status") == "dispatched"
              and by_wf.get("telegram-notify.yml") == {"approve_send": "true"},
              repr(r_send_ok))
        check("origin teams with valid session dispatches",
              r_teams_ok.get("status") == "dispatched"
              and by_wf.get("email-alert.yml") == {
                  "approve_send_email": "true", "send_to_teams": "true",
              },
              repr(r_teams_ok))
    finally:
        og._dispatch = real_dispatch
        og._recent_triggers.clear()
        for key, value in saved.items():
            setattr(config, key, value)


def check_dashboard() -> None:
    for path, label in ((TEMPLATE, "template"), (DASHBOARD, "committed")):
        html = read(path)
        check(f"{label}: dashboard HTML exists", bool(html))
        if not html:
            continue
        check(f"{label}: login/session UI",
              "GitHub로 운영자 로그인" in html and "운영자 인증됨:" in html
              and 'id="opLoginBtn"' in html and 'id="opLogoutBtn"' in html
              and 'id="opSessionState"' in html)
        check(f"{label}: auth session endpoints in JS",
              "/api/auth/session" in html and "/api/auth/github/login" in html
              and "/api/auth/logout" in html and 'credentials: "include"' in html)
        check(f"{label}: unauthenticated send lock kept",
              "authlocked" in html and "showSendLocked" in html
              and "GitHub 운영자 로그인 후" in html)
        check(f"{label}: no public secrets/token/webhook",
              not any(s in html for s in SERVER_ONLY_STRINGS)
              and not any(pattern.search(html) for pattern in SECRET_SHAPES))
        model = _model(html)
        if label == "committed" and model:
            check("committed: collect endpoint preserved",
                  model.get("operator_endpoints", {}).get("collect") == "/api/operator/collect")


def main() -> int:
    print(f"== D7-AG-5C operator auth session @ {ROOT} ==")
    check_sources()
    check_session_crypto()
    check_gateway_contract()
    check_dashboard()
    print()
    if _failures:
        print(f"RESULT: FAIL ({len(_failures)} failed)")
        for failure in _failures:
            print(f"  - {failure}")
        return 1
    print("RESULT: PASS — GitHub OAuth session contract, origin collect, authenticated sends")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
