#!/usr/bin/env python3
"""D7-AK-6E R4-R9D — strict major-media / official-source Teams delivery gate.

The operator rejected the previous 120-minute specialist fallback: specialist
or long-tail media must NEVER become a standalone automatic Teams card, even
when the event is TOP, directly relevant to Hyundai E&C, older than 120
minutes, and no major-media article exists. The system prefers zero delivery
over a specialist-only delivery.

This verifier proves, entirely offline (network 0, real SMTP 0, production
state writes 0):

* §5 the exact observed regression (TheGuru SMR/데이터센터 article) and ten
  additional long-tail publishers never produce an automatic Teams card;
* §6 a Microsoft Teams SafeLinks wrapper is never treated as the publisher —
  the validated destination classifies the source;
* §7 the twelve-fixture matrix (age 0 / 120+ / TOP+HDEC / same-event major /
  specialist-only supply / primary+specialist / secondary+specialist /
  promoted official / HDEC official release / ministry publicity / stock
  exclusion / hard-gate bypass);
* §8 the specialist audit counters, with the invariant specialist_rows_selected
  == 0;
* §9 the exact R4-R10 S저널 production regression (never_automatic pin);
* §10 the exact R4-R11 TTL뉴스/퍼블릭뉴스통신 production regression — the
  ledger-traced http article and its https/bare-host/query variants all pin to
  never_automatic, one canonical URL identity prevents scheme/www/query
  evasion, the legacy http ledger key still dedups the https form read-only,
  and spoofed labels / SafeLinks wrappers / a spoofed promoted-official lane
  can never promote ttlnews.com.

All delivery runs reuse the production sender ``deliver()`` with the injected
fake SMTP recorder; gate-shape fixtures call the pure ``apply_major_media_first_gate``.
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
)
from app import public_institution_routing as pir  # noqa: E402
from app import source_priority  # noqa: E402
from app.publisher_direct import (  # noqa: E402
    publisher_url,
    unwrap_security_intermediary_url,
)
from app.teams_ai_push import (  # noqa: E402
    IMPORTANCE_TOP,
    SOURCE_GATE_NEVER_AUTOMATIC,
    SOURCE_GATE_PROMOTED_OFFICIAL,
    SOURCE_GATE_SPECIALIST_HOLDBACK,
    ImportanceDecision,
    TeamsPushCandidate,
    TopicDecision,
    TEAMS_SPECIALIST_MAX_PER_BATCH,
    apply_major_media_first_gate,
    evaluate_source_gate,
    select_teams_push_candidates,
)
from app.teams_push_state import (  # noqa: E402
    HELD_SPECIALISTS_KEY,
    empty_state,
    evaluate_dedup,
    observe_held_specialist,
    save_state,
)
from app.watch_state import normalize_url  # noqa: E402

NOW = "2026-08-04T17:00:00+09:00"
AGED_FIRST_SEEN = "2026-08-04T14:30:00+09:00"   # 150 minutes before NOW
FRESH_FIRST_SEEN = "2026-08-04T16:55:00+09:00"  # 5 minutes before NOW

CHECKS = 0
FAILURES: list[str] = []

# §5 the exact observed TheGuru delivery plus ten long-tail publishers.
THEGURU_URL = "https://www.theguru.co.kr/news/article.html?no=105359"
THEGURU_TITLE = "현대건설, 美 홀텍·엔터지와 걸프만 SMR 사업 협력…AI 데이터센터 전력 수요 공략"
LONG_TAIL = [
    ("뉴스워커", "http://www.newsworker.co.kr/news/articleView.html?idxno=900001"),
    ("테크M", "https://www.techm.kr/news/articleView.html?idxno=900002"),
    ("테크월드", "https://www.epnc.co.kr/news/articleView.html?idxno=900003"),
    ("뉴스핌", "https://www.newspim.com/news/view/900004"),
    ("그린포스트코리아", "https://www.greened.kr/news/articleView.html?idxno=900005"),
    ("뉴스퀘스트", "https://www.newsquest.co.kr/news/articleView.html?idxno=900006"),
    ("오토데일리", "https://www.autodaily.co.kr/news/articleView.html?idxno=900007"),
    ("경남신문", "https://www.knnews.co.kr/news/articleView.php?idxno=900008"),
    ("우먼타임스", "https://www.womentimes.co.kr/news/articleView.html?idxno=900009"),
    ("비즈트리뷴", "https://www.biztribune.co.kr/news/articleView.html?idxno=900010"),
]

# §9 R4-R10 — the EXACT 2026-08-05 production regression. S저널 (s-journal.co.kr)
# is a neutral/long-tail publisher that carries none of the primary-ten /
# secondary-three / official lanes, yet this exact article was delivered through
# the teams_ai_push path on main (which does not contain this gate). The strict
# gate — plus the explicit never_automatic_publishers pin in
# data/source_priority_rules.json — must refuse it: selected 0, sent 0,
# specialist_or_neutral_rejected 1. The pin is domain/alias/label resistant.
SJOURNAL_URL = "https://www.s-journal.co.kr/news/articleView.html?idxno=42865"
SJOURNAL_SOURCE = "S저널"
SJOURNAL_TITLE = "최태원, HBM으로 빅테크 묶었다…5000억 달러 'AI 동맹' SKT까지 확장"

# §10 R4-R11 — the EXACT 2026-08-05 18:14 KST production regression. TTL뉴스/
# 퍼블릭뉴스통신 (ttlnews.com) was auto-delivered on main as delivery_id
# teams_ai_push:7081b3470e08 and ledgered under the scheme/www-preserving key
# http://www.ttlnews.com/… — which is why an https-only ledger probe produced
# a false negative. The explicit never_automatic pin plus canonical URL
# identity (http/https · www/non-www · query-order variants collapse to one
# key) must refuse every form of it, and the legacy ledger key must keep
# matching its canonical identity without the production state ever being
# rewritten.
TTL_URL_HTTP = "http://www.ttlnews.com/news/articleView.html?idxno=3131687"
TTL_URL_HTTPS = "https://www.ttlnews.com/news/articleView.html?idxno=3131687"
TTL_URL_BARE = "https://ttlnews.com/news/articleView.html?idxno=3131687"
TTL_URL_QUERY_VARIANT = (
    "https://www.ttlnews.com/news/articleView.html?utm_source=teams&idxno=3131687"
)
TTL_SOURCE = "퍼블릭뉴스통신(Public news-network for TTL)"
TTL_TITLE = "AI데이터센터 매출 폭풍성장…SK텔레콤 2분기 영업익 67.3% 급증"
TTL_DELIVERY_ID = "teams_ai_push:7081b3470e08"
TTL_PIN_NAME = "TTL뉴스 / 퍼블릭뉴스통신"
PRODUCTION_STATE_PATH = ROOT / "data" / "teams_push_state.json"
MAJOR_FIXTURE_DOMAINS = {
    "연합뉴스": "yna.co.kr",
    "SBS": "news.sbs.co.kr",
    "동아일보": "donga.com",
}


def check(name: str, ok: bool, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    if ok:
        print(f"PASS: {name}")
    else:
        FAILURES.append(name)
        print(f"FAIL: {name}" + (f" — {detail[:400]}" if detail else ""))


def specialist_article(key: str, source: str, url: str, **overrides):
    """A long-tail/specialist article that passes every pre-gate policy —
    AI-central, HDEC-relevant, confirmed material event — so only the source
    gate stops it."""
    base = _article(
        article_key=key,
        title="현대건설, AI 데이터센터 전력 인프라 사업 협력 발표",
        summary="현대건설이 AI 데이터센터 전력 인프라 사업 협력을 공식 발표했다.",
        hdec_relevance="현대건설 직접 영향",
        source=source,
        url=url,
        score=4.6,
        shadow_confirmed_event_types=["contract_signed"],
    )
    base.update(overrides)
    return base


def theguru(**overrides):
    return specialist_article(
        "obs-theguru-105359", "더구루", THEGURU_URL,
        title=THEGURU_TITLE,
        summary="현대건설이 미국 홀텍·엔터지와 걸프만 SMR 사업 협력에 나선다.",
        hdec_relevance="현대건설 직접 영향(SMR·데이터센터 전력)",
        shadow_confirmed_event_types=["partnership_signed"],
        **overrides,
    )


def sjournal(**overrides):
    """The exact S저널 production article, otherwise fully pre-gate eligible
    (AI-central, HDEC-relevant, confirmed material event) so ONLY the source
    gate — via the explicit never_automatic_publishers pin — can stop it."""
    base = specialist_article(
        "obs-sjournal-42865", SJOURNAL_SOURCE, SJOURNAL_URL,
        title=SJOURNAL_TITLE,
        summary=(
            "SK가 HBM 공급을 지렛대로 글로벌 빅테크와 5000억 달러 규모 AI 데이터센터 "
            "동맹을 맺고 현대건설 전력 인프라 협력으로 확장한다."
        ),
        hdec_relevance="현대건설 직접 영향(AI 데이터센터 전력 인프라)",
        shadow_confirmed_event_types=["partnership_signed"],
    )
    base.update(overrides)
    return base


def ttlnews(url: str = TTL_URL_HTTP, **overrides):
    """Exact TTL production identity plus adversarial generated metadata.

    R4-OPS-8 correctly rejects the verbatim earnings/tailwind title. The
    synthetic ``summary``/HDEC implication below must not rescue it.
    """
    base = specialist_article(
        "obs-ttl-3131687", TTL_SOURCE, url,
        title=TTL_TITLE,
        summary="현대건설이 AI 데이터센터 전력 인프라 계약을 체결했다.",
        hdec_relevance="현대건설 직접 영향(AI 데이터센터 전력 인프라)",
        shadow_confirmed_event_types=["contract_signed"],
    )
    base.update(overrides)
    return base


def ttl_material_neighbor(**overrides):
    """Explicit synthetic material event isolating TTL source-gate behavior."""
    base = specialist_article(
        "synthetic-ttl-material",
        TTL_SOURCE,
        "https://www.ttlnews.com/news/articleView.html?idxno=9999991",
        title="SK텔레콤, AI 데이터센터 전력 공급계약 체결",
        summary="SK텔레콤이 AI 데이터센터 전력 공급계약을 체결했다.",
        snippet="SK텔레콤이 AI 데이터센터 전력 공급계약을 체결했다.",
        hdec_relevance="AI 데이터센터 전력 인프라 공급망 영향",
        evidence_provenance="synthetic_neighbor",
    )
    base.update(overrides)
    return base


def primary(key: str, title: str, source: str = "연합뉴스", **overrides):
    base = _article(
        article_key=key, title=title, summary=f"{title} — 공식 확정 내용.",
        source=source, url=f"https://{MAJOR_FIXTURE_DOMAINS[source]}/major/{key}", score=4.7,
    )
    base.update(overrides)
    return base


def deliver(tmp: Path, articles, state_path: Path, *, send=True,
            statuses=(250,) * 8, max_articles=5, now=NOW):
    recorder = _SMTPRecorder(statuses)
    artifact = _write(
        tmp / f"strict-{abs(hash(str(sorted(a['article_key'] for a in articles)))) % 10**8}-{send}.json",
        _payload(articles),
    )
    summary = sender.deliver(
        artifact_path=artifact, state_path=state_path,
        credentials=_fixture_credentials(), should_send=send,
        smtp_factory=recorder, max_articles=max_articles, now_iso_value=now,
    )
    return summary, recorder


def seed_holds(articles, first_seen: str, state_path: Path) -> None:
    state = empty_state()
    for article in articles:
        candidates = select_teams_push_candidates([article], max_articles=None)
        if not candidates:
            continue
        state = observe_held_specialist(
            state, article, cluster_key=candidates[0].cluster_key,
            source=str(article.get("source") or ""), source_tier="specialist",
            holdback_reason="holdback_active", fallback_eligible=False, now=first_seen,
        )
    save_state(state, state_path)


def make_candidate(*, source, url, cluster, official=False, importance_top=True,
                   hdec_direct=True):
    """Minimal gate candidate for promoted-official / lane-shape fixtures."""
    article = _article(
        article_key=f"cand-{cluster}-{source}",
        title="현대건설 AI 데이터센터 전력 인프라 공식 계약 체결",
        summary="AI 데이터센터 전력 인프라 공식 계약을 체결했다.",
        source=source, url=url, score=4.8,
    )
    kwargs = {}
    if official:
        kwargs.update(
            source_class=pir.SOURCE_CLASS_OFFICIAL,
            editorial_lane=pir.LANE_PUBLIC,
            official_source_name=source,
            teams_alert_eligible=True,
        )
    return TeamsPushCandidate(
        article=article,
        topic=TopicDecision(eligible=True, topic_key="ai_material_event"),
        importance=ImportanceDecision(
            sendable=True,
            level=IMPORTANCE_TOP if importance_top else "important",
            hdec_direct=hdec_direct, score=4.8,
        ),
        cluster_key=cluster, material_signature=f"sig-{cluster}-{source}",
        **kwargs,
    )


def main() -> int:
    import tempfile

    # ------------------------------------------------------------------ §2/§3
    check("canonical constant: automatic specialist fallback removed (== 0)",
          TEAMS_SPECIALIST_MAX_PER_BATCH == 0)

    # ---------------------------------------------------------------------- §6
    wrapped = (
        "https://teams.public.onecdn.static.microsoft/evergreen-assets/safelinks/"
        "2/atp-safelinks.html?url=" + THEGURU_URL.replace(":", "%3A").replace("/", "%2F")
        + "&data=abc"
    )
    unwrapped = unwrap_security_intermediary_url(wrapped)
    wrapper_tier = source_priority.publisher_delivery_tier(
        "Microsoft", "https://teams.public.onecdn.static.microsoft/x")
    dest_tier = source_priority.publisher_delivery_tier("더구루", unwrapped or THEGURU_URL)
    check("SafeLinks unwraps to the validated TheGuru destination",
          bool(unwrapped) and "theguru.co.kr" in unwrapped, unwrapped)
    check("SafeLinks wrapper host is never an immediate/major publisher",
          wrapper_tier["tier"] not in source_priority.TEAMS_IMMEDIATE_TIERS,
          str(wrapper_tier))
    check("SafeLinks destination classifies as non-major (never automatic)",
          dest_tier["tier"] not in source_priority.TEAMS_IMMEDIATE_TIERS,
          str(dest_tier))

    with tempfile.TemporaryDirectory(prefix="hdec-r4r9d-") as raw:
        tmp = Path(raw)

        # -------------------------------------------------------------- §7 (1)
        st = tmp / "s-theguru-age0.json"
        summary, rec = deliver(tmp, [theguru()], st)
        check("(1) TheGuru at age 0: selected 0, nothing sent",
              summary["selected"] == 0 and rec.attempts == []
              and summary["specialist_rows_selected"] == 0, str(summary))

        # (2) TheGuru aged 120+ (seed an old hold) → still 0. TheGuru is neutral/
        # never-automatic, so a hold may not seed; assert delivery regardless.
        st = tmp / "s-theguru-aged.json"
        seed_holds([theguru()], AGED_FIRST_SEEN, st)
        summary, rec = deliver(tmp, [theguru()], st)
        check("(2) TheGuru aged 120+: selected 0, no fallback eligibility",
              summary["selected"] == 0 and rec.attempts == []
              and summary["teams_specialist_fallback_eligible_rows"] == 0
              and summary["specialist_rows_selected"] == 0, str(summary))

        # (3) TheGuru TOP + direct HDEC relevance → 0 (source gate is not
        # overridden by the direct Hyundai E&C relationship).
        st = tmp / "s-theguru-top.json"
        seed_holds([theguru()], AGED_FIRST_SEEN, st)
        summary, rec = deliver(tmp, [theguru(score=4.9)], st)
        check("(3) TheGuru TOP + direct HDEC: selected 0 (source gate not overridden)",
              summary["selected"] == 0 and rec.attempts == []
              and summary["specialist_rows_selected"] == 0, str(summary))

        # (4) TheGuru + one primary-ten same-event → one primary card, specialist 0.
        st = tmp / "s-theguru-plus-primary.json"
        summary, rec = deliver(
            tmp,
            [theguru(cluster_key="evt-smr"),
             primary("p-smr", "연합뉴스: 현대건설 AI 데이터센터 전력 인프라 협력 확정",
                     "연합뉴스", cluster_key="evt-smr",
                     hdec_relevance="현대건설 직접 영향",
                     shadow_confirmed_event_types=["partnership_signed"])],
            st,
        )
        check("(4) TheGuru + same-event primary: one primary card, specialist 0",
              summary["selected"] == 1 and summary["delivered_count"] == 1
              and summary["selected_primary_10_rows"] == 1
              and summary["selected_specialist_rows"] == 0
              and summary["specialist_rows_selected"] == 0, str(summary))

        # (5) specialist-only supply of 20 → selected 0.
        twenty = [
            specialist_article(f"spec-{i}", LONG_TAIL[i % len(LONG_TAIL)][0],
                               f"{LONG_TAIL[i % len(LONG_TAIL)][1]}-{i}",
                               title=f"AI 데이터센터 전력 협력 발표 {i}")
            for i in range(20)
        ]
        st = tmp / "s-spec-only.json"
        summary, rec = deliver(tmp, twenty, st)
        check("(5) specialist-only supply of 20: selected 0",
              summary["selected"] == 0 and rec.attempts == []
              and summary["specialist_rows_selected"] == 0, str(summary))

        # (6) primary 2 + specialist 20 → selected 2.
        two_primary = [
            primary("p6-a", "연합뉴스: AI 데이터센터 국가 투자 확정", "연합뉴스"),
            primary("p6-b", "SBS: AI 전력망 인프라 계약 체결", "SBS"),
        ]
        st = tmp / "s-2primary-20spec.json"
        summary, rec = deliver(tmp, two_primary + twenty, st)
        check("(6) primary 2 + specialist 20: selected 2 (specialist 0)",
              summary["selected"] == 2 and summary["selected_primary_10_rows"] == 2
              and summary["specialist_rows_selected"] == 0, str(summary))

        # (7) secondary 1 + specialist 20 → selected 1.
        one_secondary = [primary("s7", "동아일보: AI 데이터센터 착공 확정", "동아일보")]
        st = tmp / "s-1sec-20spec.json"
        summary, rec = deliver(tmp, one_secondary + twenty, st)
        check("(7) secondary 1 + specialist 20: selected 1 (specialist 0)",
              summary["selected"] == 1 and summary["selected_secondary_3_rows"] == 1
              and summary["specialist_rows_selected"] == 0, str(summary))

        # (11) generic stock-market article from a primary outlet → 0.
        st = tmp / "s-primary-stock.json"
        summary, rec = deliver(
            tmp,
            [primary("p-stock", "연합뉴스: AI 데이터센터 테마주 급등…수혜주 전망",
                     "연합뉴스",
                     summary="관련 테마주가 급등할 것이라는 증권가 전망이 나온다.")],
            st,
        )
        check("(11) primary-outlet stock-market article: selected 0 (hard exclusion)",
              summary["selected"] == 0
              and summary["stock_market_hard_rejected_rows"] >= 1, str(summary))

        # (12) specialist cannot bypass a hard gate (stock exclusion).
        st = tmp / "s-spec-stock.json"
        summary, rec = deliver(
            tmp,
            [specialist_article("spec-stock", "테크M",
                                "https://www.techm.kr/news/articleView.html?idxno=900099",
                                title="테크M: AI 데이터센터 테마주 수혜주 급등 전망",
                                summary="관련주가 급등할 것이라는 전망이 나온다.")],
            st,
        )
        check("(12) specialist cannot bypass the stock hard gate: selected 0",
              summary["selected"] == 0
              and summary["specialist_rows_selected"] == 0, str(summary))

        # ---------------------------------------------------------- §5 regression
        st = tmp / "s-theguru-exact.json"
        summary, rec = deliver(tmp, [theguru()], st)
        check("§5 exact observed TheGuru SMR article: automatic Teams = 0",
              summary["selected"] == 0 and rec.attempts == []
              and summary["specialist_rows_selected"] == 0, str(summary))
        for name, url in LONG_TAIL:
            solo = tmp / f"s-longtail-{abs(hash(name)) % 10**6}.json"
            summary, rec = deliver(
                tmp, [specialist_article(f"lt-{abs(hash(url)) % 10**6}", name, url)], solo)
            check(f"§5 long-tail {name}: automatic Teams = 0",
                  summary["selected"] == 0 and rec.attempts == []
                  and summary["specialist_rows_selected"] == 0, str(summary))

    # ------------------------------------------------ §7 (8)(9)(10) gate shape
    # (8) promoted official material event + same-event specialist → one official
    # representative, specialist selected 0.
    official = make_candidate(source="산업통상자원부",
                              url="https://www.motie.go.kr/kor/article/900201",
                              cluster="evt-official", official=True)
    spec_same = make_candidate(source="테크M",
                               url="https://www.techm.kr/news/articleView.html?idxno=900202",
                               cluster="evt-official", official=False)
    batch8 = apply_major_media_first_gate(
        [official, spec_same], state=None, run_cap=5, now_iso_value=NOW)
    check("(8) promoted official gate class is immediate promoted_official",
          evaluate_source_gate(official).gate_class == SOURCE_GATE_PROMOTED_OFFICIAL
          and evaluate_source_gate(official).immediate, str(evaluate_source_gate(official)))
    check("(8) promoted official + same-event specialist: official 1, specialist 0",
          batch8.audit["selected_promoted_official_rows"] == 1
          and batch8.audit["specialist_rows_selected"] == 0
          and batch8.audit["teams_specialist_selected_rows"] == 0, str(batch8.audit))

    # (9) an HDEC/official promoted material event may be eligible (immediate).
    hdec_official = make_candidate(
        source="산업통상자원부", url="https://www.motie.go.kr/kor/article/900301",
        cluster="evt-hdec-official", official=True)
    check("(9) promoted official material event is delivery-eligible (immediate)",
          evaluate_source_gate(hdec_official).immediate
          and evaluate_source_gate(hdec_official).gate_class
          == SOURCE_GATE_PROMOTED_OFFICIAL, str(evaluate_source_gate(hdec_official)))

    # (10) generic ministry publicity (official source, NOT promoted) → never
    # automatic (official authority alone does not create importance).
    ministry_generic = make_candidate(
        source="산업통상자원부", url="https://www.motie.go.kr/kor/article/900401",
        cluster="evt-generic", official=False)  # LANE_MAIN → not promoted
    gen_gate = evaluate_source_gate(ministry_generic)
    batch10 = apply_major_media_first_gate(
        [ministry_generic], state=None, run_cap=5, now_iso_value=NOW)
    check("(10) generic ministry publicity (not promoted): never automatic, selected 0",
          gen_gate.gate_class == SOURCE_GATE_NEVER_AUTOMATIC
          and len(batch10.selected) == 0, str(gen_gate))

    # ---------------------------------------------------------------- §8 counters
    # specialist_rows_seen / _supporting_evidence / _automatic_rejected / _selected.
    p_major = make_candidate(source="연합뉴스",
                             url="https://yna.co.kr/major/c8",
                             cluster="evt-support", official=False)
    spec_support = make_candidate(source="테크M",
                                  url="https://www.techm.kr/news/articleView.html?idxno=900501",
                                  cluster="evt-support", official=False)     # same event as major
    spec_alone = make_candidate(source="테크M",
                                url="https://www.techm.kr/news/articleView.html?idxno=900502",
                                cluster="evt-alone", official=False)         # specialist-only
    batchC = apply_major_media_first_gate(
        [p_major, spec_support, spec_alone], state=None, run_cap=5, now_iso_value=NOW)
    a = batchC.audit
    check("§8 specialist_rows_seen counts both specialist rows",
          a["specialist_rows_seen"] == 2, str(a))
    check("§8 specialist_rows_supporting_evidence counts the same-event specialist",
          a["specialist_rows_supporting_evidence"] == 1, str(a))
    check("§8 specialist_rows_automatic_rejected counts the specialist-only row",
          a["specialist_rows_automatic_rejected"] == 1, str(a))
    check("§8 invariant: specialist_rows_selected == 0",
          a["specialist_rows_selected"] == 0
          and a["specialist_automatic_fallback_removed"] is True, str(a))
    check("§8 the same-event major is the one selected representative",
          len(batchC.selected) == 1
          and a["selected_primary_10_rows"] == 1, str(a))

    # ---------------------------------------------------- §9 R4-R10 S저널 regression
    # The exact 2026-08-05 production auto-send (delivery_id teams_ai_push:…,
    # importance important, source S저널, s-journal.co.kr) must be refused by the
    # strict gate. On main the gate is absent; here it is proven blocked.
    sj_policy = source_priority.teams_delivery_source_policy(SJOURNAL_SOURCE, SJOURNAL_URL)
    check("§9 S저널 is never_automatic (never primary_10 / secondary_3 / official)",
          sj_policy["teams_lane"] == source_priority.TEAMS_LANE_NEVER_AUTOMATIC
          and sj_policy["tier"] not in source_priority.TEAMS_IMMEDIATE_TIERS
          and sj_policy["tier"] != "official_institution", str(sj_policy))
    check("§9 S저널 is on the explicit never_automatic_publishers pin",
          sj_policy["explicit_never_automatic"] == "S저널", str(sj_policy))
    sj_spoof = source_priority.teams_delivery_source_policy("Microsoft Teams", SJOURNAL_URL)
    check("§9 s-journal.co.kr destination stays never_automatic under a spoofed label",
          sj_spoof["teams_lane"] == source_priority.TEAMS_LANE_NEVER_AUTOMATIC, str(sj_spoof))

    with tempfile.TemporaryDirectory(prefix="hdec-r4r10-sj-") as raw:
        tmp = Path(raw)
        # (a) exact article, otherwise fully eligible, through the production
        # sender: selected 0, no SMTP attempt, specialist_or_neutral_rejected 1.
        st = tmp / "sj-exact.json"
        summary, rec = deliver(tmp, [sjournal()], st)
        check("§9 exact S저널 article: selected=0 accepted=0 sent=0",
              summary["selected"] == 0 and rec.attempts == []
              and summary["delivered_count"] == 0, str(summary))
        check("§9 exact S저널 article: specialist_or_neutral_rejected == 1",
              summary["specialist_or_neutral_rejected_rows"] == 1
              and summary["never_automatic_rejected_rows"] == 1
              and summary["source_gate_rejected_rows"] == 1, str(summary))
        cand = select_teams_push_candidates([sjournal()], max_articles=None)
        check("§9 S저널 candidate passes every pre-gate yet the source gate refuses it",
              len(cand) == 1
              and evaluate_source_gate(cand[0]).gate_class == SOURCE_GATE_NEVER_AUTOMATIC
              and evaluate_source_gate(cand[0]).reason
              == "explicit_never_automatic_publisher", str(cand))
        # (b) S저널 + a same-event primary-ten card: the primary is the only
        # selection; no specialist/neutral is ever sent.
        st = tmp / "sj-plus-primary.json"
        summary, rec = deliver(
            tmp,
            [sjournal(cluster_key="evt-sj"),
             primary("p-sj", "연합뉴스: 현대건설 AI 데이터센터 전력 인프라 협력 확정",
                     "연합뉴스", cluster_key="evt-sj",
                     hdec_relevance="현대건설 직접 영향",
                     shadow_confirmed_event_types=["partnership_signed"])],
            st,
        )
        check("§9 S저널 + same-event primary: primary only, specialist/neutral selected 0",
              summary["selected"] == 1 and summary["selected_primary_10_rows"] == 1
              and summary["selected_specialist_rows"] == 0
              and summary["delivered_count"] == 1, str(summary))

    # ---------------------------------------------------- §10 R4-R11 TTL regression
    import hashlib
    import json

    state_bytes_before = PRODUCTION_STATE_PATH.read_bytes()
    production_state = json.loads(state_bytes_before)

    # (a) the production send is traced in the committed ledger exactly as
    # delivered: legacy scheme/www-preserving key, delivery id, source label.
    ledger_entry = (production_state.get("normalized_urls") or {}).get(TTL_URL_HTTP)
    check("§10 TTL production send is traced in the ledger under the legacy http key",
          isinstance(ledger_entry, dict)
          and ledger_entry.get("delivery_id") == TTL_DELIVERY_ID
          and ledger_entry.get("source") == TTL_SOURCE, str(ledger_entry))

    # (b) publisher policy: every URL form of the exact article is pinned
    # never_automatic (never primary_10 / secondary_3 / official).
    for label, url in (("http", TTL_URL_HTTP), ("https", TTL_URL_HTTPS),
                       ("bare-host", TTL_URL_BARE), ("query-variant", TTL_URL_QUERY_VARIANT)):
        ttl_policy = source_priority.teams_delivery_source_policy(TTL_SOURCE, url)
        check(f"§10 TTL {label} URL is never_automatic on the explicit pin",
              ttl_policy["teams_lane"] == source_priority.TEAMS_LANE_NEVER_AUTOMATIC
              and ttl_policy["explicit_never_automatic"] == TTL_PIN_NAME
              and ttl_policy["tier"] not in source_priority.TEAMS_IMMEDIATE_TIERS
              and ttl_policy["tier"] != "official_institution", str(ttl_policy))
    for alias in ("TTL뉴스", "티티엘뉴스", "퍼블릭뉴스통신", "ttlnews"):
        alias_policy = source_priority.teams_delivery_source_policy(alias, "")
        check(f"§10 TTL alias '{alias}' alone is never_automatic",
              alias_policy["teams_lane"] == source_priority.TEAMS_LANE_NEVER_AUTOMATIC
              and alias_policy["explicit_never_automatic"] == TTL_PIN_NAME,
              str(alias_policy))

    # (c) spoof resistance: a major-media label, a SafeLinks wrapper, and even
    # a spoofed promoted-official candidate cannot promote ttlnews.com.
    spoof_major = source_priority.teams_delivery_source_policy("연합뉴스", TTL_URL_HTTPS)
    check("§10 spoofed 연합뉴스 label on ttlnews.com stays never_automatic",
          spoof_major["teams_lane"] == source_priority.TEAMS_LANE_NEVER_AUTOMATIC,
          str(spoof_major))
    ttl_wrapped = (
        "https://nam11.safelinks.protection.outlook.com/?url="
        + TTL_URL_HTTPS.replace(":", "%3A").replace("/", "%2F") + "&data=x&reserved=0"
    )
    ttl_unwrapped = unwrap_security_intermediary_url(ttl_wrapped)
    spoof_wrapped = source_priority.teams_delivery_source_policy(
        "연합뉴스", publisher_url({"url": ttl_wrapped}))
    check("§10 SafeLinks-wrapped ttlnews.com resolves to the destination before "
          "classification and stays never_automatic",
          "ttlnews.com" in ttl_unwrapped
          and spoof_wrapped["teams_lane"] == source_priority.TEAMS_LANE_NEVER_AUTOMATIC,
          f"{ttl_unwrapped} {spoof_wrapped}")
    spoof_official = make_candidate(
        source=TTL_SOURCE, url=TTL_URL_HTTPS, cluster="evt-ttl-spoof", official=True)
    spoof_gate = evaluate_source_gate(spoof_official)
    check("§10 spoofed promoted-official lane on ttlnews.com is still never_automatic",
          spoof_gate.gate_class == SOURCE_GATE_NEVER_AUTOMATIC
          and not spoof_gate.immediate
          and spoof_gate.reason == "explicit_never_automatic_publisher",
          str(spoof_gate))

    # (d) one canonical URL identity: scheme/www/query-order variants collapse,
    # and the https form matches the legacy http ledger entry read-only — a
    # scheme variant can never evade dedup, and the state is never rewritten.
    canonical_keys = {normalize_url(u) for u in (
        TTL_URL_HTTP, TTL_URL_HTTPS, TTL_URL_BARE, TTL_URL_QUERY_VARIANT)}
    check("§10 http/https/www/query variants collapse to one canonical identity",
          len(canonical_keys) == 1, str(canonical_keys))
    dedup_https = evaluate_dedup(
        production_state,
        {"title": "완전히 다른 제목의 후속 기사", "url": TTL_URL_HTTPS, "id": "ttl-probe"},
        cluster_key="", signature="probe", is_material_update=False)
    check("§10 https variant of the exact article is deduped against the legacy "
          "http ledger key",
          dedup_https.send_allowed is False
          and dedup_https.matched_key == "normalized_url", str(dedup_https))

    with tempfile.TemporaryDirectory(prefix="hdec-r4r11-ttl-") as raw:
        tmp = Path(raw)
        # (e) exact article through the production sender, http and https:
        # selected 0, accepted/delivered 0, sent 0. R4-OPS-8 now rejects this
        # exact generic earnings/tailwind framing before the source gate, so
        # source-gate counters remain zero rather than receiving false credit.
        for label, url in (("http", TTL_URL_HTTP), ("https", TTL_URL_HTTPS)):
            st = tmp / f"ttl-exact-{label}.json"
            summary, rec = deliver(tmp, [ttlnews(url)], st)
            check(f"§10 exact TTL article ({label}): selected=0 accepted=0 sent=0",
                  summary["selected"] == 0 and rec.attempts == []
                  and summary["delivered_count"] == 0
                  and summary["smtp_attempted_rows"] == 0, str(summary))
            check(f"§10 exact TTL article ({label}): semantic gate owns the reject",
                  summary["alert_policy_eligible"] == 0
                  and summary["never_automatic_rejected_rows"] == 0
                  and summary["specialist_or_neutral_rejected_rows"] == 0
                  and summary["source_gate_rejected_rows"] == 0, str(summary))
        check("§10 adversarial generated HDEC contract summary cannot rescue tailwind title",
              len(select_teams_push_candidates([ttlnews()], max_articles=None)) == 0)
        cand = select_teams_push_candidates(
            [ttl_material_neighbor()], max_articles=None
        )
        check("§10 synthetic material TTL neighbor reaches source gate and is refused",
              len(cand) == 1
              and evaluate_source_gate(cand[0]).gate_class == SOURCE_GATE_NEVER_AUTOMATIC
              and evaluate_source_gate(cand[0]).reason
              == "explicit_never_automatic_publisher", str(cand))
        # (f) the verbatim production framing (earnings summary, no confirmed
        # material event) is stopped by the R4-OPS-8 generic-tailwind gate.
        st = tmp / "ttl-verbatim.json"
        summary, rec = deliver(
            tmp,
            [ttlnews(summary="SK텔레콤이 AI 데이터센터 매출 성장에 힘입어 2분기 실적을 발표했다.",
                     hdec_relevance="AI 데이터센터 전력 인프라 수요 확대",
                     shadow_confirmed_event_types=["earnings_update"])],
            st,
        )
        check("§10 verbatim earnings framing is blocked (semantic lane, selected 0)",
              summary["selected"] == 0 and rec.attempts == []
              and summary["delivered_count"] == 0, str(summary))
        # (g) TTL + a same-event primary-ten card: the primary is the only
        # selection; the never-automatic publisher is never the representative.
        st = tmp / "ttl-plus-primary.json"
        summary, rec = deliver(
            tmp,
            [ttl_material_neighbor(cluster_key="evt-ttl"),
             primary("p-ttl", "연합뉴스: SK텔레콤 AI 데이터센터 전력 공급계약 체결",
                     "연합뉴스", cluster_key="evt-ttl",
                     hdec_relevance="현대건설 직접 영향",
                     shadow_confirmed_event_types=["contract_signed"])],
            st,
        )
        check("§10 TTL + same-event primary: primary only, never-automatic selected 0",
              summary["selected"] == 1 and summary["selected_primary_10_rows"] == 1
              and summary["delivered_count"] == 1, str(summary))

    check("§10 production ledger was read-only (byte-identical after the run)",
          PRODUCTION_STATE_PATH.read_bytes() == state_bytes_before)

    print(f"checks={CHECKS} failures={len(FAILURES)}")
    if FAILURES:
        for name in FAILURES:
            print(f"FAILED: {name}")
        return 1
    print("RESULT=D7-AK-6E_R4R9D_TEAMS_STRICT_SOURCE_GATE_PASS")
    print(
        "theguru_regression=blocked long_tail_publishers=10 "
        "sjournal_regression=blocked sjournal_specialist_or_neutral_rejected=1 "
        "ttl_regression=blocked ttl_http_exact=blocked ttl_https_exact=blocked "
        "ttl_never_automatic_rejected=1 ttl_specialist_or_neutral_rejected=1 "
        "ttl_automatic_teams_selected=0 ttl_automatic_teams_sent=0 "
        "url_identity_canonical=1 production_state_reads=readonly "
        "specialist_rows_selected=0 network_calls=0 real_smtp_connections=0 "
        "teams_sends=0 telegram_sends=0 production_state_writes=0 "
        f"specialist_max_per_batch={TEAMS_SPECIALIST_MAX_PER_BATCH}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
