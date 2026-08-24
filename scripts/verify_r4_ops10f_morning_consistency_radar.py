#!/usr/bin/env python3
"""Focused offline verifier for R4-OPS-10F Morning consistency/radar.

No collector, network, production-state writer, workflow, or delivery path is
invoked.  The one browser assertion delegates to the existing 10E real-Chrome
acceptance verifier so the inherited authentication and editing contract is
tested, rather than approximated with static HTML assertions.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
for path in (ROOT, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from app import (  # noqa: E402
    editorial_briefing_state,
    editorial_briefings as brief,
    editorial_radar,
    executive_materiality,
    teams_push_state,
    watch_semantic_precision,
)
import verify_publisher_direct_collector as publisher_pin_verifier  # noqa: E402
import run_editorial_briefing as briefing_runner  # noqa: E402


class Verifier:
    def __init__(self) -> None:
        self.checks = 0
        self.failures: list[str] = []

    def check(self, label: str, condition: object, detail: object = "") -> None:
        self.checks += 1
        if condition:
            print(f"PASS: {label}")
            return
        self.failures.append(label)
        suffix = f" — {str(detail)[:800]}" if detail else ""
        print(f"FAIL: {label}{suffix}")


RUN_AT = datetime.fromisoformat("2026-08-24T08:02:00+09:00")
SNAPSHOT_AT = datetime.fromisoformat("2026-08-24T07:45:00+09:00")
FINALIZATION_AT = datetime.fromisoformat("2026-08-24T08:02:00+09:00")
DELIVERED_AT = datetime.fromisoformat("2026-08-24T08:03:00+09:00")
FINAL_PRE_SEND_AT = datetime.fromisoformat("2026-08-24T08:05:00+09:00")

PRODUCTION_PATHS = (
    ROOT / "data" / "teams_push_state.json",
    ROOT / "data" / "editor_delivery_state.json",
    ROOT / "data" / "editorial_daily_state.json",
    ROOT / "docs" / "editorial" / "daily" / "2026-08-24.html",
)

GS_TITLE = "시공 10년 내공 쌓은 GS건설, AI 데이터센터 영토 넓힌다"
GS_URL = "https://hankookilbo.com/news/article/A2026081914130005460"


def _collected_row(index: int) -> dict:
    ai_row = index < 5
    provider = "naver" if index < 208 else "google_news"
    raw = {
        "article_id": f"collection-{index + 1}",
        "title": (
            f"생성형 AI 활용 동향 검토 {index + 1}"
            if ai_row
            else f"일반 산업 수집 기사 {index + 1}"
        ),
        "source": "연합뉴스" if ai_row else "검증매체",
        "url": f"https://news.example.com/radar/{index + 1}",
        "published_at": "2026-08-24T06:10:00+09:00",
        "snippet": (
            "생성형 AI의 일반 활용 가능성을 소개했으나 구체적 사업·프로젝트 결정은 없다."
            if ai_row
            else "산업 일반 소식이다."
        ),
        "source_metadata": {"provider": provider},
        # These deliberately prove that the allowlist does not persist bodies.
        "body": "COPYRIGHTED FULL BODY MUST NOT PERSIST",
        "content": "COPYRIGHTED FULL CONTENT MUST NOT PERSIST",
        "raw_html": "<article>FULL HTML MUST NOT PERSIST</article>",
    }
    row = editorial_radar.lightweight_row(raw, sequence=index)
    if ai_row:
        editorial_radar.set_stage(
            row,
            editorial_radar.STAGE_AI_CENTRAL,
            qualification_reason="ai_central_but_not_executive_material",
            rejection_reason="concrete_executive_or_project_consequence_not_proven",
        )
    else:
        editorial_radar.set_stage(
            row,
            editorial_radar.STAGE_EXCLUDED,
            rejection_reason="ai_centrality_not_proven",
        )
    return row


def _collection_audit() -> dict:
    rows = [_collected_row(index) for index in range(496)]
    return editorial_radar.build_audit(
        rows,
        collection_audit={
            "naver_articles_collected": 208,
            "google_news_articles_collected": 288,
        },
        selection_audit={
            "ai_central_qualified_count": 5,
            "executive_qualified_count": 0,
        },
        selected_count=0,
    )


def _watch_state(
    *,
    title: str = GS_TITLE,
    url: str = GS_URL,
    sent_at: str = "2026-08-24T07:59:00+09:00",
    published_at: str = "2026-08-19T14:13:00+09:00",
    article_id: str = "gs-ai-datacenter",
) -> tuple[dict, dict]:
    article = {
        "article_id": article_id,
        "title": title,
        "source": "한국일보" if article_id.startswith("gs-") else "연합뉴스",
        "url": url,
        "published_at": published_at,
        "first_seen_at": "2026-08-24T07:58:00+09:00",
        "first_material_discovery_at": "2026-08-24T07:59:00+09:00",
    }
    state = teams_push_state.mark_sent_after_success(
        teams_push_state.empty_state(),
        article,
        cluster_key=f"bridge:{article_id}",
        signature=f"signature:{article_id}",
        importance="important",
        source=article["source"],
        send_succeeded=True,
        sent_at=sent_at,
        delivery_id=f"teams_ai_push:{article_id}",
    )
    return state, article


def _bridge(state: dict, *, delivered_at: datetime | None = None) -> dict:
    coverage = brief.daily_coverage(RUN_AT)
    return editorial_radar.watch_bridge(
        state,
        snapshot_at=SNAPSHOT_AT,
        finalization_at=(
            datetime.fromisoformat("2026-08-24T08:20:00+09:00")
            if delivered_at is not None
            else FINALIZATION_AT
        ),
        coverage_start=coverage.start,
        coverage_end=coverage.end,
        delivered_at=delivered_at,
    )


def _normal_articles() -> list[brief.EditorialArticle]:
    return brief.normalize_articles(
        brief.fixture_articles("daily", RUN_AT),
        brief.daily_coverage(RUN_AT),
        limit=2,
        resolve_images=False,
        selection_mode=brief.SELECTION_MODE_EDITORIAL_PRIORITY,
        edition_type="daily",
    )


def _test_collection_and_ui(verifier: Verifier, audit: dict) -> None:
    funnel = audit["funnel"]
    verifier.check(
        "2026-08-24 collection funnel distinguishes collection from selection",
        funnel == {
            "collection_count": 496,
            "normalized_row_count": 496,
            "ai_central_count": 5,
            "executive_candidate_count": 0,
            "selected_count": 0,
            "watch_bridge_count": 0,
            "late_watch_count": 0,
        },
        funnel,
    )
    verifier.check(
        "provider collection counts remain truthful",
        audit["provider_counts"]["naver"] == 208
        and audit["provider_counts"]["google_news"] == 288,
        audit["provider_counts"],
    )
    verifier.check(
        "all lightweight radar rows and inspectable reasons are retained",
        len(audit["rows"]) == 496
        and all(row.get("stage") and row.get("rejection_reason") for row in audit["rows"]),
    )
    forbidden = {"body", "content", "raw_html", "html", "full_text"}
    verifier.check(
        "radar artifact never duplicates full article bodies",
        audit["row_body_fields_persisted"] is False
        and all(not (forbidden & set(row)) for row in audit["rows"]),
    )
    template = (ROOT / "templates" / "editorial_review_console.html").read_text(
        encoding="utf-8"
    )
    verifier.check(
        "Editor radar UI is bounded, searchable, filtered, and paginated",
        audit["dom_page_size"] == 50
        and "RADAR_PAGE_SIZE" in template
        and ".slice(offset,offset+RADAR_PAGE_SIZE)" in template
        and 'id="radarSearch"' in template
        and 'id="radarFilter"' in template,
    )
    verifier.check(
        "radar promotion is an explicit operator action, not auto-selection",
        'data-radar-promote' in template
        and "promoteRadarRow" in template
        and "state.selected.push(id)" in template,
    )


def _test_daily_design(verifier: Verifier, audit: dict) -> None:
    empty = brief.render_daily(
        [], run_at=RUN_AT, root_url="https://daily.fixture.test", radar_audit=audit
    )
    brief.validate_rendered(empty)
    verifier.check(
        "zero-selection Daily uses the branded headline Hero and attached summary",
        'class="hero empty-edition"' in empty.html
        and 'data-edition-status="empty"' in empty.html
        and "Editor's Summary" in empty.html
        and "기준을 낮추거나 기사를 채워 넣지 않았습니다." in empty.html,
    )
    headline_shell = empty.html.split("오늘의 브리핑", 1)[0]
    verifier.check(
        "empty Hero fabricates neither article nor image",
        "<img" not in headline_shell
        and empty.article_count == 0
        and empty.edition_manifest["articles"] == [],
    )
    verifier.check(
        "public Daily exposes the collection funnel without dumping rows",
        "수집 레이더" in empty.html
        and ">496<" in empty.html
        and ">5<" in empty.html
        and audit["rows"][10]["title"] not in empty.html,
    )

    articles = _normal_articles()
    selected_audit = editorial_radar.normalize_audit(audit, selected_count=len(articles))
    normal = brief.render_daily(
        articles,
        run_at=RUN_AT,
        root_url="https://daily.fixture.test",
        radar_audit=selected_audit,
    )
    brief.validate_rendered(normal)
    verifier.check(
        "non-empty Daily uses Hero, Editor's Summary, source, briefing, and radar",
        'data-role="headline"' in normal.html
        and "Editor's Summary" in normal.html
        and "출처" in normal.html
        and 'data-role="article-card"' in normal.html
        and 'data-role="radar-scan"' in normal.html,
    )
    verifier.check(
        "headline identity is not duplicated in briefing cards",
        normal.html.count(f"<h2 style=\"position:relative;z-index:2;margin:0;font-size:31px") == 1
        and articles[0].title not in normal.html.split('data-role="article-card"', 1)[-1],
    )
    verifier.check(
        "Editor and publication share Daily semantic hierarchy",
        all(
            token in (ROOT / "templates" / "editorial_review_console.html").read_text(encoding="utf-8")
            and token in normal.html
            for token in (
                "AI 경영 T&amp;I",
                "Daily Brief",
                "오늘의 헤드라인",
                "Editor's Summary",
                "오늘의 브리핑",
                "수집 레이더",
                "정보 분류 기준",
            )
        ),
    )
    verifier.check(
        "publication manifest uses the same selected/radar truth",
        normal.edition_manifest["headline_title"] == articles[0].title
        and [row["publisher_url"] for row in normal.edition_manifest["articles"]]
        == [article.selected_url for article in articles]
        and normal.edition_manifest["radar_audit"]["funnel"]
        == selected_audit["funnel"],
    )


def _test_bridge(verifier: Verifier, audit: dict) -> None:
    state, article = _watch_state()
    state_before = copy.deepcopy(state)
    bridge = _bridge(state)
    verifier.check(
        "07:59 important Watch item is observable before 08:02 finalization",
        bridge["bridge_count"] == 1
        and bridge["observable_count"] == 1
        and bridge["delivery_side_effects"] == 0,
        bridge,
    )
    row = bridge["bridge_rows"][0]
    verifier.check(
        "old publication and new morning discovery remain distinct",
        row["published_at"].startswith("2026-08-19")
        and row["first_seen_at"].startswith("2026-08-24")
        and row["watch_sent_at"].startswith("2026-08-24T07:59")
        and row["temporal_distinction"] == "old_publication_new_morning_discovery"
        and row["daily_disposition"] == "morning_radar_only",
        row,
    )
    merged = editorial_radar.merge_watch_bridge(audit, bridge)
    bridge_daily = brief.render_daily(
        [], run_at=RUN_AT, root_url="https://daily.fixture.test", radar_audit=merged
    )
    brief.validate_rendered(bridge_daily)
    verifier.check(
        "empty Daily qualifies its status when a morning Watch signal exists",
        "오전 레이더 추가 포착 1건" in bridge_daily.html
        and "최종 선정 0건 · 오전 레이더 추가 포착 1건" in bridge_daily.teams_text
        and merged["morning_truth_absolute_empty_allowed"] is False,
    )
    verifier.check(
        "Morning Bridge reads Watch state without mutating or redelivering it",
        state == state_before and bridge["delivery_side_effects"] == 0,
    )

    late_state, _ = _watch_state(
        sent_at="2026-08-24T08:10:00+09:00",
        published_at="2026-08-24T06:20:00+09:00",
        article_id="post-daily-watch",
        title="정부, AI 데이터센터 전력망 확충 프로젝트 착공",
        url="https://www.yna.co.kr/view/AKR20260824001000003",
    )
    late_before = copy.deepcopy(late_state)
    late = _bridge(late_state, delivered_at=DELIVERED_AT)
    verifier.check(
        "08:10 Watch after delivered Daily is future-cycle only with no resend",
        late["bridge_count"] == 0
        and late["late_count"] == 1
        and late["late_rows"][0]["daily_disposition"] == "future_cycle_only"
        and late["late_rows"][0]["rejection_reason"]
        == "watch_sent_after_daily_delivery_no_retroactive_mutation"
        and late["delivery_side_effects"] == 0
        and late_state == late_before,
        late,
    )

    duplicate = teams_push_state.evaluate_dedup(
        state,
        article,
        cluster_key="bridge:gs-ai-datacenter",
        signature="signature:gs-ai-datacenter",
        is_material_update=False,
    )
    verifier.check(
        "Watch at-most-once identity and first_sent semantics remain authoritative",
        duplicate.send_allowed is False
        and state["article_ids"][article["article_id"]]["first_sent_at"]
        == "2026-08-24T07:59:00+09:00",
        duplicate,
    )

    # Exercise the actual read-only gate at both freshness boundaries. The
    # immutable edition manifest and runtime manifest carry one exact bridge
    # identity. A later IMPORTANT Watch delivery must fail the final gate.
    with tempfile.TemporaryDirectory(prefix="r4-ops10f-freshness-") as temporary:
        root = Path(temporary)
        state_path = root / "watch-state.json"
        racing_state_path = root / "watch-state-racing.json"
        bundle_path = root / "candidates.json"
        runtime_path = root / "runtime-manifest.json"
        edition_manifest_path = root / "immutable-edition.json"
        teams_push_state.save_state(state, state_path)
        bundle_path.write_text(
            json.dumps({"candidates": []}, ensure_ascii=False), encoding="utf-8"
        )
        fresh_radar = copy.deepcopy(merged)
        fresh_radar["bridge_window"] = {
            "snapshot_at": SNAPSHOT_AT.isoformat(),
            "finalization_at": FINALIZATION_AT.isoformat(),
            "watch_delivery_ids": bridge["bridge_delivery_ids"],
        }
        gate_daily = brief.render_daily(
            [],
            run_at=RUN_AT,
            root_url="https://daily.fixture.test",
            radar_audit=fresh_radar,
        )
        runtime = brief.manifest_for_runtime(
            gate_daily,
            root / "2026-08-24.html",
            root / "latest.html",
        )
        runtime_path.write_text(
            json.dumps(runtime, ensure_ascii=False), encoding="utf-8"
        )
        edition_manifest_path.write_text(
            json.dumps(gate_daily.edition_manifest, ensure_ascii=False),
            encoding="utf-8",
        )
        command = [
            sys.executable,
            str(ROOT / "scripts/verify_daily_morning_bridge_freshness.py"),
            "--runtime-manifest",
            str(runtime_path),
            "--edition-manifest",
            str(edition_manifest_path),
            "--review-bundle",
            str(bundle_path),
            "--watch-state",
            str(state_path),
            "--checked-at",
            FINALIZATION_AT.isoformat(),
        ]
        early_fresh = subprocess.run(
            command, cwd=ROOT, capture_output=True, text=True, timeout=30, check=False
        )
        stable_final = subprocess.run(
            [
                *command[:-1],
                FINAL_PRE_SEND_AT.isoformat(),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

        racing_article = {
            "article_id": "post-early-important-watch",
            "title": "정부, AI 데이터센터 전력망 확충 사업 최종 승인",
            "source": "연합뉴스",
            "url": "https://www.yna.co.kr/view/AKR20260824001100003",
            "published_at": "2026-08-24T06:20:00+09:00",
            "first_seen_at": "2026-08-24T08:03:30+09:00",
            "first_material_discovery_at": "2026-08-24T08:04:00+09:00",
        }
        racing_state = teams_push_state.mark_sent_after_success(
            state,
            racing_article,
            cluster_key="bridge:post-early-important-watch",
            signature="signature:post-early-important-watch",
            importance="important",
            source=racing_article["source"],
            send_succeeded=True,
            sent_at="2026-08-24T08:04:00+09:00",
            delivery_id="teams_ai_push:post-early-important-watch",
        )
        teams_push_state.save_state(racing_state, racing_state_path)
        stale_command = [
            racing_state_path.as_posix() if value == str(state_path) else value
            for value in command
        ]
        stale_command[-1] = FINAL_PRE_SEND_AT.isoformat()
        gate_inputs = (
            runtime_path,
            edition_manifest_path,
            bundle_path,
            racing_state_path,
        )
        input_hashes = {
            path: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in gate_inputs
        }
        stale_final = subprocess.run(
            stale_command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        inputs_unchanged = all(
            hashlib.sha256(path.read_bytes()).hexdigest() == digest
            for path, digest in input_hashes.items()
        )

        identity = {
            "edition_key": runtime["edition_key"],
            "coverage_start": runtime["coverage_start"],
            "coverage_end": runtime["coverage_end"],
            "html_sha256": runtime["html_sha256"],
            "public_url": runtime["public_dated_url"],
            "delivery_kind": "empty_status",
            "article_count": 0,
        }
        claim_owner = "github-run:9001:attempt:1"
        claimed = editorial_briefing_state.add_claim(
            editorial_briefing_state.empty_state("daily"),
            "daily",
            {
                **identity,
                "claim_owner": claim_owner,
                "claimed_at": "2026-08-24T08:03:00+09:00",
            },
        )
        wrong_identity_rejected = False
        try:
            editorial_briefing_state.release_unaccepted_claim(
                claimed,
                "daily",
                runtime["edition_key"],
                claim_owner,
                identity={**identity, "html_sha256": "f" * 64},
            )
        except editorial_briefing_state.StateError:
            wrong_identity_rejected = True
        claim_state_path = root / "daily-claim-state.json"
        editorial_briefing_state.atomic_write_state(
            "daily", claimed, path=claim_state_path
        )
        production_env = {
            "EDITORIAL_PRODUCTION": "1",
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_RUN_ID": "9001",
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_OUTPUT": str(root / "github-output.txt"),
        }
        previous_env = {name: os.environ.get(name) for name in production_env}
        try:
            os.environ.update(production_env)
            reconciled = briefing_runner.run_reconcile_unsent_claim(
                "daily",
                run_at=RUN_AT,
                runtime_dir=root,
                state_path=claim_state_path,
            )
        finally:
            for name, value in previous_env.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
        rebuilt_identity = {
            **identity,
            "html_sha256": "b" * 64,
            "public_url": identity["public_url"] + "?revision=rebuilt",
        }
        retry_claimed = editorial_briefing_state.add_claim(
            reconciled,
            "daily",
            {
                **rebuilt_identity,
                "claim_owner": "github-run:9002:attempt:1",
                "claimed_at": "2026-08-24T08:07:00+09:00",
            },
        )
        sent_daily = editorial_briefing_state.convert_claim_to_success(
            claimed,
            "daily",
            {
                **identity,
                "smtp_status": "accepted",
                "smtp_code": 250,
                "sent_at": DELIVERED_AT.isoformat(),
            },
            claim_owner,
        )
        sent_daily_reclaim_rejected = False
        try:
            editorial_briefing_state.add_claim(
                sent_daily,
                "daily",
                {
                    **identity,
                    "claim_owner": "github-run:9003:attempt:1",
                    "claimed_at": "2026-08-24T08:11:00+09:00",
                },
            )
        except editorial_briefing_state.StateError:
            sent_daily_reclaim_rejected = True
    verifier.check(
        "early and final gates accept an immutable complete Watch bridge",
        early_fresh.returncode == 0
        and stable_final.returncode == 0
        and "RESULT=PASS_MORNING_WATCH_BRIDGE_FRESH" in early_fresh.stdout
        and "RESULT=PASS_MORNING_WATCH_BRIDGE_FRESH" in stable_final.stdout,
        {"early": early_fresh.stdout, "final": stable_final.stdout},
    )
    verifier.check(
        "new IMPORTANT Watch delivery after early PASS fails final pre-send gate",
        stale_final.returncode == 1
        and "teams_ai_push:post-early-important-watch" in stale_final.stdout
        and "RESULT=FAIL_MORNING_WATCH_BRIDGE_STALE" in stale_final.stdout,
        stale_final.stdout,
    )
    verifier.check(
        "stale final gate performs zero sends and zero production-state writes",
        "network_sends=0 smtp_attempts=0 teams_sends=0 production_state_writes=0"
        in stale_final.stdout
        and inputs_unchanged,
        stale_final.stdout,
    )
    verifier.check(
        "stale exact claim is not successful and reconciles for an exact rebuild",
        not editorial_briefing_state.has_success(claimed, runtime["edition_key"])
        and editorial_briefing_state.has_claim(claimed, runtime["edition_key"])
        and wrong_identity_rejected
        and not editorial_briefing_state.has_claim(reconciled, runtime["edition_key"])
        and editorial_briefing_state.has_claim(
            retry_claimed, runtime["edition_key"]
        ),
    )
    verifier.check(
        "Watch after an already-sent Daily cannot create a Daily resend",
        late["late_count"] == 1
        and editorial_briefing_state.has_success(sent_daily, runtime["edition_key"])
        and not editorial_briefing_state.has_claim(sent_daily, runtime["edition_key"])
        and sent_daily_reclaim_rejected,
    )


def _test_calibration(verifier: Verifier) -> None:
    gs = {"title": GS_TITLE, "source": "한국일보", "url": GS_URL}
    gs_dimensions = editorial_radar.strategic_dimensions(gs)
    gs_materiality = executive_materiality.executive_qualification(gs)
    gs_watch = watch_semantic_precision.classify(gs)
    verifier.check(
        "GS construction/AI data-center case is a strong shared positive",
        gs_dimensions["ai_central"]
        and gs_dimensions["executive_materiality"]
        and gs_dimensions["hdec_strategic_relevance"] == 3
        and gs_dimensions["competitor_relevance"] == 2
        and gs_dimensions["infrastructure_project_specificity"] >= 1
        and gs_materiality.qualified
        and gs_watch.eligible,
        {"dimensions": gs_dimensions, "watch": gs_watch},
    )

    generic_policy = {
        "title": "150조 미래대응기금…청년·AI 인재·지방 투자",
        "source": "연합뉴스",
        "url": "https://www.yna.co.kr/view/AKR20260824000300003",
    }
    infra_policy = {
        "title": "정부, AI 데이터센터 전력망 확충에 20조 프로젝트금융 투입",
        "source": "연합뉴스",
        "url": "https://www.yna.co.kr/view/AKR20260824000100003",
    }
    generic_dimensions = editorial_radar.strategic_dimensions(generic_policy)
    infra_dimensions = editorial_radar.strategic_dimensions(infra_policy)
    verifier.check(
        "generic AI policy/fund mention is not automatically interruptive Watch",
        not watch_semantic_precision.classify(generic_policy).eligible,
    )
    verifier.check(
        "concrete AI infrastructure policy ranks above generic policy",
        infra_dimensions["hdec_strategic_relevance"]
        > generic_dimensions["hdec_strategic_relevance"]
        and infra_dimensions["infrastructure_project_specificity"]
        > generic_dimensions["infrastructure_project_specificity"]
        and infra_dimensions["capital_investment_consequence"]
        >= generic_dimensions["capital_investment_consequence"],
        {"generic": generic_dimensions, "infra": infra_dimensions},
    )

    generic_fab = {
        "title": "반도체 업황 회복 기대…대형주 투자전략 점검",
        "source": "매일경제",
        "url": "https://www.mk.co.kr/news/stock/fixture-2",
    }
    infra_fab = {
        "title": "AI 반도체 팹 산업단지 부지·전력·용수·EPC 30조 투자 확정",
        "source": "연합뉴스",
        "url": "https://www.yna.co.kr/view/AKR20260824000400003",
    }
    generic_fab_dimensions = editorial_radar.strategic_dimensions(generic_fab)
    infra_fab_dimensions = editorial_radar.strategic_dimensions(infra_fab)
    verifier.check(
        "fab power/water/site/EPC context outranks semiconductor commentary",
        infra_fab_dimensions["hdec_strategic_relevance"]
        > generic_fab_dimensions["hdec_strategic_relevance"]
        and infra_fab_dimensions["infrastructure_project_specificity"]
        > generic_fab_dimensions["infrastructure_project_specificity"]
        and "semiconductor_fab" in infra_fab_dimensions["relevant_dimensions"],
        {"generic": generic_fab_dimensions, "infra": infra_fab_dimensions},
    )


def _test_pin_governance(verifier: Verifier) -> None:
    exact = publisher_pin_verifier.EXPECTED_PROTECTED_SHA256
    mutable = publisher_pin_verifier.MUTABLE_PROTECTED_STATE_PATHS
    verifier.check(
        "protected immutable/code artifacts retain exact 64-character SHA256 pins",
        bool(exact)
        and all(
            len(expected) == 64
            and all(character in "0123456789abcdef" for character in expected)
            and publisher_pin_verifier.sha256(ROOT / path) == expected
            for path, expected in exact.items()
        ),
    )
    verifier.check(
        "mutable Daily production state uses strict schema governance, not whole-file pin",
        "data/editorial_daily_state.json" not in exact
        and mutable.get("data/editorial_daily_state.json") == "daily"
        and editorial_briefing_state.load_state(
            "daily", ROOT / "data" / "editorial_daily_state.json"
        )["edition_type"]
        == "daily",
    )
    verifier_source = (ROOT / "scripts/verify_publisher_direct_collector.py").read_text(
        encoding="utf-8"
    )
    verifier.check(
        "protected checks remain mandatory exact equality checks",
        "protected_before[path] == expected" in verifier_source
        and "continue-on-error" not in verifier_source
        and "startswith(expected)" not in verifier_source,
    )


def _test_delivery_and_auth(verifier: Verifier) -> None:
    workflow = (ROOT / ".github/workflows/editorial-daily-brief.yml").read_text(
        encoding="utf-8"
    )
    runner = (ROOT / "scripts/run_editorial_briefing.py").read_text(encoding="utf-8")
    verifier.check(
        "Daily schedule and once-per-edition claim/send contract are preserved",
        all(cron in workflow for cron in ('cron: "50 22 * * *"', 'cron: "5 23 * * *"', 'cron: "15 23 * * *"'))
        and "editorial_briefing_state.add_claim" in runner
        and "editorial_briefing_state.convert_claim_to_success" in runner
        and "require_claim_owner" in runner,
    )
    verifier.check(
        "early and final Watch gates bracket publication/claim before send",
        workflow.index("Verify no important Watch item crossed the finalization boundary")
        < workflow.index("Claim exact Daily edition after public verification")
        < workflow.index("Send claimed Daily edition after final Watch freshness check")
        and workflow.rindex("verify_daily_morning_bridge_freshness.py")
        < workflow.rindex("--send"),
    )
    verifier.check(
        "final pre-send gate reads fresh authoritative Watch state and immutable bridge identity",
        'git show origin/main:data/teams_push_state.json > "$FINAL_WATCH_STATE_PATH"'
        in workflow
        and '--edition-manifest "$EDITION_MANIFEST_PATH"' in workflow
        and workflow.count("verify_daily_morning_bridge_freshness.py") == 2
        and "continue-on-error" not in workflow,
    )
    verifier.check(
        "failed final gate reconciles only the unsent exact claim before retry",
        "--reconcile-unsent-claim" in workflow
        and "release_unaccepted_claim" in runner
        and workflow.index("Send claimed Daily edition after final Watch freshness check")
        < workflow.index("Reconcile unsent Daily claim after final freshness failure")
        and "steps.send.outputs.freshness_passed != 'true'" in workflow,
    )

    browser = subprocess.run(
        [sys.executable, str(ROOT / "scripts/verify_r4_ops10e_editor_presentation_parity.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    verifier.check(
        "real browser preserves default auth UX and 10E editing/presentation parity",
        browser.returncode == 0
        and "REAL_BROWSER_USED=true" in browser.stdout
        and "OPS10E_FOCUSED=PASS" in browser.stdout,
        browser.stdout[-1500:] + browser.stderr[-500:],
    )


def main() -> int:
    verifier = Verifier()
    production_before = {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in PRODUCTION_PATHS
    }
    audit = _collection_audit()
    _test_collection_and_ui(verifier, audit)
    _test_daily_design(verifier, audit)
    _test_bridge(verifier, audit)
    _test_calibration(verifier)
    _test_pin_governance(verifier)
    _test_delivery_and_auth(verifier)
    production_after = {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in PRODUCTION_PATHS
    }
    verifier.check(
        "focused repair verification preserves all production state/artifacts",
        production_after == production_before,
        {
            str(path.relative_to(ROOT)): (
                production_before[path], production_after[path]
            )
            for path in PRODUCTION_PATHS
            if production_before[path] != production_after[path]
        },
    )

    print(f"checks={verifier.checks} failures={len(verifier.failures)}")
    print("REAL_BROWSER_USED=true")
    print(
        "network_sends=0 smtp_attempts=0 teams_sends=0 "
        "production_state_writes=0"
    )
    print(
        "OPS10F_FOCUSED=" + ("PASS" if not verifier.failures else "FAIL")
    )
    if verifier.failures:
        print("FAILED_CHECKS=" + json.dumps(verifier.failures, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
