"""D4-A verifier — public executive report quality guard.

Builds only a temporary executive report under /tmp. It does not send Telegram,
does not read Telegram secrets, and does not mutate docs/daily.
"""

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT_BUILDER = ROOT / "scripts" / "build_static_report.py"
DOCS_DAILY = ROOT / "docs" / "daily"
RADAR_DB = ROOT / "radar.db"

AI_BAD_TERMS = [
    "재생에너지 금융",
    "해상풍력 PF",
    "PF 주선",
    "완도 해상풍력",
    "성수벨트",
    "도시정비 빅3",
    "정비사업 대어급",
    "삼전",
    "하닉",
    "사세요",
    "ETF",
    "진로 모색",
    "대학생 현장견학",
]

_failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    line = f"[{'PASS' if ok else 'FAIL'}] {name}"
    if detail:
        line += f" — {detail}"
    print(line)
    if not ok:
        _failures.append(name)
    return ok


def _clean_env() -> dict:
    env = {**os.environ, "APP_MODE": "mock", "REPORT_AUDIENCE": "executive"}
    for key in ("MESSAGE", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_IDS",
                "DB_PATH", "REPORT_URL", "NEWS_MODE", "MACRO_MODE"):
        env.pop(key, None)
    return env


def _file_state(path: Path) -> dict[str, tuple[int, int]]:
    if not path.exists():
        return {}
    return {
        str(p.relative_to(path)): (p.stat().st_mtime_ns, p.stat().st_size)
        for p in sorted(path.rglob("*"))
        if p.is_file()
    }


def _db_state():
    if not RADAR_DB.exists():
        return None
    stat = RADAR_DB.stat()
    return (stat.st_mtime_ns, stat.st_size)


def _slice_ai_panel(html: str) -> str:
    match = re.search(
        r'<section id="ai-radar" class="radar-panel radar"[^>]*>(.*?)'
        r'<section id="biz-radar" class="radar-panel radar"',
        html,
        re.S,
    )
    return match.group(1) if match else ""


def _risk_event_count(html: str) -> int | None:
    match = re.search(
        r'<section aria-label="주요 리스크 사건">.*?'
        r'<span class="cd-count num">([0-9]+)건</span>',
        html,
        re.S,
    )
    return int(match.group(1)) if match else None


def main() -> int:
    print(f"== verify_public_report_quality @ {ROOT} ==")
    docs_before = _file_state(DOCS_DAILY)
    db_before = _db_state()
    with tempfile.TemporaryDirectory(prefix="hdec_public_quality_") as tmp:
        out_path = Path(tmp) / "executive.html"
        check("temporary output path is outside docs/daily",
              not out_path.resolve().is_relative_to(DOCS_DAILY.resolve()),
              str(out_path))
        proc = subprocess.run(
            [sys.executable, str(REPORT_BUILDER), "--output", str(out_path),
             "--audience", "executive"],
            capture_output=True,
            text=True,
            cwd=ROOT,
            env=_clean_env(),
            timeout=300,
        )
        check("executive report build succeeds",
              proc.returncode == 0 and out_path.exists() and out_path.stat().st_size > 0,
              (proc.stderr or proc.stdout or "").strip()[-300:])
        html = out_path.read_text(encoding="utf-8") if out_path.exists() else ""
        ai_panel = _slice_ai_panel(html)
        check("AI panel slice extracted", bool(ai_panel))
        bad = [term for term in AI_BAD_TERMS if term in ai_panel]
        check("AI panel bad terms absent", not bad, "; ".join(bad))
        has_signal = "<h3>" in ai_panel
        has_empty = "오늘 AI 인프라·건설 AI 신호 없음" in ai_panel
        check("empty AI panel has empty-state copy",
              has_signal or has_empty,
              f"has_signal={has_signal} has_empty={has_empty}")
        risk_count = _risk_event_count(html)
        check("executive risk event count parsed", risk_count is not None)
        if risk_count is not None:
            check("executive risk event count <= 5", risk_count <= 5, str(risk_count))
    check("docs/daily not mutated", _file_state(DOCS_DAILY) == docs_before)
    check("repo radar.db unchanged", _db_state() == db_before,
          f"before={db_before} after={_db_state()}")
    if _failures:
        print(f"\nRESULT: FAIL ({len(_failures)} failed)")
        for name in _failures:
            print(f"  - {name}")
        return 1
    print("\nRESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
