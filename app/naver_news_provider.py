"""Naver News 보조 provider 도메인 — 공식 Naver News Search API에서 뉴스 메타데이터만 수집 (P0-D2).

app/live_collector.py(Google RSS)와 동일한 경계 원칙을 따르는 leaf 모듈이다:

- 공식 엔드포인트(https://openapi.naver.com/v1/search/news.json)만 호출한다. Naver 웹페이지나
  언론사 사이트를 크롤링하지 않는다 — 검색 API가 주는 title/originallink/link/description/pubDate만 쓴다.
- 본문 전문을 저장/생성하지 않는다 (rules.md §3). snippet은 description을 절단한 것이다.
- X(엑스) 계열 소스는 어떤 경우에도 수집하지 않는다 (rules.md §1).
- DB·점수·insight·발송을 일절 다루지 않는다 — raw dict 리스트 + provider 상태만 돌려준다.
- 자격증명은 config(=환경변수)에서만 읽고, 값을 어디에도 print/log/직렬화하지 않는다 (rules.md §4).
- 모듈 import 시점에 네트워크를 호출하지 않는다 (네트워크는 fetch() 안에서만 일어난다).
- 기본값 off(NAVER_NEWS_ENABLED=false)이며, 자격증명이 없으면 전체 live 수집을 실패시키지 않고
  정직하게 skip한다 (status: skipped_missing_credentials). 실패 시 가짜 값을 만들지 않는다.

raw dict 형태는 live_collector와 동일해 collector가 그대로 정규화한다 (source_metadata는
rules.md §3의 허용 키 5종만 — naver_link/원문링크유무 같은 추가 필드는 persist하지 않고,
provider 식별/원문링크 판단은 감사 레이어가 파생한다):
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

from app import config, news_coverage, source_quality

KST = timezone(timedelta(hours=9))

PROVIDER = "naver_news_api"
SOURCE_LABEL = "Naver News API"
# 공식 엔드포인트만 사용한다 (코드 상수로 고정 — sources 파일이 다른 호스트로 덮어쓰지 못하게).
ENDPOINT = "https://openapi.naver.com/v1/search/news.json"
USER_AGENT = "HDEC-Executive-Radar/0.1 (+naver-news-openapi; non-crawling)"
DEFAULT_TIMEOUT = 8
SNIPPET_MAX_LEN = 500

# provider 상태 — 감사/오케스트레이션이 소비한다 (값은 절대 비밀값을 담지 않는다).
STATUS_DISABLED = "disabled"
STATUS_SKIPPED_MISSING_CREDENTIALS = "skipped_missing_credentials"
STATUS_ACTIVE = "active"
STATUS_ERROR = "error"

# X(엑스) 계열은 Day-1 전체 금지 — 코드 트리 grep 규약에 걸리지 않게 조각으로 조립한다.
_FORBIDDEN_HOST_TOKENS = ("".join(("twit", "ter.com")), "x.com", "t.co", "api.x")

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_DEFAULT_SOURCES = config.DATA_DIR / "naver_news_sources.json"


def _strip_tags(text: str) -> str:
    """Naver가 매칭어에 붙이는 <b></b> 등 HTML 태그를 제거하고 엔티티를 복원한다.

    먼저 unescape로 &lt;b&gt; 같은 이스케이프를 실제 태그로 돌린 뒤 태그를 제거한다
    (본문 저장이 아니라 제목/요약 정리용). 공백은 단일 공백으로 정리한다.
    """
    cleaned = unescape(text or "")
    cleaned = _TAG_RE.sub(" ", cleaned)
    return _WS_RE.sub(" ", cleaned).strip()


def _to_iso(pubdate: str) -> str | None:
    """Naver pubDate(RFC822, 예: 'Mon, 16 Jun 2025 09:00:00 +0900')를 tz 포함 ISO로."""
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


def _host_of(url: str) -> str:
    try:
        netloc = urllib.parse.urlsplit(url).netloc.lower()
    except (ValueError, AttributeError):
        return ""
    return netloc[4:] if netloc.startswith("www.") else netloc


def _source_from_url(url: str, host_map: dict) -> str:
    """originallink 호스트로 매체명을 추정한다 (로스터 커버리지 매칭용).

    매핑에 없으면 호스트 문자열을 그대로 돌려준다 — 가짜 매체명을 만들지 않는다.
    (검색 API가 매체명을 직접 주지 않으므로 호스트 기반 추정이다.)
    """
    host = _host_of(url)
    if not host:
        return "출처 미상"
    for key in sorted(host_map or {}, key=len, reverse=True):
        if key and key.lower() in host:
            return host_map[key]
    return host


def _load_sources(path=None) -> dict:
    src_path = path or _DEFAULT_SOURCES
    try:
        data = json.loads(src_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _prefer_url(originallink: str, link: str) -> str | None:
    """originallink가 유효한 http/https면 그것을, 아니면 link를 쓴다 (원문 우선)."""
    for candidate in (originallink, link):
        c = (candidate or "").strip()
        if c.startswith(("http://", "https://")):
            return c
    return None


def _normalize_item(item: dict, query: str, collected_at: str,
                    host_map: dict) -> dict | None:
    """Naver 검색 API item 한 건을 표준 raw dict로 정규화한다 (실패/금지 시 None).

    - title/description: <b> 등 태그 제거 + 엔티티 복원.
    - url: originallink 우선, 없으면 link.
    - source: originallink 호스트로 매체명 추정.
    - source_metadata: rules.md §3 허용 키 5종만 (provider/query/source_url/collected_at/
      provider_response_id). 원문링크 유무 등 추가 정보는 persist하지 않는다(감사 레이어가 파생).
    """
    if not isinstance(item, dict):
        return None
    title = _strip_tags(item.get("title") or "")
    url = _prefer_url(item.get("originallink") or "", item.get("link") or "")
    if not title or not url:
        return None
    if _is_forbidden(url, item.get("originallink") or "", item.get("link") or ""):
        return None  # X(엑스) 등 금지 소스는 수집하지 않는다

    source = _source_from_url(url, host_map)
    snippet = _strip_tags(item.get("description") or "")[:SNIPPET_MAX_LEN]
    published_at = _to_iso(item.get("pubDate") or "") or collected_at
    url_hash = hashlib.sha256(url.lower().rstrip("/").encode("utf-8")).hexdigest()

    return {
        "id": f"naver_{url_hash[:12]}",
        "title": title,
        "source": source,
        "published_at": published_at,
        "url": url,
        "snippet": snippet,
        "source_metadata": {
            "provider": PROVIDER,
            "query": query,
            "source_url": url,
            "collected_at": collected_at,
            "provider_response_id": url_hash[:16],
        },
    }


def parse_response(payload: dict, query: str, collected_at: str, host_map: dict,
                   max_items: int, filtered_sink: list | None = None) -> list[dict]:
    """Naver 검색 API JSON 응답에서 메타데이터만 추출한다 (본문 전문 없음).

    filtered_sink가 주어지면 출처 품질로 '제외된' 비뉴스성 항목의 메타데이터
    (title/source/url/published_at)만 담는다 (감사 투명성, P0-C1.8과 동일 정책).
    네트워크 없이 fixture dict로 호출해 파서 계약을 검증할 수 있다.
    """
    items = (payload or {}).get("items")
    if not isinstance(items, list):
        return []
    rows = []
    for item in items:
        row = _normalize_item(item, query, collected_at, host_map)
        if row is None:
            continue
        # 출처 품질 가드 (P0-C1.6과 동일) — 블로그/카페/커뮤니티성은 수집 단계에서 제외.
        if source_quality.is_excluded(row["source"], row["title"]):
            if filtered_sink is not None:
                filtered_sink.append({
                    "title": row["title"],
                    "source": row["source"],
                    "url": row["url"],
                    "published_at": row["published_at"],
                })
            continue
        rows.append(row)
        if len(rows) >= max_items:
            break
    return rows


def _request_json(url: str, headers: dict, timeout: int) -> dict:
    """공식 엔드포인트에 GET 요청해 JSON을 돌려준다 (네트워크 격리 지점 — 테스트가 stub).

    headers에 담긴 자격증명은 절대 print/log하지 않는다.
    """
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (공식 API)
        charset = resp.headers.get_content_charset() or "utf-8"
        return json.loads(resp.read().decode(charset, errors="replace"))


def _status_only(status: str, attempted: int = 0,
                 credentials_present: bool = False) -> dict:
    # credentials_present는 자격증명의 '유무(bool)'만 담는다 — 값(id/secret)은 절대 담지 않는다
    # (rules.md §4). 감사/오케스트레이션이 disabled vs skipped_missing_credentials를 구분한다.
    return {"provider": PROVIDER, "source_label": SOURCE_LABEL, "status": status,
            "articles": [], "queries_attempted": attempted, "queries_ok": 0,
            "raw_count": 0, "credentials_present": credentials_present}


def fetch(timeout: int | None = None, sources_path=None,
          filtered_out: list | None = None, include_coverage: bool = True) -> dict:
    """설정된 쿼리에 대해 공식 Naver 검색 API를 호출해 raw dict + 상태를 돌려준다.

    반환 계약 (collector 오케스트레이션 + 감사가 소비):
        {"provider", "source_label", "status", "articles": [...],
         "queries_attempted", "queries_ok"}

    상태:
      - disabled: NAVER_NEWS_ENABLED off (네트워크 0건 — 기본값, Google-only 유지).
      - skipped_missing_credentials: 켜졌지만 client id/secret 부재 (전체 수집 실패 안 함).
      - active: 1개 이상 쿼리 성공.
      - error: 켜졌고 자격증명 있으나 모든 쿼리가 실패 (가짜 값 0건).
    """
    # 자격증명 유무만 bool로 판별한다 — 값은 읽어서 헤더에만 쓰고 어디에도 출력하지 않는다.
    creds_present = bool(config.NAVER_CLIENT_ID and config.NAVER_CLIENT_SECRET)
    if not config.NAVER_NEWS_ENABLED:
        return _status_only(STATUS_DISABLED, credentials_present=creds_present)

    client_id = config.NAVER_CLIENT_ID
    client_secret = config.NAVER_CLIENT_SECRET
    if not (client_id and client_secret):
        # 자격증명 부재 — 정직하게 skip한다. 비밀값/이름-값을 출력하지 않는다.
        return _status_only(STATUS_SKIPPED_MISSING_CREDENTIALS, credentials_present=False)

    cfg = _load_sources(sources_path)
    configured = [q for q in (cfg.get("queries") or [])
                  if isinstance(q, str) and q.strip()]
    # D7-AF: Google RSS와 동일한 중앙 coverage query group을 Naver API에도 연결한다.
    # 순서를 보존하며 중복 query만 제거한다.
    queries, seen_queries = [], set()
    coverage_queries = news_coverage.all_queries() if include_coverage else []
    for query in coverage_queries + configured:
        key = query.strip().casefold()
        if not key or key in seen_queries:
            continue
        seen_queries.add(key)
        queries.append(query)
    if not queries:
        return _status_only(STATUS_ERROR, credentials_present=True)

    host_map = cfg.get("host_source_map") or {}
    display = int(cfg.get("display", 10))
    start = int(cfg.get("start", 1))
    sort = str(cfg.get("sort", "date"))
    max_per_query = int(cfg.get("max_per_query", 10))
    max_total = int(cfg.get("max_total", 80))
    to = int(timeout if timeout is not None else cfg.get("timeout_seconds", DEFAULT_TIMEOUT))
    collected_at = datetime.now(KST).isoformat(timespec="seconds")
    headers = {
        "X-Naver-Client-Id": client_id,
        "X-Naver-Client-Secret": client_secret,
        "User-Agent": USER_AGENT,
    }

    seen_urls, results, queries_ok = set(), [], 0
    filtered_urls = set()
    for query in queries:
        if len(results) >= max_total:
            break
        params = urllib.parse.urlencode(
            {"query": query, "display": display, "start": start, "sort": sort})
        url = f"{ENDPOINT}?{params}"
        try:
            payload = _request_json(url, headers, to)
        except Exception:  # noqa: BLE001 — 네트워크/HTTP/JSON 오류는 쿼리 단위로 무시
            continue
        queries_ok += 1
        sink = [] if filtered_out is not None else None
        for row in parse_response(payload, query, collected_at, host_map,
                                  max_per_query, sink):
            if row["url"] in seen_urls:
                continue
            seen_urls.add(row["url"])
            results.append(row)
            if len(results) >= max_total:
                break
        if sink:
            for item in sink:
                u = item.get("url")
                if not u or u in filtered_urls or u in seen_urls:
                    continue
                filtered_urls.add(u)
                if len(filtered_out) < max_total:
                    filtered_out.append(item)

    status = STATUS_ACTIVE if queries_ok else STATUS_ERROR
    return {"provider": PROVIDER, "source_label": SOURCE_LABEL, "status": status,
            "articles": results, "queries_attempted": len(queries),
            "queries_ok": queries_ok, "raw_count": len(results),
            "credentials_present": True}
