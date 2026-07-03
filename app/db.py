"""DB 도메인 — SQLite 연결, schema 초기화, 공용 CRUD 헬퍼.

이 저장소에서 sqlite3를 import하는 유일한 파일이다 (CLAUDE.md §4).
비즈니스 로직(점수 계산, insight 생성, 발송 판단)은 여기서 하지 않는다.
다른 도메인은 반드시 이 모듈의 헬퍼를 통해서만 DB에 접근한다.
"""

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from app import config

KST = timezone(timedelta(hours=9))

ARTICLE_COLUMNS = [
    "id", "title", "normalized_title", "source", "published_at", "collected_at",
    "url", "url_hash", "snippet", "topic_candidates", "signal_origin",
    "source_metadata_json", "status",
]

SCORE_COLUMNS = [
    "id", "article_id", "hdec_relevance", "executive_importance",
    "business_opportunity", "risk_potential", "urgency", "source_reliability",
    "trend_repeat", "competitor_relevance", "macro_impact", "rule_bonus",
    "rule_penalty", "final_score", "alert_grade", "confidence",
    "scoring_reason", "evidence_basis", "why_not_higher", "why_not_lower",
    "model_name", "created_at",
]

INSIGHT_COLUMNS = [
    "id", "article_id", "summary_3lines", "hdec_implication", "affected_units",
    "opportunity_or_risk", "executive_checkpoints", "recommended_action",
    "digest_message", "created_at",
]

FEEDBACK_COLUMNS = [
    "id", "article_id", "feedback_type", "feedback_value", "operator_id",
    "created_at", "applied_to_rules",
]

NOTIFICATION_COLUMNS = [
    "id", "article_id", "channel", "alert_grade", "message_preview",
    "send_status", "error_message", "sent_at",
]


def now_iso() -> str:
    """행 타임스탬프용 KST ISO 문자열."""
    return datetime.now(KST).isoformat(timespec="seconds")


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(seed_topics: bool = True) -> None:
    """schema.sql 적용 + keyword_rules seed. 몇 번을 호출해도 안전(idempotent)."""
    schema_sql = config.SCHEMA_PATH.read_text(encoding="utf-8")
    with get_connection() as conn:
        conn.executescript(schema_sql)
        if seed_topics:
            _seed_keyword_rules(conn)


def _seed_keyword_rules(conn: sqlite3.Connection) -> None:
    """data/topics.json의 queries를 keyword_rules에 seed (P0-A: 읽기 전용 테이블).

    이미 seed된 행은 INSERT OR IGNORE로 건너뛴다. 파일이 없으면 조용히 생략한다.
    """
    topics_path = config.DATA_DIR / "topics.json"
    if not topics_path.exists():
        return
    queries = json.loads(topics_path.read_text(encoding="utf-8")).get("queries", [])
    ts = now_iso()
    for i, query in enumerate(queries, start=1):
        conn.execute(
            "INSERT OR IGNORE INTO keyword_rules "
            "(id, topic_id, topic_name, keyword, weight, enabled, exclude, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 1.0, 1, 0, ?, ?)",
            (f"kw_{i:03d}", f"T{i:03d}", query, query, ts, ts),
        )


def _rows_to_dicts(rows) -> list[dict]:
    return [dict(row) for row in rows]


def _insert(conn: sqlite3.Connection, table: str, columns: list[str], row: dict,
            mode: str = "INSERT") -> bool:
    placeholders = ", ".join(["?"] * len(columns))
    sql = (
        f"{mode} INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
    )
    cur = conn.execute(sql, tuple(row.get(col) for col in columns))
    return cur.rowcount > 0


# ---------- articles ----------

def insert_article(article: dict) -> bool:
    """url_hash UNIQUE 기준 INSERT OR IGNORE. 새로 저장됐으면 True."""
    with get_connection() as conn:
        return _insert(conn, "articles", ARTICLE_COLUMNS, article, "INSERT OR IGNORE")


def get_existing_normalized_titles() -> set:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT normalized_title FROM articles WHERE normalized_title IS NOT NULL"
        ).fetchall()
    return {row["normalized_title"] for row in rows}


def get_existing_title_sources() -> set[tuple[str, str]]:
    """cross-source 후보 보존용 (normalized_title, normalized source) 집합."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT normalized_title, source FROM articles "
            "WHERE normalized_title IS NOT NULL"
        ).fetchall()
    return {
        (row["normalized_title"], (row["source"] or "").strip().casefold())
        for row in rows
    }


def fetch_all_articles() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM articles ORDER BY published_at DESC, id"
        ).fetchall()
    return _rows_to_dicts(rows)


def fetch_articles_with_scores(min_score: float = None, alert_grade: str = None) -> list[dict]:
    """목록 화면용: articles + scores + insights 요약 LEFT JOIN."""
    sql = (
        "SELECT a.*, "
        "s.final_score, s.alert_grade, s.confidence, s.scoring_reason, "
        "s.rule_bonus, s.rule_penalty, "
        "i.affected_units, i.opportunity_or_risk, i.recommended_action "
        "FROM articles a "
        "LEFT JOIN article_scores s ON s.article_id = a.id "
        "LEFT JOIN article_insights i ON i.article_id = a.id "
    )
    where, params = [], []
    if min_score is not None:
        where.append("s.final_score >= ?")
        params.append(min_score)
    if alert_grade is not None:
        where.append("s.alert_grade = ?")
        params.append(alert_grade)
    if where:
        sql += "WHERE " + " AND ".join(where) + " "
    sql += "ORDER BY (s.final_score IS NULL), s.final_score DESC, a.published_at DESC"
    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return _rows_to_dicts(rows)


def fetch_article_detail(article_id: str) -> dict | None:
    """단건 상세: {'article': .., 'score': ..|None, 'insight': ..|None}"""
    with get_connection() as conn:
        article = conn.execute(
            "SELECT * FROM articles WHERE id = ?", (article_id,)
        ).fetchone()
        if article is None:
            return None
        score = conn.execute(
            "SELECT * FROM article_scores WHERE article_id = ?", (article_id,)
        ).fetchone()
        insight = conn.execute(
            "SELECT * FROM article_insights WHERE article_id = ?", (article_id,)
        ).fetchone()
    return {
        "article": dict(article),
        "score": dict(score) if score else None,
        "insight": dict(insight) if insight else None,
    }


# ---------- article_scores / article_insights ----------

def upsert_score(score: dict) -> None:
    with get_connection() as conn:
        _insert(conn, "article_scores", SCORE_COLUMNS, score, "INSERT OR REPLACE")


def upsert_insight(insight: dict) -> None:
    with get_connection() as conn:
        _insert(conn, "article_insights", INSIGHT_COLUMNS, insight, "INSERT OR REPLACE")


# ---------- feedback / notification_logs ----------

def insert_feedback(row: dict) -> None:
    with get_connection() as conn:
        _insert(conn, "feedback", FEEDBACK_COLUMNS, row)


def insert_notification_log(row: dict) -> None:
    with get_connection() as conn:
        _insert(conn, "notification_logs", NOTIFICATION_COLUMNS, row)


def fetch_notification_logs(limit: int = 50) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT n.*, a.title AS article_title "
            "FROM notification_logs n LEFT JOIN articles a ON a.id = n.article_id "
            "ORDER BY n.sent_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return _rows_to_dicts(rows)


def count_notifications(send_status: str = "sent") -> int:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM notification_logs WHERE send_status = ?",
            (send_status,),
        ).fetchone()
    return int(row["c"])


# ---------- keyword_rules (P0-A: 읽기 전용) ----------

def fetch_topics() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM keyword_rules ORDER BY id"
        ).fetchall()
    return _rows_to_dicts(rows)


if __name__ == "__main__":
    init_db()
    print(f"DB initialized at {config.DB_PATH}")
