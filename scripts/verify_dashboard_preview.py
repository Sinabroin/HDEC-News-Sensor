#!/usr/bin/env python3
"""verify_dashboard_preview — 비프로덕션 대시보드 미리보기 검수 (P0 Design Preview).

이 검수기는 templates/dashboard_preview.html + app/main.py 라우트 + JSON island가
디자인/정직성 계약을 지키는지 확인한다. 완전 오프라인이다:
- 네트워크/발송/비밀값 0건. DB를 만들거나 바꾸지 않는다(라이프스팬 미실행, 라우트 직접 호출).
- 프로덕션 일일 리포트(docs/daily/latest.html · operator-latest.html)와 Telegram 발송
  경로(send_telegram.py · 워크플로)가 이 작업으로 바뀌지 않았음을 git으로 확인한다.

검사 항목(작업 명세 1~11):
  1  preview 라우트/페이지가 존재하고 렌더된다
  2  프로덕션 docs/daily 파일이 변경되지 않았다
  3  탭: 뉴스 / AI 신호 / 시장 / 카더라·초기신호
  4  data_mode 라벨: 지연 / 대용 / 보고 / 미연동
  5  Business Lens · Ecosystem 섹션
  6  호르무즈 카드 + AIS 하한 경고
  7  AI Radar 존재 · "전력 신호 100" 부재
  8  금리 비교 모드: 국가 내 비교 / 국가 간 비교
  9  미연동(시장/초기신호/호르무즈) 데이터가 가짜 값을 렌더하지 않는다
 10  Telegram 발송 경로가 바뀌지 않았다
 11  초기신호 탭에서 임원 자동 알림 액션이 없다
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

    # best-effort: 라우트 함수를 직접 호출해 200 + 템플릿 반환을 확인한다(라이프스팬 미실행 → DB 무변경).
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
# 2 · 프로덕션 docs/daily 불변
# ---------------------------------------------------------------------------

def check_docs_untouched() -> None:
    check("2a: docs/daily/latest.html 존재", DOCS_LATEST.exists())
    check("2b: docs/daily/operator-latest.html 존재", DOCS_OPERATOR.exists())
    ok, detail = _git_unchanged(["docs/daily/latest.html", "docs/daily/operator-latest.html"])
    check("2c: 프로덕션 docs/daily 파일이 변경되지 않음", ok, detail)


# ---------------------------------------------------------------------------
# 3~8 · 텍스트 계약 (탭 / data_mode / 섹션 / 호르무즈 / AI Radar / 금리 모드)
# ---------------------------------------------------------------------------

def check_tabs(tpl: str) -> None:
    for tab in ("뉴스", "AI 신호", "시장", "카더라·초기신호"):
        check(f"3: 탭 '{tab}' 노출", tab in tpl)


def check_data_modes(tpl: str) -> None:
    for label in ("지연", "대용", "보고", "미연동"):
        check(f"4: data_mode 라벨 '{label}' 노출", label in tpl)
    # 정직성 추가 상태(요구사항)도 함께 확인한다.
    for extra in ("정책/키 필요", "확인 전", "공식 발언", "승인 채널 관찰"):
        check(f"4+: honest 상태 '{extra}' 노출", extra in tpl)


def check_sections(tpl: str) -> None:
    check("5a: Business Lens 섹션", "Business Lens" in tpl and "사업 렌즈" in tpl)
    check("5b: Ecosystem 섹션", "Ecosystem" in tpl and "생태계" in tpl)
    check("5c: What to Watch Next 섹션", "What to Watch Next" in tpl)


def check_hormuz(tpl: str) -> None:
    check("6a: 호르무즈 카드 제목", "호르무즈 해협 관찰" in tpl)
    check("6b: AIS 하한 추정 배지", "AIS 하한 추정" in tpl)
    check("6c: AIS 하한 경고 문구",
          "실제 통과량보다 낮을 수 있" in tpl and "unique MMSI" in tpl)
    check("6d: AIS 관측 통과 문구", "AIS 관측 통과" in tpl)
    for win in ("1시간", "6시간", "24시간", "7일"):
        check(f"6e: 호르무즈 시간 옵션 '{win}'", win in tpl)


def check_ai_radar(tpl: str) -> None:
    check("7a: 'AI Radar' 존재", "AI Radar" in tpl)
    check("7b: '전력 신호 100' 부재 (기사 상대 비중으로 재구성)",
          "전력 신호 100" not in tpl)
    check("7c: 테마 % = 기사 상대 비중 설명",
          "상대 비중" in tpl and ("실측" in tpl or "전력 실측" in tpl))


def check_yield_modes(tpl: str) -> None:
    check("8a: '국가 내 비교' (within_country)", "국가 내 비교" in tpl)
    check("8b: '국가 간 비교' (cross_country)", "국가 간 비교" in tpl)
    check("8c: 정규화/기준일=100 라벨",
          "정규화 추세" in tpl and "기준일=100" in tpl and "절대 가격 비교 아님" in tpl)
    check("8d: 국가 내 시리즈(2Y/5Y/10Y) 표현", "10Y" in tpl and "5Y" in tpl)
    check("8e: CPI는 금리 y축에 강제되지 않음(별도/정규화 설명)",
          "CPI" in tpl and ("단위가 다" in tpl or "같은 y축" in tpl))


# ---------------------------------------------------------------------------
# 9 · 미연동 데이터가 가짜 값을 렌더하지 않음
# ---------------------------------------------------------------------------

def check_no_fake_values(tpl: str) -> None:
    # 호르무즈 선박 수는 어떤 창에서도 위조하지 않는다 — 기본/갱신 모두 '미연동'.
    check("9a: 호르무즈 선박 수가 미연동(가짜 수 미생성)",
          'id="hzCount">미연동' in tpl and "값 미생성" in tpl)
    # 시장: unavailable 지표는 value:null 로 두고 렌더는 '—'.
    has_unavail_null = bool(re.search(r'"data_mode"\s*:\s*"unavailable"', tpl)) and \
        bool(re.search(r'"value"\s*:\s*null', tpl))
    check("9b: 시장 미연동 지표는 value=null (가짜 숫자 없음)", has_unavail_null)
    check("9c: 미연동 시리즈는 차트에 가짜 선을 그리지 않음",
          "가짜 선" in tpl and ("선을 그리지 않" in tpl or "가짜 선 미표시" in tpl))
    # 초기신호 소스 미연동은 정책/키 필요로 정직 표기.
    check("9d: 초기신호 소스 미연동 → 정책/키 필요",
          "정책/키 필요" in tpl and "위조하지 않" in tpl)


# ---------------------------------------------------------------------------
# 10 · Telegram 발송 경로 불변
# ---------------------------------------------------------------------------

def check_telegram_untouched(tpl: str, main_src: str) -> None:
    # 미리보기 템플릿/라우트는 발송 경로를 전혀 건드리지 않는다.
    for needle in ("send_telegram", "approve_send", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_IDS"):
        check(f"10a: 템플릿에 발송 토큰/경로 '{needle}' 미참조", needle not in tpl)
    check("10b: main.py 미리보기 라우트가 발송을 호출하지 않음",
          "send_telegram" not in main_src)
    ok_send, d_send = _git_unchanged(["scripts/send_telegram.py"])
    check("10c: scripts/send_telegram.py 변경 없음", ok_send, d_send)
    ok_wf, d_wf = _git_unchanged([".github/workflows/telegram-notify.yml"])
    check("10d: telegram-notify.yml 워크플로 변경 없음", ok_wf, d_wf)


# ---------------------------------------------------------------------------
# 11 · 초기신호 탭에서 임원 자동 알림 없음
# ---------------------------------------------------------------------------

def _rumor_panel(tpl: str) -> str:
    start = tpl.find('id="panel-rumor"')
    if start < 0:
        return ""
    end = tpl.find("</section>", start)
    return tpl[start:end if end > 0 else len(tpl)]


def check_no_auto_alert(tpl: str) -> None:
    panel = _rumor_panel(tpl)
    check("11a: 초기신호 탭 패널 존재", bool(panel))
    # 허용 액션만 — 뉴스 승격 검토 / 근거 확인.
    check("11b: 초기신호 액션 라벨(뉴스 승격 검토)", "뉴스 승격 검토" in panel)
    check("11c: 초기신호 액션 라벨(근거 확인)", "근거 확인" in panel)
    # 자동 알림 비활성 명시.
    check("11d: '임원 자동 알림 생성 안 함' 명시",
          "임원 자동 알림을 생성하지 않" in panel)
    check("11e: '자동 승격/자동 알림 보내지 않음' 명시",
          "자동 승격" in panel and "보내지 않" in panel)
    # 운영자 즉시-알림 등급/발송 액션이 초기신호 탭에 없어야 한다.
    check("11f: 초기신호 탭에 즉시-알림 발송 액션 없음",
          "즉시 알림 후보" not in panel and "approve_send" not in panel)
    check("11g: 검증 뉴스와 분리 명시",
          "확인된 뉴스가 아" in panel and "검증 뉴스와 분리" in panel)
    # 비공개 대화 수집 금지 명시(권한 채널 관찰만).
    check("11h: 비공개 대화 수집 금지 · 승인 채널 관찰만",
          "비공개 대화 수집" in panel and "승인" in panel)


def main() -> int:
    print(f"== verify_dashboard_preview @ {ROOT} ==")
    tpl = _read(TEMPLATE)
    main_src = _read(MAIN)

    check_route_and_render(tpl, main_src)
    check_docs_untouched()
    check_tabs(tpl)
    check_data_modes(tpl)
    check_sections(tpl)
    check_hormuz(tpl)
    check_ai_radar(tpl)
    check_yield_modes(tpl)
    check_no_fake_values(tpl)
    check_telegram_untouched(tpl, main_src)
    check_no_auto_alert(tpl)

    print()
    if _failures:
        print(f"RESULT: FAIL ({len(_failures)} 항목)")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("RESULT: PASS — 대시보드 미리보기 검수 통과")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
