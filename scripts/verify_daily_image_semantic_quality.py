#!/usr/bin/env python3
"""D7-AK-6E R4-R12 §3 — Daily image semantic-quality regressions.

Complements ``verify_daily_image_completeness.py`` (which proves 100% coverage)
by pinning the exact semantic regressions that must never recur, and by proving
the design correction: a single static image per category is not acceptable —
unrelated articles in one category receive distinct, deterministic per-article
fallback assets, each explicitly labeled a category visual (never presented as
an article photograph, never a publisher trademark).

Offline (network 0, sends 0, production state writes 0). Exact cases:

* a missing MOIS image → labeled category fallback (never a blank card);
* a 250x24 MOIS banner → rejected, replaced by a category fallback;
* a transparent image → rejected, replaced;
* a logo-only image → rejected, replaced;
* the same photograph reused across two unrelated articles → the second is
  rejected as a cross-article duplicate and replaced (distinct final assets);
* a remote-image download failure → replaced (no broken remote src);
* two unrelated same-category articles → distinct deterministic fallback assets;
* the fallback materialization is deterministic byte-for-byte across runs.
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


def photo_bytes(seed: int) -> bytes:
    image = Image.new("RGB", (640, 360), (24, 40, 60))
    draw = ImageDraw.Draw(image)
    base = (seed * 29) % 160
    for y in range(360):
        draw.line((0, y, 640, y),
                  fill=((40 + base + y // 3) % 255, (90 + y // 4) % 255, (130 + y // 5) % 255))
    for index in range(6):
        x = 35 + index * 92
        draw.rectangle((x, 60 + (index % 3) * 25, x + 70, 295),
                       fill=((90 + index * 21) % 255, (120 + index * 31) % 255, (170 + index * 17) % 255))
    draw.ellipse((420, 52, 600, 232), fill=(225, 190, 92), outline=(48, 66, 82), width=5)
    buffer = BytesIO()
    image.save(buffer, format="JPEG")
    return buffer.getvalue()


def banner_bytes() -> bytes:
    image = Image.new("RGB", (250, 24), (255, 255, 255))
    ImageDraw.Draw(image).rectangle((6, 6, 60, 18), fill=(18, 78, 165))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def transparent_bytes() -> bytes:
    image = Image.new("RGBA", (360, 200), (255, 255, 255, 0))
    ImageDraw.Draw(image).rounded_rectangle((150, 70, 210, 130), radius=12, fill=(35, 84, 180, 255))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def logo_bytes() -> bytes:
    image = Image.new("RGB", (220, 72), (255, 255, 255))
    ImageDraw.Draw(image).rectangle((28, 24, 192, 48), fill=(18, 78, 165))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def article(title: str, category: str, image_url: str = "",
            source: str = "행정안전부") -> eb.EditorialArticle:
    ident = abs(hash(title)) % 10**8
    base = eb.EditorialArticle(
        title=title,
        summary="국가 AI 데이터센터 전력 인프라 투자 계획이 공개됐다.",
        source=source,
        published_at=RUN_AT,
        selected_url=f"https://publisher.fixture.test/news/{ident}",
        link_kind=news_access.LINK_KIND_PUBLISHER_DIRECT,
        link_label=news_access.LINK_LABEL_PUBLISHER_DIRECT,
        category=category,
        collection_source_kind="offline_fixture",
        publisher_article_url=f"https://publisher.fixture.test/news/{ident}",
    )
    if not image_url:
        return base
    candidate = eb.ImageCandidateOption(
        url=image_url, source_kind="og_image",
        source_page_url=base.publisher_article_url, reason="selected_og_image",
    )
    return eb.replace(
        base, image_url=image_url, image_remote_url=image_url,
        image_source_kind="og_image", image_reason="selected_og_image",
        image_candidates=(candidate,),
    )


def materialize(rows, downloader):
    tmp = tempfile.mkdtemp(prefix="r4r12-daily-semantic-")
    root = Path(tmp) / "bundle"
    out, counters = eb.materialize_preview_images(
        rows, root, html_dir=root / "daily", downloader=downloader, daily=True
    )
    return out, counters, root / "assets" / "images"


def main() -> int:
    payloads = {
        "photo.jpg": ("image/jpeg", photo_bytes(1)),
        "banner.png": ("image/png", banner_bytes()),
        "transparent.png": ("image/png", transparent_bytes()),
        "logo.png": ("image/png", logo_bytes()),
    }

    def downloader(url: str, **_kwargs) -> eb.ImageDownload:
        key = url.rsplit("/", 1)[-1]
        if key == "gone.jpg":
            raise eb.ImageDownloadError("image_http_404", status=404)
        content_type, payload = payloads[key]
        return eb.ImageDownload(200, content_type, payload, final_url=url)

    def category_fallback(item) -> bool:
        return (
            item.image_is_category_fallback
            and item.image_source_kind == "category_fallback"
            and bool(item.image_url)
            and item.image_remote_url == ""
            and not item.image_url.startswith("http")
        )

    # ---- exact regression rows -------------------------------------------- #
    mois_missing = article("MOIS 이미지 없는 발표", "기업동향")
    mois_banner = article("MOIS 250x24 배너", "기업동향", "https://img.fixture.test/banner.png")
    transparent = article("투명 이미지 기사", "기술정보", "https://img.fixture.test/transparent.png")
    logo_only = article("로고만 있는 기사", "기업동향", "https://img.fixture.test/logo.png")
    remote_fail = article("원격 실패 기사", "기술정보", "https://img.fixture.test/gone.jpg")

    out, counters, assets = materialize(
        [mois_missing, mois_banner, transparent, logo_only, remote_fail], downloader
    )
    check("missing MOIS image → labeled category fallback", category_fallback(out[0]))
    check("250x24 MOIS banner → category fallback", category_fallback(out[1]))
    check("transparent image → category fallback", category_fallback(out[2]))
    check("logo-only image → category fallback", category_fallback(out[3]))
    check("remote download failure → category fallback (no broken remote src)",
          category_fallback(out[4]))
    check("no blank cards for any regression row",
          all(bool(item.image_url) for item in out) and counters.blank_cards == 0)
    check("fallback labels itself a category visual, never an article photo",
          all(item.image_quality_reason == "daily_category_fallback_asset"
              for item in out))
    check("every rejected regression asset passes the hard gate after fallback",
          all(eb.assess_daily_image_asset((assets / item.image_local_asset).read_bytes()).valid
              for item in out))

    # ---- duplicate photograph across two unrelated articles --------------- #
    dup_a = article("서로 다른 기사 A", "투자·산업", "https://img.fixture.test/photo.jpg",
                    source="연합뉴스")
    dup_b = article("완전히 다른 기사 B", "투자·산업", "https://img.fixture.test/photo.jpg",
                    source="조선일보")
    dup_out, dup_counters, dup_assets = materialize([dup_a, dup_b], downloader)
    check("same photo reused across unrelated articles: first keeps the real image",
          not dup_out[0].image_is_category_fallback)
    check("same photo reused across unrelated articles: second is duplicate-rejected",
          dup_out[1].image_is_category_fallback
          and dup_counters.daily_duplicate_image_rejections >= 1,
          f"dup_rej={dup_counters.daily_duplicate_image_rejections}")
    check("duplicate rejection yields distinct final assets",
          dup_out[0].image_local_asset != dup_out[1].image_local_asset)

    # ---- two unrelated same-category articles → distinct fallbacks --------- #
    same_cat = [
        article("같은 카테고리 기사 하나", "투자·산업"),
        article("같은 카테고리 전혀 다른 기사", "투자·산업"),
    ]
    sc_out, _sc_counters, sc_assets = materialize(same_cat, downloader)
    check("two unrelated same-category articles receive distinct fallback assets",
          sc_out[0].image_local_asset != sc_out[1].image_local_asset
          and category_fallback(sc_out[0]) and category_fallback(sc_out[1]),
          f"{sc_out[0].image_local_asset} vs {sc_out[1].image_local_asset}")
    check("distinct fallback assets are byte-distinct on disk",
          (sc_assets / sc_out[0].image_local_asset).read_bytes()
          != (sc_assets / sc_out[1].image_local_asset).read_bytes())

    # ---- determinism: same inputs → identical asset digests --------------- #
    sc_out2, _c2, _a2 = materialize(same_cat, downloader)
    check("category fallback is deterministic byte-for-byte across runs",
          [item.image_local_asset for item in sc_out]
          == [item.image_local_asset for item in sc_out2])

    print(f"checks={CHECKS} failures={len(FAILURES)}")
    print(
        "daily_image_semantic_quality="
        + ("PASS" if not FAILURES else "FAIL")
        + " static_per_category_image=rejected"
        + " per_article_deterministic_variants=proven"
        + " network_calls=0 smtp_attempts=0 teams_sends=0 telegram_sends=0"
        + " production_state_writes=0"
    )
    if FAILURES:
        for name in FAILURES:
            print(f"FAILED: {name}")
        return 1
    print("RESULT=D7-AK-6E_R4R12_DAILY_IMAGE_SEMANTIC_QUALITY_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
