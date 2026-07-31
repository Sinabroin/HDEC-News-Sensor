"""Authenticated editorial URL import domain.

The module fetches one public article URL, extracts bounded editorial metadata,
creates a deterministic executive summary, classifies it with the R3-V6
taxonomy, and returns only a short excerpt plus an optional bounded raster data
URL. It is intentionally independent from the FastAPI route so all network
behavior can be exercised with mock resolvers/openers.
"""

from __future__ import annotations

import base64
import ipaddress
import json
import re
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape, unescape
from html.parser import HTMLParser
from io import BytesIO
from typing import Callable, Iterable, Mapping
from urllib.parse import urljoin, urlparse, urlunparse

from PIL import Image, UnidentifiedImageError

from app import editorial_briefings, editorial_review

ARTICLE_URL_MAX_LENGTH = 2048
ARTICLE_HTML_MAX_BYTES = 2_000_000
ARTICLE_IMAGE_MAX_BYTES = 5_000_000
ARTICLE_FETCH_TIMEOUT_SECONDS = 8
ARTICLE_REDIRECT_LIMIT = 3
ARTICLE_BODY_MIN_CHARS = 200
ARTICLE_EXCERPT_MAX_CHARS = 1_200
SUMMARY_MAX_CHARS = 500
SUMMARY_TARGET_MIN_CHARS = 250
SUMMARY_MAX_SENTENCES = 4
IMPORT_IMAGE_MAX_WIDTH = 1280
IMPORT_IMAGE_MAX_HEIGHT = 720
IMPORT_IMAGE_MAX_BINARY_BYTES = 250_000
IMPORT_IMAGE_MAX_DATA_URL_CHARS = 350_000
IMPORT_IMAGE_MAX_PIXELS = 40_000_000

_HTML_MEDIA_TYPES = frozenset({"text/html", "application/xhtml+xml"})
_RASTER_MEDIA_BY_EXTENSION = {
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_INTERNAL_HOST_SUFFIXES = (
    ".internal",
    ".intranet",
    ".local",
    ".localhost",
    ".home",
    ".lan",
)
_PORTAL_HOSTS: dict[str, tuple[str, ...]] = {
    "daum": ("news.daum.net", "v.daum.net", "media.daum.net"),
    "naver": ("news.naver.com", "n.news.naver.com", "m.news.naver.com"),
    "google_news": ("news.google.com",),
    "msn": ("msn.com",),
    "yahoo": ("news.yahoo.com",),
}
_SEARCH_HOSTS = (
    "google.com",
    "bing.com",
    "search.naver.com",
    "search.daum.net",
)
_SHORTENER_HOSTS = (
    "bit.ly",
    "t.co",
    "tinyurl.com",
    "goo.gl",
    "han.gl",
    "me2.do",
    "vo.la",
    "url.kr",
)
_SKIP_TAGS = frozenset(
    {"script", "style", "noscript", "nav", "footer", "aside", "form", "template"}
)
_VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)
_NEGATIVE_CONTAINER_RE = re.compile(
    r"(?:^|[-_\s])(?:ad|ads|advert|banner|comment|footer|header|menu|nav|"
    r"recommend|related|share|social|subscribe|promo|cookie|popup|sidebar)(?:$|[-_\s])",
    re.I,
)
_BODY_CONTAINER_RE = re.compile(
    r"(?:article|article[-_]?body|article[-_]?content|news[-_]?body|"
    r"news[-_]?content|story[-_]?body|post[-_]?content|content[-_]?body)",
    re.I,
)
_BOILERPLATE_RE = re.compile(
    r"(무단\s*전재|재배포\s*금지|저작권자|광고\s*문의|댓글|추천\s*기사|"
    r"구독\s*신청|공유하기|로그인|회원가입|copyright|all rights reserved)",
    re.I,
)
_SENTENCE_RE = re.compile(r"(?<=[.!?。！？])\s+|\n+")
_WORD_RE = re.compile(r"[0-9A-Za-z가-힣][0-9A-Za-z가-힣·._%-]*")
_SUMMARY_STOPWORDS = frozenset(
    {
        "관련",
        "대한",
        "위한",
        "통해",
        "이번",
        "기자",
        "것으로",
        "있다고",
        "한다고",
        "그리고",
        "하지만",
        "the",
        "and",
        "for",
        "with",
    }
)

_ERRORS: dict[str, tuple[int, str]] = {
    "INVALID_URL": (400, "올바른 기사 URL을 입력해 주세요."),
    "AUTH_REQUIRED": (401, "운영자 로그인이 필요합니다."),
    "FORBIDDEN": (403, "기사 불러오기 권한이 없습니다."),
    "UNSAFE_DESTINATION": (400, "안전하지 않은 네트워크 대상입니다."),
    "DNS_RESOLUTION_FAILED": (502, "기사 사이트 주소를 확인하지 못했습니다."),
    "REDIRECT_REJECTED": (400, "기사 사이트의 이동 경로가 안전하지 않습니다."),
    "UNSUPPORTED_CONTENT_TYPE": (415, "지원하지 않는 콘텐츠 형식입니다."),
    "RESPONSE_TOO_LARGE": (413, "기사 응답이 허용 크기를 초과했습니다."),
    "FETCH_TIMEOUT": (504, "기사 사이트 응답 시간이 초과되었습니다."),
    "ARTICLE_METADATA_NOT_FOUND": (422, "기사 제목이나 메타데이터를 추출하지 못했습니다."),
    "ARTICLE_BODY_NOT_FOUND": (422, "본문을 자동으로 추출하지 못했습니다."),
    "PORTAL_ORIGINAL_NOT_FOUND": (
        422,
        "포털 페이지에서 언론사 원문을 확인하지 못했습니다.",
    ),
    "IMAGE_REJECTED": (422, "대표 이미지가 안전성 또는 품질 검사를 통과하지 못했습니다."),
    "INTERNAL_ERROR": (500, "기사를 불러오는 중 내부 오류가 발생했습니다."),
}


class ArticleImportError(RuntimeError):
    """Client-safe import failure with a stable code and HTTP status."""

    def __init__(self, code: str, *, message: str = "", status: int | None = None):
        default_status, default_message = _ERRORS.get(code, _ERRORS["INTERNAL_ERROR"])
        super().__init__(code)
        self.code = code if code in _ERRORS else "INTERNAL_ERROR"
        self.status = int(status if status is not None else default_status)
        self.message = str(message or default_message)

    def response_payload(self) -> dict[str, object]:
        return {
            "ok": False,
            "error": {
                "code": self.code,
                "message": self.message,
            },
        }


@dataclass(frozen=True)
class FetchedDocument:
    requested_url: str
    final_url: str
    content_type: str
    text: str
    redirect_chain: tuple[str, ...]


@dataclass(frozen=True)
class Paragraph:
    text: str
    context: str
    link_chars: int
    position: int


@dataclass(frozen=True)
class ExtractedArticle:
    canonical_url: str
    title: str
    source: str
    published_at: str | None
    body: str
    image_candidates: tuple[tuple[str, str], ...]
    title_source: str
    body_source: str


@dataclass(frozen=True)
class PublisherDocument:
    input_url: str
    discovery_url: str
    publisher_url: str
    portal_source: str
    portal_resolution_reason: str
    document: FetchedDocument


def _clean_text(value: object) -> str:
    return " ".join(unescape(str(value or "")).replace("\u00a0", " ").split())


def _public_ip(address: str) -> bool:
    try:
        value = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    return bool(
        value.is_global
        and not value.is_private
        and not value.is_loopback
        and not value.is_link_local
        and not value.is_multicast
        and not value.is_reserved
        and not value.is_unspecified
    )


def validate_public_article_url(
    value: object,
    *,
    resolver: Callable[..., Iterable[tuple]] | None = None,
) -> str:
    """Normalize a URL and require every resolved address to be globally routable."""
    raw = str(value or "").strip()
    if (
        not raw
        or len(raw) > ARTICLE_URL_MAX_LENGTH
        or any(ord(character) < 32 for character in raw)
    ):
        raise ArticleImportError("INVALID_URL")
    try:
        parsed = urlparse(raw)
        port = parsed.port
    except (TypeError, ValueError):
        raise ArticleImportError("INVALID_URL") from None
    scheme = parsed.scheme.casefold()
    if scheme not in {"http", "https"} or not parsed.hostname:
        raise ArticleImportError("INVALID_URL")
    if parsed.username is not None or parsed.password is not None:
        raise ArticleImportError("UNSAFE_DESTINATION")
    if port is not None and port != (443 if scheme == "https" else 80):
        raise ArticleImportError("UNSAFE_DESTINATION")

    host = parsed.hostname.casefold().rstrip(".")
    if (
        host == "localhost"
        or "." not in host
        or any(host.endswith(suffix) for suffix in _INTERNAL_HOST_SUFFIXES)
    ):
        raise ArticleImportError("UNSAFE_DESTINATION")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        # Hostname destinations keep DNS policy auditable and prevent literal-IP tricks.
        raise ArticleImportError("UNSAFE_DESTINATION")
    try:
        ascii_host = host.encode("idna").decode("ascii")
    except UnicodeError:
        raise ArticleImportError("INVALID_URL") from None

    lookup = resolver or socket.getaddrinfo
    try:
        results = list(
            lookup(
                ascii_host,
                port or (443 if scheme == "https" else 80),
                type=socket.SOCK_STREAM,
            )
        )
    except (OSError, UnicodeError, TypeError, ValueError):
        raise ArticleImportError("DNS_RESOLUTION_FAILED") from None
    addresses = {
        str(result[4][0]).split("%", 1)[0]
        for result in results
        if len(result) >= 5 and result[4]
    }
    if not addresses:
        raise ArticleImportError("DNS_RESOLUTION_FAILED")
    if not all(_public_ip(address) for address in addresses):
        raise ArticleImportError("UNSAFE_DESTINATION")

    netloc = ascii_host + (f":{port}" if port is not None else "")
    return urlunparse(
        (
            scheme,
            netloc,
            parsed.path or "/",
            parsed.params,
            parsed.query,
            "",
        )
    )


def _host_matches(host: str, candidates: Iterable[str]) -> bool:
    return any(host == item or host.endswith("." + item) for item in candidates)


def portal_source_for_url(value: object) -> str:
    try:
        parsed = urlparse(str(value or ""))
    except ValueError:
        return ""
    host = (parsed.hostname or "").casefold().rstrip(".")
    for source, domains in _PORTAL_HOSTS.items():
        if _host_matches(host, domains):
            return source
    if _host_matches(host, _SEARCH_HOSTS):
        return "search"
    if _host_matches(host, _SHORTENER_HOSTS):
        return "shortener"
    path = parsed.path.casefold()
    if "/search" in path and host:
        return "search"
    return ""


def is_publisher_direct_url(value: object) -> bool:
    """Conservatively reject known portals, search pages, and URL shorteners."""
    try:
        parsed = urlparse(str(value or ""))
    except ValueError:
        return False
    return bool(
        parsed.scheme.casefold() in {"http", "https"}
        and parsed.hostname
        and not portal_source_for_url(value)
    )


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _network_opener():
    return urllib.request.build_opener(_NoRedirectHandler())


def _open_request(opener: object, request: urllib.request.Request):
    if callable(opener) and not hasattr(opener, "open"):
        return opener(request, timeout=ARTICLE_FETCH_TIMEOUT_SECONDS)
    return opener.open(request, timeout=ARTICLE_FETCH_TIMEOUT_SECONDS)


def _header(headers: object, name: str) -> str:
    getter = getattr(headers, "get", None)
    if callable(getter):
        return str(getter(name) or "")
    target = name.casefold()
    try:
        items = headers.items()
    except AttributeError:
        items = headers
    for key, value in items:
        if str(key).casefold() == target:
            return str(value or "")
    return ""


def _status(response: object) -> int:
    value = getattr(response, "status", None)
    if value is None:
        value = response.getcode()
    return int(value)


def _read_limited(response: object, limit: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = response.read(min(65_536, limit - total + 1))
        if not chunk:
            break
        chunks.append(bytes(chunk))
        total += len(chunk)
        if total > limit:
            raise ArticleImportError("RESPONSE_TOO_LARGE")
    return b"".join(chunks)


def _response_url(response: object, fallback: str) -> str:
    getter = getattr(response, "geturl", None)
    return str(getter() if callable(getter) else fallback)


def _request_with_redirects(
    url: str,
    *,
    accept: str,
    max_bytes: int,
    resolver: Callable[..., Iterable[tuple]] | None,
    opener: object | None,
) -> tuple[str, str, str, bytes, tuple[str, ...]]:
    requested_url = validate_public_article_url(url, resolver=resolver)
    current_url = requested_url
    redirects: list[str] = []
    active_opener = opener or _network_opener()
    for redirect_count in range(ARTICLE_REDIRECT_LIMIT + 1):
        request = urllib.request.Request(
            current_url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (compatible; HDEC-Editorial-ArticleImport/1.0; "
                    "+authenticated-editorial-use)"
                ),
                "Accept": accept,
            },
            method="GET",
        )
        response = None
        try:
            response = _open_request(active_opener, request)
        except urllib.error.HTTPError as exc:
            if exc.code in _REDIRECT_STATUSES:
                response = exc
            else:
                raise ArticleImportError("ARTICLE_METADATA_NOT_FOUND") from None
        except (TimeoutError, socket.timeout):
            raise ArticleImportError("FETCH_TIMEOUT") from None
        except urllib.error.URLError as exc:
            if isinstance(getattr(exc, "reason", None), (TimeoutError, socket.timeout)):
                raise ArticleImportError("FETCH_TIMEOUT") from None
            raise ArticleImportError("DNS_RESOLUTION_FAILED") from None
        except OSError:
            raise ArticleImportError("DNS_RESOLUTION_FAILED") from None

        try:
            status = _status(response)
            if status in _REDIRECT_STATUSES:
                location = _header(response.headers, "Location")
                if not location or redirect_count >= ARTICLE_REDIRECT_LIMIT:
                    raise ArticleImportError("REDIRECT_REJECTED")
                try:
                    target = validate_public_article_url(
                        urljoin(current_url, location),
                        resolver=resolver,
                    )
                except ArticleImportError:
                    raise ArticleImportError("REDIRECT_REJECTED") from None
                redirects.append(target)
                current_url = target
                continue
            if status < 200 or status >= 300:
                raise ArticleImportError("ARTICLE_METADATA_NOT_FOUND")

            final_candidate = _response_url(response, current_url)
            try:
                final_url = validate_public_article_url(
                    final_candidate,
                    resolver=resolver,
                )
            except ArticleImportError:
                raise ArticleImportError("REDIRECT_REJECTED") from None
            if final_url != current_url:
                redirects.append(final_url)
            content_type = _header(response.headers, "Content-Type")
            content_length = _header(response.headers, "Content-Length")
            try:
                advertised_length = int(content_length) if content_length else None
            except ValueError:
                advertised_length = None
            if advertised_length is not None and advertised_length > max_bytes:
                raise ArticleImportError("RESPONSE_TOO_LARGE")
            payload = _read_limited(response, max_bytes)
            return requested_url, final_url, content_type, payload, tuple(redirects)
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()
    raise ArticleImportError("REDIRECT_REJECTED")


def fetch_article_html(
    url: object,
    *,
    resolver: Callable[..., Iterable[tuple]] | None = None,
    opener: object | None = None,
) -> FetchedDocument:
    requested_url, final_url, content_type, payload, redirects = _request_with_redirects(
        str(url or ""),
        accept="text/html,application/xhtml+xml;q=0.9",
        max_bytes=ARTICLE_HTML_MAX_BYTES,
        resolver=resolver,
        opener=opener,
    )
    media_type = content_type.split(";", 1)[0].strip().casefold()
    if media_type not in _HTML_MEDIA_TYPES:
        raise ArticleImportError("UNSUPPORTED_CONTENT_TYPE")
    charset_match = re.search(r"charset\s*=\s*[\"']?([A-Za-z0-9._-]+)", content_type, re.I)
    charset = charset_match.group(1) if charset_match else "utf-8"
    try:
        text = payload.decode(charset, errors="replace")
    except LookupError:
        text = payload.decode("utf-8", errors="replace")
    return FetchedDocument(
        requested_url=requested_url,
        final_url=final_url,
        content_type=media_type,
        text=text,
        redirect_chain=redirects,
    )


class _ArticleHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, list[str]] = {}
        self.canonical_links: list[str] = []
        self.image_src_links: list[str] = []
        self.time_values: list[str] = []
        self.outbound_links: list[tuple[str, str]] = []
        self.document_title = ""
        self.h1_values: list[str] = []
        self.paragraphs: list[Paragraph] = []
        self.body_images: list[str] = []
        self.jsonld_blocks: list[str] = []
        self._stack: list[dict[str, object]] = []
        self._skip_depth = 0
        self._article_depth = 0
        self._main_depth = 0
        self._body_depth = 0
        self._anchor_depth = 0
        self._title_parts: list[str] | None = None
        self._h1_parts: list[str] | None = None
        self._paragraph: dict[str, object] | None = None
        self._jsonld_parts: list[str] | None = None

    @staticmethod
    def _attributes(attrs: list[tuple[str, str | None]]) -> dict[str, str]:
        return {
            str(key).casefold(): str(value or "")
            for key, value in attrs
        }

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        tag = tag.casefold()
        attributes = self._attributes(attrs)
        marker_text = " ".join((attributes.get("id", ""), attributes.get("class", "")))
        hidden = (
            "hidden" in attributes
            or attributes.get("aria-hidden", "").casefold() == "true"
            or "display:none" in attributes.get("style", "").replace(" ", "").casefold()
        )
        negative = bool(_NEGATIVE_CONTAINER_RE.search(marker_text))
        entering_skip = self._skip_depth > 0 or tag in _SKIP_TAGS or hidden or negative
        article_delta = int(tag == "article")
        main_delta = int(tag == "main")
        body_delta = int(bool(_BODY_CONTAINER_RE.search(marker_text)))
        anchor_delta = int(tag == "a")
        state = {
            "tag": tag,
            "skip": int(entering_skip),
            "article": article_delta,
            "main": main_delta,
            "body": body_delta,
            "anchor": anchor_delta,
        }
        if tag not in _VOID_TAGS:
            self._stack.append(state)
            self._skip_depth += int(entering_skip)
            self._article_depth += article_delta
            self._main_depth += main_delta
            self._body_depth += body_delta
            self._anchor_depth += anchor_delta

        if tag == "script" and "ld+json" in attributes.get("type", "").casefold():
            self._jsonld_parts = []
        if self._skip_depth and self._jsonld_parts is None:
            return
        if tag == "meta":
            key = (
                attributes.get("property")
                or attributes.get("name")
                or attributes.get("itemprop")
            ).strip().casefold()
            content = attributes.get("content", "").strip()
            if key and content:
                self.meta.setdefault(key, []).append(content)
        elif tag == "link":
            rel = {
                value.casefold()
                for value in attributes.get("rel", "").replace(",", " ").split()
            }
            href = attributes.get("href", "").strip()
            if href and "canonical" in rel:
                self.canonical_links.append(href)
            if href and "image_src" in rel:
                self.image_src_links.append(href)
        elif tag == "time":
            value = attributes.get("datetime", "").strip()
            if value:
                self.time_values.append(value)
        elif tag == "a":
            href = attributes.get("href", "").strip()
            if href and len(self.outbound_links) < 256:
                hint = " ".join(
                    (
                        attributes.get("id", ""),
                        attributes.get("class", ""),
                        attributes.get("rel", ""),
                        attributes.get("title", ""),
                    )
                )
                self.outbound_links.append((href, _clean_text(hint)))
        elif tag == "title":
            self._title_parts = []
        elif tag == "h1" and self._h1_parts is None:
            self._h1_parts = []
        elif tag == "p" and self._paragraph is None:
            context = (
                "article"
                if self._article_depth
                else "main"
                if self._main_depth
                else "body_container"
                if self._body_depth
                else "generic"
            )
            self._paragraph = {
                "parts": [],
                "link_chars": 0,
                "context": context,
                "position": len(self.paragraphs),
            }
        elif tag == "img" and (self._article_depth or self._main_depth or self._body_depth):
            source = (
                attributes.get("src")
                or attributes.get("data-src")
                or attributes.get("data-original")
            ).strip()
            if source:
                self.body_images.append(source)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]):
        self.handle_starttag(tag, attrs)

    def handle_data(self, data: str):
        if self._jsonld_parts is not None:
            self._jsonld_parts.append(data)
            return
        if self._skip_depth:
            return
        if self._title_parts is not None:
            self._title_parts.append(data)
        if self._h1_parts is not None:
            self._h1_parts.append(data)
        if self._paragraph is not None:
            self._paragraph["parts"].append(data)
            if self._anchor_depth:
                self._paragraph["link_chars"] += len(_clean_text(data))

    def handle_endtag(self, tag: str):
        tag = tag.casefold()
        if tag == "script" and self._jsonld_parts is not None:
            block = "".join(self._jsonld_parts).strip()
            if block:
                self.jsonld_blocks.append(block)
            self._jsonld_parts = None
        elif tag == "title" and self._title_parts is not None:
            self.document_title = _clean_text(" ".join(self._title_parts))
            self._title_parts = None
        elif tag == "h1" and self._h1_parts is not None:
            value = _clean_text(" ".join(self._h1_parts))
            if value:
                self.h1_values.append(value)
            self._h1_parts = None
        elif tag == "p" and self._paragraph is not None:
            value = _clean_text(" ".join(self._paragraph["parts"]))
            if value:
                self.paragraphs.append(
                    Paragraph(
                        text=value,
                        context=str(self._paragraph["context"]),
                        link_chars=int(self._paragraph["link_chars"]),
                        position=int(self._paragraph["position"]),
                    )
                )
            self._paragraph = None

        if not self._stack:
            return
        index = next(
            (
                candidate
                for candidate in range(len(self._stack) - 1, -1, -1)
                if self._stack[candidate]["tag"] == tag
            ),
            -1,
        )
        if index < 0:
            return
        while len(self._stack) > index:
            state = self._stack.pop()
            self._skip_depth = max(0, self._skip_depth - int(state["skip"]))
            self._article_depth = max(0, self._article_depth - int(state["article"]))
            self._main_depth = max(0, self._main_depth - int(state["main"]))
            self._body_depth = max(0, self._body_depth - int(state["body"]))
            self._anchor_depth = max(0, self._anchor_depth - int(state["anchor"]))


def _walk_json(value: object) -> Iterable[Mapping[str, object]]:
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from _walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json(child)


def _jsonld_article(parser: _ArticleHTMLParser) -> Mapping[str, object]:
    candidates: list[Mapping[str, object]] = []
    for block in parser.jsonld_blocks:
        try:
            payload = json.loads(block)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        for item in _walk_json(payload):
            raw_type = item.get("@type")
            values = raw_type if isinstance(raw_type, list) else [raw_type]
            types = {str(value or "").casefold() for value in values}
            if types & {
                "article",
                "newsarticle",
                "reportagenewsarticle",
                "analysisnewsarticle",
                "backgroundnewsarticle",
            }:
                candidates.append(item)
    return candidates[0] if candidates else {}


def _jsonld_publisher(value: object) -> str:
    if isinstance(value, Mapping):
        return _clean_text(value.get("name"))
    return _clean_text(value)


def _jsonld_image_values(value: object) -> list[str]:
    values = value if isinstance(value, list) else [value]
    output: list[str] = []
    for item in values:
        if isinstance(item, Mapping):
            candidate = item.get("url") or item.get("contentUrl")
        else:
            candidate = item
        text = str(candidate or "").strip()
        if text:
            output.append(text)
    return output


def _meta_first(parser: _ArticleHTMLParser, *keys: str) -> str:
    for key in keys:
        values = parser.meta.get(key.casefold()) or []
        for value in values:
            cleaned = _clean_text(value)
            if cleaned:
                return cleaned
    return ""


def _jsonld_discovery_urls(parser: _ArticleHTMLParser) -> list[str]:
    output: list[str] = []

    def add(value: object) -> None:
        if isinstance(value, Mapping):
            add(value.get("@id") or value.get("url"))
        elif isinstance(value, list):
            for item in value:
                add(item)
        else:
            text = str(value or "").strip()
            if text:
                output.append(text)

    for block in parser.jsonld_blocks:
        try:
            payload = json.loads(block)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        for node in _walk_json(payload):
            for key in (
                "mainEntityOfPage",
                "url",
                "sameAs",
                "isBasedOn",
                "isBasedOnUrl",
                "citation",
            ):
                if key in node:
                    add(node.get(key))
    return output


def _article_like_discovery_url(value: str) -> bool:
    parsed = urlparse(value)
    path = parsed.path.strip("/")
    if not path:
        return False
    lowered = f"{parsed.path}?{parsed.query}".casefold()
    if any(
        marker in lowered
        for marker in (
            "/article",
            "/articles",
            "/news",
            "/view",
            "/read",
            "/story",
            "article_id=",
            "articleid=",
            "news_id=",
            "newsid=",
        )
    ):
        return True
    return len(path) >= 8 and (
        any(character.isdigit() for character in path)
        or len(path.split("/")) >= 2
    )


def _publisher_candidates(
    values: Iterable[object],
    *,
    discovery_url: str,
    resolver: Callable[..., Iterable[tuple]] | None,
) -> tuple[list[str], bool]:
    output: list[str] = []
    unsafe_seen = False
    for value in values:
        candidate = urljoin(discovery_url, str(value or "").strip())
        if not candidate or not is_publisher_direct_url(candidate):
            continue
        if not _article_like_discovery_url(candidate):
            continue
        try:
            safe = validate_public_article_url(candidate, resolver=resolver)
        except ArticleImportError as exc:
            unsafe_seen = unsafe_seen or exc.code == "UNSAFE_DESTINATION"
            continue
        if safe not in output:
            output.append(safe)
    return output, unsafe_seen


def discover_portal_publisher_url(
    html: str,
    discovery_url: str,
    *,
    resolver: Callable[..., Iterable[tuple]] | None = None,
) -> tuple[str, str]:
    """Find one unambiguous publisher article URL without accepting a portal fallback."""
    parser = _ArticleHTMLParser()
    parser.feed(str(html or ""))
    parser.close()
    jsonld_urls = _jsonld_discovery_urls(parser)
    metadata_urls: list[str] = []
    for key in (
        "original-source",
        "syndication-source",
        "citation_public_url",
        "parsely-link",
        "publisher-url",
        "og:url",
    ):
        metadata_urls.extend(parser.meta.get(key) or [])
    outbound_values = [value for value, _hint in parser.outbound_links]
    hinted_outbound = [
        value
        for value, hint in parser.outbound_links
        if re.search(r"(?:original|origin|publisher|source|원문|출처)", hint, re.I)
    ]
    groups = (
        ("publisher_canonical", parser.canonical_links),
        ("publisher_json_ld", jsonld_urls),
        ("publisher_metadata", metadata_urls),
        ("publisher_outbound_link", hinted_outbound or outbound_values),
    )
    unsafe_seen = False
    for reason, values in groups:
        candidates, group_unsafe = _publisher_candidates(
            values,
            discovery_url=discovery_url,
            resolver=resolver,
        )
        unsafe_seen = unsafe_seen or group_unsafe
        if len(candidates) == 1:
            return candidates[0], reason
        if len(candidates) > 1:
            raise ArticleImportError("PORTAL_ORIGINAL_NOT_FOUND")
    if unsafe_seen:
        raise ArticleImportError("UNSAFE_DESTINATION")
    raise ArticleImportError("PORTAL_ORIGINAL_NOT_FOUND")


def resolve_publisher_document(
    url: object,
    *,
    resolver: Callable[..., Iterable[tuple]] | None = None,
    opener: object | None = None,
) -> PublisherDocument:
    """Resolve a discovery/portal URL to a fetched publisher-direct HTML document."""
    input_url = validate_public_article_url(url, resolver=resolver)
    discovery = fetch_article_html(input_url, resolver=resolver, opener=opener)
    input_portal = portal_source_for_url(input_url)
    final_portal = portal_source_for_url(discovery.final_url)
    portal_source = input_portal or final_portal

    if is_publisher_direct_url(discovery.final_url):
        reason = (
            "portal_redirect_to_publisher"
            if portal_source
            else "direct_input"
            if discovery.final_url == input_url
            else "direct_redirect_to_publisher"
        )
        return PublisherDocument(
            input_url=input_url,
            discovery_url=input_url if portal_source else discovery.final_url,
            publisher_url=discovery.final_url,
            portal_source=portal_source,
            portal_resolution_reason=reason,
            document=discovery,
        )
    if not portal_source:
        raise ArticleImportError("PORTAL_ORIGINAL_NOT_FOUND")

    publisher_url, reason = discover_portal_publisher_url(
        discovery.text,
        discovery.final_url,
        resolver=resolver,
    )
    publisher_document = fetch_article_html(
        publisher_url,
        resolver=resolver,
        opener=opener,
    )
    if not is_publisher_direct_url(publisher_document.final_url):
        raise ArticleImportError("PORTAL_ORIGINAL_NOT_FOUND")
    return PublisherDocument(
        input_url=input_url,
        discovery_url=discovery.final_url,
        publisher_url=publisher_document.final_url,
        portal_source=portal_source,
        portal_resolution_reason=reason,
        document=publisher_document,
    )


def _paragraph_is_content(paragraph: Paragraph) -> bool:
    text = paragraph.text
    if len(text) < 30:
        return False
    if paragraph.link_chars / max(1, len(text)) > 0.45:
        return False
    if _BOILERPLATE_RE.search(text) and len(text) < 320:
        return False
    if len(text) < 70 and not re.search(r"[.!?。！？]$", text):
        return False
    return True


def _paragraph_body(paragraphs: list[Paragraph]) -> tuple[str, str]:
    filtered: list[Paragraph] = []
    seen: set[str] = set()
    for paragraph in paragraphs:
        if not _paragraph_is_content(paragraph):
            continue
        key = re.sub(r"\W+", "", paragraph.text).casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        filtered.append(paragraph)

    for context, label in (
        ("article", "article_element"),
        ("main", "main_scored_paragraphs"),
        ("body_container", "article_body_container"),
        ("generic", "generic_scored_paragraphs"),
    ):
        rows = [paragraph for paragraph in filtered if paragraph.context == context]
        if context in {"main", "generic"}:
            ranked = sorted(
                rows,
                key=lambda paragraph: (
                    min(len(paragraph.text), 700)
                    + paragraph.text.count(".") * 35
                    - paragraph.link_chars * 2,
                    -paragraph.position,
                ),
                reverse=True,
            )[:24]
            selected_positions = {paragraph.position for paragraph in ranked}
            rows = [
                paragraph
                for paragraph in rows
                if paragraph.position in selected_positions
            ]
        body = "\n\n".join(paragraph.text for paragraph in rows)
        if len(body) >= ARTICLE_BODY_MIN_CHARS:
            return body, label
    return "", ""


def _normalize_published_at(value: object) -> str | None:
    raw = _clean_text(value)
    if not raw:
        return None
    candidate = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return raw[:80]
    if parsed.tzinfo is None:
        return parsed.isoformat(timespec="seconds")
    return parsed.isoformat(timespec="seconds")


def extract_article(
    html: str,
    page_url: str,
    *,
    resolver: Callable[..., Iterable[tuple]] | None = None,
) -> ExtractedArticle:
    parser = _ArticleHTMLParser()
    try:
        parser.feed(str(html or ""))
        parser.close()
    except (ValueError, TypeError):
        raise ArticleImportError("ARTICLE_METADATA_NOT_FOUND") from None
    jsonld = _jsonld_article(parser)

    title = _clean_text(jsonld.get("headline"))
    title_source = "json_ld"
    if not title:
        title = _meta_first(parser, "og:title")
        title_source = "og_title"
    if not title:
        title = _meta_first(parser, "twitter:title")
        title_source = "twitter_title"
    if not title:
        title = parser.document_title
        title_source = "document_title"
    if not title and parser.h1_values:
        title = parser.h1_values[0]
        title_source = "h1"
    if not title:
        raise ArticleImportError("ARTICLE_METADATA_NOT_FOUND")
    title = title[:500]

    source = _jsonld_publisher(jsonld.get("publisher"))
    if not source:
        source = _meta_first(parser, "og:site_name")
    if not source:
        source = _meta_first(parser, "author", "provider", "publisher")
    if not source:
        source = (urlparse(page_url).hostname or "").casefold().removeprefix("www.")
    source = source[:160]

    published_at = _normalize_published_at(
        jsonld.get("datePublished")
        or _meta_first(parser, "article:published_time", "datepublished", "date")
        or (parser.time_values[0] if parser.time_values else "")
    )

    jsonld_body = _clean_text(jsonld.get("articleBody"))
    if len(jsonld_body) >= ARTICLE_BODY_MIN_CHARS:
        body, body_source = jsonld_body, "json_ld_article_body"
    else:
        body, body_source = _paragraph_body(parser.paragraphs)
    if len(body) < ARTICLE_BODY_MIN_CHARS:
        raise ArticleImportError("ARTICLE_BODY_NOT_FOUND")

    canonical_raw = (
        (parser.canonical_links[0] if parser.canonical_links else "")
        or _meta_first(parser, "og:url")
        or page_url
    )
    canonical_url = ""
    try:
        candidate_url = validate_public_article_url(
            urljoin(page_url, canonical_raw),
            resolver=resolver,
        )
        if is_publisher_direct_url(candidate_url):
            canonical_url = candidate_url
    except ArticleImportError:
        pass
    if not canonical_url:
        canonical_url = validate_public_article_url(page_url, resolver=resolver)
        if not is_publisher_direct_url(canonical_url):
            raise ArticleImportError("PORTAL_ORIGINAL_NOT_FOUND")

    image_candidates: list[tuple[str, str]] = []
    for value in _jsonld_image_values(jsonld.get("image")):
        image_candidates.append((urljoin(page_url, value), "json_ld"))
    for value, label in (
        (_meta_first(parser, "og:image", "og:image:url", "og:image:secure_url"), "og_image"),
        (_meta_first(parser, "twitter:image", "twitter:image:src"), "twitter_image"),
        ((parser.image_src_links[0] if parser.image_src_links else ""), "image_src"),
        ((parser.body_images[0] if parser.body_images else ""), "article_body"),
    ):
        if value:
            image_candidates.append((urljoin(page_url, value), label))
    unique_images: list[tuple[str, str]] = []
    seen_images: set[str] = set()
    for image_url, label in image_candidates:
        if image_url in seen_images:
            continue
        seen_images.add(image_url)
        unique_images.append((image_url, label))

    return ExtractedArticle(
        canonical_url=canonical_url,
        title=title,
        source=source,
        published_at=published_at,
        body=body,
        image_candidates=tuple(unique_images),
        title_source=title_source,
        body_source=body_source,
    )


def _sentences(body: str) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for part in _SENTENCE_RE.split(body):
        sentence = _clean_text(part)
        if len(sentence) < 35 or _BOILERPLATE_RE.search(sentence):
            continue
        key = re.sub(r"\W+", "", sentence).casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(sentence)
    return output


def summarize_article(title: str, body: str) -> str:
    """Return a source-grounded, bounded 2-4 sentence executive summary."""
    sentences = _sentences(body)
    if not sentences:
        raise ArticleImportError("ARTICLE_BODY_NOT_FOUND")
    title_terms = {
        word.casefold()
        for word in _WORD_RE.findall(title)
        if len(word) >= 2 and word.casefold() not in _SUMMARY_STOPWORDS
    }
    document_frequency: dict[str, int] = {}
    sentence_terms: list[set[str]] = []
    for sentence in sentences:
        terms = {
            word.casefold()
            for word in _WORD_RE.findall(sentence)
            if len(word) >= 2 and word.casefold() not in _SUMMARY_STOPWORDS
        }
        sentence_terms.append(terms)
        for term in terms:
            document_frequency[term] = document_frequency.get(term, 0) + 1

    scored: list[tuple[float, int]] = []
    for index, (sentence, terms) in enumerate(zip(sentences, sentence_terms)):
        title_overlap = len(terms & title_terms)
        rare_terms = sum(
            1.0 / document_frequency[term]
            for term in terms
            if document_frequency.get(term, 0) <= 3
        )
        numeric_signals = len(re.findall(r"\d[\d,.]*%?|[0-9]+조|[0-9]+억", sentence))
        length_score = 2.0 if 70 <= len(sentence) <= 190 else 0.7
        position_score = max(0.0, 1.5 - index * 0.08)
        score = (
            title_overlap * 5.0
            + min(rare_terms, 8.0)
            + numeric_signals * 2.0
            + length_score
            + position_score
        )
        scored.append((score, index))

    ranked_indices = [
        index
        for _score, index in sorted(scored, key=lambda item: (-item[0], item[1]))
    ]
    chosen: list[int] = []
    total_chars = 0
    for index in ranked_indices:
        sentence = sentences[index]
        projected = total_chars + (1 if chosen else 0) + len(sentence)
        if chosen and projected > SUMMARY_MAX_CHARS:
            continue
        chosen.append(index)
        total_chars = projected
        if (
            len(chosen) >= 2
            and total_chars >= SUMMARY_TARGET_MIN_CHARS
        ) or len(chosen) >= SUMMARY_MAX_SENTENCES:
            break
    if len(chosen) < 2 and len(sentences) >= 2:
        for index in range(len(sentences)):
            if index in chosen:
                continue
            projected = total_chars + 1 + len(sentences[index])
            if projected <= SUMMARY_MAX_CHARS:
                chosen.append(index)
                break
    chosen = sorted(chosen[:SUMMARY_MAX_SENTENCES])
    summary = " ".join(sentences[index] for index in chosen)
    if len(summary) > SUMMARY_MAX_CHARS:
        summary = summary[: SUMMARY_MAX_CHARS - 1].rstrip() + "…"
    return summary


def _download_image(
    image_url: str,
    *,
    resolver: Callable[..., Iterable[tuple]] | None,
    opener: object | None,
) -> tuple[str, bytes, str]:
    _requested, final_url, content_type, payload, _redirects = _request_with_redirects(
        image_url,
        accept="image/jpeg,image/png,image/webp;q=0.9",
        max_bytes=ARTICLE_IMAGE_MAX_BYTES,
        resolver=resolver,
        opener=opener,
    )
    media_type = content_type.split(";", 1)[0].strip().casefold()
    if media_type not in set(_RASTER_MEDIA_BY_EXTENSION.values()) | {"image/svg+xml"}:
        raise ArticleImportError("IMAGE_REJECTED")
    extension, rejection = editorial_briefings._image_magic_extension(  # noqa: SLF001
        payload,
        content_type,
    )
    if rejection or extension not in _RASTER_MEDIA_BY_EXTENSION:
        raise ArticleImportError("IMAGE_REJECTED")
    if _RASTER_MEDIA_BY_EXTENSION[extension] != media_type:
        raise ArticleImportError("IMAGE_REJECTED")
    return final_url, payload, extension


def _reencode_image(
    payload: bytes,
    *,
    remote_url: str,
    source_kind: str,
    title: str,
    summary: str,
    source: str,
    canonical_url: str,
) -> str:
    quality_article = editorial_briefings.EditorialArticle(
        title=title,
        summary=summary,
        source=source,
        published_at=datetime(1970, 1, 1, tzinfo=timezone.utc),
        selected_url=canonical_url,
        link_kind="publisher_direct",
        link_label="원문 보기",
        category="기술정보",
    )
    quality_candidate = editorial_briefings.ImageCandidateOption(
        url=remote_url,
        source_kind=source_kind,
        source_page_url=canonical_url,
        reason=f"import_{source_kind}",
        context="authenticated_article_import",
    )
    try:
        assessment = editorial_briefings.assess_image_quality(
            payload,
            remote_url=remote_url,
            candidate=quality_candidate,
            article=quality_article,
        )
    except (editorial_briefings.ImageDownloadError, OSError, ValueError):
        raise ArticleImportError("IMAGE_REJECTED") from None
    if not assessment.accepted:
        raise ArticleImportError("IMAGE_REJECTED")

    try:
        with Image.open(BytesIO(payload)) as decoded:
            decoded.load()
            width, height = decoded.size
            if (
                width <= 0
                or height <= 0
                or width * height > IMPORT_IMAGE_MAX_PIXELS
            ):
                raise ArticleImportError("IMAGE_REJECTED")
            if decoded.mode in {"RGBA", "LA"} or "transparency" in decoded.info:
                rgba = decoded.convert("RGBA")
                background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
                background.alpha_composite(rgba)
                image = background.convert("RGB")
            else:
                image = decoded.convert("RGB")
    except ArticleImportError:
        raise
    except (OSError, UnidentifiedImageError, ValueError, Image.DecompressionBombError):
        raise ArticleImportError("IMAGE_REJECTED") from None

    image.thumbnail(
        (IMPORT_IMAGE_MAX_WIDTH, IMPORT_IMAGE_MAX_HEIGHT),
        Image.Resampling.LANCZOS,
    )
    encoded = b""
    for scale_round in range(4):
        for quality in (82, 78, 75, 70, 65):
            output = BytesIO()
            image.save(
                output,
                format="JPEG",
                quality=quality,
                optimize=True,
                progressive=True,
            )
            candidate = output.getvalue()
            if len(candidate) <= IMPORT_IMAGE_MAX_BINARY_BYTES:
                encoded = candidate
                break
        if encoded:
            break
        next_width = max(320, int(image.width * 0.85))
        next_height = max(180, int(image.height * 0.85))
        if (next_width, next_height) == image.size:
            break
        image = image.resize((next_width, next_height), Image.Resampling.LANCZOS)
    if not encoded:
        raise ArticleImportError("IMAGE_REJECTED")
    data_url = "data:image/jpeg;base64," + base64.b64encode(encoded).decode("ascii")
    if len(data_url) > IMPORT_IMAGE_MAX_DATA_URL_CHARS:
        raise ArticleImportError("IMAGE_REJECTED")
    return data_url


def materialize_imported_image(
    candidates: Iterable[tuple[str, str]],
    *,
    title: str,
    summary: str,
    source: str,
    canonical_url: str,
    resolver: Callable[..., Iterable[tuple]] | None = None,
    opener: object | None = None,
) -> tuple[str, str, str]:
    """Return bounded data URL, source label, and a manifest-safe result reason."""
    saw_candidate = False
    for image_url, source_kind in list(candidates)[:5]:
        saw_candidate = True
        try:
            safe_url = validate_public_article_url(image_url, resolver=resolver)
            final_url, payload, _extension = _download_image(
                safe_url,
                resolver=resolver,
                opener=opener,
            )
            return (
                _reencode_image(
                    payload,
                    remote_url=final_url,
                    source_kind=source_kind,
                    title=title,
                    summary=summary,
                    source=source,
                    canonical_url=canonical_url,
                ),
                source_kind,
                "image_reencoded",
            )
        except ArticleImportError:
            continue
    return "", "", "image_rejected" if saw_candidate else "image_not_found"


def _excerpt(body: str) -> str:
    if len(body) <= ARTICLE_EXCERPT_MAX_CHARS:
        return body
    clipped = body[:ARTICLE_EXCERPT_MAX_CHARS]
    boundary = max(clipped.rfind(" "), clipped.rfind("\n"))
    if boundary >= ARTICLE_EXCERPT_MAX_CHARS - 120:
        clipped = clipped[:boundary]
    return clipped.rstrip() + "…"


def import_article(
    url: object,
    *,
    resolver: Callable[..., Iterable[tuple]] | None = None,
    opener: object | None = None,
    image_opener: object | None = None,
) -> dict[str, object]:
    """Fetch and transform one article without retaining or returning full HTML/body."""
    resolution = resolve_publisher_document(
        url,
        resolver=resolver,
        opener=opener,
    )
    fetched = resolution.document
    extracted = extract_article(
        fetched.text,
        fetched.final_url,
        resolver=resolver,
    )
    summary = summarize_article(extracted.title, extracted.body)
    summary_html = editorial_briefings.sanitize_editorial_inline_html(
        escape(summary)
    )
    category_analysis = editorial_review.analyze_editorial_category(
        extracted.title,
        summary,
        source=extracted.source,
    )
    image_url, image_source, image_reason = materialize_imported_image(
        extracted.image_candidates,
        title=extracted.title,
        summary=summary,
        source=extracted.source,
        canonical_url=extracted.canonical_url,
        resolver=resolver,
        opener=image_opener if image_opener is not None else opener,
    )
    article = {
        "input_url": resolution.input_url,
        "discovery_url": resolution.discovery_url,
        "discovery_source": resolution.portal_source or "publisher_direct",
        "portal_source": resolution.portal_source,
        "portal_resolution_reason": resolution.portal_resolution_reason,
        "portal_fallback_used": False,
        "publisher_url": extracted.canonical_url,
        "publisher_domain": (urlparse(extracted.canonical_url).hostname or "").casefold(),
        "publisher_direct": True,
        "canonical_url": extracted.canonical_url,
        "fetched_url": fetched.final_url,
        "title": extracted.title,
        "source": extracted.source,
        "published_at": extracted.published_at,
        "summary": summary,
        "summary_html": summary_html,
        "category": category_analysis["category"],
        "category_analysis": category_analysis,
        "article_text_excerpt": _excerpt(extracted.body),
        "image_url": image_url,
        "extraction": {
            "title_source": extracted.title_source,
            "body_source": extracted.body_source,
            "image_source": image_source,
            "image_result": image_reason,
            "summary_mode": "deterministic_extractive",
            "fetched_url": fetched.final_url,
            "redirect_count": len(fetched.redirect_chain),
            "portal_source": resolution.portal_source,
            "portal_resolution_reason": resolution.portal_resolution_reason,
            "portal_fallback_used": False,
        },
    }
    return {"ok": True, "article": article}


__all__ = [
    "ARTICLE_BODY_MIN_CHARS",
    "ARTICLE_EXCERPT_MAX_CHARS",
    "ARTICLE_FETCH_TIMEOUT_SECONDS",
    "ARTICLE_HTML_MAX_BYTES",
    "ARTICLE_IMAGE_MAX_BYTES",
    "ARTICLE_REDIRECT_LIMIT",
    "IMPORT_IMAGE_MAX_DATA_URL_CHARS",
    "ArticleImportError",
    "ExtractedArticle",
    "FetchedDocument",
    "PublisherDocument",
    "discover_portal_publisher_url",
    "extract_article",
    "fetch_article_html",
    "import_article",
    "materialize_imported_image",
    "is_publisher_direct_url",
    "portal_source_for_url",
    "resolve_publisher_document",
    "summarize_article",
    "validate_public_article_url",
]
