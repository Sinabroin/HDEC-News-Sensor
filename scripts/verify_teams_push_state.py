#!/usr/bin/env python3
"""Deterministic verifier for D7-AK-5B persistent Teams send dedup."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.teams_ai_push import select_teams_push_candidates
from app.teams_push_state import (
    HELD_SPECIALISTS_KEY,
    InvalidTeamsPushState,
    article_identity,
    clear_held_record,
    derive_event_cluster_key,
    empty_state,
    evaluate_dedup,
    filter_unsent_candidates,
    get_held_record,
    load_state,
    mark_held_replaced_by_major,
    mark_sent_after_success,
    material_signature,
    minutes_between,
    observe_held_specialist,
    persist_after_success,
    save_state,
    validate_state,
)


def article(**overrides):
    base = {
        "article_key": "article-1",
        "title": "OpenAI와 Microsoft, AI 데이터센터 투자 계약 체결",
        "summary": "양사가 데이터센터 투자 계약을 공식 체결했다.",
        "source": "Reuters",
        "published_at": "2026-07-23T00:20:00+00:00",
        "url": "https://publisher.example.test/news/1?utm_source=x&ref=y",
        "publisher_direct": True,
        "shadow_confirmed_event_types": ["contract_signed"],
        "change_type": "new_article",
    }
    base.update(overrides)
    return base


def main() -> int:
    first = article()
    cluster = derive_event_cluster_key(first, "ai_datacenter")
    signature = material_signature(first)
    state = empty_state()
    decision = evaluate_dedup(
        state, first, cluster_key=cluster, signature=signature, is_material_update=False
    )
    assert decision.send_allowed and decision.reason == "new_article_or_event"

    unchanged = mark_sent_after_success(
        state, first, cluster_key=cluster, signature=signature,
        importance="top", source="Reuters", send_succeeded=False,
    )
    assert unchanged == state and unchanged["last_successful_send_at"] is None

    sent = mark_sent_after_success(
        state, first, cluster_key=cluster, signature=signature,
        importance="top", source="Reuters", send_succeeded=True,
        sent_at="2026-07-23T09:30:00+09:00", delivery_id="test-delivery",
    )
    duplicate = evaluate_dedup(
        sent, first, cluster_key=cluster, signature=signature, is_material_update=False
    )
    assert not duplicate.send_allowed and duplicate.reason.startswith("duplicate:")
    selected = select_teams_push_candidates([
        {**first, "score": 4.7, "hdec_relevance": "데이터센터 EPC 영향",
         "shadow_urgency_status": "confirmed", "shadow_would_pass": True}
    ])
    accepted, decisions = filter_unsent_candidates(sent, selected)
    assert accepted == () and len(decisions) == 1 and not decisions[0].send_allowed

    tracking_variant = article(url="https://publisher.example.test/news/1?utm_campaign=z")
    tracking_duplicate = evaluate_dedup(
        sent, tracking_variant, cluster_key=cluster,
        signature=material_signature(tracking_variant), is_material_update=False,
    )
    assert not tracking_duplicate.send_allowed

    # URL identity prefers the shared publisher-direct contract.
    google_url = "https://news.google.com/rss/articles/state-fixture"
    canonical_url = "https://publisher.example.test/news/canonical"
    canonical_article = article(
        article_key="canonical-a",
        title="OpenAI, AI 데이터센터 신규 투자 계약 체결",
        url=google_url,
        canonical_url=canonical_url,
    )
    assert article_identity(canonical_article)["normalized_url"] == canonical_url
    canonical_state = mark_sent_after_success(
        empty_state(),
        canonical_article,
        cluster_key="fixture:canonical-a",
        signature=material_signature(canonical_article),
        importance="top",
        source="Reuters",
        send_succeeded=True,
        sent_at="2026-07-23T09:30:00+09:00",
    )
    canonical_variant = article(
        article_key="canonical-b",
        title="Microsoft, 별도 제목으로 전한 AI 인프라 계약",
        url="https://news.google.com/rss/articles/state-fixture-variant",
        external_url=canonical_url,
    )
    canonical_duplicate = evaluate_dedup(
        canonical_state,
        canonical_variant,
        cluster_key="fixture:canonical-b",
        signature=material_signature(canonical_variant),
        is_material_update=False,
    )
    assert (
        not canonical_duplicate.send_allowed
        and canonical_duplicate.reason == "duplicate:normalized_url"
    )

    # Aggregator-only articles still get a normalized URL identity. Tracking variants
    # must dedup even when no publisher URL was resolved.
    google_fallback = article(
        article_key="google-fallback-a",
        title="현대건설, 공간 AI 컨퍼런스 개최 확정",
        url="https://news.google.com/rss/articles/fallback-id?oc=5&utm_source=watch",
    )
    google_identity = article_identity(google_fallback)
    assert google_identity["normalized_url"] == (
        "https://news.google.com/rss/articles/fallback-id?oc=5"
    )
    google_state = mark_sent_after_success(
        empty_state(),
        google_fallback,
        cluster_key="fixture:google-fallback-a",
        signature=material_signature(google_fallback),
        importance="top",
        source="팍스경제TV",
        send_succeeded=True,
        sent_at="2026-07-23T09:30:00+09:00",
    )
    google_variant = article(
        article_key="google-fallback-b",
        title="별도 제목으로 전한 공간 AI 행사",
        url="https://news.google.com/rss/articles/fallback-id?utm_campaign=retry&oc=5",
    )
    google_duplicate = evaluate_dedup(
        google_state,
        google_variant,
        cluster_key="fixture:google-fallback-b",
        signature=material_signature(google_variant),
        is_material_update=False,
    )
    assert (
        not google_duplicate.send_allowed
        and google_duplicate.reason == "duplicate:normalized_url"
    )

    syndication = article(
        article_key="article-2",
        title="Microsoft·OpenAI, AI 데이터센터 계약 공식 체결",
        url="https://wire.publisher.example.test/story/99",
    )
    same_cluster = derive_event_cluster_key(syndication, "ai_datacenter")
    assert same_cluster != cluster
    distinct_article = evaluate_dedup(
        sent, syndication, cluster_key=same_cluster,
        signature=material_signature(syndication), is_material_update=False,
    )
    assert distinct_article.send_allowed and distinct_article.reason == "new_article_or_event"

    update = article(
        change_type="material_content_update",
        summary="계약 금액 70억달러와 현대건설 EPC 참여 검토가 새로 공개됐다.",
        contract_value="USD 7bn",
    )
    update_signature = material_signature(update)
    allowed_update = evaluate_dedup(
        sent, update, cluster_key=cluster, signature=update_signature, is_material_update=True
    )
    assert allowed_update.send_allowed and allowed_update.is_update

    sent_update = mark_sent_after_success(
        sent, update, cluster_key=cluster, signature=update_signature,
        importance="top", source="Reuters", send_succeeded=True, is_update=True,
        sent_at="2026-07-23T09:40:00+09:00",
    )
    repeated_update = evaluate_dedup(
        sent_update, update, cluster_key=cluster, signature=update_signature, is_material_update=True
    )
    assert not repeated_update.send_allowed

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "teams_push_state.json"
        before_files = list(Path(tmp).iterdir())
        persist_after_success(
            state, first, path=path, cluster_key=cluster, signature=signature,
            importance="top", source="Reuters", send_succeeded=False,
        )
        assert list(Path(tmp).iterdir()) == before_files
        persisted = persist_after_success(
            state, first, path=path, cluster_key=cluster, signature=signature,
            importance="top", source="Reuters", send_succeeded=True,
            sent_at="2026-07-23T09:30:00+09:00",
        )
        assert path.exists() and load_state(path) == persisted
        path.write_text("{broken", encoding="utf-8")
        try:
            load_state(path)
        except InvalidTeamsPushState:
            pass
        else:
            raise AssertionError("corrupt existing state must fail closed")

    # ------------------------------------------------------------------
    # D7-AK-6E R4-R9A — held-specialist holdback records.
    # ------------------------------------------------------------------
    # Backward compatibility: legacy states carry no held section, and the
    # key is omitted again once the last hold clears (byte-stable schema).
    assert validate_state(empty_state()) == empty_state()
    assert HELD_SPECIALISTS_KEY not in validate_state(empty_state())

    held_article = article(
        article_key="held-1",
        title="SK하이닉스, HBM 데이터센터 공급 계약 체결",
        source="테크M",
        url="https://www.techm.kr/news/articleView.html?idxno=880001",
    )
    observed = observe_held_specialist(
        empty_state(), held_article,
        cluster_key="title:sk하이닉스hbm데이터센터공급계약체결",
        source="테크M", source_tier="neutral",
        holdback_reason="holdback_active", fallback_eligible=False,
        now="2026-08-04T07:00:00+09:00",
    )
    held_entry = get_held_record(observed, held_article)
    assert held_entry is not None
    assert held_entry["first_seen_at"] == "2026-08-04T07:00:00+09:00"
    assert held_entry["source_tier"] == "neutral"
    assert held_entry["fallback_eligible"] is False
    # A held record never touches the sent-ledger maps.
    assert not observed["article_ids"] and not observed["cluster_keys"]
    assert observed["last_successful_send_at"] is None

    # first_seen_at is stable across later observations; last_seen_at moves.
    reobserved = observe_held_specialist(
        observed, held_article,
        cluster_key="title:sk하이닉스hbm데이터센터공급계약체결",
        source="테크M", source_tier="neutral",
        holdback_reason="holdback_active", fallback_eligible=False,
        now="2026-08-04T09:10:00+09:00",
    )
    reentry = get_held_record(reobserved, held_article)
    assert reentry["first_seen_at"] == "2026-08-04T07:00:00+09:00"
    assert reentry["last_seen_at"] == "2026-08-04T09:10:00+09:00"
    assert minutes_between(
        reentry["first_seen_at"], reentry["last_seen_at"]
    ) == 130.0
    # Malformed timestamps read as "not aged" (held, fail-closed).
    assert minutes_between("broken", reentry["last_seen_at"]) == 0.0

    # A delivered same-event major marks the held specialist as supporting
    # evidence and removes its fallback eligibility.
    replaced, marks = mark_held_replaced_by_major(
        reobserved, "title:sk하이닉스hbm데이터센터공급계약체결",
        major_identity="teams_ai_push:deadbeef0001", major_source="연합뉴스",
    )
    assert marks == 1
    replaced_entry = get_held_record(replaced, held_article)
    assert replaced_entry["replaced_by_major_media"] == "teams_ai_push:deadbeef0001"
    assert replaced_entry["representative_publisher"] == "연합뉴스"
    assert replaced_entry["fallback_eligible"] is False

    # Clearing the last hold returns the state to the legacy shape.
    cleared = clear_held_record(replaced, held_article)
    assert HELD_SPECIALISTS_KEY not in cleared and cleared == empty_state()

    # Held records round-trip through save/load, and a malformed held
    # section fails closed like every other malformed state.
    with tempfile.TemporaryDirectory() as tmp:
        held_path = Path(tmp) / "teams_push_state_held.json"
        save_state(reobserved, held_path)
        assert load_state(held_path) == validate_state(reobserved)
        held_raw = json.loads(held_path.read_text(encoding="utf-8"))
        held_raw[HELD_SPECIALISTS_KEY] = {"key": "not-an-object"}
        held_path.write_text(
            json.dumps(held_raw, ensure_ascii=False), encoding="utf-8"
        )
        try:
            load_state(held_path)
        except InvalidTeamsPushState:
            pass
        else:
            raise AssertionError("malformed held_specialists must fail closed")

    print("RESULT=D7-AK-5B_TEAMS_PUSH_STATE_VERIFIER_PASS")
    print(json.dumps({"cluster_key": cluster, "stored_articles": len(sent_update["article_ids"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
