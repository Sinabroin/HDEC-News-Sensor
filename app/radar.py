"""Radar 도메인 — 저장된 기사/점수에서 임원용 레이더 섹션을 파생한다 (P0-C1.9).

briefing.py가 build_brief에서 호출하는 표시 전용 분류기다:
- 기사를 ai / risk_regulation / business_overseas / macro_economy / other 중
  하나의 primary radar_section으로 분류한다.
- 리스크/규제 기사에는 risk_priority_score·risk_reason·risk_radar_label·
  regulatory_relevance를 부여해, 종합 중요도(final_score)가 낮아도 리스크 레이더에
  드러나게 한다 (중대재해·규제가 9항목 가중합에서 희석되는 문제 보정).

분류 입력 계약 (P0-D3A — false-positive guard):
- AI / 거시 / 수주·해외(BUSINESS) primary 판정은 **raw 제목+스니펫만** 본다.
  collector가 50% 토큰 매칭으로 붙이는 생성 topic_candidates(예: '현대건설'만 맞아도
  '현대건설 데이터센터'가 붙음)는 분류 증거로 쓰지 않는다 — 주입된 '데이터센터'가 종전·
  주가 기사를 AI로 끌어올리던 오탐을 차단한다.
- AI 섹션은 raw에서 추출한 actor/event/infra/exclusion 교차 신호가 있어야 한다.
  title/snippet은 입력 데이터이며 특정 전체 제목이나 생성 topic_candidates를 고정
  분류 키로 쓰지 않는다.

경계 (briefing 도메인과 동일한 '파생 전용' 원칙):
- DB 접근/쓰기 없음, 네트워크 없음.
- 점수(final_score)·등급(alert_grade)을 재계산하지 않는다 — 저장된 값을 읽어
  분류/우선도만 파생한다.
"""

import json

from app import article_quality, radar_signals

# ---- radar_section 키 (단일 소스) ----
AI = "ai"
RISK = "risk_regulation"
BUSINESS = "business_overseas"
MACRO = "macro_economy"
OTHER = "other"

SECTIONS = [AI, RISK, BUSINESS, MACRO, OTHER]

# UI·리포트가 그대로 노출하는 섹션 라벨 (표시 전용 단일 소스)
# P0-C1.10: 임원 친화 표현으로 정리 — 'AI 레이더'는 'AI 관련'으로, 나머지는 '레이더/신호' 군더더기 제거.
RADAR_LABELS = {
    AI: "AI 관련",
    RISK: "리스크·규제",
    BUSINESS: "수주·해외",
    MACRO: "거시경제",
    OTHER: "기타",
}

# 강한 리스크 액션 (P0-C1.11) — 제재/사고 등 실제 리스크-액션이 있을 때만 단독 risk.
# '국토부/고용노동부/정부/안전/점검' 단독으로는 더 이상 리스크로 보지 않는다 —
# 혁신기술·정책·스마트건설 기사가 부처명만으로 리스크로 오분류되던 문제를 보정한다.
RISK_ACTION_STRONG = [
    "중대재해", "중대재해처벌법", "사망사고", "사망 사고", "산업재해", "산재",
    "안전사고", "붕괴", "인명피해",
    "벌점", "영업정지", "영업 정지", "입찰제한", "입찰 제한",
    "과징금", "행정처분", "행정 처분", "특별감독", "사전통보", "사전 통보",
    "면허취소", "등록말소", "하자", "부실시공", "압수수색", "품질 점검",
    "품질점검", "소송",
]
# 약한 리스크/규제 신호 — 산업 anchor(건설/현장/시공 등)가 함께 있을 때만 risk.
# 일반 규제·정책·법안 뉴스가 산업 맥락 없이 리스크로 오르지 않게 한다.
RISK_REG_WEAK = [
    "제재", "처벌", "처벌 강화", "위반", "법 위반", "고발", "소송",
    "조사", "감사", "감독", "책임", "규제", "법안", "개정안", "시행령",
    "특별법", "건설안전특별법", "의무화", "기준 강화", "품질 논란",
    "품질관리", "품질 관리",
]
# AI/DC/SMR 산업의 시장·발주 환경을 여는 법제 신호. 아래 토큰만 있고 구체적
# 안전·제재·의무·품질 리스크가 없으면 AI 실행/시장 신호로 유지한다. 예:
# "AI 데이터센터 특별법 시행 ... 전력망·냉각 경쟁". 반대로 "AI 안전관리 의무화"는
# RISK_POLICY_IMPACT_TERMS가 함께 잡혀 계속 risk_regulation이 우선한다.
AI_POLICY_ENABLEMENT_TERMS = {"법안", "개정안", "시행령", "특별법"}
RISK_POLICY_IMPACT_TERMS = [
    "안전", "재해", "사고", "제재", "처벌", "위반", "고발", "소송",
    "조사", "감사", "감독", "책임", "규제", "의무화", "기준 강화",
    "품질", "하자", "부실", "입찰제한", "입찰 제한", "영업정지", "영업 정지",
]
INDUSTRY_KEYWORDS = [
    "건설", "건설사", "건설업", "건설현장", "현장", "시공", "epc", "플랜트",
    "공사", "토목", "건축", "인프라", "기반시설", "현대건설", "데이터센터",
    "수주", "발주",
]

# 거시경제 변수 — FX/유가/금리/원자재 등.
MACRO_KEYWORDS = [
    "환율", "금리", "유가", "원자재", "철강", "시멘트", "fomc", "달러",
    "인플레이션", "공급망", "파이낸싱", "물가", "채권", "국채", "증시",
    "코스피", "kospi", "원/달러",
]
# 구체적 수주/사업 이벤트 — 거시 변수 기사를 'pure macro'에서 가른다.
ORDER_EVENT_KEYWORDS = [
    "수주", "발주", "우선협상", "낙찰", "본계약", "착공", "준공", "수주액",
]
# 해외·국내 사업 신호 — 발주환경/프로젝트 파이프라인.
BUSINESS_GEO_KEYWORDS = [
    "중동", "사우디", "uae", "네옴", "카타르", "쿠웨이트", "이라크", "산유국",
    "해외", "해외건설", "해외수주", "유럽", "체코", "동남아", "북미", "수출",
    "플랜트", "현대건설", "재건축", "재개발", "도시정비", "soc", "건설투자",
    "인프라 투자", "프로젝트",
]

# 키워드 미스 시 저장된 insight 카테고리로 보정 (general → OTHER).
_CATEGORY_SECTION = {
    "hdec": BUSINESS,
    "dc_power": AI,
    "competitor": BUSINESS,
    "safety": RISK,
    "macro": MACRO,
    "mideast_overseas": BUSINESS,
    "gov": BUSINESS,
    "smart_const": AI,
    "general": OTHER,
}

# 심각도 → 리스크 우선도 floor + 표시 라벨 (위에서부터 먼저 매칭).
# floor는 종합 중요도(final_score)가 가중합에서 희석돼도 리스크 레이더 상단에 두기 위한 하한.
# P0-C1.11: 부처명(국토부 등)·'안전' 단독 floor를 제거하고 실제 리스크-액션만 floor한다.
_SEVERITY_FLOOR = [
    (["중대재해", "중대재해처벌법", "사망사고", "사망 사고", "산업재해", "산재",
      "안전사고", "붕괴", "인명피해"], 4.3, "중대재해"),
    (["영업정지", "영업 정지", "입찰제한", "입찰 제한", "벌점", "과징금",
      "행정처분", "행정 처분", "특별감독", "사전통보", "사전 통보",
      "면허취소", "등록말소", "압수수색"], 3.8, "제재"),
    (["하자", "부실시공", "품질 점검", "품질점검", "품질 논란"], 3.6, "품질"),
    (["제재", "처벌", "고발", "소송", "위반", "법 위반", "조사", "감사",
      "감독", "규제", "법안", "개정안", "시행령", "특별법", "의무화"], 3.3, "규제"),
]

# P0-C1.10: 임원 메모 스타일 — 종결어미('~필요합니다/~합니다') 없이 명사형 구절로.
_RISK_REASON = {
    "중대재해": "입찰 자격·평판·안전관리 리스크 확인",
    "제재": "입찰 자격·평판·안전관리 리스크 확인",
    "품질": "품질·하자 이슈 — 평판·보수비·수주 리스크 확인",
    "규제": "입찰 자격·평판·안전관리 리스크 확인",
    "안전": "입찰 자격·평판·안전관리 리스크 확인",
}


def _hits(text: str, keywords: list[str]) -> bool:
    return any(kw in text for kw in keywords)


def _row_text(row: dict) -> str:
    """제목 + 스니펫 + 토픽 후보를 한 덩어리 소문자 텍스트로 (risk 우선도 floor 입력)."""
    parts = [row.get("title") or "", row.get("snippet") or ""]
    try:
        topics = json.loads(row.get("topic_candidates") or "[]")
        parts += [t for t in topics if isinstance(t, str)]
    except (ValueError, TypeError):
        pass
    return " ".join(parts).lower()


def _raw_text(row: dict) -> str:
    """제목 + 스니펫만 소문자로 (생성 topic_candidates 제외 — primary 분류 입력, P0-D3A).

    collector가 50% 토큰 매칭으로 붙이는 topic_candidates는 보지 않는다 — 주입된 '데이터센터'
    등이 종전·재무·주가 기사를 AI로 끌어올리던 오탐을 차단한다."""
    return " ".join([row.get("title") or "", row.get("snippet") or ""]).lower()


def classify_section(row: dict, category_key: str | None = None) -> str:
    """기사 1건의 primary radar_section을 정한다 (표시 전용 파생값).

    AI/거시/수주·해외 판정은 **raw 제목+스니펫만** 본다 (P0-D3A — 생성 topic_candidates 제외).

    우선순위 (사용자 IA 의도 반영 + P0-C1.11 품질 게이트):
    0) 주식 테마/증권 리서치성(stock-hype, 현대건설 직접 제외)은 어떤 레이더에도
       두지 않는다 → other (임원 핵심 섹션 보호).
    0') 현대건설 직접 제재/벌점 → risk_regulation. AI 계약검토도 아래 4축
       신호 기준을 통과해야 ai가 된다.
    1) raw에서 추출한 actor/event/infra/exclusion 교차 신호가 AI 기준을 충족하면
       전력/SMR/거시를 언급해도 ai로 둔다.
    2) 실제 리스크-액션(중대재해/벌점/제재 등) 또는 산업 맥락의 규제 신호는 risk_regulation.
       부처명(국토부 등)·정책·'혁신기술'만으로는 리스크로 보지 않는다.
    3) pure 거시 변수(수주/해외 이벤트 없는 FX·유가·금리)는 macro_economy.
    4) 수주/해외 사업 이벤트는 business_overseas.
    5) 남은 거시 키워드는 macro_economy, 그래도 없으면 카테고리 보정.
    """
    raw = _raw_text(row)
    aq = article_quality.assess(row.get("source"), row.get("title"))

    if aq["stock_hype"]:
        return OTHER
    if aq["hdec_enforcement"]:
        return RISK
    if aq.get("local_safety_inspection"):
        return OTHER

    ai_assessment = radar_signals.classify_ai_radar(row, section=True)
    is_ai_candidate = bool(ai_assessment["eligible"])
    weak_risk_hits = {term for term in RISK_REG_WEAK if term in raw}
    is_ai_enablement_policy = (
        is_ai_candidate
        and bool(weak_risk_hits)
        and weak_risk_hits <= AI_POLICY_ENABLEMENT_TERMS
        and not _hits(raw, RISK_POLICY_IMPACT_TERMS)
    )

    # 중대재해·제재·하자·안전규제는 AI/스마트건설 단어가 함께 있어도 리스크로 먼저 본다.
    # 단 AI/DC/SMR 특별법처럼 구체 위험 없이 시장·발주 환경을 여는 정책은 AI 후보로
    # 유지한다. 그래야 활성화 정책을 안전·제재 리스크로 과분류하지 않으면서
    # 'AI 안전관리 의무화'류는 계속 리스크 섹션이 소유한다.
    if _hits(raw, RISK_ACTION_STRONG) or (
            weak_risk_hits and _hits(raw, INDUSTRY_KEYWORDS)
            and not is_ai_enablement_policy):
        return RISK

    # 상위 radar section은 정책의 section profile을 쓴다. 일반 AI 제품 이벤트도 AI
    # section에는 둘 수 있지만, executive AI 탭은 별도 primary profile에서 ai_product를
    # 제외하므로 앱·챗봇 업데이트가 상단 실행 신호로 승격되지는 않는다.
    if is_ai_candidate:
        return AI

    has_macro = _hits(raw, MACRO_KEYWORDS)
    has_business = (_hits(raw, ORDER_EVENT_KEYWORDS)
                    or _hits(raw, BUSINESS_GEO_KEYWORDS))
    if has_macro and not has_business:
        return MACRO
    if has_business:
        return BUSINESS
    if has_macro:
        return MACRO

    fallback = _CATEGORY_SECTION.get(category_key or "general", OTHER)
    # A generated/legacy category may support routing but cannot bypass the
    # current raw-text signal contract for AI.
    if fallback == AI and not is_ai_candidate:
        return OTHER
    return fallback


def is_risk_eligible(row: dict, category_key: str | None = None) -> bool:
    """리스크·규제 레이더 노출 대상인지 — primary section이 risk_regulation인지로 판정."""
    return classify_section(row, category_key) == RISK


def risk_fields(row: dict, score: dict | None) -> dict:
    """리스크 신호용 파생 필드 — 우선도/사유/라벨/규제 관련성 (점수 재계산 아님).

    risk_priority_score = max(저장된 risk_potential, 심각도 floor)로,
    종합 중요도가 낮아도 중대재해·규제가 리스크 레이더 상단에 노출되게 한다.
    """
    text = _row_text(row)
    floor, label = 0.0, None
    for kws, fl, lab in _SEVERITY_FLOOR:
        if _hits(text, kws):
            floor, label = fl, lab
            break

    rp = (score or {}).get("risk_potential")
    rp = float(rp) if isinstance(rp, (int, float)) else 0.0
    priority = round(max(0.0, min(5.0, max(rp, floor))), 1)

    return {
        "risk_priority_score": priority,
        "risk_radar_label": label,
        "regulatory_relevance": True,
        "risk_reason": _RISK_REASON.get(
            label, "규제·안전 관련 신호 — 사업 영향 점검 대상"),
    }
