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
        "top_new_issues": [
            {
                "article_id": "n1",
                "title": "신규 데이터센터 전력 인프라 발주 공고",
                "category_label": "AI 데이터센터·전력 인프라",
                "url": "https://news.example/n1",
                "radar_section": "ai",
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
    mail_html = mail_message.get_body(preferencelist=("html",)).get_content()
    teams_html = teams_message.get_body(preferencelist=("html",)).get_content()
    check("일반 이메일과 Teams 채널 이메일 본문 동일",
          mail_text == teams_text and mail_html == teams_html)

    # D7-AD-K: 요약 대시보드 / 전체 리포트 CTA 링크 계약 (이메일·Teams 공통)
    dash_path = "/daily/dashboard-latest.html"
    report_path = "/daily/latest.html"  # dashboard-latest.html에는 포함되지 않는 고유 경로
    check("Email HTML에 '요약 대시보드 보기' CTA", "요약 대시보드 보기" in email_html)
    check("Email HTML에 '전체 리포트 보기' CTA", "전체 리포트 보기" in email_html)
    check("Email HTML에 요약 대시보드 URL", dash_path in email_html)
    check("Email HTML에 전체 리포트 URL", report_path in email_html)
    check("Email text에 요약 대시보드 URL", dash_path in email_text)
    check("Email text에 전체 리포트 URL", report_path in email_text)
    check(
        "버튼이 깨져도 plain URL fallback 노출",
        "버튼이 보이지 않으면" in email_html
        and email_html.count(digest.dashboard_url) >= 2
        and email_html.count(digest.report_url) >= 2,
    )
    check(
        "Email HTML에 외부 JS/CSS/이미지/첨부 없음",
        not any(
            token in email_html.lower()
            for token in ("<script", "<link", "<img", "<iframe", "javascript:", "url(", "@import")
        ),
    )
    rendered = "\n".join((email_html, email_text, render_subject(digest)))
    leak_names = (
        "GMAIL_SMTP_USER", "GMAIL_SMTP_APP_PASSWORD", "GMAIL_SMTP_PASSWORD",
        "ALERT_EMAIL_TO", "ALERT_EMAIL_FROM", "TEAMS_CHANNEL_EMAIL",
        "OPERATOR_SHARED_SECRET", "OPERATOR_PIN", "GH_OPERATOR_TOKEN",
        "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_IDS",
    )
    check("렌더된 본문에 secret 이름/토큰 없음",
          not any(name in rendered for name in leak_names))

    # D7-AD-L: 라벨 ↔ URL 매핑 정확성 — 라벨과 목적지 역할이 어긋나지 않는다(swap 금지).
    label_to_url = {
        label: url
        for url, label in re.findall(
            r'<a href="([^"]+)"[^>]*>(요약 대시보드 보기|전체 리포트 보기)</a>', email_html)
    }
    summary_url = label_to_url.get("요약 대시보드 보기", "")
    full_url = label_to_url.get("전체 리포트 보기", "")
    check("CTA '요약 대시보드 보기' → dashboard-latest.html",
          summary_url.endswith("/daily/dashboard-latest.html"), summary_url)
    check("CTA '전체 리포트 보기' → latest.html(전체 리포트)",
          full_url.endswith("/daily/latest.html"), full_url)
    check("CTA 라벨 역전 없음 (요약→latest / 전체→dashboard 금지)",
          not summary_url.endswith("/daily/latest.html")
          and "/dashboard-latest.html" not in full_url)
    check("fallback plain URL도 같은 매핑 (요약=dashboard / 전체=latest)",
          digest.dashboard_url.endswith("/daily/dashboard-latest.html")
          and digest.report_url.endswith("/daily/latest.html"))
    check("Email text 라벨↔URL 매핑 정확",
          f"요약 대시보드: {digest.dashboard_url}" in email_text
          and f"전체 리포트: {digest.report_url}" in email_text)

    # D7-AD-L: '오늘의 신규 이슈' 섹션 (top_new_issues 재사용 · [분류] 제목)
    check("Email에 '오늘의 신규 이슈' 섹션", bool(digest.new_issues)
          and "오늘의 신규 이슈" in email_html and "오늘의 신규 이슈" in email_text)
    check("신규 이슈는 [분류] 제목 형태(HTML·text)",
          all(f"[{issue.label}]" in email_text for issue in digest.new_issues)
          and all(f"[{issue.label}]" in email_html for issue in digest.new_issues))
    check("신규 이슈 최대 5건", len(digest.new_issues) <= 5, str(len(digest.new_issues)))


def check_telegram_mapping() -> None:
    """Telegram 짧은 라벨은 유지하되 라벨→URL 매핑을 잠근다(이메일과 표현만 다름·역할 동일)."""
    import json

    import send_telegram as st

    base = "https://x.github.io/repo/daily"
    report_url, dash_url = f"{base}/latest.html", f"{base}/dashboard-latest.html"
    payload = st.build_payload("DRY", "m", report_url, "", dash_url)
    buttons = json.loads(payload["reply_markup"])["inline_keyboard"][0]
    by_label = {b["text"]: b["url"] for b in buttons}
    check("Telegram 짧은 라벨 유지('대시보드 보기'/'상세 리포트 보기')",
          st.SUMMARY_BUTTON_TEXT == "대시보드 보기"
          and st.FULL_REPORT_BUTTON_TEXT == "상세 리포트 보기")
    check("Telegram '대시보드 보기' → dashboard-latest(요약 대시보드)",
          by_label.get("대시보드 보기", "").endswith("/dashboard-latest.html"))
    check("Telegram '상세 리포트 보기' → latest(전체 리포트)",
          by_label.get("상세 리포트 보기", "").endswith("/daily/latest.html"))


def _render_to_html(script_name: str, extra_args: tuple[str, ...] = ()) -> str:
    """빌더를 mock으로 산출해 HTML 문자열을 돌려준다(오프라인 · 임시파일)."""
    import tempfile

    handle, path = tempfile.mkstemp(suffix=".html")
    os.close(handle)
    try:
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / script_name), "--output", path, *extra_args],
            cwd=ROOT, env=_clean_env(), text=True, capture_output=True, timeout=300)
        return Path(path).read_text(encoding="utf-8") if proc.returncode == 0 else ""
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def check_cta_destination_titles() -> None:
    """CTA 목적지 페이지의 제목/헤딩이 라벨 역할과 충돌하지 않는다(빌더 산출 기준)."""
    report = _render_to_html("build_static_report.py", ("--audience", "executive"))
    dashboard = _render_to_html("build_static_dashboard.py")
    check("전체 리포트 페이지: <title>가 '전체 리포트'",
          "<title>HDEC Executive Radar — 전체 리포트</title>" in report)
    check("전체 리포트 페이지: h1이 '전체 리포트'", "<h1>전체 리포트</h1>" in report)
    check("전체 리포트 페이지: 'Executive Daily Brief' 부제로 보존",
          "Executive Daily Brief" in report)
    check("요약 대시보드 페이지: <title>가 '요약 대시보드'",
          "<title>HDEC Executive Radar — 요약 대시보드</title>" in dashboard)
    check("요약 대시보드 페이지: 브랜드에 '요약 대시보드' 가시 표기",
          "· 요약 대시보드" in dashboard)
    check("요약 대시보드 운영본에 'PREVIEW' 브랜드 표기 없음",
          "대시보드 미리보기 (PREVIEW)" not in dashboard)


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
    check_telegram_mapping()
    check_cta_destination_titles()
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
