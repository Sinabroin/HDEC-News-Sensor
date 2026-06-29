#!/usr/bin/env python3
"""D7-D verifier — AI tab is an actionable executive lens, not generic theme %.

Runs fully offline (no network, DB, secrets, or send). The AI 신호 tab is reframed from
vague theme-strength bars to executive 의사결정 신호: each signal carries 왜 중요한가 /
현대건설 관련성 / 예상 액션 / 관련 렌즈 and is grouped into practical categories
(데이터센터·전력 / 현장 생산성·자동화 / 안전·품질 리스크 / 경쟁사·발주처 / 내부 적용 후보).

Checks that:
  · the builder enriches each ai_row with aiCategory/why/relevance/action (raw 파생),
  · the template renders the actionable categorized view (renderAiSignals) with the
    business-relevance/action microcopy and an honest empty fallback,
  · AI signals are tied to article rows (real http links) — not invented metrics,
  · no fake 실적/계약/전력 values are introduced (전력 실측값 아님 honesty retained),
  · the tab works on mobile (responsive layout retained).
"""

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates" / "dashboard_preview.html"
BUILDER = ROOT / "scripts" / "build_static_dashboard.py"

AI_CATEGORIES = {"dc_power", "field_auto", "safety_ai", "market_ai", "internal"}
CATEGORY_LABELS = [
    "AI·데이터센터·전력 인프라", "현장 생산성·자동화",
    "안전·품질 리스크", "경쟁사·발주처 AI 움직임", "내부 적용 후보",
]
# 예상 액션 5종(의사결정 액션) + 관련성 4유형 — 일반 문구가 아닌 고정 유형으로 검증.
ACTION_TYPES = {"수주기획 검토", "기술검토", "현장 PoC 후보", "안전품질 검토", "모니터링"}
REL_TYPES = {"direct", "indirect", "competitor", "market"}

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


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _model(html: str) -> dict:
    m = re.search(r'<script type="application/json" id="preview-model">(.*?)</script>',
                  html, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except ValueError:
        return {}


# ---------------------------------------------------------------------------
# 1 · 빌더 enrichment — ai_rows에 카테고리/왜/관련성/액션
# ---------------------------------------------------------------------------

def check_builder_enrichment() -> None:
    builder = _read(BUILDER)
    check("1a: 빌더에 AI 카테고리 분류(_ai_category) 존재",
          "_ai_category" in builder and "_AI_CATEGORIES" in builder)
    check("1b: 빌더에 AI 행 enrichment(_ai_enrich) 존재",
          "_ai_enrich" in builder and '"aiCategory"' in builder)
    check("1b2: 빌더가 액션 유형(5종)·관련성 유형을 매핑(_AI_ACTION/relevanceType)",
          "_AI_ACTION" in builder and "relevanceType" in builder)
    with tempfile.TemporaryDirectory(prefix="hdec_ai_") as tmp:
        out = Path(tmp) / "dashboard-latest.html"
        proc = subprocess.run([sys.executable, str(BUILDER), "--output", str(out)],
                              cwd=ROOT, capture_output=True, text=True, timeout=300)
        if not check("1c: builder --output 동작", proc.returncode == 0 and out.exists(),
                     (proc.stderr or "")[-200:]):
            return
        ai = _model(out.read_text(encoding="utf-8")).get("ai_rows") or []
        check("1d: ai_rows 존재", bool(ai), f"{len(ai)}행")
        miss = [r.get("title", "?")[:20] for r in ai
                if not (r.get("aiCategory") and r.get("why")
                        and r.get("relevance") and r.get("action"))]
        check("1e: 모든 AI 행이 aiCategory/why/relevance/action 보유",
              not miss, f"누락: {miss}" if miss else f"{len(ai)}행 전부")
        badcat = sorted({r.get("aiCategory") for r in ai} - AI_CATEGORIES)
        check("1f: aiCategory가 실무 카테고리 5종 안에 있음",
              not badcat, f"미정의: {badcat}" if badcat else "ok")
        # 관련성은 저장 분류에서 파생된 문구여야 함(가짜 라벨 아님)
        rel_ok = all(any(t in (r.get("relevance") or "")
                         for t in ("현대건설", "경쟁사", "해외 발주환경", "관련성 점검"))
                     for r in ai)
        check("1g: 현대건설 관련성이 분류 파생 문구(가짜 라벨 아님)", rel_ok)
        # 행은 실기사 링크에 연결(가짜 지표가 아니라 기사 신호)
        check("1h: AI 신호가 실기사(http url)에 연결 — 발명된 지표 아님",
              all(str(r.get("url", "")).startswith("http") for r in ai))
        # 예상 액션이 5종 액션 유형 안에 있음(internal은 ' · 내부 적용 담당 배정 필요' 접미 허용).
        bad_act = sorted({r.get("action") for r in ai
                          if (str(r.get("action") or "").split(" · ")[0].strip())
                          not in ACTION_TYPES})
        check("1i: 예상 액션이 5종 의사결정 액션 유형 안에 있음",
              not bad_act, f"미정의: {bad_act}" if bad_act else "ok")
        rtypes = {r.get("relevanceType") for r in ai}
        check("1j: 관련성 유형이 direct/indirect/competitor/market 안에 있음",
              bool(rtypes) and rtypes <= REL_TYPES, f"유형: {sorted(rtypes)}")
        # 과장 금지: 모든 신호가 '직접(direct)'으로 분류되지 않는다(간접/경쟁/시장 구분).
        check("1k: 관련성이 전부 '직접'으로 과장되지 않음",
              rtypes != {"direct"}, f"유형: {sorted(rtypes)}")


# ---------------------------------------------------------------------------
# 2 · 템플릿 — 실행가능 카테고리 뷰 + 마이크로카피 + 빈 폴백
# ---------------------------------------------------------------------------

def check_template_view(tpl: str) -> None:
    check("2a: 실행가능 AI 렌더(renderAiSignals) 존재",
          "function renderAiSignals" in tpl)
    check("2b: AI 사업·운영 신호 섹션 라벨 존재",
          "AI 사업·운영 신호" in tpl)
    for lab in CATEGORY_LABELS:
        check(f"2c: 실무 카테고리 라벨 '{lab}' 노출", lab in tpl)
    for micro in ("왜 중요", "현대건설 관련성", "예상 액션", "관련 렌즈"):
        check(f"2d: 임원 마이크로카피 '{micro}' 노출", micro in tpl)
    check("2e: 실질 신호 없을 때 정직 폴백('오늘 실질적 AI 사업 신호 없음')",
          "오늘 실질적 AI 사업 신호 없음" in tpl)
    check("2f: AI 신호가 원문 링크로 연결(srowlink/원문 보기)",
          "srowlink" in tpl and "원문 보기" in tpl)


# ---------------------------------------------------------------------------
# 3 · 가짜 값 없음(전력 실측값 아님 유지) + 모바일 동작
# ---------------------------------------------------------------------------

def check_honesty_and_mobile(tpl: str) -> None:
    # 테마 % 는 보조(참고)로 남기되, 전력 실측값이 아님을 계속 명시(가짜 전력 값 금지)
    check("3a: '전력 실측값 아님' 정직 표기 유지(가짜 전력값 금지)",
          "전력 실측값 아님" in tpl)
    check("3b: 테마 분포는 보조(참고)로 강등, 상대 비중임을 명시",
          "참고 · AI Radar" in tpl and "상대 비중" in tpl)
    # 모바일: 사이드 패널 static + 본문 노출(회귀 규칙 유지)
    check("3c: 모바일 레이아웃 유지(max-width:900px + 사이드 static)",
          "max-width:900px" in tpl and "aside.lens{position:static" in tpl)
    # AI 카드도 렌즈 필터 대상(data-lens 주입)
    check("3d: AI 카드가 렌즈 필터 대상(data-lens 주입)",
          'd.setAttribute("data-lens"' in tpl)


def main() -> int:
    print(f"== verify_ai_signal_usefulness @ {ROOT} ==")
    tpl = _read(TEMPLATE)
    if not check("0: dashboard_preview.html 템플릿 존재", bool(tpl) and len(tpl) > 4000):
        print("\nRESULT: FAIL (템플릿 누락)")
        return 1
    check_builder_enrichment()
    check_template_view(tpl)
    check_honesty_and_mobile(tpl)

    print()
    if _failures:
        print(f"RESULT: FAIL ({len(_failures)} 항목)")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("RESULT: PASS — AI 신호 실행가능성 확인 (D7-D Part C)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
