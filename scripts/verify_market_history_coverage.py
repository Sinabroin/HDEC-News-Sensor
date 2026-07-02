#!/usr/bin/env python3
"""D7-G verifier — market period-history coverage is broad, honest, and never blank.

Runs fully offline (no network, DB, secrets, or send). D7-F connected period history for
the headline items only; D7-G expands real Yahoo-chart history to more visible market rows
(base metals, steel/materials, oil/refined, gas/LNG, coal) while keeping the honesty line:

  · connected rows carry per-period `history` (1w/1m/3m/1y distinct, 3m≠1y) + history_source
    + history_data_mode + history_updated_at + history_decimals and ARE clickable,
  · global-benchmark rows that are not the exact Korean spot are flagged `proxy` + 대용(proxy)
    label + a "정확한 국내 현물가 아님" note,
  · rows with no reliable free source (US 2Y / KR 10Y / nickel / …) stay non-clickable with an
    honest label and NEVER open a blank chart drawer,
  · at least one item in every major market group (base metals / steel·materials / oil·refined
    / gas·LNG / coal / FX / rates) carries history OR is clearly source-needed/proxy,
  · the provider (app/market_history) only adds symbols that return valid history (no fake
    symbols), embeds no API keys/secrets, and demo fixtures stay deterministic + period-distinct.
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TEMPLATE = ROOT / "templates" / "dashboard_preview.html"
DASHBOARD = ROOT / "docs" / "daily" / "dashboard-latest.html"
PROVIDER = ROOT / "app" / "market_history.py"

from app import market_history as mh  # noqa: E402

PERIODS = ("1w", "1m", "3m", "1y")
# 템플릿 시장 카테고리 → 태스크가 명시한 major market group (모두 ≥1 종목이 히스토리/대용/소스필요여야).
GROUPS = ("base_metals", "steel_materials", "oil_refined", "gas_lng",
          "coal", "rates_inflation", "fx")
# 공개 무료 소스가 없어 비연동(소스 필요)로 두는 종목 — 클릭/차트 없음.
SOURCE_NEEDED = ("us_2y", "kr_10y")

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


def _has_history(it: dict) -> bool:
    h = it.get("history") or {}
    return bool(h.get("1m")) and len(h.get("1m") or []) >= 2


# ---------------------------------------------------------------------------
# 1 · 커버리지 확대: 연동 종목 수 + 카테고리 그룹별 ≥1 히스토리
# ---------------------------------------------------------------------------

def check_coverage(html: str, where: str) -> None:
    items = _items(html)
    if not check(f"1a[{where}]: market_items 모델 존재(>=40)", len(items) >= 40, f"{len(items)}개"):
        return
    connected = [it for it in items if _has_history(it)]
    # D7-F headline 7 + D7-G 확대 → 최소 14개 이상 연동(보수적 하한).
    check(f"1b[{where}]: 기간 히스토리 연동 종목 확대(>=14)", len(connected) >= 14,
          f"{len(connected)}개 연동")
    by_group = {}
    for it in items:
        by_group.setdefault(it.get("category"), []).append(it)
    for g in GROUPS:
        rows = by_group.get(g) or []
        with_hist = [it for it in rows if _has_history(it)]
        # 소스가 없으면 명시적 source-needed/proxy로 둔 그룹(coal/steel)도 ≥1 히스토리를 목표로 한다.
        honest = [it for it in rows
                  if it.get("data_mode") in ("proxy_market", "unavailable", "manual_or_reported")]
        ok = bool(with_hist) or (bool(rows) and len(honest) == len(rows))
        check(f"1c[{where}]: 그룹 '{g}' ≥1 히스토리(또는 전부 명시 대용/소스필요)",
              ok, f"hist={len(with_hist)}/{len(rows)}")
    # 모든 major group이 history를 갖는 종목을 최소 1개 가짐(coal/steel 포함 — 대용이라도 차트 연동).
    groups_with_hist = {it.get("category") for it in connected}
    missing = [g for g in GROUPS if g not in groups_with_hist]
    check(f"1d[{where}]: 모든 major 그룹이 히스토리 연동 종목 보유",
          not missing, f"히스토리 없는 그룹: {missing}" if missing else "7/7 그룹 연동")


# ---------------------------------------------------------------------------
# 2 · 빈 차트 금지 + 70% 정직 커버 + 연동 종목 3개월≠1년
# ---------------------------------------------------------------------------

def check_no_blank_and_distinct(html: str, where: str) -> None:
    items = _items(html)
    # 클릭 가능 = 히스토리 보유. 히스토리 없는데 클릭 가능한 종목(=빈 드로어)이 없어야 한다.
    # (프론트 isClickable이 history를 요구하므로 모델 차원에서 동일 불변식을 확인)
    acceptable = 0
    distinct_ok = True
    for it in items:
        connected = _has_history(it)
        if connected:
            series = [tuple((it.get("history") or {}).get(p) or ()) for p in PERIODS]
            if any(len(s) < 2 for s in series) or len(set(series)) != len(series) \
                    or series[2] == series[3]:
                distinct_ok = False
            acceptable += 1
        else:
            # 비연동은 반드시 정직 라벨(대용/미연동/보고/지연)로 비클릭 — 빈 드로어 없음.
            if it.get("data_mode") in ("proxy_market", "unavailable", "manual_or_reported",
                                       "delayed_market"):
                acceptable += 1
    ratio = acceptable / len(items) if items else 0
    check(f"2a[{where}]: 시장 행의 70%+가 히스토리 또는 정직 비클릭(빈 드로어 없음)",
          ratio >= 0.70, f"{acceptable}/{len(items)} = {ratio:.0%}")
    check(f"2b[{where}]: 모든 연동 종목이 1주/1개월/3개월/1년 distinct(3개월≠1년)", distinct_ok)


# ---------------------------------------------------------------------------
# 3 · 대용(proxy) 라벨 + 소스필요 비클릭
# ---------------------------------------------------------------------------

def check_proxy_and_source_needed(html: str, tpl: str, where: str) -> None:
    items = {it.get("id"): it for it in _items(html)}
    # 대용(proxy)으로 연동한 종목은 proxy=true + proxy_note(국내 현물가 아님) + 대용 data_mode.
    proxies = [it for it in items.values() if it.get("proxy")]
    check(f"3a[{where}]: 대용(proxy) 종목 존재(>=1)", bool(proxies), f"{len(proxies)}개")
    badproxy = [it.get("id") for it in proxies
                if it.get("data_mode") != "proxy_market" or not (it.get("proxy_note") or "").strip()]
    check(f"3b[{where}]: 대용 종목은 proxy_market + proxy_note(국내 현물가 아님) 표기",
          not badproxy, f"라벨 미흡: {badproxy}" if badproxy else "ok")
    # 소스필요 종목은 어떤 경우에도 히스토리 없음(비클릭 · 가짜 차트 금지).
    # D7-AE 계약 갱신: D7-AD-X FRED leaf가 us_2y(일간)/kr_10y(OECD 월간 proxy) 단일
    # 관측값을 live로 채우므로, mock의 정직 unavailable(value=null)과 live의 FRED
    # current_only(value_source 필수 · kr_10y는 proxy_market 라벨) 둘 다 허용한다.
    for sid in SOURCE_NEEDED:
        it = items.get(sid) or {}
        no_hist = not _has_history(it)
        if it.get("value") is None:
            ok = no_hist and it.get("data_mode") == "unavailable"
            state = "mock-unavailable"
        else:
            want_mode = "proxy_market" if sid == "kr_10y" else "delayed_market"
            ok = (no_hist and bool(str(it.get("value_source") or "").strip())
                  and it.get("data_mode") == want_mode)
            state = "live-FRED-current_only"
        check(f"3c[{where}]:{sid} 소스필요 — 히스토리 없음 · 정직 상태"
              f"(unavailable 또는 FRED 단일값)", ok,
              f"{state} hist={_has_history(it)} value={it.get('value')} "
              f"mode={it.get('data_mode')} src={bool(it.get('value_source'))}")
    # 템플릿 UI 계약: 대용 라벨/소스필요 비클릭/빈드로어 거부.
    check("3d: 템플릿이 대용(proxy) 드로어 라벨을 노출",
          "대용(proxy)" in tpl and "정확한 국내 현물가 아님" in tpl)
    check("3e: 템플릿 openDrawer가 히스토리 없음을 거부(빈 차트 방지)",
          re.search(r'current\.history && current\.history\["1m"\]', tpl) is not None)
    check("3f: 비클릭 행에 '히스토리 미연동'/'차트 없음' 표기",
          "히스토리 미연동" in tpl and "차트 없음" in tpl)


# ---------------------------------------------------------------------------
# 4 · provider — 실제 심볼만(가짜 없음) + 결정적 데모 + 비밀값 없음
# ---------------------------------------------------------------------------

def check_provider() -> None:
    src = _read(PROVIDER)
    sup = set(mh.supported_ids())
    new = ("aluminum", "iron_ore", "hrc_steel", "thermal_coal", "gasoline",
           "diesel_gasoil", "henry_hub", "ttf_gas", "eurkrw", "gbpkrw")
    check("4a: provider가 D7-G 확대 종목을 지원", set(new) <= sup,
          f"누락: {sorted(set(new) - sup)}")
    check("4b: US 2Y·KR 10Y는 provider 비지원(소스 필요)",
          all(not mh.is_supported(s) for s in SOURCE_NEEDED))
    det = all(mh.demo_history(s) == mh.demo_history(s) for s in new)
    check("4c: demo_history 결정적(now()/random 미사용)", det)
    dist = all(mh.demo_distinct_ok(s) for s in new)
    check("4d: 확대 종목 demo_history 기간 distinct(3개월≠1년)", dist)
    # 각 spec은 실제 Yahoo 심볼을 가짐(빈 심볼/플레이스홀더 없음).
    syms = [mh._BY_ID[s].symbol for s in new]
    check("4e: 확대 종목이 실제 심볼 보유(가짜/빈 심볼 없음)",
          all(isinstance(x, str) and x.strip() for x in syms), f"{syms}")
    # API key/secret/token 미임베드.
    secret = re.findall(r"(?i)\b(api[_-]?key|secret|bearer|access[_-]?token)\b", src)
    check("4f: provider에 API key/secret/token 미임베드", not secret,
          f"의심: {set(secret)}" if secret else "ok")
    # 템플릿 임베드(데모 픽스처)가 provider demo와 동기화(드리프트 방지).
    titems = {it.get("id"): it for it in _items(_read(TEMPLATE))}
    sync = all((titems.get(s) or {}).get("history") == mh.demo_history(s) for s in new)
    check("4g: 템플릿 임베드 history == market_history.demo_history (동기화)", sync)

    d7z2_ids = ("lumber", "rebar", "cnykrw", "audkrw", "cadkrw",
                "dxy", "eurusd", "usdjpy", "usdcny")
    missing_d7z2 = [item_id for item_id in d7z2_ids if not mh.is_supported(item_id)]
    check("4h: D7-Z2 신규 market_history 지원 id 회귀 방지",
          not missing_d7z2, "missing=" + ",".join(missing_d7z2))

    proxy_ids = set(getattr(mh, "PROXY_IDS", ()))
    check("4i: D7-Z2 rebar 대용(proxy) 정직성 유지",
          "rebar" in proxy_ids, "PROXY_IDS=" + ",".join(sorted(proxy_ids)))


def main() -> int:
    print(f"== verify_market_history_coverage (D7-G Part A) @ {ROOT} ==")
    tpl = _read(TEMPLATE)
    if not check("0: 템플릿 존재", bool(tpl) and len(tpl) > 4000):
        print("\nRESULT: FAIL (템플릿 누락)")
        return 1
    # 템플릿(데모) + 커밋된 대시보드(라이브 스냅샷) 둘 다 검사.
    check_coverage(tpl, "tpl")
    check_no_blank_and_distinct(tpl, "tpl")
    check_proxy_and_source_needed(tpl, tpl, "tpl")
    if DASHBOARD.exists():
        dash = _read(DASHBOARD)
        check_coverage(dash, "dash")
        check_no_blank_and_distinct(dash, "dash")
        check_proxy_and_source_needed(dash, tpl, "dash")
    else:
        check("0b: docs/daily/dashboard-latest.html 존재", False)
    check_provider()

    print()
    if _failures:
        print(f"RESULT: FAIL ({len(_failures)} 항목)")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("RESULT: PASS — 시장 히스토리 커버리지 확대 + 대용/소스필요 정직성 + 빈 드로어 없음 (D7-G Part A)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
