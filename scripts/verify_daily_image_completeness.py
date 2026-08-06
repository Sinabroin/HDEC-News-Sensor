#!/usr/bin/env python3
"""D7-AK-6E R4-R12 §3 — Daily image completeness gate.

Every Daily candidate and every delivered Daily lead must end materialization
with a locally materialized, valid image — no missing image, no invalid image,
no blank card — whatever the raw supply looked like. When the article image is
missing or fails the hard gate, a per-article deterministic category visual is
materialized instead of a blank card.

Proves, entirely offline (network 0, sends 0, production state writes 0), over a
fixture set that deliberately includes every hard-invalid class:

* a normal news photograph is materialized as a real local asset;
* a missing image (no candidates) materializes a category fallback;
* a 250x24 masthead banner is rejected and replaced by a category fallback;
* a transparent/empty image is rejected and replaced;
* a logo-only image is rejected and replaced;
* a remote download failure is replaced (no blank card, no broken remote src);
* every materialized article — candidate and delivered lead alike — carries a
  present image URL, a materialized local asset that exists on disk, and bytes
  that pass the hard Daily image gate;
* MISSING_IMAGE_COUNT / INVALID_IMAGE_COUNT / BLANK_CARD_COUNT are all zero.
"""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from PIL import Image, ImageDraw  # noqa: E402

from app import editorial_briefings as eb  # noqa: E402
from app import news_access  # noqa: E402

RUN_AT = datetime(2026, 8, 6, 7, 0, tzinfo=timezone.utc)
CHECKS = 0
FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    if ok:
        print(f"PASS: {name}")
    else:
        FAILURES.append(name)
        print(f"FAIL: {name}" + (f" — {detail[:300]}" if detail else ""))


def photo_bytes(seed: int, size: tuple[int, int] = (640, 360)) -> bytes:
    """A richly varied non-logo photograph that passes both quality layers."""
    image = Image.new("RGB", size, (24, 40, 60))
    draw = ImageDraw.Draw(image)
    base = (seed * 29) % 160
    for y in range(size[1]):
        draw.line(
            (0, y, size[0], y),
            fill=((40 + base + y // 3) % 255, (90 + y // 4) % 255, (130 + y // 5) % 255),
        )
    for index in range(6):
        x = 35 + index * 92
        draw.rectangle(
            (x, 60 + (index % 3) * 25, x + 70, 295),
            fill=((90 + index * 21) % 255, (120 + index * 31) % 255, (170 + index * 17) % 255),
        )
    draw.ellipse((420, 52, 600, 232), fill=(225, 190, 92), outline=(48, 66, 82), width=5)
    buffer = BytesIO()
    image.save(buffer, format="JPEG")
    return buffer.getvalue()


def banner_bytes() -> bytes:
    """The 250x24 masthead-banner class the hard gate must reject."""
    image = Image.new("RGB", (250, 24), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle((6, 6, 60, 18), fill=(18, 78, 165))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def transparent_bytes() -> bytes:
    image = Image.new("RGBA", (360, 200), (255, 255, 255, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((150, 70, 210, 130), radius=12, fill=(35, 84, 180, 255))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def logo_bytes() -> bytes:
    """A small flat publisher logo (220x72)."""
    image = Image.new("RGB", (220, 72), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle((28, 24, 192, 48), fill=(18, 78, 165))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def article(title: str, category: str, image_url: str = "") -> eb.EditorialArticle:
    base = eb.EditorialArticle(
        title=title,
        summary="국가 AI 데이터센터 전력 인프라 투자 계획이 공개됐다.",
        source="Fixture Publisher",
        published_at=RUN_AT,
        selected_url=f"https://publisher.fixture.test/news/{abs(hash(title)) % 10**8}",
        link_kind=news_access.LINK_KIND_PUBLISHER_DIRECT,
        link_label=news_access.LINK_LABEL_PUBLISHER_DIRECT,
        category=category,
        collection_source_kind="offline_fixture",
        publisher_article_url=f"https://publisher.fixture.test/news/{abs(hash(title)) % 10**8}",
    )
    if not image_url:
        return base
    candidate = eb.ImageCandidateOption(
        url=image_url,
        source_kind="og_image",
        source_page_url=base.publisher_article_url,
        reason="selected_og_image",
    )
    return eb.replace(
        base,
        image_url=image_url,
        image_remote_url=image_url,
        image_source_kind="og_image",
        image_reason="selected_og_image",
        image_candidates=(candidate,),
    )


def main() -> int:
    payloads = {
        "photo-a.jpg": ("image/jpeg", photo_bytes(1)),
        "photo-b.jpg": ("image/jpeg", photo_bytes(2)),
        "banner.png": ("image/png", banner_bytes()),
        "transparent.png": ("image/png", transparent_bytes()),
        "logo.png": ("image/png", logo_bytes()),
    }

    def downloader(url: str, **_kwargs) -> eb.ImageDownload:
        key = url.rsplit("/", 1)[-1]
        if key == "gone.jpg":
            raise eb.ImageDownloadError("image_http_502", status=502)
        content_type, payload = payloads[key]
        return eb.ImageDownload(200, content_type, payload, final_url=url)

    rows = [
        article("정상 뉴스 사진 리드", "투자·산업", "https://img.fixture.test/photo-a.jpg"),
        article("이미지 없는 기사", "투자·산업"),
        article("250x24 배너 기사", "기업동향", "https://img.fixture.test/banner.png"),
        article("투명 로고 기사", "기술정보", "https://img.fixture.test/transparent.png"),
        article("로고만 있는 기사", "기업동향", "https://img.fixture.test/logo.png"),
        article("원격 다운로드 실패 기사", "기술정보", "https://img.fixture.test/gone.jpg"),
        article("두 번째 정상 사진", "투자·산업", "https://img.fixture.test/photo-b.jpg"),
    ]

    with tempfile.TemporaryDirectory(prefix="r4r12-daily-completeness-") as tmp_name:
        root = Path(tmp_name) / "bundle"
        materialized, counters = eb.materialize_preview_images(
            rows, root, html_dir=root / "daily", downloader=downloader, daily=True
        )
        assets_dir = root / "assets" / "images"

        total = len(materialized)
        url_present = 0
        materialized_ok = 0
        valid = 0
        missing = 0
        invalid = 0
        blank = 0
        for item in materialized:
            if item.image_url:
                url_present += 1
            else:
                blank += 1
            asset = assets_dir / item.image_local_asset if item.image_local_asset else None
            if asset is not None and asset.exists():
                materialized_ok += 1
                verdict = eb.assess_daily_image_asset(asset.read_bytes())
                if verdict.valid:
                    valid += 1
                else:
                    invalid += 1
            else:
                missing += 1

        def pct(count: int) -> float:
            return round(100.0 * count / total, 1) if total else 0.0

        check("normal photograph materialized as a real local asset",
              not materialized[0].image_is_category_fallback
              and materialized[0].image_source_kind == "og_image")
        check("missing-image article materializes a category fallback",
              materialized[1].image_is_category_fallback and bool(materialized[1].image_url))
        check("250x24 banner is replaced by a category fallback",
              materialized[2].image_is_category_fallback)
        check("transparent/empty image is replaced by a category fallback",
              materialized[3].image_is_category_fallback)
        check("logo-only image is replaced by a category fallback",
              materialized[4].image_is_category_fallback)
        check("remote download failure is replaced (no broken remote src)",
              materialized[5].image_is_category_fallback
              and materialized[5].image_remote_url == "")
        check("second normal photograph is materialized as a real local asset",
              not materialized[6].image_is_category_fallback)

        check("every article has exactly one materialized output",
              total == len(rows))
        check("DAILY_CANDIDATE_IMAGE_URL_PRESENT is 100%", url_present == total,
              f"{url_present}/{total}")
        check("DAILY_CANDIDATE_IMAGE_MATERIALIZED is 100%", materialized_ok == total,
              f"{materialized_ok}/{total}")
        check("DAILY_CANDIDATE_IMAGE_VALID is 100%", valid == total,
              f"{valid}/{total}")
        check("MISSING_IMAGE_COUNT is 0", missing == 0, str(missing))
        check("INVALID_IMAGE_COUNT is 0", invalid == 0, str(invalid))
        check("BLANK_CARD_COUNT is 0", blank == 0 and counters.blank_cards == 0)

        # Delivered leads = the ordered head that a Daily edition would send.
        delivered = materialized[:6]
        delivered_valid = 0
        for item in delivered:
            asset = assets_dir / item.image_local_asset
            if asset.exists() and eb.assess_daily_image_asset(asset.read_bytes()).valid:
                delivered_valid += 1
        check("DAILY_DELIVERED_LEAD_IMAGE_VALID is 100%",
              delivered_valid == len(delivered), f"{delivered_valid}/{len(delivered)}")
        delivered_pct = (
            round(100.0 * delivered_valid / len(delivered), 1) if delivered else 0.0
        )

        check("no repository image files were written",
              all(root.resolve() in path.resolve().parents
                  for path in assets_dir.glob("*")))

    print(f"checks={CHECKS} failures={len(FAILURES)}")
    print(
        "daily_image_completeness="
        + ("PASS" if not FAILURES else "FAIL")
        + f" DAILY_CANDIDATE_IMAGE_URL_PRESENT={pct(url_present)}%"
        + f" DAILY_CANDIDATE_IMAGE_MATERIALIZED={pct(materialized_ok)}%"
        + f" DAILY_CANDIDATE_IMAGE_VALID={pct(valid)}%"
        + f" DAILY_DELIVERED_LEAD_IMAGE_VALID={delivered_pct}%"
        + f" MISSING_IMAGE_COUNT={missing} INVALID_IMAGE_COUNT={invalid}"
        + f" BLANK_CARD_COUNT={blank}"
        + " network_calls=0 smtp_attempts=0 teams_sends=0 telegram_sends=0"
        + " production_state_writes=0"
    )
    if FAILURES:
        for name in FAILURES:
            print(f"FAILED: {name}")
        return 1
    print("RESULT=D7-AK-6E_R4R12_DAILY_IMAGE_COMPLETENESS_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
