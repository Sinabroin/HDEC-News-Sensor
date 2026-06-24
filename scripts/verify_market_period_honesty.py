#!/usr/bin/env python3
"""D7-D verifier — market period controls are honest (no fake distinct historical ranges).

Runs fully offline (no network, DB, secrets, or send). The summary dashboard market
panel exposes period buttons (1주/1개월/3개월/1년) but the underlying demo series is a
single shared base that is only resampled to the selected window — so 3개월·1년 render
the *same* demo trend. Presenting them as real distinct historical ranges is misleading.

This verifier checks the template (and the regenerated dashboard) so that:

  · period controls (yieldPeriodSeg / dwPeriodSeg) carry visible honest labeling that
    they are demo display-windows and that per-period real history is 미연동,
  · the 3개월·1년 identical-trend reality is disclosed (not hidden),
  · the demo series is a single shared base resampled per period — no fabricated
    per-period price arrays are introduced,
  · market values never claim to be current/live (현재 체결값 is always negated),
  · 지연/대용/미연동 honesty labels stay.
"""

import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates" / "dashboard_preview.html"
BUILDER = ROOT / "scripts" / "build_static_dashboard.py"
DASHBOARD = ROOT / "docs" / "daily" / "dashboard-latest.html"

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


# ---------------------------------------------------------------------------
# 1 · 기간 컨트롤 존재 + 정직 라벨
# ---------------------------------------------------------------------------

def check_period_controls(tpl: str) -> None:
    check("1a: 금리 차트 기간 세그(yieldPeriodSeg) 존재",
          'id="yieldPeriodSeg"' in tpl)
    check("1b: 시장 드로어 기간 세그(dwPeriodSeg) 존재",
          'id="dwPeriodSeg"' in tpl)
    for lab in ("1주", "1개월", "3개월", "1년"):
        check(f"1c: 기간 버튼 '{lab}' 존재", lab in tpl)
    # 핵심: 기간 버튼이 데모/표시 구간이며 기간별 실측 히스토리가 미연동임을 명시
    check("1d: 기간 버튼이 '기간별 실측 히스토리 미연동' 정직 라벨을 가짐",
          "기간별 실측 히스토리" in tpl and "미연동" in tpl)
    # 3개월·1년이 동일 데모 추세임을 숨기지 않고 공개
    check("1e: 3개월·1년 동일 데모 추세 사실을 공개",
          "동일 데모 추세" in tpl or "3개월·1년 동일" in tpl)
    # 차트 노트(JS)에도 동일 정직 표기를 주입
    check("1f: 차트 노트에 기간 데모 표기(PERIOD_DEMO_NOTE) 주입",
          "PERIOD_DEMO_NOTE" in tpl and "기간 버튼은 표시 구간" in tpl)


# ---------------------------------------------------------------------------
# 2 · 단일 공유 베이스 리샘플 (가짜 기간별 데이터 없음)
# ---------------------------------------------------------------------------

def check_no_fake_period_data(tpl: str) -> None:
    check("2a: PERIODS 정의 존재 (기간 = x축 라벨)", "var PERIODS" in tpl)
    check("2b: resample()로 공유 베이스를 기간 길이에 맞춤", "function resample" in tpl)
    # PERIODS 값은 날짜 라벨 문자열이어야 한다 — 기간 키에 숫자 시계열을 만들면 가짜 데이터.
    m = re.search(r"var PERIODS\s*=\s*\{(.*?)\};", tpl, re.S)
    body = m.group(1) if m else ""
    fake = re.search(r'"(1w|1m|3m|1y)"\s*:\s*\[\s*-?\d', body)
    check("2c: 기간 키에 숫자 시계열(가짜 기간별 가격) 없음 — 날짜 라벨만",
          bool(body) and not fake,
          "가짜 기간별 숫자 배열 감지" if fake else "날짜 라벨만")
    # 별도의 per-period 베이스 변수(YBASE_3M 등)를 만들지 않았는지
    check("2d: per-period 별도 베이스 변수(YBASE_3M/_1Y 등) 미생성",
          not re.search(r"YBASE_(1W|1M|3M|1Y|3m|1y)", tpl))


# ---------------------------------------------------------------------------
# 3 · 시장 값은 현재/실시간을 주장하지 않음
# ---------------------------------------------------------------------------

def check_not_live(tpl: str) -> None:
    check("3a: '현재 체결값 아님' 정직 표기 존재",
          "현재 체결값이 아닙니다" in tpl or "현재 체결값 아님" in tpl)
    # 모든 '현재 체결값' 등장은 부정(아님/아니/않/없)과 동반되어야 한다 (live 주장 금지)
    bad = []
    for mm in re.finditer(r"현재 체결값", tpl):
        tail = tpl[mm.end():mm.end() + 14]
        if not any(neg in tail for neg in ("아님", "아니", "아닙", "않", "없")):
            bad.append(tpl[mm.start():mm.end() + 14])
    check("3b: '현재 체결값'이 live로 주장되지 않음(항상 부정 동반)",
          not bad, f"미부정 사례: {bad[:2]}" if bad else "ok")
    for lab in ("지연", "대용", "미연동"):
        check(f"3c: 시장 정직 라벨 '{lab}' 유지", lab in tpl)


# ---------------------------------------------------------------------------
# 4 · 재생성된 대시보드에도 정직 라벨 반영
# ---------------------------------------------------------------------------

def check_regenerated() -> None:
    with tempfile.TemporaryDirectory(prefix="hdec_period_") as tmp:
        out = Path(tmp) / "dashboard-latest.html"
        proc = subprocess.run([sys.executable, str(BUILDER), "--output", str(out)],
                              cwd=ROOT, capture_output=True, text=True, timeout=300)
        if not check("4a: builder --output 동작", proc.returncode == 0 and out.exists(),
                     (proc.stderr or "")[-200:]):
            return
        gen = out.read_text(encoding="utf-8")
        check("4b: 재생성 대시보드에 기간 데모 라벨 유지",
              "기간별 실측 히스토리" in gen and "미연동" in gen)
        check("4c: 재생성 대시보드가 현재 체결값을 주장하지 않음",
              "현재 체결값이 아닙니다" in gen or "현재 체결값 아님" in gen)
    # 커밋된 대시보드도 동일(존재 시)
    if DASHBOARD.exists():
        com = _read(DASHBOARD)
        check("4d: 커밋된 dashboard-latest.html에 기간 데모 라벨 유지",
              "기간별 실측 히스토리" in com and "미연동" in com)


def main() -> int:
    print(f"== verify_market_period_honesty @ {ROOT} ==")
    tpl = _read(TEMPLATE)
    if not check("0: dashboard_preview.html 템플릿 존재", bool(tpl) and len(tpl) > 4000):
        print("\nRESULT: FAIL (템플릿 누락)")
        return 1
    check_period_controls(tpl)
    check_no_fake_period_data(tpl)
    check_not_live(tpl)
    check_regenerated()

    print()
    if _failures:
        print(f"RESULT: FAIL ({len(_failures)} 항목)")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("RESULT: PASS — 시장 기간 컨트롤 정직성 확인 (D7-D Part A)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
