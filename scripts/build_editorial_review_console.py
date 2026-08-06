#!/usr/bin/env python3
"""Build the Daily Editorial Review Console and candidate bundle."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
for path in (ROOT, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from app import (collector, editorial_briefings, editorial_feedback, editorial_review, live_collector)  # noqa: E402
from app.editorial_briefings import KST  # noqa: E402
from run_editorial_briefing import collect_live_article_bundle  # noqa: E402

TEMPLATE = ROOT / "templates" / "editorial_review_console.html"
DEFAULT_PROFILE = ROOT / "data" / "editorial_feedback" / "profile.json"
FIXTURE_IMAGE_NAME = "editorial-review-fixture.svg"
FIXTURE_IMAGE_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="960" height="540" viewBox="0 0 960 540">
<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#002c5f"/><stop offset="1" stop-color="#0e63b8"/></linearGradient></defs>
<rect width="960" height="540" rx="32" fill="url(#g)"/>
<circle cx="790" cy="112" r="168" fill="#fff" opacity=".08"/>
<path d="M108 348h744M108 394h520" stroke="#fff" stroke-width="18" stroke-linecap="round" opacity=".22"/>
<text x="108" y="225" fill="#fff" font-family="Arial,sans-serif" font-size="74" font-weight="700">HDEC AI</text>
<text x="112" y="286" fill="#fff" font-family="Arial,sans-serif" font-size="30" opacity=".82">EDITORIAL REVIEW</text>
</svg>
"""


def parse_run_at(value: str) -> datetime:
    raw = (value or "").strip()
    if not raw:
        return datetime.now(KST)
    if len(raw) == 10:
        raw += "T07:20:00+09:00"
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        raise ValueError("--run-at must include timezone")
    return parsed.astimezone(KST)


def render_console(template: str, bundle: dict) -> str:
    embedded = json.dumps(bundle, ensure_ascii=False).replace("</", "<\\/")
    return (
        template.replace("{{EDITION_KEY}}", bundle["edition_key"])
        .replace("{{COVERAGE_LABEL}}", f"{bundle['coverage_start']} ~ {bundle['coverage_end']}")
        .replace("{{CANDIDATE_JSON}}", embedded)
    )


def normalize_article_import_api_url(value: object) -> str:
    """Allow an explicit HTTPS endpoint (or loopback HTTP for local verification)."""
    candidate = str(value or "").strip().rstrip("/")
    if not candidate:
        return ""
    try:
        parsed = urlparse(candidate)
        port = parsed.port
    except (TypeError, ValueError):
        return ""
    if (
        parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not parsed.hostname
    ):
        return ""
    loopback = parsed.hostname.casefold() in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and not (parsed.scheme == "http" and loopback):
        return ""
    if port is not None and not (
        (parsed.scheme == "https" and port == 443)
        or (parsed.scheme == "http" and loopback)
    ):
        return ""
    if parsed.path.rstrip("/") != "/api/editorial/import-article":
        return ""
    return candidate


def fixture_preview_images(
    articles: Iterable[editorial_briefings.EditorialArticle],
    preview_root: Path,
) -> tuple[
    list[editorial_briefings.EditorialArticle],
    editorial_briefings.ImageMaterializationCounters,
]:
    """Create a deterministic browser-loadable image without network access."""
    assets_dir = preview_root / "assets" / "images"
    assets_dir.mkdir(parents=True, exist_ok=True)
    (assets_dir / FIXTURE_IMAGE_NAME).write_text(
        FIXTURE_IMAGE_SVG,
        encoding="utf-8",
    )
    local_src = f"assets/images/{FIXTURE_IMAGE_NAME}"
    materialized = [
        replace(
            article,
            image_url=local_src,
            image_remote_url="",
            image_source_kind="fixture_local",
            image_fallback_used=False,
            image_reason="deterministic_fixture_asset",
            image_download_status="not_attempted",
            image_local_asset=FIXTURE_IMAGE_NAME,
            image_local_src=local_src,
            image_materialization_reason="fixture_local_asset",
            image_quality_accepted=True,
            image_quality_reason="fixture_local_asset",
        )
        for article in articles
    ]
    counters = editorial_briefings.ImageMaterializationCounters(
        image_assets_materialized=1,
    )
    return materialized, counters


def copy_preview_assets(source_root: Path, destination_dir: Path) -> None:
    source = source_root / "assets" / "images"
    target = destination_dir / "assets" / "images"
    target.mkdir(parents=True, exist_ok=True)
    if source.exists():
        for asset in source.iterdir():
            if asset.is_file():
                shutil.copy2(asset, target / asset.name)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-at", default="")
    parser.add_argument("--output-root", type=Path, default=ROOT / "docs" / "editorial" / "review")
    parser.add_argument("--candidate-limit", type=int, default=24)
    parser.add_argument("--fixture", action="store_true")
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument(
        "--article-import-api-url",
        default=os.environ.get("ARTICLE_IMPORT_API_URL", ""),
        help=(
            "Public authenticated endpoint URL; empty keeps URL import disabled. "
            "No credential or secret is embedded."
        ),
    )
    args = parser.parse_args()

    run_at = parse_run_at(args.run_at)
    edition_key = editorial_briefings.edition_key("daily", run_at)
    coverage = editorial_briefings.daily_coverage(run_at)
    limit = max(editorial_briefings.DAILY_MAX_ARTICLES, min(30, args.candidate_limit))

    profile = editorial_feedback.load_profile(args.profile)
    if args.fixture:
        raw_articles = (
            editorial_briefings.fixture_articles("daily", run_at, profile="dominant")
            + editorial_briefings.fixture_articles("daily", run_at, profile="multi")
        )
        collection_audit = {
            "mode": "fixture",
            "network_calls": 0,
            "feedback_queries_attempted": 0,
            "feedback_articles_collected": 0,
        }
    else:
        raw_articles, collection_audit = collect_live_article_bundle()
        feedback_queries = editorial_feedback.collection_queries(profile)
        feedback_rows = []
        if feedback_queries:
            with tempfile.TemporaryDirectory(prefix="editorial-feedback-query-") as tmp:
                sources = Path(tmp) / "sources.json"
                sources.write_text(
                    json.dumps(
                        {
                            "hl": "ko",
                            "gl": "KR",
                            "ceid": "KR:ko",
                            "queries": feedback_queries,
                            "max_per_query": 2,
                            "max_total": 12,
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                feedback_rows = live_collector.fetch_all(sources_path=sources)
            raw_articles = collector.merge_provider_articles(
                list(raw_articles) + list(feedback_rows)
            )
        collection_audit["feedback_queries_attempted"] = len(feedback_queries)
        collection_audit["feedback_articles_collected"] = len(feedback_rows)

    selection_counters = editorial_briefings.SelectionAuditCounters()
    articles = editorial_briefings.normalize_articles(
        raw_articles,
        coverage,
        limit=limit,
        resolve_images=not args.fixture,
        allow_image_network=not args.fixture,
        selection_mode=editorial_briefings.SELECTION_MODE_EDITORIAL_PRIORITY,
        selection_audit=selection_counters,
        edition_type="daily",
        operator_review=True,
    )
    if not articles:
        raise SystemExit("no candidate articles in Daily coverage")

    image_stage_handle = tempfile.TemporaryDirectory(
        prefix="editorial-review-images-",
        dir="/tmp",
    )
    image_stage = Path(image_stage_handle.name)
    if args.fixture:
        articles, image_counters = fixture_preview_images(articles, image_stage)
    else:
        articles, image_counters = editorial_briefings.materialize_preview_images(
            articles,
            image_stage,
            html_dir=image_stage,
            daily=True,
        )

    candidates = []
    for rank, article in enumerate(articles, 1):
        base = editorial_review.article_to_candidate(article, ai_rank=rank)
        adjustment = editorial_feedback.adjustment(base, profile)
        candidates.append(
            editorial_review.article_to_candidate(
                article,
                ai_rank=rank,
                feedback_adjustment=adjustment,
            )
        )

    # Default report order required by the editor. Human drag-and-drop may override it.
    candidates.sort(
        key=lambda item: (
            int(bool(item.get("supporting_evidence_only"))),
            int(
                item.get("editorial_lane") == "public_institution"
                and not item.get("main_surface_eligible")
            ),
            editorial_review.category_rank(item.get("category")),
            -float(item["adjusted_score"]),
            int(item["ai_rank"]),
        )
    )
    headline_available = any(
        bool(item.get("headline_eligible"))
        and not bool(item.get("supporting_evidence_only"))
        for item in candidates
    )
    recommendation_count = 0
    for rank, item in enumerate(candidates, 1):
        item["adjusted_rank"] = rank
        publication_eligible = (
            not bool(item.get("supporting_evidence_only"))
            and (
                item.get("editorial_lane") != "public_institution"
                or bool(item.get("tni_brief_eligible"))
            )
        )
        recommended = (
            headline_available
            and publication_eligible
            and recommendation_count < editorial_briefings.DAILY_MAX_ARTICLES
        )
        item["ai_recommended"] = recommended
        if recommended:
            recommendation_count += 1

    output_root = args.output_root.resolve()
    edition_dir = output_root / edition_key
    latest_dir = output_root / "latest"
    generated_at = datetime.now(KST).isoformat(timespec="seconds")
    article_import_api_url = normalize_article_import_api_url(
        args.article_import_api_url
    )

    bundle = editorial_review.write_bundle(
        edition_key=edition_key,
        coverage_start=coverage.start.isoformat(),
        coverage_end=coverage.end.isoformat(),
        candidates=candidates,
        path=edition_dir / "candidates.json",
        generated_at=generated_at,
    )
    bundle["collection_audit"] = collection_audit
    bundle["feedback_profile_version"] = profile.get("version", 2)
    bundle["article_import_api_url"] = article_import_api_url
    bundle["article_import_enabled"] = bool(article_import_api_url)
    bundle["selection_audit"] = selection_counters.manifest_fields()
    (edition_dir / "candidates.json").write_text(
        json.dumps(bundle, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    html = render_console(TEMPLATE.read_text(encoding="utf-8"), bundle)
    (edition_dir / "index.html").write_text(html, encoding="utf-8")
    copy_preview_assets(image_stage, edition_dir)

    latest_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(edition_dir / "candidates.json", latest_dir / "candidates.json")
    shutil.copyfile(edition_dir / "index.html", latest_dir / "index.html")
    copy_preview_assets(image_stage, latest_dir)

    manifest = {
        "version": 2,
        "edition_key": edition_key,
        "category_order": list(editorial_review.CATEGORY_ORDER),
        "candidate_count": len(candidates),
        "main_candidate_lane_count": selection_counters.main_candidate_lane_count,
        "public_institution_lane_count": (
            selection_counters.public_institution_lane_count
        ),
        "promoted_public_candidate_count": (
            selection_counters.promoted_public_candidate_count
        ),
        "non_promoted_public_candidate_count": (
            selection_counters.non_promoted_public_candidate_count
        ),
        "generated_at": generated_at,
        "edition_console": str(edition_dir / "index.html"),
        "latest_console": str(latest_dir / "index.html"),
        "network_sends": 0,
        "smtp_attempts": 0,
        "teams_sends": 0,
        "telegram_sends": 0,
        "production_state_writes": 0,
        **selection_counters.manifest_fields(),
        "article_import_api_configured": bool(article_import_api_url),
        "image_network_calls": image_counters.image_download_attempts,
        **image_counters.manifest_fields(),
    }
    (edition_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    shutil.copyfile(edition_dir / "manifest.json", latest_dir / "manifest.json")
    image_stage_handle.cleanup()

    print(f"edition_key={edition_key}")
    print(f"candidate_count={len(candidates)}")
    print("category_order=투자·산업>기업동향>기술정보")
    print(f"console={edition_dir / 'index.html'}")
    print("smtp_attempts=0")
    print("teams_sends=0")
    print("telegram_sends=0")
    print("production_state_writes=0")
    print(f"article_import_api_configured={str(bool(article_import_api_url)).lower()}")
    print(f"image_assets_materialized={image_counters.image_assets_materialized}")
    print("RESULT=D7-AK-6E-R3_REVIEW_CONSOLE_BUILD_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
