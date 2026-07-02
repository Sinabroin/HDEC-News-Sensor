#!/usr/bin/env python3
"""D7-M verifier — 비공개 사이트 워치리스트 센싱 + 토목 starvation 수정.

사용자 QA: (1) 토목이 1건뿐인 것은 말이 안 됨, (2) 각 렌즈가 정말 수집되는지 의심,
(3) 내부 조직도/현장/지사/법인 이름으로 센싱 필요. 단, 내부 목록은 공개 저장소/공개 Pages
대시보드에 절대 노출 금지.

이 verifier는 가능한 한 OFFLINE이고, LIVE는 SKIP-friendly다:

  STATIC / 모듈 단위(항상, 네트워크 0건):
    1. data/private/site_watchlist.local.json 은 gitignored + not staged.
    2. site_watchlist.py 는 SITE_WATCHLIST_PATH 미설정 시 비공개 파일을 읽지 않는다(샘플만).
    3. 공개 샘플은 존재하되 내부 스크린샷 전체 목록을 담지 않는다(소량 + 비공개 마커 없음).
    4. 임시 비공개 워치리스트 주입 시: collection_query_groups가 bounded site 그룹을 반환,
       선택 쿼리에 civil/국내/해외 예시 포함, 총 쿼리 수가 max_queries로 상한.
    5. live_collector는 SITE_WATCHLIST_PATH가 있을 때만 site 그룹을 넣는다(env 게이팅).
    6. build_static_dashboard 분류가 매칭된 프로젝트명에 civil/국내/해외 렌즈 태그를 더한다.
    7. 매칭 안 된 비공개 항목은 생성 대시보드 모델/HTML에 덤프되지 않는다.
    8. civil_infrastructure 가 우선순위 preflight(또는 항상 audit되는 경로)에 있다.
   10. 가짜 URL/카운트 없음(site 그룹 쿼리에 http/example 없음, site_watch 행은 실 URL).

  LIVE(NEWS_MODE=live + SITE_WATCHLIST_PATH 설정 + 네트워크 가능 시에만):
    9. 실제 fetch_all() query_audit 에 site:* 그룹이 등장한다(빈 결과여도 가시).
"""

import ast
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

COLLECTOR = ROOT / "app" / "live_collector.py"
BUILDER = ROOT / "scripts" / "build_static_dashboard.py"
MODULE = ROOT / "app" / "site_watchlist.py"
SAMPLE = ROOT / "data" / "site_watchlist.sample.json"
PRIVATE_LOCAL = ROOT / "data" / "private" / "site_watchlist.local.json"
DASHBOARD = ROOT / "docs" / "daily" / "dashboard-latest.html"

from app import live_collector as lc  # noqa: E402
from app import site_watchlist as sw  # noqa: E402

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


def _git(*args) -> tuple[int, str]:
    try:
        p = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                           timeout=30)
        return p.returncode, (p.stdout or "").strip()
    except (OSError, subprocess.SubprocessError):
        return 127, ""


_REL_PRIVATE = "data/private/site_watchlist.local.json"


def _temp_private_watchlist() -> str:
    """/tmp(저장소 밖)에 작은 비공개 워치리스트를 만들어 경로를 반환한다(가짜 기사 아님)."""
    items = {
        "items": [
            {"id": "t_dom_civil", "name": "테스트 진해신항 남방파제 공사",
             "aliases": ["진해신항 테스트"], "scope": "domestic_site",
             "business_lens": "civil_infrastructure", "tier": 1},
            {"id": "t_dom_bld", "name": "테스트 ICN062 데이터센터",
             "aliases": ["ICN062 테스트"], "scope": "domestic_site",
             "business_lens": "building_housing", "tier": 1},
            {"id": "t_ovs_civil", "name": "테스트 사우디 네옴 러닝터널",
             "aliases": ["네옴 테스트터널"], "scope": "overseas_site",
             "business_lens": "civil_infrastructure", "tier": 1},
            {"id": "t_ovs_sub", "name": "테스트 Hyundai America Inc.",
             "aliases": [], "scope": "overseas_subsidiary", "tier": 1},
            {"id": "t_unmatched", "name": "절대공개금지내부프로젝트ZZX9",
             "aliases": [], "scope": "domestic_site", "business_lens": "plant",
             "tier": 2},
        ]
    }
    fd, path = tempfile.mkstemp(prefix="hdec_sw_verify_", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False)
    return path


# ---------------------------------------------------------------------------
# 1 · gitignore + not staged
# ---------------------------------------------------------------------------

def check_gitignore() -> None:
    rc, out = _git("check-ignore", _REL_PRIVATE)
    if rc == 127:
        gi = _read(ROOT / ".gitignore")
        check("1: 비공개 local.json gitignore 규칙 존재(.gitignore 파싱 폴백)",
              "data/private/*" in gi, "git 미사용 — .gitignore 규칙 확인")
    else:
        check("1a: data/private/site_watchlist.local.json 은 gitignored",
              rc == 0 and out.endswith("site_watchlist.local.json"),
              out or "check-ignore 매칭 없음")
    rc2, tracked = _git("ls-files", _REL_PRIVATE)
    check("1b: 비공개 local.json 은 추적되지 않음(not tracked)", not tracked.strip(),
          tracked or "추적 안 됨")
    rc3, staged = _git("diff", "--cached", "--name-only")
    check("1c: 비공개 local.json 은 staged 아님",
          _REL_PRIVATE not in (staged or ""), "staged에 없음")
    # .gitkeep / *.example.json 은 추적 가능해야 한다(템플릿 제공).
    rc4, _ = _git("check-ignore", "data/private/.gitkeep")
    check("1d: data/private/.gitkeep 은 무시되지 않음(추적 가능)", rc4 != 0)


# ---------------------------------------------------------------------------
# 2 · no private load without env
# ---------------------------------------------------------------------------

def check_no_private_without_env() -> None:
    saved = os.environ.pop("SITE_WATCHLIST_PATH", None)
    try:
        wl = sw.load_watchlist()
        # D7-AD-Z/D7-AE 계약 갱신: 기본 소스는 추적 공개 목록(public) — 없을 때만 sample 폴백.
        # 핵심 프라이버시 계약은 유지: env 미설정 시 data/private/* 를 절대 읽지 않는다.
        norm_path = str(wl["path"]).replace("\\", "/")
        ok = (wl["is_private"] is False and wl["source"] in ("public", "sample")
              and "/private/" not in norm_path)
        check("2a: env 미설정 시 비공개 파일 미로드(추적 공개 목록/샘플만 · D7-AE 갱신)", ok,
              f"source={wl['source']} path={Path(wl['path']).name}")
        # D7-AE 계약 갱신: 공개 목록도 수집에 참여한다(이미 커밋·공개 렌더된 이름 = 신규 노출 0).
        # 프라이버시 계약은 '쿼리가 추적 공개 목록에서만 파생'으로 검증한다 — 비공개 이름 0건.
        groups = sw.collection_query_groups(rotation_key=0)
        if wl["source"] == "public":
            allowed = {q.strip().casefold() for it in wl["items"]
                       for q in sw._queries_for(it)}
            got = [q for g in groups for q in (g.get("queries") or [])]
            foreign = [q for q in got if q.strip().casefold() not in allowed]
            check("2b: env 미설정 site 그룹은 추적 공개 목록 파생만(bounded · D7-AE 갱신)",
                  bool(groups) and not foreign and len(got) <= sw.MAX_QUERIES_HARD_CAP,
                  f"{len(got)}개 쿼리, 공개 목록 밖: {foreign[:2]}")
        else:
            check("2b: 공개 목록 없음(샘플 폴백) → site 수집 그룹 0개(no-op)",
                  groups == [])
    finally:
        if saved is not None:
            os.environ["SITE_WATCHLIST_PATH"] = saved
    # 소스에 env 키 게이팅이 박혀 있어야 한다.
    msrc = _read(MODULE)
    check("2c: 모듈이 SITE_WATCHLIST_PATH env로 비공개를 게이팅",
          "SITE_WATCHLIST_PATH" in msrc and "is_private" in msrc)


# ---------------------------------------------------------------------------
# 3 · sample exists, not the full internal list
# ---------------------------------------------------------------------------

def check_sample_safe() -> None:
    raw = _read(SAMPLE)
    if not check("3a: 공개 샘플 파일 존재", bool(raw)):
        return
    try:
        data = json.loads(raw)
    except ValueError:
        check("3a: 공개 샘플 JSON 파싱", False)
        return
    items = data.get("items") or []
    # 내부 전체 목록은 100건 이상 — 샘플은 소량이어야 한다(전체 덤프 금지).
    check("3b: 샘플은 소량(<=15) — 내부 전체 목록 아님", 1 <= len(items) <= 15,
          f"{len(items)}건")
    # 내부 고유 프로젝트명(스크린샷 전용)이 샘플에 광범위하게 들어있지 않아야 한다.
    internal_markers = ["흑석9구역", "자푸라", "코즐로두이", "진해신항", "월곶~판교",
                        "마잔", "카르발라", "루사일 타워", "신반포 22차", "대조제1구역"]
    leaked = [m for m in internal_markers if m in raw]
    check("3c: 샘플에 내부 고유 현장명 미포함(전체 목록 누출 없음)", not leaked,
          f"누출 의심: {leaked}" if leaked else "ok")
    # 샘플 항목은 public_safe 마커를 단다(공개 안전 의도 명시).
    check("3d: 샘플 항목은 public_safe=true",
          all(it.get("public_safe") is True for it in items))


# ---------------------------------------------------------------------------
# 4 · temp private → bounded site groups w/ examples
# ---------------------------------------------------------------------------

def check_bounded_groups() -> None:
    path = _temp_private_watchlist()
    try:
        groups = sw.collection_query_groups(path=path, rotation_key=0)
        check("4a: 비공개 주입 시 site 그룹 반환(>0)", bool(groups), f"{len(groups)} 그룹")
        names = [g["name"] for g in groups]
        check("4b: 모든 그룹명이 'site:' 접두", all(n.startswith("site:") for n in names),
              str(names))
        total_q = sum(len(g["queries"]) for g in groups)
        check("4c: 총 site 쿼리 수가 기본 상한(40) 이하로 bounded", total_q <= 40,
              f"{total_q}개")
        # 더 작은 상한도 존중하는지 — 결정적 bound 확인.
        capped = sw.collection_query_groups(path=path, max_queries=4, rotation_key=0)
        cq = sum(len(g["queries"]) for g in capped)
        check("4d: max_queries=4 상한 존중", cq <= 4, f"{cq}개")
        blob = " ".join(q for g in groups for q in g["queries"])
        check("4e: 선택 쿼리에 국내 토목 예시 포함", "진해신항" in blob)
        check("4f: 선택 쿼리에 해외 예시(Hyundai E&C) 포함", "Hyundai E&C" in blob)
        scopes = {g["scope"] for g in groups}
        check("4g: 국내·해외 scope 모두 등장(breadth)",
              "domestic_site" in scopes and any(s.startswith("overseas") for s in scopes),
              str(sorted(scopes)))
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# 5 · live_collector gates site groups on env
# ---------------------------------------------------------------------------

def check_collector_gating() -> None:
    src = _read(COLLECTOR)
    check("5a: live_collector가 site_watchlist 사용",
          "site_watchlist" in src and "collection_query_groups()" in src)
    check("5b: site 그룹은 기본 소스(sources_path is None)에서만 합쳐진다",
          "sources_path is None" in src and "site_groups" in src)
    # 모듈 게이팅이 곧 수집기 게이팅(D7-AE 갱신): env 없으면 추적 공개 목록에서만 파생 —
    # 비공개 이름이 쿼리에 들어갈 경로가 없다(공개 목록 없으면 0개 no-op).
    saved = os.environ.pop("SITE_WATCHLIST_PATH", None)
    try:
        wl = sw.load_watchlist()
        groups = sw.collection_query_groups(rotation_key=0)
        if wl["source"] == "public":
            allowed = {q.strip().casefold() for it in wl["items"]
                       for q in sw._queries_for(it)}
            foreign = [q for g in groups for q in (g.get("queries") or [])
                       if q.strip().casefold() not in allowed]
            check("5c: env 미설정 → site 쿼리는 공개 목록 파생만(비공개 0건 · D7-AE 갱신)",
                  not foreign, str(foreign[:2]))
        else:
            check("5c: env 미설정+공개 목록 없음 → collection_query_groups()=[]",
                  groups == [])
    finally:
        if saved is not None:
            os.environ["SITE_WATCHLIST_PATH"] = saved
    # custom sources_path 경로는 site/preflight를 타지 않는다(계약 보존).
    check("5d: site preflight는 pass_label='site'로 audit 라벨링",
          'pass_label="site"' in src)


# ---------------------------------------------------------------------------
# 6 · builder classification adds lens tags for matched names
# ---------------------------------------------------------------------------

def check_classification() -> None:
    import importlib
    b = importlib.import_module("build_static_dashboard")
    path = _temp_private_watchlist()
    saved = os.environ.get("SITE_WATCHLIST_PATH")
    os.environ["SITE_WATCHLIST_PATH"] = path
    try:
        sig = {"title": "현대건설, 테스트 진해신항 남방파제 공사 본격 착수",
               "url": "https://news.example.test/a1"}
        lens = b._lens_for(sig)
        check("6a: 매칭 제목에 civil_infrastructure 렌즈 태그",
              "civil_infrastructure" in lens, str(lens))
        check("6b: 매칭 제목에 domestic_site 렌즈 태그", "domestic_site" in lens)
        row = b._row_from_signal(sig)
        prov = row.get("provenance") or {}
        check("6c: 매칭 행에 site_watch provenance 부착",
              prov.get("site_watch_match") is True
              and prov.get("site_watch_scope") == "domestic_site"
              and prov.get("site_watch_business_lens") == "civil_infrastructure")
        sig2 = {"title": "오늘 날씨와 무관한 일반 뉴스", "url": "https://news.example.test/a2"}
        prov2 = (b._row_from_signal(sig2).get("provenance") or {})
        check("6d: 비매칭 행에는 site_watch provenance 없음",
              "site_watch_match" not in prov2)
    finally:
        if saved is None:
            os.environ.pop("SITE_WATCHLIST_PATH", None)
        else:
            os.environ["SITE_WATCHLIST_PATH"] = saved
        os.unlink(path)


# ---------------------------------------------------------------------------
# 7 · unmatched private entries not dumped into model/HTML
# ---------------------------------------------------------------------------

def check_no_private_dump() -> None:
    path = _temp_private_watchlist()
    saved = os.environ.get("SITE_WATCHLIST_PATH")
    os.environ["SITE_WATCHLIST_PATH"] = path
    secret = "절대공개금지내부프로젝트ZZX9"
    try:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "dash.html"
            proc = subprocess.run([sys.executable, str(BUILDER), "--output", str(out)],
                                  cwd=ROOT, capture_output=True, text=True, timeout=300,
                                  env={**os.environ})
            if not check("7a: 빌더 동작(비공개 env 주입)", proc.returncode == 0 and out.exists(),
                         (proc.stderr or "")[-200:]):
                return
            html = out.read_text(encoding="utf-8")
            check("7b: 매칭 안 된 비공개 항목명이 HTML에 덤프되지 않음",
                  secret not in html, "비공개 항목명 누출!" if secret in html else "ok")
            # 매칭 안 된 항목의 다른 이름들도 누출되면 안 된다(mock 기사엔 없음).
            check("7c: 매칭 안 된 비공개 항목명(테스트 진해신항)도 mock HTML에 없음",
                  "테스트 진해신항" not in html)
    finally:
        if saved is None:
            os.environ.pop("SITE_WATCHLIST_PATH", None)
        else:
            os.environ["SITE_WATCHLIST_PATH"] = saved
        os.unlink(path)


# ---------------------------------------------------------------------------
# effective_cap 계약 헬퍼 (D7-AD-Q) — 문자열 매칭 대신 '의미'를 검증한다.
#   live_collector.fetch_all 의 effective_cap 대입식을 AST로 추출해, mock 입력으로
#   실제 평가한다. 리팩터(줄바꿈/괄호/항 추가)에 견고하며 계약의 뜻을 직접 확인한다.
# ---------------------------------------------------------------------------

def _effective_cap_rhs(src: str):
    """live_collector 소스에서 effective_cap 대입문의 우변 AST 노드를 반환한다(없으면 None)."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None
    rhs = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "effective_cap" for t in node.targets
        ):
            rhs = node.value  # 정상 경로의 대입식(단일)
    return rhs


def _calls_len_on(node, arg_name: str) -> bool:
    """node 하위 트리에 len(<arg_name>) 호출이 있으면 True."""
    return any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        and n.func.id == "len"
        and any(isinstance(a, ast.Name) and a.id == arg_name for a in n.args)
        for n in ast.walk(node)
    )


def _refs_name(node, name: str) -> bool:
    return any(isinstance(n, ast.Name) and n.id == name for n in ast.walk(node))


def _eval_effective_cap(rhs, *, n_sites: int, has_press: bool) -> int:
    """소스에서 뽑은 effective_cap 우변식을 mock 입력으로 평가한다(순수 산술, 부작용 없음).

    max_total 은 budget/floor 와 겹치지 않는 sentinel(1000)로 두어, 'site 0개면 total 불변'
    같은 불변식을 우연한 값 일치 없이 확인한다. 실제 예산 상수는 모듈에서 그대로 읽는다.
    """
    ns = {
        "max_total": 1000,
        "site_groups": list(range(n_sites)),
        "global_press_group": (object() if has_press else None),
        "SITE_PREFLIGHT_GROUP_BUDGET": getattr(lc, "SITE_PREFLIGHT_GROUP_BUDGET", 0),
        "GLOBAL_PRESS_GROUP_BUDGET": getattr(lc, "GLOBAL_PRESS_GROUP_BUDGET", 0),
        "len": len,
    }
    expr = ast.Expression(body=rhs)
    ast.fix_missing_locations(expr)
    # 평가 대상은 저장소 자신의 산술식뿐 — builtins를 비워 sandbox 한다.
    return int(eval(compile(expr, "<effective_cap>", "eval"),  # noqa: S307
                    {"__builtins__": {}}, ns))


# ---------------------------------------------------------------------------
# 8 · civil_infrastructure always audited (priority/preflight)
# ---------------------------------------------------------------------------

def check_civil_priority() -> None:
    pri = set(getattr(lc, "PRIORITY_LENS_GROUPS", ()) or ())
    check("8a: lens:civil_infrastructure 가 우선순위 preflight에 포함",
          "lens:civil_infrastructure" in pri, f"priority={sorted(pri)}")
    src = _read(COLLECTOR)
    # 8b (D7-AD-Q) — 토목/site 수집이 normal 렌즈 수집 예산을 잠식하지 않음을 '의미'로 검증한다.
    #   공개 빌드 total 은 floor 로 고정되고 site preflight 만 additive(private 전용)다. 그래야
    #   다른 렌즈의 수집 깊이 게이트(verify_lens_search_depth)를 회귀시키지 않는다. 단순 문자열
    #   매칭 대신, live_collector 의 effective_cap 대입식을 AST 로 뽑아 mock 입력으로 평가한다.
    rhs = _effective_cap_rhs(src)
    budget = getattr(lc, "SITE_PREFLIGHT_GROUP_BUDGET", None)
    structural = rhs is not None and (
        _refs_name(rhs, "max_total")
        and _calls_len_on(rhs, "site_groups")
        and _refs_name(rhs, "SITE_PREFLIGHT_GROUP_BUDGET"))
    check("8b: effective_cap 대입식이 max_total·len(site_groups)·"
          "SITE_PREFLIGHT_GROUP_BUDGET 을 반영(AST 의미 검증)",
          structural,
          "effective_cap 대입 미발견" if rhs is None else "ok")
    ok_budget = isinstance(budget, int) and budget > 0
    if structural and ok_budget:
        try:
            pub = _eval_effective_cap(rhs, n_sites=0, has_press=False)
            add = _eval_effective_cap(rhs, n_sites=3, has_press=False)
            eval_err = None
        except Exception as exc:  # noqa: BLE001 — 미지의 항 추가 등은 실패로 보고(가짜 성공 금지)
            pub = add = None
            eval_err = f"{type(exc).__name__}: {exc}"
        expected = 1000 + 3 * budget
        check("8b-public: 공개 빌드(site 그룹 0개) → effective_cap == max_total "
              "(normal 렌즈 수집 예산 불변)",
              eval_err is None and pub == 1000,
              eval_err or f"effective_cap={pub} (max_total sentinel=1000)")
        check("8b-additive: 비공개 site 3개 → effective_cap == "
              "max_total + 3×SITE_PREFLIGHT_GROUP_BUDGET (site preflight만 additive)",
              eval_err is None and add == expected,
              eval_err or f"effective_cap={add} (expected={expected})")
    else:
        check("8b-eval: effective_cap 의미 평가 전제(AST 구조 + 양의 budget) 충족",
              False, f"structural={structural}, SITE_PREFLIGHT_GROUP_BUDGET={budget}")
    floor = getattr(lc, "DASHBOARD_MIN_MAX_TOTAL", 0)
    check("8b-floor: DASHBOARD_MIN_MAX_TOTAL >= 100 계약 유지(공개 빌드 floor)",
          floor >= 100, f"floor={floor}")
    # lens_queries.json 의 civil collect 쿼리가 강화됐는지(프로젝트 패밀리 신호).
    try:
        pol = json.loads(_read(ROOT / "data" / "lens_queries.json")).get("lenses") or {}
    except ValueError:
        pol = {}
    civ = pol.get("civil_infrastructure") or {}
    collect = [q for q in (civ.get("collect") or []) if isinstance(q, str)]
    check("8c: civil collect 쿼리 강화(>=12)", len(collect) >= 12, f"{len(collect)}개")
    kws = civ.get("keywords") or []
    check("8d: civil 키워드에 항만/해상풍력/고속국도 등 패밀리 신호 추가",
          {"항만", "해상풍력", "고속국도"} <= set(kws))


# ---------------------------------------------------------------------------
# 10 · no fake URLs / counts
# ---------------------------------------------------------------------------

def check_no_fake() -> None:
    path = _temp_private_watchlist()
    try:
        groups = sw.collection_query_groups(path=path, rotation_key=0)
        bad = [q for g in groups for q in g["queries"]
               if "http" in q.lower() or "example.com" in q.lower()]
        check("10a: site 그룹 쿼리에 가짜 URL 없음(쿼리 문자열만)", not bad, str(bad[:3]))
    finally:
        os.unlink(path)
    # 커밋 대시보드에 site_watch 태그가 붙은 행이 있으면 실 http URL이어야 한다.
    model = _model(_read(DASHBOARD))
    banks = model.get("lens_banks") or {}
    fake = []
    sw_rows = 0
    for rows in banks.values():
        for r in rows or []:
            if (r.get("provenance") or {}).get("site_watch_match"):
                sw_rows += 1
                u = str(r.get("url") or "")
                if not u.startswith(("http://", "https://")) or "example.com" in u:
                    fake.append((r.get("title") or "")[:32])
    check("10b: 커밋 대시보드 site_watch 행은 실 http URL(가짜 없음)", not fake,
          f"site_watch 행 {sw_rows}건, fake={fake[:3]}" if fake else f"site_watch 행 {sw_rows}건")


# ---------------------------------------------------------------------------
# 9 · LIVE — site:* groups in audit when env set (SKIP-friendly)
# ---------------------------------------------------------------------------

def check_live_site_audit() -> None:
    if os.environ.get("NEWS_MODE") != "live":
        info("LIVE 검사 SKIP — NEWS_MODE=live 아님. SITE_WATCHLIST_PATH + NEWS_MODE=live로 "
             "재실행해 site:* audit 커버리지를 확인한다.")
        return
    if not os.environ.get("SITE_WATCHLIST_PATH"):
        info("LIVE 검사 SKIP — SITE_WATCHLIST_PATH 미설정(공개 빌드는 site 그룹 없음이 정상).")
        return
    audit = []
    try:
        rows = lc.fetch_all(query_audit=audit)
    except Exception as exc:  # noqa: BLE001
        info(f"LIVE fetch 불가(네트워크?) — {type(exc).__name__} · SKIP(가짜 성공 금지)")
        return
    site_groups = sorted({a.get("group") for a in audit
                          if str(a.get("group") or "").startswith("site:")})
    check("9: SITE_WATCHLIST_PATH 설정 live 실행에 site:* audit 그룹 등장",
          bool(site_groups), f"{len(site_groups)}개: {site_groups}")
    info(f"LIVE 수집 {len(rows)}행 · site 그룹 {len(site_groups)}개")


def main() -> int:
    print(f"== verify_site_watchlist_sensing (D7-M) @ {ROOT} ==")
    check_gitignore()
    check_no_private_without_env()
    check_sample_safe()
    check_bounded_groups()
    check_collector_gating()
    check_classification()
    check_no_private_dump()
    check_civil_priority()
    check_no_fake()
    check_live_site_audit()
    print()
    if _failures:
        print(f"RESULT: FAIL ({len(_failures)} 항목)")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("RESULT: PASS — 비공개 사이트 워치리스트 센싱 + 토목 우선순위 audit (D7-M)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
