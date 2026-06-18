"""P0-D3E 검증기 — executive near-duplicate cluster exposure cap.

목적:
- 같은 사건/주제 기사들이 top/radar/category 근거 상단을 반복 점유하지 않는다.
- 원문 기사·점수·등급은 삭제/변경하지 않고, 표시 노출만 제한한다.
- D3A-D3D 회귀(오탐 라우팅, generic 사유 제거, 이유 taxonomy, 상단 품질 flag)를 함께 막는다.

사용법:
    python3 scripts/verify_executive_cluster_exposure.py
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRIEFING = ROOT / "app" / "briefing.py"
RADAR_DB = ROOT / "radar.db"

GENERIC = "수주 경쟁력·시장 포지션 영향권"
GRADE_EXCLUDED = "제외"

FIX = [
    {"id": "c_hdec_nl", "source": "연합뉴스",
     "title": "현대건설, 네덜란드 원전 2기 수주전 뛰어들었다",
     "snippet": "현대건설이 웨스팅하우스와 네덜란드 원전 프로젝트 수주전에 참여했다"},
    {"id": "c_hdec_wh", "source": "한국경제",
     "title": "현대건설, 웨스팅하우스와 맞손…네덜란드 원전 수주 만전",
     "snippet": "현대건설은 네덜란드 대형 원전 사업 수주 전략을 강화하고 있다"},
    {"id": "c_nuke_generic", "source": "전자신문",
     "title": "신규 대형원전·SMR 후보지 선정…전력수요 급증 전망에 속도",
     "snippet": "AI 전력수요 증가에 맞춰 대형원전과 SMR 후보지 선정 논의가 속도를 내고 있다"},
    {"id": "c_stock_smr", "source": "네이버 프리미엄콘텐츠",
     "title": "[현대건설] 현대건설 주가, 팰리세이즈 SMR 계약 뜨면 얼마나 달라질까? (2026_1Q)",
     "snippet": "현대건설 주가와 관련 종목 흐름을 SMR 계약 기대감과 함께 짚었다"},

    {"id": "c_smart1", "source": "연합뉴스",
     "title": "AI·로봇 활용 스마트건설 기술 한자리에…국토부, 챌린지 개최",
     "snippet": "국토부가 AI와 로봇을 활용한 스마트건설 기술 챌린지를 개최한다"},
    {"id": "c_smart2", "source": "뉴스1",
     "title": "국토부, AI·로봇 등 스마트건설 챌린지 개최",
     "snippet": "스마트건설 기술 확산을 위한 국토부 챌린지 행사가 열린다"},
    {"id": "c_smart3", "source": "이데일리",
     "title": "건설현장 안전·품질 높일 기술 찾는다…스마트건설 챌린지",
     "snippet": "건설현장 안전과 품질을 높일 AI 로봇 스마트건설 기술을 찾는다"},
    {"id": "c_smart4", "source": "대한경제",
     "title": "스마트건설 챌린지, AI 로봇 시공기술 공모",
     "snippet": "국토부가 스마트건설 챌린지를 통해 AI 로봇 시공기술을 공모한다"},
    {"id": "c_smart5", "source": "국토일보",
     "title": "국토부 스마트건설 챌린지 개최…안전·품질 혁신 기술 발굴",
     "snippet": "AI와 로봇 기반 스마트건설 기술 확산을 위한 경진대회가 열린다"},

    {"id": "c_dc_direct", "source": "한국경제",
     "title": "현대건설, AI 데이터센터 EPC 수주 추진…전력 인프라 설계 착수",
     "snippet": "현대건설이 AI 데이터센터 EPC와 전력 인프라 수주 전략을 강화한다"},
    {"id": "c_dc_policy", "source": "전자신문",
     "title": "특별법 시행 앞둔 AI 데이터센터…건설사, 전력망·냉각 솔루션 경쟁",
     "snippet": "AI 데이터센터 특별법 시행을 앞두고 건설사들이 전력망과 냉각 솔루션 경쟁을 벌인다"},
    {"id": "c_dc_power", "source": "디지털데일리",
     "title": "AI 데이터센터 전력망 증설·PPA 확보 경쟁 본격화",
     "snippet": "데이터센터 사업자들이 전력망 증설과 PPA 확보 경쟁에 나섰다"},
    {"id": "c_dc_cooling", "source": "전자신문",
     "title": "데이터센터 냉각·전력 인프라 투자 급증",
     "snippet": "AI 데이터센터 확산으로 냉각과 전력 인프라 투자가 늘고 있다"},
    {"id": "c_dc_grid", "source": "매일경제",
     "title": "AI 데이터센터 분산전원 전력 인프라 확충 속도",
     "snippet": "분산전원과 전력 인프라가 AI 데이터센터 수요 대응 과제로 떠올랐다"},
    {"id": "c_gaon", "source": "더구루",
     "title": "가온전선, 美 AI 데이터센터 전력 인프라 연이은 수주",
     "snippet": "가온전선이 미국 AI 데이터센터 전력 인프라 공급을 확대한다"},

    {"id": "c_risk", "source": "서울경제",
     "title": "건설현장 중대재해처벌법 처벌 강화…시공사 안전관리 비상",
     "snippet": "건설현장 중대재해와 안전관리 규제가 강화되며 시공사 대응 부담이 커졌다"},
    {"id": "c_mideast", "source": "매일경제",
     "title": "중동 플랜트 발주 재개…건설사 해외수주 채비",
     "snippet": "중동 플랜트 발주 재개 기대에 건설사들이 해외수주 채비에 나섰다"},

    {"id": "c_jackpot", "source": "데일리머니",
     "title": "87조 잭팟 터진다…美·이란 종전에 주가 24% 폭등 [종목+]",
     "snippet": "미국과 이란 종전 기대에 관련 종목 주가가 폭등했다"},
    {"id": "c_cb", "source": "머니투데이",
     "title": "주가상승 자신한 CB 발행…안전판 부재에 역풍 부나",
     "snippet": "주가상승을 자신한 CB 발행이 안전판 부재로 역풍을 맞을 수 있다"},
    {"id": "c_wonder", "source": "스포츠서울",
     "title": "현대건설 배구단 원더독스, 프로팀 연파하고 우승",
     "snippet": "현대건설 배구단 원더독스가 프로팀을 연파하고 우승했다"},
]
for _f in FIX:
    _f.setdefault("published_at", "2026-06-18T09:00:00+09:00")
    _f.setdefault("url", f"https://ex.test/{_f['id']}")

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
        "profiles={}\n"
        "for rid,row in rows.items():\n"
        "    d=decision_relevance.classify(row, 'general')\n"
        "    p=briefing._top_exposure_profile(row, d)\n"
        "    profiles[rid]={'flags':p.get('top_exposure_flags') or [],\n"
        "                   'penalty':p.get('top_exposure_penalty'),\n"
        "                   'excluded':p.get('top_exposure_excluded')}\n"
        "def slim(e):\n"
        "    return {'id':e.get('article_id'),'title':e.get('title'),\n"
        "            'reason':e.get('one_line_reason') or e.get('why_it_matters'),\n"
        "            'cluster':e.get('exposure_cluster_key'),\n"
        "            'flags':e.get('top_exposure_flags') or [],\n"
        "            'score':e.get('final_score')}\n"
        "def entries(k): return [slim(e) for e in (b.get(k) or [])]\n"
        "reasons={}\n"
        "for k in ('hdec_direct_signals','ai_radar_signals','business_signals',\n"
        "          'competitor_supply_signals','risk_regulation_signals',\n"
        "          'top_new_issues','top_immediate_signals'):\n"
        "    for e in (b.get(k) or []):\n"
        "        reasons.setdefault(e.get('article_id'),[]).append(e.get('one_line_reason') or '')\n"
        "cat=[]\n"
        "for sec in (b.get('category_sections') or []):\n"
        "    for a in (sec.get('top_articles') or []):\n"
        "        s=slim(a); s['category']=sec.get('category_key'); cat.append(s)\n"
        "        reasons.setdefault(a.get('article_id'),[]).append(a.get('why_it_matters') or '')\n"
        "out={'mode':b.get('news_data_mode'),\n"
        " 'hdec':entries('hdec_direct_signals'), 'ai':entries('ai_radar_signals'),\n"
        " 'biz':entries('business_signals'), 'comp':entries('competitor_supply_signals'),\n"
        " 'risk':entries('risk_regulation_signals'), 'top_new':entries('top_new_issues'),\n"
        " 'top_imm':entries('top_immediate_signals'), 'cat':cat, 'reasons':reasons,\n"
        " 'grades':{i:rows[i]['alert_grade'] for i in rows}, 'profiles':profiles,\n"
        " 'generic_absent':('" + GENERIC + "' not in json.dumps(b, ensure_ascii=False))}\n"
        "print(json.dumps(out, ensure_ascii=False))\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True,
                          text=True, cwd=ROOT, timeout=240)
    if proc.returncode != 0:
        check("파이프라인 시뮬레이션 실행", False, (proc.stderr or "").strip()[-500:])
        return None
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        check("파이프라인 시뮬레이션 출력 파싱", False, (proc.stdout or "")[-500:])
        return None


def _ids(sim: dict, section: str) -> list[str]:
    return [e.get("id") for e in (sim.get(section) or [])]


def _entries(sim: dict, *sections: str) -> list[dict]:
    out = []
    for section in sections:
        out.extend(sim.get(section) or [])
    return out


def _entry(sim: dict, fid: str) -> dict:
    for e in _entries(sim, "hdec", "ai", "biz", "comp", "risk",
                      "top_new", "top_imm", "cat"):
        if e.get("id") == fid:
            return e
    return {}


def _before(ids: list[str], first: str, second: str) -> bool:
    if first not in ids:
        return False
    if second not in ids:
        return True
    return ids.index(first) < ids.index(second)


def _count_cluster(entries: list[dict], cluster: str) -> int:
    return sum(1 for e in entries if e.get("cluster") == cluster)


def check_static_contract() -> None:
    src = BRIEFING.read_text(encoding="utf-8")
    check("briefing에 exposure cluster key helper 존재",
          "def _exposure_cluster_key" in src and "exposure_cluster_key" in src)
    check("briefing에 deterministic cluster buckets 존재",
          all(t in src for t in ("nuclear_smr_project",
                                 "smart_construction_challenge",
                                 "ai_datacenter_power",
                                 "hdec_netherlands_nuclear")))
    check("briefing에 cluster cap helper 존재",
          "def _cap_exposure_clusters" in src and "max_per_cluster" in src)
    check("HDEC 직접 섹션에 source-pair 예외 존재",
          "hdec_direct_pair_cap" in src and "source_key in seen_sources" in src)


def check_pipeline(sim: dict | None) -> None:
    if not sim:
        return
    check("시뮬: live fixture brief 생성", sim.get("mode") == "live")

    hdec_ids = _ids(sim, "hdec")
    ai_ids = _ids(sim, "ai")
    top_new = sim.get("top_new") or []
    top_imm = sim.get("top_imm") or []
    all_top = _entries(sim, "hdec", "ai", "biz", "comp", "risk",
                       "top_new", "top_imm")

    check("A: 현대건설 원전 직접 기사들이 HDEC 섹션 대표로 노출",
          {"c_hdec_nl", "c_hdec_wh"} <= set(hdec_ids), f"hdec={hdec_ids}")
    check("A: 직접 원전 프로젝트가 주가/SMR 기사보다 앞섬",
          _before(hdec_ids, "c_hdec_nl", "c_stock_smr"), f"hdec={hdec_ids}")
    check("A: 주가/SMR 기사는 executive top-1 아님",
          all((not _ids(sim, s)) or _ids(sim, s)[0] != "c_stock_smr"
              for s in ("hdec", "ai", "biz", "top_new", "top_imm")),
          " / ".join(f"{s}={_ids(sim, s)}" for s in ("hdec", "ai", "biz", "top_new", "top_imm")))
    nuclear_top_count = sum(
        1 for e in top_new
        if e.get("cluster") in {"nuclear_smr_project", "hdec_netherlands_nuclear"})
    check("A: 원전/SMR cluster가 top_new 전체를 채우지 않음",
          not top_new or nuclear_top_count < len(top_new),
          f"top_new={[(e.get('id'), e.get('cluster')) for e in top_new]}")

    check("B: AI radar 상단 스마트건설 챌린지 cluster 최대 1건",
          _count_cluster(sim.get("ai") or [], "smart_construction_challenge") <= 1,
          f"ai={[(e.get('id'), e.get('cluster')) for e in sim.get('ai') or []]}")
    smart_cat = [e for e in sim.get("cat") or []
                 if e.get("cluster") == "smart_construction_challenge"]
    check("B: 카테고리 근거 스마트건설 챌린지 cluster 최대 2건",
          len(smart_cat) <= 2, f"cat={[(e.get('id'), e.get('category')) for e in smart_cat]}")
    smart_reasons = []
    for fid in ("c_smart1", "c_smart2", "c_smart3", "c_smart4", "c_smart5"):
        smart_reasons.extend(sim.get("reasons", {}).get(fid) or [])
    check("B: 스마트건설 챌린지 사유 taxonomy 유지",
          any("스마트건설 기술 확산" in r for r in smart_reasons),
          str(smart_reasons[:4]))

    ai_dc = [e for e in sim.get("ai") or []
             if e.get("cluster") == "ai_datacenter_power"]
    check("C: AI data center cluster가 AI radar를 도배하지 않음",
          len(ai_dc) <= 1, f"ai={[(e.get('id'), e.get('cluster')) for e in sim.get('ai') or []]}")
    check("C: 직접 EPC/정책성 데이터센터 대표 기사가 남음",
          bool({e.get("id") for e in ai_dc} & {"c_dc_direct", "c_dc_policy"}),
          f"ai_dc={ai_dc}")
    check("C: top_immediate에서도 데이터센터 cluster 반복 없음",
          _count_cluster(top_imm, "ai_datacenter_power") <= 1,
          f"top_imm={[(e.get('id'), e.get('cluster')) for e in top_imm]}")

    check("D: 가온전선 supplier-only는 AI direct/EPC 대표보다 앞서지 않음",
          ("c_gaon" not in ai_ids)
          or _before(ai_ids, "c_dc_direct", "c_gaon")
          or _before(ai_ids, "c_dc_policy", "c_gaon"),
          f"ai={ai_ids}")
    check("D: 가온전선 supplier-only는 evidence/공급망에 남음",
          "c_gaon" in _ids(sim, "comp") or bool(_entry(sim, "c_gaon")),
          f"comp={_ids(sim, 'comp')} cat={_entry(sim, 'c_gaon')}")

    check("E: old generic reason phrase absent", sim.get("generic_absent") is True)
    check("E: stock-hype 잭팟은 executive top/AI/HDEC에 없음",
          "c_jackpot" not in {e.get("id") for e in all_top}
          and "c_jackpot" not in ai_ids and "c_jackpot" not in hdec_ids,
          f"top={sorted(e.get('id') for e in all_top)}")
    check("E: stock-hype 잭팟 등급=제외",
          (sim.get("grades") or {}).get("c_jackpot") == GRADE_EXCLUDED,
          str((sim.get("grades") or {}).get("c_jackpot")))
    check("E: CB 발행은 AI/HDEC top에 없음",
          "c_cb" not in ai_ids and "c_cb" not in hdec_ids,
          f"ai={ai_ids} hdec={hdec_ids}")
    check("E: 원더독스는 현대건설 직접 섹션에 없음",
          "c_wonder" not in hdec_ids, f"hdec={hdec_ids}")
    stock_entry = _entry(sim, "c_stock_smr")
    stock_profile = (sim.get("profiles") or {}).get("c_stock_smr") or stock_entry
    check("E: D3D top exposure flags 유지(표시는 억제, profile은 보존)",
          {"securities_context", "weak_source"} <= set(stock_profile.get("flags") or []),
          str(stock_profile))
    check("E: D3L 주가/SMR 증권성 기사는 executive visible top에서 제외",
          "c_stock_smr" not in {e.get("id") for e in all_top},
          f"top={sorted(e.get('id') for e in all_top)}")


def main() -> int:
    print(f"== verify_executive_cluster_exposure @ {ROOT} ==")
    db_before = _db_state()
    check_static_contract()
    sim = _run_pipeline_sim()
    check_pipeline(sim)
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
