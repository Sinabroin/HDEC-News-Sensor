#!/usr/bin/env python3
"""Offline verifier for D7-AD-N → D7-AD-W Phase 1B — 브리핑 분류 데이터 + layered flat list.

검증 대상:
- brief.accordion_sections(8키) 데이터 파이프라인 보존 — 표시 전용 파생·가짜 기사 없음
- visible accordion(details.acc-sec) 제거 → preview-model.nav_category_sections + #categoryNewsList
- 2차 pill(#cnlPills) count badge(cnl-badge) · rowsForPrimaryLens/countForSecondary 교차 필터
- weather는 nav_category_sections에서 제외 · 기상=siteWeatherCard
- 해외 언론 source group(영어 locale) 존재
- 공개 산출물 secret/token 없음

네트워크/비밀값 0건 (mock 빌드만 사용)."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT, ROOT / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

TEMPLATE = ROOT / "templates" / "dashboard_preview.html"
NEWS_PILL_KEYS = ("all", "new", "order", "finance", "policy", "competitor", "brand", "global_press")

_failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        _failures.append(name)


REQUIRED_SECTIONS = [
    ("new", ("오늘의 신규 이슈",)),
    ("order", ("수주",)),
    ("finance", ("재무",)),
    ("policy", ("정책",)),
    ("competitor", ("경쟁",)),
    ("brand", ("브랜드",)),
    ("weather", ("기상", "날씨")),
    ("global_press", ("해외 언론",)),
]


def _clean_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if k not in ("NEWS_MODE", "MACRO_MODE")}
    env["APP_MODE"] = "mock"
    env["PYTHONHASHSEED"] = "0"
    return env


def _build_dashboard_html() -> str:
    import tempfile

    handle, path = tempfile.mkstemp(suffix=".html")
    os.close(handle)
    try:
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "build_static_dashboard.py"),
             "--output", path],
            cwd=ROOT, env=_clean_env(), text=True, capture_output=True, timeout=300)
        return Path(path).read_text(encoding="utf-8") if proc.returncode == 0 else ""
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _model(html: str) -> dict:
    m = re.search(r'<script type="application/json" id="preview-model">\s*(.*?)\s*</script>',
                  html, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return {}


def check_config() -> None:
    from app import global_press as gp

    data_path = ROOT / "data" / "global_press_sources.json"
    check("해외 언론 정책 파일 존재 (data/global_press_sources.json)", data_path.exists())
    group = gp.collection_query_group()
    check("해외 언론 수집 그룹(collection_query_group) 존재",
          group is not None and group.get("name") == "global_press")
    locale = (group or {}).get("locale") or {}
    check("해외 언론 그룹은 영어 locale(en-US) — 국내(한국)와 분리",
          locale.get("hl") == "en" and locale.get("gl") == "US")
    check("해외 언론 그룹에 수집 쿼리 존재(≥3)",
          bool(group) and len(group.get("queries") or []) >= 3)
    check("해외 매체 판별: 등록 해외 매체(출처) → True",
          gp.is_foreign_press("Reuters") and gp.is_foreign_press("Bloomberg"))
    check("해외 매체 판별: 영문 제목이면 미등록 매체도 True(robust 언어 신호)",
          gp.is_foreign_press("Goldman Sachs", "US Data Center Power Demand to Double")
          and gp.is_foreign_press("Data Center Knowledge", "The AI Demand Dilemma"))
    check("해외 매체 판별: 국내 출처+한글 제목 → False(국내 비오염)",
          not gp.is_foreign_press("한국경제", "현대건설 수주 확대")
          and not gp.is_foreign_press("연합뉴스", "AI 데이터센터 착공")
          and not gp.is_foreign_press("조선비즈", ""))
    check("해외 언론 source label '해외 언론'", gp.source_label() == "해외 언론")

    news = json.loads((ROOT / "data" / "live_news_sources.json").read_text(encoding="utf-8"))
    check("국내 기본 뉴스 소스는 한국 locale 유지(국내↔해외 분리)",
          news.get("gl") == "KR" and news.get("hl") == "ko")

    from app import live_collector as lc
    en_url = lc._build_google_news_url("AI data center", {}, locale)
    check("수집기 URL 빌더가 그룹 locale을 적용(en-US)",
          "hl=en" in en_url and "gl=US" in en_url)
    ko_url = lc._build_google_news_url("현대건설", {})
    check("locale 미지정 시 기본 한국 locale(국내 분리)",
          "hl=ko" in ko_url and "gl=KR" in ko_url)
    check("해외 언론 preflight가 collector에 연결됨(예산 상수 + 모듈)",
          hasattr(lc, "GLOBAL_PRESS_GROUP_BUDGET") and hasattr(lc, "global_press"))


def check_brief() -> dict:
    from build_executive_brief import _bootstrap, build_brief_via_mock_pipeline

    brief = build_brief_via_mock_pipeline()
    db = _bootstrap()["db"]
    scored = db.fetch_articles_with_scores()
    scored_ids = {r["id"] for r in scored if r.get("final_score") is not None}

    secs = brief.get("accordion_sections") or []
    check("brief.accordion_sections 존재", bool(secs))
    by_key = {s.get("key"): s for s in secs}
    for key, _labels in REQUIRED_SECTIONS:
        check(f"섹션 키 존재: {key}", key in by_key)

    opened = [s.get("key") for s in secs if s.get("default_open")]
    check("기본 열림은 '오늘의 신규 이슈'(new)만", opened == ["new"], str(opened))

    weather = by_key.get("weather", {})
    check("기상·날씨 섹션은 항상 빈 상태(가짜 날씨 데이터 없음)",
          weather.get("empty") is True and weather.get("article_count") == 0)

    gp_sec = by_key.get("global_press", {})
    check("해외 언론 섹션은 mock에서 빈 상태(가짜 해외 기사로 채우지 않음)",
          gp_sec.get("empty") is True and gp_sec.get("article_count") == 0)

    for key in ("new", "order", "finance", "policy", "competitor", "brand"):
        arts = by_key.get(key, {}).get("articles") or []
        ids = [a.get("article_id") for a in arts]
        check(f"'{key}' 섹션 기사는 실제 채점 기사(표시 전용 파생)",
              all(i in scored_ids for i in ids), f"{len(ids)} ids")
        check(f"'{key}' 섹션 기사 카드에 제목·출처 필드",
              all(a.get("title") and (a.get("display_source") or a.get("source"))
                  for a in arts))

    for key in ("brand", "weather", "global_press"):
        s = by_key.get(key, {})
        check(f"빈 '{key}' 섹션도 라벨 유지 + 정직한 빈 상태('없음')",
              bool(s.get("label")) and "없음" in (s.get("empty_message") or ""))

    order = by_key.get("order", {})
    check("섹션 헤더 카운트 필드(issue_count/article_count) 존재",
          "issue_count" in order and "article_count" in order)
    return brief


def check_finance_brand_display_only() -> None:
    import app.briefing as briefing

    fin_row = {"title": "현대건설 전환사채 4000억 발행 자금조달", "snippet": ""}
    check("재무 멤버십: finance 토큰 → True",
          briefing._accordion_is_finance(fin_row, {}))
    check("재무 멤버십: decision is_finance 플래그 → True",
          briefing._accordion_is_finance({"title": "x", "snippet": ""}, {"is_finance": True}))
    check("재무 멤버십: stock_hype는 제외(시세성 배제)",
          not briefing._accordion_is_finance(fin_row, {"stock_hype": True}))
    check("재무 멤버십: 무관 기사 → False",
          not briefing._accordion_is_finance({"title": "AI 데이터센터 착공", "snippet": ""}, {}))

    check("브랜드 멤버십: 브랜드명/키워드 → True",
          briefing._accordion_is_brand({"title": "힐스테이트 디에이치 상품성 강화"}))
    check("브랜드 멤버십: 무관 기사 → False(좁은 필터)",
          not briefing._accordion_is_brand({"title": "중대재해 처벌 강화 추진"}))


def check_template_layered() -> None:
    t = TEMPLATE.read_text(encoding="utf-8")
    check("템플릿: visible accordion(#accordionSections) 없음",
          'id="accordionSections"' not in t)
    check("템플릿: details.acc-sec 없음", "details.acc-sec" not in t)
    check("템플릿: #categoryNewsList + #cnlPills 존재",
          'id="categoryNewsList"' in t and 'id="cnlPills"' in t)
    check("템플릿: openNavigationNewsResult + renderLayeredNewsList",
          "function openNavigationNewsResult" in t and "function renderLayeredNewsList" in t)
    check("템플릿: pill count badge(cnl-badge) + countForSecondary",
          "cnl-badge" in t and "function countForSecondary" in t)
    check("템플릿: navcat 제거", 'class="nav navcat"' not in t)
    check("템플릿: '카테고리별 브리핑' 없음", "카테고리별 브리핑" not in t)


def check_dashboard_html() -> None:
    html = _build_dashboard_html()
    check("mock 요약 대시보드 빌드 성공", bool(html))
    if not html:
        return

    check("산출물: visible accordion(details.acc-sec) 없음",
          '<details class="acc-sec"' not in html)
    check("산출물: #accordionSections 없음", 'id="accordionSections"' not in html)
    check("산출물: '카테고리별 브리핑' 없음", "카테고리별 브리핑" not in html)
    check("산출물: navcat 없음", 'class="nav navcat"' not in html)
    check("산출물: #categoryNewsList + #cnlPills 존재",
          'id="categoryNewsList"' in html and 'id="cnlPills"' in html)
    check("산출물: layered JS(openNavigationNewsResult/renderLayeredNewsList) 존재",
          "function openNavigationNewsResult" in html
          and "function renderLayeredNewsList" in html)
    check("산출물: pill count badge(cnl-badge) 렌더 로직 존재",
          "cnl-badge" in html and "countForSecondary" in html)
    check("산출물: 빈 상태 문구 '현재 수집된 항목 없음'",
          "현재 수집된 항목 없음" in html)

    model = _model(html)
    nav_secs = {s.get("key") for s in (model.get("nav_category_sections") or [])}
    check("모델: nav_category_sections 존재", bool(nav_secs), str(sorted(nav_secs)))
    for key in NEWS_PILL_KEYS:
        if key == "all":
            continue
        check(f"모델: nav_category_sections '{key}'", key in nav_secs)
    check("모델: weather는 nav_category_sections에서 제외", "weather" not in nav_secs)
    check("기상: siteWeatherCard 존재", 'id="siteWeatherCard"' in html)

    leaks = [n for n in ("TELEGRAM_BOT_TOKEN", "GMAIL_SMTP_APP_PASSWORD",
                         "GMAIL_SMTP_USER", "OPERATOR_SHARED_SECRET", "ALERT_EMAIL_TO")
             if n in html]
    check("대시보드 공개 산출물에 secret 이름 없음", not leaks, ", ".join(leaks))


def main() -> int:
    print(f"== verify_dashboard_accordion_sections (D7-AD-W Phase 1B) @ {ROOT} ==")
    check_brief()
    check_config()
    check_finance_brand_display_only()
    check_template_layered()
    check_dashboard_html()
    print()
    if _failures:
        print(f"RESULT: FAIL ({len(_failures)} 항목)")
        return 1
    print("RESULT: PASS — brief 분류 데이터 + layered flat list + pill count badge (Phase 1B)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
