#!/usr/bin/env python3
"""Detect material alert-surface changes between two static dashboards.

The detector reads only the ``script#preview-model`` JSON island. It emits a
stable fingerprint from article identity and ranking fields, never article
content or environment values. A malformed input fails closed. An empty new
candidate set is valid but can never open the alert gate.

Its ``--github-output`` (``alert_delta`` only) and stdout log stay content-free
and are unchanged. With the optional ``--delta-artifact PATH`` it additionally
writes a shared delta payload (new/changed articles, newest first, at most 5) so
the Telegram and Teams senders consume one file instead of each re-fetching the
news. That artifact carries only the short reason/summary already public in the
dashboard — no raw body, no secrets, no environment values.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable


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


def _news_data_mode(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
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


def build_delta_payload(
    old_model: dict[str, Any],
    new_model: dict[str, Any],
    *,
    alert_delta: bool,
    changed_count: int,
    news_mode: str,
    source_override: str | None = None,
    now_override: str | None = None,
) -> dict[str, Any]:
    """공유 delta 아티팩트(dict)를 만든다. 신규·변경 기사만·최신순·최대 5건.

    source는 명시 override가 있으면 그것을, 없으면 new 대시보드의 news-data-mode 마커로
    live-delta/mock-delta를 판별한다(가짜 live 방지 — 마커가 live가 아니면 mock-delta).
    alert_delta는 '보여줄 신규 기사가 있을 때'만 참으로 내려, delta=false → 발송 0건 계약을 지킨다.
    """
    rows = _delta_rows(old_model, new_model)
    articles = [_artifact_entry(row) for row in rows[:ARTIFACT_MAX_ARTICLES]]
    if source_override in VALID_SOURCE_OVERRIDES:
        source = source_override
    else:
        source = LIVE_SOURCE if news_mode == "live" else MOCK_SOURCE
    now = _now_kst(now_override)
    return {
        "schema_version": 1,
        "generated_at": now.isoformat(timespec="minutes"),
        "generated_kst": now.strftime("%Y-%m-%d %H:%M"),
        "source": source,
        "alert_delta": bool(alert_delta and articles),
        "changed_count": int(changed_count),
        "new_candidate_count": len(rows),
        "judgment": _judgment(new_model),
        "articles": articles,
    }


def write_delta_artifact(path: str, payload: dict[str, Any]) -> None:
    Path(path).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_github_output(path: str, alert_delta: bool) -> None:
    with Path(path).open("a", encoding="utf-8") as output:
        output.write(f"alert_delta={'true' if alert_delta else 'false'}\n")


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
                        help="generated_at 기준 시각 override (테스트용; 기본은 실제 벽시계 KST)")
    args = parser.parse_args(argv)

    try:
        old_model = load_preview_model(args.old_dashboard)
        new_model = load_preview_model(args.new_dashboard)
        alert_delta, changed_count, old_hash, new_hash, new_count = detect_delta(
            old_model, new_model
        )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        if args.github_output:
            _write_github_output(args.github_output, False)
        print("ERROR: dashboard alert delta input is invalid; alert gate remains closed", file=sys.stderr)
        return 2

    print(
        "dashboard alert delta: "
        f"changed_count={changed_count} old_hash={old_hash} new_hash={new_hash} "
        f"new_candidates={new_count}"
    )
    print(f"alert_delta={'true' if alert_delta else 'false'}")
    if args.github_output:
        _write_github_output(args.github_output, alert_delta)

    if args.delta_artifact:
        payload = build_delta_payload(
            old_model, new_model,
            alert_delta=alert_delta, changed_count=changed_count,
            news_mode=_news_data_mode(args.new_dashboard),
            source_override=args.source, now_override=args.now)
        try:
            write_delta_artifact(args.delta_artifact, payload)
        except OSError:
            print("ERROR: could not write delta artifact; alert gate remains closed",
                  file=sys.stderr)
            return 2
        # 내용 없는 로그(제목/본문 미노출) — source/카운트만.
        print(f"delta artifact: source={payload['source']} "
              f"alert_delta={'true' if payload['alert_delta'] else 'false'} "
              f"articles={len(payload['articles'])} "
              f"new_candidates={payload['new_candidate_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
