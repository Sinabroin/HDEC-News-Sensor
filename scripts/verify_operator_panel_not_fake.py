#!/usr/bin/env python3
"""D7-AE-RC1 verifier — 운영자 CTA가 '제품 기능처럼' 크게 보이지 않는지 정직성 감사.

사용자 실사용 QA 실패: "Teams / 메일 / 텔레그램 버튼이 비활성화되어 있다. 운영자 서버
미연결 상태인데 버튼이 크게 보여서 제품 기능처럼 보인다." verify_operator_controls.py
(D7-AA)는 버튼이 안전하게 배선됐는지(GitHub 미이동·시크릿 미노출·운영 API POST)만
검증했지, 얼마나 "크게 보이는지"는 검증하지 않았다 — 문자열 존재 여부만 보는 검사라
버튼이 상시 노출이든 접힘이든 통과했다(이 verifier가 그 공백을 메운다).

수정(app/templates/dashboard_preview.html): 3개 CTA(데이터 새로고침/텔레그램 전송/
Teams 채널 전송) + 상태줄을 기본 닫힌 <details id="opctlPanel">로 옮겼다. 항상 보이는
건 opctl-head의 요약 한 줄("운영자 모드 · 운영자 서버 미연결 · 설정 시에만 아래에서
실행할 수 있습니다")뿐이다. 운영 API base가 실제로 주입된 빌드에서만 JS가 패널을
자동으로 연다.

이 verifier가 잠그는 계약:
  A. 공개(미연결) 산출물에서 CTA 버튼은 닫힌 details 뒤에만 있다(상시 노출 마크업 0건).
  B. 항상 보이는 영역(요약 줄)은 실제로 작다 — 접힌 패널 콘텐츠보다 훨씬 짧다(크기 휴리스틱).
  C. operator_api_base가 비어 있으면 버튼은 disabled이고, "작은 설정 안내"만 보인다
     (장황한 "운영자 서버 미연결" 큰 배너/알림 박스가 아니다 — 배너류 클래스에 안 실림).
  D. secret이 필요한 기능은 정적 페이지에서 직접 호출하지 않는다(GitHub/Telegram 호스트
     리터럴 0건, 토큰 모양 0건) — verify_operator_controls의 안전계약을 이 파일에서도
     독립적으로 재확인한다(중복이 아니라 이 파일만 봐도 안전성이 증명되게).
  E. 운영자 API가 실제로 연결된 빌드에서는 패널이 자동으로 펼쳐진다(운영자에게는
     여전히 즉시 보인다 — 숨긴 게 아니라 청중에 맞게 접었을 뿐).

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
    """D7-AE-RC3(B안) — 공개(base 미설정) 빌드의 운영자 영역 계약.

    RC1은 'disabled 버튼을 접어서 작게'였지만 사용자 QA가 재지적("비활성 버튼을 제품
    기능처럼 남기지 마라") — 이제 공개 빌드는 실행 UI(버튼 3개·PIN·상태줄·details)를
    아예 렌더하지 않고 '운영 API 설정 필요' 한 줄만 남긴다. 버튼이 존재하는 유일한
    표면은 운영 API base가 주입된 운영자 빌드(템플릿 계약 — check_operator_build_expands).
    """
    section = _opctl_section(html)
    if not check(f"A0[{label}]: opctl 섹션 존재", bool(section)):
        return
    check(f"A1[{label}]: 실행 패널/버튼/PIN 마크업 0건(opctlPanel·opctl-btn·opPin)",
          "opctlPanel" not in section and 'class="opctl-btn' not in section
          and "opPin" not in section)
    check(f"A2[{label}]: '운영 API 설정 필요' 한 줄 존재", "운영 API 설정 필요" in section)
    check(f"A3[{label}]: 옛 문구('운영자 서버 미연결') 잔존 없음(섹션 내)",
          "운영자 서버 미연결" not in section)
    check(f"B1[{label}]: 섹션이 한 줄 안내 수준으로 작음(<800자)",
          len(section) < 800, f"len={len(section)}")


def check_not_big_banner(html: str, label: str) -> None:
    """'운영 API 설정 필요'가 배너/알림 박스가 아니라 한 줄(opctl-oneline)로만 표시된다."""
    section = _opctl_section(html)
    idx = section.find("운영 API 설정 필요")
    check(f"C1[{label}]: '운영 API 설정 필요' 문구 존재(정직 표기)", idx >= 0)
    window = section[max(0, idx - 220):idx]
    check(f"C2[{label}]: 배너류 클래스(banner/alert/hero)에 실려있지 않음",
          all(k not in window for k in ("banner", "alert", "hero")))
    check(f"C3[{label}]: 한 줄 요약(<span class=\"opctl-oneline)으로만 표시",
          'class="opctl-oneline' in window or 'class="opctl-oneline' in section)


def check_no_direct_privileged_calls(html: str, label: str) -> None:
    low = html.lower()
    check(f"D1[{label}]: api.github.com 미포함", "api.github.com" not in low)
    check(f"D2[{label}]: api.telegram.org 미포함", "api.telegram.org" not in low)
    check(f"D3[{label}]: GitHub Actions 수동실행 URL 미포함",
          "/actions/workflows/" not in low)
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
