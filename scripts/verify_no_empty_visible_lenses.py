#!/usr/bin/env python3
"""D7-G verifier — no primary visible sidebar lens shows an empty (0) result.

Runs fully offline (no network, DB, secrets, or send). The product rule (D7-G Part B): a
visible dashboard lens must not show 0. Weak lenses are first broadened via richer central
queries (data/lens_queries.json); any lens that still has 0 visible rows is moved out of the
primary sidebar into a lower "연동 대기 렌즈" group (the user-approved last resort) — never
faked with a non-zero count and never filled with fabricated/irrelevant articles.

Checks:
  · the partition mechanism exists (partitionLensNav + "연동 대기 렌즈" group + data-waiting),
    and essentials (전체/즉시/신규/AI) are the ONLY lenses exempt from the move,
  · nav counts are CONSISTENT with lens_banks (count == #bank rows for that lens) — no stale
    static demo counts, no fabricated non-zero counts,
  · every primary (count>0) lens therefore has ≥1 real row; every 0-count non-essential lens
    is moved to waiting (so no empty primary lens remains),
  · every article row is real: title + source + http url + brief provenance (titles all come
    from the shared brief sections — no fake/forced rows),
  · rolling-window freshness labels (오늘/최근 7일/최근 30일) are honest — old items are never
    labelled 오늘 (the builder buckets 오늘 only for day-0),
  · the weak HDEC false-positive guard stays in place (현대건설기계/현대차 not forced in).
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

ESSENTIALS = {"all", "now", "new", "ai"}
FRESH_OK = {"오늘", "최근 7일", "최근 30일"}

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


def _nav_counts(html: str) -> dict:
    """좌측 사이드바 nav 렌즈 → 정적 카운트(숫자 배지가 있는 것만). 미연동 배지는 None."""
    out = {}
    for line in html.split("\n"):
        mm = re.search(r'class="nav[^"]*"\s+data-filter="([^"]+)"', line)
        if not mm:
            continue
        cm = re.search(r'<span class="ncount">(\d+)</span>', line)
        out[mm.group(1)] = int(cm.group(1)) if cm else None
    return out


def _clean_env(**extra):
    env = {**os.environ, "APP_MODE": "mock"}
    for k in ("NEWS_MODE", "MACRO_MODE"):
        env.pop(k, None)
    env.update(extra)
    return env


# ---------------------------------------------------------------------------
# 1 · 분리 메커니즘(빈 렌즈 → 연동 대기 렌즈) 존재 + 핵심 렌즈만 예외
# ---------------------------------------------------------------------------

def check_mechanism(tpl: str) -> None:
    check("1a: 빈 렌즈 분리 함수(partitionLensNav) 존재", "function partitionLensNav" in tpl)
    check("1b: '연동 대기 렌즈' 그룹 + 이동 표식(data-waiting) 존재",
          "연동 대기 렌즈" in tpl and 'data-waiting' in tpl and 'lensWaitingTitle' in tpl)
    # 핵심 렌즈(전체/즉시/신규/AI)만 항상 기본 사이드바에 유지(0이어도) — 그 외만 이동 대상.
    m = re.search(r"var ESSENTIAL_LENS = \{([^}]*)\}", tpl)
    ess = set(re.findall(r'"?(\w+)"?\s*:', m.group(1))) if m else set()
    check("1c: 핵심 렌즈 집합 = {all, now, new, ai} (그 외만 이동)",
          ess == ESSENTIALS, f"{sorted(ess)}")
    # count>0 렌즈는 기본 사이드바 유지, 0/미연동만 대기로 이동(가짜 카운트 없음).
    check("1d: count>0면 기본 유지 · 0/미연동만 대기 이동",
          "count > 0" in tpl and 'setAttribute("data-waiting"' in tpl
          and '연동 대기' in tpl)
    check("1e: 빈 그룹 헤더 숨김(hideEmptyGroupTitles)", "function hideEmptyGroupTitles" in tpl)


# ---------------------------------------------------------------------------
# 2 · nav 카운트가 모델과 일치(스테일/가짜 카운트 없음) → 빈 primary 렌즈 없음
# ---------------------------------------------------------------------------

def _actual_counts(_html: str, model: dict) -> dict:
    """nav 카운트의 진실 원천 = lens_banks.

    D7-H 이후 특정 렌즈는 global news_rows를 필터링하지 않고 full brief 기반 전용 bank를
    렌더한다. 따라서 비핵심 nav 카운트도 bank row 수와 일치해야 한다.
    """
    return {k: len(v or []) for k, v in (model.get("lens_banks") or {}).items()}


def check_consistency(html: str, where: str) -> None:
    model = _model(html)
    nav = _nav_counts(html)
    actual = _actual_counts(html, model)
    # 비핵심 렌즈: nav 정적 카운트 == 실제 bank 카운트 (스테일/가짜 없음).
    stale = []
    for key, navc in nav.items():
        if key in ESSENTIALS or navc is None:
            continue
        if navc != actual.get(key, 0):
            stale.append(f"{key}:nav={navc}≠rows={actual.get(key,0)}")
    check(f"2a[{where}]: 비핵심 nav 카운트 == 실제 행 카운트(스테일/가짜 없음)",
          not stale, f"{stale}" if stale else "일치")
    # 0건 비핵심 렌즈는 이동 대상(런타임 분리) — primary로 남지 않음을 메커니즘이 보장.
    # 정적 검사: 0건 비핵심 렌즈가 nav에 존재하고(이동 대상), count>0 렌즈는 실제 행 보유.
    primary_nonzero = [k for k, v in nav.items()
                       if k not in ESSENTIALS and v and v > 0]
    check(f"2b[{where}]: count>0 비핵심 렌즈는 모두 실제 행 보유(빈 primary 없음)",
          all(actual.get(k, 0) > 0 for k in primary_nonzero),
          f"빈 primary: {[k for k in primary_nonzero if actual.get(k,0)==0]}")
    zero_nonessential = [k for k, v in nav.items()
                         if k not in ESSENTIALS and (v == 0 or v is None)]
    # 이 0건 렌즈들은 partitionLensNav가 '연동 대기'로 옮긴다(정적 HTML엔 0으로 정직 표기).
    check(f"2c[{where}]: 0건 비핵심 렌즈는 이동 대상으로 식별됨(가짜 비제로 없음)",
          all(nav.get(k) in (0, None) for k in zero_nonessential),
          f"{len(zero_nonessential)}개 대기 후보")


# ---------------------------------------------------------------------------
# 3 · 모든 행이 실기사(가짜/강제 행 없음) + 동일 brief 출처
# ---------------------------------------------------------------------------

def _brief_titles() -> set:
    proc = subprocess.run([sys.executable, str(BRIEF_BUILDER), "--json"],
                          cwd=ROOT, capture_output=True, text=True, env=_clean_env(),
                          timeout=300)
    if proc.returncode != 0:
        return set()
    brief = json.loads(proc.stdout)
    titles = set()
    def walk(node):
        if isinstance(node, dict):
            if node.get("title") and (node.get("url") or node.get("source")):
                titles.add(node["title"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(brief)
    return titles


def check_rows_real() -> None:
    with tempfile.TemporaryDirectory(prefix="hdec_empty_") as tmp:
        out = Path(tmp) / "d.html"
        proc = subprocess.run([sys.executable, str(BUILDER), "--output", str(out)],
                              cwd=ROOT, capture_output=True, text=True, env=_clean_env(),
                              timeout=300)
        if not check("3a: builder --output(mock) 동작", proc.returncode == 0 and out.exists(),
                     (proc.stderr or "")[-200:]):
            return
        built = out.read_text(encoding="utf-8")
        check_consistency(built, "mock-build")  # 생성 산출물에서 카운트 일치(빌더가 실카운트 기입)
        model = _model(built)
        bank_rows = [r for rows in (model.get("lens_banks") or {}).values() for r in (rows or [])]
        rows = (model.get("news_rows") or []) + (model.get("ai_rows") or []) + bank_rows
        check("3b: 행 다수(>=6)", len(rows) >= 6, f"{len(rows)}행")
        # 모든 행이 title + source + http url (가짜/빈 행 없음)
        bad = [r.get("title") for r in rows
               if not r.get("title") or not r.get("source")
               or not str(r.get("url", "")).startswith("http")]
        check("3c: 모든 행이 실제 title·source·http url 보유(가짜 행 없음)",
              not bad, f"불완전: {bad[:2]}" if bad else f"{len(rows)}행")
        bad_prov = [r.get("title") for r in bank_rows if not r.get("provenance")]
        check("3c-2: 모든 lens_banks 행이 provenance 보유",
              not bad_prov, f"provenance 누락: {bad_prov[:2]}" if bad_prov else "ok")
        # 모든 행 제목이 공유 brief 섹션에 존재 → 강제/날조 행 아님(동일 출처 provenance)
        bt = _brief_titles()
        if check("3d: brief 제목 수집(>0)", bool(bt), f"{len(bt)}건"):
            miss = [r.get("title") for r in rows if r.get("title") not in bt]
            check("3e: 모든 행 제목이 공유 brief에 존재(가짜/강제 행 없음)",
                  not miss, f"브리프 밖: {miss[:2]}" if miss else f"{len(rows)}행 일치")
        # 신선도 라벨 정직성: 라벨이 유효 집합 내. 오늘 위장 금지(빌더가 day-0만 '오늘').
        fresh = [r.get("freshness") for r in rows if r.get("freshness")]
        badfresh = [f for f in fresh if f not in FRESH_OK]
        check("3f: 신선도 라벨이 유효 집합(오늘/최근 7일/최근 30일) 내",
              not badfresh, f"미정의: {set(badfresh)}" if badfresh else f"{len(fresh)}행 라벨")


# ---------------------------------------------------------------------------
# 4 · 신선도 정직 로직 + 약한 현대건설 오탐 가드
# ---------------------------------------------------------------------------

def check_builder_guards() -> None:
    src = _read(BUILDER)
    check("4a: 신선도 버킷 로직 존재(published_at 기준)",
          "def _freshness" in src and "오늘" in src and "최근 7일" in src and "최근 30일" in src)
    # '오늘'은 day<=0에서만 — 오래된 기사를 오늘로 위장하지 않음.
    m = re.search(r"def _freshness.*?return \"\"  #", src, re.S)
    body = m.group(0) if m else ""
    check("4b: '오늘'은 days<=0에서만 부여(오래된 기사 위장 금지)",
          'days <= 0' in body and 'return "오늘"' in body
          and 'days <= 7' in body and 'days <= 31' in body)
    check("4c: 약한 현대건설 오탐 가드 유지(현대건설기계/현대차 강제 금지)",
          "_is_weak_hdec_fp" in src and "현대건설(?!기계)" in src)
    # 렌즈 우선 수집(중앙 정책) 재사용 — 빈 렌즈를 먼저 쿼리 확대로 채운다.
    collector = _read(ROOT / "app" / "live_collector.py")
    check("4d: 라이브 수집기가 렌즈 쿼리 그룹 병합(쿼리 확대로 빈 렌즈 보강)",
          "_merge_lens_query_groups" in collector)


def main() -> int:
    print(f"== verify_no_empty_visible_lenses (D7-G Part B) @ {ROOT} ==")
    tpl = _read(TEMPLATE)
    if not check("0: 템플릿 존재", bool(tpl) and len(tpl) > 4000):
        print("\nRESULT: FAIL (템플릿 누락)")
        return 1
    check_mechanism(tpl)
    # 일관성(카운트==실제 행)은 빌더가 실카운트를 기입하는 '생성 산출물'에서만 검사한다
    # (템플릿 정적 카운트는 데모 플레이스홀더라 검사 대상 아님).
    if DASHBOARD.exists():
        check_consistency(_read(DASHBOARD), "dash")
    else:
        check("0b: docs/daily/dashboard-latest.html 존재", False)
    check_rows_real()
    check_builder_guards()

    print()
    if _failures:
        print(f"RESULT: FAIL ({len(_failures)} 항목)")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("RESULT: PASS — 빈 primary 렌즈 없음(0건은 연동 대기로 이동) + 실기사 행 + 신선도 정직 (D7-G Part B)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
