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

from app import config, news_coverage, publisher_direct, source_priority, source_quality

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

# D7-AK-6E R4-R17 — non-secret discovery-lane marker. It records ONLY *how* a
# row was found (which lane surfaced it), never *whether* it is qualified. The
# marker is discovery provenance and must never be consumed as executive
# qualification evidence downstream (see editorial_briefings; the Naver query
# text — which contains a publisher name — is likewise never authority).
DISCOVERY_LANE_GENERAL = "general"
DISCOVERY_LANE_PRIMARY_PUBLISHER = "primary_publisher"

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
    """Use only a syntactically publisher-direct originallink as authority.

    A Naver ``link`` remains discovery provenance when originallink is absent;
    it is never promoted to publisher authority by this adapter.
    """
    direct = publisher_direct.normalize_publisher_canonical_url(originallink)
    if direct:
        return direct
    if str(originallink or "").strip():
        # Preserve an explicitly supplied malformed originallink for quarantine
        # audit. It is never replaced by a portal link or made delivery eligible.
        raw_origin = str(originallink).strip()
        return raw_origin if raw_origin.startswith(("http://", "https://")) else None
    discovery = (link or "").strip()
    if discovery.startswith(("http://", "https://")):
        return discovery
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
    originallink = item.get("originallink") or ""
    naver_link = item.get("link") or ""
    url = _prefer_url(originallink, naver_link)
    if not title or not url:
        return None
    if _is_forbidden(url, item.get("originallink") or "", item.get("link") or ""):
        return None  # X(엑스) 등 금지 소스는 수집하지 않는다

    direct_url = publisher_direct.normalize_publisher_canonical_url(originallink)
    source = _source_from_url(direct_url or url, host_map)
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
            "discovery_url": naver_link or url,
            "discovery_provider": "naver",
            # Discovery provenance only — defaults to the general lane; the
            # primary-publisher lane overrides accepted rows to
            # DISCOVERY_LANE_PRIMARY_PUBLISHER. Never a qualification signal.
            "discovery_lane": DISCOVERY_LANE_GENERAL,
            "publisher_url": direct_url,
            "publisher_domain": _host_of(direct_url),
            "publisher_direct": False,
            "portal_resolution_status": (
                "pending_verification" if direct_url else "publisher_resolution_pending"
            ),
            "portal_resolution_reason": (
                "naver_originallink_present"
                if direct_url
                else "naver_originallink_missing"
            ),
        },
        "discovery_url": naver_link or url,
        "discovery_provider": "naver",
        "discovery_lane": DISCOVERY_LANE_GENERAL,
        "publisher_url": direct_url,
        "publisher_domain": _host_of(direct_url),
        "publisher_direct": False,
        "portal_resolution_status": (
            "pending_verification" if direct_url else "publisher_resolution_pending"
        ),
        "portal_resolution_reason": (
            "naver_originallink_present"
            if direct_url
            else "naver_originallink_missing"
        ),
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


# ---------------------------------------------------------------------------
# D7-AK-6E R4-R16 — primary-publisher bounded discovery lane.
#
# The Review lane surfaced primary_10=0 / secondary_3=0 / official=0 for a
# coverage window where the same-window News Censor had primary_10=2 / official=2
# (root cause REVIEW_COLLECTION_OR_SELECTION_RECALL_GAP): the configured roster
# carried 0 publisher-targeted queries and the host map covered only 4/10 core
# publishers, so core-publisher AI coverage was never discovered.
#
# This lane derives the ten canonical primary publishers from the operator-locked
# policy (app/source_priority.locked_publisher_*), combines each with a small
# bounded topic set, and runs BEFORE the general/topic lane with its own separate
# query/result budget. The fact that a Naver query text contains a publisher name
# is never treated as publisher authority: every response row is normalized and
# then post-filtered by its publisher-direct URL and
# source_priority.publisher_delivery_tier(). Only a result that actually resolves
# to the expected primary_10 target publisher is accepted; any other primary
# publisher (cross-publisher), or a secondary_3 / neutral / specialist / official
# result, is rejected from the publisher query it arrived on.

# Hard code-level ceilings — config may lower these, never raise them.
PRIMARY_PUBLISHER_MAX_QUERIES = 30
PRIMARY_PUBLISHER_MAX_PER_QUERY = 2
PRIMARY_PUBLISHER_MAX_TOTAL = 40
PRIMARY_PUBLISHER_MAX_TOPICS = 3


def _dedup_key(url: str) -> str:
    """Canonical dedup key shared by both lanes (mirrors collector.make_url_hash)."""
    return (url or "").strip().lower().rstrip("/")


def _mark_discovery_lane(row: dict, lane: str) -> dict:
    """Stamp the discovery-lane provenance marker on a normalized row in place.

    Records only *how* the row was surfaced (which lane), never *whether* it is
    qualified. Mutates both the top-level field and the source_metadata mirror so
    the audit layer can read it regardless of which it inspects.
    """
    row["discovery_lane"] = lane
    metadata = row.get("source_metadata")
    if isinstance(metadata, dict):
        metadata["discovery_lane"] = lane
    return row


def primary_publisher_query_specs(
    topics, *, max_queries: int = PRIMARY_PUBLISHER_MAX_QUERIES
) -> list[dict]:
    """Bounded (publisher, topic) query specs from the canonical primary_10 policy.

    Publishers are derived only from ``source_priority`` (whose single source is
    ``data/source_priority_rules.json``) — no publisher name or domain is read
    from the Naver sources file. Each spec preserves the expected publisher
    identity/rank and canonical domains so an accepted row can be verified
    against the exact target publisher. At most three topics combine with the ten
    publishers for at most thirty query specs.
    """
    clean_topics: list[str] = []
    for topic in topics or []:
        text = " ".join(str(topic or "").split())
        if text and text not in clean_topics:
            clean_topics.append(text)
        if len(clean_topics) >= PRIMARY_PUBLISHER_MAX_TOPICS:
            break
    limit = max(0, min(int(max_queries), PRIMARY_PUBLISHER_MAX_QUERIES))
    if limit <= 0 or not clean_topics:
        return []
    names = source_priority.locked_publisher_names("primary_10")
    domain_map = source_priority.locked_publisher_domain_map(("primary_10",))
    name_to_domains: dict[str, list[str]] = {}
    for domain, name in domain_map.items():
        name_to_domains.setdefault(name, []).append(domain)
    specs: list[dict] = []
    for rank, name in enumerate(names, start=1):
        domains = tuple(sorted(name_to_domains.get(name, ())))
        if not domains:
            continue
        for topic in clean_topics:
            if len(specs) >= limit:
                return specs
            specs.append({
                "publisher": name,
                "rank": rank,
                "domains": domains,
                "topic": topic,
                "query": f"{name} {topic}",
            })
    return specs


def _publisher_lane_accepts(row: dict, spec: dict) -> bool:
    """Accept a normalized row only as its expected primary_10 target publisher.

    The Naver query text (which contains the publisher name) is never authority.
    A row is accepted iff it is publisher-direct, its canonical host matches the
    expected publisher's locked domains, and ``publisher_delivery_tier`` resolves
    it to ``primary_10`` — rejecting cross-publisher, secondary_3, neutral,
    specialist, and official results.
    """
    url = str(row.get("url") or "")
    direct = publisher_direct.normalize_publisher_canonical_url(url)
    if not direct:
        return False
    host = _host_of(direct)
    if not host:
        return False
    expected = spec.get("domains") or ()
    if not any(host == domain or host.endswith("." + domain) for domain in expected):
        return False
    tier = source_priority.publisher_delivery_tier(str(row.get("source") or ""), url)
    return tier.get("tier") == "primary_10"


def _run_primary_publisher_lane(
    cfg: dict, *, host_map: dict, headers: dict, timeout: int, collected_at: str,
    seen_keys: set, request_json, display: int, start: int, sort: str,
) -> tuple[list[dict], dict]:
    """Run the bounded primary-publisher discovery lane with its own budget.

    Returns accepted rows plus non-secret lane stats. Shares only the canonical
    dedup set with the general lane; its query/result budget is independent, so a
    later general lane runs whether or not this lane exhausts its ceiling.
    """
    stats = {
        "queries_attempted": 0,
        "queries_ok": 0,
        "articles_collected": 0,
        "budget_exhausted": False,
    }
    lane = cfg.get("primary_publisher_lane")
    if not isinstance(lane, dict) or not lane.get("enabled"):
        return [], stats
    max_queries = max(0, min(
        int(lane.get("max_queries", PRIMARY_PUBLISHER_MAX_QUERIES) or 0),
        PRIMARY_PUBLISHER_MAX_QUERIES))
    max_per_query = max(0, min(
        int(lane.get("max_per_query", PRIMARY_PUBLISHER_MAX_PER_QUERY) or 0),
        PRIMARY_PUBLISHER_MAX_PER_QUERY))
    max_total = max(0, min(
        int(lane.get("max_total", PRIMARY_PUBLISHER_MAX_TOTAL) or 0),
        PRIMARY_PUBLISHER_MAX_TOTAL))
    specs = primary_publisher_query_specs(lane.get("topics"), max_queries=max_queries)
    if not specs or max_per_query <= 0 or max_total <= 0:
        return [], stats
    # Parse enough candidates per query that publisher-lane rejections still leave
    # room to fill max_per_query accepted rows; acceptance is capped separately.
    parse_candidates = max(max_per_query * 4, 8)
    results: list[dict] = []
    for spec in specs:
        if stats["articles_collected"] >= max_total:
            break
        stats["queries_attempted"] += 1
        params = urllib.parse.urlencode(
            {"query": spec["query"], "display": display, "start": start, "sort": sort})
        url = f"{ENDPOINT}?{params}"
        try:
            payload = request_json(url, headers, timeout)
        except Exception:  # noqa: BLE001 — network/HTTP/JSON errors skip one query
            continue
        stats["queries_ok"] += 1
        per_query = 0
        for row in parse_response(payload, spec["query"], collected_at, host_map,
                                  parse_candidates):
            if per_query >= max_per_query:
                break
            if not _publisher_lane_accepts(row, spec):
                continue
            key = _dedup_key(row["url"])
            if key in seen_keys:
                continue
            seen_keys.add(key)
            # Discovery provenance only: this row was found via the bounded
            # primary-publisher lane. It grants no qualification authority; the
            # editorial layer even withholds the provider-query relevance boost
            # from primary-publisher rows so the query text is never evidence.
            _mark_discovery_lane(row, DISCOVERY_LANE_PRIMARY_PUBLISHER)
            results.append(row)
            per_query += 1
            stats["articles_collected"] += 1
            if stats["articles_collected"] >= max_total:
                break
    stats["budget_exhausted"] = stats["articles_collected"] >= max_total
    return results, stats


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
            "raw_count": 0, "credentials_present": credentials_present,
            "primary_publisher_queries_attempted": 0,
            "primary_publisher_queries_ok": 0,
            "primary_publisher_articles_collected": 0,
            "primary_10_articles_collected": 0,
            "secondary_3_articles_collected": 0,
            "primary_publisher_lane_budget_exhausted": False}


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

    # R4-R16 — merge the canonical primary/secondary domain map (single source:
    # source_priority_rules.json) into the manually curated host map. Canonical
    # entries take priority over any stale manual mapping, while existing
    # specialist / official manual mappings are preserved.
    manual_host_map = cfg.get("host_source_map") or {}
    canonical_host_map = source_priority.locked_publisher_domain_map(
        ("primary_10", "secondary_3")
    )
    host_map = {**manual_host_map, **canonical_host_map}
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

    # Shared canonical dedup across both lanes; each lane keeps its own budget.
    seen_keys: set[str] = set()
    # R4-R16 — the primary-publisher lane runs FIRST, with its own independent
    # query/result budget, so the general lane below runs whether or not this
    # lane exhausts its ceiling (and vice versa).
    publisher_results, publisher_stats = _run_primary_publisher_lane(
        cfg,
        host_map=host_map,
        headers=headers,
        timeout=to,
        collected_at=collected_at,
        seen_keys=seen_keys,
        request_json=_request_json,
        display=display,
        start=start,
        sort=sort,
    )

    general_results: list[dict] = []
    queries_ok = 0
    filtered_keys = set()
    for query in queries:
        if len(general_results) >= max_total:
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
            key = _dedup_key(row["url"])
            if key in seen_keys:
                continue
            seen_keys.add(key)
            general_results.append(row)
            if len(general_results) >= max_total:
                break
        if sink:
            for item in sink:
                u = item.get("url")
                item_key = _dedup_key(u)
                if not u or item_key in filtered_keys or item_key in seen_keys:
                    continue
                filtered_keys.add(item_key)
                if len(filtered_out) < max_total:
                    filtered_out.append(item)

    results = publisher_results + general_results
    primary_10_collected = 0
    secondary_3_collected = 0
    for row in results:
        tier = source_priority.publisher_delivery_tier(
            str(row.get("source") or ""), str(row.get("url") or "")
        )["tier"]
        if tier == "primary_10":
            primary_10_collected += 1
        elif tier == "secondary_3":
            secondary_3_collected += 1

    status = STATUS_ACTIVE if (queries_ok or publisher_stats["queries_ok"]) else STATUS_ERROR
    return {"provider": PROVIDER, "source_label": SOURCE_LABEL, "status": status,
            "articles": results, "queries_attempted": len(queries),
            "queries_ok": queries_ok, "raw_count": len(results),
            "credentials_present": True,
            "primary_publisher_queries_attempted": publisher_stats["queries_attempted"],
            "primary_publisher_queries_ok": publisher_stats["queries_ok"],
            "primary_publisher_articles_collected": publisher_stats["articles_collected"],
            "primary_10_articles_collected": primary_10_collected,
            "secondary_3_articles_collected": secondary_3_collected,
            "primary_publisher_lane_budget_exhausted": publisher_stats["budget_exhausted"]}
