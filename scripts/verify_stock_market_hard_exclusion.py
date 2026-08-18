#!/usr/bin/env python3
"""D7-AK-6E R4-R9B — Teams stock-market hard-exclusion regression verifier.

Covers the rules §4-§7 fixture matrix offline (network calls 0, real SMTP 0,
production state writes 0), including the exact observed production delivery
that motivated the repair:

* https://www.newspim.com/news/view/20260805000052 (뉴스핌)
  "[5일 중국증시] AI 랠리 훈풍 이어질까…순환매 장세 지속 전망" — dominant
  subject is stock-market movement / AI-theme investing, not a material AI
  industry event for Hyundai E&C executives.

Plus the ten observed long-tail publisher deliveries captured as §7
regression fixtures, the §5 HDEC material-event exception, the §3 SafeLinks
wrapper rules, and the §6 counter reconciliation contract.

All delivery runs reuse the production sender ``deliver()`` with the injected
fake SMTP recorder from the production verifier; state files are temp-only.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import send_teams_ai_push as sender  # noqa: E402
from verify_teams_ai_push_production import (  # noqa: E402
    _SMTPRecorder,
    _article,
    _fixture_credentials,
    _payload,
    _write,
)
from app import publisher_direct, source_priority, source_quality  # noqa: E402
from app.teams_ai_push import (  # noqa: E402
    ImportanceDecision,
    TeamsPushCandidate,
    TopicDecision,
    apply_major_media_first_gate,
    evaluate_stock_market_gate,
    evaluate_teams_push_policy,
    select_teams_push_candidates_with_audit,
)
from app.teams_push_state import (  # noqa: E402
    derive_event_cluster_key,
    empty_state,
    material_signature,
    observe_held_specialist,
    save_state,
)

CHECKS = 0
FAILURES: list[str] = []

NOW = "2026-08-05T09:00:00+09:00"
LATER_130M = "2026-08-05T11:10:00+09:00"  # 130 minutes past the holdback window
AGED_FIRST_SEEN = "2026-08-05T06:00:00+09:00"  # 180 minutes before NOW

NEWSPIM_URL = "https://www.newspim.com/news/view/20260805000052"
NEWSPIM_TITLE = "[5일 중국증시] AI 랠리 훈풍 이어질까…순환매 장세 지속 전망"

#: §7 — the ten observed production deliveries (publisher, canonical URL,
#: deterministic AI-relevant fixture title). NewsPim is stock-market dominant;
#: the exact TechM title is independently rejected by the R4-OPS-8 bounded
#: semantic gate; the remaining source-ineligible rows never auto-send.
OBSERVED_DELIVERIES = (
    ("녹색경제신문", "https://www.greened.kr/news/articleView.html?idxno=346822",
     "국내 그룹사, AI 데이터센터 전력 설비 증설 확정"),
    ("뉴스퀘스트", "https://www.newsquest.co.kr/news/articleView.html?idxno=271082",
     "정부, AI 데이터센터 전력망 연계 지침 시행"),
    ("오토데일리", "https://www.autodaily.co.kr/news/articleView.html?idxno=546410",
     "완성차 그룹, 휴머노이드 로봇 생산라인 구축 착수"),
    ("경남신문", "https://www.knnews.co.kr/news/articleView.php?idxno=1547991",
     "경남 지역 AI 데이터센터 조성 착공"),
    ("우먼타임스", "https://www.womentimes.co.kr/news/articleView.html?idxno=104970",
     "AI 데이터센터 냉각 설비 공급계약 체결"),
    ("비즈트리뷴", "https://www.biztribune.co.kr/news/articleView.html?idxno=356951",
     "빅테크, AI 인프라 대규모 투자 확정"),
    ("테크월드", "https://www.epnc.co.kr/news/articleView.html?idxno=405148",
     "온수냉각 적용 B300 AI 데이터센터 구축"),
    ("뉴스핌", NEWSPIM_URL, NEWSPIM_TITLE),
    ("뉴스워커", "https://www.newsworker.co.kr/news/articleView.html?idxno=439527",
     "국가 AI 컴퓨팅센터 착공…전력 인프라 확대"),
    ("테크M", "https://www.techm.kr/news/articleView.html?idxno=153960",
     "전력망 기자재 업체 인수 추진…AI 데이터센터 대응"),
)

EXPECTED_LANES = {
    "녹색경제신문": "never_automatic",
    "뉴스퀘스트": "never_automatic",
    "오토데일리": "never_automatic",
    "경남신문": "never_automatic",
    "우먼타임스": "never_automatic",
    "비즈트리뷴": "never_automatic",
    "테크월드": "specialist_holdback",
    "뉴스핌": "never_automatic",
    "뉴스워커": "specialist_holdback",
    "테크M": "specialist_holdback",
}


def check(name: str, ok: bool, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    if ok:
        print(f"PASS: {name}")
    else:
        FAILURES.append(name)
        print(f"FAIL: {name}" + (f" — {detail[:400]}" if detail else ""))


def newspim_article(**overrides):
    base = _article(
        article_key="obs-newspim-20260805000052",
        title=NEWSPIM_TITLE,
        summary=(
            "중국 증시에서 AI 관련주 랠리가 이어질지 관심이 쏠린다. "
            "순환매 장세 속 차익실현 매물과 투자심리가 변수다."
        ),
        source="뉴스핌",
        url=NEWSPIM_URL,
        score=4.5,
        shadow_confirmed_event_types=[],
    )
    base.update(overrides)
    return base


def deliver(tmp: Path, articles, state_path: Path, *, send=True,
            statuses=(250,) * 12, max_articles=5, now=NOW):
    recorder = _SMTPRecorder(statuses)
    artifact = _write(
        tmp / (
            "stock-artifact-"
            f"{abs(hash(str(sorted(a['article_key'] for a in articles)))) % 10**8}"
            f"-{send}-{now[-8:-6]}{now[-5:-3]}.json"
        ),
        _payload(articles),
    )
    summary = sender.deliver(
        artifact_path=artifact,
        state_path=state_path,
        credentials=_fixture_credentials(),
        should_send=send,
        smtp_factory=recorder,
        max_articles=max_articles,
        now_iso_value=now,
    )
    return summary, recorder


def manual_candidate(article) -> TeamsPushCandidate:
    """Directly-constructed TOP candidate that bypasses the policy layer."""
    return TeamsPushCandidate(
        article=article,
        topic=TopicDecision(True, topic_key="ai_infra", topic_label="AI 인프라"),
        importance=ImportanceDecision(
            True, level="top", label="TOP", reason="fixture", hdec_direct=True
        ),
        cluster_key=derive_event_cluster_key(article, "ai_infra"),
        material_signature=material_signature(article),
        delivery_category="AI 데이터센터",
    )


def verify_newspim_classification(tmp: Path) -> None:
    print("\n== 1. NewsPim observed delivery — §4 hard exclusion ==")
    art = newspim_article()
    gate = evaluate_stock_market_gate(art)
    check("NewsPim: stock-market dominant subject", gate.dominant,
          repr(gate))
    check("NewsPim: HDEC direct material event is false",
          not gate.hdec_material_event)
    check("NewsPim: gate hard-rejects (teams_stock_market_eligible false)",
          not gate.eligible and gate.exclusion_reason ==
          "stock_market_dominant_no_hdec_material_event", repr(gate))
    evaluation = evaluate_teams_push_policy(art)
    check("NewsPim: Teams policy ineligible", not evaluation.eligible,
          evaluation.rejection_reason)
    check(
        "NewsPim: decision fields stamped on the row",
        evaluation.article.get("stock_market_dominant_subject") is True
        and evaluation.article.get("hdec_direct_material_event") is False
        and evaluation.article.get("teams_stock_market_eligible") is False
        and bool(evaluation.article.get("stock_market_exclusion_reason"))
        and evaluation.article.get("stock_market_exception_reason") == "",
        repr({k: evaluation.article.get(k) for k in (
            "stock_market_dominant_subject", "hdec_direct_material_event",
            "teams_stock_market_eligible", "stock_market_exclusion_reason",
            "stock_market_exception_reason")}),
    )

    state_path = tmp / "newspim-now.json"
    summary, recorder = deliver(tmp, [newspim_article()], state_path)
    check("NewsPim: selected 0 · immediate 0 at delivery time",
          summary["selected"] == 0
          and summary["teams_immediate_major_rows"] == 0,
          repr(summary["selected"]))
    check("NewsPim: no SMTP attempt", recorder.attempted == 0
          if hasattr(recorder, "attempted") else summary["SMTP_attempted"] == 0,
          repr(summary["SMTP_attempted"]))
    check("NewsPim: counted as stock-market hard rejection",
          summary["stock_market_dominant_rows"] == 1
          and summary["stock_market_hard_rejected_rows"] == 1,
          repr({k: summary[k] for k in (
              "stock_market_dominant_rows",
              "stock_market_hard_rejected_rows")}))

    later_path = tmp / "newspim-130m.json"
    summary_late, _ = deliver(
        tmp, [newspim_article(article_key="obs-newspim-late")],
        later_path, now=LATER_130M,
    )
    check("NewsPim: still selected 0 after 120+ minutes (age cannot rescue)",
          summary_late["selected"] == 0, repr(summary_late["selected"]))

    # Aged TOP candidate constructed directly — the gate boundary itself must
    # refuse holdback and fallback for a stock-dominant article.
    aged_state = empty_state()
    aged_article = newspim_article(article_key="obs-newspim-aged")
    aged_state = observe_held_specialist(
        aged_state,
        aged_article,
        cluster_key=derive_event_cluster_key(aged_article, "ai_infra"),
        source="뉴스핌",
        source_tier="specialist",
        holdback_reason="holdback_active",
        fallback_eligible=False,
        now=AGED_FIRST_SEEN,
    )
    batch = apply_major_media_first_gate(
        [manual_candidate(aged_article)],
        state=aged_state,
        run_cap=5,
        now_iso_value=LATER_130M,
    )
    check(
        "NewsPim: aged TOP direct candidate is gate-rejected, never held or"
        " fallback-selected",
        len(batch.selected) == 0
        and len(batch.held) == 0
        and len(batch.fallback_selected) == 0
        and len(batch.rejected) == 1
        and batch.rejected[0].gate.reason == "stock_market_hard_excluded"
        and batch.rejected[0].gate.fallback_blocked
        and batch.audit["stock_market_gate_rejected_rows"] == 1,
        repr(batch.audit),
    )


def verify_generic_stock_tiers(tmp: Path) -> None:
    print("\n== 2. Generic market coverage — every tier stays unsendable ==")
    cases = (
        ("primary-ten rally", _article(
            article_key="stock-primary-rally",
            title="AI 테마주 랠리 지속…코스피 신고가 경신",
            summary="AI 테마주 중심의 랠리가 이어지며 코스피가 신고가를 새로 썼다.",
            source="매일경제",
            url="https://mk.co.kr/stock-rally",
            shadow_confirmed_event_types=[],
        )),
        ("secondary-three target price", _article(
            article_key="stock-secondary-target",
            title="증권사, AI 반도체주 목표주가 일제 상향",
            summary="주요 증권사가 AI 반도체 종목 목표주가를 올려 잡았다.",
            source="동아일보",
            url="https://donga.com/stock-target",
            shadow_confirmed_event_types=[],
        )),
        ("specialist beneficiary stocks", _article(
            article_key="stock-specialist-beneficiary",
            title="AI 수혜주 5선…지금 담아야 할 종목은",
            summary="AI 수혜주로 꼽히는 종목과 투자전략을 정리했다.",
            source="테크M",
            url="https://www.techm.kr/news/articleView.html?idxno=990001",
            shadow_confirmed_event_types=[],
        )),
    )
    for label, art in cases:
        gate = evaluate_stock_market_gate(art)
        evaluation = evaluate_teams_push_policy(art)
        check(f"{label}: dominant + hard-rejected + policy-ineligible",
              gate.dominant and not gate.eligible and not evaluation.eligible,
              f"gate={gate!r} reason={evaluation.rejection_reason}")
        state_path = tmp / f"tier-{art['article_key']}.json"
        summary, _ = deliver(tmp, [dict(art)], state_path)
        check(f"{label}: selected 0", summary["selected"] == 0,
              repr(summary["selected"]))
        if label.startswith("specialist"):
            check(
                f"{label}: held 0 and fallback blocked from the gate",
                summary["teams_specialist_held_rows"] == 0
                and summary["stock_market_fallback_blocked_rows"] == 1,
                repr({k: summary[k] for k in (
                    "teams_specialist_held_rows",
                    "stock_market_fallback_blocked_rows")}),
            )


def hdec_contract_article(**overrides):
    base = _article(
        article_key="hdec-epc-contract",
        title="현대건설, 3조원 AI 데이터센터 EPC 계약 체결",
        summary=(
            "현대건설이 3조원 규모 AI 데이터센터 EPC 계약을 체결했다. "
            "발표 직후 주가도 상승했다."
        ),
        source="연합뉴스",
        url="https://yna.co.kr/hdec-epc-contract",
        score=4.8,
        shadow_confirmed_event_types=["contract_signed"],
    )
    base.update(overrides)
    return base


def verify_hdec_exception(tmp: Path) -> None:
    print("\n== 3. §5 HDEC material-event exception ==")
    art = hdec_contract_article()
    gate = evaluate_stock_market_gate(art)
    check("HDEC EPC contract: secondary stock reference does not dominate",
          not gate.dominant and gate.hdec_material_event and gate.eligible,
          repr(gate))
    evaluation = evaluate_teams_push_policy(art)
    check("HDEC EPC contract: eligibility determined by the contract event",
          evaluation.eligible
          and evaluation.article.get("stock_market_exception_reason", "")
          .startswith("hdec_material_event:"),
          f"reason={evaluation.rejection_reason} "
          f"exc={evaluation.article.get('stock_market_exception_reason')!r}")
    state_path = tmp / "hdec-epc.json"
    summary, _ = deliver(tmp, [hdec_contract_article()], state_path)
    check("HDEC EPC contract: delivered through the normal immediate lane",
          summary["selected"] == 1 and summary["SMTP_accepted"] >= 1,
          repr({k: summary[k] for k in ("selected", "SMTP_accepted")}))
    check("HDEC EPC contract: exception counter recorded",
          summary["stock_market_hdec_exception_rows"] == 1
          and summary["stock_market_hard_rejected_rows"] == 0,
          repr({k: summary[k] for k in (
              "stock_market_hdec_exception_rows",
              "stock_market_hard_rejected_rows")}))

    beneficiary = _article(
        article_key="hdec-beneficiary-only",
        title="현대건설 주가 급등…증권가 AI 수혜주 지목",
        summary="증권가가 현대건설을 AI 수혜주로 지목하며 주가가 급등했다.",
        source="연합뉴스",
        url="https://yna.co.kr/hdec-beneficiary",
        shadow_confirmed_event_types=[],
    )
    gate_b = evaluate_stock_market_gate(beneficiary)
    check("HDEC beneficiary-only: stock move is not itself the event",
          gate_b.dominant and not gate_b.hdec_material_event
          and not gate_b.eligible, repr(gate_b))
    summary_b, _ = deliver(tmp, [dict(beneficiary)], tmp / "hdec-bene.json")
    check("HDEC beneficiary-only: selected 0", summary_b["selected"] == 0,
          repr(summary_b["selected"]))


def verify_no_bypass(tmp: Path) -> None:
    print("\n== 4. §6-8 — the exception can never bypass another gate ==")
    non_ai = _article(
        article_key="hdec-nuclear-order",
        title="현대건설 주가 상승…사우디 원전 공사 수주 확정",
        summary="현대건설이 사우디 원전 공사를 수주 확정했다. 주가도 상승했다.",
        source="연합뉴스",
        url="https://yna.co.kr/hdec-nuclear",
        shadow_confirmed_event_types=["contract_signed"],
    )
    evaluation = evaluate_teams_push_policy(non_ai)
    check(
        "AI centrality still applies: non-AI HDEC event stays ineligible"
        " even with the §5 exception",
        not evaluation.eligible
        and bool(evaluation.article.get("stock_market_exception_reason")),
        f"reason={evaluation.rejection_reason}",
    )

    neutral_publisher = hdec_contract_article(
        article_key="hdec-epc-neutral-source",
        source="뉴스핌",
        url=NEWSPIM_URL.replace("0052", "0099"),
    )
    summary, _ = deliver(
        tmp, [neutral_publisher], tmp / "hdec-neutral.json"
    )
    check(
        "source gate still applies: neutral publisher stays unselected even"
        " with the §5 exception",
        summary["selected"] == 0
        and summary["stock_market_hdec_exception_rows"] == 1,
        repr({k: summary[k] for k in (
            "selected", "stock_market_hdec_exception_rows")}),
    )

    ledger_state = tmp / "ledger-state.json"
    first, _ = deliver(tmp, [hdec_contract_article()], ledger_state)
    second, _ = deliver(tmp, [hdec_contract_article()], ledger_state)
    check(
        "accepted ledger still applies: the same article is never re-sent",
        first["selected"] == 1 and second["selected"] == 0
        and second["already_sent"] >= 1,
        repr({"first": first["selected"], "second": second["selected"],
              "already_sent": second["already_sent"]}),
    )


def verify_counter_reconciliation(tmp: Path) -> None:
    print("\n== 5. §6 — exclusion counters reconcile ==")
    batch = [
        newspim_article(article_key="ctr-newspim"),
        _article(
            article_key="ctr-rally", title="AI 테마주 랠리 지속…코스피 신고가",
            summary="AI 테마주 랠리.", source="매일경제",
            url="https://mk.co.kr/ctr-rally",
            shadow_confirmed_event_types=[],
        ),
        _article(
            article_key="ctr-target", title="증권사, AI 반도체주 목표주가 상향",
            summary="목표주가 상향.", source="동아일보",
            url="https://donga.com/ctr-target",
            shadow_confirmed_event_types=[],
        ),
        _article(
            article_key="ctr-beneficiary", title="AI 수혜주 5선…담아야 할 종목",
            summary="AI 수혜주 정리.", source="테크M",
            url="https://www.techm.kr/news/articleView.html?idxno=990002",
            shadow_confirmed_event_types=[],
        ),
        hdec_contract_article(article_key="ctr-hdec-exception"),
        _article(
            article_key="ctr-hdec-beneficiary",
            title="현대건설 주가 급등…AI 수혜주 지목",
            summary="주가 급등.", source="연합뉴스",
            url="https://yna.co.kr/ctr-bene",
            shadow_confirmed_event_types=[],
        ),
        _article(article_key="ctr-clean"),
    ]
    _candidates, audit = select_teams_push_candidates_with_audit(
        batch, max_articles=None
    )
    expected = {
        "stock_market_dominant_rows": 5,
        "stock_market_hard_rejected_rows": 5,
        "stock_market_hdec_exception_rows": 1,
        "stock_market_fallback_blocked_rows": 1,
    }
    check("selection audit counters are exact",
          {k: audit[k] for k in expected} == expected,
          repr({k: audit[k] for k in expected}))
    dominant_exceptions = sum(
        1 for art in batch
        if (gate := evaluate_stock_market_gate(art)).dominant
        and gate.hdec_material_event
    )
    check(
        "reconciliation: dominant == hard_rejected + dominant-exception rows",
        audit["stock_market_dominant_rows"]
        == audit["stock_market_hard_rejected_rows"] + dominant_exceptions,
        repr(audit),
    )
    check(
        "reconciliation: fallback_blocked is a subset of hard rejections",
        audit["stock_market_fallback_blocked_rows"]
        <= audit["stock_market_hard_rejected_rows"],
        repr(audit),
    )
    summary, _ = deliver(tmp, [dict(a) for a in batch], tmp / "ctr-state.json")
    check(
        "sender summary carries the same counters",
        {k: summary[k] for k in expected} == expected
        and summary["stock_market_gate_rejected_rows"] == 0,
        repr({k: summary.get(k) for k in expected}),
    )


def verify_observed_deliveries(tmp: Path) -> None:
    print("\n== 6. §7 — ten observed long-tail deliveries ==")
    for source, url, _title in OBSERVED_DELIVERIES:
        policy = source_priority.teams_delivery_source_policy(source, url)
        check(
            f"{source}: delivery lane is {EXPECTED_LANES[source]}",
            str(policy["teams_lane"]) == EXPECTED_LANES[source],
            repr(policy),
        )
        if source == "뉴스워커":
            check("뉴스워커: remains fallback-blocked",
                  bool(policy["fallback_blocked"]), repr(policy))
        check(
            f"{source}: not globally excluded from editorial products",
            not source_quality.is_excluded(source),
            "editorial availability must survive the Teams gate",
        )

    articles = [
        _article(
            article_key=f"obs-{index}",
            title=title,
            summary=f"{title} — 확정 발표 내용.",
            source=source,
            url=url,
            shadow_confirmed_event_types=(
                [] if source == "뉴스핌" else ["confirmed_event"]
            ),
        )
        for index, (source, url, title) in enumerate(OBSERVED_DELIVERIES)
    ]
    summary, recorder = deliver(tmp, articles, tmp / "observed-state.json")
    check(
        "observed batch: selected 0 — publisher-direct alone never grants"
        " immediate delivery",
        summary["selected"] == 0 and summary["SMTP_attempted"] == 0,
        repr({k: summary[k] for k in ("selected", "SMTP_attempted")}),
    )
    check(
        "observed batch: no immediate major rows and unused capacity stays"
        " empty",
        summary["teams_immediate_major_rows"] == 0
        and summary["deferred_due_to_cap"] == 0,
        repr({k: summary[k] for k in (
            "teams_immediate_major_rows", "deferred_due_to_cap")}),
    )
    check(
        "observed batch: only semantic-qualified specialists enter holdback",
        summary["teams_specialist_held_rows"] == 2
        and summary["teams_specialist_selected_rows"] == 0,
        repr({k: summary[k] for k in (
            "teams_specialist_held_rows", "teams_specialist_selected_rows")}),
    )
    check(
        "observed batch: NewsPim is the one stock-market hard rejection",
        summary["stock_market_dominant_rows"] == 1
        and summary["stock_market_hard_rejected_rows"] == 1
        and summary["stock_market_fallback_blocked_rows"] == 0,
        repr({k: summary[k] for k in (
            "stock_market_dominant_rows", "stock_market_hard_rejected_rows",
            "stock_market_fallback_blocked_rows")}),
    )


def verify_safelinks_rules() -> None:
    print("\n== 7. §3 — SafeLinks wrapper is never a publisher ==")
    wrapped = (
        "https://teams.public.onecdn.static.microsoft/v1/redirect?url="
        "https%3A%2F%2Fwww.newspim.com%2Fnews%2Fview%2F20260805000052"
    )
    check(
        "wrapper host classifies as security_intermediary, not a news source",
        publisher_direct.portal_provider(wrapped) == "security_intermediary",
        publisher_direct.portal_provider(wrapped),
    )
    check(
        "wrapper URL never becomes canonical article identity",
        publisher_direct.normalize_publisher_canonical_url(wrapped) == "",
    )
    unwrapped = publisher_direct.unwrap_security_intermediary_url(wrapped)
    check(
        "wrapper destination extracts to the publisher-direct article URL",
        unwrapped == NEWSPIM_URL,
        unwrapped,
    )
    check(
        "destination publisher classifies as the real publisher policy",
        source_priority.teams_delivery_source_policy("뉴스핌", unwrapped)[
            "teams_lane"
        ] == "never_automatic",
    )
    outlook = (
        "https://nam12.safelinks.protection.outlook.com/?url="
        "https%3A%2F%2Fwww.techm.kr%2Fnews%2FarticleView.html%3Fidxno%3D153960"
        "&data=05%7C02"
    )
    check(
        "Outlook SafeLinks unwraps to the publisher destination",
        publisher_direct.unwrap_security_intermediary_url(outlook)
        == "https://www.techm.kr/news/articleView.html?idxno=153960",
        publisher_direct.unwrap_security_intermediary_url(outlook),
    )
    check(
        "a non-wrapper URL never unwraps",
        publisher_direct.unwrap_security_intermediary_url(NEWSPIM_URL) == "",
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="stock-gate-") as raw:
        tmp = Path(raw)
        verify_newspim_classification(tmp)
        verify_generic_stock_tiers(tmp)
        verify_hdec_exception(tmp)
        verify_no_bypass(tmp)
        verify_counter_reconciliation(tmp)
        verify_observed_deliveries(tmp)
        verify_safelinks_rules()

    print()
    print(f"checks={CHECKS} failures={len(FAILURES)}")
    result = "PASS" if not FAILURES else "FAIL"
    print(f"RESULT=D7-AK-6E_R4R9B_STOCK_MARKET_HARD_EXCLUSION_{result}")
    print(
        "observed_urls_covered=10 network_calls=0 real_smtp_connections=0"
        " teams_sends=0 telegram_sends=0 production_state_writes=0"
    )
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    sys.exit(main())
