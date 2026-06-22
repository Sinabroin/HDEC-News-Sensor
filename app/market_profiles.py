"""Market 도메인 (설정) — 건설 원자재·국채 금리·환율 센싱 카탈로그 (P0-D5-B).

이 파일은 "어떤 시장지표를 어떤 출처/모드로 센싱하는가"만 선언하는 순수 설정 leaf다.
app/topic_profiles.py(토픽)·data/live_macro_sources.json(macro)과 같은 계열의 설정이며,
다음 원칙을 지킨다:

- 네트워크/DB/LLM/발송 0건. 숫자값(시세)을 만들지 않는다 — 카탈로그(메타)만 보유한다.
- 실제 수집은 leaf(app/live_market.py)가, 정규화/provenance는 app/market_snapshot.py가 한다.
- 정직성(rules.md §1, 사용자 요구): proxy/지연 데이터를 '실시간/현재 체결값'으로 라벨하지 않는다.
  각 지표는 자기 성격을 data_mode로 선언한다(아래 5종). 공개 무료 소스가 없는 지표는
  source_symbol=None + data_mode=unavailable로 두고, 런타임에 가짜 값을 채우지 않는다.

data_mode 5종 (출처/신선도 성격):
- live_market        : 실시간 체결 시세 (현재 프로젝트는 무료 실시간 소스가 없어 사용 안 함)
- delayed_market     : 공개·무료 지연 시세 (Yahoo chart 등) — 체결값/실시간 아님
- proxy_market       : 직접 시세가 없어 관련 지표로 대용(proxy) — 방향성 참고용
- manual_or_reported : 협회/통계/고시 기반 보고값 — 공개 실시간 API 부재(수동 입력 필요)
- unavailable        : 사용할 수 있는 공개 소스가 없음 — 값 미생성

category 3종: construction_commodities · sovereign_yields · fx.
"""

from dataclasses import dataclass

# ---------- 출처/모드 상수 ----------

PROVIDER_YAHOO = "Yahoo Finance"
PROVIDER_REPORTED = "협회·통계 보고"   # manual_or_reported (공개 실시간 API 부재)
PROVIDER_NONE = "미연동"               # unavailable (공개 소스 없음)

CATEGORY_COMMODITIES = "construction_commodities"
CATEGORY_YIELDS = "sovereign_yields"
CATEGORY_FX = "fx"
MARKET_CATEGORIES = (CATEGORY_COMMODITIES, CATEGORY_YIELDS, CATEGORY_FX)

MODE_LIVE = "live_market"
MODE_DELAYED = "delayed_market"
MODE_PROXY = "proxy_market"
MODE_MANUAL = "manual_or_reported"
MODE_UNAVAILABLE = "unavailable"
DATA_MODES = (MODE_LIVE, MODE_DELAYED, MODE_PROXY, MODE_MANUAL, MODE_UNAVAILABLE)


@dataclass(frozen=True)
class MarketInstrument:
    """시장 센싱 단위 1건의 메타데이터 (시세값은 포함하지 않는다).

    sane_min/sane_max는 live_market leaf의 스케일 가드용(심볼 혼동·자릿수 오류를 결측
    처리)이며 설정에서 선언한다. 시세 자체가 아니라 '말이 되는 범위'일 뿐이다.
    """

    id: str
    label_kr: str
    category: str
    unit: str
    currency: str
    source_symbol: str | None
    source_provider: str
    data_mode: str
    importance: int
    proxy_for: str | None
    note_kr: str
    sane_min: float | None = None
    sane_max: float | None = None


# ---------- 빌더 헬퍼 ----------

def _commodity(id_, label, symbol, provider, mode, unit, currency, importance,
               proxy_for, note, sane=(None, None)) -> MarketInstrument:
    return MarketInstrument(
        id=id_, label_kr=label, category=CATEGORY_COMMODITIES, unit=unit,
        currency=currency, source_symbol=symbol, source_provider=provider,
        data_mode=mode, importance=importance, proxy_for=proxy_for, note_kr=note,
        sane_min=sane[0], sane_max=sane[1])


def _fx(id_, label, symbol, unit, currency, importance, note,
        sane=(None, None)) -> MarketInstrument:
    return MarketInstrument(
        id=id_, label_kr=label, category=CATEGORY_FX, unit=unit, currency=currency,
        source_symbol=symbol, source_provider=PROVIDER_YAHOO, data_mode=MODE_DELAYED,
        importance=importance, proxy_for=None, note_kr=note,
        sane_min=sane[0], sane_max=sane[1])


# 공개 무료 Yahoo chart로 신뢰성 있게 받을 수 있는 국채 금리 심볼만 등록한다.
# US 5Y(^FVX)·10Y(^TNX)는 이미 매크로 레이어에서 검증된 퍼센트 스케일 심볼이다.
# 그 외 국가/만기는 공개 무료 실시간 시세가 없어 미연동(unavailable)으로 둔다
# — 가짜 값을 만들지 않고 별도 소스(중앙은행/유료)가 필요함을 note로 밝힌다.
_YIELD_SYMBOLS = {
    ("us", "5y"): ("^FVX", (0, 25)),
    ("us", "10y"): ("^TNX", (0, 25)),
}
_YIELD_COUNTRIES = [
    ("kr", "한국"), ("us", "미국"), ("jp", "일본"), ("de", "독일"),
    ("uk", "영국"), ("cn", "중국"), ("au", "호주"), ("ca", "캐나다"),
]
_TENORS = ["1y", "3y", "5y", "10y"]
_TENOR_LABEL = {"1y": "1Y", "3y": "3Y", "5y": "5Y", "10y": "10Y"}
_TENOR_IMPORTANCE = {"1y": 4, "3y": 4, "5y": 3, "10y": 2}


def _yield_instrument(cc: str, country_kr: str, tenor: str) -> MarketInstrument:
    label = f"{country_kr} 국채 {_TENOR_LABEL[tenor]}"
    importance = _TENOR_IMPORTANCE[tenor]
    hit = _YIELD_SYMBOLS.get((cc, tenor))
    if hit:
        symbol, sane = hit
        return MarketInstrument(
            id=f"{cc}_{tenor}", label_kr=label, category=CATEGORY_YIELDS,
            unit="%", currency="", source_symbol=symbol,
            source_provider=PROVIDER_YAHOO, data_mode=MODE_DELAYED,
            importance=importance, proxy_for=None,
            note_kr=f"{label} 금리 — 공개 시세(지연) 기준, 현재 체결값이 아님",
            sane_min=sane[0], sane_max=sane[1])
    return MarketInstrument(
        id=f"{cc}_{tenor}", label_kr=label, category=CATEGORY_YIELDS,
        unit="%", currency="", source_symbol=None, source_provider=PROVIDER_NONE,
        data_mode=MODE_UNAVAILABLE, importance=importance, proxy_for=None,
        note_kr=(f"{label} — 공개 무료 실시간 금리 시세 미연동. "
                 "중앙은행/유료 데이터 등 별도 소스 필요(가짜 값 미생성)."))


# ---------- 건설 원자재 (15) ----------
# 금속/철강: 직접 선물은 delayed, 철강·운임 등은 직접 시세 부재 → 대용(proxy)/보고/미연동.

_COMMODITIES = [
    # 금속 / 철강
    _commodity("copper", "구리", "HG=F", PROVIDER_YAHOO, MODE_DELAYED,
               "USD/lb", "USD", 2, None,
               "COMEX 구리 선물 — 공개 시세(지연), 현재 체결값이 아님", (0.5, 20)),
    _commodity("aluminum", "알루미늄", "ALI=F", PROVIDER_YAHOO, MODE_DELAYED,
               "USD/t", "USD", 3, None,
               "알루미늄 선물 — 공개 시세(지연), 거래 얇을 수 있음(미수신 시 미연동)",
               (800, 6000)),
    _commodity("iron_ore", "철광석", "TIO=F", PROVIDER_YAHOO, MODE_DELAYED,
               "USD/t", "USD", 2, None,
               "철광석 62% Fe 선물 — 공개 시세(지연), 미수신 시 미연동", (30, 400)),
    _commodity("steel_rebar_proxy", "철근(대용)", "SLX", PROVIDER_YAHOO, MODE_PROXY,
               "pt", "USD", 2, "글로벌 철강주 ETF(SLX)",
               "철근 직접 시세 부재 — 글로벌 철강주 ETF로 방향성만 참고(가격 아님)",
               (5, 200)),
    _commodity("hot_rolled_coil_proxy", "열연강판(대용)", "HRC=F", PROVIDER_YAHOO,
               MODE_PROXY, "USD/short ton", "USD", 2, "CME 열연강판(HRC) 선물",
               "국내 강재 원가 대용 — CME HRC 선물(지연), 국내 고시가 아님", (200, 2500)),
    _commodity("nickel", "니켈", None, PROVIDER_NONE, MODE_UNAVAILABLE,
               "USD/t", "USD", 3, None,
               "공개 무료 니켈 선물 시세 부재 — LME(유료) 등 별도 소스 필요(미연동)"),
    # 에너지 / 연료 / 아스팔트
    _commodity("wti_crude", "WTI 원유", "CL=F", PROVIDER_YAHOO, MODE_DELAYED,
               "USD/bbl", "USD", 1, None,
               "WTI 근월물 — 공개 시세(지연), 현재 체결값이 아님", (5, 250)),
    _commodity("brent_crude", "브렌트 원유", "BZ=F", PROVIDER_YAHOO, MODE_DELAYED,
               "USD/bbl", "USD", 1, None,
               "브렌트 근월물 — 공개 시세(지연), 현재 체결값이 아님", (5, 250)),
    _commodity("diesel_proxy", "경유(대용)", "HO=F", PROVIDER_YAHOO, MODE_PROXY,
               "USD/gal", "USD", 2, "NY ULSD(초저유황 경유/난방유) 선물",
               "경유 직접 시세 대용 — NY ULSD 선물(지연), 국내 주유가 아님", (0.5, 10)),
    _commodity("natural_gas", "천연가스", "NG=F", PROVIDER_YAHOO, MODE_DELAYED,
               "USD/MMBtu", "USD", 3, None,
               "헨리허브 천연가스 선물 — 공개 시세(지연)", (0.5, 50)),
    _commodity("thermal_coal_proxy", "연료탄(대용)", None, PROVIDER_REPORTED,
               MODE_MANUAL, "USD/t", "USD", 3, "Newcastle 발전용 연료탄 지수",
               "발전용 연료탄 공개 무료 시세 부재 — 지수(유료)/보고 기반(수동 입력 필요)"),
    # 건설자재 / 소프트 원자재
    _commodity("lumber", "목재", "LBR=F", PROVIDER_YAHOO, MODE_DELAYED,
               "USD/1000bf", "USD", 3, None,
               "목재 선물 — 공개 시세(지연), 거래 얇을 수 있음(미수신 시 미연동)",
               (100, 2000)),
    _commodity("cement_proxy", "시멘트(국내)", None, PROVIDER_REPORTED, MODE_MANUAL,
               "원/t", "KRW", 2, "국내 시멘트 고시·협회 통계",
               "국내 시멘트 가격은 공개 시세 API가 없어 협회/통계 보고 기반(수동 입력 필요)"),
    _commodity("asphalt_bitumen_proxy", "아스팔트·역청(대용)", "CL=F", PROVIDER_YAHOO,
               MODE_PROXY, "USD/bbl", "USD", 3, "원유(WTI) — 정제부산물 방향성",
               "아스팔트(역청)는 원유 정제부산물 — WTI로 방향성만 참고(가격 아님)",
               (5, 250)),
    # 물류 / 운임
    _commodity("baltic_dry_index_proxy", "건화물 운임(대용)", "BDRY", PROVIDER_YAHOO,
               MODE_PROXY, "pt", "USD", 3, "Baltic Dry 건화물 운임 ETF(BDRY)",
               "Baltic Dry 지수 직접값 부재 — 건화물 운임 ETF로 방향성만 참고", (1, 200)),
]


# ---------- 국채 금리 (8개국 × 1Y/3Y/5Y/10Y = 32) ----------

_YIELDS = [
    _yield_instrument(cc, country_kr, tenor)
    for cc, country_kr in _YIELD_COUNTRIES
    for tenor in _TENORS
]


# ---------- 환율 (7 + 선택 4 = 11) ----------

_FX = [
    _fx("usdkrw", "USD/KRW", "USDKRW=X", "원", "KRW", 1,
        "원/달러 — 공개 시세(지연), 현재 체결값이 아님", (500, 3000)),
    _fx("eurkrw", "EUR/KRW", "EURKRW=X", "원", "KRW", 2,
        "원/유로 — 공개 시세(지연)", (500, 4000)),
    _fx("jpykrw", "JPY/KRW", "JPYKRW=X", "원", "KRW", 2,
        "원/엔(엔당 원) — 공개 시세(지연)", (3, 30)),
    _fx("cnykrw", "CNY/KRW", "CNYKRW=X", "원", "KRW", 2,
        "원/위안 — 공개 시세(지연)", (50, 400)),
    _fx("gbpkrw", "GBP/KRW", "GBPKRW=X", "원", "KRW", 3,
        "원/파운드 — 공개 시세(지연)", (800, 4000)),
    _fx("audkrw", "AUD/KRW", "AUDKRW=X", "원", "KRW", 3,
        "원/호주달러 — 공개 시세(지연)", (400, 2000)),
    _fx("cadkrw", "CAD/KRW", "CADKRW=X", "원", "KRW", 3,
        "원/캐나다달러 — 공개 시세(지연)", (400, 2000)),
    # 선택 지표 — 달러 강도/주요 교차환율 (참고용)
    _fx("dxy", "달러지수(DXY)", "DX-Y.NYB", "pt", "", 2,
        "미 달러지수 — 공개 시세(지연)", (50, 150)),
    _fx("eurusd", "EUR/USD", "EURUSD=X", "", "USD", 3,
        "유로/달러 — 공개 시세(지연)", (0.5, 2)),
    _fx("usdjpy", "USD/JPY", "USDJPY=X", "", "JPY", 3,
        "달러/엔 — 공개 시세(지연)", (50, 250)),
    _fx("usdcnh", "USD/CNH", "USDCNH=X", "", "CNH", 3,
        "달러/역외위안 — 공개 시세(지연)", (3, 12)),
]


_ALL = tuple(_COMMODITIES + _YIELDS + _FX)
_BY_ID = {i.id: i for i in _ALL}


# ---------- 공개 헬퍼 ----------

def all_instruments() -> tuple:
    """카탈로그 전체를 category(commodities→yields→fx)·importance·id 순으로 반환."""
    order = {c: n for n, c in enumerate(MARKET_CATEGORIES)}
    return tuple(sorted(_ALL, key=lambda i: (order.get(i.category, 9),
                                             i.importance, i.id)))


def instruments_by_category(category: str) -> list:
    """한 category의 지표 목록 (importance·id 순)."""
    return [i for i in all_instruments() if i.category == category]


def get_instrument(instrument_id: str):
    """id로 지표 1건을 찾는다 (없으면 None)."""
    return _BY_ID.get(instrument_id)


def fetchable_instruments() -> list:
    """공개 시세 심볼이 있는(=네트워크로 수집 시도할) 지표만 (leaf 입력용)."""
    return [i for i in all_instruments()
            if i.source_symbol and i.source_provider == PROVIDER_YAHOO]


def iter_source_symbols() -> list:
    """수집 대상 심볼의 중복 제거 목록 (등장 순서 유지)."""
    seen, out = set(), []
    for i in fetchable_instruments():
        if i.source_symbol not in seen:
            seen.add(i.source_symbol)
            out.append(i.source_symbol)
    return out


def catalog() -> list:
    """brief/검증용 카탈로그(dict). 시세값은 포함하지 않는다(메타만)."""
    return [
        {"id": i.id, "label_kr": i.label_kr, "category": i.category,
         "unit": i.unit, "currency": i.currency, "source_symbol": i.source_symbol,
         "source_provider": i.source_provider, "data_mode": i.data_mode,
         "importance": i.importance, "proxy_for": i.proxy_for, "note_kr": i.note_kr}
        for i in all_instruments()
    ]
