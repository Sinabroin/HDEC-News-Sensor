#!/usr/bin/env python3
"""D6-G verifier — mobile evidence layout structure.

Checks the full report generator/output without sending Telegram:
- mobile evidence controls are compact drawers, not a dominant article blocker
- mobile evidence articles appear before the full category inventory
- mobile CSS disables sticky/fixed tab behavior and forces single-column flow
- latest/dashboard identity and Telegram A/B URL mapping remain intact
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
REPORT_BUILDER = SCRIPTS / "build_static_report.py"
LATEST = ROOT / "docs" / "daily" / "latest.html"
DASHBOARD = ROOT / "docs" / "daily" / "dashboard-latest.html"
OPERATOR = ROOT / "docs" / "daily" / "operator-latest.html"
SENDER = SCRIPTS / "send_telegram.py"
WORKFLOW = ROOT / ".github" / "workflows" / "telegram-notify.yml"

SUMMARY_BUTTON_TEXT = "대시보드 보기"
REPORT_BUTTON_TEXT = "상세 리포트 보기"
SAMPLE_REPORT_URL = "https://example.com/daily/latest.html"
SAMPLE_DASHBOARD_URL = "https://example.com/daily/dashboard-latest.html"

_failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    tag = "PASS" if ok else "FAIL"
    line = f"[{tag}] {name}"
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


def _clean_env(**extra: str) -> dict[str, str]:
    env = {**os.environ, "APP_MODE": "mock", "NEWS_MODE": "mock"}
    for key in (
        "MESSAGE", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_IDS",
        "TELEGRAM_SEND_MODE", "REVIEW_APPROVED", "CONFIRM_SEND",
        "REPORT_URL", "DASHBOARD_URL", "TELEGRAM_BOT_USERNAME",
        "TELEGRAM_PERSONAL_BOT_URL", "DB_PATH", "MACRO_MODE",
        "NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET",
    ):
        env.pop(key, None)
    env.update(extra)
    return env


def _run(args: list[str], timeout: int = 300,
         env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True,
                          env=env or _clean_env(), timeout=timeout)


def _style_block(html: str) -> str:
    match = re.search(r"<style>(.*?)</style>", html, re.S)
    return match.group(1) if match else ""


def _mobile_block(css: str) -> str:
    marker = "@media(max-width:640px){"
    start = css.find(marker)
    if start < 0:
        return ""
    pos = start + len(marker)
    depth = 1
    while pos < len(css) and depth:
        if css[pos] == "{":
            depth += 1
        elif css[pos] == "}":
            depth -= 1
        pos += 1
    return css[start + len(marker):pos - 1] if depth == 0 else ""


def _build_temp_report() -> str:
    with tempfile.TemporaryDirectory(prefix="hdec_mobile_layout_") as tmp:
        out = Path(tmp) / "daily" / "latest.html"
        proc = _run([
            sys.executable, str(REPORT_BUILDER), "--output", str(out),
            "--audience", "executive",
        ])
        ok = proc.returncode == 0 and out.exists()
        check("report builder regenerates executive report", ok,
              (proc.stderr or "").strip()[-200:])
        return out.read_text(encoding="utf-8") if ok else ""


def _position_order(html: str, needles: list[str]) -> tuple[bool, str]:
    positions = [html.find(needle) for needle in needles]
    ok = all(pos >= 0 for pos in positions) and positions == sorted(positions)
    return ok, " -> ".join(str(pos) for pos in positions)


def _mobile_stream_block(html: str) -> str:
    start = html.find('<section class="mobile-evidence-stream"')
    if start < 0:
        return ""
    end = html.find('<section class="category-drill-section"', start)
    return html[start:end if end >= 0 else len(html)]


def check_generated_mobile_structure() -> None:
    html = _build_temp_report()
    if not html:
        return
    css = _style_block(html)
    mobile = _mobile_block(css)

    check("generated latest remains full report",
          "Executive Daily Brief" in html and "dashboard-export:summary" not in html
          and 'id="preview-model"' not in html)
    check("desktop evidence overview is structurally separate",
          '<div class="evidence-overview-desktop">' in html)
    check("mobile lens filter is a closed details drawer",
          '<details class="evidence-mobile-filter">' in html
          and "필터/렌즈 보기" in html)
    check("mobile representative evidence stream exists",
          '<section class="mobile-evidence-stream"' in html
          and "대표 근거 기사" in html)
    check("mobile full category list is a closed drawer",
          '<details class="mobile-category-drill">' in html
          and "전체 카테고리 목록 보기" in html)
    ok, detail = _position_order(html, [
        '<details class="evidence-mobile-filter">',
        '<section class="mobile-evidence-stream"',
        '<details class="mobile-category-drill">',
    ])
    check("mobile flow is filter drawer -> articles -> full category drawer", ok, detail)

    stream = _mobile_stream_block(html)
    check("mobile stream contains article content before category inventory",
          '<article class="cd-art">' in stream or "표시 가능한 대표 근거" in stream)
    check("mobile details drawers are not open by default",
          '<details class="evidence-mobile-filter" open' not in html
          and '<details class="mobile-category-drill" open' not in html)

    check("mobile media query exists", bool(mobile))
    check("mobile tabs are not sticky/fixed overlays",
          ".topnav{position:static;top:auto;z-index:auto" in mobile
          and "position:fixed" not in mobile
          and "position:sticky" not in mobile)
    check("mobile tab controls wrap in normal flow",
          "flex-wrap:wrap;overflow-x:visible" in mobile
          and ".topnav label{flex:0 1 auto" in mobile)
    check("mobile hides desktop lens/category overview",
          ".evidence-overview-desktop,.category-drill-desktop{display:none" in mobile)
    check("mobile shows compact lens drawer",
          ".evidence-mobile-filter{display:block" in mobile)
    check("mobile shows article stream before category drawer",
          ".mobile-evidence-stream{display:block" in mobile)
    check("mobile full category inventory stays behind drawer",
          ".mobile-category-drill{display:block" in mobile)
    check("mobile evidence layout is single-column",
          ".duo,.macro-grid{grid-template-columns:1fr" in mobile
          and ".evidence-mobile-filter .duo{grid-template-columns:1fr" in mobile
          and "grid-template-columns:1.1fr 1fr" not in mobile)


def check_published_outputs() -> None:
    latest = _read(LATEST)
    dashboard = _read(DASHBOARD)
    operator = _read(OPERATOR)
    sys.path.insert(0, str(SCRIPTS))
    import build_static_report

    latest_css = _style_block(latest)
    check("docs/daily/latest.html exists", bool(latest))
    check("latest.html remains Executive Daily Brief",
          "Executive Daily Brief" in latest and "dashboard-export:summary" not in latest
          and 'id="preview-model"' not in latest)
    check("latest.html CSS is synchronized with report builder",
          latest_css.strip() == build_static_report._CSS.strip())
    check("published latest contains mobile article-first evidence flow",
          '<details class="evidence-mobile-filter">' in latest
          and '<section class="mobile-evidence-stream"' in latest
          and '<details class="mobile-category-drill">' in latest)
    check("docs/daily/dashboard-latest.html remains summary dashboard",
          "dashboard-export:summary" in dashboard and 'id="preview-model"' in dashboard
          and dashboard != latest)
    check("docs/daily/operator-latest.html remains supported",
          "Executive Daily Brief" in operator
          and ("운영자" in operator or "operator" in operator.lower()))


def check_telegram_mapping_and_send_files() -> None:
    sys.path.insert(0, str(SCRIPTS))
    import send_telegram

    payload = send_telegram.build_payload(
        "DRY", "message", SAMPLE_REPORT_URL, "", SAMPLE_DASHBOARD_URL)
    buttons = json.loads(payload["reply_markup"])["inline_keyboard"][0]
    labels = [item["text"] for item in buttons]
    urls = [item["url"] for item in buttons]
    check("Telegram summary label maps to dashboard-latest.html",
          labels[0] == SUMMARY_BUTTON_TEXT and urls[0] == SAMPLE_DASHBOARD_URL,
          f"{labels[:1]} -> {urls[:1]}")
    check("Telegram full-report label maps to latest.html",
          labels[1] == REPORT_BUTTON_TEXT and urls[1] == SAMPLE_REPORT_URL,
          f"{labels[1:2]} -> {urls[1:2]}")

    proc = _run(
        [sys.executable, str(SENDER), "--dry-run-payload", "test"],
        env=_clean_env(REPORT_URL=SAMPLE_REPORT_URL,
                       DASHBOARD_URL=SAMPLE_DASHBOARD_URL),
        timeout=120,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    check("dry-run payload preserves Telegram URL mapping",
          proc.returncode == 0
          and f"{SUMMARY_BUTTON_TEXT} -> {SAMPLE_DASHBOARD_URL}" in out
          and f"{REPORT_BUTTON_TEXT} -> {SAMPLE_REPORT_URL}" in out)

    # D6-I: 전체 리포트/요약 대시보드 버튼 타겟 swap 회귀를 막기 위해 send_telegram.py를
    # 의도적으로 수정했다(매핑을 파일명으로 강제 — _normalize_report_targets). 따라서
    # sender/workflow를 HEAD에 byte 고정하던 기존 boundary 검사는 더 이상 유효하지 않다.
    # 매핑 정합성은 바로 위(in-process build_payload + dry-run)에서 행위로 검증했다. 여기서는
    # 이 변경이 사람 검토 발송 게이트(자동 발송 금지)를 약화시키지 않았는지와 매핑 강제
    # 로직이 제자리에 있는지 — 안전 불변식만 단언한다(자세한 매핑 회귀 가드는
    # verify_report_link_targets.py).
    sender_src = SENDER.read_text(encoding="utf-8")
    check("send gate intact: 기본 manual + 승인 없이는 발송 안 함 (자동 발송 금지)",
          'DEFAULT_SEND_MODE = "manual"' in sender_src
          and "if not will_send" in sender_src
          and "REVIEW_APPROVED" in sender_src)
    check("버튼 타겟 매핑이 파일명으로 강제됨 (REPORT_URL/DASHBOARD_URL swap 내성)",
          "_normalize_report_targets(report_url, dashboard_url)" in sender_src)
    workflow_src = WORKFLOW.read_text(encoding="utf-8")
    check("workflow가 버튼 URL을 vars/secrets로만 주입 (값 비노출 유지)",
          "vars.DASHBOARD_URL" in workflow_src and "vars.REPORT_URL" in workflow_src)


def main() -> int:
    print(f"== verify_mobile_layout_structure @ {ROOT} ==")
    check_generated_mobile_structure()
    check_published_outputs()
    check_telegram_mapping_and_send_files()

    if _failures:
        print(f"\nRESULT: FAIL ({len(_failures)} failed)")
        for name in _failures:
            print(f"  - {name}")
        return 1
    print("\nRESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
