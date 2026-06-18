"""Insight 도메인 — template 기반 mock insight와 digest_message 생성.

P0-A는 LLM 없이 template으로만 생성한다 (PRD §13.1).
점수 계산을 하지 않으며, alert_grade를 재계산하거나 중복 저장하지 않는다 —
alert_grade는 article_scores가 단일 소유이고 여기서는 추천 문구 결정에 읽기만 한다.
"""

import json
import re

from app import db

# 텍스트 템플릿 선택용 경량 카테고리 키워드 (점수 계산 아님)
CATEGORY_KEYWORDS = {
    "hdec": ["현대건설"],
    "dc_power": ["데이터센터", "전력", "송배전", "전력망", "원전", "SMR", "에너지"],
    "competitor": ["삼성물산", "GS건설", "DL이앤씨", "대우건설", "포스코이앤씨"],
    "safety": ["중대재해", "안전관리", "안전", "사망사고"],
    "macro": ["환율", "금리", "유가", "원자재", "철강", "시멘트", "FOMC", "달러", "공급망"],
    "mideast_overseas": ["중동", "사우디", "네옴", "해외건설", "해외수주", "플랜트"],
    "gov": ["정부", "정책", "예산", "특별법", "시행령", "로드맵"],
    "smart_const": ["스마트건설", "건설로봇", "BIM"],
}

CATEGORY_PRIORITY = ["hdec", "dc_power", "competitor", "safety",
                     "mideast_overseas", "macro", "gov", "smart_const"]

CATEGORY_PHRASE = {
    "hdec": "현대건설 연관",
    "dc_power": "AI 데이터센터·전력 인프라",
    "competitor": "경쟁 건설사 동향",
    "safety": "건설현장 안전·중대재해",
    "mideast_overseas": "중동·해외 수주 환경",
    "macro": "거시경제 변수",
    "gov": "정부 정책·인프라 투자",
    "smart_const": "스마트건설 기술",
    "general": "건설산업 일반",
}

# P0-C1.10: 임원 메모 스타일 — 종결어미('~있다/~신호다/~합니다') 없이 명사형 구절로.
# (briefing._CATEGORY_BY_IMPLICATION 역매핑이 이 dict를 그대로 읽으므로 값은 고유해야 한다.)
IMPLICATION_TEMPLATES = {
    "hdec": "현대건설 연관 신호 — 수주 경쟁력·시장 포지션 영향권",
    "dc_power": "데이터센터 EPC·원전/SMR·송배전 등 에너지 인프라 사업 기회",
    "competitor": "경쟁사 AI·스마트건설 행보 — 기술 투자 우선순위·수주 경쟁 구도 변수",
    "safety": "현장 안전·중대재해 규제 변화 — 안전관리·입찰 자격·평판 리스크",
    "mideast_overseas": "중동·해외 발주 환경 변화 — 해외수주 전략·원가 관리 변수",
    "macro": "환율·금리·원자재 등 거시 변수 — 해외수주 원가·파이낸싱 여건 변수",
    "gov": "정부 정책·예산 방향 — 인프라·에너지 발주 환경 변수",
    "smart_const": "스마트건설 기술 확산 — 생산성·안전 기술 도입 검토 대상",
    "general": "현대건설 연관성 낮음 — 참고 수준 모니터링 대상",
}

# ---- P0-D3B: 기사 유형별 임원 사유 한 줄 (deterministic, raw 입력 기반) ----
# 문제: 모든 '현대건설' 기사가 generic "현대건설 연관 신호 — 수주 경쟁력·시장 포지션 영향권"으로
# 표시돼 도시정비 수주/원전/데이터센터/분양 PR/전환사채가 한 문장으로 뭉뚱그려졌다.
# 해결: raw 제목+스니펫과 미리 계산된 플래그(stock_hype/is_finance/hdec_direct)만 보고 유형별
# 명사형 사유 copy를 고른다. 생성된 라벨/카테고리 텍스트를 입력으로 쓰지 않는다(self-fulfilling
# 오탐 금지). 직접 영향을 과장하지 않는다 — 매칭이 없으면 중립 fallback으로 둔다.
REASON_DATACENTER = "AI 데이터센터 EPC·전력 인프라 수주 기회"
REASON_ENERGY = "원전·SMR 밸류체인 및 에너지 인프라 수주 기회"
REASON_ORDER_CITY = "도시정비 수주 경쟁력·점유율 신호"
REASON_CUSTOMER_AI = "고객접점 자동화·분양 운영 효율화 실험"
REASON_RISK = "입찰 자격·평판·컴플라이언스 리스크"
REASON_FINANCE = "자본시장·재무전략 관찰 신호"
REASON_SECURITIES = "자본시장 관찰 신호 — 사업 의사결정 핵심도 낮음"
REASON_SALES_PROMO = "소비자 분양 홍보성 정보 — 임원 의사결정 핵심도 낮음"
REASON_BUSINESS_OVERSEAS = "해외 발주 환경 변화 — 수주 전략·원가 관리 변수"
REASON_GENERIC_AI = "스마트건설 기술 확산 — 생산성·안전 기술 도입 검토 대상"
REASON_GENERIC_POLICY = "정책·제도 방향 변화 — 사업 환경 모니터링 대상"
REASON_HDEC_GENERIC = "현대건설 직접 언급 — 사업 영향 점검 대상"
REASON_FALLBACK = "참고 수준 산업 동향 — 직접 영향 제한적"

# 유형 판정 키워드 (소문자 매칭, raw 제목+스니펫 기준). 분류/라우팅을 재계산하지 않고
# 표시 copy 선택에만 쓴다 (insight._detect_categories와 같은 결의 키워드→copy 선택).
_RT_DATACENTER = ["데이터센터", "데이터 센터", "ai 데이터센터", "idc"]
_RT_RISK = ["벌점", "제재", "하도급", "중대재해", "공정위", "국토부", "고용노동부",
            "안전사고", "사망사고", "산업재해", "영업정지", "영업 정지", "입찰제한",
            "입찰 제한", "과징금", "행정처분", "행정 처분", "처분 통보", "사전통보",
            "부실시공", "붕괴"]
_RT_ORDER_CITY = ["도시정비", "재개발", "재건축", "정비사업", "수주액", "수주고",
                  "수주잔고", "정비 수주", "리모델링 수주"]
_RT_ENERGY = ["원전", "smr", "소형모듈원자로", "원자력", "송배전", "전력망",
              "에너지 인프라", "전력 인프라", "해상풍력", "수소", "재생에너지",
              "태양광", "수전해"]
_RT_AI_CORE = ["ai", "인공지능", "생성형", "에이아이"]
_RT_CUSTOMER_CTX = ["상담사", "청약 상담", "분양 상담", "입주민", "고객 응대",
                    "고객 상담", "콜센터", "챗봇", "상담 자동화", "고객 서비스"]
_RT_AI_GENERIC = ["ai", "인공지능", "생성형", "로봇", "스마트건설", "스마트 건설",
                  "bim", "디지털트윈", "디지털 트윈", "자동화", "머신러닝", "딥러닝"]
# 증권 리서치/주가 테마 마커 (목표가·증권사 리포트·종목+·잭팟 등). 현대건설은 article_quality
# stock_hype 게이트에서 면제(P0-C1.11 floor — 제외 방지)되므로 is_stock_hype만으로는 '현대건설
# 목표가' 리포트를 못 잡는다. 사유 정직성을 위해 reason 레이어에서 별도로 본다 — 라우팅/등급은
# 절대 바꾸지 않고(스코어 floor는 그대로), '직접 수주 win'으로 과장하는 copy만 막는다.
_RT_SECURITIES = ["목표가", "목표주가", "투자의견", "투자포인트", "투자 포인트",
                  "증권가", "증권사 리포트", "종목+", "[종목", "잭팟", "테마주",
                  "관련주", "대장주", "급등주", "상한가"]
_RT_FINANCE = ["전환사채", "cb 발행", "회사채", "유상증자", "자금조달", "신용등급",
               "신용도", "메자닌", "사채 발행", "기업어음"]
_RT_SALES_PROMO = ["견본주택", "모델하우스", "분양가", "특별공급", "미분양", "개관",
                   "청약 경쟁률", "1순위 마감", "정당계약", "사이버 모델하우스",
                   "분양 단지", "단지 분양"]
_RT_OVERSEAS = ["중동", "사우디", "네옴", "neom", "uae", "아랍에미리트", "카타르",
                "쿠웨이트", "이라크", "해외수주", "해외 수주", "글로벌 수주", "플랜트",
                "epc", "재건", "해외 발주", "해외 진출", "수주 채비"]
_RT_POLICY = ["정부 정책", "기술대전", "챌린지", "공모전", "예산", "특별법", "로드맵",
              "시행령", "규제 완화", "국가 정책", "정책 발표"]
_RT_HDEC = ["현대건설", "현대 건설", "현대엔지니어링", "현대eng", "hyundai e&c",
            "hyundai engineering"]


def executive_reason(title: str, snippet: str = "", *, is_stock_hype: bool = False,
                     is_finance: bool = False, hdec_direct: bool = False) -> str:
    """기사 유형별 임원 사유 한 줄 (P0-D3B) — raw 제목+스니펫과 플래그만 입력.

    분류/라우팅을 재계산하지 않는다 — briefing이 이미 구한 decision 플래그(stock_hype/
    is_finance/hdec_direct)와 raw 텍스트만 보고 표시용 사유 copy를 고른다. 우선순위:
    전략 신호(데이터센터·리스크) > 증권 테마 강등 > 사업 유형(도시정비·원전·고객 AI·재무·
    분양 PR·해외) > 일반(스마트건설·정책) > fallback. 직접 영향을 과장하지 않는다."""
    text = f"{title or ''} {snippet or ''}".lower()

    def has(keywords) -> bool:
        return any(kw in text for kw in keywords)

    # 1) 데이터센터 — 가장 강한 차별적 전략 신호. 회사채/증권 맥락이 섞여도 DC 전략으로 본다
    #    (예: '현대건설 데이터센터 사업 회사채' → 재무가 아니라 DC EPC·전력).
    if has(_RT_DATACENTER):
        return REASON_DATACENTER
    # 2) 리스크·규제 — 입찰 자격/평판/컴플라이언스. 분양·수주를 함께 언급해도 리스크 우선.
    if has(_RT_RISK):
        return REASON_RISK
    # 3) 증권 리서치/주가 테마(목표가·증권가·종목+·잭팟 등) — 원전/수주를 언급해도 '직접 수주
    #    win'으로 과장하지 않고 정직한 자본시장 관찰로 강등한다. 현대건설은 article_quality
    #    stock_hype 면제 대상이라 is_stock_hype만으로는 못 잡으므로 reason 레이어에서 직접 본다.
    if is_stock_hype or has(_RT_SECURITIES):
        return REASON_SECURITIES
    # 4) 도시정비 수주 — 점유율·경쟁력 신호.
    if has(_RT_ORDER_CITY):
        return REASON_ORDER_CITY
    # 5) 원전·SMR·에너지 인프라 — 밸류체인·발주 기회.
    if has(_RT_ENERGY):
        return REASON_ENERGY
    # 6) 고객접점 AI — 생성형/AI + 상담·청약·입주민 맥락 (분양 운영 효율화 실험).
    if has(_RT_AI_CORE) and has(_RT_CUSTOMER_CTX):
        return REASON_CUSTOMER_AI
    # 7) 자본시장·재무 이벤트 — 전환사채/회사채/유상증자 등 실제 재무 이벤트(증권 리서치와 구분).
    if is_finance or has(_RT_FINANCE):
        return REASON_FINANCE
    # 8) 단순 분양·견본주택 마케팅 — 소비자 PR, 임원 의사결정 핵심도 낮음.
    if has(_RT_SALES_PROMO):
        return REASON_SALES_PROMO
    # 9) 해외·발주 환경 — 수주 전략·원가 관리 변수.
    if has(_RT_OVERSEAS):
        return REASON_BUSINESS_OVERSEAS
    # 10) 스마트건설·AI 기술 일반 — 직접 HDEC 영향이 아니라 기술 확산 모니터링.
    if has(_RT_AI_GENERIC):
        return REASON_GENERIC_AI
    # 11) 정책·제도 — 사업 환경 모니터링.
    if has(_RT_POLICY):
        return REASON_GENERIC_POLICY
    # 12) fallback — 현대건설 직접 언급이면 중립 점검 대상, 아니면 일반 동향(과장 금지).
    if hdec_direct or has(_RT_HDEC):
        return REASON_HDEC_GENERIC
    return REASON_FALLBACK


AFFECTED_UNITS_MAP = {
    "hdec": ["전략기획"],
    "dc_power": ["데이터센터", "원전/에너지"],
    "competitor": ["전략기획", "기술연구원"],
    "safety": ["안전품질"],
    "mideast_overseas": ["해외사업"],
    "macro": ["전략기획", "재무"],
    "gov": ["전략기획"],
    "smart_const": ["기술연구원"],
}

CHECKPOINT_TEMPLATES = {
    "hdec": ["해당 프로젝트의 수주 단계와 후속 일정은 어떻게 되는가?",
             "경쟁사 대비 차별화 포인트와 리스크 요인은 무엇인가?"],
    "dc_power": ["데이터센터·전력 인프라 발주 파이프라인에서 당사가 참여 가능한 단계는 어디인가?",
                 "전력 확보·송배전 연계 역량이 수주 경쟁력에 미치는 영향은 어느 정도인가?"],
    "competitor": ["경쟁사의 해당 투자가 입찰 경쟁 구도에 미칠 영향은 무엇인가?",
                   "당사의 대응 기술/조직 로드맵은 준비되어 있는가?"],
    "safety": ["당사 현장의 동일 유형 리스크 점검 상태는 어떠한가?",
               "규제 강화 시 입찰·수주 자격에 미치는 영향은 무엇인가?"],
    "mideast_overseas": ["해당 지역 발주처의 재정 여건과 발주 일정 변화는 어떠한가?",
                         "환율·원가 변동을 반영한 수익성 시나리오는 점검되었는가?"],
    "macro": ["이 거시 변수의 지속 기간과 현재 진행 중인 프로젝트 원가에 미칠 영향은?",
              "환헤지·조달 전략 조정이 필요한 사업장은 어디인가?"],
    "gov": ["정책·예산 일정에 맞춘 수주 준비 계획이 있는가?",
            "당사 사업 포트폴리오 중 수혜/영향 범위는 어디까지인가?"],
    "smart_const": ["해당 기술의 당사 현장 적용 가능성과 도입 비용은?",
                    "기술 격차가 수주 경쟁력에 미치는 영향은 무엇인가?"],
    "general": ["임원 보고 가치가 있는 후속 신호가 이어지는지 관찰이 필요한가?",
                "관련 부문에 참고 공유할 필요가 있는가?"],
}

RECOMMENDED_ACTION_BY_GRADE = {
    "즉시 알림 후보": "운영자 검토 후 발송",
    "검토 필요": "담당부서 검토",
    "추적 필요": "계속 관찰",
    "제외": "제외",
}

DIGEST_TEMPLATE = """[HDEC Executive Radar]
{category_phrase} 관련 중요 신호 1건 감지

왜 중요한가:
{why_important}

현대건설 관점:
{implication}

영향 부문:
{units}

추천:
{recommendation}

원문:
{article_url}"""


def _detect_categories(text: str) -> list[str]:
    return [c for c in CATEGORY_PRIORITY
            if any(kw in text for kw in CATEGORY_KEYWORDS[c])]


def _split_sentences(snippet: str) -> list[str]:
    parts = re.split(r"(?<=다\.)\s+|(?<=\.)\s+(?=[가-힣A-Z])", (snippet or "").strip())
    return [p.strip() for p in parts if p.strip()]


def _summary_3lines(article: dict, categories: list[str]) -> list[str]:
    lines = _split_sentences(article.get("snippet") or "")[:3]
    primary = categories[0] if categories else "general"
    if len(lines) < 3:
        lines.append(f"{CATEGORY_PHRASE[primary]} 영역의 신호로 분류된 기사다.")
    if len(lines) < 3:
        lines.append("세부 영향은 운영자 검토 후 관련 부문에 공유 여부를 판단한다.")
    return lines[:3]


def _affected_units(categories: list[str]) -> list[str]:
    units = []
    for category in categories:
        for unit in AFFECTED_UNITS_MAP.get(category, []):
            if unit not in units:
                units.append(unit)
    return units[:4] if units else ["전략기획"]


def _opportunity_or_risk(score: dict | None) -> str:
    if not score:
        return "관찰"
    opportunity = score.get("business_opportunity") or 0
    risk = score.get("risk_potential") or 0
    if opportunity >= 3.0 and risk >= 3.0:
        return "기회+리스크"
    if opportunity >= 2.0 and opportunity >= risk + 0.5:
        return "기회"
    if risk >= 2.0 and risk >= opportunity + 0.5:
        return "리스크"
    return "관찰"


def _why_important(categories: list[str], score: dict | None) -> str:
    primary = categories[0] if categories else "general"
    base = {
        "hdec": "당사가 직접 언급된 신호로 시장과 발주처의 시선이 집중될 수 있습니다.",
        "dc_power": "전력 확보가 데이터센터 발주 경쟁력의 핵심 변수로 부상 중입니다.",
        "competitor": "경쟁사의 전략 변화는 수주 경쟁 구도에 직접 영향을 줍니다.",
        "safety": "안전·중대재해 이슈는 수주 자격과 기업 평판에 직결됩니다.",
        "mideast_overseas": "중동·해외 발주 환경 변화는 해외수주 전략의 핵심 변수입니다.",
        "macro": "거시 변수 변동은 프로젝트 원가와 파이낸싱 여건을 좌우합니다.",
        "gov": "정부 정책·예산 방향은 인프라 발주 물량을 결정합니다.",
        "smart_const": "스마트건설 기술 확산은 중장기 생산성 경쟁력과 연결됩니다.",
        "general": "직접 연관성은 낮으나 흐름 관찰 차원에서 기록된 신호입니다.",
    }[primary]
    if score and (score.get("final_score") or 0) >= 4.5:
        return base + " 점수 기준 즉시 공유 후보에 해당합니다."
    return base


def _build_insight(article: dict, score: dict | None) -> dict:
    text = f"{article['title']} {article.get('snippet') or ''}"
    categories = _detect_categories(text)
    primary = categories[0] if categories else "general"
    alert_grade = (score or {}).get("alert_grade") or "제외"
    units = _affected_units(categories)
    implication = IMPLICATION_TEMPLATES[primary]
    recommendation = RECOMMENDED_ACTION_BY_GRADE.get(alert_grade, "제외")

    digest = DIGEST_TEMPLATE.format(
        category_phrase=CATEGORY_PHRASE[primary],
        why_important=_why_important(categories, score),
        implication=implication,
        units=" / ".join(units),
        recommendation=recommendation,
        article_url=article.get("url") or "",
    )

    return {
        "id": f"insight_{article['id']}",
        "article_id": article["id"],
        "summary_3lines": json.dumps(_summary_3lines(article, categories), ensure_ascii=False),
        "hdec_implication": implication,
        "affected_units": json.dumps(units, ensure_ascii=False),
        "opportunity_or_risk": _opportunity_or_risk(score),
        "executive_checkpoints": json.dumps(CHECKPOINT_TEMPLATES[primary], ensure_ascii=False),
        "recommended_action": recommendation,
        "digest_message": digest,
        "created_at": db.now_iso(),
    }


def generate_all() -> dict:
    """전 기사에 대해 template insight를 생성해 article_insights에 저장한다."""
    generated = 0
    for article in db.fetch_all_articles():
        score = (db.fetch_article_detail(article["id"]) or {}).get("score")
        db.upsert_insight(_build_insight(article, score))
        generated += 1
    return {"insights": generated}
