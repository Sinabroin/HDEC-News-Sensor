#!/usr/bin/env python3
"""D6-F verifier — full report mobile readability and link identity.

Checks the generated Executive Daily Brief HTML/CSS without sending Telegram:
- 390px phone safeguards: viewport, no body overflow, wrapping, compact header.
- Mobile layout rules: single-column grids, compact non-sticky tabs, table scroll fallback.
- Published latest.html remains the full report and uses builder CSS.
- dashboard-latest.html remains the separate summary dashboard.
- Telegram A/B labels still map summary -> dashboard and full report -> latest.
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


def _run(args: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True,
                          env=_clean_env(), timeout=timeout)


def _style_block(html: str) -> str:
    match = re.search(r"<style>(.*?)</style>", html, re.S)
    return match.group(1) if match else ""


def _selector_block(css: str, selector: str) -> str:
    pattern = re.escape(selector) + r"\{([^}]*)\}"
    match = re.search(pattern, css, re.S)
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


def _large_fixed_widths(css: str) -> list[str]:
    issues: list[str] = []
    for prop in ("width", "min-width"):
        pattern = rf"(?:(?<=\{{)|(?<=;))\s*{prop}\s*:\s*(\d+)px"
        for match in re.finditer(pattern, css):
            value = int(match.group(1))
            if value > 390:
                issues.append(f"{prop}:{value}px")
    for match in re.finditer(r"flex\s*:\s*0\s+0\s+(\d+)px", css):
        value = int(match.group(1))
        if value > 300:
            issues.append(f"flex-basis:{value}px")
    return issues


def check_generated_mobile_css() -> None:
    with tempfile.TemporaryDirectory(prefix="hdec_mobile_report_") as tmp:
        out = Path(tmp) / "daily" / "latest.html"
        proc = _run([
            sys.executable, str(REPORT_BUILDER), "--output", str(out),
            "--audience", "executive",
        ])
        ok = proc.returncode == 0 and out.exists()
        check("report builder regenerates executive latest.html", ok,
              (proc.stderr or "").strip()[-200:])
        if not ok:
            return
        html = out.read_text(encoding="utf-8")

    css = _style_block(html)
    mobile = _mobile_block(css)
    check("generated report has viewport meta",
          '<meta name="viewport" content="width=device-width, initial-scale=1">' in html)
    check("generated report has full-report identity",
          "Executive Daily Brief" in html and "dashboard-export:summary" not in html
          and 'id="preview-model"' not in html)
    check("CSS includes pseudo-element border-box reset",
          "*,*::before,*::after{box-sizing:border-box;}" in css)
    check("html/body guard against horizontal overflow",
          "max-width:100%" in _selector_block(css, "html")
          and "overflow-x:hidden" in _selector_block(css, "html")
          and "overflow-x:hidden" in _selector_block(css, "body"))
    check("long titles/URLs wrap safely", "overflow-wrap:anywhere" in css)
    check("table content can scroll inside container",
          "table{display:block;max-width:100%;overflow-x:auto" in css)
    check("mobile media query exists", bool(mobile))
    check("mobile status board collapses to one column",
          ".board{grid-template-columns:1fr" in mobile)
    check("mobile signal cards collapse to one column",
          ".sig{grid-template-columns:1fr" in mobile)
    check("mobile macro grid collapses to one column",
          ".duo,.macro-grid{grid-template-columns:1fr" in mobile)
    check("mobile header is compact",
          ".masthead h1{font-size:24px" in mobile
          and ".page{padding:18px 14px 36px" in mobile)
    check("mobile tabs are compact and non-sticky",
          ".topnav{position:static;top:auto;z-index:auto;flex-wrap:wrap;overflow-x:visible" in mobile
          and ".topnav label{flex:0 1 auto" in mobile)
    check("mobile evidence rows stack score below title",
          ".cd-art-head{flex-direction:column" in mobile)
    check("mobile evidence flow exposes articles before full category drawer",
          '<details class="evidence-mobile-filter">' in html
          and '<section class="mobile-evidence-stream"' in html
          and '<details class="mobile-category-drill">' in html
          and html.find('<section class="mobile-evidence-stream"')
          < html.find('<details class="mobile-category-drill">'))
    fixed = _large_fixed_widths(css)
    check("no fixed large width/min-width that can force 390px overflow",
          not fixed, ", ".join(fixed[:8]))


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
    check("docs/daily/dashboard-latest.html remains summary dashboard",
          "dashboard-export:summary" in dashboard and 'id="preview-model"' in dashboard
          and dashboard != latest)
    check("docs/daily/operator-latest.html remains supported",
          "Executive Daily Brief" in operator
          and ("운영자" in operator or "operator" in operator.lower()))


def check_telegram_mapping() -> None:
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

    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / "send_telegram.py"), "--dry-run-payload", "test"],
        cwd=ROOT, capture_output=True, text=True,
        env=_clean_env(REPORT_URL=SAMPLE_REPORT_URL,
                       DASHBOARD_URL=SAMPLE_DASHBOARD_URL),
        timeout=120,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    check("dry-run payload preserves Telegram A/B labels",
          proc.returncode == 0 and SUMMARY_BUTTON_TEXT in out and REPORT_BUTTON_TEXT in out)
    check("dry-run payload preserves Telegram URL mapping",
          f"{SUMMARY_BUTTON_TEXT} -> {SAMPLE_DASHBOARD_URL}" in out
          and f"{REPORT_BUTTON_TEXT} -> {SAMPLE_REPORT_URL}" in out)


def main() -> int:
    print(f"== verify_mobile_report_readability @ {ROOT} ==")
    check_generated_mobile_css()
    check_published_outputs()
    check_telegram_mapping()

    if _failures:
        print(f"\nRESULT: FAIL ({len(_failures)} failed)")
        for name in _failures:
            print(f"  - {name}")
        return 1
    print("\nRESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
