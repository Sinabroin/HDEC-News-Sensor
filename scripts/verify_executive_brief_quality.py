"""P0-FINAL-MVP 검증기 — executive 시그널 품질 규칙 회귀 검사.

검사 항목 (전부 결정적 — 네트워크/비밀값 없음):
- 모든 시그널 entry에는 내부 액션 라벨이 유지되지만 Telegram은 중요도 중심으로 노출한다.
- spread 라벨이 보수적 표현("토픽상 관련 추정 신호 n건 · 출처 m곳" 또는 "단독 신호")이다.
- "n개 매체 보도" 같은 확정 표현이 brief/digest에 없다 (lesson: spread-score-overclaiming).
- 신규 이슈 Top 5가 단일 카테고리로 도배되지 않는다 (전체 카테고리가 2개 이상일 때).

사용법:
    python3 scripts/verify_executive_brief_quality.py
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRIEF_BUILDER = ROOT / "scripts" / "build_executive_brief.py"
DIGEST_BUILDER = ROOT / "scripts" / "build_telegram_digest.py"

ALLOWED_ACTION_LABELS = {"즉시 확인", "검토 필요", "추적 필요", "모니터링"}
# P0-C1.9: spread 라벨을 임원 친화적 표현으로 변경 ("관련 기사 n건 · 출처 m곳").
SPREAD_LABEL_RE = re.compile(r"^(단독 신호|관련 기사 \d+건 · 출처 \d+곳)$")
OVERCLAIM_RE = re.compile(r"\d+\s*개?\s*매체(가|에서)?\s*보도")

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


def _clean_env() -> dict:
    env = {**os.environ, "APP_MODE": "mock"}
    for key in ("MESSAGE", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_IDS", "DB_PATH"):
        env.pop(key, None)
    return env


def run_script(path: Path, *flags: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(path), *flags],
        capture_output=True, text=True, env=_clean_env(), cwd=ROOT, timeout=300,
    )


def check_brief_quality() -> None:
    proc = run_script(BRIEF_BUILDER, "--json")
    if not check("brief --json 동작", proc.returncode == 0,
                 (proc.stderr or "").strip()[-300:]):
        return
    try:
        brief = json.loads(proc.stdout)
    except ValueError as exc:
        check("brief --json 파싱", False, str(exc))
        return

    entries = ((brief.get("top_immediate_signals") or [])
               + (brief.get("top_new_issues") or []))
    check("시그널 entry 존재", bool(entries), f"{len(entries)}건")

    bad_actions = [f"{e.get('article_id')}:{e.get('action_label')}"
                   for e in entries
                   if e.get("action_label") not in ALLOWED_ACTION_LABELS]
    check("모든 시그널에 표준 액션 라벨 존재", not bad_actions,
          "; ".join(bad_actions))

    bad_spreads = [str((e.get("spread") or {}).get("label"))
                   for e in entries
                   if not SPREAD_LABEL_RE.match(
                       str((e.get("spread") or {}).get("label") or ""))]
    check("spread 라벨이 보수적 추정 표현", not bad_spreads,
          "; ".join(bad_spreads[:3]))

    issues = brief.get("top_new_issues") or []
    low_scores = [f"{e.get('article_id')}:{e.get('final_score')}"
                  for e in issues
                  if (e.get("final_score") or 0) < 2.5]
    has_better = any((e.get("final_score") or 0) >= 2.5
                     for e in (brief.get("top_immediate_signals") or []) + issues)
    check("신규 이슈 Top에 저점수(<2.5) 항목 없음 (대안 후보 있을 때)",
          not (has_better and low_scores), "; ".join(low_scores))
    issue_cats = {e.get("category") for e in issues if e.get("category")}
    total_cats = len(brief.get("category_counts") or [])
    if total_cats >= 2 and len(issues) >= 2:
        check("신규 이슈 Top 5 카테고리 다양성 (2개 이상)",
              len(issue_cats) >= 2, f"{sorted(issue_cats)}")
    else:
        check("신규 이슈 다양성 검사 (카테고리 풀 부족 — 통과 처리)", True)

    text_proc = run_script(BRIEF_BUILDER, "--dry-run")
    if check("brief --dry-run 동작", text_proc.returncode == 0):
        over = OVERCLAIM_RE.findall(text_proc.stdout)
        check("brief 텍스트에 'n개 매체 보도' 확정 표현 없음", not over,
              "; ".join(str(o) for o in over[:3]))


def check_digest_quality() -> None:
    proc = run_script(DIGEST_BUILDER, "--dry-run")
    if not check("digest --dry-run 동작", proc.returncode == 0,
                 (proc.stderr or "").strip()[-300:]):
        return
    message = proc.stdout
    check("digest에 중요도 표시", "중요도 " in message and "/5" in message)
    check("digest에 검토/추적 필요 액션 라벨 없음",
          "검토 필요" not in message and "추적 필요" not in message)
    over = OVERCLAIM_RE.findall(message)
    check("digest에 'n개 매체 보도' 확정 표현 없음", not over,
          "; ".join(str(o) for o in over[:3]))
    # P0-C1.9: digest는 임원용으로 간결화 — spread '추정' 안내를 본문에서 제거하고
    # 상세/근거는 리포트로 위임한다. AI-first 구성에서 노이즈 라벨을 줄인다.
    check("digest가 상세·근거를 리포트로 위임 (리포트 언급)", "리포트" in message)


def main() -> int:
    print(f"== verify_executive_brief_quality @ {ROOT} ==")
    check_brief_quality()
    check_digest_quality()

    if _failures:
        print(f"\nRESULT: FAIL ({len(_failures)} failed)")
        for name in _failures:
            print(f"  - {name}")
        return 1
    print("\nRESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
