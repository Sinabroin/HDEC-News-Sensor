#!/usr/bin/env python3
"""Deterministic no-network verifier for the D7-AK-6A Teams production sender.

Covers both halves of the production wiring:

1. ``scripts/send_teams_ai_push.py`` — fail-closed gates, article-level email
   delivery over the reused Gmail SMTP contract, persistent dedup reuse, per-email
   independence, and partial-failure semantics.
2. ``.github/workflows/scheduled-live-refresh.yml`` — the delta-gated step that
   invokes it, plus the dedup-state persistence step.

Production transport is ``email_channel``: exactly one email per eligible article
is sent to the Teams channel address over the verified Gmail SMTP contract owned by
``scripts/send_email_alert.py``. Every delivery path here runs through an injected
fake SMTP transport, so Gmail is never contacted and no message leaves this
machine. All state fixtures live in a temporary directory; the production state
path ``data/teams_push_state.json`` is asserted absent before and after.

Workflow gate expressions are evaluated with the same ``_eval_gate`` the existing
hourly-gate verifier uses, so both verifiers agree on what the YAML means.
"""

from __future__ import annotations

import email
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from email import policy as email_policy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import send_email_alert  # noqa: E402
import send_teams_ai_push as sender  # noqa: E402
from verify_meaningful_delta_quality import _eval_gate, _step_block, _step_if  # noqa: E402

SCRIPT = ROOT / "scripts" / "send_teams_ai_push.py"
WORKFLOW = ROOT / ".github" / "workflows" / "scheduled-live-refresh.yml"
PRODUCTION_STATE = ROOT / "data" / "teams_push_state.json"

TEAMS_STEP = "Hourly Teams AI article cards (delta-gated auto-send)"
PERSIST_STEP = "Persist Teams AI push dedup state"

# Fixture credentials — all fictitious. The Teams channel uses the RFC 2606
# reserved ``.invalid`` TLD, and every SMTP call is intercepted by a fake factory,
# so even a mis-wired send resolves nowhere and connects to nothing.
FIXTURE_SMTP_USER = "radar-bot@gmail.com"
FIXTURE_SMTP_PASSWORD = "fixture-app-password"
FIXTURE_FROM = "radar@hdec.co.kr"
FIXTURE_TEAMS_CHANNEL = "teams-channel.fixture@example.invalid"

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


def _fixture_credentials():
    return sender.EmailChannelCredentials(
        smtp_user=FIXTURE_SMTP_USER,
        smtp_password=FIXTURE_SMTP_PASSWORD,
        from_address=FIXTURE_FROM,
        teams_address=FIXTURE_TEAMS_CHANNEL,
    )


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


# --------------------------------------------------------------------------
# injected fake Gmail SMTP transport (no network, records every message)
# --------------------------------------------------------------------------
class _SMTPRecorder:
    """A drop-in ``smtplib.SMTP`` factory that records instead of connecting.

    ``statuses`` scripts the rcpt/data response code for the Nth send in order, so a
    partial-failure scenario (e.g. ``(250, 550, 250)``) can be reproduced offline."""

    def __init__(self, statuses=(250,)) -> None:
        self.statuses = list(statuses)
        self.attempts: list[str] = []          # recipient per send attempt (rcpt reached)
        self.messages: list[dict] = []         # {to, raw} for messages that reached DATA

    def status_for(self, index: int) -> int:
        return self.statuses[index] if index < len(self.statuses) else self.statuses[-1]

    def __call__(self, host, port, timeout=None):
        return _SMTPSession(self)


class _SMTPSession:
    def __init__(self, recorder: _SMTPRecorder) -> None:
        self.rec = recorder
        self._addr = ""
        self._code = 250

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def ehlo(self):
        return 250, b"ok"

    def starttls(self, context=None):
        return 220, b"ready"

    def login(self, user, password):
        return 235, b"authenticated"

    def mail(self, from_address):
        return 250, b"ok"

    def rcpt(self, address):
        index = len(self.rec.attempts)
        self._addr = address
        self._code = self.rec.status_for(index)
        self.rec.attempts.append(address)
        return self._code, b"recipient"

    def data(self, payload):
        self.rec.messages.append({"to": self._addr, "raw": payload})
        return self._code, b"queued"


def _parse_message(raw: bytes) -> dict:
    msg = email.message_from_bytes(raw, policy=email_policy.default)
    text = msg.get_body(preferencelist=("plain",))
    html = msg.get_body(preferencelist=("html",))
    return {
        "subject": str(msg["Subject"] or ""),
        "to": str(msg["To"] or ""),
        "from": str(msg["From"] or ""),
        "text": text.get_content() if text else "",
        "html": html.get_content() if html else "",
    }


SEND_ENV = {
    "TEAMS_AI_PUSH_MODE": "send",
    "APPROVE_TEAMS_AI_PUSH": "true",
    "GITHUB_ACTIONS": "true",
    "GMAIL_SMTP_USER": FIXTURE_SMTP_USER,
    "GMAIL_SMTP_APP_PASSWORD": FIXTURE_SMTP_PASSWORD,
    "ALERT_EMAIL_FROM": FIXTURE_FROM,
    "TEAMS_CHANNEL_EMAIL": FIXTURE_TEAMS_CHANNEL,
}


def _run_main_approved(argv: list[str], recorder: _SMTPRecorder) -> tuple[int, str]:
    """Run the real entrypoint with an approved send env and an injected SMTP factory.

    ``send_email_alert.smtplib.SMTP`` is restored and the environment rolled back even
    on error, so no later check can inherit an approved send environment or a patched
    transport."""
    import io
    from contextlib import redirect_stderr, redirect_stdout

    backup = {key: os.environ.get(key) for key in SEND_ENV}
    os.environ.update(SEND_ENV)
    original = send_email_alert.smtplib.SMTP
    send_email_alert.smtplib.SMTP = recorder
    out, err = io.StringIO(), io.StringIO()
    try:
        with redirect_stdout(out), redirect_stderr(err):
            rc = sender.main(argv)
    finally:
        send_email_alert.smtplib.SMTP = original
        for key, value in backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    return rc, out.getvalue() + err.getvalue()


def _deliver(tmp: Path, payload, state_path: Path, *, send=True, statuses=(250,)):
    recorder = _SMTPRecorder(statuses)
    artifact = _write(
        tmp / f"artifact-{abs(hash(json.dumps(payload, sort_keys=True))) % 10**8}.json",
        payload,
    )
    summary = sender.deliver(
        artifact_path=artifact,
        state_path=state_path,
        credentials=_fixture_credentials(),
        should_send=send,
        dashboard_url="https://example.com/dashboard",
        report_url="https://example.com/report",
        smtp_factory=recorder,
    )
    return summary, recorder


# --------------------------------------------------------------------------
# 1. static safety of the sender itself
# --------------------------------------------------------------------------
def _executable_source(text: str) -> str:
    """Strip docstrings and comment-only lines.

    The sender's prose legitimately names the SMTP transport and the digest sender it
    reuses/does-not-invoke, so scanning raw text would flag its own documentation.
    Scanning only executable lines also means a hidden literal cannot live in a
    comment."""
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

    # The transport lives in the proven email sender; the Teams sender delegates and
    # never re-implements the SMTP handshake itself.
    check("sender reuses the proven SMTP contract: deliver_email_message",
          "deliver_email_message" in src and "from send_email_alert import" in src)
    check("sender delegates transport — no direct smtplib handshake",
          "smtplib" not in src and "starttls" not in src and "sendmail" not in src
          and ".rcpt(" not in src)

    check("sender hardcodes no https endpoint",
          re.search(r"https://[A-Za-z0-9]", src) is None)
    check("sender hardcodes no recipient/email address literal",
          re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", src) is None)

    for helper in ("select_teams_push_from_artifact", "render_article_email",
                   "filter_unsent_candidates", "load_state", "persist_after_success",
                   "evaluate_dedup", "resolve_state_path", "article_identity"):
        check(f"sender reuses existing leaf helper: {helper}", helper in src)

    for owned in ("def evaluate_dedup", "def filter_unsent_candidates",
                  "def derive_event_cluster_key", "def material_signature",
                  "def save_state", "def classify_ai_topic", "def map_importance",
                  "def render_article_email"):
        check(f"sender does not re-implement leaf logic: {owned}", owned not in src)

    # email_channel is the production transport, not a fallback: the webhook is only a
    # reserved optional transport and is never a required send precondition.
    check("webhook is not a send precondition",
          "resolve_webhook_url" not in _preconditions_source(src))
    check("send preconditions require the SMTP credentials",
          all(tok in _preconditions_source(src)
              for tok in ("smtp_user", "smtp_password", "from_address", "teams_address")))

    state_src = (ROOT / "app" / "teams_push_state.py").read_text(encoding="utf-8")
    check("state writes stay atomic in the owning leaf",
          "NamedTemporaryFile" in state_src and "tmp_path.replace(state_path)" in state_src)
    check("sender never opens the state file for writing itself",
          'open(' not in src.replace('.open("a", encoding="utf-8")', ""))


def _preconditions_source(src: str) -> str:
    """Return just the body of ``check_send_preconditions`` from the executable source."""
    lines = src.splitlines()
    out: list[str] = []
    capturing = False
    for line in lines:
        if line.startswith("def check_send_preconditions"):
            capturing = True
            continue
        if capturing:
            if line and not line[0].isspace():
                break
            out.append(line)
    return "\n".join(out)


# --------------------------------------------------------------------------
# 2. CLI-level fail-closed gates (subprocess; no message is ever built)
# --------------------------------------------------------------------------
def _cli(tmp: Path, artifact: Path, state: Path, env_overrides: dict) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    for key in ("TEAMS_AI_PUSH_MODE", "APPROVE_TEAMS_AI_PUSH", "TEAMS_WORKFLOW_WEBHOOK_URL",
                "GITHUB_ACTIONS", "TEAMS_PUSH_STATE_PATH", "GITHUB_OUTPUT",
                "DELTA_ARTIFACT_FILE", "GMAIL_SMTP_USER", "GMAIL_SMTP_APP_PASSWORD",
                "GMAIL_SMTP_PASSWORD", "ALERT_EMAIL_FROM", "TEAMS_CHANNEL_EMAIL"):
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
        "GMAIL_SMTP_USER": FIXTURE_SMTP_USER,
        "GMAIL_SMTP_APP_PASSWORD": FIXTURE_SMTP_PASSWORD,
        "ALERT_EMAIL_FROM": FIXTURE_FROM,
        "TEAMS_CHANNEL_EMAIL": FIXTURE_TEAMS_CHANNEL,
    }

    default = _cli(tmp, artifact, state, {})
    check("default execution is dry-run and exits 0", default.returncode == 0, default.stderr)
    check("default execution performs no send", "mode=dry_run_no_send" in default.stdout
          and "attempted=0" in default.stdout, default.stdout)
    check("default execution writes no state file", not state.exists())
    check("dry-run reports state_changed=false",
          "state_changed=false" in (tmp / "gh_output.txt").read_text(encoding="utf-8"))
    check("default execution declares email_channel transport",
          "production=email_channel" in default.stdout)

    cases = (
        ("not a GitHub Actions environment blocks send", {**approved, "GITHUB_ACTIONS": ""},
         artifact, "not_github_actions"),
        ("missing approval blocks send", {**approved, "APPROVE_TEAMS_AI_PUSH": ""},
         artifact, "send_not_approved"),
        ("missing SMTP user fails closed", {**approved, "GMAIL_SMTP_USER": ""},
         artifact, "smtp_user_missing"),
        ("missing SMTP credential fails closed", {**approved, "GMAIL_SMTP_APP_PASSWORD": ""},
         artifact, "smtp_credential_missing"),
        ("missing from address fails closed", {**approved, "ALERT_EMAIL_FROM": ""},
         artifact, "from_address_missing"),
        ("missing Teams channel address fails closed", {**approved, "TEAMS_CHANNEL_EMAIL": ""},
         artifact, "teams_channel_missing"),
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
        check(f"{name} → no delivery summary emitted",
              "Teams AI push summary" not in result.stdout)

    broken = tmp / "broken-state.json"
    broken.write_text("{broken", encoding="utf-8")
    result = _cli(tmp, artifact, broken, approved)
    check("invalid persistent state fails closed",
          result.returncode == 2 and "state_invalid" in result.stderr,
          f"rc={result.returncode} err={result.stderr.strip()[:160]}")
    check("no state file is created by any fail-closed run", not state.exists())

    # webhook is optional: full credentials with no webhook satisfy the preconditions.
    backup = {k: os.environ.get(k) for k in ("GITHUB_ACTIONS", "APPROVE_TEAMS_AI_PUSH",
                                             "TEAMS_WORKFLOW_WEBHOOK_URL")}
    os.environ.update({"GITHUB_ACTIONS": "true", "APPROVE_TEAMS_AI_PUSH": "true"})
    os.environ.pop("TEAMS_WORKFLOW_WEBHOOK_URL", None)
    raised = ""
    try:
        sender.check_send_preconditions(_fixture_credentials())
    except sender.FailClosed as exc:
        raised = exc.reason
    finally:
        for k, v in backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    check("complete SMTP credentials without any webhook pass preconditions", raised == "",
          f"unexpected fail-closed: {raised}")


# --------------------------------------------------------------------------
# 3. delivery, dedup, and partial-failure behaviour (injected SMTP)
# --------------------------------------------------------------------------
def check_delivery(tmp: Path) -> None:
    state = tmp / "state.json"

    summary, rec = _deliver(tmp, _payload([_article()]), state)
    check("new article: exactly one SMTP send attempt",
          summary["attempted_count"] == 1 and len(rec.attempts) == 1, str(summary))
    check("new article: delivered and state written",
          summary["delivered_count"] == 1 and summary["failed_count"] == 0
          and summary["state_changed"] is True and state.exists(), str(summary))
    check("delivered article is accepted on SMTP 250",
          summary["records"][0]["status"] == "accepted_250", str(summary["records"]))
    after_first = _sha(state)

    parsed = _parse_message(rec.messages[0]["raw"])
    check("delivery targets the Teams channel address only",
          rec.attempts == [FIXTURE_TEAMS_CHANNEL] and parsed["to"] == FIXTURE_TEAMS_CHANNEL,
          str(rec.attempts))
    check("one email per article (single recipient message)", len(rec.messages) == 1)
    body = parsed["subject"] + "\n" + parsed["text"] + "\n" + parsed["html"]
    for field_name, token in (
        ("중요도", "최우선"),
        ("기사 제목", "AI 데이터센터 투자 계약 체결"),
        ("핵심 요약", "핵심 요약"),
        ("현대건설 영향", "현대건설 영향"),
        ("출처", "Reuters"),
        ("원문 링크", "example.com/news/a"),
        ("대시보드 링크", "example.com/dashboard"),
    ):
        check(f"email carries required field: {field_name}", token in body,
              f"missing {token!r}")

    summary, rec = _deliver(tmp, _payload([_article()]), state)
    check("same article re-run: zero attempts, one dedup block",
          summary["attempted_count"] == 0 and summary["dedup_blocked_count"] == 1
          and len(rec.attempts) == 0, str(summary))
    check("same article re-run leaves state byte-identical", _sha(state) == after_first)

    other_publisher = _article(
        article_key="evt-b",
        title="AI 데이터센터 투자 계약 공식화한 Microsoft·OpenAI",
        summary="같은 사건을 다른 매체가 보도했다.",
        source="연합뉴스",
        url="https://example.com/news/b",
    )
    summary, rec = _deliver(tmp, _payload([other_publisher]), state)
    blocked_reason = summary["records"][0]["dedup_reason"] if summary["records"] else ""
    check("same event from another publisher is cluster-deduped with zero attempts",
          summary["attempted_count"] == 0 and summary["dedup_blocked_count"] == 1
          and blocked_reason == "duplicate:cluster_key" and len(rec.attempts) == 0,
          f"reason={blocked_reason} summary={summary}")

    updated = _article(
        summary="투자 규모가 상향 조정된 것으로 계약서에 명시됐다.",
        change_type="material_content_update",
    )
    summary, rec = _deliver(tmp, _payload([updated]), state)
    updated_body = _parse_message(rec.messages[0]["raw"]) if rec.messages else {}
    updated_text = (updated_body.get("subject", "") + updated_body.get("text", "")
                    + updated_body.get("html", ""))
    check("material update re-sends once with the update label",
          summary["attempted_count"] == 1 and summary["delivered_count"] == 1
          and "[업데이트]" in updated_text, str(summary))
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
    summary, rec = _deliver(tmp, _payload([failing]), state, statuses=(550,))
    check("SMTP recipient rejection is attempted once and recorded as failed",
          summary["attempted_count"] == 1 and summary["failed_count"] == 1
          and summary["delivered_count"] == 0, str(summary))
    check("SMTP failure leaves persistent state unchanged", _sha(state) == after_update)
    check("SMTP failure exposes only a status category",
          summary["records"][0]["status"] == "recipient_rejected", str(summary["records"]))
    check("rejected recipient never reaches the DATA phase", len(rec.messages) == 0)


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
    summary, rec = _deliver(tmp, _payload(articles), cap_state)
    check("at most three articles are ever selected", summary["candidate_count"] == 3, str(summary))
    check("three articles produce exactly three SMTP sends",
          summary["attempted_count"] == 3 and len(rec.attempts) == 3, str(summary))
    check("one email per article, three distinct messages",
          len(rec.messages) == 3
          and len({_parse_message(m["raw"])["subject"] for m in rec.messages}) == 3)
    check("every send targets the Teams channel address only",
          set(rec.attempts) == {FIXTURE_TEAMS_CHANNEL})

    partial_state = tmp / "partial-state.json"
    artifact = _write(tmp / "partial.json", _payload(articles))
    gh_output = tmp / "partial-output.txt"
    recorder = _SMTPRecorder((250, 550, 250))
    rc, logs = _run_main_approved(
        ["--artifact", str(artifact), "--state", str(partial_state),
         "--github-output", str(gh_output)],
        recorder,
    )

    check("partial failure exits non-zero", rc == 1, f"rc={rc}")
    check("partial failure still attempts every article", len(recorder.attempts) == 3,
          str(len(recorder.attempts)))

    # Which article lands in the middle depends on the importance ranking, so the
    # expectation is derived from the run's own per-email outcomes rather than from the
    # fixture order. The contract under test is the mapping itself: persisted set ==
    # delivered set, exactly.
    ref_to_key = {sender.article_ref(item): item["article_key"] for item in articles}
    outcomes: dict[str, set[str]] = {"delivered": set(), "failed": set()}
    for ref, outcome in re.findall(r"article=([0-9a-f]{12}) outcome=(delivered|failed)", logs):
        outcomes[outcome].add(ref_to_key[ref])
    persisted = json.loads(partial_state.read_text(encoding="utf-8"))
    delivered_ids = set(persisted["article_ids"])

    check("run reports two delivered and one failed email",
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
    recorder = _SMTPRecorder((250,))
    rc, logs = _run_main_approved(
        ["--artifact", str(artifact), "--state", str(state),
         "--github-output", str(gh_output)],
        recorder,
    )

    check("approved send path completes", rc == 0 and len(recorder.attempts) == 1, f"rc={rc}")
    check("Teams channel address never appears in logs", FIXTURE_TEAMS_CHANNEL not in logs)
    check("SMTP credential never appears in logs", FIXTURE_SMTP_PASSWORD not in logs)
    check("SMTP user / from address never appear in logs",
          FIXTURE_SMTP_USER not in logs and FIXTURE_FROM not in logs)
    check("article URL never appears in logs", "example.com/news" not in logs)
    check("logs carry only a hashed article reference",
          re.search(r"article=[0-9a-f]{12} ", logs) is not None, logs.strip()[:200])
    # The normalized article URL is a legitimate dedup key in state; credentials,
    # recipient addresses, and approval tokens must never appear.
    state_text = state.read_text(encoding="utf-8")
    for token in (FIXTURE_TEAMS_CHANNEL, FIXTURE_SMTP_PASSWORD, FIXTURE_SMTP_USER,
                  FIXTURE_FROM, "APPROVE_TEAMS_AI_PUSH"):
        check(f"persistent state never stores: {token}", token not in state_text)
    check("persistent state stores no email body",
          "<html" not in state_text.lower() and "핵심 요약" not in state_text)
    # The email body legitimately carries the content links, but never a credential.
    parsed = _parse_message(recorder.messages[0]["raw"])
    message_text = parsed["subject"] + parsed["text"] + parsed["html"]
    check("email body never embeds the SMTP credential or approval token",
          FIXTURE_SMTP_PASSWORD not in message_text
          and "APPROVE_TEAMS_AI_PUSH" not in message_text)


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
          "send_email_alert.py" not in teams_block
          and "EMAIL_SEND_MODE" not in teams_block
          and "APPROVE_SEND_EMAIL" not in teams_block
          and "SEND_TO_TEAMS" not in teams_block)
    check("Teams step injects send mode and approval only here",
          "TEAMS_AI_PUSH_MODE: send" in teams_block
          and 'APPROVE_TEAMS_AI_PUSH: "true"' in teams_block
          and text.count("TEAMS_AI_PUSH_MODE: send") == 1
          and text.count('APPROVE_TEAMS_AI_PUSH: "true"') == 1)
    for secret_line in (
        "GMAIL_SMTP_USER: ${{ secrets.GMAIL_SMTP_USER }}",
        "GMAIL_SMTP_APP_PASSWORD: ${{ secrets.GMAIL_SMTP_APP_PASSWORD }}",
        "ALERT_EMAIL_FROM: ${{ secrets.ALERT_EMAIL_FROM }}",
        "TEAMS_CHANNEL_EMAIL: ${{ secrets.TEAMS_CHANNEL_EMAIL }}",
    ):
        check(f"Teams step injects email_channel secret: {secret_line.split(':')[0]}",
              secret_line in teams_block)
    check("Teams step uses the production state path",
          "TEAMS_PUSH_STATE_PATH: data/teams_push_state.json" in teams_block)
    check("Teams step consumes the shared delta artifact",
          "DELTA_ARTIFACT_FILE: ${{ runner.temp }}/dashboard_delta.json" in teams_block)
    check("no webhook secret is injected anywhere in the workflow",
          "secrets.TEAMS_WORKFLOW_WEBHOOK_URL" not in text)

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
    print("transport=email_channel real_smtp_connections=0 production_state_writes=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
