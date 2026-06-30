#!/usr/bin/env python3
"""Offline verifier for D7-AD-N — 요약 대시보드 임원 아코디언 8섹션 + 해외 언론 source group.

검증 대상:
- 8개 섹션(오늘의 신규 이슈 / 수주 / 재무 / 정책 / 경쟁 / 브랜드 / 기상·날씨 / 해외 언론)이
  독립적인 expandable <details> 컨테이너로 존재하고, summary와 기사 목록이 올바르게 연결된다.
- 오늘의 신규 이슈만 기본 열림(open), 나머지는 접힘. 펼쳐져도 다른 목록을 가리지 않는 블록 흐름.
- 데이터가 없으면(브랜드/기상/해외) 헤딩을 유지하고 '현재 수집된 항목 없음'처럼 정직히 표기.
- 재무/브랜드는 표시 전용 파생 섹션 — 점수/등급/분류를 바꾸지 않고 raw 제목+repo 플래그로만 멤버 선정.
- 해외 언론 source group(영어 locale)이 존재하고, mock에선 해외 출처가 없어 가짜 기사로 채우지 않는다.
- 공개 산출물에 secret/token 없음.

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

_failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        _failures.append(name)


# (key, 헤딩에 반드시 나타나야 하는 텍스트) — 기상은 '기상' 또는 '날씨' 중 하나면 통과.
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

    # 수집기에 그룹별 locale + 해외 preflight가 실제로 연결돼 있다(구조 잠금).
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

    # 표시 전용·발명 없음: 모든 섹션 기사는 실제 채점 기사여야 한다(새 기사 생성 금지).
    for key in ("new", "order", "finance", "policy", "competitor", "brand"):
        arts = by_key.get(key, {}).get("articles") or []
        ids = [a.get("article_id") for a in arts]
        check(f"'{key}' 섹션 기사는 실제 채점 기사(표시 전용 파생)",
              all(i in scored_ids for i in ids), f"{len(ids)} ids")
        # 카드 필드: 제목·출처·본문 링크 가용성
        check(f"'{key}' 섹션 기사 카드에 제목·출처 필드",
              all(a.get("title") and (a.get("display_source") or a.get("source"))
                  for a in arts))

    # 빈 섹션도 라벨 + 정직한 빈 상태 메시지를 유지한다.
    for key in ("brand", "weather", "global_press"):
        s = by_key.get(key, {})
        check(f"빈 '{key}' 섹션도 라벨 유지 + 정직한 빈 상태('없음')",
              bool(s.get("label")) and "없음" in (s.get("empty_message") or ""))

    # 헤더 카운트(이슈/기사) 구조가 존재한다.
    order = by_key.get("order", {})
    check("섹션 헤더 카운트 필드(issue_count/article_count) 존재",
          "issue_count" in order and "article_count" in order)
    return brief


def check_finance_brand_display_only() -> None:
    """재무/브랜드가 표시 전용 파생(점수/등급/분류 불변)임을 멤버십 함수로 잠근다."""
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


def _accordion_css(html: str) -> str:
    m = re.search(r"임원 브리핑 아코디언.*?</style>", html, re.S)
    return m.group(0) if m else ""


def check_dashboard_html() -> None:
    html = _build_dashboard_html()
    check("mock 요약 대시보드 빌드 성공", bool(html))
    if not html:
        return

    details = re.findall(r'<details class="acc-sec" data-acc="([a-z_]+)"( open)?>', html)
    keys = [d[0] for d in details]
    check("아코디언 <details class=acc-sec> 8개(독립 expandable 컨테이너)",
          len(details) == 8, str(len(details)))
    for key, _labels in REQUIRED_SECTIONS:
        check(f"섹션 <details> 존재: {key}", key in keys)

    opened = [d[0] for d in details if d[1]]
    check("기본 열림 <details open>은 'new' 하나만", opened == ["new"], str(opened))

    names = re.findall(r'<span class="acc-name">([^<]+)</span>', html)
    for _key, labels in REQUIRED_SECTIONS:
        check(f"섹션 헤딩 텍스트: {labels[0]}",
              any(any(lbl in n for lbl in labels) for n in names))

    blocks = re.findall(r'<details class="acc-sec".*?</details>', html, re.S)
    check("각 <details>는 <summary> 정확히 1개(summary↔목록 연결)",
          len(blocks) == 8 and all(b.count("<summary>") == 1 for b in blocks))
    check("각 <details>는 기사 목록(acc-list) 또는 빈 상태(acc-empty)를 포함",
          all(("acc-list" in b) or ("acc-empty" in b) for b in blocks))
    # summary와 목록/빈상태가 같은 details 안에 있다(연결) — summary가 먼저, 내용이 뒤.
    conn_ok = True
    for b in blocks:
        s_idx = b.find("</summary>")
        body_idx = max(b.find('<div class="acc-list"'), b.find('<div class="acc-empty"'))
        if s_idx < 0 or body_idx < 0 or body_idx < s_idx:
            conn_ok = False
    check("summary text가 그 섹션 기사 목록과 같은 컨테이너로 연결", conn_ok)

    check("빈 섹션(브랜드/기상/해외)이 정직한 빈 상태로 표기(≥3건)",
          html.count('class="acc-empty"') >= 3)
    check("빈 상태 문구가 '현재 수집된 항목 없음'/'미연동'을 정직히 표기",
          "현재 수집된 항목 없음" in html or "미연동" in html)

    # 겹침/가림 방지: 아코디언 영역은 블록 흐름 — absolute/fixed positioning을 쓰지 않는다.
    css = _accordion_css(html)
    check("아코디언 CSS에 position:absolute/fixed 없음(펼쳐도 다른 목록 안 가림)",
          bool(css) and "position:absolute" not in css and "position:fixed" not in css)
    check("아코디언 컨테이너(acc-body) 존재 + 주입 마커 치환됨",
          'id="accordionSections"' in html and "ACCORDION-INJECT" not in html)

    # 기사 본문 링크는 새 탭 안전 속성으로만 노출(외부 링크 정책).
    if "본문 보기" in html:
        check("기사 본문 링크는 target=_blank + rel=noopener noreferrer",
              'rel="noopener noreferrer"' in html and 'target="_blank"' in html)
    else:
        check("기사 본문 링크 정책(표시 항목 없으면 skip)", True)

    leaks = [n for n in ("TELEGRAM_BOT_TOKEN", "GMAIL_SMTP_APP_PASSWORD",
                         "GMAIL_SMTP_USER", "OPERATOR_SHARED_SECRET", "ALERT_EMAIL_TO")
             if n in html]
    check("대시보드 공개 산출물에 secret 이름 없음", not leaks, ", ".join(leaks))


def main() -> int:
    print(f"== verify_dashboard_accordion_sections (D7-AD-N) @ {ROOT} ==")
    # check_brief()를 먼저 호출해 build_executive_brief._bootstrap이 임시 DB_PATH를 app.config
    # import 전에 고정하게 한다 (DB_PATH 캐시 트랩 회피 — app 모듈을 먼저 import하면 repo
    # radar.db로 오염된다). 이후 check들은 이미 import된 temp-path config를 재사용한다.
    check_brief()
    check_config()
    check_finance_brand_display_only()
    check_dashboard_html()
    print()
    if _failures:
        print(f"RESULT: FAIL ({len(_failures)} 항목)")
        return 1
    print("RESULT: PASS — 임원 아코디언 8섹션 + 해외 언론 source group + 표시 전용 잠금")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
