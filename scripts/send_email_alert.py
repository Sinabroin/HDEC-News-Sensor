#!/usr/bin/env python3
"""Gated Gmail SMTP sender for executive email and Teams channel email.

Default execution is dry-run and performs no DNS, socket, SMTP, or delivery
operation.  Real delivery requires both EMAIL_SEND_MODE=send and
APPROVE_SEND_EMAIL=true.  Credentials and recipients are read from environment
variables that the GitHub Actions workflow maps exclusively from GitHub
Secrets; values are never printed.

SMTP acceptance only proves that the Gmail relay accepted the message.  It
does not prove inbox delivery or a Teams channel post.  Logs therefore report
the SMTP transport result separately from the unverified recipient-policy
state.
"""

from __future__ import annotations

import argparse
import os
import smtplib
import ssl
import sys
from dataclasses import dataclass
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import parseaddr
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
for _path in (ROOT, SCRIPTS_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from app.executive_digest import (  # noqa: E402
    ExecutiveDigest,
    build_executive_digest,
    render_email_html,
    render_email_text,
    render_subject,
)
from build_telegram_digest import build_digest_data  # noqa: E402

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_TIMEOUT_SECONDS = 30
DEFAULT_SEND_MODE = "dry_run"
APPROVAL_TRUE = {"1", "true", "yes", "approved"}
MAX_ALERT_RECIPIENTS = 10


@dataclass(frozen=True)
class DeliveryTarget:
    label: str
    address: str
    recipient_kind: str


@dataclass(frozen=True)
class DeliveryResult:
    label: str
    smtp_status: str
    smtp_code: int | None
    recipient_policy_status: str
    detail: str


def _true_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in APPROVAL_TRUE


def _valid_address(value: str) -> str:
    candidate = (value or "").strip()
    if not candidate or "\n" in candidate or "\r" in candidate:
        return ""
    _name, address = parseaddr(candidate)
    if address != candidate or address.count("@") != 1:
        return ""
    local, domain = address.rsplit("@", 1)
    if not local or "." not in domain or domain.startswith(".") or domain.endswith("."):
        return ""
    return address


def _address_list(value: str) -> list[str]:
    out: list[str] = []
    for item in (value or "").split(","):
        address = _valid_address(item)
        if not address:
            if item.strip():
                raise ValueError("ALERT_EMAIL_TO contains an invalid address")
            continue
        if address not in out:
            out.append(address)
    if len(out) > MAX_ALERT_RECIPIENTS:
        raise ValueError(f"ALERT_EMAIL_TO exceeds {MAX_ALERT_RECIPIENTS} recipients")
    return out


def resolve_targets() -> list[DeliveryTarget]:
    targets = [
        DeliveryTarget(
            label=f"alert_email_{index}",
            address=address,
            recipient_kind="mailbox",
        )
        for index, address in enumerate(
            _address_list(os.environ.get("ALERT_EMAIL_TO", "")),
            start=1,
        )
    ]
    if _true_env("SEND_TO_TEAMS"):
        teams_address = _valid_address(os.environ.get("TEAMS_CHANNEL_EMAIL", ""))
        if not teams_address:
            raise ValueError("TEAMS_CHANNEL_EMAIL is required when SEND_TO_TEAMS=true")
        targets.append(
            DeliveryTarget(
                label="teams_channel",
                address=teams_address,
                recipient_kind="teams_channel",
            )
        )
    return targets


def build_message(digest: ExecutiveDigest, from_address: str, target: DeliveryTarget) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = render_subject(digest)
    message["From"] = from_address
    message["To"] = target.address
    message.set_content(render_email_text(digest))
    message.add_alternative(render_email_html(digest), subtype="html")
    return message


def _smtp_password() -> str:
    return (
        os.environ.get("GMAIL_SMTP_APP_PASSWORD", "").strip()
        or os.environ.get("GMAIL_SMTP_PASSWORD", "").strip()
    )


def _smtp_failure(
    target: DeliveryTarget,
    detail: str,
    code: int | None = None,
) -> DeliveryResult:
    policy = (
        "possible_recipient_policy_rejection"
        if (
            target.recipient_kind == "teams_channel"
            and detail in {"recipient_rejected", "message_data_rejected"}
            and code
            and code >= 400
        )
        else "not_evaluated"
    )
    return DeliveryResult(
        label=target.label,
        smtp_status="rejected",
        smtp_code=code,
        recipient_policy_status=policy,
        detail=detail,
    )


def send_target(
    digest: ExecutiveDigest,
    target: DeliveryTarget,
    smtp_user: str,
    smtp_password: str,
    from_address: str,
    smtp_factory=smtplib.SMTP,
) -> DeliveryResult:
    """Send one target and retain SMTP response codes without logging response text."""

    message = build_message(digest, from_address, target)
    try:
        with smtp_factory(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT_SECONDS) as smtp:
            code, _response = smtp.ehlo()
            if code >= 400:
                return _smtp_failure(target, "ehlo_rejected", code)
            code, _response = smtp.starttls(context=ssl.create_default_context())
            if code >= 400:
                return _smtp_failure(target, "starttls_rejected", code)
            code, _response = smtp.ehlo()
            if code >= 400:
                return _smtp_failure(target, "post_tls_ehlo_rejected", code)
            smtp.login(smtp_user, smtp_password)

            code, _response = smtp.mail(from_address)
            if code >= 400:
                return _smtp_failure(target, "mail_from_rejected", code)
            code, _response = smtp.rcpt(target.address)
            if code >= 400:
                return _smtp_failure(target, "recipient_rejected", code)
            code, _response = smtp.data(message.as_bytes(policy=SMTP))
            if code >= 400:
                return _smtp_failure(target, "message_data_rejected", code)
            return DeliveryResult(
                label=target.label,
                smtp_status="accepted",
                smtp_code=code,
                recipient_policy_status="unverified",
                detail="smtp_relay_accepted",
            )
    except smtplib.SMTPAuthenticationError as exc:
        return _smtp_failure(target, "authentication_failed", getattr(exc, "smtp_code", None))
    except smtplib.SMTPRecipientsRefused:
        return _smtp_failure(target, "recipient_rejected")
    except smtplib.SMTPResponseException as exc:
        return _smtp_failure(target, "smtp_response_error", getattr(exc, "smtp_code", None))
    except (smtplib.SMTPException, OSError, TimeoutError, ValueError):
        return _smtp_failure(target, "smtp_transport_error")


def _print_result(result: DeliveryResult) -> None:
    code = str(result.smtp_code) if result.smtp_code is not None else "none"
    print(
        "Email delivery result: "
        f"target={result.label} smtp_status={result.smtp_status} smtp_code={code} "
        f"recipient_policy_status={result.recipient_policy_status} detail={result.detail}"
    )
    if result.smtp_status == "accepted":
        print(
            "Email receipt status: "
            f"target={result.label} delivery_to_inbox_or_teams=unverified "
            "(SMTP acceptance is not recipient-policy confirmation)"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="HDEC executive email + Teams channel-email sender (default dry-run)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="force preview only; no SMTP connection or delivery",
    )
    args = parser.parse_args(argv)

    digest = build_executive_digest(build_digest_data())
    mode = os.environ.get("EMAIL_SEND_MODE", "").strip().lower() or DEFAULT_SEND_MODE
    approved = _true_env("APPROVE_SEND_EMAIL")
    should_send = mode == "send" and approved and not args.dry_run

    if not should_send:
        print(render_subject(digest))
        print(render_email_text(digest))
        print(
            "Email send status: dry_run "
            f"(mode={mode}, approved={'true' if approved else 'false'}, smtp_connections=0)"
        )
        return 0

    if os.environ.get("GITHUB_ACTIONS", "").strip().lower() != "true":
        print("ERROR: real Gmail SMTP delivery is restricted to GitHub Actions", file=sys.stderr)
        return 2

    smtp_user = _valid_address(os.environ.get("GMAIL_SMTP_USER", ""))
    smtp_password = _smtp_password()
    from_address = _valid_address(os.environ.get("ALERT_EMAIL_FROM", ""))
    if not smtp_user:
        print("ERROR: GMAIL_SMTP_USER is missing or invalid", file=sys.stderr)
        return 2
    if not smtp_password:
        print("ERROR: Gmail SMTP credential is missing", file=sys.stderr)
        return 2
    if not from_address:
        print("ERROR: ALERT_EMAIL_FROM is missing or invalid", file=sys.stderr)
        return 2
    try:
        targets = resolve_targets()
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if not targets:
        print("ERROR: ALERT_EMAIL_TO has no recipients", file=sys.stderr)
        return 2

    print(
        "Email send gate: approved "
        f"(targets={len(targets)}, teams_enabled={'true' if _true_env('SEND_TO_TEAMS') else 'false'})"
    )
    results = [
        send_target(digest, target, smtp_user, smtp_password, from_address)
        for target in targets
    ]
    for result in results:
        _print_result(result)

    failed = sum(result.smtp_status != "accepted" for result in results)
    accepted = len(results) - failed
    print(
        f"Email SMTP summary: accepted={accepted} rejected={failed} "
        "recipient_delivery_verified=0"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
