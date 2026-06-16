"""P0-C1 검증기 — 점수 설명/시각화 UX 회귀 검사.

사용자 혼란을 줬던 점수 표현(5.00점·confidence 0.90·강도 30.7·토픽상 관련 추정 신호)이
직관적 표현으로 바뀌었는지 결정적으로 검사한다 (네트워크/비밀값 없음):
- 중요도는 분모 명시(X.X / 5.0)로 표시되고 미터 막대가 있다.
- 점수 구성요소(현대건설 관련성/사업기회/리스크 등)가 막대/숫자로 보인다.
- 'confidence 0.90' → '판정 신뢰도 90%'.
- '강도' → '테마 비중'(0~100) + 설명 캡션 (P0-C1.9: '상대 강도' 표현 제거).
- '토픽상 관련 추정 신호' → '관련 기사 n건 · 출처 m곳' + 추정 캡션 (P0-C1.9: '유사 주제 기사' 대체).

사용법:
    python3 scripts/verify_score_explanation_ui.py
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT_BUILDER = ROOT / "scripts" / "build_static_report.py"
BRIEF_BUILDER = ROOT / "scripts" / "build_executive_brief.py"
TEMPLATE = ROOT / "templates" / "index.html"
RADAR_DB = ROOT / "radar.db"

ALLOWED_BANDS = {"즉시 확인", "검토 필요", "주간 모니터링", "참고/제외"}
COMPONENT_LABELS = ["현대건설 관련성", "사업기회", "리스크/규제", "긴급도",
                    "출처 신뢰도", "반복/확산 신호"]

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


def _clean_env(**extra: str) -> dict:
    env = {**os.environ, "APP_MODE": "mock", "NEWS_MODE": "mock"}
    for key in ("MESSAGE", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_IDS", "DB_PATH"):
        env.pop(key, None)
    env.update(extra)
    return env


def _db_state() -> tuple | None:
    if not RADAR_DB.exists():
        return None
    stat = RADAR_DB.stat()
    return (stat.st_mtime_ns, stat.st_size)


def run_script(path: Path, *flags: str, timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(path), *flags],
        capture_output=True, text=True, env=_clean_env(), cwd=ROOT, timeout=timeout,
    )


def check_report_ui() -> None:
    with tempfile.TemporaryDirectory(prefix="hdec_scoreui_") as tmp:
        out = Path(tmp) / "latest.html"
        proc = run_script(REPORT_BUILDER, "--output", str(out))
        if not check("리포트 생성 동작", proc.returncode == 0 and out.exists(),
                     (proc.stderr or "").strip()[-200:]):
            return
        html = out.read_text(encoding="utf-8")

    check("중요도 분모 명시 표시 (X.X / 5.0)", "/ 5.0" in html)
    check("중요도 미터 막대 존재 (class=meter)", 'class="meter"' in html)
    check("점수 구성요소 영역 존재 (class=comps)", 'class="comps"' in html)
    check("점수 구성요소 막대 존재 (class=comp)", 'class="comp"' in html)
    present_labels = [c for c in COMPONENT_LABELS if c in html]
    check("점수 구성요소 라벨 4종 이상 노출", len(present_labels) >= 4,
          f"{present_labels}")
    check("'판정 신뢰도' 표현 사용 (confidence 0.90 대체)", "판정 신뢰도" in html)
    check("리포트에 'confidence ' 원시 표기 없음", "confidence " not in html.lower())

    # P0-C1.9: '상대 강도'(단위 불명) → '테마 비중'으로 임원 친화적 표현 통일.
    check("'테마 비중' 표현 사용 (상대 강도 대체)", "테마 비중" in html)
    check("테마 비중 설명 캡션 존재", "가장 큰 테마를 100" in html)
    check("리포트에 옛 '상대 강도' 표현 없음", "상대 강도" not in html)
    # 단위 불명 '강도' 단독 표기가 없어야 한다 (P0-C1.9에서 '강도' 표현 자체를 제거)
    check("단위 불명 '강도' 단독 표기 없음", "강도" not in html)

    # P0-C1.9: '유사 주제 기사' → '관련 기사'로 통일. '참고 묶음 추정' 노이즈 제거.
    check("'관련 기사' 표현 사용 (유사 주제 기사 대체)", "관련 기사" in html)
    check("리포트에 옛 '유사 주제 기사' 표현 없음", "유사 주제 기사" not in html)
    check("리포트에 옛 '토픽상 관련 추정 신호' 표현 없음",
          "토픽상 관련 추정 신호" not in html)
    check("관련 기사 추정 캡션 존재 (추정 표기)", "추정" in html)

    bands_present = [b for b in ALLOWED_BANDS if b in html]
    check("점수대 라벨 1개 이상 노출 (즉시 확인 등)", bool(bands_present),
          f"{bands_present}")


def check_brief_fields() -> None:
    proc = run_script(BRIEF_BUILDER, "--json")
    if not check("brief --json 동작", proc.returncode == 0,
                 (proc.stderr or "").strip()[-200:]):
        return
    try:
        brief = json.loads(proc.stdout)
    except ValueError as exc:
        check("brief --json 파싱", False, str(exc))
        return

    immediate = brief.get("top_immediate_signals") or []
    check("즉시 시그널 존재", bool(immediate), f"{len(immediate)}건")
    bad_band = [str(s.get("score_band")) for s in immediate
                if s.get("score_band") not in ALLOWED_BANDS]
    check("모든 즉시 시그널에 점수대(score_band) 존재", not bad_band,
          "; ".join(bad_band))
    bad_comp = [s.get("article_id") for s in immediate
                if not (s.get("score_components") and len(s["score_components"]) >= 1)]
    check("모든 즉시 시그널에 점수 구성요소(score_components) 존재", not bad_comp,
          "; ".join(str(b) for b in bad_comp))
    # 구성요소 값이 0~5 범위
    vals = [c.get("value") for s in immediate for c in (s.get("score_components") or [])]
    check("구성요소 값이 0~5 범위",
          all(isinstance(v, (int, float)) and 0 <= v <= 5 for v in vals),
          f"{len(vals)}개")

    themes = brief.get("theme_rankings") or []
    check("테마에 relative_strength(테마 비중) 존재",
          all(isinstance(t.get("relative_strength"), int) for t in themes))
    check("relative_strength 0~100 범위",
          all(0 <= t.get("relative_strength", -1) <= 100 for t in themes))
    check("brief에 theme_strength_note/spread_note 캡션 존재",
          bool(brief.get("theme_strength_note")) and bool(brief.get("spread_note")))


def check_dashboard_ui() -> None:
    html = TEMPLATE.read_text(encoding="utf-8")
    for fn in ("scoreMeter", "compBars", "scoreBand", "score5", "scorePct"):
        check(f"대시보드에 점수 헬퍼 {fn}() 존재", fn in html)
    check("대시보드 중요도 미터 마크업 (class=meter)", 'class="meter"' in html)
    check("대시보드 '중요도' 표현 사용", "중요도" in html)
    check("대시보드 '판정 신뢰도' 표현 사용", "판정 신뢰도" in html)
    check("대시보드 '테마 비중' 표현 사용 (상대 강도 대체)", "테마 비중" in html)
    check("대시보드에 옛 '상대 강도' 표현 없음", "상대 강도" not in html)
    check("대시보드에 'confidence ' 원시 표기 없음", "confidence " not in html.lower())


def main() -> int:
    print(f"== verify_score_explanation_ui @ {ROOT} ==")
    db_before = _db_state()

    check_report_ui()
    check_brief_fields()
    check_dashboard_ui()

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
