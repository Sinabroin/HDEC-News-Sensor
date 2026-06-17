"""P0-D2 검증기 — 주요 출처 커버리지 & 임원 필터링 회귀 검사.

네트워크 호출 0건, 비밀값 접근 0건으로 실행된다 (run_audit는 temp DB 서브프로세스에서만
돌려 저장소 radar.db를 건드리지 않는다). 전부 통과하면 exit 0.

핵심 검사:
- 선택적 Naver provider: 기본 off, 자격증명 부재 시 정직 skip, 공식 엔드포인트만, 비밀값 미출력.
- 교차 dedup: Naver-only 보존, Google+Naver 중복은 하나로 합치고 provider 근거 보존(+원문 우선).
- coverage(수집)와 ranking(임원 관련성) 분리: major 출처라도 관련성이 없으면 top 승격 안 됨.
- 커버리지 감사: 로스터 로드, provider 상태, google/naver/both, 누락 출처를 정직하게 보고.
- workflow: 이미 존재하는 repo secrets를 안전하게 주입(값 미출력).
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
CONFIG = ROOT / "app" / "config.py"
COLLECTOR = ROOT / "app" / "collector.py"
NAVER_PROVIDER = ROOT / "app" / "naver_news_provider.py"
NAVER_SOURCES = ROOT / "data" / "naver_news_sources.json"
ROSTER = ROOT / "data" / "major_source_roster.json"
AUDIT = ROOT / "scripts" / "audit_major_source_coverage.py"
WORKFLOW = ROOT / ".github" / "workflows" / "telegram-notify.yml"
RADAR_DB = ROOT / "radar.db"

NAVER_OFFICIAL_ENDPOINT = "https://openapi.naver.com/v1/search/news.json"

BANNED_TERMS = ["".join(parts) for parts in [
    ("raw", "_payload"), ("full", "_text"),
    ("article", "_body"), ("full_rss", "_content"),
    ("api.", "x.com"), ("twit", "ter"), ("x bearer", " token"),
]]
TOKEN_SHAPE = re.compile(r"[0-9]{8,}:[A-Za-z0-9_-]{20,}")

_failures = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    line = f"[{'PASS' if ok else 'FAIL'}] {name}"
    if detail:
        line += f" — {detail}"
    print(line)
    if not ok:
        _failures.append(name)
    return ok


def warn(message: str) -> None:
    print(f"[WARN] {message}")


def _db_state() -> tuple | None:
    if not RADAR_DB.exists():
        return None
    stat = RADAR_DB.stat()
    return (stat.st_mtime_ns, stat.st_size)


def _import_audit():
    scripts_dir = str(ROOT / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    os.environ.setdefault("APP_MODE", "mock")
    import audit_major_source_coverage as audit
    return audit


# ---------- 정적 검사 ----------

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


def check_config_default_off() -> None:
    src = CONFIG.read_text(encoding="utf-8")
    check("config.py에 NAVER_NEWS_ENABLED 정의", "NAVER_NEWS_ENABLED" in src)
    check("config.py에 NAVER_CLIENT_ID/SECRET 정의",
          "NAVER_CLIENT_ID" in src and "NAVER_CLIENT_SECRET" in src)
    check("config.py에 token 모양 하드코딩 없음 (자격증명은 env에서만)",
          not TOKEN_SHAPE.search(src))
    # 환경변수 미설정 시 기본 off — 서브프로세스로 깨끗한 환경에서 확인 (네트워크 0건).
    code = ("import sys; sys.path.insert(0, '.'); from app import config; "
            "print('ENABLED', config.NAVER_NEWS_ENABLED); "
            "print('ID_EMPTY', config.NAVER_CLIENT_ID == ''); "
            "print('SECRET_EMPTY', config.NAVER_CLIENT_SECRET == '')")
    env = {k: v for k, v in os.environ.items()
           if k not in ("NAVER_NEWS_ENABLED", "NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET")}
    env["APP_MODE"] = "mock"
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True,
                          text=True, cwd=ROOT, timeout=60, env=env)
    out = proc.stdout
    check("NAVER_NEWS_ENABLED 기본값 off (env 미설정 → False)", "ENABLED False" in out, out.strip()[:120])
    check("자격증명 미설정 → 빈 문자열 (로컬 안전)",
          "ID_EMPTY True" in out and "SECRET_EMPTY True" in out)


def check_roster_file() -> None:
    if not check("data/major_source_roster.json 존재", ROSTER.exists()):
        return
    try:
        data = json.loads(ROSTER.read_text(encoding="utf-8"))
    except ValueError as exc:
        check("roster JSON 파싱", False, str(exc))
        return
    tiers = data.get("tiers")
    check("roster에 tiers dict 존재", isinstance(tiers, dict) and bool(tiers))
    names = [n for v in (tiers or {}).values() if isinstance(v, list) for n in v]
    check("roster에 출처명 10곳 이상", len(names) >= 10, f"{len(names)}곳")
    for must in ("연합뉴스", "매일경제", "국토교통부"):
        check(f"roster에 대표 출처 '{must}' 포함", must in names)


def check_naver_sources_file() -> None:
    if not check("data/naver_news_sources.json 존재", NAVER_SOURCES.exists()):
        return
    raw = NAVER_SOURCES.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except ValueError as exc:
        check("naver_sources JSON 파싱", False, str(exc))
        return
    queries = data.get("queries") or []
    check("naver_sources에 쿼리 10건 이상 (현대건설 집중)", len(queries) >= 10, f"{len(queries)}건")
    low = [q for q in queries if isinstance(q, str)]
    check("naver_sources 쿼리가 현대건설 직접 커버리지 중심",
          sum(1 for q in low if "현대건설" in q) >= 10,
          f"{sum(1 for q in low if '현대건설' in q)}건")
    check("naver_sources에 공식 엔드포인트 명시", data.get("endpoint") == NAVER_OFFICIAL_ENDPOINT)
    check("naver_sources에 host_source_map 존재 (로스터 매칭용)",
          isinstance(data.get("host_source_map"), dict) and bool(data.get("host_source_map")))
    low_raw = raw.lower()
    check("naver_sources에 api key/secret/token 류 표기 없음 (비밀값은 env)",
          not any(t in low_raw for t in ("api_key", "apikey", "client_secret",
                                         "bearer", "x-naver-client")))
    check("naver_sources에 token 모양 하드코딩 없음", not TOKEN_SHAPE.search(raw))


# ---------- 순수 함수 (audit 모듈) ----------

def check_audit_pure_functions() -> None:
    audit = _import_audit()
    roster = audit.load_roster()
    check("audit.load_roster가 [{source,tier}] 평탄화", bool(roster)
          and all("source" in r and "tier" in r for r in roster))
    check("provider_label 토큰 해석",
          audit.provider_label("google_news_rss") == "google"
          and audit.provider_label("naver_news_api") == "naver"
          and audit.provider_label("google_news_rss+naver_news_api") == "both")
    m = audit.match_roster_source("연합뉴스TV", roster)
    check("match_roster_source 부분일치 (연합뉴스TV→연합뉴스)",
          m is not None and m["source"] == "연합뉴스", str(m))
    check("match_roster_source 무관 출처 → None",
          audit.match_roster_source("어떤블로그", roster) is None)
    g = [{"title": "현대건설 수주 A", "url": "https://news.google.com/x1"},
         {"title": "공통 사건 제목", "url": "https://news.google.com/x2"}]
    n = [{"title": "공통 사건 제목", "url": "https://www.yna.co.kr/v/2"},
         {"title": "Naver 단독 제목", "url": "https://www.mk.co.kr/v/3"}]
    sets = audit.coverage_sets(g, n)
    check("coverage_sets both=1 / naver_only=1 / google_only=1",
          len(sets["both"]) == 1 and len(sets["naver_only"]) == 1
          and len(sets["google_only"]) == 1, str({k: len(v) for k, v in sets.items()
                                                   if isinstance(v, set)}))


# ---------- 교차 dedup (criteria 5,6) ----------

def _raw(provider, title, url, source="출처 미상"):
    return {"id": f"{provider[:4]}_{abs(hash(url)) % 10**8}", "title": title,
            "source": source, "published_at": "2026-06-17T09:00:00+09:00",
            "url": url, "snippet": "s",
            "source_metadata": {"provider": provider, "query": "q", "source_url": url,
                                "collected_at": "t", "provider_response_id": "r"}}


def check_cross_provider_dedup() -> None:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    os.environ.setdefault("APP_MODE", "mock")
    from app import collector

    google = [_raw("google_news_rss", "현대건설 사우디 원전 EPC 수주",
                   "https://news.google.com/rss/articles/REDIRECT1")]
    naver = [
        _raw("naver_news_api", "현대건설, 사우디 원전 EPC 수주",
             "https://www.yna.co.kr/view/AKR1", "연합뉴스"),
        _raw("naver_news_api", "현대건설 신규 도시정비 단독 수주",
             "https://www.mk.co.kr/news/only", "매일경제"),
    ]
    merged = collector.merge_provider_articles(google + naver)
    check("교차 dedup: 중복 1쌍 → 기사 2건 (both 병합 + naver-only 보존)",
          len(merged) == 2, f"{len(merged)}건")
    dup = next((x for x in merged if "원전" in x["title"]), None)
    only = next((x for x in merged if "도시정비" in x["title"]), None)
    check("교차 dedup: 중복 기사 provider 결합 토큰 (provider 근거 보존)",
          dup is not None
          and dup["source_metadata"]["provider"] == "google_news_rss+naver_news_api",
          dup and dup["source_metadata"]["provider"])
    check("교차 dedup: 원문(originallink) URL 우선 (news.google 리다이렉트 대체)",
          dup is not None and dup["url"] == "https://www.yna.co.kr/view/AKR1",
          dup and dup["url"])
    check("교차 dedup: Naver-only 기사 보존 (provider=naver 단독)",
          only is not None
          and only["source_metadata"]["provider"] == "naver_news_api")


# ---------- 등급 분류 + 커버리지 빌더 (criteria 7,8) ----------

def check_executive_filter_buckets() -> None:
    audit = _import_audit()
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from app import scoring

    roster = audit.load_roster()
    top_ids = {"x_top"}
    scored = [
        {"id": "x_top", "title": "현대건설 SMR 원전 수주", "source": "연합뉴스",
         "url": "https://www.yna.co.kr/v/top", "published_at": "2026-06-17T09:00:00+09:00",
         "alert_grade": scoring.GRADE_INSTANT, "final_score": 4.7,
         "scoring_reason": "현대건설 직접·수주", "source_metadata_json": '{"provider":"naver_news_api"}'},
        {"id": "x_obs", "title": "현대건설 해외 수주 확대", "source": "매일경제",
         "url": "https://www.mk.co.kr/v/1", "published_at": "2026-06-16T09:00:00+09:00",
         "alert_grade": scoring.GRADE_DAILY, "final_score": 3.6,
         "scoring_reason": "현대건설 직접·해외", "source_metadata_json": '{"provider":"google_news_rss"}'},
        {"id": "x_exc", "title": "현대건설 테마주 급등 기대", "source": "한국경제",
         "url": "https://www.hankyung.com/v/2", "published_at": "2026-06-15T09:00:00+09:00",
         "alert_grade": scoring.GRADE_EXCLUDED, "final_score": 1.0,
         "scoring_reason": "단순 테마주", "source_metadata_json": '{"provider":"google_news_rss"}'},
    ]
    t_top = audit.grade_bucket(scoring.GRADE_INSTANT, top_ids, "x_top")
    t_obs = audit.grade_bucket(scoring.GRADE_DAILY, top_ids, "x_obs")
    t_exc = audit.grade_bucket(scoring.GRADE_EXCLUDED, top_ids, "x_exc")
    check("등급: 즉시+top → (top, observed)", t_top[0] is True and t_top[1] is True)
    check("등급: 중요(비-top) → (not top, observed)", t_obs[0] is False and t_obs[1] is True)
    check("등급: 제외 → (not top, not observed, excluded)",
          t_exc[1] is False and t_exc[2] is True)

    rows = audit._build_coverage_rows(scored, roster, top_ids, None)
    by_src = {r["source"]: r for r in rows}
    check("커버리지: 매일경제 major+Hyundai → observed≥1, top=0 (major≠자동승격)",
          by_src.get("매일경제", {}).get("observed_count", 0) >= 1
          and by_src.get("매일경제", {}).get("top_count", 0) == 0)
    check("커버리지: 한국경제 테마주 → excluded≥1 (major+저관련 제외)",
          by_src.get("한국경제", {}).get("excluded_count", 0) >= 1)

    filtered = audit._build_filtered_down(scored, roster, top_ids, None)
    fids = {f["source"] for f in filtered}
    check("filtered_down: major 비-top 기사 포함 (매일경제·한국경제), top(연합뉴스) 제외",
          "매일경제" in fids and "한국경제" in fids and "연합뉴스" not in fids,
          str(sorted(fids)))


# ---------- run_audit (temp DB 서브프로세스, 네트워크 없음) ----------

_AUDIT_RUN_PAYLOAD = r'''
import os, sys, json
os.environ.pop("DB_PATH", None)
os.environ["APP_MODE"] = "mock"
os.environ["NEWS_MODE"] = "mock"
for k in ("NAVER_NEWS_ENABLED", "NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET"):
    os.environ.pop(k, None)
ROOT = %r
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import audit_major_source_coverage as A
MODE = %r

def _raw(provider, title, url, source):
    return {"id": provider[:4] + "_" + str(abs(hash(url)) %% 10**8), "title": title,
            "source": source, "published_at": "2026-06-17T09:00:00+09:00",
            "url": url, "snippet": "요약", "source_metadata": {"provider": provider,
            "query": "q", "source_url": url, "collected_at": "t", "provider_response_id": "r"}}

if MODE == "empty":
    A.collect_providers = lambda m: {"google_rows": [], "google_status": "skipped",
                                     "naver_rows": [], "naver_status": "disabled"}
else:
    google = [_raw("google_news_rss", "현대건설 사우디 원전 EPC 수주",
                   "https://news.google.com/rss/x1", "출처 미상"),
              _raw("google_news_rss", "삼성물산 데이터센터 EPC 수주",
                   "https://news.google.com/rss/x2", "출처 미상")]
    naver = [_raw("naver_news_api", "현대건설, 사우디 원전 EPC 수주",
                  "https://www.yna.co.kr/view/AKR1", "연합뉴스"),
             _raw("naver_news_api", "현대건설 신규 도시정비 단독 수주 소식",
                  "https://www.mk.co.kr/news/only", "매일경제")]
    A.collect_providers = lambda m: {"google_rows": google, "google_status": "active",
                                     "naver_rows": naver, "naver_status": "active"}

audit = A.run_audit()
md = A.render_markdown(audit)
print("RESULT_JSON:" + json.dumps({
    "summary": audit["summary"],
    "provider_status": audit["provider_status"],
    "collection_note": audit.get("collection_note"),
    "naver_only": len(audit["naver_only_findings"]),
    "rows": len(audit["coverage_rows"]),
    "md_has_honest": ("관련 기사가 없음" in md or "발견되지 않" in md),
    "md_no_secret": all(s not in md for s in ("X-Naver", "Client-Secret")),
    "md_len": len(md),
}, ensure_ascii=False))
'''


def _run_audit_subprocess(mode: str) -> dict | None:
    payload = _AUDIT_RUN_PAYLOAD % (str(ROOT), mode)
    env = {k: v for k, v in os.environ.items()
           if k not in ("NAVER_NEWS_ENABLED", "NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET",
                        "DB_PATH")}
    env["APP_MODE"] = "mock"
    try:
        proc = subprocess.run([sys.executable, "-c", payload], capture_output=True,
                              text=True, cwd=ROOT, timeout=240, env=env)
    except subprocess.TimeoutExpired:
        check(f"run_audit({mode}) 서브프로세스 실행", False, "timeout")
        return None
    line = next((ln for ln in proc.stdout.splitlines()
                 if ln.startswith("RESULT_JSON:")), None)
    if line is None:
        check(f"run_audit({mode}) 서브프로세스 실행",
              False, (proc.stderr or proc.stdout)[-300:])
        return None
    return json.loads(line[len("RESULT_JSON:"):])


def check_audit_offline_safe() -> None:
    out = _run_audit_subprocess("empty")
    if out is None:
        return
    check("run_audit(offline): provider 정직 보고 (google skipped, naver disabled)",
          out["provider_status"]["google_news_rss"] == "skipped"
          and out["provider_status"]["naver_news_api"] == "disabled")
    check("run_audit(offline): 수집 0건 (가짜 수치 없음)",
          out["summary"]["collected_total"] == 0
          and out["summary"]["deduped_total"] == 0)
    check("run_audit(offline): '기사 없음 보장 아님' 정직 고지 + 렌더 동작",
          out["collection_note"] and "관련 기사가 없음" in (out["collection_note"])
          and out["md_len"] > 0)


def check_audit_with_fixtures() -> None:
    out = _run_audit_subprocess("fixtures")
    if out is None:
        return
    s = out["summary"]
    check("run_audit(fixtures): both≥1 (Google+Naver 중복 합산)", s["both"] >= 1, str(s))
    check("run_audit(fixtures): naver_only≥1 (Naver 단독 보존)", s["naver_only"] >= 1, str(s))
    check("run_audit(fixtures): google_only≥1", s["google_only"] >= 1, str(s))
    check("run_audit(fixtures): deduped_total < collected_total (교차 dedup 발생)",
          s["deduped_total"] < s["collected_total"], str(s))
    check("run_audit(fixtures): Naver-only 발견 목록 노출", out["naver_only"] >= 1)
    check("run_audit(fixtures): markdown에 비밀값 노출 없음", out["md_no_secret"])


# ---------- workflow secret 안전 주입 (criteria 11,12) ----------

def check_workflow_naver_wiring() -> None:
    if not check("telegram-notify.yml 존재", WORKFLOW.exists()):
        return
    text = WORKFLOW.read_text(encoding="utf-8")
    check("workflow가 secrets.NAVER_CLIENT_ID 참조 (하드코딩 아님)",
          "${{ secrets.NAVER_CLIENT_ID }}" in text)
    check("workflow가 secrets.NAVER_CLIENT_SECRET 참조 (하드코딩 아님)",
          "${{ secrets.NAVER_CLIENT_SECRET }}" in text)
    check("workflow가 NAVER_NEWS_ENABLED를 live 경로에서 활성화", "NAVER_NEWS_ENABLED" in text)

    # NAVER_* env가 secrets로만 주입되는지 (값 하드코딩 금지) — CLIENT_ID/SECRET 한정.
    unsafe = [line.strip() for line in text.splitlines()
              if re.search(r"NAVER_CLIENT_(ID|SECRET)\s*:", line)
              and "secrets." not in line]
    check("NAVER_CLIENT_* env가 secrets로만 주입됨", not unsafe, "; ".join(unsafe))

    # 비밀값을 echo/print하는 디버그 step이 없어야 한다.
    echo_leak = [line.strip() for line in text.splitlines()
                 if ("echo" in line.lower() or "printf" in line.lower())
                 and ("NAVER_CLIENT_ID" in line or "NAVER_CLIENT_SECRET" in line)]
    check("workflow가 NAVER 자격증명을 echo/print하지 않음", not echo_leak,
          "; ".join(echo_leak))
    check("workflow에 token 모양 하드코딩 없음", not TOKEN_SHAPE.search(text))

    try:
        import yaml
        try:
            yaml.safe_load(text)
            check("workflow YAML 파싱", True)
        except yaml.YAMLError as exc:
            check("workflow YAML 파싱", False, str(exc).splitlines()[0])
    except ImportError:
        warn("PyYAML 없음 — YAML 파싱 검사 생략 (구조 검사로 대체)")
        check("workflow 구조 검사 (jobs/steps 존재)",
              "jobs:" in text and "steps:" in text)


def main() -> int:
    print(f"== verify_major_source_coverage @ {ROOT} ==")
    db_before = _db_state()

    check_py_compile()
    check_config_default_off()
    check_roster_file()
    check_naver_sources_file()
    check_audit_pure_functions()
    check_cross_provider_dedup()
    check_executive_filter_buckets()
    check_audit_offline_safe()
    check_audit_with_fixtures()
    check_workflow_naver_wiring()

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
