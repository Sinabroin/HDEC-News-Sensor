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

# P0-D2 — Naver News Search API 보조 provider 스위치. 기본값 off = 네트워크/비밀값 0건이며
# 기존 Google-only 수집 동작을 그대로 유지한다 (rules.md §2 안전 계약과 동일). NEWS_MODE와
# 독립적이다: 이 플래그를 명시적으로 켜고(아래) 자격증명이 모두 있을 때만 app/naver_news_provider.py가
# 공식 검색 API(https://openapi.naver.com/v1/search/news.json)를 호출한다. 자격증명이 없으면
# 전체 live 수집을 실패시키지 않고 정직하게 skip한다(provider_status: skipped_missing_credentials).
# 자격증명은 환경변수에서만 읽고 어디에도 print/log/직렬화하지 않는다 (rules.md §4).
NAVER_NEWS_ENABLED = (os.environ.get("NAVER_NEWS_ENABLED") or "").strip().lower() in (
    "1", "true", "on", "yes")
NAVER_CLIENT_ID = (os.environ.get("NAVER_CLIENT_ID") or "").strip()
NAVER_CLIENT_SECRET = (os.environ.get("NAVER_CLIENT_SECRET") or "").strip()

# D7-AA — 운영자 실행(Operator) 설정. 공개 정적 대시보드의 "데이터 새로고침"/"텔레그램 전송"
# 버튼이 GitHub 페이지로 이동하지 않고 서버측 Operator API(POST)를 호출하게 한다.
#
# OPERATOR_API_BASE는 정적 페이지에 주입되는 *공개* base URL이다(비밀값 아님). 빈 값이면
# 3개 버튼은 비활성 + "Operator API 미연결"을 표시하고 링크로 대체하지 않는다. (빌더는 이 값을
# --operator-api-base CLI로 받아 preview-model island에 주입한다 — 빌더 소스는 env를 직접 읽지 않음.)
OPERATOR_API_BASE = (os.environ.get("OPERATOR_API_BASE") or "").strip()
# 서버측 비밀값 — 환경변수에서만 읽고 어디에도 print/log/직렬화/응답에 싣지 않는다 (rules.md §4).
# 토큰·repo가 비어 있으면 operator_gateway는 fail-closed(not_configured)로 어떤 트리거도 하지 않는다.
OPERATOR_REPO = (os.environ.get("OPERATOR_REPO") or "Sinabroin/HDEC-News-Sensor").strip()
GH_OPERATOR_TOKEN = (os.environ.get("GH_OPERATOR_TOKEN")
                     or os.environ.get("GITHUB_TOKEN") or "").strip()

# D7-AG-3 — 운영자 보호를 브라우저 PIN에서 **서버 앞단(edge) 인증**으로 이관한다.
# 공개 페이지(GitHub Pages)에 PIN을 입력받는 설계는 약하고(어깨너머·피싱·단일 공유값·신원 없음)
# 사용자도 불필요하다고 판단했다. 대신 Operator API 호스트를 인증된 경계 뒤에 배포하고(우선순위
# A: Cloudflare Access / Vercel Protection / 사내 SSO / Basic Auth), 서버는 아래 정책으로 fail-closed
# 인가한다. public HTML에는 PIN 정답도 secret도 절대 넣지 않는다(브라우저는 base+경로로만 POST).
#
# OPERATOR_ACCESS_MODE:
#   "edge"          — 경계(SSO/Access)가 주입하는 신원 헤더(OPERATOR_ACCESS_HEADER)를 신뢰하고
#                     그 값이 OPERATOR_ALLOWED_USERS 허용목록에 있을 때만 인가(권장·PIN 없음).
#   "shared_secret" — 레거시/단순 배포용. X-Operator-Token == OPERATOR_SHARED_SECRET (브라우저 UI에는
#                     노출하지 않음 — 경계가 주입하거나 서버-대-서버 호출에만 사용).
#   ""(미설정)       — 인가 정책 없음 = fail-closed(어떤 트리거도 안 함).
OPERATOR_ACCESS_MODE = (os.environ.get("OPERATOR_ACCESS_MODE") or "").strip().lower()
# 경계가 인증된 운영자 신원을 실어 보내는 헤더 이름(비밀값 아님). 기본은 Cloudflare Access 표준.
OPERATOR_ACCESS_HEADER = (os.environ.get("OPERATOR_ACCESS_HEADER")
                          or "cf-access-authenticated-user-email").strip().lower()
# edge 모드에서 실행을 허용할 운영자 신원(이메일 등) 허용목록. 비어 있으면 edge 모드는 fail-closed.
OPERATOR_ALLOWED_USERS = [
    u.strip().lower() for u in (os.environ.get("OPERATOR_ALLOWED_USERS") or "").split(",")
    if u.strip()
]
# 레거시 shared_secret 모드 값. edge 모드에서는 사용하지 않는다(브라우저 PIN 아님).
OPERATOR_SHARED_SECRET = (os.environ.get("OPERATOR_SHARED_SECRET")
                          or os.environ.get("OPERATOR_PIN") or "").strip()
# 분당 트리거 상한(레이트리밋 · 프로세스 로컬). 남용/자동요청 완화용 방어선.
try:
    OPERATOR_RATE_LIMIT_PER_MIN = int(os.environ.get("OPERATOR_RATE_LIMIT_PER_MIN") or "12")
except ValueError:
    OPERATOR_RATE_LIMIT_PER_MIN = 12
# 로컬 개발/데모 전용 우회 — loopback(127.0.0.1/localhost) origin에 한해 경계 인증 없이 인가한다.
# 프로덕션에서는 절대 설정하지 않는다("1"일 때만·loopback 한정). 활성 빌드의 fetch 경로/버튼 활성
# 상태를 로컬에서 검증하기 위한 스위치다(실제 발송은 OPERATOR_DRY_RUN과 함께 차단할 수 있다).
OPERATOR_LOCAL_DEV = (os.environ.get("OPERATOR_LOCAL_DEV") or "").strip() in ("1", "true", "on", "yes")
# 로컬 검증용 — 설정 시 workflow_dispatch(네트워크)를 실제로 하지 않고 접수 응답만 반환한다.
OPERATOR_DRY_RUN = (os.environ.get("OPERATOR_DRY_RUN") or "").strip() in ("1", "true", "on", "yes")

# 공개 정적 페이지에서 Operator API로의 cross-origin 호출을 허용할 origin 목록.
# 기본은 공개 대시보드 origin 2곳(커스텀 도메인 guides.playground-aidesignlab.co.kr +
# 프로젝트 Pages sinabroin.github.io) + loopback. 둘 다 브라우저가 base+경로로 POST할 때 Origin
# 헤더로 실려 오므로(operator_api CORS·operator_gateway 둘 다 이 목록을 사용) 기본값에 포함해 env
# 누락에도 fail-open이 아니라 fail-closed를 유지한다. 추가 origin은 콤마구분 env로 확장(비밀값 아님).
OPERATOR_ALLOWED_ORIGINS = [
    o.strip() for o in (
        (os.environ.get("OPERATOR_ALLOWED_ORIGINS") or "")
        + ",https://guides.playground-aidesignlab.co.kr"
        + ",https://sinabroin.github.io"
        + ",http://127.0.0.1:8088,http://localhost:8088"
    ).split(",") if o.strip()
]
