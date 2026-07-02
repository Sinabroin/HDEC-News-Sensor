#!/usr/bin/env python3
"""D7-AE verifier — 명일 정오 시공 리스크 (weather live adapter).

사용자 QA: "'명일 정오 시공 리스크'는 UI만 있고 실제 기상 데이터가 연동되지 않았다."
D7-AE는 app/weather_risk leaf(Open-Meteo 공개 예보 · 무키)를 신설해 권역 대표점의
D+1 12:00(현지) 예보 + rule-based 시공 리스크 등급을 모델(weather_*)로 주입한다.

이 verifier는 완전 오프라인(네트워크 0건)으로 아래 계약을 잠근다:

  1. rule 엔진 — RISK_RULES와 1:1 결정적 등급(우천 60/80% · 돌풍 10/15m/s 크레인
     기준 · 폭염 33°C · 한파 -12°C · 전무값=확인 필요). 가짜 등급/값 생성 없음.
  2. mock(기본) — 네트워크 0건 · unavailable · rows=[] · 사유 명시(데모 기상값 금지).
  3. live 실패 — fetcher 주입(None/예외/빈 응답)이 전부 unavailable('기상 데이터
     미수신')로 강등된다. mock 숫자 fallback이 생기면 FAIL(fake-fallback catcher).
  4. live 성공(주입 fixture) — 결정적 값/등급, D+1 12:00 현지 시점(now 주입 시
     KST tz 이중 가산 함정 검사 포함), 출처/수집시각 KST.
  5. 권역 부분 실패 — 해당 행만 '확인 필요', 나머지는 실측 유지(mode=live).
  6. 경계 — 기상 네트워크는 leaf만 소유(빌더 소스에 urllib/os.environ 0건),
     빌더는 CLI --weather-mode로만 모드를 받고 snapshot_for_model을 호출한다.
  7. 워크플로 — 두 라이브 게시 경로 모두 --weather-mode live로 빌드한다.
  8. 템플릿 — 실측 렌더(renderSiteWeather)와 정직 상태 문자열('기상 데이터 미수신',
     정적 '미연동' 유지), 기존 계약 문자열(명일 정오 시공 리스크 · 명일 정오 12:00 ·
     기상 데이터 소스 미연동) 보존, 소스/기준시각/target 표기.
  9. 커밋 산출물 — weather_data_mode ∈ {live, unavailable}. live면 실측 행+출처,
     unavailable이면 rows=[](가짜 숫자 0건).
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import weather_risk as wx  # noqa: E402

BUILDER = ROOT / "scripts" / "build_static_dashboard.py"
TEMPLATE = ROOT / "templates" / "dashboard_preview.html"
DASHBOARD = ROOT / "docs" / "daily" / "dashboard-latest.html"
WORKFLOWS = (ROOT / ".github" / "workflows" / "scheduled-live-refresh.yml",
             ROOT / ".github" / "workflows" / "telegram-notify.yml")

_failures: list = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    line = f"[{'PASS' if ok else 'FAIL'}] {name}"
    if detail:
        line += f" — {detail}"
    print(line)
    if not ok:
        _failures.append(name)
    return ok


def info(msg: str) -> None:
    print(f"[INFO] {msg}")


def _model(html: str) -> dict:
    m = re.search(r'<script type="application/json" id="preview-model">(.*?)</script>',
                  html, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except ValueError:
        return {}


# ---------------------------------------------------------------------------
# 1 · rule 엔진 (결정적)
# ---------------------------------------------------------------------------

def check_rule_engine() -> None:
    cases = [
        ("우천 주의(60%·1mm)", dict(precip_prob=70, precip_mm=2, wind_ms=3, gust_ms=5,
                                  temp_c=20), "주의", ["rain_watch"]),
        ("우천 높음(80%·5mm)", dict(precip_prob=90, precip_mm=8, wind_ms=3, gust_ms=5,
                                  temp_c=20), "높음", ["rain_high"]),
        ("돌풍 15m/s = 크레인 운전중지 → 높음",
         dict(precip_prob=10, precip_mm=0, wind_ms=8, gust_ms=16, temp_c=20),
         "높음", ["wind_high"]),
        ("돌풍 10m/s = 설치·해체중지 → 주의",
         dict(precip_prob=10, precip_mm=0, wind_ms=8, gust_ms=11, temp_c=20),
         "주의", ["wind_watch"]),
        ("기온 33°C 폭염 → 주의",
         dict(precip_prob=10, precip_mm=0, wind_ms=2, gust_ms=4, temp_c=35),
         "주의", ["heat"]),
        ("기온 -12°C 한파 → 주의",
         dict(precip_prob=10, precip_mm=0, wind_ms=2, gust_ms=4, temp_c=-15),
         "주의", ["cold"]),
        ("전 항목 정상 → 낮음",
         dict(precip_prob=5, precip_mm=0, wind_ms=2, gust_ms=4, temp_c=22), "낮음", []),
        ("전무값 → 확인 필요(가짜 등급 금지)", {}, "확인 필요", []),
        ("돌풍 결측 시 풍속으로 보수 평가",
         dict(precip_prob=10, precip_mm=0, wind_ms=12, gust_ms=None, temp_c=20),
         "주의", ["wind_watch"]),
    ]
    for name, kw, grade, reasons in cases:
        v = wx.grade_construction_risk(**kw)
        check(f"1: {name}", v["grade"] == grade and v["reasons"] == reasons, str(v))
    ids = {r["id"] for r in wx.RISK_RULES}
    check("1z: 발동 규칙 id가 전부 RISK_RULES에 존재(UI 추적 가능)",
          {"rain_watch", "rain_high", "wind_watch", "wind_high", "heat", "cold"} <= ids)


# ---------------------------------------------------------------------------
# 2 · mock — 네트워크 0건 · unavailable · 데모 기상값 금지
# ---------------------------------------------------------------------------

def check_mock_mode() -> None:
    def _no_network():
        raise AssertionError("mock 모드에서 fetcher가 호출되면 안 된다")

    snap = wx.snapshot_for_model(mode="mock", fetcher=_no_network)
    check("2a: mock → unavailable + live_attempted=False",
          snap["weather_data_mode"] == "unavailable"
          and snap["weather_live_attempted"] is False)
    check("2b: mock → rows=[] · 출처/시각 빈 값(데모 기상값 0건)",
          snap["weather_rows"] == [] and not snap["weather_source"]
          and not snap["weather_updated_at"])
    check("2c: mock → unavailable 사유 명시", bool(snap["weather_unavailable_reason"]))
    check("2d: 규칙 표는 항상 동봉(UI 추적 가능)",
          len(snap["weather_risk_rules"]) == len(wx.RISK_RULES))


# ---------------------------------------------------------------------------
# 3 · live 실패 — fake-fallback catcher
# ---------------------------------------------------------------------------

def check_live_failure() -> None:
    for label, fetcher in (
            ("None 응답", lambda: None),
            ("빈 리스트", lambda: []),
            ("예외", lambda: (_ for _ in ()).throw(RuntimeError("down"))),
            ("dict 아님", lambda: "oops")):
        snap = wx.snapshot_for_model(mode="live", fetcher=fetcher)
        ok = (snap["weather_data_mode"] == "unavailable"
              and snap["weather_live_attempted"] is True
              and snap["weather_rows"] == []
              and "미수신" in snap["weather_unavailable_reason"])
        check(f"3: live 실패({label}) → 미수신(가짜 fallback 0건)", ok,
              f"mode={snap['weather_data_mode']} rows={len(snap['weather_rows'])}")


# ---------------------------------------------------------------------------
# 4 · live 성공(주입 fixture) — 결정적 값/시점/등급
# ---------------------------------------------------------------------------

_NOW_KST = "2026-07-02T15:00:00+09:00"   # KST 주입 — tz 이중 가산 함정 검사용


def _fixture():
    out = []
    for i, _r in enumerate(wx.REGIONS):
        out.append({"hourly": {
            "time": ["2026-07-03T12:00"],
            "temperature_2m": [22.0], "precipitation_probability": [65 if i == 0 else 10],
            "precipitation": [2.0 if i == 0 else 0.0], "wind_speed_10m": [4.0],
            "wind_gusts_10m": [16.0 if i == 1 else 6.0]}})
    return out


def check_live_fixture() -> None:
    snap = wx.snapshot_for_model(mode="live", now=_NOW_KST, fetcher=_fixture)
    check("4a: live 성공 → mode=live + 출처 라벨",
          snap["weather_data_mode"] == "live"
          and snap["weather_source"] == wx.SOURCE_LABEL)
    check("4b: KST now 주입 → D+1 12:00 (tz 이중 가산 없음)",
          snap["weather_target_time"] == "2026-07-03T12:00",
          snap["weather_target_time"])
    check("4c: 수집시각 KST 벽시계 그대로",
          snap["weather_updated_at"] == "2026-07-02 15:00", snap["weather_updated_at"])
    rows = snap["weather_rows"]
    check("4d: 권역 행 수 = REGIONS", len(rows) == len(wx.REGIONS))
    grades = [r["risk_grade"] for r in rows]
    check("4e: fixture 결정적 등급(우천 주의/돌풍 높음/낮음…)",
          grades == ["주의", "높음", "낮음", "낮음", "낮음"], str(grades))
    check("4f: 행 값이 fixture 실수치 그대로(생성값 아님)",
          rows[0]["precip_prob"] == 65 and rows[1]["gust_ms"] == 16.0)
    check("4g: 발동 규칙 라벨 동봉(risk_labels)",
          rows[1]["risk_labels"] == ["강풍 높음"], str(rows[1]["risk_labels"]))
    check("4h: 전 행 target_local = 명일 정오(현지)",
          all(r["target_local"] == "2026-07-03T12:00" for r in rows))
    check("4i: 특보는 행마다 '수동 확인'(가짜 '특보 없음' 단정 금지)",
          all(r["advisory"] == "수동 확인" for r in rows))


def check_partial_failure() -> None:
    def fetch_partial():
        data = _fixture()
        data[2] = None                      # 영남권만 응답 누락
        return data

    snap = wx.snapshot_for_model(mode="live", now=_NOW_KST, fetcher=fetch_partial)
    rows = snap["weather_rows"]
    check("5a: 부분 실패 → mode는 live 유지", snap["weather_data_mode"] == "live")
    bad = rows[2]
    check("5b: 실패 권역만 '확인 필요' + 값 없음",
          bad["row_status"] == "unavailable" and bad["risk_grade"] == "확인 필요"
          and bad["precip_prob"] is None)
    check("5c: 나머지 권역은 실측 유지", rows[0]["precip_prob"] == 65)


# ---------------------------------------------------------------------------
# 6 · 경계 — leaf만 네트워크 · 빌더는 CLI만
# ---------------------------------------------------------------------------

def check_boundaries() -> None:
    bsrc = BUILDER.read_text(encoding="utf-8")
    check("6a: 빌더 소스 os.environ 0건(모드는 CLI)", "os.environ" not in bsrc)
    check("6b: 빌더 소스 네트워크 모듈 직접 import 0건(leaf 소유)",
          not re.search(r"^\s*(import|from)\s+(urllib|requests|httpx|socket)\b", bsrc, re.M))
    check("6c: 빌더 --weather-mode CLI + snapshot_for_model 호출",
          "--weather-mode" in bsrc and "weather_risk.snapshot_for_model" in bsrc)
    wsrc = wx.__doc__ or ""
    check("6d: leaf 정직성 계약 문서화(데모 기상값 금지)", "가짜" in wsrc or "데모" in wsrc)


def check_workflows() -> None:
    for wf in WORKFLOWS:
        src = wf.read_text(encoding="utf-8")
        line_ok = any("build_static_dashboard.py" in ln and "--weather-mode live" in ln
                      for ln in src.splitlines())
        check(f"7: {wf.name} 대시보드 빌드가 --weather-mode live", line_ok)


# ---------------------------------------------------------------------------
# 8 · 템플릿 — 실측 렌더 + 정직 상태 + 기존 계약 문자열 보존
# ---------------------------------------------------------------------------

def check_template() -> None:
    t = TEMPLATE.read_text(encoding="utf-8")
    check("8a: 기존 계약 문자열 보존(명일 정오 시공 리스크 · 명일 정오 12:00 · "
          "기상 데이터 소스 미연동)",
          "명일 정오 시공 리스크" in t and "명일 정오 12:00" in t
          and "기상 데이터 소스 미연동" in t)
    check("8b: 실측 렌더 함수 + 호출(renderSiteWeather)",
          "function renderSiteWeather" in t and "renderSiteWeather();" in t)
    check("8c: 모델 계약 키 사용(weather_rows/weather_data_mode/weather_live_attempted)",
          "MODEL.weather_rows" in t and "MODEL.weather_data_mode" in t
          and "MODEL.weather_live_attempted" in t)
    check("8d: live 실패 정직 카피('기상 데이터 미수신')", "기상 데이터 미수신" in t)
    check("8e: 출처·수집시각 표기(weather_source/weather_updated_at)",
          "MODEL.weather_source" in t and "MODEL.weather_updated_at" in t)
    check("8f: 등급 상태색 클래스(wx-low/wx-watch/wx-high/wx-unknown)",
          all(c in t for c in (".wx-risk.wx-low", ".wx-risk.wx-watch",
                               ".wx-risk.wx-high", "wx-unknown")))
    # 정적 placeholder 표에는 숫자 기상값이 없어야 한다(가짜 값 금지 — 전부 '미연동').
    m = re.search(r'id="wxGrid".*?</div>\s*<div class="wx-impact"', t, re.S)
    grid = m.group(0) if m else ""
    check("8g: 정적 표는 전 셀 '미연동'(데모 숫자 0건)",
          bool(grid) and "미연동" in grid
          and not re.search(r"\d+\s*(mm|m/s|%)", grid))
    check("8h: 리스크 규칙이 UI 카피에 추적 가능(제37조·33°C·-12°C)",
          "제37조" in t and "33°C" in t and "-12°C" in t)


# ---------------------------------------------------------------------------
# 9 · 커밋 산출물 — live 실측 또는 정직 unavailable (가짜 숫자 0건)
# ---------------------------------------------------------------------------

def check_committed() -> None:
    if not DASHBOARD.exists():
        info("커밋 대시보드 없음 — SKIP")
        return
    model = _model(DASHBOARD.read_text(encoding="utf-8"))
    mode = model.get("weather_data_mode")
    if not check("9a: 커밋 모델에 weather_data_mode ∈ {live, unavailable}",
                 mode in ("live", "unavailable"), f"mode={mode!r}"):
        return
    rows = model.get("weather_rows") or []
    if mode == "live":
        ok_rows = [r for r in rows if r.get("row_status") == "ok"]
        check("9b: live → 실측 행 ≥1 + 출처/수집시각/target 표기",
              bool(ok_rows) and bool(model.get("weather_source"))
              and bool(model.get("weather_updated_at"))
              and bool(model.get("weather_target_time")),
              f"ok_rows={len(ok_rows)}")
        check("9c: live 실측 행은 숫자 예보값 보유(전 항목 미연동이면 실패)",
              any(r.get("precip_prob") is not None or r.get("gust_ms") is not None
                  for r in ok_rows))
    else:
        check("9b: unavailable → rows=[] (가짜 숫자 0건)", rows == [],
              f"rows={len(rows)}")
        check("9c: unavailable → 사유 명시",
              bool(model.get("weather_unavailable_reason")))


def main() -> int:
    print(f"== verify_weather_risk (D7-AE) @ {ROOT} ==")
    check_rule_engine()
    check_mock_mode()
    check_live_failure()
    check_live_fixture()
    check_partial_failure()
    check_boundaries()
    check_workflows()
    check_template()
    check_committed()
    print()
    if _failures:
        print(f"RESULT: FAIL ({len(_failures)} 항목)")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("RESULT: PASS — 명일 정오 시공 리스크: Open-Meteo 실측 · rule-based 등급 · "
          "실패=미수신(가짜 값 0건) (D7-AE)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
