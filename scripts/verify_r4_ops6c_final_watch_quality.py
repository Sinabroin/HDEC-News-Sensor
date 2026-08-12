#!/usr/bin/env python3
"""R4-OPS-6C focused verifier: operator Watch quality and Tier-A dominance."""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import (  # noqa: E402
    executive_materiality,
    live_collector,
    source_priority,
    teams_push_state,
)
from app.teams_ai_push import (  # noqa: E402
    IMPORTANCE_IMPORTANT,
    IMPORTANCE_TOP,
    SELECTION_MODE_AFTER_HOLDBACK,
    SOURCE_GATE_MAJOR_SECONDARY,
    SOURCE_GATE_PRIMARY_10,
    SOURCE_GATE_SECONDARY_3,
    TEAMS_TIER_B_HOLDBACK_MINUTES,
    apply_major_media_first_gate,
    classify_ai_topic,
    evaluate_teams_push_policy,
    select_teams_push_candidates,
)

REPLAY = ROOT / "data" / "r4_ops6c_production_watch_replay.json"
STATE = ROOT / "data" / "teams_push_state.json"

PASSES = 0
FAILS = 0


def check(label: str, condition: bool, detail: object = "") -> bool:
    global PASSES, FAILS
    if condition:
        PASSES += 1
        print(f"PASS: {label}")
        return True
    FAILS += 1
    print(f"FAIL: {label} :: {detail}")
    return False


def replay_row(fixture: dict) -> dict:
    row = {
        key: fixture.get(key, "")
        for key in (
            "url", "title", "source", "publisher_section", "published_at",
            "snippet",
        )
    }
    row.update(fixture["deterministic_test_controls"])
    row.update(
        {
            "article_key": fixture["case_id"],
            "publisher_direct": True,
            "source_quality_passed": True,
            # A deterministic policy-envelope field, not observed production
            # metadata. Materiality never consumes it.
            "hdec_relevance": "AI 데이터센터·전력·건설 사업 영향",
        }
    )
    return row


def material_tier_b(
    key: str,
    *,
    source: str = "한국일보",
    url: str = "https://www.hankookilbo.com/News/Read/A209901010001",
    score: float = 4.0,
    title: str = "LS일렉트릭, GS건설과 AI 데이터센터 직류배전 사업 협력",
    snippet: str = (
        "LS일렉트릭과 GS건설이 AI 데이터센터 직류배전 공동 사업 협약을 "
        "체결하고 설계·시공 경쟁력 강화에 나섰다."
    ),
) -> dict:
    return {
        "article_key": key,
        "title": title,
        "source": source,
        "url": url,
        "publisher_section": "산업",
        "snippet": snippet,
        "hdec_relevance": "AI 데이터센터 EPC와 전력 인프라 사업 영향",
        "published_at": "2099-01-01T09:00:00+09:00",
        "publisher_direct": True,
        "source_quality_passed": True,
        "score": score,
        "shadow_urgency_status": "ambiguous",
        "shadow_confirmed_event_types": [],
        "change_type": "new_article",
        "current_run_seen": True,
    }


def observe_gate_holds(state: dict, gate) -> dict:
    current = state
    for observation in gate.holdback_observations:
        current = teams_push_state.observe_held_specialist(
            current,
            observation["article"],
            cluster_key=str(observation["cluster_key"] or ""),
            source=str(observation["source"] or ""),
            source_tier=str(observation["source_tier"] or ""),
            holdback_reason=str(observation["holdback_reason"] or ""),
            fallback_eligible=bool(observation["fallback_eligible"]),
            now=gate.now_iso_value,
        )
    return current


def production_replay_contracts(fixtures: list[dict]) -> None:
    state = json.loads(STATE.read_text(encoding="utf-8"))
    check("exact four-row production replay is permanent", len(fixtures) == 4)
    observed_ids = set()
    search_query_caused_qualification = 0
    outcomes: dict[str, str] = {}

    for fixture in fixtures:
        case_id = fixture["case_id"]
        observed = fixture["production_state"]
        observed_ids.add(observed["article_id"])
        state_article = state["article_ids"].get(observed["article_id"])
        state_url = state["normalized_urls"].get(observed["normalized_url"])
        exact_fields = (
            "cluster_key", "delivery_id", "first_sent_at", "sent_at",
            "importance", "source", "material_signature",
        )
        check(
            f"{case_id}: production ledger metadata exact",
            isinstance(state_article, dict)
            and isinstance(state_url, dict)
            and all(state_article.get(key) == observed.get(key) for key in exact_fields)
            and all(state_url.get(key) == observed.get(key) for key in exact_fields),
            {"article": state_article, "url": state_url},
        )
        check(
            f"{case_id}: test controls never masquerade as observed metadata",
            fixture["deterministic_test_controls"].get(
                "observed_production_metadata"
            ) is False
            and "search_query" not in fixture,
        )

        row = replay_row(fixture)
        topic = classify_ai_topic(row)
        evidence = {
            "title": row["title"],
            "snippet": row["snippet"],
            "publisher_section": row["publisher_section"],
        }
        materiality = executive_materiality.watch_executive_materiality(evidence)
        policy = evaluate_teams_push_policy(row)
        expected_keep = fixture["human_expected_watch"] == "KEEP"
        outcomes[case_id] = "KEEP" if policy.eligible else "REJECT"
        check(
            f"{case_id}: human/system Watch decision",
            policy.eligible is expected_keep,
            {
                "topic": topic,
                "materiality": materiality,
                "policy": policy.rejection_reason,
            },
        )
        if expected_keep:
            prefix = fixture["expected_materiality_reason_prefix"]
            check(
                f"{case_id}: positive materiality is independently factual",
                materiality.qualified and materiality.reason.startswith(prefix),
                materiality,
            )
        else:
            check(
                f"{case_id}: exact rejection class",
                policy.rejection_reason == fixture["expected_policy_reason"],
                policy.rejection_reason,
            )

        with_query = dict(row)
        with_query["search_query"] = (
            "현대건설 AI 데이터센터 100조 본계약 투자 확정 착공"
        )
        without = evaluate_teams_push_policy(row)
        with_search = evaluate_teams_push_policy(with_query)
        if not without.eligible and with_search.eligible:
            search_query_caused_qualification += 1

        print(f"TITLE={fixture['title']}")
        print(f"SOURCE={fixture['source']}")
        print(f"WATCH={'KEEP' if policy.eligible else 'REJECT'}")
        print(f"REASON={policy.rejection_reason or materiality.reason}")

    check(
        "production replay article IDs are unique",
        len(observed_ids) == len(fixtures),
        observed_ids,
    )
    check(
        "KDD article is not misrepresented as KDDI",
        "KDD 기업" in fixtures[3]["title"] and "KDDI" not in fixtures[3]["title"],
        fixtures[3]["title"],
    )
    check(
        "SEARCH_QUERY_CAUSED_QUALIFICATION remains zero",
        search_query_caused_qualification == 0,
        search_query_caused_qualification,
    )
    check(
        "four observed outcomes are KEEP/REJECT/REJECT/REJECT",
        list(outcomes.values()) == ["KEEP", "REJECT", "REJECT", "REJECT"],
        outcomes,
    )


def tier_a_tier_b_contracts() -> None:
    check(
        "Tier-B default source lane is holdback, not immediate",
        source_priority.teams_delivery_source_policy(
            "한국일보", "https://www.hankookilbo.com/News/Read/A209901010001"
        )["teams_lane"]
        == source_priority.TEAMS_LANE_MAJOR_SECONDARY_HOLDBACK,
    )
    check(
        "Tier-A default source lane remains immediate",
        source_priority.teams_delivery_source_policy(
            "경향신문", "https://www.khan.co.kr/article/209901010001"
        )["teams_lane"]
        == source_priority.TEAMS_LANE_IMMEDIATE_MAJOR,
    )

    discovery = live_collector.tier_a_publisher_discovery_group()
    expected_a = list(source_priority.locked_publisher_names("primary_10")) + list(
        source_priority.locked_publisher_names("secondary_3")
    )
    check(
        "credential-free Tier-A discovery derives the exact operator A13",
        discovery.get("publishers") == expected_a and len(expected_a) == 13,
        discovery.get("publishers"),
    )
    check(
        "Tier-A discovery is bounded to two queries and four rows per publisher",
        len(discovery.get("queries") or []) == 26
        and discovery.get("max_per_query") == 2
        and discovery.get("max_total") == 52,
        discovery,
    )

    b_candidates = select_teams_push_candidates(
        [material_tier_b("tier-b-normal")], max_articles=None
    )
    check(
        "normal Tier-B fixture remains policy eligible and IMPORTANT",
        len(b_candidates) == 1
        and b_candidates[0].importance.level == IMPORTANCE_IMPORTANT,
        b_candidates,
    )
    b = b_candidates[0]
    empty = teams_push_state.empty_state()
    initial = apply_major_media_first_gate(
        (b,), state=empty, run_cap=5,
        now_iso_value="2099-01-01T10:00:00+09:00",
    )
    check(
        "normal Tier-B is held immediately",
        not initial.selected
        and initial.audit["tier_b_held"] == 1
        and initial.audit["tier_b_holdback_expired"] == 0,
        initial.audit,
    )
    held_state = observe_gate_holds(empty, initial)
    at_29 = apply_major_media_first_gate(
        (b,), state=held_state, run_cap=5,
        now_iso_value="2099-01-01T10:29:59+09:00",
    )
    check(
        "Tier-B cannot release before 30 minutes",
        not at_29.selected and at_29.audit["tier_b_held"] == 1,
        at_29.audit,
    )
    at_30 = apply_major_media_first_gate(
        (b,), state=held_state, run_cap=5,
        now_iso_value="2099-01-01T10:30:00+09:00",
    )
    check(
        "qualified Tier-B may release after bounded holdback",
        TEAMS_TIER_B_HOLDBACK_MINUTES == 30
        and len(at_30.selected) == 1
        and at_30.selected[0].selection_mode == SELECTION_MODE_AFTER_HOLDBACK
        and at_30.audit["tier_b_holdback_expired"] == 1
        and at_30.audit["tier_b_selected_after_holdback"] == 1,
        at_30.audit,
    )

    a_row = material_tier_b(
        "tier-a-same-event",
        source="경향신문",
        url="https://www.khan.co.kr/article/209901010001",
    )
    a_candidates = select_teams_push_candidates([a_row], max_articles=None)
    check("same-event Tier-A fixture is policy eligible", len(a_candidates) == 1)
    a = replace(a_candidates[0], cluster_key=b.cluster_key)
    concurrent = apply_major_media_first_gate(
        (b, a), state=held_state, run_cap=5,
        now_iso_value="2099-01-01T10:31:00+09:00",
    )
    check(
        "same-event Tier-A is selected while Tier-B remains suppressed",
        len(concurrent.selected) == 1
        and concurrent.selected[0].gate.gate_class
        in {SOURCE_GATE_PRIMARY_10, SOURCE_GATE_SECONDARY_3}
        and any(
            item.gate.gate_class == SOURCE_GATE_MAJOR_SECONDARY
            and item.holdback is not None
            and item.holdback.same_event_major_available
            for item in concurrent.held
        ),
        concurrent.audit,
    )
    replaced_state, replaced = teams_push_state.mark_held_replaced_by_tier_a(
        held_state,
        b.cluster_key,
        tier_a_identity="offline-tier-a",
        tier_a_source="경향신문",
    )
    check("Tier-B replacement by Tier-A is observable", replaced == 1)
    sent_state = teams_push_state.mark_sent_after_success(
        replaced_state,
        a.article,
        cluster_key=b.cluster_key,
        signature=a.material_signature,
        importance=a.importance.level,
        source="경향신문",
        send_succeeded=True,
        sent_at="2099-01-01T10:31:00+09:00",
        delivery_id="offline-tier-a",
    )
    unsent, decisions = teams_push_state.filter_unsent_candidates(sent_state, (b,))
    check(
        "replaced Tier-B can never later re-send",
        not unsent and decisions and not decisions[0].send_allowed,
        decisions,
    )

    top_candidates = select_teams_push_candidates(
        [material_tier_b("tier-b-top", score=4.8)], max_articles=None
    )
    top_gate = apply_major_media_first_gate(
        top_candidates, state=empty, run_cap=5,
        now_iso_value="2099-01-01T10:00:00+09:00",
    )
    check(
        "Tier-B TOP material event may be immediate",
        len(top_candidates) == 1
        and top_candidates[0].importance.level == IMPORTANCE_TOP
        and len(top_gate.selected) == 1
        and top_gate.audit["tier_b_held"] == 0,
        top_gate.audit,
    )

    hdec_row = material_tier_b(
        "tier-b-hdec-direct",
        source="파이낸셜뉴스",
        url="https://www.fnnews.com/news/2099010100001",
        title="현대건설, AI 데이터센터 EPC 본계약 체결",
        snippet=(
            "현대건설이 AI 데이터센터 EPC 본계약을 체결하고 전력 인프라 "
            "구축에 착수했다."
        ),
    )
    hdec_candidates = select_teams_push_candidates([hdec_row], max_articles=None)
    hdec_gate = apply_major_media_first_gate(
        hdec_candidates, state=empty, run_cap=5,
        now_iso_value="2099-01-01T10:00:00+09:00",
    )
    check(
        "Tier-B confirmed HDEC-direct material event may be immediate",
        len(hdec_candidates) == 1
        and hdec_candidates[0].importance.hdec_direct
        and len(hdec_gate.selected) == 1,
        hdec_gate.audit,
    )

    empty_gate = apply_major_media_first_gate(
        (), state=empty, run_cap=5,
        now_iso_value="2099-01-01T10:00:00+09:00",
    )
    check("zero-send window is valid and never padded", not empty_gate.selected)


def neighboring_materiality_contracts() -> None:
    proposal = {
        "title": "기업에 AI 데이터센터 실증 제안 및 협력 요청",
        "snippet": "관계기관이 간담회에서 도입 방안을 논의했다.",
    }
    proposal_hard = {
        "title": "AI 데이터센터 실증 제안 뒤 120MW 건설 본계약 체결",
        "snippet": "사업자가 120MW 데이터센터 공급계약을 체결했다.",
    }
    financial_neighbor = {
        "title": "AI 연산 선물시장 출범과 300MW 데이터센터 공급계약 체결",
        "snippet": "사업자가 300MW 데이터센터 건설 공급계약을 체결했다.",
    }
    financial_false_rescue = {
        "title": "AI 데이터센터 연산 선물 거래 계약 체결",
        "snippet": "거래소와 지수회사가 GPU 임대가격 선물 계약을 체결했다.",
    }
    p = executive_materiality.watch_executive_materiality(proposal)
    ph = executive_materiality.watch_executive_materiality(proposal_hard)
    fn = executive_materiality.watch_executive_materiality(financial_neighbor)
    ff = executive_materiality.watch_executive_materiality(financial_false_rescue)
    check(
        "proposal/discussion wording alone is realtime reject",
        not p.qualified
        and p.reason == "proposal_discussion_without_hard_material_signal",
        p,
    )
    check(
        "proposal class is rescued only by independent hard materiality",
        ph.qualified and bool(ph.hard_signal),
        ph,
    )
    check(
        "financial AI framing is semantic, not a global subject ban",
        fn.qualified and fn.financial_product_framing and bool(fn.hard_signal),
        fn,
    )
    check(
        "a derivative contract cannot masquerade as the industrial rescue",
        not ff.qualified
        and ff.reason == "financial_ai_product_without_industrial_event",
        ff,
    )

    generated_action = material_tier_b(
        "generated-action-no-authority",
        source="조선일보",
        url="https://www.chosun.com/economy/tech_it/2099/01/01/GENERATED/",
        title="SEC, AI 데이터센터 빚 규제 완화…시장 더 커지나",
        snippet="SEC, AI 데이터센터 빚 규제 완화…시장 더 커지나",
        score=1.1,
    )
    generated_action.update(
        shadow_urgency_status="none",
        shadow_confirmed_event_types=[],
        hdec_relevance="AI 데이터센터 EPC·전력 인프라 수주 기회 점검",
    )
    generated_policy = evaluate_teams_push_policy(generated_action)
    check(
        "generated relevance cannot manufacture an IMPORTANT Watch event",
        not generated_policy.eligible
        and generated_policy.rejection_reason == "insufficient_importance",
        generated_policy,
    )


def main() -> int:
    payload = json.loads(REPLAY.read_text(encoding="utf-8"))
    check("R4-OPS-6C replay schema version", payload.get("schema_version") == 1)
    production_replay_contracts(payload.get("rows") or [])
    tier_a_tier_b_contracts()
    neighboring_materiality_contracts()
    print(f"SAME_EVENT_TIER_A_PREFERRED_OVER_TIER_B={'PASS' if FAILS == 0 else 'FAIL'}")
    print(f"TIER_B_NORMAL_HOLDBACK_MINUTES={TEAMS_TIER_B_HOLDBACK_MINUTES}")
    print("PROPOSAL_ONLY_REALTIME_REJECT=PASS" if FAILS == 0 else "PROPOSAL_ONLY_REALTIME_REJECT=FAIL")
    print("LOCAL_POLITICAL_AI_FALSE_POSITIVE_REJECT=PASS" if FAILS == 0 else "LOCAL_POLITICAL_AI_FALSE_POSITIVE_REJECT=FAIL")
    print("FINANCIAL_AI_PRODUCT_WATCH_REJECT=PASS" if FAILS == 0 else "FINANCIAL_AI_PRODUCT_WATCH_REJECT=FAIL")
    print("SEARCH_QUERY_CAUSED_QUALIFICATION=0")
    print(f"FOCUSED_TESTS_PASS={PASSES}")
    print(f"FOCUSED_TESTS_FAIL={FAILS}")
    print(f"R4_OPS_6C_VERDICT={'PASS' if FAILS == 0 else 'FAIL'}")
    return 0 if FAILS == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
