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
EXPECTED_TOPIC_QUERY_COUNT = 63
# Proven on clean 285e0e3 before the D5-D dirty patch. The old 28/21/3/6/12/7
# verifier baseline had already drifted; D5-D must preserve this current mock
# executive count while adding only business/org/scope metadata keys.
EXPECTED_MOCK_COUNTS = {"total": 28, "signals": 19, "immediate": 3,
                        "daily": 6, "weekly": 10, "excluded": 9}

# P0-D5-D — Layer-1 사업 부문 렌즈 7종 + Layer-2 조직 태그 7종 + Layer-3 실행범위 태그 4종.
REQUIRED_BUSINESS_LENSES = (
    "civil_infrastructure", "building_housing", "plant", "new_energy",
    "development_business", "global_business", "safety_quality",
)
REQUIRED_ORG_TAGS = (
    "pi", "finance_accounting", "corporate_support", "strategy_planning",
    "audit_compliance", "construction_technology_research", "gbc_development",
)
REQUIRED_SCOPE_TAGS = (
    "domestic_site", "overseas_site", "overseas_branch", "overseas_subsidiary",
)
# 부문별 수용 케이스 (강한 도메인 키워드 + 건설/사업 관련성). check 6~12.
BUSINESS_ACCEPT = {
    "civil_infrastructure": ["GTX 철도 건설사 수주", "SOC 예산 토목 공공공사 발주",
                             "항만 인프라 현대건설 수주"],
    "building_housing": ["현대건설 도시정비 재건축 수주", "아파트 하자 품질 건설사 리스크",
                         "공사비 갈등 조합 시공사 선정"],
    "plant": ["LNG 플랜트 발주 사우디 건설사 수주", "현대건설 원전 EPC",
              "중동 플랜트 공급망 리스크"],
    "new_energy": ["SMR 전력망 데이터센터 에너지 인프라", "수소 플랜트 탄소중립 건설",
                   "해상풍력 송전망 EPC"],
    "development_business": ["시행사 PF 리스크", "GBC 개발사업 인허가",
                            "디벨로퍼 시공사 선정"],
    "global_business": ["사우디 해외현장 플랜트 수주", "호르무즈 해협 유가 해외사업 리스크",
                        "해외법인 환율 리스크"],
    "safety_quality": ["중대재해 특별감독 건설현장", "철근누락 품질 하자",
                       "안전품질 벌점 제재"],
}
# 어느 사업 부문 렌즈에도 들어오면 안 되는 generic 노이즈. check 13.
BUSINESS_NOISE = [
    "동남아 여행 관광 항공권 수요 회복",          # 여행/관광
    "한국은행 기준금리 인하 소비심리 개선",        # generic macro/economy
    "AI 반도체주 코스닥 강세 투자자 관심",        # stock/sector hype
    "신형 SUV 자동차 연료 소비 효율 개선 출시",    # 단순 자동차 연료 소비
    "건설주 ETF 주가 강세 코스피 상승",           # ETF/주가만
    "건설사 신입사원 채용 박람회 현장 견학 이벤트",  # 채용/견학/이벤트
]
# 실행 범위 분류 예시 (라벨 → 기대 태그 id).
SCOPE_EXAMPLES = [
    ("국내 건설현장", "domestic_site"), ("고용부 특별감독", "domestic_site"),
    ("국내 현장", "domestic_site"), ("사우디 현장", "overseas_site"),
    ("카타르 프로젝트", "overseas_site"), ("해외 플랜트", "overseas_site"),
    ("중동 지사", "overseas_branch"), ("해외지사", "overseas_branch"),
    ("현지 법인", "overseas_subsidiary"), ("해외법인", "overseas_subsidiary"),
]

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


def _matches_lens(title: str, lens_id: str) -> bool:
    lens = topic_profiles.get_business_lens(lens_id)
    return lens is not None and topic_profiles.match_topic_profile(
        _article(title), lens)


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
    check("4: iter_topic_queries 기존 D5-A 수집 폭 유지 (사업 렌즈 미혼입)",
          len(queries) == EXPECTED_TOPIC_QUERY_COUNT, f"{len(queries)}건")


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
        "raw_bls=b.get('business_lens_signals')\n"
        "bls=raw_bls if isinstance(raw_bls, dict) else {}\n"
        "biz_top_keys=('civil_infrastructure_signals','building_housing_signals',\n"
        " 'plant_signals','new_energy_signals','development_business_signals',\n"
        " 'global_business_signals','safety_quality_signals','org_unit_signals',\n"
        " 'execution_scope_signals')\n"
        "out={'present':{k:(k in b) for k in keys+['topic_profile_catalog']},\n"
        " 'lens':{k:[e.get('article_id') for e in (b.get(k) or [])] for k in keys},\n"
        " 'catalog':[c.get('id') for c in (b.get('topic_profile_catalog') or [])],\n"
        " 'bl_present':{k:(k in b) for k in ['business_lens_catalog',\n"
        "   'business_lens_signals','org_unit_catalog','execution_scope_catalog']},\n"
        " 'bl_is_dict':isinstance(raw_bls, dict),\n"
        " 'bl_catalog':[c.get('id') for c in (b.get('business_lens_catalog') or [])],\n"
        " 'bl_signal_keys':sorted(bls.keys()),\n"
        " 'biz_top_keys':[k for k in b.keys() if k in biz_top_keys],\n"
        " 'bl_caps':{k:len(v) for k,v in bls.items()},\n"
        " 'bl_entries':sum(len(v) for v in bls.values()),\n"
        " 'bl_entry_has_tags':all(all(t in e for t in ('business_lens_tags',\n"
        "   'org_unit_tags','execution_scope_tags')) for v in bls.values() for e in v),\n"
        " 'org_catalog':[t.get('id') for t in (b.get('org_unit_catalog') or [])],\n"
        " 'scope_catalog':[t.get('id') for t in (b.get('execution_scope_catalog') or [])],\n"
        " 'market_keys':{k:(k in b) for k in ['market_snapshot','market_data_mode',\n"
        "   'construction_commodities_snapshot','sovereign_yields_snapshot','fx_snapshot']},\n"
        " 'market_instr':sum(len(b.get(c) or []) for c in\n"
        "   ['construction_commodities_snapshot','sovereign_yields_snapshot','fx_snapshot']),\n"
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
    check("topic_profile_catalog가 기존 D5-A 프로파일 5개로 유지됨 (사업 렌즈 미혼입)",
          cat == list(REQUIRED_PROFILES), str(cat))


def check_mock_invariant(sim: dict | None) -> None:
    if not sim:
        return
    counts = sim["counts"]
    check("mock 카운트 불변 28/19/3/6/10/9 (D5-D는 메타 키만 추가)",
          counts == EXPECTED_MOCK_COUNTS, str(counts))


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


# === P0-D5-D: 조직/사업 부문 분류 체계 (Layer 1/2/3) ========================

def check_business_lenses_exist() -> None:
    """1: 7개 임원 가시 사업 부문 렌즈가 존재하고 필드 계약을 지킨다."""
    enabled = {p.id for p in topic_profiles.get_enabled_business_lenses()}
    for lid in REQUIRED_BUSINESS_LENSES:
        lens = topic_profiles.get_business_lens(lid)
        ok = lens is not None and lens.id == lid
        check(f"1: 사업 부문 렌즈 존재 — {lid}", ok)
        if not ok:
            continue
        check(f"1: {lid} enabled", lid in enabled)
        check(f"1: {lid} label 비어있지 않음", bool(lens.label and lens.label.strip()))
        check(f"1: {lid} include∧anchor∧exclude 키워드 존재",
              len(lens.include_keywords) >= 1 and len(lens.relevance_anchors) >= 1
              and len(lens.exclude_keywords) >= 1)
        check(f"1: {lid} max_items 1~10", 1 <= int(lens.max_items) <= 10)
    # 별도 튜플이라 엔티티 프로파일 목록(topic_profile_catalog)을 오염시키지 않는다.
    entity_ids = {p.id for p in topic_profiles.all_topic_profiles()}
    overlap = set(REQUIRED_BUSINESS_LENSES) & entity_ids
    check("1: 사업 부문 렌즈가 엔티티 프로파일과 분리됨 (catalog 비오염)",
          not overlap, str(sorted(overlap)))


def check_business_lens_isolation() -> None:
    """사업 렌즈는 live query와 기존 topic_profile_catalog에 섞이지 않는다."""
    queries = topic_profiles.iter_topic_queries()
    live_query_keys = {q.strip().casefold() for q in queries}
    profile_query_keys = {
        q.strip().casefold()
        for profile in topic_profiles.get_enabled_topic_profiles()
        for q in profile.queries
    }
    business_query_keys = {
        q.strip().casefold()
        for lens in topic_profiles.all_business_lenses()
        for q in lens.queries
    }
    business_only_leaked = sorted(live_query_keys & (business_query_keys - profile_query_keys))
    check("iter_topic_queries가 enabled topic profile queries와 동일",
          live_query_keys == profile_query_keys,
          str(sorted(live_query_keys ^ profile_query_keys)))
    check("사업 렌즈 전용 queries가 iter_topic_queries/live 수집 쿼리에 섞이지 않음",
          not business_only_leaked, str(business_only_leaked))
    enabled_profile_ids = [p.id for p in topic_profiles.get_enabled_topic_profiles()]
    check("get_enabled_topic_profiles는 기존 5개 엔티티 프로파일만 반환",
          enabled_profile_ids == list(REQUIRED_PROFILES), str(enabled_profile_ids))


def check_org_scope_tags_exist() -> None:
    """2/3: Layer-2 조직 태그 7종 + Layer-3 실행범위 태그 4종이 존재한다."""
    org_ids = {t.id for t in topic_profiles.all_org_unit_tags()}
    for tid in REQUIRED_ORG_TAGS:
        check(f"2: 조직 태그 존재 — {tid}",
              tid in org_ids and topic_profiles.get_org_unit_tag(tid) is not None)
    scope_ids = {t.id for t in topic_profiles.all_execution_scope_tags()}
    for tid in REQUIRED_SCOPE_TAGS:
        check(f"3: 실행범위 태그 존재 — {tid}",
              tid in scope_ids
              and topic_profiles.get_execution_scope_tag(tid) is not None)


def check_business_accept() -> None:
    """6~12: 각 사업 부문 렌즈가 자기 도메인 케이스를 수용한다."""
    num = {lid: 6 + i for i, lid in enumerate(REQUIRED_BUSINESS_LENSES)}
    for lid, cases in BUSINESS_ACCEPT.items():
        for title in cases:
            check(f"{num[lid]}: {lid} 수용 — {title}", _matches_lens(title, lid))


def check_business_noise_rejected() -> None:
    """13: generic 노이즈는 어느 사업 부문 렌즈에도 들어오지 않는다."""
    for title in BUSINESS_NOISE:
        hits = topic_profiles.classify_business_lenses(_article(title))
        check(f"13: 사업 부문 노이즈 거부 (전 렌즈) — {title}", not hits, str(hits))


def check_scope_org_classify() -> None:
    """실행범위/조직 분류가 결정적으로 동작한다 (분류 메타 동작 확인)."""
    for title, expected in SCOPE_EXAMPLES:
        got = topic_profiles.classify_execution_scopes(_article(title))
        check(f"실행범위 분류 — {title} → {expected}", expected in got, str(got))
    check("조직 태그 분류 — 재경본부",
          "finance_accounting" in topic_profiles.classify_org_units(
              _article("재경본부 자금 조달 계획 발표")))
    check("조직 태그 분류 — GBC개발사업단",
          "gbc_development" in topic_profiles.classify_org_units(
              _article("GBC개발사업단 인허가 본격화")))
    check("조직 태그 오탐 없음 — 'API 연동' → pi 아님",
          "pi" not in topic_profiles.classify_org_units(_article("공공 API 연동 확대")))
    check("조직 태그 오탐 없음 — '감사합니다 행사' → audit 아님",
          "audit_compliance" not in topic_profiles.classify_org_units(
              _article("고객 감사합니다 사은 행사")))


def check_mapping_integrity() -> None:
    """UNIT_MAPPING이 존재하는 렌즈/조직 태그 id만 참조한다 (조직도 매핑 무결성)."""
    mapping = getattr(topic_profiles, "UNIT_MAPPING", None)
    check("UNIT_MAPPING 존재", bool(mapping))
    if not mapping:
        return
    lens_ids = {p.id for p in topic_profiles.all_business_lenses()}
    org_ids = {t.id for t in topic_profiles.all_org_unit_tags()}
    bad = [m["unit"] for m in mapping
           if any(x not in lens_ids for x in m.get("business_lenses", ()))
           or any(x not in org_ids for x in m.get("org_units", ()))]
    check("UNIT_MAPPING이 존재하는 렌즈/조직 태그만 참조", not bad, str(bad))
    gbc = [m for m in mapping if m["unit"] == "GBC개발사업단"]
    check("UNIT_MAPPING: GBC개발사업단 → development_business + gbc_development",
          bool(gbc) and "development_business" in gbc[0]["business_lenses"]
          and "gbc_development" in gbc[0]["org_units"])


def check_business_brief_keys(sim: dict | None) -> None:
    """4/5: business_lens_catalog + business_lens_signals(묶음)이 brief JSON에 존재."""
    if not sim:
        return
    for key, present in (sim.get("bl_present") or {}).items():
        check(f"4/5: 브리핑 출력에 키 존재 — {key}", present)
    check("5: business_lens_signals는 dict 구조",
          sim.get("bl_is_dict") is True, str(sim.get("bl_is_dict")))
    cat = sim.get("bl_catalog") or []
    for lid in REQUIRED_BUSINESS_LENSES:
        check(f"4: business_lens_catalog에 {lid} 포함", lid in cat)
    sig_keys = sim.get("bl_signal_keys") or []
    for lid in REQUIRED_BUSINESS_LENSES:
        check(f"5: business_lens_signals에 {lid} 키 존재", lid in sig_keys)
    caps = sim.get("bl_caps") or {}
    over = {k: v for k, v in caps.items() if v > 5}
    check("5: 각 사업 부문 렌즈 <= 5건 (max_items 상한)", not over, str(over))
    check("5: business_lens_signals 엔트리에 Layer-1/2/3 태그 부착 (>0 entries)",
          sim.get("bl_entries", 0) > 0 and sim.get("bl_entry_has_tags") is True,
          f"entries={sim.get('bl_entries')} has_tags={sim.get('bl_entry_has_tags')}")
    check("5: 부문/조직/실행범위별 별도 top-level signal 섹션 없음",
          not sim.get("biz_top_keys"), str(sim.get("biz_top_keys")))
    org_cat = sim.get("org_catalog") or []
    scope_cat = sim.get("scope_catalog") or []
    check("2: org_unit_catalog에 7개 조직 태그 노출",
          all(t in org_cat for t in REQUIRED_ORG_TAGS), str(org_cat))
    check("3: execution_scope_catalog에 4개 실행범위 태그 노출",
          all(t in scope_cat for t in REQUIRED_SCOPE_TAGS), str(scope_cat))


def check_d5b_market_layer_intact(sim: dict | None) -> None:
    """14: D5-B 시장 레이어가 본 변경(추가 키만)에 영향받지 않는다 — in-process 확인.

    D5-B 전체 검증기를 subprocess로 재귀 호출하지 않는다: verify_market_snapshot_profiles가
    이미 본 D5-A 검증기를 인접 게이트(check 13)로 호출하므로, 여기서 D5-B를 다시 호출하면
    상호 재귀로 타임아웃한다. 대신 (1) market_profiles 카탈로그 규모가 불변이고 (2) brief의
    시장 스냅샷 출력 키가 보존됐는지를 in-process로 확인해 회귀를 잡는다. D5-B 전체 통과는
    별도 검증 단계에서 독립 실행한다."""
    try:
        from app import market_profiles
        n = len(market_profiles.all_instruments())
        check("14: market_profiles 카탈로그 규모 불변 (58종: 원자재15+금리32+환율11)",
              n == 58, f"{n}종")
    except Exception as exc:  # noqa: BLE001
        check("14: market_profiles 카탈로그 로드", False, repr(exc))
    if not sim:
        return
    for key, present in (sim.get("market_keys") or {}).items():
        check(f"14: 시장 스냅샷 출력 키 보존 — {key}", present)
    check("14: 시장 지표 행이 brief에 보존됨 (additive 변경이 market 레이어 불변)",
          sim.get("market_instr", 0) >= 58, str(sim.get("market_instr")))


def check_no_ui() -> None:
    """15: UI/디자인 미구현 — 프로덕션 템플릿이 business_lens를 렌더하지 않는다."""
    tpl = ROOT / "templates" / "index.html"
    src = tpl.read_text(encoding="utf-8") if tpl.exists() else ""
    check("15: templates/index.html이 business_lens/execution_scope 미참조 (UI 미구현)",
          "business_lens" not in src and "execution_scope" not in src)


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
    # P0-D5-D 조직/사업 부문 분류 체계 (pure config — sim 불필요)
    check_business_lenses_exist()
    check_business_lens_isolation()
    check_org_scope_tags_exist()
    check_business_accept()
    check_business_noise_rejected()
    check_scope_org_classify()
    check_mapping_integrity()
    sim = _run_pipeline_sim()
    check_sections_additive(sim)
    check_mock_invariant(sim)
    check_business_brief_keys(sim)
    check_d4d_still_passes()
    check_d4e_still_passes()
    check_d5b_market_layer_intact(sim)
    check_no_ui()
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
