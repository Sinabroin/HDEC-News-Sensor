"""D7-AF 뉴스 coverage query group 정책.

Google News RSS와 Naver News Search API가 같은 정책 파일을 읽는다. 이 모듈은
정책 로드와 결정적 분류만 하며 네트워크·DB·비밀값을 다루지 않는다.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_POLICY_PATH = Path(__file__).resolve().parent.parent / "data" / "news_coverage_queries.json"
_TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣.]+")
SURFACE_CATEGORY_IDS = frozenset({"biz", "peers", "hdec", "safety", "global", "ai"})


def _load() -> dict:
    try:
        data = json.loads(_POLICY_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def collection_query_groups() -> list[dict]:
    """수집기가 소비할 bounded query group 목록."""
    groups = []
    for raw in _load().get("groups") or []:
        if not isinstance(raw, dict):
            continue
        queries = [q.strip() for q in raw.get("queries") or []
                   if isinstance(q, str) and q.strip()]
        if not queries:
            continue
        groups.append({
            "name": str(raw.get("name") or "coverage"),
            "label": str(raw.get("label") or raw.get("name") or "coverage"),
            "queries": queries,
            "max_per_query": max(1, min(5, int(raw.get("max_per_query") or 3))),
            "max_total": max(1, min(30, int(raw.get("max_total") or 18))),
            "surface_categories": [
                str(value)
                for value in raw.get("surface_categories") or []
                if str(value) in SURFACE_CATEGORY_IDS
            ],
        })
    return groups


def all_queries() -> list[str]:
    """Naver adapter가 기존 roster에 합칠 query 목록."""
    return [query for group in collection_query_groups()
            for query in group.get("queries") or []]


def query_group_for_query(query: str) -> str:
    target = " ".join(str(query or "").split()).casefold()
    for group in collection_query_groups():
        if any(target == " ".join(q.split()).casefold() for q in group["queries"]):
            return group["name"]
    return ""


def surface_categories_for_group(group_name: str) -> list[str]:
    """Return declared News Censor categories for one bounded query group."""
    for group in collection_query_groups():
        if group["name"] == str(group_name or ""):
            return list(group.get("surface_categories") or [])
    return []


def surface_query_coverage(query_audit: list[dict]) -> dict[str, dict[str, int]]:
    """Aggregate query attempts/results by public category without query text."""
    output = {
        category: {"attempted_count": 0, "successful_count": 0, "added_count": 0}
        for category in sorted(SURFACE_CATEGORY_IDS)
    }
    for row in query_audit or []:
        if not isinstance(row, dict) or not str(row.get("query") or "").strip():
            continue
        categories = surface_categories_for_group(str(row.get("group") or ""))
        for category in categories:
            output[category]["attempted_count"] += 1
            if row.get("status") in {"ok", "empty"}:
                output[category]["successful_count"] += 1
            output[category]["added_count"] += max(0, int(row.get("added_count") or 0))
    return output


def _tokens(text: str) -> set[str]:
    return {token.casefold() for token in _TOKEN_RE.findall(text or "") if len(token) >= 2}


def query_groups_for_text(title: str, snippet: str = "") -> list[str]:
    """fixture/live row가 어느 coverage group 근거를 갖는지 결정적으로 분류한다."""
    haystack = f"{title} {snippet}".casefold()
    hay_tokens = _tokens(haystack)
    matched = []
    for raw in _load().get("groups") or []:
        name = str(raw.get("name") or "")
        aliases = [str(x).strip() for x in raw.get("match_any") or [] if str(x).strip()]
        direct = any(alias.casefold() in haystack for alias in aliases)
        query_match = False
        for query in raw.get("queries") or []:
            q_tokens = _tokens(str(query))
            if q_tokens and len(q_tokens & hay_tokens) >= min(2, len(q_tokens)):
                query_match = True
                break
        if name and (direct or query_match):
            matched.append(name)
    return matched
