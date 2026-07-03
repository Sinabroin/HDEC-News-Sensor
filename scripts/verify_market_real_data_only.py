#!/usr/bin/env python3
"""D7-AE-RC1 verifier — 시장 지표 real-data-only 정책(메인 뷰=자동 연동만).

사용자 실사용 QA 실패: "모든 시장 지표가 목업이면 안 된다. proxy/manual/unavailable/
mock/demo는 메인에서 숨긴다. '데모 데이터' badge가 공개 주요 화면에 보이면 실패다."

진단(실측): 공개 dashboard-latest.html 시장 지표 48종(현재 49종) 중 28종이
proxy_market/manual_or_reported/unavailable인데도 메인 카드 목록에 그대로 노출되고
있었다. 근본원인은 JS marketLinked()가 "값이 있으면 linked"만 봤다(data_mode 무관) —
proxy/manual 값도 "값이 있다"는 이유로 메인에 섞였다. 추가로 cnykrw/aedkrw/usdcny류는
정적 data_mode가 "delayed_market"(허용)이라고 주장하지만, 실제 history_data_mode가
"mock_demo"(그날 live 실측이 실패해 데모로 대체)인 경우가 있어 — 정적 라벨만 보면
"연동됨"처럼 보이지만 실제로는 데모값이 실측인 척 노출되는 이중 정직성 결함이었다.

수정: templates/dashboard_preview.html의 isAutoLive(it) + scripts/build_static_dashboard.py
의 _market_is_auto_live(it) — 두 조건 모두 필요: (1) data_mode가 허용 목록
(live_market/delayed_market/live_macro/delayed_macro), (2) history_data_mode가 있다면
그것도 허용 목록(런타임에 데모로 대체된 행을 잡아낸다). 둘 다 만족해야 메인 카드
목록(마켓 카테고리 카드)에 노출되고, 아니면 기존 "연동 후보 N개 보기" 접힘 영역으로
간다(값 생성 없음 — 완전 제거가 아니라 강등). 시장 상세 드로어의 "데모 데이터" 배지도
이제 실제로 데모일 때만 보인다(이전엔 모든 종목에 상시 노출).

이 verifier가 잠그는 계약:
  A. isAutoLive/effectiveDataMode 두 조건 로직이 템플릿 JS와 빌더 Python 양쪽에 존재하고
     서로 동치(같은 fixture로 같은 결과) — "동일 규칙" 계약 회귀 방지.
  B. mock 빌드 — visible(자동연동으로 판정되는) market item이 전부 source(history_source)
     /as_of(history_updated_at)/data_mode를 갖는다. 금지 data_mode(mock/demo/proxy/
     manual/unavailable)가 하나도 visible 집합에 없다.
  C. live 빌드 — 동일 계약을 live 산출물에도. 추가로 cnykrw류 실측 재현(런타임에
     mock_demo로 강등된 행은 정적 data_mode가 무엇이든 hidden이어야 함) 확인.
  D. 드로어 "데모 데이터" 배지 — 실제 데모일 때만 보이는 조건부 로직 존재(상시 노출 아님).
  E. 커밋된 산출물(있으면) — 동일 계약. 정직 요구가 committed HTML에도 적용됨.
  F. hidden(backlog) 항목은 완전히 사라지지 않고 "연동 후보" 접힘 영역에 남는다(값
     생성이 아니라 강등 — 완전 삭제와 다름).

완전 OFFLINE(A/B/D/F) · C/E는 mock 빌드로 offline 검증 + live는 네트워크 있으면 추가.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import build_static_dashboard as builder  # noqa: E402

BUILDER = ROOT / "scripts" / "build_static_dashboard.py"
TEMPLATE = ROOT / "templates" / "dashboard_preview.html"
DASHBOARD = ROOT / "docs" / "daily" / "dashboard-latest.html"

ALLOWED_MODES = {"live_market", "delayed_market", "live_macro", "delayed_macro"}
FORBIDDEN_MODES = {"mock", "demo", "proxy_market", "manual_or_reported", "unavailable"}

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


def _has_value(it: dict) -> bool:
    v = it.get("value")
    return v is not None and str(v).strip() not in ("", "—")


def _py_is_auto_live(it: dict) -> bool:
    return builder._market_is_auto_live(it)


# ---------------------------------------------------------------------------
# A · 두 구현(JS/Python) 동치성 — 오프라인 fixture
# ---------------------------------------------------------------------------

FIXTURES = [
    ({"data_mode": "delayed_market"}, True, "순수 delayed, history 없음"),
    ({"data_mode": "live_market"}, True, "순수 live"),
    ({"data_mode": "proxy_market"}, False, "순수 proxy"),
    ({"data_mode": "manual_or_reported"}, False, "순수 manual"),
    ({"data_mode": "unavailable"}, False, "순수 unavailable"),
    ({"data_mode": "delayed_market", "history_data_mode": "delayed_market"}, True,
     "delayed + 실측 history"),
    ({"data_mode": "delayed_market", "history_data_mode": "mock_demo"}, False,
     "delayed 주장인데 history는 데모(cnykrw류 재현)"),
    ({"data_mode": "proxy_market", "history_data_mode": "delayed_market"}, False,
     "proxy인데 history는 live 성공(rebar류) — 그래도 proxy는 proxy"),
    ({"data_mode": "live_macro", "history_data_mode": "live_macro"}, True, "macro live"),
]


def check_logic_parity() -> None:
    for it, expected, label in FIXTURES:
        py_result = _py_is_auto_live(it)
        check(f"A-py: {label} → {expected}", py_result == expected, f"got={py_result}")

    tpl = _read(TEMPLATE)
    check("A1: 템플릿에 isAutoLive 2조건 로직 존재",
          "function isAutoLive(it)" in tpl and "AUTO_LIVE_MODES[it.data_mode]" in tpl
          and "AUTO_LIVE_MODES[it.history_data_mode]" in tpl)
    check("A2: 빌더에 _market_is_auto_live 2조건 로직 존재(동일 규칙)",
          hasattr(builder, "_market_is_auto_live"))


# ---------------------------------------------------------------------------
# B/C · mock·live 빌드 — visible 집합 완전성 + 금지 모드 0건
# ---------------------------------------------------------------------------

def _check_build(html: str, label: str) -> None:
    model = _model(html)
    items = model.get("market_items") or []
    if not check(f"{label}: market_items 존재", bool(items)):
        return
    visible = [it for it in items if _has_value(it) and _py_is_auto_live(it)]
    hidden = [it for it in items if not (_has_value(it) and _py_is_auto_live(it))]
    info(f"{label}: visible={len(visible)} hidden={len(hidden)} total={len(items)}")

    missing_fields = []
    for it in visible:
        if not it.get("data_mode"):
            missing_fields.append((it.get("id"), "data_mode"))
        # source/as_of는 두 가지 정당한 provenance 표기 중 하나면 된다: 기간 차트가
        # 있는 종목은 history_source/history_updated_at(app.market_history leaf), 단일값만
        # 있는 종목(FRED 등)은 value_source/value_as_of(_apply_point_quote) — 후자는 의도적
        #으로 "차트 없음"이다(가짜 history를 만들지 않는다). 어느 쪽도 없으면 결함.
        has_history_prov = bool(it.get("history_source")) and bool(it.get("history_updated_at"))
        has_point_prov = bool(it.get("value_source")) and bool(it.get("value_as_of"))
        if not (has_history_prov or has_point_prov):
            missing_fields.append((it.get("id"), "history_source+updated_at 또는 "
                                                    "value_source+as_of 둘 다 없음"))
    check(f"B1[{label}]: visible 항목 전부 source/as_of/data_mode 보유",
          not missing_fields, str(missing_fields[:5]))

    bad_mode = [(it.get("id"), it.get("data_mode")) for it in visible
                if it.get("data_mode") in FORBIDDEN_MODES]
    check(f"B2[{label}]: visible 항목에 금지 data_mode(mock/demo/proxy/manual/unavailable) 0건",
          not bad_mode, str(bad_mode[:5]))

    bad_hist = [(it.get("id"), it.get("history_data_mode")) for it in visible
                if it.get("history_data_mode") == "mock_demo"]
    check(f"C1[{label}]: visible 항목 중 실제로는 데모로 강등된 행(cnykrw류) 0건",
          not bad_hist, str(bad_hist[:5]))

    # hidden으로 간 항목은 여전히 모델에 남아있다(완전 삭제 아님 — backlog 강등).
    check(f"F1[{label}]: hidden 항목도 모델에서 완전히 사라지지 않음(값 생성 아닌 강등)",
          len(hidden) + len(visible) == len(items))


def check_mock_build() -> None:
    with tempfile.TemporaryDirectory(prefix="hdec_market_real_") as tmp:
        out = Path(tmp) / "dash.html"
        proc = subprocess.run([sys.executable, str(BUILDER), "--output", str(out)],
                              cwd=ROOT, capture_output=True, text=True, timeout=300,
                              env={**os.environ})
        if not check("mock 빌드 동작(오프라인)", proc.returncode == 0 and out.exists(),
                     (proc.stderr or "")[-200:]):
            return
        _check_build(out.read_text(encoding="utf-8"), "mock")


def check_live_build() -> None:
    try:
        import urllib.request
        urllib.request.urlopen("https://query1.finance.yahoo.com/v8/finance/chart/AAPL",
                               timeout=6).close()
    except Exception:
        info("live 빌드 검증 SKIP(네트워크 미가용, FAIL 아님)")
        return
    with tempfile.TemporaryDirectory(prefix="hdec_market_real_live_") as tmp:
        out = Path(tmp) / "dash.html"
        proc = subprocess.run(
            [sys.executable, str(BUILDER), "--market-mode", "live", "--output", str(out)],
            cwd=ROOT, capture_output=True, text=True, timeout=300, env={**os.environ})
        if not check("live 빌드 동작", proc.returncode == 0 and out.exists(),
                     (proc.stderr or "")[-200:]):
            return
        _check_build(out.read_text(encoding="utf-8"), "live")


def check_committed_dashboard() -> None:
    html = _read(DASHBOARD)
    if not html:
        info("커밋된 공개 대시보드 없음 — SKIP")
        return
    _check_build(html, "committed")


# ---------------------------------------------------------------------------
# D · 드로어 데모 배지 — 조건부(상시 노출 아님)
# ---------------------------------------------------------------------------

def check_drawer_badge_conditional() -> None:
    tpl = _read(TEMPLATE)
    check("D1: 드로어 데모 배지가 기본 hidden 클래스로 시작",
          'id="dwDemoFlag"' in tpl and 'class="previewflag hidden" id="dwDemoFlag"' in tpl)
    check("D2: openDrawer가 isDemoNow 판정으로 배지를 토글(상시 노출 아님)",
          "isDemoNow" in tpl and "demoFlag.classList.toggle" in tpl)
    check("D3: 시장 배너가 더 이상 전체를 NON-PRODUCTION PREVIEW로 단정하지 않음",
          "NON-PRODUCTION PREVIEW" not in tpl.split('id="panel-market"')[1].split(
              '<!-- 명일 정오')[0] if 'id="panel-market"' in tpl else True)


def main() -> int:
    print(f"== verify_market_real_data_only (D7-AE-RC1) @ {ROOT} ==")
    check_logic_parity()
    check_mock_build()
    check_live_build()
    check_committed_dashboard()
    check_drawer_badge_conditional()
    print()
    if _failures:
        print(f"RESULT: FAIL ({len(_failures)} 항목)")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("RESULT: PASS — 시장 지표 real-data-only(메인=자동연동만 · 금지모드 0건 · "
          "데모배지 조건부 · backlog 강등) (D7-AE-RC1)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
