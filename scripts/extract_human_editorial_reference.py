#!/usr/bin/env python3
"""D7-AK-6E R4-R7 §3 — sanitizing extractor for human final Weekly Briefs.

Reads private human-authored T&I Brief HTML files (never committed) and emits
only the whitelisted editorial content into the repository-owned sanitized
corpus under ``data/editorial_learning/``:

* edition metadata (issue label, issue date, coverage anchor);
* headline (hero) title, category, Editor's Summary, hero source;
* briefing cards: category, title, human summary, source, date, publisher
  URL, visible order.

Everything else — Autoway wrapper markup, scripts, session/SSO values, hidden
inputs, account identifiers, internal hosts, embedded images — is never
extracted because extraction is whitelist-only, and a post-write privacy scan
fails the run if any internal marker still appears in an output file.

Fail-closed: a file whose Brief body (hero + cards) cannot be identified is
reported and produces no output (the Autoway saved page without its sidecar
``_files`` directory is the canonical example).

Usage:
    python3 scripts/extract_human_editorial_reference.py \
        --source-dir /path/to/private/human-editorial \
        [--output-root data/editorial_learning]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PARSER_VERSION = "human-editorial-extract-v1"
EVIDENCE_GOLD_PLUS = "gold_plus"
EVIDENCE_GOLD_SELECTED = "gold_selected"
EVIDENCE_SILVER_CANDIDATE = "silver_candidate"

# Internal wrapper / identity markers that must never survive into output.
PRIVACY_FORBIDDEN_MARKERS = (
    "autoway",
    "hyundai.net",
    "hmgscript",
    "sso",
    "session",
    "cookie",
    "loginform",
    "groupware",
    "hmail",
    ".axd",
)

_ISSUE_RE = re.compile(
    r"(\d{4})년\s*(\d{1,2})월\s*(\d)주차\s*\((\d{4})\.(\d{2})\.(\d{2})\)"
)

# Human-authored pages write void elements without self-closing slashes;
# they must never affect element-depth bookkeeping.
_VOID_TAGS = frozenset(
    {"img", "br", "hr", "meta", "link", "input", "source", "wbr", "col", "area", "base"}
)


class _BriefParser(HTMLParser):
    """Whitelist state machine over hero / ednote / card structures."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_text = ""
        self._in_title = False
        self.hero_title = ""
        self.hero_category = ""
        self._hero_depth = 0
        self._in_hero_h2 = False
        self._in_hero_foot = 0
        self.ednote_text: list[str] = []
        self.ednote_source = ""
        self.ednote_source_url = ""
        self.ednote_date = ""
        self._ednote_depth = 0
        self._ednote_anchor = False
        self._ednote_in_dt = False
        self.cards: list[dict] = []
        self._card: dict | None = None
        self._card_depth = 0
        self._in_chip = False
        self._in_h3 = False
        self._in_sum = 0
        self._src_anchor = False
        self._src_in_dt = False

    @staticmethod
    def _classes(attrs) -> set[str]:
        for key, value in attrs:
            if key == "class":
                return set((value or "").split())
        return set()

    @staticmethod
    def _href(attrs) -> str:
        for key, value in attrs:
            if key == "href":
                return (value or "").strip()
        return ""

    def handle_startendtag(self, tag, attrs):
        if tag in _VOID_TAGS:
            return
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_starttag(self, tag, attrs):
        if tag in _VOID_TAGS:
            return
        classes = self._classes(attrs)
        if tag == "title":
            self._in_title = True
        if self._hero_depth:
            self._hero_depth += 1
            if tag == "h2":
                self._in_hero_h2 = True
            if "hero-foot" in classes:
                self._in_hero_foot = self._hero_depth
        elif tag == "section" and "hero" in classes:
            self._hero_depth = 1
        if self._ednote_depth:
            self._ednote_depth += 1
            if tag == "a":
                self._ednote_anchor = True
                self.ednote_source_url = self._href(attrs)
            if tag == "span" and "dt" in classes and self._ednote_anchor:
                self._ednote_in_dt = True
        elif "ednote" in classes:
            self._ednote_depth = 1
        if self._card is not None:
            self._card_depth += 1
            if "chip" in classes:
                self._in_chip = True
            if tag == "h3":
                self._in_h3 = True
            if tag == "p" and "sum" in classes:
                self._in_sum = self._card_depth
            if tag == "a" and self._card.get("_in_src"):
                self._src_anchor = True
                self._card["url"] = self._href(attrs)
            if "src" in classes:
                self._card["_in_src"] = True
            if tag == "span" and "dt" in classes and self._src_anchor:
                self._src_in_dt = True
        elif tag == "article" and "card" in classes:
            self._card = {
                "category": "",
                "title": "",
                "summary": "",
                "source": "",
                "url": "",
                "date": "",
                "_in_src": False,
            }
            self._card_depth = 1

    def handle_endtag(self, tag):
        if tag in _VOID_TAGS:
            return
        if tag == "title":
            self._in_title = False
        if self._hero_depth:
            if tag == "h2":
                self._in_hero_h2 = False
            if self._in_hero_foot and self._hero_depth <= self._in_hero_foot:
                self._in_hero_foot = 0
            self._hero_depth -= 1
        if self._ednote_depth:
            if tag == "a":
                self._ednote_anchor = False
            if tag == "span":
                self._ednote_in_dt = False
            self._ednote_depth -= 1
        if self._card is not None:
            if tag == "h3":
                self._in_h3 = False
            if tag == "a":
                self._src_anchor = False
            if tag == "span":
                self._src_in_dt = False
            if self._in_sum and self._card_depth <= self._in_sum:
                self._in_sum = 0
            self._card_depth -= 1
            if self._card_depth <= 0:
                card = self._card
                card.pop("_in_src", None)
                if card["title"]:
                    self.cards.append(card)
                self._card = None

    def handle_data(self, data):
        text = data
        if self._in_title:
            self.title_text += text
        if self._in_hero_h2:
            self.hero_title += text
        if self._in_hero_foot:
            self.hero_category += text
        if self._ednote_depth:
            if self._ednote_in_dt:
                self.ednote_date += text
            elif self._ednote_anchor:
                self.ednote_source += text
            else:
                self.ednote_text.append(text)
        if self._card is not None:
            if self._in_chip:
                self._card["category"] += text
                self._in_chip = False
            elif self._in_h3:
                self._card["title"] += text
            elif self._in_sum:
                if self._src_in_dt:
                    pass
                else:
                    self._card["summary"] += text
            elif self._src_in_dt:
                self._card["date"] += text
            elif self._src_anchor:
                self._card["source"] += text


def _clean(value: str) -> str:
    return " ".join(unescape(value or "").split())


def _iso_date(mm_dd: str, year: int) -> str:
    match = re.fullmatch(r"(\d{2})\.(\d{2})", _clean(mm_dd))
    if not match:
        return ""
    return f"{year:04d}-{match.group(1)}-{match.group(2)}"


def _public_url(url: str) -> str:
    url = _clean(url)
    if not url.startswith(("http://", "https://")):
        return ""
    lowered = url.lower()
    if any(marker in lowered for marker in PRIVACY_FORBIDDEN_MARKERS):
        return ""
    return url


def extract_file(path: Path) -> dict:
    """Extract one private Brief file; raises ValueError when fail-closed."""
    raw = path.read_text(encoding="utf-8", errors="replace")
    parser = _BriefParser()
    parser.feed(raw)

    issue_source = _clean(parser.title_text)
    issue = _ISSUE_RE.search(issue_source) or _ISSUE_RE.search(raw[:4000])
    if not parser.cards or not _clean(parser.hero_title):
        raise ValueError(
            "brief body not identifiable (no hero/cards found — likely an "
            "Autoway wrapper page whose mail body was not saved)"
        )
    if not issue:
        raise ValueError("issue label with date not identifiable")
    year = int(issue.group(4))
    issue_date = f"{issue.group(4)}-{issue.group(5)}-{issue.group(6)}"
    issue_label = f"{issue.group(1)}년 {issue.group(2)}월 {issue.group(3)}주차"

    is_candidate_pool = "후보" in path.name
    articles: list[dict] = []
    hero_record = {
        "order": 0,
        "headline": True,
        "evidence_level": (
            EVIDENCE_SILVER_CANDIDATE if is_candidate_pool else EVIDENCE_GOLD_PLUS
        ),
        "category": _clean(parser.hero_category),
        "title": _clean(parser.hero_title),
        "human_summary": _clean(" ".join(parser.ednote_text)).removeprefix(
            "Editor's Summary"
        ).strip(),
        "source": _clean(parser.ednote_source),
        "canonical_url": _public_url(parser.ednote_source_url),
        "published_date": _iso_date(parser.ednote_date, year),
    }
    articles.append(hero_record)
    for index, card in enumerate(parser.cards, start=1):
        title = _clean(card["title"])
        if not title or title.lower() == "editor's summary":
            continue
        articles.append(
            {
                "order": index,
                "headline": False,
                "evidence_level": (
                    EVIDENCE_SILVER_CANDIDATE
                    if is_candidate_pool
                    else EVIDENCE_GOLD_SELECTED
                ),
                "category": _clean(card["category"]),
                "title": title,
                "human_summary": _clean(card["summary"]),
                "source": _clean(card["source"]),
                "canonical_url": _public_url(card["url"]),
                "published_date": _iso_date(card["date"], year),
            }
        )

    return {
        "record_version": 1,
        "parser_version": PARSER_VERSION,
        "product": "weekly_tni",
        "kind": "candidate_pool" if is_candidate_pool else "final_brief",
        "edition_key": f"tni-weekly-{issue_date}",
        "issue_label": issue_label,
        "issue_date": issue_date,
        "headline_title": _clean(parser.hero_title),
        "human_editor_summary": hero_record["human_summary"],
        "article_count": len(articles),
        "articles": articles,
    }


def privacy_scan(payload: dict) -> list[str]:
    text = json.dumps(payload, ensure_ascii=False).lower()
    hits = [marker for marker in PRIVACY_FORBIDDEN_MARKERS if marker in text]
    # 7-digit standalone numbers could be employee IDs; canonical URLs keep
    # their own long numeric article ids, so scan only outside URLs.
    stripped = re.sub(r"https?://[^\s\"]+", "", text)
    if re.search(r"(?<![0-9a-z/])[0-9]{7}(?![0-9])", stripped):
        hits.append("employee-id-pattern")
    return hits


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", required=True)
    parser.add_argument(
        "--output-root", default=str(ROOT / "data" / "editorial_learning")
    )
    args = parser.parse_args()
    source_dir = Path(args.source_dir)
    output_root = Path(args.output_root)
    finals_dir = output_root / "final_briefs"
    pools_dir = output_root / "candidate_pools"
    finals_dir.mkdir(parents=True, exist_ok=True)
    pools_dir.mkdir(parents=True, exist_ok=True)

    manifest_entries = []
    failures = []
    extracted_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    for path in sorted(source_dir.glob("*.html")):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        try:
            payload = extract_file(path)
        except ValueError as exc:
            failures.append({"source_filename": path.name, "reason": str(exc)})
            print(f"FAIL_CLOSED {path.name}: {exc}")
            continue
        hits = privacy_scan(payload)
        if hits:
            failures.append(
                {"source_filename": path.name, "reason": f"privacy markers: {hits}"}
            )
            print(f"FAIL_CLOSED {path.name}: privacy markers {hits}")
            continue
        payload["provenance"] = {
            "source_filename": path.name,
            "source_sha256": digest,
            "parser_version": PARSER_VERSION,
            "extracted_article_count": payload["article_count"],
            "extracted_at": extracted_at,
        }
        target_dir = (
            pools_dir if payload["kind"] == "candidate_pool" else finals_dir
        )
        target = target_dir / f"{payload['edition_key']}.json"
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        manifest_entries.append(
            {
                "source_filename": path.name,
                "source_sha256": digest,
                "parser_version": PARSER_VERSION,
                "kind": payload["kind"],
                "edition_key": payload["edition_key"],
                "issue_date": payload["issue_date"],
                "extracted_article_count": payload["article_count"],
                "extracted_at": extracted_at,
                "output": str(target.relative_to(ROOT)),
            }
        )
        print(
            f"EXTRACTED {path.name} -> {target.name} "
            f"articles={payload['article_count']} kind={payload['kind']}"
        )

    manifest = {
        "version": 1,
        "parser_version": PARSER_VERSION,
        "extracted_at": extracted_at,
        "extracted": manifest_entries,
        "fail_closed": failures,
    }
    manifest_path = output_root / "final_briefs" / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"MANIFEST {manifest_path.relative_to(ROOT)} "
        f"extracted={len(manifest_entries)} fail_closed={len(failures)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
