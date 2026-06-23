#!/usr/bin/env python3
"""verify_hormuz_maritime_snapshot — 호르무즈 해협 관찰 카드 정직성 검수 (preview).

이 저장소에는 라이브 AIS/해상 데이터 백엔드가 없다(의도된 설계). 이 검수기는 그 사실을
강제한다: /dashboard-preview의 호르무즈 카드는 **데모 미리보기 + AIS 하한 추정**으로만
표기되어야 하며, 라이브 AIS 통합을 주장하거나 외부 AIS 소스/키를 호출해서는 안 된다.

완전 오프라인이다(네트워크/DB/발송 0건). 검사:
  A  호르무즈 카드 존재 + 풍부한 미리보기 요소(타일/막대/선종 분포/모식도)
  B  데모/preview 표기 — 값을 live 진실로 위장하지 않음
  C  AIS 하한 추정 경고(unique MMSI · 실제 통과량보다 낮을 수 있음 · 위성 AIS 미포함)
  D  시간 창 옵션(1시간/6시간/24시간/7일)
  E  라이브 AIS 통합 미주장 + 외부 AIS 소스/엔드포인트 미호출(app/·scripts/·templates/)
  F  프로덕션 일일 리포트(docs/daily)에 호르무즈 선박 수가 새지 않음
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates" / "dashboard_preview.html"
DOCS_LATEST = ROOT / "docs" / "daily" / "latest.html"
DOCS_OPERATOR = ROOT / "docs" / "daily" / "operator-latest.html"

# 라이브 AIS/해상 트래픽 제공자 호스트 — 미리보기는 이들 중 무엇도 호출하면 안 된다.
AIS_LIVE_HOSTS = (
    "marinetraffic.com", "aisstream.io", "vesselfinder.com", "myshiptracking.com",
    "datalastic.com", "spire.com", "fleetmon.com", "aishub.net", "api.vtexplorer.com",
)

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


def _hormuz_card(tpl: str) -> str:
    start = tpl.find("호르무즈 해협 관찰")
    if start < 0:
        return ""
    # 카드는 .card hz 블록 안 — 경고 ul 끝까지 넉넉히 슬라이스
    end = tpl.find("Hyundai E&C", start)
    if end < 0:
        end = tpl.find("현대건설 시장 반응", start)
    return tpl[start:end if end > 0 else min(len(tpl), start + 6000)]


def main() -> int:
    print(f"== verify_hormuz_maritime_snapshot @ {ROOT} ==")
    tpl = _read(TEMPLATE)
    if not check("0: dashboard_preview.html 로드", bool(tpl)):
        print("\nRESULT: FAIL (템플릿 없음)")
        return 1

    card = _hormuz_card(tpl)

    # A · 카드 + 풍부한 요소
    check("A1: 호르무즈 카드 존재", bool(card))
    check("A2: 메트릭 타일(현재 통항 중/대기·정박/평균 속도/AIS 신뢰도)",
          "현재 통항 중" in card and "대기/정박" in card
          and "평균 속도" in card and "AIS 신뢰도" in card)
    check("A3: 시간대별 통과 막대 차트", 'id="hzBars"' in card)
    # 선종 분포는 #hzVessels 컨테이너에 JS가 채운다 — 컨테이너는 카드 안, 라벨은 템플릿 JS에.
    check("A4: 선종 분포(유조선/LNG선/컨테이너·기타)",
          'id="hzVessels"' in card and "유조선" in tpl and "LNG선" in tpl
          and "컨테이너·기타" in tpl)
    check("A5: 해협 모식도(개념)", "해협 모식도" in card)

    # B · 데모/preview 표기 — live 위장 금지
    # 배지가 '데모 데이터'(label)와 'AIS 하한 추정 · proxy'(badge) 둘로 분리됨 — 둘 다 카드 안에 있으면 통과.
    check("B1: '데모 데이터' + 'AIS 하한 추정' 표기 (값을 live로 위장하지 않음)",
          "데모 데이터" in card and "AIS 하한 추정" in card)
    check("B2: 모든 수치가 데모 미리보기 고정값임을 명시",
          "데모 미리보기 고정값" in card or ("데모 미리보기" in card and "고정값" in card))
    check("B3: 통과 수에 '데모' 라벨", "데모" in card)

    # C · AIS 하한 추정 경고
    check("C1: 'AIS 하한 추정'", "AIS 하한 추정" in card)
    check("C2: 'unique MMSI' 기준", "unique MMSI" in card)
    check("C3: '실제 통과량보다 낮을 수 있음'", "실제 통과량보다 낮을 수 있" in card)
    check("C4: 'AIS 기반 하한 추정치'", "AIS 기반 하한 추정치" in card)
    check("C5: '위성 AIS 미포함 시 … 누락'",
          "위성 AIS 미포함" in card and "누락" in card)

    # D · 시간 창
    for win in ("1시간", "6시간", "24시간", "7일"):
        check(f"D: 시간 창 '{win}'", win in card)

    # E · 라이브 AIS 미주장 + 외부 소스 미호출
    check("E1: 라이브 AIS 통합 아님 명시",
          "라이브 AIS" in card and ("통합이 아" in card or "미연동" in card))
    check("E2: 라이브 소스/키 연동 전 프로덕션 값 미생성",
          "프로덕션 값을 생성하지 않" in card)
    self_path = Path(__file__).resolve()
    scan_targets = []
    for sub in ("app", "scripts", "templates"):
        for p in sorted((ROOT / sub).glob("**/*")):
            # 이 검수기 자신은 제외한다(여기서 호스트 목록을 '정의'하기 때문에 자기 탐지 방지).
            if p.is_file() and p.suffix in (".py", ".html", ".js", ".json") and p.resolve() != self_path:
                scan_targets.append(p)
    hit_hosts = []
    for p in scan_targets:
        txt = _read(p).lower()
        for host in AIS_LIVE_HOSTS:
            if host in txt:
                hit_hosts.append(f"{host} @ {p.relative_to(ROOT)}")
    check("E3: 외부 AIS 라이브 제공자 엔드포인트 미호출",
          not hit_hosts, "; ".join(hit_hosts) if hit_hosts else "none")
    # 라이브 AIS 백엔드 모듈 부재(정직한 미연동 — 가짜 백엔드 생성 금지)
    maritime_mods = [p.name for p in (ROOT / "app").glob("*.py")
                     if any(k in p.name.lower() for k in ("hormuz", "maritime", "ais", "vessel"))]
    check("E4: app/에 라이브 해상/AIS 백엔드 모듈 없음 (정직한 미연동)",
          not maritime_mods, ", ".join(maritime_mods) if maritime_mods else "none")

    # F · 프로덕션 docs/daily 비오염
    for label, path in (("latest", DOCS_LATEST), ("operator", DOCS_OPERATOR)):
        d = _read(path)
        leaked = "호르무즈" in d and ("AIS 관측 통과" in d or "통항 중" in d)
        check(f"F: docs/daily/{label}에 호르무즈 선박 수 미유출", not leaked)

    print()
    if _failures:
        print(f"RESULT: FAIL ({len(_failures)} 항목)")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("RESULT: PASS — 호르무즈 카드 정직성 검수 통과 (preview · 라이브 미연동)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
