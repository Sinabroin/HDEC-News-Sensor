"""D3N verifier — live search topic mix and executive relevance tuning.

Deterministic, no network, temp DB only.

Checks that the live-search mix is balanced across opportunity, risk/regulation,
technology, and weak/noise suppression without depending on current RSS results.
"""

import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "data" / "live_news_sources.json"
RADAR_DB = ROOT / "radar.db"

RISK_QUERIES = {
    "현대건설 중대재해",
    "현대건설 하자",
    "현대건설 품질",
    "현대건설 소송",
    "현대건설 벌점",
    "현대건설 영업정지",
    "현대건설 입찰제한",
    "현대건설 국토부",
    "현대건설 고용부",
    "건설사 중대재해 특별감독",
    "국토부 건설사 벌점 입찰제한",
    "고용부 건설현장 특별감독",
    "건설현장 안전규제 의무화",
}

TOP_KEYS = {
    "top_imm": "top_immediate_signals",
    "top_new": "top_new_issues",
    "hdec": "hdec_direct_signals",
    "ai": "ai_radar_signals",
    "biz": "business_signals",
    "risk": "risk_regulation_signals",
    "comp": "competitor_supply_signals",
    "macro": "macro_economy_signals",
}

TOP_NOISE_SURFACES = ("top_imm", "top_new", "hdec", "ai", "biz", "risk")
CLUSTER_CAPS = {
    "nuclear_smr_project": 3,
    "ai_datacenter_power": 3,
    "smart_construction_challenge": 2,
}

FIX = [
    {"id": "mix_hdec_nuke", "source": "연합뉴스",
     "title": "현대건설, 웨스팅하우스와 네덜란드 원전 EPC 수주 협력 강화",
     "snippet": "현대건설이 웨스팅하우스와 유럽 네덜란드 원전 EPC 파이프라인을 점검한다."},
    {"id": "mix_hdec_city", "source": "대한경제",
     "title": "현대건설, 압구정 재건축 도시정비 수주전 참여",
     "snippet": "현대건설이 국내 정비사업 수주 경쟁에서 점유율 확대를 노린다."},
    {"id": "mix_hdec_dc", "source": "전자신문",
     "title": "현대건설, AI 데이터센터 EPC 전력 인프라 수주 전략 확대",
     "snippet": "현대건설이 AI 데이터센터 EPC와 전력 인프라 설계 역량을 강화한다."},
    {"id": "mix_customer_ai", "source": "한국경제",
     "title": "현대건설, 생성형 AI 분양 상담사 도입…청약 상담 자동화",
     "snippet": "현대건설이 생성형 AI 상담사로 고객 응대와 분양 상담 운영을 자동화한다."},
    {"id": "mix_stock", "source": "머니투데이",
     "title": "AI 데이터센터 전력망 수혜주 급등…건설 관련주 투자포인트",
     "snippet": "데이터센터 전력망 수혜주와 관련 종목의 주가 급등 가능성을 짚었다."},
    {"id": "mix_supplier", "source": "한국경제",
     "title": "가온전선, AI 데이터센터 전력 케이블 공급 확대",
     "snippet": "가온전선이 데이터센터 전력 케이블과 버스덕트 공급을 확대한다."},
    {"id": "mix_risk_severe", "source": "서울경제",
     "title": "현대건설 현장 중대재해 발생…고용부 특별감독 착수",
     "snippet": "현대건설 현장에서 중대재해가 발생해 고용노동부가 특별감독에 착수했다."},
    {"id": "mix_reg_policy", "source": "연합뉴스",
     "title": "국토부, 건설사 벌점·입찰제한 기준 강화",
     "snippet": "국토교통부가 건설사 벌점과 공공입찰 입찰제한 기준 강화를 추진한다."},
    {"id": "mix_defect", "source": "한국경제",
     "title": "현대건설 시공 단지 하자 논란…품질 점검 확대",
     "snippet": "현대건설 시공 단지에서 하자 논란이 제기돼 품질 점검이 확대됐다."},
    {"id": "mix_nuke_generic1", "source": "서울경제",
     "title": "네덜란드 원전 후보지 논의 본격화…웨스팅하우스 협력 주목",
     "snippet": "유럽 원전과 SMR 발주 환경 변화에 건설 EPC 업계가 주목한다."},
    {"id": "mix_nuke_generic2", "source": "매일경제",
     "title": "SMR 특별법 논의 속도…원전 EPC 밸류체인 기대",
     "snippet": "SMR 특별법과 원전 EPC 밸류체인 논의가 이어지고 있다."},
    {"id": "mix_dc_policy", "source": "전자신문",
     "title": "AI 데이터센터 전력망·냉각 솔루션 경쟁…건설사 EPC 기회",
     "snippet": "AI 데이터센터 전력 인프라와 냉각 솔루션 경쟁이 확산되고 있다."},
]
for i, item in enumerate(FIX):
    item.setdefault("published_at", f"2026-06-18T{9 + (i % 8):02d}:00:00+09:00")
    item.setdefault("url", f"https://mix.test/{item['id']}")

_failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    line = f"[{'PASS' if ok else 'FAIL'}] {name}"
    if detail:
        line += f" — {detail}"
    print(line)
    if not ok:
        _failures.append(name)
    return ok


def _db_state():
    if not RADAR_DB.exists():
        return None
    stat = RADAR_DB.stat()
    return (stat.st_mtime_ns, stat.st_size)


def _norm_title(title: str) -> str:
    text = re.sub(r"[^0-9a-z가-힣]+", " ", (title or "").lower())
    return re.sub(r"\s+", " ", text).strip()


def _canon_cluster(cluster: str | None) -> str | None:
    return "nuclear_smr_project" if cluster == "hdec_netherlands_nuclear" else cluster


def _ids(entries: list[dict]) -> set[str]:
    return {e.get("id") for e in entries if e.get("id")}


def _section(sim: dict, key: str) -> list[dict]:
    return (sim.get("sections") or {}).get(key) or []


def _top_entries(sim: dict) -> list[dict]:
    out: list[dict] = []
    for key in TOP_KEYS:
        out.extend(_section(sim, key))
    return out


def _top_ids(sim: dict, keys=TOP_NOISE_SURFACES) -> set[str]:
    out: set[str] = set()
    for key in keys:
        out |= _ids(_section(sim, key))
    return out


def _run_pipeline_sim() -> dict | None:
    code = (
        "import os, sys, json, tempfile\n"
        "d=tempfile.mkdtemp()\n"
        "os.environ['DB_PATH']=os.path.join(d,'t.db')\n"
        "os.environ['APP_MODE']='mock'; os.environ['NEWS_MODE']='live'\n"
        "sys.path.insert(0,'.')\n"
        "FIX=" + json.dumps(FIX, ensure_ascii=False) + "\n"
        "from app import db, collector, scoring, insight, briefing, live_collector as lc\n"
        "from app import decision_relevance, radar\n"
        "lc.fetch_all=lambda *a,**k:[dict(x) for x in FIX]\n"
        "db.init_db(); collector.run(); scoring.score_all(); insight.generate_all()\n"
        "b=briefing.build_brief()\n"
        "rows={r['id']:r for r in db.fetch_articles_with_scores()}\n"
        "def slim(e,sec): return {'section':sec,'id':e.get('article_id'),\n"
        " 'title':e.get('title'),'reason':e.get('one_line_reason') or e.get('why_it_matters') or '',\n"
        " 'cluster':e.get('exposure_cluster_key'),'flags':e.get('top_exposure_flags') or [],\n"
        " 'exec_section':e.get('executive_section'),'radar_section':e.get('radar_section'),\n"
        " 'risk_label':e.get('risk_radar_label')}\n"
        "sections={}; visible=[]\n"
        "TOP_KEYS=" + json.dumps(TOP_KEYS, ensure_ascii=False) + "\n"
        "for sec,key in TOP_KEYS.items():\n"
        "    entries=[slim(e,sec) for e in (b.get(key) or [])]\n"
        "    sections[sec]=entries; visible.extend(entries)\n"
        "cat=[]\n"
        "for csec in (b.get('category_sections') or []):\n"
        "    for a in (csec.get('top_articles') or []):\n"
        "        e=slim(a,'cat:'+str(csec.get('category_key'))); cat.append(e); visible.append(e)\n"
        "sections['cat']=cat\n"
        "decisions={}; radar_sections={}; reasons={}\n"
        "for rid,row in rows.items():\n"
        "    d=decision_relevance.classify(row, 'general')\n"
        "    rs=decision_relevance.override_radar_section(radar.classify_section(row,'general'),d)\n"
        "    decisions[rid]=d; radar_sections[rid]=rs\n"
        "    reasons[rid]=insight.executive_reason(row.get('title') or '', row.get('snippet') or '',\n"
        "        is_stock_hype=bool(d.get('stock_hype')), is_finance=bool(d.get('is_finance')),\n"
        "        hdec_direct=bool(d.get('hdec_direct')))\n"
        "out={'mode':b.get('news_data_mode'),'sections':sections,'visible':visible,\n"
        " 'grades':{i:rows[i]['alert_grade'] for i in rows},\n"
        " 'scores':{i:rows[i]['final_score'] for i in rows},\n"
        " 'decisions':decisions,'radar_sections':radar_sections,'reasons':reasons}\n"
        "print(json.dumps(out, ensure_ascii=False))\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True,
                          text=True, cwd=ROOT, timeout=240)
    if proc.returncode != 0:
        check("fixture pipeline 실행", False, (proc.stderr or "").strip()[-700:])
        return None
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        check("fixture pipeline 출력 파싱", False, (proc.stdout or "")[-500:])
        return None


def _check_query_config() -> None:
    if not check("data/live_news_sources.json 존재", SOURCES.exists()):
        return
    data = json.loads(SOURCES.read_text(encoding="utf-8"))
    groups = data.get("query_groups") or []
    risk = next((g for g in groups if g.get("name") == "risk_regulation"), None)
    check("risk_regulation query group 존재", bool(risk))
    if not risk:
        return
    queries = set(risk.get("queries") or [])
    missing = sorted(RISK_QUERIES - queries)
    check("risk_regulation group 핵심 쿼리 포함", not missing,
          f"missing={missing}")
    check("risk_regulation group per-query cap 설정",
          int(risk.get("max_per_query") or 0) <= 2,
          str(risk.get("max_per_query")))
    check("risk_regulation group max_total이 global보다 작음",
          int(risk.get("max_total") or 999) < int(data.get("max_total") or 0),
          f"group={risk.get('max_total')} global={data.get('max_total')}")
    check("global max_total은 70 유지", data.get("max_total") == 70,
          str(data.get("max_total")))


def _check_topic_mix(sim: dict | None) -> None:
    if not sim:
        return
    check("fixture brief가 live 모드로 생성", sim.get("mode") == "live")

    top_ids = _top_ids(sim, keys=TOP_KEYS.keys())
    hdec_opps = {"mix_hdec_nuke", "mix_hdec_city", "mix_hdec_dc"}
    check("A: HDEC direct opportunity articles remain visible",
          hdec_opps <= top_ids, f"missing={sorted(hdec_opps - top_ids)}")

    risk_ids = _ids(_section(sim, "risk"))
    severe_visible = "mix_risk_severe" in risk_ids or "mix_risk_severe" in top_ids
    severe_is_risk = sim.get("radar_sections", {}).get("mix_risk_severe") == "risk_regulation"
    check("B: serious HDEC risk appears in risk/top surface with risk membership",
          severe_visible and severe_is_risk,
          f"visible={severe_visible} radar={sim.get('radar_sections', {}).get('mix_risk_severe')}")
    check("B: regulatory article appears in risk regulation membership",
          sim.get("radar_sections", {}).get("mix_reg_policy") == "risk_regulation",
          str(sim.get("radar_sections", {}).get("mix_reg_policy")))

    severe_flags = []
    for e in _top_entries(sim):
        if e.get("id") == "mix_risk_severe":
            severe_flags = e.get("flags") or []
            break
    check("C: 특별감독 is not treated as sports/coach",
          "sports_context" not in severe_flags,
          str(severe_flags))

    noisy_surfaces = _top_ids(sim, keys=TOP_NOISE_SURFACES)
    check("D: stock/beneficiary article absent from executive top/radar surfaces",
          "mix_stock" not in noisy_surfaces,
          sorted(noisy_surfaces & {"mix_stock"}))
    check("D: supplier-only article absent from top/new/HDEC/AI/business/risk",
          "mix_supplier" not in noisy_surfaces,
          sorted(noisy_surfaces & {"mix_supplier"}))

    check("E: sales/customer-AI article absent from top_immediate/top_new",
          "mix_customer_ai" not in (
              _ids(_section(sim, "top_imm")) | _ids(_section(sim, "top_new"))),
          f"top_imm={_ids(_section(sim, 'top_imm'))} top_new={_ids(_section(sim, 'top_new'))}")

    customer_cat = [e for e in _section(sim, "cat") if e.get("id") == "mix_customer_ai"]
    if customer_cat:
        reason = " / ".join(e.get("reason") or "" for e in customer_cat)
        check("F: category customer-AI reason is low-urgency operation label",
              "분양/고객상담 운영 자동화 실험" in reason
              and "생산성·안전 기술 도입" not in reason,
              reason)
    else:
        reason = (sim.get("reasons") or {}).get("mix_customer_ai", "")
        check("F: customer-AI fallback reason remains low-urgency operation label",
              "분양/고객상담 운영 자동화 실험" in reason,
              reason)

    first30 = (sim.get("visible") or [])[:30]
    id_counts = Counter(e.get("id") for e in first30 if e.get("id"))
    dup_ids = {k: v for k, v in id_counts.items() if v > 1}
    title_counts = Counter(_norm_title(e.get("title") or "")
                           for e in first30 if e.get("title"))
    dup_titles = {k: v for k, v in title_counts.items() if v > 1}
    check("G: first visible 30 exact duplicate ids = 0", not dup_ids, str(dup_ids))
    check("G: first visible 30 exact duplicate titles = 0", not dup_titles, str(dup_titles))

    clusters = Counter(_canon_cluster(e.get("cluster")) for e in first30 if e.get("cluster"))
    bad_clusters = {k: v for k, v in clusters.items()
                    if k in CLUSTER_CAPS and v > CLUSTER_CAPS[k]}
    check("H: dominant cluster caps respected in first visible 30",
          not bad_clusters, str(bad_clusters))

    grades = sim.get("grades") or {}
    check("I: strong HDEC risk is not excluded",
          grades.get("mix_risk_severe") != "제외", str(grades.get("mix_risk_severe")))
    check("I: normal-source regulatory risk is not excluded",
          grades.get("mix_reg_policy") != "제외", str(grades.get("mix_reg_policy")))
    check("I: HDEC defect/quality risk is not excluded",
          grades.get("mix_defect") != "제외", str(grades.get("mix_defect")))


def main() -> int:
    print(f"== verify_live_search_topic_mix @ {ROOT} ==")
    db_before = _db_state()
    _check_query_config()
    sim = _run_pipeline_sim()
    _check_topic_mix(sim)
    check("repo의 radar.db가 검증 중 변경/생성되지 않음 (temp DB 격리)",
          _db_state() == db_before)
    if _failures:
        print(f"\nRESULT: FAIL ({len(_failures)} failed)")
        for name in _failures:
            print(f"  - {name}")
        return 1
    print("\nRESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
