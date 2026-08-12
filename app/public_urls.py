"""Canonical public URL contract for Pages renderers and senders."""

from __future__ import annotations

import re
from datetime import date
from urllib.parse import urlencode, urlparse


PUBLIC_ROOT = "https://guides.playground-aidesignlab.co.kr/HDEC-News-Sensor"
CANONICAL_DASHBOARD_URL = f"{PUBLIC_ROOT}/daily/dashboard-latest.html"
COMPATIBILITY_DASHBOARD_URL = f"{PUBLIC_ROOT}/news-censor/latest.html"
DAILY_LATEST_URL = f"{PUBLIC_ROOT}/editorial/daily/latest.html"
WEEKLY_LATEST_URL = f"{PUBLIC_ROOT}/editorial/weekly/latest.html"

# R4-R9C — immutable Daily edition identity for the operator editor deep link.
# The id embeds the edition date plus the 16-hex revision prefix of the edition
# manifest's integrity digest, so a republished date mints a new id and never
# reuses an older edition's identity.
DAILY_EDITION_ID_RE = re.compile(r"^daily-(\d{4}-\d{2}-\d{2})-([0-9a-f]{16})$")
EDITOR_SNAPSHOT_ID_RE = re.compile(
    r"^review-(\d{4}-\d{2}-\d{2})-([0-9a-f]{16})$"
)
DAILY_EDITOR_LINK_SOURCE = "teams_daily"
_EDITOR_SOURCE_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


def parse_daily_edition_id(edition_id: object) -> str:
    """Return the embedded edition key for a valid Daily edition id, else ""."""
    match = DAILY_EDITION_ID_RE.match(str(edition_id or ""))
    if not match:
        return ""
    key = match.group(1)
    try:
        date.fromisoformat(key)
    except ValueError:
        return ""
    return key


def parse_editor_snapshot_id(snapshot_id: object) -> str:
    """Return the embedded date for a content-addressed Review snapshot."""
    match = EDITOR_SNAPSHOT_ID_RE.match(str(snapshot_id or ""))
    if not match:
        return ""
    key = match.group(1)
    try:
        date.fromisoformat(key)
    except ValueError:
        return ""
    return key


def _validated_root(root_url: str) -> str:
    value = str(root_url or "").strip().rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    if parsed.params or parsed.query or parsed.fragment:
        return ""
    return value


def daily_editor_console_url(
    edition_id: str,
    *,
    root_url: str = PUBLIC_ROOT,
    source: str = DAILY_EDITOR_LINK_SOURCE,
) -> str:
    """Exact-edition operator editor deep link, or "" when any part is unsafe.

    Only validated local identifiers appear in the query string; the path is the
    already-published dated Review Console for the edition's own date."""
    root = _validated_root(root_url)
    key = parse_daily_edition_id(edition_id)
    if not root or not key or not _EDITOR_SOURCE_RE.match(source or ""):
        return ""
    query = urlencode(
        {"product": "daily", "edition_id": edition_id, "source": source}
    )
    return f"{root}/editorial/review/{key}/index.html?{query}"


def daily_edition_manifest_url(edition_id: str, *, root_url: str = PUBLIC_ROOT) -> str:
    """Public URL of the immutable Daily edition manifest, or "" when unsafe."""
    root = _validated_root(root_url)
    if not root or not parse_daily_edition_id(edition_id):
        return ""
    return f"{root}/editorial/daily/editions/{edition_id}.json"


def editor_snapshot_url(snapshot_id: str, *, root_url: str = PUBLIC_ROOT) -> str:
    root = _validated_root(root_url)
    if not root or not parse_editor_snapshot_id(snapshot_id):
        return ""
    return f"{root}/editorial/review/snapshots/{snapshot_id}/index.html"


def editor_snapshot_manifest_url(
    snapshot_id: str,
    *,
    root_url: str = PUBLIC_ROOT,
) -> str:
    root = _validated_root(root_url)
    if not root or not parse_editor_snapshot_id(snapshot_id):
        return ""
    return f"{root}/editorial/review/snapshots/{snapshot_id}/manifest.json"


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
