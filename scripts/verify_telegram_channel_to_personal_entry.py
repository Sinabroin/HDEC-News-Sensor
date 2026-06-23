"""P0-C1.10 검증기 — Telegram 채널→1:1 봇 진입(개인 질의하기) 버튼 회귀 검사.

채널 일일 브리프 메시지에 "요약 대시보드 보기", "전체 리포트 보기",
"개인 질의하기"(1:1 봇 deep link) 버튼이 정직하고 안전하게 붙는지 결정적으로
검사한다 (네트워크 0건, 비밀값 0건):

- send_telegram.py가 1:1 봇 deep link 버튼을 지원한다 (개인 질의하기 / start=ask_today).
- TELEGRAM_BOT_USERNAME 설정 시 https://t.me/<bot>?start=ask_today 버튼이 붙는다.
- TELEGRAM_PERSONAL_BOT_URL(완성 t.me 링크)도 지원한다.
- 설정이 없으면 개인 버튼은 안전하게 생략되고 발송 경로가 실패하지 않는다.
- deep link start 파라미터는 ASCII-safe('ask_today')이고, 출력 어디에도 토큰이 없다.
- 워크플로 send step에 TELEGRAM_BOT_USERNAME가 vars/secrets로 주입된다.
- README에 채널+1:1 롤아웃 가이드와 'P1 webhook/polling 후 활성화' 정직성 문구가 있다.

사용법:
    python3 scripts/verify_telegram_channel_to_personal_entry.py
"""

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SENDER = ROOT / "scripts" / "send_telegram.py"
WORKFLOW = ROOT / ".github" / "workflows" / "telegram-notify.yml"
README = ROOT / "README.md"

SUMMARY_BUTTON_TEXT = "요약 대시보드 보기"
REPORT_BUTTON_TEXT = "전체 리포트 보기"
PERSONAL_BUTTON_TEXT = "개인 질의하기"
START_PARAM = "ask_today"
SAMPLE_REPORT_URL = "https://example.com/daily/latest.html"
SAMPLE_DASHBOARD_URL = "https://example.com/daily/dashboard-latest.html"
SAMPLE_USERNAME = "hdec_executive_rader_bot"

TOKEN_SHAPE = re.compile(r"[0-9]{8,}:[A-Za-z0-9_-]{20,}")
# 토큰을 출력하지 않는지 확인용 — 조각으로 조립해 코드 트리 token-shape grep을 피한다.
FAKE_TOKEN = "9" * 10 + ":" + "Z" * 26

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


def _payload_env(**extra: str) -> dict:
    """--dry-run-payload 실행용 환경 — 외부 TELEGRAM_*/REPORT_URL 오염을 제거한다."""
    env = {**os.environ, "APP_MODE": "mock"}
    for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_IDS", "REPORT_URL",
                "DASHBOARD_URL",
                "TELEGRAM_BOT_USERNAME", "TELEGRAM_PERSONAL_BOT_URL", "MESSAGE"):
        env.pop(key, None)
    env.update(extra)
    return env


def run_payload(**env_extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SENDER), "--dry-run-payload", "test"],
        capture_output=True, text=True, env=_payload_env(**env_extra),
        cwd=ROOT, timeout=120)


# ---------- 정적 소스 검사 ----------

def check_sender_source() -> None:
    src = SENDER.read_text(encoding="utf-8")
    check("sender에 개인 질의하기 버튼 라벨 존재", PERSONAL_BUTTON_TEXT in src)
    check("sender에 요약 대시보드 버튼 라벨 존재", SUMMARY_BUTTON_TEXT in src)
    check("sender에 전체 리포트 버튼 라벨 존재", REPORT_BUTTON_TEXT in src)
    check("sender에 deep link start 파라미터(ask_today) 존재", START_PARAM in src)
    check("sender에 resolve_personal_bot_url 함수 존재",
          "def resolve_personal_bot_url" in src)
    check("sender가 TELEGRAM_BOT_USERNAME/PERSONAL_BOT_URL env를 읽음",
          "TELEGRAM_BOT_USERNAME" in src and "TELEGRAM_PERSONAL_BOT_URL" in src)
    check("sender에 --dry-run-payload 모드 존재", "--dry-run-payload" in src)
    check("sender에 token 모양 하드코딩 없음", not TOKEN_SHAPE.search(src))
    # deep link URL 리터럴은 t.me prefix만 (외부 임의 호스트 하드코딩 금지)
    foreign = [u for u in re.findall(r"https?://[^\s\"'}]+", src)
               if not u.startswith(("https://api.telegram.org", "https://t.me/"))]
    check("sender URL 리터럴은 Telegram API/t.me 호스트뿐", not foreign,
          "; ".join(foreign))


# ---------- 런타임 payload 검사 (발송/비밀값 없음) ----------

def check_username_configured() -> None:
    proc = run_payload(TELEGRAM_BOT_USERNAME=SAMPLE_USERNAME,
                       REPORT_URL=SAMPLE_REPORT_URL,
                       DASHBOARD_URL=SAMPLE_DASHBOARD_URL,
                       TELEGRAM_BOT_TOKEN=FAKE_TOKEN)
    out = (proc.stdout or "")
    check("username 설정 → dry-run-payload 동작 (exit 0)", proc.returncode == 0,
          (proc.stderr or "").strip()[-160:])
    check("username 설정 → 요약 대시보드 버튼 노출", SUMMARY_BUTTON_TEXT in out)
    check("username 설정 → 전체 리포트 버튼 노출", REPORT_BUTTON_TEXT in out)
    check("username 설정 → 개인 질의하기 버튼 노출", PERSONAL_BUTTON_TEXT in out)
    expected = f"https://t.me/{SAMPLE_USERNAME}?start={START_PARAM}"
    check("개인 버튼 URL이 t.me deep link(start=ask_today)", expected in out, out[-160:])
    check("username 설정 → 개인 봇 링크 enabled true",
          "Personal bot link enabled: true" in out)
    check("출력에 토큰 노출 없음 (dry-run-payload는 토큰 미사용)",
          FAKE_TOKEN not in out and not TOKEN_SHAPE.search(out))


def check_personal_url_configured() -> None:
    url = f"https://t.me/some_other_bot?start={START_PARAM}"
    proc = run_payload(TELEGRAM_PERSONAL_BOT_URL=url, REPORT_URL=SAMPLE_REPORT_URL,
                       DASHBOARD_URL=SAMPLE_DASHBOARD_URL)
    out = proc.stdout or ""
    check("PERSONAL_BOT_URL 설정 → 그대로 사용", url in out, out[-160:])
    check("PERSONAL_BOT_URL 설정 → 개인 질의하기 버튼 노출",
          PERSONAL_BUTTON_TEXT in out)


def check_not_configured_omits_button() -> None:
    proc = run_payload(REPORT_URL=SAMPLE_REPORT_URL)  # username/personal 미설정
    out = proc.stdout or ""
    check("미설정 → dry-run-payload 동작 (exit 0, 실패 안 함)",
          proc.returncode == 0, (proc.stderr or "").strip()[-160:])
    check("미설정 → 개인 봇 링크 enabled false",
          "Personal bot link enabled: false" in out)
    check("미설정 → 개인 질의하기 버튼 생략 (안전)", PERSONAL_BUTTON_TEXT not in out)
    check("미설정 → 요약 대시보드/전체 리포트 버튼은 유지",
          SUMMARY_BUTTON_TEXT in out and REPORT_BUTTON_TEXT in out)


def check_bad_username_disabled() -> None:
    proc = run_payload(TELEGRAM_BOT_USERNAME="bad name!",
                       REPORT_URL=SAMPLE_REPORT_URL,
                       DASHBOARD_URL=SAMPLE_DASHBOARD_URL)
    out = proc.stdout or ""
    check("잘못된 username → dry-run-payload 동작 (exit 0)", proc.returncode == 0)
    check("잘못된 username → 개인 봇 링크 enabled false",
          "Personal bot link enabled: false" in out)
    check("잘못된 username → 개인 버튼 생략 (비ASCII/비밀값 유입 차단)",
          PERSONAL_BUTTON_TEXT not in out)


def check_nothing_configured_no_buttons() -> None:
    proc = run_payload()  # 아무 것도 설정 안 함
    out = proc.stdout or ""
    check("전부 미설정 → exit 0", proc.returncode == 0)
    check("전부 미설정 → 버튼 없음 표기", "button: (none)" in out)


# ---------- 워크플로 / README ----------

def check_workflow() -> None:
    if not check("telegram-notify.yml 존재", WORKFLOW.exists()):
        return
    text = WORKFLOW.read_text(encoding="utf-8")
    check("workflow send step에 TELEGRAM_BOT_USERNAME 주입",
          "TELEGRAM_BOT_USERNAME" in text)
    check("workflow가 username을 vars/secrets로 주입 (하드코딩 아님)",
          bool(re.search(r"TELEGRAM_BOT_USERNAME:\s*\$\{\{[^}]*"
                         r"(vars|secrets)\.TELEGRAM_BOT_USERNAME", text)))
    check("workflow에 token 모양 하드코딩 없음", not TOKEN_SHAPE.search(text))
    check("workflow send step에 DASHBOARD_URL/REPORT_URL 주입",
          "DASHBOARD_URL" in text and "REPORT_URL" in text)


def check_readme() -> None:
    if not check("README.md 존재", README.exists()):
        return
    text = README.read_text(encoding="utf-8")
    check("README에 'Telegram Executive Rollout' 가이드 존재",
          "Telegram Executive Rollout" in text)
    check("README에 개인 질의하기 deep link 설명 존재",
          PERSONAL_BUTTON_TEXT in text and START_PARAM in text)
    check("README에 채널 + 1:1 봇 패턴 설명 존재",
          "채널" in text and "1:1" in text)
    # 정직성: 실제 자연어 질의 응답은 P1 webhook/polling 구현 후 활성화
    check("README에 'P1 webhook/polling 후 활성화' 정직성 문구 존재",
          "P1" in text and "webhook" in text.lower()
          and ("자연어" in text or "질의 응답" in text))


def main() -> int:
    print(f"== verify_telegram_channel_to_personal_entry @ {ROOT} ==")
    check_sender_source()
    check_username_configured()
    check_personal_url_configured()
    check_not_configured_omits_button()
    check_bad_username_disabled()
    check_nothing_configured_no_buttons()
    check_workflow()
    check_readme()

    if _failures:
        print(f"\nRESULT: FAIL ({len(_failures)} failed)")
        for name in _failures:
            print(f"  - {name}")
        return 1
    print("\nRESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
