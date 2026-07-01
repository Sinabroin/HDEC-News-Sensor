#!/usr/bin/env python3
"""D7-AA verifier — operator action buttons call the Operator API, never GitHub navigation.

Runs fully offline (no network, DB writes, secrets, or send). It proves the operator
control panel on the summary dashboard is *safe by construction under the new contract*:

  · The two buttons ("데이터 새로고침 실행" / "텔레그램 전송 실행") are <button> elements that
    call a server-side Operator API (POST) via JS fetch — they are NOT <a href> links to
    GitHub Actions pages, and the public HTML contains no GitHub Actions manual-run URL.
  · The fetch target base is read from the preview-model JSON island (operator_api_base),
    so the bare interaction script stays byte-identical across template/build. When the
    base is unset (the default in the committed public build) the buttons are disabled and
    only a "운영 API가 아직 설정되지 않았습니다. GitHub로 이동하지 않습니다." notice is shown —
    no navigation anywhere.
  · No token/secret/chat-id is embedded; the PIN is a runtime input sent as a header only.
  · The browser never POSTs directly to api.github.com / api.telegram.org and never calls
    sendMessage; the privileged GitHub workflow_dispatch happens server-side in
    app/operator_gateway.py (out of scope of the public page).

This replaces the old D7-T contract (which required the buttons to *be* GitHub Actions
links). The Telegram inline-button mapping for the report/dashboard links (send_telegram)
is unrelated to the operator controls and is still checked (check 17).

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

REFRESH_LABEL = "데이터 새로고침 실행"
TELEGRAM_LABEL = "텔레그램 전송 실행"

# 운영 API 미설정 시 정확히 이 안내(이동 없음)를 표시해야 한다 (task spec).
UNSET_NOTICE = "운영 API가 아직 설정되지 않았습니다. GitHub로 이동하지 않습니다."

# 운영자 버튼이 호출하는 상대 API 경로 (정적 페이지엔 base만 주입, 비밀값 0건).
COLLECT_ENDPOINT = "/api/operator/collect"
TELEGRAM_ENDPOINT = "/api/operator/send-telegram"
TEAMS_ENDPOINT = "/api/operator/send-teams"   # D7-AD-U — Teams도 운영 API로 배선

# 버튼/상태 요소 식별자 + 요청 상태(실행중/성공/실패/timeout) + 중복클릭 가드.
REQUIRED_TOKENS = [
    'id="opCollectBtn"', 'id="opSendBtn"', 'id="opStatus"', 'id="opPin"',
    "MODEL.operator_api_base",          # base는 JSON island에서만 읽는다 (byte-identity)
    "AbortController",                  # timeout
    "실행 중",                          # 진행 상태
    "시간 초과",                        # timeout 상태
    "inflight",                         # 중복 클릭 방지
]

# 운영자 안내/경고 문구 (새 계약).
REQUIRED_COPY = [
    "운영 API 연결 시 버튼 클릭으로 즉시 실행됩니다.",
    "텔레그램 전송은 승인 PIN 입력 후 즉시 발송됩니다.",
    "브라우저에는 토큰·시크릿을 저장하지 않으며, GitHub로 이동하지 않습니다.",
    UNSET_NOTICE,
]

# Telegram inline-button mapping labels (existing send_telegram contract, check 17).
SUMMARY_LABEL = "대시보드 보기"
FULL_REPORT_LABEL = "상세 리포트 보기"

# Token / secret shapes that must never appear in public HTML/JS.
TOKEN_SHAPE = re.compile(r"[0-9]{8,}:[A-Za-z0-9_-]{20,}")          # telegram bot token
PAT_SHAPE = re.compile(r"ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}")  # GitHub PAT
SECRET_NAMES = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_IDS", "NAVER_CLIENT_SECRET",
                "GH_OPERATOR_TOKEN", "OPERATOR_SHARED_SECRET")

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


# ---------------------------------------------------------------------------
# 1 · buttons call the Operator API and do NOT navigate to GitHub
# ---------------------------------------------------------------------------

def check_buttons(html: str) -> None:
    check("3: '데이터 새로고침 실행' 컨트롤 존재", REFRESH_LABEL in html)
    check("4: '텔레그램 전송 실행' 컨트롤 존재", TELEGRAM_LABEL in html)

    # 5 · 버튼이 <button>이고 GitHub로 이동하는 <a href ...actions...> 링크가 아니다.
    check("5: 운영자 버튼이 <button> 요소(직접 실행)", '<button class="opctl-btn refresh"' in html
          and '<button class="opctl-btn send"' in html)
    # 6 · 공개 HTML에 GitHub Actions 수동실행 URL이 없다 (버튼이 GitHub로 이동하지 않음).
    check("6: GitHub Actions 워크플로 수동실행 URL 미포함 (/actions/workflows/)",
          "/actions/workflows/" not in html)
    check("6b: github.com 워크플로 페이지 링크 미포함",
          not re.search(r'href="https://github\.com/[^"]*actions', html))
    # 7 · 버튼이 운영 API(상대 경로)를 호출한다 (base는 JSON island에서 주입).
    check("7: 수집 endpoint(/api/operator/collect) 참조", COLLECT_ENDPOINT in html)
    check("7b: 텔레그램 endpoint(/api/operator/send-telegram) 참조", TELEGRAM_ENDPOINT in html)
    # 8 · fetch는 운영 API base로 POST하며 github/telegram 호스트 리터럴을 쓰지 않는다.
    check("8: fetch가 운영 API base로 POST(github/telegram 리터럴 아님)",
          "fetch(base + path" in html and 'method: "POST"' in html)
    # 9 · 버튼/상태/PIN 요소 + 상태표시 + 중복클릭 가드 토큰.
    for tok in REQUIRED_TOKENS:
        check(f"9: 운영자 컨트롤 토큰 '{tok[:28]}' 존재", tok in html)


def check_unset_behavior(html: str) -> None:
    # 10 · 운영 API 미설정 시 GitHub로 이동하지 않고 안내만 표시한다.
    check("10: 미설정 시 GitHub 이동 없이 안내 표시", UNSET_NOTICE in html)
    # 미설정 기본(공개 빌드)에서 버튼은 disabled로 시작한다(JS가 base 있을 때만 활성화).
    check("10b: 버튼은 disabled 기본값(JS가 base 있을 때만 활성화)",
          'id="opCollectBtn" type="button" disabled' in html
          and 'id="opSendBtn" type="button" disabled' in html)


def check_copy(html: str) -> None:
    for phrase in REQUIRED_COPY:
        check(f"11: 안내 문구 '{phrase[:22]}…' 존재", phrase in html)


# ---------------------------------------------------------------------------
# 18~20 · Teams 채널 전송 버튼 — 운영 API로 배선 (D7-AD-U, endpoint 구현됨)
# ---------------------------------------------------------------------------

def check_teams_control(tpl: str) -> None:
    """D7-AD-U — Teams 채널 전송이 collect/telegram과 동일하게 운영 API(POST)로 배선됐다.
    운영 API에 send-teams endpoint(app/main.py + operator_gateway.trigger_teams → email-alert.yml
    workflow_dispatch)가 생겨, 버튼은 기본 disabled로 두되 운영 API base가 주입된(운영자) 빌드에서
    JS가 활성화한다. 정적 HTML은 GitHub Actions를 직접 호출하지 않는다 — 승인 PIN 검증과
    workflow_dispatch는 서버(operator_gateway)가 소유한다. 운영자 패널은 빌더가 손대지 않는 정적
    영역이라 템플릿으로 검증한다(공개 빌드는 base 미설정이라 세 버튼 모두 disabled)."""
    check("18: 'Teams 채널 전송 실행' 컨트롤 존재", "Teams 채널 전송 실행" in tpl)
    check('18b: Teams 버튼이 <button class="opctl-btn teams" id="opTeamsBtn"> 요소',
          '<button class="opctl-btn teams" id="opTeamsBtn"' in tpl)
    check("19: Teams 버튼은 disabled 기본값(운영 API base 주입 시 JS가 활성화)",
          'id="opTeamsBtn" type="button" disabled' in tpl)
    check("19b: JS가 Teams 버튼을 운영 API로 배선(el(opTeamsBtn) + base 설정 시 활성화)",
          "teamsBtn.disabled = false" in tpl and 'el("opTeamsBtn")' in tpl)
    check("20: Teams 전송은 운영 API 상대 endpoint(send-teams)로 POST(직접 GitHub/발송 아님)",
          TEAMS_ENDPOINT in tpl)


# ---------------------------------------------------------------------------
# 2 · safety — no secrets / no direct privileged API from the browser
# ---------------------------------------------------------------------------

def check_safety(html: str, label: str) -> None:
    low = html.lower()
    leaks = []
    if TOKEN_SHAPE.search(html):
        leaks.append("token-shape")
    if PAT_SHAPE.search(html):
        leaks.append("pat-shape")
    for name in SECRET_NAMES:
        if name in html:
            leaks.append(name)
    check(f"12[{label}]: 토큰/시크릿 문자열 없음", not leaks, ", ".join(leaks))
    check(f"13[{label}]: api.github.com 미포함", "api.github.com" not in low)
    check(f"13b[{label}]: api.telegram.org 미포함", "api.telegram.org" not in low)
    check(f"14[{label}]: sendMessage 미포함", "sendmessage" not in low)
    auto = re.search(r"telegram_auto_send\s*[=:]\s*['\"]?1", low)
    check(f"15[{label}]: TELEGRAM_AUTO_SEND=1 하드코딩 없음", not auto)
    # 브라우저가 GitHub/Telegram 호스트로 직접 fetch/XHR POST하지 않는다.
    xhr = "xmlhttprequest" in low
    fetch_host = re.search(r"fetch\(\s*[`'\"][^`'\"]*(github|telegram)", low)
    post_host = re.search(r"(github|telegram)[^\n]{0,200}?method\s*:\s*['\"]?post", low)
    check(f"16[{label}]: GitHub/Telegram로의 브라우저 fetch/XHR POST 없음",
          not (xhr or fetch_host or post_host),
          "xhr" if xhr else ("fetch" if fetch_host else ("post" if post_host else "")))


# ---------------------------------------------------------------------------
# 17 · existing report/dashboard inline-button mapping unchanged (send_telegram)
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
    print(f"== verify_operator_controls (D7-AA) @ {ROOT} ==")

    if not check("1: docs/daily/dashboard-latest.html 존재 (컨트롤 표면)", DASHBOARD.exists()):
        print("\nRESULT: FAIL (대시보드 산출물 누락)")
        return 1
    dash = _read(DASHBOARD)

    check_buttons(dash)
    check_unset_behavior(dash)
    check_copy(dash)
    check_teams_control(_read(TEMPLATE) if TEMPLATE.exists() else "")

    # 11~16 · 공개 HTML 안전성 — 대시보드/운영자/리포트 + 소스 템플릿 모두 스캔.
    safety_targets = [(dash, "dashboard")]
    check("2: docs/daily/operator-latest.html 존재 시 함께 검사",
          True, "존재" if OPERATOR.exists() else "미존재(스킵)")
    if OPERATOR.exists():
        safety_targets.append((_read(OPERATOR), "operator"))
    if LATEST.exists():
        safety_targets.append((_read(LATEST), "latest"))
    if TEMPLATE.exists():
        safety_targets.append((_read(TEMPLATE), "template"))
    for html, label in safety_targets:
        check_safety(html, label)

    check_report_dashboard_mapping()

    print()
    if _failures:
        print(f"RESULT: FAIL ({len(_failures)} 항목)")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("RESULT: PASS — 운영자 버튼이 운영 API(POST) 직접 실행 · GitHub 이동/시크릿 노출 없음 (D7-AA)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
