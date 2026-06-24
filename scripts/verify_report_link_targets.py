#!/usr/bin/env python3
"""verify_report_link_targets — D6-I 버튼 라벨→대상 매핑 회귀 가드.

QA에서 '전체 리포트 보기'가 요약 대시보드를, '요약 대시보드 보기'가 전체 리포트를
여는 swap이 보고됐다. 근본 원인은 REPORT_URL / DASHBOARD_URL 환경변수가 서로 뒤바뀌어
설정돼도 send_telegram.py가 이를 그대로 신뢰해 버튼을 붙인 것. 이제 send_telegram은
파일명으로 종류를 식별해 매핑을 강제한다(_normalize_report_targets). 이 검수기는 그
계약이 깨지지 않게 막는다.

검사(완전 오프라인 · 네트워크/비밀값/발송 0건):
  1. '요약 대시보드 보기'는 요약 대시보드 URL(dashboard 포함)을 가리킨다.
  2. 그 URL은 전체 리포트(.../latest.html, dashboard 아님)를 가리키지 않는다.
  3. '전체 리포트 보기'는 .../latest.html(전체 리포트)을 가리킨다.
  4. 그 URL은 dashboard-latest.html(요약 대시보드)을 가리키지 않는다.
  5. REPORT_URL/DASHBOARD_URL이 뒤바뀌어 설정돼도 1~4가 그대로 유지된다(swap 복원).
  6. Telegram dry-run payload가 같은 매핑을 쓴다(버튼 text→url).
  7. 어떤 경로에서도 실제 Telegram 발송이 일어나지 않는다(dry-run·토큰 미사용).
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SENDER = ROOT / "scripts" / "send_telegram.py"

SUMMARY_LABEL = "요약 대시보드 보기"
FULL_LABEL = "전체 리포트 보기"

REPORT_URL = "https://example.github.io/repo/daily/latest.html"
DASHBOARD_URL = "https://example.github.io/repo/daily/dashboard-latest.html"

# Telegram bot token 형태 — 출력에 토큰이 새지 않았는지 확인용.
TOKEN_SHAPE = re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b")

_failures: list[str] = []
_passes = 0


def check(label: str, ok: bool, detail: str = "") -> bool:
    global _passes
    if ok:
        _passes += 1
        print(f"  PASS  {label}")
    else:
        _failures.append(label)
        print(f"  FAIL  {label}" + (f" — {detail}" if detail else ""))
    return ok


def _clean_env(**overrides) -> dict:
    """비밀값/토큰이 전혀 없는 깨끗한 env. dry-run 경로만 쓰므로 토큰 불필요."""
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONIOENCODING": "utf-8",
        "SystemRoot": os.environ.get("SystemRoot", ""),
    }
    env.update({k: v for k, v in overrides.items() if v is not None})
    return env


def _dry_run_buttons(report_url: str = None, dashboard_url: str = None) -> tuple[dict, str]:
    """send_telegram.py --dry-run-payload를 깨끗한 env로 돌려 버튼 text→url 매핑을 얻는다."""
    proc = subprocess.run(
        [sys.executable, str(SENDER), "--dry-run-payload", "verify"],
        capture_output=True, text=True, timeout=60,
        env=_clean_env(REPORT_URL=report_url, DASHBOARD_URL=dashboard_url))
    out = (proc.stdout or "") + (proc.stderr or "")
    mapping = {}
    for line in (proc.stdout or "").splitlines():
        m = re.match(r"button:\s*(.+?)\s*->\s*(\S+)$", line.strip())
        if m:
            mapping[m.group(1)] = m.group(2)
    return mapping, out


def check_in_process_mapping() -> None:
    """build_payload를 직접 호출해 라벨→url 매핑을 단언한다."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import send_telegram

    payload = send_telegram.build_payload(
        "DRY", "msg", REPORT_URL, "", DASHBOARD_URL)
    buttons = json.loads(payload["reply_markup"])["inline_keyboard"][0]
    by_label = {b["text"]: b["url"] for b in buttons}

    summary_url = by_label.get(SUMMARY_LABEL, "")
    full_url = by_label.get(FULL_LABEL, "")

    # 1. 요약 대시보드 보기 → dashboard
    check("1. '요약 대시보드 보기' → 요약 대시보드(dashboard) URL",
          "dashboard" in summary_url.lower(), summary_url)
    # 2. 요약 대시보드 보기는 전체 리포트(latest.html, dashboard 아님)를 가리키지 않는다
    check("2. '요약 대시보드 보기'가 전체 리포트(latest.html)를 가리키지 않음",
          not (summary_url.lower().endswith("/latest.html")), summary_url)
    # 3. 전체 리포트 보기 → latest.html
    check("3. '전체 리포트 보기' → 전체 리포트(latest.html)",
          full_url.lower().endswith("/latest.html"), full_url)
    # 4. 전체 리포트 보기는 dashboard-latest.html을 가리키지 않는다
    check("4. '전체 리포트 보기'가 요약 대시보드(dashboard-latest.html)를 가리키지 않음",
          "dashboard-latest.html" not in full_url.lower(), full_url)


def check_swap_resilience() -> None:
    """REPORT_URL/DASHBOARD_URL을 일부러 뒤바꿔 설정해도 매핑이 복원되는지."""
    # 정상 설정
    ok_map, _ = _dry_run_buttons(REPORT_URL, DASHBOARD_URL)
    check("5a. 정상 설정 시 '요약 대시보드 보기' → dashboard",
          "dashboard" in ok_map.get(SUMMARY_LABEL, "").lower(), ok_map.get(SUMMARY_LABEL, ""))
    check("5b. 정상 설정 시 '전체 리포트 보기' → latest.html",
          ok_map.get(FULL_LABEL, "").lower().endswith("/latest.html"), ok_map.get(FULL_LABEL, ""))

    # 뒤바뀐 설정(REPORT_URL에 대시보드, DASHBOARD_URL에 전체 리포트)
    swap_map, _ = _dry_run_buttons(DASHBOARD_URL, REPORT_URL)
    check("5c. 뒤바뀐 설정에도 '요약 대시보드 보기' → dashboard (swap 복원)",
          "dashboard" in swap_map.get(SUMMARY_LABEL, "").lower()
          and not swap_map.get(SUMMARY_LABEL, "").lower().endswith("/latest.html"),
          swap_map.get(SUMMARY_LABEL, ""))
    check("5d. 뒤바뀐 설정에도 '전체 리포트 보기' → latest.html (swap 복원)",
          swap_map.get(FULL_LABEL, "").lower().endswith("/latest.html")
          and "dashboard-latest.html" not in swap_map.get(FULL_LABEL, "").lower(),
          swap_map.get(FULL_LABEL, ""))

    # DASHBOARD_URL 미설정 → REPORT_URL(latest.html)에서 dashboard-latest.html 파생
    derive_map, _ = _dry_run_buttons(REPORT_URL, None)
    check("5e. DASHBOARD_URL 미설정 시 파생된 요약 대시보드 → dashboard",
          "dashboard" in derive_map.get(SUMMARY_LABEL, "").lower(),
          derive_map.get(SUMMARY_LABEL, ""))


def check_telegram_payload_mapping() -> None:
    """Telegram dry-run payload가 동일 매핑(요약→대시보드, 전체→리포트)을 쓰는지."""
    mapping, out = _dry_run_buttons(REPORT_URL, DASHBOARD_URL)
    check("6a. dry-run payload에 두 라벨 모두 존재",
          SUMMARY_LABEL in mapping and FULL_LABEL in mapping, " / ".join(mapping))
    check("6b. dry-run: 요약 대시보드 보기 → dashboard",
          "dashboard" in mapping.get(SUMMARY_LABEL, "").lower(), mapping.get(SUMMARY_LABEL, ""))
    check("6c. dry-run: 전체 리포트 보기 → latest.html",
          mapping.get(FULL_LABEL, "").lower().endswith("/latest.html"), mapping.get(FULL_LABEL, ""))


def check_no_send() -> None:
    """발송 0건 — dry-run 경로는 토큰을 읽지 않고 어떤 POST도 하지 않는다."""
    proc = subprocess.run(
        [sys.executable, str(SENDER), "--dry-run-payload", "verify"],
        capture_output=True, text=True, timeout=60,
        env=_clean_env(REPORT_URL=REPORT_URL, DASHBOARD_URL=DASHBOARD_URL))
    out = (proc.stdout or "") + (proc.stderr or "")
    check("7a. dry-run-payload exits 0 (발송 결정/토큰 없이)", proc.returncode == 0, out[-200:])
    check("7b. dry-run 출력에 토큰 형태 없음(발송/유출 0건)", not TOKEN_SHAPE.search(out))
    check("7c. dry-run이 sendMessage POST를 하지 않음(코드 경로 분리)",
          "Send status:" not in out and "delivery summary" not in out)
    # 소스 단언: 정규화 헬퍼가 build_payload 단일 chokepoint를 통과한다.
    src = SENDER.read_text(encoding="utf-8")
    check("7d. build_payload가 _normalize_report_targets로 매핑을 강제",
          "_normalize_report_targets(report_url, dashboard_url)" in src
          and "def build_payload" in src)


def main() -> int:
    print("== verify_report_link_targets (D6-I 버튼 라벨→대상 매핑) ==")
    check_in_process_mapping()
    check_swap_resilience()
    check_telegram_payload_mapping()
    check_no_send()
    print(f"\n{_passes} passed, {len(_failures)} failed")
    if _failures:
        print("FAILED:")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
