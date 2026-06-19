"""Briefing 도메인 — 기존 파이프라인 산출물에서 executive brief 파생 (P0-B2).

이 파일은 집계/요약/문구 조립만 한다:
- DB는 db.py 헬퍼로 **읽기만** 한다 (쓰기 없음, sqlite3 직접 import 금지).
- 점수·등급을 재계산하지 않는다 — article_scores에 저장된 값을 그대로 집계한다.
- insight 텍스트를 재생성하지 않는다 — 저장된 hdec_implication을
  insight.IMPLICATION_TEMPLATES 역매핑으로 카테고리에 연결할 뿐이다.
- 발송·네트워크·스케줄링을 하지 않는다.

소비처: GET /api/brief (대시보드) · scripts/build_executive_brief.py (CLI)
       · scripts/build_telegram_digest.py (Telegram 다이제스트).
"""

import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone

from app import (
    article_quality, config, db, decision_relevance, insight, macro_snapshot,
    radar, risk_events, scoring, source_quality,
)

KST = timezone(timedelta(hours=9))

HEADER = "HDEC Executive Radar"
TOP_IMMEDIATE = 3
TOP_ISSUES = 5
TOP_THEMES = 5
# P0-C1.9: 섹션별 레이더(AI/리스크·규제/수주·해외/거시경제)당 노출 신호 상한
TOP_RADAR = 5
# 카테고리 드릴다운에서 카테고리당 노출할 근거 기사 상한 (모바일 가독성 · 나머지는 '외 n건')
TOP_CATEGORY_ARTICLES = 6
TOP_DISPLAY_SCORE_FLOOR = 2.5
TOP_NEW_SCORE_FLOOR = 2.5
TOP_STALE_DAYS = 45
TOP_VERY_STALE_DAYS = 90

# P0-D3F/P0-D3L: surface 간 노출 중복 억제 — 같은 기사/제목/클러스터가 여러 상단 카드를
# 도배하지 않게 한다. D3L부터 임원 visible surface에서는 정확히 같은 article/title/url은
# 단 1회만 노출한다. 점수·등급·원문 dedup은 변경하지 않는 표시 전용 가드레일이다.
MAX_ARTICLE_TOP_SURFACES = 1
MAX_CLUSTER_TOP_SURFACES = 2
# D3L global high-exposure cluster budget. hdec_netherlands_nuclear도 canonical key에서
# nuclear_smr_project로 합산한다(현대건설 직접 원전 2건 예외가 전체 원전 flood로 번지지 않게).
GLOBAL_HIGH_EXPOSURE_CLUSTER_CAPS = {
    "nuclear_smr_project": 3,
    "ai_datacenter_power": 3,
    "smart_construction_challenge": 2,
}
CATEGORY_GLOBAL_CLUSTER_CAP = 2
# 노출 품질·중복 감사(운영자 전용)에 담을 항목 상한.
TOP_EXPOSURE_AUDIT = 30
EXPOSURE_AUDIT_NOTE = (
    "운영자 점검용 노출 품질·중복 제어 기록입니다. 동일·유사 기사는 상단에서 대표 1건만 "
    "노출하고 나머지는 근거·감사 영역에 남깁니다. 점수·등급은 변경하지 않습니다."
)
# 노출 상태 한국어 라벨 (운영자 감사 표시 전용).
EXPOSURE_STATUS_LABELS = {
    "shown_top": "상단 노출",
    "shown_category": "카테고리 근거 노출",
    "suppressed_duplicate": "중복 억제(동일 기사)",
    "suppressed_cluster_cap": "유사 기사 대표 외 억제",
    "suppressed_quality_gate": "품질 게이트 억제",
    "evidence_only": "근거/감사만",
}
# top_exposure flag / 억제 상태 → 사람이 읽는 한국어 사유 라벨 (운영자 감사 전용).
SUPPRESSION_REASON_LABELS = {
    "securities_context": "증권/주가성 문맥",
    "stock_hype": "stock-hype 제외",
    "very_stale": "매우 오래된 기사",
    "stale": "45일 초과 기사",
    "supplier_only": "공급사 단독",
    "weak_source": "약한 출처",
    "source_low": "약한 출처",
    "source_excluded": "약한 출처",
    "generic_roundup": "roundup/listicle",
    "generic_finance_roundup": "금융/PF roundup",
    "sales_promo": "분양/청약 홍보성",
    "customer_operation_ai": "고객 운영 AI",
    "sports_context": "스포츠/선수 기사",
    "competitor_only_ai_robot": "경쟁사 AI 로봇 단독",
    "suppressed_cluster_cap": "유사 기사 대표 선정됨",
    "suppressed_duplicate": "동일 기사 이미 상단 노출",
}

SECURITIES_CONTEXT_PATTERNS = (
    "주가", "목표가", "목표주가", "투자의견", "투자 의견", "투자포인트",
    "증권 레이더", "증권", "종목", "관련주", "수혜주", "테마주",
    "급등", "폭등", "상한가", "삼전닉스", "포스트워 수혜", "뭐 사야",
    # P0-D3S: 구어체 종목추천성 헤드라인(삼전/하닉/사세요/반도체주)과 株 표기 종목명을
    # 임원 상단 노출에서 제외한다(여전히 운영자 감사·참고/제외에는 남는다). stock_hype 면제된
    # 현대건설 직접 시세 기사(예: '현대건설 주가 급등')도 여기 securities_context로 걸러진다.
    "사세요", "삼전", "하닉", "반도체주", "반도체株", "건설株", "株",
    "etf", "코스피", "코스닥", "대장주", "팔고", "상장폐지", "시간외", "매수",
)
WEAK_TOP_SOURCE_PATTERNS = (
    "데일리머니", "네이버 프리미엄콘텐츠", "naver premium", "증권플러스",
    "리서치알음", "인포스탁", "팍스넷", "씽크풀",
)
GENERIC_ROUNDUP_PATTERNS = (
    "업계 소식", "오늘의 건설", "리뷰", "브리핑", "소식 모음",
    "굿모닝!", "금융권 이모저모", "은행권 이모저모",
)
GENERIC_BANKING_PATTERNS = (
    "금융권 이모저모", "은행권 이모저모", "은행권 브리핑", "은행권 소식",
)
SALES_PROMO_PATTERNS = (
    "분양", "청약", "홍보관", "모델하우스", "견본주택", "특별공급",
    "사이버 모델하우스", "분양가", "1순위 마감", "정당계약",
)
CUSTOMER_OPERATION_AI_PATTERNS = (
    "ai 상담", "ai상담", "생성형 ai", "인공지능 상담", "챗봇", "콜센터",
    "고객 응대", "고객상담", "상담 자동화", "청약 상담", "분양 상담",
    "분양상담",
)
SPORTS_CONTEXT_PATTERNS = (
    "배구", "원더독스", "스타랭킹", "박정아", "선수", "구단", "프로팀",
    "우승", "리그", "감독",
)
LOW_DIRECT_COMPETITOR_AI_PATTERNS = (
    "ai 로봇", "ai·로봇", "인공지능 로봇", "건설로봇", "스마트건설 로봇",
)
GENERIC_ENERGY_FINANCE_PATTERNS = (
    "태양광 pf", "태양광 프로젝트 파이낸싱", "태양광 금융", "solar pf",
    "신한은행", "은행", "pf 완료",
)
DIRECT_PROJECT_PATTERNS = (
    "수주", "발주", "입찰", "우선협상", "본계약", "계약", "프로젝트",
    "원전", "smr", "소형모듈원자로", "데이터센터", "데이터 센터",
    "epc", "플랜트", "착공", "준공", "특별법", "시행", "정책",
    "규제", "벌점", "제재", "중대재해", "전력망", "스마트건설",
    "스마트 건설", "r&d", "연구원", "조직", "전략", "에너지전환",
    "에너지 전환", "뉴에너지",
)
# P0-D3T: AI 관련 상단 적격성은 "AI/DC/SMR/스마트건설 토픽"과
# "건설/임원 실행 앵커"가 함께 있어야 한다. 건설·수주·전력·현대건설 같은 범용 앵커만으로
# 도시정비/재개발/에너지 금융 기사가 AI 탭에 들어오던 D3S 후속 오탐을 막는다.
AI_TOPIC_ANCHORS = (
    "ai", "인공지능", "생성형", "데이터센터", "데이터 센터", "idc",
    "ai 데이터센터", "ai 인프라", "스마트건설", "스마트 건설", "건설 ai",
    "ai 로봇", "건설로봇", "건설 로봇", "bim", "디지털트윈", "영상인식",
    "자율시공", "smr", "소형모듈원자로", "원전", "전력망", "냉각", "쿨링",
)
CONSTRUCTION_EXECUTION_ANCHORS = (
    "현대건설", "건설", "건설사", "epc", "시공", "수주", "발주", "프로젝트",
    "플랜트", "인프라", "전력 인프라", "전력인프라", "전력망", "부지", "냉각",
    "계통", "송전", "변전", "현장", "안전관리", "r&d", "연구원",
)
HDEC_AI_STRATEGIC_EXCEPTION_ANCHORS = (
    "r&d", "스마트건설", "스마트 건설", "에너지전환", "에너지 전환",
    "데이터센터", "데이터 센터", "smr", "원전", "ai",
)
EXPOSURE_TITLE_NOISE_WORDS = {
    "단독", "속보", "인터뷰", "기획", "오늘의", "업계소식", "업계", "소식",
}
EXPOSURE_FALLBACK_STOPWORDS = EXPOSURE_TITLE_NOISE_WORDS | {
    "및", "등", "관련", "대상", "위한", "한다", "개최", "진행", "확대",
}

# spread 한계 고지: 토픽 후보 집합이 겹치는 신호 수 기반의 보수적 추정이다.
# (동일 사건 클러스터링이 아니며, dedup으로 제거된 중복 기사는 집계되지 않는다)
SPREAD_METHOD = "topic-overlap heuristic — 동일 토픽 후보를 공유하는 신호 수 기반 추정"

# 운영자/표시 레이어용 짧은 고지 한 줄 (개발자용 장문 면책 대신). "추정" 표현 유지.
# P0-C1.9: '유사 주제 기사' → '관련 기사'로 임원 친화적 표현 통일.
OPERATOR_NOTE = (
    "운영자 검토용 자동 생성 브리프입니다. 관련 기사 수는 제목·주제 기준 "
    "추정값이며 동일 사건 클러스터 확정값이 아닙니다."
)

# 용어 캡션 — UI·리포트가 그대로 노출하는 단일 소스 (혼란스러운 지표 설명)
# P0-C1.9: 노이즈 표현('참고 묶음 추정')을 제거하고 '제목·주제 기준 자동 묶음'으로 정리.
SPREAD_NOTE = (
    "관련 기사 수는 제목·주제 기준 자동 묶음 추정값이며, "
    "동일 사건 클러스터 확정값은 아닙니다."
)
THEME_STRENGTH_NOTE = (
    "테마 비중은 가장 큰 테마를 100으로 둔 상대값입니다 "
    "(관련 기사 수와 중요도 점수를 합산한 내부 정렬 값 기준)."
)
# 출처 품질 고지 — UI·리포트가 그대로 노출하는 단일 소스 (P0-C1.6)
SOURCE_QUALITY_NOTE = (
    "출처 품질 필터: 블로그·카페·커뮤니티성 결과는 제외하거나 낮은 우선순위로 "
    "처리합니다. 출처 품질은 사실 보증이 아니라 랭킹/필터 가드레일입니다."
)
# 카테고리 드릴다운 고지 — UI·리포트가 그대로 노출하는 단일 소스 (P0-C1.7)
CATEGORY_DRILLDOWN_NOTE = (
    "카테고리별 근거 기사는 수집된 기사의 제목·출처·링크·중요도 기준 근거 목록입니다 "
    "(본문 전문은 저장하지 않습니다). 블로그·카페 등 비-뉴스 출처는 근거 목록에서 제외하며, "
    "카테고리 총건수에는 포함해 감사 가능하게 둡니다."
)

# 참고/제외 · 출처 품질 감사 (P0-C1.8) — 두 기준을 섞지 않는다(낮은 관련성 ≠ 출처 품질).
# 모바일 가독성을 위해 표시 상한을 두고 나머지는 '외 n건'으로 정직히 표기한다.
TOP_REVIEW_EXCLUDED = 12
TOP_SOURCE_FILTERED = 12
SOURCE_FILTERED_LABEL = "비뉴스성/낮은 신뢰 출처"
REVIEW_EXCLUDED_NOTE = (
    "참고/제외 기사: 정상 뉴스 출처이지만 현대건설 관련성·우선순위가 낮아 참고/제외로 "
    "분류된 기사입니다. 낮은 관련성·우선순위 판단이며 출처 품질 문제와는 다릅니다."
)
SOURCE_FILTERED_NOTE = (
    "출처 품질 제외: 블로그·카페·커뮤니티 등 비뉴스성/낮은 신뢰 출처로, 임원용 Top 3와 "
    "근거 목록에서 제외됩니다. 감사 투명성을 위해서만 별도로 표시합니다."
)

# 현황판 버킷 의미 설명 — UI·리포트가 그대로 노출하는 단일 소스 (P0-C1.8 용어 명확화).
# label은 scoring 등급 상수를 그대로 써서 표시 라벨과 항상 일치시킨다(이중 관리 금지).
STATUS_BOARD_LEGEND = [
    {"label": scoring.GRADE_INSTANT, "meaning": "중요도 4.5 이상 — 운영자 즉시 확인 후보"},
    {"label": "중요 신호", "meaning": "중요도 3.5~4.4 — 일간 확인 후보"},
    {"label": "관찰 신호", "meaning": "전략·반복 트렌드 — 지속 관찰 대상"},
    {"label": "참고/제외", "meaning": "낮은 관련성 또는 제외 판단"},
]

# 저장된 implication 텍스트 → insight 카테고리 키 역매핑 (탐지 로직 중복 방지)
_CATEGORY_BY_IMPLICATION = {
    text: key for key, text in insight.IMPLICATION_TEMPLATES.items()
}
_CATEGORY_BY_IMPLICATION.update({
    "현대건설 직접 언급 — 수주 경쟁력·시장 포지션 직접 영향권": "hdec",
    "현대건설 직접 연관성 낮음 — 참고 수준 모니터링 대상": "general",
})

# ---- executive_one_liner 조립용 표현 사전 (표현 전용 — 점수/등급 판단 아님) ----

SUBJECT_BY_CATEGORY = {
    "hdec": "현대건설 연관 수주 신호",
    "dc_power": "AI 데이터센터·전력 인프라 투자",
    "competitor": "경쟁사 스마트건설 행보",
    "safety": "건설현장 중대재해·안전 규제",
    "mideast_overseas": "중동·해외 발주 환경 변화",
    "macro": "환율·원자재 등 거시 변수",
    "gov": "정부 인프라 정책 드라이브",
    "smart_const": "스마트건설 기술 확산",
    "general": "일반 산업 동향",
}

OPP_ASPECT_BY_CATEGORY = {
    "hdec": "수주 경쟁력·시장 포지션 강화",
    "dc_power": "중장기 에너지 인프라 수주",
    "competitor": "기술 격차 만회",
    "safety": "스마트 안전 기술 수요",
    "mideast_overseas": "해외 발주 확대",
    "macro": "파이낸싱 여건 개선",
    "gov": "공공 인프라 발주 확대",
    "smart_const": "생산성·안전 기술 선점",
    "general": "참고 수준의 기회",
}

RISK_ASPECT_BY_CATEGORY = {
    "hdec": "평판·수주 일정",
    "dc_power": "전력 인프라 수주 경쟁 심화",
    "competitor": "수주 경쟁 구도 악화",
    "safety": "단기 평판·수주 자격",
    "mideast_overseas": "해외 원가·발주 지연",
    "macro": "원가·파이낸싱 부담",
    "gov": "규제·예산 변동",
    "smart_const": "기술 투자 지연",
    "general": "참고 수준의 리스크",
}

# 표시용 액션 라벨 — 저장된 alert_grade의 표현일 뿐, 등급을 재계산하지 않는다.
ACTION_LABEL_BY_GRADE = {
    scoring.GRADE_INSTANT: "즉시 확인",
    scoring.GRADE_DAILY: "검토 필요",
    scoring.GRADE_WEEKLY: "추적 필요",
    scoring.GRADE_EXCLUDED: "모니터링",
}

# 점수대 라벨 — final_score(0~5) 기준 직관 버킷 (점수 축 표현, 등급 재계산 아님)
SCORE_BANDS = [
    (4.5, "즉시 확인"),
    (3.5, "검토 필요"),
    (2.0, "추적 필요"),
    (0.0, "참고/제외"),
]

# 카드/리포트에 노출할 점수 구성요소 6종 (article_scores 9항목 중 핵심) — 표시 전용
SCORE_COMPONENT_KEYS = [
    ("hdec_relevance", "현대건설 관련성"),
    ("business_opportunity", "사업기회"),
    ("risk_potential", "리스크/규제"),
    ("urgency", "긴급도"),
    ("source_reliability", "출처 신뢰도"),
    ("trend_repeat", "반복/확산 신호"),
]


def score_band(final_score) -> str:
    """final_score(0~5)를 직관 버킷 라벨로 — 표시 전용 (등급 판정과 별개의 점수 축)."""
    s = final_score or 0
    for threshold, label in SCORE_BANDS:
        if s >= threshold:
            return label
    return "참고/제외"


def _score_components(score: dict | None) -> list[dict]:
    """저장된 점수에서 표시용 구성요소 6종을 0~5 값으로 뽑는다 (재계산 없음)."""
    score = score or {}
    return [{"key": k, "label": label, "value": score.get(k)}
            for k, label in SCORE_COMPONENT_KEYS if score.get(k) is not None]


def _derive_news_mode(rows: list[dict]) -> str:
    """저장된 기사 signal_origin으로 실제 뉴스 출처 모드를 판별한다 (DB가 단일 진실)."""
    for row in rows:
        if "live" in (row.get("signal_origin") or "").lower():
            return "live"
    return "mock"


def _josa(word: str, with_batchim: str, without: str) -> str:
    """받침 유무에 따른 조사 선택. 한글이 아니면 without을 쓴다."""
    ch = word[-1] if word else ""
    if "가" <= ch <= "힣":
        return with_batchim if (ord(ch) - 0xAC00) % 28 else without
    return without


def _parse_topics(row: dict) -> list[str]:
    try:
        topics = json.loads(row.get("topic_candidates") or "[]")
    except ValueError:
        return []
    return [t for t in topics if isinstance(t, str)]


def _age_days(published_at: str) -> float | None:
    try:
        published = datetime.fromisoformat(published_at)
    except (TypeError, ValueError):
        return None
    now = datetime.now(published.tzinfo)
    return max(0.0, (now - published).total_seconds() / 86400)


def _freshness_rank(row: dict) -> int:
    age = _age_days(row.get("published_at"))
    if age is None:
        return 1
    if age <= scoring.DAILY_FRESH_DAYS:
        return 0
    if age <= scoring.BACKGROUND_MAX_DAYS:
        return 1
    return 9


def _category_key(detail: dict | None) -> str:
    implication = (((detail or {}).get("insight")) or {}).get("hdec_implication") or ""
    return _CATEGORY_BY_IMPLICATION.get(implication.strip(), "general")


def _build_spreads(scored_rows: list[dict]) -> dict[str, dict]:
    """기사별 spread 지표를 한 번에 계산한다."""
    topic_sets = {r["id"]: set(_parse_topics(r)) for r in scored_rows}
    source_by_id = {r["id"]: (r.get("source") or "출처 미상") for r in scored_rows}
    spreads = {}
    for row in scored_rows:
        own = topic_sets[row["id"]]
        related = [rid for rid, topics in topic_sets.items()
                   if rid != row["id"] and own and (own & topics)]
        sources = {source_by_id[row["id"]]} | {source_by_id[rid] for rid in related}
        related_count = len(related)
        source_count = len(sources)
        # 보수적 표현: "n개 매체 보도"·"확산" 같은 확정 표현 금지 (자동 묶음 추정치).
        # P0-C1.9: '유사 주제 기사' → '관련 기사'로 임원 친화적 표현 통일.
        label = (f"관련 기사 {related_count}건 · 출처 {source_count}곳"
                 if related_count else "단독 신호")
        spreads[row["id"]] = {
            "related_count": related_count,
            "source_count": source_count,
            "label": label,
        }
    return spreads


def _signal_entry(rank: int, row: dict, category_key: str, implication: str,
                  spread: dict, score: dict | None = None,
                  section: str | None = None, decision: dict | None = None) -> dict:
    topics = _parse_topics(row)
    # 출처 품질 라벨 (P0-C1.6) — 표시 전용 파생값. 저장된 source/title을 분류만 한다
    # (점수/등급 재계산 아님). 신뢰 출처/일반 출처/낮은 신뢰도를 UI·리포트가 노출한다.
    quality = source_quality.classify(row.get("source"), row.get("title"))
    entry = {
        "rank": rank,
        "article_id": row["id"],
        "title": row["title"],
        "source": row.get("source") or "출처 미상",
        # 임원 표시용 출처 — 집계 호스트(v.daum.net 등)는 'Daum 경유'로 정규화 (P0-C1.11).
        # raw source 필드는 위에 그대로 보존한다 (내부/감사용).
        "display_source": source_quality.normalize_display_source(
            row.get("source")) or "출처 미상",
        "published_at": row.get("published_at"),
        "source_quality": quality["source_quality"],
        "source_quality_label": quality["source_quality_label"],
        "source_quality_reason": quality["source_quality_reason"],
        "source_type": quality["source_type"],
        "topic": topics[0] if topics else None,
        "category": category_key,
        "category_label": insight.CATEGORY_PHRASE.get(category_key, "건설산업 일반"),
        # P0-C1.9: 임원 IA 레이더 섹션 (ai/risk_regulation/business_overseas/macro_economy/other)
        "radar_section": section,
        "radar_label": radar.RADAR_LABELS.get(section) if section else None,
        "final_score": row.get("final_score"),
        "score_band": score_band(row.get("final_score")),
        "score_components": _score_components(score),
        "alert_grade": row.get("alert_grade"),
        "action_label": ACTION_LABEL_BY_GRADE.get(row.get("alert_grade"), "모니터링"),
        "confidence": row.get("confidence"),
        "opportunity_or_risk": row.get("opportunity_or_risk") or "관찰",
        "implication": implication,
        "one_line_reason": implication,
        "exposure_cluster_key": _exposure_cluster_key(row),
        "spread": spread,
        "url": row.get("url"),
    }
    # 임원 의사결정 관련성 (P0-C1.12) — primary/secondary 임원 섹션 + 티어/사유.
    # 표시·랭킹 가드레일이며 점수/등급을 재계산하지 않는다 (decision_relevance가 단일 소유).
    if decision:
        entry["decision_relevance_score"] = decision["decision_relevance_score"]
        entry["decision_relevance_tier"] = decision["decision_relevance_tier"]
        entry["decision_reason"] = decision["decision_reason"]
        entry["executive_section"] = decision["primary_executive_section"]
        entry["executive_label"] = decision["executive_label"]
        entry["secondary_sections"] = decision["secondary_executive_sections"]
        entry["secondary_labels"] = decision["secondary_labels"]
        # P0-C1.13: Telegram이 현대건설 직접 그룹핑·공급사 후순위·재무 라우팅을 재계산 없이
        # 쓰도록 분류 결과를 그대로 전달한다 (decision_relevance가 단일 소유, 표시 가드레일).
        entry["hdec_bucket"] = decision.get("hdec_bucket")
        entry["is_competitor"] = decision.get("is_competitor")
        entry["is_finance"] = decision.get("is_finance")
        entry["supplier_only"] = decision.get("supplier_only")
        entry["order_class"] = decision.get("order_class")
        entry.update(_top_exposure_profile(row, decision))
    # 리스크·규제 신호는 risk_priority(중요도가 낮아도 상단 노출)·사유·라벨을 부착한다.
    if section == radar.RISK:
        rf = radar.risk_fields(row, score)
        entry.update(rf)
        entry["one_line_reason"] = rf["risk_reason"]
    return entry


def _is_excluded_quality(row: dict) -> bool:
    """블로그/카페/커뮤니티성 출처인지 — Top 3/Top 5 노출에서 배제할지 판정 (P0-C1.6)."""
    return source_quality.classify(
        row.get("source"), row.get("title"))["source_quality"] == "excluded"


def _contains_any(text: str, patterns) -> bool:
    low = (text or "").lower()
    return any((p or "").lower() in low for p in patterns)


def _ai_top_eligible(row: dict) -> bool:
    """AI 관련 상단 노출 적격성 (P0-D3T) — 토픽 앵커와 실행 앵커를 모두 요구한다.

    radar가 AI로 분류했어도 도시정비·재개발·일반 에너지 금융처럼 범용 건설/전력 단어만 있는
    기사는 AI 탭에서 제외한다. 표시 가드레일이며 점수/등급/분류는 바꾸지 않는다."""
    text = " ".join([row.get("title") or "", row.get("snippet") or ""])
    if ("현대건설" in text
            and _contains_any(text, HDEC_AI_STRATEGIC_EXCEPTION_ANCHORS)):
        return True
    return (_contains_any(text, AI_TOPIC_ANCHORS)
            and _contains_any(text, CONSTRUCTION_EXECUTION_ANCHORS))


# P0-D3S Goal E: 'AI 데이터센터·전력 인프라'(dc_power) 카테고리 근거 목록 적격성.
# DC/전력 인프라 신호 + 건설/실행 앵커가 함께 있어야 근거로 노출한다 — 외국인 안전교육·
# 화학물질 규제·종목성 기사처럼 broad 키워드로 잘못 묶인 비직접 항목을 근거에서 강등한다.
DC_POWER_INFRA_TERMS = (
    "데이터센터", "데이터 센터", "idc", "전력", "전력망", "전력 인프라", "전력인프라",
    "계통", "송배전", "송전", "변전", "냉각", "쿨링", "epc", "수주", "부지",
    "원전", "smr", "소형모듈원자로", "에너지 인프라",
)
DC_POWER_CONSTRUCTION_ANCHORS = (
    "건설", "건설사", "epc", "현대건설", "수주", "발주", "프로젝트", "인프라",
    "플랜트", "원전", "전력망", "냉각", "부지", "시공", "착공", "준공",
)


def _dc_power_evidence_ok(row: dict) -> bool:
    """dc_power 근거 적격성 — DC/전력 인프라 신호 + 건설/실행 앵커가 둘 다 있어야 한다."""
    text = " ".join([row.get("title") or "", row.get("snippet") or ""])
    return (_contains_any(text, DC_POWER_INFRA_TERMS)
            and _contains_any(text, DC_POWER_CONSTRUCTION_ANCHORS))


def _is_sales_promo_text(text: str) -> bool:
    return _contains_any(text, SALES_PROMO_PATTERNS)


def _is_customer_operation_ai_text(text: str) -> bool:
    return (
        _contains_any(text, CUSTOMER_OPERATION_AI_PATTERNS)
        and _contains_any(text, ("ai", "인공지능", "생성형", "챗봇"))
    )


def _is_customer_operation_ai_sales(row: dict) -> bool:
    text = " ".join([row.get("title") or "", row.get("snippet") or ""])
    return _is_sales_promo_text(text) and _is_customer_operation_ai_text(text)


def _surface_quality_excluded(row: dict, decision: dict | None, surface: str) -> bool:
    if _is_top_exposure_excluded(row, decision):
        return True
    if _is_customer_operation_ai_sales(row):
        return True
    return False


def _normalize_exposure_text(text: str) -> str:
    """노출 클러스터링용 제목 정규화 — 표시 선택 전용, DB dedup과 분리."""
    normalized = unicodedata.normalize("NFKC", text or "").lower()
    normalized = re.sub(r"[\[\]{}()<>〈〉《》「」『』\"'“”‘’`´]+", " ", normalized)
    normalized = re.sub(r"[-–—→←·ㆍ/|:;,.!?~…]+", " ", normalized)
    normalized = re.sub(r"[^0-9a-z가-힣]+", " ", normalized)
    tokens = [t for t in normalized.split()
              if t and t not in EXPOSURE_TITLE_NOISE_WORDS]
    return " ".join(tokens)


def _exposure_cluster_key(row: dict) -> str:
    """상단 노출용 near-duplicate 클러스터 키 (점수/등급/원문 dedup 불변)."""
    title = _normalize_exposure_text(row.get("title") or "")
    snippet = _normalize_exposure_text(row.get("snippet") or "")
    text = f"{title} {snippet}".strip()

    is_hdec_nuclear = (
        "현대건설" in text
        and ("네덜란드" in text or "웨스팅하우스" in text)
        and _contains_any(text, ("원전", "smr", "소형모듈원자로", "대형원전"))
    )
    if is_hdec_nuclear:
        return "hdec_netherlands_nuclear"

    is_smart_challenge = (
        _contains_any(text, ("스마트건설", "스마트 건설"))
        and "챌린지" in text
        and _contains_any(text, ("ai", "로봇", "국토부", "건설현장", "안전", "품질"))
    )
    if is_smart_challenge:
        return "smart_construction_challenge"

    if _contains_any(text, (
            "원전", "smr", "소형모듈원자로", "네덜란드", "웨스팅하우스",
            "후보지", "대형원전")):
        return "nuclear_smr_project"

    has_ai_datacenter_phrase = _contains_any(
        text, ("ai 데이터센터", "ai 데이터 센터"))
    has_datacenter_power = (
        _contains_any(text, ("데이터센터", "데이터 센터"))
        and _contains_any(text, (
            "전력망", "전력 인프라", "전력인프라", "냉각", "ppa", "분산전원",
        ))
    )
    if has_ai_datacenter_phrase or has_datacenter_power:
        return "ai_datacenter_power"

    meaningful = [t for t in title.split()
                  if t not in EXPOSURE_FALLBACK_STOPWORDS]
    return "title:" + " ".join(meaningful[:8]) if meaningful else "title:unknown"


def _global_exposure_cluster_key(row: dict) -> str:
    """상단 노출 예산용 canonical cluster key."""
    key = _exposure_cluster_key(row)
    if key == "hdec_netherlands_nuclear":
        return "nuclear_smr_project"
    return key


def _hdec_direct_project_for_cluster(row: dict, decision: dict | None) -> bool:
    if not (decision or {}).get("hdec_direct"):
        return False
    return "direct_project_signal" in _top_exposure_profile(
        row, decision).get("top_exposure_flags", [])


def _cluster_source_key(row: dict) -> str:
    return source_quality.normalize_display_source(
        row.get("source")) or row.get("source") or "출처 미상"


def _cap_exposure_clusters(rows: list[dict], limit: int, *,
                           max_per_cluster: int = 1,
                           decisions: dict[str, dict] | None = None,
                           hdec_direct_pair_cap: bool = False,
                           global_cluster_counts: dict[str, int] | None = None,
                           global_cluster_keys: set[str] | None = None) -> list[dict]:
    """정렬된 후보에서 유사 기사 노출 수만 제한한다. 순서는 바꾸지 않는다."""
    picked: list[dict] = []
    counts: dict[str, int] = {}
    sources_by_cluster: dict[str, set[str]] = {}
    decisions = decisions or {}

    for row in rows:
        key = _exposure_cluster_key(row)
        current = counts.get(key, 0)
        use_global = (
            global_cluster_counts is not None
            and (global_cluster_keys is None or key in global_cluster_keys)
        )
        global_current = global_cluster_counts.get(key, 0) if use_global else 0
        cap = max_per_cluster
        source_key = _cluster_source_key(row)
        allow_pair = (
            hdec_direct_pair_cap
            and _hdec_direct_project_for_cluster(row, decisions.get(row["id"]))
        )
        if allow_pair:
            cap = max(cap, 2)
            seen_sources = sources_by_cluster.setdefault(key, set())
            if current >= 1 and source_key in seen_sources:
                continue

        if current >= cap or global_current >= cap:
            continue

        picked.append(row)
        counts[key] = current + 1
        if use_global:
            global_cluster_counts[key] = global_current + 1
        if allow_pair:
            sources_by_cluster.setdefault(key, set()).add(source_key)
        if len(picked) >= limit:
            break

    if not picked and rows:
        # 빈 섹션 방지로 최소 1건은 보여주되, 전부 '전역 클러스터 캡'에 막힌 경우엔 빈 채로 둔다
        # (같은 cluster를 카테고리마다 반복 노출하지 않기 위함). 카운트 기반이라 global_cluster_keys가
        # None(전체 클러스터 대상)일 때도 올바르게 동작한다 (P0-D3F: smart-only → 전체 클러스터 일반화).
        all_rows_globally_capped = (
            global_cluster_counts is not None
            and all(global_cluster_counts.get(_exposure_cluster_key(r), 0) >= max_per_cluster
                    for r in rows)
        )
        if not all_rows_globally_capped:
            return rows[:1]
    return picked


def _norm_url(url) -> str:
    """surface dedup용 URL 정규화 — 표시 선택 전용 (collector의 url_hash dedup과 별개)."""
    u = str(url or "").strip().lower()
    return u.rstrip("/")


class _ExposureSurfaceState:
    """surface 간 노출 중복 억제 상태 (표시 전용 — 점수/등급/원문 dedup 불변, P0-D3F).

    같은 기사가 여러 상단 카드에 반복되거나(동일 기사·동일 URL·동일 정규화 제목), 같은
    cluster가 여러 surface를 도배하는 것을 막는다. '의도된 multi-section'(예: 현대건설 벌점 =
    현대건설 연관 + 리스크·규제)을 보존하려고 기사당 surface 수에 cap을 둘 뿐 완전히 지우지
    않는다. 억제 결정은 audit으로 남겨 운영자가 사유를 볼 수 있게 한다.
    """

    def __init__(self) -> None:
        self.article_surfaces: dict[str, set[str]] = {}   # id -> {surface}
        self.url_owner: dict[str, str] = {}               # 정규화 URL -> 최초 노출 id
        self.title_owner: dict[str, str] = {}             # 정규화 제목 -> 최초 노출 id
        self.cluster_surfaces: dict[str, set[str]] = {}   # cluster -> {surface}
        self.cluster_owner: dict[str, str] = {}           # cluster -> 대표(최초) id
        self.global_cluster_counts: dict[str, int] = {}   # canonical cluster -> visible count
        self.category_cluster_counts: dict[str, int] = {} # canonical cluster -> category count
        self.audit: dict[str, dict] = {}                  # id -> 억제 사유/대표

    def alias_owner(self, row: dict):
        """이미 노출된 동일 기사(같은 URL/정규화 제목, 다른 id)의 대표 id (없으면 None)."""
        aid = row["id"]
        url = _norm_url(row.get("url"))
        if url and self.url_owner.get(url) not in (None, aid):
            return self.url_owner[url]
        title = _normalize_exposure_text(row.get("title") or "")
        if title and self.title_owner.get(title) not in (None, aid):
            return self.title_owner[title]
        return None

    def surface_count(self, row: dict) -> int:
        return len(self.article_surfaces.get(row["id"], ()))

    def register(self, row: dict, surface: str, cluster: str, *,
                 count_global: bool = True, count_category: bool = False) -> None:
        aid = row["id"]
        self.article_surfaces.setdefault(aid, set()).add(surface)
        url = _norm_url(row.get("url"))
        if url:
            self.url_owner.setdefault(url, aid)
        title = _normalize_exposure_text(row.get("title") or "")
        if title:
            self.title_owner.setdefault(title, aid)
        self.cluster_surfaces.setdefault(cluster, set()).add(surface)
        self.cluster_owner.setdefault(cluster, aid)
        if count_global:
            gkey = _global_exposure_cluster_key(row)
            self.global_cluster_counts[gkey] = self.global_cluster_counts.get(gkey, 0) + 1
            if count_category:
                self.category_cluster_counts[gkey] = (
                    self.category_cluster_counts.get(gkey, 0) + 1
                )

    def note_audit(self, row: dict, status: str, representative=None) -> None:
        rec = self.audit.setdefault(
            row["id"], {"status": status, "representative": None})
        rec["status"] = status
        if representative and representative != row["id"] and not rec["representative"]:
            rec["representative"] = representative

    def exact_representative(self, row: dict):
        aid = row["id"]
        if aid in self.article_surfaces:
            return aid
        return self.alias_owner(row)

    def global_cluster_capped(self, row: dict, decision: dict | None = None, *,
                              category: bool = False) -> bool:
        gkey = _global_exposure_cluster_key(row)
        cap = GLOBAL_HIGH_EXPOSURE_CLUSTER_CAPS.get(gkey)
        if cap is not None and self.global_cluster_counts.get(gkey, 0) >= cap:
            return True
        if category and self.category_cluster_counts.get(gkey, 0) >= CATEGORY_GLOBAL_CLUSTER_CAP:
            if not _hdec_direct_project_for_cluster(row, decision):
                return True
        return False


def _filter_surface_exposures(rows: list[dict], limit: int, *,
                              decisions: dict[str, dict] | None,
                              surface: str, state: _ExposureSurfaceState,
                              max_per_cluster: int = 1,
                              hdec_direct_pair_cap: bool = False,
                              max_article_surfaces: int = MAX_ARTICLE_TOP_SURFACES,
                              max_cluster_surfaces: int = MAX_CLUSTER_TOP_SURFACES,
                              count_global: bool = True,
                              count_category: bool = False
                              ) -> list[dict]:
    """정렬된 후보에서 surface 간 중복(동일 기사·동일 cluster 도배)을 억제하며 limit개를 고른다.

    - 동일 기사: 이미 max_article_surfaces개 상단 surface에 노출(또는 같은 URL/제목이 다른
      id로 노출)됐으면 양보(audit). top_new는 max_article_surfaces=1로 호출해 '다른 카드에
      없는' 기사만 남긴다(의도된 multi-section은 max=2로 보존).
    - 동일 cluster: 이미 max_cluster_surfaces개 surface를 점유했으면 새 surface 진입을 양보.
    - 양보분은 백필(정렬된 후보를 끝까지 훑어 다음 후보로 채움)로 빈 카드를 피한다.
    - per-list cluster cap(같은 surface 안 max_per_cluster, 현대건설 직접 쌍 예외)은 기존대로.
    순서·랭킹은 바꾸지 않는다 — D3D 정렬을 그대로 두고 중복만 억제한다.
    """
    picked: list[dict] = []
    local_counts: dict[str, int] = {}
    sources_by_cluster: dict[str, set[str]] = {}
    decisions = decisions or {}

    for row in rows:
        aid = row["id"]
        cluster = _exposure_cluster_key(row)
        decision = decisions.get(aid)
        if (not surface.startswith("category:")
                and _surface_quality_excluded(row, decision, surface)):
            state.note_audit(row, "suppressed_quality_gate")
            continue
        # 1) cross-surface 동일 기사/title/url cap (D3L: visible surface 단 1회)
        alias = state.exact_representative(row)
        if alias is not None or state.surface_count(row) >= max_article_surfaces:
            state.note_audit(row, "suppressed_duplicate", representative=alias or aid)
            continue
        # 2) global high-exposure cluster cap (top30 flood 방지)
        if count_global and state.global_cluster_capped(
                row, decision, category=count_category):
            state.note_audit(row, "suppressed_cluster_cap",
                             representative=state.cluster_owner.get(cluster))
            continue
        # 3) per-list cluster cap (+ 현대건설 직접 쌍 예외: 다른 출처면 2건 허용)
        cap = max_per_cluster
        source_key = _cluster_source_key(row)
        allow_pair = (hdec_direct_pair_cap
                      and _hdec_direct_project_for_cluster(row, decision))
        local = local_counts.get(cluster, 0)
        if allow_pair:
            cap = max(cap, 2)
            seen_sources = sources_by_cluster.setdefault(cluster, set())
            if local >= 1 and source_key in seen_sources:
                continue
        if local >= cap:
            continue
        # 3) cross-surface cluster cap — 이 surface 첫 진입 때만 검사(같은 surface 내 복수는 per-list가 관리)
        if local == 0 and not allow_pair:
            other_surfaces = state.cluster_surfaces.get(cluster, set()) - {surface}
            if len(other_surfaces) >= max_cluster_surfaces:
                state.note_audit(row, "suppressed_cluster_cap",
                                 representative=state.cluster_owner.get(cluster))
                continue
        picked.append(row)
        local_counts[cluster] = local + 1
        if allow_pair:
            sources_by_cluster.setdefault(cluster, set()).add(source_key)
        state.register(row, surface, cluster, count_global=count_global,
                       count_category=count_category)
        if len(picked) >= limit:
            break

    # 빈 surface 방지 — 단, 이미 다른 곳에 충분히 노출된 기사는 재노출하지 않는다.
    if not picked and rows:
        for row in rows:
            decision = decisions.get(row["id"])
            if (not surface.startswith("category:")
                    and _surface_quality_excluded(row, decision, surface)):
                state.note_audit(row, "suppressed_quality_gate")
                continue
            if (state.exact_representative(row) is None
                    and state.surface_count(row) < max_article_surfaces
                    and not (count_global and state.global_cluster_capped(
                        row, decision, category=count_category))):
                picked.append(row)
                state.register(row, surface, _exposure_cluster_key(row),
                               count_global=count_global,
                               count_category=count_category)
                break
    return picked


def _build_exposure_audit(scored_rows: list[dict], decisions: dict[str, dict],
                          shown_top_ids: set, shown_cat_ids: set) -> dict:
    """노출 품질·중복 감사 (P0-D3F, 운영자 전용) — 어디에 노출/억제됐는지 투명화.

    self-contained 추론: 노출 집합(shown_top/shown_cat)과 cluster/URL/제목 대표를 비교해
    각 기사의 상태를 정한다(품질 게이트 > 동일 기사 중복 > 유사 기사 대표 외 > 근거만).
    저장된 점수·등급을 그대로 옮길 뿐 재계산하지 않는다. top_exposure flag가 있거나 억제된
    기사만 담고(최대 TOP_EXPOSURE_AUDIT건), 임원 카드에는 raw 키를 노출하지 않는다.
    """
    shown_ids = shown_top_ids | shown_cat_ids
    # 노출된 기사 기준 대표(최초) id — cluster/URL/제목별. 미노출 기사가 어떤 대표에 양보했는지 추적.
    cluster_rep: dict[str, str] = {}
    url_rep: dict[str, str] = {}
    title_rep: dict[str, str] = {}
    for row in scored_rows:
        if row["id"] not in shown_ids:
            continue
        cluster_rep.setdefault(_exposure_cluster_key(row), row["id"])
        url = _norm_url(row.get("url"))
        if url:
            url_rep.setdefault(url, row["id"])
        title = _normalize_exposure_text(row.get("title") or "")
        if title:
            title_rep.setdefault(title, row["id"])

    entries = []
    for row in scored_rows:
        rid = row["id"]
        # 출처 품질 제외(블로그·카페·커뮤니티)는 전용 '출처 품질 감사' 섹션이 단일 소유한다 —
        # 여기(노출 품질·중복 감사)서는 다루지 않는다(섹션 역할 중복·혼선 방지).
        if _is_excluded_quality(row):
            continue
        decision = decisions.get(rid)
        profile = _top_exposure_profile(row, decision)
        flags = profile["top_exposure_flags"]
        cluster = _exposure_cluster_key(row)
        representative = None

        if rid in shown_top_ids:
            status = "shown_top"
        elif rid in shown_cat_ids:
            status = "shown_category"
        elif profile["top_exposure_excluded"] or _is_excluded_quality(row):
            status = "suppressed_quality_gate"
        else:
            url = _norm_url(row.get("url"))
            title = _normalize_exposure_text(row.get("title") or "")
            if url and url_rep.get(url) not in (None, rid):
                status, representative = "suppressed_duplicate", url_rep[url]
            elif title and title_rep.get(title) not in (None, rid):
                status, representative = "suppressed_duplicate", title_rep[title]
            elif cluster_rep.get(cluster) not in (None, rid):
                status, representative = "suppressed_cluster_cap", cluster_rep[cluster]
            else:
                status = "evidence_only"

        is_suppressed = status.startswith("suppressed")
        labels = []
        for flag in flags:
            label = SUPPRESSION_REASON_LABELS.get(flag)
            if label and label not in labels:
                labels.append(label)
        if SUPPRESSION_REASON_LABELS.get(status) and SUPPRESSION_REASON_LABELS[status] not in labels:
            labels.append(SUPPRESSION_REASON_LABELS[status])
        # 감사 대상은 '왜 강등/억제됐나'가 있는 기사만 — 억제됐거나 부정적 사유 라벨이 있을 때.
        # trusted_source·direct_project_signal 같은 긍정 flag만 있는 정상 노출 기사는 제외(노이즈 방지).
        if not labels and not is_suppressed:
            continue

        entries.append({
            "article_id": rid,
            "title": row["title"],
            "source": row.get("source") or "출처 미상",
            "display_source": source_quality.normalize_display_source(
                row.get("source")) or "출처 미상",
            "published_at": row.get("published_at"),
            "final_score": row.get("final_score"),
            "alert_grade": row.get("alert_grade"),
            "exposure_cluster_key": cluster,
            "top_exposure_flags": flags,
            "top_exposure_penalty": profile["top_exposure_penalty"],
            "top_exposure_excluded": profile["top_exposure_excluded"],
            "exposure_surface_status": status,
            "suppression_reason_labels": labels,
            "representative_article_id": representative,
        })

    # 운영자가 가장 의심스러운 항목부터 보게 — 억제/감점 큰 것 먼저.
    order = {"suppressed_quality_gate": 0, "suppressed_duplicate": 1,
             "suppressed_cluster_cap": 2, "evidence_only": 3,
             "shown_category": 4, "shown_top": 5}
    entries.sort(key=lambda e: (
        order.get(e["exposure_surface_status"], 9),
        -(e["top_exposure_penalty"] or 0),
        -(e["final_score"] or 0), e["article_id"]))
    total = len(entries)
    shown = entries[:TOP_EXPOSURE_AUDIT]
    return {
        "items": shown,
        "total_count": total,
        "shown_count": len(shown),
        "remaining_count": max(0, total - len(shown)),
        "note": EXPOSURE_AUDIT_NOTE,
        "status_labels": EXPOSURE_STATUS_LABELS,
    }


def _top_exposure_profile(row: dict, decision: dict | None = None) -> dict:
    """상단 노출 품질 프로필 — 저장 점수/등급은 바꾸지 않는 표시 전용 가드레일."""
    title = row.get("title") or ""
    source = row.get("source") or ""
    snippet = row.get("snippet") or ""
    text = " ".join([title, snippet])
    quality = source_quality.classify(source, title)
    aq = article_quality.assess(source, title, snippet)
    age = _age_days(row.get("published_at"))

    flags: list[str] = []
    penalty = 0
    exclude_top = False

    if age is not None and age > TOP_VERY_STALE_DAYS:
        flags.append("very_stale")
        penalty += 100
        exclude_top = True
    elif age is not None and age > TOP_STALE_DAYS:
        flags.append("stale")
        penalty += 35

    stock_hype = bool((decision or {}).get("stock_hype") or aq.get("stock_hype"))
    securities_context = _contains_any(title, SECURITIES_CONTEXT_PATTERNS)
    if stock_hype:
        flags.append("stock_hype")
        penalty += 100
        exclude_top = True
    elif securities_context:
        flags.append("securities_context")
        penalty += 90
        exclude_top = True

    # 중대재해·제재·규제 등 강한 리스크 맥락은 노이즈 가드(스포츠/판촉)에서 면제한다.
    # '특별감독'의 '감독'이 스포츠 패턴('감독')에 오탐돼 중대재해·특별감독 신호가 상단
    # 노출에서 통째로 제외(exclude_top)되던 회귀를 막는다 — 리스크·규제 기사는 종합 중요도가
    # 낮아도 버려지지 않는다(리스크 레이더 surface 계약, P0-C1.9). sales_promo도 동일 가드.
    strong_risk_context = _contains_any(text, (
        "벌점", "제재", "중대재해", "영업정지", "영업 정지", "입찰제한",
        "입찰 제한", "사전통보", "과징금", "행정처분", "처분", "부실시공",
        "특별감독", "하자", "품질 점검", "품질점검", "소송", "압수수색",
    ))

    sports_context = (_contains_any(text, SPORTS_CONTEXT_PATTERNS)
                      and not strong_risk_context)
    if sports_context:
        flags.append("sports_context")
        penalty += 120
        exclude_top = True

    sales_promo = _is_sales_promo_text(text) and not strong_risk_context
    customer_operation_ai = _is_customer_operation_ai_text(text)
    if sales_promo and not customer_operation_ai:
        flags.append("sales_promo")
        penalty += 95
        exclude_top = True
    elif sales_promo and customer_operation_ai:
        flags.append("customer_operation_ai")
        penalty += 20

    generic_banking = _contains_any(title, GENERIC_BANKING_PATTERNS)
    energy_finance = _contains_any(text, GENERIC_ENERGY_FINANCE_PATTERNS)
    clear_dc_epc = (
        _contains_any(text, ("데이터센터", "데이터 센터"))
        and _contains_any(text, ("epc", "건설", "시공", "수주", "전력 인프라",
                                 "전력망", "냉각", "프로젝트"))
    )
    if generic_banking or (energy_finance and not clear_dc_epc):
        flags.append("generic_finance_roundup")
        penalty += 80
        exclude_top = True

    source_low = source.lower()
    if quality["source_quality"] in ("low", "excluded"):
        flags.append(f"source_{quality['source_quality']}")
        penalty += 30
    elif _contains_any(source_low, WEAK_TOP_SOURCE_PATTERNS):
        flags.append("weak_source")
        penalty += 25

    if (decision or {}).get("supplier_only"):
        flags.append("supplier_only")
        penalty += 25
    if _contains_any(title, GENERIC_ROUNDUP_PATTERNS):
        flags.append("generic_roundup")
        penalty += 80
        exclude_top = True
    if aq.get("low_actionability"):
        flags.append("low_actionability")
        penalty += 15

    competitor_only_ai_robot = (
        not (decision or {}).get("hdec_direct")
        and _contains_any(text, decision_relevance.COMPETITOR_NAMES)
        and _contains_any(text, LOW_DIRECT_COMPETITOR_AI_PATTERNS)
        and not _contains_any(text, ("수주", "epc", "데이터센터", "데이터 센터",
                                     "프로젝트", "정책", "특별법", "규제",
                                     "중대재해", "전력망", "발주", "계약"))
    )
    if competitor_only_ai_robot:
        flags.append("competitor_only_ai_robot")
        penalty += 45

    direct_project = (
        _contains_any(text, DIRECT_PROJECT_PATTERNS)
        and not securities_context
        and not (decision or {}).get("supplier_only")
        and not _contains_any(title, GENERIC_ROUNDUP_PATTERNS)
        and not aq.get("low_actionability")
    )
    if direct_project:
        flags.append("direct_project_signal")
        penalty -= 20

    if quality["source_quality"] == "trusted":
        flags.append("trusted_source")
        penalty -= 5

    return {
        "top_exposure_penalty": penalty,
        "top_exposure_flags": flags,
        "top_exposure_excluded": exclude_top,
        "top_exposure_age_days": round(age, 1) if age is not None else None,
    }


def _top_exposure_sort_key(row: dict, decision: dict | None = None):
    profile = _top_exposure_profile(row, decision)
    return (
        profile["top_exposure_penalty"],
        _freshness_rank(row),
        -((decision or {}).get("decision_relevance_score") or 0),
        -(row.get("final_score") or 0),
        row["id"],
    )


def _is_top_exposure_excluded(row: dict, decision: dict | None = None) -> bool:
    return _top_exposure_profile(row, decision)["top_exposure_excluded"]


def _has_http_url(url) -> bool:
    return bool(url) and str(url).startswith(("http://", "https://"))


def _category_article_entry(row: dict, category_key: str, implication: str,
                            decision: dict | None = None) -> dict:
    """카테고리 드릴다운용 근거 기사 항목 — 표시 전용 파생값 (P0-C1.7).

    저장된 article/score 값을 그대로 옮길 뿐 점수·등급을 재계산하지 않는다.
    제목/출처/링크/시각/중요도만 담는다 — 본문 전문은 절대 싣지 않는다 (rules.md §3).
    """
    quality = source_quality.classify(row.get("source"), row.get("title"))
    url = row.get("url")
    entry = {
        "article_id": row["id"],
        "title": row["title"],
        "source": row.get("source") or "출처 미상",
        "display_source": source_quality.normalize_display_source(
            row.get("source")) or "출처 미상",
        "source_quality": quality["source_quality"],
        "source_quality_label": quality["source_quality_label"],
        "published_at": row.get("published_at"),
        "url": url,
        "has_original_link": _has_http_url(url),
        "final_score": row.get("final_score"),
        "score_band": score_band(row.get("final_score")),
        "alert_grade": row.get("alert_grade"),
        "action_label": ACTION_LABEL_BY_GRADE.get(row.get("alert_grade"), "모니터링"),
        "category": category_key,
        "category_label": insight.CATEGORY_PHRASE.get(category_key, "건설산업 일반"),
        "why_it_matters": implication,
        "exposure_cluster_key": _exposure_cluster_key(row),
    }
    entry.update(_top_exposure_profile(row, decision))
    return entry


def _build_category_sections(scored_rows: list[dict], categories: dict[str, str],
                             reasons: dict[str, str],
                             decisions: dict[str, dict] | None = None,
                             surface_state: _ExposureSurfaceState | None = None
                             ) -> list[dict]:
    """카테고리별 근거 기사 섹션을 만든다 — 집계/정렬만 (점수·등급 재계산 없음, P0-C1.7).

    설계 원칙:
    - total_count는 채점된 전 기사 기준이라 카테고리 요약 카운트와 정확히 일치한다
      (수집 총량을 카테고리별로 감사 가능하게 한다).
    - top_articles는 블로그/카페/커뮤니티성(excluded) 출처를 제외한 '뉴스 근거'만 노출한다
      — 임원용 근거 목록에 비-뉴스 결과가 섞이지 않게 하는 가드레일(P0-C1.6과 동일 정책).
    - 표시 한도(TOP_CATEGORY_ARTICLES)를 넘는 분량과 비-뉴스 제외분은 '외 n건'으로 정직히 표기한다.
    """
    grouped: dict[str, list[dict]] = {}
    for row in scored_rows:
        grouped.setdefault(categories.get(row["id"], "general"), []).append(row)

    sections = []
    surface_state = surface_state or _ExposureSurfaceState()
    for cat_key, rows in sorted(
            grouped.items(), key=lambda item: (-len(item[1]), item[0])):
        total = len(rows)
        instant = sum(1 for r in rows if r.get("alert_grade") == scoring.GRADE_INSTANT)
        daily = sum(1 for r in rows if r.get("alert_grade") == scoring.GRADE_DAILY)
        weekly = sum(1 for r in rows if r.get("alert_grade") == scoring.GRADE_WEEKLY)
        excluded = sum(1 for r in rows if r.get("alert_grade") == scoring.GRADE_EXCLUDED)

        # 근거 목록 풀: excluded 품질(블로그/카페/커뮤니티)은 빼고 상단 노출 품질순으로 정렬한다.
        news_rows = [r for r in rows if not _is_excluded_quality(r)]
        nonnews = total - len(news_rows)        # 블로그/카페 등 비-뉴스 출처 수
        # P0-D3S Goal E/issue#4: 'AI 데이터센터·전력 인프라'(dc_power) 근거 목록만 강화한다 —
        # stock-hype 시세성 노이즈(인프라 단어가 섞여도)를 제외하고 DC/전력 인프라 + 건설/실행
        # 앵커가 함께 있는 기사만 근거로 둔다. broad 키워드로 잘못 묶인 안전교육·화학규제·종목성
        # 항목을 근거에서 강등한다(total_count 불변 — 감사 가능). 다른 카테고리 근거는 건드리지
        # 않는다 — 증권성 기사도 자기 카테고리/참고·제외에 자본시장 사유로 남아 감사 가능하다.
        def _category_evidence_ok(r: dict) -> bool:
            if cat_key != "dc_power":
                return True
            if article_quality.assess(
                    r.get("source"), r.get("title")).get("stock_hype"):
                return False
            return _dc_power_evidence_ok(r)
        eligible_rows = [r for r in news_rows if _category_evidence_ok(r)]
        offtopic = len(news_rows) - len(eligible_rows)   # dc_power 비직접/시세성 제외 수
        evidence = sorted(
            eligible_rows,
            key=lambda r: _top_exposure_sort_key(
                r, (decisions or {}).get(r["id"])))
        # P0-D3F: 같은 cluster(예: 스마트건설 챌린지·AI 데이터센터)가 여러 카테고리 근거 상단을
        # 도배하지 않게, 카테고리 간 cluster당 최대 2건으로 제한한다(global_cluster_keys=None=전체 대상).
        # 카테고리당 cluster 최대 2건은 그대로. total_count(전 기사)는 불변이라 카운트 감사 가능.
        evidence_top = _filter_surface_exposures(
            evidence, TOP_CATEGORY_ARTICLES, decisions=decisions,
            surface=f"category:{cat_key}", state=surface_state,
            max_per_cluster=2, count_global=True, count_category=True)
        top = [_category_article_entry(
                    r, cat_key, reasons.get(r["id"], ""),
                    (decisions or {}).get(r["id"]))
               for r in evidence_top]
        sources = {(r.get("source") or "출처 미상") for r in evidence}

        shown = len(top)
        remaining = total - shown          # '외 n건' (표시 한도 초과분 + 비-뉴스/비직접 제외분)
        note_parts = []
        if remaining > 0:
            note_parts.append(f"외 {remaining}건")
        if nonnews > 0:
            note_parts.append(f"블로그·카페 등 비-뉴스 출처 {nonnews}건은 근거 목록에서 제외")
        if offtopic > 0:
            note_parts.append(f"직접 관련성 낮은 {offtopic}건은 근거 목록에서 제외")
        sections.append({
            "category_key": cat_key,
            "category_label": insight.CATEGORY_PHRASE.get(cat_key, "건설산업 일반"),
            "total_count": total,
            "all_articles_count": total,
            "evidence_count": len(evidence),
            "shown_count": shown,
            "remaining_count": remaining,
            "weak_count": nonnews,
            "instant_count": instant,
            "daily_count": daily,
            "weekly_count": weekly,
            "excluded_count": excluded,
            "source_count": len(sources),
            "top_articles": top,
            "note": " · ".join(note_parts),
        })
    sections.sort(key=lambda s: (-s["total_count"], s["category_key"]))
    return sections


def _build_review_excluded(scored_rows: list[dict], categories: dict[str, str],
                           reasons: dict[str, str]) -> dict:
    """참고/제외 등급(제외) 중 '정상 뉴스 출처'만 모은다 — 낮은 관련성/우선순위 뉴스 (P0-C1.8).

    출처 품질 제외(블로그/카페 등)는 여기서 빼고 별도 audit 섹션으로 보낸다.
    낮은 관련성 ≠ 출처 품질 문제 — 두 기준을 한 묶음으로 섞지 않는다.
    저장된 값만 옮긴다(점수·등급 재계산 없음). 본문 전문은 싣지 않는다.
    """
    rows = sorted(
        (r for r in scored_rows
         if r.get("alert_grade") == scoring.GRADE_EXCLUDED and not _is_excluded_quality(r)),
        key=lambda r: (-(r.get("final_score") or 0), r["id"]))
    items = [_category_article_entry(r, categories.get(r["id"], "general"),
                                     reasons.get(r["id"], ""))
             for r in rows[:TOP_REVIEW_EXCLUDED]]
    return {
        "items": items,
        "total_count": len(rows),
        "shown_count": len(items),
        "remaining_count": max(0, len(rows) - len(items)),
        "note": REVIEW_EXCLUDED_NOTE,
    }


def _source_filtered_entry_from_provenance(item: dict) -> dict:
    """collector가 수집 단계에서 버린 비뉴스성 출처 항목(title/source/url)을 audit 항목으로.

    이 항목은 채점·저장되지 않았으므로 중요도/카테고리가 없다(점수 None). 본문 전문 없음.
    """
    source = item.get("source") or "출처 미상"
    quality = source_quality.classify(source, item.get("title"))
    url = item.get("url")
    return {
        "article_id": None,
        "title": item.get("title") or "",
        "source": source,
        "display_source": source_quality.normalize_display_source(source) or "출처 미상",
        "source_quality": quality["source_quality"],
        "source_quality_label": SOURCE_FILTERED_LABEL,
        "source_quality_reason": quality["source_quality_reason"],
        "published_at": item.get("published_at"),
        "url": url,
        "has_original_link": _has_http_url(url),
        "final_score": None,
        "score_band": None,
        "alert_grade": None,
        "action_label": "출처 품질 제외",
        "category": None,
        "category_label": None,
        "why_it_matters": "",
        "audit_label": SOURCE_FILTERED_LABEL,
        "origin": "filtered_at_collection",
    }


def _build_source_filtered(scored_rows: list[dict], categories: dict[str, str],
                           reasons: dict[str, str],
                           provenance: dict | None) -> dict:
    """출처 품질 제외(블로그/카페/커뮤니티) 감사 목록 (P0-C1.8).

    live 수집에서 비뉴스성 출처는 수집 단계(live_collector)에서 버려져 DB에 없으므로,
    collector provenance(source_filtered: title/source/url)로 surface한다. 드물게 DB에
    excluded 품질 행이 있으면(mock 등) 함께 합친다. 본문 전문은 싣지 않는다.
    """
    stored = sorted((r for r in scored_rows if _is_excluded_quality(r)),
                    key=lambda r: (-(r.get("final_score") or 0), r["id"]))
    items = []
    seen = set()
    for r in stored:
        entry = _category_article_entry(r, categories.get(r["id"], "general"),
                                        reasons.get(r["id"], ""))
        entry["source_quality_label"] = SOURCE_FILTERED_LABEL
        entry["source_quality_reason"] = source_quality.classify(
            r.get("source"), r.get("title"))["source_quality_reason"]
        entry["audit_label"] = SOURCE_FILTERED_LABEL
        entry["origin"] = "stored"
        items.append(entry)
        seen.add((entry["source"], entry["title"]))

    for it in ((provenance or {}).get("source_filtered") or []):
        key = (it.get("source") or "출처 미상", it.get("title") or "")
        if not key[1] or key in seen:
            continue
        seen.add(key)
        items.append(_source_filtered_entry_from_provenance(it))

    total = len(items)
    shown = items[:TOP_SOURCE_FILTERED]
    return {
        "items": shown,
        "total_count": total,
        "shown_count": len(shown),
        "remaining_count": max(0, total - len(shown)),
        "audit_label": SOURCE_FILTERED_LABEL,
        "note": SOURCE_FILTERED_NOTE,
    }


def _risk_query_coverage(query_audit: list[dict]) -> dict:
    """Google RSS risk/regulation query attempt summary for operator audit."""
    risk_rows = [
        q for q in (query_audit or [])
        if q.get("group") == "risk_regulation"
    ]
    return {
        "group": "risk_regulation",
        "attempted": len(risk_rows),
        "ok": sum(1 for q in risk_rows if q.get("status") == "ok"),
        "empty": sum(1 for q in risk_rows if q.get("status") == "empty"),
        "error": sum(1 for q in risk_rows if q.get("status") == "error"),
        "fetched_count": sum(int(q.get("fetched_count") or 0) for q in risk_rows),
        "added_count": sum(int(q.get("added_count") or 0) for q in risk_rows),
        "queries": [q.get("query") for q in risk_rows if q.get("query")],
    }


def _diverse_top(rows: list[dict], categories: dict[str, str], limit: int,
                 sort_key=None) -> list[dict]:
    """점수순 후보에서 카테고리가 몰리지 않게 limit개를 고른다.

    1차: 점수순으로 카테고리당 1건씩. 2차: 남은 슬롯을 점수순으로 채움.
    최종 표시는 점수순으로 재정렬한다 (rank 숫자가 점수 역전되지 않게).
    후보가 limit 이하면 기존 선택과 완전히 동일하다.
    """
    picked, seen_cats = [], set()
    for row in rows:
        cat = categories.get(row["id"], "general")
        if cat in seen_cats:
            continue
        seen_cats.add(cat)
        picked.append(row)
        if len(picked) == limit:
            break
    if len(picked) < limit:
        picked_ids = {r["id"] for r in picked}
        for row in rows:
            if row["id"] in picked_ids:
                continue
            picked.append(row)
            if len(picked) == limit:
                break
    picked.sort(key=sort_key or (lambda r: (-(r["final_score"]), r["id"])))
    return picked


def _clean_summary_topic(topic: str) -> str:
    text = unicodedata.normalize("NFKC", str(topic or ""))
    return re.sub(r"\s+", " ", text).strip(" \t\r\n.,;:!?")


def _summary_topic_key(topic: str) -> str:
    text = unicodedata.normalize("NFKC", str(topic or ""))
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"[.,;:!?()\\[\\]{}'\"“”‘’·ㆍ\\-_/]", "", text)
    for suffix in ("동시부각", "중심", "부각"):
        if text.endswith(suffix):
            text = text[:-len(suffix)]
    return text


def _unique_summary_topics(topics: list[str]) -> list[str]:
    unique = []
    seen = set()
    for topic in topics:
        clean = _clean_summary_topic(topic)
        key = _summary_topic_key(clean)
        if not clean or not key or key in seen:
            continue
        seen.add(key)
        unique.append(clean)
    return unique


def _join_summary_topics(topics: list[str]) -> str:
    if not topics:
        return ""
    if len(topics) == 1:
        return topics[0]
    prefix = "현대건설 연관 "
    display = [topics[0]]
    prefix_seen = topics[0].startswith(prefix)
    for topic in topics[1:]:
        if prefix_seen and topic.startswith(prefix):
            display.append(topic[len(prefix):])
        else:
            display.append(topic)
            prefix_seen = prefix_seen or topic.startswith(prefix)
    a, b = display[0], display[1]
    return f"{a}{_josa(a, '과', '와')} {b}"


def _compose_executive_signal_summary(topics: list[str], opportunity: str,
                                      risk: str) -> str:
    unique_topics = _unique_summary_topics(topics)
    if len(unique_topics) >= 2:
        return (
            f"{_join_summary_topics(unique_topics[:2])} 동시 부각 — "
            f"{opportunity} 기회·{risk} 리스크 병존"
        )
    topic = unique_topics[0] if unique_topics else SUBJECT_BY_CATEGORY["general"]
    return f"{topic} 중심 — {opportunity} 기회와 {risk} 리스크 요인 점검 필요"


def _compose_one_liner(signal_rows: list[dict], categories: dict[str, str],
                       immediate_count: int, top_theme: str | None) -> str:
    """저장된 opportunity_or_risk 분류를 종합해 1~2문장 한 줄 시그널을 조립한다.

    제목을 이어붙이지 않는다 — 카테고리별 표현 사전으로만 문장을 만든다.
    """
    if not signal_rows:
        return "오늘 감지된 신호 없음 — Run Sensing으로 mock 신호 수집"

    def first_match(kinds: tuple[str, ...], skip_id: str | None = None,
                    skip_category: str | None = None):
        for row in signal_rows:
            if (row.get("opportunity_or_risk") or "관찰") not in kinds:
                continue
            if skip_id and row["id"] == skip_id:
                continue
            if skip_category and categories.get(row["id"]) == skip_category:
                continue
            return row
        return None

    opp = first_match(("기회", "기회+리스크"))
    opp_cat = categories.get(opp["id"], "general") if opp else None
    # 같은 카테고리끼리 "A와 A가"가 되지 않도록 리스크는 다른 카테고리를 우선 탐색
    risk = first_match(("리스크", "기회+리스크"),
                       skip_id=opp["id"] if opp else None,
                       skip_category=opp_cat)
    if risk is None:
        risk = first_match(("리스크",), skip_id=opp["id"] if opp else None)
    risk_cat = categories.get(risk["id"], "general") if risk else None

    # P0-C1.10: 임원 메모 스타일 — 종결어미 없이 명사형 구절로 조립 (제목 이어붙이기 아님).
    if opp and risk:
        a = SUBJECT_BY_CATEGORY.get(opp_cat, SUBJECT_BY_CATEGORY["general"])
        b = SUBJECT_BY_CATEGORY.get(risk_cat, SUBJECT_BY_CATEGORY["general"])
        return _compose_executive_signal_summary(
            [a, b],
            OPP_ASPECT_BY_CATEGORY.get(opp_cat, "신규 사업"),
            RISK_ASPECT_BY_CATEGORY.get(risk_cat, "운영"),
        )
    if opp:
        a = SUBJECT_BY_CATEGORY.get(opp_cat, SUBJECT_BY_CATEGORY["general"])
        return (
            f"{a} 중심 — {OPP_ASPECT_BY_CATEGORY.get(opp_cat, '신규 사업')} 기회 부각, "
            f"뚜렷한 리스크 신호 제한적"
        )
    if risk:
        b = SUBJECT_BY_CATEGORY.get(risk_cat, SUBJECT_BY_CATEGORY["general"])
        return (
            f"{b} 부각 — {RISK_ASPECT_BY_CATEGORY.get(risk_cat, '운영')} 리스크 점검 우선, "
            f"뚜렷한 기회 신호 제한적"
        )
    theme = top_theme or "건설·에너지"
    return (f"즉시 공유 신호 없음 — {theme} 중심 관찰, "
            f"즉시 알림 후보 {immediate_count}건")


def _fmt_kst(iso) -> str:
    """ISO 타임스탬프(UTC/KST 무관)를 KST 벽시계 'YYYY-MM-DD HH:MM'로 표시한다.

    임원 화면에 +00:00 같은 raw offset(Yahoo는 시장 기준시각을 UTC로 준다)을 그대로
    노출하지 않기 위한 표시 전용 변환이다 — 같은 순간을 KST로 읽어줄 뿐 시각 자체를
    바꾸지 않는다. 파싱 불가하면 원본을 그대로 돌려준다 (가짜 KST로 위장하지 않는다).
    """
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return str(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(KST).strftime("%Y-%m-%d %H:%M")


def _macro_warning(macro: dict | None) -> str:
    """시장지표 한 조각 — live로 실수집됐을 때만 출처·참고시각(KST)을 표기하고 그 외엔 '미연동'.

    P0-C2: 표시 게이팅(_render_macro_section)이 macro_data_mode=="live"일 때만 수치를
    렌더하는데, footer 고지가 항상 '미연동'이면 같은 리포트가 live 수치와 '미연동'을 동시에
    보여주는 모순이 생긴다. footer를 같은 조건으로 묶어 그 회귀를 차단한다.
    P0-D1.5: 기준시각은 KST 벽시계로 표기한다 — Yahoo의 UTC(+00:00)를 그대로 노출하지 않는다.
    """
    macro = macro or {}
    if macro.get("macro_data_mode") == "live" and macro.get("values"):
        line = f"시장지표: {macro.get('source') or 'live'}"
        updated = _fmt_kst(macro.get("updated_at"))
        if updated:
            line += f" · 참고시각 {updated} (KST 기준)"
        if macro.get("is_stale"):
            line += " (지연)"
        return line
    return "시장지표: 미연동"


def _data_warning(news_mode: str, fallback_used: bool, macro: dict | None = None) -> str:
    """뉴스 수집 모드 + 시장지표 출처를 한 줄로 합친 데이터 출처 고지.

    P0-C1.10: 임원 화면에 'LIVE'·'공개 RSS' 같은 기술 용어를 노출하지 않는다 —
    live는 중립적 '자동 수집'으로 표기한다 (내부 news_data_mode/JSON은 그대로 유지).
    P0-C2: 시장지표 조각은 macro_data_mode가 live일 때만 출처·기준을 표기한다.
    """
    if news_mode == "live":
        news = "뉴스: 자동 수집"
    elif fallback_used:
        news = "뉴스: live 수집 실패로 데모(mock) 데이터 대체"
    else:
        news = "뉴스: 데모(mock) 데이터"
    return f"{news} · {_macro_warning(macro)}"


def build_brief(pipeline_counts: dict | None = None,
                news_provenance: dict | None = None) -> dict:
    """현재 DB 상태로부터 executive brief 구조체를 만든다 (DB 쓰기 없음).

    news_provenance(선택)는 collector.run()이 돌려준 출처 정보 — fallback 여부 등
    DB만으로는 알 수 없는 런타임 상태를 정직하게 담기 위해 쓴다. news_data_mode 자체는
    저장된 기사 signal_origin에서 파생하므로 provenance 없이도 정확하다.
    """
    rows = db.fetch_articles_with_scores()
    scored = [r for r in rows if r.get("final_score") is not None]
    scored.sort(key=lambda r: (-(r["final_score"]), r["id"]))

    grade_counts = {}
    for row in scored:
        grade = row.get("alert_grade") or "미채점"
        grade_counts[grade] = grade_counts.get(grade, 0) + 1
    immediate_count = grade_counts.get(scoring.GRADE_INSTANT, 0)
    daily_count = grade_counts.get(scoring.GRADE_DAILY, 0)
    weekly_count = grade_counts.get(scoring.GRADE_WEEKLY, 0)
    excluded_count = grade_counts.get(scoring.GRADE_EXCLUDED, 0)

    # 신호 = 제외 제외 (즉시/일간/주간)
    signal_rows = [r for r in scored if r.get("alert_grade") != scoring.GRADE_EXCLUDED]

    # 카테고리: 저장된 implication 텍스트를 역매핑 (재탐지 없음)
    details = {r["id"]: db.fetch_article_detail(r["id"]) for r in scored}
    categories = {rid: _category_key(d) for rid, d in details.items()}
    # 점수 구성요소(9항목)는 detail의 score row에만 있다 — 표시용으로 묶어둔다.
    scores_by_id = {rid: ((d or {}).get("score") or {}) for rid, d in details.items()}

    spreads = _build_spreads(scored)

    # 레이더 섹션 분류 (P0-C1.9, 표시 전용 파생값) — 채점된 전 기사를
    # ai/risk_regulation/business_overseas/macro_economy/other 중 하나로 나눈다.
    row_by_id = {r["id"]: r for r in scored}
    radar_sections = {rid: radar.classify_section(row, categories[rid])
                      for rid, row in row_by_id.items()}
    # 임원 의사결정 관련성 (P0-C1.12, 표시 전용 파생값) — primary/secondary 임원 섹션 +
    # 티어를 파생한다. radar 위에 얹혀 '현대건설 직접 영향'·'수주·해외 발주 환경'·'경쟁사·
    # 공급망'을 추가하고, 같은 기사가 여러 섹션에 멤버로 들어갈 수 있게 한다 (multi-section).
    decisions = {rid: decision_relevance.classify(row, categories[rid])
                 for rid, row in row_by_id.items()}
    # 재무 하드 오버라이드 (P0-C1.14) — raw 제목+스니펫이 재무·자금조달 신호인데 전략 맥락이
    # 없으면, collector의 생성 topic_candidates가 AI로 끌어올린 radar_section을 거시로 되돌린다.
    # 이렇게 해야 전환사채 등 재무 기사가 ai_radar_signals/AI 섹션/AI Top에서 빠지고 거시·현대건설
    # 직접으로 라우팅된다 (decision_relevance가 단일 소유, is_finance는 raw만 본다, radar는 불변).
    for rid in row_by_id:
        radar_sections[rid] = decision_relevance.override_radar_section(
            radar_sections[rid], decisions[rid])

    # 표시용 임원 사유 (P0-D3B) — 저장된 카테고리 implication 대신 기사 유형별 명사형 사유를
    # 파생한다. raw 제목+스니펫과 decision 플래그(stock_hype/is_finance/hdec_direct)만 입력으로
    # 보고(생성 라벨 입력 금지), 분류/등급은 재계산하지 않는다. 도시정비 수주·원전/SMR·데이터센터·
    # 분양 PR·전환사채를 한 문장("수주 경쟁력·시장 포지션 영향권")으로 뭉뚱그리던 것을 유형화한다.
    # 리포트/대시보드 카드의 '왜 중요한가'·카테고리 드릴다운 사유·Telegram 사유가 이 값에서 나온다.
    display_reasons = {
        rid: insight.executive_reason(
            row.get("title") or "", row.get("snippet") or "",
            is_stock_hype=bool(decisions[rid].get("stock_hype")),
            is_finance=bool(decisions[rid].get("is_finance")),
            hdec_direct=bool(decisions[rid].get("hdec_direct")))
        for rid, row in row_by_id.items()
    }

    # Top 3/Top 5 노출 대상에서 excluded 출처(블로그/카페/커뮤니티성)는 배제한다
    # (P0-C1.6). excluded는 scoring 캡으로 이미 제외 등급이라 signal_rows에 거의 없지만,
    # 표시 직전 한 번 더 거른다 — "Top 3에 비-뉴스 금지"를 구조적으로 보장한다.
    # mock 데모는 excluded 출처가 신호에 없어 display_rows == signal_rows로 동일하다.
    display_rows = [
        r for r in signal_rows
        if (not _is_excluded_quality(r)
            and not _is_top_exposure_excluded(r, decisions.get(r["id"])))
    ]

    def _entry(rank, row):
        return _signal_entry(rank, row, categories[row["id"]],
                             display_reasons[row["id"]], spreads[row["id"]],
                             scores_by_id.get(row["id"]),
                             section=radar_sections[row["id"]],
                             decision=decisions[row["id"]])

    # ---- 섹션별 레이더 신호 (P0-C1.9 + P0-C1.12 임원 의사결정 IA + P0-D3F dedup) ----
    # AI/거시는 radar 분류(primary)로 뽑는다 — AI는 radar_section==ai 불변(IA 회귀 보호).
    # 현대건설 직접/수주·해외/경쟁사·공급망은 decision_relevance 멤버십(primary or
    # secondary)으로 뽑는다 — 같은 기사가 여러 섹션에 들어갈 수 있다(multi-section).
    # P0-D3F: surface_state를 우선순위대로 통과시켜 같은 기사·cluster가 여러 상단 카드를
    # 도배하지 않게 한다. 의도된 multi-section(현대건설 벌점=현대건설 연관+리스크)은 기사당
    # 최대 2 surface로 보존한다. 거시·즉시 알림은 독립(고-노출 surface 아님).
    surface_state = _ExposureSurfaceState()

    def _radar_group(section_key, limit=TOP_RADAR, sort_key=None, *, surface=None,
                     eligible=None):
        rows = [r for r in display_rows if radar_sections[r["id"]] == section_key]
        # P0-D3S Goal B: AI 상단 적격성 필터 — 직접 건설/인프라 관련성 없는 generic AI
        # (순수 인공지능/반도체/우주/소비자 AI)는 AI 상단에서 제외한다. 필터가 후보를 전부
        # 비우면(엣지) 원래 후보로 폴백해 빈 탭을 피한다.
        if eligible is not None:
            kept = [r for r in rows if eligible(r)]
            rows = kept if kept else rows
        rows.sort(key=sort_key or (
            lambda r: _top_exposure_sort_key(r, decisions[r["id"]])))
        if surface:
            rows = _filter_surface_exposures(
                rows, limit, decisions=decisions, surface=surface,
                state=surface_state)
        else:
            rows = _cap_exposure_clusters(rows, limit, decisions=decisions)
        return [_entry(i, r) for i, r in enumerate(rows, start=1)]

    def _decision_group(section_key, limit=TOP_RADAR, sort_key=None,
                        company_cap=None, min_score=None, *, surface=None):
        rows = [r for r in display_rows
                if decision_relevance.in_section(decisions[r["id"]], section_key)]
        if min_score is not None:
            rows = [r for r in rows if (r.get("final_score") or 0) >= min_score]
        rows.sort(key=sort_key or (
            lambda r: _top_exposure_sort_key(r, decisions[r["id"]])))
        # 같은 회사(예: 가온전선 3건)가 한 섹션을 점유하지 않게 회사당 상한을 둔다 (P0-C1.12).
        # 회사명이 없는 기사는 캡하지 않는다. 정렬 후 적용하므로 상위 신호가 먼저 살아남는다.
        if company_cap:
            capped, counts = [], {}
            for r in rows:
                ck = decision_relevance.company_key(r.get("title") or "")
                if ck is not None and counts.get(ck, 0) >= company_cap:
                    continue
                if ck is not None:
                    counts[ck] = counts.get(ck, 0) + 1
                capped.append(r)
            rows = capped
        pair_cap = (section_key == decision_relevance.HDEC_DIRECT)
        if surface:
            rows = _filter_surface_exposures(
                rows, limit, decisions=decisions, surface=surface,
                state=surface_state, hdec_direct_pair_cap=pair_cap)
        else:
            rows = _cap_exposure_clusters(
                rows, limit, decisions=decisions, hdec_direct_pair_cap=pair_cap)
        return [_entry(i, r) for i, r in enumerate(rows, start=1)]

    # --- 상단 노출 surface를 우선순위대로 빌드한다 (높은 우선순위가 기사를 먼저 점유) ---
    # 0) 즉시 알림 후보 — 임원 헤드라인 다이제스트이므로 가장 먼저 점유한다. 이후 레이더는
    #    같은 article/title/url을 반복하지 않고 다른 근거로 backfill한다(D3L visible single-use).
    instant_pool = [r for r in display_rows
                    if r.get("alert_grade") == scoring.GRADE_INSTANT]
    instant_pool.sort(key=lambda r: _top_exposure_sort_key(r, decisions[r["id"]]))
    instant_rows = _diverse_top(
        instant_pool, categories, TOP_IMMEDIATE,
        sort_key=lambda r: _top_exposure_sort_key(r, decisions[r["id"]]))
    instant_rows = _filter_surface_exposures(
        instant_rows, TOP_IMMEDIATE, decisions=decisions,
        surface="top_immediate_signals", state=surface_state)
    if not instant_rows:
        fallback_pool = [r for r in display_rows
                         if (r.get("final_score") or 0) >= TOP_DISPLAY_SCORE_FLOOR]
        fallback_pool.sort(
            key=lambda r: _top_exposure_sort_key(r, decisions[r["id"]]))
        instant_rows = _diverse_top(
            fallback_pool, categories, TOP_IMMEDIATE,
            sort_key=lambda r: _top_exposure_sort_key(r, decisions[r["id"]]))
        instant_rows = _filter_surface_exposures(
            instant_rows, TOP_IMMEDIATE, decisions=decisions,
            surface="top_immediate_signals", state=surface_state)
    top_immediate = [_entry(i, r) for i, r in enumerate(instant_rows, start=1)]

    # 1) 현대건설 직접 영향 — Phase 3 순서(리스크/제재 → 수주·DC·SMR·뉴에너지 전략 →
    #    AI·계약 → R&D·조직 → 그 외 직접). 이미 헤드라인에 잡힌 exact article/title은
    #    반복하지 않고 다른 현대건설 근거로 backfill한다.
    def _hdec_sort(r):
        d = decisions[r["id"]]
        return (_top_exposure_profile(r, d)["top_exposure_penalty"],
                d["hdec_bucket"], _freshness_rank(r),
                -(d["decision_relevance_score"] or 0),
                -(r.get("final_score") or 0), r["id"])
    hdec_direct_signals = _decision_group(
        decision_relevance.HDEC_DIRECT, limit=TOP_RADAR, sort_key=_hdec_sort,
        surface="hdec_direct_signals")

    # 2) AI 관련 — 정렬 우선순위 (P0-D3S, 임원 의도): (1) 현대건설/건설/EPC/데이터센터/전력
    # 인프라 직접 관련성 → (2) 임원 중요도(종합 점수) → (3) 신뢰 출처 → (4) 최신성.
    # penalty를 맨 앞에 둬 증권·공급사·stale·roundup 노이즈를 먼저 억제한다(품질 게이트 계약
    # 유지). 공급사 단독(부품·전선·설비)은 비공급사 AI/EPC 신호가 top-N(cap) 안에 들도록
    # 뒤로 둔다(P0-C1.14). 직접 관련성을 최신성보다 앞세워 '실뉴스성 종목·시세 기사가 실제
    # AI 인프라/데이터센터/EPC 기사를 누르지 않게' 한다.
    def _ai_sort(r):
        d = decisions[r["id"]]
        prof = _top_exposure_profile(r, d)
        flags = prof["top_exposure_flags"]
        return (
            prof["top_exposure_penalty"],
            1 if d.get("supplier_only") else 0,
            0 if (d.get("hdec_direct") or "direct_project_signal" in flags) else 1,
            -(r.get("final_score") or 0),
            -(d.get("decision_relevance_score") or 0),
            0 if "trusted_source" in flags else 1,
            _freshness_rank(r),
            r["id"])
    ai_radar_signals = _radar_group(
        radar.AI, sort_key=_ai_sort, surface="ai_radar_signals",
        eligible=_ai_top_eligible)
    # 3) 수주·해외 — 확정 계약뿐 아니라 발주 환경(중동·재건·EPC·DC·SMR·플랜트)과 경쟁사
    # 수주 전략까지 넓혀 'business 0건' 회귀를 방지한다 (decision 멤버십 기반).
    # 현대건설 직접(primary)은 위 현대건설 섹션에 이미 노출되므로, 여기선 '순수 수주·해외/
    # 경쟁사 발주' 신호(중동·재건 등)를 먼저 보여 같은 기사로 도배되지 않게 한다.
    def _order_sort(r):
        d = decisions[r["id"]]
        is_hdec_primary = d["primary_executive_section"] == decision_relevance.HDEC_DIRECT
        return (_top_exposure_profile(r, d)["top_exposure_penalty"],
                1 if is_hdec_primary else 0,
                (d.get("order_class") if d.get("order_class") is not None else 9),
                _freshness_rank(r),
                -(d["decision_relevance_score"] or 0),
                -(r.get("final_score") or 0), r["id"])
    business_signals = _decision_group(
        decision_relevance.ORDER_OVERSEAS, sort_key=_order_sort, company_cap=2,
        min_score=1.2, surface="business_signals")

    # 4) 리스크·규제 — broader pool(scored 전체, 비뉴스만 제외)에서 risk_priority순으로 뽑아
    # 중요도(final_score)가 낮아도 중대재해·규제가 레이더에 드러나게 한다. 경쟁사·공급망보다
    # 우선순위가 높아 먼저 기사를 점유한다(현대건설 벌점 등 현대건설+리스크 multi-section 보존).
    risk_pool = [r for r in scored
                 if radar_sections[r["id"]] == radar.RISK
                 and not _is_excluded_quality(r)
                 and not _is_top_exposure_excluded(r, decisions.get(r["id"]))]
    risk_pool.sort(key=lambda r: (
        _top_exposure_profile(r, decisions[r["id"]])["top_exposure_penalty"],
        -radar.risk_fields(r, scores_by_id.get(r["id"]))["risk_priority_score"],
        -(r.get("final_score") or 0), r["id"]))
    risk_rows = _filter_surface_exposures(
        risk_pool, TOP_RADAR, decisions=decisions,
        surface="risk_regulation_signals", state=surface_state)
    risk_regulation_signals = [
        _entry(i, r) for i, r in enumerate(risk_rows, start=1)]

    # 5) 경쟁사·공급망
    competitor_supply_signals = _decision_group(
        decision_relevance.COMPETITOR, company_cap=2, min_score=1.5,
        surface="competitor_supply_signals")

    # 거시경제 — executive visible surface의 exact 중복만 피한다.
    macro_economy_signals = _radar_group(
        radar.MACRO, surface="macro_economy_signals")

    # 신규 이슈 — 가장 낮은 우선순위. 위 모든 상단 카드에 양보해 '다른 카드에 없는' 신규
    # 신호만 남긴다(max_article_surfaces=1). 전부 이미 노출됐으면 빈 카드보다 상위 신호
    # 재노출이 낫다 — 기존 로직으로 폴백한다(top_new 1~5건 계약 보존).
    top_issue_pool = [r for r in display_rows
                      if (r.get("final_score") or 0) >= TOP_NEW_SCORE_FLOOR]
    if not top_issue_pool:
        top_issue_pool = [r for r in display_rows
                          if (r.get("final_score") or 0) >= TOP_DISPLAY_SCORE_FLOOR]
    top_issue_pool.sort(
        key=lambda r: _top_exposure_sort_key(r, decisions[r["id"]]))
    issue_candidates = _diverse_top(
        top_issue_pool, categories, min(len(top_issue_pool), TOP_ISSUES * 3),
        sort_key=lambda r: _top_exposure_sort_key(r, decisions[r["id"]]))
    issue_rows = _filter_surface_exposures(
        issue_candidates, TOP_ISSUES, decisions=decisions,
        surface="top_new_issues", state=surface_state, max_article_surfaces=1)
    if not issue_rows:
        issue_rows = _diverse_top(
            top_issue_pool, categories, TOP_ISSUES,
            sort_key=lambda r: _top_exposure_sort_key(r, decisions[r["id"]]))
        issue_rows = _filter_surface_exposures(
            issue_rows, TOP_ISSUES, decisions=decisions,
            surface="top_new_issues", state=surface_state,
            max_article_surfaces=1)
    top_issues = [
        _entry(i, r)
        for i, r in enumerate(issue_rows, start=1)
    ]

    # 테마 랭킹: 신호(제외 등급 제외)의 topic_candidates를 점수 가중으로 집계
    theme_stats = {}
    for row in signal_rows:
        for topic in _parse_topics(row):
            stat = theme_stats.setdefault(topic, {"count": 0, "weight": 0.0, "top": 0.0})
            stat["count"] += 1
            stat["weight"] += row["final_score"]
            stat["top"] = max(stat["top"], row["final_score"])
    theme_rankings = [
        {"rank": i, "theme": theme, "count": stat["count"],
         "weighted_strength": round(stat["weight"], 1),
         "top_score": round(stat["top"], 2)}
        for i, (theme, stat) in enumerate(
            sorted(theme_stats.items(),
                   key=lambda kv: (-kv[1]["weight"], -kv[1]["count"], kv[0]))[:TOP_THEMES],
            start=1)
    ]
    # 상대 강도(0~100) — "강도 30.7" 같은 단위 불명 표현 대신 가장 강한 테마=100 기준.
    if theme_rankings:
        max_weight = max(t["weighted_strength"] for t in theme_rankings) or 1
        for t in theme_rankings:
            t["relative_strength"] = max(1, round(t["weighted_strength"] / max_weight * 100))

    # 카테고리 요약: 전 채점 기사 기준 (제외 포함 — 분포 전체를 보여준다)
    category_stats = {}
    for row in scored:
        key = categories[row["id"]]
        stat = category_stats.setdefault(key, {"count": 0, "immediate": 0})
        stat["count"] += 1
        if row.get("alert_grade") == scoring.GRADE_INSTANT:
            stat["immediate"] += 1
    category_counts = [
        {"key": key, "label": insight.CATEGORY_PHRASE.get(key, "건설산업 일반"),
         "count": stat["count"], "immediate": stat["immediate"]}
        for key, stat in sorted(category_stats.items(),
                                key=lambda kv: (-kv[1]["count"], kv[0]))
    ]

    # 카테고리별 근거 기사 드릴다운 (P0-C1.7) — 채점된 전 기사를 카테고리로 묶어
    # 수집 총량을 카테고리별로 감사 가능하게 한다 (점수·등급 재계산 없음, DB 쓰기 없음).
    category_sections = _build_category_sections(
        scored, categories, display_reasons, decisions, surface_state)

    # 노출 품질·중복 감사 (P0-D3F, 운영자 전용) — 어떤 기사가 어느 surface에 노출/억제됐는지
    # 투명화한다. 임원 카드에는 raw 키를 노출하지 않고, 이 감사 구조에서만 사유를 보여준다.
    _top_surface_entries = (
        top_immediate + hdec_direct_signals + ai_radar_signals + business_signals
        + risk_regulation_signals + competitor_supply_signals
        + macro_economy_signals + top_issues)
    shown_top_ids = {e.get("article_id") for e in _top_surface_entries}
    shown_cat_ids = {a.get("article_id")
                     for sec in category_sections
                     for a in (sec.get("top_articles") or [])}
    exposure_quality_audit = _build_exposure_audit(
        scored, decisions, shown_top_ids, shown_cat_ids)

    top_theme = theme_rankings[0]["theme"] if theme_rankings else None
    one_liner = _compose_one_liner(signal_rows, categories, immediate_count, top_theme)

    # macro snapshot + 데이터 출처 provenance (P0-B6 → P0-C2 — mock을 live로 오인하지 않게).
    # MACRO_MODE는 NEWS_MODE와 독립적이다: 기본 mock = 네트워크 0건, live일 때만
    # app/live_macro.py가 공개 시세 API를 시도하고 실패 시 unavailable로 강등한다.
    macro = macro_snapshot.get_macro_snapshot(config.MACRO_MODE)

    # 뉴스 출처 모드는 저장된 기사 signal_origin에서 파생한다 (DB가 단일 진실).
    # provenance가 주어지면 fallback 여부 등 런타임 상태를 추가로 반영한다.
    news_mode = _derive_news_mode(rows)
    prov = news_provenance or {}
    news_fallback_used = bool(prov.get("fallback_used"))
    news_source = prov.get("news_source") or (
        "live_rss" if news_mode == "live" else "mock")
    google_query_audit = prov.get("google_query_audit") or []

    # 참고/제외 · 출처 품질 감사 (P0-C1.8) — 카운트만 보이던 버킷을 감사 가능하게 한다.
    # 두 기준을 분리한다: 참고/제외=낮은 관련성 뉴스, 출처 품질 제외=비뉴스성 출처.
    review_excluded = _build_review_excluded(scored, categories, display_reasons)
    source_filtered = _build_source_filtered(scored, categories, display_reasons, prov)
    # D3O: 리스크 사건 클러스터 — 기사 단위 카드를 대체하지 않고, visible risk/HDEC-risk와
    # 강한 직접 리스크 제외 후보를 같은 사건 렌즈로 묶어 운영자 검토를 돕는다.
    risk_event_clusters = risk_events.build_risk_event_clusters(
        scored, categories=categories, decisions=decisions,
        radar_sections=radar_sections, scores_by_id=scores_by_id)

    now = datetime.now(KST)
    return {
        "header": HEADER,
        "mode": config.APP_MODE,
        "news_data_mode": news_mode,
        "news_source": news_source,
        "news_fallback_used": news_fallback_used,
        "news_query_audit": google_query_audit,
        "risk_query_coverage": _risk_query_coverage(google_query_audit),
        "macro_data_mode": macro["macro_data_mode"],
        "macro_source": macro.get("source"),
        "macro_updated_at": macro.get("updated_at"),
        "macro_is_stale": macro.get("is_stale", True),
        "data_warning": _data_warning(news_mode, news_fallback_used, macro),
        "date_kst": now.strftime("%Y-%m-%d"),
        "generated_at": now.isoformat(timespec="seconds"),
        "total_articles": len(rows),
        "total_signals": len(signal_rows),
        "immediate_count": immediate_count,
        "daily_count": daily_count,
        "weekly_count": weekly_count,
        "excluded_count": excluded_count,
        "status_board": [
            {"key": "detected", "label": "수집·분석 기사", "value": len(rows)},
            {"key": "immediate", "label": scoring.GRADE_INSTANT, "value": immediate_count},
            {"key": "daily", "label": "중요 신호", "value": daily_count},
            {"key": "weekly", "label": "관찰 신호", "value": weekly_count},
            {"key": "excluded", "label": "참고/제외", "value": excluded_count},
        ],
        "executive_one_liner": one_liner,
        "top_immediate_signals": top_immediate,
        "top_new_issues": top_issues,
        # P0-C1.9 섹션별 레이더 + P0-C1.12 임원 의사결정 IA — 리포트/대시보드/Telegram이
        # '현대건설 직접 → AI → 수주·해외 → 리스크·규제 → 경쟁사·공급망 → 거시'로 소비한다.
        "hdec_direct_signals": hdec_direct_signals,
        "ai_radar_signals": ai_radar_signals,
        "risk_regulation_signals": risk_regulation_signals,
        "business_signals": business_signals,
        "competitor_supply_signals": competitor_supply_signals,
        "macro_economy_signals": macro_economy_signals,
        "radar_labels": radar.RADAR_LABELS,
        "executive_labels": decision_relevance.EXEC_LABELS,
        "executive_short_labels": decision_relevance.EXEC_SHORT,
        "theme_rankings": theme_rankings,
        "category_counts": category_counts,
        "category_sections": category_sections,
        "risk_event_clusters": risk_event_clusters,
        "review_excluded_evidence": review_excluded,
        "source_filtered_evidence": source_filtered,
        "exposure_quality_audit": exposure_quality_audit,
        "macro_snapshot": macro,
        "status_board_legend": STATUS_BOARD_LEGEND,
        "spread_method": SPREAD_METHOD,
        "spread_note": SPREAD_NOTE,
        "theme_strength_note": THEME_STRENGTH_NOTE,
        "source_quality_note": SOURCE_QUALITY_NOTE,
        "category_drilldown_note": CATEGORY_DRILLDOWN_NOTE,
        "operator_note": OPERATOR_NOTE,
        "pipeline_counts": pipeline_counts,
    }
