#!/usr/bin/env python3
"""Deterministic no-network verifier for the D7-AK-6A Teams production sender.

Covers both halves of the production wiring:

1. ``scripts/send_teams_ai_push.py`` — fail-closed gates, article-level delivery,
   persistent dedup reuse, per-card independence, and partial-failure semantics.
2. ``.github/workflows/scheduled-live-refresh.yml`` — the delta-gated step that
   invokes it, plus the dedup-state persistence step.

Every delivery path runs through an injected recording opener, so the real
webhook is never contacted. The fixture webhook host uses the RFC 2606 reserved
``.invalid`` TLD, so even a failed injection cannot leave this machine. All
state fixtures live in a temporary directory; the production state path
``data/teams_push_state.json`` is asserted absent before and after.

Workflow gate expressions are evaluated with the same ``_eval_gate`` the existing
hourly-gate verifier uses, so both verifiers agree on what the YAML means.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import send_teams_ai_push as sender  # noqa: E402
from verify_meaningful_delta_quality import _eval_gate, _step_block, _step_if  # noqa: E402

SCRIPT = ROOT / "scripts" / "send_teams_ai_push.py"
WORKFLOW = ROOT / ".github" / "workflows" / "scheduled-live-refresh.yml"
PRODUCTION_STATE = ROOT / "data" / "teams_push_state.json"

# RFC 2606 reserved TLD — unresolvable, so an injection failure still sends nothing.
FIXTURE_WEBHOOK = "https://teams-ai-push.invalid/workflows/fixture"
TEAMS_STEP = "Hourly Teams AI article cards (delta-gated auto-send)"
PERSIST_STEP = "Persist Teams AI push dedup state"

FAILURES: list[str] = []
CHECKS = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    if condition:
        print(f"PASS: {name}")
        return
    FAILURES.append(name)
    print(f"FAIL: {name}" + (f" — {detail}" if detail else ""))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "absent"


# --------------------------------------------------------------------------
# fixtures — same article contract the dry-run verifier already proved
# --------------------------------------------------------------------------
def _article(**overrides):
    base = {
        "article_key": "evt-a",
        "title": "OpenAI와 Microsoft, AI 데이터센터 투자 계약 체결",
        "summary": "양사가 AI 데이터센터 투자 계약을 공식 체결했다.",
        "hdec_relevance": "데이터센터 EPC와 전력 인프라 사업 기회에 직접 영향",
        "source": "Reuters",
        "published_at": "2026-07-23T00:20:00+00:00",
        "url": "https://example.com/news/a",
        "score": 4.7,
        "shadow_urgency_status": "confirmed",
        "shadow_would_pass": True,
        "shadow_confirmed_event_types": ["investment_confirmed"],
        "change_type": "new_article",
    }
    base.update(overrides)
    return base


def _payload(articles, **overrides):
    payload = {
        "schema_version": 1,
        "source": "live-delta",
        "shadow_alert_delta": True,
        "generated_at": "2026-07-23T09:31:00+09:00",
        "generated_kst": "2026-07-23 09:31",
        "articles": list(articles),
    }
    payload.update(overrides)
    return payload


def _write(path: Path, payload) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


class _FakeResponse:
    def __init__(self, status: int) -> None:
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def getcode(self) -> int:
        return self.status


class RecordingOpener:
    """Records every request instead of performing it."""

    def __init__(self, statuses=(200,)) -> None:
        self.statuses = list(statuses)
        self.calls: list[dict] = []

    def __call__(self, request, timeout=None):
        self.calls.append(
            {
                "url": request.full_url,
                "body": json.loads(request.data.decode("utf-8")),
            }
        )
        index = len(self.calls) - 1
        status = self.statuses[index] if index < len(self.statuses) else self.statuses[-1]
        return _FakeResponse(status)


SEND_ENV = {
    "TEAMS_AI_PUSH_MODE": "send",
    "APPROVE_TEAMS_AI_PUSH": "true",
    "TEAMS_WORKFLOW_WEBHOOK_URL": FIXTURE_WEBHOOK,
    "GITHUB_ACTIONS": "true",
}


def _run_main_approved(argv: list[str], opener: RecordingOpener) -> tuple[int, str]:
    """Run the real entrypoint with an approved send env and an injected opener.

    urllib.request.urlopen is restored and the environment is rolled back even on
    error, so no later check can inherit an approved send environment."""
    import io
    import urllib.request as _urllib_request
    from contextlib import redirect_stderr, redirect_stdout

    original = _urllib_request.urlopen
    backup = {key: os.environ.get(key) for key in SEND_ENV}
    os.environ.update(SEND_ENV)
    _urllib_request.urlopen = opener
    out, err = io.StringIO(), io.StringIO()
    try:
        with redirect_stdout(out), redirect_stderr(err):
            rc = sender.main(argv)
    finally:
        _urllib_request.urlopen = original
        for key, value in backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    return rc, out.getvalue() + err.getvalue()


def _deliver(tmp: Path, payload, state_path: Path, *, send=True, statuses=(200,)):
    opener = RecordingOpener(statuses)
    artifact = _write(tmp / f"artifact-{abs(hash(json.dumps(payload, sort_keys=True))) % 10**8}.json", payload)
    summary = sender.deliver(
        artifact_path=artifact,
        state_path=state_path,
        webhook_url=FIXTURE_WEBHOOK,
        should_send=send,
        dashboard_url="https://example.com/dashboard",
        report_url="https://example.com/report",
        opener=opener,
    )
    return summary, opener


# --------------------------------------------------------------------------
# 1. static safety of the sender itself
# --------------------------------------------------------------------------
def _executable_source(text: str) -> str:
    """Strip docstrings and comment-only lines.

    The sender's prose legitimately names the email sender it deliberately does
    not use, so scanning raw text would flag its own documentation. Scanning only
    executable lines also means a real import cannot hide inside a comment."""
    import ast

    tree = ast.parse(text)
    drop: set[int] = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if not body or not isinstance(body[0], ast.Expr):
            continue
        value = body[0].value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            drop.update(range(body[0].lineno, (body[0].end_lineno or body[0].lineno) + 1))
    return "\n".join(
        line
        for index, line in enumerate(text.splitlines(), 1)
        if index not in drop and not line.lstrip().startswith("#")
    )


def check_sender_source() -> None:
    src = _executable_source(SCRIPT.read_text(encoding="utf-8"))

    for token in ("smtplib", "sendmail", "SEND_TO_TEAMS", "APPROVE_SEND_EMAIL",
                  "EMAIL_SEND_MODE", "send_email_alert"):
        check(f"sender has no SMTP/email fallback token: {token}", token not in src)

    check(
        "sender hardcodes no https endpoint",
        re.search(r"https://[A-Za-z0-9]", src) is None,
    )

    for helper in ("select_teams_push_from_artifact", "build_candidate_card",
                   "filter_unsent_candidates", "load_state", "persist_after_success",
                   "evaluate_dedup", "resolve_state_path"):
        check(f"sender reuses existing helper: {helper}", f"    {helper},\n" in src)

    for owned in ("def evaluate_dedup", "def filter_unsent_candidates",
                  "def derive_event_cluster_key", "def material_signature",
                  "def save_state", "def classify_ai_topic", "def map_importance"):
        check(f"sender does not re-implement leaf logic: {owned}", owned not in src)

    state_src = (ROOT / "app" / "teams_push_state.py").read_text(encoding="utf-8")
    check(
        "state writes stay atomic in the owning leaf",
        "NamedTemporaryFile" in state_src and "tmp_path.replace(state_path)" in state_src,
    )
    check(
        "sender never opens the state file for writing itself",
        'open(' not in src.replace('.open("a", encoding="utf-8")', ""),
    )


# --------------------------------------------------------------------------
# 2. CLI-level fail-closed gates (subprocess; no request is ever built)
# --------------------------------------------------------------------------
def _cli(tmp: Path, artifact: Path, state: Path, env_overrides: dict) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    for key in ("TEAMS_AI_PUSH_MODE", "APPROVE_TEAMS_AI_PUSH", "TEAMS_WORKFLOW_WEBHOOK_URL",
                "GITHUB_ACTIONS", "TEAMS_PUSH_STATE_PATH", "GITHUB_OUTPUT",
                "DELTA_ARTIFACT_FILE"):
        env.pop(key, None)
    env.update(env_overrides)
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--artifact", str(artifact), "--state", str(state),
         "--github-output", str(tmp / "gh_output.txt")],
        cwd=ROOT, env=env, text=True, capture_output=True, check=False, timeout=60,
    )


def check_fail_closed(tmp: Path) -> None:
    artifact = _write(tmp / "cli-artifact.json", _payload([_article()]))
    state = tmp / "cli-state.json"
    approved = {
        "TEAMS_AI_PUSH_MODE": "send",
        "APPROVE_TEAMS_AI_PUSH": "true",
        "GITHUB_ACTIONS": "true",
        "TEAMS_WORKFLOW_WEBHOOK_URL": FIXTURE_WEBHOOK,
    }

    default = _cli(tmp, artifact, state, {})
    check("default execution is dry-run and exits 0", default.returncode == 0, default.stderr)
    check("default execution performs no send", "mode=dry_run_no_send" in default.stdout
          and "attempted=0" in default.stdout, default.stdout)
    check("default execution writes no state file", not state.exists())
    check("dry-run reports state_changed=false",
          "state_changed=false" in (tmp / "gh_output.txt").read_text(encoding="utf-8"))

    cases = (
        ("not a GitHub Actions environment blocks send", {**approved, "GITHUB_ACTIONS": ""},
         artifact, "not_github_actions"),
        ("missing approval blocks send", {**approved, "APPROVE_TEAMS_AI_PUSH": ""},
         artifact, "send_not_approved"),
        ("missing webhook fails closed", {**approved, "TEAMS_WORKFLOW_WEBHOOK_URL": ""},
         artifact, "webhook_missing_or_not_https"),
        ("non-https webhook fails closed",
         {**approved, "TEAMS_WORKFLOW_WEBHOOK_URL": "http://teams-ai-push.invalid/hook"},
         artifact, "webhook_missing_or_not_https"),
        ("non live-delta artifact blocks send", approved,
         _write(tmp / "mock.json", _payload([_article()], source="mock-delta")),
         "artifact_not_live_delta"),
        ("shadow_alert_delta=false sends nothing", approved,
         _write(tmp / "noshadow.json", _payload([_article()], shadow_alert_delta=False)),
         "shadow_alert_delta_closed"),
        ("missing artifact fails closed", approved, tmp / "absent.json", "artifact_missing"),
    )
    for name, env, art, reason in cases:
        result = _cli(tmp, art, state, env)
        check(name, result.returncode == 2 and reason in result.stderr,
              f"rc={result.returncode} err={result.stderr.strip()[:160]}")
        check(f"{name} → zero webhook attempts", "attempted=" not in result.stdout)

    broken = tmp / "broken-state.json"
    broken.write_text("{broken", encoding="utf-8")
    result = _cli(tmp, artifact, broken, approved)
    check("invalid persistent state fails closed",
          result.returncode == 2 and "state_invalid" in result.stderr,
          f"rc={result.returncode} err={result.stderr.strip()[:160]}")
    check("no state file is created by any fail-closed run", not state.exists())


# --------------------------------------------------------------------------
# 3. delivery, dedup, and partial-failure behaviour (injected opener)
# --------------------------------------------------------------------------
def check_delivery(tmp: Path) -> None:
    state = tmp / "state.json"

    summary, opener = _deliver(tmp, _payload([_article()]), state)
    check("new article: exactly one webhook attempt",
          summary["attempted_count"] == 1 and len(opener.calls) == 1, str(summary))
    check("new article: delivered and state written",
          summary["delivered_count"] == 1 and summary["failed_count"] == 0
          and summary["state_changed"] is True and state.exists(), str(summary))
    after_first = _sha(state)
    check("delivered card is a single Adaptive Card message",
          opener.calls[0]["body"]["type"] == "message"
          and len(opener.calls[0]["body"]["attachments"]) == 1)

    summary, opener = _deliver(tmp, _payload([_article()]), state)
    check("same article re-run: zero attempts, one dedup block",
          summary["attempted_count"] == 0 and summary["dedup_blocked_count"] == 1
          and len(opener.calls) == 0, str(summary))
    check("same article re-run leaves state byte-identical", _sha(state) == after_first)

    other_publisher = _article(
        article_key="evt-b",
        title="AI 데이터센터 투자 계약 공식화한 Microsoft·OpenAI",
        summary="같은 사건을 다른 매체가 보도했다.",
        source="연합뉴스",
        url="https://example.com/news/b",
    )
    summary, opener = _deliver(tmp, _payload([other_publisher]), state)
    blocked_reason = summary["records"][0]["dedup_reason"] if summary["records"] else ""
    check("same event from another publisher is cluster-deduped with zero attempts",
          summary["attempted_count"] == 0 and summary["dedup_blocked_count"] == 1
          and blocked_reason == "duplicate:cluster_key" and len(opener.calls) == 0,
          f"reason={blocked_reason} summary={summary}")

    updated = _article(
        summary="투자 규모가 상향 조정된 것으로 계약서에 명시됐다.",
        change_type="material_content_update",
    )
    summary, opener = _deliver(tmp, _payload([updated]), state)
    body = json.dumps(opener.calls[0]["body"], ensure_ascii=False) if opener.calls else ""
    check("material update re-sends once with the update label",
          summary["attempted_count"] == 1 and summary["delivered_count"] == 1
          and "[업데이트]" in body, str(summary))
    check("material update refreshes persistent state", _sha(state) != after_first)
    after_update = _sha(state)

    failing = _article(
        article_key="evt-fail",
        title="Google, 스마트건설 로봇 자율 시공 솔루션 출시",
        summary="건설 로봇 자율 시공 솔루션을 정식 출시했다.",
        url="https://example.com/news/fail",
        score=3.9,
        shadow_confirmed_event_types=["product_available"],
    )
    summary, opener = _deliver(tmp, _payload([failing]), state, statuses=(500,))
    check("webhook failure is attempted once and recorded as failed",
          summary["attempted_count"] == 1 and summary["failed_count"] == 1
          and summary["delivered_count"] == 0, str(summary))
    check("webhook failure leaves persistent state unchanged", _sha(state) == after_update)
    check("webhook failure exposes only a status category",
          summary["records"][0]["status"] == "rejected_500", str(summary["records"]))


def check_cap_and_partial(tmp: Path) -> None:
    articles = [
        _article(article_key="c1", title="OpenAI, AI 데이터센터 신규 투자 계약 체결",
                 url="https://example.com/c1"),
        _article(article_key="c2", title="현대건설, 스마트건설 로봇 자율 시공 계약 체결",
                 summary="스마트건설 로봇 자율 시공 계약을 체결했다.",
                 url="https://example.com/c2", score=4.6,
                 shadow_confirmed_event_types=["contract_awarded"]),
        _article(article_key="c3", title="Amazon, 원전 SMR 전력 공급 계약 승인",
                 summary="SMR 전력 공급 계약이 승인됐다.",
                 url="https://example.com/c3", score=4.55,
                 shadow_confirmed_event_types=["agreement_signed"]),
        _article(article_key="c4", title="Meta, BIM 디지털 트윈 플랫폼 정식 출시",
                 summary="BIM 디지털 트윈 플랫폼을 정식 출시했다.",
                 url="https://example.com/c4", score=4.52,
                 shadow_confirmed_event_types=["product_available"]),
    ]

    cap_state = tmp / "cap-state.json"
    summary, opener = _deliver(tmp, _payload(articles), cap_state)
    check("at most three articles are ever selected", summary["candidate_count"] == 3, str(summary))
    check("three cards produce exactly three webhook attempts",
          summary["attempted_count"] == 3 and len(opener.calls) == 3, str(summary))
    check("one card per article, each a single attachment",
          all(len(call["body"]["attachments"]) == 1 for call in opener.calls)
          and len({json.dumps(c["body"], sort_keys=True) for c in opener.calls}) == 3)
    check("every attempt targets the injected webhook only",
          {call["url"] for call in opener.calls} == {FIXTURE_WEBHOOK})

    partial_state = tmp / "partial-state.json"
    artifact = _write(tmp / "partial.json", _payload(articles))
    gh_output = tmp / "partial-output.txt"
    opener = RecordingOpener((200, 500, 200))
    rc, logs = _run_main_approved(
        ["--artifact", str(artifact), "--state", str(partial_state),
         "--github-output", str(gh_output)],
        opener,
    )

    check("partial failure exits non-zero", rc == 1, f"rc={rc}")
    check("partial failure still attempts every card", len(opener.calls) == 3, str(len(opener.calls)))

    # Which article lands in the middle depends on the importance ranking, so the
    # expectation is derived from the run's own per-card outcomes rather than from
    # the fixture order. The contract under test is the mapping itself: persisted
    # set == delivered set, exactly.
    ref_to_key = {sender.article_ref(item): item["article_key"] for item in articles}
    outcomes: dict[str, set[str]] = {"delivered": set(), "failed": set()}
    for ref, outcome in re.findall(r"article=([0-9a-f]{12}) outcome=(delivered|failed)", logs):
        outcomes[outcome].add(ref_to_key[ref])
    persisted = json.loads(partial_state.read_text(encoding="utf-8"))
    delivered_ids = set(persisted["article_ids"])

    check("run reports two delivered and one failed card",
          len(outcomes["delivered"]) == 2 and len(outcomes["failed"]) == 1, str(outcomes))
    check("only delivered articles are persisted",
          delivered_ids == outcomes["delivered"],
          f"persisted={sorted(delivered_ids)} delivered={sorted(outcomes['delivered'])}")
    check("failed article stays resendable",
          not (delivered_ids & outcomes["failed"]), str(sorted(outcomes["failed"])))
    output_text = gh_output.read_text(encoding="utf-8")
    check("partial success still reports state_changed=true",
          "state_changed=true" in output_text, output_text.strip())
    check("no temporary state residue remains",
          not [p for p in partial_state.parent.iterdir()
               if p.name.startswith("tmp") and p.suffix not in {".json", ".txt"}])


def check_no_leaks(tmp: Path) -> None:
    state = tmp / "leak-state.json"
    artifact = _write(tmp / "leak.json", _payload([_article()]))
    gh_output = tmp / "leak-output.txt"
    opener = RecordingOpener((200,))
    rc, logs = _run_main_approved(
        ["--artifact", str(artifact), "--state", str(state),
         "--github-output", str(gh_output)],
        opener,
    )

    check("approved send path completes", rc == 0 and len(opener.calls) == 1, f"rc={rc}")
    check("webhook URL never appears in logs", FIXTURE_WEBHOOK not in logs)
    check("webhook host never appears in logs", "teams-ai-push.invalid" not in logs)
    check("article URL never appears in logs", "example.com/news" not in logs)
    check("logs carry only a hashed article reference",
          re.search(r"article=[0-9a-f]{12} ", logs) is not None, logs.strip()[:200])
    state_text = state.read_text(encoding="utf-8")
    for token in (FIXTURE_WEBHOOK, "teams-ai-push.invalid", "APPROVE_TEAMS_AI_PUSH"):
        check(f"persistent state never stores: {token}", token not in state_text)
    check("persistent state stores no full card JSON",
          "AdaptiveCard" not in state_text and "attachments" not in state_text)
    card_text = json.dumps(opener.calls[0]["body"], ensure_ascii=False)
    check("card body never embeds the webhook or its secret name",
          FIXTURE_WEBHOOK not in card_text and "TEAMS_WORKFLOW_WEBHOOK_URL" not in card_text)


# --------------------------------------------------------------------------
# 4. workflow wiring
# --------------------------------------------------------------------------
def check_workflow() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    teams_if = _step_if(text, TEAMS_STEP)
    teams_block = _step_block(text, TEAMS_STEP)
    persist_block = _step_block(text, PERSIST_STEP)

    check("production Teams step exists", bool(teams_if) and bool(teams_block))
    check("state persistence step exists", bool(persist_block))

    open_ctx = {
        "steps.build.outputs.live_ok": "true",
        "steps.delta.outputs.shadow_alert_delta": "true",
        "vars.HOURLY_DELTA_AUTO_SEND": "1",
        "github.ref": "refs/heads/main",
        "github.event_name": "schedule",
        "github.event.inputs.force_dry_run": "",
    }
    check("scheduled main + hourly opt-in + live + confirmed delta → step is eligible",
          _eval_gate(teams_if, open_ctx) is True, teams_if)
    check("non-main ref → step is skipped",
          _eval_gate(teams_if, {**open_ctx, "github.ref": "refs/heads/fix/x"}) is False)
    check("workflow_dispatch force_dry_run=true → step is skipped",
          _eval_gate(teams_if, {**open_ctx, "github.event_name": "workflow_dispatch",
                                "github.event.inputs.force_dry_run": "true"}) is False)
    check("hourly opt-in disabled → step is skipped",
          _eval_gate(teams_if, {**open_ctx, "vars.HOURLY_DELTA_AUTO_SEND": "0"}) is False)
    check("no confirmed urgency → step is skipped",
          _eval_gate(teams_if, {**open_ctx,
                                "steps.delta.outputs.shadow_alert_delta": "false"}) is False)
    check("live collection failure → step is skipped",
          _eval_gate(teams_if, {**open_ctx, "steps.build.outputs.live_ok": "false"}) is False)

    check("Teams step invokes the article-level production sender",
          "python3 scripts/send_teams_ai_push.py" in teams_block)
    check("Teams step never invokes the SMTP digest sender",
          "send_email_alert.py" not in teams_block)
    check("Teams step injects send mode and approval only here",
          "TEAMS_AI_PUSH_MODE: send" in teams_block
          and 'APPROVE_TEAMS_AI_PUSH: "true"' in teams_block
          and text.count("TEAMS_AI_PUSH_MODE: send") == 1
          and text.count('APPROVE_TEAMS_AI_PUSH: "true"') == 1)
    check("Teams step uses the production state path",
          "TEAMS_PUSH_STATE_PATH: data/teams_push_state.json" in teams_block)
    check("Teams step consumes the shared delta artifact",
          "DELTA_ARTIFACT_FILE: ${{ runner.temp }}/dashboard_delta.json" in teams_block)

    check("persistence survives a partial sender failure",
          "if: always()" in persist_block
          and "steps.teams_ai_push.outcome != 'skipped'" in persist_block
          and "steps.teams_ai_push.outputs.state_changed == 'true'" in persist_block)
    check("persistence stages exactly the state file",
          "git add -- data/teams_push_state.json" in persist_block
          and persist_block.count("git add") == 1)
    check("persistence commits with the agreed subject",
          'git commit -m "chore: persist Teams AI push dedup state"' in persist_block)
    check("persistence detects an untracked first write",
          "git status --porcelain -- data/teams_push_state.json" in persist_block)
    check("persistence aborts a failed rebase instead of forcing",
          "git rebase --abort" in persist_block)
    check("persistence retries at most three times",
          "for attempt in 1 2 3; do" in persist_block)
    check("persistence prints the state commit SHA",
          "git rev-parse HEAD" in persist_block)
    for token in ("--force", "--force-with-lease", "push -f", "+HEAD", "+refs/"):
        check(f"persistence never force-pushes: {token}", token not in persist_block)
    for token in ("docs/daily", "git add .", "git add -A", "git commit -am",
                  "scripts/", "app/", ".github/"):
        check(f"persistence touches nothing beyond the state file: {token}",
              token not in persist_block)
    check("persistence never echoes secrets or state contents",
          "secrets." not in persist_block
          and not re.search(r"(cat|echo)[^\n]*teams_push_state\.json", persist_block))

    check("workflow runs this verifier in its gate",
          text.count("python3 scripts/verify_teams_ai_push_production.py") == 1)
    check("no other step carries the Teams webhook secret",
          text.count("TEAMS_WORKFLOW_WEBHOOK_URL: ${{ secrets.TEAMS_WORKFLOW_WEBHOOK_URL }}") == 1
          and text.count("TEAMS_WORKFLOW_WEBHOOK_URL") == 2)


def main() -> int:
    check("production state absent before verification", not PRODUCTION_STATE.exists())
    check_sender_source()
    with tempfile.TemporaryDirectory(prefix="hdec-ak6a-") as raw:
        tmp = Path(raw)
        check_fail_closed(tmp)
        check_delivery(tmp)
        check_cap_and_partial(tmp)
        check_no_leaks(tmp)
    check_workflow()
    check("production state absent after verification", not PRODUCTION_STATE.exists())

    print(f"checks={CHECKS} failures={len(FAILURES)}")
    if FAILURES:
        for name in FAILURES:
            print(f"FAILED: {name}")
        return 1
    print("RESULT=D7-AK-6A_TEAMS_AI_PUSH_PRODUCTION_VERIFIER_PASS")
    print("real_webhook_calls=0 smtp_connections=0 production_state_writes=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
