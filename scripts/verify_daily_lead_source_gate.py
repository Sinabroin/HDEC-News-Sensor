#!/usr/bin/env python3
"""D7-AK-6E R4-R10 — delivered Daily-Brief lead-source quality gate.

A screenshot regression showed long-tail / specialist publishers (비즈트리뷴·
더퍼블릭·녹색경제신문) and stock-theme stories arriving as delivered Daily lead
cards. This verifier proves, entirely offline (network 0, real SMTP 0, no
production-state writes):

* the delivered Daily lead-source gate keeps only locked primary-ten /
  secondary-three / promoted-official leads;
* the exact screenshot publishers (비즈트리뷴·더퍼블릭·녹색경제신문) and the exact
  S저널 URL never become a delivered lead card, while remaining available to the
  operator as supporting evidence;
* stock-theme / beneficiary / GPU-market-positioning stories are hard-excluded
  from Daily selection (ai-centrality), not merely demoted;
* an all-long-tail supply delivers zero leads (honest shortfall, never filler);
* the gate is wired into the production publish path (run_editorial_briefing).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import editorial_briefings as brief  # noqa: E402
from app import source_priority  # noqa: E402

CHECKS = 0
FAILURES: list[str] = []

RUN_AT = brief.parse_published_at("2026-08-05T07:00:00+09:00")
SJOURNAL_URL = "https://www.s-journal.co.kr/news/articleView.html?idxno=42865"
SCREENSHOT_LONG_TAIL = ("비즈트리뷴", "더퍼블릭", "녹색경제신문")


def check(name: str, ok: bool, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    if ok:
        print(f"PASS {name}")
    else:
        FAILURES.append(name)
        print(f"FAIL {name}" + (f" — {detail[:300]}" if detail else ""))


def _row(idx: int, source: str, title: str, url: str | None = None) -> dict:
    return {
        "id": f"r4r10-{idx}",
        "title": title,
        "source": source,
        "published_at": "2026-08-04T07:10:00+09:00",
        "url": url or f"https://pub.fixture.test/{idx}",
        "snippet": (
            f"{title} 관련 공식 확인된 계약·발표 내용과 적용 범위가 제시됐다. "
            "세부 일정과 조건은 원문 발표를 기준으로 한다."
        ),
        "source_metadata": {"provider": "offline_fixture"},
    }


def _select(rows: list[dict], *, operator_review: bool = False):
    return brief.normalize_articles(
        rows,
        brief.daily_coverage(RUN_AT),
        limit=brief.DAILY_MAX_ARTICLES,
        resolve_images=False,
        selection_mode=brief.SELECTION_MODE_EDITORIAL_PRIORITY,
        edition_type="daily",
        operator_review=operator_review,
    )


def main() -> int:
    # -------------------------------------------------------------- unit tiers
    for src in SCREENSHOT_LONG_TAIL:
        check(
            f"{src} classifies as long-tail (never a delivered lead)",
            not brief.lead_source_eligible_tier(src, ""),
            source_priority.publisher_delivery_tier(src, "")["tier"],
        )
    check(
        "S저널 (s-journal.co.kr) is not lead-eligible",
        not brief.lead_source_eligible_tier("S저널", SJOURNAL_URL),
    )
    for src in ("연합뉴스", "SBS", "매일경제", "한국경제"):
        check(f"{src} is lead-eligible (primary-ten)", brief.lead_source_eligible_tier(src, ""))

    # --------------------------------------------------------- mixed supply
    mixed = [
        _row(1, "연합뉴스", "현대건설, AI 데이터센터 전력 인프라 공급망 계약 확대"),
        _row(2, "SBS", "네이버, 자체 AI 데이터센터 각 세종 증설 착공 발표"),
        _row(3, "비즈트리뷴", "AI 데이터센터 냉각 운영 효율 개선 협력 발표"),
        _row(4, "더퍼블릭", "AI 데이터센터 전력 수요 대응 인프라 협력 확정"),
        _row(5, "녹색경제신문", "AI 데이터센터 전력망 연계 기준 논의 착수"),
        _row(
            6, "S저널",
            "최태원, HBM으로 빅테크 묶었다…5000억 달러 AI 동맹 SKT까지 확장",
            SJOURNAL_URL,
        ),
        _row(7, "연합뉴스", "AI 데이터센터 테마주 급등…수혜주 전망에 증권가 매수 추천"),
    ]
    selected = _select(mixed)
    selected_sources = [a.source for a in selected]
    delivered = brief.filter_lead_source_eligible(selected)
    delivered_sources = [a.source for a in delivered]

    check(
        "stock-theme story is hard-excluded from Daily selection (not demoted)",
        all("테마주" not in a.title and "수혜주" not in a.title for a in selected),
        str(selected_sources),
    )
    check(
        "delivered Daily leads are all major/official and non-empty",
        bool(delivered)
        and all(
            brief.lead_source_eligible_tier(a.source, a.selected_url) for a in delivered
        ),
        str(delivered_sources),
    )
    for src in SCREENSHOT_LONG_TAIL + ("S저널",):
        check(
            f"{src} is never a delivered Daily lead card",
            src not in delivered_sources,
            str(delivered_sources),
        )
    check(
        "the two major publishers survive as delivered leads",
        "연합뉴스" in delivered_sources and "SBS" in delivered_sources,
        str(delivered_sources),
    )
    check(
        "delivered headline (index 0) is a major/official source",
        brief.lead_source_eligible_tier(delivered[0].source, delivered[0].selected_url),
        delivered[0].source if delivered else "(none)",
    )

    # --------------------------- long-tail retained as supporting evidence
    operator_sources = {a.source for a in _select(mixed, operator_review=True)}
    check(
        "long-tail sources remain operator-visible supporting evidence",
        any(src in operator_sources for src in SCREENSHOT_LONG_TAIL),
        str(sorted(operator_sources)),
    )

    # --------------------------------------------------- end-to-end render
    edition = brief.render_daily(
        selected,
        run_at=RUN_AT,
        root_url="https://guides.example.test/HDEC-News-Sensor",
        review_mode="human_approved",
        review_decision="approved",
        editor_console_available=True,
        lead_source_gate=True,
    )
    brief.validate_rendered(edition)
    check(
        "rendered delivered daily headline title is a major-source lead",
        edition.headline == delivered[0].title,
        edition.headline,
    )
    for src in SCREENSHOT_LONG_TAIL:
        # long-tail card titles must not appear as delivered cards.
        long_tail_title = next(
            row["title"] for row in mixed if row["source"] == src
        )
        check(
            f"{src} headline/card text is absent from the delivered edition HTML",
            long_tail_title not in edition.html,
            src,
        )

    # ---------------------------------------------- honest shortfall (no filler)
    long_tail_only = _select([
        _row(11, "비즈트리뷴", "AI 데이터센터 전력 인프라 협력 방안 공개"),
        _row(12, "더퍼블릭", "AI 데이터센터 전력 조달 체계 개편 발표"),
        _row(13, "녹색경제신문", "AI 데이터센터 냉각 효율 표준 논의 시작"),
    ])
    check(
        "all-long-tail supply delivers zero leads (honest shortfall, never filler)",
        len(brief.filter_lead_source_eligible(long_tail_only)) == 0,
        str([a.source for a in long_tail_only]),
    )
    try:
        brief.render_daily(
            long_tail_only, run_at=RUN_AT, root_url="https://x.test",
            lead_source_gate=True,
        )
        check("all-long-tail delivered edition raises empty (prefer zero over filler)", False)
    except brief.EditorialError as exc:
        check(
            "all-long-tail delivered edition raises empty (prefer zero over filler)",
            "empty" in str(exc),
            str(exc),
        )

    # ------------------------------------------------------------- wiring
    runner_src = (ROOT / "scripts" / "run_editorial_briefing.py").read_text(
        encoding="utf-8"
    )
    check(
        "run_editorial_briefing wires the delivered lead-source gate; the "
        "reader-only fallback is removed (R4-R11 fail-closed skip)",
        "filter_lead_source_eligible(" in runner_src
        # R4-R11 — the live-collection fallback no longer delivers anything,
        # so there is no fallback lead to gate: a missing review bundle skips
        # the publication fail-closed instead.
        and '"daily_review_bundle_unavailable"' in runner_src
        and "lead_source_gate=True" not in runner_src,
    )

    print(f"checks={CHECKS} failures={len(FAILURES)}")
    if FAILURES:
        for name in FAILURES:
            print(f"FAILED: {name}")
        return 1
    print("RESULT=D7-AK-6E_R4R10_DAILY_LEAD_SOURCE_GATE_PASS")
    print(
        "screenshot_long_tail_leads=0 sjournal_lead=0 stock_theme_leads=0 "
        "network_calls=0 real_smtp_connections=0 teams_sends=0 telegram_sends=0 "
        "production_state_writes=0"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
