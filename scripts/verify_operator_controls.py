#!/usr/bin/env python3
"""D7-AG-2 Operator API UI/security verifier (offline, no dispatch)."""

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
API = ROOT / "app" / "operator_api.py"
GATEWAY = ROOT / "app" / "operator_gateway.py"

ENDPOINTS = {
    "collect": "/api/operator/collect",
    "telegram": "/api/operator/send",
    "teams": "/api/operator/send-teams",
}
BUTTONS = {
    "데이터 새로고침 실행": "opCollectBtn",
    "텔레그램 전송 실행": "opSendBtn",
    "Teams 채널 전송 실행": "opTeamsBtn",
}
SECRET_NAMES = (
    "GH_OPERATOR_TOKEN", "GITHUB_TOKEN", "OPERATOR_SHARED_SECRET", "OPERATOR_PIN",
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_IDS", "TEAMS_CHANNEL_EMAIL",
    "NAVER_CLIENT_SECRET", "X-Naver-Client-Secret",
)
SECRET_SHAPES = (
    re.compile(r"\b(?:ghp_|github_pat_)[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\b\d{8,}:[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"https://[^\"'\s]*(?:webhook\.office\.com|powerautomate\.com)[^\"'\s]*", re.I),
)
ISLAND = re.compile(
    r'<script type="application/json" id="preview-model">(.*?)</script>', re.S
)
failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures.append(name)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def model(html: str) -> dict:
    match = ISLAND.search(html)
    try:
        return json.loads(match.group(1)) if match else {}
    except json.JSONDecodeError:
        return {}


def build(base: str = "") -> str:
    with tempfile.TemporaryDirectory(prefix="hdec_d7ag2_operator_") as tmp:
        output = Path(tmp) / "dashboard.html"
        env = dict(os.environ)
        for name in SECRET_NAMES:
            env.pop(name, None)
        cmd = [sys.executable, str(BUILDER), "--output", str(output)]
        if base:
            cmd += ["--operator-api-base", base]
        proc = subprocess.run(
            cmd, cwd=ROOT, env=env, text=True, capture_output=True, timeout=300
        )
        check(f"dashboard build(base={'set' if base else 'unset'})", proc.returncode == 0,
              (proc.stderr or "")[-240:])
        return read(output)


def check_ui(html: str, label: str, *, enabled: bool) -> None:
    parsed = model(html)
    check(f"{label}: model JSON", bool(parsed))
    check(f"{label}: operator flag={str(enabled).lower()}",
          parsed.get("operator_api_enabled") is enabled
          and f'data-operator-api-enabled="{str(enabled).lower()}"' in html)
    for text, element_id in BUTTONS.items():
        check(f"{label}: button {text}",
              text in html and f'id="{element_id}" type="button"' in html)
    check(f"{label}: endpoint map", parsed.get("operator_endpoints") == ENDPOINTS,
          repr(parsed.get("operator_endpoints")))
    check(f"{label}: browser POSTs only to base+path",
          "fetch(base + path" in html and 'method: "POST"' in html)
    check(f"{label}: request/running/accepted/failure states",
          "실행 중" in html and "실행 요청 접수" in html and "실행 실패" in html)
    check(f"{label}: run URL/id rendering",
          "result.run_url || result.workflow_url" in html
          and "result.run_id" in html and "opctl-runlink" in html)
    check(f"{label}: link fallback removed",
          'id="opActionLinks"' not in html
          and "GitHub Actions 열기" not in html
          and "Scheduled Live Refresh 열기" not in html
          and "Telegram Notify 열기" not in html)
    # D7-AG-3 — 브라우저 PIN 제거 + 보호를 서버 앞단(edge)으로 이관.
    check(f"{label}: PIN 입력 UI 제거", 'id="opPin"' not in html and "승인 PIN" not in html)
    check(f"{label}: 브라우저는 secret 미보유(credentials로 경계 세션만 전달)",
          'credentials: "include"' in html and 'X-Operator-Token"] = pin' not in html)
    if enabled:
        # 하이브리드(D7-AG-5B): connected 빌드는 collect만 활성 · 발송(send/teams)은 인증 필요 상태.
        check(f"{label}: connected enables collect only (sends stay auth-locked)",
              "collectBtn.disabled = false" in html
              and "sendBtn.disabled = false" not in html
              and "teamsBtn.disabled = false" not in html
              and "authlocked" in html and "showSendLocked" in html)
    else:
        check(f"{label}: explicit disconnected state",
              "Operator API 미연결" in html and "setBtns(true)" in html)


def check_public_safety(html: str, label: str) -> None:
    leaks = [name for name in SECRET_NAMES if name in html]
    leaks.extend(f"shape-{idx}" for idx, pattern in enumerate(SECRET_SHAPES)
                 if pattern.search(html))
    check(f"{label}: public secret/token/webhook 0", not leaks, ", ".join(leaks))
    check(f"{label}: privileged browser endpoints 0",
          "api.github.com" not in html
          and "api.telegram.org" not in html
          and "openapi.naver.com/v1/search/news.json" not in html)


def check_server() -> None:
    api = read(API)
    gateway = read(GATEWAY)
    check("minimal deployment API exists", "app = FastAPI(" in api
          and 'entrypoint = "app.operator_api:app"' in read(ROOT / "pyproject.toml"))
    for path in ENDPOINTS.values():
        check(f"server route {path}", f'@router.post("{path}")' in api)
    check("legacy telegram alias retained",
          '@router.post("/api/operator/send-telegram"' in api)
    check("server-side edge 인가 함수 존재(authorize)", "def authorize(" in gateway)
    check("Origin 허용목록 + 분당 레이트리밋 방어",
          "_origin_allowed" in gateway and "_rate_ok" in gateway)
    check("shared_secret 레거시 경로 상수시간 비교", "hmac.compare_digest" in gateway)
    check("server dispatch workflows",
          all(name in gateway for name in (
              "scheduled-live-refresh.yml", "telegram-notify.yml", "email-alert.yml"
          )))
    check("dispatch response can include run id/url",
          '"run_id"' in gateway and '"run_url"' in gateway
          and '"workflow_url"' in gateway)
    check("server fail-closed 상태들",
          '"not_configured"' in gateway and '"unauthorized"' in gateway
          and '"forbidden"' in gateway and '"rate_limited"' in gateway)


def main() -> int:
    print(f"== D7-AG-2 operator controls @ {ROOT} ==")
    template = read(TEMPLATE)
    committed = read(DASHBOARD)
    unset = build()
    connected_base = "https://operator.example.invalid"
    connected = build(connected_base)

    check_ui(template, "template", enabled=False)
    check_ui(unset, "fresh-unset", enabled=False)
    check_ui(connected, "fresh-connected", enabled=True)
    check("connected base injected once into JSON island",
          model(connected).get("operator_api_base") == connected_base
          and connected.count(connected_base) == 1)
    for html, label in ((template, "template"), (unset, "fresh-unset"),
                        (connected, "fresh-connected"), (committed, "committed")):
        check_public_safety(html, label)
    check_server()

    print()
    if failures:
        print(f"RESULT: FAIL ({len(failures)})")
        return 1
    print("RESULT: PASS — Operator API 3-button POST contract; no link fallback or public secrets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
