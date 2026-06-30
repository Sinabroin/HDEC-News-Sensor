#!/usr/bin/env python3
"""Offline verifier for D7-AD executive email and Teams channel-email delivery."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "email-alert.yml"
SENDER = ROOT / "scripts" / "send_email_alert.py"
PUBLIC_HTML = [ROOT / "docs" / "index.html", *sorted((ROOT / "docs" / "daily").glob("*.html"))]

for _path in (ROOT, ROOT / "scripts"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from app.executive_digest import (  # noqa: E402
    build_executive_digest,
    render_email_html,
    render_email_text,
    render_subject,
    render_telegram,
)
import send_email_alert as sender  # noqa: E402

_failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        _failures.append(name)


def _fixture() -> dict:
    return {
        "date_kst": "2026-06-29",
        "news_data_mode": "live",
        "executive_one_liner": "검증용 요약",
        "hdec_signals": [
            {
                "article_id": "h1",
                "title": "현대건설 데이터센터 EPC 우선협상",
                "category_label": "현대건설 연관",
                "url": "https://news.example/h1",
            }
        ],
        "top_signals": [
            {
                "article_id": "a1",
                "title": "AI 데이터센터 전력 확보 경쟁",
                "category_label": "AI 데이터센터·전력 인프라",
                "url": "https://news.example/a1",
            }
        ],
        "biz_signals": [
            {
                "article_id": "b1",
                "title": "중동 EPC 발주 조건 변화",
                "category_label": "중동·해외 수주 환경",
                "url": "https://news.example/b1",
            }
        ],
        "risk_signals": [
            {
                "article_id": "r1",
                "title": "건설현장 안전 규제 강화",
                "category_label": "건설현장 안전·중대재해",
                "url": "https://news.example/r1",
            }
        ],
    }


def check_renderer() -> None:
    digest = build_executive_digest(_fixture())
    sentences = [digest.headline, digest.situation, digest.hdec_angle, digest.watch]
    check("공통 digest가 headline/situation/hdec_angle/watch 4문장", len(sentences) == 4)
    check("첫 문장이 결론형", digest.headline.endswith("합니다."))
    check("현대건설 관점 문장 존재", digest.hdec_angle.startswith("현대건설 관점에서는"))
    check("마지막 문장이 오늘 액션", digest.watch.startswith("오늘은 ") and digest.watch.endswith("됩니다."))
    check("핵심 링크 1~3개", 1 <= len(digest.links) <= 3, str(len(digest.links)))
    check(
        "서술 문단은 기사 제목 나열이 아님",
        all(link.title not in " ".join(sentences) for link in digest.links),
    )

    telegram = render_telegram(digest)
    email_text = render_email_text(digest)
    email_html = render_email_html(digest)
    check("Telegram에 07:00 헤더와 공통 4문장", "[오늘 07:00 브리프]" in telegram
          and all(sentence in telegram for sentence in sentences))
    check("Telegram 링크 3개 이하", telegram.count('<a href="') <= 3)
    check("Email text에 공통 4문장", all(sentence in email_text for sentence in sentences))
    check("Email HTML에 공통 4문장", all(sentence in email_html for sentence in sentences))
    check(
        "Email subject 계약",
        render_subject(digest).startswith("[HDEC News Sensor] 오늘 07:00 브리프 — "),
    )

    mailbox = sender.DeliveryTarget("mailbox", "person@example.com", "mailbox")
    teams = sender.DeliveryTarget("teams_channel", "channel@example.teams.ms", "teams_channel")
    mail_message = sender.build_message(digest, "sender@example.com", mailbox)
    teams_message = sender.build_message(digest, "sender@example.com", teams)
    mail_text = mail_message.get_body(preferencelist=("plain",)).get_content()
    teams_text = teams_message.get_body(preferencelist=("plain",)).get_content()
    check("일반 이메일과 Teams 채널 이메일 본문 동일", mail_text == teams_text)


class _FakeSMTP:
    rcpt_code = 250
    data_code = 250

    def __init__(self, host, port, timeout):
        self.host = host
        self.port = port
        self.timeout = timeout

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def ehlo(self):
        return 250, b"ok"

    def starttls(self, context):
        return 220, b"ready"

    def login(self, user, password):
        return 235, b"authenticated"

    def mail(self, from_address):
        return 250, b"ok"

    def rcpt(self, address):
        return self.rcpt_code, b"recipient"

    def data(self, payload):
        return self.data_code, b"queued"


class _RejectTeamsSMTP(_FakeSMTP):
    rcpt_code = 550


def check_smtp_classification() -> None:
    digest = build_executive_digest(_fixture())
    target = sender.DeliveryTarget("alert_email_1", "person@example.com", "mailbox")
    accepted = sender.send_target(
        digest,
        target,
        "sender@example.com",
        "fixture-credential",
        "sender@example.com",
        smtp_factory=_FakeSMTP,
    )
    check("SMTP 250 응답은 accepted로 기록", accepted.smtp_status == "accepted"
          and accepted.smtp_code == 250)
    check("SMTP accepted와 실제 수신을 분리", accepted.recipient_policy_status == "unverified")

    teams = sender.DeliveryTarget("teams_channel", "channel@example.teams.ms", "teams_channel")
    rejected = sender.send_target(
        digest,
        teams,
        "sender@example.com",
        "fixture-credential",
        "sender@example.com",
        smtp_factory=_RejectTeamsSMTP,
    )
    check("Teams SMTP 550은 rejected로 기록", rejected.smtp_status == "rejected"
          and rejected.smtp_code == 550)
    check(
        "Teams 정책 차단 가능성을 transport 결과와 분리",
        rejected.recipient_policy_status == "possible_recipient_policy_rejection",
    )


def _clean_env() -> dict[str, str]:
    # GITHUB_ACTIONS는 실제 러너가 항상 "true"로 주입한다. 이 verifier는 sender의
    # 발송 게이트를 통제된 env에서 검증하는데(특히 "Actions 밖 + 두 gate가 열려도
    # 차단" 경로), 러너의 ambient GITHUB_ACTIONS가 그대로 새어들면 CI에서만 그
    # 케이스를 재현하지 못해 거짓 실패한다. 따라서 send gate가 읽는 키와 함께
    # GITHUB_ACTIONS도 baseline에서 제거해 각 케이스가 결정적으로 동작하게 한다.
    blocked = {
        "EMAIL_SEND_MODE",
        "APPROVE_SEND_EMAIL",
        "SEND_TO_TEAMS",
        "GMAIL_SMTP_USER",
        "GMAIL_SMTP_APP_PASSWORD",
        "GMAIL_SMTP_PASSWORD",
        "ALERT_EMAIL_TO",
        "ALERT_EMAIL_FROM",
        "TEAMS_CHANNEL_EMAIL",
        "GITHUB_ACTIONS",
    }
    return {key: value for key, value in os.environ.items() if key not in blocked}


def check_default_dry_run() -> None:
    def run(extra: dict[str, str] | None = None) -> subprocess.CompletedProcess:
        env = _clean_env()
        env.update(extra or {})
        return subprocess.run(
            [sys.executable, str(SENDER)],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=120,
        )

    proc = run()
    output = (proc.stdout or "") + (proc.stderr or "")
    check("sender 기본 실행 성공", proc.returncode == 0, output[-250:])
    check("sender 기본값 dry-run/SMTP 연결 0건", "Email send status: dry_run" in output
          and "smtp_connections=0" in output)
    unapproved = run({"EMAIL_SEND_MODE": "send", "APPROVE_SEND_EMAIL": "false"})
    check("send mode만 설정해도 미승인이면 dry-run",
          unapproved.returncode == 0 and "smtp_connections=0" in unapproved.stdout)
    wrong_mode = run({"EMAIL_SEND_MODE": "dry_run", "APPROVE_SEND_EMAIL": "true"})
    check("승인만 설정해도 send mode가 아니면 dry-run",
          wrong_mode.returncode == 0 and "smtp_connections=0" in wrong_mode.stdout)
    outside_actions = run({"EMAIL_SEND_MODE": "send", "APPROVE_SEND_EMAIL": "true"})
    check("두 gate가 열려도 GitHub Actions 밖에서는 실제 발송 차단",
          outside_actions.returncode == 2
          and "restricted to GitHub Actions" in outside_actions.stderr)


def check_workflow() -> None:
    text = WORKFLOW.read_text(encoding="utf-8") if WORKFLOW.exists() else ""
    sender_text = SENDER.read_text(encoding="utf-8") if SENDER.exists() else ""
    check("email-alert workflow 존재", bool(text))
    check("workflow_dispatch 수동 실행만 사용", "workflow_dispatch:" in text and "schedule:" not in text)
    check("approve_send_email 기본 false", "approve_send_email:" in text and "default: false" in text)
    check("실제 발송 step이 명시 승인에만 열림", "inputs.approve_send_email == true" in text)
    check("dry-run preview step은 비승인 경로", "inputs.approve_send_email != true" in text
          and "send_email_alert.py --dry-run" in text)
    for name in (
        "GMAIL_SMTP_USER",
        "GMAIL_SMTP_APP_PASSWORD",
        "GMAIL_SMTP_PASSWORD",
        "ALERT_EMAIL_TO",
        "ALERT_EMAIL_FROM",
        "TEAMS_CHANNEL_EMAIL",
    ):
        check(f"{name}은 GitHub Secrets에서만 workflow 주입", f"${{{{ secrets.{name} }}}}" in text)
    check("Teams 전송은 별도 명시 input", "send_to_teams:" in text and "SEND_TO_TEAMS:" in text)
    check("실제 SMTP 발송은 GitHub Actions runner로 제한", 'os.environ.get("GITHUB_ACTIONS"' in sender_text)
    check("Power Automate/Outlook connector 미사용", "power automate" not in text.lower()
          and "outlook" not in text.lower())


def check_public_artifacts() -> None:
    secret_names = (
        "GMAIL_SMTP_USER",
        "GMAIL_SMTP_APP_PASSWORD",
        "GMAIL_SMTP_PASSWORD",
        "ALERT_EMAIL_TO",
        "ALERT_EMAIL_FROM",
        "TEAMS_CHANNEL_EMAIL",
        "GH_OPERATOR_TOKEN",
        "GITHUB_TOKEN",
        "OPERATOR_SHARED_SECRET",
        "OPERATOR_PIN",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_IDS",
        "TELEGRAM_WEBHOOK_SECRET",
    )
    telegram_token = re.compile(r"\b\d{8,}:[A-Za-z0-9_-]{20,}\b")
    github_token = re.compile(
        r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"
    )
    email_address = re.compile(
        r"(?<![\w.+-])[\w.+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![\w.-])"
    )
    for path in PUBLIC_HTML:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        leaks = [name for name in secret_names if name in text]
        if telegram_token.search(text):
            leaks.append("telegram-token-shape")
        if github_token.search(text):
            leaks.append("github-token-shape")
        if email_address.search(text):
            leaks.append("email-address")
        check(f"공개 artifact secret 없음: {path.relative_to(ROOT)}", not leaks, ", ".join(leaks))


def main() -> int:
    print(f"== verify_email_teams_alert (D7-AD) @ {ROOT} ==")
    check_renderer()
    check_smtp_classification()
    check_default_dry_run()
    check_workflow()
    check_public_artifacts()
    print()
    if _failures:
        print(f"RESULT: FAIL ({len(_failures)} 항목)")
        return 1
    print("RESULT: PASS — common executive brief + gated Gmail SMTP + Teams channel-email test")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
