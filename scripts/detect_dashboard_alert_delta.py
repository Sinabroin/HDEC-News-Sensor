#!/usr/bin/env python3
"""Detect material alert-surface changes between two static dashboards.

The detector reads only the ``script#preview-model`` JSON island. It emits a
stable fingerprint from article identity and ranking fields, never article
content or environment values. A malformed input fails closed. An empty new
candidate set is valid but can never open the alert gate.

Its ``--github-output`` and stdout log stay content-free (counts and hashes only).
``alert_delta`` opens only for changes that are both classified meaningful and
eligible for an immediate hourly alert (value + recency, D7-AK-2); the raw
fingerprint change stays a diagnostic. With the optional ``--delta-artifact
PATH`` it additionally writes a shared delta payload (eligible articles only, at
most 5) so the Telegram and Teams senders consume one file instead of each
re-fetching the news. That artifact carries only the short reason/summary already
public in the dashboard — no raw body, no secrets, no environment values.

This module owns the dashboard parsing, the surface traversal, and the single
resolution of the reference time (``--now``) and news mode that the eligibility
gate, the GITHUB_OUTPUT, and the artifact all share. The classification and
eligibility rules themselves live in ``app/delta_classifier.py``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable

# app 도메인(delta_classifier)을 import하기 위해 repo 루트를 sys.path에 올린다
# (`python3 scripts/…`로 실행되면 sys.path[0]가 scripts/라 app을 못 찾는다).
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import delta_classifier  # noqa: E402


CORE_SURFACES = (
    "top_immediate_signals",
    "top_new_issues",
    "hdec_direct_signals",
    "risk_regulation_signals",
    "business_signals",
)
FINGERPRINT_FIELDS = (
    "article_id",
    "title",
    "source",
    "url",
    "category_label",
    "score",
)

# The current dashboard model carries rendered projections rather than the
# original brief lists. These aliases preserve the requested alert surfaces.
LENS_SURFACE_ALIASES = {
    "top_immediate_signals": ("now",),
    "top_new_issues": ("new",),
    "hdec_direct_signals": ("hyundai_group",),
    "risk_regulation_signals": ("safety_quality",),
    "business_signals": (
        "business",
        "building_housing",
        "civil_infrastructure",
        "competitor_contractors",
        "developers",
        "development_business",
        "global_business",
        "new_energy",
        "oil_energy",
        "overseas_branch",
        "overseas_subsidiary",
        "plant",
        "trust_companies",
    ),
}
SITE_LENS_KEYS = ("domestic_site", "overseas_site")


class _PreviewModelParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._capturing = False
        self._found = False
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._found or tag.lower() != "script":
            return
        attributes = dict(attrs)
        if attributes.get("id") == "preview-model":
            self._capturing = True
            self._found = True

    def handle_endtag(self, tag: str) -> None:
        if self._capturing and tag.lower() == "script":
            self._capturing = False

    def handle_data(self, data: str) -> None:
        if self._capturing:
            self._parts.append(data)

    @property
    def payload(self) -> str:
        return "".join(self._parts)


def load_preview_model(path: Path) -> dict[str, Any]:
    parser = _PreviewModelParser()
    parser.feed(path.read_text(encoding="utf-8"))
    if not parser.payload.strip():
        raise ValueError("preview-model JSON island is missing")
    model = json.loads(parser.payload)
    if not isinstance(model, dict):
        raise ValueError("preview-model must be a JSON object")
    return model


def _walk_named_values(value: Any, key: str) -> Iterable[Any]:
    if isinstance(value, dict):
        if key in value:
            yield value[key]
        for child in value.values():
            yield from _walk_named_values(child, key)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_named_values(child, key)


def _article_rows(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        if any(value.get(field) not in (None, "") for field in FINGERPRINT_FIELDS):
            yield value
            return
        for child in value.values():
            yield from _article_rows(child)
    if isinstance(value, list):
        for item in value:
            yield from _article_rows(item)


def _text(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())


def _record(surface: str, row: dict[str, Any]) -> tuple[str, ...] | None:
    article_id = _text(row.get("article_id"))
    title = _text(row.get("title"))
    url = _text(row.get("url"))
    if not any((article_id, title, url)):
        return None
    category = (
        row.get("category_label")
        or row.get("cat")
        or row.get("aiCategoryLabel")
        or row.get("tag")
    )
    values = {
        "article_id": article_id,
        "title": title,
        "source": _text(row.get("source") or row.get("display_source")),
        "url": url,
        "category_label": _text(category),
        "score": _text(row.get("score")),
    }
    return (surface, *(values[field] for field in FINGERPRINT_FIELDS))


def _direct_surface_rows(model: dict[str, Any], surface: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for value in _walk_named_values(model, surface):
        rows.extend(_article_rows(value))
    return rows


def _projected_surface_rows(model: dict[str, Any], surface: str) -> list[dict[str, Any]]:
    banks = model.get("lens_banks")
    if not isinstance(banks, dict):
        return []
    rows: list[dict[str, Any]] = []
    for key in LENS_SURFACE_ALIASES[surface]:
        rows.extend(_article_rows(banks.get(key)))
    return rows


def _all_article_rows(model: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            if any(value.get(field) not in (None, "") for field in FINGERPRINT_FIELDS):
                rows.append(value)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(model)
    return rows


def _site_article_keys(model: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    site_roots = [
        value
        for key, value in model.items()
        if "site" in key.lower() and isinstance(value, (dict, list))
    ]
    for root in site_roots:
        for value in _walk_named_values(root, "article_keys"):
            if isinstance(value, list):
                keys.update(_text(item) for item in value if _text(item))
    return keys


def _site_rows(model: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for value in _walk_named_values(model, "site_article_rows"):
        rows.extend(_article_rows(value))

    banks = model.get("lens_banks")
    if isinstance(banks, dict):
        for key in SITE_LENS_KEYS:
            rows.extend(_article_rows(banks.get(key)))

    keys = _site_article_keys(model)
    if not keys:
        return rows

    indexed: dict[str, dict[str, Any]] = {}
    for row in _all_article_rows(model):
        for field in ("article_id", "url", "title", "canonical_url", "external_url"):
            value = _text(row.get(field))
            if value:
                indexed.setdefault(value, row)
    for key in sorted(keys):
        row = indexed.get(key)
        if row is not None:
            rows.append(row)
        elif key.startswith(("http://", "https://")):
            rows.append({"url": key})
        else:
            rows.append({"article_id": key})
    return rows


def fingerprint_records(model: dict[str, Any]) -> set[tuple[str, ...]]:
    records: set[tuple[str, ...]] = set()
    for surface in CORE_SURFACES:
        rows = _direct_surface_rows(model, surface)
        if not rows:
            rows = _projected_surface_rows(model, surface)
        for row in rows:
            record = _record(surface, row)
            if record is not None:
                records.add(record)
    for row in _site_rows(model):
        record = _record("site_article_rows", row)
        if record is not None:
            records.add(record)
    return records


def fingerprint_hash(records: set[tuple[str, ...]]) -> str:
    payload = json.dumps(sorted(records), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def detect_delta(old_model: dict[str, Any], new_model: dict[str, Any]) -> tuple[bool, int, str, str, int]:
    old_records = fingerprint_records(old_model)
    new_records = fingerprint_records(new_model)
    old_hash = fingerprint_hash(old_records)
    new_hash = fingerprint_hash(new_records)
    changed_count = len(old_records.symmetric_difference(new_records))
    alert_delta = bool(new_records) and old_hash != new_hash
    return alert_delta, changed_count, old_hash, new_hash, len(new_records)


# ── delta 아티팩트 (D7-AJ-2) ─────────────────────────────────────────────────
# 시간당 delta 알림 payload를 '한 번' 생성해 Telegram/Teams가 재수집 없이 공유한다.
# 기존 fingerprint/GITHUB_OUTPUT 계약은 불변이며, 이 블록은 --delta-artifact를 줄 때만
# 활성화된다. stdout에는 기사 본문/제목을 절대 출력하지 않는다(내용 없는 로그 유지).
_KST = timezone(timedelta(hours=9))
LIVE_SOURCE = "live-delta"
MOCK_SOURCE = "mock-delta"
VALID_SOURCE_OVERRIDES = (LIVE_SOURCE, MOCK_SOURCE, "test-delta")
ARTIFACT_MAX_ARTICLES = 5
ARTIFACT_MAX_ARTICLES_CEILING = 20
ARTIFACT_SUMMARY_MAX = 200
_NEWS_MODE_MARKER = re.compile(r"<!--news-data-mode:([a-z_]+)-->")


def _fmt_kst(iso: Any) -> str:
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_KST).strftime("%Y-%m-%d %H:%M")


def _parse_dt(iso: Any) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _row_published(row: dict[str, Any]) -> str:
    pub = row.get("published_at")
    if not pub:
        pub = (row.get("provenance") or {}).get("published_at") if isinstance(row.get("provenance"), dict) else None
    return _text(pub)


def _article_key(row: dict[str, Any]) -> str:
    for field in ("article_id", "canonical_url", "external_url", "url", "title"):
        value = _text(row.get(field))
        if value:
            return value
    return ""


def _row_url(row: dict[str, Any]) -> str:
    for field in ("external_url", "url", "canonical_url", "original_url"):
        value = _text(row.get(field))
        if value.lower().startswith(("http://", "https://")):
            return value
    return ""


def _surface_records(model: dict[str, Any]) -> Iterable[tuple[tuple[str, ...], dict[str, Any]]]:
    """fingerprint_records와 동일한 surface 순회를 하되, (record, row) 쌍을 함께 돌려준다."""
    for surface in CORE_SURFACES:
        rows = _direct_surface_rows(model, surface)
        if not rows:
            rows = _projected_surface_rows(model, surface)
        for row in rows:
            record = _record(surface, row)
            if record is not None:
                yield record, row
    for row in _site_rows(model):
        record = _record("site_article_rows", row)
        if record is not None:
            yield record, row


def _surface_pairs(model: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """delta_classifier에 넘길 (surface, row) 쌍. fingerprint와 동일한 surface 순회."""
    return [(record[0], row) for record, row in _surface_records(model)]


def _raw_candidate_count(old_model: dict[str, Any], new_model: dict[str, Any]) -> int:
    """dedup 이전 raw 변동 후보 수 — new surface record 중 old에 정확히 없는 것(진단용)."""
    old_records = fingerprint_records(old_model)
    return sum(1 for record, _ in _surface_records(new_model) if record not in old_records)


def _delta_rows(old_model: dict[str, Any], new_model: dict[str, Any]) -> list[dict[str, Any]]:
    """new에는 있고 old에는 없는(신규·변경) 기사 행을 최신 published 우선으로 돌려준다.

    article_key(또는 URL) 기준으로 중복을 제거한다 — 한 기사가 여러 surface에 나와도 1건."""
    old_records = fingerprint_records(old_model)
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for record, row in _surface_records(new_model):
        if record in old_records:
            continue
        key = _article_key(row)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(row)
    _epoch = datetime.min.replace(tzinfo=timezone.utc)
    out.sort(key=lambda r: (_parse_dt(_row_published(r)) or _epoch), reverse=True)
    return out


def _artifact_entry(row: dict[str, Any]) -> dict[str, str]:
    published_at = _row_published(row)
    category = (row.get("cat") or row.get("category_label")
                or row.get("aiCategoryLabel") or row.get("tag"))
    summary = _text(row.get("snippet") or row.get("whyImportant"))
    return {
        "article_key": _article_key(row),
        "title": _text(row.get("title")),
        "published_at": published_at,
        "published_kst": _fmt_kst(published_at),
        "source": _text(row.get("source") or row.get("display_source")),
        "category": _text(category),
        "hdec_relevance": _text(row.get("radarReason") or row.get("whyImportant")),
        "summary": summary[:ARTIFACT_SUMMARY_MAX],
        "url": _row_url(row),
    }


def _artifact_entry_from_classified(article: "delta_classifier.ClassifiedArticle") -> dict[str, Any]:
    """분류된 기사(대표 row)에 change_type/change_reasons/before/after + 정책 결과를 붙인다.

    아티팩트에 실리는 기사는 전부 hourly_eligible=true·hourly_suppression_reasons=[]다
    (정책에서 걸린 기사는 articles에 넣지 않고 카운트로만 남긴다). 필드를 명시적으로 싣는
    이유는 소비자가 '이건 통과한 것'임을 아티팩트만 보고 확인할 수 있게 하기 위함이다."""
    entry = _artifact_entry(article.representative)
    entry["change_type"] = article.change_type
    entry["change_reasons"] = list(article.change_reasons)
    entry["before"] = article.before
    entry["after"] = article.after
    entry["hourly_eligible"] = article.hourly_eligible
    entry["hourly_suppression_reasons"] = list(article.hourly_suppression_reasons)
    # D7-AK-4B shadow telemetry. 기존 articles 선택·정렬에는 관여하지 않는다.
    entry["shadow_urgency_status"] = article.shadow_urgency_status
    entry["shadow_would_pass"] = article.shadow_would_pass
    entry["shadow_confirmed_event_types"] = list(
        article.shadow_confirmed_event_types
    )
    entry["shadow_ambiguous_event_types"] = list(
        article.shadow_ambiguous_event_types
    )
    entry["shadow_negative_contexts"] = list(article.shadow_negative_contexts)
    entry["shadow_evidence_source"] = article.shadow_evidence_source
    return entry


def _news_data_mode(path: Path) -> str:
    """new 대시보드의 news-data-mode 마커. 읽기 실패/마커 없음이면 ''(= live가 아님 → mock 취급).

    이 헬퍼는 절대 예외를 던지지 않는다 — 입력 검증(load_preview_model)보다 먼저 호출되므로,
    깨진 파일은 여기서 죽지 않고 검증 단계의 fail-closed 경로로 넘어가야 한다."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return ""
    match = _NEWS_MODE_MARKER.search(text)
    return match.group(1) if match else ""


def _judgment(model: dict[str, Any]) -> str:
    for value in _walk_named_values(model, "executive_one_liner"):
        text = _text(value)
        if text:
            return text[:200]
    return ""


def _now_kst(override: str | None) -> datetime:
    raw = (override or "").strip()
    if raw:
        try:
            dt = datetime.fromisoformat(raw)
            return (dt if dt.tzinfo else dt.replace(tzinfo=_KST)).astimezone(_KST)
        except ValueError:
            pass
    return datetime.now(_KST)


def _resolve_artifact_max_articles() -> int:
    """How many meaningful articles to carry in the shared artifact.

    Default is ``ARTIFACT_MAX_ARTICLES`` (5) so the hourly dashboard/Telegram path is
    unchanged. The Teams AI news watch (D7-AK-6C) overrides ``DELTA_ARTIFACT_MAX_ARTICLES``
    to give the article-level Teams sender headroom to select up to ten — without
    widening any other consumer's scope. Missing/invalid → the default; clamped to a
    ceiling so a bad value can never flood the artifact.
    """
    raw = os.environ.get("DELTA_ARTIFACT_MAX_ARTICLES", "").strip()
    if not raw:
        return ARTIFACT_MAX_ARTICLES
    try:
        value = int(raw)
    except ValueError:
        return ARTIFACT_MAX_ARTICLES
    return max(1, min(value, ARTIFACT_MAX_ARTICLES_CEILING))


def build_delta_payload(
    new_model: dict[str, Any],
    classification: "delta_classifier.DeltaClassification",
    *,
    raw_alert_delta: bool,
    changed_count: int,
    raw_candidate_count: int,
    news_mode: str,
    source_override: str | None = None,
    now: datetime,
) -> dict[str, Any]:
    """공유 delta 아티팩트(dict)를 만든다. 발송 자격을 통과한 기사만·표시정렬·최대 5건.

    source는 명시 override가 있으면 그것을, 없으면 new 대시보드의 news-data-mode 마커로
    live-delta/mock-delta를 판별한다(가짜 live 방지 — 마커가 live가 아니면 mock-delta).
    alert_delta는 hourly_eligible_count>=1일 때만 참이며(GITHUB_OUTPUT과 동일 경로), 무의미
    변동/저가치/stale만 있으면 false → 발송 0건 계약을 지킨다. raw fingerprint 변화와 정책에서
    걸린 건수는 진단 카운트로만 남긴다(기사 자체는 articles에 넣지 않는다).

    now는 호출자가 이미 해석한 기준시각이다 — GITHUB_OUTPUT/신선도 게이트와 같은 값을 쓴다.
    schema_version은 1을 유지한다: 추가 필드는 전부 optional additive라 legacy v1 loader가
    그대로 안전하고, 새 아티팩트도 legacy 소비자에게 v1으로 읽힌다.
    """
    articles = [
        _artifact_entry_from_classified(article)
        for article in classification.meaningful[:_resolve_artifact_max_articles()]
    ]
    if source_override in VALID_SOURCE_OVERRIDES:
        source = source_override
    else:
        source = LIVE_SOURCE if news_mode == "live" else MOCK_SOURCE
    return {
        "schema_version": 1,
        "generated_at": now.isoformat(timespec="minutes"),
        "generated_kst": now.strftime("%Y-%m-%d %H:%M"),
        "source": source,
        "alert_delta": classification.hourly_eligible_count >= 1,
        # 진단 카운트 (raw fingerprint 변화는 게이트가 아니라 관측용).
        "changed_count": int(changed_count),
        "raw_alert_delta": bool(raw_alert_delta),
        "raw_changed_count": int(changed_count),
        "raw_candidate_count": int(raw_candidate_count),
        "deduplicated_candidate_count": classification.deduplicated_count,
        # 분류상 meaningful 전체(정책 적용 전) — 무엇을 걸렀는지 추적용.
        "pre_policy_meaningful_count": classification.pre_policy_meaningful_count,
        # 발송 대상 수 — meaningful_candidate_count == hourly_eligible_count (단일 값).
        "meaningful_candidate_count": classification.meaningful_count,
        "hourly_eligible_count": classification.hourly_eligible_count,
        "ignored_candidate_count": classification.ignored_count,
        "suppressed_low_value_count": classification.suppressed_low_value_count,
        "suppressed_stale_count": classification.suppressed_stale_count,
        "suppressed_unknown_time_count": classification.suppressed_unknown_time_count,
        # confirmed-event evidence는 진단용 shadow 계약이며 alert_delta를 바꾸지 않는다.
        "shadow_alert_delta": classification.shadow_would_pass_count >= 1,
        "shadow_would_pass_count": classification.shadow_would_pass_count,
        "shadow_confirmed_count": classification.shadow_confirmed_count,
        "shadow_ambiguous_count": classification.shadow_ambiguous_count,
        "shadow_blocked_count": classification.shadow_blocked_count,
        "shadow_none_count": classification.shadow_none_count,
        "shadow_unavailable_count": classification.shadow_unavailable_count,
        # 하위호환: 기존 소비자는 new_candidate_count를 읽는다(= dedup된 변동 후보 수).
        "new_candidate_count": classification.deduplicated_count,
        "change_type_counts": dict(classification.change_type_counts),
        "duplicate_collapsed_count": classification.duplicate_collapsed_count,
        "judgment": _judgment(new_model),
        "articles": articles,
    }


def write_delta_artifact(path: str, payload: dict[str, Any]) -> None:
    Path(path).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_github_output(
    path: str,
    *,
    alert_delta: bool,
    raw_alert_delta: bool = False,
    changed_count: int = 0,
    raw_candidate_count: int = 0,
    deduplicated_candidate_count: int = 0,
    pre_policy_meaningful_count: int = 0,
    meaningful_count: int = 0,
    hourly_eligible_count: int = 0,
    ignored_count: int = 0,
    suppressed_low_value_count: int = 0,
    suppressed_stale_count: int = 0,
    suppressed_unknown_time_count: int = 0,
    shadow_alert_delta: bool = False,
    shadow_would_pass_count: int = 0,
    shadow_confirmed_count: int = 0,
    shadow_ambiguous_count: int = 0,
    shadow_blocked_count: int = 0,
    shadow_none_count: int = 0,
    shadow_unavailable_count: int = 0,
) -> None:
    """GITHUB_OUTPUT에 alert_delta(=hourly_eligible>=1)와 진단 카운트만 쓴다.

    기사 제목/URL/본문·비밀값은 절대 쓰지 않는다(전부 bool/int)."""
    def flag(value: bool) -> str:
        return "true" if value else "false"

    with Path(path).open("a", encoding="utf-8") as output:
        output.write(f"alert_delta={flag(alert_delta)}\n")
        output.write(f"raw_alert_delta={flag(raw_alert_delta)}\n")
        output.write(f"changed_count={int(changed_count)}\n")
        output.write(f"raw_candidate_count={int(raw_candidate_count)}\n")
        output.write(f"deduplicated_candidate_count={int(deduplicated_candidate_count)}\n")
        output.write(f"pre_policy_meaningful_count={int(pre_policy_meaningful_count)}\n")
        output.write(f"meaningful_count={int(meaningful_count)}\n")
        output.write(f"hourly_eligible_count={int(hourly_eligible_count)}\n")
        output.write(f"ignored_count={int(ignored_count)}\n")
        output.write(f"suppressed_low_value_count={int(suppressed_low_value_count)}\n")
        output.write(f"suppressed_stale_count={int(suppressed_stale_count)}\n")
        output.write(f"suppressed_unknown_time_count={int(suppressed_unknown_time_count)}\n")
        output.write(f"shadow_alert_delta={flag(shadow_alert_delta)}\n")
        output.write(f"shadow_would_pass_count={int(shadow_would_pass_count)}\n")
        output.write(f"shadow_confirmed_count={int(shadow_confirmed_count)}\n")
        output.write(f"shadow_ambiguous_count={int(shadow_ambiguous_count)}\n")
        output.write(f"shadow_blocked_count={int(shadow_blocked_count)}\n")
        output.write(f"shadow_none_count={int(shadow_none_count)}\n")
        output.write(f"shadow_unavailable_count={int(shadow_unavailable_count)}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Detect dashboard alert-surface delta")
    parser.add_argument("old_dashboard", type=Path)
    parser.add_argument("new_dashboard", type=Path)
    parser.add_argument("--github-output", metavar="PATH")
    parser.add_argument("--delta-artifact", metavar="PATH",
                        help="신규·변경 기사 delta payload(JSON)를 이 경로에 생성 (Telegram/Teams 공유)")
    parser.add_argument("--source", metavar="LABEL",
                        help="delta source 라벨 override (기본은 new 대시보드 마커로 판별)")
    parser.add_argument("--now", metavar="ISO",
                        help="기준 시각 override — generated_at과 신선도 게이트가 함께 쓴다 "
                             "(테스트/재현용; 기본은 실제 벽시계 KST)")
    args = parser.parse_args(argv)

    # 기준시각과 news_mode를 '한 번' 해석해 분류 게이트·GITHUB_OUTPUT·아티팩트가 모두
    # 같은 값을 쓰게 한다 — 셋이 다른 기준시각을 보면 안 된다(D7-AK-2 §5).
    reference_dt = _now_kst(args.now)
    news_mode = _news_data_mode(args.new_dashboard)

    try:
        old_model = load_preview_model(args.old_dashboard)
        new_model = load_preview_model(args.new_dashboard)
        # raw fingerprint 변화(진단) — 게이트가 아니라 관측용으로만 쓴다.
        raw_alert_delta, changed_count, old_hash, new_hash, new_count = detect_delta(
            old_model, new_model
        )
        # 의미 기반 분류 + 시간당 알림 자격 — --delta-artifact 유무와 무관하게 항상 실행하며,
        # GITHUB_OUTPUT alert_delta와 artifact alert_delta가 이 단일 결과를 공유한다.
        classification = delta_classifier.classify_delta(
            _surface_pairs(old_model), _surface_pairs(new_model),
            news_mode=news_mode, reference_dt=reference_dt,
        )
        raw_candidate_count = _raw_candidate_count(old_model, new_model)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        if args.github_output:
            _write_github_output(args.github_output, alert_delta=False)
        print("ERROR: dashboard alert delta input is invalid; alert gate remains closed", file=sys.stderr)
        return 2

    alert_delta = classification.hourly_eligible_count >= 1

    # 내용 없는 로그 — 카운트/해시만(기사 제목/본문 미노출).
    print(
        "dashboard alert delta: "
        f"changed_count={changed_count} raw_candidates={raw_candidate_count} "
        f"deduplicated={classification.deduplicated_count} "
        f"pre_policy_meaningful={classification.pre_policy_meaningful_count} "
        f"meaningful={classification.meaningful_count} "
        f"ignored={classification.ignored_count} "
        f"old_hash={old_hash} new_hash={new_hash} new_records={new_count}"
    )
    print(
        "delta quality: "
        f"news_mode={news_mode or 'unknown'} "
        f"hourly_eligible={classification.hourly_eligible_count} "
        f"suppressed_low_value={classification.suppressed_low_value_count} "
        f"suppressed_stale={classification.suppressed_stale_count} "
        f"suppressed_unknown_time={classification.suppressed_unknown_time_count}"
    )
    print(
        f"alert_delta={'true' if alert_delta else 'false'} "
        f"raw_alert_delta={'true' if raw_alert_delta else 'false'}"
    )
    print(
        "shadow urgency: "
        f"eligible={classification.hourly_eligible_count} "
        f"confirmed={classification.shadow_confirmed_count} "
        f"ambiguous={classification.shadow_ambiguous_count} "
        f"blocked={classification.shadow_blocked_count} "
        f"none={classification.shadow_none_count} "
        f"unavailable={classification.shadow_unavailable_count} "
        f"shadow_alert_delta={'true' if classification.shadow_would_pass_count >= 1 else 'false'}"
    )
    if args.github_output:
        _write_github_output(
            args.github_output,
            alert_delta=alert_delta,
            raw_alert_delta=raw_alert_delta,
            changed_count=changed_count,
            raw_candidate_count=raw_candidate_count,
            deduplicated_candidate_count=classification.deduplicated_count,
            pre_policy_meaningful_count=classification.pre_policy_meaningful_count,
            meaningful_count=classification.meaningful_count,
            hourly_eligible_count=classification.hourly_eligible_count,
            ignored_count=classification.ignored_count,
            suppressed_low_value_count=classification.suppressed_low_value_count,
            suppressed_stale_count=classification.suppressed_stale_count,
            suppressed_unknown_time_count=classification.suppressed_unknown_time_count,
            shadow_alert_delta=classification.shadow_would_pass_count >= 1,
            shadow_would_pass_count=classification.shadow_would_pass_count,
            shadow_confirmed_count=classification.shadow_confirmed_count,
            shadow_ambiguous_count=classification.shadow_ambiguous_count,
            shadow_blocked_count=classification.shadow_blocked_count,
            shadow_none_count=classification.shadow_none_count,
            shadow_unavailable_count=classification.shadow_unavailable_count,
        )

    if args.delta_artifact:
        payload = build_delta_payload(
            new_model, classification,
            raw_alert_delta=raw_alert_delta, changed_count=changed_count,
            raw_candidate_count=raw_candidate_count,
            news_mode=news_mode,
            source_override=args.source, now=reference_dt)
        try:
            write_delta_artifact(args.delta_artifact, payload)
        except OSError:
            print("ERROR: could not write delta artifact; alert gate remains closed",
                  file=sys.stderr)
            return 2
        # 내용 없는 로그(제목/본문 미노출) — source/카운트만.
        print(f"delta artifact: source={payload['source']} "
              f"alert_delta={'true' if payload['alert_delta'] else 'false'} "
              f"meaningful={payload['meaningful_candidate_count']} "
              f"ignored={payload['ignored_candidate_count']} "
              f"articles={len(payload['articles'])} "
              f"new_candidates={payload['new_candidate_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
