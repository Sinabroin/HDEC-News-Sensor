#!/usr/bin/env python3
"""Build the Daily Editorial Review Console and candidate bundle."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-at", default="")
    parser.add_argument("--output-root", type=Path, default=ROOT / "docs" / "editorial" / "review")
    parser.add_argument("--candidate-limit", type=int, default=24)
    parser.add_argument("--fixture", action="store_true")
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    args = parser.parse_args()

    run_at = parse_run_at(args.run_at)
    edition_key = editorial_briefings.edition_key("daily", run_at)
    coverage = editorial_briefings.daily_coverage(run_at)
    limit = max(editorial_briefings.DAILY_MAX_ARTICLES, min(30, args.candidate_limit))

    profile = editorial_feedback.load_profile(args.profile)
    if args.fixture:
        raw_articles = editorial_briefings.fixture_articles("daily", run_at, profile="dominant")
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

    articles = editorial_briefings.normalize_articles(
        raw_articles,
        coverage,
        limit=limit,
        resolve_images=not args.fixture,
        allow_image_network=not args.fixture,
        selection_mode=editorial_briefings.SELECTION_MODE_EDITORIAL_PRIORITY,
    )
    if not articles:
        raise SystemExit("no candidate articles in Daily coverage")

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
            editorial_review.category_rank(item.get("category")),
            -float(item["adjusted_score"]),
            int(item["ai_rank"]),
        )
    )
    for rank, item in enumerate(candidates, 1):
        item["adjusted_rank"] = rank
        item["ai_recommended"] = rank <= editorial_briefings.DAILY_MAX_ARTICLES

    output_root = args.output_root.resolve()
    edition_dir = output_root / edition_key
    latest_dir = output_root / "latest"
    generated_at = datetime.now(KST).isoformat(timespec="seconds")

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
    (edition_dir / "candidates.json").write_text(
        json.dumps(bundle, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    html = render_console(TEMPLATE.read_text(encoding="utf-8"), bundle)
    (edition_dir / "index.html").write_text(html, encoding="utf-8")

    latest_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(edition_dir / "candidates.json", latest_dir / "candidates.json")
    shutil.copyfile(edition_dir / "index.html", latest_dir / "index.html")

    manifest = {
        "version": 2,
        "edition_key": edition_key,
        "category_order": list(editorial_review.CATEGORY_ORDER),
        "candidate_count": len(candidates),
        "generated_at": generated_at,
        "edition_console": str(edition_dir / "index.html"),
        "latest_console": str(latest_dir / "index.html"),
        "network_sends": 0,
        "smtp_attempts": 0,
        "teams_sends": 0,
        "telegram_sends": 0,
        "production_state_writes": 0,
    }
    (edition_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    shutil.copyfile(edition_dir / "manifest.json", latest_dir / "manifest.json")

    print(f"edition_key={edition_key}")
    print(f"candidate_count={len(candidates)}")
    print("category_order=투자·산업>기업동향>기술정보")
    print(f"console={edition_dir / 'index.html'}")
    print("smtp_attempts=0")
    print("teams_sends=0")
    print("telegram_sends=0")
    print("production_state_writes=0")
    print("RESULT=D7-AK-6E-R3_REVIEW_CONSOLE_BUILD_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
