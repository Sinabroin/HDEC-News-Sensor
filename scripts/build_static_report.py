"""P0-B5/P0-B6 — 정적 Executive Daily Brief HTML 리포트 빌더.

공유 briefing 레이어(scripts/build_executive_brief.py)의 brief 구조체를
단일 정적 HTML 페이지로 렌더링한다. Telegram 다이제스트의 "오늘 브리프 보기"
버튼이 게시된 이 페이지(docs/daily/latest.html)로 연결된다.

- 네트워크 호출 0건, 비밀값 접근 0건, 외부 CDN/스크립트/폰트 0건 (완전 standalone).
- 저장소의 radar.db는 절대 건드리지 않는다 — brief 빌더가 임시 DB를 쓴다.
- 본문 전문을 싣지 않는다 — 제목/카테고리/점수/시사점 등 파생 요약만 렌더링한다.
- 공개 호스팅(GitHub Pages 등)에는 mock/데모 데이터만 게시한다 (docs/daily/README.md).
- 데이터 정직성(P0-B6): macro_data_mode가 live가 아닌 한 시장지표 수치를
  렌더링하지 않는다 — 미연동 placeholder와 경고 문구만 표시한다.

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

# 기회/리스크는 채도 높은 배지 대신 텍스트 라벨 색으로만 구분한다 (executive memo 톤)
KIND_CLASS = {
    "기회": "kind-opp",
    "리스크": "kind-risk",
    "기회+리스크": "kind-both",
    "관찰": "kind-watch",
}

# live macro 전용 — mock/unavailable 상태에서는 수치·방향 자체를 렌더링하지 않는다
DIRECTION_ARROW = {"up": ("▲", "dir-up"), "down": ("▼", "dir-down")}

# macro 미연동 placeholder에 쓸 기본 지표 라벨 (snapshot이 비어 있을 때)
DEFAULT_MACRO_LABELS = ["USD/KRW", "WTI", "KOSPI", "VIX", "미 국채 10Y", "국고채 10Y"]

# 외부 리소스 0건 원칙: 로컬 폰트 스택(Pretendard 우선)만 선언하고
# <script>/<link>/@import/url()을 일절 쓰지 않는다.
_CSS = """
:root{
  --paper:#f6f4ee;--paper-2:#fdfcf9;--ink:#22262e;--ink-soft:#494e58;
  --muted:#807a6d;--navy:#152740;--accent:#176a4c;--signal:#9a3b2e;
  --hairline:#ddd8cb;--hairline-2:#c8c2b2;
}
*{box-sizing:border-box;margin:0;padding:0;}
html{-webkit-text-size-adjust:100%;}
body{
  background:var(--paper);color:var(--ink);font-size:14px;line-height:1.6;
  font-family:Pretendard,-apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans KR","Malgun Gothic",sans-serif;
  border-top:4px solid var(--navy);
  background-image:linear-gradient(180deg,rgba(21,39,64,.05),rgba(21,39,64,0) 240px);
}
.page{max-width:760px;margin:0 auto;padding:26px 20px 44px;}
.num{font-variant-numeric:tabular-nums;}

/* ---- masthead ---- */
.masthead{border-bottom:2px solid var(--ink);padding-bottom:16px;position:relative;}
.masthead::after{content:"";position:absolute;left:0;right:0;bottom:-5px;height:1px;background:var(--hairline-2);}
.mast-row{display:flex;align-items:center;justify-content:space-between;gap:10px;}
.brand{font-size:11px;letter-spacing:.34em;font-weight:700;color:var(--navy);text-transform:uppercase;}
.mode-pill{font-size:10px;letter-spacing:.18em;font-weight:600;color:var(--muted);
  border:1px solid var(--hairline-2);border-radius:2px;padding:3px 9px;white-space:nowrap;}
.masthead h1{font-size:30px;letter-spacing:-.5px;font-weight:750;color:var(--ink);margin-top:14px;line-height:1.2;}
.dateline{font-size:12.5px;color:var(--ink-soft);margin-top:6px;}
.provenance{font-size:11.5px;color:var(--muted);margin-top:3px;}

/* ---- 현황판 ---- */
.board{display:grid;grid-template-columns:repeat(5,1fr);gap:14px;margin:22px 0 6px;}
.board .item{border-top:1px solid var(--hairline-2);padding-top:9px;}
.board .n{font-size:24px;font-weight:750;color:var(--navy);letter-spacing:-.5px;}
.board .item.immediate .n{color:var(--signal);}
.board .l{font-size:10.5px;color:var(--muted);margin-top:1px;letter-spacing:.04em;}
@media(max-width:540px){.board{grid-template-columns:repeat(3,1fr);row-gap:16px;}}

/* ---- 오늘의 Executive Signal ---- */
.oneliner{background:var(--navy);color:#f2efe6;border-left:3px solid var(--accent);
  padding:18px 20px 17px;margin:18px 0 8px;border-radius:2px;}
.oneliner .ovl{font-size:10px;letter-spacing:.3em;font-weight:700;color:rgba(242,239,230,.62);display:block;text-transform:uppercase;}
.oneliner p{font-size:17.5px;line-height:1.62;font-weight:600;margin-top:8px;letter-spacing:-.2px;}

/* ---- 섹션 헤딩 ---- */
section{margin-top:26px;}
.sec-h{display:flex;align-items:baseline;gap:10px;font-size:12.5px;font-weight:750;
  letter-spacing:.07em;color:var(--ink);}
.sec-h .tag{font-size:10.5px;color:var(--muted);font-weight:500;letter-spacing:.02em;white-space:nowrap;}
.sec-h::after{content:"";flex:1;height:1px;background:var(--hairline-2);transform:translateY(-3px);}

/* ---- 시그널 ---- */
.sig{display:grid;grid-template-columns:36px 1fr;gap:0 4px;padding:15px 0 14px;
  border-bottom:1px solid var(--hairline);}
.sig:last-of-type{border-bottom:none;}
.sig .idx{font-size:15px;font-weight:750;color:var(--accent);letter-spacing:.04em;padding-top:2px;}
.sig .labels{font-size:10.5px;letter-spacing:.12em;font-weight:700;color:var(--ink-soft);}
.sig .labels .sep{color:var(--hairline-2);margin:0 6px;font-weight:400;}
.kind-opp{color:var(--accent);}
.kind-risk{color:var(--signal);}
.kind-both{color:var(--signal);}
.kind-watch{color:var(--muted);}
.sig h3{font-size:16px;font-weight:680;letter-spacing:-.25px;line-height:1.45;margin-top:5px;}
.sig .meta{font-size:11.5px;color:var(--muted);margin-top:4px;}
.sig .why,.sig .act{font-size:13px;color:var(--ink-soft);margin-top:7px;line-height:1.58;}
.sig .why strong,.sig .act strong{display:block;font-size:10.5px;letter-spacing:.14em;
  color:var(--navy);font-weight:750;margin-bottom:1px;}
.sig .spread{font-size:11px;color:var(--muted);margin-top:7px;}
.sig h3 a{color:inherit;text-decoration:none;border-bottom:1px solid var(--hairline-2);}
.sig h3 a:hover{border-bottom-color:var(--accent);}
.sig .spread a,.extra a{color:var(--navy);text-decoration:none;border-bottom:1px solid var(--hairline-2);}
.sig .spread .hint{color:var(--muted);}

/* ---- 중요도 미터 + 점수 구성요소 ---- */
.score-meter{margin-top:9px;}
.score-head{display:flex;justify-content:space-between;align-items:baseline;font-size:11.5px;}
.score-head .score-num{font-weight:750;color:var(--navy);}
.score-head .band{font-size:10.5px;font-weight:750;letter-spacing:.04em;color:var(--accent);}
.meter{height:6px;background:var(--hairline);border-radius:3px;margin-top:4px;overflow:hidden;}
.meter>span{display:block;height:100%;background:linear-gradient(90deg,var(--accent),var(--navy));}
.comps{display:grid;grid-template-columns:1fr 1fr;gap:3px 18px;margin-top:9px;}
.comp{display:flex;align-items:center;gap:7px;font-size:10.5px;color:var(--ink-soft);}
.comp .cl{flex:0 0 80px;color:var(--muted);}
.comp .cbar{flex:1;height:3px;background:var(--hairline);border-radius:2px;overflow:hidden;}
.comp .cbar i{display:block;height:100%;background:var(--accent);}
.comp .cv{flex:0 0 22px;text-align:right;font-weight:650;color:var(--ink);}
.caption{font-size:10.5px;color:var(--muted);margin-top:8px;line-height:1.55;}
@media(max-width:540px){.comps{grid-template-columns:1fr;}}

/* ---- 추가 관찰 이슈 ---- */
.extra{margin-top:4px;padding:11px 0 0;border-top:1px dashed var(--hairline-2);}
.extra .sec-sub{font-size:10.5px;letter-spacing:.14em;font-weight:750;color:var(--muted);margin-bottom:5px;}
.extra ul{list-style:none;}
.extra li{font-size:12.5px;color:var(--ink-soft);padding:3px 0;}
.extra li .cnt{color:var(--muted);}

/* ---- 테마 / 카테고리 ---- */
.duo{display:grid;grid-template-columns:1fr;gap:0 34px;}
@media(min-width:660px){.duo{grid-template-columns:1.1fr 1fr;}.duo section{margin-top:26px;}}
.theme-row{margin-top:11px;}
.theme-name{display:flex;justify-content:space-between;align-items:baseline;gap:10px;font-size:13px;font-weight:600;}
.theme-name .cnt{font-size:11px;color:var(--muted);font-weight:500;white-space:nowrap;}
.theme-bar{height:3px;background:var(--hairline);margin-top:5px;}
.theme-bar span{display:block;height:100%;background:var(--accent);}
.cat-row{display:flex;align-items:baseline;gap:8px;font-size:13px;padding:6.5px 0;}
.cat-row .lead{flex:1;border-bottom:1px dotted var(--hairline-2);transform:translateY(-3px);}
.cat-row .cnt{font-weight:650;color:var(--ink);}
.cat-row .imm{font-size:10.5px;color:var(--signal);font-weight:700;letter-spacing:.06em;margin-left:5px;}

/* ---- Macro Snapshot ---- */
.macro-note{font-size:12px;color:var(--ink-soft);margin-top:10px;line-height:1.6;}
.macro-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:10px;}
@media(max-width:540px){.macro-grid{grid-template-columns:repeat(2,1fr);}}
.macro-cell{border:1px dashed var(--hairline-2);border-radius:2px;padding:8px 11px;}
.macro-cell .lbl{font-size:10.5px;color:var(--muted);letter-spacing:.05em;}
.macro-cell .val{font-size:13px;font-weight:650;color:var(--muted);margin-top:1px;}
.macro-cell.live{border-style:solid;background:var(--paper-2);}
.macro-cell.live .val{color:var(--ink);}
.macro-src{font-size:11px;color:var(--muted);margin-top:8px;}
.dir-up{color:var(--signal);}
.dir-down{color:var(--navy);}

/* ---- 고지 / footer ---- */
.notes{border-top:1px solid var(--hairline-2);margin-top:30px;padding-top:13px;}
.notes p{font-size:11.5px;color:var(--muted);line-height:1.65;margin-top:3px;}
footer{margin-top:26px;text-align:center;font-size:10px;letter-spacing:.18em;color:var(--muted);}
@media(min-width:700px){.page{padding:38px 40px 56px;}}
"""


def _fmt(value) -> str:
    return "-" if value is None else f"{value:.2f}"


def _fmt5(value) -> str:
    """중요도 표시 — 분모 명시(X.X / 5.0)의 분자."""
    return "-" if value is None else f"{value:.1f}"


def _pct(value) -> str:
    """판정 신뢰도 — 0~1 값을 백분율 문자열로."""
    return "-" if value is None else f"{round(value * 100)}%"


def _score_pct(value) -> int:
    """점수(0~5)를 미터 막대 너비(0~100%)로."""
    try:
        return max(0, min(100, round(float(value) / 5 * 100)))
    except (TypeError, ValueError):
        return 0


def _is_http(url: str) -> bool:
    return bool(url) and url.startswith(("http://", "https://"))


def _mode_pill(brief: dict) -> str:
    """masthead 배지 — 개발자용 'MOCK DATA' 대신 뉴스 모드 기준 간결 라벨."""
    return "LIVE · 공개 RSS" if brief.get("news_data_mode") == "live" else "데모 데이터"


def _source_link(url: str, label: str) -> str:
    """원문 링크 앵커 — 새 탭 + noopener noreferrer. URL이 없으면 빈 문자열."""
    if not _is_http(url):
        return ""
    return (f'<a href="{escape(url)}" target="_blank" rel="noopener noreferrer">'
            f'{escape(label)}</a>')


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


def _render_score_meter(entry: dict) -> str:
    """중요도 미터(분모 명시) + 점수대 라벨 + 6개 구성요소 막대."""
    score = entry.get("final_score")
    band = entry.get("score_band") or "-"
    parts = [
        '<div class="score-meter">',
        '<div class="score-head">'
        f'<span class="score-num num">중요도 {_fmt5(score)} / 5.0</span>'
        f'<span class="band">{escape(band)}</span></div>',
        f'<div class="meter"><span style="width:{_score_pct(score)}%"></span></div>',
        '</div>',
    ]
    comps = entry.get("score_components") or []
    if comps:
        parts.append('<div class="comps">')
        for c in comps:
            parts.append(
                f'<div class="comp"><span class="cl">{escape(c["label"])}</span>'
                f'<span class="cbar"><i style="width:{_score_pct(c.get("value"))}%"></i></span>'
                f'<span class="cv num">{_fmt5(c.get("value"))}</span></div>')
        parts.append('</div>')
    return "".join(parts)


def _render_signal(index: int, entry: dict) -> str:
    kind = entry.get("opportunity_or_risk") or "관찰"
    kind_class = KIND_CLASS.get(kind, "kind-watch")
    action = entry.get("action_label") or "모니터링"
    url = entry.get("url") or ""
    title = escape(entry.get("title") or "")
    title_html = (f'{_source_link(url, entry.get("title") or "")} ↗'
                  if _is_http(url) else title)
    meta = " · ".join([
        escape(entry.get("source") or "출처 미상"),
        escape(entry.get("category_label") or "건설산업 일반"),
        f'<span class="num">판정 신뢰도 {_pct(entry.get("confidence"))}</span>',
    ])
    spread = entry.get("spread") or {}
    parts = [
        '<article class="sig">',
        f'<span class="idx num">{index:02d}</span>',
        '<div>',
        f'<p class="labels">{escape(action)}<span class="sep">/</span>'
        f'<span class="{kind_class}">{escape(kind)}</span></p>',
        f'<h3>{title_html}</h3>',
        f'<p class="meta">{meta}</p>',
        _render_score_meter(entry),
    ]
    if entry.get("implication"):
        parts.append('<p class="why"><strong>왜 중요한가</strong>'
                     f'{escape(entry["implication"])}</p>')
    parts.append('<p class="act"><strong>권장 워치 액션</strong>'
                 f'{escape(_watch_action(entry))}</p>')
    src = _source_link(url, "원문 보기 ↗")
    src_html = f' · {src}' if src else ""
    parts.append(f'<p class="spread">↳ {escape(spread.get("label", "단독 신호"))} '
                 f'<span class="hint">(유사 주제 기사는 참고 묶음 추정)</span>{src_html}</p>')
    parts.append('</div></article>')
    return "\n".join(parts)


def _render_macro_section(brief: dict) -> list[str]:
    """Macro Snapshot 섹션 — live가 아닌 한 수치를 렌더링하지 않는다 (P0-B6)."""
    macro = brief.get("macro_snapshot") or {}
    mode = macro.get("macro_data_mode") or brief.get("macro_data_mode") or "unavailable"
    values = macro.get("values") or []

    body = ['<section aria-label="Macro Snapshot">']
    if mode == "live" and values:
        body.append('<h2 class="sec-h">MACRO SNAPSHOT'
                    f'<span class="tag">출처 {escape(str(macro.get("source") or ""))}'
                    f' · 기준 {escape(str(macro.get("updated_at") or ""))}</span></h2>')
        body.append('<div class="macro-grid">')
        for v in values:
            arrow, dir_class = DIRECTION_ARROW.get(v.get("direction"), ("", ""))
            arrow_html = f' <span class="{dir_class}">{arrow}</span>' if arrow else ""
            body.append(f'<div class="macro-cell live"><div class="lbl">{escape(v["label"])}</div>'
                        f'<div class="val num">{escape(str(v["value"]))}{escape(v.get("unit", ""))}'
                        f'{arrow_html}</div></div>')
        body.append('</div>')
        body.append(f'<p class="macro-src">{escape(str(macro.get("disclaimer") or ""))}</p>')
    else:
        labels = [v.get("label") for v in values if v.get("label")] or DEFAULT_MACRO_LABELS
        body.append('<h2 class="sec-h">MACRO SNAPSHOT'
                    '<span class="tag">시장지표 미연동</span></h2>')
        body.append('<p class="macro-note">시장지표는 아직 실시간 연동 전입니다 — '
                    '현재 시장값이 아니므로 수치를 표시하지 않습니다.</p>')
        body.append('<div class="macro-grid">')
        for label in labels:
            body.append(f'<div class="macro-cell"><div class="lbl">{escape(label)}</div>'
                        '<div class="val">미연동</div></div>')
        body.append('</div>')
    body.append('</section>')
    return body


def render_report_html(brief: dict) -> tuple[str, list[str]]:
    """brief 구조체를 standalone HTML로 렌더링한다. (html, 포함된 섹션 키) 반환."""
    sections = ["hero", "status_board", "one_liner"]
    body = [
        '<header class="masthead">',
        '<div class="mast-row">',
        '<span class="brand">HDEC Executive Radar</span>',
        f'<span class="mode-pill">{escape(_mode_pill(brief))}</span>',
        '</div>',
        '<h1>Executive Daily Brief</h1>',
        f'<p class="dateline num">{escape(brief["date_kst"])} (KST) · 임원용 시그널 레이더 일일 브리프</p>',
        f'<p class="provenance">{escape(brief.get("data_warning") or "")}</p>',
        '</header>',
        '<section class="board" aria-label="데일리 현황판">',
    ]
    for item in brief["status_board"]:
        cls = "item immediate" if item.get("key") == "immediate" else "item"
        body.append(f'<div class="{cls}"><div class="n num">{item["value"]}</div>'
                    f'<div class="l">{escape(item["label"])}</div></div>')
    body.append('</section>')

    body += [
        '<section class="oneliner">',
        '<span class="ovl">오늘의 Executive Signal</span>',
        f'<p>{escape(brief["executive_one_liner"])}</p>',
        '</section>',
    ]

    signals = brief.get("top_immediate_signals") or []
    all_instant = bool(signals) and all(
        s.get("alert_grade") == "즉시 알림 후보" for s in signals)
    heading = "즉시 알림 후보" if all_instant else "주요 신호"
    body.append('<section aria-label="주요 시그널">')
    body.append(f'<h2 class="sec-h">{heading} TOP {len(signals)}'
                '<span class="tag">점수순 · 운영자 검토 전 자동 선별</span></h2>')
    if signals:
        sections.append("top_signals")
        body += [_render_signal(i, s) for i, s in enumerate(signals, start=1)]
    else:
        body.append('<p class="macro-note">오늘 감지된 신호가 없습니다 — '
                    'mock 파이프라인 실행 결과를 확인하세요.</p>')

    immediate_ids = {s.get("article_id") for s in signals}
    extras = [i for i in (brief.get("top_new_issues") or [])
              if i.get("article_id") not in immediate_ids]
    if extras:
        sections.append("extra_issues")
        body.append('<div class="extra"><p class="sec-sub">추가 관찰 이슈</p><ul>')
        for issue in extras:
            url = issue.get("url") or ""
            title_html = (f'{_source_link(url, issue.get("title") or "")} ↗'
                          if _is_http(url) else escape(issue.get("title") or ""))
            body.append(f'<li>· {title_html} '
                        f'<span class="cnt">— {escape(issue.get("category_label") or "")}'
                        f' · <span class="num">중요도 {_fmt5(issue.get("final_score"))}/5.0</span>'
                        f' · {escape(issue.get("action_label") or "모니터링")}</span></li>')
        body.append('</ul></div>')
    body.append('</section>')

    body.append('<div class="duo">')
    themes = brief.get("theme_rankings") or []
    body.append('<section aria-label="주요 테마"><h2 class="sec-h">주요 테마'
                '<span class="tag">상대 강도 · 100=최상위 테마</span></h2>')
    if themes:
        sections.append("themes")
        for t in themes:
            rel = t.get("relative_strength") or 1
            body.append(
                '<div class="theme-row"><div class="theme-name">'
                f'<span>{t["rank"]}. {escape(t["theme"])}</span>'
                f'<span class="cnt num">{t["count"]}건 · 상대 강도 {rel}</span>'
                f'</div><div class="theme-bar"><span style="width:{max(7, rel)}%"></span></div></div>')
        body.append(f'<p class="caption">{escape(brief.get("theme_strength_note") or "")}</p>')
    else:
        body.append('<p class="macro-note">집계된 테마가 없습니다.</p>')
    body.append('</section>')

    categories = brief.get("category_counts") or []
    body.append('<section aria-label="카테고리 요약"><h2 class="sec-h">카테고리 요약</h2>')
    if categories:
        sections.append("categories")
        for c in categories:
            imm = (f'<span class="imm">즉시 {c["immediate"]}</span>'
                   if c.get("immediate") else '')
            body.append(f'<div class="cat-row"><span>{escape(c["label"])}</span>'
                        f'<span class="lead"></span>'
                        f'<span class="cnt num">{c["count"]}건</span>{imm}</div>')
    else:
        body.append('<p class="macro-note">집계된 카테고리가 없습니다.</p>')
    body.append('</section></div>')

    sections.append("macro")
    body += _render_macro_section(brief)

    sections += ["notes", "footer"]
    body += [
        '<div class="notes">',
        f'<p>※ {escape(brief["operator_note"])}</p>',
        f'<p>※ {escape(brief.get("spread_note") or "")}</p>',
        '</div>',
        f'<footer class="num">생성 {escape(brief["generated_at"])} · {escape(brief["header"])}</footer>',
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
        "news_data_mode": brief.get("news_data_mode"),
        "macro_data_mode": brief.get("macro_data_mode"),
        "macro_source": brief.get("macro_source"),
        "macro_updated_at": brief.get("macro_updated_at"),
        "date_kst": brief["date_kst"],
        "generated_at": brief["generated_at"],
        "html_chars": len(html),
        "sections": sections,
        "signal_count": len(brief.get("top_immediate_signals") or []),
        "theme_count": len(brief.get("theme_rankings") or []),
        "category_count": len(brief.get("category_counts") or []),
        "macro_included": "macro" in sections,
        "executive_one_liner": brief["executive_one_liner"],
        "default_output": DEFAULT_OUTPUT,
        "counts": brief.get("pipeline_counts") or {},
    }


def format_summary_text(brief: dict, html: str, sections: list[str]) -> str:
    """--dry-run용 사람 읽기 좋은 요약 (HTML 전문 출력 대신)."""
    signals = brief.get("top_immediate_signals") or []
    lines = [
        f"== {REPORT_TITLE} (정적 리포트) ==",
        f"{brief['date_kst']} (KST) · mock 데이터 기반 · 시장지표 미연동",
        "",
        "[오늘의 Executive Signal]",
        brief["executive_one_liner"],
        "",
        f"[리포트 구성] {' → '.join(sections)}",
        f"[시그널 카드] {len(signals)}건"
        + (": " + " / ".join(s["title"] for s in signals) if signals else ""),
        f"[테마 {len(brief.get('theme_rankings') or [])}건 · "
        f"카테고리 {len(brief.get('category_counts') or [])}건 · "
        f"macro {brief.get('macro_data_mode')} (수치 비표시)]",
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
