"""Canonical public URL contract for Pages renderers and senders."""

from __future__ import annotations

from urllib.parse import urlparse


PUBLIC_ROOT = "https://guides.playground-aidesignlab.co.kr/HDEC-News-Sensor"
CANONICAL_DASHBOARD_URL = f"{PUBLIC_ROOT}/daily/dashboard-latest.html"
COMPATIBILITY_DASHBOARD_URL = f"{PUBLIC_ROOT}/news-censor/latest.html"
DAILY_LATEST_URL = f"{PUBLIC_ROOT}/editorial/daily/latest.html"
WEEKLY_LATEST_URL = f"{PUBLIC_ROOT}/editorial/weekly/latest.html"


def canonical_dashboard_url(value: object = "") -> str:
    """Return an explicitly valid HTTP URL or the canonical dashboard URL."""
    candidate = " ".join(str(value or "").split())
    try:
        parsed = urlparse(candidate)
    except ValueError:
        parsed = None
    if parsed and parsed.scheme in {"http", "https"} and parsed.netloc:
        return candidate
    return CANONICAL_DASHBOARD_URL


def latest_brief_url(edition_type: str) -> str:
    if edition_type == "daily":
        return DAILY_LATEST_URL
    if edition_type == "weekly":
        return WEEKLY_LATEST_URL
    raise ValueError(f"unsupported edition type: {edition_type!r}")
