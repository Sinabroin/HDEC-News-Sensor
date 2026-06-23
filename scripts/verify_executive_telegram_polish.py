"""P0-C1.13 검증기 — Executive Telegram & Section Routing Polish 회귀 검사.

목적: Telegram 다이제스트가 'AI 뉴스 요약'이 아니라 '현대건설 임원 의사결정 브리프'로
보이게 만든 변경을 결정적으로 보장한다 (네트워크 없음, temp DB 격리).

검사:
- 현대건설 연관 블록이 implication(리스크/전략/운영/재무)별로 묶여 ≤3줄 메모형으로 나온다.
- AI 라벨이 '[AI 관련]'(간결)이고 '[AI 관련 Top 3]'(리포트형)이 아니다.
- 수주·해외 후보가 있으면 [수주·해외] 블록(≤2줄)이 나온다 — 발주/EPC가 공급사보다 먼저.
- [주요 테마]·Macro Snapshot·시장지표 미연동·뉴스 자동 수집이 다이제스트에 없다.
- 재무·자금조달(전환사채/금리 등) 기사가 AI Top/AI 레이더에 들어가지 않는다.
- 현대건설 벌점 헤드라인이 다이제스트에 두 번 반복되지 않는다 (직접 vs 리스크).
- 공급사 단독(가온전선)이 더 강한 AI/EPC/현대건설 신호를 밀어내지 않는다.
- '요약 대시보드 보기' / '전체 리포트 보기' 버튼 안내가 유지된다
  (개인 봇 버튼은 별도 검증기 소관).
- mock 다이제스트가 정직하게 'mock 데이터 기반'으로 표기된다.

핵심 원칙: 분류는 순수 함수(decision_relevance/article_quality/radar)가 단일 소유하고
fixture로 결정적으로 검사한다. 라이브 인터넷에 의존하지 않는다 — fetch_all을 fixture로
패치해 temp DB subprocess에서 파이프라인+다이제스트를 돌린다. 저장소 radar.db는 안 건드린다.

사용법:
    python3 scripts/verify_executive_telegram_polish.py
"""

import json
import os
import py_compile
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIGEST_BUILDER = ROOT / "scripts" / "build_telegram_digest.py"
DR_MODULE = ROOT / "app" / "decision_relevance.py"
RADAR_DB = ROOT / "radar.db"

GRADE_EXCLUDED = "제외"

# ---- fixture: P0-C1.13 회귀 시나리오 (제목·출처만으로 결정적 판정) ----
# 현대건설 직접: 벌점(리스크) · 도시정비/DC(전략) · AI 하도급(운영) · 전환사채(재무).
# 비-공급사 AI: 광범위 AI DC 정책 · 삼성물산 EPC · SK에코플랜트 AI EPC.
# 공급사 단독: 가온전선 ×2. 발주 환경: 중동 재건.
FIX = [
    {"id": "f_pen", "source": "서울신문",
     "title": "서울시 현대건설에 벌점 사전통보 분양 수주 영향권",
     "snippet": "서울시가 현대건설에 벌점을 사전통보하면서 분양 수주 영향이 우려된다"},
    {"id": "f_dc", "source": "한국경제",
     "title": "현대건설 도시정비 12조 데이터센터 양 축 강화",
     "snippet": "현대건설이 도시정비와 데이터센터를 두 축으로 포트폴리오를 강화한다"},
    {"id": "f_aicon", "source": "데일리안",
     "title": "현대건설 AI로 하도급 계약 점검 1660억원 상생펀드 운영",
     "snippet": "현대건설이 AI로 협력사 하도급 계약을 점검하고 상생펀드를 운영한다"},
    {"id": "f_fin", "source": "한국경제",
     "title": "현대건설 0% 금리 5000억 전환사채 발행 투자자 몰린 까닭은",
     "snippet": "현대건설이 0% 금리 전환사채를 발행하자 투자자가 몰렸다"},
    {"id": "f_broadai", "source": "전자신문",
     "title": "특별법 시행 앞둔 AI 데이터센터 건설사 전력망 냉각 설루션 경쟁",
     "snippet": "AI 데이터센터 특별법 시행을 앞두고 건설사들이 전력망 냉각 경쟁을 벌인다"},
    {"id": "f_sams", "source": "한국경제",
     "title": "삼성물산 올해 EPC 수주 목표 10.1조 SMR 데이터센터 정조준",
     "snippet": "삼성물산이 EPC 수주 목표를 높이고 SMR 데이터센터를 정조준한다"},
    {"id": "f_sk", "source": "이데일리",
     "title": "SK에코플랜트 AI EPC 데이터센터 수주 추진 체질 전환",
     "snippet": "SK에코플랜트가 AI EPC로 데이터센터 수주를 추진한다"},
    {"id": "f_mideast", "source": "서울경제",
     "title": "종전에 중동 재건 기대 건설사 수주 채비 신중론 병존",
     "snippet": "중동 재건 기대에 건설사들이 수주를 준비하나 신중론도 병존한다"},
    {"id": "f_gaon1", "source": "전기신문",
     "title": "가온전선 데이터센터 전력 케이블 공급 확대",
     "snippet": "가온전선이 데이터센터 전력 케이블 공급을 확대한다"},
    {"id": "f_gaon2", "source": "전기신문",
     "title": "가온전선 데이터센터 전력망 케이블 수주 급증",
     "snippet": "가온전선 데이터센터 전력망 케이블 수주가 급증했다"},
]
for _f in FIX:
    _f.setdefault("published_at", "2026-06-14T09:00:00+09:00")
    _f.setdefault("url", f"https://ex.test/{_f['id']}")

PENALTY_MARK = "벌점 사전통보"   # 현대건설 직접·리스크 양쪽 후보 — 중복 표기 검사용

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

def _import_dr():
    sys.path.insert(0, str(ROOT))
    os.environ.setdefault("APP_MODE", "mock")
    from app import decision_relevance
    return decision_relevance


def check_finance_routing_unit() -> None:
    dr = _import_dr()
    fin = next(f for f in FIX if f["id"] == "f_fin")
    row = {"title": fin["title"], "source": fin["source"],
           "snippet": fin["snippet"], "topic_candidates": "[]"}
    c = dr.classify(row)
    check("재무 기사: primary == 현대건설 직접 영향",
          c["primary_executive_section"] == dr.HDEC_DIRECT,
          str(c["primary_executive_section"]))
    check("재무 기사: 거시경제(재무) secondary 멤버",
          dr.MACRO in c["secondary_executive_sections"],
          str(c["secondary_executive_sections"]))
    check("재무 기사: AI 섹션 멤버 아님 (투자자/현대건설 언급만으로 AI 금지)",
          dr.AI not in c["executive_sections"], str(c["executive_sections"]))
    check("재무 기사: is_finance 플래그 True", c["is_finance"] is True)
    check("재무 기사: decision_reason에 '자금조달' 포함",
          "자금조달" in c["decision_reason"], c["decision_reason"])
    # 명시적 DC 전략이 함께 있으면 AI/전략으로 본다 (예외 — finance라도 전략 맥락이면 AI).
    strat = dr.classify({"title": "현대건설 데이터센터 투자 위해 5000억 전환사채 발행",
                         "source": "한국경제", "snippet": "", "topic_candidates": "[]"})
    check("재무+데이터센터 전략 맥락이면 현대건설 직접(전략) 유지",
          strat["primary_executive_section"] == dr.HDEC_DIRECT)

    # 공급사 단독 판정 — 가온전선=True, 삼성물산/광범위 정책=False.
    check("가온전선 단독 → supplier_only True",
          dr.is_supplier_only("가온전선 데이터센터 전력 케이블 공급 확대") is True)
    check("삼성물산(건설 경쟁사) → supplier_only False",
          dr.is_supplier_only("삼성물산 EPC 수주 SMR 데이터센터") is False)
    check("광범위 AI DC 정책(회사명 없음) → supplier_only False",
          dr.is_supplier_only("특별법 AI 데이터센터 건설사 전력망 냉각 경쟁") is False)


def check_format_units() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    import build_telegram_digest as d

    # 현대건설 직접 그룹핑: 리스크/전략/운영/재무 4버킷 → ≤3줄 (재무는 캡으로 제외).
    hdec = [
        {"title": "서울시 현대건설에 벌점 사전통보 분양 수주 영향권",
         "article_id": "pen", "hdec_bucket": 1, "risk_radar_label": "제재"},
        {"title": "현대건설 도시정비 12조 데이터센터 양 축 강화",
         "article_id": "dc", "hdec_bucket": 2},
        {"title": "현대건설 AI로 하도급 계약 점검 상생펀드 운영",
         "article_id": "ai", "hdec_bucket": 3},
        {"title": "현대건설 0% 금리 전환사채 발행", "article_id": "fin", "hdec_bucket": 5},
    ]
    bullets, shown = d._hdec_grouped_bullets(hdec)
    check("현대건설 직접 그룹 bullet 2~3줄 (헤드라인 5줄 나열 아님)",
          2 <= len(bullets) <= 3, f"{len(bullets)}줄")
    check("현대건설 직접 그룹 라벨이 implication별 (리스크/전략/운영)",
          any("리스크" in b for b in bullets) and any("전략" in b for b in bullets)
          and any("운영" in b for b in bullets), " | ".join(bullets))
    check("그룹에 노출된 신호 id가 shown_ids에 수집됨", "pen" in shown and "dc" in shown)

    # 리스크 중복 회피: 벌점이 현대건설 직접에 이미 나오면 리스크·규제는 포인터만.
    data = {"header": "HDEC Executive Radar", "date_kst": "2026-06-16",
            "news_data_mode": "live", "executive_one_liner": "t", "status_board": [],
            "hdec_signals": hdec, "top_signals": [], "ai_first": True,
            "biz_signals": [{"title": "종전에 중동 재건 기대 건설사 수주 채비",
                             "article_id": "me"}],
            "risk_signals": [{"title": "서울시 현대건설에 벌점 사전통보 분양 수주 영향권",
                              "article_id": "pen", "risk_radar_label": "제재"}],
            "category_counts": [], "macro_snapshot": {}, "mode": "mock"}
    msg = d.format_digest_message(data)
    check("벌점 헤드라인이 다이제스트에 한 번만 (직접 vs 리스크 중복 제거)",
          msg.count(PENALTY_MARK) == 1, f"{msg.count(PENALTY_MARK)}회")
    check("리스크·규제 줄이 현대건설 연관 포인터로 대체",
          "현대건설 연관 항목 참고" in msg)
    check("[수주·해외] 블록 노출 (후보 있을 때)", "[수주·해외]" in msg)
    check("AI 라벨이 '[AI 관련]'이고 '[AI 관련 Top 3]' 아님",
          "[AI 관련]" in msg and "[AI 관련 Top" not in msg)
    check("다이제스트에 '[주요 테마]' 없음", "[주요 테마]" not in msg)
    check("live 다이제스트에 '자동 수집' 기술 표현 없음", "자동 수집" not in msg)


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
        "import build_telegram_digest as tg\n"
        "tg.build_brief_via_mock_pipeline=lambda *a,**k: b\n"
        "data=tg.build_digest_data(); msg=tg.format_digest_message(data)\n"
        "rows={r['id']:r for r in db.fetch_articles_with_scores()}\n"
        "def did(seq):return [s.get('article_id') for s in (seq or [])]\n"
        "out={'mode':b['news_data_mode'],'msg':msg,\n"
        " 'ai_top':did(data['top_signals']),'biz':did(data['biz_signals']),\n"
        " 'hdec':did(data['hdec_signals']),\n"
        " 'ai_section':did(b.get('ai_radar_signals')),\n"
        " 'macro_section':did(b.get('macro_economy_signals')),\n"
        " 'grades':{i:rows[i]['alert_grade'] for i in rows}}\n"
        "print(json.dumps(out, ensure_ascii=False))\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True,
                          text=True, cwd=ROOT, timeout=240)
    if proc.returncode != 0:
        check("파이프라인+다이제스트 시뮬레이션 실행", False,
              (proc.stderr or "").strip()[-400:])
        return None
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        check("파이프라인 시뮬레이션 출력 파싱", False, (proc.stdout or "")[-300:])
        return None


def check_pipeline(sim: dict | None) -> None:
    if not sim:
        return
    msg = sim.get("msg") or ""
    ai_top = set(sim.get("ai_top") or [])
    biz = sim.get("biz") or []
    hdec = set(sim.get("hdec") or [])

    check("시뮬: live 모드로 fixture 파이프라인 통과", sim.get("mode") == "live")

    # 다이제스트 구조 (Phase 2/3/5/6/7)
    check("다이제스트에 [현대건설 연관] 블록", "[현대건설 연관]" in msg)
    h_i, a_i = msg.find("[현대건설 연관]"), msg.find("[AI 관련")
    check("현대건설 연관이 AI보다 먼저", 0 <= h_i < a_i)
    check("AI 라벨 '[AI 관련]' (리포트형 'Top 3' 아님)",
          "[AI 관련]" in msg and "[AI 관련 Top" not in msg)
    check("[수주·해외] 블록 노출 (발주 환경 후보 있음)", "[수주·해외]" in msg)
    check("[주요 테마] 없음", "[주요 테마]" not in msg)
    for term in ("Macro Snapshot", "시장지표 미연동", "자동 수집"):
        check(f"다이제스트에 '{term}' 없음", term not in msg)
    check("벌점 헤드라인 두 번 반복 안 됨 (직접 vs 리스크)",
          msg.count(PENALTY_MARK) <= 1, f"{msg.count(PENALTY_MARK)}회")
    check("'요약 대시보드 보기'/'전체 리포트 보기' 안내 유지",
          "요약 대시보드 보기" in msg and "전체 리포트 보기" in msg)

    # 재무 라우팅 (Phase 3) — f_fin은 Telegram AI Top에 안 들어가고 현대건설 직접으로 간다.
    # (radar는 캔드 토픽 때문에 ai로 둘 수 있으나, 의사결정·Telegram 라우팅은 임원 기준이다.)
    check("재무 기사(f_fin)가 Telegram AI Top에 없음", "f_fin" not in ai_top,
          f"ai_top={sorted(ai_top)}")
    check("재무 기사(f_fin)가 현대건설 직접으로 라우팅 (AI 아님)", "f_fin" in hdec,
          f"hdec={sorted(hdec)}")
    check("재무 기사(f_fin)가 Telegram 수주·해외에 없음", "f_fin" not in set(biz))

    # 공급사 후순위 (Phase 4) — 가온전선이 더 강한 AI/EPC 신호를 밀어내지 않는다.
    check("AI Top에 광범위 AI DC 정책 기사(f_broadai) 포함", "f_broadai" in ai_top,
          f"ai_top={sorted(ai_top)}")
    check("AI Top이 가온전선 2건으로 도배되지 않음 (둘 다 제외)",
          "f_gaon1" not in ai_top and "f_gaon2" not in ai_top,
          f"ai_top={sorted(ai_top)}")
    check("AI Top에 경쟁 건설사 EPC 신호(삼성물산/SK) 1건 이상",
          bool({"f_sams", "f_sk"} & ai_top), f"ai_top={sorted(ai_top)}")

    # 수주·해외 — 발주/EPC/경쟁사 수주를 공급사보다 먼저 (첫 줄이 공급사 단독 아님).
    check("수주·해외 첫 줄이 공급사 단독(가온전선) 아님 (발주/EPC 우선)",
          bool(biz) and biz[0] not in ("f_gaon1", "f_gaon2"),
          f"biz={biz}")
    check("수주·해외에 발주/EPC/경쟁사 수주 신호 surface (공급사 단독 도배 아님)",
          bool(set(biz) & {"f_sk", "f_sams", "f_mideast"}), f"biz={biz}")

    # 현대건설 직접 — 벌점/전략/운영이 직접 섹션에 (재무 라우팅이 직접 섹션을 비우지 않음).
    check("현대건설 직접에 벌점·전략·운영 신호 surface",
          {"f_pen", "f_dc", "f_aicon"} <= hdec, f"hdec={sorted(hdec)}")


# ---------- mock 정직성 + 구조 ----------

def check_mock_digest() -> None:
    proc = subprocess.run([sys.executable, str(DIGEST_BUILDER), "--dry-run"],
                          capture_output=True, text=True,
                          env=_clean_env(), cwd=ROOT, timeout=120)
    if not check("mock 다이제스트 빌드 동작", proc.returncode == 0,
                 (proc.stderr or "").strip()[-200:]):
        return
    msg = proc.stdout or ""
    check("mock 다이제스트 'mock 데이터 기반' 정직 표기", "mock 데이터 기반" in msg)
    check("mock 다이제스트에 '자동 수집' 없음", "자동 수집" not in msg)
    check("mock 다이제스트에 [주요 테마] 없음", "[주요 테마]" not in msg)
    check("mock 다이제스트 AI 라벨 '[AI 관련]'",
          "[AI 관련]" in msg and "[AI 관련 Top" not in msg)
    check("mock 다이제스트 거시경제 전체 리포트 위임",
          "전체 리포트 보기" in msg and "거시경제" in msg)
    check("mock 다이제스트에 Macro Snapshot/미연동 placeholder 없음",
          "Macro Snapshot" not in msg and "시장지표 미연동" not in msg)


def check_digest_source() -> None:
    src = DIGEST_BUILDER.read_text(encoding="utf-8")
    check("다이제스트가 _hdec_grouped_bullets로 현대건설 직접 그룹핑",
          "_hdec_grouped_bullets" in src)
    check("다이제스트가 supplier_only로 공급사 후순위",
          "supplier_only" in src)
    dr = DR_MODULE.read_text(encoding="utf-8")
    check("decision_relevance가 FINANCE_TOKENS/is_supplier_only 정의",
          "FINANCE_TOKENS" in dr and "is_supplier_only" in dr)


def main() -> int:
    print(f"== verify_executive_telegram_polish @ {ROOT} ==")
    os.environ["DB_PATH"] = os.path.join(
        tempfile.mkdtemp(prefix="hdec_tgp_"), "verify.db")
    db_before = _db_state()

    check_py_compile()
    check_finance_routing_unit()
    check_format_units()

    sim = _run_pipeline_sim()
    check_pipeline(sim)

    check_mock_digest()
    check_digest_source()

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
