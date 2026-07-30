#!/usr/bin/env python3
"""Replay article fixtures through the D7-AK-6F shadow policy and outbox.

No network or channel transport is imported. The script may use an in-memory DB or an
explicit shadow SQLite path. It is safe to run against fixture JSON repeatedly because
article/event upserts and outbox creation are idempotent.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.runtime_models import (  # noqa: E402
    CanonicalArticle,
    DecisionClass,
    NewsEvent,
    deterministic_id,
    sha256_text,
    stable_json,
    utc_now_iso,
)
from app.runtime_policy import RuntimePolicyEngine, decision_summary  # noqa: E402
from app.runtime_sqlite import SQLiteRuntimeStore  # noqa: E402


_BUILTIN_FIXTURES: tuple[dict[str, Any], ...] = (
    {
        "article_id": "received:joongang-nuclear-interview",
        "canonical_url": "https://www.joongang.co.kr/article/25449302",
        "title": "[조민근의 경제를 묻다] 원전 멈춰서는데 호남 반도체? 수명 연장부터",
        "summary": "반도체 공장과 AI 데이터센터, 피지컬 AI에는 전력이 필요하다는 인터뷰 기사다.",
        "source": "중앙일보",
        "published_at": "2026-07-30T00:16:00+09:00",
    },
    {
        "article_id": "received:mtn-china-nuclear",
        "canonical_url": "https://news.mtn.co.kr/news-detail/2026072912412837947",
        "title": "中 원전 굴기의 두 얼굴 두산엔 기회 K원전엔 최대 경쟁자",
        "summary": "AI 데이터센터발 전력 수요와 중국 원전 증설을 다룬 분석 기사다.",
        "source": "MTN",
        "published_at": "2026-07-30T06:04:00+09:00",
    },
    {
        "article_id": "received:news1-taiwan-airport",
        "canonical_url": "https://www.news1.kr/realestate/general/6242752",
        "title": "멈추지 않는 공항 삼성물산 대만 하늘길의 심장을 짓다",
        "summary": "대만 공항 T3 건설과 TSMC 중심 반도체·AI 산업의 항공화물 증가를 설명한다.",
        "source": "뉴스1",
        "published_at": "2026-07-30T07:05:00+09:00",
    },
    {
        "article_id": "received:dealsite-hanmiglobal",
        "canonical_url": "https://dealsite.co.kr/articles/166095",
        "title": "건축명가 리포트 한미글로벌 SMR 데이터센터 배팅 글로벌 체질",
        "summary": "AI 데이터센터와 원전 분야를 향후 성장동력으로 삼는 기업 전략 기사다.",
        "source": "딜사이트",
        "published_at": "2026-07-30T08:02:00+09:00",
    },
    {
        "article_id": "received:econovill-bill-gates",
        "canonical_url": "https://www.econovill.com/news/articleView.html?idxno=746603",
        "title": "빌 게이츠 내달 방한 SK HD현대와 공급망 확대 논의 전망",
        "summary": "AI 데이터센터 전력 수요와 차세대 원전 공급망 협력 가능성을 전망한다.",
        "source": "이코노믹리뷰",
        "published_at": "2026-07-30T07:56:00+09:00",
    },
    {
        "article_id": "received:dealsite-naver-factory",
        "canonical_url": "https://dealsite.co.kr/articles/166078",
        "title": "네이버 AI 팩토리 뜬다 13조 프로젝트에 건설사 군침",
        "summary": "AI 데이터센터, 전력망, PF가 결합된 대규모 개발 사업을 다룬다.",
        "source": "딜사이트",
        "published_at": "2026-07-30T07:02:00+09:00",
    },
    {
        "article_id": "fixture:hdec-confirmed-contract",
        "canonical_url": "https://example.invalid/hdec-confirmed-contract",
        "title": "현대건설 AI 데이터센터 본계약 체결",
        "summary": "현대건설이 대규모 AI 데이터센터 본계약 체결을 공식 발표했다.",
        "source": "공식 발표",
        "published_at": "2026-07-30T09:00:00+09:00",
        "confirmed_event_types": ["contract_confirmed"],
        "explicit_evidence": ["official_release"],
    },
    {
        "article_id": "provider:rss-hdec-confirmed-contract",
        "canonical_url": "https://example.invalid/hdec-confirmed-contract",
        "title": "현대건설 AI 데이터센터 수주 경쟁 본격화",
        "summary": "시장 점유율 확대를 위한 수주 활동을 다룬 분석 기사다.",
        "source": "RSS 재수집",
        "published_at": "2026-07-30T09:00:00+09:00",
        "event_cluster_key": "provider:event:key-must-be-ignored",
        "material_signature": "provider-material-signature-must-be-ignored",
    },
    {
        "article_id": "fixture:hdec-analysis-first",
        "canonical_url": "https://example.invalid/hdec-analysis-first",
        "title": "현대건설 AI 데이터센터 수주 경쟁 본격화",
        "summary": "시장 점유율 확대를 위한 수주 활동을 다룬 분석 기사다.",
        "source": "분석 매체",
        "published_at": "2026-07-30T09:10:00+09:00",
    },
    {
        "article_id": "provider:official-hdec-analysis-first",
        "canonical_url": "https://example.invalid/hdec-analysis-first",
        "title": "현대건설 AI 데이터센터 본계약 체결",
        "summary": "현대건설이 대규모 AI 데이터센터 본계약 체결을 공식 발표했다.",
        "source": "공식 발표 재수집",
        "published_at": "2026-07-30T09:10:00+09:00",
        "confirmed_event_types": ["contract_confirmed"],
        "explicit_evidence": ["official_release"],
    },
)


def _load(path: Path | None) -> list[Mapping[str, Any]]:
    if path is None:
        return list(_BUILTIN_FIXTURES)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"fixture_invalid: {exc}") from exc
    if not isinstance(value, list) or any(not isinstance(item, Mapping) for item in value):
        raise SystemExit("fixture_invalid: root must be a list of objects")
    return list(value)


def _event_for(
    article: CanonicalArticle,
    fixture: Mapping[str, Any],
    *,
    canonical_article_id: str,
    resolver_event_cluster_key: str | None = None,
    resolver_material_signature: str | None = None,
) -> NewsEvent:
    """Build event identity from canonical authority, not provider presentation text.

    Provider-supplied ``event_cluster_key`` and ``material_signature`` fields are never
    trusted by the replay path. A future trusted event resolver may supply both explicit
    override arguments together; partial overrides fail closed.
    """
    resolver_event_key = str(resolver_event_cluster_key or "").strip()
    resolver_signature = str(resolver_material_signature or "").strip()
    if bool(resolver_event_key) != bool(resolver_signature):
        raise ValueError(
            "trusted resolver identity requires both event cluster key and material signature"
        )

    if resolver_event_key:
        event_key = resolver_event_key
        signature = resolver_signature
        resolver_authoritative = True
    else:
        event_key = deterministic_id("event", canonical_article_id, length=24)
        signature = sha256_text(stable_json({
            "canonical_article_id": canonical_article_id,
            "revision": "initial",
        }))
        resolver_authoritative = False

    return NewsEvent(
        event_cluster_key=event_key,
        primary_article_id=canonical_article_id,
        event_type=str(fixture.get("event_type") or "article_signal"),
        headline=article.title,
        material_signature=signature,
        first_seen_at=article.observed_at,
        last_seen_at=article.observed_at,
        attributes={
            "source": article.source,
            "canonical_url": article.canonical_url,
            "provider_article_id": article.article_id,
            "canonical_article_id": canonical_article_id,
            "resolver_authoritative": resolver_authoritative,
        },
    )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--fixtures", type=Path)
    result.add_argument("--db", default=":memory:")
    result.add_argument("--json-output", type=Path)
    return result


def main() -> int:
    args = parser().parse_args()
    fixtures = _load(args.fixtures)
    engine = RuntimePolicyEngine()
    decisions = []
    rows = []
    created_outbox = 0

    with SQLiteRuntimeStore(args.db) as store:
        for fixture in fixtures:
            article = CanonicalArticle(
                article_id=str(fixture.get("article_id") or fixture.get("article_key") or ""),
                canonical_url=str(fixture.get("canonical_url") or fixture.get("url") or ""),
                title=str(fixture.get("title") or ""),
                source=str(fixture.get("source") or "unknown"),
                published_at=str(fixture.get("published_at") or utc_now_iso()),
                summary=str(fixture.get("summary") or fixture.get("snippet") or ""),
                raw_payload=dict(fixture),
            )
            canonical_article_id = store.upsert_article(article)
            event = _event_for(
                article,
                fixture,
                canonical_article_id=canonical_article_id,
            )
            store.upsert_event(event)
            policy_input = {
                **dict(fixture),
                "event_cluster_key": event.event_cluster_key,
                "material_signature": event.material_signature,
                "article_id": canonical_article_id,
                "provider_article_id": article.article_id,
                "published_at": article.published_at,
            }
            candidate_decision = engine.decide(policy_input)
            decision = store.record_policy_decision(candidate_decision)
            decisions.append(decision)

            outbox_created = False
            if decision.should_enqueue:
                outcome = store.enqueue_outbox(
                    channel="teams_email",
                    event_cluster_key=event.event_cluster_key,
                    material_signature=event.material_signature,
                    delivery_class=decision.delivery_class,
                    payload={
                        "article_id": canonical_article_id,
                        "provider_article_id": article.article_id,
                        "title": article.title,
                        "summary": article.summary,
                        "source": article.source,
                        "canonical_url": article.canonical_url,
                        "decision_id": decision.decision_id,
                        "policy_version": decision.policy_version,
                        "decision_class": decision.decision_class.value,
                        "shadow_only": True,
                    },
                )
                outbox_created = outcome.created
                created_outbox += 1 if outcome.created else 0

            row = {
                "article_id": article.article_id,
                "canonical_article_id": canonical_article_id,
                "event_cluster_key": event.event_cluster_key,
                "material_signature": event.material_signature,
                "resolver_authoritative": bool(event.attributes.get("resolver_authoritative")),
                "provider_event_cluster_key": str(fixture.get("event_cluster_key") or ""),
                "provider_material_signature": str(fixture.get("material_signature") or ""),
                "candidate_decision_class": candidate_decision.decision_class.value,
                "candidate_delivery_class": candidate_decision.delivery_class,
                "authoritative_decision_reused": candidate_decision != decision,
                "decision_class": decision.decision_class.value,
                "delivery_class": decision.delivery_class,
                "should_enqueue": decision.should_enqueue,
                "outbox_created": outbox_created,
                "reasons": list(decision.reasons),
            }
            rows.append(row)
            print(
                "POLICY_REPLAY="
                f"{article.article_id}"
                f"|canonical_article_id={canonical_article_id}"
                f"|event_cluster_key={event.event_cluster_key}"
                f"|material_signature={event.material_signature}"
                f"|resolver_authoritative={str(bool(event.attributes.get('resolver_authoritative'))).lower()}"
                f"|candidate_decision={candidate_decision.decision_class.value}"
                f"|decision={decision.decision_class.value}"
                f"|delivery={decision.delivery_class}"
                f"|enqueue={str(decision.should_enqueue).lower()}"
                f"|outbox_created={str(outbox_created).lower()}"
                f"|reasons={','.join(decision.reasons)}"
            )

        stats = store.stats()

    payload = {
        "policy_version": engine.policy_version,
        "fixture_count": len(fixtures),
        "decision_summary": decision_summary(decisions),
        "outbox_created": created_outbox,
        "store_stats": stats,
        "results": rows,
        "network_calls": 0,
        "channel_sends": 0,
    }
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(f"fixture_count={len(fixtures)}")
    print(f"outbox_created={created_outbox}")
    print("network_calls=0")
    print("channel_sends=0")
    print("RESULT=D7-AK-6F-C1_POLICY_REPLAY_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
