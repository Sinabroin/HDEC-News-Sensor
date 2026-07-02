"""FRED 공개 CSV 시계열 leaf (D7-AD-X) — API key 불필요.

St. Louis Fed graph CSV endpoint만 사용한다. market_snapshot/build_static_dashboard가
live 모드에서만 호출한다. 실패 시 None(가짜 값 금지).
"""

from __future__ import annotations

import csv
import io
import urllib.parse
import urllib.request
from datetime import datetime, timezone

USER_AGENT = ("Mozilla/5.0 (compatible; HDEC-Executive-Radar/0.1; "
              "+public-fred-csv; non-crawling)")
DEFAULT_TIMEOUT = 10
FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"

# dashboard id → (FRED series, sane_min, sane_max)
FRED_SERIES_BY_ID = {
    "us_2y": ("DGS2", 0.0, 25.0),
    "kr_10y": ("IRLTLT01KRM156N", 0.0, 25.0),
}

_QUOTES_CACHE: dict | None = None


def reset_cache() -> None:
    global _QUOTES_CACHE
    _QUOTES_CACHE = None


def _num(value) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        s = str(value).strip().replace(",", "")
        if not s or s == ".":
            return None
        return float(s)
    except (TypeError, ValueError):
        return None


def _fetch_csv(series_id: str, timeout: int) -> list[tuple[str, float]]:
    url = FRED_CSV_URL.format(series_id=urllib.parse.quote(series_id, safe=""))
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        text = resp.read().decode(resp.headers.get_content_charset() or "utf-8",
                                  errors="replace")
    rows = []
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 2:
            continue
        val = _num(row[1])
        if val is not None:
            rows.append((row[0].strip(), val))
    return rows


def fetch_latest(series_id: str, sane_min: float | None = None,
                 sane_max: float | None = None,
                 timeout: int | None = None) -> dict | None:
    """최신 관측 1건 → {value, as_of, direction?}. 실패/범위 밖이면 None."""
    to = int(timeout if timeout is not None else DEFAULT_TIMEOUT)
    try:
        rows = _fetch_csv(series_id, to)
    except Exception:  # noqa: BLE001
        return None
    if not rows:
        return None
    date_str, val = rows[-1]
    lo, hi = _num(sane_min), _num(sane_max)
    if (lo is not None and val < lo) or (hi is not None and val > hi):
        return None
    direction = None
    if len(rows) >= 2:
        prev = rows[-2][1]
        if val > prev:
            direction = "up"
        elif val < prev:
            direction = "down"
        else:
            direction = "flat"
    try:
        as_of = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        as_of_iso = as_of.isoformat(timespec="seconds")
    except ValueError:
        as_of_iso = None
    return {"value": round(val, 3), "as_of": as_of_iso, "direction": direction}


def fetch_quotes_by_id(use_cache: bool = True,
                       timeout: int | None = None) -> dict | None:
    """FRED_SERIES_BY_ID 전체 수집 → source-id 포함 quote. 0건이면 None."""
    global _QUOTES_CACHE
    if use_cache and _QUOTES_CACHE is not None:
        return _QUOTES_CACHE
    out: dict = {}
    for iid, (series, lo, hi) in FRED_SERIES_BY_ID.items():
        q = fetch_latest(series, lo, hi, timeout=timeout)
        if q:
            q["source_id"] = series
            q["source"] = f"FRED {series} (St. Louis Fed, public CSV)"
            q["frequency"] = "monthly" if iid == "kr_10y" else "daily_business_days"
            out[iid] = q
    if not out:
        return None
    snap = {
        "source": "FRED (St. Louis Fed, public CSV)",
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "stale_after_hours": 48,
        "quotes_by_id": out,
    }
    if use_cache:
        _QUOTES_CACHE = snap
    return snap
