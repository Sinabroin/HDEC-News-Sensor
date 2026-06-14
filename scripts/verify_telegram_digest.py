"""P0-B1 검증기 — Telegram mock daily digest 도메인 회귀 검사.

네트워크 호출 0건, 비밀값 접근 0건으로 실행된다. 전부 통과하면 exit 0.
금지어 문자열은 이 파일 자체가 코드 트리 grep에 걸리지 않도록
조각(fragment)으로 보관했다가 런타임에 조립한다.

사용법:
    python3 scripts/verify_telegram_digest.py
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
BUILDER = ROOT / "scripts" / "build_telegram_digest.py"
SENDER = ROOT / "scripts" / "send_telegram.py"
WORKFLOW = ROOT / ".github" / "workflows" / "telegram-notify.yml"
MACRO_SNAPSHOT = ROOT / "data" / "mock_macro_snapshot.json"

HEADER_TEXT = "HDEC Executive Radar"
EXPECTED_SIGNALS = 3
MESSAGE_BUDGET_MAX = 3000

# mock macro 고정값 중 점수/강도와 절대 충돌하지 않는 식별용 수치
# (점수는 0~5, 테마 강도는 합산 최대 ~140이라 4자리 값은 등장할 수 없다)
DISTINCTIVE_MACRO_VALUES = ("1480.5", "2864.7")

# 금지어 — rules.md §1/§3 + P0-B1 스프린트 금지 목록 (조각으로 조립)
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

# print/fail 라인에서 비밀값 변수를 직접 출력하는 패턴
LEAK_PATTERNS = ("{token", "(token", "{chat_id", "(chat_id",
                 "{chat_ids", "(chat_ids", "{url", "(url")

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


def run_builder(*flags: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "APP_MODE": "mock"}
    env.pop("MESSAGE", None)
    return subprocess.run(
        [sys.executable, str(BUILDER), *flags],
        capture_output=True, text=True, env=env, cwd=ROOT, timeout=120,
    )


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


def check_dry_run() -> str:
    proc = run_builder("--dry-run")
    ok = proc.returncode == 0 and bool(proc.stdout.strip())
    check("builder --dry-run 동작 (exit 0, 메시지 출력)", ok,
          "" if ok else (proc.stderr or "").strip()[-300:])
    return proc.stdout if ok else ""


def _macro_file_mode() -> str:
    try:
        data = json.loads(MACRO_SNAPSHOT.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return "unavailable"
    return str(data.get("mode") or "unavailable")


def _macro_section(message: str) -> str:
    """digest에서 [Macro Snapshot ...] 섹션 텍스트만 잘라낸다."""
    idx = message.find("[Macro Snapshot")
    if idx < 0:
        return ""
    end = message.find("\n\n", idx)
    return message[idx:end if end > 0 else len(message)]


def check_message_text(message: str) -> None:
    if not message:
        check("digest 메시지 검사", False, "dry-run 출력이 비어 있어 생략")
        return
    check(f"digest에 '{HEADER_TEXT}' 포함", HEADER_TEXT in message)
    check("digest에 mock 모드 표기 포함", "mock" in message.lower())
    lowered = message.lower()
    hits = [t for t in BANNED_TERMS if t in lowered]
    check("digest에 금지어 없음", not hits, ", ".join(hits))
    check(f"digest 길이 {len(message)} <= {MESSAGE_BUDGET_MAX}",
          len(message) <= MESSAGE_BUDGET_MAX)

    # P0-B6 — mock macro 고정값을 시세처럼 발송하지 않는다
    check("digest 헤더에 데이터 출처 표기 (mock 데이터 기반/미연동)",
          "mock 데이터 기반" in message or "뉴스/시장지표 미연동" in message)
    leaked = [v for v in DISTINCTIVE_MACRO_VALUES if v in message]
    check("digest에 mock macro 고정값 수치 없음", not leaked, ", ".join(leaked))
    section = _macro_section(message)
    check("digest에 [Macro Snapshot] 섹션 존재", bool(section))
    if section and _macro_file_mode() != "live":
        check("macro 섹션에 '미연동' 안내 포함", "미연동" in section)
        decimals = re.findall(r"\d+\.\d+", section)
        check("macro 섹션에 수치 없음 (live 아님)", not decimals,
              ", ".join(decimals))


def check_json_mode() -> None:
    proc = run_builder("--json")
    if not check("builder --json 동작 (exit 0)", proc.returncode == 0,
                 (proc.stderr or "").strip()[-300:]):
        return
    try:
        data = json.loads(proc.stdout)
    except ValueError as exc:
        check("builder --json 파싱", False, str(exc))
        return
    signals = data.get("top_signals") or []
    check("JSON top_signals 1건 이상", len(signals) >= 1, f"{len(signals)}건")
    if 1 <= len(signals) < EXPECTED_SIGNALS:
        warn(f"top_signals {len(signals)}건 — 기대값은 {EXPECTED_SIGNALS}건")
    check("JSON mode == mock", data.get("mode") == "mock")
    check("JSON 각 시그널에 rank/title/source 존재",
          all(s.get("rank") and s.get("title") and s.get("source")
              for s in signals))
    chars = data.get("message_chars")
    check(f"JSON message_chars <= {MESSAGE_BUDGET_MAX}",
          isinstance(chars, int) and chars <= MESSAGE_BUDGET_MAX, str(chars))


def check_workflow() -> None:
    if not WORKFLOW.exists():
        check("telegram-notify.yml 존재", False)
        return
    text = WORKFLOW.read_text(encoding="utf-8")
    check("workflow가 secrets.TELEGRAM_BOT_TOKEN 참조",
          "${{ secrets.TELEGRAM_BOT_TOKEN }}" in text)
    check("workflow가 secrets.TELEGRAM_CHAT_IDS 참조",
          "${{ secrets.TELEGRAM_CHAT_IDS }}" in text)
    check("workflow에 token 모양 하드코딩 없음", not TOKEN_SHAPE.search(text))

    unsafe = [line.strip() for line in text.splitlines()
              if re.search(r"TELEGRAM_(BOT_TOKEN|CHAT_IDS)\s*:", line)
              and "secrets." not in line]
    check("TELEGRAM_* env가 secrets로만 주입됨", not unsafe, "; ".join(unsafe))

    message_lines = [line for line in text.splitlines()
                     if re.match(r"\s*MESSAGE\s*:", line)]
    bad_fallback = [line.strip() for line in message_lines
                    if re.search(r"\|\|\s*'[^']+'", line)]
    check("MESSAGE fallback이 빈 문자열 (digest 경로 보장)",
          bool(message_lines) and not bad_fallback, "; ".join(bad_fallback))

    bad_defaults = [line.strip() for line in text.splitlines()
                    if re.match(r"\s*default\s*:", line)
                    and not re.match(r"""\s*default\s*:\s*(""|'')?\s*$""", line)]
    check("workflow input default가 비어 있음 (digest 경로 보장)",
          not bad_defaults, "; ".join(bad_defaults))

    check("workflow APP_MODE=mock 설정", "APP_MODE: mock" in text)


def check_sender_source() -> None:
    src = SENDER.read_text(encoding="utf-8")
    leaks = []
    for lineno, line in enumerate(src.splitlines(), start=1):
        if "print(" not in line and "fail(" not in line:
            continue
        lowered = line.lower()
        for pattern in LEAK_PATTERNS:
            if pattern in lowered:
                leaks.append(f"L{lineno}: {pattern}")
    check("send_telegram.py가 token/chat id/url을 출력하지 않음",
          not leaks, "; ".join(leaks))
    check("send_telegram.py에 token 모양 하드코딩 없음",
          not TOKEN_SHAPE.search(src))

    cap_match = re.search(r"^MAX_MESSAGE_LEN\s*=\s*(\d+)", src, re.M)
    budget_match = re.search(r"^MESSAGE_BUDGET\s*=\s*(\d+)",
                             BUILDER.read_text(encoding="utf-8"), re.M)
    ok = (cap_match and budget_match
          and int(cap_match.group(1)) >= int(budget_match.group(1)))
    detail = ""
    if cap_match and budget_match:
        detail = f"cap={cap_match.group(1)} >= budget={budget_match.group(1)}"
    check("발송 상한 >= digest 예산 (절단 회귀 방지)", bool(ok), detail)


def check_code_tree_banned() -> None:
    hits = []
    for pattern in SCAN_GLOBS:
        for path in sorted(ROOT.glob(pattern)):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            try:
                lowered = path.read_text(encoding="utf-8").lower()
            except (UnicodeDecodeError, OSError):
                continue
            for term in BANNED_TERMS:
                if term in lowered:
                    hits.append(f"{path.relative_to(ROOT)}: {term}")
    check("코드 트리(app/data/scripts/templates/.github) 금지어 0건",
          not hits, "; ".join(hits))


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


def main() -> int:
    print(f"== verify_telegram_digest @ {ROOT} ==")
    check_py_compile()
    message = check_dry_run()
    check_message_text(message)
    check_json_mode()
    check_workflow()
    check_sender_source()
    check_code_tree_banned()
    check_tracked_files()

    if _failures:
        print(f"\nRESULT: FAIL ({len(_failures)} failed)")
        for name in _failures:
            print(f"  - {name}")
        return 1
    print("\nRESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
