#!/usr/bin/env python3
"""Offline D7-AD verifier for Operator API dashboard activation readiness."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates" / "dashboard_preview.html"
DASHBOARD = ROOT / "docs" / "daily" / "dashboard-latest.html"
BUILDER = ROOT / "scripts" / "build_static_dashboard.py"
GATEWAY = ROOT / "app" / "operator_gateway.py"
CONFIG = ROOT / "app" / "config.py"
MAIN = ROOT / "app" / "main.py"

_failures: list[str] = []
_ISLAND = re.compile(
    r'(<script type="application/json" id="preview-model">)(.*?)(</script>)',
    re.S,
)


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        _failures.append(name)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _model(html: str) -> dict:
    match = _ISLAND.search(html)
    if not match:
        return {}
    try:
        return json.loads(match.group(2))
    except json.JSONDecodeError:
        return {}


def _without_island(html: str) -> str:
    return _ISLAND.sub(r"\1{}\3", html, count=1)


def check_public_secret_safety() -> None:
    secret_names = (
        "GH_OPERATOR_TOKEN",
        "OPERATOR_SHARED_SECRET",
        "OPERATOR_PIN",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_IDS",
        "GMAIL_SMTP_USER",
        "GMAIL_SMTP_APP_PASSWORD",
        "GMAIL_SMTP_PASSWORD",
        "ALERT_EMAIL_TO",
        "ALERT_EMAIL_FROM",
        "TEAMS_CHANNEL_EMAIL",
    )
    value_shapes = (
        re.compile(r"\b(?:ghp_|github_pat_)[A-Za-z0-9_]{20,}\b"),
        re.compile(r"\b\d{8,}:[A-Za-z0-9_-]{20,}\b"),
    )
    targets = [TEMPLATE, DASHBOARD, *sorted((ROOT / "docs" / "daily").glob("*.html"))]
    seen: set[Path] = set()
    for path in targets:
        if path in seen or not path.exists():
            continue
        seen.add(path)
        text = _read(path)
        leaks = [name for name in secret_names if name in text]
        if any(pattern.search(text) for pattern in value_shapes):
            leaks.append("token-shape")
        check(f"공개 HTML secret/token 없음: {path.relative_to(ROOT)}", not leaks, ", ".join(leaks))


def check_unset_dashboard() -> None:
    html = _read(DASHBOARD)
    model = _model(html)
    check("공개 dashboard preview-model JSON island 파싱", bool(model))
    check("operator_api_base 기본값은 빈 문자열", model.get("operator_api_base") == "")
    check(
        "미설정 시 두 버튼 disabled 기본값",
        'id="opCollectBtn" type="button" disabled' in html
        and 'id="opSendBtn" type="button" disabled' in html,
    )
    check("미설정 안내 후 return으로 POST 차단", "if (!base)" in html
          and "운영 API가 아직 설정되지 않았습니다." in html)
    check("공개 dashboard에 GitHub Actions 이동 URL 없음", "/actions/workflows/" not in html)


def _configured_build() -> tuple[str, str]:
    public_base = "https://operator.example.invalid"
    with tempfile.TemporaryDirectory(prefix="hdec_operator_ready_") as tmp:
        output = Path(tmp) / "dashboard.html"
        env = dict(os.environ)
        for name in (
            "GH_OPERATOR_TOKEN",
            "GITHUB_TOKEN",
            "OPERATOR_SHARED_SECRET",
            "OPERATOR_PIN",
            "TELEGRAM_BOT_TOKEN",
            "GMAIL_SMTP_APP_PASSWORD",
            "TEAMS_CHANNEL_EMAIL",
        ):
            env.pop(name, None)
        proc = subprocess.run(
            [
                sys.executable,
                str(BUILDER),
                "--output",
                str(output),
                "--operator-api-base",
                public_base,
            ],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=180,
        )
        if proc.returncode != 0 or not output.exists():
            check("설정 base 임시 dashboard 빌드", False, (proc.stderr or proc.stdout)[-300:])
            return "", public_base
        check("설정 base 임시 dashboard 빌드", True)
        return output.read_text(encoding="utf-8"), public_base


def check_configured_dashboard() -> None:
    html, public_base = _configured_build()
    if not html:
        return
    model = _model(html)
    check("설정 base가 JSON island에 정확히 주입", model.get("operator_api_base") == public_base)
    check("설정 base 리터럴은 JSON island에만 존재", public_base not in _without_island(html)
          and html.count(public_base) == 1)
    check("버튼 JS는 MODEL.operator_api_base만 읽음", "MODEL.operator_api_base" in html)
    check("base 설정 시 JS가 두 버튼 활성화", "collectBtn.disabled = false" in html
          and "sendBtn.disabled = false" in html)
    check(
        "버튼은 Operator API 상대 endpoint로 POST",
        '"/api/operator/collect"' in html
        and '"/api/operator/send-telegram"' in html
        and "fetch(base + path" in html
        and 'method: "POST"' in html,
    )
    check("버튼이 GitHub/API 호스트로 직접 이동·POST하지 않음", "api.github.com" not in html
          and "/actions/workflows/" not in html)


def check_server_gateway() -> None:
    gateway = _read(GATEWAY)
    config = _read(CONFIG)
    main = _read(MAIN)
    check("Operator API POST routes 존재", '@app.post("/api/operator/collect")' in main
          and '@app.post("/api/operator/send-telegram")' in main)
    check("서버측 workflow_dispatch 소유", "/actions/workflows/" in gateway
          and 'method="POST"' in gateway)
    check("수집은 scheduled-live-refresh workflow dispatch", "scheduled-live-refresh.yml" in gateway)
    check("텔레그램은 approve_send=true 명시 dispatch", '"approve_send": "true"' in gateway)
    check("PIN은 상수시간 비교", "hmac.compare_digest" in gateway)
    check("미설정/불일치 fail-closed", '"status": "not_configured"' in gateway
          and '"status": "unauthorized"' in gateway)
    check("서버 secret은 config 환경변수에서 읽음", 'os.environ.get("GH_OPERATOR_TOKEN")' in config
          and 'os.environ.get("OPERATOR_SHARED_SECRET")' in config)
    check("CORS origin은 서버 설정 목록에서 주입", "config.OPERATOR_ALLOWED_ORIGINS" in main
          and 'os.environ.get("OPERATOR_ALLOWED_ORIGINS")' in config)


def main() -> int:
    print(f"== verify_operator_api_activation_readiness (D7-AD) @ {ROOT} ==")
    check_public_secret_safety()
    check_unset_dashboard()
    check_configured_dashboard()
    check_server_gateway()
    print()
    if _failures:
        print(f"RESULT: FAIL ({len(_failures)} 항목)")
        return 1
    print("RESULT: PASS — Operator API activation contract ready; deployment/configuration still required")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
