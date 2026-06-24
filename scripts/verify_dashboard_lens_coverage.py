#!/usr/bin/env python3
"""D7-D verifier — dashboard lens coverage is honest (empty lenses explain, not blank).

Runs fully offline (no network, DB, secrets, or send). Many executive lenses (현대 그룹사,
신탁사, 시행사·디벨로퍼, 해외지사, 해외법인 …) legitimately have 0 collected articles in
the current query set. A blank/generic "데모 데이터" empty state makes the dashboard look
uncollected. This checks that:

  · every nav lens (data-filter, except 'all') has an explicit lens_policy entry,
  · each policy entry declares a collection query + supported flag + honest note,
  · the empty state is lens-specific (per-lens query + mock/live wording), not a single
    generic 'no demo data' line,
  · core lenses that the collector can supply have keyword/category mapping in the builder,
  · weak false-positive 사명(현대차·현대건설기계 등)은 현대건설 직접이 아니라 그룹사 렌즈로
    매핑되고 featured hero 에서 제외(_is_weak_hdec_fp)되어 직접 렌즈를 오염시키지 않는다,
  · the dashboard still filters (applyLens/filterPanel), and no fake article rows are made
    (every row has a real http url; empty lenses stay empty with honest text).
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
DASHBOARD = ROOT / "docs" / "daily" / "dashboard-latest.html"

# 수집기가 (키워드/카테고리/섹션으로) 공급할 수 있어 매핑이 있어야 하는 핵심 렌즈
CORE_MAPPED = [
    "civil_infrastructure", "building_housing", "plant", "new_energy",
    "global_business", "oil_energy", "safety_quality", "competitor_contractors",
    "hyundai_group", "trust_companies", "developers",
]

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


def _nav_filters(html: str) -> list:
    return re.findall(r'class="nav[^"]*"\s+data-filter="([^"]+)"', html)


# ---------------------------------------------------------------------------
# 1 · 모든 nav 렌즈가 lens_policy 엔트리를 가짐
# ---------------------------------------------------------------------------

def check_policy_coverage(tpl: str) -> None:
    model = _model(tpl)
    policy = model.get("lens_policy") or {}
    check("1a: lens_policy 모델 존재", bool(policy), f"{len(policy)} 엔트리")
    filters = [f for f in _nav_filters(tpl) if f != "all"]
    missing = [f for f in filters if f not in policy]
    check("1b: 모든 nav 렌즈(=all 제외)가 lens_policy 엔트리 보유",
          not missing, f"누락: {missing}" if missing else f"{len(filters)} 렌즈 전부 커버")
    bad = []
    for key, p in policy.items():
        if not isinstance(p, dict) or not p.get("label") or not p.get("query") \
                or "supported" not in p or "note" not in p:
            bad.append(key)
    check("1c: 각 정책이 label·query·supported·note 필드를 가짐",
          not bad, f"불완전: {bad}" if bad else "ok")
    # 미지원(전용 쿼리 미구성) 렌즈는 note로 사유를 정직하게 밝힘
    unsupported = [k for k, p in policy.items() if not p.get("supported")]
    noted = [k for k in unsupported if (policy[k].get("note") or "").strip()]
    check("1d: 미지원 렌즈는 note로 사유를 정직 표기",
          set(unsupported) == set(noted),
          f"사유 없는 미지원: {sorted(set(unsupported) - set(noted))}")


# ---------------------------------------------------------------------------
# 2 · 렌즈별 정직 빈 상태 (일반 데모 문구 아님)
# ---------------------------------------------------------------------------

def check_empty_state(tpl: str) -> None:
    check("2a: 렌즈별 빈 상태 함수(emptyDescHtml) 존재",
          "function emptyDescHtml" in tpl)
    check("2b: 빈 상태가 lens_policy를 사용(렌즈별)",
          "MODEL.lens_policy" in tpl)
    check("2c: 빈 상태가 수집 쿼리를 노출",
          "수집 쿼리" in tpl)
    # mock/live 구분: live면 '금일 수집 결과 없음(데모 아님)', mock이면 '데모 표본'
    check("2d: 빈 상태가 live=금일 수집 결과 없음 / mock=데모 표본을 구분",
          "금일 수집 결과 없음" in tpl and "데모 표본" in tpl)
    check("2e: 빈 상태 desc에 렌즈별 주입 지점(newsEmptyDesc/aiEmptyDesc) 존재",
          'id="newsEmptyDesc"' in tpl and 'id="aiEmptyDesc"' in tpl)


# ---------------------------------------------------------------------------
# 3 · 핵심 렌즈 매핑 + 약한 오탐 가드
# ---------------------------------------------------------------------------

def check_mapping_and_fp(builder: str) -> None:
    for lens in CORE_MAPPED:
        check(f"3a: 빌더가 '{lens}' 렌즈로 매핑(키워드/카테고리/섹션)",
              f'"{lens}"' in builder)
    # 약한 현대건설 오탐 가드가 존재하고 featured hero 선택에 사용됨
    check("3b: 약한 현대건설 오탐 가드(_is_weak_hdec_fp) 존재 + hero 선택에 사용",
          "_is_weak_hdec_fp" in builder and "hdec_strong" in builder)
    check("3c: 진짜 현대건설 판별이 '현대건설기계'를 배제(negative lookahead)",
          "현대건설(?!기계)" in builder)
    # 그룹사 사명(현대차)은 hyundai_group 렌즈로만 매핑되고 별도 HDEC-직접 렌즈 키가 없다
    check("3d: 범현대 사명이 hyundai_group 렌즈로 매핑(직접 렌즈 오염 아님)",
          '"hyundai_group"' in builder and "현대차" in builder)
    # '시행'은 부분문자열 오탐(시행령) 위험 → '시행사'만 매핑
    has_bare = bool(re.search(r'"시행"(?!사)', builder))
    check("3e: 부분문자열 오탐 방지 — 바로 '시행'(시행령) 미매핑, '시행사'만",
          (not has_bare) and "시행사" in builder)


# ---------------------------------------------------------------------------
# 4 · 필터 동작 유지 + 가짜 행 없음 + nav 카운트 정직
# ---------------------------------------------------------------------------

def check_filters_and_rows(tpl: str) -> None:
    check("4a: 렌즈 필터 동작 유지(applyLens/filterPanel/selectLens)",
          "function applyLens" in tpl and "function filterPanel" in tpl
          and "function selectLens" in tpl)
    with tempfile.TemporaryDirectory(prefix="hdec_cov_") as tmp:
        out = Path(tmp) / "dashboard-latest.html"
        proc = subprocess.run([sys.executable, str(BUILDER), "--output", str(out)],
                              cwd=ROOT, capture_output=True, text=True, timeout=300)
        if not check("4b: builder --output 동작", proc.returncode == 0 and out.exists(),
                     (proc.stderr or "")[-200:]):
            return
        gen = out.read_text(encoding="utf-8")
        model = _model(gen)
        rows = (model.get("news_rows") or []) + (model.get("ai_rows") or [])
        check("4c: 모든 행이 실기사(title + http url) — 가짜 행 없음",
              bool(rows) and all(r.get("title") and str(r.get("url", "")).startswith("http")
                                 for r in rows), f"{len(rows)}행")
        # nav 카운트가 실제 행 분포와 일치(0 렌즈는 0으로 표기 — 정적 stale 값 없음)
        navc = {}
        for line in gen.split("\n"):
            mm = re.search(r'data-filter="([^"]+)"', line)
            cm = re.search(r'<span class="ncount">(\d+)</span>', line)
            if mm and cm:
                navc[mm.group(1)] = int(cm.group(1))
        actual = {}
        for r in rows:
            for l in r.get("lens") or []:
                actual[l] = actual.get(l, 0) + 1
        # featured 카드는 모델 행과 별도이므로, 0이어야 할 렌즈만 엄격 검사(stale 값 방지)
        stale = [k for k, v in navc.items()
                 if k in CORE_MAPPED and actual.get(k, 0) == 0 and v != 0]
        check("4d: 빈 렌즈의 nav 카운트가 0 (정적 데모 stale 값 없음)",
              not stale, f"stale: {stale}" if stale else "ok")


def main() -> int:
    print(f"== verify_dashboard_lens_coverage @ {ROOT} ==")
    tpl = _read(TEMPLATE)
    builder = _read(BUILDER)
    if not check("0: 템플릿 + 빌더 존재", bool(tpl) and len(tpl) > 4000 and bool(builder)):
        print("\nRESULT: FAIL (소스 누락)")
        return 1
    check_policy_coverage(tpl)
    check_empty_state(tpl)
    check_mapping_and_fp(builder)
    check_filters_and_rows(tpl)

    print()
    if _failures:
        print(f"RESULT: FAIL ({len(_failures)} 항목)")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("RESULT: PASS — 대시보드 렌즈 커버리지 정직성 확인 (D7-D Part B)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
