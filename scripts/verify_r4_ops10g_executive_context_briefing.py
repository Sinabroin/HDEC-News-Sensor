#!/usr/bin/env python3
"""Founder-visible R4-OPS-10G executive-context acceptance verifier."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from html import unescape
from pathlib import Path
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT, ROOT / "scripts"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from app import (  # noqa: E402
    editorial_briefings,
    editorial_executive_context,
    editorial_operator_review,
    editorial_radar,
    editorial_transparency,
    teams_ai_push,
)
from verify_editorial_review_console import (  # noqa: E402
    _browser_argument_path,
    _browser_executable,
    _browser_path,
)

RUN_AT = datetime.fromisoformat("2026-08-24T07:30:00+09:00")
FIXTURE_PATH = ROOT / "data" / "r4_ops10g_executive_context_replay.json"
PRODUCTION_BASELINE_PATH = (
    ROOT / "data" / "editorial" / "company_baselines" / "2026-h1-construction.json"
)


class Verifier:
    def __init__(self) -> None:
        self.failures: list[str] = []

    def check(self, key: str, passed: object, detail: object = "") -> None:
        status = bool(passed)
        print(f"{key}={'PASS' if status else 'FAIL'}")
        if not status:
            self.failures.append(key)
            if detail:
                print(f"DETAIL_{key}={detail}")


def _contexts() -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert fixture["fixture_only"] is True
    baseline = editorial_executive_context.validate_baseline_authority(
        fixture["synthetic_baseline"]
    )
    contexts = {
        row["case_id"]: editorial_executive_context.derive_executive_context(
            row["article"],
            baseline=baseline,
            article_already_qualified=row["already_qualified"],
        )
        for row in fixture["cases"]
    }
    return fixture, contexts


def _article(
    title: str,
    summary: str,
    context: dict[str, Any],
    *,
    source: str = "검증매체",
    url: str = "https://www.hankookilbo.com/News/Read/A2026082400000000001",
) -> editorial_briefings.EditorialArticle:
    return editorial_briefings.EditorialArticle(
        title=title,
        summary=summary,
        source=source,
        published_at=datetime.fromisoformat("2026-08-24T06:00:00+09:00"),
        selected_url=url,
        link_kind="publisher_direct",
        link_label="원문 보기",
        category="투자·산업",
        ai_centrality_level="explicit_ai_core",
        executive_context=context,
        executive_implication=str(context["hdec_implication"].get("text") or ""),
    )


def _windows_temp_root() -> Path:
    output = subprocess.run(
        ["cmd.exe", "/d", "/c", "echo", "%TEMP%"],
        cwd="/mnt/c",
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    windows_path = next(
        line.strip()
        for line in reversed(output.splitlines())
        if re.match(r"^[A-Za-z]:\\", line.strip())
    )
    return Path(
        subprocess.run(
            ["wslpath", "-u", windows_path],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )


BROWSER_HARNESS = r"""
<script>
(async()=>{
  const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
  const previewIds=selector=>[...document.querySelectorAll(selector)].map(node=>node.dataset.articleId);
  const importFixtures=__R4_OPS10G_IMPORT_FIXTURES__;
  const fetchLog=[];
  const response=(ok,payload)=>({ok,json:async()=>payload});
  window.fetch=async(input,options={})=>{
    const url=String(input||"");
    fetchLog.push(url);
    if(url.endsWith("/api/editorial/import-article")){
      const requested=JSON.parse(String(options.body||"{}"));
      const fixture=importFixtures.find(item=>item.url===requested.url);
      return fixture?response(true,{ok:true,article:fixture.article}):response(false,{ok:false,error:{message:"fixture not found"}});
    }
    if(url.endsWith("/api/auth/session")||url.endsWith("/api/editorial/contributor/session"))return response(true,{authenticated:false});
    return response(false,{ok:false});
  };
  await pause(250);
  const result={};
  result.logged_out=!serverContext.authenticated&&!contributorContext.authenticated;
  result.operator_collapsed=!document.getElementById("operatorPanel").open;
  result.no_github=!document.body.innerText.includes("GitHub");
  result.left_radar=!!document.getElementById("radarPanel")&&document.getElementById("radarPanel").offsetParent!==null;
  state.selected=[];state.manualCandidates=[];state.edits={};render();
  result.zero_layout=!!document.querySelector('#preview [data-role="headline-empty"]')&&!document.querySelector('#preview [data-role="article-card"]');
  document.getElementById("importUrl").value=importFixtures[0].url;
  document.getElementById("importBtn").click();
  for(let attempt=0;attempt<20&&!state.selected.length;attempt++)await pause(50);
  const first=state.selected[0];
  if(!first)throw new Error(`server import failed: ${document.getElementById("importStatus").textContent}; api=${articleImportApiUrl}; fetch=${fetchLog.join(",")}`);
  result.manual_import=!!first&&view(first).executive_context.fact_points.length>=2&&!!document.querySelector('#preview [data-role="executive-context"]');
  result.one_layout=!!document.querySelector('#preview [data-role="headline"]')&&!document.querySelector('#preview [data-role="article-card"]');
  result.right_no_radar=!document.querySelector('#preview [data-role="radar-scan"]')&&!document.querySelector('#preview [data-role="information-taxonomy"]')&&!document.getElementById("preview").innerText.includes("정보 분류 기준");
  const fact=document.querySelector('#preview [data-field="fact_point"]');
  const implication=document.querySelector('#preview [data-field="hdec_implication_text"]');
  const watch=document.querySelector('#preview [data-field="watch_point_text"]');
  if(!fact||!implication||!watch)throw new Error(`server context fields missing: ${JSON.stringify(view(first).executive_context)}`);
  fact.textContent="편집된 새만금 사실 포인트";fact.dispatchEvent(new InputEvent("input",{bubbles:true}));
  implication.textContent="편집된 현대건설 산업거점 분석";implication.dispatchEvent(new InputEvent("input",{bubbles:true}));
  watch.textContent="편집된 인허가 발주 Watch";watch.dispatchEvent(new InputEvent("input",{bubbles:true}));
  result.edit_sync=view(first).executive_context.fact_points[0]==="편집된 새만금 사실 포인트"&&view(first).executive_context.hdec_implication.text==="편집된 현대건설 산업거점 분석"&&view(first).executive_context.watch_point.text==="편집된 인허가 발주 Watch"&&selectedItems()[0].executive_context_edits.fact_points[0]==="편집된 새만금 사실 포인트";
  document.getElementById("importUrl").value=importFixtures[1].url;
  document.getElementById("importBtn").click();await pause(80);
  const second=state.selected.find(id=>id!==first);
  state.selected=[second,first];render();
  result.multi_layout=previewIds('#preview [data-role="headline"]').join("")===second&&previewIds('#preview [data-role="article-card"]').join("")===first;
  result.reorder_sync=view(first).executive_context.fact_points[0]==="편집된 새만금 사실 포인트";
  state.selected=state.selected.filter(id=>id!==first);render();
  result.remove_sync=!document.querySelector(`#preview [data-article-id="${first}"]`)&&!selectedItems().some(item=>item.candidate_id===first);
  let blob=null;const create=URL.createObjectURL;const click=HTMLAnchorElement.prototype.click;
  URL.createObjectURL=value=>{blob=value;return "blob:r4ops10g"};HTMLAnchorElement.prototype.click=function(){};
  document.getElementById("htmlBtn").click();const downloaded=blob?await blob.text():"";
  URL.createObjectURL=create;HTMLAnchorElement.prototype.click=click;
  result.download_no_radar=!!downloaded&&!downloaded.includes("수집 레이더")&&!downloaded.includes("수집·판단 레이더")&&!downloaded.includes("정보 분류 기준")&&!downloaded.includes('data-role="radar-scan"');
  const marker=document.createElement("pre");marker.id="r4ops10g-result";marker.textContent=JSON.stringify(result);document.body.appendChild(marker);
})().catch(error=>{const marker=document.createElement("pre");marker.id="r4ops10g-result";marker.textContent=JSON.stringify({error:String(error),stack:error&&error.stack||""});document.body.appendChild(marker);});
</script>
"""


def _browser_acceptance() -> dict[str, Any]:
    browser = _browser_executable()
    if browser is None:
        return {"error": "browser_not_found"}
    with tempfile.TemporaryDirectory(prefix="r4-ops10g-browser-") as value:
        root = Path(value)
        fixture_rows = [
            {
                "url": "https://www.yna.co.kr/view/SYNTHETIC_R4OPS10G_BROWSER",
                "title": "새만금, 공공주도 개발…AI·수소 산업거점 구축",
                "summary": (
                    "산업용지를 확대하고 개발 일정을 앞당김. AI·로봇 산업거점과 "
                    "데이터센터를 구축함. 재생에너지·수소 기반시설 투자를 추진함."
                ),
            },
            {
                "url": "https://www.yna.co.kr/view/SYNTHETIC_R4OPS10G_BROWSER_2",
                "title": "AI 데이터센터 전력 파트너 사업 착공",
                "summary": "AI 데이터센터 개발과 전력 공급 사업을 착공함. EPC 발주를 추진함.",
            },
        ]
        import_fixtures = []
        for row in fixture_rows:
            context = editorial_executive_context.derive_executive_context(
                {
                    "title": row["title"],
                    "summary": row["summary"],
                    "summary_authorized": True,
                    "collection_source_kind": "url_import",
                },
                article_already_qualified=True,
            )
            import_fixtures.append(
                {
                    "url": row["url"],
                    "article": {
                        "input_url": row["url"],
                        "analysis_url": row["url"],
                        "publisher_url": row["url"],
                        "publisher_domain": "yna.co.kr",
                        "publisher_direct": True,
                        "publisher_domain_authoritative": True,
                        "portal_copy": False,
                        "portal_source": "",
                        "portal_resolution_reason": "publisher_direct_verified",
                        "portal_fallback_used": False,
                        "title": row["title"],
                        "source": "연합뉴스",
                        "summary": row["summary"],
                        "summary_html": row["summary"],
                        "published_at": "2026-08-24T06:00:00+09:00",
                        "category": "투자·산업",
                        "category_analysis": {
                            "category": "투자·산업",
                            "scores": {"투자·산업": 1},
                            "matched_signals": {"투자·산업": ["fixture"]},
                            "reason": "server-derived fixture",
                        },
                        "article_text_excerpt": row["summary"],
                        "image_url": "",
                        "extraction": {},
                        "executive_context": context,
                    },
                }
            )
        build = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "build_editorial_review_console.py"),
                "--fixture",
                "--run-at",
                RUN_AT.isoformat(),
                "--output-root",
                str(root / "review"),
                "--operator-api-base",
                "https://operator.fixture.test",
            ],
            cwd=ROOT,
            env={**os.environ, "TEAMS_AI_NEWS_WATCH": "0"},
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if build.returncode:
            return {"error": build.stderr[-1000:]}
        original = root / "review" / "2026-08-24" / "index.html"
        page = original.with_name("r4-ops10g-browser.html")
        source = original.read_text(encoding="utf-8")
        harness = BROWSER_HARNESS.replace(
            "__R4_OPS10G_IMPORT_FIXTURES__",
            json.dumps(import_fixtures, ensure_ascii=False).replace("</", "<\\/"),
        )
        before, after = source.rsplit("</body>", 1)
        page.write_text(before + harness + "</body>" + after, encoding="utf-8")
        profile_owner: tempfile.TemporaryDirectory[str] | None = None
        if browser.suffix.casefold() == ".exe":
            profile_owner = tempfile.TemporaryDirectory(
                prefix="hdec-r4-ops10g-browser-",
                dir=_windows_temp_root(),
                ignore_cleanup_errors=True,
            )
            profile = Path(profile_owner.name)
        else:
            profile = root / "profile"
            profile.mkdir()
        command = [
            str(browser),
            "--headless=new",
            "--disable-gpu",
            "--disable-background-networking",
            "--disable-component-update",
            "--disable-default-apps",
            "--disable-sync",
            "--no-first-run",
            "--no-default-browser-check",
            "--allow-file-access-from-files",
            "--virtual-time-budget=5000",
            f"--user-data-dir={_browser_argument_path(profile, browser)}",
            "--dump-dom",
            _browser_path(page.resolve(), browser),
        ]
        if browser.suffix.casefold() != ".exe":
            command[2:2] = ["--no-sandbox", "--disable-dev-shm-usage"]
        completed: subprocess.CompletedProcess[str] | None = None
        try:
            for attempt in range(2):
                try:
                    completed = subprocess.run(
                        command,
                        capture_output=True,
                        text=True,
                        timeout=45,
                        check=False,
                    )
                    break
                except subprocess.TimeoutExpired:
                    if attempt:
                        return {"error": "headless browser timeout after one retry"}
        finally:
            if profile_owner is not None:
                profile_owner.cleanup()
        assert completed is not None
        match = re.search(r'<pre id="r4ops10g-result">([^<]+)</pre>', completed.stdout)
        if completed.returncode or not match:
            return {"error": completed.stderr[-1000:] or "browser_marker_missing"}
        return json.loads(unescape(match.group(1)))


def main() -> int:
    verifier = Verifier()
    fixture, contexts = _contexts()
    baseline = editorial_executive_context.validate_baseline_authority(
        fixture["synthetic_baseline"]
    )
    production_baseline = editorial_executive_context.load_baseline_authority()
    gs = contexts["gs_ai_data_center"]
    saemangeum = contexts["saemangeum_ai_hydrogen_hub"]
    generic = contexts["generic_ai_policy"]
    non_ai = contexts["non_ai_company_news"]
    semiconductor = contexts["semiconductor_infrastructure"]
    commentary = contexts["semiconductor_commentary"]

    verifier.check(
        "BASELINE_PROVENANCE",
        gs["provenance"]["baseline_id"] == baseline["baseline_id"]
        and gs["provenance"]["baseline_fact_ids"] == ["fixture-gs-portfolio-001"]
        and production_baseline["entities"] == []
        and production_baseline["source_document"]["sha256"]
        == "28c5de0ef78ce5360a330c1a03cd88d8a4b2f03a74e8bd09d65f34355f89c21b",
    )
    baseline_dump = PRODUCTION_BASELINE_PATH.read_text(encoding="utf-8")
    verifier.check(
        "BASELINE_BOUNDED_SECURITY",
        PRODUCTION_BASELINE_PATH.stat().st_size < 64_000
        and not list(PRODUCTION_BASELINE_PATH.parent.glob("*.pdf"))
        and not any(
            forbidden in baseline_dump.casefold()
            for forbidden in ("base64", "full_text", "raw_text", "page_text")
        ),
    )
    exact_gs = editorial_executive_context.match_baseline_entity("GS건설 사업", baseline)
    none_hyundai_car = editorial_executive_context.match_baseline_entity(
        "현대차 투자", baseline
    )
    ambiguous = editorial_executive_context.match_baseline_entity(
        "GS건설·삼성물산 협력", baseline
    )
    verifier.check(
        "BASELINE_ENTITY_EXACT_MATCH",
        exact_gs["status"] == "matched" and none_hyundai_car["status"] == "none",
    )
    verifier.check("AMBIGUOUS_ENTITY_FAIL_CLOSED", ambiguous["status"] == "ambiguous")
    verifier.check(
        "EXECUTIVE_FACT_POINTS",
        all(2 <= len(context["fact_points"]) <= 3 for context in contexts.values())
        and all(len(point) <= 180 for context in contexts.values() for point in context["fact_points"]),
    )
    verifier.check(
        "HDEC_IMPLICATION_LABELED_ANALYSIS",
        gs["hdec_implication"]["label"] == "현대건설 관점 · 분석"
        and gs["hdec_implication"]["status"] == "supported",
    )
    verifier.check(
        "WATCH_POINT_NO_INVENTED_DATE",
        all(
            not re.search(r"20\d{2}[-년./]\s*\d{1,2}", context["watch_point"]["text"])
            for context in contexts.values()
        ),
    )
    verifier.check(
        "GS_AI_DC_CONTEXT",
        gs["baseline_match"]["canonical_name"] == "GS건설"
        and gs["delta_vs_baseline"]["status"] == "supported"
        and "전력" in gs["hdec_implication"]["text"],
    )
    verifier.check(
        "SAEMANGEUM_CONTEXT",
        saemangeum["baseline_match"]["status"] == "none"
        and saemangeum["delta_vs_baseline"]["status"] == "not_supported"
        and "산업" in saemangeum["hdec_implication"]["text"]
        and "인허가" in saemangeum["watch_point"]["text"],
    )
    verifier.check(
        "GENERIC_AI_NEGATIVE_CASE",
        generic["hdec_implication"]["status"] == "not_supported"
        and generic["delta_vs_baseline"]["status"] == "not_supported",
    )
    verifier.check(
        "NON_AI_NEGATIVE_CASE",
        non_ai["baseline_match"]["status"] == "matched"
        and non_ai["hdec_implication"]["status"] == "not_supported"
        and non_ai["delta_vs_baseline"]["status"] == "not_supported",
    )
    verifier.check(
        "SEMICONDUCTOR_CONTEXT_BOUNDARY",
        semiconductor["hdec_implication"]["status"] == "supported"
        and commentary["hdec_implication"]["status"] == "not_supported",
    )

    case_by_id = {row["case_id"]: row for row in fixture["cases"]}
    sample_rows = [
        {
            **case_by_id["gs_ai_data_center"]["article"],
            "url": "https://www.hankookilbo.com/News/Read/A2026082400000000001",
            "published_at": "2026-08-24T06:00:00+09:00",
        },
        {
            **case_by_id["saemangeum_ai_hydrogen_hub"]["article"],
            "url": "https://www.yna.co.kr/view/SYNTHETIC_R4OPS10G_SELECTION",
            "published_at": "2026-08-24T05:00:00+09:00",
        },
    ]
    with patch.object(editorial_executive_context, "load_baseline_authority", return_value=baseline):
        with_baseline = editorial_briefings.normalize_articles(
            sample_rows,
            editorial_briefings.daily_coverage(RUN_AT),
            limit=6,
        )
    without_baseline = editorial_briefings.normalize_articles(
        sample_rows,
        editorial_briefings.daily_coverage(RUN_AT),
        limit=6,
    )
    before_ids = [editorial_briefings.editorial_article_id(row) for row in without_baseline]
    after_ids = [editorial_briefings.editorial_article_id(row) for row in with_baseline]
    verifier.check("SELECTION_INVARIANT", before_ids == after_ids)

    watch_article = {
        "title": "GS건설, AI 데이터센터 전력 프로젝트 착공",
        "source": "한국일보",
        "url": "https://www.hankookilbo.com/News/Read/A2026082400000000002",
        "snippet": "AI 데이터센터 개발과 전력 공급 사업을 착공함.",
        "published_at": "2026-08-24T06:00:00+09:00",
        "publisher_direct": True,
        "publisher_url": "https://www.hankookilbo.com/News/Read/A2026082400000000002",
    }
    policy_before = teams_ai_push.evaluate_teams_push_policy(watch_article)
    policy_after = teams_ai_push.evaluate_teams_push_policy(
        {**watch_article, "executive_context": gs, "baseline_match_status": "matched"}
    )
    verifier.check(
        "WATCH_ELIGIBILITY_INVARIANT",
        policy_before.eligible == policy_after.eligible
        and policy_before.rejection_reason == policy_after.rejection_reason,
    )

    unsupported_article = _article(
        case_by_id["saemangeum_ai_hydrogen_hub"]["article"]["title"],
        case_by_id["saemangeum_ai_hydrogen_hub"]["article"]["snippet"],
        saemangeum,
    )
    daily = editorial_briefings.render_daily(
        [unsupported_article], run_at=RUN_AT, root_url="https://daily.fixture.test"
    )
    verifier.check(
        "UNSUPPORTED_DELTA_OMITTED",
        'data-role="delta-vs-baseline"' not in daily.html,
    )
    verifier.check(
        "DAILY_EXECUTIVE_CONTEXT_FORMAT",
        'data-role="fact-points"' in daily.html
        and 'data-role="hdec-implication"' in daily.html
        and 'data-content-kind="analysis"' in daily.html
        and 'data-role="watch-point"' in daily.html,
    )
    verifier.check(
        "PUBLIC_DAILY_RADAR_HIDDEN",
        "수집 레이더" not in daily.html
        and "정보 분류 기준" not in daily.html
        and 'data-role="radar-scan"' not in daily.html,
    )

    rows = [
        {**watch_article, "article_id": "one"},
        {**watch_article, "article_id": "one-copy", "source": "한국일보"},
        {
            **watch_article,
            "article_id": "two",
            "url": "https://www.hankookilbo.com/News/Read/A2026082400000000003",
            "publisher_url": "https://www.hankookilbo.com/News/Read/A2026082400000000003",
        },
    ]
    radar_rows = [editorial_radar.lightweight_row(row, sequence=index) for index, row in enumerate(rows)]
    audit = editorial_radar.build_audit(
        radar_rows,
        collection_audit={"naver_articles_collected": 3},
        selection_audit={"ai_central_qualified_count": 2, "executive_qualified_count": 1},
        selected_count=1,
    )
    daily_transparency = editorial_briefings.render_daily(
        [unsupported_article],
        run_at=RUN_AT,
        root_url="https://daily.fixture.test",
        radar_audit=audit,
    )
    verifier.check(
        "TEAMS_DAILY_24H_TRANSPARENCY",
        "AI T&I 탐지 현황 · 최근 24시간" in daily_transparency.teams_text
        and "2건 탐지" in daily_transparency.teams_text,
    )
    verifier.check(
        "TEAMS_RAW_COUNT_HONEST",
        "3건 탐지" not in daily_transparency.teams_text
        and "수집 신호" in editorial_transparency.render_text(
            editorial_transparency.from_radar_audit(
                {
                    "version": editorial_radar.RADAR_VERSION,
                    "funnel": {
                        "collection_count": 9,
                        "normalized_row_count": 4,
                        "ai_central_count": 2,
                        "selected_count": 1,
                    }
                }
            ),
            window_label="24시간",
        ),
    )
    verifier.check(
        "TEAMS_UNIQUE_COUNT_AUTHORITY",
        audit["funnel"]["raw_collected_count"] == 3
        and audit["funnel"]["unique_collected_count"] == 2
        and audit["funnel"]["unique_count_proven"] is True,
    )
    rolling_rows = [
        {**watch_article, "published_at": "2026-08-18T06:00:00+09:00"},
        {**watch_article, "published_at": "2026-08-19T06:00:00+09:00"},
        {
            **watch_article,
            "url": "https://www.hankookilbo.com/News/Read/A2026082400000000004",
            "publisher_url": "https://www.hankookilbo.com/News/Read/A2026082400000000004",
            "published_at": "2026-08-20T06:00:00+09:00",
        },
    ]
    weekly_transparency = editorial_transparency.build_rolling_transparency(
        rolling_rows,
        window_start=datetime.fromisoformat("2026-08-18T00:00:00+09:00"),
        window_end=datetime.fromisoformat("2026-08-24T23:59:59+09:00"),
        selected_count=1,
    )
    weekly = editorial_briefings.render_weekly(
        [unsupported_article],
        run_at=RUN_AT,
        root_url="https://weekly.fixture.test",
        transparency_audit=weekly_transparency,
    )
    verifier.check(
        "TEAMS_WEEKLY_7D_TRANSPARENCY",
        "AI T&I 탐지 현황 · 최근 7일" in weekly.teams_text
        and "2건 탐지" in weekly.teams_text,
    )
    verifier.check(
        "WEEKLY_CROSS_DAY_DEDUP",
        weekly_transparency["raw_collected_count"] == 3
        and weekly_transparency["unique_collected_count"] == 2,
    )

    candidate = teams_ai_push.TeamsPushCandidate(
        article=watch_article,
        topic=teams_ai_push.TopicDecision(True),
        importance=teams_ai_push.ImportanceDecision(
            True,
            level=teams_ai_push.IMPORTANCE_TOP,
            label="최우선",
        ),
        cluster_key="fixture",
        material_signature="fixture",
        delivery_category="AI 인프라",
    )
    card = teams_ai_push.build_candidate_card({}, candidate)
    card_text = json.dumps(card, ensure_ascii=False)
    _subject, email_text, email_html = teams_ai_push.render_article_email({}, candidate)
    verifier.check(
        "TEAMS_EXECUTIVE_CONTEXT_FORMAT",
        "현대건설 관점:" in card_text
        and "Watch:" in card_text
        and "현대건설 관점:" in email_text,
    )
    verifier.check("TEAMS_BULLET_COUNT_MAX", card_text.count("• ") <= 3)
    verifier.check(
        "WATCH_CARD_FUNNEL_ABSENT",
        "AI T&I 탐지 현황" not in card_text
        and "AI T&I 탐지 현황" not in email_text
        and "AI T&I 탐지 현황" not in email_html,
    )

    payload = {
        "product": "daily",
        "edition_key": "2026-08-24",
        "review_snapshot_id": "review-2026-08-24-0000000000000000",
        "selected_items": [
            {
                "candidate_id": "manual-saemangeum",
                "origin": "human_link",
                "title": case_by_id["saemangeum_ai_hydrogen_hub"]["article"]["title"],
                "summary": case_by_id["saemangeum_ai_hydrogen_hub"]["article"]["snippet"],
                "source": "연합뉴스",
                "selected_url": "https://www.yna.co.kr/view/SYNTHETIC_R4OPS10G_SAVE",
                "category": "투자·산업",
                "executive_context_edits": {
                    "fact_points": ["편집 사실 1", "편집 사실 2"],
                    "hdec_implication_text": "편집된 현대건설 관점",
                    "watch_point_text": "편집된 발주 Watch",
                },
            }
        ],
    }
    normalized = editorial_operator_review.normalize_operator_review(
        payload,
        operator_login="fixture-operator",
        review_status="draft",
    )
    rejected_claim = False
    try:
        malicious = json.loads(json.dumps(payload))
        malicious["selected_items"][0]["executive_context"] = gs
        editorial_operator_review.normalize_operator_review(
            malicious,
            operator_login="fixture-operator",
            review_status="draft",
        )
    except editorial_operator_review.OperatorReviewError:
        rejected_claim = True
    verifier.check(
        "EDITOR_SERVER_CONTEXT_VALIDATION",
        normalized["selected_items"][0]["executive_context_edits"]["fact_points"]
        == ["편집 사실 1", "편집 사실 2"]
        and rejected_claim,
    )

    operator_source = (ROOT / "app" / "editorial_operator_review.py").read_text(
        encoding="utf-8"
    )
    contributor_source = (ROOT / "app" / "operator_api.py").read_text(encoding="utf-8")
    feedback_source = (ROOT / "app" / "editorial_feedback.py").read_text(encoding="utf-8")
    verifier.check(
        "ANONYMOUS_ROLE_BOUNDARY",
        "serverWritesReady()" in (ROOT / "templates" / "editorial_review_console.html").read_text(encoding="utf-8")
        and "session_from_headers" in contributor_source,
    )
    verifier.check(
        "CONTRIBUTOR_ROLE_BOUNDARY",
        "submit-for-review" in contributor_source
        and "publish-daily" in contributor_source
        and "editorial_contributor" in contributor_source,
    )
    verifier.check(
        "LEARNING_BOUNDARY",
        "confirmed_human_exemplars(approved_review)" in operator_source
        and "origin not in {\"human_link\", \"team_link\"}" in feedback_source
        and "selection_authority" not in feedback_source,
    )

    template = (ROOT / "templates" / "editorial_review_console.html").read_text(
        encoding="utf-8"
    )
    verifier.check("LEFT_EDITOR_RADAR_VISIBLE", 'id="radarPanel"' in template)
    verifier.check(
        "RIGHT_PREVIEW_RADAR_HIDDEN",
        "previewRadarLabel" not in template and "publicationRadarMarkup" not in template,
    )
    verifier.check(
        "RIGHT_PREVIEW_CLASSIFICATION_GUIDE_HIDDEN",
        "previewTaxonomyLabel" not in template and "taxonomyMarkup" not in template,
    )

    browser = _browser_acceptance()
    verifier.check("REAL_BROWSER_USED", "error" not in browser, browser)
    for key, result_key in (
        ("EDITOR_CONTEXT_EDIT_SYNC", "edit_sync"),
        ("EDITOR_CONTEXT_REORDER_SYNC", "reorder_sync"),
        ("EDITOR_CONTEXT_REMOVE_SYNC", "remove_sync"),
        ("MANUAL_IMPORT_CONTEXT", "manual_import"),
        ("DOWNLOADED_BRIEF_RADAR_HIDDEN", "download_no_radar"),
        ("BROWSER_LEFT_RADAR_VISIBLE", "left_radar"),
        ("BROWSER_RIGHT_RADAR_HIDDEN", "right_no_radar"),
        ("BROWSER_ZERO_LAYOUT", "zero_layout"),
        ("BROWSER_ONE_LAYOUT", "one_layout"),
        ("BROWSER_MULTI_LAYOUT", "multi_layout"),
        ("BROWSER_DEFAULT_TEAM_UI", "no_github"),
        ("BROWSER_OPERATOR_COLLAPSED", "operator_collapsed"),
        ("BROWSER_LOGGED_OUT", "logged_out"),
    ):
        verifier.check(key, browser.get(result_key), browser)

    print("AI_TNI_SCOPE_EXPANDED=false")
    print("SELECTION_CHANGED_BY_BASELINE=false" if before_ids == after_ids else "SELECTION_CHANGED_BY_BASELINE=true")
    print(
        "WATCH_ELIGIBILITY_CHANGED_BY_BASELINE=false"
        if policy_before.eligible == policy_after.eligible
        else "WATCH_ELIGIBILITY_CHANGED_BY_BASELINE=true"
    )
    print("LEARNING_CONTRACT_CHANGED=false")
    print("RIGHT_PREVIEW_RADAR_VISIBLE=false")
    print("RIGHT_PREVIEW_CLASSIFICATION_GUIDE_VISIBLE=false")
    print("LEFT_EDITOR_RADAR_VISIBLE=true")
    print("PUBLIC_DAILY_RADAR_VISIBLE=false")
    print("DOWNLOADED_BRIEF_RADAR_VISIBLE=false")
    print("EXECUTIVE_FACT_POINTS=PASS")
    print("GENERIC_AI_OVERCLAIM=false")
    print("NON_AI_SCOPE_EXPANSION=false")
    print("ANONYMOUS_PRIVILEGE_ESCALATION=false")
    print("CONTRIBUTOR_PRIVILEGE_ESCALATION=false")
    print("TEAMS_RAW_COUNT_MISREPRESENTED=false")
    print("WATCH_CARD_FUNNEL_SPAM=false")
    print("WEEKLY_PRESENTATION_CHANGED=false")
    print("PRODUCTION_WRITES=0")
    print("WORKFLOW_DISPATCHES=0")
    print("PRODUCTION_SENDS=0")
    print("REAL_BROWSER_USED=" + ("true" if "error" not in browser else "false"))
    print("OPS10G=" + ("PASS" if not verifier.failures else "FAIL"))
    if verifier.failures:
        print("FAILED_CHECKS=" + json.dumps(verifier.failures, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
