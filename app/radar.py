"""Radar 도메인 — 저장된 기사/점수에서 임원용 레이더 섹션을 파생한다 (P0-C1.9).

briefing.py가 build_brief에서 호출하는 표시 전용 분류기다:
- 기사를 ai / risk_regulation / business_overseas / macro_economy / other 중
  하나의 primary radar_section으로 분류한다 (제목·스니펫·토픽·카테고리 기반).
- 리스크/규제 기사에는 risk_priority_score·risk_reason·risk_radar_label·
  regulatory_relevance를 부여해, 종합 중요도(final_score)가 낮아도 리스크 레이더에
  드러나게 한다 (중대재해·규제가 9항목 가중합에서 희석되는 문제 보정).

경계 (briefing 도메인과 동일한 '파생 전용' 원칙):
- DB 접근/쓰기 없음, 네트워크 없음.
- 점수(final_score)·등급(alert_grade)을 재계산하지 않는다 — 저장된 값을 읽어
  분류/우선도만 파생한다.
"""

import json

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

# ---- 분류 키워드 (소문자 매칭) ----
# AI 인프라 / 건설 AI — 사용자 정의 AI 레이더 우선 키워드 (전력망·SMR·냉각 포함).
# 단독 'ai'는 'email/detail' 등 오탐을 피해 한국어 토큰/복합어로만 매칭한다.
AI_KEYWORDS = [
    "데이터센터", "idc", "인공지능", "생성형",
    "smr", "소형모듈원자로", "전력망", "송배전", "냉각",
    "스마트건설", "스마트 건설", "건설로봇", "건설 로봇", "bim", "디지털트윈",
    "건설 자동화", "자동화 시공", "영상인식", "자율시공", "자율주행",
    "ai 데이터센터", "ai 인프라", "ai 전력", "ai 시공", "ai 설계", "ai 안전",
    "ai 수주", "ai 반도체", "스마트 안전", "gpu",
]

# 심각 리스크 — 산업/현장 맥락이 내재돼 별도 anchor 없이 리스크로 본다.
SEVERE_RISK_KEYWORDS = [
    "중대재해", "사망사고", "사망 사고", "산업재해", "산재", "중대재해처벌법",
    "영업정지", "입찰제한", "입찰 제한", "벌점", "붕괴", "인명피해",
]
# 일반 리스크/규제 — 산업 anchor가 함께 있을 때만 리스크로 본다(일반 규제 뉴스 배제).
GENERAL_RISK_KEYWORDS = [
    "안전", "안전관리", "처벌", "제재", "규제", "법안", "시행령", "특별법",
    "국토부", "국토교통부", "고용노동부", "고용부", "감사", "감독", "조사",
    "점검", "의무화",
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
_SEVERITY_FLOOR = [
    (["중대재해", "사망사고", "사망 사고", "산업재해", "산재", "붕괴",
      "인명피해", "중대재해처벌법"], 4.3, "중대재해"),
    (["영업정지", "입찰제한", "입찰 제한", "벌점", "처벌", "제재"], 3.8, "제재"),
    (["규제", "법안", "시행령", "특별법", "국토부", "국토교통부",
      "고용노동부", "고용부", "감사", "감독", "조사", "의무화"], 3.3, "규제"),
    (["안전", "안전관리", "점검"], 2.8, "안전"),
]

# P0-C1.10: 임원 메모 스타일 — 종결어미('~필요합니다/~합니다') 없이 명사형 구절로.
_RISK_REASON = {
    "중대재해": "중대재해·산업안전 이슈 — 입찰 자격·평판 리스크 점검 대상",
    "제재": "제재·영업정지 등 규제 리스크 — 수주 자격 영향 점검 대상",
    "규제": "규제·법규 변화 — 컴플라이언스 대응·사업 영향 점검 대상",
    "안전": "현장 안전 이슈 — 안전관리 체계 점검 대상",
}


def _hits(text: str, keywords: list[str]) -> bool:
    return any(kw in text for kw in keywords)


def _row_text(row: dict) -> str:
    """제목 + 스니펫 + 토픽 후보를 한 덩어리 소문자 텍스트로 (분류 입력)."""
    parts = [row.get("title") or "", row.get("snippet") or ""]
    try:
        topics = json.loads(row.get("topic_candidates") or "[]")
        parts += [t for t in topics if isinstance(t, str)]
    except (ValueError, TypeError):
        pass
    return " ".join(parts).lower()


def classify_section(row: dict, category_key: str | None = None) -> str:
    """기사 1건의 primary radar_section을 정한다 (표시 전용 파생값).

    우선순위 (사용자 IA 의도 반영):
    1) AI 인프라/건설 AI 신호는 전력/SMR/거시를 언급해도 ai로 둔다.
    2) 심각 리스크(중대재해 등) 또는 산업 맥락의 규제 신호는 risk_regulation.
    3) pure 거시 변수(수주/해외 이벤트 없는 FX·유가·금리)는 macro_economy.
    4) 수주/해외 사업 이벤트는 business_overseas.
    5) 남은 거시 키워드는 macro_economy, 그래도 없으면 카테고리 보정.
    """
    text = _row_text(row)

    if _hits(text, AI_KEYWORDS):
        return AI

    if _hits(text, SEVERE_RISK_KEYWORDS) or (
            _hits(text, GENERAL_RISK_KEYWORDS) and _hits(text, INDUSTRY_KEYWORDS)):
        return RISK

    has_macro = _hits(text, MACRO_KEYWORDS)
    has_business = (_hits(text, ORDER_EVENT_KEYWORDS)
                    or _hits(text, BUSINESS_GEO_KEYWORDS))
    if has_macro and not has_business:
        return MACRO
    if has_business:
        return BUSINESS
    if has_macro:
        return MACRO

    return _CATEGORY_SECTION.get(category_key or "general", OTHER)


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
