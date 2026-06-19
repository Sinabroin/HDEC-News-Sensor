"""P0-D3S 검증기 — 임원 편집 품질(AI 탭/리스크 사건/재무·노이즈 억제) 회귀 검사.

결정적·네트워크 없음·temp DB 격리. 저장소의 radar.db는 절대 건드리지 않는다.

검사 목표 (Goal A~F):
- A 재무/주식/투자조언 노이즈 억제: 종목추천성 기사는 즉시 알림 아님 / AI Top 아님 /
  리스크 사건 아님 (구체 운영 리스크 액션이 제목에 있으면 리스크가 이긴다).
- B AI 탭 적격성: AI 상단은 직접 건설/인프라/현대건설 관련성 신호만.
- C AI 탭 정렬 라벨이 '점수순' 단정이 아니라 '종합 우선순위순'으로 정정됨.
- D 임원 리스크 사건 최대 5건(고심각 아니면 최대 3건) + 분리 안내.
- E AI 데이터센터·전력 인프라 근거 목록은 DC/전력 인프라 + 건설 앵커 동시 요구.

사용법:
    python3 scripts/verify_executive_editorial_quality.py
"""

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RADAR_DB = ROOT / "radar.db"
REPORT_BUILDER = ROOT / "scripts" / "build_static_report.py"
sys.path.insert(0, str(ROOT))

KST = timezone(timedelta(hours=9))


def _recent(days_ago: int) -> str:
    """현재시각 기준 상대 날짜(시점 비의존) — fixture가 stale 캡에 걸리지 않게."""
    return (datetime.now(KST) - timedelta(days=days_ago)).strftime(
        "%Y-%m-%dT09:00:00+09:00")


# ---- fixture: 미션 시나리오 (제목·출처만으로 결정적 판정) ----
FIX = [
    {"id": "finance_ai_noise", "source": "YTN",
     "title": "'삼전'도 없고 '하닉'도 없다? 근데 너무 올랐다? 그럼 '이것' 사세요",
     "snippet": "AI인프라 ETF와 스페이스X 관련 종목, 전력기기·쿨링·광통신 수혜주를 짚었다"},
    {"id": "true_ai_datacenter", "source": "전자신문",
     "title": "건설사 AI 데이터센터 수주전 가속…전력·냉각 기술이 승부처",
     "snippet": "AI 데이터센터 EPC 수주를 두고 건설사들이 전력망과 냉각 기술 경쟁에 나섰다"},
    {"id": "true_smr", "source": "연합뉴스",
     "title": "현대건설, 유럽 SMR·원전 EPC 수주 파이프라인 확대",
     "snippet": "현대건설이 유럽 SMR과 원전 EPC 수주 파이프라인을 확대한다"},
    {"id": "true_construction_ai", "source": "대한경제",
     "title": "건설현장 AI 영상인식 안전관리 의무화 추진",
     "snippet": "건설현장 AI 영상인식 기반 안전관리 의무화가 추진된다"},
    {"id": "stock_risk_noise", "source": "머니투데이",
     "title": "현대건설 주가, 28% 급등 마감... 시간외선 상승폭 축소",
     "snippet": "현대건설 주가가 장중 28% 급등 마감했고 시간외 거래에서 상승폭이 축소됐다"},
    {"id": "true_risk_event", "source": "서울신문",
     "title": "[단독] 철근누락 현대건설 벌점 2점 통보…선분양·공공수주 경고등",
     "snippet": "현대건설 철근누락으로 벌점 2점이 통보돼 선분양과 공공수주 리스크가 커졌다"},
    # AI 탭이 비지 않도록 강한 직접 AI/DC 신호 1건 추가(헤드라인 surface 점유 분산용)
    {"id": "true_dc_policy", "source": "한국경제",
     "title": "특별법 시행 앞둔 AI 데이터센터…건설사 전력망·냉각 솔루션 수주 경쟁",
     "snippet": "AI 데이터센터 특별법 시행을 앞두고 건설사들이 전력망·냉각 EPC 수주 경쟁을 벌인다"},
]
for i, item in enumerate(FIX):
    item.setdefault("published_at", _recent(1 + i % 3))
    item.setdefault("url", f"https://d3s.test/{item['id']}")

GRADE_INSTANT = "즉시 알림 후보"
GRADE_EXCLUDED = "제외"

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


# ---------- 순수 함수 단위 검사 (DB 미접촉) ----------

def check_units() -> None:
    from app import article_quality as aq, briefing, risk_events

    by = {f["id"]: f for f in FIX}

    def assess(fid):
        return aq.assess(by[fid]["source"], by[fid]["title"], by[fid]["snippet"])

    def elig(fid):
        return briefing._ai_top_eligible(by[fid])

    def risk_key(fid):
        return risk_events.event_key_for_row(
            {"id": fid, "title": by[fid]["title"], "snippet": by[fid]["snippet"],
             "source": by[fid]["source"]})

    # A: 종목추천성(YTN)·시세성(현대건설 주가) → 리스크 사건 아님
    check("A: finance_ai_noise는 stock_hype (AI 핵심 섹션 강등)",
          assess("finance_ai_noise")["stock_hype"])
    check("A: finance_ai_noise는 리스크 사건 키 없음", risk_key("finance_ai_noise") is None)
    check("A: stock_risk_noise(현대건설 주가 급등)는 리스크 사건 키 없음",
          risk_key("stock_risk_noise") is None)
    # 구체 리스크 액션이 제목에 있으면 시장 표현이 섞여도 리스크가 이긴다
    override = risk_events.event_key_for_row(
        {"id": "o", "title": "건설사 영업정지에 건설주 ETF 급락", "snippet": "",
         "source": "한국경제"})
    check("A: 구체 리스크(영업정지)+시장표현 → 리스크 사건 유지(시장 게이트 우회)",
          override is not None, str(override))

    # B: 직접 AI/DC/SMR/건설 AI는 적격, 정상 기사는 stock_hype 아님
    check("B: true_ai_datacenter 적격 + stock_hype 아님",
          elig("true_ai_datacenter") and not assess("true_ai_datacenter")["stock_hype"])
    check("B: true_smr 적격 + stock_hype 아님",
          elig("true_smr") and not assess("true_smr")["stock_hype"])
    check("B: 순수 generic AI(반도체 종목성)는 AI 적격성 탈락",
          not briefing._ai_top_eligible(
              {"title": "AI 반도체 투자 사이클 기대", "snippet": "반도체 업황 전망"}))

    # true_risk_event는 리스크 사건 키를 받는다
    check("F: true_risk_event는 리스크 사건 키 보유",
          risk_key("true_risk_event") is not None)


# ---------- 정적 계약 검사 ----------

def check_static() -> None:
    rep = REPORT_BUILDER.read_text(encoding="utf-8")
    check("C: AI 탭 라벨이 '점수순' 단정 제거",
          "AI 데이터센터·전력·SMR·스마트건설 · 점수순" not in rep)
    check("C: AI 탭 라벨이 '종합 우선순위순'으로 정정 + 기준 노출",
          "종합 우선순위순" in rep and "관련성·중요도·출처·최신성" in rep)
    check("D: 임원 리스크 사건 분리 안내 문구 존재",
          "추가 관찰 신호는 운영자 검수 화면에 분리했습니다." in rep)
    check("D: 임원 뷰 risk 캡 로직(고심각 5 / 그 외 3) 존재",
          "send_candidate" in rep and "cap = 5 if high_severity else 3" in rep)
    brf = (ROOT / "app" / "briefing.py").read_text(encoding="utf-8")
    check("B: briefing에 AI 적격성 앵커/필터 존재",
          "AI_TOP_RELEVANCE_ANCHORS" in brf and "_ai_top_eligible" in brf)
    check("E: briefing에 dc_power 근거 적격성 필터 존재",
          "_dc_power_evidence_ok" in brf and "DC_POWER_INFRA_TERMS" in brf)


# ---------- 파이프라인 시뮬 (temp DB subprocess, fetch_all 패치) ----------

def _run_sim() -> dict | None:
    code = (
        "import os, sys, json, tempfile\n"
        "os.environ['DB_PATH']=os.path.join(tempfile.mkdtemp(),'t.db')\n"
        "os.environ['APP_MODE']='mock'; os.environ['NEWS_MODE']='live'\n"
        "sys.path.insert(0,'.'); sys.path.insert(0,'scripts')\n"
        "FIX=" + json.dumps(FIX, ensure_ascii=False) + "\n"
        "from app import db, collector, scoring, insight, briefing, live_collector as lc\n"
        "lc.fetch_all=lambda *a,**k:[dict(x) for x in FIX]\n"
        "db.init_db(); collector.run(); scoring.score_all(); insight.generate_all()\n"
        "b=briefing.build_brief()\n"
        "rows={r['id']:r for r in db.fetch_articles_with_scores()}\n"
        "def ids(k):return [s.get('article_id') for s in (b.get(k) or [])]\n"
        "risk_ev=[a.get('article_id') for e in (b.get('risk_event_clusters') or [])\n"
        "         for a in (e.get('supporting_articles') or [])]\n"
        "dc=next((s for s in (b.get('category_sections') or [])\n"
        "         if s.get('category_key')=='dc_power'), {})\n"
        "dc_ids=[a.get('article_id') for a in (dc.get('top_articles') or [])]\n"
        "from build_static_report import render_report_html\n"
        "exec_html,_=render_report_html(b, audience='executive')\n"
        "out={'mode':b.get('news_data_mode'),\n"
        " 'ai':ids('ai_radar_signals'),'risk_reg':ids('risk_regulation_signals'),\n"
        " 'top_imm':ids('top_immediate_signals'),'top_new':ids('top_new_issues'),\n"
        " 'biz':ids('business_signals'),'macro':ids('macro_economy_signals'),\n"
        " 'hdec':ids('hdec_direct_signals'),'comp':ids('competitor_supply_signals'),\n"
        " 'risk_event_ids':sorted(set(risk_ev)),\n"
        " 'risk_event_count':len(b.get('risk_event_clusters') or []),\n"
        " 'dc_ids':dc_ids,\n"
        " 'grades':{i:rows[i]['alert_grade'] for i in rows},\n"
        " 'review_excluded':[it.get('article_id') for it in ((b.get('review_excluded_evidence') or {}).get('items') or [])],\n"
        " 'exec_has_pricetalk':(\"삼전\" in exec_html and \"사세요\" in exec_html)}\n"
        "print(json.dumps(out, ensure_ascii=False))\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True,
                          text=True, cwd=ROOT, timeout=240)
    if proc.returncode != 0:
        check("파이프라인 시뮬 실행", False, (proc.stderr or "").strip()[-700:])
        return None
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        check("파이프라인 시뮬 출력 파싱", False, (proc.stdout or "")[-500:])
        return None


def check_pipeline(sim: dict | None) -> None:
    if not sim:
        return
    check("시뮬: live 모드로 fixture 파이프라인 통과", sim.get("mode") == "live",
          str(sim.get("mode")))
    top = set().union(*(set(sim.get(s) or []) for s in (
        "ai", "risk_reg", "top_imm", "top_new", "biz", "macro", "hdec", "comp")))

    # A: 종목추천성 YTN — 즉시 아님 / AI·핵심 섹션 아님 / 리스크 사건 아님 / 참고 가능
    check("A: finance_ai_noise가 즉시 알림 후보 아님",
          sim["grades"].get("finance_ai_noise") != GRADE_INSTANT,
          str(sim["grades"].get("finance_ai_noise")))
    check("A: finance_ai_noise가 어떤 임원 핵심 섹션에도 없음 (AI Top 포함)",
          "finance_ai_noise" not in top, f"top={sorted(top)}")
    check("A: finance_ai_noise가 리스크 사건에 없음",
          "finance_ai_noise" not in (sim.get("risk_event_ids") or []))

    # A: 현대건설 주가 급등 — 즉시 아님 / 리스크 사건 아님 / 리스크·규제 top 아님
    check("A: stock_risk_noise가 즉시 알림 후보 아님",
          sim["grades"].get("stock_risk_noise") != GRADE_INSTANT,
          str(sim["grades"].get("stock_risk_noise")))
    check("A: stock_risk_noise가 리스크 사건에 없음",
          "stock_risk_noise" not in (sim.get("risk_event_ids") or []))
    check("A: stock_risk_noise가 리스크·규제 섹션에 없음",
          "stock_risk_noise" not in (sim.get("risk_reg") or []))

    # B: 직접 AI/DC/SMR 신호는 임원 surface에 노출
    check("B: true_ai_datacenter/true_dc_policy/true_smr 중 1건 이상 AI 또는 수주·해외 노출",
          bool({"true_ai_datacenter", "true_dc_policy", "true_smr"}
               & (set(sim.get("ai") or []) | set(sim.get("biz") or [])
                  | set(sim.get("hdec") or []))),
          f"ai={sim.get('ai')} biz={sim.get('biz')} hdec={sim.get('hdec')}")
    check("B: AI 섹션에 종목추천성/시세성 기사 없음",
          not ({"finance_ai_noise", "stock_risk_noise"} & set(sim.get("ai") or [])),
          f"ai={sim.get('ai')}")

    # E: dc_power 근거 목록은 직접 DC/전력 인프라 기사만 — 종목추천/시세성 없음
    check("E: dc_power 근거 목록에 종목추천/시세성 기사 없음",
          not ({"finance_ai_noise", "stock_risk_noise"} & set(sim.get("dc_ids") or [])),
          f"dc_ids={sim.get('dc_ids')}")

    # F: true_risk_event는 리스크 사건에 노출
    check("F: true_risk_event가 리스크 사건에 노출",
          "true_risk_event" in (sim.get("risk_event_ids") or []),
          str(sim.get("risk_event_ids")))

    # 임원 HTML에 종목추천성 제목이 보이지 않음(참고/감사는 운영자 뷰로 분리)
    check("A: 임원 HTML에 '삼전…사세요' 종목추천성 제목 비노출",
          not sim.get("exec_has_pricetalk"))


def main() -> int:
    print(f"== verify_executive_editorial_quality @ {ROOT} ==")
    os.environ["DB_PATH"] = os.path.join(
        tempfile.mkdtemp(prefix="hdec_d3s_"), "verify.db")
    os.environ.setdefault("APP_MODE", "mock")
    before = _db_state()

    check_units()
    check_static()
    sim = _run_sim()
    check_pipeline(sim)

    check("repo radar.db 미변경 (temp DB 격리)", _db_state() == before)

    if _failures:
        print(f"\nRESULT: FAIL ({len(_failures)} failed)")
        for name in _failures:
            print(f"  - {name}")
        return 1
    print("\nRESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
