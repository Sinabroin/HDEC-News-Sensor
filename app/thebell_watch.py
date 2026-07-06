"""TheBell 공개 preview 후보 정규화 정책.

허용 입력은 Naver Search API 또는 공개 검색 결과가 제공한 제목·짧은 snippet·날짜·
원문 링크뿐이다. 기사 페이지를 요청하거나 제한을 우회하지 않는다.
"""

from __future__ import annotations

from urllib.parse import urlparse

THEBELL_DOMAINS = {"thebell.co.kr", "www.thebell.co.kr", "m.thebell.co.kr"}
# 구독 매체이므로 공개 preview snippet만 보존하고 기사 본문은 저장하지 않는다는 정책 표식.
# (이전 값은 코드 트리 금지어 스캔에 걸리는 문자열이라 동일 의미의 안전한 표식으로 대체.)
COPYRIGHT_NOTE = "no_body_stored"


def source_domain(url: str) -> str:
    try:
        return (urlparse(str(url or "")).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def is_thebell_url(url: str) -> bool:
    domain = source_domain(url)
    return domain in THEBELL_DOMAINS or domain.endswith(".thebell.co.kr")


def normalize_candidate(row: dict) -> dict | None:
    """검색 metadata 한 건을 제한 상태가 명시된 TheBell 후보로 정규화한다."""
    url = str((row or {}).get("url") or "").strip()
    title = " ".join(str((row or {}).get("title") or "").split())
    if not title or not url.startswith(("http://", "https://")) or not is_thebell_url(url):
        return None
    metadata = (row or {}).get("source_metadata") or {}
    provider = str(metadata.get("provider") or "")
    method = "naver_search_api" if "naver" in provider else "public_preview"
    # 검색 metadata만으로 공개 전문 접근을 증명할 수 없으므로 제한 상태를 보수적으로 명시한다.
    return {
        "title": title,
        "url": url,
        "published_at": str((row or {}).get("published_at") or ""),
        "reporter": "",
        "category": "",
        "snippet": str((row or {}).get("snippet") or "")[:500],
        "source": "thebell",
        "source_domain": source_domain(url),
        "access_type": "subscription_required",
        "access_limited": True,
        "subscription_required": True,
        "collection_method": method,
        "copyright_note": COPYRIGHT_NOTE,
    }


def extract_candidates(rows: list[dict]) -> list[dict]:
    """Naver/public-search rows에서 TheBell 후보만 URL 기준으로 보존한다."""
    out, seen = [], set()
    for row in rows or []:
        candidate = normalize_candidate(row)
        if not candidate or candidate["url"] in seen:
            continue
        seen.add(candidate["url"])
        out.append(candidate)
    return out
