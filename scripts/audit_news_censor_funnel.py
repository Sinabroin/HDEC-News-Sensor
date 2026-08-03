#!/usr/bin/env python3
"""Audit one exact News Censor artifact through selector, renderer, and DOM.

The output contains aggregate counters and safe hashes only. It never performs
network I/O, sends a notification, writes production state, or emits URLs.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import date
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
for item in (ROOT, SCRIPTS):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from app import publisher_direct, teams_ai_push  # noqa: E402
import build_news_censor  # noqa: E402
from build_executive_brief import load_brief_json  # noqa: E402


class FunnelHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cards: list[dict] = []
        self.dom_ids: list[str] = []
        self.portal_href_count = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        classes = set(str(values.get("class") or "").split())
        if values.get("id"):
            self.dom_ids.append(str(values["id"]))
        if tag == "article" and classes & {"lead", "nitem"} and values.get("data-article"):
            self.cards.append({
                "article_id": str(values.get("data-article") or ""),
                "categories": tuple(
                    token for token in str(values.get("data-t") or "").split()
                    if token in build_news_censor.CATEGORY_LABELS
                ),
                "hidden": "hide" in classes,
            })
        if tag == "a" and publisher_direct.portal_provider(values.get("href")):
            self.portal_href_count += 1


def _extract_model(html: str) -> dict:
    match = re.search(
        r'<script type="application/json" id="article-data">(.*?)</script>',
        html,
        re.S,
    )
    if not match:
        raise ValueError("News Censor article JSON island missing")
    value = json.loads(match.group(1))
    if not isinstance(value, dict):
        raise ValueError("News Censor article JSON island must be an object")
    return value


def _increment_loss(audit: dict, reason: str, article_ids: list[str]) -> None:
    loss = audit["stage_losses"][reason]
    loss["count"] += len(article_ids)
    loss["article_ids"].extend(article_ids)
    audit["stage_loss_reason_counts"][reason] = loss["count"]


def audit_artifact(brief: dict, html: str, *, article_limit: int) -> dict:
    selection_audit: dict = {}
    model = build_news_censor.build_model(
        brief,
        edition=date.fromisoformat(str(brief.get("date_kst") or "") or date.today().isoformat()),
        article_limit=article_limit,
        audit_sink=selection_audit,
    )
    parser = FunnelHTMLParser()
    parser.feed(html)
    html_model = _extract_model(html)

    model_articles = model.get("articles") or []
    model_ids = [str(row.get("id") or "") for row in model_articles]
    serialized_ids = [str(value) for value in html_model]
    card_ids = [row["article_id"] for row in parser.cards]
    public_ids = list(dict.fromkeys(
        row["article_id"] for row in parser.cards if not row["hidden"]
    ))
    missing_required = [
        str(row.get("id") or "missing")
        for row in model_articles
        if any(not row.get(key) for key in ("id", "title", "source", "published_at", "url", "categories"))
    ]
    if missing_required:
        _increment_loss(selection_audit, "renderer_missing_required_field", missing_required)
    missing_cards = sorted(set(model_ids) - set(card_ids))
    if missing_cards:
        _increment_loss(selection_audit, "template_filter_excluded", missing_cards)
    serialization_delta = abs(len(model_ids) - len(serialized_ids))
    serialization_mismatches = sorted(set(model_ids) ^ set(serialized_ids))
    if serialization_delta or serialization_mismatches:
        _increment_loss(
            selection_audit,
            "serialization_loss",
            serialization_mismatches
            or [f"serialization_{index}" for index in range(serialization_delta)],
        )
    duplicate_card_ids = [
        value for value, count in Counter(card_ids).items() if value and count > 1
    ]
    duplicate_dom_ids = [
        value for value, count in Counter(parser.dom_ids).items() if value and count > 1
    ]
    duplicate_ids = sorted(set(duplicate_card_ids + duplicate_dom_ids))
    if duplicate_ids:
        _increment_loss(selection_audit, "duplicate_dom_key", duplicate_ids)

    selection_audit["stage_counts"].update({
        "renderer_input_count": len(model_articles),
        "rendered_card_count": len(parser.cards),
        "public_html_card_count": len(public_ids),
    })
    category_rendered = Counter()
    for row in parser.cards:
        if row["article_id"] in public_ids:
            category_rendered.update(set(row["categories"]))
    canonical_rows = list(build_news_censor._candidate_rows(brief))
    teams_candidates = teams_ai_push.select_teams_push_candidates(canonical_rows)
    backfill_model_ids = {
        str(row.get("id") or "") for row in model_articles if row.get("is_backfill")
    }
    teams_ids = {
        str(getattr(row, "article_key", "") or getattr(row, "article_id", ""))
        for row in teams_candidates
    }
    query_coverage = (brief.get("collector_health") or {}).get("category_query_coverage") or {}
    health = brief.get("collector_health") or {}
    resolution = health.get("publisher_resolution") or {}
    resolution_categories = resolution.get("per_category") or {}
    current_categories = health.get("current_verified_category_counts") or {}
    carry_categories = health.get("carry_forward_category_counts") or {}
    publisher_counts = Counter()
    display_counts = Counter()
    for row in canonical_rows:
        memberships = {
            str(value) for value in row.get("category_memberships") or []
            if str(value) in build_news_censor.PRIMARY_CATEGORY_IDS
        }
        publisher_counts.update(memberships)
    for decision in selection_audit["article_decisions"]:
        if decision["display_eligibility"]:
            display_counts.update(decision["assigned_categories"])
    selected_counts = {
        item["id"]: int(item["count"])
        for item in model.get("categories") or []
        if item["id"] != "all"
    }
    category_counts = {
        category: {
            "raw": int((query_coverage.get(category) or {}).get("added_count") or 0),
            "resolution_attempts": int(
                (resolution_categories.get(category) or {}).get("attempts") or 0
            ),
            "verified_successes": int(
                (resolution_categories.get(category) or {}).get("successes") or 0
            ),
            "cache_hits": int(
                (resolution_categories.get(category) or {}).get("cache_hits") or 0
            ),
            "current_verified_union": int(current_categories.get(category) or 0),
            "carry_forward_union": int(carry_categories.get(category) or 0),
            "publisher_eligible": int(publisher_counts[category]),
            "display_eligible": int(display_counts[category]),
            "selected": int(selected_counts.get(category, 0)),
            "rendered": int(category_rendered[category]),
        }
        for category in build_news_censor.PRIMARY_CATEGORY_IDS
    }
    final_reason_counts = Counter(
        row["final_rejection_reason"]
        for row in selection_audit["article_decisions"]
        if not row["selected"]
    )
    selection_audit.update({
        "renderer_accounting": {
            **dict(model.get("accounting") or {}),
            "actual_dom_article_card_count": len(parser.cards),
            "actual_distinct_dom_article_ids": len(set(card_ids)),
            "actual_public_displayed_count": len(public_ids),
            "actual_category_filter_counts": dict(sorted(category_rendered.items())),
        },
        "category_counts": category_counts,
        "final_rejection_reason_counts": dict(sorted(final_reason_counts.items())),
        "portal_href_count": parser.portal_href_count,
        "quarantined_displayed_count": int(model.get("published_quarantine_count") or 0),
        "canonical_duplicate_count": int(final_reason_counts["canonical_duplicate"]),
        "event_duplicate_count": int(final_reason_counts["event_duplicate"]),
        "primary_window_count": int((model.get("coverage") or {}).get("primary_window_count") or 0),
        "backfill_count": int((model.get("coverage") or {}).get("backfill_article_count") or 0),
        "teams_candidate_count": len(teams_candidates),
        "teams_carry_forward_candidate_count": sum(
            bool(getattr(candidate, "article", {}).get("carried_forward"))
            for candidate in teams_candidates
        ),
        "teams_backfill_candidate_count": len(backfill_model_ids & teams_ids),
        "safety_counters": {
            "external_network_calls": 0,
            "smtp_attempts": 0,
            "teams_sends": 0,
            "telegram_sends": 0,
            "production_state_writes": 0,
        },
    })
    selection_audit["accounting_pass"] = bool(
        selection_audit["eligible_to_final_reconciliation"]["balanced"]
        and model_ids == serialized_ids == card_ids
        and len(model_articles) == len(parser.cards) == len(public_ids)
        and len(set(card_ids)) == len(card_ids)
        and parser.portal_href_count == 0
        and selection_audit["quarantined_displayed_count"] == 0
    )
    return selection_audit


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit one exact News Censor artifact")
    parser.add_argument("--brief-json", type=Path, required=True)
    parser.add_argument("--html", type=Path)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--article-limit", type=int, default=build_news_censor.PUBLIC_HARD_MAX)
    parser.add_argument("--require-live", action="store_true")
    args = parser.parse_args(argv)

    brief = load_brief_json(args.brief_json.resolve())
    build_news_censor.validate_brief_artifact(brief, require_live=args.require_live)
    model = build_news_censor.build_model(
        brief,
        edition=date.fromisoformat(str(brief.get("date_kst") or "") or date.today().isoformat()),
        article_limit=args.article_limit,
    )
    html = (
        args.html.resolve().read_text(encoding="utf-8")
        if args.html else build_news_censor.render_html(model)
    )
    audit = audit_artifact(brief, html, article_limit=args.article_limit)
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "artifact_fingerprint": audit["artifact_fingerprint"],
        "stage_counts": audit["stage_counts"],
        "stage_loss_reason_counts": audit["stage_loss_reason_counts"],
        "primary_window_count": audit["primary_window_count"],
        "backfill_count": audit["backfill_count"],
        "category_counts": audit["category_counts"],
        "distinct_publishers": audit["diversity"]["distinct_publishers"],
        "largest_publisher_share": audit["diversity"]["largest_publisher_share"],
        "portal_href_count": audit["portal_href_count"],
        "accounting_pass": audit["accounting_pass"],
    }, ensure_ascii=False, sort_keys=True))
    return 0 if audit["accounting_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
