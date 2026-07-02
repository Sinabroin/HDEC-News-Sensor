#!/usr/bin/env python3
"""verify_report_link_targets — D6-I 버튼 라벨→대상 매핑 회귀 가드.

QA에서 '상세 리포트 보기'가 요약 대시보드를, '대시보드 보기'가 전체 리포트를
여는 swap이 보고됐다. 근본 원인은 REPORT_URL / DASHBOARD_URL 환경변수가 서로 뒤바뀌어
설정돼도 send_telegram.py가 이를 그대로 신뢰해 버튼을 붙인 것. 이제 send_telegram은
파일명으로 종류를 식별해 매핑을 강제한다(_normalize_report_targets). 이 검수기는 그
계약이 깨지지 않게 막는다.

검사(완전 오프라인 · 네트워크/비밀값/발송 0건):
  1. '대시보드 보기'는 요약 대시보드 URL(dashboard 포함)을 가리킨다.
  2. 그 URL은 전체 리포트(.../latest.html, dashboard 아님)를 가리키지 않는다.
  3. '상세 리포트 보기'는 .../latest.html(전체 리포트)을 가리킨다.
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
DASHBOARD_TEMPLATE = ROOT / "templates" / "dashboard_preview.html"

SUMMARY_LABEL = "대시보드 보기"
FULL_LABEL = "상세 리포트 보기"

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

    # 1. 대시보드 보기 → dashboard
    check("1. '대시보드 보기' → 요약 대시보드(dashboard) URL",
          "dashboard" in summary_url.lower(), summary_url)
    # 2. 대시보드 보기는 전체 리포트(latest.html, dashboard 아님)를 가리키지 않는다
    check("2. '대시보드 보기'가 전체 리포트(latest.html)를 가리키지 않음",
          not (summary_url.lower().endswith("/latest.html")), summary_url)
    # 3. 상세 리포트 보기 → latest.html
    check("3. '상세 리포트 보기' → 전체 리포트(latest.html)",
          full_url.lower().endswith("/latest.html"), full_url)
    # 4. 상세 리포트 보기는 dashboard-latest.html을 가리키지 않는다
    check("4. '상세 리포트 보기'가 요약 대시보드(dashboard-latest.html)를 가리키지 않음",
          "dashboard-latest.html" not in full_url.lower(), full_url)


def check_swap_resilience() -> None:
    """REPORT_URL/DASHBOARD_URL을 일부러 뒤바꿔 설정해도 매핑이 복원되는지."""
    # 정상 설정
    ok_map, _ = _dry_run_buttons(REPORT_URL, DASHBOARD_URL)
    check("5a. 정상 설정 시 '대시보드 보기' → dashboard",
          "dashboard" in ok_map.get(SUMMARY_LABEL, "").lower(), ok_map.get(SUMMARY_LABEL, ""))
    check("5b. 정상 설정 시 '상세 리포트 보기' → latest.html",
          ok_map.get(FULL_LABEL, "").lower().endswith("/latest.html"), ok_map.get(FULL_LABEL, ""))

    # 뒤바뀐 설정(REPORT_URL에 대시보드, DASHBOARD_URL에 전체 리포트)
    swap_map, _ = _dry_run_buttons(DASHBOARD_URL, REPORT_URL)
    check("5c. 뒤바뀐 설정에도 '대시보드 보기' → dashboard (swap 복원)",
          "dashboard" in swap_map.get(SUMMARY_LABEL, "").lower()
          and not swap_map.get(SUMMARY_LABEL, "").lower().endswith("/latest.html"),
          swap_map.get(SUMMARY_LABEL, ""))
    check("5d. 뒤바뀐 설정에도 '상세 리포트 보기' → latest.html (swap 복원)",
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
    check("6b. dry-run: 대시보드 보기 → dashboard",
          "dashboard" in mapping.get(SUMMARY_LABEL, "").lower(), mapping.get(SUMMARY_LABEL, ""))
    check("6c. dry-run: 상세 리포트 보기 → latest.html",
          mapping.get(FULL_LABEL, "").lower().endswith("/latest.html"), mapping.get(FULL_LABEL, ""))


def _run_preflight(report_url: str = None, dashboard_url: str = None) -> subprocess.CompletedProcess:
    """send_telegram.py --preflight를 깨끗한 env로 실행 (워크플로 발송 전 게이트와 동일)."""
    return subprocess.run(
        [sys.executable, str(SENDER), "--preflight"],
        capture_output=True, text=True, timeout=60,
        env=_clean_env(REPORT_URL=report_url, DASHBOARD_URL=dashboard_url))


def check_preflight_output() -> None:
    """워크플로 발송 전 --preflight 출력 검증 (D7-C — 실제 발송 env에서 도는 그 명령).

    워크플로의 각 send step 바로 앞에서 동일 env로 실행되는 `send_telegram.py --preflight`를
    그대로 돌려, 라벨→경로 매핑을 'pathname only'로 단언한다:
      · '대시보드 보기'   → /daily/dashboard-latest.html
      · '상세 리포트 보기' → /daily/latest.html
    또한 출력에 host/스킴/토큰이 새지 않고(pathname only), 실제 발송 경로를 타지 않으며,
    종류 식별 불가 커스텀 URL이면 preflight이 비정상 종료로 send를 막는지 확인한다.
    """
    proc = _run_preflight(REPORT_URL, DASHBOARD_URL)
    out = (proc.stdout or "") + (proc.stderr or "")
    pf = {}
    for line in (proc.stdout or "").splitlines():
        m = re.match(r"button:\s*(.+?)\s*->\s*(\S+)$", line.strip())
        if m:
            pf[m.group(1)] = m.group(2)
    check("8a. preflight 정식 설정에서 exit 0", proc.returncode == 0, out[-200:])
    check("8b. preflight: '대시보드 보기' → /daily/dashboard-latest.html (pathname only)",
          pf.get(SUMMARY_LABEL, "").endswith("/daily/dashboard-latest.html"),
          pf.get(SUMMARY_LABEL, ""))
    check("8c. preflight: '상세 리포트 보기' → /daily/latest.html (pathname only)",
          pf.get(FULL_LABEL, "").endswith("/daily/latest.html"), pf.get(FULL_LABEL, ""))
    check("8d. preflight 출력은 pathname only — host/스킴/토큰 미노출",
          "https://" not in out and "http://" not in out
          and "example.github.io" not in out and not TOKEN_SHAPE.search(out))
    check("8e. preflight이 실제 발송 경로를 타지 않음(POST/발송 0건)",
          "Send status:" not in out and "delivery summary" not in out)
    bad = _run_preflight("https://x.example/p.html", "https://x.example/p.html")
    check("8f. 비정식 커스텀 URL은 preflight이 비정상 종료로 차단(send step 실패)",
          bad.returncode != 0,
          ((bad.stdout or "") + (bad.stderr or ""))[-160:])


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


def check_article_reader_targets() -> None:
    """기사 기본 CTA는 내부 reader, 외부 원문은 명시적 보조 링크인지 확인한다."""
    html = DASHBOARD_TEMPLATE.read_text(encoding="utf-8")
    check("9a. 기사 보기 → openArticleReader 내부 reader",
          "function openArticleReader" in html
          and 'onclick="return openArticleReader(this);">기사 보기</button>' in html)
    check("9b. 원문 사이트는 새 탭 보조 링크",
          "원문 사이트 ↗" in html and 'target="_blank"' in html
          and 'rel="noopener noreferrer"' in html)
    check("9c. reader 경로에 arbitrary proxy URL 없음",
          "/proxy?url=" not in html and "/fetch-url?url=" not in html)


def main() -> int:
    print("== verify_report_link_targets (D6-I 버튼 라벨→대상 매핑) ==")
    check_in_process_mapping()
    check_swap_resilience()
    check_telegram_payload_mapping()
    check_preflight_output()
    check_no_send()
    check_article_reader_targets()
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
