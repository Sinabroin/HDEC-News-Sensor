#!/usr/bin/env python3
"""D7-AE verifier — 시장 지표 소스 감사 (market source audit).

사용자 QA: "시장 데이터는 연동/불가/프록시/수동이 섞여 있어 엄격히 구분해야 한다.
proxy를 current price처럼 표시하지 말고, 삭제 전에 source audit부터 만들라."

계약(전부 오프라인 · 네트워크 0건):
  1. 감사 문서(docs/D7AE_MARKET_SOURCE_AUDIT.md)가 존재하고, 대시보드 모델의 모든
     지표 id를 다루며, 최종 판단이 열거값 5종 중 하나다. 감사표에 없는 지표를
     대시보드에 추가하면 FAIL(감사 없는 지표 금지).
  2. D7-AE 승격 5종(CHF/SGD/HKD/INR/AED·KRW)이 market_history 지원 목록에 있고,
     us_2y/kr_10y는 여전히 market_history 밖(FRED leaf 소유 — 이중 소스 금지).
  3. 강등 로직(_demote_unbacked_market_values): live 모드에서 소스(history/FRED
     value_source) 없는 정적 표시값을 제거한다 — delayed/proxy는 unavailable로,
     manual_or_reported는 라벨 유지+값 제거. 값 생성 0건. mock 모드는 no-op
     (데모 미리보기 계약 보존).
  4. 빌더 호출 순서: overlay 2종 뒤 · meta 카운트(_market_link_counts) 앞.
  5. 커밋 산출물: live 시장 빌드(강등 플래그 존재로 판별)면 값 있는 행 전부가
     소스 근거(history_source 또는 value_source)를 갖는다 — 근거 없는 정적 값이
     남으면 FAIL(unsupported current-value claim catcher). mock 빌드면 데모 라벨
     확인 후 SKIP.
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from app import market_history as mh  # noqa: E402
import build_static_dashboard as builder  # noqa: E402

DOC = ROOT / "docs" / "D7AE_MARKET_SOURCE_AUDIT.md"
TEMPLATE = ROOT / "templates" / "dashboard_preview.html"
DASHBOARD = ROOT / "docs" / "daily" / "dashboard-latest.html"

JUDGMENTS = ("keep_live", "keep_proxy_with_caveat", "keep_manual_report_only",
             "remove_from_dashboard", "move_to_backlog")
PROMOTED = ("chfkrw", "sgdkrw", "hkdkrw", "inrkrw", "aedkrw")
FRED_ONLY = ("us_2y", "kr_10y")

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


def _model(path: Path) -> dict:
    try:
        html = path.read_text(encoding="utf-8")
    except OSError:
        return {}
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


# ---------------------------------------------------------------------------
# 1 · 감사 문서 — 전 지표 커버 + 판단 열거값
# ---------------------------------------------------------------------------

def check_audit_doc() -> None:
    if not check("1a: 감사 문서 존재(docs/D7AE_MARKET_SOURCE_AUDIT.md)", DOC.exists()):
        return
    doc = DOC.read_text(encoding="utf-8")
    ids = [it.get("id") for it in (_model(TEMPLATE).get("market_items") or [])]
    if not check("1b: 템플릿 market_items 로드", bool(ids), f"{len(ids)}개"):
        return
    missing = [i for i in ids if f"| {i} |" not in doc]
    check("1c: 감사표가 대시보드 전 지표를 다룸(감사 없는 지표 금지)", not missing,
          f"누락: {missing[:5]}")
    check("1d: 최종 판단 열거값 5종 정의", all(j in doc for j in JUDGMENTS))
    # 각 지표 행에 열거 판단이 최소 1개 들어 있는지(표 행 단위 확인).
    rows_wo_judgment = []
    for i in ids:
        m = re.search(rf"^\| {re.escape(i)} \|.*$", doc, re.M)
        if not m or not any(j in m.group(0) for j in JUDGMENTS):
            rows_wo_judgment.append(i)
    check("1e: 모든 지표 행에 최종 판단 명시", not rows_wo_judgment,
          f"판단 없음: {rows_wo_judgment[:5]}")
    check("1f: 프로빙 근거 문서화(추정 금지 계약)", "프로브" in doc or "프로빙" in doc)


# ---------------------------------------------------------------------------
# 2 · 승격 5종 + FRED 소유권
# ---------------------------------------------------------------------------

def check_supported_set() -> None:
    sup = set(mh.supported_ids())
    missing = [i for i in PROMOTED if i not in sup]
    check("2a: D7-AE 승격 5종이 market_history 지원 목록에 존재", not missing,
          f"누락: {missing}")
    dup = [i for i in FRED_ONLY if i in sup]
    check("2b: us_2y/kr_10y는 market_history 밖(FRED leaf 단일 소유)", not dup,
          f"이중 소스: {dup}")
    # 승격 5종 데모 픽스처도 결정적이어야 한다(레거시 계약과 동일).
    det = all(mh.demo_history(i) == mh.demo_history(i) for i in PROMOTED)
    check("2c: 승격 5종 데모 픽스처 결정적", det)


# ---------------------------------------------------------------------------
# 3 · 강등 로직 (합성 모델 단위검사)
# ---------------------------------------------------------------------------

def _synthetic_model() -> dict:
    return {"market_items": [
        # (a) live 실측이 붙은 행 — 유지되어야 한다
        {"id": "s_live", "value": "1,536.8", "data_mode": "delayed_market",
         "dir": "up", "delta": "▲ +0.2%", "history_data_mode": "delayed_market",
         "history_source": "Yahoo Finance chart (지연)"},
        # (b) 데모 라벨이 붙은 지원 행 — 유지(정직 라벨 계약 · D7-Z3)
        {"id": "s_demo", "value": "100.0", "data_mode": "delayed_market",
         "dir": "flat", "delta": "—", "history_data_mode": "mock_demo",
         "history_source": "데모 픽스처(결정적)"},
        # (c) FRED 단일값 행 — 유지
        {"id": "s_fred", "value": "4.10", "data_mode": "delayed_market",
         "dir": "flat", "delta": "—", "value_source": "FRED (public CSV)"},
        # (d) 소스 없는 정적 proxy 값 — live에서 unavailable 강등 대상
        {"id": "s_static_proxy", "value": "2,710", "data_mode": "proxy_market",
         "dir": "down", "delta": "▼ 0.4%", "spark": [1, 2, 3]},
        # (e) 소스 없는 정적 manual 값 — live에서 값만 제거(라벨 유지)
        {"id": "s_static_manual", "value": "12.4", "data_mode": "manual_or_reported",
         "dir": "flat", "delta": "—"},
        # (f) 원래 값 없는 행 — 그대로
        {"id": "s_null", "value": None, "data_mode": "unavailable",
         "dir": "flat", "delta": "—"},
    ]}


def check_demotion_logic() -> None:
    live = _synthetic_model()
    builder._demote_unbacked_market_values(live, "live")
    by = {it["id"]: it for it in live["market_items"]}
    check("3a: live 실측 행 유지(값·모드 불변)",
          by["s_live"]["value"] == "1,536.8"
          and by["s_live"]["data_mode"] == "delayed_market")
    check("3b: 데모 라벨 지원 행 유지(D7-Z3 데모 폴백 계약 보존)",
          by["s_demo"]["value"] == "100.0")
    check("3c: FRED 단일값 행 유지", by["s_fred"]["value"] == "4.10")
    d = by["s_static_proxy"]
    check("3d: 소스 없는 정적 proxy → 값 제거 + unavailable 강등 + 플래그",
          d["value"] is None and d["data_mode"] == "unavailable"
          and d.get("static_value_demoted") is True and "spark" not in d)
    m = by["s_static_manual"]
    check("3e: 소스 없는 정적 manual → 값만 제거(라벨 유지 = 보고 대기)",
          m["value"] is None and m["data_mode"] == "manual_or_reported"
          and m.get("static_value_demoted") is True)
    check("3f: 원래 값 없는 행 불변(생성 0건)", by["s_null"]["value"] is None
          and "static_value_demoted" not in by["s_null"])

    mock = _synthetic_model()
    builder._demote_unbacked_market_values(mock, "mock")
    mby = {it["id"]: it for it in mock["market_items"]}
    check("3g: mock 모드는 no-op(데모 미리보기 계약 보존)",
          mby["s_static_proxy"]["value"] == "2,710"
          and mby["s_static_manual"]["value"] == "12.4")


# ---------------------------------------------------------------------------
# 4 · 빌더 호출 순서
# ---------------------------------------------------------------------------

def check_builder_order() -> None:
    src = (ROOT / "scripts" / "build_static_dashboard.py").read_text(encoding="utf-8")
    i_hist = src.find("_overlay_market_history(model, market_mode)")
    i_fred = src.find("_overlay_fred_live_quotes(model, market_mode)")
    i_demote = src.find("_demote_unbacked_market_values(model, market_mode)", i_hist)
    i_counts = src.find("_market_link_counts(model.get(\"market_items\")")
    ok = 0 < i_hist < i_fred < i_demote < i_counts
    check("4a: 강등은 overlay 2종 뒤 · meta 카운트 앞(카운트 일관성)", ok,
          f"hist={i_hist} fred={i_fred} demote={i_demote} counts={i_counts}")


# ---------------------------------------------------------------------------
# 5 · 커밋 산출물 — unsupported current-value claim catcher
# ---------------------------------------------------------------------------

def check_committed() -> None:
    model = _model(DASHBOARD)
    items = model.get("market_items") or []
    if not items:
        info("커밋 대시보드 market_items 없음 — SKIP")
        return
    live_built = any(it.get("static_value_demoted") for it in items)
    if not live_built:
        # mock 빌드 산출물 — 데모 유니버스는 '데모' 정직 라벨로 커버된다.
        html = DASHBOARD.read_text(encoding="utf-8")
        check("5a: mock 시장 빌드 — 데모 정직 라벨 존재(강등은 live 빌드에서 작동)",
              "데모" in html)
        info("live 시장 빌드 아님(강등 플래그 0건) — 5b는 live 게시 산출물에서 검사")
        return
    unbacked = [it.get("id") for it in items
                if _has_value(it)
                and not it.get("history_source") and not it.get("value_source")]
    check("5b: live 빌드에서 값 있는 행 전부 소스 근거 보유(정적 죽은 값 0건)",
          not unbacked, f"근거 없는 값: {unbacked[:5]}")


def main() -> int:
    print(f"== verify_market_source_audit (D7-AE) @ {ROOT} ==")
    check_audit_doc()
    check_supported_set()
    check_demotion_logic()
    check_builder_order()
    check_committed()
    print()
    if _failures:
        print(f"RESULT: FAIL ({len(_failures)} 항목)")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("RESULT: PASS — 시장 소스 감사: 전 지표 판정 문서화 · 승격 5종 · "
          "정적 죽은 값 live 강등 (D7-AE)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
