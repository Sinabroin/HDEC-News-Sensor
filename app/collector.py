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
    """공개 RSS 수집 파이프라인. 0건이면 live를 주장하지 않고 mock으로 fallback한다.

    네트워크 import는 이 함수 안에서만 일어난다 (rules.md §10 D3 — collector 모듈
    레벨 네트워크 import 금지). live가 실패하면 가짜 live 값을 만들지 않고
    mock fallback을 명시 라벨과 함께 반환한다 ("live 주장 금지").
    """
    from app import live_collector

    # 출처 품질로 수집 단계에서 제외된 비뉴스성 항목을 감사용으로 함께 받는다 (P0-C1.8).
    # 본문 없이 title/source/url/published_at 메타데이터만 담긴다.
    source_filtered: list[dict] = []
    try:
        raw_articles = live_collector.fetch_all(filtered_out=source_filtered)
    except Exception:  # noqa: BLE001 — 네트워크/파싱 오류는 fallback으로 흡수
        raw_articles = []
        source_filtered = []

    if not raw_articles:
        return _run_mock(fallback=True, attempted="live")

    collected, deduped, inserted = _ingest(raw_articles, "Live RSS")
    return {
        "collected": collected,
        "deduplicated": deduped,
        "inserted": inserted,
        "news_data_mode": "live",
        "news_source": live_collector.SOURCE_LABEL,
        "attempted_mode": "live",
        "fallback_used": False,
        "source_filtered": source_filtered,
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
