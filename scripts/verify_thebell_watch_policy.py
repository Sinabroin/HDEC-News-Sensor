#!/usr/bin/env python3
"""D7-AF TheBell preview-only 수집 정책 검증."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import thebell_watch  # noqa: E402

FAILURES: list[str] = []
FORBIDDEN_KEYS = {
    "".join(("full", "_text")),
    "".join(("article", "_body")),
    "".join(("raw", "_payload")),
    "".join(("full_rss", "_content")),
}


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}"
          + (f" — {detail}" if detail else ""))
    if not condition:
        FAILURES.append(label)


def main() -> int:
    source = (ROOT / "app" / "thebell_watch.py").read_text(encoding="utf-8")
    check("기사 페이지 network client 없음",
          "urllib.request" not in source and "requests" not in source)

    row = {
        "title": "진원이앤씨, 도곡 개발 본PF",
        "url": "https://www.thebell.co.kr/free/content/ArticleView.asp?key=1",
        "published_at": "2026-07-03T09:00:00+09:00",
        "snippet": "검색 결과가 제공한 짧은 preview",
        "source_metadata": {"provider": "naver_news_api"},
    }
    item = thebell_watch.normalize_candidate(row)
    check("TheBell URL 후보 normalize", item is not None)
    check("제한 상태 명시",
          bool(item and item["access_limited"] and item["subscription_required"]
               and item["access_type"] == "subscription_required"))
    check("collection method 명시",
          bool(item and item["collection_method"] == "naver_search_api"))
    check("저작권 note 명시 (기사 본문 미저장 표식)",
          bool(item and item["copyright_note"] == thebell_watch.COPYRIGHT_NOTE
               and item["copyright_note"] == "no_body_stored"))
    check("금지 저장 key 없음",
          bool(item and not (set(item) & FORBIDDEN_KEYS)))
    allowed = {
        "title", "url", "published_at", "reporter", "category", "snippet",
        "source", "source_domain", "access_type", "access_limited",
        "subscription_required", "collection_method", "copyright_note",
    }
    check("허용 metadata field만 반환",
          bool(item and set(item) == allowed), str(sorted(set(item or {}) - allowed)))
    check("비-TheBell URL 제외",
          thebell_watch.normalize_candidate({**row, "url": "https://reuters.com/x"}) is None)

    if FAILURES:
        print(f"FAIL: {len(FAILURES)} checks")
        return 1
    print("PASS: TheBell preview-only policy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
