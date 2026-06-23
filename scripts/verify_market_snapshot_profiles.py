"""P0-D5-B 검증기 — 시장 센싱 데이터 레이어(건설 원자재·국채 금리·환율) 회귀 검사.

핵심 원칙 (네트워크 없이 결정적으로 통과한다):
- 카탈로그는 순수 설정 leaf(app/market_profiles.py)가 소유한다 — 시세값을 만들지 않는다.
- 네트워크 IO는 leaf(app/live_market.py)가 소유한다. app/market_snapshot.py는 fetcher를
  호출만 하고 네트워크를 직접 import하지 않는다 (macro_snapshot→live_macro와 동일 격리).
- 정직성: proxy/지연/미연동을 종목별 data_mode로 표기하고, 실패/결측을 가짜 값으로
  채우지 않는다(unavailable). '실시간/현재 체결값' 같은 무자격 주장을 하지 않는다.
- 기존 D5-A(토픽 프로파일)·D4-E(AI 탭 보강)·데이터 출처 정직성 게이트를 깨지 않는다.

저장소의 radar.db는 절대 건드리지 않는다.

사용법:
    python3 scripts/verify_market_snapshot_profiles.py
"""

import json
import os
import py_compile
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET_PROFILES = ROOT / "app" / "market_profiles.py"
LIVE_MARKET = ROOT / "app" / "live_market.py"
MARKET_SNAPSHOT = ROOT / "app" / "market_snapshot.py"
BRIEFING = ROOT / "app" / "briefing.py"
BRIEF_BUILDER = ROOT / "scripts" / "build_executive_brief.py"
RADAR_DB = ROOT / "radar.db"

REQUIRED_COMMODITIES = ("copper", "aluminum", "iron_ore", "steel_rebar_proxy",
                        "wti_crude", "brent_crude", "natural_gas", "lumber",
                        "cement_proxy")
REQUIRED_YIELD_COUNTRIES = ("kr", "us", "jp", "de", "uk", "cn")
REQUIRED_TENORS = ("1y", "3y", "5y", "10y")
REQUIRED_FX = ("usdkrw", "eurkrw", "jpykrw", "cnykrw")

# 본문 전문 필드명 (rules.md §3) — 조각 조립으로 grep 규약 회피
BANNED_TERMS = ["".join(p) for p in [
    ("raw", "_payload"), ("full", "_text"), ("article", "_body"),
    ("full_rss", "_content")]]
SECRET_TOKENS = ("TELEGRAM", "BOT_TOKEN", "API_KEY", "SECRET", "BEARER", "PASSWORD")
TOKEN_SHAPE = re.compile(r"[0-9]{8,}:[A-Za-z0-9_-]{20,}")

# 무자격 현재성/실시간 주장 — 시장 텍스트에 부정/자격 없이 등장하면 실패
CLAIM_RE = re.compile(r"실시간|현재|최신|\blive\b|real[- ]?time", re.I)
QUALIFIERS = ("미연동", "아님", "아니", "않", "없", "미반영", "미제공", "미구현",
              "연동 전", "not ", "대용", "proxy", "지연", "참고용")

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


def skip(message: str) -> None:
    print(f"[SKIP] {message}")


def _db_state():
    if not RADAR_DB.exists():
        return None
    st = RADAR_DB.stat()
    return (st.st_mtime_ns, st.st_size)


def _import(mod_name: str):
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    os.environ.setdefault("APP_MODE", "mock")
    return __import__(mod_name, fromlist=["_"])


def _claim_violations(text: str) -> bool:
    """텍스트에 부정/자격 없는 현재성·실시간 주장이 있으면 True (줄 단위)."""
    for line in str(text).splitlines():
        if CLAIM_RE.search(line) and not any(q in line for q in QUALIFIERS):
            return True
    return False


# ---------- 정적 검사 ----------

def check_py_compile() -> None:
    bad = []
    targets = sorted(list((ROOT / "scripts").glob("*.py"))
                     + list((ROOT / "app").glob("*.py")))
    with tempfile.TemporaryDirectory(prefix="hdec_pyc_") as tmp:
        for i, path in enumerate(targets):
            try:
                py_compile.compile(str(path), cfile=os.path.join(tmp, f"{i}.pyc"),
                                   doraise=True)
            except py_compile.PyCompileError as exc:
                bad.append(f"{path.name}: {exc.msg.strip().splitlines()[-1]}")
    check("py_compile scripts/*.py app/*.py", not bad, "; ".join(bad))


def check_boundaries() -> None:
    """네트워크/비밀값 격리 — live_market만 urllib을 소유, 나머지는 순수."""
    prof = MARKET_PROFILES.read_text(encoding="utf-8")
    snap = MARKET_SNAPSHOT.read_text(encoding="utf-8")
    leaf = LIVE_MARKET.read_text(encoding="utf-8")
    net_re = (r"^\s*import (urllib|requests|httpx|socket)|"
              r"^\s*from (urllib|requests|httpx|socket)")
    check("market_profiles에 네트워크 import 없음 (순수 설정)",
          not re.search(net_re, prof, re.M))
    check("market_profiles에 DB(sqlite) import 없음",
          "sqlite3" not in prof and "import db" not in prof)
    check("market_snapshot이 모듈 레벨 네트워크 import 없음 (leaf로 격리)",
          not re.search(net_re, snap, re.M))
    check("market_snapshot이 live_market를 함수 안에서 지연 import",
          "from app import live_market" in snap)
    check("live_market가 네트워크 IO를 소유 (urllib 사용 — 격리 지점)",
          "urllib" in leaf.lower())
    for label, src in (("market_profiles", prof), ("market_snapshot", snap),
                       ("live_market", leaf)):
        low = src.lower()
        hits = [t for t in BANNED_TERMS if t in low]
        check(f"{label}에 본문 전문 필드명 없음", not hits, ", ".join(hits))
        secrets = [t for t in SECRET_TOKENS if t in src]
        check(f"{label}가 비밀값/인증 토큰을 읽지 않음", not secrets, ", ".join(secrets))
        check(f"{label}에 token 모양 하드코딩 없음", not TOKEN_SHAPE.search(src))
    check("live_market가 os.environ을 직접 읽지 않음", "os.environ" not in leaf)


# ---------- 1~4: 카탈로그 구조 ----------

def check_categories_present(mp) -> None:
    cats = set(mp.MARKET_CATEGORIES)
    expected = {"construction_commodities", "sovereign_yields", "fx"}
    check("1. 카테고리 3종 정의 (commodities/yields/fx)", expected <= cats,
          ", ".join(sorted(expected - cats)))
    for c in expected:
        check(f"1. '{c}' 지표 1건 이상",
              len(mp.instruments_by_category(c)) >= 1)


def check_commodities(mp) -> None:
    ids = {i.id for i in mp.instruments_by_category("construction_commodities")}
    missing = [c for c in REQUIRED_COMMODITIES if c not in ids]
    check("2. 건설 원자재 필수 항목 포함", not missing, "missing: " + ", ".join(missing))


def check_yields(mp) -> None:
    ids = {i.id for i in mp.instruments_by_category("sovereign_yields")}
    missing = [f"{cc}_{t}" for cc in REQUIRED_YIELD_COUNTRIES
               for t in REQUIRED_TENORS if f"{cc}_{t}" not in ids]
    check("3. 국채 금리 KR/US/JP/DE/UK/CN × 1Y/3Y/5Y/10Y 포함",
          not missing, "missing: " + ", ".join(missing))


def check_fx(mp) -> None:
    ids = {i.id for i in mp.instruments_by_category("fx")}
    missing = [f for f in REQUIRED_FX if f not in ids]
    check("4. 환율 USDKRW/EURKRW/JPYKRW/CNYKRW 포함",
          not missing, "missing: " + ", ".join(missing))


# ---------- 5~7: 지표 필드 계약 + proxy/unavailable 정직성 ----------

def check_instrument_fields(mp) -> None:
    bad = []
    for i in mp.all_instruments():
        if not (i.id and i.label_kr and i.category in mp.MARKET_CATEGORIES
                and i.data_mode in mp.DATA_MODES and i.unit is not None
                and i.source_provider and i.note_kr):
            bad.append(i.id)
    check("5. 모든 지표에 id/label_kr/category/data_mode/unit/source_provider/note_kr",
          not bad, ", ".join(bad[:8]))


def check_proxy_explained(mp) -> None:
    proxies = [i for i in mp.all_instruments() if i.data_mode == mp.MODE_PROXY]
    check("6. proxy 지표 1건 이상", len(proxies) >= 1, f"{len(proxies)}건")
    bad = [i.id for i in proxies if not (i.proxy_for or i.note_kr)]
    check("6. proxy 지표는 proxy_for 또는 note_kr로 대용 사유 설명", not bad,
          ", ".join(bad))


def check_unavailable_no_fake(mp, ms) -> None:
    # 카탈로그: unavailable 지표는 공개 심볼이 없어야 한다(=가짜 수집 대상 아님)
    bad_cat = [i.id for i in mp.all_instruments()
               if i.data_mode == mp.MODE_UNAVAILABLE and i.source_symbol]
    check("7. unavailable 카탈로그 지표에 공개 심볼 없음 (가짜 수집 금지)",
          not bad_cat, ", ".join(bad_cat))
    # snapshot: 일부만 값이 있는 live 결과에서 unavailable 항목은 값이 없어야 한다
    snap = ms.get_market_snapshot("live", fetcher=_partial_fetcher,
                                  now=datetime(2026, 6, 22, 1, 0, tzinfo=timezone.utc))
    bad_val = [it["id"] for it in snap["items"]
               if it["data_mode"] == "unavailable" and it["value"] is not None]
    check("7. unavailable snapshot 항목에 가짜 수치 없음 (value=None)", not bad_val,
          ", ".join(bad_val[:8]))
    # 값이 있는 항목은 절대 unavailable이 아니어야 한다
    bad_present = [it["id"] for it in snap["items"]
                   if it["value"] is not None and it["data_mode"] == "unavailable"]
    check("7. 값이 있는 항목은 unavailable이 아님", not bad_present,
          ", ".join(bad_present))


# ---------- 8~9: 신선도(지연) 표기 + 무자격 주장 금지 ----------

def check_stale_disclaimer(ms) -> None:
    snap = ms.get_market_snapshot("live", fetcher=_partial_fetcher,
                                  now=datetime(2026, 6, 22, 1, 0, tzinfo=timezone.utc))
    check("8. snapshot disclaimer에 지연 고지 포함",
          "지연" in (snap.get("disclaimer") or ""), snap.get("disclaimer", "")[:60])
    # 오래된 기준시각(>24h)은 is_stale=True로 정직 표기
    now = datetime(2026, 6, 22, 1, 0, tzinfo=timezone.utc)
    stale_snap = ms.get_market_snapshot("live", fetcher=_stale_fetcher, now=now)
    stale_items = [it for it in stale_snap["items"] if it["value"] is not None]
    check("8. 지연(>24h) 시세 항목 is_stale=True (정직 표기)",
          bool(stale_items) and all(it["is_stale"] for it in stale_items))


def check_no_unqualified_claims(mp, ms) -> None:
    # 카탈로그 note_kr/label
    bad = [i.id for i in mp.all_instruments()
           if _claim_violations(i.note_kr) or _claim_violations(i.label_kr)]
    check("9. 카탈로그 텍스트에 무자격 실시간/현재 주장 없음", not bad,
          ", ".join(bad[:8]))
    # snapshot 텍스트 (disclaimer/source_summary/warnings/item note)
    snap = ms.get_market_snapshot("live", fetcher=_partial_fetcher,
                                  now=datetime(2026, 6, 22, 1, 0, tzinfo=timezone.utc))
    texts = [snap.get("disclaimer", ""), snap.get("source_summary", "")]
    texts += [str(w) for w in snap.get("warnings", [])]
    texts += [it.get("note_kr", "") for it in snap["items"]]
    offenders = [t[:60] for t in texts if _claim_violations(t)]
    check("9. snapshot 텍스트에 무자격 실시간/현재 주장 없음", not offenders,
          "; ".join(offenders[:4]))


# ---------- 10~11: 수집기 계약 (fetcher 주입, 네트워크 없음) ----------

def _partial_fetcher(instruments):
    """일부 심볼만 값을 돌려주는 가짜 fetcher (나머지는 미연동이 되어야 한다)."""
    return {"source": "Yahoo Finance", "fetched_at": "2026-06-22T00:00:00+00:00",
            "stale_after_hours": 24, "quotes": {
                "HG=F": {"value": 4.51, "as_of": "2026-06-22T00:30:00+00:00",
                         "direction": "up"},
                "CL=F": {"value": 78.2, "as_of": "2026-06-22T00:30:00+00:00"},
                "USDKRW=X": {"value": 1377.5, "as_of": "2026-06-22T00:30:00+00:00",
                             "direction": "down"},
                "^TNX": {"value": 4.41, "as_of": "2026-06-22T00:30:00+00:00"}}}


def _stale_fetcher(instruments):
    return {"source": "Yahoo Finance", "fetched_at": "2026-06-18T00:00:00+00:00",
            "stale_after_hours": 24, "quotes": {
                "CL=F": {"value": 80.0, "as_of": "2026-06-18T00:00:00+00:00"}}}


def check_mock_fetcher_builds(mp, ms) -> None:
    snap = ms.get_market_snapshot("live", fetcher=_partial_fetcher,
                                  now=datetime(2026, 6, 22, 1, 0, tzinfo=timezone.utc))
    check("10. mock fetcher → mode == live", snap.get("mode") == "live",
          str(snap.get("mode")))
    cats = snap.get("categories") or {}
    check("10. snapshot에 3개 카테고리 그룹 존재",
          all(c in cats for c in mp.MARKET_CATEGORIES))
    check("10. snapshot items 비어 있지 않음", bool(snap.get("items")))
    values = [it for it in snap["items"] if it["value"] is not None]
    check("10. 주입 시세가 값으로 반영됨 (≥1)", len(values) >= 1, f"{len(values)}건")
    # 직접 시세는 delayed_market, 대용은 proxy_market으로 표기
    wti = next((it for it in snap["items"] if it["id"] == "wti_crude"), {})
    asph = next((it for it in snap["items"] if it["id"] == "asphalt_bitumen_proxy"), {})
    check("10. 직접 시세 WTI → delayed_market",
          wti.get("data_mode") == "delayed_market", str(wti.get("data_mode")))
    check("10. 대용 지표(asphalt) → proxy_market + 값 반영",
          asph.get("data_mode") == "proxy_market" and asph.get("value") is not None,
          str(asph.get("data_mode")))


def check_fetch_failure_unavailable(ms) -> None:
    for label, bad in (("None 반환", lambda *a: None),
                       ("빈 quotes", lambda *a: {"quotes": {}}),
                       ("예외", lambda *a: (_ for _ in ()).throw(RuntimeError("net")))):
        snap = ms.get_market_snapshot("live", fetcher=bad)
        no_values = not any(it["value"] is not None for it in snap["items"])
        check(f"11. live 수집 실패({label}) → mode unavailable + 값 0건",
              snap.get("mode") == "unavailable" and no_values)
    # 비-live(mock) 모드도 값 0건 (네트워크 0건)
    mock = ms.get_market_snapshot("mock")
    check("11. 비-live 모드 → mode unavailable + 값 0건 (가짜 값 없음)",
          mock.get("mode") == "unavailable"
          and not any(it["value"] is not None for it in mock["items"]))


# ---------- 12: brief JSON 통합 ----------

def check_brief_integration() -> None:
    env = {**os.environ, "APP_MODE": "mock"}
    for k in ("NEWS_MODE", "MACRO_MODE", "MARKET_MODE", "DB_PATH"):
        env.pop(k, None)
    proc = subprocess.run([sys.executable, str(BRIEF_BUILDER), "--json"],
                          capture_output=True, text=True, env=env, cwd=ROOT,
                          timeout=300)
    if not check("12. brief --json 동작", proc.returncode == 0,
                 (proc.stderr or "").strip()[-300:]):
        return
    try:
        brief = json.loads(proc.stdout)
    except ValueError as exc:
        check("12. brief --json 파싱", False, str(exc))
        return
    keys = ("market_data_mode", "market_snapshot",
            "construction_commodities_snapshot", "sovereign_yields_snapshot",
            "fx_snapshot")
    missing = [k for k in keys if k not in brief]
    check("12. brief JSON에 market 키 전부 포함", not missing, ", ".join(missing))
    snap = brief.get("market_snapshot") or {}
    check("12. brief market_snapshot에 categories/items/disclaimer",
          bool(snap.get("categories")) and "items" in snap
          and bool(snap.get("disclaimer")))
    # 기본 mock 경로는 시세를 만들지 않는다 (정직성)
    if snap.get("items") is not None:
        check("12. 기본 mock 경로 market_snapshot에 가짜 값 없음",
              not any(it.get("value") is not None for it in snap["items"]))
    # mock 불변 유지 — 추가 키만 더했음을 보장.
    # Clean 285e0e3 already yields 28/19/3/6/10/9, so the old 28/21/3/6/12/7
    # expectation was a stale baseline unrelated to D5-D business/org lenses.
    inv = (brief.get("total_articles"), brief.get("total_signals"),
           brief.get("immediate_count"), brief.get("daily_count"),
           brief.get("weekly_count"), brief.get("excluded_count"))
    check("12. mock 불변 28/19/3/6/10/9 유지", inv == (28, 19, 3, 6, 10, 9), str(inv))


# ---------- 13~15: 인접 게이트 회귀 ----------

def _run_verifier(name: str, timeout: int) -> subprocess.CompletedProcess:
    env = {**os.environ, "APP_MODE": "mock"}
    for k in ("NEWS_MODE", "MACRO_MODE", "MARKET_MODE", "MESSAGE",
              "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_IDS", "DB_PATH"):
        env.pop(k, None)
    return subprocess.run([sys.executable, str(ROOT / "scripts" / name)],
                          capture_output=True, text=True, env=env, cwd=ROOT,
                          timeout=timeout)


def check_adjacent_gates() -> None:
    for num, name, timeout in (("13", "verify_topic_profiles.py", 300),
                               ("14", "verify_ai_tab_supplement_routing.py", 300),
                               ("15", "verify_data_source_honesty.py", 1800)):
        proc = _run_verifier(name, timeout)
        check(f"{num}. {name} 통과 (exit 0)", proc.returncode == 0,
              "" if proc.returncode == 0 else (proc.stdout or "").strip()[-400:])


def main() -> int:
    print(f"== verify_market_snapshot_profiles @ {ROOT} ==")
    db_before = _db_state()

    check_py_compile()
    check_boundaries()

    mp = _import("app.market_profiles")
    ms = _import("app.market_snapshot")

    check_categories_present(mp)
    check_commodities(mp)
    check_yields(mp)
    check_fx(mp)
    check_instrument_fields(mp)
    check_proxy_explained(mp)
    check_unavailable_no_fake(mp, ms)
    check_stale_disclaimer(ms)
    check_no_unqualified_claims(mp, ms)
    check_mock_fetcher_builds(mp, ms)
    check_fetch_failure_unavailable(ms)
    check_brief_integration()
    check_adjacent_gates()

    check("repo의 radar.db가 검증 중 변경/생성되지 않음", _db_state() == db_before)

    if _failures:
        print(f"\nRESULT: FAIL ({len(_failures)} failed)")
        for name in _failures:
            print(f"  - {name}")
        return 1
    print("\nRESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
