#!/usr/bin/env python3
"""Article-level Teams AI production sender (D7-AK-6A · email_channel transport).

This entrypoint wires the already-verified leaves into the scheduled production
path — it adds delivery, and nothing else:

* ``app.teams_ai_push``    — Teams-only AI topic classification, importance
  mapping, one message per article (max three), and the per-article email body.
* ``app.teams_push_state`` — persistent dedup over article id / normalized URL /
  title fingerprint / event cluster, including material-update re-send.

Selection and dedup logic is reused, never re-implemented here. The only new
behaviour is: deliver one Teams channel email per eligible article, then record
success.

Production transport is ``email_channel``: exactly one email per eligible article
is sent to the Teams channel address (``TEAMS_CHANNEL_EMAIL``) over the verified
Gmail SMTP contract owned by ``scripts/send_email_alert.py`` (reused, not
duplicated). This is the official production transport, not a fallback. Several
articles are never merged into one digest. An article counts as delivered only on
an SMTP ``250 accepted`` response, and only delivered articles are recorded, so a
partial failure keeps delivered articles recorded and leaves failed ones
resendable on the next run.

Default execution is dry-run and performs zero network operations. A real send
requires all of GITHUB_ACTIONS=true, TEAMS_AI_PUSH_MODE=send,
APPROVE_TEAMS_AI_PUSH=true, complete Gmail SMTP credentials plus a Teams channel
address, and a ``live-delta`` artifact whose shadow_alert_delta gate is open. Any
other state fails closed before a message is built.

The Teams Workflows webhook (``TEAMS_WORKFLOW_WEBHOOK_URL``) is a reserved,
currently-inactive optional transport: it is never a required condition and its
absence never fails a run. SMTP credentials, recipient/channel addresses, and
article URLs are never printed — logs carry only counts, a non-reversible article
reference hash, and an SMTP status category.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass, replace
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
for _path in (REPO_ROOT, SCRIPTS_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from app.teams_ai_push import (  # noqa: E402
    render_article_email,
    select_teams_push_from_artifact,
)
from app.teams_push_state import (  # noqa: E402
    InvalidTeamsPushState,
    article_identity,
    evaluate_dedup,
    filter_unsent_candidates,
    load_state,
    persist_after_success,
    resolve_state_path,
)

# Reuse the single proven Gmail SMTP contract — never a second copy of the handshake.
from send_email_alert import (  # noqa: E402
    DeliveryTarget,
    _smtp_password,
    _valid_address,
    deliver_email_message,
)

WEBHOOK_ENV = "TEAMS_WORKFLOW_WEBHOOK_URL"
SMTP_USER_ENV = "GMAIL_SMTP_USER"
FROM_ENV = "ALERT_EMAIL_FROM"
TEAMS_CHANNEL_ENV = "TEAMS_CHANNEL_EMAIL"
DEFAULT_MODE = "dry_run"
SEND_MODE = "send"
APPROVAL_TRUE = {"1", "true", "yes", "approved"}


class FailClosed(RuntimeError):
    """Abort before any network call. ``reason`` is a safe, value-free label."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class EmailChannelCredentials:
    """Gmail SMTP + Teams channel address, resolved from secrets (never printed)."""

    smtp_user: str = ""
    smtp_password: str = ""
    from_address: str = ""
    teams_address: str = ""

    @property
    def complete(self) -> bool:
        return bool(
            self.smtp_user and self.smtp_password and self.from_address and self.teams_address
        )


def _true_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in APPROVAL_TRUE


def resolve_email_channel_credentials() -> EmailChannelCredentials:
    """Resolve the email_channel credentials from env (secrets only).

    Addresses are validated with the same helpers the proven email sender uses and
    are never printed. Missing/invalid values yield empty fields so the caller fails
    closed before any message is built."""
    return EmailChannelCredentials(
        smtp_user=_valid_address(os.environ.get(SMTP_USER_ENV, "")),
        smtp_password=_smtp_password(),
        from_address=_valid_address(os.environ.get(FROM_ENV, "")),
        teams_address=_valid_address(os.environ.get(TEAMS_CHANNEL_ENV, "")),
    )


def resolve_webhook_url() -> str:
    """Reserved optional Teams Workflows webhook transport (currently inactive).

    Returned https-only for observability, but it is never a required condition and
    its absence never fails a run — the production transport is email_channel. This
    is a backend-only value: it is never printed to any log or artifact (rules.md §4)."""
    url = os.environ.get(WEBHOOK_ENV, "").strip()
    return url if url.lower().startswith("https://") else ""


def article_ref(article: object) -> str:
    """Stable, non-reversible article reference safe for operational logs."""
    identity = article_identity(article)
    basis = (
        identity["article_id"]
        or identity["normalized_url"]
        or identity["title_fingerprint"]
    )
    if not basis:
        return "unknown"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:12]


def load_artifact(path: Path) -> Mapping[str, Any]:
    if not path.exists():
        raise FailClosed("artifact_missing")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FailClosed("artifact_unreadable") from exc
    if not isinstance(payload, Mapping):
        raise FailClosed("artifact_root_not_object")
    if " ".join(str(payload.get("source") or "").split()) != "live-delta":
        raise FailClosed("artifact_not_live_delta")
    if payload.get("shadow_alert_delta") is not True:
        raise FailClosed("shadow_alert_delta_closed")
    return payload


def check_send_preconditions(credentials: EmailChannelCredentials) -> None:
    """Every real-send requirement, evaluated before any message is built."""
    if os.environ.get("GITHUB_ACTIONS", "").strip().lower() != "true":
        raise FailClosed("not_github_actions")
    if not _true_env("APPROVE_TEAMS_AI_PUSH"):
        raise FailClosed("send_not_approved")
    if not credentials.smtp_user:
        raise FailClosed("smtp_user_missing")
    if not credentials.smtp_password:
        raise FailClosed("smtp_credential_missing")
    if not credentials.from_address:
        raise FailClosed("from_address_missing")
    if not credentials.teams_address:
        raise FailClosed("teams_channel_missing")


def send_article_email(
    candidate,
    *,
    alert_context: Mapping[str, Any],
    credentials: EmailChannelCredentials,
    detected_at: str,
    smtp_factory=None,
) -> tuple[bool, str, int | None]:
    """Deliver exactly one Teams channel email for one article → (ok, status, smtp_code).

    ``ok`` is True only on an SMTP ``250 accepted`` response. Neither the recipient
    address nor the article URL is returned or printed; ``status`` is a coarse
    category label. ``smtp_factory`` is injectable for offline verification."""
    subject, text_body, html_body = render_article_email(
        alert_context, candidate, detected_at=detected_at
    )
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = credentials.from_address
    message["To"] = credentials.teams_address
    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")

    target = DeliveryTarget(
        label="teams_channel",
        address=credentials.teams_address,
        recipient_kind="teams_channel",
    )
    result = deliver_email_message(
        message,
        target,
        credentials.smtp_user,
        credentials.smtp_password,
        credentials.from_address,
        smtp_factory=smtp_factory,
    )
    ok = result.smtp_status == "accepted"
    if ok:
        status = f"accepted_{result.smtp_code}"
    else:
        status = result.detail or f"rejected_{result.smtp_code}"
    return ok, status, result.smtp_code


def deliver(
    *,
    artifact_path: Path,
    state_path: Path,
    credentials: EmailChannelCredentials,
    should_send: bool,
    dashboard_url: str = "",
    report_url: str = "",
    detected_at: str = "",
    smtp_factory=None,
) -> dict[str, Any]:
    """Select, dedup, and deliver at most three article emails.

    Each article is handled independently: one SMTP failure never skips the
    remaining articles, and only delivered (250 accepted) articles reach persistent
    state — failed ones stay resendable."""
    payload = load_artifact(artifact_path)
    try:
        state = load_state(state_path)
    except InvalidTeamsPushState as exc:
        raise FailClosed("state_invalid") from exc

    candidates = select_teams_push_from_artifact(payload)
    # Contract reuse: the same helper the dry-run path uses. Its decisions are the
    # baseline; each article is then re-checked against the state as it evolves within
    # this run so two near-identical articles cannot both be delivered.
    _accepted, _baseline = filter_unsent_candidates(state, candidates)

    alert_context = dict(payload)
    alert_context["dashboard_url"] = dashboard_url
    alert_context["report_url"] = report_url
    resolved_detected_at = detected_at or str(
        payload.get("generated_at") or payload.get("generated_kst") or ""
    )

    records: list[dict[str, Any]] = []
    blocked = attempted = delivered = failed = 0
    state_changed = False

    for candidate in candidates:
        ref = article_ref(candidate.article)
        decision = evaluate_dedup(
            state,
            candidate.article,
            cluster_key=candidate.cluster_key,
            signature=candidate.material_signature,
            is_material_update=bool(candidate.is_update),
        )
        if not decision.send_allowed:
            blocked += 1
            records.append(
                {
                    "article_ref": ref,
                    "outcome": "dedup_blocked",
                    "dedup_reason": decision.reason,
                    "status": "no_request",
                }
            )
            continue

        if not should_send:
            records.append(
                {
                    "article_ref": ref,
                    "outcome": "dry_run_no_send",
                    "dedup_reason": decision.reason,
                    "status": "no_request",
                    "is_update": decision.is_update,
                }
            )
            continue

        attempted += 1
        ok, status, _code = send_article_email(
            replace(candidate, is_update=decision.is_update),
            alert_context=alert_context,
            credentials=credentials,
            detected_at=resolved_detected_at,
            smtp_factory=smtp_factory,
        )
        if ok:
            delivered += 1
            state = persist_after_success(
                state,
                candidate.article,
                path=state_path,
                cluster_key=candidate.cluster_key,
                signature=candidate.material_signature,
                importance=candidate.importance.level,
                source=str(candidate.article.get("source") or ""),
                send_succeeded=True,
                is_update=decision.is_update,
                delivery_id=f"teams_ai_push:{ref}",
            )
            state_changed = True
        else:
            failed += 1
        records.append(
            {
                "article_ref": ref,
                "outcome": "delivered" if ok else "failed",
                "dedup_reason": decision.reason,
                "status": status,
                "is_update": decision.is_update,
            }
        )

    return {
        "mode": "send" if should_send else "dry_run_no_send",
        "candidate_count": len(candidates),
        "dedup_blocked_count": blocked,
        "attempted_count": attempted,
        "delivered_count": delivered,
        "failed_count": failed,
        "state_changed": state_changed,
        "records": records,
    }


def _write_github_output(path: str, summary: Mapping[str, Any]) -> None:
    if not path:
        return
    lines = (
        f"state_changed={'true' if summary.get('state_changed') else 'false'}",
        f"teams_candidate_count={int(summary.get('candidate_count') or 0)}",
        f"teams_dedup_blocked_count={int(summary.get('dedup_blocked_count') or 0)}",
        f"teams_attempted_count={int(summary.get('attempted_count') or 0)}",
        f"teams_delivered_count={int(summary.get('delivered_count') or 0)}",
        f"teams_failed_count={int(summary.get('failed_count') or 0)}",
    )
    try:
        with Path(path).open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    except OSError:
        print("WARN: could not write GITHUB_OUTPUT summary", file=sys.stderr)


def _print_summary(summary: Mapping[str, Any]) -> None:
    for record in summary.get("records", ()):
        print(
            "Teams AI email: "
            f"article={record['article_ref']} outcome={record['outcome']} "
            f"dedup={record['dedup_reason']} status={record['status']}"
        )
    print(
        "Teams AI push summary: transport=email_channel "
        f"mode={summary['mode']} "
        f"candidates={summary['candidate_count']} "
        f"dedup_blocked={summary['dedup_blocked_count']} "
        f"attempted={summary['attempted_count']} "
        f"delivered={summary['delivered_count']} "
        f"failed={summary['failed_count']} "
        f"state_changed={'true' if summary['state_changed'] else 'false'}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Article-level Teams AI channel-email sender (default dry-run)"
    )
    parser.add_argument("--artifact", default=os.environ.get("DELTA_ARTIFACT_FILE", ""))
    parser.add_argument("--state", default=os.environ.get("TEAMS_PUSH_STATE_PATH", ""))
    parser.add_argument("--dashboard-url", default=os.environ.get("DASHBOARD_URL", ""))
    parser.add_argument("--report-url", default=os.environ.get("REPORT_URL", ""))
    parser.add_argument("--detected-at", default="")
    parser.add_argument("--github-output", default=os.environ.get("GITHUB_OUTPUT", ""))
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="force preview only; no email request and no state write",
    )
    args = parser.parse_args(argv)

    if not args.artifact:
        print("ERROR: DELTA_ARTIFACT_FILE/--artifact is required", file=sys.stderr)
        _write_github_output(args.github_output, {"state_changed": False})
        return 2

    mode = os.environ.get("TEAMS_AI_PUSH_MODE", "").strip().lower() or DEFAULT_MODE
    send_requested = mode == SEND_MODE and not args.dry_run
    credentials = resolve_email_channel_credentials()
    webhook_url = resolve_webhook_url()
    # The webhook is a reserved, inactive optional transport — logged for
    # observability only, never gating and never printed as a value.
    print(
        "Teams AI push transport: production=email_channel "
        f"optional_webhook={'configured' if webhook_url else 'absent'}"
    )

    try:
        if send_requested:
            check_send_preconditions(credentials)
        summary = deliver(
            artifact_path=Path(args.artifact).expanduser().resolve(),
            state_path=resolve_state_path(args.state or None).expanduser().resolve(),
            credentials=credentials,
            should_send=send_requested,
            dashboard_url=args.dashboard_url,
            report_url=args.report_url,
            detected_at=args.detected_at,
        )
    except FailClosed as exc:
        print(
            f"ERROR: Teams AI push failed closed: {exc.reason} "
            "(email_sends=0 state_writes=0)",
            file=sys.stderr,
        )
        _write_github_output(args.github_output, {"state_changed": False})
        return 2

    _write_github_output(args.github_output, summary)
    _print_summary(summary)
    if summary["failed_count"]:
        print(
            f"ERROR: {summary['failed_count']} Teams AI email(s) failed to deliver",
            file=sys.stderr,
        )
        return 1
    print("RESULT=D7-AK-6A_TEAMS_AI_PUSH_COMPLETE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
