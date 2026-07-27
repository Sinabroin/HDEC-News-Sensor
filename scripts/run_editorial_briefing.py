#!/usr/bin/env python3
"""Common orchestrator for Daily and Weekly editorial briefings.

Preview is fully offline and writes outside the repository. Production is split
into ``--publish`` (collect/render/write dated+latest) and ``--send`` (verify the
dated public page, send one link-only message, then persist exact-250 state) so
the workflow can commit/push the publication between those phases.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from email.message import EmailMessage
from email.utils import parseaddr
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
for _path in (ROOT, SCRIPTS):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from app import collector, editorial_briefing_state, editorial_briefings  # noqa: E402
from app.editorial_briefings import EditorialError, KST  # noqa: E402

RUNTIME_MANIFEST = "runtime-manifest.json"
PUBLICATION_TIMEOUT_SECONDS = 300
PUBLICATION_INTERVAL_SECONDS = 10


class OrchestratorError(RuntimeError):
    """A production precondition failed; no later side effect is allowed."""


def _now() -> datetime:
    return datetime.now(KST)


def _parse_run_at(value: str | None) -> datetime:
    if not value:
        return _now()
    raw = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        raw += "T07:30:00+09:00"
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise OrchestratorError("--run-at must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise OrchestratorError("--run-at must include timezone")
    return parsed.astimezone(KST)


def _github_output(name: str, value: str) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT", "").strip()
    if not output_path:
        return
    with Path(output_path).open("a", encoding="utf-8") as handle:
        handle.write(f"{name}={value}\n")


def _safe_address(value: str) -> str:
    candidate = str(value or "").strip()
    if not candidate or "\r" in candidate or "\n" in candidate:
        return ""
    _display, parsed = parseaddr(candidate)
    if parsed != candidate or parsed.count("@") != 1:
        return ""
    local, domain = parsed.rsplit("@", 1)
    if not local or "." not in domain or domain.startswith(".") or domain.endswith("."):
        return ""
    return parsed


def _production_credentials() -> tuple[str, str, str, str]:
    smtp_user = _safe_address(os.environ.get("GMAIL_SMTP_USER", ""))
    password = (
        os.environ.get("GMAIL_SMTP_APP_PASSWORD", "").strip()
        or os.environ.get("GMAIL_SMTP_PASSWORD", "").strip()
    )
    from_address = _safe_address(os.environ.get("ALERT_EMAIL_FROM", "")) or smtp_user
    recipient = _safe_address(os.environ.get("TEAMS_CHANNEL_EMAIL", ""))
    if not smtp_user or not password or not from_address or not recipient:
        raise OrchestratorError("mail transport configuration is incomplete")
    return smtp_user, password, from_address, recipient


def _require_production_gate() -> None:
    if os.environ.get("EDITORIAL_PRODUCTION", "").strip() != "1":
        raise OrchestratorError("production gate is closed")
    ref = os.environ.get("GITHUB_REF", "").strip()
    if ref != "refs/heads/main":
        raise OrchestratorError("production is main-only")


def collect_live_articles() -> list[dict]:
    """Collect real metadata from the established providers without DB writes."""
    from app import live_collector, naver_news_provider

    google_rows = live_collector.fetch_all()
    naver_result = naver_news_provider.fetch()
    naver_rows = list(naver_result.get("articles") or [])
    resolvable = list(google_rows) + naver_rows
    if resolvable:
        live_collector.resolve_publisher_urls(resolvable)
    combined = collector.merge_provider_articles(resolvable)
    if not combined:
        raise OrchestratorError("live collection returned no articles; fail closed")
    return combined


def _runtime_dir(value: str | None, edition_type: str) -> Path:
    raw = value or os.environ.get("EDITORIAL_RUNTIME_DIR", "")
    if not raw:
        raise OrchestratorError("--runtime-dir or EDITORIAL_RUNTIME_DIR is required")
    path = Path(raw).resolve()
    if path == ROOT or ROOT in path.parents:
        raise OrchestratorError("runtime directory must be outside repository")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _docs_paths(edition_type: str, key: str) -> tuple[Path, Path]:
    directory = ROOT / "docs" / "editorial" / edition_type
    return directory / f"{key}.html", directory / "latest.html"


def _write_runtime_manifest(runtime_dir: Path, manifest: dict) -> Path:
    target = runtime_dir / RUNTIME_MANIFEST
    editorial_briefings.atomic_write_bytes(
        target,
        (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )
    return target


def _load_runtime_manifest(runtime_dir: Path, edition_type: str) -> dict:
    path = runtime_dir / RUNTIME_MANIFEST
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise OrchestratorError("runtime manifest missing or malformed") from exc
    required = {
        "version", "edition_type", "edition_key", "coverage_start", "coverage_end",
        "html_sha256", "public_dated_url", "public_latest_url", "dated_path",
        "latest_path", "teams_text", "teams_html", "headline", "issue_mode",
        "article_count",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise OrchestratorError("runtime manifest fields mismatch")
    if value["version"] != 1 or value["edition_type"] != edition_type:
        raise OrchestratorError("runtime manifest identity mismatch")
    return value


def run_preview(
    edition_type: str,
    *,
    run_at: datetime,
    preview_root: Path,
    fixture_root: str,
    fixture_profile: str,
) -> dict:
    if edition_type == "daily":
        output_dir = preview_root / "daily"
        profile = "dominant"
    else:
        profile = fixture_profile
        output_dir = preview_root / "weekly" / (
            "dominant" if profile == "dominant" else "multi-issue"
        )
    raw_articles = editorial_briefings.fixture_articles(
        edition_type, run_at, profile=profile
    )
    edition = editorial_briefings.render_edition(
        edition_type, raw_articles, run_at=run_at, root_url=fixture_root
    )
    editorial_briefings.validate_rendered(edition)
    manifest = editorial_briefings.write_preview_bundle(edition, output_dir)
    print(
        f"preview_ok edition_type={edition_type} edition={edition.edition_key} "
        "network_sends=0 smtp_attempts=0 state_reads=0 state_writes=0 "
        "docs_writes=0 git_writes=0"
    )
    print(f"preview_path={output_dir}")
    return manifest


def run_publish(
    edition_type: str,
    *,
    run_at: datetime,
    runtime_dir: Path,
    collect: Callable[[], list[dict]] = collect_live_articles,
) -> dict | None:
    _require_production_gate()
    key = editorial_briefings.edition_key(edition_type, run_at)
    state = editorial_briefing_state.load_state(edition_type)
    if editorial_briefing_state.has_success(state, key):
        _github_output("skipped", "true")
        _github_output("edition", key)
        print(f"publish_skip edition_type={edition_type} edition={key} reason=already_successful")
        return None
    root_url = editorial_briefings.derive_public_root(os.environ.get("REPORT_URL", ""))
    edition = editorial_briefings.render_edition(
        edition_type, collect(), run_at=run_at, root_url=root_url
    )
    editorial_briefings.validate_rendered(edition)
    dated_path, latest_path = _docs_paths(edition_type, edition.edition_key)
    payload = edition.html.encode("utf-8")
    editorial_briefings.atomic_write_bytes(dated_path, payload)
    editorial_briefings.atomic_write_bytes(latest_path, payload)
    if dated_path.read_bytes() != latest_path.read_bytes():
        raise OrchestratorError("dated/latest bytes differ after publication write")
    manifest = editorial_briefings.manifest_for_runtime(edition, dated_path, latest_path)
    _write_runtime_manifest(runtime_dir, manifest)
    _github_output("skipped", "false")
    _github_output("edition", edition.edition_key)
    _github_output("dated_path", dated_path.relative_to(ROOT).as_posix())
    _github_output("latest_path", latest_path.relative_to(ROOT).as_posix())
    print(
        f"publish_ready edition_type={edition_type} edition={edition.edition_key} "
        f"articles={edition.article_count}"
    )
    return manifest


def _response_status(response: object) -> int:
    status = getattr(response, "status", None)
    if status is None and hasattr(response, "getcode"):
        status = response.getcode()
    try:
        return int(status)
    except (TypeError, ValueError) as exc:
        raise OrchestratorError("public page returned no HTTP status") from exc


def verify_public_page_once(
    url: str,
    expected_edition: str,
    *,
    opener: Callable | None = None,
) -> bool:
    if not editorial_briefings.valid_http_url(url):
        return False
    open_url = opener or urllib.request.urlopen
    try:
        response = open_url(url, timeout=15)
        with response:
            if _response_status(response) != 200:
                return False
            body = response.read(2_000_000).decode("utf-8", errors="replace")
    except (OSError, TimeoutError, urllib.error.URLError, ValueError):
        return False
    expected_meta = f'<meta name="editorial-edition" content="{expected_edition}">'
    expected_data = f'data-edition-key="{expected_edition}"'
    return expected_meta in body and expected_data in body


def poll_public_dated_page(
    url: str,
    expected_edition: str,
    *,
    timeout_seconds: int = PUBLICATION_TIMEOUT_SECONDS,
    interval_seconds: int = PUBLICATION_INTERVAL_SECONDS,
    opener: Callable | None = None,
    sleeper: Callable[[float], None] = time.sleep,
) -> bool:
    if timeout_seconds < 0 or interval_seconds <= 0:
        raise OrchestratorError("invalid polling configuration")
    deadline = time.monotonic() + timeout_seconds
    while True:
        if verify_public_page_once(url, expected_edition, opener=opener):
            return True
        if time.monotonic() >= deadline:
            return False
        sleeper(min(interval_seconds, max(0.0, deadline - time.monotonic())))


def build_link_message(manifest: dict, from_address: str, recipient: str) -> EmailMessage:
    message = EmailMessage()
    prefix = "[HDEC AI Daily Brief]" if manifest["edition_type"] == "daily" else "[AI 경영 T&I]"
    message["Subject"] = f"{prefix} {manifest['edition_key']}"
    message["From"] = from_address
    message["To"] = recipient
    message.set_content(manifest["teams_text"])
    message.add_alternative(manifest["teams_html"], subtype="html")
    if list(message.iter_attachments()):
        raise OrchestratorError("attachments are forbidden")
    return message


def _verify_local_publication(manifest: dict) -> None:
    dated = Path(manifest["dated_path"]).resolve()
    latest = Path(manifest["latest_path"]).resolve()
    expected_dated, expected_latest = _docs_paths(
        manifest["edition_type"], manifest["edition_key"]
    )
    if dated != expected_dated.resolve() or latest != expected_latest.resolve():
        raise OrchestratorError("runtime publication paths mismatch")
    try:
        dated_bytes = dated.read_bytes()
        latest_bytes = latest.read_bytes()
    except OSError as exc:
        raise OrchestratorError("local publication is missing") from exc
    if dated_bytes != latest_bytes:
        raise OrchestratorError("dated/latest local bytes differ")
    import hashlib

    if hashlib.sha256(dated_bytes).hexdigest() != manifest["html_sha256"]:
        raise OrchestratorError("local publication hash mismatch")
    text = dated_bytes.decode("utf-8")
    if f'data-edition-key="{manifest["edition_key"]}"' not in text:
        raise OrchestratorError("local publication edition marker mismatch")


def persist_exact_250_success(
    edition_type: str,
    manifest: dict,
    *,
    smtp_status: str,
    smtp_code: int | None,
    sent_at: datetime,
    path: Path | None = None,
) -> dict:
    if smtp_status != "accepted" or smtp_code != 250:
        raise OrchestratorError("state requires exact SMTP DATA 250")
    state = editorial_briefing_state.load_state(edition_type, path=path)
    updated = editorial_briefing_state.add_success(
        state,
        edition_type,
        {
            "edition_key": manifest["edition_key"],
            "coverage_start": manifest["coverage_start"],
            "coverage_end": manifest["coverage_end"],
            "html_sha256": manifest["html_sha256"],
            "public_url": manifest["public_dated_url"],
            "smtp_status": smtp_status,
            "smtp_code": smtp_code,
            "sent_at": sent_at.astimezone(KST).isoformat(timespec="seconds"),
        },
    )
    editorial_briefing_state.atomic_write_state(edition_type, updated, path=path)
    return updated


def run_send(
    edition_type: str,
    *,
    run_at: datetime,
    runtime_dir: Path,
    smtp_factory=None,
    opener: Callable | None = None,
) -> dict | None:
    _require_production_gate()
    key = editorial_briefings.edition_key(edition_type, run_at)
    state = editorial_briefing_state.load_state(edition_type)
    if editorial_briefing_state.has_success(state, key):
        _github_output("skipped", "true")
        print(f"send_skip edition_type={edition_type} edition={key} reason=already_successful")
        return None
    manifest = _load_runtime_manifest(runtime_dir, edition_type)
    if manifest["edition_key"] != key:
        raise OrchestratorError("runtime edition does not match current catch-up edition")
    _verify_local_publication(manifest)
    root_url = editorial_briefings.derive_public_root(os.environ.get("REPORT_URL", ""))
    dated_url, latest_url = editorial_briefings.public_urls(root_url, edition_type, key)
    if manifest["public_dated_url"] != dated_url or manifest["public_latest_url"] != latest_url:
        raise OrchestratorError("runtime public URL mismatch")
    if not poll_public_dated_page(dated_url, key, opener=opener):
        raise OrchestratorError("dated public page did not reach matching HTTP 200 state")

    smtp_user, password, from_address, recipient = _production_credentials()
    message = build_link_message(manifest, from_address, recipient)
    from send_email_alert import DeliveryTarget, deliver_email_message

    result = deliver_email_message(
        message,
        DeliveryTarget("editorial_teams_channel", recipient, "teams_channel"),
        smtp_user,
        password,
        from_address,
        smtp_factory=smtp_factory,
    )
    if result.smtp_status != "accepted" or result.smtp_code != 250:
        raise OrchestratorError(
            f"mail delivery rejected: status={result.smtp_status} "
            f"code={result.smtp_code if result.smtp_code is not None else 'none'}"
        )
    updated = persist_exact_250_success(
        edition_type,
        manifest,
        smtp_status=result.smtp_status,
        smtp_code=result.smtp_code,
        sent_at=_now(),
    )
    state_target = editorial_briefing_state.state_path(edition_type)
    _github_output("state_changed", "true")
    _github_output("state_path", state_target.relative_to(ROOT).as_posix())
    print(
        f"send_ok edition_type={edition_type} edition={key} "
        "smtp_status=accepted smtp_code=250 state_changed=true"
    )
    return updated


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--edition-type", choices=("daily", "weekly"), required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preview", action="store_true")
    mode.add_argument("--publish", action="store_true")
    mode.add_argument("--send", action="store_true")
    parser.add_argument("--run-at", default="")
    parser.add_argument("--runtime-dir", default="")
    parser.add_argument("--preview-root", default="/tmp/d7ak6e-preview")
    parser.add_argument(
        "--fixture-root", default="https://preview.fixture.test/HDEC-News-Sensor"
    )
    parser.add_argument("--fixture-profile", choices=("dominant", "multi"), default="dominant")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        run_at = _parse_run_at(args.run_at)
        if args.preview:
            run_preview(
                args.edition_type,
                run_at=run_at,
                preview_root=Path(args.preview_root).resolve(),
                fixture_root=args.fixture_root,
                fixture_profile=args.fixture_profile,
            )
        elif args.publish:
            run_publish(
                args.edition_type,
                run_at=run_at,
                runtime_dir=_runtime_dir(args.runtime_dir, args.edition_type),
            )
        else:
            run_send(
                args.edition_type,
                run_at=run_at,
                runtime_dir=_runtime_dir(args.runtime_dir, args.edition_type),
            )
    except (
        EditorialError,
        editorial_briefing_state.StateError,
        OrchestratorError,
        OSError,
    ) as exc:
        print(f"ERROR: editorial briefing failed closed ({type(exc).__name__})", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
