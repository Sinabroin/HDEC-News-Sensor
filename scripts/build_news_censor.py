#!/usr/bin/env python3
"""Build the standalone HDEC News Censor static surface.

The page reuses the sealed executive-brief and publisher-direct policies.  It
does not fetch in the browser, send notifications, mutate production state, or
add itself to an existing page's navigation.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from html import escape
from pathlib import Path
from typing import Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
for item in (ROOT, SCRIPTS):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from app import publisher_direct  # noqa: E402
from build_executive_brief import build_brief_via_mock_pipeline  # noqa: E402

TEMPLATE = ROOT / "templates" / "news_censor.html"
LOGO = ROOT / "docs" / "assets" / "brand" / "hdec-logo.svg"
DEFAULT_OUTPUT_ROOT = ROOT / "docs" / "news-censor"
REFERENCE_SHA256 = "c4a1d129a9e8b6d824b961e2042f345cfc2eb405dcbc488a542e5bc6cee14804"
CONTRACT = "D7-AK-6E-R4-STANDALONE-NEWS-CENSOR"
KST = timezone(timedelta(hours=9))

CATEGORY_LABELS = {
    "all": "홈",
    "biz": "사업영역",
    "peers": "동종사",
    "hdec": "현대그룹",
    "safety": "안전품질",
    "global": "해외지정학",
    "ai": "AI",
}

SURFACE_CATEGORIES = (
    ("top_immediate_signals", ("all",)),
    ("top_new_issues", ("all",)),
    ("hdec_direct_signals", ("hdec",)),
    ("business_signals", ("biz",)),
    ("competitor_contractor_signals", ("peers",)),
    ("competitor_supply_signals", ("peers",)),
    ("risk_regulation_signals", ("safety",)),
    ("ai_radar_signals", ("ai",)),
    ("ai_value_chain_pool", ("ai",)),
    ("macro_economy_signals", ("biz",)),
    ("hyundai_group_signals", ("hdec",)),
    ("trust_company_signals", ("biz",)),
    ("developer_signals", ("biz",)),
)

SECTION_CATEGORY = {
    "hdec_direct": "hdec",
    "ai": "ai",
    "risk_regulation": "safety",
    "order_overseas": "global",
    "competitor_supply": "peers",
    "macro_economy": "biz",
}

KEYWORD_CATEGORIES = {
    "hdec": ("현대건설", "현대엔지니어링", "현대 그룹", "현대그룹"),
    "peers": ("gs건설", "대우건설", "dl이앤씨", "롯데건설", "포스코이앤씨", "삼성물산"),
    "safety": ("안전", "중대재해", "사고", "품질", "하자", "감독", "규제"),
    "global": ("해외", "글로벌", "중동", "사우디", "네옴", "미국", "유럽", "지정학"),
    "ai": (" ai ", "인공지능", "데이터센터", "data center", "smr", "bim", "로봇", "디지털 트윈"),
}

VERDICT_STYLE = {
    "기회": ("기회", "#1E5F8A"),
    "리스크": ("리스크", "#B3372B"),
    "위험": ("리스크", "#B3372B"),
    "관찰": ("관찰", "#68716A"),
}


def _parse_datetime(value: object) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=KST)
    return parsed.astimezone(KST)


def _edition_date(value: str) -> date:
    if value:
        return date.fromisoformat(value)
    return datetime.now(KST).date()


def _metadata(row: Mapping) -> dict:
    value = row.get("source_metadata") or row.get("source_metadata_json") or {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            value = {}
    return dict(value) if isinstance(value, Mapping) else {}


def _category_tokens(row: Mapping, seeds: Iterable[str]) -> set[str]:
    tokens = {"all", *seeds}
    for section in (
        row.get("executive_section"),
        row.get("radar_section"),
        *(row.get("secondary_sections") or []),
    ):
        mapped = SECTION_CATEGORY.get(str(section or ""))
        if mapped:
            tokens.add(mapped)
    text = f" {row.get('title') or ''} {row.get('snippet') or ''} ".casefold()
    for category, keywords in KEYWORD_CATEGORIES.items():
        if any(keyword in text for keyword in keywords):
            tokens.add(category)
    return tokens


def _candidate_rows(brief: Mapping) -> Iterable[tuple[dict, tuple[str, ...]]]:
    for key, categories in SURFACE_CATEGORIES:
        for item in brief.get(key) or []:
            if isinstance(item, Mapping):
                yield dict(item), categories
    for section in brief.get("category_sections") or []:
        if not isinstance(section, Mapping):
            continue
        for item in section.get("top_articles") or []:
            if isinstance(item, Mapping):
                yield dict(item), ("biz",)
    for section in brief.get("accordion_sections") or []:
        if not isinstance(section, Mapping):
            continue
        category = SECTION_CATEGORY.get(str(section.get("key") or ""), "all")
        for item in section.get("articles") or []:
            if isinstance(item, Mapping):
                yield dict(item), (category,)


def _verdict(row: Mapping) -> tuple[str, str]:
    raw = str(row.get("opportunity_or_risk") or "").strip()
    if raw in VERDICT_STYLE:
        return VERDICT_STYLE[raw]
    action = str(row.get("action_label") or row.get("alert_grade") or "")
    if "즉시" in action or "중요" in action:
        return "중요", "#0B6B3A"
    return "관찰", "#68716A"


def _initials(value: object) -> str:
    text = re.sub(r"[^0-9A-Za-z가-힣]", "", str(value or ""))
    return (text[:2] or "HDEC").upper()


def _published_rank(value: object) -> float:
    parsed = _parse_datetime(value)
    return parsed.timestamp() if parsed else 0.0


def build_model(brief: Mapping, *, edition: date, article_limit: int = 24) -> dict:
    """Derive a browser-safe, publisher-only model from the shared brief."""
    merged: dict[str, dict] = {}
    rejected = 0
    for raw, seeds in _candidate_rows(brief):
        assessment = publisher_direct.assess_delivery_eligibility(
            raw,
            relevance_qualified=True,
        )
        if not assessment.eligible:
            rejected += 1
            continue
        canonical = assessment.publisher_url
        key = canonical.casefold().rstrip("/")
        if key in merged:
            merged[key]["categories"].update(_category_tokens(raw, seeds))
            continue
        row = dict(raw)
        row["url"] = canonical
        merged[key] = {
            "row": row,
            "categories": _category_tokens(row, seeds),
        }

    ranked = sorted(
        merged.values(),
        key=lambda item: (
            float(item["row"].get("final_score") or 0),
            _published_rank(item["row"].get("published_at")),
            str(item["row"].get("title") or ""),
        ),
        reverse=True,
    )[: max(1, min(40, article_limit))]

    articles = []
    for index, item in enumerate(ranked):
        row = item["row"]
        published = _parse_datetime(row.get("published_at"))
        verdict, color = _verdict(row)
        url = publisher_direct.publisher_url(row)
        source = str(row.get("display_source") or row.get("source") or "발행처")
        reason = str(
            row.get("why_it_matters")
            or row.get("one_line_reason")
            or row.get("implication")
            or "현대건설 사업 영향과 대응 필요성을 확인합니다."
        )
        article_id = "nc_" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
        articles.append({
            "id": article_id,
            "title": str(row.get("title") or "").strip(),
            "summary": str(row.get("snippet") or "").strip(),
            "source": source,
            "published_at": published.isoformat() if published else "",
            "published_label": published.strftime("%m-%d %H:%M") if published else "시각 미상",
            "verdict": verdict,
            "verdict_color": color,
            "why": reason,
            "url": url,
            "publisher_direct": True,
            "authority_label": "Publisher Direct",
            "categories": sorted(item["categories"], key=lambda token: tuple(CATEGORY_LABELS).index(token)),
            "initials": _initials(source),
            "tint": ("#0B6B3A", "#1E5F8A", "#8F6A2E", "#455B73", "#68716A")[index % 5],
            "score": round(float(row.get("final_score") or 0), 2),
        })

    if not articles:
        raise RuntimeError("no publisher-direct News Censor articles available")

    themes = []
    for theme in (brief.get("theme_rankings") or [])[:5]:
        if isinstance(theme, Mapping):
            themes.append({
                "label": str(theme.get("theme") or "").strip(),
                "count": int(theme.get("count") or 0),
            })

    generated = _parse_datetime(brief.get("generated_at"))
    return {
        "contract": CONTRACT,
        "reference_sha256": REFERENCE_SHA256,
        "edition": edition.isoformat(),
        "edition_label": edition.strftime("%Y.%m.%d"),
        "generated_at": generated.isoformat() if generated else "",
        "generated_label": generated.strftime("%Y-%m-%d %H:%M KST") if generated else "생성시각 미상",
        "news_data_mode": str(brief.get("news_data_mode") or "mock"),
        "source_label": "LIVE · publisher-direct" if brief.get("news_data_mode") == "live" else "DEMO · deterministic fixture",
        "article_count": len(articles),
        "rejected_count": rejected,
        "articles": articles,
        "themes": themes,
        "categories": [
            {"id": key, "label": label, "count": sum(key in row["categories"] for row in articles)}
            for key, label in CATEGORY_LABELS.items()
        ],
    }


def _json_island(value: object) -> str:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _article_card(article: Mapping, *, lead: bool = False) -> str:
    kind = "lead" if lead else "news-card"
    categories = " ".join(escape(token) for token in article["categories"])
    title_tag = "h2" if lead else "h3"
    summary = ""
    if lead and article.get("summary"):
        summary = f'<p class="summary">{escape(str(article["summary"]))}</p>'
    return (
        f'<article class="{kind}" data-article-id="{escape(str(article["id"]))}" '
        f'data-categories="{categories}" tabindex="0" role="button" '
        f'aria-label="기사 읽기: {escape(str(article["title"]))}">'
        f'<span class="thumb" style="--tint:{escape(str(article["tint"]))}" aria-hidden="true">'
        f'{escape(str(article["initials"]))}</span>'
        '<div class="card-body">'
        f'<span class="verdict" style="--verdict:{escape(str(article["verdict_color"]))}">'
        f'{escape(str(article["verdict"]))}</span>'
        f'<{title_tag}>{escape(str(article["title"]))}</{title_tag}>'
        f'{summary}'
        f'<p class="why"><b>Why</b> {escape(str(article["why"]))}</p>'
        f'<p class="source">{escape(str(article["source"]))} · '
        f'{escape(str(article["published_label"]))} · Publisher Direct</p>'
        '</div></article>'
    )


def render_html(model: Mapping) -> str:
    template = TEMPLATE.read_text(encoding="utf-8")
    logo_uri = "data:image/svg+xml;base64," + base64.b64encode(LOGO.read_bytes()).decode("ascii")
    articles = model["articles"]
    lead = _article_card(articles[0], lead=True)
    cards = "\n".join(_article_card(row) for row in articles[1:])
    filters = "".join(
        f'<button class="filter{(" active" if item["id"] == "all" else "")}" '
        f'type="button" data-filter="{escape(item["id"])}" aria-pressed="'
        f'{str(item["id"] == "all").lower()}">{escape(item["label"])}'
        f'<small>{item["count"]}</small></button>'
        for item in model["categories"]
    )
    themes = "".join(
        f'<li><span>{escape(item["label"])}</span><b>{item["count"]}</b></li>'
        for item in model["themes"]
    ) or '<li><span>관측 테마 없음</span><b>0</b></li>'
    mode_class = "live" if model["news_data_mode"] == "live" else "demo"
    warning = (
        "실시간 수집 · 게시자 원문 검증 완료"
        if mode_class == "live"
        else "검증용 데모 기사 · 현재 뉴스가 아님"
    )
    replacements = {
        "{{CONTRACT}}": CONTRACT,
        "{{PAGE_TITLE}}": escape(f'HDEC News Censor · {model["edition_label"]}'),
        "{{LOGO_DATA_URI}}": logo_uri,
        "{{EDITION_LABEL}}": escape(str(model["edition_label"])),
        "{{GENERATED_LABEL}}": escape(str(model["generated_label"])),
        "{{MODE_CLASS}}": mode_class,
        "{{SOURCE_LABEL}}": escape(str(model["source_label"])),
        "{{MODE_WARNING}}": escape(warning),
        "{{ARTICLE_COUNT}}": str(model["article_count"]),
        "{{FILTER_BUTTONS}}": filters,
        "{{LEAD_ARTICLE}}": lead,
        "{{ARTICLE_CARDS}}": cards,
        "{{THEME_ROWS}}": themes,
        "{{MODEL_JSON}}": _json_island(model),
        "{{REFERENCE_SHA256}}": REFERENCE_SHA256,
    }
    for marker, value in replacements.items():
        template = template.replace(marker, value)
    unresolved = re.findall(r"\{\{[A-Z0-9_]+\}\}", template)
    if unresolved:
        raise RuntimeError(f"unresolved template markers: {unresolved}")
    return template


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def build(*, edition: date, article_limit: int = 24) -> tuple[dict, str]:
    brief = build_brief_via_mock_pipeline()
    model = build_model(brief, edition=edition, article_limit=article_limit)
    return model, render_html(model)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the standalone HDEC News Censor page")
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--output-root", type=Path, help="write latest.html and a dated archive")
    output.add_argument("--json", action="store_true", help="print build metadata without writing")
    output.add_argument("--dry-run", action="store_true", help="print a build summary without writing")
    parser.add_argument("--edition-date", default="", help="archive date (YYYY-MM-DD; default KST today)")
    parser.add_argument("--article-limit", type=int, default=24)
    parser.add_argument("--require-live", action="store_true", help="fail closed unless collection mode is live")
    args = parser.parse_args(argv)

    edition = _edition_date(args.edition_date)
    try:
        model, html = build(edition=edition, article_limit=args.article_limit)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"ERROR: News Censor build failed closed: {exc}", file=sys.stderr)
        return 2

    if args.require_live and model["news_data_mode"] != "live":
        print("ERROR: News Censor require-live gate rejected non-live collection", file=sys.stderr)
        return 3

    metadata = {
        "contract": CONTRACT,
        "news_data_mode": model["news_data_mode"],
        "edition": model["edition"],
        "article_count": model["article_count"],
        "rejected_count": model["rejected_count"],
        "publisher_direct_count": sum(row["publisher_direct"] for row in model["articles"]),
        "portal_url_count": publisher_direct.count_portal_urls(model["articles"]),
        "html_chars": len(html),
        "reference_sha256": REFERENCE_SHA256,
    }
    if args.json:
        print(json.dumps(metadata, ensure_ascii=False, indent=2))
        return 0
    if args.output_root:
        root = args.output_root.resolve()
        latest = root / "latest.html"
        archive = root / f"{model['edition']}.html"
        _atomic_write(archive, html)
        _atomic_write(latest, html)
        print(
            f"news censor written: {latest} + {archive} ({len(html)} chars) "
            f"news_data_mode={model['news_data_mode']} articles={model['article_count']}"
        )
        return 0

    print(
        f"{CONTRACT} edition={model['edition']} mode={model['news_data_mode']} "
        f"articles={model['article_count']} portal_urls=0 html_chars={len(html)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
