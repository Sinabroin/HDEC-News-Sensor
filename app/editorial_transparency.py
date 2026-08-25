"""Read-only collection-to-selection transparency for summary deliveries."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from html import escape
from typing import Any

from app import collector, editorial_radar, teams_push_state


def _clean(value: object, limit: int = 500) -> str:
    return " ".join(str(value or "").split())[:limit]


def _published(value: object) -> datetime | None:
    raw = _clean(value, 80)
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _identity(row: Mapping[str, Any]) -> tuple[str, bool]:
    identity = teams_push_state.article_identity(row)
    normalized_url = identity.get("normalized_url") or ""
    if normalized_url:
        return f"url:{normalized_url}", True
    explicit_cluster = _clean(
        row.get("event_cluster_id")
        or row.get("evidence_cluster_key")
        or row.get("cluster_key"),
        160,
    )
    if explicit_cluster:
        return f"event:{explicit_cluster}", True
    article_id = identity.get("article_id") or ""
    if article_id:
        return f"id:{article_id}", True
    # Conservative last resort: source + normalized title, never title alone.
    source = _clean(row.get("source"), 120).casefold()
    title = collector.normalize_title(_clean(row.get("title"), 300))
    if source and title:
        return f"signal:{source}:{title}", False
    return "", False


def build_rolling_transparency(
    rows: Iterable[Mapping[str, Any]],
    *,
    window_start: datetime,
    window_end: datetime,
    selected_count: int,
) -> dict[str, Any]:
    """Recompute one bounded window with existing URL/article identities.

    Cross-day counts are never produced by adding daily totals.  Rows without a
    reliable URL/article/event identity remain conservatively countable only as
    ``수집 신호`` and make the user-facing unit explicit.
    """
    if window_start.tzinfo is None or window_end.tzinfo is None or window_end < window_start:
        raise ValueError("transparency window is invalid")
    in_window: list[Mapping[str, Any]] = []
    for raw in rows:
        if not isinstance(raw, Mapping):
            continue
        published = _published(raw.get("published_at") or raw.get("published_kst"))
        if published is None or not (window_start <= published <= window_end):
            continue
        in_window.append(raw)
    identity_groups: dict[str, list[Mapping[str, Any]]] = {}
    reliable = True
    unkeyed = 0
    for row in in_window:
        key, proven = _identity(row)
        reliable = reliable and proven
        if not key:
            unkeyed += 1
            key = f"unkeyed:{unkeyed}"
            reliable = False
        identity_groups.setdefault(key, []).append(row)
    ai_count = 0
    executive_count = 0
    for group in identity_groups.values():
        dimension_rows = [editorial_radar.strategic_dimensions(row) for row in group]
        group_ai = any(row.get("ai_central") is True for row in dimension_rows)
        group_executive = any(
            row.get("ai_central") is True
            and row.get("executive_materiality") is True
            for row in dimension_rows
        )
        ai_count += int(group_ai)
        executive_count += int(group_executive)
    return {
        "raw_collected_count": len(in_window),
        "unique_collected_count": len(identity_groups),
        "ai_central_count": ai_count,
        "executive_candidate_count": executive_count,
        "selected_count": max(0, int(selected_count)),
        "unique_count_proven": reliable,
        "unique_count_authority": (
            "canonical_article_identity"
            if reliable
            else "bounded_source_title_collection_signal"
        ),
        "unit_label": "건 탐지" if reliable else "건 수집 신호",
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
    }


def from_radar_audit(audit: object, *, selected_count: int | None = None) -> dict[str, Any]:
    normalized = editorial_radar.normalize_audit(audit, selected_count=selected_count)
    funnel = normalized["funnel"]
    selected = max(0, int(funnel.get("selected_count") or 0))
    unique_count = max(
        selected,
        int(
            funnel.get("unique_collected_count")
            or funnel.get("normalized_row_count")
            or 0
        ),
    )
    raw_count = max(
        unique_count,
        int(funnel.get("raw_collected_count") or funnel.get("collection_count") or 0),
    )
    proven = funnel.get("unique_count_proven") is True
    return {
        "raw_collected_count": raw_count,
        "unique_collected_count": unique_count,
        "ai_central_count": max(0, int(funnel.get("ai_central_count") or 0)),
        "executive_candidate_count": max(
            0, int(funnel.get("executive_candidate_count") or 0)
        ),
        "selected_count": selected,
        "unique_count_proven": proven,
        "unique_count_authority": str(
            funnel.get("unique_count_authority") or "normalized_article_identity"
        ),
        "unit_label": "건 탐지" if proven else "건 수집 신호",
    }


def render_text(transparency: Mapping[str, Any], *, window_label: str) -> str:
    proven = transparency.get("unique_count_proven") is True
    count_field = "unique_collected_count" if proven else "raw_collected_count"
    count = max(0, int(transparency.get(count_field) or 0))
    unit = "건 탐지" if proven else "건 수집 신호"
    lines = [
        f"AI T&I 탐지 현황 · 최근 {window_label}",
        f"{count}{unit}",
        f"→ AI 핵심 {max(0, int(transparency.get('ai_central_count') or 0))}건",
    ]
    executive_count = max(0, int(transparency.get("executive_candidate_count") or 0))
    if executive_count:
        lines.append(f"→ 임원 후보 {executive_count}건")
    lines.append(f"→ 최종 {max(0, int(transparency.get('selected_count') or 0))}건")
    return "\n".join(lines)


def render_html(transparency: Mapping[str, Any], *, window_label: str) -> str:
    lines = render_text(transparency, window_label=window_label).splitlines()
    return (
        '<div data-role="teams-collection-transparency" '
        'style="margin-top:18px;padding-top:12px;border-top:1px solid #e4e7ec;'
        'font-size:12px;color:#667085;line-height:1.6">'
        + "<br>".join(escape(line) for line in lines)
        + "</div>"
    )


__all__ = [
    "build_rolling_transparency",
    "from_radar_audit",
    "render_html",
    "render_text",
]
