"""P0-D3K verifier — executive summary duplicate-topic quality guard.

Runs deterministic helper fixtures, checks the committed static report snapshot,
and reuses the existing static report / human review gate verifiers.

Usage:
    python3 scripts/verify_executive_summary_quality.py
"""

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LATEST_HTML = ROOT / "docs" / "daily" / "latest.html"

DUP_TOPIC = "현대건설 연관 수주 신호"

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


def _topic_key(text: str) -> str:
    text = re.sub(r"\s+", "", text or "")
    return re.sub(r"[.,;:!?()\\[\\]{}'\"“”‘’·ㆍ\\-_/]", "", text)


def _has_self_join(summary: str) -> bool:
    head = summary.split(" — ", 1)[0]
    head = head.replace("동시 부각", "").strip()
    for sep in ("와", "과"):
        if sep not in head:
            continue
        left, right = head.split(sep, 1)
        if _topic_key(left) and _topic_key(left) == _topic_key(right):
            return True
    return False


def _clean_env() -> dict:
    env = {**os.environ, "APP_MODE": "mock"}
    for key in (
        "DB_PATH", "NEWS_MODE", "MACRO_MODE", "MESSAGE", "REPORT_URL",
        "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_IDS", "TELEGRAM_SEND_MODE",
        "REVIEW_APPROVED", "CONFIRM_SEND", "TELEGRAM_BOT_USERNAME",
        "TELEGRAM_PERSONAL_BOT_URL",
    ):
        env.pop(key, None)
    return env


def _run_script(script_name: str, label: str, timeout: int = 360) -> None:
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / script_name)],
        capture_output=True, text=True, cwd=ROOT, env=_clean_env(), timeout=timeout)
    detail = f"rc={proc.returncode}"
    if proc.returncode != 0:
        detail += " " + ((proc.stderr or proc.stdout or "").strip()[-500:])
    check(label, proc.returncode == 0, detail)


def check_helper_fixtures() -> None:
    sys.path.insert(0, str(ROOT))
    os.environ.setdefault("APP_MODE", "mock")
    from app import briefing

    bad_duplicate = f"{DUP_TOPIC}와 {DUP_TOPIC}"

    duplicate = briefing._compose_executive_signal_summary(
        [DUP_TOPIC, DUP_TOPIC],
        "수주 경쟁력·시장 포지션 강화",
        "평판·수주 일정")
    check("중복 topic label → 단일 topic 문장",
          bad_duplicate not in duplicate and "동시 부각" not in duplicate
          and duplicate.startswith(f"{DUP_TOPIC} 중심"),
          duplicate)
    check("중복 topic label → A와 A 없음", not _has_self_join(duplicate), duplicate)

    spaced = briefing._compose_executive_signal_summary(
        [DUP_TOPIC, f"{DUP_TOPIC} "],
        "수주 경쟁력·시장 포지션 강화",
        "평판·수주 일정")
    check("공백 차이 topic label collapse",
          bad_duplicate not in spaced and spaced.startswith(f"{DUP_TOPIC} 중심"),
          spaced)
    check("공백 차이 topic label → A와 A 없음", not _has_self_join(spaced), spaced)

    distinct = briefing._compose_executive_signal_summary(
        ["AI 데이터센터·전력 인프라", "중대재해·안전 규제"],
        "중장기 에너지 인프라 수주",
        "단기 평판·수주 자격")
    check("서로 다른 topic label → two-topic summary 유지",
          "AI 데이터센터·전력 인프라와 중대재해·안전 규제 동시 부각" in distinct,
          distinct)
    check("서로 다른 topic label → A와 A 없음", not _has_self_join(distinct), distinct)

    hdec_pair = briefing._compose_executive_signal_summary(
        [DUP_TOPIC, "현대건설 연관 안전 리스크"],
        "수주 경쟁력·시장 포지션 강화",
        "평판·수주 일정")
    check("현대건설 연관 prefix 반복 제거",
          hdec_pair.split(" — ", 1)[0].count("현대건설 연관") == 1,
          hdec_pair)


def check_latest_html() -> None:
    bad_duplicate = f"{DUP_TOPIC}와 {DUP_TOPIC}"
    if not check("docs/daily/latest.html 존재", LATEST_HTML.exists()):
        return
    html = LATEST_HTML.read_text(encoding="utf-8")
    check("committed latest.html duplicate summary phrase 없음",
          bad_duplicate not in html)


def main() -> int:
    print(f"== verify_executive_summary_quality @ {ROOT} ==")
    check_helper_fixtures()
    check_latest_html()
    _run_script("verify_static_report.py", "mock static report verifier 통과")
    _run_script("verify_human_review_gate.py", "human review gate verifier 통과")

    if _failures:
        print("\nRESULT: FAIL")
        for item in _failures:
            print(f"- {item}")
        return 1
    print("\nRESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
