#!/usr/bin/env python3
"""D7-AG-3 — 호르무즈 해협 선박 통과량(AIS 통항) 지표 계약 검수 (offline-safe).

이 검수기는 사용자 실패 신고("호르무즈가 기사 카드로 대체됐다")를 잠근다. 공개 대시보드 시장
패널에는 **호르무즈 해협 통과 선박량 지표**(기사 카드가 아니라 통과 선박 수)가 있어야 하고,
실 연동이면 수치를, 미연동이면 가짜 수치 없이 '연동 미설정'을 정직하게 표시해야 한다.

계약:
  L  leaf(app/hormuz_transit) — mock=unavailable(네트워크 0건·수치 0건), live=계약 키 반환.
  T  시장 패널에 '호르무즈 해협 통과 선박량' 카드(class="card hz-transit") 존재 + 기사 아님.
  M  data_mode ∈ {live, connected, unavailable}. live/connected이면 통항 수가 숫자·출처 존재.
     unavailable이면 수치 없음 + '연동 미설정' 표시.
  X  데모/모식도/proxy/가짜 잔재 없음(해협 모식도·AIS 하한 추정 · proxy·데모 데이터·hz* DOM).

네트워크 없이도 통과한다: mock 빌드는 항상 unavailable(정직), live 빌드는 실측이면 수치·
아니면 unavailable(둘 다 계약 충족). 실 발송/DB/네트워크 필수 아님.
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "scripts" / "build_static_dashboard.py"
DASHBOARD = ROOT / "docs" / "daily" / "dashboard-latest.html"
LEAF = ROOT / "app" / "hormuz_transit.py"

CARD_CLASS = 'class="card hz-transit"'
CARD_TITLE = "호르무즈 해협 통과 선박량"
DEMO_RESIDUE = (
    "해협 모식도", "AIS 하한 추정 · proxy", "데모 데이터",
    "시간대별 통과 (데모)", "선종 분포 (현재 통항 중 · 데모)",
)
VALID_MODES = {"live", "connected", "unavailable"}

_failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        _failures.append(name)
    return ok


def info(msg: str) -> None:
    print(f"[INFO] {msg}")


def _model(html: str) -> dict:
    m = re.search(r'<script type="application/json" id="preview-model">(.*?)</script>',
                  html, re.S)
    try:
        return json.loads(m.group(1)) if m else {}
    except json.JSONDecodeError:
        return {}


def _card_region(html: str) -> str:
    """호르무즈 통항 카드(div) 구간만 잘라낸다(기사 여부 판정용)."""
    i = html.find('id="hormuzTransitCard"')
    if i < 0:
        return ""
    start = html.rfind("<div", 0, i)
    depth, j = 0, start
    tag = re.compile(r"<div\b|</div>")
    for mt in tag.finditer(html, start):
        if mt.group(0) == "</div>":
            depth -= 1
            if depth == 0:
                j = mt.end()
                break
        else:
            depth += 1
    return html[start:j]


def _load_builder():
    spec = importlib.util.spec_from_file_location("d7ag3_hz_builder", BUILDER)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def check_leaf() -> None:
    if not check("L0: leaf app/hormuz_transit.py 존재", LEAF.exists()):
        return
    sys.path.insert(0, str(ROOT))
    from app import hormuz_transit as ht  # noqa: E402

    mock = ht.fetch_hormuz_transit("mock")
    check("L1: mock=unavailable(네트워크 0건)",
          mock.get("data_mode") == "unavailable")
    check("L2: mock은 통항 수치를 만들지 않음",
          "n_total" not in mock and "n_tanker" not in mock)
    check("L3: unavailable 사유가 '기사 기반 대체 아님' 명시",
          "기사 기반 대체 아님" in (mock.get("unavailable_reason") or ""))
    check("L4: 출처 라벨이 실 소스(IMF PortWatch)",
          "PortWatch" in (mock.get("source") or ""))
    # live는 네트워크가 있으면 실측, 없으면 unavailable — 둘 다 계약 충족(오프라인 안전).
    live = ht.fetch_hormuz_transit("live", today=date(2026, 7, 6))
    if live.get("data_mode") == "live":
        check("L5: live 실측 시 통항 수가 정수",
              isinstance(live.get("n_total"), int) and live["n_total"] >= 0)
        check("L6: live 실측 시 관측일 존재",
              bool(re.match(r"\d{4}-\d{2}-\d{2}", str(live.get("observed_date") or ""))))
    else:
        info("L5/L6: live 네트워크 미가용 — unavailable로 정직 강등(오프라인 SKIP)")
        check("L5(off): 미가용도 unavailable 계약 준수",
              live.get("data_mode") == "unavailable" and "n_total" not in live)


def check_surface(html: str, label: str) -> None:
    if not html:
        info(f"{label}: 산출물 없음 — SKIP")
        return
    model = _model(html)
    hz = model.get("hormuz_transit") or {}
    mode = hz.get("data_mode")

    check(f"T1[{label}]: 통과 선박량 카드 존재(class=hz-transit)",
          CARD_CLASS in html and 'id="hormuzTransitCard"' in html)
    check(f"T2[{label}]: '호르무즈 해협 통과 선박량' 지표 제목", CARD_TITLE in html)
    check(f"T3[{label}]: '선박'/'통과' 지표 문맥", "선박" in html and "통과" in html)

    card = _card_region(html)
    # 기사 카드가 아님: 통항 카드 구간에 기사 링크/기사 클래스가 없어야 한다(뉴스영역 hormuz_watch와 구별).
    check(f"T4[{label}]: 지표는 기사 카드가 아님(카드 내 기사 href/클래스 없음)",
          bool(card) and "hormuz-article" not in card
          and 'target="_blank"' not in card and "http" not in card)

    check(f"M1[{label}]: data_mode ∈ {{live,connected,unavailable}}",
          mode in VALID_MODES, repr(mode))
    check(f"M2[{label}]: 출처 필드 존재(실 소스)",
          bool(hz.get("source")) and "PortWatch" in str(hz.get("source")))

    if mode in {"live", "connected"}:
        check(f"M3[{label}]: 연결이면 통항 수가 숫자",
              isinstance(hz.get("n_total"), int) and hz["n_total"] >= 0, repr(hz.get("n_total")))
        check(f"M4[{label}]: 연결이면 관측일 + 카드에 수치 렌더",
              bool(hz.get("observed_date"))
              and "IMF PortWatch · live" in card and "척" in card)
    else:
        check(f"M3[{label}]: 미연동이면 수치 미생성",
              hz.get("n_total") is None and "n_total" not in hz)
        check(f"M4[{label}]: 미연동이면 '연동 미설정' 정직 표기",
              "연동 미설정" in card)

    residue = [p for p in DEMO_RESIDUE if p in html]
    check(f"X1[{label}]: 데모/모식도/proxy 잔재 0건", not residue, str(residue))
    hz_ids = re.findall(r'id="(hz[A-Za-z]+)"', html)
    check(f"X2[{label}]: 데모 hz* DOM 없음", not hz_ids, str(sorted(set(hz_ids))))


def build(market_mode: str) -> str:
    with tempfile.TemporaryDirectory(prefix="hdec_hz_ais_") as tmp:
        out = Path(tmp) / "d.html"
        cmd = [sys.executable, str(BUILDER), "--output", str(out)]
        if market_mode == "live":
            cmd += ["--market-mode", "live"]
        proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, timeout=300)
        check(f"build(market={market_mode})", proc.returncode == 0, (proc.stderr or "")[-200:])
        return out.read_text(encoding="utf-8") if out.exists() else ""


def main() -> int:
    print(f"== D7-AG-3 Hormuz AIS transit indicator @ {ROOT} ==")
    check_leaf()
    check_surface(build("mock"), "fresh-mock")
    check_surface(build("live"), "fresh-live")
    check_surface(DASHBOARD.read_text(encoding="utf-8") if DASHBOARD.exists() else "",
                  "committed")
    print()
    if _failures:
        print(f"RESULT: FAIL ({len(_failures)})")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("RESULT: PASS — 호르무즈 통과 선박량 지표(실 소스·기사 아님·미연동은 정직) 계약 통과")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
