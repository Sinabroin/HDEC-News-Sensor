"""D7-P 검증기 — 예약 라이브 갱신 + 게이트된 Telegram 자동 발송 (완전 오프라인).

검사 계약:
  A. 예약 워크플로가 매시간 정각 cron(0 * * * *)을 사용한다.
  B. 예약 발송 기본값은 dry-run (발송 0건) — TELEGRAM_AUTO_SEND 미설정이면 보내지 않는다.
  C. 발송 opt-in 게이트(TELEGRAM_AUTO_SEND)가 소스/워크플로에 존재하고, 하드코딩 자동발송이 없다.
  D. 새 파일(스크립트·워크플로·검증기)에 토큰/시크릿 모양 문자열이 0건이다.
  E. generated_at(생성 시각) freshness 게이트 — 오래된(stale) 산출물은 자동 발송하지 않는다.
  F. mock/live 정직성 게이트 — news_data_mode != live 면 발송하지 않는다(ALLOW_MOCK 제외).
  G. 공개 예약 워크플로에 비공개 감시 목록 경로(SITE_WATCHLIST_PATH/data/private)가 없다.
  H. live·fresh·opt-in이라도 자격증명이 없으면 실제 발송 0건(가짜 발송 위장 금지).
  I. 실제 발송은 send_telegram.py(D3I 단일 진실원)에 위임하고 POST를 재구현하지 않는다.

모든 검사는 네트워크 호출 0건·실제 Telegram POST 0건이다. 자격증명을 주지 않고
비교 기준 시각(now)을 고정 주입하므로 시간 드리프트 없이 결정적이다.

사용법:
    python3 scripts/verify_scheduled_refresh_and_telegram.py
"""

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHED_SENDER = ROOT / "scripts" / "send_scheduled_telegram.py"
SENDER = ROOT / "scripts" / "send_telegram.py"
WORKFLOW = ROOT / ".github" / "workflows" / "scheduled-live-refresh.yml"
SELF = Path(__file__).resolve()

# 비교 기준 시각을 고정 주입(KST 정오) — freshness 검사를 드리프트 없이 결정적으로.
NOW_ISO = "2026-06-26T12:00:00+09:00"
FRESH_KST = "2026-06-26 11:00"   # NOW 기준 1시간 전 (fresh)
STALE_KST = "2026-06-23 12:00"   # NOW 기준 72시간 전 (stale)

# 발송 의도 없는 테스트 자격증명 (비밀값 아님 · token 모양 아님). truthy 여부만 쓰인다.
FIXTURE_TOKEN = "scheduled-gate-probe-not-a-real-token"
FIXTURE_CHAT_IDS = "111,222"

# 실제 발송으로 새는 흔적 — 어떤 비발송 경로에도 등장해선 안 된다.
SENT_MARKERS = ("delivered=", "Send status: approved", "실제 발송 진행")
TOKEN_SHAPE = re.compile(r"[0-9]{8,}:[A-Za-z0-9_-]{20,}")
HEADER_TEXT = "HDEC Executive Radar"

EXPECTED_HOURLY_CRON = "0 * * * *"

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


def _clean_env(**extra: str) -> dict:
    """예약 발송 검사용 환경 — 외부 TELEGRAM_*/승인/모드 오염을 제거하고 mock 고정."""
    env = {**os.environ, "APP_MODE": "mock"}
    for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_IDS", "TELEGRAM_SEND_MODE",
                "REVIEW_APPROVED", "CONFIRM_SEND", "TELEGRAM_AUTO_SEND",
                "TELEGRAM_ALLOW_MOCK", "TELEGRAM_MAX_AGE_HOURS", "SCHEDULED_NOW",
                "REPORT_URL", "DASHBOARD_URL", "TELEGRAM_BOT_USERNAME",
                "TELEGRAM_PERSONAL_BOT_URL", "MESSAGE", "NEWS_MODE", "MACRO_MODE"):
        env.pop(key, None)
    env.update(extra)
    return env


def run_scheduled(args, timeout=240, **env_extra) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCHED_SENDER), *args],
        capture_output=True, text=True, env=_clean_env(**env_extra),
        cwd=str(ROOT), timeout=timeout)


def _combined(proc: subprocess.CompletedProcess) -> str:
    return (proc.stdout or "") + (proc.stderr or "")


def _claims_sent(text: str) -> bool:
    return any(marker in text for marker in SENT_MARKERS)


def _artifact_html(mode: str, generated_kst: str,
                   latest_kst: str = "2026-06-26 10:30") -> str:
    """빌더 산출물의 정직성 마커(news-data-mode·pubKst·latest_article_kst)를 모사한 HTML."""
    return (
        "<!doctype html><html><head>"
        f"<!--news-data-mode:{mode}-->"
        f'<script>var MODEL={{"latest_article_kst": "{latest_kst}"}};</script>'
        "</head><body>"
        f'<div class="v num" id="pubKst">{generated_kst}</div>'
        "</body></html>"
    )


def _write_dashboard(tmp: Path, mode: str, generated_kst: str) -> tuple[str, str]:
    """임시 대시보드 산출물을 쓰고 (dashboard_path, 존재하지 않는 report_path)를 돌려준다.

    report는 일부러 미생성 경로로 둬, 검사가 실제 docs/daily/latest.html을 읽지 않게 격리한다."""
    dash = tmp / "dashboard.html"
    dash.write_text(_artifact_html(mode, generated_kst), encoding="utf-8")
    return str(dash), str(tmp / "noreport.html")


# ---------- A. 워크플로 구조 + hourly cron ----------

def check_workflow_schedule() -> None:
    if not check("A 예약 워크플로 scheduled-live-refresh.yml 존재", WORKFLOW.exists()):
        return
    text = WORKFLOW.read_text(encoding="utf-8")

    try:
        import yaml
        try:
            yaml.safe_load(text)
            check("A 워크플로 YAML 파싱", True)
        except yaml.YAMLError as exc:
            check("A 워크플로 YAML 파싱", False, str(exc).splitlines()[0])
    except ImportError:
        check("A 워크플로 구조(jobs/steps/schedule) 존재",
              "jobs:" in text and "steps:" in text and "schedule:" in text)

    check("A workflow_dispatch 수동 트리거 존재", "workflow_dispatch:" in text)
    check("A schedule(cron) 트리거 존재", "schedule:" in text and "cron:" in text)
    check("A 동시성 직렬화(concurrency) 존재", "concurrency:" in text)
    check("A contents: write 권한 존재 (Pages commit)", "contents: write" in text)

    crons = re.findall(r"cron:\s*[\"']([^\"']+)[\"']", text)
    check("A 정확히 1개 hourly cron", crons == [EXPECTED_HOURLY_CRON], repr(crons))
    parts = crons[0].split() if len(crons) == 1 else []
    check("A 매시간 정각(minute=0, hour=*) 실행",
          len(parts) == 5 and parts[:2] == ["0", "*"], repr(parts))


# ---------- B. 기본은 dry-run (발송 0건) ----------

def check_default_dry_run() -> None:
    with tempfile.TemporaryDirectory() as td:
        dash, rep = _write_dashboard(Path(td), "live", FRESH_KST)
        # TELEGRAM_AUTO_SEND 미설정 + 자격증명 없음 → 후보만 출력, 발송 0건, exit 0.
        proc = run_scheduled(["--report", rep, "--dashboard", dash, "--now", NOW_ISO])
    out = _combined(proc)
    check("B 기본(opt-in 없음) → exit 0 · 실제 발송 0건",
          proc.returncode == 0 and not _claims_sent(out), f"rc={proc.returncode}")
    check("B 기본 → dry-run 상태 표기", "dry-run" in out)
    check("B 기본 → 후보 다이제스트 본문 출력 (알림 후보 보존)", HEADER_TEXT in out)
    check("B 기본 → generated_kst/latest_article_kst 메타 노출",
          "generated_kst:" in out and "latest_article_kst:" in out)


# ---------- C. 발송 opt-in 게이트 (소스 + 워크플로) ----------

def check_optin_gate() -> None:
    src = SCHED_SENDER.read_text(encoding="utf-8")
    check("C 소스에 TELEGRAM_AUTO_SEND opt-in 게이트 존재",
          "TELEGRAM_AUTO_SEND" in src and "AUTO_SEND_ENV" in src)
    check("C 소스가 auto_send 변수로 발송 여부 결정",
          "auto_send = " in src and "if not auto_send" in src)

    text = WORKFLOW.read_text(encoding="utf-8")
    check("C 워크플로가 TELEGRAM_AUTO_SEND를 vars에서 주입 (하드코딩 아님)",
          "vars.TELEGRAM_AUTO_SEND" in text)
    hardcoded = re.search(r"TELEGRAM_AUTO_SEND\s*:\s*[\"']?1[\"']?\s*$",
                          text, re.MULTILINE)
    check("C 워크플로에 TELEGRAM_AUTO_SEND=1 하드코딩 없음", hardcoded is None)
    check("C 발송 step이 live_ok에 게이트됨 (mock fallback 자동발송 차단)",
          "steps.build.outputs.live_ok == 'true'" in text)
    check("C 워크플로에 무조건 send(TELEGRAM_SEND_MODE: send) 하드코딩 없음",
          re.search(r"TELEGRAM_SEND_MODE\s*:\s*send", text) is None)


# ---------- D. 토큰/시크릿 모양 문자열 0건 ----------

def check_no_secrets() -> None:
    for path in (SCHED_SENDER, WORKFLOW, SELF):
        text = path.read_text(encoding="utf-8")
        check(f"D {path.name}에 token 모양 문자열 없음",
              not TOKEN_SHAPE.search(text))


# ---------- E. freshness(stale) 게이트 ----------

def check_freshness_gate() -> None:
    src = SCHED_SENDER.read_text(encoding="utf-8")
    check("E 소스에 freshness/stale 로직 존재 (max_age·age_hours)",
          "max_age" in src and "age_hours" in src and "stale" in src.lower())

    with tempfile.TemporaryDirectory() as td:
        dash, rep = _write_dashboard(Path(td), "live", STALE_KST)
        # opt-in + 산출물은 live지만 72h 지남(stale) + 자격증명 없음 → 거부, 발송 0건.
        proc = run_scheduled(["--report", rep, "--dashboard", dash, "--now", NOW_ISO],
                             TELEGRAM_AUTO_SEND="1")
    out = _combined(proc)
    check("E stale 산출물 + opt-in → 발송 거부 (비-0 종료)",
          proc.returncode != 0, f"rc={proc.returncode}")
    check("E stale 거부 → 'stale' 사유 표기 · 발송 위장 없음",
          "stale" in out and not _claims_sent(out))


# ---------- F. mock/live 정직성 게이트 ----------

def check_mock_honesty_gate() -> None:
    src = SCHED_SENDER.read_text(encoding="utf-8")
    check("F 소스에 news_data_mode != live 게이트 존재",
          'news_data_mode' in src and 'live' in src and "ALLOW_MOCK" in src)

    with tempfile.TemporaryDirectory() as td:
        dash, rep = _write_dashboard(Path(td), "mock", FRESH_KST)
        # opt-in + fresh지만 mock + ALLOW_MOCK 없음 + 자격증명 없음 → 거부, 발송 0건.
        proc = run_scheduled(["--report", rep, "--dashboard", dash, "--now", NOW_ISO],
                             TELEGRAM_AUTO_SEND="1")
    out = _combined(proc)
    check("F mock 산출물 + opt-in → 발송 거부 (비-0 종료)",
          proc.returncode != 0, f"rc={proc.returncode}")
    check("F mock 거부 → live 정직성 사유 표기 · 발송 위장 없음",
          "live" in out and not _claims_sent(out))
    check("F mock 거부 사유에 ALLOW_MOCK opt-out 안내 노출 (강제 발송 경로 명시)",
          "TELEGRAM_ALLOW_MOCK" in out or "ALLOW_MOCK" in out)


# ---------- G. 공개 워크플로에 비공개 감시 목록 경로 없음 ----------

def check_no_private_watchlist() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    check("G 공개 예약 워크플로에 SITE_WATCHLIST_PATH 없음 (사생활)",
          "SITE_WATCHLIST_PATH" not in text)
    check("G 공개 예약 워크플로에 data/private 경로 없음",
          "data/private" not in text)


# ---------- H. 자격증명 없으면 가짜 발송 없음 (live·fresh·opt-in이라도) ----------

def check_no_fake_send_without_creds() -> None:
    with tempfile.TemporaryDirectory() as td:
        dash, rep = _write_dashboard(Path(td), "live", FRESH_KST)
        # 모든 정직성 게이트 통과(live·fresh·opt-in)하지만 자격증명 없음 → send_telegram이 fail-fast.
        proc = run_scheduled(["--report", rep, "--dashboard", dash, "--now", NOW_ISO],
                             TELEGRAM_AUTO_SEND="1")
    out = _combined(proc)
    check("H live·fresh·opt-in + 자격증명 없음 → 가짜 발송 0건 (위임 sender fail-fast)",
          not _claims_sent(out))
    check("H 자격증명 없음 → 명확한 missing 사유로 실패 (조용한 가짜 발송 아님)",
          proc.returncode != 0 and "TELEGRAM_BOT_TOKEN is missing" in out,
          f"rc={proc.returncode}")


# ---------- I. 실제 발송은 send_telegram.py에 위임 (POST 재구현 금지) ----------

def check_delegates_to_single_source() -> None:
    src = SCHED_SENDER.read_text(encoding="utf-8")
    check("I 실제 발송을 send_telegram.py에 위임",
          "send_telegram.py" in src and "_delegate_to_sender" in src)
    check("I POST/토큰 처리를 재구현하지 않음 (urlopen·api 호스트 없음)",
          "urlopen" not in src and "api.telegram.org" not in src)
    # 게이트가 위임보다 먼저 — stale/mock 거부 return이 _delegate 호출보다 앞선다.
    refuse_idx = src.find("AUTO-SEND 거부")
    delegate_idx = src.find("return _delegate_to_sender()")
    check("I 정직성 게이트(거부)가 발송 위임보다 먼저 (정적 순서)",
          0 <= refuse_idx < delegate_idx, f"refuse={refuse_idx}, delegate={delegate_idx}")


def main() -> int:
    print(f"== verify_scheduled_refresh_and_telegram @ {ROOT} ==")
    check_workflow_schedule()
    check_default_dry_run()
    check_optin_gate()
    check_no_secrets()
    check_freshness_gate()
    check_mock_honesty_gate()
    check_no_private_watchlist()
    check_no_fake_send_without_creds()
    check_delegates_to_single_source()

    if _failures:
        print(f"\nRESULT: FAIL ({len(_failures)} failed)")
        for name in _failures:
            print(f"  - {name}")
        return 1
    print("\nRESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
