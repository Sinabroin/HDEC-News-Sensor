"""P0-D3I 검증기 — 사람 검토 / 수동 Send 게이트 (human-on-the-loop).

알림 후보 생성과 실제 임원 발송 사이의 경계를 검사한다. 핵심 계약:
  1. 기본은 절대 자동 발송하지 않는다 (manual = 비발송).
  2. 실제 발송은 TELEGRAM_SEND_MODE=send + 운영자 승인(REVIEW_APPROVED/CONFIRM_SEND)
     + 자격증명이 모두 있을 때만 일어난다.
  3. 자격증명이 없으면 어떤 모드에서도 '발송됨'으로 위장하지 않고 fail-fast한다.
  4. 후보 다이제스트(알림 후보 추출)는 발송 여부와 무관하게 계속 생성된다.
  5. 워크플로는 schedule(예약) 트리거에서 자동 발송하지 않는다 — 수동 승인 입력으로만 send.

전부 네트워크 호출 0건·실제 발송 0건으로 실행된다. send 모드·승인·자격증명을 동시에
주지 않으므로 어떤 검사도 실제 Telegram POST 경로에 도달하지 않는다 (오프라인 안전).

사용법:
    python3 scripts/verify_human_review_gate.py
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SENDER = ROOT / "scripts" / "send_telegram.py"
DIGEST_BUILDER = ROOT / "scripts" / "build_telegram_digest.py"
TELEGRAM_VERIFIER = ROOT / "scripts" / "verify_telegram_digest.py"
WORKFLOW = ROOT / ".github" / "workflows" / "telegram-notify.yml"

HEADER_TEXT = "HDEC Executive Radar"
# 발송 의도가 없는 테스트 자격증명 (비밀값 아님 · token 모양 아님). truthy 여부만 검사된다.
FIXTURE_TOKEN = "review-gate-probe-not-a-real-token"
FIXTURE_CHAT_IDS = "111,222"
# 실제 발송으로 새는 흔적 — 어떤 비발송 경로에도 등장해선 안 된다.
SENT_MARKERS = ("delivered=", "Send status: approved", "실제 발송 진행")

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
    """발송 게이트 검사용 환경 — 외부 TELEGRAM_*/승인/메시지 오염을 제거하고 mock 고정."""
    env = {**os.environ, "APP_MODE": "mock"}
    for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_IDS", "TELEGRAM_SEND_MODE",
                "REVIEW_APPROVED", "CONFIRM_SEND", "REPORT_URL",
                "TELEGRAM_BOT_USERNAME", "TELEGRAM_PERSONAL_BOT_URL", "MESSAGE"):
        env.pop(key, None)
    env.update(extra)
    return env


def run_sender(**env_extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SENDER)],
        capture_output=True, text=True, env=_clean_env(**env_extra),
        cwd=ROOT, timeout=180)


def _combined(proc: subprocess.CompletedProcess) -> str:
    return (proc.stdout or "") + (proc.stderr or "")


def _claims_sent(text: str) -> bool:
    return any(marker in text for marker in SENT_MARKERS)


# ---------- A. 기본은 안전 (자동 발송 없음) ----------

def check_default_safe() -> None:
    # A1) 자격증명 없음 + 모드 없음 → fail-fast(exit 1), '발송됨' 주장 없음.
    proc = run_sender(MESSAGE="review gate A1")
    combined = _combined(proc)
    check("A1 자격증명 없음 → fail-fast exit 1 (조용한 가짜 발송 차단)",
          proc.returncode == 1 and "TELEGRAM_BOT_TOKEN is missing" in combined,
          f"rc={proc.returncode}")
    check("A1 자격증명 없음 → 발송됨으로 위장하지 않음", not _claims_sent(combined))

    # A2) 자격증명 있음 + 모드/승인 없음(기본 manual) → exit 0, 비발송 + 검토 문구.
    # MESSAGE를 주지 않아 mock-digest 경로로 후보 다이제스트가 조립되는지까지 확인한다.
    proc = run_sender(TELEGRAM_BOT_TOKEN=FIXTURE_TOKEN,
                      TELEGRAM_CHAT_IDS=FIXTURE_CHAT_IDS)
    out = _combined(proc)
    check("A2 기본 모드 → 자동 발송 안 함 (exit 0, 실제 발송 0건)",
          proc.returncode == 0 and not _claims_sent(out), f"rc={proc.returncode}")
    check("A2 기본 모드 → send_mode=manual 표기", "send_mode=manual" in out)
    check("A2 기본 모드 → send_allowed=false + review_required",
          "send_allowed=false" in out and "review_required" in out)
    check("A2 기본 모드 → 사람 검토 문구 노출", "발송하지 않음" in out)
    # C2(후보 보존): 비발송이어도 후보 다이제스트 본문을 출력한다.
    check("A2 기본 모드 → 후보 다이제스트 본문 출력 (알림 후보 보존)",
          HEADER_TEXT in out)


# ---------- B. 실제 발송은 명시 승인 필요 ----------

def check_send_requires_approval() -> None:
    # B1) send 모드 + 자격증명 있음 + 승인 없음 → 발송 차단(비-0 종료), 실제 발송 없음.
    proc = run_sender(TELEGRAM_SEND_MODE="send",
                      TELEGRAM_BOT_TOKEN=FIXTURE_TOKEN,
                      TELEGRAM_CHAT_IDS=FIXTURE_CHAT_IDS,
                      MESSAGE="review gate B1")
    out = _combined(proc)
    check("B1 send 모드 + 승인 없음 → 발송 차단 (비-0 종료)",
          proc.returncode != 0, f"rc={proc.returncode}")
    check("B1 send 모드 + 승인 없음 → approval_required 표기, 발송 안 함",
          "approval_required" in out and not _claims_sent(out))

    # B2) send 모드 + 승인 있음 + 자격증명 없음 → fail-fast, '발송됨' 아님.
    proc = run_sender(TELEGRAM_SEND_MODE="send", REVIEW_APPROVED="true",
                      MESSAGE="review gate B2")
    out = _combined(proc)
    check("B2 승인됐어도 자격증명 없으면 발송 아님 (exit 1, missing)",
          proc.returncode == 1 and "TELEGRAM_BOT_TOKEN is missing" in out,
          f"rc={proc.returncode}")
    check("B2 자격증명 없음 → 발송됨으로 위장하지 않음", not _claims_sent(out))


# ---------- C. 알림 후보(다이제스트) 생성은 계속된다 ----------

def check_candidate_preserved() -> None:
    proc = subprocess.run([sys.executable, str(DIGEST_BUILDER), "--json"],
                          capture_output=True, text=True,
                          env=_clean_env(), cwd=ROOT, timeout=180)
    if not check("C1 다이제스트 빌더 --json 동작 (exit 0)", proc.returncode == 0,
                 (proc.stderr or "").strip()[-200:]):
        return
    try:
        data = json.loads(proc.stdout)
    except ValueError as exc:
        check("C1 다이제스트 JSON 파싱", False, str(exc))
        return
    signals = data.get("top_signals") or []
    check("C1 알림 후보(top_signals) 1건 이상 생성", len(signals) >= 1, f"{len(signals)}건")
    check("C1 다이제스트에 생성시각(generated_at) 존재",
          bool(data.get("generated_at")))
    check("C1 다이제스트 mode == mock (안전 기본)", data.get("mode") == "mock")


# ---------- D. 워크플로는 예약 자동 발송을 하지 않는다 ----------

def check_workflow_no_autosend() -> None:
    if not check("D 워크플로 telegram-notify.yml 존재", WORKFLOW.exists()):
        return
    text = WORKFLOW.read_text(encoding="utf-8")

    try:
        import yaml
        try:
            yaml.safe_load(text)
            check("D 워크플로 YAML 파싱", True)
        except yaml.YAMLError as exc:
            check("D 워크플로 YAML 파싱", False, str(exc).splitlines()[0])
    except ImportError:
        check("D 워크플로 구조(jobs/steps) 존재",
              "jobs:" in text and "steps:" in text)

    check("D 수동 승인 입력 approve_send 존재", "approve_send" in text)

    send_mode_lines = [ln.strip() for ln in text.splitlines()
                       if re.search(r"TELEGRAM_SEND_MODE\s*:", ln)]
    check("D 발송 step에 TELEGRAM_SEND_MODE 게이트 존재", bool(send_mode_lines),
          f"{len(send_mode_lines)} lines")
    ungated = [ln for ln in send_mode_lines if "approve_send" not in ln]
    check("D 모든 SEND_MODE가 approve_send 승인에 게이트됨 (예약 자동발송 차단)",
          bool(send_mode_lines) and not ungated, "; ".join(ungated[:2]))
    literal_send = [ln for ln in send_mode_lines
                    if re.search(r"TELEGRAM_SEND_MODE\s*:\s*send\b", ln)]
    check("D 무조건 send(하드코딩) 없음", not literal_send, "; ".join(literal_send[:2]))

    # 예약(cron)은 유지하되 — 입력이 없으면 게이트가 닫혀 발송하지 않는다.
    check("D schedule(cron) 트리거 유지", "schedule:" in text and "cron:" in text)
    check("D 여전히 send_telegram.py로 발송 경로 사용", "send_telegram.py" in text)
    check("D live_ok 게이트 유지 (가짜 live 발송 차단)",
          "steps.report.outputs.live_ok == 'true'" in text)


# ---------- E. 기존 Telegram 검증기 회귀 없음 ----------

def check_existing_telegram_verifier() -> None:
    proc = subprocess.run([sys.executable, str(TELEGRAM_VERIFIER)],
                          capture_output=True, text=True, cwd=ROOT, timeout=900)
    check("E 기존 verify_telegram_digest.py 통과 (exit 0)",
          proc.returncode == 0, (proc.stdout or "").strip()[-300:])


# ---------- F. 소스 계약 (게이트가 POST보다 먼저, 가짜 발송 금지) ----------

def check_sender_source_contract() -> None:
    src = SENDER.read_text(encoding="utf-8")
    for token in ("TELEGRAM_SEND_MODE", "REVIEW_APPROVED", "CONFIRM_SEND",
                  "def resolve_send_mode", "def review_approved", "review_required"):
        check(f"F sender 소스에 '{token}' 존재", token in src)

    # 발송(POST)은 사람 검토 게이트 '뒤'에서만 일어난다 — 정적 순서 검사.
    guard_idx = src.find("if not will_send")
    post_idx = src.find("urlopen")
    approved_idx = src.find("Send status: approved")
    check("F 사람 검토 게이트(if not will_send)가 POST(urlopen)보다 먼저",
          0 <= guard_idx < post_idx, f"guard={guard_idx}, post={post_idx}")
    check("F 실제 발송은 승인 확인 이후에만 (approved 표기 → POST 순서)",
          0 <= approved_idx < post_idx, f"approved={approved_idx}, post={post_idx}")
    # POST 경로가 will_send 게이트 변수에 묶여 있는지.
    check("F will_send 게이트 변수로 발송 여부 결정",
          "will_send = " in src and "send_mode == SEND_MODE_SEND" in src)


def main() -> int:
    print(f"== verify_human_review_gate @ {ROOT} ==")
    check_default_safe()
    check_send_requires_approval()
    check_candidate_preserved()
    check_workflow_no_autosend()
    check_sender_source_contract()
    check_existing_telegram_verifier()

    if _failures:
        print(f"\nRESULT: FAIL ({len(_failures)} failed)")
        for name in _failures:
            print(f"  - {name}")
        return 1
    print("\nRESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
