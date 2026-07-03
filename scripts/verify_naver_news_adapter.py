#!/usr/bin/env python3
"""D7-AF Naver News Search API 연결 계약 검증."""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import config, naver_news_provider as naver, news_coverage  # noqa: E402

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    mark = "PASS" if condition else "FAIL"
    print(f"[{mark}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        FAILURES.append(label)


def main() -> int:
    source = (ROOT / "app" / "naver_news_provider.py").read_text(encoding="utf-8")
    config_source = (ROOT / "app" / "config.py").read_text(encoding="utf-8")
    workflows = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / ".github" / "workflows").glob("*.yml")
    )
    check("공식 search/news endpoint 고정", naver.ENDPOINT == (
        "https://openapi.naver.com/v1/search/news.json"))
    check("adapter는 config에서만 credential 참조",
          "os.environ" not in source
          and "NAVER_CLIENT_ID" in config_source
          and "NAVER_CLIENT_SECRET" in config_source)
    leak_lines = [
        line for line in source.splitlines()
        if ("print(" in line.lower() or "logging" in line.lower())
        and ("client_id" in line.lower() or "client_secret" in line.lower())
    ]
    check("credential 값을 print/log하지 않음", not leak_lines)
    check("scheduled workflow env 연결",
          all(token in workflows for token in (
              'NAVER_NEWS_ENABLED: "1"',
              "NAVER_CLIENT_ID: ${{ secrets.NAVER_CLIENT_ID }}",
              "NAVER_CLIENT_SECRET: ${{ secrets.NAVER_CLIENT_SECRET }}",
          )))
    check("coverage query group 연결", len(news_coverage.collection_query_groups()) == 5
          and len(news_coverage.all_queries()) >= 25)

    saved = (config.NAVER_NEWS_ENABLED, config.NAVER_CLIENT_ID, config.NAVER_CLIENT_SECRET)
    original_request = naver._request_json
    try:
        calls = []
        config.NAVER_NEWS_ENABLED = True
        config.NAVER_CLIENT_ID = ""
        config.NAVER_CLIENT_SECRET = ""
        result = naver.fetch()
        check("env 없음은 unavailable 상태", result["status"] == (
            naver.STATUS_SKIPPED_MISSING_CREDENTIALS))
        check("env 없음은 fake article 0건", result["articles"] == [])

        config.NAVER_CLIENT_ID = "fixture-id"
        config.NAVER_CLIENT_SECRET = "fixture-secret"

        def fake_request(url, headers, timeout):
            calls.append((url, set(headers)))
            return {"items": [{
                "title": "<b>AI 데이터센터</b> 전력기기 공급",
                "originallink": "https://example.test/news/1",
                "link": "https://n.news.naver.com/1",
                "description": "전력망 투자 metadata",
                "pubDate": "Fri, 03 Jul 2026 09:00:00 +0900",
            }]}

        naver._request_json = fake_request
        with tempfile.TemporaryDirectory(prefix="d7af_naver_") as tmp:
            path = Path(tmp) / "sources.json"
            path.write_text(json.dumps({
                "queries": ["AI 데이터센터 전력"],
                "display": 1,
                "max_per_query": 1,
                "max_total": 1,
                "host_source_map": {},
            }), encoding="utf-8")
            result = naver.fetch(sources_path=path, include_coverage=False)
        check("stubbed API success는 active", result["status"] == naver.STATUS_ACTIVE)
        check("공식 endpoint 구조만 호출",
              len(calls) == 1 and calls[0][0].startswith(naver.ENDPOINT + "?"))
        check("인증 header 이름 존재",
              calls and {"X-Naver-Client-Id", "X-Naver-Client-Secret"} <= calls[0][1])
        check("normalize metadata만 반환",
              len(result["articles"]) == 1
              and set(result["articles"][0]) <= {
                  "id", "title", "source", "published_at", "url", "snippet",
                  "source_metadata",
              })
    finally:
        naver._request_json = original_request
        (config.NAVER_NEWS_ENABLED, config.NAVER_CLIENT_ID,
         config.NAVER_CLIENT_SECRET) = saved

    html = (ROOT / "docs" / "daily" / "dashboard-latest.html").read_text(
        encoding="utf-8", errors="replace")
    check("GitHub Pages HTML에서 Naver API 직접 호출 없음",
          naver.ENDPOINT not in html and "X-Naver-Client-Secret" not in html)

    # 실제 env가 프로세스에 연결된 경우에만 한 query live smoke. 값은 출력하지 않는다.
    live_ready = bool(
        os.environ.get("NAVER_NEWS_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}
        and os.environ.get("NAVER_CLIENT_ID")
        and os.environ.get("NAVER_CLIENT_SECRET")
    )
    if live_ready:
        with tempfile.TemporaryDirectory(prefix="d7af_naver_live_") as tmp:
            path = Path(tmp) / "sources.json"
            path.write_text(json.dumps({
                "queries": ["AI 데이터센터 전력"],
                "display": 3,
                "max_per_query": 3,
                "max_total": 3,
                "host_source_map": {},
            }), encoding="utf-8")
            live = naver.fetch(timeout=8, sources_path=path, include_coverage=False)
        check("live env one-query smoke", live["status"] in {
            naver.STATUS_ACTIVE, naver.STATUS_ERROR})
        print(f"[INFO] live_status={live['status']} rows={len(live.get('articles') or [])}")
    else:
        print("[SKIP] live env 미주입 — 실제 API 호출 없음")

    if FAILURES:
        print(f"FAIL: {len(FAILURES)} checks")
        return 1
    print("PASS: Naver adapter contract")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
