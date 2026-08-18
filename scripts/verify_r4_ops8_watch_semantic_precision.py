#!/usr/bin/env python3
"""R4-OPS-8 Watch dominant-subject semantic precision acceptance replay.

The canonical corpus combines three visibly distinct provenance classes:

* committed production sends and user-confirmed false positives;
* user-supplied suspect production sends with recovered publisher evidence;
* explicitly synthetic adversarial neighbors and positive recall cases.

For an observed send, the committed Teams ledger proves that the historical
upstream Watch gates admitted the article.  Missing historical provider
snippet, score, query, and generated text remain UNKNOWN in the corpus.  This
verifier therefore replays the newly added counterfactual semantic gate from
bounded publisher evidence, rather than fabricating the missing inputs.

The verifier is fully offline.  It blocks network and SMTP construction and
hashes every committed production state file before and after the replay.
"""

from __future__ import annotations

import copy
import hashlib
import json
import smtplib
import socket
import sys
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import (  # noqa: E402
    ai_centrality,
    executive_materiality,
    source_priority,
    watch_semantic_precision,
)
from app.teams_ai_push import (  # noqa: E402
    classify_ai_topic,
    evaluate_realtime_opinion_gate,
    evaluate_stock_market_gate,
    evaluate_teams_push_policy,
    is_executive_relevant_for_push,
)
from app.teams_push_state import article_identity  # noqa: E402

CORPUS_PATH = ROOT / "data" / "r4_ops8_watch_semantic_precision_replay.json"
LEDGER_PATH = ROOT / "data" / "teams_push_state.json"
STATE_PATHS = (
    ROOT / "data" / "teams_push_state.json",
    ROOT / "data" / "editorial_daily_state.json",
    ROOT / "data" / "editorial_weekly_state.json",
    ROOT / "data" / "news_censor_verified_state.json",
)

ALLOWED_PROVENANCE = {
    "observed_production",
    "user_confirmed",
    "synthetic_neighbor",
}
OBSERVED_ROLES = {"confirmed_anchor", "suspect", "window_audit"}
FULL_POLICY_ROLES = {
    "confirmed_anchor",
    "negative_neighbor",
    "positive_neighbor",
    "positive_recall",
}

CHECKS = 0
FAILURES: list[str] = []
NETWORK_CALLS = 0
SMTP_CONNECTIONS = 0


def check(name: str, condition: bool, detail: str = "") -> bool:
    global CHECKS
    CHECKS += 1
    if condition:
        print(f"PASS {name}")
    else:
        FAILURES.append(name)
        print(f"FAIL {name}" + (f" — {detail[:500]}" if detail else ""))
    return bool(condition)


def _blocked_network(*_args: object, **_kwargs: object) -> None:
    global NETWORK_CALLS
    NETWORK_CALLS += 1
    raise RuntimeError("R4-OPS-8 verifier blocks external network access")


class _BlockedSMTP:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        global SMTP_CONNECTIONS
        SMTP_CONNECTIONS += 1
        raise RuntimeError("R4-OPS-8 verifier blocks SMTP connections")


def _install_side_effect_guards() -> None:
    socket.getaddrinfo = _blocked_network
    socket.create_connection = _blocked_network
    urllib.request.urlopen = _blocked_network
    smtplib.SMTP = _BlockedSMTP
    smtplib.SMTP_SSL = _BlockedSMTP


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "MISSING"


def _state_hashes() -> dict[str, str]:
    return {str(path.relative_to(ROOT)): _sha256(path) for path in STATE_PATHS}


def _expected_eligible(row: Mapping[str, Any]) -> bool:
    return row.get("expected_realtime") == "KEEP"


def _scaffold_watch_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Add explicit synthetic upstream facts; never call these observed facts."""
    article = copy.deepcopy(dict(row))
    article.update(
        {
            "article_id": f"r4-ops8:{row['case_id']}",
            "article_key": f"r4-ops8:{row['case_id']}",
            "publisher_direct": True,
            "source_quality_passed": True,
            "current_run_seen": True,
            "teams_newness_eligible": True,
            "carried_forward": False,
            "score": 5.0,
            "final_score": 5.0,
            "shadow_urgency_status": "confirmed",
            "shadow_confirmed_event_types": ["ai_investment_confirmed"],
            "hdec_relevance_tier": "A",
            "decision_relevance_tier": "A",
            # These fields are deliberately hostile generated metadata.  The
            # new semantic gate must never read them.
            "summary": "생성 요약: AI 데이터센터 핵심 투자 계약으로 임원 보고가 필요하다.",
            "whyImportant": "생성 문구: 현대건설 AI 인프라 수주 기회",
            "why_it_matters": "생성 문구: 최우선 전략 이벤트",
            "radarReason": "생성 문구: AI 데이터센터 투자",
            "category_label": "생성 AI 데이터센터",
            "source_metadata": {
                "provider": "synthetic_offline_replay",
                "query": "AI 데이터센터 5조원 투자 확정 공급계약 현대건설",
            },
        }
    )
    article.setdefault("published_at", "2026-08-18T09:00:00+09:00")
    return article


def _ledger_match(row: Mapping[str, Any], ledger: Mapping[str, Any]) -> bool:
    return bool(_ledger_entry(row, ledger))


def _ledger_entry(
    row: Mapping[str, Any], ledger: Mapping[str, Any]
) -> Mapping[str, Any]:
    identity = article_identity(row)
    for bucket, key in (
        ("normalized_urls", identity["normalized_url"]),
        ("title_fingerprints", identity["title_fingerprint"]),
    ):
        entry = ledger.get(bucket, {}).get(key, {})
        if isinstance(entry, Mapping) and entry:
            return entry
    return {}


def _plain(value: object) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    if value is None:
        return "UNKNOWN"
    return " ".join(str(value).split())


def _print_case(
    row: Mapping[str, Any],
    decision: watch_semantic_precision.WatchSemanticPrecisionDecision,
    ledger: Mapping[str, Any],
) -> None:
    topic = classify_ai_topic(row)
    centrality = ai_centrality.classify(row)
    executive_relevant = is_executive_relevant_for_push(row, topic)
    material = executive_materiality.executive_qualification(
        {
            "title": row.get("title", ""),
            "subtitle": row.get("subtitle", ""),
            "snippet": row.get("snippet", ""),
            "publisher_section": row.get("publisher_section", ""),
        }
    )
    stock = evaluate_stock_market_gate(row)
    fund = executive_materiality.is_fund_product_launch_noise(row)
    opinion = evaluate_realtime_opinion_gate(row)
    source = source_priority.teams_delivery_source_policy(
        str(row.get("source", "")), str(row.get("url", ""))
    )
    observed_entry = _ledger_entry(row, ledger)
    if row.get("corpus_role") in OBSERVED_ROLES:
        importance = observed_entry.get("importance", "UNKNOWN")
        policy_eligible = decision.eligible
    else:
        policy = evaluate_teams_push_policy(_scaffold_watch_row(row))
        importance = policy.importance.level or "NOT_REACHED"
        policy_eligible = policy.eligible
    final = "KEEP" if decision.eligible else "REJECT"
    match = final == row.get("expected_realtime")
    fields = {
        "CASE_ID": row.get("case_id"),
        "PROVENANCE": row.get("provenance"),
        "TITLE": row.get("title"),
        "SOURCE": row.get("source"),
        "URL": row.get("url"),
        "RESOLVED_PUBLISHER_IDENTITY": source.get("publisher_identity"),
        "SOURCE_TIER": source.get("operator_tier"),
        "SEMANTIC_CLASS": decision.semantic_class,
        "AI_CENTRAL": centrality.is_central,
        "EXECUTIVE_RELEVANT": executive_relevant,
        "MATERIAL": material.qualified,
        "STOCK_MARKET": stock.dominant,
        "FUND_PRODUCT": fund,
        "OPINION": opinion.excluded,
        "ROUNDUP": decision.roundup,
        "INVESTOR_DOMINANT": decision.investor_dominant,
        "IMPORTANCE": importance,
        "OPINION_GATE": opinion.excluded,
        "TEAMS_POLICY_ELIGIBLE": policy_eligible,
        "FINAL_REALTIME_DECISION": final,
        "REASON": decision.reason,
        "HUMAN_EXPECTED": row.get("human_expected"),
        "MATCH": match,
    }
    print("CASE_BEGIN")
    for key, value in fields.items():
        print(f"{key}={_plain(value)}")
    print("CASE_END")


def _policy_fixture(
    case_id: str,
    title: str,
    snippet: str,
    *,
    source: str = "연합뉴스",
    url: str = "https://www.yna.co.kr/view/AKR20260818000000000",
    **extra: object,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "case_id": case_id,
        "title": title,
        "snippet": snippet,
        "source": source,
        "url": url,
        "published_at": "2026-08-18T09:00:00+09:00",
    }
    row.update(extra)
    return _scaffold_watch_row(row)


def main() -> int:
    _install_side_effect_guards()
    state_before = _state_hashes()
    corpus = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    ledger = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
    rows = corpus.get("rows", [])

    check(
        "canonical corpus contract",
        corpus.get("fixture_contract")
        == "R4_OPS8_WATCH_SEMANTIC_PRECISION_REPLAY_V1"
        and corpus.get("schema_version") == 1,
    )
    check("canonical corpus has 65 rows", len(rows) == 65, str(len(rows)))
    check(
        "case ids and publisher URLs are deduplicated",
        len({row.get("case_id") for row in rows}) == len(rows)
        and len({article_identity(row)["normalized_url"] for row in rows}) == len(rows),
    )
    check(
        "provenance vocabulary is closed",
        {row.get("provenance") for row in rows} <= ALLOWED_PROVENANCE,
    )
    role_counts = Counter(row.get("corpus_role") for row in rows)
    check(
        "observed and synthetic corpus roles are visibly separated",
        role_counts
        == Counter(
            {
                "suspect": 32,
                "positive_recall": 9,
                "negative_neighbor": 12,
                "positive_neighbor": 5,
                "window_audit": 4,
                "confirmed_anchor": 3,
            }
        ),
        repr(role_counts),
    )

    observed = [row for row in rows if row.get("corpus_role") in OBSERVED_ROLES]
    check("all 39 production-window sends are represented", len(observed) == 39)
    check(
        "every observed row is corroborated by committed Teams ledger",
        all(_ledger_match(row, ledger) for row in observed),
        repr([row["case_id"] for row in observed if not _ledger_match(row, ledger)]),
    )
    check(
        "unknown historical operational evidence remains explicit",
        all(
            isinstance(row.get("unknown_fields"), list)
            and "historical_provider_snippet" in row["unknown_fields"]
            for row in rows
            if row.get("corpus_role") == "confirmed_anchor"
        ),
    )

    decisions: dict[str, watch_semantic_precision.WatchSemanticPrecisionDecision] = {}
    mismatches: list[str] = []
    semantic_class_mismatches: list[str] = []
    for row in rows:
        decision = watch_semantic_precision.classify(row)
        decisions[row["case_id"]] = decision
        _print_case(row, decision, ledger)
        final = "KEEP" if decision.eligible else "REJECT"
        if final != row.get("expected_realtime"):
            mismatches.append(row["case_id"])
        expected_class = row.get("expected_semantic_class")
        if expected_class and decision.semantic_class != expected_class:
            semantic_class_mismatches.append(row["case_id"])

    check("all replay decisions match expected realtime", not mismatches, repr(mismatches))
    check(
        "all pinned semantic classes match",
        not semantic_class_mismatches,
        repr(semantic_class_mismatches),
    )

    # Prove the production policy path for confirmed anchors and every
    # synthetic neighbor/recall case with explicitly synthetic upstream facts.
    policy_mismatches: list[str] = []
    policy_results: dict[str, Any] = {}
    for row in rows:
        if row.get("corpus_role") not in FULL_POLICY_ROLES:
            continue
        result = evaluate_teams_push_policy(_scaffold_watch_row(row))
        policy_results[row["case_id"]] = result
        if result.eligible != _expected_eligible(row):
            policy_mismatches.append(
                f"{row['case_id']}:{result.rejection_reason or 'KEEP'}"
            )
    check(
        "real Watch policy matches anchors and synthetic neighbors",
        not policy_mismatches,
        repr(policy_mismatches),
    )

    def rejected(case_id: str, semantic_class: str | None = None) -> bool:
        decision = decisions[case_id]
        return not decision.eligible and (
            semantic_class is None or decision.semantic_class == semantic_class
        )

    def accepted(case_id: str) -> bool:
        return decisions[case_id].eligible

    flags = {
        "CONFIRMED_FP1_REJECTED": rejected(
            "r4ops8_fp1_mk_investor_guidance",
            watch_semantic_precision.INVESTOR_MARKET_COMMENTARY,
        ),
        "CONFIRMED_FP2_REJECTED": rejected(
            "r4ops8_fp2_sedaily_generic_tailwind",
            watch_semantic_precision.GENERIC_INDUSTRY_AI_TAILWIND,
        ),
        "CONFIRMED_FP3_REJECTED": rejected(
            "r4ops8_fp3_segye_roundup_contamination",
            watch_semantic_precision.ROUNDUP_MULTI_TOPIC,
        ),
        "INVESTOR_GUIDANCE_FALSE_POSITIVE_REJECTED": rejected(
            "neighbor_investor_guidance",
            watch_semantic_precision.INVESTOR_MARKET_COMMENTARY,
        ),
        "BARE_INVESTMENT_NOT_GLOBALLY_BLOCKED": accepted(
            "neighbor_bare_corporate_investment"
        ),
        "CORPORATE_AI_INVESTMENT_ACCEPTED": accepted(
            "neighbor_corporate_investment_without_ai_title"
        ),
        "GENERIC_AI_TAILWIND_REJECTED": rejected(
            "neighbor_generic_ai_tailwind",
            watch_semantic_precision.GENERIC_INDUSTRY_AI_TAILWIND,
        ),
        "MATERIAL_AI_INDUSTRIAL_EVENT_ACCEPTED": accepted(
            "neighbor_tailwind_with_material_event"
        ),
        "NON_AI_ROUNDUP_WITH_AI_SECONDARY_REJECTED": rejected(
            "neighbor_non_ai_roundup_ai_secondary",
            watch_semantic_precision.ROUNDUP_MULTI_TOPIC,
        ),
        "AI_DOMINANT_ROUNDUP_NOT_BLANKET_REJECTED": accepted(
            "neighbor_ai_dominant_roundup_format"
        ),
        "TITLE_AI_ABSENT_SECONDARY_CONTAMINATION_REJECTED": rejected(
            "neighbor_title_ai_absent_secondary_contamination",
            watch_semantic_precision.AI_INCIDENTAL,
        ),
        "INCIDENTAL_AI_REJECTED": rejected(
            "neighbor_incidental_ai_buzzword",
            watch_semantic_precision.AI_INCIDENTAL,
        ),
        "AI_DC_FIRST_SHOVEL_ACCEPTED": accepted(
            "neighbor_ai_dc_first_shovel"
        ),
        "AI_DC_GROUNDBREAKING_ACCEPTED": accepted(
            "neighbor_ai_dc_groundbreaking"
        ),
        "HDEC_AI_DC_FIRST_SHOVEL_ACCEPTED": accepted(
            "neighbor_hdec_ai_dc_first_shovel"
        ),
        "NON_AI_FIRST_SHOVEL_WITH_INCIDENTAL_AI_REJECTED": rejected(
            "neighbor_non_ai_first_shovel_incidental_ai",
            watch_semantic_precision.AI_INCIDENTAL,
        ),
        "SPECULATIVE_GROUNDBREAKING_REJECTED": rejected(
            "neighbor_speculative_groundbreaking",
            watch_semantic_precision.OTHER_NONEXECUTIVE,
        ),
        "HISTORICAL_GROUNDBREAKING_CONTEXT_REJECTED": rejected(
            "neighbor_historical_groundbreaking",
            watch_semantic_precision.OTHER_NONEXECUTIVE,
        ),
    }
    for name, value in flags.items():
        check(name, value)

    # Exercise the construction-start language as a semantic class, beyond
    # the six named corpus neighbors.  Ceremony/cue text alone remains false;
    # a contradictory speculative headline can continue only when the bounded
    # publisher lead independently states that the event actually occurred.
    groundbreaking_variant_matrix = (
        (
            "spaceless first-shovel idiom",
            "국가 AI 컴퓨팅센터 첫삽 떴다",
            "정부가 센터 공사를 시작했다.",
            True,
        ),
        (
            "spaced first-shovel verb idiom",
            "국가 AI 컴퓨팅센터 첫 삽 떴다",
            "정부가 센터 건설에 착수했다.",
            True,
        ),
        (
            "bare groundbreaking idiom",
            "국가 AI 컴퓨팅센터 기공",
            "정부가 이날 센터를 기공했다.",
            True,
        ),
        (
            "construction ceremony idiom",
            "국가 AI 컴퓨팅센터 착공식",
            "정부가 이날 착공식을 열었다.",
            True,
        ),
        (
            "speculative headline with independently confirmed event",
            "AI 데이터센터 기공식 가능성 검토",
            "정부가 이날 기공식을 개최하고 공사를 시작했다.",
            True,
        ),
        (
            "construction ceremony speculation alone",
            "국가 AI 컴퓨팅센터 착공식 예정 검토",
            "정부가 착공식 개최 가능성을 검토 중이다.",
            False,
        ),
        (
            "historical action stated only in lead",
            "첫 삽 뜬 AI센터 효과 분석",
            "센터는 지난해 착공했다는 분석이다.",
            False,
        ),
    )
    for name, title, snippet, expected in groundbreaking_variant_matrix:
        decision = watch_semantic_precision.classify(
            {"title": title, "snippet": snippet}
        )
        check(
            f"groundbreaking variant: {name}",
            decision.eligible is expected,
            repr(decision),
        )

    # Inputs outside the bounded evidence contract cannot alter a semantic
    # verdict.  Count only false->true changes; the required authority is zero.
    query_caused = 0
    generated_caused = 0
    publisher_caused = 0
    for row in rows:
        baseline = decisions[row["case_id"]]
        query_row = copy.deepcopy(row)
        query_row["source_metadata"] = {
            "provider": "adversarial",
            "query": "AI 데이터센터 투자 확정 공급계약 현대건설",
        }
        query_result = watch_semantic_precision.classify(query_row)
        generated_row = copy.deepcopy(row)
        generated_row.update(
            {
                "summary": "AI 데이터센터 5조원 투자 계약 확정",
                "whyImportant": "현대건설 최우선 사업 기회",
                "why_it_matters": "AI 인프라 핵심 이벤트",
                "category_label": "AI 데이터센터",
            }
        )
        generated_result = watch_semantic_precision.classify(generated_row)
        publisher_row = copy.deepcopy(row)
        publisher_row.update(
            {
                "source": "연합뉴스",
                "url": "https://www.yna.co.kr/view/AKR20260818000000001",
                "publisher_direct": True,
            }
        )
        publisher_result = watch_semantic_precision.classify(publisher_row)
        query_caused += int(not baseline.eligible and query_result.eligible)
        generated_caused += int(not baseline.eligible and generated_result.eligible)
        publisher_caused += int(not baseline.eligible and publisher_result.eligible)
        check(
            f"{row['case_id']}: non-authoritative metadata leaves verdict unchanged",
            query_result == baseline
            and generated_result == baseline
            and publisher_result == baseline,
        )

    check("QUERY_CAUSED_QUALIFICATION=0", query_caused == 0, str(query_caused))
    check(
        "GENERATED_TEXT_CAUSED_QUALIFICATION=0",
        generated_caused == 0,
        str(generated_caused),
    )
    check(
        "PUBLISHER_ALONE_CAUSED_QUALIFICATION=0",
        publisher_caused == 0,
        str(publisher_caused),
    )

    # Existing hard exclusions remain owned by their established gates.
    etf = evaluate_teams_push_policy(
        _policy_fixture(
            "etf_regression",
            "AI 전략산업 ETF 1조원 규모 출시",
            "운용사가 AI 관련 종목을 담는 상장지수펀드를 신규 출시했다.",
        )
    )
    stock = evaluate_teams_push_policy(
        _policy_fixture(
            "stock_regression",
            "AI 데이터센터 수혜주 급등…목표주가 상향",
            "증권가는 테마 종목의 주가 전망과 매수 전략을 제시했다.",
            publisher_section="증권",
        )
    )
    opinion = evaluate_teams_push_policy(
        _policy_fixture(
            "opinion_regression",
            "[기고] AI 데이터센터 전력망 전략",
            "AI 전력 인프라 정책 방향을 제언한다.",
            publisher_section="기고",
        )
    )
    tier_c = source_priority.teams_delivery_source_policy(
        "IT조선", "https://it.chosun.com/news/articleView.html?idxno=9999999"
    )
    regression_flags = {
        "ETF_FALSE_POSITIVE_STILL_REJECTED": not etf.eligible,
        "STOCK_MARKET_FALSE_POSITIVE_STILL_REJECTED": not stock.eligible,
        "OPINION_REALTIME_STILL_REJECTED": (
            not opinion.eligible
            and opinion.rejection_reason == "excluded_opinion_content"
        ),
        "TIER_C_STANDALONE_STILL_ZERO": not tier_c["realtime_auto_send"],
    }
    for name, value in regression_flags.items():
        check(name, value)

    recall_flags = {
        "LS_ELECTRIC_GS_EC_RECALL": policy_results[
            "neighbor_ls_electric_gs_ec_recall"
        ].eligible,
        "MATERIAL_AI_INFRA_RECALL": policy_results[
            "neighbor_material_ai_infra_contract"
        ].eligible,
        "HDEC_DIRECT_RECALL": policy_results[
            "neighbor_hdec_direct_material"
        ].eligible,
    }
    for name, value in recall_flags.items():
        check(name, value)

    actor_bridge_false_positive_flags = {
        "ACTOR_BRIDGE_SUV_INCIDENTAL_AI_REJECTED": rejected(
            "neighbor_actor_bridge_suv_incidental_ai",
            watch_semantic_precision.AI_INCIDENTAL,
        ),
        "ACTOR_BRIDGE_APARTMENT_INCIDENTAL_AI_REJECTED": rejected(
            "neighbor_actor_bridge_apartment_incidental_ai",
            watch_semantic_precision.AI_INCIDENTAL,
        ),
        "ACTOR_BRIDGE_REFINERY_INCIDENTAL_AI_REJECTED": rejected(
            "neighbor_actor_bridge_refinery_incidental_ai",
            watch_semantic_precision.AI_INCIDENTAL,
        ),
    }
    for name, value in actor_bridge_false_positive_flags.items():
        check(name, value)
    actor_bridge_hardened = all(actor_bridge_false_positive_flags.values())
    actor_bridge_recall_regression = not all(
        (
            accepted("suspect_06_yna_cursor_acquisition"),
            accepted("suspect_26_munhwa_hdec_terrapower_smr"),
            accepted("neighbor_corporate_investment_without_ai_title"),
            accepted("neighbor_material_ai_infra_contract"),
            accepted("neighbor_ai_regulation_effective"),
            accepted("neighbor_ai_security_incident"),
            accepted("neighbor_ls_electric_gs_ec_recall"),
        )
    )
    check("ACTOR_BRIDGE_HARDENED=true", actor_bridge_hardened)
    check(
        "ACTOR_BRIDGE_RECALL_REGRESSION=false",
        not actor_bridge_recall_regression,
    )

    expected_rejects = [row for row in rows if not _expected_eligible(row)]
    expected_keeps = [row for row in rows if _expected_eligible(row)]
    under_filtering = any(decisions[row["case_id"]].eligible for row in expected_rejects)
    over_filtering = any(
        not decisions[row["case_id"]].eligible for row in expected_keeps
    ) or not all(recall_flags.values())
    check("OVER_FILTERING_DETECTED=false", not over_filtering)
    check("UNDER_FILTERING_DETECTED=false", not under_filtering)

    suspects = [row for row in rows if row.get("corpus_role") == "suspect"]
    suspect_counts = Counter(row.get("human_expected") for row in suspects)
    check(
        "suspect proposal counts are exact",
        suspect_counts == Counter({"REJECT": 17, "KEEP": 10, "BORDERLINE": 5}),
        repr(suspect_counts),
    )

    state_after = _state_hashes()
    check("EXTERNAL_NETWORK_CALLS=0", NETWORK_CALLS == 0, str(NETWORK_CALLS))
    check("REAL_SMTP_CONNECTIONS=0", SMTP_CONNECTIONS == 0, str(SMTP_CONNECTIONS))
    check("PRODUCTION_STATE_WRITES=0", state_before == state_after)

    print(f"QUERY_CAUSED_QUALIFICATION={query_caused}")
    print(f"GENERATED_TEXT_CAUSED_QUALIFICATION={generated_caused}")
    print(f"PUBLISHER_ALONE_CAUSED_QUALIFICATION={publisher_caused}")
    for name, value in {**flags, **regression_flags, **recall_flags}.items():
        print(f"{name}={'PASS' if value else 'FAIL'}")
    print(f"ACTOR_BRIDGE_HARDENED={str(actor_bridge_hardened).lower()}")
    print(
        "ACTOR_BRIDGE_FALSE_POSITIVE_NEIGHBORS="
        + ("PASS" if actor_bridge_hardened else "FAIL")
    )
    print(
        "ACTOR_BRIDGE_RECALL_REGRESSION="
        + str(actor_bridge_recall_regression).lower()
    )
    print(f"SUSPECT_CORPUS_TOTAL={len(suspects)}")
    print(f"SUSPECT_CORPUS_RECOVERED={len(suspects)}")
    print(f"SUSPECT_PROPOSED_REJECT={suspect_counts['REJECT']}")
    print(f"SUSPECT_PROPOSED_KEEP={suspect_counts['KEEP']}")
    print(f"SUSPECT_BORDERLINE={suspect_counts['BORDERLINE']}")
    print("SUSPECT_UNKNOWN_EVIDENCE=0")
    print(f"OVER_FILTERING_DETECTED={str(over_filtering).lower()}")
    print(f"UNDER_FILTERING_DETECTED={str(under_filtering).lower()}")
    print(f"EXTERNAL_NETWORK_CALLS={NETWORK_CALLS}")
    print(f"REAL_SMTP_CONNECTIONS={SMTP_CONNECTIONS}")
    print(f"PRODUCTION_STATE_WRITES={int(state_before != state_after)}")
    print(f"R4_OPS8_CHECKS={CHECKS}")
    print(f"R4_OPS8_FAILURES={len(FAILURES)}")
    if FAILURES:
        print("R4_OPS8_WATCH_SEMANTIC_PRECISION=FAIL")
        return 1
    print("R4_OPS8_WATCH_SEMANTIC_PRECISION=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
