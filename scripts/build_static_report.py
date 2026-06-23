"""P0-B5/P0-B6 — 정적 Executive Daily Brief HTML 리포트 빌더.

공유 briefing 레이어(scripts/build_executive_brief.py)의 brief 구조체를
단일 정적 HTML 페이지로 렌더링한다. Telegram 다이제스트의 "전체 리포트 보기"
버튼이 게시된 이 페이지(docs/daily/latest.html)로 연결된다. "요약 대시보드 보기"는
별도 export(docs/daily/dashboard-latest.html)를 가리킨다.

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
import os
import sys
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from build_executive_brief import build_brief_via_mock_pipeline  # noqa: E402

REPORT_TITLE = "HDEC Executive Radar — Executive Daily Brief"
DEFAULT_OUTPUT = "docs/daily/latest.html"
_KST = timezone(timedelta(hours=9))

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
.sig .srcq{font-weight:700;letter-spacing:.02em;}
.sig .srcq.trust{color:var(--accent);}
.sig .srcq.low{color:var(--signal);}
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

/* ---- 카테고리별 근거 기사 (드릴다운, JS 없이 <details>만) ---- */
.cd-note{font-size:11px;color:var(--muted);margin:9px 0 4px;line-height:1.55;}
details.cat-drill{border-bottom:1px solid var(--hairline);}
details.cat-drill:first-of-type{border-top:1px solid var(--hairline-2);}
details.cat-drill>summary{list-style:none;cursor:pointer;display:flex;align-items:baseline;
  gap:9px;padding:11px 2px;}
details.cat-drill>summary::-webkit-details-marker{display:none;}
details.cat-drill>summary::before{content:"▸";color:var(--muted);font-size:10px;
  flex:0 0 9px;transform:translateY(-1px);}
details.cat-drill[open]>summary::before{content:"▾";}
.cd-label{font-size:13.5px;font-weight:680;color:var(--ink);}
.cd-count{color:var(--ink);font-size:12px;font-weight:650;white-space:nowrap;}
.cd-imm{color:var(--signal);font-size:10px;font-weight:750;letter-spacing:.04em;white-space:nowrap;}
.cd-flex{flex:1;}
.cd-body{padding:1px 0 13px 17px;}
.cd-art{padding:9px 0;border-top:1px dashed var(--hairline);}
.cd-art:first-child{border-top:none;}
.cd-art-head{display:flex;justify-content:space-between;align-items:baseline;gap:12px;}
.cd-title{font-size:13.5px;font-weight:600;line-height:1.46;}
.cd-title a{color:inherit;text-decoration:none;border-bottom:1px solid var(--hairline-2);}
.cd-title a:hover{border-bottom-color:var(--accent);}
.cd-sc{font-size:11px;font-weight:750;color:var(--navy);white-space:nowrap;}
.cd-meta{font-size:11px;color:var(--muted);margin-top:3px;}
.cd-meta .srcq{font-weight:700;}
.cd-meta .srcq.trust{color:var(--accent);}
.cd-meta .srcq.low{color:var(--signal);}
.cd-why{font-size:11.5px;color:var(--ink-soft);margin-top:4px;line-height:1.5;}
.cd-more{font-size:11px;color:var(--muted);margin-top:9px;}
.cd-empty{font-size:11.5px;color:var(--muted);padding:6px 0;}

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

/* ---- P0-C3 상단 탭/필터 (앵커 점프 없음, JS 없음) ---- */
.radar-tabs-ui{margin:18px 0 4px;}
.radar-tab-input{position:absolute;opacity:0;pointer-events:none;}
.topnav{position:sticky;top:0;z-index:5;display:flex;flex-wrap:wrap;gap:7px;
  margin:18px 0 8px;padding:7px 0;background:rgba(246,244,238,.96);
  border-bottom:1px solid var(--hairline);}
.topnav label{font-size:11.5px;font-weight:650;letter-spacing:.02em;color:var(--navy);
  text-decoration:none;border:1px solid var(--hairline-2);border-radius:14px;
  padding:5px 13px;background:var(--paper-2);white-space:nowrap;cursor:pointer;}
.topnav label:hover{border-color:var(--accent);color:var(--accent);}
.radar-panels{min-height:180px;}
.radar-panel{display:none;}
#radar-tab-hdec:checked ~ .topnav label[for="radar-tab-hdec"],
#radar-tab-ai:checked ~ .topnav label[for="radar-tab-ai"],
#radar-tab-biz:checked ~ .topnav label[for="radar-tab-biz"],
#radar-tab-risk:checked ~ .topnav label[for="radar-tab-risk"],
#radar-tab-comp:checked ~ .topnav label[for="radar-tab-comp"],
#radar-tab-macro:checked ~ .topnav label[for="radar-tab-macro"],
#radar-tab-evidence:checked ~ .topnav label[for="radar-tab-evidence"]{
  background:var(--navy);color:#f2efe6;border-color:var(--navy);}
#radar-tab-hdec:checked ~ .radar-panels #hdec-radar,
#radar-tab-ai:checked ~ .radar-panels #ai-radar,
#radar-tab-biz:checked ~ .radar-panels #biz-radar,
#radar-tab-risk:checked ~ .radar-panels #risk-radar,
#radar-tab-comp:checked ~ .radar-panels #comp-radar,
#radar-tab-macro:checked ~ .radar-panels #macro,
#radar-tab-evidence:checked ~ .radar-panels #evidence{display:block;}

/* ---- P0-C1.9 레이더 섹션 ---- */
section.radar{margin-top:24px;}
section.radar.lead .sec-h{font-size:14px;}
.radar-empty{font-size:12px;color:var(--muted);padding:8px 0 2px;}
/* collapsed radar drawers (macro / evidence): native details with no open attribute */
details.radar-section{margin-top:24px;border-top:1px solid var(--hairline-2);}
details.radar-section>summary{list-style:none;cursor:pointer;display:flex;align-items:baseline;
  gap:10px;padding:13px 2px 4px;}
details.radar-section>summary::-webkit-details-marker{display:none;}
details.radar-section>summary::before{content:"▸";color:var(--muted);font-size:11px;
  flex:0 0 10px;transform:translateY(-1px);}
details.radar-section[open]>summary::before{content:"▾";}
details.radar-section>summary .rs-h{font-size:13px;font-weight:750;letter-spacing:.06em;color:var(--ink);}
details.radar-section>summary .rs-tag{font-size:10.5px;color:var(--muted);font-weight:500;}
details.radar-section>.rs-body{padding:4px 0 6px;}

/* ---- P0-C1.9 리스크 칩 + 리스크 우선도 ---- */
.risk-chip{display:inline-block;font-size:10px;font-weight:750;letter-spacing:.04em;
  color:var(--signal);border:1px solid var(--signal);border-radius:3px;padding:1px 7px;margin-right:7px;}
.risk-pri{margin-top:9px;}
.risk-pri .rp-head{display:flex;justify-content:space-between;align-items:baseline;font-size:11.5px;}
.risk-pri .rp-num{font-weight:750;color:var(--signal);}
.risk-pri .rp-lbl{font-size:10.5px;font-weight:700;letter-spacing:.06em;color:var(--signal);}
.risk-pri .meter>span{background:linear-gradient(90deg,var(--signal),var(--navy));}

/* ---- P0-C1.9 중요도 아코디언 (구성요소 기본 접힘) ---- */
details.score-acc{margin-top:9px;}
details.score-acc>summary{list-style:none;cursor:pointer;}
details.score-acc>summary::-webkit-details-marker{display:none;}
details.score-acc>summary .score-head{display:flex;justify-content:space-between;align-items:baseline;font-size:11.5px;}
details.score-acc>summary .more{font-size:10px;color:var(--muted);letter-spacing:.02em;}
details.score-acc[open]>summary .more::after{content:" 접기";}
details.score-acc:not([open])>summary .more::after{content:" 펼치기";}

/* ---- 고지 / footer ---- */
.notes{border-top:1px solid var(--hairline-2);margin-top:30px;padding-top:13px;}
.notes p{font-size:11.5px;color:var(--muted);line-height:1.65;margin-top:3px;}
footer{margin-top:26px;text-align:center;font-size:10px;letter-spacing:.18em;color:var(--muted);}
@media(min-width:700px){.page{padding:38px 40px 56px;}}
"""


def _fmt(value) -> str:
    return "-" if value is None else f"{value:.2f}"


def _fmt_kst(iso) -> str:
    """ISO 타임스탬프(UTC/KST 무관)를 KST 벽시계 'YYYY-MM-DD HH:MM'로 표시한다.

    임원 화면에 +00:00 같은 raw offset(Yahoo는 시장 기준시각을 UTC로 준다)을 그대로
    노출하지 않기 위한 표시 전용 변환이다 (P0-D1.5). 시각 자체는 바꾸지 않고 같은 순간을
    KST로 읽어줄 뿐이며, 파싱 불가하면 원본을 그대로 돌려준다 (가짜 KST 위장 금지)."""
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return str(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_KST).strftime("%Y-%m-%d %H:%M")


def _fmt5(value) -> str:
    """중요도 표시 — 분모 명시(X.X / 5.0)의 분자."""
    return "-" if value is None else f"{value:.1f}"


def _score_pct(value) -> int:
    """점수(0~5)를 미터 막대 너비(0~100%)로."""
    try:
        return max(0, min(100, round(float(value) / 5 * 100)))
    except (TypeError, ValueError):
        return 0


def _is_http(url: str) -> bool:
    return bool(url) and url.startswith(("http://", "https://"))


def _display(text) -> str:
    """임원 화면용 용어 치환. 내부 키/분류명은 그대로 둔다."""
    out = "" if text is None else str(text)
    for old, new in (
        ("현대건설 직접 관련", "현대건설 연관"),
        ("현대건설 직접 영향", "현대건설 연관"),
        ("현대건설 직접", "현대건설 연관"),
        ("검토 필요", "중요 신호"),
        ("추적 필요", "계속 관찰"),
    ):
        out = out.replace(old, new)
    return out


def _mode_pill(brief: dict) -> str:
    """masthead 배지 — 'LIVE'·'공개 RSS' 같은 기술 용어 없이 (P0-C1.10).

    live는 중립적 '자동 수집', mock/fallback은 '데모 데이터'로 표기한다.
    live/mock 기계 판별은 본문 상단의 보이지 않는 마커(news-data-mode)로 한다.
    """
    return "자동 수집" if brief.get("news_data_mode") == "live" else "데모 데이터"


def _source_link(url: str, label: str) -> str:
    """원문 링크 앵커 — 새 탭 + noopener noreferrer. URL이 없으면 빈 문자열."""
    if not _is_http(url):
        return ""
    return (f'<a href="{escape(url)}" target="_blank" rel="noopener noreferrer">'
            f'{escape(_display(label))}</a>')


def _render_score_meter(entry: dict) -> str:
    """중요도(분모 명시) + 미터 — 6개 구성요소는 <details>로 기본 접힘 (P0-C1.9).

    카드는 중요도 점수 + 컴팩트 미터만 보여주고, '중요도' summary를 펼치면 구성요소
    막대가 나타난다 (JS 없이 네이티브 details/summary, open 속성 없음).
    """
    score = entry.get("final_score")
    comps = entry.get("score_components") or []
    head = (
        '<summary>'
        '<span class="score-head">'
        f'<span class="score-num num">중요도 {_fmt5(score)} / 5.0</span>'
        '<span class="more"></span></span>'
        f'<span class="meter"><span style="width:{_score_pct(score)}%"></span></span>'
        '</summary>'
    )
    body = []
    if comps:
        body.append('<div class="comps">')
        for c in comps:
            body.append(
                f'<div class="comp"><span class="cl">{escape(c["label"])}</span>'
                f'<span class="cbar"><i style="width:{_score_pct(c.get("value"))}%"></i></span>'
                f'<span class="cv num">{_fmt5(c.get("value"))}</span></div>')
        body.append('</div>')
    return f'<details class="score-acc">{head}{"".join(body)}</details>'


def _render_risk_priority(entry: dict) -> str:
    """리스크 우선도 미터 — 종합 중요도가 낮아도 중대재해·규제를 전면에 둔다 (P0-C1.9)."""
    pri = entry.get("risk_priority_score")
    if pri is None:
        return ""
    label = entry.get("risk_radar_label") or "리스크"
    return (
        '<div class="risk-pri"><div class="rp-head">'
        f'<span class="rp-num num">리스크 우선도 {_fmt5(pri)} / 5.0</span>'
        f'<span class="rp-lbl">{escape(label)}</span></div>'
        f'<div class="meter"><span style="width:{_score_pct(pri)}%"></span></div></div>'
    )


def _render_signal(index: int, entry: dict, risk_mode: bool = False) -> str:
    url = entry.get("url") or ""
    title = escape(_display(entry.get("title") or ""))
    title_html = (f'{_source_link(url, entry.get("title") or "")} ↗'
                  if _is_http(url) else title)
    # 라벨 줄: 등급 액션 라벨은 숨기고, 리스크 맥락 칩만 필요한 경우 노출한다.
    labels = []
    if risk_mode and entry.get("risk_radar_label"):
        labels.append(f'<span class="risk-chip">{escape(_display(entry["risk_radar_label"]))}</span>')
    # 임원 표시용 출처 — 집계 호스트는 'Daum 경유' 등으로 정규화된 display_source 우선 (P0-C1.11).
    meta_parts = [escape(_display(entry.get("display_source") or entry.get("source") or "출처 미상"))]
    # 출처 품질 라벨 — 어수선함을 피해 신뢰/낮은 신뢰도일 때만 노출 (일반 출처는 생략).
    sq = entry.get("source_quality")
    if sq in ("trusted", "low"):
        cls = "srcq trust" if sq == "trusted" else "srcq low"
        meta_parts.append(
            f'<span class="{cls}">{escape(_display(entry.get("source_quality_label") or ""))}</span>')
    meta_parts.append(escape(_display(entry.get("category_label") or "건설산업 일반")))
    if entry.get("published_at"):
        meta_parts.append(f'<span class="num">{escape(_fmt_date(entry.get("published_at")))}</span>')
    meta = " · ".join(meta_parts)
    spread = entry.get("spread") or {}
    reason = _display(entry.get("one_line_reason") or entry.get("implication") or "")
    label_html = f'<p class="labels">{"".join(labels)}</p>' if labels else ""
    parts = [
        '<article class="sig">',
        f'<span class="idx num">{index:02d}</span>',
        '<div>',
        label_html,
        f'<h3>{title_html}</h3>',
        f'<p class="meta">{meta}</p>',
    ]
    if risk_mode:
        parts.append(_render_risk_priority(entry))
    parts.append(_render_score_meter(entry))
    if reason:
        parts.append('<p class="why"><strong>왜 중요한가</strong>'
                     f'{escape(reason)}</p>')
    src = _source_link(url, "원문 보기 ↗")
    src_html = f' · {src}' if src else ""
    parts.append(f'<p class="spread">↳ {escape(_display(spread.get("label", "단독 신호")))}'
                 f'{src_html}</p>')
    parts.append('</div></article>')
    return "\n".join(parts)


def _render_radar_signals(signals: list, risk_mode: bool = False,
                          empty_text: str = "") -> list[str]:
    """레이더 섹션 본문 — 시그널 카드 묶음 또는 빈 안내."""
    if signals:
        return [_render_signal(i, s, risk_mode=risk_mode)
                for i, s in enumerate(signals, start=1)]
    return [f'<p class="radar-empty">{escape(empty_text)}</p>']


def _fmt_date(value) -> str:
    """published_at에서 날짜(YYYY-MM-DD)만 추려 근거 목록을 간결하게 표기한다."""
    text = str(value or "")
    return text[:10] if len(text) >= 10 else (text or "-")


def _render_category_article(art: dict) -> str:
    """카테고리 드릴다운 한 줄 — 제목(원문 링크)/중요도/출처/품질/시각/액션 + 시사점."""
    url = art.get("url") or ""
    title = _display(art.get("title") or "")
    title_html = (_source_link(url, f"{title} ↗") if _is_http(url) else escape(title))
    meta = [escape(_display(art.get("display_source") or art.get("source") or "출처 미상"))]
    sq = art.get("source_quality")
    if sq in ("trusted", "low"):
        cls = "srcq trust" if sq == "trusted" else "srcq low"
        meta.append(f'<span class="{cls}">{escape(_display(art.get("source_quality_label") or ""))}</span>')
    meta.append(f'<span class="num">{escape(_fmt_date(art.get("published_at")))}</span>')
    parts = [
        '<article class="cd-art">',
        '<div class="cd-art-head">',
        f'<span class="cd-title">{title_html}</span>',
        f'<span class="cd-sc num">중요도 {_fmt5(art.get("final_score"))} / 5.0</span>',
        '</div>',
        f'<p class="cd-meta">{" · ".join(meta)}</p>',
    ]
    if art.get("why_it_matters"):
        parts.append(f'<p class="cd-why">{escape(_display(art["why_it_matters"]))}</p>')
    parts.append('</article>')
    return "".join(parts)


def _render_category_drilldown(brief: dict) -> list[str]:
    """카테고리별 근거 기사 섹션 — 네이티브 <details>/<summary>만 사용 (JS·CDN 0건).

    수집·분석된 기사를 카테고리별로 펼쳐 근거(제목·출처·중요도·원문 링크)를 감사할 수
    있게 한다. 본문 전문은 싣지 않는다 (brief 파생 요약만 렌더링).
    """
    sections = brief.get("category_sections") or []
    total = sum(s.get("total_count", 0) for s in sections)
    body = ['<section aria-label="카테고리별 근거 기사">',
            '<h2 class="sec-h">카테고리별 근거 기사'
            f'<span class="tag">수집·분석 {total}건 · 모든 분류 기본 접힘</span></h2>']
    if not sections:
        body.append('<p class="macro-note">집계된 카테고리가 없습니다.</p></section>')
        return body
    # 모든 카테고리는 기본 접힘 — 운영자가 필요한 분류만 펼쳐 근거를 확인한다 (자동 펼침 없음).
    body.append('<p class="cd-note">카테고리를 펼쳐 근거 기사를 확인하세요. '
                '(모든 분류는 기본 접힘 상태입니다.)</p>')
    body.append(f'<p class="cd-note">{escape(brief.get("category_drilldown_note") or "")}</p>')
    for sec in sections:
        imm = (f'<span class="cd-imm">즉시 {sec["instant_count"]}</span>'
               if sec.get("instant_count") else "")
        body.append('<details class="cat-drill">')
        body.append(
            '<summary>'
            f'<span class="cd-label">{escape(sec.get("category_label") or "")}</span>'
            f'<span class="cd-count num">{sec.get("total_count", 0)}건</span>'
            f'{imm}<span class="cd-flex"></span></summary>')
        body.append('<div class="cd-body">')
        arts = sec.get("top_articles") or []
        if arts:
            body += [_render_category_article(a) for a in arts]
        else:
            body.append('<p class="cd-empty">표시 가능한 뉴스 출처 근거가 없습니다 '
                        '(블로그·카페 등 비-뉴스 출처만 수집됨).</p>')
        if sec.get("note"):
            body.append(f'<p class="cd-more">{escape(sec["note"])}</p>')
        body.append('</div></details>')
    body.append('</section>')
    return body


def _render_audit_article(art: dict) -> str:
    """참고/제외·출처 품질 감사 항목 한 줄 — 제목/출처/중요도(또는 미채점)/사유 + 원문 링크.

    출처 품질 제외 항목은 '비뉴스성/낮은 신뢰 출처' 라벨을 항목마다 명시한다.
    수집 단계 제외(미채점) 항목은 중요도 대신 상태를 표기한다. 본문 전문은 싣지 않는다.
    """
    url = art.get("url") or ""
    title = _display(art.get("title") or "")
    title_html = (_source_link(url, f"{title} ↗") if _is_http(url) else escape(title))
    meta = [escape(_display(art.get("display_source") or art.get("source") or "출처 미상"))]
    audit_label = art.get("audit_label")
    if audit_label:  # 출처 품질 제외 — 비뉴스성/낮은 신뢰 출처임을 항목마다 명시
        meta.append(f'<span class="srcq low">{escape(_display(audit_label))}</span>')
    else:
        sq = art.get("source_quality")
        if sq in ("trusted", "low"):
            cls = "srcq trust" if sq == "trusted" else "srcq low"
            meta.append(f'<span class="{cls}">{escape(_display(art.get("source_quality_label") or ""))}</span>')
    if art.get("category_label"):
        meta.append(escape(_display(art["category_label"])))
    if art.get("published_at"):
        meta.append(f'<span class="num">{escape(_fmt_date(art.get("published_at")))}</span>')
    score = art.get("final_score")
    score_html = (f'중요도 {_fmt5(score)} / 5.0' if score is not None
                  else '수집 단계 제외 · 미채점')
    reason = art.get("why_it_matters") or art.get("source_quality_reason")
    parts = [
        '<article class="cd-art">',
        '<div class="cd-art-head">',
        f'<span class="cd-title">{title_html}</span>',
        f'<span class="cd-sc num">{score_html}</span>',
        '</div>',
        f'<p class="cd-meta">{" · ".join(meta)}</p>',
    ]
    if reason:
        parts.append(f'<p class="cd-why">{escape(_display(reason))}</p>')
    parts.append('</article>')
    return "".join(parts)


def _render_audit_details(label: str, bucket: dict, empty_text: str) -> list[str]:
    """감사용 <details> 한 묶음 (기본 접힘) — 헤더 카운트 + 안내 + 항목 + '외 n건'."""
    items = bucket.get("items") or []
    total = bucket.get("total_count", len(items))
    body = ['<details class="cat-drill">',
            '<summary>'
            f'<span class="cd-label">{escape(label)}</span>'
            f'<span class="cd-count num">{total}건</span>'
            '<span class="cd-flex"></span></summary>',
            '<div class="cd-body">']
    if bucket.get("note"):
        body.append(f'<p class="cd-more">{escape(bucket["note"])}</p>')
    if items:
        body += [_render_audit_article(a) for a in items]
    else:
        body.append(f'<p class="cd-empty">{escape(empty_text)}</p>')
    remaining = bucket.get("remaining_count", 0)
    if remaining:
        body.append(f'<p class="cd-more">외 {remaining}건</p>')
    body.append('</div></details>')
    return body


def _render_support_evidence(articles: list) -> str:
    """리스크 사건 근거 기사 묶음 — url이 있으면 새 탭 링크(↗), 없으면 평문 fallback (P0-D3P).

    임원·운영자 양쪽 뷰가 공유한다. 각 제목은 _source_link(앵커) 또는 escape로 개별
    이스케이프하므로, 조립된 HTML 문자열을 다시 escape하지 않는다 (이중 escape 방지).
    링크는 기사 원문 href만 — 외부 스크립트/리소스는 도입하지 않는다.
    """
    links = []
    for a in articles:
        if not isinstance(a, dict):
            continue
        title = a.get("title")
        if not title:
            continue
        url = a.get("url") or ""
        if _is_http(url):
            links.append(f'{_source_link(url, title)} ↗')
        else:
            links.append(escape(_display(title)))
    return " / ".join(links)


def _render_risk_event_clusters(brief: dict, audience: str = "operator") -> list[str]:
    """리스크 사건 클러스터 — 기사 카드와 별도인 event-level 검토 렌즈 (P0-D3O/D3P).

    operator: 운영자 확인·발송불가 등 운영 메커니즘을 그대로 노출한다 (검증용 감사 뷰).
    executive: '주요 리스크 사건'으로 리네이밍하고 운영자 전용 설명(운영자 확인용 요약·
    발송불가·운영자 확인)을 임원 친화 '확인 필요: 기관…'으로 바꾼다. 근거 기사 링크는 공통.
    """
    executive = audience == "executive"
    clusters = brief.get("risk_event_clusters") or []
    # P0-D3S Goal D: 임원 뷰는 주요 리스크 사건을 최대 5건(고심각 send_candidate가 없으면
    # 최대 3건)만 노출하고 나머지는 운영자 검수 화면으로 분리한다. 운영자 뷰는 전부 노출한다.
    # (시장·증권성 항목은 risk_events 단계에서 이미 클러스터로 잡히지 않으므로 카운트되지 않는다.)
    hidden_count = 0
    if executive and clusters:
        high_severity = any(ev.get("severity") == "send_candidate" for ev in clusters)
        cap = 5 if high_severity else 3
        if len(clusters) > cap:
            hidden_count = len(clusters) - cap
            clusters = clusters[:cap]
    if executive:
        section_label = "주요 리스크 사건"
        intro = "동일 이슈로 보이는 기사들을 묶어 주요 영향과 근거를 요약했습니다."
    else:
        section_label = "리스크 사건 클러스터"
        intro = ("동일 사건으로 보이는 리스크 기사를 묶은 운영자 확인용 요약입니다. "
                 "발송은 사람 검토 전까지 허용되지 않습니다.")
    body = [
        f'<section aria-label="{escape(section_label)}">',
        '<details class="cat-drill">',
        '<summary>'
        f'<span class="cd-label">{escape(section_label)}</span>'
        f'<span class="cd-count num">{len(clusters)}건</span>'
        '<span class="cd-flex"></span></summary>',
        '<div class="cd-body">',
        f'<p class="cd-note">{escape(intro)}</p>',
    ]
    if hidden_count:
        body.append('<p class="cd-note">추가 관찰 신호는 운영자 검수 화면에 분리했습니다.</p>')
    if not clusters:
        body.append('<p class="cd-empty">묶인 리스크 사건이 없습니다.</p>')
    for ev in clusters:
        sources = ev.get("sources") or []
        source_names = [s.get("name") for s in sources if isinstance(s, dict)]
        axes = " · ".join(ev.get("impact_axes") or []) or "영향축 확인 필요"
        counts = f"기사 {ev.get('article_count', 0)} / 출처 {ev.get('source_count', 0)}"
        support_arts = [a for a in (ev.get("supporting_articles") or [])[:5]
                        if isinstance(a, dict) and a.get("title")]
        evidence_html = _render_support_evidence(support_arts)
        body += [
            '<article class="cd-art">',
            '<div class="cd-art-head">',
            f'<span class="cd-title">{escape(_display(ev.get("event_title") or ""))}</span>',
            f'<span class="cd-sc num">{escape(_display(ev.get("severity_label") or ""))}</span>',
            '</div>',
            f'<p class="cd-meta">{escape(_display(counts))} · '
            f'{escape(_display(" / ".join(source_names) or "출처 확인 필요"))}</p>',
            f'<p class="cd-why"><strong>영향</strong> {escape(_display(axes))}</p>',
        ]
        if executive:
            # 임원 뷰: 운영자 메커니즘 대신 '확인 필요: 기관…' 한 줄 (발송불가 표기 없음).
            targets = ev.get("operator_confirmation_targets") or []
            confirm = " / ".join(t for t in targets if t) or "공식자료"
            body.append(
                f'<p class="cd-why"><strong>확인 필요</strong> {escape(_display(confirm))}</p>')
        else:
            confirm = ev.get("operator_confirmation_note") or "운영자 확인 필요"
            if not ev.get("send_allowed"):
                confirm = f"{confirm} · 발송불가"
            body.append(
                f'<p class="cd-why"><strong>운영자 확인</strong> {escape(_display(confirm))}</p>')
        if evidence_html:
            body.append(f'<p class="cd-why"><strong>근거</strong> {evidence_html}</p>')
        body.append('</article>')
    body.append('</div></details></section>')
    return body


def _render_audit_sections(brief: dict) -> list[str]:
    """참고/제외 기사 + 출처 품질 제외 결과 (감사 전용, 둘 다 기본 접힘) — P0-C1.8.

    두 기준을 분리해 보여준다: 참고/제외=낮은 관련성 뉴스, 출처 품질 제외=비뉴스성 출처.
    카운트만 보이던 버킷을 운영자가 직접 들여다볼 수 있게 한다.
    """
    review = brief.get("review_excluded_evidence") or {}
    filtered = brief.get("source_filtered_evidence") or {}
    body = [
        '<section aria-label="참고/제외 및 출처 품질 감사">',
        '<h2 class="sec-h">참고/제외 · 출처 품질 감사'
        '<span class="tag">낮은 우선순위·비뉴스 출처 점검 · 기본 접힘</span></h2>',
        '<p class="cd-note">참고/제외 기사는 정상 뉴스이지만 관련성·우선순위가 낮은 기사이고, '
        '출처 품질 제외는 블로그·카페 등 비뉴스성 출처입니다 — 서로 다른 기준으로 분리해 표시합니다.</p>',
    ]
    body += _render_audit_details(
        "참고/제외 기사", review,
        "참고/제외로 분류된 뉴스 기사가 없습니다.")
    body += _render_audit_details(
        "출처 품질 제외 결과", filtered,
        "출처 품질로 제외된 비뉴스성 출처 결과가 없습니다 "
        "(또는 수집 단계에서 제외되어 표시할 항목이 없습니다).")
    body.append('</section>')
    return body


def _render_exposure_audit(brief: dict) -> list[str]:
    """운영자 점검: 노출 품질·중복 제어 (P0-D3F) — 기본 접힘 <details>, 임원 카드와 분리.

    어떤 기사가 어느 surface에 노출/억제됐는지 한국어 라벨로 보여준다. 운영자 감사용이므로
    compact raw 키(cluster/penalty/flags)도 한국어 라벨과 함께 노출할 수 있다 — 단 임원 상단
    카드에는 절대 raw 키를 노출하지 않는다(이 섹션 안에서만). 본문 전문·외부 링크는 없다.
    """
    audit = brief.get("exposure_quality_audit") or {}
    items = audit.get("items") or []
    status_labels = audit.get("status_labels") or {}
    total = audit.get("total_count", len(items))
    body = [
        '<section aria-label="운영자 점검: 노출 품질·중복 제어">',
        '<details class="cat-drill">',
        '<summary>'
        '<span class="cd-label">운영자 점검: 노출 품질·중복 제어</span>'
        f'<span class="cd-count num">{total}건</span>'
        '<span class="cd-flex"></span></summary>',
        '<div class="cd-body">',
        f'<p class="cd-note">{escape(audit.get("note") or "")}</p>',
    ]
    if not items:
        body.append('<p class="cd-empty">노출 억제·품질 사유가 있는 기사가 없습니다.</p>')
    for it in items:
        status_ko = status_labels.get(
            it.get("exposure_surface_status") or "", it.get("exposure_surface_status") or "")
        title = escape(_display(it.get("title") or ""))
        meta = [escape(_display(it.get("display_source") or it.get("source") or "출처 미상"))]
        if it.get("published_at"):
            meta.append(f'<span class="num">{escape(_fmt_date(it.get("published_at")))}</span>')
        score = it.get("final_score")
        score_html = (f'중요도 {_fmt5(score)} / 5.0' if score is not None else '미채점')
        chips = "".join(
            f'<span class="srcq low">{escape(_display(lbl))}</span>'
            for lbl in (it.get("suppression_reason_labels") or []))
        # compact raw 키 — 한국어 라벨(위 chips)과 함께만 노출하는 운영자 감사용 한 줄.
        raw_parts = [f'cluster={it.get("exposure_cluster_key")}',
                     f'penalty={it.get("top_exposure_penalty")}']
        flags = it.get("top_exposure_flags") or []
        if flags:
            raw_parts.append('flags=' + ",".join(flags))
        rep = it.get("representative_article_id")
        if rep:
            raw_parts.append(f'rep={rep}')
        body += [
            '<article class="cd-art">',
            '<div class="cd-art-head">',
            f'<span class="cd-title">{title}</span>',
            f'<span class="cd-sc num">{score_html}</span>',
            '</div>',
            f'<p class="cd-meta"><strong>{escape(status_ko)}</strong> · {" · ".join(meta)}</p>',
        ]
        if chips:
            body.append(f'<p class="labels">{chips}</p>')
        body.append(f'<p class="cd-meta num">{escape(" · ".join(raw_parts))}</p>')
        body.append('</article>')
    remaining = audit.get("remaining_count", 0)
    if remaining:
        body.append(f'<p class="cd-more">외 {remaining}건</p>')
    body.append('</div></details></section>')
    return body


def _render_macro_section(brief: dict) -> list[str]:
    """Macro Snapshot 섹션 — live가 아닌 한 수치를 렌더링하지 않는다 (P0-B6)."""
    macro = brief.get("macro_snapshot") or {}
    mode = macro.get("macro_data_mode") or brief.get("macro_data_mode") or "unavailable"
    values = macro.get("values") or []

    body = ['<section aria-label="Macro Snapshot">']
    if mode == "live" and values:
        # 기준시각은 KST 벽시계로 표기한다 — Yahoo의 UTC(+00:00)를 그대로 노출하지 않는다 (P0-D1.5).
        updated_kst = _fmt_kst(macro.get("updated_at"))
        body.append('<h2 class="sec-h">MACRO SNAPSHOT'
                    f'<span class="tag">출처 {escape(str(macro.get("source") or ""))}'
                    f' · 시장지표 참고시각 {escape(updated_kst)} (KST 기준)</span></h2>')
        body.append('<div class="macro-grid">')
        for v in values:
            arrow, dir_class = DIRECTION_ARROW.get(v.get("direction"), ("", ""))
            arrow_html = f' <span class="{dir_class}">{arrow}</span>' if arrow else ""
            body.append(f'<div class="macro-cell live"><div class="lbl">{escape(v["label"])}</div>'
                        f'<div class="val num">{escape(str(v["value"]))}{escape(v.get("unit", ""))}'
                        f'{arrow_html}</div></div>')
        body.append('</div>')
        body.append(f'<p class="macro-src">{escape(str(macro.get("disclaimer") or ""))}</p>')
        body.append('<p class="macro-src">시장지표는 Yahoo Finance 참고값이며 '
                    '거래용 실시간 시세가 아닙니다 (투자 판단 근거 아님).</p>')
    else:
        labels = [v.get("label") for v in values if v.get("label")] or DEFAULT_MACRO_LABELS
        body.append('<h2 class="sec-h">MACRO SNAPSHOT'
                    '<span class="tag">시장지표 준비 중</span></h2>')
        body.append('<p class="macro-note">시장지표는 다음 단계에서 연동 예정입니다 (준비 중) — '
                    '현재 시장값이 아니므로 수치를 표시하지 않습니다. '
                    '<span class="num">[미연동]</span></p>')
        body.append('<div class="macro-grid">')
        for label in labels:
            body.append(f'<div class="macro-cell"><div class="lbl">{escape(label)}</div>'
                        '<div class="val">미연동</div></div>')
        body.append('</div>')
    body.append('</section>')
    return body


TAB_ITEMS = [
    ("hdec", "radar-tab-hdec", "hdec-radar", "현대건설 연관"),
    ("ai", "radar-tab-ai", "ai-radar", "AI 관련"),
    ("biz", "radar-tab-biz", "biz-radar", "수주·해외"),
    ("risk", "radar-tab-risk", "risk-radar", "리스크·규제"),
    ("comp", "radar-tab-comp", "comp-radar", "경쟁사·공급망"),
    ("macro", "radar-tab-macro", "macro", "거시경제"),
    ("evidence", "radar-tab-evidence", "evidence", "전체 근거"),
]


def _render_tabs_ui(panels: dict[str, list[str]], default_key: str) -> list[str]:
    """상단 탭/필터 — radio+label CSS만 사용해 앵커 점프 없이 패널을 전환한다."""
    out = ['<section class="radar-tabs-ui" aria-label="레이더 탭 필터">']
    for key, input_id, panel_id, label in TAB_ITEMS:
        checked = " checked" if key == default_key else ""
        out.append(
            f'<input class="radar-tab-input" type="radio" name="radar-tab" '
            f'id="{input_id}" aria-controls="{panel_id}"{checked}>')
    out.append('<nav class="topnav" aria-label="레이더 필터" role="tablist">')
    for key, input_id, _panel_id, label in TAB_ITEMS:
        selected = "true" if key == default_key else "false"
        out.append(f'<label for="{input_id}" role="tab" aria-selected="{selected}">'
                   f'{escape(label)}</label>')
    out.append('</nav>')
    out.append('<div class="radar-panels">')
    for key, _input_id, _panel_id, _label in TAB_ITEMS:
        out += panels.get(key, [])
    out.append('</div></section>')
    return out


def _render_visible_radar(section_id: str, heading: str, tag: str,
                          signals: list, lead: bool = False,
                          risk_mode: bool = False, empty: str = "") -> list[str]:
    """항상 보이는 레이더 섹션(AI/리스크·규제/수주·해외) — <section>로 첫 화면에 노출."""
    cls = "radar-panel radar lead" if lead else "radar-panel radar"
    out = [f'<section id="{section_id}" class="{cls}" aria-label="{escape(heading)}">',
           f'<h2 class="sec-h">{escape(heading)}'
           f'<span class="tag">{escape(tag)}</span></h2>']
    out += _render_radar_signals(signals, risk_mode=risk_mode, empty_text=empty)
    out.append('</section>')
    return out


def _render_themes_block(brief: dict) -> list[str]:
    """주요 테마 — '상대 강도' 대신 '관련 기사 n건' + 테마 비중 막대 (P0-C1.9)."""
    themes = brief.get("theme_rankings") or []
    out = ['<section aria-label="주요 테마"><h2 class="sec-h">주요 테마'
           '<span class="tag">테마 비중 · 100=최상위 테마</span></h2>']
    if themes:
        for t in themes:
            rel = t.get("relative_strength") or 1
            out.append(
                '<div class="theme-row"><div class="theme-name">'
                f'<span>{t["rank"]}. {escape(t["theme"])}</span>'
                f'<span class="cnt num">관련 기사 {t["count"]}건</span>'
                f'</div><div class="theme-bar"><span style="width:{max(7, rel)}%"></span></div></div>')
        out.append(f'<p class="caption">{escape(brief.get("theme_strength_note") or "")}</p>')
    else:
        out.append('<p class="macro-note">집계된 테마가 없습니다.</p>')
    out.append('</section>')
    return out


def _render_categories_block(brief: dict) -> list[str]:
    categories = brief.get("category_counts") or []
    out = ['<section aria-label="카테고리 요약"><h2 class="sec-h">카테고리 요약</h2>']
    if categories:
        for c in categories:
            imm = (f'<span class="imm">즉시 {c["immediate"]}</span>'
                   if c.get("immediate") else '')
            out.append(f'<div class="cat-row"><span>{escape(c["label"])}</span>'
                       f'<span class="lead"></span>'
                       f'<span class="cnt num">{c["count"]}건</span>{imm}</div>')
    else:
        out.append('<p class="macro-note">집계된 카테고리가 없습니다.</p>')
    out.append('</section>')
    return out


def _render_macro_panel(brief: dict, signals: list) -> list[str]:
    out = ['<section id="macro" class="radar-panel radar macro-section" aria-label="거시경제">',
           '<h2 class="sec-h">거시경제'
           '<span class="tag">FX·금리·원자재 등</span></h2>']
    out += _render_radar_signals(
        signals, empty_text="거시경제 단독 신호 없음 — 아래 시장지표 상태 참고")
    out += _render_macro_section(brief)
    out.append('</section>')
    return out


def _render_evidence_panel(brief: dict, audience: str = "operator") -> list[str]:
    # executive 뷰는 참고/제외·출처 품질 감사(운영자 전용 점검)를 노출하지 않는다 (P0-D3P).
    executive = audience == "executive"
    tag = "테마·카테고리·근거 기사" if executive else "테마·카테고리·근거 기사·참고/제외 감사"
    out = ['<section id="evidence" class="radar-panel radar evidence-section" aria-label="전체 근거">',
           '<h2 class="sec-h">전체 근거'
           f'<span class="tag">{escape(tag)}</span></h2>',
           '<div class="duo">']
    out += _render_themes_block(brief)
    out += _render_categories_block(brief)
    out.append('</div>')
    out += _render_category_drilldown(brief)
    if not executive:
        out += _render_audit_sections(brief)
    out.append('</section>')
    return out


def render_report_html(brief: dict, audience: str = "operator") -> tuple[str, list[str]]:
    """brief 구조체를 standalone HTML로 렌더링한다. (html, 포함된 섹션 키) 반환.

    IA (P0-C1.9~P0-C3): 헤더 → 현황판 → Executive Signal → 상단 탭 필터 →
    선택된 레이더 패널만 노출. 전체 근거는 탭 패널 안에서만 열린다.

    audience (P0-D3P): 'operator'(기본)는 운영자 감사 상세(참고/제외·출처 품질 제외·
    운영자 점검·운영자 확인·발송불가)를 그대로 노출한다. 'executive'는 그 운영자 전용
    블록·문구를 숨기고 리스크 사건을 임원 친화 요약으로 보여준다. 기본값은 운영자 호환.
    """
    executive = audience == "executive"
    sections = ["hero", "status_board", "one_liner"]
    # 헤더 — 노이즈 출처/시장지표 표기는 제거하고 footer로 내린다 (Phase 6).
    body = [
        '<header class="masthead">',
        '<div class="mast-row">',
        '<span class="brand">HDEC Executive Radar</span>',
        f'<span class="mode-pill">{escape(_mode_pill(brief))}</span>',
        '</div>',
        '<h1>Executive Daily Brief</h1>',
        f'<p class="dateline num">{escape(brief["date_kst"])} (KST) · 임원용 시그널 레이더 일일 브리프</p>',
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
        f'<p>{escape(_display(brief["executive_one_liner"]))}</p>',
        '</section>',
    ]

    # 1) 현대건설 직접 영향 — 임원 의사결정 최상위 섹션 (Executive Signal 직후 첫 신호).
    # AI보다 먼저 노출한다 (P0-C1.12: 제품 목표를 'AI 수집'에서 '현대건설 의사결정'으로).
    hdec_sigs = brief.get("hdec_direct_signals") or []
    panels = {}
    panels["hdec"] = _render_visible_radar(
        "hdec-radar", "현대건설 연관",
        "현대건설 수주·전략·조직·리스크 · 의사결정 우선순위", hdec_sigs, lead=True,
        empty="오늘 현대건설 연관 신호 없음 — 아래 산업 신호 확인")
    sections.append("hdec_direct")

    # 2) AI 관련
    ai_sigs = brief.get("ai_radar_signals") or []
    # P0-D3S Goal C: 라벨을 '점수순'에서 '종합 우선순위순'으로 정정한다. 순서는 표시 중요도
    # 점수만이 아니라 관련성·출처·최신성을 함께 반영하므로(증권/공급사/오래된 기사 강등),
    # 라벨에 정렬 기준을 명시해 '점수순인데 2.5가 4.1 위'라는 혼선을 없앤다.
    panels["ai"] = _render_visible_radar(
        "ai-radar", "AI 관련",
        "AI 데이터센터·전력·SMR·스마트건설 · 종합 우선순위순(관련성·중요도·출처·최신성)",
        ai_sigs, empty="오늘 AI 인프라·건설 AI 신호 없음")
    if ai_sigs:
        sections.append("ai_radar")
        sections.append("top_signals")  # 메타데이터 backward-compat 별칭

    # 3) 수주·해외·발주 환경 — 확정 계약뿐 아니라 중동·재건·EPC·DC·SMR 발주 환경까지.
    biz_sigs = brief.get("business_signals") or []
    panels["biz"] = _render_visible_radar(
        "biz-radar", "수주·해외·발주 환경",
        "수주·발주·해외·플랜트·EPC·중동 · 의사결정순", biz_sigs,
        empty="오늘 두드러진 수주·해외·발주 환경 신호 없음")
    if biz_sigs:
        sections.append("business_radar")

    # 4) 리스크·규제 — 중요도가 낮아도 중대재해·규제를 분명히 노출
    risk_sigs = brief.get("risk_regulation_signals") or []
    panels["risk"] = _render_visible_radar(
        "risk-radar", "리스크·규제",
        "중대재해·안전·규제 · 리스크 우선도순", risk_sigs, risk_mode=True,
        empty="오늘 두드러진 안전·규제 리스크 신호 없음")
    if risk_sigs:
        sections.append("risk_radar")

    # 5) 경쟁사·공급망 — 경쟁 건설사 EPC/DC/SMR 전략 + 전력·냉각·전선 공급망 신호
    comp_sigs = brief.get("competitor_supply_signals") or []
    panels["comp"] = _render_visible_radar(
        "comp-radar", "경쟁사·공급망",
        "경쟁 건설사·전력/공급망 · 의사결정순", comp_sigs,
        empty="오늘 두드러진 경쟁사·공급망 신호 없음")
    if comp_sigs:
        sections.append("competitor_radar")

    # 6) 거시경제 — 탭 패널. 기본 선택이 아니면 첫 화면을 점유하지 않는다.
    sections.append("macro")
    macro_sigs = brief.get("macro_economy_signals") or []
    panels["macro"] = _render_macro_panel(brief, macro_sigs)

    # 7) 전체 근거 — 탭 패널. 기본 선택이 아니므로 긴 근거 목록을 첫 화면에 덤프하지 않는다.
    sections += ["themes", "categories", "evidence"]
    if brief.get("category_sections"):
        sections.append("category_drilldown")
    # 참고/제외·출처 품질 감사는 운영자 전용 — executive 뷰에서는 렌더하지 않는다 (P0-D3P).
    if not executive:
        sections.append("audit_evidence")
    panels["evidence"] = _render_evidence_panel(brief, audience)
    default_tab = "hdec" if hdec_sigs else "ai"
    body += _render_tabs_ui(panels, default_tab)

    # D3O: 기사별 리스크 카드와 별개로, 같은 사건을 하나로 묶은 검토 렌즈.
    # D3P: operator는 '리스크 사건 클러스터', executive는 '주요 리스크 사건'으로 렌더.
    sections.append("risk_events")
    body += _render_risk_event_clusters(brief, audience)

    # 운영자 점검: 노출 품질·중복 제어 (P0-D3F) — 탭 밖, 본문 하단의 기본 접힘 감사 블록.
    # 임원 카드는 깨끗하게 두고, 노출 억제/중복/품질 사유는 여기서만 raw 키와 함께 투명화한다.
    # executive 뷰에서는 운영자 전용 점검 블록이므로 노출하지 않는다 (P0-D3P).
    if not executive:
        sections.append("exposure_audit")
        body += _render_exposure_audit(brief)

    sections += ["notes", "footer"]
    # P0-D1.5/D3P: footer 각주. operator는 운영자 검토 고지·출처 품질 가드레일·데이터 출처
    # (mock 표기 포함)·시세 면책을 모두 싣는다. executive는 운영자/품질-제어 설명과 raw
    # 'mock' 토큰을 덜어내고, 자동 생성 고지(추정 안내 유지)·데이터 출처·시세 면책만 남긴다.
    if executive:
        brief_note = (brief.get("operator_note") or "").replace(
            "운영자 검토용 자동 생성", "자동 생성")
        data_src = (brief.get("data_warning") or "").replace("(mock)", "")
        body += [
            '<div class="notes">',
            f'<p>※ {escape(brief_note)}</p>',
            f'<p>※ 데이터 출처 — {escape(data_src)}</p>',
            '<p>※ 시장지표는 참고용이며 거래용 실시간 시세가 아닙니다 (투자 판단 근거 아님).</p>',
            '</div>',
        ]
    else:
        body += [
            '<div class="notes">',
            f'<p>※ {escape(brief["operator_note"])}</p>',
            f'<p>※ {escape(brief.get("source_quality_note") or "")}</p>',
            # 데이터 출처/시장지표 상태 — 뉴스 수집 모드 + 시장지표 출처·참고시각(KST) (정직성 유지).
            f'<p>※ 데이터 출처 — {escape(brief.get("data_warning") or "")}</p>',
            '<p>※ 시장지표는 참고용이며 거래용 실시간 시세가 아닙니다 (투자 판단 근거 아님).</p>',
            '</div>',
        ]
    body += [
        # 생성 시각은 KST 벽시계로 표기한다 (raw +09:00 ISO offset 대신).
        f'<footer class="num">생성 {escape(_fmt_kst(brief["generated_at"]))} KST'
        f' · {escape(brief["header"])}</footer>',
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
        # 보이지 않는 데이터 모드 마커 — 검증기/CI가 live·mock을 결정적으로 판별한다
        # (HTML 주석이라 임원 화면엔 안 보이고, 'LIVE'/'공개 RSS' 노출 없이 정직성 유지).
        f'<!--news-data-mode:{escape(brief.get("news_data_mode") or "mock")}-->',
        '<div class="page">',
        *body,
        '</div>',
        '</body>',
        '</html>',
        '',
    ])
    return html, sections


def report_metadata(brief: dict, html: str, sections: list[str],
                    audience: str = "operator") -> dict:
    """--json용 기계 검증 메타데이터 (HTML 전문은 싣지 않는다)."""
    return {
        "report_title": REPORT_TITLE,
        "audience": audience,
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
        "hdec_direct_count": len(brief.get("hdec_direct_signals") or []),
        "ai_radar_count": len(brief.get("ai_radar_signals") or []),
        "competitor_supply_count": len(brief.get("competitor_supply_signals") or []),
        "risk_radar_count": len(brief.get("risk_regulation_signals") or []),
        "business_radar_count": len(brief.get("business_signals") or []),
        "macro_radar_count": len(brief.get("macro_economy_signals") or []),
        "theme_count": len(brief.get("theme_rankings") or []),
        "category_count": len(brief.get("category_counts") or []),
        "category_section_count": len(brief.get("category_sections") or []),
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
        # 데이터 출처 한 줄은 brief의 data_warning을 그대로 쓴다 (뉴스·시장지표 모드 정직 반영).
        f"{brief['date_kst']} (KST) · {brief.get('data_warning') or 'mock 데이터 기반'}",
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
    # P0-D3P: 대상 뷰. 기본은 operator(운영자 감사 상세 유지=기존 호환). executive는
    # 운영자 전용 설명/감사 블록을 숨긴다. 명시적 CLI 플래그가 REPORT_AUDIENCE env보다 우선.
    env_audience = (os.environ.get("REPORT_AUDIENCE") or "operator").strip().lower()
    if env_audience not in ("operator", "executive"):
        env_audience = "operator"
    parser.add_argument("--audience", choices=["operator", "executive"],
                        default=env_audience,
                        help="리포트 대상 뷰: operator(기본, 운영자 감사 상세 포함) | "
                             "executive(운영자 전용 설명 숨김). env REPORT_AUDIENCE로도 지정 가능.")
    args = parser.parse_args(argv)
    audience = args.audience

    brief = build_brief_via_mock_pipeline()
    html, sections = render_report_html(brief, audience=audience)

    if args.json:
        print(json.dumps(report_metadata(brief, html, sections, audience),
                         ensure_ascii=False, indent=2))
        return 0

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(html, encoding="utf-8")
        # news_data_mode·audience를 함께 출력한다 — CI가 단일 빌드(=단일 RSS 수집) 결과만으로
        # live/mock을 판별해 게시 여부를 정한다 (비밀값 아님, 출력 안전).
        print(f"report written: {out_path} ({len(html)} chars) "
              f"news_data_mode={brief.get('news_data_mode')} audience={audience}")
        return 0

    print(format_summary_text(brief, html, sections))
    if args.dry_run:
        print(f"[dry-run] audience={audience} html_chars={len(html)} "
              f"sections={len(sections)} "
              f"signals={len(brief.get('top_immediate_signals') or [])} (쓰기 없음)",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
