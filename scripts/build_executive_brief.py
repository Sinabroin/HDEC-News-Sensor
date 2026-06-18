"""P0-B2 — Executive Brief 빌더 CLI (공유 briefing 데이터).

기존 P0-A 파이프라인(collector → scoring → insight)을 임시 SQLite DB 위에서
돌린 뒤 app.briefing.build_brief()로 executive brief 구조체를 만든다.

- 네트워크 호출 0건, 비밀값 접근 0건.
- 저장소의 radar.db는 절대 건드리지 않는다 — 매 실행마다 tmp 디렉터리의
  일회용 DB를 새로 만든다 (P0-B1과 동일한 패턴).
- scripts/build_telegram_digest.py가 build_brief_via_mock_pipeline()을 재사용한다.

사용법:
    python3 scripts/build_executive_brief.py --dry-run   # 사람용 텍스트 brief
    python3 scripts/build_executive_brief.py --json      # 기계 검증용 JSON
"""

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
_KST = timezone(timedelta(hours=9))


def _fmt_kst(iso) -> str:
    """ISO 타임스탬프를 KST 벽시계 'YYYY-MM-DD HH:MM'로 (raw +00:00 비노출, P0-D1.5)."""
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return str(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_KST).strftime("%Y-%m-%d %H:%M")

# 프로세스당 1회만 부트스트랩한다 — app.config가 import 시점에 DB_PATH를 읽으므로
# 임시 DB 경로는 app 모듈 import 전에 환경변수로 고정해야 한다.
_RUNTIME = None


def _bootstrap():
    global _RUNTIME
    if _RUNTIME is None:
        tmp = tempfile.TemporaryDirectory(prefix="hdec_brief_")
        os.environ["DB_PATH"] = os.path.join(tmp.name, "brief_mock.db")
        os.environ.setdefault("APP_MODE", "mock")
        if str(REPO_ROOT) not in sys.path:
            sys.path.insert(0, str(REPO_ROOT))
        from app import briefing, collector, db, insight, scoring

        modules = {"briefing": briefing, "collector": collector, "db": db,
                   "insight": insight, "scoring": scoring}
        _RUNTIME = (modules, tmp)
    return _RUNTIME[0]


def build_brief_via_mock_pipeline() -> dict:
    """임시 DB에서 파이프라인을 돌리고 brief 구조체를 반환한다.

    수집 경로는 collector가 NEWS_MODE에 따라 고른다 (mock 기본 / live 공개 RSS).
    출처/대체 여부(provenance)를 build_brief에 넘겨 live·mock·fallback을 정직하게 표기한다.
    """
    m = _bootstrap()
    m["db"].init_db()
    collect_stats = m["collector"].run("mock")
    score_stats = m["scoring"].score_all()
    m["insight"].generate_all()
    return m["briefing"].build_brief(
        pipeline_counts={
            "collected": collect_stats["collected"],
            "deduplicated": collect_stats["deduplicated"],
            "inserted": collect_stats["inserted"],
            "scored": score_stats["scored"],
            "alert_candidates": score_stats["alert_candidates"],
        },
        news_provenance={
            "news_source": collect_stats.get("news_source"),
            "fallback_used": collect_stats.get("fallback_used"),
            "attempted_mode": collect_stats.get("attempted_mode"),
            "source_filtered": collect_stats.get("source_filtered"),
            "google_query_audit": collect_stats.get("google_query_audit"),
        },
    )


def _fmt_score(value) -> str:
    return "-" if value is None else f"{value:.2f}"


def _fmt5(value) -> str:
    """중요도 표시 — 분모를 명시한 X.X/5.0 형식의 분자."""
    return "-" if value is None else f"{value:.1f}"


def format_brief_text(brief: dict) -> str:
    """--dry-run용 사람 읽기 좋은 한국어 brief 텍스트."""
    news_mode = brief.get("news_data_mode", "mock")
    source_line = "자동 수집" if news_mode == "live" else "데모(mock) 데이터"
    # 시장지표는 macro_data_mode=live일 때만 출처를 표기한다 — 헤더가 항상 '미연동'이면
    # 아래 [Macro Snapshot] 블록의 live 수치와 모순된다 (P0-C2 정직성).
    _macro = brief.get("macro_snapshot") or {}
    macro_tag = (f"시장지표 {_macro.get('source') or 'live'}"
                 if _macro.get("macro_data_mode") == "live" and _macro.get("values")
                 else "시장지표 미연동")
    lines = [
        f"== {brief['header']} — Executive Brief ==",
        f"{brief['date_kst']} (KST) · 뉴스 {source_line} · {macro_tag}",
        "",
        "[데일리 현황판]",
        " · ".join(f"{b['label']} {b['value']}" for b in brief["status_board"]),
        "",
        "[오늘의 Executive Signal]",
        brief["executive_one_liner"],
        "",
        f"[즉시 알림 후보 Top {len(brief['top_immediate_signals'])}]",
    ]
    for s in brief["top_immediate_signals"]:
        lines.append(f"{s['rank']}. {s['title']}")
        lines.append(f"   {s['source']} · {s['category_label']}"
                     f" · 중요도 {_fmt5(s['final_score'])}/5.0")
        comps = s.get("score_components") or []
        if comps:
            lines.append("   " + " · ".join(
                f"{c['label']} {_fmt5(c['value'])}" for c in comps))
        if s["implication"]:
            lines.append(f"   → {s['implication']}")
        lines.append(f"   ↳ {s['spread']['label']}")
    lines += ["", f"[신규 이슈 Top {len(brief['top_new_issues'])}]"]
    for s in brief["top_new_issues"]:
        lines.append(f"{s['rank']}. {s['title']}"
                     f" — {s['category_label']} · 중요도 {_fmt5(s['final_score'])}/5.0"
                     f" · {s['spread']['label']}")
    lines += ["", "[주요 테마]"]
    for t in brief["theme_rankings"]:
        lines.append(f"{t['rank']}. {t['theme']} — 관련 기사 {t['count']}건"
                     f" · 테마 비중 {t.get('relative_strength', '-')}")
    if brief.get("theme_strength_note"):
        lines.append(f"   ※ {brief['theme_strength_note']}")
    lines += ["", "[카테고리 요약]"]
    lines.append(" · ".join(f"{c['label']} {c['count']}"
                            for c in brief["category_counts"]))
    # Macro Snapshot — live가 아닌 한 수치를 표시하지 않는다 (P0-B6 데이터 정직성).
    macro = brief.get("macro_snapshot") or {}
    macro_mode = macro.get("macro_data_mode") or brief.get("macro_data_mode")
    lines += ["", "[Macro Snapshot]"]
    if macro_mode == "live" and macro.get("values"):
        lines.append(f"출처 {macro.get('source')} · 참고시각 "
                     f"{_fmt_kst(macro.get('updated_at'))} (KST 기준)")
        lines.append(" · ".join(
            f"{v['label']} {v['value']}{v.get('unit', '')}"
            for v in macro["values"]))
    else:
        labels = " · ".join(v["label"] for v in macro.get("values") or []) or "지표 없음"
        lines.append("시장지표 미연동 — 현재 시장값이 아니므로 수치를 표시하지 "
                     f"않습니다: {labels}")
    lines += ["", f"※ {brief['operator_note']}", f"※ {brief.get('data_warning', '')}",
              f"※ {brief.get('spread_note', '')}"]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="HDEC Executive Radar — executive brief 빌더 (발송 없음)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true",
                       help="사람용 brief 텍스트를 출력하고 요약을 stderr에 남긴다")
    group.add_argument("--json", action="store_true",
                       help="기계 검증용 JSON을 출력한다")
    args = parser.parse_args(argv)

    brief = build_brief_via_mock_pipeline()

    if args.json:
        print(json.dumps(brief, ensure_ascii=False, indent=2))
        return 0

    print(format_brief_text(brief))
    if args.dry_run:
        print(f"[dry-run] immediate={len(brief['top_immediate_signals'])} "
              f"issues={len(brief['top_new_issues'])} "
              f"themes={len(brief['theme_rankings'])} "
              f"categories={len(brief['category_counts'])} (발송 없음)",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
