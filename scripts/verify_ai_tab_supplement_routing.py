"""D4-E verifier — AI-tab supplement routing.

When the executive AI tab is under-filled (<3 items), high-quality AI/DC/smart-
construction stories already surfaced in OTHER executive sections (immediate,
HDEC-direct, order/overseas, new issues) supplement it to a stable 3–5 range —
WITHOUT reintroducing mixed-business noise, generic nuclear, risk-safety items,
or duplicate robot stories.

Deterministic, fully offline. No DB, no network, no Telegram. Tests the surface
contract (surface_contracts.decide_ai_supplement) and the routing helper
(briefing._supplement_ai_tab) directly with constructed entries.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import briefing, surface_contracts  # noqa: E402

BRIEFING_SRC = (ROOT / "app" / "briefing.py").read_text(encoding="utf-8")
REPORT_SRC = (ROOT / "scripts" / "build_static_report.py").read_text(encoding="utf-8")

_failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    line = f"[{'PASS' if ok else 'FAIL'}] {name}"
    if detail:
        line += f" — {detail}"
    print(line)
    if not ok:
        _failures.append(name)
    return ok


def _entry(aid: str, title: str) -> dict:
    """Build a realistic top-surface entry (cluster key derived as in briefing)."""
    return {
        "article_id": aid,
        "title": title,
        "exposure_cluster_key": briefing._exposure_cluster_key({"title": title}),
        "rank": 0,
    }


# ---- representative title classes (pattern-based, not overfit to one day) ----
T_HDEC_PATENT = "데이터센터 누수 잡는 AI 현대엔지니어링 설비운영 기술 특허 출원"   # tier 1
T_DC_CONTRACT = "건설사 AI 데이터센터 수주전 가속 DL건설 부천 본계약 체결"        # tier 2
T_DC_SPECIAL = "대우건설 데이터센터 속도 전담 TF 특별법 수혜 기대"               # tier 2
T_ROBOT_A = "대동로보틱스 GS건설 AI 필드로봇 활용 건설현장 자동화 맞손"          # tier 3
T_ROBOT_B = "대동로보틱스 GS건설 AI 필드로봇으로 건설현장 자동화 나선다"         # tier 3 (dup of A)
T_MIXED = "현대건설 도시정비 12조 데이터센터 양 축 강화"                          # reject
T_NUCLEAR = "신규 원전 부지 선정 원전산업 재도약의 기회돼야"                      # reject
T_RISK_SAFETY = "AI로 건설현장 산재 예방 사후 대응서 위험 예측으로 전환"          # reject (not AI-primary)
T_GENERIC_AI = "AI 반도체 투자 사이클 기대"                                       # reject
T_BASE_1 = "AI 영상인식 건설현장 안전관리 자동화 솔루션 도입"
T_BASE_2 = "BIM 디지털트윈 시공 자동화 플랫폼 본격 가동"
T_BASE_3 = "건설 AI 데이터 표준화 협의체 출범"


def check_supplement_contract() -> None:
    """surface_contracts.decide_ai_supplement: eligibility + tier + rejects."""
    def dec(title):
        return surface_contracts.decide_ai_supplement({"title": title})

    for title, tier in ((T_HDEC_PATENT, 1), (T_DC_CONTRACT, 2),
                        (T_DC_SPECIAL, 2), (T_ROBOT_A, 3), (T_ROBOT_B, 3)):
        d = dec(title)
        check(f"contract: eligible tier{tier} — {title[:24]}…",
              d.eligible and d.tier == tier
              and d.reason_code == f"ai_tab.supplement.accept.tier{tier}",
              f"eligible={d.eligible} tier={d.tier} rc={d.reason_code}")

    # tier ordering: HDEC > DC-order > robot
    check("contract: tier order HDEC(1) < DC-order(2) < robot(3)",
          dec(T_HDEC_PATENT).tier < dec(T_DC_CONTRACT).tier < dec(T_ROBOT_A).tier)

    rejects = {
        "mixed business/redevelopment (D4-D)": T_MIXED,
        "generic nuclear (원전 only)": T_NUCLEAR,
        "risk-safety / 산재 (not AI-primary)": T_RISK_SAFETY,
        "generic AI semiconductor": T_GENERIC_AI,
        "career/event (취업 기회)": "비석유 5대 전략 산업이 여는 2026 UAE 취업 기회",
        "stock hype (삼전/사세요)": "삼전도 없고 하닉도 없다 근데 너무 올랐다 이것 사세요",
    }
    for label, title in rejects.items():
        d = dec(title)
        check(f"contract: reject {label}",
              not d.eligible and d.reason_code.startswith("ai_tab.supplement.reject"),
              f"eligible={d.eligible} rc={d.reason_code}")

    # generic nuclear must not be rescued merely by 원전/SMR — only a DC/AI hook does
    check("contract: 원전 alone NOT eligible, but 데이터센터+원전 IS",
          not dec("폴란드 원전 EPC 수주 유력").eligible
          and dec("AI 데이터센터 전력 확보용 SMR 원전 연계 검토").eligible)


def check_near_dup() -> None:
    """_supplement_title_overlap: duplicate robot pair vs distinct DC stories."""
    norm = briefing._normalize_exposure_text
    ratio = briefing.AI_TAB_SUPPLEMENT_NEAR_DUP_RATIO
    dup = briefing._supplement_title_overlap(norm(T_ROBOT_A), norm(T_ROBOT_B))
    distinct = briefing._supplement_title_overlap(norm(T_DC_CONTRACT), norm(T_HDEC_PATENT))
    check("near-dup: robot pair overlap >= threshold", dup >= ratio, f"{dup:.2f} >= {ratio}")
    check("near-dup: distinct DC stories overlap < threshold",
          distinct < ratio, f"{distinct:.2f} < {ratio}")


def _supp(ai_entries, surfaces):
    return briefing._supplement_ai_tab(ai_entries, surfaces)


def _ids(entries):
    return [e.get("article_id") for e in entries]


def check_fill_tier_and_reject() -> None:
    """Scenario A: under-filled tab fills with best-tier AI/DC; junk excluded."""
    base = []
    surfaces = [
        [_entry("dc_special", T_DC_SPECIAL)],                       # immediate
        [_entry("hdec_patent", T_HDEC_PATENT), _entry("mixed", T_MIXED)],  # hdec
        [_entry("dc_contract", T_DC_CONTRACT), _entry("robot_a", T_ROBOT_A),
         _entry("robot_b", T_ROBOT_B), _entry("nuclear", T_NUCLEAR)],  # business
        [],                                                          # new issues
    ]
    out = _supp(base, surfaces)
    ids = _ids(out)
    check("A: fills to at least 3", len(out) >= 3, f"ids={ids}")
    check("A: no more than 5", len(out) <= 5, f"len={len(out)}")
    check("A: tier-1 HDEC + tier-2 DC chosen over tier-3 robot",
          {"hdec_patent", "dc_special", "dc_contract"} <= set(ids), f"ids={ids}")
    check("A: mixed redevelopment/DC NOT added", "mixed" not in ids, f"ids={ids}")
    check("A: generic nuclear NOT added", "nuclear" not in ids, f"ids={ids}")
    check("A: robots NOT added while higher tiers fill the floor",
          not ({"robot_a", "robot_b"} & set(ids)), f"ids={ids}")
    check("A: every supplemented entry is flagged ai_supplement",
          all(e.get("ai_supplement") for e in out), f"flags={[e.get('ai_supplement') for e in out]}")


def check_robot_dedup_in_pipeline() -> None:
    """Scenario B: robots are the only low-tier option; the dup is dropped."""
    base = []
    surfaces = [[], [], [
        _entry("robot_a", T_ROBOT_A), _entry("robot_b", T_ROBOT_B),
        _entry("dc_contract", T_DC_CONTRACT), _entry("mixed", T_MIXED),
        _entry("nuclear", T_NUCLEAR)], []]
    out = _supp(base, surfaces)
    ids = _ids(out)
    robot_count = len({"robot_a", "robot_b"} & set(ids))
    check("B: duplicate field-robot pair contributes at most one",
          robot_count <= 1, f"robot_count={robot_count} ids={ids}")
    check("B: the dropped robot is the near-duplicate (one robot present)",
          robot_count == 1, f"ids={ids}")
    check("B: DC contract still added", "dc_contract" in ids, f"ids={ids}")
    check("B: mixed/nuclear excluded", not ({"mixed", "nuclear"} & set(ids)), f"ids={ids}")


def check_existing_items_preserved() -> None:
    """Scenario G: a 2-item tab keeps both items and fills to 3."""
    base = [_entry("base1", T_BASE_1), _entry("base2", T_BASE_2)]
    surfaces = [[], [_entry("hdec_patent", T_HDEC_PATENT)],
                [_entry("dc_contract", T_DC_CONTRACT), _entry("robot_a", T_ROBOT_A)], []]
    out = _supp(base, surfaces)
    ids = _ids(out)
    check("G: existing two AI items remain", {"base1", "base2"} <= set(ids), f"ids={ids}")
    check("G: tab reaches 3", len(out) == 3, f"ids={ids}")
    check("G: base items keep their original entries (not flagged supplement)",
          out[0] is base[0] and out[1] is base[1]
          and not out[0].get("ai_supplement") and not out[1].get("ai_supplement"))
    check("G: the supplement is the tier-1 HDEC item",
          out[2].get("article_id") == "hdec_patent" and out[2].get("ai_supplement"),
          f"third={out[2].get('article_id')}")


def check_no_junk_padding() -> None:
    """Scenario C: an under-filled tab with no eligible candidate stays as-is."""
    base = [_entry("base1", T_BASE_1), _entry("base2", T_BASE_2)]
    surfaces = [[], [_entry("mixed", T_MIXED)],
                [_entry("nuclear", T_NUCLEAR), _entry("risk", T_RISK_SAFETY)], []]
    out = _supp(base, surfaces)
    check("C: no eligible candidate → tab is not padded with junk",
          _ids(out) == ["base1", "base2"], f"ids={_ids(out)}")


def check_d3u_all_ineligible_stays_empty() -> None:
    """Scenario D: an empty tab with only ineligible items stays empty (D3U)."""
    surfaces = [[], [_entry("mixed", T_MIXED)],
                [_entry("nuclear", T_NUCLEAR), _entry("risk", T_RISK_SAFETY),
                 _entry("generic", T_GENERIC_AI)], []]
    out = _supp([], surfaces)
    check("D: only-ineligible candidates → AI tab stays empty",
          out == [], f"ids={_ids(out)}")


def check_gated_off_when_filled() -> None:
    """Scenario E: a tab already at the target is never supplemented."""
    base = [_entry("b1", T_BASE_1), _entry("b2", T_BASE_2), _entry("b3", T_BASE_3)]
    surfaces = [[], [_entry("hdec_patent", T_HDEC_PATENT)],
                [_entry("dc_contract", T_DC_CONTRACT)], []]
    out = _supp(base, surfaces)
    check("E: base >= 3 → returned unchanged (gated off)",
          out is base and len(out) == 3
          and not any(e.get("ai_supplement") for e in out))


def check_risk_not_a_candidate_source() -> None:
    """Static: the briefing call site supplements from immediate/HDEC/order/new
    only — risk_regulation, competitor and macro are NOT candidate sources."""
    # locate the _supplement_ai_tab call argument list in briefing.py
    idx = BRIEFING_SRC.find("ai_radar_signals = _supplement_ai_tab(")
    check("risk: briefing calls _supplement_ai_tab", idx != -1)
    if idx == -1:
        return
    call = BRIEFING_SRC[idx:idx + 400]
    for src in ("top_immediate", "hdec_direct_signals", "business_signals", "top_issues"):
        check(f"risk: candidate source includes {src}", src in call)
    for banned in ("risk_regulation_signals", "competitor_supply_signals",
                   "macro_economy_signals"):
        check(f"risk: {banned} is NOT a candidate source (avoided by default)",
              banned not in call)


def check_no_executive_label_leak() -> None:
    """The internal ai_supplement marker must not surface as an executive label."""
    check("leak: report renderer never references ai_supplement",
          "ai_supplement" not in REPORT_SRC)


def main() -> int:
    print(f"== verify_ai_tab_supplement_routing @ {ROOT} ==")
    check_supplement_contract()
    check_near_dup()
    check_fill_tier_and_reject()
    check_robot_dedup_in_pipeline()
    check_existing_items_preserved()
    check_no_junk_padding()
    check_d3u_all_ineligible_stays_empty()
    check_gated_off_when_filled()
    check_risk_not_a_candidate_source()
    check_no_executive_label_leak()
    if _failures:
        print(f"\nRESULT: FAIL ({len(_failures)} failed)")
        for name in _failures:
            print(f"  - {name}")
        return 1
    print("\nRESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
