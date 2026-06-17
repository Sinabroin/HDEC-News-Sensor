"""Live Macro 도메인 — 공개 시세 API에서 실제 시장지표만 수집 (P0-C2).

이 파일은 macro_snapshot이 live 모드일 때만 호출되는 leaf 모듈이며,
app/live_collector.py와 동일한 경계 원칙을 따른다:

- 공개 시세 JSON(Yahoo Finance chart 등)만 읽는다 — API key/비밀값이 전혀 필요 없다.
  공급자는 data/live_macro_sources.json으로 교체 가능하다 (이 파일만 바꾸면 된다).
- 기사 본문/원문을 다루지 않는다. 오직 숫자형 시장지표(value/단위/방향/기준시각)만.
- 실패 시 가짜 값을 만들지 않는다 — None 또는 빈 values를 돌려주고,
  macro_snapshot이 그것을 unavailable로 강등한다 ("live 주장 금지").
- 지표 하나의 실패는 격리한다 (해당 지표만 건너뛰고 나머지는 수집) — live_collector의
  query 단위 무시와 동일. 전부 실패하면 None을 돌려준다.
- DB·점수·insight·발송·digest를 일절 다루지 않는다.

반환 계약 (macro_snapshot.get_macro_snapshot의 live 분기가 소비):
    {
      "source": "Yahoo Finance",
      "fetched_at": "<수집 시각 ISO>",
      "stale_after_hours": 24,
      "values": [
        {"key","label","value"(float),"unit","direction"("up"|"down"|"flat"),"as_of"(ISO)}
      ],
    }
  또는 수집 실패/0건이면 None.
"""

import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from app import config

SOURCE_LABEL = "Yahoo Finance"
PROVIDER = "yahoo_chart"
# Yahoo chart 엔드포인트는 브라우저 형태의 User-Agent를 요구한다 (공개·무인증).
USER_AGENT = "Mozilla/5.0 (compatible; HDEC-Executive-Radar/0.1; +public-quote-json; non-crawling)"
DEFAULT_TIMEOUT = 8
DEFAULT_BASE_URL = "https://query1.finance.yahoo.com/v8/finance/chart/"
DEFAULT_PARAMS = {"interval": "1d", "range": "5d"}
_DEFAULT_SOURCES = config.DATA_DIR / "live_macro_sources.json"

# P0-C2 Blocker 2 — 한 프로세스 안에서 fetch_snapshot이 여러 번 호출돼도(리포트가 brief를
# 두 번 파생하는 등) 네트워크를 한 번만 때리고 동일 snapshot을 돌려준다. 같은 publish 실행이
# 여러 출력에 서로 다른 시세를 싣는 불일치를 막는다. 프로세스 경계를 넘는 공유는
# macro_snapshot의 MACRO_SNAPSHOT_FILE이 담당한다.
_SNAPSHOT_CACHE = None


def reset_cache() -> None:
    """프로세스 캐시 초기화 (테스트/검증 전용 — 운영 경로에서는 호출하지 않는다)."""
    global _SNAPSHOT_CACHE
    _SNAPSHOT_CACHE = None


def _load_sources(path=None) -> dict:
    src_path = path or _DEFAULT_SOURCES
    try:
        data = json.loads(src_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _chart_url(base_url: str, symbol: str, params: dict) -> str:
    query = urllib.parse.urlencode(params or DEFAULT_PARAMS)
    return f"{base_url.rstrip('/')}/{urllib.parse.quote(symbol, safe='')}?{query}"


def _fetch_json(url: str, timeout: int) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (public JSON)
        charset = resp.headers.get_content_charset() or "utf-8"
        return json.loads(resp.read().decode(charset, errors="replace"))


def _num(value) -> float | None:
    """숫자만 받아들인다. None/비숫자면 None (가짜 값 생성 금지)."""
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
        return datetime.fromtimestamp(int(epoch), tz=timezone.utc).isoformat(timespec="seconds")
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


def _to_value(meta: dict | None, cfg: dict) -> dict | None:
    """chart meta + indicator 설정 → 표준 value dict. 가격이 없으면 None (결측 제외)."""
    if not isinstance(meta, dict):
        return None
    price = _num(meta.get("regularMarketPrice"))
    if price is None:
        return None
    # 스케일 sanity 가드 — 심볼 혼동/자릿수 오류(예: KOSPI 자리에 다른 지수, 10Y가 ÷10 안 된 값)는
    # 실제로 fetch된 숫자라도 "라벨과 안 맞는 오해 소지 값"이므로 보여주지 않고 결측 처리한다.
    # 범위는 실세계 스케일 기준으로 넓게 잡아(정상값 오탐 방지) 자릿수/심볼 오류만 잡는다.
    lo, hi = _num(cfg.get("sane_min")), _num(cfg.get("sane_max"))
    if (lo is not None and price < lo) or (hi is not None and price > hi):
        return None
    prev = _num(meta.get("chartPreviousClose"))
    if prev is None:
        prev = _num(meta.get("previousClose"))
    value = {
        "key": cfg["key"],
        "label": cfg["label"],
        # 표시용 소수 2자리 — 시세 자체를 바꾸지 않는 display 정밀도 (가짜 값 아님).
        "value": round(price, 2),
        "unit": cfg.get("unit", ""),
        "as_of": _as_of_from_epoch(meta.get("regularMarketTime")),
    }
    direction = _direction(price, prev)
    if direction:
        value["direction"] = direction
    return value


def fetch_snapshot(timeout: int | None = None, sources_path=None,
                   use_cache: bool = True) -> dict | None:
    """설정된 지표를 공개 시세 API에서 수집해 표준 snapshot dict를 반환한다.

    지표 하나의 네트워크/파싱 실패는 격리하고(해당 지표만 제외) 계속한다.
    전부 실패/0건이면 None을 반환한다 (가짜 값 생성 금지 → 호출부가 unavailable 처리).

    use_cache=True(기본)면 같은 프로세스의 첫 성공 결과를 재사용한다(중복 fetch 방지).
    실패(None)는 캐시하지 않아 다음 호출에서 재시도할 수 있다.
    """
    global _SNAPSHOT_CACHE
    if use_cache and _SNAPSHOT_CACHE is not None:
        return _SNAPSHOT_CACHE

    cfg = _load_sources(sources_path)
    indicators = [i for i in (cfg.get("indicators") or [])
                  if isinstance(i, dict) and i.get("key") and i.get("symbol")]
    if not indicators:
        return None
    indicators = indicators[: int(cfg.get("max_indicators", 8))]

    base_url = cfg.get("base_url") or DEFAULT_BASE_URL
    params = cfg.get("params") or DEFAULT_PARAMS
    to = int(timeout if timeout is not None else cfg.get("timeout_seconds", DEFAULT_TIMEOUT))

    values, seen = [], set()
    for ind in indicators:
        if ind["key"] in seen:
            continue
        url = _chart_url(base_url, str(ind["symbol"]), params)
        try:
            payload = _fetch_json(url, to)
        except Exception:  # noqa: BLE001 — 네트워크/HTTP/JSON 오류는 지표 단위로 무시
            continue
        value = _to_value(_meta_from_chart(payload), ind)
        if value is None:
            continue
        seen.add(ind["key"])
        values.append(value)

    if not values:
        return None
    snapshot = {
        "source": cfg.get("source_label") or SOURCE_LABEL,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "stale_after_hours": int(cfg.get("stale_after_hours", 24)),
        "values": values,
    }
    if use_cache:
        _SNAPSHOT_CACHE = snapshot
    return snapshot
