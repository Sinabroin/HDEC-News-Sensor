"""Collector 도메인 — mock 기사 로드, 정규화, dedup, 저장 호출.

P0-A 데이터 소스는 data/mock_articles.json 단 하나다 (rules.md §2).
이 파일은 네트워크 라이브러리를 import하지 않으며, 점수 계산·insight·알림을 하지 않는다.
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


def _to_article_row(raw: dict, queries: list[str], collected_at: str) -> dict:
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
        "signal_origin": "Mock",
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


def run(mode: str = "mock") -> dict:
    """mock 기사 로드 → 정규화 → dedup(배치+DB) → articles 저장.

    P0-A에는 mock 경로만 존재한다. 다른 mode는 명시적으로 거부해
    어떤 경로로도 외부 호출이 생기지 않게 한다.
    """
    if mode != "mock":
        raise ValueError("Day-1 P0-A는 APP_MODE=mock만 지원한다 (rules.md §2)")

    raw_articles = _load_mock_articles()
    queries = _load_topic_queries()
    collected_at = db.now_iso()

    rows = [_to_article_row(raw, queries, collected_at) for raw in raw_articles]
    deduped = _dedup(rows)

    existing_titles = db.get_existing_normalized_titles()
    inserted = 0
    for row in deduped:
        if row["normalized_title"] and row["normalized_title"] in existing_titles:
            continue
        if db.insert_article(row):
            inserted += 1

    return {
        "collected": len(rows),
        "deduplicated": len(deduped),
        "inserted": inserted,
        "fallback_used": False,
    }
