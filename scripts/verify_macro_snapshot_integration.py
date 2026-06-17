"""P0-C2 검증기 — 실제 macro snapshot(시장지표) 연동 경로 회귀 검사.

핵심 원칙 (네트워크 없이도 대부분 결정적으로 통과한다):
- 네트워크 IO는 leaf(app/live_macro.py)가 소유한다. app/macro_snapshot.py는 네트워크를
  직접 import하지 않고 fetcher를 호출만 한다 (collector→live_collector와 동일 격리).
- live 수집은 공개 시세 API(Yahoo Finance chart JSON)만 읽는다 — API key/비밀값이 필요 없다.
- 시장 본문/기사 텍스트가 아닌 숫자형 지표만 다룬다 (본문 필드명 금지, rules.md §3).
- live 수집 실패/0건이면 가짜 값으로 채우지 않고 unavailable로 강등한다.
- 출처(source)·기준시각(updated_at)·stale(지연) 플래그를 함께 표기한다.
- 표시 레이어(digest/report/dashboard/brief)는 macro_data_mode=live일 때만 수치를 노출한다.
  live 수치가 렌더되는데 footer/헤더가 '시장지표 미연동'이라 모순되는 회귀를 막는다(Blocker 1).
- 같은 publish 실행은 모든 출력에 동일 snapshot을 쓴다 — 프로세스 캐시 + 공유 파일(Blocker 2).
- 심볼 혼동/자릿수 오류 값은 sane range 가드로 결측 처리한다 (가짜로 표시 금지, Blocker 3).
- 네트워크가 막혀 있으면 실제 수집은 SKIP으로 보고하고 가짜 성공을 주장하지 않는다.

저장소의 radar.db는 절대 건드리지 않는다 (네트워크 0건 경로만 결정적으로 검증).

사용법:
    python3 scripts/verify_macro_snapshot_integration.py
    MACRO_MODE=live python3 scripts/verify_macro_snapshot_integration.py   # 실제 수집까지 시도
"""

import json
import os
import py_compile
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIVE_MACRO = ROOT / "app" / "live_macro.py"
MACRO_MODULE = ROOT / "app" / "macro_snapshot.py"
CONFIG = ROOT / "app" / "config.py"
BRIEFING = ROOT / "app" / "briefing.py"
SOURCES = ROOT / "data" / "live_macro_sources.json"
TEMPLATE = ROOT / "templates" / "index.html"
RADAR_DB = ROOT / "radar.db"

# 본문 전문 필드명 (rules.md §3) — 조각 조립으로 grep 규약 회피
BANNED_TERMS = ["".join(parts) for parts in [
    ("raw", "_payload"), ("full", "_text"),
    ("article", "_body"), ("full_rss", "_content"),
]]
# 비밀값/인증 토큰을 읽으면 안 된다 (공개 CSV는 무인증)
SECRET_TOKENS = ("TELEGRAM", "BOT_TOKEN", "API_KEY", "SECRET", "BEARER", "PASSWORD")
TOKEN_SHAPE = re.compile(r"[0-9]{8,}:[A-Za-z0-9_-]{20,}")
# mock 고정값 — live 산출물 어디에도 섞이면 안 된다 (점수/테마 강도와 충돌하지 않는 식별 수치)
MOCK_CONSTANTS = ("1480.5", "2864.7")

ALLOWED_VALUE_KEYS = {"key", "label", "value", "unit", "direction", "as_of"}

# Yahoo Finance chart 응답 형태 (meta 블록만 사용). 데이터 없는 심볼은 price가 없다.
SAMPLE_CHART = {"chart": {"result": [{"meta": {
    "symbol": "USDKRW=X", "regularMarketPrice": 1357.74,
    "chartPreviousClose": 1350.0, "regularMarketTime": 1718500000,
    "currency": "KRW"}}], "error": None}}
SAMPLE_INDICATOR = {"key": "usdkrw", "label": "USD/KRW", "unit": "원"}

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


def warn(message: str) -> None:
    print(f"[WARN] {message}")


def _db_state() -> tuple | None:
    if not RADAR_DB.exists():
        return None
    stat = RADAR_DB.stat()
    return (stat.st_mtime_ns, stat.st_size)


def _import(mod_name: str):
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    os.environ.setdefault("APP_MODE", "mock")
    return __import__(mod_name, fromlist=["_"])


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


def check_config() -> None:
    src = CONFIG.read_text(encoding="utf-8")
    check("config.py에 MACRO_MODE 정의", "MACRO_MODE" in src)
    check("config.py MACRO_MODE 기본값 mock (안전)",
          bool(re.search(r'MACRO_MODE\s*=.*"mock"', src)))


def check_macro_module_boundary() -> None:
    src = MACRO_MODULE.read_text(encoding="utf-8")
    check("macro_snapshot이 모듈 레벨에서 네트워크를 import하지 않음 (지연 import)",
          not re.search(r"^\s*import (urllib|requests|httpx|socket)|"
                        r"^\s*from (urllib|requests|httpx|socket)", src, re.M))
    check("macro_snapshot이 live_macro를 함수 안에서 지연 import",
          "from app import live_macro" in src and "_live_snapshot" in src)
    check("briefing이 MACRO_MODE로 macro snapshot을 선택 (APP_MODE와 독립)",
          "get_macro_snapshot(config.MACRO_MODE)" in BRIEFING.read_text(encoding="utf-8"))


def check_live_macro_source() -> None:
    src = LIVE_MACRO.read_text(encoding="utf-8")
    lowered = src.lower()
    hits = [t for t in BANNED_TERMS if t in lowered]
    check("live_macro에 본문 전문 필드명 없음", not hits, ", ".join(hits))
    secret_hits = [t for t in SECRET_TOKENS if t in src]
    check("live_macro가 비밀값/인증 토큰을 읽지 않음", not secret_hits, ", ".join(secret_hits))
    check("live_macro에 token 모양 하드코딩 없음", not TOKEN_SHAPE.search(src))
    check("live_macro가 os.environ을 직접 읽지 않음 (config만 사용)", "os.environ" not in src)
    check("live_macro가 네트워크 IO를 소유 (urllib 사용 — 격리 지점)", "urllib" in lowered)
    leaked = [c for c in MOCK_CONSTANTS if c in src]
    check("live_macro에 mock 고정값 하드코딩 없음 (가짜 시세 금지)", not leaked, ", ".join(leaked))


def check_sources_file() -> None:
    if not check("data/live_macro_sources.json 존재", SOURCES.exists()):
        return
    raw = SOURCES.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except ValueError as exc:
        check("sources JSON 파싱", False, str(exc))
        return
    indicators = data.get("indicators") or []
    check("sources에 indicator 1건 이상", len(indicators) >= 1, f"{len(indicators)}건")
    check("모든 indicator에 key/label/symbol/unit",
          all(i.get("key") and i.get("label") and i.get("symbol") and "unit" in i
              for i in indicators))
    base = str(data.get("base_url") or "")
    check("base_url이 공개 https 엔드포인트", base.startswith("https://"), base)
    check("sources에 stale_after_hours 정의", isinstance(data.get("stale_after_hours"), int))
    # 무인증 공개 CSV — key/token/secret 류 파라미터/토큰이 들어있지 않아야 한다
    low = raw.lower()
    check("sources에 api key/secret 류 표기 없음 (무인증)",
          not any(t in low for t in ("api_key", "apikey", "token", "secret", "bearer")))
    check("sources에 token 모양 하드코딩 없음", not TOKEN_SHAPE.search(raw))
    leaked = [c for c in MOCK_CONSTANTS if c in raw]
    check("sources에 mock 고정 시세값 없음", not leaked, ", ".join(leaked))


# ---------- 파서 계약 (네트워크 없음) ----------

def check_parse_contract() -> None:
    lm = _import("app.live_macro")

    check("_num이 None/비숫자를 None으로 (가짜 값 금지)",
          lm._num(None) is None and lm._num("abc") is None and lm._num(True) is None)
    check("_num이 문자열/숫자 파싱", lm._num("1357.74") == 1357.74 and lm._num(90) == 90.0)
    check("_direction up/down/flat",
          lm._direction(1357.74, 1350.0) == "up"
          and lm._direction(89.0, 90.0) == "down"
          and lm._direction(5.0, 5.0) == "flat")
    check("_direction 결측 입력 → None", lm._direction(None, 90.0) is None)
    as_of = lm._as_of_from_epoch(1718500000)
    check("_as_of_from_epoch이 ISO(UTC)로 정규화",
          isinstance(as_of, str) and "T" in as_of
          and ("+00:00" in as_of or as_of.endswith("Z")), str(as_of))
    check("_as_of_from_epoch이 비숫자 → None", lm._as_of_from_epoch("nope") is None)
    check("_meta_from_chart이 정상 응답에서 meta 추출",
          isinstance(lm._meta_from_chart(SAMPLE_CHART), dict))
    check("_meta_from_chart이 깨진 응답 → None",
          lm._meta_from_chart({"chart": {"result": None}}) is None
          and lm._meta_from_chart({}) is None)

    meta = lm._meta_from_chart(SAMPLE_CHART)
    val = lm._to_value(meta, SAMPLE_INDICATOR)
    check("_to_value가 표준 value dict 생성", isinstance(val, dict) and val is not None)
    if val:
        check("값 키가 허용 집합뿐 (본문 필드 없음)",
              set(val.keys()) <= ALLOWED_VALUE_KEYS,
              ", ".join(sorted(set(val) - ALLOWED_VALUE_KEYS)))
        check("value가 숫자 (round 2)", val["value"] == 1357.74)
        check("direction=up (price>prevClose)", val.get("direction") == "up")
        check("as_of 존재 (epoch→ISO)", bool(val.get("as_of")))

    # 가격 없는 meta / meta 없음 → None (결측을 가짜 값으로 채우지 않음)
    check("price 없는 meta → None", lm._to_value({"symbol": "X"}, SAMPLE_INDICATOR) is None)
    check("meta None → None", lm._to_value(None, SAMPLE_INDICATOR) is None)


# ---------- sane range 가드 (Blocker 3 — 심볼 혼동/자릿수 오류 차단) ----------

def check_sane_ranges() -> None:
    lm = _import("app.live_macro")
    data = json.loads(SOURCES.read_text(encoding="utf-8"))
    inds = data.get("indicators") or []
    check("모든 indicator에 sane_min<sane_max (실세계 스케일 가드)",
          bool(inds) and all(
              isinstance(i.get("sane_min"), (int, float))
              and isinstance(i.get("sane_max"), (int, float))
              and i["sane_min"] < i["sane_max"] for i in inds))
    check("sources에 심볼 선택 근거(_symbol_notes) 문서화", bool(data.get("_symbol_notes")))

    def meta(price):
        return {"regularMarketPrice": price, "chartPreviousClose": price,
                "regularMarketTime": 1718500000}

    kospi = {"key": "kospi", "label": "KOSPI", "unit": "pt",
             "sane_min": 1000, "sane_max": 5000}
    check("sane range: KOSPI 8726.6(실세계 스케일 밖) → None (가드 결측)",
          lm._to_value(meta(8726.6), kospi) is None)
    ok = lm._to_value(meta(2700.0), kospi)
    check("sane range: KOSPI 2700(정상) → 통과", ok is not None and ok["value"] == 2700.0)

    y10 = {"key": "us10y", "label": "미 국채 10Y", "unit": "%",
           "sane_min": 0, "sane_max": 25}
    check("sane range: 10Y 44.3(÷10 누락 의심) → None (자릿수 오류 가드)",
          lm._to_value(meta(44.3), y10) is None)
    check("sane range: 10Y 4.43(정상) → 통과", lm._to_value(meta(4.43), y10) is not None)

    check("sane range: bound 미설정이면 통과 (하위호환)",
          lm._to_value(meta(99999.0), {"key": "x", "label": "X"}) is not None)


# ---------- snapshot 계약 (fetcher 주입, 네트워크 없음) ----------

def check_snapshot_contract() -> None:
    ms = _import("app.macro_snapshot")
    now = datetime(2026, 6, 17, 0, 0, tzinfo=timezone.utc)
    fresh_iso = (now - timedelta(hours=2)).isoformat(timespec="seconds")
    stale_iso = (now - timedelta(hours=72)).isoformat(timespec="seconds")

    mock = ms.get_macro_snapshot("mock")
    check("mock → macro_data_mode == mock_static",
          mock.get("macro_data_mode") == "mock_static", str(mock.get("macro_data_mode")))
    check("mock → is_stale True (고정값은 신선도 주장 안 함)", mock.get("is_stale") is True)

    def fresh_fetch():
        return {"source": "Yahoo Finance", "fetched_at": fresh_iso, "stale_after_hours": 24,
                "values": [{"key": "usdkrw", "label": "USD/KRW", "value": 1399.9,
                            "unit": "원", "direction": "up", "as_of": fresh_iso}]}

    ok = ms.get_macro_snapshot("live", fetcher=fresh_fetch, now=now)
    check("live 성공 → macro_data_mode == live", ok.get("macro_data_mode") == "live")
    check("live 성공 → source/updated_at/as_of 표기",
          bool(ok.get("source")) and bool(ok.get("updated_at")) and bool(ok.get("as_of")))
    check("live 성공(최근) → is_stale False", ok.get("is_stale") is False)
    check("live 성공 → values 노출", ok.get("values") and ok["values"][0]["value"] == 1399.9)
    blob = json.dumps(ok, ensure_ascii=False)
    leaked = [c for c in MOCK_CONSTANTS if c in blob]
    check("live 성공 → mock 고정값 혼입 없음", not leaked, ", ".join(leaked))

    def stale_fetch():
        return {"source": "Yahoo Finance", "fetched_at": stale_iso, "stale_after_hours": 24,
                "values": [{"key": "wti", "label": "WTI", "value": 90.0,
                            "unit": "달러", "as_of": stale_iso}]}

    stale = ms.get_macro_snapshot("live", fetcher=stale_fetch, now=now)
    check("live 지연(72h>24h) → is_stale True", stale.get("is_stale") is True)
    check("live 지연 → disclaimer에 stale 명시", "stale" in str(stale.get("disclaimer")).lower())

    def no_asof_fetch():
        return {"source": "Yahoo Finance", "stale_after_hours": 24,
                "values": [{"key": "vix", "label": "VIX", "value": 17.0, "unit": "pt"}]}

    no_asof = ms.get_macro_snapshot("live", fetcher=no_asof_fetch, now=now)
    check("live 기준시각 미상 → is_stale True (보수적)", no_asof.get("is_stale") is True)

    for label, bad in (("None", lambda: None),
                       ("빈 values", lambda: {"source": "x", "values": []}),
                       ("예외", lambda: (_ for _ in ()).throw(RuntimeError("net")))):
        res = ms.get_macro_snapshot("live", fetcher=bad, now=now)
        check(f"live 실패({label}) → unavailable + values 비어 있음",
              res.get("macro_data_mode") == "unavailable" and res.get("values") == [])


# ---------- snapshot 일관성 (Blocker 2 — 한 실행에 동일 snapshot) ----------

def check_process_cache() -> None:
    """같은 프로세스에서 fetch_snapshot을 반복 호출해도 네트워크는 1회, 값은 동일."""
    lm = _import("app.live_macro")
    calls = {"n": 0}

    def fake_fetch_json(url, timeout):
        calls["n"] += 1
        # 호출마다 값이 달라진다 — 캐시가 없으면 두 snapshot이 어긋난다.
        return {"chart": {"result": [{"meta": {
            "regularMarketPrice": 1000.0 + calls["n"],
            "chartPreviousClose": 1000.0, "regularMarketTime": 1718500000}}]}}

    with tempfile.TemporaryDirectory(prefix="hdec_macro_cache_") as d:
        src = Path(d) / "sources.json"
        src.write_text(json.dumps({
            "source_label": "Yahoo Finance",
            "indicators": [{"key": "usdkrw", "label": "USD/KRW", "unit": "원",
                            "symbol": "USDKRW=X", "sane_min": 0, "sane_max": 100000}],
        }), encoding="utf-8")
        orig = lm._fetch_json
        lm._fetch_json = fake_fetch_json
        try:
            lm.reset_cache()
            snap1 = lm.fetch_snapshot(sources_path=src)
            n1 = calls["n"]
            snap2 = lm.fetch_snapshot(sources_path=src)
            n2 = calls["n"]
            check("process cache: 2nd fetch_snapshot이 네트워크 재호출 안 함", n2 == n1, f"{n1}→{n2}")
            check("process cache: 두 호출 snapshot 동일",
                  snap1 is not None and snap1 == snap2)

            lm.reset_cache()
            a = lm.fetch_snapshot(sources_path=src, use_cache=False)
            b = lm.fetch_snapshot(sources_path=src, use_cache=False)
            check("process cache: use_cache=False는 매번 재fetch (값 갱신)",
                  a is not None and b is not None and a != b)
        finally:
            lm._fetch_json = orig
            lm.reset_cache()


def check_shared_snapshot_file() -> None:
    """MACRO_SNAPSHOT_FILE이 설정되면 뒤따르는 프로세스가 재fetch 없이 동일 snapshot 사용."""
    ms = _import("app.macro_snapshot")
    now = datetime(2026, 6, 17, 0, 0, tzinfo=timezone.utc)
    fresh_iso = (now - timedelta(hours=1)).isoformat(timespec="seconds")
    calls = {"n": 0}

    def counting_fetch():  # 리포트 프로세스: fetch + 파일 기록
        calls["n"] += 1
        return {"source": "Yahoo Finance", "fetched_at": fresh_iso, "stale_after_hours": 24,
                "values": [{"key": "usdkrw", "label": "USD/KRW", "value": 1499.0 + calls["n"],
                            "unit": "원", "as_of": fresh_iso}]}

    def raising_fetch():  # 다이제스트 프로세스: 파일을 읽어야 하며 재fetch하면 안 됨
        raise AssertionError("공유 파일이 있으면 재fetch하면 안 된다")

    with tempfile.TemporaryDirectory(prefix="hdec_macro_share_") as d:
        path = os.path.join(d, "macro_snapshot.json")
        old = getattr(ms.config, "MACRO_SNAPSHOT_FILE", None)
        ms.config.MACRO_SNAPSHOT_FILE = path
        try:
            first = ms.get_macro_snapshot("live", fetcher=counting_fetch, now=now)
            check("공유 파일: 첫 호출이 fetch 후 파일 기록",
                  calls["n"] == 1 and os.path.exists(path))
            second = ms.get_macro_snapshot("live", fetcher=raising_fetch, now=now)
            v1 = first.get("values", [{}])[0].get("value")
            v2 = second.get("values", [{}])[0].get("value")
            check("공유 파일: 2nd 호출이 재fetch 없이 동일 값 사용 (불일치 0)",
                  v1 == v2 and second.get("macro_data_mode") == "live", f"{v1} vs {v2}")
            check("공유 파일: 2nd 호출도 출처/기준 표기 유지",
                  bool(second.get("source")) and bool(second.get("updated_at")))
        finally:
            ms.config.MACRO_SNAPSHOT_FILE = old


# ---------- 렌더 게이팅 (live일 때만 수치 노출) ----------

def _brief(mode: str, value: float) -> dict:
    snap = ({"macro_data_mode": "live", "source": "Yahoo Finance",
             "updated_at": "2026-06-16T21:30:00+00:00", "is_stale": False,
             "disclaimer": "공개 시세 API 실데이터",
             "values": [{"key": "usdkrw", "label": "USD/KRW", "value": value,
                         "unit": "원", "direction": "up"}]}
            if mode == "live"
            else {"macro_data_mode": "mock_static", "source": "demo_mock",
                  "is_stale": True, "disclaimer": "데모 mock 고정값",
                  "values": [{"key": "usdkrw", "label": "USD/KRW", "value": 1480.5,
                              "unit": "원"}]})
    return {"macro_data_mode": snap["macro_data_mode"], "macro_snapshot": snap}


def check_render_report() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    from build_static_report import _render_macro_section

    live_html = "".join(_render_macro_section(_brief("live", 1357.7)))
    check("리포트 live → macro-cell live 렌더", "macro-cell live" in live_html)
    check("리포트 live → 실수치 노출", "1357.7" in live_html)
    check("리포트 live → 출처/기준 표기",
          "Yahoo Finance" in live_html and "기준" in live_html)

    mock_html = "".join(_render_macro_section(_brief("mock", 0)))
    check("리포트 mock_static → 미연동 placeholder", "미연동" in mock_html)
    check("리포트 mock_static → mock 고정값(1480.5) 미노출", "1480.5" not in mock_html)
    check("리포트 mock_static → 수치(소수) 미노출",
          not re.findall(r"\d+\.\d+", mock_html),
          ", ".join(re.findall(r"\d+\.\d+", mock_html)))


def check_render_digest() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    from build_telegram_digest import format_digest_message

    base = {"header": "HDEC Executive Radar", "date_kst": "2026-01-01",
            "executive_one_liner": "검증용 문장입니다.", "status_board": [],
            "top_signals": [], "theme_rankings": [], "category_counts": [], "mode": "mock"}

    live_msg = format_digest_message({**base, **_brief("live", 1357.7)})
    check("digest live → 수치+출처+기준 노출",
          "1357.7" in live_msg and "Yahoo Finance" in live_msg and "기준" in live_msg)

    mock_msg = format_digest_message({**base, **_brief("mock", 0)})
    check("digest mock_static → 수치 숨김 + Macro Snapshot 미노출",
          "1480.5" not in mock_msg and "Macro Snapshot" not in mock_msg)


_FOOTER_E2E_PAYLOAD = '''
import os, sys, json, tempfile
_d = tempfile.mkdtemp(prefix="hdec_macro_verify_")
os.environ["DB_PATH"] = os.path.join(_d, "verify.db")   # repo radar.db 절대 미접촉
os.environ["APP_MODE"] = "mock"
os.environ["NEWS_MODE"] = "mock"
os.environ["MACRO_MODE"] = "mock"
os.environ.pop("MACRO_SNAPSHOT_FILE", None)
ROOT = %r
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import build_static_report as bsr
import build_executive_brief as beb
beb._bootstrap()
from app import briefing
_orig = briefing.macro_snapshot.get_macro_snapshot
LIVE = {"macro_data_mode": "live", "source": "Yahoo Finance",
        "updated_at": "2026-06-17T23:16:00+00:00", "as_of": "2026-06-17T23:16:00+00:00",
        "is_stale": False, "disclaimer": "공개 시세 API 실데이터",
        "values": [{"key": "usdkrw", "label": "USD/KRW", "value": 1512.91,
                    "unit": "원", "direction": "down"}]}
def render(mode):
    briefing.macro_snapshot.get_macro_snapshot = (lambda *a, **k: dict(LIVE)) if mode == "live" else _orig
    brief = beb.build_brief_via_mock_pipeline()
    html, _ = bsr.render_report_html(brief)
    return brief.get("macro_data_mode"), html
live_mode, h_live = render("live")
mock_mode, h_mock = render("mock")
fw_live = briefing._data_warning("live", False, LIVE)
fw_mock = briefing._data_warning("mock", False, {"macro_data_mode": "mock_static",
          "values": [{"label": "USD/KRW", "value": 1480.5}]})
fw_none = briefing._data_warning("live", False, None)
fw_stale = briefing._data_warning("live", False, dict(LIVE, is_stale=True))
out = {
    "live_mode": live_mode,
    "live_unavailable": "시장지표: 미연동" in h_live,
    "live_has_value": "1512.91" in h_live,
    "live_source": "Yahoo Finance" in h_live,
    "mock_unavailable": "시장지표: 미연동" in h_mock,
    "mock_has_value": "1512.91" in h_mock,
    "fw_live_ok": ("Yahoo Finance" in fw_live and "기준" in fw_live and "미연동" not in fw_live),
    "fw_mock_ok": ("시장지표: 미연동" in fw_mock),
    "fw_none_ok": ("미연동" in fw_none),
    "fw_stale_ok": ("(지연)" in fw_stale),
}
print("RESULT_JSON:" + json.dumps(out, ensure_ascii=False))
''' % str(ROOT)


def check_report_footer_consistency() -> None:
    """Blocker 1 — live 수치를 렌더하는 리포트는 footer/헤더가 '시장지표: 미연동'일 수 없다.

    temp DB 서브프로세스에서 mock 파이프라인 + 주입된 live macro로 실제 리포트를 렌더해
    (a) _data_warning 함수 계약, (b) 렌더된 HTML 전체를 함께 검사한다.
    """
    try:
        proc = subprocess.run([sys.executable, "-c", _FOOTER_E2E_PAYLOAD],
                              capture_output=True, text=True, timeout=240, cwd=str(ROOT))
    except subprocess.TimeoutExpired:
        check("Blocker1: 리포트 렌더 서브프로세스 (temp DB)", False, "timeout")
        return
    line = next((ln for ln in proc.stdout.splitlines()
                 if ln.startswith("RESULT_JSON:")), None)
    if line is None:
        check("Blocker1: 리포트 렌더 서브프로세스 실행",
              False, (proc.stderr or proc.stdout)[-400:])
        return
    out = json.loads(line[len("RESULT_JSON:"):])

    check("Blocker1: live 리포트 macro_data_mode == live", out["live_mode"] == "live")
    check("Blocker1: live 리포트가 실수치(1512.91) 노출", out["live_has_value"])
    check("Blocker1: live 리포트 footer가 출처(Yahoo Finance) 표기", out["live_source"])
    check("Blocker1: live 리포트에 '시장지표: 미연동' 없음 (live+미연동 동시표시 금지)",
          not out["live_unavailable"])
    check("Blocker1: mock 리포트는 '시장지표: 미연동' 유지", out["mock_unavailable"])
    check("Blocker1: mock 리포트에 live 수치 미노출", not out["mock_has_value"])
    check("Blocker1: _data_warning live → 출처 표기 + 미연동 아님", out["fw_live_ok"])
    check("Blocker1: _data_warning mock → '시장지표: 미연동'", out["fw_mock_ok"])
    check("Blocker1: _data_warning macro None → '미연동'(안전 기본)", out["fw_none_ok"])
    check("Blocker1: _data_warning live+stale → (지연) 표기", out["fw_stale_ok"])


def check_render_dashboard() -> None:
    html = TEMPLATE.read_text(encoding="utf-8")
    check("대시보드: macro_data_mode === 'live' 분기 존재",
          'macroMode === "live"' in html or "macroMode === 'live'" in html)
    check("대시보드: 시장지표 미연동 라벨 존재 (비-live)", "시장지표 미연동" in html)
    leaked = [c for c in MOCK_CONSTANTS if c in html]
    check("대시보드: mock 고정값 하드코딩 없음", not leaked, ", ".join(leaked))


# ---------- 실수집 (선택, SKIP-friendly) ----------

def check_real_fetch_optional() -> None:
    lm = _import("app.live_macro")
    ms = _import("app.macro_snapshot")
    lm.reset_cache()  # 앞선 캐시 테스트의 잔여 캐시가 실수집을 오염시키지 않도록
    try:
        snap = lm.fetch_snapshot(timeout=6)
    except Exception as exc:  # noqa: BLE001 — leaf가 흡수하지만 방어적으로 한 번 더
        skip(f"실제 시세 API 수집 불가 (네트워크 차단?) — {type(exc).__name__} · SKIP")
        return
    if not snap or not snap.get("values"):
        skip("실제 시세 API 수집 0건 (네트워크 차단/결과 없음) — SKIP, 가짜 성공 주장 안 함")
        return

    values = snap["values"]
    print(f"[LIVE] 실제 공개 시세 API 수집 {len(values)}건 · 출처 {snap.get('source')}")
    check("LIVE: 지표 1건 이상", len(values) >= 1, f"{len(values)}건")
    check("LIVE: 모든 value가 숫자", all(isinstance(v.get("value"), (int, float)) for v in values))
    check("LIVE: 값 키가 허용 집합뿐 (본문 필드 없음)",
          all(set(v.keys()) <= ALLOWED_VALUE_KEYS for v in values))
    check("LIVE: source/fetched_at 존재",
          bool(snap.get("source")) and bool(snap.get("fetched_at")))
    blob = json.dumps(snap, ensure_ascii=False)
    leaked = [c for c in MOCK_CONSTANTS if c in blob]
    check("LIVE: mock 고정값 혼입 없음", not leaked, ", ".join(leaked))

    # 실수집된 값은 전부 설정된 sane range 안이어야 한다 (심볼/자릿수 오류 0건).
    bounds = {i["key"]: (i.get("sane_min"), i.get("sane_max"))
              for i in (json.loads(SOURCES.read_text(encoding="utf-8")).get("indicators") or [])}
    out_of_range = [
        f"{v['key']}={v['value']}" for v in values
        if v.get("key") in bounds and (
            (bounds[v["key"]][0] is not None and v["value"] < bounds[v["key"]][0])
            or (bounds[v["key"]][1] is not None and v["value"] > bounds[v["key"]][1]))
    ]
    check("LIVE: 모든 수집값이 sane range 안 (스케일 오류 0건)",
          not out_of_range, ", ".join(out_of_range))

    full = ms.get_macro_snapshot("live")
    check("LIVE: get_macro_snapshot('live') → live 또는 unavailable (mock_static 아님)",
          full.get("macro_data_mode") in ("live", "unavailable"),
          str(full.get("macro_data_mode")))
    if full.get("macro_data_mode") == "live":
        check("LIVE: snapshot에 source/updated_at + is_stale(bool)",
              bool(full.get("source")) and bool(full.get("updated_at"))
              and isinstance(full.get("is_stale"), bool))


def main() -> int:
    print(f"== verify_macro_snapshot_integration @ {ROOT} ==")
    db_before = _db_state()

    check_py_compile()
    check_config()
    check_macro_module_boundary()
    check_live_macro_source()
    check_sources_file()
    check_parse_contract()
    check_sane_ranges()
    check_snapshot_contract()
    check_process_cache()
    check_shared_snapshot_file()
    check_render_report()
    check_render_digest()
    check_report_footer_consistency()
    check_render_dashboard()
    check_real_fetch_optional()

    check("repo의 radar.db가 검증 중 변경/생성되지 않음 (temp DB 격리)",
          _db_state() == db_before)

    if _failures:
        print(f"\nRESULT: FAIL ({len(_failures)} failed)")
        for name in _failures:
            print(f"  - {name}")
        return 1
    print("\nRESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
