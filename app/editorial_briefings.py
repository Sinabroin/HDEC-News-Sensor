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

from app import (
    ai_centrality,
    config,
    editorial_preference_runtime,
    executive_materiality,
    news_access,
    news_coverage,
    public_institution_routing,
    public_urls as public_url_contract,
    source_priority,
    source_quality,
)

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
# R4-R12 §3 — hard floors for every Daily candidate / delivered-lead image.
# The 250x24 MOIS banner class fails both the height floor and the banner
# ratio; nothing below these limits may ever reach a Daily card.
DAILY_IMAGE_MIN_WIDTH = 320
DAILY_IMAGE_MIN_HEIGHT = 180
DAILY_IMAGE_MAX_BANNER_RATIO = 4.0
DAILY_IMAGE_MAX_VERTICAL_RATIO = 3.0
DAILY_CATEGORY_FALLBACK_DIR = config.DATA_DIR / "editorial_image_fallbacks"
DAILY_CATEGORY_FALLBACK_ASSETS = {
    "투자·산업": "category-invest-industry.png",
    "기업동향": "category-corporate.png",
    "기술정보": "category-technology.png",
}
DAILY_CATEGORY_FALLBACK_GENERIC = "category-ai-infrastructure.png"
APPROVED_REAL_ARTICLE_PHOTO_SOURCE_KINDS = frozenset(
    {
        "rss_image",
        "media_content",
        "media_thumbnail",
        "enclosure",
        "og_image",
        "twitter_image",
        "jsonld_image",
        "image_src",
        "body_image",
        "human_supplied",
    }
)
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
# D7-AK-6E R4-R17 §C — same-publisher retransmission collapse window. A single
# publisher re-emitting the identical headline at a different time within the
# bounded Daily coverage window is one story, not two. This is same-publisher
# only; different publishers reporting the same event are never collapsed here.
SAME_PUBLISHER_RETRANSMISSION_WINDOW = timedelta(hours=24)
SELECTION_MODE_LEGACY = "legacy"
SELECTION_MODE_DIRECT_AWARE_DAILY = "direct_aware_daily"
SELECTION_MODE_EDITORIAL_PRIORITY = SELECTION_MODE_DIRECT_AWARE_DAILY

# D7-AK-6E R4-R17 — discovery-lane provenance marker (mirrors
# naver_news_provider.DISCOVERY_LANE_PRIMARY_PUBLISHER). A row surfaced by the
# bounded primary-publisher lane carries a query string that is literally
# "<publisher name> <topic>"; that discovery text must never become relevance
# authority, so a primary-publisher row is denied the provider-query relevance
# boost and the provider-query-only fallback. Discovery ≠ qualification.
DISCOVERY_LANE_PRIMARY_PUBLISHER = "primary_publisher"

# D7-AK-6E R4-R17 — the executive materiality qualification layer is scoped to
# the Daily/Review executive-brief curation surface: a daily edition built for
# operator review (edition_type == "daily" AND operator_review). This is exactly
# §G's "Review qualification path" and the surface where the raw primary-lane
# supply exposed the observed noise; the published Daily is curated from this
# gated pool. Other normalize_articles callers — the generic edition_type=None
# mechanics/preview path and non-review daily previews (operator_review=False) —
# keep their prior behavior unchanged.
_EXECUTIVE_QUALIFICATION_EDITIONS = frozenset({"daily"})

_SELECTION_MODES = {
    SELECTION_MODE_LEGACY,
    SELECTION_MODE_DIRECT_AWARE_DAILY,
}

# The operator-locked ordered publisher lists live in the canonical
# source-priority contract (data/source_priority_rules.json). These derived
# tuples keep the editorial-facing names stable for consumers and tests.
PRIMARY_PUBLISHER_PRIORITY = source_priority.locked_publisher_names("primary_10")
SECONDARY_PUBLISHER_PRIORITY = source_priority.locked_publisher_names("secondary_3")

PREFERRED_PUBLISHER_DAILY_TARGET = 4
PREFERRED_PUBLISHER_WEEKLY_TARGET = 8
# §11/§13 — honest edition-size targets: publish fewer, never pad with weak
# content; the gap below the floor is reported machine-readably as
# selection_shortfall.
DAILY_TARGET_MIN_ARTICLES = 4
WEEKLY_TARGET_MIN_ARTICLES = 8

# R4-R6 §6 — the Daily headline must itself be a qualified AI-central article
# (explicit AI core or enabling infrastructure core); "operator_override" is
# the explicit, written-reason human escape from app.editorial_review. A
# non-AI headline is a hard validation failure, never a warning.
DAILY_HEADLINE_ALLOWED_CENTRALITY = frozenset(
    {
        ai_centrality.LEVEL_EXPLICIT_AI_CORE,
        ai_centrality.LEVEL_ENABLING_INFRASTRUCTURE_CORE,
        "operator_override",
    }
)

# Canonical delivery tier -> legacy editorial group vocabulary.
_LEGACY_GROUP_BY_DELIVERY_TIER = {
    "primary_10": "primary",
    "secondary_3": "secondary",
    "official_institution": "institution",
}

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
    # R4-R12 §3 — hard Daily-image gate accounting. A blank card is
    # impossible: every article without a valid article image materializes a
    # labeled deterministic category visual instead.
    daily_hard_gate_rejections: int = 0
    daily_duplicate_image_rejections: int = 0
    category_fallbacks_materialized: int = 0
    blank_cards: int = 0

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
            "daily_hard_gate_rejections": self.daily_hard_gate_rejections,
            "daily_duplicate_image_rejections": (
                self.daily_duplicate_image_rejections
            ),
            "category_fallbacks_materialized": (
                self.category_fallbacks_materialized
            ),
            "blank_cards": self.blank_cards,
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
    weak_content_rejected: int = 0
    qualified_candidates: int = 0
    selected_candidates: int = 0
    selection_shortfall: int = 0
    # R4-R6 §5 — AI-only scope accounting: every rejection class is
    # machine-readable so a short edition is provably honest.
    ai_central_qualified_count: int = 0
    incidental_ai_rejected_count: int = 0
    stock_market_rejected_count: int = 0
    unrelated_domain_rejected_count: int = 0
    selected_ai_core_count: int = 0
    selected_enabling_infrastructure_count: int = 0
    # R4-R7 §5 — human-memory decision audit. Shadow-only while the committed
    # profile is inactive: the deterministic selection stays authoritative and
    # these fields expose what memory observed / would have changed.
    memory_profile: str = ""
    memory_active: bool = False
    # True only when the memory stage actually evaluated in shadow mode
    # (inactive profile); stays False when memory never ran (legacy mode).
    memory_shadow_only: bool = False
    memory_runtime_invoked: bool = False
    selected_with_memory_support: int = 0
    rejected_with_negative_precedent: int = 0
    selection_changed_by_memory: bool = False
    headline_supported_by_gold_plus: bool = False
    retrieved_precedent_count: int = 0
    deterministic_selected_ids: tuple[str, ...] = ()
    memory_shadow_selected_ids: tuple[str, ...] = ()
    # R4-R8 — public-institution routing is operator/audit metadata only.  It
    # never creates a fourth visible Brief category or Report section.
    main_candidate_lane_count: int = 0
    public_institution_lane_count: int = 0
    promoted_public_candidate_count: int = 0
    non_promoted_public_candidate_count: int = 0
    selected_public_candidate_count: int = 0
    tni_brief_public_candidate_count: int = 0
    tni_report_topic_candidate_count: int = 0
    duplicate_official_media_event_clusters: int = 0
    public_candidate_ids: tuple[str, ...] = ()
    promoted_public_candidate_ids: tuple[str, ...] = ()
    public_supporting_evidence_ids: tuple[str, ...] = ()
    public_candidate_category_map: tuple[str, ...] = ()
    # R4-R12 §2 — official-institution semantic gate accounting. Official
    # source status is authority only: these counters prove the AI Daily pool
    # admitted an official row solely on semantic eligibility.
    official_rows_seen: int = 0
    official_ai_central_rows: int = 0
    official_incidental_ai_rejected_rows: int = 0
    official_unrelated_domain_rejected_rows: int = 0
    official_material_event_rows: int = 0
    official_selected_rows: int = 0
    # D7-AK-6E R4-R17 — executive materiality qualification accounting. The
    # hard Executive Qualification Gate runs after canonical AI-centrality and
    # weak-content rejection, so a nonmaterial-but-AI-central row (generic "AI is
    # the future" / "ChatGPT popularity" commentary, a strategic keyword with no
    # impact) is provably excluded before final selection. None of these consume
    # provider query metadata: query text is discovery provenance, never
    # qualification evidence.
    executive_qualified_count: int = 0
    executive_materiality_rejected_count: int = 0
    provider_query_only_rejected_count: int = 0
    same_publisher_duplicate_rejected_count: int = 0
    review_qualified_primary_10: int = 0
    review_qualified_secondary_3: int = 0
    review_qualified_official: int = 0
    deliverable_major_lead_candidates: int = 0

    def manifest_fields(
        self,
    ) -> dict[str, int | str | bool | tuple[str, ...]]:
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
            "weak_content_rejected": self.weak_content_rejected,
            "qualified_candidates": self.qualified_candidates,
            "selected_candidates": self.selected_candidates,
            "selection_shortfall": self.selection_shortfall,
            "ai_central_qualified_count": self.ai_central_qualified_count,
            "incidental_ai_rejected_count": self.incidental_ai_rejected_count,
            "stock_market_rejected_count": self.stock_market_rejected_count,
            "unrelated_domain_rejected_count": (
                self.unrelated_domain_rejected_count
            ),
            "selected_ai_core_count": self.selected_ai_core_count,
            "selected_enabling_infrastructure_count": (
                self.selected_enabling_infrastructure_count
            ),
            "memory_profile": self.memory_profile,
            "memory_active": self.memory_active,
            "memory_shadow_only": self.memory_shadow_only,
            "memory_runtime_invoked": self.memory_runtime_invoked,
            "selected_with_memory_support": self.selected_with_memory_support,
            "rejected_with_negative_precedent": (
                self.rejected_with_negative_precedent
            ),
            "selection_changed_by_memory": self.selection_changed_by_memory,
            "headline_supported_by_gold_plus": (
                self.headline_supported_by_gold_plus
            ),
            "retrieved_precedent_count": self.retrieved_precedent_count,
            "deterministic_selected_ids": self.deterministic_selected_ids,
            "memory_shadow_selected_ids": self.memory_shadow_selected_ids,
            "main_candidate_lane_count": self.main_candidate_lane_count,
            "public_institution_lane_count": self.public_institution_lane_count,
            "promoted_public_candidate_count": (
                self.promoted_public_candidate_count
            ),
            "non_promoted_public_candidate_count": (
                self.non_promoted_public_candidate_count
            ),
            "selected_public_candidate_count": (
                self.selected_public_candidate_count
            ),
            "tni_brief_public_candidate_count": (
                self.tni_brief_public_candidate_count
            ),
            "tni_report_topic_candidate_count": (
                self.tni_report_topic_candidate_count
            ),
            "duplicate_official_media_event_clusters": (
                self.duplicate_official_media_event_clusters
            ),
            "public_candidate_ids": self.public_candidate_ids,
            "promoted_public_candidate_ids": self.promoted_public_candidate_ids,
            "public_supporting_evidence_ids": (
                self.public_supporting_evidence_ids
            ),
            "public_candidate_category_map": (
                self.public_candidate_category_map
            ),
            "official_rows_seen": self.official_rows_seen,
            "official_ai_central_rows": self.official_ai_central_rows,
            "official_incidental_ai_rejected_rows": (
                self.official_incidental_ai_rejected_rows
            ),
            "official_unrelated_domain_rejected_rows": (
                self.official_unrelated_domain_rejected_rows
            ),
            "official_material_event_rows": self.official_material_event_rows,
            "official_selected_rows": self.official_selected_rows,
            "executive_qualified_count": self.executive_qualified_count,
            "executive_materiality_rejected_count": (
                self.executive_materiality_rejected_count
            ),
            "provider_query_only_rejected_count": (
                self.provider_query_only_rejected_count
            ),
            "same_publisher_duplicate_rejected_count": (
                self.same_publisher_duplicate_rejected_count
            ),
            "review_qualified_primary_10": self.review_qualified_primary_10,
            "review_qualified_secondary_3": self.review_qualified_secondary_3,
            "review_qualified_official": self.review_qualified_official,
            "deliverable_major_lead_candidates": (
                self.deliverable_major_lead_candidates
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
    # Exact Review assets are a separate immutable provenance domain from a
    # Daily publication. These fields are minted only while loading the exact
    # dated Review bundle; the publisher never treats image_url by itself as
    # filesystem authority.
    review_asset_edition_key: str = ""
    review_asset_relative_path: str = ""
    review_asset_sha256_prefix: str = ""
    image_duplicate_asset_reused: bool = False
    image_materialization_reason: str = "not_materialized"
    image_quality_accepted: bool = False
    image_quality_reason: str = ""
    image_quality_signals: tuple[str, ...] = ()
    # R4-OPS-7: this is deliberately stricter than image_quality_accepted.
    # Only validated raster bytes materialized to an immutable local/public
    # publication asset may set it. A technically valid fallback visual is
    # never a real article photo.
    image_real_article_photo: bool = False
    # R4-R12 §3 — a category fallback is a clearly-labeled deterministic
    # neutral visual, never presented as an article photograph.
    image_is_category_fallback: bool = False
    image_candidates: tuple[ImageCandidateOption, ...] = ()
    image_candidate_attempts: tuple[ImageCandidateAttempt, ...] = ()
    # D7-AK-6E R4-R6 §11 — explainable selection factors, safe for surfaces.
    materiality_score: float = 0.0
    hdec_relevance_score: float = 0.0
    publisher_tier: str = ""
    publisher_tier_rank: int = 99
    publisher_rank: int = 0
    publisher_priority_label: str = ""
    source_authority_rank: int = 9
    executive_relevance_reason: str = ""
    materiality_reason: str = ""
    diversity_contribution: str = ""
    # §12 — Editor's Summary implication: generated default, human override wins.
    executive_implication: str = ""
    implication_html: str = ""
    # R4-R6 §5/§6 — canonical AI-centrality level carried to the headline
    # contract; "operator_override" marks an explicit human override with a
    # written reason (never silent).
    ai_centrality_level: str = ""
    # R4-R8 machine-readable routing. These fields are intentionally never
    # rendered as new Brief/Report nodes.
    source_class: str = public_institution_routing.SOURCE_CLASS_OTHER
    editorial_lane: str = public_institution_routing.LANE_MAIN
    public_institution_type: str = ""
    official_source_name: str = ""
    source_registry_id: str = ""
    source_domain: str = ""
    default_surface: str = public_institution_routing.SURFACE_MAIN
    main_surface_eligible: bool = True
    teams_alert_eligible: bool = True
    tni_brief_eligible: bool = True
    tni_report_topic_eligible: bool = False
    promotion_reason: str = "not_public_institution"
    promotion_condition: str = ""
    final_category: str = ""
    final_surface: str = public_institution_routing.SURFACE_MAIN
    human_placement_override: bool = False
    human_placement_reason: str = ""
    headline_eligible: bool = True
    authority_verified: bool = False
    # R4-R12 §2 — official semantic AI gate verdict (authority never precedes it).
    official_ai_centrality_level: str = ""
    official_ai_gate_reason: str = ""
    official_material_event: bool = False
    official_daily_pool_eligible: bool = True
    duplicate_event_cluster: str = ""
    supporting_evidence_only: bool = False

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
    # R4-R9C — immutable Daily edition identity. editor_url stays "" unless the
    # dated Review Console for this edition is known to exist, so a broken
    # editor link can never be emitted; edition_manifest is the non-sensitive
    # editor-load record whose digest the edition_id embeds.
    edition_id: str = ""
    editor_url: str = ""
    edition_manifest: dict | None = None
    image_audit: dict | None = None

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
    try:
        parsed_port = parsed.port
    except ValueError as exc:
        raise EditorialError("REPORT_URL is invalid") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or "\\" in value
        or any(character.isspace() for character in value)
    ):
        raise EditorialError("REPORT_URL must use http/https")
    if parsed.params or parsed.query or parsed.fragment:
        raise EditorialError("REPORT_URL suffix contract mismatch")
    canonical_root_path = urlparse(public_url_contract.PUBLIC_ROOT).path.rstrip("/")
    accepted_paths = {
        canonical_root_path,
        canonical_root_path + DAILY_REPORT_SUFFIX,
        urlparse(public_url_contract.CANONICAL_DASHBOARD_URL).path,
        urlparse(public_url_contract.COMPATIBILITY_DASHBOARD_URL).path,
        urlparse(public_url_contract.DAILY_LATEST_URL).path,
    }
    candidate_path = parsed.path
    if candidate_path.endswith("/") and candidate_path.rstrip("/") == canonical_root_path:
        candidate_path = candidate_path.rstrip("/")
    if candidate_path not in accepted_paths:
        raise EditorialError("REPORT_URL suffix contract mismatch")
    netloc = (parsed.hostname or "").lower()
    if parsed_port is not None:
        default_port = 443 if parsed.scheme == "https" else 80
        if parsed_port != default_port:
            netloc = f"{netloc}:{parsed_port}"
    return f"{parsed.scheme}://{netloc}{canonical_root_path}"


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
    tier = source_priority.publisher_delivery_tier(source, selected_url)
    group = _LEGACY_GROUP_BY_DELIVERY_TIER.get(str(tier.get("tier")))
    if group == "institution":
        return "institution", 0
    if group:
        return group, int(tier.get("publisher_rank") or 0)
    return "other", 999


def publisher_priority(source: str, selected_url: str) -> tuple[str, int]:
    """Return the shared locked publisher tier/rank for delivery surfaces."""
    return _publisher_priority(source, selected_url)


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
        ("투자·산업", ("투자", "계약", "수주", "펀드", "인수", "파트너십", "데이터센터", "전력", "인프라", "정책", "규제")),
        ("기업동향", ("기업", "현대건설", "삼성", "구글", "마이크로소프트", "오픈ai", "openai", "엔비디아", "nvidia")),
        ("기술정보", ("모델", "서비스", "플랫폼", "로봇", "소프트웨어", "제품", "반도체", "gpu", "ai", "인공지능")),
    )
    for label, keywords in rules:
        if any(keyword in text for keyword in keywords):
            return label
    return "기업동향"


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
    weak_content_reason: str = ""
    materiality_score: float = 0.0
    hdec_relevance_score: float = 0.0
    # R4-R6 §2/§5 — canonical AI-centrality decision (title/lead evidence only).
    ai_centrality_level: str = ""
    ai_centrality_exclusion: str = ""
    ai_centrality_reason: str = ""

    @property
    def is_ai_central(self) -> bool:
        from app import ai_centrality as _ai_centrality

        return (
            not self.ai_centrality_exclusion
            and self.ai_centrality_level in _ai_centrality.CENTRAL_LEVELS
        )

    @property
    def ai_rejection_class(self) -> str:
        """stock_market | unrelated_domain | incidental_ai | '' (qualified)."""
        from app import ai_centrality as _ai_centrality

        if self.is_ai_central:
            return ""
        if self.ai_centrality_exclusion == _ai_centrality.EXCLUSION_STOCK_MARKET:
            return "stock_market"
        if self.ai_centrality_exclusion:
            return "unrelated_domain"
        if self.ai_centrality_level == _ai_centrality.LEVEL_INCIDENTAL_AI_MENTION:
            return "incidental_ai"
        return "unrelated_domain"

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
        return self.article.source_class == public_institution_routing.SOURCE_CLASS_OFFICIAL

    @property
    def is_public_lane(self) -> bool:
        return self.article.editorial_lane == public_institution_routing.LANE_PUBLIC

    @property
    def is_main_surface_eligible(self) -> bool:
        return not self.is_public_lane or self.article.main_surface_eligible

    @property
    def is_headline_eligible(self) -> bool:
        return self.is_main_surface_eligible and self.article.headline_eligible


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


# D7-AK-6E R4-R6 §11 — weak-content rejection. Title-first matching: a passing
# mention inside a snippet must not kill a material article, so summary terms
# only apply when the title carries no strong material signal of its own.
_WEAK_CONTENT_RULES: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("stock_theme_article", ("수혜주", "관련주", "테마주", "급등주", "목표주가", "상한가 행진", "매수 추천"), ()),
    ("recruitment_notice", ("채용", "공채 모집", "인재 모집", "입사 지원", "채용설명회"), ("채용 박람회 개최",)),
    ("book_or_review", ("신간", "출간", "서평", "북콘서트", "출판기념"), ()),
    ("promotion_notice", ("사은품", "경품", "특가 프로모션", "할인 이벤트", "구매 이벤트"), ()),
    ("campaign_publicity", ("캠페인", "공모전", "봉사활동", "걷기대회", "사생대회", "그림대회", "사진 공모"), ()),
    ("award_publicity", ("시상식", "표창", "감사패", "공로상", "수상했", "수상의 영예", "수상자로"), ("수상태양광", "수상 태양광")),
    ("lifestyle_content", ("맛집", "여행코스", "레시피", "뷰티", "웰니스 팁"), ()),
    ("education_publicity", ("교육 수료식", "교육생 모집", "아카데미 수료", "장학금 전달"), ()),
)


def _weak_content_reason(title: str, summary: str) -> str:
    """Return the §11 weak-content rejection label, or '' when none applies."""
    title_text = " ".join(str(title or "").split())
    combined = f"{title_text} {' '.join(str(summary or '').split())}"
    for label, terms, exceptions in _WEAK_CONTENT_RULES:
        if any(exception in combined for exception in exceptions):
            continue
        if any(term in title_text for term in terms):
            return label
    return ""


# R4-OPS-2 — the material-event vocabulary and scorer now live in the shared
# app.executive_materiality leaf so the Daily editorial gate and the real-time
# Teams AI News Watch judge send-eligibility materiality by one rule (no drift).
# The private module names are preserved as aliases for every existing caller.
_MATERIAL_ACTION_TERMS = executive_materiality.MATERIAL_ACTION_TERMS
_MATERIAL_SCALE_RE = executive_materiality.MATERIAL_SCALE_RE
_MATERIAL_RISK_TERMS = executive_materiality.MATERIAL_RISK_TERMS
_materiality_score = executive_materiality.materiality_score


_HDEC_DIRECT_TERMS = executive_materiality.HDEC_DIRECT_TERMS
_HDEC_STRATEGIC_TERMS = (
    "AI 데이터센터", "데이터센터", "SMR", "소형모듈원전", "원전", "스마트건설",
    "BIM", "디지털 트윈", "건설로봇", "해외수주", "전력 인프라", "송전", "변전",
)

# ---------------------------------------------------------------------------
# D7-AK-6E R4-R17 §B — Executive Qualification Gate.
#
# An article being AI-central is necessary but NOT sufficient: an executive
# brief must exclude generic "AI is the future" commentary, "ChatGPT
# popularity" chatter, soft outlooks, and market/earnings-calendar pieces even
# when they mention a famous AI company. After the canonical AI-centrality gate
# and weak-content rejection, a NON-OFFICIAL candidate must additionally carry
# at least one strong, machine-readable material signal. Official-institution
# rows keep their dedicated semantic/material-event gate and are never
# re-judged here (no weaker parallel path).
#
# Allowed evidence: title, publisher subtitle, and the first factual
# publisher lead/snippet sentence only. Forbidden evidence (never read here):
# the provider query string, the generated summary/why-it-matters/executive
# implication/category, feedback, or publisher prestige on its own. The gate
# therefore cannot be rescued by search-query metadata, regardless of lane.
# ---------------------------------------------------------------------------

# R4-OPS-2 — the strategic-domain / impact / AI-security vocabularies and the
# gate verdict type now live in the shared app.executive_materiality leaf so the
# Daily gate below and the real-time Teams Watch share one materiality contract.
# Private names are preserved as aliases for any in-module reference.
_EXEC_STRATEGIC_DOMAIN_TERMS = executive_materiality.EXEC_STRATEGIC_DOMAIN_TERMS
_EXEC_IMPACT_SIGNAL_TERMS = executive_materiality.EXEC_IMPACT_SIGNAL_TERMS
_EXEC_AI_SECURITY_TERMS = executive_materiality.EXEC_AI_SECURITY_TERMS
_ExecutiveQualification = executive_materiality.ExecutiveQualification


def _executive_evidence(candidate: "_ArticleCandidate") -> dict:
    """Allowed evidence only — title / publisher subtitle / factual lead.

    Reads the raw factual snippet (never the generated summary) and never the
    provider query string, so search-query metadata can never qualify a row."""
    raw = candidate.raw
    return {
        "title": candidate.article.title,
        "snippet": str(raw.get("snippet") or ""),
        "subtitle": str(raw.get("subtitle") or ""),
        "publisher_section": str(
            raw.get("publisher_section") or raw.get("section") or ""
        ),
    }


def _executive_qualification(
    candidate: "_ArticleCandidate",
) -> _ExecutiveQualification:
    """R4-R17 §B — is this AI-central candidate materially useful to an executive?

    Returns qualified=True only when the title / subtitle / factual lead carry a
    strong material signal (structural AI event, HDEC-direct AI event, confirmed
    corporate/industrial event, AI security incident, or a strategic HDEC
    infrastructure domain paired with an actual impact/constraint signal).
    Opinion-labelled pieces require a hard factual signal (1/2/3/5) and never
    qualify on a strategic-domain+impact pairing alone.

    R4-OPS-2 — the five-signal decision now lives in the shared
    app.executive_materiality leaf (single source of truth for Daily and the
    real-time Teams Watch); this wrapper only builds the allowed evidence
    (title / subtitle / factual snippet) from the candidate and delegates."""
    return executive_materiality.executive_qualification(
        _executive_evidence(candidate)
    )


def _hdec_relevance_score(title: str, summary: str) -> float:
    """§11 factor 4 — 현대건설 direct impact first, strategic domain second."""
    text = f"{title} {summary}"
    if any(term in text for term in _HDEC_DIRECT_TERMS):
        return 1.0
    if any(term in text for term in _HDEC_STRATEGIC_TERMS):
        return 0.5
    return 0.0


_IMPLICATION_DOMAIN_LABELS = (
    ("현대건설", "현대건설 직접 관련 사안"),
    ("현대엔지니어링", "현대건설그룹 직접 관련 사안"),
    ("데이터센터", "AI 데이터센터·전력 인프라 사업 기회"),
    ("SMR", "원전·SMR 사업 포트폴리오"),
    ("원전", "원전·에너지 사업 포트폴리오"),
    ("스마트건설", "스마트건설 기술 경쟁력"),
    ("BIM", "설계·시공 디지털 전환 역량"),
    ("디지털 트윈", "설계·시공 디지털 전환 역량"),
    ("로봇", "건설 자동화·생산성 역량"),
    ("중대재해", "안전·품질 리스크 관리"),
    ("규제", "규제·정책 대응"),
    ("해외", "해외수주 환경"),
    ("금리", "자금조달·재무 환경"),
    ("공급망", "자재·공급망 조건"),
)


def _compose_executive_implication(
    title: str,
    summary: str,
    category: str,
    materiality_reasons: tuple[str, ...],
) -> str:
    """§12 — why-it-matters sentence built only from detected article signals.

    States the affected business/risk/capability area and what to monitor,
    without asserting any fact beyond what the article text itself carries."""
    text = f"{title} {summary}"
    domain = next(
        (label for term, label in _IMPLICATION_DOMAIN_LABELS if term in text),
        "",
    )
    if not domain:
        domain = f"{category} 동향" if category else "산업 동향"
    action = next(
        (
            reason.split(":", 1)[1]
            for reason in materiality_reasons
            if reason.startswith("confirmed_action:")
        ),
        "",
    )
    if any(term in text for term in _HDEC_DIRECT_TERMS):
        subject = "현대건설이 직접 당사자인 사안으로"
    elif action:
        subject = f"'{action}' 단계까지 확인된 사안으로"
    else:
        subject = "동향 단계의 사안으로"
    monitor = (
        "후속 발주·계약 조건과 경쟁 구도"
        if any(term in text for term in ("수주", "발주", "계약", "입찰"))
        else "안전·품질 대응 체계"
        if any(term in text for term in _MATERIAL_RISK_TERMS)
        else "사업·기술 파급 범위"
    )
    return f"{subject} {domain} 관점에서 {monitor}을(를) 점검할 필요가 있습니다."


def _candidate_relevance(
    title: str,
    summary: str,
    category: str,
    raw: Mapping,
) -> tuple[float, tuple[str, ...]]:
    metadata = _article_metadata(raw)
    query = str(metadata.get("query") or "").strip()
    # D7-AK-6E R4-R17 — the primary-publisher discovery lane's query text is
    # "<publisher name> <topic>", i.e. how the row was FOUND, not evidence that
    # it is relevant. Deny such rows the provider-query relevance signals so the
    # query text can never lift a row over the floor by itself; a
    # primary-publisher row must earn relevance from its own title/summary
    # content, category, or institution status.
    lane = str(
        metadata.get("discovery_lane") or raw.get("discovery_lane") or ""
    ).strip()
    query_is_authority = lane != DISCOVERY_LANE_PRIMARY_PUBLISHER
    text_groups = news_coverage.query_groups_for_text(title, summary)
    query_group = news_coverage.query_group_for_query(query)
    score = 0.0
    reasons: list[str] = []
    if query_group and query_is_authority:
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

    if not reasons and query and query_is_authority:
        # Provider query evidence is weaker than text/query-group agreement but prevents
        # complete blindness for configured non-coverage source files. It never
        # reaches the selection floor on its own (0.5 < SELECTION_RELEVANCE_FLOOR)
        # and is withheld entirely from the primary-publisher discovery lane.
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
    # R4-R6 §2 — canonical AI-centrality from the article's own title and raw
    # lead only (never the derived summary, never generated metadata).
    centrality = ai_centrality.classify(
        {
            "title": title,
            "snippet": str(raw.get("snippet") or raw.get("summary") or ""),
            "subtitle": str(raw.get("subtitle") or ""),
            "publisher_section": str(raw.get("publisher_section") or ""),
        }
    )
    freshness = _candidate_freshness(published, coverage)
    source_quality = 2.0 if selected.is_direct else 0.0
    total = round(relevance + freshness + source_quality, 3)
    publisher_key = _publisher_key(source, selected_url)
    title_key = _title_fingerprint(title)
    materiality, materiality_reasons = _materiality_score(title, summary)
    hdec_relevance = _hdec_relevance_score(title, summary)
    delivery_tier = source_priority.publisher_delivery_tier(source, selected_url)
    authority_rank = int(
        source_priority.classify(source)["source_priority_rank"]
    )
    weak_content = _weak_content_reason(title, summary)
    routing_input = dict(raw)
    routing_input.update(
        {
            "title": title,
            "source": source,
            "publisher_url": selected_url,
            # Promotion is derived from the raw factual lead, not the generated
            # summary or executive implication.
            "snippet": str(raw.get("snippet") or raw.get("summary") or ""),
        }
    )
    public_route = public_institution_routing.classify(routing_input)
    if public_route.tni_brief_eligible and public_route.final_category:
        category = public_route.final_category
    routing_input.update(public_route.metadata())
    explicit_cluster = " ".join(
        str(raw.get("event_cluster_id") or raw.get("duplicate_event_cluster") or "").split()
    )
    # D7-AK-6E R4-R6 §11 ranking precedence: executive decision relevance →
    # materiality → publisher priority → HDEC direct/strategic relevance →
    # freshness → source authority. Topic/publisher diversity is applied by the
    # selection pass (soft caps), and link quality by the existing direct-margin
    # swap on total_ranking_score.
    ranking_key = (
        relevance,
        materiality,
        int(
            not public_route.is_public_lane
            or public_route.main_surface_eligible
        ),
        -int(delivery_tier["tier_rank"]),
        -int(delivery_tier["publisher_rank"]),
        hdec_relevance,
        freshness,
        -authority_rank,
        source_quality,
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
            materiality_score=materiality,
            hdec_relevance_score=hdec_relevance,
            publisher_tier=str(delivery_tier["tier"]),
            publisher_tier_rank=int(delivery_tier["tier_rank"]),
            publisher_rank=int(delivery_tier["publisher_rank"]),
            publisher_priority_label=str(delivery_tier["label"]),
            source_authority_rank=authority_rank,
            executive_relevance_reason=";".join(reasons) or "no_relevance_signal",
            materiality_reason=";".join(materiality_reasons) or "no_material_signal",
            executive_implication=_compose_executive_implication(
                title, summary, category, materiality_reasons
            ),
            ai_centrality_level=centrality.level,
            **public_route.metadata(),
            final_surface=public_route.default_surface,
            duplicate_event_cluster=explicit_cluster,
        ),
        raw=routing_input,
        selected_url=selected_url,
        selected_kind=selected.kind,
        provider_tokens=_provider_tokens(raw),
        is_direct=selected.is_direct,
        publisher_key=publisher_key,
        title_key=title_key,
        cluster_key=(
            "explicit:" + explicit_cluster.casefold()
            if explicit_cluster
            else title_key if len(title_key) >= 12 else ""
        ),
        relevance_score=relevance,
        freshness_score=freshness,
        source_quality_score=source_quality,
        total_ranking_score=total,
        ranking_key=ranking_key,
        relevance_reasons=reasons,
        weak_content_reason=weak_content,
        materiality_score=materiality,
        hdec_relevance_score=hdec_relevance,
        ai_centrality_level=centrality.level,
        ai_centrality_exclusion=centrality.exclusion,
        ai_centrality_reason=centrality.reason,
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
    routing_input = dict(raw)
    routing_input.update(
        {
            "title": title,
            "source": source,
            "publisher_url": selected_url,
            "snippet": str(raw.get("snippet") or raw.get("summary") or ""),
        }
    )
    public_route = public_institution_routing.classify(routing_input)
    category = classify_category(title, summary)
    if public_route.tni_brief_eligible and public_route.final_category:
        category = public_route.final_category
    routing_input.update(public_route.metadata())
    return (
        EditorialArticle(
            title=title,
            summary=summary,
            source=source,
            published_at=published,
            selected_url=selected_url,
            link_kind=selected.kind,
            link_label=selected.label,
            category=category,
            collection_source_kind=_collection_source_kind(raw),
            # Legacy preview/fixture rows still carry their true canonical
            # level so the Daily headline contract (§6) applies everywhere.
            ai_centrality_level=ai_centrality.classify(
                {
                    "title": title,
                    "snippet": str(raw.get("snippet") or raw.get("summary") or ""),
                }
            ).level,
            **public_route.metadata(),
            final_surface=public_route.default_surface,
        ),
        routing_input,
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
        -candidate.article.publisher_tier_rank,
        -(candidate.article.publisher_rank or 999),
        candidate.is_naver_direct,
        candidate.is_direct,
        candidate.total_ranking_score,
        candidate.article.published_at.isoformat(),
    )
    existing_key = (
        -existing.article.publisher_tier_rank,
        -(existing.article.publisher_rank or 999),
        existing.is_naver_direct,
        existing.is_direct,
        existing.total_ranking_score,
        existing.article.published_at.isoformat(),
    )
    return candidate_key > existing_key


def _routing_candidate_id(candidate: _ArticleCandidate) -> str:
    for key in ("candidate_id", "article_id", "id"):
        value = str(candidate.raw.get(key) or "").strip()
        if value:
            return value
    stable = "\x1f".join(
        (
            candidate.article.title,
            candidate.article.source,
            candidate.article.selected_url,
        )
    )
    return "candidate-" + hashlib.sha256(stable.encode("utf-8")).hexdigest()[:16]


def _public_media_duplicate_groups(
    candidates: list[_ArticleCandidate],
) -> tuple[int, tuple[str, ...]]:
    groups: dict[str, list[_ArticleCandidate]] = {}
    for candidate in candidates:
        # Cross-publisher official/media reconciliation must be backed by an
        # explicit collector or editorial event identity. A normalized title
        # alone is not sufficient evidence that two direct articles describe
        # the same event.
        if candidate.cluster_key.startswith("explicit:"):
            groups.setdefault(candidate.cluster_key, []).append(candidate)
    supporting: list[str] = []
    cluster_count = 0
    for members in groups.values():
        public = [item for item in members if item.is_public_lane]
        media = [item for item in members if not item.is_public_lane]
        if public and media:
            cluster_count += 1
            supporting.extend(_routing_candidate_id(item) for item in public)
    return cluster_count, tuple(dict.fromkeys(supporting))


def _retransmission_rank_key(candidate: _ArticleCandidate) -> tuple:
    """Best-representation ordering for a same-publisher retransmission group.

    Mirrors ``_is_better_duplicate``: canonical publisher tier/rank first, then
    direct-link quality, then ranking/material evidence, then newer timestamp as
    a final tie-breaker, with the URL as an absolute deterministic backstop."""
    return (
        -candidate.article.publisher_tier_rank,
        -(candidate.article.publisher_rank or 999),
        candidate.is_naver_direct,
        candidate.is_direct,
        candidate.total_ranking_score,
        candidate.article.published_at.isoformat(),
        candidate.selected_url,
    )


def _collapse_same_publisher_retransmissions(
    candidates: list[_ArticleCandidate],
    *,
    retain_support: Callable[[_ArticleCandidate], None],
    audit: SelectionAuditCounters | None,
) -> list[_ArticleCandidate]:
    """R4-R17 §C — collapse a single publisher's retransmitted identical headline.

    A row is folded into an earlier one only when it shares the SAME publisher
    identity AND the SAME normalized title fingerprint AND lies within the
    bounded 24-hour window. Exactly one best representation survives per group;
    the rest are counted and (for the public lane under operator review) retained
    as supporting evidence. Different publishers are never collapsed here, so
    explicit cross-publisher event-cluster semantics are untouched."""
    groups: dict[tuple[str, str], list[_ArticleCandidate]] = {}
    passthrough: list[_ArticleCandidate] = []
    for candidate in candidates:
        title_key = candidate.title_key
        # Require a substantial fingerprint (mirrors the cluster_key len>=12
        # floor) so short/degenerate titles are never over-collapsed.
        if title_key and len(title_key) >= 12:
            groups.setdefault((candidate.publisher_key, title_key), []).append(
                candidate
            )
        else:
            passthrough.append(candidate)

    kept: list[_ArticleCandidate] = list(passthrough)
    for members in groups.values():
        if len(members) == 1:
            kept.append(members[0])
            continue
        ordered = sorted(members, key=_retransmission_rank_key, reverse=True)
        survivor = ordered[0]
        window_kept = [survivor]
        for other in ordered[1:]:
            within_window = (
                abs(other.article.published_at - survivor.article.published_at)
                <= SAME_PUBLISHER_RETRANSMISSION_WINDOW
            )
            if within_window:
                # Same publisher + same headline + inside window ⇒ retransmission.
                if audit is not None:
                    audit.same_publisher_duplicate_rejected_count += 1
                retain_support(other)
            else:
                # A genuinely stale reissue outside the window survives on its own.
                window_kept.append(other)
        kept.extend(window_kept)
    return kept


def _deduplicate_article_candidates(
    candidates: list[_ArticleCandidate],
    *,
    preserve_public_supporting_duplicates: bool = False,
    audit: SelectionAuditCounters | None = None,
) -> list[_ArticleCandidate]:
    supporting: list[_ArticleCandidate] = []

    def retain_support(candidate: _ArticleCandidate) -> None:
        if preserve_public_supporting_duplicates and candidate.is_public_lane:
            supporting.append(
                replace(
                    candidate,
                    article=replace(
                        candidate.article,
                        supporting_evidence_only=True,
                    ),
                )
            )

    by_exact: dict[tuple[str, str], _ArticleCandidate] = {}
    for candidate in candidates:
        exact_key = (
            re.sub(r"\W+", "", candidate.article.title).casefold(),
            candidate.selected_url.rstrip("/").casefold(),
        )
        existing = by_exact.get(exact_key)
        if existing is None:
            by_exact[exact_key] = candidate
        elif _is_better_duplicate(candidate, existing):
            retain_support(existing)
            by_exact[exact_key] = candidate
        else:
            retain_support(candidate)

    # R4-R17 §C — collapse same-publisher retransmissions (same publisher + same
    # title fingerprint within 24h) before the cross-publisher cluster pass, so
    # a publisher re-emitting one headline twice contributes exactly one row.
    retransmission_deduped = _collapse_same_publisher_retransmissions(
        list(by_exact.values()),
        retain_support=retain_support,
        audit=audit,
    )

    clustered: list[_ArticleCandidate] = []
    by_cluster: dict[str, int] = {}
    for candidate in sorted(retransmission_deduped, key=_candidate_sort_key, reverse=True):
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
        explicit_cluster = key.startswith("explicit:")
        same_event_hint = (
            explicit_cluster
            or candidate.is_aggregator != existing.is_aggregator
        )
        if key and close_time and same_event_hint and _is_better_duplicate(
            candidate, existing
        ):
            retain_support(existing)
            clustered[idx] = candidate
        elif key and close_time and same_event_hint:
            retain_support(candidate)
        else:
            clustered.append(candidate)
    selected_ids = {id(item) for item in clustered}
    extras = [item for item in supporting if id(item) not in selected_ids]
    return sorted(clustered, key=_candidate_sort_key, reverse=True) + extras


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


# R4-R10 — delivered Daily-Brief lead-source floor. A screenshot regression
# showed long-tail / specialist publishers (비즈트리뷴·더퍼블릭·녹색경제신문) and
# stock-theme stories arriving as delivered Daily lead cards. Only the locked
# primary-ten / secondary-three / promoted-official publishers may become a
# delivered lead card; specialist and long-tail (neutral·low) sources are kept
# upstream as supporting evidence but never a standalone delivered card. The
# tier is re-derived authoritatively from source_priority at render time so the
# floor holds even when the article was rebuilt from a serialized review bundle.
_LEAD_SOURCE_ELIGIBLE_TIERS = frozenset(
    {"primary_10", "secondary_3", "major_secondary", "official_institution"}
)


def lead_source_eligible_tier(source: str, selected_url: str = "") -> bool:
    """True only for locked primary-ten / secondary-three / official publishers."""
    tier = str(
        source_priority.publisher_delivery_tier(source, selected_url).get("tier")
    )
    return tier in _LEAD_SOURCE_ELIGIBLE_TIERS


def filter_lead_source_eligible(
    articles: "list[EditorialArticle]",
) -> "list[EditorialArticle]":
    """Keep only delivered lead cards from major or promoted-official sources.

    Order is preserved; a shorter (even empty) brief is preferred to padding
    with a weak-source lead. An official-institution card must additionally be
    main-surface eligible (a non-promoted institution never becomes a lead).
    """
    kept: list[EditorialArticle] = []
    for article in articles:
        tier = str(
            source_priority.publisher_delivery_tier(
                article.source, article.selected_url
            ).get("tier")
        )
        if tier not in _LEAD_SOURCE_ELIGIBLE_TIERS:
            continue
        if tier == "official_institution" and not getattr(
            article, "main_surface_eligible", True
        ):
            continue
        kept.append(article)
    return kept


def _run_deterministic_selection(
    relevant: list[_ArticleCandidate],
    *,
    limit: int,
) -> tuple[list[_ArticleCandidate], list[_ArticleCandidate], bool]:
    """§11/§13 deterministic diversity selection over the ranked eligible pool.

    Pure helper (no counter side effects) so the memory-shadow hypothetical
    selection can reuse the identical pipeline. Returns
    ``(selected, direct_pool, direct_supply_sufficient)``."""
    direct_pool = [
        candidate
        for candidate in relevant
        if candidate.direct_priority_eligible
    ]
    headline_pool = [
        candidate for candidate in relevant if candidate.is_headline_eligible
    ]
    headline_direct_pool = [
        candidate for candidate in direct_pool if candidate.is_headline_eligible
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

    direct_supply_sufficient = (
        len(direct_pool) >= DIRECT_SUPPLY_FOR_AGGREGATOR_CAP
    )
    selected: list[_ArticleCandidate] = []

    # §11/§13 — the headline is chosen by the ranking itself (decision
    # relevance → materiality → publisher priority → …), never by an
    # unconditional publisher or institution preference. The existing
    # direct-margin swap still protects link quality for the headline slot.
    if headline_pool:
        best = headline_pool[0]
        best_direct = headline_direct_pool[0] if headline_direct_pool else None

        if (
            best_direct is not None
            and (best.is_aggregator or not best.is_direct)
            and best.total_ranking_score - best_direct.total_ranking_score
            <= HEADLINE_DIRECT_MARGIN
        ):
            selected.append(best_direct)
        else:
            selected.append(best)
    else:
        # The immutable Brief/Daily renderers require article zero to be the
        # headline. A secondary-only public lane cannot be smuggled into that
        # slot merely because stronger supply is absent.
        return [], direct_pool, direct_supply_sufficient

    # §11 — no unconditional institution quota: official-institution rows
    # compete purely on the shared ranking like every other candidate.

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
    return selected, direct_pool, direct_supply_sufficient


def _memory_product(edition_type: str | None, limit: int) -> str:
    """Resolve an explicit product head, retaining limit fallback for callers.

    Production Daily/Weekly paths always pass ``edition_type``.  The fallback
    preserves compatibility for older fixture callers without making the real
    runtime depend on a fragile numeric limit heuristic.
    """
    if edition_type == "daily":
        return editorial_preference_runtime.PRODUCT_DAILY
    if edition_type == "weekly":
        return editorial_preference_runtime.PRODUCT_WEEKLY
    return (
        editorial_preference_runtime.PRODUCT_DAILY
        if limit <= DAILY_MAX_ARTICLES
        else editorial_preference_runtime.PRODUCT_WEEKLY
    )


def _memory_candidate_id(candidate: _ArticleCandidate) -> str:
    for key in ("candidate_id", "article_id", "id"):
        value = str(candidate.raw.get(key) or "").strip()
        if value:
            return value
    stable = "\x1f".join(
        (
            candidate.article.title,
            candidate.article.source,
            candidate.article.selected_url,
        )
    )
    return "selection-" + hashlib.sha256(stable.encode("utf-8")).hexdigest()[:16]


def _select_article_candidates(
    candidates: list[_ArticleCandidate],
    *,
    limit: int,
    audit: SelectionAuditCounters | None,
    preference_runtime: (
        "editorial_preference_runtime.EditorialPreferenceRuntime | None"
    ) = None,
    edition_type: str | None = None,
    operator_review: bool = False,
) -> list[_ArticleCandidate]:
    # R4-R6 §5 — AI-only scope is the first gate: an AI-branded edition never
    # fills with non-AI-central articles, whatever the supply looks like.
    # Every rejection class is counted machine-readably.
    # R4-R12 §2 — for a verified official institution the dedicated semantic
    # gate runs first: official source status is authority, never relevance,
    # so a tourism/ceremony release or a broad ministry report with AI as one
    # bullet is excluded before candidate selection — including the
    # operator-review extras lane.
    ai_qualified: list[_ArticleCandidate] = []
    for candidate in candidates:
        if candidate.is_official_institution:
            article = candidate.article
            if audit is not None:
                audit.official_rows_seen += 1
                if article.official_ai_centrality_level in (
                    ai_centrality.CENTRAL_LEVELS
                ):
                    audit.official_ai_central_rows += 1
                if article.official_material_event:
                    audit.official_material_event_rows += 1
            if not article.official_daily_pool_eligible:
                if audit is not None:
                    if article.official_ai_gate_reason == (
                        public_institution_routing.OFFICIAL_AI_GATE_INCIDENTAL
                    ):
                        audit.official_incidental_ai_rejected_rows += 1
                    elif article.official_ai_gate_reason == (
                        public_institution_routing.OFFICIAL_AI_GATE_PUBLICITY
                    ):
                        audit.official_unrelated_domain_rejected_rows += 1
                    # no-material-event / outside-domain rejections stay
                    # visible as official_ai_central_rows > official_selected_rows.
                    legacy_class = candidate.ai_rejection_class
                    if legacy_class == "stock_market":
                        audit.stock_market_rejected_count += 1
                    elif legacy_class == "incidental_ai":
                        audit.incidental_ai_rejected_count += 1
                    elif legacy_class:
                        audit.unrelated_domain_rejected_count += 1
                continue
        rejection = candidate.ai_rejection_class
        if not rejection:
            ai_qualified.append(candidate)
            continue
        if audit is not None:
            if rejection == "stock_market":
                audit.stock_market_rejected_count += 1
            elif rejection == "incidental_ai":
                audit.incidental_ai_rejected_count += 1
            else:
                audit.unrelated_domain_rejected_count += 1
    if audit is not None:
        audit.ai_central_qualified_count = len(ai_qualified)
    candidates = ai_qualified

    floor_qualified = [
        candidate
        for candidate in candidates
        if candidate.relevance_score >= SELECTION_RELEVANCE_FLOOR
    ]
    # §11 — weak content never becomes filler, whatever the supply looks like.
    quality_relevant = [
        candidate
        for candidate in floor_qualified
        if not candidate.weak_content_reason
    ]
    weak_rejected = len(floor_qualified) - len(quality_relevant)

    # R4-R17 §B — Executive Qualification Gate. Runs AFTER weak-content
    # rejection (so those counters are preserved) and only on the Daily/Review
    # curation surface (edition_type == "daily" AND operator_review — §G's
    # "Review qualification path"); the generic edition_type=None mechanics path
    # and non-review daily previews are unchanged. A non-official AI-central
    # candidate survives only with a strong material signal; official rows keep
    # their dedicated semantic gate and are never re-judged here.
    executive_gate_active = (
        edition_type in _EXECUTIVE_QUALIFICATION_EDITIONS and bool(operator_review)
    )
    executive_qualified: list[_ArticleCandidate] = []
    executive_rejected = 0
    for candidate in quality_relevant:
        if not executive_gate_active or candidate.is_official_institution:
            executive_qualified.append(candidate)
            continue
        if _executive_qualification(candidate).qualified:
            executive_qualified.append(candidate)
        else:
            executive_rejected += 1

    # R4-R8: an official item can be operator-visible without being eligible
    # for automated publication. Supporting duplicate releases also remain
    # available to the operator but never become a second final card.
    public_review_candidates = [
        candidate for candidate in floor_qualified if candidate.is_public_lane
    ]
    relevant = [
        candidate
        for candidate in executive_qualified
        if not candidate.article.supporting_evidence_only
        and (
            not candidate.is_public_lane
            or candidate.article.tni_brief_eligible
        )
    ]
    relevant.sort(key=_candidate_sort_key, reverse=True)
    public_review_candidates.sort(key=_candidate_sort_key, reverse=True)

    if audit is not None and executive_gate_active:
        # R4-R17 §D — executive materiality accounting (Daily/Review only). None
        # of these consume provider query metadata.
        audit.executive_qualified_count = len(executive_qualified)
        audit.executive_materiality_rejected_count = executive_rejected
        # A provider-query-only relevance signal never reaches the floor by
        # itself (0.5 < SELECTION_RELEVANCE_FLOOR); count rows that had no
        # stronger relevance evidence so query-only starvation stays visible.
        audit.provider_query_only_rejected_count = sum(
            1
            for candidate in candidates
            if candidate.relevance_score < SELECTION_RELEVANCE_FLOOR
            and candidate.relevance_reasons == ("provider_query_only",)
        )
        audit.review_qualified_primary_10 = sum(
            1 for candidate in relevant
            if candidate.article.publisher_tier == "primary_10"
        )
        audit.review_qualified_secondary_3 = sum(
            1 for candidate in relevant
            if candidate.article.publisher_tier == "secondary_3"
        )
        audit.review_qualified_official = sum(
            1 for candidate in relevant if candidate.is_official_institution
        )
        audit.deliverable_major_lead_candidates = sum(
            1 for candidate in relevant
            if lead_source_eligible_tier(
                candidate.article.source, candidate.selected_url
            )
        )

    if audit is not None:
        public_ids = tuple(
            _routing_candidate_id(candidate)
            for candidate in public_review_candidates
        )
        promoted_ids = tuple(
            _routing_candidate_id(candidate)
            for candidate in public_review_candidates
            if candidate.article.main_surface_eligible
        )
        audit.public_institution_lane_count = len(public_review_candidates)
        audit.promoted_public_candidate_count = len(promoted_ids)
        audit.non_promoted_public_candidate_count = (
            len(public_review_candidates) - len(promoted_ids)
        )
        audit.main_candidate_lane_count = sum(
            1
            for candidate in floor_qualified
            if not candidate.is_public_lane
            or candidate.article.main_surface_eligible
        )
        audit.tni_brief_public_candidate_count = sum(
            candidate.article.tni_brief_eligible
            for candidate in public_review_candidates
        )
        audit.tni_report_topic_candidate_count = sum(
            candidate.article.tni_report_topic_eligible
            for candidate in public_review_candidates
        )
        audit.public_candidate_ids = public_ids
        audit.promoted_public_candidate_ids = promoted_ids
        audit.public_candidate_category_map = tuple(
            f"{_routing_candidate_id(candidate)}:{candidate.article.final_category}"
            for candidate in public_review_candidates
        )

    # First establish the complete deterministic outcome (ranking, diversity,
    # selection, headline). This is the production baseline and is returned
    # byte-identically while the committed profile remains inactive.
    deterministic_selected, direct_pool, _direct_supply = (
        _run_deterministic_selection(relevant, limit=limit)
    )
    deterministic_ids = {id(item) for item in deterministic_selected}
    deterministic_order = deterministic_selected + [
        item for item in relevant if id(item) not in deterministic_ids
    ]

    # R4-R7 §5/§6 — memory sees only the already-qualified deterministic
    # ordering. Its bounded reorder can alter an injected active preview, or
    # produce an inactive hypothetical selection, but can never see (and thus
    # never resurrect) a candidate rejected by any deterministic gate.
    memory_runtime = preference_runtime
    if memory_runtime is None:
        memory_runtime = editorial_preference_runtime.default_runtime()
    memory_decisions: list = []
    adjusted_order = deterministic_order
    if deterministic_order:
        product = _memory_product(edition_type, limit)
        memory_decisions = [
            memory_runtime.decide(product, dict(candidate.raw))
            for candidate in deterministic_order
        ]
        order = editorial_preference_runtime.memory_adjusted_order(
            len(deterministic_order),
            [decision.preference_adjustment for decision in memory_decisions],
            group_of=lambda index: int(
                deterministic_order[index].is_public_lane
                and not deterministic_order[index].article.main_surface_eligible
            ),
        )
        adjusted_order = [deterministic_order[index] for index in order]

    shadow_selected, _shadow_direct, _shadow_supply = _run_deterministic_selection(
        adjusted_order,
        limit=limit,
    )
    selection_changed_by_memory = (
        [_memory_candidate_id(item) for item in deterministic_selected[:limit]]
        != [_memory_candidate_id(item) for item in shadow_selected]
    )
    memory_applied = bool(memory_decisions) and memory_runtime.memory_active
    selected = shadow_selected if memory_applied else deterministic_selected

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
        # §11 — honest shortfall: never pad with weak content; make the gap
        # machine-readable instead.
        audit.weak_content_rejected = weak_rejected
        audit.qualified_candidates = len(relevant)
        audit.selected_candidates = min(len(selected), limit)
        audit.selected_public_candidate_count = sum(
            item.is_public_lane for item in selected[:limit]
        )
        audit.official_selected_rows = sum(
            item.is_official_institution for item in selected[:limit]
        )
        target_floor = (
            DAILY_TARGET_MIN_ARTICLES
            if limit <= DAILY_MAX_ARTICLES
            else WEEKLY_TARGET_MIN_ARTICLES
        )
        audit.selection_shortfall = max(
            0, min(limit, target_floor) - min(len(selected), limit)
        )
        audit.selected_ai_core_count = sum(
            1
            for item in selected[:limit]
            if item.ai_centrality_level == ai_centrality.LEVEL_EXPLICIT_AI_CORE
        )
        audit.selected_enabling_infrastructure_count = sum(
            1
            for item in selected[:limit]
            if item.ai_centrality_level
            == ai_centrality.LEVEL_ENABLING_INFRASTRUCTURE_CORE
        )
        if memory_decisions:
            decision_by_candidate = {
                id(candidate): decision
                for candidate, decision in zip(
                    deterministic_order, memory_decisions
                )
            }
            final_ids = {id(item) for item in selected[:limit]}
            audit.memory_profile = memory_runtime.profile_version
            audit.memory_active = memory_runtime.memory_active
            audit.memory_shadow_only = not memory_runtime.memory_active
            audit.memory_runtime_invoked = True
            audit.selected_with_memory_support = sum(
                1
                for item in selected[:limit]
                if decision_by_candidate[id(item)].approved_precedents
            )
            audit.rejected_with_negative_precedent = sum(
                1
                for candidate in deterministic_order
                if id(candidate) not in final_ids
                and decision_by_candidate[id(candidate)].recommendation
                == editorial_preference_runtime.RECOMMEND_AVOID
            )
            audit.selection_changed_by_memory = selection_changed_by_memory
            audit.retrieved_precedent_count = sum(
                len(decision.approved_precedents)
                + len(decision.rejected_precedents)
                + len(decision.near_miss_precedents)
                + len(decision.silver_precedents)
                for decision in memory_decisions
            )
            audit.deterministic_selected_ids = tuple(
                _memory_candidate_id(item)
                for item in deterministic_selected[:limit]
            )
            audit.memory_shadow_selected_ids = tuple(
                _memory_candidate_id(item) for item in shadow_selected
            )
            if selected:
                head_decision = decision_by_candidate[id(selected[0])]
                audit.headline_supported_by_gold_plus = any(
                    ref.evidence_level == "gold_plus"
                    for ref in head_decision.approved_precedents
                )

    return_candidates = selected[:limit]
    if operator_review:
        seen_ids = {id(candidate) for candidate in deterministic_order}
        review_extras = [
            candidate
            for candidate in public_review_candidates
            if id(candidate) not in seen_ids
        ]
        return_candidates = (deterministic_order + review_extras)[:limit]
    selected_identity = {id(candidate) for candidate in selected}
    return [
        replace(
            candidate,
            article=replace(
                candidate.article,
                selection_reason=(
                    _selection_reason(candidate, selected)
                    if id(candidate) in selected_identity
                    else "operator_public_lane_candidate"
                ),
                diversity_contribution=_diversity_contribution(
                    candidate,
                    selected,
                ),
            ),
        )
        for candidate in return_candidates
    ]

def _diversity_contribution(
    candidate: _ArticleCandidate,
    selected: list[_ArticleCandidate],
) -> str:
    category_count = sum(
        1 for item in selected if item.article.category == candidate.article.category
    )
    publisher_count = sum(
        1 for item in selected if item.publisher_key == candidate.publisher_key
    )
    return (
        f"category:{candidate.article.category}#{category_count}"
        f";publisher:{candidate.publisher_key}#{publisher_count}"
    )


def _selection_reason(
    candidate: _ArticleCandidate,
    selected: list[_ArticleCandidate],
) -> str:
    direct_count = sum(1 for item in selected if item.is_direct)
    aggregator_count = sum(1 for item in selected if item.is_aggregator)
    factors = (
        f";decision_relevance={candidate.relevance_score}"
        f";materiality={candidate.materiality_score}"
        f";publisher_tier={candidate.article.publisher_tier or 'unknown'}"
        f";hdec_relevance={candidate.hdec_relevance_score}"
    )
    if candidate.is_naver_direct:
        return (
            "selected_naver_direct_by_relevance_freshness_source_quality"
            f";direct_selected={direct_count};aggregator_selected={aggregator_count}"
            f"{factors}"
        )
    if candidate.is_direct:
        return (
            "selected_publisher_direct_by_relevance_freshness_source_quality"
            f";direct_selected={direct_count};aggregator_selected={aggregator_count}"
            f"{factors}"
        )
    return (
        "selected_aggregator_after_direct_pool_or_importance"
        f";direct_selected={direct_count};aggregator_selected={aggregator_count}"
        f"{factors}"
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


@dataclass(frozen=True)
class DailyImageAssessment:
    """R4-R12 §3 — one hard-gate verdict for a materialized Daily image."""

    valid: bool
    reason: str
    width: int = 0
    height: int = 0


def assess_daily_image_asset(
    payload: bytes,
    *,
    duplicate_article_key: str = "",
) -> DailyImageAssessment:
    """Hard Daily-image gate: dimensional floors, ratio limits, emptiness,
    and unconditional cross-article duplicate rejection.

    Runs on decoded bytes only (the semantic/text-marker layer stays in
    :func:`assess_image_quality`); every Daily candidate image and every
    delivered Daily lead image must pass this gate or be replaced by a
    labeled category fallback."""
    if duplicate_article_key:
        return DailyImageAssessment(
            False, "daily_image_duplicate_across_articles"
        )
    try:
        signals, width, height = _decoded_image_quality_signals(payload)
    except ImageDownloadError as exc:
        return DailyImageAssessment(False, f"daily_{exc.reason}")
    if width < DAILY_IMAGE_MIN_WIDTH:
        return DailyImageAssessment(
            False, "daily_image_below_min_width", width, height
        )
    if height < DAILY_IMAGE_MIN_HEIGHT:
        return DailyImageAssessment(
            False, "daily_image_below_min_height", width, height
        )
    if height and width / height > DAILY_IMAGE_MAX_BANNER_RATIO:
        return DailyImageAssessment(
            False, "daily_image_extreme_banner_ratio", width, height
        )
    if width and height / width > DAILY_IMAGE_MAX_VERTICAL_RATIO:
        return DailyImageAssessment(
            False, "daily_image_extreme_vertical_ratio", width, height
        )
    signal_set = set(signals)
    if "logo_like_transparency" in signal_set:
        return DailyImageAssessment(
            False, "daily_image_transparent_or_empty", width, height
        )
    if {
        "small_effective_content_area",
        "very_low_visual_variation",
    } <= signal_set:
        return DailyImageAssessment(
            False, "daily_image_transparent_or_empty", width, height
        )
    return DailyImageAssessment(True, "daily_image_valid", width, height)


def _raster_content_type(payload: bytes) -> str:
    """Return the canonical supported raster MIME from bytes, never a suffix."""
    if payload.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(payload) >= 12 and payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
        return "image/webp"
    if len(payload) >= 12 and payload[4:8] == b"ftyp" and b"avif" in payload[8:32]:
        return "image/avif"
    return ""


def real_article_photo_validation_reason(
    article: EditorialArticle,
    payload: bytes,
    *,
    local_src: str,
) -> str:
    """Fail-closed byte/path/source validation for one production photo.

    This is the only authority used to mint ``image_real_article_photo=True``.
    A source-kind label or a ``.jpg`` suffix alone has no authority.
    """
    if article.image_fallback_used:
        return "image_fallback_used"
    if article.image_is_category_fallback:
        return "category_fallback_visual"
    if article.image_source_kind not in APPROVED_REAL_ARTICLE_PHOTO_SOURCE_KINDS:
        return "image_source_kind_not_approved"
    if not article.image_quality_accepted:
        return article.image_quality_reason or "image_quality_not_accepted"
    if not local_src or local_src.startswith(("http://", "https://", "data:image/")):
        return "image_local_public_reference_missing"
    if not _raster_content_type(payload):
        return "image_not_supported_raster"
    verdict = assess_daily_image_asset(payload)
    if not verdict.valid:
        return verdict.reason
    return ""


def mark_real_article_photo(
    article: EditorialArticle,
    payload: bytes,
    *,
    local_src: str,
    local_asset: str,
    materialization_reason: str = "publication_asset_materialized",
) -> EditorialArticle:
    """Return an article with a real-photo verdict only after byte validation."""
    reason = real_article_photo_validation_reason(
        article,
        payload,
        local_src=local_src,
    )
    if reason:
        raise EditorialError(f"real article photo rejected: {reason}")
    verdict = assess_daily_image_asset(payload)
    return replace(
        article,
        image_url=local_src,
        image_local_src=local_src,
        image_local_asset=local_asset,
        image_width=verdict.width,
        image_height=verdict.height,
        image_download_status="success",
        image_download_content_type=_raster_content_type(payload),
        image_download_bytes=len(payload),
        image_materialization_reason=materialization_reason,
        image_real_article_photo=True,
        image_is_category_fallback=False,
    )


def editorial_article_id(article: EditorialArticle) -> str:
    value = "\x1f".join((article.selected_url, article.title, article.source))
    return "article-" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]


def image_audit_manifest(
    articles: Iterable[EditorialArticle],
    *,
    image_materialization_failed_count: int = 0,
    image_quality_rejected_count: int = 0,
    failure_reasons: Mapping[str, str] | None = None,
    publication_assets: Iterable[Mapping[str, object]] = (),
) -> dict:
    rows = list(articles)
    real_ids = [
        editorial_article_id(article)
        for article in rows
        if article.image_real_article_photo
    ]
    fallback_ids = [
        editorial_article_id(article)
        for article in rows
        if article.image_fallback_used
        or article.image_is_category_fallback
        or not article.image_real_article_photo
    ]
    return {
        "article_count": len(rows),
        "real_article_photo_count": len(real_ids),
        "fallback_visual_count": len(fallback_ids),
        "image_materialization_failed_count": int(
            image_materialization_failed_count
        ),
        "image_quality_rejected_count": int(image_quality_rejected_count),
        "real_photo_article_ids": real_ids,
        "fallback_article_ids": fallback_ids,
        "failure_reasons": dict(failure_reasons or {}),
        "publication_image_assets": [dict(item) for item in publication_assets],
    }


def production_image_gate_error(manifest: object) -> str:
    """Return a machine reason when a non-empty publication is not all-real."""
    if not isinstance(manifest, Mapping):
        return "image_manifest_missing"
    required = {
        "article_count",
        "real_article_photo_count",
        "fallback_visual_count",
        "image_materialization_failed_count",
        "image_quality_rejected_count",
    }
    if not required <= set(manifest):
        return "image_manifest_fields_missing"
    values = {name: manifest.get(name) for name in required}
    if any(type(value) is not int or value < 0 for value in values.values()):
        return "image_manifest_counter_malformed"
    article_count = values["article_count"]
    if article_count == 0:
        return ""
    if values["real_article_photo_count"] != article_count:
        return "real_article_photo_coverage_incomplete"
    if values["fallback_visual_count"] != 0:
        return "fallback_visual_present"
    return ""


def production_rendered_image_gate_error(
    html: str,
    *,
    edition_type: str,
    edition_key: str,
    manifest: object,
) -> str:
    """Bind every rendered image element to one exact publication raster."""
    counter_error = production_image_gate_error(manifest)
    if counter_error:
        return counter_error
    if not isinstance(manifest, Mapping):
        return "image_manifest_missing"
    article_count = int(manifest.get("article_count") or 0)
    if article_count == 0:
        return ""
    if edition_type not in {"daily", "weekly"}:
        return "image_manifest_product_malformed"
    assets = manifest.get("publication_image_assets")
    if not isinstance(assets, list) or len(assets) != article_count:
        return "publication_image_asset_count_mismatch"
    prefix = f"editorial/{edition_type}/assets/{edition_key}/"
    expected_sources: list[str] = []
    for asset in assets:
        if not isinstance(asset, Mapping):
            return "publication_image_asset_record_malformed"
        relative = str(asset.get("relative_path") or "")
        filename = relative.removeprefix(prefix)
        if (
            not relative.startswith(prefix)
            or not filename
            or "/" in filename
            or "\\" in filename
        ):
            return "publication_image_asset_path_mismatch"
        expected_sources.append(f"assets/{edition_key}/{filename}")
    rendered_sources = re.findall(
        r'<img\b[^>]*\bsrc=["\']([^"\']+)["\']',
        str(html or ""),
        flags=re.IGNORECASE,
    )
    if sorted(rendered_sources) != sorted(expected_sources):
        return "rendered_image_asset_identity_mismatch"
    return ""


def daily_category_fallback_asset(category: str) -> Path:
    """Committed deterministic category visual for one Brief category."""
    name = DAILY_CATEGORY_FALLBACK_ASSETS.get(
        " ".join(str(category or "").split()), DAILY_CATEGORY_FALLBACK_GENERIC
    )
    return DAILY_CATEGORY_FALLBACK_DIR / name


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
    preference_runtime: (
        "editorial_preference_runtime.EditorialPreferenceRuntime | None"
    ) = None,
    edition_type: str | None = None,
    operator_review: bool = False,
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

        duplicate_clusters, supporting_ids = _public_media_duplicate_groups(
            candidates
        )
        deduped = _deduplicate_article_candidates(
            candidates,
            preserve_public_supporting_duplicates=operator_review,
            audit=audit,
        )
        if audit is not None:
            audit.duplicate_official_media_event_clusters = duplicate_clusters
            audit.public_supporting_evidence_ids = supporting_ids
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
            preference_runtime=preference_runtime,
            edition_type=edition_type,
            operator_review=operator_review,
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


def _daily_fallback_fingerprint(article: EditorialArticle) -> str:
    """Stable per-article fingerprint for the deterministic category fallback.

    Deliberately title-inclusive: two unrelated articles in one category (and
    even two rows that share a publisher_article_url/selected_url) must resolve
    to distinct fingerprints, so their fallback visuals never collide."""
    return "|".join(
        (
            " ".join((article.title or "").split()),
            (article.source or "").strip(),
            str(article.published_at or ""),
            _article_quality_key(article),
        )
    )


def _render_article_fallback_variant(base_payload: bytes, fingerprint: str) -> bytes:
    """R4-R12 §3 — deterministic per-article variation of a committed category
    base visual.

    Overlays a fingerprint-seeded pixel band on the committed base PNG. The
    baked Korean '카테고리 이미지' label and the no-trademark guarantee are
    preserved (pixel-only overlay, no runtime font), while unrelated articles
    receive byte-distinct assets. Deterministic byte-for-byte for a fixed
    Pillow build; the overlay only adds pixel variation, so the hard-gate
    emptiness signals can never fire because of it. Falls back to the base
    bytes verbatim when Pillow is unavailable."""
    if Image is None:
        return base_payload
    from PIL import ImageDraw

    seed = hashlib.sha256(fingerprint.encode("utf-8")).digest()
    with Image.open(BytesIO(base_payload)) as opened:
        image = opened.convert("RGB")
    draw = ImageDraw.Draw(image)
    width, height = image.size
    columns = len(seed)
    col_w = max(1, width // columns)
    for index, value in enumerate(seed):
        x0 = index * col_w
        x1 = width if index == columns - 1 else x0 + col_w
        bar_h = 6 + (value % 18)
        shade = (
            40 + (value * 3) % 180,
            60 + (value * 7) % 170,
            90 + (value * 11) % 150,
        )
        draw.rectangle((x0, 0, x1, bar_h), fill=shade)
    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _materialize_daily_category_fallback(
    article: EditorialArticle,
    *,
    assets_dir: Path,
    target_html_dir: Path,
    counters: ImageMaterializationCounters,
    assets_by_digest: dict[str, Path],
    failure_reason: str,
) -> EditorialArticle:
    """R4-R12 §3 — replace a missing/invalid article image with a deterministic
    per-article category visual. Never leaves a blank card; the visual is
    explicitly labeled as a category image, never an article photograph, and
    two unrelated same-category articles receive distinct assets."""
    base_payload = daily_category_fallback_asset(article.category).read_bytes()
    payload = _render_article_fallback_variant(
        base_payload, _daily_fallback_fingerprint(article)
    )
    digest = hashlib.sha256(payload).hexdigest()
    asset_path = assets_by_digest.get(digest)
    if asset_path is None:
        asset_path = assets_dir / (
            f"{digest[:PREVIEW_IMAGE_ASSET_DIGEST_CHARS]}.png"
        )
        atomic_write_bytes(asset_path, payload)
        assets_by_digest[digest] = asset_path
        counters.image_assets_materialized += 1
    local_src = _preview_image_relative_src(asset_path, target_html_dir)
    counters.category_fallbacks_materialized += 1
    verdict = assess_daily_image_asset(payload)
    return replace(
        article,
        image_url=local_src,
        image_remote_url="",
        image_source_kind="category_fallback",
        image_source_page_url="",
        image_width=verdict.width or None,
        image_height=verdict.height or None,
        image_fallback_used=True,
        image_reason=failure_reason or "daily_category_fallback",
        image_download_status="category_fallback_materialized",
        image_download_content_type="image/png",
        image_download_bytes=len(payload),
        image_local_asset=asset_path.name,
        image_local_src=local_src,
        image_duplicate_asset_reused=False,
        image_materialization_reason="daily_category_fallback",
        image_quality_accepted=True,
        image_quality_reason="daily_category_fallback_asset",
        image_is_category_fallback=True,
        image_real_article_photo=False,
    )


def materialize_preview_images(
    articles: Iterable[EditorialArticle],
    preview_root: Path,
    *,
    html_dir: Path | None = None,
    downloader: Callable[..., ImageDownload] | None = None,
    opener: object | None = None,
    daily: bool = False,
) -> tuple[list[EditorialArticle], ImageMaterializationCounters]:
    """Download browser-loadable image bytes into a bounded /tmp preview bundle.

    ``daily=True`` activates the R4-R12 §3 Daily image contract: the hard
    dimensional/ratio/emptiness gate, unconditional cross-article duplicate
    rejection, and per-article category-fallback materialization (no blank
    cards). ``daily=False`` (the default, used by non-Daily / generic callers)
    preserves the original semantic-quality-only behavior exactly, so a small
    but legitimate photo is not rejected by a Daily-only dimensional floor."""
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
            if daily:
                materialized.append(
                    _materialize_daily_category_fallback(
                        article,
                        assets_dir=assets_dir,
                        target_html_dir=target_html_dir,
                        counters=counters,
                        assets_by_digest=assets_by_digest,
                        failure_reason=article.image_reason or "image_url_missing",
                    )
                )
            else:
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
                # R4-R12 §3 — cross-article duplicate rejection (Daily only):
                # one image may represent one article only (category fallbacks
                # are per-article deterministic, so they never collide here).
                duplicate_owner = article_key_by_digest.get(digest, "")
                if (
                    daily
                    and duplicate_owner
                    and duplicate_owner != _article_quality_key(article)
                ):
                    counters.daily_duplicate_image_rejections += 1
                    counters.image_candidate_failures += 1
                    quality_rejected_for_article = True
                    last_failure = ImageDownloadError(
                        "daily_image_duplicate_across_articles",
                        status=download.status,
                        content_type=download.content_type,
                        byte_size=len(download.payload),
                    )
                    attempts.append(
                        ImageCandidateAttempt(
                            source_kind=candidate.source_kind,
                            host=_url_host(remote_url),
                            status="rejected",
                            reason="daily_image_duplicate_across_articles",
                            content_type=download.content_type.split(";", 1)[0].strip(),
                            byte_size=len(download.payload),
                            byte_validation_status="passed",
                            quality_accepted=False,
                            quality_rejection_reason=(
                                "daily_image_duplicate_across_articles"
                            ),
                        )
                    )
                    continue
                # R4-R12 §3 — hard dimensional/emptiness gate ahead of the
                # heuristic quality layer (Daily only): the 250x24 banner class
                # can never pass on a single missing heuristic signal again.
                hard_verdict = (
                    assess_daily_image_asset(download.payload) if daily else None
                )
                if hard_verdict is not None and not hard_verdict.valid:
                    counters.daily_hard_gate_rejections += 1
                    counters.image_quality_rejections += 1
                    counters.image_candidate_failures += 1
                    quality_rejected_for_article = True
                    last_failure = ImageDownloadError(
                        hard_verdict.reason,
                        status=download.status,
                        content_type=download.content_type,
                        byte_size=len(download.payload),
                    )
                    attempts.append(
                        ImageCandidateAttempt(
                            source_kind=candidate.source_kind,
                            host=_url_host(remote_url),
                            status="rejected",
                            reason=hard_verdict.reason,
                            content_type=download.content_type.split(";", 1)[0].strip(),
                            byte_size=len(download.payload),
                            byte_validation_status="passed",
                            quality_accepted=False,
                            quality_rejection_reason=hard_verdict.reason,
                        )
                    )
                    continue
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
                if (
                    candidate.source_kind
                    not in APPROVED_REAL_ARTICLE_PHOTO_SOURCE_KINDS
                ):
                    last_failure = ImageDownloadError(
                        "image_source_kind_not_approved",
                        status=download.status,
                        content_type=download.content_type,
                        byte_size=len(download.payload),
                    )
                    counters.image_quality_rejections += 1
                    counters.image_candidate_failures += 1
                    quality_rejected_for_article = True
                    attempts.append(
                        ImageCandidateAttempt(
                            source_kind=candidate.source_kind,
                            host=_url_host(remote_url),
                            status="rejected",
                            reason="image_source_kind_not_approved",
                            content_type=download.content_type.split(";", 1)[0].strip(),
                            byte_size=len(download.payload),
                            byte_validation_status="passed",
                            quality_accepted=False,
                            quality_rejection_reason="image_source_kind_not_approved",
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
                    image_width=(hard_verdict.width if hard_verdict else 0) or candidate.width,
                    image_height=(hard_verdict.height if hard_verdict else 0) or candidate.height,
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
                    image_real_article_photo=True,
                    image_is_category_fallback=False,
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
        if daily:
            # R4-R12 §3 — every remote candidate failed the download, quality,
            # or hard gate: materialize the labeled per-article deterministic
            # category visual instead of a blank card. The last failure reason
            # stays on the record.
            materialized.append(
                _materialize_daily_category_fallback(
                    replace(article, image_candidate_attempts=tuple(attempts)),
                    assets_dir=assets_dir,
                    target_html_dir=target_html_dir,
                    counters=counters,
                    assets_by_digest=assets_by_digest,
                    failure_reason=last_failure.reason,
                )
            )
            continue
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
                image_real_article_photo=False,
                image_candidate_attempts=tuple(attempts),
            )
        )
    return materialized, counters


def _template(name: str) -> str:
    # newline="" disables universal-newline translation: the sealed weekly T&I
    # template is byte-exact CRLF (extracted from the immutable reference), and
    # LF templates pass through unchanged.
    with (config.TEMPLATES_DIR / name).open(encoding="utf-8", newline="") as handle:
        return handle.read()


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


_BRIEF_FALLBACK_IMAGE = (
    "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' "
    "viewBox='0 0 640 260'%3E%3Crect width='640' height='260' "
    "fill='%23002C5F'/%3E%3Ccircle cx='520' cy='30' r='170' "
    "fill='%23004B93'/%3E%3Ccircle cx='80' cy='250' r='150' "
    "fill='%230D9488' fill-opacity='.55'/%3E%3C/svg%3E"
)


def _safe_brief_image_src(article: EditorialArticle) -> str:
    """Local materialized asset or data URI only; '' when nothing safe exists.

    Briefs never hotlink a remote image."""
    candidate = str(article.image_local_src or article.image_url or "").strip()
    safe_materialized = (
        re.fullmatch(r"\.\./assets/images/[A-Za-z0-9][A-Za-z0-9._-]*", candidate)
        is not None
        or re.fullmatch(
            r"assets/(?:\d{4}-\d{2}-\d{2}|\d{4}-W\d{2})/"
            r"[A-Za-z0-9][A-Za-z0-9._-]*",
            candidate,
        )
        is not None
        or re.fullmatch(
            r"\.\./review/\d{4}-\d{2}-\d{2}/assets/images/"
            r"[A-Za-z0-9][A-Za-z0-9._-]*",
            candidate,
        )
        is not None
    )
    if candidate.startswith("data:image/") or safe_materialized:
        return escape(candidate, quote=True)
    return ""


def _reference_image(article: EditorialArticle, *, hero: bool = False) -> str:
    source = _safe_brief_image_src(article) or _BRIEF_FALLBACK_IMAGE
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


def _implication_inline_html(article: EditorialArticle) -> str:
    """§12 why-it-matters text: editor override wins over the generated one."""
    if article.implication_html:
        return sanitize_editorial_inline_html(article.implication_html)
    if article.executive_implication:
        return escape(article.executive_implication)
    return ""


def _editor_summary_block(article: EditorialArticle) -> str:
    """§12 Editor's Summary body: factual base + HDEC implication paragraphs.

    A human-approved ``summary_html`` controls the exact content; the
    implication paragraph is appended only from the generated evidence-based
    implication or an explicit editor ``implication_html`` override — never
    invented facts."""
    summary = _daily_summary_html(article)
    implication = _implication_inline_html(article)
    if article.summary_html and not article.implication_html:
        return f"<p>{summary}</p>"
    if not implication:
        return f"<p>{summary}</p>"
    return (
        f"<p>{summary}</p>"
        f'<p class="implication">현대건설 시사점 — {implication}</p>'
    )


def _card_summary_html(article: EditorialArticle) -> str:
    """Card summary in the T&I pattern: factual text, then an em-dash insight."""
    summary = _daily_summary_html(article)
    implication = _implication_inline_html(article)
    if article.summary_html and not article.implication_html:
        return summary
    if not implication:
        return summary
    return f"{summary} — {implication}"


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
        f"{_editor_summary_block(article)}"
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
        f'<h3>{escape(article.title)}</h3><p class="sum">{_card_summary_html(article)}</p>'
        '<div class="src" style="margin-top:14px;padding-top:10px;border-top:1px solid '
        '#EEF0F4;font-size:11.5px;color:#9CA3B0;font-weight:600;">출처 '
        f"{_article_source_anchor(article)}</div></div></article>"
    )


def _brief_styles() -> str:
    return _template("editorial_brief.css")


def _taxonomy_html() -> str:
    return (
        '<div class="taxonomy" data-role="information-taxonomy">'
        '<div class="tax-row"><span class="tax-name"><span class="tax-invest">●</span> 투자·산업</span>'
        '<span class="tax-desc">자본과 인프라의 흐름 — 투자, 시장 재편, 정책·글로벌 동향</span></div>'
        '<div class="tax-row"><span class="tax-name"><span class="tax-corp">●</span> 기업동향</span>'
        '<span class="tax-desc">선도 기업과 경쟁사의 AI 도입·전환 전략</span></div>'
        '<div class="tax-row"><span class="tax-name"><span class="tax-tech">●</span> 기술정보</span>'
        '<span class="tax-desc">신규 AI 모델·제품·기술의 등장과 경영 영향</span></div></div>'
    )


def _brief_footer(edition_type: str, key: str, coverage: CoverageWindow) -> str:
    label = "DAILY BRIEF" if edition_type == "daily" else "WEEKLY BRIEF"
    return (
        '<footer data-role="publication-footer">'
        '<p class="pub">워크이노베이션센터 | AI디자인랩</p>'
        '<p class="note">공신력 있는 외부 보도의 제목·게시시각·제공 요약을 바탕으로 편집했으며 세부 내용은 게시자 원문 기준입니다.</p>'
        f'<div class="meta">AI 경영 T&amp;I · {label} · {escape(key)} · {escape(coverage.label())}</div>'
        '</footer>'
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


# ---------------------------------------------------------------------------
# R4-R9C — exact-edition identity for the Daily Teams operator action.
# The edition manifest is the immutable, non-sensitive editor-load record: it
# binds date, coverage window, ordered articles (titles / factual summaries /
# categories / publishers / publisher-direct URLs), the Editor's Summary, the
# review state and the publication digest. Its integrity digest names the
# revision, and the edition_id embeds that revision, so a republished date
# mints a new id instead of overwriting an older edition.
# ---------------------------------------------------------------------------

DAILY_EDITOR_LINK_LABEL = "Daily Brief 편집기에서 열기"
DAILY_PUBLISHED_LINK_LABEL = "게시된 Daily Brief 보기"
DAILY_EMPTY_STATUS_TEXT = "오늘 기준을 충족한 임원용 AI 핵심 뉴스가 없습니다."
EDITION_MANIFEST_IDENTITY_FIELDS = ("revision", "edition_id", "integrity")


def canonical_edition_manifest_bytes(payload: Mapping) -> bytes:
    """Digest input: sorted-key compact JSON of the manifest minus identity fields."""
    core = {
        key: value
        for key, value in payload.items()
        if key not in EDITION_MANIFEST_IDENTITY_FIELDS
    }
    return json.dumps(
        core, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def build_daily_edition_manifest(
    *,
    edition_key: str,
    coverage: CoverageWindow,
    articles: list[EditorialArticle],
    html_sha256: str,
    dated_url: str,
    latest_url: str,
    run_at: datetime,
    review_mode: str,
    review_decision: str,
) -> dict:
    manifest = {
        "version": 2,
        "product": "daily",
        "edition_key": edition_key,
        "coverage_start": coverage.start.isoformat(),
        "coverage_end": coverage.end.isoformat(),
        "published_run_at": _as_kst(run_at).isoformat(timespec="seconds"),
        "review_mode": str(review_mode or "not_applicable"),
        "review_decision": str(review_decision or "not_applicable"),
        "edition_status": "nonempty" if articles else "empty",
        "article_count": len(articles),
        "headline_title": articles[0].title if articles else "",
        "editor_summary": articles[0].summary if articles else DAILY_EMPTY_STATUS_TEXT,
        "articles": [
            {
                "position": index,
                "headline": index == 1,
                "title": article.title,
                "summary": article.summary,
                "category": article.category,
                "publisher": article.source,
                "publisher_url": article.selected_url,
                "published_at": article.published_at.astimezone(KST).isoformat(
                    timespec="seconds"
                ),
            }
            for index, article in enumerate(articles, start=1)
        ],
        "publication": {
            "dated_url": dated_url,
            "latest_url": latest_url,
            "html_sha256": html_sha256,
            "publication_state": "published",
        },
    }
    digest = hashlib.sha256(canonical_edition_manifest_bytes(manifest)).hexdigest()
    manifest["revision"] = digest[:16]
    manifest["edition_id"] = f"daily-{edition_key}-{digest[:16]}"
    manifest["integrity"] = {
        "algorithm": "sha256",
        "canonicalization": "sorted-compact-json-utf8",
        "digest": digest,
    }
    return manifest


def verify_daily_edition_manifest(manifest: object) -> str:
    """Fail-closed manifest validation; returns "" when valid, else the reason."""
    if not isinstance(manifest, Mapping):
        return "manifest_not_object"
    edition_id = str(manifest.get("edition_id") or "")
    embedded_key = public_url_contract.parse_daily_edition_id(edition_id)
    if not embedded_key:
        return "edition_id_malformed"
    if manifest.get("product") != "daily" or manifest.get("edition_key") != embedded_key:
        return "identity_mismatch"
    version = manifest.get("version")
    articles = manifest.get("articles")
    if not isinstance(articles, list):
        return "articles_malformed"
    if version == 2:
        status = manifest.get("edition_status")
        count = manifest.get("article_count")
        if type(count) is not int or count != len(articles):
            return "article_count_mismatch"
        if status not in {"empty", "nonempty"}:
            return "edition_status_malformed"
        if (status == "empty") != (count == 0):
            return "edition_status_mismatch"
    elif version != 1 or not articles:
        return "manifest_version_unsupported"
    integrity = manifest.get("integrity")
    if not isinstance(integrity, Mapping):
        return "integrity_missing"
    digest = str(integrity.get("digest") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        return "digest_malformed"
    if manifest.get("revision") != digest[:16] or not edition_id.endswith(digest[:16]):
        return "revision_mismatch"
    recomputed = hashlib.sha256(canonical_edition_manifest_bytes(manifest)).hexdigest()
    if recomputed != digest:
        return "digest_mismatch"
    return ""


def render_daily(
    articles: list[EditorialArticle],
    *,
    run_at: datetime,
    root_url: str,
    review_mode: str = "not_applicable",
    review_decision: str = "not_applicable",
    editor_console_available: bool = False,
    lead_source_gate: bool = False,
) -> RenderedEdition:
    if lead_source_gate:
        # R4-R10 — drop long-tail/specialist leads from the delivered brief.
        # An empty result is a graceful "prefer zero" skip, not a crash.
        articles = filter_lead_source_eligible(articles)
    articles = articles[:DAILY_MAX_ARTICLES]
    key = edition_key("daily", run_at)
    coverage = daily_coverage(run_at)
    dated_url, latest_url = public_urls(root_url, "daily", key)
    headline = articles[0] if articles else None
    if headline is not None and headline.ai_centrality_level not in DAILY_HEADLINE_ALLOWED_CENTRALITY:
        raise EditorialError(
            "daily headline is not AI-central: level="
            f"{headline.ai_centrality_level or 'unknown'}"
        )
    html = _fill(
        _template("editorial_daily.html"),
        {
            "EDITION_KEY": escape(key, quote=True),
            "PAGE_TITLE": escape(f"AI 경영 T&I Daily Brief · {key}"),
            "EDITION_LABEL": escape(key),
            "COVERAGE_LABEL": escape(coverage.label()),
            "BRIEF_STYLES": _brief_styles(),
            "HEADLINE_HTML": (
                _daily_headline(headline)
                if headline is not None
                else (
                    '<section class="hero empty-edition" data-role="headline" '
                    'data-edition-status="empty" style="border-radius:22px;'
                    'background:#eef3f8;color:#002c5f;padding:34px 30px">'
                    f'<h2 style="margin:0;line-height:1.45">{escape(DAILY_EMPTY_STATUS_TEXT)}</h2>'
                    '</section><div class="ednote"><h3 class="ed-k">Editor\'s Summary</h3>'
                    '<p>기준을 낮추거나 기사를 채워 넣지 않았습니다. 해당 수집 범위의 '
                    '정직한 빈 에디션입니다.</p></div>'
                )
            ),
            "ARTICLE_CARDS_HTML": (
                "".join(_daily_card(item) for item in articles[1:6])
                or (
                    '<p class="empty">추가로 선정된 주요 기사 없음</p>'
                    if articles
                    else '<p class="empty">오늘의 브리핑에 포함할 기사가 없습니다.</p>'
                )
            ),
            "TAXONOMY_HTML": _taxonomy_html(),
            "FOOTER_HTML": _brief_footer("daily", key, coverage),
        },
    )
    bound_articles = articles
    edition_manifest = build_daily_edition_manifest(
        edition_key=key,
        coverage=coverage,
        articles=bound_articles,
        html_sha256=hashlib.sha256(html.encode("utf-8")).hexdigest(),
        dated_url=dated_url,
        latest_url=latest_url,
        run_at=run_at,
        review_mode=review_mode,
        review_decision=review_decision,
    )
    edition_id = edition_manifest["edition_id"]
    # The operator action is emitted only when the dated Review Console for
    # this edition is known to exist — a missing editor identity never mints a
    # guessed or broken editor link. R4-R11: the production caller
    # (run_editorial_briefing.run_publish) additionally fails the whole Daily
    # publication closed when editor_url is empty or the editor cannot be
    # reconstructed, so a delivered Daily always carries the editor CTA.
    editor_url = (
        public_url_contract.daily_editor_console_url(edition_id, root_url=root_url)
        if editor_console_available
        else ""
    )
    text_lines = [
        f"HDEC AI Daily Brief · {key}",
        headline.title if headline is not None else DAILY_EMPTY_STATUS_TEXT,
        "",
    ]
    if editor_url:
        text_lines.append(f"{DAILY_EDITOR_LINK_LABEL}: {editor_url}")
    text_lines.extend(
        [
            f"{DAILY_PUBLISHED_LINK_LABEL}: {dated_url}",
            f"전체 뉴스 대시보드 보기: {public_url_contract.CANONICAL_DASHBOARD_URL}",
        ]
    )
    teams_text = "\n".join(text_lines)
    teams_html = _teams_html(
        "HDEC AI Daily Brief",
        key,
        coverage,
        [(
            "오늘의 헤드라인" if headline is not None else "오늘의 상태",
            escape(headline.title if headline is not None else DAILY_EMPTY_STATUS_TEXT),
        )],
        dated_url,
        DAILY_PUBLISHED_LINK_LABEL,
        leading_actions=(
            ((DAILY_EDITOR_LINK_LABEL, editor_url),) if editor_url else ()
        ),
    )
    return RenderedEdition(
        "daily", key, coverage, html, dated_url, latest_url, teams_text, teams_html,
        "daily_empty_status" if not articles else "daily",
        headline.title if headline is not None else DAILY_EMPTY_STATUS_TEXT,
        len(articles),
        edition_id=edition_id,
        editor_url=editor_url,
        edition_manifest=edition_manifest,
        image_audit=image_audit_manifest(articles),
    )


# ---------------------------------------------------------------------------
# D7-AK-6E R4-R6 §14 — Weekly T&I exact-reference rendering.
# templates/editorial_weekly_tni.html is the immutable reference document with
# only its dynamic content islands cut out; re-injecting the reference's own
# island content reproduces the reference byte-for-byte (verified by
# scripts/verify_weekly_tni_reference_parity.py, incl. pixel diff = 0).
# ---------------------------------------------------------------------------

_TNI_CATEGORY_STYLE = {
    "투자·산업": "--cat:var(--c-invest);--tint:#F7F0E2",
    "기업동향": "--cat:var(--c-corp);--tint:#E6F5F3",
    "기술정보": "--cat:var(--c-tech);--tint:#F1F1FD",
}


def tni_issue_labels(run_at: datetime) -> tuple[str, str]:
    """Korean issue label pair for the T&I masthead/meta line.

    The anchor Wednesday names the issue: e.g. anchor 2026-07-15 →
    ("2026년 7월 3주차", "2026년 7월 3주차 (2026.07.15)")."""
    anchor = weekly_anchor_date(run_at)
    week_of_month = (anchor.day + 6) // 7
    label = f"{anchor.year}년 {anchor.month}월 {week_of_month}주차"
    return label, f"{label} ({anchor:%Y.%m.%d})"


def _tni_source_anchor(article: EditorialArticle) -> str:
    date_label = f"{article.published_at.astimezone(KST):%m.%d}"
    return (
        f'<a href="{escape(article.selected_url, quote=True)}" target="_blank" '
        f'rel="noopener noreferrer">{escape(article.source)}'
        f'<span class="dt" style="font-size: 8.6pt;">{date_label}</span></a>'
    )


def _tni_hero_image(article: EditorialArticle) -> str:
    source = _safe_brief_image_src(article)
    if not source:
        return ""
    return (
        '<img alt="" aria-hidden="true" style="position:absolute;inset:0;'
        "width:100%;height:100%;object-fit:cover;object-position:center 40%;"
        f'z-index:0;" src="{source}">'
    )


def _tni_thumb_image(article: EditorialArticle) -> str:
    source = _safe_brief_image_src(article) or _BRIEF_FALLBACK_IMAGE
    return (
        f'<img alt="{escape(article.title, quote=True)}" '
        'style="position:absolute;inset:0;width:100%;height:100%;'
        f'object-fit:cover;object-position:center;display:block;" src="{source}">'
    )


def _tni_ednote_html(article: EditorialArticle) -> str:
    paragraph_style = (
        "font-size: 13.1pt; line-height: 1.5; margin-top: 0px; margin-bottom: 0px;"
    )
    base = f'<p style="{paragraph_style}">{_daily_summary_html(article)}</p>'
    implication = _implication_inline_html(article)
    if article.summary_html and not article.implication_html:
        return base
    if not implication:
        return base
    return base + (
        '<p style="font-size: 13.1pt; line-height: 1.5; margin-top: 10px; '
        f'margin-bottom: 0px;">현대건설 시사점 — {implication}</p>'
    )


def _tni_card(article: EditorialArticle) -> str:
    style = _TNI_CATEGORY_STYLE.get(article.category, _TNI_CATEGORY_STYLE["기술정보"])
    # §13 — machine-readable selection rationale rides invisibly on the card
    # (island content, so the sealed reference shell stays byte-identical).
    rationale = escape(article.selection_reason, quote=True)
    return (
        f'<article class="card" data-selection-rationale="{rationale}" '
        f'style="{style};display:grid;grid-template-columns:'
        "128px 1fr;background:#fff;border:1px solid rgba(16,18,24,.10);"
        'border-radius:16px;overflow:hidden;margin:0 0 12px;">\r\n'
        '<div class="thumb" style="position:relative;min-height:128px;'
        f'background:#1a1a2e;overflow:hidden;">{_tni_thumb_image(article)}</div>\r\n'
        '<div class="card-body"><span class="chip" style="font-size: 8.3pt;">'
        f'{escape(article.category)}</span><h3 style="line-height: 1.5;">'
        f"{escape(article.title)}</h3>\r\n"
        '<p class="sum" style="font-size: 11.6pt; line-height: 1.5; margin-top: 0px; '
        f'margin-bottom: 0px;">{_card_summary_html(article)}</p>\r\n'
        '<div class="src" style="margin-top: 14px; padding-top: 10px; border-top: '
        "1px solid rgb(238, 240, 244); font-size: 11.5px; color: rgb(156, 163, 176); "
        'font-weight: 600; line-height: 1.5;">출처 '
        f"{_tni_source_anchor(article)}</div></div></article>"
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
    headline = articles[0]
    issue, issue_dated = tni_issue_labels(run_at)
    cards = "\r\n".join(
        _tni_card(item) for item in articles[1:WEEKLY_MAX_ARTICLES]
    )
    # Invisible contract markers ride inside the cards island so the sealed
    # reference shell stays byte-identical for the parity fixture.
    contract_comment = (
        f'<!-- data-brief-contract="AI_TNI_EXECUTIVE_V1" '
        f'data-edition-key="{escape(key, quote=True)}" '
        f'coverage="{escape(coverage.label(), quote=True)}" -->'
    )
    cards = f"{cards}\r\n{contract_comment}" if cards else contract_comment
    html = _fill(
        _template("editorial_weekly_tni.html"),
        {
            "TNI_TITLE_ISSUE": escape(issue_dated),
            "TNI_ISSUE_LABEL": escape(issue),
            "TNI_HERO_IMAGE": _tni_hero_image(headline),
            "TNI_HERO_TITLE": escape(headline.title),
            "TNI_HERO_CATEGORY": escape(headline.category),
            "TNI_EDNOTE_HTML": _tni_ednote_html(headline),
            "TNI_EDNOTE_SOURCE": _tni_source_anchor(headline),
            "TNI_CARDS": cards,
            "TNI_META_ISSUE": escape(issue_dated),
        },
    )
    text_lines = [
        f"[AI 경영 T&I Weekly Brief] {key}",
        headline.title,
        "",
        f"이번 주 Weekly Brief 보기: {public_url_contract.WEEKLY_LATEST_URL}",
        f"전체 뉴스 대시보드 보기: {public_url_contract.CANONICAL_DASHBOARD_URL}",
    ]
    teams_text = "\n".join(text_lines)
    teams_html = _teams_html(
        "AI 경영 T&I Weekly Brief",
        key,
        coverage,
        [("이번 주 헤드라인", escape(headline.title))],
        public_url_contract.WEEKLY_LATEST_URL,
        "이번 주 Weekly Brief 보기",
    )
    return RenderedEdition(
        "weekly", key, coverage, html, dated_url, latest_url, teams_text, teams_html,
        mode, headline.title, len(articles),
        image_audit=image_audit_manifest(articles),
    )


def _teams_html(
    heading: str,
    key: str,
    coverage: CoverageWindow,
    sections: list[tuple[str, str]],
    public_url: str,
    cta: str,
    *,
    leading_actions: tuple[tuple[str, str], ...] = (),
) -> str:
    blocks = "".join(
        f"<p><strong>{escape(label)}</strong><br>{body}</p>" for label, body in sections
    )
    button_style = (
        "display:inline-block;margin:4px 8px 4px 0;padding:11px 16px;"
        "border-radius:8px;background:#002c5f;color:#fff;text-decoration:none;font-weight:700"
    )
    dashboard_url = public_url_contract.CANONICAL_DASHBOARD_URL
    leading = "".join(
        f'<a href="{escape(url, quote=True)}" target="_blank" rel="noopener noreferrer" '
        f'style="{button_style}">{escape(label)}</a>'
        for label, url in leading_actions
    )
    return (
        '<!doctype html><html lang="ko"><body style="font-family:Segoe UI,Malgun Gothic,Arial,sans-serif;max-width:640px;color:#101218">'
        f"<h2>{escape(heading)}</h2><p>{escape(key)}<br>{escape(coverage.label())}</p>{blocks}"
        f'<p>{leading}<a href="{escape(public_url, quote=True)}" target="_blank" rel="noopener noreferrer" style="{button_style}">{escape(cta)}</a>'
        f'<a href="{escape(dashboard_url, quote=True)}" target="_blank" rel="noopener noreferrer" style="{button_style}">전체 뉴스 대시보드 보기</a></p>'
        '</body></html>'
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
    selection_audit: SelectionAuditCounters | None = None,
    preference_runtime: (
        "editorial_preference_runtime.EditorialPreferenceRuntime | None"
    ) = None,
    review_mode: str = "not_applicable",
    review_decision: str = "not_applicable",
    editor_console_available: bool = False,
    lead_source_gate: bool = False,
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
        selection_audit=selection_audit,
        preference_runtime=preference_runtime,
        edition_type=edition_type,
    )
    if edition_type == "daily":
        return render_daily(
            articles,
            run_at=run_at,
            root_url=root_url,
            review_mode=review_mode,
            review_decision=review_decision,
            editor_console_available=editor_console_available,
            lead_source_gate=lead_source_gate,
        )
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
    empty_daily = edition.edition_type == "daily" and edition.article_count == 0
    if edition.article_count < 1 and not empty_daily:
        raise EditorialError("empty edition")

    teams_anchors = re.findall(r"<a\b[^>]*>", edition.teams_html)
    dashboard_href = (
        f'href="{escape(public_url_contract.CANONICAL_DASHBOARD_URL, quote=True)}"'
    )
    if edition.edition_type == "daily":
        # R4-R9C — the reader action targets the immutable dated publication,
        # and the optional leading operator action targets the validated
        # exact-edition editor deep link. The mutable latest URL is never a
        # Daily Teams action.
        if public_url_contract.parse_daily_edition_id(edition.edition_id) != (
            edition.edition_key
        ):
            raise EditorialError("daily edition identity is invalid")
        manifest_error = verify_daily_edition_manifest(edition.edition_manifest)
        if manifest_error:
            raise EditorialError(f"daily edition manifest invalid: {manifest_error}")
        manifest = dict(edition.edition_manifest or {})
        expected_status = "empty" if empty_daily else "nonempty"
        if (
            manifest.get("edition_status") != expected_status
            or manifest.get("article_count") != edition.article_count
        ):
            raise EditorialError("daily edition status mismatch")
        if manifest.get("edition_id") != edition.edition_id:
            raise EditorialError("daily edition manifest identity mismatch")
        publication = manifest.get("publication") or {}
        if publication.get("html_sha256") != edition.html_sha256 or (
            publication.get("dated_url") != edition.public_dated_url
        ):
            raise EditorialError("daily edition manifest publication mismatch")
        dated_suffix = f"/editorial/daily/{edition.edition_key}.html"
        if not edition.public_dated_url.endswith(dated_suffix):
            raise EditorialError("daily dated URL contract mismatch")
        public_root = edition.public_dated_url[: -len(dated_suffix)]
        expected_anchor_count = 3 if edition.editor_url else 2
        if len(teams_anchors) != expected_anchor_count:
            raise EditorialError("Teams message action count mismatch")
        anchor_index = 0
        if edition.editor_url:
            expected_editor_url = public_url_contract.daily_editor_console_url(
                edition.edition_id, root_url=public_root
            )
            if not expected_editor_url or edition.editor_url != expected_editor_url:
                raise EditorialError(
                    "Teams editor CTA is not a validated exact-edition link"
                )
            editor_href = f'href="{escape(edition.editor_url, quote=True)}"'
            if editor_href not in teams_anchors[0]:
                raise EditorialError("Teams editor CTA anchor mismatch")
            if edition.editor_url not in edition.teams_text:
                raise EditorialError("Teams text editor CTA missing")
            anchor_index = 1
        dated_href = f'href="{escape(edition.public_dated_url, quote=True)}"'
        if dated_href not in teams_anchors[anchor_index]:
            raise EditorialError(
                "Teams published CTA does not target the immutable dated page"
            )
        if dashboard_href not in teams_anchors[anchor_index + 1]:
            raise EditorialError("Teams dashboard CTA is not canonical")
        if edition.public_dated_url not in edition.teams_text:
            raise EditorialError("Teams text published CTA missing")
        if public_url_contract.CANONICAL_DASHBOARD_URL not in edition.teams_text:
            raise EditorialError("Teams text dashboard CTA missing")
        for surface in (edition.teams_text, edition.teams_html):
            if public_url_contract.DAILY_LATEST_URL in surface:
                raise EditorialError("mutable latest URL is not a Daily Teams action")
    else:
        if len(teams_anchors) != 2:
            raise EditorialError("Teams message must contain brief and dashboard CTAs")

        expected_brief_url = public_url_contract.latest_brief_url(edition.edition_type)
        expected_href = f'href="{escape(expected_brief_url, quote=True)}"'
        if expected_href not in teams_anchors[0]:
            raise EditorialError("Teams brief CTA does not target canonical latest")
        if dashboard_href not in teams_anchors[1]:
            raise EditorialError("Teams dashboard CTA is not canonical")

        if expected_brief_url not in edition.teams_text:
            raise EditorialError("Teams text public brief CTA missing")
        if public_url_contract.CANONICAL_DASHBOARD_URL not in edition.teams_text:
            raise EditorialError("Teams text dashboard CTA missing")

    for anchor in re.findall(r"<a\b[^>]*>", edition.html):
        if 'target="_blank"' not in anchor or 'rel="noopener noreferrer"' not in anchor:
            raise EditorialError("external link security attributes missing")
        match = re.search(r'href="([^"]+)"', anchor)
        if not match or not valid_http_url(match.group(1).replace("&amp;", "&")):
            raise EditorialError("invalid external URL")
    if edition.edition_type == "weekly":
        # §14 — the Weekly shell is the sealed T&I reference: the hero section
        # and reference card markup are the structural anchors (the reference
        # carries no data-role attributes, and none may be added to the shell).
        if edition.html.count('<section class="hero"') != 1:
            raise EditorialError("brief headline count mismatch")
        if edition.html.count('<article class="card"') > WEEKLY_MAX_ARTICLES - 1:
            raise EditorialError("brief article card cap exceeded")
    else:
        if edition.html.count('data-role="headline"') != 1:
            raise EditorialError("brief headline count mismatch")
        if empty_daily and (
            'data-edition-status="empty"' not in edition.html
            or DAILY_EMPTY_STATUS_TEXT not in edition.html
            or DAILY_EMPTY_STATUS_TEXT not in edition.teams_text
            or DAILY_EMPTY_STATUS_TEXT not in edition.teams_html
        ):
            raise EditorialError("truthful empty Daily status missing")
        if edition.html.count('data-role="article-card"') > 5:
            raise EditorialError("brief article card cap exceeded")
    if 'data-brief-contract="AI_TNI_EXECUTIVE_V1"' not in edition.html:
        raise EditorialError("shared brief design contract missing")
    required_copy = (
        ("Daily Brief", "매일 전하는", "오늘의 헤드라인", "오늘의 브리핑")
        if edition.edition_type == "daily"
        else ("Weekly Brief", "매주 전하는", "이번 주 헤드라인", "이번 주 브리핑")
    )
    if any(text not in edition.html for text in required_copy):
        raise EditorialError("brief edition wording mismatch")
    if "Editor's Summary" not in edition.html:
        raise EditorialError("Editor's Summary missing")
    if re.search(r'<img\b[^>]*src="https?://', edition.html, flags=re.I):
        raise EditorialError("remote image hotlink forbidden")


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
    # R4-R17 §B — the preview/verifier fixtures represent a demo Daily/Review
    # brief, so each dominant title carries a concrete material AI event (the
    # executive-materiality bar the real Daily/Review now enforces). Sectors are
    # unchanged (all 투자·산업), so the console layout and structure signatures
    # are preserved while the executive qualification gate admits every row.
    dominant_titles = [
        "AI 데이터센터 전력 조달 계약 체결",
        "AI 데이터센터 전력망 연계 증설 착공",
        "AI 데이터센터 전력 효율 기술 투자 확정",
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
    audit = dict(edition.image_audit or image_audit_manifest(()))
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
        "edition_id": edition.edition_id,
        "editor_url": edition.editor_url,
        **audit,
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
        "image_real_article_photo": article.image_real_article_photo,
        "image_is_category_fallback": article.image_is_category_fallback,
    }
