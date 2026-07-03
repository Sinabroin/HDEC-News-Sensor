#!/usr/bin/env python3
"""D7-AE verifier — 임원용 UI 간결화 (executive UI simplification).

사용자 요구: 흰 배경 중심 · 파스텔 하늘색 장식 제거 · 색은 상태 표현 전용 ·
버튼 라벨은 역할이 즉시 드러나는 동사+목적어(모호 라벨 금지) · 첫 화면이
세 질문(무엇이 바뀌었나/어디가 위험한가/다음에 무엇을 클릭하나)에 답할 것.

이 verifier는 완전 오프라인으로 템플릿과 커밋 산출물 둘 다 잠근다:

  1. 모호 라벨 금지 — `보기`/`열기`/`확인`/`자세히` 단독 라벨 0건.
  2. 흰 지면 — 파스텔 그라데이션 배경 제거(flat), 카드 순백 유지,
     구 파스텔 스카이 토큰(#EAF2FC/#EFF4FA/#EAF1F8/#E8EFF8) 0건.
  3. 색=상태 계약 — 상태 클래스(tb-red/tb-amber/tb-green/tb-off ·
     wx-low/wx-watch/wx-high)와 '상태 표현 전용' 의도 문서화.
  4. 오늘의 판단 바 — todayStrip이 즉시 확인/신규 이슈/현장 매칭/명일 정오
     시공 리스크 4항목 + 명시 액션 라벨로 세 질문에 답한다. 카운트는 기존
     빌더 실측 배지/모델에서만 읽는다(스트립 자체 값 생성 금지 주석).
  5. 명시 어포던스 — "시세 열기 ▸"/"리스크 표 열기 ▸"/"전체 기사 더보기".
  6. 기존 계약 문자열 보존 — 기사 보기 버튼, 명일 정오 시공 리스크,
     기상 데이터 미수신, 시장 상태 보드 4라벨(간결화가 정직성 계약을
     지우지 않았음을 보증).
  7. 커밋 산출물 — 빌더 마커 존재(hand-edit 아님) + 동일 계약.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates" / "dashboard_preview.html"
DASHBOARD = ROOT / "docs" / "daily" / "dashboard-latest.html"

VAGUE = re.compile(r">(보기|열기|확인|자세히)<")
OLD_PASTEL = ("#EAF2FC", "#EFF4FA", "#EAF1F8", "#E8EFF8", "#F1F7FE")
LOCKED = (
    ">기사 보기</button>",
    "명일 정오 시공 리스크",
    "연동 완료", "보고·수동 확인", "미연동 후보", "우선 연동 필요",
)
WX_OLD = "기상 데이터 소스 미연동"
WX_UNAVAILABLE = "기상 데이터 미수신"
STRIP_ITEMS = ("즉시 확인", "신규 이슈", "현장 매칭", "명일 정오 시공 리스크")
STRIP_ACTIONS = ("즉시 확인 목록 보기", "신규 이슈 보기", "현장별 기사 확인",
                 "리스크 표 열기")

_failures: list = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    line = f"[{'PASS' if ok else 'FAIL'}] {name}"
    if detail:
        line += f" — {detail}"
    print(line)
    if not ok:
        _failures.append(name)
    return ok


def check_surface(label: str, text: str) -> None:
    vague = VAGUE.findall(text)
    check(f"1[{label}]: 모호 단독 라벨(보기/열기/확인/자세히) 0건", not vague,
          f"발견: {vague[:4]}")
    pastel = [h for h in OLD_PASTEL if h in text]
    check(f"2[{label}]: 구 파스텔 스카이 토큰 0건", not pastel, str(pastel))
    check(f"4[{label}]: 오늘의 판단 바(todayStrip) 존재",
          'id="todayStrip"' in text)
    for s in STRIP_ACTIONS:
        check(f"5[{label}]: 명시 액션 '{s}'", s in text)
    # D7-AE-RC1: "시세 열기 ▸"는 좌측 목차 navmkt 버튼 전용 라벨이었다 — 그 버튼 자체가
    # 시장 탭 상단 pill bar(#mktPillBar)로 옮겨가며 인터랙션 모델이 "목록 항목"에서
    # "탭/세그먼트 pill"로 바뀌었다(다른 기존 pill류 — NAV_CAT_PILLS 등 — 도 동사 접미
    # 없이 라벨만 쓴다. 탭 관용구는 그 자체로 자명해 "열기" 동사가 불필요). 어포던스
    # 명확성은 여전히 요구하되, pill bar 존재 + 자기설명적 카테고리 라벨로 검증한다.
    check(f"5[{label}]: 기상 탐색 어포던스(리스크 표 열기)", "리스크 표 열기 ▸" in text)
    check(f"5[{label}]: 시장 탐색은 상단 pill bar(자기설명적 카테고리 라벨, 좌측 중복 제거)",
          'id="mktPillBar"' in text and "현대건설 주가" in text
          and '<div class="gtitle">시장 모니터링</div>' not in text)
    check(f"5[{label}]: 뉴스 더보기 라벨 명시(전체 기사 더보기)",
          "전체 기사 더보기" in text)
    missing = [s for s in LOCKED if s not in text]
    check(f"6[{label}]: 기존 정직성 계약 문자열 보존", not missing,
          f"소실: {missing[:3]}")
    wx_live = '"weather_data_mode": "live"' in text
    if wx_live:
        check(f"6w[{label}]: weather live 산출물 — 옛 문구 raw HTML 0건(실측 렌더)",
              WX_OLD not in text)
    else:
        check(f"6w[{label}]: weather 비live — '{WX_UNAVAILABLE}' 자리표시자",
              WX_UNAVAILABLE in text and WX_OLD not in text)


def check_template_only() -> None:
    t = TEMPLATE.read_text(encoding="utf-8")
    # flat 지면 — body에 그라데이션 배경 없음 + 카드 순백.
    # 함정: `html,body{…}`가 먼저 걸리므로 standalone `body{` 룰만 잡는다.
    body = re.search(r"\n  body\{.*?\}", t, re.S)
    body_css = body.group(0) if body else ""
    check("2t: body 배경 flat(그라데이션 제거)",
          "linear-gradient" not in body_css and "background:var(--bg1)" in body_css)
    check("2t: 카드 순백(--card:#FFFFFF)", "--card:#FFFFFF" in t)
    check("2t: 지면은 warm off-white(--bg1 정의)", "--bg1:#F7F6F2" in t)
    # 색=상태 계약.
    for cls in (".tb-red", ".tb-amber", ".tb-green", ".tb-off",
                ".wx-risk.wx-low", ".wx-risk.wx-watch", ".wx-risk.wx-high"):
        check(f"3t: 상태 클래스 '{cls}'", cls in t)
    check("3t: '상태 표현 전용' 의도 문서화", "상태 표현 전용" in t)
    # 판단 바 데이터 정직성 — 스트립은 값을 만들지 않는다(주석 계약 + 소스 참조).
    check("4t: 스트립 렌더 함수 + 실측 소스만 참조",
          "function renderTodayStrip" in t and "renderTodayStrip();" in t
          and "recent_matched_nodes" in t
          and "이 스트립은 어떤 값도 만들지 않는다" in t)
    for s in STRIP_ITEMS:
        check(f"4t: 판단 바 항목 '{s}'", s in t)
    # D7-AE-RC1: 사용자 실사용 QA가 이 serif 마스트헤드를 "구리다"고 명시 지적했다
    # ("serif/Georgia 스타일은 제거하라") — 계약을 반대로 뒤집는다: serif 변수/사용처가
    # 없어야 하고, Pretendard가 최우선 폰트여야 한다(전용 검증은
    # verify_hdec_visual_system.py). 외부 폰트 CDN 0건 계약은 그대로 유지.
    check("7t: serif 변수/마스트헤드 제거됨(Pretendard로 대체, 사용자 지적 반영)",
          "--serif:Georgia" not in t and "font-family:var(--serif)" not in t
          and "Georgia" not in t and "Times New Roman" not in t)
    check("7t: Pretendard가 body 최우선 폰트", "font-family:Pretendard," in t)
    check("7t: 외부 폰트/CDN 0건(@font-face·fonts.googleapis 없음)",
          "@font-face" not in t and "fonts.googleapis" not in t.lower())


def check_committed() -> None:
    if not DASHBOARD.exists():
        check("8: 커밋 대시보드 존재", False)
        return
    html = DASHBOARD.read_text(encoding="utf-8")
    check("8: 커밋 산출물은 빌더 생성(마커 존재 — hand-edit 아님)",
          "dashboard-export:summary" in html)
    check_surface("커밋", html)


def main() -> int:
    print(f"== verify_dashboard_ui_simplification (D7-AE) @ {ROOT} ==")
    check_surface("템플릿", TEMPLATE.read_text(encoding="utf-8"))
    check_template_only()
    check_committed()
    print()
    if _failures:
        print(f"RESULT: FAIL ({len(_failures)} 항목)")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("RESULT: PASS — 임원용 UI 간결화: 흰 지면 · 상태색 전용 · 명시 액션 라벨 · "
          "오늘의 판단 바 (D7-AE)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
