"""Live Market 도메인 — 공개 시세 API에서 시장지표 값만 수집 (P0-D5-B).

app/live_macro.py와 동일한 경계 원칙을 따르는 network leaf다. market_snapshot이
live 모드일 때만 호출되며, 다른 도메인을 import하지 않는다(설정은 호출부가 주입).

- 공개 시세 JSON(Yahoo Finance chart)만 읽는다 — API key/비밀값이 전혀 필요 없다.
- 숫자형 시세만 다룬다(value/단위/방향/기준시각). 기사 본문/원문을 다루지 않는다.
- 지표 하나의 네트워크/파싱 실패는 격리한다(해당 심볼만 건너뜀). 전부 실패면 None.
- 실패/결측을 가짜 값으로 채우지 않는다 — 호출부(market_snapshot)가 unavailable 처리.
- 심볼 혼동/자릿수 오류는 sane range 가드로 결측 처리(가짜로 표시하지 않음).
- DB·점수·insight·발송을 일절 다루지 않는다.

반환 계약 (market_snapshot._live_snapshot이 소비):
    {
      "source": "Yahoo Finance",
      "fetched_at": "<수집 시각 ISO>",
      "stale_after_hours": 24,
      "quotes": { "<symbol>": {"value"(float), "as_of"(ISO|None), "direction"(str|None)} },
    }
  또는 수집 0건이면 None.
"""

import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone

SOURCE_LABEL = "Yahoo Finance"
# Yahoo chart 엔드포인트는 브라우저 형태의 User-Agent를 요구한다 (공개·무인증).
USER_AGENT = ("Mozilla/5.0 (compatible; HDEC-Executive-Radar/0.1; "
              "+public-quote-json; non-crawling)")
DEFAULT_TIMEOUT = 8
DEFAULT_BASE_URL = "https://query1.finance.yahoo.com/v8/finance/chart/"
DEFAULT_PARAMS = {"interval": "1d", "range": "5d"}
STALE_AFTER_HOURS = 24

# 한 프로세스 안에서 여러 번 호출돼도(리포트가 brief를 두 번 파생 등) 네트워크를 한 번만
# 때리고 동일 결과를 돌려준다. live_macro와 동일 패턴(같은 publish 실행의 불일치 방지).
_QUOTES_CACHE = None


def reset_cache() -> None:
    """프로세스 캐시 초기화 (테스트/검증 전용 — 운영 경로에서는 호출하지 않는다)."""
    global _QUOTES_CACHE
    _QUOTES_CACHE = None


def _chart_url(base_url: str, symbol: str, params: dict) -> str:
    query = urllib.parse.urlencode(params or DEFAULT_PARAMS)
    return f"{base_url.rstrip('/')}/{urllib.parse.quote(symbol, safe='')}?{query}"


def _fetch_json(url: str, timeout: int) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (public JSON)
        charset = resp.headers.get_content_charset() or "utf-8"
        return json.loads(resp.read().decode(charset, errors="replace"))


def _num(value) -> float | None:
    """숫자만 받아들인다. None/비숫자/bool이면 None (가짜 값 생성 금지)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _direction(price: float | None, prev_close: float | None) -> str | None:
    if price is None or prev_close is None:
        return None
    if price > prev_close:
        return "up"
    if price < prev_close:
        return "down"
    return "flat"


def _as_of_from_epoch(ts) -> str | None:
    """Yahoo regularMarketTime(epoch 초)을 ISO(UTC)로 정규화한다."""
    epoch = _num(ts)
    if epoch is None:
        return None
    try:
        return datetime.fromtimestamp(int(epoch), tz=timezone.utc).isoformat(
            timespec="seconds")
    except (ValueError, OSError, OverflowError):
        return None


def _meta_from_chart(payload: dict) -> dict | None:
    """Yahoo chart 응답에서 meta 블록만 안전하게 꺼낸다 (형식이 다르면 None)."""
    try:
        result = payload["chart"]["result"]
    except (KeyError, TypeError):
        return None
    if not isinstance(result, list) or not result or not isinstance(result[0], dict):
        return None
    meta = result[0].get("meta")
    return meta if isinstance(meta, dict) else None


def _quote_from_meta(meta: dict | None, sane_min, sane_max) -> dict | None:
    """chart meta → {value, as_of, direction}. 가격 없음/스케일 밖이면 None(결측)."""
    if not isinstance(meta, dict):
        return None
    price = _num(meta.get("regularMarketPrice"))
    if price is None:
        return None
    lo, hi = _num(sane_min), _num(sane_max)
    if (lo is not None and price < lo) or (hi is not None and price > hi):
        return None  # 심볼 혼동/자릿수 오류 — 라벨과 안 맞는 값은 보여주지 않는다
    prev = _num(meta.get("chartPreviousClose"))
    if prev is None:
        prev = _num(meta.get("previousClose"))
    quote = {
        # 표시용 소수 2자리 — 시세 자체를 바꾸지 않는 display 정밀도 (가짜 값 아님).
        "value": round(price, 2),
        "as_of": _as_of_from_epoch(meta.get("regularMarketTime")),
    }
    direction = _direction(price, prev)
    if direction:
        quote["direction"] = direction
    return quote


def fetch_quotes(instruments, timeout: int | None = None,
                 use_cache: bool = True) -> dict | None:
    """주입된 instrument들의 공개 시세를 수집해 symbol→quote 매핑을 반환한다.

    instruments는 source_symbol과 sane_min/sane_max를 가진 객체(또는 dict)다.
    market_profiles.MarketInstrument를 그대로 받는다. 심볼 1건의 실패는 격리하고,
    전부 실패/0건이면 None을 반환한다(호출부가 unavailable 처리 → 가짜 값 금지).
    """
    global _QUOTES_CACHE
    if use_cache and _QUOTES_CACHE is not None:
        return _QUOTES_CACHE

    to = int(timeout if timeout is not None else DEFAULT_TIMEOUT)
    quotes: dict = {}
    for inst in instruments or []:
        symbol = _attr(inst, "source_symbol")
        if not symbol or symbol in quotes:
            continue
        url = _chart_url(DEFAULT_BASE_URL, str(symbol), DEFAULT_PARAMS)
        try:
            payload = _fetch_json(url, to)
        except Exception:  # noqa: BLE001 — 네트워크/HTTP/JSON 오류는 심볼 단위로 무시
            continue
        quote = _quote_from_meta(_meta_from_chart(payload),
                                 _attr(inst, "sane_min"), _attr(inst, "sane_max"))
        if quote is None:
            continue
        quotes[symbol] = quote

    if not quotes:
        return None
    snapshot = {
        "source": SOURCE_LABEL,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "stale_after_hours": STALE_AFTER_HOURS,
        "quotes": quotes,
    }
    if use_cache:
        _QUOTES_CACHE = snapshot
    return snapshot


def _attr(obj, name):
    """MarketInstrument(객체)와 dict를 모두 받아 속성/키를 읽는다."""
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)
