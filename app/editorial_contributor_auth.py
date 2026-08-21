"""Isolated team-contributor authentication for pending editorial intake.

This role can submit a server-revalidated article for review. It is deliberately
not an operator identity and its token can never satisfy GitHub operator routes.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from http.cookies import SimpleCookie

from app import config

_SIGNATURE_DOMAIN = b"hdec-editorial-contributor-session-v1\0"
_ROLE = "editorial_contributor"
_CODE_MAX_BYTES = 256


def configured() -> bool:
    return bool(
        config.OPERATOR_SESSION_SECRET
        and re.fullmatch(r"[0-9a-f]{64}", config.EDITORIAL_CONTRIBUTOR_CODE_SHA256)
    )


def valid_code(value: object) -> bool:
    """Hash a bounded supplied code and compare without exposing either value."""
    if not configured() or not isinstance(value, str):
        return False
    encoded = value.encode("utf-8")
    if not encoded or len(encoded) > _CODE_MAX_BYTES:
        return False
    supplied = hashlib.sha256(encoded).hexdigest()
    return hmac.compare_digest(supplied, config.EDITORIAL_CONTRIBUTOR_CODE_SHA256)


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _signature(payload: str) -> str:
    digest = hmac.new(
        config.OPERATOR_SESSION_SECRET.encode("utf-8"),
        _SIGNATURE_DOMAIN + payload.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return _b64e(digest)


def create_session_token(*, now: int | None = None) -> str:
    if not configured():
        raise ValueError("contributor_auth_not_configured")
    issued = int(time.time() if now is None else now)
    payload = {
        "exp": issued + int(config.EDITORIAL_CONTRIBUTOR_SESSION_MAX_AGE_SECONDS),
        "role": _ROLE,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    encoded = _b64e(raw)
    return encoded + "." + _signature(encoded)


def verify_session_token(value: object, *, now: int | None = None) -> dict | None:
    if not configured():
        return None
    try:
        payload_b64, supplied = str(value or "").split(".", 1)
    except ValueError:
        return None
    if not hmac.compare_digest(supplied, _signature(payload_b64)):
        return None
    try:
        payload = json.loads(_b64d(payload_b64).decode("utf-8"))
        exp = int(payload.get("exp"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    current = int(time.time() if now is None else now)
    if set(payload) != {"exp", "role"} or payload.get("role") != _ROLE or exp <= current:
        return None
    return {"role": _ROLE, "exp": exp}


def session_from_cookie_header(cookie_header: object) -> dict | None:
    jar = SimpleCookie()
    try:
        jar.load(str(cookie_header or ""))
    except Exception:
        return None
    morsel = jar.get(config.EDITORIAL_CONTRIBUTOR_SESSION_COOKIE)
    return verify_session_token(morsel.value if morsel else "")


def session_from_headers(headers: object) -> dict | None:
    getter = getattr(headers, "get", None)
    cookie = getter("cookie", "") if callable(getter) else ""
    return session_from_cookie_header(cookie)


def set_session_cookie(response: object) -> None:
    response.set_cookie(
        config.EDITORIAL_CONTRIBUTOR_SESSION_COOKIE,
        create_session_token(),
        max_age=config.EDITORIAL_CONTRIBUTOR_SESSION_MAX_AGE_SECONDS,
        httponly=True,
        secure=True,
        samesite="none",
        path="/",
    )


def clear_session_cookie(response: object) -> None:
    response.set_cookie(
        config.EDITORIAL_CONTRIBUTOR_SESSION_COOKIE,
        "",
        max_age=0,
        expires=0,
        httponly=True,
        secure=True,
        samesite="none",
        path="/",
    )


__all__ = [
    "clear_session_cookie",
    "configured",
    "create_session_token",
    "session_from_cookie_header",
    "session_from_headers",
    "set_session_cookie",
    "valid_code",
    "verify_session_token",
]
