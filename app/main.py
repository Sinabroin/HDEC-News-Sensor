"""API 도메인 — FastAPI 라우팅과 요청/응답 변환만 한다.

도메인 로직은 절대 여기에 inline으로 구현하지 않는다 (CLAUDE.md §4).
/api/sense/run은 collector.run() → scoring.score_all() → insight.generate_all()을
순서대로 호출하는 orchestration이 전부다.
"""

import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app import (
    briefing, collector, config, db, feedback, insight, notification, scoring,
    source_quality,
)

ALERT_GRADE_ALIASES = {
    "instant_candidate": "즉시 알림 후보",
    "daily": "검토 필요",
    "weekly": "추적 필요",
    "excluded": "제외",
}

JSON_TEXT_FIELDS = {
    "topic_candidates", "evidence_basis", "summary_3lines",
    "affected_units", "executive_checkpoints",
}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="HDEC Executive Radar", version="day1-p0a", lifespan=lifespan)

# CORS: 로컬 데모 최소 허용 (rules.md §4)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000", "http://127.0.0.1:8000"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


class SenseRunRequest(BaseModel):
    mode: str | None = None
    limit: int | None = None  # P0-A에서는 사용하지 않음 (mock 30건 고정)


class FeedbackRequest(BaseModel):
    feedback_type: str
    feedback_value: str | None = None
    operator_id: str = "operator"


class NotifyRequest(BaseModel):
    channel: str = "mock"
    operator_id: str = "operator"


class NotifyTestRequest(BaseModel):
    channel: str = "mock"
    message: str = "HDEC Executive Radar test"


def _parse_json_fields(row: dict | None) -> dict | None:
    if row is None:
        return None
    parsed = dict(row)
    for key in JSON_TEXT_FIELDS & parsed.keys():
        value = parsed[key]
        if isinstance(value, str) and value:
            try:
                parsed[key] = json.loads(value)
            except json.JSONDecodeError:
                pass
    return parsed


def _attach_source_quality(row: dict | None) -> dict | None:
    """응답 변환: 저장된 source/title을 출처 품질로 분류해 표시용 라벨을 덧붙인다.

    점수/등급을 바꾸지 않는다 — UI 카드의 출처 품질 칩(신뢰/일반/낮은 신뢰도)용
    파생 필드일 뿐이다. 분류 로직은 source_quality 도메인이 단일 소유한다.
    """
    if not row:
        return row
    q = source_quality.classify(row.get("source"), row.get("title"))
    row["source_quality"] = q["source_quality"]
    row["source_quality_label"] = q["source_quality_label"]
    row["source_type"] = q["source_type"]
    # 임원 표시용 출처 — 집계 호스트(v.daum.net 등)는 'Daum 경유'로 정규화 (P0-C1.11).
    # raw source는 보존한다 (UI가 display_source를 우선 노출).
    row["display_source"] = source_quality.normalize_display_source(row.get("source"))
    return row


@app.get("/")
def index_page():
    index_path = config.TEMPLATES_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="UI가 아직 준비되지 않았다 (D8)")
    return FileResponse(index_path)


@app.post("/api/sense/run")
def sense_run(body: SenseRunRequest | None = None):
    mode = (body.mode if body and body.mode else config.APP_MODE).strip().lower()
    if mode != "mock":
        raise HTTPException(
            status_code=400,
            detail="Day-1 P0-A는 APP_MODE=mock만 지원한다 (rules.md §2)",
        )
    collected = collector.run(mode)
    scored = scoring.score_all()
    insight.generate_all()
    return {
        "collected": collected["collected"],
        "deduplicated": collected["deduplicated"],
        "scored": scored["scored"],
        "alert_candidates": scored["alert_candidates"],
        "mode": mode,
        "fallback_used": collected["fallback_used"],
    }


@app.get("/api/articles")
def list_articles(min_score: float | None = None, alert_grade: str | None = None):
    grade = ALERT_GRADE_ALIASES.get(alert_grade, alert_grade) if alert_grade else None
    rows = [_attach_source_quality(_parse_json_fields(r))
            for r in db.fetch_articles_with_scores(min_score, grade)]
    candidates = [r for r in rows if r.get("alert_grade") == "즉시 알림 후보"]
    return {
        "articles": rows,
        "notification_logs": db.fetch_notification_logs(),
        "counts": {
            "total": len(rows),
            "alert_candidates": len(candidates),
        },
    }


@app.get("/api/articles/{article_id}")
def article_detail(article_id: str):
    detail = db.fetch_article_detail(article_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"기사를 찾을 수 없음: {article_id}")
    return {
        "article": _attach_source_quality(_parse_json_fields(detail["article"])),
        "score": _parse_json_fields(detail["score"]),
        "insight": _parse_json_fields(detail["insight"]),
    }


@app.post("/api/articles/{article_id}/feedback")
def save_feedback(article_id: str, body: FeedbackRequest):
    if db.fetch_article_detail(article_id) is None:
        raise HTTPException(status_code=404, detail=f"기사를 찾을 수 없음: {article_id}")
    try:
        row = feedback.save(article_id, body.feedback_type, body.feedback_value,
                            body.operator_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"saved": True, "feedback": row}


@app.post("/api/articles/{article_id}/notify")
def notify_article(article_id: str, body: NotifyRequest | None = None):
    body = body or NotifyRequest()
    try:
        row = notification.send(article_id, body.channel, body.operator_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"sent": True, "notification": row}


@app.post("/api/notify/test")
def notify_test(body: NotifyTestRequest | None = None):
    body = body or NotifyTestRequest()
    try:
        row = notification.send_test(body.channel, body.message)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"sent": True, "notification": row}


@app.get("/api/brief")
def get_brief():
    # P0-B2: 현재 DB 상태 기반 executive brief (파생 데이터 — DB 쓰기 없음)
    return briefing.build_brief()


@app.get("/api/settings/topics")
def settings_topics():
    # Day-1: 조회 전용. 수정 endpoint는 만들지 않는다 (rules.md §1).
    return {"topics": db.fetch_topics(), "read_only": True}
