#!/usr/bin/env python3
"""Offline verifier for D7-AH-1 hourly, delta-gated alert delivery."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "scheduled-live-refresh.yml"
DETECTOR = ROOT / "scripts" / "detect_dashboard_alert_delta.py"
EMAIL_SENDER = ROOT / "scripts" / "send_email_alert.py"
OPERATOR_VERIFIER = ROOT / "scripts" / "verify_operator_actual_buttons.py"
PUBLIC_HTML = [ROOT / "docs" / "index.html", *sorted((ROOT / "docs" / "daily").glob("*.html"))]

TOKEN_PATTERNS = (
    re.compile(r"\b\d{8,}:[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    re.compile(r"https://[^\s\"']*(?:webhook\.office\.com|powerautomate)[^\s\"']*", re.I),
)
PUBLIC_SECRET_NAMES = (
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_IDS",
    "TELEGRAM_WEBHOOK_SECRET",
    "GMAIL_SMTP_USER",
    "GMAIL_SMTP_APP_PASSWORD",
    "GMAIL_SMTP_PASSWORD",
    "ALERT_EMAIL_TO",
    "ALERT_EMAIL_FROM",
    "TEAMS_CHANNEL_EMAIL",
    "NAVER_CLIENT_ID",
    "NAVER_CLIENT_SECRET",
    "GH_OPERATOR_TOKEN",
    "GITHUB_TOKEN",
    "GITHUB_OAUTH_CLIENT_SECRET",
    "OPERATOR_SHARED_SECRET",
    "OPERATOR_SESSION_SECRET",
    "OPERATOR_PIN",
)

_failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""))
    if not ok:
        _failures.append(name)
    return ok


def _step(text: str, name: str) -> str:
    pattern = re.compile(
        rf"(?ms)^\s+- name: {re.escape(name)}\s*$\n(.*?)(?=^\s+- name: |\Z)"
    )
    match = pattern.search(text)
    return match.group(1) if match else ""


def _dashboard_html(model: dict) -> str:
    payload = json.dumps(model, ensure_ascii=False)
    return (
        "<!doctype html><html><body>"
        f'<script type="application/json" id="preview-model">{payload}</script>'
        "</body></html>"
    )


def _article(**overrides: object) -> dict:
    row = {
        "article_id": "article-1",
        "title": "Hourly alert fixture",
        "source": "fixture.example",
        "url": "https://fixture.example/article-1",
        "category_label": "Business",
        "score": 4.2,
    }
    row.update(overrides)
    return row


def _run_detector(old_model: dict, new_model: dict) -> tuple[subprocess.CompletedProcess, str]:
    with tempfile.TemporaryDirectory(prefix="hdec_hourly_delta_") as tmp:
        tmp_path = Path(tmp)
        old_path = tmp_path / "old.html"
        new_path = tmp_path / "new.html"
        output_path = tmp_path / "github_output.txt"
        old_path.write_text(_dashboard_html(old_model), encoding="utf-8")
        new_path.write_text(_dashboard_html(new_model), encoding="utf-8")
        proc = subprocess.run(
            [
                sys.executable,
                str(DETECTOR),
                str(old_path),
                str(new_path),
                "--github-output",
                str(output_path),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=30,
        )
        output = output_path.read_text(encoding="utf-8") if output_path.exists() else ""
        return proc, output


def check_schedule_and_dispatch(text: str) -> None:
    crons = re.findall(r"cron:\s*[\"']([^\"']+)[\"']", text)
    check("schedule is exactly hourly", crons == ["0 * * * *"], repr(crons))
    check("workflow_dispatch is preserved", "workflow_dispatch:" in text)
    check("force_dry_run manual input is preserved", "force_dry_run:" in text)
    check("scheduled runs remain serialized", "group: scheduled-live-refresh" in text)


def check_delta_wiring(text: str) -> None:
    snapshot = _step(text, "Snapshot dashboard before live build")
    build = _step(text, "Build live report + dashboard")
    delta = _step(text, "Detect dashboard alert delta")
    check("delta detector script exists", DETECTOR.is_file())
    check(
        "old dashboard is copied to runner temp",
        'cp docs/daily/dashboard-latest.html "$RUNNER_TEMP/dashboard-before.html"' in snapshot,
    )
    check(
        "snapshot precedes live dashboard build",
        bool(snapshot and build) and text.index("Snapshot dashboard") < text.index("Build live report"),
    )
    check("delta step has id delta", "id: delta" in delta)
    check("delta step runs only after live success", "steps.build.outputs.live_ok == 'true'" in delta)
    check(
        "delta step compares temp old dashboard with new dashboard",
        "detect_dashboard_alert_delta.py" in delta
        and '"$RUNNER_TEMP/dashboard-before.html"' in delta
        and "docs/daily/dashboard-latest.html" in delta
        and '"$GITHUB_OUTPUT"' in delta,
    )


def check_detector_behavior() -> None:
    base = {"top_immediate_signals": [_article()]}
    same_proc, same_output = _run_detector(base, base)
    same_log = (same_proc.stdout or "") + (same_proc.stderr or "")
    check(
        "unchanged fingerprint closes alert delta",
        same_proc.returncode == 0 and same_output == "alert_delta=false\n",
        f"rc={same_proc.returncode} output={same_output.strip()}",
    )
    check(
        "detector log is human-readable and content-free",
        all(label in same_log for label in ("changed_count=", "old_hash=", "new_hash="))
        and "Hourly alert fixture" not in same_log,
    )

    field_changes = {
        "article_id": "article-2",
        "title": "Updated hourly alert fixture",
        "source": "updated.example",
        "url": "https://fixture.example/article-2",
        "category_label": "Risk",
        "score": 4.8,
    }
    failed_fields: list[str] = []
    for field, value in field_changes.items():
        changed = {"top_immediate_signals": [_article(**{field: value})]}
        changed_proc, changed_output = _run_detector(base, changed)
        if changed_proc.returncode != 0 or changed_output != "alert_delta=true\n":
            failed_fields.append(field)
    check(
        "all requested article fields participate in the fingerprint",
        not failed_fields,
        ", ".join(failed_fields),
    )

    empty_proc, empty_output = _run_detector(base, {"top_immediate_signals": []})
    check(
        "empty new candidate set closes alert delta",
        empty_proc.returncode == 0 and empty_output == "alert_delta=false\n",
        f"rc={empty_proc.returncode} output={empty_output.strip()}",
    )

    site_old = {
        "news_rows": [_article()],
        "site_watch_tree": {"nodes": [{"article_keys": ["https://fixture.example/article-1"]}]},
    }
    site_new = {
        "news_rows": [_article(title="Updated site signal")],
        "site_watch_tree": {"nodes": [{"article_keys": ["https://fixture.example/article-1"]}]},
    }
    site_proc, site_output = _run_detector(site_old, site_new)
    check(
        "site article_keys participate in the fingerprint",
        site_proc.returncode == 0 and site_output == "alert_delta=true\n",
        f"rc={site_proc.returncode} output={site_output.strip()}",
    )

    with tempfile.TemporaryDirectory(prefix="hdec_hourly_invalid_") as tmp:
        tmp_path = Path(tmp)
        old_path = tmp_path / "old.html"
        new_path = tmp_path / "new.html"
        output_path = tmp_path / "github_output.txt"
        old_path.write_text(_dashboard_html(base), encoding="utf-8")
        new_path.write_text("<html>invalid dashboard</html>", encoding="utf-8")
        invalid = subprocess.run(
            [
                sys.executable,
                str(DETECTOR),
                str(old_path),
                str(new_path),
                "--github-output",
                str(output_path),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=30,
        )
        invalid_output = output_path.read_text(encoding="utf-8")
    check(
        "invalid dashboard fails closed",
        invalid.returncode != 0 and invalid_output == "alert_delta=false\n",
        f"rc={invalid.returncode} output={invalid_output.strip()}",
    )


def check_send_gates(text: str) -> None:
    telegram = _step(text, "Hourly telegram digest (delta-gated auto-send)")
    teams = _step(text, "Hourly Teams channel email (delta-gated auto-send)")
    skip = _step(text, "Skip automatic alerts (no delta)")
    email_sender = EMAIL_SENDER.read_text(encoding="utf-8")
    required_delta = (
        "steps.build.outputs.live_ok == 'true'",
        "steps.delta.outputs.alert_delta == 'true'",
        "vars.HOURLY_DELTA_AUTO_SEND == '1'",
    )
    check("Telegram auto-send step exists", bool(telegram))
    check("Teams auto-send step exists", bool(teams))
    check("Telegram opens only for live + delta + hourly opt-in", all(x in telegram for x in required_delta))
    check("Telegram keeps existing explicit opt-in", "vars.TELEGRAM_AUTO_SEND == '1'" in telegram)
    check("Teams opens only for live + delta + hourly opt-in", all(x in teams for x in required_delta))
    check(
        "hourly opt-in absent means no automatic sender can run",
        "vars.HOURLY_DELTA_AUTO_SEND == '1'" in telegram
        and "vars.HOURLY_DELTA_AUTO_SEND == '1'" in teams,
    )
    check(
        "Teams send approvals exist only inside its guarded step",
        'APPROVE_SEND_EMAIL: "true"' in teams
        and 'SEND_TO_TEAMS: "true"' in teams
        and text.count('APPROVE_SEND_EMAIL: "true"') == 1
        and text.count('SEND_TO_TEAMS: "true"') == 1,
    )
    check("Teams reuses gated email sender", "python3 scripts/send_email_alert.py" in teams)
    check(
        "SMTP acceptance remains separate from actual Teams receipt",
        "SMTP acceptance only proves" in email_sender
        and "does not prove inbox delivery or a Teams channel post" in email_sender
        and "unverified" in email_sender,
    )
    check(
        "no-delta path skips both Telegram and Teams",
        "steps.delta.outputs.alert_delta != 'true'" in skip
        and "no alert delta — skip telegram" in skip
        and "no alert delta — skip teams" in skip,
    )


def check_public_artifacts() -> None:
    leaks: list[str] = []
    for path in PUBLIC_HTML:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for name in PUBLIC_SECRET_NAMES:
            if name in text:
                leaks.append(f"{path.relative_to(ROOT)}:{name}")
        if any(pattern.search(text) for pattern in TOKEN_PATTERNS):
            leaks.append(f"{path.relative_to(ROOT)}:credential-shape")
    check("public artifacts expose zero secret/token/webhook values", not leaks, ", ".join(leaks[:5]))


def check_operator_verifier(skip: bool) -> None:
    if skip:
        check("operator button verifier is separately wired in Verify pipeline", "verify_operator_actual_buttons.py" in WORKFLOW.read_text(encoding="utf-8"))
        return
    proc = subprocess.run(
        [sys.executable, str(OPERATOR_VERIFIER)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=600,
    )
    output = (proc.stdout or "") + (proc.stderr or "")
    check("existing operator button verifier passes", proc.returncode == 0, output[-500:])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--skip-operator-verifier",
        action="store_true",
        help="avoid duplicate work when the same pipeline runs the operator verifier next",
    )
    args = parser.parse_args(argv)

    print(f"== verify_hourly_delta_auto_send @ {ROOT} ==")
    if not check("scheduled workflow exists", WORKFLOW.is_file()):
        return 1
    text = WORKFLOW.read_text(encoding="utf-8")
    check_schedule_and_dispatch(text)
    check_delta_wiring(text)
    check_detector_behavior()
    check_send_gates(text)
    check_public_artifacts()
    check_operator_verifier(args.skip_operator_verifier)

    print()
    if _failures:
        print(f"RESULT: FAIL ({len(_failures)} failed)")
        return 1
    print("RESULT: PASS - hourly live refresh with delta-gated Telegram/Teams alerts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
