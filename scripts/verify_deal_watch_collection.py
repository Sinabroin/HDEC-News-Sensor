#!/usr/bin/env python3
"""D7-AG-1 Deal Watch — 분류·카드 태그 흡수·독립 섹션 제거 검증.

D7-AF의 Deal Watch 독립 섹션을 없애고, 딜/투자/조달/프로젝트성 신호를 각 뉴스 카드의
'근거 있는' 보조 태그(row["deal_tags"], 최대 3개)로 흡수한 계약을 검증한다.
공개 HTML에 Deal Watch heading/섹션/renderDealWatch/빈 상태 문구/반복 시사점 문구가 남지
않아야 하고, 카드에는 근거가 있을 때만 작은 태그가 붙어야 한다(태그 없는 기사는 태그 없음).
"""

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
PUBLIC_DASHBOARD = ROOT / "docs" / "daily" / "dashboard-latest.html"

# 공개 UI에 노출을 허용하는 보조 태그 어휘(사용자 승인 매핑). 이 밖의 태그가 나오면 실패.
ALLOWED_TAGS = {
    "PF·조달", "개발사업", "투자·딜", "HMG",
    "AI 인프라", "그룹 전략", "건설테크", "글로벌 리스크",
}


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


def _display_rows(model: dict) -> list[dict]:
    """카드로 표시되는 모든 행(featured/news/ai/lens/nav_category)을 모은다."""
    rows: list[dict] = []
    if model.get("featured_row"):
        rows.append(model["featured_row"])
    rows += list(model.get("news_rows") or [])
    rows += list(model.get("ai_rows") or [])
    for bank in (model.get("lens_banks") or {}).values():
        rows += list(bank or [])
    for section in model.get("nav_category_sections") or []:
        rows += list(section.get("articles") or [])
    return rows


def main() -> int:
    # 1) label 분류 (D7-AF classify 계약 유지 — 태그 매핑의 근거원).
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

    # 2) display_tags — 근거 기반 카드 보조 태그(매핑·키워드·우선순위·cap).
    def tags(title: str) -> list[str]:
        return deal_watch.display_tags(title)

    check("major_groups→그룹 전략",
          "그룹 전략" in tags("LG전자 로보틱스사업센터 출범"))
    project_tags = tags("진원이앤씨 오피스 개발 본PF 조달")
    check("project→개발사업(+PF·조달 근거 시)",
          "개발사업" in project_tags and "PF·조달" in project_tags, str(project_tags))
    cm_tags = tags("SK KKR 지분 투자와 자금조달")
    check("capital_markets→PF·조달(+투자·딜 근거 시)",
          "PF·조달" in cm_tags and "투자·딜" in cm_tags, str(cm_tags))
    check("construction_industry→건설테크",
          "건설테크" in tags("코매퍼 AI 기반 시설 안전진단 솔루션"))
    check("global_issues→글로벌 리스크",
          "글로벌 리스크" in tags("미국 수출통제와 글로벌 공급망 지정학 리스크"))
    check("hmg→HMG", "HMG" in tags("현대오토에버 AX 조직개편 RX사업실 신설"))
    check("ai_infra→AI 인프라", "AI 인프라" in tags("AI 데이터센터 GPU 전력망 냉각 투자"))
    check("근거 없으면 태그 없음(자연스럽게 무태그)",
          tags("오늘 서울 정오 날씨 대체로 맑음") == [])
    many = tags("현대차그룹 AI 데이터센터 본PF 지분 투자 개발사업 M&A 자금조달")
    check("기사당 태그 ≤ 3", len(many) <= 3, str(many))
    check("허용 태그 어휘만 사용", set(many) <= ALLOWED_TAGS, str(many))
    check("우선순위(PF·조달>개발사업>투자·딜…) 정렬",
          many == sorted(many, key=lambda t: deal_watch._TAG_RANK.get(t, 99)), str(many))

    # 3) collector cross-source 후보 보존 (D7-AF 계약 유지).
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

    # 4) 소스 계약: builder가 카드에 태그 부착, 템플릿에서 독립 섹션 제거.
    collector_source = (ROOT / "app" / "collector.py").read_text(encoding="utf-8")
    brief_source = (ROOT / "app" / "briefing.py").read_text(encoding="utf-8")
    builder_source = (ROOT / "scripts" / "build_static_dashboard.py").read_text(
        encoding="utf-8")
    template_source = (ROOT / "templates" / "dashboard_preview.html").read_text(
        encoding="utf-8")
    check("collector가 TheBell adapter 호출",
          "thebell_watch.extract_candidates" in collector_source)
    check("briefing에 Deal Watch rows 생성(데이터 구조 유지)",
          "deal_watch.build_dashboard_rows" in brief_source)
    check("builder가 카드에 deal_tags 부착",
          "deal_watch.display_tags" in builder_source)
    check("builder가 deal_watch_rows를 공개 MODEL에 싣지 않음",
          'model["deal_watch_rows"]' not in builder_source)
    check("템플릿에 dealwatch 독립 섹션 없음",
          'class="dealwatch"' not in template_source
          and 'id="dealWatchRows"' not in template_source
          and 'id="dealWatchTitle"' not in template_source)
    check("템플릿에 renderDealWatch 없음", "renderDealWatch" not in template_source)
    check("템플릿에 Deal Watch 빈 상태 문구 없음",
          "현재 수집된 Deal Watch 후보가 없습니다" not in template_source)
    check("템플릿에 카드 보조 태그 렌더 존재",
          "dealTagsHtml" in template_source and "deal-tag" in template_source)

    # 5) mock-safe 빌드 산출물 계약: 섹션·문구 제거 + 카드 태그 흡수 (네트워크 0건).
    with tempfile.TemporaryDirectory(prefix="d7ag1_dashboard_") as tmp:
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

        check("공개 HTML에 Deal Watch 섹션/heading 없음",
              'class="dealwatch"' not in html
              and 'id="dealWatchRows"' not in html
              and ">Deal Watch<" not in html)
        check("공개 HTML에 renderDealWatch 없음", "renderDealWatch" not in html)
        check("공개 HTML에 Deal Watch 빈 상태 문구 없음",
              "현재 수집된 Deal Watch 후보가 없습니다" not in html)
        # 내부 deal_watch_rows가 generic full-pool 재귀에 섞이면 이 문구가 일반 뉴스 카드의
        # radarReason/whyImportant로 반복된다. 공개 카드에는 작은 deal_tags만 허용한다.
        dealwatch_only = list(deal_watch._IMPLICATION.values())
        check("공개 HTML에 Deal Watch 반복 시사점 문구 없음",
              all(phrase not in html for phrase in dealwatch_only),
              str([p for p in dealwatch_only if p in html]))
        check("공개 MODEL에 deal_watch_rows 미포함", "deal_watch_rows" not in model)

        rows = _display_rows(model)
        tagged = [r for r in rows if r.get("deal_tags")]
        check("뉴스 카드에 보조 태그 흡수됨(≥1건)",
              len(tagged) >= 1, f"{len(tagged)}/{len(rows)} tagged")
        check("모든 카드 태그 ≤3 · 허용 어휘만",
              all(len(r.get("deal_tags") or []) <= deal_watch.MAX_DISPLAY_TAGS
                  and set(r.get("deal_tags") or []) <= ALLOWED_TAGS for r in rows),
              str(sorted({t for r in rows for t in (r.get("deal_tags") or [])})))
        check("public HTML에서 Naver API 직접 호출 없음",
              "openapi.naver.com/v1/search/news.json" not in html)

    # 6) 실제 게시 대상도 같은 계약이어야 한다. live snapshot을 mock으로 덮어쓰지 않고 읽기만 한다.
    if PUBLIC_DASHBOARD.exists():
        committed = PUBLIC_DASHBOARD.read_text(encoding="utf-8")
        match = re.search(
            r'<script type="application/json" id="preview-model">(.*?)</script>',
            committed, re.S)
        committed_model = json.loads(match.group(1)) if match else {}
        check("committed public에 Deal Watch visible DOM/renderer/빈 문구 없음",
              'class="dealwatch"' not in committed
              and 'id="dealWatchRows"' not in committed
              and "renderDealWatch" not in committed
              and "현재 수집된 Deal Watch 후보가 없습니다" not in committed)
        check("committed public에 반복 generic implication 없음",
              all(phrase not in committed for phrase in deal_watch._IMPLICATION.values()))
        check("committed public MODEL에 deal_watch_rows 미포함",
              "deal_watch_rows" not in committed_model)
        committed_rows = _display_rows(committed_model)
        committed_tags = {t for row in committed_rows for t in (row.get("deal_tags") or [])}
        check("committed public 카드에 승인 보조 태그 존재",
              bool(committed_tags) and committed_tags <= ALLOWED_TAGS,
              str(sorted(committed_tags)))

    if FAILURES:
        print(f"FAIL: {len(FAILURES)} checks")
        return 1
    print("PASS: Deal Watch 흡수 · 독립 섹션 제거")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
