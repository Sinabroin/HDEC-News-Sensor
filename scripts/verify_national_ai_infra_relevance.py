#!/usr/bin/env python3
"""D7-AB verifier — national-grade AI/semiconductor/datacenter/power-infra investment
relevance gate (no network, no DB, no secrets).

Mirrors the Hormuz guard philosophy: an article is classified as a *national AI-infra
investment* signal ONLY when three independent axes co-occur —
  · actor anchor : 정부 / 대통령 / 산업부 / 과기정통부 / 삼성 / SK / 지자체 / 비수도권 …
  · event anchor : 투자 / 발표 / 조성 / 협약 / 메가프로젝트 / 클러스터 / 착공 / 구축 …
  · infra anchor : AI 데이터센터 / 반도체 클러스터 / 전력 인프라 / 변전 / EPC / 산업단지 …
True ⇔ actor AND event AND infra (그리고 negative 가드 미해당). No single keyword
(정부 · AI · 데이터센터) and no single person name / "1000조" passes on its own — the gate is
NOT a keyword OR. boost(Executive Read 후보 승격)는 relevant 의 STRICT 부분집합이다
(relevant ∧ boost combo), 단독 통과 조건이 아니다.

Layers checked (all OFFLINE):
  1. app.lens_queries.national_ai_infra_relevant(text)  — the 3-axis gate predicate.
  2. app.lens_queries.national_ai_infra_boost(text)      — the ranking-boost flag (⊆ relevant).
  3. app.lens_queries.collection_query_groups()          — minimal candidate-acquisition queries.
  4. scripts.build_static_dashboard._signal_rank_key     — the consumption: a boosted signal
     outranks an equal-score non-boosted one, but never overrides a higher score.

Usage:
    python3 scripts/verify_national_ai_infra_relevance.py
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


# task §7 positive regression fixtures + 견고성 보강(인물명 없이 구조만으로 통과).
POSITIVE = [
    "대통령 삼성 SK 1000조 AI 데이터센터 반도체 클러스터 투자",
    "삼성 10년간 대규모 투자 AI 데이터센터 충남 아산",
    "정부 메가프로젝트 반도체 AI 데이터센터 전력 인프라",
    "비수도권 반도체 클러스터 데이터센터 전력망 구축",
    "산업부, 용인 반도체 클러스터 전력 인프라 조성 협약",      # 인물명 0 · actor+event+infra
    "지자체, 데이터센터 산업단지 유치 위해 변전소 착공",        # 지자체 actor · 변전(소) infra
]

# task §6 negative guard fixtures + 견고성(부분 충족·소비재 가드).
NEGATIVE = [
    "삼성전자 스마트폰 판매량",
    "삼성전자 주가 단순 등락",
    "대통령 지지율 여론조사",
    "SK 야구 경기 결과",
    "SK텔레콤 요금제 출시",
    "일반 AI 서비스 출시 기사",
    "정부 국정 운영 방향 발표",                         # actor+event, infra 없음
    "데이터센터 전력 수요 급증 전망",                   # infra만(actor·event 없음)
    "삼성전자, 갤럭시 신제품 데이터센터 투자 발표",       # 3축 충족하지만 negative(갤럭시) 가드
]

# task §5 boost(Executive Read 후보) — relevant ∧ boost combo.
BOOST_TRUE = [
    "대통령 삼성 SK 1000조 AI 데이터센터 반도체 클러스터 투자",  # 반도체 클러스터 + 1000조
    "삼성 10년간 대규모 투자 AI 데이터센터 충남 아산",            # 삼성 + 대규모 투자 + 데이터센터
    "정부 메가프로젝트 반도체 AI 데이터센터 전력 인프라",          # AI 데이터센터 + 전력 인프라
    "정부 발표 메가프로젝트 산업단지 EPC 전력 구축",              # 정부 발표 + 메가프로젝트 + EPC/전력
]
# relevant 이지만 boost combo 미충족 → boost False (boost는 단독 통과 조건이 아님, task §4).
BOOST_FALSE_BUT_RELEVANT = [
    "비수도권 반도체 클러스터 데이터센터 전력망 구축",  # relevant=True, 투자규모/AI DC 조합 없음
    "정부 데이터센터 산업단지 조성 발표",               # relevant=True, boost combo 미해당
]


def check_predicate() -> None:
    for t in POSITIVE:
        check(f"POS relevant True | {t[:30]}", lens_queries.national_ai_infra_relevant(t) is True)
    for t in NEGATIVE:
        check(f"NEG relevant False | {t[:30]}", lens_queries.national_ai_infra_relevant(t) is False)


def check_boost() -> None:
    for t in BOOST_TRUE:
        check(f"BOOST True | {t[:30]}", lens_queries.national_ai_infra_boost(t) is True)
    for t in BOOST_FALSE_BUT_RELEVANT:
        rel = lens_queries.national_ai_infra_relevant(t)
        bo = lens_queries.national_ai_infra_boost(t)
        check(f"BOOST subset relevant∧¬boost | {t[:22]}", rel is True and bo is False,
              f"rel={rel} boost={bo}")
    # boost ⊆ relevant — boost True면 반드시 relevant True (boost는 relevant 위의 승격).
    for t in BOOST_TRUE + NEGATIVE + POSITIVE:
        if lens_queries.national_ai_infra_boost(t):
            check(f"BOOST⇒relevant | {t[:22]}", lens_queries.national_ai_infra_relevant(t) is True)


def check_3axis_isolation() -> None:
    cases = [
        ("정부 국정 운영 발표", False),                 # actor+event, infra 없음
        ("정부 데이터센터 청사", False),                 # actor+infra, event 없음
        ("투자 발표 데이터센터 전력 인프라", False),       # event+infra, actor 없음
        ("정부", False),
        ("투자 발표", False),
        ("AI 데이터센터 전력 인프라", False),             # infra만
        ("정부 데이터센터 투자", True),                  # 3축 최소 충족
    ]
    for t, want in cases:
        check(f"3axis {'T' if want else 'F'} | {t[:24]}",
              lens_queries.national_ai_infra_relevant(t) is want)


def check_no_overfit() -> None:
    # 인물명·'1000조' 단독은 통과 조건이 아니다(과적합 금지, task §4).
    check("no-overfit: '1000조 투자' 단독 미통과",
          lens_queries.national_ai_infra_relevant("1000조 투자") is False)
    check("no-overfit: 인물명 없이 generic 구조 통과",
          lens_queries.national_ai_infra_relevant("정부, AI 데이터센터 전력 인프라 투자 발표") is True)
    pol = lens_queries.national_ai_infra_relevance_policy()
    alla = set(pol["actor_anchors"])
    for nm in ("이재명", "윤석열", "이재용", "최태원"):
        check(f"no-overfit: 인물명 '{nm}' actor 앵커 아님", nm not in alla)


def check_sk_ascii_trap() -> None:
    # 'SK' actor 앵커가 영문 단어 내부('risk'/'task'/'desk'/'asked')에 오탐되지 않는다.
    for t in ["risk and task management desk update",
              "Asked investors about a new datacenter"]:
        check(f"SK-trap relevant False | {t[:28]}",
              lens_queries.national_ai_infra_relevant(t) is False)


def check_infra_specificity() -> None:
    # 과도하게 넓은 단일어(인프라/SOC/AI/전력 단독)는 infra 앵커가 아니어야 한다 — 일반
    # SOC/인프라 기사가 국가급 AI 인프라로 새지 않게(특정 AI·반도체·DC·전력 인프라만).
    pol = lens_queries.national_ai_infra_relevance_policy()
    infra = {w.lower() for w in pol["infra_anchors"]}
    for banned in ("인프라", "soc", "ai", "전력"):
        check(f"infra: 과도한 단일어 '{banned}' 앵커 아님", banned not in infra)
    check("infra: generic 'SOC 예산·인프라 건설투자' 미통과",
          lens_queries.national_ai_infra_relevant(
              "정부, 하반기 SOC 예산 조기 집행 인프라 건설투자 확대") is False)


def check_policy_shape() -> None:
    pol = lens_queries.national_ai_infra_relevance_policy()
    check("policy: actor/event/infra/negatives 비어있지 않음",
          all(pol[k] for k in ("actor_anchors", "event_anchors", "infra_anchors", "negatives")))
    check("policy: boost_combos 그룹 구조(AND of ORs)",
          bool(pol["boost_combos"]) and all(
              isinstance(c, list) and all(isinstance(g, list) and g for g in c)
              for c in pol["boost_combos"]))


def check_collection_queries() -> None:
    groups = {g["name"]: g for g in lens_queries.collection_query_groups()}
    ok = check("collect: national_ai_infra 후보 그룹 존재", "national_ai_infra" in groups)
    if ok:
        qs = groups["national_ai_infra"]["queries"]
        check("collect: 후보 확보용 최소 쿼리(>=3, 무차별 OR 아님)", 3 <= len(qs) <= 8, f"n={len(qs)}")
    # D7-AC: 전용 쿼리가 전역 cap에 굶지 않도록 우선순위 preflight 그룹이어야 한다(default
    # priority면 fetch order 끝이라 query_audit 0건 → 실제 수집 안 됨). coverage starvation 잠금.
    try:
        from app import live_collector as _lc
        check("collect: national_ai_infra가 우선순위 preflight 그룹 (coverage starvation 방지)",
              "national_ai_infra" in _lc.PRIORITY_LENS_GROUPS)
    except Exception as exc:  # noqa: BLE001
        check("collect: live_collector.PRIORITY_LENS_GROUPS import", False, str(exc))


def check_rank_boost() -> None:
    """소비 지점 — _signal_rank_key가 boost 신호를 동점에서 앞세우되 점수는 뒤집지 않는가."""
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import build_static_dashboard as b  # noqa: WPS433
    except Exception as exc:  # noqa: BLE001
        check("rank: build_static_dashboard import", False, str(exc))
        return
    boosted = {"title": "정부 메가프로젝트 반도체 AI 데이터센터 전력 인프라", "final_score": 3.0}
    plain = {"title": "일반 건설 자재 시황 동향 정리", "final_score": 3.0}
    high = {"title": "일반 건설 자재 시황 동향 정리", "final_score": 4.5}
    check("rank: 동점에서 boost 신호가 앞선다",
          b._signal_rank_key(boosted) < b._signal_rank_key(plain))
    check("rank: 더 높은 점수는 boost를 이긴다(override 아님)",
          b._signal_rank_key(high) < b._signal_rank_key(boosted))


def main() -> int:
    print(f"== verify_national_ai_infra_relevance (D7-AB) @ {ROOT} ==")
    check_predicate()
    check_boost()
    check_3axis_isolation()
    check_no_overfit()
    check_sk_ascii_trap()
    check_infra_specificity()
    check_policy_shape()
    check_collection_queries()
    check_rank_boost()
    print()
    if _failures:
        print(f"RESULT: FAIL ({len(_failures)} 항목)")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("RESULT: PASS — 국가급 AI 인프라 투자 신호는 actor∧event∧infra 3축일 때만 분류 (D7-AB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
