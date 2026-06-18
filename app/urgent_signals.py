"""Urgent executive signal detection for watch mode.

This module is intentionally separate from the daily scoring weights. It looks
for newly seen articles that deserve operator review now, writes review-only
queue records, and never sends notifications.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone

from app import article_quality, decision_relevance, source_quality, watch_state

KST = timezone(timedelta(hours=9))

URGENCY_SEND_CANDIDATE = "send_candidate"
URGENCY_REVIEW_TODAY = "review_today"
URGENCY_MONITOR_ONLY = "monitor_only"

SEEN_NEW_ARTICLE = "new_article"
SEEN_NEW_CLUSTER = "new_cluster"
SEEN_REPEATED_CLUSTER = "repeated_cluster"
SEEN_KNOWN_DUPLICATE = "known_duplicate"

HDEC_NAMES = tuple(n.lower() for n in decision_relevance.HDEC_NAMES)
COMPETITOR_NAMES = tuple(n.lower() for n in decision_relevance.COMPETITOR_NAMES) + (
    "posco e&c", "posco이앤씨", "포스코건설", "samsung c&t",
)

RISK_TERMS = (
    "벌점", "제재", "영업정지", "영업 정지", "중대재해", "사망사고", "안전사고",
    "사고", "품질", "하자", "소송", "조사", "압수수색", "입찰 제한", "입찰제한",
    "평판 리스크", "부실시공", "붕괴", "과징금", "행정처분", "사전통보",
)
SEVERE_RISK_TERMS = (
    "중대재해", "사망사고", "압수수색", "영업정지", "영업 정지", "입찰 제한",
    "입찰제한", "붕괴", "인명피해",
)
ORDER_TERMS = (
    "수주", "우선협상", "우선협상대상자", "본계약", "계약", "입찰", "발주",
    "낙찰", "수주전", "수주액", "착공", "준공",
)
PROJECT_TERMS = (
    "원전", "smr", "소형모듈원자로", "데이터센터", "데이터 센터", "epc",
    "해외 프로젝트", "해외수주", "해외 수주", "도시정비", "재건축", "재개발",
    "플랜트", "중동", "사우디", "uae", "네옴",
)
POLICY_DOMAIN_TERMS = (
    "건설산업", "건설", "원전", "smr", "소형모듈원자로", "데이터센터",
    "데이터 센터", "전력망", "송전", "변전", "중대재해", "안전규제",
    "안전 규제", "공공입찰", "공공 입찰", "입찰",
)
POLICY_ACTION_TERMS = (
    "규제", "법", "시행령", "특별법", "정책", "제도", "의무화", "처벌",
    "제재", "입찰 제한", "입찰제한", "개정", "발표", "확정", "강화",
    "완화", "rules", "rule",
)
COMPETITOR_MOVE_TERMS = (
    "대형 수주", "수주", "원전", "smr", "데이터센터", "데이터 센터",
    "해외 epc", "해외 프로젝트", "도시정비", "재건축", "재개발", "정책 수혜",
    "우선협상", "본계약", "발주", "입찰", "플랜트",
)

STOCK_LISTICLE_TERMS = (
    "주가", "목표가", "목표주가", "투자의견", "투자 의견", "종목", "수혜주",
    "관련주", "대장주", "테마주", "증권 레이더", "증권가", "증권사",
    "삼전닉스", "뭐 사야", "사야 해", "listicle", "급등주", "상한가",
)
SALES_PROMO_TERMS = (
    "분양", "청약", "홍보관", "모델하우스", "견본주택", "특별공급",
    "사이버 모델하우스", "분양가", "1순위 마감", "정당계약",
)
CUSTOMER_OPERATION_AI_TERMS = (
    "ai 상담", "ai상담", "생성형 ai", "인공지능 상담", "챗봇", "콜센터",
    "고객 응대", "고객상담", "상담 자동화", "청약 상담", "분양 상담",
)
SPORTS_TERMS = (
    "배구", "스타랭킹", "박정아", "선수", "구단", "프로팀", "원더독스",
    "리그", "감독", "우승",
)
GENERIC_ROUNDUP_TERMS = (
    "이모저모", "라운드업", "roundup", "브리핑", "뉴스 모음", "소식 모음",
    "업계 소식", "한눈에", "오늘의 뉴스", "금융권 이모저모", "은행권 이모저모",
)
WEAK_HYPE_TERMS = (
    "급등", "폭등", "잭팟", "수혜", "추천", "후기", "직접 써본", "최저가",
    "할인", "협찬", "광고",
)


def detected_now() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


def _text(article: dict) -> str:
    return " ".join([
        str(article.get("title") or ""),
        str(article.get("snippet") or ""),
        str(article.get("source") or ""),
    ]).lower()


def _hits(text: str, terms) -> bool:
    return any(term.lower() in text for term in terms)


def _domain(text: str) -> str:
    if _hits(text, ("smr", "원전", "원자력", "소형모듈원자로")):
        return "nuclear_smr"
    if _hits(text, ("데이터센터", "데이터 센터", "idc", "냉각")):
        return "ai_datacenter"
    if _hits(text, ("전력망", "송전", "변전", "전력 인프라", "송배전")):
        return "power_grid"
    if _hits(text, ("도시정비", "재건축", "재개발", "정비사업")):
        return "city_redevelopment"
    if _hits(text, ("중동", "사우디", "uae", "네옴", "해외", "플랜트")):
        return "overseas_epc"
    if _hits(text, ("중대재해", "안전", "사고", "품질", "하자")):
        return "safety_quality"
    if _hits(text, ("스마트건설", "스마트 건설", "bim", "로봇")):
        return "smart_construction"
    return "general"


def _risk_class(text: str) -> str:
    if _hits(text, SEVERE_RISK_TERMS):
        return "severe_hdec_risk"
    if _hits(text, ("벌점", "제재", "영업정지", "입찰 제한", "입찰제한", "과징금",
                   "행정처분", "사전통보")):
        return "sanction"
    if _hits(text, ("소송", "조사", "압수수색", "고발")):
        return "litigation_investigation"
    if _hits(text, ("품질", "하자", "부실시공", "붕괴")):
        return "quality_defect"
    return "risk"


def _company_key(text: str) -> str:
    for name in COMPETITOR_NAMES:
        if name in text:
            return name.replace(" ", "_")
    return "competitor"


def hard_exclusion_reason(article: dict) -> str:
    """Return a hard urgent-exclusion reason, or empty string if eligible."""
    text = _text(article)
    aq = article_quality.assess(article.get("source"), article.get("title"))
    sq = source_quality.classify(article.get("source"), article.get("title"))

    if aq.get("stock_hype") or _hits(text, STOCK_LISTICLE_TERMS):
        return "stock/listicle article"
    if _hits(text, SPORTS_TERMS):
        return "sports/player-ranking article"
    customer_ai = _hits(text, CUSTOMER_OPERATION_AI_TERMS)
    if _hits(text, SALES_PROMO_TERMS) and not customer_ai:
        return "sales/promo article"
    if _hits(text, GENERIC_ROUNDUP_TERMS):
        return "generic roundup article"
    if sq["source_quality"] in ("excluded", "low") and _hits(text, WEAK_HYPE_TERMS):
        return "low-quality weak-source hype"
    return ""


def detect_trigger(article: dict) -> dict | None:
    text = _text(article)
    hdec_direct = _hits(text, HDEC_NAMES)
    competitor = _hits(text, COMPETITOR_NAMES)
    domain = _domain(text)

    if hdec_direct and _hits(text, RISK_TERMS):
        risk_class = _risk_class(text)
        severe = risk_class == "severe_hdec_risk"
        return {
            "trigger": "hdec_direct_risk",
            "cluster_key": f"hdec_direct_risk:{risk_class}:{domain}",
            "risk_class": risk_class,
            "urgency_score": 4.9 if severe else 4.4,
            "why_now": "현대건설 직접 리스크 신호 신규 감지",
            "affected_units": ["안전품질", "법무/컴플라이언스", "전략기획"],
            "recommended_action": (
                "운영자 즉시 사실확인 후 유관부서 공유 여부 검토"
                if severe else "금일 운영자 리뷰에서 리스크 영향 확인"),
        }

    if hdec_direct and (_hits(text, ORDER_TERMS) or _hits(text, PROJECT_TERMS)):
        score = 4.5 if _hits(text, ("우선협상", "본계약", "낙찰", "대형 수주")) else 4.0
        units = ["전략기획", "영업/수주"]
        if domain in ("nuclear_smr", "power_grid"):
            units.append("원전/에너지")
        if domain == "ai_datacenter":
            units.append("데이터센터")
        if domain == "overseas_epc":
            units.append("해외사업")
        return {
            "trigger": "hdec_direct_order_project",
            "cluster_key": f"hdec_direct_project:{domain}",
            "risk_class": "project_order",
            "urgency_score": score,
            "why_now": "현대건설 직접 수주·프로젝트 신호 신규 감지",
            "affected_units": units,
            "recommended_action": "수주 단계·계약 범위·경쟁 구도 확인",
        }

    if _hits(text, POLICY_DOMAIN_TERMS) and _hits(text, POLICY_ACTION_TERMS):
        return {
            "trigger": "policy_regulatory_shock",
            "cluster_key": f"policy_regulatory:{domain}",
            "risk_class": "policy_regulatory",
            "urgency_score": 3.8,
            "why_now": "건설·원전·데이터센터·입찰 관련 정책/규제 변화 감지",
            "affected_units": ["전략기획", "법무/컴플라이언스", "사업기획"],
            "recommended_action": "현대건설 사업 영향과 대응 필요성 검토",
        }

    if competitor and _hits(text, COMPETITOR_MOVE_TERMS):
        return {
            "trigger": "major_competitor_move",
            "cluster_key": f"competitor_move:{_company_key(text)}:{domain}",
            "risk_class": "competitor_move",
            "urgency_score": 3.2,
            "why_now": "주요 경쟁사 수주·원전·데이터센터·해외 EPC 움직임 감지",
            "affected_units": ["전략기획", "영업/수주", "기술연구원"],
            "recommended_action": "경쟁 구도와 당사 대응 포인트 모니터링",
        }

    return None


def _urgency_class(score: float) -> str:
    if score >= 4.6:
        return URGENCY_SEND_CANDIDATE
    if score >= 3.0:
        return URGENCY_REVIEW_TODAY
    return URGENCY_MONITOR_ONLY


def _seen_status(state: dict, article: dict, cluster_key: str | None) -> str:
    if watch_state.first_seen_match(state, article):
        return SEEN_KNOWN_DUPLICATE
    if cluster_key and watch_state.cluster_entry(state, cluster_key):
        return SEEN_REPEATED_CLUSTER
    if cluster_key:
        return SEEN_NEW_CLUSTER
    return SEEN_NEW_ARTICLE


def _material_change(article: dict, cluster: dict | None, risk_class: str,
                     trigger: str) -> bool:
    if not cluster:
        return True
    known_risks = set(cluster.get("risk_classes") or [])
    if risk_class and risk_class not in known_risks:
        return True
    known_sources = set(cluster.get("sources") or [])
    source = str(article.get("source") or "").strip()
    if (trigger == "hdec_direct_risk" and risk_class == "severe_hdec_risk"
            and source and source not in known_sources):
        return True
    return False


def build_queue_entry(article: dict, trigger: dict, seen_status: str,
                      detected_at: str, *, repeated_without_change: bool = False) -> dict:
    score = float(trigger.get("urgency_score") or 0.0)
    if repeated_without_change:
        score = min(score, 2.0)
    urgency_class = _urgency_class(score)
    why_now = trigger["why_now"]
    if repeated_without_change:
        why_now = (
            "이미 관찰 중인 클러스터의 반복 기사 — 새 긴급 알림 대신 모니터링 유지"
        )
    return {
        "article_id": str(article.get("id") or ""),
        "title": str(article.get("title") or ""),
        "source": source_quality.normalize_display_source(str(article.get("source") or "")),
        "url": str(article.get("url") or ""),
        "published_at": article.get("published_at"),
        "detected_at": detected_at,
        "urgency_score": round(score, 2),
        "urgency_class": urgency_class,
        "why_now": why_now,
        "affected_units": trigger.get("affected_units") or ["전략기획"],
        "recommended_action": trigger.get("recommended_action") or "운영자 검토",
        "evidence_cluster_key": trigger.get("cluster_key") or "",
        "seen_status": seen_status,
        "send_allowed": False,
        "review_required": True,
    }


def build_review_digest(summary: dict, queue: list[dict], generated_at: str) -> str:
    lines = [
        "== HDEC Urgent Signal Queue ==",
        f"{generated_at} (KST)",
        f"scanned={summary['scanned_count']} new={summary['new_count']} "
        f"urgent_candidates={summary['urgent_candidate_count']} "
        f"duplicates={summary['skipped_duplicate_count']}",
        "Telegram send: blocked by human review gate",
    ]
    candidates = [
        item for item in sorted(queue, key=lambda r: r["urgency_score"], reverse=True)
        if item["urgency_class"] in (URGENCY_SEND_CANDIDATE, URGENCY_REVIEW_TODAY)
    ][:5]
    if not candidates:
        lines.append("No urgent review candidates.")
        return "\n".join(lines)

    lines.append("")
    lines.append("[Top urgent candidates]")
    for idx, item in enumerate(candidates, start=1):
        units = ", ".join(item.get("affected_units") or [])
        lines.append(f"{idx}. [{item['urgency_class']}] {item['title']}")
        lines.append(
            f"   score={item['urgency_score']:.1f} source={item['source']} "
            f"seen={item['seen_status']} cluster={item['evidence_cluster_key']}")
        lines.append(f"   why_now: {item['why_now']}")
        lines.append(f"   affected_units: {units}")
        lines.append(f"   action: {item['recommended_action']}")
    return "\n".join(lines)


def evaluate_articles(articles: list[dict], state: dict | None = None,
                      detected_at: str | None = None) -> dict:
    """Evaluate current articles against watch state and return a review queue."""
    ts = detected_at or detected_now()
    current_state = copy.deepcopy(state or watch_state.empty_state())
    queue: list[dict] = []
    scanned = len(articles)
    new_count = 0
    skipped_duplicate = 0
    skipped_excluded = 0
    seen_status_counts = {
        SEEN_NEW_ARTICLE: 0,
        SEEN_NEW_CLUSTER: 0,
        SEEN_REPEATED_CLUSTER: 0,
        SEEN_KNOWN_DUPLICATE: 0,
    }
    skipped: list[dict] = []

    for article in articles:
        trigger = detect_trigger(article)
        cluster_key = (trigger or {}).get("cluster_key")
        status = _seen_status(current_state, article, cluster_key)
        seen_status_counts[status] = seen_status_counts.get(status, 0) + 1
        if status != SEEN_KNOWN_DUPLICATE:
            new_count += 1

        exclusion = hard_exclusion_reason(article)
        if status == SEEN_KNOWN_DUPLICATE:
            skipped_duplicate += 1
            skipped.append({
                "article_id": article.get("id"),
                "title": article.get("title"),
                "reason": "known_duplicate",
                "seen_status": status,
            })
            watch_state.mark_seen(
                current_state, article, cluster_key=cluster_key, detected_at=ts,
                urgency_class=URGENCY_MONITOR_ONLY,
                risk_class=(trigger or {}).get("risk_class"))
            continue
        if exclusion:
            skipped_excluded += 1
            skipped.append({
                "article_id": article.get("id"),
                "title": article.get("title"),
                "reason": exclusion,
                "seen_status": status,
            })
            watch_state.mark_seen(
                current_state, article, cluster_key=cluster_key, detected_at=ts,
                urgency_class=URGENCY_MONITOR_ONLY,
                risk_class=(trigger or {}).get("risk_class"))
            continue
        if not trigger:
            watch_state.mark_seen(
                current_state, article, cluster_key=cluster_key, detected_at=ts,
                urgency_class=URGENCY_MONITOR_ONLY)
            continue

        cluster = watch_state.cluster_entry(current_state, trigger["cluster_key"])
        repeated_without_change = (
            status == SEEN_REPEATED_CLUSTER
            and not _material_change(
                article, cluster, trigger.get("risk_class") or "", trigger["trigger"])
        )
        entry = build_queue_entry(
            article, trigger, status, ts,
            repeated_without_change=repeated_without_change)
        queue.append(entry)
        watch_state.mark_seen(
            current_state, article, cluster_key=trigger["cluster_key"],
            detected_at=ts, urgency_class=entry["urgency_class"],
            risk_class=trigger.get("risk_class"))

    urgent_count = sum(
        1 for item in queue
        if item["urgency_class"] in (URGENCY_SEND_CANDIDATE, URGENCY_REVIEW_TODAY)
    )
    summary = {
        "scanned_count": scanned,
        "new_count": new_count,
        "urgent_candidate_count": urgent_count,
        "skipped_duplicate_count": skipped_duplicate,
        "skipped_excluded_count": skipped_excluded,
        "queue_count": len(queue),
        "seen_status_counts": seen_status_counts,
        "telegram_send": "blocked by human review gate",
    }
    queue.sort(key=lambda r: (r["urgency_score"], r["published_at"] or ""), reverse=True)
    digest = build_review_digest(summary, queue, ts)
    return {
        "generated_at": ts,
        "summary": summary,
        "queue": queue,
        "skipped": skipped,
        "review_digest": digest,
        "next_state": current_state,
    }


def public_result(result: dict) -> dict:
    return {k: v for k, v in result.items() if k != "next_state"}


def commit_result(result: dict, path=None) -> dict:
    state = copy.deepcopy(result["next_state"])
    watch_state.write_queue(
        state, result["queue"], result["review_digest"], result["generated_at"])
    watch_state.save_state(state, path)
    return state


def to_json(result: dict) -> str:
    return json.dumps(public_result(result), ensure_ascii=False, indent=2)
