"""Daily/Weekly editorial briefing domain logic.

The module is deterministic and side-effect free except for the explicit preview
bundle writer. It does not collect news, send mail, mutate production state, or
write under ``docs``. Production orchestration belongs to
``scripts/run_editorial_briefing.py``.
"""

from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import math
import os
import re
import socket
import tempfile
import unicodedata
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
from html import escape, unescape
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from typing import Callable, Iterable, Mapping
from urllib.parse import parse_qsl, quote, unquote, urlencode, urljoin, urlparse, urlunparse

try:
    from PIL import Image, UnidentifiedImageError
except ImportError:  # Text/editorial policy remains usable without image extras.
    Image = None
    UnidentifiedImageError = OSError

from app import config, news_access, news_coverage, source_quality

KST = timezone(timedelta(hours=9))
DAILY_REPORT_SUFFIX = "/daily/latest.html"
DAILY_MAX_ARTICLES = 6
WEEKLY_MAX_ARTICLES = 12
IMAGE_URL_MAX_LENGTH = 2048
IMAGE_PAGE_MAX_BYTES = 1_000_000
IMAGE_PAGE_TIMEOUT_SECONDS = 8
IMAGE_REDIRECT_LIMIT = 3
IMAGE_DOWNLOAD_MAX_BYTES = 5_000_000
IMAGE_DOWNLOAD_TIMEOUT_SECONDS = 8
IMAGE_DOWNLOAD_MAX_ATTEMPTS_PER_ARTICLE = 5
IMAGE_DOWNLOAD_MAX_TOTAL_BYTES_PER_ARTICLE = (
    IMAGE_DOWNLOAD_MAX_BYTES * IMAGE_DOWNLOAD_MAX_ATTEMPTS_PER_ARTICLE
)
PREVIEW_IMAGE_ASSET_DIGEST_CHARS = 24
IMAGE_MIN_WIDTH = 120
IMAGE_MIN_HEIGHT = 90
PUBLISHER_URL_MAX_LENGTH = 2048
PUBLISHER_PAGE_MAX_BYTES = 1_000_000
PUBLISHER_PAGE_TIMEOUT_SECONDS = 8
PUBLISHER_REDIRECT_LIMIT = 3
SELECTION_RELEVANCE_FLOOR = 1.0
DIRECT_PRIORITY_RELEVANCE_FLOOR = 2.0
DIRECT_PRIORITY_TARGET = 4
DIRECT_SUPPLY_FOR_AGGREGATOR_CAP = 4
AGGREGATOR_CAP_WHEN_DIRECT_SUPPLY_SUFFICIENT = 2
HEADLINE_DIRECT_MARGIN = 0.75
PUBLISHER_DIVERSITY_SOFT_CAP = 2
CATEGORY_DIVERSITY_SOFT_CAP = 3
TITLE_CLUSTER_TIME_WINDOW = timedelta(hours=6)
SELECTION_MODE_LEGACY = "legacy"
SELECTION_MODE_DIRECT_AWARE_DAILY = "direct_aware_daily"
SELECTION_MODE_EDITORIAL_PRIORITY = SELECTION_MODE_DIRECT_AWARE_DAILY

_SELECTION_MODES = {
    SELECTION_MODE_LEGACY,
    SELECTION_MODE_DIRECT_AWARE_DAILY,
}

PRIMARY_PUBLISHER_PRIORITY = (
    "연합뉴스",
    "MBC",
    "KBS",
    "조선일보",
    "YTN",
    "JTBC",
    "중앙일보",
    "매일경제",
    "한국경제",
    "SBS",
)

SECONDARY_PUBLISHER_PRIORITY = (
    "동아일보",
    "한겨레",
    "경향신문",
)

PREFERRED_PUBLISHER_DAILY_TARGET = 4
PREFERRED_PUBLISHER_WEEKLY_TARGET = 8

_PUBLISHER_PRIORITY_POLICIES = (
    ("primary", 1, ("연합뉴스", "yonhap"), ("yna.co.kr",)),
    ("primary", 2, ("mbc",), ("imbc.com", "mbc.co.kr")),
    ("primary", 3, ("kbs",), ("kbs.co.kr",)),
    ("primary", 4, ("조선일보",), ("chosun.com",)),
    ("primary", 5, ("ytn",), ("ytn.co.kr",)),
    ("primary", 6, ("jtbc",), ("jtbc.co.kr",)),
    ("primary", 7, ("중앙일보",), ("joongang.co.kr",)),
    ("primary", 8, ("매일경제", "매경"), ("mk.co.kr",)),
    ("primary", 9, ("한국경제", "한경"), ("hankyung.com",)),
    ("primary", 10, ("sbs",), ("sbs.co.kr",)),
    ("secondary", 1, ("동아일보",), ("donga.com",)),
    ("secondary", 2, ("한겨레",), ("hani.co.kr",)),
    ("secondary", 3, ("경향신문",), ("khan.co.kr",)),
)

_SENTENCE_RE = re.compile(r"(?<=[.!?。！？])\s+")
_WORD_RE = re.compile(r"[0-9A-Za-z가-힣]+")
_STOPWORDS = {
    "관련", "대한", "위한", "통해", "이번", "주요", "발표", "확대", "추진", "전망",
    "시장", "기업", "기술", "산업", "경영", "뉴스", "ai", "the", "and", "for", "with",
}
_IMAGE_EXTENSIONS = {
    ".avif", ".gif", ".jpeg", ".jpg", ".png", ".webp",
}
_IMAGE_REJECT_MARKERS = (
    "advert", "adserver", "adservice", "banner", "doubleclick", "favicon",
    "icon", "logo", "profile", "author", "avatar", "sprite", "tracking",
    "tracker", "pixel", "beacon",
)
_IMAGE_LOGO_TEXT_MARKERS = (
    "logo", "logos", "brand", "symbol", "ci", "identity", "masthead",
    "header-logo", "site-logo", "publisher-logo", "emblem", "favicon",
)
_IMAGE_DEFAULT_TEXT_MARKERS = (
    "default-og", "og-default", "share-default", "default-image",
    "default_image", "og-default-image", "site-default", "site_default",
)
_PRIVATE_HOST_SUFFIXES = (".internal", ".intranet", ".local", ".localhost")
_PUBLISHER_REJECT_HOST_MARKERS = (
    "accounts.google.", "googleadservices.", "googleusercontent.",
    "translate.google.", "webcache.googleusercontent.", "consent.google.",
    "doubleclick.", "googlesyndication.", "googletagmanager.",
    "google-analytics.", "adservice.", "adserver.", "adnxs.",
    "taboola.", "outbrain.", "tracking.", "tracker.", "l.facebook.",
)
_PUBLISHER_REJECT_PATH_MARKERS = (
    "/account", "/advert", "/auth", "/cache", "/login", "/privacy",
    "/register", "/result", "/search", "/share", "/signin", "/sign-in",
    "/subscribe", "/subscription", "/terms", "/translate", "/users",
)
_TRACKING_QUERY_KEYS = {
    "fbclid", "gclid", "mc_cid", "mc_eid", "oc", "ref", "referrer",
    "utm_campaign", "utm_content", "utm_medium", "utm_source", "utm_term",
}


class EditorialError(RuntimeError):
    """Fail-closed editorial contract violation."""


class ImageDownloadError(EditorialError):
    """Safe image materialization failure with a manifest-safe reason."""

    def __init__(
        self,
        reason: str,
        *,
        status: int | None = None,
        content_type: str = "",
        byte_size: int = 0,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status = status
        self.content_type = content_type
        self.byte_size = byte_size


@dataclass(frozen=True)
class ImageCandidateOption:
    url: str
    source_kind: str
    source_page_url: str = ""
    width: int | None = None
    height: int | None = None
    reason: str = ""
    context: str = ""


@dataclass(frozen=True)
class ImageCandidateAttempt:
    source_kind: str
    host: str
    status: str
    reason: str
    content_type: str = ""
    byte_size: int = 0
    local_asset: str = ""
    duplicate_asset_reused: bool = False
    selected: bool = False
    byte_validation_status: str = ""
    quality_accepted: bool = False
    quality_rejection_reason: str = ""
    logo_signals: tuple[str, ...] = ()


@dataclass(frozen=True)
class ImageQualityAssessment:
    accepted: bool
    reason: str = "image_quality_passed"
    logo_signals: tuple[str, ...] = ()


@dataclass(frozen=True)
class ImageResolution:
    url: str = ""
    source_kind: str = "fallback"
    source_page_url: str = ""
    width: int | None = None
    height: int | None = None
    fallback_used: bool = True
    reason: str = "no_safe_image_candidate"
    candidates: tuple[ImageCandidateOption, ...] = ()
    context: str = ""


@dataclass
class ImageResolutionCounters:
    network_page_gets: int = 0
    network_image_head_or_range_gets: int = 0
    images_from_feed: int = 0
    images_from_og: int = 0
    images_from_twitter: int = 0
    images_from_jsonld: int = 0
    images_from_image_src: int = 0
    images_from_body: int = 0
    images_from_fallback: int = 0

    def account(self, resolution: ImageResolution) -> None:
        if resolution.source_kind in {
            "rss_image", "media_content", "media_thumbnail", "enclosure",
        }:
            self.images_from_feed += 1
        elif resolution.source_kind == "og_image":
            self.images_from_og += 1
        elif resolution.source_kind == "twitter_image":
            self.images_from_twitter += 1
        elif resolution.source_kind == "jsonld_image":
            self.images_from_jsonld += 1
        elif resolution.source_kind == "image_src":
            self.images_from_image_src += 1
        elif resolution.source_kind == "body_image":
            self.images_from_body += 1
        else:
            self.images_from_fallback += 1

    def manifest_fields(self) -> dict[str, int]:
        return {
            "network_page_gets": self.network_page_gets,
            "network_image_head_or_range_gets": self.network_image_head_or_range_gets,
            "image_probe_requests": self.network_image_head_or_range_gets,
            "images_from_feed": self.images_from_feed,
            "images_from_og": self.images_from_og,
            "images_from_twitter": self.images_from_twitter,
            "images_from_jsonld": self.images_from_jsonld,
            "images_from_image_src": self.images_from_image_src,
            "images_from_body": self.images_from_body,
            "images_from_fallback": self.images_from_fallback,
        }


@dataclass(frozen=True)
class ImageDownload:
    status: int
    content_type: str
    payload: bytes
    final_url: str = ""


@dataclass
class ImageMaterializationCounters:
    image_urls_resolved: int = 0
    image_candidates_discovered: int = 0
    image_candidates_attempted: int = 0
    image_candidate_failures: int = 0
    image_download_attempts: int = 0
    image_downloads_succeeded: int = 0
    image_downloads_failed: int = 0
    image_bytes_validated: int = 0
    image_assets_materialized: int = 0
    images_recovered_from_secondary_candidate: int = 0
    headline_image_candidates_attempted: int = 0
    headline_image_recovered: int = 0
    images_browser_loaded: int = 0
    images_browser_failed: int = 0
    image_quality_checks: int = 0
    image_quality_rejections: int = 0
    publisher_logo_candidates_rejected: int = 0
    publisher_default_images_rejected: int = 0
    images_recovered_after_quality_rejection: int = 0
    images_fallback_after_quality_rejection: int = 0
    images_from_feed: int = 0
    images_from_og: int = 0
    images_from_twitter: int = 0
    images_from_jsonld: int = 0
    images_from_image_src: int = 0
    images_from_body: int = 0
    images_from_fallback: int = 0

    def account_source(self, source_kind: str) -> None:
        if source_kind in {
            "rss_image", "media_content", "media_thumbnail", "enclosure",
        }:
            self.images_from_feed += 1
        elif source_kind == "og_image":
            self.images_from_og += 1
        elif source_kind == "twitter_image":
            self.images_from_twitter += 1
        elif source_kind == "jsonld_image":
            self.images_from_jsonld += 1
        elif source_kind == "image_src":
            self.images_from_image_src += 1
        elif source_kind == "body_image":
            self.images_from_body += 1

    def manifest_fields(self) -> dict[str, int]:
        return {
            "image_urls_resolved": self.image_urls_resolved,
            "image_candidates_discovered": self.image_candidates_discovered,
            "image_candidates_attempted": self.image_candidates_attempted,
            "image_candidate_failures": self.image_candidate_failures,
            "image_download_attempts": self.image_download_attempts,
            "image_downloads_succeeded": self.image_downloads_succeeded,
            "image_downloads_failed": self.image_downloads_failed,
            "image_bytes_validated": self.image_bytes_validated,
            "image_assets_materialized": self.image_assets_materialized,
            "images_recovered_from_secondary_candidate": (
                self.images_recovered_from_secondary_candidate
            ),
            "headline_image_candidates_attempted": self.headline_image_candidates_attempted,
            "headline_image_recovered": self.headline_image_recovered,
            "images_browser_loaded": self.images_browser_loaded,
            "images_browser_failed": self.images_browser_failed,
            "image_quality_checks": self.image_quality_checks,
            "image_quality_rejections": self.image_quality_rejections,
            "publisher_logo_candidates_rejected": (
                self.publisher_logo_candidates_rejected
            ),
            "publisher_default_images_rejected": (
                self.publisher_default_images_rejected
            ),
            "images_recovered_after_quality_rejection": (
                self.images_recovered_after_quality_rejection
            ),
            "images_fallback_after_quality_rejection": (
                self.images_fallback_after_quality_rejection
            ),
            "images_from_feed": self.images_from_feed,
            "images_from_og": self.images_from_og,
            "images_from_twitter": self.images_from_twitter,
            "images_from_jsonld": self.images_from_jsonld,
            "images_from_image_src": self.images_from_image_src,
            "images_from_body": self.images_from_body,
            "images_from_fallback": self.images_from_fallback,
        }


@dataclass(frozen=True)
class PublisherUrlResolution:
    original_url: str = ""
    resolved_url: str = ""
    source_kind: str = "unresolved_aggregator"
    original_host: str = ""
    resolved_host: str = ""
    aggregator_used: bool = False
    network_gets: int = 0
    fallback_used: bool = True
    reason: str = "no_safe_publisher_url"


@dataclass
class PublisherUrlResolutionCounters:
    publisher_urls_existing_direct: int = 0
    publisher_urls_from_source_url: int = 0
    publisher_urls_from_orig_link: int = 0
    publisher_urls_from_description: int = 0
    publisher_urls_from_content: int = 0
    publisher_urls_from_guid: int = 0
    publisher_urls_from_atom: int = 0
    publisher_urls_from_redirect: int = 0
    publisher_urls_from_canonical: int = 0
    publisher_urls_from_og_url: int = 0
    publisher_urls_from_outbound: int = 0
    publisher_urls_unresolved: int = 0
    aggregator_page_gets: int = 0

    def account(self, resolution: PublisherUrlResolution) -> None:
        if resolution.source_kind == "existing_publisher_direct":
            self.publisher_urls_existing_direct += 1
        elif resolution.source_kind == "rss_source_url":
            self.publisher_urls_from_source_url += 1
        elif resolution.source_kind == "rss_orig_link":
            self.publisher_urls_from_orig_link += 1
        elif resolution.source_kind == "rss_description_link":
            self.publisher_urls_from_description += 1
        elif resolution.source_kind == "rss_content_link":
            self.publisher_urls_from_content += 1
        elif resolution.source_kind == "rss_guid_direct":
            self.publisher_urls_from_guid += 1
        elif resolution.source_kind == "rss_atom_link":
            self.publisher_urls_from_atom += 1
        elif resolution.source_kind == "aggregator_redirect":
            self.publisher_urls_from_redirect += 1
        elif resolution.source_kind == "aggregator_canonical":
            self.publisher_urls_from_canonical += 1
        elif resolution.source_kind == "aggregator_og_url":
            self.publisher_urls_from_og_url += 1
        elif resolution.source_kind == "aggregator_outbound_link":
            self.publisher_urls_from_outbound += 1
        else:
            self.publisher_urls_unresolved += 1

    def manifest_fields(self) -> dict[str, int]:
        direct_from_feed = (
            self.publisher_urls_existing_direct
            + self.publisher_urls_from_source_url
            + self.publisher_urls_from_orig_link
            + self.publisher_urls_from_description
            + self.publisher_urls_from_content
            + self.publisher_urls_from_guid
            + self.publisher_urls_from_atom
        )
        return {
            "publisher_urls_existing_direct": self.publisher_urls_existing_direct,
            "publisher_urls_from_source_url": self.publisher_urls_from_source_url,
            "publisher_urls_from_orig_link": self.publisher_urls_from_orig_link,
            "publisher_urls_from_description": self.publisher_urls_from_description,
            "publisher_urls_from_content": self.publisher_urls_from_content,
            "publisher_urls_from_guid": self.publisher_urls_from_guid,
            "publisher_urls_from_atom": self.publisher_urls_from_atom,
            "publisher_urls_from_redirect": self.publisher_urls_from_redirect,
            "publisher_urls_from_canonical": self.publisher_urls_from_canonical,
            "publisher_urls_from_og_url": self.publisher_urls_from_og_url,
            "publisher_urls_from_outbound": self.publisher_urls_from_outbound,
            "publisher_urls_unresolved": self.publisher_urls_unresolved,
            "aggregator_page_gets": self.aggregator_page_gets,
            "publisher_urls_direct_from_feed": direct_from_feed,
            "publisher_urls_from_rss_description": (
                self.publisher_urls_from_description
            ),
            "publisher_urls_from_outbound_link": self.publisher_urls_from_outbound,
        }


@dataclass
class SelectionAuditCounters:
    naver_articles_in_coverage: int = 0
    naver_articles_relevance_qualified: int = 0
    naver_articles_after_dedup: int = 0
    google_articles_in_coverage: int = 0
    google_articles_relevance_qualified: int = 0
    direct_candidates_before_selection: int = 0
    aggregator_candidates_before_selection: int = 0
    naver_direct_articles_selected: int = 0
    other_direct_articles_selected: int = 0
    aggregator_articles_selected: int = 0
    direct_candidates_displaced_by_aggregator: int = 0
    direct_candidates_rejected_below_relevance_floor: int = 0

    def manifest_fields(self) -> dict[str, int]:
        return {
            "naver_articles_in_coverage": self.naver_articles_in_coverage,
            "naver_articles_relevance_qualified": self.naver_articles_relevance_qualified,
            "naver_articles_after_dedup": self.naver_articles_after_dedup,
            "google_articles_in_coverage": self.google_articles_in_coverage,
            "google_articles_relevance_qualified": self.google_articles_relevance_qualified,
            "direct_candidates_before_selection": self.direct_candidates_before_selection,
            "aggregator_candidates_before_selection": self.aggregator_candidates_before_selection,
            "naver_direct_articles_selected": self.naver_direct_articles_selected,
            "other_direct_articles_selected": self.other_direct_articles_selected,
            "aggregator_articles_selected": self.aggregator_articles_selected,
            "direct_candidates_displaced_by_aggregator": (
                self.direct_candidates_displaced_by_aggregator
            ),
            "direct_candidates_rejected_below_relevance_floor": (
                self.direct_candidates_rejected_below_relevance_floor
            ),
        }


@dataclass(frozen=True)
class CoverageWindow:
    start: datetime
    end: datetime

    def label(self) -> str:
        return (
            f"{self.start:%Y-%m-%d %H:%M:%S} ~ "
            f"{self.end:%Y-%m-%d %H:%M:%S} KST"
        )


@dataclass(frozen=True)
class EditorialArticle:
    title: str
    summary: str
    source: str
    published_at: datetime
    selected_url: str
    link_kind: str
    link_label: str
    category: str
    summary_html: str = ""
    collection_source_kind: str = ""
    relevance_score: float = 0.0
    freshness_score: float = 0.0
    source_quality_score: float = 0.0
    total_ranking_score: float = 0.0
    total_ranking_key: tuple = ()
    selection_reason: str = "selected_by_legacy_order"
    original_article_url: str = ""
    publisher_article_url: str = ""
    publisher_url_source_kind: str = "unresolved_aggregator"
    publisher_url_reason: str = "publisher_resolution_not_run"
    image_url: str = ""
    image_source_kind: str = "fallback"
    image_source_page_url: str = ""
    image_width: int | None = None
    image_height: int | None = None
    image_fallback_used: bool = True
    image_reason: str = "no_safe_image_candidate"
    image_remote_url: str = ""
    image_download_status: str = "not_attempted"
    image_download_content_type: str = ""
    image_download_bytes: int = 0
    image_local_asset: str = ""
    image_local_src: str = ""
    image_duplicate_asset_reused: bool = False
    image_materialization_reason: str = "not_materialized"
    image_quality_accepted: bool = False
    image_quality_reason: str = ""
    image_quality_signals: tuple[str, ...] = ()
    image_candidates: tuple[ImageCandidateOption, ...] = ()
    image_candidate_attempts: tuple[ImageCandidateAttempt, ...] = ()

    @property
    def published_label(self) -> str:
        return self.published_at.astimezone(KST).strftime("%Y-%m-%d %H:%M KST")


@dataclass(frozen=True)
class RenderedEdition:
    edition_type: str
    edition_key: str
    coverage: CoverageWindow
    html: str
    public_dated_url: str
    public_latest_url: str
    teams_text: str
    teams_html: str
    issue_mode: str
    headline: str
    article_count: int

    @property
    def html_sha256(self) -> str:
        return hashlib.sha256(self.html.encode("utf-8")).hexdigest()


def _as_kst(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise EditorialError("timezone-aware datetime required")
    return value.astimezone(KST)


def daily_coverage(run_at: datetime) -> CoverageWindow:
    current = _as_kst(run_at)
    run_date = current.date()
    return CoverageWindow(
        datetime.combine(run_date - timedelta(days=1), time(7, 0), KST),
        datetime.combine(run_date, time(6, 40), KST),
    )


def weekly_anchor_date(run_at: datetime) -> date:
    current_date = _as_kst(run_at).date()
    days_since_wednesday = (current_date.weekday() - 2) % 7
    return current_date - timedelta(days=days_since_wednesday)


def weekly_coverage(run_at: datetime) -> CoverageWindow:
    anchor = weekly_anchor_date(run_at)
    return CoverageWindow(
        datetime.combine(anchor - timedelta(days=7), time.min, KST),
        datetime.combine(anchor - timedelta(days=1), time(23, 59, 59), KST),
    )


def edition_key(edition_type: str, run_at: datetime) -> str:
    if edition_type == "daily":
        return _as_kst(run_at).strftime("%Y-%m-%d")
    if edition_type == "weekly":
        iso = weekly_anchor_date(run_at).isocalendar()
        return f"{iso.year}-W{iso.week:02d}"
    raise EditorialError(f"unsupported edition type: {edition_type!r}")


def coverage_for(edition_type: str, run_at: datetime) -> CoverageWindow:
    if edition_type == "daily":
        return daily_coverage(run_at)
    if edition_type == "weekly":
        return weekly_coverage(run_at)
    raise EditorialError(f"unsupported edition type: {edition_type!r}")


def derive_public_root(report_url: str) -> str:
    value = str(report_url or "").strip()
    try:
        parsed = urlparse(value)
    except ValueError as exc:
        raise EditorialError("REPORT_URL is invalid") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise EditorialError("REPORT_URL must use http/https")
    if parsed.params or parsed.query or parsed.fragment or not parsed.path.endswith(
        DAILY_REPORT_SUFFIX
    ):
        raise EditorialError("REPORT_URL suffix contract mismatch")
    root_path = parsed.path[: -len(DAILY_REPORT_SUFFIX)].rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}{root_path}"


def _validate_fixture_root(root_url: str) -> str:
    value = str(root_url or "").strip().rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise EditorialError("preview public root must use http/https")
    if parsed.params or parsed.query or parsed.fragment:
        raise EditorialError("preview public root cannot contain params/query/fragment")
    return value


def public_urls(root_url: str, edition_type: str, key: str) -> tuple[str, str]:
    root = _validate_fixture_root(root_url)
    if edition_type not in {"daily", "weekly"}:
        raise EditorialError("unsupported edition type")
    safe_key = re.sub(r"[^0-9W-]", "", key)
    if safe_key != key or not key:
        raise EditorialError("invalid edition key")
    base = f"{root}/editorial/{edition_type}"
    return f"{base}/{key}.html", f"{base}/latest.html"


def valid_http_url(value: object) -> str:
    candidate = str(value or "").strip()
    try:
        parsed = urlparse(candidate)
    except ValueError:
        return ""
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or "\r" in candidate
        or "\n" in candidate
    ):
        return ""
    return candidate


_IMAGE_URL_WRAPPERS = (('"', '"'), ("'", "'"), ("“", "”"), ("‘", "’"))
_IMAGE_URL_QUOTE_CHARS = {'"', "'", "“", "”", "‘", "’"}
_URL_PATH_SAFE = "/:@!$&'()*+,;=-._~%"
_URL_QUERY_SAFE = "=&?/:@!$'()*+,;%-._~"


class _EditorialInlineSanitizer(HTMLParser):
    """Allow only bold and line-break markup in operator-edited summaries."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.strong_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        lowered = tag.casefold()
        if lowered in {"strong", "b"}:
            self.parts.append("<strong>")
            self.strong_depth += 1
        elif lowered == "br":
            self.parts.append("<br>")

    def handle_startendtag(self, tag: str, attrs) -> None:
        if tag.casefold() == "br":
            self.parts.append("<br>")

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() in {"strong", "b"} and self.strong_depth:
            self.parts.append("</strong>")
            self.strong_depth -= 1

    def handle_data(self, data: str) -> None:
        self.parts.append(escape(data))

    def output(self) -> str:
        return "".join(self.parts) + "</strong>" * self.strong_depth


class _EditorialInlineText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.casefold() == "br":
            self.parts.append(" ")

    def handle_startendtag(self, tag: str, attrs) -> None:
        if tag.casefold() == "br":
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def sanitize_editorial_inline_html(value: object) -> str:
    parser = _EditorialInlineSanitizer()
    parser.feed(str(value or ""))
    parser.close()
    return parser.output().strip()


def editorial_inline_plain_text(value: object) -> str:
    parser = _EditorialInlineText()
    parser.feed(sanitize_editorial_inline_html(value))
    parser.close()
    return " ".join("".join(parser.parts).split())


def _has_url_control_character(value: str) -> bool:
    return any(unicodedata.category(character) == "Cc" for character in value)


def _strip_wrapping_url_quotes(value: str) -> str:
    for opening, closing in _IMAGE_URL_WRAPPERS:
        if len(value) >= 2 and value.startswith(opening) and value.endswith(closing):
            return value[1:-1].strip(" \f\v")
    return value


def _normalize_url_hostname(hostname: str) -> str:
    host = hostname.casefold().rstrip(".")
    if not host:
        return ""
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        return host if literal.is_global else ""
    try:
        host = host.encode("idna").decode("ascii").casefold()
    except UnicodeError:
        return ""
    if (
        "." not in host
        or host == "localhost"
        or any(host.endswith(suffix) for suffix in _PRIVATE_HOST_SUFFIXES)
        or not re.fullmatch(r"[a-z0-9.-]+", host)
        or any(not label or label.startswith("-") or label.endswith("-") for label in host.split("."))
    ):
        return ""
    return host


def _candidate_text_like_without_image_hint(
    *,
    raw_value: str,
    normalized_url: str,
    content_context: str,
) -> bool:
    if not (
        any(character.isspace() for character in raw_value)
        or any(character in raw_value for character in _IMAGE_URL_QUOTE_CHARS)
    ):
        return False
    media_type = content_context.split(";", 1)[0].strip().casefold()
    if media_type.startswith("image/") or _has_image_extension(normalized_url):
        return False
    return True


def normalize_image_candidate_url(
    value: object,
    *,
    base_url: str = "",
) -> str:
    """Return one request-safe image candidate URL, or an empty string if invalid."""
    if not isinstance(value, str):
        return ""
    candidate = unescape(value)
    if _has_url_control_character(candidate):
        return ""
    candidate = _strip_wrapping_url_quotes(candidate.strip(" \f\v"))
    if not candidate or len(candidate) > IMAGE_URL_MAX_LENGTH:
        return ""
    try:
        joined = urljoin(base_url, candidate)
        parsed = urlparse(joined)
        port = parsed.port
    except (TypeError, ValueError, UnicodeError):
        return ""
    if (
        parsed.scheme.casefold() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        return ""
    host = _normalize_url_hostname(parsed.hostname)
    if not host:
        return ""
    if port is not None and (port < 1 or port > 65535):
        return ""
    netloc_host = f"[{host}]" if ":" in host else host
    netloc = f"{netloc_host}:{port}" if port is not None else netloc_host
    path = quote(parsed.path or "", safe=_URL_PATH_SAFE)
    params = quote(parsed.params or "", safe=_URL_PATH_SAFE)
    query = quote(parsed.query or "", safe=_URL_QUERY_SAFE)
    normalized = urlunparse(
        (parsed.scheme.casefold(), netloc, path, params, query, "")
    )
    if len(normalized) > IMAGE_URL_MAX_LENGTH or _has_url_control_character(normalized):
        return ""
    return normalized


def _url_host(value: object) -> str:
    try:
        return (urlparse(str(value or "")).hostname or "").casefold().rstrip(".")
    except ValueError:
        return ""


def _publisher_priority(source: str, selected_url: str) -> tuple[str, int]:
    quality = source_quality.classify(source)
    if quality.get("source_type") == "institution":
        return "institution", 0

    source_key = re.sub(
        r"\s+",
        "",
        unicodedata.normalize("NFKC", str(source or "")).casefold(),
    )
    host = _url_host(selected_url)

    for group, rank, aliases, domains in _PUBLISHER_PRIORITY_POLICIES:
        alias_match = any(
            re.sub(r"\s+", "", alias.casefold()) in source_key
            for alias in aliases
        )
        domain_match = any(
            host == domain or host.endswith("." + domain)
            for domain in domains
        )
        if alias_match or domain_match:
            return group, rank

    return "other", 999


def _preferred_publisher_target(limit: int) -> int:
    configured = (
        PREFERRED_PUBLISHER_DAILY_TARGET
        if limit <= DAILY_MAX_ARTICLES
        else PREFERRED_PUBLISHER_WEEKLY_TARGET
    )
    return min(limit, configured)


def parse_published_at(value: object) -> datetime:
    raw = str(value or "").strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise EditorialError("article published_at is invalid") from exc
    if parsed.tzinfo is None:
        raise EditorialError("article published_at must include timezone")
    return parsed.astimezone(KST)


def _summary_sentences(title: str, snippet: str) -> str:
    cleaned = " ".join(str(snippet or "").split())
    if not cleaned:
        raise EditorialError("article summary/snippet is required")
    sentences = [part.strip() for part in _SENTENCE_RE.split(cleaned) if part.strip()]
    if len(sentences) == 1:
        comma_parts = [
            part.strip() for part in re.split(r"(?<=[,;·])\s*", cleaned) if part.strip()
        ]
        if len(comma_parts) >= 2:
            midpoint = max(1, math.ceil(len(comma_parts) / 2))
            sentences = [
                " ".join(comma_parts[:midpoint]).rstrip(",;·") + ".",
                " ".join(comma_parts[midpoint:]).rstrip(",;·") + ".",
            ]
        elif cleaned.casefold() != title.strip().casefold():
            sentences = [title.strip().rstrip(".!?") + ".", cleaned]
    return " ".join(sentences[:3])


def classify_category(title: str, summary: str) -> str:
    text = f"{title} {summary}".casefold()
    rules = (
        ("AI 인프라", ("데이터센터", "전력", "반도체", "gpu", "인프라")),
        ("정책·규제", ("규제", "법안", "정부", "정책", "보안", "안전")),
        ("투자·사업", ("투자", "계약", "수주", "펀드", "인수", "파트너십")),
        ("기술·제품", ("모델", "서비스", "플랫폼", "로봇", "소프트웨어", "제품")),
    )
    for label, keywords in rules:
        if any(keyword in text for keyword in keywords):
            return label
    return "기업·산업"


def _metadata_url_values(value: object) -> list[str]:
    values = value if isinstance(value, (list, tuple)) else (value,)
    output: list[str] = []
    for item in values:
        if isinstance(item, Mapping):
            candidate = item.get("url") or item.get("href") or ""
        else:
            candidate = item
        text = str(candidate or "").strip()
        if text and text not in output:
            output.append(text)
    return output


def _google_or_aggregator_host(host: str) -> bool:
    lowered = host.casefold().rstrip(".")
    if (
        lowered == "google.com"
        or lowered.startswith("google.")
        or ".google." in lowered
        or lowered.endswith(".google.com")
    ):
        return True
    return any(marker in lowered for marker in _PUBLISHER_REJECT_HOST_MARKERS)


def _publisher_host_matches_source(candidate_url: str, source_home_url: str) -> bool:
    try:
        candidate = (urlparse(candidate_url).hostname or "").casefold().rstrip(".")
        source = (urlparse(source_home_url).hostname or "").casefold().rstrip(".")
    except ValueError:
        return False
    if not candidate or not source:
        return False
    return (
        candidate == source
        or candidate.endswith("." + source)
        or source.endswith("." + candidate)
    )


def _normalize_publisher_article_url(
    value: object,
    *,
    source_home_url: str = "",
    require_source_match: bool = False,
) -> str:
    candidate = valid_http_url(value)
    if not candidate or len(candidate) > PUBLISHER_URL_MAX_LENGTH:
        return ""
    try:
        parsed = urlparse(candidate)
    except ValueError:
        return ""
    host = (parsed.hostname or "").casefold().rstrip(".")
    if (
        not host
        or host == "localhost"
        or "." not in host
        or any(host.endswith(suffix) for suffix in _PRIVATE_HOST_SUFFIXES)
        or _google_or_aggregator_host(host)
        or news_access.is_aggregator_url(candidate)
        or news_access.classify_source_type(candidate) in {"portal", "search", "rss"}
    ):
        return ""
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None and not literal.is_global:
        return ""
    lowered_path = unquote(parsed.path or "").casefold()
    if not lowered_path.strip("/") or any(
        marker in lowered_path for marker in _PUBLISHER_REJECT_PATH_MARKERS
    ):
        return ""
    if any(lowered_path.endswith(extension) for extension in _IMAGE_EXTENSIONS):
        return ""
    if (
        require_source_match
        and source_home_url
        and not _publisher_host_matches_source(candidate, source_home_url)
    ):
        return ""
    query = [
        (key, item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_")
        and key.casefold() not in _TRACKING_QUERY_KEYS
    ]
    normalized = parsed._replace(
        query=urlencode(query, doseq=True),
        fragment="",
    )
    return urlunparse(normalized)


def _unique_publisher_candidate(
    values: Iterable[object],
    *,
    source_home_url: str = "",
    require_source_match: bool = False,
) -> tuple[str, str]:
    candidates: list[str] = []
    for value in values:
        for candidate in _metadata_url_values(value):
            direct = _normalize_publisher_article_url(
                candidate,
                source_home_url=source_home_url,
                require_source_match=require_source_match,
            )
            if direct and direct not in candidates:
                candidates.append(direct)
    if len(candidates) == 1:
        return candidates[0], ""
    if len(candidates) > 1:
        return "", "multiple_publisher_candidates"
    return "", ""


class _PublisherUrlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.canonical_urls: list[str] = []
        self.og_urls: list[str] = []
        self.outbound_urls: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        values = {str(key).casefold(): str(value or "") for key, value in attrs}
        lowered = tag.casefold()
        if lowered == "link":
            rel = set(values.get("rel", "").casefold().split())
            if "canonical" in rel and values.get("href"):
                self.canonical_urls.append(values["href"])
        elif lowered == "meta":
            key = (values.get("property") or values.get("name") or "").casefold()
            if key == "og:url" and values.get("content"):
                self.og_urls.append(values["content"])
        elif lowered == "a" and values.get("href") and len(self.outbound_urls) < 128:
            self.outbound_urls.append(values["href"])


def _publisher_resolution(
    original_url: str,
    resolved_url: str,
    source_kind: str,
    *,
    aggregator_used: bool,
    network_gets: int = 0,
    reason: str,
) -> PublisherUrlResolution:
    return PublisherUrlResolution(
        original_url=original_url,
        resolved_url=resolved_url,
        source_kind=source_kind,
        original_host=_url_host(original_url),
        resolved_host=_url_host(resolved_url),
        aggregator_used=aggregator_used,
        network_gets=network_gets,
        fallback_used=not bool(resolved_url),
        reason=reason,
    )


@dataclass(frozen=True)
class _ImageCandidate:
    url: str
    source_kind: str
    width: int | None = None
    height: int | None = None
    content_type: str = ""
    context: str = ""


def _positive_int(value: object) -> int | None:
    try:
        number = int(str(value or "").strip())
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _article_metadata(raw: Mapping) -> Mapping:
    metadata = raw.get("source_metadata") or raw.get("source_metadata_json") or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except (TypeError, ValueError):
            metadata = {}
    return metadata if isinstance(metadata, Mapping) else {}


def _collection_source_kind(raw: Mapping) -> str:
    metadata = _article_metadata(raw)
    return str(
        raw.get("collection_source_kind")
        or metadata.get("provider")
        or raw.get("provider")
        or ""
    ).strip()


@dataclass(frozen=True)
class _ArticleCandidate:
    article: EditorialArticle
    raw: Mapping
    selected_url: str
    selected_kind: str
    provider_tokens: frozenset[str]
    is_direct: bool
    publisher_key: str
    title_key: str
    cluster_key: str
    relevance_score: float
    freshness_score: float
    source_quality_score: float
    total_ranking_score: float
    ranking_key: tuple
    relevance_reasons: tuple[str, ...]

    @property
    def is_naver_direct(self) -> bool:
        return self.is_direct and "naver_news_api" in self.provider_tokens

    @property
    def is_aggregator(self) -> bool:
        return not self.is_direct

    @property
    def direct_priority_eligible(self) -> bool:
        return self.is_direct and self.relevance_score >= DIRECT_PRIORITY_RELEVANCE_FLOOR

    @property
    def publisher_priority(self) -> tuple[str, int]:
        return _publisher_priority(
            self.article.source,
            self.selected_url,
        )

    @property
    def is_primary_publisher(self) -> bool:
        return self.publisher_priority[0] == "primary"

    @property
    def is_official_institution(self) -> bool:
        return self.publisher_priority[0] == "institution"


def _provider_tokens(raw: Mapping) -> frozenset[str]:
    return frozenset(
        item
        for item in _collection_source_kind(raw).split("+")
        if item
    )


def _title_fingerprint(title: str) -> str:
    normalized = unicodedata.normalize("NFKC", title or "")
    normalized = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", normalized)
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", normalized).casefold()


def _publisher_key(source: str, selected_url: str) -> str:
    host = _url_host(selected_url)
    if host.startswith("www."):
        host = host[4:]
    return host or re.sub(r"\s+", "", source or "").casefold() or "unknown"


def _candidate_relevance(
    title: str,
    summary: str,
    category: str,
    raw: Mapping,
) -> tuple[float, tuple[str, ...]]:
    metadata = _article_metadata(raw)
    query = str(metadata.get("query") or "").strip()
    text_groups = news_coverage.query_groups_for_text(title, summary)
    query_group = news_coverage.query_group_for_query(query)
    score = 0.0
    reasons: list[str] = []
    if query_group:
        score += 2.0
        reasons.append(f"query_group:{query_group}")
    if text_groups:
        score += min(2.0, len(text_groups) * 0.75)
        reasons.append("text_group:" + ",".join(text_groups[:3]))
    if category != "기업·산업":
        score += 0.5
        reasons.append(f"category:{category}")

    quality = source_quality.classify(
        str(raw.get("source") or ""),
        title,
    )
    if (
        quality.get("source_type") == "institution"
        and category != "기업·산업"
    ):
        score += 0.5
        reasons.append("institution_relevant_category")

    if not reasons and query:
        # Provider query evidence is weaker than text/query-group agreement but prevents
        # complete blindness for configured non-coverage source files.
        score += 0.5
        reasons.append("provider_query_only")
    if not reasons and not (_provider_tokens(raw) & {"google_news_rss", "naver_news_api"}):
        score += 1.0
        reasons.append("curated_or_offline_source")
    return round(score, 3), tuple(reasons)


def _candidate_freshness(published: datetime, coverage: CoverageWindow) -> float:
    age_minutes = max(0.0, (coverage.end - published).total_seconds() / 60.0)
    return round(max(0.0, 3.0 - min(age_minutes, 1440.0) / 480.0), 3)


def _build_article_candidate(
    raw: Mapping,
    coverage: CoverageWindow,
) -> _ArticleCandidate | None:
    try:
        published = parse_published_at(raw.get("published_at"))
    except EditorialError:
        return None
    if not (coverage.start <= published <= coverage.end):
        return None
    title = " ".join(str(raw.get("title") or "").split())
    source = " ".join(str(raw.get("source") or "").split())
    if not title or not source:
        return None
    selected = news_access.choose_article_link(raw)
    selected_url = valid_http_url(selected.url)
    if not selected_url:
        return None
    if selected.is_direct:
        selected_url = _normalize_publisher_article_url(selected_url)
        if not selected_url:
            return None
    if selected.kind not in {
        news_access.LINK_KIND_PUBLISHER_DIRECT,
        news_access.LINK_KIND_GOOGLE_NEWS_FALLBACK,
        news_access.LINK_KIND_PORTAL_FALLBACK,
    }:
        return None
    try:
        summary = _summary_sentences(
            title, str(raw.get("snippet") or raw.get("summary") or "")
        )
    except EditorialError:
        return None
    category = classify_category(title, summary)
    relevance, reasons = _candidate_relevance(title, summary, category, raw)
    freshness = _candidate_freshness(published, coverage)
    source_quality = 2.0 if selected.is_direct else 0.0
    total = round(relevance + freshness + source_quality, 3)
    publisher_key = _publisher_key(source, selected_url)
    title_key = _title_fingerprint(title)
    ranking_key = (
        total,
        relevance,
        source_quality,
        freshness,
        published.isoformat(),
        title_key,
        publisher_key,
    )
    return _ArticleCandidate(
        article=EditorialArticle(
            title=title,
            summary=summary,
            source=source,
            published_at=published,
            selected_url=selected_url,
            link_kind=selected.kind,
            link_label=selected.label,
            category=category,
            collection_source_kind=_collection_source_kind(raw),
            relevance_score=relevance,
            freshness_score=freshness,
            source_quality_score=source_quality,
            total_ranking_score=total,
            total_ranking_key=ranking_key,
        ),
        raw=raw,
        selected_url=selected_url,
        selected_kind=selected.kind,
        provider_tokens=_provider_tokens(raw),
        is_direct=selected.is_direct,
        publisher_key=publisher_key,
        title_key=title_key,
        cluster_key=title_key if len(title_key) >= 12 else "",
        relevance_score=relevance,
        freshness_score=freshness,
        source_quality_score=source_quality,
        total_ranking_score=total,
        ranking_key=ranking_key,
        relevance_reasons=reasons,
    )


def _legacy_article_from_raw(
    raw: Mapping,
    coverage: CoverageWindow,
) -> tuple[EditorialArticle, Mapping] | None:
    try:
        published = parse_published_at(raw.get("published_at"))
    except EditorialError:
        return None
    if not (coverage.start <= published <= coverage.end):
        return None
    title = " ".join(str(raw.get("title") or "").split())
    source = " ".join(str(raw.get("source") or "").split())
    if not title or not source:
        return None
    selected = news_access.choose_article_link(raw)
    selected_url = valid_http_url(selected.url)
    if not selected_url:
        return None
    if selected.is_direct:
        selected_url = _normalize_publisher_article_url(selected_url)
        if not selected_url:
            return None
    if selected.kind not in {
        news_access.LINK_KIND_PUBLISHER_DIRECT,
        news_access.LINK_KIND_GOOGLE_NEWS_FALLBACK,
        news_access.LINK_KIND_PORTAL_FALLBACK,
    }:
        return None
    try:
        summary = _summary_sentences(
            title, str(raw.get("snippet") or raw.get("summary") or "")
        )
    except EditorialError:
        return None
    return (
        EditorialArticle(
            title=title,
            summary=summary,
            source=source,
            published_at=published,
            selected_url=selected_url,
            link_kind=selected.kind,
            link_label=selected.label,
            category=classify_category(title, summary),
            collection_source_kind=_collection_source_kind(raw),
        ),
        raw,
    )


def _select_legacy_article_rows(
    raw_articles: Iterable[Mapping],
    coverage: CoverageWindow,
    *,
    limit: int,
) -> list[tuple[EditorialArticle, Mapping]]:
    normalized: list[tuple[EditorialArticle, Mapping]] = []
    seen: set[tuple[str, str]] = set()
    for raw in raw_articles:
        if not isinstance(raw, Mapping):
            continue
        row = _legacy_article_from_raw(raw, coverage)
        if row is None:
            continue
        article, _raw = row
        dedup_key = (
            re.sub(r"\W+", "", article.title).casefold(),
            article.selected_url.rstrip("/").casefold(),
        )
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        normalized.append(row)
    normalized.sort(key=lambda item: (item[0].published_at, item[0].title), reverse=True)
    return normalized[:limit]


def _candidate_sort_key(candidate: _ArticleCandidate) -> tuple:
    return candidate.ranking_key


def _is_better_duplicate(
    candidate: _ArticleCandidate,
    existing: _ArticleCandidate,
) -> bool:
    candidate_key = (
        candidate.is_naver_direct,
        candidate.is_direct,
        candidate.total_ranking_score,
        candidate.article.published_at.isoformat(),
    )
    existing_key = (
        existing.is_naver_direct,
        existing.is_direct,
        existing.total_ranking_score,
        existing.article.published_at.isoformat(),
    )
    return candidate_key > existing_key


def _deduplicate_article_candidates(
    candidates: list[_ArticleCandidate],
) -> list[_ArticleCandidate]:
    by_exact: dict[tuple[str, str], _ArticleCandidate] = {}
    for candidate in candidates:
        exact_key = (
            re.sub(r"\W+", "", candidate.article.title).casefold(),
            candidate.selected_url.rstrip("/").casefold(),
        )
        existing = by_exact.get(exact_key)
        if existing is None or _is_better_duplicate(candidate, existing):
            by_exact[exact_key] = candidate

    clustered: list[_ArticleCandidate] = []
    by_cluster: dict[str, int] = {}
    for candidate in sorted(by_exact.values(), key=_candidate_sort_key, reverse=True):
        key = candidate.cluster_key
        idx = by_cluster.get(key) if key else None
        if idx is None:
            by_cluster[key] = len(clustered)
            clustered.append(candidate)
            continue
        existing = clustered[idx]
        close_time = (
            abs(candidate.article.published_at - existing.article.published_at)
            <= TITLE_CLUSTER_TIME_WINDOW
        )
        same_event_hint = candidate.is_aggregator != existing.is_aggregator
        if key and close_time and same_event_hint and _is_better_duplicate(
            candidate, existing
        ):
            clustered[idx] = candidate
        elif not (key and close_time and same_event_hint):
            clustered.append(candidate)
    return sorted(clustered, key=_candidate_sort_key, reverse=True)


def _select_with_diversity(
    pool: list[_ArticleCandidate],
    selected: list[_ArticleCandidate],
    *,
    limit: int,
    predicate: Callable[[_ArticleCandidate], bool] | None = None,
) -> None:
    selected_ids = {id(item) for item in selected}

    def can_add(candidate: _ArticleCandidate, *, strict: bool) -> bool:
        if id(candidate) in selected_ids:
            return False
        if predicate is not None and not predicate(candidate):
            return False
        if not strict:
            return True
        publishers = Counter(item.publisher_key for item in selected)
        categories = Counter(item.article.category for item in selected)
        return (
            publishers[candidate.publisher_key] < PUBLISHER_DIVERSITY_SOFT_CAP
            and categories[candidate.article.category] < CATEGORY_DIVERSITY_SOFT_CAP
        )

    for strict in (True, False):
        for candidate in pool:
            if len(selected) >= limit:
                return
            if can_add(candidate, strict=strict):
                selected.append(candidate)
                selected_ids.add(id(candidate))



def _select_article_candidates(
    candidates: list[_ArticleCandidate],
    *,
    limit: int,
    audit: SelectionAuditCounters | None,
) -> list[_ArticleCandidate]:
    relevant = [
        candidate
        for candidate in candidates
        if candidate.relevance_score >= SELECTION_RELEVANCE_FLOOR
    ]
    relevant.sort(key=_candidate_sort_key, reverse=True)

    direct_pool = [
        candidate
        for candidate in relevant
        if candidate.direct_priority_eligible
    ]

    primary_pool = sorted(
        (
            candidate
            for candidate in relevant
            if candidate.is_primary_publisher
        ),
        key=lambda candidate: (
            -candidate.publisher_priority[1],
            candidate.ranking_key,
        ),
        reverse=True,
    )

    institution_pool = sorted(
        (
            candidate
            for candidate in relevant
            if candidate.is_official_institution
        ),
        key=_candidate_sort_key,
        reverse=True,
    )

    direct_supply_sufficient = (
        len(direct_pool) >= DIRECT_SUPPLY_FOR_AGGREGATOR_CAP
    )
    selected: list[_ArticleCandidate] = []

    if primary_pool:
        selected.append(primary_pool[0])
    elif relevant:
        best = relevant[0]
        best_direct = direct_pool[0] if direct_pool else None

        if (
            best_direct is not None
            and (best.is_aggregator or not best.is_direct)
            and best.total_ranking_score - best_direct.total_ranking_score
            <= HEADLINE_DIRECT_MARGIN
        ):
            selected.append(best_direct)
        else:
            selected.append(best)

    if (
        institution_pool
        and len(selected) < limit
        and not any(item.is_official_institution for item in selected)
    ):
        _select_with_diversity(
            institution_pool,
            selected,
            limit=min(limit, len(selected) + 1),
        )

    target = _preferred_publisher_target(limit)
    selected_primary_count = sum(
        item.is_primary_publisher
        for item in selected
    )

    if selected_primary_count < target:
        _select_with_diversity(
            primary_pool,
            selected,
            limit=min(
                limit,
                len(selected) + target - selected_primary_count,
            ),
        )

    if direct_supply_sufficient:
        _select_with_diversity(
            direct_pool,
            selected,
            limit=min(limit, DIRECT_PRIORITY_TARGET),
        )

    def aggregator_allowed(candidate: _ArticleCandidate) -> bool:
        return not (
            direct_supply_sufficient
            and candidate.is_aggregator
            and sum(1 for item in selected if item.is_aggregator)
            >= AGGREGATOR_CAP_WHEN_DIRECT_SUPPLY_SUFFICIENT
        )

    _select_with_diversity(
        relevant,
        selected,
        limit=limit,
        predicate=aggregator_allowed,
    )

    if audit is not None:
        selected_ids = {id(item) for item in selected}

        audit.naver_direct_articles_selected = sum(
            1 for item in selected if item.is_naver_direct
        )
        audit.other_direct_articles_selected = sum(
            1
            for item in selected
            if item.is_direct and not item.is_naver_direct
        )
        audit.aggregator_articles_selected = sum(
            1 for item in selected if item.is_aggregator
        )

        unselected_direct = [
            item for item in direct_pool
            if id(item) not in selected_ids
        ]

        audit.direct_candidates_displaced_by_aggregator = min(
            len(unselected_direct),
            audit.aggregator_articles_selected,
        )

    return [
        replace(
            candidate,
            article=replace(
                candidate.article,
                selection_reason=_selection_reason(
                    candidate,
                    selected,
                ),
            ),
        )
        for candidate in selected[:limit]
    ]

def _selection_reason(
    candidate: _ArticleCandidate,
    selected: list[_ArticleCandidate],
) -> str:
    direct_count = sum(1 for item in selected if item.is_direct)
    aggregator_count = sum(1 for item in selected if item.is_aggregator)
    if candidate.is_naver_direct:
        return (
            "selected_naver_direct_by_relevance_freshness_source_quality"
            f";direct_selected={direct_count};aggregator_selected={aggregator_count}"
        )
    if candidate.is_direct:
        return (
            "selected_publisher_direct_by_relevance_freshness_source_quality"
            f";direct_selected={direct_count};aggregator_selected={aggregator_count}"
        )
    return (
        "selected_aggregator_after_direct_pool_or_importance"
        f";direct_selected={direct_count};aggregator_selected={aggregator_count}"
    )


def _mapping_candidates(value: object, source_kind: str) -> list[_ImageCandidate]:
    values = value if isinstance(value, (list, tuple)) else (value,)
    output: list[_ImageCandidate] = []
    for item in values:
        if isinstance(item, Mapping):
            url = item.get("url") or item.get("src") or item.get("href") or ""
            width = _positive_int(item.get("width"))
            height = _positive_int(item.get("height"))
            content_type = str(item.get("type") or item.get("content_type") or "")
            context = str(item.get("alt") or item.get("title") or "")
        else:
            url, width, height, content_type, context = item, None, None, "", ""
        if str(url or "").strip():
            output.append(
                _ImageCandidate(
                    str(url).strip(), source_kind, width, height, content_type, context
                )
            )
    return output


def _feed_image_candidates(raw: Mapping) -> list[_ImageCandidate]:
    metadata = _article_metadata(raw)
    groups: tuple[tuple[str, tuple[object, ...]], ...] = (
        (
            "rss_image",
            (
                raw.get("image_url"),
                raw.get("image"),
                raw.get("representative_image_url"),
                raw.get("thumbnail_url"),
                metadata.get("image_url"),
                metadata.get("thumbnail_url"),
            ),
        ),
        (
            "media_content",
            (raw.get("media_content"), metadata.get("media_content")),
        ),
        (
            "media_thumbnail",
            (raw.get("media_thumbnail"), metadata.get("media_thumbnail")),
        ),
        ("enclosure", (raw.get("enclosure"), metadata.get("enclosure"))),
    )
    output: list[_ImageCandidate] = []
    for source_kind, values in groups:
        for value in values:
            output.extend(_mapping_candidates(value, source_kind))
    return output


def _safe_image_url(
    value: object,
    *,
    base_url: str = "",
    content_type: str = "",
) -> str:
    raw_value = unescape(value) if isinstance(value, str) else ""
    candidate = normalize_image_candidate_url(value, base_url=base_url)
    if not candidate:
        return ""
    try:
        parsed = urlparse(candidate)
    except ValueError:
        return ""
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        return ""
    host = parsed.hostname.casefold().rstrip(".")
    if (
        host == "localhost"
        or "." not in host
        or any(host.endswith(suffix) for suffix in _PRIVATE_HOST_SUFFIXES)
        or _google_or_aggregator_host(host)
        or news_access.is_aggregator_url(candidate)
    ):
        return ""
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None and not literal.is_global:
        return ""
    if parsed.scheme == "http":
        try:
            base = urlparse(base_url)
        except ValueError:
            return ""
        if base.scheme != "https" or base.hostname != parsed.hostname:
            return ""
        parsed = parsed._replace(scheme="https")
        candidate = urlunparse(parsed)
    lowered = unquote(candidate).casefold()
    context = str(content_type or "").casefold()
    if _candidate_text_like_without_image_hint(
        raw_value=raw_value,
        normalized_url=candidate,
        content_context=context,
    ):
        return ""
    if any(marker in lowered or marker in context for marker in _IMAGE_REJECT_MARKERS):
        return ""
    return candidate


def _has_image_extension(url: str) -> bool:
    path = urlparse(url).path.casefold()
    return any(path.endswith(extension) for extension in _IMAGE_EXTENSIONS)


def _geometry_is_safe(width: int | None, height: int | None) -> bool:
    if width is not None and width < IMAGE_MIN_WIDTH:
        return False
    if height is not None and height < IMAGE_MIN_HEIGHT:
        return False
    if width is not None and height is not None and width * height < 20_000:
        return False
    return True


def _host_is_public(hostname: str) -> bool:
    host = hostname.casefold().rstrip(".")
    if (
        host == "localhost"
        or "." not in host
        or any(host.endswith(suffix) for suffix in _PRIVATE_HOST_SUFFIXES)
    ):
        return False
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        return literal.is_global
    try:
        addresses = {
            result[4][0].split("%", 1)[0]
            for result in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        }
    except (OSError, UnicodeError):
        return False
    if not addresses:
        return False
    try:
        return all(ipaddress.ip_address(address).is_global for address in addresses)
    except ValueError:
        return False


def _public_fetch_url(value: str) -> str:
    candidate = normalize_image_candidate_url(value)
    if not candidate or len(candidate) > IMAGE_URL_MAX_LENGTH:
        raise EditorialError("unsafe image metadata URL")
    parsed = urlparse(candidate)
    if not parsed.hostname or not _host_is_public(parsed.hostname):
        raise EditorialError("private or non-public image metadata host")
    return candidate


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = _public_fetch_url(urljoin(req.full_url, newurl))
        count = int(getattr(req, "_hdec_redirect_count", 0)) + 1
        if count > IMAGE_REDIRECT_LIMIT:
            raise urllib.error.HTTPError(
                target, code, "image metadata redirect limit exceeded", headers, fp
            )
        redirected = super().redirect_request(req, fp, code, msg, headers, target)
        if redirected is not None:
            setattr(redirected, "_hdec_redirect_count", count)
        return redirected


def _network_opener():
    return urllib.request.build_opener(_SafeRedirectHandler())


def _open_request(opener: object, request: urllib.request.Request, timeout: int):
    if callable(opener) and not hasattr(opener, "open"):
        return opener(request, timeout=timeout)
    return opener.open(request, timeout=timeout)


def _fetch_aggregator_html(
    page_url: str,
    *,
    counters: PublisherUrlResolutionCounters,
    opener: object | None = None,
) -> tuple[str, str]:
    safe_url = _public_fetch_url(page_url)
    request = urllib.request.Request(
        safe_url,
        headers={
            "User-Agent": (
                "HDEC-Editorial-PublisherResolver/1.0 "
                "(+anonymous-metadata-only)"
            ),
            "Accept": "text/html,application/xhtml+xml;q=0.9",
        },
        method="GET",
    )
    counters.aggregator_page_gets += 1
    response = _open_request(
        opener or _network_opener(), request, PUBLISHER_PAGE_TIMEOUT_SECONDS
    )
    with response:
        status = int(getattr(response, "status", response.getcode()))
        if status < 200 or status >= 400:
            raise EditorialError("aggregator response was not successful")
        final_url = _public_fetch_url(response.geturl())
        content_type = str(response.headers.get("Content-Type") or "")
        if content_type.split(";", 1)[0].strip().casefold() not in {
            "text/html", "application/xhtml+xml",
        }:
            raise EditorialError("aggregator response is not HTML")
        length = _positive_int(response.headers.get("Content-Length"))
        if length is not None and length > PUBLISHER_PAGE_MAX_BYTES:
            raise EditorialError("aggregator response is too large")
        payload = response.read(PUBLISHER_PAGE_MAX_BYTES + 1)
        if len(payload) > PUBLISHER_PAGE_MAX_BYTES:
            raise EditorialError("aggregator response is too large")
        charset = (
            response.headers.get_content_charset()
            if hasattr(response.headers, "get_content_charset")
            else None
        ) or "utf-8"
    return final_url, payload.decode(charset, errors="replace")


def resolve_publisher_article_url(
    article: Mapping,
    *,
    allow_network: bool,
    fetcher: Callable[[str], tuple[str, str]] | None = None,
    opener: object | None = None,
    counters: PublisherUrlResolutionCounters | None = None,
) -> PublisherUrlResolution:
    """Recover one publisher article URL without search, script execution, or body storage."""
    stats = counters if counters is not None else PublisherUrlResolutionCounters()
    metadata = _article_metadata(article)
    selected = news_access.choose_article_link(article)
    original_url = valid_http_url(article.get("url") or selected.url)
    aggregator_used = bool(original_url and news_access.is_aggregator_url(original_url))
    source_home_url = valid_http_url(
        metadata.get("rss_source_home_url")
        or metadata.get("rss_source_url")
        or article.get("rss_source_home_url")
        or article.get("rss_source_url")
        or ""
    )

    existing_direct = _normalize_publisher_article_url(
        selected.url,
        source_home_url=source_home_url,
        require_source_match=bool(source_home_url),
    ) if selected.is_direct else ""
    if existing_direct:
        resolution = _publisher_resolution(
            original_url,
            existing_direct,
            "existing_publisher_direct",
            aggregator_used=aggregator_used,
            reason="selected_existing_publisher_direct",
        )
        stats.account(resolution)
        return resolution

    groups: tuple[tuple[str, tuple[object, ...], bool], ...] = (
        (
            "rss_source_url",
            (
                article.get("rss_source_url"),
                metadata.get("rss_source_url"),
                article.get("rss_source_home_url"),
                metadata.get("rss_source_home_url"),
            ),
            True,
        ),
        (
            "rss_orig_link",
            (
                article.get("rss_orig_link"),
                metadata.get("rss_orig_link"),
                article.get("feedburner_orig_link"),
                metadata.get("feedburner_orig_link"),
            ),
            bool(source_home_url),
        ),
        (
            "rss_description_link",
            (
                article.get("rss_description_links"),
                metadata.get("rss_description_links"),
            ),
            True,
        ),
        (
            "rss_content_link",
            (
                article.get("rss_content_links"),
                metadata.get("rss_content_links"),
            ),
            True,
        ),
        (
            "rss_guid_direct",
            (
                article.get("rss_guid"),
                metadata.get("rss_guid"),
                article.get("dc_identifier"),
                metadata.get("dc_identifier"),
            ),
            bool(source_home_url),
        ),
        (
            "rss_atom_link",
            (
                article.get("rss_atom_links"),
                metadata.get("rss_atom_links"),
            ),
            True,
        ),
    )
    for source_kind, values, require_match in groups:
        direct, ambiguity = _unique_publisher_candidate(
            values,
            source_home_url=source_home_url,
            require_source_match=require_match and bool(source_home_url),
        )
        if ambiguity:
            resolution = _publisher_resolution(
                original_url,
                "",
                "unresolved_aggregator",
                aggregator_used=aggregator_used,
                reason=f"{source_kind}_{ambiguity}",
            )
            stats.account(resolution)
            return resolution
        if direct:
            resolution = _publisher_resolution(
                original_url,
                direct,
                source_kind,
                aggregator_used=aggregator_used,
                reason=f"selected_{source_kind}",
            )
            stats.account(resolution)
            return resolution

    if not aggregator_used:
        resolution = _publisher_resolution(
            original_url,
            "",
            "unresolved_aggregator",
            aggregator_used=False,
            reason="no_safe_publisher_article_url",
        )
        stats.account(resolution)
        return resolution
    if not allow_network:
        resolution = _publisher_resolution(
            original_url,
            "",
            "unresolved_aggregator",
            aggregator_used=True,
            reason="network_disabled_and_rss_had_no_direct_url",
        )
        stats.account(resolution)
        return resolution

    try:
        before = stats.aggregator_page_gets
        if fetcher is not None:
            stats.aggregator_page_gets += 1
            final_url, html = fetcher(original_url)
            final_url = valid_http_url(final_url)
            if not final_url:
                raise EditorialError("fixture aggregator final URL is invalid")
            if len(str(html or "").encode("utf-8")) > PUBLISHER_PAGE_MAX_BYTES:
                raise EditorialError("aggregator response is too large")
        else:
            final_url, html = _fetch_aggregator_html(
                original_url, counters=stats, opener=opener
            )
        network_gets = stats.aggregator_page_gets - before
    except (
        EditorialError,
        OSError,
        TimeoutError,
        urllib.error.URLError,
        ValueError,
    ):
        resolution = _publisher_resolution(
            original_url,
            "",
            "unresolved_aggregator",
            aggregator_used=True,
            network_gets=1,
            reason="aggregator_page_unavailable_or_invalid",
        )
        stats.account(resolution)
        return resolution

    redirected = _normalize_publisher_article_url(
        final_url,
        source_home_url=source_home_url,
        require_source_match=bool(source_home_url),
    )
    if redirected:
        resolution = _publisher_resolution(
            original_url,
            redirected,
            "aggregator_redirect",
            aggregator_used=True,
            network_gets=network_gets,
            reason="aggregator_redirected_to_publisher",
        )
        stats.account(resolution)
        return resolution

    parser = _PublisherUrlParser()
    parser.feed(str(html or "")[:PUBLISHER_PAGE_MAX_BYTES])
    for source_kind, values in (
        ("aggregator_canonical", parser.canonical_urls),
        ("aggregator_og_url", parser.og_urls),
    ):
        direct, ambiguity = _unique_publisher_candidate(
            [urljoin(final_url, value) for value in values],
            source_home_url=source_home_url,
            require_source_match=bool(source_home_url),
        )
        if ambiguity:
            resolution = _publisher_resolution(
                original_url,
                "",
                "unresolved_aggregator",
                aggregator_used=True,
                network_gets=network_gets,
                reason=f"{source_kind}_{ambiguity}",
            )
            stats.account(resolution)
            return resolution
        if direct:
            resolution = _publisher_resolution(
                original_url,
                direct,
                source_kind,
                aggregator_used=True,
                network_gets=network_gets,
                reason=f"selected_{source_kind}",
            )
            stats.account(resolution)
            return resolution

    outbound: list[str] = []
    for value in parser.outbound_urls:
        direct = _normalize_publisher_article_url(
            urljoin(final_url, value),
            source_home_url=source_home_url,
            require_source_match=bool(source_home_url),
        )
        if direct and direct not in outbound:
            outbound.append(direct)
    if len(outbound) == 1:
        resolution = _publisher_resolution(
            original_url,
            outbound[0],
            "aggregator_outbound_link",
            aggregator_used=True,
            network_gets=network_gets,
            reason="single_unambiguous_publisher_outbound_link",
        )
        stats.account(resolution)
        return resolution

    reason = (
        "multiple_publisher_outbound_candidates"
        if len(outbound) > 1
        else "aggregator_exposed_no_safe_publisher_url"
    )
    resolution = _publisher_resolution(
        original_url,
        "",
        "unresolved_aggregator",
        aggregator_used=True,
        network_gets=network_gets,
        reason=reason,
    )
    stats.account(resolution)
    return resolution


def _fetch_publisher_html(
    page_url: str,
    *,
    counters: ImageResolutionCounters,
    opener: object | None = None,
) -> tuple[str, str]:
    safe_url = _public_fetch_url(page_url)
    request = urllib.request.Request(
        safe_url,
        headers={
            "User-Agent": "HDEC-Editorial-ImageResolver/1.0 (+anonymous-metadata-only)",
            "Accept": "text/html,application/xhtml+xml;q=0.9",
        },
        method="GET",
    )
    counters.network_page_gets += 1
    response = _open_request(opener or _network_opener(), request, IMAGE_PAGE_TIMEOUT_SECONDS)
    with response:
        status = int(getattr(response, "status", response.getcode()))
        if status < 200 or status >= 400:
            raise EditorialError("publisher metadata response was not successful")
        final_url = _public_fetch_url(response.geturl())
        content_type = str(response.headers.get("Content-Type") or "")
        if content_type.split(";", 1)[0].strip().casefold() not in {
            "text/html", "application/xhtml+xml",
        }:
            raise EditorialError("publisher metadata response is not HTML")
        length = _positive_int(response.headers.get("Content-Length"))
        if length is not None and length > IMAGE_PAGE_MAX_BYTES:
            raise EditorialError("publisher metadata response is too large")
        payload = response.read(IMAGE_PAGE_MAX_BYTES + 1)
        if len(payload) > IMAGE_PAGE_MAX_BYTES:
            raise EditorialError("publisher metadata response is too large")
        charset = (
            response.headers.get_content_charset()
            if hasattr(response.headers, "get_content_charset")
            else None
        ) or "utf-8"
    return final_url, payload.decode(charset, errors="replace")


def _probe_image_mime(
    image_url: str,
    *,
    counters: ImageResolutionCounters,
    opener: object | None = None,
) -> bool:
    try:
        safe_url = _public_fetch_url(image_url)
        request = urllib.request.Request(
            safe_url,
            headers={
                "User-Agent": "HDEC-Editorial-ImageResolver/1.0 (+anonymous-metadata-only)",
                "Accept": "image/*",
            },
            method="HEAD",
        )
        counters.network_image_head_or_range_gets += 1
        response = _open_request(
            opener or _network_opener(), request, IMAGE_PAGE_TIMEOUT_SECONDS
        )
        with response:
            status = int(getattr(response, "status", response.getcode()))
            if status < 200 or status >= 400:
                return False
            _public_fetch_url(response.geturl())
            content_type = str(response.headers.get("Content-Type") or "")
            if not content_type.split(";", 1)[0].strip().casefold().startswith("image/"):
                return False
            length = _positive_int(response.headers.get("Content-Length"))
            return length is None or length > 64
    except (
        EditorialError,
        OSError,
        TimeoutError,
        UnicodeError,
        urllib.error.URLError,
        http.client.HTTPException,
        ValueError,
    ):
        return False


def _download_image_bytes(
    image_url: str,
    *,
    referer_url: str = "",
    opener: object | None = None,
) -> ImageDownload:
    try:
        safe_url = _public_fetch_url(image_url)
        if urlparse(safe_url).scheme != "https":
            raise ImageDownloadError("image_non_https")
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (compatible; HDEC-Editorial-ImageMaterializer/1.0; "
                "+anonymous-preview-only)"
            ),
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        }
        if referer_url:
            try:
                referer = _public_fetch_url(referer_url)
            except EditorialError:
                referer = ""
            if referer:
                headers["Referer"] = referer
        request = urllib.request.Request(safe_url, headers=headers, method="GET")
        response = _open_request(
            opener or _network_opener(), request, IMAGE_DOWNLOAD_TIMEOUT_SECONDS
        )
    except urllib.error.HTTPError as exc:
        raise ImageDownloadError(
            f"image_http_{int(exc.code)}",
            status=int(exc.code),
            content_type=str(exc.headers.get("Content-Type") or ""),
        ) from exc
    except TimeoutError as exc:
        raise ImageDownloadError("image_download_timeout") from exc
    except urllib.error.URLError as exc:
        reason = (
            "image_tls_failure"
            if "CERT" in str(getattr(exc, "reason", exc)).upper()
            else "image_download_failed"
        )
        raise ImageDownloadError(reason) from exc
    except OSError as exc:
        raise ImageDownloadError("image_download_failed") from exc
    except (EditorialError, UnicodeError, http.client.HTTPException, ValueError) as exc:
        raise ImageDownloadError("image_candidate_invalid_url") from exc

    with response:
        status = int(getattr(response, "status", response.getcode()))
        if status < 200 or status >= 400:
            raise ImageDownloadError(f"image_http_{status}", status=status)
        try:
            final_url = _public_fetch_url(response.geturl())
        except EditorialError as exc:
            raise ImageDownloadError(
                "image_redirect_rejected",
                status=status,
                content_type=str(response.headers.get("Content-Type") or ""),
            ) from exc
        if urlparse(final_url).scheme != "https":
            raise ImageDownloadError("image_redirect_rejected", status=status)
        content_type = str(response.headers.get("Content-Type") or "")
        length = _positive_int(response.headers.get("Content-Length"))
        if length is not None and length > IMAGE_DOWNLOAD_MAX_BYTES:
            raise ImageDownloadError(
                "image_oversized",
                status=status,
                content_type=content_type,
                byte_size=length,
            )
        payload = response.read(IMAGE_DOWNLOAD_MAX_BYTES + 1)
    return ImageDownload(
        status=status,
        content_type=content_type,
        payload=payload,
        final_url=final_url,
    )


def _coerce_image_download(value: object) -> ImageDownload:
    if isinstance(value, ImageDownload):
        return value
    if isinstance(value, tuple) and len(value) >= 3:
        return ImageDownload(
            status=int(value[0]),
            content_type=str(value[1]),
            payload=bytes(value[2]),
            final_url=str(value[3]) if len(value) > 3 else "",
        )
    raise ImageDownloadError("image_materialization_failed")


def _image_magic_extension(payload: bytes, content_type: str) -> tuple[str, str]:
    media_type = content_type.split(";", 1)[0].strip().casefold()
    if not media_type.startswith("image/"):
        return "", "image_invalid_content_type"
    if media_type == "image/svg+xml":
        return "", "image_svg_rejected"
    if not payload:
        return "", "image_empty_body"
    if len(payload) > IMAGE_DOWNLOAD_MAX_BYTES:
        return "", "image_oversized"

    # Some publisher CDNs return a valid raster image under an incorrect
    # image/* subtype. Keep the image/* boundary, but derive the canonical
    # local extension from supported magic bytes rather than the MIME subtype.
    if payload.startswith(b"\xff\xd8\xff"):
        return ".jpg", ""
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png", ""
    if len(payload) >= 12 and payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
        return ".webp", ""
    if (
        len(payload) >= 12
        and payload[4:8] == b"ftyp"
        and b"avif" in payload[8:32]
    ):
        return ".avif", ""
    return "", "image_magic_mismatch"


def _marker_present(text: str, marker: str) -> bool:
    haystack = text.casefold()
    variants = {marker.casefold(), marker.replace("-", "_").casefold()}
    if "-" in marker:
        variants.add(marker.replace("-", "").casefold())
    for variant in variants:
        pattern = rf"(?<![a-z0-9]){re.escape(variant)}(?![a-z0-9])"
        if re.search(pattern, haystack):
            return True
    return False


def _image_text_signals(
    remote_url: str,
    candidate: ImageCandidateOption,
) -> tuple[str, ...]:
    parsed = urlparse(remote_url)
    text = " ".join(
        item
        for item in (
            unquote(parsed.path or ""),
            unquote(parsed.query or ""),
            candidate.reason,
            candidate.context,
        )
        if item
    ).casefold()
    signals: list[str] = []
    for marker in _IMAGE_DEFAULT_TEXT_MARKERS:
        if _marker_present(text, marker):
            signals.append(f"default_marker:{marker}")
    for marker in _IMAGE_LOGO_TEXT_MARKERS:
        if _marker_present(text, marker):
            signals.append(f"logo_marker:{marker}")
    return tuple(dict.fromkeys(signals))


def _flatten_for_quality(image: Image.Image) -> Image.Image:
    if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        background.alpha_composite(rgba)
        return background.convert("RGB")
    return image.convert("RGB")


def _decoded_image_quality_signals(payload: bytes) -> tuple[tuple[str, ...], int, int]:
    if Image is None:
        raise ImageDownloadError("image_dependency_unavailable")
    try:
        with Image.open(BytesIO(payload)) as decoded:
            decoded.load()
            width, height = decoded.size
            mode = decoded.mode
            alpha_ratio = 0.0
            if mode in {"RGBA", "LA"} or "transparency" in decoded.info:
                alpha = decoded.convert("RGBA").getchannel("A").resize((64, 64))
                alpha_pixels = list(alpha.getdata())
                alpha_ratio = (
                    sum(1 for value in alpha_pixels if value < 16) / len(alpha_pixels)
                    if alpha_pixels
                    else 0.0
                )
            sample = _flatten_for_quality(decoded)
    except (OSError, UnidentifiedImageError, ValueError) as exc:
        raise ImageDownloadError("image_decode_failed") from exc

    sample.thumbnail((96, 96))
    rgb = sample.convert("RGB")
    pixels = list(rgb.getdata())
    if not pixels:
        raise ImageDownloadError("image_decode_failed")
    counts = Counter(pixels)
    dominant_color, dominant_count = counts.most_common(1)[0]
    dominant_ratio = dominant_count / len(pixels)
    quantized = rgb.quantize(colors=64, method=Image.Quantize.MEDIANCUT)
    colors = quantized.getcolors(maxcolors=4096) or []
    significant_colors = sum(1 for count, _color in colors if count / len(pixels) >= 0.005)

    def color_distance(first: tuple[int, int, int], second: tuple[int, int, int]) -> int:
        return sum(abs(first[index] - second[index]) for index in range(3))

    effective_content_ratio = (
        sum(1 for pixel in pixels if color_distance(pixel, dominant_color) > 35)
        / len(pixels)
    )
    aspect_ratio = width / height if height else 0.0

    signals: list[str] = []
    if width < 320 or height < 120 or (aspect_ratio >= 2.6 and height <= 180):
        signals.append("logo_like_dimensions")
    if alpha_ratio >= 0.35:
        signals.append("logo_like_transparency")
    if dominant_ratio >= 0.45 and significant_colors <= 10:
        signals.append("logo_like_flat_graphic")
    if effective_content_ratio <= 0.42 and dominant_ratio >= 0.35:
        signals.append("small_effective_content_area")
    if significant_colors <= 4 and dominant_ratio >= 0.35:
        signals.append("very_low_visual_variation")
    return tuple(signals), width, height


def _article_quality_key(article: EditorialArticle) -> str:
    return (
        _normalize_publisher_article_url(article.publisher_article_url)
        or _normalize_publisher_article_url(article.selected_url)
        or _canonical_title(article.title)
    )


def assess_image_quality(
    payload: bytes,
    *,
    remote_url: str,
    candidate: ImageCandidateOption,
    article: EditorialArticle,
    duplicate_article_key: str = "",
) -> ImageQualityAssessment:
    text_signals = _image_text_signals(remote_url, candidate)
    pixel_signals, _width, _height = _decoded_image_quality_signals(payload)
    signals = tuple(dict.fromkeys((*text_signals, *pixel_signals)))
    signal_set = set(signals)
    has_default_marker = any(signal.startswith("default_marker:") for signal in signals)
    has_logo_marker = any(signal.startswith("logo_marker:") for signal in signals)
    # A digest can only be registered after a previous article accepted it:
    # the current article has not materialized an asset yet. Therefore an
    # existing digest is sufficient evidence of cross-article reuse, even
    # when fixture or feed metadata yields the same canonical article key.
    has_duplicate_default = (
        bool(duplicate_article_key)
        and (
            has_default_marker
            or {
                "logo_like_dimensions",
                "logo_like_flat_graphic",
                "small_effective_content_area",
            }.issubset(signal_set)
        )
    )
    if has_duplicate_default:
        return ImageQualityAssessment(False, "duplicate_publisher_default", signals)
    if has_default_marker:
        return ImageQualityAssessment(False, "site_default_image", signals)
    if has_logo_marker:
        return ImageQualityAssessment(False, "publisher_logo_marker", signals)
    # Transparency is not rejected on its own. Reject only when it is
    # accompanied by a second strong logo-layout signal.
    if (
        "logo_like_transparency" in signal_set
        and (
            "logo_like_flat_graphic" in signal_set
            or "small_effective_content_area" in signal_set
            or "very_low_visual_variation" in signal_set
        )
    ):
        return ImageQualityAssessment(False, "logo_like_transparency", signals)

    # Small logo-shaped canvases with little effective visual content are
    # logo-like even when anti-aliasing prevents flat-color thresholds from
    # firing. A single dimension signal remains insufficient on its own.
    if {
        "logo_like_dimensions",
        "small_effective_content_area",
    } <= signal_set:
        return ImageQualityAssessment(False, "logo_like_flat_graphic", signals)

    # A logo can also be rendered on a full-size social/OG canvas, so small
    # dimensions must not be mandatory. Require flatness, limited effective
    # content, and either low variation or logo-like dimensions.
    if (
        "logo_like_flat_graphic" in signal_set
        and "small_effective_content_area" in signal_set
        and (
            "logo_like_dimensions" in signal_set
            or "very_low_visual_variation" in signal_set
        )
    ):
        return ImageQualityAssessment(False, "logo_like_flat_graphic", signals)
    return ImageQualityAssessment(True, "image_quality_passed", signals)


def _preview_image_relative_src(asset_path: Path, html_dir: Path) -> str:
    return os.path.relpath(asset_path, start=html_dir).replace(os.sep, "/")


def _same_site(first_url: str, second_url: str) -> bool:
    try:
        first = (urlparse(first_url).hostname or "").casefold().split(".")
        second = (urlparse(second_url).hostname or "").casefold().split(".")
    except ValueError:
        return False
    return len(first) >= 2 and len(second) >= 2 and first[-2:] == second[-2:]


class _PublisherImageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: list[tuple[str, str]] = []
        self.image_src: list[str] = []
        self.body_images: list[dict[str, str]] = []
        self.jsonld_blocks: list[str] = []
        self._jsonld_depth = 0
        self._jsonld_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        values = {str(key).casefold(): str(value or "") for key, value in attrs}
        lowered = tag.casefold()
        if lowered == "meta":
            key = (values.get("property") or values.get("name") or "").casefold()
            content = values.get("content", "").strip()
            if key and content:
                self.meta.append((key, content))
        elif lowered == "link":
            rel = set(values.get("rel", "").casefold().split())
            if "image_src" in rel and values.get("href"):
                self.image_src.append(values["href"])
        elif lowered == "img" and len(self.body_images) < 16:
            source = (
                values.get("src")
                or values.get("data-src")
                or values.get("data-original")
                or ""
            )
            if not source and values.get("srcset"):
                source = values["srcset"].split(",", 1)[0].strip().split(" ", 1)[0]
            if source:
                self.body_images.append(
                    {
                        "url": source,
                        "width": values.get("width", ""),
                        "height": values.get("height", ""),
                        "alt": values.get("alt", ""),
                    }
                )
        elif (
            lowered == "script"
            and values.get("type", "").split(";", 1)[0].strip().casefold()
            == "application/ld+json"
        ):
            self._jsonld_depth = 1
            self._jsonld_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "script" and self._jsonld_depth:
            block = "".join(self._jsonld_parts).strip()
            if block:
                self.jsonld_blocks.append(block)
            self._jsonld_depth = 0
            self._jsonld_parts = []

    def handle_data(self, data: str) -> None:
        if self._jsonld_depth:
            self._jsonld_parts.append(data)


def _jsonld_image_values(value: object, *, depth: int = 0) -> list[object]:
    if depth > 8:
        return []
    if isinstance(value, list):
        output: list[object] = []
        for item in value:
            output.extend(_jsonld_image_values(item, depth=depth + 1))
        return output
    if isinstance(value, Mapping):
        output = []
        for key, item in value.items():
            lowered = str(key).casefold()
            if lowered == "image":
                if isinstance(item, Mapping):
                    output.append(
                        {
                            "url": item.get("contentUrl") or item.get("url") or "",
                            "width": item.get("width"),
                            "height": item.get("height"),
                        }
                    )
                else:
                    output.extend(_jsonld_image_values(item, depth=depth + 1))
            elif lowered not in {"logo", "author"}:
                output.extend(_jsonld_image_values(item, depth=depth + 1))
        return output
    return [value] if isinstance(value, str) else []


def parse_publisher_image_candidates(html: str, page_url: str) -> list[_ImageCandidate]:
    parser = _PublisherImageParser()
    parser.feed(str(html or "")[:IMAGE_PAGE_MAX_BYTES])
    meta: dict[str, list[str]] = {}
    for key, value in parser.meta:
        meta.setdefault(key, []).append(value)

    og_width = _positive_int((meta.get("og:image:width") or [None])[0])
    og_height = _positive_int((meta.get("og:image:height") or [None])[0])
    og_type = str((meta.get("og:image:type") or [""])[0])
    output: list[_ImageCandidate] = []
    for value in (meta.get("og:image:secure_url") or []) + (meta.get("og:image") or []):
        output.append(_ImageCandidate(value, "og_image", og_width, og_height, og_type))
    for value in (
        (meta.get("twitter:image") or [])
        + (meta.get("twitter:image:src") or [])
    ):
        output.append(_ImageCandidate(value, "twitter_image"))
    for block in parser.jsonld_blocks:
        try:
            payload = json.loads(block)
        except (TypeError, ValueError):
            continue
        for value in _jsonld_image_values(payload):
            output.extend(_mapping_candidates(value, "jsonld_image"))
    for value in parser.image_src:
        output.append(_ImageCandidate(value, "image_src"))
    for value in parser.body_images:
        output.extend(_mapping_candidates(value, "body_image"))
    return output


def _candidate_resolution(
    candidate: _ImageCandidate,
    *,
    page_url: str,
    used_urls: set[str],
    allow_network: bool,
    counters: ImageResolutionCounters,
    image_probe: Callable[[str], bool] | None,
    opener: object | None,
) -> ImageResolution | None:
    url = _safe_image_url(
        candidate.url,
        base_url=page_url,
        content_type=" ".join(
            item for item in (candidate.content_type, candidate.context) if item
        ),
    )
    if not url or url in used_urls:
        return None
    if not _geometry_is_safe(candidate.width, candidate.height):
        return None
    if candidate.source_kind == "body_image" and not _same_site(page_url, url):
        return None
    mime = candidate.content_type.split(";", 1)[0].strip().casefold()
    media_confirmed = mime.startswith("image/") or _has_image_extension(url)
    if not media_confirmed:
        if not allow_network:
            return None
        try:
            media_confirmed = (
                image_probe(url)
                if image_probe is not None
                else _probe_image_mime(url, counters=counters, opener=opener)
            )
        except (
            EditorialError,
            OSError,
            TimeoutError,
            UnicodeError,
            urllib.error.URLError,
            http.client.HTTPException,
            ValueError,
        ):
            media_confirmed = False
    if not media_confirmed:
        return None
    used_urls.add(url)
    return ImageResolution(
        url=url,
        source_kind=candidate.source_kind,
        source_page_url=page_url,
        width=candidate.width,
        height=candidate.height,
        fallback_used=False,
        reason=f"selected_{candidate.source_kind}",
        context=candidate.context,
    )


def _candidate_option(resolution: ImageResolution) -> ImageCandidateOption:
    return ImageCandidateOption(
        url=resolution.url,
        source_kind=resolution.source_kind,
        source_page_url=resolution.source_page_url,
        width=resolution.width,
        height=resolution.height,
        reason=resolution.reason,
        context=resolution.context,
    )


def resolve_article_image(
    article: Mapping,
    *,
    allow_network: bool,
    used_urls: set[str] | None = None,
    counters: ImageResolutionCounters | None = None,
    page_fetcher: Callable[[str], tuple[str, str]] | None = None,
    image_probe: Callable[[str], bool] | None = None,
    opener: object | None = None,
) -> ImageResolution:
    """Resolve one representative image without storing a page body or image bytes."""
    selected_url = valid_http_url(
        article.get("selected_url") or news_access.choose_article_link(article).url
    )
    used = used_urls if used_urls is not None else set()
    stats = counters if counters is not None else ImageResolutionCounters()
    rejection_reason = "no_safe_feed_image"
    candidates: list[ImageCandidateOption] = []

    for candidate in _feed_image_candidates(article):
        resolution = _candidate_resolution(
            candidate,
            page_url=selected_url,
            used_urls=used,
            allow_network=allow_network,
            counters=stats,
            image_probe=image_probe,
            opener=opener,
        )
        if resolution is not None:
            candidates.append(_candidate_option(resolution))

    if (
        allow_network
        and selected_url
        and not news_access.is_aggregator_url(selected_url)
        and (not candidates or page_fetcher is not None)
    ):
        try:
            if page_fetcher is not None:
                stats.network_page_gets += 1
                final_page_url, html = page_fetcher(selected_url)
                final_page_url = valid_http_url(final_page_url)
                if not final_page_url:
                    raise EditorialError("fixture page URL is invalid")
            else:
                final_page_url, html = _fetch_publisher_html(
                    selected_url, counters=stats, opener=opener
                )
            for candidate in parse_publisher_image_candidates(html, final_page_url):
                resolution = _candidate_resolution(
                    candidate,
                    page_url=final_page_url,
                    used_urls=used,
                    allow_network=True,
                    counters=stats,
                    image_probe=image_probe,
                    opener=opener,
                )
                if resolution is not None:
                    candidates.append(_candidate_option(resolution))
            rejection_reason = "publisher_page_had_no_safe_image"
        except (
            EditorialError,
            OSError,
            TimeoutError,
            urllib.error.URLError,
            ValueError,
        ):
            rejection_reason = "publisher_page_unavailable"
    elif allow_network and selected_url:
        rejection_reason = "aggregator_page_not_used_for_image"
    elif not allow_network:
        rejection_reason = "network_disabled_no_safe_feed_image"

    if candidates:
        first = candidates[0]
        resolution = ImageResolution(
            url=first.url,
            source_kind=first.source_kind,
            source_page_url=first.source_page_url,
            width=first.width,
            height=first.height,
            fallback_used=False,
            reason=first.reason,
            candidates=tuple(candidates),
        )
        stats.account(resolution)
        return resolution

    fallback = ImageResolution(
        source_page_url=selected_url,
        reason=rejection_reason,
    )
    stats.account(fallback)
    return fallback


def normalize_articles(
    raw_articles: Iterable[Mapping],
    coverage: CoverageWindow,
    *,
    limit: int,
    resolve_images: bool = True,
    allow_image_network: bool = False,
    image_counters: ImageResolutionCounters | None = None,
    image_page_fetcher: Callable[[str], tuple[str, str]] | None = None,
    image_probe: Callable[[str], bool] | None = None,
    image_opener: object | None = None,
    publisher_counters: PublisherUrlResolutionCounters | None = None,
    publisher_fetcher: Callable[[str], tuple[str, str]] | None = None,
    publisher_opener: object | None = None,
    selection_audit: SelectionAuditCounters | None = None,
    selection_mode: str = SELECTION_MODE_LEGACY,
) -> list[EditorialArticle]:
    if selection_mode not in _SELECTION_MODES:
        raise EditorialError(f"unsupported selection mode: {selection_mode}")
    if selection_mode == SELECTION_MODE_LEGACY:
        selected_rows = _select_legacy_article_rows(
            raw_articles,
            coverage,
            limit=limit,
        )
    else:
        candidates: list[_ArticleCandidate] = []
        audit = selection_audit
        for raw in raw_articles:
            if not isinstance(raw, Mapping):
                continue
            try:
                published = parse_published_at(raw.get("published_at"))
            except EditorialError:
                continue
            if not (coverage.start <= published <= coverage.end):
                continue
            provider_tokens = _provider_tokens(raw)
            if audit is not None:
                if "naver_news_api" in provider_tokens:
                    audit.naver_articles_in_coverage += 1
                if "google_news_rss" in provider_tokens:
                    audit.google_articles_in_coverage += 1
            candidate = _build_article_candidate(raw, coverage)
            if candidate is not None:
                candidates.append(candidate)

        deduped = _deduplicate_article_candidates(candidates)
        if audit is not None:
            audit.naver_articles_after_dedup = sum(
                1 for candidate in deduped
                if "naver_news_api" in candidate.provider_tokens
            )
            for candidate in deduped:
                qualified = candidate.relevance_score >= SELECTION_RELEVANCE_FLOOR
                if "naver_news_api" in candidate.provider_tokens and qualified:
                    audit.naver_articles_relevance_qualified += 1
                if "google_news_rss" in candidate.provider_tokens and qualified:
                    audit.google_articles_relevance_qualified += 1
                if candidate.is_direct and qualified:
                    audit.direct_candidates_before_selection += 1
                elif candidate.is_aggregator and qualified:
                    audit.aggregator_candidates_before_selection += 1
                if candidate.is_direct and not qualified:
                    audit.direct_candidates_rejected_below_relevance_floor += 1

        selected_candidates = _select_article_candidates(
            deduped,
            limit=limit,
            audit=audit,
        )
        selected_rows = [
            (candidate.article, candidate.raw) for candidate in selected_candidates
        ]
    if not resolve_images:
        return [article for article, _raw in selected_rows]

    counters = image_counters if image_counters is not None else ImageResolutionCounters()
    publisher_stats = (
        publisher_counters
        if publisher_counters is not None
        else PublisherUrlResolutionCounters()
    )
    used_image_urls: set[str] = set()
    enriched: list[EditorialArticle] = []
    for article, raw in selected_rows:
        publisher = resolve_publisher_article_url(
            raw,
            allow_network=allow_image_network,
            fetcher=publisher_fetcher,
            opener=publisher_opener,
            counters=publisher_stats,
        )
        original_url = valid_http_url(raw.get("url")) or article.selected_url
        if publisher.resolved_url:
            article = replace(
                article,
                selected_url=publisher.resolved_url,
                link_kind=news_access.LINK_KIND_PUBLISHER_DIRECT,
                link_label=news_access.LINK_LABEL_PUBLISHER_DIRECT,
            )
        image_input = dict(raw)
        image_input["selected_url"] = article.selected_url
        resolution = resolve_article_image(
            image_input,
            allow_network=allow_image_network,
            used_urls=used_image_urls,
            counters=counters,
            page_fetcher=image_page_fetcher,
            image_probe=image_probe,
            opener=image_opener,
        )
        enriched.append(
            replace(
                article,
                original_article_url=original_url,
                publisher_article_url=publisher.resolved_url,
                publisher_url_source_kind=publisher.source_kind,
                publisher_url_reason=publisher.reason,
                image_url=resolution.url,
                image_source_kind=resolution.source_kind,
                image_source_page_url=resolution.source_page_url,
                image_width=resolution.width,
                image_height=resolution.height,
                image_fallback_used=resolution.fallback_used,
                image_reason=resolution.reason,
                image_remote_url=resolution.url,
                image_materialization_reason=(
                    "remote_candidate_selected"
                    if not resolution.fallback_used
                    else resolution.reason
                ),
                image_candidates=resolution.candidates,
            )
        )
    return enriched


def _article_image_candidates(
    article: EditorialArticle,
) -> tuple[ImageCandidateOption, ...]:
    output: list[ImageCandidateOption] = []
    seen: set[str] = set()
    for candidate in article.image_candidates:
        url = normalize_image_candidate_url(
            candidate.url,
            base_url=candidate.source_page_url,
        )
        seen_key = url or (
            "invalid:"
            + hashlib.sha256(str(candidate.url or "").encode("utf-8")).hexdigest()
        )
        if seen_key in seen:
            continue
        seen.add(seen_key)
        if not url:
            output.append(candidate)
            continue
        output.append(replace(candidate, url=url))
    fallback_url = normalize_image_candidate_url(article.image_remote_url or article.image_url)
    if fallback_url and fallback_url not in seen:
        output.append(
            ImageCandidateOption(
                url=fallback_url,
                source_kind=article.image_source_kind,
                source_page_url=article.image_source_page_url,
                width=article.image_width,
                height=article.image_height,
                reason=article.image_reason,
            )
        )
    return tuple(output)


def materialize_preview_images(
    articles: Iterable[EditorialArticle],
    preview_root: Path,
    *,
    html_dir: Path | None = None,
    downloader: Callable[..., ImageDownload] | None = None,
    opener: object | None = None,
) -> tuple[list[EditorialArticle], ImageMaterializationCounters]:
    """Download browser-loadable image bytes into a bounded /tmp preview bundle."""
    output_root = preview_root.resolve()
    system_tmp = Path("/tmp").resolve()
    if output_root == system_tmp or system_tmp not in output_root.parents:
        raise EditorialError("image preview output must be a child of /tmp")
    if output_root == config.BASE_DIR or config.BASE_DIR in output_root.parents:
        raise EditorialError("image preview output must be outside repository")
    target_html_dir = (html_dir or (output_root / "daily")).resolve()
    assets_dir = output_root / "assets" / "images"
    if output_root not in assets_dir.resolve().parents:
        raise EditorialError("image asset directory escaped preview root")

    counters = ImageMaterializationCounters()
    materialized: list[EditorialArticle] = []
    assets_by_digest: dict[str, Path] = {}
    article_key_by_digest: dict[str, str] = {}
    image_downloader = downloader or _download_image_bytes
    for article_index, article in enumerate(articles):
        candidates = _article_image_candidates(article)
        if not candidates:
            counters.images_from_fallback += 1
            materialized.append(
                replace(
                    article,
                    image_url="",
                    image_remote_url="",
                    image_fallback_used=True,
                    image_download_status="not_attempted",
                    image_materialization_reason=article.image_reason,
                )
            )
            continue

        counters.image_urls_resolved += 1
        counters.image_candidates_discovered += len(candidates)
        attempts: list[ImageCandidateAttempt] = []
        selected_article: EditorialArticle | None = None
        last_failure = ImageDownloadError("image_materialization_failed")
        total_candidate_bytes = 0
        quality_rejected_for_article = False
        attempt_candidates = candidates[:IMAGE_DOWNLOAD_MAX_ATTEMPTS_PER_ARTICLE]
        for attempt_index, candidate in enumerate(attempt_candidates, start=1):
            remote_url = normalize_image_candidate_url(
                candidate.url,
                base_url=candidate.source_page_url,
            )
            counters.image_candidates_attempted += 1
            counters.image_download_attempts += 1
            if article_index == 0:
                counters.headline_image_candidates_attempted += 1
            try:
                if not remote_url:
                    raise ImageDownloadError("image_candidate_invalid_url")
                download = _coerce_image_download(
                    image_downloader(
                        remote_url,
                        referer_url=(
                            article.publisher_article_url or article.selected_url
                        ),
                        opener=opener,
                    )
                )
                total_candidate_bytes += len(download.payload)
                if (
                    total_candidate_bytes
                    > IMAGE_DOWNLOAD_MAX_TOTAL_BYTES_PER_ARTICLE
                ):
                    raise ImageDownloadError(
                        "image_total_bytes_exceeded",
                        status=download.status,
                        content_type=download.content_type,
                        byte_size=len(download.payload),
                    )
                if download.status < 200 or download.status >= 400:
                    raise ImageDownloadError(
                        f"image_http_{download.status}",
                        status=download.status,
                        content_type=download.content_type,
                        byte_size=len(download.payload),
                    )
                extension, rejection_reason = _image_magic_extension(
                    download.payload, download.content_type
                )
                if rejection_reason:
                    raise ImageDownloadError(
                        rejection_reason,
                        status=download.status,
                        content_type=download.content_type,
                        byte_size=len(download.payload),
                    )
                digest = hashlib.sha256(download.payload).hexdigest()
                counters.image_downloads_succeeded += 1
                counters.image_bytes_validated += len(download.payload)
                counters.image_quality_checks += 1
                assessment = assess_image_quality(
                    download.payload,
                    remote_url=remote_url,
                    candidate=candidate,
                    article=article,
                    duplicate_article_key=article_key_by_digest.get(digest, ""),
                )
                if not assessment.accepted:
                    quality_rejected_for_article = True
                    last_failure = ImageDownloadError(
                        assessment.reason,
                        status=download.status,
                        content_type=download.content_type,
                        byte_size=len(download.payload),
                    )
                    counters.image_quality_rejections += 1
                    counters.image_candidate_failures += 1
                    if assessment.reason in {
                        "publisher_logo_marker",
                        "favicon_or_brand_asset",
                        "logo_like_dimensions",
                        "logo_like_transparency",
                        "logo_like_flat_graphic",
                        "image_not_article_representative",
                    }:
                        counters.publisher_logo_candidates_rejected += 1
                    if assessment.reason in {
                        "site_default_image",
                        "duplicate_publisher_default",
                    }:
                        counters.publisher_default_images_rejected += 1
                    attempts.append(
                        ImageCandidateAttempt(
                            source_kind=candidate.source_kind,
                            host=_url_host(remote_url),
                            status="rejected",
                            reason=assessment.reason,
                            content_type=download.content_type.split(";", 1)[0].strip(),
                            byte_size=len(download.payload),
                            byte_validation_status="passed",
                            quality_accepted=False,
                            quality_rejection_reason=assessment.reason,
                            logo_signals=assessment.logo_signals,
                        )
                    )
                    continue
                asset_path = assets_by_digest.get(digest)
                reused = asset_path is not None
                if asset_path is None:
                    asset_path = assets_dir / (
                        f"{digest[:PREVIEW_IMAGE_ASSET_DIGEST_CHARS]}{extension}"
                    )
                    if output_root not in asset_path.resolve().parents:
                        raise ImageDownloadError("image_materialization_failed")
                    atomic_write_bytes(asset_path, download.payload)
                    assets_by_digest[digest] = asset_path
                    article_key_by_digest[digest] = _article_quality_key(article)
                    counters.image_assets_materialized += 1
                local_src = _preview_image_relative_src(asset_path, target_html_dir)
                counters.account_source(candidate.source_kind)
                if attempt_index > 1:
                    counters.images_recovered_from_secondary_candidate += 1
                    if quality_rejected_for_article:
                        counters.images_recovered_after_quality_rejection += 1
                if article_index == 0:
                    counters.headline_image_recovered = 1
                attempts.append(
                    ImageCandidateAttempt(
                        source_kind=candidate.source_kind,
                        host=_url_host(remote_url),
                        status="success",
                        reason=(
                            "duplicate_asset_reused"
                            if reused
                            else "image_materialized"
                        ),
                        content_type=download.content_type.split(";", 1)[0].strip(),
                        byte_size=len(download.payload),
                        local_asset=asset_path.name,
                        duplicate_asset_reused=reused,
                        selected=True,
                        byte_validation_status="passed",
                        quality_accepted=True,
                        logo_signals=assessment.logo_signals,
                    )
                )
                selected_article = replace(
                    article,
                    image_url=local_src,
                    image_remote_url=remote_url,
                    image_source_kind=candidate.source_kind,
                    image_source_page_url=candidate.source_page_url,
                    image_width=candidate.width,
                    image_height=candidate.height,
                    image_fallback_used=False,
                    image_reason=candidate.reason or f"selected_{candidate.source_kind}",
                    image_download_status="success",
                    image_download_content_type=download.content_type.split(";", 1)[0].strip(),
                    image_download_bytes=len(download.payload),
                    image_local_asset=asset_path.name,
                    image_local_src=local_src,
                    image_duplicate_asset_reused=reused,
                    image_materialization_reason=(
                        "duplicate_asset_reused" if reused else "image_materialized"
                    ),
                    image_quality_accepted=True,
                    image_quality_reason=assessment.reason,
                    image_quality_signals=assessment.logo_signals,
                    image_candidate_attempts=tuple(attempts),
                )
                break
            except (
                ImageDownloadError,
                EditorialError,
                OSError,
                TimeoutError,
                UnicodeError,
                urllib.error.URLError,
                http.client.HTTPException,
                ValueError,
            ) as exc:
                if isinstance(exc, ImageDownloadError):
                    pass
                elif isinstance(
                    exc,
                    (UnicodeError, http.client.HTTPException, ValueError),
                ):
                    exc = ImageDownloadError("image_candidate_invalid_url")
                elif isinstance(exc, TimeoutError):
                    exc = ImageDownloadError("image_download_timeout")
                else:
                    exc = ImageDownloadError("image_materialization_failed")
                last_failure = exc
                counters.image_downloads_failed += 1
                counters.image_candidate_failures += 1
                attempts.append(
                    ImageCandidateAttempt(
                        source_kind=candidate.source_kind,
                        host=_url_host(remote_url),
                        status="failed",
                        reason=exc.reason,
                        content_type=exc.content_type.split(";", 1)[0].strip(),
                        byte_size=exc.byte_size,
                        byte_validation_status=(
                            "failed"
                            if exc.reason
                            in {
                                "image_invalid_content_type",
                                "image_svg_rejected",
                                "image_empty_body",
                                "image_oversized",
                                "image_magic_mismatch",
                                "image_decode_failed",
                            }
                            else ""
                        ),
                    )
                )

        if selected_article is not None:
            materialized.append(selected_article)
            continue

        counters.images_from_fallback += 1
        if quality_rejected_for_article:
            counters.images_fallback_after_quality_rejection += 1
        fallback_remote_url = next(
            (
                normalized
                for normalized in (
                    normalize_image_candidate_url(
                        candidate.url,
                        base_url=candidate.source_page_url,
                    )
                    for candidate in candidates
                )
                if normalized
            ),
            "",
        )
        materialized.append(
            replace(
                article,
                image_url="",
                image_remote_url=fallback_remote_url,
                image_fallback_used=True,
                image_reason=last_failure.reason,
                image_download_status="failed",
                image_download_content_type=last_failure.content_type.split(";", 1)[0].strip(),
                image_download_bytes=last_failure.byte_size,
                image_materialization_reason=last_failure.reason,
                image_quality_accepted=False,
                image_quality_reason=last_failure.reason,
                image_candidate_attempts=tuple(attempts),
            )
        )
    return materialized, counters


def _template(name: str) -> str:
    return (config.TEMPLATES_DIR / name).read_text(encoding="utf-8")


def _fill(template: str, values: Mapping[str, str]) -> str:
    output = template
    for key, value in values.items():
        output = output.replace("{{" + key + "}}", value)
    unresolved = sorted(set(re.findall(r"\{\{[A-Z0-9_]+\}\}", output)))
    if unresolved:
        raise EditorialError(f"unresolved template slots: {', '.join(unresolved)}")
    return output


def _external_anchor(article: EditorialArticle, *, class_name: str = "link") -> str:
    return (
        f'<a class="{escape(class_name, quote=True)}" '
        f'data-link-kind="{escape(article.link_kind, quote=True)}" '
        f'href="{escape(article.selected_url, quote=True)}" target="_blank" '
        f'rel="noopener noreferrer">{escape(article.link_label)}</a>'
    )


def _article_source_anchor(
    article: EditorialArticle, *, class_name: str = "link"
) -> str:
    return (
        f'<a class="{escape(class_name, quote=True)}" '
        f'data-link-kind="{escape(article.link_kind, quote=True)}" '
        f'href="{escape(article.selected_url, quote=True)}" target="_blank" '
        f'rel="noopener noreferrer">{escape(article.source)}'
        f'<span class="dt">{escape(article.published_label)} · '
        f"{escape(article.link_label)}</span></a>"
    )


def _reference_image(article: EditorialArticle, *, hero: bool = False) -> str:
    fallback = (
        "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' "
        "viewBox='0 0 640 260'%3E%3Crect width='640' height='260' "
        "fill='%23002C5F'/%3E%3Ccircle cx='520' cy='30' r='170' "
        "fill='%23004B93'/%3E%3Ccircle cx='80' cy='250' r='150' "
        "fill='%230D9488' fill-opacity='.55'/%3E%3C/svg%3E"
    )
    if article.image_url:
        source = escape(article.image_url, quote=True)
    else:
        source = fallback
    if hero:
        return (
            f'<img src="{source}" alt="" aria-hidden="true" '
            'style="position:absolute;inset:0;width:100%;height:100%;object-fit:cover;'
            'object-position:center;z-index:0;">'
        )
    return (
        f'<img src="{source}" alt="{escape(article.title, quote=True)}" '
        'style="width:100%;height:100%;object-fit:cover;display:block;">'
    )


def _category_style(category: str) -> str:
    return {
        "투자·산업": "--cat:var(--c-invest);--tint:#F7F0E2",
        "기업동향": "--cat:var(--c-corp);--tint:#EBF6F4",
        "기술정보": "--cat:var(--c-tech);--tint:#F1F1FD",
    }.get(category, "--cat:var(--c-tech);--tint:#F1F1FD")


def _daily_summary_html(article: EditorialArticle) -> str:
    if article.summary_html:
        return sanitize_editorial_inline_html(article.summary_html)
    return escape(article.summary)


def _daily_headline(article: EditorialArticle) -> str:
    return (
        '<section class="hero" data-role="headline" style="position:relative;'
        'overflow:hidden;border-radius:22px;background:linear-gradient(125deg,'
        '#002C5F 0%,#004B93 58%,#0E63B8 100%);color:#fff;padding:34px 30px 30px;">'
        f"{_reference_image(article, hero=True)}"
        '<div style="position:absolute;inset:0;background:linear-gradient(135deg,'
        'rgba(10,20,60,.82) 0%,rgba(6,48,40,.75) 100%);z-index:1;"></div>'
        '<h2 style="position:relative;z-index:2;margin:0;font-size:31px;font-weight:800;'
        'letter-spacing:-.035em;line-height:1.3;color:#fff;">'
        f"{escape(article.title)}</h2>"
        '<div class="hero-rule" style="position:relative;z-index:2;height:1px;'
        'background:rgba(255,255,255,.35);margin:22px 0 12px;max-width:340px;"></div>'
        '<div class="hero-foot" style="position:relative;z-index:2;font-size:13px;'
        'font-weight:600;color:rgba(255,255,255,.85);">'
        f"<span>{escape(article.category)}</span></div></section>"
        '<div class="ednote" style="background:#fff;border:1px solid rgba(16,18,24,.10);'
        'border-radius:0 0 22px 22px;margin-top:-14px;padding:30px 30px 24px;">'
        "<h3 class=\"ed-k\">Editor's Summary</h3>"
        f"<p>{_daily_summary_html(article)}</p>"
        '<div class="src" style="margin-top:14px;padding-top:10px;border-top:1px solid '
        '#EEF0F4;font-size:11.5px;color:#9CA3B0;font-weight:600;">출처 '
        f"{_article_source_anchor(article)}</div></div>"
    )


def _daily_card(article: EditorialArticle) -> str:
    return (
        '<article class="card" data-role="article-card" '
        f'style="{_category_style(article.category)};display:grid;grid-template-columns:'
        '128px 1fr;background:#fff;border:1px solid rgba(16,18,24,.10);'
        'border-radius:16px;overflow:hidden;margin:0 0 12px;">'
        f'<div class="thumb">{_reference_image(article)}</div>'
        '<div class="card-body">'
        f'<span class="chip"><span class="d"></span>{escape(article.category)}</span>'
        f'<h3>{escape(article.title)}</h3><p class="sum">{_daily_summary_html(article)}</p>'
        '<div class="src" style="margin-top:14px;padding-top:10px;border-top:1px solid '
        '#EEF0F4;font-size:11.5px;color:#9CA3B0;font-weight:600;">출처 '
        f"{_article_source_anchor(article)}</div></div></article>"
    )


def _daily_key_lines(articles: list[EditorialArticle]) -> list[str]:
    lines = [article.title for article in articles[:3]]
    if articles:
        candidates = [
            part.strip()
            for article in articles
            for part in _SENTENCE_RE.split(article.summary)
            if part.strip()
        ]
        candidates.append(f"{articles[0].source} · {articles[0].published_label} 게시")
        for candidate in candidates:
            if candidate not in lines:
                lines.append(candidate)
            if len(lines) == 3:
                break
    return lines[:3]


def _daily_editor_lines(articles: list[EditorialArticle]) -> str:
    return "".join(f"<li>{escape(line)}</li>" for line in _daily_key_lines(articles))


def _dominant_issue(articles: list[EditorialArticle]) -> tuple[bool, str, int]:
    if len(articles) < 3:
        return False, "이번 주 핵심 이슈 묶음", 0
    per_article: list[set[str]] = []
    counts: dict[str, int] = {}
    for article in articles:
        words = {
            word.casefold()
            for word in _WORD_RE.findall(article.title)
            if len(word) >= 3 and word.casefold() not in _STOPWORDS and not word.isdigit()
        }
        per_article.append(words)
        for word in words:
            counts[word] = counts.get(word, 0) + 1
    ranked = sorted(counts, key=lambda word: (-counts[word], -len(word), word))
    if not ranked:
        return False, "이번 주 핵심 이슈 묶음", 0
    top = ranked[0]
    matched = counts[top]
    dominant = matched >= 3 and matched / len(articles) >= 0.5
    if not dominant:
        return False, "이번 주 핵심 이슈 묶음", matched
    companion = next(
        (word for word in ranked[1:] if counts[word] == matched and word != top), ""
    )
    label = " · ".join(filter(None, (top, companion)))
    return True, label, matched


def _weekly_fact(article: EditorialArticle) -> str:
    return (
        '<div class="fact-item"><div class="tt">'
        f"{escape(article.title)}</div><ul><li>{escape(article.summary)}</li>"
        f"<li>{escape(article.source)} · {escape(article.published_label)} · "
        f"{_external_anchor(article)}</li></ul></div>"
    )


def _weekly_time(article: EditorialArticle) -> str:
    return (
        '<div class="time-item"><time class="time-date" '
        f'datetime="{escape(article.published_at.isoformat(), quote=True)}">'
        f"{article.published_at:%m.%d %H:%M}</time><div class=\"time-box\">"
        f'<div class="time-title">{escape(article.title)}</div>'
        f'<div class="time-desc">{escape(article.source)} · {_external_anchor(article)}</div>'
        "</div></div>"
    )


def _weekly_comparison(articles: list[EditorialArticle]) -> str:
    if len(articles) < 2:
        return '<tr><td colspan="5">확인된 비교 데이터 없음</td></tr>'
    cells = []
    for article in articles[:4]:
        cells.append(
            '<td class="num"><div class="mythos-cell">'
            f'<div class="mythos-val">{escape(article.source)}</div>'
            f'<span class="ver">{escape(article.published_label)}</span>'
            f"{_external_anchor(article)}</div></td>"
        )
    while len(cells) < 4:
        cells.append('<td class="num">—</td>')
    return (
        '<tr class="cat-row"><td colspan="5">▲ 확인된 정성 비교</td></tr>'
        '<tr><td class="metric">기사별 확인 흐름</td>'
        + "".join(cells)
        + "</tr>"
    )


def _weekly_insights(articles: list[EditorialArticle]) -> str:
    return "".join(
        '<div class="j-item">'
        f'<div class="j-no">{index}</div><div class="j-txt">'
        f"<strong>{escape(article.title)}</strong><ul><li>{escape(article.summary)}</li>"
        f"<li>{escape(article.source)} · {_external_anchor(article)}</li></ul></div></div>"
        for index, article in enumerate(articles[:3], start=1)
    )


def _weekly_sources(articles: list[EditorialArticle]) -> str:
    return " · ".join(
        f"{escape(article.source)} {_external_anchor(article, class_name='source-link')}"
        for article in articles
    )


def render_daily(
    articles: list[EditorialArticle],
    *,
    run_at: datetime,
    root_url: str,
) -> RenderedEdition:
    if not articles:
        raise EditorialError("daily edition has no eligible linked articles")
    key = edition_key("daily", run_at)
    coverage = daily_coverage(run_at)
    dated_url, latest_url = public_urls(root_url, "daily", key)
    headline = articles[0]
    html = _fill(
        _template("editorial_daily.html"),
        {
            "EDITION_KEY": escape(key, quote=True),
            "PAGE_TITLE": escape(f"HDEC AI Daily Brief · {key}"),
            "EDITION_LABEL": escape(key),
            "COVERAGE_LABEL": escape(coverage.label()),
            "HEADLINE_HTML": _daily_headline(headline),
            "ARTICLE_CARDS_HTML": (
                "".join(_daily_card(item) for item in articles[1:6])
                or '<p class="empty">추가로 선정된 주요 기사 없음</p>'
            ),
        },
    )
    summary_lines = _daily_key_lines(articles)
    text_lines = [
        "[HDEC AI Daily Brief]",
        f"edition: {key}",
        f"coverage: {coverage.label()}",
        "",
        "오늘의 핵심 3줄",
        *[f"- {line}" for line in summary_lines],
        "",
        f"headline: {headline.title}",
        "주요 기사",
    ]
    text_lines.extend(
        f"- {item.title} | {item.source} | {item.published_label}"
        for item in articles[1:6]
    )
    text_lines.extend(("", f"전체 Daily Brief 보기: {dated_url}"))
    teams_text = "\n".join(text_lines)
    teams_html = _teams_html(
        "HDEC AI Daily Brief",
        key,
        coverage,
        [
            ("오늘의 핵심", "<br>".join(escape(line) for line in summary_lines)),
            (
                "기사",
                "<br>".join(
                    f"{escape(item.title)} · {escape(item.source)} · "
                    f"{escape(item.published_label)}"
                    for item in articles
                ),
            ),
        ],
        dated_url,
        "전체 Daily Brief 보기",
    )
    return RenderedEdition(
        "daily", key, coverage, html, dated_url, latest_url, teams_text, teams_html,
        "daily", headline.title, len(articles),
    )


def render_weekly(
    articles: list[EditorialArticle],
    *,
    run_at: datetime,
    root_url: str,
) -> RenderedEdition:
    if not articles:
        raise EditorialError("weekly edition has no eligible linked articles")
    key = edition_key("weekly", run_at)
    coverage = weekly_coverage(run_at)
    dated_url, latest_url = public_urls(root_url, "weekly", key)
    dominant, issue_label, issue_count = _dominant_issue(articles)
    mode = "dominant_issue" if dominant else "multi_issue"
    mode_label = "단일 핵심 이슈" if dominant else "복수 핵심 이슈"
    headline = articles[0]
    key_title = issue_label if not dominant else f"{issue_label}: 이번 주 핵심 흐름"
    key_items = "".join(
        f"<li><b>{escape(item.source)}</b> {escape(item.title)}</li>"
        for item in articles[:3]
    )
    changed = headline
    why_title = (
        f"관련 보도 {issue_count}건 확인"
        if dominant
        else f"서로 다른 핵심 흐름 {min(3, len(articles))}건 확인"
    )
    management_cards = "".join(
        (
            '<div class="top-card"><div class="k">'
            f"{escape(label)}</div><div class=\"v\">{escape(title)}</div>"
            f'<div class="d">{escape(description)}</div></div>'
        )
        for label, title, description in (
            ("WHAT CHANGED", changed.title, changed.summary),
            (
                "WHY IT MATTERS",
                why_title,
                "동일 coverage 안에서 확인된 기사 제목·요약·출처를 교차해 판단해야 합니다.",
            ),
            (
                "MANAGEMENT POINT",
                "원문 근거 확인 후 영향 범위 판단",
                "기사별 게시시각과 링크 유형을 함께 보고 후속 확인 대상을 정합니다.",
            ),
        )
    )
    alternative = (
        "단일 주제가 다수를 차지했지만 공개 기사 메타데이터와 제공 요약만으로 구성되어 "
        "원문의 후속 정정·추가 발표에 따라 해석이 달라질 수 있습니다."
        if dominant
        else
        "한 주제를 지배적 이슈로 확정할 만큼 보도 집중도가 높지 않았습니다. 공개 기사 "
        "메타데이터와 제공 요약만 사용했으며 후속 보도에 따라 우선순위가 달라질 수 있습니다."
    )
    html = _fill(
        _template("editorial_weekly.html"),
        {
            "EDITION_KEY": escape(key, quote=True),
            "PAGE_TITLE": escape(f"AI 경영 T&I · {key}"),
            "EDITION_LABEL": escape(key),
            "COVERAGE_SHORT": escape(
                f"{coverage.start:%m.%d}–{coverage.end:%m.%d} KST"
            ),
            "DOCUMENT_TITLE": escape(
                issue_label if dominant else "이번 주 핵심 이슈 묶음"
            ),
            "ISSUE_MODE_LABEL": escape(mode_label),
            "COVERAGE_LABEL": escape(coverage.label()),
            "KEY_MESSAGE_TITLE": escape(key_title),
            "KEY_MESSAGE_ITEMS": key_items,
            "MANAGEMENT_CARDS": management_cards,
            "FACT_ITEMS": "".join(_weekly_fact(item) for item in articles[:3]),
            "TIMELINE_ITEMS": "".join(
                _weekly_time(item) for item in sorted(articles[:6], key=lambda x: x.published_at)
            ),
            "COMPARISON_ROWS": _weekly_comparison(articles),
            "INSIGHT_ITEMS": _weekly_insights(articles),
            "ALTERNATIVE_VIEW": escape(alternative),
            "SOURCE_LINKS": _weekly_sources(articles),
        },
    )
    text_lines = [
        "[AI 경영 T&I]",
        f"ISO week: {key}",
        f"coverage: {coverage.label()}",
        f"mode: {mode_label}",
        "",
        f"KEY MESSAGE: {key_title}",
        f"MANAGEMENT POINT: 원문 근거 확인 후 영향 범위 판단",
        f"headline: {headline.title}",
        "",
        f"전체 Weekly T&I 보기: {dated_url}",
    ]
    teams_text = "\n".join(text_lines)
    teams_html = _teams_html(
        "AI 경영 T&I",
        key,
        coverage,
        [
            ("KEY MESSAGE", escape(key_title)),
            ("MANAGEMENT POINT", "원문 근거 확인 후 영향 범위 판단"),
            ("편집 모드", escape(mode_label)),
        ],
        dated_url,
        "전체 Weekly T&I 보기",
    )
    return RenderedEdition(
        "weekly", key, coverage, html, dated_url, latest_url, teams_text, teams_html,
        mode, headline.title, len(articles),
    )


def _teams_html(
    heading: str,
    key: str,
    coverage: CoverageWindow,
    sections: list[tuple[str, str]],
    public_url: str,
    cta: str,
) -> str:
    blocks = "".join(
        f"<p><strong>{escape(label)}</strong><br>{body}</p>" for label, body in sections
    )
    return (
        '<!DOCTYPE html><html lang="ko"><body style="font-family:Arial,sans-serif">'
        f"<h2>{escape(heading)}</h2><p>{escape(key)}<br>{escape(coverage.label())}</p>"
        f"{blocks}<p><a href=\"{escape(public_url, quote=True)}\" target=\"_blank\" "
        f'rel="noopener noreferrer">{escape(cta)}</a></p></body></html>'
    )


def render_edition(
    edition_type: str,
    raw_articles: Iterable[Mapping],
    *,
    run_at: datetime,
    root_url: str,
    allow_image_network: bool = False,
    image_counters: ImageResolutionCounters | None = None,
    image_page_fetcher: Callable[[str], tuple[str, str]] | None = None,
    image_probe: Callable[[str], bool] | None = None,
    image_opener: object | None = None,
    publisher_counters: PublisherUrlResolutionCounters | None = None,
    publisher_fetcher: Callable[[str], tuple[str, str]] | None = None,
    publisher_opener: object | None = None,
    selection_mode: str = SELECTION_MODE_LEGACY,
) -> RenderedEdition:
    coverage = coverage_for(edition_type, run_at)
    limit = DAILY_MAX_ARTICLES if edition_type == "daily" else WEEKLY_MAX_ARTICLES
    articles = normalize_articles(
        raw_articles,
        coverage,
        limit=limit,
        resolve_images=edition_type == "daily",
        allow_image_network=allow_image_network and edition_type == "daily",
        image_counters=image_counters,
        image_page_fetcher=image_page_fetcher,
        image_probe=image_probe,
        image_opener=image_opener,
        publisher_counters=publisher_counters,
        publisher_fetcher=publisher_fetcher,
        publisher_opener=publisher_opener,
        selection_mode=selection_mode,
    )
    if edition_type == "daily":
        return render_daily(articles, run_at=run_at, root_url=root_url)
    if edition_type == "weekly":
        return render_weekly(articles, run_at=run_at, root_url=root_url)
    raise EditorialError("unsupported edition type")


def validate_rendered(edition: RenderedEdition) -> None:
    encoded = edition.html.encode("utf-8")
    marker = f'data-edition-key="{escape(edition.edition_key, quote=True)}"'
    if marker not in edition.html:
        raise EditorialError("edition marker missing")
    if re.search(rb"\{\{[A-Z0-9_]+\}\}", encoded):
        raise EditorialError("unresolved template marker")
    if edition.article_count < 1:
        raise EditorialError("empty edition")

    teams_anchors = re.findall(r"<a\b[^>]*>", edition.teams_html)
    if len(teams_anchors) != 1:
        raise EditorialError(
            "Teams message must contain exactly one public brief CTA"
        )

    expected_href = (
        f'href="{escape(edition.public_dated_url, quote=True)}"'
    )
    if expected_href not in teams_anchors[0]:
        raise EditorialError(
            "Teams CTA does not target the dated public brief"
        )

    if edition.public_dated_url not in edition.teams_text:
        raise EditorialError("Teams text public brief CTA missing")

    for anchor in re.findall(r"<a\b[^>]*>", edition.html):
        if 'target="_blank"' not in anchor or 'rel="noopener noreferrer"' not in anchor:
            raise EditorialError("external link security attributes missing")
        match = re.search(r'href="([^"]+)"', anchor)
        if not match or not valid_http_url(match.group(1).replace("&amp;", "&")):
            raise EditorialError("invalid external URL")
    if edition.edition_type == "daily":
        if edition.html.count('data-role="headline"') != 1:
            raise EditorialError("daily headline count mismatch")
        if edition.html.count('data-role="article-card"') > 5:
            raise EditorialError("daily article card cap exceeded")
    else:
        required = (
            "key-message", "management-cards", "key-facts", "timeline", "comparison",
            "industry-insight", "alternative-view", "sources",
        )
        if any(f'data-section="{name}"' not in edition.html for name in required):
            raise EditorialError("weekly required section missing")


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    temp_path = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def write_preview_bundle(edition: RenderedEdition, output_dir: Path) -> dict:
    output_dir = output_dir.resolve()
    if output_dir == config.BASE_DIR or config.BASE_DIR in output_dir.parents:
        raise EditorialError("preview output must be outside repository")
    html_bytes = edition.html.encode("utf-8")
    dated = output_dir / f"{edition.edition_key}.html"
    latest = output_dir / "latest.html"
    text_path = output_dir / "teams-preview.txt"
    mail_path = output_dir / "teams-preview.html"
    for path, payload in (
        (dated, html_bytes),
        (latest, html_bytes),
        (text_path, edition.teams_text.encode("utf-8")),
        (mail_path, edition.teams_html.encode("utf-8")),
    ):
        atomic_write_bytes(path, payload)
    manifest = {
        "version": 1,
        "mode": "preview",
        "edition_type": edition.edition_type,
        "edition_key": edition.edition_key,
        "coverage_start": edition.coverage.start.isoformat(),
        "coverage_end": edition.coverage.end.isoformat(),
        "html_sha256": edition.html_sha256,
        "dated_html": str(dated),
        "latest_html": str(latest),
        "teams_text": str(text_path),
        "teams_html": str(mail_path),
        "public_dated_url": edition.public_dated_url,
        "public_latest_url": edition.public_latest_url,
        "issue_mode": edition.issue_mode,
        "article_count": edition.article_count,
        "network_sends": 0,
        "smtp_attempts": 0,
        "production_state_reads": 0,
        "production_state_writes": 0,
        "docs_writes": 0,
        "git_writes": 0,
        "forbidden_platform_calls": 0,
    }
    atomic_write_bytes(
        output_dir / "manifest.json",
        (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )
    return manifest


def fixture_articles(
    edition_type: str,
    run_at: datetime,
    *,
    profile: str = "dominant",
) -> list[dict]:
    """Deterministic linked metadata used only by explicit preview/verifier paths."""
    coverage = coverage_for(edition_type, run_at)
    span = int((coverage.end - coverage.start).total_seconds())
    if profile not in {"dominant", "multi"}:
        raise EditorialError("unsupported fixture profile")
    dominant_titles = [
        "AI 데이터센터 전력 조달 계획 공개",
        "AI 데이터센터 전력망 연계 기준 논의",
        "AI 데이터센터 전력 효율 기술 투자",
        "AI 데이터센터 전력 수요 대응 협력",
        "AI 데이터센터 냉각 운영 기준 점검",
        "AI 데이터센터 공급망 계약 확대",
    ]
    multi_titles = [
        "산업용 로봇 안전 기준 개정 논의",
        "기업용 소프트웨어 투자 계획 공개",
        "반도체 공급망 협력 체계 발표",
        "공공 부문 인공지능 조달 지침 검토",
        "클라우드 보안 인증 절차 개선",
        "디지털 인재 교육 프로그램 확대",
    ]
    titles = dominant_titles if profile == "dominant" else multi_titles
    rows = []
    for index, title in enumerate(titles):
        seconds = min(span, 600 + index * max(1, span // (len(titles) + 1)))
        published = coverage.start + timedelta(seconds=seconds)
        rows.append(
            {
                "id": f"fixture-{edition_type}-{profile}-{index + 1}",
                "title": title,
                "source": f"검증매체 {index + 1}",
                "published_at": published.isoformat(),
                "url": f"https://news{index + 1}.fixture.test/articles/{edition_type}-{index + 1}",
                "snippet": (
                    f"{title}과 관련한 공개 계획과 적용 범위가 제시됐다. "
                    "세부 일정과 조건은 원문 발표를 기준으로 추가 확인이 필요하다."
                ),
                "source_metadata": {"provider": "offline_fixture"},
            }
        )
    return rows


def manifest_for_runtime(edition: RenderedEdition, dated_path: Path, latest_path: Path) -> dict:
    return {
        "version": 1,
        "edition_type": edition.edition_type,
        "edition_key": edition.edition_key,
        "coverage_start": edition.coverage.start.isoformat(),
        "coverage_end": edition.coverage.end.isoformat(),
        "html_sha256": edition.html_sha256,
        "public_dated_url": edition.public_dated_url,
        "public_latest_url": edition.public_latest_url,
        "dated_path": str(dated_path),
        "latest_path": str(latest_path),
        "teams_text": edition.teams_text,
        "teams_html": edition.teams_html,
        "headline": edition.headline,
        "issue_mode": edition.issue_mode,
        "article_count": edition.article_count,
    }


def article_dict(article: EditorialArticle) -> dict:
    """Stable helper for offline contract assertions."""
    output = asdict(article)
    output["published_at"] = article.published_at.isoformat()
    return output


def resolved_image_record(article: EditorialArticle, *, is_headline: bool = False) -> dict:
    """Minimal live-preview image audit record; no page body or credential data."""
    remote_image_url = normalize_image_candidate_url(article.image_remote_url) or (
        normalize_image_candidate_url(article.image_url)
    )
    candidate_source_kinds = [candidate.source_kind for candidate in article.image_candidates]
    candidate_hosts = [
        _url_host(
            normalize_image_candidate_url(candidate.url, base_url=candidate.source_page_url)
        )
        for candidate in article.image_candidates
    ]
    candidate_attempts = [
        {
            "source_kind": attempt.source_kind,
            "host": attempt.host,
            "status": attempt.status,
            "reason": attempt.reason,
            "content_type": attempt.content_type,
            "byte_size": attempt.byte_size,
            "local_asset": attempt.local_asset,
            "duplicate_asset_reuse": attempt.duplicate_asset_reused,
            "selected": attempt.selected,
            "byte_validation_status": attempt.byte_validation_status,
            "quality_accepted": attempt.quality_accepted,
            "quality_rejection_reason": attempt.quality_rejection_reason,
            "logo_signals": list(attempt.logo_signals),
        }
        for attempt in article.image_candidate_attempts
    ]
    return {
        "title": article.title,
        "is_headline": is_headline,
        "publisher": article.source,
        "collection_source_kind": article.collection_source_kind,
        "article_url": article.selected_url,
        "original_article_url": article.original_article_url,
        "original_host": _url_host(article.original_article_url),
        "publisher_article_url": article.publisher_article_url,
        "publisher_host": _url_host(article.publisher_article_url),
        "publisher_url_source_kind": article.publisher_url_source_kind,
        "publisher_url_reason": article.publisher_url_reason,
        "relevance_score": article.relevance_score,
        "freshness_score": article.freshness_score,
        "source_quality_score": article.source_quality_score,
        "total_ranking_score": article.total_ranking_score,
        "total_ranking_key": list(article.total_ranking_key),
        "selection_reason": article.selection_reason,
        "resolved_image_url": remote_image_url,
        "rendered_image_src": article.image_url,
        "local_image_src": article.image_local_src,
        "local_asset_filename": article.image_local_asset,
        "image_host": _url_host(remote_image_url),
        "image_candidate_source_kinds": candidate_source_kinds,
        "image_candidate_hosts": candidate_hosts,
        "image_candidate_attempts": candidate_attempts,
        "final_image_candidate_source_kind": article.image_source_kind,
        "source_kind": article.image_source_kind,
        "image_source_kind": article.image_source_kind,
        "width": article.image_width,
        "height": article.image_height,
        "fallback_used": article.image_fallback_used,
        "image_reason": article.image_reason,
        "reason": article.image_reason,
        "image_download_status": article.image_download_status,
        "downloaded_content_type": article.image_download_content_type,
        "image_byte_size": article.image_download_bytes,
        "duplicate_asset_reuse": article.image_duplicate_asset_reused,
        "image_materialization_reason": article.image_materialization_reason,
        "image_quality_accepted": article.image_quality_accepted,
        "image_quality_reason": article.image_quality_reason,
        "image_quality_signals": list(article.image_quality_signals),
    }
