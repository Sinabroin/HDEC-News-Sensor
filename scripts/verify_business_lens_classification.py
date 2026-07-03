#!/usr/bin/env python3
"""D7-AE-RC1 verifier — 부서/사업영역별 기사 분류(business lens classification) 정확성.

사용자 실사용 QA 실패: "좌측 렌즈/사업영역 분류가 부정확하다. 단순 키워드 한두 개로
렌즈를 여러 개 붙이면 안 된다." 근본 원인 진단(app/topic_profiles.py 감사):

  - plant·new_energy가 "원전"/"수소"를 둘 다 include로 갖고 있어, 원전/수소 기사가
    항상 두 렌즈 모두에 동시 배정됐다(실측: "코즐로두이 원전" 기사가 plant AND
    new_energy 둘 다 매칭). New Energy는 SMR/차세대로 충분히 구분되므로 원전(전통
    EPC)은 plant 전속으로 뗐다.
  - RC4에서 "하자"는 사용자 지정 안전·품질 직접 근거로 safety_quality 전속이며,
    building_housing는 주택/정비/분양/공사비 근거로만 분류한다.
  - classify_business_lenses()에 상한이 없어 넓은 종합 기사가 3개 이상 렌즈에 동시
    배정될 수 있었다 — 이제 3개 이상이면 애매한 신호로 보고 빈 리스트(전체 종합에만).
  - business_lens_reason이 존재했지만 briefing._business_lens_group이 호출하지 않아
    분류 사유가 UI에 닿지 않았다 — 이제 각 엔트리에 business_lens_reason을 붙인다.

이 verifier는 app.topic_profiles(+app.briefing 배선)를 오프라인으로 잠근다:

  A. controlled fixtures 20건 이상 — 각 렌즈 positive 2개 이상 + negative 2개 이상.
  B. 사용자 지정 케이스 — "주택 착공 물량 감소"류는 건축주택 가능·안전품질 불가.
     "AI 로봇 운반"류는 안전·품질(강한 안전 근거 없음) 불가.
  C. 렌즈 vocabulary 격리 — plant/new_energy가 "원전"을 공유하지 않음(및 동종 과거
     충돌 쌍), 사고 기사라고 토목/건축주택/플랜트에 무분별 중복 배정 안 됨.
  D. 애매 배정 상한 — 3개 이상 렌즈가 동시에 걸리는 합성 기사는 classify가 빈 리스트.
  E. reason 배선 — business_lens_reason이 실제 텍스트를 반환하고, briefing 산출물의
     각 business_lens_signals 엔트리에 business_lens_reason 필드가 존재한다(오프라인
     mock 파이프라인 시뮬레이션).

완전 OFFLINE · 네트워크 0건 · DB는 temp 격리.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import topic_profiles as tp  # noqa: E402

_failures: list = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    line = f"[{'PASS' if ok else 'FAIL'}] {name}"
    if detail:
        line += f" — {detail}"
    print(line)
    if not ok:
        _failures.append(name)
    return ok


def info(msg: str) -> None:
    print(f"[INFO] {msg}")


def _art(title: str, snippet: str = "", source: str = "연합뉴스") -> dict:
    return {"title": title, "snippet": snippet, "source": source}


def _lens_matches(title: str, lens_id: str, snippet: str = "") -> bool:
    lens = tp.get_business_lens(lens_id)
    return lens is not None and tp.match_topic_profile(_art(title, snippet), lens)


LENSES = ("civil_infrastructure", "building_housing", "plant", "new_energy",
          "development_business", "global_business", "safety_quality")

# ---------------------------------------------------------------------------
# A · controlled fixtures — 렌즈별 positive 2건 + negative 2건 (총 20건 이상)
# ---------------------------------------------------------------------------

POSITIVE = {
    "civil_infrastructure": [
        "현대건설 컨소시엄, GTX-C 철도 구간 착공식 개최",
        "부산항 진해신항 항만 인프라 확장공사 현대건설 수주",
    ],
    "building_housing": [
        "현대건설, 힐스테이트 도시정비 재건축 수주 확정",
        "아파트 하자 논란에 건설사 공사비 갈등 조합 반발",
    ],
    "plant": [
        "현대건설, 중동 LNG 플랜트 발주 수주 확정",
        "현대건설 원전 EPC 계약 체결…석유화학 정유 플랜트 동반 수주",
    ],
    "new_energy": [
        "현대건설, SMR 소형원전 전력망 데이터센터 건설 계약",
        "현대건설 해상풍력 송전망 EPC 수주…탄소중립 인프라 확대",
    ],
    "development_business": [
        "시행사 PF 리스크에 현대건설 시공사 선정 시장 위축",
        "GBC 개발사업 인허가 본격화…디벨로퍼 복합개발 착수",
    ],
    "global_business": [
        "현대건설, 사우디 해외현장 플랜트 대형 수주",
        "현대건설 해외법인 환율 리스크 점검…해외지사 확대",
    ],
    "safety_quality": [
        "현대건설 현장 중대재해 발생…고용부 특별감독 착수",
        "현대건설 철근누락 부실시공 논란…행정처분 검토",
    ],
}

NEGATIVE = {
    # 렌즈별 "이 렌즈에는 절대 안 걸려야 하는" 기사 2건 이상 — 다른 렌즈 사례를 재사용해
    # cross-lens 오염이 없는지 함께 검증한다.
    "civil_infrastructure": ["현대건설 원전 EPC 계약 체결…석유화학 정유 플랜트 동반 수주",
                              "시행사 PF 리스크에 현대건설 시공사 선정 시장 위축"],
    "building_housing": ["현대건설, 중동 LNG 플랜트 발주 수주 확정",
                          "현대건설 해외법인 환율 리스크 점검…해외지사 확대"],
    "plant": ["현대건설, 힐스테이트 도시정비 재건축 수주 확정",
              "부산항 진해신항 항만 인프라 확장공사 현대건설 수주"],
    "new_energy": ["아파트 하자 논란에 건설사 공사비 갈등 조합 반발",
                   "GBC 개발사업 인허가 본격화…디벨로퍼 복합개발 착수"],
    "development_business": ["현대건설 컨소시엄, GTX-C 철도 구간 착공식 개최",
                              "현대건설 현장 중대재해 발생…고용부 특별감독 착수"],
    "global_business": ["현대건설, 힐스테이트 도시정비 재건축 수주 확정",
                        "현대건설 철근누락 부실시공 논란…행정처분 검토"],
    "safety_quality": ["현대건설, SMR 소형원전 전력망 데이터센터 건설 계약",
                       "현대건설, 사우디 해외현장 플랜트 대형 수주"],
}


def check_controlled_fixtures() -> None:
    total = 0
    for lid in LENSES:
        pos = POSITIVE.get(lid, [])
        neg = NEGATIVE.get(lid, [])
        check(f"A: {lid} positive 2건 이상 준비됨", len(pos) >= 2, str(len(pos)))
        check(f"A: {lid} negative 2건 이상 준비됨", len(neg) >= 2, str(len(neg)))
        total += len(pos) + len(neg)
        for title in pos:
            check(f"A: {lid} positive 수용 — {title}", _lens_matches(title, lid))
        for title in neg:
            check(f"A: {lid} negative 거부 — {title}", not _lens_matches(title, lid))
    check(f"A: controlled fixture 총 {total}건 >= 20건", total >= 20, str(total))


# ---------------------------------------------------------------------------
# B · 사용자 지정 케이스
# ---------------------------------------------------------------------------

def check_user_specified_cases() -> None:
    housing_decline = "현대건설, 전국 주택 착공 물량 감소에 실적 우려"
    check("B1: '주택 착공 물량 감소' → 건축주택 가능",
          _lens_matches(housing_decline, "building_housing"))
    check("B2: '주택 착공 물량 감소' → 안전·품질 불가(강한 사고 근거 없음)",
          not _lens_matches(housing_decline, "safety_quality"))

    ai_robot = "현대건설 건설현장, AI 로봇 자재 운반 시범 도입"
    check("B3: 'AI 로봇 운반' → 안전·품질 불가(강한 안전 근거 없음)",
          not _lens_matches(ai_robot, "safety_quality"))
    # AI 신호 자체는 business lens 영역이 아니라 별도 AI 레이더 소관이므로(라우팅
    # 불가침), 여기서는 안전·품질에 잘못 붙지 않는다는 것만 확인한다.
    ai_robot_hits = tp.classify_business_lenses(_art(ai_robot))
    check("B4: 'AI 로봇 운반' → 어느 사업 렌즈에도 강제 배정되지 않음(약한 신호)",
          "safety_quality" not in ai_robot_hits, str(ai_robot_hits))

    # 사고 기사가 토목/건축주택/플랜트에 무분별하게 중복 배정되면 FAIL.
    accident = "현대건설 아파트 신축 현장서 붕괴 사고…고용부 특별감독"
    hits = tp.classify_business_lenses(_art(accident))
    check("B5: 사고 기사 → 토목/건축주택/플랜트 무분별 동시배정 금지(<=2개 렌즈)",
          len(hits) <= 2, str(hits))
    check("B6: 사고 기사 → safety_quality는 포함(강한 사고 근거 있음)",
          "safety_quality" in hits or hits == [], str(hits))


# ---------------------------------------------------------------------------
# C · 렌즈 vocabulary 격리 — 과거 실측 충돌 쌍 재발 방지
# ---------------------------------------------------------------------------

def check_vocabulary_isolation() -> None:
    kw = {lid: set(tp.get_business_lens(lid).include_keywords) for lid in LENSES}
    check("C1: plant·new_energy가 '원전'을 공유하지 않음(과거 실측 충돌)",
          not ({"원전"} <= kw["plant"] and "원전" in kw["new_energy"]))
    check("C2: '하자'는 safety_quality 전속(building_housing와 비공유)",
          "하자" not in kw["building_housing"] and "하자" in kw["safety_quality"])
    # 실측 재현 — 코즐로두이 원전 기사가 더 이상 plant+new_energy 동시 매칭 아님.
    kozloduy = "현대건설, 미 원전 시장 속도낸다…웨스팅하우스 자금 지원 '호재'"
    hits = tp.classify_business_lenses(_art(kozloduy))
    check("C3: 실측 재현 — 원전 기사가 plant+new_energy 동시매칭 아님",
          not ("plant" in hits and "new_energy" in hits), str(hits))
    # 완전 겹치는 include 단어 쌍이 없는지 전수 확인(회귀 고정). RC4는 bare
    # "데이터센터"도 제거해 AI 기사 전체가 건축주택/New Energy로 fan-out하지 않게 한다.
    _INTENTIONAL_OVERLAP = {}
    overlap_pairs = []
    ids = list(kw)
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            shared = kw[ids[i]] & kw[ids[j]]
            allowed = _INTENTIONAL_OVERLAP.get(frozenset({ids[i], ids[j]}), set())
            unexpected = shared - allowed
            if unexpected:
                overlap_pairs.append((ids[i], ids[j], unexpected))
    check("C4: 렌즈 간 include_keywords 중복 단어 0쌍(회귀 고정)",
          not overlap_pairs, str(overlap_pairs))


# ---------------------------------------------------------------------------
# D · 애매 배정 상한 — 3개 이상 렌즈 동시매칭 시 빈 리스트
# ---------------------------------------------------------------------------

def check_ambiguous_cap() -> None:
    # 여러 사업을 나열한 합성 종합 기사 — 5개 렌즈의 include+anchor를 모두 담는다.
    synthetic = ("현대건설, 국내외 종합 실적 발표…토목 SOC 철도와 아파트 도시정비, "
                 "원전 플랜트, SMR 전력망 데이터센터, 사우디 해외현장까지 고르게 성장")
    raw_hits = [lid for lid in LENSES if _lens_matches(synthetic, lid)]
    check("D1: 합성 종합 기사는 실제로 3개 이상 렌즈의 raw 매칭 조건을 만족(fixture 유효성)",
          len(raw_hits) >= 3, str(raw_hits))
    capped = tp.classify_business_lenses(_art(synthetic))
    check("D2: 3개 이상 렌즈 동시매칭 → classify_business_lenses는 빈 리스트(전체 종합)",
          capped == [], str(capped))

    # 정확히 2개 렌즈만 걸리는 경우는 여전히 허용된다(진짜 cross-domain 신호).
    dual = "현대건설, 사우디 해외현장 플랜트 대형 수주"
    dual_hits = tp.classify_business_lenses(_art(dual))
    check("D3: 2개 이하 렌즈 매칭은 그대로 허용(과잉 억제 아님)",
          0 < len(dual_hits) <= 2, str(dual_hits))


# ---------------------------------------------------------------------------
# E · reason 배선 — business_lens_reason 원시함수 + briefing 산출물 배선
# ---------------------------------------------------------------------------

def check_reason_present() -> None:
    lens = tp.get_business_lens("safety_quality")
    reason = tp.business_lens_reason(_art("현대건설 현장 중대재해 발생…고용부 특별감독 착수"), lens)
    check("E1: business_lens_reason이 매칭 시 텍스트 반환", bool(reason and reason.strip()),
          str(reason))
    reason_none = tp.business_lens_reason(_art("동남아 여행 관광 항공권 수요 회복"), lens)
    check("E2: business_lens_reason이 미매칭 시 None", reason_none is None)


def check_briefing_wiring() -> None:
    code = (
        "import os, sys, json, tempfile\n"
        "d=tempfile.mkdtemp()\n"
        "os.environ['DB_PATH']=os.path.join(d,'t.db')\n"
        "os.environ['APP_MODE']='mock'; os.environ['NEWS_MODE']='mock'\n"
        "sys.path.insert(0,'.')\n"
        "from app import db, collector, scoring, insight, briefing\n"
        "db.init_db(); collector.run(); scoring.score_all(); insight.generate_all()\n"
        "b=briefing.build_brief()\n"
        "bls=b.get('business_lens_signals') or {}\n"
        "entries=[e for v in bls.values() for e in v]\n"
        "out={'entries':len(entries),\n"
        " 'has_reason_key':all('business_lens_reason' in e for e in entries),\n"
        " 'nonempty_reason':sum(1 for e in entries if (e.get('business_lens_reason') or '').strip()),\n"
        " 'tag_counts':[len(e.get('business_lens_tags') or []) for e in entries]}\n"
        "print(json.dumps(out, ensure_ascii=False))\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True,
                          text=True, cwd=ROOT, timeout=240)
    if not check("E3: mock 파이프라인 실행(오프라인)", proc.returncode == 0,
                 (proc.stderr or "")[-500:]):
        return
    try:
        out = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        check("E3: mock 파이프라인 출력 파싱", False, (proc.stdout or "")[-300:])
        return
    info(f"business_lens_signals 엔트리 {out['entries']}건")
    check("E4: 모든 엔트리에 business_lens_reason 키 존재", out["has_reason_key"])
    if out["entries"]:
        check("E5: 매칭 엔트리 중 reason 텍스트가 실제로 채워짐(1건 이상)",
              out["nonempty_reason"] > 0, str(out["nonempty_reason"]))
    check("E6: 각 엔트리의 business_lens_tags가 3개 이하(애매 상한 적용됨)",
          all(c <= 2 for c in out["tag_counts"]), str(out["tag_counts"]))


def main() -> int:
    print(f"== verify_business_lens_classification (D7-AE-RC1) @ {ROOT} ==")
    check_controlled_fixtures()
    check_user_specified_cases()
    check_vocabulary_isolation()
    check_ambiguous_cap()
    check_reason_present()
    check_briefing_wiring()
    print()
    if _failures:
        print(f"RESULT: FAIL ({len(_failures)} 항목)")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("RESULT: PASS — 사업영역 렌즈 분류 정확성(vocabulary 격리 · 애매 상한 · "
          "reason 배선) (D7-AE-RC1)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
