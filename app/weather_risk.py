"""Weather Risk 도메인 (leaf) — 명일 정오 시공 리스크 데이터 (D7-AE).

대시보드의 "명일 정오 시공 리스크" 카드(D7-AD-U에서 UI 계약만 존재)를 실제 데이터로
채우는 유일한 소스. 권역 대표점의 명일(D+1) 정오 12:00(현지) 예보를 공개·무키
Open-Meteo forecast API에서 1회 batch로 받아 rule-based 시공 리스크 등급을 산정한다.

경계(이 파일만 한다 / 절대 안 한다):
- 한다: Open-Meteo 공개 예보 GET(권역 batch 1회), D+1 12:00 시점 추출, rule-based
  등급 산정(RISK_RULES와 1:1), 실패 시 unavailable 반환. 순수 leaf — DB 0건,
  app.config import 0건, env 읽기 0건(모드는 호출 인자).
- 안 한다: 점수/insight/발송, 데모 기상값 생성(시장과 달리 기상은 데모 픽스처도
  금지 — 임의 숫자가 실제 예보처럼 읽힌다), 실패 시 mock 숫자 fallback.

정직성 계약:
- mock(기본) 모드는 네트워크 0건 · weather_rows=[] · unavailable 사유 명시.
- live 모드에서 API 실패/무응답이면 unavailable("기상 데이터 미수신") — 가짜 값 0건.
- 권역별 부분 실패는 해당 행만 '확인 필요'로 남긴다(다른 권역 값은 실측 유지).
- 특보(기상청 기상특보)는 무료 무키 API가 없어 미연동 — 행마다 '수동 확인'으로
  정직하게 표기한다(가짜 '특보 없음' 단정 금지).

소스 프로빙(lesson: macro-live-source-probe): 파서 작성 전 실제 GET 확인 완료 —
multi-location 요청은 JSON 배열을 반환하고, timezone 파라미터로 hourly.time이
현지 ISO(분 단위)로 온다. wind_speed_unit=ms로 풍속/돌풍이 m/s 단위.
"""

import json
import urllib.request
from datetime import datetime, timedelta, timezone

USER_AGENT = "HDEC-Executive-Radar/1.0 (weather_risk leaf)"
DEFAULT_TIMEOUT = 12
API_BASE = "https://api.open-meteo.com/v1/forecast"
SOURCE_LABEL = "Open-Meteo 공개 예보"

MODE_LIVE = "live"
MODE_UNAVAILABLE = "unavailable"

GRADE_LOW = "낮음"
GRADE_WATCH = "주의"
GRADE_HIGH = "높음"
GRADE_UNKNOWN = "확인 필요"

_KST = timezone(timedelta(hours=9))

# 권역 대표점 — 권역 전체가 아니라 '대표 지점 기준'임을 basis로 항상 명시한다(정직성).
# 중동·해외는 워치리스트 해외 현장이 밀집한 중동 허브(리야드)를 대표점으로 쓴다.
# 오프셋 고정 tz(서울 +9 / 리야드 +3, 둘 다 DST 없음)라 zoneinfo 의존이 없다.
REGIONS = (
    {"id": "capital", "label": "수도권", "basis": "서울 기준",
     "lat": 37.5665, "lon": 126.9780, "tz": "Asia/Seoul", "utc_offset": 9},
    {"id": "central", "label": "중부권", "basis": "대전 기준",
     "lat": 36.3504, "lon": 127.3845, "tz": "Asia/Seoul", "utc_offset": 9},
    {"id": "yeongnam", "label": "영남권", "basis": "부산 기준",
     "lat": 35.1796, "lon": 129.0756, "tz": "Asia/Seoul", "utc_offset": 9},
    {"id": "honam", "label": "호남권", "basis": "광주 기준",
     "lat": 35.1595, "lon": 126.8526, "tz": "Asia/Seoul", "utc_offset": 9},
    {"id": "mideast", "label": "중동·해외", "basis": "리야드 기준",
     "lat": 24.7136, "lon": 46.6753, "tz": "Asia/Riyadh", "utc_offset": 3},
)

# 시공 리스크 규칙 — 코드와 UI가 같은 표를 쓴다(추적 가능성). level: 낮음<주의<높음.
# 근거: 산업안전보건기준에 관한 규칙 제37조(순간풍속 10m/s 초과 시 타워크레인
# 설치·수리·점검·해체 중지, 15m/s 초과 시 운전 중지), 폭염 33°C/한파 -12°C는
# 기상청 특보 기준을 옥외작업 주의 신호로 쓴다.
RISK_RULES = (
    {"id": "rain_watch", "label": "우천 주의",
     "threshold": "강수확률 ≥60% 및 예상 강수량 ≥1mm",
     "level": GRADE_WATCH, "impact": "타설·외장방수·토공 지연 검토"},
    {"id": "rain_high", "label": "우천 높음",
     "threshold": "강수확률 ≥80% 및 예상 강수량 ≥5mm",
     "level": GRADE_HIGH, "impact": "옥외 습식공정 중지 검토"},
    {"id": "wind_watch", "label": "강풍 주의",
     "threshold": "돌풍 ≥10m/s",
     "level": GRADE_WATCH,
     "impact": "타워크레인 설치·수리·해체 중지 기준(산업안전보건기준 규칙 제37조)"},
    {"id": "wind_high", "label": "강풍 높음",
     "threshold": "돌풍 ≥15m/s",
     "level": GRADE_HIGH,
     "impact": "타워크레인 운전 중지 기준(산업안전보건기준 규칙 제37조) · 양중/고소작업 중지"},
    {"id": "heat", "label": "폭염 주의", "threshold": "기온 ≥33°C",
     "level": GRADE_WATCH, "impact": "옥외작업 휴식시간제 · 온열질환 예방"},
    {"id": "cold", "label": "한파 주의", "threshold": "기온 ≤-12°C",
     "level": GRADE_WATCH, "impact": "콘크리트 양생·옥외작업 보온 대책"},
)
_RULES_BY_ID = {r["id"]: r for r in RISK_RULES}

TARGET_LABEL = "명일(D+1) 정오 12:00 · 권역 현지시각"
ADVISORY_NOTE = "기상특보(기상청)는 무료 무키 API 미연동 — 발령 여부 수동 확인 필요"
_UNAVAILABLE_REASON = "기상 데이터 미수신 — 공개 예보 API 응답 없음. 값을 만들지 않습니다."
_MOCK_REASON = _UNAVAILABLE_REASON
_FAIL_REASON = _UNAVAILABLE_REASON

_HOURLY_FIELDS = ("temperature_2m", "precipitation_probability", "precipitation",
                  "wind_speed_10m", "wind_gusts_10m")


def _num(value):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out


def grade_construction_risk(precip_prob=None, precip_mm=None, wind_ms=None,
                            gust_ms=None, temp_c=None) -> dict:
    """예보 수치 → 시공 리스크 등급 (rule-based · RISK_RULES와 1:1 · 결정적).

    값이 전부 없으면 '확인 필요'(가짜 등급 금지). 있는 값만으로 규칙을 평가하고
    발동 규칙 중 최고 레벨이 등급이 된다(낮음<주의<높음). reasons에는 발동 규칙 id,
    flags에는 폭염/한파 표기가 담긴다. 돌풍값이 없으면 평균 풍속으로 보수 평가한다.
    """
    pp, pm = _num(precip_prob), _num(precip_mm)
    wind, gust, temp = _num(wind_ms), _num(gust_ms), _num(temp_c)
    if all(v is None for v in (pp, pm, wind, gust, temp)):
        return {"grade": GRADE_UNKNOWN, "reasons": [], "flags": []}
    hits: list = []
    if pp is not None and pm is not None:
        if pp >= 80 and pm >= 5:
            hits.append("rain_high")
        elif pp >= 60 and pm >= 1:
            hits.append("rain_watch")
    gust_eff = gust if gust is not None else wind
    if gust_eff is not None:
        if gust_eff >= 15:
            hits.append("wind_high")
        elif gust_eff >= 10:
            hits.append("wind_watch")
    flags: list = []
    if temp is not None and temp >= 33:
        hits.append("heat")
        flags.append("폭염")
    if temp is not None and temp <= -12:
        hits.append("cold")
        flags.append("한파")
    grade = GRADE_LOW
    if any(_RULES_BY_ID[h]["level"] == GRADE_HIGH for h in hits):
        grade = GRADE_HIGH
    elif hits:
        grade = GRADE_WATCH
    return {"grade": grade, "reasons": hits, "flags": flags}


def rule_labels(rule_ids) -> list:
    """규칙 id 리스트 → 사람이 읽는 라벨(UI 표시용 · 미지의 id는 버린다)."""
    return [_RULES_BY_ID[i]["label"] for i in rule_ids or [] if i in _RULES_BY_ID]


# ---------------------------------------------------------------------------
# fetch (이 leaf만 네트워크 소유)
# ---------------------------------------------------------------------------

def _build_url(regions=REGIONS) -> str:
    lats = ",".join(f"{r['lat']:.4f}" for r in regions)
    lons = ",".join(f"{r['lon']:.4f}" for r in regions)
    tzs = ",".join(str(r["tz"]).replace("/", "%2F") for r in regions)
    return (f"{API_BASE}?latitude={lats}&longitude={lons}"
            f"&hourly={','.join(_HOURLY_FIELDS)}"
            f"&wind_speed_unit=ms&forecast_days=3&timezone={tzs}")


def _fetch_forecasts(timeout: int = DEFAULT_TIMEOUT):
    """Open-Meteo batch 예보 GET — 권역 순서와 동일한 리스트를 반환(실패는 호출자 처리)."""
    req = urllib.request.Request(_build_url(), headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (공개 예보 API)
        data = json.loads(resp.read().decode("utf-8"))
    if isinstance(data, dict):
        data = [data]
    return data if isinstance(data, list) else None


# ---------------------------------------------------------------------------
# 시점 추출 + 행 조립
# ---------------------------------------------------------------------------

def _coerce_now(now=None) -> datetime:
    """now → 항상 UTC로 정규화한 tz-aware datetime.

    함정: brief.generated_at는 KST(+09:00) ISO다 — 원본 tz를 유지한 채 utc_offset을
    더하면 이중 가산되어 D+1이 D+2로 밀린다. 반드시 UTC로 변환 후 쓴다.
    """
    if isinstance(now, datetime):
        dt = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    if now:
        try:
            dt = datetime.fromisoformat(str(now))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except (TypeError, ValueError):
            pass
    return datetime.now(timezone.utc)


def _target_local_iso(now_utc: datetime, utc_offset: int) -> str:
    """권역 현지 기준 '명일 12:00' — Open-Meteo hourly.time(현지 ISO 분 단위)과 동일 포맷."""
    local_today = (now_utc + timedelta(hours=utc_offset)).date()
    return f"{(local_today + timedelta(days=1)).isoformat()}T12:00"


def _unavailable_row(region: dict, note: str) -> dict:
    return {
        "region_id": region["id"], "region": region["label"], "basis": region["basis"],
        "target_local": "", "precip_prob": None, "precip_mm": None,
        "wind_ms": None, "gust_ms": None, "temp_c": None,
        "flags": [], "advisory": "수동 확인",
        "risk_grade": GRADE_UNKNOWN, "risk_reasons": [], "risk_labels": [],
        "row_status": "unavailable", "status_note": note,
    }


def _row_from_forecast(region: dict, forecast: dict, now_utc: datetime) -> dict:
    hourly = forecast.get("hourly") if isinstance(forecast, dict) else None
    times = (hourly or {}).get("time") or []
    target = _target_local_iso(now_utc, int(region["utc_offset"]))
    try:
        idx = times.index(target)
    except ValueError:
        return _unavailable_row(region, "예보 응답에 명일 정오 시점 없음")

    def at(key):
        arr = (hourly or {}).get(key) or []
        return arr[idx] if 0 <= idx < len(arr) else None

    pp, pm = _num(at("precipitation_probability")), _num(at("precipitation"))
    wind, gust = _num(at("wind_speed_10m")), _num(at("wind_gusts_10m"))
    temp = _num(at("temperature_2m"))
    verdict = grade_construction_risk(pp, pm, wind, gust, temp)
    return {
        "region_id": region["id"], "region": region["label"], "basis": region["basis"],
        "target_local": target, "precip_prob": pp, "precip_mm": pm,
        "wind_ms": wind, "gust_ms": gust, "temp_c": temp,
        "flags": verdict["flags"], "advisory": "수동 확인",
        "risk_grade": verdict["grade"], "risk_reasons": verdict["reasons"],
        "risk_labels": rule_labels(verdict["reasons"]),
        "row_status": "ok", "status_note": "",
    }


# ---------------------------------------------------------------------------
# 모델 스냅샷 (빌더 진입점)
# ---------------------------------------------------------------------------

def snapshot_for_model(mode: str = "mock", now=None, fetcher=None) -> dict:
    """대시보드 모델용 weather_* 키 묶음.

    mode != "live" → 네트워크 0건, unavailable(사유 명시, weather_live_attempted=False).
    mode == "live" → fetcher(기본 _fetch_forecasts) 1회 호출. 실패/무응답이면
    unavailable(weather_live_attempted=True, '기상 데이터 미수신') — mock 숫자
    fallback을 만들지 않는다. now(빌드 시각)를 주입하면 D+1 판정과 updated_at이
    결정적이 된다(테스트/검증기용 · 미주입 시 현재 시각).
    """
    base = {
        "weather_target_label": TARGET_LABEL,
        "weather_advisory_note": ADVISORY_NOTE,
        "weather_risk_rules": [dict(r) for r in RISK_RULES],
    }
    mode = (mode or "mock").strip().lower()
    if mode != MODE_LIVE:
        return {
            **base,
            "weather_data_mode": MODE_UNAVAILABLE,
            "weather_live_attempted": False,
            "weather_source": "",
            "weather_updated_at": "",
            "weather_target_time": "",
            "weather_rows": [],
            "weather_unavailable_reason": _MOCK_REASON,
        }

    now_utc = _coerce_now(now)
    fetch = fetcher if fetcher is not None else _fetch_forecasts
    try:
        forecasts = fetch()
    except Exception:  # noqa: BLE001 — 네트워크/파싱 실패는 unavailable로 정직 강등
        forecasts = None
    if not isinstance(forecasts, list) or not forecasts:
        return {
            **base,
            "weather_data_mode": MODE_UNAVAILABLE,
            "weather_live_attempted": True,
            "weather_source": "",
            "weather_updated_at": "",
            "weather_target_time": "",
            "weather_rows": [],
            "weather_unavailable_reason": _FAIL_REASON,
        }

    rows = []
    for i, region in enumerate(REGIONS):
        forecast = forecasts[i] if i < len(forecasts) else None
        if isinstance(forecast, dict):
            rows.append(_row_from_forecast(region, forecast, now_utc))
        else:
            rows.append(_unavailable_row(region, "예보 응답 누락"))
    if all(r["row_status"] == "unavailable" for r in rows):
        return {
            **base,
            "weather_data_mode": MODE_UNAVAILABLE,
            "weather_live_attempted": True,
            "weather_source": "",
            "weather_updated_at": "",
            "weather_target_time": "",
            "weather_rows": [],
            "weather_unavailable_reason": _FAIL_REASON,
        }
    return {
        **base,
        "weather_data_mode": MODE_LIVE,
        "weather_live_attempted": True,
        "weather_source": SOURCE_LABEL,
        "weather_updated_at": now_utc.astimezone(_KST).strftime("%Y-%m-%d %H:%M"),
        "weather_target_time": _target_local_iso(now_utc, 9),
        "weather_rows": rows,
        "weather_unavailable_reason": "",
    }
