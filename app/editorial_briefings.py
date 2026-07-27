"""Daily/Weekly editorial briefing domain logic.

The module is deterministic and side-effect free except for the explicit preview
bundle writer. It does not collect news, send mail, mutate production state, or
write under ``docs``. Production orchestration belongs to
``scripts/run_editorial_briefing.py``.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from html import escape
from pathlib import Path
from typing import Iterable, Mapping
from urllib.parse import urlparse

from app import config, news_access

KST = timezone(timedelta(hours=9))
DAILY_REPORT_SUFFIX = "/daily/latest.html"
DAILY_MAX_ARTICLES = 6
WEEKLY_MAX_ARTICLES = 12

_SENTENCE_RE = re.compile(r"(?<=[.!?。！？])\s+")
_WORD_RE = re.compile(r"[0-9A-Za-z가-힣]+")
_STOPWORDS = {
    "관련", "대한", "위한", "통해", "이번", "주요", "발표", "확대", "추진", "전망",
    "시장", "기업", "기술", "산업", "경영", "뉴스", "ai", "the", "and", "for", "with",
}


class EditorialError(RuntimeError):
    """Fail-closed editorial contract violation."""


@dataclass(frozen=True)
class CoverageWindow:
    start: datetime
    end: datetime

    def label(self) -> str:
        return (
            f"{self.start:%Y-%m-%d %H:%M:%S} ~ "
            f"{self.end:%Y-%m-%d %H:%M:%S} KST"
        )


@dataclass(frozen=True)
class EditorialArticle:
    title: str
    summary: str
    source: str
    published_at: datetime
    selected_url: str
    link_kind: str
    link_label: str
    category: str
    image_url: str = ""

    @property
    def published_label(self) -> str:
        return self.published_at.astimezone(KST).strftime("%Y-%m-%d %H:%M KST")


@dataclass(frozen=True)
class RenderedEdition:
    edition_type: str
    edition_key: str
    coverage: CoverageWindow
    html: str
    public_dated_url: str
    public_latest_url: str
    teams_text: str
    teams_html: str
    issue_mode: str
    headline: str
    article_count: int

    @property
    def html_sha256(self) -> str:
        return hashlib.sha256(self.html.encode("utf-8")).hexdigest()


def _as_kst(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise EditorialError("timezone-aware datetime required")
    return value.astimezone(KST)


def daily_coverage(run_at: datetime) -> CoverageWindow:
    current = _as_kst(run_at)
    run_date = current.date()
    return CoverageWindow(
        datetime.combine(run_date - timedelta(days=1), time(7, 0), KST),
        datetime.combine(run_date, time(6, 40), KST),
    )


def weekly_anchor_date(run_at: datetime) -> date:
    current_date = _as_kst(run_at).date()
    days_since_wednesday = (current_date.weekday() - 2) % 7
    return current_date - timedelta(days=days_since_wednesday)


def weekly_coverage(run_at: datetime) -> CoverageWindow:
    anchor = weekly_anchor_date(run_at)
    return CoverageWindow(
        datetime.combine(anchor - timedelta(days=7), time.min, KST),
        datetime.combine(anchor - timedelta(days=1), time(23, 59, 59), KST),
    )


def edition_key(edition_type: str, run_at: datetime) -> str:
    if edition_type == "daily":
        return _as_kst(run_at).strftime("%Y-%m-%d")
    if edition_type == "weekly":
        iso = weekly_anchor_date(run_at).isocalendar()
        return f"{iso.year}-W{iso.week:02d}"
    raise EditorialError(f"unsupported edition type: {edition_type!r}")


def coverage_for(edition_type: str, run_at: datetime) -> CoverageWindow:
    if edition_type == "daily":
        return daily_coverage(run_at)
    if edition_type == "weekly":
        return weekly_coverage(run_at)
    raise EditorialError(f"unsupported edition type: {edition_type!r}")


def derive_public_root(report_url: str) -> str:
    value = str(report_url or "").strip()
    try:
        parsed = urlparse(value)
    except ValueError as exc:
        raise EditorialError("REPORT_URL is invalid") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise EditorialError("REPORT_URL must use http/https")
    if parsed.params or parsed.query or parsed.fragment or not parsed.path.endswith(
        DAILY_REPORT_SUFFIX
    ):
        raise EditorialError("REPORT_URL suffix contract mismatch")
    root_path = parsed.path[: -len(DAILY_REPORT_SUFFIX)].rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}{root_path}"


def _validate_fixture_root(root_url: str) -> str:
    value = str(root_url or "").strip().rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise EditorialError("preview public root must use http/https")
    if parsed.params or parsed.query or parsed.fragment:
        raise EditorialError("preview public root cannot contain params/query/fragment")
    return value


def public_urls(root_url: str, edition_type: str, key: str) -> tuple[str, str]:
    root = _validate_fixture_root(root_url)
    if edition_type not in {"daily", "weekly"}:
        raise EditorialError("unsupported edition type")
    safe_key = re.sub(r"[^0-9W-]", "", key)
    if safe_key != key or not key:
        raise EditorialError("invalid edition key")
    base = f"{root}/editorial/{edition_type}"
    return f"{base}/{key}.html", f"{base}/latest.html"


def valid_http_url(value: object) -> str:
    candidate = str(value or "").strip()
    try:
        parsed = urlparse(candidate)
    except ValueError:
        return ""
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or "\r" in candidate
        or "\n" in candidate
    ):
        return ""
    return candidate


def parse_published_at(value: object) -> datetime:
    raw = str(value or "").strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise EditorialError("article published_at is invalid") from exc
    if parsed.tzinfo is None:
        raise EditorialError("article published_at must include timezone")
    return parsed.astimezone(KST)


def _summary_sentences(title: str, snippet: str) -> str:
    cleaned = " ".join(str(snippet or "").split())
    if not cleaned:
        raise EditorialError("article summary/snippet is required")
    sentences = [part.strip() for part in _SENTENCE_RE.split(cleaned) if part.strip()]
    if len(sentences) == 1:
        comma_parts = [
            part.strip() for part in re.split(r"(?<=[,;·])\s*", cleaned) if part.strip()
        ]
        if len(comma_parts) >= 2:
            midpoint = max(1, math.ceil(len(comma_parts) / 2))
            sentences = [
                " ".join(comma_parts[:midpoint]).rstrip(",;·") + ".",
                " ".join(comma_parts[midpoint:]).rstrip(",;·") + ".",
            ]
        elif cleaned.casefold() != title.strip().casefold():
            sentences = [title.strip().rstrip(".!?") + ".", cleaned]
    return " ".join(sentences[:3])


def classify_category(title: str, summary: str) -> str:
    text = f"{title} {summary}".casefold()
    rules = (
        ("AI 인프라", ("데이터센터", "전력", "반도체", "gpu", "인프라")),
        ("정책·규제", ("규제", "법안", "정부", "정책", "보안", "안전")),
        ("투자·사업", ("투자", "계약", "수주", "펀드", "인수", "파트너십")),
        ("기술·제품", ("모델", "서비스", "플랫폼", "로봇", "소프트웨어", "제품")),
    )
    for label, keywords in rules:
        if any(keyword in text for keyword in keywords):
            return label
    return "기업·산업"


def _image_candidate(raw: Mapping) -> str:
    metadata = raw.get("source_metadata")
    if not isinstance(metadata, Mapping):
        metadata = {}
    for value in (
        raw.get("image_url"),
        raw.get("image"),
        metadata.get("image_url"),
        metadata.get("thumbnail_url"),
    ):
        chosen = valid_http_url(value)
        if chosen:
            return chosen
    return ""


def normalize_articles(
    raw_articles: Iterable[Mapping],
    coverage: CoverageWindow,
    *,
    limit: int,
) -> list[EditorialArticle]:
    normalized: list[EditorialArticle] = []
    seen: set[tuple[str, str]] = set()
    for raw in raw_articles:
        if not isinstance(raw, Mapping):
            continue
        try:
            published = parse_published_at(raw.get("published_at"))
        except EditorialError:
            continue
        if not (coverage.start <= published <= coverage.end):
            continue
        title = " ".join(str(raw.get("title") or "").split())
        source = " ".join(str(raw.get("source") or "").split())
        if not title or not source:
            continue
        selected = news_access.choose_article_link(raw)
        selected_url = valid_http_url(selected.url)
        if not selected_url:
            continue
        if selected.kind not in {
            news_access.LINK_KIND_PUBLISHER_DIRECT,
            news_access.LINK_KIND_GOOGLE_NEWS_FALLBACK,
            news_access.LINK_KIND_PORTAL_FALLBACK,
        }:
            continue
        try:
            summary = _summary_sentences(title, str(raw.get("snippet") or raw.get("summary") or ""))
        except EditorialError:
            continue
        dedup_key = (re.sub(r"\W+", "", title).casefold(), selected_url.rstrip("/").casefold())
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        normalized.append(
            EditorialArticle(
                title=title,
                summary=summary,
                source=source,
                published_at=published,
                selected_url=selected_url,
                link_kind=selected.kind,
                link_label=selected.label,
                category=classify_category(title, summary),
                image_url=_image_candidate(raw),
            )
        )
    normalized.sort(key=lambda item: (item.published_at, item.title), reverse=True)
    return normalized[:limit]


def _template(name: str) -> str:
    return (config.TEMPLATES_DIR / name).read_text(encoding="utf-8")


def _fill(template: str, values: Mapping[str, str]) -> str:
    output = template
    for key, value in values.items():
        output = output.replace("{{" + key + "}}", value)
    unresolved = sorted(set(re.findall(r"\{\{[A-Z0-9_]+\}\}", output)))
    if unresolved:
        raise EditorialError(f"unresolved template slots: {', '.join(unresolved)}")
    return output


def _external_anchor(article: EditorialArticle, *, class_name: str = "link") -> str:
    return (
        f'<a class="{escape(class_name, quote=True)}" '
        f'data-link-kind="{escape(article.link_kind, quote=True)}" '
        f'href="{escape(article.selected_url, quote=True)}" target="_blank" '
        f'rel="noopener noreferrer">{escape(article.link_label)}</a>'
    )


def _daily_headline(article: EditorialArticle) -> str:
    image = (
        f'<img class="hero-media" src="{escape(article.image_url, quote=True)}" '
        f'alt="" loading="lazy">'
        if article.image_url
        else ""
    )
    return (
        '<article class="hero" data-role="headline">'
        f"{image}<div class=\"hero-body\"><span class=\"chip\">{escape(article.category)}</span>"
        f"<h2>{escape(article.title)}</h2><p class=\"summary\">{escape(article.summary)}</p>"
        f'<div class="meta"><span>{escape(article.source)}</span>'
        f"<time datetime=\"{escape(article.published_at.isoformat(), quote=True)}\">"
        f"{escape(article.published_label)}</time><span>{_external_anchor(article)}</span>"
        "</div></div></article>"
    )


def _daily_card(article: EditorialArticle) -> str:
    image = (
        f'<img class="thumb" src="{escape(article.image_url, quote=True)}" '
        f'alt="" loading="lazy">'
        if article.image_url
        else ""
    )
    css_class = "card" if image else "card no-image"
    return (
        f'<article class="{css_class}" data-role="article-card">{image}<div>'
        f'<span class="chip">{escape(article.category)}</span>'
        f"<h3>{escape(article.title)}</h3><p>{escape(article.summary)}</p>"
        f'<div class="meta"><span>{escape(article.source)}</span>'
        f"<time datetime=\"{escape(article.published_at.isoformat(), quote=True)}\">"
        f"{escape(article.published_label)}</time><span>{_external_anchor(article)}</span>"
        "</div></div></article>"
    )


def _daily_key_lines(articles: list[EditorialArticle]) -> list[str]:
    lines = [article.title for article in articles[:3]]
    if articles:
        candidates = [
            part.strip()
            for article in articles
            for part in _SENTENCE_RE.split(article.summary)
            if part.strip()
        ]
        candidates.append(f"{articles[0].source} · {articles[0].published_label} 게시")
        for candidate in candidates:
            if candidate not in lines:
                lines.append(candidate)
            if len(lines) == 3:
                break
    return lines[:3]


def _daily_editor_lines(articles: list[EditorialArticle]) -> str:
    return "".join(f"<li>{escape(line)}</li>" for line in _daily_key_lines(articles))


def _dominant_issue(articles: list[EditorialArticle]) -> tuple[bool, str, int]:
    if len(articles) < 3:
        return False, "이번 주 핵심 이슈 묶음", 0
    per_article: list[set[str]] = []
    counts: dict[str, int] = {}
    for article in articles:
        words = {
            word.casefold()
            for word in _WORD_RE.findall(article.title)
            if len(word) >= 3 and word.casefold() not in _STOPWORDS and not word.isdigit()
        }
        per_article.append(words)
        for word in words:
            counts[word] = counts.get(word, 0) + 1
    ranked = sorted(counts, key=lambda word: (-counts[word], -len(word), word))
    if not ranked:
        return False, "이번 주 핵심 이슈 묶음", 0
    top = ranked[0]
    matched = counts[top]
    dominant = matched >= 3 and matched / len(articles) >= 0.5
    if not dominant:
        return False, "이번 주 핵심 이슈 묶음", matched
    companion = next(
        (word for word in ranked[1:] if counts[word] == matched and word != top), ""
    )
    label = " · ".join(filter(None, (top, companion)))
    return True, label, matched


def _weekly_fact(article: EditorialArticle) -> str:
    return (
        '<article class="fact-item"><h3 class="tt">'
        f"{escape(article.title)}</h3><p class=\"dd\">"
        f"{escape(article.source)} · {escape(article.published_label)} · "
        f"{_external_anchor(article)}</p></article>"
    )


def _weekly_time(article: EditorialArticle) -> str:
    return (
        '<article class="time-item"><time class="time-date" '
        f'datetime="{escape(article.published_at.isoformat(), quote=True)}">'
        f"{article.published_at:%m.%d %H:%M}</time><div class=\"time-box\">"
        f'<h3 class="time-title">{escape(article.title)}</h3>'
        f'<p class="time-desc">{escape(article.source)} · {_external_anchor(article)}</p>'
        "</div></article>"
    )


def _weekly_comparison(articles: list[EditorialArticle]) -> str:
    if len(articles) < 2:
        return '<tr><td colspan="5">확인된 비교 데이터 없음</td></tr>'
    return "".join(
        "<tr>"
        f'<td class="metric">{escape(article.category)}</td>'
        f"<td>{escape(article.title)}</td><td>{escape(article.source)}</td>"
        f"<td>{escape(article.published_label)}</td>"
        f"<td>{_external_anchor(article)}</td></tr>"
        for article in articles[:6]
    )


def _weekly_insights(articles: list[EditorialArticle]) -> str:
    return "".join(
        '<article class="j-item">'
        f'<span class="j-no">{index}</span><div class="j-txt">'
        f"<strong>{escape(article.title)}</strong><br>{escape(article.summary)} "
        f"({escape(article.source)}, {_external_anchor(article)})</div></article>"
        for index, article in enumerate(articles[:3], start=1)
    )


def _weekly_sources(articles: list[EditorialArticle]) -> str:
    return " · ".join(
        f"{escape(article.source)} {_external_anchor(article, class_name='source-link')}"
        for article in articles
    )


def render_daily(
    articles: list[EditorialArticle],
    *,
    run_at: datetime,
    root_url: str,
) -> RenderedEdition:
    if not articles:
        raise EditorialError("daily edition has no eligible linked articles")
    key = edition_key("daily", run_at)
    coverage = daily_coverage(run_at)
    dated_url, latest_url = public_urls(root_url, "daily", key)
    headline = articles[0]
    html = _fill(
        _template("editorial_daily.html"),
        {
            "EDITION_KEY": escape(key, quote=True),
            "PAGE_TITLE": escape(f"HDEC AI Daily Brief · {key}"),
            "EDITION_LABEL": escape(key),
            "COVERAGE_LABEL": escape(coverage.label()),
            "HEADLINE_HTML": _daily_headline(headline),
            "EDITOR_SUMMARY_HTML": _daily_editor_lines(articles),
            "ARTICLE_CARDS_HTML": (
                "".join(_daily_card(item) for item in articles[1:6])
                or '<p class="empty">추가로 선정된 주요 기사 없음</p>'
            ),
        },
    )
    summary_lines = _daily_key_lines(articles)
    text_lines = [
        "[HDEC AI Daily Brief]",
        f"edition: {key}",
        f"coverage: {coverage.label()}",
        "",
        "오늘의 핵심 3줄",
        *[f"- {line}" for line in summary_lines],
        "",
        f"headline: {headline.title}",
        "주요 기사",
    ]
    text_lines.extend(
        f"- {item.title} | {item.source} | {item.published_label} | "
        f"{item.link_label}: {item.selected_url}"
        for item in articles[1:6]
    )
    text_lines.extend(("", f"전체 Daily Brief 보기: {dated_url}"))
    teams_text = "\n".join(text_lines)
    teams_html = _teams_html(
        "HDEC AI Daily Brief",
        key,
        coverage,
        [
            ("오늘의 핵심", "<br>".join(escape(line) for line in summary_lines)),
            (
                "기사",
                "<br>".join(
                    f"{escape(item.title)} · {escape(item.source)} · "
                    f"{escape(item.published_label)} · {_external_anchor(item)}"
                    for item in articles
                ),
            ),
        ],
        dated_url,
        "전체 Daily Brief 보기",
    )
    return RenderedEdition(
        "daily", key, coverage, html, dated_url, latest_url, teams_text, teams_html,
        "daily", headline.title, len(articles),
    )


def render_weekly(
    articles: list[EditorialArticle],
    *,
    run_at: datetime,
    root_url: str,
) -> RenderedEdition:
    if not articles:
        raise EditorialError("weekly edition has no eligible linked articles")
    key = edition_key("weekly", run_at)
    coverage = weekly_coverage(run_at)
    dated_url, latest_url = public_urls(root_url, "weekly", key)
    dominant, issue_label, issue_count = _dominant_issue(articles)
    mode = "dominant_issue" if dominant else "multi_issue"
    mode_label = "단일 핵심 이슈" if dominant else "복수 핵심 이슈"
    headline = articles[0]
    key_title = issue_label if not dominant else f"{issue_label}: 이번 주 핵심 흐름"
    key_items = "".join(
        f"<li><b>{escape(item.source)}</b> {escape(item.title)}</li>"
        for item in articles[:3]
    )
    changed = headline
    why_title = (
        f"관련 보도 {issue_count}건 확인"
        if dominant
        else f"서로 다른 핵심 흐름 {min(3, len(articles))}건 확인"
    )
    management_cards = "".join(
        (
            '<article class="top-card"><h2 class="k">'
            f"{escape(label)}</h2><div class=\"v\">{escape(title)}</div>"
            f'<p class="d">{escape(description)}</p></article>'
        )
        for label, title, description in (
            ("WHAT CHANGED", changed.title, changed.summary),
            (
                "WHY IT MATTERS",
                why_title,
                "동일 coverage 안에서 확인된 기사 제목·요약·출처를 교차해 판단해야 합니다.",
            ),
            (
                "MANAGEMENT POINT",
                "원문 근거 확인 후 영향 범위 판단",
                "기사별 게시시각과 링크 유형을 함께 보고 후속 확인 대상을 정합니다.",
            ),
        )
    )
    alternative = (
        "단일 주제가 다수를 차지했지만 공개 기사 메타데이터와 제공 요약만으로 구성되어 "
        "원문의 후속 정정·추가 발표에 따라 해석이 달라질 수 있습니다."
        if dominant
        else
        "한 주제를 지배적 이슈로 확정할 만큼 보도 집중도가 높지 않았습니다. 공개 기사 "
        "메타데이터와 제공 요약만 사용했으며 후속 보도에 따라 우선순위가 달라질 수 있습니다."
    )
    html = _fill(
        _template("editorial_weekly.html"),
        {
            "EDITION_KEY": escape(key, quote=True),
            "PAGE_TITLE": escape(f"AI 경영 T&I · {key}"),
            "EDITION_LABEL": escape(key),
            "COVERAGE_SHORT": escape(
                f"{coverage.start:%m.%d}–{coverage.end:%m.%d} KST"
            ),
            "DOCUMENT_TITLE": escape(
                issue_label if dominant else "이번 주 핵심 이슈 묶음"
            ),
            "ISSUE_MODE_LABEL": escape(mode_label),
            "COVERAGE_LABEL": escape(coverage.label()),
            "KEY_MESSAGE_TITLE": escape(key_title),
            "KEY_MESSAGE_ITEMS": key_items,
            "MANAGEMENT_CARDS": management_cards,
            "FACT_ITEMS": "".join(_weekly_fact(item) for item in articles[:3]),
            "TIMELINE_ITEMS": "".join(
                _weekly_time(item) for item in sorted(articles[:6], key=lambda x: x.published_at)
            ),
            "COMPARISON_ROWS": _weekly_comparison(articles),
            "INSIGHT_ITEMS": _weekly_insights(articles),
            "ALTERNATIVE_VIEW": escape(alternative),
            "SOURCE_LINKS": _weekly_sources(articles),
        },
    )
    text_lines = [
        "[AI 경영 T&I]",
        f"ISO week: {key}",
        f"coverage: {coverage.label()}",
        f"mode: {mode_label}",
        "",
        f"KEY MESSAGE: {key_title}",
        f"MANAGEMENT POINT: 원문 근거 확인 후 영향 범위 판단",
        f"headline: {headline.title}",
        "",
        f"전체 Weekly T&I 보기: {dated_url}",
    ]
    teams_text = "\n".join(text_lines)
    teams_html = _teams_html(
        "AI 경영 T&I",
        key,
        coverage,
        [
            ("KEY MESSAGE", escape(key_title)),
            ("MANAGEMENT POINT", "원문 근거 확인 후 영향 범위 판단"),
            ("편집 모드", escape(mode_label)),
        ],
        dated_url,
        "전체 Weekly T&I 보기",
    )
    return RenderedEdition(
        "weekly", key, coverage, html, dated_url, latest_url, teams_text, teams_html,
        mode, headline.title, len(articles),
    )


def _teams_html(
    heading: str,
    key: str,
    coverage: CoverageWindow,
    sections: list[tuple[str, str]],
    public_url: str,
    cta: str,
) -> str:
    blocks = "".join(
        f"<p><strong>{escape(label)}</strong><br>{body}</p>" for label, body in sections
    )
    return (
        '<!DOCTYPE html><html lang="ko"><body style="font-family:Arial,sans-serif">'
        f"<h2>{escape(heading)}</h2><p>{escape(key)}<br>{escape(coverage.label())}</p>"
        f"{blocks}<p><a href=\"{escape(public_url, quote=True)}\" target=\"_blank\" "
        f'rel="noopener noreferrer">{escape(cta)}</a></p></body></html>'
    )


def render_edition(
    edition_type: str,
    raw_articles: Iterable[Mapping],
    *,
    run_at: datetime,
    root_url: str,
) -> RenderedEdition:
    coverage = coverage_for(edition_type, run_at)
    limit = DAILY_MAX_ARTICLES if edition_type == "daily" else WEEKLY_MAX_ARTICLES
    articles = normalize_articles(raw_articles, coverage, limit=limit)
    if edition_type == "daily":
        return render_daily(articles, run_at=run_at, root_url=root_url)
    if edition_type == "weekly":
        return render_weekly(articles, run_at=run_at, root_url=root_url)
    raise EditorialError("unsupported edition type")


def validate_rendered(edition: RenderedEdition) -> None:
    encoded = edition.html.encode("utf-8")
    marker = f'data-edition-key="{escape(edition.edition_key, quote=True)}"'
    meta = f'<meta name="editorial-edition" content="{escape(edition.edition_key, quote=True)}">'
    if marker not in edition.html or meta not in edition.html:
        raise EditorialError("edition marker missing")
    if b"{{" in encoded or b"}}" in encoded:
        raise EditorialError("unresolved template marker")
    if edition.article_count < 1:
        raise EditorialError("empty edition")
    for anchor in re.findall(r"<a\b[^>]*>", edition.html):
        if 'target="_blank"' not in anchor or 'rel="noopener noreferrer"' not in anchor:
            raise EditorialError("external link security attributes missing")
        match = re.search(r'href="([^"]+)"', anchor)
        if not match or not valid_http_url(match.group(1).replace("&amp;", "&")):
            raise EditorialError("invalid external URL")
    if edition.edition_type == "daily":
        if edition.html.count('data-role="headline"') != 1:
            raise EditorialError("daily headline count mismatch")
        if edition.html.count('data-role="article-card"') > 5:
            raise EditorialError("daily article card cap exceeded")
    else:
        required = (
            "key-message", "management-cards", "key-facts", "timeline", "comparison",
            "industry-insight", "alternative-view", "sources",
        )
        if any(f'data-section="{name}"' not in edition.html for name in required):
            raise EditorialError("weekly required section missing")


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    temp_path = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def write_preview_bundle(edition: RenderedEdition, output_dir: Path) -> dict:
    output_dir = output_dir.resolve()
    if output_dir == config.BASE_DIR or config.BASE_DIR in output_dir.parents:
        raise EditorialError("preview output must be outside repository")
    html_bytes = edition.html.encode("utf-8")
    dated = output_dir / f"{edition.edition_key}.html"
    latest = output_dir / "latest.html"
    text_path = output_dir / "teams-preview.txt"
    mail_path = output_dir / "teams-preview.html"
    for path, payload in (
        (dated, html_bytes),
        (latest, html_bytes),
        (text_path, edition.teams_text.encode("utf-8")),
        (mail_path, edition.teams_html.encode("utf-8")),
    ):
        atomic_write_bytes(path, payload)
    manifest = {
        "version": 1,
        "mode": "preview",
        "edition_type": edition.edition_type,
        "edition_key": edition.edition_key,
        "coverage_start": edition.coverage.start.isoformat(),
        "coverage_end": edition.coverage.end.isoformat(),
        "html_sha256": edition.html_sha256,
        "dated_html": str(dated),
        "latest_html": str(latest),
        "teams_text": str(text_path),
        "teams_html": str(mail_path),
        "public_dated_url": edition.public_dated_url,
        "public_latest_url": edition.public_latest_url,
        "issue_mode": edition.issue_mode,
        "article_count": edition.article_count,
        "network_sends": 0,
        "smtp_attempts": 0,
        "production_state_reads": 0,
        "production_state_writes": 0,
        "docs_writes": 0,
        "git_writes": 0,
        "forbidden_platform_calls": 0,
    }
    atomic_write_bytes(
        output_dir / "manifest.json",
        (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )
    return manifest


def fixture_articles(
    edition_type: str,
    run_at: datetime,
    *,
    profile: str = "dominant",
) -> list[dict]:
    """Deterministic linked metadata used only by explicit preview/verifier paths."""
    coverage = coverage_for(edition_type, run_at)
    span = int((coverage.end - coverage.start).total_seconds())
    if profile not in {"dominant", "multi"}:
        raise EditorialError("unsupported fixture profile")
    dominant_titles = [
        "AI 데이터센터 전력 조달 계획 공개",
        "AI 데이터센터 전력망 연계 기준 논의",
        "AI 데이터센터 전력 효율 기술 투자",
        "AI 데이터센터 전력 수요 대응 협력",
        "AI 데이터센터 냉각 운영 기준 점검",
        "AI 데이터센터 공급망 계약 확대",
    ]
    multi_titles = [
        "산업용 로봇 안전 기준 개정 논의",
        "기업용 소프트웨어 투자 계획 공개",
        "반도체 공급망 협력 체계 발표",
        "공공 부문 인공지능 조달 지침 검토",
        "클라우드 보안 인증 절차 개선",
        "디지털 인재 교육 프로그램 확대",
    ]
    titles = dominant_titles if profile == "dominant" else multi_titles
    rows = []
    for index, title in enumerate(titles):
        seconds = min(span, 600 + index * max(1, span // (len(titles) + 1)))
        published = coverage.start + timedelta(seconds=seconds)
        rows.append(
            {
                "id": f"fixture-{edition_type}-{profile}-{index + 1}",
                "title": title,
                "source": f"검증매체 {index + 1}",
                "published_at": published.isoformat(),
                "url": f"https://news{index + 1}.fixture.test/articles/{edition_type}-{index + 1}",
                "snippet": (
                    f"{title}과 관련한 공개 계획과 적용 범위가 제시됐다. "
                    "세부 일정과 조건은 원문 발표를 기준으로 추가 확인이 필요하다."
                ),
                "source_metadata": {"provider": "offline_fixture"},
            }
        )
    return rows


def manifest_for_runtime(edition: RenderedEdition, dated_path: Path, latest_path: Path) -> dict:
    return {
        "version": 1,
        "edition_type": edition.edition_type,
        "edition_key": edition.edition_key,
        "coverage_start": edition.coverage.start.isoformat(),
        "coverage_end": edition.coverage.end.isoformat(),
        "html_sha256": edition.html_sha256,
        "public_dated_url": edition.public_dated_url,
        "public_latest_url": edition.public_latest_url,
        "dated_path": str(dated_path),
        "latest_path": str(latest_path),
        "teams_text": edition.teams_text,
        "teams_html": edition.teams_html,
        "headline": edition.headline,
        "issue_mode": edition.issue_mode,
        "article_count": edition.article_count,
    }


def article_dict(article: EditorialArticle) -> dict:
    """Stable helper for offline contract assertions."""
    output = asdict(article)
    output["published_at"] = article.published_at.isoformat()
    return output
