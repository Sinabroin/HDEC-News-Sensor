"""P0-D3A 검증기 — Topic Classification Contract & False Positive Guard (결정적·네트워크 없음).

확정된 오탐 3종을 분류/라우팅 레이어에서 닫았는지 fixture로 결정적으로 검사한다:

A. AI 오탐 — '잭팟…주가 폭등 [종목+]'·'건설주 폭등 재건 특수' 같은 종전/테마 기사가 AI
   섹션에 들어가지 않는다. radar는 raw 제목+스니펫만 보고, collector가 50% 토큰 매칭으로
   주입한 topic_candidates('현대건설 데이터센터' 등)는 AI 증거로 쓰지 않는다 (주입 우회 차단).
   단 'AI 데이터센터'·'SMR 발주' 등 raw에 직접 AI 증거가 있으면 AI로 유지된다.
B. 거시 오탐 — 현대건설 전환사채/CB 등 재무·자본시장 기사가 거시경제 레이더(FX·유가·금리)에
   섞이지 않는다. is_finance면 override가 AI를 거시가 아니라 other로 내리고, 현대건설 재무는
   [현대건설 직접](decision 멤버십)으로만 노출된다. 단 데이터센터 사업 전략 CB는 AI 유지.
C. 현대건설 오탐 — '현대건설 직접 언급(is_hdec_direct)'과 '임원 전략 신호(strategic/primary)'를
   분리한다. 분양/견본주택 PR·스포츠 구단(원더독스) 기사는 직접 언급이어도 전략 신호가 아니다.
   현대차 사옥(현대차 ≠ 현대건설)·코스피 지수선물은 애초에 현대건설 직접이 아니다.

핵심 원칙:
- 분류 로직은 순수 함수(app/radar.py, app/decision_relevance.py, app/article_quality.py)가
  단일 소유한다 — fixture로 결정적으로 검사한다.
- 라이브 인터넷 결과에 의존하지 않는다 — fetch_all을 fixture로 패치해 파이프라인을 temp DB
  subprocess에서 돌린다. 저장소의 radar.db는 절대 건드리지 않는다.
- mock 데모 숫자는 그대로 유지된다 (분류 가드가 mock을 오염시키지 않음).

사용법:
    python3 scripts/verify_executive_topic_classification.py
"""

import json
import os
import py_compile
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RADAR = ROOT / "app" / "radar.py"
DR_MODULE = ROOT / "app" / "decision_relevance.py"
AQ_RULES = ROOT / "data" / "article_quality_rules.json"
BRIEF_BUILDER = ROOT / "scripts" / "build_executive_brief.py"
RADAR_DB = ROOT / "radar.db"

GRADE_EXCLUDED = "제외"
MOCK_BASELINE = {"total_articles": 28, "total_signals": 21, "immediate_count": 3,
                 "daily_count": 4, "weekly_count": 14, "excluded_count": 7}

# ---- fixture: 미션 회귀 시나리오 (제목·스니펫만으로 결정적 판정) ----
# kind 태그로 단위·파이프라인 검사를 구동한다.
FIX = [
    {"id": "t_jackpot", "source": "데일리머니", "kind": "not_ai_stockhype",
     "title": "87조 잭팟 터진다…美·이란 종전에 주가 24% 폭등 [종목+]",
     "snippet": "미국과 이란 종전 기대에 관련 종목 주가가 24% 폭등했다"},
    {"id": "t_iran_hdec", "source": "서울경제", "kind": "not_ai",
     "title": "미·이란 종전 특수 기대 현대건설…대이란 경제 제재 해지 여부는 변수",
     "snippet": "미·이란 종전 특수 기대에 현대건설이 주목받으나 제재 해지가 변수다"},
    {"id": "t_constock", "source": "이데일리", "kind": "not_ai_stockhype",
     "title": "중동 '종전 합의'에 韓 건설주 '폭등'…450조 재건 특수",
     "snippet": "중동 종전 합의 기대에 한국 건설주가 폭등하며 재건 특수가 거론된다"},
    {"id": "t_hdec_cb", "source": "한국경제", "kind": "hdec_finance",
     "title": "현대건설, '0% 금리' 5000억 전환사채 발행",
     "snippet": "현대건설이 0% 금리로 5000억원 규모 전환사채를 발행했다"},
    {"id": "t_cb", "source": "머니투데이", "kind": "finance",
     "title": "주가상승 자신한 CB 발행…안전판 부재에 역풍 부나",
     "snippet": "주가상승을 자신한 CB 발행이 안전판 부재로 역풍을 맞을 수 있다"},
    {"id": "t_ai_dc", "source": "전자신문", "kind": "ai",
     "title": "특별법 시행 앞둔 AI 데이터센터…건설사, 전력망·냉각 솔루션 경쟁",
     "snippet": "AI 데이터센터 특별법 시행을 앞두고 건설사들이 전력망 냉각 솔루션 경쟁을 벌인다"},
    {"id": "t_smr", "source": "전자신문", "kind": "ai",
     "title": "AI 전력난에 SMR 발주전 본격화",
     "snippet": "AI 전력난에 SMR 발주전이 본격화된다"},
    {"id": "t_hdec_genai", "source": "데일리안", "kind": "ai",
     "title": "현대건설, 생성형 AI 서비스로 입주민 지원 고도화",
     "snippet": "현대건설이 생성형 AI 서비스로 입주민 지원을 고도화한다"},
    {"id": "t_sports", "source": "스포츠서울", "kind": "not_hdec_exec",
     "title": "'원더독스 5명' 수원시청, 프로팀 연파하고 우승",
     "snippet": "원더독스 5명이 활약한 수원시청이 프로팀을 연파하고 우승했다"},
    {"id": "t_hmotor", "source": "한국경제", "kind": "not_hdec_direct",
     "title": "시골 동네라던 위례 복정의 반전, 8조 규모 현대차 사옥 실착공",
     "snippet": "위례 복정에 8조 규모 현대차 사옥이 실착공에 들어갔다"},
    {"id": "t_hillstate", "source": "서울경제", "kind": "hdec_promo",
     "title": "현대건설, 양산 첫 힐스테이트 598가구 견본주택 개관",
     "snippet": "현대건설이 양산 첫 힐스테이트 598가구 견본주택을 개관했다"},
    {"id": "t_kospi", "source": "인포스탁", "kind": "not_hdec_exec",
     "title": "[코스피 지수선물 옵션] 외국인·기관 폭풍 매수, 베이시스 흐름 주목",
     "snippet": "코스피 지수선물 옵션 시장에서 외국인·기관이 폭풍 매수에 나섰다"},
    # 예외 — 데이터센터 사업 전략 CB는 raw에 직접 AI 증거가 있어 AI/전략 유지.
    {"id": "t_dccb", "source": "이데일리", "kind": "ai",
     "title": "현대건설 데이터센터 사업 투자 위해 3000억 회사채 발행",
     "snippet": "현대건설이 데이터센터 사업 투자를 위해 3000억 회사채를 발행한다"},
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
    rd = RADAR.read_text(encoding="utf-8")
    check("radar가 raw 제목+스니펫 기준 분류 헬퍼(_raw_text) 사용",
          "_raw_text" in rd and "classify_section" in rd)
    check("radar가 명시적 AI 오탐 가드(AI_NEGATIVE_GUARD) 정의",
          "AI_NEGATIVE_GUARD" in rd
          and all(tok in rd for tok in ("전환사채", "종전", "건설주")))
    dr = DR_MODULE.read_text(encoding="utf-8")
    check("decision_relevance가 분양/스포츠 강등 가드(_hdec_demoted) 정의",
          "_hdec_demoted" in dr and "HDEC_SALES_PROMO" in dr
          and "HDEC_STRATEGIC_ANCHOR" in dr)
    check("override_radar_section이 재무 AI를 other로 내림 (거시 비혼입)",
          "base_section == radar.AI and d.get(\"is_finance\")" in dr
          and "return radar.OTHER" in dr)
    check("decision_relevance 재무 토큰에 자본시장(cb/주가상승/코스피) 포함",
          all(t in dr for t in ("cb 발행", "주가상승", "코스피")))


# ---------- 순수 함수 단위 검사 (in-process, DB 미접촉) ----------

def _import_app():
    sys.path.insert(0, str(ROOT))
    os.environ.setdefault("APP_MODE", "mock")
    from app import article_quality, decision_relevance, radar
    return article_quality, radar, decision_relevance


def _route(radar, dr, f, topics="[]"):
    """fixture 1건의 표시용 radar_section (override 포함) + decision 뷰를 돌려준다."""
    row = {"title": f["title"], "snippet": f["snippet"], "source": f["source"],
           "topic_candidates": topics}
    d = dr.classify(row, "general")
    section = dr.override_radar_section(radar.classify_section(row, "general"), d)
    return section, d


def check_units() -> None:
    aq, radar, dr = _import_app()
    by = {f["id"]: _route(radar, dr, f) for f in FIX}

    def sec(fid):
        return by[fid][0]

    def dec(fid):
        return by[fid][1]

    # ---- A. AI 오탐 ----
    for fid in ("t_jackpot", "t_iran_hdec", "t_constock"):
        check(f"A: {fid} → AI 섹션 아님", sec(fid) != radar.AI, sec(fid))
    for fid in ("t_jackpot", "t_constock"):
        check(f"A: {fid} → stock-hype 판정(어떤 레이더에도 없음, other)",
              aq.assess(by_id(fid)["source"], by_id(fid)["title"])["stock_hype"]
              and sec(fid) == radar.OTHER, sec(fid))
    for fid in ("t_ai_dc", "t_smr", "t_hdec_genai", "t_dccb"):
        check(f"A: {fid} → AI 섹션 유지 (raw에 직접 AI 증거)",
              sec(fid) == radar.AI, sec(fid))

    # ---- B. 거시 오탐 / 재무 라우팅 ----
    for fid in ("t_hdec_cb", "t_cb"):
        check(f"B: {fid} → 거시경제 primary 아님 (재무 거시 비혼입)",
              sec(fid) != radar.MACRO, sec(fid))
        check(f"B: {fid} → AI 섹션 아님", sec(fid) != radar.AI, sec(fid))
        check(f"B: {fid} → is_finance True (자본시장·재무 인식)",
              dec(fid)["is_finance"] is True)
    check("B: 현대건설 전환사채 → 현대건설 직접 primary + 거시 secondary",
          dec("t_hdec_cb")["primary_executive_section"] == dr.HDEC_DIRECT
          and dr.MACRO in dec("t_hdec_cb")["secondary_executive_sections"],
          str(dec("t_hdec_cb")["executive_sections"]))
    check("B: 단독 CB(현대건설 아님)는 거시 primary 아님",
          dec("t_cb")["primary_executive_section"] != dr.MACRO,
          dec("t_cb")["primary_executive_section"])
    check("B: 데이터센터 사업 CB(t_dccb)는 is_finance False (전략 맥락 → AI 유지)",
          dec("t_dccb")["is_finance"] is False
          and dr.AI in dec("t_dccb")["executive_sections"],
          str(dec("t_dccb")["executive_sections"]))

    # ---- C. 현대건설 직접 언급 vs 임원 전략 신호 분리 ----
    check("C: 현대차 사옥(현대차≠현대건설)은 현대건설 직접 아님",
          not dr.is_hdec_direct(by_id("t_hmotor")["title"])
          and dec("t_hmotor")["primary_executive_section"] != dr.HDEC_DIRECT)
    check("C: 원더독스 스포츠 기사 → 현대건설 임원 신호 아님",
          not dr.is_hdec_strategic(by_id("t_sports")["title"])
          and dec("t_sports")["primary_executive_section"] != dr.HDEC_DIRECT)
    check("C: 코스피 지수선물 → 현대건설 임원 신호 아님",
          not dr.is_hdec_direct(by_id("t_kospi")["title"])
          and dec("t_kospi")["primary_executive_section"] != dr.HDEC_DIRECT)
    # 분양 PR — 직접 언급(is_hdec_direct)은 유지하되 전략/primary는 아니다.
    check("C: 힐스테이트 견본주택 PR → 현대건설 직접 언급은 유지",
          dr.is_hdec_direct(by_id("t_hillstate")["title"]))
    check("C: 힐스테이트 견본주택 PR → 전략 신호 아님 (등급 floor 대상 아님)",
          not dr.is_hdec_strategic(by_id("t_hillstate")["title"]))
    check("C: 힐스테이트 견본주택 PR → 현대건설 직접 primary 아님 (상단 신호 아님)",
          dec("t_hillstate")["primary_executive_section"] != dr.HDEC_DIRECT,
          dec("t_hillstate")["primary_executive_section"])
    # 강등 예외 — 전략 앵커(도시정비/데이터센터/수주 등)가 있으면 분양이 있어도 전략 유지.
    check("C: '현대건설 도시정비 분양' (전략 앵커 동반)은 전략 신호 유지",
          dr.is_hdec_strategic("현대건설 도시정비 분양 미아 재개발 수주"))


def by_id(fid):
    return next(f for f in FIX if f["id"] == fid)


def check_ai_injection_guard() -> None:
    """collector가 주입한 topic_candidates만으로는 AI가 되지 않음을 직접 검사 (A 핵심)."""
    _, radar, dr = _import_app()
    polluted = '["현대건설 데이터센터", "현대건설 전환사채 자금조달"]'
    fin = by_id("t_hdec_cb")
    row = {"title": fin["title"], "snippet": fin["snippet"], "source": fin["source"],
           "topic_candidates": polluted}
    # _row_text(토픽 포함)에는 '데이터센터'가 있으나, _raw_text(제목+스니펫)에는 없다.
    check("주입 토픽이 _row_text엔 있으나 _raw_text엔 없음 (분류 입력 분리)",
          "데이터센터" in radar._row_text(row)
          and "데이터센터" not in radar._raw_text(row))
    check("주입된 '현대건설 데이터센터' 토픽만으로는 AI 아님 (재무 기사 보호)",
          radar.classify_section(row, "general") != radar.AI)
    d = dr.classify(row, "general")
    check("주입 토픽이 있어도 재무 기사는 AI 멤버 아님",
          dr.AI not in d["executive_sections"], str(d["executive_sections"]))
    # 대조군 — raw에 직접 데이터센터가 있으면 (주입이 아니라) AI 유지.
    raw_dc = {"title": "현대건설 데이터센터 사업 투자 위해 3000억 회사채 발행",
              "snippet": "", "topic_candidates": "[]", "source": "이데일리"}
    check("대조군: raw에 직접 '데이터센터'가 있으면 AI 유지",
          radar.classify_section(raw_dc, "general") == radar.AI)


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
        "def ids(k):return [s.get('article_id') for s in (b.get(k) or [])]\n"
        "out={'mode':b['news_data_mode'],\n"
        " 'ai':ids('ai_radar_signals'),'macro':ids('macro_economy_signals'),\n"
        " 'hdec':ids('hdec_direct_signals'),'biz':ids('business_signals'),\n"
        " 'risk':ids('risk_regulation_signals'),\n"
        " 'top_new':ids('top_new_issues'),'top_imm':ids('top_immediate_signals'),\n"
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
    S = {k: set(sim.get(k) or []) for k in
         ("ai", "macro", "hdec", "biz", "risk", "top_new", "top_imm")}
    grades = sim.get("grades") or {}

    # A. AI 오탐 — 종전/테마 기사가 AI/상단 어디에도 없음.
    for fid in ("t_jackpot", "t_constock"):
        where = [k for k in S if fid in S[k]]
        check(f"A: {fid}가 AI/상단 섹션 어디에도 없음", not where, f"등장: {where}")
        check(f"A: {fid} 등급=제외 (stock-hype)",
              grades.get(fid) == GRADE_EXCLUDED, str(grades.get(fid)))
    check("A: t_iran_hdec(종전·현대건설·제재)가 AI 섹션에 없음", "t_iran_hdec" not in S["ai"])
    # AI 유지 — 직접 AI 증거가 있는 기사.
    for fid in ("t_ai_dc", "t_smr"):
        check(f"A: {fid}가 AI/상단 executive surface 유지",
              fid in (S["ai"] | S["top_imm"] | S["top_new"]),
              f"ai={sorted(S['ai'])} top_imm={sorted(S['top_imm'])} top_new={sorted(S['top_new'])}")
    check("A: 현대건설 생성형 AI가 AI 또는 현대건설 직접에 노출",
          "t_hdec_genai" in S["ai"] or "t_hdec_genai" in S["hdec"])

    # B. 거시 오탐 — 재무 기사가 거시경제·AI 섹션에 없음.
    for fid in ("t_hdec_cb", "t_cb"):
        check(f"B: {fid}가 거시경제 섹션에 없음 (재무 거시 비혼입)",
              fid not in S["macro"], f"macro={sorted(S['macro'])}")
        check(f"B: {fid}가 AI 섹션에 없음 (주입 토픽 우회 차단 포함)",
              fid not in S["ai"], f"ai={sorted(S['ai'])}")
    check("B: 현대건설 전환사채가 현대건설 직접에 노출", "t_hdec_cb" in S["hdec"],
          f"hdec={sorted(S['hdec'])}")
    check("B: 현대건설 전환사채 제외 아님 (재무 floor)",
          grades.get("t_hdec_cb") != GRADE_EXCLUDED, str(grades.get("t_hdec_cb")))

    # C. 현대건설 오탐 — PR/스포츠/현대차/코스피가 현대건설 직접 섹션에 없음.
    for fid in ("t_hillstate", "t_sports", "t_hmotor", "t_kospi"):
        check(f"C: {fid}가 현대건설 직접 섹션에 없음", fid not in S["hdec"],
              f"hdec={sorted(S['hdec'])}")


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
    check("mock 데모 숫자 불변 (분류 가드가 mock을 바꾸지 않음)",
          got == MOCK_BASELINE, f"got={got} expect={MOCK_BASELINE}")
    mock_hdec_visible = bool(b.get("hdec_direct_signals")) or any(
        e.get("executive_section") == "hdec_direct"
        for e in (b.get("top_immediate_signals") or []))
    check("mock 현대건설 직접 신호 유지 (네옴 EPC)",
          mock_hdec_visible,
          f"hdec={len(b.get('hdec_direct_signals') or [])} top_imm="
          f"{[(e.get('article_id'), e.get('executive_section')) for e in (b.get('top_immediate_signals') or [])]}")
    check("mock AI 신호 유지", bool(b.get("ai_radar_signals")),
          f"{len(b.get('ai_radar_signals') or [])}건")


def main() -> int:
    print(f"== verify_executive_topic_classification @ {ROOT} ==")
    os.environ["DB_PATH"] = os.path.join(
        tempfile.mkdtemp(prefix="hdec_tcv_"), "verify.db")
    db_before = _db_state()

    check_py_compile()
    check_integration()
    check_units()
    check_ai_injection_guard()

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
