"""P0-D2 — 주요 출처 커버리지 & 임원 필터링 감사 (markdown 리포트 생성).

coverage(폭넓은 수집)와 ranking(임원 관련성 필터)을 분리해 점검한다:
- 어떤 major 출처가 커버됐는가 / 누락됐는가
- 어떤 기사가 Google-only / Naver-only / both인가
- 수집됐지만 우선순위에서 내려간(filtered down) major 출처 기사는 무엇인가
- 무엇이 어떤 이유로 제외됐는가

경계/안전 원칙:
- 저장소의 radar.db를 절대 건드리지 않는다 — 매 실행마다 임시 DB에 파이프라인을 새로 돌린다.
- 네트워크 IO는 provider leaf(live_collector / naver_news_provider)가 소유한다. 이 스크립트는
  그들을 호출만 한다. 네트워크/자격증명이 없으면 정직하게 skip을 보고하고 절대 가짜 성공/가짜
  수치를 만들지 않는다. "기사가 없다"가 아니라 "설정된 provider/쿼리로 발견되지 않았다"고 적는다.
- 비밀값(자격증명)을 print/log/직렬화하지 않는다. provider 상태는 disabled/skipped/active/error만 노출한다.
- 본문 전문을 싣지 않는다 (제목/출처/링크/점수/등급 등 파생 요약만).

사용법:
    NEWS_MODE=live python3 scripts/audit_major_source_coverage.py            # stdout
    NEWS_MODE=live python3 scripts/audit_major_source_coverage.py --output FILE.md
    # 로컬에서 Naver 자격증명이 없으면 Naver는 skipped로 보고된다 (Google만 수집/감사).
"""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

PROVIDER_GOOGLE = "google_news_rss"
PROVIDER_NAVER = "naver_news_api"
ROSTER_PATH = REPO_ROOT / "data" / "major_source_roster.json"

# ---- 순수 함수 (네트워크 없이 검증 가능) -------------------------------------

def load_roster(path=None) -> list[dict]:
    """로스터 JSON을 [{source, tier}] 평탄 리스트로 로드한다 (없거나 깨지면 빈 리스트)."""
    p = Path(path) if path else ROSTER_PATH
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    tiers = data.get("tiers") if isinstance(data, dict) else None
    if not isinstance(tiers, dict):
        return []
    out = []
    for tier, names in tiers.items():
        for name in names or []:
            if isinstance(name, str) and name.strip():
                out.append({"source": name.strip(), "tier": tier})
    return out


def providers_in(token: str) -> set:
    """source_metadata.provider 결합 토큰('google_news_rss+naver_news_api')을 집합으로."""
    return {p for p in (token or "").split("+") if p}


def provider_label(token: str) -> str:
    """provider 토큰 → 'google' | 'naver' | 'both' | '기타' (표시용)."""
    p = providers_in(token)
    has_g, has_n = PROVIDER_GOOGLE in p, PROVIDER_NAVER in p
    if has_g and has_n:
        return "both"
    if has_n:
        return "naver"
    if has_g:
        return "google"
    return "기타"


def match_roster_source(source: str, roster: list[dict]) -> dict | None:
    """기사 source가 로스터의 어떤 major 출처에 해당하는지 (양방향 부분일치)."""
    src = (source or "").strip()
    if not src:
        return None
    for entry in roster:
        name = entry["source"]
        if name and (name in src or src in name):
            return entry
    return None


def coverage_key(title: str, url: str) -> str:
    """provider 간 동일 사건 판정 키 — normalized_title 우선, 없으면 url_hash (collector와 동일)."""
    from app import collector
    return collector.normalize_title(title or "") or collector.make_url_hash(url or "")


def coverage_sets(google_rows: list[dict], naver_rows: list[dict]) -> dict:
    """raw provider 결과로 google_only / naver_only / both 키 집합을 만든다."""
    g = {coverage_key(r.get("title", ""), r.get("url", "")) for r in google_rows}
    n = {coverage_key(r.get("title", ""), r.get("url", "")) for r in naver_rows}
    g.discard("")
    n.discard("")
    return {"google_only": g - n, "naver_only": n - g, "both": g & n,
            "google_total": len(g), "naver_total": len(n)}


# ---- markdown 조립 (순수) ----------------------------------------------------

def _md_escape(text) -> str:
    """markdown 표 셀 안에서 파이프가 칸을 깨지 않게 escape한다."""
    return str(text or "").replace("|", "\\|").replace("\n", " ").strip()


def render_markdown(audit: dict) -> str:
    """audit 데이터 구조체를 markdown 리포트로 렌더한다 (순수 — 네트워크 없음)."""
    ps = audit["provider_status"]
    summ = audit["summary"]
    lines = [
        "# HDEC Executive Radar — 주요 출처 커버리지 & 임원 필터링 감사 (P0-D2)",
        "",
        f"- 생성(KST): {audit.get('generated_at', '-')}",
        f"- 뉴스 모드: {audit.get('news_mode', '-')}  ·  로스터 출처 {audit.get('roster_size', 0)}곳",
        "- 본 감사는 coverage(수집)와 ranking(임원 관련성)을 분리해 점검합니다. "
        "로스터에 hit가 없다는 것은 '관련 기사가 없음'을 보장하지 않고 "
        "'설정된 provider/쿼리로 발견되지 않음'을 뜻합니다.",
        "",
        "## 1. Provider 상태",
        "",
        "| provider | status |",
        "| --- | --- |",
        f"| google_news_rss | {_md_escape(ps.get('google_news_rss'))} |",
        f"| naver_news_api | {_md_escape(ps.get('naver_news_api'))} |",
        "",
        "## 2. 수집 요약",
        "",
        "| 항목 | 값 |",
        "| --- | --- |",
        f"| collected_total (provider 원시 합계) | {summ.get('collected_total', 0)} |",
        f"| google_only | {summ.get('google_only', 0)} |",
        f"| naver_only | {summ.get('naver_only', 0)} |",
        f"| both | {summ.get('both', 0)} |",
        f"| deduped_total (교차 dedup 후) | {summ.get('deduped_total', 0)} |",
        f"| executive_signals (제외 제외) | {summ.get('executive_signals', 0)} |",
        f"| top_display (즉시 후보 Top) | {summ.get('top_display', 0)} |",
        f"| observed (즉시/중요/관찰) | {summ.get('observed', 0)} |",
        f"| excluded/background (참고·제외) | {summ.get('excluded', 0)} |",
    ]
    if audit.get("collection_note"):
        lines += ["", f"> {audit['collection_note']}"]

    # 3. 주요 출처 커버리지
    lines += ["", "## 3. 주요 출처 커버리지", "",
              "| source | tier | covered | provider | top | observed | excluded | coverage_status | latest_title |",
              "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for r in audit["coverage_rows"]:
        lines.append(
            f"| {_md_escape(r['source'])} | {_md_escape(r['tier'])} | {r['covered_count']} "
            f"| {_md_escape(r['provider'])} | {r['top_count']} | {r['observed_count']} "
            f"| {r['excluded_count']} | {_md_escape(r['coverage_status'])} "
            f"| {_md_escape(r['latest_title'])} |")

    # 4. Naver-only 발견
    lines += ["", "## 4. Naver-only 발견", ""]
    if audit["naver_only_findings"]:
        lines += ["| title | source | published_at | grade | 사유 | url |",
                  "| --- | --- | --- | --- | --- | --- |"]
        for f in audit["naver_only_findings"]:
            lines.append(
                f"| {_md_escape(f['title'])} | {_md_escape(f['source'])} "
                f"| {_md_escape(f['published_at'])} | {_md_escape(f['grade'])} "
                f"| {_md_escape(f['reason'])} | {_md_escape(f['url'])} |")
    else:
        lines.append("- Naver-only 기사 없음 (Naver provider가 disabled/skipped이거나 "
                     "교차 dedup 결과 Naver 단독 기사가 없음 — 누락 보장이 아님).")

    # 5. 우선순위에서 내려간 major 출처 기사
    lines += ["", "## 5. 주요 출처인데 우선순위에서 내려간 기사", ""]
    if audit["filtered_down"]:
        lines += ["| title | source | score | grade | not promoted because |",
                  "| --- | --- | --- | --- | --- |"]
        for f in audit["filtered_down"]:
            lines.append(
                f"| {_md_escape(f['title'])} | {_md_escape(f['source'])} "
                f"| {_md_escape(f['score'])} | {_md_escape(f['grade'])} "
                f"| {_md_escape(f['reason'])} |")
    else:
        lines.append("- 우선순위에서 내려간 major 출처 기사 없음 (또는 수집된 major 출처 기사 없음).")

    # 6. 가능한 커버리지 공백
    lines += ["", "## 6. 가능한 커버리지 공백", ""]
    gaps = audit["coverage_gaps"]
    if gaps:
        lines.append("아래 로스터 출처는 이번 실행에서 hit가 없었습니다. 이는 '관련 기사가 "
                     "없음'을 보장하지 않으며, provider 누락·쿼리 공백·해당 매체의 무관 기사일 수 "
                     "있습니다 (절대적 부재 아님):")
        lines.append("")
        for g in gaps:
            lines.append(f"- {_md_escape(g['source'])} ({_md_escape(g['tier'])}) — {_md_escape(g['coverage_status'])}")
    else:
        lines.append("- 로스터의 모든 출처가 1건 이상 커버됨 (또는 provider가 skipped이라 공백 판정 보류).")
    lines += ["", "---",
              "_커버리지 로스터는 품질 보증이 아니라 점검용입니다. 전체 언론 커버리지를 "
              "보장하지 않으며, 원문 본문 크롤링은 하지 않습니다 (제목/요약 메타데이터만)._", ""]
    return "\n".join(lines)


# ---- 등급 분류 (순수, scoring 상수 기반) -------------------------------------

def grade_bucket(alert_grade: str, top_ids: set, article_id: str):
    """기사 등급을 (is_top, is_observed, is_excluded)로 분류한다."""
    from app import scoring
    is_top = article_id in top_ids
    is_excluded = alert_grade in (None, scoring.GRADE_EXCLUDED)
    is_observed = (not is_excluded)
    return is_top, is_observed, is_excluded


# ---- live 수집 + 파이프라인 드라이버 (네트워크는 provider leaf가 소유) ----------

def _bootstrap_runtime():
    tmp = tempfile.TemporaryDirectory(prefix="hdec_coverage_")
    os.environ["DB_PATH"] = os.path.join(tmp.name, "coverage.db")
    os.environ.setdefault("APP_MODE", "mock")
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from app import (briefing, collector, config, db, insight,
                     live_collector, naver_news_provider, scoring)
    return {
        "briefing": briefing, "collector": collector, "config": config, "db": db,
        "insight": insight, "live_collector": live_collector,
        "naver_news_provider": naver_news_provider, "scoring": scoring,
        "_tmp": tmp,
    }


def collect_providers(m) -> dict:
    """두 provider를 직접 호출해 raw 결과 + 상태를 모은다 (오프라인 안전)."""
    lc, nv = m["live_collector"], m["naver_news_provider"]
    google_rows, google_status = [], "skipped"
    try:
        google_rows = lc.fetch_all()
        google_status = "active" if google_rows else "skipped"
    except Exception:  # noqa: BLE001 — 네트워크 차단 등은 skipped로 정직 보고
        google_rows, google_status = [], "error"
    try:
        naver_result = nv.fetch()
    except Exception:  # noqa: BLE001
        naver_result = {"status": nv.STATUS_ERROR, "articles": []}
    return {
        "google_rows": google_rows,
        "google_status": google_status,
        "naver_rows": naver_result.get("articles") or [],
        "naver_status": naver_result.get("status"),
    }


def run_audit() -> dict:
    """live 수집 → 교차 dedup → 임시 DB 파이프라인 → 감사 데이터 구조체를 만든다."""
    m = _bootstrap_runtime()
    collector, db, scoring, briefing = (m["collector"], m["db"], m["scoring"],
                                        m["briefing"])
    roster = load_roster()

    prov = collect_providers(m)
    google_rows, naver_rows = prov["google_rows"], prov["naver_rows"]
    sets = coverage_sets(google_rows, naver_rows)
    combined = collector.merge_provider_articles(google_rows + naver_rows)

    # 임시 DB에 정규화/저장 → 점수 → insight → brief (저장소 radar.db 미접촉).
    db.init_db()
    deduped_total = inserted = 0
    brief = None
    scored = []
    if combined:
        _, deduped_total, inserted = collector._ingest(combined, "Live RSS")
        scoring.score_all()
        m["insight"].generate_all()
        brief = briefing.build_brief(news_provenance={
            "news_source": "audit", "fallback_used": False, "attempted_mode": "live"})
        scored = db.fetch_articles_with_scores()

    top_ids = {s.get("article_id") for s in (brief or {}).get("top_immediate_signals", [])}

    # 등급 집계
    observed = excluded = 0
    for row in scored:
        _, is_obs, is_exc = grade_bucket(row.get("alert_grade"), top_ids, row["id"])
        observed += 1 if is_obs else 0
        excluded += 1 if is_exc else 0

    coverage_rows = _build_coverage_rows(scored, roster, top_ids, m)
    naver_only_findings = _build_naver_only(scored, sets, top_ids, m)
    filtered_down = _build_filtered_down(scored, roster, top_ids, m)
    coverage_gaps = _build_gaps(coverage_rows, roster, prov)

    providers_ran = prov["google_status"] == "active" or prov["naver_status"] == "active"
    collection_note = None
    if not combined:
        collection_note = ("설정된 provider/쿼리로 수집된 기사가 없습니다 "
                           "(네트워크 차단 또는 자격증명 부재 가능). 이는 '관련 기사가 없음'을 "
                           "뜻하지 않습니다 — provider 상태를 확인하세요.")

    return {
        "generated_at": db.now_iso(),
        "news_mode": m["config"].NEWS_MODE,
        "roster_size": len(roster),
        "provider_status": {
            "google_news_rss": prov["google_status"],
            "naver_news_api": prov["naver_status"],
        },
        "summary": {
            "collected_total": len(google_rows) + len(naver_rows),
            "google_only": len(sets["google_only"]),
            "naver_only": len(sets["naver_only"]),
            "both": len(sets["both"]),
            "deduped_total": deduped_total or len(combined),
            "executive_signals": observed,
            "top_display": len(top_ids),
            "observed": observed,
            "excluded": excluded,
        },
        "coverage_rows": coverage_rows,
        "naver_only_findings": naver_only_findings,
        "filtered_down": filtered_down,
        "coverage_gaps": coverage_gaps,
        "collection_note": collection_note,
        "providers_ran": providers_ran,
    }


def _provider_token(row: dict) -> str:
    try:
        meta = json.loads(row.get("source_metadata_json") or "{}")
    except ValueError:
        return ""
    return meta.get("provider") or ""


def _short_reason(row: dict, promoted: bool) -> str:
    reason = (row.get("scoring_reason") or "").strip()
    if promoted:
        return reason or "임원 관련성 충족 — 상위 노출"
    return reason or "임원 관련성/우선순위 낮음 — 상위 미노출"


def _build_coverage_rows(scored, roster, top_ids, m) -> list[dict]:
    """로스터 출처별 커버리지 집계 행 (covered/top/observed/excluded/provider/최신 제목)."""
    agg = {e["source"]: {"source": e["source"], "tier": e["tier"], "covered_count": 0,
                         "top_count": 0, "observed_count": 0, "excluded_count": 0,
                         "providers": set(), "latest_title": "", "latest_pub": ""}
           for e in roster}
    for row in scored:
        entry = match_roster_source(row.get("source"), roster)
        if not entry:
            continue
        a = agg[entry["source"]]
        a["covered_count"] += 1
        a["providers"] |= providers_in(_provider_token(row))
        is_top, is_obs, is_exc = grade_bucket(row.get("alert_grade"), top_ids, row["id"])
        a["top_count"] += 1 if is_top else 0
        a["observed_count"] += 1 if is_obs else 0
        a["excluded_count"] += 1 if is_exc else 0
        pub = row.get("published_at") or ""
        if pub >= a["latest_pub"]:
            a["latest_pub"], a["latest_title"] = pub, (row.get("title") or "")
    rows = []
    for a in agg.values():
        if a["covered_count"]:
            p = a["providers"]
            prov = ("both" if PROVIDER_GOOGLE in p and PROVIDER_NAVER in p
                    else "naver" if PROVIDER_NAVER in p
                    else "google" if PROVIDER_GOOGLE in p else "기타")
            status = "covered"
        else:
            prov, status = "-", "no_result / query_gap"
        rows.append({**a, "provider": prov, "coverage_status": status,
                     "latest_title": a["latest_title"][:60]})
    # 커버된 것 먼저, 그다음 tier/source 정렬
    rows.sort(key=lambda r: (r["covered_count"] == 0, r["tier"], r["source"]))
    for r in rows:
        r.pop("providers", None)
        r.pop("latest_pub", None)
    return rows


def _build_naver_only(scored, sets, top_ids, m) -> list[dict]:
    """Naver-only 키에 해당하는 저장 기사를 등급/사유와 함께 모은다 (최대 25건)."""
    out = []
    for row in scored:
        key = coverage_key(row.get("title", ""), row.get("url", ""))
        token = _provider_token(row)
        # naver-only: 커버리지 셋이 naver_only이거나, 저장 토큰이 naver 단독
        if key not in sets["naver_only"] and providers_in(token) != {PROVIDER_NAVER}:
            continue
        is_top, is_obs, is_exc = grade_bucket(row.get("alert_grade"), top_ids, row["id"])
        out.append({
            "title": (row.get("title") or "")[:70],
            "source": row.get("source") or "출처 미상",
            "published_at": (row.get("published_at") or "")[:10],
            "grade": row.get("alert_grade") or "미채점",
            "reason": _short_reason(row, is_top or is_obs)[:80],
            "url": row.get("url") or "",
        })
        if len(out) >= 25:
            break
    return out


def _build_filtered_down(scored, roster, top_ids, m) -> list[dict]:
    """major 출처인데 top에 못 든 기사 (observed지만 비-top, 또는 excluded) — 최대 25건."""
    out = []
    for row in scored:
        if not match_roster_source(row.get("source"), roster):
            continue
        is_top, _is_obs, _is_exc = grade_bucket(row.get("alert_grade"), top_ids, row["id"])
        if is_top:
            continue  # top은 '내려간' 것이 아니다
        score = row.get("final_score")
        out.append({
            "title": (row.get("title") or "")[:70],
            "source": row.get("source") or "출처 미상",
            "score": f"{score:.1f}" if isinstance(score, (int, float)) else "-",
            "grade": row.get("alert_grade") or "미채점",
            "reason": _short_reason(row, False)[:90],
        })
        if len(out) >= 25:
            break
    return out


def _build_gaps(coverage_rows, roster, prov) -> list[dict]:
    """hit가 없는 로스터 출처 — provider가 모두 skip이면 'provider_skipped'로 정직 표기."""
    providers_ran = prov["google_status"] == "active" or prov["naver_status"] == "active"
    gaps = []
    for r in coverage_rows:
        if r["covered_count"] == 0:
            status = "no_result / query_gap" if providers_ran else "provider_skipped"
            gaps.append({"source": r["source"], "tier": r["tier"],
                         "coverage_status": status})
    return gaps


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="HDEC Executive Radar — 주요 출처 커버리지 감사 (발송 없음, repo DB 미접촉)")
    parser.add_argument("--output", metavar="PATH",
                        help="markdown 리포트를 PATH에 저장한다 (미지정 시 stdout)")
    parser.add_argument("--json", action="store_true",
                        help="감사 데이터 구조체를 JSON으로 출력한다 (기계 검증용)")
    args = parser.parse_args(argv)

    audit = run_audit()
    if args.json:
        print(json.dumps(audit, ensure_ascii=False, indent=2))
        return 0

    md = render_markdown(audit)
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md, encoding="utf-8")
        print(f"coverage audit written: {out} ({len(md)} chars) "
              f"providers={audit['provider_status']}")
    else:
        print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
