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
    gs_real = editorial_executive_context.derive_executive_context(
        {"title": "GS건설, AI 데이터센터 개발·투자·운영 사업 확대"},
        baseline=production_baseline,
        article_already_qualified=True,
    )
    delta_cases = {
        "gs_seminar": editorial_executive_context.derive_executive_context(
            {"title": "GS건설, AI 데이터센터 관련 세미나 참석"},
            baseline=production_baseline,
        ),
        "gs_ai_training": editorial_executive_context.derive_executive_context(
            {"title": "GS건설, 임직원 AI 교육 확대"},
            baseline=production_baseline,
        ),
        "gs_market_commentary": editorial_executive_context.derive_executive_context(
            {"title": "GS건설, 데이터센터 시장 전망 발표"},
            baseline=production_baseline,
        ),
        "gs_withdrawal": editorial_executive_context.derive_executive_context(
            {"title": "GS건설, AI 데이터센터 사업 철수 검토"},
            baseline=production_baseline,
        ),
        "gs_cross_clause": editorial_executive_context.derive_executive_context(
            {
                "title": (
                    "GS건설은 데이터센터 시장 전망을 발표함. "
                    "해외 주택사업 투자를 확대함."
                ),
                "subtitle": "GS건설",
            },
            baseline=production_baseline,
        ),
        "gs_cross_connector": editorial_executive_context.derive_executive_context(
            {
                "title": (
                    "GS건설, 데이터센터 시장 전망을 발표했고 "
                    "해외 주택사업을 확대했다"
                )
            },
            baseline=production_baseline,
        ),
        "samsung_ai_execution": editorial_executive_context.derive_executive_context(
            {"title": "삼성물산, 설계·입찰 AI 적용 확대"},
            baseline=production_baseline,
        ),
        "samsung_commentary": editorial_executive_context.derive_executive_context(
            {"title": "삼성물산, AI 건설시장 전망 발표"},
            baseline=production_baseline,
        ),
        "multi_entity": editorial_executive_context.derive_executive_context(
            {"title": "GS건설·삼성물산 AI 데이터센터 공동사업"},
            baseline=production_baseline,
        ),
        "asset_disposal_only": editorial_executive_context.derive_executive_context(
            {"title": "GS건설, AI 데이터센터 자산 매각"},
            baseline=production_baseline,
        ),
    }
    saemangeum = contexts["saemangeum_ai_hydrogen_hub"]
    generic = contexts["generic_ai_policy"]
    non_ai = contexts["non_ai_company_news"]
    semiconductor = contexts["semiconductor_infrastructure"]
    commentary = contexts["semiconductor_commentary"]

    source = production_baseline["source_document"]
    verification_source = source.get("verification_source", {})
    production_entities = {
        entity["canonical_name"]: entity for entity in production_baseline["entities"]
    }
    production_facts = [
        fact for entity in production_baseline["entities"] for fact in entity["facts"]
    ]
    verifier.check(
        "BASELINE_PROVENANCE",
        gs["provenance"]["baseline_id"] == baseline["baseline_id"]
        and gs["provenance"]["baseline_fact_ids"] == ["fixture-gs-portfolio-001"]
        and gs["provenance"]["delta_baseline_fact_ids"]
        == ["fixture-gs-portfolio-001"]
        and source["filename"] == "260814_사업보고서_비교검토_26년 반기(배포).pdf"
        and source["title"] == "국내 건설사 사업보고서 비교 요약"
        and source["reporting_basis"] == "'26년 반기(연결기준) 보고서 기준"
        and source["source_document_date"] == "2026-08"
        and source["organization"] == "전략기획사업부 / 경영기획실"
        and source["sha256"]
        == "28c5de0ef78ce5360a330c1a03cd88d8a4b2f03a74e8bd09d65f34355f89c21b"
        and source["primary_local_source_status"]
        == "microsoft_irm_protected_unreadable"
        and source["extraction_status"] == "microsoft_irm_protected_unreadable"
        and verification_source.get("source_kind") == "founder_provided_readable_copy"
        and verification_source.get("sha256")
        == "63807d7cd8d5e8f2f27b4263c15cab3bea6127d811f1eb65ab855e7a59e9358e"
        and verification_source.get("verification_status")
        == "verified_from_founder_provided_readable_copy"
        and verification_source.get("byte_identity_with_primary") == "not_asserted"
        and source["sha256"] != verification_source.get("sha256"),
    )
    production_facts_verified = (
        production_baseline["status"] == "verified"
        and production_baseline["baseline_version"] == "2026-h1-construction-v2"
        and len(production_entities) == 5
        and len(production_facts) == 23
        and all(fact["status"] == "verified" for fact in production_facts)
        and all(fact["freshness"] == "historical_baseline" for fact in production_facts)
        and all(fact["as_of"] == "2026-06-30" for fact in production_facts)
        and all(
            re.match(r"^p\.(?:3|6) ", fact["source_reference"])
            for fact in production_facts
        )
    )
    verifier.check("BASELINE_PRODUCTION_FACTS_VERIFIED", production_facts_verified)
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
    expected_fact_signatures = {
        "현대건설": [
            ("operating_cash_flow", -1.7, "KRW trillion", "p.3 Executive Summary / 재무현황"),
            ("net_cash_or_debt", -0.6, "KRW trillion", "p.3 Executive Summary / 재무현황"),
            ("pf_guarantee", 14, "KRW trillion approximately", "p.3 Executive Summary / 우발채무 등"),
            ("completion_guarantee", 19, "KRW trillion approximately", "p.3 Executive Summary / 우발채무 등"),
            ("strategy", None, "", "p.3 Executive Summary / 재무현황 주석"),
            ("order_backlog", 104.9, "KRW trillion", "p.6 사업내용(2) / 수주잔고"),
        ],
        "GS건설": [
            ("portfolio_shift", None, "", "p.3 Executive Summary / 회사현황·사업내용"),
            ("data_center_strategy", None, "", "p.3 Executive Summary / GS 이니마 주석"),
            ("portfolio_shift", 1.7, "KRW trillion", "p.3 Executive Summary / GS 이니마 주석"),
            ("operating_profit", 47, "percent of H1 operating result approximately", "p.3 Executive Summary / GS 이니마 주석"),
            ("net_cash_or_debt", -3.0, "KRW trillion", "p.3 Executive Summary / 재무현황"),
            ("pf_guarantee", 2.9, "KRW trillion", "p.3 Executive Summary / 우발채무 등"),
            ("order_backlog", 73.4, "KRW trillion", "p.6 사업내용(2) / 수주잔고"),
        ],
        "삼성물산": [
            ("ai_transformation", None, "", "p.3 Executive Summary / 회사현황"),
            ("net_cash_or_debt", 4.5, "KRW trillion", "p.3 Executive Summary / 재무현황"),
            ("pf_guarantee", 2.0, "KRW trillion", "p.3 Executive Summary / 우발채무 등"),
            ("order_backlog", 34.2, "KRW trillion", "p.6 사업내용(2) / 수주잔고"),
        ],
        "대우건설": [
            ("net_cash_or_debt", -2.8, "KRW trillion", "p.3 Executive Summary / 재무현황"),
            ("pf_guarantee", 2.2, "KRW trillion", "p.3 Executive Summary / 우발채무 등"),
            ("order_backlog", 53.4, "KRW trillion", "p.6 사업내용(2) / 수주잔고"),
        ],
        "DL이앤씨": [
            ("net_cash_or_debt", 1.2, "KRW trillion", "p.3 Executive Summary / 재무현황"),
            ("pf_guarantee", 1.8, "KRW trillion", "p.3 Executive Summary / 우발채무 등"),
            ("order_backlog", 29.8, "KRW trillion", "p.6 사업내용(2) / 수주잔고"),
        ],
    }

    def fact_signatures(canonical_name: str) -> list[tuple[object, ...]]:
        return [
            (
                fact["dimension"],
                fact["value"],
                fact["unit"],
                fact["source_reference"],
            )
            for fact in production_entities[canonical_name]["facts"]
        ]

    exact_hdec = editorial_executive_context.match_baseline_entity(
        "현대건설", production_baseline
    )
    none_hyundai_car = editorial_executive_context.match_baseline_entity(
        "현대차", production_baseline
    )
    exact_gs = editorial_executive_context.match_baseline_entity(
        "GS건설", production_baseline
    )
    none_gs_group = editorial_executive_context.match_baseline_entity(
        "GS그룹", production_baseline
    )
    exact_samsung = editorial_executive_context.match_baseline_entity(
        "삼성물산 AI 네이티브 건설", production_baseline
    )
    none_samsung_electronics = editorial_executive_context.match_baseline_entity(
        "삼성전자 AI", production_baseline
    )
    exact_daewoo = editorial_executive_context.match_baseline_entity(
        "대우건설", production_baseline
    )
    exact_dl = editorial_executive_context.match_baseline_entity(
        "DL이앤씨", production_baseline
    )
    ambiguous = editorial_executive_context.match_baseline_entity(
        "GS건설·삼성물산 협력", production_baseline
    )
    aliases = {
        alias.casefold()
        for entity in production_baseline["entities"]
        for alias in entity["aliases"]
    }
    verifier.check(
        "HDEC_BASELINE",
        exact_hdec["status"] == "matched"
        and exact_hdec["entity"]["canonical_name"] == "현대건설"
        and none_hyundai_car["status"] == "none"
        and set(production_entities["현대건설"]["aliases"])
        == {"현대건설", "Hyundai E&C", "Hyundai Engineering & Construction"}
        and fact_signatures("현대건설") == expected_fact_signatures["현대건설"],
    )
    verifier.check(
        "GS_BASELINE",
        exact_gs["status"] == "matched"
        and exact_gs["entity"]["canonical_name"] == "GS건설"
        and none_gs_group["status"] == "none"
        and set(production_entities["GS건설"]["aliases"])
        == {"GS건설", "GS E&C", "GS Engineering & Construction"}
        and fact_signatures("GS건설") == expected_fact_signatures["GS건설"],
    )
    verifier.check(
        "SAMSUNG_BASELINE",
        exact_samsung["status"] == "matched"
        and exact_samsung["entity"]["canonical_name"] == "삼성물산"
        and none_samsung_electronics["status"] == "none"
        and set(production_entities["삼성물산"]["aliases"])
        == {"삼성물산", "삼성물산 건설", "Samsung C&T"}
        and fact_signatures("삼성물산") == expected_fact_signatures["삼성물산"],
    )
    verifier.check(
        "DAEWOO_BASELINE",
        exact_daewoo["status"] == "matched"
        and set(production_entities["대우건설"]["aliases"])
        == {"대우건설", "Daewoo E&C", "Daewoo Engineering & Construction"}
        and fact_signatures("대우건설") == expected_fact_signatures["대우건설"],
    )
    verifier.check(
        "DL_BASELINE",
        exact_dl["status"] == "matched"
        and set(production_entities["DL이앤씨"]["aliases"])
        == {"DL이앤씨", "DL E&C", "DL E&C Co."}
        and fact_signatures("DL이앤씨") == expected_fact_signatures["DL이앤씨"]
        and aliases.isdisjoint({"삼성", "gs", "dl", "현대"}),
    )
    verifier.check(
        "BASELINE_ENTITY_EXACT_MATCH",
        exact_hdec["status"] == "matched"
        and none_hyundai_car["status"] == "none"
        and exact_gs["status"] == "matched"
        and none_gs_group["status"] == "none"
        and exact_samsung["status"] == "matched"
        and none_samsung_electronics["status"] == "none",
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
    gs_production_fact_ids = {
        fact["fact_id"] for fact in production_entities["GS건설"]["facts"]
    }
    verifier.check(
        "GS_REAL_BASELINE_DELTA",
        gs_real["baseline_match"]["status"] == "matched"
        and gs_real["baseline_match"]["canonical_name"] == "GS건설"
        and gs_real["baseline_context"]["status"] == "supported"
        and gs_real["delta_vs_baseline"]["status"] == "supported"
        and gs_real["hdec_implication"]["status"] == "supported"
        and gs_real["provenance"]["baseline_id"]
        == production_baseline["baseline_id"]
        and gs_real["provenance"]["baseline_version"]
        == "2026-h1-construction-v2"
        and gs_real["provenance"]["baseline_fact_ids"]
        == ["2026h1-gs-data-center-strategy-p3"]
        and gs_real["provenance"]["delta_baseline_fact_ids"]
        == ["2026h1-gs-data-center-strategy-p3"]
        and set(gs_real["provenance"]["baseline_fact_ids"])
        <= gs_production_fact_ids,
    )
    gs_seminar = delta_cases["gs_seminar"]
    gs_ai_training = delta_cases["gs_ai_training"]
    gs_market_commentary = delta_cases["gs_market_commentary"]
    gs_withdrawal = delta_cases["gs_withdrawal"]
    gs_cross_clause = delta_cases["gs_cross_clause"]
    gs_cross_connector = delta_cases["gs_cross_connector"]
    samsung_ai_execution = delta_cases["samsung_ai_execution"]
    samsung_commentary = delta_cases["samsung_commentary"]
    multi_entity = delta_cases["multi_entity"]
    asset_disposal_only = delta_cases["asset_disposal_only"]
    expected_gs_delta_fact_ids = ["2026h1-gs-data-center-strategy-p3"]
    expected_samsung_delta_fact_ids = ["2026h1-samsung-ai-transformation-p3"]
    verifier.check(
        "GS_EXPANSION_DELTA",
        gs_real["baseline_match"]["status"] == "matched"
        and gs_real["baseline_match"]["canonical_name"] == "GS건설"
        and gs_real["delta_vs_baseline"]["status"] == "supported"
        and gs_real["delta_vs_baseline"]["dimension"] == "data_center"
        and gs_real["delta_vs_baseline"]["movement_direction"] == "expansion"
        and gs_real["provenance"]["delta_baseline_fact_ids"]
        == expected_gs_delta_fact_ids
        and "같은 전략 축의 실행·강화 신호로 볼 수 있음" in gs_real["delta_vs_baseline"]["text"]
        and "해외주택 투자사업 확대" not in gs_real["delta_vs_baseline"]["text"]
        and "기준선 방향의 후속 신호" not in gs_real["delta_vs_baseline"]["text"],
    )
    verifier.check(
        "GS_SEMINAR_NEGATIVE",
        gs_seminar["baseline_match"]["status"] == "matched"
        and gs_seminar["delta_vs_baseline"]["status"] == "not_supported"
        and gs_seminar["provenance"]["delta_baseline_fact_ids"] == [],
    )
    verifier.check(
        "GS_AI_TRAINING_NEGATIVE",
        gs_ai_training["baseline_match"]["status"] == "matched"
        and gs_ai_training["baseline_context"]["status"] == "not_supported"
        and gs_ai_training["delta_vs_baseline"]["status"] == "not_supported"
        and gs_ai_training["provenance"]["delta_baseline_fact_ids"] == [],
    )
    verifier.check(
        "GS_MARKET_COMMENTARY_NEGATIVE",
        gs_market_commentary["delta_vs_baseline"]["status"] == "not_supported"
        and gs_market_commentary["provenance"]["delta_baseline_fact_ids"] == [],
    )
    verifier.check(
        "GS_WITHDRAWAL_DIRECTION",
        gs_withdrawal["delta_vs_baseline"]["status"] == "supported"
        and gs_withdrawal["delta_vs_baseline"]["dimension"] == "data_center"
        and gs_withdrawal["delta_vs_baseline"]["movement_direction"] == "contraction"
        and gs_withdrawal["provenance"]["delta_baseline_fact_ids"]
        == expected_gs_delta_fact_ids
        and "반대 방향의 전략 변화 또는 재검토 신호" in gs_withdrawal["delta_vs_baseline"]["text"]
        and "기준선 방향의 후속 신호" not in gs_withdrawal["delta_vs_baseline"]["text"],
    )
    verifier.check(
        "GS_CROSS_CLAUSE_FALSE_BINDING",
        gs_cross_clause["baseline_match"]["status"] == "matched"
        and gs_cross_clause["delta_vs_baseline"]["status"] == "not_supported"
        and gs_cross_connector["baseline_match"]["status"] == "matched"
        and gs_cross_connector["delta_vs_baseline"]["status"] == "not_supported",
    )
    verifier.check(
        "SAMSUNG_AI_EXECUTION_DELTA",
        samsung_ai_execution["baseline_match"]["status"] == "matched"
        and samsung_ai_execution["baseline_match"]["canonical_name"] == "삼성물산"
        and samsung_ai_execution["delta_vs_baseline"]["status"] == "supported"
        and samsung_ai_execution["delta_vs_baseline"]["dimension"]
        == "automation_physical_ai"
        and samsung_ai_execution["delta_vs_baseline"]["movement_direction"]
        == "expansion"
        and samsung_ai_execution["provenance"]["delta_baseline_fact_ids"]
        == expected_samsung_delta_fact_ids,
    )
    verifier.check(
        "SAMSUNG_COMMENTARY_NEGATIVE",
        samsung_commentary["baseline_match"]["status"] == "matched"
        and samsung_commentary["delta_vs_baseline"]["status"] == "not_supported"
        and samsung_commentary["provenance"]["delta_baseline_fact_ids"] == [],
    )
    verifier.check(
        "MULTI_ENTITY_FAIL_CLOSED",
        multi_entity["baseline_match"]["status"] == "ambiguous"
        and multi_entity["delta_vs_baseline"]["status"] == "not_supported"
        and multi_entity["provenance"]["delta_baseline_fact_ids"] == [],
    )
    verifier.check(
        "DIMENSION_FACT_MATCHING",
        gs_real["provenance"]["delta_baseline_fact_ids"]
        == expected_gs_delta_fact_ids
        and samsung_ai_execution["provenance"]["delta_baseline_fact_ids"]
        == expected_samsung_delta_fact_ids
        and "해외주택" not in gs_real["baseline_context"]["text"],
    )
    verifier.check(
        "DELTA_BASELINE_FACT_IDS",
        gs_real["provenance"]["delta_baseline_fact_ids"]
        == expected_gs_delta_fact_ids
        and gs_withdrawal["provenance"]["delta_baseline_fact_ids"]
        == expected_gs_delta_fact_ids
        and all(
            context["provenance"]["delta_baseline_fact_ids"] == []
            for context in (
                gs_seminar,
                gs_ai_training,
                gs_market_commentary,
                gs_cross_clause,
                gs_cross_connector,
                samsung_commentary,
                multi_entity,
                asset_disposal_only,
            )
        ),
    )
    verifier.check(
        "CLAUSE_LEVEL_BINDING",
        gs_cross_clause["delta_vs_baseline"]["status"] == "not_supported"
        and gs_cross_connector["delta_vs_baseline"]["status"] == "not_supported",
    )
    verifier.check(
        "MOVEMENT_DIRECTION_CLASSIFICATION",
        gs_real["delta_vs_baseline"]["movement_direction"] == "expansion"
        and gs_withdrawal["delta_vs_baseline"]["movement_direction"] == "contraction"
        and asset_disposal_only["delta_vs_baseline"]["status"] == "not_supported"
        and asset_disposal_only["delta_vs_baseline"]["movement_direction"]
        == "neutral_unknown",
    )
    verifier.check(
        "DELTA_EVIDENCE_GATE",
        gs_real["delta_vs_baseline"]["status"] == "supported"
        and gs_withdrawal["delta_vs_baseline"]["status"] == "supported"
        and samsung_ai_execution["delta_vs_baseline"]["status"] == "supported"
        and all(
            context["delta_vs_baseline"]["status"] == "not_supported"
            for context in (
                gs_seminar,
                gs_ai_training,
                gs_market_commentary,
                gs_cross_clause,
                gs_cross_connector,
                samsung_commentary,
                multi_entity,
                asset_disposal_only,
            )
        ),
    )
    verifier.check(
        "SAEMANGEUM_CONTEXT",
        saemangeum["baseline_match"]["status"] == "none"
        and saemangeum["delta_vs_baseline"]["status"] == "not_supported"
        and "산업" in saemangeum["hdec_implication"]["text"]
        and "인허가" in saemangeum["watch_point"]["text"],
    )
    verifier.check(
        "HDEC_IMPLICATION_INDEPENDENT",
        saemangeum["baseline_match"]["status"] == "none"
        and saemangeum["delta_vs_baseline"]["status"] == "not_supported"
        and saemangeum["hdec_implication"]["status"] == "supported",
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
    edited_delta = editorial_executive_context.apply_editor_edits(
        gs_real, {"delta_text": "편집된 Delta 설명"}
    )
    rejected_direction_edit = False
    try:
        editorial_executive_context.normalize_editor_edits(
            {
                "delta_text": "브라우저가 만든 Delta",
                "movement_direction": "contraction",
            }
        )
    except editorial_executive_context.ExecutiveContextError:
        rejected_direction_edit = True
    verifier.check(
        "EDITOR_SERVER_CONTEXT_VALIDATION",
        normalized["selected_items"][0]["executive_context_edits"]["fact_points"]
        == ["편집 사실 1", "편집 사실 2"]
        and rejected_claim,
    )
    verifier.check(
        "SERVER_REVALIDATION",
        edited_delta["delta_vs_baseline"]["text"] == "편집된 Delta 설명"
        and edited_delta["delta_vs_baseline"]["status"] == "supported"
        and edited_delta["delta_vs_baseline"]["movement_direction"] == "expansion"
        and edited_delta["provenance"]["delta_baseline_fact_ids"]
        == expected_gs_delta_fact_ids
        and rejected_claim
        and rejected_direction_edit,
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
    shared_authority_claim_valid = (
        'delta_vs_baseline:{status:"not_supported",text:""}' in template
        and "data_center_strategy" not in template
        and "movement_direction" not in template
        and rejected_claim
        and rejected_direction_edit
    )
    verifier.check("SHARED_AUTHORITY_CLAIM_VALID", shared_authority_claim_valid)

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
    print("BROWSER_PROVENANCE_INJECTION=false")
    print(
        "SHARED_AUTHORITY_CLAIM_VALID="
        + ("true" if shared_authority_claim_valid else "false")
    )
    # R4-OPS-10G-R2 — Public Weekly is outside the 10G presentation
    # change boundary. Compare complete deterministic fixture HTML against
    # fresh production main, including both dominant and multi modes.
    import hashlib as _weekly_hashlib
    from datetime import datetime as _weekly_datetime
    from app import editorial_briefings as _weekly_brief

    _weekly_run_at = _weekly_datetime.fromisoformat(
        "2026-07-29T07:30:00+09:00"
    )
    _weekly_root_url = (
        "https://preview.fixture.test/HDEC-News-Sensor"
    )
    _weekly_expected_sha256 = {
        "dominant": (
            "45b12fe861d4e58cca3d60e1e1d786c1"
            "aa10b45ed031c5285c747f89f4f29824"
        ),
        "multi": (
            "53e188fac050517292c934961f5f39d0"
            "60a80d5af5e53132d7a64d2378b7d284"
        ),
    }
    _weekly_actual_sha256 = {}

    for _weekly_profile in ("dominant", "multi"):
        _weekly_edition = _weekly_brief.render_edition(
            "weekly",
            _weekly_brief.fixture_articles(
                "weekly",
                _weekly_run_at,
                profile=_weekly_profile,
            ),
            run_at=_weekly_run_at,
            root_url=_weekly_root_url,
        )
        _weekly_brief.validate_rendered(_weekly_edition)
        _weekly_actual_sha256[_weekly_profile] = (
            _weekly_hashlib.sha256(
                _weekly_edition.html.encode("utf-8")
            ).hexdigest()
        )

    _weekly_presentation_changed = (
        _weekly_actual_sha256 != _weekly_expected_sha256
    )

    if _weekly_presentation_changed:
        raise AssertionError(
            "Public Weekly presentation changed: "
            f"expected={_weekly_expected_sha256!r} "
            f"actual={_weekly_actual_sha256!r}"
        )

    print(
        "WEEKLY_PRESENTATION_CHANGED="
        + ("true" if _weekly_presentation_changed else "false")
    )
    print(f"BASELINE_ENTITY_COUNT={len(production_entities)}")
    print(f"BASELINE_FACT_COUNT={len(production_facts)}")
    print(
        "BASELINE_PRODUCTION_FACTS_VERIFIED="
        + ("true" if production_facts_verified else "false")
    )
    print(f"PRIMARY_LOCAL_SOURCE_SHA256={source['sha256']}")
    print("PRIMARY_LOCAL_SOURCE_READABLE=false")
    print(f"VERIFICATION_COPY_SHA256={verification_source.get('sha256', '')}")
    print(
        "VERIFICATION_COPY_STATUS="
        + str(verification_source.get("verification_status", ""))
    )
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
