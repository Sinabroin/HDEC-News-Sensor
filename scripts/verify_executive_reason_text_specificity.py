"""P0-D3B 검증기 — Executive Reason Text Specificity (결정적·네트워크 없음).

분류가 맞아도 '왜 중요한가' 사유가 모든 현대건설 기사에 generic으로 붙던 문제
("현대건설 연관 신호 — 수주 경쟁력·시장 포지션 영향권")를 유형별 사유로 바꿨는지 검사한다.

검사 대상(순수 함수 + 파이프라인):
- insight.executive_reason: raw 제목+스니펫 + decision 플래그(stock_hype/is_finance/
  hdec_direct)만 입력으로 보고 도시정비/원전·SMR/데이터센터/고객 AI/리스크/재무/분양 PR/
  해외/스마트건설/정책별 명사형 사유 copy를 고른다(생성 라벨 입력 금지, 라우팅/등급 불변).
- briefing.build_brief: 저장된 카테고리 implication 대신 display_reasons(executive_reason)를
  카드 one_line_reason·카테고리 드릴다운 why_it_matters에 싣는다 — generic 문구가 사라진다.

정직성 계약:
- 목표가·증권 리포트성 기사는 '직접 수주 win'으로 과장하지 않고 자본시장 관찰로 강등한다
  (현대건설은 article_quality stock_hype 면제 대상이라 reason 레이어가 별도로 본다).
- 단순 분양 PR·스마트건설 일반은 직접 HDEC 영향으로 과장하지 않는다.
- P0-D3A 오탐 가드는 그대로 유지된다(잭팟→AI 아님·CB→거시 primary 아님·원더독스→현대건설 신호 아님).

원칙:
- 분류 로직 단일 소유(app/radar.py·app/decision_relevance.py)는 재계산하지 않는다.
- 라이브 인터넷에 의존하지 않는다 — fetch_all을 fixture로 패치해 temp DB subprocess에서 돈다.
- mock 데모 숫자(28/21/3/4/14/7)는 불변이다(사유 copy는 표시 전용 — 등급/점수에 영향 없음).

사용법:
    python3 scripts/verify_executive_reason_text_specificity.py
"""

import json
import os
import py_compile
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSIGHT = ROOT / "app" / "insight.py"
BRIEFING = ROOT / "app" / "briefing.py"
DR_MODULE = ROOT / "app" / "decision_relevance.py"
BRIEF_BUILDER = ROOT / "scripts" / "build_executive_brief.py"
RADAR_DB = ROOT / "radar.db"

GENERIC = "수주 경쟁력·시장 포지션 영향권"  # 제거 대상 generic 사유 조각
GRADE_EXCLUDED = "제외"
MOCK_BASELINE = {"total_articles": 28, "total_signals": 21, "immediate_count": 3,
                 "daily_count": 4, "weekly_count": 14, "excluded_count": 7}

# ---- fixture: 유형별 사유 + P0-D3A 가드 (제목·스니펫만으로 결정적 판정) ----
# must = 사유에 적어도 하나는 포함돼야 하는 조각, forbid = 어떤 사유에도 없어야 하는 조각.
FIX = [
    {"id": "r_city", "source": "서울경제",
     "title": "현대건설, 올해 도시정비사업 수주액 6조원 돌파…업계 1위",
     "snippet": "현대건설이 도시정비 수주액 6조원을 돌파해 업계 1위에 올랐다",
     "must": ["도시정비", "수주 경쟁력"], "forbid": [GENERIC]},
    {"id": "r_city_dc", "source": "전자신문",
     "title": "[기획] 현대건설, 도시정비 12조·데이터센터 양 축 강화",
     "snippet": "현대건설이 도시정비 12조와 데이터센터 사업을 양 축으로 강화한다",
     "must": ["도시정비", "데이터센터"], "forbid": [GENERIC]},
    {"id": "r_custai", "source": "데일리안",
     "title": "현대건설, 생성형 AI 분양 상담사 도입…24시간 청약 상담",
     "snippet": "현대건설이 생성형 AI 분양 상담사를 도입해 24시간 청약 상담을 제공한다",
     "must": ["고객접점", "분양 운영", "상담 자동화"], "forbid": ["수주 경쟁력", GENERIC]},
    {"id": "r_cb", "source": "한국경제",
     "title": "현대건설, 0% 금리 5000억 전환사채 발행",
     "snippet": "현대건설이 0% 금리로 5000억원 전환사채를 발행했다",
     "must": ["자본시장", "재무전략", "자금조달"], "forbid": ["거시경제 단독", GENERIC]},
    {"id": "r_sec", "source": "대신증권",
     "title": "대신證 현대건설, 원전으로 한 번 더 도약 목표가 7만원 제시",
     "snippet": "대신증권이 현대건설에 대해 원전 모멘텀을 들어 목표가 7만원을 제시했다",
     # 목표가 리포트 — 원전을 언급해도 '직접 수주 win'으로 과장 금지(자본시장 관찰).
     "must": ["원전", "자본시장 관찰"], "forbid": ["수주 기회", "수주 경쟁력", GENERIC]},
    {"id": "r_risk", "source": "이데일리",
     "title": "현대건설, 벌점 사전통보…분양·수주 영향권",
     "snippet": "현대건설이 벌점 사전통보를 받아 분양·수주에 영향이 우려된다",
     "must": ["입찰 자격", "평판", "컴플라이언스 리스크"], "forbid": [GENERIC]},
    {"id": "r_smartc", "source": "대학신문",
     "title": "스마트 건설 시대 이끄는 전문 엔지니어 양성 집중",
     "snippet": "스마트 건설 시대를 이끄는 전문 엔지니어 양성에 집중한다",
     # 직접 HDEC 영향으로 과장 금지 — 기술 확산/참고 수준 모니터링.
     "must": ["스마트건설", "검토", "참고", "모니터링"],
     "forbid": ["현대건설 직접", "수주 경쟁력", GENERIC]},
    # ---- P0-D3A 가드 유지 ----
    {"id": "r_jackpot", "source": "데일리머니", "guard": "stockhype",
     "title": "87조 잭팟 터진다…美·이란 종전에 주가 24% 폭등 [종목+]",
     "snippet": "미국과 이란 종전 기대에 관련 종목 주가가 24% 폭등했다",
     "must": ["자본시장 관찰"], "forbid": ["수주", GENERIC]},
    {"id": "r_cb2", "source": "머니투데이", "guard": "finance",
     "title": "주가상승 자신한 CB 발행…안전판 부재에 역풍 부나",
     "snippet": "주가상승을 자신한 CB 발행이 안전판 부재로 역풍을 맞을 수 있다",
     "must": ["자본시장"], "forbid": ["ai", GENERIC]},
    {"id": "r_wonder", "source": "스포츠서울", "guard": "not_hdec",
     "title": "현대건설 배구단 원더독스, 프로팀 연파하고 우승",
     "snippet": "현대건설 배구단 원더독스가 프로팀을 연파하고 우승했다",
     "must": ["점검 대상", "참고"], "forbid": ["수주 경쟁력", "수주 기회", GENERIC]},
]
for _f in FIX:
    _f.setdefault("published_at", "2026-06-14T09:00:00+09:00")
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


def by_id(fid):
    return next(f for f in FIX if f["id"] == fid)


def _db_state():
    if not RADAR_DB.exists():
        return None
    stat = RADAR_DB.stat()
    return (stat.st_mtime_ns, stat.st_size)


def _clean_env(**extra: str) -> dict:
    env = {**os.environ, "APP_MODE": "mock"}
    for key in ("MESSAGE", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_IDS",
                "DB_PATH", "REPORT_URL", "NEWS_MODE"):
        env.pop(key, None)
    env.update(extra)
    return env


# ---------- 정적 검사 ----------

def check_py_compile() -> None:
    bad = []
    targets = sorted(list((ROOT / "scripts").glob("*.py"))
                     + list((ROOT / "app").glob("*.py")))
    with tempfile.TemporaryDirectory(prefix="hdec_pyc_") as tmp:
        for i, path in enumerate(targets):
            try:
                py_compile.compile(str(path), cfile=os.path.join(tmp, f"{i}.pyc"),
                                   doraise=True)
            except py_compile.PyCompileError as exc:
                bad.append(f"{path.name}: {exc.msg.strip().splitlines()[-1]}")
    check("py_compile scripts/*.py app/*.py", not bad, "; ".join(bad))


def check_integration() -> None:
    ins = INSIGHT.read_text(encoding="utf-8")
    check("insight가 유형별 사유 함수(executive_reason) 정의",
          "def executive_reason(" in ins)
    check("insight가 유형별 사유 copy 상수 정의 (DC/증권/분양 PR)",
          all(c in ins for c in ("REASON_DATACENTER", "REASON_SECURITIES",
                                 "REASON_SALES_PROMO", "REASON_FINANCE")))
    check("insight가 증권 리서치 마커(_RT_SECURITIES, 목표가) 별도 인식",
          "_RT_SECURITIES" in ins and "목표가" in ins)
    brf = BRIEFING.read_text(encoding="utf-8")
    check("briefing이 executive_reason 기반 display_reasons를 카드/드릴다운에 사용",
          "display_reasons" in brf and "insight.executive_reason(" in brf)
    check("briefing이 저장된 카테고리 implication을 카드 사유로 더 이상 싣지 않음",
          "implications[row[\"id\"]]" not in brf)
    dr = DR_MODULE.read_text(encoding="utf-8")
    check("decision_relevance.classify가 stock_hype 플래그를 표시 소비처에 surface",
          '"stock_hype": flags["stock_hype"]' in dr)


# ---------- 순수 함수 단위 검사 (in-process, DB 미접촉) ----------

def _import_app():
    sys.path.insert(0, str(ROOT))
    os.environ.setdefault("APP_MODE", "mock")
    from app import decision_relevance, insight, radar
    return insight, radar, decision_relevance


def _reason_for(insight, dr, f) -> str:
    row = {"title": f["title"], "snippet": f["snippet"], "source": f["source"],
           "topic_candidates": "[]"}
    d = dr.classify(row, "general")
    return insight.executive_reason(
        f["title"], f["snippet"], is_stock_hype=d["stock_hype"],
        is_finance=d["is_finance"], hdec_direct=d["hdec_direct"])


def _assert_reason(fid: str, reason: str, where: str) -> None:
    f = by_id(fid)
    low = reason.lower()
    ok_must = any(m in reason for m in f["must"])
    bad = [x for x in f["forbid"] if x.lower() in low]
    check(f"{where}: {fid} 사유가 유형별 표현 포함 {f['must']}", ok_must, reason)
    check(f"{where}: {fid} 사유에 과장/generic 표현 없음 {f['forbid']}",
          not bad, f"발견: {bad} — '{reason}'")


def check_units() -> None:
    insight, radar, dr = _import_app()
    reasons = {f["id"]: _reason_for(insight, dr, f) for f in FIX}
    for fid, reason in reasons.items():
        _assert_reason(fid, reason, "unit")

    # generic 사유 자체가 어떤 fixture에도 나오지 않음.
    check("unit: generic '수주 경쟁력·시장 포지션 영향권'가 어떤 사유에도 없음",
          all(GENERIC not in r for r in reasons.values()))

    # 사유가 한 가지로 뭉치지 않고 유형별로 갈린다 (최소 6종).
    distinct = set(reasons.values())
    check("unit: 사유가 유형별로 분화 (≥6종)", len(distinct) >= 6, f"{len(distinct)}종")

    # P0-D3A 라우팅 가드 유지 (사유와 별개로 분류가 그대로인지).
    def route(f):
        row = {"title": f["title"], "snippet": f["snippet"], "source": f["source"],
               "topic_candidates": "[]"}
        d = dr.classify(row, "general")
        return dr.override_radar_section(radar.classify_section(row, "general"), d), d

    sec_j, _ = route(by_id("r_jackpot"))
    check("guard: 잭팟 기사 → AI/거시 아님 (other)", sec_j == radar.OTHER, sec_j)
    sec_c, dec_c = route(by_id("r_cb2"))
    check("guard: CB 발행 → 거시 primary 아님 (재무 거시 비혼입)",
          dec_c["primary_executive_section"] != dr.MACRO,
          dec_c["primary_executive_section"])
    check("guard: CB 발행 → AI 아님", sec_c != radar.AI, sec_c)
    _, dec_w = route(by_id("r_wonder"))
    check("guard: 원더독스 → 현대건설 직접 임원 신호 아님",
          dec_w["primary_executive_section"] != dr.HDEC_DIRECT,
          dec_w["primary_executive_section"])
    _, dec_sec = route(by_id("r_sec"))
    check("guard: 목표가 리포트는 현대건설 직접 발주 win으로 과장하지 않음 (사유=자본시장 관찰)",
          "자본시장 관찰" in reasons["r_sec"], reasons["r_sec"])


# ---------- 파이프라인 시뮬레이션 (temp DB subprocess, fetch_all 패치) ----------

def _run_pipeline_sim() -> dict | None:
    code = (
        "import os, sys, json, tempfile\n"
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
        "reasons={}\n"
        "def add(aid,r):\n"
        "    if aid and r:\n"
        "        reasons.setdefault(aid,[])\n"
        "        if r not in reasons[aid]: reasons[aid].append(r)\n"
        "SEC=['ai_radar_signals','macro_economy_signals','hdec_direct_signals',\n"
        "     'business_signals','competitor_supply_signals','risk_regulation_signals',\n"
        "     'top_new_issues','top_immediate_signals']\n"
        "for k in SEC:\n"
        "    for s in (b.get(k) or []): add(s.get('article_id'), s.get('one_line_reason'))\n"
        "for sec in (b.get('category_sections') or []):\n"
        "    for a in (sec.get('top_articles') or []): add(a.get('article_id'), a.get('why_it_matters'))\n"
        "for a in ((b.get('review_excluded_evidence') or {}).get('items') or []):\n"
        "    add(a.get('article_id'), a.get('why_it_matters'))\n"
        "def ids(k): return [s.get('article_id') for s in (b.get(k) or [])]\n"
        "out={'mode':b['news_data_mode'],\n"
        " 'reasons':reasons,\n"
        " 'ai':ids('ai_radar_signals'),'macro':ids('macro_economy_signals'),\n"
        " 'hdec':ids('hdec_direct_signals'),\n"
        " 'generic_present': '" + GENERIC + "' in json.dumps(b, ensure_ascii=False),\n"
        " 'grades':{i:rows[i]['alert_grade'] for i in rows}}\n"
        "print(json.dumps(out, ensure_ascii=False))\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True,
                          text=True, cwd=ROOT, timeout=240)
    if proc.returncode != 0:
        check("파이프라인 시뮬레이션 실행", False, (proc.stderr or "").strip()[-400:])
        return None
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        check("파이프라인 시뮬레이션 출력 파싱", False, (proc.stdout or "")[-300:])
        return None


def check_pipeline(sim: dict | None) -> None:
    if not sim:
        return
    check("시뮬: live 모드로 fixture 파이프라인 통과", sim.get("mode") == "live")
    check("시뮬: 리포트 전체에 generic '수주 경쟁력·시장 포지션 영향권' 없음",
          not sim.get("generic_present"))
    reasons = sim.get("reasons") or {}

    # 각 fixture가 브리프(카드/드릴다운) 어딘가에 유형별 사유로 노출된다.
    for f in FIX:
        seen = reasons.get(f["id"]) or []
        if not check(f"시뮬: {f['id']} 사유가 브리프에 노출됨", bool(seen)):
            continue
        ok_must = any(any(m in r for m in f["must"]) for r in seen)
        bad = [r for r in seen for x in f["forbid"] if x.lower() in r.lower()]
        check(f"시뮬: {f['id']} 사유가 유형별 표현 포함 {f['must']}", ok_must, str(seen))
        check(f"시뮬: {f['id']} 사유에 과장/generic 표현 없음 {f['forbid']}",
              not bad, str(bad[:2]))

    # P0-D3A 라우팅 가드 — 사유 개선이 분류를 되돌리지 않았는지.
    S = {k: set(sim.get(k) or []) for k in ("ai", "macro", "hdec")}
    grades = sim.get("grades") or {}
    check("guard 시뮬: 잭팟이 AI 섹션에 없음", "r_jackpot" not in S["ai"])
    check("guard 시뮬: 잭팟 등급=제외 (stock-hype)",
          grades.get("r_jackpot") == GRADE_EXCLUDED, str(grades.get("r_jackpot")))
    check("guard 시뮬: CB 발행이 거시경제 섹션에 없음", "r_cb2" not in S["macro"])
    check("guard 시뮬: 원더독스가 현대건설 직접 섹션에 없음", "r_wonder" not in S["hdec"])


# ---------- mock 무결성 ----------

def check_mock_integrity() -> None:
    proc = subprocess.run([sys.executable, str(BRIEF_BUILDER), "--json"],
                          capture_output=True, text=True,
                          env=_clean_env(), cwd=ROOT, timeout=300)
    if not check("mock brief --json 동작", proc.returncode == 0,
                 (proc.stderr or "").strip()[-200:]):
        return
    b = json.loads(proc.stdout)
    check("mock news_data_mode=mock 유지", b.get("news_data_mode") == "mock")
    got = {k: b.get(k) for k in MOCK_BASELINE}
    check("mock 데모 숫자 불변 (사유 copy는 표시 전용 — 등급/점수 불변)",
          got == MOCK_BASELINE, f"got={got} expect={MOCK_BASELINE}")
    check("mock 리포트에 generic '수주 경쟁력·시장 포지션 영향권' 없음",
          GENERIC not in json.dumps(b, ensure_ascii=False))

    # mock 카드/드릴다운 사유가 한 문구로 뭉치지 않고 분화돼 있다.
    seen = set()
    for k in ("ai_radar_signals", "hdec_direct_signals", "business_signals",
              "risk_regulation_signals", "top_new_issues"):
        for s in (b.get(k) or []):
            if s.get("one_line_reason"):
                seen.add(s["one_line_reason"])
    for sec in (b.get("category_sections") or []):
        for a in (sec.get("top_articles") or []):
            if a.get("why_it_matters"):
                seen.add(a["why_it_matters"])
    check("mock 사유가 유형별로 분화 (≥4종)", len(seen) >= 4, f"{len(seen)}종")


def main() -> int:
    print(f"== verify_executive_reason_text_specificity @ {ROOT} ==")
    os.environ["DB_PATH"] = os.path.join(
        tempfile.mkdtemp(prefix="hdec_rtv_"), "verify.db")
    db_before = _db_state()

    check_py_compile()
    check_integration()
    check_units()

    sim = _run_pipeline_sim()
    check_pipeline(sim)

    check_mock_integrity()

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
