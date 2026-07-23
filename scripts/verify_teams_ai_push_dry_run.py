#!/usr/bin/env python3
"""Deterministic no-network verifier for D7-AK-5C dry-run integration."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.teams_ai_push import select_teams_push_from_artifact
from app.teams_push_state import (
    empty_state,
    mark_sent_after_success,
    save_state,
)

SCRIPT = REPO_ROOT / "scripts" / "prepare_teams_ai_push_dry_run.py"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "absent"


def _article(**overrides):
    base = {
        "article_key": "article-top",
        "title": "OpenAI와 Microsoft, AI 데이터센터 투자 계약 체결",
        "summary": "양사가 AI 데이터센터 투자 계약을 공식 체결했다.",
        "hdec_relevance": "데이터센터 EPC와 전력 인프라 사업 기회에 직접 영향",
        "source": "Reuters",
        "published_at": "2026-07-23T00:20:00+00:00",
        "url": "https://example.com/news/top?utm_source=test",
        "score": 4.7,
        "shadow_urgency_status": "confirmed",
        "shadow_would_pass": True,
        "shadow_confirmed_event_types": ["investment_confirmed"],
        "change_type": "new_article",
    }
    base.update(overrides)
    return base


def _payload():
    return {
        "schema_version": 1,
        "source": "live-delta",
        "shadow_alert_delta": True,
        "generated_at": "2026-07-23T09:31:00+09:00",
        "generated_kst": "2026-07-23 09:31",
        "articles": [
            _article(),
            _article(
                article_key="article-important",
                title="BIM 기반 설계 자동화 솔루션 정식 출시",
                summary="건설 BIM 자동화 제품이 정식 출시됐다.",
                source="전자신문",
                url="https://example.com/news/bim",
                score=3.6,
                shadow_confirmed_event_types=["product_available"],
            ),
            _article(
                article_key="article-stock",
                title="AI 데이터센터 관련주 급등, 목표주가 상향",
                url="https://example.com/news/stock",
            ),
            _article(
                article_key="article-ambiguous",
                title="AI 전력망 투자가 늘어날 전망",
                summary="향후 확대될 가능성이 제기됐다.",
                url="https://example.com/news/forecast",
                shadow_urgency_status="ambiguous",
                shadow_would_pass=False,
                shadow_confirmed_event_types=[],
            ),
        ],
    }


def _run(artifact: Path, state: Path, output: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    # A configured secret must not affect this script; remove it from the verifier
    # environment so accidental future consumption becomes visible in code review.
    env.pop("TEAMS_WORKFLOW_WEBHOOK_URL", None)
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--artifact",
            str(artifact),
            "--state",
            str(state),
            "--output-dir",
            str(output),
            "--dashboard-url",
            "https://example.com/dashboard",
            "--report-url",
            "https://example.com/report",
        ],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="hdec-ak5c-") as tmp_raw:
        tmp = Path(tmp_raw)
        artifact = tmp / "dashboard_delta.json"
        state = tmp / "teams_push_state.json"
        artifact.write_text(
            json.dumps(_payload(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        artifact_before = _sha(artifact)

        output1 = tmp / "out-1"
        first = _run(artifact, state, output1)
        assert first.returncode == 0, first.stderr
        assert "RESULT=D7-AK-5C_TEAMS_AI_PUSH_DRY_RUN_COMPLETE" in first.stdout
        manifest1 = json.loads((output1 / "manifest.json").read_text(encoding="utf-8"))
        assert manifest1["candidate_count_before_dedup"] == 2
        assert manifest1["card_count"] == 2
        assert manifest1["dedup_blocked_count"] == 0
        assert manifest1["safety"] == {
            "send_count": 0,
            "webhook_calls": 0,
            "smtp_connections": 0,
            "state_write": False,
            "artifact_write": False,
        }
        assert not state.exists()
        assert _sha(artifact) == artifact_before

        for entry in manifest1["cards"]:
            card_path = output1 / entry["card_file"]
            card = json.loads(card_path.read_text(encoding="utf-8"))
            assert card["type"] == "message"
            assert len(card["attachments"]) == 1
            rendered = json.dumps(card, ensure_ascii=False)
            assert rendered.count("원문 보기") == 1
            assert "TEAMS_WORKFLOW_WEBHOOK_URL" not in rendered

        candidates = select_teams_push_from_artifact(_payload())
        first_candidate = candidates[0]
        sent_state = mark_sent_after_success(
            empty_state(),
            first_candidate.article,
            cluster_key=first_candidate.cluster_key,
            signature=first_candidate.material_signature,
            importance=first_candidate.importance.level,
            source=str(first_candidate.article.get("source") or ""),
            send_succeeded=True,
            sent_at="2026-07-23T09:30:00+09:00",
            delivery_id="fixture-success",
        )
        save_state(sent_state, state)
        state_before = _sha(state)

        output2 = tmp / "out-2"
        second = _run(artifact, state, output2)
        assert second.returncode == 0, second.stderr
        manifest2 = json.loads((output2 / "manifest.json").read_text(encoding="utf-8"))
        assert manifest2["candidate_count_before_dedup"] == 2
        assert manifest2["card_count"] == 1
        assert manifest2["dedup_blocked_count"] == 1
        assert _sha(state) == state_before
        assert _sha(artifact) == artifact_before

        broken = tmp / "broken-state.json"
        broken.write_text("{broken", encoding="utf-8")
        failed = _run(artifact, broken, tmp / "out-broken")
        assert failed.returncode == 2
        assert "failed closed" in failed.stderr

        mock_payload = _payload()
        mock_payload["source"] = "mock-delta"
        mock_artifact = tmp / "mock.json"
        mock_artifact.write_text(
            json.dumps(mock_payload, ensure_ascii=False), encoding="utf-8"
        )
        output3 = tmp / "out-3"
        mock = _run(mock_artifact, tmp / "missing-state.json", output3)
        assert mock.returncode == 0
        manifest3 = json.loads((output3 / "manifest.json").read_text(encoding="utf-8"))
        assert manifest3["candidate_count_before_dedup"] == 0
        assert manifest3["card_count"] == 0

    print("RESULT=D7-AK-5C_TEAMS_AI_PUSH_DRY_RUN_VERIFIER_PASS")
    print("network_calls=0 state_writes=0 first_cards=2 dedup_cards=1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
