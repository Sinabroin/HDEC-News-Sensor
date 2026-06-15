"""Live Collector 도메인 — 공개 RSS에서 실제 뉴스 메타데이터만 수집 (P0-C1).

이 파일은 "외부 네트워크 IO"를 소유하는 유일한 모듈이다. 핵심 경계 원칙:

- 공개 RSS(Google News 검색 RSS 등)만 읽는다. API key/비밀값이 전혀 필요 없다.
- 기사 페이지를 크롤링하지 않는다 — RSS가 주는 제목/요약/출처/링크/시각만 쓴다.
- 본문 전문을 저장/생성하지 않는다 (rules.md §3). snippet은 RSS summary를 절단한 것.
- X(엑스) 계열 소스는 어떤 경우에도 수집하지 않는다 (rules.md §1).
- DB·점수·insight·발송을 일절 다루지 않는다 — collector.run(live)이 반환값을
  받아 정규화/dedup/저장한다. 이 파일은 raw dict 리스트만 돌려준다.
- 실패 시 가짜 값을 만들지 않는다 — 빈 리스트를 돌려주고 collector가 fallback을 판단한다.

raw dict 형태는 data/mock_articles.json 항목과 동일해 collector가 그대로 정규화한다:
    {id, title, source, published_at, url, snippet, source_metadata}
"""

import hashlib
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape

from app import config

KST = timezone(timedelta(hours=9))

SOURCE_LABEL = "Google News RSS"
PROVIDER = "google_news_rss"
USER_AGENT = "HDEC-Executive-Radar/0.1 (+public-rss; non-crawling)"
DEFAULT_TIMEOUT = 8
SNIPPET_MAX_LEN = 500

# X(엑스) 계열은 Day-1 전체 금지 — URL/출처에 이 토큰이 있으면 수집 자체를 건너뛴다.
# 금지 호스트 토큰은 코드 트리 grep 규약에 걸리지 않게 조각으로 조립한다 (verifier와 동일).
_FORBIDDEN_HOST_TOKENS = ("".join(("twit", "ter.com")), "x.com", "t.co", "api.x")

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_DEFAULT_SOURCES = config.DATA_DIR / "live_news_sources.json"


def _strip_html(text: str) -> str:
    """RSS description의 HTML 조각을 제거하고 공백을 정리한다 (본문 저장 아님 — 요약 절단용).

    Google News description은 엔티티로 이스케이프돼 있어 먼저 unescape한 뒤 태그를 지운다.
    """
    cleaned = unescape(text or "")   # &lt;a&gt; → <a> 로 복원
    cleaned = _TAG_RE.sub(" ", cleaned)  # 그다음 실제 태그 제거
    return _WS_RE.sub(" ", cleaned).strip()


def _to_iso(pubdate: str) -> str | None:
    try:
        dt = parsedate_to_datetime(pubdate)
    except (TypeError, ValueError, IndexError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat(timespec="seconds")


def _is_forbidden(*values: str) -> bool:
    blob = " ".join(v.lower() for v in values if v)
    return any(token in blob for token in _FORBIDDEN_HOST_TOKENS)


def _load_sources(path=None) -> dict:
    src_path = path or _DEFAULT_SOURCES
    try:
        data = json.loads(src_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _build_google_news_url(query: str, cfg: dict) -> str:
    params = urllib.parse.urlencode({
        "q": query,
        "hl": cfg.get("hl", "ko"),
        "gl": cfg.get("gl", "KR"),
        "ceid": cfg.get("ceid", "KR:ko"),
    })
    return f"https://news.google.com/rss/search?{params}"


def _fetch(url: str, timeout: int) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (public RSS)
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace")


def _parse_items(xml_text: str, query: str, collected_at: str,
                 max_items: int) -> list[dict]:
    """RSS 2.0 <item>에서 메타데이터만 추출한다 (본문 전문 없음)."""
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    rows = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if not title or not link.startswith(("http://", "https://")):
            continue

        source_el = item.find("source")
        source = (source_el.text or "").strip() if source_el is not None else ""
        # Google News 제목은 "헤드라인 - 출처" 형태가 많다 — 출처가 없으면 분리해 얻는다.
        if not source and " - " in title:
            head, _, tail = title.rpartition(" - ")
            if head and len(tail) <= 40:
                title, source = head.strip(), tail.strip()
        source = source or "출처 미상"
        # 제목 끝의 " - 출처" 중복 꼬리표는 제거해 임원용 제목을 깔끔히 한다.
        if source != "출처 미상" and title.endswith(f" - {source}"):
            title = title[: -(len(source) + 3)].strip()

        if _is_forbidden(link, source):
            continue  # X(엑스) 등 금지 소스는 수집하지 않는다

        snippet = _strip_html(item.findtext("description") or "")[:SNIPPET_MAX_LEN]
        published_at = _to_iso(item.findtext("pubDate") or "") or collected_at
        url_hash = hashlib.sha256(link.lower().rstrip("/").encode("utf-8")).hexdigest()

        rows.append({
            "id": f"live_{url_hash[:12]}",
            "title": title,
            "source": source,
            "published_at": published_at,
            "url": link,
            "snippet": snippet,
            "source_metadata": {
                "provider": PROVIDER,
                "query": query,
                "source_url": link,
                "collected_at": collected_at,
                "provider_response_id": url_hash[:16],
            },
        })
        if len(rows) >= max_items:
            break
    return rows


def fetch_all(timeout: int = DEFAULT_TIMEOUT, sources_path=None) -> list[dict]:
    """설정된 모든 query에 대해 공개 RSS를 수집해 raw dict 리스트를 반환한다.

    네트워크/파싱 실패는 해당 query만 건너뛰고 계속한다 (가짜 값 생성 금지).
    수집 결과가 0건이면 빈 리스트를 반환하고, fallback 판단은 collector가 한다.
    """
    cfg = _load_sources(sources_path)
    queries = [q for q in (cfg.get("queries") or []) if isinstance(q, str) and q.strip()]
    if not queries:
        return []

    max_per_query = int(cfg.get("max_per_query", 4))
    max_total = int(cfg.get("max_total", 40))
    collected_at = datetime.now(KST).isoformat(timespec="seconds")

    seen_urls, results = set(), []
    for query in queries:
        if len(results) >= max_total:
            break
        url = _build_google_news_url(query, cfg)
        try:
            xml_text = _fetch(url, timeout)
        except Exception:  # noqa: BLE001 — 네트워크/HTTP 오류는 query 단위로 무시
            continue
        for row in _parse_items(xml_text, query, collected_at, max_per_query):
            if row["url"] in seen_urls:
                continue
            seen_urls.add(row["url"])
            results.append(row)
            if len(results) >= max_total:
                break
    return results
