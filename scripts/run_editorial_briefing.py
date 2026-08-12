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
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime
from dataclasses import replace
from urllib.parse import urlsplit, urlunsplit
from email.message import EmailMessage
from email.utils import parseaddr
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
for _path in (ROOT, SCRIPTS):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from app import (collector, daily_publication, editorial_briefing_state, editorial_briefings, editorial_review, news_access, news_censor_verified_state, publisher_direct)  # noqa: E402
from app import public_urls as public_url_contract  # noqa: E402
from app.editorial_briefings import EditorialError, KST  # noqa: E402

RUNTIME_MANIFEST = "runtime-manifest.json"
PUBLICATION_TIMEOUT_SECONDS = 300
PUBLICATION_INTERVAL_SECONDS = 10

# R4-R12 §6 step 10 — a Daily edition may be claimed and sent only after every
# exact immutable resource (the dated reader page AND the content-addressed
# edition manifest) publicly resolves and reconstructs. The machine-readable
# skip reason is the shared contract constant; the earlier gates keep their own
# more granular reasons and skip fail-closed before publish
# (daily_editor_console_missing / daily_review_bundle_unavailable /
# daily_editor_not_reconstructable / insufficient_quality).
SKIP_PUBLIC_RESOURCE_VERIFICATION = daily_publication.SKIP_PUBLIC_RESOURCE


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
            "primary_publisher_queries_attempted": 0,
            "primary_publisher_queries_ok": 0,
            "primary_publisher_articles_collected": 0,
            "primary_10_articles_collected": 0,
            "secondary_3_articles_collected": 0,
            "primary_publisher_lane_budget_exhausted": False,
        }
    naver_status = str(naver_result.get("status") or "unknown")
    naver_requests = int(naver_result.get("queries_attempted") or 0)
    naver_queries_ok = int(naver_result.get("queries_ok") or 0)
    # R4-R16 — non-secret primary-publisher discovery-lane counters.
    primary_publisher_queries_attempted = int(
        naver_result.get("primary_publisher_queries_attempted") or 0
    )
    primary_publisher_queries_ok = int(
        naver_result.get("primary_publisher_queries_ok") or 0
    )
    primary_publisher_articles_collected = int(
        naver_result.get("primary_publisher_articles_collected") or 0
    )
    primary_10_articles_collected = int(
        naver_result.get("primary_10_articles_collected") or 0
    )
    secondary_3_articles_collected = int(
        naver_result.get("secondary_3_articles_collected") or 0
    )
    primary_publisher_lane_budget_exhausted = bool(
        naver_result.get("primary_publisher_lane_budget_exhausted")
    )
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
        "primary_publisher_queries_attempted": primary_publisher_queries_attempted,
        "primary_publisher_queries_ok": primary_publisher_queries_ok,
        "primary_publisher_articles_collected": primary_publisher_articles_collected,
        "primary_10_articles_collected": primary_10_articles_collected,
        "secondary_3_articles_collected": secondary_3_articles_collected,
        "primary_publisher_lane_budget_exhausted": (
            primary_publisher_lane_budget_exhausted
        ),
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


def _existing_publication_image_bytes(
    article,
    *,
    edition_type: str,
    edition_key: str,
    review_asset_root: Path | None,
) -> tuple[bytes, str]:
    """Read one explicitly provenanced exact-Review asset for Daily only.

    An arbitrary candidate image_url is never a local path capability. The
    caller supplies the exact Review edition root, and the serialized candidate
    must carry the matching edition plus a single content-addressed filename.
    """
    if edition_type != "daily":
        return b"", "exact_review_asset_not_applicable"
    provenance_edition = str(article.review_asset_edition_key or "").strip()
    relative = str(article.review_asset_relative_path or "").strip()
    digest_prefix = str(article.review_asset_sha256_prefix or "").strip()
    if not provenance_edition and not relative and not digest_prefix:
        return b"", "exact_review_asset_provenance_missing"
    if provenance_edition != edition_key or review_asset_root is None:
        raise OrchestratorError("exact Review asset edition identity mismatch")
    match = re.fullmatch(
        r"assets/images/([0-9a-f]{24})\.(jpg|jpeg|png|webp|avif)",
        relative,
    )
    if not match or digest_prefix != match.group(1):
        raise OrchestratorError("exact Review asset path provenance invalid")
    exact_root = review_asset_root.resolve()
    allowed_root = (exact_root / "assets" / "images").resolve()
    source = (exact_root / relative).resolve()
    if source.parent != allowed_root:
        raise OrchestratorError("exact Review asset path escaped exact edition")
    try:
        payload = source.read_bytes()
    except OSError:
        return b"", "exact_review_asset_unavailable"
    digest = hashlib.sha256(payload).hexdigest()
    if not digest.startswith(digest_prefix):
        return b"", "exact_review_asset_digest_mismatch"
    if not editorial_briefings._raster_content_type(payload):
        return b"", "exact_review_asset_not_supported_raster"
    verdict = editorial_briefings.assess_daily_image_asset(payload)
    if not verdict.valid:
        return b"", f"exact_review_asset_{verdict.reason}"
    if (
        article.image_width
        and verdict.width != int(article.image_width)
        or article.image_height
        and verdict.height != int(article.image_height)
    ):
        return b"", "exact_review_asset_geometry_mismatch"
    return payload, ""


def prepare_publication_images(
    articles: list,
    *,
    edition_type: str,
    edition_key: str,
    publication_dir: Path,
    review_asset_root: Path | None = None,
    downloader=None,
) -> tuple[list, dict, list[dict]]:
    """Stage immutable publication-owned real photos and return no fallbacks.

    Existing dated Review assets are byte-validated before being copied. If an
    exact Review asset is unavailable, every bounded publisher candidate is
    retried through the existing materializer. Articles that still lack a real
    photo are excluded; callers must fail explicitly when that would turn a
    qualified non-empty selection into an empty edition.
    """
    if edition_type not in {"daily", "weekly"}:
        raise OrchestratorError("unsupported publication image product")
    publication_dir = publication_dir.resolve()
    materialized: list = []
    asset_payloads: list[dict] = []
    failure_reasons: dict[str, str] = {}
    materialization_failures = 0
    quality_rejections = 0
    seen_digests: set[str] = set()

    with tempfile.TemporaryDirectory(prefix=f"r4-ops-7-{edition_type}-images-", dir="/tmp") as tmp:
        stage_root = Path(tmp)
        for index, article in enumerate(articles):
            article_id = editorial_briefings.editorial_article_id(article)
            payload, local_error = _existing_publication_image_bytes(
                article,
                edition_type=edition_type,
                edition_key=edition_key,
                review_asset_root=review_asset_root,
            )
            candidate = article
            reason = local_error
            if payload:
                try:
                    # The target path is publication-owned even when the source
                    # bytes came from an immutable Review snapshot.
                    digest = hashlib.sha256(payload).hexdigest()
                    mime = editorial_briefings._raster_content_type(payload)
                    extension = {
                        "image/jpeg": ".jpg",
                        "image/png": ".png",
                        "image/webp": ".webp",
                        "image/avif": ".avif",
                    }.get(mime, "")
                    filename = f"{digest[:24]}{extension}"
                    local_src = f"assets/{edition_key}/{filename}"
                    candidate = editorial_briefings.mark_real_article_photo(
                        article,
                        payload,
                        local_src=local_src,
                        local_asset=filename,
                        materialization_reason="copied_exact_review_asset",
                    )
                    reason = ""
                except editorial_briefings.EditorialError as exc:
                    reason = str(exc).split(": ", 1)[-1]
                    payload = b""

            if not payload:
                one_root = stage_root / f"article-{index + 1}"
                staged, counters = editorial_briefings.materialize_preview_images(
                    [article],
                    one_root,
                    html_dir=one_root / edition_type,
                    downloader=downloader,
                    daily=True,
                )
                staged_article = staged[0]
                if staged_article.image_real_article_photo:
                    staged_path = (
                        one_root / "assets" / "images" / staged_article.image_local_asset
                    )
                    try:
                        payload = staged_path.read_bytes()
                    except OSError:
                        payload = b""
                    if payload:
                        digest = hashlib.sha256(payload).hexdigest()
                        mime = editorial_briefings._raster_content_type(payload)
                        extension = {
                            "image/jpeg": ".jpg",
                            "image/png": ".png",
                            "image/webp": ".webp",
                            "image/avif": ".avif",
                        }.get(mime, "")
                        filename = f"{digest[:24]}{extension}"
                        local_src = f"assets/{edition_key}/{filename}"
                        candidate = editorial_briefings.mark_real_article_photo(
                            staged_article,
                            payload,
                            local_src=local_src,
                            local_asset=filename,
                        )
                        reason = ""
                if not payload or not staged_article.image_real_article_photo:
                    reason = (
                        staged_article.image_materialization_reason
                        or staged_article.image_quality_reason
                        or reason
                        or "image_materialization_failed"
                    )
                    if counters.image_quality_rejections:
                        quality_rejections += 1
                    else:
                        materialization_failures += 1
                    failure_reasons[article_id] = reason
                    continue

            digest = hashlib.sha256(payload).hexdigest()
            if digest in seen_digests:
                quality_rejections += 1
                failure_reasons[article_id] = "image_duplicate_across_articles"
                continue
            seen_digests.add(digest)
            target = publication_dir / "assets" / edition_key / candidate.image_local_asset
            public_path = (
                f"editorial/{edition_type}/assets/{edition_key}/"
                f"{candidate.image_local_asset}"
            )
            asset_payloads.append(
                {
                    "article_id": article_id,
                    "path": target,
                    "relative_path": public_path,
                    "sha256": digest,
                    "byte_size": len(payload),
                    "content_type": editorial_briefings._raster_content_type(payload),
                    "image_source_kind": candidate.image_source_kind,
                    "payload": payload,
                }
            )
            materialized.append(candidate)

    public_records = [
        {key: value for key, value in asset.items() if key not in {"path", "payload"}}
        for asset in asset_payloads
    ]
    audit = editorial_briefings.image_audit_manifest(
        materialized,
        image_materialization_failed_count=materialization_failures,
        image_quality_rejected_count=quality_rejections,
        failure_reasons=failure_reasons,
        publication_assets=public_records,
    )
    return materialized, audit, asset_payloads


def write_publication_image_assets(
    *,
    edition_type: str,
    edition_key: str,
    publication_dir: Path,
    audit: dict,
    asset_payloads: list[dict],
) -> str:
    """Write only the exact staged assets plus their immutable audit manifest."""
    asset_dir = publication_dir / "assets" / edition_key
    for asset in asset_payloads:
        target = Path(asset["path"]).resolve()
        if target.parent != asset_dir.resolve():
            raise OrchestratorError("publication image asset escaped exact edition")
        editorial_briefings.atomic_write_bytes(target, bytes(asset["payload"]))
    manifest_path = asset_dir / "image-manifest.json"
    manifest = {
        "version": 1,
        "edition_type": edition_type,
        "edition_key": edition_key,
        **audit,
    }
    editorial_briefings.atomic_write_bytes(
        manifest_path,
        (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )
    return _publication_output_path(manifest_path)


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
        "image_manifest_path",
        "image_manifest_sha256",
        "real_article_photo_count",
        "fallback_visual_count",
        "image_materialization_failed_count",
        "image_quality_rejected_count",
        "real_photo_article_ids",
        "fallback_article_ids",
        "failure_reasons",
        "publication_image_assets",
        "production_image_gate_required",
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
    edition_type: str = "daily",
    collect: Callable[[], list[dict]] = collect_live_articles,
    image_page_fetcher=None,
    image_probe=None,
    image_opener=None,
    image_downloader=None,
    publisher_fetcher=None,
    publisher_opener=None,
) -> dict:
    """Build one Daily/Weekly live preview without any production side effect."""
    if edition_type not in {"daily", "weekly"}:
        raise OrchestratorError("unsupported live-preview edition type")
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
        "primary_publisher_queries_attempted": 0,
        "primary_publisher_queries_ok": 0,
        "primary_publisher_articles_collected": 0,
        "primary_10_articles_collected": 0,
        "secondary_3_articles_collected": 0,
        "primary_publisher_lane_budget_exhausted": False,
    }
    if collect is collect_live_articles:
        raw_articles, collection_audit = collect_live_article_bundle()
    else:
        raw_articles = collect()
    counters = editorial_briefings.ImageResolutionCounters()
    publisher_counters = editorial_briefings.PublisherUrlResolutionCounters()
    selection_counters = editorial_briefings.SelectionAuditCounters()
    coverage = editorial_briefings.coverage_for(edition_type, run_at)
    articles = editorial_briefings.normalize_articles(
        raw_articles,
        coverage,
        limit=(
            editorial_briefings.DAILY_MAX_ARTICLES
            if edition_type == "daily"
            else editorial_briefings.WEEKLY_MAX_ARTICLES
        ),
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
        edition_type=edition_type,
    )
    if not articles:
        raise OrchestratorError(
            f"live preview found no {edition_type} articles in exact coverage"
        )
    output_dir = output_root / edition_type
    articles, materialization_counters = editorial_briefings.materialize_preview_images(
        articles,
        output_root,
        html_dir=output_dir,
        downloader=image_downloader,
        opener=image_opener,
        daily=True,
    )
    edition = (
        editorial_briefings.render_daily(
            articles, run_at=run_at, root_url=fixture_root
        )
        if edition_type == "daily"
        else editorial_briefings.render_weekly(
            articles, run_at=run_at, root_url=fixture_root
        )
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
    image_audit = editorial_briefings.image_audit_manifest(
        articles,
        image_materialization_failed_count=materialization_counters.image_downloads_failed,
        image_quality_rejected_count=materialization_counters.image_quality_rejections,
    )
    manifest = {
        "version": 1,
        "mode": "live-preview",
        "edition_type": edition_type,
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
        **image_audit,
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
        f"live_preview_ok edition_type={edition_type} edition={edition.edition_key} "
        f"articles={len(articles)} "
        f"aggregator_page_gets={publisher_counters.aggregator_page_gets} "
        f"publisher_page_gets={counters.network_page_gets} "
        f"images_resolved_actual={manifest['images_resolved_actual']} "
        "smtp_attempts=0 teams_sends=0 telegram_calls=0 state_reads=0 "
        "state_writes=0 docs_writes=0 git_writes=0"
    )
    print(f"ARTICLE_COUNT={manifest['article_count']}")
    print(f"REAL_ARTICLE_PHOTO_COUNT={manifest['real_article_photo_count']}")
    print(f"FALLBACK_VISUAL_COUNT={manifest['fallback_visual_count']}")
    print(
        "IMAGE_FAILURE_COUNT="
        f"{manifest['image_materialization_failed_count'] + manifest['image_quality_rejected_count']}"
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


def _daily_console_path(key: str) -> Path:
    """Dated Review Console artifact for one Daily edition — the exact page the
    Teams editor CTA opens (docs/editorial/review/<edition>/index.html)."""
    return ROOT / "docs" / "editorial" / "review" / key / "index.html"


_CONSOLE_BUNDLE_RE = re.compile(
    r'<script id="candidate-data" type="application/json">(.*?)</script>',
    re.DOTALL,
)


def _console_candidate_bundle(console_path: Path) -> dict | None:
    """Inline candidate bundle of a built Review Console page, or None."""
    try:
        match = _CONSOLE_BUNDLE_RE.search(console_path.read_text(encoding="utf-8"))
        loaded = json.loads(match.group(1)) if match else None
    except (OSError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _console_url_key(value: object) -> str:
    """Python mirror of the Review Console's JS ``canonicalKey`` — the URL
    identity ``duplicateByUrl`` uses to match a manifest article to a console
    candidate. Any divergence fails closed (returns "")."""
    raw = str(value or "").strip()
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError:
        return ""
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    if scheme not in {"http", "https"} or not host:
        return ""
    default_port = 443 if scheme == "https" else 80
    netloc = host if port in (None, default_port) else f"{host}:{port}"
    path = re.sub(r"/+$", "", parsed.path) or "/"
    return urlunsplit((scheme, netloc, path, parsed.query, ""))


def daily_editor_publication_error(edition) -> str:
    """R4-R11 §2 — fail-closed exact-edition editor availability gate.

    Returns "" only when the delivered Daily's operator editor is fully
    reconstructable from already-published artifacts: the editor deep link is
    minted, the immutable edition manifest verifies, the dated Review Console
    page exists, its inline candidate bundle carries the same edition key, and
    every manifest article adopts into a distinct console candidate exactly the
    way the browser-side ``adoptExactEdition`` will replay it. Any other state
    returns a machine-readable reason and the publication must skip — a Daily
    Teams message is never sent reader-only."""
    if not getattr(edition, "editor_url", ""):
        return "daily_editor_identity_unavailable"
    manifest = getattr(edition, "edition_manifest", None)
    if editorial_briefings.verify_daily_edition_manifest(manifest):
        return "daily_editor_identity_unavailable"
    console_path = _daily_console_path(edition.edition_key)
    if not console_path.is_file():
        return "daily_editor_console_missing"
    bundle = _console_candidate_bundle(console_path)
    if not bundle or str(bundle.get("edition_key") or "") != edition.edition_key:
        return "daily_editor_not_reconstructable"
    candidate_ids: dict[str, str] = {}
    for candidate in bundle.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        key = _console_url_key(candidate.get("selected_url"))
        if key and key not in candidate_ids:
            candidate_ids[key] = str(candidate.get("candidate_id") or "")
    rows = manifest.get("articles") or []
    matched: set[str] = set()
    if not rows:
        if (
            manifest.get("edition_status") == "empty"
            and manifest.get("article_count") == 0
        ):
            return ""
        return "daily_editor_not_reconstructable"
    for row in rows:
        if not isinstance(row, dict):
            return "daily_editor_not_reconstructable"
        key = _console_url_key(row.get("publisher_url"))
        candidate_id = candidate_ids.get(key)
        if not key or not candidate_id or candidate_id in matched:
            return "daily_editor_not_reconstructable"
        matched.add(candidate_id)
    return ""


def _skip_daily_editor_unavailable(
    key: str, review_decision: str, reason: str
) -> None:
    """R4-R11 §2.5 — the reader-only degradation policy is removed.

    A Daily whose exact-edition editor cannot be reconstructed publishes
    nothing and sends nothing: the skip is machine-readable and the later
    scheduled attempt (07:50/08:05/08:15 KST) is the only retry path."""
    _github_output("skipped", "true")
    _github_output("edition", key)
    _github_output("delivery_authorized", "false")
    _github_output("review_mode", "editor_unavailable")
    _github_output("review_decision", review_decision)
    _github_output("skip_reason", reason)
    print(f"publish_skip edition_type=daily edition={key} reason={reason}")
    print("daily_reader_only_send_allowed=false")
    return None


def _skip_image_quality_failure(
    edition_type: str,
    key: str,
    audit: dict,
) -> None:
    """Qualified supply existed, but no truthful non-empty photo edition did."""
    _github_output("skipped", "true")
    _github_output("edition", key)
    _github_output("delivery_authorized", "false")
    _github_output("skip_reason", "real_article_photo_gate_failed")
    for field in (
        "article_count",
        "real_article_photo_count",
        "fallback_visual_count",
        "image_materialization_failed_count",
        "image_quality_rejected_count",
    ):
        _github_output(field, str(audit.get(field, 0)))
    print(
        f"publish_skip edition_type={edition_type} edition={key} "
        "reason=real_article_photo_gate_failed "
        f"image_materialization_failed_count={audit.get('image_materialization_failed_count', 0)} "
        f"image_quality_rejected_count={audit.get('image_quality_rejected_count', 0)}"
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
    dated_path, latest_path = _docs_paths(edition_type, key)
    review_mode = "not_applicable"
    review_decision = "not_applicable"
    selection_counters = editorial_briefings.SelectionAuditCounters()
    selection_manifest: dict = {}
    feedback_proposal: dict | None = None
    image_audit = editorial_briefings.image_audit_manifest(())
    publication_asset_payloads: list[dict] = []
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
                limit=max(
                    editorial_briefings.DAILY_MAX_ARTICLES,
                    len(bundle.get("candidates") or []),
                ),
            )
            if review is not None:
                # R4-OPS-7: preserve the human order first, then keep only
                # already-qualified/recommended Review candidates as a bounded
                # real-photo backfill pool. This never collects filler and is
                # used only when an earlier selected row cannot materialize a
                # truthful article photo.
                automatic_pool, _automatic_mode = (
                    editorial_review.choose_daily_articles(
                        bundle,
                        None,
                        limit=len(bundle.get("candidates") or []),
                    )
                )
                selected_urls = {article.selected_url for article in selected_articles}
                recommended_urls = {
                    str(candidate.get("selected_url") or "")
                    for candidate in bundle.get("candidates") or []
                    if candidate.get("ai_recommended") is True
                }
                selected_articles.extend(
                    article
                    for article in automatic_pool
                    if article.selected_url not in selected_urls
                    and article.selected_url in recommended_urls
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
                review_mode = "empty_edition"
            selected_articles = gated_articles
            # R4-R11 §2 — the operator editor action is mandatory on every
            # delivered Daily. The dated Review Console for this edition must
            # already exist (editorial-review-console.yml, cron "20 22"); when
            # it is missing the publication skips fail-closed instead of
            # degrading to a reader-only message, and the later scheduled
            # attempt retries.
            if not _daily_console_path(key).is_file():
                return _skip_daily_editor_unavailable(
                    key, review_decision, "daily_editor_console_missing"
                )
            qualified_article_count = len(selected_articles)
            if selected_articles:
                selected_articles, image_audit, publication_asset_payloads = (
                    prepare_publication_images(
                        selected_articles,
                        edition_type="daily",
                        edition_key=key,
                        publication_dir=dated_path.parent,
                        review_asset_root=bundle_path.parent,
                    )
                )
                selected_articles = selected_articles[
                    : editorial_briefings.DAILY_MAX_ARTICLES
                ]
                if not selected_articles and qualified_article_count:
                    return _skip_image_quality_failure("daily", key, image_audit)
                # Recompute final rendered counters after optional backfill/drop,
                # while retaining attempt failures from the complete pool.
                selected_image_ids = {
                    editorial_briefings.editorial_article_id(article)
                    for article in selected_articles
                }
                publication_asset_payloads = [
                    item
                    for item in publication_asset_payloads
                    if item["article_id"] in selected_image_ids
                ]
                image_audit = editorial_briefings.image_audit_manifest(
                    selected_articles,
                    image_materialization_failed_count=image_audit[
                        "image_materialization_failed_count"
                    ],
                    image_quality_rejected_count=image_audit[
                        "image_quality_rejected_count"
                    ],
                    failure_reasons=image_audit["failure_reasons"],
                    publication_assets=[
                        item
                        for item in image_audit["publication_image_assets"]
                        if item["article_id"] in selected_image_ids
                    ],
                )
            edition = editorial_briefings.render_daily(
                selected_articles,
                run_at=run_at,
                root_url=root_url,
                review_mode=review_mode,
                review_decision=review_decision,
                editor_console_available=True,
            )
            edition = replace(edition, image_audit=image_audit)
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
            # R4-R11 §2.5 — the reader-only live-collection fallback is
            # removed. Without this edition's review bundle the exact-edition
            # editor can never be faithfully reconstructed (fresh collection is
            # not the console's candidate pool), so the publication skips
            # fail-closed with a machine-readable reason and the later
            # scheduled attempt is the only retry path.
            return _skip_daily_editor_unavailable(
                key, review_decision, "daily_review_bundle_unavailable"
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
        articles = editorial_briefings.normalize_articles(
            raw_articles,
            editorial_briefings.weekly_coverage(run_at),
            limit=editorial_briefings.WEEKLY_MAX_ARTICLES * 2,
            resolve_images=True,
            allow_image_network=True,
            selection_mode=(
                editorial_briefings.SELECTION_MODE_EDITORIAL_PRIORITY
            ),
            selection_audit=selection_counters,
            edition_type="weekly",
        )
        if not articles:
            return _skip_insufficient_quality(edition_type, key, review_decision)
        qualified_article_count = len(articles)
        articles, image_audit, publication_asset_payloads = prepare_publication_images(
            articles,
            edition_type="weekly",
            edition_key=key,
            publication_dir=dated_path.parent,
        )
        articles = articles[: editorial_briefings.WEEKLY_MAX_ARTICLES]
        if not articles and qualified_article_count:
            return _skip_image_quality_failure("weekly", key, image_audit)
        selected_image_ids = {
            editorial_briefings.editorial_article_id(article)
            for article in articles
        }
        publication_asset_payloads = [
            item
            for item in publication_asset_payloads
            if item["article_id"] in selected_image_ids
        ]
        image_audit = editorial_briefings.image_audit_manifest(
            articles,
            image_materialization_failed_count=image_audit[
                "image_materialization_failed_count"
            ],
            image_quality_rejected_count=image_audit[
                "image_quality_rejected_count"
            ],
            failure_reasons=image_audit["failure_reasons"],
            publication_assets=[
                item
                for item in image_audit["publication_image_assets"]
                if item["article_id"] in selected_image_ids
            ],
        )
        edition = editorial_briefings.render_weekly(
            articles,
            run_at=run_at,
            root_url=root_url,
        )
        edition = replace(edition, image_audit=image_audit)
    print(f"editorial_review_mode={review_mode}")
    print(f"editorial_review_decision={review_decision}")
    _github_output("review_mode", review_mode)
    _github_output("review_decision", review_decision)
    editorial_briefings.validate_rendered(edition)
    image_gate_error = editorial_briefings.production_image_gate_error(
        edition.image_audit
    )
    if edition.article_count and image_gate_error:
        raise OrchestratorError(f"production image gate failed: {image_gate_error}")
    rendered_image_gate_error = (
        editorial_briefings.production_rendered_image_gate_error(
            edition.html,
            edition_type=edition_type,
            edition_key=edition.edition_key,
            manifest=edition.image_audit,
        )
    )
    if edition.article_count and rendered_image_gate_error:
        raise OrchestratorError(
            f"production rendered image gate failed: {rendered_image_gate_error}"
        )
    if edition_type == "daily":
        # R4-R11 §2.4 — before anything publishes, the exact-edition editor
        # must be provably reconstructable: editor deep link minted, immutable
        # manifest verified, dated console present, and every manifest article
        # adopting into a distinct console candidate (the same replay the
        # browser performs). Failure publishes nothing and sends nothing.
        editor_gate_error = daily_editor_publication_error(edition)
        if editor_gate_error:
            return _skip_daily_editor_unavailable(
                edition.edition_key, review_decision, editor_gate_error
            )
    image_manifest_output = ""
    if edition.article_count:
        image_manifest_output = write_publication_image_assets(
            edition_type=edition_type,
            edition_key=edition.edition_key,
            publication_dir=dated_path.parent,
            audit=image_audit,
            asset_payloads=publication_asset_payloads,
        )
    payload = edition.html.encode("utf-8")
    editorial_briefings.atomic_write_bytes(dated_path, payload)
    editorial_briefings.atomic_write_bytes(latest_path, payload)
    if dated_path.read_bytes() != latest_path.read_bytes():
        raise OrchestratorError("dated/latest bytes differ after publication write")
    edition_manifest_output = ""
    if edition_type == "daily":
        # The editor gate above guarantees editor_url and a verified manifest;
        # the immutable edition manifest is published for every delivered Daily.
        edition_manifest_output = write_daily_edition_manifest(edition)
    manifest = editorial_briefings.manifest_for_runtime(edition, dated_path, latest_path)
    if edition_manifest_output:
        manifest["edition_manifest_path"] = edition_manifest_output
    if image_manifest_output:
        manifest["image_manifest_path"] = image_manifest_output
        manifest["image_manifest_sha256"] = hashlib.sha256(
            (ROOT / image_manifest_output).read_bytes()
        ).hexdigest()
    # Only the real production publisher mints this authority. Generic
    # fixture/runtime helpers remain usable for non-production contract tests,
    # but every publish -> verify -> claim -> send path carries and rechecks it.
    manifest["production_image_gate_required"] = True
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
    _github_output("image_manifest_path", image_manifest_output)
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


def verify_public_edition_manifest_once(
    url: str,
    expected_edition_id: str,
    *,
    opener: Callable | None = None,
) -> bool:
    """True only when the public immutable edition manifest resolves (HTTP 200),
    parses, reconstructs (``verify_daily_edition_manifest`` returns "") and
    carries the exact expected edition id. Any deviation returns False so the
    caller fails closed."""
    if not editorial_briefings.valid_http_url(url) or not expected_edition_id:
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
    try:
        payload = json.loads(body)
    except ValueError:
        return False
    if editorial_briefings.verify_daily_edition_manifest(payload):
        return False
    return str(payload.get("edition_id") or "") == expected_edition_id


def verify_public_image_once(
    url: str,
    asset: dict,
    *,
    opener: Callable | None = None,
) -> bool:
    """Verify an exact public raster by bytes and digest; extensions do not count."""
    if not editorial_briefings.valid_http_url(url):
        return False
    expected_sha = str(asset.get("sha256") or "")
    expected_size = asset.get("byte_size")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha) or type(expected_size) is not int:
        return False
    open_url = opener or urllib.request.urlopen
    try:
        response = open_url(url, timeout=15)
        with response:
            if _response_status(response) != 200:
                return False
            payload = response.read(editorial_briefings.IMAGE_DOWNLOAD_MAX_BYTES + 1)
    except (OSError, TimeoutError, urllib.error.URLError, ValueError):
        return False
    return (
        len(payload) == expected_size
        and hashlib.sha256(payload).hexdigest() == expected_sha
        and bool(editorial_briefings._raster_content_type(payload))
        and editorial_briefings.assess_daily_image_asset(payload).valid
    )


def poll_public_edition_manifest(
    url: str,
    expected_edition_id: str,
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
        if verify_public_edition_manifest_once(url, expected_edition_id, opener=opener):
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
    image_gate_required = manifest.get("production_image_gate_required") is True
    image_gate_error = editorial_briefings.production_image_gate_error(manifest)
    if article_count := int(manifest.get("article_count") or 0):
        if not image_gate_required:
            return
        if image_gate_error:
            raise OrchestratorError(f"production image gate failed: {image_gate_error}")
        assets = manifest.get("publication_image_assets")
        if not isinstance(assets, list) or len(assets) != article_count:
            raise OrchestratorError("publication image asset count mismatch")
        image_manifest_path = str(manifest.get("image_manifest_path") or "")
        expected_manifest_path = (
            f"docs/editorial/{manifest['edition_type']}/assets/"
            f"{manifest['edition_key']}/image-manifest.json"
        )
        if image_manifest_path != expected_manifest_path:
            raise OrchestratorError("publication image manifest path mismatch")
        try:
            image_manifest_bytes = (ROOT / image_manifest_path).read_bytes()
            image_manifest = json.loads(image_manifest_bytes)
        except (OSError, ValueError) as exc:
            raise OrchestratorError("publication image manifest missing or malformed") from exc
        if (
            hashlib.sha256(image_manifest_bytes).hexdigest()
            != manifest.get("image_manifest_sha256")
            or image_manifest.get("edition_type") != manifest["edition_type"]
            or image_manifest.get("edition_key") != manifest["edition_key"]
            or image_manifest.get("publication_image_assets") != assets
            or editorial_briefings.production_image_gate_error(image_manifest)
        ):
            raise OrchestratorError("publication image manifest validation failed")
        expected_prefix = (
            f"editorial/{manifest['edition_type']}/assets/"
            f"{manifest['edition_key']}/"
        )
        for asset in assets:
            if not isinstance(asset, dict):
                raise OrchestratorError("publication image asset record malformed")
            relative = str(asset.get("relative_path") or "")
            filename = relative.removeprefix(expected_prefix)
            if (
                not relative.startswith(expected_prefix)
                or not filename
                or "/" in filename
                or "\\" in filename
            ):
                raise OrchestratorError("publication image path identity mismatch")
            image_path = dated.parent / "assets" / manifest["edition_key"] / filename
            try:
                image_payload = image_path.read_bytes()
            except OSError as exc:
                raise OrchestratorError("publication image asset missing") from exc
            if (
                hashlib.sha256(image_payload).hexdigest() != asset.get("sha256")
                or len(image_payload) != asset.get("byte_size")
                or not editorial_briefings._raster_content_type(image_payload)
                or not editorial_briefings.assess_daily_image_asset(image_payload).valid
            ):
                raise OrchestratorError("publication image asset validation failed")
        rendered_gate_error = editorial_briefings.production_rendered_image_gate_error(
            text,
            edition_type=manifest["edition_type"],
            edition_key=manifest["edition_key"],
            manifest=manifest,
        )
        if rendered_gate_error:
            raise OrchestratorError(
                f"rendered image references invalid: {rendered_gate_error}"
            )


def _manifest_identity(manifest: dict) -> dict:
    article_count = int(manifest.get("article_count") or 0)
    return {
        "edition_key": manifest["edition_key"],
        "coverage_start": manifest["coverage_start"],
        "coverage_end": manifest["coverage_end"],
        "html_sha256": manifest["html_sha256"],
        "public_url": manifest["public_dated_url"],
        "delivery_kind": (
            "empty_status" if article_count == 0 else "nonempty_digest"
        ),
        "article_count": article_count,
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


def run_verify_public(
    edition_type: str,
    *,
    run_at: datetime,
    runtime_dir: Path,
    opener: Callable | None = None,
    publication_timeout_seconds: int = PUBLICATION_TIMEOUT_SECONDS,
    publication_interval_seconds: int = PUBLICATION_INTERVAL_SECONDS,
    sleeper: Callable[[float], None] = time.sleep,
) -> bool:
    """R4-R12 §6 step 10 — verify every exact immutable Daily resource publicly
    resolves and reconstructs, strictly after the publication commit/push and
    strictly before ``run_claim``.

    For Daily this reconstructs both the dated reader page and the content
    -addressed immutable edition manifest. Any failure emits the machine-readable
    ``daily_public_resource_verification_failed`` skip reason and fails closed, so
    the later claim/send workflow steps (gated on ``success()``) never run.
    Reader-only delivery stays impossible: no verified manifest → no claim, and
    ``run_send`` refuses to send without the durable claim."""
    _require_production_gate()
    key = editorial_briefings.edition_key(edition_type, run_at)
    manifest = _load_runtime_manifest(runtime_dir, edition_type)
    if manifest["edition_key"] != key:
        raise OrchestratorError("runtime edition does not match current catch-up edition")
    if (
        os.environ.get("GITHUB_ACTIONS") == "true"
        and manifest.get("production_image_gate_required") is not True
    ):
        raise OrchestratorError("production image gate authority missing")
    _verify_local_publication(manifest)
    root_url = editorial_briefings.derive_public_root(os.environ.get("REPORT_URL", ""))
    dated_url, latest_url = editorial_briefings.public_urls(root_url, edition_type, key)
    if manifest["public_dated_url"] != dated_url or manifest["public_latest_url"] != latest_url:
        raise OrchestratorError("runtime public URL mismatch")

    def _fail(reason: str) -> None:
        _github_output("resources_verified", "false")
        _github_output("skip_reason", reason)
        print(
            f"public_resource_skip edition_type={edition_type} edition={key} "
            f"reason={reason}"
        )
        print(
            "daily_reader_only_send_allowed="
            + str(daily_publication.READER_ONLY_SEND_ALLOWED).lower()
        )
        raise OrchestratorError(reason)

    if not poll_public_dated_page(
        dated_url,
        key,
        timeout_seconds=publication_timeout_seconds,
        interval_seconds=publication_interval_seconds,
        opener=opener,
        sleeper=sleeper,
    ):
        _fail(SKIP_PUBLIC_RESOURCE_VERIFICATION)
    if edition_type == "daily":
        edition_id = str(manifest.get("edition_id") or "")
        manifest_url = public_url_contract.daily_edition_manifest_url(
            edition_id, root_url=root_url
        )
        if not poll_public_edition_manifest(
            manifest_url,
            edition_id,
            timeout_seconds=publication_timeout_seconds,
            interval_seconds=publication_interval_seconds,
            opener=opener,
            sleeper=sleeper,
        ):
            _fail(SKIP_PUBLIC_RESOURCE_VERIFICATION)
    for asset in manifest.get("publication_image_assets") or []:
        asset_url = root_url.rstrip("/") + "/" + str(asset["relative_path"])
        if not verify_public_image_once(asset_url, asset, opener=opener):
            _fail("public_image_asset_verification_failed")
    if (
        manifest.get("article_count")
        and manifest.get("production_image_gate_required") is True
    ):
        image_manifest_url = (
            root_url.rstrip("/")
            + "/"
            + str(manifest["image_manifest_path"]).removeprefix("docs/")
        )
        expected_digest = str(manifest.get("image_manifest_sha256") or "")
        try:
            response = (opener or urllib.request.urlopen)(image_manifest_url, timeout=15)
            with response:
                public_image_manifest = response.read(2_000_000)
        except (OSError, TimeoutError, urllib.error.URLError, ValueError):
            _fail("public_image_manifest_verification_failed")
        if hashlib.sha256(public_image_manifest).hexdigest() != expected_digest:
            _fail("public_image_manifest_verification_failed")
    _github_output("resources_verified", "true")
    _github_output("edition", key)
    print(
        f"public_resources_verified edition_type={edition_type} edition={key} "
        f"dated_page=200 publication_images={len(manifest.get('publication_image_assets') or [])} "
        "edition_manifest=reconstructed "
        "smtp_attempts=0 teams_sends=0 telegram_calls=0 state_writes=0"
    )
    return True


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
    if (
        os.environ.get("GITHUB_ACTIONS") == "true"
        and manifest.get("production_image_gate_required") is not True
    ):
        raise OrchestratorError("production image gate authority missing")
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
    if (
        os.environ.get("GITHUB_ACTIONS") == "true"
        and manifest.get("production_image_gate_required") is not True
    ):
        raise OrchestratorError("production image gate authority missing")
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
    mode.add_argument("--verify-public", action="store_true")
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
            run_live_preview(
                run_at=run_at,
                preview_root=Path(args.preview_root),
                fixture_root=args.fixture_root,
                edition_type=args.edition_type,
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
        elif args.verify_public:
            run_verify_public(
                args.edition_type,
                run_at=run_at,
                runtime_dir=_runtime_dir(args.runtime_dir, args.edition_type),
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
