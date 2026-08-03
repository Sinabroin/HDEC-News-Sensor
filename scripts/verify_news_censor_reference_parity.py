#!/usr/bin/env python3
"""Verify the immutable NEW_CENSOR shell against generated production HTML.

The raw design reference remains outside the repository.  Local acceptance
checks its sealed SHA-256 and derives the normalized shell from that file.  CI
can use the sealed shell/style signatures after the external reference has
been checked locally; no sample article, weather, market, or safety data is
stored here.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
for item in (ROOT, SCRIPTS):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

import build_news_censor  # noqa: E402
from app import publisher_direct  # noqa: E402
from build_executive_brief import load_brief_json  # noqa: E402

REFERENCE = Path("/tmp/d7ak6e-r4r5-reference/NEW_CENSOR (1)(1).html")
REFERENCE_SHA256 = "c4a1d129a9e8b6d824b961e2042f345cfc2eb405dcbc488a542e5bc6cee14804"
REFERENCE_SHELL_SHA256 = "a5928a0d63c8844f203cbcfea04944aba84a1ed69d1ddc5aa0b3a1218ee4a403"
REFERENCE_STYLE_SHA256 = "ca7acab1940fd98c61f69b920909fecb88dda248ded72865ed1f27c4a99a2b31"
REQUIRED_IDS = (
    "pane-articles",
    "pane-reader",
    "reader-content",
    "pane-market",
    "article-data",
)
CATEGORIES = ("홈", "사업영역", "동종사", "현대그룹", "안전품질", "해외지정학", "AI")
CORE_CLASSES = {
    "wrap", "mast", "brand", "mark", "brand-text", "when", "catnav", "block",
    "cattop", "cat", "subbar", "sub", "cols", "pane", "active", "panewrap",
    "feed-title", "newslist", "lead", "lead-thumb", "thumb", "lead-body", "grid",
    "pane-head", "mgroups", "mgroup", "mrow", "rail",
    "memo", "memo-wx", "krmap", "legend", "memo-mk", "memo-hz",
}
REFERENCE_CLASS_VOCABULARY = frozenset({
    "active", "art", "block", "brand", "brand-text", "cat", "catnav", "cattop",
    "cols", "delta", "down", "feed-title", "fx-more", "grid", "krmap", "lead",
    "lead-body", "lead-sum", "lead-thumb", "legend", "mark", "mast", "memo",
    "memo-hz", "memo-mk", "memo-wx", "mgroup", "mgroup-h", "mgroups", "mkclose",
    "ml", "mlabel", "mnote", "morebtn", "mrow", "mv", "mval", "nbody",
    "newslist", "nitem", "nthumb", "num", "pane", "pane-head", "panewrap", "ph",
    "rail", "sm", "spark", "src", "sub", "sub2", "subbar", "thumb", "up",
    "verdict", "warn", "when", "why", "wrap", "wximpact", "wxmap-wrap", "wxnotes",
})
FORBIDDEN_VISIBLE = (
    "Coverage Health",
    "collection-status",
    "verified-supply",
    "cache counters",
    "publisher-count banner",
    "primary/backfill dashboard banner",
)
PORTAL_HOST_TOKENS = (
    "news.google.", "google.com", "news.naver.com", "search.naver.com",
    "news.daum.net", "search.daum.net", "m.daum.net",
)
VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}
DYNAMIC_CLASSES = {"subbar", "newslist", "mgroups", "krmap", "wximpact", "wxnotes", "mnote"}


def digest(value: str | bytes) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def extract_style(html: str) -> str:
    match = re.search(r"<style>(.*?)</style>", html, flags=re.DOTALL)
    if not match:
        raise AssertionError("style block missing")
    return match.group(1)


class ShellNormalizer(HTMLParser):
    """Keep the approved shell and replace only authorized data islands."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.output: list[str] = []
        self.stack: list[tuple[str, set[str]]] = []
        self.skip_depth = 0

    @staticmethod
    def _attributes(attributes: list[tuple[str, str | None]]) -> str:
        return "".join(
            f' {key}="{value}"' if value is not None else f" {key}"
            for key, value in attributes
        )

    def _class_context(self) -> set[str]:
        return {name for _, classes in self.stack for name in classes}

    @staticmethod
    def _is_external_font_link(tag: str, attributes: dict[str, str | None]) -> bool:
        return tag == "link" and "cdn.jsdelivr.net" in str(attributes.get("href") or "")

    def handle_decl(self, declaration: str) -> None:
        self.output.append(f"<!{declaration.casefold()}>")

    def handle_starttag(self, tag: str, attributes: list[tuple[str, str | None]]) -> None:
        values = dict(attributes)
        classes = set(str(values.get("class") or "").split())
        if self._is_external_font_link(tag, values):
            return
        if self.skip_depth:
            if tag not in VOID_TAGS:
                self.skip_depth += 1
            return

        context = self._class_context()
        dynamic = (
            bool(classes & DYNAMIC_CLASSES)
            or (tag == "ul" and "memo-mk" in context)
            or ("memo-hz" in context and tag != "h4")
        )
        self.output.append(f"<{tag}{self._attributes(attributes)}>")
        if tag not in VOID_TAGS:
            self.stack.append((tag, classes))

        if dynamic:
            labels = sorted(classes & DYNAMIC_CLASSES)
            label = labels[0] if labels else "safety"
            self.output.append(f"{{DYNAMIC:{label}}}")
            self.skip_depth = 1
        elif tag == "style":
            self.output.append("{STATIC_CODE}")
            self.skip_depth = 1
        elif tag == "script":
            self.output.append("{INTERACTION_CODE}")
            self.skip_depth = 1
        elif (
            tag == "title"
            or "when" in classes
            or (tag == "small" and ("pane-head" in context or "memo-wx" in context))
        ):
            self.output.append("{DYNAMIC:TEXT}")
            self.skip_depth = 1

    def handle_startendtag(self, tag: str, attributes: list[tuple[str, str | None]]) -> None:
        if self.skip_depth:
            return
        values = dict(attributes)
        if self._is_external_font_link(tag, values):
            return
        self.output.append(f"<{tag}{self._attributes(attributes)}/>")

    def handle_endtag(self, tag: str) -> None:
        if self.skip_depth:
            self.skip_depth -= 1
            if self.skip_depth:
                return
        self.output.append(f"</{tag}>")
        if self.stack:
            self.stack.pop()

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        normalized = re.sub(r"\s+", " ", data).strip()
        if normalized:
            self.output.append(normalized)

    def handle_comment(self, data: str) -> None:
        del data


def normalized_shell(html: str) -> str:
    parser = ShellNormalizer()
    parser.feed(html)
    parser.close()
    if parser.stack or parser.skip_depth:
        raise AssertionError("unbalanced HTML while normalizing dashboard shell")
    return "".join(parser.output)


@dataclass
class Node:
    tag: str
    attrs: dict[str, str]
    parent: "Node | None" = None
    children: list["Node"] = field(default_factory=list)
    text: list[str] = field(default_factory=list)

    @property
    def classes(self) -> set[str]:
        return set(self.attrs.get("class", "").split())

    @property
    def content(self) -> str:
        return re.sub(r"\s+", " ", " ".join(self.text)).strip()


class TreeParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("document", {})
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attributes: list[tuple[str, str | None]]) -> None:
        node = Node(tag, {key: str(value or "") for key, value in attributes}, self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attributes: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attributes)
        if tag not in VOID_TAGS:
            self.stack.pop()

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if self.stack:
            self.stack[-1].text.append(data)


def parse_tree(html: str) -> Node:
    parser = TreeParser()
    parser.feed(html)
    parser.close()
    return parser.root


def walk(node: Node) -> Iterable[Node]:
    yield node
    for child in node.children:
        yield from walk(child)


def one(root: Node, *, tag: str | None = None, node_id: str | None = None, cls: str | None = None) -> Node:
    matches = [
        node for node in walk(root)
        if (tag is None or node.tag == tag)
        and (node_id is None or node.attrs.get("id") == node_id)
        and (cls is None or cls in node.classes)
    ]
    if len(matches) != 1:
        raise AssertionError(f"expected one node tag={tag} id={node_id} class={cls}; got {len(matches)}")
    return matches[0]


def direct(parent: Node, *, tag: str | None = None, cls: str | None = None) -> list[Node]:
    return [
        node for node in parent.children
        if (tag is None or node.tag == tag) and (cls is None or cls in node.classes)
    ]


def visible_class_set(root: Node) -> set[str]:
    return {name for node in walk(root) for name in node.classes}


def role(node: Node, allowed: set[str]) -> str:
    matches = sorted(node.classes & allowed)
    return matches[0] if matches else ""


def assert_dom_contract(html: str, *, reference_classes: set[str]) -> None:
    root = parse_tree(html)
    ids = [node.attrs["id"] for node in walk(root) if node.attrs.get("id")]
    if ids != list(REQUIRED_IDS):
        raise AssertionError(f"dashboard IDs/order changed: {ids}")

    wrap = one(root, tag="div", cls="wrap")
    top = [(node.tag, role(node, {"mast", "catnav", "cols"})) for node in wrap.children]
    if top[:3] != [("header", "mast"), ("nav", "catnav"), ("div", "cols")]:
        raise AssertionError(f"top-level dashboard order changed: {top[:3]}")
    if not wrap.children or wrap.children[-1].tag != "footer":
        raise AssertionError("footer is not the final element inside .wrap")

    mast = one(root, tag="header", cls="mast")
    if [role(node, {"brand", "when"}) for node in mast.children] != ["brand", "when"]:
        raise AssertionError("mast brand/when nesting changed")
    brand = one(mast, tag="div", cls="brand")
    if [node.classes for node in brand.children] != [{"mark"}, {"brand-text"}]:
        raise AssertionError("mast mark/brand-text order changed")

    nav = one(root, tag="nav", cls="catnav")
    cattop = direct(nav, tag="div", cls="cattop")
    subbars = direct(nav, tag="div", cls="subbar")
    if len(cattop) != 1 or len(subbars) != 7:
        raise AssertionError("category/subfilter hierarchy changed")
    cats = direct(cattop[0], tag="button", cls="cat")
    if [node.content for node in cats] != list(CATEGORIES):
        raise AssertionError("seven category order changed")
    if [node.attrs.get("data-for") for node in subbars] != list(build_news_censor.CATEGORY_LABELS):
        raise AssertionError("subbar category order changed")
    if any(not direct(bar, tag="button", cls="sub") for bar in subbars):
        raise AssertionError("a category subbar has no subfilter button")

    cols = one(root, tag="div", cls="cols")
    if len(cols.children) != 2 or cols.children[0].tag != "main" or "rail" not in cols.children[1].classes:
        raise AssertionError("right rail must follow main inside .cols")
    main = cols.children[0]
    pane_ids = [node.attrs.get("id") for node in direct(main, tag="section")]
    if pane_ids != ["pane-articles", "pane-reader", "pane-market"]:
        raise AssertionError(f"pane order changed: {pane_ids}")

    articles = one(root, node_id="pane-articles")
    article_wrap = direct(articles, tag="div", cls="panewrap")
    if len(article_wrap) != 1:
        raise AssertionError("article pane wrapper changed")
    newslist = one(article_wrap[0], tag="div", cls="newslist")
    if [role(node, {"lead", "grid"}) for node in newslist.children] != ["lead", "grid"]:
        raise AssertionError("lead/grid order changed")
    lead = direct(newslist, tag="article", cls="lead")
    grid = direct(newslist, tag="div", cls="grid")
    if len(lead) != 1 or len(grid) != 1:
        raise AssertionError("dashboard must contain one lead followed by one grid")
    if [role(node, {"lead-thumb", "lead-body"}) for node in lead[0].children] != ["lead-thumb", "lead-body"]:
        raise AssertionError("lead article nesting changed")
    for card in direct(grid[0], tag="article", cls="nitem"):
        if [role(node, {"nthumb", "nbody"}) for node in card.children] != ["nthumb", "nbody"]:
            raise AssertionError("grid article nesting changed")

    rail = cols.children[1]
    if [node.classes & {"memo-wx", "memo-mk", "memo-hz"} for node in rail.children] != [
        {"memo-wx"}, {"memo-mk"}, {"memo-hz"}
    ]:
        raise AssertionError("weather/market/safety rail order changed")

    candidate_classes = visible_class_set(root)
    if not CORE_CLASSES <= candidate_classes:
        raise AssertionError(f"required DOM classes missing: {sorted(CORE_CLASSES - candidate_classes)}")
    if not candidate_classes <= reference_classes:
        raise AssertionError(f"unauthorized DOM classes introduced: {sorted(candidate_classes - reference_classes)}")


def hrefs(html: str) -> list[str]:
    return re.findall(r'''\bhref=["']([^"']+)["']''', html, flags=re.IGNORECASE)


def portal_hrefs(html: str) -> list[str]:
    found = []
    for value in hrefs(html):
        host = (urlparse(value).hostname or "").casefold()
        if any(token in host for token in PORTAL_HOST_TOKENS) or publisher_direct.portal_provider(value):
            found.append(value)
    return found


def remote_image_hotlinks(html: str) -> list[str]:
    values = re.findall(r'''\bsrc=["'](https?://[^"']+)["']''', html, flags=re.IGNORECASE)
    values += re.findall(r'''url\(["']?(https?://[^)'"\s]+)''', html, flags=re.IGNORECASE)
    return values


def assert_interactions(html: str) -> None:
    required = (
        "activateCategory(c.dataset.cat)",
        "applyFilter(firstFilter === 'all' ? cat : firstFilter)",
        "s.closest('.subbar')",
        "openReader(el.dataset.article, el)",
        "e.key === 'Enter' || e.key === ' '",
        "showPane('pane-reader')",
        "sourceUrl",
        "showPane(b.dataset.goto)",
        "readerTrigger.focus({preventScroll:true})",
        "prefers-reduced-motion: reduce",
    )
    missing = [value for value in required if value not in html]
    if missing:
        raise AssertionError(f"interaction parity tokens missing: {missing}")


def fixture_shells(brief_path: Path) -> tuple[str, str]:
    brief = load_brief_json(brief_path)
    build_news_censor.validate_brief_artifact(brief, require_live=False)
    model = build_news_censor.build_model(
        brief,
        edition=build_news_censor._edition_date("2026-08-03"),
        article_limit=build_news_censor.PUBLIC_HARD_MAX,
    )
    articles = list(model.get("articles") or [])
    if not articles:
        raise AssertionError("article-count shell test requires at least one verified fixture article")
    expanded = []
    for index in range(40):
        clone = copy.deepcopy(articles[index % len(articles)])
        clone["id"] = f"shell-fixture-{index:02d}"
        clone["url"] = f"https://publisher.example/reference-shell-{index:02d}"
        expanded.append(clone)
    twenty = copy.deepcopy(model)
    forty = copy.deepcopy(model)
    twenty["articles"] = expanded[:20]
    forty["articles"] = expanded
    return (
        normalized_shell(build_news_censor.render_html(twenty)),
        normalized_shell(build_news_censor.render_html(forty)),
    )


def verify(reference_path: Path, candidates: list[Path], *, brief_json: Path | None, require_reference: bool) -> dict:
    reference_html = ""
    if reference_path.is_file():
        reference_bytes = reference_path.read_bytes()
        actual = digest(reference_bytes)
        if actual != REFERENCE_SHA256:
            raise AssertionError(f"reference SHA-256 mismatch: {actual}")
        reference_html = reference_bytes.decode("utf-8")
        reference_shell = normalized_shell(reference_html)
        if digest(reference_shell) != REFERENCE_SHELL_SHA256:
            raise AssertionError("sealed normalized reference shell changed")
        if digest(extract_style(reference_html)) != REFERENCE_STYLE_SHA256:
            raise AssertionError("sealed reference CSS changed")
    elif require_reference:
        raise AssertionError(f"required reference file missing: {reference_path}")

    reference_classes = visible_class_set(parse_tree(reference_html)) if reference_html else set(REFERENCE_CLASS_VOCABULARY)
    if reference_classes != set(REFERENCE_CLASS_VOCABULARY):
        raise AssertionError("sealed reference DOM class vocabulary changed")
    if not candidates:
        raise AssertionError("no generated dashboard candidate supplied")

    signatures: list[str] = []
    article_counts: list[int] = []
    for path in candidates:
        html = path.read_text(encoding="utf-8")
        style = extract_style(html)
        if digest(style) != REFERENCE_STYLE_SHA256:
            raise AssertionError(f"{path}: CSS differs from exact reference")
        shell = normalized_shell(html)
        signature = digest(shell)
        if signature != REFERENCE_SHELL_SHA256:
            raise AssertionError(f"{path}: normalized shell differs: {signature}")
        assert_dom_contract(html, reference_classes=reference_classes)
        assert_interactions(html)
        forbidden = [value for value in FORBIDDEN_VISIBLE if value.casefold() in html.casefold()]
        if forbidden:
            raise AssertionError(f"{path}: unauthorized visible diagnostics: {forbidden}")
        portals = portal_hrefs(html)
        if portals:
            raise AssertionError(f"{path}: portal href count is {len(portals)}")
        hotlinks = remote_image_hotlinks(html)
        if hotlinks:
            raise AssertionError(f"{path}: remote image hotlink count is {len(hotlinks)}")
        tree = parse_tree(html)
        article_counts.append(len([node for node in walk(tree) if "lead" in node.classes or "nitem" in node.classes]))
        signatures.append(signature)

    if len(set(signatures)) != 1:
        raise AssertionError("public dashboard outputs do not share one shell signature")
    if len(candidates) > 1 and candidates[0].read_bytes() != candidates[1].read_bytes():
        raise AssertionError("public dashboard outputs do not use identical model/rendered bytes")

    fixture_invariant = None
    if brief_json:
        shell20, shell40 = fixture_shells(brief_json)
        fixture_invariant = shell20 == shell40 and digest(shell20) == REFERENCE_SHELL_SHA256
        if not fixture_invariant:
            raise AssertionError("dynamic 20/40 article counts changed the normalized shell")

    return {
        "contract": "D7_AK_6E_R4_R5_NEWS_CENSOR_EXACT_REFERENCE_PARITY_V1",
        "reference_sha256": REFERENCE_SHA256,
        "reference_file_verified": bool(reference_html),
        "reference_shell_sha256": REFERENCE_SHELL_SHA256,
        "reference_style_sha256": REFERENCE_STYLE_SHA256,
        "candidate_count": len(candidates),
        "candidate_shell_sha256": signatures[0],
        "public_outputs_byte_identical": len(candidates) == 1 or candidates[0].read_bytes() == candidates[1].read_bytes(),
        "article_counts": article_counts,
        "dynamic_20_40_shell_invariant": fixture_invariant,
        "portal_href_count": 0,
        "remote_image_hotlink_count": 0,
        "unauthorized_visible_sections": 0,
        "status": "PASS",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, default=REFERENCE)
    parser.add_argument("--require-reference", action="store_true")
    parser.add_argument("--candidate", type=Path, action="append", default=[])
    parser.add_argument("--brief-json", type=Path)
    args = parser.parse_args(argv)
    candidates = args.candidate or [
        ROOT / "docs" / "news-censor" / "latest.html",
        ROOT / "docs" / "daily" / "dashboard-latest.html",
    ]
    try:
        report = verify(
            args.reference,
            candidates,
            brief_json=args.brief_json,
            require_reference=args.require_reference,
        )
    except (AssertionError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"NEWS_CENSOR_REFERENCE_PARITY=FAIL: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    print("NEWS_CENSOR_REFERENCE_PARITY=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
