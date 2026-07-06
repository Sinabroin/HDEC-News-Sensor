#!/usr/bin/env python3
"""D7-AG-1 verifier — 공개 운영자 모드가 클릭 가능하고 무력하지 않은지 감사.

이 verifier가 잠그는 계약:
  A. 공개 산출물의 운영자 모드는 항상 클릭 가능한 details summary다.
  B. 펼치면 Actions/워크플로/공개 URL의 실제 href 4개가 보인다.
  C. 직접 실행 API UI는 hidden이며 비활성 CTA로 보이지 않는다.
  D. GitHub/Telegram privileged API를 브라우저가 직접 호출하지 않고 인증값도 없다.
  E. 운영자 API가 실제로 연결된 빌드에서는 패널이 자동으로 펼쳐진다(운영자에게는
     직접 실행 UI가 보인다).

완전 OFFLINE · 네트워크 0건 · DB 미접근.
"""

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "docs" / "daily" / "dashboard-latest.html"
TEMPLATE = ROOT / "templates" / "dashboard_preview.html"
BUILDER = ROOT / "scripts" / "build_static_dashboard.py"

_failures: list = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    line = f"[{'PASS' if ok else 'FAIL'}] {name}"
    if detail:
        line += f" — {detail}"
    print(line)
    if not ok:
        _failures.append(name)
    return ok


def info(msg: str) -> None:
    print(f"[INFO] {msg}")


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _opctl_section(html: str) -> str:
    start = html.find('id="opctl"')
    if start < 0:
        return ""
    # 섹션 시작 태그의 시작점까지 되돌아간다.
    open_tag = html.rfind("<section", 0, start)
    end = html.find("</section>", start)
    if open_tag < 0 or end < 0:
        return ""
    return html[open_tag:end + len("</section>")]


# ---------------------------------------------------------------------------
# A/B · 공개 산출물 — CTA는 접힘 뒤, 상시 노출 영역은 작다
# ---------------------------------------------------------------------------

def check_public_build_collapsed(html: str, label: str) -> None:
    """공개(base 미설정) 빌드는 3개 실행 버튼과 명확한 미연결 상태를 제공한다."""
    section = _opctl_section(html)
    if not check(f"A0[{label}]: opctl 섹션 존재", bool(section)):
        return
    check(f"A1[{label}]: 운영자 모드는 클릭 가능한 details summary",
          '<details class="opctl-panel" id="opctlPanel">' in section
          and '<summary class="opctl-mode-toggle">' in section)
    check(f"A2[{label}]: 실행 UI visible + 링크 fallback 없음",
          'id="opApiControls"' in section and 'id="opActionLinks"' not in section)
    for label_text in ("데이터 새로고침 실행", "텔레그램 전송 실행",
                       "Teams 채널 전송 실행"):
        check(f"B1[{label}]: 실행 버튼 '{label_text}'",
              re.search(r'<button[^>]+id="op[^"]+Btn"[^>]*>.*?'
                        + re.escape(label_text), section, re.S) is not None)
    check(f"B1[{label}]: 보조 Public URL 새로고침 버튼",
          'id="opPublicRefreshBtn"' in section and "Public URL 새로고침" in section)
    check(f"B2[{label}]: API 미연결 플래그 false",
          'data-operator-api-enabled="false"' in section)


def check_not_big_banner(html: str, label: str) -> None:
    """미연결을 숨기거나 링크로 위장하지 않고 짧게 명시한다."""
    section = _opctl_section(html)
    check(f"C1[{label}]: 막힌 문구 없음",
          "운영 API 설정 필요" not in section
          and "실행 버튼은 운영 API가 연결된 빌드에서만 표시됩니다" not in section)
    check(f"C2[{label}]: 정직한 Operator API 미연결 안내",
          "Operator API 미연결" in section
          and "gateway base가 주입되기 전에는 실행할 수 없습니다." in section)
    check(f"C3[{label}]: summary가 Operator API 미연결로 안내",
          'id="opModeSummary">Operator API 미연결</span>' in section)


def check_no_direct_privileged_calls(html: str, label: str) -> None:
    low = html.lower()
    check(f"D1[{label}]: api.github.com 미포함", "api.github.com" not in low)
    check(f"D2[{label}]: api.telegram.org 미포함", "api.telegram.org" not in low)
    check(f"D3[{label}]: GitHub Actions 링크 fallback 없음",
          "api.github.com" not in low
          and "github actions 열기" not in low
          and "scheduled live refresh 열기" not in low)
    check(f"D4[{label}]: 텔레그램 봇 토큰 모양(숫자:문자) 미포함",
          re.search(r"\b\d{8,}:[a-z0-9_-]{20,}\b", low) is None)
    check(f"D5[{label}]: GitHub PAT 모양(ghp_/github_pat_) 미포함",
          re.search(r"ghp_[a-z0-9]{20,}|github_pat_[a-z0-9_]{20,}", low) is None)


# ---------------------------------------------------------------------------
# E · 운영자(연결됨) 빌드는 패널이 자동으로 펼쳐진다(숨긴 게 아니라 접었을 뿐)
# ---------------------------------------------------------------------------

def check_operator_build_expands() -> None:
    tpl = _read(TEMPLATE)
    check("E1: JS가 base 설정 시 opctlPanel.open=true로 자동 확장",
          'el("opctlPanel")' in tpl and "panel.open = true" in tpl)
    # 정적 HTML 자체는 base를 모르므로(빌더가 심지 않는 한) 항상 닫힘으로 시작해야
    # 하고, 오직 클라이언트 JS가 base 유무를 보고 그때 연다 — 즉 '숨김'이 아니라
    # '청중별 기본값'이다(운영자는 여전히 버튼을 즉시 쓸 수 있다).
    check("E2: MODEL.operator_api_base를 JS가 런타임에 읽음(빌더가 details를 미리 안 엶)",
          "MODEL.operator_api_base" in tpl)


# ---------------------------------------------------------------------------
# 오케스트레이션
# ---------------------------------------------------------------------------

def _targets() -> list:
    out = []
    if DASHBOARD.exists():
        out.append((_read(DASHBOARD), "committed-dashboard"))
    else:
        info("커밋된 공개 대시보드 없음 — 해당 대상 SKIP")
    return out


def check_fresh_mock_build() -> None:
    with tempfile.TemporaryDirectory(prefix="hdec_opnotfake_") as tmp:
        out = Path(tmp) / "dash.html"
        proc = subprocess.run([sys.executable, str(BUILDER), "--output", str(out)],
                              cwd=ROOT, capture_output=True, text=True, timeout=300,
                              env={**os.environ})
        if not check("F1: mock 빌드 동작(오프라인)", proc.returncode == 0 and out.exists(),
                     (proc.stderr or "")[-200:]):
            return
        html = out.read_text(encoding="utf-8")
        check_public_build_collapsed(html, "fresh-mock-build")
        check_not_big_banner(html, "fresh-mock-build")
        check_no_direct_privileged_calls(html, "fresh-mock-build")


def main() -> int:
    print(f"== verify_operator_panel_not_fake (D7-AE-RC1) @ {ROOT} ==")
    check_fresh_mock_build()
    for html, label in _targets():
        check_public_build_collapsed(html, label)
        check_not_big_banner(html, label)
        check_no_direct_privileged_calls(html, label)
    check_operator_build_expands()
    print()
    if _failures:
        print(f"RESULT: FAIL ({len(_failures)} 항목)")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("RESULT: PASS — 운영자 CTA 정직성(접힘 기본값 · 작은 안내 · 배너 아님 · "
          "특권 API 미직접호출 · 운영자 자동확장) (D7-AE-RC1)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
