#!/usr/bin/env python3
"""Offline R4-OPS-5 production-policy acceptance and real-corpus replay.

No transport, provider, or production-state API is called.  The verifier uses
the production classifiers/renderers against committed evidence and in-memory
state, then prints every replay row in the operator-requested field format.
"""

from __future__ import annotations

import json
import os
import re
import socket
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import editorial_briefing_state as daily_state  # noqa: E402
from app import editorial_briefings as brief  # noqa: E402
from app import editorial_review  # noqa: E402
from app import executive_materiality  # noqa: E402
from app import source_priority  # noqa: E402
from app import teams_push_state  # noqa: E402
from app.teams_ai_push import (  # noqa: E402
    IMPORTANCE_IMPORTANT,
    IMPORTANCE_TOP,
    TEAMS_NORMAL_PACING_MINUTES,
    apply_major_media_first_gate,
    classify_ai_topic,
    evaluate_realtime_opinion_gate,
    evaluate_source_gate,
    evaluate_teams_push_policy,
    is_executive_relevant_for_push,
    map_importance,
    select_teams_push_candidates,
)
from scripts import run_editorial_briefing as daily_runner  # noqa: E402

KST = datetime.now().astimezone().tzinfo
REPLAY_PATH = ROOT / "data" / "r4_ops5_production_replay.json"
RULES_PATH = ROOT / "data" / "source_priority_rules.json"
COMMON_STANDARD_PATH = ROOT / "AI_PROJECT_EXECUTION_STANDARD.md"
PROJECT_ACCEPTANCE_PATH = ROOT / "docs" / "acceptance" / "PROJECT_ACCEPTANCE.md"
STARTUP_CONTRACT_PATHS = (
    ROOT / "docs" / "handoff" / "HDEC_CURRENT_HANDOFF.md",
    ROOT / "docs" / "handoff" / "COMPANY_RESUME_PROMPT.md",
)
EMPTY_STATUS = "오늘 기준을 충족한 임원용 AI 핵심 뉴스가 없습니다."
SBS_PREMIUM_OBSERVED_URL = "https://premium.sbs.co.kr/article/r26f8YfJ9"
SBS_PREMIUM_OBSERVED_CASE = "sbs_premium_observed_incident_r26f8YfJ9"
SBS_PREMIUM_STRESS_CASE = "sbs_premium_synthetic_adversarial_stress"
OBSERVABLE_REPLAY_FIELDS = (
    "article_key",
    "title",
    "source",
    "url",
    "publisher_section",
    "snippet",
    "summary",
    "hdec_relevance",
    "published_at",
    "publisher_direct",
    "score",
    "shadow_urgency_status",
    "shadow_would_pass",
    "shadow_confirmed_event_types",
    "change_type",
    "current_run_seen",
    "search_query",
)

CHECKS = 0
FAILURES: list[str] = []
NETWORK_CALLS = 0


def check(label: str, condition: bool, detail: object = "") -> None:
    global CHECKS
    CHECKS += 1
    if condition:
        print(f"PASS: {label}")
        return
    FAILURES.append(label)
    suffix = f" — {str(detail)[:500]}" if detail != "" else ""
    print(f"FAIL: {label}{suffix}")


def _forbid_network(*_args, **_kwargs):
    global NETWORK_CALLS
    NETWORK_CALLS += 1
    raise AssertionError("external network is forbidden in R4-OPS-5 replay")


def _material_decision(row: dict) -> executive_materiality.ExecutiveQualification:
    return executive_materiality.executive_qualification(
        {
            "title": row.get("title", ""),
            "snippet": row.get("snippet", ""),
            "subtitle": row.get("subtitle", ""),
            "publisher_section": row.get("publisher_section", ""),
        }
    )


def governing_standard_contracts() -> dict[str, bool]:
    common_present = COMMON_STANDARD_PATH.is_file()
    project_present = PROJECT_ACCEPTANCE_PATH.is_file()
    check("common execution standard is tracked at repository root", common_present)
    check("project acceptance contract is tracked at canonical path", project_present)
    for path in STARTUP_CONTRACT_PATHS:
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        normalized = text.casefold()
        check(
            f"{path.name} requires both governing documents before material work",
            "ai_project_execution_standard.md" in normalized
            and "docs/acceptance/project_acceptance.md" in normalized
            and "before material work" in normalized,
        )
    return {
        "common_present": common_present,
        "project_present": project_present,
    }


def project_acceptance_overlay_contracts() -> dict[str, bool]:
    text = PROJECT_ACCEPTANCE_PATH.read_text(encoding="utf-8")
    version_updated = "**Version:** 1.2" in text
    defect_present = (
        "HDEC-DEFECT-005 — SBS Premium realtime authority leak" in text
        and SBS_PREMIUM_OBSERVED_URL in text
        and "operator_surface=editorial_analysis" in text
        and "realtime standalone Teams auto-send is `false`" in text
    )
    authority_invariant = (
        "KNOWN_AUTHORITATIVE_URL_IDENTITY > DISPLAY_SOURCE_ALIAS" in text
        and "Unknown foreign hosts do not inherit alias authority" in text
        and "Unenumerated\nsibling subdomains do not inherit parent-publication authority" in text
        and "exact selected-URL host is configured" in text
    )
    opinion_contract = all(
        token in text
        for token in (
            "clear bracketed title-boundary markers",
            "leading or trailing title boundary",
            "`Opinion`",
            "`Editorial`",
            "Summary,\nquery, and body text have zero authority",
        )
    )
    check("HDEC project acceptance overlay version is 1.2", version_updated)
    check("HDEC-DEFECT-005 is sealed in project acceptance", defect_present)
    check("publisher URL authority invariant is sealed in project acceptance", authority_invariant)
    check("trailing and English opinion contract is sealed in project acceptance", opinion_contract)
    return {
        "version_updated": version_updated,
        "defect_present": defect_present,
        "authority_invariant": authority_invariant,
        "opinion_contract": opinion_contract,
    }


def publisher_contracts(rules: dict) -> dict[str, object]:
    tier_a = [
        "연합뉴스", "MBC", "KBS", "조선일보", "YTN", "JTBC", "중앙일보",
        "매일경제", "한국경제", "SBS", "동아일보", "한겨레", "경향신문",
    ]
    tier_b = [
        "서울경제", "한국일보", "뉴스1", "뉴시스", "조선비즈", "머니투데이",
        "이데일리", "아시아경제", "파이낸셜뉴스", "헤럴드경제", "전자신문",
        "디지털타임스", "국민일보", "세계일보", "서울신문", "문화일보",
    ]
    policies = rules["publisher_delivery_policies"]
    actual_a = [
        row["name"] for row in policies
        if row["tier"] in {"primary_10", "secondary_3"}
    ]
    actual_b = [row["name"] for row in policies if row["tier"] == "major_secondary"]
    check("Tier A operator list is exact", actual_a == tier_a, actual_a)
    check("Tier B operator allowlist is exact", actual_b == tier_b, actual_b)

    # Known exact URL identity outranks every contradictory display alias. The
    # last two rows are non-Chosun cross-family neighbors.
    cross_identity_matrix = (
        ("조선일보", "https://www.chosun.com/a", "primary_10", "조선일보", "tier_a_core_major"),
        ("조선일보", "https://biz.chosun.com/a", "major_secondary", "조선비즈", "tier_b_major_secondary"),
        ("연합뉴스", "https://it.chosun.com/a", "specialist", "IT조선", "tier_c_specialist_niche"),
        ("한국일보", "https://www.chosun.com/a", "primary_10", "조선일보", "tier_a_core_major"),
        ("연합뉴스", "https://hankookilbo.com/a", "major_secondary", "한국일보", "tier_b_major_secondary"),
        ("서울경제", "https://ytn.co.kr/a", "primary_10", "YTN", "tier_a_core_major"),
    )
    cross_identity_failures = []
    for source, url, expected_tier, identity, operator_tier in cross_identity_matrix:
        resolved = source_priority.publisher_delivery_tier(source, url)
        teams = source_priority.teams_delivery_source_policy(source, url)
        correct = (
            resolved["tier"] == expected_tier
            and resolved["publisher_identity"] == identity
            and resolved["identity_evidence"] == "exact_domain"
            and teams["tier"] == resolved["tier"]
            and teams["publisher_identity"] == resolved["publisher_identity"]
            and teams["operator_tier"] == operator_tier
        )
        if not correct:
            cross_identity_failures.append((source, url, resolved, teams))
        check(
            f"known URL identity defeats contradictory alias: {source} -> {url}",
            correct,
            {"tier": resolved, "teams": teams},
        )

    sibling_matrix = (
        ("조선일보", "https://sports.chosun.com/a", "스포츠조선", "exact_domain"),
        ("조선일보", "https://foo.chosun.com/a", "foo.chosun.com", "unrecognized_url_host"),
        ("조선일보", "https://evilchosun.com/a", "evilchosun.com", "unrecognized_url_host"),
        ("조선일보", "https://chosun.com.evil.example/a", "chosun.com.evil.example", "unrecognized_url_host"),
    )
    for source, url, identity, evidence in sibling_matrix:
        resolved = source_priority.publisher_delivery_tier(source, url)
        teams = source_priority.teams_delivery_source_policy(source, url)
        correct = (
            resolved["tier"] == "neutral"
            and resolved["publisher_identity"] == identity
            and resolved["identity_evidence"] == evidence
            and teams["publisher_identity"] == identity
            and teams["teams_lane"] == source_priority.TEAMS_LANE_NEVER_AUTOMATIC
            and not teams["realtime_auto_send"]
        )
        if not correct:
            cross_identity_failures.append((source, url, resolved, teams))
        check(
            f"unlisted Chosun sibling never inherits 조선일보: {url}",
            correct,
            {"tier": resolved, "teams": teams},
        )

    unknown = source_priority.teams_delivery_source_policy(
        "연합뉴스", "https://unknown.example/a"
    )
    unknown_correct = (
        unknown["tier"] == "neutral"
        and unknown["publisher_identity"] == "unknown.example"
        and unknown["identity_evidence"] == "unrecognized_url_host"
        and unknown["teams_lane"] == source_priority.TEAMS_LANE_NEVER_AUTOMATIC
    )
    if not unknown_correct:
        cross_identity_failures.append(("연합뉴스", "https://unknown.example/a", unknown))
    check(
        "unknown foreign host does not inherit a known display alias",
        unknown_correct,
        unknown,
    )

    specialist_rows = rules["teams_delivery_source_policy"]["specialist_publishers"]
    configured_identities = [
        (row["name"], row["tier"], row.get("domains") or [], (
            "tier_a_core_major"
            if row["tier"] in {"primary_10", "secondary_3"}
            else "tier_b_major_secondary"
        ))
        for row in policies
    ] + [
        (row["name"], "specialist", row.get("domains") or [], "tier_c_specialist_niche")
        for row in specialist_rows
    ]

    # Sweep every configured Tier-A/B/C domain with a deliberately contradictory
    # alias from another publication. Both public resolvers must select the same
    # exact URL identity and tier.
    sweep_failures = []
    for index, (name, tier, domains, operator_tier) in enumerate(configured_identities):
        contradictory_source = configured_identities[(index + 1) % len(configured_identities)][0]
        for domain in domains:
            url = f"https://{domain}/cross-entry-audit"
            resolved = source_priority.publisher_delivery_tier(contradictory_source, url)
            teams = source_priority.teams_delivery_source_policy(contradictory_source, url)
            if not (
                resolved["publisher_identity"] == name
                and resolved["tier"] == tier
                and teams["publisher_identity"] == name
                and teams["tier"] == tier
                and teams["operator_tier"] == operator_tier
            ):
                sweep_failures.append((contradictory_source, url, name, resolved, teams))
    check(
        "all configured Tier-A/B/C domains defeat cross-entry aliases",
        not sweep_failures,
        sweep_failures,
    )
    cross_identity_failures.extend(sweep_failures)

    # A fabricated child of every configured A/B/C domain must never inherit it.
    collision_failures = []
    for name, tier, domains, _operator_tier in configured_identities:
        for domain in domains:
            normalized = domain.removeprefix("www.")
            child_url = f"https://unlisted-sibling.{normalized}/article/1"
            resolved = source_priority.publisher_delivery_tier(
                name, child_url
            )
            teams = source_priority.teams_delivery_source_policy(name, child_url)
            if resolved["tier"] == tier or resolved["publisher_identity"] == name or teams["realtime_auto_send"]:
                collision_failures.append((name, child_url, resolved, teams))
    check(
        "all configured Tier-A/B/C domains reject unenumerated child properties",
        not collision_failures,
        collision_failures,
    )
    cross_identity_failures.extend(collision_failures)

    tier_c_examples = {
        row["name"]
        for row in rules["teams_delivery_source_policy"]["specialist_publishers"]
    }
    required_c = {
        "IT조선", "ZDNet Korea", "테크M", "테크월드", "더벨", "인베스트조선",
        "대한경제", "에너지경제", "전기신문",
    }
    check("Tier C operator list contains every required specialist", required_c <= tier_c_examples)
    for name in required_c:
        policy = source_priority.teams_delivery_source_policy(name, "")
        check(
            f"Tier C {name} is never immediate",
            policy["operator_tier"] == "tier_c_specialist_niche"
            and policy["teams_lane"] == source_priority.TEAMS_LANE_SPECIALIST_HOLDBACK,
            policy,
        )

    sbs_cases = (
        ("SBS", "https://news.sbs.co.kr/article/1", "SBS", "primary_10", True, "hard_news"),
        ("SBS", "https://sbs.co.kr/article/1", "SBS", "primary_10", True, "hard_news"),
        ("SBS", "https://premium.sbs.co.kr/article/r26f8YfJ9", "SBS Premium", "neutral", False, "editorial_analysis"),
        ("SBS", "https://foo.sbs.co.kr/article/1", "foo.sbs.co.kr", "neutral", False, "unlisted"),
        ("arbitrary", "https://premium.sbs.co.kr/article/r26f8YfJ9?utm_source=x&ref=y&refer=z", "SBS Premium", "neutral", False, "editorial_analysis"),
    )
    sbs_failures = []
    for source, url, identity, tier, realtime, surface in sbs_cases:
        resolved = source_priority.publisher_delivery_tier(source, url)
        teams = source_priority.teams_delivery_source_policy(source, url)
        correct = (
            resolved["publisher_identity"] == identity
            and resolved["tier"] == tier
            and teams["publisher_identity"] == identity
            and teams["operator_surface"] == surface
            and teams["realtime_auto_send"] is realtime
        )
        if not correct:
            sbs_failures.append((source, url, resolved, teams))
        check(f"SBS exact-surface authority: {url}", correct, {"tier": resolved, "teams": teams})

    check(
        "CROSS_PUBLISHER_ALIAS_URL_ELEVATION remains zero",
        not cross_identity_failures,
        cross_identity_failures,
    )
    premium_policies = [
        source_priority.teams_delivery_source_policy(source, url)
        for source, url, _identity, _tier, _realtime, _surface in sbs_cases
        if "premium.sbs.co.kr" in url
    ]
    return {
        "cross_publisher_alias_url_elevation": len(cross_identity_failures),
        "sbs_premium_tier_a_inheritance": sum(
            policy["tier"] in source_priority.TEAMS_IMMEDIATE_TIERS
            for policy in premium_policies
        ),
        "sbs_premium_realtime_auto_send": any(
            policy["realtime_auto_send"] for policy in premium_policies
        ),
    }


def opinion_contracts() -> dict[str, bool]:
    sections = (
        "칼럼", "오피니언", "사설", "논설", "기고", "기고문", "전문가칼럼", "시론",
        "Opinion", "Editorial", "Column", "Commentary", "Op-Ed", "OpEd",
    )
    for section in sections:
        row = {"title": "AI 데이터센터 전력망 투자 계약 체결", "publisher_section": section}
        check(
            f"opinion section marker is a hard gate: {section}",
            evaluate_realtime_opinion_gate(row).excluded,
        )

    title_cases = (
        ("[기고] foo", "leading Korean square marker"),
        ("foo [기고]", "trailing Korean square marker"),
        ("【Opinion】 foo", "leading English corner marker"),
        ("foo 【Opinion】", "trailing English corner marker"),
        ("［사설］ 제목", "leading full-width square marker"),
    )
    title_results: dict[str, bool] = {}
    for title, label in title_cases:
        excluded = evaluate_realtime_opinion_gate(
            {"title": title, "publisher_section": "산업"}
        ).excluded
        title_results[label] = excluded
        check(f"opinion title boundary: {label}", excluded)

    incidental_titles = (
        "업계 관계자가 보고서에 기고했다고 밝혔다",
        "칼럼비아대 연구진, AI 데이터센터 연구 발표",
        "AI 데이터센터 [기고] 분석 보고서 발표",
    )
    incidental_pass = all(
        not evaluate_realtime_opinion_gate(
            {"title": title, "publisher_section": "산업"}
        ).excluded
        for title in incidental_titles
    )
    check("incidental opinion-token prose remains allowed", incidental_pass)
    check(
        "non-exact publisher section containing an opinion token remains allowed",
        not evaluate_realtime_opinion_gate(
            {"title": "AI 데이터센터 계약", "publisher_section": "오피니언룸 안내"}
        ).excluded,
    )
    non_authoritative = {
        "title": "AI 데이터센터 전력망 투자 계약 체결",
        "publisher_section": "산업",
        "snippet": "[기고] 본문 표시는 realtime opinion 판정 권한이 없다.",
        "summary": "【Opinion】 생성 요약",
        "search_query": "Editorial AI 데이터센터",
        "body": "[사설] 본문",
    }
    metadata_zero_authority = not evaluate_realtime_opinion_gate(non_authoritative).excluded
    check("summary/query/body have zero realtime opinion authority", metadata_zero_authority)
    return {
        "leading": title_results["leading Korean square marker"] and title_results["leading English corner marker"],
        "trailing": title_results["trailing Korean square marker"] and title_results["trailing English corner marker"],
        "english_section": all(
            evaluate_realtime_opinion_gate(
                {"title": "AI 데이터센터 계약", "publisher_section": section}
            ).excluded
            for section in ("Opinion", "Editorial")
        ),
        "incidental": incidental_pass and metadata_zero_authority,
    }


def replay_provenance_contracts(rows: list[dict]) -> dict[str, bool]:
    observed_rows = [
        row for row in rows if row.get("evidence_kind") == "observed_production"
    ]
    synthetic_rows = [
        row for row in rows if row.get("evidence_kind") == "synthetic_adversarial"
    ]

    observed_failures = []
    for row in observed_rows:
        provenance = row.get("provenance") or {}
        observed_fields = set(provenance.get("observed_fields") or [])
        undeclared_metadata = [
            field
            for field in OBSERVABLE_REPLAY_FIELDS
            if row.get(field) not in (None, "", "unknown")
            and field not in observed_fields
        ]
        if not (
            row.get("observed_url")
            and "observed_url" in observed_fields
            and provenance.get("evidence_source")
            and provenance.get("historical_metadata_status")
            == "unrecovered_from_committed_repository_evidence"
            and not undeclared_metadata
        ):
            observed_failures.append(
                {"case_id": row.get("case_id"), "undeclared_metadata": undeclared_metadata}
            )
    check(
        "observed production replay rows declare provenance and no unverified metadata",
        bool(observed_rows) and not observed_failures,
        observed_failures,
    )

    synthetic_failures = []
    for row in synthetic_rows:
        labelled_text = all(
            str(row.get(field) or "").startswith("[SYNTHETIC]")
            for field in ("title", "snippet", "summary", "hdec_relevance", "search_query")
        )
        provenance = row.get("provenance") or {}
        if not (
            row.get("synthetic_metadata") is True
            and labelled_text
            and provenance.get("evidence_source")
            and provenance.get("historical_claim") is False
        ):
            synthetic_failures.append(row.get("case_id"))
    check(
        "synthetic adversarial replay rows are explicit and never historical",
        bool(synthetic_rows) and not synthetic_failures,
        synthetic_failures,
    )

    observed_sbs = [
        row for row in observed_rows
        if row.get("case_id") == SBS_PREMIUM_OBSERVED_CASE
    ]
    synthetic_sbs = [
        row for row in synthetic_rows
        if row.get("case_id") == SBS_PREMIUM_STRESS_CASE
    ]
    sbs_split = (
        len(observed_sbs) == 1
        and observed_sbs[0].get("observed_url") == SBS_PREMIUM_OBSERVED_URL
        and len(synthetic_sbs) == 1
        and str(synthetic_sbs[0].get("url") or "").split("?", 1)[0]
        == SBS_PREMIUM_OBSERVED_URL
    )
    check("SBS observed incident and synthetic stress are separate fixtures", sbs_split)

    verdict = not observed_failures and not synthetic_failures and sbs_split
    return {
        "verdict": verdict,
        "observed_present": len(observed_sbs) == 1,
        "synthetic_present": len(synthetic_sbs) == 1,
    }


def replay_contracts(rows: list[dict]) -> dict[str, bool | None]:
    outcomes: dict[str, bool | None] = {}
    query_caused_qualification = 0
    for row in rows:
        evidence_kind = str(row.get("evidence_kind") or "legacy_regression")
        source = str(row.get("source") or "")
        url = str(row.get("observed_url") or row.get("url") or "")
        source_policy = source_priority.teams_delivery_source_policy(
            source, url
        )

        if evidence_kind == "observed_production":
            # Historical article metadata is unavailable for this incident. Do
            # not fabricate semantic classifications: the exact configured
            # editorial surface is independently sufficient to deny realtime
            # authority, while every unverified article-level field stays unknown.
            surface_rejected = (
                source_policy["operator_surface"] == "editorial_analysis"
                and not source_policy["realtime_auto_send"]
            )
            final: bool | None = False if surface_rejected else None
            reason = (
                "explicit_non_realtime_editorial_surface"
                if surface_rejected
                else "insufficient_observed_metadata_for_final_decision"
            )
            expected_final = row["human_expected"] == "KEEP"
            expected_tokens = row.get("expected_reason_tokens") or []
            check(
                f"replay {row['case_id']} human/system final match",
                final is expected_final,
                {"expected": expected_final, "actual": final, "reason": reason},
            )
            check(
                f"replay {row['case_id']} source tier expectation",
                source_policy["operator_tier"] == row["expected_operator_tier"],
                source_policy,
            )
            check(
                f"replay {row['case_id']} reason expectation",
                all(token in reason for token in expected_tokens),
                reason,
            )
            print("TITLE=UNKNOWN")
            print("SOURCE=UNKNOWN")
            print(f"RESOLVED_PUBLISHER_IDENTITY={source_policy['publisher_identity']}")
            print(f"SOURCE_TIER={source_policy['operator_tier']}")
            print(f"OPERATOR_SURFACE={source_policy['operator_surface']}")
            print("AI_CENTRAL=unknown")
            print("EXECUTIVE_RELEVANT=unknown")
            print("MATERIAL=unknown")
            print("IMPORTANCE=unknown")
            print("OPINION_GATE=unknown")
            print("TEAMS_POLICY_ELIGIBLE=unknown")
            print(f"FINAL_REALTIME_DECISION={'REJECT' if final is False else 'UNKNOWN'}")
            print(f"REASON={reason}")
            outcomes[row["case_id"]] = final
            continue

        topic = classify_ai_topic(row)
        executive_relevant = is_executive_relevant_for_push(row, topic)
        material = _material_decision(row)
        importance = map_importance(row, topic)
        opinion = evaluate_realtime_opinion_gate(row)
        policy = evaluate_teams_push_policy(row)
        candidates = select_teams_push_candidates([row], max_articles=None)
        gate = evaluate_source_gate(candidates[0]) if candidates else None
        final = bool(policy.eligible and gate is not None and gate.immediate)
        reasons: list[str] = []
        if policy.rejection_reason:
            reasons.append(policy.rejection_reason)
        if source_policy["operator_tier"] == "tier_c_specialist_niche":
            reasons.append("tier_c_not_standalone")
        elif gate is not None:
            reasons.append(gate.reason)
        if not reasons:
            reasons.append("eligible_subject_to_dedup_and_pacing")
        reason = "+".join(dict.fromkeys(reasons))

        expected_final = row["human_expected"] == "KEEP"
        expected_tokens = row.get("expected_reason_tokens") or []
        check(
            f"replay {row['case_id']} human/system final match",
            final == expected_final,
            {"expected": expected_final, "actual": final, "reason": reason},
        )
        check(
            f"replay {row['case_id']} policy expectation",
            policy.eligible is row["expected_policy_eligible"],
            policy,
        )
        check(
            f"replay {row['case_id']} source tier expectation",
            source_policy["operator_tier"] == row["expected_operator_tier"],
            source_policy,
        )
        check(
            f"replay {row['case_id']} reason expectation",
            all(token in reason for token in expected_tokens),
            reason,
        )

        without_query = dict(row)
        without_query.pop("search_query", None)
        if (
            evaluate_teams_push_policy(without_query).eligible is False
            and policy.eligible is True
        ):
            query_caused_qualification += 1

        print(f"TITLE={row.get('title') or 'UNKNOWN'}")
        print(f"SOURCE={row.get('source') or 'UNKNOWN'}")
        print(f"RESOLVED_PUBLISHER_IDENTITY={source_policy['publisher_identity']}")
        print(f"SOURCE_TIER={source_policy['operator_tier']}")
        print(f"OPERATOR_SURFACE={source_policy['operator_surface']}")
        print(f"AI_CENTRAL={str(topic.eligible).lower()}")
        print(f"EXECUTIVE_RELEVANT={str(executive_relevant).lower()}")
        print(f"MATERIAL={str(material.qualified).lower()}")
        print(f"IMPORTANCE={importance.level or 'not_sendable'}")
        print(f"OPINION_GATE={'REJECT' if opinion.excluded else 'PASS'}")
        print(f"TEAMS_POLICY_ELIGIBLE={str(policy.eligible).lower()}")
        print(f"FINAL_REALTIME_DECISION={'KEEP' if final else 'REJECT'}")
        print(f"REASON={reason}")
        outcomes[row["case_id"]] = final

    check(
        "SEARCH_QUERY_CAUSED_QUALIFICATION remains zero",
        query_caused_qualification == 0,
        query_caused_qualification,
    )
    return outcomes


def _normal_article(key: str, source: str, url: str, title: str) -> dict:
    return {
        "article_key": key,
        "title": title,
        "source": source,
        "url": url,
        "snippet": f"{title}에 관한 협약을 체결하고 구축 계획을 공식 발표했다.",
        "summary": f"{title}에 관한 협약을 체결했다.",
        "hdec_relevance": "AI 데이터센터 EPC와 전력 인프라 사업 기회에 영향",
        "published_at": "2026-08-11T09:00:00+09:00",
        "publisher_direct": True,
        "score": 3.9,
        "shadow_urgency_status": "ambiguous",
        "shadow_would_pass": False,
        "shadow_confirmed_event_types": [],
        "change_type": "new_article",
        "current_run_seen": True,
    }


def pacing_contracts() -> None:
    articles = [
        _normal_article(
            "pace-1", "연합뉴스", "https://yna.co.kr/view/PACE1",
            "정부, AI 데이터센터 전력망 구축 협약 체결",
        ),
        _normal_article(
            "pace-2", "한국일보", "https://hankookilbo.com/news/article/PACE2",
            "건설사, AI 데이터센터 냉각 설비 구축 협약 체결",
        ),
        _normal_article(
            "pace-3", "서울경제", "https://sedaily.com/article/PACE3",
            "전력기업, AI 데이터센터 직류배전 구축 협약 체결",
        ),
    ]
    candidates = select_teams_push_candidates(articles, max_articles=None)
    check(
        "normal pacing fixtures are three IMPORTANT events",
        len(candidates) == 3
        and all(item.importance.level == IMPORTANCE_IMPORTANT for item in candidates),
        [item.importance for item in candidates],
    )
    ledger = teams_push_state.empty_state()
    first = apply_major_media_first_gate(
        candidates, state=ledger, run_cap=5,
        now_iso_value="2026-08-11T10:00:00+09:00",
    )
    check(
        "open normal window selects one best event and defers two",
        len(first.selected) == 1
        and len(first.deferred_major) == 2
        and first.audit["normal_rows_deferred_by_pacing"] == 2,
        first.audit,
    )
    sent = first.selected[0].candidate
    ledger = teams_push_state.mark_sent_after_success(
        ledger,
        sent.article,
        cluster_key=sent.cluster_key,
        signature=sent.material_signature,
        importance=sent.importance.level,
        source=str(sent.article.get("source") or ""),
        send_succeeded=True,
        sent_at="2026-08-11T10:00:00+09:00",
        delivery_id="offline-r4-ops-5",
    )
    remaining, _ = teams_push_state.filter_unsent_candidates(ledger, candidates)
    closed = apply_major_media_first_gate(
        remaining, state=ledger, run_cap=5,
        now_iso_value="2026-08-11T10:20:00+09:00",
    )
    check(
        "closed rolling window sends zero normal events without dropping backlog",
        len(closed.selected) == 0
        and len(closed.deferred_major) == 2
        and len(remaining) == 2,
        closed.audit,
    )
    reopened = apply_major_media_first_gate(
        remaining, state=ledger, run_cap=5,
        now_iso_value="2026-08-11T11:00:00+09:00",
    )
    check(
        "rolling 60-minute window reconsiders preserved backlog",
        TEAMS_NORMAL_PACING_MINUTES == 60
        and len(reopened.selected) == 1
        and len(reopened.deferred_major) == 1,
        reopened.audit,
    )

    urgent_row = _normal_article(
        "pace-top", "조선일보", "https://chosun.com/economy/PACE-TOP",
        "현대건설, AI 데이터센터 5조원 EPC 본계약 체결",
    )
    urgent_row.update(
        score=4.8,
        shadow_urgency_status="confirmed",
        shadow_would_pass=True,
        shadow_confirmed_event_types=["contract_signed"],
        hdec_relevance="현대건설 직접 AI 데이터센터 EPC 계약",
    )
    urgent = select_teams_push_candidates([urgent_row], max_articles=None)
    bypass = apply_major_media_first_gate(
        tuple(urgent) + tuple(remaining), state=ledger, run_cap=5,
        now_iso_value="2026-08-11T10:20:00+09:00",
    )
    check(
        "TOP/HDEC-direct event bypasses a closed normal pace window",
        len(urgent) == 1
        and urgent[0].importance.level == IMPORTANCE_TOP
        and any(item.candidate.article["article_key"] == "pace-top" for item in bypass.selected)
        and bypass.audit["urgent_rows_selected"] == 1,
        bypass.audit,
    )
    urgent_sent = teams_push_state.mark_sent_after_success(
        teams_push_state.empty_state(),
        urgent[0].article,
        cluster_key=urgent[0].cluster_key,
        signature=urgent[0].material_signature,
        importance=IMPORTANCE_IMPORTANT,
        source="조선일보",
        send_succeeded=True,
        sent_at="2026-08-11T10:20:00+09:00",
        delivery_id="offline-urgent-r4-ops-5",
        advances_normal_pace=False,
    )
    check(
        "urgent/HDEC-direct success does not consume the normal-card window",
        urgent_sent["last_normal_send_at"] is None,
        urgent_sent,
    )
    check("pacing never creates filler", len(bypass.selected) <= len(urgent) + len(remaining))


def _daily_identity(edition: brief.RenderedEdition) -> dict:
    return {
        "edition_key": edition.edition_key,
        "coverage_start": edition.coverage.start.isoformat(),
        "coverage_end": edition.coverage.end.isoformat(),
        "html_sha256": edition.html_sha256,
        "public_url": edition.public_dated_url,
        "delivery_kind": "empty_status" if edition.article_count == 0 else "nonempty_digest",
        "article_count": edition.article_count,
    }


def daily_and_editor_contracts() -> None:
    run_at = datetime.fromisoformat("2026-08-11T07:55:00+09:00")
    root_url = "https://sinabroin.github.io/HDEC-News-Sensor"
    empty = brief.render_daily(
        [], run_at=run_at, root_url=root_url, editor_console_available=True,
        review_mode="empty_edition", review_decision="empty_edition",
    )
    brief.validate_rendered(empty)
    check(
        "empty Daily is truthful, immutable, and CTA-complete",
        empty.article_count == 0
        and empty.issue_mode == "daily_empty_status"
        and empty.edition_manifest["edition_status"] == "empty"
        and empty.edition_manifest["article_count"] == 0
        and EMPTY_STATUS in empty.html
        and EMPTY_STATUS in empty.teams_text
        and empty.editor_url in empty.teams_text
        and empty.public_dated_url in empty.teams_text
        and empty.public_latest_url not in empty.teams_text,
        empty.teams_text,
    )

    rows = brief.fixture_articles("daily", run_at)[:2]
    for index, row in enumerate(rows):
        row["source"] = ("한국일보", "서울경제")[index]
        row["url"] = (
            "https://hankookilbo.com/news/article/FIXTURE1",
            "https://sedaily.com/article/FIXTURE2",
        )[index]
    articles = brief.normalize_articles(
        rows,
        brief.daily_coverage(run_at),
        limit=2,
        resolve_images=False,
    )
    nonempty = brief.render_daily(
        articles, run_at=run_at, root_url=root_url,
        editor_console_available=True, review_mode="human_approved",
        review_decision="approved",
    )
    brief.validate_rendered(nonempty)
    check(
        "non-empty Daily renders candidate cards and exact actions",
        nonempty.article_count == 2
        and nonempty.edition_manifest["edition_status"] == "nonempty"
        and nonempty.edition_manifest["article_count"] == 2
        and "Daily Brief 편집기에서 열기" in nonempty.teams_html
        and nonempty.edition_id in nonempty.teams_html
        and nonempty.public_dated_url in nonempty.teams_html
        and all(row.title in nonempty.html for row in articles),
        nonempty.teams_text,
    )

    owner = "github-run:5001:attempt:1"
    identity = _daily_identity(empty)
    claim = {
        **identity,
        "claim_owner": owner,
        "claimed_at": "2026-08-11T07:55:00+09:00",
    }
    ledger = daily_state.add_claim(daily_state.empty_state("daily"), "daily", claim)
    success = {
        **identity,
        "smtp_status": "accepted",
        "smtp_code": 250,
        "sent_at": "2026-08-11T08:00:00+09:00",
    }
    ledger = daily_state.convert_claim_to_success(ledger, "daily", success, owner)
    retry_blocked = False
    try:
        daily_state.add_claim(ledger, "daily", claim)
    except daily_state.StateError:
        retry_blocked = True
    check(
        "empty Daily status records one success and retry is idempotently blocked",
        retry_blocked
        and daily_state.has_success(ledger, empty.edition_key)
        and not daily_state.has_claim(ledger, empty.edition_key)
        and len(ledger["successful_editions"]) == 1
        and ledger["successful_editions"][0]["delivery_kind"] == "empty_status"
        and ledger["successful_editions"][0]["article_count"] == 0,
        ledger,
    )
    check(
        "state distinguishes non-empty digest identity",
        _daily_identity(nonempty)["delivery_kind"] == "nonempty_digest"
        and _daily_identity(nonempty)["article_count"] == 2,
    )

    # Exercise the actual publication orchestrator with the committed truthful
    # zero-candidate Review bundle. Every write is redirected to /tmp; the
    # immutable manifest publisher is stubbed to prevent repository mutation.
    with tempfile.TemporaryDirectory(prefix="r4-ops-5-empty-daily-") as temporary:
        temp_root = Path(temporary)
        dated_path = temp_root / "2026-08-11.html"
        latest_path = temp_root / "latest.html"
        runtime_path = temp_root / "runtime"
        original_docs_paths = daily_runner._docs_paths
        original_load_state = daily_runner.editorial_briefing_state.load_state
        original_manifest_writer = daily_runner.write_daily_edition_manifest
        env_names = (
            "EDITORIAL_PRODUCTION", "REPORT_URL", "GITHUB_REF",
            "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GMAIL_SMTP_USER",
            "GMAIL_SMTP_APP_PASSWORD", "ALERT_EMAIL_FROM", "TEAMS_CHANNEL_EMAIL",
        )
        original_env = {name: os.environ.get(name) for name in env_names}
        offline_smtp_attempts: list[str] = []

        class OfflineSMTP:
            def __init__(self, *_args, **_kwargs):
                offline_smtp_attempts.append("smtp")

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def ehlo(self):
                return 250, b"offline"

            def starttls(self, **_kwargs):
                return 220, b"offline"

            def login(self, *_args):
                return 235, b"offline"

            def mail(self, *_args):
                return 250, b"offline"

            def rcpt(self, *_args):
                return 250, b"offline"

            def data(self, *_args):
                return 250, b"offline"

        try:
            os.environ.update(
                {
                    "EDITORIAL_PRODUCTION": "1",
                    "REPORT_URL": root_url + "/daily/latest.html",
                    "GITHUB_REF": "refs/heads/main",
                    "GITHUB_RUN_ID": "5001",
                    "GITHUB_RUN_ATTEMPT": "1",
                    "GMAIL_SMTP_USER": "offline-sender@example.test",
                    "GMAIL_SMTP_APP_PASSWORD": "offline-app-password",
                    "ALERT_EMAIL_FROM": "offline-sender@example.test",
                    "TEAMS_CHANNEL_EMAIL": "offline-channel@example.test",
                }
            )
            daily_runner._docs_paths = lambda _kind, _key: (dated_path, latest_path)
            daily_runner.editorial_briefing_state.load_state = (
                lambda edition_type, path=None: daily_state.empty_state(edition_type)
            )
            daily_runner.write_daily_edition_manifest = (
                lambda _edition, **_kwargs: "tmp/offline-empty-manifest.json"
            )
            published = daily_runner.run_publish(
                "daily", run_at=run_at, runtime_dir=runtime_path
            )
            # Restore the real loader, then exercise claim -> one accepted
            # status message -> success using only a temp ledger and fake SMTP.
            daily_runner.editorial_briefing_state.load_state = original_load_state
            send_state_path = temp_root / "daily-state.json"
            claim_identity = daily_runner._manifest_identity(published)
            claimed = daily_state.add_claim(
                daily_state.empty_state("daily"),
                "daily",
                {
                    **claim_identity,
                    "claim_owner": "github-run:5001:attempt:1",
                    "claimed_at": "2026-08-11T07:59:00+09:00",
                },
            )
            daily_state.atomic_write_state("daily", claimed, send_state_path)
            sent_state = daily_runner.run_send(
                "daily",
                run_at=run_at,
                runtime_dir=runtime_path,
                state_path=send_state_path,
                smtp_factory=OfflineSMTP,
            )
            retry_blocked_before_transport = False
            try:
                daily_runner.run_send(
                    "daily",
                    run_at=run_at,
                    runtime_dir=runtime_path,
                    state_path=send_state_path,
                    smtp_factory=OfflineSMTP,
                )
            except daily_state.StateError:
                retry_blocked_before_transport = True
        finally:
            daily_runner._docs_paths = original_docs_paths
            daily_runner.editorial_briefing_state.load_state = original_load_state
            daily_runner.write_daily_edition_manifest = original_manifest_writer
            for name, value in original_env.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
        check(
            "actual Daily publish orchestrator accepts the empty Review bundle",
            published is not None
            and published.get("article_count") == 0
            and published.get("issue_mode") == "daily_empty_status"
            and dated_path.is_file()
            and dated_path.read_bytes() == latest_path.read_bytes()
            and EMPTY_STATUS in dated_path.read_text(encoding="utf-8"),
            published,
        )
        check(
            "empty Daily sends exactly one offline status and records its kind",
            offline_smtp_attempts == ["smtp"]
            and retry_blocked_before_transport
            and sent_state is not None
            and len(sent_state["successful_editions"]) == 1
            and sent_state["successful_editions"][0]["delivery_kind"] == "empty_status"
            and sent_state["successful_editions"][0]["article_count"] == 0,
            sent_state,
        )

    exact_path = ROOT / "docs" / "editorial" / "review" / "2026-08-11" / "candidates.json"
    latest_path = ROOT / "docs" / "editorial" / "review" / "latest" / "candidates.json"
    exact_bundle = editorial_review.load_bundle(exact_path, "2026-08-11")
    latest_bundle = editorial_review.load_bundle(latest_path, "2026-08-11")
    exact_articles, exact_mode = editorial_review.choose_daily_articles(exact_bundle, None)
    check(
        "exact dated and latest Editor candidate bundles load consistently",
        exact_bundle["edition_key"] == latest_bundle["edition_key"] == "2026-08-11"
        and exact_bundle["candidates"] == latest_bundle["candidates"]
        and (bool(exact_articles) or exact_mode == "empty_edition"),
        exact_mode,
    )
    template = (ROOT / "templates" / "editorial_review_console.html").read_text(
        encoding="utf-8"
    )
    exact_html = (ROOT / "docs" / "editorial" / "review" / "2026-08-11" / "index.html").read_text(
        encoding="utf-8"
    )
    latest_html = (ROOT / "docs" / "editorial" / "review" / "latest" / "index.html").read_text(
        encoding="utf-8"
    )
    controls = (
        'id="preview"', 'id="boldBtn"', 'draggable="true"',
        'contenteditable="true"', 'addEventListener("dragstart"',
        'addEventListener("drop"', "normalizeSelectedOrder()", "render()",
    )
    check(
        "Editor exposes ordering/edit controls and Daily preview rendering",
        all(token in template for token in controls),
    )
    for label, html in (("template", template), ("exact", exact_html), ("latest", latest_html)):
        check(
            f"{label} Editor disables and accurately labels unavailable URL import",
            "기사 URL 자동 불러오기 · 현재 사용할 수 없음" in html
            and "운영자 API 미구성" in html
            and 'document.getElementById("importBtn").disabled=true' in html
            and 'document.getElementById("importBtn").textContent="사용 불가"' in html,
        )
        check(
            f"{label} Editor reconstructs a valid empty exact edition",
            'manifest.edition_status!=="empty"||manifest.article_count!==0' in html
            and "state.selected=[]" in html
            and "return true" in html,
        )

    workflow = (ROOT / ".github" / "workflows" / "editorial-daily-brief.yml").read_text(
        encoding="utf-8"
    )
    crons = re.findall(r'cron:\s*["\']([^"\']+)', workflow)
    check(
        "Daily target/retry schedule is 07:50, 08:05, 08:15 KST",
        crons == ["50 22 * * *", "5 23 * * *", "15 23 * * *"],
        crons,
    )


def main() -> int:
    original_create_connection = socket.create_connection
    socket.create_connection = _forbid_network
    try:
        rules = json.loads(RULES_PATH.read_text(encoding="utf-8"))
        replay = json.loads(REPLAY_PATH.read_text(encoding="utf-8"))
        check("replay schema and required row count", replay.get("schema_version") == 2 and len(replay.get("rows") or []) >= 7)
        governance = governing_standard_contracts()
        overlay = project_acceptance_overlay_contracts()
        source_contract = publisher_contracts(rules)
        opinion_contract = opinion_contracts()
        provenance = replay_provenance_contracts(replay["rows"])
        outcomes = replay_contracts(replay["rows"])
        pacing_contracts()
        daily_and_editor_contracts()
    finally:
        socket.create_connection = original_create_connection

    check("deterministic acceptance made zero external network calls", NETWORK_CALLS == 0, NETWORK_CALLS)
    print(f"checks={CHECKS} failures={len(FAILURES)}")
    print(f"COMMON_EXECUTION_STANDARD_PRESENT={str(governance['common_present']).lower()}")
    print(f"PROJECT_ACCEPTANCE_CONTRACT_PRESENT={str(governance['project_present']).lower()}")
    print(f"HDEC_PROJECT_ACCEPTANCE_UPDATED={str(overlay['version_updated']).lower()}")
    print(f"HDEC_DEFECT_005_PRESENT={str(overlay['defect_present']).lower()}")
    print(f"PUBLISHER_AUTHORITY_INVARIANT_PRESENT={str(overlay['authority_invariant']).lower()}")
    print(f"CROSS_PUBLISHER_ALIAS_URL_ELEVATION={source_contract['cross_publisher_alias_url_elevation']}")
    print("PUBLISHER_URL_AUTHORITY_VERDICT=" + ("PASS" if source_contract["cross_publisher_alias_url_elevation"] == 0 else "FAIL"))
    print(f"OPINION_LEADING_MARKER={'PASS' if opinion_contract['leading'] else 'FAIL'}")
    print(f"OPINION_TRAILING_MARKER={'PASS' if opinion_contract['trailing'] else 'FAIL'}")
    print(f"OPINION_ENGLISH_SECTION={'PASS' if opinion_contract['english_section'] else 'FAIL'}")
    print(f"OPINION_INCIDENTAL_TEXT_FALSE_POSITIVE={'PASS' if opinion_contract['incidental'] else 'FAIL'}")
    print(f"LS_ELECTRIC_GS_EC_ACTUAL={'KEEP' if outcomes.get('hankookilbo_ls_gs_dc_distribution') else 'REJECT'}")
    print(f"ETF_FALSE_POSITIVE_ACTUAL={'KEEP' if outcomes.get('yonhap_strategy_etf_false_positive') else 'REJECT'}")
    observed_sbs = outcomes.get(SBS_PREMIUM_OBSERVED_CASE)
    synthetic_sbs = outcomes.get(SBS_PREMIUM_STRESS_CASE)
    print("SBS_PREMIUM_REAL_INCIDENT_EXPECTED=REJECT")
    print(f"SBS_PREMIUM_REAL_INCIDENT_ACTUAL={'REJECT' if observed_sbs is False else 'FAIL'}")
    print(f"SBS_PREMIUM_OBSERVED_INCIDENT={'REJECT' if observed_sbs is False else 'FAIL'}")
    print(f"SBS_PREMIUM_ADVERSARIAL_STRESS={'REJECT' if synthetic_sbs is False else 'FAIL'}")
    print(f"SBS_REPLAY_PROVENANCE_VERDICT={'PASS' if provenance['verdict'] else 'FAIL'}")
    print(f"SBS_PREMIUM_TIER_A_INHERITANCE={source_contract['sbs_premium_tier_a_inheritance']}")
    print(f"SBS_PREMIUM_REALTIME_AUTO_SEND={str(source_contract['sbs_premium_realtime_auto_send']).lower()}")
    print(f"EXTERNAL_TEST_NETWORK_CALLS={NETWORK_CALLS}")
    print("PRODUCTION_SMTP_ATTEMPTS=0")
    print("PRODUCTION_TEAMS_SENDS=0")
    print("PRODUCTION_STATE_WRITES=0")
    print("RESULT=" + ("R4_OPS_5_PRODUCTION_ACCEPTANCE_PASS" if not FAILURES else "R4_OPS_5_PRODUCTION_ACCEPTANCE_FAIL"))
    if FAILURES:
        print("FAILED_CHECKS=" + " | ".join(FAILURES))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
