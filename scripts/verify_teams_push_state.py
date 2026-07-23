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
    InvalidTeamsPushState,
    derive_event_cluster_key,
    empty_state,
    evaluate_dedup,
    filter_unsent_candidates,
    load_state,
    mark_sent_after_success,
    material_signature,
    persist_after_success,
)


def article(**overrides):
    base = {
        "article_key": "article-1",
        "title": "OpenAI와 Microsoft, AI 데이터센터 투자 계약 체결",
        "summary": "양사가 데이터센터 투자 계약을 공식 체결했다.",
        "source": "Reuters",
        "published_at": "2026-07-23T00:20:00+00:00",
        "url": "https://example.com/news/1?utm_source=x&ref=y",
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

    tracking_variant = article(url="https://example.com/news/1?utm_campaign=z")
    tracking_duplicate = evaluate_dedup(
        sent, tracking_variant, cluster_key=cluster,
        signature=material_signature(tracking_variant), is_material_update=False,
    )
    assert not tracking_duplicate.send_allowed

    syndication = article(
        article_key="article-2",
        title="Microsoft·OpenAI, AI 데이터센터 계약 공식 체결",
        url="https://other.example/story/99",
    )
    same_cluster = derive_event_cluster_key(syndication, "ai_datacenter")
    assert same_cluster == cluster
    cluster_duplicate = evaluate_dedup(
        sent, syndication, cluster_key=same_cluster,
        signature=material_signature(syndication), is_material_update=False,
    )
    assert not cluster_duplicate.send_allowed and cluster_duplicate.matched_key == "cluster_key"

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

    print("RESULT=D7-AK-5B_TEAMS_PUSH_STATE_VERIFIER_PASS")
    print(json.dumps({"cluster_key": cluster, "stored_articles": len(sent_update["article_ids"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
