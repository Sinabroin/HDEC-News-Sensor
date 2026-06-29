#!/usr/bin/env python3
"""D7-AA verifier — Hormuz lens relevance guard (no network, no DB, no secrets).

Proves that the Hormuz lens only catches articles that are *actually* about the Strait of
Hormuz: a direct mention (호르무즈 / Hormuz / Strait of Hormuz), OR a corroborating
combination of a geopolitics anchor (이란 / 중동 / 해협 / 걸프 …) AND a maritime/oil-risk
anchor (원유 / 유조선 / 해상운송 / 봉쇄 / 선박 / 군사 / 나포 …). Bare LNG / 중동 / 유가 / 해운
single keywords must NOT route an article into the Hormuz lens.

Two layers are checked:
  1. app.lens_queries.hormuz_relevant(text) — the policy predicate.
  2. scripts.build_static_dashboard._lens_for(sig) — the dashboard tagging that consumes it
     (the actual leak site: e.g. "UAE LNG 터미널 건설" used to match "LNG" → 호르무즈).

Usage:
    python3 scripts/verify_hormuz_lens_relevance.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import lens_queries  # noqa: E402

_failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    line = f"[{'PASS' if ok else 'FAIL'}] {name}"
    if detail:
        line += f" — {detail}"
    print(line)
    if not ok:
        _failures.append(name)
    return ok


# task spec 예시 + 견고성 보강 예시.
POSITIVE = [
    "이란, 호르무즈 해협 봉쇄 경고",                       # 직접 + geo∧risk
    "Strait of Hormuz shipping risk raises oil prices",   # 직접(영문)
    "Hormuz tensions: tanker attack near the strait",     # 직접(영문)
    "이란 유조선 나포로 원유 수송 차질 우려",              # geo(이란) ∧ risk(유조선·나포·원유)
    "걸프 해상 봉쇄 시 원유 수송 비상",                    # geo(걸프) ∧ risk(봉쇄·원유·해상)
]

NEGATIVE = [
    "중동 LNG 플랜트 발주 일정",      # geo만(중동) · LNG는 risk 앵커 아님
    "국제유가 상승, 수요 회복 기대",  # risk어(유가) 없음 + geo 없음 → 단순 유가
    "아시아 LNG 가격 변동",           # geo·risk 모두 없음
    "중동 건설 수주 호조 전망",       # geo만(중동) · risk 없음
    "이란 핵협상 재개 가능성",        # geo만(이란) · risk 없음
    "국내 정유사 정제마진 개선",      # geo·risk 모두 없음 (유가 계열이지만 호르무즈 아님)
]


def check_predicate() -> None:
    for t in POSITIVE:
        check(f"POS: hormuz_relevant True | {t[:34]}", lens_queries.hormuz_relevant(t) is True)
    for t in NEGATIVE:
        check(f"NEG: hormuz_relevant False | {t[:34]}", lens_queries.hormuz_relevant(t) is False)


def check_policy_shape() -> None:
    pol = lens_queries.hormuz_relevance_policy()
    check("policy: direct/geo/risk 앵커 비어있지 않음",
          bool(pol["direct"]) and bool(pol["geo_anchors"]) and bool(pol["risk_anchors"]))
    # 과도하게 넓은 단일 키워드(유가/LNG/지정학/보험료)는 어떤 앵커 목록에도 없어야 한다.
    all_anchors = {a.lower() for a in
                   pol["direct"] + pol["geo_anchors"] + pol["risk_anchors"]}
    for banned in ("유가", "국제유가", "lng", "지정학", "보험료"):
        check(f"policy: 과도한 단일어 '{banned}' 앵커 아님", banned not in all_anchors)
    # geo 단독·risk 단독으로는 통과하지 않는다 (둘 다 필요).
    check("policy: geo 단독으로는 미통과", lens_queries.hormuz_relevant("중동 동향") is False)
    check("policy: risk 단독으로는 미통과", lens_queries.hormuz_relevant("원유 가격") is False)


def check_dashboard_tagging() -> None:
    """실제 누수 지점 — 대시보드 _lens_for가 hormuz를 정확히 태깅하는가."""
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import build_static_dashboard as b  # noqa: WPS433
    except Exception as exc:  # noqa: BLE001
        check("tag: build_static_dashboard import", False, str(exc))
        return

    def tag(title: str) -> set:
        # category_label은 일부러 geo어("중동·해외")를 담아, 제목이 아닌 섹션 라벨로 hormuz가
        # 새지 않는지(raw 제목만 보는지) 함께 검증한다.
        sig = {"title": title, "category": "mideast_overseas",
               "category_label": "중동·해외 수주 환경", "radar_section": "macro",
               "source": "테스트", "snippet": ""}
        return set(b._lens_for(sig))

    # 누수 회귀: "UAE LNG 터미널 건설" 은 LNG로 hormuz에 들어가면 안 된다.
    leak = tag("UAE LNG 터미널 건설 프로젝트")
    check("tag: 'UAE LNG 터미널 건설' 은 hormuz 아님 (LNG 누수 차단)", "hormuz" not in leak,
          f"keys={sorted(leak)}")
    check("tag: '국제유가 상승…' 은 hormuz 아님", "hormuz" not in tag("국제유가 상승, 수요 회복 기대"))
    check("tag: category_label '중동·해외'만으로 hormuz 새지 않음",
          "hormuz" not in tag("국내 아파트 분양 일정 공지"))
    # 적격 기사: 직접/앵커 조합은 hormuz로 태깅된다.
    check("tag: '이란, 호르무즈 해협 봉쇄 경고' 은 hormuz", "hormuz" in tag("이란, 호르무즈 해협 봉쇄 경고"))
    check("tag: '이란 유조선 나포 원유 수송 차질' 은 hormuz",
          "hormuz" in tag("이란 유조선 나포로 원유 수송 차질 우려"))


def main() -> int:
    print(f"== verify_hormuz_lens_relevance (D7-AA) @ {ROOT} ==")
    check_predicate()
    check_policy_shape()
    check_dashboard_tagging()
    print()
    if _failures:
        print(f"RESULT: FAIL ({len(_failures)} 항목)")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("RESULT: PASS — 호르무즈 렌즈는 직접 언급 또는 geo∧risk 앵커일 때만 태깅 (D7-AA)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
