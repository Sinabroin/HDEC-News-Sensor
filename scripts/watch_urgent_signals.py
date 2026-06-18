"""Watch-mode CLI: detect newly important executive signals for review.

Examples:
    python3 scripts/watch_urgent_signals.py --dry-run
    python3 scripts/watch_urgent_signals.py --write
    python3 scripts/watch_urgent_signals.py --json

The script never calls Telegram APIs. Queue entries are review-required and
send_allowed=false by construction; real Telegram still goes through the
existing approve_send gate in scripts/send_telegram.py.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import config, collector, urgent_signals, watch_state  # noqa: E402


def _load_mock_articles() -> tuple[list[dict], dict]:
    articles = json.loads((config.DATA_DIR / "mock_articles.json").read_text(encoding="utf-8"))
    return articles, {
        "news_data_mode": "mock",
        "news_source": "mock",
        "fallback_used": False,
        "provider_status": {"mock": "active"},
    }


def _load_live_articles() -> tuple[list[dict], dict]:
    """Fetch current live article metadata without writing to radar.db."""
    from app import live_collector, naver_news_provider

    source_filtered = []
    try:
        google_rows = live_collector.fetch_all(filtered_out=source_filtered)
    except Exception:  # noqa: BLE001 - network/parsing failure becomes honest fallback
        google_rows = []
    google_status = "active" if google_rows else "skipped"

    naver_result = {"status": "disabled", "articles": []}
    if config.NAVER_NEWS_ENABLED:
        try:
            naver_result = naver_news_provider.fetch()
        except Exception:  # noqa: BLE001 - optional provider failure should not stop watch mode
            naver_result = {"status": "error", "articles": []}
    naver_rows = naver_result.get("articles") or []

    combined = collector.merge_provider_articles(google_rows + naver_rows)
    if not combined:
        mock_articles, meta = _load_mock_articles()
        meta.update({
            "news_source": "mock_fallback",
            "fallback_used": True,
            "attempted_mode": "live",
            "provider_status": {
                "google_news_rss": google_status,
                "naver_news_api": naver_result.get("status"),
            },
        })
        return mock_articles, meta

    labels = []
    if google_rows:
        labels.append(live_collector.SOURCE_LABEL)
    if naver_rows:
        labels.append(naver_news_provider.SOURCE_LABEL)
    return combined, {
        "news_data_mode": "live",
        "news_source": " + ".join(labels) or live_collector.SOURCE_LABEL,
        "fallback_used": False,
        "attempted_mode": "live",
        "provider_status": {
            "google_news_rss": google_status,
            "naver_news_api": naver_result.get("status"),
        },
    }


def load_current_articles() -> tuple[list[dict], dict]:
    if config.NEWS_MODE == "live":
        return _load_live_articles()
    return _load_mock_articles()


def format_text(result: dict, state_path: Path, provenance: dict, *, wrote: bool) -> str:
    summary = result["summary"]
    lines = [
        "== HDEC Urgent Signal Watch ==",
        f"news_data_mode: {provenance.get('news_data_mode')}",
        f"news_source: {provenance.get('news_source')}",
        f"fallback_used: {str(bool(provenance.get('fallback_used'))).lower()}",
        f"state_path: {state_path}",
        f"state_write: {'yes' if wrote else 'no'}",
        f"scanned count: {summary['scanned_count']}",
        f"new count: {summary['new_count']}",
        f"urgent candidate count: {summary['urgent_candidate_count']}",
        f"skipped duplicate count: {summary['skipped_duplicate_count']}",
        f"skipped excluded count: {summary['skipped_excluded_count']}",
        "Telegram send: blocked by human review gate",
    ]

    candidates = [
        item for item in result["queue"]
        if item["urgency_class"] in (
            urgent_signals.URGENCY_SEND_CANDIDATE,
            urgent_signals.URGENCY_REVIEW_TODAY,
        )
    ][:5]
    if candidates:
        lines.append("")
        lines.append("Top urgent candidates:")
        for idx, item in enumerate(candidates, start=1):
            lines.append(
                f"{idx}. [{item['urgency_class']}] score={item['urgency_score']:.1f} "
                f"{item['title']}")
            lines.append(f"   why_now: {item['why_now']}")
            lines.append(f"   action: {item['recommended_action']}")
    else:
        lines.append("")
        lines.append("Top urgent candidates: none")

    monitor = [
        item for item in result["queue"]
        if item["urgency_class"] == urgent_signals.URGENCY_MONITOR_ONLY
    ][:3]
    if monitor:
        lines.append("")
        lines.append("Monitor-only repeated clusters:")
        for item in monitor:
            lines.append(f"- {item['title']} ({item['seen_status']})")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Detect newly urgent executive signals and write a review queue.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true",
                       help="scan and print review digest without writing state")
    group.add_argument("--write", action="store_true",
                       help="scan, update state, and write the urgent review queue")
    parser.add_argument("--json", action="store_true",
                        help="print machine-readable result JSON")
    args = parser.parse_args(argv)

    state_path = watch_state.resolve_state_path()
    state = watch_state.load_state(state_path)
    articles, provenance = load_current_articles()
    result = urgent_signals.evaluate_articles(articles, state)
    wrote = False
    if args.write:
        urgent_signals.commit_result(result, state_path)
        wrote = True

    public = urgent_signals.public_result(result)
    public["state_path"] = str(state_path)
    public["state_written"] = wrote
    public["provenance"] = provenance

    if args.json:
        print(json.dumps(public, ensure_ascii=False, indent=2))
    else:
        print(format_text(public, state_path, provenance, wrote=wrote))

    # Contract marker requested by operators and verifiers.
    if args.json:
        print("Telegram send: blocked by human review gate", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
