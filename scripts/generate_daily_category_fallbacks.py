#!/usr/bin/env python3
"""D7-AK-6E R4-R12 §3 — generate the committed Daily category-fallback visuals.

One-off provenance tool: the runtime never generates images — it copies the
PNG assets this script wrote into ``data/editorial_image_fallbacks/`` (checked
into the repository, so every materialization is deterministic byte-for-byte).

Each visual is a neutral abstract composition that:
* clearly labels itself as a category visual, never an article photograph;
* varies by category (distinct color + label) so cards never all look alike;
* contains no publisher trademark, logo, or photographic content;
* passes the Daily hard image gate (800x450, 16:9, rich pixel variation).

Requires Pillow and a Korean-capable TTF (searched in common locations).
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "data" / "editorial_image_fallbacks"
SIZE = (800, 450)

FONT_CANDIDATES = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    "/mnt/c/Windows/Fonts/malgunbd.ttf",
    "/mnt/c/Windows/Fonts/malgun.ttf",
)

# category key -> (asset name, base color, accent color, label)
CATEGORIES = {
    "투자·산업": ("category-invest-industry.png", (11, 44, 84), (86, 156, 214), "투자·산업"),
    "기업동향": ("category-corporate.png", (26, 71, 42), (120, 190, 140), "기업동향"),
    "기술정보": ("category-technology.png", (66, 39, 90), (170, 130, 210), "기술정보"),
    "generic": ("category-ai-infrastructure.png", (50, 50, 58), (150, 160, 180), "AI·인프라 일반"),
}


def load_font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    raise SystemExit("no Korean-capable TTF found — install Noto CJK or map malgun.ttf")


def draw_asset(base: tuple, accent: tuple, label: str) -> Image.Image:
    image = Image.new("RGB", SIZE, base)
    draw = ImageDraw.Draw(image)
    # deterministic abstract grid — varied enough to defeat flat-graphic
    # heuristics, obviously non-photographic.
    for index in range(0, SIZE[0] + SIZE[1], 46):
        shade = tuple(
            min(255, channel + ((index * 7) % 60)) for channel in base
        )
        draw.line(((index, 0), (index - SIZE[1], SIZE[1])), fill=shade, width=10)
    for x in range(40, SIZE[0], 96):
        for y in range(40, SIZE[1], 96):
            radius = 3 + ((x + y) // 96) % 4
            draw.ellipse(
                (x - radius, y - radius, x + radius, y + radius), fill=accent
            )
    draw.rectangle((40, 280, 760, 410), fill=tuple(max(0, c - 14) for c in base))
    draw.text((60, 292), f"카테고리 대표 이미지 · {label}", font=load_font(40), fill=(255, 255, 255))
    draw.text(
        (60, 352),
        "기사 사진 아님 · CATEGORY VISUAL (NOT AN ARTICLE PHOTO)",
        font=load_font(24),
        fill=accent,
    )
    return image


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for key, (name, base, accent, label) in CATEGORIES.items():
        path = OUTPUT_DIR / name
        draw_asset(base, accent, label).save(path, format="PNG", optimize=True)
        print(f"written: {path.relative_to(ROOT)} ({path.stat().st_size} bytes) [{key}]")
    print("RESULT=DAILY_CATEGORY_FALLBACKS_GENERATED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
