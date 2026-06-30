"""P0-C1.14 검증기 — Final Live Routing Cleanup 회귀 검사 (결정적, 네트워크 없음).

P0-C1.13 라이브 점검에서 남은 라우팅 갭 3개를 닫았는지 brief JSON 구조 + Telegram
문자열 + 감사 마크다운으로 함께 보장한다 (Telegram 문자열만 보지 않는다):

A. 수주·해외 Telegram 블록 — 발주/EPC/해외 후보가 있으면 [수주·해외]가 항상 나오고,
   dedup(현대건설 직접 키 선점)이 블록을 통째로 지우지 않는다. 공급사 단독은 발주/EPC/해외
   신호보다 먼저 오지 않는다.
B. 재무·자금조달 하드 오버라이드 — 현대건설 전환사채 기사가 ai_radar_signals/AI Top/
   신규이슈·즉시 신호에 'AI'로 남지 않고 현대건설 직접으로 라우팅된다. P0-D3A: 재무는
   거시경제 레이더(FX·유가·금리 시장변수)에 **들어가지 않는다** — 자본시장 이벤트(전환사채·
   CB)는 거시 변수가 아니므로 override가 AI를 거시가 아니라 other로 내린다 (현대건설 재무는
   decision 멤버십으로 [현대건설 직접], 거시는 secondary 라벨로만 남는다). 단 명시적
   데이터센터/사업 전략 맥락이 있으면 현대건설 전략(AI)으로 유지된다.
C. 공급사 단독 우선순위 — 더 강한 비공급사(현대건설/삼성/EPC/해외) 후보가 3건 이상이면
   공급사 단독은 AI Top에 들지 않는다. 공급사만 있으면 1건만(클래스 단위 dedup). 공급사는
   리포트/감사의 경쟁사·공급망에 그대로 남는다.
D. 감사 헬퍼 — 'AI 섹션에 남은 재무 신호'가 0건(통과)이다.

핵심 원칙: 분류는 순수 함수(decision_relevance/radar/article_quality)가 단일 소유하고
fixture로 결정적으로 검사한다. live_collector.fetch_all을 fixture로 패치해 temp DB
subprocess에서 파이프라인+브리프+다이제스트+감사를 돌린다. 저장소 radar.db는 안 건드린다.

사용법:
    python3 scripts/verify_executive_final_live_routing.py
"""

import json
import os
import py_compile
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIGEST_BUILDER = ROOT / "scripts" / "build_telegram_digest.py"
RADAR_DB = ROOT / "radar.db"
KST = timezone(timedelta(hours=9))
FIXTURE_PUBLISHED_AT = (
    datetime.now(KST) - timedelta(days=1)
).strftime("%Y-%m-%dT09:00:00+09:00")

# ---- fixture: P0-C1.14 라이브 라우팅 시나리오 (제목·스니펫만으로 결정적 판정) ----
# 현대건설 직접: 벌점(리스크) · 도시정비/DC(전략) · 전환사채(재무, AI 금지) ·
#               데이터센터 사업 전환사채(전략 맥락 → AI 유지).
# 비-공급사 AI: 광범위 AI DC 발주 환경 · 삼성물산 EPC/SMR/DC · SK에코플랜트 AI EPC DC.
# 발주 환경: 중동 재건. 공급사 단독(서로 다른 회사, 같은 전선 클래스): 가온전선 · 대한전선.
FIX = [
    {"id": "f_pen", "source": "서울신문",
     "title": "서울시 현대건설에 벌점 사전통보 분양 수주 영향권",
     "snippet": "서울시가 현대건설에 벌점을 사전통보하면서 분양 수주 영향이 우려된다"},
    {"id": "f_dc", "source": "한국경제",
     "title": "현대건설 도시정비 12조 데이터센터 양 축 강화",
     "snippet": "현대건설이 도시정비와 데이터센터를 두 축으로 포트폴리오를 강화한다"},
    {"id": "f_fin", "source": "한국경제",
     "title": "현대건설 0% 금리 5000억 전환사채 발행 투자자 몰린 까닭은",
     "snippet": "현대건설이 0% 금리 전환사채를 발행하자 투자자가 몰렸다"},
    {"id": "f_finproj", "source": "이데일리",
     "title": "현대건설 데이터센터 사업 투자 위해 3000억 회사채 발행",
     "snippet": "현대건설이 데이터센터 사업 투자를 위해 회사채를 발행한다"},
    {"id": "f_broadai", "source": "전자신문",
     "title": "AI 데이터센터 발주 확대 건설사 전력망 냉각 설루션 경쟁",
     "snippet": "AI 데이터센터 발주 확대에 맞춰 건설사들이 전력망 냉각 경쟁을 벌인다"},
    {"id": "f_sams", "source": "한국경제",
     "title": "삼성물산 올해 EPC 수주 목표 10.1조 SMR 데이터센터 정조준",
     "snippet": "삼성물산이 EPC 수주 목표를 높이고 SMR 데이터센터를 정조준한다"},
    {"id": "f_sk", "source": "이데일리",
     "title": "SK에코플랜트 AI EPC 데이터센터 수주 추진 체질 전환",
     "snippet": "SK에코플랜트가 AI EPC로 데이터센터 수주를 추진한다"},
    {"id": "f_mideast", "source": "서울경제",
     "title": "종전에 중동 재건 기대 건설사 수주 채비 신중론 병존",
     "snippet": "중동 재건 기대에 건설사들이 수주를 준비하나 신중론도 병존한다"},
    {"id": "f_gaon", "source": "전기신문",
     "title": "가온전선 미국 AI 데이터센터 전력 케이블 버스덕트 공급 확대",
     "snippet": "가온전선이 미국 AI 데이터센터에 전력 케이블과 버스덕트 공급을 확대한다"},
    {"id": "f_daehan", "source": "전기신문",
     "title": "대한전선 데이터센터 전력망 케이블 수주 급증 생산능력 2배",
     "snippet": "대한전선이 데이터센터 전력망 케이블 수주가 급증해 생산능력을 2배로 늘린다"},
]
for _f in FIX:
    # Freshness is not the contract under test. Keep routing fixtures recent so an
    # aging wall-clock date cannot turn supplier/AI candidates into excluded rows.
    _f.setdefault("published_at", FIXTURE_PUBLISHED_AT)
    _f.setdefault("url", f"https://ex.test/{_f['id']}")

PENALTY_MARK = "벌점 사전통보"
SUPPLIERS = {"f_gaon", "f_daehan"}

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


# ---------- 순수 함수 단위 검사 (in-process) ----------

def _import():
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "scripts"))
    os.environ.setdefault("APP_MODE", "mock")
    from app import decision_relevance
    import build_telegram_digest as digest
    return decision_relevance, digest


def check_units() -> None:
    dr, d = _import()

    # B 단위 — 재무 라우팅은 raw 제목+스니펫만 본다 (생성 topic_candidates 무시).
    pollute = '["현대건설 데이터센터"]'   # collector가 50% 토큰 매칭으로 붙이는 캔드 토픽
    fin = dr.classify({"title": "현대건설 0% 금리 5000억 전환사채 발행",
                       "source": "한국경제", "snippet": "투자자가 몰렸다",
                       "topic_candidates": pollute})
    check("B: 재무 기사 is_finance True (raw 기준, 캔드 토픽 무시)", fin["is_finance"] is True)
    check("B: 재무 기사 AI 섹션 멤버 아님", dr.AI not in fin["executive_sections"],
          str(fin["executive_sections"]))
    check("B: 재무 기사 수주·해외 멤버 아님 (주입 토픽으로 발주 오분류 차단)",
          dr.ORDER_OVERSEAS not in fin["executive_sections"],
          str(fin["executive_sections"]))
    check("B: 재무 기사 현대건설 직접 + 거시 라우팅",
          fin["primary_executive_section"] == dr.HDEC_DIRECT
          and dr.MACRO in fin["secondary_executive_sections"])
    # P0-D3A: 재무 AI는 거시가 아니라 other로 내린다 (자본시장 이벤트 ≠ 거시 변수).
    check("B: override_radar_section이 AI 재무를 other로 내림 (거시 비혼입)",
          dr.override_radar_section("ai", fin) == dr.OTHER)
    check("B: 비재무는 override가 AI 그대로 둠",
          dr.override_radar_section("ai", {"is_finance": False}) == "ai")
    # 예외 — 명시적 데이터센터/사업 전략이면 재무라도 현대건설 전략(AI 유지 가능).
    proj = dr.classify({"title": "현대건설 데이터센터 사업 투자 위해 3000억 회사채 발행",
                        "source": "한국경제", "snippet": "", "topic_candidates": "[]"})
    check("B: 데이터센터 사업 전략 맥락이면 is_finance False (AI/전략 유지)",
          proj["is_finance"] is False and dr.AI in proj["executive_sections"],
          str(proj["executive_sections"]))

    # A 단위 — order_class: 경쟁사 EPC/DC=0, 해외=1, 현대건설 직접=2, 공급사 단독=3.
    oc = lambda t: dr.classify({"title": t, "source": "x", "snippet": "",
                                "topic_candidates": "[]"})["order_class"]
    check("A: 삼성물산 EPC·SMR·DC → order_class 0", oc("삼성물산 EPC 수주 SMR 데이터센터") == 0)
    check("A: 중동 재건 → order_class 1", oc("중동 재건 건설사 수주 채비") == 1)
    check("A: 가온전선 단독 → order_class 3", oc("가온전선 데이터센터 케이블 공급") == 3)
    check("A: 공급사 클래스가 경쟁사 EPC보다 뒤 (3 > 0)",
          oc("가온전선 데이터센터 케이블") > oc("삼성물산 EPC 데이터센터 수주"))

    # C 단위 — 클래스 단위 dedup: 서로 다른 공급사라도 AI Top 1건만.
    strong = [{"title": f"AI 데이터센터 정책 {i}", "article_id": f"s{i}",
               "final_score": 4.0 - i * 0.1, "supplier_only": False} for i in range(3)]
    sup = [{"title": "가온전선 케이블", "article_id": "g1", "final_score": 3.9,
            "supplier_only": True},
           {"title": "대한전선 버스덕트", "article_id": "g2", "final_score": 3.8,
            "supplier_only": True}]
    ai_sorted = sorted(strong + sup, key=lambda e: 1 if e.get("supplier_only") else 0)
    top = d._pick_diverse(ai_sorted, 3, set(), key=d._dedup_key)
    ids = [e["article_id"] for e in top]
    check("C: 강한 비공급사 3건이면 AI Top에서 공급사 제외",
          not ({"g1", "g2"} & set(ids)), str(ids))
    only_sup = d._pick_diverse(sorted(sup, key=lambda e: 1), 3, set(), key=d._dedup_key)
    check("C: 공급사만 있으면 AI Top 1건만 (서로 다른 회사라도 클래스 dedup)",
          len(only_sup) == 1, str([e["article_id"] for e in only_sup]))


# ---------- 파이프라인 시뮬레이션 (temp DB subprocess, fetch_all 패치) ----------

def _run_sim() -> dict | None:
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
        "import build_telegram_digest as tg\n"
        "tg.build_brief_via_mock_pipeline=lambda *a,**k: b\n"
        "data=tg.build_digest_data(); msg=tg.format_digest_message(data)\n"
        "import audit_live_article_quality as aud\n"
        "audit_md=aud.build_markdown(b)\n"
        "def did(seq):return [s.get('article_id') for s in (seq or [])]\n"
        "def sect(seq):return [(s.get('article_id'), s.get('radar_section')) "
        "for s in (seq or [])]\n"
        "out={'mode':b['news_data_mode'],'msg':msg,\n"
        " 'ai_top':did(data['top_signals']),'biz':did(data['biz_signals']),\n"
        " 'hdec':did(data['hdec_signals']),\n"
        " 'ai_section':did(b.get('ai_radar_signals')),\n"
        " 'macro_section':did(b.get('macro_economy_signals')),\n"
        " 'competitor':did(b.get('competitor_supply_signals')),\n"
        " 'business':did(b.get('business_signals')),\n"
        " 'top_new':sect(b.get('top_new_issues')),\n"
        " 'top_immediate':sect(b.get('top_immediate_signals')),\n"
        " 'audit_fin_zero': ('재무 하드 오버라이드 검증 · 정상=0건) (0건)' in audit_md),\n"
        " 'audit_chars': len(audit_md)}\n"
        "print(json.dumps(out, ensure_ascii=False))\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True,
                          text=True, cwd=ROOT, timeout=240)
    if proc.returncode != 0:
        check("파이프라인+다이제스트+감사 시뮬레이션 실행", False,
              (proc.stderr or "").strip()[-500:])
        return None
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        check("시뮬레이션 출력 파싱", False, (proc.stdout or "")[-300:])
        return None


def check_sim(sim: dict | None) -> None:
    if not sim:
        return
    msg = sim.get("msg") or ""
    ai_top = set(sim.get("ai_top") or [])
    biz = sim.get("biz") or []
    hdec = set(sim.get("hdec") or [])
    ai_section = set(sim.get("ai_section") or [])
    macro_section = set(sim.get("macro_section") or [])
    competitor = set(sim.get("competitor") or [])

    check("시뮬: live 모드로 fixture 파이프라인 통과", sim.get("mode") == "live")

    # ---- B. 재무 하드 오버라이드 (JSON 구조 기준) ----
    check("B: f_fin이 ai_radar_signals(AI 섹션)에 없음", "f_fin" not in ai_section,
          f"ai_section={sorted(ai_section)}")
    check("B: f_fin이 Telegram AI Top에 없음", "f_fin" not in ai_top,
          f"ai_top={sorted(ai_top)}")
    check("B: f_fin이 현대건설 직접으로 라우팅", "f_fin" in hdec, f"hdec={sorted(hdec)}")
    # P0-D3A: 재무는 거시경제 레이더에 들어가지 않는다 ([현대건설 직접]에서만 노출, 거시 secondary 라벨).
    check("B: f_fin이 거시경제 섹션에 없음 (재무 거시 레이더 비혼입)",
          "f_fin" not in macro_section, f"macro_section={sorted(macro_section)}")
    check("B: f_fin이 Telegram 수주·해외에 없음", "f_fin" not in set(biz), f"biz={biz}")
    for label, seq in (("신규이슈", sim.get("top_new")),
                       ("즉시 신호", sim.get("top_immediate"))):
        bad = [aid for aid, s in (seq or []) if aid == "f_fin" and s == "ai"]
        check(f"B: f_fin이 {label}에 'AI' 라벨로 남지 않음", not bad, str(seq))
    # 예외 — 데이터센터 사업 전략 전환사채(f_finproj)는 AI/전략(현대건설 직접)에 남을 수 있다.
    check("B: 데이터센터 사업 전환사채(f_finproj)는 AI 또는 현대건설 직접 유지",
          "f_finproj" in ai_section or "f_finproj" in hdec,
          f"ai_section={sorted(ai_section)} hdec={sorted(hdec)}")

    # ---- A. 수주·해외 selection ----
    # D7-AD의 핵심 링크는 3개 cap(HDEC·AI·리스크 우선)이므로 수주·해외는 JSON selection으로
    # 검증하고, Telegram 본문은 공통 renderer의 링크 cap/4문장 계약을 검증한다.
    check("A: Telegram 공통 renderer 핵심 링크 cap", "핵심 링크" in msg
          and msg.count('<a href="') <= 3)
    check("A: 수주·해외가 dedup으로 통째로 사라지지 않음 (≥1줄)", len(biz) >= 1, f"biz={biz}")
    check("A: 수주·해외 첫 줄이 공급사 단독 아님 (발주/EPC/해외 우선)",
          bool(biz) and biz[0] not in SUPPLIERS, f"biz={biz}")
    check("A: 수주·해외에 발주/EPC/경쟁사/해외 신호 surface",
          bool(set(biz) & {"f_sams", "f_sk", "f_mideast"}), f"biz={biz}")
    # 공급사가 발주/EPC/해외 신호보다 앞에 오지 않는다 (순서 보장).
    first_sup = next((i for i, x in enumerate(biz) if x in SUPPLIERS), len(biz))
    first_strong = next((i for i, x in enumerate(biz)
                         if x in {"f_sams", "f_sk", "f_mideast"}), len(biz))
    check("A: 공급사 단독이 발주/EPC/해외보다 먼저 오지 않음",
          first_strong <= first_sup, f"biz={biz}")

    # ---- C. 공급사 단독 우선순위 ----
    check("C: AI Top에 광범위 AI DC 발주 환경(f_broadai) 포함", "f_broadai" in ai_top,
          f"ai_top={sorted(ai_top)}")
    check("C: AI Top에 공급사 단독 없음 (강한 비공급사 3건 이상 존재)",
          not (SUPPLIERS & ai_top), f"ai_top={sorted(ai_top)}")
    check("C: AI Top에 경쟁 건설사 EPC(삼성물산/SK) 1건 이상",
          bool({"f_sams", "f_sk"} & ai_top), f"ai_top={sorted(ai_top)}")
    check("C: 공급사 단독이 리포트/감사 경쟁사·공급망에 그대로 남음",
          bool(SUPPLIERS & competitor), f"competitor={sorted(competitor)}")

    # ---- D. 감사 헬퍼 — AI 섹션 잔류 재무 0건 ----
    check("D: 감사 'AI 섹션에 남은 재무 신호' 0건 (오버라이드 통과)",
          bool(sim.get("audit_fin_zero")), f"audit_chars={sim.get('audit_chars')}")

    # ---- 회귀 가드 ----
    check("회귀: [현대건설 연관] 블록 유지", "[현대건설 연관]" in msg)
    check("회귀: AI 라벨 '[AI 관련]' (리포트형 'Top 3' 아님)",
          "[AI 관련]" in msg and "[AI 관련 Top" not in msg)
    check("회귀: [주요 테마] 없음", "[주요 테마]" not in msg)
    for term in ("Macro Snapshot", "시장지표 미연동", "자동 수집"):
        check(f"회귀: 다이제스트에 '{term}' 없음", term not in msg)
    check("회귀: 벌점 헤드라인 두 번 반복 안 됨", msg.count(PENALTY_MARK) <= 1,
          f"{msg.count(PENALTY_MARK)}회")
    check("회귀: '대시보드 보기'/'상세 리포트 보기' 안내 유지",
          "대시보드 보기" in msg and "상세 리포트 보기" in msg)


# ---------- mock 정직성 + 회귀 ----------

def check_mock() -> None:
    proc = subprocess.run([sys.executable, str(DIGEST_BUILDER), "--dry-run"],
                          capture_output=True, text=True,
                          env=_clean_env(), cwd=ROOT, timeout=120)
    if not check("mock 다이제스트 빌드 동작", proc.returncode == 0,
                 (proc.stderr or "").strip()[-200:]):
        return
    msg = proc.stdout or ""
    check("mock 다이제스트 'mock 데이터 기반' 정직 표기", "mock 데이터 기반" in msg)
    check("mock 다이제스트는 리포트형 상태 카운트(참고/제외)를 본문에 싣지 않음",
          "참고/제외" not in msg)
    check("mock 다이제스트에 [주요 테마] 없음", "[주요 테마]" not in msg)
    check("mock 다이제스트 AI 라벨 '[AI 관련]'",
          "[AI 관련]" in msg and "[AI 관련 Top" not in msg)


def check_personal_bot_button() -> None:
    """개인 봇 진입 버튼 동작이 그대로인지 — 발송 스크립트의 deep-link 키보드 유지."""
    sender = ROOT / "scripts" / "send_telegram.py"
    if not sender.exists():
        check("send_telegram.py 존재", False)
        return
    src = sender.read_text(encoding="utf-8")
    check("회귀: 개인 봇 진입(개인 질의하기 deep-link) 동작 유지",
          "개인 질의하기" in src or "inline_keyboard" in src or "t.me/" in src)


def main() -> int:
    print(f"== verify_executive_final_live_routing @ {ROOT} ==")
    os.environ["DB_PATH"] = os.path.join(
        tempfile.mkdtemp(prefix="hdec_flr_"), "verify.db")
    db_before = _db_state()

    check_py_compile()
    check_units()

    sim = _run_sim()
    check_sim(sim)

    check_mock()
    check_personal_bot_button()

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
