#!/usr/bin/env python3
"""Offline adversarial regression for real-photo production authorization."""

from __future__ import annotations

import hashlib
import json
import tempfile
import sys
from dataclasses import replace
from datetime import datetime
from io import BytesIO
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import editorial_briefings as brief, editorial_review
from scripts import run_editorial_briefing as runner


def raster(seed: int = 1) -> bytes:
    image = Image.new("RGB", (640, 360))
    pixels = []
    for y in range(360):
        for x in range(640):
            pixels.append(
                (
                    (x * 13 + y * 3 + seed * 17) % 256,
                    (x * 5 + y * 11 + seed * 29) % 256,
                    (x * 7 + y * 17 + seed * 37) % 256,
                )
            )
    image.putdata(pixels)
    output = BytesIO()
    image.save(output, format="JPEG", quality=88)
    return output.getvalue()


def article(index: int, **changes) -> brief.EditorialArticle:
    base = brief.EditorialArticle(
        title=f"AI 데이터센터 전력 계약 {index}",
        summary="AI 데이터센터 전력 인프라 계약이 확정됐다.",
        source="한국일보",
        published_at=datetime.fromisoformat("2026-08-11T06:00:00+09:00"),
        selected_url=f"https://example.com/article/{index}",
        link_kind="publisher_direct",
        link_label="원문 보기",
        category="투자·산업",
        image_source_kind="og_image",
        image_fallback_used=False,
        image_quality_accepted=True,
        image_quality_reason="image_quality_passed",
    )
    return replace(base, **changes)


def real(index: int) -> brief.EditorialArticle:
    payload = raster(index)
    return brief.mark_real_article_photo(
        article(index),
        payload,
        local_src=f"assets/2026-08-11/{index}.jpg",
        local_asset=f"{index}.jpg",
    )


def review_article(
    index: int,
    *,
    edition_key: str,
    image_url: str,
    image_remote_url: str = "https://images.fixture.test/recovery.jpg",
) -> brief.EditorialArticle:
    candidate = {
        "title": f"AI 데이터센터 전력 계약 {index}",
        "summary": "AI 데이터센터 전력 인프라 계약이 확정됐다.",
        "source": "한국일보",
        "published_at": "2026-08-11T06:00:00+09:00",
        "selected_url": f"https://example.com/review/{index}",
        "category": "투자·산업",
        "image_url": image_url,
        "image_remote_url": image_remote_url,
        "image_source_kind": "og_image",
        "image_source_page_url": f"https://example.com/review/{index}",
        "image_fallback_used": False,
        "image_quality_accepted": True,
        "image_quality_reason": "image_quality_passed",
        "ai_centrality_level": "explicit_ai_core",
    }
    return editorial_review.candidate_to_article(
        editorial_review._candidate_for_daily_render(candidate, edition_key)
    )


def assert_gate(rows: list[brief.EditorialArticle], expected: bool) -> None:
    error = brief.production_image_gate_error(brief.image_audit_manifest(rows))
    if (error == "") is not expected:
        raise AssertionError(f"unexpected image gate={error!r}")


def main() -> int:
    fallback = article(
        100,
        image_source_kind="category_fallback",
        image_fallback_used=True,
        image_is_category_fallback=True,
        image_quality_accepted=True,
        image_quality_reason="daily_category_fallback_asset",
        image_real_article_photo=False,
        image_url="data:image/svg+xml,%3Csvg/%3E",
    )

    # Cases 3/4 and the exact production incident shape: img-card count has no authority.
    for product in ("Daily", "Weekly"):
        manifest = {
            "article_count": 12,
            "real_article_photo_count": 0,
            "fallback_visual_count": 12,
            "image_materialization_failed_count": 0,
            "image_quality_rejected_count": 0,
        }
        if not brief.production_image_gate_error(manifest):
            raise AssertionError(f"all-fallback {product} passed")
    print("ALL_FALLBACK_DAILY=BLOCKED")
    print("ALL_FALLBACK_WEEKLY=BLOCKED")
    print("TWELVE_IMG_TAGS_ZERO_REAL_PHOTOS=BLOCKED")

    # Cases 5/6.
    assert_gate([real(index) for index in range(1, 6)] + [fallback], False)
    assert_gate([real(index) for index in range(1, 7)], True)
    print("MIXED_REAL_5_FALLBACK_1=BLOCKED")
    print("ALL_REAL_PHOTOS=PASS")

    # Case 7: the bytes, not the suffix, own raster identity.
    fake_jpg_rejected = False
    try:
        brief.mark_real_article_photo(
            article(200),
            b"<svg xmlns='http://www.w3.org/2000/svg'></svg>",
            local_src="assets/2026-08-11/fake.jpg",
            local_asset="fake.jpg",
        )
    except brief.EditorialError:
        fake_jpg_rejected = True
    if not fake_jpg_rejected:
        raise AssertionError("SVG bytes under .jpg passed real-photo validation")
    print("FAKE_RASTER_EXTENSION=BLOCKED")

    # Case 8: remote hotlinks never count as a local/public materialized photo.
    hotlink = article(201, image_url="https://publisher.example/photo.jpg")
    assert_gate([hotlink], False)
    if brief._safe_brief_image_src(hotlink):
        raise AssertionError("remote hotlink reached rendered image source")
    print("REMOTE_HOTLINK=BLOCKED")

    # Blocker 2 / Cases A-D: an exact dated Review asset is the first immutable
    # source for Daily, with explicit edition/path/digest authority. Publisher
    # download is recovery only; traversal fails before any filesystem read.
    with tempfile.TemporaryDirectory(prefix="r4-ops-7-review-owned-") as temporary:
        root = Path(temporary)
        edition_key = "2026-08-12"
        review_root = root / "docs/editorial/review" / edition_key
        review_assets = review_root / "assets/images"
        review_assets.mkdir(parents=True)
        publication_dir = root / "docs/editorial/daily"
        publication_dir.mkdir(parents=True)

        exact_payload = raster(301)
        exact_digest = hashlib.sha256(exact_payload).hexdigest()
        exact_name = f"{exact_digest[:24]}.jpg"
        (review_assets / exact_name).write_bytes(exact_payload)
        exact = review_article(
            301,
            edition_key=edition_key,
            image_url=f"assets/images/{exact_name}",
        )
        exact_download_calls = 0

        def must_not_download(*_args, **_kwargs):
            nonlocal exact_download_calls
            exact_download_calls += 1
            raise brief.ImageDownloadError("forced_remote_failure")

        selected, exact_audit, exact_assets = runner.prepare_publication_images(
            [exact],
            edition_type="daily",
            edition_key=edition_key,
            publication_dir=publication_dir,
            review_asset_root=review_root,
            downloader=must_not_download,
        )
        if (
            len(selected) != 1
            or len(exact_assets) != 1
            or exact_download_calls != 0
            or selected[0].image_materialization_reason
            != "copied_exact_review_asset"
            or exact_assets[0]["payload"] != exact_payload
            or exact_audit["real_article_photo_count"] != 1
        ):
            raise AssertionError("exact Review asset was not authoritative first")

        traversal = review_article(
            302,
            edition_key=edition_key,
            image_url="assets/images/../../escape.jpg",
            image_remote_url="",
        )
        traversal_failed = False
        try:
            runner.prepare_publication_images(
                [traversal],
                edition_type="daily",
                edition_key=edition_key,
                publication_dir=publication_dir,
                review_asset_root=review_root,
                downloader=must_not_download,
            )
        except runner.OrchestratorError:
            traversal_failed = True
        if not traversal_failed:
            raise AssertionError("Review asset traversal did not fail closed")

        invalid_expected = raster(303)
        invalid_digest = hashlib.sha256(invalid_expected).hexdigest()
        invalid_name = f"{invalid_digest[:24]}.jpg"
        (review_assets / invalid_name).write_bytes(
            b"<svg xmlns='http://www.w3.org/2000/svg'></svg>"
        )
        invalid = review_article(
            303,
            edition_key=edition_key,
            image_url=f"assets/images/{invalid_name}",
        )
        invalid_download_calls = 0

        def failed_recovery(*_args, **_kwargs):
            nonlocal invalid_download_calls
            invalid_download_calls += 1
            raise brief.ImageDownloadError("forced_remote_failure")

        invalid_selected, invalid_audit, invalid_assets = (
            runner.prepare_publication_images(
                [invalid],
                edition_type="daily",
                edition_key=edition_key,
                publication_dir=publication_dir,
                review_asset_root=review_root,
                downloader=failed_recovery,
            )
        )
        if (
            invalid_selected
            or invalid_assets
            or invalid_download_calls < 1
            or not invalid_audit["image_materialization_failed_count"]
        ):
            raise AssertionError("invalid Review bytes counted as a real photo")

        recovery_payload = raster(304)
        recovery_digest = hashlib.sha256(recovery_payload).hexdigest()
        unavailable_name = f"{recovery_digest[:24]}.jpg"
        unavailable = review_article(
            304,
            edition_key=edition_key,
            image_url=f"assets/images/{unavailable_name}",
        )
        recovery_download_calls = 0

        def successful_recovery(url, *_args, **_kwargs):
            nonlocal recovery_download_calls
            recovery_download_calls += 1
            return brief.ImageDownload(
                200, "image/jpeg", recovery_payload, final_url=url
            )

        recovered, recovered_audit, recovered_assets = (
            runner.prepare_publication_images(
                [unavailable],
                edition_type="daily",
                edition_key=edition_key,
                publication_dir=publication_dir,
                review_asset_root=review_root,
                downloader=successful_recovery,
            )
        )
        if (
            len(recovered) != 1
            or len(recovered_assets) != 1
            or recovery_download_calls != 1
            or recovered_assets[0]["payload"] != recovery_payload
            or recovered_audit["real_article_photo_count"] != 1
        ):
            raise AssertionError("bounded publisher recovery did not materialize Daily ownership")

    print("EXACT_REVIEW_ASSET_FIRST=PASS")
    print("EXACT_REVIEW_ASSET_REMOTE_INDEPENDENCE=PASS")
    print("REVIEW_ASSET_PATH_ESCAPE_BLOCKED=PASS")
    print("REVIEW_ASSET_BYTES_REVALIDATED=PASS")
    print("REVIEW_ASSET_UNAVAILABLE_REMOTE_RECOVERY=PASS")

    # Case 9: a stale Review-relative asset cannot become fallback-success.
    with tempfile.TemporaryDirectory(prefix="r4-ops-7-photo-") as temporary:
        root = Path(temporary)
        publication_dir = root / "editorial" / "daily"
        publication_dir.mkdir(parents=True)
        unavailable = article(
            202,
            image_url="../review/2026-08-11/assets/images/missing.jpg",
            image_remote_url="",
        )

        def failed_download(*_args, **_kwargs):
            raise brief.ImageDownloadError("image_download_failed")

        selected, audit, assets = runner.prepare_publication_images(
            [unavailable],
            edition_type="daily",
            edition_key="2026-08-11",
            publication_dir=publication_dir,
            downloader=failed_download,
        )
        if selected or assets or not audit["image_materialization_failed_count"]:
            raise AssertionError("missing Review-relative image became publication success")

        dated = publication_dir / "2026-08-11.html"
        latest = publication_dir / "latest.html"
        html = '<main data-edition-key="2026-08-11"><img src="assets/2026-08-11/missing.jpg"></main>'
        dated.write_text(html, encoding="utf-8")
        latest.write_text(html, encoding="utf-8")
        missing_asset_manifest = {
            "edition_type": "daily",
            "edition_key": "2026-08-11",
            "dated_path": str(dated),
            "latest_path": str(latest),
            "html_sha256": hashlib.sha256(html.encode()).hexdigest(),
            "article_count": 1,
            "real_article_photo_count": 1,
            "fallback_visual_count": 0,
            "image_materialization_failed_count": 0,
            "image_quality_rejected_count": 0,
            "production_image_gate_required": True,
            "image_manifest_path": (
                "docs/editorial/daily/assets/2026-08-11/image-manifest.json"
            ),
            "image_manifest_sha256": "f" * 64,
            "publication_image_assets": [{
                "relative_path": "editorial/daily/assets/2026-08-11/missing.jpg",
                "sha256": hashlib.sha256(raster(9)).hexdigest(),
                "byte_size": len(raster(9)),
            }],
        }
        original_paths = runner._docs_paths
        runner._docs_paths = lambda _kind, _key: (dated, latest)
        missing_publication_failed = False
        try:
            runner._verify_local_publication(missing_asset_manifest)
        except runner.OrchestratorError:
            missing_publication_failed = True
        finally:
            runner._docs_paths = original_paths
        if not missing_publication_failed:
            raise AssertionError("missing publication asset verifier passed")
    print("REVIEW_RELATIVE_ASSET_UNAVAILABLE=BLOCKED")

    # The final local publication verifier binds article-count, audit manifest,
    # exact raster bytes, and rendered <img src> references together. A valid
    # bundle passes; changing only HTML to a remote hotlink fails closed.
    with tempfile.TemporaryDirectory(prefix="r4-ops-7-publication-") as temporary:
        repo_root = Path(temporary)
        publication_dir = repo_root / "docs" / "editorial" / "daily"
        asset_dir = publication_dir / "assets" / "2026-08-11"
        asset_dir.mkdir(parents=True)
        payload = raster(250)
        digest = hashlib.sha256(payload).hexdigest()
        filename = f"{digest[:24]}.jpg"
        (asset_dir / filename).write_bytes(payload)
        publication_article = brief.mark_real_article_photo(
            article(250),
            payload,
            local_src=f"assets/2026-08-11/{filename}",
            local_asset=filename,
        )
        asset_record = {
            "article_id": brief.editorial_article_id(publication_article),
            "relative_path": f"editorial/daily/assets/2026-08-11/{filename}",
            "sha256": digest,
            "byte_size": len(payload),
            "content_type": "image/jpeg",
            "image_source_kind": "og_image",
        }
        audit = brief.image_audit_manifest(
            [publication_article], publication_assets=[asset_record]
        )
        image_manifest = {
            "version": 1,
            "edition_type": "daily",
            "edition_key": "2026-08-11",
            **audit,
        }
        image_manifest_bytes = (
            json.dumps(image_manifest, ensure_ascii=False, indent=2) + "\n"
        ).encode()
        (asset_dir / "image-manifest.json").write_bytes(image_manifest_bytes)
        expected_src = f"assets/2026-08-11/{filename}"
        html = (
            '<main data-edition-key="2026-08-11">'
            f'<img src="{expected_src}"></main>'
        )
        dated = publication_dir / "2026-08-11.html"
        latest = publication_dir / "latest.html"
        dated.write_text(html, encoding="utf-8")
        latest.write_text(html, encoding="utf-8")
        valid_manifest = {
            "edition_type": "daily",
            "edition_key": "2026-08-11",
            "dated_path": str(dated),
            "latest_path": str(latest),
            "html_sha256": hashlib.sha256(html.encode()).hexdigest(),
            "production_image_gate_required": True,
            "image_manifest_path": (
                "docs/editorial/daily/assets/2026-08-11/image-manifest.json"
            ),
            "image_manifest_sha256": hashlib.sha256(image_manifest_bytes).hexdigest(),
            **audit,
        }
        original_root = runner.ROOT
        original_paths = runner._docs_paths
        runner.ROOT = repo_root
        runner._docs_paths = lambda _kind, _key: (dated, latest)
        try:
            runner._verify_local_publication(valid_manifest)
            hotlink_html = (
                '<main data-edition-key="2026-08-11">'
                '<img src="https://publisher.example/photo.jpg"></main>'
            )
            dated.write_text(hotlink_html, encoding="utf-8")
            latest.write_text(hotlink_html, encoding="utf-8")
            hotlink_manifest = {
                **valid_manifest,
                "html_sha256": hashlib.sha256(hotlink_html.encode()).hexdigest(),
            }
            hotlink_failed = False
            try:
                runner._verify_local_publication(hotlink_manifest)
            except runner.OrchestratorError:
                hotlink_failed = True
            if not hotlink_failed:
                raise AssertionError("remote rendered src passed publication verifier")
        finally:
            runner.ROOT = original_root
            runner._docs_paths = original_paths
    print("PUBLIC_IMAGE_ASSET_VERIFICATION=PASS")
    print("PRODUCTION_IMAGE_GATE=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
