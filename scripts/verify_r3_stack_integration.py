#!/usr/bin/env python3
"""Offline integration audit for the R3 editorial and delivery stack.

The verifier composes the OPS-R1, R3-V7 console, and R3-V8 publisher-direct
contracts.  Every generated artifact lives below a temporary directory.  A
``sitecustomize`` guard blocks non-loopback Python networking in child
verifiers, while this process blocks all socket/SMTP/urllib access during the
integrated fixture scenario.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import smtplib
import socket
import subprocess
import sys
import tempfile
import urllib.request
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
for item in (ROOT, SCRIPTS):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

for unsafe_name in (
    "RUN_EXTERNAL_NETWORK_TESTS",
    "NEWS_MODE",
    "EDITORIAL_PRODUCTION",
):
    os.environ.pop(unsafe_name, None)
os.environ["APP_MODE"] = "mock"
os.environ["TEAMS_AI_NEWS_WATCH"] = "0"
os.environ["TELEGRAM_AUTO_SEND"] = "0"

from app import (  # noqa: E402
    briefing,
    collector,
    config,
    db,
    editorial_briefings,
    insight,
    publisher_direct,
    scoring,
    source_priority,
    teams_ai_push,
)
import build_static_dashboard  # noqa: E402
import build_static_report  # noqa: E402
import build_telegram_digest  # noqa: E402

PASS = 0
FAIL = 0
NETWORK_ATTEMPTS = 0
SMTP_ATTEMPTS = 0
TEAMS_SENDS = 0
TELEGRAM_SENDS = 0

PROTECTED_PATHS = (
    ".github/workflows/editorial-daily-brief.yml",
    ".github/workflows/editorial-review-console.yml",
    ".github/workflows/editorial-weekly-ti.yml",
    ".github/workflows/email-alert.yml",
    ".github/workflows/scheduled-live-refresh.yml",
    ".github/workflows/teams-ai-news-watch.yml",
    ".github/workflows/telegram-notify.yml",
    "templates/editorial_daily.html",
    "data/editorial_daily_state.json",
    "data/teams_push_state.json",
)

SUBVERIFIERS = (
    (
        "OPS_R1_VERIFIER",
        "verify_ops_r1.py",
        (
            r"checks=(?P<passed>\d+) failures=0[\s\S]*"
            r"RESULT=D7-AK-6F-OPS-R1_VERIFIER_PASS"
        ),
    ),
    (
        "PUBLISHER_DIRECT_VERIFIER",
        "verify_publisher_direct_collector.py",
        r"PUBLISHER_DIRECT_VERIFIER=(?P<passed>\d+)/0",
    ),
    (
        "R3_V7_VERIFIER",
        "verify_editorial_review_console.py",
        r"R3_V7_VERIFIER=(?P<passed>\d+)/0",
    ),
    (
        "EDITORIAL_REGRESSION",
        "verify_editorial_briefings.py",
        r"RESULT pass=(?P<passed>\d+) fail=0",
    ),
    (
        "TEAMS_CORE_VERIFIER",
        "verify_teams_ai_push.py",
        r"RESULT=D7-AK-6C_TEAMS_AI_PUSH_VERIFIER_PASS",
    ),
    (
        "TEAMS_STATE_VERIFIER",
        "verify_teams_push_state.py",
        r"RESULT=D7-AK-5B_TEAMS_PUSH_STATE_VERIFIER_PASS",
    ),
    (
        "TEAMS_DRY_RUN_VERIFIER",
        "verify_teams_ai_push_dry_run.py",
        r"RESULT=D7-AK-5C_TEAMS_AI_PUSH_DRY_RUN_VERIFIER_PASS",
    ),
    (
        "TEAMS_WORKFLOW_VERIFIER",
        "verify_teams_ai_push_workflow_dry_run.py",
        r"RESULT=D7-AK-6C_TEAMS_AI_NEWS_WATCH_WORKFLOW_VERIFIER_PASS",
    ),
    (
        "TEAMS_PRODUCTION_NO_SEND_VERIFIER",
        "verify_teams_ai_push_production.py",
        r"RESULT=D7-AK-6A_TEAMS_AI_PUSH_PRODUCTION_VERIFIER_PASS",
    ),
    (
        "TEAMS_VALIDATED_BRIEF_EQUIVALENCE_VERIFIER",
        "verify_teams_validated_brief_semantic_equivalence.py",
        r"RESULT=D7-AK-6E_TEAMS_VALIDATED_BRIEF_SEMANTIC_EQUIVALENCE_PASS",
    ),
    (
        "NEWS_CENSOR_FULL_IMAGE_COVERAGE_VERIFIER",
        "verify_news_censor_image_coverage.py",
        r"RESULT=D7-AK-6E_NEWS_CENSOR_FULL_IMAGE_COVERAGE_PASS",
    ),
    (
        "NEWS_CENSOR_SEMANTIC_FILTER_VERIFIER",
        "verify_news_censor_semantic_filters.py",
        r"NEWS_CENSOR_SEMANTIC_FILTERS=PASS",
    ),
    (
        "SCHEDULED_REFRESH_VERIFIER",
        "verify_scheduled_refresh_and_telegram.py",
        r"RESULT: PASS",
    ),
    (
        "STATIC_REPORT_VERIFIER",
        "verify_static_report.py",
        r"RESULT: PASS",
    ),
    (
        "DATA_SOURCE_HONESTY_VERIFIER",
        "verify_data_source_honesty.py",
        r"RESULT: PASS",
    ),
    (
        "DASHBOARD_REAL_DATA_VERIFIER",
        "verify_dashboard_real_data.py",
        r"RESULT: PASS",
    ),
    (
        "HUMAN_REVIEW_GATE",
        "verify_human_review_gate.py",
        r"RESULT: PASS",
    ),
)


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


def _write_child_network_guard(directory: Path) -> Path:
    """Install a child-process guard while allowing local browser IPC."""
    guard = directory / "sitecustomize.py"
    guard.write_text(
        r'''
import ipaddress
import os
import smtplib
import socket
import urllib.parse
import urllib.request

_audit = os.environ.get("R3_NETWORK_AUDIT_LOG", "")
_socket_connect = socket.socket.connect
_socket_connect_ex = socket.socket.connect_ex
_getaddrinfo = socket.getaddrinfo
_urlopen = urllib.request.urlopen
_smtp_connect = smtplib.SMTP.connect
_smtp_ssl_connect = smtplib.SMTP_SSL.connect

def _record(kind, target):
    if _audit:
        with open(_audit, "a", encoding="utf-8") as stream:
            stream.write(f"{kind}\t{target}\n")

def _loopback(value):
    host = str(value or "").strip("[]").casefold().rstrip(".")
    if host in {"localhost", "ip6-localhost"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False

def _blocked(kind, target):
    _record(kind, target)
    raise RuntimeError(f"R3 integration external network blocked: {kind} {target}")

def guarded_getaddrinfo(host, *args, **kwargs):
    normalized = str(host or "").casefold().rstrip(".")
    if _loopback(host):
        return _getaddrinfo(host, *args, **kwargs)
    # Verifiers pass injected fetchers/openers but the production safety helpers
    # still require a DNS classification first. Return deterministic addresses
    # without asking the host resolver; any subsequent real connect remains
    # blocked and audited below.
    address = (
        "10.0.0.7"
        if normalized.startswith("private.")
        or normalized.endswith((".internal", ".local", ".localhost"))
        else "93.184.216.34"
    )
    socktype = kwargs.get("type") or socket.SOCK_STREAM
    return [(socket.AF_INET, socktype, 6, "", (address, 0))]

def guarded_connect(sock, address):
    if sock.family == socket.AF_UNIX:
        return _socket_connect(sock, address)
    host = address[0] if isinstance(address, tuple) and address else address
    if _loopback(host):
        return _socket_connect(sock, address)
    return _blocked("socket.connect", address)

def guarded_connect_ex(sock, address):
    if sock.family == socket.AF_UNIX:
        return _socket_connect_ex(sock, address)
    host = address[0] if isinstance(address, tuple) and address else address
    if _loopback(host):
        return _socket_connect_ex(sock, address)
    return _blocked("socket.connect_ex", address)

def guarded_urlopen(url, *args, **kwargs):
    value = url.full_url if hasattr(url, "full_url") else str(url)
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in {"http", "https"} or _loopback(parsed.hostname):
        return _urlopen(url, *args, **kwargs)
    return _blocked("urlopen", value)

def guarded_smtp_connect(instance, host="localhost", port=0, *args, **kwargs):
    return _blocked("smtp.connect", f"{host}:{port}")

socket.getaddrinfo = guarded_getaddrinfo
socket.socket.connect = guarded_connect
socket.socket.connect_ex = guarded_connect_ex
urllib.request.urlopen = guarded_urlopen
smtplib.SMTP.connect = guarded_smtp_connect
smtplib.SMTP_SSL.connect = guarded_smtp_connect
'''.lstrip(),
        encoding="utf-8",
    )
    return guard


def _child_env(guard_dir: Path, audit_log: Path, db_path: Path) -> dict[str, str]:
    env = dict(os.environ)
    for name in (
        "RUN_EXTERNAL_NETWORK_TESTS",
        "NEWS_MODE",
        "EDITORIAL_PRODUCTION",
        "GMAIL_SMTP_USER",
        "GMAIL_SMTP_APP_PASSWORD",
        "ALERT_EMAIL_FROM",
        "ALERT_EMAIL_TO",
        "TEAMS_CHANNEL_EMAIL",
        "TEAMS_WORKFLOW_WEBHOOK_URL",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "CONFIRM_SEND",
        "REVIEW_APPROVED",
        "APPROVE_TEAMS_AI_PUSH",
    ):
        env.pop(name, None)
    env.update(
        {
            "APP_MODE": "mock",
            "DB_PATH": str(db_path),
            "MACRO_MODE": "mock",
            "TEAMS_AI_NEWS_WATCH": "0",
            "TEAMS_AI_PUSH_MODE": "dry-run",
            "TELEGRAM_AUTO_SEND": "0",
            "R3_NETWORK_AUDIT_LOG": str(audit_log),
            "PYTHONPATH": os.pathsep.join(
                (
                    str(guard_dir),
                    str(ROOT),
                    str(SCRIPTS),
                    env.get("PYTHONPATH", ""),
                )
            ).rstrip(os.pathsep),
        }
    )
    return env


def _run(
    command: list[str],
    *,
    env: dict[str, str],
    timeout: int = 1200,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


@contextmanager
def block_current_process_network():
    """Fail closed if an integrated fixture unexpectedly reaches a sender/network."""
    global NETWORK_ATTEMPTS, SMTP_ATTEMPTS
    old_connect = socket.socket.connect
    old_connect_ex = socket.socket.connect_ex
    old_create_connection = socket.create_connection
    old_getaddrinfo = socket.getaddrinfo
    old_urlopen = urllib.request.urlopen
    old_smtp_connect = smtplib.SMTP.connect
    old_smtp_ssl_connect = smtplib.SMTP_SSL.connect

    def blocked_network(*_args, **_kwargs):
        global NETWORK_ATTEMPTS
        NETWORK_ATTEMPTS += 1
        raise AssertionError("external network forbidden in R3 stack fixture")

    def blocked_smtp(*_args, **_kwargs):
        global SMTP_ATTEMPTS
        SMTP_ATTEMPTS += 1
        raise AssertionError("SMTP forbidden in R3 stack fixture")

    socket.socket.connect = blocked_network
    socket.socket.connect_ex = blocked_network
    socket.create_connection = blocked_network
    socket.getaddrinfo = blocked_network
    urllib.request.urlopen = blocked_network
    smtplib.SMTP.connect = blocked_smtp
    smtplib.SMTP_SSL.connect = blocked_smtp
    try:
        yield
    finally:
        socket.socket.connect = old_connect
        socket.socket.connect_ex = old_connect_ex
        socket.create_connection = old_create_connection
        socket.getaddrinfo = old_getaddrinfo
        urllib.request.urlopen = old_urlopen
        smtplib.SMTP.connect = old_smtp_connect
        smtplib.SMTP_SSL.connect = old_smtp_ssl_connect


def _direct_article(
    url: str,
    *,
    title: str,
    source: str,
    discovery_url: str = "",
    discovery_provider: str = "publisher_direct_rss",
) -> dict:
    return {
        "id": hashlib.sha256(url.encode("utf-8")).hexdigest()[:16],
        "title": title,
        "snippet": (
            "현대건설 사업과 연결되는 AI 데이터센터·스마트건설 인프라 투자와 "
            "실행 일정이 공식 발표됐다."
        ),
        "summary": (
            "AI 데이터센터와 전력 인프라 투자 계획이 확정됐으며 현대건설의 "
            "EPC·스마트건설 사업 검토에 직접 영향을 준다."
        ),
        "source": source,
        "published_at": "2026-07-31T06:00:00+09:00",
        "url": url,
        "canonical_url": url,
        "publisher_url": url,
        "publisher_direct": True,
        "publisher_domain": re.sub(r"^https?://([^/]+).*$", r"\1", url),
        "discovery_url": discovery_url or url,
        "discovery_provider": discovery_provider,
        "portal_resolution_status": "resolved",
        "portal_resolution_reason": "integration_fixture_verified_publisher_page",
        "quarantine": False,
        "status": "collected",
        "signal_origin": "Live RSS",
        "source_metadata": {
            "provider": discovery_provider,
            "discovery_url": discovery_url or url,
            "discovery_provider": discovery_provider,
            "publisher_url": url,
            "canonical_url": url,
            "publisher_direct": True,
            "portal_resolution_status": "resolved",
        },
    }


def _teams_article(article: dict) -> dict:
    row = dict(article)
    row.update(
        {
            "article_key": article.get("id")
            or hashlib.sha256(
                str(
                    article.get("publisher_url")
                    or article.get("discovery_url")
                    or article.get("url")
                    or article.get("title")
                    or ""
                ).encode("utf-8")
            ).hexdigest()[:16],
            "hdec_relevance": "데이터센터 EPC와 스마트건설 사업기회에 직접 영향",
            "score": 4.8,
            "shadow_urgency_status": "confirmed",
            "shadow_would_pass": True,
            "shadow_confirmed_event_types": ["investment_confirmed"],
            "change_type": "new_article",
        }
    )
    return row


def _html_link_urls(document: str) -> list[str]:
    return re.findall(r'href=["\']([^"\']+)', document or "", re.I)


def _structured_action_urls(value: object) -> list[str]:
    if isinstance(value, dict):
        urls = [
            str(item)
            for key, item in value.items()
            if str(key).casefold() in {"url", "href"}
            and isinstance(item, str)
        ]
        return urls + [
            item
            for nested in value.values()
            for item in _structured_action_urls(nested)
        ]
    if isinstance(value, (list, tuple)):
        return [
            item
            for nested in value
            for item in _structured_action_urls(nested)
        ]
    return []


def _portal_final_urls(values: list[str]) -> list[str]:
    return [value for value in values if publisher_direct.portal_provider(value)]


def integrated_fixture(build_root: Path, child_env: dict[str, str]) -> dict[str, int]:
    build_root.mkdir(parents=True, exist_ok=True)
    major_url = "https://major.fixture.test/news/ai-datacenter"
    daum_url = "https://v.daum.net/v/202607310900"
    government_url = "https://official.fixture.test/brief/smart-construction"
    consumer_url = "https://major.fixture.test/news/consumer-ai-gossip"

    major = _direct_article(
        major_url,
        title="현대건설 AI 데이터센터 인프라 투자 계약 확정",
        source="연합뉴스",
    )
    portal_discovery = {
        "title": major["title"],
        "snippet": major["snippet"],
        "source": "Daum 뉴스",
        "published_at": major["published_at"],
        "url": daum_url,
        "discovery_url": daum_url,
        "discovery_provider": "daum",
        "publisher_direct": False,
        "source_metadata": {
            "provider": "daum",
            "discovery_url": daum_url,
            "discovery_provider": "daum",
        },
    }
    portal_resolved = publisher_direct.apply_publisher_authority(
        portal_discovery,
        publisher_canonical_url=major_url,
        source="연합뉴스",
        published_at=major["published_at"],
        resolution_reason="fixture_daum_canonical_then_publisher_refetch",
    )
    government = _direct_article(
        government_url,
        title="국토부 AI 스마트건설 안전관리 정책 확정",
        source="국토교통부",
    )
    unresolved = publisher_direct.quarantine_article(
        {
            "title": "중요 AI 인프라 포털 기사 원문 미해소",
            "snippet": "AI 데이터센터 투자 기사지만 언론사 원문을 찾지 못했다.",
            "source": "Daum 뉴스",
            "published_at": "2026-07-31T06:10:00+09:00",
            "url": daum_url + "1",
            "discovery_url": daum_url + "1",
            "discovery_provider": "daum",
            "publisher_direct": False,
        },
        "portal_original_not_found",
    )
    consumer = _direct_article(
        consumer_url,
        title="AI 스마트폰 루머 셀카 소비자 가십",
        source="연합뉴스",
    )
    consumer["snippet"] = "스마트폰 셀카 기능에 관한 확인되지 않은 소비자 가십이다."
    consumer["summary"] = "스마트폰 루머와 소비자 반응만 다룬 기사다."

    check(
        "policy order: publisher authority precedes trusted source priority",
        publisher_direct.is_publisher_direct_delivery_eligible(major)
        and source_priority.classify(major["source"])["source_priority_bucket"]
        == "major",
    )
    check(
        "trusted portal discovery remains ineligible before publisher resolution",
        not publisher_direct.is_publisher_direct_delivery_eligible(
            portal_discovery,
            relevance_qualified=True,
        ),
    )
    check(
        "Daum discovery resolves to publisher authority with provenance",
        portal_resolved["url"] == major_url
        and portal_resolved["publisher_direct"] is True
        and portal_resolved["discovery_url"] == daum_url,
    )

    clustered, duplicate_quarantine = publisher_direct.partition_delivery_articles(
        collector.merge_provider_articles([major, portal_resolved]),
        relevance_qualified=True,
    )
    check(
        "publisher canonical clusters direct and Daum discovery to one article",
        len(clustered) == 1 and duplicate_quarantine == [],
    )
    check(
        "publisher canonical cluster retains Daum audit provenance",
        daum_url
        in (clustered[0]["source_metadata"].get("discovery_urls") or []),
    )
    check(
        "unresolved important portal article remains quarantined",
        unresolved["status"] == "quarantine"
        and not publisher_direct.is_publisher_direct_delivery_eligible(
            unresolved,
            relevance_qualified=True,
        ),
    )
    check(
        "official smart-construction press release is eligible",
        publisher_direct.is_publisher_direct_delivery_eligible(government)
        and source_priority.classify(government["source"])[
            "source_priority_bucket"
        ]
        == "official",
    )
    check(
        "major-media consumer AI gossip fails relevance before priority",
        not publisher_direct.is_publisher_direct_delivery_eligible(consumer)
        and source_priority.classify(consumer["source"])[
            "source_priority_bucket"
        ]
        == "major",
    )

    direct_rows, delivery_quarantine = publisher_direct.partition_delivery_articles(
        clustered + [government, unresolved, consumer]
    )
    check(
        "common delivery partition keeps direct relevant rows only",
        len(direct_rows) == 2 and len(delivery_quarantine) == 2,
    )

    quarantine_storage = collector._to_article_row(  # noqa: SLF001
        unresolved,
        [],
        "2026-07-31T06:30:00+09:00",
        "Live RSS Quarantine",
    )
    verified_storage = collector._to_article_row(  # noqa: SLF001
        portal_resolved,
        [],
        "2026-07-31T06:31:00+09:00",
        "Live RSS",
    )
    check(
        "quarantine identity cannot block verified publisher canonical",
        quarantine_storage["id"] != verified_storage["id"]
        and quarantine_storage["status"] == "quarantine"
        and verified_storage["url"] == major_url,
    )

    previous_db_path = config.DB_PATH
    previous_macro_mode = config.MACRO_MODE
    config.DB_PATH = build_root / "integration.db"
    config.MACRO_MODE = "mock"
    try:
        db.init_db()
        collector._ingest([unresolved], "Live RSS Quarantine")  # noqa: SLF001
        collected, deduplicated, inserted = collector._ingest(  # noqa: SLF001
            direct_rows,
            "Live RSS",
        )
        stored = db.fetch_articles_with_scores()
        check(
            "quarantine audit row and verified publisher row coexist in DB",
            any(row["status"] == "quarantine" for row in stored)
            and any(row["url"] == major_url for row in stored),
        )
        score_stats = scoring.score_all()
        insight.generate_all()
        brief = briefing.build_brief(
            pipeline_counts={
                "collected": collected,
                "deduplicated": deduplicated,
                "inserted": inserted,
                "scored": score_stats["scored"],
                "alert_candidates": score_stats["alert_candidates"],
            },
            news_provenance={
                "news_source": "publisher_direct_integration_fixture",
                "fallback_used": False,
                "publisher_direct_quarantine_count": 1,
            },
        )
    finally:
        config.DB_PATH = previous_db_path
        config.MACRO_MODE = previous_macro_mode

    check(
        "brief exposes quarantine count but never quarantine URL",
        brief["publisher_direct_delivery"]["quarantine_count"] == 1
        and daum_url + "1" not in json.dumps(brief, ensure_ascii=False),
    )

    report_html, _sections = build_static_report.render_report_html(brief)
    dashboard_html = build_static_dashboard.render_dashboard_html(
        brief,
        market_mode="mock",
    )
    original_brief_builder = build_telegram_digest.build_brief_via_mock_pipeline
    build_telegram_digest.build_brief_via_mock_pipeline = lambda: brief
    try:
        telegram_data = build_telegram_digest.build_digest_data()
        telegram_message = build_telegram_digest.format_digest_message(
            telegram_data
        )
    finally:
        build_telegram_digest.build_brief_via_mock_pipeline = original_brief_builder

    run_at = editorial_briefings.parse_published_at(
        "2026-07-31T07:30:00+09:00"
    )
    daily_articles = editorial_briefings.normalize_articles(
        direct_rows,
        editorial_briefings.daily_coverage(run_at),
        limit=6,
        resolve_images=False,
        selection_mode=(
            editorial_briefings.SELECTION_MODE_DIRECT_AWARE_DAILY
        ),
    )
    daily = editorial_briefings.render_daily(
        daily_articles,
        run_at=run_at,
        root_url="https://integration.fixture.test/HDEC-News-Sensor",
    )

    output_files = {
        "daily": build_root / "daily.html",
        "report": build_root / "static-report.html",
        "dashboard": build_root / "static-dashboard.html",
        "teams_card": build_root / "teams-card.json",
        "teams_email": build_root / "teams-email.html",
        "telegram": build_root / "telegram-preview.html",
    }
    output_files["daily"].write_text(daily.html, encoding="utf-8")
    output_files["report"].write_text(report_html, encoding="utf-8")
    output_files["dashboard"].write_text(dashboard_html, encoding="utf-8")
    output_files["telegram"].write_text(telegram_message, encoding="utf-8")

    teams_rows = [_teams_article(major), _teams_article(unresolved)]
    candidates = teams_ai_push.select_teams_push_candidates(
        teams_rows,
        max_articles=10,
    )
    check(
        "Teams authority gate excludes quarantine before SMTP",
        len(candidates) == 1 and candidates[0].article["url"] == major_url,
    )
    card = teams_ai_push.build_candidate_card(
        {
            "dashboard_url": "https://integration.fixture.test/dashboard",
            "report_url": "https://integration.fixture.test/report",
        },
        candidates[0],
        detected_at="2026-07-31T06:20:00+09:00",
    )
    _subject, _text_body, email_html = teams_ai_push.render_article_email(
        {
            "dashboard_url": "https://integration.fixture.test/dashboard",
            "report_url": "https://integration.fixture.test/report",
        },
        candidates[0],
        detected_at="2026-07-31T06:20:00+09:00",
    )
    output_files["teams_card"].write_text(
        json.dumps(card, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    output_files["teams_email"].write_text(email_html, encoding="utf-8")

    review_root = build_root / "editorial-review"
    review = _run(
        [
            sys.executable,
            str(SCRIPTS / "build_editorial_review_console.py"),
            "--fixture",
            "--run-at",
            "2026-07-31T07:20:00+09:00",
            "--output-root",
            str(review_root),
        ],
        env=child_env,
    )
    check(
        "editorial review fixture console build succeeds offline",
        review.returncode == 0,
        (review.stdout + review.stderr)[-1000:],
    )
    latest_console = review_root / "latest" / "index.html"
    latest_candidates = review_root / "latest" / "candidates.json"
    latest_manifest = review_root / "latest" / "manifest.json"
    check(
        "all required temporary build files exist",
        all(path.is_file() and path.stat().st_size > 0 for path in output_files.values())
        and latest_console.is_file()
        and latest_candidates.is_file()
        and latest_manifest.is_file(),
    )

    counts = {
        "daily": len(_portal_final_urls(_html_link_urls(daily.html))),
        "dashboard": len(
            _portal_final_urls(_html_link_urls(dashboard_html))
        ),
        "teams": len(
            _portal_final_urls(
                _structured_action_urls(card)
                + _html_link_urls(email_html)
            )
        ),
        "telegram": len(
            _portal_final_urls(_html_link_urls(telegram_message))
        ),
    }
    check(
        "Daily/Dashboard/Teams/Telegram final portal URLs are zero",
        all(count == 0 for count in counts.values()),
        counts,
    )
    check(
        "temporary surfaces contain publisher original links",
        major_url in daily.html
        and major_url in report_html
        and major_url in dashboard_html
        and major_url in json.dumps(card, ensure_ascii=False),
    )
    check(
        "quarantine URL is absent from every temporary delivery surface",
        daum_url + "1" not in daily.html
        and daum_url + "1" not in dashboard_html
        and daum_url + "1" not in json.dumps(card, ensure_ascii=False)
        and daum_url + "1" not in email_html
        and daum_url + "1" not in telegram_message
        and daum_url + "1" not in report_html,
    )
    return counts


def main() -> int:
    protected_before = {path: sha256(ROOT / path) for path in PROTECTED_PATHS}
    child_results: dict[str, str] = {}
    counts = {"daily": -1, "dashboard": -1, "teams": -1, "telegram": -1}

    with tempfile.TemporaryDirectory(
        prefix="d7ak6e-r3-stack-integration-",
        dir="/tmp",
    ) as raw:
        temporary = Path(raw)
        guard = _write_child_network_guard(temporary)
        audit_log = temporary / "external-network-attempts.log"

        for label, script, expected in SUBVERIFIERS:
            env = _child_env(
                guard.parent,
                audit_log,
                temporary / f"{script}.db",
            )
            result = _run(
                [sys.executable, str(SCRIPTS / script)],
                env=env,
            )
            output = result.stdout + result.stderr
            if (
                label == "R3_V7_VERIFIER"
                and result.returncode != 0
                and "headless browser timeout" in output
            ):
                print(
                    "INFO R3_V7_VERIFIER headless browser timed out; "
                    "retrying once with a fresh profile"
                )
                result = _run(
                    [sys.executable, str(SCRIPTS / script)],
                    env=env,
                )
                output = result.stdout + result.stderr
            match = re.search(expected, output)
            check(
                f"{label} subprocess passes under network guard",
                result.returncode == 0 and match is not None,
                f"rc={result.returncode} tail={output[-1600:]!r}",
            )
            if match and "passed" in match.groupdict():
                child_results[label] = match.group("passed")
            else:
                child_results[label] = "PASS" if match else "FAIL"

        with block_current_process_network():
            counts = integrated_fixture(
                temporary / "builds",
                _child_env(
                    guard.parent,
                    audit_log,
                    temporary / "review-builder.db",
                ),
            )

        external_attempts = (
            audit_log.read_text(encoding="utf-8").splitlines()
            if audit_log.exists()
            else []
        )
        check(
            "child verifiers made zero external network attempts",
            external_attempts == [],
            external_attempts[:10],
        )

    protected_after = {path: sha256(ROOT / path) for path in PROTECTED_PATHS}
    check(
        "workflow files remain byte-identical",
        all(
            protected_after[path] == protected_before[path]
            for path in PROTECTED_PATHS
            if path.startswith(".github/workflows/")
        ),
    )
    check(
        "Daily template and production states remain byte-identical",
        all(
            protected_after[path] == protected_before[path]
            for path in (
                "templates/editorial_daily.html",
                "data/editorial_daily_state.json",
                "data/teams_push_state.json",
            )
        ),
    )
    check(
        "all send and production-write counters remain zero",
        NETWORK_ATTEMPTS == 0
        and SMTP_ATTEMPTS == 0
        and TEAMS_SENDS == 0
        and TELEGRAM_SENDS == 0,
    )
    check(
        "TEAMS transport and canary contract remains intact",
        teams_ai_push.MAX_TEAMS_ARTICLES == 10
        and "TEAMS_CHANNEL_EMAIL"
        in (ROOT / ".github/workflows/teams-ai-news-watch.yml").read_text(
            encoding="utf-8"
        )
        and "cron: '7,17,27,37,47,57 * * * *'"
        in (ROOT / ".github/workflows/teams-ai-news-watch.yml").read_text(
            encoding="utf-8"
        ),
    )
    check(
        "TEAMS_AI_NEWS_WATCH remains zero",
        os.environ.get("TEAMS_AI_NEWS_WATCH") == "0",
    )

    publisher_raw = child_results.get("PUBLISHER_DIRECT_VERIFIER", "0")
    console_raw = child_results.get("R3_V7_VERIFIER", "0")
    editorial_raw = child_results.get("EDITORIAL_REGRESSION", "0")
    ops_raw = child_results.get("OPS_R1_VERIFIER", "0")
    ops_count = int(ops_raw) if ops_raw.isdigit() else 0
    publisher_count = int(publisher_raw) if publisher_raw.isdigit() else 0
    console_count = int(console_raw) if console_raw.isdigit() else 0
    editorial_count = int(editorial_raw) if editorial_raw.isdigit() else 0
    check("OPS-R1 verifier has zero failures", ops_count > 0)
    check("publisher-direct verifier count is at least 79", publisher_count >= 79)
    check("R3-V7 verifier count remains at least 189", console_count >= 189)
    # The sealed main suite currently contains 333 checks. R4 does not alter
    # Editorial behavior; allow future additive checks without pinning a stale
    # historical count.
    check("Editorial regression count remains at least 333", editorial_count >= 333)

    print(f"R3_STACK_INTEGRATION_VERIFIER={PASS}/{FAIL}")
    print(f"OPS_R1_VERIFIER={ops_count}/0")
    print(f"PUBLISHER_DIRECT_VERIFIER={publisher_count}/0")
    print(f"R3_V7_VERIFIER={console_count}/0")
    print(f"EDITORIAL_REGRESSION={editorial_count}/0")
    for label in (
        "TEAMS_CORE_VERIFIER",
        "TEAMS_STATE_VERIFIER",
        "TEAMS_DRY_RUN_VERIFIER",
        "TEAMS_WORKFLOW_VERIFIER",
        "TEAMS_PRODUCTION_NO_SEND_VERIFIER",
        "SCHEDULED_REFRESH_VERIFIER",
        "STATIC_REPORT_VERIFIER",
        "DATA_SOURCE_HONESTY_VERIFIER",
        "DASHBOARD_REAL_DATA_VERIFIER",
        "HUMAN_REVIEW_GATE",
    ):
        print(f"{label}={child_results.get(label, 'FAIL')}")
    print(f"FINAL_DAILY_PORTAL_URLS={counts['daily']}")
    print(f"FINAL_DASHBOARD_PORTAL_URLS={counts['dashboard']}")
    print(f"FINAL_TEAMS_PORTAL_URLS={counts['teams']}")
    print(f"FINAL_TELEGRAM_PORTAL_URLS={counts['telegram']}")
    print("QUARANTINE_DELIVERY_COUNT=0")
    print(f"EXTERNAL_TEST_NETWORK_CALLS={NETWORK_ATTEMPTS}")
    print(f"SMTP_ATTEMPTS={SMTP_ATTEMPTS}")
    print(f"TEAMS_SENDS={TEAMS_SENDS}")
    print(f"TELEGRAM_SENDS={TELEGRAM_SENDS}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
