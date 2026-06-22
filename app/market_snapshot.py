"""Market 도메인 (조정) — 시장지표 snapshot의 출처·신선도·정직성 부착 (P0-D5-B).

app/macro_snapshot.py와 동일한 역할 분담을 따른다:

- 네트워크 IO는 leaf(app/live_market.py)가 소유한다. 이 파일은 fetcher를 호출만 하고
  (collector→live_collector, macro_snapshot→live_macro와 동일), 결과에 출처·기준시각·
  stale(지연) 플래그·종목별 data_mode를 부착한다. 이 파일 자체는 네트워크를 import하지 않는다.
- 카탈로그(어떤 지표/심볼/성격)는 app/market_profiles.py가 소유한다.
- live 수집이 실패/0건이면 가짜 값으로 채우지 않고 unavailable을 드러낸다.
- 정직성(사용자 요구): proxy/지연 값을 '실시간/현재 체결값'으로 표시하지 않는다. 표시
  레이어는 종목별 data_mode와 disclaimer를 반드시 함께 노출해야 한다.

이 파일은 DB·점수·insight·발송을 일절 다루지 않는다.

반환 구조 (항상 동일 키):
    {
      "mode": "live" | "mock" | "unavailable",   # 전체 snapshot 상태
      "updated_at", "as_of", "source_summary", "disclaimer",
      "categories": {construction_commodities:[...], sovereign_yields:[...], fx:[...]},
      "items": [ {id, label_kr, category, value, unit, currency, change_1d_pct,
                  change_5d_pct, source_provider, source_symbol, data_mode, as_of,
                  is_stale, proxy_for, note_kr, direction?}, ... ],
      "warnings": [...],
    }
"""

from datetime import datetime, timezone

from app import market_profiles as mp

MODE_LIVE = "live"
MODE_MOCK = "mock"
MODE_UNAVAILABLE = "unavailable"

DEFAULT_STALE_HOURS = 24

_DISCLAIMER = (
    "시장 데이터는 공개·무료(지연) 또는 대용(proxy)·보고 기반 참고용이며 체결값이 "
    "아닙니다. 종목별 data_mode·출처·기준시각을 함께 확인하세요. "
    "미연동(unavailable) 지표는 값을 생성하지 않습니다."
)
_UNAVAILABLE_SUMMARY = (
    "시장지표 미연동 — 사용할 수 있는 공개 시세가 없어 값을 생성하지 않습니다."
)
_MOCK_SUMMARY = (
    "시장지표 비-live 모드 — 공개 시세를 호출하지 않으며 값을 생성하지 않습니다."
)


def _parse_iso(iso) -> datetime | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _is_stale(as_of_dt: datetime | None, stale_hours: int,
              now: datetime | None) -> bool:
    """기준시각을 알 수 없거나(보수적으로 stale) 임계 시간을 초과하면 stale."""
    if as_of_dt is None:
        return True
    ref = now or datetime.now(timezone.utc)
    return (ref - as_of_dt).total_seconds() / 3600 > max(0, stale_hours)


def _null_item(inst) -> dict:
    """값이 없는(미수신/미연동/보고기반) 지표 항목 — 가짜 숫자를 채우지 않는다.

    delayed/proxy/live 성격인데 값이 없으면 런타임 상태는 unavailable로 강등한다
    (live를 시도했으나 못 받음). manual_or_reported/unavailable은 성격을 유지한다.
    """
    runtime_mode = inst.data_mode
    if inst.data_mode in (mp.MODE_LIVE, mp.MODE_DELAYED, mp.MODE_PROXY):
        runtime_mode = mp.MODE_UNAVAILABLE
    return {
        "id": inst.id, "label_kr": inst.label_kr, "category": inst.category,
        "value": None, "unit": inst.unit, "currency": inst.currency,
        "change_1d_pct": None, "change_5d_pct": None,
        "source_provider": inst.source_provider, "source_symbol": inst.source_symbol,
        "data_mode": runtime_mode, "as_of": None, "is_stale": True,
        "proxy_for": inst.proxy_for, "note_kr": inst.note_kr,
    }


def _value_item(inst, quote: dict, stale_hours: int, now) -> dict:
    """공개 시세를 받은 지표 항목 — data_mode는 지표의 성격(delayed/proxy)을 유지한다."""
    as_of = quote.get("as_of")
    item = {
        "id": inst.id, "label_kr": inst.label_kr, "category": inst.category,
        "value": quote.get("value"), "unit": inst.unit, "currency": inst.currency,
        "change_1d_pct": None, "change_5d_pct": None,
        "source_provider": inst.source_provider, "source_symbol": inst.source_symbol,
        "data_mode": inst.data_mode, "as_of": as_of,
        "is_stale": _is_stale(_parse_iso(as_of), stale_hours, now),
        "proxy_for": inst.proxy_for, "note_kr": inst.note_kr,
    }
    if quote.get("direction"):
        item["direction"] = quote["direction"]
    return item


def _assemble(items: list, mode: str, source_summary: str,
              fetched_at: str | None) -> dict:
    """항목 목록을 category 그룹 + 전체 목록 + provenance로 조립한다."""
    categories = {c: [it for it in items if it["category"] == c]
                  for c in mp.MARKET_CATEGORIES}
    as_ofs = [_parse_iso(it.get("as_of")) for it in items if it.get("value") is not None]
    as_ofs = [d for d in as_ofs if d is not None]
    newest = max(as_ofs).isoformat(timespec="seconds") if as_ofs else None

    warnings = []
    for cat in mp.MARKET_CATEGORIES:
        rows = categories[cat]
        connected = sum(1 for it in rows if it.get("value") is not None)
        proxy = sum(1 for it in rows
                    if it.get("value") is not None and it["data_mode"] == mp.MODE_PROXY)
        unavailable = len(rows) - connected
        warnings.append(
            f"{cat}: 연동 {connected}/{len(rows)}"
            + (f", 대용(proxy) {proxy}" if proxy else "")
            + (f", 미연동 {unavailable}" if unavailable else ""))

    return {
        "mode": mode,
        "updated_at": newest or fetched_at,
        "as_of": newest,
        "source_summary": source_summary,
        "disclaimer": _DISCLAIMER,
        "categories": categories,
        "items": items,
        "warnings": warnings,
    }


def _unavailable_snapshot(mode: str, summary: str) -> dict:
    """모든 지표를 값 없이(정직하게) 나열한 snapshot — 가짜 값 0건."""
    items = [_null_item(i) for i in mp.all_instruments()]
    return _assemble(items, mode, summary, None)


def _live_snapshot(fetcher, now: datetime | None) -> dict:
    """live fetcher 결과(symbol→quote)에 출처·data_mode·stale을 부착한다.

    fetcher는 app.live_market.fetch_quotes 형태({source, fetched_at, quotes} | None).
    전부 실패/0건이면 unavailable로 강등한다 (가짜 live 주장 금지).
    """
    instruments = mp.all_instruments()
    fetchable = mp.fetchable_instruments()
    if fetcher is None:
        from app import live_market  # 지연 import — 이 모듈은 네트워크를 직접 들이지 않는다
        fetcher = live_market.fetch_quotes
    try:
        raw = fetcher(fetchable)
    except Exception:  # noqa: BLE001 — leaf가 흡수하지 못한 예외도 unavailable로 흡수
        raw = None

    quotes = (raw or {}).get("quotes") or {}
    if not quotes:
        return _unavailable_snapshot(MODE_UNAVAILABLE, _UNAVAILABLE_SUMMARY)

    stale_hours = int((raw or {}).get("stale_after_hours", DEFAULT_STALE_HOURS))
    fetched_at = (raw or {}).get("fetched_at")
    source = (raw or {}).get("source") or mp.PROVIDER_YAHOO

    items = []
    for inst in instruments:
        quote = quotes.get(inst.source_symbol) if inst.source_symbol else None
        if quote and quote.get("value") is not None:
            items.append(_value_item(inst, quote, stale_hours, now))
        else:
            items.append(_null_item(inst))

    connected = sum(1 for it in items if it.get("value") is not None)
    if connected == 0:
        return _unavailable_snapshot(MODE_UNAVAILABLE, _UNAVAILABLE_SUMMARY)

    proxy = sum(1 for it in items
                if it.get("value") is not None and it["data_mode"] == mp.MODE_PROXY)
    proxy_part = f", 대용(proxy) {proxy}종" if proxy else ""
    summary = (f"건설 원자재·국채 금리·환율 {connected}/{len(items)}종 연동 "
               f"— {source} 공개 시세(지연){proxy_part}. 현재 체결값이 아님.")
    return _assemble(items, MODE_LIVE, summary, fetched_at)


def get_market_snapshot(mode: str = "mock", fetcher=None,
                        now: datetime | None = None) -> dict:
    """현재 모드에서 사용할 market snapshot과 provenance를 반환한다.

    mode=="live"일 때만 공개 시세를 시도한다(실패 시 unavailable). 그 외 모드는
    네트워크 0건으로 모든 지표를 값 없이 정직하게 나열한다(가짜 값 미생성).
    fetcher/now는 네트워크 없이 live 분기를 검증할 때만 주입한다(평상시 None).
    """
    selected = (mode or "mock").strip().lower()
    if selected == "live":
        return _live_snapshot(fetcher, now)
    return _unavailable_snapshot(MODE_UNAVAILABLE, _MOCK_SUMMARY)
