"""Macro 도메인 — macro snapshot의 데이터 출처(provenance)·신선도 판별 (P0-B6 → P0-C2).

이 파일은 "지금 쓸 수 있는 macro 데이터가 무엇이고, 그것이 어떤 출처/상태인지"만
답한다. 핵심 원칙:

- mock 모드: data/mock_macro_snapshot.json만 읽는다 (네트워크 호출 0건).
- live 모드(P0-C2): 네트워크 IO는 leaf 모듈 app/live_macro.py가 소유한다(공개 시세 API,
  무인증). 이 파일은 그 fetcher를 호출만 하고(collector → live_collector와 동일 패턴),
  결과에 출처·기준시각·stale(지연) 플래그를 부착한다. 이 파일 자체는 네트워크를 import하지 않는다.
- live 수집이 실패/0건이면 가짜 값으로 silent fallback 하지 않고 unavailable을 드러낸다.
- mock 파일이 없거나 깨져 있어도 unavailable로 강등할 뿐, 임의의 숫자를 만들지 않는다.
- 표시 레이어(digest/report/dashboard)는 macro_data_mode가 "live"가 아닌 한
  values의 수치를 시세처럼 렌더링해서는 안 된다.

이 파일은 DB·점수·insight·발송을 일절 다루지 않는다.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from app import config

MODE_MOCK_STATIC = "mock_static"
MODE_LIVE = "live"
MODE_UNAVAILABLE = "unavailable"

DEFAULT_SNAPSHOT_PATH = config.DATA_DIR / "mock_macro_snapshot.json"
DEFAULT_STALE_HOURS = 24

_UNAVAILABLE_DISCLAIMER = (
    "시장지표 미연동 — 사용할 수 있는 macro 데이터가 없다. "
    "가짜 값으로 대체하지 않는다."
)
_LIVE_FETCH_FAILED_DISCLAIMER = (
    "live 시장지표 수집 실패/0건 — 가짜 값으로 채우지 않고 unavailable로 처리한다 "
    "(미연동과 동일하게 수치를 시세처럼 표시하지 않는다)."
)
_LIVE_OK_DISCLAIMER = (
    "시장지표는 공개 시세 API 기준 참고용 데이터입니다. "
    "현재 체결값이 아니며, 출처·기준시각을 함께 확인해야 합니다."
)
_LIVE_STALE_SUFFIX = (
    " stale(지연) 상태입니다. "
    "최신 갱신이 지연된 공개 시세 API 기준 참고용 데이터이며 현재 체결값이 아닙니다."
)


def _unavailable(disclaimer: str) -> dict:
    return {
        "macro_data_mode": MODE_UNAVAILABLE,
        "source": None,
        "updated_at": None,
        "as_of": None,
        "is_stale": True,
        "disclaimer": disclaimer,
        "values": [],
    }


def _load_mock_file(path: Path) -> dict | None:
    """mock snapshot 파일을 읽는다. 없거나 형식이 깨지면 None (가짜 값 생성 금지)."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    values = data.get("values")
    if not isinstance(values, list) or not values:
        return None
    if not all(isinstance(v, dict) and v.get("label") and v.get("value") is not None
               for v in values):
        return None
    return data


def _newest_as_of(values: list[dict]) -> tuple[str | None, datetime | None]:
    """values 중 가장 최근 as_of(ISO)와 그 datetime을 돌려준다 (없으면 (None, None))."""
    best_iso, best_dt = None, None
    for v in values:
        iso = v.get("as_of")
        if not iso:
            continue
        try:
            dt = datetime.fromisoformat(iso)
        except (TypeError, ValueError):
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if best_dt is None or dt > best_dt:
            best_dt, best_iso = dt, iso
    return best_iso, best_dt


def _is_stale(newest_dt: datetime | None, stale_after_hours: int,
              now: datetime | None = None) -> bool:
    """기준시각을 알 수 없거나(보수적으로 stale) 임계 시간을 초과하면 stale."""
    if newest_dt is None:
        return True
    ref = now or datetime.now(timezone.utc)
    age_hours = (ref - newest_dt).total_seconds() / 3600
    return age_hours > max(0, stale_after_hours)


def _cache_path() -> Path | None:
    """같은 publish 실행에서 프로세스 간 snapshot을 공유할 파일 경로 (미설정이면 None)."""
    raw = getattr(config, "MACRO_SNAPSHOT_FILE", None)
    return Path(raw) if raw else None


def _read_shared_snapshot(path: Path | None) -> dict | None:
    """다른 프로세스(예: 리포트 빌드)가 남긴 raw snapshot을 읽는다 (없거나 깨지면 None).

    파일은 leaf가 반환하는 raw 형태({source, fetched_at, stale_after_hours, values})다.
    여기서는 신선도를 판단하지 않고 그대로 돌려준다 — is_stale은 호출부가 now 기준으로 다시
    계산하므로 오래된 파일이라도 지연 여부를 정직하게 표기한다 (가짜 신선도 주장 없음).
    """
    if not path or not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    values = data.get("values")
    if not isinstance(values, list) or not values:
        return None
    return data


def _write_shared_snapshot(path: Path | None, snap: dict) -> None:
    """fetch한 raw snapshot을 뒤따르는 프로세스가 재사용하도록 파일로 남긴다 (best-effort).

    실패해도 무시한다(공유 캐시는 최적화일 뿐 — 없으면 각자 fetch). 호출부가 values가 있을
    때만 호출하므로 가짜/빈 값을 쓰지 않는다. 시장지표 숫자만 담기며 비밀값은 없다.
    """
    if not path:
        return
    try:
        path.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _live_snapshot(fetcher, now: datetime | None) -> dict:
    """live fetcher 결과에 출처·기준시각·stale 플래그를 부착한다.

    fetcher는 app.live_macro.fetch_snapshot 형태({source, values, ...} | None)를 따른다.
    실패/0건이면 unavailable로 강등한다 (가짜 live 주장 금지).

    MACRO_SNAPSHOT_FILE이 설정돼 있으면 같은 실행의 다른 프로세스가 남긴 snapshot을 먼저
    읽어(재fetch 없이) 출력 간 값/기준시각 불일치를 막고, 없으면 fetch 후 파일로 남긴다.
    """
    cache_path = _cache_path()
    snap = _read_shared_snapshot(cache_path)  # 다른 프로세스가 남긴 동일 snapshot 우선
    if snap is None:
        if fetcher is None:
            from app import live_macro  # 지연 import — 이 모듈은 네트워크를 직접 들이지 않는다
            fetcher = live_macro.fetch_snapshot
        try:
            snap = fetcher()
        except Exception:  # noqa: BLE001 — leaf가 흡수하지 못한 예외도 unavailable로 흡수
            snap = None
        if snap and snap.get("values"):
            _write_shared_snapshot(cache_path, snap)

    values = (snap or {}).get("values") or []
    if not snap or not values:
        return _unavailable(_LIVE_FETCH_FAILED_DISCLAIMER)

    stale_hours = int(snap.get("stale_after_hours", DEFAULT_STALE_HOURS))
    newest_iso, newest_dt = _newest_as_of(values)
    is_stale = _is_stale(newest_dt, stale_hours, now)
    disclaimer = _LIVE_OK_DISCLAIMER + (_LIVE_STALE_SUFFIX if is_stale else "")

    return {
        "macro_data_mode": MODE_LIVE,
        "source": snap.get("source") or "live",
        "updated_at": newest_iso or snap.get("fetched_at"),
        "as_of": newest_iso or snap.get("fetched_at"),
        "fetched_at": snap.get("fetched_at"),
        "is_stale": is_stale,
        "disclaimer": disclaimer,
        "values": values,
    }


def get_macro_snapshot(mode: str = "mock",
                       snapshot_path: Path | str | None = None,
                       fetcher=None,
                       now: datetime | None = None) -> dict:
    """현재 모드에서 사용할 macro snapshot과 provenance를 반환한다.

    반환 구조 (항상 동일 키):
        macro_data_mode: "mock_static" | "live" | "unavailable"
        source / updated_at / as_of / is_stale / disclaimer / values

    snapshot_path는 mock 파일 누락/손상 시나리오를, fetcher/now는 live 분기를
    네트워크 없이 검증할 때만 주입한다 (둘 다 평상시 None).
    """
    selected = (mode or "mock").strip().lower()

    if selected == "live":
        # live 수집은 leaf(app/live_macro.py)가 담당한다. 실패하면 가짜 값으로
        # 대체하지 않고 unavailable을 그대로 드러낸다.
        return _live_snapshot(fetcher, now)

    path = Path(snapshot_path) if snapshot_path else DEFAULT_SNAPSHOT_PATH
    data = _load_mock_file(path)
    if data is None:
        return _unavailable(_UNAVAILABLE_DISCLAIMER)

    return {
        "macro_data_mode": MODE_MOCK_STATIC,
        "source": data.get("source") or "demo_mock",
        "updated_at": data.get("updated_at"),
        "as_of": data.get("as_of"),
        # mock 고정값은 정의상 항상 stale이다 — 신선도를 주장하지 않는다.
        "is_stale": True,
        "disclaimer": data.get("disclaimer") or _UNAVAILABLE_DISCLAIMER,
        "values": data["values"],
    }
