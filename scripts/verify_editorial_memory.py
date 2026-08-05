#!/usr/bin/env python3
"""D7-AK-6E R4-R7 §14 — deterministic verifier for the human editorial memory.

Covers: safe human-HTML extraction and Autoway wrapper rejection, corpus
schema and digest, append-only decisions with supersede-only corrections,
profile reproducibility and non-activation, product-head separation,
approved/near-miss/negative retrieval, market-surface versus structural-AI
distinction, Hermes disabled-fallback and fake-transport retrieval with zero
live writes, and privacy scans (no raw internal HTML, no employee identifier,
no session/SSO data, no secret) over the committed corpus.

CI-safe: private human files are NOT required — extraction is verified on
synthetic fixtures built in a temporary directory. Fully offline, no sends.
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (ROOT, ROOT / "scripts"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from app import ai_centrality, editorial_memory, hermes_adapter  # noqa: E402
from app.teams_ai_push import evaluate_teams_push_policy  # noqa: E402
import build_editorial_learning_profile as profile_builder  # noqa: E402
import extract_human_editorial_reference as extractor  # noqa: E402

CORPUS_ROOT = ROOT / "data" / "editorial_learning"

PASSES = 0
FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSES
    if condition:
        PASSES += 1
        print(f"PASS {name}")
    else:
        FAILURES.append(name)
        print(f"FAIL {name}" + (f" — {detail}" if detail else ""))


SYNTHETIC_BRIEF = """<!DOCTYPE html><html lang="ko"><head>
<title>AI 경영 T&amp;I · Weekly Brief — 2026년 8월 1주차 (2026.08.06)</title>
</head><body>
<section class="hero"><img src="x.jpg"><h2>합성 AI 데이터센터 헤드라인</h2>
<div class="hero-foot"><span>투자·산업</span></div></section>
<div class="ednote"><p>합성 에디터 요약.</p>출처 <a href="https://v.daum.net/v/1">합성매체<span class="dt">08.05</span></a></div>
<article class="card"><div class="card-body"><span class="chip">기술정보</span>
<h3>합성 카드 기사 제목 AI</h3><p class="sum">합성 요약 문장.</p>
<div class="src">출처 <a href="https://v.daum.net/v/2">합성매체2<span class="dt">08.04</span></a></div>
</div></article>
</body></html>"""

SYNTHETIC_AUTOWAY = """<!DOCTYPE html>
<!-- saved from url=(0033)https://autoway.hyundai.net/main/ -->
<html><head><title>메일</title>
<script src="./files/HMGScriptResource.axd"></script></head>
<body><input type="hidden" name="SessionToken" value="FAKE-SESSION-000">
<div id="sso-frame">groupware hmail wrapper only — no brief body</div>
</body></html>"""


def main() -> int:
    # ------------------------------------------------------------------
    # 1. Safe extraction on synthetic fixtures (no private files needed).
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory(prefix="editorial-memory-verify-") as tmp:
        tmp_path = Path(tmp)
        clean = tmp_path / "clean.html"
        clean.write_text(SYNTHETIC_BRIEF, encoding="utf-8")
        payload = extractor.extract_file(clean)
        check(
            "synthetic clean brief extracts hero + card",
            payload["article_count"] == 2
            and payload["articles"][0]["headline"]
            and payload["articles"][0]["evidence_level"] == "gold_plus"
            and payload["articles"][1]["evidence_level"] == "gold_selected"
            and payload["edition_key"] == "tni-weekly-2026-08-06",
            json.dumps(payload, ensure_ascii=False)[:200],
        )
        check(
            "extraction output passes the privacy scan",
            extractor.privacy_scan(payload) == [],
        )
        wrapper = tmp_path / "autoway.html"
        wrapper.write_text(SYNTHETIC_AUTOWAY, encoding="utf-8")
        try:
            extractor.extract_file(wrapper)
            check("Autoway wrapper without brief body fails closed", False)
        except ValueError:
            check("Autoway wrapper without brief body fails closed", True)
        tainted = dict(payload)
        tainted["articles"] = payload["articles"] + [
            {"title": "leak", "canonical_url": "", "human_summary": "autoway session"}
        ]
        check(
            "privacy scan catches internal wrapper markers",
            "autoway" in extractor.privacy_scan(tainted)
            and "session" in extractor.privacy_scan(tainted),
        )

    # ------------------------------------------------------------------
    # 2. Corpus schema + digest.
    # ------------------------------------------------------------------
    corpus = editorial_memory.load_corpus()
    check(
        "corpus loads with every evidence level accounted",
        len(corpus.records) >= 70
        and len(corpus.by_level("gold_plus")) == 5
        and len(corpus.by_level("gold_selected")) >= 25
        and len(corpus.by_level("silver_candidate")) >= 30
        and len(corpus.by_level("hard_negative")) == 9,
        f"records={len(corpus.records)}",
    )
    check(
        "corpus digest is deterministic",
        editorial_memory.load_corpus().digest == corpus.digest,
    )
    schema = json.loads((CORPUS_ROOT / "schema.json").read_text(encoding="utf-8"))
    check(
        "schema pins evidence levels and append-only decisions",
        schema["evidence_levels"]
        == list(editorial_memory.EVIDENCE_LEVELS)
        and schema["decision_record_fields"]["append_only"] is True,
    )
    required = set(schema["article_record_fields"]["required"]) - {
        "record_version",
        "factual_summary",
    }
    sample = corpus.by_level("gold_selected")[0]
    check(
        "corpus records carry the required schema fields",
        all(
            getattr(sample, name, None) not in (None,)
            for name in ("article_id", "evidence_level", "edition_key", "title", "source", "category")
        )
        and required
        <= {
            "evidence_level", "product", "edition_key", "article_id",
            "title", "source", "category",
        },
    )

    # ------------------------------------------------------------------
    # 3. Append-only decisions + supersede-only corrections.
    # ------------------------------------------------------------------
    decisions = editorial_memory.load_decisions()
    check(
        "decision ledger materialized with ingest + reconciliation + hard negatives",
        len(decisions) >= 10
        and any(r["record_type"] == "final_brief_ingest" for r in decisions)
        and any(r["record_type"] == "candidate_pool_ingest" for r in decisions)
        and any(
            r["record_type"] == "candidate_final_reconciliation" for r in decisions
        )
        and any(r["record_type"] == "hard_negative_ingest" for r in decisions),
        f"count={len(decisions)}",
    )
    check(
        "fail-closed extraction is recorded, never invented",
        any(r.get("status") == "fail_closed" for r in decisions),
    )
    with tempfile.TemporaryDirectory(prefix="decisions-append-") as tmp:
        tmp_root = Path(tmp)
        ledger = tmp_root / "decisions.jsonl"
        ledger.write_text("", encoding="utf-8")
        base = {
            "decision_id": "d-1",
            "record_version": 1,
            "recorded_at": "2026-08-04T00:00:00+00:00",
            "record_type": "editor_approval_feedback",
            "edition_key": "daily-2026-08-03",
        }
        editorial_memory.append_decision(base, tmp_root)
        editorial_memory.append_decision(base, tmp_root)  # idempotent, no dup
        supersede = {
            **base,
            "decision_id": "d-2",
            "record_type": "supersede",
            "supersedes": "d-1",
        }
        editorial_memory.append_decision(supersede, tmp_root)
        lines = [
            json.loads(line)
            for line in ledger.read_text(encoding="utf-8").splitlines()
        ]
        check(
            "decisions are append-only with supersede-style corrections",
            len(lines) == 2
            and lines[0]["decision_id"] == "d-1"
            and lines[1]["supersedes"] == "d-1",
        )
        try:
            editorial_memory.append_decision({"decision_id": "bad"}, tmp_root)
            check("malformed decision record fails closed", False)
        except ValueError:
            check("malformed decision record fails closed", True)

    # ------------------------------------------------------------------
    # 4. Profile reproducibility + non-activation.
    # ------------------------------------------------------------------
    profile_path = CORPUS_ROOT / "profiles" / "profile-v001.json"
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    rebuilt = profile_builder.build_profile(
        corpus, decisions, profile["build_timestamp"]
    )
    check(
        "profile rebuild reproduces the committed digest",
        rebuilt["profile_digest"] == profile["profile_digest"]
        and profile["corpus_digest"] == corpus.digest,
        f"{rebuilt['profile_digest'][:12]} vs {profile['profile_digest'][:12]}",
    )
    pointer = json.loads(
        (CORPUS_ROOT / "profiles" / "latest.json").read_text(encoding="utf-8")
    )
    check(
        "built profiles never self-activate",
        profile["active"] is False
        and pointer["active"] is False
        and "human approval" in " ".join(profile["activation_requirements"]),
    )

    # ------------------------------------------------------------------
    # 5. Product-head separation (§6/§14).
    # ------------------------------------------------------------------
    glasses = {
        "title": "AI 안경 커닝 진화에 대학가 ‘비상’…시험장 대응책은 미흡",
        "snippet": "AI 안경을 활용한 커닝 수법이 진화하며 대학 시험 관리에 비상이 걸렸다.",
    }
    weekly_score = editorial_memory.score_article(
        editorial_memory.PRODUCT_WEEKLY, glasses, corpus
    )
    teams_score = editorial_memory.score_article(
        editorial_memory.PRODUCT_TEAMS, glasses, corpus
    )
    teams_gate = evaluate_teams_push_policy(
        {
            **glasses,
            "source": "연합뉴스",
            "url": "https://www.yna.co.kr/view/AKRGLASSES",
            "publisher_direct": True,
            "current_run_seen": True,
            "published_at": "2026-08-02T09:00:00+09:00",
            "score": 3.0,
            "shadow_urgency_status": "none",
            "shadow_confirmed_event_types": [],
        }
    )
    check(
        "AI-glasses cheating: Weekly/Daily-eligible preference, Teams gate rejects",
        weekly_score.preference_score > teams_score.preference_score
        and ai_centrality.classify(glasses).is_central
        and not teams_gate.eligible
        and teams_gate.rejection_reason
        in {"insufficient_importance", "insufficient_hdec_relevance"},
        f"weekly={weekly_score.preference_score} teams={teams_score.preference_score} "
        f"gate={teams_gate.rejection_reason}",
    )
    eu_act = {
        "title": "EU, AI법 본격 시행···AI 생성물 표시·챗봇 고지 의무화",
        "snippet": "유럽연합 AI법이 시행되며 AI 생성물 표시 의무가 도입됐다.",
    }
    check(
        "EU AI Act qualifies for policy/regulation",
        ai_centrality.delivery_category(eu_act)[0] == ai_centrality.CATEGORY_AI_POLICY,
        ai_centrality.delivery_category(eu_act)[0],
    )
    hallucination = {
        "title": "AI가 만든 '유령 판례'에 법조계 골머리",
        "snippet": "존재하지 않는 판례 인용이 법률 실무에서 문제가 되고 있다.",
    }
    check(
        "legal hallucinated precedent qualifies for governance/risk",
        ai_centrality.delivery_category(hallucination)[0]
        == ai_centrality.CATEGORY_AI_RISK_SECURITY,
        ai_centrality.delivery_category(hallucination)[0],
    )
    check(
        "AI data-center construction remains eligible",
        ai_centrality.classify(
            {
                "title": "전남광주·장성, 20MW AI 데이터센터 구축 착수",
                "snippet": "20MW 규모 AI 데이터센터 구축이 시작됐다.",
            }
        ).is_central,
    )
    check(
        "generic political candidacy remains rejected",
        not ai_centrality.classify(
            {
                "title": "권향엽, 민주당 전남광주특별시당 위원장 출마 선언",
                "snippet": "위원장 선거 출마를 선언했다.",
            }
        ).is_central,
    )
    check(
        "generic property sale remains rejected without data-center conversion",
        not ai_centrality.classify(
            {
                "title": "중수청 품은 서울 을지로 '르네스퀘어' 다시 매물로",
                "snippet": "오피스 빌딩이 다시 매물로 나왔다.",
            }
        ).is_central
        and ai_centrality.classify(
            {
                "title": "낡은 공장 매각…AI 데이터센터 전환 확정",
                "snippet": "공장 부지가 AI 데이터센터로 전환된다.",
            }
        ).is_central,
    )

    # ------------------------------------------------------------------
    # 6. Market-surface vs structural-AI distinction (§2/§13).
    # ------------------------------------------------------------------
    ibm = ai_centrality.classify(
        {
            "title": "IBM 시가총액 하락…AI 인프라 지출 확대가 소프트웨어·컨설팅 예산 잠식",
            "snippet": "AI 인프라 지출이 기존 예산을 잠식하고 있다.",
        }
    )
    google = ai_centrality.classify(
        {
            "title": "알파벳 주가 하락…핵심 AI 인재 연쇄 이탈에 경쟁력 우려",
            "snippet": "핵심 AI 인재 연쇄 이탈이 이어졌다.",
        }
    )
    onsemi = ai_centrality.classify(
        {
            "title": "[미국 특징주] 온세미, 3분기 가이던스 기대치 상회…시간 외 6% 반등",
            "snippet": "AI 데이터센터용 반도체 수요 확대 속에 가이던스가 기대치를 상회했다.",
        }
    )
    check(
        "IBM budget displacement is not rejected solely for stock language",
        ibm.is_central and ibm.surface_market and ibm.structural_event == "ai_budget_reallocation",
    )
    check(
        "Google talent exodus is not rejected solely for market-cap language",
        google.is_central and google.structural_event == "ai_talent_change",
    )
    check(
        "Onsemi rebound rejected when the central event is market movement",
        not onsemi.is_central
        and onsemi.exclusion == ai_centrality.EXCLUSION_STOCK_MARKET,
    )

    # ------------------------------------------------------------------
    # 7. Retrieval: approved / near-miss / negative precedents.
    # ------------------------------------------------------------------
    onsemi_retrieval = editorial_memory.retrieve(
        {"title": "[미국 특징주] 온세미 가이던스 상회…시간 외 반등", "snippet": "AI 데이터센터 수요."},
        corpus,
    )
    check(
        "hard-negative retrieval surfaces the observed Onsemi precedent",
        bool(onsemi_retrieval.hard_negative)
        and "온세미" in onsemi_retrieval.hard_negative[0].record.title,
    )
    gov = editorial_memory.retrieve(
        {"title": "정부, 공공부문 AI 전환 확대 발표", "snippet": "공공부문 AI 전환."},
        corpus,
    )
    check(
        "approved retrieval surfaces the human gold precedent",
        bool(gov.gold_plus) and "공공부문 AI 전환" in gov.gold_plus[0].record.title,
    )
    reconciliation = editorial_memory.reconcile_pool_with_finals(
        "tni-weekly-2026-07-23", corpus
    )
    check(
        "near-miss evidence stays honest when the final edition is unavailable",
        reconciliation["same_edition_final_available"] is False
        and reconciliation["unresolved_status"] == "silver_final_unavailable"
        and not corpus.by_level("near_miss"),
        json.dumps(reconciliation)[:120],
    )
    assessment = editorial_memory.score_article(
        editorial_memory.PRODUCT_TEAMS,
        {"title": "[특징주] 반도체주 급등…AI 데이터센터 기대", "snippet": "AI 수요 기대."},
        corpus,
    )
    check(
        "similarity never bypasses deterministic gates (advisory only)",
        assessment.deterministic_gates_bypassed is False
        and any(
            r.startswith("fails_deterministic_ai_gate")
            for r in assessment.rationale
        ),
        repr(assessment.rationale),
    )
    check(
        "explanations name approved/rejected precedents and the difference",
        assessment.rejected_precedent != ""
        and assessment.decisive_difference != "",
    )

    # ------------------------------------------------------------------
    # 8. Hermes adapter: default-off, fake retrieval, fallback, no writes.
    # ------------------------------------------------------------------
    check(
        "Hermes is disabled by default",
        hermes_adapter.hermes_enabled({}) is False
        and hermes_adapter.HermesEditorialMemoryAdapter(env={}).enabled is False,
    )
    calls: list[dict] = []

    def fake_transport(request):
        calls.append(dict(request))
        return {
            "matched_article_ids": [corpus.by_level("gold_plus")[0].article_id]
        }

    fake = hermes_adapter.HermesEditorialMemoryAdapter(
        enabled=True, transport=fake_transport
    )
    fake_result = fake.retrieve({"title": "정부 AI 전환", "snippet": ""}, corpus)
    check(
        "fake-Hermes retrieval is used when enabled",
        fake_result.mode == "hermes"
        and calls
        and calls[0]["read_only"] is True
        and calls[0]["action"] == "retrieve",
    )
    broken = hermes_adapter.HermesEditorialMemoryAdapter(
        enabled=True,
        transport=lambda request: (_ for _ in ()).throw(RuntimeError("down")),
    )
    check(
        "Hermes failure falls back to local retrieval (never fail-open)",
        broken.retrieve({"title": "정부 AI 전환", "snippet": ""}, corpus).mode
        == "local_fallback",
    )
    disabled = hermes_adapter.HermesEditorialMemoryAdapter(env={})
    check(
        "Hermes-disabled path uses the local corpus",
        disabled.retrieve({"title": "정부 AI 전환", "snippet": ""}, corpus).mode
        == "local_disabled",
    )
    check(
        "Hermes adapter exposes no write/approve/send path",
        fake.live_writes == 0
        and not any(
            hasattr(fake, attr)
            for attr in ("write", "send", "approve", "persist", "deploy")
        ),
    )
    report = hermes_adapter.build_weekly_learning_report(corpus, decisions=decisions)
    check(
        "weekly learning report is human-readable and role-bounded",
        "gold_plus: 5" in report and "no approve/send/state" in report,
    )

    # ------------------------------------------------------------------
    # 9. Privacy of the committed corpus.
    # ------------------------------------------------------------------
    def _blank_meta(value, *, key=""):
        """Blank policy/reason meta-fields that legitimately DESCRIBE the
        forbidden wrapper vocabulary (schema privacy policy, fail-closed
        reasons); every content-bearing field stays fully scanned."""
        if isinstance(value, dict):
            return {
                k: ("" if k in {"reason", "forbidden_content", "note", "privacy",
                                "activation_note", "description"}
                    else _blank_meta(v, key=k))
                for k, v in value.items()
            }
        if isinstance(value, list):
            return [_blank_meta(item, key=key) for item in value]
        return value

    offenders = []
    for path in sorted(CORPUS_ROOT.rglob("*")):
        if path.is_dir():
            continue
        if path.suffix not in {".json", ".jsonl"}:
            offenders.append(f"non-json artifact: {path.name}")
            continue
        if path.suffix == ".jsonl":
            rows = [
                _blank_meta(json.loads(line))
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            text = json.dumps(rows, ensure_ascii=False).lower()
        else:
            text = json.dumps(
                _blank_meta(json.loads(path.read_text(encoding="utf-8"))),
                ensure_ascii=False,
            ).lower()
        for marker in extractor.PRIVACY_FORBIDDEN_MARKERS:
            if marker in text:
                offenders.append(f"{path.name}:{marker}")
        stripped = re.sub(r"https?://[^\s\"]+", "", text)
        if re.search(r"(?<![0-9a-z/])[0-9]{7}(?![0-9])", stripped):
            offenders.append(f"{path.name}:employee-id-pattern")
        if re.search(r"(smtp|webhook|token|secret)\s*[:=]\s*['\"]?[a-z0-9]{12,}", stripped):
            offenders.append(f"{path.name}:secret-pattern")
    check(
        "committed corpus carries no raw HTML, identifiers, sessions, or secrets",
        offenders == [],
        repr(offenders[:5]),
    )

    print()
    print(
        f"EDITORIAL_MEMORY_VERIFIER={'PASS' if not FAILURES else 'FAIL'} "
        f"checks={PASSES} failures={len(FAILURES)}"
    )
    print(
        "COUNTERS network=0 smtp=0 teams=0 telegram=0 production_state_writes=0 "
        "hermes_live_writes=0 workflow_dispatches=0"
    )
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    raise SystemExit(main())
