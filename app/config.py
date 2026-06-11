"""환경 설정 로더 (도메인 아님 — 공용 인프라).

.env가 있으면 KEY=VALUE 줄만 읽어 os.environ에 보충한다.
P0-A가 사용하는 키는 APP_MODE, DB_PATH뿐이며 기본값만으로도 동작한다.
비밀값을 print/log로 출력하지 않는다.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
TEMPLATES_DIR = BASE_DIR / "templates"
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


_load_dotenv(BASE_DIR / ".env")

# Day-1 기본값은 mock이다. 어떤 설정 실수가 있어도 mock으로 떨어진다.
APP_MODE = (os.environ.get("APP_MODE") or "mock").strip().lower() or "mock"
DB_PATH = os.environ.get("DB_PATH") or str(BASE_DIR / "radar.db")
