#!/usr/bin/env python3
"""D7-AK-6E R4-R9B — dashboard freshness contract verifier (no network, no send).

The 2026-08-03→08-05 production incident: the committed public dashboard
(docs/daily/dashboard-latest.html, SHA256 87e38131…) stayed byte-identical for
two days because the Scheduled Live Refresh "Verify pipeline" step failed
before the build step — first on a category-token allowance that never
matched the builder contract, then on a warm-cache fixture frozen to
2026-08-02 wall-clock.  Pages kept deploying (Teams-state and Daily-Brief
commits push to main), so Pages success masqueraded as freshness.

This verifier makes each layer of that failure a first-class regression:

  1. committed-artifact coherence — the public dashboard's embedded model,
     visible cards, publisher-direct links, and generated timestamp agree;
  2. builder freshness property — the pure model builder represents the
     current verified article pool: a changed pool must change the model,
     an unchanged pool is the only valid explanation for an unchanged model,
     unchanged market/weather data never freezes articles, and missing
     images never block the build;
  3. verify-gate deadlock guard — the two previously-deadlocking verifiers
     must pass, and their repaired contracts (real-clock warm fixture,
     builder token allowance) must still be present, so the workflow can
     always reach the build step that replaces a stale committed artifact;
  4. Pages interpretation (§10) — a Pages deployment proves only that the
     current docs tree was served: the Teams watch commits state only and
     the Daily Brief workflow never touches dashboard-latest.html, so both
     redeploy Pages while leaving the dashboard byte-identical.  Freshness
     authority is the generated dashboard content, never the Pages
     workflow conclusion.
"""

from __future__ import annotations

import copy
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import build_news_censor  # noqa: E402
from build_executive_brief import (  # noqa: E402
    attach_artifact_contract,
    build_brief_via_mock_pipeline,
)
from app import publisher_direct  # noqa: E402

DASHBOARD = ROOT / "docs" / "daily" / "dashboard-latest.html"
REFRESH_WORKFLOW = ROOT / ".github" / "workflows" / "scheduled-live-refresh.yml"
TEAMS_WORKFLOW = ROOT / ".github" / "workflows" / "teams-ai-news-watch.yml"
DAILY_WORKFLOW = ROOT / ".github" / "workflows" / "editorial-daily-brief.yml"

CHECKS = 0
FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    global CHECKS
    CHECKS += 1
    if ok:
        print(f"PASS: {name}")
    else:
        FAILURES.append(name)
        print(f"FAIL: {name}" + (f" — {str(detail)[:400]}" if detail else ""))
    return bool(ok)


def extract_article_map(html: str) -> dict:
    matched = re.search(
        r'<script type="application/json" id="article-data">\s*(.*?)\s*</script>',
        html,
        re.S,
    )
    if not matched:
        return {}
    try:
        data = json.loads(matched.group(1))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def verify_committed_artifact() -> None:
    print("\n== 1. Committed public dashboard coherence ==")
    if not check("dashboard-latest.html exists", DASHBOARD.exists()):
        return
    html = DASHBOARD.read_text(encoding="utf-8", errors="ignore")
    article_map = extract_article_map(html)
    check("embedded article model present", bool(article_map),
          f"{len(article_map)} rows")
    visible = re.findall(r"<article\b[^>]*\bdata-article=\"([^\"]+)\"", html)
    check(
        "visible article count equals the generated model",
        len(visible) == len(article_map)
        and set(visible) == set(article_map),
        f"visible={len(visible)} model={len(article_map)}",
    )
    urls = [
        str((row or {}).get("url") or "")
        for row in article_map.values()
        if isinstance(row, dict)
    ]
    portal = [url for url in urls if publisher_direct.portal_provider(url)]
    check("no remote portal URL in the published model", not portal,
          repr(portal[:3]))
    invalid = [
        url for url in urls
        if not publisher_direct.normalize_publisher_canonical_url(url)
    ]
    check("publisher-direct links remain valid canonical URLs", not invalid,
          repr(invalid[:3]))
    stamp = re.search(r"(\d{4}-\d{2}-\d{2}) \d{2}:\d{2}", html)
    check("generated timestamp is present and parseable", bool(stamp),
          html[:120])


def _live_brief(rows: list[dict], *, generated_kst: str) -> dict:
    brief = attach_artifact_contract(
        build_brief_via_mock_pipeline(), weather_mode="mock"
    )
    brief = copy.deepcopy(brief)
    brief.update(
        {
            "news_data_mode": "live",
            "news_source": "fixture_live_publishers",
            "news_fallback_used": False,
            "collection_status": build_news_censor.LIVE_HEALTHY_WITH_ARTICLES,
            "collection_failure_category": "",
            "generated_kst": generated_kst,
        }
    )
    brief[build_news_censor.DISPLAY_FIELD] = rows
    contract = dict(brief.get("news_censor_display_contract") or {})
    contract["candidate_count"] = len(rows)
    brief["news_censor_display_contract"] = contract
    brief["collector_health"] = {
        "status": build_news_censor.LIVE_HEALTHY_WITH_ARTICLES,
        "request_count": 6,
        "source_count": 3,
        "successful_source_count": 3,
        "raw_candidate_count": len(rows),
        "publisher_direct_eligible_count": len(rows),
        "quarantine_count": 0,
        "final_portal_url_count": 0,
        "failure_category": "",
        "quarantine_reason_counts": {},
        "publisher_resolution": {
            "attempted_count": len(rows),
            "resolved_count": len(rows),
            "failed_count": 0,
            "budget_exhausted_count": 0,
            "policy": "bounded_fair_per_publisher",
        },
    }
    return brief


def _model_titles(model: dict) -> list[str]:
    return [
        str((row or {}).get("title") or "")
        for row in (model.get("articles") or [])
    ]


def verify_builder_freshness_property() -> None:
    print("\n== 2. Builder freshness property (pure model builder) ==")
    edition = date(2026, 8, 5)
    demo = attach_artifact_contract(
        build_brief_via_mock_pipeline(), weather_mode="mock"
    )
    qualified = [
        copy.deepcopy(row)
        for row in demo.get(build_news_censor.DISPLAY_FIELD) or []
        if row.get("display_relevance_qualified") is True
    ]
    if not check(
        "mock pipeline supplies enough qualified display rows",
        len(qualified) >= 11,
        f"{len(qualified)} rows",
    ):
        return
    pool_a = qualified[:6]
    pool_b = qualified[6:11]

    brief_a = _live_brief(pool_a, generated_kst="2026-08-05 09:00")
    model_a = build_news_censor.build_model(brief_a, edition=edition)
    titles_a = _model_titles(model_a)
    pool_a_titles = {r["title"] for r in pool_a}
    check(
        "successful collector result is represented in the generated model",
        bool(titles_a)
        and set(titles_a) <= pool_a_titles
        and len(titles_a) >= len(pool_a) - 2,
        f"model={len(titles_a)} pool={len(pool_a)} foreign="
        f"{sorted(set(titles_a) - pool_a_titles)[:2]}",
    )

    brief_b = _live_brief(pool_b, generated_kst="2026-08-05 10:00")
    for key in ("market_snapshot", "market", "weather_risk", "weather"):
        if key in brief_a:
            brief_b[key] = copy.deepcopy(brief_a[key])
    model_b = build_news_censor.build_model(brief_b, edition=edition)
    titles_b = _model_titles(model_b)
    check(
        "build output changes when the verified article pool changes",
        bool(titles_b)
        and set(titles_b) <= {r["title"] for r in pool_b}
        and not (set(titles_b) & set(titles_a)),
        repr(titles_b[:3]),
    )
    check(
        "unchanged market/weather data does not prevent article refresh",
        sorted(titles_b) != sorted(titles_a),
    )

    brief_a_later = _live_brief(pool_a, generated_kst="2026-08-05 11:00")
    model_a_later = build_news_censor.build_model(brief_a_later, edition=edition)
    check(
        "an unchanged model is explained only by an unchanged pool",
        sorted(_model_titles(model_a_later)) == sorted(titles_a),
        "same pool must reproduce the same articles",
    )

    imageless = copy.deepcopy(pool_a)
    for row in imageless:
        row.pop("image_url", None)
        row.pop("image", None)
    model_imageless = build_news_censor.build_model(
        _live_brief(imageless, generated_kst="2026-08-05 12:00"),
        edition=edition,
    )
    check(
        "image failure does not prevent the full dashboard model",
        len(model_imageless.get("articles") or []) == len(titles_a),
        f"{len(model_imageless.get('articles') or [])}/{len(titles_a)}",
    )


def verify_gate_deadlock_guard() -> None:
    print("\n== 3. Verify-gate deadlock guard ==")
    state_text = (ROOT / "scripts" / "verify_news_censor_verified_state.py").read_text(
        encoding="utf-8"
    )
    integration = state_text.split("def verify_cache_integration", 1)[-1]
    check(
        "warm-cache integration fixture is anchored to the real clock",
        "datetime.now(timezone.utc)" in integration.split("def ", 1)[0],
        "frozen fixture clock reintroduces the 24h scheduled time bomb",
    )
    exposure_text = (
        ROOT / "scripts" / "verify_ai_market_exposure_quality.py"
    ).read_text(encoding="utf-8")
    check(
        "exact-reference token allowance matches the builder contract",
        '"sub:"' in exposure_text and 'startswith("lens:")' in exposure_text,
        "sub_/no-lens allowance deadlocks the refresh against its own artifact",
    )
    for script in (
        "verify_news_censor_verified_state.py",
        "verify_ai_market_exposure_quality.py",
    ):
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / script)],
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
        check(
            f"{script} passes — workflow reaches the dashboard build step",
            proc.returncode == 0,
            (proc.stdout + proc.stderr)[-400:],
        )


def verify_pages_interpretation() -> None:
    print("\n== 4. §10 Pages deployment is not freshness proof ==")
    refresh = REFRESH_WORKFLOW.read_text(encoding="utf-8")
    check(
        "scheduled refresh publishes the dashboard only behind live_ok",
        "docs/daily/dashboard-latest.html" in refresh
        and "steps.build.outputs.live_ok == 'true'" in refresh,
    )
    check(
        "verify step precedes the build step that replaces the artifact",
        refresh.find("Verify pipeline") < refresh.find("Build live report"),
    )
    teams = TEAMS_WORKFLOW.read_text(encoding="utf-8")
    check(
        "Teams watch commits state only — it can redeploy Pages while the"
        " dashboard stays byte-identical",
        "git add -- data/teams_push_state.json" in teams
        and "dashboard-latest.html" not in teams,
    )
    daily = DAILY_WORKFLOW.read_text(encoding="utf-8")
    check(
        "Daily Brief publication never touches dashboard-latest.html",
        "dashboard-latest.html" not in daily,
    )


def main() -> int:
    verify_committed_artifact()
    verify_builder_freshness_property()
    verify_gate_deadlock_guard()
    verify_pages_interpretation()
    print()
    print(f"checks={CHECKS} failures={len(FAILURES)}")
    result = "PASS" if not FAILURES else "FAIL"
    print(f"RESULT=D7-AK-6E_R4R9B_DASHBOARD_FRESHNESS_{result}")
    print(
        "network_calls=0 smtp_attempts=0 teams_sends=0 telegram_sends=0"
        " production_state_writes=0 docs_writes=0"
    )
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    sys.exit(main())
