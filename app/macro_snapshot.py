"""Macro 도메인 — macro snapshot의 데이터 출처(provenance) 판별 (P0-B6).

이 파일은 "지금 쓸 수 있는 macro 데이터가 무엇이고, 그것이 어떤 출처/상태인지"만
답한다. 핵심 원칙:

- mock 모드: data/mock_macro_snapshot.json만 읽는다 (네트워크 호출 0건).
- live 모드: 아직 미구현 — 명시적으로 연동을 구현하기 전까지는 항상
  unavailable을 반환한다. 가짜 값으로 silent fallback 하지 않는다.
- 파일이 없거나 깨져 있어도 unavailable로 강등할 뿐, 임의의 숫자를 만들지 않는다.
- 표시 레이어(digest/report/dashboard)는 macro_data_mode가 "live"가 아닌 한
  values의 수치를 시세처럼 렌더링해서는 안 된다.

이 파일은 DB·점수·insight·발송을 일절 다루지 않는다.
"""

import json
from pathlib import Path

from app import config

MODE_MOCK_STATIC = "mock_static"
MODE_LIVE = "live"
MODE_UNAVAILABLE = "unavailable"

DEFAULT_SNAPSHOT_PATH = config.DATA_DIR / "mock_macro_snapshot.json"

_UNAVAILABLE_DISCLAIMER = (
    "시장지표 미연동 — 사용할 수 있는 macro 데이터가 없다. "
    "가짜 값으로 대체하지 않는다."
)
_LIVE_NOT_IMPLEMENTED_DISCLAIMER = (
    "live macro 연동은 아직 미구현 — 명시적으로 구현/설정되기 전까지 "
    "unavailable로 처리한다 (가짜 값 fallback 금지)."
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


def get_macro_snapshot(app_mode: str = "mock",
                       snapshot_path: Path | str | None = None) -> dict:
    """현재 모드에서 사용할 macro snapshot과 provenance를 반환한다.

    반환 구조 (항상 동일 키):
        macro_data_mode: "mock_static" | "live" | "unavailable"
        source / updated_at / as_of / is_stale / disclaimer / values

    snapshot_path는 검증기에서 파일 누락/손상 시나리오를 재현할 때만 쓴다.
    """
    mode = (app_mode or "mock").strip().lower()

    if mode != "mock":
        # live 연동은 미구현이다. 어떤 경우에도 mock 고정값이나 임의 숫자로
        # 대체하지 않고 unavailable을 그대로 드러낸다.
        return _unavailable(_LIVE_NOT_IMPLEMENTED_DISCLAIMER)

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
