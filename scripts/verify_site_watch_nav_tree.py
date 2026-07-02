#!/usr/bin/env python3
"""D7-AD-P verifier — '실행 범위' 현장 워치리스트 네비(개별 현장 표시/필터).

기존 D7-N site_watch_tree / onSiteNodeClick / article_keys 구조를 좌측 '실행 범위' 네비로
끌어올린 **표시·필터 레이어**를 검증한다. 핵심 계약:

  - 공개 빌드(env 미설정): 승인된 공개 현장 목록을 site_watch_tree로 노출한다.
    scope 그룹 + 개별 현장 노드 + match_count/article_keys 계약을 유지한다.
  - 운영자(비공개) 빌드(SITE_WATCHLIST_PATH + SITE_WATCHLIST_EXPOSE_TREE=1): scope별 전체 현장
    노출, 각 현장 고유 id·match_count·article_keys 유지, alias 매칭, match_count=0도 정직 표시.
    현장 클릭 필터는 기존 onSiteNodeClick(article_keys)를 그대로 재사용한다(코어 랭킹/점수 불변).
  - data/private는 계속 staged 금지이며, 공개 산출물은 data/site_watchlist.public.json 기반 현장명을 노출한다.

완전 OFFLINE · 시간 무관. 실제 비공개 파일/커밋 산출물은 있으면 검사, 없으면 SKIP(가짜 성공 금지).
기존 verify_site_watch_tree.py / verify_site_watchlist_sensing.py 계약은 깨지 않는다(앵커 재확인).
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MODULE = ROOT / "app" / "site_watchlist.py"
BUILDER = ROOT / "scripts" / "build_static_dashboard.py"
TEMPLATE = ROOT / "templates" / "dashboard_preview.html"
DASHBOARD = ROOT / "docs" / "daily" / "dashboard-latest.html"
PRIVATE_LOCAL = ROOT / "data" / "private" / "site_watchlist.local.json"
_REL_PRIVATE = "data/private/site_watchlist.local.json"

from app import site_watchlist as sw  # noqa: E402

# 운영자 실데이터 현재 시드의 scope 분포(사용자 확정값). 워치리스트를 갱신하면 함께 갱신한다.
EXPECTED_REAL = {"domestic_site": 57, "overseas_site": 53,
                 "overseas_branch": 23, "overseas_subsidiary": 2}
EXPECTED_REAL_TOTAL = 135

# 공개 산출물에 새면 안 되는 내부 고유 현장명 표본(verify_site_watchlist_sensing.py와 동일 의도).
INTERNAL_MARKERS = ["흑석9구역", "자푸라", "코즐로두이", "진해신항", "월곶~판교",
                    "마잔", "카르발라", "루사일 타워", "신반포 22차", "대조제1구역"]

_SECRET = "절대공개금지내부ZZX9현장"
_OPERATOR_ONLY_NAME = "둘째국내현장테스트"   # 어떤 mock 기사에도 안 나오는 이름 → 공개/비공개 대조용

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


def info(msg: str) -> None:
    print(f"[INFO] {msg}")


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


def _nodes(tree: dict) -> dict:
    out = {}
    for sc in (tree.get("by_scope") or {}).values():
        for g in sc.get("groups") or []:
            for n in g.get("nodes") or []:
                out[n["id"]] = n
    return out


def _git(*args) -> tuple[int, str]:
    try:
        p = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                           timeout=30)
        return p.returncode, (p.stdout or "").strip()
    except (OSError, subprocess.SubprocessError):
        return 127, ""


def _temp_watchlist() -> str:
    """저장소 밖 임시 비공개 워치리스트(이름/별칭/미매칭 대조 포함, 가짜 기사 아님)."""
    items = {"items": [
        # 이름으로 직접 매칭(공개 mock 기사 '…사우디 네옴 AI 데이터센터 EPC…')
        {"id": "neom_name", "name": "네옴 AI 데이터센터", "scope": "overseas_site",
         "business_lens": "new_energy", "tier": 1},
        # 별칭으로만 매칭(이름은 어떤 기사에도 없음 → alias match 동작 증명)
        {"id": "alias_only", "name": "알리아스전용현장테스트XYZ",
         "aliases": ["네옴 AI 데이터센터"], "scope": "overseas_site",
         "business_lens": "civil_infrastructure", "tier": 1},
        # 미매칭(비공개) — 공개 빌드 비노출, 운영자 빌드는 match_count=0 정직 표시
        {"id": "unmatched", "name": _SECRET, "scope": "domestic_site",
         "business_lens": "plant", "tier": 2},
        {"id": "dom2", "name": _OPERATOR_ONLY_NAME, "scope": "domestic_site",
         "business_lens": "civil_infrastructure", "tier": 2},
        {"id": "branch1", "name": "해외지사테스트", "scope": "overseas_branch",
         "org_unit": "해외영업", "tier": 2},
        {"id": "sub1", "name": "해외법인테스트Inc", "scope": "overseas_subsidiary",
         "org_unit": "해외법인", "tier": 2},
    ]}
    fd, path = tempfile.mkstemp(prefix="hdec_sw_nav_", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False)
    return path


_TEMP_COUNTS = {"domestic_site": 2, "overseas_site": 2,
                "overseas_branch": 1, "overseas_subsidiary": 1}


def _build(out: Path, env_extra=None):
    env = {**os.environ}
    env.pop("SITE_WATCHLIST_PATH", None)
    env.pop("SITE_WATCHLIST_EXPOSE_TREE", None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run([sys.executable, str(BUILDER), "--output", str(out)],
                          cwd=ROOT, capture_output=True, text=True, timeout=300, env=env)


# ---------------------------------------------------------------------------
# 1 · 모듈 scope_summary_for_model — 이름 비노출 집계
# ---------------------------------------------------------------------------

def check_module_summary() -> None:
    saved = os.environ.pop("SITE_WATCHLIST_PATH", None)
    try:
        s = sw.scope_summary_for_model()                 # env 미설정 = 공개 샘플
    finally:
        if saved is not None:
            os.environ["SITE_WATCHLIST_PATH"] = saved
    check("1a: 공개(샘플) 요약 is_private=False", s.get("is_private") is False, str(s.get("is_private")))
    check("1b: by_scope 4개 scope 키 + 정수 카운트",
          set(s.get("by_scope", {})) == set(sw.SCOPES)
          and all(isinstance(v, int) for v in s["by_scope"].values()), str(s.get("by_scope")))
    check("1c: scope_labels 4개(국내/해외 현장·지사·법인)",
          set(s.get("scope_labels", {})) == set(sw.SCOPES), str(s.get("scope_labels")))

    path = _temp_watchlist()
    try:
        sp = sw.scope_summary_for_model(path)
    finally:
        os.unlink(path)
    check("1d: 비공개 주입 요약 is_private=True + total 정확",
          sp.get("is_private") is True and sp.get("total") == 6, f"total={sp.get('total')}")
    check("1e: 비공개 scope 카운트 정확", sp.get("by_scope") == _TEMP_COUNTS, str(sp.get("by_scope")))
    dumped = json.dumps(sp, ensure_ascii=False)
    leaked = [m for m in (_SECRET, _OPERATOR_ONLY_NAME, "네옴 AI 데이터센터", "해외지사테스트")
              if m in dumped]
    check("1f: 요약 dict에 현장명/별칭 0건(이름 비노출 — 카운트만)", not leaked,
          f"누출: {leaked}" if leaked else "ok")


# ---------------------------------------------------------------------------
# 2 · 템플릿 DOM/JS — 네비 렌더 + 기존 구조 재사용 + 공개 현장 트리 게이트
# ---------------------------------------------------------------------------

def check_template() -> None:
    t = _read(TEMPLATE)
    check("2a: 네비 컨테이너 DOM(siteScopeNav)", 'id="siteScopeNav"' in t)
    for fn in ("function renderSiteScopeNav", "function renderScopeNodes",
               "function refreshSiteNavActive", "function filterSiteNav"):
        check(f"2b: 함수 정의 '{fn}'", fn in t)
    check("2c: 현장 클릭이 기존 onSiteNodeClick 재사용",
          'onSiteNodeClick(node.getAttribute("data-scope")' in t)
    check("2d: 네비가 renderScopeNodes→renderSiteNode(기존 노드 마크업) 재사용",
          "renderScopeNodes(sc, data)" in t and "out += renderSiteNode(scope, n)" in t)
    check("2e: 이름 비노출 카운트 소스(MODEL.site_scope_summary)",
          "MODEL.site_scope_summary || null" in t)
    check("2f: 공개 현장 트리 게이트 — SITE_TREE가 있으면 공개 현장명 렌더",
          "var hasTree = !!SITE_TREE" in t
          and "if (!hasTree)" in t
          and "var priv = !!SITE_TREE" not in t)
    check("2g: 검색 입력(snSearch)", 'id="snSearch"' in t)
    check("2h: match_count=0 현장 흐리게(zero 클래스)",
          'Number(n.match_count || 0)' in t and 'Number(n.match_count) ? "" : " zero"' in t
          and ".st-node.zero" in t)
    check("2i: 네비 그룹은 data-sn-scope 사용(렌즈 data-filter 미사용 — 카운트/파티션 로직 비충돌)",
          "data-sn-scope" in t)
    check("2j: 부팅 시 renderSiteScopeNav() 호출", "renderSiteScopeNav();" in t)
    # 기존 D7-N 계약 앵커 보존(회귀 가드)
    for anchor in ('id="siteTreePanel"', 'id="siteFilterBar"', "function showSiteTreeForLens",
                   "function renderSiteTreeHtml", "function renderSiteNode",
                   "function onSiteNodeClick", "MODEL.site_watch_tree || null",
                   "if (!SITE_TREE || !isSiteScope(key))"):
        check(f"2k: 기존 D7-N 앵커 유지 '{anchor}'", anchor in t)


# ---------------------------------------------------------------------------
# 3 · 빌더 wiring — 집계 주입 + os.environ 미사용(D7-N 4d 보존)
# ---------------------------------------------------------------------------

def check_builder() -> None:
    src = _read(BUILDER)
    check("3a: 빌더가 site_scope_summary 주입(scope_summary_for_model)",
          'model["site_scope_summary"] = site_watchlist.scope_summary_for_model()' in src)
    check("3b: 빌더 os.environ/‘import os’ 미사용(리프가 게이팅 — 4d 보존)",
          "os.environ" not in src
          and not re.search(r"^\s*import os\b", src, re.M))


# ---------------------------------------------------------------------------
# 4 · 통합 빌드 PUBLIC(env 미설정) — 카운트만, 트리/이름 비노출
# ---------------------------------------------------------------------------

def check_public_build() -> None:
    with tempfile.TemporaryDirectory(prefix="hdec_sw_nav_pub_") as tmp:
        out = Path(tmp) / "dash.html"
        proc = _build(out)
        if not check("4a: 공개 빌드 동작(env 미설정)", proc.returncode == 0 and out.exists(),
                     (proc.stderr or "")[-200:]):
            return
        html = out.read_text(encoding="utf-8")
        model = _model(html)
        ss = model.get("site_scope_summary") or {}
        check("4b: 공개 모델에 site_scope_summary 주입(is_private=False)",
              bool(ss) and ss.get("is_private") is False, str(ss.get("is_private")))
        tree = model.get("site_watch_tree")
        if not check("4c: 공개 모델에 site_watch_tree 주입(D7-AD-Z)", isinstance(tree, dict)):
            return
        nodes = _nodes(tree)
        check("4d: 공개 HTML에 네비 DOM(siteScopeNav) 존재", 'id="siteScopeNav"' in html)
        check("4e: 공개 모델에 현장 노드 노출", len(nodes) > 0, f"{len(nodes)}개")
        check("4f: 공개 트리는 전체 노드 노출(expose_full_tree=True)",
              tree.get("expose_full_tree") is True, str(tree.get("expose_full_tree")))

# ---------------------------------------------------------------------------
# 5 · 통합 빌드 OPERATOR(비공개 + EXPOSE_TREE=1) — 전체 현장 + 고유키/카운트/alias/0건
# ---------------------------------------------------------------------------

def check_operator_build() -> None:
    path = _temp_watchlist()
    try:
        with tempfile.TemporaryDirectory(prefix="hdec_sw_nav_op_") as tmp:
            out = Path(tmp) / "dash.html"
            proc = _build(out, {"SITE_WATCHLIST_PATH": path,
                                "SITE_WATCHLIST_EXPOSE_TREE": "1"})
            if not check("5a: 운영자 빌드 동작(비공개 + EXPOSE_TREE=1)",
                         proc.returncode == 0 and out.exists(), (proc.stderr or "")[-200:]):
                return
            html = out.read_text(encoding="utf-8")
            model = _model(html)
            tree = model.get("site_watch_tree")
            if not check("5b: 운영자 모델에 site_watch_tree 주입", tree is not None):
                return
            nodes = _nodes(tree)
            check("5c: EXPOSE_TREE → 전체 6개 현장 노드 노출(미매칭 포함)",
                  len(nodes) == 6, f"{len(nodes)}개")
            check("5d: 각 노드 고유 id + match_count(int) + article_keys(list) 유지",
                  all(n.get("id") and isinstance(n.get("match_count"), int)
                      and isinstance(n.get("article_keys"), list) for n in nodes.values()))
            check("5e: 이름 매칭 현장(neom_name) match_count>0",
                  (nodes.get("neom_name") or {}).get("match_count", 0) > 0,
                  f"mc={(nodes.get('neom_name') or {}).get('match_count')}")
            check("5f: 별칭 매칭 현장(alias_only) match_count>0 (alias match 동작)",
                  (nodes.get("alias_only") or {}).get("match_count", 0) > 0,
                  f"mc={(nodes.get('alias_only') or {}).get('match_count')}")
            check("5g: 미매칭 현장(unmatched) match_count=0 정직 표시(노드는 존재)",
                  "unmatched" in nodes and nodes["unmatched"].get("match_count") == 0)
            ss = model.get("site_scope_summary") or {}
            check("5h: 운영자 site_scope_summary is_private=True + scope 카운트 정확",
                  ss.get("is_private") is True and ss.get("by_scope") == _TEMP_COUNTS,
                  str(ss.get("by_scope")))
            check("5i: 운영자 빌드는 현장명 노출(미매칭 이름도 트리에 — 운영자 로컬 전용)",
                  _OPERATOR_ONLY_NAME in html)
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# 6 · 커밋 산출물(docs/daily) — 공개·이름 비노출 + 빌더 생성(hand-edit 아님)
# ---------------------------------------------------------------------------

def check_committed_artifact() -> None:
    html = _read(DASHBOARD)
    if not html:
        info(f"SKIP — 커밋 대시보드 없음({DASHBOARD.name}). 빌드 후 재검.")
        return
    model = _model(html)
    check("6a: 커밋 대시보드에 네비 DOM(siteScopeNav)", 'id="siteScopeNav"' in html)
    tree = model.get("site_watch_tree")
    if not check("6b: 커밋 대시보드 공개 site_watch_tree 데이터 있음(D7-AD-Z)",
                 isinstance(tree, dict)):
        return
    nodes = _nodes(tree)
    check("6c: 커밋 대시보드 공개 현장 노드 노출", len(nodes) > 0, f"{len(nodes)}개")
    ss = model.get("site_scope_summary") or {}
    check("6d: 커밋 대시보드 site_scope_summary는 공개(is_private=False)",
          ss.get("is_private") is False, str(ss.get("is_private")))
    check("6e: 커밋 대시보드는 빌더 생성물(hand-edit 아님 — export 마커)",
          "source=templates/dashboard_preview.html" in html)

# ---------------------------------------------------------------------------
# 7 · 비공개 목록 stage 금지(gitignore + not staged)
# ---------------------------------------------------------------------------

def check_not_staged() -> None:
    rc, out = _git("check-ignore", _REL_PRIVATE)
    if rc == 127:
        gi = _read(ROOT / ".gitignore")
        check("7a: 비공개 local.json gitignore 규칙(폴백)", "data/private/*" in gi)
    else:
        check("7a: 비공개 local.json gitignored", rc == 0 and out.endswith("site_watchlist.local.json"),
              out or "매칭 없음")
    _, staged = _git("diff", "--cached", "--name-only")
    check("7b: 비공개 local.json staged 아님", _REL_PRIVATE not in (staged or ""))
    check("7c: docs/daily 가 staged 목록에 hand-edit로 포함되지 않음(빌더 산출만 허용)",
          True if "docs/daily" not in (staged or "")
          else "source=templates/dashboard_preview.html" in _read(DASHBOARD),
          "staged docs 없음" if "docs/daily" not in (staged or "") else "staged docs는 빌더 산출")


# ---------------------------------------------------------------------------
# 8 · 실데이터 scope 분포(있으면 검사) — 사용자 확정값
# ---------------------------------------------------------------------------

def check_real_counts() -> None:
    if not PRIVATE_LOCAL.exists():
        info("SKIP — 실 비공개 워치리스트 없음. SITE_WATCHLIST_PATH 시드 후 재검(57/53/23/2 분포).")
        return
    s = sw.scope_summary_for_model(str(PRIVATE_LOCAL))   # 이름 비노출(카운트만)
    check("8a: 실데이터 total=135", s.get("total") == EXPECTED_REAL_TOTAL, f"total={s.get('total')}")
    check("8b: 실데이터 scope 분포(국내57/해외53/지사23/법인2)",
          s.get("by_scope") == EXPECTED_REAL, str(s.get("by_scope")))
    dumped = json.dumps(s, ensure_ascii=False)
    leaked = [m for m in INTERNAL_MARKERS if m in dumped]
    check("8c: 실데이터 요약 dict에도 현장명 0건(카운트만)", not leaked, str(leaked) if leaked else "ok")


def main() -> int:
    print(f"== verify_site_watch_nav_tree (D7-AD-P) @ {ROOT} ==")
    check_module_summary()
    check_template()
    check_builder()
    check_public_build()
    check_operator_build()
    check_committed_artifact()
    check_not_staged()
    check_real_counts()
    print()
    if _failures:
        print(f"RESULT: FAIL ({len(_failures)} 항목)")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("RESULT: PASS — 실행 범위 현장 워치리스트 네비(개별 현장 표시/필터) public-safe (D7-AD-P)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
