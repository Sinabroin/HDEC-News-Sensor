#!/usr/bin/env python3
"""D7-AE verifier — 현장별 기사 센싱 (site-by-site article sensing).

사용자 QA: "현장 위치리스트는 보이지만 각 현장별 관련 기사가 사실상 비어 있다."
근본 원인은 (1) 공개 빌드에서 사이트 쿼리가 0개(수집 미참여), (2) 매칭이 제목
부분문자열뿐이라 사이트 쿼리로 수집된 기사도 제목에 공식 명칭이 없으면 버려짐.

이 verifier는 D7-AE 수정 계약을 오프라인으로 잠근다(네트워크 0건 · 발송 0건):

  1. 리프 수집 그룹 — 추적 공개 목록(data/site_watchlist.public.json)에서 bounded
     site:* 그룹이 파생된다(이미 커밋·공개 렌더된 이름 = 신규 노출 0). 쿼리는 전부
     공개 목록 항목에서 나온다(비공개 이름 0건).
  2. 쿼리 역인덱스 — 리프가 만든 쿼리를 그대로 되돌리면 해당 항목이 나온다.
     무관 쿼리/빈 쿼리는 None(가짜 매칭 없음).
  3. 빌더 매칭 판정점(_site_matches_for) — (a) 제목 직접 언급 = title 매칭,
     (b) 사이트 쿼리 수집 provenance = query 매칭, (c) 무관 기사 = 매칭 없음.
     매칭 행에는 site_watch provenance + scope/business 렌즈가 붙는다.
  4. controlled fixture 통합(mock 파이프라인 · 오프라인) — fixture 현장이 mock 기사와
     매칭되어 model.site_watch_tree에 match_count>0 + article_keys가 생기고,
     lens_banks[scope]가 그 article_keys의 실제 행으로 동기화된다. 미매칭 fixture
     이름은 HTML에 노출되지 않는다(내부 목록 프라이버시 · D7-N 복원).
  5. 템플릿 UI — 현장 클릭 → 기사 리스트(매칭 상태)와 "현재 매칭된 기사 없음"
     (빈 상태) 둘 다 처리한다.
  6. 커밋 산출물 무결성 — site_watch_tree의 article_keys는 모델에 실존하는 행을
     가리킨다(깨진 참조 = 가짜 카운트 금지). 매칭 수는 정보로만 보고한다(그날
     뉴스에 현장 언급이 없으면 0이 정직한 값이다).
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
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from app import site_watchlist as sw  # noqa: E402
import build_static_dashboard as builder  # noqa: E402

BUILDER = ROOT / "scripts" / "build_static_dashboard.py"
TEMPLATE = ROOT / "templates" / "dashboard_preview.html"
DASHBOARD = ROOT / "docs" / "daily" / "dashboard-latest.html"
PUBLIC_LIST = ROOT / "data" / "site_watchlist.public.json"

_failures: list = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    line = f"[{'PASS' if ok else 'FAIL'}] {name}"
    if detail:
        line += f" — {detail}"
    print(line)
    if not ok:
        _failures.append(name)
    return ok


def info(msg: str) -> None:
    print(f"[INFO] {msg}")


def _model(html: str) -> dict:
    m = re.search(r'<script type="application/json" id="preview-model">(.*?)</script>',
                  html, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except ValueError:
        return {}


def _tree_nodes(tree: dict) -> dict:
    out = {}
    for sc in (tree.get("by_scope") or {}).values():
        for g in sc.get("groups") or []:
            for n in g.get("nodes") or []:
                out[n.get("id")] = n
    return out


# ---------------------------------------------------------------------------
# 1 · 리프 수집 그룹 — 공개 목록 파생(bounded · 공개 이름만)
# ---------------------------------------------------------------------------

def check_public_collection_groups() -> None:
    saved = os.environ.pop("SITE_WATCHLIST_PATH", None)
    try:
        wl = sw.load_watchlist()
        if not check("1a: 추적 공개 목록 로드(source=public)", wl["source"] == "public",
                     f"source={wl['source']}"):
            return
        groups = sw.collection_query_groups(rotation_key=0)
        check("1b: 공개 빌드도 site:* 수집 그룹 파생(D7-AE — 수집 참여)",
              bool(groups) and all(str(g.get("name") or "").startswith("site:")
                                   for g in groups),
              f"{len(groups)} 그룹")
        queries = [q for g in groups for q in (g.get("queries") or [])]
        check("1c: 총 쿼리 bounded(기본 상한 이하)",
              0 < len(queries) <= sw.DEFAULT_MAX_QUERIES, f"{len(queries)}개")
        allowed = {q.strip().casefold() for it in wl["items"]
                   for q in sw._queries_for(it)}
        foreign = [q for q in queries if q.strip().casefold() not in allowed]
        check("1d: 모든 쿼리가 공개 목록 항목에서 파생(비공개/가짜 이름 0건)",
              not foreign, str(foreign[:2]))
        check("1e: 쿼리에 URL/스킴 없음(검색어만)",
              all("http" not in q.lower() for q in queries))
        scopes = {g.get("scope") for g in groups}
        check("1f: 국내·해외 scope 모두 등장(breadth 라운드로빈)",
              "domestic_site" in scopes
              and any(str(s).startswith("overseas") for s in scopes),
              str(sorted(str(s) for s in scopes)))
    finally:
        if saved is not None:
            os.environ["SITE_WATCHLIST_PATH"] = saved


# ---------------------------------------------------------------------------
# 2 · 쿼리 역인덱스 — roundtrip / 무관 쿼리 None
# ---------------------------------------------------------------------------

def check_query_index() -> None:
    saved = os.environ.pop("SITE_WATCHLIST_PATH", None)
    try:
        wl = sw.load_watchlist()
        items = wl["items"]
        if not items:
            check("2a: 공개 목록 항목 존재", False)
            return
        item = items[0]
        q = sw._queries_for(item)[0]
        got = sw.match_item_for_query(q)
        check("2a: 리프 파생 쿼리 roundtrip → 해당 항목",
              bool(got) and got.get("id") == item["id"],
              f"{q!r} -> {got.get('id') if got else None}")
        check("2b: 대소문자 무시(casefold) roundtrip",
              (sw.match_item_for_query(q.upper()) or {}).get("id") == item["id"])
        check("2c: 무관 쿼리 → None(가짜 매칭 없음)",
              sw.match_item_for_query('"존재하지않는현장QZX" 현대건설') is None)
        check("2d: 빈 쿼리 → None", sw.match_item_for_query("") is None
              and sw.match_item_for_query(None) is None)
    finally:
        if saved is not None:
            os.environ["SITE_WATCHLIST_PATH"] = saved


# ---------------------------------------------------------------------------
# 3 · 빌더 매칭 판정점 — title / query / 무관 3종
# ---------------------------------------------------------------------------

def _meta_json(query: str) -> str:
    return json.dumps({
        "provider": "google_news_rss", "query": query,
        "source_url": "https://news.example.test/a", "collected_at": "2026-07-01T09:00:00+09:00",
        "provider_response_id": "t1"}, ensure_ascii=False)


def check_builder_matching() -> None:
    saved = os.environ.pop("SITE_WATCHLIST_PATH", None)
    try:
        wl = sw.load_watchlist()
        item = next((i for i in wl["items"] if i["scope"] == "domestic_site"), None)
        if not check("3a: 공개 국내 현장 항목 존재", item is not None):
            return
        site_query = sw._queries_for(item)[0]
        # (a) 사이트 쿼리 provenance 매칭 — 제목은 공식 명칭을 반복하지 않는다.
        sig_q = {"title": "현장 인근 교통 대책 발표…시공사 협의 착수",
                 "url": "https://news.example.test/q1",
                 "published_at": "2026-07-01T09:00:00+09:00",
                 "source_metadata_json": _meta_json(site_query)}
        got = builder._site_matches_for(sig_q)
        check("3b: 사이트 쿼리 수집 provenance → query 매칭",
              [ (m.get("id"), m.get("matched_via")) for m in got ]
              == [(item["id"], "query")], str(got))
        lens = builder._lens_for(sig_q)
        check("3c: query 매칭 행에 scope 렌즈 태깅", item["scope"] in lens, str(lens))
        prov = (builder._row_from_signal(sig_q).get("provenance") or {})
        check("3d: provenance에 site_watch_match + matched_via=query",
              prov.get("site_watch_match") is True
              and prov.get("site_watch_id") == item["id"]
              and prov.get("site_watch_matched_via") == "query")
        # (b) 제목 직접 언급 = title 매칭(기존 규칙 불변).
        sig_t = {"title": f"{item['name']} 공정 점검", "url": "https://news.example.test/t1"}
        got_t = builder._site_matches_for(sig_t)
        check("3e: 제목 직접 언급 → title 매칭",
              bool(got_t) and got_t[0].get("id") == item["id"]
              and got_t[0].get("matched_via") == "title", str(got_t[:1]))
        # (c) 무관 기사(일반 지명·무관 쿼리) → 매칭 없음(broad FP 방지).
        sig_n = {"title": "서울 아파트 분양 시장 동향", "url": "https://news.example.test/n1",
                 "source_metadata_json": _meta_json("건설 AI 동향")}
        check("3f: 무관 기사/무관 쿼리 → 매칭 없음(가짜 매칭 0)",
              builder._site_matches_for(sig_n) == [])
        prov_n = (builder._row_from_signal(sig_n).get("provenance") or {})
        check("3g: 무관 행 provenance에 site_watch 키 없음",
              "site_watch_match" not in prov_n)
    finally:
        if saved is not None:
            os.environ["SITE_WATCHLIST_PATH"] = saved


# ---------------------------------------------------------------------------
# 4 · controlled fixture 통합 빌드(mock · 오프라인) — nonzero 매칭 + 프라이버시
# ---------------------------------------------------------------------------

_UNMATCHED = "D7AE미매칭내부명XQZ구역"


def check_fixture_integration() -> None:
    fixture = {"items": [
        # 공개 mock 기사 제목("…사우디 네옴 AI 데이터센터 EPC…")을 직접 언급 → title 매칭
        {"id": "fx_neom", "name": "네옴 AI 데이터센터", "scope": "overseas_site",
         "business_lens": "new_energy", "tier": 1},
        # 어떤 mock 기사도 언급하지 않음 → 트리/HTML 비노출(내부 목록 프라이버시)
        {"id": "fx_unmatched", "name": _UNMATCHED, "scope": "domestic_site",
         "business_lens": "plant", "tier": 2},
    ]}
    fd, path = tempfile.mkstemp(prefix="hdec_d7ae_fix_", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(fixture, f, ensure_ascii=False)
    saved = os.environ.get("SITE_WATCHLIST_PATH")
    saved_expose = os.environ.pop("SITE_WATCHLIST_EXPOSE_TREE", None)
    os.environ["SITE_WATCHLIST_PATH"] = path
    try:
        with tempfile.TemporaryDirectory(prefix="hdec_d7ae_") as tmp:
            out = Path(tmp) / "dash.html"
            proc = subprocess.run([sys.executable, str(BUILDER), "--output", str(out)],
                                  cwd=ROOT, capture_output=True, text=True, timeout=300,
                                  env={**os.environ})
            if not check("4a: fixture 빌드 동작(mock · 오프라인)",
                         proc.returncode == 0 and out.exists(),
                         (proc.stderr or "")[-200:]):
                return
            html = out.read_text(encoding="utf-8")
            model = _model(html)
            tree = model.get("site_watch_tree") or {}
            nodes = _tree_nodes(tree)
            node = nodes.get("fx_neom") or {}
            check("4b: fixture 현장 match_count > 0 (모델 기준 nonzero 매칭)",
                  int(node.get("match_count") or 0) > 0,
                  f"mc={node.get('match_count')}")
            keys = node.get("article_keys") or []
            check("4c: fixture 현장 article_keys 채움", bool(keys), f"{len(keys)}건")
            banks = model.get("lens_banks") or {}
            bank_rows = banks.get("overseas_site") or []
            bank_keys = {r.get("url") or r.get("title") for r in bank_rows}
            check("4d: lens_banks[scope]가 트리 article_keys 행으로 동기화",
                  bool(bank_rows) and any(k in bank_keys for k in keys),
                  f"bank={len(bank_rows)}행")
            check("4e: 미매칭 fixture 이름 HTML 비노출(내부 목록 프라이버시)",
                  _UNMATCHED not in html)
            check("4f: 미매칭 fixture 노드 트리 비포함(EXPOSE_TREE 미설정)",
                  "fx_unmatched" not in nodes)
    finally:
        if saved is None:
            os.environ.pop("SITE_WATCHLIST_PATH", None)
        else:
            os.environ["SITE_WATCHLIST_PATH"] = saved
        if saved_expose is not None:
            os.environ["SITE_WATCHLIST_EXPOSE_TREE"] = saved_expose
        os.unlink(path)


# ---------------------------------------------------------------------------
# 5 · 템플릿 UI — 매칭 상태 + 빈 상태 둘 다 처리
# ---------------------------------------------------------------------------

def check_template_states() -> None:
    t = TEMPLATE.read_text(encoding="utf-8")
    for fn in ("function rowsForSite", "function renderSiteNewsList",
               "function onSiteNodeClick"):
        check(f"5a: 현장 클릭 파이프라인 '{fn}'", fn in t)
    check("5b: 빈 상태 카피('현재 매칭된 기사 없음') — 가짜 기사 생성 없음",
          "현재 매칭된 기사 없음" in t)
    check("5c: 노드에 매칭 기사 수 표시(st-mc)", "st-mc" in t and "match_count" in t)
    check("5d: article_keys 기반 필터(키 교집합 — 점수/랭킹 재계산 없음)",
          "article_keys" in t and "siteKeySet" in t)


# ---------------------------------------------------------------------------
# 6 · 커밋 산출물 무결성 — article_keys는 실존 행만(깨진 참조 = 가짜 카운트)
# ---------------------------------------------------------------------------

def check_committed_integrity() -> None:
    if not DASHBOARD.exists():
        info("커밋 대시보드 없음 — 무결성 검사 SKIP")
        return
    model = _model(DASHBOARD.read_text(encoding="utf-8"))
    tree = model.get("site_watch_tree")
    if not isinstance(tree, dict):
        info("커밋 모델에 site_watch_tree 없음 — SKIP(공개 트리 도입 전 산출물)")
        return
    row_keys = set()
    for r in (model.get("news_rows") or []) + (model.get("ai_rows") or []):
        row_keys.add(r.get("url") or r.get("title"))
    for rows in (model.get("lens_banks") or {}).values():
        for r in rows or []:
            row_keys.add(r.get("url") or r.get("title"))
    fr = model.get("featured_row")
    if isinstance(fr, dict):
        row_keys.add(fr.get("url") or fr.get("title"))
    nodes = _tree_nodes(tree)
    broken = [nid for nid, n in nodes.items()
              if any(k not in row_keys for k in (n.get("article_keys") or []))]
    check("6a: 커밋 트리 article_keys가 모델 실존 행만 참조(깨진 참조 0)",
          not broken, str(broken[:3]))
    matched = [n for n in nodes.values() if int(n.get("match_count") or 0) > 0]
    info(f"커밋 산출물 매칭 현장 {len(matched)}/{len(nodes)}건 — "
         "0건이어도 그날 공개뉴스에 현장 언급이 없으면 정직한 값(게이트 아님)")


def main() -> int:
    print(f"== verify_site_article_sensing (D7-AE) @ {ROOT} ==")
    check_public_collection_groups()
    check_query_index()
    check_builder_matching()
    check_fixture_integration()
    check_template_states()
    check_committed_integrity()
    print()
    if _failures:
        print(f"RESULT: FAIL ({len(_failures)} 항목)")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("RESULT: PASS — 현장별 기사 센싱(공개 목록 수집 참여 · 쿼리 provenance 매칭 · "
          "fixture nonzero · 빈 상태 정직) (D7-AE)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
