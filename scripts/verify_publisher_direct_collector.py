#!/usr/bin/env python3
"""Offline verifier for R3-V8 publisher-direct production collection.

All URL/DNS/HTTP behavior is fixture-owned. The verifier never dispatches a
workflow, opens SMTP, sends Teams/Telegram, writes production state, or calls an
external article/feed endpoint.
"""

from __future__ import annotations

import copy
import hashlib
import html
import json
import os
import re
import socket
import sys
import tempfile
import urllib.request
from contextlib import contextmanager
from email.message import Message
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
for item in (ROOT, SCRIPTS):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from app import (  # noqa: E402
    briefing,
    collector,
    config,
    db,
    editorial_article_import,
    editorial_briefings,
    insight,
    live_collector,
    naver_news_provider,
    publisher_direct,
    scoring,
    teams_ai_push,
    thebell_watch,
)
import build_static_dashboard  # noqa: E402
import build_static_report  # noqa: E402
import build_telegram_digest  # noqa: E402

EXPECTED_PROTECTED_SHA256 = {
    ".github/workflows/editorial-daily-brief.yml": (
        "2d2d3d7cbfaae111356e22bb8f4090aaf91db14b1c612623874ef8e9aa0bcff0"
    ),
    ".github/workflows/editorial-review-console.yml": (
        "1f88a8c69f546daab9fed5d69dee353fc0a541ca26b80bb49de805a6d93517f0"
    ),
    ".github/workflows/editorial-weekly-ti.yml": (
        "f214f797271ffbfc54ea76da9f3da71ea4aa77a7846f130b90b0eb8546353ea4"
    ),
    ".github/workflows/email-alert.yml": (
        "b410682cc5e62da76b6a2e6e8b55f1fad945fb27fa41d6019808fc1192ef054d"
    ),
    ".github/workflows/scheduled-live-refresh.yml": (
        # R4-R1 intentionally replaces exact main baseline
        # 65dc2a9d6ea31a5d0495d30a27a015f7d74d50a1386ce995ce853a265024b5ec
        # with one collected brief artifact reused by every static consumer;
        # both sender defaults remain closed during deployment.
        "c426e16ec876b3df18d174a06794ec5e261bef2a23b242bcda1c92c2c5a8a1f2"
    ),
    ".github/workflows/teams-ai-news-watch.yml": (
        # R4-R1 intentionally replaces exact main baseline
        # f0a9bd171bd5dc1a29a357c0f8394c4ae7957d1f34ba23a94d2c5c0d30667df0
        # so Teams reuses and validates the same collected artifact before delta.
        "e275b1b2a57b7f28c97b7ccce98c5890eb62777cace5fc5ff1a0561cbb606406"
    ),
    ".github/workflows/telegram-notify.yml": (
        "18ed4f6df937685329dda440a29bb8979d780c06e169af721b9c2218f01f379c"
    ),
    "templates/editorial_daily.html": (
        "936b497c51200f8d90994de4f607bbc1570bbaefbb3b443ad7844a50758476fe"
    ),
    "data/editorial_daily_state.json": (
        # Sealed main state after the 2026-08-01 Daily delivery commits.
        "4c1588ddde8b177191f9bd1e9ff7b2d52b56cae3b9f233012abb005ca0fb3d90"
    ),
    "data/teams_push_state.json": (
        "3032d829262085164cc159d902701b65526c58bec34b52531abd9ed78bbdca3a"
    ),
}

PASS = 0
FAIL = 0
EXTERNAL_NETWORK_CALLS = 0
SMTP_ATTEMPTS = 0
TEAMS_SENDS = 0
TELEGRAM_SENDS = 0


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


def portal_hrefs(document: str) -> list[str]:
    return [
        html.unescape(value)
        for value in re.findall(r'href=["\']([^"\']+)', str(document or ""), re.I)
        if publisher_direct.portal_provider(html.unescape(value))
    ]


def resolver(host: str, port: int, **_kwargs):
    if host in {"private.fixture.test", "localhost"}:
        address = "10.0.0.7"
    else:
        address = "93.184.216.34"
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]


class FixtureResponse:
    def __init__(
        self,
        url: str,
        *,
        status: int = 200,
        content_type: str = "text/html; charset=utf-8",
        body: bytes | str = b"",
        location: str = "",
    ):
        self.status = status
        self._url = url
        self._payload = body.encode("utf-8") if isinstance(body, str) else bytes(body)
        self._offset = 0
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        self.headers["Content-Length"] = str(len(self._payload))
        if location:
            self.headers["Location"] = location

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            size = len(self._payload) - self._offset
        start = self._offset
        end = min(len(self._payload), start + size)
        self._offset = end
        return self._payload[start:end]

    def getcode(self) -> int:
        return self.status

    def geturl(self) -> str:
        return self._url

    def close(self) -> None:
        return None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
        return False


class FixtureOpener:
    def __init__(self, routes: dict[str, dict]):
        self.routes = routes
        self.calls: list[str] = []

    def open(self, request, timeout: int = 0):
        url = request.full_url if hasattr(request, "full_url") else str(request)
        self.calls.append(url)
        route = self.routes.get(url)
        if route is None:
            raise AssertionError(f"fixture route missing: {url}")
        return FixtureResponse(url, **route)


def article_html(
    canonical: str,
    title: str,
    source: str,
    *,
    published_at: str = "2026-07-31T06:00:00+09:00",
) -> str:
    body = (
        f"{title} 관련 공식 발표에서 현대건설 사업과 연결되는 AI 데이터센터, "
        "스마트건설, 전력 인프라 투자 계획과 실행 일정이 공개됐다. "
        "발표 기관은 수치와 계약 범위, 기술 적용 대상, 안전 및 품질 관리 기준을 "
        "구체적으로 설명했으며 원문에서 확인 가능한 사실만 제공했다. "
        "이번 조치는 건설·에너지·플랜트 조직의 의사결정과 공급망 검토에 활용될 수 있다."
    )
    payload = {
        "@context": "https://schema.org",
        "@type": "NewsArticle",
        "headline": title,
        "publisher": {"@type": "Organization", "name": source},
        "datePublished": published_at,
        "articleBody": body,
    }
    return (
        "<!doctype html><html><head>"
        f'<link rel="canonical" href="{canonical}">'
        f'<script type="application/ld+json">{json.dumps(payload, ensure_ascii=False)}</script>'
        "</head><body><article><p>"
        + body
        + "</p></article></body></html>"
    )


@contextmanager
def block_external_network():
    global EXTERNAL_NETWORK_CALLS
    original_urlopen = urllib.request.urlopen
    original_getaddrinfo = socket.getaddrinfo
    original_create_connection = socket.create_connection

    def blocked(*_args, **_kwargs):
        global EXTERNAL_NETWORK_CALLS
        EXTERNAL_NETWORK_CALLS += 1
        raise AssertionError("external network is forbidden in R3-V8 verifier")

    urllib.request.urlopen = blocked
    socket.getaddrinfo = blocked
    socket.create_connection = blocked
    try:
        yield
    finally:
        urllib.request.urlopen = original_urlopen
        socket.getaddrinfo = original_getaddrinfo
        socket.create_connection = original_create_connection


def fixture_row(url: str, *, provider: str, title: str, source: str = "발견 출처") -> dict:
    return {
        "id": hashlib.sha256(url.encode("utf-8")).hexdigest()[:16],
        "title": title,
        "source": source,
        "published_at": "2026-07-31T06:00:00+09:00",
        "url": url,
        "snippet": "AI 데이터센터와 스마트건설 인프라 투자 계획이 확정됐다.",
        "discovery_url": url,
        "discovery_provider": provider,
        "publisher_direct": not bool(publisher_direct.portal_provider(url)),
        "source_metadata": {
            "provider": provider,
            "query": "fixture",
            "source_url": url,
            "collected_at": "2026-07-31T06:10:00+09:00",
            "provider_response_id": "fixture",
            "discovery_url": url,
            "discovery_provider": provider,
        },
    }


def teams_article(url: str) -> dict:
    return {
        "article_key": hashlib.sha256(url.encode("utf-8")).hexdigest()[:12],
        "title": "OpenAI, 한국 AI 데이터센터에 50억달러 투자 확정",
        "summary": "GPU 기반 데이터센터 투자와 전력 인프라 계약을 공식 확정했다.",
        "hdec_relevance": "데이터센터 EPC와 전력 인프라 사업기회에 직접 영향",
        "source": "Fixture Publisher",
        "published_at": "2026-07-31T06:00:00+09:00",
        "url": url,
        "canonical_url": url,
        "publisher_url": url if not publisher_direct.portal_provider(url) else "",
        "publisher_direct": not bool(publisher_direct.portal_provider(url)),
        "score": 4.7,
        "shadow_urgency_status": "confirmed",
        "shadow_would_pass": True,
        "shadow_confirmed_event_types": ["investment_confirmed"],
        "change_type": "new_article",
    }


def main() -> int:
    protected_before = {
        path: sha256(ROOT / path) for path in EXPECTED_PROTECTED_SHA256
    }
    for path, expected in EXPECTED_PROTECTED_SHA256.items():
        check(f"protected SHA256 exact: {path}", protected_before[path] == expected)
    check(
        "TEAMS_AI_NEWS_WATCH remains zero",
        os.environ.get("TEAMS_AI_NEWS_WATCH", "0") == "0",
    )
    collector_source = (ROOT / "app/collector.py").read_text(encoding="utf-8")
    check(
        "publisher verification budget follows direct then Naver then portal order",
        "direct_rows + naver_rows + google_rows" in collector_source,
    )

    registry = json.loads(
        (ROOT / "data/publisher_direct_sources.json").read_text(encoding="utf-8")
    )
    enabled = [source for source in registry["sources"] if source["enabled"]]
    check("publisher source registry is versioned", registry["version"] == 1)
    check("registry has three enabled official sources", len(enabled) == 3)
    check(
        "enabled registry feeds are HTTPS and non-portal",
        all(
            feed.startswith("https://") and not publisher_direct.portal_provider(feed)
            for source in enabled
            for feed in source["feed_urls"]
        ),
    )
    check(
        "unverified HDEC RSS candidate stays disabled",
        any(
            source["source_id"] == "hdec_newsroom"
            and source["enabled"] is False
            and source["feed_urls"] == []
            for source in registry["sources"]
        ),
    )

    publisher_main = "https://publisher.fixture.test/news/main"
    publisher_outbound = "https://outbound.fixture.test/news/original"
    publisher_google = "https://global.fixture.test/news/google"
    publisher_canonical = "https://canonical.fixture.test/news/final"
    publisher_duplicate = "https://duplicate.fixture.test/news/canonical"
    official_direct = "https://official.fixture.test/news/direct-rss"
    official_unavailable = "https://official.fixture.test/news/page-unavailable"
    daum_canonical = "https://v.daum.net/v/2026073101"
    daum_outbound = "https://news.daum.net/outbound/2026073102"
    daum_missing = "https://v.daum.net/v/2026073103"
    daum_private = "https://v.daum.net/v/2026073104"
    daum_loop_a = "https://v.daum.net/loop/a"
    daum_loop_b = "https://news.daum.net/loop/b"
    naver_missing = "https://n.news.naver.com/mnews/article/001/001"
    google_discovery = "https://news.google.com/rss/articles/FIXTURE"

    routes = {
        daum_canonical: {
            "body": (
                '<html><head><link rel="canonical" '
                f'href="{publisher_main}"></head><body>portal</body></html>'
            )
        },
        publisher_main: {
            "body": article_html(
                publisher_main,
                "현대건설 AI 데이터센터 인프라 투자 확정",
                "퍼블리셔 메인",
            )
        },
        daum_outbound: {
            "body": (
                "<html><body><nav>메뉴</nav>"
                f'<a href="{publisher_outbound}">언론사 원문</a>'
                "</body></html>"
            )
        },
        publisher_outbound: {
            "body": article_html(
                publisher_outbound,
                "스마트건설 로봇 자동화 계약 체결",
                "아웃바운드 언론사",
            )
        },
        daum_missing: {
            "body": "<html><body><nav>메뉴 광고 로그인 공유하기</nav></body></html>"
        },
        daum_private: {
            "status": 302,
            "location": "http://10.0.0.8/private",
        },
        daum_loop_a: {"status": 302, "location": daum_loop_b},
        daum_loop_b: {"status": 302, "location": daum_loop_a},
        naver_missing: {
            "body": "<html><body><nav>포털 메뉴만 있음</nav></body></html>"
        },
        publisher_google: {
            "body": article_html(
                publisher_google,
                "글로벌 AI 전력망 데이터센터 투자 확대",
                "Global Fixture News",
            )
        },
        "https://canonical.fixture.test/news/old": {
            "body": article_html(
                publisher_canonical,
                "건설 디지털트윈 BIM 기술 도입",
                "Canonical Fixture",
            )
        },
        publisher_duplicate: {
            "body": article_html(
                publisher_duplicate,
                "현대건설 스마트건설 AI 통합 플랫폼 발표",
                "Duplicate Fixture",
            )
        },
        official_direct: {
            "body": (
                "<html><head>"
                "<title>국토부 건설현장 AI 안전관리 정책 발표</title>"
                f'<link rel="canonical" href="{official_direct}">'
                "</head><body><main>"
                "<h1>국토부 건설현장 AI 안전관리 정책 발표</h1>"
                "<p>자세한 보도자료 내용은 첨부파일을 참고하시기 바랍니다.</p>"
                "</main></body></html>"
            )
        },
        official_unavailable: {"status": 403},
    }
    opener = FixtureOpener(routes)

    direct_rss = f"""<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
    <channel><item>
      <title>국토부 건설현장 AI 안전관리 정책 발표</title>
      <link>{official_direct}</link>
      <pubDate>2026.07.31</pubDate>
      <content:encoded>스마트건설과 AI 안전관리 정책을 발표했다.</content:encoded>
    </item></channel></rss>"""
    feed_fetches: list[str] = []

    def feed_fetcher(url: str, _timeout: int) -> str:
        feed_fetches.append(url)
        return direct_rss

    with block_external_network():
        direct_rows = live_collector.fetch_publisher_direct_sources(
            fetcher=feed_fetcher
        )
        check("direct official RSS collection returns one canonical row", len(direct_rows) == 1)
        check("direct RSS stays pending until publisher page verification", (
            direct_rows[0]["source_metadata"]["provider"] == "publisher_direct_rss"
            and direct_rows[0]["publisher_direct"] is False
        ))
        check("official dotted RSS date is normalized with explicit KST", (
            direct_rows[0]["published_at"] == "2026-07-31T00:00:00+09:00"
        ))
        check("official content:encoded is retained only as bounded snippet", (
            direct_rows[0]["snippet"] == "스마트건설과 AI 안전관리 정책을 발표했다."
            and len(direct_rows[0]["snippet"]) <= live_collector.SNIPPET_MAX_LEN
        ))
        check("all enabled official feeds were fixture-fetched", len(feed_fetches) == 3)
        check("direct source collection made zero external calls", EXTERNAL_NETWORK_CALLS == 0)

        resolved_direct = copy.deepcopy(direct_rows)
        count = live_collector.resolve_publisher_urls(
            resolved_direct,
            strict=True,
            resolver=resolver,
            opener=opener,
            decoder=lambda _url: None,
        )
        check("direct source publisher page verification succeeds", count == 1)
        check("direct source final URL stays publisher canonical", (
            resolved_direct[0]["url"] == official_direct
            and resolved_direct[0]["publisher_direct"] is True
        ))
        check("publisher page fixes official source name", resolved_direct[0]["source"] == "국토교통부")
        check("official attachment-only release uses disclosed metadata fallback", (
            "publisher_body_attachment_only"
            in resolved_direct[0]["portal_resolution_reason"]
        ))

        unavailable_direct = copy.deepcopy(direct_rows)
        unavailable_direct[0]["url"] = official_unavailable
        unavailable_direct[0]["publisher_url"] = official_unavailable
        unavailable_direct[0]["discovery_url"] = official_unavailable
        unavailable_direct[0]["source_metadata"]["source_url"] = official_unavailable
        unavailable_direct[0]["source_metadata"]["publisher_url"] = official_unavailable
        unavailable_direct[0]["source_metadata"]["discovery_url"] = official_unavailable
        check("official feed remains authority when validated page returns 4xx", (
            live_collector.resolve_publisher_urls(
                unavailable_direct,
                strict=True,
                resolver=resolver,
                opener=opener,
                decoder=lambda _url: None,
            ) == 1
            and unavailable_direct[0]["publisher_direct"] is True
            and "publisher_page_unavailable"
            in unavailable_direct[0]["portal_resolution_reason"]
        ))
        portal_unavailable = [
            fixture_row(
                daum_missing,
                provider="google_news_rss",
                title="포털 4xx 원문 미확인",
            )
        ]
        check("portal discovery never receives official-feed fallback", (
            live_collector.resolve_publisher_urls(
                portal_unavailable,
                strict=True,
                resolver=resolver,
                opener=opener,
                decoder=lambda _url: None,
            ) == 0
            and portal_unavailable[0]["status"] == "quarantine"
        ))

        daum_row = fixture_row(
            daum_canonical,
            provider="google_news_rss",
            title="포털 발견 제목",
        )
        check("Daum discovery is not publisher authority", (
            not publisher_direct.is_publisher_direct_delivery_eligible(
                daum_row, relevance_qualified=True
            )
        ))
        resolved_daum = [copy.deepcopy(daum_row)]
        check("Daum canonical portal resolution succeeds", (
            live_collector.resolve_publisher_urls(
                resolved_daum,
                strict=True,
                resolver=resolver,
                opener=opener,
                decoder=lambda _url: None,
            ) == 1
        ))
        check("Daum selected_url becomes publisher URL", resolved_daum[0]["url"] == publisher_main)
        check("Daum provenance remains discovery-only", (
            resolved_daum[0]["discovery_url"] == daum_canonical
            and resolved_daum[0]["discovery_provider"] == "google_news_rss"
        ))
        check("publisher source replaces portal source", resolved_daum[0]["source"] == "퍼블리셔 메인")

        outbound_row = fixture_row(
            daum_outbound,
            provider="daum",
            title="포털 아웃바운드 발견",
        )
        resolved_outbound = [outbound_row]
        live_collector.resolve_publisher_urls(
            resolved_outbound,
            strict=True,
            resolver=resolver,
            opener=opener,
            decoder=lambda _url: None,
        )
        check("Daum outbound publisher link resolution succeeds", (
            resolved_outbound[0]["url"] == publisher_outbound
            and resolved_outbound[0]["publisher_direct"] is True
        ))

        missing_rows = [
            fixture_row(daum_missing, provider="daum", title="본문 없는 포털"),
            fixture_row(naver_missing, provider="naver_news_api", title="원문 없는 네이버"),
        ]
        live_collector.resolve_publisher_urls(
            missing_rows,
            strict=True,
            resolver=resolver,
            opener=opener,
            decoder=lambda _url: None,
        )
        check("unresolved portal rows are quarantined", all(row["quarantine"] for row in missing_rows))
        check("quarantine retains raw discovery provenance", all(row["discovery_url"] for row in missing_rows))
        check("quarantine is never delivery eligible", all(
            not publisher_direct.is_publisher_direct_delivery_eligible(
                row, relevance_qualified=True
            )
            for row in missing_rows
        ))

        private_rows = [
            fixture_row(daum_private, provider="daum", title="private redirect"),
        ]
        live_collector.resolve_publisher_urls(
            private_rows,
            strict=True,
            resolver=resolver,
            opener=opener,
            decoder=lambda _url: None,
        )
        check("portal private redirect is quarantined", (
            private_rows[0]["quarantine"]
            and "REDIRECT_REJECTED" in private_rows[0]["portal_resolution_reason"]
        ))

        loop_rows = [
            fixture_row(daum_loop_a, provider="daum", title="redirect loop"),
        ]
        live_collector.resolve_publisher_urls(
            loop_rows,
            strict=True,
            resolver=resolver,
            opener=opener,
            decoder=lambda _url: None,
        )
        check("portal redirect loop is quarantined", (
            loop_rows[0]["quarantine"]
            and "REDIRECT_REJECTED" in loop_rows[0]["portal_resolution_reason"]
        ))

        google_rows = [
            fixture_row(
                google_discovery,
                provider="google_news_rss",
                title="Google 발견 AI 인프라 기사",
            )
        ]
        live_collector.resolve_publisher_urls(
            google_rows,
            strict=True,
            resolver=resolver,
            opener=opener,
            decoder=lambda _url: publisher_google,
        )
        check("Google discovery resolves to publisher page", google_rows[0]["url"] == publisher_google)
        check("Google discovery provenance is preserved", (
            google_rows[0]["discovery_url"] == google_discovery
            and "google_news_decoder" in google_rows[0]["portal_resolution_reason"]
        ))

        canonical_rows = [
            fixture_row(
                "https://canonical.fixture.test/news/old",
                provider="publisher_direct_rss",
                title="old canonical",
            )
        ]
        live_collector.resolve_publisher_urls(
            canonical_rows,
            strict=True,
            resolver=resolver,
            opener=opener,
            decoder=lambda _url: None,
        )
        check("publisher canonical change is applied", canonical_rows[0]["url"] == publisher_canonical)

        duplicate_portal = fixture_row(
            daum_canonical,
            provider="daum",
            title="현대건설 스마트건설 AI 통합 플랫폼 발표",
        )
        duplicate_direct = fixture_row(
            publisher_duplicate,
            provider="publisher_direct_rss",
            title="현대건설 스마트건설 AI 통합 플랫폼 발표",
        )
        # Point portal discovery at the same final canonical for cluster verification.
        routes[daum_canonical]["body"] = (
            '<html><head><link rel="canonical" '
            f'href="{publisher_duplicate}"></head><body>portal</body></html>'
        )
        duplicates = [duplicate_portal, duplicate_direct]
        live_collector.resolve_publisher_urls(
            duplicates,
            strict=True,
            resolver=resolver,
            opener=opener,
            decoder=lambda _url: None,
        )
        merged = collector.merge_provider_articles(duplicates)
        unique, duplicate_quarantine = publisher_direct.partition_delivery_articles(
            merged,
            relevance_qualified=True,
        )
        check("publisher canonical dedup collapses portal/direct duplicate", (
            len(unique) == 1 and duplicate_quarantine == []
        ))
        check("canonical dedup keeps provider cluster provenance", (
            set(unique[0]["source_metadata"]["provider"].split("+"))
            == {"daum", "publisher_direct_rss"}
        ))
        check("canonical dedup preserves portal discovery URL", (
            daum_canonical in unique[0]["source_metadata"].get("discovery_urls", [])
        ))

        naver_payload = {
            "items": [
                {
                    "title": "<b>현대건설</b> AI 데이터센터 투자",
                    "originallink": publisher_main,
                    "link": "https://n.news.naver.com/mnews/article/001/002",
                    "description": "스마트건설 AI 투자 확정",
                    "pubDate": "Thu, 31 Jul 2026 06:00:00 +0900",
                },
                {
                    "title": "네이버 originallink 없음",
                    "originallink": "",
                    "link": naver_missing,
                    "description": "AI 데이터센터",
                    "pubDate": "Thu, 31 Jul 2026 06:00:00 +0900",
                },
            ]
        }
        parsed_naver = naver_news_provider.parse_response(
            naver_payload,
            "fixture",
            "2026-07-31T06:10:00+09:00",
            {"publisher.fixture.test": "퍼블리셔 메인"},
            10,
        )
        check("Naver originallink is retained as an unverified candidate", (
            parsed_naver[0]["url"] == publisher_main
            and parsed_naver[0]["publisher_direct"] is False
        ))
        check("Naver missing originallink stays portal discovery", (
            parsed_naver[1]["url"] == naver_missing
            and parsed_naver[1]["publisher_direct"] is False
        ))
        check("unverified direct-looking URL is not delivery eligible", (
            not publisher_direct.is_publisher_direct_delivery_eligible(
                parsed_naver[0], relevance_qualified=True
            )
        ))
        verified_naver = copy.deepcopy(parsed_naver)
        check("Naver originallink publisher page is refetched and verified", (
            live_collector.resolve_publisher_urls(
                verified_naver,
                strict=True,
                resolver=resolver,
                opener=opener,
                decoder=lambda _url: None,
            ) == 1
            and verified_naver[0]["publisher_direct"] is True
            and verified_naver[0]["url"] == publisher_main
        ))
        naver_partition, naver_quarantine = publisher_direct.partition_delivery_articles(
            verified_naver,
            relevance_qualified=True,
        )
        check("Naver originallink policy allows only direct row", (
            len(naver_partition) == 1 and len(naver_quarantine) == 1
        ))

        check("shortener is never final authority", (
            publisher_direct.portal_provider("https://bit.ly/abc") == "shortener"
            and not publisher_direct.normalize_publisher_canonical_url("https://bit.ly/abc")
        ))
        check("private literal is never final authority", (
            not publisher_direct.normalize_publisher_canonical_url("http://10.0.0.2/article")
        ))

        official_press = {
            "title": "국토부 건설현장 AI 안전 정책 확정",
            "source": "국토교통부",
            "published_at": "2026-07-31T06:00:00+09:00",
            "url": "https://molit.fixture.test/news/official",
            "publisher_direct": True,
        }
        company_newsroom = {
            "title": "현대건설 스마트건설 로봇 기술 도입",
            "source": "현대건설 뉴스룸",
            "published_at": "2026-07-31T06:00:00+09:00",
            "url": "https://newsroom.hdec.fixture.test/news/robot",
            "publisher_direct": True,
        }
        consumer_gossip = {
            "title": "AI 스마트폰 루머 셀카 소비자 가십",
            "source": "Consumer Fixture",
            "published_at": "2026-07-31T06:00:00+09:00",
            "url": "https://consumer.fixture.test/gossip",
            "publisher_direct": True,
        }
        check("official government press release is eligible", (
            publisher_direct.is_publisher_direct_delivery_eligible(official_press)
        ))
        check("official company newsroom is eligible", (
            publisher_direct.is_publisher_direct_delivery_eligible(company_newsroom)
        ))
        check("low relevance consumer AI gossip is not eligible", (
            not publisher_direct.is_publisher_direct_delivery_eligible(consumer_gossip)
        ))

        delivery_rows = [
            resolved_direct[0],
            resolved_daum[0],
            resolved_outbound[0],
            google_rows[0],
            canonical_rows[0],
        ]
        selected, quarantined = publisher_direct.partition_delivery_articles(
            delivery_rows + missing_rows + private_rows + loop_rows
        )
        check("common delivery partition excludes all quarantine", (
            len(selected) == 5 and len(quarantined) == 4
        ))
        check("common delivery output contains zero portal URLs", all(
            publisher_direct.portal_provider(row["url"]) == "" for row in selected
        ))

        saved_collector_leaves = (
            live_collector.fetch_publisher_direct_sources,
            live_collector.fetch_all,
            live_collector.resolve_publisher_urls,
            naver_news_provider.fetch,
            thebell_watch.extract_candidates,
            collector._ingest,  # noqa: SLF001
        )
        ingested_delivery_rows: list[dict] = []
        ingested_quarantine_rows: list[dict] = []

        def fixture_strict_resolve(rows, *_args, **_kwargs):
            verified = copy.deepcopy(resolved_direct[0])
            quarantined_portal = publisher_direct.quarantine_article(
                rows[1],
                "fixture_original_not_found",
            )
            rows[0].clear()
            rows[0].update(verified)
            rows[1].clear()
            rows[1].update(quarantined_portal)
            return 1

        try:
            def fixture_direct_sources(**kwargs):
                audit = kwargs.get("source_audit")
                if audit is not None:
                    audit.append({
                        "provider": "publisher_direct_rss",
                        "source_id": "fixture",
                        "status": "ok",
                        "fetched_count": 1,
                    })
                return copy.deepcopy(direct_rows[:1])

            def fixture_google_sources(**kwargs):
                audit = kwargs.get("query_audit")
                if audit is not None:
                    audit.append({
                        "provider": "google_news_rss",
                        "group": "fixture",
                        "query": "fixture",
                        "status": "ok",
                        "fetched_count": 1,
                        "added_count": 1,
                        "pass": "main",
                    })
                return [
                    fixture_row(
                        daum_missing,
                        provider="google_news_rss",
                        title="포털 원문 미해소 AI 기사",
                    )
                ]

            live_collector.fetch_publisher_direct_sources = fixture_direct_sources
            live_collector.fetch_all = fixture_google_sources
            live_collector.resolve_publisher_urls = fixture_strict_resolve
            naver_news_provider.fetch = lambda **_kwargs: {
                "provider": "naver_news_api",
                "status": "disabled",
                "articles": [],
                "queries_attempted": 0,
                "queries_ok": 0,
                "credentials_present": False,
            }
            thebell_watch.extract_candidates = lambda _rows: []

            def fixture_ingest(rows, signal_origin):
                target = (
                    ingested_quarantine_rows
                    if signal_origin == "Live RSS Quarantine"
                    else ingested_delivery_rows
                )
                target.extend(copy.deepcopy(rows))
                return len(rows), len(rows), len(rows)

            collector._ingest = fixture_ingest  # noqa: SLF001
            production_result = collector._run_live()  # noqa: SLF001
        finally:
            (
                live_collector.fetch_publisher_direct_sources,
                live_collector.fetch_all,
                live_collector.resolve_publisher_urls,
                naver_news_provider.fetch,
                thebell_watch.extract_candidates,
                collector._ingest,  # noqa: SLF001
            ) = saved_collector_leaves

        check("production collector prioritizes direct registry source", (
            production_result["news_source"].startswith("Publisher Direct RSS")
        ))
        check("production collector ingests only verified publisher rows", (
            len(ingested_delivery_rows) == 1
            and ingested_delivery_rows[0]["publisher_direct"] is True
            and ingested_delivery_rows[0]["url"] == official_direct
        ))
        check("production collector preserves unresolved portal quarantine", (
            production_result["publisher_direct_quarantine_count"] == 1
            and len(ingested_quarantine_rows) == 1
            and production_result["publisher_direct_quarantine_inserted"] == 1
            and production_result["publisher_direct_quarantine"][0]["discovery_url"]
            == daum_missing
        ))
        check("production collector final portal URL counter is zero", (
            production_result["external_delivery_portal_urls"] == 0
        ))
        check("production collector reports healthy-with-articles explicitly", (
            production_result["collection_status"]
            == collector.LIVE_HEALTHY_WITH_ARTICLES
            and production_result["collector_health"]["status"]
            == collector.LIVE_HEALTHY_WITH_ARTICLES
            and production_result["collector_successful_source_count"] == 2
        ))

        saved_health_leaves = (
            live_collector.fetch_publisher_direct_sources,
            live_collector.fetch_all,
            live_collector.resolve_publisher_urls,
            naver_news_provider.fetch,
            thebell_watch.extract_candidates,
            collector._ingest,  # noqa: SLF001
        )

        def empty_direct_healthy(**kwargs):
            audit = kwargs.get("source_audit")
            if audit is not None:
                audit.append({
                    "provider": "publisher_direct_rss",
                    "source_id": "fixture",
                    "status": "empty",
                    "fetched_count": 0,
                })
            return []

        def one_google_healthy(**kwargs):
            audit = kwargs.get("query_audit")
            if audit is not None:
                audit.append({
                    "provider": "google_news_rss",
                    "group": "fixture",
                    "query": "fixture",
                    "status": "ok",
                    "fetched_count": 1,
                    "added_count": 1,
                    "pass": "main",
                })
            return [fixture_row(
                daum_missing,
                provider="google_news_rss",
                title="검증 원문 없는 정상 수집 후보",
            )]

        def quarantine_all(rows, *_args, **_kwargs):
            for index, row in enumerate(list(rows)):
                rows[index] = publisher_direct.quarantine_article(
                    row,
                    "fixture_original_not_found",
                )
            return 0

        try:
            live_collector.fetch_publisher_direct_sources = empty_direct_healthy
            live_collector.fetch_all = one_google_healthy
            live_collector.resolve_publisher_urls = quarantine_all
            naver_news_provider.fetch = lambda **_kwargs: {
                "provider": "naver_news_api",
                "status": "disabled",
                "articles": [],
                "queries_attempted": 0,
                "queries_ok": 0,
                "credentials_present": False,
            }
            thebell_watch.extract_candidates = lambda _rows: []
            collector._ingest = (  # noqa: SLF001
                lambda rows, _origin: (len(rows), len(rows), len(rows))
            )
            healthy_zero_result = collector._run_live()  # noqa: SLF001

            def failed_direct(**kwargs):
                audit = kwargs.get("source_audit")
                if audit is not None:
                    audit.append({
                        "provider": "publisher_direct_rss",
                        "source_id": "fixture",
                        "status": "error",
                        "fetched_count": 0,
                    })
                return []

            def failed_google(**kwargs):
                audit = kwargs.get("query_audit")
                if audit is not None:
                    audit.append({
                        "provider": "google_news_rss",
                        "group": "fixture",
                        "query": "fixture",
                        "status": "error",
                        "fetched_count": 0,
                        "added_count": 0,
                        "pass": "main",
                    })
                return []

            live_collector.fetch_publisher_direct_sources = failed_direct
            live_collector.fetch_all = failed_google
            naver_news_provider.fetch = lambda **_kwargs: {
                "provider": "naver_news_api",
                "status": "error",
                "articles": [],
                "queries_attempted": 1,
                "queries_ok": 0,
                "credentials_present": True,
            }
            failed_result = collector._run_live()  # noqa: SLF001
        finally:
            (
                live_collector.fetch_publisher_direct_sources,
                live_collector.fetch_all,
                live_collector.resolve_publisher_urls,
                naver_news_provider.fetch,
                thebell_watch.extract_candidates,
                collector._ingest,  # noqa: SLF001
            ) = saved_health_leaves

        check("healthy zero remains live and never falls back to fixture", (
            healthy_zero_result["collection_status"]
            == collector.LIVE_HEALTHY_NO_ELIGIBLE_ARTICLES
            and healthy_zero_result["news_data_mode"] == "live"
            and healthy_zero_result["fallback_used"] is False
            and healthy_zero_result["publisher_direct_eligible_count"] == 0
        ))
        check("all-source failure is explicit fallback rejection", (
            failed_result["collection_status"] == collector.LIVE_FALLBACK_REJECTED
            and failed_result["news_data_mode"] == "mock"
            and failed_result["fallback_used"] is True
            and failed_result["collector_successful_source_count"] == 0
            and failed_result["collection_failure_category"]
            == "all_live_sources_failed"
        ))
        failed_same_publisher = publisher_direct.quarantine_article(
            resolved_daum[0],
            "temporary_publisher_verification_failure",
        )
        quarantine_storage = collector._to_article_row(  # noqa: SLF001
            failed_same_publisher,
            [],
            "2026-07-31T07:00:00+09:00",
            "Live RSS Quarantine",
        )
        verified_storage = collector._to_article_row(  # noqa: SLF001
            resolved_daum[0],
            [],
            "2026-07-31T07:01:00+09:00",
            "Live RSS",
        )
        check("quarantine identity cannot block later verified canonical", (
            quarantine_storage["id"] != verified_storage["id"]
            and quarantine_storage["status"] == "quarantine"
            and verified_storage["url"] == publisher_main
        ))

        run_at = editorial_briefings.parse_published_at(
            "2026-07-31T07:30:00+09:00"
        )
        daily_articles = editorial_briefings.normalize_articles(
            selected,
            editorial_briefings.daily_coverage(run_at),
            limit=6,
            resolve_images=False,
            selection_mode=editorial_briefings.SELECTION_MODE_DIRECT_AWARE_DAILY,
        )
        daily = editorial_briefings.render_daily(
            daily_articles,
            run_at=run_at,
            root_url="https://brief.fixture.test/HDEC-News-Sensor",
        )
        check("Daily renders at least one publisher-direct article", daily.article_count > 0)
        check("Daily final HTML has zero portal links", (
            publisher_direct.count_portal_urls(daily.html) == 0
            and "news.daum.net" not in daily.html
            and "v.daum.net" not in daily.html
            and "news.google.com" not in daily.html
        ))
        check("Daily selected URLs are publisher canonical", all(
            publisher_direct.normalize_publisher_canonical_url(article.selected_url)
            for article in daily_articles
        ))

        with tempfile.TemporaryDirectory(prefix="d7ak6e-r3v8-db-") as temporary:
            previous_db_path = config.DB_PATH
            previous_macro_mode = config.MACRO_MODE
            config.DB_PATH = Path(temporary) / "publisher-direct.db"
            config.MACRO_MODE = "mock"
            try:
                db.init_db()
                legacy_portal = publisher_direct.quarantine_article(
                    fixture_row(
                        daum_canonical,
                        provider="daum",
                        title=resolved_daum[0]["title"],
                        source=resolved_daum[0]["source"],
                    ),
                    "legacy_portal_original_unresolved",
                )
                collector._ingest(  # noqa: SLF001
                    [legacy_portal],
                    "Live RSS Quarantine",
                )
                collected, deduped, inserted = collector._ingest(  # noqa: SLF001
                    selected,
                    "Live RSS",
                )
                stored_before_scoring = db.fetch_articles_with_scores()
                check("verified canonical inserts beside legacy portal identity", (
                    any(row["status"] == "quarantine" for row in stored_before_scoring)
                    and any(
                        row["url"] == publisher_main
                        for row in stored_before_scoring
                    )
                ))
                score_stats = scoring.score_all()
                insight.generate_all()
                brief = briefing.build_brief(
                    pipeline_counts={
                        "collected": collected,
                        "deduplicated": deduped,
                        "inserted": inserted,
                        "scored": score_stats["scored"],
                        "alert_candidates": score_stats["alert_candidates"],
                    },
                    news_provenance={
                        "news_source": "publisher_direct_fixture",
                        "fallback_used": False,
                        "publisher_direct_quarantine_count": len(quarantined),
                    },
                )
                dashboard_html = build_static_dashboard.render_dashboard_html(
                    brief,
                    market_mode="mock",
                )
                report_html, _sections = build_static_report.render_report_html(brief)
                check("legacy portal quarantine is absent from brief rows", (
                    daum_canonical not in {
                        article["url"]
                        for article in brief.get("articles", [])
                    }
                ))
                original_build = build_telegram_digest.build_brief_via_mock_pipeline
                build_telegram_digest.build_brief_via_mock_pipeline = lambda: brief
                try:
                    telegram_data = build_telegram_digest.build_digest_data()
                    telegram_message = build_telegram_digest.format_digest_message(
                        telegram_data
                    )
                finally:
                    build_telegram_digest.build_brief_via_mock_pipeline = original_build
            finally:
                config.DB_PATH = previous_db_path
                config.MACRO_MODE = previous_macro_mode

        check("Dashboard final portal URLs are zero", (
            portal_hrefs(dashboard_html) == []
        ))
        check("static Daily report final portal URLs are zero", (
            portal_hrefs(report_html) == []
        ))
        check("Telegram payload portal URLs are zero", (
            publisher_direct.count_portal_urls(telegram_data) == 0
            and "news.daum.net" not in telegram_message
            and "news.google.com" not in telegram_message
        ))
        check("brief exposes only aggregate quarantine audit", (
            brief["publisher_direct_delivery"]["quarantine_count"]
            == len(quarantined)
            and brief["publisher_direct_delivery"]["final_portal_urls"] == 0
            and "publisher_direct_quarantine" not in brief
        ))

        direct_team = teams_article(publisher_main)
        portal_team = teams_article(daum_canonical)
        candidates = teams_ai_push.select_teams_push_candidates(
            [direct_team, portal_team],
            max_articles=10,
        )
        check("Teams candidate gate excludes portal article", (
            len(candidates) == 1
            and candidates[0].article["url"] == publisher_main
        ))
        card = teams_ai_push.build_candidate_card(
            {
                "dashboard_url": "https://brief.fixture.test/dashboard",
                "report_url": "https://brief.fixture.test/report",
            },
            candidates[0],
            detected_at="2026-07-31T06:10:00+09:00",
        )
        subject, text_body, html_body = teams_ai_push.render_article_email(
            {
                "dashboard_url": "https://brief.fixture.test/dashboard",
                "report_url": "https://brief.fixture.test/report",
            },
            candidates[0],
            detected_at="2026-07-31T06:10:00+09:00",
        )
        check("Teams Adaptive Card uses publisher URL", (
            publisher_main in json.dumps(card, ensure_ascii=False)
        ))
        check("Teams email title/source/time/publisher URL are present", all(
            value in (subject + text_body + html_body)
            for value in (
                "AI 데이터센터",
                "Fixture Publisher",
                "2026-07-31 06:00",
                publisher_main,
            )
        ))
        check("Teams payload portal URLs are zero", (
            publisher_direct.count_portal_urls(card) == 0
            and publisher_direct.count_portal_urls(text_body) == 0
            and publisher_direct.count_portal_urls(html_body) == 0
        ))
        check("Teams maximum send cap remains ten", teams_ai_push.MAX_TEAMS_ARTICLES == 10)
        check("fixture HTTP activity stayed inside mock opener", (
            len(opener.calls) > 0 and EXTERNAL_NETWORK_CALLS == 0
        ))

    protected_after = {
        path: sha256(ROOT / path) for path in EXPECTED_PROTECTED_SHA256
    }
    check("all workflows remain byte-identical", all(
        protected_after[path] == protected_before[path]
        for path in EXPECTED_PROTECTED_SHA256
        if path.startswith(".github/workflows/")
    ))
    check("Daily template remains byte-identical", (
        protected_after["templates/editorial_daily.html"]
        == protected_before["templates/editorial_daily.html"]
    ))
    check("Daily state remains byte-identical", (
        protected_after["data/editorial_daily_state.json"]
        == protected_before["data/editorial_daily_state.json"]
    ))
    check("Teams state remains byte-identical", (
        protected_after["data/teams_push_state.json"]
        == protected_before["data/teams_push_state.json"]
    ))
    check("external test network calls are zero", EXTERNAL_NETWORK_CALLS == 0)
    check("SMTP attempts are zero", SMTP_ATTEMPTS == 0)
    check("Teams sends are zero", TEAMS_SENDS == 0)
    check("Telegram sends are zero", TELEGRAM_SENDS == 0)

    print(f"PUBLISHER_DIRECT_VERIFIER={PASS}/{FAIL}")
    print(
        "DIRECT_SOURCE_COLLECTION="
        + ("PASS" if FAIL == 0 else "FAIL")
        + " PORTAL_RESOLUTION="
        + ("PASS" if FAIL == 0 else "FAIL")
        + " UNRESOLVED_PORTAL_QUARANTINE="
        + ("PASS" if FAIL == 0 else "FAIL")
    )
    print(
        f"EXTERNAL_TEST_NETWORK_CALLS={EXTERNAL_NETWORK_CALLS} "
        f"SMTP_ATTEMPTS={SMTP_ATTEMPTS} TEAMS_SENDS={TEAMS_SENDS} "
        f"TELEGRAM_SENDS={TELEGRAM_SENDS}"
    )
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
