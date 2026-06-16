"""P0-B5 검증기 — 정적 리포트 페이지 + Telegram 링크 카드 회귀 검사.

네트워크 호출 0건, 비밀값 접근 0건으로 실행된다. 전부 통과하면 exit 0.
금지어 문자열은 이 파일 자체가 코드 트리 grep에 걸리지 않도록
조각(fragment)으로 보관했다가 런타임에 조립한다 (P0-B1/B2 verifier와 동일 규약).

사용법:
    python3 scripts/verify_static_report.py
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
REPORT_BUILDER = ROOT / "scripts" / "build_static_report.py"
SENDER = ROOT / "scripts" / "send_telegram.py"
WORKFLOW = ROOT / ".github" / "workflows" / "telegram-notify.yml"
COMMITTED_REPORT = ROOT / "docs" / "daily" / "latest.html"
NOJEKYLL = ROOT / "docs" / ".nojekyll"
RADAR_DB = ROOT / "radar.db"

# 존재하면 함께 돌리는 기존 verifier들 (앞 2개는 필수)
REQUIRED_VERIFIERS = ["verify_telegram_digest.py", "verify_executive_brief.py"]
OPTIONAL_VERIFIERS = ["verify_issue_clusters.py", "verify_executive_brief_quality.py"]

BUTTON_TEXT = "오늘 브리프 보기"
TELEGRAM_API_HOST = "https://api.telegram.org"

# 모드/신호 유무와 무관하게 항상 존재하는 구조 마커 (mock·live 공통).
# "즉시 알림 후보"는 현황판 라벨, "주요 테마/카테고리 요약"은 전체 근거 서랍,
# AI 레이더/리스크·규제/거시경제/전체 근거는 P0-C1.9 IA 상단 목차로 항상 렌더된다.
CORE_HTML_MARKERS = [
    "HDEC Executive Radar", "Executive Daily Brief", "오늘의 Executive Signal",
    "즉시 알림 후보", "주요 테마", "카테고리 요약",
    'lang="ko"', "viewport", "Pretendard",
    "AI 레이더", "리스크·규제", "거시경제", "전체 근거", "테마 비중",
]
# 시그널 카드가 렌더된 경우에만 존재하는 마커 (P0-C1 점수 미터/원문 링크).
# fresh mock 빌드는 결정적으로 항상 카드가 있으므로 늘 요구하고, 게시된 live
# 리포트는 신호가 0건인 날에도 게이트가 막히지 않게 카드 존재 시에만 요구한다.
# P0-C1.9: '권장 워치 액션' 제거, '중요도'는 점수 아코디언 summary로 유지.
SIGNAL_HTML_MARKERS = [
    "중요도", 'class="meter"', "원문 보기",
]

# mock macro 고정값 중 점수/강도와 충돌할 수 없는 식별용 수치 (4자리)
DISTINCTIVE_MACRO_VALUES = ("1480.5", "2864.7")

PAGES_INDEX = ROOT / "docs" / "index.html"

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

# 코드 트리 금지어 스캔 대상 — P0-B2 목록 + docs (스펙 문서 rules.md/PRD.md/.claude 제외)
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


def run_script(path: Path, *flags: str, env: dict | None = None,
               timeout: int = 300) -> subprocess.CompletedProcess:
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


def check_report_builder_source() -> None:
    src = REPORT_BUILDER.read_text(encoding="utf-8")
    check("리포트 빌더에 네트워크 import 없음 (offline 보장)",
          not re.search(r"^\s*import (urllib|requests|httpx|socket)|"
                        r"^\s*from (urllib|requests|httpx|socket)",
                        src, re.M))
    check("리포트 빌더가 TELEGRAM 비밀값을 참조하지 않음", "TELEGRAM" not in src)
    check("리포트 빌더가 공유 brief 레이어를 재사용",
          "build_brief_via_mock_pipeline" in src)


def check_sender_source() -> None:
    src = SENDER.read_text(encoding="utf-8")
    check("sender가 REPORT_URL env를 읽음 (하드코딩 아님)",
          'os.environ.get("REPORT_URL"' in src)
    check("sender에 reply_markup/inline_keyboard 버튼 지원 존재",
          "reply_markup" in src and "inline_keyboard" in src)
    check(f"sender 버튼 텍스트 '{BUTTON_TEXT}' 존재", BUTTON_TEXT in src)

    urls = re.findall(r"https?://[^\s\"'}]+", src)
    foreign = [u for u in urls if not u.startswith(TELEGRAM_API_HOST)]
    check("sender의 URL 리터럴은 Telegram API 호스트뿐", not foreign,
          "; ".join(foreign))

    leaks = []
    for lineno, line in enumerate(src.splitlines(), start=1):
        if "print(" not in line and "fail(" not in line:
            continue
        if "{report_url" in line or "report_url}" in line:
            leaks.append(f"L{lineno}")
    check("sender가 REPORT_URL 값을 출력하지 않음 (enabled true/false만)",
          not leaks, "; ".join(leaks))
    check("sender에 'Report link enabled' 안전 로그 존재",
          "Report link enabled" in src)


def check_workflow() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    for verifier in REQUIRED_VERIFIERS + ["verify_static_report.py"]:
        check(f"workflow가 {verifier}를 발송 전에 실행", verifier in text)
    check("workflow가 발송 전에 정적 리포트를 생성",
          "build_static_report.py --output docs/daily/latest.html" in text)
    build_idx = text.find("build_static_report.py")
    send_idx = text.find("send_telegram.py")
    check("리포트 생성 step이 발송 step보다 앞",
          0 <= build_idx < send_idx)

    report_lines = [line for line in text.splitlines()
                    if re.match(r"\s*REPORT_URL\s*:", line)]
    ok = bool(report_lines) and all(
        ("vars.REPORT_URL" in line or "secrets.REPORT_URL" in line)
        and "http" not in line.lower()
        for line in report_lines)
    check("REPORT_URL이 vars/secrets로만 주입됨 (URL 하드코딩 없음)", ok,
          "; ".join(line.strip() for line in report_lines) or "REPORT_URL 라인 없음")
    check("workflow APP_MODE=mock 유지", "APP_MODE: mock" in text)

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
    check("git diff --check 통과 (whitespace 오류 없음)", proc.returncode == 0,
          (proc.stdout or "").strip()[-200:])


# ---------- 리포트 실행 검사 ----------

def check_report_dry_run() -> None:
    proc = run_script(REPORT_BUILDER, "--dry-run")
    ok = proc.returncode == 0 and "Executive Daily Brief" in (proc.stdout or "")
    check("report --dry-run 동작 (exit 0, 요약 출력)", ok,
          "" if ok else (proc.stderr or "").strip()[-300:])


def check_report_json() -> None:
    proc = run_script(REPORT_BUILDER, "--json")
    if not check("report --json 동작 (exit 0)", proc.returncode == 0,
                 (proc.stderr or "").strip()[-300:]):
        return
    try:
        meta = json.loads(proc.stdout)
    except ValueError as exc:
        check("report --json 파싱", False, str(exc))
        return
    check("report --json 파싱", True)
    check("report JSON mode == mock", meta.get("mode") == "mock")
    check("report JSON html_chars > 0",
          isinstance(meta.get("html_chars"), int) and meta["html_chars"] > 0,
          str(meta.get("html_chars")))
    check("report JSON signal_count 1~3",
          isinstance(meta.get("signal_count"), int)
          and 1 <= meta["signal_count"] <= 3, str(meta.get("signal_count")))
    required_sections = {"hero", "status_board", "one_liner", "top_signals",
                         "ai_radar", "macro", "evidence",
                         "themes", "categories", "notes", "footer"}
    missing = required_sections - set(meta.get("sections") or [])
    check("report JSON 필수 섹션 전부 포함", not missing, "; ".join(sorted(missing)))
    check("report JSON default_output == docs/daily/latest.html",
          meta.get("default_output") == "docs/daily/latest.html")


def _url_policy_violations(html: str) -> list[str]:
    """P0-C1 URL 정책: 기사 원문 링크(href)만 허용, 그 외 외부 리소스는 금지.

    href 속성값을 제거한 뒤 남는 http/https는 외부 script/CSS/이미지/iframe/CDN/fetch로
    간주해 위반으로 잡는다. 외부 리소스 태그·@import·url(http)·src=http도 금지한다.
    """
    issues = []
    stripped = re.sub(r'href="[^"]*"', 'href=""', html)
    stripped = re.sub(r"href='[^']*'", "href=''", stripped)
    low = stripped.lower()
    if "http://" in low or "https://" in low:
        idx = low.find("https://")
        if idx < 0:
            idx = low.find("http://")
        issues.append("href 외 http(s): …"
                      + stripped[max(0, idx - 24):idx + 30].replace("\n", " "))
    for tag in ("<script", "<iframe", "<img", "<link ", "<link>", "<object", "<embed"):
        if tag in html.lower():
            issues.append(f"외부 리소스 태그: {tag}")
    for token in ("@import", "url(http", "src=http", 'src="http', "src='http"):
        if token in html.lower():
            issues.append(f"외부 참조: {token}")
    return issues


def _external_anchor_safety(html: str) -> list[str]:
    """http(s) href 앵커가 target=_blank + rel=noopener noreferrer를 갖는지 검사."""
    bad = []
    for m in re.finditer(r"<a\b[^>]*>", html):
        tag = m.group(0)
        href = re.search(r'href="([^"]*)"', tag)
        if not href or not href.group(1).lower().startswith(("http://", "https://")):
            continue
        if 'target="_blank"' not in tag:
            bad.append("target 누락: " + tag[:70])
        if "noopener" not in tag or "noreferrer" not in tag:
            bad.append("rel noopener noreferrer 누락: " + tag[:70])
    return bad


def _is_live_report(html: str) -> bool:
    """게시된 리포트가 live(공개 RSS) 모드인지 — data_warning/모드 배지로 판별한다.

    live 리포트의 data_warning은 '뉴스: 공개 RSS 수집 · 시장지표: 미연동',
    모드 배지는 'LIVE · 공개 RSS'다. mock/fallback 리포트에는 둘 다 없다.
    """
    return "공개 RSS 수집" in html or "LIVE · 공개 RSS" in html


def _check_html_content(html: str, label: str, committed: bool = False) -> None:
    live = _is_live_report(html)

    for marker in CORE_HTML_MARKERS:
        check(f"{label}: '{marker}' 포함", marker in html)
    # 시그널 카드 마커: fresh 빌드는 항상 요구. committed 리포트는 신호 카드가
    # 실제 렌더된 경우에만 요구한다 (신호 0건 live day에도 게이트가 막히지 않게).
    if (not committed) or ('class="sig"' in html):
        for marker in SIGNAL_HTML_MARKERS:
            check(f"{label}: '{marker}' 포함", marker in html)
    else:
        check(f"{label}: 신호 0건 안내 표시", "감지된 신호가 없습니다" in html)

    # 데이터 출처 정직성 — live면 '공개 RSS 수집' 표기 + mock 배지/placeholder 금지,
    # mock이면 mock/데모 표기 (mock을 live로, live를 mock으로 오인하지 않게).
    if live:
        check(f"{label}: live 출처 표기 (공개 RSS 수집)", "공개 RSS 수집" in html)
        check(f"{label}: live 리포트에 '데모 데이터' 배지 없음", "데모 데이터" not in html)
        check(f"{label}: live 리포트에 mock placeholder 도메인 없음",
              "example.com" not in html)
    else:
        check(f"{label}: mock 표기 포함", "mock" in html.lower())
    check(f"{label}: spread 추정 라벨 포함", "추정" in html)

    # P0-B6 — mock macro 고정값을 시세처럼 렌더링하지 않는다
    macro_match = re.search(r'<section aria-label="Macro Snapshot".*?</section>',
                            html, re.S)
    macro_sec = macro_match.group(0) if macro_match else ""
    check(f"{label}: Macro Snapshot 섹션 존재", bool(macro_sec))
    if macro_sec and 'macro-cell live' not in macro_sec:
        check(f"{label}: macro 미연동 placeholder 표시", "미연동" in macro_sec)
        decimals = re.findall(r"\d+\.\d+", re.sub(r"<style.*?</style>", "",
                                                  macro_sec, flags=re.S))
        check(f"{label}: macro 섹션에 수치 없음 (live 아님)", not decimals,
              ", ".join(decimals))
    check(f"{label}: macro 경고(현재 시장값 아님) 포함",
          bool(re.search(r"현재 시장값(?:이|은)? 아니", html)))
    leaked = [v for v in DISTINCTIVE_MACRO_VALUES if v in html]
    check(f"{label}: mock macro 고정값 수치 미노출", not leaked, ", ".join(leaked))

    lowered = html.lower()
    # P0-C1: 기사 원문 링크(href)는 허용, 그 외 외부 리소스(script/css/img/iframe/cdn)는 금지.
    url_issues = _url_policy_violations(html)
    check(f"{label}: 기사 링크(href) 외 외부 리소스 없음", not url_issues,
          "; ".join(url_issues[:3]))
    anchor_issues = _external_anchor_safety(html)
    check(f"{label}: 외부 링크는 새 탭 + noopener noreferrer", not anchor_issues,
          "; ".join(anchor_issues[:3]))
    check(f"{label}: telegram/webhook 문자열 없음",
          "telegram" not in lowered and "webhook" not in lowered)

    hits = [t for t in BANNED_TERMS if t in lowered]
    check(f"{label}: 금지 필드/용어 0건", not hits, ", ".join(hits))
    check(f"{label}: token 모양 문자열 없음", not TOKEN_SHAPE.search(html))


def check_report_output() -> None:
    with tempfile.TemporaryDirectory(prefix="hdec_report_") as tmp:
        out = Path(tmp) / "daily" / "latest.html"
        proc = run_script(REPORT_BUILDER, "--output", str(out))
        ok = proc.returncode == 0 and out.exists() and out.stat().st_size > 0
        check("report --output 동작 (파일 생성, 하위 디렉터리 자동 생성)", ok,
              "" if ok else (proc.stderr or "").strip()[-300:])
        if ok:
            _check_html_content(out.read_text(encoding="utf-8"), "생성 HTML")


def check_committed_report() -> None:
    if not check("docs/daily/latest.html 존재 (게시 대상 스냅샷)",
                 COMMITTED_REPORT.exists()):
        return
    # 게시된 리포트는 mock 스냅샷이거나 (워크플로 auto-publish 후) live 리포트일 수
    # 있다 — committed=True로 모드 인지 검사한다.
    _check_html_content(COMMITTED_REPORT.read_text(encoding="utf-8"), "커밋 HTML",
                        committed=True)
    check("docs/.nojekyll 존재 (Pages에서 Jekyll 비활성)", NOJEKYLL.exists())


def check_pages_root() -> None:
    """Pages 루트(/) 404 방지 — docs/index.html 랜딩 페이지 (P0-FINAL-MVP)."""
    if not check("docs/index.html 존재 (Pages 루트 진입점)", PAGES_INDEX.exists()):
        return
    html = PAGES_INDEX.read_text(encoding="utf-8")
    lowered = html.lower()
    check("랜딩: 최신 리포트 상대 링크 존재", 'href="./daily/latest.html"' in html)
    check("랜딩: mock/데모 고지 포함", "mock" in lowered and "데모" in html)
    check("랜딩: 외부 리소스 없음 (http/https URL 0건)",
          "http://" not in lowered and "https://" not in lowered)
    check("랜딩: <script> 없음", "<script" not in lowered)
    check("랜딩: telegram/webhook 문자열 없음",
          "telegram" not in lowered and "webhook" not in lowered)


# ---------- sender 동작 검사 (네트워크/비밀값 없음) ----------

def check_sender_paths() -> None:
    resolver = ("import sys; sys.path.insert(0, 'scripts'); "
                "from send_telegram import resolve_message; "
                "m, s = resolve_message(); print(s); print(len(m))")

    proc = subprocess.run([sys.executable, "-c", resolver], capture_output=True,
                          text=True, cwd=ROOT, timeout=300,
                          env=_clean_env(MESSAGE="P0-B5 sender path check"))
    lines = (proc.stdout or "").strip().splitlines()
    check("MESSAGE env 우선 경로 유지 (env-message)",
          proc.returncode == 0 and lines and lines[0] == "env-message",
          "; ".join(lines[:1]))

    proc = subprocess.run([sys.executable, "-c", resolver], capture_output=True,
                          text=True, cwd=ROOT, timeout=300, env=_clean_env())
    lines = (proc.stdout or "").strip().splitlines()
    ok = (proc.returncode == 0 and len(lines) >= 2 and lines[0] == "mock-digest"
          and lines[1].isdigit() and int(lines[1]) <= 3500)
    check("빈 MESSAGE → mock-digest 경로 유지 (텍스트 fallback)", ok,
          "; ".join(lines[:2]) if lines else (proc.stderr or "").strip()[-200:])


def check_report_url_paths() -> None:
    sample_url = "https://example.com/daily/latest.html"
    resolver = ("import sys; sys.path.insert(0, 'scripts'); "
                "from send_telegram import resolve_report_url; "
                "print(repr(resolve_report_url()))")

    proc = subprocess.run([sys.executable, "-c", resolver], capture_output=True,
                          text=True, cwd=ROOT, timeout=120, env=_clean_env())
    check("REPORT_URL 미설정 → 빈 값 (실패하지 않음)",
          proc.returncode == 0 and (proc.stdout or "").strip() == "''",
          (proc.stdout or "").strip())

    proc = subprocess.run([sys.executable, "-c", resolver], capture_output=True,
                          text=True, cwd=ROOT, timeout=120,
                          env=_clean_env(REPORT_URL=sample_url))
    check("REPORT_URL 설정 → 그대로 사용",
          proc.returncode == 0 and (proc.stdout or "").strip() == repr(sample_url))

    proc = subprocess.run([sys.executable, "-c", resolver], capture_output=True,
                          text=True, cwd=ROOT, timeout=120,
                          env=_clean_env(REPORT_URL="not-a-link"))
    check("REPORT_URL 형식 오류 → 비활성화 (exit 0, 빈 값)",
          proc.returncode == 0 and (proc.stdout or "").strip() == "''",
          (proc.stdout or "").strip())

    payload_test = (
        "import sys, json; sys.path.insert(0, 'scripts'); "
        "from send_telegram import build_payload, BUTTON_TEXT; "
        "p1 = build_payload('123', 'msg', ''); "
        "print('no-button' if 'reply_markup' not in p1 else 'button'); "
        f"p2 = build_payload('123', 'msg', '{sample_url}'); "
        "rm = json.loads(p2['reply_markup']); btn = rm['inline_keyboard'][0][0]; "
        "print(btn['text'] == BUTTON_TEXT); print(btn['url'])")
    proc = subprocess.run([sys.executable, "-c", payload_test], capture_output=True,
                          text=True, cwd=ROOT, timeout=120, env=_clean_env())
    lines = (proc.stdout or "").strip().splitlines()
    check("payload: REPORT_URL 없으면 버튼 없음 (기존 동작 보존)",
          proc.returncode == 0 and lines and lines[0] == "no-button",
          "; ".join(lines[:1]))
    check("payload: REPORT_URL 있으면 inline 버튼(reply_markup) 포함",
          proc.returncode == 0 and len(lines) >= 3
          and lines[1] == "True" and lines[2] == sample_url,
          "; ".join(lines[1:3]))

    proc = run_script(SENDER, env=_clean_env(MESSAGE="fail fast check",
                                             REPORT_URL=sample_url))
    combined = (proc.stdout or "") + (proc.stderr or "")
    check("비밀값 없으면 발송 전에 fail-fast 유지 (exit 1)",
          proc.returncode == 1 and "TELEGRAM_BOT_TOKEN is missing" in combined)
    check("fail-fast 출력에 REPORT_URL 값/token/API URL 누출 없음",
          "example.com" not in combined and "api.telegram.org" not in combined
          and not TOKEN_SHAPE.search(combined))


# ---------- 기존 verifier 회귀 ----------

def check_existing_verifiers() -> None:
    # WSL/DrvFs에서 하위 verifier가 파이프라인을 여러 번 돌리므로 timeout을 넉넉히 둔다
    for name in REQUIRED_VERIFIERS:
        proc = run_script(ROOT / "scripts" / name, timeout=900)
        check(f"기존 {name} 통과 (exit 0)", proc.returncode == 0,
              "" if proc.returncode == 0 else (proc.stdout or "").strip()[-300:])
    for name in OPTIONAL_VERIFIERS:
        path = ROOT / "scripts" / name
        if not path.exists():
            continue
        proc = run_script(path, timeout=900)
        check(f"기존 {name} 통과 (exit 0)", proc.returncode == 0,
              "" if proc.returncode == 0 else (proc.stdout or "").strip()[-300:])


def main() -> int:
    print(f"== verify_static_report @ {ROOT} ==")
    db_before = _db_state()

    check_py_compile()
    check_report_builder_source()
    check_sender_source()
    check_workflow()
    check_code_tree_banned()
    check_tracked_files()

    check_report_dry_run()
    check_report_json()
    check_report_output()
    check_committed_report()
    check_pages_root()

    check_sender_paths()
    check_report_url_paths()
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
