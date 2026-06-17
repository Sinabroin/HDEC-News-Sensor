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

# P0-C1 — 뉴스 수집 모드. 기본값 mock은 네트워크 호출이 0건이며 어떤 비밀값도
# 필요 없다 (rules.md §2의 "mock = 외부 호출 금지" 안전성을 그대로 유지한다).
# NEWS_MODE=live를 운영자가 명시적으로 설정했을 때만 app/live_collector.py가
# 공개 RSS를 시도한다. 어떤 값으로 잘못 설정해도 mock으로 떨어진다.
NEWS_MODE = (os.environ.get("NEWS_MODE") or "mock").strip().lower() or "mock"

# P0-C2 — macro snapshot(시장지표) 모드. NEWS_MODE와 완전히 독립적이며 동일한
# 안전 계약을 따른다: 기본값 mock = 네트워크 0건 / 비밀값 0건 (data/mock_macro_snapshot.json만).
# MACRO_MODE=live를 운영자가 명시적으로 설정했을 때만 app/live_macro.py가 공개
# 시세 API(API key 불필요)를 시도한다. 수집 실패 시 가짜 값으로 채우지 않고
# unavailable로 강등한다. 어떤 값으로 잘못 설정해도 mock으로 떨어진다.
MACRO_MODE = (os.environ.get("MACRO_MODE") or "mock").strip().lower() or "mock"

# P0-C2 — 같은 publish 실행에서 report 빌드(build_static_report)와 digest 발송(send_telegram)이
# 서로 다른 프로세스로 돌면 각자 live 시세를 따로 fetch해 값/기준시각이 미세하게 어긋날 수 있다.
# 이 env에 (CI에서 그 실행에만 쓰는 1회용 임시 경로를) 지정하면 먼저 도는 프로세스가 fetch한
# raw snapshot을 파일로 남기고, 뒤따르는 프로세스는 그 파일을 읽어 동일 snapshot을 쓴다(재fetch 없음).
# 미설정(기본)이면 파일 공유 없이 각자 fetch하되, 같은 프로세스 안에서는 live_macro의 프로세스
# 캐시가 중복 fetch를 막는다. 저장소에 commit하지 않는 휘발 경로를 쓴다(가짜 값 0건 계약 유지).
MACRO_SNAPSHOT_FILE = (os.environ.get("MACRO_SNAPSHOT_FILE") or "").strip() or None
