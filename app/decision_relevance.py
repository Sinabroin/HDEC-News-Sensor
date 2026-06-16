"""Decision Relevance 도메인 (P0-C1.12) — 임원 의사결정 관련성 레이어.

radar/article_quality 위에 얹혀, 기사를 "현대건설 임원 의사결정에 얼마나 유용한가"로
티어링하고, 임원 섹션 멤버십(primary + secondary)을 파생한다. 제품 목표를
'AI 뉴스 수집'에서 '현대건설 임원 의사결정 레이더'로 재정렬한다 (AI-우선 강조는 유지).

임원 섹션:
- hdec_direct       현대건설 직접 영향 (현대건설/현대ENG/HMG건설기술연구원이 주체)
- ai                AI 관련 (데이터센터·전력·SMR·스마트건설 인프라)
- order_overseas    수주·해외 (해외수주·중동·플랜트·EPC·발주 환경 — 확정 계약뿐 아니라 환경)
- risk_regulation   리스크·규제 (중대재해·벌점·제재 등 — P0-C1.11 게이트 유지)
- competitor_supply 경쟁사·공급망 (삼성물산·GS건설·SK에코플랜트 / 전선·냉각·버스덕트 등)
- macro_economy     거시경제 (환율·유가·금리·원자재·공사비·PF)
- other             기타

경계 (radar/briefing과 동일 '순수 파생' 원칙):
- 순수 함수만 — DB·네트워크·발송·점수(final_score)·등급(alert_grade) 재계산 없음.
- **raw 제목/출처/스니펫/토픽만** 입력으로 본다. 생성된 사유/카테고리 라벨 텍스트
  ('현대건설 직접 연관성 낮음' 등 우리가 만든 문자열)를 분류 입력으로 쓰지 않는다 —
  생성 라벨에 '현대건설'이 들어가 self-fulfilling 오탐을 내는 것을 막는다.
- 관련성/티어는 사실 보증이 아니라 랭킹/필터/표시 가드레일이다 (가짜 신뢰도 금지).
- stock-hype/증권 리서치성은 article_quality가 단일 판정 — 여기선 other로만 둔다.

소비처:
- briefing: 시그널 entry에 decision_relevance 필드 부착 + 섹션 리스트 구성(멤버십).
- scoring: hdec 전략/수주·해외 환경 후보를 제외에서 끌어올리는 등급 floor.
- audit helper: 의사결정 관련성 점검(읽기 전용).
"""

import json

from app import article_quality, radar, source_quality

# ---- 임원 섹션 키 (단일 소스) ----
HDEC_DIRECT = "hdec_direct"
AI = "ai"
ORDER_OVERSEAS = "order_overseas"
RISK = "risk_regulation"
COMPETITOR = "competitor_supply"
MACRO = "macro_economy"
OTHER = "other"

# primary 선택 우선순위 (위에서부터). 리스크는 임원이 반드시 봐야 하므로 최상위.
SECTION_PRIORITY = [RISK, HDEC_DIRECT, AI, ORDER_OVERSEAS, COMPETITOR, MACRO, OTHER]

# UI·리포트·Telegram이 그대로 노출하는 섹션 라벨 (표시 전용 단일 소스)
EXEC_LABELS = {
    HDEC_DIRECT: "현대건설 직접 영향",
    AI: "AI 관련",
    ORDER_OVERSEAS: "수주·해외",
    RISK: "리스크·규제",
    COMPETITOR: "경쟁사·공급망",
    MACRO: "거시경제",
    OTHER: "기타",
}
# 짧은 라벨 (대시보드 탭/Telegram 칩)
EXEC_SHORT = {
    HDEC_DIRECT: "현대건설", AI: "AI", ORDER_OVERSEAS: "수주·해외",
    RISK: "리스크·규제", COMPETITOR: "경쟁사·공급망", MACRO: "거시경제", OTHER: "기타",
}

# 의사결정 관련성 티어 (랭킹용) — A가 가장 유용.
TIER_A, TIER_A_MINUS = "A", "A-"
TIER_B_PLUS, TIER_B, TIER_B_MINUS = "B+", "B", "B-"
TIER_C, TIER_EXCLUDE = "C", "exclude"
_TIER_BANDS = [
    (4.3, TIER_A), (3.6, TIER_A_MINUS), (2.9, TIER_B_PLUS),
    (2.2, TIER_B), (1.5, TIER_B_MINUS), (0.01, TIER_C),
]

# ---- 키워드 (소문자 매칭) ----
# 현대건설 family (현대ENG/현대엔지니어링은 현대건설 그룹 — 경쟁사가 아니다).
HDEC_NAMES = ["현대건설", "현대 건설", "현대ENG", "현대 ENG", "현대엔지니어링",
              "hmg건설기술연구원", "hyundai e&c", "hyundai engineering"]
# 현대건설 직접 영향 판정 — 전략/사업/조직/기술/리스크 신호가 함께 있어야 한다.
HDEC_SIGNAL = [
    "수주", "발주", "데이터센터", "smr", "소형모듈원자로", "원전", "뉴에너지",
    "에너지전환", "에너지 전환", "스마트건설", "스마트 건설", "r&d", "연구원",
    "조직", "통합", "일원화", "협력사", "하도급", "계약", "불공정", "벌점",
    "사전통보", "제재", "분양", "도시정비", "재건축", "재개발", "플랜트", "해외",
    "중동", "상생펀드", "ai", "수소", "재생에너지", "태양광", "해상풍력", "송배전",
    "전력", "epc", "착공", "준공", "포트폴리오", "신사업", "수전해",
]
# 현대건설이지만 의사결정과 무관(직접 섹션에서 낮춤) — 헬스케어/생활/스포츠/연예.
HDEC_OFFTOPIC = ["헬스케어", "건강", "병원", "스포츠", "축구", "골프", "야구",
                 "연예", "드라마", "웹툰", "레시피", "맛집", "여행 후기"]

# 수주·해외 — 확정 계약뿐 아니라 '발주 환경'까지 넓게 본다.
ORDER_TOKENS = ["해외수주", "해외 수주", "수주", "발주", "낙찰", "우선협상", "본계약",
                "글로벌 수주", "프로젝트 파이프라인", "발주처", "수주 채비", "수주잔고",
                "수주 목표", "수주 추진", "수주 확대"]
ORDER_GEO = ["중동", "사우디", "네옴", "neom", "uae", "아랍에미리트", "카타르",
             "쿠웨이트", "이라크", "재건", "동남아", "북미", "유럽", "체코",
             "폴란드", "인도네시아", "베트남"]
ORDER_SECTOR = ["플랜트", "lng", "원전", "smr", "소형모듈원자로", "epc",
                "데이터센터", "해상풍력", "송전", "변전", "정유", "석유화학"]
# 강한 수주·환경 신호 — radar가 AI/other/macro로 보내도 수주·해외 후보로 끌어온다.
ORDER_ENV_STRONG = (ORDER_GEO + ["해외수주", "해외 수주", "글로벌 수주", "재건",
                                 "수주 채비", "수주 추진", "발주 환경", "발주환경"])

# 경쟁사 (현대건설 family 제외) + 공급망.
COMPETITOR_NAMES = ["삼성물산", "삼성ENG", "삼성엔지니어링", "gs건설", "gs e&c",
                    "sk에코플랜트", "dl이앤씨", "대우건설", "포스코이앤씨",
                    "롯데건설", "한화건설", "한화 건설", "ds건설"]
# 공급망 — 데이터센터/전력 공급망의 '구별되는' 부품·설비 신호만 (일반 DC 기사를
# 경쟁사·공급망으로 끌어오지 않도록 흔한 '냉각/조달'은 제외, 버스덕트/전선/변압기 등만).
SUPPLY_TOKENS = ["전선", "케이블", "버스덕트", "변압기", "전력설비", "수냉식",
                 "공급망 재편", "기자재 공급", "부품 공급"]

# 비용/거시/파이낸싱 — radar 거시 키워드 + 건설 원가/파이낸싱 변수.
COST_MACRO = radar.MACRO_KEYWORDS + ["공사비", "원가", "pf", "프로젝트 파이낸싱",
                                     "조달비용", "분양시장", "주택시장"]


def _hits(text: str, keywords) -> bool:
    return any(kw in text for kw in keywords)


def _text(row: dict) -> str:
    """제목 + 스니펫 + 토픽 후보를 한 덩어리 소문자 텍스트로 (raw 입력만)."""
    parts = [row.get("title") or "", row.get("snippet") or ""]
    tc = row.get("topic_candidates")
    try:
        if isinstance(tc, str):
            tc = json.loads(tc or "[]")
        parts += [t for t in (tc or []) if isinstance(t, str)]
    except (ValueError, TypeError):
        pass
    return " ".join(parts).lower()


def is_hdec_direct(title: str) -> bool:
    """제목이 현대건설 family를 직접 다루는지 (현대건설/현대ENG/HMG건설기술연구원)."""
    return _hits((title or "").lower(), [n.lower() for n in HDEC_NAMES])


def is_hdec_strategic(title: str) -> bool:
    """현대건설 직접 + 전략/사업/조직/기술/리스크 신호 — 등급 floor 대상.

    제목만 본다 (스니펫 제외 — 오탐 방지). 헬스케어/생활/연예성은 제외한다
    ('현대건설 AI 헬스케어 협약'은 직접 영향으로 끌어올리지 않는다)."""
    low = (title or "").lower()
    if not is_hdec_direct(title):
        return False
    if _hits(low, HDEC_OFFTOPIC) and not _hits(low, [
            "수주", "데이터센터", "smr", "벌점", "제재", "하도급", "epc", "플랜트"]):
        return False
    return _hits(low, HDEC_SIGNAL)


def is_order_environment(row: dict) -> bool:
    """수주·해외 '발주 환경' 후보인지 — EPC/데이터센터/SMR/플랜트/중동/재건 등.

    radar가 AI/other/macro로 분류해도 수주·해외 후보로 surface하기 위한 판정.
    generic '착공/수주'만으로는 트리거하지 않는다 (주택 착공 통계 등 오탐 방지) —
    섹터(EPC/플랜트/원전/SMR/데이터센터/해상풍력) 또는 지역(중동/재건 등) 신호를 요구한다."""
    text = _text(row)
    if article_quality.assess(row.get("source"), row.get("title"))["stock_hype"]:
        return False
    return _hits(text, ORDER_SECTOR) or _hits(text, ORDER_GEO) or _hits(
        text, ["해외수주", "해외 수주", "글로벌 수주", "재건"])


def _memberships(row: dict, category=None) -> tuple[set, dict]:
    """기사 1건의 임원 섹션 멤버십 집합과 부가 플래그를 계산한다."""
    title = row.get("title") or ""
    title_low = title.lower()
    text = _text(row)
    aq = article_quality.assess(row.get("source"), title)

    flags = {"hdec_direct": False, "hdec_strategic": False,
             "is_competitor": False, "stock_hype": aq["stock_hype"]}

    # stock-hype/증권 리서치성 → 어떤 임원 섹션에도 넣지 않는다 (other).
    if aq["stock_hype"]:
        return {OTHER}, flags

    base = radar.classify_section(row, category)
    members: set[str] = set()
    if base == radar.AI:
        members.add(AI)
    elif base == radar.RISK:
        members.add(RISK)
    elif base == radar.MACRO:
        members.add(MACRO)
    elif base == radar.BUSINESS:
        members.add(ORDER_OVERSEAS)

    # 현대건설 직접 영향
    if is_hdec_strategic(title):
        members.add(HDEC_DIRECT)
        flags["hdec_direct"] = True
        flags["hdec_strategic"] = True
    elif is_hdec_direct(title):
        flags["hdec_direct"] = True  # 직접 언급은 맞지만 전략 신호 약함

    # 수주·해외 환경 (broadening) — 섹터/지역 신호가 있으면 무조건 후보.
    if is_order_environment(row) or _hits(text, ORDER_TOKENS):
        if _hits(text, ORDER_SECTOR) or _hits(text, ORDER_GEO) or _hits(
                text, ORDER_ENV_STRONG):
            members.add(ORDER_OVERSEAS)

    # 경쟁사·공급망
    if _hits(title_low, [n.lower() for n in COMPETITOR_NAMES]) or _hits(
            text, SUPPLY_TOKENS):
        members.add(COMPETITOR)
        flags["is_competitor"] = True

    # 비용/거시 — 다른 멤버십이 전혀 없을 때만 (희석 방지).
    if not members and _hits(text, COST_MACRO):
        members.add(MACRO)

    if not members:
        members.add(OTHER)
    return members, flags


def _hdec_bucket(row: dict, members: set, aq: dict) -> int:
    """현대건설 직접 영향 정렬 버킷 (작을수록 상단) — Phase 3 순서.

    1 리스크/제재 · 2 수주/데이터센터/SMR/뉴에너지 전략 · 3 AI/계약/컴플라이언스 ·
    4 R&D/조직/기술역량 · 5 그 외 직접(시장 포지션 등)."""
    low = (row.get("title") or "").lower()
    if RISK in members or aq.get("hdec_enforcement"):
        return 1
    if _hits(low, ["수주", "데이터센터", "smr", "뉴에너지", "에너지전환",
                   "원전", "플랜트", "도시정비", "해외", "발주"]):
        return 2
    if _hits(low, ["ai", "하도급", "계약", "협력사", "상생펀드", "불공정", "컴플라이언스"]):
        return 3
    if _hits(low, ["r&d", "연구원", "조직", "통합", "일원화", "기술"]):
        return 4
    return 5


def _score_and_tier(members: set, flags: dict, source: str, title: str) -> tuple:
    """의사결정 관련성 점수(0~5)와 티어 — 랭킹용 가드레일(사실 보증 아님)."""
    if flags["stock_hype"] or members == {OTHER}:
        return 0.0, TIER_EXCLUDE
    score = 0.0
    if HDEC_DIRECT in members:
        score += 2.2
    if RISK in members:
        score += 1.6
    if ORDER_OVERSEAS in members:
        score += 1.3
    if AI in members:
        score += 1.0
    if COMPETITOR in members:
        score += 0.8
    if MACRO in members:
        score += 0.5
    if flags["hdec_strategic"]:
        score += 0.6
    # 출처 신뢰도 — 낮은 신뢰/제외 출처는 의사결정 관련성을 낮춘다 (가짜 신뢰 금지).
    sq = source_quality.classify(source, title)["source_quality"]
    if sq == "excluded":
        score = min(score, 1.0)
    elif sq == "low":
        score -= 0.5
    score = max(0.0, min(5.0, score))
    tier = TIER_C
    for threshold, band in _TIER_BANDS:
        if score >= threshold:
            tier = band
            break
    return round(score, 2), tier


def _pick_primary(members: set) -> str:
    for sec in SECTION_PRIORITY:
        if sec in members:
            return sec
    return OTHER


def _reason(primary: str, members: set, flags: dict) -> str:
    """명사형 사유 한 줄 (표시/감사용) — 분류 입력으로는 절대 쓰지 않는다."""
    parts = [EXEC_LABELS.get(primary, "기타")]
    extra = [EXEC_SHORT[s] for s in SECTION_PRIORITY
             if s in members and s != primary and s != OTHER]
    if extra:
        parts.append("·".join(extra) + " 연계")
    if flags["hdec_direct"]:
        tail = "현대건설 직접 의사결정 신호"
    elif flags["is_competitor"]:
        tail = "경쟁사·공급망 동향 — 전략 비교 신호"
    elif primary == ORDER_OVERSEAS:
        tail = "수주·발주 환경 신호"
    elif primary == RISK:
        tail = "리스크·규제 점검 신호"
    elif primary == AI:
        tail = "AI·인프라 전략 신호"
    else:
        tail = "참고 신호"
    parts.append(tail)
    return " — ".join([" / ".join(parts[:-1]), parts[-1]]) if len(parts) > 1 else parts[0]


def classify(row: dict, category=None) -> dict:
    """기사 1건의 임원 의사결정 관련성 뷰 (표시 전용 파생값).

    반환:
        primary_executive_section / secondary_executive_sections / executive_sections
        executive_label / secondary_labels
        decision_relevance_score / decision_relevance_tier / decision_reason
        hdec_direct / hdec_strategic / is_competitor / hdec_bucket
    """
    members, flags = _memberships(row, category)
    aq = article_quality.assess(row.get("source"), row.get("title"))
    primary = _pick_primary(members)
    secondary = [s for s in SECTION_PRIORITY
                 if s in members and s != primary and s != OTHER]
    score, tier = _score_and_tier(members, flags, row.get("source"),
                                  row.get("title") or "")
    return {
        "primary_executive_section": primary,
        "secondary_executive_sections": secondary,
        "executive_sections": [primary] + secondary,
        "executive_label": EXEC_LABELS.get(primary, "기타"),
        "secondary_labels": [EXEC_LABELS[s] for s in secondary],
        "decision_relevance_score": score,
        "decision_relevance_tier": tier,
        "decision_reason": _reason(primary, members, flags),
        "hdec_direct": flags["hdec_direct"],
        "hdec_strategic": flags["hdec_strategic"],
        "is_competitor": flags["is_competitor"],
        "hdec_bucket": _hdec_bucket(row, members, aq) if flags["hdec_direct"] else 9,
    }


def in_section(dr: dict, section: str) -> bool:
    """decision_relevance 결과가 해당 임원 섹션 멤버(primary or secondary)인지."""
    return section in (dr.get("executive_sections") or [])


# 회사 도배 방지용 주체 추출 — 같은 회사 기사가 한 섹션을 점유하지 않게 캡할 때 쓴다.
_PEER_COMPANIES = HDEC_NAMES + COMPETITOR_NAMES + [
    "가온전선", "ls전선", "대한전선", "대원전선", "일진전기", "효성중공업", "ls일렉트릭"]


def company_key(title: str) -> str | None:
    """제목에서 회사 주체 토큰을 뽑는다 (없으면 None). 같은 회사 반복 노출을 캡할 때 쓴다."""
    low = (title or "").lower()
    for name in _PEER_COMPANIES:
        if name.lower() in low:
            return name
    return None
