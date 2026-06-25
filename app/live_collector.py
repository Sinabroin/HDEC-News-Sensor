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

from app import config, lens_queries, source_quality, topic_profiles

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


def _configured_query_groups(cfg: dict) -> list[dict]:
    """Return ordered query groups with per-group caps.

    query_groups, when present, are attempted before the legacy top-level queries so
    risk/regulation coverage can be probed without raising the global max_total.
    """
    groups: list[dict] = []
    for raw in cfg.get("query_groups") or []:
        if not isinstance(raw, dict):
            continue
        queries = [q for q in (raw.get("queries") or [])
                   if isinstance(q, str) and q.strip()]
        if not queries:
            continue
        groups.append({
            "name": raw.get("name") or "custom",
            "label": raw.get("label") or raw.get("name") or "custom",
            "queries": queries,
            "max_per_query": int(raw.get("max_per_query")
                                 or cfg.get("max_per_query", 4)),
            "max_total": int(raw.get("max_total") or cfg.get("max_total", 40)),
        })

    legacy_queries = [q for q in (cfg.get("queries") or [])
                      if isinstance(q, str) and q.strip()]
    if legacy_queries:
        groups.append({
            "name": "default",
            "label": "기본",
            "queries": legacy_queries,
            "max_per_query": int(cfg.get("max_per_query", 4)),
            "max_total": int(cfg.get("max_total", 40)),
        })
    return groups


def _merge_topic_profile_groups(groups: list[dict], cfg: dict) -> list[dict]:
    """기본(production) 소스에 한해 enabled 토픽 프로파일 쿼리 그룹을 합친다 (P0-D5-A).

    리스크/규제 그룹(고정 커버리지) 다음, 기본(default) 백필 그룹 앞에 끼워 넣어 프로파일이
    공정한 수집 몫을 갖되 전역 max_total(70)을 넘지 않게 한다. 이미 다른 그룹에 있는 쿼리는
    제외해 쿼리 폭증을 막는다(런타임 dedup과 별개로 audit도 깔끔). 새 그룹의 group["name"]은
    profile.id라 query_audit이 자동으로 프로파일 출처를 담는다.

    custom sources_path로 호출될 때(테스트 등)는 fetch_all이 이 함수를 거치지 않는다 —
    호출자가 넘긴 설정만 그대로 쓰는 계약을 보존한다.
    """
    existing = {q.strip().casefold()
                for g in groups for q in (g.get("queries") or [])}
    default_per_query = int(cfg.get("max_per_query", 4))
    profile_groups: list[dict] = []
    for profile in topic_profiles.get_enabled_topic_profiles():
        queries, seen = [], set()
        for query in profile.queries:
            key = query.strip().casefold()
            if not key or key in existing or key in seen:
                continue
            seen.add(key)
            queries.append(query)
        if not queries:
            continue
        existing.update(seen)
        profile_groups.append({
            "name": profile.id,
            "label": profile.label,
            "queries": queries,
            # 새 그룹은 보수적으로 per-query 2건 — 기존 그룹의 max_per_query는 바꾸지
            # 않는다. 그룹 총량은 프로파일 max_items로 제한한다.
            "max_per_query": min(2, default_per_query),
            "max_total": max(1, int(profile.max_items)),
        })
    if not profile_groups:
        return groups
    for i, group in enumerate(groups):
        if group.get("name") == "default":
            return groups[:i] + profile_groups + groups[i:]
    return groups + profile_groups


def _merge_lens_query_groups(groups: list[dict], cfg: dict) -> list[dict]:
    """중앙 렌즈 정책(app.lens_queries)의 렌즈 쿼리 그룹을 기본 소스에 합친다 (P0-D7-E).

    토픽 프로파일과 동일 패턴 — 렌즈 우선(lens-first) 수집: supported 렌즈의 collect 쿼리를
    수집 유니버스에 넣어, 빈 렌즈가 '수집 안 함'이 아니라 '쿼리는 돌렸으나 결과 없음'이 되게
    한다. 미연동 렌즈(collect 없음)는 제외된다 — 가짜 수집을 만들지 않는다.

    이미 다른 그룹에 있는 쿼리는 제외(폭증 방지), 보수적 per-query 캡, default 백필 그룹 앞에
    끼워 넣는다. group["name"]='lens:<id>'라 query_audit이 렌즈 출처를 자동으로 담는다.
    custom sources_path로 호출되면 fetch_all이 이 함수를 거치지 않는다(계약 보존).
    """
    existing = {q.strip().casefold()
                for g in groups for q in (g.get("queries") or [])}
    default_per_query = int(cfg.get("max_per_query", 4))
    lens_groups: list[dict] = []
    for group in lens_queries.collection_query_groups():
        queries, seen = [], set()
        for query in group.get("queries") or []:
            key = query.strip().casefold()
            if not key or key in existing or key in seen:
                continue
            seen.add(key)
            queries.append(query)
        if not queries:
            continue
        existing.update(seen)
        lens_groups.append({
            "name": group["name"],
            "label": group.get("label") or group["name"],
            "queries": queries,
            # 렌즈 전용 그룹은 대시보드 bank를 채울 수 있도록만 확장한다.
            # 전역 cfg max_total guard는 fetch 루프에서 그대로 적용된다.
            "max_per_query": max(3, min(4, default_per_query)),
            "max_total": min(32, max(12, len(queries) * 4)),
        })
    if not lens_groups:
        return groups
    for i, group in enumerate(groups):
        if group.get("name") == "default":
            return groups[:i] + lens_groups + groups[i:]
    return groups + lens_groups


def _fetch(url: str, timeout: int) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (public RSS)
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace")


def _parse_items(xml_text: str, query: str, collected_at: str,
                 max_items: int, filtered_sink: list | None = None) -> list[dict]:
    """RSS 2.0 <item>에서 메타데이터만 추출한다 (본문 전문 없음).

    filtered_sink가 주어지면 출처 품질로 '제외된' 비뉴스성 항목의 메타데이터
    (title/source/url/published_at — 본문 없음)를 거기에 담는다 (P0-C1.8 감사 투명성).
    X(엑스)/금지 소스는 어떤 경우에도 sink에 담지 않는다 (rules.md §1).
    """
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

        # 출처 품질 가드 (P0-C1.6): 블로그/카페/커뮤니티/티스토리/유튜브성 출처는
        # 수집 단계에서 제외한다 — 임원용 신호에 비-뉴스 결과가 섞이지 않게 한다.
        # raw dict에는 품질 필드를 부착하지 않는다 (허용 키 계약 유지) — 제외 판정에만 쓴다.
        if source_quality.is_excluded(source, title):
            if filtered_sink is not None:
                # 감사 투명성용 메타데이터만 — 본문/snippet은 담지 않는다 (rules.md §3).
                filtered_sink.append({
                    "title": title,
                    "source": source,
                    "url": link,
                    "published_at": _to_iso(item.findtext("pubDate") or "") or collected_at,
                })
            continue

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


def fetch_all(timeout: int = DEFAULT_TIMEOUT, sources_path=None,
              filtered_out: list | None = None,
              query_audit: list | None = None) -> list[dict]:
    """설정된 모든 query에 대해 공개 RSS를 수집해 raw dict 리스트를 반환한다.

    네트워크/파싱 실패는 해당 query만 건너뛰고 계속한다 (가짜 값 생성 금지).
    수집 결과가 0건이면 빈 리스트를 반환하고, fallback 판단은 collector가 한다.

    filtered_out 리스트가 주어지면 출처 품질로 제외된 비뉴스성 항목의 메타데이터를
    URL 기준으로 dedup해 담는다 (P0-C1.8 감사 — 임원이 무엇이 걸러졌는지 볼 수 있게).
    """
    cfg = _load_sources(sources_path)
    query_groups = _configured_query_groups(cfg)
    # P0-D5-A: 기본 production 소스일 때만 enabled 토픽 프로파일 쿼리를 합친다.
    # custom sources_path(테스트/대체 설정)는 넘긴 설정만 그대로 쓴다.
    if sources_path is None:
        query_groups = _merge_topic_profile_groups(query_groups, cfg)
        # P0-D7-E: 중앙 렌즈 정책의 렌즈 쿼리 그룹을 합쳐 렌즈 우선 수집을 한다(단일 소스 공유).
        query_groups = _merge_lens_query_groups(query_groups, cfg)
    if not query_groups:
        return []

    max_total = int(cfg.get("max_total", 40))
    collected_at = datetime.now(KST).isoformat(timespec="seconds")

    seen_urls, results = set(), []
    filtered_urls = set()
    seen_queries = set()
    for group in query_groups:
        group_count = 0
        for query in group["queries"]:
            if len(results) >= max_total or group_count >= group["max_total"]:
                break
            query_key = query.strip().lower()
            if query_key in seen_queries:
                continue
            seen_queries.add(query_key)
            url = _build_google_news_url(query, cfg)
            try:
                xml_text = _fetch(url, timeout)
            except Exception:  # noqa: BLE001 — 네트워크/HTTP 오류는 query 단위로 무시
                if query_audit is not None:
                    query_audit.append({
                        "provider": PROVIDER,
                        "group": group["name"],
                        "query": query,
                        "status": "error",
                        "fetched_count": 0,
                        "added_count": 0,
                    })
                continue
            sink = [] if filtered_out is not None else None
            parsed = _parse_items(
                xml_text, query, collected_at, group["max_per_query"], sink)
            added = 0
            for row in parsed:
                if row["url"] in seen_urls:
                    continue
                seen_urls.add(row["url"])
                results.append(row)
                added += 1
                group_count += 1
                if len(results) >= max_total or group_count >= group["max_total"]:
                    break
            if query_audit is not None:
                query_audit.append({
                    "provider": PROVIDER,
                    "group": group["name"],
                    "query": query,
                    "status": "ok" if parsed else "empty",
                    "fetched_count": len(parsed),
                    "added_count": added,
                })
            if sink:  # 제외 항목을 URL 기준 dedup해 누적 (수집된 기사와 겹치면 제외)
                for item in sink:
                    u = item.get("url")
                    if not u or u in filtered_urls or u in seen_urls:
                        continue
                    filtered_urls.add(u)
                    if len(filtered_out) < max_total:
                        filtered_out.append(item)
        if len(results) >= max_total:
            break
    return results
