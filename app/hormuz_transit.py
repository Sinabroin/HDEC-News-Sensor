"""호르무즈 해협 선박 통항량 leaf (D7-AG-3) — 네트워크 격리.

이 파일만 외부 네트워크(IMF PortWatch 공개 ArcGIS FeatureServer)를 호출한다. DB·점수·insight·
발송 어디에도 접근하지 않는다(CLAUDE.md §4 경계 · macro_snapshot/live_macro와 동일 계약).
값을 못 구하면 가짜 수치를 만들지 않고 ``data_mode="unavailable"``로 정직하게 반환한다
(unavailable-on-failure).

데이터 출처: **IMF PortWatch — "Daily Chokepoint Transit Calls and Trade Volume Estimates"**
(위성 AIS 기반 · 일 단위 통항 · 처리 지연 있음). Strait of Hormuz = ``portid="chokepoint6"``.
공개 ArcGIS FeatureServer(API 키 불필요, HTTP GET) — GitHub Actions 배치 모델과 호환된다.

왜 aisstream/yasumorishima repo가 아니라 PortWatch인가:
- 사용자가 소개한 ``yasumorishima/hormuz-ship-tracker``는 aisstream.io WebSocket **상주 스트림**이라
  상주 러너(Raspberry Pi/Docker)와 발급 키가 필요하다 — GitHub Actions 배치 파이프라인에는 즉시
  연동 불가다(docs/operations/HORMUZ_LIVE_INTEGRATION.md §5 결론). 그 저자의 Hugging Face 데이터셋은
  갱신 중단(stale) 상태다.
- PortWatch는 동일 목표(호르무즈 통항 선박량)를 위성 AIS 기반 **일 단위 실측**으로, 키 없이 GET으로
  제공해 우리 파이프라인에서 실제 연동이 가능하다. 상주 러너/키가 확보되면 aisstream 준실시간
  게이트 IN/OUT 통항을 병기하는 업그레이드가 §6 계획에 남아있다.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone

# 공개 FeatureServer(키 불필요). 제공자 교체 시 이 상수와 파서만 바꾼다(네트워크는 leaf 전용).
_PORTWATCH_QUERY_URL = (
    "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/ArcGIS/rest/services/"
    "Daily_Chokepoints_Data/FeatureServer/0/query"
)
_HORMUZ_PORTID = "chokepoint6"
_SOURCE_PAGE_URL = "https://portwatch.imf.org/pages/chokepoint6"
_TIMEOUT_SECONDS = 20
_RECENT_DAYS = 8  # 최신 + 직전 7일(추세/평균 산출용)

_SOURCE_LABEL = "IMF PortWatch"
_SOURCE_DETAIL = "Daily Chokepoint Transit Calls · 위성 AIS 기반 일 단위 통항 추정"

_UNAVAILABLE_REASON = "AIS 선박 통항 데이터 소스 미연결 — 기사 기반 대체 아님."


def _unavailable(reason: str = _UNAVAILABLE_REASON) -> dict:
    """가짜 수치 없이 정직한 미연동 상태만 반환한다."""
    return {
        "data_mode": "unavailable",
        "source": _SOURCE_LABEL,
        "source_detail": _SOURCE_DETAIL,
        "source_url": _SOURCE_PAGE_URL,
        "chokepoint": "Strait of Hormuz",
        "chokepoint_kr": "호르무즈 해협",
        "unavailable_reason": reason,
    }


def _fetch_features() -> list[dict]:
    """PortWatch에서 호르무즈(chokepoint6) 최근 통항 레코드를 내림차순으로 받는다."""
    params = urllib.parse.urlencode({
        "where": f"portid='{_HORMUZ_PORTID}'",
        "outFields": ("date,portname,n_total,n_tanker,n_cargo,"
                      "n_container,n_dry_bulk,n_general_cargo,n_roro"),
        "orderByFields": "date DESC",
        "resultRecordCount": _RECENT_DAYS,
        "returnGeometry": "false",
        "f": "json",
    })
    req = urllib.request.Request(f"{_PORTWATCH_QUERY_URL}?{params}", method="GET")
    req.add_header("Accept", "application/json")
    with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if "error" in payload:
        raise ValueError(f"portwatch error: {payload.get('error')}")
    return [f.get("attributes") or {} for f in (payload.get("features") or [])]


def _to_date(raw) -> date | None:
    """PortWatch date 필드('YYYY-MM-DD' 또는 epoch ms)를 date로 정규화한다."""
    if raw in (None, ""):
        return None
    if isinstance(raw, (int, float)):
        try:
            return datetime.fromtimestamp(raw / 1000, tz=timezone.utc).date()
        except (OverflowError, OSError, ValueError):
            return None
    try:
        return datetime.strptime(str(raw)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _pct_delta(current, base) -> float | None:
    """base 대비 current 변화율(%). base가 0/None이면 None(가짜 비율 금지)."""
    if current is None or not base:
        return None
    try:
        return round((current - base) / base * 100.0, 1)
    except (TypeError, ZeroDivisionError):
        return None


def fetch_hormuz_transit(mode: str = "mock", today: date | None = None) -> dict:
    """호르무즈 해협 일일 선박 통항량 스냅샷을 반환한다.

    mode != "live"  → 네트워크 0건, ``unavailable``(정직한 미연동, 가짜 수치 없음).
    mode == "live"  → PortWatch GET. 성공하면 최신 관측일의 통항 수·선종 분해·직전일/7일 평균 대비
                      변화율·처리 지연(lag)을 담아 ``data_mode="live"``로 반환. 어떤 실패든 unavailable.

    반환 계약(가짜 수치 절대 생성 금지 · 값 없으면 키를 비운다):
      data_mode, source, source_detail, source_url, chokepoint(_kr),
      observed_date, n_total, n_tanker, n_cargo, n_container, n_dry_bulk,
      n_general_cargo, n_roro, prev_day, prev7_avg, delta_vs_prev_day_pct,
      delta_vs_prev7_pct, recent[], lag_days, coverage_note, updated_at
    """
    if (mode or "mock").strip().lower() != "live":
        return _unavailable()

    try:
        rows = _fetch_features()
    except (urllib.error.HTTPError, urllib.error.URLError, OSError,
            ValueError, json.JSONDecodeError):
        return _unavailable()
    if not rows:
        return _unavailable()

    # date 파싱 후 내림차순 정렬(제공자 정렬을 신뢰하지 않고 재정렬).
    parsed = []
    for row in rows:
        d = _to_date(row.get("date"))
        total = _int(row.get("n_total"))
        if d is None or total is None:
            continue
        parsed.append((d, row, total))
    if not parsed:
        return _unavailable()
    parsed.sort(key=lambda item: item[0], reverse=True)

    latest_date, latest, latest_total = parsed[0]
    prior = parsed[1:]

    prev_day = None
    if prior:
        pd_date, _pd_row, pd_total = prior[0]
        prev_day = {"date": pd_date.isoformat(), "n_total": pd_total}

    prior_totals = [total for _d, _r, total in prior]
    prev7_avg = round(sum(prior_totals) / len(prior_totals), 1) if prior_totals else None

    recent = [
        {
            "date": d.isoformat(),
            "n_total": total,
            "n_tanker": _int(row.get("n_tanker")),
            "n_cargo": _int(row.get("n_cargo")),
        }
        for d, row, total in reversed(parsed)  # 오름차순(오래된→최신) — 미니 추세용
    ]

    ref = today or datetime.now(timezone.utc).date()
    lag_days = max((ref - latest_date).days, 0)

    coverage_note = (
        f"IMF PortWatch 위성 AIS 기반 일 단위 통항 추정치 · 관측일({latest_date.isoformat()}) 기준 "
        f"약 {lag_days}일 지연"
    )

    return {
        "data_mode": "live",
        "source": _SOURCE_LABEL,
        "source_detail": _SOURCE_DETAIL,
        "source_url": _SOURCE_PAGE_URL,
        "chokepoint": "Strait of Hormuz",
        "chokepoint_kr": "호르무즈 해협",
        "observed_date": latest_date.isoformat(),
        "n_total": latest_total,
        "n_tanker": _int(latest.get("n_tanker")),
        "n_cargo": _int(latest.get("n_cargo")),
        "n_container": _int(latest.get("n_container")),
        "n_dry_bulk": _int(latest.get("n_dry_bulk")),
        "n_general_cargo": _int(latest.get("n_general_cargo")),
        "n_roro": _int(latest.get("n_roro")),
        "prev_day": prev_day,
        "prev7_avg": prev7_avg,
        "delta_vs_prev_day_pct": _pct_delta(latest_total,
                                            prev_day["n_total"] if prev_day else None),
        "delta_vs_prev7_pct": _pct_delta(latest_total, prev7_avg),
        "recent": recent,
        "lag_days": lag_days,
        "coverage_note": coverage_note,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
