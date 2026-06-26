#!/usr/bin/env python3
"""D7-N verifier — 현장/조직 감시 트리 freshness('!') + 생성일 + 프라이버시.

사용자 정정: `!` 는 "과거에 한 번이라도 매칭"이 아니라 **최근(≤30일) 공개뉴스 매칭**을 뜻해야
한다. 임원 센싱에서 '!'='지금 봐야 할 신호'. 과거(>30일) 매칭은 '!' 대신 '과거 매칭'으로만.

이 verifier는 완전 OFFLINE이고 시간 드리프트에 강하다:
- 마커/요약 단정은 **고정 now**를 주입한 site_watchlist.build_tree 로 결정적으로 검사한다
  (현재 시각에 의존하지 않음 — mock 발행일 vs 오늘 드리프트로 깨지지 않는다).
- 통합 빌드(mock, 임시 비공개 워치리스트)는 시간 무관 속성만 단정한다: 트리 주입 여부,
  freshness 키 존재, 레전드/카피의 HTML 노출, 비공개(미매칭) 이름 비노출.

사용자 요구 검사(핵심):
  1. 2025년 과거 매칭은 alert_marker="!" 를 받지 않는다.
  2. 최근 매칭은 alert_marker="!" 를 받는다.
  3. 마커 레전드가 "최근 공개뉴스 매칭 있음" 이라고 말한다.
  4. matched_nodes_all_time 가 recent_matched_nodes 보다 클 수 있다.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MODULE = ROOT / "app" / "site_watchlist.py"
BUILDER = ROOT / "scripts" / "build_static_dashboard.py"
TEMPLATE = ROOT / "templates" / "dashboard_preview.html"

from app import site_watchlist as sw  # noqa: E402

_KST = timezone(timedelta(hours=9))
# 고정 now — 사용자가 준 실데이터 시점(GTX-C latest 2026-06-22, GTX-B 2025-09-11)을 가르도록 2026-06-26.
NOW = datetime(2026, 6, 26, 12, 0, tzinfo=_KST)

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


# ---------------------------------------------------------------------------
# 1 · 결정적 freshness 계약 (고정 now 주입) — 사용자 핵심 요구 1~4
# ---------------------------------------------------------------------------

def check_freshness_contract() -> None:
    items = [
        {"id": "gtx_b", "name": "GTX-B 5공구 테스트", "scope": "domestic_site",
         "business_lens": "civil_infrastructure", "tier": 1},                   # 2025 → old
        {"id": "gtx_c", "name": "GTX-C 1공구 테스트", "scope": "domestic_site",
         "business_lens": "civil_infrastructure", "tier": 1},                   # 2026-06-22 → recent
        {"id": "today1", "name": "오늘 매칭 현장 테스트", "scope": "overseas_site",
         "business_lens": "plant", "tier": 2},                                  # today
        {"id": "edge30", "name": "30일경계 현장 테스트", "scope": "overseas_site",
         "business_lens": "plant", "tier": 2},                                  # 30일 → recent_30d
        {"id": "edge31", "name": "31일경계 현장 테스트", "scope": "overseas_site",
         "business_lens": "plant", "tier": 2},                                  # 31일 → old
        {"id": "undated", "name": "무날짜 현장 테스트", "scope": "domestic_site",
         "business_lens": "plant", "tier": 2},                                  # 날짜 없음 → 미상
    ]
    matches = {
        "gtx_b":  {"match_count": 1, "latest_published_at": "2025-09-11T16:00:00+09:00",
                   "article_keys": ["k_b"]},
        "gtx_c":  {"match_count": 2, "latest_published_at": "2026-06-22T15:01:00+09:00",
                   "article_keys": ["k_c1", "k_c2"]},
        "today1": {"match_count": 1, "latest_published_at": "2026-06-26T07:30:00+09:00",
                   "article_keys": ["k_t"]},
        "edge30": {"match_count": 1,
                   "latest_published_at": (NOW - timedelta(days=30)).isoformat(),
                   "article_keys": ["k_30"]},
        "edge31": {"match_count": 1,
                   "latest_published_at": (NOW - timedelta(days=31)).isoformat(),
                   "article_keys": ["k_31"]},
        "undated": {"match_count": 1, "latest_published_at": "", "article_keys": ["k_u"]},
    }
    tree = sw.build_tree(items, matches, expose_full_tree=False, is_private=True, now=NOW)
    n = _nodes(tree)

    # (1) 2025 과거 매칭은 '!' 없음
    check("1a: 2025 과거 매칭은 alert_marker='!' 를 받지 않는다",
          n.get("gtx_b", {}).get("alert_marker") == "" and not n["gtx_b"]["is_recent_signal"],
          f"gtx_b marker={n.get('gtx_b', {}).get('alert_marker')!r} fresh={n.get('gtx_b', {}).get('match_freshness')!r}")
    check("1a+: 과거 매칭은 '과거 매칭' 카피로 표시(muted)",
          n.get("gtx_b", {}).get("freshness_label") == "과거 매칭"
          and n["gtx_b"]["match_freshness"] == "old")

    # (2) 최근 매칭은 '!' 부여
    check("1b: 최근 매칭(2026-06-22)은 alert_marker='!' 를 받는다",
          n.get("gtx_c", {}).get("alert_marker") == "!" and n["gtx_c"]["is_recent_signal"] is True,
          f"gtx_c marker={n.get('gtx_c', {}).get('alert_marker')!r} fresh={n.get('gtx_c', {}).get('match_freshness')!r}")
    check("1b+: today / 30일경계 매칭도 '!'(recent)",
          n["today1"]["match_freshness"] == "today" and n["today1"]["alert_marker"] == "!"
          and n["edge30"]["match_freshness"] == "recent_30d" and n["edge30"]["alert_marker"] == "!")
    check("1b++: 31일(>30일) 매칭은 old → '!' 없음",
          n["edge31"]["match_freshness"] == "old" and n["edge31"]["alert_marker"] == "")
    check("1b+++: 발행일 미상 매칭은 '최근'으로 위장하지 않음('!' 없음, freshness '')",
          n["undated"]["match_freshness"] == "" and n["undated"]["alert_marker"] == "")

    # freshness 필드 존재(노드 스키마)
    miss = [k for k in ("match_freshness", "is_recent_signal", "freshness_label",
                        "latest_kst", "alert_marker") if k not in n["gtx_c"]]
    check("1c: 노드에 freshness 필드 존재(match_freshness/is_recent_signal/…)", not miss,
          f"누락: {miss}" if miss else "ok")
    check("1c+: latest_kst 는 표시용 날짜로 유지(old여도 날짜 노출)",
          n["gtx_b"]["latest_kst"] == "2025-09-11 16:00")

    # (4) 요약 — all_time > recent
    summ = {k: tree.get(k) for k in ("total_nodes", "matched_nodes_all_time",
                                     "recent_matched_nodes", "old_matched_nodes")}
    check("1d: 요약에 4개 카운트 키 존재",
          all(isinstance(summ[k], int) for k in summ), str(summ))
    # recent: gtx_c, today1, edge30 (3) · old: gtx_b, edge31 (2) · undated: 1 (미상) · all_time=6
    check("1d+: recent_matched_nodes 정확", summ["recent_matched_nodes"] == 3,
          str(summ["recent_matched_nodes"]))
    check("1d++: old_matched_nodes 는 명시적 old만(미상 제외)", summ["old_matched_nodes"] == 2,
          str(summ["old_matched_nodes"]))
    check("1d+++: matched_nodes_all_time(=6) > recent_matched_nodes(=3) 가능",
          summ["matched_nodes_all_time"] == 6
          and summ["matched_nodes_all_time"] > summ["recent_matched_nodes"], str(summ))
    check("1d#: matched_nodes 하위호환 별칭 = matched_nodes_all_time",
          tree.get("matched_nodes") == summ["matched_nodes_all_time"])

    # (3) 레전드
    check("1e: marker_legend 가 '최근 공개뉴스 매칭 있음' 이라고 말한다",
          "최근 공개뉴스 매칭 있음" in (tree.get("marker_legend") or ""),
          tree.get("marker_legend"))
    check("1e+: 레전드는 '위험 확정 아님' 단서를 유지",
          "위험 확정 아님" in (tree.get("marker_legend") or ""))

    # per-scope/group recent 카운트
    dom = (tree.get("by_scope") or {}).get("domestic_site") or {}
    check("1f: scope 에 recent_node_count 집계", "recent_node_count" in dom,
          f"domestic recent={dom.get('recent_node_count')}")


# ---------------------------------------------------------------------------
# 2 · 레전드/카피 상수 + now 미주입 시 비크래시
# ---------------------------------------------------------------------------

def check_constants() -> None:
    check("2a: TREE_MARKER_LEGEND 상수가 '최근 공개뉴스 매칭'",
          "최근 공개뉴스 매칭 있음" in sw.TREE_MARKER_LEGEND, sw.TREE_MARKER_LEGEND)
    check("2b: old freshness 라벨 = '과거 매칭'",
          sw._FRESHNESS_LABEL.get("old") == "과거 매칭")
    # now 미지정 시 현재 시각으로 동작(크래시 없음, 키 존재)
    t = sw.build_tree(
        [{"id": "x", "name": "임의 현장 테스트", "scope": "domestic_site",
          "business_lens": "plant", "tier": 2}],
        {"x": {"match_count": 1, "latest_published_at": "2020-01-01T00:00:00+09:00",
               "article_keys": ["k"]}})
    check("2c: now 미주입(현재 시각) 동작 + 요약 키 존재",
          "recent_matched_nodes" in t and "old_matched_nodes" in t)
    nx = _nodes(t).get("x") or {}
    check("2c+: 아주 오래된(2020) 매칭은 old → '!' 없음",
          nx.get("match_freshness") == "old" and nx.get("alert_marker") == "")


# ---------------------------------------------------------------------------
# 3 · 템플릿 UI — 트리 렌더 + 레전드 + 과거 매칭 카피 + 마커 클래스
# ---------------------------------------------------------------------------

def check_template_ui() -> None:
    tpl = _read(TEMPLATE)
    check("3a: 트리 패널 DOM(siteTreePanel)", 'id="siteTreePanel"' in tpl)
    check("3b: 필터 바 DOM(siteFilterBar)", 'id="siteFilterBar"' in tpl)
    for fn in ("function showSiteTreeForLens", "function renderSiteTreeHtml",
               "function renderSiteNode", "function onSiteNodeClick"):
        check(f"3c: 렌더 함수 정의 '{fn}'", fn in tpl)
    check("3d: UI 레전드 카피 '! = 최근 공개뉴스 매칭 있음 · 위험 확정 아님'",
          "최근 공개뉴스 매칭 있음 · 위험 확정 아님" in tpl)
    check("3e: 과거 매칭 카피 '과거 매칭'", "과거 매칭" in tpl)
    check("3f: '!' 마커 스타일 클래스(.st-bang) + recent 노드 클래스",
          ".st-bang" in tpl and ".st-node.recent" in tpl)
    # is_recent_signal 로 마커를 가른다(템플릿이 리프 판정을 따름 — 자체 재계산 금지)
    check("3g: UI 마커는 is_recent_signal/alert_marker 를 따른다(자체 재계산 아님)",
          "is_recent_signal" in tpl and "n.alert_marker" in tpl)
    # SITE_TREE 는 비공개 빌드에서만 채워진다(공개=null 가드)
    check("3h: SITE_TREE 공개 빌드 null 가드(공개 대시보드 트리 비노출)",
          "MODEL.site_watch_tree || null" in tpl
          and "if (!SITE_TREE || !isSiteScope(key))" in tpl)


# ---------------------------------------------------------------------------
# 4 · 빌더 — generated_at 을 freshness now 로 주입 + 생성일 표기
# ---------------------------------------------------------------------------

def check_builder_wiring() -> None:
    src = _read(BUILDER)
    check("4a: 빌더가 tree_for_model 에 now=brief.generated_at 주입",
          'tree_for_model(all_rows, now=brief.get("generated_at"))' in src)
    check("4b: 생성 KST 메타 주입(generated_kst) — 정적 stale 날짜 대체",
          'meta["generated_kst"]' in src and "_fmt_kst_full" in src)
    check("4c: 헤더 stale 정적 날짜 치환 로직(_update_header_dates)",
          "_update_header_dates" in src and 'id="pubKst"' in src)
    # 빌더는 env/네트워크를 직접 읽지 않는다(리프 게이팅 유지) — D7-B 계약 보존
    check("4d: 빌더가 os.environ/네트워크 직접 미사용(리프가 게이팅)",
          "os.environ" not in src
          and not re.search(r"^\s*(import|from)\s+(urllib|requests|httpx|socket)\b", src, re.M))


# ---------------------------------------------------------------------------
# 5 · 프라이버시 — 공개 빌드는 트리 없음 / 미매칭 이름 비노출
# ---------------------------------------------------------------------------

def check_privacy_no_env() -> None:
    saved = os.environ.pop("SITE_WATCHLIST_PATH", None)
    try:
        check("5a: env 미설정(공개 샘플) → tree_for_model None(트리 없음)",
              sw.tree_for_model([]) is None)
    finally:
        if saved is not None:
            os.environ["SITE_WATCHLIST_PATH"] = saved


# ---------------------------------------------------------------------------
# 6 · 통합 빌드(mock · 임시 비공개) — 트리 주입 + freshness 키 + 레전드 HTML + 비노출
#     (시간 무관 속성만 단정 — 마커 값은 §1에서 결정적으로 검사)
# ---------------------------------------------------------------------------

_SECRET = "절대공개금지내부ZZX9구역"


def _temp_watchlist() -> str:
    items = {"items": [
        # 공개 mock 기사 제목("…사우디 네옴 AI 데이터센터 EPC…")을 직접 언급 → 매칭
        {"id": "t_neom", "name": "네옴 AI 데이터센터", "scope": "overseas_site",
         "business_lens": "new_energy", "tier": 1},
        {"id": "t_secret", "name": _SECRET, "scope": "domestic_site",
         "business_lens": "plant", "tier": 2},   # 미매칭(비공개) — 노출되면 안 됨
    ]}
    fd, path = tempfile.mkstemp(prefix="hdec_swtree_", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False)
    return path


def check_integration_build() -> None:
    path = _temp_watchlist()
    saved = os.environ.get("SITE_WATCHLIST_PATH")
    os.environ["SITE_WATCHLIST_PATH"] = path
    try:
        with tempfile.TemporaryDirectory(prefix="hdec_swtree_") as tmp:
            out = Path(tmp) / "dash.html"
            proc = subprocess.run([sys.executable, str(BUILDER), "--output", str(out)],
                                  cwd=ROOT, capture_output=True, text=True, timeout=300,
                                  env={**os.environ})
            if not check("6a: 비공개 빌드 동작", proc.returncode == 0 and out.exists(),
                         (proc.stderr or "")[-200:]):
                return
            html = out.read_text(encoding="utf-8")
            model = _model(html)
            tree = model.get("site_watch_tree")
            check("6b: 비공개 빌드 모델에 site_watch_tree 주입", tree is not None)
            if tree:
                check("6b+: 주입 트리에 4개 요약 카운트 키",
                      all(k in tree for k in ("total_nodes", "matched_nodes_all_time",
                                              "recent_matched_nodes", "old_matched_nodes")))
                nodes = _nodes(tree)
                check("6c: 모든 노드에 freshness 키(match_freshness/is_recent_signal/alert_marker)",
                      all("match_freshness" in n and "is_recent_signal" in n
                          and "alert_marker" in n for n in nodes.values()),
                      f"{len(nodes)} 노드")
                # '!' 는 is_recent_signal 일 때만(가짜 마커 없음)
                bad = [nid for nid, n in nodes.items()
                       if n["alert_marker"] == "!" and not n["is_recent_signal"]]
                check("6c+: '!' 노드는 모두 is_recent_signal(가짜 마커 없음)", not bad, str(bad[:3]))
                if "t_neom" in nodes:
                    info(f"네옴 노드 freshness={nodes['t_neom']['match_freshness']!r} "
                         f"marker={nodes['t_neom']['alert_marker']!r} "
                         f"kst={nodes['t_neom']['latest_kst']}")
            check("6d: HTML 에 트리 레전드 노출('최근 공개뉴스 매칭 있음')",
                  "최근 공개뉴스 매칭 있음" in html)
            check("6e: 미매칭 비공개 이름 비노출(privacy)", _SECRET not in html,
                  "비공개 이름 누출!" if _SECRET in html else "ok")
            # 생성 KST 가 stale 정적 리터럴이 아님(헤더 날짜 fix)
            check("6f: 헤더 생성 KST stale 정적값 미잔존(2026·06·22 월 07:00)",
                  "2026·06·22 월 07:00" not in html)
    finally:
        if saved is None:
            os.environ.pop("SITE_WATCHLIST_PATH", None)
        else:
            os.environ["SITE_WATCHLIST_PATH"] = saved
        os.unlink(path)


def main() -> int:
    print(f"== verify_site_watch_tree (D7-N) @ {ROOT} ==")
    check_freshness_contract()
    check_constants()
    check_template_ui()
    check_builder_wiring()
    check_privacy_no_env()
    check_integration_build()
    print()
    if _failures:
        print(f"RESULT: FAIL ({len(_failures)} 항목)")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("RESULT: PASS — 현장/조직 감시 트리 freshness('!')·생성일·프라이버시 (D7-N)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
