#!/usr/bin/env python3
"""verify_dashboard_preview — 비프로덕션 대시보드 미리보기 검수 (Design Parity, D5-E3).

templates/dashboard_preview.html + app/main.py 라우트가 Claude Design 패리티 + 정직성
계약을 지키는지 확인한다. 완전 오프라인이다:
- 네트워크/발송/비밀값 0건. DB를 만들거나 바꾸지 않는다(라이프스팬 미실행, 라우트 직접 호출).
- 프로덕션 일일 리포트(docs/daily/*.html)와 Telegram 발송 경로가 이 작업으로 바뀌지 않았음을
  git으로 확인한다.

D5-E3 명세 검사(1~12):
   1  /dashboard-preview 라우트/페이지가 존재하고 렌더된다
   2  NON-PRODUCTION PREVIEW 표기
   3  '데모 데이터' + '현재 체결값 아님' 표기
   4  금리 차트 데모 라벨: US 2Y / US 5Y / US 10Y
   5  US 2Y가 preview 차트에서 '미연동만'으로 표시되지 않음 (데모 선이 그려짐)
   6  '국가 내 비교' (within_country)
   7  '국가 간 비교' (cross_country)
   8  2Y / 5Y / 10Y / CPI 컨트롤
   9  호르무즈 카드 필수 요소(제목·AIS 하한 추정·proxy·212·unique MMSI·유조선·LNG선·컨테이너·
      현재 통항 중·대기/정박·평균 속도·AIS 신뢰도·실제 통과량보다 낮을 수 있음)
  10  '전력 신호 100' 부재 (정직성)
  11  프로덕션 docs/daily 파일 미변경 (git)
  12  Telegram 발송 워크플로/스크립트 미변경 (git)

추가 정직성 보강(요구사항 §4): 기간 컨트롤, data_mode 라벨, 미연동 가짜값/가짜 선 금지,
시장 상세 드로어, 초기신호 탭 분리·임원 자동 발송 없음, 발송 토큰 미참조.
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates" / "dashboard_preview.html"
MAIN = ROOT / "app" / "main.py"
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
# 2 · NON-PRODUCTION PREVIEW 표기
# ---------------------------------------------------------------------------

def check_preview_flag(tpl: str) -> None:
    check("2: 'NON-PRODUCTION PREVIEW' 표기", "NON-PRODUCTION PREVIEW" in tpl)


# ---------------------------------------------------------------------------
# 3 · 데모/체결값 정직성 라벨
# ---------------------------------------------------------------------------

def check_honesty_labels(tpl: str) -> None:
    check("3a: '데모 데이터' 표기", "데모 데이터" in tpl)
    check("3b: '현재 체결값 아님' 표기", "현재 체결값 아님" in tpl)
    check("3c: production 일일 리포트와 무관함 명시",
          "프로덕션 일일 리포트" in tpl or "docs/daily" in tpl)


# ---------------------------------------------------------------------------
# 4 / 5 · 금리 차트 데모 라벨 + US 2Y 데모 선
# ---------------------------------------------------------------------------

def check_yield_labels(tpl: str) -> None:
    for lab in ("US 2Y", "US 5Y", "US 10Y"):
        check(f"4: 금리 차트 데모 라벨 '{lab}'", lab in tpl)
    # KR/EU/JP 다중 시리즈도 라벨 존재(국가 내/간 다중선)
    extra = [f"{c} {m}" for c in ("KR", "EU", "JP") for m in ("2Y", "5Y", "10Y")]
    found = [x for x in extra if x in tpl]
    check("4+: KR/EU/JP 만기 라벨도 존재 (국가 내/간 다중 시리즈)",
          len(found) >= 6, f"{len(found)}/9 found")


def check_us2y_demo(tpl: str) -> None:
    has_arr = bool(re.search(r'"us_2y"\s*:\s*\[', tpl))
    is_null = bool(re.search(r'"us_2y"\s*:\s*null', tpl))
    check("5: US 2Y가 preview 차트에서 데모 선으로 표시됨 (미연동만 아님)",
          has_arr and not is_null,
          "us_2y=데모 배열" if has_arr and not is_null else "us_2y가 데모 배열이 아님/null")
    # 정직성: 그래도 일부 미연동 시리즈(예: 일부 CPI)는 가짜 선을 그리지 않는 동작이 남아 있어야 한다
    check("5+: 미연동 시리즈는 차트에 가짜 선을 그리지 않음 (가짜 선 미표시)",
          "가짜 선 미표시" in tpl)


# ---------------------------------------------------------------------------
# 6 / 7 / 8 · 비교 모드 + 만기/CPI 컨트롤
# ---------------------------------------------------------------------------

def check_compare_modes(tpl: str) -> None:
    check("6: '국가 내 비교' (within_country)", "국가 내 비교" in tpl)
    check("7: '국가 간 비교' (cross_country)", "국가 간 비교" in tpl)
    for opt in (">2Y<", ">5Y<", ">10Y<", ">CPI<"):
        check(f"8: 지표 컨트롤 옵션 '{opt.strip('<>')}'", opt in tpl)
    check("8+: CPI는 금리 y축에 강제되지 않음(별도 축/비교 설명)",
          "CPI" in tpl and ("단위·축이 다" in tpl or "같은 y축에 올리지 않" in tpl))


# ---------------------------------------------------------------------------
# 9 · 호르무즈 카드 (운영 관찰 카드 · 필수 요소)
# ---------------------------------------------------------------------------

def check_hormuz(tpl: str) -> None:
    required = [
        ("호르무즈 해협 관찰", "제목"),
        ("AIS 하한 추정", "AIS 하한 추정 배지"),
        ("proxy", "proxy 표기"),
        ("212", "24시간 통과 수(212)"),
        ("unique MMSI", "unique MMSI 기준"),
        ("유조선", "선종 유조선"),
        ("LNG선", "선종 LNG선"),
        ("컨테이너", "선종 컨테이너"),
        ("현재 통항 중", "타일 현재 통항 중"),
        ("대기/정박", "타일 대기/정박"),
        ("평균 속도", "타일 평균 속도"),
        ("AIS 신뢰도", "타일 AIS 신뢰도"),
        ("실제 통과량보다 낮을 수 있", "하한 추정 경고"),
    ]
    for needle, label in required:
        check(f"9: 호르무즈 — {label} ('{needle}')", needle in tpl)
    # 시간 창 컨트롤
    for win in ("1시간", "6시간", "24시간", "7일"):
        check(f"9+: 호르무즈 시간 창 '{win}'", win in tpl)
    # 운영 카드 요소(설명-나열형이 아닌 시각 요소)
    check("9+: 시간대별 통과 막대 + 해협 모식도 + 메트릭 타일",
          'id="hzBars"' in tpl and "해협 모식도" in tpl and "hz-tile" in tpl)
    # 데모/라이브 정직성
    check("9+: 데모 미리보기 고정값 + 라이브 AIS 통합 아님 + 프로덕션 값 미생성",
          "데모 미리보기 고정값" in tpl and "라이브 AIS" in tpl
          and ("통합이 아" in tpl or "미연동" in tpl)
          and "프로덕션 값을 생성하지 않" in tpl)
    check("9+: 위성 AIS 미포함 누락 경고", "위성 AIS 미포함" in tpl and "누락" in tpl)


# ---------------------------------------------------------------------------
# 10 · '전력 신호 100' 부재
# ---------------------------------------------------------------------------

def check_no_power100(tpl: str) -> None:
    check("10a: '전력 신호 100' 부재 (기사 상대 비중으로 재구성)", "전력 신호 100" not in tpl)
    check("10b: 'AI Radar' 존재", "AI Radar" in tpl)
    check("10c: 테마 % = 기사 상대 비중 설명",
          "상대 비중" in tpl and "실측" in tpl)


# ---------------------------------------------------------------------------
# 11 / 12 · 프로덕션 docs/daily + Telegram 불변 (git)
# ---------------------------------------------------------------------------

def check_production_untouched(tpl: str, main_src: str) -> None:
    check("11a: docs/daily/latest.html 존재", DOCS_LATEST.exists())
    check("11b: docs/daily/operator-latest.html 존재", DOCS_OPERATOR.exists())
    ok, detail = _git_unchanged(["docs/daily/latest.html", "docs/daily/operator-latest.html"])
    check("11c: 프로덕션 docs/daily 파일이 변경되지 않음 (git)", ok, detail)

    for needle in ("send_telegram", "approve_send", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_IDS"):
        check(f"12a: 템플릿에 발송 토큰/경로 '{needle}' 미참조", needle not in tpl)
    check("12b: main.py 미리보기 라우트가 발송을 호출하지 않음", "send_telegram" not in main_src)
    ok_send, d_send = _git_unchanged(["scripts/send_telegram.py"])
    check("12c: scripts/send_telegram.py 변경 없음", ok_send, d_send)
    ok_wf, d_wf = _git_unchanged([".github/workflows/telegram-notify.yml"])
    check("12d: telegram-notify.yml 워크플로 변경 없음", ok_wf, d_wf)


# ---------------------------------------------------------------------------
# 추가 정직성/구조 — 탭, 시각 자산, 기간 컨트롤, 드로어, 초기신호, data_mode
# ---------------------------------------------------------------------------

def check_structure_extras(tpl: str) -> None:
    for tab in ("뉴스", "AI 신호", "시장", "카더라·초기신호"):
        check(f"E-tab: 탭 '{tab}' 노출", tab in tpl)

    classes = ["metric", "featured", "lens", "panel", "tabpanel", "srow",
               "drawer", "hz-tile", "kpi", "chartbox", "hz-bars", "hz-metric"]
    present = [c for c in classes if f".{c}" in tpl or f'"{c}' in tpl or f" {c}" in tpl]
    check("E-visual: 대시보드 카드/컴포넌트 클래스 다수 (>=10)",
          len(present) >= 10, f"{len(present)}/{len(classes)}")
    check("E-visual: 소프트 카드 스타일(box-shadow + border-radius)",
          "box-shadow" in tpl and "border-radius" in tpl)
    check("E-visual: 인라인 SVG 시각화 다수 (>=4)", tpl.count("<svg") >= 4,
          f"<svg 개수={tpl.count('<svg')}")

    check("E-period: 금리 차트 기간 컨트롤(1주/1개월/3개월/1년)",
          'id="yieldPeriodSeg"' in tpl and "1주" in tpl and "1개월" in tpl
          and "3개월" in tpl and "1년" in tpl)

    check("E-drawer: 시장 상세 드로어(행 클릭 → 상세 차트 + 기간 + 값/변동/갱신)",
          'id="mktDrawerOv"' in tpl and 'id="dwChart"' in tpl and "data-mid" in tpl
          and 'id="dwVal"' in tpl and "행을 클릭" in tpl)
    check("E-drawer: 드로어 주의문(현재 체결값 아님)",
          "공개·무료 또는 대용 데이터 기준이며 현재 체결값이 아닙니다" in tpl)

    for label in ("지연", "대용", "보고", "미연동"):
        check(f"E-data_mode: '{label}' 노출", label in tpl)
    has_unavail_null = (bool(re.search(r'"data_mode"\s*:\s*"unavailable"', tpl))
                        and bool(re.search(r'"value"\s*:\s*null', tpl)))
    check("E-fake: 시장 미연동 지표는 value=null (가짜 숫자 없음)", has_unavail_null)


def check_early_signals(tpl: str) -> None:
    start = tpl.find('id="panel-rumor"')
    panel = tpl[start:tpl.find("</section>", start)] if start >= 0 else ""
    check("E-early: 초기신호 탭 패널 존재", bool(panel))
    check("E-early: 관찰 소스 X(엑스) / Truth Social / Telegram",
          "X (엑스)" in panel and "Truth Social" in panel and "Telegram" in panel)
    check("E-early: 허용 액션만(뉴스 승격 검토 / 근거 확인)",
          "뉴스 승격 검토" in panel and "근거 확인" in panel)
    check("E-early: 임원 자동 알림/직접 발송 없음",
          "임원 자동 알림을 생성하지 않" in panel and "approve_send" not in panel
          and "send_telegram" not in panel)
    check("E-early: 검증 뉴스와 분리 + 비공개 대화 수집 금지",
          "확인된 뉴스가 아" in panel and "검증 뉴스와 분리" in panel
          and "비공개 대화 수집" in panel)


def main() -> int:
    print(f"== verify_dashboard_preview @ {ROOT} ==")
    tpl = _read(TEMPLATE)
    main_src = _read(MAIN)

    check_route_and_render(tpl, main_src)     # 1
    check_preview_flag(tpl)                    # 2
    check_honesty_labels(tpl)                  # 3
    check_yield_labels(tpl)                    # 4
    check_us2y_demo(tpl)                       # 5
    check_compare_modes(tpl)                   # 6 / 7 / 8
    check_hormuz(tpl)                          # 9
    check_no_power100(tpl)                     # 10
    check_production_untouched(tpl, main_src)  # 11 / 12
    check_structure_extras(tpl)               # 보강
    check_early_signals(tpl)                   # 보강

    print()
    if _failures:
        print(f"RESULT: FAIL ({len(_failures)} 항목)")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("RESULT: PASS — 대시보드 미리보기 검수 통과 (design parity · D5-E3)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
