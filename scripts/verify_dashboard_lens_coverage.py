#!/usr/bin/env python3
"""D7-E verifier — lens coverage is honest + driven by a central lens-first query policy.

Runs fully offline (no network, DB, secrets, or send). D7-E introduces a single source of
truth, data/lens_queries.json (loaded by app/lens_queries.py), shared by BOTH the dashboard
builder (lens_policy + keyword tagging) and the live collector (lens query universe). Empty
lenses must explain themselves — "쿼리는 돌렸으나 결과 없음" (supported) or "전용 수집 쿼리
미구성" (unconfigured) — not a blank/generic 데모 line, and weak false positives (현대건설기계
등) must not pollute the HDEC-direct lens.

Checks:
  · central config + leaf exist and expose policy / keyword / collection-query helpers,
  · every nav lens has a policy entry with label·query·supported·note·collection,
  · the builder imports the central policy (injects lens_policy + keyword tagging from it),
  · the live collector reuses the same policy (lens-first collection query groups),
  · empty-state text is lens-specific (mock/live/unconfigured wording), not generic demo,
  · core lenses are mapped via the config; FP guard keeps 현대건설기계/현대차 out of direct,
  · the generated model's lens_policy carries collection status and core lenses are not all 0,
  · filters still work and no fake article rows are made (every row has a real http url).
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
COLLECTOR = ROOT / "app" / "live_collector.py"
CONFIG = ROOT / "data" / "lens_queries.json"
LEAF = ROOT / "app" / "lens_queries.py"
DASHBOARD = ROOT / "docs" / "daily" / "dashboard-latest.html"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 수집기가 (config 쿼리/키워드로) 공급할 수 있어 매핑이 있어야 하는 핵심 렌즈
CORE_MAPPED = [
    "civil_infrastructure", "building_housing", "plant", "new_energy",
    "global_business", "oil_energy", "safety_quality", "competitor_contractors",
    "hyundai_group", "trust_companies", "developers",
]
_COLLECTION_VALUES = {"query", "keyword", "derived", "unconfigured"}

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
# 0 · 중앙 정책(단일 소스) + leaf 존재 및 동작
# ---------------------------------------------------------------------------

def check_central_policy(tpl: str) -> None:
    check("0a: 중앙 렌즈 쿼리 정책 파일(data/lens_queries.json) 존재", CONFIG.exists())
    check("0b: 렌즈 정책 leaf(app/lens_queries.py) 존재", LEAF.exists())
    try:
        from app import lens_queries  # noqa: WPS433
    except Exception as exc:  # noqa: BLE001
        check("0c: leaf import 가능", False, str(exc))
        return
    pol = lens_queries.policy_for_model()
    pairs = lens_queries.keyword_lens_pairs()
    groups = lens_queries.collection_query_groups()
    check("0c: leaf가 정책/키워드/수집쿼리 헬퍼 제공",
          isinstance(pol, dict) and bool(pol) and isinstance(pairs, list)
          and isinstance(groups, list))
    # 중앙 정책이 모든 nav 렌즈(=all 제외)를 덮는다.
    nav = [f for f in _nav_filters(tpl) if f != "all"]
    missing = [f for f in nav if f not in pol]
    check("0d: 중앙 정책이 모든 nav 렌즈를 커버", not missing,
          f"누락: {missing}" if missing else f"{len(nav)} 렌즈")
    # 수집 쿼리 그룹은 collector-supported 렌즈만(미연동 렌즈 제외) — 가짜 수집 금지.
    names = {g.get("name") for g in groups}
    check("0e: 수집 쿼리 그룹이 supported 렌즈만 포함(미연동 제외)",
          "lens:hormuz" not in names and "lens:overseas_branch" not in names
          and any(n and n.startswith("lens:") for n in names),
          f"{len(groups)} 그룹")


# ---------------------------------------------------------------------------
# 1 · 빌더/수집기가 중앙 정책을 재사용
# ---------------------------------------------------------------------------

def check_reuse(builder: str, collector: str) -> None:
    check("1a: 빌더가 중앙 정책 import(app.lens_queries)",
          "from app import lens_queries" in builder or "import lens_queries" in builder)
    check("1b: 빌더가 키워드 태깅을 중앙 정책에서 로드",
          "lens_queries.keyword_lens_pairs()" in builder)
    check("1c: 빌더가 lens_policy를 중앙 정책에서 주입",
          "lens_queries.policy_for_model()" in builder and 'model["lens_policy"]' in builder)
    check("1d: 라이브 수집기가 동일 정책을 재사용(렌즈 쿼리 그룹 병합)",
          "lens_queries" in collector and "_merge_lens_query_groups" in collector
          and "collection_query_groups()" in collector)


# ---------------------------------------------------------------------------
# 2 · 정책 커버리지 + collection 상태 (템플릿 모델)
# ---------------------------------------------------------------------------

def check_policy_coverage(tpl: str) -> None:
    model = _model(tpl)
    policy = model.get("lens_policy") or {}
    check("2a: lens_policy 모델 존재", bool(policy), f"{len(policy)} 엔트리")
    filters = [f for f in _nav_filters(tpl) if f != "all"]
    missing = [f for f in filters if f not in policy]
    check("2b: 모든 nav 렌즈(=all 제외)가 lens_policy 엔트리 보유",
          not missing, f"누락: {missing}" if missing else f"{len(filters)} 렌즈 전부 커버")
    bad = []
    for key, p in policy.items():
        if not isinstance(p, dict) or not p.get("label") or not p.get("query") \
                or "supported" not in p or "note" not in p or "collection" not in p:
            bad.append(key)
    check("2c: 각 정책이 label·query·supported·note·collection 필드를 가짐",
          not bad, f"불완전: {bad}" if bad else "ok")
    badcol = sorted({p.get("collection") for p in policy.values()
                     if isinstance(p, dict)} - _COLLECTION_VALUES)
    check("2d: collection 상태값이 정의된 집합 내(query/keyword/derived/unconfigured)",
          not badcol, f"미정의: {badcol}" if badcol else "ok")
    unsupported = [k for k, p in policy.items() if not p.get("supported")]
    noted = [k for k in unsupported if (policy[k].get("note") or "").strip()]
    check("2e: 미지원 렌즈는 note로 사유를 정직 표기",
          set(unsupported) == set(noted),
          f"사유 없는 미지원: {sorted(set(unsupported) - set(noted))}")
    check("2f: 미지원 렌즈는 collection=unconfigured로 표기",
          all(policy[k].get("collection") == "unconfigured" for k in unsupported),
          "미지원인데 unconfigured 아님" if any(
              policy[k].get("collection") != "unconfigured" for k in unsupported) else "ok")


# ---------------------------------------------------------------------------
# 3 · 렌즈별 정직 빈 상태 (일반 데모 문구 아님)
# ---------------------------------------------------------------------------

def check_empty_state(tpl: str) -> None:
    check("3a: 렌즈별 빈 상태 함수(emptyDescHtml) 존재", "function emptyDescHtml" in tpl)
    check("3b: 빈 상태가 lens_policy를 사용(렌즈별)", "MODEL.lens_policy" in tpl)
    check("3c: 빈 상태가 수집 쿼리를 노출", "수집 쿼리" in tpl)
    check("3d: 빈 상태가 live=금일 수집 결과 없음 / mock=데모 표본을 구분",
          "금일 수집 결과 없음" in tpl and "데모 표본" in tpl)
    check("3e: 빈 상태가 미연동(전용 수집 쿼리 미구성)을 별도 표기",
          "전용 수집 쿼리가 구성되지" in tpl and "collection" in tpl)
    check("3f: 빈 상태 desc에 렌즈별 주입 지점(newsEmptyDesc/aiEmptyDesc) 존재",
          'id="newsEmptyDesc"' in tpl and 'id="aiEmptyDesc"' in tpl)


# ---------------------------------------------------------------------------
# 4 · 핵심 렌즈 매핑(중앙 정책) + 약한 오탐 가드
# ---------------------------------------------------------------------------

def check_mapping_and_fp(builder: str) -> None:
    try:
        from app import lens_queries  # noqa: WPS433
        kw = {lens: set(words) for words, lens in lens_queries.keyword_lens_pairs()}
    except Exception:  # noqa: BLE001
        kw = {}
    for lens in CORE_MAPPED:
        check(f"4a: 중앙 정책이 '{lens}' 렌즈에 키워드 매핑 보유", bool(kw.get(lens)))
    # 약한 현대건설 오탐 가드가 존재하고 featured hero 선택에 사용됨
    check("4b: 약한 현대건설 오탐 가드(_is_weak_hdec_fp) 존재 + hero 선택에 사용",
          "_is_weak_hdec_fp" in builder and "hdec_strong" in builder)
    check("4c: 진짜 현대건설 판별이 '현대건설기계'를 배제(negative lookahead)",
          "현대건설(?!기계)" in builder)
    # 그룹사 사명(현대차)은 hyundai_group 렌즈로 매핑되고 별도 HDEC-직접 키가 없다
    check("4d: 범현대 사명이 hyundai_group 렌즈로 매핑(직접 렌즈 오염 아님)",
          "현대차" in (kw.get("hyundai_group") or set())
          and "현대건설" not in (kw.get("hyundai_group") or set()))
    # '시행'은 부분문자열 오탐(시행령) 위험 → '시행사'만 매핑 (config 키워드 기준)
    dev = kw.get("developers") or set()
    check("4e: 부분문자열 오탐 방지 — 바로 '시행'(시행령) 미매핑, '시행사'만",
          "시행" not in dev and "시행사" in dev)


# ---------------------------------------------------------------------------
# 5 · 필터 동작 유지 + 가짜 행 없음 + 생성 모델 정책/카운트 정직
# ---------------------------------------------------------------------------

def check_filters_and_rows(tpl: str) -> None:
    check("5a: 렌즈 필터 동작 유지(applyLens/filterPanel/selectLens)",
          "function applyLens" in tpl and "function filterPanel" in tpl
          and "function selectLens" in tpl)
    with tempfile.TemporaryDirectory(prefix="hdec_cov_") as tmp:
        out = Path(tmp) / "dashboard-latest.html"
        proc = subprocess.run([sys.executable, str(BUILDER), "--output", str(out)],
                              cwd=ROOT, capture_output=True, text=True, timeout=300)
        if not check("5b: builder --output 동작", proc.returncode == 0 and out.exists(),
                     (proc.stderr or "")[-200:]):
            return
        gen = out.read_text(encoding="utf-8")
        model = _model(gen)
        rows = (model.get("news_rows") or []) + (model.get("ai_rows") or [])
        check("5c: 모든 행이 실기사(title + http url) — 가짜 행 없음",
              bool(rows) and all(r.get("title") and str(r.get("url", "")).startswith("http")
                                 for r in rows), f"{len(rows)}행")
        # 생성 모델의 lens_policy가 중앙 정책에서 와 collection 상태를 담는다.
        gpol = model.get("lens_policy") or {}
        check("5d: 생성 모델 lens_policy가 collection 상태 포함",
              bool(gpol) and all("collection" in v for v in gpol.values()),
              f"{len(gpol)} 엔트리")
        # 핵심 렌즈가 전부 0이 아님(데이터 있음) — 개별 0은 빈 상태가 정직 설명.
        actual = {}
        for r in rows:
            for l in r.get("lens") or []:
                actual[l] = actual.get(l, 0) + 1
        core_total = sum(actual.get(k, 0) for k in CORE_MAPPED)
        check("5e: 핵심 비즈니스 렌즈가 전부 0이 아님(수집 신호 존재)",
              core_total > 0, f"core 합계 {core_total}")
        # featured 카드는 모델 행과 별도이므로, 0이어야 할 렌즈만 엄격 검사(stale 값 방지)
        navc = {}
        for line in gen.split("\n"):
            mm = re.search(r'data-filter="([^"]+)"', line)
            cm = re.search(r'<span class="ncount">(\d+)</span>', line)
            if mm and cm:
                navc[mm.group(1)] = int(cm.group(1))
        stale = [k for k, v in navc.items()
                 if k in CORE_MAPPED and actual.get(k, 0) == 0 and v != 0]
        check("5f: 빈 렌즈의 nav 카운트가 0 (정적 데모 stale 값 없음)",
              not stale, f"stale: {stale}" if stale else "ok")


def main() -> int:
    print(f"== verify_dashboard_lens_coverage @ {ROOT} ==")
    tpl = _read(TEMPLATE)
    builder = _read(BUILDER)
    collector = _read(COLLECTOR)
    if not check("0: 템플릿 + 빌더 + 수집기 존재",
                 bool(tpl) and len(tpl) > 4000 and bool(builder) and bool(collector)):
        print("\nRESULT: FAIL (소스 누락)")
        return 1
    check_central_policy(tpl)
    check_reuse(builder, collector)
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
    print("RESULT: PASS — 중앙 정책 기반 렌즈 커버리지 정직성 확인 (D7-E Part B)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
