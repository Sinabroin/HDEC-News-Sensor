#!/usr/bin/env python3
"""D6-A/B + D7-B — Executive summary dashboard static export (real article data).

`/dashboard-preview` is served from `templates/dashboard_preview.html` (demo preview).
This builder reuses that checked-in template *shell* but injects the REAL daily-brief
article data into the preview-model island so `docs/daily/dashboard-latest.html` shows
the same collected articles as `docs/daily/latest.html` — both derive from the one
shared brief object (`build_executive_brief.build_brief_via_mock_pipeline`).

Honesty contract is preserved:
- This file itself reads no secrets, opens no socket, and touches no env directly. The
  shared brief pipeline owns collection — mock mode uses a temp DB + mock articles, and
  `NEWS_MODE=live` (workflow) collects public RSS, exactly like `build_static_report.py`.
- Only the news/AI article rows + the featured hero become real. The market / AIS /
  early-signal / theme blocks stay demo (지연/대용/보고/미연동) and keep their labels.
- In live mode the news section is labelled '자동 수집 기사' (real); in mock it stays
  '데모 데이터'. Market preview stays demo in both. The full daily report remains
  `docs/daily/latest.html` and is never replaced by this export.
"""

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from build_executive_brief import build_brief_via_mock_pipeline  # noqa: E402

SOURCE_TEMPLATE = ROOT / "templates" / "dashboard_preview.html"
DEFAULT_OUTPUT = "docs/daily/dashboard-latest.html"
EXPORT_TITLE = "HDEC Executive Radar — 요약 대시보드"
EXPORT_MARKER = "dashboard-export:summary"
_KST = timezone(timedelta(hours=9))

# 템플릿 nav의 data-filter와 1:1 — 생성된 행의 lens 키는 반드시 이 집합 안에 있어야 한다
# (유효하지 않은 키는 어떤 nav 필터에도 걸리지 않아 죽은 태그가 된다).
VALID_LENS = {
    "now", "new", "ai", "civil_infrastructure", "building_housing", "plant",
    "new_energy", "development_business", "global_business", "safety_quality",
    "hyundai_group", "competitor_contractors", "trust_companies", "developers",
    "oil_energy", "hormuz", "domestic_site", "overseas_site", "overseas_branch",
    "overseas_subsidiary",
}

# lens 키 → 한국어 라벨 (featured 칩용). nav 라벨과 일치.
_LENS_LABEL = {
    "now": "즉시 확인", "new": "신규 이슈", "ai": "AI 신호",
    "civil_infrastructure": "토목", "building_housing": "건축주택", "plant": "플랜트",
    "new_energy": "New Energy", "development_business": "개발사업", "global_business": "글로벌",
    "safety_quality": "안전·품질", "hyundai_group": "현대 그룹사",
    "competitor_contractors": "경쟁 시공사", "trust_companies": "신탁사",
    "developers": "시행사·디벨로퍼", "oil_energy": "유가·에너지", "hormuz": "호르무즈",
    "domestic_site": "국내현장", "overseas_site": "해외현장", "overseas_branch": "해외지사",
    "overseas_subsidiary": "해외법인",
}

# 머신 category → 기본 렌즈 (briefing이 분류한 결정 카테고리)
_CATEGORY_LENS = {
    "dc_power": ["ai", "new_energy", "plant"],
    "mideast_overseas": ["global_business", "overseas_site", "plant"],
    "safety": ["safety_quality"],
    "hdec": [],
}
# radar_section → 렌즈
_SECTION_LENS = {
    "ai": ["ai"],
    "business_overseas": ["global_business", "overseas_site"],
    "risk_regulation": ["safety_quality"],
    "competitor_supply": ["competitor_contractors"],
    "macro": ["oil_energy"],
    "hdec_direct": [],
}
# category_label/제목 키워드 → 추가 렌즈 (보수적 보강 — 매핑 불완전 시 fallback)
_KEYWORD_LENS = [
    (("토목", "철도", "광역철도", "도로", "교량", "터널", "SOC"), "civil_infrastructure"),
    (("건축", "주택", "정비", "분양", "공사비", "재건축", "재개발"), "building_housing"),
    (("플랜트", "LNG", "원전", "EPC", "정유", "석유화학", "발전소", "SMR"), "plant"),
    (("SMR", "수소", "전력망", "데이터센터", "신재생", "태양광", "풍력", "전력 인프라"), "new_energy"),
    (("중동", "해외", "글로벌", "사우디", "UAE", "카타르", "네옴", "체코", "유럽", "수출"), "global_business"),
    (("유가", "원유", "WTI", "브렌트", "연료", "정제유"), "oil_energy"),
    (("안전", "중대재해", "특별감독", "규제", "벌점", "산업안전"), "safety_quality"),
    (("경쟁", "수주 경쟁"), "competitor_contractors"),
]

# opportunity_or_risk → (cat 라벨, catColor, tag class)
_KIND = {
    "기회": ("기회", "#3C7A4E", "green"),
    "리스크": ("리스크", "#B85049", "red"),
    "관찰": ("관찰", "#3F6FA8", "sky"),
}
# score_band → (tag 라벨, tag class)
_BAND_TAG = {
    "즉시 확인": ("즉시", "red"),
    "검토 필요": ("검토 필요", "amber"),
    "추적 필요": ("참고", "sky"),
}
# score_components 색 팔레트 (featured 신호 지수)
_COMP_PALETTE = ["#3F6FA8", "#3C7A4E", "#9AAAC1", "#9C7232", "#3F6FA8", "#3C7A4E"]


def _is_http(url) -> bool:
    return bool(url) and str(url).startswith(("http://", "https://"))


def _key(sig) -> str:
    if not sig:
        return ""
    return str(sig.get("article_id") or sig.get("url") or sig.get("title") or "")


def _fmt_time(iso) -> str:
    """published_at → KST 'MM-DD HH:MM' (결정적 절대시각, now() 의존 없음)."""
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return str(iso)[:16]
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_KST).strftime("%m-%d %H:%M")


def _lens_for(sig) -> list:
    keys = set(_CATEGORY_LENS.get(sig.get("category"), []))
    keys.update(_SECTION_LENS.get(sig.get("radar_section"), []))
    text = f"{sig.get('title') or ''} {sig.get('category_label') or ''}"
    for words, lens in _KEYWORD_LENS:
        if any(w in text for w in words):
            keys.add(lens)
    if sig.get("alert_grade") == "즉시 알림 후보" or sig.get("score_band") == "즉시 확인":
        keys.add("now")
    return sorted(keys & VALID_LENS)


def _row_from_signal(sig, extra_lens=()) -> dict:
    kind = sig.get("opportunity_or_risk") or "관찰"
    cat_label, cat_color, _kind_cls = _KIND.get(kind, ("관찰", "#3F6FA8", "sky"))
    band = sig.get("score_band") or sig.get("alert_grade") or "추적 필요"
    tag, tag_class = _BAND_TAG.get(band, ("참고", "sky"))
    score = sig.get("final_score")
    score_str = "-" if score is None else f"{float(score):.1f}"
    if kind == "리스크":
        score_label, score_color = "리스크", "#B85049"
    elif band == "즉시 확인":
        score_label, score_color = "즉시", "#B85049"
    elif score is not None and float(score) >= 3.5:
        score_label, score_color = "중요", "#5E6E8C"
    else:
        score_label, score_color = "관찰", "#5E6E8C"
    spread = sig.get("spread") or {}
    related = (f"관련 {spread.get('related_count')}건"
               if spread.get("related_count") else "단독 신호")
    sources = (f"출처 {spread.get('source_count')}곳"
               if spread.get("source_count") else "출처 1곳")
    lens = sorted(set(_lens_for(sig)) | ({l for l in extra_lens} & VALID_LENS))
    return {
        "tag": tag, "tagClass": tag_class,
        "title": sig.get("title") or "",
        "source": sig.get("display_source") or sig.get("source") or "출처 미상",
        "time": _fmt_time(sig.get("published_at")),
        "related": related, "sources": sources,
        "cat": cat_label, "catColor": cat_color,
        "score": score_str, "scoreLabel": score_label, "scoreColor": score_color,
        "lens": lens,
        "url": sig.get("url") if _is_http(sig.get("url")) else "",
    }


def _metric_indices(sig) -> list:
    comps = sig.get("score_components") or []
    out = []
    for i, c in enumerate(comps[:6]):
        try:
            value = round(float(c.get("value") or 0), 1)
        except (TypeError, ValueError):
            value = 0
        out.append({"label": c.get("label") or c.get("key") or "지표",
                    "value": value, "color": _COMP_PALETTE[i % len(_COMP_PALETTE)]})
    return out


def _dedup_signals(*lists) -> list:
    seen, out = set(), []
    for lst in lists:
        for s in lst or []:
            k = _key(s)
            if not k or k in seen:
                continue
            seen.add(k)
            out.append(s)
    return out


def _lens_counts(rows) -> dict:
    counts = {}
    for r in rows:
        for l in r.get("lens") or []:
            counts[l] = counts.get(l, 0) + 1
    return counts


def _derive(brief: dict) -> dict:
    """공유 brief → 대시보드 모델 조각 (featured/news_rows/ai_rows/counts)."""
    immediate = brief.get("top_immediate_signals") or []
    hdec = brief.get("hdec_direct_signals") or []
    new_issues = brief.get("top_new_issues") or []

    featured_sig = (hdec or immediate or [None])[0]
    if featured_sig is None:
        pool0 = _dedup_signals(brief.get("ai_radar_signals"), brief.get("business_signals"),
                               brief.get("risk_regulation_signals"))
        pool0.sort(key=lambda s: float(s.get("final_score") or 0), reverse=True)
        featured_sig = pool0[0] if pool0 else None
    fkey = _key(featured_sig)
    new_keys = {_key(s) for s in new_issues}

    ai_pool = [s for s in (brief.get("ai_radar_signals") or []) if _key(s) != fkey]
    ai_rows = [_row_from_signal(s) for s in ai_pool[:6]]

    pool = _dedup_signals(immediate, hdec, brief.get("business_signals"),
                          brief.get("risk_regulation_signals"),
                          brief.get("competitor_supply_signals"),
                          new_issues, brief.get("macro_economy_signals"))
    pool = [s for s in pool if _key(s) != fkey]
    pool.sort(key=lambda s: float(s.get("final_score") or 0), reverse=True)
    news_rows = [_row_from_signal(s, ["new"] if _key(s) in new_keys else [])
                 for s in pool[:9]]

    featured_row = _row_from_signal(featured_sig) if featured_sig else None
    panel_rows = ([featured_row] if featured_row else []) + news_rows
    lens_counts = _lens_counts(panel_rows)

    nav_counts = dict(lens_counts)
    nav_counts["all"] = len(panel_rows)
    nav_counts["ai"] = len(ai_rows)
    nav_counts.setdefault("now", lens_counts.get("now", 0))
    nav_counts.setdefault("new", lens_counts.get("new", 0))

    return {
        "featured_sig": featured_sig,
        "featured_row": featured_row,
        "news_rows": news_rows,
        "ai_rows": ai_rows,
        "metric_indices": _metric_indices(featured_sig) if featured_sig else [],
        "lens_counts": lens_counts,
        "nav_counts": nav_counts,
        "immediate_n": lens_counts.get("now", 0),
    }


_TRUST_SVG = ('<span class="trust"><svg width="11" height="11" viewBox="0 0 24 24" fill="none">'
              '<path d="M9 12l2 2 4-4" stroke="#3F6FA8" stroke-width="2.2" stroke-linecap="round" '
              'stroke-linejoin="round"></path><circle cx="12" cy="12" r="9" stroke="#3F6FA8" '
              'stroke-width="1.6"></circle></svg>신뢰 출처</span>')
_LINK_SVG = ('<svg width="13" height="13" viewBox="0 0 24 24" fill="none"><path d="M7 17L17 7M17 7H9M17 7v8" '
             'stroke="#3F6FA8" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"></path></svg>')


def _render_featured(sig: dict, row: dict) -> str:
    """featured hero 카드를 실제 최상위 신호로 생성 (data-lens/data-category + 원문 링크)."""
    kind = sig.get("opportunity_or_risk") or "관찰"
    _cl, _cc, kind_cls = _KIND.get(kind, ("관찰", "#3F6FA8", "sky"))
    chip_keys = [l for l in row["lens"] if l not in ("now", "new")][:3]
    chips = "".join(f'<span class="flchip">{escape(_LENS_LABEL.get(k, k))}</span>'
                    for k in chip_keys)
    trust = _TRUST_SVG if sig.get("source_quality") == "trusted" else ""
    url = sig.get("url") or ""
    if _is_http(url):
        link = (f'<a class="extlink" href="{escape(url)}" target="_blank" rel="noopener noreferrer">'
                f'원문 보기 {_LINK_SVG}</a>')
    else:
        link = '<span style="font-size:12px; color:var(--mute4);">원문 링크 없음</span>'
    summary = escape(sig.get("one_line_reason") or sig.get("implication") or "")
    impact = escape(f"{sig.get('category_label') or ''} · {row['cat']} 신호 · 의사결정 관련도 점검")
    action = escape(sig.get("action_label") or "내용 확인 후 담당 라인 검토 배정")
    return (
        f'<article class="card featured" data-lens="{escape(" ".join(row["lens"]))}" '
        f'data-category="{escape(row["cat"])}">'
        '<div class="head"><div style="min-width:0;">'
        f'<div class="tagrow"><span class="tag {escape(row["tagClass"])}">{escape(row["tag"])}</span>'
        f'<span class="tag {kind_cls}">{escape(row["cat"])}</span></div>'
        f'<h2>{escape(sig.get("title") or "")}</h2>'
        f'<div class="meta"><b>{escape(row["source"])}</b><span class="sep">·</span>'
        f'<span>{escape(row["time"])}</span><span class="sep">·</span><span>{escape(row["related"])}</span>'
        f'<span class="sep">·</span><span>{escape(row["sources"])}</span>{trust}</div>'
        f'<div class="featlens"><span class="fllabel">렌즈</span>{chips}</div>'
        '</div>'
        f'<div class="score"><div class="v num" style="color:{row["scoreColor"]};">{escape(row["score"])}'
        f'<small> / 5</small></div><div class="l" style="color:{row["scoreColor"]};">'
        f'{escape(row["scoreLabel"])}</div></div>'
        '</div>'
        '<div class="read">'
        f'<div class="rd"><span class="rdb sum">요약</span><span>{summary}</span></div>'
        f'<div class="rd"><span class="rdb imp">영향</span><span>{impact}</span></div>'
        f'<div class="rd act"><span class="rdb act">액션</span><span>{action}</span></div>'
        '</div>'
        '<div class="idx"><div class="h">신호 지수 · 0–5</div>'
        '<div class="idxgrid" id="idxgrid"></div></div>'
        '<div class="cardfoot">'
        '<div style="display:flex; align-items:center; gap:14px; flex-wrap:wrap;">'
        f'{link}'
        f'<span style="font-size:12px; color:var(--mute4);">연계 {escape(sig.get("category_label") or "")}</span>'
        '</div>'
        '<span style="font-size:11.5px; color:var(--mute4);" class="num">자동 분류 · '
        f'{escape(sig.get("category") or "")}</span>'
        '</div></article>'
    )


def _inject_featured(html: str, featured_html: str) -> str:
    if not featured_html:
        return html
    new, n = re.subn(r'<article class="card featured".*?</article>', lambda _m: featured_html,
                     html, count=1, flags=re.S)
    if n != 1:
        print("ERROR: featured 카드 블록을 찾지 못함 (템플릿 구조 변경?)", file=sys.stderr)
        raise SystemExit(1)
    return new


def _inject_model(html: str, parts: dict, news_mode: str) -> str:
    m = re.search(r'(<script type="application/json" id="preview-model">)(.*?)(</script>)',
                  html, re.S)
    if not m:
        print("ERROR: preview-model JSON island을 찾지 못함", file=sys.stderr)
        raise SystemExit(1)
    model = json.loads(m.group(2))
    model["news_rows"] = parts["news_rows"]
    model["ai_rows"] = parts["ai_rows"]
    if parts["metric_indices"]:
        model["metric_indices"] = parts["metric_indices"]
    meta = dict(model.get("meta") or {})
    meta["news_data_mode"] = news_mode
    meta["demo"] = news_mode != "live"
    meta["collection"] = "자동 수집 (live)" if news_mode == "live" else "수동 · 데모"
    model["meta"] = meta
    counts = parts["lens_counts"]
    for grp in ("business_lens", "ecosystem"):
        for it in model.get(grp) or []:
            if it.get("id") == "hormuz":
                continue  # 미연동 유지 (count=null → '미연동' 배지)
            it["count"] = counts.get(it.get("id"), 0)
    new_json = json.dumps(model, ensure_ascii=False, indent=2)
    return html[:m.start()] + m.group(1) + "\n" + new_json + "\n" + m.group(3) + html[m.end():]


def _update_nav_counts(html: str, counts: dict) -> str:
    """좌측 nav의 정적 카운트 배지를 실제 분포로 갱신 (hormuz는 '미연동' 배지 유지)."""
    out = []
    for line in html.split("\n"):
        mm = re.search(r'data-filter="([^"]+)"', line)
        if mm and 'class="ncount"' in line and mm.group(1) in counts:
            value = counts[mm.group(1)]
            line = re.sub(r'(<span class="ncount">)\d+(</span>)',
                          lambda x: f'{x.group(1)}{value}{x.group(2)}', line, count=1)
        out.append(line)
    return "\n".join(out)


def _update_section_counts(html: str, immediate_n: int, ai_n: int, index_n: int) -> str:
    html = html.replace(
        '<span class="t">A · 즉시 확인</span><span class="ln"></span><span class="c">3건</span>',
        f'<span class="t">A · 즉시 확인</span><span class="ln"></span><span class="c">{immediate_n}건</span>')
    html = html.replace(
        'AI 신호 피드 · AI ONLY</span><span class="ln"></span><span class="c">8건</span>',
        f'AI 신호 피드 · AI ONLY</span><span class="ln"></span><span class="c">{ai_n}건</span>')
    html = html.replace('신호 인덱스 · 전체 21건 (기본 접힘)',
                        f'신호 인덱스 · 전체 {index_n}건 (기본 접힘)')
    return html


def _update_honesty(html: str, news_mode: str) -> str:
    """live면 뉴스 렌즈노트 라벨을 '자동 수집 기사'로 정직 표기 (실기사 ≠ 데모).

    시장/AIS/초기신호 등 데모 미리보기 블록의 '데모 데이터' 라벨은 그대로 둔다(여전히 데모).
    """
    if news_mode == "live":
        html = html.replace(
            '<span class="previewflag" style="margin:0 0 0 8px;">데모 데이터</span>',
            '<span class="previewflag" style="margin:0 0 0 8px;">자동 수집 기사</span>')
    return html


def render_dashboard_html(brief: dict) -> str:
    """공유 brief를 standalone 요약 대시보드 HTML로 렌더 (실기사 데이터 주입)."""
    try:
        html = SOURCE_TEMPLATE.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"ERROR: dashboard template missing: {SOURCE_TEMPLATE}", file=sys.stderr)
        raise SystemExit(1) from exc

    news_mode = brief.get("news_data_mode") or "mock"
    html = html.replace(
        "<title>HDEC Executive Radar — 대시보드 미리보기 (Preview)</title>",
        f"<title>{EXPORT_TITLE}</title>", 1)
    if EXPORT_MARKER not in html:
        html = html.replace(
            "<!DOCTYPE html>\n",
            "<!DOCTYPE html>\n"
            f"<!--{EXPORT_MARKER} source=templates/dashboard_preview.html "
            f"target={DEFAULT_OUTPUT}-->\n"
            # 보이지 않는 데이터 모드 마커 — CI/검증기가 live·mock을 결정적으로 판별한다.
            f"<!--news-data-mode:{news_mode}-->\n", 1)

    parts = _derive(brief)
    if parts["featured_sig"]:
        html = _inject_featured(html, _render_featured(parts["featured_sig"], parts["featured_row"]))
    html = _inject_model(html, parts, news_mode)
    html = _update_nav_counts(html, parts["nav_counts"])
    html = _update_section_counts(html, parts["immediate_n"], len(parts["ai_rows"]),
                                  len(parts["news_rows"]) + len(parts["ai_rows"]))
    html = _update_honesty(html, news_mode)
    return html


def dashboard_metadata(html: str, brief: dict) -> dict:
    """기계 검증용 메타데이터 (HTML 전문은 싣지 않는다)."""
    parts = _derive(brief)
    return {
        "title": EXPORT_TITLE,
        "source_template": str(SOURCE_TEMPLATE.relative_to(ROOT)),
        "default_output": DEFAULT_OUTPUT,
        "html_chars": len(html),
        "has_export_marker": EXPORT_MARKER in html,
        "has_preview_model": 'id="preview-model"' in html,
        "has_data_honesty_labels": (
            "데모 데이터" in html
            and "현재 체결값 아님" in html
            and "미연동" in html
        ),
        "news_data_mode": brief.get("news_data_mode"),
        "news_row_count": len(parts["news_rows"]),
        "ai_row_count": len(parts["ai_rows"]),
        "featured_title": (parts["featured_sig"] or {}).get("title"),
        "uses_real_articles": bool(parts["news_rows"]) and bool(parts["featured_sig"]),
    }


def format_summary(html: str, brief: dict) -> str:
    meta = dashboard_metadata(html, brief)
    return "\n".join([
        "== HDEC Executive Radar — Summary Dashboard Export (real articles) ==",
        f"[source] {meta['source_template']}",
        f"[default_output] {meta['default_output']}",
        f"[news_data_mode] {meta['news_data_mode']} · news_rows={meta['news_row_count']} "
        f"· ai_rows={meta['ai_row_count']}",
        f"[featured] {meta['featured_title']}",
        f"[html_chars] {meta['html_chars']}",
        "[contract] 실기사 주입(brief 공유) · 시장/AIS 데모 라벨 유지 · 전체 리포트=latest.html",
    ])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="HDEC Executive Radar — 정적 요약 대시보드 export 빌더 (실기사 데이터)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true",
                       help="export 요약을 출력한다 (파일 쓰기 없음)")
    group.add_argument("--json", action="store_true",
                       help="기계 검증용 메타데이터 JSON을 출력한다")
    group.add_argument("--output", metavar="PATH",
                       help=f"HTML 파일을 PATH에 생성한다 (예: {DEFAULT_OUTPUT})")
    args = parser.parse_args(argv)

    brief = build_brief_via_mock_pipeline()
    html = render_dashboard_html(brief)

    if args.json:
        print(json.dumps(dashboard_metadata(html, brief), ensure_ascii=False, indent=2))
        return 0

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(html, encoding="utf-8")
        print(f"dashboard written: {out_path} ({len(html)} chars) "
              f"news_data_mode={brief.get('news_data_mode')}")
        return 0

    print(format_summary(html, brief))
    if args.dry_run:
        print(f"[dry-run] html_chars={len(html)} (쓰기 없음)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
