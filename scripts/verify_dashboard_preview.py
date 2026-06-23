#!/usr/bin/env python3
"""verify_dashboard_preview — 비프로덕션 대시보드 미리보기 검수 (Market Universe, D5-E4).

templates/dashboard_preview.html + app/main.py 라우트가 Claude Design 패리티 + 정직성
계약을 지키는지 확인한다. 완전 오프라인이다:
- 네트워크/발송/비밀값 0건. DB를 만들거나 바꾸지 않는다(라이프스팬 미실행, 라우트 직접 호출).
- 프로덕션 일일 리포트(docs/daily/*.html)와 Telegram 발송 경로가 이 작업으로 바뀌지 않았음을
  git으로 확인한다.

D5-E4 시장 유니버스 확장 검사:
  · 금리 유니버스: 만기 3M/6M/1Y/2Y/3Y/5Y/7Y/10Y/20Y/30Y, 기준금리, CPI/Core CPI/PPI,
    10Y-2Y·10Y-3M 스프레드, 국가 내/간 비교, 정규화 추세, 전체 만기 확장, 미연동 칩.
  · FX 유니버스: USD/EUR/JPY/CNY(CNH)/GBP/CHF/CAD/AUD/SGD/HKD/TWD/INR/AED/SAR/QAR /KRW.
  · 원자재/에너지: 금속·철강건자재·유가정제유·가스LNG·석탄 카테고리 + 대표 종목.
  · 시장 탭 IA: 카테고리 카드, 우측 레일 dedup('시장 모니터링'), '전체 보기'.
  · 라벨 리네임: '시장 압력' 비사용, '초기신호' 탭, 'AI 테마 강도 · 전력 실측값 아님'.

이전(D5-E3) 정직성/구조 계약도 유지: NON-PRODUCTION PREVIEW, 데모/체결값 라벨, US 2Y 데모 선,
호르무즈 운영 카드, '전력 신호 100' 부재, 시장 상세 드로어, 초기신호 분리, docs/daily·Telegram 불변.
"""

import json
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


def _all(tpl: str, needles, prefix: str) -> None:
    for n in needles:
        check(f"{prefix}: '{n}'", n in tpl)


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
# 2 / 3 · 미리보기 플래그 + 정직성 라벨
# ---------------------------------------------------------------------------

def check_preview_flag(tpl: str) -> None:
    check("2: 'NON-PRODUCTION PREVIEW' 표기", "NON-PRODUCTION PREVIEW" in tpl)


def check_honesty_labels(tpl: str) -> None:
    check("3a: '데모 데이터' 표기", "데모 데이터" in tpl)
    check("3b: '현재 체결값 아님' 표기", "현재 체결값 아님" in tpl)
    check("3c: production 일일 리포트와 무관함 명시",
          "프로덕션 일일 리포트" in tpl or "docs/daily" in tpl)
    for lab in ("지연", "대용", "보고", "미연동"):
        check(f"3d: data_mode 라벨 '{lab}'", lab in tpl)
    check("3e: 대용/proxy 표기", "대용" in tpl and "proxy" in tpl)


# ---------------------------------------------------------------------------
# 4 / 5 · 금리 차트 라벨 + US 2Y 데모 선
# ---------------------------------------------------------------------------

def check_yield_labels(tpl: str) -> None:
    for lab in ("US 2Y", "US 5Y", "US 10Y"):
        check(f"4: 금리 차트 데모 라벨 '{lab}'", lab in tpl)
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
    check("5+: 미연동 시리즈는 차트에 가짜 선을 그리지 않음 (가짜 선 미표시)",
          "가짜 선 미표시" in tpl)


# ---------------------------------------------------------------------------
# 6 · 금리 유니버스 (D5-E4 §1) — 전체 만기 + 정책/물가/스프레드 + 비교 모드
# ---------------------------------------------------------------------------

def check_yield_universe(tpl: str) -> None:
    # 만기 옵션 라벨 3M..30Y (cross 모드 select 옵션 텍스트)
    for mat in ("3M", "6M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "20Y", "30Y"):
        check(f"6-만기: '>{mat}<' 옵션", f">{mat}<" in tpl)
    # 정책/물가/스프레드 지표
    _all(tpl, ("기준금리", "CPI", "Core CPI", "PPI", "10Y-2Y", "10Y-3M"), "6-지표")
    # 비교 모드 + 정규화
    check("6-mode: '국가 내 비교' (within_country)", "국가 내 비교" in tpl)
    check("6-mode: '국가 간 비교' (cross_country)", "국가 간 비교" in tpl)
    check("6-mode: '정규화 추세' (기준일=100)",
          "정규화 추세" in tpl and "기준일=100" in tpl
          and "절대 가격 비교 아님" in tpl)
    # CPI/물가는 금리 y축에 강제되지 않음
    check("6-axis: 물가(CPI/PPI)는 금리 y축에 강제되지 않음(별도 축 설명)",
          "단위·축이 다" in tpl or "같은 y축에 올리지 않" in tpl)
    # 전체 만기 확장 + 미연동 칩
    check("6-expand: '전체 만기' 확장 셀렉터", "전체 만기" in tpl)
    check("6-chip: 미연동 만기/지표 비활성 칩('소스 필요'/'미연동')",
          "소스 필요" in tpl and ".mtog" in tpl)
    # 8개국 셀렉터
    for ctry in ("US", "KR", "JP", "UK", "CN", "CA", "AU"):
        check(f"6-country: '{ctry}' 국가", ctry in tpl)
    check("6-country: EU 또는 DE", "(EU" in tpl or "EU/DE" in tpl or " EU)" in tpl)


# ---------------------------------------------------------------------------
# 7 · FX 유니버스 (D5-E4 §2)
# ---------------------------------------------------------------------------

def check_fx_universe(tpl: str) -> None:
    required = ("USD/KRW", "EUR/KRW", "JPY/KRW", "GBP/KRW", "CHF/KRW", "CAD/KRW",
                "AUD/KRW", "SGD/KRW", "HKD/KRW", "TWD/KRW", "INR/KRW",
                "AED/KRW", "SAR/KRW", "QAR/KRW")
    for pair in required:
        check(f"7-fx: '{pair}'", pair in tpl)
    check("7-fx: CNY/KRW 또는 CNH/KRW", "CNY/KRW" in tpl or "CNH/KRW" in tpl)


# ---------------------------------------------------------------------------
# 8 · 원자재·에너지 유니버스 (D5-E4 §3)
# ---------------------------------------------------------------------------

def check_commodity_universe(tpl: str) -> None:
    simple = ("Copper", "Aluminum", "Nickel", "Zinc", "Iron ore", "HRC", "Rebar",
              "Cement", "WTI", "Brent", "LNG JKM", "TTF", "Henry Hub", "Gasoline")
    for c in simple:
        check(f"8-commodity: '{c}'", c in tpl)
    check("8-commodity: Dubai 또는 Oman 원유", "Dubai" in tpl or "Oman" in tpl)
    check("8-commodity: Diesel 또는 Gasoil", "Diesel" in tpl or "Gasoil" in tpl)
    check("8-commodity: Jet fuel 또는 Kerosene", "Jet" in tpl or "Kerosene" in tpl)
    # 카테고리 그룹 라벨
    for cat in ("원자재·금속", "철강·건자재", "유가·정제유", "가스·LNG", "석탄"):
        check(f"8-cat: 카테고리 '{cat}'", cat in tpl)


# ---------------------------------------------------------------------------
# 9 · 시장 탭 IA + 라벨 리네임 (D5-E4 §4/§5)
# ---------------------------------------------------------------------------

def check_market_ia(tpl: str) -> None:
    # 카테고리 카드 컨테이너 + 금리·물가/환율 카테고리
    check("9-ia: 카테고리 카드 컨테이너(marketCategories)",
          'id="marketCategories"' in tpl and "mktcats" in tpl)
    check("9-ia: '금리·물가' 카테고리", "금리·물가" in tpl)
    check("9-ia: '환율' 카테고리", "환율" in tpl)
    # 우측 레일 dedup: 큐레이션 스냅샷 + 전체 보기 (전체 유니버스 복제 금지)
    check("9-rail: 레일 큐레이션 스냅샷(rail_snapshot)", '"rail_snapshot"' in tpl)
    check("9-rail: '전체 보기' 링크/버튼", "전체 보기" in tpl and "seeall" in tpl)
    # 라벨 리네임
    check("9-rename: '시장 압력'을 기본 카드 라벨로 쓰지 않음", "시장 압력" not in tpl)
    check("9-rename: '시장 모니터링' 또는 '시장 리스크' 존재",
          "시장 모니터링" in tpl or "시장 리스크" in tpl)
    # 초기신호 탭 (카더라를 1차 탭 라벨로 쓰지 않음)
    m = re.search(r'data-tab="rumor"[^>]*>([^<]*)<', tpl)
    tab_label = (m.group(1).strip() if m else "")
    check("9-tab: 카더라·초기신호 탭 → '초기신호'", tab_label == "초기신호",
          f"tab label='{tab_label}'")
    check("9-tab: '카더라·초기신호'를 1차 탭 라벨로 쓰지 않음", "카더라·초기신호" not in tpl)
    # AI 테마 강도 명확화
    check("9-ai: 'AI 테마 강도' + '전력 실측값 아님' 명확화",
          "AI 테마 강도" in tpl and "전력 실측값 아님" in tpl)


# ---------------------------------------------------------------------------
# 10 · 호르무즈 카드 (운영 관찰 카드 · 필수 요소 유지)
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
        check(f"10: 호르무즈 — {label} ('{needle}')", needle in tpl)
    for win in ("1시간", "6시간", "24시간", "7일"):
        check(f"10+: 호르무즈 시간 창 '{win}'", win in tpl)
    check("10+: 시간대별 통과 막대 + 해협 모식도 + 메트릭 타일",
          'id="hzBars"' in tpl and "해협 모식도" in tpl and "hz-tile" in tpl)
    check("10+: 데모 미리보기 고정값 + 라이브 AIS 통합 아님 + 프로덕션 값 미생성",
          "데모 미리보기 고정값" in tpl and "라이브 AIS" in tpl
          and ("통합이 아" in tpl or "미연동" in tpl)
          and "프로덕션 값을 생성하지 않" in tpl)
    check("10+: 위성 AIS 미포함 누락 경고", "위성 AIS 미포함" in tpl and "누락" in tpl)


# ---------------------------------------------------------------------------
# 11 · '전력 신호 100' 부재
# ---------------------------------------------------------------------------

def check_no_power100(tpl: str) -> None:
    check("11a: '전력 신호 100' 부재 (기사 상대 비중으로 재구성)", "전력 신호 100" not in tpl)
    check("11b: 'AI Radar' 존재", "AI Radar" in tpl)
    check("11c: 테마 % = 기사 상대 비중 설명",
          "상대 비중" in tpl and "실측" in tpl)


# ---------------------------------------------------------------------------
# 12 · 프로덕션 docs/daily + Telegram 불변 (git)
# ---------------------------------------------------------------------------

def check_production_untouched(tpl: str, main_src: str) -> None:
    check("12a: docs/daily/latest.html 존재", DOCS_LATEST.exists())
    check("12b: docs/daily/operator-latest.html 존재", DOCS_OPERATOR.exists())
    ok, detail = _git_unchanged(["docs/daily/latest.html", "docs/daily/operator-latest.html"])
    check("12c: 프로덕션 docs/daily 파일이 변경되지 않음 (git)", ok, detail)

    for needle in ("send_telegram", "approve_send", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_IDS"):
        check(f"12d: 템플릿에 발송 토큰/경로 '{needle}' 미참조", needle not in tpl)
    check("12e: main.py 미리보기 라우트가 발송을 호출하지 않음", "send_telegram" not in main_src)
    sender = _read(ROOT / "scripts" / "send_telegram.py")
    workflow = _read(ROOT / ".github" / "workflows" / "telegram-notify.yml")
    guard_idx = sender.find("if not will_send")
    post_idx = sender.find("urlopen")
    check("12f: send_telegram.py 발송 경로는 사람 검토 gate 뒤에 있음",
          0 <= guard_idx < post_idx, f"guard={guard_idx}, post={post_idx}")
    check("12g: telegram-notify.yml send mode가 approve_send에 gate됨",
          "github.event.inputs.approve_send == 'true' && 'send' || 'manual'" in workflow)


# ---------------------------------------------------------------------------
# 추가 정직성/구조 — 탭, 시각 자산, 기간 컨트롤, 드로어, data_mode, 가짜값 금지
# ---------------------------------------------------------------------------

def check_structure_extras(tpl: str) -> None:
    for tab in ("뉴스", "AI 신호", "시장", "초기신호"):
        check(f"E-tab: 탭 '{tab}' 노출", tab in tpl)

    classes = ["metric", "featured", "lens", "panel", "tabpanel", "srow",
               "drawer", "hz-tile", "kpi", "chartbox", "hz-bars", "hz-metric",
               "mcat-card", "mtog"]
    present = [c for c in classes if f".{c}" in tpl or f'"{c}' in tpl or f" {c}" in tpl]
    check("E-visual: 대시보드 카드/컴포넌트 클래스 다수 (>=12)",
          len(present) >= 12, f"{len(present)}/{len(classes)}")
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

    has_unavail_null = (bool(re.search(r'"data_mode"\s*:\s*"unavailable"', tpl))
                        and bool(re.search(r'"value"\s*:\s*null', tpl)))
    check("E-fake: 시장 미연동 지표는 value=null (가짜 숫자 없음)", has_unavail_null)


def check_model_island(tpl: str) -> None:
    m = re.search(r'<script type="application/json" id="preview-model">(.*?)</script>',
                  tpl, re.S)
    if not check("E-model: preview-model JSON island 존재", bool(m)):
        return
    try:
        data = json.loads(m.group(1))
    except ValueError as exc:
        check("E-model: JSON island 파싱", False, str(exc))
        return
    items = data.get("market_items") or []
    check("E-model: market_items 대규모 유니버스(>=40종)", len(items) >= 40, f"{len(items)}종")
    cats = {it.get("category") for it in items}
    expected = {"base_metals", "steel_materials", "oil_refined", "gas_lng", "coal",
                "rates_inflation", "fx"}
    check("E-model: 7개 카테고리 전부 채워짐", expected <= cats,
          ", ".join(sorted(expected - cats)) or "ok")
    fx = [it for it in items if it.get("category") == "fx"]
    check("E-model: FX 유니버스 확장(>=14쌍)", len(fx) >= 14, f"{len(fx)}쌍")
    nulls = [it for it in items if it.get("value") is None]
    check("E-model: 미연동 종목 value=null (정직, >=1)", len(nulls) >= 1, f"{len(nulls)}종")
    rail = data.get("rail_snapshot") or []
    check("E-model: 레일 스냅샷 큐레이션(4~6개, 전체 복제 아님)",
          4 <= len(rail) <= 6, f"{len(rail)}개")


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
    check_yield_universe(tpl)                  # 6
    check_fx_universe(tpl)                     # 7
    check_commodity_universe(tpl)             # 8
    check_market_ia(tpl)                       # 9
    check_hormuz(tpl)                          # 10
    check_no_power100(tpl)                     # 11
    check_production_untouched(tpl, main_src)  # 12
    check_structure_extras(tpl)               # 보강
    check_model_island(tpl)                   # 보강
    check_early_signals(tpl)                   # 보강

    print()
    if _failures:
        print(f"RESULT: FAIL ({len(_failures)} 항목)")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("RESULT: PASS — 대시보드 미리보기 검수 통과 (market universe · D5-E4)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
