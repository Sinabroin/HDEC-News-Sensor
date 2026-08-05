#!/usr/bin/env python3
"""Verify News Censor semantic tokens and browser filtering on generated HTML.

This gate deliberately separates navigation parity from article semantics.  It
uses the embedded canonical article model as the expected set, checks every
rendered ``data-t`` token against explicit evidence, and (with ``--browser``)
clicks every visible filter in Chromium to prove the DOM set and promoted lead.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import threading
from dataclasses import dataclass
from html.parser import HTMLParser
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Mapping

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
for item in (ROOT, SCRIPTS):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

import build_news_censor  # noqa: E402

REFERENCE = Path("/tmp/d7ak6e-r4r5-reference/NEW_CENSOR (1)(1).html")
REFERENCE_SHA256 = "c4a1d129a9e8b6d824b961e2042f345cfc2eb405dcbc488a542e5bc6cee14804"

FILTER_MATRIX = (
    ("홈", "전체", "all", "magazine", "magazine"),
    ("사업영역", "전체", "biz", "all", "biz"),
    ("사업영역", "플랜트", "biz", "lens:plant", "lens:plant"),
    ("사업영역", "토목", "biz", "lens:civil_infrastructure", "lens:civil_infrastructure"),
    ("사업영역", "건축·주택", "biz", "lens:building_housing", "lens:building_housing"),
    ("사업영역", "시행사", "biz", "lens:developers", "lens:developers"),
    ("사업영역", "개발사업", "biz", "lens:development_business", "lens:development_business"),
    ("동종사", "전체", "peers", "all", "peers"),
    ("동종사", "경쟁 시공사", "peers", "lens:competitor_contractors", "lens:competitor_contractors"),
    ("동종사", "GS건설", "peers", "sub:4e7d40c0", "sub:4e7d40c0"),
    ("동종사", "대우건설", "peers", "sub:fd3376dd", "sub:fd3376dd"),
    ("동종사", "롯데건설", "peers", "sub:e8a249a3", "sub:e8a249a3"),
    ("현대그룹", "전체", "hdec", "all", "hdec"),
    ("현대그룹", "현대 그룹사", "hdec", "lens:hyundai_group", "lens:hyundai_group"),
    ("현대그룹", "현대엔지니어링", "hdec", "sub:d914e406", "sub:d914e406"),
    ("현대그룹", "국내현장", "hdec", "lens:domestic_site", "lens:domestic_site"),
    ("안전품질", "전체", "safety", "all", "safety"),
    ("안전품질", "안전·품질", "safety", "lens:safety_quality", "lens:safety_quality"),
    ("해외지정학", "전체", "global", "all", "global"),
    ("해외지정학", "해외수주", "global", "lens:global_business", "lens:global_business"),
    ("AI", "전체", "ai", "all", "ai"),
    ("AI", "AI", "ai", "lens:ai", "lens:ai"),
    ("AI", "신재생·전력", "ai", "lens:new_energy", "lens:new_energy"),
)

SCREENSHOT_FILTERS = {
    ("peers", "lens:competitor_contractors"): "competitor-contractors.png",
    ("hdec", "lens:hyundai_group"): "hyundai-group.png",
    ("safety", "lens:safety_quality"): "safety-quality.png",
    ("global", "lens:global_business"): "global-business.png",
    ("ai", "lens:ai"): "ai.png",
    ("ai", "lens:new_energy"): "new-energy.png",
}


@dataclass(frozen=True)
class SealedCase:
    name: str
    title: str
    snippet: str
    expected: frozenset[str]
    forbidden: frozenset[str]


SEALED_CASES = (
    SealedCase(
        "competitor contractor",
        "GS건설, 싱가포르 데이터센터 EPC 수주",
        "해외 데이터센터 건설 계약을 체결했다.",
        frozenset({"peers", "lens:competitor_contractors", "sub:4e7d40c0"}),
        frozenset({"hdec", "safety"}),
    ),
    SealedCase(
        "generic AI is not peer or Hyundai",
        "AI 데이터센터 전력 인프라 투자 확대",
        "산업용 AI 인프라 건설 투자 계획이다.",
        frozenset({"ai", "lens:ai"}),
        frozenset({"peers", "hdec", "lens:competitor_contractors", "lens:hyundai_group"}),
    ),
    SealedCase(
        "Hyundai Engineering material subject",
        "현대엔지니어링, 사우디 플랜트 EPC 수주",
        "중동 에너지 인프라 프로젝트 계약이다.",
        frozenset({"hdec", "lens:hyundai_group", "sub:d914e406", "global", "lens:global_business"}),
        frozenset({"peers"}),
    ),
    SealedCase(
        "incidental modern word",
        "현대적 설계가 돋보이는 소비자 가전",
        "미국 기업이 신제품을 공개했다.",
        frozenset(),
        frozenset({"hdec", "global", "ai", "safety"}),
    ),
    SealedCase(
        "construction safety",
        "노후교량 안전점검 결과 D·E등급 긴급 보수",
        "교량 시공과 시설물 안전 대책을 강화한다.",
        frozenset({"safety", "lens:safety_quality", "lens:civil_infrastructure"}),
        frozenset({"peers", "hdec"}),
    ),
    SealedCase(
        "financial collapse metaphor",
        "AI가 아닌 레버리지의 붕괴…월가 펀드 몰락",
        "데이터센터 건설과 전력 계약 이후 AI 투자 붐이 이어졌다.",
        frozenset({"ai", "lens:ai"}),
        frozenset({"peers", "hdec", "safety", "lens:safety_quality"}),
    ),
    SealedCase(
        "overseas project",
        "현대엔지니어링, 사우디 플랜트 프로젝트 계약",
        "해외수주와 중동 EPC 공급망에 직접 영향을 준다.",
        frozenset({"global", "lens:global_business"}),
        frozenset({"safety"}),
    ),
    SealedCase(
        "foreign mention only",
        "미국 소비재 기업 분기 실적 발표",
        "주가와 매출 전망을 발표했다.",
        frozenset(),
        frozenset({"global", "ai", "peers", "hdec"}),
    ),
    SealedCase(
        "AI stock theme only",
        "AI 테마주 ETF 급등…증시 수익률 주목",
        "건설·산업 인프라와 무관한 종목 시황이다.",
        frozenset(),
        frozenset({"ai", "lens:ai", "peers", "hdec", "safety"}),
    ),
    SealedCase(
        "developer and development project",
        "시행사, 복합개발 본PF 전환 후 시공사 선정",
        "부동산 디벨로퍼가 개발사업 인허가와 토지확보를 마쳤다.",
        frozenset({"lens:developers", "lens:development_business"}),
        frozenset({"peers", "hdec"}),
    ),
    SealedCase(
        "Daewoo exact company token",
        "대우건설, 체코 원전 EPC 계약 추진",
        "경쟁 시공사의 해외 플랜트 수주 신호다.",
        frozenset({"peers", "lens:competitor_contractors", "sub:fd3376dd"}),
        frozenset({"sub:4e7d40c0", "sub:e8a249a3", "hdec"}),
    ),
)


class ArticleTagParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.articles: dict[str, set[str]] = {}
        self.order: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "article":
            return
        values = dict(attrs)
        classes = set(str(values.get("class") or "").split())
        if not (classes & {"lead", "nitem"}):
            return
        article_id = str(values.get("data-article") or "")
        if article_id:
            self.articles[article_id] = set(str(values.get("data-t") or "").split())
            self.order.append(("lead" if "lead" in classes else "nitem", article_id))


def _model(html: str) -> dict[str, dict]:
    match = re.search(
        r'<script\s+type="application/json"\s+id="article-data">(.*?)</script>',
        html,
        flags=re.DOTALL,
    )
    if not match:
        raise ValueError("article-data JSON island is missing")
    value = json.loads(match.group(1))
    if not isinstance(value, dict):
        raise ValueError("article-data must be an object")
    return value


def _tokens(article: Mapping) -> set[str]:
    values = {
        *(str(value) for value in article.get("categories") or []),
        *(str(value) for value in article.get("subfilterIds") or []),
    }
    if article.get("magazine") is True:
        values.add("magazine")
    return values


def _sealed_fixture_failures() -> list[str]:
    failures: list[str] = []
    for case in SEALED_CASES:
        result = build_news_censor._semantic_filter_contract({
            "title": case.title,
            "snippet": case.snippet,
            "source": "sealed-fixture-publisher",
            "display_relevance_reason": "sealed semantic fixture",
            "category_memberships": ["ai", "biz", "global", "hdec", "peers", "safety"],
        })
        actual = set(result["categories"]) | set(result["lens_tokens"])
        missing = sorted(case.expected - actual)
        forbidden = sorted(case.forbidden & actual)
        if missing or forbidden:
            failures.append(f"{case.name}: missing={missing} forbidden_present={forbidden}")
        for token in actual - {"all", "biz"}:
            if not result["evidence"].get(token):
                failures.append(f"{case.name}: token_without_evidence={token}")
    return failures


def _static_audit(candidate: Path, compatibility: Path | None) -> tuple[dict, list[str]]:
    html = candidate.read_text(encoding="utf-8")
    failures = _sealed_fixture_failures()
    if compatibility is not None and candidate.read_bytes() != compatibility.read_bytes():
        failures.append("canonical and compatibility outputs are not byte-identical")
    model = _model(html)
    parser = ArticleTagParser()
    parser.feed(html)
    if set(parser.articles) != set(model):
        failures.append("rendered article IDs do not equal article-data IDs")
    if len(parser.order) != len(model):
        failures.append("rendered article count does not equal semantic model count")
    if parser.order and parser.order[0][0] != "lead":
        failures.append("lead is not first")
    if sum(kind == "lead" for kind, _ in parser.order) != 1:
        failures.append("generated page does not contain exactly one semantic lead")

    article_audit = []
    for article_id, article in model.items():
        expected = _tokens(article)
        actual = parser.articles.get(article_id, set())
        if actual != expected:
            failures.append(
                f"{article_id}: data-t mismatch expected={sorted(expected)} actual={sorted(actual)}"
            )
        evidence = article.get("semanticFilterEvidence") or {}
        required = expected - {"magazine"}
        missing_evidence = sorted(token for token in required if not evidence.get(token))
        if missing_evidence:
            failures.append(f"{article_id}: missing evidence for {missing_evidence}")
        article_audit.append({
            "article_id": article_id,
            "title": article.get("title") or "",
            "publisher": article.get("source") or "",
            "top_level_category_tokens": list(article.get("categories") or []),
            "lens_and_company_tokens": list(article.get("subfilterIds") or []),
            "token_evidence": evidence,
        })

    return {
        "displayed_article_count": len(model),
        "articles": article_audit,
        "sealed_fixture_cases": len(SEALED_CASES),
        "sealed_fixture_failures": _sealed_fixture_failures(),
    }, failures


def _browser_audit(candidate: Path, screenshot_dir: Path | None) -> tuple[list[dict], list[str]]:
    from capture_news_censor_image_acceptance import (  # noqa: WPS433
        _PipeCDP,
        _QuietHandler,
        _chrome,
    )

    if screenshot_dir is not None:
        screenshot_dir.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="hdec-semantic-filter-web-") as raw:
        webroot = Path(raw)
        public = webroot / "HDEC-News-Sensor" / "news-censor"
        public.parent.mkdir(parents=True)
        public.symlink_to(candidate.parent, target_is_directory=True)
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            lambda *items, **kwargs: _QuietHandler(
                *items, directory=str(webroot), **kwargs
            ),
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        browser = _PipeCDP(_chrome())
        try:
            url = (
                f"http://127.0.0.1:{server.server_port}"
                "/HDEC-News-Sensor/news-censor/latest.html"
            )
            browser.open(url, width=1440, height=1200)
            initial = browser.evaluate("""
              (() => ({
                category:document.querySelector('.cat.active')?.dataset.cat || '',
                subbar:document.querySelector('.subbar.show')?.dataset.for || '',
                filter:document.querySelector('.subbar.show .sub.active')?.dataset.filter || ''
              }))()
            """)
            if initial != {"category": "all", "subbar": "all", "filter": "magazine"}:
                failures.append(f"initial filter state mismatch: {initial}")

            matrix: list[dict] = []
            for cat_label, sub_label, category, selected_filter, effective in FILTER_MATRIX:
                state = browser.evaluate(f"""
                  (() => {{
                    document.querySelector('.cat[data-cat={json.dumps(category)}]').click();
                    const sub = document.querySelector(
                      '.subbar[data-for={json.dumps(category)}] .sub[data-filter={json.dumps(selected_filter)}]'
                    );
                    sub.click();
                    const data = JSON.parse(document.getElementById('article-data').textContent || '{{}}');
                    const expected = Object.entries(data).filter(([_id, article]) => {{
                      const tokens = [...(article.categories || []), ...(article.subfilterIds || [])];
                      if (article.magazine) tokens.push('magazine');
                      return tokens.includes({json.dumps(effective)});
                    }}).map(([id]) => id);
                    const visible = [...document.querySelectorAll('.lead,.nitem')]
                      .filter(card => !card.classList.contains('hide'));
                    const visibleIds = visible.map(card => card.dataset.article);
                    const lead = document.querySelector('.lead');
                    const leadId = lead && !lead.classList.contains('hide') ? lead.dataset.article : '';
                    const empty = [...document.querySelectorAll('[role=status]')]
                      .find(node => node.textContent.includes('검증 기사가 없습니다'));
                    const falsePositive = visibleIds.filter(id => !expected.includes(id));
                    const falseNegative = expected.filter(id => !visibleIds.includes(id));
                    return {{
                      activeCategory:document.querySelector('.cat.active')?.dataset.cat || '',
                      visibleSubbar:document.querySelector('.subbar.show')?.dataset.for || '',
                      activeFilter:document.querySelector('.subbar.show .sub.active')?.dataset.filter || '',
                      expected, visibleIds, leadId,
                      titles:visible.map(card => (card.querySelector('h2,h3')?.textContent || '').trim()),
                      falsePositive, falseNegative,
                      unrelatedLead:leadId && !expected.includes(leadId) ? 1 : 0,
                      emptyMessageVisible:Boolean(empty && !empty.hidden)
                    }};
                  }})()
                """)
                entry = {
                    "category": cat_label,
                    "subcategory": sub_label,
                    "effective_token": effective,
                    "matching_article_count": len(state["expected"]),
                    "visible_article_ids": state["visibleIds"],
                    "visible_titles": state["titles"],
                    "false_positive_count": len(state["falsePositive"]),
                    "false_negative_count": len(state["falseNegative"]),
                    "unrelated_lead_count": state["unrelatedLead"],
                }
                matrix.append(entry)
                if state["activeCategory"] != category:
                    failures.append(f"{cat_label}/{sub_label}: wrong active category")
                if state["visibleSubbar"] != category:
                    failures.append(f"{cat_label}/{sub_label}: wrong visible subbar")
                if state["activeFilter"] != selected_filter:
                    failures.append(f"{cat_label}/{sub_label}: wrong active subfilter")
                if state["falsePositive"] or state["falseNegative"]:
                    failures.append(
                        f"{cat_label}/{sub_label}: DOM/model mismatch "
                        f"false_positive={state['falsePositive']} false_negative={state['falseNegative']}"
                    )
                if state["unrelatedLead"]:
                    failures.append(f"{cat_label}/{sub_label}: unrelated lead remains visible")
                if not state["expected"] and not state["emptyMessageVisible"]:
                    failures.append(f"{cat_label}/{sub_label}: zero-result message is not visible")
                if state["expected"] and state["emptyMessageVisible"]:
                    failures.append(f"{cat_label}/{sub_label}: empty message shown with matches")
                screenshot = SCREENSHOT_FILTERS.get((category, selected_filter))
                if screenshot_dir is not None and screenshot:
                    browser.evaluate("window.scrollTo(0,0)")
                    browser.screenshot(screenshot_dir / screenshot)
        finally:
            browser.close()
            server.shutdown()
            server.server_close()
    return matrix, failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--compatibility", type=Path)
    parser.add_argument("--audit-json", type=Path)
    parser.add_argument("--browser", action="store_true")
    parser.add_argument("--screenshot-dir", type=Path)
    args = parser.parse_args()
    candidate = args.candidate.resolve() if args.candidate else None
    compatibility = args.compatibility.resolve() if args.compatibility else None
    if candidate is not None and not candidate.is_file():
        raise SystemExit("generated candidate HTML does not exist")
    if compatibility is not None and not compatibility.is_file():
        raise SystemExit("generated compatibility HTML does not exist")
    if args.browser and candidate is None:
        raise SystemExit("--browser requires --candidate")
    if compatibility is not None and candidate is None:
        raise SystemExit("--compatibility requires --candidate")
    if args.screenshot_dir and not args.browser:
        raise SystemExit("--screenshot-dir requires --browser")

    if candidate is not None:
        static, failures = _static_audit(candidate, compatibility)
    else:
        sealed_failures = _sealed_fixture_failures()
        static = {
            "displayed_article_count": 0,
            "articles": [],
            "sealed_fixture_cases": len(SEALED_CASES),
            "sealed_fixture_failures": sealed_failures,
        }
        failures = list(sealed_failures)
    matrix: list[dict] = []
    if args.browser:
        matrix, browser_failures = _browser_audit(candidate, args.screenshot_dir)
        failures.extend(browser_failures)
    report = {
        "contract": "D7_AK_6E_R4_R5_NEWS_CENSOR_SEMANTIC_FILTER_V1",
        "reference_sha256_expected": REFERENCE_SHA256,
        "candidate": str(candidate) if candidate else "sealed-fixtures-only",
        "compatibility": str(compatibility) if compatibility else "",
        **static,
        "filter_matrix": matrix,
        "filter_matrix_count": len(matrix),
        "false_positive_count": sum(row["false_positive_count"] for row in matrix),
        "false_negative_count": sum(row["false_negative_count"] for row in matrix),
        "unrelated_lead_count": sum(row["unrelated_lead_count"] for row in matrix),
        "failures": failures,
        "external_network_requests": 0,
        "smtp_attempts": 0,
        "teams_sends": 0,
        "telegram_sends": 0,
        "production_state_writes": 0,
        "status": "PASS" if not failures else "FAIL",
    }
    if args.audit_json:
        args.audit_json.parent.mkdir(parents=True, exist_ok=True)
        args.audit_json.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(json.dumps({
        key: report[key]
        for key in (
            "displayed_article_count", "sealed_fixture_cases", "filter_matrix_count",
            "false_positive_count", "false_negative_count", "unrelated_lead_count",
            "external_network_requests", "smtp_attempts", "teams_sends",
            "telegram_sends", "production_state_writes", "status",
        )
    }, ensure_ascii=False, sort_keys=True))
    for failure in failures:
        print(f"FAIL {failure}")
    print(f"NEWS_CENSOR_SEMANTIC_FILTERS={report['status']}")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
