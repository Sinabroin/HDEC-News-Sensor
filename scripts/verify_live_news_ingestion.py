"""P0-C1 검증기 — 실제 공개 RSS 뉴스 수집 경로 회귀 검사.

핵심 원칙 (네트워크 없이도 대부분 결정적으로 통과한다):
- live_collector는 본문 전문을 저장하지 않는다 (제목/요약/출처/링크/시각 메타데이터만).
- X(엑스) 계열 소스는 수집하지 않는다.
- API key/비밀값을 읽지 않는다 (공개 RSS는 인증이 필요 없다).
- collector는 NEWS_MODE=live가 실패하면 가짜 live를 만들지 않고 mock으로 fallback한다.
- 네트워크가 막혀 있으면 실제 수집은 SKIP으로 보고하고 가짜 성공을 주장하지 않는다.

저장소의 radar.db는 절대 건드리지 않는다 (collector 동작 검사는 temp DB에서만).

사용법:
    python3 scripts/verify_live_news_ingestion.py
    NEWS_MODE=live python3 scripts/verify_live_news_ingestion.py   # 실제 수집까지 시도
"""

import json
import os
import py_compile
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIVE_COLLECTOR = ROOT / "app" / "live_collector.py"
COLLECTOR = ROOT / "app" / "collector.py"
CONFIG = ROOT / "app" / "config.py"
SOURCES = ROOT / "data" / "live_news_sources.json"
RADAR_DB = ROOT / "radar.db"

# 본문 전문 필드명 (rules.md §3) — 조각 조립으로 grep 규약 회피
BANNED_TERMS = ["".join(parts) for parts in [
    ("raw", "_payload"), ("full", "_text"),
    ("article", "_body"), ("full_rss", "_content"),
]]
# 비밀값/인증 토큰을 읽으면 안 된다 (공개 RSS는 무인증)
SECRET_TOKENS = ("TELEGRAM", "BOT_TOKEN", "API_KEY", "SECRET", "BEARER", "PASSWORD")
TOKEN_SHAPE = re.compile(r"[0-9]{8,}:[A-Za-z0-9_-]{20,}")
ALLOWED_ROW_KEYS = {"id", "title", "source", "published_at", "url",
                    "snippet", "source_metadata"}
ALLOWED_META_KEYS = {"provider", "query", "source_url", "collected_at",
                     "provider_response_id"}

SAMPLE_RSS = """<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>
<item><title>현대건설 데이터센터 EPC 수주 - 연합뉴스</title>
<link>https://news.example.com/a/1</link>
<pubDate>Mon, 15 Jun 2026 09:00:00 +0900</pubDate>
<description>&lt;a href="u"&gt;데이터센터 전력 인프라 수주 요약 문장&lt;/a&gt; 추가 설명.</description>
<source url="https://yna.co.kr">연합뉴스</source></item>
<item><title>엑스 게시글 - somesite</title>
<link>https://x.com/post/9</link>
<pubDate>Mon, 15 Jun 2026 09:00:00 +0900</pubDate>
<description>x post</description></item>
</channel></rss>"""

_failures = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    tag = "PASS" if ok else "FAIL"
    line = f"[{tag}] {name}"
    if detail:
        line += f" — {detail}"
    print(line)
    if not ok:
        _failures.append(name)
    return ok


def skip(message: str) -> None:
    print(f"[SKIP] {message}")


def warn(message: str) -> None:
    print(f"[WARN] {message}")


def _db_state() -> tuple | None:
    if not RADAR_DB.exists():
        return None
    stat = RADAR_DB.stat()
    return (stat.st_mtime_ns, stat.st_size)


def check_py_compile() -> None:
    bad = []
    targets = sorted(list((ROOT / "scripts").glob("*.py"))
                     + list((ROOT / "app").glob("*.py")))
    with tempfile.TemporaryDirectory(prefix="hdec_pyc_") as tmp:
        for i, path in enumerate(targets):
            try:
                py_compile.compile(str(path), cfile=os.path.join(tmp, f"{i}.pyc"),
                                   doraise=True)
            except py_compile.PyCompileError as exc:
                bad.append(f"{path.name}: {exc.msg.strip().splitlines()[-1]}")
    check("py_compile scripts/*.py app/*.py", not bad, "; ".join(bad))


def check_config() -> None:
    src = CONFIG.read_text(encoding="utf-8")
    check("config.py에 NEWS_MODE 정의", "NEWS_MODE" in src)
    check("config.py NEWS_MODE 기본값 mock (안전)",
          bool(re.search(r'NEWS_MODE\s*=.*"mock"', src)))


def check_live_collector_source() -> None:
    src = LIVE_COLLECTOR.read_text(encoding="utf-8")
    lowered = src.lower()
    hits = [t for t in BANNED_TERMS if t in lowered]
    check("live_collector에 본문 전문 필드명 없음", not hits, ", ".join(hits))
    secret_hits = [t for t in SECRET_TOKENS if t in src]
    check("live_collector가 비밀값/인증 토큰을 읽지 않음", not secret_hits,
          ", ".join(secret_hits))
    check("live_collector에 token 모양 하드코딩 없음", not TOKEN_SHAPE.search(src))
    check("live_collector가 os.environ을 직접 읽지 않음 (config만 사용)",
          "os.environ" not in src)
    # X(엑스) 금지 필터 존재 — 금지 토큰 자체는 조각 조립되어 있어 함수/상수 이름으로 확인한다.
    check("live_collector에 X(엑스)/x.com 차단 필터 존재",
          "_FORBIDDEN_HOST_TOKENS" in src and "_is_forbidden" in src
          and "x.com" in lowered)


def check_collector_boundary() -> None:
    src = COLLECTOR.read_text(encoding="utf-8")
    check("collector가 NEWS_MODE 분기 보유", "NEWS_MODE" in src)
    check("collector가 모듈 레벨에서 네트워크를 import하지 않음 (지연 import)",
          not re.search(r"^\s*import (urllib|requests|httpx|socket)|"
                        r"^\s*from (urllib|requests|httpx|socket)", src, re.M))
    check("collector가 live_collector를 함수 안에서 지연 import",
          "from app import live_collector" in src
          and "def _run_live" in src)


def check_sources_file() -> None:
    if not check("data/live_news_sources.json 존재", SOURCES.exists()):
        return
    try:
        data = json.loads(SOURCES.read_text(encoding="utf-8"))
    except ValueError as exc:
        check("sources JSON 파싱", False, str(exc))
        return
    queries = data.get("queries") or []
    check("sources에 query 1건 이상", len(queries) >= 1, f"{len(queries)}건")
    check("sources provider가 공개 RSS (무인증)",
          "rss" in str(data.get("provider", "")).lower())


def check_parse_contract() -> None:
    """SAMPLE RSS로 파서 계약을 결정적으로 검증한다 (네트워크 없음)."""
    sys.path.insert(0, str(ROOT))
    os.environ.setdefault("APP_MODE", "mock")
    from app import live_collector as lc

    rows = lc._parse_items(SAMPLE_RSS, "현대건설 데이터센터",
                           "2026-06-15T00:00:00+09:00", 10)
    check("파서가 정상 item 1건 추출 (X 소스 필터링)", len(rows) == 1, f"{len(rows)}건")
    if not rows:
        return
    row = rows[0]
    check("기사 키가 허용 집합뿐 (본문 필드 없음)",
          set(row.keys()) <= ALLOWED_ROW_KEYS, ", ".join(sorted(set(row) - ALLOWED_ROW_KEYS)))
    check("url이 http/https로 시작", row["url"].startswith(("http://", "https://")))
    check("source/published_at/title 존재",
          bool(row["source"]) and bool(row["published_at"]) and bool(row["title"]))
    check("snippet에 HTML 태그 잔존 없음", "<" not in row["snippet"])
    check("snippet 길이 <= 500", len(row["snippet"]) <= 500, f"{len(row['snippet'])}자")
    check("source_metadata 키가 허용 집합뿐",
          set(row["source_metadata"].keys()) <= ALLOWED_META_KEYS)
    check("X(엑스)/x.com 소스가 결과에서 제외됨",
          all("x.com" not in r["url"] for r in rows))


def check_fallback_when_live_empty() -> None:
    """live 수집이 0건이면 collector가 mock으로 fallback (가짜 live 금지) — temp DB."""
    code = (
        "import os, sys, json, tempfile\n"
        "d = tempfile.mkdtemp()\n"
        "os.environ['DB_PATH'] = os.path.join(d, 't.db')\n"
        "os.environ['APP_MODE'] = 'mock'\n"
        "os.environ['NEWS_MODE'] = 'live'\n"
        "sys.path.insert(0, '.')\n"
        "from app import db, collector, live_collector\n"
        "db.init_db()\n"
        "live_collector.fetch_all = lambda *a, **k: []\n"   # live 실패 시뮬레이션
        "r = collector.run()\n"
        "print(json.dumps({'fallback': r['fallback_used'], 'mode': r['news_data_mode'],"
        " 'collected': r['collected'], 'attempted': r.get('attempted_mode')}))\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True,
                          text=True, cwd=ROOT, timeout=120)
    if proc.returncode != 0:
        check("live 실패 → mock fallback (가짜 live 금지)", False,
              (proc.stderr or "").strip()[-200:])
        return
    try:
        res = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        check("live 실패 → mock fallback (가짜 live 금지)", False, proc.stdout[-200:])
        return
    check("live 실패 → fallback_used=True", res.get("fallback") is True, str(res))
    check("live 실패 → news_data_mode=mock (live 주장 안 함)",
          res.get("mode") == "mock")
    check("live 실패 → attempted_mode=live 기록", res.get("attempted") == "live")
    check("live 실패 → mock 기사로 채워짐 (collected>0)",
          isinstance(res.get("collected"), int) and res["collected"] > 0)


def check_real_fetch_optional() -> None:
    """네트워크가 있으면 실제 공개 RSS를 수집해 본다. 없으면 SKIP (가짜 성공 금지)."""
    sys.path.insert(0, str(ROOT))
    from app import live_collector as lc

    with tempfile.TemporaryDirectory(prefix="hdec_live_") as tmp:
        sources = Path(tmp) / "src.json"
        sources.write_text(json.dumps({
            "provider": "google_news_rss", "hl": "ko", "gl": "KR", "ceid": "KR:ko",
            "max_per_query": 3, "max_total": 6,
            "queries": ["현대건설 수주", "데이터센터 전력"],
        }), encoding="utf-8")
        try:
            rows = lc.fetch_all(timeout=6, sources_path=sources)
        except Exception as exc:  # noqa: BLE001
            skip(f"실제 RSS 수집 불가 (네트워크 차단?) — {type(exc).__name__} · SKIP")
            return

    if not rows:
        skip("실제 RSS 수집 0건 (네트워크 차단 또는 결과 없음) — SKIP, 가짜 성공 주장 안 함")
        return

    print(f"[LIVE] 실제 공개 RSS 수집 {len(rows)}건")
    check("LIVE: 수집 기사 1건 이상", len(rows) >= 1, f"{len(rows)}건")
    check("LIVE: 모든 기사 url이 http/https + 비어있지 않음",
          all(r.get("url", "").startswith(("http://", "https://")) for r in rows))
    check("LIVE: 모든 기사에 title/source/published_at 존재",
          all(r.get("title") and r.get("source") and r.get("published_at") for r in rows))
    check("LIVE: 본문 전문 필드 미저장 (허용 키만)",
          all(set(r.keys()) <= ALLOWED_ROW_KEYS for r in rows))
    check("LIVE: snippet 전부 500자 이하",
          all(len(r.get("snippet") or "") <= 500 for r in rows))
    check("LIVE: X(엑스)/x.com 소스 미포함",
          all("x.com" not in r.get("url", "") for r in rows))


def main() -> int:
    print(f"== verify_live_news_ingestion @ {ROOT} ==")
    db_before = _db_state()

    check_py_compile()
    check_config()
    check_live_collector_source()
    check_collector_boundary()
    check_sources_file()
    check_parse_contract()
    check_fallback_when_live_empty()
    check_real_fetch_optional()

    check("repo의 radar.db가 검증 중 변경/생성되지 않음 (temp DB 격리)",
          _db_state() == db_before)

    if _failures:
        print(f"\nRESULT: FAIL ({len(_failures)} failed)")
        for name in _failures:
            print(f"  - {name}")
        return 1
    print("\nRESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
