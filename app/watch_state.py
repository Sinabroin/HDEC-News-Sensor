"""Watch-mode state helpers for urgent signal review queues.

The watch mode deliberately uses a small JSON file instead of a DB migration.
It stores stable article keys and compact cluster metadata only: no secrets,
Telegram tokens, chat ids, or full article bodies.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import unicodedata
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app import config

KST = timezone(timedelta(hours=9))
STATE_VERSION = 1
DEFAULT_STATE_PATH = config.DATA_DIR / "watch_state.json"

TRACKING_QUERY_PREFIXES = ("utm_",)
TRACKING_QUERY_KEYS = {
    "fbclid", "gclid", "igshid", "mc_cid", "mc_eid", "ref", "spm",
}


def now_iso() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


def resolve_state_path(path: str | Path | None = None) -> Path:
    raw = path or os.environ.get("WATCH_STATE_PATH") or DEFAULT_STATE_PATH
    return Path(raw)


def empty_state() -> dict:
    return {
        "version": STATE_VERSION,
        "seen_article_ids": {},
        "normalized_urls": {},
        "normalized_title_fingerprints": {},
        "cluster_keys": {},
        "last_urgent_queue_at": None,
        "urgent_queue": [],
        "review_digest": "",
    }


def _coerce_state(data) -> dict:
    state = empty_state()
    if isinstance(data, dict):
        state.update(data)
    for key in (
            "seen_article_ids", "normalized_urls",
            "normalized_title_fingerprints", "cluster_keys"):
        if not isinstance(state.get(key), dict):
            state[key] = {}
    if not isinstance(state.get("urgent_queue"), list):
        state["urgent_queue"] = []
    if not isinstance(state.get("review_digest"), str):
        state["review_digest"] = ""
    state["version"] = STATE_VERSION
    return state


def load_state(path: str | Path | None = None) -> dict:
    state_path = resolve_state_path(path)
    try:
        return _coerce_state(json.loads(state_path.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return empty_state()


def save_state(state: dict, path: str | Path | None = None) -> Path:
    state_path = resolve_state_path(path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(_coerce_state(state), ensure_ascii=False, indent=2, sort_keys=True)
    with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=state_path.parent, delete=False) as tmp:
        tmp.write(payload)
        tmp.write("\n")
        tmp_path = Path(tmp.name)
    tmp_path.replace(state_path)
    return state_path


def normalize_url(url: str) -> str:
    """Canonical URL key for seen matching; removes common tracking noise."""
    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        parsed = urllib.parse.urlsplit(raw)
    except ValueError:
        return raw.lower().rstrip("/")

    scheme = (parsed.scheme or "https").lower()
    netloc = parsed.netloc.lower()
    path = re.sub(r"/{2,}", "/", urllib.parse.unquote(parsed.path or "/")).rstrip("/")
    if not path:
        path = "/"
    pairs = []
    for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True):
        k = key.strip()
        if not k:
            continue
        if k.lower() in TRACKING_QUERY_KEYS:
            continue
        if any(k.lower().startswith(prefix) for prefix in TRACKING_QUERY_PREFIXES):
            continue
        pairs.append((k, value))
    query = urllib.parse.urlencode(sorted(pairs), doseq=True)
    return urllib.parse.urlunsplit((scheme, netloc, path, query, ""))


def title_fingerprint(title: str) -> str:
    text = unicodedata.normalize("NFKC", title or "")
    text = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", text)
    text = text.lower()
    return re.sub(r"[^0-9a-z가-힣]+", "", text)


def article_identity(article: dict) -> dict:
    return {
        "article_id": str(article.get("id") or "").strip(),
        "normalized_url": normalize_url(str(article.get("url") or "")),
        "title_fingerprint": title_fingerprint(str(article.get("title") or "")),
    }


def first_seen_match(state: dict, article: dict) -> str | None:
    keys = article_identity(article)
    if keys["article_id"] and keys["article_id"] in state["seen_article_ids"]:
        return "article_id"
    if keys["normalized_url"] and keys["normalized_url"] in state["normalized_urls"]:
        return "normalized_url"
    if (keys["title_fingerprint"]
            and keys["title_fingerprint"] in state["normalized_title_fingerprints"]):
        return "title_fingerprint"
    return None


def cluster_entry(state: dict, cluster_key: str | None) -> dict | None:
    if not cluster_key:
        return None
    entry = state.get("cluster_keys", {}).get(cluster_key)
    return entry if isinstance(entry, dict) else None


def _touch_map(mapping: dict, key: str, detected_at: str, extra: dict | None = None) -> None:
    if not key:
        return
    entry = mapping.get(key)
    if not isinstance(entry, dict):
        entry = {"first_seen_at": detected_at}
    entry["last_seen_at"] = detected_at
    if extra:
        entry.update(extra)
    mapping[key] = entry


def mark_seen(
        state: dict,
        article: dict,
        *,
        cluster_key: str | None = None,
        detected_at: str | None = None,
        urgency_class: str | None = None,
        risk_class: str | None = None,
) -> None:
    """Record article identity and compact cluster metadata in state."""
    ts = detected_at or now_iso()
    keys = article_identity(article)
    extra = {"cluster_key": cluster_key or ""}
    if urgency_class:
        extra["last_urgency_class"] = urgency_class
    if risk_class:
        extra["last_risk_class"] = risk_class

    _touch_map(state["seen_article_ids"], keys["article_id"], ts, extra)
    _touch_map(state["normalized_urls"], keys["normalized_url"], ts, extra)
    _touch_map(state["normalized_title_fingerprints"], keys["title_fingerprint"], ts, extra)

    if cluster_key:
        clusters = state["cluster_keys"]
        entry = clusters.get(cluster_key)
        if not isinstance(entry, dict):
            entry = {
                "first_seen_at": ts,
                "last_seen_at": ts,
                "article_ids": [],
                "sources": [],
                "risk_classes": [],
            }
        entry["last_seen_at"] = ts
        aid = keys["article_id"]
        source = str(article.get("source") or "").strip()
        for field, value in (("article_ids", aid), ("sources", source),
                             ("risk_classes", risk_class or "")):
            if value:
                values = entry.get(field)
                if not isinstance(values, list):
                    values = []
                if value not in values:
                    values.append(value)
                entry[field] = values[:20]
        if urgency_class in ("send_candidate", "review_today"):
            entry["last_urgent_queue_at"] = ts
        clusters[cluster_key] = entry


def write_queue(state: dict, queue: list[dict], review_digest: str,
                detected_at: str | None = None) -> None:
    ts = detected_at or now_iso()
    state["urgent_queue"] = queue
    state["review_digest"] = review_digest
    state["last_urgent_queue_at"] = ts
