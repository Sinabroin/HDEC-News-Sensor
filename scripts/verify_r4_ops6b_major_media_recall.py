#!/usr/bin/env python3
"""Offline R4-OPS-6B recall/authority regression matrix (network: zero)."""

from __future__ import annotations

import copy
import json
import socket
import sys
from email.message import Message
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import editorial_article_import as article_import  # noqa: E402
from app import live_collector, naver_news_provider, publisher_direct, source_priority  # noqa: E402
from app.teams_ai_push import evaluate_teams_push_policy  # noqa: E402
from scripts import send_teams_ai_push  # noqa: E402

FAILURES: list[str] = []
PASSES = 0


def check(label: str, condition: bool, detail: object = "") -> bool:
    global PASSES
    if condition:
        PASSES += 1
        print(f"PASS {label}")
        return True
    FAILURES.append(label)
    print(f"FAIL {label}" + (f" — {detail}" if detail else ""))
    return False


def resolver(host: str, port: int, **_kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]


class Response:
    def __init__(self, url: str, body: str):
        self.status = 200
        self._url = url
        self._body = body.encode("utf-8")
        self._offset = 0
        self.headers = Message()
        self.headers["Content-Type"] = "text/html; charset=utf-8"
        self.headers["Content-Length"] = str(len(self._body))

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self._body) - self._offset
        start = self._offset
        self._offset = min(len(self._body), start + size)
        return self._body[start:self._offset]

    def getcode(self):
        return self.status

    def geturl(self):
        return self._url

    def close(self):
        return None


class Opener:
    def __init__(self, routes: dict[str, str]):
        self.routes = routes

    def open(self, request, timeout=0):
        url = request.full_url
        if url not in self.routes:
            raise AssertionError(f"unexpected fixture URL: {url}")
        return Response(url, self.routes[url])


def google_row(index: int, source: str, source_home: str) -> dict:
    wrapper = f"https://news.google.com/rss/articles/fixture-{index}?oc=5"
    return {
        "id": f"g-{index}",
        "title": f"AI 데이터센터 전력 인프라 계약 {index}",
        "source": source,
        "published_at": "2026-08-11T10:00:00+09:00",
        "url": wrapper,
        "discovery_url": wrapper,
        "publisher_direct": False,
        "source_metadata": {
            "provider": "google_news_rss",
            "discovery_url": wrapper,
            "rss_source_home_url": source_home,
            "rss_source_url": source_home,
        },
    }


def scheduling_contracts() -> tuple[int, bool, int]:
    major_domains = list(
        source_priority.locked_publisher_domain_map(
            ("primary_10", "secondary_3", "major_secondary")
        ).items()
    )
    priority_probe = [
        google_row(index, f"Neutral {index}", f"https://neutral-{index}.example/")
        for index in range(40)
    ] + [
        google_row(100 + index, name, f"https://{domain}/")
        for index, (domain, name) in enumerate(major_domains[:20])
    ]
    prioritized = live_collector._round_robin_publishers(priority_probe)
    check(
        "exact configured Google source hints receive scheduling priority only",
        all(
            live_collector._resolution_scheduling_hint_tier(row)
            in {"primary_10", "secondary_3", "major_secondary"}
            for row in prioritized[:20]
        ),
    )
    original = live_collector._strict_publisher_authority

    def isolated(row, **_kwargs):
        return publisher_direct.quarantine_article(row, "offline_resolution_probe")

    live_collector._strict_publisher_authority = isolated
    try:
        distributed = [
            google_row(index, f"Publisher {index % 25}", f"https://p{index % 25}.example/")
            for index in range(275)
        ]
        metrics: dict = {}
        live_collector.resolve_publisher_urls(
            distributed,
            strict=True,
            max_items=60,
            per_host_max_items=12,
            workers=4,
            deadline=20,
            metrics=metrics,
        )
        active_buckets = sum(
            value["attempts"] > 0
            for value in metrics["per_scheduling_bucket"].values()
        )
        global_starvation = int(
            metrics["attempted_count"] <= 12 or active_buckets <= 1
        )
        check(
            "275 Google rows are distributed beyond one news.google.com cap",
            metrics["attempted_count"] == 60 and active_buckets >= 20,
            {"attempted": metrics["attempted_count"], "buckets": active_buckets},
        )

        claimed = [
            google_row(index, "One Publisher", "https://one-publisher.example/")
            for index in range(275)
        ]
        one_metrics: dict = {}
        live_collector.resolve_publisher_urls(
            claimed,
            strict=True,
            max_items=60,
            per_host_max_items=12,
            workers=4,
            deadline=20,
            metrics=one_metrics,
        )
        per_publisher_bound = one_metrics["attempted_count"] == 12
        check("one claimed publisher remains bounded to 12 attempts", per_publisher_bound)
    finally:
        live_collector._strict_publisher_authority = original

    spoof = publisher_direct.apply_publisher_authority(
        google_row(999, "연합뉴스", "https://www.yna.co.kr/"),
        publisher_canonical_url="https://foreign.example/news/999",
        source="연합뉴스",
        published_at="2026-08-11T10:00:00+09:00",
        resolution_reason="offline_spoof_probe",
    )
    spoof_policy = source_priority.teams_delivery_source_policy(
        spoof["source"], spoof["url"]
    )
    unresolved = google_row(1000, "연합뉴스", "https://www.yna.co.kr/")
    authority_leaks = int(
        spoof_policy["tier"] != "neutral"
        or spoof_policy["identity_evidence"] != "unrecognized_url_host"
        or publisher_direct.is_publisher_direct_delivery_eligible(
            unresolved, relevance_qualified=True
        )
    )
    check("spoofed scheduling hint cannot elevate a foreign resolved URL", authority_leaks == 0)
    check("unresolved Google wrapper remains non-authoritative", not publisher_direct.is_publisher_direct_delivery_eligible(unresolved, relevance_qualified=True))
    return global_starvation, per_publisher_bound, authority_leaks


def structured_html(canonical: str, title: str, source: str) -> str:
    body = (
        "정부와 기업은 AI 데이터센터 전력 인프라 구축 계약을 확정하고 투자 규모와 "
        "실행 일정을 공개했다. GPU와 HBM 공급망, 냉각 설비, 직류 배전 계획을 포함해 "
        "건설 및 산업 조직의 의사결정에 필요한 구체적 범위가 제시됐다. "
    ) * 3
    payload = {
        "@context": "https://schema.org",
        "@type": "NewsArticle",
        "headline": title,
        "publisher": {"@type": "Organization", "name": source},
        "datePublished": "2026-08-11T10:00:00+09:00",
        "articleBody": body,
    }
    return (
        "<html><head>"
        f'<link rel="canonical" href="{canonical}">'
        f'<script type="application/ld+json">{json.dumps(payload, ensure_ascii=False)}</script>'
        "</head><body></body></html>"
    )


def extractor_contracts() -> tuple[bool, bool, int]:
    matrix: dict[str, bool] = {}
    for tier in ("primary_10", "secondary_3", "major_secondary"):
        domain_map = source_priority.locked_publisher_domain_map((tier,))
        domains_by_name: dict[str, str] = {}
        for domain, name in domain_map.items():
            domains_by_name.setdefault(name, domain)
        for name in source_priority.locked_publisher_names(tier):
            domain = domains_by_name[name]
            canonical = f"https://{domain}/news/article/20260811/{len(matrix) + 1}"
            try:
                extracted = article_import.extract_article(
                    structured_html(canonical, f"{name} AI 인프라 구축 계약", name),
                    canonical,
                    resolver=resolver,
                )
                matrix[name] = bool(
                    extracted.body_source == "json_ld_article_body"
                    and extracted.canonical_url == canonical
                )
            except article_import.ArticleImportError:
                matrix[name] = False
    tier_a_names = set(source_priority.locked_publisher_names("primary_10")) | set(
        source_priority.locked_publisher_names("secondary_3")
    )
    tier_b_names = set(source_priority.locked_publisher_names("major_secondary"))
    tier_a_pass = len(tier_a_names) == 13 and all(matrix.get(name) for name in tier_a_names)
    tier_b_pass = len(tier_b_names) == 16 and all(matrix.get(name) for name in tier_b_names)
    check("Tier-A13 structured extractor matrix", tier_a_pass)
    check("Tier-B16 structured extractor matrix", tier_b_pass)
    dom_cases = {
        "imbc.com": "news_txt",
        "kbs.co.kr": "cont_newstext",
        "chosun.com": "news_body_id",
        "ytn.co.kr": "cmadcontent",
        "jtbc.co.kr": "articlebody",
        "hankyung.com": "articletxt",
        "donga.com": "article_txt",
        "biz.chosun.com": "article-body",
        "news1.kr": "articles_detail",
        "edaily.co.kr": "articlebody",
        "kmib.co.kr": "articlebody",
        "seoul.co.kr": "articlecontent",
    }
    dom_failures: list[str] = []
    dom_body = (
        "AI 데이터센터 전력 인프라 구축 계약과 투자 일정이 공식 확정됐다. "
        "GPU HBM 공급망 및 냉각 직류배전 범위를 구체적으로 공개했다. "
    ) * 5
    for index, (host, selector) in enumerate(dom_cases.items()):
        canonical = f"https://{host}/news/article/20260811/dom-{index}"
        html = (
            "<html><head><title>AI 데이터센터 인프라 계약</title>"
            f'<link rel="canonical" href="{canonical}"></head>'
            f'<body><div class="{selector}">{dom_body}</div></body></html>'
        )
        try:
            extracted = article_import.extract_article(html, canonical, resolver=resolver)
            if extracted.body_source != "publisher_exact_host_container":
                dom_failures.append(host)
        except article_import.ArticleImportError:
            dom_failures.append(host)
    check("affected-publisher bounded DOM selector matrix", not dom_failures, dom_failures)

    metadata_matrix: dict[str, bool] = {}
    for tier in ("primary_10", "secondary_3", "major_secondary"):
        domain_map = source_priority.locked_publisher_domain_map((tier,))
        domains_by_name: dict[str, str] = {}
        for domain, name in domain_map.items():
            domains_by_name.setdefault(name, domain)
        for index, name in enumerate(source_priority.locked_publisher_names(tier)):
            domain = domains_by_name[name]
            target = f"https://{domain}/news/article/20260811/meta-{tier}-{index}"
            page_title = f"{name} AI 데이터센터 전력 인프라 계약"
            page = (
                "<html><head>"
                f"<title>{page_title}</title>"
                f'<meta property="og:title" content="{page_title}">'
                f'<link rel="canonical" href="{target}">'
                "</head><body><div>본문 선택자가 없는 짧은 메타데이터 페이지</div></body></html>"
            )
            matrix_row = google_row(
                2100 + len(metadata_matrix), name, f"https://{domain}/"
            )
            matrix_row["title"] = page_title
            result = live_collector._strict_publisher_authority(
                matrix_row,
                resolver=resolver,
                opener=Opener({target: page}),
                decoder=lambda _url, destination=target: destination,
            )
            metadata_matrix[name] = bool(
                result.get("publisher_direct") is True
                and result.get("publisher_verification_strength")
                == "metadata_only_exact_host"
            )
    check(
        "all Tier-A13/Tier-B16 exact hosts pass bounded metadata-only matrix",
        len(metadata_matrix) == 29 and all(metadata_matrix.values()),
        [name for name, passed in metadata_matrix.items() if not passed],
    )

    title = "LS일렉트릭, GS건설과 AI데이터센터 직류 배전 사업 협력"
    exact = "https://www.donga.com/news/Economy/article/all/20260811/123456789/1"
    page = (
        "<html><head>"
        f"<title>{title}</title>"
        f'<meta property="og:title" content="{title}">'
        f'<link rel="canonical" href="{exact}">'
        "</head><body><div>짧은 기사 메타데이터 페이지</div></body></html>"
    )
    row = google_row(2000, "동아일보", "https://www.donga.com/")
    row["title"] = title
    verified = live_collector._strict_publisher_authority(
        row,
        resolver=resolver,
        opener=Opener({exact: page}),
        decoder=lambda _url: exact,
    )
    check(
        "exact Tier-A article identity permits bounded metadata-only verification",
        verified.get("publisher_verification_strength") == "metadata_only_exact_host"
        and verified.get("publisher_direct") is True,
        verified.get("portal_resolution_reason"),
    )

    escaping = "https://foreign.example/news/article/20260811/1"
    escaping_page = page.replace(exact, escaping)
    escaped = live_collector._strict_publisher_authority(
        google_row(2001, "동아일보", "https://www.donga.com/"),
        resolver=resolver,
        opener=Opener({exact: escaping_page}),
        decoder=lambda _url: exact,
    )
    sibling = "https://it.chosun.com/news/articleView.html?idxno=1234"
    sibling_page = page.replace(exact, sibling).replace(title, "조선일보 AI 데이터센터 계약")
    sibling_result = live_collector._strict_publisher_authority(
        google_row(2002, "조선일보", "https://chosun.com/"),
        resolver=resolver,
        opener=Opener({sibling: sibling_page}),
        decoder=lambda _url: sibling,
    )
    sbs_premium = "https://premium.sbs.co.kr/article/synthetic-boundary"
    sbs_page = page.replace(exact, sbs_premium).replace(title, "SBS AI 데이터센터 계약")
    sbs_result = live_collector._strict_publisher_authority(
        google_row(2003, "SBS", "https://sbs.co.kr/"),
        resolver=resolver,
        opener=Opener({sbs_premium: sbs_page}),
        decoder=lambda _url: sbs_premium,
    )
    unknown = "https://unknown.example/news/article/20260811/1"
    unknown_page = page.replace(exact, unknown).replace(title, "연합뉴스 AI 데이터센터 계약")
    unknown_result = live_collector._strict_publisher_authority(
        google_row(2004, "연합뉴스", "https://yna.co.kr/"),
        resolver=resolver,
        opener=Opener({unknown: unknown_page}),
        decoder=lambda _url: unknown,
    )
    broadening = sum(
        result.get("publisher_direct") is True
        for result in (escaped, sibling_result, sbs_result, unknown_result)
    )
    check(
        "metadata-only authority rejects foreign canonical, IT조선, SBS Premium, and unknown",
        broadening == 0,
    )
    return tier_a_pass, tier_b_pass, broadening


def naver_contracts() -> tuple[bool, bool, bool]:
    cfg = json.loads((ROOT / "data/naver_news_sources.json").read_text(encoding="utf-8"))
    topics = cfg["primary_publisher_lane"]["topics"]
    specs = naver_news_provider.primary_publisher_query_specs(topics, max_queries=999)
    tier_a = {spec["publisher"] for spec in specs if spec["tier"] in {"primary_10", "secondary_3"}}
    tier_b = {spec["publisher"] for spec in specs if spec["tier"] == "major_secondary"}
    a_pass = len(tier_a) == 13
    b_pass = len(tier_b) == 16
    check("Naver targeted lane covers all Tier-A13 publishers", a_pass)
    check("Naver targeted lane covers all Tier-B16 publishers", b_pass)
    check("all bounded material-event topic families are scheduled", set(topics) <= {spec["topic"] for spec in specs})
    probe = specs[0]
    domain = probe["domains"][0]
    exact_row = {"source": probe["publisher"], "url": f"https://{domain}/news/article/1"}
    sibling_row = {"source": probe["publisher"], "url": f"https://unlisted.{domain}/news/article/1"}
    exact_only = (
        naver_news_provider._publisher_lane_accepts(exact_row, probe)
        and not naver_news_provider._publisher_lane_accepts(sibling_row, probe)
    )
    check("Naver targeted lane accepts exact publisher identity only", exact_only)
    check(
        "머니투데이/디지털타임스 hosts do not collide with the t.co ban",
        not naver_news_provider._is_forbidden("https://mt.co.kr/news/1")
        and not naver_news_provider._is_forbidden("https://dt.co.kr/news/1")
        and naver_news_provider._is_forbidden("https://t.co/blocked"),
    )
    return a_pass, b_pass, exact_only


def recall_contracts() -> dict[str, bool]:
    fixture = json.loads(
        (ROOT / "data/r4_ops6b_major_media_recall.json").read_text(encoding="utf-8")
    )
    outcomes: dict[str, bool] = {}
    for index, case in enumerate(fixture["rows"]):
        target = case["synthetic_adversarial_destination"]
        title = case["observed_title"]
        row = google_row(3000 + index, case["observed_source"], f"https://{case['observed_source_domain']}/")
        row.update({
            "url": case["observed_google_wrapper_url"],
            "discovery_url": case["observed_google_wrapper_url"],
            "title": title,
            "snippet": "AI 데이터센터 전력 인프라 투자와 계약, 금융 플랫폼 구축 계획을 공식 발표했다.",
            "summary": "AI 인프라 투자를 위한 구체적 계약과 구축 계획이 발표됐다.",
            "hdec_relevance": "AI 데이터센터 EPC 및 전력 인프라 사업 기회",
            "published_at": case["observed_published_kst"].replace(" ", "T") + ":00+09:00",
            "score": 4.2,
            "current_run_seen": True,
            "change_type": "new_article",
        })
        row["source_metadata"]["discovery_url"] = case["observed_google_wrapper_url"]
        verified = live_collector._strict_publisher_authority(
            row,
            resolver=resolver,
            opener=Opener({target: structured_html(target, title, case["observed_source"])}),
            decoder=lambda _url, destination=target: destination,
        )
        tier = source_priority.publisher_delivery_tier(verified.get("source", ""), verified.get("url", ""))
        evaluate_teams_push_policy(verified)
        reachable = bool(
            verified.get("publisher_direct") is True
            and tier["tier"] in {"primary_10", "secondary_3", "major_secondary"}
            and tier["identity_evidence"] == "exact_domain"
        )
        outcomes[case["case_id"]] = reachable
        check(f"{case['case_id']} reaches existing content/source policy", reachable)
    return outcomes


def precision_contracts() -> tuple[dict[str, bool], int]:
    replay = json.loads((ROOT / "data/r4_ops5_production_replay.json").read_text(encoding="utf-8"))
    outcomes: dict[str, bool] = {}
    query_leaks = 0
    for row in replay["rows"]:
        if row.get("expected_policy_eligible") is None:
            continue
        policy = evaluate_teams_push_policy(row)
        outcomes[row["case_id"]] = policy.eligible
        without_query = dict(row)
        without_query.pop("search_query", None)
        query_leaks += int(policy.eligible and not evaluate_teams_push_policy(without_query).eligible)
        check(
            f"precision replay {row['case_id']}",
            policy.eligible is bool(row["expected_policy_eligible"]),
            policy.rejection_reason,
        )
    return outcomes, query_leaks


def observability_contract() -> bool:
    major = {"article_key": "major", "source": "연합뉴스", "url": "https://yna.co.kr/view/A1"}
    neutral = {
        "article_key": "neutral",
        "source": "https://source-label-must-not-leak.example/path",
        "url": "https://neutral.example/news/1",
    }
    major_candidate = SimpleNamespace(article=major)
    neutral_candidate = SimpleNamespace(article=neutral)
    gate_batch = SimpleNamespace(
        rejected=(SimpleNamespace(candidate=neutral_candidate, gate=SimpleNamespace(reason="source_tier_not_eligible")),),
        held=(),
        deferred_major=(),
        selected=(SimpleNamespace(candidate=major_candidate, gate=SimpleNamespace(reason="primary_publisher_immediate")),),
    )
    traces, reasons, tiers = send_teams_ai_push._policy_eligible_row_traces(
        article_rows=[major, neutral],
        policy_evaluations=[SimpleNamespace(eligible=True), SimpleNamespace(eligible=True)],
        candidates=[major_candidate, neutral_candidate],
        baseline=[SimpleNamespace(send_allowed=True), SimpleNamespace(send_allowed=True)],
        gate_batch=gate_batch,
    )
    allowed = {
        "article_ref", "display_source", "resolved_publisher_identity",
        "resolved_source_tier", "teams_lane", "content_eligibility_state",
        "source_gate_result", "source_gate_reason",
    }
    safe = bool(
        len(traces) == 2
        and all(set(trace) == allowed for trace in traces)
        and reasons == {"source_tier_not_eligible": 1}
        and tiers == {"primary_10": 1, "neutral": 1}
        and all("http" not in json.dumps(trace) for trace in traces)
        and traces[1]["display_source"] == "redacted_source"
    )
    check("every eligible row has only the safe categorical trace fields", safe)
    return safe


def main() -> int:
    starvation, publisher_bound, authority_leaks = scheduling_contracts()
    tier_a_extract, tier_b_extract, metadata_broadening = extractor_contracts()
    naver_a, naver_b, naver_exact = naver_contracts()
    recall = recall_contracts()
    precision, query_leaks = precision_contracts()
    observability = observability_contract()

    print(f"GOOGLE_RESOLUTION_GLOBAL_STARVATION={starvation}")
    print(f"GOOGLE_RESOLUTION_PER_PUBLISHER_BOUND={'PASS' if publisher_bound else 'FAIL'}")
    print(f"SCHEDULING_HINT_AUTHORITY_LEAK={authority_leaks}")
    print(f"TIER_A_EXTRACTOR_REGRESSION_MATRIX={'PASS' if tier_a_extract else 'FAIL'}")
    print(f"TIER_B_EXTRACTOR_REGRESSION_MATRIX={'PASS' if tier_b_extract else 'FAIL'}")
    print(f"METADATA_ONLY_AUTHORITY_BROADENING={metadata_broadening}")
    print(f"NAVER_TIER_A13_TARGETED_COVERAGE={'PASS' if naver_a else 'FAIL'}")
    print(f"NAVER_TIER_B16_TARGETED_COVERAGE={'PASS' if naver_b else 'FAIL'}")
    print(f"NAVER_EXACT_PUBLISHER_MATCH_ONLY={'PASS' if naver_exact else 'FAIL'}")
    print(f"DONGA_KEEP_CLASS_REACHABLE_TO_POLICY={'PASS' if recall.get('donga_gs_ls_ai_datacenter_power') else 'FAIL'}")
    print(f"CHOSUNBIZ_KEEP_CLASS_REACHABLE_TO_POLICY={'PASS' if recall.get('chosunbiz_nvidia_wall_street_ai_infra') else 'FAIL'}")
    print(f"YONHAP_AI_INFRA_KEEP_CLASS_REACHABLE_TO_POLICY={'PASS' if recall.get('yonhap_nvidia_ai_infra_financing') else 'FAIL'}")
    print(f"ZERO_SEND_OBSERVABILITY={'PASS' if observability else 'FAIL'}")
    print(f"SEARCH_QUERY_CAUSED_QUALIFICATION={query_leaks}")
    print(f"FOCUSED_TESTS_PASS={PASSES}")
    print(f"FOCUSED_TESTS_FAIL={len(FAILURES)}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
