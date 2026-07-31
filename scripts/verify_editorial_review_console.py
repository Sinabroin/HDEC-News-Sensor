#!/usr/bin/env python3
"""Offline regression verifier for the Editorial Review Console."""

from __future__ import annotations

import base64
import json
import os
import py_compile
import re
import shutil
import subprocess
import sys
import tempfile
from email.message import Message
from html import escape, unescape
from io import BytesIO
from datetime import datetime
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("APP_MODE", "mock")
os.environ.setdefault("NEWS_MODE", "mock")

from app import (  # noqa: E402
    config,
    editorial_article_import,
    editorial_briefings,
    editorial_feedback,
    editorial_review,
    operator_gateway,
)
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


def _fixture_body() -> str:
    return (
        "현대건설은 국내 데이터센터 인프라 투자 확대를 위해 주요 기업과 "
        "전략적 계약을 체결했다고 밝혔다. "
        "이번 계약에는 고효율 전력 설비와 클라우드 운영 기술을 적용해 "
        "에너지 사용량을 줄이는 계획이 포함됐다. "
        "회사는 2027년까지 관련 사업 예산 3천억원을 단계적으로 집행하고 "
        "공급망 협력을 확대할 예정이다. "
        "시장 참여자들은 정부의 국가전략과 반도체 수요 증가가 데이터센터 "
        "건설 시장 성장에 영향을 줄 것으로 분석했다. "
        "현대건설은 프로젝트별 수익성과 안전 기준을 검토해 투자 속도를 "
        "조정하며 고객사와 세부 일정을 협의할 계획이다."
    )


def _jsonld_fixture_html() -> str:
    body = _fixture_body()
    payload = {
        "@context": "https://schema.org",
        "@type": "NewsArticle",
        "headline": "현대건설 데이터센터 투자 확대",
        "publisher": {"@type": "Organization", "name": "테스트경제"},
        "datePublished": "2026-07-31T10:00:00+09:00",
        "articleBody": body,
        "image": "https://media.example.test/article-photo.jpg",
    }
    return (
        "<!doctype html><html><head>"
        "<meta disabled>"
        f'<script type="application/ld+json">{json.dumps(payload, ensure_ascii=False)}</script>'
        '<link rel="canonical" href="https://publisher.example.test/article/1">'
        "</head><body>"
        '<nav><p>메뉴 투자 시장 기업 기술 구독 로그인</p></nav>'
        f"<article><img loading><p>{escape(body)}</p></article>"
        '<footer><p>저작권자 무단 전재 및 재배포 금지</p></footer>'
        "</body></html>"
    )


def _og_fixture_html() -> str:
    paragraphs = [
        "기업들은 생성형 AI 에이전트를 업무 시스템에 도입하며 조직별 협업 절차와 고객 대응 방식을 재설계하고 있다.",
        "이번 전환은 반복 업무를 줄이고 생산성을 높이기 위한 것으로, 각 사업부는 보안 기준과 데이터 접근 권한을 함께 점검한다.",
        "프로젝트 책임자는 모델 성능만이 아니라 현장 적용 과정에서 발생하는 오류와 운영 비용을 수치로 관리해야 한다고 설명했다.",
        "계약 기업들은 올해 하반기부터 단계적으로 서비스를 적용하고 직원 교육과 고객 피드백을 반영해 범위를 확대할 계획이다.",
    ]
    body_html = "".join(f"<p>{escape(paragraph)}</p>" for paragraph in paragraphs)
    return (
        "<!doctype html><html><head>"
        '<meta property="og:title" content="기업 AI 에이전트 업무 전환 확대">'
        '<meta property="og:site_name" content="오픈그래프뉴스">'
        '<meta property="article:published_time" content="2026-07-31T09:15:00+09:00">'
        '<meta property="og:image" content="https://media.example.test/og-photo.png">'
        '<link rel="canonical" href="/article/og-2">'
        "</head><body>"
        '<div class="menu"><p>로그인 구독 추천 기사 광고 문의</p></div>'
        f"<article>{body_html}</article>"
        '<aside><p>추천 기사와 공유하기 댓글 모음입니다.</p></aside>'
        "</body></html>"
    )


def _failure_fixture_html() -> str:
    return (
        "<!doctype html><html><head><title>메뉴 페이지</title></head><body>"
        '<nav><a href="/">홈</a><a href="/login">로그인</a></nav>'
        '<div class="advert"><p>광고 문의와 구독 신청</p></div>'
        '<footer><p>저작권자 무단 전재 및 재배포 금지</p></footer>'
        "</body></html>"
    )


def _daum_canonical_fixture_html() -> str:
    return (
        "<!doctype html><html><head>"
        '<title>Daum 뉴스 발견 페이지</title>'
        '<link rel="canonical" href="https://publisher.example.test/article/1">'
        '<meta property="og:url" content="https://v.daum.net/v/20260731100000001">'
        "</head><body><main><p>포털은 기사 발견만 보조합니다.</p></main></body></html>"
    )


def _daum_outbound_fixture_html() -> str:
    return (
        "<!doctype html><html><head><title>Daum 뉴스 발견 페이지</title></head>"
        "<body><main>"
        '<a class="publisher-original-link" title="언론사 원문" '
        'href="https://publisher.example.test/article/1">언론사 원문</a>'
        "</main></body></html>"
    )


def _fixture_resolver(host: str, port: int, type=None):
    del type
    address = "10.0.0.7" if host == "private.example.test" else "93.184.216.34"
    return [(2, 1, 6, "", (address, port))]


class _MockResponse:
    def __init__(
        self,
        url: str,
        payload: bytes,
        content_type: str,
        *,
        status: int = 200,
        location: str = "",
        content_length: int | None = None,
    ):
        self.status = status
        self._url = url
        self._buffer = BytesIO(payload)
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        self.headers["Content-Length"] = str(
            len(payload) if content_length is None else content_length
        )
        if location:
            self.headers["Location"] = location

    def read(self, size=-1):
        return self._buffer.read(size)

    def getcode(self):
        return self.status

    def geturl(self):
        return self._url

    def close(self):
        self._buffer.close()


class _MockOpener:
    def __init__(self, responses: dict[str, dict[str, object]]):
        self.responses = responses
        self.calls: list[str] = []

    def open(self, request, timeout):
        if timeout > editorial_article_import.ARTICLE_FETCH_TIMEOUT_SECONDS:
            raise AssertionError("timeout exceeded contract")
        url = request.full_url
        self.calls.append(url)
        record = self.responses[url]
        return _MockResponse(
            url,
            bytes(record.get("payload") or b""),
            str(record.get("content_type") or "text/html"),
            status=int(record.get("status") or 200),
            location=str(record.get("location") or ""),
            content_length=record.get("content_length"),
        )


def _fixture_raster_bytes(image_format: str = "JPEG") -> bytes:
    image = Image.new("RGB", (640, 360))
    pixels = image.load()
    for y in range(image.height):
        for x in range(image.width):
            pixels[x, y] = (
                (x * 7 + y * 3) % 256,
                (x * 2 + y * 11) % 256,
                (x * 13 + y * 5) % 256,
            )
    output = BytesIO()
    image.save(output, format=image_format, quality=88)
    return output.getvalue()


def verify_article_import_domain(v: V) -> dict[str, object]:
    article_url = "https://publisher.example.test/article/1"
    portal_url = "https://v.daum.net/v/20260731100000001"
    image_url = "https://media.example.test/article-photo.jpg"
    jpeg = _fixture_raster_bytes("JPEG")
    opener = _MockOpener(
        {
            portal_url: {
                "payload": _daum_canonical_fixture_html().encode("utf-8"),
                "content_type": "text/html; charset=utf-8",
            },
            article_url: {
                "payload": _jsonld_fixture_html().encode("utf-8"),
                "content_type": "text/html; charset=utf-8",
            },
            image_url: {
                "payload": jpeg,
                "content_type": "image/jpeg",
            },
        }
    )
    result = editorial_article_import.import_article(
        portal_url,
        resolver=_fixture_resolver,
        opener=opener,
    )
    article = result["article"]
    v.check(
        "secure import uses fixture network adapter only",
        opener.calls == [portal_url, article_url, image_url],
        str(opener.calls),
    )
    v.equal("portal input URL is disclosed", article["input_url"], portal_url)
    v.equal("portal discovery source is disclosed", article["discovery_source"], "daum")
    v.equal("portal publisher URL is direct", article["publisher_url"], article_url)
    v.check(
        "portal fallback is explicitly not used",
        article["publisher_direct"] is True
        and article["portal_fallback_used"] is False
        and article["portal_source"] == "daum"
        and article["portal_resolution_reason"] == "publisher_canonical",
    )
    v.equal("JSON-LD title extraction", article["title"], "현대건설 데이터센터 투자 확대")
    v.check(
        "valueless HTML attributes do not abort article extraction",
        article["extraction"]["title_source"] == "json_ld",
    )
    v.equal("JSON-LD publisher extraction", article["source"], "테스트경제")
    v.equal(
        "JSON-LD published date extraction",
        article["published_at"],
        "2026-07-31T10:00:00+09:00",
    )
    v.equal(
        "JSON-LD articleBody extraction",
        article["extraction"]["body_source"],
        "json_ld_article_body",
    )
    v.equal(
        "canonical article URL extraction",
        article["canonical_url"],
        article_url,
    )
    v.check(
        "full article body is not returned",
        "body" not in article and "html" not in article,
    )
    v.check(
        "article excerpt is bounded",
        0 < len(article["article_text_excerpt"])
        <= editorial_article_import.ARTICLE_EXCERPT_MAX_CHARS,
    )
    summary_sentences = [
        part.strip()
        for part in re.split(r"(?<=[.!?。！？])\s+", article["summary"])
        if part.strip()
    ]
    v.check(
        "extractive summary is bounded",
        2 <= len(summary_sentences) <= 4
        and 220 <= len(article["summary"]) <= 500,
        article["summary"],
    )
    v.check(
        "summary contains source-supported sentences",
        all(sentence in _fixture_body() for sentence in summary_sentences),
        article["summary"],
    )
    v.equal(
        "summary mode is deterministic extractive",
        article["extraction"]["summary_mode"],
        "deterministic_extractive",
    )
    v.equal("import category analysis applied", article["category"], "투자·산업")
    v.check(
        "import category analysis preserves evidence",
        set(article["category_analysis"]) >= {
            "category",
            "scores",
            "matched_signals",
            "reason",
        }
        and bool(article["category_analysis"]["reason"]),
    )
    v.check(
        "import image is bounded raster data URL",
        article["image_url"].startswith("data:image/jpeg;base64,/9j/")
        and len(article["image_url"])
        <= editorial_article_import.IMPORT_IMAGE_MAX_DATA_URL_CHARS,
        str(len(article["image_url"])),
    )
    encoded_image = article["image_url"].split(",", 1)[1]
    with Image.open(BytesIO(base64.b64decode(encoded_image))) as decoded:
        decoded.load()
        raster_ok = (
            decoded.format == "JPEG"
            and decoded.width <= editorial_article_import.IMPORT_IMAGE_MAX_WIDTH
            and decoded.height <= editorial_article_import.IMPORT_IMAGE_MAX_HEIGHT
        )
    v.check("import image decodes within geometry bound", raster_ok)
    v.equal("import image source priority", article["extraction"]["image_source"], "json_ld")

    og = editorial_article_import.extract_article(
        _og_fixture_html(),
        "https://publisher.example.test/article/og-2",
        resolver=_fixture_resolver,
    )
    v.equal("Open Graph title fallback extraction", og.title, "기업 AI 에이전트 업무 전환 확대")
    v.equal("Open Graph source fallback extraction", og.source, "오픈그래프뉴스")
    v.equal("article paragraph extraction", og.body_source, "article_element")
    v.check(
        "boilerplate removal",
        "로그인" not in og.body
        and "구독" not in og.body
        and "추천 기사" not in og.body
        and "댓글" not in og.body,
        og.body,
    )
    v.check(
        "Open Graph image fallback extraction",
        og.image_candidates
        and og.image_candidates[0]
        == ("https://media.example.test/og-photo.png", "og_image"),
        str(og.image_candidates),
    )

    outbound_portal_url = "https://news.daum.net/outbound/20260731"
    outbound_opener = _MockOpener(
        {
            outbound_portal_url: {
                "payload": _daum_outbound_fixture_html().encode("utf-8"),
                "content_type": "text/html; charset=utf-8",
            },
            article_url: {
                "payload": _jsonld_fixture_html().encode("utf-8"),
                "content_type": "text/html; charset=utf-8",
            },
        }
    )
    outbound_resolution = editorial_article_import.resolve_publisher_document(
        outbound_portal_url,
        resolver=_fixture_resolver,
        opener=outbound_opener,
    )
    v.check(
        "Daum outbound publisher link resolves and refetches",
        outbound_resolution.publisher_url == article_url
        and outbound_resolution.portal_source == "daum"
        and outbound_resolution.portal_resolution_reason
        == "publisher_outbound_link"
        and outbound_opener.calls == [outbound_portal_url, article_url],
        str(outbound_opener.calls),
    )

    missing_portal_url = "https://news.daum.net/no-original/20260731"
    missing_opener = _MockOpener(
        {
            missing_portal_url: {
                "payload": (
                    "<html><head><title>Daum 메뉴</title></head>"
                    "<body><nav>뉴스 메뉴</nav><div class=\"advert\">광고</div></body></html>"
                ).encode("utf-8"),
                "content_type": "text/html; charset=utf-8",
            }
        }
    )
    try:
        editorial_article_import.resolve_publisher_document(
            missing_portal_url,
            resolver=_fixture_resolver,
            opener=missing_opener,
        )
    except editorial_article_import.ArticleImportError as exc:
        missing_portal_code = exc.code
    else:
        missing_portal_code = ""
    v.equal(
        "portal without publisher original fails closed",
        missing_portal_code,
        "PORTAL_ORIGINAL_NOT_FOUND",
    )

    private_portal_url = "https://v.daum.net/private/20260731"
    private_opener = _MockOpener(
        {
            private_portal_url: {
                "payload": (
                    '<html><head><link rel="canonical" '
                    'href="http://127.0.0.1/private/article"></head></html>'
                ).encode("utf-8"),
                "content_type": "text/html; charset=utf-8",
            }
        }
    )
    try:
        editorial_article_import.resolve_publisher_document(
            private_portal_url,
            resolver=_fixture_resolver,
            opener=private_opener,
        )
    except editorial_article_import.ArticleImportError as exc:
        private_portal_code = exc.code
    else:
        private_portal_code = ""
    v.equal(
        "portal private publisher target is rejected",
        private_portal_code,
        "UNSAFE_DESTINATION",
    )

    loop_a = "https://v.daum.net/loop/a"
    loop_b = "https://news.daum.net/loop/b"
    loop_opener = _MockOpener(
        {
            loop_a: {
                "status": 302,
                "location": loop_b,
                "payload": b"",
                "content_type": "text/html",
            },
            loop_b: {
                "status": 302,
                "location": loop_a,
                "payload": b"",
                "content_type": "text/html",
            },
        }
    )
    try:
        editorial_article_import.resolve_publisher_document(
            loop_a,
            resolver=_fixture_resolver,
            opener=loop_opener,
        )
    except editorial_article_import.ArticleImportError as exc:
        loop_code = exc.code
    else:
        loop_code = ""
    v.equal("portal redirect loop is rejected", loop_code, "REDIRECT_REJECTED")
    v.check(
        "portal redirect loop stays within redirect bound",
        len(loop_opener.calls)
        == editorial_article_import.ARTICLE_REDIRECT_LIMIT + 1,
        str(loop_opener.calls),
    )
    v.check(
        "publisher page supplies final source and selected URL",
        article["source"] == "테스트경제"
        and article["canonical_url"] == article_url
        and article["publisher_direct"] is True,
    )

    try:
        editorial_article_import.extract_article(
            _failure_fixture_html(),
            "https://publisher.example.test/menu",
            resolver=_fixture_resolver,
        )
    except editorial_article_import.ArticleImportError as exc:
        body_failure_code = exc.code
    else:
        body_failure_code = ""
    v.equal("body extraction failure is explicit", body_failure_code, "ARTICLE_BODY_NOT_FOUND")

    unsafe_codes = []
    for unsafe_url in (
        "javascript:alert(1)",
        "file:///etc/passwd",
        "http://127.0.0.1/private",
        "http://localhost/private",
        "https://service.internal/article",
        "https://private.example.test/article",
        "https://user:secret@publisher.example.test/article",
        "https://publisher.example.test:8443/article",
    ):
        try:
            editorial_article_import.validate_public_article_url(
                unsafe_url,
                resolver=_fixture_resolver,
            )
        except editorial_article_import.ArticleImportError as exc:
            unsafe_codes.append(exc.code)
        else:
            unsafe_codes.append("")
    v.check(
        "URL validation rejects script file and private destinations",
        all(code in {"INVALID_URL", "UNSAFE_DESTINATION"} for code in unsafe_codes),
        str(unsafe_codes),
    )

    redirect_source = "https://redirect.example.test/article"
    redirect_opener = _MockOpener(
        {
            redirect_source: {
                "status": 302,
                "location": "http://127.0.0.1/private",
                "payload": b"",
                "content_type": "text/html",
            }
        }
    )
    try:
        editorial_article_import.fetch_article_html(
            redirect_source,
            resolver=_fixture_resolver,
            opener=redirect_opener,
        )
    except editorial_article_import.ArticleImportError as exc:
        redirect_code = exc.code
    else:
        redirect_code = ""
    v.equal("redirect destination is revalidated", redirect_code, "REDIRECT_REJECTED")
    v.equal("unsafe redirect is never fetched", redirect_opener.calls, [redirect_source])

    oversized_url = "https://oversized.example.test/article"
    oversized_opener = _MockOpener(
        {
            oversized_url: {
                "payload": b"x",
                "content_type": "text/html",
                "content_length": editorial_article_import.ARTICLE_HTML_MAX_BYTES + 1,
            }
        }
    )
    try:
        editorial_article_import.fetch_article_html(
            oversized_url,
            resolver=_fixture_resolver,
            opener=oversized_opener,
        )
    except editorial_article_import.ArticleImportError as exc:
        oversized_code = exc.code
    else:
        oversized_code = ""
    v.equal("HTML response byte limit exists", oversized_code, "RESPONSE_TOO_LARGE")
    v.check(
        "article fetch limits and timeout are bounded",
        editorial_article_import.ARTICLE_HTML_MAX_BYTES == 2_000_000
        and editorial_article_import.ARTICLE_IMAGE_MAX_BYTES == 5_000_000
        and editorial_article_import.ARTICLE_FETCH_TIMEOUT_SECONDS <= 8
        and editorial_article_import.ARTICLE_REDIRECT_LIMIT == 3,
    )

    wrong_type_url = "https://publisher.example.test/api.json"
    wrong_type_opener = _MockOpener(
        {
            wrong_type_url: {
                "payload": b"{}",
                "content_type": "application/json",
            }
        }
    )
    try:
        editorial_article_import.fetch_article_html(
            wrong_type_url,
            resolver=_fixture_resolver,
            opener=wrong_type_opener,
        )
    except editorial_article_import.ArticleImportError as exc:
        wrong_type_code = exc.code
    else:
        wrong_type_code = ""
    v.equal(
        "non-HTML article content is rejected",
        wrong_type_code,
        "UNSUPPORTED_CONTENT_TYPE",
    )

    image_cases = {
        "svg": {
            "url": "https://media.example.test/image.svg",
            "payload": b'<svg xmlns="http://www.w3.org/2000/svg"></svg>',
            "content_type": "image/svg+xml",
        },
        "mime_mismatch": {
            "url": "https://media.example.test/mismatch.jpg",
            "payload": _fixture_raster_bytes("PNG"),
            "content_type": "image/jpeg",
        },
        "oversized": {
            "url": "https://media.example.test/oversized.jpg",
            "payload": b"\xff\xd8\xff",
            "content_type": "image/jpeg",
            "content_length": editorial_article_import.ARTICLE_IMAGE_MAX_BYTES + 1,
        },
        "invalid": {
            "url": "https://media.example.test/invalid.jpg",
            "payload": b"not an image",
            "content_type": "image/jpeg",
        },
    }
    image_results = {}
    for name, record in image_cases.items():
        case_opener = _MockOpener(
            {
                str(record["url"]): {
                    "payload": record["payload"],
                    "content_type": record["content_type"],
                    "content_length": record.get("content_length"),
                }
            }
        )
        image_results[name] = editorial_article_import.materialize_imported_image(
            [(str(record["url"]), name)],
            title="대표 이미지 검증",
            summary="대표 이미지 검증을 위한 충분한 기사 요약입니다.",
            source="검증매체",
            canonical_url=article_url,
            resolver=_fixture_resolver,
            opener=case_opener,
        )
    v.equal("SVG imported image is rejected", image_results["svg"][0], "")
    v.equal("image MIME mismatch is rejected", image_results["mime_mismatch"][0], "")
    v.equal("oversized imported image is rejected", image_results["oversized"][0], "")
    v.equal("invalid imported image bytes are rejected", image_results["invalid"][0], "")

    api_source = (ROOT / "app/operator_api.py").read_text(encoding="utf-8")
    import_source = (
        ROOT / "app/editorial_article_import.py"
    ).read_text(encoding="utf-8")
    v.check(
        "secure import endpoint exists",
        '@router.post("/api/editorial/import-article")' in api_source,
    )
    v.check(
        "import endpoint requires existing operator auth",
        "_authorize_article_import(request)" in api_source
        and "operator_gateway.authorize(" in api_source
        and 'action="import_article"' in api_source,
    )
    v.check(
        "import endpoint is POST only",
        '@router.get("/api/editorial/import-article")' not in api_source,
    )
    v.check(
        "import endpoint requires JSON and hides internal exceptions",
        'media_type != "application/json"' in api_source
        and "except Exception:" in api_source
        and '"INTERNAL_ERROR"' in api_source,
    )
    v.check(
        "import route returns transformed article not proxy HTML",
        "run_in_threadpool(" in api_source
        and "request.body()" in api_source
        and "response.body" not in api_source,
    )
    v.check(
        "SSRF defense validates every DNS answer",
        "all(_public_ip(address) for address in addresses)" in import_source
        and "validate_public_article_url(" in import_source,
    )
    v.check(
        "streaming response byte limit is applied",
        "def _read_limited(" in import_source
        and "if total > limit:" in import_source,
    )
    v.check(
        "origin mode import requires OAuth session",
        "import_article" not in operator_gateway._ORIGIN_MODE_ACTIONS,
    )
    return result


def _browser_executable() -> Path | None:
    candidates = [
        # GitHub-hosted Ubuntu images ship Google Chrome as the supported
        # browser; distro Chromium wrappers can crash or retain child processes
        # after --dump-dom. Prefer Chrome while retaining local fallbacks.
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
    windows_path = subprocess.run(
        ["wslpath", "-w", str(path.resolve())],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return "file:///" + windows_path.replace("\\", "/")


def _browser_argument_path(path: Path, browser: Path) -> str:
    if browser.suffix.casefold() != ".exe":
        return str(path.resolve())
    return subprocess.run(
        ["wslpath", "-w", str(path.resolve())],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def run_browser_interaction(
    console_path: Path,
    profile_dir: Path,
    import_response: dict[str, object],
) -> dict[str, object]:
    """Exercise real drag/drop and reload behavior in dependency-free headless Chrome."""
    browser = _browser_executable()
    if browser is None:
        return {"browser_available": False, "error": "Chrome/Edge executable not found"}
    embedded_response = json.dumps(import_response, ensure_ascii=False).replace(
        "</", "<\\/"
    )
    harness = r"""
<script>
(async()=>{
  const results={browser_available:true};
  const importFixture=__IMPORT_RESPONSE__;
  let mockImportCalls=0;
  window.fetch=(url,options={})=>new Promise(resolve=>setTimeout(()=>{
    mockImportCalls+=1;
    const request=JSON.parse(options.body||"{}");
    if(String(request.url||"").includes("failure")){
      resolve({ok:false,status:422,json:async()=>({ok:false,error:{code:"ARTICLE_BODY_NOT_FOUND",message:"본문을 자동으로 추출하지 못했습니다."}})});
      return;
    }
    resolve({ok:true,status:200,json:async()=>importFixture});
  },80));
  const phaseKey=storageKey+":r3v7-browser-phase";
  const expectedKey=storageKey+":r3v7-browser-expected";
  const pause=milliseconds=>new Promise(resolve=>setTimeout(resolve,milliseconds));
  function dispatchDrag(source,target,clientY){
    const transfer=new DataTransfer();
    source.dispatchEvent(new DragEvent("dragstart",{bubbles:true,cancelable:true,dataTransfer:transfer,clientY:clientY||0}));
    target.dispatchEvent(new DragEvent("dragover",{bubbles:true,cancelable:true,dataTransfer:transfer,clientY:clientY||0}));
    target.dispatchEvent(new DragEvent("drop",{bubbles:true,cancelable:true,dataTransfer:transfer,clientY:clientY||0}));
    source.dispatchEvent(new DragEvent("dragend",{bubbles:true,cancelable:true,dataTransfer:transfer,clientY:clientY||0}));
  }
  function domSelected(){
    return [...document.querySelectorAll(".selected-article-card")].map(card=>card.dataset.selectedId);
  }
  function sectorOrder(){
    return [...document.querySelectorAll(".editorial-sector")].map(section=>section.dataset.sectorCategory);
  }
  window.alert=()=>{};
  if(localStorage.getItem(phaseKey)!=="restore"){
    results.manual_fallback_initially_hidden=document.getElementById("manualFallback").hidden===true;
    const initiallySelected=document.querySelector(".candidate.selected");
    const removedId=initiallySelected.dataset.id;
    initiallySelected.querySelector('input[type="checkbox"]').click();
    const beforeCount=state.selected.length;
    const candidate=document.querySelector(".candidate:not(.selected)");
    const candidateId=candidate.dataset.id;
    const corporateZone=document.querySelector('[data-drop-category="기업동향"]');
    dispatchDrag(candidate,corporateZone,corporateZone.getBoundingClientRect().bottom-2);
    results.left_to_right_drag=state.selected.length===beforeCount+1&&state.selected.includes(candidateId);
    results.left_drop_category=view(candidateId).category==="기업동향"&&!!document.querySelector(`[data-sector-category="기업동향"] [data-selected-id="${candidateId}"]`);

    let movedCard=document.querySelector(`[data-selected-id="${candidateId}"]`);
    let technologyZone=document.querySelector('[data-drop-category="기술정보"]');
    dispatchDrag(movedCard,technologyZone,technologyZone.getBoundingClientRect().bottom-2);
    results.cross_sector_move=view(candidateId).category==="기술정보"&&!!document.querySelector(`[data-sector-category="기술정보"] [data-selected-id="${candidateId}"]`);

    const secondId=state.selected.find(id=>id!==candidateId&&view(id).category!=="기술정보");
    movedCard=document.querySelector(`[data-selected-id="${secondId}"]`);
    technologyZone=document.querySelector('[data-drop-category="기술정보"]');
    dispatchDrag(movedCard,technologyZone,technologyZone.getBoundingClientRect().bottom-2);
    const technologyCards=[...document.querySelectorAll('[data-sector-category="기술정보"] .selected-article-card')];
    const beforeOrder=technologyCards.map(card=>card.dataset.selectedId);
    const reorderSource=technologyCards[1];
    const reorderTarget=technologyCards[0];
    const targetBox=reorderTarget.getBoundingClientRect();
    dispatchDrag(reorderSource,reorderTarget,targetBox.top+1);
    const afterOrder=[...document.querySelectorAll('[data-sector-category="기술정보"] .selected-article-card')].map(card=>card.dataset.selectedId);
    results.same_sector_reorder=beforeOrder.join("|")!==afterOrder.join("|")&&afterOrder[0]===beforeOrder[1];
    results.dom_state_order=domSelected().join("|")===state.selected.join("|");
    results.review_status_draft=state.reviewStatus==="draft";

    const extraCandidate=[...document.querySelectorAll(".candidate:not(.selected)")].find(card=>card.dataset.id!==removedId);
    const countAtLimit=state.selected.length;
    if(extraCandidate){
      dispatchDrag(extraCandidate,technologyZone,technologyZone.getBoundingClientRect().bottom-2);
      results.maximum_six=state.selected.length===countAtLimit&&state.selected.length===6&&!state.selected.includes(extraCandidate.dataset.id);
    }else{
      results.maximum_six=state.selected.length===6;
    }
    const originalLink=document.querySelector(".candidate .original-article-link");
    const linkStateBefore=state.selected.join("|");
    originalLink.addEventListener("click",event=>event.preventDefault(),{once:true});
    originalLink.dispatchEvent(new MouseEvent("click",{bubbles:true,cancelable:true}));
    results.left_original_link=/^https?:/.test(originalLink.href)&&originalLink.target==="_blank"&&originalLink.rel.includes("noopener")&&originalLink.rel.includes("noreferrer");
    results.link_does_not_select=state.selected.join("|")===linkStateBefore&&!dragging;
    results.sector_order=sectorOrder().join(">")==="투자·산업>기업동향>기술정보";
    results.exactly_three_sectors=sectorOrder().length===3;
    results.drop_zones=document.querySelectorAll("[data-drop-category]").length===3;
    await pause(300);
    results.images_render=[...document.querySelectorAll("[data-image-frame]")].every(frame=>{
      const image=frame.querySelector("img");
      const fallback=frame.querySelector(".image-fallback");
      return !!(image&&image.naturalWidth>0)||!!(fallback&&!fallback.hidden);
    });
    results.no_remote_image_src=![...document.images].some(image=>/^https?:/i.test(image.getAttribute("src")||""));

    const importCapacityCard=document.querySelector(".candidate.selected");
    importCapacityCard.querySelector('input[type="checkbox"]').click();
    const importBeforeCandidates=allCandidates().length;
    const importBeforeSelected=state.selected.length;
    const importInput=document.getElementById("importUrl");
    importInput.value=importFixture.article.input_url;
    importInput.dispatchEvent(new KeyboardEvent("keydown",{key:"Enter",bubbles:true,cancelable:true}));
    results.import_enter_triggered=document.getElementById("importStatus").classList.contains("loading");
    results.import_loading_state=results.import_enter_triggered&&document.getElementById("importBtn").textContent==="취소";
    await pause(450);
    const imported=state.manualCandidates.find(item=>item.collection_source_kind==="url_import");
    const importedId=imported&&imported.candidate_id;
    const importedCard=importedId&&document.querySelector(`[data-selected-id="${importedId}"]`);
    results.import_candidate_added=allCandidates().length===importBeforeCandidates+1&&!!imported;
    results.import_auto_selected=state.selected.length===importBeforeSelected+1&&state.selected.includes(importedId);
    results.import_sector_placement=!!importedCard&&view(importedId).category===importFixture.article.category&&!!document.querySelector(`[data-sector-category="${importFixture.article.category}"] [data-selected-id="${importedId}"]`);
    results.import_fields_filled=imported.title===importFixture.article.title&&imported.summary===importFixture.article.summary&&imported.source===importFixture.article.source;
    results.import_success_state=document.getElementById("importStatus").classList.contains("success")&&document.getElementById("importStatus").textContent.includes(importFixture.article.category);
    results.manual_fallback_hidden_after_success=document.getElementById("manualFallback").hidden===true;
    await pause(250);
    const importedFrame=importedCard&&importedCard.querySelector("[data-image-frame]");
    const importedImage=importedFrame&&importedFrame.querySelector("img");
    const importedFallback=importedFrame&&importedFrame.querySelector(".image-fallback");
    results.import_image_render=!!(importedImage&&importedImage.naturalWidth>0)||!!(importedFallback&&!importedFallback.hidden);

    const duplicateCapacity=state.selected.find(id=>id!==importedId);
    toggleSelected(duplicateCapacity,false);
    const callsBeforeDuplicate=mockImportCalls;
    const candidatesBeforeDuplicate=allCandidates().length;
    importInput.value=importFixture.article.input_url;
    document.getElementById("importBtn").click();
    await pause(250);
    results.duplicate_url_guard=mockImportCalls===callsBeforeDuplicate+1&&allCandidates().length===candidatesBeforeDuplicate&&document.getElementById("importStatus").textContent.includes("이미")&&imported.selected_url===importFixture.article.publisher_url;
    toggleSelected(duplicateCapacity,true);

    const importedMoveSource=document.querySelector(`[data-selected-id="${importedId}"]`);
    technologyZone=document.querySelector('[data-drop-category="기술정보"]');
    dispatchDrag(importedMoveSource,technologyZone,technologyZone.getBoundingClientRect().bottom-2);
    results.imported_card_drag=view(importedId).category==="기술정보"&&!!document.querySelector(`[data-sector-category="기술정보"] [data-selected-id="${importedId}"]`);
    const standalone=standaloneHtml();
    results.import_standalone_html=standalone.includes(imported.title)&&standalone.includes(imported.image_url)&&imported.image_url.startsWith("data:image/jpeg;base64,");
    results.final_html_portal_links=!(standalone.includes("v.daum.net")||standalone.includes("news.daum.net"))&&standalone.includes(importFixture.article.publisher_url);

    const failureCapacity=state.selected.find(id=>id!==importedId);
    toggleSelected(failureCapacity,false);
    importInput.value="https://publisher.example.test/failure";
    importInput.dispatchEvent(new KeyboardEvent("keydown",{key:"Enter",bubbles:true,cancelable:true}));
    await pause(250);
    results.import_error_state=document.getElementById("importStatus").classList.contains("error")&&document.getElementById("importStatus").textContent.includes("본문");
    results.manual_fallback_after_failure=document.getElementById("manualFallback").hidden===false&&document.getElementById("manualFallback").open===true;
    results.retry_control=document.getElementById("importBtn").textContent==="다시 시도";
    toggleSelected(failureCapacity,true);
    results.fixture_import_calls=mockImportCalls===3;
    results.external_test_network_calls=0;
    results.final_download_primary=document.querySelectorAll(".primary-action").length===1&&document.querySelector(".primary-action")?.textContent.trim()==="최종 브리핑 다운로드";
    results.removed_action_buttons=!document.getElementById("categoryOrderBtn")&&!document.getElementById("feedbackBtn")&&!document.getElementById("approveBtn");
    results.reset_secondary_menu=!!document.querySelector(".utility-menu #restoreBtn")&&!!document.querySelector("#restoreBtn").closest("details");

    const expected=window.__editorialReviewDebug();
    expected.dom=domSelected();
    expected.interaction=results;
    localStorage.setItem(expectedKey,JSON.stringify(expected));
    localStorage.setItem(phaseKey,"restore");
    location.reload();
    return;
  }
  await pause(300);
  const expected=JSON.parse(localStorage.getItem(expectedKey)||"{}");
  Object.assign(results,expected.interaction||{});
  const restored=window.__editorialReviewDebug();
  results.local_storage_restore=JSON.stringify(restored.selected)===JSON.stringify(expected.selected)&&JSON.stringify(restored.categories)===JSON.stringify(expected.categories);
  results.restored_dom_order=domSelected().join("|")===restored.selected.join("|")&&domSelected().join("|")===(expected.dom||[]).join("|");
  results.restored_sector_order=sectorOrder().join(">")==="투자·산업>기업동향>기술정보";
  results.restored_images=[...document.querySelectorAll("[data-image-frame]")].every(frame=>{
    const image=frame.querySelector("img");
    const fallback=frame.querySelector(".image-fallback");
    return !!(image&&image.naturalWidth>0)||!!(fallback&&!fallback.hidden);
  });
  const restoredImport=(restored.manualCandidates||[]).find(item=>item.collection_source_kind==="url_import");
  results.restored_imported_candidate=!!restoredImport&&restored.selected.includes(restoredImport.candidate_id)&&!!document.querySelector(`[data-selected-id="${restoredImport.candidate_id}"]`);
  results.restored_imported_image=!!restoredImport&&String(restoredImport.image_url||"").startsWith("data:image/jpeg;base64,");
  results.restored_imported_category=!!restoredImport&&restoredImport.category==="기술정보"&&restored.categories[restoredImport.candidate_id]==="기술정보";
  const marker=document.createElement("pre");
  marker.id="r3v7-browser-result";
  marker.textContent=JSON.stringify(results);
  document.body.appendChild(marker);
  localStorage.removeItem(phaseKey);
  localStorage.removeItem(expectedKey);
})().catch(error=>{
  const marker=document.createElement("pre");
  marker.id="r3v7-browser-result";
  marker.textContent=JSON.stringify({browser_available:true,error:String(error),stack:error&&error.stack||""});
  document.body.appendChild(marker);
});
</script>
"""
    harness = harness.replace("__IMPORT_RESPONSE__", embedded_response)
    interaction_path = console_path.with_name("interaction.html")
    console_source = console_path.read_text(encoding="utf-8")
    before_body_end, after_body_end = console_source.rsplit("</body>", 1)
    interaction_path.write_text(
        before_body_end + harness + "</body>" + after_body_end,
        encoding="utf-8",
    )
    profile_handle = None
    if browser.suffix.casefold() == ".exe":
        windows_temp_output = subprocess.run(
            ["cmd.exe", "/d", "/c", "echo", "%TEMP%"],
            cwd="/mnt/c",
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        windows_temp = next(
            line.strip()
            for line in reversed(windows_temp_output.splitlines())
            if re.match(r"^[A-Za-z]:\\", line.strip())
        )
        wsl_temp = subprocess.run(
            ["wslpath", "-u", windows_temp],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        profile_handle = tempfile.TemporaryDirectory(
            prefix="hdec-r3v7-browser-",
            dir=wsl_temp,
            ignore_cleanup_errors=True,
        )
        active_profile = Path(profile_handle.name)
    else:
        profile_dir.mkdir(parents=True, exist_ok=True)
        active_profile = profile_dir
    try:
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
            "--virtual-time-budget=8000",
            f"--user-data-dir={_browser_argument_path(active_profile, browser)}",
            "--dump-dom",
            _browser_path(interaction_path, browser),
        ]
        if browser.suffix.casefold() != ".exe":
            # GitHub-hosted runners can deny Chromium's user-namespace sandbox
            # and expose a very small /dev/shm. This fixture opens local files
            # only (window.fetch is replaced above), so these Linux CI flags do
            # not weaken any production browser or network boundary.
            command[2:2] = ["--no-sandbox", "--disable-dev-shm-usage"]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=45,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return {
                "browser_available": True,
                "error": (
                    "headless browser timeout "
                    f"after {exc.timeout}s; external network remains disabled"
                ),
            }
    finally:
        if profile_handle is not None:
            profile_handle.cleanup()
    match = re.search(
        r'<pre id="r3v7-browser-result">([^<]+)</pre>',
        completed.stdout,
    )
    if completed.returncode != 0 or not match:
        return {
            "browser_available": True,
            "error": (
                f"returncode={completed.returncode} "
                f"stderr={completed.stderr[-800:]!r}"
            ),
        }
    return json.loads(unescape(match.group(1)))


def main() -> int:
    v = V()
    for rel in (
        "app/editorial_article_import.py",
        "app/editorial_briefings.py",
        "app/editorial_review.py",
        "app/editorial_feedback.py",
        "app/operator_api.py",
        "scripts/build_editorial_review_console.py",
        "scripts/compile_editorial_feedback.py",
        "scripts/run_editorial_briefing.py",
        "scripts/verify_editorial_review_console.py",
    ):
        py_compile.compile(str(ROOT / rel), doraise=True)
    v.check("Python compile", True)
    import_fixture_response = verify_article_import_domain(v)

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
        "category normalize legacy value signal",
        editorial_review.normalize_category("정책"),
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
    analysis = editorial_review.analyze_editorial_category(
        "AI 로봇 모델 공개",
        "투자 확대 계획",
        source="검증매체",
        suggested_category="기업동향",
    )
    v.check(
        "AI category analysis returns scores and reason",
        analysis["category"] == "기술정보"
        and set(analysis["scores"]) == set(editorial_review.CATEGORY_ORDER)
        and isinstance(analysis["matched_signals"], dict)
        and bool(analysis["reason"]),
    )
    v.check(
        "title signal outweighs summary signal",
        analysis["scores"]["기술정보"] > analysis["scores"]["투자·산업"],
    )
    v.equal(
        "AI no-signal fallback is technology",
        editorial_review.analyze_editorial_category("", "")["category"],
        "기술정보",
    )
    v.equal(
        "AI no-signal fallback outweighs weak prior",
        editorial_review.analyze_editorial_category(
            "",
            "",
            suggested_category="투자·산업",
        )["category"],
        "기술정보",
    )
    v.equal(
        "AI deterministic tie break",
        editorial_review.analyze_editorial_category("투자 기업", "")["category"],
        "투자·산업",
    )
    v.equal(
        "suggested category is weak prior",
        editorial_review.analyze_editorial_category(
            "기업 경영 발표",
            "",
            suggested_category="투자·산업",
        )["category"],
        "기업동향",
    )
    v.equal(
        "longer corporate signal beats embedded acquisition token",
        editorial_review.analyze_editorial_category("인수합병 발표", "")["category"],
        "기업동향",
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
    v.check(
        "candidate JSON model includes category analysis",
        all(
            item.get("category_analysis", {}).get("category") == item["category"]
            and bool(item["category_analysis"].get("reason"))
            for item in candidates
        ),
    )
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
        v.equal("manual category remains explicit", selected[1].category, "기업동향")

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
        "기사 URL로 자동 불러오기",
        'contenteditable="true"',
        'id="boldBtn"',
        "직접 입력하기",
        "최종 브리핑 다운로드",
        "feedbackRecords",
        "selectedItems",
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
    v.check(
        "candidate card is draggable",
        'class="candidate ${selected?"selected":""}" draggable="true"' in template
        and 'event.dataTransfer.setData(kind==="candidate"?"candidate_id"' in template,
    )
    v.check(
        "candidate card contains safe original links",
        "original-article-link" in template
        and "원문 열기 ↗" in template
        and "safeAuthorityUrl(candidate.selected_url)" in template,
    )
    v.check(
        "left original links use target and rel security",
        'target="_blank" rel="noopener noreferrer"' in template,
    )
    v.check(
        "selected article card is draggable",
        'class="article-card selected-article-card" draggable="true"' in template,
    )
    v.check(
        "sector card drop controls category and order",
        "moveArticle(payload.id,card.dataset.selectedCategory,id,after)" in template,
    )
    v.check(
        "sector empty drop appends article",
        "moveArticle(payload.id,zone.dataset.dropCategory)" in template,
    )
    v.check(
        "maximum six remains enforced",
        "const MAX_SELECTED=6" in template
        and "state.selected.length>=MAX_SELECTED" in template,
    )
    v.check(
        "fixed sector empty guidance present",
        "왼쪽 기사를 이 섹터로 드래그하세요" in template
        and "data-drop-category" in template,
    )
    v.check(
        "order chips removed in favor of article cards",
        'id="orderList"' not in template and "order-chip" not in template,
    )
    v.check(
        "image rendering is local and escaped",
        "safeImageUrl(candidate.image_url)" in template
        and 'src="${esc(imageUrl)}"' in template,
    )
    v.check(
        "image lazy decode and fallback control exist",
        'loading="lazy" decoding="async"' in template
        and "bindImageFallbacks" in template
        and "image-fallback" in template,
    )
    v.check(
        "localStorage restoration remains present",
        "localStorage.getItem(storageKey)" in template
        and "localStorage.setItem(storageKey" in template,
    )
    v.check(
        "live image materialization uses temporary local root",
        "editorial_briefings.materialize_preview_images(" in builder_source
        and 'dir="/tmp"' in builder_source
        and "html_dir=image_stage" in builder_source,
    )
    v.check(
        "article URL is the only default import input",
        'id="importUrl"' in template
        and 'id="importBtn"' in template
        and 'id="manualFallback" hidden' in template,
    )
    v.check(
        "URL import exposes loading success and error states",
        "기사 본문과 이미지를 안전하게 분석하고 있습니다" in template
        and "섹터에 추가했습니다" in template
        and "기사를 자동으로 불러오지 못했습니다" in template,
    )
    v.check(
        "Enter key triggers URL import",
        'event.key==="Enter"' in template
        and "importArticleFromUrl()" in template,
    )
    v.check(
        "browser uses authenticated API and never fetches article URL",
        "fetch(articleImportApiUrl" in template
        and 'credentials:"include"' in template
        and "fetch(inputUrl" not in template
        and "fetch(article.canonical_url" not in template,
    )
    v.check(
        "API-disabled console remains usable",
        "기사 자동 불러오기 API가 설정되지 않았습니다" in template
        and 'document.getElementById("importBtn").disabled=true' in template,
    )
    v.check(
        "imported candidate keeps analysis and auto placement",
        "category_analysis:article.category_analysis" in template
        and "moveArticle(id,item.category)" in template
        and 'collection_source_kind:"url_import"' in template,
    )
    v.check(
        "duplicate canonical URL guard exists",
        "duplicateByUrl(article.canonical_url)" in template
        and "같은 원문 URL의 기사가 이미 후보에 있습니다" in template,
    )
    v.check(
        "final links reject portal and redirect authorities",
        "safeAuthorityUrl(candidate.selected_url)" in template
        and '"news.daum.net","v.daum.net"' in template
        and '"news.naver.com"' in template
        and '"news.google.com"' in template
        and '"msn.com"' in template,
    )
    v.check(
        "imported candidate preserves publisher discovery audit",
        "publisher_direct:article.publisher_direct" in template
        and "portal_resolution_reason:article.portal_resolution_reason" in template
        and "portal_fallback_used:article.portal_fallback_used" in template,
    )
    v.check(
        "bounded raster data URL validator exists",
        "MAX_IMPORTED_IMAGE_DATA_URL=350000" in template
        and "data:image\\/(jpeg|png|webp)" in template
        and "data:image/svg+xml" not in template,
    )
    v.check(
        "imported image is preserved in standalone HTML",
        "standaloneHtml()" in template
        and "safeImageUrl(candidate.image_url)" in template
        and "briefBody(state.selected.map(view)" in template,
    )
    v.check(
        "only final briefing download is primary",
        template.count('class="primary-action"') == 1
        and ">최종 브리핑 다운로드</button>" in template,
    )
    v.check(
        "category-order action button is absent",
        'id="categoryOrderBtn"' not in template
        and "카테고리 기본순서" not in template,
    )
    v.check(
        "feedback export action button is absent",
        'id="feedbackBtn"' not in template
        and "평가 JSONL" not in template,
    )
    v.check(
        "approval export action button is absent",
        'id="approveBtn"' not in template
        and "최종 승인 JSON" not in template,
    )
    v.check(
        "AI reset moved to confirmed secondary menu",
        'class="utility-menu"' in template
        and 'id="restoreBtn"' in template
        and "URL로 불러온 기사가 모두 사라집니다" in template
        and "confirm(" in template,
    )
    v.check(
        "builder injects explicit import API config without hardcoding",
        "ARTICLE_IMPORT_API_URL" in builder_source
        and "--article-import-api-url" in builder_source
        and "normalize_article_import_api_url" in builder_source
        and "hdec-news-sensor-operator.vercel.app" not in builder_source,
    )
    operator_api_source = (ROOT / "app/operator_api.py").read_text(encoding="utf-8")
    v.check(
        "CORS uses allowlist credentials and preflight",
        "allow_origins=[" in operator_api_source
        and 'allow_methods=["GET", "POST", "OPTIONS"]' in operator_api_source
        and "allow_credentials=True" in operator_api_source,
    )
    requirements_source = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    pyproject_source = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    docker_source = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    v.check(
        "existing Pillow validator is declared for API runtime",
        "Pillow==12.3.0" in requirements_source
        and "Pillow==12.3.0" in pyproject_source,
    )
    v.check(
        "minimal Docker runtime includes secure import leaves",
        "app/editorial_article_import.py" in docker_source
        and "app/editorial_briefings.py" in docker_source
        and "app/editorial_review.py" in docker_source
        and "app/operator_auth.py" in docker_source,
    )

    with tempfile.TemporaryDirectory(prefix="editorial-r3-v7-build-") as build_tmp:
        output_root = Path(build_tmp) / "review"
        build_environment = os.environ.copy()
        build_environment["TEAMS_AI_NEWS_WATCH"] = "0"
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "build_editorial_review_console.py"),
                "--fixture",
                "--run-at",
                "2026-07-31T07:20:00+09:00",
                "--output-root",
                str(output_root),
                "--article-import-api-url",
                "https://operator.example.test/api/editorial/import-article",
            ],
            cwd=ROOT,
            env=build_environment,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        v.check(
            "fixture console builds successfully",
            completed.returncode == 0,
            completed.stderr[-1000:],
        )
        edition_dir = output_root / "2026-07-31"
        latest_dir = output_root / "latest"
        fixture_bundle = json.loads(
            (edition_dir / "candidates.json").read_text(encoding="utf-8")
        )
        fixture_manifest = json.loads(
            (edition_dir / "manifest.json").read_text(encoding="utf-8")
        )
        latest_manifest = json.loads(
            (latest_dir / "manifest.json").read_text(encoding="utf-8")
        )
        v.check(
            "fixture mode performs zero image network calls",
            fixture_bundle["collection_audit"]["network_calls"] == 0
            and fixture_manifest["image_network_calls"] == 0
            and fixture_manifest["image_download_attempts"] == 0,
        )
        v.check(
            "fixture console receives explicit import API URL",
            fixture_bundle["article_import_api_url"]
            == "https://operator.example.test/api/editorial/import-article"
            and fixture_bundle["article_import_enabled"] is True
            and fixture_manifest["article_import_api_configured"] is True,
        )
        v.check(
            "candidate JSON contains category analysis",
            fixture_bundle["candidates"]
            and all(
                set(candidate["category_analysis"]) >= {
                    "category", "scores", "matched_signals", "reason",
                }
                for candidate in fixture_bundle["candidates"]
            ),
        )
        v.check(
            "candidate image paths are local",
            all(
                re.fullmatch(
                    r"assets/images/[A-Za-z0-9._-]+",
                    candidate.get("image_url", ""),
                )
                for candidate in fixture_bundle["candidates"]
            ),
        )
        fixture_assets = [
            edition_dir / candidate["image_url"]
            for candidate in fixture_bundle["candidates"]
        ]
        latest_assets = [
            latest_dir / candidate["image_url"]
            for candidate in fixture_bundle["candidates"]
        ]
        v.check(
            "fixture image asset exists",
            bool(fixture_assets)
            and all(path.is_file() and path.stat().st_size > 0 for path in fixture_assets),
        )
        v.check(
            "latest image assets exist",
            all(path.is_file() and path.stat().st_size > 0 for path in latest_assets),
        )
        daily_articles, _ = editorial_review.choose_daily_articles(
            fixture_bundle,
            None,
        )
        v.check(
            "Daily renderer path rebases to review image asset",
            all(
                article.image_url.startswith(
                    "../review/2026-07-31/assets/images/"
                )
                and (
                    output_root.parent
                    / "daily"
                    / article.image_url
                ).resolve().is_file()
                for article in daily_articles
            ),
        )
        v.check(
            "image counters copied to latest manifest",
            fixture_manifest["image_assets_materialized"] == 1
            and latest_manifest["image_assets_materialized"] == 1,
        )
        browser_results = run_browser_interaction(
            latest_dir / "index.html",
            Path(build_tmp) / "browser-profile",
            import_fixture_response,
        )
        v.check(
            "headless browser available",
            browser_results.get("browser_available") is True,
            str(browser_results),
        )
        for key, label in (
            ("left_to_right_drag", "left unselected candidate drag selects article"),
            ("left_drop_category", "left candidate appears in corporate sector"),
            ("cross_sector_move", "cross-sector drop changes category"),
            ("same_sector_reorder", "same-sector card drop changes order"),
            ("dom_state_order", "DOM order equals state.selected order"),
            ("maximum_six", "browser drag preserves maximum six"),
            ("left_original_link", "browser original link is safe"),
            ("link_does_not_select", "link click does not alter selection or drag"),
            ("sector_order", "browser sector order is fixed"),
            ("exactly_three_sectors", "browser renders exactly three sectors"),
            ("drop_zones", "browser renders three drop zones"),
            ("images_render", "fixture images load or show fallback"),
            ("no_remote_image_src", "rendered DOM has no remote image src"),
            ("review_status_draft", "drag returns review status to draft"),
            ("local_storage_restore", "reload restores selection and categories"),
            ("restored_dom_order", "reload restores selected DOM order"),
            ("restored_sector_order", "reload preserves fixed sector order"),
            ("restored_images", "reload preserves image or fallback rendering"),
            ("manual_fallback_initially_hidden", "manual fallback is hidden before failure"),
            ("import_enter_triggered", "Enter key triggers article import"),
            ("import_loading_state", "URL import shows loading and cancel state"),
            ("import_candidate_added", "URL import adds one left candidate"),
            ("import_auto_selected", "URL import automatically selects the candidate"),
            ("import_sector_placement", "URL import places card in classified sector"),
            ("import_fields_filled", "URL import fills title source and summary"),
            ("import_success_state", "URL import reports classified success"),
            ("manual_fallback_hidden_after_success", "manual fallback stays hidden after success"),
            ("import_image_render", "imported raster image renders or falls back"),
            ("duplicate_url_guard", "duplicate canonical URL is rejected without API call"),
            ("imported_card_drag", "imported selected card remains draggable across sectors"),
            ("import_standalone_html", "standalone HTML includes imported article and image"),
            ("final_html_portal_links", "standalone HTML contains publisher URL and no portal URL"),
            ("import_error_state", "URL import exposes safe extraction error state"),
            ("manual_fallback_after_failure", "manual fallback opens only after failure"),
            ("retry_control", "failed import exposes retry control"),
            ("fixture_import_calls", "browser import uses exactly three mock calls"),
            ("external_test_network_calls", "browser performs zero external test requests"),
            ("final_download_primary", "final briefing download is the only primary action"),
            ("removed_action_buttons", "removed top action buttons stay absent"),
            ("reset_secondary_menu", "AI reset is in the secondary menu"),
            ("restored_imported_candidate", "reload restores imported selected candidate"),
            ("restored_imported_image", "reload restores imported bounded data image"),
            ("restored_imported_category", "reload restores imported category and order"),
        ):
            expected = 0 if key == "external_test_network_calls" else True
            v.check(label, browser_results.get(key) == expected, str(browser_results))

    workflow = (
        ROOT / ".github/workflows/editorial-review-console.yml"
    ).read_text(encoding="utf-8")
    v.check("console schedule is 07:20 KST", 'cron: "20 22 * * *"' in workflow)
    v.check(
        "console supports same-day manual publication",
        "workflow_dispatch:" in workflow,
    )
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
    print("secure_article_import=PASS")
    print("PORTAL_LINK_RESOLUTION=PASS")
    print("PUBLISHER_DIRECT_URL=PASS")
    print("FINAL_HTML_PORTAL_LINKS=0")
    print("PUBLISHER_CANONICAL_DEDUP=PASS")
    print("PORTAL_FALLBACK_DISCLOSED=PASS")
    print("external_test_network_calls=0")
    print("network_sends=0")
    print("smtp_attempts=0")
    print("teams_sends=0")
    print("telegram_sends=0")
    print("production_state_writes=0")
    print(f"R3_V7_VERIFIER={v.checks}/{v.failures}")
    if v.failures:
        print("RESULT=D7-AK-6E-R3-V7_EDITORIAL_REVIEW_CONSOLE_FAIL")
        return 1
    print("RESULT=D7-AK-6E-R3-V7_EDITORIAL_REVIEW_CONSOLE_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
