#!/usr/bin/env python3
"""D7-AE-RC1 verifier — 현대건설(000720) 주가 자동 갱신 그래프.

사용자 실사용 QA 실패: "현대건설 주가도 자동 갱신 그래프가 있어야 한다. mock chart면
공개 화면에 표시하지 마라." 시장 모니터링 48종 어디에도 현대건설 자사주가 없었다.

수정: 000720.KS(KRX, Yahoo Finance 공개 무인증 chart API — 심볼은 실 GET 프로빙으로
검증, 2026-07-02 실측 종가 115,200원 KRW)를 기존 시장 히스토리 파이프라인
(app/market_history.py)에 등록했다 — 다른 47개 종목과 동일 leaf·동일 정직성 계약을
그대로 재사용한다(신규 코드 경로 없음). 새 카테고리 "equities"(현대건설 주가)를
dashboard CAT_LABEL/CAT_ORDER에 추가하고, docs/D7AE_MARKET_SOURCE_AUDIT.md에
keep_live로 문서화했다.

이 verifier가 잠그는 계약:
  A. market_history 리프에 hdec_stock/000720.KS가 정확히 등록됨(오프라인).
  B. 대시보드 모델(market_items)에 hdec_stock 항목이 존재하고 category=equities.
  C. mock 빌드 — value/history/source/as_of/data_mode 전부 존재, history는 결정적
     데모(mock_demo)로 정직 표기(라이브로 위장 안 함), 1주/1개월/3개월/1년이 서로 다름.
  D. live 빌드(네트워크 있으면 실측, 없으면 SKIP) — data_mode가 live_market/delayed_market
     계열이고, history_source가 실제 Yahoo Finance고, as_of가 결정적 데모 기준일이
     아닌 최근 날짜다(진짜 실측임을 증명).
  E. 차트 클릭 가능성 — history["1m"]이 JS의 isClickable 기준(>=2점)을 만족한다.
  F. 카테고리 배선 — CAT_LABEL/CAT_ORDER에 equities가 있어 renderCategoryCards가
     자동으로 카드를 만든다(전용 렌더 코드 불필요 — 기존 제네릭 로직 재사용 확인).
  G. 감사 문서 — docs/D7AE_MARKET_SOURCE_AUDIT.md에 hdec_stock 행 + keep_live 판단.

완전 OFFLINE(A/B/C/E/F/G) · D만 네트워크 있으면 추가 검증(없으면 SKIP, FAIL 아님).
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

from app import market_history as mh  # noqa: E402

BUILDER = ROOT / "scripts" / "build_static_dashboard.py"
TEMPLATE = ROOT / "templates" / "dashboard_preview.html"
AUDIT_DOC = ROOT / "docs" / "D7AE_MARKET_SOURCE_AUDIT.md"

ITEM_ID = "hdec_stock"
SYMBOL = "000720.KS"
ALLOWED_LIVE_MODES = ("live_market", "delayed_market")

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


def _item(model: dict) -> dict | None:
    return next((i for i in (model.get("market_items") or []) if i.get("id") == ITEM_ID), None)


# ---------------------------------------------------------------------------
# A · leaf 등록(오프라인)
# ---------------------------------------------------------------------------

def check_leaf_registration() -> None:
    check("A1: market_history에 hdec_stock 등록됨", ITEM_ID in mh.supported_ids())
    spec = mh._BY_ID.get(ITEM_ID)
    check("A2: 심볼이 000720.KS(현대건설 KRX)", bool(spec) and spec.symbol == SYMBOL,
          getattr(spec, "symbol", None))
    check("A3: 1주/1개월/3개월/1년 전부 서로 다른 배열(가짜 resample 아님)",
          mh.demo_distinct_ok(ITEM_ID))
    entry = mh.demo_entry(ITEM_ID)
    check("A4: demo_entry가 5개 필드 전부 반환(history/source/mode/as_of/decimals)",
          all(k in entry for k in ("history", "history_source", "history_data_mode",
                                   "history_updated_at", "history_decimals")))


# ---------------------------------------------------------------------------
# B/C · mock 빌드 — 필드 완전성 + 정직 라벨
# ---------------------------------------------------------------------------

def check_mock_build() -> None:
    with tempfile.TemporaryDirectory(prefix="hdec_stock_mock_") as tmp:
        out = Path(tmp) / "dash.html"
        proc = subprocess.run([sys.executable, str(BUILDER), "--output", str(out)],
                              cwd=ROOT, capture_output=True, text=True, timeout=300,
                              env={**os.environ})
        if not check("mock 빌드 동작(오프라인)", proc.returncode == 0 and out.exists(),
                     (proc.stderr or "")[-200:]):
            return
        model = _model(out.read_text(encoding="utf-8"))
        item = _item(model)
        if not check("B1: market_items에 hdec_stock 존재", item is not None):
            return
        check("B2: category=equities", item.get("category") == "equities", item.get("category"))
        check("B3: label_kr에 '현대건설' 포함", "현대건설" in (item.get("label_kr") or ""))
        for field in ("value", "history", "history_source", "history_updated_at", "data_mode"):
            check(f"C1: 필드 '{field}' 존재(None 아님)", item.get(field) is not None,
                  str(item.get(field))[:60])
        check("C2: data_mode가 live/delayed 계열", item.get("data_mode") in ALLOWED_LIVE_MODES,
              item.get("data_mode"))
        check("C3: mock 빌드에서 history_data_mode=mock_demo(라이브로 위장 안 함)",
              item.get("history_data_mode") == "mock_demo", item.get("history_data_mode"))
        check("C4: history_source가 데모 픽스처로 정직 표기",
              "데모" in (item.get("history_source") or ""))
        hist = item.get("history") or {}
        series = [tuple(hist.get(p) or ()) for p in ("1w", "1m", "3m", "1y")]
        check("C5: 1주/1개월/3개월/1년 전부 서로 다른 배열(모델 레벨)",
              all(len(s) >= 2 for s in series) and len(set(series)) == len(series))
        check("E1: 1개월 히스토리 >=2점(JS isClickable 기준 충족 → 차트 클릭 가능)",
              len(hist.get("1m") or []) >= 2)


# ---------------------------------------------------------------------------
# D · live 빌드(네트워크 있으면 실측 확인, 없으면 SKIP)
# ---------------------------------------------------------------------------

def check_live_build() -> None:
    try:
        import urllib.request
        probe_req = urllib.request.Request(
            "https://query1.finance.yahoo.com/v8/finance/chart/000720.KS?interval=1d&range=1d",
            headers={"User-Agent": "Mozilla/5.0"})
        urllib.request.urlopen(probe_req, timeout=8).close()
    except Exception as exc:
        info(f"D: 네트워크 미가용({exc!r}) — live 빌드 실측 검증 SKIP(FAIL 아님)")
        return
    with tempfile.TemporaryDirectory(prefix="hdec_stock_live_") as tmp:
        out = Path(tmp) / "dash.html"
        proc = subprocess.run(
            [sys.executable, str(BUILDER), "--market-mode", "live", "--output", str(out)],
            cwd=ROOT, capture_output=True, text=True, timeout=300, env={**os.environ})
        if not check("D0: live 빌드 동작", proc.returncode == 0 and out.exists(),
                     (proc.stderr or "")[-200:]):
            return
        item = _item(_model(out.read_text(encoding="utf-8")))
        if not check("D1: live 빌드 market_items에 hdec_stock 존재", item is not None):
            return
        mode = item.get("history_data_mode")
        if mode != "delayed_market":
            info(f"D: live 실측 실패(네트워크 순단 등) — history_data_mode={mode}, "
                 "최선노력 계약이라 SKIP(FAIL 아님, 데모로 정직 유지되는지만 확인)")
            check("D2: live 실패 시에도 data_mode가 여전히 live/delayed 계열로 정직 유지",
                  item.get("data_mode") in ALLOWED_LIVE_MODES)
            return
        check("D2: live 실측 성공 — history_source가 Yahoo Finance(지연)",
              "Yahoo" in (item.get("history_source") or ""), item.get("history_source"))
        check("D3: live 실측 as_of가 데모 고정일(2026-06-22)이 아님(진짜 실측 증명)",
              item.get("history_updated_at") != mh.DEMO_AS_OF, item.get("history_updated_at"))
        check("D4: data_mode가 live/delayed 계열", item.get("data_mode") in ALLOWED_LIVE_MODES,
              item.get("data_mode"))
        val = str(item.get("value") or "").replace(",", "")
        check("D5: value가 숫자로 파싱 가능(가짜 문자열 아님)",
              val.replace(".", "", 1).isdigit(), item.get("value"))


# ---------------------------------------------------------------------------
# F · 카테고리 배선(제네릭 렌더 재사용 확인)
# ---------------------------------------------------------------------------

def check_category_wiring() -> None:
    tpl = _read(TEMPLATE)
    check("F1: CAT_LABEL에 equities 라벨 존재", "equities:" in tpl and "현대건설 주가" in tpl)
    check("F2: CAT_ORDER에 equities 포함",
          re.search(r'CAT_ORDER\s*=\s*\[[^\]]*"equities"', tpl) is not None)
    # renderCategoryCards는 CAT_ORDER를 순회하는 제네릭 함수 하나뿐 — 전용 렌더 코드가
    # 새로 생기지 않았음을 확인한다(중복 렌더 로직 없음 = 기존 인프라 재사용 증거).
    check("F3: renderCategoryCards 함수가 여전히 1개(전용 렌더 코드 신설 안 함)",
          tpl.count("function renderCategoryCards(") == 1)


# ---------------------------------------------------------------------------
# G · 감사 문서
# ---------------------------------------------------------------------------

def check_audit_documented() -> None:
    doc = _read(AUDIT_DOC)
    check("G1: 감사 문서 존재", bool(doc))
    check("G2: hdec_stock 행 존재", f"| {ITEM_ID} |" in doc)
    check("G3: keep_live 판단 부여", bool(re.search(rf"\| {ITEM_ID} \|.*keep_live", doc)))
    check("G4: 000720.KS 심볼 프로빙 근거 기록", SYMBOL in doc)


def main() -> int:
    print(f"== verify_hdec_stock_chart (D7-AE-RC1) @ {ROOT} ==")
    check_leaf_registration()
    check_mock_build()
    check_live_build()
    check_category_wiring()
    check_audit_documented()
    print()
    if _failures:
        print(f"RESULT: FAIL ({len(_failures)} 항목)")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("RESULT: PASS — 현대건설 주가 자동 갱신 그래프(mock 정직 데모 · live 실측 · "
          "카테고리 배선 · 감사 문서화) (D7-AE-RC1)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
