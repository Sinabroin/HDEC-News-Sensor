#!/usr/bin/env python3
"""D7-AD-R verifier — 단일 임원 대시보드 IA 정리(consolidate executive radar layout).

사용자 D7-AD-R 검증 항목을 오프라인으로 확인한다(네트워크/비밀값 0건, 시간 무관):

  1. '임원 브리핑 섹션' 독립 블록이 화면 중앙에 덩그러니 남아 있지 않다(제거).
  2. 브리핑 섹션(오늘의 신규 이슈/수주/재무/정책/경쟁/브랜드/해외 언론)이 News Explorer(뉴스 탭)로 통합.
  3. 기상·날씨가 뉴스 기사 섹션이 아니다 — '현장 기상' 카드(시장 탭) + weather 섹션은 '기사 0건' 미표기.
  4. 운영 API 장문 설명이 details(opctl-more)로 접힘 + opctl compact.
  5. disabled 버튼 설명은 '운영자 서버 미연결' 한 줄(opUnsetLine).
  6. 현장 워치리스트가 3단계 접힘 구조(대분류→중분류→개별 현장) — 개별 현장은 중분류 펼침 전 비표시.
  7. private/operator에서 135개 현장 보존(있으면 검사, 없으면 SKIP — 가짜 성공 금지).
  8. public에서 실제 현장명 미노출.
  9. 시장 미연동 항목이 상태 보드(연동 완료/보고·수동 확인/미연동 후보/우선 연동 필요)로 정리(방치 금지).
 10. docs/daily/*.html 은 빌더 산출(hand-edit 아님) + 비공개 목록/에이전트/디자인 stage 금지.

기존 verify_dashboard_accordion_sections / verify_operator_controls / verify_site_watch_nav_tree
계약은 그대로 유지된다(이 verifier는 D7-AD-R 재배치/재구성 계약만 추가 검증한다).
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
_OP_ONLY = "디컨설현장전용테스트R"   # 어떤 mock 기사에도 없는 이름 → 공개/비공개 대조용
# 사용자 지정 market source backlog(미연동/보고 항목) — label_kr 부분일치로 존재 확인(방치 금지 → 상태 분류).
MARKET_BACKLOG_LABELS = ["니켈", "철스크랩", "시멘트", "아스팔트", "항공유",
                         "벙커유", "원료탄", "미 국채 2Y", "한국 국고 10Y", "TWD/KRW"]

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
    """needles가 HTML에서 주어진 순서로(각각 존재하며 증가하는 위치) 등장하면 True."""
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


# ---------------------------------------------------------------------------
# 1 · 템플릿 소스 계약(재구성 로직 — DOM/JS/CSS 앵커)
# ---------------------------------------------------------------------------

def check_template() -> None:
    t = _read(TEMPLATE)
    check("1a: 독립 '임원 브리핑 섹션' <section class=\"accordion\"> 제거",
          '<section class="accordion"' not in t
          and 'aria-label="임원 브리핑 섹션"' not in t)
    check("1b: 독립 브리핑 타이틀 블록(acc-title/acc-head) 제거",
          'class="acc-title"' not in t)
    check("1c: 브리핑 컨테이너(accordionSections)와 서버 렌더 마커는 유지(뉴스 탭으로 이동)",
          'id="accordionSections"' in t and "<!-- ACCORDION-INJECT -->" in t)
    check("1d: News Explorer 통합 헤더(news-brief-head)",
          'class="news-brief-head"' in t)

    # 3단계 현장 네비(대분류→중분류→개별) — 렌더/토글/CSS 소스 앵커
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
    # D7-AD-P/D7-N 계약 앵커 회귀 가드(깨지지 않았는지)
    for anchor in ("function renderScopeNodes", "function renderSiteScopeNav",
                   "function filterSiteNav", "renderScopeNodes(sc, data)",
                   'Number(n.match_count) ? "" : " zero"', "var priv = !!SITE_TREE",
                   "if (!SITE_TREE || !isSiteScope(key))", "MODEL.site_watch_tree || null"):
        check(f"6f: 기존 네비/트리 앵커 유지 '{anchor}'", anchor in t)

    # 운영자 compact + 접이식 + 한 줄 상태
    check("4a: 운영자 패널 compact(opctl compact)", 'class="opctl compact"' in t)
    check("4b: 장문 안내/PIN는 접이식(details.opctl-more)로", 'class="opctl-more"' in t
          and "<details class=\"opctl-more\">" in t)
    check("5a: disabled 한 줄 설명(opUnsetLine) + '운영자 서버 미연결'",
          'id="opUnsetLine"' in t and "운영자 서버 미연결" in t)
    # 운영 계약 회귀 가드(라벨/ID/보안 문구 — verify_operator_controls와 동일 의도)
    for anchor in ("데이터 새로고침 실행", "텔레그램 전송 실행", "Teams 채널 전송 실행",
                   'id="opCollectBtn" type="button" disabled',
                   'id="opSendBtn" type="button" disabled',
                   'id="opTeamsBtn" type="button" disabled', 'id="opPin"',
                   "브라우저에는 토큰·시크릿을 저장하지 않으며, GitHub로 이동하지 않습니다."):
        check(f"4c: 운영 계약 앵커 유지 '{anchor[:32]}'", anchor in t)
    # D7-AD-U — Teams 채널 전송이 collect/telegram과 동일하게 운영 API(send-teams)로 배선됐다
    # (operator_gateway.trigger_teams → email-alert.yml dispatch). 더 이상 dead가 아니다.
    check("4d: Teams 버튼이 운영 API로 배선(el(opTeamsBtn)+활성화+send-teams endpoint)",
          'el("opTeamsBtn")' in t and "teamsBtn.disabled = false" in t
          and "/api/operator/send-teams" in t)

    # D7-AD-V — 별도 상단 목차(railNav '전체 탐색')와 본문 위 카테고리 칩 바(newsCatFilter)를 제거하고,
    # 기존 좌측 목차(#lensnav) 하나를 필터/탐색 진입점으로 흡수했다.
    check("V1: 별도 상단 목차(railNav) 제거", 'id="railNav"' not in t and 'class="railnav"' not in t)
    check("V1: '전체 탐색' 문구 제거(주석 포함)", "전체 탐색" not in t)
    check("V1: 본문 위 뉴스 카테고리 칩 바(newsCatFilter) 제거", 'id="newsCatFilter"' not in t)
    check("V2: 좌측 목차가 단일 탐색 진입점(lensnav)", 'id="lensnav"' in t)
    for key in ("order", "finance", "policy", "competitor", "brand", "global_press"):
        check(f"V2: 좌측 목차 뉴스 분류 항목(navcat data-acc='{key}')",
              f'class="nav navcat" data-acc="{key}"' in t)
    for lbl in ("수주", "재무", "정책", "경쟁", "브랜드", "해외 언론"):
        check(f"V2: 뉴스 분류 라벨 '{lbl}'", f'class="nlabel">{lbl}</span>' in t)
    check("V3: 좌측 목차 시장 그룹(navmkt data-market)", 'class="nav navmkt" data-market="' in t)
    check("V3: 좌측 목차 기상 항목(navwx)→siteWeatherCard(뉴스 필터 제외)",
          'class="nav navwx"' in t and "function openWeatherRisk" in t
          and 'el("siteWeatherCard")' in t)
    check("V4: 뉴스 분류 클릭이 해당 브리핑 섹션을 연다(openNewsCategory→details.acc-sec)",
          "function openNewsCategory" in t and "details.acc-sec" in t and "d.open = on" in t)
    # 운영자 실행은 별도 '운영' 목차 항목 없이 왼쪽 rail 컬럼(railcol) 하단 compact 카드로 유지.
    check("U2: 운영자 패널이 왼쪽 rail 컬럼(railcol) 안", 'class="railcol"' in t)

    # 시장 소스 상태 보드(4단계) + 미연동 사유
    check("9a: 시장 상태 보드(renderMarketStatusBoard/marketStatusBoard)",
          "function renderMarketStatusBoard" in t and 'id="marketStatusBoard"' in t)
    for lbl in ("연동 완료", "보고·수동 확인", "미연동 후보", "우선 연동 필요"):
        check(f"9b: 상태 라벨 '{lbl}'", lbl in t)
    check("9c: 우선 연동 필요 목록(MARKET_PRIORITY) 정의", "MARKET_PRIORITY" in t)
    # D7-AD-U — 미연동/우선 항목을 방치하지 않고 '왜 미연동인지' 사유를 함께 노출한다.
    check("U3: 미연동 사유(MARKET_REASON) 정의 + 사유 렌더(marketReason/ms-why)",
          "MARKET_REASON" in t and "marketReason" in t and "ms-why" in t)

    # 현장 기상 카드(뉴스 accordion 아님) — D7-AD-U: 명일 정오 시공 리스크 계약
    check("3a: 현장 기상 카드(siteWeatherCard) + '기상 데이터 소스 미연동'",
          'id="siteWeatherCard"' in t and "기상 데이터 소스 미연동" in t)
    check("U4: 기상 카드가 '명일 정오 시공 리스크'(기준 명일 정오 12:00) 계약",
          "명일 정오 시공 리스크" in t and "명일 정오 12:00" in t)
    for fld in ("강수확률", "예상 강수량", "풍속", "돌풍", "폭염·한파", "특보", "작업 리스크 등급"):
        check(f"U4: 기상 표시 항목 '{fld}'", fld in t)
    for imp in ("우천", "강풍", "고소작업", "타워크레인", "콘크리트 타설", "외장·방수", "토공"):
        check(f"U4: 시공 영향 문구 '{imp}'", imp in t)


# ---------------------------------------------------------------------------
# 2 · 공개(mock) 빌드 — IA 순서/통합/기상·운영자 재배치 + 이름 비노출
# ---------------------------------------------------------------------------

def check_public_build() -> None:
    with tempfile.TemporaryDirectory(prefix="hdec_iar_pub_") as tmp:
        out = Path(tmp) / "dash.html"
        proc = _build(out)
        if not check("2a: 공개 빌드 동작", proc.returncode == 0 and out.exists(),
                     (proc.stderr or "")[-200:]):
            return
        html = out.read_text(encoding="utf-8")

        check("2b: 브리핑 섹션이 News Explorer(뉴스 탭) 안으로 통합(accordionSections가 "
              "panel-news와 panel-ai 사이)",
              _order(html, 'id="panel-news"', 'id="accordionSections"', 'id="panel-ai"'))
        secs = re.findall(r'<details class="acc-sec" data-acc="([a-z_]+)"', html)
        check("2c: 브리핑 8섹션이 뉴스 탭 안에 렌더(오늘의 신규 이슈/수주/재무/정책/경쟁/브랜드/기상/해외)",
              len(secs) == 8
              and {"new", "order", "finance", "policy", "competitor", "brand",
                   "weather", "global_press"} <= set(secs), str(secs))
        # D7-AD-V — 본문 위 카테고리 칩 바(newsCatFilter)는 제거됐고, 카테고리 탐색은 좌측 목차의
        # 뉴스 분류 항목(navcat)이 담당한다. 그 data-acc 키는 서버 렌더 아코디언 키와 정합해야 한다.
        check("2d: 본문 위 뉴스 카테고리 칩 바(newsCatFilter) 제거",
              'id="newsCatFilter"' not in html)
        check("2d: 좌측 목차 뉴스 분류 항목(navcat)이 브리핑 아코디언 키와 정합",
              all(f'class="nav navcat" data-acc="{k}"' in html for k in
                  ("order", "finance", "policy", "competitor", "brand", "global_press")))

        # 기상·날씨: 뉴스 기사 섹션이 아니다 — 현장 기상 카드는 시장 탭, weather 섹션은 '기사 0건' 미표기
        check("3b: '현장 기상' 카드가 시장 탭 안(panel-market 이후)",
              _order(html, 'id="panel-market"', 'id="siteWeatherCard"'))
        wm = re.search(r'<details class="acc-sec" data-acc="weather".*?</details>', html, re.S)
        check("3c: weather 브리핑 섹션은 '기사 0건'을 표기하지 않음(뉴스 수집 섹션 아님)",
              bool(wm) and "기사 0건" not in wm.group(0), "weather details 미발견"
              if not wm else "ok")
        check("3d: weather 섹션은 데이터 소스 미연동으로 정직 표기",
              bool(wm) and ("미연동" in wm.group(0)))

        # D7-AD-U — 운영자 실행: 화면 하단(panel-rumor 뒤)에서 왼쪽 rail 컬럼(railcol)으로 이동.
        # 이제 rail 안에서 lensnav 뒤·본문(main/panel-news) 앞에 온다(하단 덩그러니 제거).
        check("4e: 운영자 실행 컨트롤이 왼쪽 rail(railcol) 하단으로 이동(main 본문 앞)",
              _order(html, 'class="railcol"', 'id="lensnav"', 'id="opctl"', 'id="panel-news"')
              and _order(html, 'id="opctl"', "<footer"))
        check("4f: 장문 보안 안내가 details(opctl-more) 안으로 접힘",
              _order(html, '<details class="opctl-more">',
                     "브라우저에는 토큰·시크릿을 저장하지 않으며, GitHub로 이동하지 않습니다."))
        check("5b: 미설정(공개) 시 '운영자 서버 미연결' 한 줄 노출(opUnsetLine)",
              'id="opUnsetLine"' in html and "운영자 서버 미연결" in html)

        # 시장 상태 보드 + 백로그 항목이 방치되지 않고 분류 대상으로 존재
        check("9d: 시장 상태 보드 컨테이너 렌더(marketStatusBoard)",
              'id="marketStatusBoard"' in html)
        model = _model(html)
        labels = {(it.get("label_kr") or "") for it in (model.get("market_items") or [])}
        missing = [b for b in MARKET_BACKLOG_LABELS
                   if not any(b in lk for lk in labels)]
        check("9e: 사용자 지정 market backlog 항목이 모두 모델에 존재(상태 보드가 분류)",
              not missing, f"누락: {missing}" if missing else "ok")

        # public-safe: 이름/트리 비노출
        check("8a: 공개 모델에 site_watch_tree 키 없음(공개 null-gating)",
              "site_watch_tree" not in model)
        leaked = [m for m in INTERNAL_MARKERS if m in html]
        check("8b: 공개 HTML에 내부 고유 현장명 표본 비노출", not leaked,
              str(leaked) if leaked else "ok")


# ---------------------------------------------------------------------------
# 3 · 운영자(비공개 + EXPOSE_TREE) 빌드 — 3단계 데이터(그룹→노드) + 현장명 노출
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# 4 · 실데이터 scope 분포 135 보존(있으면 검사) — 사용자 확정값
# ---------------------------------------------------------------------------

def check_real_counts() -> None:
    if not PRIVATE_LOCAL.exists():
        info("SKIP — 실 비공개 워치리스트 없음. SITE_WATCHLIST_PATH 시드 후 재검(135: 57/53/23/2).")
        return
    s = sw.scope_summary_for_model(str(PRIVATE_LOCAL))   # 이름 비노출(카운트만)
    check("7d: 실데이터 total=135 보존", s.get("total") == EXPECTED_REAL_TOTAL,
          f"total={s.get('total')}")
    check("7e: 실데이터 scope 분포(국내57/해외53/지사23/법인2) 보존",
          s.get("by_scope") == EXPECTED_REAL, str(s.get("by_scope")))
    leaked = [m for m in INTERNAL_MARKERS if m in json.dumps(s, ensure_ascii=False)]
    check("8c: 실데이터 요약 dict에도 현장명 0건(카운트만)", not leaked,
          str(leaked) if leaked else "ok")


# ---------------------------------------------------------------------------
# 5 · 커밋 산출물/스테이징 위생 — hand-edit 금지 · 비공개/에이전트/디자인 stage 금지
# ---------------------------------------------------------------------------

def check_artifact_and_staging() -> None:
    html = _read(DASHBOARD)
    if html:
        check("10a: 커밋 대시보드는 빌더 산출(hand-edit 아님 — export 마커)",
              "source=templates/dashboard_preview.html" in html)
        leaked = [m for m in INTERNAL_MARKERS if m in html]
        check("10b: 커밋 대시보드에 내부 고유 현장명 표본 비노출", not leaked,
              str(leaked) if leaked else "ok")
    else:
        info(f"SKIP — 커밋 대시보드 없음({DASHBOARD.name}). CI/빌더 재생성 시 반영.")

    rc, out = _git("check-ignore", _REL_PRIVATE)
    if rc == 127:
        check("10c: 비공개 local.json gitignore 규칙(폴백)",
              "data/private/*" in _read(ROOT / ".gitignore"))
    else:
        check("10c: 비공개 local.json gitignored", rc == 0, out or "매칭 없음")

    _, staged = _git("diff", "--cached", "--name-only")
    staged_lines = [s for s in (staged or "").splitlines() if s.strip()]
    forbidden = [".agents/", "design/", "data/private/"]
    hits = [s for s in staged_lines if any(s.startswith(p) for p in forbidden)]
    check("10d: 금지 경로(.agents/·design/·data/private/) staged 아님", not hits, str(hits))
    docs_staged = [s for s in staged_lines if s.startswith("docs/daily/")
                   and s.endswith(".html")]
    check("10e: docs/daily/*.html hand-edit stage 아님(빌더 산출만 허용)",
          not docs_staged or "source=templates/dashboard_preview.html" in html, str(docs_staged))


def main() -> int:
    print(f"== verify_dashboard_ia_consolidation (D7-AD-R → D7-AD-V fold) @ {ROOT} ==")
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
    print("RESULT: PASS — 단일 임원 대시보드 IA(좌측 목차 단일 진입점 · railNav/newsCatFilter 제거 · "
          "뉴스 분류/시장/기상 흡수 · 3단계 현장 네비 · 시장 상태 보드 · 현장 기상 카드 · 운영자 compact) "
          "public-safe (D7-AD-R→V)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
