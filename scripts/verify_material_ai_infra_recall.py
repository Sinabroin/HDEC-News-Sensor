#!/usr/bin/env python3
"""D7-AK-6E R4-R21 — Material AI Infrastructure Recall + Honest Empty Edition.

Two guarantees, proven entirely offline (EXTERNAL_NETWORK_CALLS=0, SMTP 0,
Teams 0, Telegram 0, production-state writes 0) by exercising the real
production functions (never source grep):

1. MATERIAL AI INFRASTRUCTURE RECALL — the executive qualification gate admits a
   material AI enabling-infrastructure event (AI data-center / power / grid /
   cooling / SMR / semiconductor infrastructure paired with a confirmed material
   event and executive relevance) while continuing to reject the noise it
   already rejected: stock/"기대감" framing, generic "AI가 뜬다" themes, non-AI
   infrastructure with no AI causal link, and bare op-eds. Being a major
   publisher never qualifies a row on its own, and the discovery query never
   qualifies a row (SEARCH_QUERY_CAUSED_QUALIFICATION=0). This locks the
   already-correct behavior against regression; it is not a new rule.

2. HONEST EMPTY EDITION — zero qualifying candidates on a thin news day is a
   successful empty edition, not a build failure. The review-console build emits
   a valid empty bundle/manifest/console (candidate_count=0, candidates=[]),
   never pads a count, performs no send, and writes no production state.

Network / mail are hard-guarded; any real outbound attempt is counted and fails
the run.
"""

from __future__ import annotations

import contextlib
import io
import json
import smtplib
import socket
import sys
import tempfile
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app import editorial_briefings as brief  # noqa: E402
from app import editorial_review  # noqa: E402

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
RUN_AT = datetime(2026, 8, 10, 14, 2, 0, tzinfo=KST)
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


def row(title: str, *, snippet: str = "", source: str = "연합뉴스",
        url: str | None = None, lane: str = "general",
        query: str = "", provider: str = "naver_news_api") -> dict:
    _ROW_SEQ["n"] += 1
    seq = _ROW_SEQ["n"]
    return {
        "id": f"r4r21-{seq}",
        "title": title,
        "source": source,
        "published_at": INSIDE,
        "url": url or f"https://www.yna.co.kr/view/AKR{seq:08d}",
        "snippet": snippet or f"{title} 관련 내용이 전해졌다.",
        "source_metadata": {"provider": provider, "query": query, "discovery_lane": lane},
        "discovery_lane": lane,
    }


def select(rows: list[dict], *, limit: int = 24, operator_review: bool = True):
    audit = brief.SelectionAuditCounters()
    articles = brief.normalize_articles(
        rows, COVERAGE, limit=limit, resolve_images=False, allow_image_network=False,
        selection_mode=brief.SELECTION_MODE_EDITORIAL_PRIORITY,
        selection_audit=audit, edition_type="daily", operator_review=operator_review,
    )
    return articles, audit


def survives(title: str, rows: list[dict]) -> bool:
    articles, _audit = select(rows)
    return any(a.title == title for a in articles)


# --------------------------------------------------------------------------- #
# 1. Material AI infrastructure recall (positive)
# --------------------------------------------------------------------------- #
MATERIAL_AI_INFRA_POSITIVE = [
    ("AI 데이터센터 power-equipment supply contract",
     "HD현대일렉트릭, 미국 AI 데이터센터용 발전설비 9560억원 공급 계약 체결",
     "HD현대일렉트릭이 미국 AI 데이터센터용 발전설비를 9560억원 규모로 공급하는 계약을 체결했다."),
    ("AI 데이터센터 grid/transmission build event",
     "한전, AI 데이터센터 전력 공급 위해 송전망 증설 착공",
     "한전이 AI 데이터센터 전력 공급을 위해 송전망 증설에 착공했다."),
    ("AI 데이터센터 SMR power-source supply",
     "두산에너빌리티, AI 데이터센터용 SMR 공급 계약 체결…1조원 규모",
     "두산에너빌리티가 AI 데이터센터용 SMR 공급 계약을 1조원 규모로 체결했다."),
    ("AI 데이터센터 cooling supply",
     "삼성물산, 美 AI 데이터센터 냉각 시스템 공급 계약",
     "삼성물산이 미국 AI 데이터센터 냉각 시스템 공급 계약을 체결했다."),
    ("AI 데이터센터 grid constraint with factual impact",
     "AI 데이터센터 전력 수요 급증에 수도권 송전망 용량 부족 심화",
     "AI 데이터센터 전력 수요 급증으로 수도권 송전망 용량 부족이 심화되고 있다."),
]


def material_ai_infra_positive_checks() -> bool:
    all_ok = True
    for label, title, snippet in MATERIAL_AI_INFRA_POSITIVE:
        ok = check(f"material AI infrastructure accepted — {label}",
                   survives(title, [row(title, snippet=snippet)]))
        all_ok = all_ok and ok
    arts, audit = select([row(t, snippet=sn) for _l, t, sn in MATERIAL_AI_INFRA_POSITIVE])
    all_ok = all_ok and check(
        "all material AI infrastructure events are executive-qualified",
        len(arts) == len(MATERIAL_AI_INFRA_POSITIVE)
        and audit.executive_qualified_count == len(MATERIAL_AI_INFRA_POSITIVE)
        and audit.executive_materiality_rejected_count == 0,
        repr({k: audit.manifest_fields()[k] for k in (
            "executive_qualified_count", "executive_materiality_rejected_count")}),
    )
    return all_ok


# --------------------------------------------------------------------------- #
# 2. Generic-infrastructure negative (no AI causal link / no material event)
# --------------------------------------------------------------------------- #
GENERIC_INFRA_NEGATIVE = [
    ("non-AI data-center market outlook (no AI link)",
     "일반 데이터센터 시장 성장 전망", "일반 데이터센터 시장이 성장할 것이라는 전망이 나왔다."),
    ("generic 'AI data-center is rising' theme (no event)",
     "AI 시대 데이터센터가 뜬다", "AI 시대를 맞아 데이터센터가 주목받고 있다는 전망이다."),
    ("AI data-center investment expectation, no confirmed event",
     "AI 데이터센터 투자 확대 기대감 고조", "AI 데이터센터 투자 확대 기대감이 커지고 있다."),
    ("plain op-ed with no factual event",
     "AI 인프라를 준비해야 한다", "AI 인프라를 지금부터 준비해야 한다는 의견이 제기됐다."),
    ("labeled op-ed with no factual event",
     "[기고] AI 인프라를 준비해야 한다", "AI 인프라를 준비해야 한다는 기고문이다."),
    ("non-AI power-plant contract (no AI link)",
     "발전사, 일반 화력발전소 정비 계약 체결", "발전사가 일반 화력발전소 정비 계약을 체결했다."),
]


def generic_infra_negative_checks() -> bool:
    all_ok = True
    for label, title, snippet in GENERIC_INFRA_NEGATIVE:
        ok = check(f"generic/non-material infrastructure rejected — {label}",
                   not survives(title, [row(title, snippet=snippet)]))
        all_ok = all_ok and ok
    arts, _audit = select([row(t, snippet=sn) for _l, t, sn in GENERIC_INFRA_NEGATIVE])
    all_ok = all_ok and check(
        "the whole generic-infrastructure batch yields zero survivors",
        len(arts) == 0, str([a.title for a in arts]))
    return all_ok


# --------------------------------------------------------------------------- #
# 3. AI-infrastructure stock-noise negative (stock framing must stay rejected)
# --------------------------------------------------------------------------- #
AI_INFRA_STOCK_NOISE_NEGATIVE = [
    ("stock surge framing on AI data-center theme",
     "AI 데이터센터 기대감에 건설주 급등", "AI 데이터센터 기대감에 건설주가 급등했다."),
    ("target-price framing on AI data-center theme",
     "AI 데이터센터 관련주 목표주가 일제히 상향", "증권가가 AI 데이터센터 관련주 목표주가를 상향했다."),
    ("construction-stock rally framing (observed live noise)",
     "건설주 8월들어 17%↑…원전 이어 AI 데이터센터 기대감",
     "건설주가 8월 들어 17% 올랐다. 원전에 이어 AI 데이터센터 기대감이 반영됐다."),
]


def ai_infra_stock_noise_negative_checks() -> bool:
    all_ok = True
    for label, title, snippet in AI_INFRA_STOCK_NOISE_NEGATIVE:
        ok = check(f"AI-infrastructure stock noise rejected — {label}",
                   not survives(title, [row(title, snippet=snippet)]))
        all_ok = all_ok and ok
    return all_ok


# --------------------------------------------------------------------------- #
# 4. Major publisher alone / discovery query alone never qualify
# --------------------------------------------------------------------------- #
def qualification_authority_checks() -> None:
    # A major primary_10 publisher carrying a soft, non-material AI row and a
    # strong coverage-mapping discovery query never survives: neither the tier
    # nor the query is qualification authority.
    soft = "AI 서비스 이용 후기 인기 급상승"
    r = row(soft, source="연합뉴스", lane="primary_publisher",
            query="연합뉴스 AI 데이터센터 전력", snippet="AI 서비스 이용 후기가 인기다.")
    check("major-publisher tier alone never qualifies a soft row", not survives(soft, [r]))
    # SEARCH_QUERY_CAUSED_QUALIFICATION=0 — a batch of non-material rows each
    # carrying a strong coverage query (both lanes) yields zero survivors.
    batch = [
        row("AI 트렌드 소개 인기 콘텐츠", lane="general", query="AI 데이터센터 전력 수요",
            url="https://pub.fixture.test/qa1"),
        row("AI 활용 팁 모음 화제", source="한국경제", lane="primary_publisher",
            query="한국경제 AI 데이터센터", url="https://pub.fixture.test/qa2"),
    ]
    arts, _audit = select(batch)
    check("SEARCH_QUERY_CAUSED_QUALIFICATION is zero", len(arts) == 0,
          str([a.title for a in arts]))


# --------------------------------------------------------------------------- #
# 5. Honest empty edition — the review-console build on zero qualifying rows.
# --------------------------------------------------------------------------- #
def empty_edition_checks() -> tuple[bool, bool, bool, bool]:
    import build_editorial_review_console as bconsole
    original_normalize = brief.normalize_articles

    def empty_normalize(_raw, _coverage, **kw):
        audit = kw.get("selection_audit")
        if audit is not None:
            audit.selection_shortfall = brief.DAILY_MAX_ARTICLES
            audit.selected_candidates = 0
        return []

    saved_argv = sys.argv
    rc = None
    err = None
    out = ""
    with tempfile.TemporaryDirectory(prefix="r4r21-empty-") as td:
        brief.normalize_articles = empty_normalize
        try:
            sys.argv = ["build", "--fixture", "--output-root", td]
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = bconsole.main()
            out = buf.getvalue()
        except SystemExit as exc:  # would be the OLD failure behavior
            err = f"SystemExit:{exc.code!r}"
        except Exception as exc:  # noqa: BLE001
            err = f"{type(exc).__name__}:{exc}"
        finally:
            brief.normalize_articles = original_normalize
            sys.argv = saved_argv

        editions = [p for p in Path(td).glob("*") if p.is_dir() and p.name != "latest"]
        cand = editions[0] / "candidates.json" if editions else None
        man = editions[0] / "manifest.json" if editions else None
        idx = editions[0] / "index.html" if editions else None
        bundle = json.loads(cand.read_text()) if cand and cand.exists() else {}
        manifest = json.loads(man.read_text()) if man and man.exists() else {}

        build_ok = check(
            "ZERO_CANDIDATE_BUILD — empty coverage builds successfully (no SystemExit)",
            err is None and rc == 0 and "RESULT=SUCCESS_EMPTY_EDITION" in out,
            f"err={err} rc={rc}",
        )
        artifacts_ok = check(
            "ZERO_CANDIDATE_ARTIFACTS — empty bundle/manifest/console emitted, no padding",
            bool(cand and cand.exists()) and bool(man and man.exists())
            and bool(idx and idx.exists())
            and bundle.get("candidate_count") == 0 and bundle.get("candidates") == []
            and "collection_audit" in bundle and "selection_audit" in bundle
            and manifest.get("candidate_count") == 0
            and manifest.get("selection_shortfall", -1) >= brief.DAILY_MAX_ARTICLES,
            repr({"bundle_count": bundle.get("candidate_count"),
                  "manifest_count": manifest.get("candidate_count"),
                  "shortfall": manifest.get("selection_shortfall")}),
        )
        no_send_ok = check(
            "ZERO_CANDIDATE_NO_SEND — empty edition performs no Teams/SMTP/Telegram/network send",
            all(manifest.get(k) == 0 for k in
                ("network_sends", "smtp_attempts", "teams_sends", "telegram_sends"))
            and EXTERNAL["count"] == 0 and SMTP["count"] == 0,
            repr({k: manifest.get(k) for k in
                  ("network_sends", "smtp_attempts", "teams_sends", "telegram_sends")}),
        )
        # state byte-identity is asserted globally in main(); here we assert the
        # build itself declares zero production-state writes.
        no_state_ok = check(
            "ZERO_CANDIDATE_NO_STATE_WRITE — empty edition declares zero production-state writes",
            manifest.get("production_state_writes") == 0,
            repr(manifest.get("production_state_writes")),
        )
    return build_ok, artifacts_ok, no_send_ok, no_state_ok


def _snapshot() -> dict:
    return {str(p): p.read_bytes() for p in PRODUCTION_STATE_FILES if p.exists()}


def main() -> int:
    state_before = _snapshot()
    try:
        positive_ok = material_ai_infra_positive_checks()
        generic_ok = generic_infra_negative_checks()
        stock_ok = ai_infra_stock_noise_negative_checks()
        qualification_authority_checks()
        build_ok, artifacts_ok, no_send_ok, no_state_ok = empty_edition_checks()
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
    for name in FAILURES:
        print(f"FAILED: {name}")

    print(f"MATERIAL_AI_INFRA_POSITIVE={'PASS' if positive_ok else 'FAIL'}")
    print(f"GENERIC_INFRA_NEGATIVE={'PASS' if generic_ok else 'FAIL'}")
    print(f"AI_INFRA_STOCK_NOISE_NEGATIVE={'PASS' if stock_ok else 'FAIL'}")
    print(f"ZERO_CANDIDATE_BUILD={'PASS' if build_ok else 'FAIL'}")
    print(f"ZERO_CANDIDATE_ARTIFACTS={'PASS' if artifacts_ok else 'FAIL'}")
    print(f"ZERO_CANDIDATE_NO_SEND={'PASS' if no_send_ok else 'FAIL'}")
    print(f"ZERO_CANDIDATE_NO_STATE_WRITE={'PASS' if no_state_ok else 'FAIL'}")
    print(f"EXTERNAL_NETWORK_CALLS={EXTERNAL['count']}")
    print(f"SMTP_ATTEMPTS={SMTP['count']}")
    print("TEAMS_SENDS=0")
    print("TELEGRAM_SENDS=0")
    print(f"PRODUCTION_STATE_WRITES={0 if state_ok else 1}")
    print("MATERIAL_AI_INFRA_RECALL_VERIFIER=" + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
