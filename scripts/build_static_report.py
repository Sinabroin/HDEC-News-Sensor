"""P0-B5 — 정적 Executive Daily Brief HTML 리포트 빌더.

공유 briefing 레이어(scripts/build_executive_brief.py)의 brief 구조체를
단일 정적 HTML 페이지로 렌더링한다. Telegram 다이제스트의 "오늘 브리프 보기"
버튼이 게시된 이 페이지(docs/daily/latest.html)로 연결된다.

- 네트워크 호출 0건, 비밀값 접근 0건, 외부 CDN/스크립트/폰트 0건 (완전 standalone).
- 저장소의 radar.db는 절대 건드리지 않는다 — brief 빌더가 임시 DB를 쓴다.
- 본문 전문을 싣지 않는다 — 제목/카테고리/점수/시사점 등 파생 요약만 렌더링한다.
- 공개 호스팅(GitHub Pages 등)에는 mock/데모 데이터만 게시한다 (docs/daily/README.md).

사용법:
    python3 scripts/build_static_report.py --dry-run                        # 요약 출력 (쓰기 없음)
    python3 scripts/build_static_report.py --json                           # 기계 검증용 메타데이터
    python3 scripts/build_static_report.py --output docs/daily/latest.html  # HTML 파일 생성
"""

import argparse
import json
import sys
from html import escape
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from build_executive_brief import build_brief_via_mock_pipeline  # noqa: E402

REPORT_TITLE = "HDEC Executive Radar — Executive Daily Brief"
DEFAULT_OUTPUT = "docs/daily/latest.html"

KIND_CLASS = {
    "기회": "kind-opp",
    "리스크": "kind-risk",
    "기회+리스크": "kind-both",
    "관찰": "kind-watch",
}

DIRECTION_ARROW = {"up": ("▲", "dir-up"), "down": ("▼", "dir-down")}

# 외부 리소스 0건 원칙: 시스템 폰트 스택만 사용하고 <script>/<link>를 일절 쓰지 않는다.
_CSS = """
:root{--navy:#0e2a47;--navy2:#163b61;--green:#19b27b;--green-dark:#0e7d57;
--red:#d64545;--amber:#e8a23d;--ink:#22313f;--muted:#64748b;
--bg:#f2f5f8;--card:#ffffff;--line:#e2e8f0;}
*{box-sizing:border-box;margin:0;padding:0;}
body{background:var(--bg);color:var(--ink);line-height:1.55;-webkit-text-size-adjust:100%;
font-family:"Apple SD Gothic Neo","Malgun Gothic","Noto Sans KR","Segoe UI",system-ui,sans-serif;}
.page{max-width:860px;margin:0 auto;padding:14px 14px 36px;}
.hero{background:linear-gradient(135deg,var(--navy) 0%,var(--navy2) 62%,var(--green-dark) 135%);
color:#fff;border-radius:14px;padding:20px 20px 18px;margin-bottom:14px;}
.hero-top{display:flex;align-items:center;justify-content:space-between;gap:8px;}
.brand{font-size:13px;letter-spacing:.4px;opacity:.92;font-weight:600;}
.mode-chip{font-size:11px;border:1px solid rgba(255,255,255,.45);border-radius:999px;
padding:2px 10px;text-transform:uppercase;letter-spacing:.8px;}
.hero h1{font-size:24px;margin-top:10px;letter-spacing:-.3px;}
.hero-sub{font-size:13px;opacity:.85;margin-top:4px;}
section{margin-bottom:14px;}
h2{font-size:14px;color:var(--navy);letter-spacing:.2px;margin-bottom:8px;}
h2 .tag{font-size:11px;color:var(--muted);font-weight:500;margin-left:6px;}
.board{display:grid;grid-template-columns:repeat(auto-fit,minmax(96px,1fr));gap:8px;}
.metric{background:var(--card);border:1px solid var(--line);border-radius:12px;
padding:10px 8px;text-align:center;}
.metric .num{font-size:22px;font-weight:700;color:var(--navy);}
.metric .lbl{font-size:11px;color:var(--muted);margin-top:2px;}
.metric.immediate{border-color:var(--red);background:#fdf1f1;}
.metric.immediate .num{color:var(--red);}
.oneliner{background:var(--card);border:1px solid var(--line);border-left:5px solid var(--green);
border-radius:12px;padding:13px 16px;}
.oneliner h2{margin-bottom:6px;}
.oneliner p{font-size:15px;font-weight:600;}
.signal-card{background:var(--card);border:1px solid var(--line);border-left:5px solid var(--red);
border-radius:12px;padding:12px 14px;margin-bottom:10px;}
.signal-card.kind-opp{border-left-color:var(--green);}
.signal-card.kind-both{border-left-color:var(--amber);}
.signal-card.kind-watch{border-left-color:var(--muted);}
.signal-head{display:flex;align-items:center;gap:6px;flex-wrap:wrap;}
.rank{display:inline-flex;align-items:center;justify-content:center;width:22px;height:22px;
border-radius:50%;background:var(--navy);color:#fff;font-size:12px;font-weight:700;}
.badge{font-size:11px;border-radius:999px;padding:2px 9px;font-weight:600;}
.badge.grade{background:#fdecec;color:var(--red);border:1px solid #f3c2c2;}
.badge.grade.normal{background:#eef2f7;color:var(--muted);border:1px solid var(--line);}
.badge.kind-opp{background:#e9f7f0;color:var(--green-dark);border:1px solid #bfe6d4;}
.badge.kind-risk{background:#fdecec;color:var(--red);border:1px solid #f3c2c2;}
.badge.kind-both{background:#fdf3e3;color:#a36b14;border:1px solid #eed5a8;}
.badge.kind-watch{background:#eef2f7;color:var(--muted);border:1px solid var(--line);}
.signal-card h3{font-size:15px;margin:8px 0 4px;letter-spacing:-.2px;}
.meta{font-size:12px;color:var(--muted);}
.why,.act{font-size:13px;margin-top:6px;}
.why strong,.act strong{color:var(--navy);font-size:12px;margin-right:6px;}
.spread{font-size:12px;color:var(--muted);margin-top:6px;}
.spread .hint{font-size:11px;}
.extra{background:var(--card);border:1px dashed var(--line);border-radius:12px;
padding:10px 14px;font-size:13px;}
.extra li{list-style:none;padding:3px 0;}
.panel{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px;}
.theme-row{font-size:13px;margin-bottom:9px;}
.theme-row:last-child{margin-bottom:0;}
.theme-name{display:flex;justify-content:space-between;gap:8px;}
.theme-name .cnt{color:var(--muted);font-size:12px;white-space:nowrap;}
.theme-bar{height:6px;border-radius:4px;background:#e8eef4;margin-top:3px;overflow:hidden;}
.theme-bar span{display:block;height:100%;border-radius:4px;
background:linear-gradient(90deg,var(--green),var(--navy2));}
.cat-row{display:flex;justify-content:space-between;gap:8px;font-size:13px;padding:4px 0;
border-bottom:1px dashed var(--line);}
.cat-row:last-child{border-bottom:none;}
.cat-imm{color:var(--red);font-weight:600;font-size:12px;white-space:nowrap;}
.macro-strip{display:flex;flex-wrap:wrap;gap:8px;}
.macro-chip{background:var(--card);border:1px solid var(--line);border-radius:10px;
padding:8px 12px;font-size:12px;min-width:96px;flex:1;}
.macro-chip .v{font-size:15px;font-weight:700;color:var(--navy);display:block;}
.dir-up{color:var(--red);}
.dir-down{color:#2563c9;}
.dir-flat{color:var(--muted);}
.note{font-size:12px;color:#6b5d33;background:#fbf7ec;border:1px solid #eee3c2;
border-radius:12px;padding:10px 14px;}
footer{font-size:11px;color:var(--muted);text-align:center;margin-top:18px;}
@media(min-width:760px){.duo{display:grid;grid-template-columns:1fr 1fr;gap:12px;}
.duo section{margin-bottom:0;}}
"""


def _fmt(value) -> str:
    return "-" if value is None else f"{value:.2f}"


def _watch_action(entry: dict) -> str:
    """권장 워치 액션 문구 — briefing 도메인의 표현 사전을 재사용한다 (표현 전용).

    build_brief_via_mock_pipeline()이 app 모듈을 bootstrap한 뒤에만 호출된다.
    """
    from app import briefing

    cat = entry.get("category") or "general"
    kind = entry.get("opportunity_or_risk") or "관찰"
    opp = briefing.OPP_ASPECT_BY_CATEGORY.get(cat, "신규 사업")
    risk = briefing.RISK_ASPECT_BY_CATEGORY.get(cat, "운영")
    if kind == "기회+리스크":
        return f"{opp} 기회와 {risk} 리스크 양면 점검"
    if kind == "기회":
        return f"{opp} 관점의 후속 신호·발주 일정 확인"
    if kind == "리스크":
        return f"{risk} 영향 점검 및 대응 상태 확인"
    return "후속 신호 관찰 후 관련 부문 참고 공유"


def _render_signal_card(entry: dict) -> str:
    kind = entry.get("opportunity_or_risk") or "관찰"
    kind_class = KIND_CLASS.get(kind, "kind-watch")
    grade = entry.get("alert_grade") or "미채점"
    grade_class = "grade" if grade == "즉시 알림 후보" else "grade normal"
    meta = " · ".join([
        escape(entry.get("source") or "출처 미상"),
        escape(entry.get("category_label") or "건설산업 일반"),
        f"{_fmt(entry.get('final_score'))}점",
        f"신뢰도 {_fmt(entry.get('confidence'))}",
    ])
    spread = entry.get("spread") or {}
    parts = [
        f'<article class="signal-card {kind_class}">',
        '<div class="signal-head">',
        f'<span class="rank">{entry.get("rank", "-")}</span>',
        f'<span class="badge {grade_class}">{escape(grade)}</span>',
        f'<span class="badge {kind_class}">{escape(kind)}</span>',
        '</div>',
        f'<h3>{escape(entry.get("title") or "")}</h3>',
        f'<p class="meta">{meta}</p>',
    ]
    if entry.get("implication"):
        parts.append('<p class="why"><strong>왜 중요한가</strong>'
                     f'{escape(entry["implication"])}</p>')
    parts.append('<p class="act"><strong>권장 워치 액션</strong>'
                 f'{escape(_watch_action(entry))}</p>')
    parts.append(f'<p class="spread">↳ {escape(spread.get("label", "단독 신호"))} '
                 '<span class="hint">(토픽 중복 기반 추정)</span></p>')
    parts.append('</article>')
    return "\n".join(parts)


def render_report_html(brief: dict) -> tuple[str, list[str]]:
    """brief 구조체를 standalone HTML로 렌더링한다. (html, 포함된 섹션 키) 반환."""
    sections = ["hero", "status_board", "one_liner"]
    body = [
        '<header class="hero">',
        '<div class="hero-top">',
        f'<span class="brand">📡 {escape(brief["header"])}</span>',
        f'<span class="mode-chip">{escape(brief["mode"])}</span>',
        '</div>',
        '<h1>Executive Daily Brief</h1>',
        f'<p class="hero-sub">{escape(brief["date_kst"])} (KST) · 임원용 시그널 레이더 일일 브리프</p>',
        '</header>',
        '<section class="board" aria-label="데일리 현황판">',
    ]
    for item in brief["status_board"]:
        cls = "metric immediate" if item.get("key") == "immediate" else "metric"
        body.append(f'<div class="{cls}"><div class="num">{item["value"]}</div>'
                    f'<div class="lbl">{escape(item["label"])}</div></div>')
    body.append('</section>')

    body += [
        '<section class="oneliner">',
        '<h2>오늘의 Executive Signal</h2>',
        f'<p>{escape(brief["executive_one_liner"])}</p>',
        '</section>',
    ]

    signals = brief.get("top_immediate_signals") or []
    all_instant = bool(signals) and all(
        s.get("alert_grade") == "즉시 알림 후보" for s in signals)
    heading = "즉시 알림 후보" if all_instant else "주요 신호"
    body.append('<section class="signals">')
    body.append(f'<h2>{heading} Top {len(signals)}</h2>')
    if signals:
        sections.append("top_signals")
        body += [_render_signal_card(s) for s in signals]
    else:
        body.append('<div class="extra">오늘 감지된 신호가 없습니다 — '
                    'mock 파이프라인 실행 결과를 확인하세요.</div>')

    immediate_ids = {s.get("article_id") for s in signals}
    extras = [i for i in (brief.get("top_new_issues") or [])
              if i.get("article_id") not in immediate_ids]
    if extras:
        sections.append("extra_issues")
        body.append('<div class="extra"><h2>추가 관찰 이슈</h2><ul>')
        for issue in extras:
            body.append(f'<li>· {escape(issue.get("title") or "")} — '
                        f'{escape(issue.get("category_label") or "")}'
                        f' · {_fmt(issue.get("final_score"))}점</li>')
        body.append('</ul></div>')
    body.append('</section>')

    body.append('<div class="duo">')
    themes = brief.get("theme_rankings") or []
    body.append('<section class="panel"><h2>주요 테마 <span class="tag">점수 가중 랭킹</span></h2>')
    if themes:
        sections.append("themes")
        max_weight = max(t["weighted_strength"] for t in themes) or 1
        for t in themes:
            pct = max(8, round(t["weighted_strength"] / max_weight * 100))
            body.append(
                '<div class="theme-row"><div class="theme-name">'
                f'<span>{t["rank"]}. {escape(t["theme"])}</span>'
                f'<span class="cnt">{t["count"]}건 · 강도 {t["weighted_strength"]}</span>'
                f'</div><div class="theme-bar"><span style="width:{pct}%"></span></div></div>')
    else:
        body.append('<p class="meta">집계된 테마가 없습니다.</p>')
    body.append('</section>')

    categories = brief.get("category_counts") or []
    body.append('<section class="panel"><h2>카테고리 요약</h2>')
    if categories:
        sections.append("categories")
        for c in categories:
            imm = (f'<span class="cat-imm">● 즉시 {c["immediate"]}</span>'
                   if c.get("immediate") else '')
            body.append(f'<div class="cat-row"><span>{escape(c["label"])}</span>'
                        f'<span>{c["count"]}건 {imm}</span></div>')
    else:
        body.append('<p class="meta">집계된 카테고리가 없습니다.</p>')
    body.append('</section></div>')

    macro = brief.get("macro_snapshot")
    if macro and macro.get("indicators"):
        sections.append("macro")
        body.append('<section><h2>Macro Snapshot <span class="tag">mock 고정값 — 실시간 아님</span></h2>')
        body.append('<div class="macro-strip">')
        for ind in macro["indicators"]:
            arrow, dir_class = DIRECTION_ARROW.get(ind.get("direction"), ("―", "dir-flat"))
            body.append(f'<div class="macro-chip">{escape(ind["label"])}'
                        f'<span class="v">{escape(str(ind["value"]))}{escape(ind.get("unit", ""))} '
                        f'<span class="{dir_class}">{arrow}</span></span></div>')
        body.append('</div></section>')

    sections += ["notes", "footer"]
    body += [
        '<section class="note">',
        f'※ {escape(brief["operator_note"])}<br>',
        '※ 본 페이지는 데모용 mock 데이터로 생성된 정적 리포트입니다 — 실시간 뉴스·시세가 아니며, '
        '공개 호스팅에는 mock/데모 데이터만 게시합니다.',
        '</section>',
        f'<footer>생성 {escape(brief["generated_at"])} · {escape(brief["header"])}'
        f' · APP_MODE={escape(brief["mode"])} · 정적 스냅샷</footer>',
    ]

    html = "\n".join([
        '<!doctype html>',
        '<html lang="ko">',
        '<head>',
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f'<title>{escape(REPORT_TITLE)}</title>',
        f'<style>{_CSS}</style>',
        '</head>',
        '<body>',
        '<div class="page">',
        *body,
        '</div>',
        '</body>',
        '</html>',
        '',
    ])
    return html, sections


def report_metadata(brief: dict, html: str, sections: list[str]) -> dict:
    """--json용 기계 검증 메타데이터 (HTML 전문은 싣지 않는다)."""
    return {
        "report_title": REPORT_TITLE,
        "mode": brief["mode"],
        "date_kst": brief["date_kst"],
        "generated_at": brief["generated_at"],
        "html_chars": len(html),
        "sections": sections,
        "signal_count": len(brief.get("top_immediate_signals") or []),
        "theme_count": len(brief.get("theme_rankings") or []),
        "category_count": len(brief.get("category_counts") or []),
        "macro_included": bool(brief.get("macro_snapshot")),
        "executive_one_liner": brief["executive_one_liner"],
        "default_output": DEFAULT_OUTPUT,
        "counts": brief.get("pipeline_counts") or {},
    }


def format_summary_text(brief: dict, html: str, sections: list[str]) -> str:
    """--dry-run용 사람 읽기 좋은 요약 (HTML 전문 출력 대신)."""
    signals = brief.get("top_immediate_signals") or []
    lines = [
        f"== {REPORT_TITLE} (정적 리포트) ==",
        f"{brief['date_kst']} (KST) · 모드: {brief['mode']}",
        "",
        "[오늘의 Executive Signal]",
        brief["executive_one_liner"],
        "",
        f"[리포트 구성] {' → '.join(sections)}",
        f"[시그널 카드] {len(signals)}건"
        + (": " + " / ".join(s["title"] for s in signals) if signals else ""),
        f"[테마 {len(brief.get('theme_rankings') or [])}건 · "
        f"카테고리 {len(brief.get('category_counts') or [])}건 · "
        f"macro {'포함' if brief.get('macro_snapshot') else '없음'}]",
        f"[HTML 크기] {len(html)}자 · 외부 CDN/스크립트 0건",
        f"[기본 출력 경로] {DEFAULT_OUTPUT}",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="HDEC Executive Radar — 정적 Executive Daily Brief 빌더 (발송 없음)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true",
                       help="리포트 요약을 출력한다 (파일 쓰기 없음)")
    group.add_argument("--json", action="store_true",
                       help="기계 검증용 메타데이터 JSON을 출력한다")
    group.add_argument("--output", metavar="PATH",
                       help=f"HTML 파일을 PATH에 생성한다 (예: {DEFAULT_OUTPUT})")
    args = parser.parse_args(argv)

    brief = build_brief_via_mock_pipeline()
    html, sections = render_report_html(brief)

    if args.json:
        print(json.dumps(report_metadata(brief, html, sections),
                         ensure_ascii=False, indent=2))
        return 0

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(html, encoding="utf-8")
        print(f"report written: {out_path} ({len(html)} chars)")
        return 0

    print(format_summary_text(brief, html, sections))
    if args.dry_run:
        print(f"[dry-run] html_chars={len(html)} sections={len(sections)} "
              f"signals={len(brief.get('top_immediate_signals') or [])} (쓰기 없음)",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
