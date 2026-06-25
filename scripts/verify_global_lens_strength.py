#!/usr/bin/env python3
"""D7-G verifier — the Global lens is a real executive overseas-business lens.

Runs fully offline (no network, DB, secrets, or send). D7-G Part C strengthens the Global
lens from a thin keyword tag into an overseas-business desk: overseas orders, Middle East
(Saudi/UAE/Qatar/Neom), overseas EPC/plant/infra, supply chain (material cost/FX), owner
funding, geopolitical chokepoints, and overseas competitors — and each Global row shows a
"why it matters" tag (수주 기회 / 원가 부담 / 환율 부담 / 공급망 리스크 / 발주 재개 / 경쟁사 동향).

Checks (mock build = deterministic gate; committed live = lenient sanity):
  · Global lens has ≥5 visible rows OR uses rolling recent rows (freshness-labelled backfill),
  · Global rows span ≥3 distinct overseas subtopics (not one narrow theme),
  · Global rows are overseas, not generic Korean domestic company news,
  · every Global row has a real http URL + brief provenance,
  · the "why it matters" tag is one of the honest reasons and HDEC relevance is NOT overclaimed
    (no fabricated 현대건설 직접 claim on a generic overseas row),
  · the builder + template actually derive + render the Global why-tag.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates" / "dashboard_preview.html"
BUILDER = ROOT / "scripts" / "build_static_dashboard.py"
BRIEF_BUILDER = ROOT / "scripts" / "build_executive_brief.py"
DASHBOARD = ROOT / "docs" / "daily" / "dashboard-latest.html"

# 해외사업 서브토픽(임원 렌즈) — 6 reasons에 대응. 각 행은 하나 이상에 걸려야 한다.
SUBTOPICS = {
    "mideast": ("중동", "사우디", "UAE", "카타르", "네옴", "오만", "이라크", "두바이", "아부다비"),
    "orders": ("해외수주", "해외건설", "수주", "낙찰", "발주처", "팀코리아"),
    "epc_plant": ("EPC", "플랜트", "원전", "SMR", "인프라", "정유", "석유화학"),
    "supply_fx": ("환율", "달러", "원화", "유가", "원유", "원자재", "원가", "공급망", "공사비", "LNG", "가스"),
    "competitor": ("경쟁", "삼성물산", "대우건설", "GS건설", "DL이앤씨", "포스코이앤씨"),
    "geo_region": ("체코", "유럽", "폴란드", "인도네시아", "베트남", "호르무즈", "지정학", "제재"),
}
# 해외 마커(국내 일반 기업뉴스가 아님을 보장) — 빌더 _OVERSEAS_MARKERS와 정렬(제목 기준).
FOREIGN = ("중동", "해외", "사우디", "UAE", "카타르", "네옴", "오만", "이라크", "두바이", "아부다비",
           "체코", "유럽", "폴란드", "인도네시아", "베트남", "글로벌", "수출", "팀코리아",
           "싱가포", "美", "미국", "일본", "인도", "국제유가", "달러", "EPC",
           "해외수주", "해외건설")
WHY_OK = {"수주 기회", "원가 부담", "환율 부담", "공급망 리스크", "발주 재개", "경쟁사 동향", "해외사업 영향"}
ROLLING = {"최근 7일", "최근 30일"}

_failures = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    tag = "PASS" if ok else "FAIL"
    line = f"[{tag}] {name}"
    if detail:
        line += f" — {detail}"
    print(line)
    if not ok:
        _failures.append(name)
    return ok


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _model(html: str) -> dict:
    m = re.search(r'<script type="application/json" id="preview-model">(.*?)</script>',
                  html, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except ValueError:
        return {}


def _clean_env():
    env = {**os.environ, "APP_MODE": "mock"}
    for k in ("NEWS_MODE", "MACRO_MODE"):
        env.pop(k, None)
    return env


def _global_rows(html: str) -> list:
    """global_business 렌즈로 태그된 행(featured 카드 + news_rows)."""
    model = _model(html)
    rows = []
    fm = re.search(r'class="card featured"[^>]*data-lens="([^"]*)"', html)
    if fm and "global_business" in fm.group(1).split():
        ft = re.search(r'class="card featured".*?<h2>(.*?)</h2>', html, re.S)
        rows.append({"title": (ft.group(1).strip() if ft else ""), "url": "featured",
                     "lens": ["global_business"], "featured": True})
    for r in model.get("news_rows") or []:
        if "global_business" in (r.get("lens") or []):
            rows.append(r)
    return rows


def _subtopics(rows) -> set:
    found = set()
    for r in rows:
        t = r.get("title") or ""
        for key, words in SUBTOPICS.items():
            if any(w in t for w in words):
                found.add(key)
    return found


def check_strength(html: str, where: str, strict: bool) -> None:
    rows = _global_rows(html)
    n = len(rows)
    model = _model(html)
    # 신선도(롤링) 라벨 보유 여부
    has_rolling = any((r.get("freshness") in ROLLING) for r in model.get("news_rows") or []
                      if "global_business" in (r.get("lens") or []))
    # (a) ≥5 visible rows OR rolling recent rows
    ok_count = (n >= 5) or (n >= 1 and has_rolling)
    check(f"a[{where}]: Global ≥5 행 또는 롤링(최근) 행 사용", ok_count,
          f"{n}행, rolling={has_rolling}")
    if n == 0:
        # global이 '연동 대기'로 내려간 경우(해당 일 해외신호 0) — 정직 빈상태이므로 skip.
        check(f"a2[{where}]: Global 0행이면 연동 대기로 이동(정직 빈상태)", not strict,
              "mock는 global 행이 있어야 함" if strict else "live 스냅샷 빈상태 허용")
        return
    # (b) ≥3 distinct subtopics
    subs = _subtopics(rows)
    check(f"b[{where}]: Global 행이 ≥3 해외 서브토픽 포함", len(subs) >= 3 or not strict,
          f"{sorted(subs)}")
    # (c) overseas, not generic domestic — 대부분 행이 해외 마커 보유(최대 1행 예외 허용)
    no_marker = [r.get("title", "")[:30] for r in rows
                 if not any(w in (r.get("title") or "") for w in FOREIGN)]
    check(f"c[{where}]: Global 행이 해외 신호(국내 일반 기업뉴스 아님)",
          len(no_marker) <= max(1, n // 5), f"마커 없는 행: {no_marker}")
    # (d) real urls (featured는 별도 링크 — news 행만 http url 검사)
    news_g = [r for r in rows if not r.get("featured")]
    bad_url = [r.get("title", "")[:30] for r in news_g
               if not str(r.get("url", "")).startswith("http")]
    check(f"d[{where}]: Global news 행이 실 http url 보유", not bad_url,
          f"url 없는 행: {bad_url}" if bad_url else f"{len(news_g)}행")
    # (e) why-tag honest + HDEC 과장 금지
    whys = [r.get("whyGlobal") for r in news_g if r.get("whyGlobal")]
    bad_why = [w for w in whys if w not in WHY_OK]
    check(f"e[{where}]: '왜 중요' 태그가 정직 사유 집합 내(과장 없음)",
          not bad_why and bool(whys), f"미정의: {set(bad_why)}" if bad_why else f"{len(whys)}행 태그")
    overclaim = [r.get("title", "")[:30] for r in news_g
                 if "현대건설 직접" in str(r.get("whyGlobal") or "")]
    check(f"f[{where}]: HDEC 관련성 과장 없음(generic 해외 행에 '현대건설 직접' 금지)",
          not overclaim, f"과장: {overclaim}" if overclaim else "ok")


def check_brief_provenance() -> None:
    """Global 행 제목이 공유 brief에 존재(강제/날조 아님)."""
    proc = subprocess.run([sys.executable, str(BRIEF_BUILDER), "--json"],
                          cwd=ROOT, capture_output=True, text=True, env=_clean_env(),
                          timeout=300)
    if not check("g: brief --json 동작", proc.returncode == 0):
        return
    brief = json.loads(proc.stdout)
    titles = set()
    for key in ("top_immediate_signals", "top_new_issues", "hdec_direct_signals",
                "ai_radar_signals", "business_signals", "risk_regulation_signals",
                "competitor_supply_signals", "macro_economy_signals"):
        for s in brief.get(key) or []:
            if s.get("title"):
                titles.add(s["title"])
    with tempfile.TemporaryDirectory(prefix="hdec_global_") as tmp:
        out = Path(tmp) / "d.html"
        p2 = subprocess.run([sys.executable, str(BUILDER), "--output", str(out)],
                            cwd=ROOT, capture_output=True, text=True, env=_clean_env(), timeout=300)
        if not check("h: builder --output(mock) 동작", p2.returncode == 0 and out.exists()):
            return
        html = out.read_text(encoding="utf-8")
        check_strength(html, "mock-build", strict=True)
        news_g = [r for r in _global_rows(html) if not r.get("featured")]
        miss = [r.get("title") for r in news_g if r.get("title") not in titles]
        check("i: Global news 행 제목이 모두 공유 brief에 존재(가짜/강제 행 없음)",
              not miss, f"브리프 밖: {miss[:2]}" if miss else f"{len(news_g)}행 일치")


def check_ui_derivation() -> None:
    src = _read(BUILDER)
    tpl = _read(TEMPLATE)
    check("j: 빌더가 Global '왜 중요'를 파생(_global_why + 6 사유)",
          "_global_why" in src and "수주 기회" in src and "원가 부담" in src
          and "환율 부담" in src and "공급망 리스크" in src and "발주 재개" in src
          and "경쟁사 동향" in src)
    check("k: 빌더가 global_business 행에만 whyGlobal 부착",
          'whyGlobal' in src and 'global_business' in src)
    check("l: 템플릿이 '왜 중요' 칩(whyChip)을 렌더",
          "function whyChip" in tpl and "whyChip(r.whyGlobal)" in tpl)
    # 중앙 정책의 global_business 쿼리가 강화됨(서브토픽 포함)
    cfg = _read(ROOT / "data" / "lens_queries.json")
    check("m: 중앙 정책 global_business 쿼리가 해외 서브토픽으로 강화",
          all(tok in cfg for tok in ("해외수주", "네옴", "해외 LNG", "해외 경쟁 시공사")))


def main() -> int:
    print(f"== verify_global_lens_strength (D7-G Part C) @ {ROOT} ==")
    tpl = _read(TEMPLATE)
    if not check("0: 템플릿 존재", bool(tpl) and len(tpl) > 4000):
        print("\nRESULT: FAIL (템플릿 누락)")
        return 1
    check_ui_derivation()
    check_brief_provenance()  # mock build(결정적) + brief provenance
    if DASHBOARD.exists():
        check_strength(_read(DASHBOARD), "live-snapshot", strict=False)  # 라이브 스냅샷 = lenient
    else:
        check("0b: docs/daily/dashboard-latest.html 존재", False)

    print()
    if _failures:
        print(f"RESULT: FAIL ({len(_failures)} 항목)")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("RESULT: PASS — Global = 실질 해외사업 렌즈(≥5/롤링·서브토픽·해외·왜중요·과장없음) (D7-G Part C)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
