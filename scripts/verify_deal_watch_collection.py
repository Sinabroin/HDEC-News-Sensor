#!/usr/bin/env python3
"""D7-AF Deal Watch 분류·collector·dashboard merge 경로 검증."""

from __future__ import annotations

import os
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import collector, deal_watch  # noqa: E402

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}"
          + (f" — {detail}" if detail else ""))
    if not condition:
        FAILURES.append(label)


def _raw(provider: str, source: str, url: str) -> dict:
    return {
        "id": provider + source,
        "title": "동일 사건 제목",
        "source": source,
        "published_at": "2026-07-03T09:00:00+09:00",
        "url": url,
        "snippet": "프로젝트 투자",
        "source_metadata": {
            "provider": provider, "query": "q", "source_url": url,
            "collected_at": "t", "provider_response_id": "r",
        },
    }


def main() -> int:
    cases = {
        "project": "진원이앤씨 오피스 개발 본PF 조달",
        "construction_industry": "코매퍼 AI 기반 시설 안전진단 솔루션",
        "hmg": "현대오토에버 AX 조직개편 RX사업실 신설",
        "major_groups": "LG전자 로보틱스사업센터 출범",
        "global_issues": "미국 AI칩 수출통제와 글로벌 공급망",
        "ai_infra": "AI 데이터센터 GPU 전력망 냉각 투자",
        "capital_markets": "SK KKR 지분 투자와 자금조달",
    }
    for expected, title in cases.items():
        labels = deal_watch.classify_labels(title)
        check(f"label 분류: {expected}", expected in labels, str(labels))

    merged = collector.merge_provider_articles([
        _raw("google_news_rss", "Reuters", "https://reuters.com/world/a"),
        _raw("naver_news_api", "더벨", "https://www.thebell.co.kr/news/a"),
    ])
    check("서로 다른 Reuters/TheBell 원문 후보 보존", len(merged) == 2, str(len(merged)))
    normalized = [
        collector._to_article_row(row, [], "2026-07-03T10:00:00+09:00", "Live RSS")
        for row in merged
    ]
    check("DB 직전 batch dedup도 cross-source 후보 보존",
          len(collector._dedup(normalized)) == 2)

    collector_source = (ROOT / "app" / "collector.py").read_text(encoding="utf-8")
    brief_source = (ROOT / "app" / "briefing.py").read_text(encoding="utf-8")
    builder_source = (ROOT / "scripts" / "build_static_dashboard.py").read_text(
        encoding="utf-8")
    template_source = (ROOT / "templates" / "dashboard_preview.html").read_text(
        encoding="utf-8")
    check("collector가 TheBell adapter 호출",
          "thebell_watch.extract_candidates" in collector_source)
    check("briefing에 Deal Watch rows 생성",
          "deal_watch.build_dashboard_rows" in brief_source)
    check("dashboard model merge path",
          'model["deal_watch_rows"]' in builder_source)
    check("compact UI와 원문 보기 존재",
          'id="dealWatchRows"' in template_source and "원문 보기" in template_source)

    with tempfile.TemporaryDirectory(prefix="d7af_dashboard_") as tmp:
        output = Path(tmp) / "dashboard.html"
        env = dict(os.environ, NEWS_MODE="mock", MACRO_MODE="mock")
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "build_static_dashboard.py"),
             "--output", str(output)],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=120,
        )
        check("mock-safe dashboard build", proc.returncode == 0,
              (proc.stderr or proc.stdout)[-400:])
        html = output.read_text(encoding="utf-8") if output.exists() else ""
        match = re.search(
            r'<script type="application/json" id="preview-model">(.*?)</script>',
            html, re.S)
        model = json.loads(match.group(1)) if match else {}
        rows = model.get("deal_watch_rows")
        check("generated model에 deal_watch_rows 존재",
              isinstance(rows, list) and bool(rows), str(len(rows or [])))
        check("Deal Watch row에 label·시사점·원문 링크",
              bool(rows) and all(
                  row.get("deal_watch_label")
                  and row.get("implication")
                  and str(row.get("url") or "").startswith(("http://", "https://"))
                  for row in rows
              ))
        check("제한 상태 field 존재",
              bool(rows) and all(
                  "access_limited" in row and "subscription_required" in row
                  for row in rows
              ))
        check("public HTML에서 Naver API 직접 호출 없음",
              "openapi.naver.com/v1/search/news.json" not in html)

    if FAILURES:
        print(f"FAIL: {len(FAILURES)} checks")
        return 1
    print("PASS: Deal Watch collection path")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
