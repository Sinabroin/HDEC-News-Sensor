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
path ``data/teams_push_state.json`` is validated read-only when present and its
SHA256 must remain exactly unchanged. An absent production state is also valid.

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
from app.teams_push_state import (  # noqa: E402
    HELD_SPECIALISTS_KEY,
    InvalidTeamsPushState,
    empty_state,
    load_state,
    observe_held_specialist,
    save_state,
)
from app.public_urls import CANONICAL_DASHBOARD_URL  # noqa: E402
from verify_meaningful_delta_quality import _eval_gate, _step_block, _step_if  # noqa: E402

SCRIPT = ROOT / "scripts" / "send_teams_ai_push.py"
WORKFLOW = ROOT / ".github" / "workflows" / "scheduled-live-refresh.yml"
WATCH_WORKFLOW = ROOT / ".github" / "workflows" / "teams-ai-news-watch.yml"
PRODUCTION_STATE = ROOT / "data" / "teams_push_state.json"
DIRECT_ARTICLE_URL = "https://yna.co.kr/news/a"
GOOGLE_AGGREGATOR_URL = "https://news.google.com/rss/articles/teams-production-fixture"
REPRESENTATIVE_IMAGE_URL = "https://images.publisher.example.test/news/a.jpg"
MAJOR_FIXTURE_DOMAINS = {
    "연합뉴스": "yna.co.kr",
    "KBS": "kbs.co.kr",
    "조선일보": "chosun.com",
    "매일경제": "mk.co.kr",
}

# D7-AK-6C — the article-level Teams sender now lives solely in the 10-minute watch
# workflow. The hourly scheduled-live-refresh no longer runs it (single owner).
TEAMS_STEP = "Teams AI news article cards (watch auto-send)"
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


def _state_status(path: Path) -> str:
    """Classify state without logging its contents and without writing to its path."""
    if not path.exists():
        return "absent"
    try:
        load_state(path)
    except InvalidTeamsPushState:
        return "malformed"
    return "valid"


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
        # R4-R9A: the default fixture publisher is a locked primary-ten name —
        # the major-media-first source gate makes non-major publishers
        # non-selectable, and these fixtures prove the delivery path.
        "source": "연합뉴스",
        "published_at": "2026-07-23T00:20:00+00:00",
        "url": DIRECT_ARTICLE_URL,
        "publisher_direct": True,
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


def _validated_payload(articles, **overrides):
    """Serialize live facts with the production validated-Brief field contract."""
    rows = []
    for article in articles:
        why = str(
            article.get("whyImportant")
            or article.get("why_it_matters")
            or article.get("hdec_relevance")
            or ""
        )
        tier = str(
            article.get("hdec_relevance_tier")
            or article.get("decision_relevance_tier")
            or "A"
        )
        rows.append({
            **article,
            "article_id": article.get("article_id") or article.get("article_key"),
            "snippet": article.get("snippet") or article.get("summary"),
            "final_score": article.get("final_score", article.get("score")),
            "whyImportant": why,
            "why_it_matters": why,
            "hdec_relevance_tier": tier,
            "decision_relevance_tier": tier,
            "source_quality_passed": article.get("source_quality_passed", True),
            "publisher_direct": article.get("publisher_direct", True),
            "current_run_seen": article.get("current_run_seen", True),
            "teams_newness_eligible": article.get("teams_newness_eligible", True),
            "carried_forward": article.get("carried_forward", False),
        })
    payload = {
        "artifact_contract": "HDEC_VALIDATED_EXECUTIVE_BRIEF_V1",
        "news_data_mode": "live",
        "news_fallback_used": False,
        "collection_status": "LIVE_HEALTHY_WITH_ARTICLES",
        "generated_at": "2026-07-23T09:31:00+09:00",
        "generated_kst": "2026-07-23 09:31",
        "news_censor_display_articles": rows,
    }
    payload.update(overrides)
    return payload


def _write(path: Path, payload) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


# Twelve distinct, genuinely-sendable AI events (each an AI topic + a confirmed action),
# with distinct titles/URLs/sources so none collapse into one dedup cluster within a run.
_CAP_TITLES = (
    "OpenAI, 미국 AI 데이터센터에 대규모 투자 계약 체결",
    "Microsoft, 유럽 AI 데이터센터 전력공급 계약 체결",
    "Google, 아시아 AI 데이터센터 신설 투자 확정",
    "Amazon, 원전 SMR 기반 AI 전력공급 계약 승인",
    "Meta, AI 기반 BIM 디지털 트윈 플랫폼 정식 출시",
    "엔비디아, 차세대 GPU 클러스터 공급 계약 체결",
    "삼성전자, AI 반도체 파운드리 증설 투자 확정",
    "SK하이닉스, HBM 데이터센터 공급 계약 체결",
    "오라클, 클라우드 AI 데이터센터 신규 착공",
    "앤트로픽, 대규모 AI 인프라 투자 계약 공개",
    "현대건설, AI 데이터센터 EPC 계약 체결",
    "포스코이앤씨, 스마트건설 AI 자율 시공 계약 체결",
)


def _cap_articles(n: int) -> list:
    # R4-R9A: all four rotating publishers are locked primary-ten names so the
    # batch/cap/drain matrix keeps proving capacity behavior; source-gate
    # rejection and holdback are proven by their own dedicated fixtures.
    sources = ("연합뉴스", "KBS", "조선일보", "매일경제")
    return [
        _article(
            article_key=f"cap-{i}",
            title=_CAP_TITLES[i],
            summary=f"{_CAP_TITLES[i]} — 상세 내용.",
            source=sources[i % len(sources)],
            url=f"https://{MAJOR_FIXTURE_DOMAINS[sources[i % len(sources)]]}/cap/{i}",
            score=round(4.7 - i * 0.05, 3),
            shadow_confirmed_event_types=["investment_confirmed"],
        )
        for i in range(n)
    ]


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


def _deliver(
    tmp: Path,
    payload,
    state_path: Path,
    *,
    send=True,
    statuses=(250,),
    max_articles=10,
    now="",
):
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
        max_articles=max_articles,
        now_iso_value=now,
    )
    return summary, recorder


def check_state_read_only_contract(tmp: Path) -> None:
    """Regression fixtures for absent, valid-present, and malformed state."""
    absent = tmp / "state-contract-absent.json"
    check("absent production-state shape passes read-only validation",
          _state_status(absent) == "absent")

    present = tmp / "state-contract-present.json"
    save_state(empty_state(), present)
    present_before = _sha(present)
    check("existing production-state shape passes read-only validation",
          _state_status(present) == "valid")
    check("existing state fixture is byte-identical after read-only validation",
          _sha(present) == present_before)

    malformed = tmp / "state-contract-malformed.json"
    malformed.write_text("{malformed", encoding="utf-8")
    malformed_before = _sha(malformed)
    check("malformed production-state shape fails read-only validation",
          _state_status(malformed) == "malformed")
    check("malformed state fixture is byte-identical after read-only validation",
          _sha(malformed) == malformed_before)


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

    first_article = _article(
        url=GOOGLE_AGGREGATOR_URL,
        canonical_url=DIRECT_ARTICLE_URL,
        image_url=REPRESENTATIVE_IMAGE_URL,
        summary=(
            "양사가 AI 데이터센터 투자 계약을 공식 체결했다. "
            "투자 범위에는 GPU 인프라와 전력 설비가 포함된다. "
            "사업 일정도 함께 확정됐다. "
            "이 문장은 3줄 요약 상한 밖이므로 이메일에 끝까지 노출되면 안 된다. "
            + "추가 상세 " * 80
        ),
    )
    summary, rec = _deliver(tmp, _payload([first_article]), state)
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
        ("카테고리", "AI 데이터센터"),
        ("기사 제목", "AI 데이터센터 투자 계약 체결"),
        ("현대건설 관점", "현대건설 관점:"),
        ("출처", "연합뉴스"),
        ("언론사 원문 링크", DIRECT_ARTICLE_URL),
        ("대시보드 링크", CANONICAL_DASHBOARD_URL),
    ):
        check(f"email carries required field: {field_name}", token in body,
              f"missing {token!r}")
    check("plain-text fallback contains both named actions",
          f"기사 원문 보기: {DIRECT_ARTICLE_URL}" in parsed["text"]
          and f"전체 뉴스 대시보드 보기: {CANONICAL_DASHBOARD_URL}" in parsed["text"])
    check("HTML contains robust origin and dashboard buttons",
          '>기사 원문 보기</a>' in parsed["html"]
          and '>전체 뉴스 대시보드 보기</a>' in parsed["html"])
    check("email does not hotlink a remote representative image",
          REPRESENTATIVE_IMAGE_URL not in parsed["html"] and "<img" not in parsed["html"])
    check("Google News aggregator URL appears zero times in the email",
          GOOGLE_AGGREGATOR_URL not in body)
    check("executive context bullet count is bounded",
          1 <= parsed["text"].count("• ") <= 3
          and 1 <= parsed["html"].count("<li>") <= 3)
    check("unverified generated summary and long tail are excluded",
          "양사가 AI 데이터센터 투자 계약을 공식 체결했다." not in body
          and "이 문장은 3줄 요약 상한 밖이므로" not in body
          and "추가 상세" not in body)
    check("executive implication is explicitly labeled",
          "현대건설 관점:" in body)
    check("watch point is retained when supported",
          "Watch:" in parsed["text"] and "<strong>Watch</strong>" in parsed["html"])
    check("legacy summary and impact labels are absent",
          "핵심 요약" not in body and "왜 중요한가" not in body
          and "현대건설 영향" not in body)
    check("watch email contains no funnel diagnostics",
          "AI T&I 탐지 현황" not in body)
    check("detected time is removed from the body", "감지시각" not in body)
    check("importance is rendered as a small badge",
          "font-size:12px" in parsed["html"] and "border-radius:12px" in parsed["html"])
    check("canonical dashboard is exact and report link is absent",
          CANONICAL_DASHBOARD_URL in body and "example.com/report" not in body)
    check("email embeds no external CSS or JavaScript",
          "<script" not in parsed["html"].lower()
          and "<link" not in parsed["html"].lower()
          and "<style" not in parsed["html"].lower())

    fallback_state = tmp / "fallback-state.json"
    fallback_article = _article(
        article_key="evt-google-fallback",
        title="현대건설, 공간 AI 기반 스마트건설 계약 체결",
        summary="현대건설이 공간 AI 기반 스마트건설 계약을 체결했다.",
        source="팍스경제TV",
        url=GOOGLE_AGGREGATOR_URL,
        canonical_url="",
        external_url="",
        original_url="",
    )
    fallback_summary, fallback_rec = _deliver(
        tmp, _payload([fallback_article]), fallback_state
    )
    check("Google News-only important article is blocked before SMTP",
          fallback_summary["candidate_count"] == 0
          and fallback_summary["attempted_count"] == 0
          and fallback_summary["delivered_count"] == 0
          and len(fallback_rec.attempts) == 0
          and not fallback_state.exists(),
          str(fallback_summary))
    check("Google News-only article creates no email body",
          fallback_rec.messages == [])

    summary, rec = _deliver(tmp, _payload([first_article]), state)
    check("same article re-run: zero attempts, one dedup block",
          summary["attempted_count"] == 0 and summary["dedup_blocked_count"] == 1
          and len(rec.attempts) == 0, str(summary))
    check("same article re-run leaves state byte-identical", _sha(state) == after_first)

    other_publisher = _article(
        article_key="evt-b",
        title="AI 데이터센터 투자 계약 공식화한 Microsoft·OpenAI",
        summary="같은 사건을 다른 매체가 보도했다.",
        source="연합뉴스",
        url="https://yna.co.kr/news/b",
    )
    summary, rec = _deliver(tmp, _payload([other_publisher]), state)
    blocked_reason = summary["records"][0]["dedup_reason"] if summary["records"] else ""
    check("distinct article in the same broad event remains independently eligible",
          summary["attempted_count"] == 1 and summary["dedup_blocked_count"] == 0
          and blocked_reason == "new_article_or_event" and len(rec.attempts) == 1,
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
        title="Google, AI 기반 스마트건설 로봇 자율 시공 솔루션 출시",
        summary="AI가 건설 로봇의 자율 시공을 지원하는 솔루션을 정식 출시했다.",
        url="https://yna.co.kr/news/fail",
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

    # Fixture 16 — shadow_alert_delta=false no longer blocks: an important AI article still
    # delivers when the artifact flag is closed (the flag is not a send gate anymore).
    noflag_state = tmp / "noflag-state.json"
    summary, rec = _deliver(
        tmp,
        _payload(
            [_article(article_key="noflag", url="https://yna.co.kr/news/noflag")],
            shadow_alert_delta=False,
        ),
        noflag_state,
    )
    check("shadow_alert_delta=false still delivers an important article (fixture 16)",
          summary["candidate_count"] == 1 and summary["delivered_count"] == 1
          and len(rec.attempts) == 1, str(summary))


def check_cap_and_partial(tmp: Path) -> None:
    """Prove 0-5 batch selection, event dedup, and accepted-only persistence.

    D7-AK-6E R4-R6 §8-§10: zero eligible unsent send zero; one-to-five send all;
    more than five send exactly five and defer the rest; misconfiguration never
    widens the batch; only SMTP 250 becomes persistent sent state."""
    # -- §8 exact selection matrix over eligible supply 0..5, 6, 12.
    for supply, want_selected, want_deferred in (
        (0, 0, 0), (1, 1, 0), (2, 2, 0), (3, 3, 0), (4, 4, 0), (5, 5, 0),
        (6, 5, 1), (12, 5, 7),
    ):
        state = tmp / f"count-{supply}-state.json"
        summary, transport = _deliver(
            tmp, _validated_payload(_cap_articles(supply)), state, max_articles=5
        )
        check(f"{supply} eligible unsent select {want_selected} defer {want_deferred}",
              summary["eligible_unsent_rows"] == supply
              and summary["selected_rows"] == want_selected
              and summary["deferred_rows"] == want_deferred
              and summary["smtp_attempted_rows"] == want_selected
              and summary["smtp_accepted_rows"] == want_selected
              and summary["smtp_failed_rows"] == 0
              and summary["state_committed_rows"] == want_selected
              and len(transport.attempts) == want_selected
              and summary["counters_reconciled"] is True,
              str({k: summary[k] for k in (
                  "raw_rows", "eligible_unsent_rows", "selected_rows",
                  "deferred_rows", "smtp_attempted_rows", "counters_reconciled",
              )}))

    three, _ = _deliver(
        tmp, _validated_payload(_cap_articles(3)), tmp / "vocab-state.json",
        max_articles=5,
    )
    check("validated-Brief policy-stage counters retain normalized rows",
          three["verified_candidates"] == 3
          and three["AI_core"] == 3
          and three["HDEC_relevant"] == 3
          and three["importance_qualified"] == 3,
          str(three))
    check("validated-Brief rejection counters expose the exact stable vocabulary",
          tuple(three["rejection_breakdown"]) == sender.REJECTION_COUNTER_KEYS,
          str(three["rejection_breakdown"]))
    check("aggregate counters reconcile to every input row",
          three["rejection_reconciled"] is True
          and three["rejected_rows"] + three["eligible_unsent_rows"]
          == three["rejection_input_count"] == 3,
          str(three))

    # -- deferred queue drains at-most-once across natural runs.
    drain_state = tmp / "drain-state.json"
    drain_payload = _validated_payload(_cap_articles(6))
    first, first_transport = _deliver(tmp, drain_payload, drain_state, max_articles=5)
    check("run one sends the full batch of five and defers one",
          first["selected_rows"] == 5 and first["deferred_rows"] == 1
          and len(first_transport.attempts) == 5, str(first))
    check("non-selected eligible articles are not marked sent",
          len(load_state(drain_state)["article_ids"]) == 5,
          str(sorted(load_state(drain_state)["article_ids"])))
    second, second_transport = _deliver(tmp, drain_payload, drain_state, max_articles=5)
    check("run two skips the five sent items and drains the deferred one",
          second["previously_sent_rows"] == 5
          and second["selected_rows"] == 1
          and second["deferred_rows"] == 0
          and len(second_transport.attempts) == 1, str(second))
    third_run, third_transport = _deliver(tmp, drain_payload, drain_state, max_articles=5)
    check("accepted state is at-most-once on later runs",
          third_run["previously_sent_rows"] == 6
          and third_run["selected_rows"] == 0
          and third_run["smtp_attempted_rows"] == 0
          and not third_transport.attempts, str(third_run))

    # -- configured cap 1-5 is respected; the batch never invents filler.
    capped, capped_transport = _deliver(
        tmp, _validated_payload(_cap_articles(4)), tmp / "capped-state.json",
        max_articles=2,
    )
    check("configured cap two selects exactly two of four eligible",
          capped["selected_rows"] == 2 and capped["deferred_rows"] == 2
          and len(capped_transport.attempts) == 2, str(capped))

    # -- failed SMTP inside a batch: exact partial persistence, retry stays open.
    failed_state = tmp / "failed-queue-state.json"
    mixed_payload = _validated_payload(_cap_articles(3))
    failed, failed_transport = _deliver(
        tmp, mixed_payload, failed_state, statuses=(250, 550, 250), max_articles=5
    )
    check("mixed batch attempts all selected and persists only accepted",
          failed["selected_rows"] == 3
          and failed["smtp_attempted_rows"] == 3
          and failed["smtp_accepted_rows"] == 2
          and failed["smtp_failed_rows"] == 1
          and failed["state_committed_rows"] == 2
          and len(load_state(failed_state)["article_ids"]) == 2
          and len(failed_transport.attempts) == 3, str(failed))
    retried, retried_transport = _deliver(tmp, mixed_payload, failed_state, max_articles=5)
    check("failed article remains eligible and completes on retry",
          retried["selected_rows"] == 1
          and retried["smtp_accepted_rows"] == 1
          and retried["state_committed_rows"] == 1
          and len(retried_transport.attempts) == 1, str(retried))

    # -- §9 duplicate identity contract: URL variant, title variant.
    base_sent = _article(
        article_key="dup-base",
        url="https://yna.co.kr/dup-base",
        title="현대건설, AI 데이터센터 EPC 본계약 체결",
    )
    dup_state = tmp / "dup-state.json"
    _deliver(tmp, _validated_payload([base_sent]), dup_state, max_articles=5)
    url_variant = _article(
        article_key="dup-url-variant",
        url="https://yna.co.kr/dup-base?utm_source=teams&utm_medium=push",
        title="현대건설, AI 데이터센터 EPC 본계약 체결",
    )
    url_summary, url_transport = _deliver(
        tmp, _validated_payload([url_variant]), dup_state, max_articles=5
    )
    check("tracking-query URL variants collapse against the sent ledger",
          url_summary["previously_sent_rows"] == 1
          and url_summary["selected_rows"] == 0
          and not url_transport.attempts, str(url_summary))
    title_variant = _article(
        article_key="dup-title-variant",
        url="https://yna.co.kr/dup-title-variant",
        title="[속보] 현대건설 — \"AI 데이터센터\" EPC 본계약 체결!",
    )
    title_summary, title_transport = _deliver(
        tmp, _validated_payload([title_variant]), dup_state, max_articles=5
    )
    check("title punctuation/spacing variants collapse against the sent ledger",
          title_summary["previously_sent_rows"] == 1
          and title_summary["selected_rows"] == 0
          and not title_transport.attempts, str(title_summary))

    # -- §9 same-event multi-publisher collapse: the primary-ten version wins.
    event_title = "AI 데이터센터 전력 인프라 투자 계약 확정"
    other_top = _article(
        article_key="event-other-top",
        source="테크전문지",
        url="https://tech-press.example.test/event",
        title=event_title,
        score=5.0,
        shadow_confirmed_event_types=["investment_confirmed"],
    )
    yonhap_important = _article(
        article_key="event-yonhap",
        source="연합뉴스",
        url="https://yna.co.kr/news/event",
        title=event_title,
        score=3.8,
        shadow_confirmed_event_types=["investment_confirmed"],
    )
    event_state = tmp / "event-state.json"
    event_summary, event_transport = _deliver(
        tmp, _validated_payload([other_top, yonhap_important]), event_state,
        max_articles=5,
    )
    event_sources = {
        entry["source"]
        for entry in load_state(event_state)["article_ids"].values()
    }
    check("same event collapses to one representative from the primary ten",
          event_summary["duplicate_event_rows"] == 1
          and event_summary["selected_rows"] == 1
          and len(event_transport.attempts) == 1
          and event_sources == {"연합뉴스"}, str(event_summary))

    # -- distinct events on the same broad topic stay independent.
    distinct_state = tmp / "distinct-state.json"
    distinct_summary, distinct_transport = _deliver(
        tmp,
        _validated_payload([
            _article(
                article_key="distinct-a",
                source="연합뉴스",
                url="https://yna.co.kr/news/distinct-a",
                title="Microsoft, 유럽 AI 데이터센터 전력공급 계약 체결",
            ),
            _article(
                article_key="distinct-b",
                source="연합뉴스",
                url="https://yna.co.kr/news/distinct-b",
                title="Google, 아시아 AI 데이터센터 신설 투자 확정",
            ),
        ]),
        distinct_state,
        max_articles=5,
    )
    check("distinct events on one broad topic remain independent",
          distinct_summary["duplicate_event_rows"] == 0
          and distinct_summary["selected_rows"] == 2
          and len(distinct_transport.attempts) == 2, str(distinct_summary))

    # -- §9 material update may resend; a superficial rewrite may not.
    material_state = tmp / "material-state.json"
    original = _article(
        article_key="material-base",
        url="https://yna.co.kr/material",
        title="OpenAI, 미국 AI 데이터센터에 대규모 투자 계약 체결",
        summary="1단계 투자 규모는 100억 달러다.",
    )
    _deliver(tmp, _validated_payload([original]), material_state, max_articles=5)
    superficial = dict(original)
    superficial["article_key"] = "material-superficial"
    superficial["title"] = "OpenAI, 미국 AI 데이터센터에 '대규모 투자 계약' 체결!"
    superficial_summary, superficial_transport = _deliver(
        tmp, _validated_payload([superficial]), material_state, max_articles=5
    )
    check("superficial rewrite is not a material update and stays blocked",
          superficial_summary["selected_rows"] == 0
          and superficial_summary["previously_sent_rows"] == 1
          and not superficial_transport.attempts, str(superficial_summary))
    material = dict(original)
    material["change_type"] = "material_content_update"
    material["summary"] = "투자 규모가 300억 달러로 세 배 확대 승인됐다."
    material["snippet"] = material["summary"]
    material_summary, material_transport = _deliver(
        tmp, _validated_payload([material]), material_state, max_articles=5
    )
    check("meaningful material update is resent exactly once",
          material_summary["selected_rows"] == 1
          and material_summary["smtp_accepted_rows"] == 1
          and len(material_transport.attempts) == 1, str(material_summary))

    # -- carry-forward-only rows never alert (unchanged contract).
    carry = _article(
        article_key="carry-only",
        url="https://yna.co.kr/carry-only",
        carried_forward=True,
        current_run_seen=False,
        teams_newness_eligible=False,
    )
    carry_summary, carry_transport = _deliver(
        tmp, _validated_payload([carry]), tmp / "carry-state.json", max_articles=5
    )
    check("carry-forward-only article cannot alert",
          carry_summary["current_rows"] == 0
          and carry_summary["eligible_unsent_rows"] == 0
          and carry_summary["selected_rows"] == 0
          and not carry_transport.attempts, str(carry_summary))
    check("carry-forward exclusion is observable and reconciled",
          carry_summary["rejection_breakdown"]["carry_forward_excluded"] == 1
          and carry_summary["rejection_reconciled"] is True,
          str(carry_summary))

    # -- malformed validated rows fail as schema rejections (unchanged contract).
    malformed = dict(_cap_articles(1)[0])
    malformed.pop("shadow_urgency_status")
    malformed_summary, malformed_transport = _deliver(
        tmp,
        _validated_payload([malformed]),
        tmp / "malformed-state.json",
        send=False,
        max_articles=5,
    )
    check("validated artifact missing urgency status fails as a schema rejection",
          malformed_summary["eligible_unsent_rows"] == 0
          and malformed_summary["rejection_breakdown"]["malformed_required_field"] == 1
          and malformed_summary["rejection_breakdown"]["shadow_unavailable"] == 0
          and malformed_summary["rejection_reconciled"] is True
          and not malformed_transport.attempts,
          str(malformed_summary))

    # -- dashboard refresh between runs cannot consume the candidate queue.
    shadow_state = tmp / "dashboard-independent-state.json"
    queue_payload = _validated_payload(_cap_articles(3))
    before, _ = _deliver(tmp, queue_payload, shadow_state, send=False, max_articles=2)
    dashboard = tmp / "mutable-dashboard.html"
    dashboard.write_text("<html>refreshed before Teams watch</html>", encoding="utf-8")
    after, _ = _deliver(tmp, queue_payload, shadow_state, send=False, max_articles=2)
    check("dashboard refresh cannot consume the Teams candidate queue",
          before["selected_rows"] == after["selected_rows"] == 2
          and before["deferred_rows"] == after["deferred_rows"] == 1
          and not shadow_state.exists())

    # -- §8 configuration matrix: default 5, 1-5 respected, clamp, fail-closed floor.
    check("absent cap resolves to the default batch of five",
          sender._resolve_max_articles("") == (5, "default_5"))
    check("configured one-to-five caps are respected",
          sender._resolve_max_articles("1") == (1, "configured")
          and sender._resolve_max_articles("3") == (3, "configured")
          and sender._resolve_max_articles("5") == (5, "configured"))
    check("oversized caps clamp to the hard maximum of five",
          sender._resolve_max_articles("10") == (5, "clamped_to_hard_max_5")
          and sender._resolve_max_articles("99") == (5, "clamped_to_hard_max_5"))
    check("zero, negative, and malformed caps fail closed to one",
          sender._resolve_max_articles("0") == (1, "fail_closed_floor_1")
          and sender._resolve_max_articles("-7") == (1, "fail_closed_floor_1")
          and sender._resolve_max_articles("invalid") == (1, "fail_closed_floor_1"))
    check("batch constants are five and named",
          sender.DEFAULT_TEAMS_BATCH_MAX == 5 and sender.HARD_TEAMS_BATCH_MAX == 5)

    zero_state = tmp / "zero-state.json"
    blocked_only = _article(
        article_key="blocked-only",
        url="https://yna.co.kr/blocked",
        shadow_urgency_status="blocked",
        shadow_would_pass=False,
        shadow_confirmed_event_types=[],
    )
    zero, zero_transport = _deliver(tmp, _payload([blocked_only]), zero_state)
    check("zero policy-eligible candidates means zero SMTP attempts",
          zero["alert_policy_eligible"] == 0
          and zero["selected_rows"] == 0
          and zero["smtp_attempted_rows"] == 0
          and not zero_transport.attempts
          and not zero_state.exists(), str(zero))

    aggregator_state = tmp / "aggregator-only-state.json"
    aggregator_article = _article(
        article_key="aggregator-only",
        url=GOOGLE_AGGREGATOR_URL,
    )
    aggregator_summary, aggregator_transport = _deliver(
        tmp,
        _payload([aggregator_article]),
        aggregator_state,
    )
    check("aggregator-only important article is quarantined from delivery",
          aggregator_summary["candidate_count"] == 0
          and aggregator_summary["smtp_attempted_rows"] == 0
          and len(aggregator_transport.attempts) == 0
          and not aggregator_state.exists(),
          str(aggregator_summary))

    # -- §10 mixed accepted/rejected batch through the real CLI entrypoint.
    ten = _cap_articles(10)
    partial_state = tmp / "partial-state.json"
    artifact = _write(tmp / "partial.json", _payload(ten))
    gh_output = tmp / "partial-output.txt"
    recorder = _SMTPRecorder((250, 550, 250, 250, 550))
    rc, logs = _run_main_approved(
        ["--artifact", str(artifact), "--state", str(partial_state),
         "--github-output", str(gh_output)],
        recorder,
    )
    check("partial failure exits non-zero", rc == 1, f"rc={rc}")
    check("default batch attempts exactly five of ten eligible",
          len(recorder.attempts) == 5, str(len(recorder.attempts)))
    ref_to_key = {sender.article_ref(item): item["article_key"] for item in ten}
    outcomes: dict[str, set[str]] = {"delivered": set(), "failed": set()}
    for ref, outcome in re.findall(r"article=([0-9a-f]{12}) outcome=(delivered|failed)", logs):
        outcomes[outcome].add(ref_to_key[ref])
    persisted = json.loads(partial_state.read_text(encoding="utf-8"))
    delivered_ids = set(persisted["article_ids"])
    check("run reports three delivered and two failed emails",
          len(outcomes["delivered"]) == 3 and len(outcomes["failed"]) == 2,
          str(outcomes))
    check("only delivered articles are persisted, exactly",
          delivered_ids == outcomes["delivered"],
          f"persisted={sorted(delivered_ids)} delivered={sorted(outcomes['delivered'])}")
    check("failed articles stay resendable",
          not (delivered_ids & outcomes["failed"]), str(sorted(outcomes["failed"])))
    output_text = gh_output.read_text(encoding="utf-8")
    check("partial success still reports state_changed=true",
          "state_changed=true" in output_text, output_text.strip())
    check("GITHUB_OUTPUT carries the R4-R6 aggregate counter contract",
          "selected_rows=5" in output_text
          and "deferred_rows=5" in output_text
          and "smtp_attempted_rows=5" in output_text
          and "smtp_accepted_rows=3" in output_text
          and "smtp_failed_rows=2" in output_text
          and "state_committed_rows=3" in output_text
          and "counters_reconciled=true" in output_text
          and "max_articles_cap=5" in output_text, output_text.strip())
    check("every send targets the Teams channel address only",
          set(recorder.attempts) == {FIXTURE_TEAMS_CHANNEL})
    check("one email per delivered article, distinct messages",
          len(recorder.messages) == 3
          and len({_parse_message(m["raw"])["subject"] for m in recorder.messages}) == 3)


def check_no_leaks(tmp: Path) -> None:
    state = tmp / "leak-state.json"
    direct_url = "https://yna.co.kr/news/no-log-leak"
    artifact = _write(
        tmp / "leak.json",
        _payload([
            _article(
                url=GOOGLE_AGGREGATOR_URL,
                external_url=direct_url,
            )
        ]),
    )
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
    check("direct publisher URL never appears in summary logs", direct_url not in logs)
    check("aggregator URL never appears in summary logs", GOOGLE_AGGREGATOR_URL not in logs)
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
    check("no representative-image field means no invented image",
          "<img" not in parsed["html"].lower())


# --------------------------------------------------------------------------
# 4. workflow wiring
# --------------------------------------------------------------------------
def check_workflow() -> None:
    watch = WATCH_WORKFLOW.read_text(encoding="utf-8")
    scheduled = WORKFLOW.read_text(encoding="utf-8")
    teams_if = _step_if(watch, TEAMS_STEP)
    teams_block = _step_block(watch, TEAMS_STEP)
    persist_block = _step_block(watch, PERSIST_STEP)
    build_block = _step_block(watch, "Build live news metadata (temp only)")

    check("watch Teams sender step exists", bool(teams_if) and bool(teams_block))
    check("watch state persistence step exists", bool(persist_block))

    # D7-AK-6C gate — TEAMS_AI_NEWS_WATCH is the send opt-in; shadow_alert_delta is not a gate.
    open_ctx = {
        "steps.build.outputs.live_ok": "true",
        "vars.TEAMS_AI_NEWS_WATCH": "1",
        "github.ref": "refs/heads/main",
        "github.event_name": "schedule",
        "github.event.inputs.force_dry_run": "",
        "github.event.inputs.production_canary": "",
        "github.event.inputs.canary_cap": "",
    }
    check("scheduled main + watch opt-in + live → step is eligible",
          _eval_gate(teams_if, open_ctx) is True, teams_if)
    check("watch gate no longer references shadow_alert_delta (fixture 16)",
          "shadow_alert_delta" not in teams_if, teams_if)
    check("non-main ref → step is skipped",
          _eval_gate(teams_if, {**open_ctx, "github.ref": "refs/heads/fix/x"}) is False)
    check("workflow_dispatch force_dry_run=true → step is skipped",
          _eval_gate(teams_if, {**open_ctx, "github.event_name": "workflow_dispatch",
                                "github.event.inputs.force_dry_run": "true"}) is False)
    manual_ctx = {
        **open_ctx,
        "vars.TEAMS_AI_NEWS_WATCH": "0",
        "github.event_name": "workflow_dispatch",
        "github.event.inputs.production_canary": "true",
        "github.event.inputs.canary_cap": "1",
    }
    check("explicit manual canary opens while scheduled watch remains disabled",
          _eval_gate(teams_if, manual_ctx) is True, teams_if)
    check("manual canary input false blocks the sender",
          _eval_gate(
              teams_if,
              {**manual_ctx, "github.event.inputs.production_canary": "false"},
          ) is False)
    check("manual canary cap other than exactly one blocks the sender",
          _eval_gate(
              teams_if,
              {**manual_ctx, "github.event.inputs.canary_cap": "2"},
          ) is False)
    check("watch opt-in disabled → step is skipped",
          _eval_gate(teams_if, {**open_ctx, "vars.TEAMS_AI_NEWS_WATCH": "0"}) is False)
    check("live collection failure → step is skipped",
          _eval_gate(teams_if, {**open_ctx, "steps.build.outputs.live_ok": "false"}) is False)

    check("watch Teams step invokes the article-level production sender",
          "python3 scripts/send_teams_ai_push.py" in teams_block)
    check("watch Teams step never invokes the SMTP digest sender",
          "send_email_alert.py" not in teams_block
          and "EMAIL_SEND_MODE" not in teams_block
          and "APPROVE_SEND_EMAIL" not in teams_block
          and "SEND_TO_TEAMS" not in teams_block)
    check("watch Teams step injects send mode and approval only here",
          "TEAMS_AI_PUSH_MODE: send" in teams_block
          and 'APPROVE_TEAMS_AI_PUSH: "true"' in teams_block
          and watch.count("TEAMS_AI_PUSH_MODE: send") == 1
          and watch.count('APPROVE_TEAMS_AI_PUSH: "true"') == 1)
    for secret_line in (
        "GMAIL_SMTP_USER: ${{ secrets.GMAIL_SMTP_USER }}",
        "GMAIL_SMTP_APP_PASSWORD: ${{ secrets.GMAIL_SMTP_APP_PASSWORD }}",
        "ALERT_EMAIL_FROM: ${{ secrets.ALERT_EMAIL_FROM }}",
        "TEAMS_CHANNEL_EMAIL: ${{ secrets.TEAMS_CHANNEL_EMAIL }}",
    ):
        check(f"watch Teams step injects email_channel secret: {secret_line.split(':')[0]}",
              secret_line in teams_block)
    check("watch Teams step uses the production state path",
          "TEAMS_PUSH_STATE_PATH: data/teams_push_state.json" in teams_block)
    check("watch Teams step consumes the validated temp brief directly",
          "TEAMS_ARTIFACT_FILE: ${{ runner.temp }}/validated-live-brief.json" in teams_block
          and "DELTA_ARTIFACT_FILE" not in teams_block)
    check("watch Teams step honours the per-run canary cap",
          "TEAMS_AI_PUSH_MAX_ARTICLES:" in teams_block)
    check("manual canary injects exactly one independent of repository variables",
          "github.event_name == 'workflow_dispatch' && '1'" in teams_block)
    check("scheduled rollout cap safely defaults to one",
          "vars.TEAMS_AI_NEWS_MAX_ARTICLES || '1'" in teams_block)
    check("no webhook secret is injected anywhere in the watch workflow",
          "secrets.TEAMS_WORKFLOW_WEBHOOK_URL" not in watch)

    # Fixture 18 — Telegram is never run by the watch (Teams-only owner).
    for tele in ("send_telegram", "send_scheduled_telegram", "TELEGRAM_AUTO_SEND",
                 "TELEGRAM_BOT_TOKEN"):
        check(f"watch never runs any Telegram path: {tele}", tele not in watch)

    # 10-minute best-effort schedule, serialized runs, manual dispatch preserved.
    check("watch runs on a 10-minute schedule", "cron: '7,17,27,37,47,57 * * * *'" in watch)
    check("watch documents GitHub best-effort scheduling", "best-effort" in watch)
    check("watch serializes runs with a concurrency group",
          "group: teams-ai-news-watch" in watch)
    check("watch preserves manual dispatch and the force-dry-run input",
          "workflow_dispatch:" in watch and "force_dry_run:" in watch)
    check("watch exposes explicit exactly-one production canary inputs",
          "production_canary:" in watch and "canary_cap:" in watch)

    # Lightweight — live news metadata to a temp file only; no dashboard baseline and
    # no docs/daily writes. Public refresh cannot consume the dedicated Teams queue.
    check("watch builds live news metadata to a temp file, not docs/daily",
          bool(build_block)
          and "build_static_dashboard.py" not in build_block
          and 'BRIEF_JSON="$RUNNER_TEMP/validated-live-brief.json"' in build_block
          and '--output-json "$BRIEF_JSON"' in build_block
          and "--output docs/daily" not in build_block)
    check("watch never compares against the mutable public dashboard",
          "detect_dashboard_alert_delta.py" not in watch
          and "docs/daily/dashboard-latest.html" not in watch)
    for heavy in ("docs/daily/latest.html", "docs/daily/operator-latest.html",
                  "python3 scripts/build_static_report.py", "actions/upload-pages",
                  "Publish to Pages"):
        check(f"watch does not run the heavy publish path: {heavy}", heavy not in watch)
    check("watch pushes only inside the state-persist step",
          watch.count("git push origin HEAD:main") == 1
          and "git push origin HEAD:main" in persist_block)

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
    check("persistence uses a history-preserving merge and no rebase",
          "git merge --no-edit origin/main" in persist_block
          and "git merge --abort" in persist_block
          and "git rebase" not in persist_block)
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

    # Mutual exclusion — the hourly scheduled-live-refresh no longer sends Teams (single
    # production owner), so the two workflows can never double-send the same article.
    check("scheduled-live-refresh no longer invokes the Teams sender (single owner)",
          "python3 scripts/send_teams_ai_push.py" not in scheduled)
    check("scheduled-live-refresh injects no Teams send mode/approval",
          "TEAMS_AI_PUSH_MODE: send" not in scheduled
          and 'APPROVE_TEAMS_AI_PUSH: "true"' not in scheduled)
    check("scheduled-live-refresh persists no Teams dedup state",
          "git add -- data/teams_push_state.json" not in scheduled
          and "TEAMS_PUSH_STATE_PATH: data/teams_push_state.json" not in scheduled)

    check("watch runs this verifier in its gate",
          watch.count("python3 scripts/verify_teams_ai_push_production.py") == 1)
    check("scheduled-live-refresh does not depend on the Watch verifier",
          "python3 scripts/verify_teams_ai_push_production.py" not in scheduled)
    semantic_command = (
        "python3 scripts/verify_teams_validated_brief_semantic_equivalence.py"
    )
    check("watch gates on validated-Brief semantic equivalence",
          watch.count(semantic_command) == 1)
    check("scheduled refresh does not depend on Watch semantic fixtures",
          semantic_command not in scheduled)
    check("scheduled refresh runs its isolated production gate",
          scheduled.count(
              "python3 scripts/verify_r4_ops7_refresh_production_gate.py"
          ) == 1)


def check_source_gate(tmp: Path) -> None:
    """R4-R9A — major-media-first source gate on the production delivery path.

    Deep scenario coverage (the three observed URLs, the full §10 fixture
    matrix) lives in scripts/verify_teams_major_media_gate.py; this block
    keeps the workflow-gating verifier proving the production contract."""
    # 1) Mixed supply: primary/secondary deliver immediately, specialist and
    #    trusted-other publishers are held, a neutral publisher never sends.
    mixed = [
        _article(article_key="gate-p1", title=_CAP_TITLES[0],
                 source="연합뉴스", url="https://yna.co.kr/gate/1"),
        _article(article_key="gate-p2", title=_CAP_TITLES[1],
                 source="KBS", url="https://kbs.co.kr/gate/2"),
        _article(article_key="gate-s1", title=_CAP_TITLES[2],
                 source="동아일보", url="https://donga.com/gate/3"),
        _article(article_key="gate-h1", title=_CAP_TITLES[7],
                 source="테크M",
                 url="https://www.techm.kr/news/articleView.html?idxno=990001"),
        _article(article_key="gate-h2", title=_CAP_TITLES[8],
                 source="전자신문", url="https://etnews.com/gate/5"),
        _article(article_key="gate-n1", title=_CAP_TITLES[9],
                 source="Reuters", url="https://publisher.example.test/gate/6"),
    ]
    state = tmp / "state-source-gate.json"
    mixed_summary, rec = _deliver(
        tmp, _payload(mixed), state,
        statuses=(250, 250, 250, 250),
        now="2026-08-04T09:00:00+09:00",
    )
    check("source gate: only Tier-A/Tier-B major-media articles deliver",
          mixed_summary["delivered_count"] == 4
          and rec.attempts == [FIXTURE_TEAMS_CHANNEL] * 4, str(mixed_summary))
    check("source gate: specialist/trusted articles are held, not deferred",
          mixed_summary["teams_specialist_held_rows"] == 1
          and mixed_summary["deferred_rows"] == 0, str(mixed_summary))
    check("source gate: neutral publisher is never selected",
          mixed_summary["source_gate_rejected_rows"] == 1, str(mixed_summary))
    check("source gate: selected tier counters are exact",
          mixed_summary["selected_primary_10_rows"] == 2
          and mixed_summary["selected_secondary_3_rows"] == 1
          and mixed_summary["selected_major_secondary_rows"] == 1
          and mixed_summary["selected_specialist_rows"] == 0, str(mixed_summary))
    check("source gate: counters reconcile with the gate partition",
          mixed_summary["counters_reconciled"] is True)
    saved = load_state(state)
    held_records = saved.get(HELD_SPECIALISTS_KEY) or {}
    check("source gate: held specialists persist without entering the sent ledger",
          len(held_records) == 1 and len(saved["article_ids"]) == 4,
          str(sorted(held_records)))
    check("source gate: held record carries identity and holdback fields",
          all(
              entry.get("first_seen_at") and entry.get("cluster_key")
              and entry.get("holdback_reason") and entry.get("source_tier")
              for entry in held_records.values()
          ), str(held_records))
    gh_output = tmp / "source-gate-output.txt"
    sender._write_github_output(str(gh_output), mixed_summary)
    gh_text = gh_output.read_text(encoding="utf-8")
    check("source gate: GITHUB_OUTPUT exposes the §11 counters",
          all(token in gh_text for token in (
              "raw_primary_10_rows=2",
              "raw_secondary_3_rows=1",
              "raw_major_secondary_rows=1",
              "raw_specialist_rows=1",
              "verified_major_secondary_rows=1",
              "teams_immediate_major_rows=4",
              "teams_specialist_held_rows=1",
              "teams_specialist_selected_rows=0",
              "selected_primary_10_rows=2",
              "selected_secondary_3_rows=1",
              "selected_major_secondary_rows=1",
              "selected_promoted_official_rows=0",
              "selected_specialist_rows=0",
              "source_gate_rejected_rows=1",
          )), gh_text)

    # 2) Specialist-only supply: zero selected, zero attempted; a dry run
    #    evaluates holdback without writing any state.
    specialists = [
        _article(article_key="gate-only-1", title=_CAP_TITLES[7],
                 source="테크M",
                 url="https://www.techm.kr/news/articleView.html?idxno=990002"),
        _article(article_key="gate-only-2", title=_CAP_TITLES[8],
                 source="테크월드",
                 url="https://www.epnc.co.kr/news/articleView.html?idxno=990003"),
    ]
    spec_state = tmp / "state-source-gate-specialist.json"
    summary, rec = _deliver(
        tmp, _payload(specialists), spec_state,
        now="2026-08-04T09:00:00+09:00",
    )
    check("specialist-only supply selects and attempts zero",
          summary["selected"] == 0 and summary["SMTP_attempted"] == 0
          and rec.attempts == [], str(summary))
    check("specialist-only supply is fully held",
          summary["teams_specialist_held_rows"] == 2
          and summary["teams_specialist_selected_rows"] == 0)
    dry_state = tmp / "state-source-gate-dry.json"
    summary, _rec = _deliver(
        tmp, _payload(specialists), dry_state,
        send=False, now="2026-08-04T09:00:00+09:00",
    )
    check("dry run evaluates holdback but writes no state",
          summary["selected"] == 0 and not dry_state.exists())

    # 3) R4-R9D — an aged unique TOP specialist with direct HDEC relevance is
    #    the exact case the old §6 fallback delivered. Automatic specialist
    #    fallback is now removed: it delivers zero, keeps its hold, and never
    #    enters the sent ledger. The system prefers zero delivery here.
    fallback_article = _article(
        article_key="gate-f1",
        title=_CAP_TITLES[10],
        summary="현대건설이 AI 데이터센터 EPC 계약을 체결했다.",
        source="테크M",
        url="https://www.techm.kr/news/articleView.html?idxno=990004",
    )
    aged_state_path = tmp / "state-source-gate-fallback.json"
    save_state(
        observe_held_specialist(
            empty_state(), fallback_article,
            cluster_key="", source="테크M", source_tier="neutral",
            holdback_reason="holdback_active", fallback_eligible=False,
            now="2026-08-04T07:00:00+09:00",
        ),
        aged_state_path,
    )
    summary, rec = _deliver(
        tmp, _payload([fallback_article]), aged_state_path,
        now="2026-08-04T09:05:00+09:00",
    )
    check("aged unique TOP specialist is never auto-selected (fallback removed)",
          summary["delivered_count"] == 0
          and summary["teams_specialist_selected_rows"] == 0
          and summary["specialist_rows_selected"] == 0
          and summary["specialist_automatic_fallback_removed"] is True, str(summary))
    fallback_saved = load_state(aged_state_path)
    check("undelivered specialist keeps its hold and never enters the sent ledger",
          len(fallback_saved.get(HELD_SPECIALISTS_KEY) or {}) == 1
          and not fallback_saved["article_ids"], str(fallback_saved))

    # 4) A fallback-blocked publisher never sends even under the same aged
    #    TOP conditions.
    blocked_article = _article(
        article_key="gate-b1",
        title="현대건설, 국가 AI 컴퓨팅센터 시공 계약 체결",
        summary="현대건설이 국가 AI 컴퓨팅센터 시공 계약을 체결했다.",
        source="뉴스워커",
        url="http://www.newsworker.co.kr/news/articleView.html?idxno=990005",
    )
    blocked_state_path = tmp / "state-source-gate-blocked.json"
    save_state(
        observe_held_specialist(
            empty_state(), blocked_article,
            cluster_key="", source="뉴스워커", source_tier="neutral",
            holdback_reason="fallback_blocked_publisher",
            fallback_eligible=False,
            now="2026-08-04T07:00:00+09:00",
        ),
        blocked_state_path,
    )
    summary, rec = _deliver(
        tmp, _payload([blocked_article]), blocked_state_path,
        now="2026-08-04T09:05:00+09:00",
    )
    check("fallback-blocked publisher never sends even when aged TOP",
          summary["delivered_count"] == 0
          and summary["teams_specialist_selected_rows"] == 0
          and rec.attempts == [], str(summary))


def main() -> int:
    production_sha_before = _sha(PRODUCTION_STATE)
    production_status_before = _state_status(PRODUCTION_STATE)
    print(f"production_state_sha256_before={production_sha_before}")
    check(
        "production state is absent or valid before verification",
        production_status_before in {"absent", "valid"},
        "malformed production state" if production_status_before == "malformed" else "",
    )
    check_sender_source()
    with tempfile.TemporaryDirectory(prefix="hdec-ak6a-") as raw:
        tmp = Path(raw)
        check_state_read_only_contract(tmp)
        check_fail_closed(tmp)
        check_delivery(tmp)
        check_cap_and_partial(tmp)
        check_source_gate(tmp)
        check_no_leaks(tmp)
    check_workflow()
    production_sha_after = _sha(PRODUCTION_STATE)
    production_status_after = _state_status(PRODUCTION_STATE)
    print(f"production_state_sha256_after={production_sha_after}")
    check(
        "production state remains absent or valid after verification",
        production_status_after in {"absent", "valid"},
        "malformed production state" if production_status_after == "malformed" else "",
    )
    check(
        "production state SHA256 is exactly unchanged after verification",
        production_sha_after == production_sha_before,
    )

    print(f"checks={CHECKS} failures={len(FAILURES)}")
    if FAILURES:
        for name in FAILURES:
            print(f"FAILED: {name}")
        return 1
    print("RESULT=D7-AK-6A_TEAMS_AI_PUSH_PRODUCTION_VERIFIER_PASS")
    print(
        "transport=email_channel network_calls=0 real_smtp_connections=0 "
        "actual_smtp_sends=0 production_state_writes=0"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
