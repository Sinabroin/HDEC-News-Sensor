"""Collector 도메인 — 기사 로드, 정규화, dedup, 저장 호출.

NEWS_MODE=mock(기본)은 data/mock_articles.json 단 하나만 읽는다 (rules.md §2 — mock은
네트워크 0건). NEWS_MODE=live(운영자가 명시적으로 설정한 경우만)는 app/live_collector.py가
공개 RSS에서 가져온 raw dict를 정규화한다.

이 파일은 네트워크 라이브러리를 모듈 레벨에서 import하지 않는다 (rules.md §10 D3). live
경로일 때만 live_collector를 지연 import한다. 점수 계산·insight·알림은 하지 않는다.
정규화/dedup/저장 파이프라인은 mock·live가 완전히 동일하게 공유한다.
"""

import hashlib
import json
import re
import unicodedata

from app import config, db

SNIPPET_MAX_LEN = 500
ALLOWED_METADATA_KEYS = (
    "provider", "query", "source_url", "collected_at", "provider_response_id",
)


def normalize_title(title: str) -> str:
    """유사 제목 dedup용 정규화: 괄호 머리말 제거, 소문자화, 한글/영숫자만 유지."""
    text = unicodedata.normalize("NFKC", title or "")
    text = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", text)
    text = text.lower()
    return re.sub(r"[^0-9a-z가-힣]+", "", text)


def make_url_hash(url: str) -> str:
    canonical = (url or "").strip().lower().rstrip("/")
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _load_mock_articles() -> list[dict]:
    path = config.DATA_DIR / "mock_articles.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _load_topic_queries() -> list[str]:
    path = config.DATA_DIR / "topics.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8")).get("queries", [])


def _match_topic_candidates(title: str, snippet: str, queries: list[str]) -> list[str]:
    """topic query의 토큰 과반이 제목+snippet에 등장하면 후보로 본다 (최대 3개)."""
    haystack = f"{title} {snippet}".lower()
    matched = []
    for query in queries:
        tokens = [t for t in query.lower().split() if t]
        if not tokens:
            continue
        hits = sum(1 for t in tokens if t in haystack)
        if hits * 2 >= len(tokens):
            matched.append(query)
    return matched[:3]


def _to_article_row(raw: dict, queries: list[str], collected_at: str,
                    signal_origin: str = "Mock") -> dict:
    title = (raw.get("title") or "").strip()
    snippet = (raw.get("snippet") or "")[:SNIPPET_MAX_LEN]
    metadata = raw.get("source_metadata") or {}
    safe_metadata = {k: metadata[k] for k in ALLOWED_METADATA_KEYS if k in metadata}
    return {
        "id": raw.get("id"),
        "title": title,
        "normalized_title": normalize_title(title),
        "source": raw.get("source"),
        "published_at": raw.get("published_at"),
        "collected_at": collected_at,
        "url": raw.get("url"),
        "url_hash": make_url_hash(raw.get("url")),
        "snippet": snippet,
        "topic_candidates": json.dumps(
            _match_topic_candidates(title, snippet, queries), ensure_ascii=False
        ),
        "signal_origin": signal_origin,
        "source_metadata_json": json.dumps(safe_metadata, ensure_ascii=False),
        "status": "collected",
    }


def _dedup(rows: list[dict]) -> list[dict]:
    """배치 내 dedup: url_hash 우선, 다음 normalized_title. 첫 기사를 유지한다."""
    seen_hashes, seen_titles, kept = set(), set(), []
    for row in rows:
        if row["url_hash"] in seen_hashes:
            continue
        if row["normalized_title"] and row["normalized_title"] in seen_titles:
            continue
        seen_hashes.add(row["url_hash"])
        seen_titles.add(row["normalized_title"])
        kept.append(row)
    return kept


# news.google 리다이렉트는 canonical 원문이 아니다 — 원문(originallink) 우선 판정에 쓴다.
_GOOGLE_REDIRECT_MARK = "news.google."


def merge_provider_articles(rows: list[dict]) -> list[dict]:
    """여러 provider(Google RSS + Naver API)의 raw 기사를 교차 dedup해 합친다 (P0-D2).

    설계 (coverage와 ranking 분리: 여기선 '하나의 정규화된 기사'만 만든다):
    - dedup 키: url_hash(정확 URL) 우선, 다음 normalized_title(동일 사건·다른 URL).
    - 동일 사건이 여러 provider에서 오면 하나만 남기고, source_metadata.provider를
      결합 토큰('google_news_rss+naver_news_api')으로 합쳐 provider 근거를 보존한다
      (rules.md §3 허용 키 'provider'만 쓴다 — naver_link 등 새 키를 만들지 않는다).
    - 원문 URL을 우선한다: news.google 리다이렉트보다 언론사 원문(originallink)을 채택하고,
      그때 더 구체적인 source(매체명)도 함께 받는다.
    - Naver-only 기사(중복 아님)는 절대 잃지 않는다 — 새 항목으로 남는다.

    입력은 raw dict(live_collector / naver_news_provider 형태)이며, 반환도 같은 raw dict다
    (collector._to_article_row가 이후 정규화/저장한다). 점수·등급은 다루지 않는다.
    """
    kept: list[dict] = []
    by_url: dict[str, int] = {}
    by_title: dict[str, int] = {}
    providers: list[set] = []

    for raw in rows:
        url = raw.get("url") or ""
        title = raw.get("title") or ""
        uh = make_url_hash(url)
        nt = normalize_title(title)
        prov = ((raw.get("source_metadata") or {}).get("provider")) or "unknown"

        idx = by_url.get(uh)
        if idx is None and nt:
            idx = by_title.get(nt)

        if idx is None:
            entry = dict(raw)
            entry["source_metadata"] = dict(raw.get("source_metadata") or {})
            kept.append(entry)
            i = len(kept) - 1
            by_url[uh] = i
            if nt:
                by_title.setdefault(nt, i)
            providers.append({prov})
            continue

        # 중복 — provider 근거 병합 + 원문(originallink) URL 우선
        providers[idx].add(prov)
        existing = kept[idx]
        ex_url = (existing.get("url") or "").lower()
        if _GOOGLE_REDIRECT_MARK in ex_url and _GOOGLE_REDIRECT_MARK not in url.lower():
            displaced = existing.get("url") or ""   # news.google 경유 URL — provenance로 보존
            by_url.pop(make_url_hash(displaced), None)
            existing["url"] = url   # 퍼블리셔 원문(originallink) 우선 — 외부 href가 직링크가 된다
            meta = dict(existing.get("source_metadata") or {})
            # source_url에는 경유(aggregator) URL을 보존한다(허용 키만 씀 — rules.md §3). 원문
            # href는 url(퍼블리셔 직링크)이 담당하고, 감사/모델은 '어디서 발견했는지'를 이 값으로
            # 추적한다(Google News 경유 링크를 잃지 않되 href로 승격하지도 않는다).
            if displaced:
                meta["source_url"] = displaced
            existing["source_metadata"] = meta
            if raw.get("source") and existing.get("source") in (None, "", "출처 미상"):
                existing["source"] = raw["source"]
            by_url[make_url_hash(url)] = idx

    for i, entry in enumerate(kept):
        meta = dict(entry.get("source_metadata") or {})
        meta["provider"] = "+".join(sorted(providers[i]))
        entry["source_metadata"] = meta
    return kept


def _provider_dedup_counts(rows: list[dict]) -> tuple[int, int, int]:
    """merge_provider_articles 결과에서 provider별 dedup 후 기여도를 센다 (비밀값 0건).

    반환: (google_only, naver_only, both). source_metadata.provider 결합 토큰
    ('google_news_rss+naver_news_api')을 집합으로 해석한다 — 감사/provider_status가
    Naver 기사가 dedup/병합 이후 몇 건 살아남았는지(naver_only + both) 정직하게 본다.
    """
    g_only = n_only = both = 0
    for row in rows:
        toks = {p for p in
                ((row.get("source_metadata") or {}).get("provider") or "").split("+") if p}
        has_g = "google_news_rss" in toks
        has_n = "naver_news_api" in toks
        if has_g and has_n:
            both += 1
        elif has_n:
            n_only += 1
        elif has_g:
            g_only += 1
    return g_only, n_only, both


def _ingest(raw_articles: list[dict], signal_origin: str) -> tuple[int, int, int]:
    """raw 기사들을 정규화 → dedup(배치+DB) → articles 저장. (collected, deduped, inserted)."""
    queries = _load_topic_queries()
    collected_at = db.now_iso()

    rows = [_to_article_row(raw, queries, collected_at, signal_origin)
            for raw in raw_articles]
    deduped = _dedup(rows)

    existing_titles = db.get_existing_normalized_titles()
    inserted = 0
    for row in deduped:
        if row["normalized_title"] and row["normalized_title"] in existing_titles:
            continue
        if db.insert_article(row):
            inserted += 1
    return len(rows), len(deduped), inserted


def _run_mock(fallback: bool = False, attempted: str = "mock") -> dict:
    """mock 기사 파이프라인. fallback=True면 live 실패 후 대체된 것임을 표기한다."""
    collected, deduped, inserted = _ingest(_load_mock_articles(), "Mock")
    return {
        "collected": collected,
        "deduplicated": deduped,
        "inserted": inserted,
        "news_data_mode": "mock",
        "news_source": "mock_fallback" if fallback else "mock",
        "attempted_mode": attempted,
        "fallback_used": fallback,
    }


def _run_live() -> dict:
    """공개 provider 수집 파이프라인. 0건이면 live를 주장하지 않고 mock으로 fallback한다.

    P0-D2: Google News RSS(기본 provider)에 더해 선택적 Naver News Search API(보조 provider)를
    합친다. 두 provider 결과를 교차 dedup(merge_provider_articles)한 뒤 동일한 정규화/저장
    파이프라인에 넣는다 — coverage(폭넓은 수집)와 ranking(점수/등급)은 분리한다.

    네트워크 import는 이 함수 안에서만 일어난다 (rules.md §10 D3 — collector 모듈 레벨
    네트워크 import 금지). Naver는 기본 off(disabled)이며 자격증명이 없으면 정직하게 skip한다
    (전체 수집을 실패시키지 않는다). live가 0건이면 가짜 live를 만들지 않고 mock으로 fallback한다.
    """
    from app import live_collector, naver_news_provider

    # 출처 품질로 수집 단계에서 제외된 비뉴스성 항목을 감사용으로 함께 받는다 (P0-C1.8).
    source_filtered: list[dict] = []
    google_query_audit: list[dict] = []
    try:
        google_rows = live_collector.fetch_all(
            filtered_out=source_filtered, query_audit=google_query_audit)
    except Exception:  # noqa: BLE001 — 네트워크/파싱 오류는 fallback으로 흡수
        google_rows = []
        source_filtered = []
        google_query_audit = []
    google_status = "active" if google_rows else "skipped"

    # D7-AE-RC1 — 원문 URL 최선노력 해석. fetch_all 자체는 오프라인 테스트 표면이라 자동
    # 실행하지 않으므로, 실제 live 진입점인 여기서만 명시적으로 켠다. 실패해도(네트워크
    # 없음/타임아웃/포맷 변경) google_rows는 원래 값 그대로 쓰인다 — 수집을 막지 않는다.
    if google_rows:
        try:
            live_collector.resolve_publisher_urls(google_rows)
        except Exception:  # noqa: BLE001 — 최선노력, 실패해도 수집 결과는 그대로 진행
            pass

    # Naver 보조 provider — 기본 off면 네트워크 0건(status=disabled), 자격증명 없으면 정직 skip.
    naver_filtered: list[dict] = []
    try:
        naver_result = naver_news_provider.fetch(filtered_out=naver_filtered)
    except Exception:  # noqa: BLE001 — leaf가 흡수하지만 방어적으로 한 번 더
        naver_result = {"provider": naver_news_provider.PROVIDER,
                        "status": naver_news_provider.STATUS_ERROR, "articles": []}
    naver_rows = naver_result.get("articles") or []

    # 교차 dedup — Naver-only는 보존하고, 동일 사건은 provider 근거를 합쳐 하나로 만든다.
    combined = merge_provider_articles(google_rows + naver_rows)
    if not combined:
        return _run_mock(fallback=True, attempted="live")

    # 감사용 제외 항목 — 두 provider의 것을 URL 기준 dedup해 합친다.
    seen_filtered = {f.get("url") for f in source_filtered}
    for item in naver_filtered:
        u = item.get("url")
        if u and u not in seen_filtered:
            seen_filtered.add(u)
            source_filtered.append(item)

    _, deduped, inserted = _ingest(combined, "Live RSS")
    labels = []
    if google_rows:
        labels.append(live_collector.SOURCE_LABEL)
    if naver_rows:
        labels.append(naver_news_provider.SOURCE_LABEL)
    # provider별 dedup 후 기여도 — combined의 결합 provider 토큰에서 파생 (비밀값 0건).
    g_only, n_only, both = _provider_dedup_counts(combined)
    return {
        # collected = 두 provider 원시 수집 합계, deduplicated = 교차 dedup 후 고유 기사 수.
        "collected": len(google_rows) + len(naver_rows),
        "deduplicated": deduped,
        "inserted": inserted,
        "news_data_mode": "live",
        "news_source": " + ".join(labels) or live_collector.SOURCE_LABEL,
        "attempted_mode": "live",
        "fallback_used": False,
        "source_filtered": source_filtered,
        "google_query_audit": google_query_audit,
        # provider 상태 — 비밀값 0건(자격증명은 유무 bool만). 감사/운영자/대시보드가 어느
        # provider가 active/skip/error이고 각 단계(raw→dedup)에서 몇 건이 살아남았는지 본다.
        # naver_only/dedup_merged로 Naver가 '보조 코드'가 아니라 실제 1급 provider임을 기록한다.
        "provider_status": {
            "google_news_rss": {
                "status": google_status,
                "raw_count": len(google_rows),
                "after_dedup_count": g_only + both,
            },
            "naver_news_api": {
                "status": naver_result.get("status"),
                "raw_count": len(naver_rows),
                "after_dedup_count": n_only + both,
                "naver_only_count": n_only,
                "dedup_merged_count": both,
                "credentials_present": bool(naver_result.get("credentials_present")),
            },
            "both_count": both,
        },
    }


def run(mode: str = "mock") -> dict:
    """기사 수집 진입점. NEWS_MODE에 따라 mock/live 경로를 고른다.

    NEWS_MODE=mock(기본)은 네트워크 0건이며 data/mock_articles.json만 읽는다.
    NEWS_MODE=live는 공개 RSS를 시도하고, 실패하면 mock으로 fallback한다 (가짜 live 금지).
    `mode` 인자는 API 하위호환을 위해 유지하지만 분기는 config.NEWS_MODE가 결정한다.
    """
    if config.NEWS_MODE == "live":
        return _run_live()
    return _run_mock()
