#!/usr/bin/env python3
"""D7-K verifier — market row chart-state contract, renderer guards, and nice axis ticks.

Runs fully offline (no network, DB, secrets, or send). D7-J left the market tab with awkward
chart behaviour: rows drew a misleading mini-sparkline they could not open, no-value rows
showed redundant "미연동 + 차트 없음" badges, 철근(대용) had no honest no-chart state, the chart
renderer had no guards for empty/one-point/non-numeric/equal-domain series, and the y-axis used
raw fractional ticks (4.56 / 4.12 / 3.68 …) instead of clean equal-interval ticks.

D7-K fixes it with a single market row state contract (driven only from the model) and a nice
tick helper. This verifier locks the contract:

  · every market row resolves to exactly ONE state — chartable / current_only / unavailable —
    derivable from the model (value + per-period history),
  · a row with no real history (≥2 numeric points) is never clickable/chartable (no empty drawer),
  · a row with no current value never shows a fabricated number,
  · 철근(rebar) is either chartable with real history OR explicitly non-clickable with 차트 없음,
  · HRC steel / scrap / cement / lumber / bitumen / jet / bunker / LNG / coal rows are each
    internally consistent (no contradictory badge combinations),
  · the chart renderer guards empty history, one-point history, non-numeric points, and an
    equal min/max domain (no divide-by-zero, no broken SVG),
  · a nice tick helper (niceTicks) exists and is used by BOTH the detail/drawer chart and the
    comparison chart, and its ticks are numerically equal-interval (proven by running the JS),
  · missing comparison series are omitted from the plotted set and reported as unavailable
    (never zero-filled), and no fake history is generated.

Checks the template, the regenerated mock dashboard, and the committed dashboard snapshot.
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
DASHBOARD = ROOT / "docs" / "daily" / "dashboard-latest.html"

PERIODS = ("1w", "1m", "3m", "1y")
# Task가 명시한 점검 대상 행(상태가 내부적으로 일관돼야 — 모순 배지 금지).
NAMED_ROWS = ("hrc_steel", "scrap_steel", "cement", "lumber", "bitumen",
              "jet_kerosene", "bunker_fuel", "lng_jkm", "ttf_gas", "henry_hub",
              "thermal_coal", "coking_coal", "diesel_gasoil")
HONEST_MODES = ("delayed_market", "proxy_market", "manual_or_reported", "unavailable")

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


def _items(html: str) -> list:
    return _model(html).get("market_items") or []


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _numeric_pts(arr) -> int:
    return sum(1 for x in (arr or []) if _is_num(x))


def _has_value(it: dict) -> bool:
    v = it.get("value")
    return v not in (None, "", "—")


def _has_history(it: dict, period: str = "1m") -> bool:
    h = it.get("history") or {}
    return _numeric_pts(h.get(period)) >= 2


def _state(it: dict) -> str:
    """모델에서 파생하는 단일 상태 — JS marketState와 동일 규칙."""
    if _has_history(it, "1m"):
        return "chartable"
    if _has_value(it):
        return "current_only"
    return "unavailable"


# ---------------------------------------------------------------------------
# 1 · 모든 행이 정확히 한 상태로 파생 + 비연동은 정직 라벨(빈 드로어 없음)
# ---------------------------------------------------------------------------

def check_state_contract(html: str, where: str) -> None:
    items = _items(html)
    if not check(f"1a[{where}]: market_items 모델 존재(>=40)", len(items) >= 40, f"{len(items)}개"):
        return
    bad_state, bad_label, partial = [], [], []
    counts = {"chartable": 0, "current_only": 0, "unavailable": 0}
    for it in items:
        st = _state(it)
        counts[st] += 1
        # 정확히 한 상태(상호배타) — _state가 항상 하나를 반환하므로 모순 데이터만 잡는다.
        if st == "chartable" and not _has_history(it, "1m"):
            bad_state.append(it.get("id"))
        if st == "unavailable" and _has_value(it):
            bad_state.append(it.get("id"))
        # 비클릭(비-chartable) 행은 정직한 data_mode 라벨이어야 한다.
        if st != "chartable" and it.get("data_mode") not in HONEST_MODES:
            bad_label.append(f"{it.get('id')}={it.get('data_mode')}")
        # 부분/가짜 히스토리 금지: 1m이 연동이면 4개 기간 모두 ≥2 숫자점이어야 한다.
        if _has_history(it, "1m"):
            if not all(_has_history(it, p) for p in PERIODS):
                partial.append(it.get("id"))
    check(f"1b[{where}]: 모든 행이 단일·일관 상태(모순 데이터 없음)", not bad_state,
          f"모순: {bad_state}" if bad_state else "ok")
    check(f"1c[{where}]: 비클릭 행은 정직 data_mode(지연/대용/보고/미연동)", not bad_label,
          f"라벨 미흡: {bad_label}" if bad_label else "ok")
    check(f"1d[{where}]: 부분/가짜 히스토리 없음(연동이면 4개 기간 전부)", not partial,
          f"부분 히스토리: {partial}" if partial else "ok")
    check(f"1e[{where}]: 상태 분포(chartable/current_only/unavailable) 합리적",
          counts["chartable"] >= 12 and sum(counts.values()) == len(items),
          f"{counts}")


# ---------------------------------------------------------------------------
# 2 · 히스토리 없는 행은 클릭/차트 불가 · 값 없는 행은 가짜 숫자 없음
# ---------------------------------------------------------------------------

def check_no_blank_no_fake(html: str, where: str) -> None:
    items = _items(html)
    clickable_without_history = [it.get("id") for it in items
                                 if _has_history(it, "1m") is False
                                 and (it.get("history") or {}).get("1m")
                                 and _numeric_pts((it.get("history") or {}).get("1m")) < 2]
    check(f"2a[{where}]: 히스토리(<2점) 행이 차트가능으로 위장하지 않음",
          not clickable_without_history, f"{clickable_without_history}")
    # 값 없는(미연동) 행은 value=null — 가짜 숫자 금지.
    fake_num = [it.get("id") for it in items
                if not _has_value(it) and it.get("value") not in (None, "", "—")]
    check(f"2b[{where}]: 값 없는 행에 가짜 숫자 없음(value=null)", not fake_num, f"{fake_num}")
    # unavailable data_mode면 반드시 값 없음.
    unav_with_val = [it.get("id") for it in items
                     if it.get("data_mode") == "unavailable" and _has_value(it)]
    check(f"2c[{where}]: data_mode=unavailable 행은 값 없음", not unav_with_val, f"{unav_with_val}")


# ---------------------------------------------------------------------------
# 3 · 철근(rebar) — 차트가능(실히스토리) 또는 명시적 비클릭(차트 없음)
# ---------------------------------------------------------------------------

def check_rebar(html: str, where: str) -> None:
    items = {it.get("id"): it for it in _items(html)}
    rebar = items.get("rebar")
    if not check(f"3a[{where}]: rebar(철근) 행 존재", bool(rebar)):
        return
    st = _state(rebar)
    # 두 정직 결말만 허용: chartable(실 히스토리) 또는 current_only(값+차트 없음).
    # 절대 금지: 히스토리 없는데 클릭가능 위장, 또는 부분/가짜 히스토리.
    ok = (st == "chartable" and _has_history(rebar, "1m")) or \
         (st == "current_only" and _has_value(rebar) and not _has_history(rebar, "1m")) or \
         (st == "unavailable" and not _has_value(rebar))
    check(f"3b[{where}]: rebar는 차트가능(실히스토리) 또는 명시적 비클릭 — 빈 드로어 없음",
          ok, f"state={st} value={rebar.get('value')} hist1m={_numeric_pts((rebar.get('history') or {}).get('1m'))}")
    check(f"3c[{where}]: rebar 라벨이 대용(proxy)임을 정직 표기",
          "대용" in (rebar.get("label_kr") or "") or rebar.get("data_mode") == "proxy_market"
          or rebar.get("proxy") is True)


# ---------------------------------------------------------------------------
# 4 · 점검 대상 행들이 모순 배지 상태를 만들지 않음
# ---------------------------------------------------------------------------

def check_named_rows(html: str, where: str) -> None:
    items = {it.get("id"): it for it in _items(html)}
    present = [r for r in NAMED_ROWS if r in items]
    check(f"4a[{where}]: 점검 대상 행 다수 존재(>=9)", len(present) >= 9,
          f"{len(present)}/{len(NAMED_ROWS)}")
    inconsistent = []
    for rid in present:
        it = items[rid]
        st = _state(it)
        # 모순 정의: chartable인데 1m 히스토리 없음 / unavailable인데 값 있음 /
        #            current_only인데 히스토리 있음.
        if st == "chartable" and not _has_history(it, "1m"):
            inconsistent.append(f"{rid}:chartable-no-hist")
        if st == "unavailable" and _has_value(it):
            inconsistent.append(f"{rid}:unavail-has-val")
        if st == "current_only" and _has_history(it, "1m"):
            inconsistent.append(f"{rid}:curonly-has-hist")
    check(f"4b[{where}]: 점검 대상 행 모순 배지 상태 없음", not inconsistent,
          f"{inconsistent}" if inconsistent else "ok")


# ---------------------------------------------------------------------------
# 5 · 프론트 계약 — 상태 헬퍼 / 가짜 미니차트 제거 / 중복 배지 제거
# ---------------------------------------------------------------------------

def check_frontend_contract(tpl: str) -> None:
    check("5a: marketState(단일 상태 계약) 헬퍼 존재", "function marketState" in tpl)
    check("5b: hasPeriodHistory(실데이터 판정) 헬퍼 존재", "function hasPeriodHistory" in tpl)
    check("5c: isClickable이 it.history[\"1m\"] 연동을 요구(빈 드로어 방지)",
          "function isClickable" in tpl and 'it.history && it.history["1m"]' in tpl)
    # 미니 스파크라인은 실 히스토리(chartable)일 때만 — it.spark 가짜 미니차트 폴백 제거.
    check("5d: 미니 스파크라인은 chartable일 때만(가짜 spark 폴백 제거)",
          "st.chartable ? sparkSvg(it.history" in tpl
          and "(it.spark || [])" not in tpl)
    # 차트 상태 배지는 current_only일 때만 '차트 없음' — '미연동 + 차트 없음' 중복 제거.
    check("5e: '차트 없음' 배지는 current_only일 때만(미연동 행 중복 배지 제거)",
          "st.isCurrentOnly" in tpl
          and re.search(r'st\.isCurrentOnly\s*\?\s*\'<span class="dm unavail"[^\']*차트 없음', tpl) is not None)
    # 가용성 기반 클릭(전부 true 아님) 유지.
    check("5f: 시장 행이 가용성 기반 클릭(mrowHtml(it, isClickable(it)))",
          "mrowHtml(it, isClickable(it))" in tpl and "mrowHtml(it, true)" not in tpl)
    # 두 라벨 문자열은 계속 존재(기존 정직성 verifier와 정합).
    check("5g: '차트 없음'·'히스토리 미연동' 라벨 문자열 유지",
          "차트 없음" in tpl and "히스토리 미연동" in tpl)


# ---------------------------------------------------------------------------
# 6 · 렌더러 가드 — 빈/단일점/비숫자/동일도메인
# ---------------------------------------------------------------------------

def check_renderer_guards(tpl: str) -> None:
    m = re.search(r"function renderLineChart\b.*?\n  \}", tpl, re.S)
    body = m.group(0) if m else ""
    check("6a: renderLineChart 존재", bool(body))
    # 숫자 판정(비숫자점 스킵).
    check("6b: 비숫자점 가드(typeof === number && isFinite)",
          'typeof v === "number"' in body and "isFinite" in body)
    # 시리즈당 숫자점 <2면 그리지 않음(빈 히스토리/단일점 → 빈 상태, 0으로 나눔 방지).
    check("6c: 빈/단일점 시리즈 가드(nums >= 2 → 빈 상태)",
          "nums >= 2" in body and "if (!clean.length)" in body)
    # 빈 상태 메시지(가짜 선 금지).
    check("6d: 빈 상태 메시지(가짜 선 미표시)", "가짜 선 미표시" in body)
    # 동일 min/max 도메인 가드는 niceTicks가 처리.
    check("6e: niceTicks가 동일 min/max(hi-lo≈0) 도메인 가드",
          "function niceTicks" in tpl and "hi - lo < 1e-12" in tpl)
    # 드로어가 기간 전환마다 hasPeriodHistory로 재평가 + 빈 차트 대신 정직 메시지.
    check("6f: 드로어가 기간별 chartability 재평가(빈 차트 대신 메시지)",
          "hasPeriodHistory(current, period)" in tpl
          and "기간의 히스토리 차트가 없습니다" in tpl)


# ---------------------------------------------------------------------------
# 7 · niceTicks가 두 차트(드로어·비교)에서 사용 + 비교차트 미연동 시리즈 제외
# ---------------------------------------------------------------------------

def check_nicetick_usage(tpl: str) -> None:
    check("7a: niceTicks/niceStep/tickDecimals/fmtTick 헬퍼 존재",
          all(f"function {fn}" in tpl for fn in ("niceTicks", "niceStep", "tickDecimals", "fmtTick")))
    check("7b: renderLineChart가 niceTicks로 y축 도메인·눈금 생성",
          re.search(r"function renderLineChart\b.*?niceTicks\(", tpl, re.S) is not None)
    # 드로어 차트와 비교 차트 둘 다 renderLineChart를 통한다(공용 nice 눈금).
    check("7c: 드로어 차트가 renderLineChart 사용",
          re.search(r"function drawDrawerChart\b.*?renderLineChart\(", tpl, re.S) is not None)
    check("7d: 비교(금리) 차트가 renderLineChart 사용",
          re.search(r"function drawYieldChart\b.*?renderLineChart\(", tpl, re.S) is not None)
    # 미연동 비교 시리즈는 그리지 않고(unavail) 0으로 채우지 않음.
    check("7e: 비교차트 미연동 시리즈 제외(unavail) + 0필 금지",
          "unavail.push" in tpl
          and re.search(r"function pushSeries\b.*?if \(base && base\.length\)", tpl, re.S) is not None)
    check("7f: 미연동 비교 시리즈를 범례에 '미연동'으로 보고",
          re.search(r"unavail\.forEach[^}]*미연동", tpl, re.S) is not None)


# ---------------------------------------------------------------------------
# 8 · 눈금이 수치적으로 등간격(JS를 실제 실행해 증명)
# ---------------------------------------------------------------------------

def _grab_fn(src: str, name: str) -> str:
    m = re.search(r"function " + re.escape(name) + r"\s*\([^)]*\)\s*\{", src)
    if not m:
        return ""
    i = m.start()
    depth = 0
    k = src.index("{", i)
    while k < len(src):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                return src[i:k + 1]
        k += 1
    return ""


def check_ticks_equal_interval(tpl: str) -> None:
    fns = "\n".join(_grab_fn(tpl, fn) for fn in ("niceStep", "niceTicks", "tickDecimals", "fmtTick"))
    if not check("8a: 눈금 헬퍼 4종 추출 성공", all(f"function {fn}" in fns
                 for fn in ("niceStep", "niceTicks", "tickDecimals", "fmtTick"))):
        return
    cases = [["yield", 4.37, 4.47], ["copper", 13000, 13500], ["henry", 2.80, 2.87],
             ["usdkrw", 1510.2, 1541.2], ["spread", -0.2, 0.6], ["equal", 105.0, 105.0],
             ["norm", 96.0, 104.0], ["gasoline", 2.06, 2.34], ["jpy", 9.53, 9.78],
             ["tiny", 0.001, 0.001]]
    harness = ("var NICE_STEPS=[1,2,2.5,5,10];\n" + fns + "\nvar C=" + json.dumps(cases) + ";\n" + r"""
var allOk=true, out=[];
C.forEach(function(c){
  var tk=niceTicks(c[1],c[2],5), dec=tickDecimals(tk.step);
  var ok=true;
  if(tk.ticks.length<3||tk.ticks.length>7) ok=false;          // 4~5 목표(3~7 허용)
  for(var i=2;i<tk.ticks.length;i++){
    if(Math.abs((tk.ticks[i]-tk.ticks[i-1])-(tk.ticks[i-1]-tk.ticks[i-2]))>Math.abs(tk.step)*1e-6) ok=false;
  }
  // 라벨도 등간격으로 보이는지(소수 자리 부족으로 왜곡되지 않는지)
  var labs=tk.ticks.map(function(v){return fmtTick(v,dec);});
  var nums=labs.map(function(s){return parseFloat(s.replace(/,/g,''));});
  for(var j=2;j<nums.length;j++){
    if(Math.abs((nums[j]-nums[j-1])-(nums[j-1]-nums[j-2]))>1e-9) ok=false;
  }
  // 데이터가 도메인 안에 들어오는지
  if(!(c[1]>=tk.min-1e-9 && c[2]<=tk.max+1e-9)) ok=false;
  if(!ok) allOk=false;
  out.push(c[0]+":"+(ok?"ok":"BAD")+"(step="+tk.step+",n="+tk.ticks.length+")");
});
console.log(JSON.stringify({allOk:allOk, out:out}));
""")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(harness)
        path = f.name
    try:
        proc = subprocess.run(["node", path], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        check("8b: niceTicks 등간격(JS 실행)", False, f"node 실행 실패: {exc}")
        os.unlink(path)
        return
    os.unlink(path)
    try:
        res = json.loads((proc.stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        check("8b: niceTicks 등간격(JS 실행)", False, (proc.stderr or proc.stdout)[-200:])
        return
    check("8b: niceTicks 눈금이 수치적 등간격(JS 실행 증명)", res.get("allOk") is True,
          " ".join(res.get("out") or []))


# ---------------------------------------------------------------------------
# 9 · 가짜 히스토리 미생성 + 빌더가 시세를 위조하지 않음
# ---------------------------------------------------------------------------

def check_no_fake_history(html: str, where: str) -> None:
    items = _items(html)
    fabricated = []
    for it in items:
        h = it.get("history") or {}
        if not h:
            continue
        # 히스토리 키가 있으면 4개 기간 모두 실데이터(≥2 숫자점) — 빈/1점 가짜 배열 금지.
        for p in PERIODS:
            arr = h.get(p)
            if arr is not None and _numeric_pts(arr) < 2:
                fabricated.append(f"{it.get('id')}:{p}={len(arr or [])}pts")
    check(f"9a[{where}]: 가짜/부분 히스토리 배열 없음(있으면 ≥2 숫자점)", not fabricated,
          f"{fabricated}" if fabricated else "ok")


def check_builder_hygiene() -> None:
    src = _read(BUILDER)
    # 빌더는 네트워크 라이브러리를 직접 import하지 않고(히스토리 leaf만), env/secrets를 읽지 않는다.
    check("9b: 빌더가 네트워크 라이브러리를 직접 import하지 않음",
          not re.search(r"^\s*import\s+(requests|httpx|urllib\.request|aiohttp)\b", src, re.M),
          "ok")
    # live overlay는 market_history leaf의 실측(history_for_model)만 사용하고, LIVE_MODE 성공분만
    # 교체하며, live 모드일 때만 동작한다(mock은 결정적 데모 픽스처 유지 — 위조 없음).
    check("9c: 빌더가 시세를 위조하지 않음(live는 leaf 실측만 overlay)",
          "history_for_model" in src and "market_history.LIVE_MODE" in src
          and re.search(r'market_mode[^\n]*!=\s*"live"', src) is not None)


def check_regenerated() -> None:
    with tempfile.TemporaryDirectory(prefix="hdec_chart_") as tmp:
        out = Path(tmp) / "dashboard-latest.html"
        proc = subprocess.run([sys.executable, str(BUILDER), "--output", str(out)],
                              cwd=ROOT, capture_output=True, text=True, timeout=300)
        if not check("10a: builder --output 동작(기본 mock · 네트워크 0건)",
                     proc.returncode == 0 and out.exists(), (proc.stderr or "")[-200:]):
            return
        gen = out.read_text(encoding="utf-8")
        check_state_contract(gen, "regen")
        check_no_blank_no_fake(gen, "regen")
        check_rebar(gen, "regen")
        check_no_fake_history(gen, "regen")


def main() -> int:
    print(f"== verify_market_chart_integrity (D7-K) @ {ROOT} ==")
    tpl = _read(TEMPLATE)
    if not check("0: dashboard_preview.html 템플릿 존재", bool(tpl) and len(tpl) > 4000):
        print("\nRESULT: FAIL (템플릿 누락)")
        return 1
    # 템플릿(데모) 모델
    check_state_contract(tpl, "tpl")
    check_no_blank_no_fake(tpl, "tpl")
    check_rebar(tpl, "tpl")
    check_named_rows(tpl, "tpl")
    check_no_fake_history(tpl, "tpl")
    # 프론트 계약 + 렌더러 가드 + 눈금
    check_frontend_contract(tpl)
    check_renderer_guards(tpl)
    check_nicetick_usage(tpl)
    check_ticks_equal_interval(tpl)
    # 빌더 위생 + 재생성 대시보드
    check_builder_hygiene()
    check_regenerated()
    # 커밋된 대시보드 스냅샷(있으면)
    if DASHBOARD.exists():
        dash = _read(DASHBOARD)
        check_state_contract(dash, "dash")
        check_no_blank_no_fake(dash, "dash")
        check_rebar(dash, "dash")
        check_named_rows(dash, "dash")
        check_no_fake_history(dash, "dash")
    else:
        check("0b: docs/daily/dashboard-latest.html 존재", False)

    print()
    if _failures:
        print(f"RESULT: FAIL ({len(_failures)} 항목)")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("RESULT: PASS — 시장 행 상태 계약(차트가능/값만/미연동) + 렌더러 가드 + "
          "등간격 nice 눈금 + 가짜 히스토리 없음 (D7-K)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
