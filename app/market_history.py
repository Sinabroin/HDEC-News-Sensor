"""Market History 도메인 (leaf) — 시장지표의 기간별(1주/1개월/3개월/1년) 히스토리 제공 (D7-F).

대시보드 시장 기간 버튼(1주·1개월·3개월·1년)을 실제로 동작시키기 위한 per-period 시계열을
공급한다. app/live_market.py(현재값 leaf)와 같은 경계 원칙을 따른다:

- 네트워크 IO는 이 leaf만 한다(공개·무료 Yahoo chart JSON). API key/비밀값이 필요 없다.
- 다른 app 도메인을 import하지 않는다(설정/심볼을 자체 보유). DB·점수·insight·발송 0건.
- live 수집이 실패하면 가짜 live 값을 만들지 않는다 — 해당 종목은 demo 픽스처로 두되
  history_data_mode를 'mock_demo'로 정직하게 표기한다(live로 위장 금지).

두 가지 출처(history_data_mode):
- mock_demo      : 오프라인/데모용 **결정적** 픽스처. 1주/1개월/3개월/1년이 각각 다른 창(window)
                   이며(가짜 resample 아님), '현재 체결값 아님 · 데모'로 표기된다.
- delayed_market : live 모드에서 받은 **실제** 공개 시세(지연) 히스토리. 각 기간이 실제로
                   서로 다른 날짜 구간(실측 배열)이다.

핵심 계약: 어떤 모드든 1주/1개월/3개월/1년은 **서로 다른 배열**이다(특히 3개월 ≠ 1년).
하나의 베이스를 길이만 바꿔 resample하지 않는다.

엔트리 구조 (대시보드 market_item에 부착):
    {
      "history": {"1w": [...], "1m": [...], "3m": [...], "1y": [...]},
      "history_source": "<출처 라벨>",
      "history_data_mode": "mock_demo" | "delayed_market",
      "history_updated_at": "<YYYY-MM-DD | ISO>",
      "history_decimals": <int>,
    }
"""

import json
import math
import urllib.parse
import urllib.request
from datetime import datetime, timezone

# 기간 키와 표시 길이(포인트 수) — 서로 다른 창임을 길이로도 드러낸다.
# 1주=일별 7, 1개월=일별 22, 3개월=주별 13, 1년=월별 12 (실측 샘플링 간격과 동형).
PERIOD_KEYS = ("1w", "1m", "3m", "1y")
PERIOD_LEN = {"1w": 7, "1m": 22, "3m": 13, "1y": 12}
# 기간별 데모 추세 폭(끝점=현재값 기준 시작점까지의 상대 이동) — 장기일수록 크게.
_PERIOD_DRIFT = {"1w": 0.006, "1m": 0.018, "3m": 0.045, "1y": 0.115}

DEMO_SOURCE = "데모 픽스처(결정적)"
DEMO_MODE = "mock_demo"
LIVE_SOURCE = "Yahoo Finance (지연)"
LIVE_MODE = "delayed_market"
# 데모 픽스처의 기준일(결정적) — now() 의존을 피한다. 모델 published 날짜와 맞춘다.
DEMO_AS_OF = "2026-06-22"

# Yahoo chart (공개·무인증) — live_market.py와 동일 패턴.
USER_AGENT = ("Mozilla/5.0 (compatible; HDEC-Executive-Radar/0.1; "
              "+public-quote-json; non-crawling)")
DEFAULT_TIMEOUT = 8
DEFAULT_BASE_URL = "https://query1.finance.yahoo.com/v8/finance/chart/"
# live 1회 요청으로 1년 일별 시계열을 받아 4개 기간 창으로 슬라이스한다(심볼당 1요청).
LIVE_RANGE_PARAMS = {"interval": "1d", "range": "1y"}


# ---------------------------------------------------------------------------
# 지원 종목 카탈로그 — 대시보드 market_item id 기준 (market_profiles와 별개의 데모 유니버스).
# symbol이 있는 종목만 live 연동 가능. base는 데모 픽스처의 현재값 앵커(표시값과 일치).
# scale: live 종가 → 표시 단위 환산(예: COMEX 구리 USD/lb → LME식 USD/t = ×2204.62).
# ---------------------------------------------------------------------------
class _Spec:
    __slots__ = ("id", "symbol", "base", "decimals", "scale", "sane_min", "sane_max", "unit")

    def __init__(self, id, symbol, base, decimals, scale, sane_min, sane_max, unit):
        self.id = id
        self.symbol = symbol
        self.base = base
        self.decimals = decimals
        self.scale = scale
        self.sane_min = sane_min
        self.sane_max = sane_max
        self.unit = unit


_SUPPORTED = [
    _Spec("usdkrw", "USDKRW=X", 1536.8, 1, 1.0, 500, 3000, "원"),
    _Spec("jpykrw", "JPYKRW=X", 9.74, 2, 1.0, 3, 30, "원"),
    _Spec("wti", "CL=F", 78.4, 1, 1.0, 5, 250, "USD/bbl"),
    _Spec("brent", "BZ=F", 82.1, 1, 1.0, 5, 250, "USD/bbl"),
    _Spec("copper", "HG=F", 9180.0, 0, 2204.62, 1000, 30000, "USD/t"),
    _Spec("us_5y", "^FVX", 4.22, 2, 1.0, 0, 25, "%"),
    _Spec("us_10y", "^TNX", 4.45, 2, 1.0, 0, 25, "%"),
    # ── D7-G: 카테고리별 커버리지 확대 — 공개·무료 Yahoo chart로 실제 히스토리를 받는 종목만
    # 추가한다(심볼은 test-fetch로 검증, 가짜 심볼 없음). PROXY_IDS는 글로벌 벤치마크라 정확한
    # 국내 현물가가 아니며 템플릿이 '대용(proxy)'으로 표기한다(여기선 시계열만 공급).
    # 원자재·금속: 알루미늄(COMEX Aluminum, USD/t).
    _Spec("aluminum", "ALI=F", 2620.0, 0, 1.0, 1000, 6000, "USD/t"),
    # 철강·건자재: 철광석(62% CFR China TSI)·열연강판(미 Midwest HRC) — 둘 다 글로벌 벤치마크 대용.
    _Spec("iron_ore", "TIO=F", 100.5, 1, 1.0, 30, 400, "USD/t"),
    _Spec("hrc_steel", "HRC=F", 1193.0, 0, 1.0, 200, 3000, "USD/t"),
    # 석탄: 연료탄(API2 ARA, ARGUS-McCloskey) — 국내 도입가 아닌 유럽 인도 벤치마크 대용.
    _Spec("thermal_coal", "MTF=F", 105.0, 0, 1.0, 30, 600, "USD/t"),
    # 유가·정제유: 휘발유(RBOB, USD/gal)·경유(난방유 HO×42 = USD/bbl 환산 대용).
    _Spec("gasoline", "RB=F", 2.34, 2, 1.0, 0.5, 10, "USD/gal"),
    _Spec("diesel_gasoil", "HO=F", 130.4, 1, 42.0, 30, 400, "USD/bbl"),
    # 가스·LNG: 헨리허브(USD/MMBtu)·TTF(EUR/MWh).
    _Spec("henry_hub", "NG=F", 2.86, 2, 1.0, 0.5, 30, "USD/MMBtu"),
    _Spec("ttf_gas", "TTF=F", 34.5, 1, 1.0, 5, 200, "EUR/MWh"),
    # 환율: EUR/KRW·GBP/KRW (공개 무료 Yahoo FX).
    _Spec("eurkrw", "EURKRW=X", 1648.2, 1, 1.0, 800, 4000, "원"),
    _Spec("gbpkrw", "GBPKRW=X", 2034.9, 1, 1.0, 800, 4000, "원"),
]
_BY_ID = {s.id: s for s in _SUPPORTED}

# 글로벌 벤치마크라 정확한 국내 현물가가 아닌(대용) 연동 종목 — 템플릿이 '대용(proxy)'으로
# 표기한다(히스토리는 실측이되 국내 현물가 주장 금지). 검증 헬퍼가 참조한다.
PROXY_IDS = ("iron_ore", "hrc_steel", "thermal_coal", "diesel_gasoil")

# 공개 무료 소스가 없어 기간 히스토리를 연동하지 않는 종목(정직: 소스 필요).
# US 2Y·KR 10Y는 Yahoo 무료 심볼이 없어 비연동으로 둔다(가짜 값/가짜 선 미생성).
# 니켈·아연(ZNC=F는 신뢰 불가한 ALTSYMBOL)·원료탄 등은 신뢰 가능한 무료 심볼이 없어 비연동.
SOURCE_NEEDED_IDS = ("us_2y", "kr_10y")


def supported_ids() -> list:
    """기간 히스토리를 제공하는 종목 id 목록 (표시 순서 유지)."""
    return [s.id for s in _SUPPORTED]


def is_supported(item_id: str) -> bool:
    return item_id in _BY_ID


# ---------------------------------------------------------------------------
# 결정적 데모 픽스처 — now()/random 미사용. 기간별로 서로 다른 창을 생성한다.
# ---------------------------------------------------------------------------

def _lcg(seed: int):
    """결정적 의사난수 [0,1) 생성기 (Math.random 미사용 — 재현 가능)."""
    state = (seed & 0x7FFFFFFF) or 1

    def nxt() -> float:
        nonlocal state
        state = (1103515245 * state + 12345) & 0x7FFFFFFF
        return state / 0x7FFFFFFF

    return nxt


def _seed_for(key: str) -> int:
    return (sum(ord(c) * (i + 1) for i, c in enumerate(key)) * 2654435761) & 0x7FFFFFFF


def _round(value: float, decimals: int):
    if decimals <= 0:
        return int(round(value))
    return round(value, decimals)


def _demo_series(base: float, period: str, decimals: int, seed_key: str) -> list:
    """한 기간(period)의 결정적 데모 시계열 — 끝점은 현재값(base), 길이/추세는 기간 고유.

    장기 기간일수록 시작점이 base에서 더 멀고(추세 폭 큼), 진폭도 크다. 각 기간이 독립적으로
    생성되므로(서로 다른 seed·길이·추세) 3개월·1년이 동일 배열이 되지 않는다.
    """
    n = PERIOD_LEN[period]
    drift = _PERIOD_DRIFT[period]
    rnd = _lcg(_seed_for(f"{seed_key}:{period}"))
    start = base * (1.0 - drift)
    amp = base * drift * 0.35  # 추세 위 잔물결 진폭(기간 폭에 비례)
    out = []
    for i in range(n):
        frac = i / (n - 1) if n > 1 else 1.0
        trend = start + (base - start) * frac
        wig = (rnd() - 0.5) * 2 * amp
        out.append(_round(trend + wig, decimals))
    out[-1] = _round(base, decimals)  # 끝점=현재값(모든 기간 일관)
    return out


def demo_history(item_id: str) -> dict:
    """종목의 기간별 데모 히스토리 {period: [...]} (결정적, 기간별 상이)."""
    spec = _BY_ID.get(item_id)
    if not spec:
        return {}
    return {p: _demo_series(spec.base, p, spec.decimals, item_id) for p in PERIOD_KEYS}


def demo_entry(item_id: str) -> dict:
    """market_item에 부착할 데모 히스토리 엔트리(출처/모드/기준일 포함)."""
    spec = _BY_ID.get(item_id)
    if not spec:
        return {}
    return {
        "history": demo_history(item_id),
        "history_source": DEMO_SOURCE,
        "history_data_mode": DEMO_MODE,
        "history_updated_at": DEMO_AS_OF,
        "history_decimals": spec.decimals,
    }


# ---------------------------------------------------------------------------
# live 히스토리 — 공개 Yahoo chart 1년 일별 시계열을 4개 기간 창으로 슬라이스.
# ---------------------------------------------------------------------------

def _chart_url(symbol: str) -> str:
    query = urllib.parse.urlencode(LIVE_RANGE_PARAMS)
    return f"{DEFAULT_BASE_URL.rstrip('/')}/{urllib.parse.quote(symbol, safe='')}?{query}"


def _fetch_json(url: str, timeout: int) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (public JSON)
        charset = resp.headers.get_content_charset() or "utf-8"
        return json.loads(resp.read().decode(charset, errors="replace"))


def _series_from_chart(payload: dict) -> tuple:
    """chart 응답에서 (timestamps, closes) 정렬 배열을 꺼낸다 (None 종가는 제거)."""
    try:
        result = payload["chart"]["result"][0]
        stamps = result.get("timestamp") or []
        closes = result["indicators"]["quote"][0].get("close") or []
    except (KeyError, TypeError, IndexError):
        return [], []
    pairs = [(t, c) for t, c in zip(stamps, closes)
             if isinstance(t, (int, float)) and isinstance(c, (int, float))]
    pairs.sort(key=lambda p: p[0])
    return [p[0] for p in pairs], [p[1] for p in pairs]


def _sample(values: list, n: int) -> list:
    """배열에서 n개를 균등 샘플(끝점 포함). resample 추세왜곡이 아니라 실측 부분집합 추출이다."""
    if not values:
        return []
    if len(values) <= n:
        return list(values)
    out = []
    for i in range(n):
        idx = round(i * (len(values) - 1) / (n - 1))
        out.append(values[idx])
    return out


def _window(closes: list, period: str) -> list:
    """1년 일별 종가에서 기간 창을 잘라 표시 길이로 샘플한다(실제 서로 다른 날짜 구간)."""
    if not closes:
        return []
    # 거래일 기준 대략 구간: 1주≈5, 1개월≈22, 3개월≈63, 1년=전체.
    tail = {"1w": 6, "1m": 22, "3m": 63, "1y": len(closes)}[period]
    sliced = closes[-tail:] if tail < len(closes) else closes
    return _sample(sliced, PERIOD_LEN[period])


def _scaled(values: list, spec: "_Spec") -> list:
    out = []
    for v in values:
        s = v * spec.scale
        if s < spec.sane_min or s > spec.sane_max:
            continue  # 심볼 혼동/자릿수 오류 — 범위 밖이면 버린다(가짜로 표시 안 함)
        out.append(_round(s, spec.decimals))
    return out


def fetch_live_history(item_ids=None, timeout: int | None = None,
                       fetcher=None) -> dict:
    """지원 종목의 공개 시세 1년 히스토리를 수집해 기간별 엔트리로 반환.

    심볼 1건 실패/결측은 격리(해당 id 제외). 반환은 {id: live_entry} (성공분만).
    fetcher는 테스트 주입용(symbol→{timestamps, closes} 또는 None). 평상시 None.
    """
    ids = list(item_ids) if item_ids else supported_ids()
    to = int(timeout if timeout is not None else DEFAULT_TIMEOUT)
    out = {}
    for item_id in ids:
        spec = _BY_ID.get(item_id)
        if not spec or not spec.symbol:
            continue
        try:
            if fetcher is not None:
                got = fetcher(spec.symbol) or {}
                stamps, closes = got.get("timestamps") or [], got.get("closes") or []
            else:
                stamps, closes = _series_from_chart(
                    _fetch_json(_chart_url(spec.symbol), to))
        except Exception:  # noqa: BLE001 — 네트워크/HTTP/JSON 오류는 종목 단위로 무시
            continue
        if len(closes) < PERIOD_LEN["1w"]:
            continue
        history = {}
        ok = True
        for period in PERIOD_KEYS:
            arr = _scaled(_window(closes, period), spec)
            if len(arr) < 2:
                ok = False
                break
            history[period] = arr
        if not ok:
            continue
        updated = _iso_date(stamps[-1]) if stamps else DEMO_AS_OF
        out[item_id] = {
            "history": history,
            "history_source": LIVE_SOURCE,
            "history_data_mode": LIVE_MODE,
            "history_updated_at": updated,
            "history_decimals": spec.decimals,
        }
    return out


def _iso_date(epoch) -> str:
    try:
        return datetime.fromtimestamp(int(epoch), tz=timezone.utc).strftime("%Y-%m-%d")
    except (ValueError, OSError, OverflowError, TypeError):
        return DEMO_AS_OF


def history_for_model(mode: str = "mock", fetcher=None,
                      timeout: int | None = None) -> dict:
    """대시보드 주입용 {id: entry} — mock=데모 픽스처, live=실측(실패분은 데모로 정직 표기).

    live 모드에서 일부 심볼이 실패해도 해당 종목은 demo 엔트리(history_data_mode=mock_demo)
    로 남는다 — live로 위장하지 않는다. 각 종목이 자기 출처를 들고 있어 혼재가 정직하다.
    """
    base = {s.id: demo_entry(s.id) for s in _SUPPORTED}
    if (mode or "mock").strip().lower() != "live":
        return base
    live = fetch_live_history(fetcher=fetcher, timeout=timeout)
    base.update(live)  # 성공한 종목만 실측으로 덮어쓴다(나머지는 데모 유지)
    return base


def demo_distinct_ok(item_id: str) -> bool:
    """검증 헬퍼 — 종목의 1주/1개월/3개월/1년이 모두 다른 배열인지(특히 3개월≠1년)."""
    h = demo_history(item_id)
    series = [tuple(h.get(p) or ()) for p in PERIOD_KEYS]
    if any(len(s) < 2 for s in series):
        return False
    return len(set(series)) == len(series) and series[2] != series[3]
