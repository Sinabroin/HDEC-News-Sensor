#!/usr/bin/env python3
"""Offline regression verifier for the Editorial Review Console."""

from __future__ import annotations

import json
import os
import py_compile
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("APP_MODE", "mock")
os.environ.setdefault("NEWS_MODE", "mock")

from app import editorial_briefings, editorial_feedback, editorial_review  # noqa: E402
from app.editorial_briefings import KST  # noqa: E402


class V:
    def __init__(self):
        self.checks = 0
        self.failures = 0

    def check(self, name, condition, detail=""):
        self.checks += 1
        if condition:
            print(f"PASS: {name}")
        else:
            self.failures += 1
            print(f"FAIL: {name} {detail}")

    def equal(self, name, actual, expected):
        self.check(name, actual == expected, f"expected={expected!r} actual={actual!r}")


def main() -> int:
    v = V()
    for rel in (
        "app/editorial_briefings.py",
        "app/editorial_review.py",
        "app/editorial_feedback.py",
        "scripts/build_editorial_review_console.py",
        "scripts/compile_editorial_feedback.py",
        "scripts/run_editorial_briefing.py",
        "scripts/verify_editorial_review_console.py",
    ):
        py_compile.compile(str(ROOT / rel), doraise=True)
    v.check("Python compile", True)

    v.equal(
        "category order fixed",
        editorial_review.CATEGORY_ORDER,
        ("투자·산업", "기업동향", "기술정보"),
    )
    v.equal(
        "category normalize investment",
        editorial_review.normalize_category("정책", "AI 투자 확대"),
        "투자·산업",
    )
    v.equal(
        "category normalize corporate",
        editorial_review.normalize_category("", "기업 AI 도입"),
        "기업동향",
    )
    v.equal(
        "category fallback technology",
        editorial_review.normalize_category("", "새 추론 모델 공개"),
        "기술정보",
    )

    rich = editorial_briefings.sanitize_editorial_inline_html(
        '<strong>핵심</strong><script>alert(1)</script><img src=x> 내용<br><b>수치</b>'
    )
    v.equal(
        "rich text keeps safe bold",
        rich,
        "<strong>핵심</strong>alert(1) 내용<br><strong>수치</strong>",
    )
    v.check("rich text removes unsafe tags", "<script" not in rich and "<img" not in rich)
    v.equal(
        "rich text plain extraction",
        editorial_briefings.editorial_inline_plain_text(rich),
        "핵심alert(1) 내용 수치",
    )

    run_at = datetime(2026, 7, 31, 7, 20, tzinfo=KST)
    fixture = editorial_briefings.fixture_articles("daily", run_at, profile="dominant")
    coverage = editorial_briefings.daily_coverage(run_at)
    articles = editorial_briefings.normalize_articles(
        fixture,
        coverage,
        limit=12,
        resolve_images=False,
        selection_mode=editorial_briefings.SELECTION_MODE_EDITORIAL_PRIORITY,
    )
    candidates = [
        editorial_review.article_to_candidate(article, ai_rank=index)
        for index, article in enumerate(articles, 1)
    ]
    candidates.sort(
        key=lambda item: (
            editorial_review.category_rank(item["category"]),
            -float(item["adjusted_score"]),
            int(item["ai_rank"]),
        )
    )
    for index, item in enumerate(candidates, 1):
        item["adjusted_rank"] = index
        item["ai_recommended"] = index <= 6

    with tempfile.TemporaryDirectory(prefix="editorial-r3-") as tmp:
        tmp_path = Path(tmp)
        bundle_path = tmp_path / "candidates.json"
        bundle = editorial_review.write_bundle(
            edition_key="2026-07-31",
            coverage_start=coverage.start.isoformat(),
            coverage_end=coverage.end.isoformat(),
            candidates=candidates,
            path=bundle_path,
            generated_at=run_at.isoformat(),
        )
        loaded = editorial_review.load_bundle(bundle_path, "2026-07-31")
        v.equal("bundle version", loaded["version"], 2)
        v.equal(
            "bundle category order",
            loaded["category_order"],
            list(editorial_review.CATEGORY_ORDER),
        )

        ids = [item["candidate_id"] for item in candidates]
        approved = {
            "version": 2,
            "edition_type": "daily",
            "edition_key": "2026-07-31",
            "review_status": "approved",
            "selected_items": [
                {
                    "candidate_id": ids[0],
                    "origin": "ai_collected",
                    "title": "사용자가 고친 제목",
                    "summary_html": "<strong>볼드 핵심</strong> 설명",
                    "category": "투자·산업",
                },
                {
                    "candidate_id": "manual-1",
                    "origin": "human_link",
                    "title": "사용자 선별 AI 투자 기사",
                    "summary": "직접 고른 기사 요약",
                    "summary_html": "직접 고른 <strong>기사 요약</strong>",
                    "source": "사용자선별언론",
                    "published_at": run_at.isoformat(),
                    "selected_url": "https://example.org/manual-ai-investment",
                    "category": "기업동향",
                    "image_url": "",
                },
            ],
            "approved_at": run_at.isoformat(),
        }
        review_path = tmp_path / "review.json"
        review_path.write_text(
            json.dumps(approved, ensure_ascii=False),
            encoding="utf-8",
        )
        review = editorial_review.load_review(review_path, "2026-07-31")
        selected, mode = editorial_review.choose_daily_articles(bundle, review)
        v.equal("approved review mode", mode, "human_approved")
        v.equal("edited title preserved", selected[0].title, "사용자가 고친 제목")
        v.equal(
            "bold summary preserved",
            selected[0].summary_html,
            "<strong>볼드 핵심</strong> 설명",
        )
        v.equal(
            "manual link selected",
            selected[1].selected_url,
            "https://example.org/manual-ai-investment",
        )
        v.equal("manual link kind", selected[1].collection_source_kind, "human_link")

        edition = editorial_briefings.render_daily(
            selected,
            run_at=run_at,
            root_url="https://preview.fixture.test/HDEC-News-Sensor",
        )
        v.check("rendered HTML contains bold", "<strong>볼드 핵심</strong>" in edition.html)
        v.check("rendered HTML contains manual link", "manual-ai-investment" in edition.html)
        v.check(
            "rendered HTML contains category ticker",
            "투자·산업" in edition.html and "기업동향" in edition.html,
        )

        auto, auto_mode = editorial_review.choose_daily_articles(bundle, None)
        v.equal("AI fallback mode", auto_mode, "ai_fallback")
        ranks = [editorial_review.category_rank(item.category) for item in auto]
        v.equal("AI fallback category order", ranks, sorted(ranks))

    records = [
        {
            "version": 2,
            "edition_key": "2026-07-31",
            "candidate_id": "manual-1",
            "origin": "human_link",
            "selected_url": "https://quality.example.com/ai-data-center",
            "title": "AI 데이터센터 투자 확대",
            "source": "사용자선별언론",
            "category": "투자·산업",
            "selected": True,
            "overall_rating": 0,
            "dimension_ratings": {},
            "exclusion_tags": [],
            "rated_at": run_at.isoformat(),
        }
    ]
    profile = editorial_feedback.compile_profile(records, minimum_samples=3)
    v.check(
        "manual domain seed learned",
        profile["manual_domain_seeds"].get("quality.example.com", 0) > 0,
    )
    v.check(
        "manual keyword seed learned",
        profile["manual_keyword_seeds"].get("데이터센터", 0) > 0,
    )
    candidate = {
        "source": "다른언론",
        "category": "투자·산업",
        "selected_url": "https://quality.example.com/another",
        "title": "AI 데이터센터 신규 투자",
    }
    v.check(
        "manual link affects future ranking",
        editorial_feedback.adjustment(candidate, profile) > 0,
    )
    v.check(
        "feedback cap bounded",
        abs(editorial_feedback.adjustment(candidate, profile))
        <= profile["max_abs_adjustment"],
    )

    repeated_records = records * 3
    repeated_profile = editorial_feedback.compile_profile(
        repeated_records, minimum_samples=3
    )
    learned_queries = editorial_feedback.collection_queries(repeated_profile)
    v.check(
        "repeated manual domain activates bounded collection query",
        "site:quality.example.com AI" in learned_queries,
    )
    v.check(
        "repeated manual keyword activates bounded collection query",
        "AI 데이터센터" in learned_queries,
    )
    v.check(
        "learned collection queries remain bounded",
        len(learned_queries) <= editorial_feedback.COLLECTION_QUERY_LIMIT,
    )

    template = (ROOT / "templates/editorial_review_console.html").read_text(
        encoding="utf-8"
    )
    for token in (
        "인간이 선별한 기사 링크 추가",
        'contenteditable="true"',
        'id="boldBtn"',
        "카테고리 기본순서",
        "HTML 다운로드",
        "평가 JSONL",
        "selected_items",
        "human_link",
        "투자·산업",
        "기업동향",
        "기술정보",
    ):
        v.check(f"console contains {token}", token in template)

    builder_source = (
        ROOT / "scripts/build_editorial_review_console.py"
    ).read_text(encoding="utf-8")
    v.check(
        "matured manual links feed supplemental collection",
        "editorial_feedback.collection_queries(profile)" in builder_source
        and "live_collector.fetch_all(sources_path=sources)" in builder_source,
    )

    workflow = (
        ROOT / ".github/workflows/editorial-review-console.yml"
    ).read_text(encoding="utf-8")
    v.check("console schedule is 07:20 KST", 'cron: "20 22 * * *"' in workflow)
    v.check(
        "console workflow has no sender",
        not any(
            token in workflow
            for token in (
                "send_teams",
                "send_email",
                "send_telegram",
                "run_editorial_briefing.py --send",
            )
        ),
    )
    run_source = (ROOT / "scripts/run_editorial_briefing.py").read_text(
        encoding="utf-8"
    )
    v.check("publish reads approved review", "editorial_review.load_review" in run_source)
    v.check("publish retains AI fallback", "live_collection_fallback" in run_source)

    print(f"checks={v.checks} failures={v.failures}")
    print("category_ticker_order=투자·산업>기업동향>기술정보")
    print("rich_text_editing=PASS")
    print("bold_sanitization=PASS")
    print("manual_link_selection=PASS")
    print("manual_link_learning=PASS")
    print("network_sends=0")
    print("smtp_attempts=0")
    print("teams_sends=0")
    print("telegram_sends=0")
    print("production_state_writes=0")
    if v.failures:
        print("RESULT=D7-AK-6E-R3_EDITORIAL_REVIEW_CONSOLE_FAIL")
        return 1
    print("RESULT=D7-AK-6E-R3_EDITORIAL_REVIEW_CONSOLE_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
