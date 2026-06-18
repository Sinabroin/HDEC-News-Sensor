"""P0-D3D 검증기 — executive top exposure quality gate (결정적·네트워크 없음).

목적:
- 상단/리드 노출은 직접 프로젝트·수주·정책·규제 신호를 우선한다.
- 주가/증권 맥락, 매우 오래된 기사, 공급사 단독, 약한 출처, generic roundup은
  근거에는 남기되 executive top/radar 상단을 차지하지 않는다.
- P0-D3A/D3B/D3C 라우팅·사유 회귀를 함께 막는다.

사용법:
    python3 scripts/verify_executive_top_exposure_quality.py
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
    {"id": "q_nuke", "source": "연합뉴스",
     "title": "현대건설, 네덜란드 원전 2기 수주전 뛰어들었다",
     "snippet": "현대건설이 웨스팅하우스와 네덜란드 원전 프로젝트 수주전에 참여했다",
     "published_at": "2026-06-18T09:00:00+09:00"},
    {"id": "q_nuke_partner", "source": "한국경제",
     "title": "현대건설, 웨스팅하우스와 맞손…네덜란드 원전 수주 만전",
     "snippet": "현대건설은 유럽 원전 사업 수주 전략을 강화하고 있다",
     "published_at": "2026-06-18T08:00:00+09:00"},
    {"id": "q_stock_smr", "source": "네이버 프리미엄콘텐츠",
     "title": "[현대건설] 현대건설 주가, 팰리세이즈 SMR 계약 뜨면 얼마나 달라질까? (2026_1Q)",
     "snippet": "현대건설 주가와 관련 종목 흐름을 SMR 계약 기대감과 함께 짚었다",
     "published_at": "2026-05-26T09:00:00+09:00"},
    {"id": "q_dc_policy", "source": "전자신문",
     "title": "특별법 시행 앞둔 AI 데이터센터…건설사, 전력망·냉각 솔루션 경쟁",
     "snippet": "AI 데이터센터 특별법 시행을 앞두고 건설사들이 EPC와 전력망 경쟁을 벌인다",
     "published_at": "2026-06-16T09:00:00+09:00"},
    {"id": "q_dc_epc", "source": "한국경제",
     "title": "AI 데이터센터 EPC 시장 열린다…건설사 전력 인프라 수주 경쟁",
     "snippet": "건설사들이 AI 데이터센터 EPC와 전력 인프라 수주를 준비한다",
     "published_at": "2026-06-17T09:00:00+09:00"},
    {"id": "q_schneider_old", "source": "보도자료",
     "title": "슈나이더 일렉트릭, EPC 및 플랜트, 데이터센터 고객 대상 이노베이션 데이 개최..",
     "snippet": "EPC 및 플랜트, 데이터센터 고객을 대상으로 행사를 개최했다",
     "published_at": "2025-10-23T09:00:00+09:00"},
    {"id": "q_gaon", "source": "더구루",
     "title": "가온전선, 美 AI 데이터센터 전력 인프라 연이은 수주",
     "snippet": "가온전선이 미국 AI 데이터센터 전력 인프라 공급을 확대한다",
     "published_at": "2026-06-16T09:00:00+09:00"},
    {"id": "q_roundup", "source": "건설타임즈",
     "title": "[굿모닝! 17일 건설 업계 소식] 대우건설·롯데건설·GS건설·현대건설",
     "snippet": "여러 건설사 소식을 모은 roundup 기사",
     "published_at": "2026-06-17T07:00:00+09:00"},
    {"id": "q_sams", "source": "매일경제",
     "title": "삼성물산 올해 EPC 수주 목표 10조…SMR 데이터센터 정조준",
     "snippet": "삼성물산이 EPC 수주 목표와 데이터센터 전략을 제시했다",
     "published_at": "2026-06-17T10:00:00+09:00"},
    {"id": "q_smart_challenge", "source": "연합뉴스",
     "title": "국토부, AI·로봇 등 스마트건설 챌린지 개최",
     "snippet": "스마트건설 기술 확산을 위한 국토부 챌린지 행사가 열린다",
     "published_at": "2026-06-17T11:00:00+09:00"},
    {"id": "q_jackpot", "source": "데일리머니",
     "title": "87조 잭팟 터진다…美·이란 종전에 주가 24% 폭등 [종목+]",
     "snippet": "미국과 이란 종전 기대에 관련 종목 주가가 폭등했다",
     "published_at": "2026-06-17T09:00:00+09:00"},
    {"id": "q_cb", "source": "머니투데이",
     "title": "주가상승 자신한 CB 발행…안전판 부재에 역풍 부나",
     "snippet": "주가상승을 자신한 CB 발행이 안전판 부재로 역풍을 맞을 수 있다",
     "published_at": "2026-06-17T09:00:00+09:00"},
    {"id": "q_wonder", "source": "스포츠서울",
     "title": "현대건설 배구단 원더독스, 프로팀 연파하고 우승",
     "snippet": "현대건설 배구단 원더독스가 프로팀을 연파하고 우승했다",
     "published_at": "2026-06-17T09:00:00+09:00"},
]
for _f in FIX:
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
        "lc.fetch_all=lambda *a,**k:[dict(x) for x in FIX]\n"
        "db.init_db(); collector.run(); scoring.score_all(); insight.generate_all()\n"
        "b=briefing.build_brief()\n"
        "rows={r['id']:r for r in db.fetch_articles_with_scores()}\n"
        "def slim(e):\n"
        "    return {'id':e.get('article_id'),'title':e.get('title'),\n"
        "            'reason':e.get('one_line_reason') or e.get('why_it_matters'),\n"
        "            'penalty':e.get('top_exposure_penalty'),\n"
        "            'flags':e.get('top_exposure_flags') or []}\n"
        "def entries(k): return [slim(e) for e in (b.get(k) or [])]\n"
        "reasons={}\n"
        "for k in ('hdec_direct_signals','ai_radar_signals','business_signals',\n"
        "          'competitor_supply_signals','risk_regulation_signals',\n"
        "          'top_new_issues','top_immediate_signals'):\n"
        "    for e in (b.get(k) or []):\n"
        "        reasons.setdefault(e.get('article_id'),[]).append(e.get('one_line_reason') or '')\n"
        "cat_entries=[]\n"
        "for sec in (b.get('category_sections') or []):\n"
        "    for a in (sec.get('top_articles') or []):\n"
        "        cat_entries.append(slim(a))\n"
        "        reasons.setdefault(a.get('article_id'),[]).append(a.get('why_it_matters') or '')\n"
        "out={'mode':b.get('news_data_mode'),\n"
        " 'hdec':entries('hdec_direct_signals'), 'ai':entries('ai_radar_signals'),\n"
        " 'biz':entries('business_signals'), 'comp':entries('competitor_supply_signals'),\n"
        " 'macro':entries('macro_economy_signals'), 'top_new':entries('top_new_issues'),\n"
        " 'top_imm':entries('top_immediate_signals'), 'cat':cat_entries,\n"
        " 'reasons':reasons,\n"
        " 'grades':{i:rows[i]['alert_grade'] for i in rows},\n"
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


def _entry(sim: dict, fid: str) -> dict:
    for section in ("hdec", "ai", "biz", "comp", "macro", "top_new", "top_imm", "cat"):
        for e in sim.get(section) or []:
            if e.get("id") == fid:
                return e
    return {}


def _before(ids: list[str], first: str, second: str) -> bool:
    if first not in ids:
        return False
    if second not in ids:
        return True
    return ids.index(first) < ids.index(second)


def check_static_contract() -> None:
    src = BRIEFING.read_text(encoding="utf-8")
    check("briefing에 top exposure profile helper 존재",
          "_top_exposure_profile" in src and "top_exposure_penalty" in src)
    check("briefing이 45/90일 stale 기준을 상단 노출에 사용",
          "TOP_STALE_DAYS = 45" in src and "TOP_VERY_STALE_DAYS = 90" in src)
    check("briefing이 증권/roundup/supplier/stale flag를 surface",
          all(t in src for t in ("securities_context", "generic_roundup",
                                 "supplier_only", "very_stale")))


def check_pipeline(sim: dict | None) -> None:
    if not sim:
        return
    check("시뮬: live fixture brief 생성", sim.get("mode") == "live")

    hdec_ids = _ids(sim, "hdec")
    ai_ids = _ids(sim, "ai")
    biz_ids = _ids(sim, "biz")
    comp_ids = _ids(sim, "comp")
    top_sections = ("hdec", "ai", "biz", "comp", "top_new", "top_imm")

    check("A: 직접 원전 프로젝트가 현대건설 섹션에 노출", "q_nuke" in hdec_ids,
          f"hdec={hdec_ids}")
    check("A: 직접 원전 프로젝트가 주가/SMR 기사보다 앞섬",
          _before(hdec_ids, "q_nuke", "q_stock_smr"), f"hdec={hdec_ids}")

    for section in ("hdec", "ai", "biz"):
        ids = _ids(sim, section)
        check(f"B: 주가/SMR 기사가 {section} top-1 아님",
              not ids or ids[0] != "q_stock_smr", f"{section}={ids}")
    stock_reasons = sim.get("reasons", {}).get("q_stock_smr") or []
    check("B: 주가/SMR 사유는 자본시장 관찰 유지",
          any("자본시장 관찰" in r for r in stock_reasons), str(stock_reasons))
    stock_entry = _entry(sim, "q_stock_smr")
    check("B: 주가/SMR 기사에 securities/weak source 노출 flag 부착",
          {"securities_context", "weak_source"} <= set(stock_entry.get("flags") or []),
          str(stock_entry))

    check("C: 최근 AI/DC 정책·EPC 기사가 AI 섹션에 노출",
          bool({"q_dc_policy", "q_dc_epc"} & set(ai_ids)), f"ai={ai_ids}")
    check("C: 2025-10-23 슈나이더 기사는 AI top/radar에 없음",
          "q_schneider_old" not in ai_ids, f"ai={ai_ids}")
    old_entry = _entry(sim, "q_schneider_old")
    check("C: 슈나이더 기사에 very_stale flag 부착",
          "very_stale" in (old_entry.get("flags") or []), str(old_entry))

    check("D: 가온전선 supplier-only가 AI direct 기사보다 앞서지 않음",
          ("q_gaon" not in ai_ids)
          or _before(ai_ids, "q_dc_policy", "q_gaon")
          or _before(ai_ids, "q_dc_epc", "q_gaon"),
          f"ai={ai_ids}")
    gaon_entry = _entry(sim, "q_gaon")
    check("D: 가온전선 기사에 supplier_only flag 부착",
          "supplier_only" in (gaon_entry.get("flags") or []), str(gaon_entry))

    check("E: generic roundup이 executive top-1을 차지하지 않음",
          all((not _ids(sim, s)) or _ids(sim, s)[0] != "q_roundup"
              for s in top_sections),
          " / ".join(f"{s}={_ids(sim, s)}" for s in top_sections))
    if "q_roundup" in comp_ids:
        check("E: 경쟁사·공급망에서도 specific EPC 기사가 roundup보다 앞섬",
              _before(comp_ids, "q_sams", "q_roundup"), f"comp={comp_ids}")
    roundup_entry = _entry(sim, "q_roundup")
    check("E: roundup 기사에 generic_roundup flag 부착",
          "generic_roundup" in (roundup_entry.get("flags") or []), str(roundup_entry))

    check("F: old generic reason phrase absent", sim.get("generic_absent") is True)
    smart_reasons = sim.get("reasons", {}).get("q_smart_challenge") or []
    check("F: 국토부 스마트건설 챌린지 사유 유지",
          any("스마트건설 기술 확산" in r for r in smart_reasons), str(smart_reasons))

    all_top = set().union(*(set(_ids(sim, s)) for s in top_sections))
    check("F: stock-hype 잭팟은 executive top/AI에 없음",
          "q_jackpot" not in all_top and "q_jackpot" not in ai_ids,
          f"top={sorted(all_top)} ai={ai_ids}")
    check("F: stock-hype 잭팟 등급=제외",
          (sim.get("grades") or {}).get("q_jackpot") == GRADE_EXCLUDED,
          str((sim.get("grades") or {}).get("q_jackpot")))
    check("F: CB 발행은 AI/거시 top에 없음",
          "q_cb" not in ai_ids and "q_cb" not in _ids(sim, "macro"),
          f"ai={ai_ids} macro={_ids(sim, 'macro')}")
    check("F: 원더독스는 현대건설 직접 섹션에 없음",
          "q_wonder" not in hdec_ids, f"hdec={hdec_ids}")


def main() -> int:
    print(f"== verify_executive_top_exposure_quality @ {ROOT} ==")
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
