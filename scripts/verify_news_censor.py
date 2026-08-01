#!/usr/bin/env python3
"""Offline verifier for D7-AK-6E R4 Standalone News Censor.

The verifier blocks network and sender entry points, builds only into a
temporary directory, and confirms that production state and existing page
contracts remain byte-identical throughout the run.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import smtplib
import socket
import sys
import tempfile
import urllib.request
from contextlib import contextmanager
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

WORKFLOW = ROOT / ".github" / "workflows" / "scheduled-live-refresh.yml"
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
SEND_ATTEMPTS = 0


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
    global NETWORK_ATTEMPTS, SEND_ATTEMPTS
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
            global SEND_ATTEMPTS
            SEND_ATTEMPTS += 1
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
        if tag == "form":
            self.forms += 1
        for key in ("src", "href"):
            value = values.get(key) or ""
            if value.startswith(("http://", "https://")) and tag in {"script", "link", "img", "iframe"}:
                self.external_assets.append(value)


def extract_model(html: str) -> dict:
    match = re.search(
        r'<script type="application/json" id="news-censor-model">(.*?)</script>',
        html,
        re.S,
    )
    if not match:
        raise ValueError("News Censor JSON island missing")
    return json.loads(match.group(1))


def verify_html(html: str, label: str) -> None:
    parser = SurfaceParser()
    parser.feed(html)
    model = extract_model(html)
    articles = model.get("articles") or []

    check(f"{label}: contract marker", parser.contract == CONTRACT, parser.contract)
    check(f"{label}: model contract", model.get("contract") == CONTRACT)
    check(f"{label}: reference fingerprint", model.get("reference_sha256") == REFERENCE_SHA256)
    check(f"{label}: article count is bounded and nonzero", 1 <= len(articles) <= 40, len(articles))
    check(f"{label}: DOM/model article counts agree", len(parser.article_ids) == len(articles))
    check(f"{label}: article identities are unique", len(set(parser.article_ids)) == len(parser.article_ids))
    check(f"{label}: all category filters exist", set(build_news_censor.CATEGORY_LABELS) == set(parser.filters))
    check(f"{label}: no external runtime assets", not parser.external_assets, parser.external_assets)
    check(f"{label}: no forms or mutation controls", parser.forms == 0)
    check(f"{label}: no portal URL in browser model", publisher_direct.count_portal_urls(model) == 0)
    check(
        f"{label}: publisher URLs are canonical and unique",
        len({str(row.get("url") or "").casefold().rstrip("/") for row in articles}) == len(articles),
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


def main() -> int:
    before = snapshot(PROTECTED_PATHS)
    workflow_text = WORKFLOW.read_text(encoding="utf-8")
    template_text = TEMPLATE.read_text(encoding="utf-8")

    check("builder and template exist", build_news_censor.TEMPLATE == TEMPLATE and TEMPLATE.exists())
    check("reference fingerprint is sealed in builder", build_news_censor.REFERENCE_SHA256 == REFERENCE_SHA256)
    check(
        "existing page owners do not link the standalone page",
        all("news-censor" not in path.read_text(encoding="utf-8").casefold() for path in NAVIGATION_OWNERS),
    )
    check("template has no external stylesheet or preconnect", "cdn." not in template_text and "preconnect" not in template_text)

    with tempfile.TemporaryDirectory(prefix="hdec-news-censor-") as tmp:
        output_root = Path(tmp) / "news-censor"
        rejected_root = Path(tmp) / "require-live-rejected"
        with no_external_effects():
            rc = build_news_censor.main([
                "--output-root", str(output_root),
                "--edition-date", "2026-08-02",
                "--article-limit", "24",
            ])
            rejected_rc = build_news_censor.main([
                "--output-root", str(rejected_root),
                "--edition-date", "2026-08-02",
                "--require-live",
            ])
        latest = output_root / "latest.html"
        archive = output_root / "2026-08-02.html"
        check("offline fixture build succeeds", rc == 0, rc)
        check("latest and dated archive are both produced", latest.exists() and archive.exists())
        if latest.exists() and archive.exists():
            check("latest and dated archive are byte-identical", latest.read_bytes() == archive.read_bytes())
            verify_html(latest.read_text(encoding="utf-8"), "temporary output")
        check("require-live rejects mock before writing", rejected_rc == 3 and not rejected_root.exists(), rejected_rc)

    check("verifier attempted zero network calls", NETWORK_ATTEMPTS == 0, NETWORK_ATTEMPTS)
    check("verifier attempted zero sends", SEND_ATTEMPTS == 0, SEND_ATTEMPTS)
    check("sender gates remain explicitly closed in verifier", os.environ.get("TEAMS_AI_NEWS_WATCH") == "0" and os.environ.get("TELEGRAM_AUTO_SEND") == "0")

    check("workflow runs News Censor verifier", "python3 scripts/verify_news_censor.py" in workflow_text)
    check(
        "workflow builds live News Censor fail-closed",
        "python3 scripts/build_news_censor.py" in workflow_text
        and "--output-root docs/news-censor" in workflow_text
        and "--require-live" in workflow_text,
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
        verify_html(committed_latest.read_text(encoding="utf-8"), "committed output")

    after = snapshot(PROTECTED_PATHS)
    check("existing pages, workflow, and production state unchanged by verifier", before == after)
    check("no repository database created", not (ROOT / "radar.db").exists())

    print(
        f"NEWS_CENSOR_VERIFIER={PASS}/0" if FAIL == 0
        else f"NEWS_CENSOR_VERIFIER={PASS}/{FAIL}"
    )
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
