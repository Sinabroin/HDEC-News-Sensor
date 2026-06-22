"""D5-A verifier — configurable topic sensing profiles.

Deterministic, fully offline. No network, no Telegram. The classification checks
run in-process (pure config matching); the pipeline check uses a throwaway temp
DB and never touches the repo radar.db.

Validates:
- the topic profile config (required profiles, fields, query dedup),
- deterministic classification (entity + relevance anchor, noise rejected),
- that the new briefing sections are additive and the mock invariant holds,
- that the D4-D mixed-title rejection and D4-E AI supplement still pass.
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import topic_profiles  # noqa: E402

LIVE_COLLECTOR_SRC = (ROOT / "app" / "live_collector.py").read_text(encoding="utf-8")
BRIEFING_SRC = (ROOT / "app" / "briefing.py").read_text(encoding="utf-8")
RADAR_DB = ROOT / "radar.db"

REQUIRED_PROFILES = (
    "hdec_direct", "hyundai_group", "competitor_contractors",
    "trust_companies", "developers",
)

_failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    line = f"[{'PASS' if ok else 'FAIL'}] {name}"
    if detail:
        line += f" — {detail}"
    print(line)
    if not ok:
        _failures.append(name)
    return ok


def _article(title: str, snippet: str = "", source: str = "연합뉴스") -> dict:
    return {"title": title, "snippet": snippet, "source": source}


def _matches(title: str, profile_id: str) -> bool:
    profile = topic_profiles.get_topic_profile(profile_id)
    return profile is not None and topic_profiles.match_topic_profile(
        _article(title), profile)


def _db_state():
    if not RADAR_DB.exists():
        return None
    stat = RADAR_DB.stat()
    return (stat.st_mtime_ns, stat.st_size)


# --- 1/2: module exports + required profiles ------------------------------

def check_module_and_required() -> None:
    enabled = topic_profiles.get_enabled_topic_profiles()
    check("1: get_enabled_topic_profiles가 1건 이상 반환", len(enabled) >= 1,
          f"{len(enabled)}건")
    ids = {p.id for p in topic_profiles.all_topic_profiles()}
    for pid in REQUIRED_PROFILES:
        check(f"2: 필수 프로파일 존재 — {pid}", pid in ids)
        prof = topic_profiles.get_topic_profile(pid)
        check(f"2: get_topic_profile('{pid}') 반환", prof is not None and prof.id == pid)


# --- 3: per-profile field contract ----------------------------------------

def check_profile_fields() -> None:
    for pid in REQUIRED_PROFILES:
        p = topic_profiles.get_topic_profile(pid)
        if p is None:
            check(f"3: {pid} 필드 검사", False, "프로파일 없음")
            continue
        check(f"3: {pid} label 비어있지 않음", bool(p.label and p.label.strip()))
        check(f"3: {pid} queries >= 5건", len(p.queries) >= 5, f"{len(p.queries)}건")
        check(f"3: {pid} include_keywords 존재", len(p.include_keywords) >= 1)
        check(f"3: {pid} relevance_anchors 존재", len(p.relevance_anchors) >= 1)
        check(f"3: {pid} exclude_keywords 존재", len(p.exclude_keywords) >= 1)
        check(f"3: {pid} max_items 1~10", 1 <= int(p.max_items) <= 10, str(p.max_items))
        check(f"3: {pid} enabled가 bool", isinstance(p.enabled, bool))
        check(f"3: {pid} surface_key 비어있지 않음", bool(p.surface_key))


# --- 4: query dedup across enabled profiles -------------------------------

def check_query_dedup() -> None:
    queries = topic_profiles.iter_topic_queries()
    lowered = [q.strip().casefold() for q in queries]
    check("4: iter_topic_queries 중복 없음 (enabled 프로파일 전체)",
          len(lowered) == len(set(lowered)),
          f"{len(lowered)}개 중 고유 {len(set(lowered))}개")
    check("4: iter_topic_queries 비어있지 않음", len(queries) >= 5, f"{len(queries)}건")


# --- 5: generic Hyundai noise rejected ------------------------------------

def check_generic_hyundai_rejected() -> None:
    cases = [
        "현대차 신차 출시 행사 성황",
        "현대차 야구단 한국시리즈 우승",
        "현대차 1분기 자동차 판매량 사상 최대",
    ]
    for title in cases:
        check(f"5: generic Hyundai 거부 — {title}",
              not _matches(title, "hyundai_group"))


# --- 6: hyundai group accepted --------------------------------------------

def check_hyundai_group_accepted() -> None:
    cases = [
        "현대엔지니어링 데이터센터 AI 운영 기술 특허 출원",
        "HD현대일렉트릭 현대건설 전력기기 품질 ESG 협력",
        "현대제철 건설용 철강 공급망 안정화 투자",
    ]
    for title in cases:
        check(f"6: 현대 그룹사 인식 — {title}", _matches(title, "hyundai_group"))


# --- 7: competitor contractor accepted ------------------------------------

def check_competitor_accepted() -> None:
    cases = [
        "DL이앤씨 AI 데이터센터 EPC 본계약 체결",
        "포스코이앤씨 현장 안전 고용부 특별감독 착수",
        "대우건설 해외 원전 수주 기대감 확대",
    ]
    for title in cases:
        check(f"7: 경쟁 시공사 인식 — {title}", _matches(title, "competitor_contractors"))


# --- 8: trust company accepted --------------------------------------------

def check_trust_accepted() -> None:
    cases = [
        "한국토지신탁 정비사업 PF 구조 점검",
        "KB부동산신탁 책임준공 리스크 부각",
        "코람코자산신탁 개발사업 시공사 선정 본격화",
    ]
    for title in cases:
        check(f"8: 신탁사 인식 — {title}", _matches(title, "trust_companies"))


# --- 9: developer accepted ------------------------------------------------

def check_developer_accepted() -> None:
    cases = [
        "시행사 PF 브릿지론 연체 리스크 확대",
        "엠디엠 대형 개발사업 인허가 속도",
        "디벨로퍼 주도 사업 시공사 선정 난항",
    ]
    for title in cases:
        check(f"9: 시행사 인식 — {title}", _matches(title, "developers"))


# --- 10: stock/ETF/job/event noise rejected -------------------------------

def check_noise_rejected() -> None:
    # 엔티티는 맞지만 노이즈 — 어느 프로파일에도 들어오면 안 된다.
    cases = [
        "삼성물산 ETF 신규 편입 기대감",          # 종목/ETF
        "GS건설 신입사원 채용 박람회 개최",        # 채용
        "현대제철 사내 체육대회 이벤트 성료",       # 이벤트/사회공헌성
        "한국토지신탁 임원 인사 단행",            # 단순 인사
        "현대차 주가 급등 사세요",                # 주가/종목
    ]
    for title in cases:
        hits = topic_profiles.classify_topic_profiles(_article(title))
        check(f"10: 노이즈 거부 (전 프로파일) — {title}", not hits, str(hits))


# --- new briefing sections are additive + mock invariant holds ------------

def _run_pipeline_sim() -> dict | None:
    code = (
        "import os, sys, json, tempfile\n"
        "d=tempfile.mkdtemp()\n"
        "os.environ['DB_PATH']=os.path.join(d,'t.db')\n"
        "os.environ['APP_MODE']='mock'; os.environ['NEWS_MODE']='mock'\n"
        "sys.path.insert(0,'.')\n"
        "from app import db, collector, scoring, insight, briefing\n"
        "db.init_db(); collector.run(); scoring.score_all(); insight.generate_all()\n"
        "b=briefing.build_brief()\n"
        "keys=['hyundai_group_signals','competitor_contractor_signals',\n"
        " 'trust_company_signals','developer_signals']\n"
        "out={'present':{k:(k in b) for k in keys+['topic_profile_catalog']},\n"
        " 'lens':{k:[e.get('article_id') for e in (b.get(k) or [])] for k in keys},\n"
        " 'catalog':[c.get('id') for c in (b.get('topic_profile_catalog') or [])],\n"
        " 'counts':{'total':b.get('total_articles'),'signals':b.get('total_signals'),\n"
        "  'immediate':b.get('immediate_count'),'daily':b.get('daily_count'),\n"
        "  'weekly':b.get('weekly_count'),'excluded':b.get('excluded_count')}}\n"
        "print(json.dumps(out, ensure_ascii=False))\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True,
                          text=True, cwd=ROOT, timeout=240)
    if proc.returncode != 0:
        check("mock 파이프라인 실행", False, (proc.stderr or "").strip()[-700:])
        return None
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        check("mock 파이프라인 출력 파싱", False, (proc.stdout or "")[-400:])
        return None


def check_sections_additive(sim: dict | None) -> None:
    if not sim:
        return
    for key, present in sim["present"].items():
        check(f"브리핑 출력에 키 존재 — {key}", present)
    # hdec_direct는 기존 섹션 재사용이라 새 lens 키가 없다 — 나머지 4개만 lens 검사.
    for key, ids in sim["lens"].items():
        # 섹션별 max_items 상한 (프로파일 5건) 준수
        check(f"{key} <= 5건", len(ids) <= 5, f"{len(ids)}건")
        check(f"{key} 내부 중복 article 없음", len(ids) == len(set(ids)), str(ids))
    cat = sim.get("catalog") or []
    for pid in REQUIRED_PROFILES:
        check(f"topic_profile_catalog에 {pid} 포함", pid in cat)


def check_mock_invariant(sim: dict | None) -> None:
    if not sim:
        return
    counts = sim["counts"]
    expected = {"total": 28, "signals": 21, "immediate": 3,
                "daily": 6, "weekly": 12, "excluded": 7}
    check("mock 카운트 불변 28/21/3/6/12/7 (추가 키가 기존 집계를 바꾸지 않음)",
          counts == expected, str(counts))


# --- 11/12: D4-D mixed-title rejection + D4-E supplement still pass --------

def _run_verifier(script: str) -> tuple[bool, str]:
    path = ROOT / "scripts" / script
    if not path.exists():
        return False, f"{script} 없음"
    proc = subprocess.run([sys.executable, str(path)], capture_output=True,
                          text=True, cwd=ROOT, timeout=300)
    return proc.returncode == 0, (proc.stdout or "").strip().splitlines()[-1:][0] \
        if proc.stdout else ""


def check_d4d_still_passes() -> None:
    from app import surface_contracts
    mixed = surface_contracts.decide_ai_tab(
        {"title": "현대건설 도시정비 12조 데이터센터 양 축 강화"})
    check("11: D4-D 혼합제목 AI 탭 거부 유지 (decide_ai_tab)",
          not mixed.eligible, mixed.reason_code)
    ok, last = _run_verifier("verify_surface_contracts.py")
    check("11: verify_surface_contracts PASS", ok, last)


def check_d4e_still_passes() -> None:
    ok, last = _run_verifier("verify_ai_tab_supplement_routing.py")
    check("12: verify_ai_tab_supplement_routing PASS", ok, last)


# --- live_collector wiring is scoped (static) -----------------------------

def check_wiring_scoped() -> None:
    check("live_collector가 topic_profiles import",
          "topic_profiles" in LIVE_COLLECTOR_SRC)
    check("live_collector 프로파일 병합이 기본 소스로 한정 (sources_path is None)",
          "_merge_topic_profile_groups" in LIVE_COLLECTOR_SRC
          and "sources_path is None" in LIVE_COLLECTOR_SRC)
    check("briefing가 topic_profiles로 추가 섹션 파생",
          "topic_profiles" in BRIEFING_SRC
          and "hyundai_group_signals" in BRIEFING_SRC)


def main() -> int:
    print(f"== verify_topic_profiles @ {ROOT} ==")
    db_before = _db_state()
    check_module_and_required()
    check_profile_fields()
    check_query_dedup()
    check_generic_hyundai_rejected()
    check_hyundai_group_accepted()
    check_competitor_accepted()
    check_trust_accepted()
    check_developer_accepted()
    check_noise_rejected()
    check_wiring_scoped()
    sim = _run_pipeline_sim()
    check_sections_additive(sim)
    check_mock_invariant(sim)
    check_d4d_still_passes()
    check_d4e_still_passes()
    check("repo radar.db가 검증 중 변경/생성되지 않음 (temp DB 격리)",
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
