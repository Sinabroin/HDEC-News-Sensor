#!/usr/bin/env python3
"""Common orchestrator for Daily and Weekly editorial briefings.

Preview is fully offline and writes outside the repository. Production is split
into ``--publish`` (collect/render/write dated+latest), ``--republish`` (the
same publication path with delivery permanently disabled), ``--claim`` (verify
the public dated page and durably reserve its delivery), and ``--send``
(require the exact durable claim, send one link-only message, then convert
exact-250 state).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from email.message import EmailMessage
from email.utils import parseaddr
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
for _path in (ROOT, SCRIPTS):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from app import (collector, editorial_briefing_state, editorial_briefings, editorial_review, news_access, news_censor_verified_state, publisher_direct)  # noqa: E402
from app import public_urls as public_url_contract  # noqa: E402
from app.editorial_briefings import EditorialError, KST  # noqa: E402

RUNTIME_MANIFEST = "runtime-manifest.json"
PUBLICATION_TIMEOUT_SECONDS = 300
PUBLICATION_INTERVAL_SECONDS = 10


class OrchestratorError(RuntimeError):
    """A production precondition failed; no later side effect is allowed."""


def _now() -> datetime:
    return datetime.now(KST)


def _parse_run_at(value: str | None) -> datetime:
    if not value:
        return _now()
    raw = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        raw += "T07:30:00+09:00"
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise OrchestratorError("--run-at must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise OrchestratorError("--run-at must include timezone")
    return parsed.astimezone(KST)


def _github_output(name: str, value: str) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT", "").strip()
    if not output_path:
        return
    with Path(output_path).open("a", encoding="utf-8") as handle:
        handle.write(f"{name}={value}\n")


def _safe_address(value: str) -> str:
    candidate = str(value or "").strip()
    if not candidate or "\r" in candidate or "\n" in candidate:
        return ""
    _display, parsed = parseaddr(candidate)
    if parsed != candidate or parsed.count("@") != 1:
        return ""
    local, domain = parsed.rsplit("@", 1)
    if not local or "." not in domain or domain.startswith(".") or domain.endswith("."):
        return ""
    return parsed


def _production_credentials() -> tuple[str, str, str, str]:
    smtp_user = _safe_address(os.environ.get("GMAIL_SMTP_USER", ""))
    password = (
        os.environ.get("GMAIL_SMTP_APP_PASSWORD", "").strip()
        or os.environ.get("GMAIL_SMTP_PASSWORD", "").strip()
    )
    from_address = _safe_address(os.environ.get("ALERT_EMAIL_FROM", "")) or smtp_user
    recipient = _safe_address(os.environ.get("TEAMS_CHANNEL_EMAIL", ""))
    if not smtp_user or not password or not from_address or not recipient:
        raise OrchestratorError("mail transport configuration is incomplete")
    return smtp_user, password, from_address, recipient


def _require_production_gate() -> None:
    if os.environ.get("EDITORIAL_PRODUCTION", "").strip() != "1":
        raise OrchestratorError("production gate is closed")
    ref = os.environ.get("GITHUB_REF", "").strip()
    if ref != "refs/heads/main":
        raise OrchestratorError("production is main-only")


def _github_claim_owner() -> str:
    components = []
    for name in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT"):
        raw = os.environ.get(name)
        if (
            raw is None
            or raw != raw.strip()
            or re.fullmatch(r"[1-9][0-9]*", raw) is None
        ):
            raise OrchestratorError(f"{name} is missing or malformed")
        components.append(raw)
    return f"github-run:{components[0]}:attempt:{components[1]}"


def collect_live_articles() -> list[dict]:
    """Collect real metadata from the established providers without DB writes."""
    articles, _audit = collect_live_article_bundle()
    return articles


def supplement_weekly_verified_supply(
    current_articles: list[dict],
    *,
    run_at: datetime,
    state_path: Path = ROOT / "data" / "news_censor_verified_state.json",
) -> tuple[list[dict], int]:
    """Add only unexpired publisher-direct verified rows for Weekly coverage.

    Weekly covers the prior completed Wednesday-to-Tuesday window, so a live
    point-in-time collection can truthfully contain no rows from that window.
    The bounded News Censor state is the approved seven-day public carry-forward
    source and is read-only here; it never implies Teams newness.
    """
    try:
        loaded = news_censor_verified_state.load_state(state_path, now=run_at)
        carried, _diagnostics = news_censor_verified_state.carry_forward_articles(
            loaded.state,
            current_articles,
            now=run_at,
        )
    except (OSError, news_censor_verified_state.VerifiedStateError) as exc:
        raise OrchestratorError("weekly verified carry-forward state is unavailable") from exc
    coverage = editorial_briefings.weekly_coverage(run_at)
    eligible: list[dict] = []
    for row in carried:
        try:
            published = editorial_briefings.parse_published_at(row.get("published_at"))
        except EditorialError:
            continue
        if coverage.start <= published <= coverage.end:
            eligible.append(row)
    merged = collector.merge_provider_articles([*current_articles, *eligible])
    return merged, len(eligible)


def _provider_tokens(row: dict) -> set[str]:
    metadata = row.get("source_metadata") or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except (TypeError, ValueError):
            metadata = {}
    provider = str((metadata or {}).get("provider") or "")
    return {item for item in provider.split("+") if item}


def collect_live_article_bundle() -> tuple[list[dict], dict]:
    """Collect live metadata plus non-secret provider counters for preview audit."""
    from app import live_collector, naver_news_provider

    strict_production_collection = bool(
        naver_news_provider.config.NEWS_MODE == "live"
        or os.environ.get("EDITORIAL_PRODUCTION", "").strip() == "1"
    )
    if strict_production_collection:
        try:
            direct_rows = live_collector.fetch_publisher_direct_sources()
        except Exception:  # noqa: BLE001 - keep other verified providers alive
            direct_rows = []
    else:
        # Offline verifier/custom collector calls remain network-free.
        direct_rows = []
    naver_enabled = bool(naver_news_provider.config.NAVER_NEWS_ENABLED)
    naver_credentials_present = bool(
        naver_news_provider.config.NAVER_CLIENT_ID
        and naver_news_provider.config.NAVER_CLIENT_SECRET
    )
    try:
        google_rows = live_collector.fetch_all()
    except Exception:  # noqa: BLE001 - preview keeps other providers alive
        google_rows = []
    try:
        naver_result = naver_news_provider.fetch()
    except Exception:  # noqa: BLE001 - never expose provider exception details
        naver_result = {
            "provider": naver_news_provider.PROVIDER,
            "status": naver_news_provider.STATUS_ERROR,
            "articles": [],
            "queries_attempted": 0,
            "queries_ok": 0,
            "credentials_present": naver_credentials_present,
        }
    naver_status = str(naver_result.get("status") or "unknown")
    naver_requests = int(naver_result.get("queries_attempted") or 0)
    naver_queries_ok = int(naver_result.get("queries_ok") or 0)
    naver_credentials_present = bool(
        naver_result.get("credentials_present") or naver_credentials_present
    )
    naver_activation_error = bool(
        naver_enabled and naver_credentials_present and naver_requests == 0
    )
    if naver_activation_error:
        raise OrchestratorError(
            "Naver provider activation error: enabled provider with credentials made zero API requests"
        )
    naver_rows = list(naver_result.get("articles") or [])
    resolvable = direct_rows + list(google_rows) + naver_rows
    if strict_production_collection and resolvable:
        try:
            live_collector.resolve_publisher_urls(resolvable, strict=True)
        except Exception:  # noqa: BLE001 - fail closed into quarantine
            for row in resolvable:
                if isinstance(row, dict):
                    quarantined = publisher_direct.quarantine_article(
                        row,
                        "publisher_resolution_pass_failed",
                    )
                    row.clear()
                    row.update(quarantined)
    combined_all = collector.merge_provider_articles(resolvable)
    if strict_production_collection:
        combined, quarantined = publisher_direct.partition_delivery_articles(
            combined_all,
            # Editorial relevance ranking remains the downstream owner.
            relevance_qualified=True,
        )
    else:
        # Existing injected/offline collector fixtures exercise normalization
        # without claiming production delivery eligibility.
        combined, quarantined = combined_all, []
    if not combined:
        raise OrchestratorError("live collection returned no articles; fail closed")
    audit = {
        "naver_provider_enabled": naver_enabled,
        "naver_provider_status": naver_status,
        "naver_credentials_present": naver_credentials_present,
        "naver_provider_activation_error": naver_activation_error,
        "naver_provider_queries_ok": naver_queries_ok,
        "naver_api_requests": naver_requests,
        "naver_articles_collected": len(naver_rows),
        "naver_originallinks_collected": sum(
            1
            for row in naver_rows
            if news_access.choose_article_link(row).is_direct
        ),
        "google_news_articles_collected": len(google_rows),
        "publisher_direct_rss_articles_collected": len(direct_rows),
        "publisher_direct_eligible_count": len(combined),
        "publisher_direct_quarantine_count": len(quarantined),
        "final_portal_urls": 0,
    }
    return combined, audit


def _runtime_dir(value: str | None, edition_type: str) -> Path:
    raw = value or os.environ.get("EDITORIAL_RUNTIME_DIR", "")
    if not raw:
        raise OrchestratorError("--runtime-dir or EDITORIAL_RUNTIME_DIR is required")
    path = Path(raw).resolve()
    if path == ROOT or ROOT in path.parents:
        raise OrchestratorError("runtime directory must be outside repository")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _edition_manifest_docs_path(edition_id: str, docs_root: Path | None = None) -> Path:
    """Append-only immutable Daily edition manifest location (id embeds revision)."""
    if not public_url_contract.parse_daily_edition_id(edition_id):
        raise OrchestratorError("invalid daily edition id")
    root = docs_root if docs_root is not None else ROOT
    return root / "docs" / "editorial" / "daily" / "editions" / f"{edition_id}.json"


def write_daily_edition_manifest(edition, *, docs_root: Path | None = None) -> str:
    """R4-R9C — persist the immutable editor-load manifest, append-only.

    A republished date mints a new edition_id, so the only same-path rewrite
    ever allowed is the byte-identical idempotent one; a differing payload at
    an existing path fails closed instead of overwriting an older edition."""
    manifest_error = editorial_briefings.verify_daily_edition_manifest(
        edition.edition_manifest
    )
    if manifest_error:
        raise OrchestratorError(f"edition manifest invalid: {manifest_error}")
    root = docs_root if docs_root is not None else ROOT
    edition_manifest_file = _edition_manifest_docs_path(edition.edition_id, root)
    payload = (
        json.dumps(edition.edition_manifest, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    if edition_manifest_file.exists():
        if edition_manifest_file.read_bytes() != payload:
            raise OrchestratorError(
                "edition manifest collision: refusing to overwrite an existing edition"
            )
    else:
        editorial_briefings.atomic_write_bytes(edition_manifest_file, payload)
    try:
        return edition_manifest_file.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(edition_manifest_file.resolve())


def _docs_paths(edition_type: str, key: str) -> tuple[Path, Path]:
    directory = ROOT / "docs" / "editorial" / edition_type
    return directory / f"{key}.html", directory / "latest.html"


def _write_runtime_manifest(runtime_dir: Path, manifest: dict) -> Path:
    target = runtime_dir / RUNTIME_MANIFEST
    editorial_briefings.atomic_write_bytes(
        target,
        (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )
    return target


def _publication_output_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path.resolve())


def _load_runtime_manifest(runtime_dir: Path, edition_type: str) -> dict:
    path = runtime_dir / RUNTIME_MANIFEST
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise OrchestratorError("runtime manifest missing or malformed") from exc
    required = {
        "version", "edition_type", "edition_key", "coverage_start", "coverage_end",
        "html_sha256", "public_dated_url", "public_latest_url", "dated_path",
        "latest_path", "teams_text", "teams_html", "headline", "issue_mode",
        "article_count", "edition_id", "editor_url",
    }
    optional = set(
        editorial_briefings.SelectionAuditCounters().manifest_fields()
    ) | {
        "feedback_proposal_path",
        "feedback_proposal_sha256",
        "feedback_proposal_created",
        "edition_manifest_path",
    }
    if (
        not isinstance(value, dict)
        or not required <= set(value)
        or not set(value) <= required | optional
    ):
        raise OrchestratorError("runtime manifest fields mismatch")
    if value["version"] != 1 or value["edition_type"] != edition_type:
        raise OrchestratorError("runtime manifest identity mismatch")
    return value


def run_preview(
    edition_type: str,
    *,
    run_at: datetime,
    preview_root: Path,
    fixture_root: str,
    fixture_profile: str,
) -> dict:
    if edition_type == "daily":
        output_dir = preview_root / "daily"
        profile = "dominant"
    else:
        profile = fixture_profile
        output_dir = preview_root / "weekly" / (
            "dominant" if profile == "dominant" else "multi-issue"
        )
    raw_articles = editorial_briefings.fixture_articles(
        edition_type, run_at, profile=profile
    )
    edition = editorial_briefings.render_edition(
        edition_type, raw_articles, run_at=run_at, root_url=fixture_root
    )
    editorial_briefings.validate_rendered(edition)
    manifest = editorial_briefings.write_preview_bundle(edition, output_dir)
    print(
        f"preview_ok edition_type={edition_type} edition={edition.edition_key} "
        "network_sends=0 smtp_attempts=0 state_reads=0 state_writes=0 "
        "docs_writes=0 git_writes=0"
    )
    print(f"preview_path={output_dir}")
    return manifest


def _live_preview_root(value: str | Path) -> Path:
    path = Path(value).resolve()
    system_tmp = Path("/tmp").resolve()
    if path == system_tmp or system_tmp not in path.parents:
        raise OrchestratorError("live-preview output must be a child of /tmp")
    if path == ROOT or ROOT in path.parents:
        raise OrchestratorError("live-preview output must be outside repository")
    return path


def run_live_preview(
    *,
    run_at: datetime,
    preview_root: Path,
    fixture_root: str,
    collect: Callable[[], list[dict]] = collect_live_articles,
    image_page_fetcher=None,
    image_probe=None,
    image_opener=None,
    image_downloader=None,
    publisher_fetcher=None,
    publisher_opener=None,
) -> dict:
    """Build one Daily preview from live metadata without any production side effect."""
    output_root = _live_preview_root(preview_root)
    collection_audit = {
        "naver_provider_enabled": False,
        "naver_provider_status": "not_used",
        "naver_credentials_present": False,
        "naver_provider_activation_error": False,
        "naver_provider_queries_ok": 0,
        "naver_api_requests": 0,
        "naver_articles_collected": 0,
        "naver_originallinks_collected": 0,
        "google_news_articles_collected": 0,
    }
    if collect is collect_live_articles:
        raw_articles, collection_audit = collect_live_article_bundle()
    else:
        raw_articles = collect()
    counters = editorial_briefings.ImageResolutionCounters()
    publisher_counters = editorial_briefings.PublisherUrlResolutionCounters()
    selection_counters = editorial_briefings.SelectionAuditCounters()
    coverage = editorial_briefings.daily_coverage(run_at)
    articles = editorial_briefings.normalize_articles(
        raw_articles,
        coverage,
        limit=editorial_briefings.DAILY_MAX_ARTICLES,
        resolve_images=True,
        allow_image_network=True,
        image_counters=counters,
        image_page_fetcher=image_page_fetcher,
        image_probe=image_probe,
        image_opener=image_opener,
        publisher_counters=publisher_counters,
        publisher_fetcher=publisher_fetcher,
        publisher_opener=publisher_opener,
        selection_audit=selection_counters,
        selection_mode=editorial_briefings.SELECTION_MODE_DIRECT_AWARE_DAILY,
        edition_type="daily",
    )
    if not articles:
        raise OrchestratorError("live preview found no Daily articles in exact coverage")
    output_dir = output_root / "daily"
    articles, materialization_counters = editorial_briefings.materialize_preview_images(
        articles,
        output_root,
        html_dir=output_dir,
        downloader=image_downloader,
        opener=image_opener,
    )
    edition = editorial_briefings.render_daily(
        articles, run_at=run_at, root_url=fixture_root
    )
    editorial_briefings.validate_rendered(edition)

    latest_path = output_dir / "latest.html"
    dated_path = output_dir / f"{edition.edition_key}.html"
    payload = edition.html.encode("utf-8")
    editorial_briefings.atomic_write_bytes(latest_path, payload)
    editorial_briefings.atomic_write_bytes(dated_path, payload)
    image_records = [
        editorial_briefings.resolved_image_record(
            article,
            is_headline=index == 0,
        )
        for index, article in enumerate(articles)
    ]
    resolved_path = output_root / "resolved-images.json"
    editorial_briefings.atomic_write_bytes(
        resolved_path,
        (json.dumps(image_records, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )
    manifest = {
        "version": 1,
        "mode": "live-preview",
        "edition_type": "daily",
        "edition_key": edition.edition_key,
        "coverage_start": coverage.start.isoformat(),
        "coverage_end": coverage.end.isoformat(),
        "article_count": len(articles),
        "dated_html": str(dated_path),
        "latest_html": str(latest_path),
        "resolved_images": str(resolved_path),
        **publisher_counters.manifest_fields(),
        **counters.manifest_fields(),
        **materialization_counters.manifest_fields(),
        **selection_counters.manifest_fields(),
        **collection_audit,
        "publisher_page_gets": counters.network_page_gets,
        "images_resolved_actual": sum(
            1 for article in articles if article.image_remote_url
        ),
        "images_resolved_actual_semantics": "remote image URL candidates selected",
        "smtp_attempts": 0,
        "teams_sends": 0,
        "telegram_calls": 0,
        "state_reads": 0,
        "state_writes": 0,
        "docs_writes": 0,
        "git_writes": 0,
    }
    manifest_path = output_root / "manifest.json"
    editorial_briefings.atomic_write_bytes(
        manifest_path,
        (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )
    print(
        f"live_preview_ok edition_type=daily edition={edition.edition_key} "
        f"articles={len(articles)} "
        f"aggregator_page_gets={publisher_counters.aggregator_page_gets} "
        f"publisher_page_gets={counters.network_page_gets} "
        f"images_resolved_actual={manifest['images_resolved_actual']} "
        "smtp_attempts=0 teams_sends=0 telegram_calls=0 state_reads=0 "
        "state_writes=0 docs_writes=0 git_writes=0"
    )
    print(f"live_preview_path={output_root}")
    return manifest


def _skip_insufficient_quality(
    edition_type: str, key: str, review_decision: str
) -> None:
    """§12 — zero qualified articles publish nothing, machine-readably.

    Weak-content rejection can honestly empty an edition; that outcome is an
    explicit publication mode, never silent filler."""
    _github_output("skipped", "true")
    _github_output("edition", key)
    _github_output("delivery_authorized", "false")
    _github_output("review_mode", "insufficient_quality")
    _github_output("review_decision", review_decision)
    print(
        f"publish_skip edition_type={edition_type} edition={key} "
        "reason=insufficient_quality"
    )
    return None


def run_publish(
    edition_type: str,
    *,
    run_at: datetime,
    runtime_dir: Path,
    collect: Callable[[], list[dict]] = collect_live_articles,
    republish: bool = False,
) -> dict | None:
    _require_production_gate()
    key = editorial_briefings.edition_key(edition_type, run_at)
    state = editorial_briefing_state.load_state(edition_type)
    active_state, expired_claims = editorial_briefing_state.expire_stale_claims(
        state,
        edition_type,
        now=_now(),
    )
    if expired_claims:
        print(
            "publish_stale_claims_ignored="
            + ",".join(expired_claims)
            + " state_writes=0"
        )
    if not republish and (
        editorial_briefing_state.has_success(active_state, key)
        or editorial_briefing_state.has_claim(active_state, key)
    ):
        _github_output("skipped", "true")
        _github_output("edition", key)
        _github_output("delivery_authorized", "false")
        reason = (
            "already_successful"
            if editorial_briefing_state.has_success(active_state, key)
            else "already_claimed"
        )
        print(f"publish_skip edition_type={edition_type} edition={key} reason={reason}")
        return None
    root_url = editorial_briefings.derive_public_root(os.environ.get("REPORT_URL", ""))
    review_mode = "not_applicable"
    review_decision = "not_applicable"
    selection_counters = editorial_briefings.SelectionAuditCounters()
    selection_manifest: dict = {}
    feedback_proposal: dict | None = None
    if edition_type == "daily":
        bundle_path = ROOT / "docs" / "editorial" / "review" / key / "candidates.json"
        review_path = ROOT / "data" / "editorial_reviews" / f"{key}.json"
        try:
            bundle = editorial_review.load_bundle(bundle_path, key)
            # §12 — the review decision (approved/absent/malformed) is traced
            # machine-readably; a malformed review fails closed to the AI order
            # with no partial application and never claims human approval.
            review, review_decision = editorial_review.load_review_decision(
                review_path, key
            )
            selected_articles, review_mode = editorial_review.choose_daily_articles(
                bundle,
                review,
                limit=editorial_briefings.DAILY_MAX_ARTICLES,
            )
            # R4-R10 — delivered Daily lead cards must come from locked
            # primary-ten / secondary-three / promoted-official publishers.
            # Long-tail/specialist leads (비즈트리뷴·더퍼블릭·녹색경제신문·S저널) are
            # dropped from delivery; a shorter, honest brief is preferred to a
            # weak-source lead. Long-tail articles remain operator-visible
            # supporting evidence in the review bundle.
            gated_articles = editorial_briefings.filter_lead_source_eligible(
                selected_articles
            )
            print(
                "daily_lead_source_gate "
                f"delivered_leads={len(gated_articles)} "
                f"long_tail_leads_dropped={len(selected_articles) - len(gated_articles)}"
            )
            if not gated_articles:
                return _skip_insufficient_quality(
                    edition_type, key, review_decision
                )
            selected_articles = gated_articles
            # R4-R9C — the operator editor action requires the dated Review
            # Console for this edition to already exist in the repository;
            # otherwise the message degrades to public-link-only output.
            editor_console_available = (
                ROOT / "docs" / "editorial" / "review" / key / "index.html"
            ).is_file()
            edition = editorial_briefings.render_daily(
                selected_articles,
                run_at=run_at,
                root_url=root_url,
                review_mode=review_mode,
                review_decision=review_decision,
                editor_console_available=editor_console_available,
            )
            raw_selection_manifest = bundle.get("selection_audit")
            if isinstance(raw_selection_manifest, dict):
                allowed_memory_fields = set(
                    selection_counters.manifest_fields()
                )
                selection_manifest = {
                    key: value
                    for key, value in raw_selection_manifest.items()
                    if key in allowed_memory_fields
                }
            if review is not None:
                generated_at = _now().isoformat(timespec="seconds")
                feedback_proposal = editorial_review.build_feedback_proposal(
                    bundle,
                    review,
                    selected_articles,
                    review_mode=review_mode,
                    generated_at=generated_at,
                )
        except editorial_review.EditorialReviewError:
            review_mode = "live_collection_fallback"
            # R4-R10 — even on the live-collection fallback, emit the exact-edition
            # editor CTA when the dated Review Console for this edition exists.
            editor_console_available = (
                ROOT / "docs" / "editorial" / "review" / key / "index.html"
            ).is_file()
            try:
                edition = editorial_briefings.render_edition(
                    edition_type,
                    collect(),
                    run_at=run_at,
                    root_url=root_url,
                    allow_image_network=True,
                    selection_mode=(
                        editorial_briefings.SELECTION_MODE_EDITORIAL_PRIORITY
                    ),
                    selection_audit=selection_counters,
                    review_mode=review_mode,
                    review_decision=review_decision,
                    editor_console_available=editor_console_available,
                    # R4-R10 — the live-collection fallback delivers the same
                    # major/official lead-source floor as the review-bundle path.
                    lead_source_gate=True,
                )
            except editorial_briefings.EditorialError as exc:
                if str(exc) != "empty edition":
                    raise
                return _skip_insufficient_quality(
                    edition_type, key, review_decision
                )
    else:
        raw_articles = collect()
        verified_added = 0
        if collect is collect_live_articles:
            raw_articles, verified_added = supplement_weekly_verified_supply(
                raw_articles,
                run_at=run_at,
            )
        print(
            f"weekly_verified_carry_forward_added={verified_added} "
            "teams_newness_eligible=0 state_writes=0"
        )
        try:
            edition = editorial_briefings.render_edition(
                edition_type,
                raw_articles,
                run_at=run_at,
                root_url=root_url,
                allow_image_network=False,
                selection_mode=(
                    editorial_briefings.SELECTION_MODE_EDITORIAL_PRIORITY
                ),
                selection_audit=selection_counters,
            )
        except editorial_briefings.EditorialError as exc:
            if str(exc) != "empty edition":
                raise
            return _skip_insufficient_quality(edition_type, key, review_decision)
    print(f"editorial_review_mode={review_mode}")
    print(f"editorial_review_decision={review_decision}")
    _github_output("review_mode", review_mode)
    _github_output("review_decision", review_decision)
    editorial_briefings.validate_rendered(edition)
    dated_path, latest_path = _docs_paths(edition_type, edition.edition_key)
    payload = edition.html.encode("utf-8")
    editorial_briefings.atomic_write_bytes(dated_path, payload)
    editorial_briefings.atomic_write_bytes(latest_path, payload)
    if dated_path.read_bytes() != latest_path.read_bytes():
        raise OrchestratorError("dated/latest bytes differ after publication write")
    edition_manifest_output = ""
    if edition_type == "daily" and edition.editor_url:
        edition_manifest_output = write_daily_edition_manifest(edition)
    manifest = editorial_briefings.manifest_for_runtime(edition, dated_path, latest_path)
    if edition_manifest_output:
        manifest["edition_manifest_path"] = edition_manifest_output
    if not selection_manifest:
        selection_manifest = selection_counters.manifest_fields()
    manifest.update(selection_manifest)
    if feedback_proposal is not None:
        proposal_path = runtime_dir / "editorial-feedback-proposal.json"
        proposal_bytes = (
            json.dumps(feedback_proposal, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")
        editorial_briefings.atomic_write_bytes(proposal_path, proposal_bytes)
        manifest.update(
            {
                "feedback_proposal_created": True,
                "feedback_proposal_path": str(proposal_path),
                "feedback_proposal_sha256": hashlib.sha256(
                    proposal_bytes
                ).hexdigest(),
            }
        )
    else:
        manifest["feedback_proposal_created"] = False
    _write_runtime_manifest(runtime_dir, manifest)
    _github_output("skipped", "false")
    _github_output("edition", edition.edition_key)
    _github_output("dated_path", _publication_output_path(dated_path))
    _github_output("latest_path", _publication_output_path(latest_path))
    _github_output("edition_manifest_path", edition_manifest_output)
    _github_output("delivery_authorized", str(not republish).lower())
    print(
        f"publish_ready edition_type={edition_type} edition={edition.edition_key} "
        f"articles={edition.article_count} republish={str(republish).lower()} "
        f"delivery_authorized={str(not republish).lower()}"
    )
    return manifest


def _response_status(response: object) -> int:
    status = getattr(response, "status", None)
    if status is None and hasattr(response, "getcode"):
        status = response.getcode()
    try:
        return int(status)
    except (TypeError, ValueError) as exc:
        raise OrchestratorError("public page returned no HTTP status") from exc


def verify_public_page_once(
    url: str,
    expected_edition: str,
    *,
    opener: Callable | None = None,
) -> bool:
    if not editorial_briefings.valid_http_url(url):
        return False
    open_url = opener or urllib.request.urlopen
    try:
        response = open_url(url, timeout=15)
        with response:
            if _response_status(response) != 200:
                return False
            body = response.read(2_000_000).decode("utf-8", errors="replace")
    except (OSError, TimeoutError, urllib.error.URLError, ValueError):
        return False
    expected_data = f'data-edition-key="{expected_edition}"'
    return expected_data in body


def poll_public_dated_page(
    url: str,
    expected_edition: str,
    *,
    timeout_seconds: int = PUBLICATION_TIMEOUT_SECONDS,
    interval_seconds: int = PUBLICATION_INTERVAL_SECONDS,
    opener: Callable | None = None,
    sleeper: Callable[[float], None] = time.sleep,
) -> bool:
    if timeout_seconds < 0 or interval_seconds <= 0:
        raise OrchestratorError("invalid polling configuration")
    deadline = time.monotonic() + timeout_seconds
    while True:
        if verify_public_page_once(url, expected_edition, opener=opener):
            return True
        if time.monotonic() >= deadline:
            return False
        sleeper(min(interval_seconds, max(0.0, deadline - time.monotonic())))


def build_link_message(manifest: dict, from_address: str, recipient: str) -> EmailMessage:
    message = EmailMessage()
    prefix = "[HDEC AI Daily Brief]" if manifest["edition_type"] == "daily" else "[AI 경영 T&I]"
    message["Subject"] = f"{prefix} {manifest['edition_key']}"
    message["From"] = from_address
    message["To"] = recipient
    message.set_content(manifest["teams_text"])
    message.add_alternative(manifest["teams_html"], subtype="html")
    if list(message.iter_attachments()):
        raise OrchestratorError("attachments are forbidden")
    return message


def _verify_local_publication(manifest: dict) -> None:
    dated = Path(manifest["dated_path"]).resolve()
    latest = Path(manifest["latest_path"]).resolve()
    expected_dated, expected_latest = _docs_paths(
        manifest["edition_type"], manifest["edition_key"]
    )
    if dated != expected_dated.resolve() or latest != expected_latest.resolve():
        raise OrchestratorError("runtime publication paths mismatch")
    try:
        dated_bytes = dated.read_bytes()
        latest_bytes = latest.read_bytes()
    except OSError as exc:
        raise OrchestratorError("local publication is missing") from exc
    if dated_bytes != latest_bytes:
        raise OrchestratorError("dated/latest local bytes differ")
    import hashlib

    if hashlib.sha256(dated_bytes).hexdigest() != manifest["html_sha256"]:
        raise OrchestratorError("local publication hash mismatch")
    text = dated_bytes.decode("utf-8")
    if f'data-edition-key="{manifest["edition_key"]}"' not in text:
        raise OrchestratorError("local publication edition marker mismatch")


def _manifest_identity(manifest: dict) -> dict:
    return {
        "edition_key": manifest["edition_key"],
        "coverage_start": manifest["coverage_start"],
        "coverage_end": manifest["coverage_end"],
        "html_sha256": manifest["html_sha256"],
        "public_url": manifest["public_dated_url"],
    }


def _state_output_path(edition_type: str, path: Path | None) -> str:
    target = Path(path) if path is not None else editorial_briefing_state.state_path(
        edition_type
    )
    try:
        return target.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(target.resolve())


def _emit_claim_outputs(
    edition_type: str,
    edition_key: str,
    claim_owner: str,
    *,
    state_changed: bool,
    send_authorized: bool,
    path: Path | None,
) -> None:
    _github_output("state_changed", str(state_changed).lower())
    _github_output("state_path", _state_output_path(edition_type, path))
    _github_output("send_authorized", str(send_authorized).lower())
    _github_output("edition", edition_key)
    _github_output("claim_owner", claim_owner)


def run_claim(
    edition_type: str,
    *,
    run_at: datetime,
    runtime_dir: Path,
    opener: Callable | None = None,
    state_path: Path | None = None,
    publication_timeout_seconds: int = PUBLICATION_TIMEOUT_SECONDS,
    publication_interval_seconds: int = PUBLICATION_INTERVAL_SECONDS,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict | None:
    _require_production_gate()
    key = editorial_briefings.edition_key(edition_type, run_at)
    manifest = _load_runtime_manifest(runtime_dir, edition_type)
    if manifest["edition_key"] != key:
        raise OrchestratorError("runtime edition does not match current catch-up edition")
    _verify_local_publication(manifest)
    root_url = editorial_briefings.derive_public_root(os.environ.get("REPORT_URL", ""))
    dated_url, latest_url = editorial_briefings.public_urls(root_url, edition_type, key)
    if manifest["public_dated_url"] != dated_url or manifest["public_latest_url"] != latest_url:
        raise OrchestratorError("runtime public URL mismatch")
    loaded = editorial_briefing_state.load_state(edition_type, path=state_path)
    current, expired_claims = editorial_briefing_state.expire_stale_claims(
        loaded,
        edition_type,
        now=_now(),
    )
    if key in expired_claims:
        print(
            f"claim_expired edition_type={edition_type} edition={key} "
            "accepted_delivery=false"
        )
    existing = current["delivery_claims"].get(key)
    if editorial_briefing_state.has_success(current, key):
        claim_owner = _github_claim_owner()
        _emit_claim_outputs(
            edition_type,
            key,
            claim_owner,
            state_changed=False,
            send_authorized=False,
            path=state_path,
        )
        print(
            f"claim_skip edition_type={edition_type} edition={key} "
            "reason=already_successful "
            "send_authorized=false"
        )
        return None
    identity = _manifest_identity(manifest)
    if existing is not None:
        claim_owner = _github_claim_owner()
        if existing["claim_owner"] != claim_owner:
            _emit_claim_outputs(
                edition_type,
                key,
                claim_owner,
                state_changed=False,
                send_authorized=False,
                path=state_path,
            )
            print(
                f"claim_skip edition_type={edition_type} edition={key} "
                "reason=claimed_by_another_owner send_authorized=false"
            )
            return None
        editorial_briefing_state.require_claim_owner(
            current,
            edition_type,
            key,
            claim_owner,
            identity=identity,
        )
        _emit_claim_outputs(
            edition_type,
            key,
            claim_owner,
            state_changed=False,
            send_authorized=True,
            path=state_path,
        )
        print(
            f"claim_ready edition_type={edition_type} edition={key} "
            "state_changed=false send_authorized=true"
        )
        return current
    if not poll_public_dated_page(
        dated_url,
        key,
        timeout_seconds=publication_timeout_seconds,
        interval_seconds=publication_interval_seconds,
        opener=opener,
        sleeper=sleeper,
    ):
        raise OrchestratorError("dated public page did not reach matching HTTP 200 state")
    claim_owner = _github_claim_owner()
    claim = {
        **identity,
        "claim_owner": claim_owner,
        "claimed_at": _now().isoformat(timespec="seconds"),
    }
    updated = editorial_briefing_state.add_claim(
        current,
        edition_type,
        claim,
    )
    editorial_briefing_state.atomic_write_state(
        edition_type,
        updated,
        path=state_path,
    )
    _emit_claim_outputs(
        edition_type,
        key,
        claim_owner,
        state_changed=True,
        send_authorized=True,
        path=state_path,
    )
    print(
        f"claim_ready edition_type={edition_type} edition={key} "
        "state_changed=true send_authorized=true smtp_attempts=0"
    )
    return updated


def persist_exact_250_success(
    edition_type: str,
    manifest: dict,
    *,
    claim_owner: str,
    smtp_status: str,
    smtp_code: int | None,
    sent_at: datetime,
    path: Path | None = None,
) -> dict:
    if smtp_status != "accepted" or type(smtp_code) is not int or smtp_code != 250:
        raise OrchestratorError("state requires exact SMTP DATA 250")
    state = editorial_briefing_state.load_state(edition_type, path=path)
    updated = editorial_briefing_state.convert_claim_to_success(
        state,
        edition_type,
        {
            **_manifest_identity(manifest),
            "smtp_status": smtp_status,
            "smtp_code": smtp_code,
            "sent_at": sent_at.astimezone(KST).isoformat(timespec="seconds"),
        },
        claim_owner,
    )
    editorial_briefing_state.atomic_write_state(edition_type, updated, path=path)
    return updated


def run_send(
    edition_type: str,
    *,
    run_at: datetime,
    runtime_dir: Path,
    smtp_factory=None,
    opener: Callable | None = None,
    state_path: Path | None = None,
) -> dict | None:
    _require_production_gate()
    key = editorial_briefings.edition_key(edition_type, run_at)
    claim_owner = _github_claim_owner()
    state = editorial_briefing_state.load_state(edition_type, path=state_path)
    manifest = _load_runtime_manifest(runtime_dir, edition_type)
    if manifest["edition_key"] != key:
        raise OrchestratorError("runtime edition does not match current catch-up edition")
    _verify_local_publication(manifest)
    root_url = editorial_briefings.derive_public_root(os.environ.get("REPORT_URL", ""))
    dated_url, latest_url = editorial_briefings.public_urls(root_url, edition_type, key)
    if manifest["public_dated_url"] != dated_url or manifest["public_latest_url"] != latest_url:
        raise OrchestratorError("runtime public URL mismatch")
    editorial_briefing_state.require_claim_owner(
        state,
        edition_type,
        key,
        claim_owner,
        identity=_manifest_identity(manifest),
    )

    smtp_user, password, from_address, recipient = _production_credentials()
    message = build_link_message(manifest, from_address, recipient)
    from send_email_alert import DeliveryTarget, deliver_email_message

    result = deliver_email_message(
        message,
        DeliveryTarget("editorial_teams_channel", recipient, "teams_channel"),
        smtp_user,
        password,
        from_address,
        smtp_factory=smtp_factory,
    )
    if result.smtp_status != "accepted" or result.smtp_code != 250:
        raise OrchestratorError(
            f"mail delivery rejected: status={result.smtp_status} "
            f"code={result.smtp_code if result.smtp_code is not None else 'none'}"
        )
    updated = persist_exact_250_success(
        edition_type,
        manifest,
        claim_owner=claim_owner,
        smtp_status=result.smtp_status,
        smtp_code=result.smtp_code,
        sent_at=_now(),
        path=state_path,
    )
    _github_output("state_changed", "true")
    _github_output("state_path", _state_output_path(edition_type, state_path))
    print(
        f"send_ok edition_type={edition_type} edition={key} "
        "smtp_status=accepted smtp_code=250 state_changed=true"
    )
    return updated


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--edition-type", choices=("daily", "weekly"), required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preview", action="store_true")
    mode.add_argument("--live-preview", action="store_true")
    mode.add_argument("--publish", action="store_true")
    mode.add_argument("--republish", action="store_true")
    mode.add_argument("--claim", action="store_true")
    mode.add_argument("--send", action="store_true")
    parser.add_argument("--run-at", default="")
    parser.add_argument("--runtime-dir", default="")
    parser.add_argument("--preview-root", default="/tmp/d7ak6e-preview")
    parser.add_argument(
        "--fixture-root", default="https://preview.fixture.test/HDEC-News-Sensor"
    )
    parser.add_argument("--fixture-profile", choices=("dominant", "multi"), default="dominant")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        run_at = _parse_run_at(args.run_at)
        if args.preview:
            run_preview(
                args.edition_type,
                run_at=run_at,
                preview_root=Path(args.preview_root).resolve(),
                fixture_root=args.fixture_root,
                fixture_profile=args.fixture_profile,
            )
        elif args.live_preview:
            if args.edition_type != "daily":
                raise OrchestratorError("--live-preview supports Daily only")
            run_live_preview(
                run_at=run_at,
                preview_root=Path(args.preview_root),
                fixture_root=args.fixture_root,
            )
        elif args.publish:
            run_publish(
                args.edition_type,
                run_at=run_at,
                runtime_dir=_runtime_dir(args.runtime_dir, args.edition_type),
            )
        elif args.republish:
            run_publish(
                args.edition_type,
                run_at=run_at,
                runtime_dir=_runtime_dir(args.runtime_dir, args.edition_type),
                republish=True,
            )
        elif args.claim:
            run_claim(
                args.edition_type,
                run_at=run_at,
                runtime_dir=_runtime_dir(args.runtime_dir, args.edition_type),
            )
        else:
            run_send(
                args.edition_type,
                run_at=run_at,
                runtime_dir=_runtime_dir(args.runtime_dir, args.edition_type),
            )
    except (
        EditorialError,
        editorial_briefing_state.StateError,
        OrchestratorError,
        OSError,
    ) as exc:
        print(f"ERROR: editorial briefing failed closed ({type(exc).__name__})", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
