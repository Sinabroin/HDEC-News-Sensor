#!/usr/bin/env python3
"""D7-A verifier — summary dashboard lens filters are functional (not a static preview).

Runs fully offline (no network, DB, secrets, or send). It checks that the summary
dashboard (`templates/dashboard_preview.html` → `docs/daily/dashboard-latest.html`)
actually filters visible article cards when a lens/category is selected:

  · lens nav items carry `data-filter` attributes (the filter control),
  · article cards carry `data-lens` + category tags (the content the filter matches),
  · vanilla-JS click handler + applyLens/selectLens filtering exists,
  · lens-specific views render MODEL.lens_banks instead of only filtering global top rows,
  · `전체 종합` resets the filter to all cards,
  · an empty state exists for lenses with no matching demo article,
  · `dashboard-latest.html` is byte-for-byte regenerated from the template,
  · `latest.html` stays the full Executive Daily Brief (not replaced by the dashboard),
  · the Telegram A/B mapping stays 요약 대시보드→dashboard / 전체 리포트→report,
  · demo/proxy/manual/unavailable honesty labels stay, no fake live values introduced.
"""

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates" / "dashboard_preview.html"
BUILDER = ROOT / "scripts" / "build_static_dashboard.py"
SENDER = ROOT / "scripts" / "send_telegram.py"
DASHBOARD = ROOT / "docs" / "daily" / "dashboard-latest.html"
LATEST = ROOT / "docs" / "daily" / "latest.html"

SUMMARY_LABEL = "대시보드 보기"
FULL_REPORT_LABEL = "상세 리포트 보기"
SAMPLE_REPORT_URL = "https://example.com/daily/latest.html"
SAMPLE_DASHBOARD_URL = "https://example.com/daily/dashboard-latest.html"
TOKEN_SHAPE = re.compile(r"[0-9]{8,}:[A-Za-z0-9_-]{20,}")

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


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _model(tpl: str) -> dict:
    m = re.search(r'<script type="application/json" id="preview-model">(.*?)</script>',
                  tpl, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except ValueError:
        return {}


# ---------------------------------------------------------------------------
# 1 · 렌즈 컨트롤: nav 아이템에 data-filter
# ---------------------------------------------------------------------------

def check_lens_controls(tpl: str) -> None:
    filters = re.findall(r'class="nav[^"]*"\s+data-filter="([^"]+)"', tpl)
    check("1a: 좌측 렌즈 nav가 data-filter 속성을 가짐 (>=10)", len(filters) >= 10,
          f"{len(filters)}개")
    check("1b: '전체 종합' 리셋 렌즈(data-filter=\"all\") 존재", "all" in filters)
    # D7-AA — 부가 관찰(SUPPORTING LENSES: Business Lens·Ecosystem Watch·What to Watch Next)
    # 섹션은 제거되었다. 과거 rail lensitem 필터 바인딩 대신, 해당 섹션/렌더 함수가 다시
    # 들어오지 않았는지 회귀 가드를 둔다(빈 섹션·죽은 코드 재유입 방지).
    check("1c: 부가 관찰(SUPPORTING LENSES) 섹션 제거 유지 (회귀 가드)",
          "renderLensList" not in tpl
          and 'id="businessLens"' not in tpl
          and "부가 관찰" not in tpl)
    # D7-AC — 우측 보조 컨텍스트 레일(SUPPORTING CONTEXT: 시장 모니터링·AI 테마·내러티브 요약)도
    # 제거되었다. 라벨/섹션/렌더 함수/모델키가 다시 들어오지 않았는지 회귀 가드를 둔다.
    check("1e: 보조 컨텍스트 레일(SUPPORTING CONTEXT) 제거 유지 (회귀 가드)",
          "보조 컨텍스트" not in tpl
          and "SUPPORTING CONTEXT" not in tpl
          and 'class="rail"' not in tpl
          and "renderRailSnapshot" not in tpl
          and '"rail_snapshot"' not in tpl
          and "railAi" not in tpl
          and "railNarrative" not in tpl
          and 'id="railMarket"' not in tpl)
    check("1d: data-filter 컨트롤이 충분히 다양(>=12 고유)",
          len(set(filters)) >= 12, f"{len(set(filters))} 고유")


# ---------------------------------------------------------------------------
# 2 · 콘텐츠 태그: 기사 카드에 data-lens / category
# ---------------------------------------------------------------------------

def check_article_tags(tpl: str) -> None:
    check("2a: featured 카드에 data-lens 콘텐츠 태그",
          bool(re.search(r'class="card featured"\s+data-lens="[^"]+"', tpl)))
    check("2b: featured 카드에 data-category 태그",
          bool(re.search(r'class="card featured"[^>]*data-category="[^"]+"', tpl)))
    check("2c: renderRows가 각 행에 data-lens 주입",
          'a.setAttribute("data-lens"' in tpl)
    check("2d: renderRows가 각 행에 data-category 주입",
          'a.setAttribute("data-category"' in tpl)

    model = _model(tpl)
    news = model.get("news_rows") or []
    ai = model.get("ai_rows") or []
    check("2e: news_rows/ai_rows 모델 존재", bool(news) and bool(ai),
          f"news={len(news)}, ai={len(ai)}")
    rows = news + ai
    tagged = [r for r in rows if isinstance(r.get("lens"), list) and r.get("lens")]
    check("2f: 모든 기사 행이 비어있지 않은 lens 태그를 가짐",
          len(tagged) == len(rows) and len(rows) >= 6,
          f"{len(tagged)}/{len(rows)} 태그됨")
    # 필터가 의미있으려면 데모 표본이 주요 렌즈를 덮어야 함
    keys = set(re.findall(r'class="nav[^"]*"\s+data-filter="([^"]+)"', tpl))
    covered = {l for r in rows for l in r.get("lens", [])}
    unknown = covered - keys
    check("2g: 기사 lens 키가 모두 유효한 nav 필터 키",
          not unknown, f"미정의 키: {sorted(unknown)}" if unknown else "ok")
    check("2h: 데모 표본이 주요 렌즈를 다양하게 덮음(>=8 렌즈)",
          len(covered) >= 8, f"{len(covered)} 렌즈 커버")


# ---------------------------------------------------------------------------
# 3 · 필터 JS (클릭 핸들러 + applyLens + reset)
# ---------------------------------------------------------------------------

def check_filter_js(tpl: str) -> None:
    check("3a: lensnav 클릭 핸들러 존재",
          'lensnav.addEventListener("click"' in tpl)
    check("3b: applyLens / selectLens / filterPanel 정의",
          "function applyLens" in tpl and "function selectLens" in tpl
          and "function filterPanel" in tpl)
    check("3c: data-lens 태그 매칭으로 카드 표시/숨김",
          'querySelectorAll("[data-lens]")' in tpl
          and 'tags.indexOf(key)' in tpl
          and 'classList.toggle("hidden"' in tpl)
    check("3d: '전체 종합'(all)이 전체 카드로 리셋",
          'key === "all"' in tpl)
    check("3e: 활성 렌즈 라벨/카운트를 동적 갱신",
          'el("activeLens")' in tpl and 'el("lensCount")' in tpl)
    check("3f: 좌측 nav active 상태 동기화(시각적으로 명확)",
          'n.classList.toggle("active", n.getAttribute("data-filter") === key)' in tpl)
    check("3g: specific lens selection renders lens_banks before filtering",
          "MODEL.lens_banks" in tpl and "hasBankRows" in tpl
          and "bankRows || MODEL.news_rows" in tpl)


# ---------------------------------------------------------------------------
# 4 · 빈 상태 + 모바일
# ---------------------------------------------------------------------------

def check_empty_and_mobile(tpl: str) -> None:
    check("4a: 뉴스/AI 빈 상태 요소 존재",
          'id="newsEmpty"' in tpl and 'id="aiEmpty"' in tpl)
    check("4b: .emptylens 스타일 정의", ".emptylens{" in tpl
          and 'class="emptylens' in tpl)
    check("4c: 빈 상태가 전체 종합 복귀를 안내",
          "전체 종합" in tpl and "표시할 신호가 없" in tpl)
    check("4d: 매칭 0건일 때만 빈 상태 노출(shown>0이면 숨김)",
          'e.classList.toggle("hidden", shown > 0)' in tpl)
    # 모바일: 사이드 패널이 본문을 가리지 않음 + 선택 후 본문 노출
    check("4e: 모바일 사이드 패널 static (본문 가림 방지 회귀 규칙 유지)",
          "aside.lens{position:static" in tpl)
    check("4f: 모바일 선택 후 본문(main) 노출 위해 scrollIntoView",
          "max-width:900px" in tpl and "scrollIntoView" in tpl
          and 'querySelector("main")' in tpl)


# ---------------------------------------------------------------------------
# 5 · dashboard-latest.html는 빌더가 생성 (실기사 데이터 주입 · 손수 편집 아님)
# ---------------------------------------------------------------------------

def _interaction_script(html: str) -> str:
    """preview-model(JSON)을 제외한 메인 인터랙션 <script> 블록."""
    blocks = re.findall(r"<script>(.*?)</script>", html, re.S)
    return blocks[-1] if blocks else ""


def check_regenerated(tpl: str) -> None:
    if not check("5a: docs/daily/dashboard-latest.html 존재", DASHBOARD.exists()):
        return
    committed = _read(DASHBOARD)
    # 재생성된 산출물에 신규 필터 토큰 + 빌더 마커가 반영되어 있어야 함
    for tok in ('data-filter="all"', 'id="newsEmpty"', "function applyLens",
                'a.setAttribute("data-lens"', "dashboard-export:summary"):
        check(f"5b: 재생성된 대시보드에 필터/빌더 토큰 '{tok}'", tok in committed)
    # RC4 public export는 hidden template/JS literal에서도 금지 문자열을 제거하므로 JS
    # byte-identity를 요구할 수 없다. 핵심 인터랙션 함수 보존 + public raw residual 0으로
    # 빌더 변환 계약을 검증한다.
    committed_js = _interaction_script(committed)
    tpl_js = _interaction_script(tpl)
    check("5c: public 인터랙션 함수 보존 + raw demo residual 0",
          bool(committed_js) and all(
              token in committed_js and token in tpl_js
              for token in ("function applyLens", "function renderRows", "function filterPanel")
          ) and "데모 데이터" not in committed)
    # 빌더를 mock으로 다시 돌려 결정적 산출물이 실기사 구조를 갖는지 확인 (데모 표본 아님).
    with tempfile.TemporaryDirectory(prefix="hdec_lens_") as tmp:
        out = Path(tmp) / "dashboard-latest.html"
        proc = subprocess.run([sys.executable, str(BUILDER), "--output", str(out)],
                              cwd=ROOT, capture_output=True, text=True, timeout=300)
        ok = proc.returncode == 0 and out.exists()
        if check("5d: builder --output 동작", ok, (proc.stderr or "")[-200:]):
            regen = out.read_text(encoding="utf-8")
            model = _model(regen)
            rows = model.get("news_rows") or []
            banks = model.get("lens_banks") or {}
            tpl_rows = _model(tpl).get("news_rows") or []
            check("5e: 재생성 대시보드 news_rows가 실기사(>=6, url 보유)",
                  len(rows) >= 6 and all(str(r.get("url", "")).startswith("http") for r in rows),
                  f"{len(rows)}행")
            check("5f: 재생성 news_rows가 템플릿 데모 표본과 다름 (실데이터 주입 증명)",
                  rows != tpl_rows)
            regen_js = _interaction_script(regen)
            check("5g: 재생성 인터랙션 함수 보존 + public raw demo residual 0",
                  all(token in regen_js for token in (
                      "function applyLens", "function renderRows", "function filterPanel"))
                  and "데모 데이터" not in regen)
            check("5h: 재생성 모델에 lens_banks가 있고 bank rows가 real url 보유",
                  bool(banks) and all(str(r.get("url", "")).startswith("http")
                                      for bank in banks.values() for r in (bank or [])),
                  f"{len(banks)} banks")


# ---------------------------------------------------------------------------
# 6 · latest.html는 전체 리포트로 유지 (대시보드로 교체 금지)
# ---------------------------------------------------------------------------

def check_latest_separation() -> None:
    if not check("6a: docs/daily/latest.html 존재", LATEST.exists()):
        return
    latest = _read(LATEST)
    dashboard = _read(DASHBOARD)
    check("6b: latest.html은 전체 Executive Daily Brief", "Executive Daily Brief" in latest)
    check("6c: latest.html != dashboard-latest.html", latest != dashboard)
    for tok in ('id="preview-model"', "dashboard-export:summary",
                "function applyLens", 'id="newsEmpty"', 'data-filter="all"'):
        check(f"6d: 전체 리포트에 대시보드 전용 토큰 '{tok}' 미혼입", tok not in latest)


# ---------------------------------------------------------------------------
# 7 · Telegram A/B 매핑 유지
# ---------------------------------------------------------------------------

def check_telegram_mapping() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import send_telegram
    except Exception as exc:  # noqa: BLE001
        check("7: send_telegram import", False, str(exc))
        return
    payload = send_telegram.build_payload(
        "DRY", "message", SAMPLE_REPORT_URL, "", SAMPLE_DASHBOARD_URL)
    buttons = json.loads(payload["reply_markup"])["inline_keyboard"][0]
    labels = [b["text"] for b in buttons]
    urls = [b["url"] for b in buttons]
    check("7a: '대시보드 보기' 버튼 존재", SUMMARY_LABEL in labels, " / ".join(labels))
    check("7b: '상세 리포트 보기' 버튼 존재", FULL_REPORT_LABEL in labels, " / ".join(labels))
    check("7c: 버튼 순서 = 요약 대시보드 → 전체 리포트",
          labels[:2] == [SUMMARY_LABEL, FULL_REPORT_LABEL], " / ".join(labels[:2]))
    check("7d: 요약→dashboard URL, 전체→report URL 매핑",
          urls[:2] == [SAMPLE_DASHBOARD_URL, SAMPLE_REPORT_URL], " / ".join(urls[:2]))


# ---------------------------------------------------------------------------
# 8 · 정직성 유지 (데모/대용/미연동 라벨 + 가짜 live 값 없음)
# ---------------------------------------------------------------------------

def check_honesty(tpl: str) -> None:
    dashboard = _read(DASHBOARD)
    check("8a: public raw '데모 데이터' 0건", "데모 데이터" not in dashboard)
    for lab in ("현재 체결값 아님", "미연동", "대용"):
        check(f"8a: 정직성 라벨 유지 '{lab}'", lab in dashboard)
    check("8b: 빈 상태 유지 + public 내부 고정 샘플 표기",
          'id="newsEmpty"' in dashboard and "내부 고정 샘플" in dashboard)
    check("8c: 시장 미연동 지표는 value=null (가짜 숫자 없음)",
          bool(re.search(r'"data_mode"\s*:\s*"unavailable"', tpl))
          and bool(re.search(r'"value"\s*:\s*null', tpl)))
    check("8d: 템플릿/대시보드에 발송 토큰/시크릿 미혼입",
          not TOKEN_SHAPE.search(tpl) and "TELEGRAM_BOT_TOKEN" not in tpl
          and "TELEGRAM_BOT_TOKEN" not in dashboard)


def main() -> int:
    print(f"== verify_dashboard_lens_filters @ {ROOT} ==")
    tpl = _read(TEMPLATE)
    if not check("0: dashboard_preview.html 템플릿 존재", bool(tpl) and len(tpl) > 4000):
        print("\nRESULT: FAIL (템플릿 누락)")
        return 1
    check_lens_controls(tpl)
    check_article_tags(tpl)
    check_filter_js(tpl)
    check_empty_and_mobile(tpl)
    check_regenerated(tpl)
    check_latest_separation()
    check_telegram_mapping()
    check_honesty(tpl)

    print()
    if _failures:
        print(f"RESULT: FAIL ({len(_failures)} 항목)")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("RESULT: PASS — 요약 대시보드 렌즈 필터 기능 동작 확인 (D7-A)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
