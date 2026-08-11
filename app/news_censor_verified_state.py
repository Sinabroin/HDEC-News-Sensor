"""Bounded verified-article state for the standalone News Censor.

The state contains only publisher-direct public metadata.  Discovery URLs,
redirect details, fetched markup, sender state, and credentials are deliberately
outside this contract.  Callers must opt in with an explicit path; this keeps the
independent Teams collection/current-delta path free of carry-forward rows.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Mapping

from app import publisher_direct

STATE_CONTRACT = "HDEC_NEWS_CENSOR_VERIFIED_STATE_V1"
STATE_VERSION = 1
CANONICALIZATION_VERSION = "publisher-canonical-v1"
PUBLISHER_AUTHORITY_POLICY_VERSION = "publisher-authority-v2"
SOURCE_QUALITY_POLICY_VERSION = "source-quality-v1"

VERIFY_REUSE_TTL = timedelta(hours=24)
PUBLIC_CARRY_FORWARD_MAX_AGE = timedelta(days=7)
STATE_RETENTION_MAX_AGE = timedelta(days=14)
MAX_ACTIVE_ENTRIES = 300
MAX_INVALID_ENTRIES = 100
MAX_SERIALIZED_BYTES = 2_000_000
SNIPPET_MAX_CHARS = 500

_TITLE_RE = re.compile(r"[^0-9a-z가-힣]+")
_SAFE_CATEGORIES = frozenset({"biz", "peers", "hdec", "safety", "global", "ai"})
_ENTRY_KEYS = frozenset(
    {
        "contract",
        "version",
        "article_id",
        "article_identity",
        "canonical_hash",
        "canonical_url",
        "publisher_host",
        "source",
        "title",
        "snippet",
        "published_at",
        "first_verified_at",
        "last_verified_at",
        "last_seen_at",
        "verification_expires_at",
        "category_memberships",
        "display_relevance_decision",
        "source_quality_decision",
        "canonicalization_version",
        "publisher_authority_policy_version",
        "source_quality_policy_version",
        "publisher_verification_strength",
        "image_local_path",
        "image_status",
        "image_source_kind",
        "image_source_page_url",
        "image_width",
        "image_height",
        "image_quality_accepted",
        "image_reason",
        "image_attempted",
        "image_cache_hit",
        "image_materialized",
        "image_retry_after",
        "invalidated",
        "invalidation_reason",
        "carry_forward_eligible",
        "last_transient_failure_at",
        "last_transient_failure_reason",
        "retry_after_at",
    }
)

_IMAGE_STATUSES = {"", "local_materialized", "deterministic_fallback"}
_PUBLISHER_VERIFICATION_STRENGTHS = {
    "full_body",
    "structured_metadata",
    "metadata_only_exact_host",
    "official_registry_feed",
    "legacy_unclassified",
}
_IMAGE_FALLBACK_REASONS = {
    "no_image_candidate",
    "publisher_blocked",
    "timeout",
    "invalid_mime",
    "invalid_magic",
    "dimensions_too_small",
    "logo_or_banner_rejected",
    "duplicate_image_rejected",
    "unsafe_url_rejected",
    "download_failed",
    "materialization_failed",
    "total_deadline_exhausted_after_attempt",
}


class VerifiedStateError(ValueError):
    """The state cannot be safely consumed or replaced."""


@dataclass(frozen=True)
class StateLoad:
    state: dict
    entries_loaded: int
    entries_valid: int
    entries_pruned: int
    sha256: str


def _now(value: datetime | str | None = None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse_iso(value: object, field: str) -> datetime:
    try:
        return _now(str(value or ""))
    except (TypeError, ValueError):
        raise VerifiedStateError(f"invalid {field}") from None


def _normalized_title(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return _TITLE_RE.sub("", text)


def article_identity(article: Mapping) -> str:
    """Stable identity derived only from public article metadata."""
    published_raw = str(article.get("published_at") or "").strip()
    try:
        published = _iso(_now(published_raw))
    except (TypeError, ValueError):
        published = published_raw
    source = " ".join(str(article.get("source") or "").casefold().split())
    material = "\n".join((_normalized_title(article.get("title")), source, published))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _canonical_hash(url: str) -> str:
    return hashlib.sha256(url.casefold().rstrip("/").encode("utf-8")).hexdigest()


def empty_state(*, now: datetime | str | None = None) -> dict:
    return {
        "contract": STATE_CONTRACT,
        "version": STATE_VERSION,
        "generated_at": _iso(_now(now)),
        "entries": [],
    }


def _validate_local_image_path(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    candidate = Path(text)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise VerifiedStateError("unsafe image_local_path")
    if not text.startswith("assets/images/"):
        raise VerifiedStateError("unsupported image_local_path")
    return text


def validate_entry(raw: object) -> dict:
    if not isinstance(raw, dict) or not set(raw).issubset(_ENTRY_KEYS):
        raise VerifiedStateError("invalid verified entry shape")
    if raw.get("contract") != STATE_CONTRACT or raw.get("version") != STATE_VERSION:
        raise VerifiedStateError("unsupported verified entry version")

    canonical = publisher_direct.normalize_publisher_canonical_url(raw.get("canonical_url"))
    if not canonical or publisher_direct.portal_provider(canonical):
        raise VerifiedStateError("verified entry canonical is not publisher-direct")
    host = canonical.split("/", 3)[2].casefold()
    required_text = {
        "article_id": raw.get("article_id"),
        "article_identity": raw.get("article_identity"),
        "canonical_hash": raw.get("canonical_hash"),
        "source": raw.get("source"),
        "title": raw.get("title"),
        "published_at": raw.get("published_at"),
        "first_verified_at": raw.get("first_verified_at"),
        "last_verified_at": raw.get("last_verified_at"),
        "last_seen_at": raw.get("last_seen_at"),
        "verification_expires_at": raw.get("verification_expires_at"),
    }
    if any(not str(value or "").strip() for value in required_text.values()):
        raise VerifiedStateError("verified entry mandatory metadata missing")
    if str(raw.get("canonical_hash")) != _canonical_hash(canonical):
        raise VerifiedStateError("verified entry canonical hash mismatch")
    if str(raw.get("publisher_host") or "").casefold() != host:
        raise VerifiedStateError("verified entry publisher host mismatch")
    policy_versions = {
        "canonicalization_version": raw.get("canonicalization_version"),
        "publisher_authority_policy_version": raw.get(
            "publisher_authority_policy_version"
        ),
        "source_quality_policy_version": raw.get("source_quality_policy_version"),
    }
    if any(
        not str(value or "").strip() or len(str(value)) > 80
        for value in policy_versions.values()
    ):
        raise VerifiedStateError("verified entry policy version invalid")
    verification_strength = str(
        raw.get("publisher_verification_strength") or "legacy_unclassified"
    )
    if verification_strength not in _PUBLISHER_VERIFICATION_STRENGTHS:
        raise VerifiedStateError("unsupported publisher verification strength")

    published = _parse_iso(raw.get("published_at"), "published_at")
    first_verified = _parse_iso(raw.get("first_verified_at"), "first_verified_at")
    last_verified = _parse_iso(raw.get("last_verified_at"), "last_verified_at")
    last_seen = _parse_iso(raw.get("last_seen_at"), "last_seen_at")
    expires = _parse_iso(raw.get("verification_expires_at"), "verification_expires_at")
    if first_verified > last_verified or last_verified > expires:
        raise VerifiedStateError("verified entry timestamp ordering invalid")
    if published > last_seen + timedelta(days=2):
        raise VerifiedStateError("verified entry publication timestamp implausible")

    categories = raw.get("category_memberships")
    if not isinstance(categories, list):
        raise VerifiedStateError("verified entry categories invalid")
    normalized_categories = sorted({str(value) for value in categories if str(value) in _SAFE_CATEGORIES})
    if normalized_categories != sorted(categories):
        raise VerifiedStateError("verified entry categories unsupported or unordered")
    bool_fields = (
        "display_relevance_decision",
        "source_quality_decision",
        "invalidated",
        "carry_forward_eligible",
    )
    if any(not isinstance(raw.get(field), bool) for field in bool_fields):
        raise VerifiedStateError("verified entry boolean field invalid")
    reason = str(raw.get("invalidation_reason") or "")
    if bool(raw.get("invalidated")) != bool(reason):
        raise VerifiedStateError("verified entry invalidation reason mismatch")
    for optional_timestamp in ("last_transient_failure_at", "retry_after_at"):
        if str(raw.get(optional_timestamp) or ""):
            _parse_iso(raw.get(optional_timestamp), optional_timestamp)

    image_status = str(raw.get("image_status") or "")
    if image_status not in _IMAGE_STATUSES:
        raise VerifiedStateError("unsupported image_status")
    image_path = _validate_local_image_path(raw.get("image_local_path"))
    image_reason = str(raw.get("image_reason") or "")[:120]
    image_source_page_url = str(raw.get("image_source_page_url") or "").strip()
    if image_source_page_url:
        image_source_page_url = (
            publisher_direct.normalize_publisher_canonical_url(
                image_source_page_url
            )
            or ""
        )
        if not image_source_page_url:
            raise VerifiedStateError("unsafe image_source_page_url")
    image_width = raw.get("image_width")
    image_height = raw.get("image_height")
    if image_width is not None and (
        not isinstance(image_width, int) or image_width <= 0
    ):
        raise VerifiedStateError("invalid image_width")
    if image_height is not None and (
        not isinstance(image_height, int) or image_height <= 0
    ):
        raise VerifiedStateError("invalid image_height")
    image_quality_accepted = bool(raw.get("image_quality_accepted", False))
    image_attempted = bool(raw.get("image_attempted", False))
    image_cache_hit = bool(raw.get("image_cache_hit", False))
    image_materialized = bool(raw.get("image_materialized", False))
    image_retry_after = str(raw.get("image_retry_after") or "")[:40]
    if image_retry_after:
        _parse_iso(image_retry_after, "image_retry_after")
    if image_status == "local_materialized" and (
        not image_path
        or not image_quality_accepted
        or not image_materialized
        or not image_reason
    ):
        raise VerifiedStateError("incomplete local image state")
    if image_status == "deterministic_fallback" and (
        image_path
        or image_quality_accepted
        or image_materialized
        or image_reason not in _IMAGE_FALLBACK_REASONS
    ):
        raise VerifiedStateError("incomplete fallback image state")

    return {
        "contract": STATE_CONTRACT,
        "version": STATE_VERSION,
        "article_id": str(raw["article_id"]),
        "article_identity": str(raw["article_identity"]),
        "canonical_hash": str(raw["canonical_hash"]),
        "canonical_url": canonical,
        "publisher_host": host,
        "source": str(raw["source"]).strip()[:160],
        "title": str(raw["title"]).strip()[:500],
        "snippet": str(raw.get("snippet") or "")[:SNIPPET_MAX_CHARS],
        "published_at": _iso(published),
        "first_verified_at": _iso(first_verified),
        "last_verified_at": _iso(last_verified),
        "last_seen_at": _iso(last_seen),
        "verification_expires_at": _iso(expires),
        "category_memberships": normalized_categories,
        "display_relevance_decision": bool(raw["display_relevance_decision"]),
        "source_quality_decision": bool(raw["source_quality_decision"]),
        "canonicalization_version": str(raw["canonicalization_version"]),
        "publisher_authority_policy_version": str(
            raw["publisher_authority_policy_version"]
        ),
        "source_quality_policy_version": str(raw["source_quality_policy_version"]),
        "publisher_verification_strength": verification_strength,
        "image_local_path": image_path,
        "image_status": image_status,
        "image_source_kind": str(raw.get("image_source_kind") or "")[:80],
        "image_source_page_url": image_source_page_url,
        "image_width": image_width,
        "image_height": image_height,
        "image_quality_accepted": image_quality_accepted,
        "image_reason": image_reason,
        "image_attempted": image_attempted,
        "image_cache_hit": image_cache_hit,
        "image_materialized": image_materialized,
        "image_retry_after": image_retry_after,
        "invalidated": bool(raw["invalidated"]),
        "invalidation_reason": reason[:120],
        "carry_forward_eligible": bool(raw["carry_forward_eligible"]),
        "last_transient_failure_at": str(raw.get("last_transient_failure_at") or "")[:40],
        "last_transient_failure_reason": str(raw.get("last_transient_failure_reason") or "")[:120],
        "retry_after_at": str(raw.get("retry_after_at") or "")[:40],
    }


def validate_state(raw: object) -> dict:
    if not isinstance(raw, dict):
        raise VerifiedStateError("verified state must be an object")
    if raw.get("contract") != STATE_CONTRACT or raw.get("version") != STATE_VERSION:
        raise VerifiedStateError("unsupported verified state version")
    if set(raw) != {"contract", "version", "generated_at", "entries"}:
        raise VerifiedStateError("invalid verified state shape")
    generated_at = _parse_iso(raw.get("generated_at"), "generated_at")
    entries = raw.get("entries")
    if not isinstance(entries, list) or len(entries) > MAX_ACTIVE_ENTRIES + MAX_INVALID_ENTRIES:
        raise VerifiedStateError("verified state entry count exceeds bound")
    raw_validated = [validate_entry(entry) for entry in entries]
    if raw_validated != sorted(
        raw_validated,
        key=lambda entry: entry["canonical_url"].casefold(),
    ):
        raise VerifiedStateError("verified state entries are not deterministically ordered")
    by_canonical: dict[str, dict] = {}
    for entry in raw_validated:
        previous = by_canonical.get(entry["canonical_hash"])
        if previous is None:
            by_canonical[entry["canonical_hash"]] = entry
            continue
        newest = max(
            (previous, entry),
            key=lambda item: (
                _parse_iso(item["last_seen_at"], "last_seen_at"),
                item["canonical_url"],
            ),
        )
        merged = dict(newest)
        merged["first_verified_at"] = _iso(min(
            _parse_iso(previous["first_verified_at"], "first_verified_at"),
            _parse_iso(entry["first_verified_at"], "first_verified_at"),
        ))
        merged["category_memberships"] = sorted(set(
            previous["category_memberships"] + entry["category_memberships"]
        ))
        by_canonical[entry["canonical_hash"]] = validate_entry(merged)
    validated = sorted(
        by_canonical.values(),
        key=lambda entry: entry["canonical_url"].casefold(),
    )
    return {
        "contract": STATE_CONTRACT,
        "version": STATE_VERSION,
        "generated_at": _iso(generated_at),
        "entries": validated,
    }


def _serialized(state: dict) -> bytes:
    value = (json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if len(value) > MAX_SERIALIZED_BYTES:
        raise VerifiedStateError("verified state exceeds serialized size bound")
    return value


def load_state(path: Path, *, now: datetime | str | None = None) -> StateLoad:
    if not path.exists():
        return StateLoad(empty_state(now=now), 0, 0, 0, "")
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise VerifiedStateError("verified state read failed") from exc
    if len(payload) > MAX_SERIALIZED_BYTES:
        raise VerifiedStateError("verified state exceeds serialized size bound")
    digest = hashlib.sha256(payload).hexdigest()
    try:
        raw = json.loads(payload.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise VerifiedStateError("verified state parse failed") from exc
    loaded = len(raw.get("entries") or []) if isinstance(raw, dict) else 0
    state = validate_state(raw)
    pruned, removed = prune_state(state, now=now)
    total_removed = max(removed, max(0, loaded - len(pruned["entries"])))
    return StateLoad(pruned, loaded, len(pruned["entries"]), total_removed, digest)


def prune_state(state: dict, *, now: datetime | str | None = None) -> tuple[dict, int]:
    reference = _now(now)
    cutoff = reference - STATE_RETENTION_MAX_AGE
    retained = [
        dict(entry)
        for entry in state.get("entries") or []
        if _parse_iso(entry.get("published_at"), "published_at") >= cutoff
        and _parse_iso(entry.get("last_seen_at"), "last_seen_at") >= cutoff
    ]
    active = sorted(
        (entry for entry in retained if not entry.get("invalidated")),
        key=lambda entry: (
            -_parse_iso(entry["last_seen_at"], "last_seen_at").timestamp(),
            -_parse_iso(entry["published_at"], "published_at").timestamp(),
            entry["canonical_url"].casefold(),
        ),
    )[:MAX_ACTIVE_ENTRIES]
    invalid = sorted(
        (entry for entry in retained if entry.get("invalidated")),
        key=lambda entry: (
            -_parse_iso(entry["last_seen_at"], "last_seen_at").timestamp(),
            entry["canonical_url"].casefold(),
        ),
    )[:MAX_INVALID_ENTRIES]
    entries = sorted(active + invalid, key=lambda entry: entry["canonical_url"].casefold())
    output = {
        "contract": STATE_CONTRACT,
        "version": STATE_VERSION,
        "generated_at": _iso(reference),
        "entries": entries,
    }
    return output, max(0, len(state.get("entries") or []) - len(entries))


def atomic_write_state(
    path: Path,
    state: dict,
    *,
    replace: Callable[[str, str], None] = os.replace,
) -> str:
    """Validate and atomically replace state; the previous file survives failure."""
    validated = validate_state(state)
    payload = _serialized(validated)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        validate_state(json.loads(temporary.read_text(encoding="utf-8")))
        replace(str(temporary), str(path))
        temporary = None
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
    return hashlib.sha256(payload).hexdigest()


def reusable_entry(
    entries_by_identity: Mapping[str, dict],
    entries_by_canonical: Mapping[str, dict],
    article: Mapping,
    *,
    now: datetime | str | None = None,
) -> tuple[dict | None, str]:
    reference = _now(now)
    candidate_url = publisher_direct.normalize_publisher_canonical_url(
        publisher_direct.publisher_url(article)
    )
    entry = entries_by_canonical.get(_canonical_hash(candidate_url)) if candidate_url else None
    if entry is None:
        entry = entries_by_identity.get(article_identity(article))
    if entry is None:
        return None, "cache_miss"
    if entry.get("invalidated") or not entry.get("carry_forward_eligible"):
        retry_after = str(entry.get("retry_after_at") or "")
        if retry_after and _parse_iso(retry_after, "retry_after_at") > reference:
            return None, "cache_retry_backoff"
        return None, "cache_invalidated"
    if candidate_url and candidate_url.split("/", 3)[2].casefold() != entry["publisher_host"]:
        return None, "cache_host_mapping_changed"
    if entry.get("canonicalization_version") != CANONICALIZATION_VERSION:
        return None, "cache_canonicalization_version_changed"
    if entry.get("publisher_authority_policy_version") != PUBLISHER_AUTHORITY_POLICY_VERSION:
        return None, "cache_authority_policy_changed"
    if entry.get("source_quality_policy_version") != SOURCE_QUALITY_POLICY_VERSION:
        return None, "cache_source_quality_policy_changed"
    expires = _parse_iso(entry["verification_expires_at"], "verification_expires_at")
    if expires <= reference:
        retry_after = str(entry.get("retry_after_at") or "")
        if retry_after and _parse_iso(retry_after, "retry_after_at") > reference:
            return None, "cache_retry_backoff"
        return None, "cache_expired"
    published = _parse_iso(entry["published_at"], "published_at")
    if published < reference - PUBLIC_CARRY_FORWARD_MAX_AGE or published > reference + timedelta(hours=6):
        return None, "cache_public_window_expired"
    if not publisher_direct.normalize_publisher_canonical_url(entry["canonical_url"]):
        return None, "cache_canonical_invalid"
    return dict(entry), "verified_cache_hit"


def state_indexes(state: Mapping) -> tuple[dict[str, dict], dict[str, dict]]:
    identities: dict[str, dict] = {}
    canonicals: dict[str, dict] = {}
    for entry in state.get("entries") or []:
        identities[str(entry["article_identity"])] = entry
        canonicals[str(entry["canonical_hash"])] = entry
    return identities, canonicals


def article_from_entry(
    entry: Mapping,
    *,
    current_run_seen: bool,
    cache_reused: bool = False,
) -> dict:
    canonical = str(entry["canonical_url"])
    metadata = {
        "provider": "verified_state_cache" if current_run_seen else "verified_state_carry_forward",
        "source_url": canonical,
        "publisher_url": canonical,
        "publisher_domain": str(entry["publisher_host"]),
        "publisher_direct": True,
        "portal_resolution_status": "resolved",
        "portal_resolution_reason": "verified_cache_hit" if cache_reused else "verified_state_carry_forward",
        "current_run_seen": bool(current_run_seen),
        "carried_forward": not bool(current_run_seen),
        "carry_forward_reason": "" if current_run_seen else "unexpired_verified_state",
        "teams_newness_eligible": bool(current_run_seen),
        "discovery_run_status": "current_verified_reused" if current_run_seen else "carry_forward_only",
        "verification_cache_status": "verified_cache_hit" if cache_reused else "carried_forward",
        "first_verified_at": str(entry["first_verified_at"]),
        "last_verified_at": str(entry["last_verified_at"]),
        "category_memberships": list(entry.get("category_memberships") or []),
        "publisher_verification_strength": str(
            entry.get("publisher_verification_strength") or "legacy_unclassified"
        ),
    }
    return {
        "id": str(entry["article_id"]),
        "title": str(entry["title"]),
        "source": str(entry["source"]),
        "published_at": str(entry["published_at"]),
        "url": canonical,
        "canonical_url": canonical,
        "publisher_url": canonical,
        "publisher_domain": str(entry["publisher_host"]),
        "publisher_direct": True,
        "publisher_verification_strength": metadata[
            "publisher_verification_strength"
        ],
        "snippet": str(entry.get("snippet") or "")[:SNIPPET_MAX_CHARS],
        "portal_resolution_status": "resolved",
        "portal_resolution_reason": metadata["portal_resolution_reason"],
        "quarantine": False,
        "status": "collected",
        "source_metadata": metadata,
    }


def verified_entry_from_article(
    article: Mapping,
    *,
    now: datetime | str | None = None,
    previous: Mapping | None = None,
    categories: list[str] | tuple[str, ...] = (),
    display_relevant: bool = True,
    source_quality_passed: bool = True,
    network_verified: bool = True,
) -> dict:
    reference = _now(now)
    canonical = publisher_direct.normalize_publisher_canonical_url(
        publisher_direct.publisher_url(article)
    )
    if not canonical:
        raise VerifiedStateError("cannot persist a non-publisher article")
    published = _parse_iso(article.get("published_at"), "published_at")
    previous = previous or {}
    first_verified = str(previous.get("first_verified_at") or _iso(reference))
    last_verified = _iso(reference) if network_verified else str(
        previous.get("last_verified_at") or _iso(reference)
    )
    expires = _iso(reference + VERIFY_REUSE_TTL) if network_verified else str(
        previous.get("verification_expires_at") or _iso(reference + VERIFY_REUSE_TTL)
    )
    article_id = str(previous.get("article_id") or _canonical_hash(canonical)[:16])
    entry = {
        "contract": STATE_CONTRACT,
        "version": STATE_VERSION,
        "article_id": article_id,
        "article_identity": article_identity(article),
        "canonical_hash": _canonical_hash(canonical),
        "canonical_url": canonical,
        "publisher_host": canonical.split("/", 3)[2].casefold(),
        "source": str(article.get("source") or "").strip(),
        "title": str(article.get("title") or "").strip(),
        "snippet": str(article.get("snippet") or "")[:SNIPPET_MAX_CHARS],
        "published_at": _iso(published),
        "first_verified_at": first_verified,
        "last_verified_at": last_verified,
        "last_seen_at": _iso(reference),
        "verification_expires_at": expires,
        "category_memberships": sorted({value for value in categories if value in _SAFE_CATEGORIES}),
        "display_relevance_decision": bool(display_relevant),
        "source_quality_decision": bool(source_quality_passed),
        "canonicalization_version": CANONICALIZATION_VERSION,
        "publisher_authority_policy_version": PUBLISHER_AUTHORITY_POLICY_VERSION,
        "source_quality_policy_version": SOURCE_QUALITY_POLICY_VERSION,
        "publisher_verification_strength": str(
            article.get("publisher_verification_strength")
            or (article.get("source_metadata") or {}).get(
                "publisher_verification_strength"
            )
            or "full_body"
        ),
        "image_local_path": str(previous.get("image_local_path") or ""),
        "image_status": str(previous.get("image_status") or ""),
        "image_source_kind": str(previous.get("image_source_kind") or ""),
        "image_source_page_url": str(
            previous.get("image_source_page_url") or ""
        ),
        "image_width": previous.get("image_width"),
        "image_height": previous.get("image_height"),
        "image_quality_accepted": bool(
            previous.get("image_quality_accepted", False)
        ),
        "image_reason": str(previous.get("image_reason") or ""),
        "image_attempted": bool(previous.get("image_attempted", False)),
        "image_cache_hit": bool(previous.get("image_cache_hit", False)),
        "image_materialized": bool(previous.get("image_materialized", False)),
        "image_retry_after": str(previous.get("image_retry_after") or ""),
        "invalidated": False,
        "invalidation_reason": "",
        "carry_forward_eligible": bool(display_relevant and source_quality_passed),
        "last_transient_failure_at": "",
        "last_transient_failure_reason": "",
        "retry_after_at": "",
    }
    return validate_entry(entry)


def merge_verified_entries(
    state: dict,
    verified_articles: list[Mapping],
    *,
    now: datetime | str | None = None,
    category_resolver: Callable[[Mapping], list[str] | set[str] | tuple[str, ...]],
    relevance_resolver: Callable[[Mapping], bool],
    source_quality_resolver: Callable[[Mapping], bool],
) -> tuple[dict, int, int]:
    reference = _now(now)
    _identities, previous_by_canonical = state_indexes(state)
    merged = {entry["canonical_hash"]: dict(entry) for entry in state.get("entries") or []}
    new_count = reused_count = 0
    for article in verified_articles:
        canonical = publisher_direct.normalize_publisher_canonical_url(
            publisher_direct.publisher_url(article)
        )
        if not canonical:
            continue
        key = _canonical_hash(canonical)
        previous = previous_by_canonical.get(key)
        metadata = article.get("source_metadata") or {}
        cache_reused = isinstance(metadata, dict) and metadata.get("verification_cache_status") == "verified_cache_hit"
        entry = verified_entry_from_article(
            article,
            now=reference,
            previous=previous,
            categories=sorted(category_resolver(article)),
            display_relevant=relevance_resolver(article),
            source_quality_passed=source_quality_resolver(article),
            network_verified=not cache_reused,
        )
        merged[key] = entry
        reused_count += int(bool(cache_reused))
        new_count += int(not cache_reused and previous is None)
    output = {
        "contract": STATE_CONTRACT,
        "version": STATE_VERSION,
        "generated_at": _iso(reference),
        "entries": sorted(merged.values(), key=lambda entry: entry["canonical_url"].casefold()),
    }
    output, _removed = prune_state(output, now=reference)
    return output, new_count, reused_count


def record_resolution_failures(
    state: dict,
    failed_articles: list[Mapping],
    *,
    now: datetime | str | None = None,
) -> tuple[dict, int, int]:
    """Record bounded invalidation/transient evidence without deleting prior proof."""
    reference = _now(now)
    by_identity = {
        str(entry["article_identity"]): dict(entry)
        for entry in state.get("entries") or []
    }
    invalidated = transient = 0
    for article in failed_articles:
        entry = by_identity.get(article_identity(article))
        if entry is None:
            continue
        entry["last_seen_at"] = _iso(reference)
        reason = str(article.get("portal_resolution_reason") or "")
        if any(
            marker in reason
            for marker in (
                "ARTICLE_NOT_FOUND",
                "ARTICLE_GONE",
                "ARTICLE_BODY_NOT_FOUND",
                "ARTICLE_METADATA_NOT_FOUND",
                "UNSAFE_DESTINATION",
                "REDIRECT_REJECTED",
                "resolved_url_is_not_publisher_direct",
                "publisher_canonical_not_direct",
            )
        ):
            entry["invalidated"] = True
            entry["invalidation_reason"] = (
                "publisher_revalidation_rejected"
                if "UNSAFE" not in reason and "REDIRECT" not in reason
                else "publisher_revalidation_unsafe_target"
            )
            entry["carry_forward_eligible"] = False
            entry["retry_after_at"] = _iso(
                reference
                + (
                    timedelta(days=7)
                    if any(marker in reason for marker in ("UNSAFE", "REDIRECT", "canonical_not_direct"))
                    else timedelta(hours=24)
                )
            )
            invalidated += 1
        elif any(marker in reason for marker in ("FETCH_TIMEOUT", "DNS_RESOLUTION_FAILED")):
            entry["last_transient_failure_at"] = _iso(reference)
            entry["last_transient_failure_reason"] = (
                "publisher_revalidation_timeout"
                if "FETCH_TIMEOUT" in reason
                else "publisher_revalidation_network_error"
            )
            entry["retry_after_at"] = _iso(reference + timedelta(hours=1))
            transient += 1
        by_identity[entry["article_identity"]] = entry
    output = {
        "contract": STATE_CONTRACT,
        "version": STATE_VERSION,
        "generated_at": _iso(reference),
        "entries": sorted(by_identity.values(), key=lambda entry: entry["canonical_url"].casefold()),
    }
    return validate_state(output), invalidated, transient


def carry_forward_articles(
    state: Mapping,
    current_articles: list[Mapping],
    *,
    now: datetime | str | None = None,
) -> tuple[list[dict], dict[str, int]]:
    reference = _now(now)
    current = {
        _canonical_hash(url)
        for row in current_articles
        if (url := publisher_direct.normalize_publisher_canonical_url(
            publisher_direct.publisher_url(row)
        ))
    }
    carried: list[dict] = []
    diagnostics = {"candidates": 0, "expired": 0, "invalidated": 0}
    for entry in state.get("entries") or []:
        if entry["canonical_hash"] in current:
            continue
        diagnostics["candidates"] += 1
        if entry.get("invalidated") or not entry.get("carry_forward_eligible"):
            diagnostics["invalidated"] += 1
            continue
        published = _parse_iso(entry["published_at"], "published_at")
        expires = _parse_iso(entry["verification_expires_at"], "verification_expires_at")
        if published < reference - PUBLIC_CARRY_FORWARD_MAX_AGE or expires <= reference:
            diagnostics["expired"] += 1
            continue
        carried.append(article_from_entry(entry, current_run_seen=False))
    carried.sort(key=lambda row: (str(row.get("published_at") or ""), str(row.get("url") or "")), reverse=True)
    return carried, diagnostics
