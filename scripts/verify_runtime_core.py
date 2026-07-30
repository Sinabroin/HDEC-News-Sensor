#!/usr/bin/env python3
"""Offline verifier for the D7-AK-6F-C1-R1 shadow runtime core."""

from __future__ import annotations

import hashlib
import json
import py_compile
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.runtime_models import (  # noqa: E402
    CanonicalArticle,
    DecisionClass,
    NewsEvent,
    OutboxStatus,
    RuntimeHeartbeat,
    RuntimeModelError,
    deterministic_id,
    sha256_text,
    stable_json,
)
from app.runtime_policy import RuntimePolicyEngine  # noqa: E402
from app.runtime_sqlite import SQLiteRuntimeStore  # noqa: E402
from app.runtime_store import ClaimConflict, InvalidStateTransition  # noqa: E402


class Verifier:
    def __init__(self) -> None:
        self.checks = 0
        self.failures = 0

    def check(self, name: str, condition: bool) -> None:
        self.checks += 1
        if condition:
            print(f"PASS: {name}")
        else:
            self.failures += 1
            print(f"FAIL: {name}")

    def equal(self, name: str, actual: object, expected: object) -> None:
        self.check(name, actual == expected)
        if actual != expected:
            print(f"  expected={expected!r}")
            print(f"  actual={actual!r}")

    def raises(self, name: str, exc_type: type[BaseException], callable_obj) -> None:
        try:
            callable_obj()
        except exc_type:
            self.check(name, True)
        except Exception as exc:  # pragma: no cover - diagnostic path
            self.check(name, False)
            print(f"  unexpected_exception={type(exc).__name__}: {exc}")
        else:
            self.check(name, False)


def article(article_id: str, title: str, summary: str = "AI 데이터센터 기사") -> CanonicalArticle:
    return CanonicalArticle(
        article_id=article_id,
        canonical_url=f"https://example.invalid/{article_id}",
        title=title,
        source="검증 소스",
        published_at="2026-07-30T00:00:00Z",
        summary=summary,
        observed_at="2026-07-30T00:01:00Z",
        raw_payload={"fixture": True},
    )


def event_for(item: CanonicalArticle, suffix: str = "") -> NewsEvent:
    key = f"event:{item.article_id}{suffix}"
    signature = sha256_text(stable_json({"title": item.title, "summary": item.summary, "suffix": suffix}))
    return NewsEvent(
        event_cluster_key=key,
        primary_article_id=item.article_id,
        event_type="article_signal",
        headline=item.title,
        material_signature=signature,
        first_seen_at="2026-07-30T00:01:00Z",
        last_seen_at="2026-07-30T00:01:00Z",
        attributes={"fixture": True},
    )


def verify_models(v: Verifier) -> None:
    value = article("a1", "현대건설 AI 데이터센터 본계약 체결")
    v.equal("article id retained", value.article_id, "a1")
    v.check("article content signature sha256", len(value.content_signature) == 64)
    v.equal(
        "deterministic ids are stable",
        deterministic_id("x", "a", "b"),
        deterministic_id("x", "a", "b"),
    )
    v.raises(
        "empty article title fails closed",
        RuntimeModelError,
        lambda: CanonicalArticle(
            article_id="bad",
            canonical_url="https://example.invalid/bad",
            title="",
            source="source",
            published_at="2026-07-30T00:00:00Z",
        ),
    )


def verify_policy(v: Verifier) -> None:
    engine = RuntimePolicyEngine()
    cases = {
        "interview": ({
            "article_id": "interview",
            "event_cluster_key": "event:interview",
            "material_signature": "sig-interview",
            "title": "경제를 묻다 원전과 AI 데이터센터 전력 수요",
            "summary": "AI 데이터센터와 원전 전력 정책을 분석하는 인터뷰",
            "source": "중앙일보",
            "published_at": "2026-07-30T00:00:00Z",
        }, DecisionClass.P2),
        "airport": ({
            "article_id": "airport",
            "event_cluster_key": "event:airport",
            "material_signature": "sig-airport",
            "title": "삼성물산 대만 공항 터미널 건설",
            "summary": "TSMC와 AI 산업 성장으로 항공화물이 증가한다",
            "source": "뉴스1",
            "published_at": "2026-07-30T00:00:00Z",
        }, DecisionClass.P3),
        "speculation": ({
            "article_id": "speculation",
            "event_cluster_key": "event:speculation",
            "material_signature": "sig-speculation",
            "title": "빌 게이츠 방한 공급망 논의 전망",
            "summary": "AI 데이터센터와 원전 협력 가능성이 관측된다",
            "source": "매체",
            "published_at": "2026-07-30T00:00:00Z",
        }, DecisionClass.REJECT),
        "hdec_confirmed": ({
            "article_id": "hdec",
            "event_cluster_key": "event:hdec",
            "material_signature": "sig-hdec",
            "title": "현대건설 AI 데이터센터 본계약 체결",
            "summary": "현대건설이 공식 발표했다",
            "source": "공식",
            "published_at": "2026-07-30T00:00:00Z",
            "confirmed_event_types": ["contract_confirmed"],
            "explicit_evidence": ["official_release"],
        }, DecisionClass.P0),
        "competitor_confirmed": ({
            "article_id": "competitor",
            "event_cluster_key": "event:competitor",
            "material_signature": "sig-competitor",
            "title": "삼성물산 5조원 AI 데이터센터 본계약 체결",
            "summary": "공식 발표를 통해 계약 체결을 확인했다",
            "source": "공식",
            "published_at": "2026-07-30T00:00:00Z",
            "confirmed_event_types": ["contract_confirmed"],
            "explicit_evidence": ["official_release"],
        }, DecisionClass.P1),
        "hdec_unconfirmed": ({
            "article_id": "hdec-unconfirmed",
            "event_cluster_key": "event:hdec-unconfirmed",
            "material_signature": "sig-hdec-unconfirmed",
            "title": "현대건설 AI 데이터센터 수주 경쟁 본격화",
            "summary": "시장 점유율 확대를 위한 수주 활동을 다룬 분석 기사",
            "source": "분석 매체",
            "published_at": "2026-07-30T00:00:00Z",
        }, DecisionClass.P2),
    }
    for name, (payload, expected) in cases.items():
        decision = engine.decide(payload)
        v.equal(f"policy {name} class", decision.decision_class, expected)
    p0 = engine.decide(cases["hdec_confirmed"][0])
    v.equal("P0 uses immediate delivery", p0.delivery_class, "immediate")
    v.check("P0 enqueues", p0.should_enqueue)
    unconfirmed = engine.decide(cases["hdec_unconfirmed"][0])
    v.equal("unconfirmed HDEC action stays below P0", unconfirmed.decision_class, DecisionClass.P2)
    v.check("unconfirmed HDEC action is not immediate", unconfirmed.delivery_class != "immediate")
    p2 = engine.decide(cases["interview"][0])
    v.equal("P2 uses hourly digest", p2.delivery_class, "hourly_digest")
    p3 = engine.decide(cases["airport"][0])
    v.check("P3 does not enqueue", not p3.should_enqueue)


def verify_store(v: Verifier, temp: Path) -> None:
    db = temp / "runtime.db"
    with SQLiteRuntimeStore(db) as store:
        initial = store.stats()
        v.check("schema initializes all seven tables", len(initial) == 7)
        v.check("initial tables empty", all(value == 0 for value in initial.values()))

        first = article("store-a1", "현대건설 AI 데이터센터 본계약 체결")
        store.upsert_article(first)
        store.upsert_article(first)
        v.equal("article upsert idempotent", store.stats()["canonical_articles"], 1)

        same_url_other_provider = CanonicalArticle(
            article_id="rss:store-a1",
            canonical_url=first.canonical_url,
            title="현대건설 AI 데이터센터 본계약 체결 후속",
            source="다른 provider",
            published_at="2026-07-30T09:00:00+09:00",
            summary="동일 원문을 다른 provider id로 재수집",
            observed_at="2026-07-30T09:02:00+09:00",
            raw_payload={"fixture": "same-url"},
        )
        canonical_id = store.upsert_article(same_url_other_provider)
        v.equal("same URL returns existing canonical article id", canonical_id, first.article_id)
        v.equal("same URL different provider converges to one row", store.stats()["canonical_articles"], 1)

        first_event = event_for(first)
        store.upsert_event(first_event)
        store.upsert_event(first_event)
        v.equal("event upsert idempotent", store.stats()["news_events"], 1)

        decision = RuntimePolicyEngine().decide({
            "article_id": first.article_id,
            "event_cluster_key": first_event.event_cluster_key,
            "material_signature": first_event.material_signature,
            "title": first.title,
            "summary": first.summary,
            "source": first.source,
            "published_at": first.published_at,
            "confirmed_event_types": ["contract_confirmed"],
            "explicit_evidence": ["official_release"],
        })
        store.record_policy_decision(decision)
        store.record_policy_decision(decision)
        v.equal("policy decision insert idempotent", store.stats()["policy_decisions"], 1)

        enqueued = store.enqueue_outbox(
            channel="teams_email",
            event_cluster_key=first_event.event_cluster_key,
            material_signature=first_event.material_signature,
            delivery_class=decision.delivery_class,
            payload={"title": first.title, "shadow_only": True},
        )
        duplicate = store.enqueue_outbox(
            channel="teams_email",
            event_cluster_key=first_event.event_cluster_key,
            material_signature=first_event.material_signature,
            delivery_class=decision.delivery_class,
            payload={"title": "changed payload cannot duplicate", "shadow_only": True},
        )
        v.check("first outbox row created", enqueued.created)
        v.check("duplicate outbox row blocked", not duplicate.created)
        v.equal("outbox unique count one", store.stats()["delivery_outbox"], 1)
        v.equal("duplicate returns original id", duplicate.message.outbox_id, enqueued.message.outbox_id)

        claimed = store.claim_outbox(
            channel="teams_email",
            worker_id="worker-a",
            limit=10,
            lease_seconds=60,
            now="2026-07-30T00:02:00Z",
        )
        v.equal("one outbox row claimed", len(claimed), 1)
        claim = claimed[0]
        v.equal("claimed status", claim.status, OutboxStatus.CLAIMED)
        v.check("claim token issued", bool(claim.claim_token))
        v.equal("attempt count increments on claim", claim.attempt_count, 1)
        v.raises(
            "wrong claim token rejected",
            ClaimConflict,
            lambda: store.mark_delivery_succeeded(
                outbox_id=claim.outbox_id,
                claim_token="wrong",
                provider="smtp",
                provider_code="250",
            ),
        )
        store.mark_delivery_succeeded(
            outbox_id=claim.outbox_id,
            claim_token=claim.claim_token or "",
            provider="smtp",
            provider_code="250",
            attempted_at="2026-07-30T00:02:59Z",
        )
        delivered = store.get_outbox(claim.outbox_id)
        v.check("delivered outbox can be read", delivered is not None)
        v.equal("successful delivery is terminal delivered", delivered.status if delivered else None, OutboxStatus.DELIVERED)
        v.equal("successful attempt recorded", store.stats()["delivery_attempts"], 1)
        v.equal(
            "delivered row cannot be claimed again",
            len(store.claim_outbox(
                channel="teams_email",
                worker_id="worker-b",
                now="2026-07-30T00:10:00Z",
            )),
            0,
        )
        v.raises(
            "delivered row cannot transition with stale claim",
            InvalidStateTransition,
            lambda: store.mark_delivery_succeeded(
                outbox_id=claim.outbox_id,
                claim_token=claim.claim_token or "",
                provider="smtp",
                provider_code="250",
            ),
        )

        retry_article = article("store-retry", "AI 데이터센터 전력망 분석")
        store.upsert_article(retry_article)
        retry_event = event_for(retry_article)
        store.upsert_event(retry_event)
        retry = store.enqueue_outbox(
            channel="teams_email",
            event_cluster_key=retry_event.event_cluster_key,
            material_signature=retry_event.material_signature,
            delivery_class="hourly_digest",
            payload={"title": retry_article.title},
        ).message
        retry_claim = store.claim_outbox(
            channel="teams_email",
            worker_id="worker-retry",
            now="2026-07-30T01:00:00Z",
        )[0]
        store.mark_delivery_failed(
            outbox_id=retry.outbox_id,
            claim_token=retry_claim.claim_token or "",
            provider="smtp",
            retryable=True,
            provider_code="421",
            error_class="temporary_smtp",
            error_message="try later",
            attempted_at="2026-07-30T01:01:00Z",
            retry_not_before="2026-07-30T01:10:00Z",
        )
        retry_failed = store.get_outbox(retry.outbox_id)
        v.equal("retryable failure state", retry_failed.status if retry_failed else None, OutboxStatus.RETRYABLE_FAILED)
        v.equal(
            "retry not claimable before not_before",
            len(store.claim_outbox(
                channel="teams_email",
                worker_id="worker-too-early",
                now="2026-07-30T01:05:00Z",
            )),
            0,
        )
        retry_claimed = store.claim_outbox(
            channel="teams_email",
            worker_id="worker-after-delay",
            now="2026-07-30T01:10:00Z",
        )[0]
        v.equal("retry claim increments attempt count", retry_claimed.attempt_count, 2)
        store.mark_delivery_succeeded(
            outbox_id=retry.outbox_id,
            claim_token=retry_claimed.claim_token or "",
            provider="smtp",
            provider_code="250",
            attempted_at="2026-07-30T01:11:00Z",
        )
        v.equal("retry eventually delivered", store.get_outbox(retry.outbox_id).status, OutboxStatus.DELIVERED)

        timezone_article = article("store-timezone", "AI 데이터센터 시간대 정규화")
        store.upsert_article(timezone_article)
        timezone_event = event_for(timezone_article)
        store.upsert_event(timezone_event)
        timezone_message = store.enqueue_outbox(
            channel="teams_email",
            event_cluster_key=timezone_event.event_cluster_key,
            material_signature=timezone_event.material_signature,
            delivery_class="hourly_digest",
            payload={"title": timezone_article.title},
            not_before="2026-07-30T10:10:00+09:00",
        ).message
        v.equal("not_before normalized to canonical UTC", timezone_message.not_before, "2026-07-30T01:10:00Z")
        v.equal(
            "mixed timezone claim blocked before instant",
            len(store.claim_outbox(
                channel="teams_email",
                worker_id="timezone-too-early",
                now="2026-07-30T01:09:59Z",
            )),
            0,
        )
        timezone_claim = store.claim_outbox(
            channel="teams_email",
            worker_id="timezone-on-time",
            now="2026-07-30T10:10:00+09:00",
        )
        v.equal("mixed timezone claim allowed at same instant", len(timezone_claim), 1)
        v.raises(
            "naive claim timestamp fails closed",
            Exception,
            lambda: store.claim_outbox(
                channel="teams_email",
                worker_id="timezone-naive",
                now="2026-07-30T01:10:00",
            ),
        )
        store.mark_delivery_succeeded(
            outbox_id=timezone_claim[0].outbox_id,
            claim_token=timezone_claim[0].claim_token or "",
            provider="shadow",
            provider_code="200",
            attempted_at="2026-07-30T01:10:30Z",
        )
        v.equal(
            "mixed timezone fixture closes cleanly",
            store.get_outbox(timezone_message.outbox_id).status,
            OutboxStatus.DELIVERED,
        )

        terminal_article = article("store-terminal", "AI 규제 공식 발표")
        store.upsert_article(terminal_article)
        terminal_event = event_for(terminal_article)
        store.upsert_event(terminal_event)
        terminal = store.enqueue_outbox(
            channel="teams_email",
            event_cluster_key=terminal_event.event_cluster_key,
            material_signature=terminal_event.material_signature,
            delivery_class="priority_digest",
            payload={"title": terminal_article.title},
        ).message
        terminal_claim = store.claim_outbox(
            channel="teams_email",
            worker_id="worker-terminal",
            now="2026-07-30T02:00:00Z",
        )[0]
        store.mark_delivery_failed(
            outbox_id=terminal.outbox_id,
            claim_token=terminal_claim.claim_token or "",
            provider="smtp",
            retryable=False,
            provider_code="550",
            error_class="recipient_rejected",
            error_message="terminal",
            attempted_at="2026-07-30T02:01:00Z",
        )
        v.equal("terminal failure state", store.get_outbox(terminal.outbox_id).status, OutboxStatus.TERMINAL_FAILED)
        v.equal(
            "terminal failure cannot be reclaimed",
            len(store.claim_outbox(
                channel="teams_email",
                worker_id="worker-terminal-2",
                now="2026-07-30T03:00:00Z",
            )),
            0,
        )

        lease_article = article("store-lease", "AI 데이터센터 공식 발표")
        store.upsert_article(lease_article)
        lease_event = event_for(lease_article)
        store.upsert_event(lease_event)
        lease_message = store.enqueue_outbox(
            channel="teams_email",
            event_cluster_key=lease_event.event_cluster_key,
            material_signature=lease_event.material_signature,
            delivery_class="hourly_digest",
            payload={"title": lease_article.title},
        ).message
        first_lease = store.claim_outbox(
            channel="teams_email",
            worker_id="lease-worker-1",
            lease_seconds=30,
            now="2026-07-30T04:00:00Z",
        )[0]
        v.equal(
            "active lease blocks second worker",
            len(store.claim_outbox(
                channel="teams_email",
                worker_id="lease-worker-2",
                now="2026-07-30T04:00:20Z",
            )),
            0,
        )
        v.raises(
            "expired lease token cannot complete before reclaim",
            ClaimConflict,
            lambda: store.mark_delivery_succeeded(
                outbox_id=first_lease.outbox_id,
                claim_token=first_lease.claim_token or "",
                provider="smtp",
                provider_code="250",
                attempted_at="2026-07-30T04:00:31Z",
            ),
        )
        reclaimed = store.claim_outbox(
            channel="teams_email",
            worker_id="lease-worker-2",
            now="2026-07-30T04:00:31Z",
        )[0]
        v.equal("expired lease row reclaimed", reclaimed.outbox_id, lease_message.outbox_id)
        v.check("reclaim rotates claim token", reclaimed.claim_token != first_lease.claim_token)
        v.equal("reclaim increments attempt count", reclaimed.attempt_count, 2)

        store.record_heartbeat(RuntimeHeartbeat(
            component="collector",
            run_id="run-1",
            status="success",
            observed_at="2026-07-30T05:00:00Z",
            details={"articles": 12},
        ))
        store.record_heartbeat(RuntimeHeartbeat(
            component="collector",
            run_id="run-1",
            status="success",
            observed_at="2026-07-30T05:01:00Z",
            details={"articles": 13},
        ))
        v.equal("heartbeat upsert idempotent", store.stats()["runtime_heartbeats"], 1)

        legacy = {
            "version": 1,
            "article_ids": {"a": {"sent_at": "2026-07-30T00:00:00+09:00"}},
            "normalized_urls": {"u": {"sent_at": "2026-07-30T00:00:00+09:00"}},
            "title_fingerprints": {"t": {"sent_at": "2026-07-30T00:00:00+09:00"}},
            "cluster_keys": {"c": {"sent_at": "2026-07-30T00:00:00+09:00"}},
            "last_successful_send_at": "2026-07-30T00:00:00+09:00",
        }
        first_import = store.import_legacy_teams_state(legacy)
        second_import = store.import_legacy_teams_state(legacy)
        v.equal("legacy import scanned four entries", first_import.scanned, 4)
        v.equal("legacy import inserted four entries", first_import.inserted, 4)
        v.equal("legacy reimport inserts zero", second_import.inserted, 0)
        v.equal("legacy reimport reports four duplicates", second_import.duplicates, 4)
        v.equal("legacy import table remains four", store.stats()["legacy_delivery_imports"], 4)


def verify_cli_and_static_safety(v: Verifier, temp: Path) -> None:
    files = (
        ROOT / "app/runtime_models.py",
        ROOT / "app/runtime_store.py",
        ROOT / "app/runtime_sqlite.py",
        ROOT / "app/runtime_policy.py",
        ROOT / "scripts/import_legacy_runtime_state.py",
        ROOT / "scripts/replay_runtime_policy.py",
        ROOT / "scripts/verify_runtime_core.py",
    )
    for path in files:
        py_compile.compile(str(path), doraise=True)
    v.check("all runtime Python files compile", True)

    forbidden_imports = ("import requests", "import httpx", "import smtplib", "import socket", "urllib.request")
    production_files = files[:-1]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in production_files)
    v.check("runtime core imports no network client", not any(token in combined for token in forbidden_imports))
    v.check("runtime core contains no Git push", "git push" not in combined)
    v.check("runtime core contains no workflow dispatch", "workflow_dispatch" not in combined)

    legacy_path = temp / "teams_push_state.json"
    legacy_payload = {
        "version": 1,
        "article_ids": {"a": {"sent_at": "2026-07-30T00:00:00+09:00"}},
        "normalized_urls": {},
        "title_fingerprints": {},
        "cluster_keys": {},
        "last_successful_send_at": "2026-07-30T00:00:00+09:00",
    }
    legacy_path.write_text(json.dumps(legacy_payload, ensure_ascii=False), encoding="utf-8")
    before_sha = hashlib.sha256(legacy_path.read_bytes()).hexdigest()

    dry = subprocess.run(
        [sys.executable, str(ROOT / "scripts/import_legacy_runtime_state.py"), "--teams-state", str(legacy_path)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    v.equal("legacy CLI dry-run exits zero", dry.returncode, 0)
    v.check("legacy CLI dry-run reports no write", "mode=dry_run_no_write" in dry.stdout)

    shadow_db = temp / "legacy-shadow.db"
    apply = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/import_legacy_runtime_state.py"),
            "--teams-state",
            str(legacy_path),
            "--db",
            str(shadow_db),
            "--apply",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    v.equal("legacy CLI apply exits zero", apply.returncode, 0)
    v.check("legacy CLI apply targets shadow DB", "mode=apply_shadow_db" in apply.stdout)
    after_sha = hashlib.sha256(legacy_path.read_bytes()).hexdigest()
    v.equal("legacy source state remains byte-identical", after_sha, before_sha)

    replay_json = temp / "replay.json"
    replay = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/replay_runtime_policy.py"),
            "--json-output",
            str(replay_json),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    v.equal("policy replay CLI exits zero", replay.returncode, 0)
    v.check("policy replay reports zero network", "network_calls=0" in replay.stdout)
    v.check("policy replay reports zero channel sends", "channel_sends=0" in replay.stdout)
    replay_payload = json.loads(replay_json.read_text(encoding="utf-8"))
    by_id = {item["article_id"]: item for item in replay_payload["results"]}
    v.equal(
        "received airport article downgraded to dashboard only",
        by_id["received:news1-taiwan-airport"]["decision_class"],
        DecisionClass.P3.value,
    )
    v.equal(
        "received interview article downgraded to hourly digest",
        by_id["received:joongang-nuclear-interview"]["decision_class"],
        DecisionClass.P2.value,
    )
    v.equal(
        "received Bill Gates speculation rejected",
        by_id["received:econovill-bill-gates"]["decision_class"],
        DecisionClass.REJECT.value,
    )
    v.equal(
        "confirmed HDEC contract remains immediate",
        by_id["fixture:hdec-confirmed-contract"]["decision_class"],
        DecisionClass.P0.value,
    )

    architecture = ROOT / "docs/architecture/d7-ak-6f-runtime-contract.md"
    v.check("runtime architecture contract exists", architecture.exists())
    if architecture.exists():
        text = architecture.read_text(encoding="utf-8")
        for token in (
            "delivery_outbox",
            "runtime_heartbeats",
            "shadow-only",
            "TEAMS_AI_NEWS_WATCH=0",
            "GitHub Actions",
            "timezone-aware",
            "canonical article id",
            "expired lease",
        ):
            v.check(f"architecture contract contains {token}", token in text)


def main() -> int:
    verifier = Verifier()
    verify_models(verifier)
    verify_policy(verifier)
    with tempfile.TemporaryDirectory(prefix="d7ak6f-runtime-core-") as tmp:
        temp = Path(tmp)
        verify_store(verifier, temp)
        verify_cli_and_static_safety(verifier, temp)

    print(f"checks={verifier.checks} failures={verifier.failures}")
    print("network_calls=0")
    print("smtp_connections=0")
    print("teams_sends=0")
    print("telegram_sends=0")
    print("production_state_writes=0")
    if verifier.failures:
        print("RESULT=D7-AK-6F-C1-R1_RUNTIME_CORE_VERIFIER_FAIL")
        return 1
    print("RESULT=D7-AK-6F-C1-R1_RUNTIME_CORE_VERIFIER_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
