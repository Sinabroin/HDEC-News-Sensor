"""Risk event clustering for the executive/operator review layer.

This module groups article-level risk signals into real-world event summaries.
It is deliberately a pure derived layer:
- no DB access, no network access, no sender access;
- no score or grade recalculation;
- article cards and radar sections remain owned by briefing/radar.
"""

import re
import unicodedata
from datetime import datetime

from app import article_quality, decision_relevance, radar, source_quality

MAX_SUPPORTING_ARTICLES = 5

SEVERITY_ORDER = {"send_candidate": 0, "review_today": 1, "monitor": 2}

EVENT_TYPE_LABELS = {
    "hdec_direct_risk": "현대건설 직접 리스크",
    "policy_regulatory": "정책·규제",
    "safety_severe": "중대재해·안전",
    "quality_defect": "품질·하자",
    "legal_dispute": "소송·분쟁",
    "bid_restriction": "벌점·입찰제한",
}

SEVERITY_LABELS = {
    "send_candidate": "즉시 확인 후보",
    "review_today": "당일 검토",
    "monitor": "모니터링",
}

IMPACT_ORDER = [
    "공공수주",
    "입찰자격",
    "평판",
    "품질관리",
    "안전관리",
    "선분양/사업일정",
    "해외 프로젝트",
]

_CORE_STOPWORDS = {
    "단독", "속보", "기획", "심층", "관련", "추진", "착수", "확대",
    "강화", "논란", "가능성", "파장", "점검", "확인", "오늘", "뉴스",
    "건설사", "건설업계", "현대건설", "현장", "기준", "제도",
}

_SEVERE_TERMS = (
    "중대재해", "중대재해처벌법", "사망사고", "사망 사고", "산업재해", "산재",
    "압수수색", "영업정지", "영업 정지", "입찰제한", "입찰 제한",
)
_REVIEW_RISK_TERMS = (
    "벌점", "하자", "품질", "부실시공", "소송", "조사", "공공수주",
    "선분양", "특별감독", "제재", "고용부", "고용노동부", "국토부",
    "서울시", "공정위", "안전규제", "품질관리", "품질 점검", "품질점검",
)
_CONCRETE_RISK_ACTION_TERMS = (
    "중대재해", "중대재해처벌법", "사망사고", "사망 사고", "산업재해", "산재",
    "안전사고", "붕괴", "인명피해", "압수수색",
    "영업정지", "영업 정지", "입찰제한", "입찰 제한", "공공수주 제한",
    "벌점", "공식 벌점 통보", "벌점 통보", "제재", "과징금",
    "행정처분", "행정 처분", "사전통보", "사전 통보", "면허취소", "등록말소",
    "하자", "부실시공", "품질 점검", "품질점검", "품질 논란",
    "철근 누락", "철근누락", "누수",
    "소송", "손배", "손해배상", "공방", "공기지연", "공기 지연",
    "고발", "법 위반",
)
_SPECIAL_SUPERVISION_CONTEXT_TERMS = (
    "중대재해", "사망", "산재", "산업재해", "안전사고",
    "현장", "건설현장", "고용부", "고용노동부",
)
_POLICY_OR_INDUSTRY_CONTEXT_TERMS = tuple(dict.fromkeys(
    list(radar.INDUSTRY_KEYWORDS) + [
        "국토부", "국토교통부", "고용부", "고용노동부", "공정위", "정부", "서울시",
        "공공수주", "공공 수주", "공공공사", "공공 공사", "입찰", "건설사", "건설현장",
    ]
))
_ALL_RISK_TERMS = tuple(dict.fromkeys(
    list(radar.RISK_ACTION_STRONG) + list(radar.RISK_REG_WEAK)
    + list(_SEVERE_TERMS) + list(_REVIEW_RISK_TERMS)
))

# P0-D3S: 시장·증권 코멘터리 마커(제목 기준) — 주가 반응/종목성/기대감 기사를 식별한다.
# 이런 기사는 '사건'이 아니라 시장 반응이므로, 제목에 구체적 리스크 액션이 없으면 리스크
# 사건 클러스터에서 제외한다. bare '주가'는 발주가/수주가에 substring 오탐이 있어 쓰지 않고
# (급등/급락/폭등으로 대체), '株'는 한국어 제목에서 사실상 종목 표기로만 쓰여 안전하다.
_MARKET_COMMENTARY_TERMS = (
    "급등", "급락", "폭등", "건설주", "건설株", "반도체주", "반도체株", "株",
    "관련주", "수혜주", "테마주", "목표가", "목표주가", "증권", "종목",
    "상한가", "잭팟", "사세요", "오를까", "오를지", "샴페인", "투자 기대",
    "etf", "코스피", "코스닥", "대장주", "팔고", "상장폐지", "시간외", "매수",
)


def _contains(text: str, terms) -> bool:
    return any((term or "").lower() in text for term in terms)


def _text(row: dict) -> str:
    return " ".join([row.get("title") or "", row.get("snippet") or ""]).lower()


def _normalize(text: str) -> str:
    value = unicodedata.normalize("NFKC", text or "").lower()
    value = re.sub(r"[\[\]{}()<>〈〉《》「」『』\"'“”‘’`´]+", " ", value)
    value = re.sub(r"[-–—→←·ㆍ/|:;,.!?~…]+", " ", value)
    value = re.sub(r"[^0-9a-z가-힣]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _core_tokens(text: str, limit: int = 6) -> list[str]:
    tokens = []
    for token in _normalize(text).split():
        if token in _CORE_STOPWORDS:
            continue
        if len(token) < 2 and not token.isdigit():
            continue
        tokens.append(token)
    return tokens[:limit]


def _has_hdec(row: dict, decision: dict | None = None) -> bool:
    title = row.get("title") or ""
    return bool(
        (decision or {}).get("hdec_direct")
        or article_quality.assess(row.get("source"), title).get("hdec_direct")
        or decision_relevance.is_hdec_direct(title)
    )


def _has_concrete_risk_action(text: str) -> bool:
    if "철근" in text and "누락" in text:
        return True
    if _contains(text, _CONCRETE_RISK_ACTION_TERMS):
        return True
    return "특별감독" in text and _contains(text, _SPECIAL_SUPERVISION_CONTEXT_TERMS)


def _has_strong_risk(text: str) -> bool:
    return _has_concrete_risk_action(text)


def _has_review_risk(text: str) -> bool:
    return _contains(text, _ALL_RISK_TERMS)


def event_key_for_row(row: dict, decision: dict | None = None,
                      radar_section: str | None = None) -> str | None:
    """Return a deterministic event key for a risk article, or None.

    Keys group same real-world incidents first, then fall back to compact
    noun-token keys. The fallback is intentionally conservative so generic risk
    categories do not collapse unrelated incidents into one event.
    """
    aq = article_quality.assess(row.get("source"), row.get("title"))
    if aq.get("stock_hype"):
        return None

    # P0-D3S: 시장·증권 코멘터리(주가 반응·건설株·종전 투자기대·샴페인 등)는 사건이 아니다.
    # 제목에 구체적 리스크 액션(중대재해·벌점·제재·하자·소송 등)이 없으면 리스크 사건에서
    # 제외한다 — stock_hype에서 면제된 현대건설 직접 시세 기사('현대건설 주가 28% 급등')도
    # 여기서 걸러진다. 제목만 보므로 시장 기사 스니펫에 섞인 위험 단어가 사건으로 승격되지 않는다.
    title_low = (row.get("title") or "").lower()
    if (_contains(title_low, _MARKET_COMMENTARY_TERMS)
            and not _has_concrete_risk_action(title_low)):
        return None

    text = _text(row)
    has_hdec = _has_hdec(row, decision)
    concrete_action = _has_concrete_risk_action(text)
    riskish = concrete_action or radar_section == radar.RISK

    # Known HDEC incident keys run before generic risk eligibility so excluded direct-risk
    # follow-up articles that omit legal action terms still attach to the event evidence.
    gtx_station_anchor = "삼성역" in text or "영동대로" in text
    gtx_station_action = _contains(
        text,
        ("철근", "철근누락", "철근 누락", "벌점", "공공수주", "공공 수주", "입찰제한", "입찰 제한"),
    )
    hdec_rebar_anchor = (
        has_hdec
        and _contains(text, ("철근", "철근누락", "철근 누락", "178톤"))
        and _contains(text, ("벌점", "공공수주", "공공 수주", "선분양",
                             "안전불감증", "입찰제한", "입찰 제한"))
    )
    if (gtx_station_anchor and gtx_station_action) or hdec_rebar_anchor:
        return "hdec_risk_gtx_samseong_rebar_penalty"

    if not riskish:
        return None

    if (
        ("지축" in text or "지축역" in text)
        and _contains(text, ("누수", "물난리", "벌점", "설계", "감리", "하자", "품질"))
    ):
        return "hdec_risk_jichuk_leak_defect_penalty"

    if (
        "아인 두바이" in text
        or "ain dubai" in text
        or ("두바이" in text and _contains(text, ("손배", "공방", "하자", "공기지연", "공기 지연")))
    ):
        return "hdec_risk_ain_dubai_dispute_defect_delay"

    if (
        _contains(text, ("중대재해", "사망사고", "사망 사고"))
        or (_contains(text, ("고용부", "고용노동부", "특별감독"))
            and _contains(text, ("중대재해", "사망", "산재", "산업재해", "건설현장", "현장")))
    ):
        return "construction_severe_accident_supervision"

    if (
        _contains(text, ("벌점", "입찰제한", "입찰 제한"))
        and _contains(text, ("국토부", "국토교통부", "서울시", "고용부", "고용노동부",
                             "공공수주", "공공 수주", "건설사", "건설업계"))
    ):
        return "construction_penalty_bid_restriction_policy"

    if has_hdec and concrete_action:
        tokens = _core_tokens(text)
        return "hdec_risk:" + "_".join(tokens) if tokens else "hdec_risk:unknown"

    if concrete_action and (
        radar_section == radar.RISK or _contains(text, _POLICY_OR_INDUSTRY_CONTEXT_TERMS)
    ):
        tokens = _core_tokens(text)
        return "policy_risk:" + "_".join(tokens) if tokens else "policy_risk:unknown"

    return None


def _event_type(key: str, rows: list[dict], has_direct_hdec: bool) -> str:
    text = " ".join(_text(r) for r in rows)
    if "severe_accident" in key or _contains(text, ("중대재해", "사망사고", "사망 사고")):
        return "safety_severe"
    if "ain_dubai" in key or _contains(text, ("손배", "공방", "소송", "분쟁", "공기지연", "공기 지연")):
        return "legal_dispute"
    if "penalty" in key or _contains(text, ("입찰제한", "입찰 제한", "영업정지", "영업 정지", "벌점")):
        return "bid_restriction"
    if "defect" in key or _contains(text, ("하자", "품질", "부실시공", "철근", "누수", "설계", "감리")):
        return "quality_defect"
    if _contains(text, ("규제", "특별법", "의무화", "국토부", "고용부", "기준 강화", "제도")):
        return "policy_regulatory"
    return "hdec_direct_risk" if has_direct_hdec else "policy_regulatory"


def _event_title(key: str, event_type: str, rows: list[dict]) -> str:
    if key == "hdec_risk_gtx_samseong_rebar_penalty":
        return "GTX 삼성역 철근 누락·벌점·공공수주 제한 가능성"
    if key == "hdec_risk_jichuk_leak_defect_penalty":
        return "지축 누수·벌점·품질관리 리스크"
    if key == "hdec_risk_ain_dubai_dispute_defect_delay":
        return "아인 두바이 하자·공기지연 손배 분쟁"
    if key == "construction_severe_accident_supervision":
        return "건설현장 중대재해·고용부 특별감독 가능성"
    if key == "construction_penalty_bid_restriction_policy":
        return "건설사 벌점·입찰제한 제도 변화"

    title = (rows[0].get("title") or "").strip()
    title = re.sub(r"^\[[^\]]+\]\s*", "", title)
    if len(title) > 58:
        title = title[:57] + "…"
    prefix = "현대건설 직접 리스크" if event_type == "hdec_direct_risk" else EVENT_TYPE_LABELS.get(event_type, "리스크 사건")
    return f"{prefix} — {title}" if title else prefix


def _impact_axes(rows: list[dict], event_type: str, has_direct_hdec: bool) -> list[str]:
    text = " ".join(_text(r) for r in rows)
    axes = set()
    if _contains(text, ("공공수주", "공공 수주", "공공공사", "공공 공사", "발주", "수주")):
        axes.add("공공수주")
    if _contains(text, ("입찰제한", "입찰 제한", "입찰", "영업정지", "영업 정지", "벌점", "제재")):
        axes.add("입찰자격")
    if has_direct_hdec or _contains(text, ("평판", "불안", "안전불감증", "논란", "책임")):
        axes.add("평판")
    if event_type == "quality_defect" or _contains(text, ("하자", "품질", "철근", "누수", "부실시공", "설계", "감리")):
        axes.add("품질관리")
    if event_type == "safety_severe" or _contains(text, ("중대재해", "사망", "안전", "고용부", "특별감독", "산재")):
        axes.add("안전관리")
    if _contains(text, ("선분양", "사업일정", "공기지연", "공기 지연", "분양", "일정")):
        axes.add("선분양/사업일정")
    if _contains(text, ("해외", "두바이", "ain dubai", "아인 두바이")):
        axes.add("해외 프로젝트")
    if not axes and event_type == "policy_regulatory":
        axes.add("입찰자격")
    return [axis for axis in IMPACT_ORDER if axis in axes]


def _parse_time(value) -> float:
    try:
        text = str(value or "")
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _source_profile(row: dict) -> dict:
    q = source_quality.classify(row.get("source"), row.get("title"))
    display = source_quality.normalize_display_source(row.get("source")) or row.get("source") or "출처 미상"
    raw = row.get("source") or "출처 미상"
    is_gateway = display != raw or "경유" in display or q.get("source_type") == "aggregator"
    return {
        "source": raw,
        "display_source": display,
        "source_quality": q["source_quality"],
        "source_quality_label": q["source_quality_label"],
        "source_type": q["source_type"],
        "is_gateway": is_gateway,
        "is_major": q["source_quality"] == "trusted",
        "is_weak": q["source_quality"] in ("low", "excluded"),
    }


def _supporting_article(row: dict, event_key: str, decision: dict | None = None) -> dict:
    prof = _source_profile(row)
    has_hdec = _has_hdec(row, decision)
    return {
        "article_id": row.get("id"),
        "title": row.get("title") or "",
        "source": prof["source"],
        "display_source": prof["display_source"],
        "source_quality": prof["source_quality"],
        "source_quality_label": prof["source_quality_label"],
        "source_type": prof["source_type"],
        "is_gateway_source": prof["is_gateway"],
        "published_at": row.get("published_at"),
        "url": row.get("url"),
        "final_score": row.get("final_score"),
        "alert_grade": row.get("alert_grade"),
        "included_from_excluded": row.get("alert_grade") == "제외",
        "hdec_direct": has_hdec,
        "event_key": event_key,
    }


def _support_sort(row: dict) -> tuple:
    grade = row.get("alert_grade")
    grade_rank = {"즉시 확인": 0, "검토 필요": 1, "추적 필요": 2, "제외": 3}.get(grade, 4)
    prof = _source_profile(row)
    source_rank = 0 if prof["is_major"] else (2 if prof["is_weak"] or prof["is_gateway"] else 1)
    return (grade_rank, source_rank, -(row.get("final_score") or 0), -_parse_time(row.get("published_at")),
            row.get("id") or "")


def _severity(rows: list[dict], has_direct_hdec: bool, has_major_source: bool,
              only_gateway_or_weak: bool) -> str:
    text = " ".join(_text(r) for r in rows)
    severe_or_confirmed = (
        _contains(text, _SEVERE_TERMS)
        or ("벌점" in text and _contains(text, ("확정", "통보", "서울시", "국토부", "입찰제한", "입찰 제한")))
    )
    if has_direct_hdec and severe_or_confirmed and (has_major_source or not only_gateway_or_weak):
        return "send_candidate"
    if has_direct_hdec and _contains(text, _REVIEW_RISK_TERMS + _SEVERE_TERMS):
        return "review_today"
    return "monitor"


def _confirmation_targets(event_type: str, text: str) -> list[str]:
    targets = []
    if _contains(text, ("서울시", "gtx", "삼성역", "영동대로", "철근", "벌점")):
        targets.append("서울시")
    if _contains(text, ("국토부", "국토교통부", "입찰제한", "벌점", "건설사")):
        targets.append("국토부")
    if event_type == "safety_severe" or _contains(text, ("고용부", "고용노동부", "중대재해", "특별감독")):
        targets.append("고용부")
    if _contains(text, ("공시", "소송", "손배", "해외", "두바이", "현대건설")):
        targets.append("공시/공식자료")
    if not targets:
        targets.append("공식자료")
    out = []
    for target in targets:
        if target not in out:
            out.append(target)
    return out


def build_risk_event_clusters(scored_rows: list[dict], *,
                              categories: dict[str, str] | None = None,
                              decisions: dict[str, dict] | None = None,
                              radar_sections: dict[str, str] | None = None,
                              scores_by_id: dict[str, dict] | None = None,
                              limit: int = 8) -> list[dict]:
    """Build bounded event-level risk clusters from scored article rows."""
    del categories, scores_by_id  # reserved for future display fields; do not recalculate scores.
    decisions = decisions or {}
    radar_sections = radar_sections or {}
    grouped: dict[str, list[dict]] = {}

    for row in scored_rows:
        if source_quality.classify(row.get("source"), row.get("title"))["source_quality"] == "excluded":
            continue
        rid = row.get("id")
        decision = decisions.get(rid, {})
        key = event_key_for_row(row, decision=decision, radar_section=radar_sections.get(rid))
        if key:
            grouped.setdefault(key, []).append(row)

    events = []
    for key, rows in grouped.items():
        rows = sorted(rows, key=_support_sort)
        has_direct_hdec = any(_has_hdec(r, decisions.get(r.get("id"), {})) for r in rows)
        profiles = [_source_profile(r) for r in rows]
        sources = []
        seen_sources = set()
        for prof in profiles:
            name = prof["display_source"]
            if name in seen_sources:
                continue
            seen_sources.add(name)
            sources.append({
                "name": name,
                "raw_source": prof["source"],
                "source_quality": prof["source_quality"],
                "source_quality_label": prof["source_quality_label"],
                "source_type": prof["source_type"],
                "is_gateway": prof["is_gateway"],
            })

        has_gateway = any(p["is_gateway"] for p in profiles)
        has_major = any(p["is_major"] for p in profiles)
        only_gateway_or_weak = all(p["is_gateway"] or p["is_weak"] for p in profiles)
        event_type = _event_type(key, rows, has_direct_hdec)
        text = " ".join(_text(r) for r in rows)
        severity = _severity(rows, has_direct_hdec, has_major, only_gateway_or_weak)
        support = [
            _supporting_article(r, key, decisions.get(r.get("id"), {}))
            for r in rows[:MAX_SUPPORTING_ARTICLES]
        ]
        excluded_support_count = sum(1 for r in rows if r.get("alert_grade") == "제외")
        confirmation_targets = _confirmation_targets(event_type, text)
        events.append({
            "event_key": key,
            "event_title": _event_title(key, event_type, rows),
            "event_type": event_type,
            "event_type_label": EVENT_TYPE_LABELS.get(event_type, event_type),
            "severity": severity,
            "severity_label": SEVERITY_LABELS.get(severity, severity),
            "impact_axes": _impact_axes(rows, event_type, has_direct_hdec),
            "article_count": len(rows),
            "source_count": len(sources),
            "sources": sources,
            "supporting_articles": support,
            "has_gateway_source": has_gateway,
            "has_major_source": has_major,
            "has_direct_hdec": has_direct_hdec,
            "excluded_support_count": excluded_support_count,
            "needs_operator_confirmation": True,
            "operator_confirmation_targets": confirmation_targets,
            "operator_confirmation_note": " / ".join(confirmation_targets) + " 확인 필요",
            "send_allowed": False,
            "review_required": True,
            "latest_published_at": max((r.get("published_at") or "" for r in rows), default=""),
        })

    events.sort(key=lambda e: (
        SEVERITY_ORDER.get(e["severity"], 9),
        0 if e["has_direct_hdec"] else 1,
        -len(e["impact_axes"]),
        0 if e["has_major_source"] else (2 if e["has_gateway_source"] else 1),
        -_parse_time(e.get("latest_published_at")),
        e["event_key"],
    ))
    return events[:limit]
