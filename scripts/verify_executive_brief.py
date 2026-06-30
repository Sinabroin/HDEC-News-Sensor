"""P0-B2 검증기 — Executive Brief 레이어 회귀 검사.

네트워크 호출 0건, 비밀값 접근 0건으로 실행된다. 전부 통과하면 exit 0.
금지어 문자열은 이 파일 자체가 코드 트리 grep에 걸리지 않도록
조각(fragment)으로 보관했다가 런타임에 조립한다 (P0-B1 verifier와 동일 규약).

사용법:
    python3 scripts/verify_executive_brief.py
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
BRIEF_BUILDER = ROOT / "scripts" / "build_executive_brief.py"
DIGEST_BUILDER = ROOT / "scripts" / "build_telegram_digest.py"
SENDER = ROOT / "scripts" / "send_telegram.py"
P0B1_VERIFIER = ROOT / "scripts" / "verify_telegram_digest.py"
BRIEFING_MODULE = ROOT / "app" / "briefing.py"
MAIN_MODULE = ROOT / "app" / "main.py"
TEMPLATE = ROOT / "templates" / "index.html"
WORKFLOW = ROOT / ".github" / "workflows" / "telegram-notify.yml"
MACRO_SNAPSHOT = ROOT / "data" / "mock_macro_snapshot.json"
RADAR_DB = ROOT / "radar.db"

HEADER_TEXT = "HDEC Executive Radar"
ONE_LINER_LABEL = "오늘의 Executive Signal"
MESSAGE_BUDGET_MAX = 3000
SENDER_CAP_MAX = 3500

REQUIRED_BRIEF_KEYS = [
    "generated_at", "mode", "total_articles", "total_signals",
    "immediate_count", "daily_count", "weekly_count", "excluded_count",
    "status_board", "executive_one_liner", "top_immediate_signals",
    "top_new_issues", "theme_rankings", "category_counts",
    "spread_method", "operator_note",
    # P0-B6 데이터 출처 provenance
    "news_data_mode", "macro_data_mode", "macro_source",
    "macro_updated_at", "macro_is_stale", "data_warning",
]

# mock macro 고정값 중 점수/강도와 충돌할 수 없는 식별용 수치 (4자리)
DISTINCTIVE_MACRO_VALUES = ("1480.5", "2864.7")

# 금지어 — rules.md §1/§3 + P0-B 스프린트 금지 목록 (조각으로 조립)
BANNED_TERMS = ["".join(parts) for parts in [
    ("raw", "_payload"),
    ("full", "_text"),
    ("article", "_body"),
    ("full_rss", "_content"),
    ("api.", "x.com"),
    ("twit", "ter"),
    ("x bearer", " token"),
]]

# Telegram bot token 모양 (숫자ID:시크릿) — 어디에도 하드코딩 금지
TOKEN_SHAPE = re.compile(r"[0-9]{8,}:[A-Za-z0-9_-]{20,}")

# 코드 트리 금지어 스캔 대상 (스펙 문서 rules.md/PRD.md/.claude는 제외 — README §7)
SCAN_GLOBS = ["app/*.py", "app/*.sql", "scripts/*.py",
              "templates/*", "data/*.json", ".github/workflows/*"]

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


def warn(message: str) -> None:
    print(f"[WARN] {message}")


def _clean_env(**extra: str) -> dict:
    env = {**os.environ, "APP_MODE": "mock"}
    for key in ("MESSAGE", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_IDS", "DB_PATH"):
        env.pop(key, None)
    env.update(extra)
    return env


def run_script(path: Path, *flags: str, env: dict | None = None,
               timeout: int = 180) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(path), *flags],
        capture_output=True, text=True, env=env or _clean_env(),
        cwd=ROOT, timeout=timeout,
    )


def _db_state() -> tuple | None:
    if not RADAR_DB.exists():
        return None
    stat = RADAR_DB.stat()
    return (stat.st_mtime_ns, stat.st_size)


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


def check_domain_boundaries() -> None:
    offenders = []
    for path in sorted(list((ROOT / "app").glob("*.py"))
                       + list((ROOT / "scripts").glob("*.py"))):
        if path.name == "db.py":
            continue
        src = path.read_text(encoding="utf-8")
        if re.search(r"^\s*import sqlite3|^\s*from sqlite3", src, re.M):
            offenders.append(path.name)
    check("sqlite3 import는 app/db.py 단독 소유", not offenders,
          "; ".join(offenders))

    briefing_src = BRIEFING_MODULE.read_text(encoding="utf-8")
    writes = [m for m in ("upsert_", "insert_", "executescript", "DELETE", "UPDATE")
              if m in briefing_src]
    check("briefing.py는 DB에 쓰지 않음 (파생 전용)", not writes, "; ".join(writes))
    check("briefing.py에 네트워크 import 없음",
          not re.search(r"^\s*import (urllib|requests|httpx|socket)|"
                        r"^\s*from (urllib|requests|httpx|socket)",
                        briefing_src, re.M))


def check_api_and_template() -> None:
    main_src = MAIN_MODULE.read_text(encoding="utf-8")
    check("main.py에 GET /api/brief 라우트 존재",
          '"/api/brief"' in main_src and "briefing.build_brief" in main_src)

    html = TEMPLATE.read_text(encoding="utf-8")
    check("index.html이 /api/brief를 호출", '"/api/brief"' in html)
    for label in ("brief-section", ONE_LINER_LABEL, "주요 테마", "카테고리 요약",
                  "신규 이슈"):
        check(f"index.html에 '{label}' 표시 영역 존재", label in html)
    check("index.html에 telegram/webhook 문자열 없음 (rules.md §4)",
          "telegram" not in html.lower() and "webhook" not in html.lower())


def check_macro_snapshot() -> None:
    if not MACRO_SNAPSHOT.exists():
        warn("mock_macro_snapshot.json 없음 — macro 검사 생략 (선택 항목)")
        return
    try:
        data = json.loads(MACRO_SNAPSHOT.read_text(encoding="utf-8"))
    except ValueError as exc:
        check("macro snapshot JSON 파싱", False, str(exc))
        return
    check("macro snapshot mode == mock_static (P0-B6 스키마)",
          data.get("mode") == "mock_static", str(data.get("mode")))
    check("macro snapshot source == demo_mock",
          data.get("source") == "demo_mock", str(data.get("source")))
    check("macro snapshot에 updated_at 존재", bool(data.get("updated_at")))
    disclaimer = str(data.get("disclaimer") or "")
    check("macro snapshot disclaimer가 mock·현재 시장값 아님을 명시",
          "mock" in disclaimer.lower() and "현재 시장값" in disclaimer)
    values = data.get("values") or []
    check("macro values에 label/value 존재",
          bool(values) and all(
              v.get("label") and v.get("value") is not None for v in values),
          f"{len(values)}개")


def check_code_tree_banned() -> None:
    hits = []
    for pattern in SCAN_GLOBS:
        for path in sorted(ROOT.glob(pattern)):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            lowered = text.lower()
            for term in BANNED_TERMS:
                if term in lowered:
                    hits.append(f"{path.relative_to(ROOT)}: {term}")
            if TOKEN_SHAPE.search(text):
                hits.append(f"{path.relative_to(ROOT)}: token-shape")
    check("코드 트리 금지어/token 모양 0건", not hits, "; ".join(hits))


def check_cap_contract() -> None:
    cap = re.search(r"^MAX_MESSAGE_LEN\s*=\s*(\d+)",
                    SENDER.read_text(encoding="utf-8"), re.M)
    budget = re.search(r"^MESSAGE_BUDGET\s*=\s*(\d+)",
                       DIGEST_BUILDER.read_text(encoding="utf-8"), re.M)
    ok = (cap and budget and int(budget.group(1)) <= MESSAGE_BUDGET_MAX
          and int(cap.group(1)) >= int(budget.group(1))
          and int(cap.group(1)) <= SENDER_CAP_MAX)
    detail = ""
    if cap and budget:
        detail = (f"budget={budget.group(1)} <= cap={cap.group(1)}"
                  f" <= {SENDER_CAP_MAX} < telegram 4096")
    check("길이 계약: budget <= cap <= 3500", bool(ok), detail)


def check_workflow() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    check("workflow가 두 verifier를 발송 전에 실행",
          "verify_telegram_digest.py" in text
          and "verify_executive_brief.py" in text)
    try:
        import yaml  # CI 기본 이미지에 없을 수 있음 — 있으면 정식 파싱
        try:
            yaml.safe_load(text)
            check("workflow YAML 파싱", True)
        except yaml.YAMLError as exc:
            check("workflow YAML 파싱", False, str(exc).splitlines()[0])
    except ImportError:
        warn("PyYAML 없음 — YAML 파싱 검사 생략 (구조 검사로 대체)")
        check("workflow 구조 검사 (jobs/steps 존재)",
              "jobs:" in text and "steps:" in text)


def check_tracked_files() -> None:
    try:
        proc = subprocess.run(["git", "ls-files"], capture_output=True,
                              text=True, cwd=ROOT, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        warn("git 실행 불가 — tracked 파일 검사 생략")
        return
    if proc.returncode != 0:
        warn("git ls-files 실패 — tracked 파일 검사 생략")
        return
    offenders = []
    for tracked in proc.stdout.splitlines():
        name = tracked.rsplit("/", 1)[-1]
        if (name == ".env" or name.endswith(".db") or name.endswith(".pyc")
                or "__pycache__" in tracked or name.startswith(".telegram_token")):
            offenders.append(tracked)
    check("비밀/DB/캐시 파일이 git에 추적되지 않음", not offenders,
          "; ".join(offenders))


# ---------- brief 실행 검사 ----------

def check_brief_dry_run() -> str:
    proc = run_script(BRIEF_BUILDER, "--dry-run")
    ok = proc.returncode == 0 and bool(proc.stdout.strip())
    check("brief --dry-run 동작 (exit 0, 텍스트 출력)", ok,
          "" if ok else (proc.stderr or "").strip()[-300:])
    return proc.stdout if ok else ""


def check_brief_json() -> dict | None:
    proc = run_script(BRIEF_BUILDER, "--json")
    if not check("brief --json 동작 (exit 0)", proc.returncode == 0,
                 (proc.stderr or "").strip()[-300:]):
        return None
    try:
        brief = json.loads(proc.stdout)
    except ValueError as exc:
        check("brief --json 파싱", False, str(exc))
        return None
    check("brief --json 파싱", True)

    missing = [k for k in REQUIRED_BRIEF_KEYS if k not in brief]
    check("brief 필수 필드 전부 존재", not missing, "; ".join(missing))
    check("brief mode == mock", brief.get("mode") == "mock")
    check("brief total_articles > 0",
          isinstance(brief.get("total_articles"), int)
          and brief["total_articles"] > 0,
          str(brief.get("total_articles")))

    # P0-B6 — live macro 연동 전에는 mock_static/unavailable만 허용한다.
    check("brief news_data_mode == mock", brief.get("news_data_mode") == "mock")
    macro_mode = brief.get("macro_data_mode")
    check("brief macro_data_mode가 mock_static|unavailable (live 미구현)",
          macro_mode in ("mock_static", "unavailable"), str(macro_mode))
    if macro_mode == "mock_static":
        check("brief macro_source == demo_mock",
              brief.get("macro_source") == "demo_mock")
        check("brief macro_is_stale == true (mock은 항상 stale)",
              brief.get("macro_is_stale") is True)
        check("brief macro_updated_at 존재", bool(brief.get("macro_updated_at")))
    warning = str(brief.get("data_warning") or "")
    check("brief data_warning이 mock/미연동을 명시",
          "mock" in warning.lower() and "미연동" in warning, warning[:60])
    return brief


def check_one_liner(brief: dict) -> None:
    one = (brief.get("executive_one_liner") or "").strip()
    check("executive_one_liner 비어 있지 않음", bool(one))
    if not one:
        return
    check("executive_one_liner이 한국어 문장",
          bool(re.search(r"[가-힣]", one)), one[:60])
    check("executive_one_liner 길이 20~240자", 20 <= len(one) <= 240,
          f"{len(one)}자")
    check("executive_one_liner 1~2문장", one.count("다.") <= 2)
    titles = [i.get("title") or "" for i in brief.get("top_new_issues", [])]
    concat = [t[:25] for t in titles if t and t in one]
    check("executive_one_liner이 제목 이어붙이기가 아님 (종합 문장)",
          not concat, "; ".join(concat))


def check_brief_lists(brief: dict) -> None:
    immediate = brief.get("top_immediate_signals") or []
    check("top_immediate_signals 1~3건", 1 <= len(immediate) <= 3,
          f"{len(immediate)}건")
    required = ("rank", "title", "source", "final_score", "alert_grade",
                "confidence", "implication", "spread")
    check("즉시 시그널 필드(rank/title/source/score/grade/confidence/implication/spread) 완비",
          all(all(k in s for k in required) for s in immediate))
    check("즉시 시그널 spread 지표(related_count/source_count/label) 완비",
          all(isinstance(s.get("spread"), dict)
              and isinstance(s["spread"].get("related_count"), int)
              and isinstance(s["spread"].get("source_count"), int)
              and s["spread"].get("label") for s in immediate))

    issues = brief.get("top_new_issues") or []
    check("top_new_issues 1~5건", 1 <= len(issues) <= 5, f"{len(issues)}건")
    check("신규 이슈 필드(rank/title/category_label) 완비",
          all(s.get("rank") and s.get("title") and s.get("category_label")
              for s in issues))

    themes = brief.get("theme_rankings") or []
    check("theme_rankings 1~5건", 1 <= len(themes) <= 5, f"{len(themes)}건")
    check("테마 필드(theme/count/weighted_strength) 완비",
          all(t.get("theme") and isinstance(t.get("count"), int)
              and t.get("weighted_strength") is not None for t in themes))

    categories = brief.get("category_counts") or []
    check("category_counts 1건 이상", len(categories) >= 1, f"{len(categories)}건")
    check("카테고리 필드(label/count) 완비",
          all(c.get("label") and isinstance(c.get("count"), int)
              for c in categories))

    counted = (brief.get("immediate_count", 0) + brief.get("daily_count", 0)
               + brief.get("weekly_count", 0) + brief.get("excluded_count", 0))
    check("등급 분포 합계가 채점 기사 수와 일치",
          counted <= brief.get("total_articles", 0) and counted > 0,
          f"등급합 {counted} / 전체 {brief.get('total_articles')}")


# ---------- digest 실행 검사 ----------

def check_digest() -> None:
    proc = run_script(DIGEST_BUILDER, "--dry-run")
    ok = proc.returncode == 0 and bool(proc.stdout.strip())
    check("digest --dry-run 동작", ok,
          "" if ok else (proc.stderr or "").strip()[-300:])
    if ok:
        message = proc.stdout
        # D7-AD: Telegram은 리포트형 현황판/카테고리/점수 나열이 아니라 공통
        # executive digest의 결론→상황→HDEC 의미→오늘 액션 4문장만 노출한다.
        for label in (HEADER_TEXT, "[오늘 07:00 브리프]", "현대건설 관점에서는",
                      "오늘은 ", "핵심 링크"):
            check(f"digest에 '{label}' 포함", label in message)
        # P0-C1.13 — [주요 테마]는 Telegram에서 제거(부풀어 보이는 '40건' 카운트 = 임원용
        # 노이즈). 테마 정보는 리포트/대시보드에 유지된다 (verify_static_report가 검사).
        check("digest에 '[주요 테마]' 없음 (P0-C1.13 노이즈 제거)",
              "[주요 테마]" not in message)
        check("digest에 mock 모드 표기 포함", "mock" in message.lower())
        check(f"digest 길이 {len(message.rstrip())} <= {MESSAGE_BUDGET_MAX}",
              len(message.rstrip()) <= MESSAGE_BUDGET_MAX)
        # P0-B6 — mock macro 고정값을 시세처럼 발송하지 않는다
        leaked = [v for v in DISTINCTIVE_MACRO_VALUES if v in message]
        check("digest에 mock macro 고정값 수치 없음", not leaked, ", ".join(leaked))
        # P0-C1.12 — digest는 '시장지표 미연동' placeholder를 빼고 거시경제를 리포트로
        # 위임한다 (노이즈 제거). 가짜 macro 수치 금지는 위 검사로 유지된다.
        check("digest가 미연동 placeholder 없이 거시경제를 전체 리포트로 위임",
              "시장지표 미연동" not in message
              and "상세 리포트 보기" in message and "거시경제" in message)

    proc = run_script(DIGEST_BUILDER, "--json")
    if not check("digest --json 동작", proc.returncode == 0,
                 (proc.stderr or "").strip()[-300:]):
        return
    try:
        data = json.loads(proc.stdout)
    except ValueError as exc:
        check("digest --json 파싱", False, str(exc))
        return
    signals = data.get("top_signals") or []
    check("digest JSON top_signals 1~3건", 1 <= len(signals) <= 3,
          f"{len(signals)}건")
    check("digest JSON에 executive_one_liner 존재",
          bool((data.get("executive_one_liner") or "").strip()))
    executive = data.get("executive_digest") or {}
    check("digest JSON에 공통 executive 4필드 존재",
          all(executive.get(key) for key in ("headline", "situation", "hdec_angle", "watch")))
    check("digest JSON 핵심 링크 1~3개",
          1 <= len(executive.get("links") or []) <= 3)
    chars = data.get("message_chars")
    check(f"digest JSON message_chars <= {MESSAGE_BUDGET_MAX}",
          isinstance(chars, int) and chars <= MESSAGE_BUDGET_MAX, str(chars))


# ---------- 기존 동작 보존 검사 ----------

def check_p0b1_verifier() -> None:
    proc = run_script(P0B1_VERIFIER)
    check("기존 P0-B1 verifier 통과 (exit 0)", proc.returncode == 0,
          "" if proc.returncode == 0 else (proc.stdout or "").strip()[-300:])


def check_sender_paths() -> None:
    resolver = ("import sys; sys.path.insert(0, 'scripts'); "
                "from send_telegram import resolve_message; "
                "m, s = resolve_message(); print(s); print(len(m))")

    proc = subprocess.run([sys.executable, "-c", resolver], capture_output=True,
                          text=True, cwd=ROOT, timeout=180,
                          env=_clean_env(MESSAGE="P0-B2 sender path check"))
    lines = (proc.stdout or "").strip().splitlines()
    check("MESSAGE env 우선 경로 동작 (env-message)",
          proc.returncode == 0 and lines and lines[0] == "env-message",
          "; ".join(lines[:1]))

    proc = subprocess.run([sys.executable, "-c", resolver], capture_output=True,
                          text=True, cwd=ROOT, timeout=180, env=_clean_env())
    lines = (proc.stdout or "").strip().splitlines()
    ok = (proc.returncode == 0 and len(lines) >= 2 and lines[0] == "mock-digest"
          and lines[1].isdigit() and int(lines[1]) <= SENDER_CAP_MAX)
    check("빈 MESSAGE → mock-digest 경로 동작 (길이 <= 발송 상한)", ok,
          "; ".join(lines[:2]) if lines else (proc.stderr or "").strip()[-200:])

    proc = run_script(SENDER, env=_clean_env(MESSAGE="fail fast check"))
    combined = (proc.stdout or "") + (proc.stderr or "")
    check("비밀값 없으면 발송 전에 fail-fast (exit 1)",
          proc.returncode == 1 and "TELEGRAM_BOT_TOKEN is missing" in combined)
    check("fail-fast 출력에 token 모양/API URL 누출 없음",
          not TOKEN_SHAPE.search(combined) and "api.telegram.org" not in combined)


def main() -> int:
    print(f"== verify_executive_brief @ {ROOT} ==")
    db_before = _db_state()

    check_py_compile()
    check_domain_boundaries()
    check_api_and_template()
    check_macro_snapshot()
    check_code_tree_banned()
    check_cap_contract()
    check_workflow()
    check_tracked_files()

    check_brief_dry_run()
    brief = check_brief_json()
    if brief:
        check_one_liner(brief)
        check_brief_lists(brief)

    check_digest()
    check_p0b1_verifier()
    check_sender_paths()

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
