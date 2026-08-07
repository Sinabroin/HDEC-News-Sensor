#!/usr/bin/env python3
"""D7-AK-6E R4-R16 — primary-publisher bounded discovery lane verifier.

An audit found the 2026-08-07 Review lane surfaced primary_10=0 / secondary_3=0
/ official=0 for a coverage window whose same-window News Censor had
primary_10=2 / official=2. The configured roster carried 0 publisher-targeted
queries and the core-publisher host map covered only 4/10 publishers (MBC, KBS,
YTN, JTBC, 중앙일보, SBS were missing) — a REVIEW_COLLECTION_OR_SELECTION_RECALL_GAP.

This verifier proves, entirely offline (no external network, no SMTP, no Teams,
no Telegram, no production-state write, no delivery-gate change), that the new
bounded primary-publisher discovery lane:

* derives exactly ten canonical primary publishers from the operator-locked
  policy (data/source_priority_rules.json) — never a duplicated publisher list;
* normalizes the six previously-missing publisher domains into the host map;
* is hard-clamped in code to ≤30 queries, ≤2 rows/query, ≤40 rows total;
* runs with a budget independent of the general lane (each runs regardless of
  the other's exhaustion);
* accepts a result only as its expected primary_10 target publisher, rejecting
  cross-publisher, secondary_3, neutral, specialist, and official results even
  when the Naver query text contains the publisher name;
* dedups final canonical URLs deterministically across both lanes;
* emits six accurate audit counters that propagate to the Review manifest.

The request adapter is monkeypatched and DNS/urlopen/SMTP are guarded, so any
real network or mail attempt is counted and fails the run.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import smtplib
import socket
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
for _p in (str(ROOT), str(SCRIPTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app import editorial_briefings as brief  # noqa: E402
from app import naver_news_provider as nn  # noqa: E402
from app import source_priority  # noqa: E402

# --------------------------------------------------------------------------- #
# Network / mail guards. All provider requests are routed through a fake adapter
# (below); these guards ensure that if any code path attempted a REAL outbound
# call it would be counted and fail the run. DNS + urlopen cover network; the
# SMTP guard covers mail. Subprocess pipes/file I/O are unaffected.
# --------------------------------------------------------------------------- #
EXTERNAL = {"count": 0}
SMTP = {"count": 0}

_orig_getaddrinfo = socket.getaddrinfo
_orig_create_connection = socket.create_connection
_orig_urlopen = urllib.request.urlopen


def _blocked_getaddrinfo(*_args, **_kwargs):
    EXTERNAL["count"] += 1
    raise RuntimeError("external DNS resolution is blocked in this verifier")


def _blocked_create_connection(*_args, **_kwargs):
    EXTERNAL["count"] += 1
    raise RuntimeError("external socket connection is blocked in this verifier")


def _blocked_urlopen(*_args, **_kwargs):
    EXTERNAL["count"] += 1
    raise RuntimeError("external urlopen is blocked in this verifier")


class _BlockedSMTP:
    def __init__(self, *_args, **_kwargs):
        SMTP["count"] += 1
        raise RuntimeError("SMTP is blocked in this verifier")


socket.getaddrinfo = _blocked_getaddrinfo
socket.create_connection = _blocked_create_connection
urllib.request.urlopen = _blocked_urlopen
smtplib.SMTP = _BlockedSMTP
smtplib.SMTP_SSL = _BlockedSMTP

CHECKS = 0
FAILURES: list[str] = []

PRODUCTION_STATE_FILES = (
    ROOT / "data" / "editorial_daily_state.json",
    ROOT / "data" / "editorial_weekly_state.json",
    ROOT / "data" / "teams_push_state.json",
    ROOT / "data" / "news_censor_verified_state.json",
)

PRIMARY_NAMES = list(source_priority.locked_publisher_names("primary_10"))
# domain -> canonical name (primary only); pick one representative domain per name.
_PRIMARY_DOMAIN_MAP = source_priority.locked_publisher_domain_map(("primary_10",))
NAME_TO_DOMAIN: dict[str, str] = {}
for _domain, _name in _PRIMARY_DOMAIN_MAP.items():
    NAME_TO_DOMAIN.setdefault(_name, _domain)

PORTAL_LINK = "https://n.news.naver.com/mnews/article/001/0000000001"
GENERIC_DESC = "AI 데이터센터 전력 인프라 투자 계획이 공식 발표됐다. 세부 일정과 적용 범위가 공개됐다."


def check(name: str, ok: bool, detail: str = "") -> bool:
    global CHECKS
    CHECKS += 1
    if ok:
        print(f"PASS {name}")
    else:
        FAILURES.append(name)
        print(f"FAIL {name}" + (f" — {detail[:300]}" if detail else ""))
    return bool(ok)


def _item(title: str, originallink: str) -> dict:
    return {
        "title": title,
        "description": GENERIC_DESC,
        "pubDate": "Wed, 06 Aug 2026 09:00:00 +0900",
        "originallink": originallink,
        "link": PORTAL_LINK,
    }


def _direct_item(name: str, idx: int, topic: str = "AI") -> dict:
    domain = NAME_TO_DOMAIN[name]
    return _item(f"{name} {topic} 공식 계약 {idx}", f"https://www.{domain}/news/{name}-{idx}")


def _snapshot_state() -> dict:
    return {
        str(path): path.read_bytes()
        for path in PRODUCTION_STATE_FILES
        if path.exists()
    }


def _sources(lane, *, queries=("현대건설",), host_map=None,
            max_per_query=10, max_total=180) -> dict:
    cfg = {
        "queries": list(queries),
        "display": 10,
        "start": 1,
        "sort": "date",
        "max_per_query": max_per_query,
        "max_total": max_total,
        "host_source_map": dict(host_map or {}),
    }
    if lane is not None:
        cfg["primary_publisher_lane"] = lane
    return cfg


def run_fetch(cfg_dict: dict, router):
    """Run nn.fetch() with a monkeypatched request adapter that never hits the
    network. ``router(query_text) -> payload dict``."""
    calls: list[str] = []

    def fake_request(url, _headers, _timeout):
        calls.append(url)
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["query"][0]
        return router(query)

    original = nn._request_json  # noqa: SLF001
    nn._request_json = fake_request  # noqa: SLF001
    try:
        with tempfile.TemporaryDirectory(prefix="r4r16-fetch-") as td:
            src = Path(td) / "sources.json"
            src.write_text(json.dumps(cfg_dict, ensure_ascii=False), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                result = nn.fetch(timeout=8, sources_path=src, include_coverage=False)
    finally:
        nn._request_json = original  # noqa: SLF001
    return result, calls


def _publisher_name_of_query(query: str) -> str:
    head = query.split(" ", 1)[0]
    return head if head in PRIMARY_NAMES else ""


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #

def canonical_policy_checks() -> int:
    check(
        "canonical primary publishers number exactly 10",
        len(PRIMARY_NAMES) == 10 and len(set(PRIMARY_NAMES)) == 10,
        str(PRIMARY_NAMES),
    )
    both = source_priority.locked_publisher_domain_map(("primary_10", "secondary_3"))
    primary_only = source_priority.locked_publisher_domain_map(("primary_10",))
    check(
        "primary_10-only domain map covers all ten publishers",
        len(set(primary_only.values())) == 10,
        str(sorted(set(primary_only.values()))),
    )
    check(
        "primary+secondary canonical domain map is complete (13 publishers)",
        len(set(both.values())) == 13
        and len(set(source_priority.locked_publisher_names("secondary_3"))) == 3,
        str(sorted(set(both.values()))),
    )
    # Six previously-missing publishers must now normalize to a canonical domain.
    missing_before = {
        "MBC": ("imbc.com", "mbc.co.kr"),
        "KBS": ("kbs.co.kr",),
        "YTN": ("ytn.co.kr",),
        "JTBC": ("jtbc.co.kr",),
        "중앙일보": ("joongang.co.kr",),
        "SBS": ("sbs.co.kr",),
    }
    for name, domains in missing_before.items():
        mapped = {domain for domain, value in both.items() if value == name}
        check(
            f"{name} domain(s) normalized in canonical map",
            mapped == set(domains),
            f"{sorted(mapped)} != {sorted(domains)}",
        )
    # Fresh dict — callers cannot mutate the cached policy through the result.
    first = source_priority.locked_publisher_domain_map(("primary_10",))
    first["__mutated__"] = "x"
    second = source_priority.locked_publisher_domain_map(("primary_10",))
    check("locked_publisher_domain_map returns a fresh dict", "__mutated__" not in second)
    # Tiers outside primary_10/secondary_3 are rejected.
    rejected = False
    try:
        source_priority.locked_publisher_domain_map(("primary_10", "major"))
    except ValueError:
        rejected = True
    check("unsupported tier input is rejected", rejected)

    # Merged host-mapping coverage (manual + canonical), computed the way the
    # provider merges them: canonical entries win, manual specialist/official
    # entries are preserved.
    manual = json.loads(
        (ROOT / "data" / "naver_news_sources.json").read_text(encoding="utf-8")
    ).get("host_source_map") or {}
    merged = {**manual, **both}
    primary_set = set(PRIMARY_NAMES)
    manual_covered = {name for name in manual.values() if name in primary_set}
    merged_covered = {name for name in merged.values() if name in primary_set}
    check(
        "core-publisher host mapping rises from partial to full 10/10",
        len(manual_covered) < 10 and len(merged_covered) == 10,
        f"manual={len(manual_covered)} merged={len(merged_covered)}",
    )
    # Existing specialist/official manual mappings survive the merge.
    check(
        "existing specialist/official manual host mappings are preserved",
        merged.get("dnews.co.kr") == "대한경제"
        and merged.get("molit.go.kr") == "국토교통부"
        and merged.get("biz.chosun.com") == "조선비즈",
    )
    return len(merged_covered)


def clamp_and_spec_checks() -> None:
    check(
        "code-level ceilings are 30 / 2 / 40",
        nn.PRIMARY_PUBLISHER_MAX_QUERIES == 30
        and nn.PRIMARY_PUBLISHER_MAX_PER_QUERY == 2
        and nn.PRIMARY_PUBLISHER_MAX_TOTAL == 40,
    )
    specs_full = nn.primary_publisher_query_specs(
        ["AI", "AI 데이터센터", "AI 투자"], max_queries=999
    )
    check(
        "10 publishers × 3 topics yields exactly 30 query specs (query cap 30)",
        len(specs_full) == 30
        and all(spec["query"] == f"{spec['publisher']} {spec['topic']}" for spec in specs_full)
        and {spec["publisher"] for spec in specs_full} == set(PRIMARY_NAMES),
    )
    check(
        "a lower configured max_queries lowers the spec count",
        len(nn.primary_publisher_query_specs(["AI", "AI 데이터센터"], max_queries=5)) == 5,
    )
    check(
        "at most three topics are combined even if more are configured",
        {
            spec["topic"]
            for spec in nn.primary_publisher_query_specs(
                ["t1", "t2", "t3", "t4", "t5"], max_queries=999
            )
        }
        == {"t1", "t2", "t3"},
    )
    check(
        "each spec preserves expected publisher identity/rank and canonical domains",
        all(
            spec["rank"] == PRIMARY_NAMES.index(spec["publisher"]) + 1
            and spec["domains"]
            and all(dom in _PRIMARY_DOMAIN_MAP for dom in spec["domains"])
            for spec in specs_full
        ),
    )


def acceptance_unit_checks() -> bool:
    specs = nn.primary_publisher_query_specs(["AI"])
    yonhap = next(s for s in specs if s["publisher"] == "연합뉴스")
    mbc = next(s for s in specs if s["publisher"] == "MBC")

    yna_row = {"url": "https://www.yna.co.kr/view/AKR20260806", "source": "연합뉴스"}
    mbc_row = {"url": "https://imbc.com/news/2026080601", "source": "MBC"}
    thebell_row = {"url": "https://www.thebell.co.kr/free/content/a1", "source": "더벨"}
    neutral_row = {"url": "https://unknown-outlet.example/news/1", "source": "unknown-outlet.example"}
    portal_row = {"url": PORTAL_LINK, "source": "naver"}

    accept_target = check(
        "expected primary_10 target is accepted",
        nn._publisher_lane_accepts(yna_row, yonhap) is True,  # noqa: SLF001
    )
    reject_cross = check(
        "cross-publisher (another primary) is rejected on the wrong query",
        nn._publisher_lane_accepts(mbc_row, yonhap) is False  # noqa: SLF001
        and nn._publisher_lane_accepts(yna_row, mbc) is False,  # noqa: SLF001
    )
    reject_specialist = check(
        "specialist and neutral results are not accepted as primary_10",
        nn._publisher_lane_accepts(thebell_row, yonhap) is False  # noqa: SLF001
        and nn._publisher_lane_accepts(neutral_row, yonhap) is False,  # noqa: SLF001
    )
    reject_portal = check(
        "a portal-only (non publisher-direct) result is rejected",
        nn._publisher_lane_accepts(portal_row, yonhap) is False,  # noqa: SLF001
    )
    not_misclassified = check(
        "specialist/neutral do not resolve to the primary_10 tier",
        source_priority.publisher_delivery_tier("더벨", thebell_row["url"])["tier"] != "primary_10"
        and source_priority.publisher_delivery_tier("unknown-outlet.example", neutral_row["url"])["tier"] != "primary_10",
    )
    return bool(
        accept_target and reject_cross and reject_specialist and reject_portal
        and not_misclassified
    )


def budget_and_counter_checks() -> tuple[bool, bool]:
    # ---- accurate 6 counters (10 pubs × 1 topic, one direct row each) --------
    def one_each_router(query: str):
        name = _publisher_name_of_query(query)
        if name:
            return {"items": [_direct_item(name, 1)]}
        # general roster returns one secondary_3 (donga) direct row.
        return {"items": [_item("현대건설 일반 동향", "https://www.donga.com/news/2026080699")]}

    result, calls = run_fetch(
        _sources({"enabled": True, "topics": ["AI"], "max_queries": 30,
                  "max_per_query": 2, "max_total": 40}),
        one_each_router,
    )
    check(
        "audit counters are accurate (attempted/ok/collected/tiers/exhausted)",
        result["primary_publisher_queries_attempted"] == 10
        and result["primary_publisher_queries_ok"] == 10
        and result["primary_publisher_articles_collected"] == 10
        and result["primary_10_articles_collected"] == 10
        and result["secondary_3_articles_collected"] == 1
        and result["primary_publisher_lane_budget_exhausted"] is False,
        json.dumps({k: result[k] for k in (
            "primary_publisher_queries_attempted", "primary_publisher_queries_ok",
            "primary_publisher_articles_collected", "primary_10_articles_collected",
            "secondary_3_articles_collected", "primary_publisher_lane_budget_exhausted",
        )}),
    )
    check(
        "existing provider return fields are preserved",
        result["provider"] == nn.PROVIDER
        and result["status"] == nn.STATUS_ACTIVE
        and result["raw_count"] == len(result["articles"]) == 11
        and result["queries_attempted"] == 1
        and "credentials_present" in result,
    )

    # ---- per-query cap clamps to 2 even when config asks 99 -------------------
    def single_pub_five_router(query: str):
        # Only 연합뉴스 answers, with five distinct publisher-direct rows; every
        # other publisher query is empty. A single query offering five valid
        # rows must yield at most two accepted rows.
        if _publisher_name_of_query(query) == "연합뉴스":
            domain = NAME_TO_DOMAIN["연합뉴스"]
            return {"items": [
                _item(f"연합뉴스 AI 기사 {i}", f"https://www.{domain}/news/pq-{i}")
                for i in range(5)
            ]}
        return {"items": []}

    per_query, _pc = run_fetch(
        _sources({"enabled": True, "topics": ["AI"], "max_queries": 30,
                  "max_per_query": 99, "max_total": 40}),
        single_pub_five_router,
    )
    check(
        "per-query cap clamps to 2 even when config asks 99 and 5 rows are offered",
        per_query["primary_publisher_articles_collected"] == 2
        and per_query["primary_10_articles_collected"] == 2
        and per_query["primary_publisher_lane_budget_exhausted"] is False,
        f"collected={per_query['primary_publisher_articles_collected']}",
    )

    # ---- query-count cap 30 (10 publishers × 3 topics), total not force-reached
    def one_unique_router(query: str):
        name = _publisher_name_of_query(query)
        if not name:
            return {"items": []}
        domain = NAME_TO_DOMAIN[name]
        topic = query.split(" ", 1)[1] if " " in query else ""
        slug = topic.replace(" ", "-")
        return {"items": [_item(f"{name} {topic}", f"https://www.{domain}/news/{name}-{slug}")]}

    query_cap, _qc = run_fetch(
        _sources({"enabled": True, "topics": ["AI", "AI 데이터센터", "AI 투자"],
                  "max_queries": 999, "max_per_query": 2, "max_total": 999}),
        one_unique_router,
    )
    check(
        "query attempts are bounded to 30 with one unique row per query",
        query_cap["primary_publisher_queries_attempted"] == 30
        and query_cap["primary_publisher_articles_collected"] == 30
        and query_cap["primary_publisher_lane_budget_exhausted"] is False,
        f"attempted={query_cap['primary_publisher_queries_attempted']} "
        f"collected={query_cap['primary_publisher_articles_collected']}",
    )

    # ---- total cap clamps to 40 even when config asks 999 --------------------
    def three_unique_router(query: str):
        name = _publisher_name_of_query(query)
        if not name:
            return {"items": []}
        domain = NAME_TO_DOMAIN[name]
        topic = query.split(" ", 1)[1] if " " in query else ""
        slug = topic.replace(" ", "-")
        return {"items": [
            _item(f"{name} {topic} {i}", f"https://www.{domain}/news/{name}-{slug}-{i}")
            for i in range(3)
        ]}

    total_cap, _tc = run_fetch(
        _sources({"enabled": True, "topics": ["AI", "AI 데이터센터", "AI 투자"],
                  "max_queries": 999, "max_per_query": 2, "max_total": 999}),
        three_unique_router,
    )
    check(
        "total cap clamps to 40 even when config asks 999",
        total_cap["primary_publisher_articles_collected"] == 40
        and total_cap["primary_10_articles_collected"] == 40
        and total_cap["primary_publisher_lane_budget_exhausted"] is True,
        f"collected={total_cap['primary_publisher_articles_collected']}",
    )

    # ---- PUBLISHER budget exhausted ⇒ general lane still runs -----------------
    def pub_and_general_router(query: str):
        name = _publisher_name_of_query(query)
        if name:
            return {"items": [_direct_item(name, i) for i in range(2)]}
        return {"items": [_item("현대건설 일반", "https://www.example-biz.co.kr/g/1"),
                          _item("현대건설 추가", "https://www.example-biz.co.kr/g/2")]}

    pub_cap, _c = run_fetch(
        _sources(
            {"enabled": True, "topics": ["AI"], "max_queries": 30,
             "max_per_query": 2, "max_total": 2},
            queries=("현대건설",),
            host_map={"example-biz.co.kr": "예시경제"},
        ),
        pub_and_general_router,
    )
    general_rows_after_pub_exhaustion = (
        pub_cap["raw_count"] - pub_cap["primary_publisher_articles_collected"]
    )
    publisher_independent = check(
        "publisher budget exhausted ⇒ general lane still runs",
        pub_cap["primary_publisher_lane_budget_exhausted"] is True
        and pub_cap["primary_publisher_articles_collected"] == 2
        and general_rows_after_pub_exhaustion >= 1,
        f"general_rows={general_rows_after_pub_exhaustion}",
    )

    # ---- GENERAL budget exhausted ⇒ publisher lane still runs -----------------
    def general_capped_router(query: str):
        name = _publisher_name_of_query(query)
        if name:
            return {"items": [_direct_item(name, 1)]}
        return {"items": [_item(f"현대건설 {i}", f"https://www.example-biz.co.kr/c/{i}")
                          for i in range(3)]}

    gen_cap, _c2 = run_fetch(
        _sources(
            {"enabled": True, "topics": ["AI"], "max_queries": 30,
             "max_per_query": 2, "max_total": 40},
            queries=("현대건설",),
            host_map={"example-biz.co.kr": "예시경제"},
            max_total=1,
        ),
        general_capped_router,
    )
    general_contributed = gen_cap["raw_count"] - gen_cap["primary_publisher_articles_collected"]
    general_independent = check(
        "general budget exhausted (cap 1) ⇒ publisher lane still runs fully",
        gen_cap["primary_publisher_articles_collected"] == 10
        and general_contributed == 1,
        f"pub={gen_cap['primary_publisher_articles_collected']} general={general_contributed}",
    )
    return publisher_independent, general_independent


def dedup_determinism_checks() -> None:
    shared = "https://www.yna.co.kr/view/SHARED2026"

    def shared_url_router(query: str):
        name = _publisher_name_of_query(query)
        if name == "연합뉴스":
            return {"items": [_item("연합뉴스 공유 기사", shared)]}
        if name:
            return {"items": []}
        # general roster returns the SAME publisher URL (trailing-slash variant).
        return {"items": [_item("현대건설 동일 기사", shared + "/")]}

    cfg = _sources(
        {"enabled": True, "topics": ["AI"], "max_queries": 30,
         "max_per_query": 2, "max_total": 40},
        queries=("현대건설",),
    )
    first, _c = run_fetch(cfg, shared_url_router)
    urls = [row["url"] for row in first["articles"]]
    check(
        "the same canonical URL from both lanes dedups to one final row",
        sum(1 for url in urls if url.rstrip("/") == shared) == 1
        and first["raw_count"] == 1,
        str(urls),
    )
    second, _c2 = run_fetch(cfg, shared_url_router)
    check(
        "final URL dedup / ordering is deterministic across runs",
        [row["url"] for row in second["articles"]] == urls,
    )


def manifest_propagation_checks() -> None:
    # Static wiring: the manifest sources all six counters from collection_audit.
    console_src = (ROOT / "scripts" / "build_editorial_review_console.py").read_text(
        encoding="utf-8"
    )
    counters = (
        "primary_publisher_queries_attempted",
        "primary_publisher_queries_ok",
        "primary_publisher_articles_collected",
        "primary_10_articles_collected",
        "secondary_3_articles_collected",
        "primary_publisher_lane_budget_exhausted",
    )
    check(
        "Review console manifest reads all six counters from collection_audit",
        all(
            f'"{key}"' in console_src and f'collection_audit.get("{key}"' in console_src
            for key in counters
        ),
    )
    runner_src = (ROOT / "scripts" / "run_editorial_briefing.py").read_text(
        encoding="utf-8"
    )
    check(
        "run_editorial_briefing propagates all six counters into collection_audit",
        all(key in runner_src for key in counters),
    )
    # Functional: build the Review console offline (--fixture) into a temp root and
    # confirm the six fields land in BOTH the dated and latest manifests.
    with tempfile.TemporaryDirectory(prefix="r4r16-console-") as td:
        env = dict(os.environ)
        env.update(
            NEWS_MODE="mock",
            NAVER_NEWS_ENABLED="0",
            EDITORIAL_PRODUCTION="0",
            PYTHONPATH=str(ROOT),
        )
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "build_editorial_review_console.py"),
                "--fixture",
                "--run-at",
                "2026-08-07",
                "--output-root",
                td,
            ],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
        ok_build = completed.returncode == 0
        if not ok_build:
            check("Review console --fixture build succeeds offline", False,
                  completed.stderr.strip()[-300:])
            return
        dated = json.loads((Path(td) / "2026-08-07" / "manifest.json").read_text("utf-8"))
        latest = json.loads((Path(td) / "latest" / "manifest.json").read_text("utf-8"))
        check(
            "dated and latest Review manifests carry all six counter fields",
            all(key in dated for key in counters)
            and all(key in latest for key in counters),
        )
        check(
            "Review console build reports zero sends and zero production writes",
            dated.get("network_sends") == 0
            and dated.get("smtp_attempts") == 0
            and dated.get("teams_sends") == 0
            and dated.get("telegram_sends") == 0
            and dated.get("production_state_writes") == 0,
        )


def delivery_gate_unchanged_checks() -> bool:
    sjournal = "https://www.s-journal.co.kr/news/articleView.html?idxno=42865"
    ok = check(
        "delivery gates behave unchanged (primary immediate, S저널 never_automatic, long-tail not a lead)",
        source_priority.publisher_delivery_tier("연합뉴스", "https://www.yna.co.kr/view/x")["tier"] == "primary_10"
        and source_priority.publisher_delivery_tier("동아일보", "https://www.donga.com/news/x")["tier"] == "secondary_3"
        and source_priority.teams_delivery_source_policy("S저널", sjournal)["teams_lane"] == "never_automatic"
        and bool(brief.lead_source_eligible_tier("연합뉴스", ""))
        and not brief.lead_source_eligible_tier("비즈트리뷴", ""),
    )
    # The Teams sender and workflow files are outside this change's scope.
    check(
        "Teams sender and editorial workflows remain present (out of scope)",
        (ROOT / "scripts" / "send_teams_ai_push.py").is_file()
        and (ROOT / ".github" / "workflows" / "editorial-daily-brief.yml").is_file(),
    )
    return ok


def main() -> int:
    saved = (
        nn.config.NAVER_NEWS_ENABLED,
        nn.config.NAVER_CLIENT_ID,
        nn.config.NAVER_CLIENT_SECRET,
    )
    nn.config.NAVER_NEWS_ENABLED = True
    nn.config.NAVER_CLIENT_ID = "fixture-client-id"
    nn.config.NAVER_CLIENT_SECRET = "fixture-client-secret"
    state_before = _snapshot_state()

    try:
        primary_host_mapping_count = canonical_policy_checks()
        clamp_and_spec_checks()
        cross_publisher_rejected = acceptance_unit_checks()
        publisher_independent, general_independent = budget_and_counter_checks()
        dedup_determinism_checks()
        manifest_propagation_checks()
        delivery_gate_unchanged = delivery_gate_unchanged_checks()
    finally:
        (
            nn.config.NAVER_NEWS_ENABLED,
            nn.config.NAVER_CLIENT_ID,
            nn.config.NAVER_CLIENT_SECRET,
        ) = saved
        socket.getaddrinfo = _orig_getaddrinfo
        socket.create_connection = _orig_create_connection
        urllib.request.urlopen = _orig_urlopen

    state_after = _snapshot_state()
    state_ok = check(
        "production state files are byte-identical (0 writes)",
        state_after == state_before,
    )
    no_network = check("no external network call occurred", EXTERNAL["count"] == 0)
    no_smtp = check("no SMTP attempt occurred", SMTP["count"] == 0)

    print(f"checks={CHECKS} failures={len(FAILURES)}")
    if FAILURES:
        for name in FAILURES:
            print(f"FAILED: {name}")

    ok = not FAILURES and state_ok and no_network and no_smtp

    print(f"LOCKED_PRIMARY_PUBLISHERS={len(PRIMARY_NAMES)}")
    print(f"PRIMARY_PUBLISHER_QUERY_LIMIT={nn.PRIMARY_PUBLISHER_MAX_QUERIES}")
    print(f"PRIMARY_PUBLISHER_MAX_PER_QUERY={nn.PRIMARY_PUBLISHER_MAX_PER_QUERY}")
    print(f"PRIMARY_PUBLISHER_MAX_TOTAL={nn.PRIMARY_PUBLISHER_MAX_TOTAL}")
    print(f"PRIMARY_HOST_MAPPING_COUNT={primary_host_mapping_count}")
    print(f"GENERAL_BUDGET_INDEPENDENT={'PASS' if general_independent else 'FAIL'}")
    print(f"PUBLISHER_BUDGET_INDEPENDENT={'PASS' if publisher_independent else 'FAIL'}")
    print(f"CROSS_PUBLISHER_REJECTION={'PASS' if cross_publisher_rejected else 'FAIL'}")
    print(f"DELIVERY_GATE_UNCHANGED={'PASS' if delivery_gate_unchanged else 'FAIL'}")
    print(f"EXTERNAL_NETWORK_CALLS={EXTERNAL['count']}")
    print(f"SMTP_ATTEMPTS={SMTP['count']}")
    print("TEAMS_SENDS=0")
    print("TELEGRAM_SENDS=0")
    print(f"PRODUCTION_STATE_WRITES={0 if state_ok else 1}")
    print(
        "PRIMARY_PUBLISHER_DISCOVERY_LANE_VERIFIER="
        + ("PASS" if ok else "FAIL")
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
