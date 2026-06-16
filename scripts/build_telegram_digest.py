"""P0-B1/P0-B2 — Telegram mock daily digest 빌더.

P0-B2부터 다이제스트 데이터는 scripts/build_executive_brief.py(공유 briefing
레이어)에서 가져오고, 이 파일은 Telegram용 한국어 텍스트 조립만 담당한다.

- 네트워크 호출 0건, 비밀값 접근 0건 (Telegram 발송은 send_telegram.py 소관).
- 저장소의 radar.db는 절대 건드리지 않는다 — brief 빌더가 tmp 디렉터리의
  일회용 DB에 mock 파이프라인을 새로 돌린다.
- APP_MODE=mock 고정.

사용법:
    python3 scripts/build_telegram_digest.py            # 메시지 출력
    python3 scripts/build_telegram_digest.py --dry-run  # 메시지 + 요약(stderr)
    python3 scripts/build_telegram_digest.py --json     # 기계 검증용 JSON
"""

import argparse
import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from build_executive_brief import build_brief_via_mock_pipeline  # noqa: E402

HEADER = "HDEC Executive Radar"
# 메시지 길이 예산. send_telegram.py의 MAX_MESSAGE_LEN보다 항상 작거나 같아야 한다
# (verify_telegram_digest.py / verify_executive_brief.py가 이 관계를 검사한다).
MESSAGE_BUDGET = 3000
TITLE_MAX = 70
REASON_MAX = 90
DIGEST_THEMES_MAX = 5
DIGEST_CATEGORIES_MAX = 5


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _digest_signal(entry: dict) -> dict:
    """brief의 시그널 entry를 다이제스트 JSON 호환 형태로 변환한다."""
    return {
        "rank": entry["rank"],
        "article_id": entry["article_id"],
        "title": entry["title"],
        "source": entry["source"],
        "topic": entry.get("topic"),
        "category_label": entry.get("category_label"),
        "radar_section": entry.get("radar_section"),
        "final_score": entry.get("final_score"),
        "score_band": entry.get("score_band"),
        "alert_grade": entry.get("alert_grade"),
        "action_label": entry.get("action_label"),
        "confidence": entry.get("confidence"),
        "reason": entry.get("one_line_reason") or entry.get("implication") or "",
        "risk_priority_score": entry.get("risk_priority_score"),
        "risk_radar_label": entry.get("risk_radar_label"),
        "spread_label": (entry.get("spread") or {}).get("label", "단독 신호"),
        "url": entry.get("url"),
    }


def build_digest_data() -> dict:
    """공유 brief 레이어에서 다이제스트 구조체를 만든다 (P0-C1.9: AI-first)."""
    brief = build_brief_via_mock_pipeline()
    # AI 레이더 신호를 우선 노출하고, 없으면 즉시 알림 후보로 대체한다 (항상 1~3건).
    ai = brief.get("ai_radar_signals") or []
    base = ai or brief.get("top_immediate_signals") or []
    signals = [_digest_signal(e) for e in base[:3]]
    risk = [_digest_signal(e) for e in (brief.get("risk_regulation_signals") or [])[:2]]
    return {
        "header": HEADER,
        "mode": brief["mode"],
        "news_data_mode": brief.get("news_data_mode", "mock"),
        "macro_data_mode": brief.get("macro_data_mode", "unavailable"),
        "data_warning": brief.get("data_warning", ""),
        "date_kst": brief["date_kst"],
        "generated_at": brief["generated_at"],
        "executive_one_liner": brief["executive_one_liner"],
        "status_board": brief["status_board"],
        "top_signals": signals,
        "ai_first": bool(ai),
        "risk_signals": risk,
        "theme_rankings": brief["theme_rankings"][:DIGEST_THEMES_MAX],
        "category_counts": brief["category_counts"],
        "macro_snapshot": brief["macro_snapshot"],
        "operator_note": brief["operator_note"],
        "counts": brief["pipeline_counts"] or {},
    }


def format_digest_message(data: dict) -> str:
    """다이제스트 구조체를 Telegram용 한국어 plain text로 변환한다.

    P0-C1: 임원용으로 간결하게 — 제목/날짜/모드, 한 줄 시그널, 현황판 한 줄,
    Top 3 짧은 제목, 시장지표 미연동 한 줄, 추정 안내 한 줄. 원문 URL·장문 점수
    설명·반복 면책은 넣지 않고 상세는 '오늘 브리프 보기' 버튼(리포트)으로 넘긴다.
    """
    news_mode = data.get("news_data_mode", "mock")
    source_line = "공개 RSS 수집" if news_mode == "live" else "mock 데이터 기반"
    # 헤더에서 '시장지표 미연동' 노이즈를 빼고(하단 Macro 섹션에서만 정직 표기), 출처만 표기.
    lines = [
        f"📡 {data['header']} — Executive Daily Brief",
        f"{data['date_kst']} (KST) · 뉴스 {source_line}",
        "",
        "[오늘의 Executive Signal]",
        data["executive_one_liner"],
        "",
        "[데일리 현황판]",
        " · ".join(f"{b['label']} {b['value']}" for b in data["status_board"]),
    ]

    # AI-first: AI 레이더 신호를 가장 먼저 보여준다 (거시경제보다 앞).
    signals = data["top_signals"]
    has_instant = any(s.get("alert_grade") == "즉시 알림 후보" for s in signals)
    section = "AI 레이더" if data.get("ai_first") else "주요 신호"
    lines += ["", f"[{section} Top {len(signals)}]"]
    for s in signals:
        lines.append(f"{s['rank']}. {_clip(s['title'], TITLE_MAX)}")
        meta = []
        if s.get("final_score") is not None:
            meta.append(f"중요도 {s['final_score']:.1f}/5")
        if s.get("action_label"):
            meta.append(s["action_label"])
        if meta:
            lines.append("   " + " · ".join(meta))

    # live 수집일에 즉시 확인급(신뢰 출처 4.5+) 신호가 없으면 — 약한 출처(블로그·재전송 등)를
    # 임원 알림으로 띄우지 않고, 주간 모니터링 후보 중심임을 명확히 한다 (P0-C1.6).
    # mock 데모는 즉시 후보가 항상 있어 이 줄이 추가되지 않는다 (다이제스트 길이 불변).
    if news_mode == "live" and not has_instant:
        lines += ["", "오늘은 즉시 확인급 신호 없음 · 주간 모니터링 후보 중심"]

    # 리스크·규제 한 줄 — 있으면 거시경제보다 먼저, 중대재해·규제를 분명히 가리킨다.
    risk = data.get("risk_signals") or []
    if risk:
        top = risk[0]
        chip = top.get("risk_radar_label") or "리스크"
        lines += ["", f"[리스크·규제] {chip}: {_clip(top['title'], TITLE_MAX)}"]

    themes = data["theme_rankings"][:3]
    if themes:
        lines += ["", "[주요 테마] " + " · ".join(
            f"{t['theme']} {t.get('count', '')}건".strip() for t in themes)]

    categories = data["category_counts"]
    if categories:
        shown = categories[:DIGEST_CATEGORIES_MAX]
        rest = len(categories) - len(shown)
        summary = " · ".join(f"{c['label']} {c['count']}" for c in shown)
        if rest > 0:
            summary += f" · 외 {rest}개 분류"
        lines += ["", "[카테고리 요약] " + summary]

    # Macro Snapshot — AI/리스크 뒤에 둔다. live가 아닌 한 수치를 넣지 않는다 (P0-B6).
    # mock 고정값을 시세처럼 보낼 수 없다: 미연동 상태만 알리고 상세는 리포트로.
    macro = data.get("macro_snapshot") or {}
    macro_mode = macro.get("macro_data_mode") or data.get("macro_data_mode")
    if macro_mode == "live" and macro.get("values"):
        lines += ["", f"[Macro Snapshot — {macro.get('source')} · 기준 {macro.get('updated_at')}]"]
        lines.append(" · ".join(
            f"{v['label']} {v['value']}{v.get('unit', '')}"
            for v in macro["values"]))
    else:
        lines += ["", "[Macro Snapshot]",
                  "시장지표 미연동 · 거시경제 신호는 리포트에서 확인"]

    lines += ["", "※ 원문·점수·카테고리별 근거 기사는 '오늘 브리프 보기' 리포트에서 확인"]
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
