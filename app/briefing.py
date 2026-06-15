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
from datetime import datetime, timedelta, timezone

from app import config, db, insight, macro_snapshot, scoring

KST = timezone(timedelta(hours=9))

HEADER = "HDEC Executive Radar"
TOP_IMMEDIATE = 3
TOP_ISSUES = 5
TOP_THEMES = 5

# spread 한계 고지: 토픽 후보 집합이 겹치는 신호 수 기반의 보수적 추정이다.
# (동일 사건 클러스터링이 아니며, dedup으로 제거된 중복 기사는 집계되지 않는다)
SPREAD_METHOD = "topic-overlap heuristic — 동일 토픽 후보를 공유하는 신호 수 기반 추정"

# 운영자/표시 레이어용 짧은 고지 한 줄 (개발자용 장문 면책 대신). "추정" 표현 유지.
OPERATOR_NOTE = (
    "운영자 검토용 자동 생성 브리프입니다. 유사 주제 기사 수는 제목·토픽 기준 "
    "추정값이며 동일 사건 클러스터 확정값이 아닙니다."
)

# 용어 캡션 — UI·리포트가 그대로 노출하는 단일 소스 (혼란스러운 지표 설명)
SPREAD_NOTE = (
    "유사 주제 기사는 제목·토픽 기준의 참고 묶음(추정)이며, "
    "동일 사건 클러스터 확정값은 아닙니다."
)
THEME_STRENGTH_NOTE = (
    "상대 강도는 가장 강한 테마를 100으로 둔 상대 지표입니다 "
    "(관련 기사 수와 중요도 점수를 합산한 내부 정렬 값 기준)."
)

# 저장된 implication 텍스트 → insight 카테고리 키 역매핑 (탐지 로직 중복 방지)
_CATEGORY_BY_IMPLICATION = {
    text: key for key, text in insight.IMPLICATION_TEMPLATES.items()
}

# ---- executive_one_liner 조립용 표현 사전 (표현 전용 — 점수/등급 판단 아님) ----

SUBJECT_BY_CATEGORY = {
    "hdec": "현대건설 직접 관련 수주 신호",
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
    scoring.GRADE_WEEKLY: "주간 보고 후보",
    scoring.GRADE_EXCLUDED: "모니터링",
}

# 점수대 라벨 — final_score(0~5) 기준 직관 버킷 (점수 축 표현, 등급 재계산 아님)
SCORE_BANDS = [
    (4.5, "즉시 확인"),
    (3.5, "검토 필요"),
    (2.0, "주간 모니터링"),
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
        # 보수적 표현: "n개 매체 보도"·"확산" 같은 확정 표현 금지 (참고 묶음 추정치).
        label = (f"유사 주제 기사 {related_count}건 · 출처 {source_count}곳"
                 if related_count else "단독 신호")
        spreads[row["id"]] = {
            "related_count": related_count,
            "source_count": source_count,
            "label": label,
        }
    return spreads


def _signal_entry(rank: int, row: dict, category_key: str, implication: str,
                  spread: dict, score: dict | None = None) -> dict:
    topics = _parse_topics(row)
    return {
        "rank": rank,
        "article_id": row["id"],
        "title": row["title"],
        "source": row.get("source") or "출처 미상",
        "topic": topics[0] if topics else None,
        "category": category_key,
        "category_label": insight.CATEGORY_PHRASE.get(category_key, "건설산업 일반"),
        "final_score": row.get("final_score"),
        "score_band": score_band(row.get("final_score")),
        "score_components": _score_components(score),
        "alert_grade": row.get("alert_grade"),
        "action_label": ACTION_LABEL_BY_GRADE.get(row.get("alert_grade"), "모니터링"),
        "confidence": row.get("confidence"),
        "opportunity_or_risk": row.get("opportunity_or_risk") or "관찰",
        "implication": implication,
        "spread": spread,
        "url": row.get("url"),
    }


def _diverse_top(rows: list[dict], categories: dict[str, str], limit: int) -> list[dict]:
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
    picked.sort(key=lambda r: (-(r["final_score"]), r["id"]))
    return picked


def _compose_one_liner(signal_rows: list[dict], categories: dict[str, str],
                       immediate_count: int, top_theme: str | None) -> str:
    """저장된 opportunity_or_risk 분류를 종합해 1~2문장 한 줄 시그널을 조립한다.

    제목을 이어붙이지 않는다 — 카테고리별 표현 사전으로만 문장을 만든다.
    """
    if not signal_rows:
        return "오늘 감지된 신호가 없습니다. Run Sensing을 실행해 mock 신호를 수집하세요."

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

    if opp and risk:
        a = SUBJECT_BY_CATEGORY.get(opp_cat, SUBJECT_BY_CATEGORY["general"])
        b = SUBJECT_BY_CATEGORY.get(risk_cat, SUBJECT_BY_CATEGORY["general"])
        return (
            f"{a}{_josa(a, '과', '와')} {b}{_josa(b, '이', '가')} 동시에 부각되며, "
            f"{OPP_ASPECT_BY_CATEGORY.get(opp_cat, '신규 사업')} 기회와 "
            f"{RISK_ASPECT_BY_CATEGORY.get(risk_cat, '운영')} 리스크가 함께 감지됩니다."
        )
    if opp:
        a = SUBJECT_BY_CATEGORY.get(opp_cat, SUBJECT_BY_CATEGORY["general"])
        return (
            f"{a} 중심의 신호가 우세하며, "
            f"{OPP_ASPECT_BY_CATEGORY.get(opp_cat, '신규 사업')} 기회가 부각됩니다. "
            f"뚜렷한 리스크 신호는 제한적입니다."
        )
    if risk:
        b = SUBJECT_BY_CATEGORY.get(risk_cat, SUBJECT_BY_CATEGORY["general"])
        return (
            f"{b} 관련 신호가 두드러져 "
            f"{RISK_ASPECT_BY_CATEGORY.get(risk_cat, '운영')} 리스크 점검이 필요합니다. "
            f"뚜렷한 기회 신호는 제한적입니다."
        )
    theme = top_theme or "건설·에너지"
    return (f"즉시 공유가 필요한 신호 없이 {theme} 중심의 관찰 신호만 감지되었습니다. "
            f"즉시 알림 후보는 {immediate_count}건입니다.")


def _data_warning(news_mode: str, fallback_used: bool) -> str:
    """뉴스 수집 모드에 맞는 한 줄 데이터 출처 고지 (시장지표는 P0-C1까지 항상 미연동)."""
    if news_mode == "live":
        return "뉴스: 공개 RSS 수집 · 시장지표: 미연동"
    if fallback_used:
        return "뉴스: live 수집 실패로 데모(mock) 데이터 대체 · 시장지표: 미연동"
    return "뉴스: 데모(mock) 데이터 · 시장지표: 미연동"


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
    implications = {
        rid: ((d or {}).get("insight") or {}).get("hdec_implication") or ""
        for rid, d in details.items()
    }
    # 점수 구성요소(9항목)는 detail의 score row에만 있다 — 표시용으로 묶어둔다.
    scores_by_id = {rid: ((d or {}).get("score") or {}) for rid, d in details.items()}

    spreads = _build_spreads(scored)

    # 즉시 알림 후보 우선, 비면 상위 신호로 보충 (entry의 alert_grade가 실제 등급).
    # 후보가 표시 한도보다 많을 때만 카테고리 다양성 선택이 동작한다 —
    # 등급 판정 자체는 바꾸지 않고, 어떤 후보를 보여줄지만 고른다.
    instant_pool = [r for r in scored
                    if r.get("alert_grade") == scoring.GRADE_INSTANT]
    instant_rows = _diverse_top(instant_pool, categories, TOP_IMMEDIATE)
    if not instant_rows:
        instant_rows = _diverse_top(signal_rows, categories, TOP_IMMEDIATE)
    top_immediate = [
        _signal_entry(i, r, categories[r["id"]], implications[r["id"]],
                      spreads[r["id"]], scores_by_id.get(r["id"]))
        for i, r in enumerate(instant_rows, start=1)
    ]

    top_issues = [
        _signal_entry(i, r, categories[r["id"]], implications[r["id"]],
                      spreads[r["id"]], scores_by_id.get(r["id"]))
        for i, r in enumerate(
            _diverse_top(signal_rows, categories, TOP_ISSUES), start=1)
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

    top_theme = theme_rankings[0]["theme"] if theme_rankings else None
    one_liner = _compose_one_liner(signal_rows, categories, immediate_count, top_theme)

    # macro snapshot + 데이터 출처 provenance (P0-B6 — mock을 live로 오인하지 않게)
    macro = macro_snapshot.get_macro_snapshot(config.APP_MODE)

    # 뉴스 출처 모드는 저장된 기사 signal_origin에서 파생한다 (DB가 단일 진실).
    # provenance가 주어지면 fallback 여부 등 런타임 상태를 추가로 반영한다.
    news_mode = _derive_news_mode(rows)
    prov = news_provenance or {}
    news_fallback_used = bool(prov.get("fallback_used"))
    news_source = prov.get("news_source") or (
        "live_rss" if news_mode == "live" else "mock")

    now = datetime.now(KST)
    return {
        "header": HEADER,
        "mode": config.APP_MODE,
        "news_data_mode": news_mode,
        "news_source": news_source,
        "news_fallback_used": news_fallback_used,
        "macro_data_mode": macro["macro_data_mode"],
        "macro_source": macro.get("source"),
        "macro_updated_at": macro.get("updated_at"),
        "macro_is_stale": macro.get("is_stale", True),
        "data_warning": _data_warning(news_mode, news_fallback_used),
        "date_kst": now.strftime("%Y-%m-%d"),
        "generated_at": now.isoformat(timespec="seconds"),
        "total_articles": len(rows),
        "total_signals": len(signal_rows),
        "immediate_count": immediate_count,
        "daily_count": daily_count,
        "weekly_count": weekly_count,
        "excluded_count": excluded_count,
        "status_board": [
            {"key": "detected", "label": "오늘 감지 신호", "value": len(rows)},
            {"key": "immediate", "label": scoring.GRADE_INSTANT, "value": immediate_count},
            {"key": "daily", "label": scoring.GRADE_DAILY, "value": daily_count},
            {"key": "weekly", "label": scoring.GRADE_WEEKLY, "value": weekly_count},
            {"key": "excluded", "label": "제외/참고", "value": excluded_count},
        ],
        "executive_one_liner": one_liner,
        "top_immediate_signals": top_immediate,
        "top_new_issues": top_issues,
        "theme_rankings": theme_rankings,
        "category_counts": category_counts,
        "macro_snapshot": macro,
        "spread_method": SPREAD_METHOD,
        "spread_note": SPREAD_NOTE,
        "theme_strength_note": THEME_STRENGTH_NOTE,
        "operator_note": OPERATOR_NOTE,
        "pipeline_counts": pipeline_counts,
    }
