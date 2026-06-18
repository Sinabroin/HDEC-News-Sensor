"""P0-D3F 검증기 — cross-surface 노출 중복 제어 + 운영자 노출 품질 감사.

목적:
- 항상 보이는 디제스트(신규 이슈)는 상단 레이더 카드·즉시 알림과 동일 기사를 반복하지 않는다.
- 같은 cluster(스마트건설 챌린지·AI 데이터센터)가 카테고리 근거 전반을 도배하지 않는다.
- 의도된 multi-section(현대건설 원전 직접 쌍, 현대건설+리스크)은 보존한다.
- 운영자 전용 '노출 품질·중복 제어' 감사를 한국어 라벨로 노출하되 임원 카드는 raw 키 없이 깨끗하다.
- D3A~D3E 회귀(오탐 라우팅·generic 사유·이유 taxonomy·상단 품질·cluster cap)를 함께 막는다.

네트워크/비밀값 0건 — temp DB + live_collector.fetch_all 패치로만 돈다.

사용법:
    python3 scripts/verify_executive_surface_dedup_audit.py
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRIEFING = ROOT / "app" / "briefing.py"
REPORT_BUILDER = ROOT / "scripts" / "build_static_report.py"
RADAR_DB = ROOT / "radar.db"

GENERIC = "수주 경쟁력·시장 포지션 영향권"
GRADE_EXCLUDED = "제외"
AUDIT_TITLE = "운영자 점검: 노출 품질·중복 제어"
# 임원 상단 카드에 절대 노출되면 안 되는 raw 키 (감사 <details> 안에서만 허용).
RAW_KEYS = ["top_exposure_penalty", "exposure_cluster_key", "securities_context",
            "supplier_only", "very_stale"]
KNOWN_STATUSES = {"shown_top", "shown_category", "suppressed_duplicate",
                  "suppressed_cluster_cap", "suppressed_quality_gate", "evidence_only"}
THEMATIC = ("hdec", "ai", "biz", "comp", "risk")

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
        "import os, sys, json, tempfile, re as _re\n"
        "d=tempfile.mkdtemp()\n"
        "os.environ['DB_PATH']=os.path.join(d,'t.db')\n"
        "os.environ['APP_MODE']='mock'; os.environ['NEWS_MODE']='live'\n"
        "sys.path.insert(0,'.'); sys.path.insert(0,'scripts')\n"
        "FIX=" + json.dumps(FIX, ensure_ascii=False) + "\n"
        "from app import db, collector, scoring, insight, briefing, live_collector as lc\n"
        "lc.fetch_all=lambda *a,**k:[dict(x) for x in FIX]\n"
        "db.init_db(); collector.run(); scoring.score_all(); insight.generate_all()\n"
        "b=briefing.build_brief()\n"
        "rows={r['id']:r for r in db.fetch_articles_with_scores()}\n"
        "def ids(k): return [s.get('article_id') for s in (b.get(k) or [])]\n"
        "reasons={}\n"
        "for k in ('hdec_direct_signals','ai_radar_signals','business_signals',\n"
        "          'competitor_supply_signals','risk_regulation_signals',\n"
        "          'top_new_issues','top_immediate_signals'):\n"
        "    for e in (b.get(k) or []):\n"
        "        reasons.setdefault(e.get('article_id'),[]).append(e.get('one_line_reason') or '')\n"
        "cat_clusters={}\n"
        "for sec in (b.get('category_sections') or []):\n"
        "    for a in (sec.get('top_articles') or []):\n"
        "        reasons.setdefault(a.get('article_id'),[]).append(a.get('why_it_matters') or '')\n"
        "        cat_clusters.setdefault(a.get('exposure_cluster_key'),[]).append(a.get('article_id'))\n"
        "aud=b.get('exposure_quality_audit') or {}\n"
        "audit=[{'id':it.get('article_id'),'status':it.get('exposure_surface_status'),\n"
        "        'labels':it.get('suppression_reason_labels') or [],\n"
        "        'cluster':it.get('exposure_cluster_key'),\n"
        "        'flags':it.get('top_exposure_flags') or [],\n"
        "        'rep':it.get('representative_article_id')} for it in (aud.get('items') or [])]\n"
        "from build_static_report import render_report_html\n"
        "html,_=render_report_html(b)\n"
        "midx=html.find('" + AUDIT_TITLE + "')\n"
        "before=html[:midx] if midx>=0 else html\n"
        "after=html[midx:] if midx>=0 else ''\n"
        "RAW=" + json.dumps(RAW_KEYS) + "\n"
        "kor=['유사 기사 대표 선정됨','증권/주가성 문맥','공급사 단독','약한 출처']\n"
        "report={'has_section':midx>=0,\n"
        " 'audit_in_details':'<details' in after,\n"
        " 'before_raw':{k:before.count(k) for k in RAW},\n"
        " 'after_has_korean':any(k in after for k in kor)}\n"
        "out={'mode':b.get('news_data_mode'),\n"
        " 'hdec':ids('hdec_direct_signals'),'ai':ids('ai_radar_signals'),\n"
        " 'biz':ids('business_signals'),'comp':ids('competitor_supply_signals'),\n"
        " 'risk':ids('risk_regulation_signals'),'top_new':ids('top_new_issues'),\n"
        " 'top_imm':ids('top_immediate_signals'),'cat_clusters':cat_clusters,\n"
        " 'reasons':reasons,'audit':audit,'audit_shown':len(audit),\n"
        " 'audit_total':aud.get('total_count'),\n"
        " 'grades':{i:rows[i]['alert_grade'] for i in rows},\n"
        " 'report':report,\n"
        " 'generic_absent':('" + GENERIC + "' not in json.dumps(b, ensure_ascii=False))}\n"
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


def _thematic_union(sim: dict) -> set:
    out = set()
    for k in THEMATIC:
        out |= set(sim.get(k) or [])
    return out


def _audit_by_id(sim: dict, fid: str) -> dict:
    for it in sim.get("audit") or []:
        if it.get("id") == fid:
            return it
    return {}


def check_static_contract() -> None:
    src = BRIEFING.read_text(encoding="utf-8")
    check("briefing에 cross-surface dedup 상태/필터 존재",
          "class _ExposureSurfaceState" in src and "def _filter_surface_exposures" in src)
    check("briefing에 기사당/클러스터당 surface cap 상수 존재",
          "MAX_ARTICLE_TOP_SURFACES" in src and "MAX_CLUSTER_TOP_SURFACES" in src)
    check("briefing에 노출 품질 감사 빌더 + brief 키 존재",
          "def _build_exposure_audit" in src and '"exposure_quality_audit"' in src)
    check("briefing이 한국어 억제 사유 라벨을 보유",
          "SUPPRESSION_REASON_LABELS" in src and "유사 기사 대표 선정됨" in src
          and "동일 기사 이미 상단 노출" in src)
    rep = REPORT_BUILDER.read_text(encoding="utf-8")
    check("build_static_report에 노출 감사 렌더러 + 제목 존재",
          "def _render_exposure_audit" in rep and AUDIT_TITLE in rep)


def check_pipeline(sim: dict | None) -> None:
    if not sim:
        return
    check("시뮬: live fixture brief 생성", sim.get("mode") == "live")

    hdec = sim.get("hdec") or []
    ai = sim.get("ai") or []
    comp = sim.get("comp") or []
    top_new = sim.get("top_new") or []
    top_imm = sim.get("top_imm") or []
    thematic = _thematic_union(sim)

    # ---- 핵심: cross-surface dedup — 디제스트(신규 이슈)는 상단/즉시와 동일 기사 0건 ----
    overlap = set(top_new) & (thematic | set(top_imm))
    check("핵심: 신규 이슈가 상단 레이더·즉시 알림과 동일 기사 0건",
          not overlap, f"중복={sorted(overlap)} top_new={top_new}")

    # ---- A. 동일 기사 중복 — '신규 대형원전·SMR 후보지' 반복 차단 ----
    where_nuke = [k for k in THEMATIC if "c_nuke_generic" in set(sim.get(k) or [])]
    check("A: c_nuke_generic이 레이더 어딘가에 노출(완전 삭제 아님)", bool(where_nuke),
          f"섹션={where_nuke}")
    check("A: c_nuke_generic이 신규 이슈에 반복되지 않음(상단 노출 시 디제스트 양보)",
          "c_nuke_generic" not in top_new, f"top_new={top_new}")

    # ---- B. 현대건설 네덜란드 원전 쌍 — 둘 다 현대건설 직접에 보존(서로 다른 출처) ----
    check("B: 현대건설 원전 직접 쌍이 현대건설 섹션에 모두 노출",
          {"c_hdec_nl", "c_hdec_wh"} <= set(hdec), f"hdec={hdec}")
    if "c_hdec_nl" in hdec and "c_stock_smr" in hdec:
        check("B: 직접 원전 쌍이 주가/SMR 기사보다 앞섬",
              hdec.index("c_hdec_nl") < hdec.index("c_stock_smr"), f"hdec={hdec}")
    check("B: 주가/SMR 기사는 어떤 상단 섹션의 top-1도 아님",
          all((not sim.get(k)) or sim[k][0] != "c_stock_smr"
              for k in THEMATIC + ("top_new", "top_imm")),
          " / ".join(f"{k}={sim.get(k)}" for k in THEMATIC))

    # ---- C. 스마트건설 챌린지 — AI ≤1, 카테고리 ≤2, 감사에 cluster 노출, 사유 taxonomy ----
    cat_clusters = sim.get("cat_clusters") or {}
    ai_smart = [x for x in ai if x.startswith("c_smart")]
    check("C: AI 섹션 스마트건설 챌린지 최대 1건", len(ai_smart) <= 1, f"ai={ai}")
    smart_cat = cat_clusters.get("smart_construction_challenge") or []
    check("C: 카테고리 근거 스마트건설 챌린지 cluster 최대 2건",
          len(smart_cat) <= 2, f"smart_cat={smart_cat}")
    audit_clusters = {it.get("cluster") for it in (sim.get("audit") or [])}
    check("C: 감사에 smart_construction_challenge cluster 노출",
          "smart_construction_challenge" in audit_clusters,
          f"clusters={sorted(c for c in audit_clusters if c)}")
    smart_reasons = []
    for fid in ("c_smart1", "c_smart2", "c_smart3", "c_smart4", "c_smart5"):
        smart_reasons.extend(sim.get("reasons", {}).get(fid) or [])
    check("C: 스마트건설 챌린지 사유 taxonomy 유지",
          any("스마트건설 기술 확산" in r for r in smart_reasons), str(smart_reasons[:3]))

    # ---- D. AI 데이터센터 — 직접 대표 유지, 공급사는 보조(경쟁사·공급망/근거), 감사 노출 ----
    check("D: 직접 EPC/정책성 데이터센터 대표가 AI 섹션에 남음",
          bool({"c_dc_direct", "c_dc_policy"} & set(ai)), f"ai={ai}")
    if "c_gaon" in ai:
        check("D: 가온전선 supplier-only가 AI 직접 대표보다 앞서지 않음",
              ("c_dc_direct" in ai and ai.index("c_dc_direct") < ai.index("c_gaon"))
              or ("c_dc_policy" in ai and ai.index("c_dc_policy") < ai.index("c_gaon")),
              f"ai={ai}")
    check("D: 가온전선 supplier-only가 경쟁사·공급망 또는 감사에 남음",
          "c_gaon" in comp or bool(_audit_by_id(sim, "c_gaon")),
          f"comp={comp} audit_gaon={_audit_by_id(sim, 'c_gaon')}")
    gaon_audit = _audit_by_id(sim, "c_gaon")
    check("D: 감사에 공급사 단독 또는 데이터센터 cluster 라벨",
          ("공급사 단독" in (gaon_audit.get("labels") or []))
          or (gaon_audit.get("cluster") == "ai_datacenter_power"), str(gaon_audit))

    # ---- E. 주가/SMR 증권성 — top-1 아님 + 사유 유지 + 감사에 증권성 표기 ----
    stock_reasons = sim.get("reasons", {}).get("c_stock_smr") or []
    check("E: 주가/SMR 사유는 자본시장 관찰 유지",
          any("자본시장 관찰" in r for r in stock_reasons), str(stock_reasons))
    stock_audit = _audit_by_id(sim, "c_stock_smr")
    check("E: 감사에 증권/주가성 사유 또는 securities flag 노출",
          ("증권/주가성 문맥" in (stock_audit.get("labels") or []))
          or ("securities_context" in (stock_audit.get("flags") or [])), str(stock_audit))

    # ---- 감사 구조 무결성 ----
    audit = sim.get("audit") or []
    check("감사: 항목 ≤ 30건", (sim.get("audit_shown") or 0) <= 30, str(sim.get("audit_shown")))
    check("감사: 모든 항목에 상태/사유 라벨 존재",
          all(it.get("status") in KNOWN_STATUSES
              and (it.get("labels") or it.get("status", "").startswith("suppressed"))
              for it in audit), f"{[it for it in audit if it.get('status') not in KNOWN_STATUSES][:2]}")
    check("감사: 블로그/카페(출처 품질 제외)는 이 감사에 미포함(전용 섹션 단일 소유)",
          all(it.get("status") != "suppressed_quality_gate"
              or it.get("cluster") for it in audit))

    # ---- F. 정적 리포트 — 감사 섹션 존재/접힘 + 임원 카드 raw 키 청정 ----
    report = sim.get("report") or {}
    check("F: 리포트에 '운영자 점검: 노출 품질·중복 제어' 섹션 존재",
          report.get("has_section"))
    check("F: 감사 블록이 <details>로 접힘", report.get("audit_in_details"))
    leaked = {k: v for k, v in (report.get("before_raw") or {}).items() if v}
    check("F: 임원 상단 카드(감사 섹션 이전)에 raw 키 노출 0건", not leaked, str(leaked))
    check("F: 감사 섹션에 한국어 사유 라벨 노출", report.get("after_has_korean"))

    # ---- G. 회귀 가드 ----
    check("G: old generic reason phrase absent", sim.get("generic_absent") is True)
    grades = sim.get("grades") or {}
    check("G: stock-hype 잭팟 등급=제외", grades.get("c_jackpot") == GRADE_EXCLUDED,
          str(grades.get("c_jackpot")))
    check("G: 잭팟이 현대건설/AI 상단에 없음",
          "c_jackpot" not in set(hdec) and "c_jackpot" not in set(ai))
    check("G: 원더독스가 현대건설 직접 섹션에 없음", "c_wonder" not in set(hdec),
          f"hdec={hdec}")
    check("G: CB 발행이 AI 섹션에 없음", "c_cb" not in set(ai), f"ai={ai}")


def main() -> int:
    print(f"== verify_executive_surface_dedup_audit @ {ROOT} ==")
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
