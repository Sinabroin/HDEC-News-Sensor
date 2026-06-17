"""P0-C1.12 검증기 — 임원 의사결정 관련성 재정렬 회귀 검사 (결정적·네트워크 없음).

목적: 제품 목표를 'AI 뉴스 수집'에서 '현대건설 임원 의사결정 레이더'로 옮긴 변경을 보장한다.
- 현대건설 직접 영향 섹션이 생기고, AI보다 먼저 노출된다.
- 수주·해외가 발주 환경(중동·재건·EPC·DC·SMR)·경쟁사 수주 전략까지 넓어진다 (0건 회귀 방지).
- 같은 기사가 여러 임원 섹션에 멤버로 들어갈 수 있다 (primary + secondary).
- 의사결정 관련성 티어로 상단 항목을 고른다 (AI 관련성만이 아니라 임원 유용성).
- stock-hype/증권 리서치성 제외(P0-C1.11)와 리스크·규제 품질 게이트는 그대로 유지된다.
- Telegram Top이 같은 회사/공급사로 도배되지 않는다.
- mock 데모 숫자는 그대로 유지된다.

핵심 원칙:
- 분류 로직은 순수 함수(app/decision_relevance.py, app/article_quality.py, app/radar.py)가
  단일 소유한다 — fixture로 결정적으로 검사한다.
- 라이브 인터넷 결과에 의존하지 않는다 — fetch_all을 fixture로 패치해 파이프라인을
  temp DB subprocess에서 돌린다. 저장소의 radar.db는 절대 건드리지 않는다.
- 생성된 사유/카테고리 라벨('현대건설 직접 연관성 낮음' 등)을 분류 입력으로 쓰지 않는다.

사용법:
    python3 scripts/verify_executive_decision_relevance.py
"""

import json
import os
import py_compile
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DR_MODULE = ROOT / "app" / "decision_relevance.py"
BRIEFING = ROOT / "app" / "briefing.py"
SCORING = ROOT / "app" / "scoring.py"
REPORT_BUILDER = ROOT / "scripts" / "build_static_report.py"
DIGEST_BUILDER = ROOT / "scripts" / "build_telegram_digest.py"
BRIEF_BUILDER = ROOT / "scripts" / "build_executive_brief.py"
AUDIT_HELPER = ROOT / "scripts" / "audit_live_article_quality.py"
TEMPLATE = ROOT / "templates" / "index.html"
RADAR_DB = ROOT / "radar.db"

GRADE_EXCLUDED = "제외"
MOCK_BASELINE = {"total_articles": 28, "total_signals": 21, "immediate_count": 3,
                 "daily_count": 4, "weekly_count": 14, "excluded_count": 7}

# ---- fixture: 미션 회귀 시나리오 (제목·출처만으로 결정적 판정) ----
FIX = [
    {"id": "f_dc", "source": "한국경제",
     "title": "현대건설 도시정비 12조 데이터센터 양 축 강화",
     "snippet": "현대건설이 도시정비와 데이터센터를 두 축으로 포트폴리오를 강화한다"},
    {"id": "f_aicon", "source": "데일리안",
     "title": "현대건설 AI로 하도급 계약 점검 1660억원 상생펀드 운영",
     "snippet": "현대건설이 AI로 협력사 하도급 계약을 점검하고 상생펀드를 운영한다"},
    {"id": "f_pen", "source": "서울신문",
     "title": "서울시 현대건설에 벌점 사전통보 분양 수주 영향권",
     "snippet": "서울시가 현대건설에 벌점을 사전통보하면서 분양 수주 영향이 우려된다"},
    {"id": "f_rnd", "source": "연합뉴스",
     "title": "현대건설 현대ENG R&D조직 일원화 에너지전환 스마트건설 대응",
     "snippet": "현대건설과 현대ENG가 R&D 조직을 일원화해 에너지전환과 스마트건설에 대응한다"},
    {"id": "f_smr", "source": "매일경제",
     "title": "아파트부터 SMR까지 뉴에너지 인프라 판 키우는 현대건설",
     "snippet": "현대건설이 아파트부터 SMR까지 뉴에너지 인프라 사업을 키운다"},
    {"id": "f_sams", "source": "한국경제",
     "title": "삼성물산 올해 EPC 수주 목표 10.1조 SMR 데이터센터 정조준",
     "snippet": "삼성물산이 EPC 수주 목표를 높이고 SMR 데이터센터를 정조준한다"},
    {"id": "f_mideast", "source": "서울경제",
     "title": "종전에 중동 재건 기대 건설사 수주 채비 신중론 병존",
     "snippet": "중동 재건 기대에 건설사들이 수주를 준비하나 신중론도 병존한다"},
    {"id": "f_sk", "source": "이데일리",
     "title": "SK에코플랜트 AI EPC로 체질 전환 데이터센터 수주 추진",
     "snippet": "SK에코플랜트가 AI EPC로 체질을 전환하고 데이터센터 수주를 추진한다"},
    {"id": "f_gaon1", "source": "전기신문",
     "title": "가온전선 데이터센터 전력 케이블 공급 확대",
     "snippet": "가온전선이 데이터센터 전력 케이블 공급을 확대한다"},
    {"id": "f_gaon2", "source": "전기신문",
     "title": "가온전선 전력망 버스덕트 수주 급증",
     "snippet": "가온전선 전력망 버스덕트 수주가 급증했다"},
    {"id": "f_money", "source": "데일리머니",
     "title": "스페이스X 상장 쇼크의 대안 SMR 파운드리 거물 두산에너빌리티로 머니무브 터졌다",
     "snippet": "두산에너빌리티 SMR 파운드리 머니무브 테마주 급등"},
    {"id": "f_iljin", "source": "리서치알음",
     "title": "일진파워 전력 인프라 투자 사이클 수혜 원전 연료전지 성장 기대",
     "snippet": "일진파워 수혜 성장 기대 목표가"},
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
    check("app/decision_relevance.py 존재 (순수 leaf 도메인)", DR_MODULE.exists())
    dr = DR_MODULE.read_text(encoding="utf-8")
    check("decision_relevance가 article_quality/radar 재사용",
          "article_quality" in dr and "radar" in dr)
    check("decision_relevance가 임원 섹션 상수 정의",
          all(k in dr for k in ("HDEC_DIRECT", "ORDER_OVERSEAS", "COMPETITOR")))
    # 생성 사유 텍스트를 분류 입력으로 쓰지 않는다 (raw 제목/출처만).
    check("decision_relevance가 생성 라벨/사유를 분류 입력으로 쓰지 않음 (주석 명시)",
          "생성된 사유" in dr or "self-fulfilling" in dr)

    bf = BRIEFING.read_text(encoding="utf-8")
    check("briefing이 hdec_direct_signals/competitor_supply_signals 구성",
          "hdec_direct_signals" in bf and "competitor_supply_signals" in bf)
    check("briefing이 decision_relevance import + 멤버십 사용",
          "decision_relevance" in bf and "in_section" in bf)
    sc = SCORING.read_text(encoding="utf-8")
    check("scoring이 현대건설 전략/수주·해외 등급 floor 적용",
          "is_hdec_strategic" in sc and "is_order_environment" in sc)

    rep = REPORT_BUILDER.read_text(encoding="utf-8")
    check("리포트가 현대건설 연관/경쟁사·공급망 섹션 렌더",
          "hdec-radar" in rep and "comp-radar" in rep
          and "현대건설 연관" in rep)
    tpl = TEMPLATE.read_text(encoding="utf-8")
    check("대시보드가 현대건설/경쟁사·공급망 탭 + 동적 기본 탭",
          "hdec_direct_signals" in tpl and "competitor_supply_signals" in tpl
          and '"hdec"' in tpl)
    dg = DIGEST_BUILDER.read_text(encoding="utf-8")
    check("Telegram이 현대건설 연관 라인 + 회사 dedup",
          "현대건설 연관" in dg and "_entity_key" in dg)


# ---------- 순수 함수 단위 검사 (in-process, DB 미접촉) ----------

def _import_app():
    sys.path.insert(0, str(ROOT))
    os.environ.setdefault("APP_MODE", "mock")
    from app import decision_relevance
    return decision_relevance


def check_decision_unit() -> None:
    dr = _import_app()
    by = {f["id"]: dr.classify(
        {"title": f["title"], "source": f["source"], "snippet": f["snippet"],
         "topic_candidates": "[]"}) for f in FIX}

    def secs(fid):
        return by[fid]["executive_sections"]

    # 현대건설 직접 — 전략/계약/조직/뉴에너지가 현대건설 직접 영향에 들어간다.
    for fid, label in (("f_dc", "데이터센터 전략"), ("f_aicon", "AI 계약"),
                       ("f_rnd", "R&D 일원화"), ("f_smr", "뉴에너지 SMR")):
        check(f"현대건설 {label} → 현대건설 직접 영향 멤버 + 제외 아님",
              dr.HDEC_DIRECT in secs(fid)
              and by[fid]["decision_relevance_tier"] != dr.TIER_EXCLUDE,
              f"{secs(fid)} / {by[fid]['decision_relevance_tier']}")
    # 현대건설 벌점 — 리스크·규제(primary) + 현대건설 직접(secondary) 둘 다 노출.
    check("현대건설 벌점 → 리스크·규제 + 현대건설 직접 둘 다 멤버",
          dr.RISK in secs("f_pen") and dr.HDEC_DIRECT in secs("f_pen"),
          str(secs("f_pen")))
    check("현대건설 벌점 primary == 리스크·규제 (임원이 반드시 봄)",
          by["f_pen"]["primary_executive_section"] == dr.RISK)
    # 삼성물산 EPC/SMR/DC — AI + 수주·해외 둘 다 후보 (multi-section).
    check("삼성물산 EPC/SMR/DC → AI + 수주·해외 멤버",
          dr.AI in secs("f_sams") and dr.ORDER_OVERSEAS in secs("f_sams"),
          str(secs("f_sams")))
    # 중동 재건 — 수주·해외 후보로 surface (silently buried 아님).
    check("중동 재건 건설사 수주 채비 → 수주·해외 멤버",
          dr.ORDER_OVERSEAS in secs("f_mideast"), str(secs("f_mideast")))
    # SK에코플랜트 AI EPC — 경쟁사/수주 후보, stock-hype 제외 아님.
    check("SK에코플랜트 AI EPC → 경쟁사·공급망 또는 수주·해외 (제외 아님)",
          (dr.COMPETITOR in secs("f_sk") or dr.ORDER_OVERSEAS in secs("f_sk"))
          and by["f_sk"]["decision_relevance_tier"] != dr.TIER_EXCLUDE,
          str(secs("f_sk")))
    # stock-hype — exclude 티어 + other (어떤 임원 섹션에도 없음).
    for fid in ("f_money", "f_iljin"):
        check(f"stock-hype({fid}) → exclude 티어 + other",
              by[fid]["decision_relevance_tier"] == dr.TIER_EXCLUDE
              and by[fid]["primary_executive_section"] == dr.OTHER,
              str(by[fid]["primary_executive_section"]))


def check_telegram_dedup_unit() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    import build_telegram_digest as d
    g1 = {"title": FIX[8]["title"], "article_id": "f_gaon1", "topic": "전력 케이블"}
    g2 = {"title": FIX[9]["title"], "article_id": "f_gaon2", "topic": "버스덕트"}
    check("같은 회사(가온전선) 2건은 같은 dedup 키",
          d._entity_key(g1) == d._entity_key(g2),
          f"{d._entity_key(g1)} vs {d._entity_key(g2)}")
    picked = d._pick_diverse([g1, g2], 3, set())
    check("Telegram Top에서 가온전선 2건이 1건으로 축소 (회사 도배 방지)",
          len(picked) == 1, f"{len(picked)}건")
    # live 헤더에서 '자동 수집' 기술 표현 제거 (mock 빌더로는 안 잡혀 직접 단위 검사).
    base = {"header": "HDEC Executive Radar", "date_kst": "2026-06-16",
            "executive_one_liner": "검증용 문장", "status_board": [],
            "hdec_signals": [], "top_signals": [], "biz_signals": [],
            "risk_signals": [], "theme_rankings": [], "category_counts": [],
            "macro_snapshot": {}, "mode": "mock"}
    live_msg = d.format_digest_message({**base, "news_data_mode": "live"})
    mock_msg = d.format_digest_message({**base, "news_data_mode": "mock"})
    check("live digest 헤더에 '자동 수집' 기술 표현 없음", "자동 수집" not in live_msg)
    check("mock digest 헤더는 'mock 데이터 기반' 정직 표기 유지",
          "mock 데이터 기반" in mock_msg)


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
        " 'hdec':ids('hdec_direct_signals'),'ai':ids('ai_radar_signals'),\n"
        " 'risk':ids('risk_regulation_signals'),'biz':ids('business_signals'),\n"
        " 'comp':ids('competitor_supply_signals'),'macro':ids('macro_economy_signals'),\n"
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
    core = ("hdec", "ai", "risk", "biz", "comp", "macro")
    S = {k: set(sim.get(k) or []) for k in core}
    grades = sim.get("grades") or {}

    # 현대건설 직접 — 전략/계약/조직/뉴에너지/벌점이 현대건설 직접 섹션에 노출 + 제외 아님.
    for fid, label in (("f_dc", "데이터센터"), ("f_aicon", "AI 계약"),
                       ("f_rnd", "R&D"), ("f_smr", "뉴에너지"), ("f_pen", "벌점")):
        check(f"현대건설 {label}({fid})가 현대건설 직접 섹션에 노출", fid in S["hdec"],
              f"hdec={sorted(S['hdec'])}")
        check(f"현대건설 {label}({fid}) 제외 아님",
              grades.get(fid) != GRADE_EXCLUDED, str(grades.get(fid)))
    # 현대건설 벌점은 리스크·규제에도 노출 (multi-section).
    check("현대건설 벌점이 리스크·규제 섹션에도 노출", "f_pen" in S["risk"])
    # 수주·해외 broadening — 삼성물산/중동 재건/SK에코플랜트가 수주·해외에 노출.
    check("삼성물산 EPC/SMR이 AI 또는 수주·해외에 노출",
          "f_sams" in S["ai"] or "f_sams" in S["biz"])
    check("중동 재건 기사가 수주·해외에 노출 (0건 회귀 방지)", "f_mideast" in S["biz"],
          f"biz={sorted(S['biz'])}")
    check("중동 재건 기사 제외 아님 (발주 환경 floor)",
          grades.get("f_mideast") != GRADE_EXCLUDED, str(grades.get("f_mideast")))
    check("SK에코플랜트가 수주·해외 또는 경쟁사·공급망에 노출",
          "f_sk" in S["biz"] or "f_sk" in S["comp"])
    # stock-hype — 제외 + 어떤 임원 섹션에도 없음 (P0-C1.11 유지).
    for fid in ("f_money", "f_iljin"):
        where = [k for k in core if fid in S[k]]
        check(f"stock-hype({fid})가 어떤 임원 섹션에도 없음", not where, f"등장: {where}")
        check(f"stock-hype({fid}) 등급=제외",
              grades.get(fid) == GRADE_EXCLUDED, str(grades.get(fid)))


# ---------- mock 무결성 + 리포트/Telegram/감사 구조 ----------

def check_mock_integrity() -> None:
    proc = subprocess.run([sys.executable, str(BRIEF_BUILDER), "--json"],
                          capture_output=True, text=True,
                          env=_clean_env(), cwd=ROOT, timeout=300)
    if not check("mock brief --json 동작", proc.returncode == 0,
                 (proc.stderr or "").strip()[-200:]):
        return
    b = json.loads(proc.stdout)
    got = {k: b.get(k) for k in MOCK_BASELINE}
    check("mock 데모 숫자 불변 (의사결정 재정렬이 mock을 바꾸지 않음)",
          got == MOCK_BASELINE, f"got={got}")
    check("mock에 현대건설 직접 신호 존재 (네옴 EPC)",
          bool(b.get("hdec_direct_signals")),
          f"{len(b.get('hdec_direct_signals') or [])}건")


def check_report_structure() -> None:
    with tempfile.TemporaryDirectory(prefix="hdec_dr_") as tmp:
        out = Path(tmp) / "r.html"
        proc = subprocess.run([sys.executable, str(REPORT_BUILDER), "--output", str(out)],
                              capture_output=True, text=True,
                              env=_clean_env(), cwd=ROOT, timeout=240)
        if not check("리포트 빌드 동작", proc.returncode == 0 and out.exists(),
                     (proc.stderr or "").strip()[-200:]):
            return
        html = out.read_text(encoding="utf-8")

    def idx(m):
        return html.index(m) if m in html else -1
    check("리포트에 '현대건설 연관' 섹션", "현대건설 연관" in html)
    check("리포트에 '수주·해외·발주 환경' (broadened) 섹션", "수주·해외·발주 환경" in html)
    check("현대건설 연관 섹션이 generic AI 섹션보다 먼저",
          0 <= idx('id="hdec-radar"') < idx('id="ai-radar"'))
    # 거시경제는 탭 패널로 유지 — 기본 선택이 아니면 CSS로 숨김.
    import re
    macro_panel = re.search(r'<section id="macro"[^>]*class="[^"]*radar-panel', html)
    check("거시경제 탭 패널 유지", bool(macro_panel))
    check("거시경제 탭이 기본 선택 아님", 'id="radar-tab-macro" checked' not in html)


def check_telegram_structure() -> None:
    proc = subprocess.run([sys.executable, str(DIGEST_BUILDER), "--dry-run"],
                          capture_output=True, text=True,
                          env=_clean_env(), cwd=ROOT, timeout=120)
    if not check("digest 빌드 동작", proc.returncode == 0,
                 (proc.stderr or "").strip()[-200:]):
        return
    msg = proc.stdout or ""
    check("digest에 [현대건설 연관] 라인 (mock 네옴 EPC)", "[현대건설 연관]" in msg)
    check("digest에 '뉴스 자동 수집' 표기 없음 (기술 용어 제거)", "자동 수집" not in msg)
    check("digest에 Macro Snapshot 미노출 (mock — 거시 리포트 위임)",
          "Macro Snapshot" not in msg and "시장지표 미연동" not in msg)
    # HDEC 직접 라인이 AI 관련보다 먼저
    h, a = msg.find("[현대건설 연관]"), msg.find("[AI 관련")
    check("digest: 현대건설 연관이 AI 관련보다 먼저", h >= 0 and a >= 0 and h < a)


def check_audit_helper() -> None:
    src = AUDIT_HELPER.read_text(encoding="utf-8")
    check("감사 헬퍼가 현대건설 직접/수주·해외·발주 환경 후보 섹션 포함",
          "현대건설 직접" in src and "발주 환경" in src)
    check("감사 헬퍼가 '상단 표시 후보'로 리네임 (즉시 알림 후보 오라벨 제거)",
          "상단 표시 후보" in src)
    check("감사 헬퍼가 '의사결정 관련 높은' 제외 후보 점검 포함",
          "의사결정" in src)


def main() -> int:
    print(f"== verify_executive_decision_relevance @ {ROOT} ==")
    os.environ["DB_PATH"] = os.path.join(
        tempfile.mkdtemp(prefix="hdec_drv_"), "verify.db")
    db_before = _db_state()

    check_py_compile()
    check_integration()
    check_decision_unit()
    check_telegram_dedup_unit()

    sim = _run_pipeline_sim()
    check_pipeline(sim)

    check_mock_integrity()
    check_report_structure()
    check_telegram_structure()
    check_audit_helper()

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
