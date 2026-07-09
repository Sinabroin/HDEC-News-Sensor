#!/usr/bin/env python3
"""D7-F verifier — market period controls do REAL per-period history (no fake resample).

Runs fully offline (no network, DB, secrets, or send). D7-E disabled the 1주/1개월/3개월/1년
buttons as an honesty workaround because the underlying series was one shared base resampled
per window (3개월·1년 rendered identically). D7-F replaces that the honest way:

  · the period buttons (1주/1개월/3개월/1년) are ENABLED again and each shows a genuinely
    DIFFERENT window — supported headline items carry per-period `history` arrays
    (`history: {"1w":[...], "1m":[...], "3m":[...], "1y":[...]}`) that the frontend draws
    from directly (`item.history[period]`), NOT a `resample(sharedBase, n)`,
  · the per-period arrays are distinct (in particular 3개월 ≠ 1년) and not all identical,
  · in mock/offline the arrays are a **deterministic demo fixture** (history_data_mode
    mock_demo) sourced from app/market_history; with `--market-mode live` the builder
    replaces them with public delayed quotes (history_data_mode delayed_market),
  · items with NO free source (US 2Y / KR 10Y) carry no history → non-clickable, labelled
    히스토리 미연동, and openDrawer refuses them (no blank chart drawer),
  · market values never claim to be current/live (현재 체결값 is always negated), and no
    API keys/secrets are embedded.

This checks the template, the per-period history model, the app/market_history provider
(demo determinism + distinctness + live capability), and the regenerated mock dashboard.
"""

import re
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TEMPLATE = ROOT / "templates" / "dashboard_preview.html"
BUILDER = ROOT / "scripts" / "build_static_dashboard.py"
DASHBOARD = ROOT / "docs" / "daily" / "dashboard-latest.html"

from app import market_history as mh  # noqa: E402

# 기간 히스토리가 연동돼야 하는 headline 종목 (task가 명시한 최소집합 + 깔끔한 Yahoo 심볼).
SUPPORTED = ("usdkrw", "jpykrw", "wti", "copper", "us_10y")
# 공개 무료 소스가 없어 히스토리를 연동하지 않는(소스 필요) 종목.
# D7-AE-RC3: us_2y는 FRED DGS2(일별 CSV)로 기간 히스토리가 연동됐다 — 소스필요는 kr_10y만.
# (mock 템플릿의 us_2y는 여전히 unavailable·히스토리 없음 — 아래 4a-us2y가 별도 검증)
SOURCE_NEEDED = ("kr_10y",)
PERIODS = ("1w", "1m", "3m", "1y")

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


def _items(html: str) -> dict:
    return {it.get("id"): it for it in (_model(html).get("market_items") or [])}


def _seg_block(html: str, seg_id: str) -> str:
    m = re.search(r'id="' + re.escape(seg_id) + r'".*?</div>', html, re.S)
    return m.group(0) if m else ""


def _period_buttons(block: str) -> list:
    return re.findall(r"<button[^>]*\bdata-period=\"[^\"]*\"[^>]*>", block)


def _period_handler(tpl: str, varname: str, seg_id: str):
    """기간 세그의 click 핸들러 본문을 추출 (period 전환 핸들러만 — 칩 핸들러와 분리)."""
    m = re.search(r"var " + varname + r" = el\(\"" + re.escape(seg_id)
                  + r"\"\);.*?" + varname + r"\.addEventListener\(\"click\", "
                  r"function \(ev\) \{(.*?)\}\);", tpl, re.S)
    return m.group(1) if m else None


def _all_enabled(block: str) -> bool:
    btns = _period_buttons(block)
    return len(btns) == 4 and all("disabled" not in b for b in btns)


# ---------------------------------------------------------------------------
# 1 · 기간 컨트롤 = 활성(다시 동작) — 비활성 워크어라운드 제거
# ---------------------------------------------------------------------------

def check_period_controls_enabled(tpl: str) -> None:
    check("1a: 금리 차트 기간 세그(yieldPeriodSeg) 존재", 'id="yieldPeriodSeg"' in tpl)
    check("1b: 시장 드로어 기간 세그(dwPeriodSeg) 존재", 'id="dwPeriodSeg"' in tpl)
    for lab in ("1주", "1개월", "3개월", "1년"):
        check(f"1c: 기간 버튼 라벨 '{lab}' 존재", lab in tpl)
    yseg = _seg_block(tpl, "yieldPeriodSeg")
    dseg = _seg_block(tpl, "dwPeriodSeg")
    check("1d: 금리 차트 기간 버튼 4개 모두 활성(disabled 없음)", _all_enabled(yseg))
    check("1d: 드로어 기간 버튼 4개 모두 활성(disabled 없음)", _all_enabled(dseg))
    check("1e: 비활성 세그 표기(seg disabled/aria-disabled) 제거",
          'class="seg disabled" id="yieldPeriodSeg"' not in tpl
          and 'class="seg disabled" id="dwPeriodSeg"' not in tpl
          and 'id="yieldPeriodSeg" aria-disabled' not in tpl
          and 'id="dwPeriodSeg" aria-disabled' not in tpl)
    # 기간 클릭 핸들러가 더 이상 disabled로 가드하지 않는다(실제 기간 전환 동작).
    # 칩(만기/국가) 핸들러의 정당한 b.disabled 가드와 구분해 period 핸들러만 본다.
    yield_h = _period_handler(tpl, "pseg", "yieldPeriodSeg")
    drawer_h = _period_handler(tpl, "dpseg", "dwPeriodSeg")
    check("1f: 기간 클릭 핸들러가 disabled로 막지 않음(실제 전환)",
          yield_h is not None and drawer_h is not None
          and "b.disabled" not in yield_h and "b.disabled" not in drawer_h)
    # 비활성/'동일 데모 추세' 류 옛 문구 제거.
    check("1g: 옛 '기간 버튼 비활성/3개월·1년 동일' 문구 제거",
          "기간 버튼은 비활성" not in tpl and "3개월·1년 동일" not in tpl
          and "최근 추세 데모" not in tpl)


# ---------------------------------------------------------------------------
# 2 · 프론트가 item.history[period]를 그린다 (resample 공유베이스 금지)
# ---------------------------------------------------------------------------

def check_uses_real_period_data(tpl: str) -> None:
    check("2a: 드로어가 current.history[period]를 그린다",
          "current.history[period]" in tpl)
    check("2b: isClickable이 it.history[\"1m\"] 연동을 요구",
          "function isClickable" in tpl and 'it.history && it.history["1m"]' in tpl)
    # resample(공유베이스를 길이만 바꿔 가짜 기간 전환) 함수/호출 제거.
    check("2c: resample() 함수/호출 제거(가짜 기간 전환 없음)",
          "function resample" not in tpl and "resample(" not in tpl)
    # 기간 라벨은 배열 길이에 맞춘 상대 창 라벨 — 가짜 per-period 가격 배열(PERIODS) 없음.
    check("2d: relLabels(기간 창 라벨) 사용", "function relLabels" in tpl)
    check("2d2: 옛 정적 PERIODS 날짜배열(공유 베이스 라벨) 제거", "var PERIODS" not in tpl)
    # 금리 비교 차트도 공유베이스를 resample하지 않고 기간 창(window)을 쓴다.
    check("2e: 금리 차트가 periodWindow(기간 창)로 전환", "function periodWindow" in tpl)


# ---------------------------------------------------------------------------
# 3 · 지원 종목은 4개 기간 히스토리(서로 다른 창)를 갖는다 (3개월 ≠ 1년)
# ---------------------------------------------------------------------------

def check_supported_history(tpl: str) -> None:
    items = _items(tpl)
    distinct_any = False
    for sid in SUPPORTED:
        it = items.get(sid) or {}
        hist = it.get("history") or {}
        keys_ok = all(p in hist for p in PERIODS)
        nonempty = keys_ok and all(len(hist.get(p) or []) >= 2 for p in PERIODS)
        check(f"3a:{sid} history 4개 기간(1w/1m/3m/1y) 비어있지 않음", nonempty,
              "" if nonempty else f"keys={sorted(hist)}")
        if nonempty:
            series = [tuple(hist[p]) for p in PERIODS]
            all_distinct = len(set(series)) == len(series)
            month_quarter = hist["3m"] != hist["1y"]
            check(f"3b:{sid} 1w/1m/3m/1y 모두 서로 다른 배열", all_distinct)
            check(f"3c:{sid} 3개월 ≠ 1년 (별도 구간)", month_quarter)
            if all_distinct and month_quarter:
                distinct_any = True
        # 정직성: 데모 모드는 mock_demo로 표기(연동 위장 금지) + 출처 라벨 존재.
        check(f"3d:{sid} history_data_mode/source 정직 표기",
              it.get("history_data_mode") in ("mock_demo", "delayed_market")
              and bool(it.get("history_source")))
    check("3e: 최소 1개 지원 종목이 3개월·1년 별도 구간을 가짐", distinct_any)


# ---------------------------------------------------------------------------
# 4 · 미연동(소스 필요) 종목은 히스토리 없음 + 빈 차트 미오픈
# ---------------------------------------------------------------------------

def check_source_needed(tpl: str) -> None:
    items = _items(tpl)
    for sid in SOURCE_NEEDED:
        it = items.get(sid) or {}
        check(f"4a:{sid} 히스토리 미연동(history 없음 · value=null · unavailable)",
              not it.get("history") and it.get("value") is None
              and it.get("data_mode") == "unavailable",
              f"value={it.get('value')} mode={it.get('data_mode')} has_history={bool(it.get('history'))}")
    # openDrawer가 히스토리 없음을 거부(빈 차트 방지) + 비클릭 행에 '히스토리 미연동' 라벨.
    check("4b: openDrawer가 히스토리 없음을 거부(빈 차트 방지)",
          "function openDrawer" in tpl
          and re.search(r'current\.history && current\.history\["1m"\]', tpl) is not None)
    check("4c: 비클릭 행에 '히스토리 미연동'/'차트 없음' 표기",
          "히스토리 미연동" in tpl and "차트 없음" in tpl)
    # clickhint가 미연동 종목(KR 10Y 등)을 비오픈으로 경고 + us_2y FRED 연동을 명시(D7-AE-RC3).
    check("4d: clickhint가 KR 10Y 비오픈(소스 필요) 경고 + US 2Y FRED 연동 명시",
          "KR 10Y" in tpl and "열지 않습니다" in tpl and "소스 필요" in tpl
          and "FRED DGS2" in tpl)
    # 템플릿(mock 데모)의 us_2y는 여전히 정직한 unavailable(값·히스토리 없음)이다 —
    # FRED 연동은 live 빌드에서만 붙는다(mock에 가짜 데모 차트를 만들지 않는다).
    us2 = _items(tpl).get("us_2y") or {}
    check("4a-us2y: 템플릿 us_2y는 unavailable(값·히스토리 없음 — live에서만 FRED 연동)",
          not us2.get("history") and us2.get("value") is None
          and us2.get("data_mode") == "unavailable")
    # 시장 행이 가용성 기반 클릭(전부 true 아님).
    check("4e: 시장 행이 가용성 기반 클릭(mrowHtml(it, isClickable(it)))",
          "mrowHtml(it, isClickable(it))" in tpl and "mrowHtml(it, true)" not in tpl)


# ---------------------------------------------------------------------------
# 5 · 시장 값은 현재/실시간 미주장 + 비밀값 없음
# ---------------------------------------------------------------------------

def check_not_live_no_secrets(tpl: str) -> None:
    check("5a: '현재 체결값 아님' 정직 표기 존재",
          "현재 체결값이 아닙니다" in tpl or "현재 체결값 아님" in tpl)
    bad = []
    for mm in re.finditer(r"현재 체결값", tpl):
        tail = tpl[mm.end():mm.end() + 14]
        if not any(neg in tail for neg in ("아님", "아니", "아닙", "않", "없")):
            bad.append(tpl[mm.start():mm.end() + 14])
    check("5b: '현재 체결값'이 live로 주장되지 않음(항상 부정 동반)",
          not bad, f"미부정 사례: {bad[:2]}" if bad else "ok")
    for lab in ("지연", "미연동"):
        check(f"5c: 시장 정직 라벨 '{lab}' 유지", lab in tpl)
    # API key/token credential-shaped 패턴만 감지한다. 일반 설명 단어 'secret'은 오탐에서 제외.
    secret_hits = re.findall(r"(?i)\b(api[_-]?key|bearer|access[_-]?token|client[_-]?secret)\b",
                             tpl)
    check("5d: API key/token credential pattern 미임베드", not secret_hits,
          f"의심 토큰: {set(secret_hits)}" if secret_hits else "ok")


# ---------------------------------------------------------------------------
# 6 · provider(app/market_history) — 결정적 데모 + 기간 distinct + live 능력
# ---------------------------------------------------------------------------

def check_provider() -> None:
    sup = set(mh.supported_ids())
    check("6a: provider가 headline 종목을 모두 지원", set(SUPPORTED) <= sup,
          f"누락: {set(SUPPORTED) - sup}")
    check("6b: KR 10Y는 provider 비지원(소스 필요) · us_2y는 Yahoo 카탈로그 밖(FRED 전용)",
          all(not mh.is_supported(s) for s in SOURCE_NEEDED)
          and not mh.is_supported("us_2y")
          and mh._FRED_HISTORY.get("us_2y", ("",))[0] == "DGS2")
    # 결정적: 같은 호출 두 번이 동일.
    det = all(mh.demo_history(s) == mh.demo_history(s) for s in SUPPORTED)
    check("6c: demo_history 결정적(now()/random 미사용)", det)
    # 기간 distinct(3개월 ≠ 1년) + 끝점=현재값.
    dist = all(mh.demo_distinct_ok(s) for s in SUPPORTED)
    check("6d: demo_history 기간 distinct(3개월 ≠ 1년)", dist)
    # 템플릿 임베드가 provider demo와 정확히 일치(드리프트 방지).
    items = _items(_read(TEMPLATE))
    sync = True
    for s in SUPPORTED:
        if (items.get(s) or {}).get("history") != mh.demo_history(s):
            sync = False
            break
    check("6e: 템플릿 임베드 history == market_history.demo_history (동기화)", sync)
    # live 수집 능력 존재(호출하지 않음 — 오프라인).
    check("6f: provider에 live 수집 함수(fetch_live_history) 존재",
          callable(getattr(mh, "fetch_live_history", None))
          and mh.LIVE_MODE == "delayed_market")
    # demo entry는 mock_demo로 정직 표기(live 위장 금지).
    check("6g: demo_entry가 mock_demo로 정직 표기",
          mh.demo_entry("usdkrw").get("history_data_mode") == "mock_demo")


# ---------------------------------------------------------------------------
# 7 · 재생성된 mock 대시보드도 활성 기간 + 기간 히스토리 + 정직 라벨
# ---------------------------------------------------------------------------

def check_regenerated() -> None:
    with tempfile.TemporaryDirectory(prefix="hdec_period_") as tmp:
        out = Path(tmp) / "dashboard-latest.html"
        proc = subprocess.run([sys.executable, str(BUILDER), "--output", str(out)],
                              cwd=ROOT, capture_output=True, text=True, timeout=300)
        if not check("7a: builder --output 동작(기본 mock · 네트워크 0건)",
                     proc.returncode == 0 and out.exists(), (proc.stderr or "")[-200:]):
            return
        gen = out.read_text(encoding="utf-8")
        check("7b: 재생성 대시보드 기간 버튼 활성 유지",
              _all_enabled(_seg_block(gen, "yieldPeriodSeg"))
              and _all_enabled(_seg_block(gen, "dwPeriodSeg")))
        items = _items(gen)
        good = 0
        for sid in SUPPORTED:
            hist = (items.get(sid) or {}).get("history") or {}
            if all(len(hist.get(p) or []) >= 2 for p in PERIODS) and hist.get("3m") != hist.get("1y"):
                good += 1
        check("7c: 재생성 대시보드 지원 종목 기간 히스토리(3개월≠1년) 유지",
              good == len(SUPPORTED), f"{good}/{len(SUPPORTED)}")
        check("7d: 재생성 대시보드가 현재 체결값을 주장하지 않음",
              "현재 체결값이 아닙니다" in gen or "현재 체결값 아님" in gen)
        check("7e: 재생성 대시보드 mock에서 데모 픽스처(mock_demo) 표기",
              '"history_data_mode": "mock_demo"' in gen or '"history_data_mode":"mock_demo"' in gen)
        gitems = items
        check("7f: 재생성 대시보드도 US 2Y/KR 10Y 히스토리 미연동",
              not (gitems.get("us_2y") or {}).get("history")
              and not (gitems.get("kr_10y") or {}).get("history"))
    if DASHBOARD.exists():
        com = _items(_read(DASHBOARD))
        ok = all((com.get(s) or {}).get("history") for s in SUPPORTED)
        check("7g: 커밋된 dashboard-latest.html에 지원 종목 기간 히스토리 존재", ok)


def main() -> int:
    print(f"== verify_market_period_honesty (D7-F) @ {ROOT} ==")
    tpl = _read(TEMPLATE)
    if not check("0: dashboard_preview.html 템플릿 존재", bool(tpl) and len(tpl) > 4000):
        print("\nRESULT: FAIL (템플릿 누락)")
        return 1
    check_period_controls_enabled(tpl)
    check_uses_real_period_data(tpl)
    check_supported_history(tpl)
    check_source_needed(tpl)
    check_not_live_no_secrets(tpl)
    check_provider()
    check_regenerated()

    print()
    if _failures:
        print(f"RESULT: FAIL ({len(_failures)} 항목)")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("RESULT: PASS — 시장 기간 컨트롤 실동작(기간별 실데이터/데모 distinct) "
          "+ 미연동 소스필요 비클릭 확인 (D7-F)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
