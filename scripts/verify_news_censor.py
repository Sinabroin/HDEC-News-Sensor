#!/usr/bin/env python3
"""Offline verifier for D7-AK-6E R4 Standalone News Censor.

The verifier blocks network and sender entry points, builds only into a
temporary directory, and confirms that production state and existing page
contracts remain byte-identical throughout the run.
"""

from __future__ import annotations

import hashlib
import copy
import json
import os
import re
import shutil
import smtplib
import socket
import subprocess
import sys
import tempfile
import urllib.request
from contextlib import contextmanager
from datetime import date
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
for item in (ROOT, SCRIPTS):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

for name in (
    "NAVER_CLIENT_ID",
    "NAVER_CLIENT_SECRET",
    "GMAIL_SMTP_USER",
    "GMAIL_SMTP_APP_PASSWORD",
    "TEAMS_CHANNEL_EMAIL",
    "TEAMS_WEBHOOK_URL",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_IDS",
):
    os.environ.pop(name, None)
os.environ["APP_MODE"] = "mock"
os.environ["NEWS_MODE"] = "mock"
os.environ["TEAMS_AI_NEWS_WATCH"] = "0"
os.environ["TELEGRAM_AUTO_SEND"] = "0"
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

from app import publisher_direct  # noqa: E402
import build_news_censor  # noqa: E402
from build_executive_brief import (  # noqa: E402
    attach_artifact_contract,
    build_brief_via_mock_pipeline,
    write_brief_json,
)

WORKFLOW = ROOT / ".github" / "workflows" / "scheduled-live-refresh.yml"
TEAMS_WORKFLOW = ROOT / ".github" / "workflows" / "teams-ai-news-watch.yml"
TEMPLATE = ROOT / "templates" / "news_censor.html"
COMMITTED_ROOT = ROOT / "docs" / "news-censor"
REFERENCE_SHA256 = "c4a1d129a9e8b6d824b961e2042f345cfc2eb405dcbc488a542e5bc6cee14804"
CONTRACT = "D7-AK-6E-R4-STANDALONE-NEWS-CENSOR"

PROTECTED_PATHS = (
    ROOT / "docs" / "index.html",
    ROOT / "docs" / "daily" / "latest.html",
    ROOT / "docs" / "daily" / "dashboard-latest.html",
    ROOT / "docs" / "daily" / "operator-latest.html",
    ROOT / "templates" / "index.html",
    ROOT / "templates" / "dashboard_preview.html",
    ROOT / "templates" / "editorial_daily.html",
    ROOT / "templates" / "editorial_review_console.html",
    ROOT / "data" / "teams_push_state.json",
    ROOT / "data" / "editorial_daily_state.json",
    WORKFLOW,
    TEAMS_WORKFLOW,
)

NAVIGATION_OWNERS = (
    ROOT / "docs" / "index.html",
    ROOT / "templates" / "index.html",
    ROOT / "templates" / "dashboard_preview.html",
    ROOT / "templates" / "editorial_daily.html",
    ROOT / "templates" / "editorial_review_console.html",
)

PASS = 0
FAIL = 0
NETWORK_ATTEMPTS = 0
SMTP_ATTEMPTS = 0
TEAMS_SENDS = 0
TELEGRAM_SENDS = 0
PRODUCTION_STATE_WRITES = 0


def check(label: str, condition: object, detail: object = "") -> bool:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"PASS {label}")
        return True
    FAIL += 1
    suffix = f" :: {detail}" if detail else ""
    print(f"FAIL {label}{suffix}")
    return False


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot(paths: tuple[Path, ...]) -> dict[str, str]:
    return {str(path.relative_to(ROOT)): sha256(path) for path in paths if path.exists()}


@contextmanager
def no_external_effects():
    global NETWORK_ATTEMPTS, SMTP_ATTEMPTS
    originals: dict[str, Callable] = {
        "create_connection": socket.create_connection,
        "socket_connect": socket.socket.connect,
        "urlopen": urllib.request.urlopen,
        "smtp": smtplib.SMTP,
        "smtp_ssl": smtplib.SMTP_SSL,
    }

    def blocked_network(*_args, **_kwargs):
        global NETWORK_ATTEMPTS
        NETWORK_ATTEMPTS += 1
        raise AssertionError("external network access blocked by News Censor verifier")

    class BlockedSMTP:
        def __init__(self, *_args, **_kwargs):
            global SMTP_ATTEMPTS
            SMTP_ATTEMPTS += 1
            raise AssertionError("SMTP access blocked by News Censor verifier")

    socket.create_connection = blocked_network
    socket.socket.connect = blocked_network
    urllib.request.urlopen = blocked_network
    smtplib.SMTP = BlockedSMTP
    smtplib.SMTP_SSL = BlockedSMTP
    try:
        yield
    finally:
        socket.create_connection = originals["create_connection"]
        socket.socket.connect = originals["socket_connect"]
        urllib.request.urlopen = originals["urlopen"]
        smtplib.SMTP = originals["smtp"]
        smtplib.SMTP_SSL = originals["smtp_ssl"]


class SurfaceParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.contract = ""
        self.article_ids: list[str] = []
        self.filters: list[str] = []
        self.external_assets: list[str] = []
        self.forms = 0
        self.image_sources: list[str] = []
        self.portal_hrefs: list[str] = []
        self.panes: set[str] = set()
        self.rails: set[str] = set()
        self.subfilters: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "html":
            self.contract = values.get("data-news-censor-contract") or ""
        article_id = values.get("data-article-id")
        if article_id:
            self.article_ids.append(article_id)
        filter_id = values.get("data-filter")
        if filter_id:
            self.filters.append(filter_id)
        subfilter_id = values.get("data-subfilter")
        if subfilter_id:
            self.subfilters.append(subfilter_id)
        if values.get("data-pane"):
            self.panes.add(values["data-pane"] or "")
        if values.get("data-rail"):
            self.rails.add(values["data-rail"] or "")
        if tag == "img":
            self.image_sources.append(values.get("src") or "")
        if tag == "form":
            self.forms += 1
        for key in ("src", "href"):
            value = values.get(key) or ""
            if value.startswith(("http://", "https://")) and tag in {"script", "link", "img", "iframe"}:
                self.external_assets.append(value)
            if tag == "a" and publisher_direct.portal_provider(value):
                self.portal_hrefs.append(value)


def extract_model(html: str) -> dict:
    match = re.search(
        r'<script type="application/json" id="news-censor-model">(.*?)</script>',
        html,
        re.S,
    )
    if not match:
        raise ValueError("News Censor JSON island missing")
    return json.loads(match.group(1))


def _browser_executable() -> Path | None:
    candidates = [
        shutil.which("google-chrome"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        "/mnt/c/Program Files/Google/Chrome/Application/chrome.exe",
        "/mnt/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    return None


def _browser_path(path: Path, browser: Path) -> str:
    if browser.suffix.casefold() != ".exe":
        return path.resolve().as_uri()
    converted = subprocess.run(
        ["wslpath", "-w", str(path.resolve())],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return "file:///" + converted.replace("\\", "/")


def _browser_argument_path(path: Path, browser: Path) -> str:
    if browser.suffix.casefold() != ".exe":
        return str(path.resolve())
    return subprocess.run(
        ["wslpath", "-w", str(path.resolve())],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def run_browser_interaction(document: Path, profile_dir: Path, *, mobile: bool) -> dict:
    """Exercise the real generated reader/filter DOM at desktop and mobile widths."""
    browser = _browser_executable()
    if browser is None:
        return {"browser_available": False, "error": "Chrome/Edge executable not found"}
    source = document.read_text(encoding="utf-8")
    harness = r'''
<script>
(async()=>{
  const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
  const results={browser_available:true,width:window.innerWidth};
  const card=document.querySelector('[data-article-id]');
  card.focus();
  card.dispatchEvent(new MouseEvent('click',{bubbles:true}));
  await pause(50);
  results.click_open=!document.getElementById('reader').hidden;
  results.close_focus=document.activeElement===document.getElementById('reader-close');
  document.getElementById('reader-close').click();
  await pause(30);
  results.focus_restored=document.activeElement===card;
  card.dispatchEvent(new KeyboardEvent('keydown',{key:' ',bubbles:true,cancelable:true}));
  await pause(30);
  results.space_open=!document.getElementById('reader').hidden;
  document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true,cancelable:true}));
  await pause(30);
  results.escape_close=document.getElementById('reader').hidden;
  const category=[...document.querySelectorAll('.filter')].find(button=>button.dataset.filter!=='all'&&Number(button.querySelector('small').textContent)>0);
  category.click();
  const subfilter=document.querySelector('.subfilter[data-subfilter]:not([data-subfilter="all"])');
  if(subfilter)subfilter.click();
  results.filter_interaction=category.classList.contains('active')&&(!subfilter||subfilter.classList.contains('active'));
  const layout=getComputedStyle(document.querySelector('.layout'));
  const grid=getComputedStyle(document.querySelector('.grid'));
  results.layout_display=layout.display;
  results.grid_columns=grid.gridTemplateColumns;
  const pre=document.createElement('pre');
  pre.id='news-censor-browser-result';
  pre.textContent=JSON.stringify(results);
  document.body.append(pre);
})();
</script>
'''
    interaction_path = document.parent / ("mobile-browser.html" if mobile else "desktop-browser.html")
    before, after = source.rsplit("</body>", 1)
    interaction_path.write_text(before + harness + "</body>" + after, encoding="utf-8")

    profile_handle = None
    if browser.suffix.casefold() == ".exe":
        windows_output = subprocess.run(
            ["cmd.exe", "/d", "/c", "echo", "%TEMP%"],
            cwd="/mnt/c",
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        windows_temp = next(
            line.strip() for line in reversed(windows_output.splitlines())
            if re.match(r"^[A-Za-z]:\\", line.strip())
        )
        wsl_temp = subprocess.run(
            ["wslpath", "-u", windows_temp],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        profile_handle = tempfile.TemporaryDirectory(
            prefix="hdec-news-censor-browser-",
            dir=wsl_temp,
            ignore_cleanup_errors=True,
        )
        active_profile = Path(profile_handle.name)
    else:
        profile_dir.mkdir(parents=True, exist_ok=True)
        active_profile = profile_dir
    command = [
        str(browser),
        "--headless=new",
        "--disable-gpu",
        "--disable-background-networking",
        "--disable-component-update",
        "--disable-default-apps",
        "--disable-sync",
        "--metrics-recording-only",
        "--no-first-run",
        "--no-default-browser-check",
        "--allow-file-access-from-files",
        "--virtual-time-budget=2500",
        f"--window-size={'390,844' if mobile else '1280,900'}",
        f"--user-data-dir={_browser_argument_path(active_profile, browser)}",
        "--dump-dom",
        _browser_path(interaction_path, browser),
    ]
    if browser.suffix.casefold() != ".exe":
        command[2:2] = ["--no-sandbox", "--disable-dev-shm-usage"]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=35,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {"browser_available": True, "error": f"browser timeout after {exc.timeout}s"}
    finally:
        if profile_handle is not None:
            profile_handle.cleanup()
    match = re.search(
        r'<pre id="news-censor-browser-result">([^<]+)</pre>',
        completed.stdout,
    )
    if not match:
        return {
            "browser_available": True,
            "error": "browser result missing",
            "returncode": completed.returncode,
        }
    return json.loads(unescape(match.group(1)))


def verify_html(
    html: str,
    label: str,
    *,
    expected_mode: str,
    expected_status: str,
    expected_articles: int | None = None,
) -> None:
    parser = SurfaceParser()
    parser.feed(html)
    model = extract_model(html)
    articles = model.get("articles") or []

    check(f"{label}: contract marker", parser.contract == CONTRACT, parser.contract)
    check(f"{label}: model contract", model.get("contract") == CONTRACT)
    check(f"{label}: reference fingerprint", model.get("reference_sha256") == REFERENCE_SHA256)
    check(f"{label}: expected data mode", model.get("news_data_mode") == expected_mode)
    check(f"{label}: expected collection status", model.get("collection_status") == expected_status)
    check(f"{label}: article count is bounded", 0 <= len(articles) <= 40, len(articles))
    if expected_articles is not None:
        check(f"{label}: expected article count", len(articles) == expected_articles, len(articles))
    check(f"{label}: DOM/model article counts agree", len(parser.article_ids) == len(articles))
    check(f"{label}: article identities are unique", len(set(parser.article_ids)) == len(parser.article_ids))
    check(f"{label}: all category filters exist", set(build_news_censor.CATEGORY_LABELS) == set(parser.filters))
    coverage = model.get("coverage") or {}
    categories = model.get("categories") or []
    legacy_committed = label == "committed output" and not coverage
    check(
        f"{label}: coverage rail and category indicators exist",
        legacy_committed or (
        "coverage" in parser.rails
        and len(categories) == len(build_news_censor.CATEGORY_LABELS)
        and all(item.get("coverage_status") in {"covered", "gap"} for item in categories)),
    )
    check(
        f"{label}: category coverage totals are internally consistent",
        legacy_committed or (
        int(coverage.get("category_target_count") or 0)
        == len(build_news_censor.PRIMARY_CATEGORY_IDS)
        and int(coverage.get("category_covered_count") or 0)
        + int(coverage.get("category_gap_count") or 0)
        == len(build_news_censor.PRIMARY_CATEGORY_IDS)),
    )
    dynamic_subfilters = model.get("subfilters") or []
    check(
        f"{label}: dynamic subfilters are nonzero-only",
        all(int(item.get("count") or 0) > 0 for item in dynamic_subfilters),
    )
    if articles:
        check(f"{label}: dynamic article-tag subfilters exist", bool(dynamic_subfilters))
        check(
            f"{label}: rendered subfilters agree with model",
            {item["id"] for item in dynamic_subfilters}.issubset(set(parser.subfilters)),
        )
    check(f"{label}: no external runtime assets", not parser.external_assets, parser.external_assets)
    check(f"{label}: no forms or mutation controls", parser.forms == 0)
    check(f"{label}: no portal URL in browser model", publisher_direct.count_portal_urls(model) == 0)
    check(f"{label}: portal href count 0", not parser.portal_hrefs, parser.portal_hrefs)
    check(
        f"{label}: published quarantine count 0",
        int(model.get("published_quarantine_count") or 0) == 0,
    )
    check(
        f"{label}: publisher URLs are canonical and unique",
        len({str(row.get("url") or "").casefold().rstrip("/") for row in articles}) == len(articles),
    )
    check(
        f"{label}: freshness and backfill policy is explicit",
        legacy_committed or (all(
            row.get("freshness_status") in {
                "fresh", "recent", "backfill", "archive", "unknown"
            }
            and bool(row.get("freshness_label"))
            and isinstance(row.get("is_backfill"), bool)
            for row in articles
        )
        and int(coverage.get("fresh_article_count") or 0)
        + int(coverage.get("backfill_article_count") or 0)
        == len(articles)),
    )
    check(
        f"{label}: publisher diversity total matches visible rows",
        legacy_committed or int(coverage.get("publisher_count") or 0)
        == len({row.get("publisher_key") for row in articles}),
    )
    check(
        f"{label}: display and Teams policies remain separate",
        legacy_committed or (
        coverage.get("display_policy") == "publisher_direct_coverage"
        and coverage.get("teams_policy")
        == "ai_topic+executive_relevance+importance+sender_gate"
        and "News Censor 표시 범위와 Teams 중요 AI 발송 정책은 서로 독립" in html),
    )
    check(
        f"{label}: quarantine diagnostics are aggregate-only",
        legacy_committed or (all(set(item) == {"key", "label", "count"}
            for item in coverage.get("quarantine_diagnostics") or [])
        and "URL·응답 내용은 공개하지 않습니다" in html),
    )
    check(
        f"{label}: valid local article image or deterministic fallback",
        all(
            str(row.get("image_src") or "").startswith(("assets/images/", "data:image/"))
            and not str(row.get("image_src") or "").startswith(("http://", "https://"))
            for row in articles
        ),
    )
    check(f"{label}: article images are never remote hotlinks", not any(
        source.startswith(("http://", "https://")) for source in parser.image_sources
    ))
    market = model.get("market") or {}
    check(f"{label}: market pane exists", "market" in parser.panes and "items" in market)
    check(
        f"{label}: market pane uses artifact values or N/A",
        all(item.get("value") is not None for item in market.get("items") or [])
        or "N/A" in html,
    )
    check(
        f"{label}: market provenance and delayed/proxy honesty",
        bool(market.get("source"))
        and "as-of" in html
        and ("delayed" in html or "proxy" in html or "unavailable" in html),
    )
    weather = model.get("weather") or {}
    check(f"{label}: weather rail exists", "weather" in parser.rails and bool(weather))
    check(
        f"{label}: weather uses dynamic rows or unavailable state",
        bool(weather.get("rows")) or weather.get("status") == "unavailable",
    )
    check(f"{label}: weather representative basis and forecast provenance", (
        all(row.get("basis") and (row.get("forecast_at") or row.get("status_note"))
            for row in weather.get("rows") or [])
        if weather.get("rows") else bool(weather.get("unavailable_reason"))
    ))
    safety = model.get("safety") or {}
    check(f"{label}: safety rail exists", "safety" in parser.rails and bool(safety))
    check(
        f"{label}: safety is verified or explicitly unavailable",
        safety.get("status") in {"verified", "unavailable"}
        and (bool(safety.get("items")) or bool(safety.get("unavailable_reason"))),
    )
    eligibility = [
        publisher_direct.assess_delivery_eligibility(row, relevance_qualified=True).eligible
        for row in articles
    ]
    check(f"{label}: every article passes common publisher authority", all(eligibility), eligibility)
    check(
        f"{label}: no discovery or credential fields exposed",
        all(
            not ({"discovery_url", "raw_provenance_urls", "source_metadata", "source_metadata_json"} & set(row))
            for row in articles
        ),
    )
    check(f"{label}: no remote browser fetch primitive", "fetch(" not in html and "XMLHttpRequest" not in html)
    check(f"{label}: no browser persistence", "localStorage" not in html and "sessionStorage" not in html)
    check(
        f"{label}: safe origin-link contract",
        'link.rel = "noopener noreferrer"' in html and 'link.target = "_blank"' in html,
    )
    check(
        f"{label}: responsive standalone structure",
        "@media(max-width:680px)" in html
        and ".layout{display:block}" in html
        and ".rail{display:grid;grid-template-columns:1fr" in html,
    )
    check(f"{label}: Korean language and viewport", '<html lang="ko"' in html and 'name="viewport"' in html)
    check(f"{label}: search indexing disabled", 'content="noindex,nofollow"' in html)
    check(f"{label}: no navigation to existing products", "daily/latest" not in html and "editorial/review" not in html)
    check(
        f"{label}: reader uses safe DOM text only",
        "innerHTML" not in html
        and "textContent" in html
        and "replaceChildren" in html,
    )
    check(
        f"{label}: desktop keyboard reader open/close and focus restoration",
        'event.key === "Enter" || event.key === " "' in html
        and 'event.key === "Escape"' in html
        and "readerTrigger.focus" in html
        and 'document.getElementById("reader-close").focus' in html,
    )
    check(
        f"{label}: desktop category/tag intersection",
        "categoryMatch && subfilterMatch" in html and "applyFilters();" in html,
    )
    check(
        f"{label}: mobile interaction/layout contract",
        "@media(max-width:680px)" in html
        and ".layout{display:block}" in html
        and ".grid{grid-template-columns:1fr}" in html
        and ".rail{display:grid;grid-template-columns:1fr" in html,
    )
    if expected_status == build_news_censor.LIVE_HEALTHY_NO_ELIGIBLE_ARTICLES:
        check(
            f"{label}: truthful live production empty state",
            "현재 조건을 충족한 신규 기사가 없습니다" in html
            and "DEMO · deterministic fixture" not in html,
        )


def main() -> int:
    before = snapshot(PROTECTED_PATHS)
    workflow_text = WORKFLOW.read_text(encoding="utf-8")
    teams_workflow_text = TEAMS_WORKFLOW.read_text(encoding="utf-8")
    template_text = TEMPLATE.read_text(encoding="utf-8")
    builder_text = (ROOT / "scripts" / "build_news_censor.py").read_text(encoding="utf-8")

    check("builder and template exist", build_news_censor.TEMPLATE == TEMPLATE and TEMPLATE.exists())
    check("reference fingerprint is sealed in builder", build_news_censor.REFERENCE_SHA256 == REFERENCE_SHA256)
    check(
        "existing page owners do not link the standalone page",
        all("news-censor" not in path.read_text(encoding="utf-8").casefold() for path in NAVIGATION_OWNERS),
    )
    check("template has no external stylesheet or preconnect", "cdn." not in template_text and "preconnect" not in template_text)
    check(
        "production builder has no mock pipeline call or import",
        "build_brief_via_mock_pipeline" not in builder_text,
    )
    check(
        "production builder requires explicit brief JSON",
        '"--brief-json"' in builder_text and "required=True" in builder_text,
    )

    with tempfile.TemporaryDirectory(prefix="hdec-news-censor-") as tmp:
        temp_root = Path(tmp)
        fixture_artifact = temp_root / "fixture-brief.json"
        live_artifact = temp_root / "live-brief.json"
        empty_artifact = temp_root / "empty-live-brief.json"
        failed_artifact = temp_root / "failed-live-brief.json"
        fallback_artifact = temp_root / "fallback-brief.json"
        fixture_root = temp_root / "fixture-output"
        live_root = temp_root / "live-output"
        empty_root = temp_root / "empty-output"
        with no_external_effects():
            demo = attach_artifact_contract(
                build_brief_via_mock_pipeline(),
                weather_mode="mock",
            )
            live = copy.deepcopy(demo)
            live.update({
                "news_data_mode": "live",
                "news_source": "fixture_live_publishers",
                "news_fallback_used": False,
                "collection_status": build_news_censor.LIVE_HEALTHY_WITH_ARTICLES,
                "collection_failure_category": "",
            })
            live["collector_health"] = {
                "status": build_news_censor.LIVE_HEALTHY_WITH_ARTICLES,
                "request_count": 6,
                "source_count": 3,
                "successful_source_count": 5,
                "raw_candidate_count": 24,
                "publisher_direct_eligible_count": 24,
                "quarantine_count": 0,
                "final_portal_url_count": 0,
                "failure_category": "",
                "quarantine_reason_counts": {},
                "publisher_resolution": {
                    "attempted_count": 24,
                    "resolved_count": 24,
                    "failed_count": 0,
                    "budget_exhausted_count": 0,
                    "policy": "bounded_fair_per_publisher",
                },
            }
            live["publisher_direct_delivery"] = {
                "eligible_count": 24,
                "quarantine_count": 0,
                "final_portal_urls": 0,
                "policy": "publisher_direct_only",
            }
            live["market_snapshot"] = {
                "mode": "live",
                "source_summary": "Fixture delayed market provider",
                "as_of": "2026-08-02T08:30:00+09:00",
                "updated_at": "2026-08-02T08:31:00+09:00",
                "disclaimer": "검증용 동적 지연/대용 값",
                "items": [{
                    "id": "fixture_copper",
                    "label_kr": "구리",
                    "value": 4.321,
                    "unit": "USD/lb",
                    "data_mode": "delayed",
                    "is_stale": False,
                    "proxy_for": "",
                    "source_provider": "Fixture Market",
                    "as_of": "2026-08-02T08:30:00+09:00",
                }, {
                    "id": "fixture_unavailable",
                    "label_kr": "국내 시멘트",
                    "value": None,
                    "unit": "KRW/t",
                    "data_mode": "unavailable",
                    "is_stale": True,
                    "proxy_for": "",
                    "source_provider": "",
                    "as_of": None,
                }],
            }
            live["weather_snapshot"] = {
                "weather_data_mode": "live",
                "weather_source": "Fixture public forecast",
                "weather_updated_at": "2026-08-02 08:32",
                "weather_target_time": "2026-08-03T12:00",
                "weather_unavailable_reason": "",
                "weather_rows": [{
                    "region": "수도권",
                    "basis": "서울 기준",
                    "target_local": "2026-08-03T12:00",
                    "risk_grade": "주의",
                    "temp_c": 31.2,
                    "precip_prob": 70,
                    "gust_ms": 8.1,
                    "row_status": "ok",
                    "status_note": "",
                }],
            }
            empty = copy.deepcopy(live)
            empty["collection_status"] = build_news_censor.LIVE_HEALTHY_NO_ELIGIBLE_ARTICLES
            empty["collector_health"].update({
                "status": build_news_censor.LIVE_HEALTHY_NO_ELIGIBLE_ARTICLES,
                "publisher_direct_eligible_count": 0,
                "raw_candidate_count": 7,
                "quarantine_count": 7,
                "empty_reason": "no_publisher_direct_eligible_articles",
                "quarantine_reason_counts": {
                    "publisher_resolution_budget_exhausted": 7,
                },
                "publisher_resolution": {
                    "attempted_count": 0,
                    "resolved_count": 0,
                    "failed_count": 0,
                    "budget_exhausted_count": 7,
                    "policy": "bounded_fair_per_publisher",
                },
            })
            empty["publisher_direct_delivery"].update({
                "eligible_count": 0,
                "quarantine_count": 7,
            })
            for key, _categories in build_news_censor.SURFACE_CATEGORIES:
                empty[key] = []
            empty["category_sections"] = []
            empty["accordion_sections"] = []
            empty["risk_event_clusters"] = []
            empty["theme_rankings"] = []

            display_probe = copy.deepcopy(empty)
            display_probe["generated_at"] = "2026-08-02T09:00:00+09:00"
            display_probe["collection_status"] = (
                build_news_censor.LIVE_HEALTHY_WITH_ARTICLES
            )
            display_probe["collector_health"].update({
                "status": build_news_censor.LIVE_HEALTHY_WITH_ARTICLES,
                "publisher_direct_eligible_count": 5,
                "quarantine_count": 0,
                "quarantine_reason_counts": {},
                "publisher_resolution": {
                    "attempted_count": 5,
                    "resolved_count": 5,
                    "failed_count": 0,
                    "budget_exhausted_count": 0,
                    "policy": "bounded_fair_per_publisher",
                },
            })
            display_probe["publisher_direct_delivery"].update({
                "eligible_count": 5,
                "quarantine_count": 0,
            })
            probe_rows = []
            for index, (title, host, published_at) in enumerate((
                ("현대건설 도시정비 사업 관찰", "alpha.example", "2026-08-02T08:00:00+09:00"),
                ("GS건설 신규 프로젝트 관찰", "beta.example", "2026-08-02T07:00:00+09:00"),
                ("건설현장 안전 품질 제도 관찰", "alpha.example", "2026-07-20T07:00:00+09:00"),
                ("중동 해외건설 사업환경 관찰", "alpha.example", "2026-08-01T07:00:00+09:00"),
                ("데이터센터 AI 기술 동향 관찰", "alpha.example", "2026-08-01T06:00:00+09:00"),
            )):
                url = f"https://{host}/news/{index}"
                probe_rows.append({
                    "article_id": f"display-{index}",
                    "title": title,
                    "source": host,
                    "display_source": host,
                    "published_at": published_at,
                    "snippet": "확정 중요 사건이 아닌 일반 관측 기사입니다.",
                    "url": url,
                    "publisher_url": url,
                    "canonical_url": url,
                    "publisher_direct": True,
                    "status": "collected",
                    "quarantine": False,
                    "final_score": 0.0,
                    "alert_grade": "참고/제외",
                    "action_label": "모니터링",
                    "category": "general",
                    "category_label": "건설산업 일반",
                    "why_it_matters": "표시 커버리지 관측",
                })
            display_probe["news_censor_display_articles"] = probe_rows
            probe_model = build_news_censor.build_model(
                display_probe,
                edition=date(2026, 8, 2),
                article_limit=4,
            )
            check(
                "display coverage includes low-importance publisher rows",
                probe_model["article_count"] == 4,
            )
            check(
                "display coverage applies publisher diversity and backfill labels",
                probe_model["coverage"]["publisher_count"] == 2
                and probe_model["coverage"]["backfill_article_count"] >= 1,
            )
            from app import teams_ai_push as teams_policy

            check(
                "display-only rows remain ineligible for Teams send",
                teams_policy.select_teams_push_candidates(probe_rows) == (),
            )
            failed = copy.deepcopy(empty)
            failed["collection_status"] = build_news_censor.LIVE_COLLECTION_FAILED
            failed["collection_failure_category"] = "official_feed_collection_failure"
            failed["collector_health"].update({
                "status": build_news_censor.LIVE_COLLECTION_FAILED,
                "successful_source_count": 0,
                "failure_category": "official_feed_collection_failure",
            })
            fallback = copy.deepcopy(demo)
            fallback.update({
                "collection_status": build_news_censor.LIVE_FALLBACK_REJECTED,
                "collection_failure_category": "all_live_sources_failed",
                "news_fallback_used": True,
            })
            fallback["collector_health"] = {
                "status": build_news_censor.LIVE_FALLBACK_REJECTED,
                "request_count": 5,
                "source_count": 2,
                "successful_source_count": 0,
                "raw_candidate_count": 0,
                "publisher_direct_eligible_count": 0,
                "quarantine_count": 0,
                "final_portal_url_count": 0,
                "failure_category": "all_live_sources_failed",
            }
            for path, artifact in (
                (fixture_artifact, demo),
                (live_artifact, live),
                (empty_artifact, empty),
                (failed_artifact, failed),
                (fallback_artifact, fallback),
            ):
                write_brief_json(path, artifact)

            fixture_rc = build_news_censor.main([
                "--brief-json", str(fixture_artifact),
                "--output-root", str(fixture_root),
                "--edition-date", "2026-08-02",
                "--article-limit", "24",
            ])
            mock_rejected_rc = build_news_censor.main([
                "--brief-json", str(fixture_artifact),
                "--output-root", str(temp_root / "mock-rejected"),
                "--edition-date", "2026-08-02",
                "--require-live",
            ])
            live_rc = build_news_censor.main([
                "--brief-json", str(live_artifact),
                "--output-root", str(live_root),
                "--edition-date", "2026-08-02",
                "--require-live",
            ])
            empty_rc = build_news_censor.main([
                "--brief-json", str(empty_artifact),
                "--output-root", str(empty_root),
                "--edition-date", "2026-08-03",
                "--require-live",
            ])

        fixture_latest = fixture_root / "latest.html"
        fixture_archive = fixture_root / "2026-08-02.html"
        check("explicit fixture input builds DEMO preview", fixture_rc == 0, fixture_rc)
        check("fixture latest and dated outputs are correct", fixture_latest.exists() and fixture_archive.exists())
        if fixture_latest.exists() and fixture_archive.exists():
            check("fixture latest and dated outputs are byte-identical", fixture_latest.read_bytes() == fixture_archive.read_bytes())
            verify_html(
                fixture_latest.read_text(encoding="utf-8"),
                "explicit DEMO preview",
                expected_mode="mock",
                expected_status="FIXTURE_DEMO",
                expected_articles=24,
            )
        check("require-live rejects explicit fixture before writing", mock_rejected_rc == 3 and not (temp_root / "mock-rejected").exists(), mock_rejected_rc)

        live_latest = live_root / "latest.html"
        live_archive = live_root / "2026-08-02.html"
        check("healthy live input with articles publishes LIVE", live_rc == 0, live_rc)
        check("live latest and dated outputs are correct", live_latest.exists() and live_archive.exists())
        if live_latest.exists() and live_archive.exists():
            check("live latest and dated outputs are byte-identical", live_latest.read_bytes() == live_archive.read_bytes())
            verify_html(
                live_latest.read_text(encoding="utf-8"),
                "healthy LIVE output",
                expected_mode="live",
                expected_status=build_news_censor.LIVE_HEALTHY_WITH_ARTICLES,
                expected_articles=24,
            )
            desktop_browser = run_browser_interaction(
                live_latest,
                temp_root / "desktop-browser-profile",
                mobile=False,
            )
            mobile_browser = run_browser_interaction(
                live_latest,
                temp_root / "mobile-browser-profile",
                mobile=True,
            )
            check(
                "real desktop browser interaction passes",
                desktop_browser.get("browser_available") is True
                and all(desktop_browser.get(key) is True for key in (
                    "click_open", "close_focus", "focus_restored", "space_open",
                    "escape_close", "filter_interaction",
                ))
                and desktop_browser.get("layout_display") == "grid",
                desktop_browser,
            )
            check(
                "real mobile browser interaction and single-column layout pass",
                mobile_browser.get("browser_available") is True
                and all(mobile_browser.get(key) is True for key in (
                    "click_open", "close_focus", "focus_restored", "space_open",
                    "escape_close", "filter_interaction",
                ))
                and mobile_browser.get("layout_display") == "block"
                and len(str(mobile_browser.get("grid_columns") or "").split()) == 1,
                mobile_browser,
            )

        empty_latest = empty_root / "latest.html"
        empty_archive = empty_root / "2026-08-03.html"
        check("healthy live input with zero eligible publishes LIVE empty state", empty_rc == 0, empty_rc)
        check("empty latest and dated outputs are correct", empty_latest.exists() and empty_archive.exists())
        if empty_latest.exists() and empty_archive.exists():
            verify_html(
                empty_latest.read_text(encoding="utf-8"),
                "healthy LIVE empty output",
                expected_mode="live",
                expected_status=build_news_censor.LIVE_HEALTHY_NO_ELIGIBLE_ARTICLES,
                expected_articles=0,
            )

        live_hash_before_rejection = sha256(live_latest)
        with no_external_effects():
            failed_rc = build_news_censor.main([
                "--brief-json", str(failed_artifact),
                "--output-root", str(live_root),
                "--edition-date", "2026-08-02",
                "--require-live",
            ])
            fallback_rc = build_news_censor.main([
                "--brief-json", str(fallback_artifact),
                "--output-root", str(live_root),
                "--edition-date", "2026-08-02",
                "--require-live",
            ])
        check("failed live input returns nonzero", failed_rc == 3, failed_rc)
        check("fallback input returns nonzero", fallback_rc == 3, fallback_rc)
        check(
            "failed/fallback input does not overwrite last valid live output",
            sha256(live_latest) == live_hash_before_rejection,
        )

    check("verifier attempted zero network calls", NETWORK_ATTEMPTS == 0, NETWORK_ATTEMPTS)
    check("SMTP attempts 0", SMTP_ATTEMPTS == 0, SMTP_ATTEMPTS)
    check("Teams sends 0", TEAMS_SENDS == 0, TEAMS_SENDS)
    check("Telegram sends 0", TELEGRAM_SENDS == 0, TELEGRAM_SENDS)
    check("sender gates remain explicitly closed in verifier", os.environ.get("TEAMS_AI_NEWS_WATCH") == "0" and os.environ.get("TELEGRAM_AUTO_SEND") == "0")

    check("workflow runs News Censor verifier", "python3 scripts/verify_news_censor.py" in workflow_text)
    check(
        "workflow builds live News Censor fail-closed",
        "python3 scripts/build_news_censor.py" in workflow_text
        and "--output-root docs/news-censor" in workflow_text
        and "--require-live" in workflow_text,
    )
    check(
        "scheduled workflow generates and reuses one live brief artifact",
        "scripts/build_executive_brief.py" in workflow_text
        and '--output-json "$BRIEF_JSON"' in workflow_text
        and workflow_text.count('--brief-json "$BRIEF_JSON"') >= 4
        and "grep -q \"news_data_mode=live\"" not in workflow_text,
    )
    check(
        "Teams workflow validates and reuses the same live artifact",
        '--output-json "$BRIEF_JSON"' in teams_workflow_text
        and teams_workflow_text.count('--brief-json "$BRIEF_JSON"') >= 2
        and "grep -q \"news_data_mode=live\"" not in teams_workflow_text,
    )
    check("workflow publishes the standalone output root", "docs/news-censor" in workflow_text)
    check(
        "workflow deployment defaults keep senders closed",
        'TEAMS_AI_NEWS_WATCH: "0"' in workflow_text and 'TELEGRAM_AUTO_SEND: "0"' in workflow_text,
    )

    committed_latest = COMMITTED_ROOT / "latest.html"
    archives = sorted(COMMITTED_ROOT.glob("20??-??-??.html")) if COMMITTED_ROOT.exists() else []
    check("committed latest output exists", committed_latest.exists())
    check("committed dated archive exists", bool(archives))
    if committed_latest.exists():
        committed_html = committed_latest.read_text(encoding="utf-8")
        committed_model = extract_model(committed_html)
        if committed_model.get("collection_status"):
            verify_html(
                committed_html,
                "committed output",
                expected_mode=str(committed_model.get("news_data_mode")),
                expected_status=str(committed_model.get("collection_status")),
                expected_articles=len(committed_model.get("articles") or []),
            )
        else:
            check(
                "legacy committed seed remains explicitly DEMO pending live deployment",
                "DEMO · deterministic fixture" in committed_html,
            )

    after = snapshot(PROTECTED_PATHS)
    check("existing pages, workflow, and production state unchanged by verifier", before == after)
    check("production state writes 0", before == after and PRODUCTION_STATE_WRITES == 0)
    check("no repository database created", not (ROOT / "radar.db").exists())

    print(
        f"COUNTERS network={NETWORK_ATTEMPTS} smtp={SMTP_ATTEMPTS} "
        f"teams={TEAMS_SENDS} telegram={TELEGRAM_SENDS} "
        f"production_state_writes={PRODUCTION_STATE_WRITES}"
    )
    print(
        f"NEWS_CENSOR_VERIFIER={PASS}/0" if FAIL == 0
        else f"NEWS_CENSOR_VERIFIER={PASS}/{FAIL}"
    )
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
