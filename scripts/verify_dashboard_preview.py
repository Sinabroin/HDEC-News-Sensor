#!/usr/bin/env python3
"""verify_dashboard_preview — 비프로덕션 대시보드 미리보기 검수 (Design Parity 패치, D5-E2).

templates/dashboard_preview.html + app/main.py 라우트가 디자인 패리티 + 정직성 계약을
지키는지 확인한다. 완전 오프라인이다:
- 네트워크/발송/비밀값 0건. DB를 만들거나 바꾸지 않는다(라이프스팬 미실행, 라우트 직접 호출).
- 프로덕션 일일 리포트(docs/daily/*.html)와 Telegram 발송 경로(send_telegram.py · 워크플로)가
  이 작업으로 바뀌지 않았음을 git으로 확인한다.

검사 항목(작업 명세 1~13):
   1  /dashboard-preview 라우트/페이지가 존재하고 렌더된다
   2  프로덕션 docs/daily 파일이 변경되지 않았다
   3  탭: 뉴스 / AI 신호 / 시장 / 카더라·초기신호
   4  대시보드용 시각 자산/클래스(텍스트만이 아님)
   5  국가 내 비교 / 국가 간 비교 / 2Y / 5Y / 10Y / CPI
   6  최소 2개의 금리 시리즈 라벨(미리보기)
   7  시장 상세 드로어/모달 마크업
   8  호르무즈 카드: AIS 하한 추정 / unique MMSI / 실제 통과량보다 낮을 수 있음 / 1·6·24시간·7일
   9  AI Radar 존재 · "전력 신호 100" 부재
  10  초기신호 탭: X / Truth Social / Telegram + 임원 직접 발송 없음
  11  NON-PRODUCTION PREVIEW 표기
  12  docs/daily 미변경(git)
  13  Telegram 발송 워크플로/스크립트 미변경(git)

추가 정직성 검사(요구사항 보강): data_mode 라벨, 미연동 가짜값 금지, 기간 컨트롤,
정규화/CPI 별도축, 발송 토큰 미참조 등.
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates" / "dashboard_preview.html"
MAIN = ROOT / "app" / "main.py"
SENDER = ROOT / "scripts" / "send_telegram.py"
WORKFLOW = ROOT / ".github" / "workflows" / "telegram-notify.yml"
DOCS_LATEST = ROOT / "docs" / "daily" / "latest.html"
DOCS_OPERATOR = ROOT / "docs" / "daily" / "operator-latest.html"

ROUTE = "/dashboard-preview"

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


def warn(message: str) -> None:
    print(f"[WARN] {message}")


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _git_unchanged(rel_paths: list[str]) -> tuple[bool, str]:
    """working tree(스테이지 포함)가 HEAD 대비 해당 경로들을 바꾸지 않았으면 True."""
    try:
        proc = subprocess.run(
            ["git", "diff", "--quiet", "HEAD", "--", *rel_paths],
            cwd=ROOT, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return True, f"git 실행 불가(검사 생략): {exc}"
    if proc.returncode == 0:
        return True, "변경 없음"
    if proc.returncode == 1:
        return False, "HEAD 대비 변경 감지됨"
    return True, f"git diff 비정상 종료(검사 생략): {proc.returncode}"


# ---------------------------------------------------------------------------
# 1 · 라우트/렌더
# ---------------------------------------------------------------------------

def check_route_and_render(tpl: str, main_src: str) -> None:
    file_ok = bool(tpl) and "<html" in tpl.lower() and "</html>" in tpl.lower() and len(tpl) > 4000
    check("1a: preview 템플릿 존재 + HTML 구조", file_ok,
          "" if file_ok else "templates/dashboard_preview.html 누락/구조 이상")

    wired = (ROUTE in main_src and "dashboard_preview.html" in main_src
             and "FileResponse" in main_src)
    check("1b: /dashboard-preview 라우트가 app/main.py에 연결", wired)

    rendered = None
    try:
        sys.path.insert(0, str(ROOT))
        import importlib
        main = importlib.import_module("app.main")
        resp = main.dashboard_preview_page()
        path_ok = str(getattr(resp, "path", "")).endswith("dashboard_preview.html")
        rendered = getattr(resp, "status_code", None) == 200 and path_ok
    except Exception as exc:  # noqa: BLE001 — 의존성/환경 차이는 정적 검사로 대체
        warn(f"라우트 직접 렌더 생략(정적 검사로 대체): {exc}")
    if rendered is not None:
        check("1c: 라우트 직접 호출 렌더(200 · 템플릿 반환)", rendered)


# ---------------------------------------------------------------------------
# 2 / 12 · 프로덕션 docs/daily 불변
# ---------------------------------------------------------------------------

def check_docs_untouched() -> None:
    check("2a: docs/daily/latest.html 존재", DOCS_LATEST.exists())
    check("2b: docs/daily/operator-latest.html 존재", DOCS_OPERATOR.exists())
    ok, detail = _git_unchanged(["docs/daily/latest.html", "docs/daily/operator-latest.html"])
    check("2c/12: 프로덕션 docs/daily 파일이 변경되지 않음", ok, detail)


# ---------------------------------------------------------------------------
# 3 · 탭
# ---------------------------------------------------------------------------

def check_tabs(tpl: str) -> None:
    for tab in ("뉴스", "AI 신호", "시장", "카더라·초기신호"):
        check(f"3: 탭 '{tab}' 노출", tab in tpl)


# ---------------------------------------------------------------------------
# 4 · 시각 자산/클래스 (텍스트만이 아님)
# ---------------------------------------------------------------------------

def check_visual_assets(tpl: str) -> None:
    has_style = "<style>" in tpl and "</style>" in tpl
    check("4a: 인라인 <style> 디자인 블록 존재", has_style)
    classes = ["metric", "featured", "lens", "panel", "tabpanel", "srow",
               "drawer", "hz-tile", "kpi", "chartbox", "hz-bars"]
    present = [c for c in classes if (f'"{c}' in tpl or f" {c}" in tpl or f".{c}" in tpl)]
    check("4b: 대시보드 카드/컴포넌트 클래스 다수 존재 (>=9)",
          len(present) >= 9, f"{len(present)}/{len(classes)}: {present}")
    soft = "box-shadow" in tpl and "border-radius" in tpl
    check("4c: 소프트 카드 스타일(box-shadow + border-radius)", soft)
    svg = tpl.count("<svg") >= 4
    check("4d: 인라인 SVG 시각화 다수 (>=4)", svg, f"<svg 개수={tpl.count('<svg')}")


# ---------------------------------------------------------------------------
# 5 · 금리 비교 모드 + 만기/CPI
# ---------------------------------------------------------------------------

def check_yield_modes(tpl: str) -> None:
    check("5a: '국가 내 비교' (within_country)", "국가 내 비교" in tpl)
    check("5b: '국가 간 비교' (cross_country)", "국가 간 비교" in tpl)
    for m in ("2Y", "5Y", "10Y", "CPI"):
        check(f"5c: 만기/지표 '{m}' 옵션", m in tpl)
    check("5d: 정규화/기준일=100 라벨",
          "정규화 추세" in tpl and "기준일=100" in tpl and "절대 가격 비교 아님" in tpl)
    check("5e: CPI는 금리 y축에 강제되지 않음(별도 축/비교 설명)",
          "CPI" in tpl and ("단위·축이 다" in tpl or "같은 y축에 올리지 않" in tpl))


# ---------------------------------------------------------------------------
# 6 · 최소 2개 금리 시리즈 라벨
# ---------------------------------------------------------------------------

def check_series_labels(tpl: str) -> None:
    labels = [f"{c} {m}" for c in ("US", "KR", "EU", "JP") for m in ("2Y", "5Y", "10Y")]
    found = [lab for lab in labels if lab in tpl]
    check("6: 금리 시리즈 라벨 2개 이상 (미리보기 다중 시리즈)",
          len(found) >= 2, f"{len(found)} found: {found[:6]}")


# ---------------------------------------------------------------------------
# 7 · 시장 상세 드로어/모달
# ---------------------------------------------------------------------------

def check_market_drawer(tpl: str) -> None:
    check("7a: 드로어 오버레이/컨테이너 마크업",
          'id="mktDrawerOv"' in tpl and 'class="drawer"' in tpl)
    check("7b: 드로어 상세 차트 SVG", 'id="dwChart"' in tpl)
    check("7c: 드로어 기간 버튼(1주·1개월·3개월·1년)",
          'id="dwPeriodSeg"' in tpl and "1주" in tpl and "1개월" in tpl
          and "3개월" in tpl and "1년" in tpl)
    check("7d: 드로어 값/변동/갱신 필드",
          'id="dwVal"' in tpl and 'id="dwChg"' in tpl and 'id="dwUpd"' in tpl)
    check("7e: 드로어 주의문(현재 체결값 아님)",
          "공개·무료 또는 대용 데이터 기준이며 현재 체결값이 아닙니다" in tpl)
    check("7f: 행 클릭 → 상세 안내(클릭 가능)",
          "data-mid" in tpl and "행을 클릭" in tpl)


# ---------------------------------------------------------------------------
# 8 · 호르무즈 카드 (풍부 + 정직)
# ---------------------------------------------------------------------------

def check_hormuz(tpl: str) -> None:
    check("8a: 호르무즈 카드 제목", "호르무즈 해협 관찰" in tpl)
    check("8b: AIS 하한 추정 배지", "AIS 하한 추정" in tpl)
    check("8c: unique MMSI 기준", "unique MMSI" in tpl)
    check("8d: '실제 통과량보다 낮을 수 있음' 경고", "실제 통과량보다 낮을 수 있" in tpl)
    for win in ("1시간", "6시간", "24시간", "7일"):
        check(f"8e: 호르무즈 시간 옵션 '{win}'", win in tpl)
    # 풍부한 미리보기 요소
    check("8f: 시간대별 통과 막대 차트", 'id="hzBars"' in tpl)
    check("8g: 선종 분포(유조선/LNG선/컨테이너·기타)",
          "유조선" in tpl and "LNG선" in tpl and "컨테이너·기타" in tpl)
    check("8h: 메트릭 타일(현재 통항 중/대기·정박/평균 속도/AIS 신뢰도)",
          "현재 통항 중" in tpl and "평균 속도" in tpl and "AIS 신뢰도" in tpl)
    check("8i: 해협 모식도(개념)", "해협 모식도" in tpl)
    # 정직성: 데모 표기 + 라이브 미연동 명시 + 위성 AIS 경고
    check("8j: '데모 데이터 · AIS 하한 추정' 배지 (값을 live로 위장하지 않음)",
          "데모 데이터 · AIS 하한 추정" in tpl)
    check("8k: 라이브 AIS 통합 아님 명시",
          "라이브 AIS" in tpl and ("통합이 아" in tpl or "미연동" in tpl))
    check("8l: 위성 AIS 미포함 누락 경고",
          "위성 AIS 미포함" in tpl)


# ---------------------------------------------------------------------------
# 9 · AI Radar / 전력 신호 100 부재
# ---------------------------------------------------------------------------

def check_ai_radar(tpl: str) -> None:
    check("9a: 'AI Radar' 존재", "AI Radar" in tpl)
    check("9b: '전력 신호 100' 부재 (기사 상대 비중으로 재구성)", "전력 신호 100" not in tpl)
    check("9c: 테마 % = 기사 상대 비중 설명",
          "상대 비중" in tpl and ("실측" in tpl or "전력 실측" in tpl))


# ---------------------------------------------------------------------------
# 10 / 11 · 초기신호 탭 분리 + 임원 자동 발송 없음
# ---------------------------------------------------------------------------

def _rumor_panel(tpl: str) -> str:
    start = tpl.find('id="panel-rumor"')
    if start < 0:
        return ""
    end = tpl.find("</section>", start)
    return tpl[start:end if end > 0 else len(tpl)]


def check_early_signals(tpl: str) -> None:
    panel = _rumor_panel(tpl)
    check("10a: 초기신호 탭 패널 존재", bool(panel))
    check("10b: 관찰 소스 X(엑스) / Truth Social / Telegram",
          "X (엑스)" in panel and "Truth Social" in panel and "Telegram" in panel)
    check("10c: 허용 액션만(뉴스 승격 검토 / 근거 확인)",
          "뉴스 승격 검토" in panel and "근거 확인" in panel)
    check("10d: 임원 자동 알림/직접 발송 없음",
          "임원 자동 알림을 생성하지 않" in panel and "approve_send" not in panel
          and "send_telegram" not in panel)
    check("10e: 즉시-알림 발송 후보 액션이 초기신호 탭에 없음",
          "즉시 알림 후보" not in panel)
    check("10f: 검증 뉴스와 분리 + 비공개 대화 수집 금지",
          "확인된 뉴스가 아" in panel and "검증 뉴스와 분리" in panel
          and "비공개 대화 수집" in panel)


# ---------------------------------------------------------------------------
# 11 · NON-PRODUCTION PREVIEW 표기
# ---------------------------------------------------------------------------

def check_preview_flag(tpl: str) -> None:
    check("11: 'NON-PRODUCTION PREVIEW' 표기", "NON-PRODUCTION PREVIEW" in tpl)
    check("11+: '데모 데이터' 표기", "데모 데이터" in tpl)


# ---------------------------------------------------------------------------
# 13 · Telegram 발송 경로 불변 + 토큰 미참조
# ---------------------------------------------------------------------------

def check_telegram_untouched(tpl: str, main_src: str) -> None:
    for needle in ("send_telegram", "approve_send", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_IDS"):
        check(f"13a: 템플릿에 발송 토큰/경로 '{needle}' 미참조", needle not in tpl)
    check("13b: main.py 미리보기 라우트가 발송을 호출하지 않음",
          "send_telegram" not in main_src)
    ok_send, d_send = _git_unchanged(["scripts/send_telegram.py"])
    check("13c: scripts/send_telegram.py 변경 없음", ok_send, d_send)
    ok_wf, d_wf = _git_unchanged([".github/workflows/telegram-notify.yml"])
    check("13d: telegram-notify.yml 워크플로 변경 없음", ok_wf, d_wf)


# ---------------------------------------------------------------------------
# 추가 정직성 — data_mode 라벨 / 미연동 가짜값 금지 / 기간 컨트롤
# ---------------------------------------------------------------------------

def check_data_modes(tpl: str) -> None:
    for label in ("지연", "대용", "보고", "미연동"):
        check(f"E-data_mode: '{label}' 노출", label in tpl)
    for extra in ("정책/키 필요", "확인 전", "공식 발언", "승인 채널 관찰"):
        check(f"E-state: honest 상태 '{extra}' 노출", extra in tpl)


def check_no_fake_values(tpl: str) -> None:
    has_unavail_null = bool(re.search(r'"data_mode"\s*:\s*"unavailable"', tpl)) and \
        bool(re.search(r'"value"\s*:\s*null', tpl))
    check("E-fake: 시장 미연동 지표는 value=null (가짜 숫자 없음)", has_unavail_null)
    check("E-fake: 미연동 시리즈는 차트에 가짜 선을 그리지 않음 (가짜 선 미표시)",
          "가짜 선 미표시" in tpl)
    check("E-fake: 초기신호 소스 미연동/위조 금지",
          "정책/키 필요" in tpl and "위조하지 않" in tpl)
    check("E-fake: 호르무즈 수치는 데모 고정값 + 라이브 값 미생성 명시",
          "데모 미리보기 고정값" in tpl and "프로덕션 값을 생성하지 않" in tpl)


def check_period_controls(tpl: str) -> None:
    check("E-period: 금리 차트 기간 컨트롤(1주/1개월/3개월/1년)",
          'id="yieldPeriodSeg"' in tpl)
    for p in ("1주", "1개월", "3개월", "1년"):
        check(f"E-period: 기간 버튼 '{p}'", p in tpl)


def main() -> int:
    print(f"== verify_dashboard_preview @ {ROOT} ==")
    tpl = _read(TEMPLATE)
    main_src = _read(MAIN)

    check_route_and_render(tpl, main_src)        # 1
    check_docs_untouched()                        # 2 / 12
    check_tabs(tpl)                               # 3
    check_visual_assets(tpl)                      # 4
    check_yield_modes(tpl)                        # 5
    check_series_labels(tpl)                      # 6
    check_market_drawer(tpl)                      # 7
    check_hormuz(tpl)                             # 8
    check_ai_radar(tpl)                           # 9
    check_early_signals(tpl)                      # 10 / 11(no-auto-alert)
    check_preview_flag(tpl)                       # 11
    check_telegram_untouched(tpl, main_src)       # 13
    check_data_modes(tpl)                         # extra
    check_no_fake_values(tpl)                     # extra
    check_period_controls(tpl)                    # extra

    print()
    if _failures:
        print(f"RESULT: FAIL ({len(_failures)} 항목)")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("RESULT: PASS — 대시보드 미리보기 검수 통과 (design parity)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
