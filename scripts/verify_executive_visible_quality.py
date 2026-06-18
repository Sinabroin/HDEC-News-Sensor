"""P0-D3L verifier — executive visible article quality and cross-surface dedup.

Deterministic, no network, temp DB only.

Checks:
- exact article/title/url repeats do not appear across executive visible surfaces
- dominant clusters stay within high-exposure budgets
- stock/listicle, sales promo, sports, generic bank/PF, and weak competitor-only AI robot
  items do not displace direct HDEC/project/policy/risk signals
- reason text does not overstate stock/UAE jobs/bank solar PF as compliance or AI DC EPC
"""

import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRIEFING = ROOT / "app" / "briefing.py"
INSIGHT = ROOT / "app" / "insight.py"
RADAR_DB = ROOT / "radar.db"

OLD_PHRASES = [
    "현대건설 연관 수주 신호와 현대건설 연관 수주 신호",
    "수주 경쟁력·시장 포지션 영향권",
]
STOCK_IDS = {"v_stock_kmec", "v_stock_sams", "v_stock_postwar", "v_stock_smr"}
SALES_PROMO_IDS = {"v_sales_promo"}
CUSTOMER_AI_IDS = {"v_customer_ai"}
SPORTS_IDS = {"v_sports"}
LOW_COMPETITOR_IDS = {"v_comp_robot"}
DIRECT_PROJECT_POLICY_RISK_IDS = {
    "v_dup_dc1", "v_dup_dc2", "v_hdec_nuke1", "v_hdec_nuke2",
    "v_dc_policy", "v_hdec_city", "v_risk",
}
CLUSTER_CAPS = {
    "nuclear_smr_project": 3,
    "ai_datacenter_power": 3,
    "smart_construction_challenge": 2,
}

FIX = [
    {"id": "v_dup_dc1", "source": "한국경제",
     "title": "현대건설, AI 데이터센터 EPC 수주 추진…전력 인프라 설계 착수",
     "snippet": "현대건설이 AI 데이터센터 EPC와 전력 인프라 수주 전략을 강화한다"},
    {"id": "v_dup_dc2", "source": "이데일리",
     "title": "현대건설, AI 데이터센터 EPC 수주 추진…전력 인프라 설계 착수",
     "snippet": "같은 제목의 후속 기사로 전력 인프라 설계 착수 내용을 다뤘다"},
    {"id": "v_hdec_nuke1", "source": "연합뉴스",
     "title": "현대건설, 네덜란드 원전 2기 수주전 뛰어들었다",
     "snippet": "현대건설이 웨스팅하우스와 네덜란드 원전 프로젝트 수주전에 참여했다"},
    {"id": "v_hdec_nuke2", "source": "한국경제",
     "title": "현대건설, 웨스팅하우스와 맞손…네덜란드 원전 수주 만전",
     "snippet": "현대건설은 유럽 원전 사업 수주 전략을 강화하고 있다"},
    {"id": "v_nuke_generic1", "source": "전자신문",
     "title": "신규 대형원전·SMR 후보지 선정…전력수요 급증 전망에 속도",
     "snippet": "AI 전력수요 증가에 맞춰 대형원전과 SMR 후보지 논의가 속도를 낸다"},
    {"id": "v_nuke_generic2", "source": "매일경제",
     "title": "SMR 특별법 논의 본격화…건설사 원전 밸류체인 기대",
     "snippet": "SMR 특별법과 원전 밸류체인 논의가 이어지고 있다"},
    {"id": "v_nuke_generic3", "source": "서울경제",
     "title": "원전·SMR 전력 인프라 투자 확대…AI 전력난 대응",
     "snippet": "전력난 대응을 위해 원전과 SMR 투자 확대가 거론된다"},
    {"id": "v_nuke_generic4", "source": "대한경제",
     "title": "소형모듈원자로 발주 채비…건설 EPC 시장 주목",
     "snippet": "소형모듈원자로 발주와 EPC 시장 기회를 다룬 기사"},
    {"id": "v_stock_smr", "source": "네이버 프리미엄콘텐츠",
     "title": "[KMEC 증권 레이더 ⑫] 대미투자 1호 사업은 SMR, 현대건설 주가 향방은",
     "snippet": "현대건설 주가와 SMR 관련 종목 흐름을 짚었다"},
    {"id": "v_stock_sams", "source": "머니투데이",
     "title": "‘삼전닉스’ 말고 뭐 사야 해? AI 전력망 수혜주 총정리",
     "snippet": "AI 전력망 관련 수혜주와 종목 흐름을 소개했다"},
    {"id": "v_stock_postwar", "source": "데일리머니",
     "title": "포스트워 수혜 건설주 투자 리스트…중동 재건 관련주 강세",
     "snippet": "중동 재건 기대감에 건설주 투자 리스트를 제시했다"},
    {"id": "v_sales_promo", "source": "헤럴드경제",
     "title": "현대건설, 힐스테이트 분양 홍보관 개관…청약 일정 공개",
     "snippet": "소비자 대상 분양 홍보관과 청약 일정을 안내했다"},
    {"id": "v_customer_ai", "source": "데일리안",
     "title": "현대건설, 생성형 AI 분양 상담사 도입…청약 상담 자동화",
     "snippet": "현대건설이 생성형 AI 상담사로 고객 응대와 청약 상담을 자동화한다"},
    {"id": "v_sports", "source": "스포츠서울",
     "title": "박정아, 여자배구 스타랭킹 1위…현대건설 배구단 관심",
     "snippet": "현대건설 배구단과 선수 스타랭킹 소식을 전했다"},
    {"id": "v_comp_robot", "source": "로봇신문",
     "title": "DL이앤씨, AI 로봇으로 건설현장 혁신 가속",
     "snippet": "경쟁사 단독 AI 로봇 도입 사례를 소개했다"},
    {"id": "v_dc_policy", "source": "전자신문",
     "title": "특별법 시행 앞둔 AI 데이터센터…건설사 전력망·냉각 솔루션 경쟁",
     "snippet": "AI 데이터센터 특별법 시행을 앞두고 건설사들이 EPC와 전력망 경쟁을 벌인다"},
    {"id": "v_hdec_city", "source": "서울경제",
     "title": "현대건설, 목동 재건축 수주전 참여…도시정비 경쟁 본격화",
     "snippet": "현대건설이 목동 재건축 수주전에 참여해 도시정비 경쟁을 이어간다"},
    {"id": "v_risk", "source": "서울경제",
     "title": "건설현장 중대재해처벌법 처벌 강화…시공사 안전관리 비상",
     "snippet": "건설현장 중대재해와 안전관리 규제가 강화되며 시공사 대응 부담이 커졌다"},
    {"id": "v_uae_jobs", "source": "KOTRA",
     "title": "비석유 5대 전략 산업이 여는 2026 UAE 취업 기회",
     "snippet": "UAE AI 데이터센터와 비석유 산업의 취업·채용 기회를 소개했다"},
    {"id": "v_solar_pf", "source": "이코노믹리뷰",
     "title": "신한은행, 2410억 태양광 PF 완료…영광 300MW 사업 시동",
     "snippet": "태양광 프로젝트 파이낸싱과 은행 금융주선 내용을 다뤘다"},
    {"id": "v_bank_roundup", "source": "비즈워치",
     "title": "금융권 이모저모: 은행권 태양광 PF·해외 투자 동향",
     "snippet": "은행권 PF와 해외 투자 소식을 묶은 금융권 이모저모 기사"},
    {"id": "v_bank_dc_epc", "source": "한국경제",
     "title": "신한은행, AI 데이터센터 EPC PF 금융주선…건설사 전력 인프라 참여",
     "snippet": "AI 데이터센터 EPC 프로젝트의 전력 인프라와 건설사 참여를 전제로 PF를 주선한다"},
]
for i, item in enumerate(FIX):
    item.setdefault("published_at", f"2026-06-18T{9 + (i % 6):02d}:00:00+09:00")
    item.setdefault("url", f"https://ex.test/{item['id']}")

_failures = []


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
    if cluster == "hdec_netherlands_nuclear":
        return "nuclear_smr_project"
    return cluster


def _run_pipeline_sim() -> dict | None:
    code = (
        "import os, sys, json, tempfile\n"
        "d=tempfile.mkdtemp()\n"
        "os.environ['DB_PATH']=os.path.join(d,'t.db')\n"
        "os.environ['APP_MODE']='mock'; os.environ['NEWS_MODE']='live'\n"
        "sys.path.insert(0,'.')\n"
        "FIX=" + json.dumps(FIX, ensure_ascii=False) + "\n"
        "from app import db, collector, scoring, insight, briefing, live_collector as lc\n"
        "from app import decision_relevance\n"
        "lc.fetch_all=lambda *a,**k:[dict(x) for x in FIX]\n"
        "db.init_db(); collector.run(); scoring.score_all(); insight.generate_all()\n"
        "b=briefing.build_brief()\n"
        "rows={r['id']:r for r in db.fetch_articles_with_scores()}\n"
        "SURF=[('top_imm','top_immediate_signals'),('top_new','top_new_issues'),\n"
        "      ('hdec','hdec_direct_signals'),('ai','ai_radar_signals'),\n"
        "      ('biz','business_signals'),('risk','risk_regulation_signals'),\n"
        "      ('comp','competitor_supply_signals'),('macro','macro_economy_signals')]\n"
        "def slim(e,sec): return {'section':sec,'id':e.get('article_id'),\n"
        " 'title':e.get('title'),'reason':e.get('one_line_reason') or e.get('why_it_matters') or '',\n"
        " 'cluster':e.get('exposure_cluster_key'),'flags':e.get('top_exposure_flags') or []}\n"
        "sections={}\n"
        "visible=[]\n"
        "for sec,key in SURF:\n"
        "    entries=[slim(e,sec) for e in (b.get(key) or [])]\n"
        "    sections[sec]=entries; visible.extend(entries)\n"
        "cat=[]\n"
        "for csec in (b.get('category_sections') or []):\n"
        "    for a in (csec.get('top_articles') or []):\n"
        "        e=slim(a,'cat:'+str(csec.get('category_key'))); cat.append(e); visible.append(e)\n"
        "sections['cat']=cat\n"
        "reasons={}\n"
        "for e in visible:\n"
        "    if e.get('id') and e.get('reason'):\n"
        "        reasons.setdefault(e['id'],[]).append(e['reason'])\n"
        "for bucket in ('review_excluded_evidence','source_filtered_evidence'):\n"
        "    for a in ((b.get(bucket) or {}).get('items') or []):\n"
        "        if a.get('article_id') and a.get('why_it_matters'):\n"
        "            reasons.setdefault(a['article_id'],[]).append(a['why_it_matters'])\n"
        "for rid,row in rows.items():\n"
        "    d=decision_relevance.classify(row, 'general')\n"
        "    reason=insight.executive_reason(row.get('title') or '', row.get('snippet') or '',\n"
        "        is_stock_hype=bool(d.get('stock_hype')), is_finance=bool(d.get('is_finance')),\n"
        "        hdec_direct=bool(d.get('hdec_direct')))\n"
        "    if reason and reason not in reasons.setdefault(rid,[]): reasons[rid].append(reason)\n"
        "out={'mode':b.get('news_data_mode'),'sections':sections,'visible':visible,\n"
        " 'reasons':reasons,'grades':{i:rows[i]['alert_grade'] for i in rows},\n"
        " 'brief_json':json.dumps(b, ensure_ascii=False)}\n"
        "print(json.dumps(out, ensure_ascii=False))\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True,
                          text=True, cwd=ROOT, timeout=240)
    if proc.returncode != 0:
        check("파이프라인 시뮬레이션 실행", False, (proc.stderr or "").strip()[-600:])
        return None
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        check("파이프라인 시뮬레이션 출력 파싱", False, (proc.stdout or "")[-500:])
        return None


def _ids(entries: list[dict]) -> list[str]:
    return [e.get("id") for e in entries]


def _section(sim: dict, name: str) -> list[dict]:
    return (sim.get("sections") or {}).get(name) or []


def _all_top_entries(sim: dict) -> list[dict]:
    sections = sim.get("sections") or {}
    out = []
    for key in ("top_imm", "top_new", "hdec", "ai", "biz", "risk", "comp", "macro"):
        out.extend(sections.get(key) or [])
    return out


def _check_static_contract() -> None:
    briefing = BRIEFING.read_text(encoding="utf-8")
    insight = INSIGHT.read_text(encoding="utf-8")
    check("briefing에 D3L exact single-use cap 존재",
          "MAX_ARTICLE_TOP_SURFACES = 1" in briefing
          and "GLOBAL_HIGH_EXPOSURE_CLUSTER_CAPS" in briefing)
    check("briefing에 주식/분양/스포츠/PF high-exposure flag 존재",
          all(s in briefing for s in (
              "securities_context", "sales_promo", "sports_context",
              "generic_finance_roundup", "competitor_only_ai_robot")))
    check("insight에 UAE 취업/PF 사유 guard 존재",
          "REASON_OVERSEAS_LABOR" in insight and "REASON_ENERGY_FINANCE" in insight)


def _check_visible_quality(sim: dict | None) -> None:
    if not sim:
        return
    check("시뮬: live fixture brief 생성", sim.get("mode") == "live")

    visible = sim.get("visible") or []
    top_entries = _all_top_entries(sim)
    first30 = visible[:30]

    id_counts = Counter(e.get("id") for e in visible if e.get("id"))
    dup_ids = {k: v for k, v in id_counts.items() if v > 1}
    check("A: executive visible surfaces exact article id 중복 0건",
          not dup_ids, str(dup_ids))

    title_counts = Counter(_norm_title(e.get("title") or "") for e in visible if e.get("title"))
    dup_titles = {k: v for k, v in title_counts.items() if v > 1}
    check("A: executive visible surfaces normalized title 중복 0건",
          not dup_titles, str(dup_titles))

    first30_clusters = Counter(_canon_cluster(e.get("cluster")) for e in first30 if e.get("cluster"))
    for cluster, cap in CLUSTER_CAPS.items():
        check(f"B: first 30 visible {cluster} <= {cap}",
              first30_clusters.get(cluster, 0) <= cap,
              f"{cluster}={first30_clusters.get(cluster, 0)}")

    for section in ("top_new", "top_imm"):
        clusters = Counter(_canon_cluster(e.get("cluster")) for e in _section(sim, section)
                           if e.get("cluster"))
        bad = {k: v for k, v in clusters.items() if v > 1}
        check(f"B: {section} same cluster <= 1", not bad, str(bad))

    cat_clusters = Counter(_canon_cluster(e.get("cluster")) for e in _section(sim, "cat")
                           if e.get("cluster"))
    cat_bad = {k: v for k, v in cat_clusters.items() if v > 2}
    check("B: category top_articles same cluster <= 2", not cat_bad, str(cat_bad))

    for section in ("hdec", "ai", "biz", "risk", "top_new"):
        entries = _section(sim, section)
        first_id = entries[0].get("id") if entries else None
        check(f"C: stock/listicle cannot be top-1 in {section}",
              first_id not in STOCK_IDS, f"{section}={_ids(entries)}")
    top_ids = set(_ids(top_entries))
    check("C/D/E: stock/sales promo/sports absent from executive top/radar surfaces",
          not ((STOCK_IDS | SALES_PROMO_IDS | SPORTS_IDS) & top_ids),
          f"bad={sorted((STOCK_IDS | SALES_PROMO_IDS | SPORTS_IDS) & top_ids)}")

    check("D: customer-operation AI not in top_new/top_immediate",
          not (CUSTOMER_AI_IDS & (set(_ids(_section(sim, "top_new")))
                                  | set(_ids(_section(sim, "top_imm"))))),
          f"top_new={_ids(_section(sim, 'top_new'))} top_imm={_ids(_section(sim, 'top_imm'))}")

    ordered_ids = _ids(top_entries)
    direct_positions = [ordered_ids.index(i) for i in DIRECT_PROJECT_POLICY_RISK_IDS
                        if i in ordered_ids]
    for low_id in LOW_COMPETITOR_IDS:
        if low_id not in ordered_ids or not direct_positions:
            check("F: competitor-only AI robot does not outrank direct/project/policy/risk", True)
            continue
        check("F: competitor-only AI robot comes after direct/project/policy/risk",
              ordered_ids.index(low_id) > min(direct_positions),
              f"order={ordered_ids}")

    reasons = sim.get("reasons") or {}
    for fid in STOCK_IDS:
        text = " / ".join(reasons.get(fid) or [])
        if not text:
            continue
        check(f"G: {fid} stock/listicle reason is capital-market, not compliance",
              "자본시장" in text and "컴플라이언스" not in text and "규제" not in text,
              text)

    uae = " / ".join(reasons.get("v_uae_jobs") or [])
    check("G: UAE jobs is not AI data center EPC opportunity",
          uae and "AI 데이터센터 EPC" not in uae and "노동시장" in uae,
          uae)
    for fid in ("v_solar_pf", "v_bank_roundup"):
        text = " / ".join(reasons.get(fid) or [])
        check(f"G: {fid} generic bank/solar PF is not AI data center EPC opportunity",
              text and "AI 데이터센터 EPC" not in text and ("에너지 금융" in text or "PF" in text),
              text)
    dc_bank = " / ".join(reasons.get("v_bank_dc_epc") or [])
    check("G: explicit bank DC/EPC finance may keep AI data center reason",
          not dc_bank or "AI 데이터센터 EPC" in dc_bank, dc_bank)

    brief_json = sim.get("brief_json") or ""
    for phrase in OLD_PHRASES:
        check(f"H: old bad phrase absent: {phrase}", phrase not in brief_json)


def main() -> int:
    print(f"== verify_executive_visible_quality @ {ROOT} ==")
    db_before = _db_state()
    _check_static_contract()
    sim = _run_pipeline_sim()
    _check_visible_quality(sim)
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
