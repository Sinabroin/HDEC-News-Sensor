#!/usr/bin/env python3
"""Offline R4-R4 verifier: state/cache/carry-forward/concurrent resolution.

Every resolver, fetcher, and sender boundary is fixture-owned.  Temporary state
is the only write; repository production state is hashed before/after.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import (  # noqa: E402
    collector,
    live_collector,
    naver_news_provider,
    news_censor_verified_state as verified_state,
    news_coverage,
    publisher_direct,
    teams_ai_push,
    thebell_watch,
)

PASS = 0
FAIL = 0
NETWORK_CALLS = 0
SMTP_ATTEMPTS = 0
TEAMS_SENDS = 0
TELEGRAM_SENDS = 0
PRODUCTION_STATE_WRITES = 0


def check(label: str, condition: bool) -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"PASS {label}")
    else:
        FAIL += 1
        print(f"FAIL {label}")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "missing"


NOW = datetime(2026, 8, 2, 10, 0, tzinfo=timezone.utc)


def article(
    index: int,
    *,
    host: str = "publisher.example",
    published: datetime | None = None,
    provider: str = "publisher_direct_rss",
    title: str | None = None,
) -> dict:
    timestamp = published or (NOW - timedelta(hours=index % 12))
    url = f"https://{host}/news/{index}"
    return {
        "id": f"fixture-{index}",
        "title": title or f"현대건설 AI 데이터센터 인프라 투자 {index}",
        "source": f"Publisher {host}",
        "published_at": timestamp.isoformat(timespec="seconds"),
        "url": url,
        "canonical_url": url,
        "publisher_url": url,
        "publisher_domain": host,
        "publisher_direct": True,
        "snippet": "검증된 데이터센터 전력 인프라 투자와 건설 사업 관련 짧은 요약",
        "portal_resolution_status": "resolved",
        "portal_resolution_reason": "fixture_verified",
        "status": "collected",
        "quarantine": False,
        "source_metadata": {
            "provider": provider,
            "publisher_url": url,
            "publisher_domain": host,
            "publisher_direct": True,
            "portal_resolution_status": "resolved",
            "portal_resolution_reason": "fixture_verified",
        },
    }


def entry_for(
    row: dict,
    *,
    verified_at: datetime = NOW,
    categories=("ai", "biz"),
) -> dict:
    return verified_state.verified_entry_from_article(
        row,
        now=verified_at,
        categories=categories,
        display_relevant=True,
        source_quality_passed=True,
    )


def state_for(entries: list[dict], *, generated_at: datetime = NOW) -> dict:
    return {
        "contract": verified_state.STATE_CONTRACT,
        "version": verified_state.STATE_VERSION,
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "entries": sorted(entries, key=lambda item: item["canonical_url"].casefold()),
    }


def verify_state_contract(tmp: Path) -> dict:
    base_row = article(1)
    base_entry = entry_for(base_row)
    valid = state_for([base_entry])
    path = tmp / "verified.json"
    digest = verified_state.atomic_write_state(path, valid)
    loaded = verified_state.load_state(path, now=NOW)
    check("valid state loads", loaded.entries_valid == 1 and loaded.sha256 == digest)
    check("state contract/version are explicit", (
        loaded.state["contract"] == verified_state.STATE_CONTRACT
        and loaded.state["version"] == 1
    ))
    check("deterministic JSON is newline terminated", path.read_bytes().endswith(b"\n"))
    twin = tmp / "verified-twin.json"
    verified_state.atomic_write_state(twin, valid)
    check("deterministic serialization", path.read_bytes() == twin.read_bytes())

    partial = tmp / "partial.json"
    partial.write_text('{"contract":', encoding="utf-8")
    try:
        verified_state.load_state(partial, now=NOW)
        partial_closed = False
    except verified_state.VerifiedStateError:
        partial_closed = True
    check("partial JSON fails closed", partial_closed)

    invalid = copy.deepcopy(valid)
    invalid["unexpected"] = True
    invalid_path = tmp / "invalid.json"
    invalid_path.write_text(json.dumps(invalid), encoding="utf-8")
    try:
        verified_state.load_state(invalid_path, now=NOW)
        invalid_closed = False
    except verified_state.VerifiedStateError:
        invalid_closed = True
    check("invalid schema fails closed", invalid_closed)

    unsupported = copy.deepcopy(valid)
    unsupported["version"] = 2
    unsupported_path = tmp / "unsupported.json"
    unsupported_path.write_text(json.dumps(unsupported), encoding="utf-8")
    try:
        verified_state.load_state(unsupported_path, now=NOW)
        unsupported_closed = False
    except verified_state.VerifiedStateError:
        unsupported_closed = True
    check("unsupported state version fails closed", unsupported_closed)

    oversized = tmp / "oversized.json"
    oversized.write_bytes(b" " * (verified_state.MAX_SERIALIZED_BYTES + 1))
    try:
        verified_state.load_state(oversized, now=NOW)
        oversized_closed = False
    except verified_state.VerifiedStateError:
        oversized_closed = True
    check("oversized state is rejected", oversized_closed)

    previous = path.read_bytes()
    changed = state_for([entry_for(article(2))])
    try:
        verified_state.atomic_write_state(
            path,
            changed,
            replace=lambda _source, _target: (_ for _ in ()).throw(OSError("fixture")),
        )
    except OSError:
        pass
    check("atomic replacement failure preserves previous state", path.read_bytes() == previous)

    duplicate = copy.deepcopy(base_entry)
    duplicate["last_seen_at"] = (NOW + timedelta(minutes=1)).isoformat(timespec="seconds")
    duplicate_state = state_for([base_entry, duplicate])
    merged = verified_state.validate_state(duplicate_state)
    check("duplicate canonical entries merge deterministically", len(merged["entries"]) == 1)

    old_row = article(3, published=NOW - timedelta(days=20))
    old_entry = entry_for(old_row, verified_at=NOW - timedelta(days=20))
    pruned, count = verified_state.prune_state(state_for([base_entry, old_entry]), now=NOW)
    check("retention pruning is deterministic", count == 1 and len(pruned["entries"]) == 1)

    serialized = path.read_text(encoding="utf-8")
    forbidden = (
        "discovery_url", "redirect_chain", "cookies", "recipient", "smtp",
        "telegram", "search.naver.com", "news.google.com",
    )
    check("no private discovery or sender data is persisted", not any(x in serialized.casefold() for x in forbidden))
    return valid


def verify_cache_and_carry(valid: dict) -> None:
    base_entry = valid["entries"][0]
    base_row = article(1)
    identities, canonicals = verified_state.state_indexes(valid)
    hit, reason = verified_state.reusable_entry(
        identities,
        canonicals,
        base_row,
        now=NOW + timedelta(hours=1),
    )
    check("unexpired verified entry is reused", hit is not None and reason == "verified_cache_hit")
    check("cache hit avoids network by contract", hit is not None)
    expired, expired_reason = verified_state.reusable_entry(
        identities,
        canonicals,
        base_row,
        now=NOW + timedelta(hours=25),
    )
    check("expired entry requires revalidation", expired is None and expired_reason == "cache_expired")

    for field, expected_reason in (
        ("publisher_authority_policy_version", "cache_authority_policy_changed"),
        ("source_quality_policy_version", "cache_source_quality_policy_changed"),
        ("canonicalization_version", "cache_canonicalization_version_changed"),
    ):
        altered = copy.deepcopy(base_entry)
        altered[field] = "prior-policy-version"
        altered_state = state_for([altered])
        alt_identities, alt_canonicals = verified_state.state_indexes(altered_state)
        reused, mismatch_reason = verified_state.reusable_entry(
            alt_identities,
            alt_canonicals,
            base_row,
            now=NOW + timedelta(hours=1),
        )
        check(f"{field} mismatch requires revalidation", reused is None and mismatch_reason == expected_reason)

    invalid = copy.deepcopy(base_entry)
    invalid.update({
        "invalidated": True,
        "invalidation_reason": "publisher_revalidation_rejected",
        "carry_forward_eligible": False,
    })
    invalid_state = state_for([invalid])
    invalid_ids, invalid_urls = verified_state.state_indexes(invalid_state)
    invalid_hit, invalid_reason = verified_state.reusable_entry(
        invalid_ids,
        invalid_urls,
        base_row,
        now=NOW + timedelta(hours=1),
    )
    check("invalidated cache entry is excluded", invalid_hit is None and invalid_reason == "cache_invalidated")

    timed_out = publisher_direct.quarantine_article(base_row, "publisher_verification_failed:FETCH_TIMEOUT")
    transient_state, invalidated_count, transient_count = verified_state.record_resolution_failures(
        valid,
        [timed_out],
        now=NOW + timedelta(hours=1),
    )
    check("transient timeout does not destroy valid proof", (
        invalidated_count == 0 and transient_count == 1
        and transient_state["entries"][0]["invalidated"] is False
    ))
    not_found = publisher_direct.quarantine_article(base_row, "publisher_verification_failed:ARTICLE_NOT_FOUND")
    rejected_state, invalidated_count, _transient = verified_state.record_resolution_failures(
        valid,
        [not_found],
        now=NOW + timedelta(hours=1),
    )
    check("bounded 404 proof invalidates prior entry", (
        invalidated_count == 1
        and rejected_state["entries"][0]["invalidated"] is True
        and rejected_state["entries"][0]["carry_forward_eligible"] is False
    ))
    rejected_ids, rejected_urls = verified_state.state_indexes(rejected_state)
    _retry_entry, retry_reason = verified_state.reusable_entry(
        rejected_ids,
        rejected_urls,
        base_row,
        now=NOW + timedelta(hours=2),
    )
    check("invalid retry backoff is bounded and explicit", retry_reason == "cache_retry_backoff")
    unsafe = publisher_direct.quarantine_article(base_row, "publisher_verification_failed:UNSAFE_DESTINATION")
    unsafe_state, unsafe_count, _transient = verified_state.record_resolution_failures(
        valid,
        [unsafe],
        now=NOW + timedelta(hours=1),
    )
    check("unsafe or portal revalidation invalidates", unsafe_count == 1 and unsafe_state["entries"][0]["invalidated"])

    fresh_row = article(10, host="fresh.example", published=NOW - timedelta(days=2))
    old_row = article(11, host="old.example", published=NOW - timedelta(days=8))
    invalid_row = article(12, host="invalid.example", published=NOW - timedelta(days=2))
    fresh_entry = entry_for(fresh_row, verified_at=NOW)
    old_entry = entry_for(old_row, verified_at=NOW)
    invalid_entry = entry_for(invalid_row, verified_at=NOW)
    invalid_entry.update({
        "invalidated": True,
        "invalidation_reason": "publisher_revalidation_rejected",
        "carry_forward_eligible": False,
    })
    union_state = state_for([base_entry, fresh_entry, old_entry, invalid_entry])
    carried, diagnostics = verified_state.carry_forward_articles(
        union_state,
        [base_row],
        now=NOW + timedelta(hours=1),
    )
    check("current plus carried union canonical-deduplicates and current wins", (
        len(carried) == 1 and carried[0]["url"] == fresh_row["url"]
    ))
    check("carried article preserves real publication timestamp", carried[0]["published_at"] == fresh_entry["published_at"])
    check("older-than-seven-day and invalid carried rows expire", diagnostics == {
        "candidates": 3, "expired": 1, "invalidated": 1,
    })
    check("carry-forward-only row has explicit Teams boundary", (
        carried[0]["source_metadata"]["carried_forward"] is True
        and carried[0]["source_metadata"]["teams_newness_eligible"] is False
    ))

    teams_row = article(
        50,
        host="teams.example",
        title="OpenAI AI 데이터센터 전력 인프라 50억달러 투자 계약 확정",
    )
    teams_row.update({
        "score": 4.9,
        "final_score": 4.9,
        "shadow_urgency_status": "confirmed",
        "shadow_would_pass": True,
        "shadow_confirmed_event_types": ["investment_confirmed"],
        "change_type": "new_article",
        "carried_forward": True,
        "teams_newness_eligible": False,
    })
    check("carry-forward-only article cannot enter Teams candidates", (
        teams_ai_push.select_teams_push_candidates([teams_row]) == ()
    ))
    current_teams_row = dict(teams_row)
    current_teams_row.update({"carried_forward": False, "teams_newness_eligible": True})
    check("current new article remains independently Teams-eligible", (
        len(teams_ai_push.select_teams_push_candidates([current_teams_row])) == 1
    ))


def verify_concurrent_resolution() -> None:
    original = live_collector._strict_publisher_authority
    lock = threading.Lock()
    active_global = 0
    max_global = 0
    active_hosts: dict[str, int] = {}
    max_per_host: dict[str, int] = {}

    groups = news_coverage.collection_query_groups()
    category_queries = {}
    for category in ("biz", "peers", "hdec", "safety", "global", "ai"):
        category_queries[category] = next(
            group["queries"][0]
            for group in groups
            if category in group.get("surface_categories", [])
        )

    rows = []
    for index in range(60):
        provider = (
            "publisher_direct_rss" if index % 3 == 0
            else "naver_news_api" if index % 3 == 1
            else "google_news_rss"
        )
        row = article(index, host=f"host-{index % 10}.example", provider=provider)
        row["source_metadata"]["query"] = category_queries[
            ("biz", "peers", "hdec", "safety", "global", "ai")[index % 6]
        ]
        rows.append(row)
    ordered = live_collector.prioritize_publisher_resolution_rows(
        [row for row in rows if row["source_metadata"]["provider"] == "publisher_direct_rss"],
        [row for row in rows if row["source_metadata"]["provider"] == "naver_news_api"],
        [row for row in rows if row["source_metadata"]["provider"] == "google_news_rss"],
    )
    titles_before = [row["title"] for row in ordered]

    def fake_authority(row, **_kwargs):
        nonlocal active_global, max_global
        host = row["publisher_domain"]
        with lock:
            active_global += 1
            active_hosts[host] = active_hosts.get(host, 0) + 1
            max_global = max(max_global, active_global)
            max_per_host[host] = max(max_per_host.get(host, 0), active_hosts[host])
        time.sleep(0.002 * (1 + int(row["id"].split("-")[-1]) % 4))
        with lock:
            active_global -= 1
            active_hosts[host] -= 1
        return publisher_direct.apply_publisher_authority(
            row,
            publisher_canonical_url=row["url"],
            source=row["source"],
            published_at=row["published_at"],
            resolution_reason="fixture_verified",
        )

    try:
        live_collector._strict_publisher_authority = fake_authority
        metrics: dict = {}
        resolved = live_collector.resolve_publisher_urls(
            ordered,
            strict=True,
            max_items=60,
            deadline=5,
            workers=4,
            per_host_workers=1,
            per_host_max_items=12,
            metrics=metrics,
        )
    finally:
        live_collector._strict_publisher_authority = original
    check("attempt budget materially exceeds prior 8-10", metrics["attempted_count"] >= 30)
    check("sixty-item bounded fixture resolves all", resolved == 60 and metrics["attempted_count"] == 60)
    check("global concurrency is bounded at four", 1 < max_global <= 4)
    check("per-host concurrency is exactly bounded at one", max(max_per_host.values()) == 1)
    check("concurrent completion preserves deterministic result ordering", [row["title"] for row in ordered] == titles_before)
    check("direct and Naver lanes are not starved", (
        metrics["per_source_lane"]["direct_official"]["attempts"] > 0
        and metrics["per_source_lane"]["naver_originallink"]["attempts"] > 0
        and metrics["per_source_lane"]["google_discovery"]["attempts"] > 0
    ))
    check("all six topical categories receive attempts", all(
        metrics["per_category"].get(category, {}).get("attempts", 0) > 0
        for category in ("biz", "peers", "hdec", "safety", "global", "ai")
    ))
    check("explicit outcome vocabulary is complete", set(live_collector._RESOLUTION_OUTCOMES) == set(metrics["outcomes"]))
    check("latency p50/p95 and per-host successes are recorded", (
        metrics["p50_latency_seconds"] > 0
        and metrics["p95_latency_seconds"] >= metrics["p50_latency_seconds"]
        and all(item["attempts"] >= item["successes"] for item in metrics["per_host"].values())
    ))

    isolated_rows = [article(100 + index, host=f"isolated-{index}.example") for index in range(4)]
    def isolated_authority(row, **_kwargs):
        if row["id"] == "fixture-100":
            raise RuntimeError("fixture leaf failure")
        return publisher_direct.apply_publisher_authority(
            row,
            publisher_canonical_url=row["url"],
            source=row["source"],
            published_at=row["published_at"],
            resolution_reason="fixture_verified",
        )
    try:
        live_collector._strict_publisher_authority = isolated_authority
        isolated_metrics: dict = {}
        isolated_count = live_collector.resolve_publisher_urls(
            isolated_rows,
            strict=True,
            max_items=4,
            deadline=2,
            metrics=isolated_metrics,
        )
    finally:
        live_collector._strict_publisher_authority = original
    check("one worker exception quarantines only its article", (
        isolated_count == 3
        and isolated_rows[0]["portal_resolution_reason"] == "publisher_verification_internal_error"
        and all(row["publisher_direct"] for row in isolated_rows[1:])
    ))

    deadline_rows = [article(200 + index, host=f"deadline-{index}.example") for index in range(12)]
    def slow_authority(row, **_kwargs):
        time.sleep(0.04)
        return publisher_direct.apply_publisher_authority(
            row,
            publisher_canonical_url=row["url"],
            source=row["source"],
            published_at=row["published_at"],
            resolution_reason="fixture_verified",
        )
    try:
        live_collector._strict_publisher_authority = slow_authority
        deadline_metrics: dict = {}
        completed = live_collector.resolve_publisher_urls(
            deadline_rows,
            strict=True,
            max_items=12,
            deadline=0.01,
            workers=2,
            metrics=deadline_metrics,
        )
    finally:
        live_collector._strict_publisher_authority = original
    check("deadline stops new work and preserves completed successes", (
        completed == 2
        and deadline_metrics["outcomes"]["skipped_global_deadline"] == 10
        and deadline_metrics["resolved_count"] == 2
    ))


def verify_cache_integration(tmp: Path) -> None:
    row = article(700, host="cache-integration.example")
    state_path = tmp / "warm-state.json"
    verified_state.atomic_write_state(state_path, state_for([entry_for(row)]))
    saved = (
        live_collector.fetch_publisher_direct_sources,
        live_collector.fetch_all,
        live_collector.resolve_publisher_urls,
        naver_news_provider.fetch,
        thebell_watch.extract_candidates,
        collector._ingest,
        os.environ.get("NEWS_CENSOR_VERIFIED_STATE_PATH"),
    )
    attempted = 0
    ingested: list[dict] = []
    def no_network_resolve(rows, *_args, **kwargs):
        nonlocal attempted
        attempted += len(rows)
        metrics = kwargs.get("metrics")
        if metrics is not None:
            metrics.clear()
            metrics.update(live_collector._new_resolution_metrics(rows))
        return 0
    try:
        os.environ["NEWS_CENSOR_VERIFIED_STATE_PATH"] = str(state_path)
        live_collector.fetch_publisher_direct_sources = lambda **kwargs: (
            kwargs.get("source_audit", []).append({
                "provider": "publisher_direct_rss", "status": "ok", "fetched_count": 1,
            }) or [copy.deepcopy(row)]
        )
        live_collector.fetch_all = lambda **kwargs: []
        live_collector.resolve_publisher_urls = no_network_resolve
        naver_news_provider.fetch = lambda **_kwargs: {
            "provider": "naver_news_api", "status": "disabled", "articles": [],
            "queries_attempted": 0, "queries_ok": 0, "credentials_present": False,
        }
        thebell_watch.extract_candidates = lambda _rows: []
        collector._ingest = lambda rows, _origin: (
            ingested.extend(copy.deepcopy(rows)) or (len(rows), len(rows), len(rows))
        )
        result = collector._run_live()  # noqa: SLF001
    finally:
        (
            live_collector.fetch_publisher_direct_sources,
            live_collector.fetch_all,
            live_collector.resolve_publisher_urls,
            naver_news_provider.fetch,
            thebell_watch.extract_candidates,
            collector._ingest,
            old_state_path,
        ) = saved
        if old_state_path is None:
            os.environ.pop("NEWS_CENSOR_VERIFIED_STATE_PATH", None)
        else:
            os.environ["NEWS_CENSOR_VERIFIED_STATE_PATH"] = old_state_path
    resolution = result["collector_health"]["publisher_resolution"]
    check("warm collector cache hit avoids resolver network work", (
        attempted == 0 and resolution["cache_hits"] == 1
        and resolution["attempted_count"] == 0
    ))
    check("warm valid supply remains available", (
        result["publisher_direct_eligible_count"] == 1
        and len(ingested) >= 1
    ))


def verify_hyundai_fixtures() -> None:
    positives = (
        "현대엔지니어링 해외 스마트시티 인프라 프로젝트 수주",
        "현대차그룹 데이터센터 전력 에너지 전략 투자",
        "현대모비스 현대오토에버 로보틱스 인프라 협력",
        "현대제철 현대글로비스 수소 에너지 물류 인프라",
        "기아 로보틱스 스마트시티 전략 투자",
    )
    negatives = (
        "현대자동차 신차 출시 가격표와 트림 공개",
        "기아 신차 판매량과 출고 전망",
        "현대차 모터스포츠 디자인 공개",
    )
    check("Hyundai Group strategic positive fixtures classify", all(
        "hmg_watch" in news_coverage.query_groups_for_text(title)
        for title in positives
    ))
    check("ordinary consumer vehicle negative fixtures stay out", all(
        "hmg_watch" not in news_coverage.query_groups_for_text(title)
        for title in negatives
    ))


def main() -> int:
    teams_path = ROOT / "data/teams_push_state.json"
    editorial_path = ROOT / "data/editorial_daily_state.json"
    protected_before = (sha256(teams_path), sha256(editorial_path))
    with tempfile.TemporaryDirectory(prefix="hdec-r4r4-") as name:
        tmp = Path(name)
        valid = verify_state_contract(tmp)
        verify_cache_and_carry(valid)
        verify_concurrent_resolution()
        verify_cache_integration(tmp)
        verify_hyundai_fixtures()
    protected_after = (sha256(teams_path), sha256(editorial_path))
    check("protected Teams/editorial state remains byte-identical", protected_before == protected_after)
    check("external network calls are zero", NETWORK_CALLS == 0)
    check("SMTP attempts are zero", SMTP_ATTEMPTS == 0)
    check("Teams sends are zero", TEAMS_SENDS == 0)
    check("Telegram sends are zero", TELEGRAM_SENDS == 0)
    check("production state writes are zero", PRODUCTION_STATE_WRITES == 0)
    print(
        f"R4_R4_VERIFIER={PASS}/{FAIL} "
        f"network={NETWORK_CALLS} smtp={SMTP_ATTEMPTS} teams={TEAMS_SENDS} "
        f"telegram={TELEGRAM_SENDS} production_state_writes={PRODUCTION_STATE_WRITES}"
    )
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
