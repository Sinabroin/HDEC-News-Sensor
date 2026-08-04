#!/usr/bin/env python3
"""D7-AK-6E R4-R6 §9 / R4-R7 §14 — production-observed false-positive regression.

Every article a human reviewer flagged in the actual Daily/Teams outputs is
pinned in ``data/observed_false_positive_fixtures.json`` and must stay
rejected by the canonical AI-centrality gate on every surface (Teams push,
Daily selection, dashboard AI subcategory), while the confirmed-valid
articles stay eligible with their exact evidence-based category. The
market-surface versus structural-AI-causal-event distinction (IBM budget
displacement / Google talent exodus stay eligible; Onsemi after-hours rebound
stays rejected) is verified alongside.

Fully offline: no network, no SMTP, no state writes.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from app import ai_centrality, editorial_briefings as brief  # noqa: E402
from app import editorial_review  # noqa: E402
from app.teams_ai_push import (  # noqa: E402
    evaluate_teams_push_policy,
    select_teams_push_candidates,
)
from build_news_censor import _semantic_filter_contract  # noqa: E402

FIXTURE_PATH = ROOT / "data" / "observed_false_positive_fixtures.json"
_LEVEL_ORDER = {
    ai_centrality.LEVEL_NON_AI: 0,
    ai_centrality.LEVEL_INCIDENTAL_AI_MENTION: 1,
    ai_centrality.LEVEL_ENABLING_INFRASTRUCTURE_CORE: 2,
    ai_centrality.LEVEL_EXPLICIT_AI_CORE: 3,
}

PASSES = 0
FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSES
    if condition:
        PASSES += 1
        print(f"PASS {name}")
    else:
        FAILURES.append(name)
        print(f"FAIL {name}" + (f" — {detail}" if detail else ""))


def teams_row(article: dict, **extra: object) -> dict:
    row = dict(article)
    row["publisher_direct"] = True
    row["current_run_seen"] = True
    row["summary"] = article.get("snippet", "")
    row.update(extra)
    return row


def daily_raw(article: dict) -> dict:
    return {
        "title": article["title"],
        "source": article["source"],
        "url": article["url"],
        "snippet": article["snippet"],
        "published_at": article["published_at"],
        "metadata": {"query": article.get("query", "")},
        "publisher_direct": True,
    }


def main() -> int:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    check(
        "fixture contract identity",
        fixture.get("fixture_contract") == "OBSERVED_FALSE_POSITIVE_REGRESSION_V1"
        and isinstance(fixture.get("articles"), list)
        and len(fixture["articles"]) >= 13,
    )
    articles = fixture["articles"]
    run_at = datetime.fromisoformat(fixture["reference_run_at_kst"])
    coverage = brief.daily_coverage(run_at)

    rejected = [a for a in articles if not a["expected"].get("teams_eligible")]
    accepted = [a for a in articles if a["expected"].get("teams_eligible")]
    check("fixture covers 9 rejected and 4 eligible articles",
          len(rejected) == 9 and len(accepted) == 4,
          f"rejected={len(rejected)} accepted={len(accepted)}")

    # ------------------------------------------------------------------
    # 1. Per-article canonical decision, Teams policy, and category.
    # ------------------------------------------------------------------
    for article in articles:
        expected = article["expected"]
        decision = ai_centrality.classify(article)
        category, cat_terms, cat_zone = ai_centrality.delivery_category(
            article, decision
        )
        evaluation = evaluate_teams_push_policy(teams_row(article))
        name = article["id"]

        if "ai_centrality_max" in expected:
            check(
                f"{name}: level <= {expected['ai_centrality_max']}",
                _LEVEL_ORDER[decision.level]
                <= _LEVEL_ORDER[expected["ai_centrality_max"]],
                decision.level,
            )
        if "ai_centrality_min" in expected:
            check(
                f"{name}: level >= {expected['ai_centrality_min']}",
                _LEVEL_ORDER[decision.level]
                >= _LEVEL_ORDER[expected["ai_centrality_min"]],
                decision.level,
            )

        if expected.get("teams_eligible"):
            check(f"{name}: Teams policy eligible", evaluation.eligible,
                  evaluation.rejection_reason)
            check(
                f"{name}: exact evidence-based category",
                category == expected["delivery_category"]
                and evaluation.delivery_category == expected["delivery_category"],
                f"category={category!r} policy={evaluation.delivery_category!r}",
            )
            check(
                f"{name}: category evidence present in title/lead map",
                bool(cat_terms) and cat_zone in {"title", "lead"},
                f"terms={cat_terms} zone={cat_zone}",
            )
            check(
                f"{name}: importance basis is explicit",
                evaluation.importance.sendable
                and evaluation.importance.level in {"top", "important"}
                and bool(evaluation.importance.reason),
                repr(evaluation.importance),
            )
            if expected.get("delivery_category_must_not_be"):
                check(
                    f"{name}: never mislabeled {expected['delivery_category_must_not_be']}",
                    category != expected["delivery_category_must_not_be"],
                    category,
                )
            if expected.get("opinion_labeled"):
                check(f"{name}: editorial/opinion label detected",
                      decision.opinion_labeled)
        else:
            check(f"{name}: Teams policy rejects", not evaluation.eligible,
                  "unexpectedly eligible")
            check(
                f"{name}: rejection reason is granular",
                evaluation.rejection_reason.startswith(("excluded_", "ai_not_central_")),
                evaluation.rejection_reason,
            )
            check(f"{name}: no delivery category emitted", category == "",
                  category)
            reason_class = expected.get("reason_class")
            if reason_class == "stock_market":
                check(
                    f"{name}: stock/market exclusion class",
                    decision.exclusion == ai_centrality.EXCLUSION_STOCK_MARKET,
                    decision.exclusion,
                )
            elif reason_class == "unrelated_domain":
                check(
                    f"{name}: unrelated-domain exclusion class",
                    decision.exclusion
                    in {
                        ai_centrality.EXCLUSION_POLITICAL,
                        ai_centrality.EXCLUSION_REAL_ESTATE,
                        ai_centrality.EXCLUSION_CIVIC_PUBLICITY,
                    },
                    decision.exclusion,
                )
            elif reason_class == "incidental_ai":
                check(
                    f"{name}: incidental-AI level without exclusion",
                    decision.level == ai_centrality.LEVEL_INCIDENTAL_AI_MENTION,
                    decision.level,
                )
            # A generated executive implication must never rescue it.
            rescued = evaluate_teams_push_policy(
                teams_row(
                    article,
                    whyImportant="AI 데이터센터 확산 관점에서 임원 보고 필요",
                    why_it_matters="AI 인프라 전략 시사점",
                    radarReason="AI 데이터센터 레이더",
                    category_label="AI 데이터센터",
                    hdec_relevance="현대건설 AI 데이터센터 사업 기회",
                )
            )
            check(
                f"{name}: generated implication cannot rescue",
                not rescued.eligible,
                rescued.rejection_reason,
            )

    # ------------------------------------------------------------------
    # 2. Batch selection: only the eligible four survive the selector.
    # ------------------------------------------------------------------
    selected = select_teams_push_candidates(
        [teams_row(a) for a in articles], max_articles=None
    )
    selected_titles = {c.article["title"] for c in selected}
    check(
        "Teams selector keeps exactly the four eligible events",
        len(selected) == 4
        and selected_titles == {a["title"] for a in accepted},
        repr(sorted(selected_titles)),
    )
    check(
        "every selected candidate carries an evidenced category",
        all(c.delivery_category in ai_centrality.DELIVERY_CATEGORIES for c in selected),
        repr([c.delivery_category for c in selected]),
    )

    # ------------------------------------------------------------------
    # 3. Daily selection: rejects never enter; counters are exact.
    # ------------------------------------------------------------------
    audit = brief.SelectionAuditCounters()
    daily_articles = brief.normalize_articles(
        [daily_raw(a) for a in articles],
        coverage,
        limit=brief.DAILY_MAX_ARTICLES,
        resolve_images=False,
        selection_audit=audit,
        selection_mode=brief.SELECTION_MODE_DIRECT_AWARE_DAILY,
    )
    daily_titles = [a.title for a in daily_articles]
    check(
        "Daily selects only AI-central articles",
        set(daily_titles) == {a["title"] for a in accepted},
        repr(daily_titles),
    )
    check(
        "Daily manifest counters expose every rejection class",
        audit.ai_central_qualified_count == 4
        and audit.stock_market_rejected_count == 5
        and audit.unrelated_domain_rejected_count == 3
        and audit.incidental_ai_rejected_count == 1
        and audit.selected_ai_core_count >= 3,
        repr(audit.manifest_fields()),
    )
    check(
        "Daily headline is AI-central",
        daily_articles[0].ai_centrality_level
        in brief.DAILY_HEADLINE_ALLOWED_CENTRALITY,
        daily_articles[0].ai_centrality_level,
    )
    rendered = brief.render_daily(
        daily_articles, run_at=run_at, root_url="https://fixture.invalid/root"
    )
    check(
        "rendered Daily contains no rejected title",
        all(a["title"] not in rendered.html for a in rejected),
    )

    # Non-AI headline is a hard render failure.
    try:
        brief.render_daily(
            [
                daily_articles[0].__class__(
                    **{
                        **daily_articles[0].__dict__,
                        "title": "일반 부동산 매물 소식",
                        "ai_centrality_level": "non_ai",
                    }
                )
            ],
            run_at=run_at,
            root_url="https://fixture.invalid/root",
        )
        check("non-AI headline fails closed", False, "render_daily accepted it")
    except brief.EditorialError:
        check("non-AI headline fails closed", True)

    # Editor override cannot silently rescue; written reason is the only path.
    base_candidate = {
        "candidate_id": "cand-1",
        "title": "부동산 매물 정리 기사",
        "summary": "일반 부동산 매물 동향.",
        "source": "연합뉴스",
        "published_at": run_at.isoformat(),
        "selected_url": "https://www.yna.co.kr/view/override-check",
    }
    try:
        editorial_review.candidate_to_article(base_candidate)
        check("non-AI review candidate fails closed", False)
    except editorial_review.EditorialReviewError:
        check("non-AI review candidate fails closed", True)
    overridden = editorial_review.candidate_to_article(
        base_candidate,
        override={
            "operator_override_reason": "임원 지시로 예외 유지 (사유 기록)",
        },
    )
    check(
        "explicit written operator override is preserved and auditable",
        overridden.ai_centrality_level
        == editorial_review.AI_CENTRALITY_OPERATOR_OVERRIDE,
    )

    # ------------------------------------------------------------------
    # 4. Dashboard AI subcategory reuses the canonical decision.
    # ------------------------------------------------------------------
    for article in rejected:
        contract = _semantic_filter_contract(
            {
                "title": article["title"],
                "snippet": article["snippet"],
                "source": article["source"],
            }
        )
        check(
            f"dashboard AI filter excludes {article['id']}",
            "ai" not in contract["categories"],
            repr(sorted(contract["categories"])),
        )
    explicit_accepts = [
        a
        for a in accepted
        if a["expected"].get("ai_centrality_min") == "explicit_ai_core"
    ]
    dashboard_hits = sum(
        1
        for a in explicit_accepts
        if "ai"
        in _semantic_filter_contract(
            {"title": a["title"], "snippet": a["snippet"], "source": a["source"]}
        )["categories"]
    )
    check(
        "dashboard AI filter keeps explicit AI-core articles",
        dashboard_hits == len(explicit_accepts),
        f"{dashboard_hits}/{len(explicit_accepts)}",
    )

    # ------------------------------------------------------------------
    # 5. R4-R7 §2/§14 — market-surface vs structural-AI-causal distinction.
    # ------------------------------------------------------------------
    ibm = {
        "title": "IBM 시가총액 하락…AI 인프라 지출 확대가 소프트웨어·컨설팅 예산 잠식",
        "snippet": (
            "IBM 시가총액이 하락했다. AI 인프라 지출 확대가 기존 소프트웨어·"
            "컨설팅 예산을 잠식하며 사업 구조를 바꾸고 있다는 분석이다."
        ),
    }
    google = {
        "title": "알파벳 주가 하락…핵심 AI 인재 연쇄 이탈에 경쟁력 우려",
        "snippet": "알파벳 주가가 하락했다. 핵심 AI 인재 연쇄 이탈로 AI 경쟁력 약화 우려가 커진다.",
    }
    for label, case, event in (
        ("IBM structural AI budget displacement", ibm, "ai_budget_reallocation"),
        ("Google structural AI talent exodus", google, "ai_talent_change"),
    ):
        decision = ai_centrality.classify(case)
        check(
            f"{label} is not rejected solely for stock language",
            decision.is_central
            and decision.surface_market
            and decision.structural_event == event,
            f"level={decision.level} exclusion={decision.exclusion} "
            f"event={decision.structural_event}",
        )
    onsemi = next(
        a for a in rejected if "온세미" in a["title"] and a["source"] == "매일경제"
    )
    onsemi_decision = ai_centrality.classify(onsemi)
    check(
        "Onsemi rebound rejected: market movement without structural AI event",
        onsemi_decision.exclusion == ai_centrality.EXCLUSION_STOCK_MARKET
        and onsemi_decision.surface_market
        and not onsemi_decision.structural_event,
        repr(onsemi_decision),
    )
    property_conversion = ai_centrality.classify(
        {
            "title": "낡은 공장 부지 매각…AI 데이터센터 전환 확정",
            "snippet": "노후 공장 부지가 AI 데이터센터로 전환된다.",
        }
    )
    check(
        "property sale stays eligible only with explicit data-center conversion",
        property_conversion.is_central,
        repr(property_conversion),
    )

    print()
    print(
        f"OBSERVED_FALSE_POSITIVE_REGRESSION="
        f"{'PASS' if not FAILURES else 'FAIL'} checks={PASSES} failures={len(FAILURES)}"
    )
    print(
        "COUNTERS network=0 smtp=0 teams=0 telegram=0 production_state_writes=0"
    )
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    raise SystemExit(main())
