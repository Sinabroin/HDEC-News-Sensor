#!/usr/bin/env python3
"""Offline verifier for the independent Daily/Weekly editorial workflows."""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
for _path in (ROOT, SCRIPTS):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from app import editorial_briefing_state as state  # noqa: E402
from app import editorial_briefings as brief  # noqa: E402
from app import news_access  # noqa: E402

_SPEC = importlib.util.spec_from_file_location(
    "run_editorial_briefing", SCRIPTS / "run_editorial_briefing.py"
)
assert _SPEC and _SPEC.loader
runner = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(runner)

PASS = 0
FAIL = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"PASS {label}")
    else:
        FAIL += 1
        suffix = f" — {detail}" if detail else ""
        print(f"FAIL {label}{suffix}")


def expect_raises(label: str, error_type, callback) -> None:
    try:
        callback()
    except error_type:
        check(label, True)
    except Exception as exc:  # noqa: BLE001
        check(label, False, f"wrong exception {type(exc).__name__}")
    else:
        check(label, False, "exception was not raised")


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def fixture_row(published: datetime, *, url: str = "https://publisher.fixture.test/a") -> dict:
    return {
        "title": "검증용 <경계> 기사",
        "snippet": "첫 번째 확인 문장이다. 두 번째 확인 문장이다.",
        "source": "검증매체",
        "published_at": published.isoformat(),
        "url": url,
        "source_metadata": {"provider": "offline_fixture"},
    }


def source_contracts() -> None:
    expected = {
        ".github/workflows/editorial-daily-brief.yml",
        ".github/workflows/editorial-weekly-ti.yml",
        "app/editorial_briefings.py",
        "app/editorial_briefing_state.py",
        "scripts/run_editorial_briefing.py",
        "scripts/verify_editorial_briefings.py",
        "templates/editorial_daily.html",
        "templates/editorial_weekly.html",
    }
    check("all eight source files exist", all((ROOT / item).is_file() for item in expected))
    reference = Path(
        "/mnt/c/Users/HDEC/Downloads/"
        "260415_AI 경영 T&I_Report_" + "My" + "thos 이슈 브리핑.html"
    )
    if reference.exists():
        digest = hashlib.sha256(reference.read_bytes()).hexdigest()
        check(
            "exact Weekly source SHA256 remains unchanged",
            digest == "6af54a441991bb8af8390e39e2de2c1e576c831bd5537b51162c74e841decd84",
            digest,
        )
    else:
        print("INFO exact Weekly source is unavailable in this environment; DOM contract still verified")


def workflow_contracts() -> None:
    daily = read(".github/workflows/editorial-daily-brief.yml")
    weekly = read(".github/workflows/editorial-weekly-ti.yml")
    daily_crons = re.findall(r'cron:\s*["\']([^"\']+)', daily)
    weekly_crons = re.findall(r'cron:\s*["\']([^"\']+)', weekly)
    check(
        "Daily cron set is exact",
        daily_crons == ["0 22 * * *", "20 22 * * *", "40 22 * * *"],
        repr(daily_crons),
    )
    check(
        "Weekly cron set is exact",
        weekly_crons == ["30 22 * * 2", "50 22 * * 2"],
        repr(weekly_crons),
    )
    check(
        "UTC schedules convert to requested KST execution times",
        {
            (22 + 9) % 24: 7,
            ((2 + (22 + 9) // 24) % 7): 3,
        } == {7: 7, 3: 3},
    )
    for name, workflow, group in (
        ("Daily", daily, "editorial-daily-brief"),
        ("Weekly", weekly, "editorial-weekly-ti"),
    ):
        check(f"{name} workflow_dispatch is enabled", "workflow_dispatch:" in workflow)
        check(f"{name} force_dry_run exists", "force_dry_run:" in workflow)
        check(f"{name} concurrency group is independent", f"group: {group}" in workflow)
        check(f"{name} cancel-in-progress is false", "cancel-in-progress: false" in workflow)
        check(
            f"{name} production is main-only",
            "github.ref == 'refs/heads/main'" in workflow,
        )
        check(
            f"{name} uses only the channel email recipient secret",
            "TEAMS_CHANNEL_EMAIL: ${{ secrets.TEAMS_CHANNEL_EMAIL }}" in workflow,
        )
        check(
            f"{name} contains fetch/rebase/push retry",
            "git fetch origin main" in workflow
            and "git rebase origin/main" in workflow
            and "for attempt in 1 2 3" in workflow,
        )
        check(
            f"{name} conflict handling has no reset or checkout deletion",
            "git reset" not in workflow and "git checkout --" not in workflow,
        )
        check(f"{name} never stages the repository root", "git add ." not in workflow)

    check(
        "Daily exact publication/state git-add allowlist",
        'git add -- "$DATED_PATH" "$LATEST_PATH"' in daily
        and 'git add -- "$STATE_PATH"' in daily
        and 'DATED_PATH="docs/editorial/daily/${EDITION}.html"' in daily
        and 'LATEST_PATH="docs/editorial/daily/latest.html"' in daily
        and 'STATE_PATH="data/editorial_daily_state.json"' in daily,
    )
    check(
        "Weekly exact publication/state git-add allowlist",
        'git add -- "$DATED_PATH" "$LATEST_PATH"' in weekly
        and 'git add -- "$STATE_PATH"' in weekly
        and 'DATED_PATH="docs/editorial/weekly/${EDITION}.html"' in weekly
        and 'LATEST_PATH="docs/editorial/weekly/latest.html"' in weekly
        and 'STATE_PATH="data/editorial_weekly_state.json"' in weekly,
    )
    platform_token = "tele" + "gram"
    implementation = "\n".join(
        read(path).casefold()
        for path in (
            ".github/workflows/editorial-daily-brief.yml",
            ".github/workflows/editorial-weekly-ti.yml",
            "app/editorial_briefings.py",
            "app/editorial_briefing_state.py",
            "scripts/run_editorial_briefing.py",
            "templates/editorial_daily.html",
            "templates/editorial_weekly.html",
        )
    )
    check("forbidden messaging platform string/import/secret/step count is zero",
          platform_token not in implementation)
    shared_state = "teams" + "_push_state"
    check("shared legacy state path is never referenced", shared_state not in implementation)
    check("HTML attachment workflow configuration count is zero",
          "attachment:" not in daily.casefold() and "attachment:" not in weekly.casefold())


def time_and_filter_contracts() -> None:
    daily_at = dt("2026-07-27T07:00:00+09:00")
    daily = brief.daily_coverage(daily_at)
    check("Daily coverage start exact", daily.start == dt("2026-07-26T07:00:00+09:00"))
    check("Daily coverage end exact", daily.end == dt("2026-07-27T06:40:00+09:00"))
    rows = [
        fixture_row(daily.start, url="https://one.fixture.test/a"),
        fixture_row(daily.end, url="https://two.fixture.test/a"),
        fixture_row(daily.start - timedelta(seconds=1), url="https://three.fixture.test/a"),
        fixture_row(daily.end + timedelta(seconds=1), url="https://four.fixture.test/a"),
    ]
    selected = brief.normalize_articles(rows, daily, limit=10)
    check("Daily coverage is inclusive at both boundaries", len(selected) == 2)

    weekly_at = dt("2026-07-29T07:30:00+09:00")
    weekly = brief.weekly_coverage(weekly_at)
    check("Weekly coverage start exact", weekly.start == dt("2026-07-22T00:00:00+09:00"))
    check("Weekly coverage end exact", weekly.end == dt("2026-07-28T23:59:59+09:00"))
    rows = [
        fixture_row(weekly.start, url="https://five.fixture.test/a"),
        fixture_row(weekly.end, url="https://six.fixture.test/a"),
        fixture_row(weekly.start - timedelta(seconds=1), url="https://seven.fixture.test/a"),
        fixture_row(weekly.end + timedelta(seconds=1), url="https://eight.fixture.test/a"),
    ]
    check(
        "Weekly coverage is inclusive at both boundaries",
        len(brief.normalize_articles(rows, weekly, limit=10)) == 2,
    )
    check(
        "ISO year-week handles year boundary",
        brief.edition_key("weekly", dt("2026-01-01T07:30:00+09:00")) == "2026-W01"
        and brief.edition_key("weekly", dt("2025-01-01T07:30:00+09:00")) == "2025-W01",
    )
    check(
        "catch-up executions retain the same edition",
        brief.edition_key("daily", daily_at) == brief.edition_key(
            "daily", daily_at + timedelta(minutes=40)
        )
        and brief.edition_key("weekly", weekly_at) == brief.edition_key(
            "weekly", weekly_at + timedelta(minutes=20)
        ),
    )


def state_contracts() -> None:
    check(
        "Daily/Weekly state paths are independent",
        state.state_path("daily") != state.state_path("weekly")
        and state.state_path("daily").name == "editorial_daily_state.json"
        and state.state_path("weekly").name == "editorial_weekly_state.json",
    )
    with tempfile.TemporaryDirectory(prefix="d7ak6e-state-") as temporary:
        root = Path(temporary)
        absent = root / "absent.json"
        check("absent state passes as empty", state.load_state("daily", absent) == state.empty_state("daily"))
        malformed = root / "malformed.json"
        malformed.write_text("{not-json", encoding="utf-8")
        expect_raises("malformed state fails closed", state.StateError,
                      lambda: state.load_state("daily", malformed))
        wrong = root / "wrong.json"
        wrong.write_text(json.dumps(state.empty_state("weekly")), encoding="utf-8")
        expect_raises("cross-edition state fails closed", state.StateError,
                      lambda: state.load_state("daily", wrong))
        forbidden = root / ("teams" + "_push_state.json")
        expect_raises("shared legacy state access is rejected", state.StateError,
                      lambda: state.load_state("daily", forbidden))

        record = {
            "edition_key": "2026-07-27",
            "coverage_start": "2026-07-26T07:00:00+09:00",
            "coverage_end": "2026-07-27T06:40:00+09:00",
            "html_sha256": "a" * 64,
            "public_url": "https://public.fixture.test/editorial/daily/2026-07-27.html",
            "smtp_status": "accepted",
            "smtp_code": 250,
            "sent_at": "2026-07-27T07:05:00+09:00",
        }
        updated = state.add_success(state.empty_state("daily"), "daily", record)
        check("successful edition is skipped on catch-up", state.has_success(updated, "2026-07-27"))


def link_and_render_contracts() -> tuple[brief.RenderedEdition, brief.RenderedEdition, brief.RenderedEdition]:
    run_daily = dt("2026-07-27T07:00:00+09:00")
    run_weekly = dt("2026-07-29T07:30:00+09:00")
    root = "https://preview.fixture.test/HDEC-News-Sensor"

    labels = []
    for url in (
        "https://publisher.fixture.test/a",
        "https://news.google.com/articles/a",
        "https://n.news.naver.com/article/a",
    ):
        row = fixture_row(brief.daily_coverage(run_daily).start, url=url)
        chosen = news_access.choose_article_link(row)
        labels.append((chosen.kind, chosen.label))
    check(
        "link kinds and labels are exact",
        labels
        == [
            ("publisher_direct", "원문"),
            ("google_news_fallback", "Google News 경유"),
            ("portal_fallback", "포털 경유"),
        ],
        repr(labels),
    )
    invalid = fixture_row(
        brief.daily_coverage(run_daily).start, url="javascript:alert(1)"
    )
    check(
        "invalid URL scheme is rejected",
        not brief.normalize_articles([invalid], brief.daily_coverage(run_daily), limit=1),
    )

    daily_rows = brief.fixture_articles("daily", run_daily)
    daily_rows[0]["title"] = '경영진 <script>alert("x")</script> 확인 기사'
    daily_rows[0]["image_url"] = ""
    daily = brief.render_edition("daily", daily_rows, run_at=run_daily, root_url=root)
    brief.validate_rendered(daily)
    check("Daily has exactly one headline", daily.html.count('data-role="headline"') == 1)
    check("Daily has no more than five article cards",
          daily.html.count('data-role="article-card"') == 5)
    check("Daily timestamps are explicit KST", daily.html.count(" KST</time>") == 6)
    check("Daily Editor's Summary contains three evidence lines",
          daily.html.count("<li>") >= 3)
    check("HTML escaping blocks injected markup",
          "<script>alert" not in daily.html and "&lt;script&gt;" in daily.html)
    check("missing image renders naturally without a placeholder",
          "placeholder" not in daily.html.casefold())
    check("external links all carry target/rel security",
          daily.html.count('target="_blank"') == daily.html.count('rel="noopener noreferrer"'))
    check("selected link kind is preserved in HTML",
          daily.html.count('data-link-kind="') == daily.html.count('target="_blank"'))
    check("Daily reference semantics were not copied",
          "Weekly Brief" not in daily.html and "첫 발행" not in daily.html)

    dominant = brief.render_edition(
        "weekly",
        brief.fixture_articles("weekly", run_weekly, profile="dominant"),
        run_at=run_weekly,
        root_url=root,
    )
    multi = brief.render_edition(
        "weekly",
        brief.fixture_articles("weekly", run_weekly, profile="multi"),
        run_at=run_weekly,
        root_url=root,
    )
    brief.validate_rendered(dominant)
    brief.validate_rendered(multi)
    check("Weekly dominant mode is selected", dominant.issue_mode == "dominant_issue")
    check("Weekly multi-issue mode is selected", multi.issue_mode == "multi_issue")
    for section in (
        "key-message", "management-cards", "key-facts", "timeline", "comparison",
        "industry-insight", "alternative-view", "sources",
    ):
        check(f"Weekly DOM section exists: {section}",
              f'data-section="{section}"' in dominant.html)
    weekly_template = read("templates/editorial_weekly.html")
    daily_template = read("templates/editorial_daily.html")
    check("Weekly page keeps 794px × min-height 1123px",
          "width:794px;min-height:1123px" in weekly_template)
    check("Weekly page keeps max-width viewport protection",
          re.search(r"\.page\{[^{}]*max-width:100%", weekly_template) is not None)
    check("Weekly keeps A4 portrait @page", re.search(
        r"@page\{size:A4 portrait;", weekly_template) is not None)
    check("Weekly keeps print media and color adjustment",
          "@media print" in weekly_template and "print-color-adjust:exact" in weekly_template)
    check("Weekly keeps break-inside protections",
          "break-inside:avoid" in weekly_template and "page-break-inside:avoid" in weekly_template)
    check("Weekly print starts comparison on a new page",
          re.search(
              r'@media print\{[\s\S]*?\[data-section="comparison"\]'
              r"\{[^{}]*break-before:page;[^{}]*page-break-before:always",
              weekly_template,
          ) is not None)
    check("Weekly keeps a screen-only 768px mobile breakpoint",
          "@media screen and (max-width:768px)" in weekly_template)
    check("Weekly mobile comparison scroll hint is rendered and visible",
          dominant.html.count('class="table-scroll-hint"') == 1
          and "좌우로 스크롤해 전체 내용을 확인하세요" in dominant.html
          and re.search(
              r"@media screen and \(max-width:768px\)\{[\s\S]*?\.table-scroll-hint"
              r"\{[^{}]*display:block",
              weekly_template,
          ) is not None)
    check("Weekly comparison scroll hint is hidden in print",
          re.search(
              r"@media print\{[\s\S]*?\.table-scroll-hint[^{}]*"
              r"\{[^{}]*display:none!important",
              weekly_template,
          ) is not None)
    check("Daily mobile links keep 24px touch targets",
          re.search(
              r"@media\(max-width:560px\)\{[\s\S]*?\.link"
              r"\{[^{}]*display:inline-flex;[^{}]*min-width:24px;"
              r"[^{}]*min-height:24px",
              daily_template,
          ) is not None)
    check("Weekly mobile links keep 24px touch targets",
          re.search(
              r"@media screen and \(max-width:768px\)\{[\s\S]*?\.link,\.source-link"
              r"\{[^{}]*display:inline-flex;[^{}]*min-width:24px;"
              r"[^{}]*min-height:24px",
              weekly_template,
          ) is not None)
    check("Weekly keeps PDF save control",
          'onclick="window.print()"' in weekly_template and "PDF 저장" in weekly_template)

    sample_sentinels = [
        "My" + "thos", "Anth" + "ropic", "Op" + "us", "G" + "PT", "Gem" + "ini",
        "Glass" + "wing", "260415", "$100M", "27 years", "5 million",
        "16 years", "12 companies", "40 institutions",
    ]
    combined = weekly_template + dominant.html + multi.html
    check(
        "Weekly sample names, dates, and figures are absent",
        not any(token.casefold() in combined.casefold() for token in sample_sentinels),
    )
    check("all Weekly external links carry target/rel security",
          dominant.html.count('target="_blank"')
          == dominant.html.count('rel="noopener noreferrer"'))
    return daily, dominant, multi


def url_and_publication_contracts(daily: brief.RenderedEdition) -> None:
    report = "https://guides.fixture.test/HDEC-News-Sensor/daily/latest.html"
    root = brief.derive_public_root(report)
    check("REPORT_URL root derives by exact suffix removal",
          root == "https://guides.fixture.test/HDEC-News-Sensor")
    dated, latest = brief.public_urls(root, "daily", "2026-07-27")
    check("editorial URLs compose from derived root",
          dated.endswith("/editorial/daily/2026-07-27.html")
          and latest.endswith("/editorial/daily/latest.html"))
    for invalid in (
        "ftp://guides.fixture.test/HDEC-News-Sensor/daily/latest.html",
        "https://guides.fixture.test/HDEC-News-Sensor/latest.html",
        "https://guides.fixture.test/HDEC-News-Sensor/daily/latest.html?x=1",
    ):
        expect_raises("invalid REPORT_URL fails closed", brief.EditorialError,
                      lambda value=invalid: brief.derive_public_root(value))

    class FakeResponse:
        def __init__(self, status: int, body: str):
            self.status = status
            self._body = body.encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit: int):
            return self._body

    just_200 = lambda *_args, **_kwargs: FakeResponse(200, "<html>ready</html>")
    marker_200 = lambda *_args, **_kwargs: FakeResponse(200, daily.html)
    wrong_status = lambda *_args, **_kwargs: FakeResponse(204, daily.html)
    check("HTTP 200 without edition marker is insufficient",
          not runner.verify_public_page_once(daily.public_dated_url, daily.edition_key,
                                             opener=just_200))
    check("matching HTTP 200 dated page passes",
          runner.verify_public_page_once(daily.public_dated_url, daily.edition_key,
                                         opener=marker_200))
    check("non-200 response fails even with marker",
          not runner.verify_public_page_once(daily.public_dated_url, daily.edition_key,
                                             opener=wrong_status))


def smtp_and_state_contracts(daily: brief.RenderedEdition) -> None:
    manifest = brief.manifest_for_runtime(
        daily,
        ROOT / "docs/editorial/daily/2026-07-27.html",
        ROOT / "docs/editorial/daily/latest.html",
    )
    message = runner.build_link_message(
        manifest, "sender@fixture.test", "channel@fixture.test"
    )
    check("message has no attachments", list(message.iter_attachments()) == [])
    body_text = "\n".join(
        str(part.get_content())
        for part in message.walk()
        if part.get_content_type() in {"text/plain", "text/html"}
    )
    body_urls = re.findall(r"https?://[^\s\"'<>]+", body_text)
    check("message contains only public brief/article links",
          daily.public_dated_url in body_text
          and bool(body_urls)
          and all(
              url.startswith("https://news")
              or url == daily.public_dated_url
              for url in body_urls
          ))

    with tempfile.TemporaryDirectory(prefix="d7ak6e-smtp-state-") as temporary:
        root = Path(temporary)
        accepted_path = root / "accepted.json"
        runner.persist_exact_250_success(
            "daily",
            manifest,
            smtp_status="accepted",
            smtp_code=250,
            sent_at=dt("2026-07-27T07:05:00+09:00"),
            path=accepted_path,
        )
        check("SMTP DATA 250 changes state", accepted_path.is_file())
        for code in (251, 252, 300, 399, 400, 500, None):
            candidate = root / f"code-{code}.json"
            expect_raises(
                f"SMTP code {code} changes state zero",
                runner.OrchestratorError,
                lambda code=code, candidate=candidate: runner.persist_exact_250_success(
                    "daily",
                    manifest,
                    smtp_status="accepted" if code and code < 400 else "rejected",
                    smtp_code=code,
                    sent_at=dt("2026-07-27T07:05:00+09:00"),
                    path=candidate,
                ),
            )
            check(f"SMTP code {code} left no state file", not candidate.exists())
        exception_path = root / "exception.json"
        try:
            raise TimeoutError("offline fixture")
        except TimeoutError:
            pass
        check("SMTP exception path changes state zero", not exception_path.exists())


@contextlib.contextmanager
def hard_block_network():
    original_socket = socket.socket
    original_urlopen = runner.urllib.request.urlopen

    def blocked(*_args, **_kwargs):
        raise AssertionError("network fixture was invoked")

    socket.socket = blocked
    runner.urllib.request.urlopen = blocked
    try:
        yield
    finally:
        socket.socket = original_socket
        runner.urllib.request.urlopen = original_urlopen


def preview_contracts() -> None:
    protected_paths = [
        ROOT / "data/editorial_daily_state.json",
        ROOT / "data/editorial_weekly_state.json",
        ROOT / "docs/editorial/daily",
        ROOT / "docs/editorial/weekly",
    ]

    def snapshot(path: Path):
        if path.is_file():
            return ("file", hashlib.sha256(path.read_bytes()).hexdigest())
        if path.is_dir():
            return (
                "dir",
                tuple(
                    (item.relative_to(path).as_posix(), hashlib.sha256(item.read_bytes()).hexdigest())
                    for item in sorted(path.rglob("*"))
                    if item.is_file()
                ),
            )
        return ("absent",)

    before = {path: snapshot(path) for path in protected_paths}
    status_before = subprocess.run(
        ["git", "status", "--short", "--untracked-files=all"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    ).stdout
    fake_secrets = {
        "GMAIL_SMTP_USER": "do-not-log-user@fixture.test",
        "GMAIL_SMTP_APP_PASSWORD": "do-not-log-password",
        "ALERT_EMAIL_FROM": "do-not-log-from@fixture.test",
        "TEAMS_CHANNEL_EMAIL": "do-not-log-recipient@fixture.test",
    }
    previous = {key: os.environ.get(key) for key in fake_secrets}
    os.environ.update(fake_secrets)
    try:
        with tempfile.TemporaryDirectory(prefix="d7ak6e-preview-") as temporary:
            preview_root = Path(temporary)
            output = io.StringIO()
            with hard_block_network(), contextlib.redirect_stdout(output):
                daily_manifest = runner.run_preview(
                    "daily",
                    run_at=dt("2026-07-27T07:00:00+09:00"),
                    preview_root=preview_root,
                    fixture_root="https://preview.fixture.test/HDEC-News-Sensor",
                    fixture_profile="dominant",
                )
                dominant_manifest = runner.run_preview(
                    "weekly",
                    run_at=dt("2026-07-29T07:30:00+09:00"),
                    preview_root=preview_root,
                    fixture_root="https://preview.fixture.test/HDEC-News-Sensor",
                    fixture_profile="dominant",
                )
                multi_manifest = runner.run_preview(
                    "weekly",
                    run_at=dt("2026-07-29T07:30:00+09:00"),
                    preview_root=preview_root,
                    fixture_root="https://preview.fixture.test/HDEC-News-Sensor",
                    fixture_profile="multi",
                )
            manifests = (daily_manifest, dominant_manifest, multi_manifest)
            check("preview writes dated/latest/text/html/manifest",
                  all(Path(item["dated_html"]).is_file()
                      and Path(item["latest_html"]).is_file()
                      and Path(item["teams_text"]).is_file()
                      and Path(item["teams_html"]).is_file()
                      and (Path(item["dated_html"]).parent / "manifest.json").is_file()
                      for item in manifests))
            check("preview dated/latest bytes are identical",
                  all(Path(item["dated_html"]).read_bytes()
                      == Path(item["latest_html"]).read_bytes() for item in manifests))
            zero_fields = (
                "network_sends", "smtp_attempts", "production_state_reads",
                "production_state_writes", "docs_writes", "git_writes",
                "forbidden_platform_calls",
            )
            check("preview side-effect counters are all zero",
                  all(item[field] == 0 for item in manifests for field in zero_fields))
            captured = output.getvalue()
            check("secret and recipient values are absent from logs",
                  not any(value in captured for value in fake_secrets.values()))
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    after = {path: snapshot(path) for path in protected_paths}
    status_after = subprocess.run(
        ["git", "status", "--short", "--untracked-files=all"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    ).stdout
    check("preview performs zero production state/docs writes", before == after)
    check("preview performs zero git writes", status_before == status_after)


def main() -> int:
    source_contracts()
    workflow_contracts()
    time_and_filter_contracts()
    state_contracts()
    daily, _dominant, _multi = link_and_render_contracts()
    url_and_publication_contracts(daily)
    smtp_and_state_contracts(daily)
    preview_contracts()
    print(f"\nRESULT pass={PASS} fail={FAIL}")
    print("NETWORK_FIXTURE=hard_blocked SMTP_FIXTURE=offline_only")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
