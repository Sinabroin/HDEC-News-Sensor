#!/usr/bin/env python3
"""D7-AK-6E R4-R9A — Teams major-media-first source gate regression verifier.

Covers the full rules §10 fixture matrix offline (network calls 0, real SMTP
0, production state writes 0), including the three exact observed production
deliveries that motivated the repair:

* http://www.newsworker.co.kr/news/articleView.html?idxno=439527 (뉴스워커)
* https://www.techm.kr/news/articleView.html?idxno=153960 (테크M)
* https://www.epnc.co.kr/news/articleView.html?idxno=405148 (테크월드)

The three URLs are publisher-direct and safe; the defect was editorial — Teams
delivered specialist-only supply instead of prioritizing major media. Under
the gate they are captured as regression fixtures and must never again be
immediately selected. All delivery runs reuse the production sender
``deliver()`` with the injected fake SMTP recorder from the production
verifier, temp state files only.
"""

from __future__ import annotations

import sys
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
    FIXTURE_TEAMS_CHANNEL,
)
from app.teams_ai_push import (  # noqa: E402
    TEAMS_SPECIALIST_HOLDBACK_MINUTES,
    TEAMS_SPECIALIST_MAX_PER_BATCH,
    select_teams_push_candidates,
)
from app.teams_push_state import (  # noqa: E402
    HELD_SPECIALISTS_KEY,
    empty_state,
    load_state,
    observe_held_specialist,
    save_state,
)

CHECKS = 0
FAILURES: list[str] = []

NOW = "2026-08-04T17:00:00+09:00"
AGED_FIRST_SEEN = "2026-08-04T14:30:00+09:00"  # 150 minutes before NOW
FRESH_FIRST_SEEN = "2026-08-04T16:30:00+09:00"  # 30 minutes before NOW
MAJOR_FIXTURE_DOMAINS = {
    "연합뉴스": "yna.co.kr",
    "MBC": "imbc.com",
    "KBS": "kbs.co.kr",
    "SBS": "news.sbs.co.kr",
    "동아일보": "donga.com",
    "한겨레": "hani.co.kr",
    "경향신문": "khan.co.kr",
}


def check(name: str, ok: bool, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    if ok:
        print(f"PASS: {name}")
    else:
        FAILURES.append(name)
        print(f"FAIL: {name}" + (f" — {detail[:400]}" if detail else ""))


# ---------------------------------------------------------------------------
# Observed-production regression fixtures (§2). Metadata resolved from the
# committed accepted ledger (data/teams_push_state.json as of 2026-08-04):
# normalized publisher, canonical URL, importance, and event identity. These
# articles pass every pre-gate hard gate — only the source gate stops them.
# ---------------------------------------------------------------------------
def observed_newsworker(**overrides):
    base = _article(
        article_key="obs-newsworker-439527",
        title="해남군 국가 AI 컴퓨팅센터 착공…AI 3대 강국 시동",
        summary="해남군에서 국가 AI 컴퓨팅센터(AI 데이터센터) 착공이 확정돼 전력 인프라 수요가 확대된다.",
        hdec_relevance="AI 데이터센터·전력 인프라 발주 환경에 영향",
        source="뉴스워커",
        url="http://www.newsworker.co.kr/news/articleView.html?idxno=439527",
        score=4.2,
        shadow_confirmed_event_types=["construction_started"],
    )
    base.update(overrides)
    return base


def observed_techm(**overrides):
    base = _article(
        article_key="obs-techm-153960",
        title="프리즈미안, 전기자재 업체 애트코어 인수 추진…북미 전력망 대응",
        summary="AI 데이터센터 전력 수요 확대에 대응해 북미 전력망 기자재 기업 인수를 추진한다.",
        hdec_relevance="AI 데이터센터 전력 인프라 공급망에 영향",
        source="테크M",
        url="https://www.techm.kr/news/articleView.html?idxno=153960",
        score=4.0,
        shadow_confirmed_event_types=["acquisition_announced"],
    )
    base.update(overrides)
    return base


def observed_epnc(**overrides):
    base = _article(
        article_key="obs-epnc-405148",
        title="엘리스그룹, 온수냉각 적용 B300 AI 데이터센터 구축",
        summary="엘리스그룹이 온수냉각을 적용한 B300 기반 AI 데이터센터 구축을 공개했다.",
        hdec_relevance="AI 데이터센터 냉각·전력 설계 방식에 영향",
        source="테크월드",
        url="https://www.epnc.co.kr/news/articleView.html?idxno=405148",
        score=4.6,
        shadow_confirmed_event_types=["launch_announced"],
    )
    base.update(overrides)
    return base


def primary(key: str, title: str, source: str = "연합뉴스", **overrides):
    base = _article(
        article_key=key,
        title=title,
        summary=f"{title} — 공식 확정 내용.",
        source=source,
        url=f"https://{MAJOR_FIXTURE_DOMAINS[source]}/major/{key}",
        score=4.7,
    )
    base.update(overrides)
    return base


def deliver(tmp: Path, articles, state_path: Path, *, send=True,
            statuses=(250,) * 8, max_articles=5, now=NOW):
    recorder = _SMTPRecorder(statuses)
    artifact = _write(
        tmp / f"gate-artifact-{abs(hash(str(sorted(a['article_key'] for a in articles)))) % 10**8}-{send}.json",
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


def seed_holds(articles, first_seen: str, state_path: Path) -> None:
    """Seed aged/fresh held records for already-policy-eligible articles."""
    state = empty_state()
    for article in articles:
        candidates = select_teams_push_candidates([article], max_articles=None)
        assert len(candidates) == 1, article["article_key"]
        state = observe_held_specialist(
            state, article,
            cluster_key=candidates[0].cluster_key,
            source=str(article.get("source") or ""),
            source_tier="neutral",
            holdback_reason="holdback_active",
            fallback_eligible=False,
            now=first_seen,
        )
    save_state(state, state_path)


def main() -> int:
    import tempfile

    check("R4-R9D canonical constants: holdback metadata retained (120), "
          "automatic specialist fallback removed (max_per_batch == 0)",
          TEAMS_SPECIALIST_HOLDBACK_MINUTES == 120
          and TEAMS_SPECIALIST_MAX_PER_BATCH == 0)

    with tempfile.TemporaryDirectory(prefix="hdec-r4r9a-gate-") as raw:
        tmp = Path(raw)

        # (1)+(5) all three exact observed URLs together: immediate selected 0.
        trio = [observed_newsworker(), observed_techm(), observed_epnc()]
        state = tmp / "state-observed-trio.json"
        summary, rec = deliver(tmp, trio, state)
        check("observed trio: every article passes the pre-gate policy",
              summary["alert_policy_eligible"] == 3, str(summary))
        check("observed trio: immediate selected is zero",
              summary["selected"] == 0 and rec.attempts == [], str(summary))
        check("observed trio: all three are held by the source gate",
              summary["teams_specialist_held_rows"] == 3
              and summary["teams_specialist_selected_rows"] == 0, str(summary))
        check("observed trio: counters reconcile",
              summary["counters_reconciled"] is True)
        saved = load_state(state)
        held = saved.get(HELD_SPECIALISTS_KEY) or {}
        check("observed trio: held records exist without any ledger entry",
              len(held) == 3 and not saved["article_ids"], str(sorted(held)))

        # (2)(3)(4) single-publisher supplies: immediate selected 0 each.
        for label, fixture in (
            ("newsworker", observed_newsworker()),
            ("techm", observed_techm()),
            ("epnc", observed_epnc()),
        ):
            solo_state = tmp / f"state-solo-{label}.json"
            summary, rec = deliver(tmp, [fixture], solo_state)
            check(f"{label}-only supply: immediate selected 0",
                  summary["selected"] == 0 and rec.attempts == []
                  and summary["teams_specialist_held_rows"] == 1, str(summary))

        # (6) all three aged past the holdback but only IMPORTANT: selected 0.
        # Non-major confirmed event types keep importance at IMPORTANT (the
        # observed fixtures otherwise carry major-confirmed tokens → TOP).
        aged_important = [
            observed_newsworker(
                score=4.2, shadow_confirmed_event_types=["industry_update"]),
            observed_techm(
                score=4.0, shadow_confirmed_event_types=["industry_update"]),
            observed_epnc(
                score=4.2, shadow_confirmed_event_types=["industry_update"]),
        ]
        aged_state = tmp / "state-aged-important.json"
        seed_holds(aged_important, AGED_FIRST_SEEN, aged_state)
        summary, rec = deliver(tmp, aged_important, aged_state)
        check("aged 150min but only IMPORTANT: selected 0",
              summary["selected"] == 0 and rec.attempts == [], str(summary))
        check("aged 150min: holdback expiry is visible in the audit",
              summary["teams_specialist_holdback_expired_rows"] == 3
              and summary["teams_specialist_fallback_eligible_rows"] == 0,
              str(summary))

        # fresh unique TOP specialist at 30 minutes: hold, select 0.
        top_techm = observed_techm(
            article_key="obs-techm-top",
            title="테크M 단독: 현대건설, AI 데이터센터 EPC 계약 체결",
            summary="현대건설이 AI 데이터센터 EPC 계약을 체결했다고 단독 보도했다.",
            hdec_relevance="현대건설 직접 영향",
            url="https://www.techm.kr/news/articleView.html?idxno=880010",
            score=4.8,
        )
        fresh_state = tmp / "state-fresh-top.json"
        seed_holds([top_techm], FRESH_FIRST_SEEN, fresh_state)
        summary, rec = deliver(tmp, [top_techm], fresh_state)
        check("unique TOP specialist at 30 minutes: held, selected 0",
              summary["selected"] == 0
              and summary["teams_specialist_fallback_eligible_rows"] == 0,
              str(summary))

        # (7) R4-R9D — a unique TechM TOP aged 150min with direct HDEC relevance
        # is the exact case the old policy auto-sent. The strict source gate now
        # refuses it: specialist selected 0, no send attempt, hold retained,
        # ledger untouched. Prefer zero delivery over a specialist-only card.
        fallback_state = tmp / "state-eligible-top.json"
        seed_holds([top_techm], AGED_FIRST_SEEN, fallback_state)
        summary, rec = deliver(tmp, [top_techm], fallback_state)
        check("aged unique TOP TechM with HDEC relevance: specialist selected 0",
              summary["selected"] == 0 and summary["delivered_count"] == 0
              and rec.attempts == []
              and summary["teams_specialist_selected_rows"] == 0
              and summary["specialist_rows_selected"] == 0, str(summary))
        check("aged unique TOP TechM: no automatic fallback eligibility remains",
              summary["teams_specialist_fallback_eligible_rows"] == 0
              and summary["specialist_automatic_fallback_removed"] is True,
              str(summary))
        check("aged unique TOP TechM: audited as held, never a fallback card",
              not summary["selected_source_audit"]
              and summary["specialist_rows_automatic_rejected"] == 1,
              str(summary["selected_source_audit"]))
        fallback_saved = load_state(fallback_state)
        check("aged unique TOP TechM: hold retained, never enters the ledger",
              len(fallback_saved.get(HELD_SPECIALISTS_KEY) or {}) == 1
              and not fallback_saved["article_ids"], str(fallback_saved))

        # (8) NewsWorker stays blocked from automatic fallback even aged TOP.
        top_newsworker = observed_newsworker(
            article_key="obs-nw-top",
            title="뉴스워커 단독: 현대건설, 국가 AI 컴퓨팅센터 시공 계약 체결",
            summary="현대건설이 국가 AI 컴퓨팅센터 시공 계약을 체결했다.",
            hdec_relevance="현대건설 직접 영향",
            url="http://www.newsworker.co.kr/news/articleView.html?idxno=880011",
            score=4.8,
        )
        nw_state = tmp / "state-newsworker-top.json"
        seed_holds([top_newsworker], AGED_FIRST_SEEN, nw_state)
        summary, rec = deliver(tmp, [top_newsworker], nw_state)
        check("NewsWorker aged TOP: automatic fallback stays blocked",
              summary["selected"] == 0 and rec.attempts == []
              and summary["teams_specialist_fallback_eligible_rows"] == 0,
              str(summary))

        # (9) one primary + three specialists: primary selected, specialists 0.
        nine = [
            primary("p-nine", "정부, AI 데이터센터 전력망 투자 확정"),
            observed_techm(), observed_epnc(),
            _article(article_key="spec-it-chosun",
                     title="오라클, 클라우드 AI 데이터센터 신규 착공",
                     source="IT조선",
                     url="https://it.chosun.com/news/articleView.html?idxno=1"),
        ]
        nine_state = tmp / "state-one-primary.json"
        summary, rec = deliver(tmp, nine, nine_state)
        check("one primary + three specialists: primary only",
              summary["selected"] == 1
              and summary["selected_primary_10_rows"] == 1
              and summary["selected_specialist_rows"] == 0
              and summary["teams_specialist_held_rows"] == 3, str(summary))
        check("unused major-media capacity is not filled with specialists",
              summary["selected"] == 1 and summary["max_articles_cap"] == 5)

        # (10) two primary + three secondary: major-media ordering preserved.
        ten = [
            primary("p-yna", "연합뉴스: AI 데이터센터 국가 투자 확정", "연합뉴스"),
            primary("p-sbs", "SBS: AI 전력망 인프라 투자 계약 체결", "SBS"),
            primary("s-donga", "동아일보: AI 데이터센터 착공 확정", "동아일보"),
            primary("s-hani", "한겨레: AI 데이터센터 전력 계약 체결", "한겨레"),
            primary("s-khan", "경향신문: AI 인프라 투자 승인", "경향신문"),
        ]
        ten_state = tmp / "state-ordering.json"
        summary, rec = deliver(tmp, ten, ten_state)
        audit_rows = summary["selected_source_audit"]
        check("two primary + three secondary: all five selected",
              summary["selected"] == 5 and summary["delivered_count"] == 5
              and summary["selected_primary_10_rows"] == 2
              and summary["selected_secondary_3_rows"] == 3, str(summary))
        check("major-media ordering: primary tier precedes secondary, rank ascending",
              [(row["publisher_tier"], row["publisher_rank"]) for row in audit_rows]
              == [("primary_10", 1), ("primary_10", 10),
                  ("secondary_3", 1), ("secondary_3", 2), ("secondary_3", 3)],
              str(audit_rows))

        # (11) specialist first, same-event primary later: primary wins and
        # the held specialist becomes supporting evidence.
        event_specialist = observed_techm(
            article_key="evt-spec",
            cluster_key="evt-shared-1",
        )
        event_primary = primary(
            "evt-prim", "정부, 북미 전력망 AI 데이터센터 투자 확정",
            cluster_key="evt-shared-1",
        )
        seq_state = tmp / "state-sequence.json"
        summary, rec = deliver(tmp, [event_specialist], seq_state)
        check("run 1: specialist arrives first and is held",
              summary["selected"] == 0
              and summary["teams_specialist_held_rows"] == 1, str(summary))
        summary, rec = deliver(tmp, [event_primary, event_specialist], seq_state)
        check("run 2: same-event primary wins with one Teams card",
              summary["selected"] == 1 and summary["delivered_count"] == 1
              and summary["selected_primary_10_rows"] == 1, str(summary))
        seq_saved = load_state(seq_state)
        seq_held = list((seq_saved.get(HELD_SPECIALISTS_KEY) or {}).values())
        check("run 2: held specialist is marked replaced by the major card",
              len(seq_held) == 1
              and seq_held[0]["replaced_by_major_media"].startswith("teams_ai_push:")
              and seq_held[0]["representative_publisher"] == "연합뉴스"
              and seq_held[0]["fallback_eligible"] is False, str(seq_held))
        check("run 2: replacement is counted in the audit",
              summary["teams_specialist_replaced_by_major_rows"] == 1,
              str(summary))
        summary, rec = deliver(tmp, [event_specialist], seq_state)
        check("run 3: replaced specialist never re-sends after major coverage",
              summary["selected"] == 0 and summary["already_sent"] == 1,
              str(summary))

        # (12) same-event specialist and primary in one run: one primary card.
        twelve_state = tmp / "state-same-run.json"
        summary, rec = deliver(
            tmp,
            [
                observed_epnc(article_key="same-spec", cluster_key="evt-shared-2"),
                primary("same-prim", "KBS: B300 AI 데이터센터 구축 공개", "KBS",
                        cluster_key="evt-shared-2"),
            ],
            twelve_state,
        )
        check("same-event specialist+primary in one run: one primary card",
              summary["selected"] == 1 and summary["delivered_count"] == 1
              and summary["duplicate_event_rows"] == 1
              and summary["selected_primary_10_rows"] == 1, str(summary))

        # (13) a distinct held TOP specialist does not displace an available
        # primary when the batch has no room.
        displaced_state = tmp / "state-no-displacement.json"
        seed_holds([top_techm], AGED_FIRST_SEEN, displaced_state)
        summary, rec = deliver(
            tmp,
            [primary("p-room", "MBC: AI 데이터센터 전력 계약 체결", "MBC"), top_techm],
            displaced_state,
            max_articles=1,
        )
        check("held TOP specialist never displaces an available primary",
              summary["selected"] == 1
              and summary["selected_primary_10_rows"] == 1
              and summary["teams_specialist_selected_rows"] == 0, str(summary))
        check("undisplaced specialist is never fallback-eligible (fallback removed)",
              summary["teams_specialist_fallback_eligible_rows"] == 0
              and summary["specialist_rows_selected"] == 0
              and summary["specialist_automatic_fallback_removed"] is True,
              str(summary))

        # (14) a specialist cannot bypass AI centrality or hard exclusions —
        # and a hard-rejected article never creates a held record.
        stock_state = tmp / "state-hard-exclusion.json"
        summary, rec = deliver(
            tmp,
            [observed_techm(
                article_key="spec-stock",
                title="테크M: AI 데이터센터 테마주 수혜주 급등 전망",
                summary="관련주가 급등할 것이라는 전망이 나온다.",
                url="https://www.techm.kr/news/articleView.html?idxno=880012",
            )],
            stock_state,
        )
        check("specialist cannot bypass hard exclusions",
              summary["alert_policy_eligible"] == 0 and summary["selected"] == 0,
              str(summary))
        check("hard-rejected specialist never creates a held record",
              not stock_state.exists())

        # (15) dry-run: no sends, no state writes, holdback still evaluated.
        dry_state = tmp / "state-dry-run.json"
        summary, rec = deliver(tmp, trio, dry_state, send=False)
        check("dry-run sends nothing and writes no state",
              summary["mode"] == "dry_run_no_send"
              and summary["SMTP_attempted"] == 0 and rec.attempts == []
              and not dry_state.exists(), str(summary))
        check("dry-run still reports the gate partition",
              summary["teams_specialist_held_rows"] == 3
              and summary["source_gate_rejected_rows"] == 0)

        # (16) R4-R9D — a held TOP specialist is never selected, so there is no
        # fallback send to attempt or retry: zero SMTP attempts, hold retained
        # across runs, ledger never gains a specialist entry.
        retry_state = tmp / "state-retry.json"
        seed_holds([top_techm], AGED_FIRST_SEEN, retry_state)
        summary, rec = deliver(tmp, [top_techm], retry_state, statuses=(550,))
        check("strict gate: held specialist is never attempted for send",
              summary["SMTP_attempted"] == 0 and summary["delivered_count"] == 0
              and summary["teams_specialist_selected_rows"] == 0
              and rec.attempts == [], str(summary))
        retry_saved = load_state(retry_state)
        check("strict gate: hold retained, ledger untouched",
              len(retry_saved.get(HELD_SPECIALISTS_KEY) or {}) == 1
              and not retry_saved["article_ids"], str(retry_saved))
        summary, rec = deliver(tmp, [top_techm], retry_state, statuses=(250,))
        check("strict gate: still zero specialist selected on the next run",
              summary["delivered_count"] == 0
              and summary["teams_specialist_selected_rows"] == 0
              and summary["specialist_rows_selected"] == 0, str(summary))

        # (17) batch and source counters reconcile in a mixed worst case.
        mixed = [
            primary("mx-p1", "연합뉴스: AI 데이터센터 투자 확정", "연합뉴스"),
            primary("mx-s1", "동아일보: AI 전력망 투자 계약 체결", "동아일보"),
            observed_newsworker(article_key="mx-nw"),
            observed_techm(article_key="mx-tm",
                           url="https://www.techm.kr/news/articleView.html?idxno=880013"),
            _article(article_key="mx-neutral",
                     title="블룸버그: OpenAI, AI 데이터센터 투자 확정",
                     source="블룸버그",
                     url="https://publisher.example.test/bbg/1"),
        ]
        mixed_state = tmp / "state-mixed-reconcile.json"
        summary, rec = deliver(tmp, mixed, mixed_state)
        check("mixed batch: counters reconcile end-to-end",
              summary["counters_reconciled"] is True, str(summary))
        check("mixed batch: partition counters are exact",
              summary["selected"] == 2
              and summary["teams_specialist_held_rows"] == 2
              and summary["source_gate_rejected_rows"] == 1
              and summary["selected_primary_10_rows"] == 1
              and summary["selected_secondary_3_rows"] == 1, str(summary))

    print(f"checks={CHECKS} failures={len(FAILURES)}")
    if FAILURES:
        for name in FAILURES:
            print(f"FAILED: {name}")
        return 1
    print("RESULT=D7-AK-6E_R4R9A_TEAMS_MAJOR_MEDIA_GATE_PASS")
    print(
        "observed_urls_covered=3 network_calls=0 real_smtp_connections=0 "
        "teams_sends=0 telegram_sends=0 production_state_writes=0 "
        f"specialist_holdback_minutes={TEAMS_SPECIALIST_HOLDBACK_MINUTES} "
        f"specialist_max_per_batch={TEAMS_SPECIALIST_MAX_PER_BATCH}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
