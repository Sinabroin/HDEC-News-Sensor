#!/usr/bin/env python3
"""Bounded GET-only external Tier-A13 RSS sweep for live comparison."""

from __future__ import annotations

import argparse
import email.utils
import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
USER_AGENT = "HDEC-News-Sensor-R4-OPS-6C-readonly-audit/1.0"
TIER_A = (
    ("연합뉴스", "yna.co.kr"), ("MBC", "imnews.imbc.com"),
    ("KBS", "news.kbs.co.kr"), ("조선일보", "chosun.com"),
    ("YTN", "ytn.co.kr"), ("JTBC", "news.jtbc.co.kr"),
    ("중앙일보", "joongang.co.kr"), ("매일경제", "mk.co.kr"),
    ("한국경제", "hankyung.com"), ("SBS", "news.sbs.co.kr"),
    ("동아일보", "donga.com"), ("한겨레", "hani.co.kr"),
    ("경향신문", "khan.co.kr"),
)


def _tmp_path(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if Path("/tmp") not in path.parents:
        raise argparse.ArgumentTypeError("external sweep output must be under /tmp")
    return path


def _published(value: str) -> datetime | None:
    try:
        result = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=_tmp_path, required=True)
    parser.add_argument("--hours", type=int, default=24)
    args = parser.parse_args()
    hours = max(1, min(int(args.hours), 48))
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hours)
    rows: list[dict] = []
    publisher_counts: dict[str, int] = {}
    errors: dict[str, str] = {}

    for publisher, domain in TIER_A:
        query = f'(AI OR 인공지능 OR 데이터센터) site:{domain} when:1d'
        url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({
            "q": query, "hl": "ko", "gl": "KR", "ceid": "KR:ko",
        })
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=10) as response:
                root = ET.fromstring(response.read(1_500_000))
        except Exception as exc:  # noqa: BLE001 - audit records bounded category
            errors[publisher] = type(exc).__name__
            continue
        count = 0
        for item in root.findall("./channel/item")[:20]:
            pub = _published(item.findtext("pubDate") or "")
            if pub is None or pub < cutoff:
                continue
            title = re.sub(r"\s+-\s+[^-]+$", "", item.findtext("title") or "").strip()
            rows.append({
                "publisher": publisher,
                "domain": domain,
                "title": title,
                "google_news_url": (item.findtext("link") or "").strip(),
                "published_at": pub.astimezone(KST).isoformat(timespec="seconds"),
            })
            count += 1
        publisher_counts[publisher] = count

    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(KST).isoformat(timespec="seconds"),
        "window_hours": hours,
        "tier_a_publishers_queried": len(TIER_A),
        "publisher_counts": publisher_counts,
        "error_categories": errors,
        "articles": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"EXTERNAL_TIER_A_ARTICLES={len(rows)}")
    print(f"EXTERNAL_TIER_A_PUBLISHERS_WITH_SUPPLY={sum(v > 0 for v in publisher_counts.values())}")
    print(f"EXTERNAL_TIER_A_QUERY_ERRORS={len(errors)}")
    print(f"EXTERNAL_TIER_A_SWEEP={args.output}")
    print("NETWORK_METHOD=GET_ONLY")
    print("PRODUCTION_STATE_WRITES=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
