#!/usr/bin/env python3
"""D7-AK-6E R4-R17 — Executive Qualification Hardening behavioral verifier.

R4-R16 fixed major-publisher DISCOVERY recall, but the raw primary-lane supply
then included obvious executive-news noise (political, offshore-wind, KOSDAQ /
growth-fund tips, FX, leverage, luxury-cruise/oil, gold recommendation,
political talk, and an NVIDIA earnings-calendar preview). Being AI-central is
necessary but not sufficient for an executive AI intelligence brief: quality
beats count, and zero excellent articles is preferable to padding.

This verifier proves, entirely offline (EXTERNAL_NETWORK_CALLS=0, SMTP 0, Teams
0, Telegram 0, production-state writes 0), by exercising the real production
functions (never source grep), that:

* DISCOVERY and QUALIFICATION are independent. A row surfaced by the
  primary-publisher lane carries a "<publisher> <topic>" query string that is
  discovery provenance only: it is denied the +2 provider-query relevance
  boost and the provider-query-only fallback, and the hard Executive
  Qualification Gate never reads the query at all
  (SEARCH_QUERY_CAUSED_QUALIFICATION=0).
* After canonical AI-centrality and weak-content rejection, a non-official
  candidate survives only with a strong, machine-readable material signal
  (structural AI event / material corporate event / HDEC-direct AI event /
  strategic HDEC-infrastructure impact / material AI security incident).
* Every observed noise title is rejected; generic "AI is the future" and
  "ChatGPT popularity" commentary is rejected; concrete material AI events are
  accepted.
* Same-publisher retransmission of one headline collapses to exactly one row,
  while the same title from different publishers is never collapsed.
* Weak supply never pads a short edition, and primary/secondary/official lead
  authority and delivery gates are unchanged.

Network / mail are hard-guarded; any real outbound attempt is counted and
fails the run.
"""

from __future__ import annotations

import contextlib
import io
import smtplib
import socket
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app import editorial_briefings as brief  # noqa: E402
from app import naver_news_provider as nn  # noqa: E402
from app import source_priority  # noqa: E402

# --------------------------------------------------------------------------- #
# Network / mail guards. No production path in this verifier may touch the
# network or SMTP; if one tried, it would be counted here and fail the run.
# --------------------------------------------------------------------------- #
EXTERNAL = {"count": 0}
SMTP = {"count": 0}

_orig_getaddrinfo = socket.getaddrinfo
_orig_create_connection = socket.create_connection
_orig_urlopen = urllib.request.urlopen


def _blocked_getaddrinfo(*_a, **_k):
    EXTERNAL["count"] += 1
    raise RuntimeError("external DNS resolution is blocked in this verifier")


def _blocked_create_connection(*_a, **_k):
    EXTERNAL["count"] += 1
    raise RuntimeError("external socket connection is blocked in this verifier")


def _blocked_urlopen(*_a, **_k):
    EXTERNAL["count"] += 1
    raise RuntimeError("external urlopen is blocked in this verifier")


class _BlockedSMTP:
    def __init__(self, *_a, **_k):
        SMTP["count"] += 1
        raise RuntimeError("SMTP is blocked in this verifier")


socket.getaddrinfo = _blocked_getaddrinfo
socket.create_connection = _blocked_create_connection
urllib.request.urlopen = _blocked_urlopen
smtplib.SMTP = _BlockedSMTP
smtplib.SMTP_SSL = _BlockedSMTP

KST = timezone(timedelta(hours=9))
RUN_AT = datetime(2026, 8, 7, 14, 2, 0, tzinfo=KST)
COVERAGE = brief.daily_coverage(RUN_AT)
INSIDE = (COVERAGE.end - timedelta(hours=2)).isoformat()

PRODUCTION_STATE_FILES = (
    ROOT / "data" / "editorial_daily_state.json",
    ROOT / "data" / "editorial_weekly_state.json",
    ROOT / "data" / "teams_push_state.json",
    ROOT / "data" / "news_censor_verified_state.json",
)

CHECKS = 0
FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    global CHECKS
    CHECKS += 1
    if ok:
        print(f"PASS {name}")
    else:
        FAILURES.append(name)
        print(f"FAIL {name}" + (f" — {detail[:300]}" if detail else ""))
    return bool(ok)


_ROW_SEQ = {"n": 0}


def row(
    title: str,
    *,
    snippet: str = "",
    source: str = "연합뉴스",
    url: str | None = None,
    lane: str = "general",
    query: str = "AI 데이터센터 전력",
    published_at: str | None = None,
    provider: str = "naver_news_api",
) -> dict:
    _ROW_SEQ["n"] += 1
    seq = _ROW_SEQ["n"]
    return {
        "id": f"r4r17-{seq}",
        "title": title,
        "source": source,
        "published_at": published_at or INSIDE,
        "url": url or f"https://www.yna.co.kr/view/AKR{seq:08d}",
        "snippet": snippet or f"{title} 관련 내용이 전해졌다.",
        "source_metadata": {
            "provider": provider,
            "query": query,
            "discovery_lane": lane,
        },
        "discovery_lane": lane,
    }


def select(rows: list[dict], *, limit: int = 24, operator_review: bool = True):
    """Run the exact Review qualification path used by the Daily/Review console."""
    audit = brief.SelectionAuditCounters()
    articles = brief.normalize_articles(
        rows,
        COVERAGE,
        limit=limit,
        resolve_images=False,
        allow_image_network=False,
        selection_mode=brief.SELECTION_MODE_EDITORIAL_PRIORITY,
        selection_audit=audit,
        edition_type="daily",
        operator_review=operator_review,
    )
    return articles, audit


def survives(title: str, rows: list[dict]) -> bool:
    articles, _audit = select(rows)
    return any(a.title == title for a in articles)


# --------------------------------------------------------------------------- #
# Observed noise (the exact titles from the post-R4-R16 live Naver audit).
# --------------------------------------------------------------------------- #
OBSERVED_NOISE = [
    ("MBC", "심상치 않은 美 민주당 진보 돌풍‥한국계 프란체스카 홍이 이어가나",
     "미국 민주당 경선에서 진보 진영 돌풍이 이어지고 있다."),
    ("KBS", "첫 민간 주도 해상풍력 ‘시동’…산업 기반 확대 기대",
     "첫 민간 주도 해상풍력 사업이 시동을 걸며 산업 기반 확대가 기대된다."),
    ("조선일보", "“코스닥 상관 없이 국민성장펀드 대박 계속될 것”",
     "국민성장펀드 수익률이 코스닥과 무관하게 이어질 것이라는 전망이 나왔다."),
    ("YTN", "'제2의 플라자합의' 미일 환율 개입, 무슨 일?",
     "미국과 일본의 환율 개입을 두고 제2의 플라자합의 우려가 제기됐다."),
    ("YTN", "\"본전 찾으면 다시는 '이 시장' 안들어온다!\" 레버리지가 망쳐놓은 코스닥 개미",
     "레버리지 투자로 손실을 본 개인 투자자들의 사연이 전해졌다."),
    ("매일경제", "특급호텔도 뛰어든 ‘럭셔리 크루즈’…유가 급등에 신고가 노리는 종목",
     "유가 급등 속 크루즈 관련 종목이 신고가를 노리고 있다."),
    ("한국경제", "요즘 개미들 피 말리는데…\"지금 '금화산' 사라\" 깜짝 전망",
     "증시 부진 속 금 관련 종목을 사라는 깜짝 전망이 나왔다."),
    ("SBS", "[정치쇼] 김영진 \"민주당 당대표 토론...\"",
     "민주당 당대표 경선 토론을 두고 김영진 의원이 입장을 밝혔다."),
    # A famous AI company inside a market-calendar / earnings-preview story must
    # NOT be sufficient for executive qualification.
    ("KBS", "[시사플랫폼] “8월 27일에 엔비디아 실적, 한은 금통위 핵심 이벤트…”",
     "AI 대장주 엔비디아 실적 발표와 한국은행 금통위 일정이 이번 주 증시 핵심 이벤트로 꼽힌다."),
]


def observed_noise_checks() -> int:
    rejected = 0
    for src, title, snippet in OBSERVED_NOISE:
        ok = not survives(title, [row(title, source=src, snippet=snippet,
                                      url=f"https://pub.fixture.test/{abs(hash(title))%10**8}")])
        if check(f"observed noise rejected — {src}: {title[:24]}…", ok):
            rejected += 1
    # Whole batch together also yields zero survivors.
    batch = [row(t, source=s, snippet=sn,
                 url=f"https://pub.fixture.test/b{i}")
             for i, (s, t, sn) in enumerate(OBSERVED_NOISE)]
    arts, audit = select(batch)
    check("the full observed-noise batch yields zero survivors",
          len(arts) == 0, str([a.title for a in arts]))
    return rejected


def generic_commentary_checks() -> bool:
    future = row(
        "AI가 미래다…생성형 AI가 바꿀 세상",
        snippet="생성형 AI가 산업 전반을 바꿀 것이라는 전망이 이어지고 있다.",
    )
    chatgpt = row(
        "챗GPT 인기 지속…이용자 급증 전망",
        snippet="챗GPT 이용자가 계속 늘어날 것이라는 전망이 나왔다.",
    )
    ok_future = check("generic 'AI is the future' commentary is rejected",
                      not survives(future["title"], [future]))
    ok_chat = check("generic 'ChatGPT popularity' commentary is rejected",
                    not survives(chatgpt["title"], [chatgpt]))
    # Both are AI-central (so the executive gate, not the AI-centrality gate,
    # is what rejects them) — proving the new layer adds real signal.
    _arts, audit = select([future, chatgpt])
    ok_stage = check(
        "generic AI commentary is AI-central but executive-materiality rejected",
        audit.ai_central_qualified_count == 2
        and audit.executive_materiality_rejected_count == 2
        and audit.executive_qualified_count == 0,
        repr({k: audit.manifest_fields()[k] for k in (
            "ai_central_qualified_count", "executive_materiality_rejected_count",
            "executive_qualified_count")}),
    )
    return ok_future and ok_chat and ok_stage


def material_event_checks() -> bool:
    cases = [
        ("concrete AI data-center construction/investment event",
         row("네이버, 세종 AI 데이터센터 착공…1조원 규모 투자 확정",
             snippet="네이버가 세종에 AI 데이터센터를 착공하고 1조원 규모 투자를 확정했다.")),
        ("concrete AI-data-center power/grid constraint with factual impact",
         row("AI 데이터센터 전력 수요 급증에 송전망 용량 부족 심화",
             snippet="AI 데이터센터 전력 수요 급증으로 수도권 송전망 용량 부족이 심화되고 있다.")),
        ("material AI model/product launch",
         row("오픈AI, 차세대 AI 모델 GPT-5 상용화 출시",
             snippet="오픈AI가 차세대 AI 모델 GPT-5를 상용화해 출시했다.")),
        ("material AI regulation event",
         row("EU, AI 규제법 본격 시행 확정…고위험 AI 의무화",
             snippet="EU가 AI 규제법을 본격 시행하기로 확정하고 고위험 AI 의무를 규정했다.")),
        ("material AI security incident",
         row("챗GPT 이용자 대화 내용 대규모 유출 사고 발생",
             snippet="챗GPT 이용자 대화 내용이 대규모로 유출되는 보안 사고가 발생했다.")),
    ]
    all_ok = True
    for label, fixture in cases:
        ok = check(f"material AI event accepted — {label}",
                   survives(fixture["title"], [fixture]))
        all_ok = all_ok and ok
    # As a batch all five survive and are AI-central + executive-qualified.
    arts, audit = select([c for _l, c in cases])
    all_ok = all_ok and check(
        "all five material AI events survive as executive-qualified",
        len(arts) == 5
        and audit.ai_central_qualified_count == 5
        and audit.executive_qualified_count == 5
        and audit.executive_materiality_rejected_count == 0,
        repr([a.title for a in arts]),
    )
    return all_ok


def hdec_direct_checks() -> None:
    hdec = row(
        "현대건설, AI 데이터센터 전력 인프라 EPC 계약 체결",
        snippet="현대건설이 AI 데이터센터 전력 인프라 EPC 계약을 체결했다.",
    )
    check("HDEC-direct AI event is executive-qualified",
          survives(hdec["title"], [hdec]))


def provider_query_rescue_checks() -> tuple[bool, bool]:
    # (a) Relevance: the primary-publisher lane is denied the +2 query-group
    # boost and the provider-query-only fallback; the general lane keeps them.
    weak = "AI 서비스 이용 후기 인기 급상승"
    primary = row(weak, lane="primary_publisher", query="연합뉴스 AI 데이터센터 전력",
                  snippet="AI 서비스 이용 후기가 인기다.")
    general = row(weak, lane="general", query="AI 데이터센터 전력", source="한국경제",
                  url="https://www.hankyung.com/article/gq1",
                  snippet="AI 서비스 이용 후기가 인기다.")
    cand_primary = brief._build_article_candidate(primary, COVERAGE)  # noqa: SLF001
    cand_general = brief._build_article_candidate(general, COVERAGE)  # noqa: SLF001
    lane_ok = check(
        "primary-publisher lane is denied the provider-query relevance boost",
        cand_primary is not None
        and cand_general is not None
        and not any(r.startswith("query_group:") for r in cand_primary.relevance_reasons)
        and any(r.startswith("query_group:") for r in cand_general.relevance_reasons)
        and cand_primary.relevance_score < brief.SELECTION_RELEVANCE_FLOOR
        and cand_general.relevance_score > cand_primary.relevance_score,
        f"primary={cand_primary.relevance_score}/{cand_primary.relevance_reasons} "
        f"general={cand_general.relevance_score}/{cand_general.relevance_reasons}",
    )

    # (b) A weak/nonmaterial row surfaced by the primary-publisher lane, even
    # with a strong coverage-mapping query, never survives.
    rescue_ok = check(
        "primary-publisher query metadata cannot rescue a weak/nonmaterial row",
        not survives(weak, [primary]),
    )

    # (c) SEARCH_QUERY_CAUSED_QUALIFICATION=0: the executive gate never reads the
    # query. A batch of nonmaterial rows, each carrying a strong coverage query
    # (both lanes), yields zero survivors regardless of the query text.
    query_only_batch = [
        row("AI 트렌드 소개 인기 콘텐츠", lane="general",
            query="AI 데이터센터 전력 수요", url="https://pub.fixture.test/q1"),
        row("AI 활용 팁 모음 화제", lane="primary_publisher",
            query="연합뉴스 AI 데이터센터", url="https://pub.fixture.test/q2"),
        row("생성형 AI 체험 후기 눈길", lane="general",
            query="AI 전력 인프라", url="https://pub.fixture.test/q3"),
    ]
    arts, _audit = select(query_only_batch)
    query_caused = len(arts)
    rescue_ok = rescue_ok and check(
        "SEARCH_QUERY_CAUSED_QUALIFICATION is zero (query never qualifies a row)",
        query_caused == 0,
        str([a.title for a in arts]),
    )
    return lane_ok, rescue_ok


def same_publisher_retransmission_checks() -> bool:
    title = "[속보] AI 데이터센터 전력 수요 급증 대응 착공"
    snippet = "AI 데이터센터 전력 수요 급증에 대응해 착공했다."
    first = row(title, source="KBS", snippet=snippet,
                url="https://kbs.co.kr/news/view/aaa",
                published_at=(COVERAGE.end - timedelta(hours=3)).isoformat())
    retransmit = row(title, source="KBS", snippet=snippet,
                     url="https://kbs.co.kr/news/view/bbb",
                     published_at=(COVERAGE.end - timedelta(hours=1)).isoformat())
    arts, audit = select([first, retransmit])
    surviving = [a for a in arts if a.title == title]
    collapse_ok = check(
        "same-publisher same-title retransmission collapses to exactly one row",
        len(surviving) == 1
        and audit.same_publisher_duplicate_rejected_count == 1,
        f"survivors={len(surviving)} "
        f"dupes={audit.same_publisher_duplicate_rejected_count}",
    )

    # Different primary publishers reporting the same event are NOT collapsed.
    yna = row(title, source="연합뉴스", snippet=snippet,
              url="https://www.yna.co.kr/view/xp1")
    hk = row(title, source="한국경제", snippet=snippet,
             url="https://www.hankyung.com/article/xp2")
    arts2, audit2 = select([yna, hk])
    cross_ok = check(
        "same title from different publishers is never collapsed",
        sum(1 for a in arts2 if a.title == title) == 2
        and audit2.same_publisher_duplicate_rejected_count == 0,
        f"survivors={sum(1 for a in arts2 if a.title == title)} "
        f"dupes={audit2.same_publisher_duplicate_rejected_count}",
    )
    return collapse_ok, cross_ok


def weak_supply_never_pads_checks() -> None:
    strong = row(
        "삼성전자, AI 반도체 HBM 공급 계약 체결…2조원 규모",
        snippet="삼성전자가 AI 반도체 HBM 공급 계약을 2조원 규모로 체결했다.",
        url="https://www.yna.co.kr/view/strong1",
    )
    weak = [
        row(f"AI 서비스 인기 화제 {i}", url=f"https://pub.fixture.test/weak{i}",
            snippet=f"AI 서비스가 인기라는 소식 {i}.")
        for i in range(5)
    ]
    _arts, audit = select([strong] + weak, limit=brief.DAILY_MAX_ARTICLES,
                          operator_review=True)
    check(
        "weak supply never pads selection (1 strong + 5 weak → 1 selected)",
        audit.selected_candidates == 1
        and audit.executive_materiality_rejected_count == 5
        and audit.selection_shortfall == brief.DAILY_TARGET_MIN_ARTICLES - 1,
        repr({k: audit.manifest_fields()[k] for k in (
            "selected_candidates", "executive_materiality_rejected_count",
            "qualified_candidates", "selection_shortfall")}),
    )
    # Zero excellent articles → publish nothing.
    empty_arts, _empty_audit = select(weak, operator_review=True)
    check("zero material supply selects nothing (prefer zero over weak padding)",
          len(empty_arts) == 0, str([a.title for a in empty_arts]))


def delivery_gate_unchanged_checks() -> bool:
    sjournal = "https://www.s-journal.co.kr/news/articleView.html?idxno=42865"
    ok = check(
        "primary/secondary/official/long-tail lead authority is unchanged",
        source_priority.publisher_delivery_tier("연합뉴스", "https://www.yna.co.kr/view/x")["tier"]
        == "primary_10"
        and source_priority.publisher_delivery_tier("동아일보", "https://www.donga.com/news/x")["tier"]
        == "secondary_3"
        and brief.lead_source_eligible_tier("연합뉴스", "")
        and brief.lead_source_eligible_tier("동아일보", "")
        and not brief.lead_source_eligible_tier("비즈트리뷴", "")
        and not brief.lead_source_eligible_tier("S저널", sjournal)
        and source_priority.teams_delivery_source_policy("S저널", sjournal)["teams_lane"]
        == "never_automatic",
    )
    # The executive materiality gate is scoped to the Daily/Review curation
    # surface (edition_type == "daily" AND operator_review). It must NOT run on
    # the generic edition_type=None mechanics path, nor on a non-review daily
    # preview (operator_review=False): a soft AI-central row survives both,
    # exactly as it did before R4-R17.
    soft = "AI 서비스 인기 상승 소식"

    def _scope_probe(*, edition_type, operator_review):
        audit = brief.SelectionAuditCounters()
        arts = brief.normalize_articles(
            [row(soft, url="https://pub.fixture.test/scope1")],
            COVERAGE, limit=6, resolve_images=False,
            selection_mode=brief.SELECTION_MODE_EDITORIAL_PRIORITY,
            selection_audit=audit, edition_type=edition_type,
            operator_review=operator_review,
        )
        return arts, audit

    generic_arts, generic_audit = _scope_probe(edition_type=None, operator_review=False)
    preview_arts, preview_audit = _scope_probe(edition_type="daily", operator_review=False)
    review_arts, review_audit = _scope_probe(edition_type="daily", operator_review=True)
    scope_ok = check(
        "executive gate is scoped to the Daily/Review curation surface "
        "(edition_type=None and non-review daily preview stay ungated)",
        generic_audit.executive_materiality_rejected_count == 0
        and any(a.title == soft for a in generic_arts)
        and preview_audit.executive_materiality_rejected_count == 0
        and any(a.title == soft for a in preview_arts)
        # …but the operator Review path DOES reject the same soft row.
        and review_audit.executive_materiality_rejected_count == 1
        and not any(a.title == soft for a in review_arts),
        repr({
            "generic_rejected": generic_audit.executive_materiality_rejected_count,
            "preview_rejected": preview_audit.executive_materiality_rejected_count,
            "review_rejected": review_audit.executive_materiality_rejected_count,
        }),
    )
    return ok and scope_ok


def discovery_lane_marking_checks() -> None:
    """§A — the provider marks rows with the discovery-lane provenance marker."""
    NAME = source_priority.locked_publisher_names("primary_10")[0]
    domain_map = source_priority.locked_publisher_domain_map(("primary_10",))
    name_domain = next(d for d, n in domain_map.items() if n == NAME)

    def router(query: str):
        head = query.split(" ", 1)[0]
        if head == NAME:
            return {"items": [{
                "title": f"{NAME} AI 데이터센터 착공",
                "description": "AI 데이터센터 전력 인프라 착공.",
                "pubDate": "Wed, 06 Aug 2026 09:00:00 +0900",
                "originallink": f"https://www.{name_domain}/news/primary-1",
                "link": "https://n.news.naver.com/mnews/article/001/0000000001",
            }]}
        # general roster row (secondary_3 publisher, direct)
        return {"items": [{
            "title": "AI 데이터센터 일반 동향",
            "description": "AI 데이터센터 관련 일반 동향.",
            "pubDate": "Wed, 06 Aug 2026 09:00:00 +0900",
            "originallink": "https://www.donga.com/news/general-1",
            "link": "https://n.news.naver.com/mnews/article/002/0000000002",
        }]}

    calls: list[str] = []

    def fake_request(url, _headers, _timeout):
        calls.append(url)
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["query"][0]
        return router(q)

    import json
    import tempfile

    saved = (nn.config.NAVER_NEWS_ENABLED, nn.config.NAVER_CLIENT_ID,
             nn.config.NAVER_CLIENT_SECRET)
    original_request = nn._request_json  # noqa: SLF001
    nn.config.NAVER_NEWS_ENABLED = True
    nn.config.NAVER_CLIENT_ID = "fixture-id"
    nn.config.NAVER_CLIENT_SECRET = "fixture-secret"
    nn._request_json = fake_request  # noqa: SLF001
    try:
        with tempfile.TemporaryDirectory(prefix="r4r17-lane-") as td:
            src = Path(td) / "sources.json"
            src.write_text(json.dumps({
                "queries": ["현대건설"],
                "display": 10, "start": 1, "sort": "date",
                "max_per_query": 10, "max_total": 80,
                "host_source_map": {},
                "primary_publisher_lane": {
                    "enabled": True, "topics": ["AI"],
                    "max_queries": 30, "max_per_query": 2, "max_total": 40,
                },
            }, ensure_ascii=False), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                result = nn.fetch(timeout=8, sources_path=src, include_coverage=False)
    finally:
        nn._request_json = original_request  # noqa: SLF001
        (nn.config.NAVER_NEWS_ENABLED, nn.config.NAVER_CLIENT_ID,
         nn.config.NAVER_CLIENT_SECRET) = saved

    articles = result["articles"]
    primary_rows = [a for a in articles
                    if a.get("discovery_lane") == nn.DISCOVERY_LANE_PRIMARY_PUBLISHER]
    general_rows = [a for a in articles
                    if a.get("discovery_lane") == nn.DISCOVERY_LANE_GENERAL]
    check(
        "primary-publisher lane rows are marked discovery_lane=primary_publisher",
        len(primary_rows) >= 1
        and all(
            a["source_metadata"]["discovery_lane"] == nn.DISCOVERY_LANE_PRIMARY_PUBLISHER
            for a in primary_rows
        ),
        f"primary={len(primary_rows)}",
    )
    check(
        "general lane rows are marked discovery_lane=general",
        len(general_rows) >= 1
        and all(
            a["source_metadata"]["discovery_lane"] == nn.DISCOVERY_LANE_GENERAL
            for a in general_rows
        ),
        f"general={len(general_rows)}",
    )
    check(
        "every returned row carries a discovery-lane marker",
        all(a.get("discovery_lane") in
            (nn.DISCOVERY_LANE_PRIMARY_PUBLISHER, nn.DISCOVERY_LANE_GENERAL)
            for a in articles),
    )


def audit_counter_checks() -> None:
    fields = brief.SelectionAuditCounters().manifest_fields()
    required = (
        "executive_qualified_count", "executive_materiality_rejected_count",
        "provider_query_only_rejected_count", "same_publisher_duplicate_rejected_count",
        "review_qualified_primary_10", "review_qualified_secondary_3",
        "review_qualified_official", "deliverable_major_lead_candidates",
        # existing counters preserved
        "stock_market_rejected_count", "unrelated_domain_rejected_count",
        "incidental_ai_rejected_count", "weak_content_rejected",
        "qualified_candidates", "selection_shortfall", "ai_central_qualified_count",
    )
    check(
        "manifest_fields exposes all R4-R17 counters plus the preserved ones",
        all(key in fields for key in required),
        str([k for k in required if k not in fields]),
    )
    # review_qualified_* and deliverable counters are populated for a real
    # Daily/Review run with mixed-tier material supply.
    mixed = [
        row("연합뉴스 AI 데이터센터 전력 인프라 공급 계약 체결", source="연합뉴스",
            url="https://www.yna.co.kr/view/rq1",
            snippet="연합뉴스가 전한 AI 데이터센터 전력 인프라 공급 계약 체결 소식."),
        row("동아일보 AI 반도체 클러스터 전력망 증설 착공", source="동아일보",
            url="https://www.donga.com/news/rq2",
            snippet="AI 반도체 클러스터 전력망 증설을 착공했다."),
    ]
    _arts, audit = select(mixed, operator_review=True)
    check(
        "review_qualified tier counters and deliverable_major_lead_candidates populate",
        audit.review_qualified_primary_10 == 1
        and audit.review_qualified_secondary_3 == 1
        and audit.deliverable_major_lead_candidates == 2,
        repr({k: audit.manifest_fields()[k] for k in (
            "review_qualified_primary_10", "review_qualified_secondary_3",
            "review_qualified_official", "deliverable_major_lead_candidates")}),
    )


def _snapshot() -> dict:
    return {str(p): p.read_bytes() for p in PRODUCTION_STATE_FILES if p.exists()}


def main() -> int:
    state_before = _snapshot()
    try:
        noise_rejected = observed_noise_checks()
        generic_ok = generic_commentary_checks()
        material_ok = material_event_checks()
        hdec_direct_checks()
        lane_ok, rescue_ok = provider_query_rescue_checks()
        collapse_ok, cross_ok = same_publisher_retransmission_checks()
        weak_supply_never_pads_checks()
        delivery_ok = delivery_gate_unchanged_checks()
        discovery_lane_marking_checks()
        audit_counter_checks()
    finally:
        socket.getaddrinfo = _orig_getaddrinfo
        socket.create_connection = _orig_create_connection
        urllib.request.urlopen = _orig_urlopen
    state_after = _snapshot()

    state_ok = check("production state files are byte-identical (0 writes)",
                     state_after == state_before)
    no_network = check("no external network call occurred", EXTERNAL["count"] == 0)
    no_smtp = check("no SMTP attempt occurred", SMTP["count"] == 0)

    ok = not FAILURES and state_ok and no_network and no_smtp

    print(f"\nchecks={CHECKS} failures={len(FAILURES)}")
    if FAILURES:
        for name in FAILURES:
            print(f"FAILED: {name}")

    print(f"OBSERVED_NOISE_REJECTED={noise_rejected}/9")
    print(f"PROVIDER_QUERY_ONLY_RESCUE={0 if (lane_ok and rescue_ok) else 1}")
    print(f"GENERIC_AI_COMMENTARY_REJECTED={'PASS' if generic_ok else 'FAIL'}")
    print(f"MATERIAL_AI_EVENTS_ACCEPTED={'PASS' if material_ok else 'FAIL'}")
    print(f"SAME_PUBLISHER_RETRANSMISSION_COLLAPSE={'PASS' if collapse_ok else 'FAIL'}")
    print(f"CROSS_PUBLISHER_COLLAPSE={0 if cross_ok else 1}")
    print(f"DELIVERY_GATE_UNCHANGED={'PASS' if delivery_ok else 'FAIL'}")
    print(f"EXTERNAL_NETWORK_CALLS={EXTERNAL['count']}")
    print(f"SMTP_ATTEMPTS={SMTP['count']}")
    print("TEAMS_SENDS=0")
    print("TELEGRAM_SENDS=0")
    print(f"PRODUCTION_STATE_WRITES={0 if state_ok else 1}")
    print("EXECUTIVE_QUALIFICATION_VERIFIER=" + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
