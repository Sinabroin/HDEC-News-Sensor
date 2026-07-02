#!/usr/bin/env python3
"""D7-AD-V verifier — 기존 좌측 목차(#lensnav)로 카테고리 탐색을 흡수했는지 검증한다.

D7-AD-U가 만든 별도 상단 목차(railNav '전체 탐색')와 본문 위 카테고리 칩 바(newsCatFilter)를 반려하고,
기존 왼쪽 목차 하나를 필터/탐색/운영의 단일 진입점으로 개선한 계약을 오프라인으로 확인한다
(네트워크/비밀값 0건, 시간 무관). 사용자 D7-AD-V 검증 조건 1–15에 대응한다.

  1. '전체 탐색' 문구 없음 · 2. id="railNav" 없음 · 3. #lensnav 단일 진입점
  4. 본문 위 id="newsCatFilter" 칩 바 없음
  5. 좌측 목차에 수주/재무/정책/경쟁/브랜드/해외 언론 존재
  6. 해당 항목이 news accordion(details data-acc)/기사 필터와 연결
  7. 기상은 뉴스 필터가 아니라 siteWeatherCard(명일 정오 시공 리스크)로 연결
  8. 운영자 실행이 왼쪽 목차 하단 compact card로 존재
  9. OPERATOR_API_BASE 주입 시 collect/telegram/teams 버튼 enabled
 10. 무설정 시 버튼 disabled(+ '운영자 서버 미연결') · 11. local operator smoke fail-closed
 12. 시장 지표 10개 source discovery status가 문서/모델에 존재
 13. '무료 소스 없음' 단정 대신 후보/다음 액션 기록 · 14. public artifact 현장명/secret/token 없음
 15. docs/daily/*.html hand-edit/stage 없음
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "scripts" / "build_static_dashboard.py"
TEMPLATE = ROOT / "templates" / "dashboard_preview.html"
FRED_MODULE = ROOT / "app" / "fred_market.py"
SMOKE = ROOT / "scripts" / "smoke_operator_api_local.py"
DISCOVERY_DOC = ROOT / "docs" / "operations" / "D7ADV_MARKET_SOURCE_DISCOVERY.md"
INTEGRATION_DOC = ROOT / "docs" / "operations" / "D7ADX_MARKET_SOURCE_INTEGRATION.md"

# 사용자 지정 2차 pill(오른쪽 결과 내부) — 좌측 1차 navcat 아님.
NEWS_PILL_LABELS = {"all": "전체", "new": "신규", "order": "수주", "finance": "재무",
                    "policy": "정책", "competitor": "경쟁", "brand": "브랜드", "global_press": "해외 언론"}
# 1차 렌즈 예시(좌측 data-filter) — layered news result 검증용.
LAYER1_LENS_EXAMPLES = (
    ("civil_infrastructure", "토목"),
    ("building_housing", "건축주택"),
    ("global_business", "글로벌"),
)
MARKET_CATS = ("base_metals", "steel_materials", "oil_refined", "gas_lng", "coal", "rates_inflation")
# 사용자 지정 시장 지표 10개(id) — 상태 보드(MARKET_REASON) + 조사 문서에 존재해야 한다.
MARKET_IDS = ("nickel", "scrap_steel", "cement", "bitumen", "jet_kerosene",
              "bunker_fuel", "coking_coal", "us_2y", "kr_10y", "twdkrw")
UNLINKED_MARKET_IDS = ("nickel", "scrap_steel", "cement", "bitumen",
                       "jet_kerosene", "bunker_fuel", "coking_coal", "twdkrw")
INTERNAL_MARKERS = ["흑석9구역", "자푸라", "코즐로두이", "진해신항", "월곶~판교",
                    "마잔", "카르발라", "루사일 타워", "신반포 22차", "대조제1구역"]
SECRET_NAMES = ("GH_OPERATOR_TOKEN", "OPERATOR_SHARED_SECRET", "OPERATOR_PIN",
                "TELEGRAM_BOT_TOKEN", "GMAIL_SMTP_APP_PASSWORD", "TEAMS_CHANNEL_EMAIL")
_TOKEN_SHAPES = (re.compile(r"\b(?:ghp_|github_pat_)[A-Za-z0-9_]{20,}\b"),
                 re.compile(r"\b\d{8,}:[A-Za-z0-9_-]{20,}\b"))

_failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        _failures.append(name)
    return ok


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return ""


def _order(html: str, *needles) -> bool:
    last = -1
    for n in needles:
        i = html.find(n)
        if i < 0 or i <= last:
            return False
        last = i
    return True


def _market_reason(template: str, item_id: str) -> str:
    match = re.search(
        rf'^\s*{re.escape(item_id)}:\s*"([^"]*)"', template, re.MULTILINE)
    return match.group(1) if match else ""


def _build(out: Path, base: str | None = None):
    env = {**os.environ}
    env.pop("SITE_WATCHLIST_PATH", None)
    env.pop("SITE_WATCHLIST_EXPOSE_TREE", None)
    env["APP_MODE"] = "mock"
    env["PYTHONHASHSEED"] = "0"
    cmd = [sys.executable, str(BUILDER), "--output", str(out)]
    if base:
        cmd += ["--operator-api-base", base]
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=300, env=env)


# ---------------------------------------------------------------------------
# 1 · 템플릿 소스 계약 (nav fold — DOM/JS 앵커)
# ---------------------------------------------------------------------------

def check_template() -> None:
    t = _read(TEMPLATE)
    check("1: '전체 탐색' 문구 제거", "전체 탐색" not in t)
    check("2: 별도 상단 목차(railNav/railnav) 제거",
          'id="railNav"' not in t and 'class="railnav"' not in t)
    check("3: 좌측 목차(#lensnav)가 단일 탐색 진입점", 'id="lensnav"' in t)
    check("4: 본문 위 뉴스 카테고리 칩 바(newsCatFilter) 제거", 'id="newsCatFilter"' not in t)
    for key, lbl in LAYER1_LENS_EXAMPLES:
        check(f"5: 좌측 1차 렌즈 '{lbl}'(data-filter='{key}')",
              f'data-filter="{key}"' in t and f'class="nlabel">{lbl}</span>' in t)
    for key, lbl in NEWS_PILL_LABELS.items():
        check(f"5: 2차 pill '{lbl}'(NAV_CAT_PILLS/data-cat='{key}') — 좌측 navcat 아님",
              f'key: "{key}", label: "{lbl}"' in t
              and f'class="nav navcat" data-acc="{key}"' not in t)
    check("5: 2차 pill count badge(cnl-badge/countForSecondary) 렌더",
          "cnl-badge" in t and "function countForSecondary" in t)
    check("6: layered 탐색 JS(openNavigationNewsResult/renderLayeredNewsList) 존재",
          "function openNavigationNewsResult" in t and "function renderLayeredNewsList" in t
          and "isLayeredNewsLens" in t and "details.acc-sec" not in t)
    check("6b: 현장 필터(flat list) — renderSiteNewsList/rowsForSite/onSiteNodeClick",
          "function renderSiteNewsList" in t and "function rowsForSite" in t
          and "function onSiteNodeClick" in t and "article_keys" in t
          and "현재 매칭된 기사 없음" in t and "st-fclear" in t)
    check("6: 좌측 목차 클릭 라우팅이 navmkt/navwx/렌즈(data-filter)로 분기",
          'classList.contains("navmkt")' in t and 'classList.contains("navwx")' in t
          and 'classList.contains("navcat")' not in t)
    check("7: 기상은 navwx→siteWeatherCard(뉴스 카테고리 필터 아님)",
          'class="nav navwx"' in t and "function openWeatherRisk" in t
          and 'el("siteWeatherCard")' in t
          and 'class="nav navcat" data-acc="weather"' not in t)
    for cat in MARKET_CATS:
        check(f"7: 좌측 목차 시장 그룹 navmkt data-market='{cat}'",
              f'class="nav navmkt" data-market="{cat}"' in t)
    check("7: 시장 그룹 클릭이 카테고리 카드로 이동(openMarketCategory→mcat-<cat>)",
          "function openMarketCategory" in t and 'el("mcat-" + cat)' in t)
    check("8: 운영자 실행이 왼쪽 목차 하단 compact 카드(opctl compact)",
          'class="opctl compact"' in t and 'id="opctl"' in t)
    # 13 · 미연동은 후보/한계/다음 액션, FRED overlay는 provenance/caveat를 노출한다.
    check("13: 시장 상태 보드에 항목별 사유(MARKET_REASON/marketReason/ms-why)",
          "MARKET_REASON" in t and "marketReason" in t and "ms-why" in t)
    check("13: 미연동/우선=접힘(ms-collapse) · 시장 카드=mcat-backlog",
          "ms-collapse" in t and "mcat-backlog" in t)
    us_reason = _market_reason(t, "us_2y")
    check("13: us_2y 연동 사유에 FRED DGS2 provenance",
          "FRED" in us_reason and "DGS2" in us_reason, us_reason)
    check("13: us_2y 현재값 오인 방지(영업일/단일값/delayed caveat)",
          any(term in us_reason for term in ("영업일", "단일값", "지연")),
          us_reason)
    kr_reason = _market_reason(t, "kr_10y")
    check("13: kr_10y FRED/OECD 월간 proxy provenance",
          "FRED" in kr_reason and "OECD" in kr_reason and "월간" in kr_reason,
          kr_reason)
    check("13: kr_10y를 실시간 한국 국고채로 표현하지 않음",
          "일간 국고 10Y 아님" in kr_reason
          or any(term in kr_reason for term in ("proxy", "대용", "지연")),
          kr_reason)
    for mid in UNLINKED_MARKET_IDS:
        reason = _market_reason(t, mid)
        check(f"13: {mid} 미연동 사유에 후보/한계/next action",
              reason.startswith("후보:")
              and "한계:" in reason and "다음:" in reason,
              reason)
    fred = _read(FRED_MODULE)
    builder = _read(BUILDER)
    check("13: FRED overlay가 item별 source id/provenance를 모델에 보존",
          all(token in fred for token in ('"us_2y": ("DGS2"', '"kr_10y": ("IRLTLT01KRM156N"'))
          and "source_id" in fred and "value_source_id" in builder
          and "value_source" in builder)
    # D7-AE 계약 갱신: kr_10y는 월간 OECD 장기금리 '대용'이므로 delayed로 위장하지 않고
    # proxy_market으로 라벨한다(감사 keep_proxy_with_caveat · market_history_coverage 3b 정합).
    # us_2y(일간 DGS2)는 여전히 delayed_market — 두 라벨이 모두 빌더에 있어야 한다.
    check("13: kr_10y point는 proxy_note + proxy_market 라벨(월간 OECD 대용 정직화)",
          "일간 국고채 10Y 아님" in builder
          and 'data_mode="proxy_market" if iid == "kr_10y" else "delayed_market"' in builder
          and 'it["proxy"] = True' in builder)


# ---------------------------------------------------------------------------
# 2 · 공개(mock) 빌드 — nav fold가 산출물에 반영 + public-safe
# ---------------------------------------------------------------------------

def check_public_build() -> None:
    with tempfile.TemporaryDirectory(prefix="hdec_railfold_") as tmp:
        out = Path(tmp) / "dash.html"
        proc = _build(out)
        if not check("공개(mock) 빌드 동작", proc.returncode == 0 and out.exists(),
                     (proc.stderr or "")[-200:]):
            return
        html = out.read_text(encoding="utf-8")
        check("1/2/4: 산출물에 전체 탐색/railNav/newsCatFilter 없음",
              "전체 탐색" not in html and 'id="railNav"' not in html
              and 'id="newsCatFilter"' not in html)
        check("6: 사용자 화면에 visible accordion(details.acc-sec) 없음",
              '<details class="acc-sec"' not in html and "카테고리별 브리핑" not in html)
        check("6: 좌측 navcat(수주/재무 등 1차 항목) 없음",
              'class="nav navcat"' not in html)
        check("6: flat news list 컨테이너(#categoryNewsList) + 2차 pill 영역(#cnlPills) 존재",
              'id="categoryNewsList"' in html and 'id="cnlPills"' in html)
        check("6: layered 탐색 JS(openNavigationNewsResult/renderLayeredNewsList) 존재",
              "function openNavigationNewsResult" in html and "function renderLayeredNewsList" in html)
        check("6: 2차 pill count badge(cnl-badge/countForSecondary) 렌더 로직",
              "cnl-badge" in html and "countForSecondary" in html)
        m = re.search(r'<script type="application/json" id="preview-model">\s*(.*?)\s*</script>',
                      html, re.S)
        if m:
            try:
                model = json.loads(m.group(1))
                nav_secs = {s.get("key") for s in (model.get("nav_category_sections") or [])}
                for key in NEWS_PILL_LABELS:
                    if key == "all":
                        continue
                    check(f"6: 2차 pill '{key}' ↔ nav_category_sections",
                          key in nav_secs)
                check("6: weather는 nav_category_sections에서 제외",
                      "weather" not in nav_secs)
            except json.JSONDecodeError:
                check("6: preview-model JSON 파싱", False)
        else:
            check("6: preview-model JSON 추출", False)
        # 8 · 좌측 rail 순서: railcol → lensnav → opctl → 본문(panel-news)
        check("8: 운영자 compact가 좌측 rail 하단(railcol→lensnav→opctl→panel-news)",
              _order(html, 'class="railcol"', 'id="lensnav"', 'id="opctl"', 'id="panel-news"'))
        # 7 · 기상 카드(명일 정오 시공 리스크)는 시장 탭 안
        check("7: 명일 정오 시공 리스크 카드(siteWeatherCard) 존재",
              'id="siteWeatherCard"' in html and "명일 정오 시공 리스크" in html)
        # 10 · 무설정 공개 빌드는 버튼 disabled + '운영자 서버 미연결'
        check("10: 무설정 공개 빌드 버튼 disabled + 운영자 서버 미연결",
              'id="opCollectBtn" type="button" disabled' in html
              and "운영자 서버 미연결" in html)
        # 14 · D7-AD-Z: approved public site watchlist is intentionally visible.
        tree = model.get("site_watch_tree") or {}
        node_total = 0
        for sc in (tree.get("by_scope") or {}).values():
            for g in sc.get("groups") or []:
                node_total += len(g.get("nodes") or [])
        check("14: 공개 산출물에 승인된 현장 워치리스트 노드 노출",
              node_total > 0, f"nodes={node_total}")
        sec = [s for s in SECRET_NAMES if s in html]
        toks = any(p.search(html) for p in _TOKEN_SHAPES)
        check("14: 공개 산출물에 secret 이름/token 형태 없음", not sec and not toks,
              ", ".join(sec) + ("+token" if toks else ""))


# ---------------------------------------------------------------------------
# 3 · 운영자 base 주입 빌드 — 세 버튼 활성화 + endpoint 주입 (조건 9)
# ---------------------------------------------------------------------------

def check_operator_base_build() -> None:
    base = "https://operator.example.invalid"
    with tempfile.TemporaryDirectory(prefix="hdec_railfold_op_") as tmp:
        out = Path(tmp) / "dash.html"
        proc = _build(out, base=base)
        if not check("운영자 base 주입 빌드 동작", proc.returncode == 0 and out.exists(),
                     (proc.stderr or "")[-200:]):
            return
        html = out.read_text(encoding="utf-8")
        check("9: base 주입 시 collect/telegram/teams 버튼 활성화",
              "collectBtn.disabled = false" in html and "sendBtn.disabled = false" in html
              and "teamsBtn.disabled = false" in html)
        check("9: 세 Operator endpoint 주입(collect/send-telegram/send-teams)",
              '"/api/operator/collect"' in html and '"/api/operator/send-telegram"' in html
              and '"/api/operator/send-teams"' in html)
        check("9: 버튼이 GitHub Actions로 직접 이동하지 않음(서버 dispatch 소유)",
              "/actions/workflows/" not in html and "api.github.com" not in html)


# ---------------------------------------------------------------------------
# 4 · 로컬 Operator smoke — 무설정 fail-closed, 실제 dispatch 없음 (조건 11)
# ---------------------------------------------------------------------------

def check_operator_smoke() -> None:
    if not SMOKE.exists():
        check("11: local operator smoke 존재", False, "smoke_operator_api_local.py 없음")
        return
    proc = subprocess.run([sys.executable, str(SMOKE)], cwd=ROOT,
                          capture_output=True, text=True, timeout=120)
    check("11: local operator smoke PASS(무설정 fail-closed · 실제 dispatch 0건)",
          proc.returncode == 0, (proc.stdout or proc.stderr)[-200:])


# ---------------------------------------------------------------------------
# 5 · 시장 지표 조사 문서 (조건 12/13)
# ---------------------------------------------------------------------------

def check_discovery_doc() -> None:
    if not check("12: 시장 소스 조사 문서 존재(D7ADV_MARKET_SOURCE_DISCOVERY.md)",
                 DISCOVERY_DOC.exists()):
        return
    doc = _read(DISCOVERY_DOC)
    missing = [mid for mid in MARKET_IDS if f"`{mid}`" not in doc]
    check("12: 문서에 시장 지표 10개(id) 모두 존재", not missing, str(missing))
    for col in ("current_status", "source_candidate", "source_type",
                "update_freq", "chart_possible", "limitation", "next_action"):
        check(f"12: 분류 컬럼 '{col}' 존재", col in doc)
    check("13: '무료 소스 없음' 단정 대신 후보/한계/다음 액션 기록",
          "next_action" in doc and doc.count("candidate") >= 5
          and ("후보" in doc and "다음" in doc))
    integration = _read(INTEGRATION_DOC)
    check("13: D7-AD-X 문서에 FRED source id와 point/proxy 한계 기록",
          "`DGS2`" in integration and "`IRLTLT01KRM156N`" in integration
          and "단일값" in integration and "일간 국고 10Y 아님" in integration)
    check("13: 호르무즈 미연동에 후보/한계/next action 기록",
          "호르무즈 해협 API" in integration and "후보:" in integration
          and "AIS/API 키·정책 한계" in integration
          and "사용자 링크 재요청" in integration)


# ---------------------------------------------------------------------------
# 6 · 스테이징 위생 — docs/daily/*.html hand-edit/stage 금지 (조건 15)
# ---------------------------------------------------------------------------

def check_staging() -> None:
    try:
        p = subprocess.run(["git", "diff", "--cached", "--name-only"], cwd=ROOT,
                           capture_output=True, text=True, timeout=30)
        staged = [s for s in (p.stdout or "").splitlines() if s.strip()]
    except (OSError, subprocess.SubprocessError):
        check("15: git staged 목록 조회", False, "git 실행 실패")
        return
    forbidden = [".agents/", "design/", "data/private/"]
    hits = [s for s in staged if any(s.startswith(f) for f in forbidden)]
    check("15: 금지 경로(.agents/·design/·data/private/) staged 아님", not hits, str(hits))
    docs_daily = [s for s in staged if s.startswith("docs/daily/") and s.endswith(".html")]
    check("15: docs/daily/*.html hand-edit stage 아님(빌더 산출만 허용)",
          not docs_daily, str(docs_daily))


def main() -> int:
    print(f"== verify_dashboard_existing_rail_navigation (D7-AD-V) @ {ROOT} ==")
    check_template()
    check_public_build()
    check_operator_base_build()
    check_operator_smoke()
    check_discovery_doc()
    check_staging()
    print()
    if _failures:
        print(f"RESULT: FAIL ({len(_failures)} 항목)")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("RESULT: PASS — 좌측 목차 단일 진입점 · flat news list · pill count badge · "
          "시장 접힘 · railNav/newsCatFilter 제거 · public-safe (D7-AD-W Phase 1B)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
