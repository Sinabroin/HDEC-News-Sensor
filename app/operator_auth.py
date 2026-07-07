"""GitHub OAuth + stateless operator session helpers.

Only server-side routes import this module. Secrets are read from app.config and never returned to
the browser. The public dashboard receives only the Operator API base URL and uses credentialed
fetches so this host's HttpOnly session cookie can be checked by the gateway.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from http.cookies import SimpleCookie

from app import config

_GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
_GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
_GITHUB_USER_URL = "https://api.github.com/user"
_TIMEOUT_SECONDS = 15


def oauth_configured() -> bool:
    """Return whether OAuth/session env is sufficient for login and callback handling."""
    return bool(
        config.GITHUB_OAUTH_CLIENT_ID
        and config.GITHUB_OAUTH_CLIENT_SECRET
        and config.OPERATOR_SESSION_SECRET
        and config.OPERATOR_AUTH_CALLBACK_URL
        and config.OPERATOR_ALLOWED_GITHUB_LOGINS
    )


def generate_state() -> str:
    return secrets.token_urlsafe(32)


def github_authorize_url(state: str) -> str:
    params = {
        "client_id": config.GITHUB_OAUTH_CLIENT_ID,
        "redirect_uri": config.OPERATOR_AUTH_CALLBACK_URL,
        "state": state,
        "allow_signup": "false",
    }
    # Empty scope keeps OAuth access minimal; /user still returns the account login.
    return _GITHUB_AUTHORIZE_URL + "?" + urllib.parse.urlencode(params)


def allowed_github_login(login: str) -> bool:
    return bool(login and login.strip().lower() in set(config.OPERATOR_ALLOWED_GITHUB_LOGINS))


def exchange_code_for_token(code: str) -> str:
    body = urllib.parse.urlencode({
        "client_id": config.GITHUB_OAUTH_CLIENT_ID,
        "client_secret": config.GITHUB_OAUTH_CLIENT_SECRET,
        "code": code,
        "redirect_uri": config.OPERATOR_AUTH_CALLBACK_URL,
    }).encode("utf-8")
    req = urllib.request.Request(_GITHUB_TOKEN_URL, data=body, method="POST")
    req.add_header("Accept", "application/json")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    token = str(payload.get("access_token") or "")
    if not token:
        raise ValueError("missing_access_token")
    return token


def fetch_github_login(access_token: str) -> str:
    req = urllib.request.Request(_GITHUB_USER_URL, method="GET")
    req.add_header("Authorization", f"Bearer {access_token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    login = str(payload.get("login") or "")
    if not login:
        raise ValueError("missing_github_login")
    return login


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + ("=" * (-len(text) % 4)))


def _session_signature(payload_b64: str) -> str:
    digest = hmac.new(
        config.OPERATOR_SESSION_SECRET.encode("utf-8"),
        payload_b64.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return _b64e(digest)


def create_session_token(login: str, now: int | None = None) -> str:
    if not config.OPERATOR_SESSION_SECRET:
        raise ValueError("session_secret_not_configured")
    ts = int(now if now is not None else time.time())
    payload = {
        "login": str(login),
        "exp": ts + int(config.OPERATOR_SESSION_MAX_AGE_SECONDS),
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    payload_b64 = _b64e(raw.encode("utf-8"))
    return payload_b64 + "." + _session_signature(payload_b64)


def verify_session_token(token: str, now: int | None = None) -> dict | None:
    if not token or not config.OPERATOR_SESSION_SECRET:
        return None
    try:
        payload_b64, sig = str(token).split(".", 1)
    except ValueError:
        return None
    expected = _session_signature(payload_b64)
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        payload = json.loads(_b64d(payload_b64).decode("utf-8"))
    except (ValueError, TypeError, json.JSONDecodeError):
        return None
    login = str(payload.get("login") or "")
    try:
        exp = int(payload.get("exp"))
    except (TypeError, ValueError):
        return None
    ts = int(now if now is not None else time.time())
    if exp <= ts or not allowed_github_login(login):
        return None
    return {"login": login, "exp": exp}


def session_from_cookie_header(cookie_header: str) -> dict | None:
    if not cookie_header:
        return None
    jar = SimpleCookie()
    try:
        jar.load(cookie_header)
    except Exception:
        return None
    morsel = jar.get(config.OPERATOR_SESSION_COOKIE)
    return verify_session_token(morsel.value if morsel else "")


def _header_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("latin-1", errors="ignore")
    return str(value)


def _headers_get(headers, name: str) -> str:
    if not headers:
        return ""
    target = name.lower()

    get = getattr(headers, "get", None)
    if callable(get):
        for key in (name, target, name.title()):
            value = get(key)
            if value:
                return _header_text(value)

    try:
        items = headers.items()
    except AttributeError:
        items = headers
    values = []
    for key, value in items:
        if _header_text(key).lower() == target:
            text = _header_text(value)
            if text:
                values.append(text)
    return "; ".join(values)


def session_from_headers(headers) -> dict | None:
    return session_from_cookie_header(_headers_get(headers, "cookie"))


def set_state_cookie(response, state: str) -> None:
    response.set_cookie(
        config.OPERATOR_OAUTH_STATE_COOKIE,
        state,
        max_age=config.OPERATOR_OAUTH_STATE_MAX_AGE_SECONDS,
        httponly=True,
        secure=True,
        samesite="none",
        path="/",
    )


def clear_state_cookie(response) -> None:
    response.set_cookie(
        config.OPERATOR_OAUTH_STATE_COOKIE,
        "",
        max_age=0,
        expires=0,
        httponly=True,
        secure=True,
        samesite="none",
        path="/",
    )


def set_session_cookie(response, login: str) -> None:
    response.set_cookie(
        config.OPERATOR_SESSION_COOKIE,
        create_session_token(login),
        max_age=config.OPERATOR_SESSION_MAX_AGE_SECONDS,
        httponly=True,
        secure=True,
        samesite="none",
        path="/",
    )


def clear_session_cookie(response) -> None:
    response.set_cookie(
        config.OPERATOR_SESSION_COOKIE,
        "",
        max_age=0,
        expires=0,
        httponly=True,
        secure=True,
        samesite="none",
        path="/",
    )
