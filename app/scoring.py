"""Scoring 도메인 — rule-based 9항목 점수, 가중합, 가점/감점, alert_grade.

P0-A는 rule-based only다. LLM/외부 API를 호출하지 않으며,
기사 수집·insight 텍스트 생성·발송은 하지 않는다 (CLAUDE.md §4).
가중치와 가점/감점 값은 rules.md §6 표를 그대로 따른다.
"""

import json
import re
from datetime import datetime, timedelta

from app import db

MODEL_NAME = "rule-based-v1"

WEIGHTS = {
    "hdec_relevance": 0.20,
    "executive_importance": 0.18,
    "business_opportunity": 0.13,
    "risk_potential": 0.13,
    "urgency": 0.12,
    "source_reliability": 0.08,
    "trend_repeat": 0.06,
    "competitor_relevance": 0.05,
    "macro_impact": 0.05,
}

GROUPS = {
    "hdec": ["현대건설"],
    "dc": ["데이터센터", "idc"],
    "power": ["전력", "송배전", "전력망", "송전", "변전", "발전"],
    "grid": ["송배전", "전력망", "송전", "변전"],
    "power_demand": ["전력수요", "전력 확보", "전력 공급"],
    "nuclear": ["원전", "smr", "원자력", "소형모듈원자로"],
    "construction": ["건설", "시공", "epc", "플랜트", "착공", "공사", "인프라", "기반시설"],
    "plant": ["플랜트"],
    "order": ["수주", "발주", "입찰", "우선협상", "계약", "협상"],
    "smart_const": ["스마트건설", "스마트 건설", "건설로봇", "bim", "건설 자동화", "스마트 안전"],
    "competitor": ["삼성물산", "gs건설", "dl이앤씨", "대우건설", "포스코이앤씨"],
    "ai": ["ai", "인공지능", "생성형", "챗봇", "llm"],
    "macro": ["환율", "금리", "유가", "원자재", "철강", "시멘트", "fomc", "달러",
              "인플레이션", "공급망", "파이낸싱"],
    "raw_materials": ["원자재", "철강", "시멘트"],
    "fx": ["환율", "달러"],
    "mideast": ["중동", "사우디", "uae", "네옴", "카타르", "쿠웨이트", "이라크", "산유국"],
    "overseas": ["해외건설", "해외수주", "해외", "유럽", "글로벌", "수출"],
    "gov": ["정부", "정책", "국토교통부", "산업통상자원부", "고용노동부", "고용부",
            "특별법", "시행령", "로드맵", "예산", "의무화"],
    "infra_energy": ["인프라", "에너지", "건설투자", "건설 투자", "전력망", "기반시설"],
    "safety": ["중대재해", "안전관리", "안전", "사망사고", "산재"],
    "severe": ["중대재해", "사망사고"],
    "regulation": ["규제", "처벌", "감독", "의무화", "점검"],
    "cost": ["원가", "공사비", "환헤지"],
    "invest": ["투자", "투입", "협약", "mou", "파트너십"],
    "consumer": ["소비자", "이용자", "가입자", "구독", "앱", "맛집", "숙소", "패션"],
    "lifestyle": ["여행", "쇼핑", "교육", "연예", "영어회화", "데이팅", "패션", "사진 보정"],
    "stock": ["테마주", "수혜주", "급등주", "상한가", "증권가", "종목"],
    "press_release": ["보도자료", "프로모션", "할인 이벤트"],
    "urgent": ["급증", "급등", "급락", "돌파", "확정", "체결", "착수", "선정", "타결", "발표"],
    "momentum": ["사상 최대", "본격화", "재개", "잇따라"],
    "launch": ["출시", "공개", "런칭", "선보"],
}

GROUP_LABELS = {
    "hdec": "현대건설 직접 언급", "dc": "데이터센터", "power": "전력 인프라",
    "nuclear": "원전/SMR", "construction": "건설/EPC", "smart_const": "스마트건설",
    "competitor": "경쟁사 동향", "macro": "거시경제 변수", "mideast": "중동",
    "overseas": "해외 사업", "gov": "정부 정책", "safety": "안전/중대재해",
    "ai": "AI", "order": "수주/발주",
}

TIER1_SOURCES = {"연합뉴스", "한국경제", "매일경제", "서울경제", "전자신문", "조선비즈", "머니투데이"}
TIER2_SOURCES = {"Mock News", "디지털데일리"}

GRADE_INSTANT = "즉시 알림 후보"
GRADE_DAILY = "일간 요약"
GRADE_WEEKLY = "주간 리포트 후보"
GRADE_EXCLUDED = "제외"

INSTANT_THRESHOLD = 4.5
DAILY_THRESHOLD = 3.5
MAX_INSTANT_CANDIDATES = 3

TREND_CLUSTERS = ["dc", "nuclear", "competitor", "safety", "mideast", "macro", "gov"]

# (라벨, 가점, 조건) — rules.md §6 가점 표 + PRD §12.1 추가분
BONUS_RULES = [
    ("데이터센터+전력+건설/EPC", 0.7,
     lambda g: g["dc"] and g["power"] and g["construction"]),
    ("AI 전력수요+원전/SMR/송배전", 0.7,
     lambda g: g["ai"] and (g["power"] or g["power_demand"]) and (g["nuclear"] or g["grid"])),
    ("경쟁 건설사+AI/스마트건설/데이터센터", 0.6,
     lambda g: g["competitor"] and (g["ai"] or g["smart_const"] or g["dc"])),
    ("중동/원자재/환율+해외수주/플랜트", 0.6,
     lambda g: (g["mideast"] or g["raw_materials"] or g["fx"]) and (g["overseas"] or g["plant"])),
    ("정부 정책+인프라/에너지/건설투자", 0.5,
     lambda g: g["gov"] and g["infra_energy"]),
    ("현대건설 직접 언급", 0.8, lambda g: g["hdec"]),
    ("원전/SMR+데이터센터 전력수요", 0.7,
     lambda g: g["nuclear"] and g["dc"] and (g["power"] or g["power_demand"])),
    ("중대재해/안전+AI 안전관리", 0.5, lambda g: g["safety"] and g["ai"]),
]

INDUSTRY_LINK_GROUPS = ["dc", "power", "grid", "nuclear", "construction",
                        "smart_const", "macro", "infra_energy", "competitor"]


def _contains(text: str, keyword: str) -> bool:
    if keyword.isascii() and keyword.isalpha() and len(keyword) <= 3:
        return re.search(rf"\b{keyword}\b", text) is not None
    return keyword in text


def _group_hits(text: str) -> dict:
    lowered = text.lower()
    return {name: any(_contains(lowered, kw) for kw in kws) for name, kws in GROUPS.items()}


def clamp(value: float, low: float, high: float) -> float:
    """모든 점수는 DB에 쓰기 직전 반드시 이 함수를 통과한다."""
    return max(low, min(high, value))


def _source_tier(source: str) -> float:
    source = (source or "").strip()
    if any(token in source for token in ("블로그", "카페", "재전송")):
        return 1.5
    if source in TIER1_SOURCES:
        return 4.5
    if source in TIER2_SOURCES:
        return 3.5
    return 3.0


def _is_fresh(published_at: str, hours: int = 48) -> bool:
    try:
        published = datetime.fromisoformat(published_at)
        now = datetime.now(published.tzinfo)
        return now - published <= timedelta(hours=hours)
    except (TypeError, ValueError):
        return False


# ---------- 9개 평가 항목 (각 0~5) ----------

def _hdec_relevance(g: dict) -> float:
    if g["hdec"]:
        return 5.0
    score = (
        1.6 * g["dc"]
        + 1.3 * (g["power"] or g["nuclear"])
        + 0.6 * (g["power"] and g["nuclear"])
        + 1.0 * g["construction"]
        + 0.7 * g["smart_const"]
        + 0.9 * (g["mideast"] and (g["construction"] or g["overseas"]))
        + 0.6 * (g["overseas"] and g["order"])
        + 0.4 * g["order"]
        + 0.5 * (g["competitor"] and (g["smart_const"] or g["dc"] or g["ai"]))
        + 0.5 * (g["nuclear"] and g["overseas"])
        + 0.4 * (g["gov"] and g["infra_energy"])
        + 0.4 * (g["safety"] and g["construction"])
    )
    return clamp(score, 0.0, 5.0)


def _executive_importance(g: dict) -> float:
    score = (
        1.5 * (g["dc"] and g["power"])
        + 1.2 * g["nuclear"]
        + 1.2 * g["hdec"]
        + 1.4 * (g["competitor"] and (g["ai"] or g["smart_const"] or g["dc"]))
        + 1.1 * (g["mideast"] and (g["order"] or g["construction"]))
        + 1.1 * (g["macro"] and (g["construction"] or g["overseas"]))
        + 1.0 * (g["dc"] and g["construction"] and g["order"])
        + 0.9 * (g["nuclear"] and (g["order"] or g["overseas"]))
        + 0.7 * (g["gov"] and g["infra_energy"])
        + 0.7 * (g["gov"] and g["invest"])
        + 0.8 * (g["safety"] and g["construction"])
        + 0.8 * (g["safety"] and g["ai"])
        + 0.6 * (g["safety"] and g["regulation"])
        + 0.6 * (g["momentum"] and (g["dc"] or g["nuclear"] or g["construction"]))
        + 0.5 * g["power_demand"]
        + 0.5 * (g["invest"] and (g["dc"] or g["power"] or g["nuclear"]))
        + 0.4 * g["smart_const"]
        + 0.4 * g["urgent"]
    )
    return clamp(score, 0.0, 5.0)


def _business_opportunity(g: dict) -> float:
    score = (
        1.2 * g["order"]
        + 1.0 * (g["dc"] and g["construction"])
        + 0.9 * (g["nuclear"] and (g["order"] or g["overseas"]))
        + 0.8 * (g["mideast"] and g["order"])
        + 0.7 * (g["invest"] and (g["dc"] or g["power"] or g["construction"]))
        + 0.6 * (g["overseas"] and g["order"])
        + 0.6 * (g["dc"] and g["order"])
        + 0.5 * (g["gov"] and g["infra_energy"])
        + 0.5 * (g["smart_const"] and g["construction"])
        + 0.4 * (g["gov"] and g["invest"])
        + 0.4 * (g["momentum"] and g["order"])
    )
    return clamp(score, 0.0, 5.0)


def _risk_potential(g: dict) -> float:
    score = (
        1.8 * (g["safety"] and g["construction"])
        + 1.0 * g["severe"]
        + 1.0 * g["regulation"]
        + 0.9 * g["cost"]
        + 1.0 * (g["macro"] and (g["construction"] or g["overseas"]))
        + 0.8 * (g["competitor"] and (g["ai"] or g["smart_const"] or g["dc"]))
        + 0.5 * (g["mideast"] and g["macro"])
        + 0.3 * g["urgent"]
    )
    return clamp(score, 0.0, 5.0)


def _urgency(g: dict, fresh: bool) -> float:
    score = (
        0.5
        + 2.0 * g["urgent"]
        + 1.0 * g["momentum"]
        + 1.0 * (g["dc"] and g["power_demand"])
        + 0.8 * (g["safety"] and g["urgent"])
        + 0.6 * (g["gov"] and g["urgent"])
        + (0.5 if fresh else 0.0)
    )
    return clamp(score, 0.0, 5.0)


def _trend_repeat(g: dict, cluster_counts: dict) -> float:
    counts = [cluster_counts.get(c, 0) for c in TREND_CLUSTERS if g[c]]
    best = max(counts, default=0)
    if best >= 4:
        return 4.0
    if best == 3:
        return 3.5
    if best == 2:
        return 2.5
    return 1.0


def _competitor_relevance(g: dict) -> float:
    if g["competitor"]:
        return 4.5
    if g["hdec"]:
        return 2.0
    return 0.5


def _macro_impact(g: dict, text: str) -> float:
    lowered = text.lower()
    distinct = sum(1 for kw in GROUPS["macro"] if _contains(lowered, kw))
    if distinct >= 3:
        base = 4.5
    elif distinct == 2:
        base = 4.0
    elif distinct == 1:
        base = 3.0
    else:
        base = 0.5
    return clamp(base + (0.5 if (g["mideast"] and distinct) else 0.0), 0.0, 5.0)


# ---------- 가점 / 감점 ----------

def _apply_bonuses(g: dict) -> tuple[float, list]:
    applied = [(label, value) for label, value, cond in BONUS_RULES if cond(g)]
    return round(sum(v for _, v in applied), 2), applied


def _apply_penalties(g: dict, title: str, source_tier: float, dup_in_batch: bool) -> tuple[float, list]:
    title_hits = _group_hits(title)
    industry_link = any(g[k] for k in INDUSTRY_LINK_GROUPS)
    title_industry_link = any(title_hits[k] for k in INDUSTRY_LINK_GROUPS)
    applied = []
    if g["ai"] and g["launch"] and not industry_link and not g["gov"]:
        applied.append(("HDEC 사업과 무관한 일반 AI 제품 출시", 0.8))
    if g["ai"] and g["consumer"] and not (g["dc"] or g["power"] or g["nuclear"] or g["construction"]):
        applied.append(("소비자 앱 중심 AI 기사", 0.7))
    if g["stock"] and not g["construction"]:
        applied.append(("단순 테마주/주가 기사", 0.7))
    if source_tier <= 1.5:
        applied.append(("출처 불명확 블로그성", 0.6))
    if title_hits["ai"] and not title_industry_link:
        applied.append(("제목에 AI만 있고 산업 연결 없음", 0.8))
    if g["ai"] and g["lifestyle"]:
        applied.append(("연예/생활/교육용 AI 기사", 0.9))
    if dup_in_batch:
        # collector dedup이 선차단하므로 P0-A 정상 흐름에서는 발생하지 않는다.
        applied.append(("동일 내용 24시간 내 재수집", 1.0))
    if g["press_release"]:
        applied.append(("광고성 보도자료 패턴", 0.5))
    return round(sum(v for _, v in applied), 2), applied


# ---------- 설명 텍스트 ----------

def _build_reason(g: dict, bonuses: list, penalties: list) -> str:
    signals = [label for key, label in GROUP_LABELS.items() if g.get(key)]
    parts = []
    parts.append("주요 신호: " + "·".join(signals[:6]) if signals else "뚜렷한 산업 신호 없음")
    if bonuses:
        parts.append("가점: " + ", ".join(f"{label}(+{value})" for label, value in bonuses))
    if penalties:
        parts.append("감점: " + ", ".join(f"{label}(-{value})" for label, value in penalties))
    return " / ".join(parts)


DIM_LABELS = {
    "hdec_relevance": "현대건설 관련성", "executive_importance": "임원 중요도",
    "business_opportunity": "사업기회", "risk_potential": "리스크",
    "urgency": "긴급도", "source_reliability": "출처 신뢰도",
    "trend_repeat": "반복 트렌드", "competitor_relevance": "경쟁사 관련성",
    "macro_impact": "거시경제 영향",
}


def _build_why_not_higher(g: dict, dims: dict, final_score: float) -> str:
    if final_score >= 5.0:
        return "규칙 기반 평가에서 만점 — 추가 상향 여지 없음"
    reasons = []
    if not g["hdec"]:
        reasons.append("현대건설 직접 언급이 없음")
    weak = sorted((v, k) for k, v in dims.items() if v < 2.5)[:2]
    if weak:
        reasons.append(", ".join(DIM_LABELS[k] for _, k in weak) + " 항목이 낮음")
    if not reasons:
        reasons.append("일부 가점 조건이 충족되지 않음")
    return "; ".join(reasons) + " — 더 높은 점수는 아님"


def _build_why_not_lower(g: dict, dims: dict, bonuses: list, final_score: float) -> str:
    strong = sorted(((v, k) for k, v in dims.items() if v >= 3.5), reverse=True)[:2]
    if bonuses:
        return (
            "·".join(label for label, _ in bonuses[:2])
            + " 조건이 충족되어 일반 뉴스 대비 연관성이 높음 — 더 낮은 점수는 아님"
        )
    if strong:
        return (
            ", ".join(DIM_LABELS[k] for _, k in strong)
            + " 항목이 높아 신호 가치가 있음 — 더 낮은 점수는 아님"
        )
    return "출처가 식별 가능한 매체의 기사로 최소 기록 가치는 유지 — 0점 미만은 없음"


# ---------- 메인 ----------

def _build_batch_context(articles: list[dict]) -> dict:
    cluster_counts = {c: 0 for c in TREND_CLUSTERS}
    title_counts = {}
    hits_by_id = {}
    for article in articles:
        text = f"{article['title']} {article.get('snippet') or ''}"
        g = _group_hits(text)
        hits_by_id[article["id"]] = g
        for cluster in TREND_CLUSTERS:
            if g[cluster]:
                cluster_counts[cluster] += 1
        nt = article.get("normalized_title") or ""
        title_counts[nt] = title_counts.get(nt, 0) + 1
    return {"cluster_counts": cluster_counts, "title_counts": title_counts,
            "hits_by_id": hits_by_id}


def _grade_for(final_score: float, dims: dict) -> str:
    if final_score >= INSTANT_THRESHOLD:
        return GRADE_INSTANT
    if final_score >= DAILY_THRESHOLD:
        return GRADE_DAILY
    strategic = (
        dims["trend_repeat"] >= 3.5
        or dims["competitor_relevance"] >= 4.0
        or dims["macro_impact"] >= 3.5
        or dims["risk_potential"] >= 4.0
    )
    if strategic and final_score >= 1.5:
        return GRADE_WEEKLY
    return GRADE_EXCLUDED


def _score_article(article: dict, ctx: dict) -> dict:
    text = f"{article['title']} {article.get('snippet') or ''}"
    g = ctx["hits_by_id"][article["id"]]
    fresh = _is_fresh(article.get("published_at"))
    tier = _source_tier(article.get("source"))

    dims = {
        "hdec_relevance": _hdec_relevance(g),
        "executive_importance": _executive_importance(g),
        "business_opportunity": _business_opportunity(g),
        "risk_potential": _risk_potential(g),
        "urgency": _urgency(g, fresh),
        "source_reliability": tier,
        "trend_repeat": _trend_repeat(g, ctx["cluster_counts"]),
        "competitor_relevance": _competitor_relevance(g),
        "macro_impact": _macro_impact(g, text),
    }
    dims = {k: round(clamp(v, 0.0, 5.0), 1) for k, v in dims.items()}

    rule_bonus, bonuses = _apply_bonuses(g)
    dup_in_batch = ctx["title_counts"].get(article.get("normalized_title") or "", 0) > 1
    rule_penalty, penalties = _apply_penalties(g, article["title"], tier, dup_in_batch)

    weighted = sum(dims[k] * WEIGHTS[k] for k in WEIGHTS)
    final_score = round(clamp(weighted + rule_bonus - rule_penalty, 0.0, 5.0), 2)

    matched_groups = sum(1 for v in g.values() if v)
    confidence = round(clamp(0.45 + 0.05 * min(matched_groups, 8)
                             + (0.08 if tier >= 4.5 else 0.0)
                             + (0.05 if g["hdec"] else 0.0), 0.3, 0.9), 2)

    evidence = ["title", "snippet"]
    if tier != 3.0:
        evidence.append("source")
    if fresh:
        evidence.append("published_at")

    return {
        "id": f"score_{article['id']}",
        "article_id": article["id"],
        **dims,
        "rule_bonus": rule_bonus,
        "rule_penalty": rule_penalty,
        "final_score": final_score,
        "alert_grade": _grade_for(final_score, dims),
        "confidence": confidence,
        "scoring_reason": _build_reason(g, bonuses, penalties),
        "evidence_basis": json.dumps(evidence, ensure_ascii=False),
        "why_not_higher": _build_why_not_higher(g, dims, final_score),
        "why_not_lower": _build_why_not_lower(g, dims, bonuses, final_score),
        "model_name": MODEL_NAME,
        "created_at": db.now_iso(),
    }


def _apply_candidate_cap(rows: list[dict]) -> list[dict]:
    """즉시 알림 후보는 1회 sensing당 최대 3건. 초과분은 점수순으로 일간 요약 전환."""
    instant = [r for r in rows if r["alert_grade"] == GRADE_INSTANT]
    instant.sort(key=lambda r: r["final_score"], reverse=True)
    for row in instant[MAX_INSTANT_CANDIDATES:]:
        row["alert_grade"] = GRADE_DAILY
        row["why_not_higher"] = (
            f"점수 {row['final_score']}점으로 즉시 알림 기준(4.5)을 충족했으나 "
            f"당회 즉시 알림 정원({MAX_INSTANT_CANDIDATES}건) 초과로 일간 요약 전환; "
        ) + row["why_not_higher"]
    return rows


def score_all() -> dict:
    """DB의 전 기사를 rule-based로 점수화하고 article_scores에 저장한다."""
    articles = db.fetch_all_articles()
    ctx = _build_batch_context(articles)
    rows = [_score_article(article, ctx) for article in articles]
    rows = _apply_candidate_cap(rows)
    for row in rows:
        db.upsert_score(row)
    alert_candidates = sum(1 for r in rows if r["alert_grade"] == GRADE_INSTANT)
    return {"scored": len(rows), "alert_candidates": alert_candidates}
