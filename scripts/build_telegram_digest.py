"""P0-B1 — Telegram mock daily digest 빌더.

기존 P0-A 도메인(collector → scoring → insight)을 임시 SQLite DB 위에서 그대로
재사용해 임원용 한국어 다이제스트 메시지를 만든다.

- 네트워크 호출 0건, 비밀값 접근 0건 (Telegram 발송은 send_telegram.py 소관).
- 저장소의 radar.db는 절대 건드리지 않는다 — 매 실행마다 tmp 디렉터리의
  일회용 DB에 mock 파이프라인을 새로 돌린다.
- APP_MODE=mock 고정: collector.run("mock")만 호출한다.

사용법:
    python3 scripts/build_telegram_digest.py            # 메시지 출력
    python3 scripts/build_telegram_digest.py --dry-run  # 메시지 + 요약(stderr)
    python3 scripts/build_telegram_digest.py --json     # 기계 검증용 JSON
"""

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
KST = timezone(timedelta(hours=9))

HEADER = "HDEC Executive Radar"
TOP_N = 3
# 메시지 길이 예산. send_telegram.py의 MAX_MESSAGE_LEN보다 항상 작거나 같아야 한다
# (verify_telegram_digest.py가 이 관계를 검사한다).
MESSAGE_BUDGET = 3000
TITLE_MAX = 70
REASON_MAX = 90

# 프로세스당 1회만 부트스트랩한다 — app.config가 import 시점에 DB_PATH를 읽으므로
# 임시 DB 경로는 app 모듈 import 전에 환경변수로 고정해야 한다.
_RUNTIME = None


def _bootstrap():
    global _RUNTIME
    if _RUNTIME is None:
        tmp = tempfile.TemporaryDirectory(prefix="hdec_digest_")
        os.environ["DB_PATH"] = os.path.join(tmp.name, "digest_mock.db")
        os.environ.setdefault("APP_MODE", "mock")
        if str(REPO_ROOT) not in sys.path:
            sys.path.insert(0, str(REPO_ROOT))
        from app import collector, db, insight, scoring

        modules = {"collector": collector, "db": db,
                   "insight": insight, "scoring": scoring}
        _RUNTIME = (modules, tmp)
    return _RUNTIME[0]


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _select_top_rows(rows: list[dict], instant_grade: str) -> list[dict]:
    """즉시 알림 후보(점수순) 우선, 부족하면 나머지에서 점수순으로 채운다."""
    scored = [r for r in rows if r.get("final_score") is not None]
    instant = sorted((r for r in scored if r.get("alert_grade") == instant_grade),
                     key=lambda r: r["final_score"], reverse=True)
    rest = sorted((r for r in scored if r.get("alert_grade") != instant_grade),
                  key=lambda r: r["final_score"], reverse=True)
    return (instant + rest)[:TOP_N]


def _signal_entry(rank: int, row: dict, detail: dict | None) -> dict:
    insight_row = (detail or {}).get("insight") or {}
    reason = (insight_row.get("hdec_implication") or "").strip()
    try:
        topics = json.loads(row.get("topic_candidates") or "[]")
    except ValueError:
        topics = []
    return {
        "rank": rank,
        "article_id": row["id"],
        "title": row["title"],
        "source": row.get("source") or "출처 미상",
        "topic": topics[0] if topics else None,
        "final_score": row.get("final_score"),
        "alert_grade": row.get("alert_grade"),
        "confidence": row.get("confidence"),
        "reason": reason,
        "url": row.get("url"),
    }


def build_digest_data() -> dict:
    """임시 DB에서 mock 파이프라인을 돌리고 다이제스트 구조체를 반환한다."""
    m = _bootstrap()
    m["db"].init_db()
    collect_stats = m["collector"].run("mock")
    score_stats = m["scoring"].score_all()
    m["insight"].generate_all()

    rows = m["db"].fetch_articles_with_scores()
    top_rows = _select_top_rows(rows, m["scoring"].GRADE_INSTANT)
    signals = [
        _signal_entry(rank, row, m["db"].fetch_article_detail(row["id"]))
        for rank, row in enumerate(top_rows, start=1)
    ]

    now = datetime.now(KST)
    return {
        "header": HEADER,
        "mode": "mock",
        "date_kst": now.strftime("%Y-%m-%d"),
        "generated_at": now.isoformat(timespec="seconds"),
        "top_signals": signals,
        "counts": {
            "collected": collect_stats["collected"],
            "deduplicated": collect_stats["deduplicated"],
            "inserted": collect_stats["inserted"],
            "scored": score_stats["scored"],
            "alert_candidates": score_stats["alert_candidates"],
        },
    }


def format_digest_message(data: dict) -> str:
    """다이제스트 구조체를 Telegram용 한국어 plain text로 변환한다."""
    counts = data["counts"]
    lines = [
        f"📡 {data['header']} — 일일 다이제스트",
        f"{data['date_kst']} (KST) · 모드: {data['mode']}",
        "",
        f"오늘의 Top {len(data['top_signals'])} 시그널",
    ]
    for signal in data["top_signals"]:
        meta = [signal["source"]]
        if signal["topic"]:
            meta.append(signal["topic"])
        if signal["final_score"] is not None:
            meta.append(f"{signal['final_score']:.2f}점")
        if signal["alert_grade"]:
            meta.append(f"[{signal['alert_grade']}]")
        if signal["confidence"] is not None:
            meta.append(f"신뢰도 {signal['confidence']:.2f}")
        lines.append("")
        lines.append(f"{signal['rank']}. {_clip(signal['title'], TITLE_MAX)}")
        lines.append("   " + " · ".join(meta))
        if signal["reason"]:
            lines.append(f"   → {_clip(signal['reason'], REASON_MAX)}")
    lines += [
        "",
        (f"수집 {counts['collected']} · 신규 저장 {counts['inserted']}"
         f" · 즉시 알림 후보 {counts['alert_candidates']}"),
        "※ mock 모드 다이제스트 — 외부 뉴스 API 호출 없이 로컬 mock 데이터로 생성됨.",
    ]
    message = "\n".join(lines)
    if len(message) > MESSAGE_BUDGET:
        message = message[: MESSAGE_BUDGET - 1] + "…"
    return message


def build_digest_message() -> str:
    """send_telegram.py가 사용하는 단일 진입점."""
    return format_digest_message(build_digest_data())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="HDEC Executive Radar — Telegram mock daily digest 빌더 (발송 없음)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true",
                       help="메시지를 stdout에 출력하고 요약을 stderr에 남긴다")
    group.add_argument("--json", action="store_true",
                       help="기계 검증용 JSON을 출력한다")
    args = parser.parse_args(argv)

    data = build_digest_data()
    message = format_digest_message(data)

    if args.json:
        data["message_chars"] = len(message)
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0

    print(message)
    if args.dry_run:
        print(f"[dry-run] signals={len(data['top_signals'])} "
              f"chars={len(message)} budget={MESSAGE_BUDGET} (발송 없음)",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
