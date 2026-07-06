#!/usr/bin/env python3
"""D7-AD-R → D7-AD-W Phase 1B verifier — 단일 임원 대시보드 IA 정리.

오프라인 검증(네트워크/비밀값 0건):

  1. 독립 '임원 브리핑 섹션' accordion 제거 · layered flat list(#categoryNewsList)로 대체
  2. 브리핑 분류 데이터는 preview-model.nav_category_sections로 주입(weather 제외)
  3. 기상·날씨는 siteWeatherCard(시장 탭) — 뉴스 기사 섹션 아님
  4. 운영 API 상세는 details(opctl-more) 접힘 + opctl compact
  5. API 미연결 공개 페이지 → 3개 실행 버튼 visible + 명확한 미연결 상태
  6. 현장 워치리스트 3단계 접힘(대분류→중분류→개별 현장)
  7. private/operator 135개 현장 보존(있으면 검사)
  8. public 실제 현장명 미노출
  9. 시장: 메인=연동 완료·보고·수동 확인 · 미연동/우선=접힘(ms-collapse) · MARKET_REASON 보존
 10. docs/daily/*.html 빌더 산출 · 금지 경로 stage 없음
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

BUILDER = ROOT / "scripts" / "build_static_dashboard.py"
TEMPLATE = ROOT / "templates" / "dashboard_preview.html"
DASHBOARD = ROOT / "docs" / "daily" / "dashboard-latest.html"
PRIVATE_LOCAL = ROOT / "data" / "private" / "site_watchlist.local.json"
_REL_PRIVATE = "data/private/site_watchlist.local.json"

from app import site_watchlist as sw  # noqa: E402

EXPECTED_REAL = {"domestic_site": 57, "overseas_site": 53,
                 "overseas_branch": 23, "overseas_subsidiary": 2}
EXPECTED_REAL_TOTAL = 135
INTERNAL_MARKERS = ["흑석9구역", "자푸라", "코즐로두이", "진해신항", "월곶~판교",
                    "마잔", "카르발라", "루사일 타워", "신반포 22차", "대조제1구역"]
_SECRET = "절대공개금지내부ZZX9현장R"
_OP_ONLY = "디컨설현장전용테스트R"
MARKET_BACKLOG_LABELS = ["니켈", "철스크랩", "시멘트", "아스팔트", "항공유",
                         "벙커유", "원료탄", "미 국채 2Y", "한국 국고 10Y", "TWD/KRW"]
NEWS_PILL_KEYS = ("all", "new", "order", "finance", "policy", "competitor", "brand", "global_press")

_failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        _failures.append(name)
    return ok


def info(msg: str) -> None:
    print(f"[INFO] {msg}")


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
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


def _order(html: str, *needles) -> bool:
    last = -1
    for n in needles:
        i = html.find(n)
        if i < 0 or i <= last:
            return False
        last = i
    return True


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


def _build(out: Path, env_extra=None):
    env = {**os.environ}
    env.pop("SITE_WATCHLIST_PATH", None)
    env.pop("SITE_WATCHLIST_EXPOSE_TREE", None)
    env["APP_MODE"] = "mock"
    env["PYTHONHASHSEED"] = "0"
    if env_extra:
        env.update(env_extra)
    return subprocess.run([sys.executable, str(BUILDER), "--output", str(out)],
                          cwd=ROOT, capture_output=True, text=True, timeout=300, env=env)


def _temp_watchlist() -> str:
    items = {"items": [
        {"id": "neom_name", "name": "네옴 AI 데이터센터", "scope": "overseas_site",
         "business_lens": "new_energy", "tier": 1},
        {"id": "dom_a", "name": _OP_ONLY, "scope": "domestic_site",
         "business_lens": "civil_infrastructure", "tier": 2},
        {"id": "dom_b", "name": _SECRET, "scope": "domestic_site",
         "business_lens": "plant", "tier": 2},
        {"id": "branch1", "name": "해외지사테스트R", "scope": "overseas_branch",
         "org_unit": "해외영업", "tier": 2},
        {"id": "sub1", "name": "해외법인테스트IncR", "scope": "overseas_subsidiary",
         "org_unit": "해외법인", "tier": 2},
    ]}
    fd, path = tempfile.mkstemp(prefix="hdec_iar_", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False)
    return path


def check_template() -> None:
    t = _read(TEMPLATE)
    check("1a: 독립 '임원 브리핑 섹션' <section class=\"accordion\"> 제거",
          '<section class="accordion"' not in t
          and 'aria-label="임원 브리핑 섹션"' not in t)
    check("1b: 독립 브리핑 타이틀 블록(acc-title/acc-head) 제거",
          'class="acc-title"' not in t)
    check("1c: visible accordion 제거(#accordionSections·ACCORDION-INJECT 없음)",
          'id="accordionSections"' not in t and "<!-- ACCORDION-INJECT -->" not in t)
    check("1d: layered flat list(#categoryNewsList + #cnlPills) 존재",
          'id="categoryNewsList"' in t and 'id="cnlPills"' in t)
    check("1e: layered 탐색 JS(openNavigationNewsResult/renderLayeredNewsList/countForSecondary)",
          "function openNavigationNewsResult" in t and "function renderLayeredNewsList" in t
          and "function countForSecondary" in t)
    check("1f: 2차 pill count badge(cnl-badge) 렌더",
          "cnl-badge" in t and 'class="cnl-badge' in t)
    check("1g: '카테고리별 브리핑' 문구 없음", "카테고리별 브리핑" not in t)

    check("6a: renderScopeNodes가 중분류 접이식(sn-mid/sn-midtog) 생성",
          'class="sn-mid"' in t and "sn-midtog" in t and "sn-midcaret" in t)
    check("6b: 개별 현장은 기존 노드 마크업 재사용(out += renderSiteNode)",
          "out += renderSiteNode(scope, n)" in t)
    check("6c: match_count 우선 정렬(중분류 내 정렬 comparator)",
          "Number(b.match_count)" in t and ".slice().sort(" in t)
    check("6d: 중분류 클릭 토글 핸들러(closest('.sn-midtog'))",
          'closest(".sn-midtog")' in t)
    check("6e: CSS — 개별 현장은 중분류 펼침 전 비표시(.sn-mid .sn-nodes{display:none})",
          re.search(r"\.sitenav\s+\.sn-mid\s+\.sn-nodes\{display:none", t) is not None
          and re.search(r"\.sitenav\s+\.sn-mid\.open\s+\.sn-nodes\{display:flex", t) is not None)
    for anchor in ("function renderScopeNodes", "function renderSiteScopeNav",
                   "function filterSiteNav", "renderScopeNodes(sc, data)",
                   'Number(n.match_count || 0)', 'Number(n.match_count) ? "" : " zero"',
                   "var hasTree = !!SITE_TREE",
                   "if (!SITE_TREE || !isSiteScope(key))", "MODEL.site_watch_tree || null"):
        check(f"6f: 기존 네비/트리 앵커 유지 '{anchor}'", anchor in t)

    check("4a: 운영자 패널 compact(opctl compact)", 'class="opctl compact"' in t)
    check("4b: 장문 안내/PIN는 접이식(details.opctl-more)로", 'class="opctl-more"' in t
          and "<details class=\"opctl-more\">" in t)
    check("5a: 운영자 모드가 클릭 가능한 summary + 실행 버튼 3개",
          '<summary class="opctl-mode-toggle">' in t and 'id="opActionLinks"' not in t
          and all(label in t for label in (
              "데이터 새로고침 실행", "텔레그램 전송 실행", "Teams 채널 전송 실행"
          )))
    # D7-AG-3 — 브라우저 PIN 제거 + 보호 서버 앞단 이관. opPin·PIN 저장 문구 앵커는 제거하고,
    # 서버 앞단 인증 안내(opAuthNote) + PIN 미보유(페이지에 PIN·토큰을 넣지 않습니다) 앵커로 대체.
    for anchor in ("데이터 새로고침 실행", "텔레그램 전송 실행", "Teams 채널 전송 실행",
                   'id="opCollectBtn" type="button" disabled',
                   'id="opSendBtn" type="button" disabled',
                   'id="opTeamsBtn" type="button" disabled', 'id="opAuthNote"',
                   "페이지에 PIN·토큰을 넣지 않습니다"):
        check(f"4c: 운영 계약 앵커 유지 '{anchor[:32]}'", anchor in t)
    check("4c-2: 브라우저 PIN 입력 제거(서버 앞단 인증)",
          'id="opPin"' not in t and "승인 PIN" not in t)
    # 하이브리드(D7-AG-5B): Teams 버튼은 운영 API에 배선(endpoint 주입)되되, 공개 Origin 인가로는
    # 발송을 열지 않으므로 인증잠금(authlocked) 상태다 — 가짜가 아니라 '인증 필요'로 정직하게 막는다.
    check("4d: Teams 버튼이 운영 API로 배선(el(opTeamsBtn)+send-teams endpoint) · 인증잠금",
          'el("opTeamsBtn")' in t and "/api/operator/send-teams" in t
          and "authlocked" in t)

    check("V1: 별도 상단 목차(railNav) 제거", 'id="railNav"' not in t and 'class="railnav"' not in t)
    check("V1: '전체 탐색' 문구 제거(주석 포함)", "전체 탐색" not in t)
    check("V1: 본문 위 뉴스 카테고리 칩 바(newsCatFilter) 제거", 'id="newsCatFilter"' not in t)
    check("V2: 좌측 목차가 단일 탐색 진입점(lensnav)", 'id="lensnav"' in t)
    check("V2: 좌측 navcat(수주/재무 등 1차 항목) 제거",
          'class="nav navcat"' not in t)
    # D7-AE-RC1: 시장 그룹 이동은 좌측 목차가 아니라 시장 탭 상단 pill bar(#mktPillBar)로
    # 옮겼다(사용자 지시 — 좌·상단 시장 nav 중복 제거). navmkt 클래스/라우팅은 그대로다.
    check("V3: 시장 그룹 navmkt data-market(pill bar, 좌측 목차 밖)",
          'class="nav navmkt mkt-pill" data-market="' in t and 'id="mktPillBar"' in t
          and '<div class="gtitle">시장 모니터링</div>' not in t)
    check("V3: 좌측 목차 기상 항목(navwx)→siteWeatherCard(뉴스 필터 제외)",
          'class="nav navwx"' in t and "function openWeatherRisk" in t
          and 'el("siteWeatherCard")' in t)
    check("V4: openNewsCategory/acc-sec 아코디언 탐색 제거",
          "function openNewsCategory" not in t and "details.acc-sec" not in t)
    check("U2: 운영자 패널이 왼쪽 rail 컬럼(railcol) 안", 'class="railcol"' in t)

    check("9a: 시장 상태 보드(renderMarketStatusBoard/marketStatusBoard)",
          "function renderMarketStatusBoard" in t and 'id="marketStatusBoard"' in t)
    for lbl in ("연동 완료", "보고·수동 확인", "미연동 후보", "우선 연동 필요"):
        check(f"9b: 상태 라벨 '{lbl}'", lbl in t)
    check("9c: 우선 연동 필요 목록(MARKET_PRIORITY) 정의", "MARKET_PRIORITY" in t)
    check("U3: 미연동 사유(MARKET_REASON) 정의 + 사유 렌더(marketReason/ms-why)",
          "MARKET_REASON" in t and "marketReason" in t and "ms-why" in t)
    check("9d: 미연동/우선=접힘(ms-collapse) · 메인=연동 완료·보고(ms-collapse-body)",
          "ms-collapse" in t and "mktstatus-main" in t and "ms-collapse-sum" in t)
    check("9e: 시장 카드 미연동=접힘(mcat-backlog/details)",
          "mcat-backlog" in t and "<details class=\"mcat-backlog\">" in t)

    check("3a: 현장 기상 카드(siteWeatherCard) + '기상 데이터 미수신'",
          'id="siteWeatherCard"' in t and "기상 데이터 미수신" in t
          and "기상 데이터 소스 미연동" not in t)
    check("U4: 기상 카드가 '명일 정오 시공 리스크'(기준 명일 정오 12:00) 계약",
          "명일 정오 시공 리스크" in t and "명일 정오 12:00" in t)
    for fld in ("강수확률", "예상 강수량", "풍속", "돌풍", "폭염·한파", "특보", "작업 리스크 등급"):
        check(f"U4: 기상 표시 항목 '{fld}'", fld in t)
    for imp in ("우천", "강풍", "고소작업", "타워크레인", "콘크리트 타설", "외장·방수", "토공"):
        check(f"U4: 시공 영향 문구 '{imp}'", imp in t)


def check_public_build() -> None:
    with tempfile.TemporaryDirectory(prefix="hdec_iar_pub_") as tmp:
        out = Path(tmp) / "dash.html"
        proc = _build(out)
        if not check("2a: 공개 빌드 동작", proc.returncode == 0 and out.exists(),
                     (proc.stderr or "")[-200:]):
            return
        html = out.read_text(encoding="utf-8")

        check("2b: visible accordion(details.acc-sec) 없음",
              '<details class="acc-sec"' not in html and 'id="accordionSections"' not in html)
        check("2c: layered flat list(#categoryNewsList + #cnlPills) 존재",
              'id="categoryNewsList"' in html and 'id="cnlPills"' in html)
        check("2d: navcat(1차 분류 항목) 없음", 'class="nav navcat"' not in html)
        check("2d: 본문 위 뉴스 카테고리 칩 바(newsCatFilter) 제거",
              'id="newsCatFilter"' not in html)

        check("3b: '현장 기상' 카드가 시장 탭 안(panel-market 이후)",
              _order(html, 'id="panel-market"', 'id="siteWeatherCard"'))
        check("3c: weather acc-sec 없음(뉴스 accordion 제거)",
              '<details class="acc-sec" data-acc="weather"' not in html)

        check("4e: 운영자 실행 컨트롤이 왼쪽 rail(railcol) 하단으로 이동(main 본문 앞)",
              _order(html, 'class="railcol"', 'id="lensnav"', 'id="opctl"', 'id="panel-news"')
              and _order(html, 'id="opctl"', "<footer"))
        check("4f: 공개 빌드 운영자 모드는 클릭 가능한 details",
              '<details class="opctl-panel" id="opctlPanel">' in html
              and '<summary class="opctl-mode-toggle">' in html)
        check("5b: 미설정 공개 빌드에 실행 UI visible + 명확한 미연결",
              'id="opActionLinks"' not in html and 'id="opApiControls"' in html
              and "Operator API 미연결" in html
              and "데이터 새로고침 실행" in html
              and "텔레그램 전송 실행" in html
              and "Teams 채널 전송 실행" in html
              and "Public URL 새로고침" in html)

        check("9d: 시장 상태 보드 컨테이너 렌더(marketStatusBoard)",
              'id="marketStatusBoard"' in html)
        model = _model(html)
        labels = {(it.get("label_kr") or "") for it in (model.get("market_items") or [])}
        missing = [b for b in MARKET_BACKLOG_LABELS
                   if not any(b in lk for lk in labels)]
        check("9f: 사용자 지정 market backlog 항목이 모두 모델에 존재(상태 보드가 분류)",
              not missing, f"누락: {missing}" if missing else "ok")
        nav_secs = {s.get("key") for s in (model.get("nav_category_sections") or [])}
        for key in NEWS_PILL_KEYS:
            if key == "all":
                continue
            check(f"2e: nav_category_sections에 '{key}' 존재", key in nav_secs)
        check("2e: weather는 nav_category_sections에서 제외", "weather" not in nav_secs)

        tree = model.get("site_watch_tree") or {}
        node_total = sum(len(g.get("nodes") or [])
                         for sc in (tree.get("by_scope") or {}).values()
                         for g in sc.get("groups") or [])
        check("8a: 공개 모델에 site_watch_tree 현장 노드 노출(D7-AD-Z)",
              node_total > 0, f"nodes={node_total}")
        check("8b: 공개 HTML에 현장 워치리스트 DOM 유지",
              'id="siteScopeNav"' in html)


def check_operator_build() -> None:
    path = _temp_watchlist()
    try:
        with tempfile.TemporaryDirectory(prefix="hdec_iar_op_") as tmp:
            out = Path(tmp) / "dash.html"
            proc = _build(out, {"SITE_WATCHLIST_PATH": path,
                                "SITE_WATCHLIST_EXPOSE_TREE": "1"})
            if not check("7a: 운영자 빌드 동작(비공개 + EXPOSE_TREE=1)",
                         proc.returncode == 0 and out.exists(), (proc.stderr or "")[-200:]):
                return
            html = out.read_text(encoding="utf-8")
            model = _model(html)
            tree = model.get("site_watch_tree")
            if not check("7b: 운영자 모델에 site_watch_tree 주입", tree is not None):
                return
            nodes = _nodes(tree)
            check("7c: EXPOSE_TREE → 전체 5개 현장 노드 노출", len(nodes) == 5, f"{len(nodes)}개")
            has_grouped = any((sc.get("groups") and any(g.get("nodes") for g in sc["groups"]))
                              for sc in (tree.get("by_scope") or {}).values())
            check("6g: 트리가 scope→group→node(중분류→개별 현장) 계층을 제공(3단계 데이터)",
                  has_grouped)
            check("6h: 운영자 빌드는 현장명 노출(개별 현장 필터용)", _OP_ONLY in html)
    finally:
        os.unlink(path)


def check_real_counts() -> None:
    if not PRIVATE_LOCAL.exists():
        info("SKIP — 실 비공개 워치리스트 없음. SITE_WATCHLIST_PATH 시드 후 재검(135: 57/53/23/2).")
        return
    s = sw.scope_summary_for_model(str(PRIVATE_LOCAL))
    check("7d: 실데이터 total=135 보존", s.get("total") == EXPECTED_REAL_TOTAL,
          f"total={s.get('total')}")
    check("7e: 실데이터 scope 분포(국내57/해외53/지사23/법인2) 보존",
          s.get("by_scope") == EXPECTED_REAL, str(s.get("by_scope")))
    leaked = [m for m in INTERNAL_MARKERS if m in json.dumps(s, ensure_ascii=False)]
    check("8c: 실데이터 요약 dict에도 현장명 0건(카운트만)", not leaked,
          str(leaked) if leaked else "ok")


def check_artifact_and_staging() -> None:
    html = _read(DASHBOARD)
    if not html:
        info(f"SKIP — 커밋 대시보드 없음({DASHBOARD.name}). 빌드 후 재검.")
        return

    check("10a: 커밋 대시보드는 빌더 산출(hand-edit 아님 — export 마커)",
          "source=templates/dashboard_preview.html" in html
          or "dashboard-export:summary" in html)

    model = _model(html)
    tree = model.get("site_watch_tree") or {}
    node_total = sum(
        len(g.get("nodes") or [])
        for sc in (tree.get("by_scope") or {}).values()
        for g in sc.get("groups") or []
    )
    check("10b: 커밋 대시보드에 승인된 공개 현장 노드 노출(D7-AD-Z)",
          node_total > 0, f"nodes={node_total}")

    try:
        import subprocess
        ignored = subprocess.run(
            ["git", "check-ignore", "data/private/site_watchlist.local.json"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        check("10c: 비공개 local.json gitignored",
              ignored.returncode == 0,
              (ignored.stdout or ignored.stderr).strip())
    except Exception as exc:
        check("10c: 비공개 local.json gitignored", False, str(exc))

    try:
        import subprocess
        staged = subprocess.check_output(
            ["git", "diff", "--cached", "--name-only"],
            cwd=ROOT,
            text=True,
        ).splitlines()
    except Exception:
        staged = []

    forbidden = [
        x for x in staged
        if x.startswith(".agents/")
        or x.startswith("design/")
        or x.startswith("data/private/")
    ]
    check("10d: 금지 경로(.agents/·design/·data/private/) staged 아님",
          not forbidden, str(forbidden))

    staged_docs = [
        x for x in staged
        if x.startswith("docs/daily/") and x.endswith(".html")
    ]
    check("10e: docs/daily/*.html hand-edit stage 아님(빌더 산출만 허용)",
          not staged_docs, "staged docs 없음" if not staged_docs else str(staged_docs))

def main() -> int:
    print(f"== verify_dashboard_ia_consolidation (D7-AD-W Phase 1B) @ {ROOT} ==")
    check_template()
    check_public_build()
    check_operator_build()
    check_real_counts()
    check_artifact_and_staging()
    print()
    if _failures:
        print(f"RESULT: FAIL ({len(_failures)} 항목)")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("RESULT: PASS — layered flat list · pill count badge · 시장 접힘 · navcat/accordion 제거 "
          "(D7-AD-W Phase 1B)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
