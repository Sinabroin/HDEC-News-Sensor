#!/usr/bin/env python3
"""D7-T verifier — operator-safe data-refresh / telegram-send controls (separate, link-only).

Runs fully offline (no network, DB writes, secrets, or send). It proves the operator
control panel added to the summary dashboard is *safe by construction*: two separate
controls that are nothing more than links to GitHub Actions workflow pages, where the
GitHub login + workflow_dispatch UI (and, for Telegram, the existing approve_send / human
review gate) remain the only execution gate.

Why the panel lives on the summary dashboard (docs/daily/dashboard-latest.html) and not on
the detailed operator report (docs/daily/operator-latest.html):
  · The dashboard is the operator's summary/action surface and is the explicitly-preferred
    placement. It is generated from templates/dashboard_preview.html via the builder.
  · The detailed report (latest.html / operator-latest.html) is produced by
    build_static_report.py, whose output is leak-guarded by verify_static_report.py — that
    gate bans the literal substring "telegram" anywhere in the report HTML (a guard against
    leaking telegram tokens / dev wording). The Telegram workflow URL legitimately contains
    "telegram-notify.yml", so embedding the panel in the report would trip that safety gate.
    We do NOT weaken that gate. So the report pages are *safety-checked* here (no token/API
    leaks) but are not required to host the workflow-link panel.

Checks (the D7-T contract):
  1.  docs/daily/dashboard-latest.html exists (the control surface).
  2.  docs/daily/operator-latest.html, if it exists, is also safety-checked.
  3.  "데이터 새로고침 실행" control exists on the dashboard.
  4.  "텔레그램 전송 실행" control exists on the dashboard.
  5.  The two buttons do NOT share the same URL.
  6.  Refresh button URL contains /actions/workflows/scheduled-live-refresh.yml.
  7.  Telegram button URL contains /actions/workflows/telegram-notify.yml.
  8.  Both are normal https://github.com/Sinabroin/HDEC-News-Sensor/actions/workflows/… links.
  9.  Both buttons use target="_blank" and rel includes noopener noreferrer.
  10. Required helper/warning copy is present (5 strings).
  11. No token-like strings in public HTML/JS (token shape, PAT shape, *_BOT_TOKEN).
  12. No api.github.com in public dashboard/operator HTML.
  13. No api.telegram.org in public dashboard/operator HTML.
  14. No sendMessage in public dashboard/operator HTML.
  15. No hardcoded TELEGRAM_AUTO_SEND=1.
  16. No browser fetch/XMLHttpRequest POST to GitHub or Telegram.
  17. Existing report/dashboard button mapping still passes
      (요약 대시보드 → dashboard-latest.html · 상세 리포트 → latest.html).

Usage:
    python3 scripts/verify_operator_controls.py
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "docs" / "daily" / "dashboard-latest.html"
OPERATOR = ROOT / "docs" / "daily" / "operator-latest.html"
LATEST = ROOT / "docs" / "daily" / "latest.html"
TEMPLATE = ROOT / "templates" / "dashboard_preview.html"

GH_BASE = "https://github.com/Sinabroin/HDEC-News-Sensor/actions/workflows/"
REFRESH_WF = "scheduled-live-refresh.yml"
TELEGRAM_WF = "telegram-notify.yml"
REFRESH_PATH = "/actions/workflows/" + REFRESH_WF
TELEGRAM_PATH = "/actions/workflows/" + TELEGRAM_WF

REFRESH_LABEL = "데이터 새로고침 실행"
TELEGRAM_LABEL = "텔레그램 전송 실행"

# Required helper/warning copy near the controls (task spec).
REQUIRED_COPY = [
    "GitHub 권한 필요",
    "승인 입력 필요",
    "새로고침은 리포트 생성만 수행합니다",
    "텔레그램 전송은 별도 승인 후 최신 리포트를 발송합니다",
    "브라우저에서는 토큰을 저장하거나 직접 발송하지 않습니다",
]

# Telegram inline-button mapping labels (existing send_telegram contract, check 17).
SUMMARY_LABEL = "대시보드 보기"
FULL_REPORT_LABEL = "상세 리포트 보기"

# Token / secret shapes that must never appear in public HTML/JS.
TOKEN_SHAPE = re.compile(r"[0-9]{8,}:[A-Za-z0-9_-]{20,}")          # telegram bot token
PAT_SHAPE = re.compile(r"ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}")  # GitHub PAT
SECRET_NAMES = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_IDS", "NAVER_CLIENT_SECRET")

_failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    line = f"[{'PASS' if ok else 'FAIL'}] {name}"
    if detail:
        line += f" — {detail}"
    print(line)
    if not ok:
        _failures.append(name)
    return ok


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _anchor_for(html: str, url_needle: str) -> tuple[str, str]:
    """Return (opening <a> tag, href) for the first anchor whose href contains url_needle."""
    for m in re.finditer(r"<a\b[^>]*>", html):
        tag = m.group(0)
        href = re.search(r'href="([^"]*)"', tag)
        if href and url_needle in href.group(1):
            return tag, href.group(1)
    return "", ""


# ---------------------------------------------------------------------------
# 1 · control surface exists + the two separate buttons
# ---------------------------------------------------------------------------

def check_buttons(html: str) -> tuple[str, str]:
    check("3: '데이터 새로고침 실행' 컨트롤 존재", REFRESH_LABEL in html)
    check("4: '텔레그램 전송 실행' 컨트롤 존재", TELEGRAM_LABEL in html)

    refresh_tag, refresh_url = _anchor_for(html, REFRESH_WF)
    telegram_tag, telegram_url = _anchor_for(html, TELEGRAM_WF)

    check("3b: 새로고침 버튼이 실제 <a href> 링크(직접 발송 JS 아님)", bool(refresh_tag), refresh_url)
    check("4b: 텔레그램 버튼이 실제 <a href> 링크(직접 발송 JS 아님)", bool(telegram_tag), telegram_url)

    # 5 · 두 버튼의 URL이 동일하지 않다 (refresh ↔ send 결합 금지).
    check("5: 두 버튼 URL이 서로 다름 (refresh ≠ send)",
          bool(refresh_url) and bool(telegram_url) and refresh_url != telegram_url,
          f"refresh={refresh_url} · telegram={telegram_url}")

    # 6 · refresh URL.
    check("6: 새로고침 버튼 URL이 scheduled-live-refresh.yml 워크플로",
          REFRESH_PATH in refresh_url, refresh_url)
    # 7 · telegram URL.
    check("7: 텔레그램 버튼 URL이 telegram-notify.yml 워크플로",
          TELEGRAM_PATH in telegram_url, telegram_url)
    # 7c · 버튼이 서로의 워크플로를 가리키지 않는다 (라벨↔링크 swap 방지).
    check("7c: 새로고침 버튼이 telegram-notify를 가리키지 않음",
          TELEGRAM_WF not in refresh_url, refresh_url)
    check("7d: 텔레그램 버튼이 scheduled-live-refresh를 가리키지 않음",
          REFRESH_WF not in telegram_url, telegram_url)

    # 8 · 둘 다 정상 github.com 워크플로 페이지 링크 (api 아님).
    for label, url in (("새로고침", refresh_url), ("텔레그램", telegram_url)):
        check(f"8: {label} 링크가 정상 GitHub 워크플로 페이지 URL",
              url.startswith(GH_BASE) and "api.github.com" not in url, url)

    # 9 · 새 탭 + noopener noreferrer.
    for label, tag in (("새로고침", refresh_tag), ("텔레그램", telegram_tag)):
        ok = ('target="_blank"' in tag
              and "noopener" in tag and "noreferrer" in tag)
        check(f"9: {label} 버튼 target=_blank + rel noopener noreferrer", ok, tag[:90])

    return refresh_url, telegram_url


def check_copy(html: str) -> None:
    for phrase in REQUIRED_COPY:
        check(f"10: 안내/경고 문구 '{phrase[:24]}…' 존재", phrase in html)


# ---------------------------------------------------------------------------
# 2 · safety — no secrets / no direct privileged API from the browser
# ---------------------------------------------------------------------------

def check_safety(html: str, label: str) -> None:
    low = html.lower()
    # 11 · 토큰/시크릿 모양.
    leaks = []
    if TOKEN_SHAPE.search(html):
        leaks.append("token-shape")
    if PAT_SHAPE.search(html):
        leaks.append("pat-shape")
    for name in SECRET_NAMES:
        if name in html:
            leaks.append(name)
    check(f"11[{label}]: 토큰/시크릿 문자열 없음", not leaks, ", ".join(leaks))
    # 12 / 13 · 직접 권한 API 호스트.
    check(f"12[{label}]: api.github.com 미포함", "api.github.com" not in low)
    check(f"13[{label}]: api.telegram.org 미포함", "api.telegram.org" not in low)
    # 14 · sendMessage 직접 호출.
    check(f"14[{label}]: sendMessage 미포함", "sendmessage" not in low)
    # 15 · 자동 발송 하드코딩.
    auto = re.search(r"telegram_auto_send\s*[=:]\s*['\"]?1", low)
    check(f"15[{label}]: TELEGRAM_AUTO_SEND=1 하드코딩 없음", not auto)
    # 16 · 브라우저 fetch/XHR POST → GitHub/Telegram.
    xhr = "xmlhttprequest" in low
    fetch_host = re.search(r"fetch\(\s*[`'\"][^`'\"]*(github|telegram)", low)
    post_host = re.search(r"(github|telegram)[^\n]{0,200}?method\s*:\s*['\"]?post", low)
    check(f"16[{label}]: GitHub/Telegram로의 브라우저 fetch/XHR POST 없음",
          not (xhr or fetch_host or post_host),
          "xhr" if xhr else ("fetch" if fetch_host else ("post" if post_host else "")))


# ---------------------------------------------------------------------------
# 17 · existing report/dashboard button mapping unchanged
# ---------------------------------------------------------------------------

def check_report_dashboard_mapping() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import send_telegram as st
    except Exception as exc:  # noqa: BLE001
        check("17: send_telegram import", False, str(exc))
        return
    payload = st.build_payload(
        "DRY", "msg",
        "https://example.github.io/repo/daily/latest.html", "",
        "https://example.github.io/repo/daily/dashboard-latest.html")
    buttons = json.loads(payload["reply_markup"])["inline_keyboard"][0]
    by_label = {b["text"]: b["url"] for b in buttons}
    summary = by_label.get(SUMMARY_LABEL, "")
    full = by_label.get(FULL_REPORT_LABEL, "")
    check("17a: '대시보드 보기' → 요약 대시보드(dashboard-latest.html)",
          summary.endswith("/dashboard-latest.html"), summary)
    check("17b: '상세 리포트 보기' → 전체 리포트(latest.html, dashboard 아님)",
          full.endswith("/latest.html") and "dashboard-latest.html" not in full, full)


def main() -> int:
    print(f"== verify_operator_controls (D7-T) @ {ROOT} ==")

    # 1 · 컨트롤 표면(요약 대시보드) 존재.
    if not check("1: docs/daily/dashboard-latest.html 존재 (컨트롤 표면)", DASHBOARD.exists()):
        print("\nRESULT: FAIL (대시보드 산출물 누락)")
        return 1
    dash = _read(DASHBOARD)

    # 3~10 · 두 분리 버튼 + 안내 문구 (요약 대시보드 표면).
    check_buttons(dash)
    check_copy(dash)

    # 11~16 · 공개 HTML 안전성 — 대시보드/운영자/리포트 + 소스 템플릿.
    safety_targets = [(dash, "dashboard")]
    # 2 · operator-latest.html 존재 시 함께 검사.
    if check("2: docs/daily/operator-latest.html 존재 시 함께 검사",
             True, "존재" if OPERATOR.exists() else "미존재(스킵)"):
        if OPERATOR.exists():
            safety_targets.append((_read(OPERATOR), "operator"))
    if LATEST.exists():
        safety_targets.append((_read(LATEST), "latest"))
    if TEMPLATE.exists():
        safety_targets.append((_read(TEMPLATE), "template"))
    for html, label in safety_targets:
        check_safety(html, label)

    # 17 · 기존 리포트/대시보드 버튼 매핑 유지.
    check_report_dashboard_mapping()

    print()
    if _failures:
        print(f"RESULT: FAIL ({len(_failures)} 항목)")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("RESULT: PASS — 운영자 안전 컨트롤(데이터 새로고침 ↔ 텔레그램 전송, 분리·링크 전용) 확인 (D7-T)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
