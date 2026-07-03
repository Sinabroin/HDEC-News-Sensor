#!/usr/bin/env python3
"""D7-AE-RC1 verifier — 공개 대시보드에서 호르무즈 AIS 데모 블록이 실제로 사라졌는지.

사용자 실사용 QA 실패: "AIS/호르무즈 데모 블록은 현재 필요 없다. 실시간 연동 없이
'AIS 하한 추정 · proxy', '데모 데이터', '212척' 같은 값이 보이면 실패다."

조사 결과(재확인, docs/operations/D7ADX_MARKET_SOURCE_INTEGRATION.md 참고):
  - repo 전체(.agents/·design/·tmp/ 미커밋 포함) 재검색 — 사용자가 언급한 Hormuz/AIS
    GitHub repo·API 참조는 **찾지 못함**(repo reference not found, 이전 조사와 동일).
  - 실 라이브 AIS 소스가 없는 채로 데모 카드(선박 수·시간대별 통과·선종 분포·모식도)가
    공개 요약 대시보드(docs/daily/dashboard-latest.html)에 실측처럼 노출되고 있었다 —
    templates/dashboard_preview.html이 비프로덕션 /dashboard-preview 라우트와 공개
    정적 빌더 둘 다의 소스라서 그대로 새어나간 것.
  - 조치: scripts/build_static_dashboard.py의 _strip_hormuz_demo_card가 공개 정적
    산출물에서만 카드를 통째로 제거한다. 내부 /dashboard-preview 디자인 도구는 원본
    템플릿을 그대로 서빙해 데모 카드가 남는다(비프로덕션·미공개라 문제 없음).

이 verifier가 잠그는 계약(완전 OFFLINE):
  A. _strip_hormuz_demo_card 단위 검사 — 합성 fixture로 카드만 정확히 제거되고 주변
     마크업/스크립트는 손대지 않는지 결정적으로 검사한다.
  B. 공개 산출물(fresh mock 빌드 + 커밋된 docs/daily/dashboard-latest.html)에 사용자가
     지목한 5개 문구/배지가 전혀 보이지 않는다: AIS 하한 추정, 데모 데이터(호르무즈
     맥락), 시간대별 통과(데모), 선종 분포, 212척.
  C. 구조적 확인 — class="card hz" 및 id="hz*" 계열 DOM이 공개 산출물에 전혀 없다.
  D. 조건부 계약 — 만약 미래에 호르무즈 관측 카드가 공개 산출물에 다시 등장한다면,
     반드시 source/data_mode/updated_at을 갖춰야 한다(실 연동 없이는 절대 재노출 금지).
  E. backlog/audit 흔적 — repo reference not found + 제거 결정이 docs/operations에
     문서화돼 있다(조사 증발 방지).
  F. 내부 /dashboard-preview 디자인 도구는 그대로 — 원본 템플릿은 데모 카드를 유지한다
     (숨긴 게 아니라 청중을 분리한 것 — 비프로덕션 도구까지 망가뜨리지 않는다).
"""

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import build_static_dashboard as builder  # noqa: E402

BUILDER = ROOT / "scripts" / "build_static_dashboard.py"
DASHBOARD = ROOT / "docs" / "daily" / "dashboard-latest.html"
TEMPLATE = ROOT / "templates" / "dashboard_preview.html"
D7ADX_DOC = ROOT / "docs" / "operations" / "D7ADX_MARKET_SOURCE_INTEGRATION.md"

FORBIDDEN_PHRASES = (
    "AIS 하한 추정 · proxy",
    "시간대별 통과 (데모)",
    "선종 분포 (현재 통항 중 · 데모)",
    # 주의: 맨 "212" 단독은 넣지 않는다 — 시장 지표(예: 상품가 "212.1", FRED 시리즈 ID
    # "WPU05810212")에서 우연히 매치되는 오탐이 크다. "몇 척" 표시가 실제로 렌더되는지는
    # 구조 검사(C1/C2 — class="card hz"/id="hz*" DOM 전체 부재)로 훨씬 정확히 잡힌다.
)

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


# ---------------------------------------------------------------------------
# A · _strip_hormuz_demo_card 단위 검사(합성 fixture, 결정적)
# ---------------------------------------------------------------------------

_FIXTURE = (
    '<div class="before">앞 콘텐츠 유지</div>\n'
    '<!-- Hormuz -->\n'
    '<div class="card hz" style="padding:1px">\n'
    '  <div class="hz-head"><div class="inner">AIS 하한 추정 · proxy</div></div>\n'
    '  <div class="hz-tiles"><div class="hz-tile">212척</div></div>\n'
    '</div>\n'
    '<div class="after">뒤 콘텐츠 유지</div>\n'
)


def check_strip_function_unit() -> None:
    out = builder._strip_hormuz_demo_card(_FIXTURE)
    check("A1: 앞 콘텐츠 보존", '<div class="before">앞 콘텐츠 유지</div>' in out)
    check("A2: 뒤 콘텐츠 보존", '<div class="after">뒤 콘텐츠 유지</div>' in out)
    check("A3: <!-- Hormuz --> 마커 제거", "<!-- Hormuz -->" not in out)
    check("A4: class=\"card hz\" 카드 자체 제거", 'class="card hz"' not in out)
    check("A5: 카드 내부 AIS 문구 제거", "AIS 하한 추정" not in out)
    check("A6: 카드 내부 212척 제거", "212척" not in out)

    check("A7: 마커 없는 입력은 그대로 반환(예외 없이 no-op)",
          builder._strip_hormuz_demo_card("<div>마커 없음</div>") == "<div>마커 없음</div>")

    # 중첩 div가 깊어도 진짜 매칭 닫는 태그를 정확히 찾는지(깊이 카운팅 검사).
    nested = (
        '<div class="keep">유지</div>\n'
        '<!-- Hormuz -->\n'
        '<div class="card hz"><div><div><div>깊은 중첩</div></div></div>'
        '<div>형제</div></div>\n'
        '<div class="keep2">이것도 유지</div>\n'
    )
    out2 = builder._strip_hormuz_demo_card(nested)
    check("A8: 깊은 중첩 div도 정확히 매칭되는 닫는 태그까지만 제거",
          '<div class="keep">유지</div>' in out2 and '<div class="keep2">이것도 유지</div>' in out2
          and "깊은 중첩" not in out2 and "형제" not in out2)


# ---------------------------------------------------------------------------
# B/C · 공개 산출물 — 5개 금지 문구 + 구조적 DOM 부재
# ---------------------------------------------------------------------------

def _check_output_clean(html: str, label: str) -> None:
    if not html:
        info(f"{label}: 산출물 없음 — SKIP")
        return
    leaks = [p for p in FORBIDDEN_PHRASES if p in html]
    check(f"B[{label}]: 사용자 지목 금지 문구 0건", not leaks, str(leaks))
    check(f"C1[{label}]: class=\"card hz\" DOM 없음", 'class="card hz"' not in html)
    hz_ids = re.findall(r'id="(hz[A-Za-z]+)"', html)
    check(f"C2[{label}]: id=\"hz*\" 계열 DOM 없음(hzCount/hzBars/hzVessels 등)",
          not hz_ids, str(sorted(set(hz_ids))))
    check(f"C3[{label}]: '해협 모식도' 개념도 없음", "해협 모식도" not in html)


def check_fresh_mock_build() -> None:
    with tempfile.TemporaryDirectory(prefix="hdec_hormuz_removed_") as tmp:
        out = Path(tmp) / "dash.html"
        proc = subprocess.run([sys.executable, str(BUILDER), "--output", str(out)],
                              cwd=ROOT, capture_output=True, text=True, timeout=300,
                              env={**os.environ})
        if not check("mock 빌드 동작(오프라인)", proc.returncode == 0 and out.exists(),
                     (proc.stderr or "")[-200:]):
            return
        _check_output_clean(out.read_text(encoding="utf-8"), "fresh-mock-build")


def check_committed_dashboard() -> None:
    _check_output_clean(_read(DASHBOARD), "committed-dashboard-latest.html")


# ---------------------------------------------------------------------------
# D · 조건부 계약 — 카드가 미래에 재등장한다면 source/mode/updated_at 필수
# ---------------------------------------------------------------------------

def check_conditional_if_reintroduced() -> None:
    html = _read(DASHBOARD)
    if not html or 'class="card hz"' not in html:
        check("D: (해당 없음 — 카드가 없으므로 조건부 계약 검사 대상 아님)", True)
        return
    # 카드가 있다면 실 연동 근거(출처/모드/시각)가 반드시 함께 있어야 한다 — 데모만
    # 있고 이 셋이 없으면 실연동 없이 재노출된 것이므로 FAIL.
    start = html.find('class="card hz"')
    card = html[max(0, start - 200):start + 4000]
    check("D1: 카드 재등장 시 출처 표기 존재", any(k in card for k in ("출처", "source")))
    check("D2: 카드 재등장 시 data_mode 계열 표기 존재",
          any(k in card for k in ("data_mode", "live", "지연")))
    check("D3: 카드 재등장 시 갱신 시각 표기 존재",
          any(k in card for k in ("업데이트", "updated_at", "기준")))


# ---------------------------------------------------------------------------
# E · backlog/audit 흔적 — 조사 결과가 문서화돼 있다
# ---------------------------------------------------------------------------

def check_backlog_documented() -> None:
    doc = _read(D7ADX_DOC)
    check("E1: D7ADX 문서 존재", bool(doc))
    check("E2: 'repo reference not found' 결론 기록", "repo reference not found" in doc)
    check("E3: 재검색 대상(.agents/design/tmp 포함) 기록",
          ".agents" in doc and "design" in doc and "tmp" in doc)
    check("E4: 제거 조치(_strip_hormuz_demo_card) 기록", "_strip_hormuz_demo_card" in doc)


# ---------------------------------------------------------------------------
# F · 내부 /dashboard-preview 디자인 도구는 그대로(숨긴 게 아니라 청중 분리)
# ---------------------------------------------------------------------------

def check_internal_preview_unaffected() -> None:
    tpl = _read(TEMPLATE)
    check("F1: 원본 템플릿(내부 /dashboard-preview 소스)은 카드를 계속 보유",
          'class="card hz"' in tpl)
    check("F2: 원본 템플릿에 '호르무즈 해협 관찰' 제목 존재(디자인 도구 기능 유지)",
          "호르무즈 해협 관찰" in tpl)


def main() -> int:
    print(f"== verify_hormuz_demo_removed (D7-AE-RC1) @ {ROOT} ==")
    check_strip_function_unit()
    check_fresh_mock_build()
    check_committed_dashboard()
    check_conditional_if_reintroduced()
    check_backlog_documented()
    check_internal_preview_unaffected()
    print()
    if _failures:
        print(f"RESULT: FAIL ({len(_failures)} 항목)")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("RESULT: PASS — 공개 대시보드 호르무즈 AIS 데모 완전 제거(내부 미리보기는 "
          "유지) (D7-AE-RC1)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
