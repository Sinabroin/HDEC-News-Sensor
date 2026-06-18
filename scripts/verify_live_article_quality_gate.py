"""P0-C1.11 검증기 — 라이브 기사 품질 게이트 회귀 검사 (결정적·네트워크 없음).

목적: AI 편중 쿼리 이후 임원용 기사 품질을 보장한다.
- 주식 테마/급등/증권 리서치성(stock-hype) 기사를 임원 핵심 섹션에서 강등한다
  (AI 관련 Top / 신규 이슈 / 즉시 후보 / 수주·해외 / 리스크·규제 어디에도 없음).
- 리스크/규제 분류를 조인다 — '국토부/혁신기술'만으로 리스크가 되지 않는다.
- 현대건설 직접 AI-계약/제재 기사를 제외에서 끌어올린다(최소 '추적 필요').
- 집계 호스트(v.daum.net 등) 출처 표시를 정규화한다('Daum 경유').
- mock 데모 숫자는 그대로 유지된다.

핵심 원칙:
- 분류/게이트 로직은 순수 함수(app/article_quality.py, app/radar.py,
  app/source_quality.py)가 단일 소유한다 — fixture로 결정적으로 검사한다.
- 라이브 인터넷 결과에 의존하지 않는다 — fetch_all을 fixture로 패치해 파이프라인을
  temp DB subprocess에서 돌린다. 저장소의 radar.db는 절대 건드리지 않는다.
- 본문 전문은 저장하지 않는다 (기존 금지어 계약 유지).

사용법:
    python3 scripts/verify_live_article_quality_gate.py
"""

import json
import os
import py_compile
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AQ_RULES = ROOT / "data" / "article_quality_rules.json"
SQ_RULES = ROOT / "data" / "source_quality_rules.json"
AQ_MODULE = ROOT / "app" / "article_quality.py"
SCORING = ROOT / "app" / "scoring.py"
RADAR = ROOT / "app" / "radar.py"
BRIEFING = ROOT / "app" / "briefing.py"
MAIN = ROOT / "app" / "main.py"
REPORT_BUILDER = ROOT / "scripts" / "build_static_report.py"
BRIEF_BUILDER = ROOT / "scripts" / "build_executive_brief.py"
TEMPLATE = ROOT / "templates" / "index.html"
RADAR_DB = ROOT / "radar.db"

DAILY_THRESHOLD = 3.5
GRADE_EXCLUDED = "제외"
GRADE_WEEKLY = "추적 필요"

# mock 데모 기준선 — 이 값들이 바뀌면 품질 게이트가 mock을 오염시킨 것 (의도된 변경이면 갱신).
MOCK_BASELINE = {"total_articles": 28, "total_signals": 21, "immediate_count": 3,
                 "daily_count": 4, "weekly_count": 14, "excluded_count": 7}

# ---- fixture: 미션 회귀 시나리오 (제목·출처만으로 결정적 판정) ----
FIX = [
    {"id": "f_smr", "source": "데일리머니",
     "title": "스페이스X 상장 쇼크의 대안 SMR 파운드리 거물 두산에너빌리티로 머니무브 터졌다",
     "snippet": "두산에너빌리티 SMR 파운드리 머니무브 테마주 급등",
     "published_at": "2026-06-14T09:00:00+09:00", "url": "https://ex.test/smr"},
    {"id": "f_iljin", "source": "리서치알음",
     "title": "일진파워 전력 인프라 투자 사이클 수혜 원전 연료전지 성장 기대",
     "snippet": "일진파워 수혜 성장 기대 목표가",
     "published_at": "2026-06-14T09:00:00+09:00", "url": "https://ex.test/iljin"},
    {"id": "f_gov", "source": "연합뉴스",
     "title": "국토부 건설산업 대전환 이끌 혁신기술 발굴한다",
     "snippet": "국토교통부가 스마트건설 혁신기술 공모 지원사업으로 건설산업 대전환을 추진한다",
     "published_at": "2026-06-14T09:00:00+09:00", "url": "https://ex.test/gov"},
    {"id": "f_event", "source": "인공지능신문",
     "title": "인공지능 로봇으로 안전한 건설현장 만든다 스마트건설 챌린지 개최",
     "snippet": "스마트건설 챌린지 행사가 개최되고 총 3억원 상금이 수여된다",
     "published_at": "2026-06-14T09:00:00+09:00", "url": "https://ex.test/event"},
    {"id": "f_local_safety", "source": "시사저널",
     "title": "대전시 건설관리본부 폭염 집중호우 대비 안전점검",
     "snippet": "지역 건설관리본부가 폭염과 집중호우에 대비해 안전점검을 실시한다",
     "published_at": "2026-06-14T09:00:00+09:00", "url": "https://ex.test/local"},
    {"id": "f_hdec_ai", "source": "데일리안",
     "title": "현대건설 AI로 하도급 계약 점검 1660억원 상생펀드 운영",
     "snippet": "현대건설이 AI로 협력사 하도급 계약을 점검하고 동반성장펀드를 운영한다",
     "published_at": "2026-06-14T09:00:00+09:00", "url": "https://ex.test/hdecai"},
    {"id": "f_hdec_enf", "source": "서울신문",
     "title": "서울시 현대건설에 벌점 사전통보 분양 수주 영향권",
     "snippet": "서울시가 현대건설에 벌점을 사전통보하면서 분양 수주 영향이 우려된다",
     "published_at": "2026-06-14T09:00:00+09:00", "url": "https://ex.test/hdecenf"},
    {"id": "f_ai_dc", "source": "전자신문",
     "title": "AI 데이터센터 전력망 냉각 수요 급증에 전력 인프라 건설 투자 확대",
     "snippet": "AI 데이터센터 전력 냉각 송배전 건설 EPC 발주가 확대된다",
     "published_at": "2026-06-14T09:00:00+09:00", "url": "https://ex.test/aidc"},
    {"id": "f_samsung", "source": "한국경제",
     "title": "삼성물산 SMR 데이터센터 EPC 수주 추진 스마트건설 확대",
     "snippet": "삼성물산이 데이터센터 EPC와 원전 SMR 수주를 추진하며 스마트건설을 확대한다",
     "published_at": "2026-06-14T09:00:00+09:00", "url": "https://ex.test/samsung"},
    {"id": "f_old_hdec", "source": "한국경제",
     "title": "현대건설 도시정비 12조 데이터센터 양 축 강화",
     "snippet": "현대건설이 도시정비와 데이터센터를 두 축으로 포트폴리오를 강화한다",
     "published_at": "2026-04-03T09:00:00+09:00", "url": "https://ex.test/oldhdec"},
    {"id": "f_agg", "source": "v.daum.net",
     "title": "AI 데이터센터 전력 인프라 건설 발주 확대 전망",
     "snippet": "데이터센터 전력 건설 EPC 발주가 늘어날 전망이다",
     "published_at": "2026-06-14T09:00:00+09:00", "url": "https://v.daum.net/v/aggx"},
    {"id": "f_broad", "source": "한국경제",
     "title": "삼성물산 해외 플랜트 수주 확대 실적 개선 수혜 기대",
     "snippet": "삼성물산 해외 플랜트 수주가 확대되며 실적 개선이 기대된다",
     "published_at": "2026-06-14T09:00:00+09:00", "url": "https://ex.test/broad"},
]

_failures = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    line = f"[{'PASS' if ok else 'FAIL'}] {name}"
    if detail:
        line += f" — {detail}"
    print(line)
    if not ok:
        _failures.append(name)
    return ok


def skip(message: str) -> None:
    print(f"[SKIP] {message}")


def _db_state() -> tuple | None:
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


def check_rules_files() -> None:
    if check("data/article_quality_rules.json 존재", AQ_RULES.exists()):
        try:
            data = json.loads(AQ_RULES.read_text(encoding="utf-8"))
        except ValueError as exc:
            check("article_quality_rules.json 파싱", False, str(exc))
            data = {}
        else:
            check("article_quality_rules.json 파싱", True)
        cap = data.get("stockhype_score_cap")
        check("stockhype_score_cap이 검토 필요 임계(3.5)보다 낮음",
              cap is not None and float(cap) < DAILY_THRESHOLD, str(cap))
        for key in ("stockhype_strong_title_patterns", "equity_research_source_patterns",
                    "hdec_contract_patterns", "hdec_enforcement_patterns"):
            check(f"{key} 1건 이상", bool(data.get(key)),
                  f"{len(data.get(key) or [])}건")
        check("low_actionability_score_cap이 검토 필요 임계(3.5)보다 낮음",
              float(data.get("low_actionability_score_cap") or 9) < DAILY_THRESHOLD,
              str(data.get("low_actionability_score_cap")))
        check("low_actionability/local safety 패턴 존재",
              bool(data.get("low_actionability_title_patterns"))
              and bool(data.get("local_safety_inspection_patterns")))
        # 발주가/수주가(정상 건설 기사)에 substring으로 걸리는 패턴이 있으면 안 된다.
        # bare '주가'는 위험('발주가'에 포함), '주가 급등'/'목표주가'는 안전(부분문자열 아님).
        pats = [str(p).strip() for p in
                (data.get("stockhype_strong_title_patterns") or [])
                + (data.get("stockhype_weak_title_patterns") or [])]
        unsafe = [p for p in pats if p and (p in "발주가" or p in "수주가")]
        check("stock-hype 패턴이 발주가/수주가에 substring 오탐 없음 (bare '주가' 금지)",
              not unsafe, f"위험 패턴: {unsafe}")

    if check("data/source_quality_rules.json에 aggregator_display 존재",
             SQ_RULES.exists()):
        sq = json.loads(SQ_RULES.read_text(encoding="utf-8"))
        agg = sq.get("aggregator_display") or {}
        check("aggregator_display에 v.daum.net/naver/google 매핑",
              "v.daum.net" in agg and any("naver" in k for k in agg)
              and any("google" in k for k in agg), str(list(agg)[:4]))


def check_integration() -> None:
    sc = SCORING.read_text(encoding="utf-8")
    check("scoring이 article_quality import + stock-hype 캡 적용",
          "article_quality" in sc and "STOCKHYPE_SCORE_CAP" in sc and "stock_hype" in sc)
    check("scoring이 현대건설 직접 등급 floor 적용 (hdec_ai_contract/enforcement)",
          "hdec_ai_contract" in sc and "hdec_enforcement" in sc and "_max_grade" in sc)
    rd = RADAR.read_text(encoding="utf-8")
    check("radar가 article_quality로 stock-hype 제외 + hdec 라우팅",
          "article_quality" in rd and "stock_hype" in rd
          and "hdec_ai_contract" in rd and "hdec_enforcement" in rd)
    check("radar 리스크 분류가 risk-action 키워드 요구 (RISK_ACTION_STRONG)",
          "RISK_ACTION_STRONG" in rd and "RISK_REG_WEAK" in rd)
    check("radar가 부처명 단독 리스크 트리거 제거 (국토부 GENERAL_RISK 미사용)",
          "GENERAL_RISK_KEYWORDS" not in rd)
    for label, path in (("briefing", BRIEFING), ("main", MAIN),
                        ("build_static_report", REPORT_BUILDER),
                        ("templates/index.html", TEMPLATE)):
        text = path.read_text(encoding="utf-8")
        check(f"{label}이 display_source 사용 (집계 호스트 표시 정규화)",
              "display_source" in text)


# ---------- 순수 함수 단위 검사 (in-process, DB 미접촉) ----------

def _import_app():
    sys.path.insert(0, str(ROOT))
    os.environ.setdefault("APP_MODE", "mock")
    from app import article_quality, radar, scoring, source_quality
    return article_quality, radar, scoring, source_quality


def check_article_quality_unit() -> None:
    aq, _, _, _ = _import_app()
    by = {f["id"]: aq.assess(f["source"], f["title"]) for f in FIX}
    check("stock-hype: 데일리머니 SMR 머니무브 → stock_hype", by["f_smr"]["stock_hype"])
    check("stock-hype: 리서치알음 일진파워 수혜·성장 기대 → stock_hype",
          by["f_iljin"]["stock_hype"])
    check("현대건설 AI 하도급 → hdec_ai_contract (stock_hype 아님)",
          by["f_hdec_ai"]["hdec_ai_contract"] and not by["f_hdec_ai"]["stock_hype"])
    check("현대건설 벌점 사전통보 → hdec_enforcement",
          by["f_hdec_enf"]["hdec_enforcement"])
    check("국토부 혁신기술 → stock_hype/hdec 아님 (중립)",
          not by["f_gov"]["stock_hype"] and not by["f_gov"]["hdec_ai_contract"]
          and not by["f_gov"]["hdec_enforcement"])
    check("스마트건설 챌린지 행사 → low_actionability",
          by["f_event"]["low_actionability"])
    check("지역 폭염·집중호우 안전점검 → local_safety_inspection",
          by["f_local_safety"]["local_safety_inspection"])
    check("정상 AI 데이터센터 기사 → stock_hype 아님", not by["f_ai_dc"]["stock_hype"])
    check("삼성물산 EPC/SMR 경쟁사 기사 → stock_hype 아님",
          not by["f_samsung"]["stock_hype"])
    # 신뢰 매체의 광범위 산업 기사에 시장 용어 1개(수혜)만 있으면 강등하지 않는다
    check("신뢰 매체 광범위 기사(수혜 1회) → stock_hype 아님 (오강등 방지)",
          not by["f_broad"]["stock_hype"])


def check_radar_unit() -> None:
    _, radar, _, _ = _import_app()

    def sect(f):
        return radar.classify_section(
            {"title": f["title"], "snippet": f["snippet"], "source": f["source"],
             "topic_candidates": "[]"}, "general")

    s = {f["id"]: sect(f) for f in FIX}
    check("radar: stock-hype(SMR/일진파워)는 어떤 레이더에도 없음(other)",
          s["f_smr"] == radar.OTHER and s["f_iljin"] == radar.OTHER,
          f"smr={s['f_smr']} iljin={s['f_iljin']}")
    check("radar: 국토부 혁신기술 ≠ risk_regulation (오탐 차단)",
          s["f_gov"] != radar.RISK, s["f_gov"])
    check("radar: 지역 폭염·집중호우 안전점검 ≠ risk_regulation (배경 처리)",
          s["f_local_safety"] != radar.RISK, s["f_local_safety"])
    check("radar: 현대건설 AI 하도급 → ai", s["f_hdec_ai"] == radar.AI, s["f_hdec_ai"])
    check("radar: 현대건설 벌점 → risk_regulation",
          s["f_hdec_enf"] == radar.RISK, s["f_hdec_enf"])
    check("radar: 정상 AI 데이터센터 → ai", s["f_ai_dc"] == radar.AI, s["f_ai_dc"])
    check("radar: 삼성물산 EPC/SMR → ai 또는 business (경쟁사 맥락 유지)",
          s["f_samsung"] in (radar.AI, radar.BUSINESS), s["f_samsung"])


def check_source_display_unit() -> None:
    _, _, _, sq = _import_app()
    check("display: v.daum.net → Daum 경유",
          sq.normalize_display_source("v.daum.net") == "Daum 경유")
    check("display: n.news.naver.com → Naver 경유",
          sq.normalize_display_source("n.news.naver.com") == "Naver 경유")
    check("display: news.google.com → Google News 경유",
          sq.normalize_display_source("news.google.com") == "Google News 경유")
    check("display: 정상 매체명(연합뉴스)은 그대로",
          sq.normalize_display_source("연합뉴스") == "연합뉴스")


def check_scoring_unit() -> None:
    _, _, scoring, _ = _import_app()
    arts = [{"id": f["id"], "title": f["title"], "snippet": f["snippet"],
             "source": f["source"], "published_at": f["published_at"],
             "normalized_title": f["id"]} for f in FIX]
    ctx = scoring._build_batch_context(arts)
    rows = {r["article_id"]: r for r in (scoring._score_article(a, ctx) for a in arts)}
    for fid in ("f_smr", "f_iljin"):
        r = rows[fid]
        check(f"scoring: {fid} stock-hype 점수 캡(<=2.4)",
              (r["final_score"] or 0) <= 2.4, str(r["final_score"]))
        check(f"scoring: {fid} stock-hype 등급=제외",
              r["alert_grade"] == GRADE_EXCLUDED, r["alert_grade"])
    check("scoring: 현대건설 AI 하도급 ≠ 제외 (최소 추적 필요로 floor)",
          rows["f_hdec_ai"]["alert_grade"] != GRADE_EXCLUDED,
          rows["f_hdec_ai"]["alert_grade"])
    check("scoring: 현대건설 벌점 ≠ 제외 (최소 추적 필요로 floor)",
          rows["f_hdec_enf"]["alert_grade"] != GRADE_EXCLUDED,
          rows["f_hdec_enf"]["alert_grade"])
    for fid, label in (("f_event", "스마트건설 챌린지"),
                       ("f_local_safety", "지역 안전점검")):
        check(f"scoring: {label}은 배경화되어 등급=제외",
              rows[fid]["alert_grade"] == GRADE_EXCLUDED,
              rows[fid]["alert_grade"])
        check(f"scoring: {label} 점수 캡(<=1.4)",
              (rows[fid]["final_score"] or 0) <= 1.4, str(rows[fid]["final_score"]))
    check("scoring: 30일 초과 현대건설 직접 기사도 배경 근거로 캡",
          rows["f_old_hdec"]["alert_grade"] == GRADE_EXCLUDED
          and (rows["f_old_hdec"]["final_score"] or 0) <= 1.0,
          f"{rows['f_old_hdec']['alert_grade']} {rows['f_old_hdec']['final_score']}")


# ---------- 파이프라인 시뮬레이션 (temp DB subprocess, fetch_all 패치) ----------

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
        "def ids(k):return [s.get('article_id') for s in (b.get(k) or [])]\n"
        # 집계 호스트 near-dup(f_agg)은 D3D cluster cap 이후 신뢰 매체 f_ai_dc에 밀려 AI 테마
        # 섹션에서 dedup되고 '신규 이슈'로 노출된다 — display_source 확인은 실제 노출 surface
        # (ai/biz/top_new) 전체에서 찾는다.
        "pools=((b.get('ai_radar_signals') or [])+(b.get('business_signals') or [])\n"
        "       +(b.get('top_new_issues') or []))\n"
        "agg=next((s for s in pools if s.get('article_id')=='f_agg'),{})\n"
        "from build_static_report import render_report_html\n"
        "html,_=render_report_html(b)\n"
        "visible=_re.sub(r'href=\"[^\"]*\"','',html)\n"
        "out={'mode':b['news_data_mode'],\n"
        " 'ai':ids('ai_radar_signals'),'risk':ids('risk_regulation_signals'),\n"
        " 'biz':ids('business_signals'),'macro':ids('macro_economy_signals'),\n"
        " 'top_new':ids('top_new_issues'),'top_imm':ids('top_immediate_signals'),\n"
        " 'review_excluded':[it.get('article_id') for it in (b['review_excluded_evidence'].get('items') or [])],\n"
        " 'grades':{i:rows[i]['alert_grade'] for i in rows},\n"
        " 'scores':{i:rows[i]['final_score'] for i in rows},\n"
        " 'risk_pri':{s.get('article_id'):s.get('risk_priority_score') for s in (b.get('risk_regulation_signals') or [])},\n"
        " 'agg_display':agg.get('display_source'),'agg_source':agg.get('source'),\n"
        " 'report_vdaum_visible':'v.daum.net' in visible,'report_via':'경유' in html}\n"
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
    check("시뮬: live 모드로 fixture 파이프라인 통과", sim.get("mode") == "live",
          str(sim.get("mode")))
    top_sections = ("ai", "risk", "biz", "macro", "top_new", "top_imm")
    allsec = {sec: set(sim.get(sec) or []) for sec in top_sections}

    # (1)(2) stock-hype/equity 강등 — 어떤 핵심 섹션에도 없음 + 제외 등급 + 캡 + 감사 노출
    for fid, label in (("f_smr", "데일리머니 SMR"), ("f_iljin", "리서치알음 일진파워")):
        where = [sec for sec in top_sections if fid in allsec[sec]]
        check(f"stock-hype({label})가 임원 핵심 섹션에 전혀 없음", not where,
              f"등장: {where}")
        check(f"stock-hype({label}) 등급=제외",
              sim["grades"].get(fid) == GRADE_EXCLUDED, str(sim["grades"].get(fid)))
        check(f"stock-hype({label}) 점수 캡(<=2.4)",
              (sim["scores"].get(fid) or 0) <= 2.4, str(sim["scores"].get(fid)))
        check(f"stock-hype({label})는 참고/제외 감사에만 노출",
              fid in (sim.get("review_excluded") or []))

    # (3) risk 오탐 가드 — 국토부 혁신기술은 리스크·규제에 없음
    check("국토부 혁신기술이 리스크·규제 섹션에 없음 (오탐 차단)",
          "f_gov" not in allsec["risk"], str(sim.get("risk")))
    check("지역 폭염·집중호우 안전점검이 리스크·규제 섹션에 없음",
          "f_local_safety" not in allsec["risk"], str(sim.get("risk")))

    # (4) 현대건설 AI 계약 — 제외 아님 + ai 또는 risk 노출
    check("현대건설 AI 하도급이 제외 아님",
          sim["grades"].get("f_hdec_ai") != GRADE_EXCLUDED,
          str(sim["grades"].get("f_hdec_ai")))
    check("현대건설 AI 하도급이 AI 또는 리스크 섹션에 노출",
          "f_hdec_ai" in allsec["ai"] or "f_hdec_ai" in allsec["risk"])

    # (5) 현대건설 제재 — risk_regulation + 제외 아님 + risk_priority 설정
    check("현대건설 벌점이 리스크·규제 섹션에 노출", "f_hdec_enf" in allsec["risk"])
    check("현대건설 벌점이 제외 아님",
          sim["grades"].get("f_hdec_enf") != GRADE_EXCLUDED,
          str(sim["grades"].get("f_hdec_enf")))
    check("현대건설 벌점에 risk_priority_score 설정(>0)",
          (sim.get("risk_pri") or {}).get("f_hdec_enf", 0) > 0,
          str((sim.get("risk_pri") or {}).get("f_hdec_enf")))

    # (6) 집계 호스트(v.daum.net) 정상 노출(하드 제외 금지) + 표시 정규화
    # D3D near-dup cluster cap(d64275c) 이후: 동일 'AI 데이터센터 전력' 사안을 신뢰 매체
    # f_ai_dc가 AI 테마 섹션 대표로 점유하므로, 집계 호스트 near-dup f_agg는 AI 섹션에서
    # dedup되고 D3F cross-surface 로직에 의해 '신규 이슈'로 노출된다(하드 제외 아님).
    # 핵심 계약은 그대로다: 집계 호스트 기사가 (1) 임원 surface에 정상 노출되고 (2)
    # display_source가 'Daum 경유'로 정규화된다. ('AI 섹션 고정 노출'은 클러스터링 도입
    # 이전의 stale 기대였다 — 집계 near-dup을 신뢰 매체 위에 노출하지 않는 것이 옳다.)
    check("집계 호스트 fixture가 임원 surface에 정상 노출 (하드 제외 아님)",
          "f_agg" in (allsec["ai"] | allsec["top_new"]),
          f"ai={sim.get('ai')} top_new={sim.get('top_new')}")
    check("집계 호스트 display_source가 'Daum 경유'로 정규화",
          sim.get("agg_display") == "Daum 경유", str(sim.get("agg_display")))
    check("정적 리포트에 raw 'v.daum.net' 가시 노출 없음 (href 제외)",
          not sim.get("report_vdaum_visible"))
    check("정적 리포트에 '경유' 표기 존재", sim.get("report_via"))

    # (7) 광범위 미파손 — 정상 AI/경쟁사 기사 유지
    check("정상 AI 데이터센터 기사가 AI 섹션 유지", "f_ai_dc" in allsec["ai"])
    check("삼성물산 EPC/SMR 경쟁사 기사가 AI 또는 수주·해외 유지",
          "f_samsung" in allsec["ai"] or "f_samsung" in allsec["biz"])
    check("스마트건설 챌린지가 Top 신규/상단 표시 후보에 없음",
          "f_event" not in allsec["top_new"] and "f_event" not in allsec["top_imm"],
          f"top_new={sim.get('top_new')} top_imm={sim.get('top_imm')}")
    check("30일 초과 현대건설 기사는 Top 신규/상단 표시 후보에 없음",
          "f_old_hdec" not in allsec["top_new"] and "f_old_hdec" not in allsec["top_imm"],
          f"top_new={sim.get('top_new')} top_imm={sim.get('top_imm')}")


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
    check("mock 데모 숫자 불변 (품질 게이트가 mock을 바꾸지 않음)",
          got == MOCK_BASELINE, f"got={got} expect={MOCK_BASELINE}")


def main() -> int:
    print(f"== verify_live_article_quality_gate @ {ROOT} ==")
    # in-process import이 저장소 radar.db를 건드리지 않게 temp DB로 격리한다 (방어적).
    os.environ["DB_PATH"] = os.path.join(
        tempfile.mkdtemp(prefix="hdec_aqv_"), "verify.db")
    db_before = _db_state()

    check_py_compile()
    check_rules_files()
    check_integration()
    check_article_quality_unit()
    check_radar_unit()
    check_source_display_unit()
    check_scoring_unit()

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
