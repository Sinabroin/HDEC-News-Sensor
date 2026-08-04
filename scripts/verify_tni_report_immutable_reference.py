#!/usr/bin/env python3
"""Fail-closed guard for the immutable AI T&I Report design authority.

There is intentionally no Report renderer in the current runtime.  This guard
verifies the operator-supplied reference and records the exact static shell/CSS
signatures future renderer work must target.  It does not create a renderer,
publication workflow, output document, or dynamic-island contract.
"""

from __future__ import annotations

import hashlib
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = Path(
    "/mnt/c/Users/HDEC/Downloads/260415_AI 경영 T&I_Report_Mythos 이슈 브리핑.html"
)
REFERENCE_SHA256 = "6af54a441991bb8af8390e39e2de2c1e576c831bd5537b51162c74e841decd84"
STYLE_SHA256 = "b9ae8ff4e308a4653b2a9d2fc03b57f54383c7cce50826371e6e7881f2445bfb"
TAG_SEQUENCE_SHA256 = "ac6ee5c5e6bb6edb3b3cebbaf3a4c36afd80b31262e575c45db81af3acaf8b04"
TAG_COUNT = 276

CHECKS = 0
FAILURES: list[str] = []


def check(label: str, condition: bool, detail: object = "") -> None:
    global CHECKS
    CHECKS += 1
    if condition:
        print(f"PASS {label}")
    else:
        FAILURES.append(label)
        print(f"FAIL {label}" + (f" :: {detail}" if detail else ""))


class SignatureParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[str] = []
        self.classes: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        values = dict(attrs)
        self.tags.append(tag)
        self.classes.extend(str(values.get("class") or "").split())


def _ordered(text: str, values: tuple[str, ...]) -> bool:
    cursor = 0
    for value in values:
        position = text.find(value, cursor)
        if position < 0:
            return False
        cursor = position + len(value)
    return True


def _repository_has_tni_report_renderer() -> bool:
    candidates = [ROOT / "app", ROOT / "templates"]
    for directory in candidates:
        for path in directory.rglob("*"):
            if not path.is_file() or path.suffix not in {".py", ".html", ".css", ".js"}:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if "KEY MESSAGE" in text and "width:794px" in text:
                return True
    return False


def main() -> int:
    # Explicit fail-closed requirement: missing or changed authority is a hard
    # failure rather than a skipped parity check.
    check("authoritative Report reference exists", REFERENCE.is_file(), REFERENCE)
    if not REFERENCE.is_file():
        print("REPORT_RENDERER=NOT_IN_CURRENT_RUNTIME")
        print(f"checks={CHECKS} failures={len(FAILURES)}")
        return 1

    raw = REFERENCE.read_bytes()
    actual_sha = hashlib.sha256(raw).hexdigest()
    check("authoritative Report SHA256 is exact", actual_sha == REFERENCE_SHA256, actual_sha)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        check("Report reference is UTF-8", False)
        return 1
    styles = "\n".join(
        re.findall(r"<style[^>]*>(.*?)</style>", text, flags=re.I | re.S)
    )
    parser = SignatureParser()
    parser.feed(text)
    check(
        "static CSS signature is exact",
        hashlib.sha256(styles.encode("utf-8")).hexdigest() == STYLE_SHA256,
    )
    check(
        "static shell tag signature is exact",
        hashlib.sha256("|".join(parser.tags).encode("utf-8")).hexdigest()
        == TAG_SEQUENCE_SHA256,
    )
    check("static shell tag count is exact", len(parser.tags) == TAG_COUNT, len(parser.tags))
    check("A4 page width is 794px", "width:794px" in styles)
    check("A4 minimum page height is 1123px", "min-height:1123px" in styles)
    check("three-column management cards remain", "grid-template-columns:1fr 1fr 1fr" in styles)
    check("mobile breakpoint remains 768px", "@media (max-width:768px)" in styles)
    check("print color preservation remains", "print-color-adjust:exact" in styles)
    check("print page contract remains A4 portrait", "size:A4 portrait" in styles)
    check("page-break avoidance remains", "page-break-inside:avoid" in styles)
    check(
        "PDF print button behavior remains exact",
        '<button class="btn-pdf" onclick="window.print()">' in text,
    )
    check("management card count remains three", text.count('class="top-card"') == 3)
    check(
        "Report section hierarchy remains ordered",
        _ordered(
            text,
            (
                'class="hd-header"',
                'class="hd-rule"',
                'class="hd-rule2"',
                'class="doc-title-block"',
                'class="hero"',
                'class="top-grid"',
                '<div class="sec-title"><span class="bar"></span>핵심 사실</div>',
                'class="timeline"',
                '<table class="table">',
                "업계 동향 &amp; 인사이트",
                'class="judgement"',
                'class="alt-view"',
                'class="footer"',
                'class="source"',
            ),
        ),
    )
    forbidden_visible = (
        "공공기관·정책",
        "정책·공공",
        "정부자료",
        "Official Sources",
    )
    check(
        "no public-institution final tab or section exists",
        not any(value in text for value in forbidden_visible),
    )
    check(
        "repository has no fabricated T&I Report renderer",
        not _repository_has_tni_report_renderer(),
    )
    print("REPORT_RENDERER=NOT_IN_CURRENT_RUNTIME")
    print("TNI_REPORT_FORMAT_STATUS=NOT_IN_CURRENT_RUNTIME")
    print(
        f"checks={CHECKS} failures={len(FAILURES)} source_sha256={actual_sha} "
        "network_calls=0 sends=0 production_state_writes=0"
    )
    if FAILURES:
        return 1
    print("RESULT=D7-AK-6E_R4_R8_TNI_REPORT_REFERENCE_GUARD_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
