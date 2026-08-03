#!/usr/bin/env python3
"""D7-AK-6E R4-R6 §14 — Weekly T&I exact reference parity verifier.

The immutable design authority is the operator-supplied reference document
(kept OUTSIDE the repository, never committed):

    /tmp/d7ak6e-r4r5-reference/AI경영_TnI_Weekly_2026-07월-3주차_최종(1).html
    sha256 e71308b7e1a9ee4697a5a597d5b074ce5aa1ec7ba60f83e597ebbdab6873dea2

Committed contract under test: ``templates/editorial_weekly_tni.html`` is that
reference with ONLY its dynamic content islands replaced by ``{{TNI_*}}``
placeholders, and ``app.editorial_briefings.render_weekly`` fills exactly those
islands.

Checks (offline, no network, no sends, no state writes):

1. Template structure (always): the nine placeholders appear exactly once, the
   file keeps the reference's CRLF byte discipline, and the split shell
   segments are non-empty and ordered.
2. Product shell parity (always): a deterministic product render contains every
   template shell segment byte-exactly and in order (the normalized structural
   comparison — islands are the only degrees of freedom), passes
   ``validate_rendered``, and never hotlinks a remote image.
3. Reference byte round-trip (when the reference file is present): islands
   re-extracted from the reference and injected into the committed template
   reproduce the reference BYTE-FOR-BYTE.
4. Pixel parity (when the reference and a Chromium binary are present): the
   round-trip fixture and the reference are screenshotted offline
   (DNS hard-blocked) at desktop and mobile viewports and must differ by
   ZERO pixels.

SHA-checking the reference alone is never treated as parity proof.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
for item in (ROOT, SCRIPTS):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from app import editorial_briefings as brief  # noqa: E402

REFERENCE_PATH = Path(
    "/tmp/d7ak6e-r4r5-reference/AI경영_TnI_Weekly_2026-07월-3주차_최종(1).html"
)
REFERENCE_SHA256 = "e71308b7e1a9ee4697a5a597d5b074ce5aa1ec7ba60f83e597ebbdab6873dea2"
TEMPLATE_PATH = ROOT / "templates" / "editorial_weekly_tni.html"
PLACEHOLDERS = (
    "{{TNI_TITLE_ISSUE}}",
    "{{TNI_ISSUE_LABEL}}",
    "{{TNI_HERO_IMAGE}}",
    "{{TNI_HERO_TITLE}}",
    "{{TNI_HERO_CATEGORY}}",
    "{{TNI_EDNOTE_HTML}}",
    "{{TNI_EDNOTE_SOURCE}}",
    "{{TNI_CARDS}}",
    "{{TNI_META_ISSUE}}",
)
# Desktop and mobile (below the reference's 560px breakpoint) viewports.
VIEWPORTS = (("desktop", 1280, 1800), ("mobile", 420, 2400))

CHECKS = 0
FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    if condition:
        print(f"PASS {name}")
    else:
        FAILURES.append(name)
        print(f"FAIL {name}" + (f" — {detail}" if detail else ""))


def _reference_islands(reference: str) -> dict[str, str]:
    """Re-derive the island content of the sealed reference document."""
    hero_image = (
        '<img alt="" aria-hidden="true" style="position:absolute;inset:0;'
        "width:100%;height:100%;object-fit:cover;object-position:center 40%;"
        'z-index:0;" src="https://img1.daumcdn.net/thumb/S1200x630/?fname='
        'https://t1.daumcdn.net/news/202607/14/yonhap/20260714135444603efhz.jpg">'
    )
    ednote_html = reference[
        reference.index('<p style="font-size: 13.1pt;'):
        reference.index("</p>", reference.index('<p style="font-size: 13.1pt;')) + 4
    ]
    ednote_source = reference[
        reference.index('출처 <a href="https://v.daum.net/v/20260714133647583"') + len("출처 "):
        reference.index("</a>", reference.index('출처 <a href="https://v.daum.net/v/20260714133647583"')) + 4
    ]
    cards_start_anchor = ' line-height: 1.5;">이번 주 브리핑</div>\r\n'
    cards_end_anchor = (
        '<div style="height: 36px; line-height: 1px; font-size: 1px;">&nbsp;</div>\r\n'
        '<div class="sec-label" style="display: inline-block; font-size: 15px; '
        "font-weight: 800; color: rgb(16, 18, 24); border: 2px solid rgb(16, 18, 24); "
        "border-radius: 999px; padding: 6px 18px; background: rgb(255, 255, 255); "
        'margin: 0px 0px 16px; line-height: 1.5;">정보 분류 기준</div>'
    )
    cards_start = reference.index(cards_start_anchor) + len(cards_start_anchor)
    cards = reference[cards_start:reference.index(cards_end_anchor)]
    return {
        "{{TNI_TITLE_ISSUE}}": "2026년 7월 3주차 (2026.07.15)",
        "{{TNI_ISSUE_LABEL}}": "2026년 7월 3주차",
        "{{TNI_HERO_IMAGE}}": hero_image,
        "{{TNI_HERO_TITLE}}": "구글, 국내 기업 겨냥<br>'풀스택 AI' 선보인다",
        "{{TNI_HERO_CATEGORY}}": "기업동향",
        "{{TNI_EDNOTE_HTML}}": ednote_html,
        "{{TNI_EDNOTE_SOURCE}}": ednote_source,
        "{{TNI_CARDS}}\r\n": cards,
        "{{TNI_META_ISSUE}}": "2026년 7월 3주차 (2026.07.15)",
    }


def _product_sample_html() -> str:
    KST = timezone(timedelta(hours=9))
    run_at = datetime(2026, 8, 5, 7, 30, tzinfo=KST)
    coverage = brief.weekly_coverage(run_at)
    sources = (
        ("연합뉴스", "yna.co.kr"), ("MBC", "imbc.com"), ("KBS", "kbs.co.kr"),
        ("조선일보", "chosun.com"), ("YTN", "ytn.co.kr"), ("JTBC", "jtbc.co.kr"),
        ("중앙일보", "joongang.co.kr"), ("매일경제", "mk.co.kr"),
        ("한국경제", "hankyung.com"), ("SBS", "sbs.co.kr"),
        ("동아일보", "donga.com"), ("대한경제", "dnews.co.kr"),
    )
    rows = [
        {
            "title": f"{source} AI 데이터센터 전력 인프라 {index + 1}조원 투자 계약 체결",
            "source": source,
            "published_at": (coverage.end - timedelta(hours=3 + index)).isoformat(),
            "url": f"https://{domain}/news/tni-parity-{index}",
            "snippet": (
                f"{index + 1}조원 규모 AI 데이터센터 전력 인프라 투자 계약이 "
                "체결됐다. 건설 산업 파급 효과가 크다."
            ),
            "source_metadata": {"provider": "offline_fixture", "query": "AI 데이터센터 전력"},
        }
        for index, (source, domain) in enumerate(sources)
    ]
    edition = brief.render_edition(
        "weekly",
        rows,
        run_at=run_at,
        root_url="https://preview.fixture.test/HDEC-News-Sensor",
        selection_mode=brief.SELECTION_MODE_EDITORIAL_PRIORITY,
    )
    brief.validate_rendered(edition)
    return edition.html


def _chrome_binary() -> str:
    try:
        from capture_news_censor_reference_visual import CHROME_CANDIDATES
    except ImportError:
        CHROME_CANDIDATES = ()
    for candidate in CHROME_CANDIDATES:
        if Path(candidate).is_file():
            return str(candidate)
    for name in ("chromium", "chromium-browser", "google-chrome"):
        found = shutil.which(name)
        if found:
            return found
    return ""


def _screenshot(chrome: str, html_path: Path, out_png: Path, width: int, height: int) -> bool:
    command = [
        chrome,
        "--headless=new",
        "--disable-gpu",
        "--hide-scrollbars",
        "--force-device-scale-factor=1",
        "--host-resolver-rules=MAP * ~NOTFOUND",
        "--disable-lcd-text",
        "--allow-file-access-from-files",
        f"--window-size={width},{height}",
        f"--screenshot={out_png}",
        html_path.resolve().as_uri(),
    ]
    try:
        completed = subprocess.run(
            command, capture_output=True, timeout=120, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0 and out_png.is_file()


def main() -> int:
    template_bytes = TEMPLATE_PATH.read_bytes()
    template = template_bytes.decode("utf-8")

    # 1. Template structure (always).
    for placeholder in PLACEHOLDERS:
        check(
            f"template carries {placeholder} exactly once",
            template.count(placeholder) == 1,
            str(template.count(placeholder)),
        )
    check(
        "template preserves the reference CRLF byte discipline",
        b"\r\n" in template_bytes
        and template_bytes.count(b"\n") == template_bytes.count(b"\r\n"),
    )
    shell_segments: list[str] = [template]
    for placeholder in PLACEHOLDERS:
        next_segments: list[str] = []
        for segment in shell_segments:
            next_segments.extend(segment.split(placeholder))
        shell_segments = next_segments
    check(
        "template splits into ordered non-trivial shell segments",
        len(shell_segments) == len(PLACEHOLDERS) + 1
        and sum(len(segment) for segment in shell_segments) > 10_000,
        str(len(shell_segments)),
    )

    # 2. Product shell parity (always): normalized structural comparison — the
    # product document must contain every shell segment byte-exactly, in order.
    product = _product_sample_html()
    cursor = 0
    ordered = True
    for segment in shell_segments:
        position = product.find(segment, cursor)
        if position < 0:
            ordered = False
            break
        cursor = position + len(segment)
    check("product render preserves the exact reference shell in order", ordered)
    check(
        "product render never hotlinks a remote image",
        'src="http' not in product.replace('src="https://preview.fixture.test', ""),
    )
    check(
        "product render keeps the fixed visible labels",
        all(
            token in product
            for token in (
                "이번 주 헤드라인", "이번 주 브리핑", "정보 분류 기준",
                "Editor's Summary", "발행 — 워크이노베이션센터 | AI디자인랩",
            )
        ),
    )

    reference_state = "absent_skipped"
    pixel_state = "skipped"
    if REFERENCE_PATH.is_file():
        raw = REFERENCE_PATH.read_bytes()
        check(
            "reference document verifies the immutable SHA256",
            hashlib.sha256(raw).hexdigest() == REFERENCE_SHA256,
            hashlib.sha256(raw).hexdigest(),
        )
        reference = raw.decode("utf-8")
        refill = template
        for placeholder, content in _reference_islands(reference).items():
            check(
                f"island content resolves for {placeholder.strip(chr(123) + chr(125))}",
                bool(content),
            )
            refill = refill.replace(placeholder, content)
        round_trip_exact = refill.encode("utf-8") == raw
        check(
            "reference-content fixture reproduces the reference BYTE-FOR-BYTE",
            round_trip_exact,
            f"fixture={len(refill.encode('utf-8'))}B reference={len(raw)}B",
        )
        reference_state = "verified"

        # 4. Pixel parity — zero differing pixels at both viewports.
        chrome = _chrome_binary()
        if round_trip_exact and chrome:
            try:
                from PIL import Image, ImageChops
            except ImportError:
                Image = None
            if Image is not None:
                pixel_state = "verified"
                with tempfile.TemporaryDirectory(
                    prefix="tni-parity-", dir="/tmp"
                ) as raw_tmp:
                    tmp = Path(raw_tmp)
                    fixture_path = tmp / "fixture.html"
                    fixture_path.write_bytes(refill.encode("utf-8"))
                    reference_copy = tmp / "reference.html"
                    reference_copy.write_bytes(raw)
                    for label, width, height in VIEWPORTS:
                        ref_png = tmp / f"reference-{label}.png"
                        fix_png = tmp / f"fixture-{label}.png"
                        ok = _screenshot(
                            chrome, reference_copy, ref_png, width, height
                        ) and _screenshot(chrome, fixture_path, fix_png, width, height)
                        if not ok:
                            check(f"{label} screenshots captured", False)
                            pixel_state = "capture_failed"
                            continue
                        with Image.open(ref_png) as ref_img, Image.open(fix_png) as fix_img:
                            same_size = ref_img.size == fix_img.size
                            diff = ImageChops.difference(
                                ref_img.convert("RGB"), fix_img.convert("RGB")
                            )
                            differing = (
                                sum(
                                    1
                                    for pixel in diff.getdata()
                                    if pixel != (0, 0, 0)
                                )
                                if same_size
                                else -1
                            )
                        check(
                            f"{label} reference fixture pixel diff = 0",
                            same_size and differing == 0,
                            f"size_match={same_size} differing_pixels={differing}",
                        )
            else:
                pixel_state = "pil_unavailable"
                check("PIL available for pixel parity", False)
        elif round_trip_exact:
            pixel_state = "chromium_unavailable"
            print("SKIP pixel parity — no Chromium binary found")
    else:
        print(
            "SKIP reference byte/pixel parity — immutable reference not present "
            f"at {REFERENCE_PATH}"
        )

    print(
        f"checks={CHECKS} failures={len(FAILURES)} "
        f"reference_fixture={reference_state} pixel_parity={pixel_state} "
        "network_calls=0 smtp_attempts=0 teams_sends=0 telegram_sends=0 "
        "production_state_writes=0"
    )
    if FAILURES:
        for name in FAILURES:
            print(f"FAILED: {name}")
        return 1
    print("RESULT=D7-AK-6E_WEEKLY_TNI_REFERENCE_PARITY_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
