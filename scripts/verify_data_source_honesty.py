"""P0-B6 검증기 — 데이터 출처 정직성 회귀 검사.

목적: mock/미연동 데이터가 실시간 데이터처럼 보이는 회귀를 기계적으로 차단한다.
- executive brief에 provenance 필드(news_data_mode/macro_data_mode 등)가 있어야 한다.
- macro_data_mode가 live가 아닌 한, Telegram/리포트/대시보드 어디에도
  mock macro 고정값 수치가 시세처럼 노출되면 안 된다.
- 시장지표 관련 문장에서 "실시간/현재/최신/live" 표현은 부정(미연동/아님 등)과
  함께일 때만 허용된다.
- live 데이터 실패 시 가짜 값으로 fallback 하지 않는다 (unavailable로 강등).

네트워크 호출 0건, 비밀값 접근 0건. 전부 통과하면 exit 0.
금지어 문자열은 조각(fragment)으로 보관했다가 런타임에 조립한다 (기존 verifier 규약).

사용법:
    python3 scripts/verify_data_source_honesty.py
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
REPORT_BUILDER = ROOT / "scripts" / "build_static_report.py"
MACRO_MODULE = ROOT / "app" / "macro_snapshot.py"
MACRO_FILE = ROOT / "data" / "mock_macro_snapshot.json"
TEMPLATE = ROOT / "templates" / "index.html"
COMMITTED_REPORT = ROOT / "docs" / "daily" / "latest.html"
PAGES_INDEX = ROOT / "docs" / "index.html"
README = ROOT / "README.md"
WORKFLOW = ROOT / ".github" / "workflows" / "telegram-notify.yml"
RADAR_DB = ROOT / "radar.db"

# 발송 전에 함께 통과해야 하는 기존 verifier들
EXISTING_VERIFIERS = [
    ("verify_telegram_digest.py", 900),
    ("verify_executive_brief.py", 1200),
    ("verify_static_report.py", 1800),
]

# mock macro 고정값 중 점수(0~5)/테마 강도(~140)와 절대 충돌하지 않는 식별용 수치
DISTINCTIVE_MACRO_VALUES = ("1480.5", "2864.7")

# 시장지표 문맥 토큰 — 이 토큰이 있는 줄만 claim 검사한다
# (mock 기사 snippet의 "실시간 경보 체계" 같은 뉴스 본문 표현은 대상이 아니다)
MACRO_CONTEXT = ("macro", "시장지표", "시세", "시장값")

# live/현재성 주장 표현 — 시장지표 문맥에서 부정 없이 등장하면 실패
CLAIM_RE = re.compile(r"실시간|현재|최신|\blive\b|real[- ]?time", re.I)
NEGATIONS = ("미연동", "아님", "아니", "않", "없", "미반영", "미제공", "미구현",
             "연동 전", "not ")

# 금지어 — rules.md §1/§3 (조각으로 조립, 기존 verifier 규약)
BANNED_TERMS = ["".join(parts) for parts in [
    ("raw", "_payload"),
    ("full", "_text"),
    ("article", "_body"),
    ("full_rss", "_content"),
    ("api.", "x.com"),
    ("twit", "ter"),
    ("x bearer", " token"),
]]

TOKEN_SHAPE = re.compile(r"[0-9]{8,}:[A-Za-z0-9_-]{20,}")

SCAN_GLOBS = ["app/*.py", "app/*.sql", "scripts/*.py",
              "templates/*", "data/*.json", ".github/workflows/*",
              "docs/**/*"]

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
    for key in ("MESSAGE", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_IDS",
                "DB_PATH", "REPORT_URL"):
        env.pop(key, None)
    env.update(extra)
    return env


def run_script(path: Path, *flags: str, timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(path), *flags],
        capture_output=True, text=True, env=_clean_env(), cwd=ROOT, timeout=timeout,
    )


def _db_state() -> tuple | None:
    if not RADAR_DB.exists():
        return None
    stat = RADAR_DB.stat()
    return (stat.st_mtime_ns, stat.st_size)


def _visible_text(html: str) -> str:
    """HTML에서 사람에게 보이는 텍스트만 — <style>과 태그를 제거한다."""
    text = re.sub(r"<style.*?</style>", "", html, flags=re.S)
    return re.sub(r"<[^>]+>", " ", text)


def claim_violations(text: str) -> list[str]:
    """시장지표 문맥 줄에서 부정 없는 live/현재성 주장을 찾는다."""
    bad = []
    for line in text.splitlines():
        folded = line.casefold()
        if not any(t in folded for t in MACRO_CONTEXT):
            continue
        if not CLAIM_RE.search(line):
            continue
        if not any(n in line for n in NEGATIONS):
            bad.append(line.strip()[:90])
    return bad


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


def check_macro_file() -> None:
    if not check("data/mock_macro_snapshot.json 존재", MACRO_FILE.exists()):
        return
    try:
        data = json.loads(MACRO_FILE.read_text(encoding="utf-8"))
    except ValueError as exc:
        check("macro 파일 JSON 파싱", False, str(exc))
        return
    check("macro 파일 mode == mock_static", data.get("mode") == "mock_static",
          str(data.get("mode")))
    check("macro 파일 source == demo_mock", data.get("source") == "demo_mock")
    check("macro 파일 updated_at 존재", bool(data.get("updated_at")))
    disclaimer = str(data.get("disclaimer") or "")
    check("macro 파일 disclaimer가 mock·현재 시장값 아님 명시",
          "mock" in disclaimer.lower() and "현재 시장값" in disclaimer)
    values = data.get("values") or []
    check("macro 파일 values 완비 (label/value)",
          bool(values) and all(v.get("label") and v.get("value") is not None
                               for v in values), f"{len(values)}개")


def check_macro_module() -> None:
    """app/macro_snapshot.py 동작 — live 미구현·가짜 fallback 금지."""
    src = MACRO_MODULE.read_text(encoding="utf-8")
    check("macro 모듈에 네트워크 import 없음",
          not re.search(r"^\s*import (urllib|requests|httpx|socket)|"
                        r"^\s*from (urllib|requests|httpx|socket)", src, re.M))

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    os.environ.setdefault("APP_MODE", "mock")
    from app import macro_snapshot as mod

    snap = mod.get_macro_snapshot("mock")
    check("mock 모드 → macro_data_mode == mock_static",
          snap.get("macro_data_mode") == "mock_static")
    check("mock 모드 → source == demo_mock", snap.get("source") == "demo_mock")
    check("mock 모드 → updated_at/is_stale 존재",
          bool(snap.get("updated_at")) and snap.get("is_stale") is True)
    check("mock 모드 → values 존재", bool(snap.get("values")))

    live = mod.get_macro_snapshot("live")
    check("live 모드(미구현) → unavailable 반환",
          live.get("macro_data_mode") == "unavailable", str(live.get("macro_data_mode")))
    check("live 모드(미구현) → 가짜 값 없음 (values 비어 있음)",
          live.get("values") == [])

    missing = mod.get_macro_snapshot("mock", snapshot_path=ROOT / "data" / "_no_such_file.json")
    check("파일 누락 → unavailable (가짜 fallback 없음)",
          missing.get("macro_data_mode") == "unavailable" and missing.get("values") == [])

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tmp:
        tmp.write("{ broken json !!")
        broken_path = tmp.name
    try:
        broken = mod.get_macro_snapshot("mock", snapshot_path=broken_path)
        check("파일 손상 → unavailable (가짜 fallback 없음)",
              broken.get("macro_data_mode") == "unavailable" and broken.get("values") == [])
    finally:
        os.unlink(broken_path)


# ---------- 산출물 검사 ----------

def check_brief_provenance() -> None:
    proc = run_script(BRIEF_BUILDER, "--json")
    if not check("brief --json 동작", proc.returncode == 0,
                 (proc.stderr or "").strip()[-300:]):
        return
    try:
        brief = json.loads(proc.stdout)
    except ValueError as exc:
        check("brief --json 파싱", False, str(exc))
        return
    required = ("news_data_mode", "news_source", "news_fallback_used",
                "macro_data_mode", "macro_source",
                "macro_updated_at", "macro_is_stale", "data_warning")
    missing = [k for k in required if k not in brief]
    check("brief에 provenance 필드 전부 존재", not missing, "; ".join(missing))
    check("brief news_data_mode == mock", brief.get("news_data_mode") == "mock")
    check("brief news_fallback_used == false (mock 정상 경로)",
          brief.get("news_fallback_used") is False)
    check("brief macro_data_mode ∈ {mock_static, unavailable} (live 미구현)",
          brief.get("macro_data_mode") in ("mock_static", "unavailable"),
          str(brief.get("macro_data_mode")))
    macro = brief.get("macro_snapshot") or {}
    check("brief macro_snapshot에 disclaimer 포함", bool(macro.get("disclaimer")))

    text_proc = run_script(BRIEF_BUILDER, "--dry-run")
    if check("brief --dry-run 동작", text_proc.returncode == 0):
        text = text_proc.stdout
        check("brief 텍스트에 '시장지표 미연동' 표기", "시장지표 미연동" in text)
        leaked = [v for v in DISTINCTIVE_MACRO_VALUES if v in text]
        check("brief 텍스트에 mock macro 수치 없음", not leaked, ", ".join(leaked))
        bad = claim_violations(text)
        check("brief 텍스트에 부정 없는 live/현재성 주장 없음", not bad, "; ".join(bad))


def check_digest_output() -> None:
    proc = run_script(DIGEST_BUILDER, "--dry-run")
    if not check("digest --dry-run 동작", proc.returncode == 0,
                 (proc.stderr or "").strip()[-300:]):
        return
    message = proc.stdout
    check("digest 헤더에 데이터 출처 표기",
          "mock 데이터 기반" in message or "뉴스/시장지표 미연동" in message)
    leaked = [v for v in DISTINCTIVE_MACRO_VALUES if v in message]
    check("digest에 mock macro 고정값 수치 없음", not leaked, ", ".join(leaked))
    # P0-C1.12 — mock/미연동 상태에서 digest는 Macro Snapshot/미연동 placeholder를 넣지
    # 않는다 (거시경제는 리포트로 위임). live 데이터일 때만 수치를 노출한다(아래 단위 검사).
    idx = message.find("[Macro Snapshot")
    section = ""
    if idx >= 0:
        end = message.find("\n\n", idx)
        section = message[idx:end if end > 0 else len(message)]
    check("digest에 mock Macro Snapshot/미연동 placeholder 없음 (거시 리포트 위임)",
          not section and "시장지표 미연동" not in message)
    bad = claim_violations(message)
    check("digest에 부정 없는 live/현재성 주장 없음", not bad, "; ".join(bad))


def check_digest_live_branch() -> None:
    """live 데이터일 때만 수치가 표시되는지 — 빌더 함수 단위 검사 (네트워크 없음)."""
    scripts_dir = str(ROOT / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    from build_telegram_digest import format_digest_message

    base = {
        "header": "HDEC Executive Radar", "date_kst": "2026-01-01",
        "executive_one_liner": "검증용 문장입니다.", "status_board": [],
        "top_signals": [], "theme_rankings": [], "category_counts": [],
        "mode": "mock",
    }
    live_msg = format_digest_message({**base, "macro_snapshot": {
        "macro_data_mode": "live", "source": "test_feed",
        "updated_at": "2026-01-01T08:00:00+09:00",
        "values": [{"label": "USD/KRW", "value": 1399.9, "unit": "원"}],
    }})
    check("live macro → 수치+출처+기준시각 표시",
          "1399.9" in live_msg and "test_feed" in live_msg and "기준" in live_msg)

    mock_msg = format_digest_message({**base, "macro_snapshot": {
        "macro_data_mode": "mock_static",
        "values": [{"label": "USD/KRW", "value": 1480.5, "unit": "원"}],
    }})
    check("mock_static macro → 수치 숨김 + Macro Snapshot 미노출 (거시 리포트 위임)",
          "1480.5" not in mock_msg and "Macro Snapshot" not in mock_msg)


def _check_report_html(html: str, label: str) -> None:
    macro_match = re.search(r'<section aria-label="Macro Snapshot".*?</section>',
                            html, re.S)
    macro_sec = macro_match.group(0) if macro_match else ""
    check(f"{label}: Macro Snapshot 섹션 존재", bool(macro_sec))
    if macro_sec and "macro-cell live" not in macro_sec:
        check(f"{label}: macro 미연동 placeholder", "미연동" in macro_sec)
        decimals = re.findall(r"\d+\.\d+", macro_sec)
        check(f"{label}: macro 섹션에 수치 없음 (live 아님)", not decimals,
              ", ".join(decimals))
    check(f"{label}: '현재 시장값 아님' 경고 포함",
          bool(re.search(r"현재 시장값(?:이|은)? 아니", html)))
    # 게시 리포트는 mock 스냅샷이거나 (워크플로 auto-publish 후) live 리포트일 수 있다.
    # P0-C1.10: live/mock은 보이지 않는 모드 마커로 판별한다 ('LIVE'·'공개 RSS' 노출 금지).
    # live면 중립 '자동 수집' 표기 + mock 배지 금지, 아니면 mock/데모 표기를 요구한다.
    if "news-data-mode:live" in html:
        check(f"{label}: live 출처 표기 (자동 수집)", "자동 수집" in html)
        check(f"{label}: live 리포트에 '데모 데이터' 배지 없음", "데모 데이터" not in html)
    else:
        check(f"{label}: mock/데모 표기 포함", "데모" in html or "mock" in html.lower())
    check(f"{label}: 'LIVE'·'공개 RSS' 기술 표기 없음 (임원 화면)",
          "LIVE" not in html and "공개 RSS" not in html)
    leaked = [v for v in DISTINCTIVE_MACRO_VALUES if v in html]
    check(f"{label}: mock macro 고정값 수치 미노출", not leaked, ", ".join(leaked))
    bad = claim_violations(_visible_text(html))
    check(f"{label}: 부정 없는 live/현재성 주장 없음", not bad, "; ".join(bad))


def check_report_output() -> None:
    with tempfile.TemporaryDirectory(prefix="hdec_honesty_") as tmp:
        out = Path(tmp) / "latest.html"
        proc = run_script(REPORT_BUILDER, "--output", str(out))
        ok = proc.returncode == 0 and out.exists()
        check("report --output 동작", ok,
              "" if ok else (proc.stderr or "").strip()[-300:])
        if ok:
            _check_report_html(out.read_text(encoding="utf-8"), "생성 HTML")
    if COMMITTED_REPORT.exists():
        _check_report_html(COMMITTED_REPORT.read_text(encoding="utf-8"), "커밋 HTML")
    else:
        check("docs/daily/latest.html 존재", False)


def check_pages_index() -> None:
    if not check("docs/index.html 존재 (Pages 루트)", PAGES_INDEX.exists()):
        return
    html = PAGES_INDEX.read_text(encoding="utf-8")
    lowered = html.lower()
    check("랜딩: mock/데모 고지 포함", "mock" in lowered)
    check("랜딩: 외부 리소스/스크립트 없음",
          "http://" not in lowered and "https://" not in lowered
          and "<script" not in lowered)
    bad = claim_violations(_visible_text(html))
    check("랜딩: 부정 없는 live/현재성 주장 없음", not bad, "; ".join(bad))


def check_dashboard_template() -> None:
    html = TEMPLATE.read_text(encoding="utf-8")
    check("대시보드: '시장지표 미연동' 라벨 존재", "시장지표 미연동" in html)
    check("대시보드: macro_data_mode 분기 존재", "macro_data_mode" in html)
    leaked = [v for v in DISTINCTIVE_MACRO_VALUES if v in html]
    check("대시보드: macro 고정값 하드코딩 없음", not leaked, ", ".join(leaked))


def check_no_dev_wording() -> None:
    """사용자 화면에서 개발자/내부 용어 제거 확인 (P0-C1 Phase 4).

    '정적 스냅샷'·'mock_static' 같은 원시 모드/디버그 표현이 임원용 화면에 노출되면
    안 된다 (technical README에서만 허용). 시장지표는 '시장지표 미연동'으로 표기한다.
    """
    targets = [(COMMITTED_REPORT, "리포트"), (PAGES_INDEX, "랜딩"), (TEMPLATE, "대시보드")]
    for path, label in targets:
        if not path.exists():
            check(f"{label} 파일 존재", False)
            continue
        html = path.read_text(encoding="utf-8")
        check(f"{label}: 사용자 화면에 '정적 스냅샷' 표현 없음", "정적 스냅샷" not in html)
        check(f"{label}: 'mock_static' 원시 모드 노출 없음", "mock_static" not in html)


def check_readme() -> None:
    text = README.read_text(encoding="utf-8")
    check("README에 'Data Source Honesty' 섹션 존재", "Data Source Honesty" in text)
    check("README에 REPORT_URL 설정 절차 존재", "REPORT_URL" in text)


def check_workflow() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    check("workflow가 verify_data_source_honesty.py를 발송 전에 실행",
          "verify_data_source_honesty.py" in text)
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
    check("코드 트리(+docs) 금지어/token 모양 0건", not hits, "; ".join(hits))


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


def check_git_diff() -> None:
    try:
        proc = subprocess.run(["git", "diff", "--check"], capture_output=True,
                              text=True, cwd=ROOT, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        warn("git 실행 불가 — diff --check 생략")
        return
    check("git diff --check 통과", proc.returncode == 0,
          (proc.stdout or "").strip()[-200:])


def check_existing_verifiers() -> None:
    for name, timeout in EXISTING_VERIFIERS:
        proc = run_script(ROOT / "scripts" / name, timeout=timeout)
        check(f"기존 {name} 통과 (exit 0)", proc.returncode == 0,
              "" if proc.returncode == 0 else (proc.stdout or "").strip()[-300:])


def main() -> int:
    print(f"== verify_data_source_honesty @ {ROOT} ==")
    db_before = _db_state()

    check_py_compile()
    check_macro_file()
    check_macro_module()
    check_dashboard_template()
    check_no_dev_wording()
    check_readme()
    check_workflow()
    check_code_tree_banned()
    check_tracked_files()

    check_brief_provenance()
    check_digest_output()
    check_digest_live_branch()
    check_report_output()
    check_pages_index()

    check_existing_verifiers()
    check_git_diff()

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
