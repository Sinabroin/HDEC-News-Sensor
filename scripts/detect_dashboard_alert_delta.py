#!/usr/bin/env python3
"""Detect material alert-surface changes between two static dashboards.

The detector reads only the ``script#preview-model`` JSON island. It emits a
stable fingerprint from article identity and ranking fields, never article
content or environment values. A malformed input fails closed. An empty new
candidate set is valid but can never open the alert gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
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


def _write_github_output(path: str, alert_delta: bool) -> None:
    with Path(path).open("a", encoding="utf-8") as output:
        output.write(f"alert_delta={'true' if alert_delta else 'false'}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Detect dashboard alert-surface delta")
    parser.add_argument("old_dashboard", type=Path)
    parser.add_argument("new_dashboard", type=Path)
    parser.add_argument("--github-output", metavar="PATH")
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
