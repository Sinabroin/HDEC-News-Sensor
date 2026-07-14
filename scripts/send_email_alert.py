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

D7-AJ-2 — when DELTA_ARTIFACT_FILE points at the shared delta payload the sender
renders the delta-first, real-KST alert (app/delta_alert) instead of the daily
07:00 brief, consuming the same file Telegram uses (no re-fetch).  An invalid
artifact fails closed; a valid-but-empty delta sends nothing.

D7-AJ-3 — for the delta alert Teams is delivered as an Adaptive Card posted to
the TEAMS_WORKFLOW_WEBHOOK_URL secret (server-side only, never printed or placed
in any artifact).  A successful card post suppresses the Teams channel email (no
duplicate); a missing or failing webhook falls back to that email.  Mailbox
recipients (ALERT_EMAIL_TO) are unaffected.
"""

from __future__ import annotations

import argparse
import json
import os
import smtplib
import ssl
import sys
import urllib.error
import urllib.request
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

from app import delta_alert  # noqa: E402
from app.delta_alert import DeltaAlert, InvalidDeltaArtifact, load_delta_alert  # noqa: E402
from app.executive_digest import (  # noqa: E402
    ExecutiveDigest,
    build_executive_digest,
    render_email_html,
    render_email_text,
    render_subject,
)
from build_telegram_digest import build_digest_data  # noqa: E402
from send_telegram import resolve_dashboard_url, resolve_report_url  # noqa: E402

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


def _render_message(payload) -> tuple[str, str, str]:
    """(subject, text, html)을 payload 유형에 맞게 렌더한다.

    DeltaAlert(시간당 변동 알림)면 delta_alert(실제 KST · 변동 우선)로, ExecutiveDigest
    (일일 브리프)면 executive_digest(07:00 아침 브리프)로 렌더한다 — 두 계약은 서로 침범하지
    않는다. build_message/send_target 시그니처(검증기 계약)는 그대로 유지된다."""
    if isinstance(payload, DeltaAlert):
        return (
            delta_alert.render_subject(payload),
            delta_alert.render_email_text(payload),
            delta_alert.render_email_html(payload),
        )
    return render_subject(payload), render_email_text(payload), render_email_html(payload)


def build_message(payload, from_address: str, target: DeliveryTarget) -> EmailMessage:
    subject, text_body, html_body = _render_message(payload)
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = from_address
    message["To"] = target.address
    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")
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


def resolve_delta_alert():
    """DELTA_ARTIFACT_FILE가 있으면 검증된 DeltaAlert를 돌려준다 (없으면 None).

    D7-AJ-2 — 시간당 변동 알림은 이 공유 아티팩트가 단일 진실원(재수집 없음)이다. Telegram과
    같은 파일을 소비해 불일치·재수집이 없다. 깨졌거나 스키마를 위반하면 fail-closed로 발송을
    중단한다(가짜/빈 알림 위장 금지). CTA URL은 기존 REPORT_URL/DASHBOARD_URL 계약을 재사용한다."""
    path = os.environ.get("DELTA_ARTIFACT_FILE", "").strip()
    if not path:
        return None
    report_url = resolve_report_url()
    dashboard_url = resolve_dashboard_url(report_url)
    try:
        return load_delta_alert(path, dashboard_url=dashboard_url, report_url=report_url)
    except InvalidDeltaArtifact as exc:
        print(f"ERROR: delta artifact invalid — 발송 중단(fail-closed): {type(exc).__name__}",
              file=sys.stderr)
        raise SystemExit(2)


TEAMS_WEBHOOK_ENV = "TEAMS_WORKFLOW_WEBHOOK_URL"
WEBHOOK_TIMEOUT_SECONDS = 20


def resolve_teams_webhook_url() -> str:
    """Teams Workflows(Power Automate) webhook URL을 env(secret)에서 읽는다.

    https만 허용하고, 잘못된 값이면 빈 문자열을 돌려줘 이메일 fallback으로 흐르게 한다.
    이 값은 backend 전용 비밀값이다 — 어떤 로그/artifact/응답에도 출력하지 않는다(rules.md §4)."""
    url = os.environ.get(TEAMS_WEBHOOK_ENV, "").strip()
    return url if url.lower().startswith("https://") else ""


def post_teams_card(webhook_url: str, card: dict, opener=urllib.request.urlopen) -> tuple[bool, str]:
    """Adaptive Card를 Teams webhook에 POST한다 → (성공여부, 상태 라벨).

    webhook URL·응답 본문은 절대 출력/반환하지 않는다 — HTTP 상태코드 범주만 라벨로 돌려준다
    (accepted_2xx / rejected_Nxx / http_error_N / transport_error). opener는 검증기가 네트워크
    없이 주입할 수 있게 분리한다(send_target의 smtp_factory와 동일한 격리 패턴)."""
    data = json.dumps(card, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        webhook_url, data=data, method="POST",
        headers={"Content-Type": "application/json"})
    try:
        with opener(request, timeout=WEBHOOK_TIMEOUT_SECONDS) as response:
            code = int(getattr(response, "status", None) or response.getcode())
    except urllib.error.HTTPError as exc:
        return False, f"http_error_{exc.code}"
    except (urllib.error.URLError, OSError, TimeoutError, ValueError):
        return False, "transport_error"
    if 200 <= code < 300:
        return True, f"accepted_{code}"
    return False, f"rejected_{code}"


def plan_teams_delivery(delta, targets, webhook_url, poster=post_teams_card):
    """Teams webhook-first 전달 계획 → (card_delivered, email_targets, status).

    · delta 알림이고 webhook이 설정되면 Adaptive Card를 먼저 POST한다.
    · 성공: Teams 채널 이메일 target을 제거한다(중복 발송 0). 메일박스 수신자는 그대로 유지.
    · 실패/미설정: 원래 target을 그대로 두어 Teams 채널 이메일로 fallback한다.
    네트워크·비밀값은 poster가 캡슐화한다 — 이 함수는 URL을 반환/출력하지 않는다."""
    if delta is None or not webhook_url:
        return False, list(targets), "webhook_skipped"
    delivered, status = poster(webhook_url, delta_alert.build_teams_card(delta))
    if not delivered:
        return False, list(targets), status
    email_targets = [t for t in targets if t.recipient_kind != "teams_channel"]
    return True, email_targets, status


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

    # D7-AJ-2 — 공유 delta 아티팩트가 있으면 재수집 없이 그 파일만 렌더(변동 우선·실제 KST).
    # 없으면 기존 일일 executive 브리프(07:00 아침 브리프)를 만든다. 깨진 아티팩트는 fail-closed.
    # CTA URL은 기존 REPORT_URL/DASHBOARD_URL 계약(send_telegram)을 그대로 재사용한다.
    delta = resolve_delta_alert()
    if delta is not None:
        payload = delta
    else:
        report_url = resolve_report_url()
        dashboard_url = resolve_dashboard_url(report_url)
        payload = build_executive_digest(
            build_digest_data(),
            dashboard_url=dashboard_url,
            report_url=report_url,
        )
    subject, text_body, _html_body = _render_message(payload)
    mode = os.environ.get("EMAIL_SEND_MODE", "").strip().lower() or DEFAULT_SEND_MODE
    approved = _true_env("APPROVE_SEND_EMAIL")
    should_send = mode == "send" and approved and not args.dry_run

    # 유효하지만 보낼 신규 변동이 없으면(alert_delta=false) 발송 0건 (graceful no-send).
    if delta is not None and not delta.sendable:
        print(subject)
        print("Email send status: no_delta (alert_delta=false, smtp_connections=0)")
        return 0

    if not should_send:
        print(subject)
        print(text_body)
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

    # D7-AJ-3 — Teams는 Adaptive Card webhook을 우선 시도하고, 미설정/실패면 채널 이메일로
    # fallback한다. webhook 성공 시 Teams 채널 이메일은 보내지 않는다(같은 run 중복 0).
    # webhook URL은 어떤 로그에도 출력하지 않는다 — 상태 라벨만 노출한다(rules.md §4).
    webhook_url = resolve_teams_webhook_url()
    card_delivered, email_targets, teams_status = plan_teams_delivery(
        delta, targets, webhook_url)
    if delta is not None and webhook_url:
        print(f"Teams delivery: channel=workflow_webhook_card status={teams_status} "
              f"delivered={'true' if card_delivered else 'false'}")
        if not card_delivered:
            print("Teams delivery: webhook 실패 — Teams 채널 이메일로 fallback")

    if not email_targets and not card_delivered:
        print("ERROR: ALERT_EMAIL_TO has no recipients", file=sys.stderr)
        return 2

    print(
        "Email send gate: approved "
        f"(targets={len(email_targets)}, "
        f"teams_enabled={'true' if _true_env('SEND_TO_TEAMS') else 'false'}, "
        f"teams_card={'true' if card_delivered else 'false'})"
    )
    results = [
        send_target(payload, target, smtp_user, smtp_password, from_address)
        for target in email_targets
    ]
    for result in results:
        _print_result(result)

    failed = sum(result.smtp_status != "accepted" for result in results)
    accepted = len(results) - failed
    print(
        f"Email SMTP summary: accepted={accepted} rejected={failed} "
        f"recipient_delivery_verified=0 teams_card_delivered={'1' if card_delivered else '0'}"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
