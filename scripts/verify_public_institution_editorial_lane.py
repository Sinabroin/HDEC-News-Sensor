#!/usr/bin/env python3
"""R4-R8 deterministic public-institution routing integration verifier.

Offline only: no collectors, senders, state writers, workflow dispatches,
Hermes calls, or repository mutations are reachable from this verifier.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import (  # noqa: E402
    editorial_briefings,
    editorial_review,
    public_institution_routing as routing,
    teams_ai_push,
)

CHECKS = 0
FAILURES: list[str] = []
BRIEF_TEMPLATE_SHA256 = "3cdcbf4891ad24c52a9465fa6cacd8757246fc6b33959c60a190405c321e6206"


def check(label: str, condition: object, detail: object = "") -> None:
    global CHECKS
    CHECKS += 1
    if condition:
        print(f"PASS {label}")
    else:
        FAILURES.append(label)
        print(f"FAIL {label}" + (f" :: {detail}" if detail else ""))


def public_row(
    article_id: str,
    title: str,
    source: str,
    url: str,
    summary: str,
    *,
    event_cluster_id: str = "",
) -> dict:
    return {
        "id": article_id,
        "article_id": article_id,
        "article_key": article_id,
        "title": title,
        "source": source,
        "url": url,
        "publisher_url": url,
        "publisher_direct": True,
        "published_at": "2026-08-04T01:00:00+00:00",
        "snippet": summary,
        "summary": summary,
        "source_metadata": {
            "provider": "publisher_direct_rss",
            "query": "AI 데이터센터 인공지능 정책",
        },
        **({"event_cluster_id": event_cluster_id} if event_cluster_id else {}),
    }


def teams_row(row: dict, *, score: float = 4.7) -> dict:
    return {
        **row,
        "score": score,
        "shadow_urgency_status": "confirmed",
        "shadow_would_pass": True,
        "shadow_confirmed_event_types": ["construction_confirmed"],
        "change_type": "new_article",
        "hdec_relevance": "AI 데이터센터 EPC와 전력 인프라 사업기회에 직접 영향",
    }


def main() -> int:
    mois = public_row(
        "mois-public-data",
        "공공데이터 활용, 이제 AI에게 물어보세요 ‘AI기반 공공데이터포털’ 서비스 개시",
        "행정안전부",
        "https://www.mois.go.kr/frt/bbs/type010/commonSelectBoardArticle.do?bbsId=x&nttId=1",
        "AI 기반 공공데이터 포털 서비스를 개시하고 클라우드 인프라를 확충했다.",
    )
    msit = public_row(
        "msit-ax360",
        "인공지능 전환 일괄 지원포털 AX360 서비스 개시",
        "과학기술정보통신부",
        "https://www.msit.go.kr/bbs/view.do?bbsSeqNo=94&nttSeqNo=2",
        "기업의 AI 전환을 지원하는 AX 원스톱 지원포털을 개시했다.",
    )
    archives = public_row(
        "archives-training",
        "보존·복원부터 AI 활용 디지털 아카이빙까지 국가기록원, 오만 맞춤형 교육 개최",
        "행정안전부",
        "https://www.mois.go.kr/frt/bbs/type010/commonSelectBoardArticle.do?bbsId=x&nttId=3",
        "AI 활용 디지털 아카이빙 교육을 개최했다.",
    )
    computing = public_row(
        "national-ai-center",
        "과기정통부, 국가AI컴퓨팅센터 첫 삽",
        "과학기술정보통신부",
        "https://www.msit.go.kr/bbs/view.do?bbsSeqNo=94&nttSeqNo=4",
        "국가 AI 컴퓨팅센터 건설을 착공해 GPU 인프라 구축을 시작했다.",
    )
    ai_law = public_row(
        "eu-ai-act",
        "EU AI Act obligations enter into force",
        "European Union",
        "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=fixture",
        "Mandatory enterprise AI compliance obligations are effective.",
    )
    campaign = public_row(
        "generic-campaign",
        "행정안전부, 생활 속 AI 체험 캠페인 개최",
        "행정안전부",
        "https://www.mois.go.kr/frt/bbs/type010/commonSelectBoardArticle.do?nttId=5",
        "시민 대상 인공지능 홍보 캠페인을 개최했다.",
    )

    decisions = {row["id"]: routing.classify(row) for row in (
        mois, msit, archives, computing, ai_law, campaign
    )}
    for key in ("mois-public-data", "msit-ax360", "archives-training", "generic-campaign"):
        decision = decisions[key]
        check(f"{key} is verified official", decision.is_verified_official)
        check(f"{key} is in operator public lane", decision.editorial_lane == routing.LANE_PUBLIC)
        check(f"{key} is not main by default", not decision.main_surface_eligible)
        check(f"{key} is not Teams eligible", not decision.teams_alert_eligible)
    check("MOIS public-data portal is conditional Brief supply", decisions["mois-public-data"].tni_brief_eligible)
    check("MOIS public-data portal maps to 기술정보", decisions["mois-public-data"].final_category == "기술정보")
    check("AX360 is conditional Brief supply", decisions["msit-ax360"].tni_brief_eligible)
    check("AX360 maps by substance to 투자·산업", decisions["msit-ax360"].final_category == "투자·산업")
    check("training event is not forced into Brief", not decisions["archives-training"].tni_brief_eligible)
    check("training event has no invented category", not decisions["archives-training"].final_category)
    check("National Archives identity resolves as public agency", decisions["archives-training"].public_institution_type == "public_agency" and decisions["archives-training"].official_source_name == "국가기록원")
    check("generic campaign is not filler", not decisions["generic-campaign"].tni_brief_eligible)

    for key in ("national-ai-center", "eu-ai-act"):
        decision = decisions[key]
        check(f"{key} is promoted to main", decision.main_surface_eligible)
        check(f"{key} is Teams eligible", decision.teams_alert_eligible)
        check(f"{key} is Brief eligible", decision.tni_brief_eligible)
        check(f"{key} is Report-topic eligible", decision.tni_report_topic_eligible)
        check(f"{key} carries explicit promotion reason", decision.promotion_reason.startswith("material_condition_proven:"))

    # Identity alone and generated commentary alone cannot establish authority
    # or promotion.
    unverified = dict(mois)
    unverified["url"] = unverified["publisher_url"] = "https://example.org/public-ai"
    unverified["why_it_matters"] = "binding national AI law now applies"
    unverified_decision = routing.classify(unverified)
    check("unregistered domain never becomes verified official", not unverified_decision.is_verified_official)
    check("unregistered institution is fail-closed from Teams", not unverified_decision.teams_alert_eligible)
    generated_only = dict(mois)
    generated_only["why_it_matters"] = "국가 AI 전략 확정 및 대규모 예산 승인"
    generated_decision = routing.classify(generated_only)
    check("generated why-it-matters cannot promote", not generated_decision.main_surface_eligible)

    # Teams: default public rows are rejected after ordinary gates; promoted
    # material rows remain subject to those gates and can become candidates.
    default_teams, default_audit = teams_ai_push.select_teams_push_candidates_with_audit(
        [teams_row(mois)]
    )
    promoted_teams, promoted_audit = teams_ai_push.select_teams_push_candidates_with_audit(
        [teams_row(computing)]
    )
    check("default public article produces zero Teams candidates", not default_teams)
    check("Teams audit sees one non-promoted public row", default_audit["non_promoted_public_candidate_count"] == 1)
    check("promoted computing-center article reaches Teams", len(promoted_teams) == 1)
    check("promoted Teams candidate preserves routing fields", bool(promoted_teams) and promoted_teams[0].tni_report_topic_eligible)
    check("Teams promoted-public audit is explicit", promoted_audit["promoted_public_candidate_count"] == 1)

    # Daily/Weekly ranking and same-event representation. The primary-ten
    # media card wins; the ministry release stays operator-visible support.
    media = public_row(
        "media-center",
        "연합뉴스, 국가AI컴퓨팅센터 착공…GPU 인프라 구축",
        "연합뉴스",
        "https://www.yna.co.kr/view/AKR202608040001",
        "국가 AI 컴퓨팅센터가 착공해 GPU 인프라 구축을 시작했다.",
        event_cluster_id="national-ai-center-start",
    )
    official_duplicate = {**computing, "event_cluster_id": "national-ai-center-start"}
    coverage = editorial_briefings.CoverageWindow(
        datetime(2026, 8, 3, tzinfo=timezone.utc),
        datetime(2026, 8, 5, tzinfo=timezone.utc),
    )
    daily_audit = editorial_briefings.SelectionAuditCounters()
    daily = editorial_briefings.normalize_articles(
        [official_duplicate, media, mois],
        coverage,
        limit=6,
        resolve_images=False,
        selection_mode=editorial_briefings.SELECTION_MODE_EDITORIAL_PRIORITY,
        selection_audit=daily_audit,
        edition_type="daily",
    )
    check("Daily headline remains a main candidate", bool(daily) and daily[0].editorial_lane == routing.LANE_MAIN)
    # R4-R12 §2 — reversed by operator directive: an official article without a
    # material confirmed event (the AI-portal service launch) must never enter
    # the AI Daily candidate pool, however AI-central its subject is. Official
    # source status is authority, never relevance.
    check(
        "R4-R12 §2: official row without material confirmed event never enters the Daily pool",
        all(item.source != "행정안전부" for item in daily),
    )
    check(
        "R4-R12 §2: official gate accounting is exposed for the Daily run",
        daily_audit.official_rows_seen >= 1
        and daily_audit.official_ai_central_rows >= 1
        and daily_audit.official_selected_rows == 0,
    )
    check("same-event primary-ten representative wins", bool(daily) and daily[0].source == "연합뉴스")
    check("duplicate cluster count is audited", daily_audit.duplicate_official_media_event_clusters == 1)
    check("official duplicate remains supporting evidence in audit", "national-ai-center" in daily_audit.public_supporting_evidence_ids)

    operator_audit = editorial_briefings.SelectionAuditCounters()
    operator_rows = editorial_briefings.normalize_articles(
        [official_duplicate, media, mois, archives, campaign],
        coverage,
        limit=12,
        resolve_images=False,
        selection_mode=editorial_briefings.SELECTION_MODE_EDITORIAL_PRIORITY,
        selection_audit=operator_audit,
        edition_type="daily",
        operator_review=True,
    )
    supporting = [item for item in operator_rows if item.supporting_evidence_only]
    check("operator review preserves official duplicate evidence", len(supporting) == 1 and supporting[0].source == "과학기술정보통신부")
    # R4-R12 §2 — gate-rejected officials are no longer operator-visible pool
    # candidates; the official gate counters expose them instead.
    check(
        "operator audit exposes official gate accounting",
        operator_audit.official_rows_seen >= 4
        and operator_audit.official_unrelated_domain_rejected_rows >= 2
        and operator_audit.public_institution_lane_count >= 1,
    )

    weekly_audit = editorial_briefings.SelectionAuditCounters()
    weekly = editorial_briefings.normalize_articles(
        [computing, media],
        coverage,
        limit=12,
        resolve_images=False,
        selection_mode=editorial_briefings.SELECTION_MODE_EDITORIAL_PRIORITY,
        selection_audit=weekly_audit,
        edition_type="weekly",
    )
    check("Weekly remains capable of selecting promoted strategic public material", any(item.tni_report_topic_eligible for item in weekly))
    check("public article maps only to existing Brief taxonomy", all(item.category in editorial_review.CATEGORY_ORDER for item in weekly))

    # Final visible products stay immutable; routing metadata never creates a
    # fourth shell node.
    weekly_template_path = ROOT / "templates" / "editorial_weekly_tni.html"
    weekly_template = weekly_template_path.read_text(encoding="utf-8")
    check("Brief template SHA is unchanged", hashlib.sha256(weekly_template_path.read_bytes()).hexdigest() == BRIEF_TEMPLATE_SHA256)
    check("Brief taxonomy remains exactly three", editorial_review.CATEGORY_ORDER == ("투자·산업", "기업동향", "기술정보"))
    check("Brief shell has no operator public tab", "공공기관·정책" not in weekly_template)
    check("Brief shell has no fourth category", all(value not in weekly_template for value in ("정책·공공", "정부자료", "Official Sources")))

    console = (ROOT / "templates" / "editorial_review_console.html").read_text(encoding="utf-8")
    check("operator console has all/main/public lane controls", all(value in console for value in (">전체<", ">주요 후보<", ">공공기관·정책<")))
    check("operator console exposes public source metadata", all(value in console for value in ("공식 소스", "소스 유형", "승격 사유", "권장 최종 분류")))
    check("operator console exposes product eligibility", all(value in console for value in ("Teams", "Brief", "Report 주제")))
    check("operator console requires written placement reason", "배치 변경에는 사유가 필요합니다" in console)
    check("operator console exports placement fields", all(value in console for value in ("human_placement_override", "human_placement_reason", "final_surface")))

    shadow_runner = (
        ROOT / "scripts" / "run_public_institution_no_send_shadow.py"
    ).read_text(encoding="utf-8")
    check(
        "no-send shadow invokes the live collector exactly once",
        shadow_runner.count("collect_live_article_bundle()") == 1,
    )
    check(
        "no-send shadow cannot reach any sender",
        all(
            token not in shadow_runner
            for token in (
                "deliver_email_message(",
                "send_scheduled_telegram(",
                "send_teams",
                "dispatch_workflow",
            )
        ),
    )
    check(
        "no-send shadow is /tmp-only and Hermes-disabled",
        'Path("/tmp/d7ak6e-r4r8-public-institution-shadow.json")'
        in shadow_runner
        and "Hermes must be disabled" in shadow_runner,
    )
    check(
        "no-send shadow emits every required safety counter",
        all(
            field in shadow_runner
            for field in (
                '"smtp_attempts": 0',
                '"teams_sends": 0',
                '"telegram_sends": 0',
                '"production_state_writes": 0',
                '"workflow_dispatches": 0',
                '"hermes_live_calls": 0',
                '"repository_variable_changes": 0',
                '"profile_activation_writes": 0',
            )
        ),
    )

    schema = json.loads((ROOT / "data" / "editorial_learning" / "schema.json").read_text(encoding="utf-8"))
    required_placement = set(schema["decision_record_fields"]["placement_fields_required_for_editor_feedback"])
    check("learning schema preserves every required placement field", required_placement == {
        "source_class", "editorial_lane", "public_institution_type",
        "main_surface_eligible", "teams_alert_eligible", "tni_brief_eligible",
        "tni_report_topic_eligible", "default_surface", "final_surface",
        "final_category", "promotion_reason", "human_placement_override",
        "human_placement_reason",
    })
    check("public status is not an evidence level", "public_institution" not in schema["evidence_levels"])

    try:
        routing.validate_placement_override(
            decisions["mois-public-data"].metadata(),
            {"final_surface": routing.SURFACE_MAIN},
        )
    except ValueError:
        reason_required = True
    else:
        reason_required = False
    check("silent human placement override fails closed", reason_required)
    approved_override = routing.validate_placement_override(
        decisions["mois-public-data"].metadata(),
        {
            "final_surface": routing.SURFACE_MAIN,
            "human_placement_override": True,
            "human_placement_reason": "법적 시행 영향이 별도 확인됨",
        },
    )
    check("written human placement override is auditable", approved_override == (True, routing.SURFACE_MAIN, "기술정보"))

    print(
        f"checks={CHECKS} failures={len(FAILURES)} "
        "smtp_attempts=0 teams_sends=0 telegram_sends=0 "
        "production_state_writes=0 workflow_dispatches=0 hermes_live_calls=0 "
        "repository_variable_changes=0 profile_activation_writes=0"
    )
    if FAILURES:
        for label in FAILURES:
            print(f"FAILED: {label}")
        return 1
    print("PUBLIC_INSTITUTION_EDITORIAL_LANE=PASS")
    print("RESULT=D7-AK-6E_R4_R8_PUBLIC_INSTITUTION_LANE_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
