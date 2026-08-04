#!/usr/bin/env python3
"""Offline invocation verifier for R4-R7 editorial-memory runtime integration."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
for item in (ROOT, SCRIPTS):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from app import (  # noqa: E402
    editorial_briefings,
    editorial_memory,
    editorial_preference_runtime,
    editorial_review,
    hermes_adapter,
    teams_ai_push,
)
import ingest_editorial_feedback  # noqa: E402

PASSES = 0
FAILURES: list[str] = []


def check(label: str, condition: object, detail: object = "") -> bool:
    global PASSES
    if condition:
        PASSES += 1
        print(f"PASS {label}")
        return True
    FAILURES.append(label)
    suffix = f" :: {detail}" if detail else ""
    print(f"FAIL {label}{suffix}")
    return False


def _ref(level: str, label: str) -> editorial_preference_runtime.PrecedentRef:
    return editorial_preference_runtime.PrecedentRef(
        article_id=f"spy-{level}-{label}",
        evidence_level=level,
        similarity=1.0,
        title=f"{level} precedent",
        edition_key="spy-edition",
    )


@dataclass
class SpyRuntime:
    memory_active: bool = False
    adjustments: tuple[float, ...] = ()
    profile_version: str = "spy-profile-v1"

    def __post_init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def decide(
        self, product: str, article: Mapping[str, Any]
    ) -> editorial_preference_runtime.PreferenceDecision:
        index = len(self.calls)
        self.calls.append((product, dict(article)))
        adjustment = self.adjustments[index] if index < len(self.adjustments) else 0.0
        approved = (_ref("gold_plus", str(index)),) if adjustment > 0 else ()
        rejected = (_ref("hard_negative", str(index)),) if adjustment <= -0.75 else ()
        near = (_ref("near_miss", str(index)),) if -0.75 < adjustment < 0 else ()
        silver = (_ref("silver_candidate", str(index)),) if adjustment == 0.25 else ()
        recommendation = (
            editorial_preference_runtime.RECOMMEND_PREFER
            if adjustment >= 0.15
            else editorial_preference_runtime.RECOMMEND_AVOID
            if rejected
            else editorial_preference_runtime.RECOMMEND_NEUTRAL
        )
        return editorial_preference_runtime.PreferenceDecision(
            product=product,
            runtime_version=editorial_preference_runtime.RUNTIME_VERSION,
            profile_version=self.profile_version,
            profile_digest="spy-profile-digest",
            corpus_digest="spy-corpus-digest",
            memory_active=self.memory_active,
            local_retrieval_used=True,
            hermes_retrieval_used=False,
            approved_precedents=approved,
            rejected_precedents=rejected,
            near_miss_precedents=near,
            silver_precedents=silver,
            decisive_differences=("spy deterministic difference",),
            preference_score=adjustment * 10,
            preference_adjustment=adjustment,
            recommendation=recommendation,
            reason_codes=("spy_runtime",),
        )


def _brief_rows(edition_type: str, count: int = 8) -> tuple[datetime, list[dict]]:
    run_at = datetime.fromisoformat("2026-07-27T07:00:00+09:00")
    coverage = editorial_briefings.coverage_for(edition_type, run_at)
    titles = (
        "정부, 공공부문 AI 전환 1조원 투자 확정",
        "기업, 생성형 AI 업무혁신 플랫폼 도입 계약 체결",
        "산업용 AI 로봇 생산라인 가동 계획 공개",
        "EU AI법 시행과 생성물 표시 의무 확정",
        "소버린 AI 데이터센터 20MW 구축 착공",
        "대학, AI 평가와 교육 제도 개편안 시행",
        "법조계, AI 환각 판례 검증 지침 도입",
        "AI 반도체 연구 모델 공개와 인프라 투자 확대",
        "기업 AI 인재 조직 신설과 전문인력 전환 계획",
        "소비자 생성형 AI 위험 보호 기준 발표",
        "산업용 물리 AI 안전 규제와 현장 적용 확정",
        "AI 시장 구조 변화와 장기 생산성 연구 공개",
    )
    publishers = (
        ("연합뉴스", "yna.co.kr"),
        ("조선일보", "chosun.com"),
        ("중앙일보", "joongang.co.kr"),
        ("동아일보", "donga.com"),
        ("한국경제", "hankyung.com"),
        ("매일경제", "mk.co.kr"),
        ("서울경제", "sedaily.com"),
        ("전자신문", "etnews.com"),
        ("ZDNet Korea", "zdnet.co.kr"),
        ("파이낸셜뉴스", "fnnews.com"),
        ("헤럴드경제", "heraldcorp.com"),
        ("아시아경제", "asiae.co.kr"),
    )
    rows = []
    for index in range(count):
        source, host = publishers[index]
        rows.append(
            {
                "id": f"runtime-{edition_type}-{index + 1}",
                "title": titles[index],
                "source": source,
                "published_at": (
                    coverage.end - timedelta(minutes=index + 5)
                ).isoformat(),
                "url": f"https://www.{host}/ai/runtime-{index + 1}",
                "snippet": (
                    f"{titles[index]} 관련 공식 발표가 나왔다. "
                    "구체적 적용 범위와 일정이 확인돼 경영 의사결정 검토가 필요하다."
                ),
                "source_metadata": {
                    "provider": "offline_fixture",
                    "query": "AI 데이터센터",
                },
            }
        )
    return run_at, rows


def _normalize_with_spy(
    edition_type: str,
    rows: list[dict],
    runtime: SpyRuntime,
    audit: editorial_briefings.SelectionAuditCounters,
) -> list[editorial_briefings.EditorialArticle]:
    run_at = datetime.fromisoformat("2026-07-27T07:00:00+09:00")
    return editorial_briefings.normalize_articles(
        rows,
        editorial_briefings.coverage_for(edition_type, run_at),
        limit=(
            editorial_briefings.DAILY_MAX_ARTICLES
            if edition_type == "daily"
            else editorial_briefings.WEEKLY_MAX_ARTICLES
        ),
        resolve_images=False,
        selection_mode=editorial_briefings.SELECTION_MODE_EDITORIAL_PRIORITY,
        selection_audit=audit,
        preference_runtime=runtime,
        edition_type=edition_type,
    )


def _single_level_runtime(level: str) -> editorial_preference_runtime.EditorialPreferenceRuntime:
    title = "정부 공공부문 AI 전환 정책 시행"
    record = editorial_memory.CorpusRecord(
        article_id=f"record-{level}",
        evidence_level=level,
        product=editorial_memory.PRODUCT_WEEKLY,
        edition_key="fixture-edition",
        title=title,
        human_summary="정부 공공부문 AI 전환 정책 시행",
        category="투자·산업",
        source="연합뉴스",
        canonical_url="https://example.test/article",
        headline=level == editorial_memory.EVIDENCE_GOLD_PLUS,
        human_order=1,
        tokens=editorial_memory.tokenize(title, title),
    )
    corpus = editorial_memory.Corpus(
        records=(record,), digest=f"digest-{level}", editions=("fixture-edition",)
    )
    status = editorial_preference_runtime.RuntimeStatus(
        available=True,
        active=True,
        profile_version="fixture-active",
        profile_digest="fixture-profile-digest",
        corpus_digest=corpus.digest,
        detail="verified",
    )
    return editorial_preference_runtime.EditorialPreferenceRuntime(
        corpus=corpus,
        profile={"product_heads": {}},
        status=status,
    )


def main() -> int:
    # Teams invocation is observable and runs only over deterministic eligible rows.
    observed = json.loads(
        (ROOT / "data" / "observed_false_positive_fixtures.json").read_text(
            encoding="utf-8"
        )
    )["articles"]
    teams_spy = SpyRuntime()
    teams_rows = [
        {
            **item,
            "publisher_direct": True,
            "current_run_seen": True,
            "summary": item.get("snippet", ""),
        }
        for item in observed
    ]
    teams_selected, teams_audit = teams_ai_push.select_teams_push_candidates_with_audit(
        teams_rows,
        max_articles=teams_ai_push.MAX_TEAMS_ARTICLES,
        preference_runtime=teams_spy,
    )
    eligible_expected = sum(
        bool(item.get("expected", {}).get("teams_eligible")) for item in observed
    )
    check(
        "Teams selector calls the memory runtime for every eligible candidate",
        len(teams_spy.calls) == eligible_expected
        and teams_spy.calls
        and all(product == editorial_memory.PRODUCT_TEAMS for product, _ in teams_spy.calls)
        and teams_audit["editorial_memory_invoked"] is True,
        f"calls={len(teams_spy.calls)} expected={eligible_expected}",
    )
    called_titles = {str(article.get("title") or "") for _, article in teams_spy.calls}
    rejected_titles = {
        str(item.get("title") or "")
        for item in observed
        if not item.get("expected", {}).get("teams_eligible")
    }
    check(
        "Teams hard gates are never bypassed or presented to memory",
        not (called_titles & rejected_titles)
        and len(teams_selected) == eligible_expected,
    )
    check(
        "Teams candidates expose the complete memory audit contract",
        all(
            candidate.editorial_memory_profile == "spy-profile-v1"
            and isinstance(candidate.approved_precedent_ids, tuple)
            and isinstance(candidate.rejected_precedent_ids, tuple)
            and isinstance(candidate.near_miss_precedent_ids, tuple)
            and isinstance(candidate.silver_precedent_ids, tuple)
            and isinstance(candidate.memory_preference_adjustment, float)
            and candidate.memory_rank_before > 0
            and candidate.memory_rank_after > 0
            for candidate in teams_selected
        ),
    )

    # Daily: inactive is byte-stable, still emits a would-change shadow.
    _daily_run_at, daily_rows = _brief_rows("daily", 8)
    neutral_daily = SpyRuntime()
    neutral_daily_audit = editorial_briefings.SelectionAuditCounters()
    daily_baseline = _normalize_with_spy(
        "daily", daily_rows, neutral_daily, neutral_daily_audit
    )
    shadow_daily = SpyRuntime(
        memory_active=False,
        adjustments=(0, 0, 0, 0, -1, -1, 1, 1),
    )
    shadow_daily_audit = editorial_briefings.SelectionAuditCounters()
    daily_inactive = _normalize_with_spy(
        "daily", daily_rows, shadow_daily, shadow_daily_audit
    )
    check(
        "Daily selector calls the explicit Daily product head",
        len(shadow_daily.calls) == shadow_daily_audit.qualified_candidates
        and all(product == editorial_memory.PRODUCT_DAILY for product, _ in shadow_daily.calls),
    )
    check(
        "inactive Daily runtime preserves deterministic selection byte-for-byte",
        [editorial_briefings.article_dict(item) for item in daily_baseline]
        == [editorial_briefings.article_dict(item) for item in daily_inactive],
    )
    check(
        "inactive Daily runtime emits hypothetical ranking and membership",
        shadow_daily_audit.memory_runtime_invoked
        and shadow_daily_audit.memory_shadow_only
        and shadow_daily_audit.deterministic_selected_ids
        and shadow_daily_audit.memory_shadow_selected_ids
        and shadow_daily_audit.selection_changed_by_memory,
        shadow_daily_audit.manifest_fields(),
    )
    active_daily = SpyRuntime(
        memory_active=True,
        adjustments=(0, 0, 0, 0, -1, -1, 1, 1),
    )
    active_daily_audit = editorial_briefings.SelectionAuditCounters()
    daily_active = _normalize_with_spy(
        "daily", daily_rows, active_daily, active_daily_audit
    )
    check(
        "active fixture changes only qualified Daily ranking",
        [item.title for item in daily_active] != [item.title for item in daily_baseline]
        and {item.title for item in daily_active}
        <= {str(item["title"]) for item in daily_rows}
        and active_daily_audit.ai_central_qualified_count
        == active_daily_audit.qualified_candidates,
    )

    # Weekly uses its own broad head and can keep strategic non-urgent AI topics.
    _weekly_run_at, weekly_rows = _brief_rows("weekly", 12)
    weekly_spy = SpyRuntime()
    weekly_audit = editorial_briefings.SelectionAuditCounters()
    weekly_selected = _normalize_with_spy(
        "weekly", weekly_rows, weekly_spy, weekly_audit
    )
    check(
        "Weekly selector calls the distinct Weekly product head",
        len(weekly_spy.calls) == weekly_audit.qualified_candidates
        and all(product == editorial_memory.PRODUCT_WEEKLY for product, _ in weekly_spy.calls),
    )
    strategic = {
        "id": "weekly-strategic-nonurgent",
        "title": "AI 안경 커닝 진화에 대학가 시험 평가 제도 개편",
        "source": "연합뉴스",
        "published_at": editorial_briefings.weekly_coverage(
            datetime.fromisoformat("2026-07-27T07:00:00+09:00")
        ).end.isoformat(),
        "url": "https://www.yna.co.kr/view/WEEKLYSTRATEGIC",
        "snippet": (
            "AI 안경을 활용한 커닝 수법이 확산돼 대학 평가 제도 개편이 논의됐다. "
            "교육과 전문 거버넌스의 장기 대응이 필요한 구조적 변화다."
        ),
        "source_metadata": {
            "provider": "offline_fixture",
            "query": "AI 데이터센터",
        },
        "publisher_direct": True,
        "current_run_seen": True,
        "score": 1.0,
        "shadow_urgency_status": "none",
        "shadow_confirmed_event_types": [],
    }
    strategic_spy = SpyRuntime()
    strategic_audit = editorial_briefings.SelectionAuditCounters()
    strategic_weekly = _normalize_with_spy(
        "weekly", [strategic], strategic_spy, strategic_audit
    )
    strategic_teams = teams_ai_push.evaluate_teams_push_policy(strategic)
    check(
        "Weekly selects strategic non-urgent Gold-like evidence without Teams urgency",
        len(strategic_weekly) == 1 and not strategic_teams.eligible,
        strategic_teams.rejection_reason,
    )
    check(
        "Daily and Weekly manifests expose observable memory invocation",
        shadow_daily_audit.manifest_fields()["memory_runtime_invoked"] is True
        and weekly_audit.manifest_fields()["memory_runtime_invoked"] is True
        and shadow_daily_audit.manifest_fields()["deterministic_selected_ids"]
        and weekly_audit.manifest_fields()["memory_shadow_selected_ids"],
    )

    # Evidence semantics: Gold > Silver; Hard-negative bounded; Near-miss non-terminal.
    query = {
        "title": "정부 공공부문 AI 전환 정책 시행",
        "snippet": "정부 공공부문 AI 전환 정책 시행",
    }
    gold = _single_level_runtime(editorial_memory.EVIDENCE_GOLD_PLUS).decide(
        editorial_memory.PRODUCT_WEEKLY, query
    )
    silver = _single_level_runtime(editorial_memory.EVIDENCE_SILVER).decide(
        editorial_memory.PRODUCT_WEEKLY, query
    )
    hard = _single_level_runtime(editorial_memory.EVIDENCE_HARD_NEGATIVE).decide(
        editorial_memory.PRODUCT_WEEKLY, query
    )
    near = _single_level_runtime(editorial_memory.EVIDENCE_NEAR_MISS).decide(
        editorial_memory.PRODUCT_WEEKLY, query
    )
    check(
        "Gold evidence is stronger than Silver evidence",
        gold.preference_adjustment > silver.preference_adjustment > 0,
    )
    check(
        "Hard-negative evidence causes bounded suppression",
        -editorial_preference_runtime.MAX_PREFERENCE_ADJUSTMENT
        <= hard.preference_adjustment
        < 0
        and hard.recommendation == editorial_preference_runtime.RECOMMEND_AVOID,
    )
    check(
        "Near-miss evidence is advisory and non-terminal",
        near.preference_adjustment < 0
        and near.recommendation == editorial_preference_runtime.RECOMMEND_NEUTRAL
        and near.near_miss_precedents,
    )
    committed_runtime = editorial_preference_runtime.load_runtime(env={})
    product_article = {
        "title": "AI 안경 커닝과 대학 평가 제도 개편",
        "snippet": "AI 안경 커닝 확산으로 대학 교육과 평가 제도 개편이 논의됐다.",
    }
    product_decisions = [
        committed_runtime.decide(product, product_article)
        for product in editorial_memory.PRODUCTS
    ]
    check(
        "the same candidate receives distinct Teams, Daily, and Weekly decisions",
        {item.product for item in product_decisions}
        == set(editorial_memory.PRODUCTS)
        and len({item.preference_score for item in product_decisions}) > 1,
        [item.preference_score for item in product_decisions],
    )

    # Hermes remains default-off and fail-closed to deterministic local retrieval.
    corpus = editorial_memory.load_corpus()
    disabled_calls: list[dict] = []
    disabled_runtime = editorial_preference_runtime.load_runtime(
        env={"HERMES_EDITORIAL_MEMORY_ENABLED": "0"},
        transport=lambda request: disabled_calls.append(dict(request)) or {},
    )
    disabled_decision = disabled_runtime.decide(
        editorial_memory.PRODUCT_DAILY, product_article
    )
    check(
        "Hermes disabled causes zero transport calls and local retrieval",
        disabled_calls == []
        and disabled_decision.local_retrieval_used
        and not disabled_decision.hermes_retrieval_used,
    )
    known_id = corpus.records[0].article_id
    valid_requests: list[dict] = []
    fake = hermes_adapter.HermesEditorialMemoryAdapter(
        enabled=True,
        transport=lambda request: (
            valid_requests.append(dict(request))
            or {"matched_article_ids": [known_id]}
        ),
    )
    valid_result = fake.retrieve(
        product_article, corpus, query_label=editorial_memory.PRODUCT_DAILY
    )
    check(
        "fake Hermes transmits sanitized features and validated known IDs only",
        valid_result.mode == "hermes"
        and valid_requests
        and "title" not in valid_requests[0]
        and "summary" not in valid_requests[0]
        and valid_requests[0]["query_labels"] == [editorial_memory.PRODUCT_DAILY]
        and isinstance(valid_requests[0]["query_features"], dict),
    )
    unknown_result = hermes_adapter.HermesEditorialMemoryAdapter(
        enabled=True,
        transport=lambda _request: {
            "matched_article_ids": [known_id, "unknown-precedent-id"]
        },
    ).retrieve(product_article, corpus)
    malformed_result = hermes_adapter.HermesEditorialMemoryAdapter(
        enabled=True,
        transport=lambda _request: {"matched_article_ids": "malformed"},
    ).retrieve(product_article, corpus)
    check(
        "unknown or malformed Hermes IDs fail closed to local retrieval",
        unknown_result.mode == "local_fallback"
        and malformed_result.mode == "local_fallback"
        and unknown_result.result.mode == "local"
        and malformed_result.result.mode == "local",
    )

    # Approved feedback creates only a proposal; reviewed ingestion is append-only.
    bundle_candidates = [
        editorial_review.article_to_candidate(article, ai_rank=index)
        for index, article in enumerate(daily_baseline, start=1)
    ]
    selected_items = [dict(item) for item in bundle_candidates[:2]]
    selected_items[0]["title"] += " — 편집"
    selected_items[0]["summary_html"] = "편집된 요약 문장."
    selected_items[0]["implication_html"] = "경영진 검토가 필요한 변화다."
    review = {
        "selected_items": selected_items,
        "approved_at": "2026-08-04T13:00:00+09:00",
        "ratings": {"overall": 5},
        "excluded_items": [
            {"candidate_id": bundle_candidates[2]["candidate_id"], "tags": ["중복"]}
        ],
    }
    bundle = {
        "edition_key": "daily-2026-07-27",
        "candidates": bundle_candidates,
    }
    proposal = editorial_review.build_feedback_proposal(
        bundle,
        review,
        daily_baseline[:2],
        review_mode="human_approved",
        generated_at="2026-08-04T13:01:00+09:00",
    )
    check(
        "editorial approval produces a complete feedback proposal only",
        proposal["candidate_ids"]
        and proposal["final_selected_ids"]
        and proposal["excluded_ids"]
        and proposal["title_edits"]
        and proposal["summary_edits"]
        and proposal["executive_implication_edits"]
        and proposal["corpus_mutation"].startswith("none"),
    )
    with tempfile.TemporaryDirectory(prefix="editorial-feedback-ingest-") as tmp:
        tmp_root = Path(tmp)
        corpus_root = tmp_root / "corpus"
        corpus_root.mkdir()
        shutil.copy2(editorial_memory.DEFAULT_CORPUS_ROOT / "schema.json", corpus_root / "schema.json")
        shutil.copy2(
            editorial_memory.DEFAULT_CORPUS_ROOT / "decisions.jsonl",
            corpus_root / "decisions.jsonl",
        )
        proposal_path = tmp_root / "proposal.json"
        proposal_path.write_text(
            json.dumps(proposal, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        before = (corpus_root / "decisions.jsonl").read_bytes()
        dry = ingest_editorial_feedback.ingest_proposal(
            proposal_path, corpus_root=corpus_root, write=False, env={}
        )
        check(
            "dry-run feedback ingestion changes no corpus file",
            not dry["written"]
            and (corpus_root / "decisions.jsonl").read_bytes() == before,
        )
        written = ingest_editorial_feedback.ingest_proposal(
            proposal_path, corpus_root=corpus_root, write=True, env={}
        )
        after_first = (corpus_root / "decisions.jsonl").read_bytes()
        check(
            "explicit temp-corpus ingestion appends exactly one record",
            written["written"]
            and after_first.startswith(before)
            and written["result_record_count"]
            == written["previous_record_count"] + 1,
        )
        correction = dict(proposal)
        correction["proposed_decision_id"] = proposal["proposed_decision_id"] + "-correction"
        correction["supersedes"] = proposal["proposed_decision_id"]
        correction["prior_proposal_id"] = proposal["proposed_decision_id"]
        correction_path = tmp_root / "correction.json"
        correction_path.write_text(
            json.dumps(correction, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        correction_result = ingest_editorial_feedback.ingest_proposal(
            correction_path, corpus_root=corpus_root, write=True, env={}
        )
        after_correction = (corpus_root / "decisions.jsonl").read_bytes()
        records = [
            json.loads(line)
            for line in after_correction.decode("utf-8").splitlines()
            if line.strip()
        ]
        check(
            "correction appends a superseding record without mutating history",
            after_correction.startswith(after_first)
            and correction_result["record"]["record_type"] == "supersede"
            and correction_result["record"]["supersedes"]
            == proposal["proposed_decision_id"]
            and records[-2]["decision_id"] == proposal["proposed_decision_id"]
            and records[-1]["supersedes"] == records[-2]["decision_id"],
        )

    print()
    print(
        "EDITORIAL_MEMORY_RUNTIME_INTEGRATION="
        f"{'PASS' if not FAILURES else 'FAIL'} checks={PASSES} "
        f"failures={len(FAILURES)}"
    )
    print(
        "COUNTERS network=0 smtp=0 teams=0 telegram=0 "
        "production_state_writes=0 hermes_live_calls=0 hermes_live_writes=0"
    )
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    raise SystemExit(main())
