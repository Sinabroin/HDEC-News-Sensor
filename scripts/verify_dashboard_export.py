#!/usr/bin/env python3
"""D6-A/B verifier — summary dashboard export + Telegram A/B report links.

Runs fully offline. It checks:
- `docs/daily/dashboard-latest.html` exists and is generated from the preview template.
- Telegram payload exposes "대시보드 보기" and "상세 리포트 보기".
- The send path remains behind the human review gate.
- `operator-latest.html` remains supported.
- `latest.html` remains the full report and is not replaced by the dashboard export.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "scripts" / "build_static_dashboard.py"
REPORT_BUILDER = ROOT / "scripts" / "build_static_report.py"
SENDER = ROOT / "scripts" / "send_telegram.py"
WORKFLOW = ROOT / ".github" / "workflows" / "telegram-notify.yml"
DASHBOARD = ROOT / "docs" / "daily" / "dashboard-latest.html"
LATEST = ROOT / "docs" / "daily" / "latest.html"
OPERATOR = ROOT / "docs" / "daily" / "operator-latest.html"
TEMPLATE = ROOT / "templates" / "dashboard_preview.html"

SUMMARY_TEXT = "대시보드 보기"
FULL_REPORT_LABEL = "상세 리포트 보기"
EXPORT_MARKER = "dashboard-export:summary"
SAMPLE_REPORT_URL = "https://example.com/daily/latest.html"
SAMPLE_DASHBOARD_URL = "https://example.com/daily/dashboard-latest.html"
TOKEN_SHAPE = re.compile(r"[0-9]{8,}:[A-Za-z0-9_-]{20,}")

_failures = []


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


def _clean_env(**extra: str) -> dict:
    env = {**os.environ, "APP_MODE": "mock", "NEWS_MODE": "mock"}
    for key in (
        "MESSAGE", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_IDS",
        "TELEGRAM_SEND_MODE", "REVIEW_APPROVED", "CONFIRM_SEND",
        "REPORT_URL", "DASHBOARD_URL", "TELEGRAM_BOT_USERNAME",
        "TELEGRAM_PERSONAL_BOT_URL", "DB_PATH", "MACRO_MODE",
    ):
        env.pop(key, None)
    env.update(extra)
    return env


def _run(args: list[str], env: dict | None = None,
         timeout: int = 180) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True,
                          env=env or _clean_env(), timeout=timeout)


def _git_unchanged(paths: list[str]) -> tuple[bool, str]:
    proc = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", *paths],
                          cwd=ROOT, capture_output=True, text=True, timeout=30)
    if proc.returncode == 0:
        return True, "변경 없음"
    if proc.returncode == 1:
        return False, "HEAD 대비 변경 감지됨"
    return False, f"git diff failed rc={proc.returncode}"


def check_builder_generation() -> None:
    src = _read(BUILDER)
    check("dashboard builder 존재", bool(src))
    check("builder가 preview template을 재사용", "templates\" / \"dashboard_preview.html" in src)
    check("builder가 Telegram/env/secrets를 읽지 않음",
          "TELEGRAM_" not in src and "os.environ" not in src)
    check("builder가 네트워크 라이브러리를 import하지 않음",
          not re.search(r"^\s*(import|from)\s+(urllib|requests|httpx|socket)\b", src, re.M))

    proc = _run([sys.executable, str(BUILDER), "--json"])
    if check("builder --json 동작", proc.returncode == 0,
             (proc.stderr or "").strip()[-200:]):
        try:
            meta = json.loads(proc.stdout)
        except ValueError as exc:
            check("builder --json 파싱", False, str(exc))
        else:
            check("builder default output == dashboard-latest.html",
                  meta.get("default_output") == "docs/daily/dashboard-latest.html")
            check("builder output has preview model",
                  meta.get("has_preview_model") is True)
            check("builder output keeps honesty labels",
                  meta.get("has_data_honesty_labels") is True)
            check("builder output is compact, not a design dump",
                  20_000 < int(meta.get("html_chars") or 0) < 650_000,
                  str(meta.get("html_chars")))

    with tempfile.TemporaryDirectory(prefix="hdec_dash_") as tmp:
        out = Path(tmp) / "daily" / "dashboard-latest.html"
        proc = _run([sys.executable, str(BUILDER), "--output", str(out)])
        ok = proc.returncode == 0 and out.exists() and out.stat().st_size > 20_000
        check("builder --output가 dashboard-latest.html 생성", ok,
              (proc.stderr or "").strip()[-200:])
        if ok:
            html = out.read_text(encoding="utf-8")
            check("generated dashboard export marker", EXPORT_MARKER in html)
            check("generated dashboard preview model 유지", 'id="preview-model"' in html)
            check("generated dashboard no token/chat id", "TELEGRAM_BOT_TOKEN" not in html
                  and "TELEGRAM_CHAT_IDS" not in html and not TOKEN_SHAPE.search(html))


def check_committed_dashboard() -> None:
    if not check("docs/daily/dashboard-latest.html 존재", DASHBOARD.exists()):
        return
    dashboard = _read(DASHBOARD)
    latest = _read(LATEST)
    operator = _read(OPERATOR)

    check("dashboard export marker 포함", EXPORT_MARKER in dashboard)
    check("dashboard가 preview model/대시보드 구조 포함",
          'id="preview-model"' in dashboard and "HDEC Executive Radar" in dashboard)
    check("dashboard가 public 정직성 라벨 유지 + demo residual 제거",
          "데모 데이터" not in dashboard and "현재 체결값 아님" in dashboard
          and "미연동" in dashboard and "기상 데이터 미수신" in dashboard)
    check("dashboard는 latest.html과 다른 파일", dashboard != latest)
    check("latest.html은 전체 Executive Daily Brief로 유지",
          "Executive Daily Brief" in latest and EXPORT_MARKER not in latest
          and 'id="preview-model"' not in latest)
    check("operator-latest.html 존재 및 운영자 뷰 유지",
          bool(operator) and ("운영자" in operator or "operator" in operator.lower()))

    check("docs/daily/latest.html는 dashboard export로 교체되지 않음",
          "Executive Daily Brief" in latest and EXPORT_MARKER not in latest
          and 'id="preview-model"' not in latest)
    ok, detail = _git_unchanged(["docs/daily/operator-latest.html"])
    check("docs/daily/operator-latest.html HEAD 대비 변경 없음", ok, detail)


def check_operator_builder_support() -> None:
    with tempfile.TemporaryDirectory(prefix="hdec_operator_") as tmp:
        out = Path(tmp) / "operator-latest.html"
        proc = _run([
            sys.executable, str(REPORT_BUILDER), "--output", str(out),
            "--audience", "operator",
        ], timeout=300)
        ok = proc.returncode == 0 and out.exists()
        check("build_static_report operator audience 지원", ok,
              (proc.stderr or "").strip()[-200:])
        if ok:
            html = out.read_text(encoding="utf-8")
            check("operator output includes operator diagnostics",
                  "운영자" in html or "operator" in html.lower())


def check_telegram_ab_payload() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    import send_telegram

    payload = send_telegram.build_payload(
        "DRY", "message", SAMPLE_REPORT_URL, "", SAMPLE_DASHBOARD_URL)
    buttons = json.loads(payload["reply_markup"])["inline_keyboard"][0]
    labels = [b["text"] for b in buttons]
    urls = [b["url"] for b in buttons]
    check("Telegram payload contains '대시보드 보기'", SUMMARY_TEXT in labels,
          " / ".join(labels))
    check("Telegram payload contains '상세 리포트 보기'", FULL_REPORT_LABEL in labels,
          " / ".join(labels))
    check("Telegram payload A/B button order: summary then full report",
          labels[:2] == [SUMMARY_TEXT, FULL_REPORT_LABEL], " / ".join(labels[:2]))
    check("Telegram payload URLs map to dashboard/report",
          urls[:2] == [SAMPLE_DASHBOARD_URL, SAMPLE_REPORT_URL], " / ".join(urls[:2]))

    proc = _run([sys.executable, str(SENDER), "--dry-run-payload", "test"],
                env=_clean_env(REPORT_URL=SAMPLE_REPORT_URL,
                               DASHBOARD_URL=SAMPLE_DASHBOARD_URL))
    out = (proc.stdout or "") + (proc.stderr or "")
    check("--dry-run-payload exits 0", proc.returncode == 0, out[-200:])
    check("--dry-run-payload prints summary/full labels",
          SUMMARY_TEXT in out and FULL_REPORT_LABEL in out)
    check("--dry-run-payload uses no token", not TOKEN_SHAPE.search(out))

    resolver = (
        "import sys; sys.path.insert(0, 'scripts'); "
        "from send_telegram import resolve_report_url, resolve_dashboard_url; "
        "r=resolve_report_url(); print(r); print(resolve_dashboard_url(r))"
    )
    proc = _run([sys.executable, "-c", resolver],
                env=_clean_env(REPORT_URL=SAMPLE_REPORT_URL))
    lines = (proc.stdout or "").strip().splitlines()
    check("DASHBOARD_URL 미설정 시 REPORT_URL에서 dashboard-latest 파생",
          proc.returncode == 0 and len(lines) == 2
          and lines[0] == SAMPLE_REPORT_URL
          and lines[1] == SAMPLE_DASHBOARD_URL,
          " / ".join(lines))


def check_send_gate_unchanged() -> None:
    src = _read(SENDER)
    guard_idx = src.find("if not will_send")
    post_idx = src.find("urlopen")
    approved_idx = src.find("Send status: approved")
    check("send path remains gated before Telegram POST",
          0 <= guard_idx < approved_idx < post_idx,
          f"guard={guard_idx}, approved={approved_idx}, post={post_idx}")
    for token in ("DEFAULT_SEND_MODE = \"manual\"", "TELEGRAM_SEND_MODE",
                  "REVIEW_APPROVED", "CONFIRM_SEND", "review_required"):
        check(f"send gate token 유지: {token}", token in src)


def check_workflow_contract() -> None:
    text = _read(WORKFLOW)
    if not check("telegram workflow 존재", bool(text)):
        return
    check("workflow builds dashboard-latest.html",
          "build_static_dashboard.py --output docs/daily/dashboard-latest.html" in text)
    check("workflow exact-stages dashboard with daily reports",
          "git add docs/daily/latest.html docs/daily/operator-latest.html docs/daily/dashboard-latest.html"
          in text)
    check("workflow injects DASHBOARD_URL via vars/secrets only",
          bool(re.search(r"DASHBOARD_URL:\s*\$\{\{[^}]*"
                         r"(vars|secrets)\.DASHBOARD_URL", text)))
    check("workflow keeps REPORT_URL via vars/secrets only",
          bool(re.search(r"REPORT_URL:\s*\$\{\{[^}]*"
                         r"(vars|secrets)\.REPORT_URL", text)))
    check("workflow send mode still approve_send-gated",
          "github.event.inputs.approve_send == 'true' && 'send' || 'manual'" in text)


def main() -> int:
    print(f"== verify_dashboard_export @ {ROOT} ==")
    check_builder_generation()
    check_committed_dashboard()
    check_operator_builder_support()
    check_telegram_ab_payload()
    check_send_gate_unchanged()
    check_workflow_contract()

    print()
    if _failures:
        print(f"RESULT: FAIL ({len(_failures)} failed)")
        for name in _failures:
            print(f"  - {name}")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
