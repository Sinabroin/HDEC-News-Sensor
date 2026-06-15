"""P0-C1.5 검증기 — live 일일 리포트 게시 경로 회귀 검사.

목적: Telegram Notify 워크플로가 "예약/빈 메시지" 실행에서 NEWS_MODE=live로
docs/daily/latest.html를 생성해 main에 auto-commit하고, NEWS_MODE=live 다이제스트를
발송하는 경로가 안전하고 정직한지 기계적으로 보장한다.

핵심 원칙 (네트워크 없이도 대부분 결정적으로 통과한다):
- 워크플로는 게시/발송 경로에 NEWS_MODE=live를 켜되 검증/파이프라인은 APP_MODE=mock 유지.
- auto-commit을 위해 contents: write 권한이 있고, 변경 시에만 commit한다.
- live 수집 성공(live_ok=true)일 때만 commit/발송한다 — 실패 시 작업트리를 복원해
  가짜 live 게시를 막는다 (live failure를 live로 mislabel 금지).
- REPORT_URL/비밀값을 로그로 출력하지 않는다 (vars/secrets로만 주입).
- 발송 전에 기존 verifier들을 모두 실행한다.
- live 빌드는 네트워크가 있으면 news_data_mode=live 리포트를 만들고(실제 href·
  example.com/mock 링크 없음), 없으면 SKIP한다 (가짜 성공 주장 안 함).

저장소의 radar.db는 절대 건드리지 않는다 (모든 app 동작은 temp DB 격리 subprocess).

사용법:
    python3 scripts/verify_live_publish_path.py
    NEWS_MODE=live python3 scripts/verify_live_publish_path.py   # 동일 (live 빌드는 항상 시도)
"""

import json
import os
import py_compile
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "telegram-notify.yml"
REPORT_BUILDER = ROOT / "scripts" / "build_static_report.py"
RADAR_DB = ROOT / "radar.db"

# 발송 전에 반드시 실행돼야 하는 기존 verifier들
REQUIRED_VERIFIERS_IN_WORKFLOW = [
    "verify_telegram_digest.py", "verify_executive_brief.py",
    "verify_static_report.py", "verify_data_source_honesty.py",
    "verify_live_news_ingestion.py",
]

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


def skip(message: str) -> None:
    print(f"[SKIP] {message}")


def warn(message: str) -> None:
    print(f"[WARN] {message}")


def _db_state() -> tuple | None:
    if not RADAR_DB.exists():
        return None
    stat = RADAR_DB.stat()
    return (stat.st_mtime_ns, stat.st_size)


def _clean_live_env(**extra: str) -> dict:
    env = {**os.environ, "APP_MODE": "mock"}
    for key in ("MESSAGE", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_IDS",
                "DB_PATH", "REPORT_URL"):
        env.pop(key, None)
    env.update(extra)
    return env


# ---------- 정적 검사 ----------

def check_py_compile() -> None:
    bad = []
    targets = sorted(list((ROOT / "scripts").glob("*.py"))
                     + list((ROOT / "app").glob("*.py")))
    with tempfile.TemporaryDirectory(prefix="hdec_pyc_") as tmp:
        for i, path in enumerate(targets):
            try:
                py_compile.compile(str(path), cfile=os.path.join(tmp, f"{i}.pyc"),
                                   doraise=True)
            except py_compile.PyCompileError as exc:
                bad.append(f"{path.name}: {exc.msg.strip().splitlines()[-1]}")
    check("py_compile scripts/*.py app/*.py", not bad, "; ".join(bad))


def check_builder_emits_mode() -> None:
    """build_static_report.py --output가 news_data_mode를 출력해야
    CI가 단일 빌드로 live/mock을 판별할 수 있다."""
    src = REPORT_BUILDER.read_text(encoding="utf-8")
    check("리포트 빌더가 --output에 news_data_mode 출력 (CI 단일 빌드 판별용)",
          "news_data_mode={brief.get(" in src)


def check_workflow_publish() -> None:
    if not check("telegram-notify.yml 존재", WORKFLOW.exists()):
        return
    text = WORKFLOW.read_text(encoding="utf-8")

    check("workflow에 NEWS_MODE=live (live 게시/발송 경로)",
          bool(re.search(r"NEWS_MODE:\s*live", text)))
    check("workflow가 APP_MODE=mock 유지 (검증/파이프라인 안전)",
          "APP_MODE: mock" in text)
    check("workflow에 contents: write 권한 (auto-commit)",
          bool(re.search(r"contents:\s*write", text)))
    check("workflow에 token 모양 하드코딩 없음", not TOKEN_SHAPE.search(text))

    # 게시 빌드 + 변경 시에만 commit
    check("workflow가 docs/daily/latest.html를 NEWS_MODE=live로 빌드",
          "build_static_report.py --output docs/daily/latest.html" in text)
    check("workflow가 변경 시에만 commit (git diff --quiet 가드)",
          "git diff --quiet" in text)
    check("workflow commit 메시지 'chore: update live daily report'",
          "chore: update live daily report" in text)
    check("workflow가 github-actions[bot] 신원 사용", "github-actions[bot]" in text)

    # 가짜 live 게시 차단: live 성공일 때만 commit/발송 + 실패 시 작업트리 복원
    check("workflow commit/발송이 live_ok=true에 게이트됨",
          "steps.report.outputs.live_ok == 'true'" in text)
    check("workflow가 live 실패 시 작업트리 복원 (가짜 live 게시 차단)",
          "git checkout -- docs/daily/latest.html" in text)

    # REPORT_URL은 env 주입(REPORT_URL:)으로만 등장해야 한다. 주석(#)은 런타임에
    # 출력되지 않으므로 무해 — env 검사 대상에서 제외한다 (verify_static_report와 동일).
    report_lines = [ln for ln in text.splitlines()
                    if "REPORT_URL" in ln and not ln.strip().startswith("#")]
    env_lines = [ln for ln in report_lines if re.match(r"\s*REPORT_URL\s*:", ln)]
    non_env = [ln.strip() for ln in report_lines if ln not in env_lines]
    check("REPORT_URL은 env 주입 라인으로만 등장 (run/echo 노출 없음)",
          bool(env_lines) and not non_env, "; ".join(non_env[:2]))
    src_ok = all(("vars.REPORT_URL" in ln or "secrets.REPORT_URL" in ln)
                 and "http" not in ln.lower() for ln in env_lines)
    check("REPORT_URL이 vars/secrets로만 주입 (URL 하드코딩 없음)", src_ok)

    # 비밀값/REPORT_URL을 출력(echo/printf)하지 않는다
    leak = [ln.strip() for ln in text.splitlines()
            if ("echo" in ln or "printf" in ln)
            and re.search(r"REPORT_URL|TELEGRAM_BOT_TOKEN|TELEGRAM_CHAT_IDS|secrets\.", ln)]
    check("workflow가 secrets/REPORT_URL을 출력하지 않음", not leak, "; ".join(leak[:2]))

    # 발송 전에 기존 verifier들을 모두 실행 (send_telegram.py보다 앞)
    send_idx = text.find("send_telegram.py")
    check("workflow에 send_telegram.py 발송 step 존재", send_idx >= 0)
    for v in REQUIRED_VERIFIERS_IN_WORKFLOW:
        idx = text.find(v)
        check(f"workflow가 {v}를 발송 전에 실행",
              0 <= idx < send_idx if send_idx >= 0 else idx >= 0,
              f"idx={idx}, send={send_idx}")

    # YAML 파싱 (PyYAML 있으면)
    try:
        import yaml
        try:
            yaml.safe_load(text)
            check("workflow YAML 파싱", True)
        except yaml.YAMLError as exc:
            check("workflow YAML 파싱", False, str(exc).splitlines()[0])
    except ImportError:
        warn("PyYAML 없음 — YAML 파싱 검사 생략 (구조 검사로 대체)")
        check("workflow 구조 검사 (jobs/steps/permissions 존재)",
              "jobs:" in text and "steps:" in text and "permissions:" in text)


# ---------- mock 경로 무결성 ----------

def check_mock_path_intact() -> None:
    """mock 경로가 그대로 동작하는지 — build_static_report --json이 mock을 반환.

    (전체 mock verifier 스위트는 워크플로의 별도 step에서 실행한다 — 여기서는
    O(n^2) 재실행을 피하고 mock 경로가 깨지지 않았다는 대표 검사만 한다.)
    """
    proc = subprocess.run(
        [sys.executable, str(REPORT_BUILDER), "--json"],
        capture_output=True, text=True,
        env=_clean_live_env(NEWS_MODE="mock"), cwd=ROOT, timeout=300)
    if not check("mock report --json 동작 (exit 0)", proc.returncode == 0,
                 (proc.stderr or "").strip()[-200:]):
        return
    try:
        meta = json.loads(proc.stdout)
    except ValueError as exc:
        check("mock report --json 파싱", False, str(exc))
        return
    check("mock 경로 news_data_mode=mock 유지", meta.get("news_data_mode") == "mock",
          str(meta.get("news_data_mode")))
    check("mock 경로 signal_count 1~3 유지",
          isinstance(meta.get("signal_count"), int)
          and 1 <= meta["signal_count"] <= 3, str(meta.get("signal_count")))


# ---------- live 실패 정직성 (네트워크 없이 결정적) ----------

def check_live_failure_not_mislabeled() -> None:
    """live 수집이 0건이면 리포트가 live를 주장하지 않고 mock으로 정직 강등되는지.

    live_collector.fetch_all을 빈 리스트로 패치해 'live 실패'를 시뮬레이션하고,
    NEWS_MODE=live에서 빌드된 리포트가 mock 모드(데모 배지)로 표기되는지 본다.
    """
    code = (
        "import os, sys, json, tempfile\n"
        "d = tempfile.mkdtemp()\n"
        "os.environ['DB_PATH'] = os.path.join(d, 't.db')\n"
        "os.environ['APP_MODE'] = 'mock'\n"
        "os.environ['NEWS_MODE'] = 'live'\n"
        "sys.path.insert(0, '.'); sys.path.insert(0, 'scripts')\n"
        "from app import live_collector\n"
        "live_collector.fetch_all = lambda *a, **k: []\n"   # live 실패 시뮬레이션
        "from build_static_report import (build_brief_via_mock_pipeline,\n"
        "                                 render_report_html, _mode_pill)\n"
        "brief = build_brief_via_mock_pipeline()\n"
        "html, _ = render_report_html(brief)\n"
        "print(json.dumps({'mode': brief['news_data_mode'], 'pill': _mode_pill(brief),\n"
        " 'live_badge': 'LIVE · 공개 RSS' in html, 'fallback': brief.get('news_fallback_used')}))\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True,
                          text=True, cwd=ROOT, timeout=180)
    if proc.returncode != 0:
        check("live 실패 → mock으로 정직 강등 (mislabel 금지)", False,
              (proc.stderr or "").strip()[-200:])
        return
    try:
        res = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        check("live 실패 → mock으로 정직 강등 (mislabel 금지)", False,
              (proc.stdout or "")[-200:])
        return
    check("live 실패 → news_data_mode=mock (live 주장 안 함)",
          res.get("mode") == "mock", str(res))
    check("live 실패 → 리포트 모드 배지가 'LIVE · 공개 RSS' 아님",
          res.get("live_badge") is False)
    check("live 실패 → 모드 배지 '데모 데이터'", res.get("pill") == "데모 데이터",
          str(res.get("pill")))
    check("live 실패 → news_fallback_used=True (정직 표기)",
          res.get("fallback") is True)


# ---------- 실제 live 빌드 (SKIP-friendly) ----------

def _href_values(html: str) -> list[str]:
    return re.findall(r'href="([^"]*)"', html)


def _anchor_safety_issues(html: str) -> list[str]:
    bad = []
    for m in re.finditer(r"<a\b[^>]*>", html):
        tag = m.group(0)
        href = re.search(r'href="([^"]*)"', tag)
        if not href or not href.group(1).lower().startswith(("http://", "https://")):
            continue
        if 'target="_blank"' not in tag:
            bad.append("target 누락: " + tag[:60])
        if "noopener" not in tag or "noreferrer" not in tag:
            bad.append("rel noopener noreferrer 누락: " + tag[:60])
    return bad


def check_live_build_optional() -> None:
    """NEWS_MODE=live로 실제 리포트를 빌드해 본다 (네트워크 필요).

    성공(news_data_mode=live)하면 실제 href·정직 라벨을 검증한다. 네트워크가
    없으면 mock fallback이 되며 SKIP한다 (가짜 live 성공을 주장하지 않는다).
    """
    with tempfile.TemporaryDirectory(prefix="hdec_pub_") as tmp:
        out = Path(tmp) / "latest.html"
        try:
            proc = subprocess.run(
                [sys.executable, str(REPORT_BUILDER), "--output", str(out)],
                capture_output=True, text=True,
                env=_clean_live_env(NEWS_MODE="live"), cwd=ROOT, timeout=240)
        except subprocess.TimeoutExpired:
            skip("live 빌드 타임아웃 (네트워크 지연?) — SKIP, 가짜 성공 주장 안 함")
            return
        if proc.returncode != 0 or not out.exists():
            skip(f"live 빌드 실패 — SKIP ({(proc.stderr or '').strip()[-120:]})")
            return

        html = out.read_text(encoding="utf-8")
        if "news_data_mode=live" not in (proc.stdout or ""):
            skip("live 수집 0건/네트워크 차단 → mock fallback — SKIP, 가짜 live 주장 안 함")
            # fallback 리포트라도 live를 사칭하지 않는지 최소 확인
            check("fallback 빌드는 'LIVE · 공개 RSS' 배지를 달지 않음",
                  "LIVE · 공개 RSS" not in html)
            return

        print("[LIVE] news_data_mode=live 리포트 생성됨")
        check("LIVE: news_data_mode=live 리포트 생성", True)
        check("LIVE: live 출처 표기 (공개 RSS 수집)", "공개 RSS 수집" in html)
        check("LIVE: '데모 데이터' 배지 없음 (live를 mock으로 표기 안 함)",
              "데모 데이터" not in html)

        hrefs = _href_values(html)
        http_hrefs = [h for h in hrefs if h.lower().startswith(("http://", "https://"))]
        check("LIVE: 실제 기사 href(http) 1건 이상", bool(http_hrefs),
              f"{len(http_hrefs)}건")
        bad_links = [h for h in hrefs
                     if "example.com" in h.lower() or "mock" in h.lower()]
        check("LIVE: example.com/mock placeholder 링크 없음", not bad_links,
              ", ".join(bad_links[:3]))

        anchor_bad = _anchor_safety_issues(html)
        check("LIVE: 외부 링크 새 탭 + noopener noreferrer", not anchor_bad,
              "; ".join(anchor_bad[:2]))

        # live 리포트도 시장지표는 미연동 — 가짜 시세 노출 금지
        check("LIVE: 시장지표 미연동 표기 유지", "미연동" in html)


def main() -> int:
    print(f"== verify_live_publish_path @ {ROOT} ==")
    db_before = _db_state()

    check_py_compile()
    check_builder_emits_mode()
    check_workflow_publish()
    check_mock_path_intact()
    check_live_failure_not_mislabeled()
    check_live_build_optional()

    check("repo의 radar.db가 검증 중 변경/생성되지 않음 (temp DB 격리)",
          _db_state() == db_before)

    if _failures:
        print(f"\nRESULT: FAIL ({len(_failures)} failed)")
        for name in _failures:
            print(f"  - {name}")
        return 1
    print("\nRESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
