#!/usr/bin/env python3
"""D7-AE-RC1 verifier — 현장별 기사 연관성(site match relevance) 신뢰도 계약.

사용자 실사용 QA 실패: "현장 노드에 실제 관련 없는 뉴스가 붙어 있다. Google News query
provenance만으로 현장 기사라고 보면 안 된다." 실측 재현(공개 dashboard-latest.html):

  - 현장 "파나마 메트로 3호선" ← 기사 "대구교통공사, 파마나 모노레일 안전성 높인다"
    (대구 모노레일 안전점검 기사. 제목/스니펫 어디에도 "파나마"/"메트로 3호선" 없음.
    query="\"파나마 메트로 3호선\" 수주"로 수집됐다는 이유만으로 매칭됨)
  - 현장 "불가리아 코즐로두이 원전 7,8호기" ← 기사 "현대건설, 미 원전 시장 속도낸다…
    웨스팅하우스 자금 지원"(미국 원전시장 기사. "불가리아"/"코즐로두이" 전혀 없음.
    query="\"코즐로두이 원전\" 현대건설"로 수집됐다는 이유만으로 매칭됨)

이 verifier는 app.site_watchlist + scripts.build_static_dashboard._site_matches_for의
신뢰도 계약을 오프라인으로 잠근다(네트워크 0건):

  A. 실측 재현 — 위 두 실사례를 그대로 재생하면 매칭이 0건이어야 한다(회귀 고정).
  B. controlled fixture — "파나마 메트로 3호선"류 현장에 무관 기사가 query provenance만으로
     붙으면 FAIL. title/snippet에 현장명·별칭이 있는 controlled article은 PASS(high).
     query provenance + 식별 토큰(국가/도시/고유명) corroboration이 있으면 PASS(medium).
     query provenance only(식별 토큰 없음)는 매칭 자체가 반환되지 않는다(hidden = 아예 없음).
  C. corroboration 원시함수 — 업종/공종 일반명사(원전·플랜트·철도 등) 단독은 corroboration
     토큰으로 인정하지 않는다(같은 업종의 다른 무관 프로젝트와 구별 못 함 — 실측 코즐로두이
     사례가 바로 이 실패 패턴이었다). 국가/도시/고유 프로젝트명은 인정한다.
  D. 모델 무결성 — mock 파이프라인으로 빌드한 공개 산출물에서 site_watch_tree의 모든
     article_keys는 confidence(high/medium) threshold 이상인 provenance를 가진 행만
     가리킨다(low-confidence match가 article_keys에 새면 FAIL).

완전 OFFLINE · 네트워크 0건 · 발송 0건.
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


def _meta(query: str) -> str:
    return json.dumps({"provider": "google_news_rss", "query": query,
                        "source_url": "https://news.example.test/x",
                        "collected_at": "2026-07-02T17:21:00+09:00"}, ensure_ascii=False)


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
# A · 실측 재현(회귀 고정) — 사용자가 지적한 두 실사례
# ---------------------------------------------------------------------------

def check_real_world_regressions() -> None:
    panama_fp = {
        "title": "대구교통공사, 파마나 모노레일 안전성 높인다",
        "snippet": "대구교통공사, 파마나 모노레일 안전성 높인다 대구일보",
        "source_metadata_json": _meta('"파나마 메트로 3호선" 수주'),
    }
    kozloduy_fp = {
        "title": "현대건설, 미 원전 시장 속도낸다…웨스팅하우스 자금 지원 '호재'",
        "snippet": "현대건설, 미 원전 시장 속도낸다…웨스팅하우스 자금 지원 '호재' 글로벌이코노믹",
        "source_metadata_json": _meta('"코즐로두이 원전" 현대건설'),
    }
    check("A1: 실측 오탐 재현 — 파나마 메트로 3호선 ↔ 대구 모노레일 기사 → 매칭 0건",
          builder._site_matches_for(panama_fp) == [], str(builder._site_matches_for(panama_fp)))
    check("A2: 실측 오탐 재현 — 코즐로두이 원전 ↔ 미 원전시장 기사 → 매칭 0건",
          builder._site_matches_for(kozloduy_fp) == [],
          str(builder._site_matches_for(kozloduy_fp)))
    lens_p = builder._lens_for(panama_fp)
    lens_k = builder._lens_for(kozloduy_fp)
    check("A3: 오탐 기사 scope 렌즈(overseas_site) 미태깅",
          "overseas_site" not in lens_p and "overseas_site" not in lens_k,
          f"panama={lens_p} kozloduy={lens_k}")


# ---------------------------------------------------------------------------
# B · controlled fixture — 격리된 임시 워치리스트로 4종 케이스 검증
# ---------------------------------------------------------------------------

_FIXTURE = {"items": [
    {"id": "fx_panama", "name": "파나마 메트로 3호선", "aliases": ["파나마 메트로"],
     "scope": "overseas_site", "business_lens": "civil_infrastructure", "tier": 1},
]}


def _with_fixture_watchlist(fn):
    fd, path = tempfile.mkstemp(prefix="hdec_site_relevance_", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(_FIXTURE, f, ensure_ascii=False)
    try:
        return fn(path)
    finally:
        os.unlink(path)


def check_controlled_fixture() -> None:
    def run(path):
        item = sw.load_watchlist(path)["items"][0]
        site_query = sw._queries_for(item)[0]

        # B1: query provenance only, 완전 무관 기사(제목/스니펫에 아무 근거 없음) → FAIL(매칭 0)
        unrelated = {"title": "대구교통공사, 파마나 모노레일 안전성 높인다",
                     "snippet": "대구교통공사, 파마나 모노레일 안전성 높인다 대구일보",
                     "source_metadata_json": _meta(site_query)}
        check("B1: query-only + 무관 기사 → 매칭 없음(FAIL 판정)",
              sw.classify_site_lenses(unrelated["title"], unrelated["snippet"], path) == []
              and _query_only_match(item, unrelated, path) is None)

        # B2: title에 정식 명칭 직접 포함 → PASS(high)
        title_hit = {"title": "현대건설, 파나마 메트로 3호선 수주 확정",
                     "snippet": "", "source_metadata_json": ""}
        m2 = sw.classify_site_lenses(title_hit["title"], title_hit["snippet"], path)
        check("B2: title에 현장명 직접 포함 → PASS(high, matched_via=title)",
              bool(m2) and m2[0]["id"] == "fx_panama" and m2[0]["matched_via"] == "title")

        # B3: snippet에 별칭 직접 포함(제목엔 없음) → PASS(high)
        snippet_hit = {"title": "해외 인프라 수주 소식", "snippet": "파나마 메트로 구간 공정 순항",
                        "source_metadata_json": ""}
        m3 = sw.classify_site_lenses(snippet_hit["title"], snippet_hit["snippet"], path)
        check("B3: snippet에 별칭 직접 포함 → PASS(high, matched_via=snippet)",
              bool(m3) and m3[0]["id"] == "fx_panama" and m3[0]["matched_via"] == "snippet")

        # B4: query provenance + 식별 토큰("파나마") corroboration → PASS(medium)
        corroborated = {"title": "파나마 현지 교통 인프라 투자 확대", "snippet": "",
                         "source_metadata_json": _meta(site_query)}
        hit = sw.corroboration_hit(item, corroborated["title"] + " " + corroborated["snippet"])
        check("B4: query + 식별 토큰(파나마) corroboration → PASS(medium)", hit == "파나마",
              f"hit={hit!r}")

    def _query_only_match(item, sig, path):
        q = json.loads(sig.get("source_metadata_json") or "{}").get("query") or ""
        matched_item = sw.match_item_for_query(q, path)
        if not matched_item:
            return None
        hit = sw.corroboration_hit(matched_item, f"{sig.get('title', '')} {sig.get('snippet', '')}")
        return matched_item if hit else None

    _with_fixture_watchlist(run)


# ---------------------------------------------------------------------------
# C · corroboration 원시함수 — 업종어 단독 불인정, 국가/도시/고유명 인정
# ---------------------------------------------------------------------------

def check_corroboration_primitives() -> None:
    kozloduy = {"id": "x", "name": "불가리아 코즐로두이 원전 7,8호기 설계공사",
                "aliases": ["코즐로두이 원전"]}
    tokens = sw.corroboration_tokens(kozloduy)
    check("C1: 업종어('원전') 단독은 식별 토큰에서 제외",
          "원전" not in tokens, str(tokens))
    check("C2: 순번/숫자 토큰('7,8호기') 식별 토큰에서 제외",
          not any(any(ch.isdigit() for ch in t) for t in tokens), str(tokens))
    check("C3: 국가/고유명('불가리아'/'코즐로두이') 식별 토큰으로 인정",
          "불가리아" in tokens and "코즐로두이" in tokens, str(tokens))
    check("C4: 업종어만 있는 텍스트는 corroboration_hit 실패",
          sw.corroboration_hit(kozloduy, "국내 원전 산업 동향 발전소 건설") is None)
    check("C5: 국가명이 있는 텍스트는 corroboration_hit 성공",
          sw.corroboration_hit(kozloduy, "불가리아 현지 에너지 정책 변화") == "불가리아")

    panama = {"id": "y", "name": "파나마 메트로 3호선", "aliases": ["파나마 메트로"]}
    check("C6: 짧은 지명 토큰도 인정(파나마)",
          "파나마" in sw.corroboration_tokens(panama))


# ---------------------------------------------------------------------------
# D · 모델 무결성 — mock 빌드에서 tree article_keys가 threshold 이상만 가리킴
# ---------------------------------------------------------------------------

def check_model_integrity() -> None:
    with tempfile.TemporaryDirectory(prefix="hdec_site_relevance_model_") as tmp:
        out = Path(tmp) / "dash.html"
        proc = subprocess.run([sys.executable, str(BUILDER), "--output", str(out)],
                              cwd=ROOT, capture_output=True, text=True, timeout=300,
                              env={**os.environ})
        if not check("D1: mock 빌드 동작(오프라인)", proc.returncode == 0 and out.exists(),
                     (proc.stderr or "")[-200:]):
            return
        model = _model(out.read_text(encoding="utf-8"))

        row_confidence: dict = {}
        for row in (model.get("news_rows") or []) + (model.get("ai_rows") or []):
            prov = row.get("provenance") or {}
            key = row.get("url") or row.get("title") or ""
            conf = prov.get("site_watch_match_confidence")
            if key and conf:
                row_confidence[key] = conf
        for rows in (model.get("lens_banks") or {}).values():
            for row in rows or []:
                prov = row.get("provenance") or {}
                key = row.get("url") or row.get("title") or ""
                conf = prov.get("site_watch_match_confidence")
                if key and conf:
                    row_confidence[key] = conf

        tree = model.get("site_watch_tree") or {}
        bad_refs = []
        total_keys = 0
        for sc in (tree.get("by_scope") or {}).values():
            for g in sc.get("groups") or []:
                for n in g.get("nodes") or []:
                    for k in n.get("article_keys") or []:
                        total_keys += 1
                        conf = row_confidence.get(k)
                        if conf not in ("high", "medium"):
                            bad_refs.append((n.get("id"), k, conf))
        check("D2: site_watch_tree article_keys는 전부 confidence high/medium 근거만",
              not bad_refs, str(bad_refs[:5]))
        info(f"트리 article_keys 총 {total_keys}건 점검(mock)")


DASHBOARD = ROOT / "docs" / "daily" / "dashboard-latest.html"


def check_committed_integrity() -> None:
    """커밋된 공개 산출물(있으면)도 동일 무결성 — mock D2와 같은 검사를 live 산출물에."""
    if not DASHBOARD.exists():
        info("커밋된 공개 대시보드 없음 — SKIP")
        return
    model = _model(DASHBOARD.read_text(encoding="utf-8"))
    row_confidence: dict = {}
    for row in (model.get("news_rows") or []) + (model.get("ai_rows") or []):
        prov = row.get("provenance") or {}
        key = row.get("url") or row.get("title") or ""
        conf = prov.get("site_watch_match_confidence")
        if key and conf:
            row_confidence[key] = conf
    for rows in (model.get("lens_banks") or {}).values():
        for row in rows or []:
            prov = row.get("provenance") or {}
            key = row.get("url") or row.get("title") or ""
            conf = prov.get("site_watch_match_confidence")
            if key and conf:
                row_confidence[key] = conf
    tree = model.get("site_watch_tree") or {}
    bad_refs = []
    total_keys = 0
    for sc in (tree.get("by_scope") or {}).values():
        for g in sc.get("groups") or []:
            for n in g.get("nodes") or []:
                for k in n.get("article_keys") or []:
                    total_keys += 1
                    conf = row_confidence.get(k)
                    if conf not in ("high", "medium"):
                        bad_refs.append((n.get("id"), k, conf))
    check("D3: 커밋된 공개 산출물도 site_watch_tree article_keys가 high/medium 근거만",
          not bad_refs, str(bad_refs[:5]))
    info(f"커밋 산출물 트리 article_keys 총 {total_keys}건 점검")


def main() -> int:
    print(f"== verify_site_match_relevance (D7-AE-RC1) @ {ROOT} ==")
    check_real_world_regressions()
    check_controlled_fixture()
    check_corroboration_primitives()
    check_model_integrity()
    check_committed_integrity()
    print()
    if _failures:
        print(f"RESULT: FAIL ({len(_failures)} 항목)")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("RESULT: PASS — 현장 기사 연관성 신뢰도 계약(query-only 배제 · corroboration · "
          "모델 무결성) (D7-AE-RC1)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
