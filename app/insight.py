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
    "hdec": "현대건설 직접 관련",
    "dc_power": "AI 데이터센터·전력 인프라",
    "competitor": "경쟁 건설사 동향",
    "safety": "건설현장 안전·중대재해",
    "mideast_overseas": "중동·해외 수주 환경",
    "macro": "거시경제 변수",
    "gov": "정부 정책·인프라 투자",
    "smart_const": "스마트건설 기술",
    "general": "건설산업 일반",
}

IMPLICATION_TEMPLATES = {
    "hdec": "현대건설이 직접 언급된 기사로, 수주 경쟁력과 시장 포지션에 즉각적인 영향 가능성이 있다.",
    "dc_power": "데이터센터 EPC, 원전/SMR, 송배전 등 에너지 인프라 사업 기회와 직결될 수 있는 신호다.",
    "competitor": "경쟁 건설사의 AI·스마트건설 행보로, 기술 투자 우선순위와 수주 경쟁 구도에 영향을 줄 수 있다.",
    "safety": "현장 안전·중대재해 규제 환경 변화로 안전관리 체계, 입찰 자격, 평판 리스크에 영향 가능성이 있다.",
    "mideast_overseas": "중동·해외 발주 환경 변화로 해외수주 전략과 프로젝트 원가 관리에 영향을 줄 수 있다.",
    "macro": "환율·금리·원자재 등 거시 변수 변화로 해외수주 원가와 프로젝트 파이낸싱 여건에 영향을 줄 수 있다.",
    "gov": "정부 정책·예산 방향에 따라 인프라·에너지 발주 환경이 달라질 수 있다.",
    "smart_const": "스마트건설 기술 확산 흐름으로, 생산성·안전 분야 기술 도입 검토와 연결될 수 있다.",
    "general": "현대건설 사업과의 직접 연관성은 낮아 참고 수준의 모니터링 대상이다.",
}

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
    "주간 리포트 후보": "주간 보고 후보",
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
