#!/usr/bin/env python3
"""verify_rc3_public_truth — D7-AE-RC3 공개 URL 실패 제거 통합 계약 (오프라인).

사용자 QA 9항목의 산출물 계약을 한 곳에서 잠근다:
  1 현장 노드 '관련 기사 없음' 정직 표시(무관 기사 미부착은 site_match_relevance가 소유)
  2 사업영역 렌즈 근거 게이팅 — lens_reasons 노출 + 기사당 사업 렌즈 상한
  3 2차 레이블 exclusive partition('기타' 포함 · 전체=합산 · 0건 pill 숨김)
  4 운영자 모드 — 공개 빌드에 클릭 가능한 Actions/새로고침 링크
  5 호르무즈 실연동 조사 문서(삭제로 끝내지 않음 · 확인된 repo 기록)
  6 공식 현대건설 CI 임베드(가짜 로고 금지 · data-URI)
  7 US 2Y FRED 일별 히스토리 계약 + 미연동 4분류 백로그 문서 + 공개 UI 백로그 강등
  8 시장 상단 장문 설명 제거 → 한 줄
  9 모든 공개 산출물은 기상 unavailable을 '기상 데이터 미수신'으로만 표기

네트워크 0건: fresh 빌드는 mock, 커밋 산출물 검사는 마커(news-data-mode/weather_data_mode)
로 모드를 판별해 해당 모드의 계약만 적용한다.
"""

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "scripts" / "build_static_dashboard.py"
TEMPLATE = ROOT / "templates" / "dashboard_preview.html"
DASHBOARD = ROOT / "docs" / "daily" / "dashboard-latest.html"

BIZ_LENSES = ("civil_infrastructure", "building_housing", "plant", "new_energy",
              "development_business", "global_business", "safety_quality")
VERBOSE_MARKET_ANCHORS = (
    "건설 시장 유니버스",
    'id="marketLinkNote"',
    'class="clickhint" style="margin-top:8px;"',
    "· 원자재·금속 · 철강·건자재",
)
MARKET_ONE_LINER = "자동 연동 지표만 표시합니다. 지연 시세이며 현재 체결값은 아닙니다."

_failures: list = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    line = f"[{'PASS' if ok else 'FAIL'}] {name}"
    if detail:
        line += f" — {detail}"
    print(line)
    if not ok:
        _failures.append(name)
    return ok


def _model(html: str) -> dict:
    m = re.search(r'<script type="application/json" id="preview-model">(.*?)</script>',
                  html, re.S)
    return json.loads(m.group(1)) if m else {}


def _no_script(html: str) -> str:
    return re.sub(r"<script.*?</script>", "", html, flags=re.S)


def check_template() -> None:
    t = TEMPLATE.read_text(encoding="utf-8")
    check("T1: 2차 레이블 partition 함수(rowSecondaryKey) 존재", "function rowSecondaryKey" in t)
    check("T2: '기타' pill 정의", '{ key: "etc", label: "기타" }' in t)
    check("T3: 0건 pill 숨김 분기", "0건 pill은 렌더하지 않는다" in t)
    check("T4: partition 계약 주석(전체 = 하위 레이블 합산)", "전체 = 하위 레이블 합산" in t)
    check("T5: 현장 0건 노드 '관련 기사 없음' 렌더 분기",
          "관련 기사 없음" in t and ".st-none" in t)
    check("T6: 시장 백로그 강등 플래그 분기(market_backlog_visible)",
          "market_backlog_visible" in t and "mcat-backlognote" in t and "ms-backlognote" in t)
    check("T7: placeholder 아이콘 제거 + 빌더 로고 슬롯", '<div class="mark">' not in t
          and "_embed_brand_logo" in t)


def check_surface(html: str, label: str, *, expect_operator_hidden: bool) -> None:
    body = _no_script(html)
    model = _model(html)
    # 4 · 공개 운영자 실행 버튼 + 미연결 fail-closed
    if expect_operator_hidden:
        check(f"S4a[{label}]: 클릭 가능한 운영자 details + 실행 UI visible",
              '<details class="opctl-panel" id="opctlPanel">' in body
              and '<summary class="opctl-mode-toggle">' in body
              and 'id="opApiControls"' in body)
        check(f"S4b[{label}]: 실행 버튼 3개 + 보조 새로고침",
              all(label_text in body for label_text in (
                  "데이터 새로고침 실행", "텔레그램 전송 실행",
                  "Teams 채널 전송 실행", "Public URL 새로고침"))
              and 'id="opActionLinks"' not in body)
    # 8 · 시장 장문 설명 제거
    leftovers = [a for a in VERBOSE_MARKET_ANCHORS if a in html]
    check(f"S8a[{label}]: 시장 장문 설명 anchor 0건", not leftovers, str(leftovers[:2]))
    check(f"S8b[{label}]: 한 줄 안내 존재", MARKET_ONE_LINER in html)
    # 7 · 백로그 강등 플래그
    check(f"S7a[{label}]: market_backlog_visible=false 주입",
          model.get("market_backlog_visible") is False)
    # 6 · 공식 CI 로고
    check(f"S6a[{label}]: 공식 CI 로고 data-URI 임베드(alt=현대건설)",
          'class="cilogo"' in html and 'src="data:image/svg+xml;base64,' in html
          and 'alt="현대건설"' in html)
    # 2 · lens_reasons + 사업 렌즈 상한
    rows = list(model.get("news_rows") or [])
    for bank in (model.get("lens_banks") or {}).values():
        rows.extend(bank or [])
    with_reason_field = [r for r in rows if isinstance(r.get("lens_reasons"), dict)]
    check(f"S2a[{label}]: 행에 lens_reasons(근거) 필드 노출",
          bool(rows) and len(with_reason_field) == len(rows),
          f"{len(with_reason_field)}/{len(rows)}")
    nonempty = [r for r in rows if r.get("lens_reasons")]
    check(f"S2b[{label}]: 근거가 실제로 채워짐(≥1행)", bool(nonempty))
    over = [r.get("title") for r in rows
            if len([l for l in (r.get("lens") or []) if l in BIZ_LENSES]) > 3]
    check(f"S2c[{label}]: 기사당 사업영역 렌즈 ≤3(과대분류 상한)", not over,
          str(over[:2])[:120])
    # 9 · RC4 기상 문구: live/unavailable 모두 옛 '미연동' 문구를 쓰지 않는다.
    wx_live = model.get("weather_data_mode") == "live"
    if wx_live:
        check(f"S9[{label}]: weather live — source/as_of + 옛 문구 0건",
              "기상 데이터 소스 미연동" not in html
              and bool(model.get("weather_source"))
              and bool(model.get("weather_updated_at")))
    else:
        check(f"S9[{label}]: weather unavailable — '기상 데이터 미수신'만 사용",
              "기상 데이터 소스 미연동" not in html
              and "기상 데이터 미수신" in html
              and not (model.get("weather_rows") or []))
    # 1 · 현장 매칭 정직성 — 매칭 노드 수 ≤ 워치리스트 총 노드, 0건 노드는 UI가
    # '관련 기사 없음'을 렌더할 수 있어야 한다(렌더 분기는 T5/JS가 소유 — 여기선 데이터).
    tree = model.get("site_watch_tree") or {}
    total = int(tree.get("total_nodes") or 0)
    matched = int(tree.get("matched_nodes") or 0)
    check(f"S1[{label}]: 현장 매칭 데이터 정합(matched {matched} ≤ total {total} · 트리 존재)",
          bool(tree) and 0 <= matched <= max(total, 1))


def check_fresh_mock_build() -> None:
    with tempfile.TemporaryDirectory(prefix="hdec_rc3_") as tmp:
        out = Path(tmp) / "dash.html"
        proc = subprocess.run([sys.executable, str(BUILDER), "--output", str(out)],
                              cwd=ROOT, capture_output=True, text=True, timeout=300)
        if not check("F0: mock 빌드 동작(오프라인)", proc.returncode == 0 and out.exists(),
                     (proc.stderr or "")[-200:]):
            return
        check_surface(out.read_text(encoding="utf-8"), "fresh-mock",
                      expect_operator_hidden=True)


def check_committed() -> None:
    if not DASHBOARD.exists():
        check("C0: 커밋 대시보드 존재", False)
        return
    html = DASHBOARD.read_text(encoding="utf-8")
    check_surface(html, "committed", expect_operator_hidden=True)
    model = _model(html)
    live = "<!--news-data-mode:live-->" in html
    if live:
        items = {i.get("id"): i for i in model.get("market_items") or []}
        us2 = items.get("us_2y") or {}
        hist = us2.get("history") or {}
        periods_ok = (len(hist.get("1w") or []) >= 2 and len(hist.get("1y") or []) >= 2
                      and hist.get("3m") != hist.get("1y"))
        check("C7: live 산출물 us_2y — FRED 일별 기간 히스토리(3개월≠1년) + delayed",
              periods_ok and us2.get("data_mode") == "delayed_market"
              and "FRED" in str(us2.get("history_source") or ""),
              f"mode={us2.get('data_mode')} src={us2.get('history_source')}")
    else:
        check("C7: (mock 산출물) us_2y는 unavailable·히스토리 없음(정직)",
              not (model and (dict((i.get('id'), i) for i in model.get('market_items') or [])
                              .get('us_2y') or {}).get('history')))


def check_offline_contracts() -> None:
    sys.path.insert(0, str(ROOT))
    from app import market_history as mh
    check("O7a: market_history FRED 레지스트리(us_2y=DGS2)",
          mh._FRED_HISTORY.get("us_2y", ("",))[0] == "DGS2")
    check("O7b: SOURCE_NEEDED_IDS=('kr_10y',) · us_2y는 Yahoo 카탈로그 밖",
          mh.SOURCE_NEEDED_IDS == ("kr_10y",) and not mh.is_supported("us_2y"))
    hz = (ROOT / "docs" / "operations" / "HORMUZ_LIVE_INTEGRATION.md")
    doc = hz.read_text(encoding="utf-8") if hz.exists() else ""
    check("O5: 호르무즈 실연동 조사 문서(확인된 repo·필요 입력·운영 방식·어댑터 계획)",
          "hormuz-ship-tracker" in doc and "필요 입력" in doc
          and "운영 방식" in doc and "어댑터 구현 계획" in doc)
    bl = (ROOT / "docs" / "operations" / "D7AE_RC3_MARKET_BACKLOG.md")
    bdoc = bl.read_text(encoding="utf-8") if bl.exists() else ""
    check("O7c: 시장 미연동 4분류 백로그 문서",
          all(k in bdoc for k in ("무료/공개 데이터 자체가 없음", "유료 전용",
                                  "어댑터 미구현", "proxy로만 가능")))
    br = (ROOT / "docs" / "assets" / "brand" / "README.md")
    check("O6: 로고 출처 문서(docs/assets/brand/README.md) + asset 존재",
          br.exists() and (ROOT / "docs" / "assets" / "brand" / "hdec-logo.svg").exists()
          and "hdec.kr" in br.read_text(encoding="utf-8"))


def main() -> int:
    print(f"== verify_rc3_public_truth (D7-AE-RC3) @ {ROOT} ==")
    check_template()
    check_offline_contracts()
    check_fresh_mock_build()
    check_committed()
    print()
    if _failures:
        print(f"RESULT: FAIL ({len(_failures)} 항목)")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("RESULT: PASS — D7-AE-RC3 공개 표면 계약(렌즈 근거·partition·운영 B안·시장 한 줄·"
          "백로그 강등·공식 CI·US 2Y FRED·기상 문구·호르무즈 조사) (D7-AE-RC3)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
