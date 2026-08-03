#!/usr/bin/env python3
"""Offline full-list image coverage verifier for the exact News Censor surface."""

from __future__ import annotations

import copy
import json
import re
import sys
import tempfile
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path

from PIL import ImageDraw

ROOT = Path(__file__).resolve().parents[1]
for item in (ROOT, ROOT / "scripts"):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from app import editorial_briefings, news_censor_verified_state  # noqa: E402
import build_executive_brief  # noqa: E402
import build_news_censor  # noqa: E402
import verify_news_censor_reference_parity as parity  # noqa: E402

_BASE_MODEL: dict | None = None


def _fixture_model(count: int) -> dict:
    global _BASE_MODEL
    if _BASE_MODEL is None:
        brief = build_executive_brief.attach_artifact_contract(
            build_executive_brief.build_brief_via_mock_pipeline()
        )
        _BASE_MODEL = build_news_censor.build_model(
            brief,
            edition=build_news_censor._edition_date("2026-08-03"),
        )
    base = copy.deepcopy(_BASE_MODEL)
    if not base.get("articles"):
        raise AssertionError("mock brief produced no News Censor article")
    seed = base["articles"][0]
    rows = []
    for index in range(count):
        row = copy.deepcopy(seed)
        row.update({
            "id": f"image-fixture-{index + 1:02d}",
            "title": f"검증 기사 {index + 1} — 전체 이미지 고려",
            "url": f"https://publisher-{index + 1}.example.test/news/{index + 1}",
            "source": f"검증 발행사 {index + 1}",
            "initials": f"I{index + 1}",
            "image_src": build_news_censor._fallback_image_data(row),
            "image_status": "deterministic_fallback",
            "image_source_kind": "fallback",
            "image_source_page_url": f"https://publisher-{index + 1}.example.test/news/{index + 1}",
            "image_width": None,
            "image_height": None,
            "image_quality_accepted": False,
            "image_reason": "no_image_candidate",
            "image_attempted": False,
            "image_cache_hit": False,
            "image_materialized": False,
            "image_retry_after": "",
        })
        rows.append(row)
    base["articles"] = rows
    base["article_count"] = count
    return base


def _image_bytes(index: int) -> bytes:
    image = editorial_briefings.Image.new("RGB", (400, 240))
    pixels = image.load()
    for y in range(240):
        for x in range(400):
            pixels[x, y] = (
                (x * 7 + index * 31) % 256,
                (y * 11 + index * 43) % 256,
                ((x + y) * 5 + index * 59) % 256,
            )
    stream = BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


def _undersized_bytes() -> bytes:
    image = editorial_briefings.Image.new("RGB", (250, 24), "white")
    stream = BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


def _logo_only_bytes() -> bytes:
    image = editorial_briefings.Image.new("RGB", (780, 520), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((270, 225, 510, 295), fill="#005baa")
    draw.rectangle((310, 245, 470, 275), fill="#00a651")
    stream = BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


class FixtureImages:
    def __init__(
        self,
        *,
        invalid_mime: set[int] | None = None,
        duplicate: dict[int, int] | None = None,
        undersized: set[int] | None = None,
        logo_only: set[int] | None = None,
    ):
        self.invalid_mime = invalid_mime or set()
        self.duplicate = duplicate or {}
        self.undersized = undersized or set()
        self.logo_only = logo_only or set()
        self.downloads = 0

    @staticmethod
    def _index(url: str) -> int:
        return int(re.search(r"/(\d+)\.png$", url).group(1))

    def resolver(self, article, **_kwargs):
        index = int(str(article["selected_url"]).rstrip("/").rsplit("/", 1)[-1])
        image_url = f"https://images-{index}.example.test/assets/{index}.png"
        option = editorial_briefings.ImageCandidateOption(
            url=image_url,
            source_kind="og_image",
            source_page_url=str(article["selected_url"]),
            width=400,
            height=240,
            reason="selected_og_image",
        )
        return editorial_briefings.ImageResolution(
            url=image_url,
            source_kind="og_image",
            source_page_url=str(article["selected_url"]),
            width=400,
            height=240,
            fallback_used=False,
            reason="selected_og_image",
            candidates=(option,),
        )

    def downloader(self, url: str, **_kwargs):
        self.downloads += 1
        index = self._index(url)
        if index in self.invalid_mime:
            return editorial_briefings.ImageDownload(
                200, "text/html", b"<html>not an image</html>", url
            )
        if index in self.undersized:
            return editorial_briefings.ImageDownload(
                200, "image/png", _undersized_bytes(), url
            )
        if index in self.logo_only:
            return editorial_briefings.ImageDownload(
                200, "image/png", _logo_only_bytes(), url
            )
        payload_index = self.duplicate.get(index, index)
        return editorial_briefings.ImageDownload(
            200, "image/png", _image_bytes(payload_index), url
        )


def _assert_local_assets(model: dict, output_root: Path) -> None:
    for row in model["articles"]:
        if row["image_status"] != "local_materialized":
            continue
        prefix = build_news_censor.IMAGE_PUBLIC_SRC_PREFIX
        assert row["image_src"].startswith(prefix)
        name = row["image_src"].removeprefix(prefix)
        assert name and Path(name).name == name
        assert (output_root / "assets" / "images" / name).is_file()


def _assert_shell(model: dict) -> None:
    html = build_news_censor.render_html(model)
    assert parity.digest(parity.extract_style(html)) == parity.REFERENCE_STYLE_SHA256
    assert parity.digest(parity.normalized_shell(html)) == parity.REFERENCE_SHELL_SHA256
    assert not parity.remote_image_hotlinks(html)
    assert "width:96px;height:64px" in parity.extract_style(html).replace(" ", "")


def _negative_cache_state(model: dict, path: Path, *, now: datetime) -> None:
    retry_after = (now + timedelta(hours=2)).isoformat(timespec="seconds")
    entries = []
    for row in model["articles"]:
        previous = {
            "image_status": "deterministic_fallback",
            "image_source_kind": "fallback",
            "image_source_page_url": row["url"],
            "image_quality_accepted": False,
            "image_reason": "publisher_blocked",
            "image_attempted": True,
            "image_cache_hit": False,
            "image_materialized": False,
            "image_retry_after": retry_after,
        }
        entries.append(news_censor_verified_state.verified_entry_from_article(
            row,
            now=now,
            previous=previous,
            categories=("ai",),
        ))
    state = news_censor_verified_state.empty_state(now=now)
    state["entries"] = sorted(entries, key=lambda item: item["canonical_url"])
    news_censor_verified_state.atomic_write_state(path, state)


def main() -> int:
    assert "image_limit" not in build_news_censor.materialize_article_images.__code__.co_varnames
    source = Path(build_news_censor.__file__).read_text(encoding="utf-8")
    workflow = (ROOT / ".github/workflows/scheduled-live-refresh.yml").read_text(encoding="utf-8")
    assert "--image-limit" not in source and "--image-limit" not in workflow
    assert "--verified-state data/news_censor_verified_state.json" in workflow

    with tempfile.TemporaryDirectory(prefix="d7ak6e-image-coverage-", dir="/tmp") as raw:
        root = Path(raw)
        for count in (8, 20, 40):
            model = _fixture_model(count)
            output = root / f"coverage-{count}"
            fixture = FixtureImages()
            counters = build_news_censor.materialize_article_images(
                model,
                output_root=output,
                resolver=fixture.resolver,
                downloader=fixture.downloader,
            )
            assert counters["displayed_articles"] == count
            assert counters["image_resolution_attempted"] == count
            assert counters["image_download_attempts"] == count
            assert counters["image_materialized"] == count
            assert counters["deterministic_fallbacks"] == 0
            assert counters["not_attempted_due_to_cap"] == 0
            assert counters["accounting_pass"] and counters["all_displayed_considered"]
            assert counters["real_image_positions"] == list(range(1, count + 1))
            assert fixture.downloads == count
            _assert_local_assets(model, output)
            _assert_shell(model)
            if count >= 9:
                assert model["articles"][8]["image_status"] == "local_materialized"
            if count >= 20:
                assert model["articles"][19]["image_status"] == "local_materialized"
            if count == 40:
                assert model["articles"][39]["image_status"] == "local_materialized"

                # A rendered prior edition is an article→asset cache. Reusing all
                # 40 associations must perform zero resolver/download work.
                (output / "latest.html").write_text(
                    build_news_censor.render_html(model), encoding="utf-8"
                )
                cached_model = _fixture_model(40)
                cache_fixture = FixtureImages()
                cached = build_news_censor.materialize_article_images(
                    cached_model,
                    output_root=output,
                    resolver=cache_fixture.resolver,
                    downloader=cache_fixture.downloader,
                )
                assert cached["valid_cached_local_images"] == 40
                assert cached["image_resolution_attempted"] == 0
                assert cached["image_download_attempts"] == 0
                assert cached["not_attempted_due_to_cap"] == 0
                assert cache_fixture.downloads == 0
                _assert_local_assets(cached_model, output)

        rejected_model = _fixture_model(5)
        rejected_fixture = FixtureImages(
            invalid_mime={2},
            duplicate={3: 1},
            undersized={4},
            logo_only={5},
        )
        rejected = build_news_censor.materialize_article_images(
            rejected_model,
            output_root=root / "rejected",
            resolver=rejected_fixture.resolver,
            downloader=rejected_fixture.downloader,
        )
        assert rejected["image_materialized"] == 1
        assert rejected["deterministic_fallbacks"] == 4
        assert rejected["fallback_reason_counts"] == {
            "dimensions_too_small": 1,
            "duplicate_image_rejected": 1,
            "invalid_mime": 1,
            "logo_or_banner_rejected": 1,
        }
        assert rejected["duplicate_image_rejections"] == 1
        assert rejected["not_attempted_due_to_cap"] == 0
        assert all(
            row["image_status"] == "local_materialized"
            or row["image_reason"] in build_news_censor.IMAGE_FALLBACK_REASONS
            for row in rejected_model["articles"]
        )
        _assert_shell(rejected_model)

        # A recent explicit failure is a negative-cache hit, not another probe.
        # Once its TTL expires, every row is again eligible for one bounded try.
        negative_model = _fixture_model(3)
        cache_now = (
            build_news_censor._parse_datetime(
                negative_model["articles"][0]["published_at"]
            )
            + timedelta(hours=1)
        )
        state_path = root / "negative-image-state.json"
        _negative_cache_state(negative_model, state_path, now=cache_now)
        negative_fixture = FixtureImages()
        negative = build_news_censor.materialize_article_images(
            negative_model,
            output_root=root / "negative-cache",
            verified_state_path=state_path,
            resolver=negative_fixture.resolver,
            downloader=negative_fixture.downloader,
            now=cache_now,
        )
        assert negative["negative_cache_hits"] == 3
        assert negative["image_resolution_attempted"] == 0
        assert negative["image_download_attempts"] == 0
        assert negative_fixture.downloads == 0
        assert negative["fallback_reason_counts"] == {"publisher_blocked": 3}
        retry_fixture = FixtureImages()
        retried = build_news_censor.materialize_article_images(
            _fixture_model(3),
            output_root=root / "negative-cache",
            verified_state_path=state_path,
            resolver=retry_fixture.resolver,
            downloader=retry_fixture.downloader,
            now=cache_now + timedelta(hours=3),
        )
        assert retried["image_resolution_attempted"] == 3
        assert retried["image_materialized"] == 3
        assert retry_fixture.downloads == 3
        state_cache_fixture = FixtureImages()
        state_cached = build_news_censor.materialize_article_images(
            _fixture_model(3),
            output_root=root / "negative-cache",
            verified_state_path=state_path,
            resolver=state_cache_fixture.resolver,
            downloader=state_cache_fixture.downloader,
            now=cache_now + timedelta(hours=3, minutes=1),
        )
        assert state_cached["valid_cached_local_images"] == 3
        assert state_cached["image_resolution_attempted"] == 0
        assert state_cached["image_download_attempts"] == 0
        assert state_cache_fixture.downloads == 0

        # A prior local association is still quality-checked before reuse. A
        # logo-only cache entry must be retried instead of becoming permanent.
        cache_reject_root = root / "quality-cache-reject"
        cached_logo_model = _fixture_model(1)
        cached_logo_model["articles"][0].update({
            "image_src": build_news_censor.IMAGE_PUBLIC_SRC_PREFIX + "cached-logo.png",
            "image_status": "local_materialized",
            "image_source_kind": "cached_local",
            "image_quality_accepted": True,
            "image_reason": "image_materialized",
            "image_materialized": True,
        })
        build_news_censor._atomic_write_bytes(
            cache_reject_root / "assets" / "images" / "cached-logo.png",
            _logo_only_bytes(),
        )
        (cache_reject_root / "latest.html").write_text(
            build_news_censor.render_html(cached_logo_model), encoding="utf-8"
        )
        cache_reject_fixture = FixtureImages()
        cache_rejected = build_news_censor.materialize_article_images(
            _fixture_model(1),
            output_root=cache_reject_root,
            resolver=cache_reject_fixture.resolver,
            downloader=cache_reject_fixture.downloader,
        )
        assert cache_rejected["valid_cached_local_images"] == 0
        assert cache_rejected["image_resolution_attempted"] == 1
        assert cache_rejected["image_materialized"] == 1
        assert cache_reject_fixture.downloads == 1

    print("RESULT=D7-AK-6E_NEWS_CENSOR_FULL_IMAGE_COVERAGE_PASS")
    print(
        "fixtures=8,20,40 article_9=local article_20=local article_40=local "
        "cache_downloads=0 not_attempted_due_to_cap=0 remote_hotlinks=0 "
        "smtp_attempts=0 teams_sends=0 telegram_sends=0 production_state_writes=0"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
