"""Daily editorial candidate, rich-edit, and approved-review contracts."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any, Mapping, Sequence

from app import ai_centrality
from app.editorial_briefings import (
    EditorialArticle,
    EditorialError,
    KST,
    editorial_inline_plain_text,
    sanitize_editorial_inline_html,
    valid_http_url,
)

BUNDLE_VERSION = 2
REVIEW_VERSION = 2
MAX_REVIEW_ARTICLES = 6
CATEGORY_ORDER = ("투자·산업", "기업동향", "기술정보")
CATEGORY_RANK = {name: index for index, name in enumerate(CATEGORY_ORDER)}

_CATEGORY_SIGNALS = {
    "투자·산업": (
        "투자", "시장", "정책", "규제", "인프라", "데이터센터", "반도체",
        "에너지", "원전", "smr", "펀드", "인수", "매출", "실적", "공급망",
        "국가전략", "예산",
    ),
    "기업동향": (
        "기업", "경영", "조직", "인사", "노사", "도입", "전환", "협업",
        "계약", "제휴", "인수합병", "사내", "업무혁신", "생산성", "고객",
        "사업부",
    ),
    "기술정보": (
        "모델", "추론", "에이전트", "로봇", "소프트웨어", "오픈소스", "연구",
        "알고리즘", "컴퓨팅", "gpu", "칩", "벤치마크", "멀티모달", "생성형",
        "llm", "보안", "데이터", "클라우드",
    ),
}
_CATEGORY_FIELD_WEIGHTS = {
    "title": 3.0,
    "summary": 1.0,
    "source": 0.5,
}
_SUGGESTED_CATEGORY_WEIGHT = 0.25


class EditorialReviewError(EditorialError):
    """Malformed candidate bundle or approved review snapshot."""


def _clean(value: object) -> str:
    return " ".join(str(value or "").split())


#: R4-R6 §6 — an operator may keep a non-AI-central article only through an
#: explicit override with a written reason; silent override is forbidden.
AI_CENTRALITY_OPERATOR_OVERRIDE = "operator_override"


def _resolve_review_ai_centrality(
    title: str,
    summary: str,
    item: Mapping[str, Any] | None,
) -> str:
    """AI-centrality of the FINAL edited fields (§6).

    The editor may not override a non-AI article into the AI Brief merely by
    editing its title or category: the decision reruns on the final title and
    summary. The only escape is a non-empty ``operator_override_reason`` on
    the review item, which is preserved as an explicit, auditable override."""
    decision = ai_centrality.classify({"title": title, "snippet": summary})
    if decision.is_central:
        return decision.level
    reason = _clean((item or {}).get("operator_override_reason"))
    if reason:
        return AI_CENTRALITY_OPERATOR_OVERRIDE
    raise EditorialReviewError(
        "review article is not AI-central and carries no operator_override_reason"
        f" (level={decision.level}, exclusion={decision.exclusion or '-'})"
    )


def analyze_editorial_category(
    title: object,
    summary: object,
    source: object = "",
    suggested_category: object = "",
) -> dict[str, Any]:
    """Explainably classify an article into the fixed editorial taxonomy."""
    fields = {
        "title": _clean(title).casefold(),
        "summary": _clean(summary).casefold(),
        "source": _clean(source).casefold(),
    }
    scores = {category: 0.0 for category in CATEGORY_ORDER}
    matched_signals: dict[str, dict[str, list[str]]] = {
        category: {field: [] for field in fields}
        for category in CATEGORY_ORDER
    }
    content_signal_count = 0
    for field, text in fields.items():
        raw_matches = {
            category: [
                signal
                for signal in _CATEGORY_SIGNALS[category]
                if signal.casefold() in text
            ]
            for category in CATEGORY_ORDER
        }
        all_matches = [
            signal
            for category_matches in raw_matches.values()
            for signal in category_matches
        ]
        for category in CATEGORY_ORDER:
            matches = [
                signal
                for signal in raw_matches[category]
                if not any(
                    signal.casefold() != other.casefold()
                    and signal.casefold() in other.casefold()
                    for other in all_matches
                )
            ]
            matched_signals[category][field] = matches
            scores[category] += len(matches) * _CATEGORY_FIELD_WEIGHTS[field]
            content_signal_count += len(matches)

    suggested = _clean(suggested_category)
    if suggested in CATEGORY_RANK:
        scores[suggested] += _SUGGESTED_CATEGORY_WEIGHT

    if content_signal_count == 0:
        category = "기술정보"
        reason = "콘텐츠 신호가 없어 약한 제안값보다 기술정보 기본값을 우선"
    else:
        # CATEGORY_ORDER is the explicit deterministic tie-break.
        category = max(
            CATEGORY_ORDER,
            key=lambda item: (scores[item], -CATEGORY_RANK[item]),
        )
        top_matches = matched_signals[category]
        signal_text = ", ".join(
            f"{field}:{'/'.join(values)}"
            for field, values in top_matches.items()
            if values
        )
        prior_text = (
            f"; 제안값 {suggested}은 {_SUGGESTED_CATEGORY_WEIGHT:g}점의 약한 prior"
            if suggested in CATEGORY_RANK
            else ""
        )
        reason = (
            f"{category} 최고점 {scores[category]:g}; "
            f"동점은 {' > '.join(CATEGORY_ORDER)} 순으로 결정"
            f"{'; ' + signal_text if signal_text else ''}{prior_text}"
        )
    return {
        "category": category,
        "scores": {key: round(value, 2) for key, value in scores.items()},
        "matched_signals": matched_signals,
        "reason": reason,
    }


def normalize_category(value: object, title: object = "", summary: object = "") -> str:
    candidate = _clean(value)
    if candidate in CATEGORY_RANK:
        return candidate
    legacy_summary = " ".join(
        part for part in (_clean(summary), candidate) if part
    )
    return str(
        analyze_editorial_category(
            title,
            legacy_summary,
        )["category"]
    )


def category_rank(value: object) -> int:
    return CATEGORY_RANK.get(normalize_category(value), len(CATEGORY_ORDER))


def candidate_id(article: EditorialArticle) -> str:
    payload = "\x1f".join(
        (
            _clean(article.selected_url),
            _clean(article.title),
            _clean(article.source),
            article.published_at.astimezone(KST).isoformat(timespec="seconds"),
        )
    )
    return "candidate-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def article_to_candidate(
    article: EditorialArticle,
    *,
    ai_rank: int,
    feedback_adjustment: float = 0.0,
) -> dict[str, Any]:
    adjusted = round(float(article.total_ranking_score) + float(feedback_adjustment), 4)
    category_analysis = analyze_editorial_category(
        article.title,
        article.summary,
        source=article.source,
        suggested_category=article.category,
    )
    category = str(category_analysis["category"])
    summary_html = sanitize_editorial_inline_html(
        article.summary_html or escape(article.summary)
    )
    return {
        "candidate_id": candidate_id(article),
        "origin": "ai_collected",
        "ai_rank": int(ai_rank),
        "adjusted_rank": 0,
        "feedback_adjustment": round(float(feedback_adjustment), 4),
        "adjusted_score": adjusted,
        "title": article.title,
        "summary": article.summary,
        "summary_html": summary_html,
        "source": article.source,
        "published_at": article.published_at.astimezone(KST).isoformat(timespec="seconds"),
        "selected_url": article.selected_url,
        "link_kind": article.link_kind,
        "link_label": article.link_label,
        "category": category,
        "category_analysis": category_analysis,
        "category_rank": category_rank(category),
        "collection_source_kind": article.collection_source_kind,
        "relevance_score": article.relevance_score,
        "freshness_score": article.freshness_score,
        "source_quality_score": article.source_quality_score,
        "total_ranking_score": article.total_ranking_score,
        "selection_reason": article.selection_reason,
        "original_article_url": article.original_article_url,
        "publisher_article_url": article.publisher_article_url,
        "publisher_url_source_kind": article.publisher_url_source_kind,
        "publisher_url_reason": article.publisher_url_reason,
        "image_url": article.image_url,
        "image_source_kind": article.image_source_kind,
        "image_source_page_url": article.image_source_page_url,
        "image_width": article.image_width,
        "image_height": article.image_height,
        "image_fallback_used": article.image_fallback_used,
        "image_reason": article.image_reason,
        "image_remote_url": article.image_remote_url or article.image_url,
        "image_quality_accepted": article.image_quality_accepted,
        "image_quality_reason": article.image_quality_reason,
        # D7-AK-6E R4-R6 §11/§12 — explainable selection factors + implication
        # surfaced to the editor console and preserved through approval.
        "materiality_score": article.materiality_score,
        "hdec_relevance_score": article.hdec_relevance_score,
        "publisher_tier": article.publisher_tier,
        "publisher_priority_label": article.publisher_priority_label,
        "executive_relevance_reason": article.executive_relevance_reason,
        "materiality_reason": article.materiality_reason,
        "executive_implication": article.executive_implication,
        "ai_centrality_level": article.ai_centrality_level,
    }


def _published_at(value: object) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise EditorialReviewError("published_at is malformed") from exc
    if parsed.tzinfo is None:
        raise EditorialReviewError("published_at must be timezone-aware")
    return parsed.astimezone(KST)


def _safe_article_url(value: object) -> str:
    candidate = valid_http_url(value)
    if not candidate:
        raise EditorialReviewError("article URL must be public http/https")
    return candidate


def candidate_to_article(
    candidate: Mapping[str, Any],
    *,
    override: Mapping[str, Any] | None = None,
) -> EditorialArticle:
    edit = dict(override or {})
    published_at = _published_at(candidate.get("published_at"))
    title = _clean(edit.get("title") or candidate.get("title"))
    source = _clean(candidate.get("source"))
    selected_url = _safe_article_url(candidate.get("selected_url"))
    raw_summary_html = (
        edit.get("summary_html")
        or candidate.get("summary_html")
        or escape(_clean(candidate.get("summary")))
    )
    summary_html = sanitize_editorial_inline_html(raw_summary_html)
    summary = editorial_inline_plain_text(summary_html)
    category = normalize_category(
        edit.get("category") or candidate.get("category"),
        title,
        summary,
    )
    if not all((title, summary, source, selected_url)):
        raise EditorialReviewError("candidate required field is empty")
    raw_implication_override = _clean(edit.get("implication_html"))
    implication_html = (
        sanitize_editorial_inline_html(raw_implication_override)
        if raw_implication_override
        else ""
    )

    return EditorialArticle(
        title=title,
        summary=summary,
        summary_html=summary_html,
        source=source,
        published_at=published_at,
        selected_url=selected_url,
        link_kind=_clean(candidate.get("link_kind")) or "publisher_direct",
        link_label=_clean(candidate.get("link_label")) or "원문 보기",
        category=category,
        collection_source_kind=_clean(candidate.get("collection_source_kind")),
        relevance_score=float(candidate.get("relevance_score") or 0.0),
        freshness_score=float(candidate.get("freshness_score") or 0.0),
        source_quality_score=float(candidate.get("source_quality_score") or 0.0),
        total_ranking_score=float(candidate.get("total_ranking_score") or 0.0),
        selection_reason=_clean(candidate.get("selection_reason")) or "selected_from_review_bundle",
        original_article_url=_clean(candidate.get("original_article_url")),
        publisher_article_url=_clean(candidate.get("publisher_article_url")),
        publisher_url_source_kind=_clean(candidate.get("publisher_url_source_kind")) or "unresolved_aggregator",
        publisher_url_reason=_clean(candidate.get("publisher_url_reason")) or "loaded_from_review_bundle",
        image_url=_clean(candidate.get("image_url")),
        image_source_kind=_clean(candidate.get("image_source_kind")) or "fallback",
        image_source_page_url=_clean(candidate.get("image_source_page_url")),
        image_width=candidate.get("image_width"),
        image_height=candidate.get("image_height"),
        image_fallback_used=bool(candidate.get("image_fallback_used", True)),
        image_reason=_clean(candidate.get("image_reason")) or "loaded_from_review_bundle",
        image_remote_url=_clean(candidate.get("image_remote_url") or candidate.get("image_url")),
        image_quality_accepted=bool(candidate.get("image_quality_accepted")),
        image_quality_reason=_clean(candidate.get("image_quality_reason")),
        materiality_score=float(candidate.get("materiality_score") or 0.0),
        hdec_relevance_score=float(candidate.get("hdec_relevance_score") or 0.0),
        publisher_tier=_clean(candidate.get("publisher_tier")),
        publisher_priority_label=_clean(candidate.get("publisher_priority_label")),
        executive_relevance_reason=_clean(candidate.get("executive_relevance_reason")),
        materiality_reason=_clean(candidate.get("materiality_reason")),
        executive_implication=_clean(candidate.get("executive_implication")),
        implication_html=implication_html,
        ai_centrality_level=_resolve_review_ai_centrality(title, summary, edit),
    )


def manual_item_to_article(item: Mapping[str, Any]) -> EditorialArticle:
    if item.get("origin") != "human_link":
        raise EditorialReviewError("manual item origin mismatch")
    title = _clean(item.get("title"))
    source = _clean(item.get("source"))
    selected_url = _safe_article_url(item.get("selected_url"))
    summary_html = sanitize_editorial_inline_html(
        item.get("summary_html") or escape(_clean(item.get("summary")))
    )
    summary = editorial_inline_plain_text(summary_html)
    category = normalize_category(item.get("category"), title, summary)
    published_at = _published_at(item.get("published_at"))
    if not all((title, source, summary, selected_url)):
        raise EditorialReviewError("manual article requires URL, source, title, and summary")
    image_url = valid_http_url(item.get("image_url"))
    raw_manual_implication = _clean(item.get("implication_html"))
    manual_implication_html = (
        sanitize_editorial_inline_html(raw_manual_implication)
        if raw_manual_implication
        else ""
    )
    return EditorialArticle(
        title=title,
        summary=summary,
        summary_html=summary_html,
        source=source,
        published_at=published_at,
        selected_url=selected_url,
        link_kind="publisher_direct",
        link_label="사용자 선별 원문",
        category=category,
        collection_source_kind="human_link",
        selection_reason="human_supplied_link",
        original_article_url=selected_url,
        publisher_article_url=selected_url,
        publisher_url_source_kind="human_supplied",
        publisher_url_reason="human_supplied_link",
        image_url=image_url,
        image_remote_url=image_url,
        image_source_kind="human_supplied" if image_url else "fallback",
        image_fallback_used=not bool(image_url),
        image_reason="human_supplied" if image_url else "no_manual_image",
        implication_html=manual_implication_html,
        ai_centrality_level=_resolve_review_ai_centrality(title, summary, item),
    )


def _candidate_for_daily_render(
    candidate: Mapping[str, Any],
    edition_key: object,
) -> Mapping[str, Any]:
    """Rebase a console-local image for Daily HTML in docs/editorial/daily."""
    image_url = _clean(candidate.get("image_url"))
    edition = _clean(edition_key)
    prefix = "assets/images/"
    filename = image_url.removeprefix(prefix)
    if (
        len(edition) == 10
        and edition[4] == "-"
        and edition[7] == "-"
        and edition.replace("-", "").isdigit()
        and image_url.startswith(prefix)
        and filename
        and "/" not in filename
        and "\\" not in filename
        and filename not in {".", ".."}
    ):
        rebased = dict(candidate)
        rebased["image_url"] = (
            f"../review/{edition}/{prefix}{filename}"
        )
        return rebased
    return candidate


def write_bundle(
    *,
    edition_key: str,
    coverage_start: str,
    coverage_end: str,
    candidates: Sequence[Mapping[str, Any]],
    path: Path,
    generated_at: str,
) -> dict[str, Any]:
    ids = [str(item.get("candidate_id") or "") for item in candidates]
    if not candidates or any(not item for item in ids) or len(ids) != len(set(ids)):
        raise EditorialReviewError("candidate IDs must be non-empty and unique")
    payload = {
        "version": BUNDLE_VERSION,
        "edition_type": "daily",
        "edition_key": edition_key,
        "coverage_start": coverage_start,
        "coverage_end": coverage_end,
        "generated_at": generated_at,
        "category_order": list(CATEGORY_ORDER),
        "candidate_count": len(candidates),
        "candidates": list(candidates),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def load_bundle(path: Path, edition_key: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise EditorialReviewError("candidate bundle missing or malformed") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("version") != BUNDLE_VERSION
        or payload.get("edition_type") != "daily"
        or payload.get("edition_key") != edition_key
        or payload.get("category_order") != list(CATEGORY_ORDER)
        or not isinstance(payload.get("candidates"), list)
        or not payload["candidates"]
    ):
        raise EditorialReviewError("candidate bundle identity mismatch")
    ids = [str(item.get("candidate_id") or "") for item in payload["candidates"]]
    if any(not item for item in ids) or len(ids) != len(set(ids)):
        raise EditorialReviewError("candidate bundle IDs invalid")
    return payload


REVIEW_DECISION_APPROVED = "approved"
REVIEW_DECISION_ABSENT = "absent"
REVIEW_DECISION_MALFORMED = "malformed"


def load_review_decision(
    path: Path, edition_key: str
) -> tuple[dict[str, Any] | None, str]:
    """§12 decision trace: (review, approved|absent|malformed).

    A malformed review fails closed to None — the caller falls back to the
    documented AI order with no partial application, and the reason stays
    machine-readable."""
    if not path.exists():
        return None, REVIEW_DECISION_ABSENT
    review = load_review(path, edition_key)
    if review is None:
        return None, REVIEW_DECISION_MALFORMED
    return review, REVIEW_DECISION_APPROVED


def load_review(path: Path, edition_key: str) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (
        not isinstance(payload, dict)
        or payload.get("version") != REVIEW_VERSION
        or payload.get("edition_type") != "daily"
        or payload.get("edition_key") != edition_key
        or payload.get("review_status") != "approved"
    ):
        return None
    selected = payload.get("selected_items")
    if not isinstance(selected, list) or not selected or len(selected) > MAX_REVIEW_ARTICLES:
        return None
    ids = [str(item.get("candidate_id") or "") for item in selected if isinstance(item, Mapping)]
    if len(ids) != len(selected) or any(not item for item in ids) or len(ids) != len(set(ids)):
        return None
    if any(item.get("origin") not in {"ai_collected", "human_link"} for item in selected):
        return None
    return payload


def choose_daily_articles(
    bundle: Mapping[str, Any],
    review: Mapping[str, Any] | None,
    *,
    limit: int = MAX_REVIEW_ARTICLES,
) -> tuple[list[EditorialArticle], str]:
    candidates = list(bundle.get("candidates") or [])
    by_id = {str(item.get("candidate_id")): item for item in candidates}

    if review is not None:
        selected_items = list(review.get("selected_items") or [])
        if selected_items and len(selected_items) <= limit:
            articles: list[EditorialArticle] = []
            for item in selected_items:
                candidate_id_value = str(item.get("candidate_id") or "")
                if item.get("origin") == "human_link":
                    articles.append(manual_item_to_article(item))
                    continue
                base = by_id.get(candidate_id_value)
                if base is None:
                    raise EditorialReviewError("approved AI candidate is not in the bundle")
                articles.append(
                    candidate_to_article(
                        _candidate_for_daily_render(
                            base,
                            bundle.get("edition_key"),
                        ),
                        override=item,
                    )
                )
            if articles:
                return articles, "human_approved"

    auto = candidates[:limit]
    if not auto:
        raise EditorialReviewError("candidate bundle has no automatic fallback")
    return [
        candidate_to_article(
            _candidate_for_daily_render(item, bundle.get("edition_key"))
        )
        for item in auto
    ], "ai_fallback"
