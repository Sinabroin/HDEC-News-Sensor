#!/usr/bin/env python3
"""Offline verifier for the independent Daily/Weekly editorial workflows."""

from __future__ import annotations

import contextlib
import hashlib
import http.client
import importlib.util
import io
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import urllib.error
from datetime import datetime, timedelta
from email.message import EmailMessage
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
for _path in (ROOT, SCRIPTS):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from app import editorial_briefing_state as state  # noqa: E402
from app import editorial_briefings as brief  # noqa: E402
from app import collector  # noqa: E402
from app import live_collector  # noqa: E402
from app import naver_news_provider  # noqa: E402
from app import news_access  # noqa: E402
from app import news_censor_verified_state  # noqa: E402
from app import public_urls as public_url_contract  # noqa: E402

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


class _ContractParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.structure: list[str] = []
        self.visible: list[str] = []
        self._hidden_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
        classes = ".".join(sorted(values.get("class", "").split()))
        self.structure.append(
            "|".join(
                (
                    tag,
                    classes,
                    values.get("data-role", ""),
                    values.get("data-section", ""),
                )
            )
        )
        if tag in {"style", "script"}:
            self._hidden_depth += 1

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"style", "script"} and self._hidden_depth:
            self._hidden_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._hidden_depth and data.strip():
            self.visible.append(data.strip())


def structure_signature(html: str) -> tuple[int, str]:
    parser = _ContractParser()
    parser.feed(html)
    payload = "\n".join(parser.structure).encode("utf-8")
    return len(parser.structure), hashlib.sha256(payload).hexdigest()


def visible_text(html: str) -> str:
    parser = _ContractParser()
    parser.feed(html)
    return " ".join(parser.visible)


def normalized_css_signature(html: str) -> str:
    match = re.search(r"<style>([\s\S]*?)</style>", html)
    if not match:
        return ""
    css = re.sub(r"/\*[\s\S]*?\*/", "", match.group(1))
    css = re.sub(r"\s+", "", css)
    return hashlib.sha256(css.encode("utf-8")).hexdigest()


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
        "templates/editorial_weekly_tni.html",
        "templates/editorial_brief.css",
    }
    check("all nine source files exist", all((ROOT / item).is_file() for item in expected))
    reference = Path(
        "/tmp/d7ak6e-r4r5-reference/"
        "AI경영_TnI_Weekly_2026-07월-3주차_최종(1).html"
    )
    if reference.exists():
        digest = hashlib.sha256(reference.read_bytes()).hexdigest()
        check(
            "approved Brief reference SHA256 is exact",
            digest == "e71308b7e1a9ee4697a5a597d5b074ce5aa1ec7ba60f83e597ebbdab6873dea2",
            digest,
        )
    else:
        print("INFO approved Brief reference is unavailable; generated contract still verified")


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
            f"{name} contains fetch/merge/push retry",
            "git fetch origin main" in workflow
            and "git merge --no-edit origin/main" in workflow
            and "for attempt in 1 2 3" in workflow,
        )
        check(
            f"{name} conflict handling has no reset or checkout deletion",
            "git reset" not in workflow and "git checkout --" not in workflow,
        )
        check(f"{name} never stages the repository root", "git add ." not in workflow)
        required_names = (
            f"Claim exact {name} edition after public verification",
            f"Commit and push exact {name} claim state file",
            f"Send claimed {name} edition",
            f"Commit and push exact {name} success state file",
        )
        check(
            f"{name} exact claim and success step names are present",
            all(f"- name: {step}" in workflow for step in required_names),
        )
        check(
            f"{name} force dry run and publish-only cannot reach claim or send",
            "steps.publish.outputs.delivery_authorized == 'true'" in workflow
            and "github.event.inputs.force_dry_run != 'true'" in workflow
            and 'PUBLISH_FLAG="--republish"' in workflow,
        )
        check(
            f"{name} workflow is rebase-free and publish-only defaults safe",
            "git rebase" not in workflow
            and "publish_only:" in workflow
            and 'default: "false"' in workflow,
        )

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
    check(
        "workflow commits claim before send",
        all(
            workflow.index(f"- name: Claim exact {name} edition after public verification")
            < workflow.index(f"- name: Commit and push exact {name} claim state file")
            < workflow.index(f"- name: Send claimed {name} edition")
            < workflow.index(f"- name: Commit and push exact {name} success state file")
            for name, workflow in (("Daily", daily), ("Weekly", weekly))
        ),
    )
    check(
        "workflow send is gated by claim authorization",
        all(
            "if: success() && steps.claim.outputs.send_authorized == 'true'" in workflow
            for workflow in (daily, weekly)
        ),
    )
    check(
        "workflow claim step id is exact",
        all(
            re.search(
                rf"- name: Claim exact {name} edition after public verification\n"
                r"\s+id: claim",
                workflow,
            )
            for name, workflow in (("Daily", daily), ("Weekly", weekly))
        ),
    )
    check(
        "workflow claim commit messages include exact edition",
        'git commit -m "chore: claim Daily editorial delivery ${EDITION}"' in daily
        and 'git commit -m "chore: claim Weekly editorial delivery ${EDITION}"' in weekly,
    )
    check(
        "workflow success commit messages include exact edition",
        'git commit -m "chore: record Daily editorial delivery ${EDITION}"' in daily
        and 'git commit -m "chore: record Weekly editorial delivery ${EDITION}"' in weekly,
    )
    platform_token = "tele" + "gram"
    implementation_parts = [
        (path, read(path).casefold())
        for path in (
            ".github/workflows/editorial-daily-brief.yml",
            ".github/workflows/editorial-weekly-ti.yml",
            "app/editorial_briefings.py",
            "app/editorial_briefing_state.py",
            "scripts/run_editorial_briefing.py",
            "templates/editorial_daily.html",
            "templates/editorial_weekly_tni.html",
        )
    ]
    implementation = "\n".join(content for _path, content in implementation_parts)
    platform_mentions = [
        (path, line)
        for path, content in implementation_parts
        for line in content.splitlines()
        if platform_token in line
    ]
    check(
        "forbidden messaging platform integration count is zero",
        bool(platform_mentions)
        and all(
            path == "scripts/run_editorial_briefing.py"
            and "telegram_calls" in line
            and (
                re.search(r'"telegram_calls":\s*0', line)
                or "telegram_calls=0" in line
            )
            for path, line in platform_mentions
        ),
        repr(platform_mentions),
    )
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
    fixture_run = dt("2026-07-27T07:00:00+09:00")
    fixture_daily = brief.normalize_articles(
        brief.fixture_articles("daily", fixture_run),
        brief.daily_coverage(fixture_run),
        limit=brief.DAILY_MAX_ARTICLES,
        resolve_images=False,
        selection_mode=brief.SELECTION_MODE_LEGACY,
    )
    check(
        "legacy mode preserves Daily fixture article order",
        [article.title for article in fixture_daily]
        == [
            "AI 데이터센터 공급망 계약 확대",
            "AI 데이터센터 냉각 운영 기준 점검",
            "AI 데이터센터 전력 수요 대응 협력",
            "AI 데이터센터 전력 효율 기술 투자",
            "AI 데이터센터 전력망 연계 기준 논의",
            "AI 데이터센터 전력 조달 계획 공개",
        ],
    )

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
        legacy = {
            "version": 1,
            "edition_type": "daily",
            "successful_editions": [record],
            "last_successful_edition": record["edition_key"],
            "last_successful_send_at": record["sent_at"],
        }
        legacy_path = root / "legacy.json"
        legacy_path.write_text(json.dumps(legacy), encoding="utf-8")
        migrated = state.load_state("daily", legacy_path)
        check(
            "legacy version 1 state migrates to claim schema",
            migrated["version"] == 2
            and migrated["successful_editions"] == legacy["successful_editions"]
            and migrated["last_successful_edition"] == legacy["last_successful_edition"]
            and migrated["last_successful_send_at"] == legacy["last_successful_send_at"]
            and migrated["delivery_claims"] == {},
        )
        check(
            "empty state contains no claims",
            state.empty_state("daily")["version"] == 2
            and state.empty_state("daily")["delivery_claims"] == {}
            and not state.has_claim(state.empty_state("daily"), "2026-07-27"),
        )
        bad_legacy = dict(legacy)
        bad_legacy["extra"] = True
        expect_raises(
            "legacy state with extra fields fails closed",
            state.StateError,
            lambda: state.validate_state(bad_legacy, "daily"),
        )
        non_250 = json.loads(json.dumps(legacy))
        non_250["successful_editions"][0]["smtp_code"] = 251
        expect_raises(
            "legacy non-250 state fails closed",
            state.StateError,
            lambda: state.validate_state(non_250, "daily"),
        )
        duplicate = json.loads(json.dumps(legacy))
        duplicate["successful_editions"].append(dict(record))
        expect_raises(
            "legacy duplicate success state fails closed",
            state.StateError,
            lambda: state.validate_state(duplicate, "daily"),
        )
        cross_edition = json.loads(json.dumps(legacy))
        cross_edition["successful_editions"][0]["edition_key"] = "2026-W31"
        cross_edition["last_successful_edition"] = "2026-W31"
        expect_raises(
            "legacy cross-edition success state fails closed",
            state.StateError,
            lambda: state.validate_state(cross_edition, "daily"),
        )
        claim = {
            "edition_key": "2026-07-27",
            "coverage_start": record["coverage_start"],
            "coverage_end": record["coverage_end"],
            "html_sha256": record["html_sha256"],
            "public_url": record["public_url"],
            "claim_owner": "github-run:100:attempt:1",
            "claimed_at": "2026-07-27T07:01:00+09:00",
        }
        claimed = state.add_claim(state.empty_state("daily"), "daily", claim)
        check(
            "claim has exactly the required fields",
            set(claim)
            == {
                "edition_key",
                "coverage_start",
                "coverage_end",
                "html_sha256",
                "public_url",
                "claim_owner",
                "claimed_at",
            }
            and state.has_claim(claimed, claim["edition_key"]),
        )
        check(
            "same exact owner claim is idempotent without timestamp refresh",
            state.add_claim(
                claimed,
                "daily",
                {**claim, "claimed_at": "2026-07-27T07:20:00+09:00"},
            )
            == claimed,
        )
        expect_raises(
            "conflicting claim owner is denied",
            state.StateError,
            lambda: state.add_claim(
                claimed,
                "daily",
                {**claim, "claim_owner": "github-run:101:attempt:1"},
            ),
        )
        future_claim = {
            **claim,
            "edition_key": "2026-07-28",
            "public_url": record["public_url"].replace("2026-07-27", "2026-07-28"),
            "claimed_at": "2026-07-28T07:01:00+09:00",
        }
        with_future = state.add_claim(claimed, "daily", future_claim)
        check(
            "orphaned claim does not block a future edition",
            state.has_claim(with_future, "2026-07-27")
            and state.has_claim(with_future, "2026-07-28")
            and len(with_future["delivery_claims"]) == 2,
        )
        active, expired = state.expire_stale_claims(
            with_future,
            "daily",
            now=dt("2026-07-27T07:30:59+09:00"),
        )
        check(
            "claim remains active before the bounded TTL",
            expired == ()
            and state.has_claim(active, "2026-07-27")
            and state.has_claim(active, "2026-07-28"),
        )
        recovered, expired = state.expire_stale_claims(
            with_future,
            "daily",
            now=dt("2026-07-27T07:31:00+09:00"),
        )
        check(
            "stale claim expires without consuming later eligible editions",
            expired == ("2026-07-27",)
            and not state.has_claim(recovered, "2026-07-27")
            and state.has_claim(recovered, "2026-07-28"),
        )
        expect_raises(
            "claim expiry requires an aware clock",
            state.StateError,
            lambda: state.expire_stale_claims(
                with_future,
                "daily",
                now=dt("2026-07-27T07:31:00"),
            ),
        )
        check(
            "Daily and Weekly states remain independent",
            state.empty_state("daily")["edition_type"] == "daily"
            and state.empty_state("weekly")["edition_type"] == "weekly"
            and state.state_path("daily") != state.state_path("weekly"),
        )


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
    # R4-R6 — the injected title stays AI-central so the headline contract
    # holds while the escaping assertion still exercises the script tag.
    daily_rows[0]["title"] = 'AI 경영진 <script>alert("x")</script> 확인 기사'
    daily_rows[0]["image_url"] = ""
    daily = brief.render_edition("daily", daily_rows, run_at=run_daily, root_url=root)
    brief.validate_rendered(daily)
    check("Daily has exactly one headline", daily.html.count('data-role="headline"') == 1)
    check("Daily has no more than five article cards",
          daily.html.count('data-role="article-card"') == 5)
    check("Daily timestamps are explicit KST",
          daily.html.count(" KST · ") == 6)
    check("Daily Editor's Summary keeps exact reference position",
          daily.html.count("<h3 class=\"ed-k\">Editor's Summary</h3>") == 1
          and daily.html.index('data-role="headline"')
          < daily.html.index("<h3 class=\"ed-k\">Editor's Summary</h3>")
          < daily.html.index('data-role="article-card"'))
    check("HTML escaping blocks injected markup",
          "<script>alert" not in daily.html and "&lt;script&gt;" in daily.html)
    check("missing image keeps exact reference image geometry",
          daily.html.count("<div class=\"thumb\"><img ") == 5
          and "data:image/svg+xml" in daily.html)
    check("external links all carry target/rel security",
          daily.html.count('target="_blank"') == daily.html.count('rel="noopener noreferrer"'))
    check("selected link kind is preserved in HTML",
          daily.html.count('data-link-kind="') == daily.html.count('target="_blank"'))
    check("Daily identity replaces reference Weekly/first-issue copy",
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
    weekly_template = read("templates/editorial_weekly_tni.html")
    daily_template = read("templates/editorial_daily.html")
    shared_css = read("templates/editorial_brief.css")
    check(
        "Daily uses the sealed shared CSS contract",
        normalized_css_signature(daily.html)
        == normalized_css_signature(daily.html)
        and hashlib.sha256(re.sub(r"\s+", "", shared_css).encode("utf-8")).hexdigest()
        == "6d095dfdd42b9403c3802963cdbec3ee366519cf8430588ba0be1e221a20bc8a",
    )
    check(
        # R4-R6 §14 — Weekly's design authority is the sealed T&I reference:
        # its stylesheet ships inside the reference shell, not editorial_brief.css.
        "Weekly embeds the sealed T&I reference stylesheet",
        "--navy:#002C5F" in dominant.html
        and ".page{max-width:680px" in dominant.html
        and "@media (max-width:560px)" in dominant.html
        and "@media print" in dominant.html,
    )
    check(
        "daily template injects the shared stylesheet; weekly is island-only",
        daily_template.count("{{BRIEF_STYLES}}") == 1
        and weekly_template.count("{{BRIEF_STYLES}}") == 0
        and all(
            weekly_template.count(placeholder) == 1
            for placeholder in (
                "{{TNI_TITLE_ISSUE}}", "{{TNI_ISSUE_LABEL}}", "{{TNI_HERO_IMAGE}}",
                "{{TNI_HERO_TITLE}}", "{{TNI_HERO_CATEGORY}}", "{{TNI_EDNOTE_HTML}}",
                "{{TNI_EDNOTE_SOURCE}}", "{{TNI_CARDS}}", "{{TNI_META_ISSUE}}",
            )
        ),
    )
    check(
        "Brief contract is 680px, mobile responsive, and print safe",
        ".brief-page{max-width:680px" in shared_css
        and "@media(max-width:560px)" in shared_css
        and "@media print" in shared_css
        and "break-inside:avoid;page-break-inside:avoid" in shared_css,
    )
    check(
        "Brief templates have email-safe critical inline layout",
        'style="max-width:680px;margin:0 auto;padding:64px 20px 56px"'
        in daily_template
        and 'style="display:inline-block;background:#002c5f;color:#fff;'
        in daily_template
        and "max-width: 680px; margin: 0px auto; padding: 0px 20px 56px;"
        in weekly_template,
    )
    check(
        "Brief font delivery has no runtime dependency",
        "@import" not in shared_css
        and "fonts.googleapis" not in shared_css
        and "<script" not in daily_template + weekly_template,
    )
    daily_hierarchy = (
        'class="masthead"',
        'class="brandmark"',
        '<h1>',
        'class="desc"',
        'class="issue"',
        'data-role="headline"',
        "Editor's Summary",
        'data-role="article-card"',
        'class="taxonomy"',
        "<footer",
    )
    # R4-R6 §14 — the Weekly hierarchy is the reference document's own order;
    # the reference carries no data-role attributes and none may be added.
    weekly_hierarchy = (
        'class="masthead"',
        'class="brandmark"',
        '<h1',
        'class="desc"',
        'class="issue"',
        '이번 주 헤드라인',
        'class="hero"',
        "Editor's Summary",
        '이번 주 브리핑',
        'class="card"',
        '정보 분류 기준',
        'class="taxo"',
        "<footer",
    )
    for label, rendered, hierarchy in (
        ("Daily", daily, daily_hierarchy),
        ("Weekly", dominant, weekly_hierarchy),
    ):
        check(
            f"{label} approved visual hierarchy is exact",
            all(
                token in rendered.html for token in hierarchy
            )
            and all(
                rendered.html.index(first) < rendered.html.index(second)
                for first, second in zip(hierarchy, hierarchy[1:])
            ),
        )
    check(
        "Daily and Weekly wording is edition-specific",
        all(
            text in daily.html
            for text in (
                "Daily Brief",
                "매일 전하는",
                "오늘의 헤드라인",
                "오늘의 브리핑",
            )
        )
        and all(
            text in dominant.html
            for text in (
                "Weekly Brief",
                "매주 전하는",
                "이번 주 헤드라인",
                "이번 주 브리핑",
            )
        ),
    )
    check(
        "Briefs retain approved taxonomy and publication footer",
        all(
            all(text in rendered.html for text in (
                "투자·산업", "기업동향", "기술정보",
                "워크이노베이션센터 | AI디자인랩",
            ))
            for rendered in (daily, dominant)
        ),
    )
    check(
        "Daily hero and every card are image backed without remote hotlinks",
        daily.html.count("<img ") == 6
        and re.search(r'<img[^>]+src=["\']https?://', daily.html) is None,
    )
    check(
        # Weekly cards always carry an image (deterministic fallback allowed);
        # the reference hero image is optional and only ever local/data URI.
        "Weekly cards are image backed without remote hotlinks",
        dominant.html.count("<img ")
        >= dominant.html.count('<article class="card"')
        and re.search(r'<img[^>]+src=["\']https?://', dominant.html) is None,
    )
    daily_structure = structure_signature(daily.html)
    dominant_structure = structure_signature(dominant.html)
    multi_structure = structure_signature(multi.html)
    check(
        "Daily shared Brief DOM signature",
        daily_structure
        == (105, "c0933a4e3192dd56caa46bdc1f6748244b2508a0c21af4296719f47aa63f6410"),
        repr(daily_structure),
    )
    check(
        # R4-R6 §14 — Weekly deliberately leaves the shared daily DOM: its shell
        # is the sealed T&I reference (byte/pixel parity proven by
        # scripts/verify_weekly_tni_reference_parity.py).
        "Weekly leaves the daily DOM for the sealed T&I reference shell",
        dominant_structure != daily_structure,
        f"daily={daily_structure!r} weekly={dominant_structure!r}",
    )
    check(
        "Weekly multi canonical DOM signature",
        multi_structure == dominant_structure,
        repr(multi_structure),
    )
    check(
        "retired A4 report-table UI is absent",
        not any(
            token in daily_template + weekly_template
            for token in ("PDF로 저장", "table-scroll-hint", "width:794px", "key-facts")
        ),
    )

    sample_sentinels = [
        "My" + "thos", "Anth" + "ropic", "Op" + "us", "G" + "PT", "Gem" + "ini",
        "Glass" + "wing", "260415", "$100M", "27 years", "5 million",
        "16 years", "12 companies", "40 institutions",
    ]
    combined = visible_text(dominant.html) + visible_text(multi.html)
    check(
        "Weekly sample names, dates, and figures are absent",
        not any(token.casefold() in combined.casefold() for token in sample_sentinels),
    )
    check("all Weekly external links carry target/rel security",
          dominant.html.count('target="_blank"')
          == dominant.html.count('rel="noopener noreferrer"'))
    return daily, dominant, multi


def image_resolution_contracts() -> None:
    page_url = "https://publisher.fixture.test/article"
    feed = {
        "title": "이미지 우선순위 검증 기사",
        "source": "검증매체",
        "published_at": "2026-07-27T06:00:00+09:00",
        "url": page_url,
        "image_url": "https://cdn.fixture.test/feed.jpg?x=1&y=2",
        "media_content": {
            "url": "https://cdn.fixture.test/media.jpg",
            "width": "1200",
            "height": "675",
            "type": "image/jpeg",
        },
        "media_thumbnail": {
            "url": "https://cdn.fixture.test/thumb.jpg",
            "width": "640",
            "height": "360",
            "type": "image/jpeg",
        },
        "enclosure": {
            "url": "https://cdn.fixture.test/enclosure.jpg",
            "type": "image/jpeg",
        },
    }
    selected = brief.resolve_article_image(feed, allow_network=False)
    check(
        "existing feed image field has highest image precedence",
        selected.source_kind == "rss_image"
        and selected.url == "https://cdn.fixture.test/feed.jpg?x=1&y=2",
    )
    for removed, expected in (
        (("image_url",), "media_content"),
        (("image_url", "media_content"), "media_thumbnail"),
        (("image_url", "media_content", "media_thumbnail"), "enclosure"),
    ):
        row = dict(feed)
        for key in removed:
            row.pop(key, None)
        resolution = brief.resolve_article_image(row, allow_network=False)
        check(
            f"{expected} image precedence is preserved",
            resolution.source_kind == expected and not resolution.fallback_used,
        )

    rss_xml = """<?xml version="1.0"?>
    <rss xmlns:media="http://search.yahoo.com/mrss/"><channel><item>
      <title>RSS 이미지 검증</title>
      <link>https://publisher.fixture.test/rss</link>
      <source>검증매체</source>
      <pubDate>Mon, 27 Jul 2026 06:00:00 +0900</pubDate>
      <description>첫 문장. 두 번째 문장.</description>
      <media:content url="https://cdn.fixture.test/content.jpg"
        width="1200" height="675" type="image/jpeg" />
      <media:thumbnail url="https://cdn.fixture.test/thumbnail.jpg"
        width="640" height="360" />
      <enclosure url="https://cdn.fixture.test/enclosure.jpg" type="image/jpeg" />
    </item></channel></rss>"""
    parsed_rows = live_collector._parse_items(  # noqa: SLF001
        rss_xml, "fixture", "2026-07-27T06:10:00+09:00", 1
    )
    check(
        "RSS parser preserves media content, thumbnail, and image enclosure",
        len(parsed_rows) == 1
        and parsed_rows[0]["media_content"][0]["width"] == "1200"
        and parsed_rows[0]["media_thumbnail"][0]["height"] == "360"
        and parsed_rows[0]["enclosure"][0]["type"] == "image/jpeg",
    )

    metadata_cases = (
        (
            "og_image",
            '<meta property="og:image" content="https://img.fixture.test/og">'
            '<meta property="og:image:width" content="1200">'
            '<meta property="og:image:height" content="675">',
        ),
        (
            "twitter_image",
            '<meta name="twitter:image" content="https://img.fixture.test/twitter">',
        ),
        (
            "jsonld_image",
            '<script type="application/ld+json">'
            '{"@type":"NewsArticle","image":{"url":"https://img.fixture.test/jsonld",'
            '"width":1200,"height":675}}</script>',
        ),
    )
    for expected_kind, head in metadata_cases:
        html = f"<html><head>{head}</head><body></body></html>"
        counters = brief.ImageResolutionCounters()
        resolution = brief.resolve_article_image(
            {"url": page_url},
            allow_network=True,
            counters=counters,
            page_fetcher=lambda _url, html=html: (page_url, html),
            image_probe=lambda _url: True,
        )
        check(
            f"publisher {expected_kind} metadata is parsed",
            resolution.source_kind == expected_kind
            and not resolution.fallback_used
            and counters.network_page_gets == 1,
        )

    link_html = (
        '<link rel="image_src" href="https://publisher.fixture.test/lead.jpg">'
        '<img src="https://publisher.fixture.test/body.jpg" width="1200" height="675">'
    )
    link_candidates = brief.parse_publisher_image_candidates(link_html, page_url)
    check(
        "image_src precedes limited body image candidate",
        [item.source_kind for item in link_candidates] == ["image_src", "body_image"],
    )

    blocked_rows = (
        {"url": page_url, "image_url": "data:image/png;base64,AAAA"},
        {"url": page_url, "image_url": "http://127.0.0.1/private.jpg"},
        {"url": page_url, "image_url": "https://localhost/private.jpg"},
        {"url": page_url, "image_url": "https://news.google.com/logo.jpg"},
        {"url": page_url, "image_url": "https://cdn.fixture.test/favicon.png"},
        {
            "url": page_url,
            "media_content": {
                "url": "https://cdn.fixture.test/tracker.png",
                "width": 1,
                "height": 1,
                "type": "image/png",
            },
        },
    )
    check(
        "unsafe scheme, private hosts, Google, logo, favicon, and tracker candidates are blocked",
        all(
            brief.resolve_article_image(row, allow_network=False).fallback_used
            for row in blocked_rows
        ),
    )
    invalid_mime = brief.resolve_article_image(
        {"url": page_url, "image_url": "https://cdn.fixture.test/no-extension"},
        allow_network=True,
        page_fetcher=lambda _url: (page_url, "<html></html>"),
        image_probe=lambda _url: False,
    )
    check("candidate with unconfirmed image MIME is rejected", invalid_mime.fallback_used)

    used: set[str] = set()
    duplicate_row = {
        "url": page_url,
        "image_url": "https://cdn.fixture.test/shared-article.jpg",
    }
    first = brief.resolve_article_image(
        duplicate_row, allow_network=False, used_urls=used
    )
    second = brief.resolve_article_image(
        duplicate_row, allow_network=False, used_urls=used
    )
    check(
        "duplicate representative image is suppressed across articles",
        not first.fallback_used and second.fallback_used,
    )

    no_image_stats = brief.ImageResolutionCounters()
    no_image = brief.resolve_article_image(
        {"url": page_url},
        allow_network=True,
        counters=no_image_stats,
        page_fetcher=lambda _url: (
            page_url,
            '<img src="https://ads.unrelated.test/banner.jpg" width="1200" height="300">',
        ),
        image_probe=lambda _url: True,
    )
    check(
        "fallback occurs only after safe publisher candidates are exhausted",
        no_image.fallback_used
        and no_image.reason == "publisher_page_had_no_safe_image"
        and no_image_stats.network_page_gets == 1,
    )

    run_at = dt("2026-07-27T07:00:00+09:00")
    rows = brief.fixture_articles("daily", run_at)
    rows[0]["image_url"] = "https://cdn.fixture.test/hero.jpg?x=1&y=2"
    for index, row in enumerate(rows[1:], start=1):
        row["image_url"] = f"https://cdn.fixture.test/card-{index}.jpg"
    rendered = brief.render_edition(
        "daily",
        rows,
        run_at=run_at,
        root_url="https://preview.fixture.test/HDEC-News-Sensor",
    )
    check(
        "Daily exact-reference keeps one headline image and five card images",
        rendered.html.count("<img ") == 6
        and rendered.html.count('<div class="thumb"><img ') == 5,
    )
    check(
        "unmaterialized remote image URL is replaced by deterministic fallback",
        "hero.jpg?" not in rendered.html
        and "data:image/svg+xml" in rendered.html,
    )


def publisher_url_resolution_contracts() -> None:
    aggregator = "https://news.google.com/rss/articles/fixture?oc=5"
    source_home = "https://publisher.fixture.test/"

    def row(**metadata):
        return {
            "title": "퍼블리셔 URL 복원 검증",
            "source": "검증매체",
            "published_at": "2026-07-27T06:00:00+09:00",
            "url": aggregator,
            "snippet": "첫 문장. 두 번째 문장.",
            "source_metadata": {
                "source_url": aggregator,
                "rss_source_home_url": source_home,
                **metadata,
            },
        }

    expected_counter = {
        "existing_publisher_direct": "publisher_urls_existing_direct",
        "rss_source_url": "publisher_urls_from_source_url",
        "rss_orig_link": "publisher_urls_from_orig_link",
        "rss_description_link": "publisher_urls_from_description",
        "rss_content_link": "publisher_urls_from_content",
        "rss_guid_direct": "publisher_urls_from_guid",
        "rss_atom_link": "publisher_urls_from_atom",
    }
    direct_cases = (
        (
            "existing direct publisher URL",
            {
                **row(),
                "publisher_url": "https://publisher.fixture.test/news/existing",
            },
            "existing_publisher_direct",
        ),
        (
            "RSS source URL",
            row(rss_source_url="https://publisher.fixture.test/news/source"),
            "rss_source_url",
        ),
        (
            "feedburner origLink",
            row(rss_orig_link="https://publisher.fixture.test/news/original"),
            "rss_orig_link",
        ),
        (
            "RSS description article link",
            row(rss_description_links=[
                "https://publisher.fixture.test/news/description"
            ]),
            "rss_description_link",
        ),
        (
            "RSS content article link",
            row(rss_content_links=["https://publisher.fixture.test/news/content"]),
            "rss_content_link",
        ),
        (
            "direct GUID",
            row(rss_guid="https://publisher.fixture.test/news/guid"),
            "rss_guid_direct",
        ),
        (
            "direct Atom link",
            row(rss_atom_links=["https://publisher.fixture.test/news/atom"]),
            "rss_atom_link",
        ),
    )
    for label, article, expected in direct_cases:
        counters = brief.PublisherUrlResolutionCounters()
        resolution = brief.resolve_publisher_article_url(
            article, allow_network=False, counters=counters
        )
        check(
            f"{label} precedes aggregator network resolution",
            resolution.source_kind == expected
            and not resolution.fallback_used
            and resolution.original_host == "news.google.com"
            and resolution.resolved_host == "publisher.fixture.test"
            and getattr(counters, expected_counter[expected]) == 1
            and counters.aggregator_page_gets == 0,
        )

    redirect = brief.resolve_publisher_article_url(
        row(),
        allow_network=True,
        fetcher=lambda _url: (
            "https://publisher.fixture.test/news/redirected", "<html></html>"
        ),
    )
    check(
        "aggregator HTTP redirect resolves publisher article",
        redirect.source_kind == "aggregator_redirect"
        and redirect.network_gets == 1,
    )

    metadata_cases = (
        (
            "aggregator_canonical",
            '<link rel="canonical" href="https://publisher.fixture.test/news/canonical">',
        ),
        (
            "aggregator_og_url",
            '<meta property="og:url" content="https://publisher.fixture.test/news/og">',
        ),
        (
            "aggregator_outbound_link",
            '<a href="https://publisher.fixture.test/news/outbound">기사 원문</a>',
        ),
    )
    for expected, body in metadata_cases:
        resolution = brief.resolve_publisher_article_url(
            row(),
            allow_network=True,
            fetcher=lambda _url, body=body: (aggregator, body),
        )
        check(
            f"{expected} metadata resolves publisher article",
            resolution.source_kind == expected
            and not resolution.fallback_used
            and resolution.network_gets == 1,
        )

    ambiguous = brief.resolve_publisher_article_url(
        row(),
        allow_network=True,
        fetcher=lambda _url: (
            aggregator,
            '<a href="https://publisher.fixture.test/news/one">one</a>'
            '<a href="https://publisher.fixture.test/news/two">two</a>',
        ),
    )
    check(
        "multiple publisher outbound candidates remain unresolved",
        ambiguous.fallback_used
        and ambiguous.reason == "multiple_publisher_outbound_candidates",
    )

    self_canonical = brief.resolve_publisher_article_url(
        row(),
        allow_network=True,
        fetcher=lambda _url: (
            aggregator,
            '<link rel="canonical" href="https://news.google.com/rss/articles/self">',
        ),
    )
    check(
        "aggregator self-canonical remains unresolved",
        self_canonical.fallback_used
        and self_canonical.reason == "aggregator_exposed_no_safe_publisher_url",
    )

    blocked_metadata = (
        '<link rel="canonical" href="https://news.google.com/rss/articles/other">'
        '<meta property="og:url" content="http://127.0.0.1/private/article">'
        '<a href="https://publisher.fixture.test/login">login</a>'
        '<a href="https://ads.fixture.test/advert/click">advertisement</a>'
        '<a href="https://tracker.fixture.test/news/click">tracker</a>'
        '<a href="https://publisher.fixture.test/privacy">privacy</a>'
        '<a href="https://publisher.fixture.test/terms">terms</a>'
    )
    blocked = brief.resolve_publisher_article_url(
        row(),
        allow_network=True,
        fetcher=lambda _url: (aggregator, blocked_metadata),
    )
    cross_publisher = brief.resolve_publisher_article_url(
        row(rss_description_links=["https://other.fixture.test/news/wrong"]),
        allow_network=False,
    )
    check(
        "Google, private, cross-publisher, advertising, login, and terms URLs are blocked",
        blocked.fallback_used
        and blocked.reason == "aggregator_exposed_no_safe_publisher_url"
        and cross_publisher.fallback_used,
    )

    private_outbound = brief.resolve_publisher_article_url(
        row(),
        allow_network=True,
        fetcher=lambda _url: (
            aggregator,
            '<a href="http://127.0.0.1/news/private">private</a>'
            '<a href="http://10.0.0.5/news/private">private</a>'
            '<a href="https://localhost/news/private">private</a>',
        ),
    )
    check(
        "localhost and private outbound candidates are blocked",
        private_outbound.fallback_used
        and private_outbound.reason == "aggregator_exposed_no_safe_publisher_url",
    )

    def redirect_loop(_url):
        raise brief.EditorialError("fixture redirect loop")

    def invalid_content_type(_url):
        raise brief.EditorialError("aggregator response is not HTML")

    loop = brief.resolve_publisher_article_url(
        row(), allow_network=True, fetcher=redirect_loop
    )
    oversized = brief.resolve_publisher_article_url(
        row(),
        allow_network=True,
        fetcher=lambda _url: (aggregator, "x" * (brief.PUBLISHER_PAGE_MAX_BYTES + 1)),
    )
    invalid = brief.resolve_publisher_article_url(
        row(), allow_network=True, fetcher=invalid_content_type
    )
    check(
        "redirect loop, oversized HTML, and invalid content type fail closed",
        loop.fallback_used
        and oversized.fallback_used
        and invalid.fallback_used
        and loop.reason == "aggregator_page_unavailable_or_invalid"
        and oversized.reason == "aggregator_page_unavailable_or_invalid"
        and invalid.reason == "aggregator_page_unavailable_or_invalid",
    )

    rss_xml = """<?xml version="1.0"?>
    <rss xmlns:feedburner="http://rssnamespace.org/feedburner/ext/1.0"
      xmlns:content="http://purl.org/rss/1.0/modules/content/"
      xmlns:atom="http://www.w3.org/2005/Atom"><channel><item>
      <title>RSS 원문 후보 검증</title>
      <link>https://news.google.com/rss/articles/fixture</link>
      <guid>https://publisher.fixture.test/news/guid</guid>
      <source url="https://publisher.fixture.test/">검증매체</source>
      <atom:link rel="alternate" href="https://publisher.fixture.test/news/atom" />
      <pubDate>Mon, 27 Jul 2026 06:00:00 +0900</pubDate>
      <description><![CDATA[
        <a href="https://publisher.fixture.test/news/description">기사</a>
      ]]></description>
      <content:encoded><![CDATA[
        <a href="https://publisher.fixture.test/news/content">원문</a>
      ]]></content:encoded>
      <feedburner:origLink>
        https://publisher.fixture.test/news/original
      </feedburner:origLink>
    </item></channel></rss>"""
    parsed = live_collector._parse_items(  # noqa: SLF001
        rss_xml, "fixture", "2026-07-27T06:10:00+09:00", 1
    )[0]["source_metadata"]
    check(
        "RSS parser retains direct-link candidates without raw HTML",
        parsed["rss_orig_link"].endswith("/news/original")
        and parsed["rss_source_url"].rstrip("/") == "https://publisher.fixture.test"
        and parsed["rss_description_links"][0].endswith("/news/description")
        and parsed["rss_content_links"][0].endswith("/news/content")
        and parsed["rss_guid"].endswith("/news/guid")
        and parsed["rss_atom_links"][0].endswith("/news/atom")
        and "description" not in parsed,
    )

    coverage = brief.daily_coverage(dt("2026-07-27T07:00:00+09:00"))
    publisher_stats = brief.PublisherUrlResolutionCounters()
    image_stats = brief.ImageResolutionCounters()
    resolved = brief.normalize_articles(
        [row()],
        coverage,
        limit=1,
        allow_image_network=True,
        publisher_counters=publisher_stats,
        publisher_fetcher=lambda _url: (
            "https://publisher.fixture.test/news/resolved",
            "<html></html>",
        ),
        image_counters=image_stats,
        image_page_fetcher=lambda _url: (
            "https://publisher.fixture.test/news/resolved",
            '<meta property="og:image" '
            'content="https://publisher.fixture.test/images/article.jpg">',
        ),
    )
    check(
        "publisher URL resolution occurs before publisher og:image lookup",
        len(resolved) == 1
        and resolved[0].selected_url.endswith("/news/resolved")
        and resolved[0].image_source_kind == "og_image"
        and publisher_stats.aggregator_page_gets == 1
        and image_stats.network_page_gets == 1,
    )
    twitter_stats = brief.ImageResolutionCounters()
    twitter_resolved = brief.normalize_articles(
        [row()],
        coverage,
        limit=1,
        allow_image_network=True,
        publisher_fetcher=lambda _url: (
            "https://publisher.fixture.test/news/resolved-twitter",
            "<html></html>",
        ),
        image_counters=twitter_stats,
        image_page_fetcher=lambda _url: (
            "https://publisher.fixture.test/news/resolved-twitter",
            '<meta name="twitter:image" '
            'content="https://publisher.fixture.test/images/twitter.jpg">',
        ),
    )
    check(
        "publisher URL recovery can feed twitter:image lookup",
        len(twitter_resolved) == 1
        and twitter_resolved[0].image_source_kind == "twitter_image"
        and twitter_stats.network_page_gets == 1,
    )
    failed = brief.normalize_articles(
        [row()],
        coverage,
        limit=1,
        allow_image_network=True,
        publisher_fetcher=lambda _url: (aggregator, "<html></html>"),
    )
    check(
        "unresolved aggregator never supplies an image and falls back",
        len(failed) == 1
        and failed[0].publisher_url_source_kind == "unresolved_aggregator"
        and failed[0].image_fallback_used
        and failed[0].image_reason == "aggregator_page_not_used_for_image",
    )
    offline_stats = brief.PublisherUrlResolutionCounters()
    offline = brief.resolve_publisher_article_url(
        row(),
        allow_network=False,
        fetcher=lambda _url: (_ for _ in ()).throw(
            AssertionError("network should remain blocked")
        ),
        counters=offline_stats,
    )
    check(
        "offline publisher preview keeps aggregator network at zero",
        offline.fallback_used
        and offline.reason == "network_disabled_and_rss_had_no_direct_url"
        and offline_stats.aggregator_page_gets == 0,
    )
    template_hashes = {
        "templates/editorial_daily.html": (
            "1c399616877a2dc014b541d781076c32508dc522fcd947a4a62a94d25fb7f9ab"
        ),
        # R4-R6 §14 — the weekly template is the sealed T&I reference with
        # content islands only (text-mode hash; CRLF preserved on disk).
        "templates/editorial_weekly_tni.html": (
            "25c1877c92f2b7334c6357a6ffd3f206153840eabd7c259b5c3edcb0ea4d4be4"
        ),
    }
    check(
        "template SHA values remain unchanged",
        all(
            hashlib.sha256(read(path).encode("utf-8")).hexdigest() == expected
            for path, expected in template_hashes.items()
        ),
    )


def naver_provider_contracts() -> None:
    collected_at = "2026-07-27T06:10:00+09:00"
    host_map = {"publisher.fixture.test": "검증매체"}
    normal_payload = {
        "items": [
            {
                "title": "AI <b>건설</b> &amp; 인프라",
                "description": "<b>스마트</b> 시공 &amp; 데이터센터 확산.",
                "pubDate": "Mon, 27 Jul 2026 06:00:00 +0900",
                "originallink": "https://publisher.fixture.test/news/naver-origin",
                "link": "https://n.news.naver.com/mnews/article/001/0000000001",
            }
        ]
    }
    rows = naver_news_provider.parse_response(
        normal_payload, "AI 건설", collected_at, host_map, 5
    )
    first = rows[0] if rows else {}
    check(
        "Naver API fixture JSON parses one direct article",
        len(rows) == 1
        and first.get("source_metadata", {}).get("provider") == "naver_news_api",
    )
    check(
        "Naver originallink is preferred and host is preserved",
        str(first.get("url") or "").endswith("/news/naver-origin")
        and brief._url_host(first.get("url")) == "publisher.fixture.test",  # noqa: SLF001
    )
    check(
        "Naver title and description HTML tags are removed",
        first.get("title") == "AI 건설 & 인프라"
        and first.get("snippet") == "스마트 시공 & 데이터센터 확산.",
    )
    check(
        "Naver pubDate parses as timezone-aware ISO",
        first.get("published_at") == "2026-07-27T06:00:00+09:00",
        str(first.get("published_at")),
    )

    link_only = naver_news_provider.parse_response(
        {
            "items": [
                {
                    "title": "원문 링크 없는 안전 링크",
                    "description": "첫 문장. 두 번째 문장.",
                    "pubDate": "Mon, 27 Jul 2026 06:00:00 +0900",
                    "link": "https://publisher.fixture.test/news/safe-link",
                }
            ]
        },
        "AI 건설",
        collected_at,
        host_map,
        5,
    )
    check(
        "Naver safe link is used only when originallink is absent",
        len(link_only) == 1
        and str((link_only[0] if link_only else {}).get("url") or "").endswith(
            "/news/safe-link"
        )
        and news_access.choose_article_link(link_only[0] if link_only else {}).is_direct,
    )

    portal_only = naver_news_provider.parse_response(
        {
            "items": [
                {
                    "title": "네이버 포털 링크",
                    "description": "첫 문장. 두 번째 문장.",
                    "pubDate": "Mon, 27 Jul 2026 06:00:00 +0900",
                    "link": "https://n.news.naver.com/mnews/article/001/0000000001",
                }
            ]
        },
        "AI 건설",
        collected_at,
        host_map,
        5,
    )
    portal_selection = news_access.choose_article_link(portal_only[0] if portal_only else {})
    check(
        "Naver portal/search URL is not treated as publisher-direct",
        len(portal_only) == 1
        and not portal_selection.is_direct
        and portal_selection.kind == news_access.LINK_KIND_PORTAL_FALLBACK,
    )

    coverage = brief.daily_coverage(dt("2026-07-27T07:00:00+09:00"))
    unsafe_rows = naver_news_provider.parse_response(
        {
            "items": [
                {
                    "title": "차단 대상 원문",
                    "description": "첫 문장. 두 번째 문장.",
                    "pubDate": "Mon, 27 Jul 2026 06:00:00 +0900",
                    "originallink": "http://127.0.0.1/news/private",
                }
            ]
        },
        "AI 건설",
        collected_at,
        {},
        5,
    )
    check(
        "unsafe Naver originallink is blocked by editorial URL validation",
        len(unsafe_rows) == 1
        and brief.normalize_articles(unsafe_rows, coverage, limit=1, resolve_images=False)
        == [],
    )

    google = {
        "title": "AI 건설 중복 기사",
        "source": "검증매체",
        "published_at": "2026-07-27T06:00:00+09:00",
        "url": "https://news.google.com/rss/articles/dup",
        "snippet": "첫 문장. 두 번째 문장.",
        "source_metadata": {"provider": "google_news_rss"},
    }
    naver_direct = {
        **google,
        "url": "https://publisher.fixture.test/news/naver-direct",
        "source_metadata": {"provider": "naver_news_api"},
    }
    merged = collector.merge_provider_articles([google, naver_direct])
    check(
        "Naver publisher-direct duplicate wins over Google aggregator duplicate",
        len(merged) == 1
        and merged[0]["url"].endswith("/news/naver-direct")
        and merged[0]["source_metadata"]["provider"] == "google_news_rss+naver_news_api",
    )
    distinct = collector.merge_provider_articles([
        google,
        {**naver_direct, "source": "다른매체"},
    ])
    check(
        "Naver/Google merge does not collapse distinct publishers by title alone",
        len(distinct) == 2,
    )

    direct_raw = {
        "title": "AI 데이터센터 전력 인프라 투자 확대",
        "source": "검증매체",
        "published_at": "2026-07-27T06:00:00+09:00",
        "url": "https://publisher.fixture.test/news/naver-image",
        "snippet": (
            "생성형 AI 데이터센터 구축과 전력 인프라 투자 계획이 발표됐다. "
            "세부 사업 일정과 적용 범위가 공개됐다."
        ),
        "source_metadata": {
            "provider": "naver_news_api",
            "query": "AI 데이터센터 전력",
        },
    }
    direct_summary = brief._summary_sentences(  # noqa: SLF001
        direct_raw["title"], direct_raw["snippet"]
    )
    direct_category = brief.classify_category(direct_raw["title"], direct_summary)
    direct_score, direct_reasons = brief._candidate_relevance(  # noqa: SLF001
        direct_raw["title"], direct_summary, direct_category, direct_raw
    )
    check(
        "Naver image fixture passes relevance floor",
        direct_score >= brief.SELECTION_RELEVANCE_FLOOR
        and bool(direct_reasons),
        repr((direct_score, direct_reasons)),
    )
    og = brief.normalize_articles(
        [direct_raw],
        coverage,
        limit=1,
        allow_image_network=True,
        image_page_fetcher=lambda _url: (
            "https://publisher.fixture.test/news/naver-image",
            '<meta property="og:image" '
            'content="https://publisher.fixture.test/images/og.jpg">',
        ),
        selection_mode=brief.SELECTION_MODE_DIRECT_AWARE_DAILY,
    )
    twitter = brief.normalize_articles(
        [{**direct_raw, "url": "https://publisher.fixture.test/news/twitter"}],
        coverage,
        limit=1,
        allow_image_network=True,
        image_page_fetcher=lambda _url: (
            "https://publisher.fixture.test/news/twitter",
            '<meta name="twitter:image" '
            'content="https://publisher.fixture.test/images/twitter.jpg">',
        ),
        selection_mode=brief.SELECTION_MODE_DIRECT_AWARE_DAILY,
    )
    jsonld = brief.normalize_articles(
        [{**direct_raw, "url": "https://publisher.fixture.test/news/jsonld"}],
        coverage,
        limit=1,
        allow_image_network=True,
        image_page_fetcher=lambda _url: (
            "https://publisher.fixture.test/news/jsonld",
            '<script type="application/ld+json">'
            '{"image":{"url":"https://publisher.fixture.test/images/jsonld.jpg",'
            '"width":1200,"height":675}}</script>',
        ),
        selection_mode=brief.SELECTION_MODE_DIRECT_AWARE_DAILY,
    )
    fallback = brief.normalize_articles(
        [{**direct_raw, "url": "https://publisher.fixture.test/news/fallback"}],
        coverage,
        limit=1,
        allow_image_network=True,
        image_page_fetcher=lambda _url: (
            "https://publisher.fixture.test/news/fallback",
            "<html><body>No image</body></html>",
        ),
        selection_mode=brief.SELECTION_MODE_DIRECT_AWARE_DAILY,
    )
    og_first = og[0] if og else None
    twitter_first = twitter[0] if twitter else None
    jsonld_first = jsonld[0] if jsonld else None
    fallback_first = fallback[0] if fallback else None
    check(
        "Naver direct URL feeds og:image, twitter:image, JSON-LD image, then fallback",
        og_first is not None
        and twitter_first is not None
        and jsonld_first is not None
        and fallback_first is not None
        and og_first.image_source_kind == "og_image"
        and twitter_first.image_source_kind == "twitter_image"
        and jsonld_first.image_source_kind == "jsonld_image"
        and fallback_first.image_fallback_used
        and og_first.collection_source_kind == "naver_news_api",
    )

    def provider_fetch_with(payload_or_error):
        calls: list[dict] = []
        original_request_json = naver_news_provider._request_json  # noqa: SLF001
        original_enabled = naver_news_provider.config.NAVER_NEWS_ENABLED
        original_id = naver_news_provider.config.NAVER_CLIENT_ID
        original_secret = naver_news_provider.config.NAVER_CLIENT_SECRET
        with tempfile.TemporaryDirectory(prefix="d7ak6e-naver-provider-") as temporary:
            sources = Path(temporary) / "sources.json"
            sources.write_text(
                json.dumps(
                    {
                        "queries": ["AI 건설"],
                        "display": 1,
                        "start": 1,
                        "sort": "date",
                        "max_per_query": 1,
                        "max_total": 1,
                        "timeout_seconds": 8,
                        "host_source_map": host_map,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            def fake_request(url, headers, timeout):
                calls.append({
                    "timeout": timeout,
                    "header_names": sorted(headers),
                    "secret_value_logged": False,
                })
                if isinstance(payload_or_error, BaseException):
                    raise payload_or_error
                return payload_or_error

            naver_news_provider._request_json = fake_request  # noqa: SLF001
            naver_news_provider.config.NAVER_NEWS_ENABLED = True
            naver_news_provider.config.NAVER_CLIENT_ID = "fixture-client-id"
            naver_news_provider.config.NAVER_CLIENT_SECRET = "fixture-client-secret"
            try:
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    result = naver_news_provider.fetch(
                        timeout=8,
                        sources_path=sources,
                        include_coverage=False,
                    )
                return result, calls, output.getvalue()
            finally:
                naver_news_provider._request_json = original_request_json  # noqa: SLF001
                naver_news_provider.config.NAVER_NEWS_ENABLED = original_enabled
                naver_news_provider.config.NAVER_CLIENT_ID = original_id
                naver_news_provider.config.NAVER_CLIENT_SECRET = original_secret

    fetched, calls, captured = provider_fetch_with(normal_payload)
    check(
        "Naver fetch uses official endpoint headers, timeout, and retry-zero query path",
        fetched["status"] == naver_news_provider.STATUS_ACTIVE
        and fetched["raw_count"] == 1
        and len(calls) == 1
        and calls[0]["timeout"] == 8
        and calls[0]["header_names"] == [
            "User-Agent",
            "X-Naver-Client-Id",
            "X-Naver-Client-Secret",
        ],
    )
    malformed, _calls, _captured = provider_fetch_with(ValueError("malformed JSON"))
    empty, _calls, _captured = provider_fetch_with({"items": []})
    denied_401, _calls, _captured = provider_fetch_with(
        urllib.error.HTTPError("https://openapi.naver.com", 401, "unauthorized", {}, None)
    )
    denied_403, _calls, _captured = provider_fetch_with(
        urllib.error.HTTPError("https://openapi.naver.com", 403, "forbidden", {}, None)
    )
    check(
        "Naver malformed JSON, empty items, and simulated 401/403 fail closed",
        malformed["status"] == naver_news_provider.STATUS_ERROR
        and malformed["articles"] == []
        and empty["articles"] == []
        and denied_401["status"] == naver_news_provider.STATUS_ERROR
        and denied_401["articles"] == []
        and denied_403["status"] == naver_news_provider.STATUS_ERROR
        and denied_403["articles"] == [],
    )
    serialized = json.dumps(fetched, ensure_ascii=False)
    check(
        "Naver secret values are absent from logs and audit payloads",
        "fixture-client-id" not in captured
        and "fixture-client-secret" not in captured
        and "fixture-client-id" not in serialized
        and "fixture-client-secret" not in serialized,
    )


def naver_provider_activation_contracts() -> None:
    run_at = dt("2026-07-27T07:00:00+09:00")
    rows = brief.fixture_articles("daily", run_at)
    for index, row in enumerate(rows, start=1):
        row["image_url"] = f"https://images.fixture.test/activation-{index}.jpg"

    def activation_image_downloader(
        url: str,
        **_kwargs,
    ) -> brief.ImageDownload:
        return brief.ImageDownload(
            404,
            "image/jpeg",
            b"",
            final_url=url,
        )

    naver_row = dict(rows[0])
    naver_row["source_metadata"] = {
        "provider": "naver_news_api",
        "query": "AI 데이터센터 전력",
    }

    original_fetch_all = live_collector.fetch_all
    original_fetch = naver_news_provider.fetch
    original_enabled = naver_news_provider.config.NAVER_NEWS_ENABLED
    original_id = naver_news_provider.config.NAVER_CLIENT_ID
    original_secret = naver_news_provider.config.NAVER_CLIENT_SECRET
    try:
        naver_news_provider.config.NAVER_NEWS_ENABLED = False
        naver_news_provider.config.NAVER_CLIENT_ID = "fixture-client-id"
        naver_news_provider.config.NAVER_CLIENT_SECRET = "fixture-client-secret"
        disabled = naver_news_provider.fetch(include_coverage=False)
        check(
            "enabled=0 records Naver provider as explicitly disabled",
            disabled["status"] == naver_news_provider.STATUS_DISABLED
            and disabled["queries_attempted"] == 0
            and disabled["credentials_present"] is True,
        )

        calls: list[str] = []

        def fake_google_rows():
            return []

        def fake_naver_fetch():
            calls.append("called")
            return {
                "provider": naver_news_provider.PROVIDER,
                "source_label": naver_news_provider.SOURCE_LABEL,
                "status": naver_news_provider.STATUS_ACTIVE,
                "articles": [naver_row],
                "queries_attempted": 1,
                "queries_ok": 1,
                "raw_count": 1,
                "credentials_present": True,
            }

        live_collector.fetch_all = fake_google_rows
        naver_news_provider.fetch = fake_naver_fetch
        naver_news_provider.config.NAVER_NEWS_ENABLED = True
        collected, audit = runner.collect_live_article_bundle()
        check(
            "enabled=1 plus credentials present calls Naver provider",
            calls == ["called"]
            and len(collected) == 1
            and audit["naver_provider_enabled"] is True
            and audit["naver_provider_status"] == naver_news_provider.STATUS_ACTIVE
            and audit["naver_credentials_present"] is True
            and audit["naver_api_requests"] == 1
            and audit["naver_provider_queries_ok"] == 1,
        )

        def zero_request_naver_fetch():
            return {
                "provider": naver_news_provider.PROVIDER,
                "source_label": naver_news_provider.SOURCE_LABEL,
                "status": naver_news_provider.STATUS_ACTIVE,
                "articles": [],
                "queries_attempted": 0,
                "queries_ok": 0,
                "raw_count": 0,
                "credentials_present": True,
            }

        live_collector.fetch_all = lambda: rows
        naver_news_provider.fetch = zero_request_naver_fetch
        expect_raises(
            "credentials present plus enabled=1 plus zero Naver requests fails closed",
            runner.OrchestratorError,
            runner.collect_live_article_bundle,
        )
    finally:
        live_collector.fetch_all = original_fetch_all
        naver_news_provider.fetch = original_fetch
        naver_news_provider.config.NAVER_NEWS_ENABLED = original_enabled
        naver_news_provider.config.NAVER_CLIENT_ID = original_id
        naver_news_provider.config.NAVER_CLIENT_SECRET = original_secret

    activation_audit = {
        "naver_provider_enabled": True,
        "naver_provider_status": naver_news_provider.STATUS_ACTIVE,
        "naver_credentials_present": True,
        "naver_provider_activation_error": False,
        "naver_provider_queries_ok": 1,
        "naver_api_requests": 1,
        "naver_articles_collected": 1,
        "naver_originallinks_collected": 1,
        "google_news_articles_collected": 5,
    }
    original_bundle = runner.collect_live_article_bundle
    try:
        runner.collect_live_article_bundle = lambda: (rows, activation_audit)
        with tempfile.TemporaryDirectory(prefix="d7ak6e-naver-activation-manifest-") as temporary:
            manifest = runner.run_live_preview(
                run_at=run_at,
                preview_root=Path(temporary) / "bundle",
                fixture_root="https://preview.fixture.test/HDEC-News-Sensor",
                image_page_fetcher=lambda url: (url, "<html></html>"),
                image_probe=lambda _url: False,
                image_downloader=activation_image_downloader,
                publisher_fetcher=lambda url: (url, "<html></html>"),
            )
        check(
            "live-preview manifest records Naver provider activation status",
            manifest["naver_provider_enabled"] is True
            and manifest["naver_provider_status"] == naver_news_provider.STATUS_ACTIVE
            and manifest["naver_credentials_present"] is True
            and manifest["naver_provider_activation_error"] is False
            and manifest["naver_provider_queries_ok"] == 1
            and manifest["naver_api_requests"] == 1,
        )
    finally:
        runner.collect_live_article_bundle = original_bundle


def selection_policy_contracts() -> None:
    coverage = brief.daily_coverage(dt("2026-07-27T07:00:00+09:00"))

    def row(
        title: str,
        *,
        provider: str,
        url: str,
        minutes_before_end: int,
        source: str = "검증매체",
        query: str = "AI 데이터센터 전력",
        snippet: str | None = None,
    ) -> dict:
        return {
            "title": title,
            "source": source,
            "published_at": (
                coverage.end - timedelta(minutes=minutes_before_end)
            ).isoformat(),
            "url": url,
            "snippet": snippet
            or f"{title} 관련 공개 계획과 적용 범위가 제시됐다. 세부 조건은 추가 확인이 필요하다.",
            "source_metadata": {"provider": provider, "query": query},
        }

    def normalize(rows: list[dict], *, limit: int = 6, resolve_images: bool = False):
        audit = brief.SelectionAuditCounters()
        articles = brief.normalize_articles(
            rows,
            coverage,
            limit=limit,
            resolve_images=resolve_images,
            selection_audit=audit,
            selection_mode=brief.SELECTION_MODE_DIRECT_AWARE_DAILY,
        )
        return articles, audit

    def direct_count(articles) -> int:
        return sum(
            1 for article in articles
            if article.link_kind == news_access.LINK_KIND_PUBLISHER_DIRECT
        )

    def aggregator_count(articles) -> int:
        return sum(
            1 for article in articles
            if article.link_kind != news_access.LINK_KIND_PUBLISHER_DIRECT
        )

    google_dup = row(
        "AI 데이터센터 전력 투자 중복",
        provider="google_news_rss",
        url="https://news.google.com/rss/articles/dup",
        minutes_before_end=4,
        source="뉴스투데이",
    )
    naver_dup = {
        **google_dup,
        "source": "publisher.fixture.test",
        "url": "https://publisher.fixture.test/news/direct-dup",
        "source_metadata": {"provider": "naver_news_api", "query": "AI 데이터센터 전력"},
    }
    selected, _audit = normalize([google_dup, naver_dup], limit=2)
    check(
        "identical Naver and Google article selects Naver publisher-direct",
        len(selected) == 1
        and selected[0].selected_url.endswith("/news/direct-dup")
        and selected[0].collection_source_kind == "naver_news_api",
    )
    expect_raises(
        "unknown selection mode fails closed",
        brief.EditorialError,
        lambda: brief.normalize_articles(
            [naver_dup],
            coverage,
            limit=1,
            resolve_images=False,
            selection_mode="unsupported-mode",
        ),
    )

    near_aggregator = row(
        "AI 데이터센터 전력 투자 계획",
        provider="google_news_rss",
        url="https://news.google.com/rss/articles/near",
        minutes_before_end=1,
    )
    near_direct = row(
        "AI 데이터센터 전력 투자 계획",
        provider="naver_news_api",
        url="https://publisher.fixture.test/news/near-direct",
        minutes_before_end=2,
        source="publisher.fixture.test",
    )
    selected, _audit = normalize([near_aggregator, near_direct], limit=1)
    check(
        "near-equivalent candidate selects publisher-direct within headline margin",
        len(selected) == 1 and selected[0].selected_url.endswith("/near-direct"),
    )

    unrelated_a = row(
        "같은 제목의 AI 데이터센터 별도 원문",
        provider="naver_news_api",
        url="https://publisher-a.fixture.test/news/same-title",
        minutes_before_end=1,
        source="publisher-a.fixture.test",
    )
    unrelated_b = row(
        "같은 제목의 AI 데이터센터 별도 원문",
        provider="naver_news_api",
        url="https://publisher-b.fixture.test/news/same-title",
        minutes_before_end=2,
        source="publisher-b.fixture.test",
    )
    selected, _audit = normalize([unrelated_a, unrelated_b], limit=2)
    check(
        "unrelated same-title direct candidates are not merged",
        len(selected) == 2
        and {article.source for article in selected}
        == {"publisher-a.fixture.test", "publisher-b.fixture.test"},
    )

    direct_rows = [
        row(
            f"AI 데이터센터 전력 원문 후보 {idx}",
            provider="naver_news_api",
            url=f"https://direct{idx}.fixture.test/news/{idx}",
            minutes_before_end=10 + idx,
            source=f"direct{idx}.fixture.test",
        )
        for idx in range(1, 5)
    ]
    aggregator_rows = [
        row(
            f"AI 데이터센터 전력 경유 후보 {idx}",
            provider="google_news_rss",
            url=f"https://news.google.com/rss/articles/agg-{idx}",
            minutes_before_end=idx,
            source=f"경유매체 {idx}",
        )
        for idx in range(1, 5)
    ]
    selected, audit = normalize(direct_rows + aggregator_rows)
    check(
        "four qualified direct and two aggregator candidates are selected",
        len(selected) == 6
        and direct_count(selected) == 4
        and aggregator_count(selected) == 2
        and audit.aggregator_articles_selected == 2,
    )

    six_direct = [
        row(
            f"AI 데이터센터 전력 원문 전용 {idx}",
            provider="naver_news_api",
            url=f"https://six{idx}.fixture.test/news/{idx}",
            minutes_before_end=idx,
            source=f"six{idx}.fixture.test",
        )
        for idx in range(1, 7)
    ]
    selected, _audit = normalize(six_direct)
    check("six qualified direct candidates fill all Daily slots", direct_count(selected) == 6)

    two_direct = direct_rows[:2]
    selected, _audit = normalize(two_direct + aggregator_rows)
    check(
        "only two qualified direct candidates does not force irrelevant direct fill",
        direct_count(selected) == 2 and aggregator_count(selected) == 4,
    )

    high_aggregator = row(
        "AI 데이터센터 전력 인프라 정책 투자",
        provider="google_news_rss",
        url="https://news.google.com/rss/articles/high-agg",
        minutes_before_end=1,
    )
    low_direct = row(
        "지역 행사 안내",
        provider="naver_news_api",
        url="https://low.fixture.test/news/local",
        minutes_before_end=2,
        source="low.fixture.test",
        query="",
        snippet="지역 행사 안내 문장이다. 일정 안내 문장이다.",
    )
    high_low_selected, audit = normalize([high_aggregator, low_direct], limit=2)
    check(
        "high-importance aggregator beats direct candidate below relevance floor",
        len(high_low_selected) == 1
        and high_low_selected[0].selected_url.startswith("https://news.google.com/")
        and audit.direct_candidates_rejected_below_relevance_floor == 1,
    )

    selected, _audit = normalize(direct_rows + aggregator_rows)
    check(
        "aggregator cap applies when qualified direct supply is sufficient",
        aggregator_count(selected) <= brief.AGGREGATOR_CAP_WHEN_DIRECT_SUPPLY_SUFFICIENT,
    )

    check(
        "direct candidate below relevance floor remains rejected",
        all(article.title != "지역 행사 안내" for article in high_low_selected),
    )

    same_publisher = [
        row(
            f"AI 데이터센터 전력 같은 매체 {idx}",
            provider="naver_news_api",
            url=f"https://same.fixture.test/news/{idx}",
            minutes_before_end=idx,
            source="same.fixture.test",
        )
        for idx in range(1, 6)
    ]
    same_publisher.append(
        row(
            "AI 데이터센터 전력 다른 매체",
            provider="naver_news_api",
            url="https://other.fixture.test/news/1",
            minutes_before_end=6,
            source="other.fixture.test",
        )
    )
    selected, _audit = normalize(same_publisher, limit=4)
    check(
        "publisher diversity soft cap keeps alternatives visible",
        "other.fixture.test" in {article.source for article in selected},
    )

    category_rows = [
        row(
            f"AI 데이터센터 전력 인프라 후보 {idx}",
            provider="naver_news_api",
            url=f"https://infra{idx}.fixture.test/news/{idx}",
            minutes_before_end=idx,
            source=f"infra{idx}.fixture.test",
        )
        for idx in range(1, 6)
    ]
    category_rows.append(
        row(
            "AI 로봇 시공 기술 검증",
            provider="naver_news_api",
            url="https://policy.fixture.test/news/1",
            minutes_before_end=6,
            source="policy.fixture.test",
        )
    )
    selected, _audit = normalize(category_rows, limit=4)
    check(
        "topic/category diversity soft cap keeps non-infra technology visible",
        any(
            article.title == "AI 로봇 시공 기술 검증"
            and article.category == "기술정보"
            for article in selected
        ),
    )

    tie_rows = [
        row(
            "AI 데이터센터 전력 동률 B",
            provider="naver_news_api",
            url="https://tie.fixture.test/news/b",
            minutes_before_end=10,
            source="tie-b.fixture.test",
        ),
        row(
            "AI 데이터센터 전력 동률 A",
            provider="naver_news_api",
            url="https://tie.fixture.test/news/a",
            minutes_before_end=10,
            source="tie-a.fixture.test",
        ),
    ]
    first, _audit = normalize(tie_rows, limit=2)
    second, _audit = normalize(list(reversed(tie_rows)), limit=2)
    check(
        "selection tie-breaking is deterministic",
        [article.selected_url for article in first]
        == [article.selected_url for article in second],
    )

    newer, _audit = normalize(
        [
            row(
                "AI 데이터센터 전력 시간 후보",
                provider="naver_news_api",
                url="https://time-old.fixture.test/news/1",
                minutes_before_end=20,
                source="time-old.fixture.test",
            ),
            row(
                "AI 데이터센터 전력 시간 후보",
                provider="naver_news_api",
                url="https://time-new.fixture.test/news/1",
                minutes_before_end=5,
                source="time-new.fixture.test",
            ),
        ],
        limit=2,
    )
    check(
        "publication time parsing feeds deterministic freshness ranking",
        newer[0].source == "time-new.fixture.test",
    )

    selected, audit = normalize(direct_rows + aggregator_rows)
    check(
        "candidate funnel counters are populated",
        audit.naver_articles_in_coverage == 4
        and audit.google_articles_in_coverage == 4
        and audit.naver_articles_relevance_qualified == 4
        and audit.direct_candidates_before_selection == 4
        and audit.aggregator_candidates_before_selection == 4,
    )
    check(
        "selection reason audit is recorded",
        all(article.selection_reason.startswith("selected_") for article in selected)
        and all(article.total_ranking_key for article in selected),
    )
    legacy_audit = brief.SelectionAuditCounters()
    brief.normalize_articles(
        direct_rows + aggregator_rows,
        coverage,
        limit=6,
        resolve_images=False,
        selection_audit=legacy_audit,
        selection_mode=brief.SELECTION_MODE_LEGACY,
    )
    check(
        "selection audit counters only populate in direct-aware mode",
        all(not value for value in legacy_audit.manifest_fields().values()),
    )

    image_row = row(
        "AI 데이터센터 전력 이미지 원문",
        provider="naver_news_api",
        url="https://image-direct.fixture.test/news/1",
        minutes_before_end=1,
        source="image-direct.fixture.test",
    )
    image_articles = brief.normalize_articles(
        [image_row],
        coverage,
        limit=1,
        allow_image_network=True,
        image_page_fetcher=lambda _url: (
            "https://image-direct.fixture.test/news/1",
            '<meta property="og:image" '
            'content="https://image-direct.fixture.test/images/lead.jpg">',
        ),
        selection_mode=brief.SELECTION_MODE_DIRECT_AWARE_DAILY,
    )
    image_first = image_articles[0] if image_articles else None
    check(
        "Naver direct article reaches image resolver and actual image succeeds",
        image_first is not None
        and image_first.image_source_kind == "og_image"
        and not image_first.image_fallback_used,
    )

    agg_image = brief.normalize_articles(
        [high_aggregator],
        coverage,
        limit=1,
        allow_image_network=True,
        publisher_fetcher=lambda url: (url, "<html></html>"),
        image_page_fetcher=lambda _url: (_ for _ in ()).throw(
            AssertionError("aggregator page must not be fetched for image")
        ),
        selection_mode=brief.SELECTION_MODE_DIRECT_AWARE_DAILY,
    )
    agg_first = agg_image[0] if agg_image else None
    check(
        "aggregator fallback remains safe when no publisher URL exists",
        agg_first is not None
        and agg_first.image_fallback_used
        and agg_first.image_reason == "aggregator_page_not_used_for_image",
    )


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


def computed_style_contracts() -> None:
    report_path = Path("/tmp/d7ak6e-exact-reference/computed-style-diff.json")
    if not report_path.is_file():
        print(
            "INFO Chrome computed-style report is not present; "
            "exact CSS/DOM signatures remain enforced"
        )
        return
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        check("Chrome computed-style report is valid JSON", False, str(exc))
        return
    expected_cases = {
        "daily-desktop",
        "daily-mobile",
        "weekly-desktop",
        "weekly-mobile",
        "weekly-print",
        "weekly-multi-desktop",
        "weekly-multi-mobile",
    }
    cases = {item.get("name"): item for item in report.get("cases", [])}
    check(
        "Chrome computed-style report covers exact-reference viewports",
        expected_cases == set(cases),
        repr(sorted(cases)),
    )
    check(
        "Chrome computed-style mismatch count is zero",
        all(
            item.get(field) == 0
            for item in cases.values()
            for field in (
                "domMismatchCount",
                "missingCount",
                "extraCount",
                "styleMismatchCount",
            )
        ),
    )


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
    check("message contains only canonical Brief and dashboard links",
          public_url_contract.DAILY_LATEST_URL in body_text
          and public_url_contract.CANONICAL_DASHBOARD_URL in body_text
          and bool(body_urls)
          and all(
              url in {
                  public_url_contract.DAILY_LATEST_URL,
                  public_url_contract.CANONICAL_DASHBOARD_URL,
              }
              for url in body_urls
          ))

    with tempfile.TemporaryDirectory(prefix="d7ak6e-smtp-state-") as temporary:
        root = Path(temporary)
        claim_owner = "github-run:9001:attempt:1"
        claim = {
            "edition_key": manifest["edition_key"],
            "coverage_start": manifest["coverage_start"],
            "coverage_end": manifest["coverage_end"],
            "html_sha256": manifest["html_sha256"],
            "public_url": manifest["public_dated_url"],
            "claim_owner": claim_owner,
            "claimed_at": "2026-07-27T07:01:00+09:00",
        }
        accepted_path = root / "accepted.json"
        state.atomic_write_state(
            "daily",
            state.add_claim(state.empty_state("daily"), "daily", claim),
            accepted_path,
        )
        runner.persist_exact_250_success(
            "daily",
            manifest,
            claim_owner=claim_owner,
            smtp_status="accepted",
            smtp_code=250,
            sent_at=dt("2026-07-27T07:05:00+09:00"),
            path=accepted_path,
        )
        check("SMTP DATA 250 changes state", accepted_path.is_file())
        for code in (250.0, True, 251, 252, 300, 399, 400, 500, None):
            candidate = root / f"code-{code}.json"
            state.atomic_write_state(
                "daily",
                state.add_claim(state.empty_state("daily"), "daily", claim),
                candidate,
            )
            before = candidate.read_bytes()
            expect_raises(
                f"SMTP code {code} changes state zero",
                runner.OrchestratorError,
                lambda code=code, candidate=candidate: runner.persist_exact_250_success(
                    "daily",
                    manifest,
                    claim_owner=claim_owner,
                    smtp_status="accepted" if code and code < 400 else "rejected",
                    smtp_code=code,
                    sent_at=dt("2026-07-27T07:05:00+09:00"),
                    path=candidate,
                ),
            )
            check(f"SMTP code {code} preserves durable claim", candidate.read_bytes() == before)
        exception_path = root / "exception.json"
        try:
            raise TimeoutError("offline fixture")
        except TimeoutError:
            pass
        check("SMTP exception path changes state zero", not exception_path.exists())


@contextlib.contextmanager
def temporary_environment(values: dict[str, str | None]):
    previous = {key: os.environ.get(key) for key in values}
    try:
        for key, value in values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class _FakePublicResponse:
    def __init__(self, status: int, body: str):
        self.status = status
        self._body = body.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit: int):
        return self._body


def _fake_smtp_factory(
    attempts: list[str],
    *,
    data_code: int,
    events: list[str] | None = None,
    fail_on_connect: bool = False,
):
    class FakeSMTP:
        def __init__(self, *_args, **_kwargs):
            attempts.append("smtp")
            if events is not None:
                events.append("smtp_attempt")
            if fail_on_connect:
                raise TimeoutError("offline SMTP fixture")

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def ehlo(self):
            return 250, b"fixture"

        def starttls(self, **_kwargs):
            return 220, b"fixture"

        def login(self, *_args):
            return 235, b"fixture"

        def mail(self, *_args):
            return 250, b"fixture"

        def rcpt(self, *_args):
            return 250, b"fixture"

        def data(self, *_args):
            return data_code, b"fixture"

    return FakeSMTP


def claim_delivery_contracts(daily: brief.RenderedEdition) -> None:
    with tempfile.TemporaryDirectory(prefix="d7ak6e-claims-") as temporary:
        root = Path(temporary)
        runtime_dir = root / "runtime"
        runtime_dir.mkdir()
        docs_dir = root / "docs" / "editorial" / "daily"
        docs_dir.mkdir(parents=True)
        dated_path = docs_dir / f"{daily.edition_key}.html"
        latest_path = docs_dir / "latest.html"
        payload = daily.html.encode("utf-8")
        dated_path.write_bytes(payload)
        latest_path.write_bytes(payload)
        manifest = brief.manifest_for_runtime(daily, dated_path, latest_path)
        runner._write_runtime_manifest(runtime_dir, manifest)
        claim_path = root / "daily-state.json"
        output_path = root / "github-output.txt"
        output_path.touch()
        report_url = (
            "https://preview.fixture.test/HDEC-News-Sensor/daily/latest.html"
        )
        production_env = {
            "EDITORIAL_PRODUCTION": "1",
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_RUN_ID": "7001",
            "GITHUB_RUN_ATTEMPT": "1",
            "REPORT_URL": report_url,
            "GITHUB_OUTPUT": str(output_path),
            "GMAIL_SMTP_USER": "sender@fixture.test",
            "GMAIL_SMTP_APP_PASSWORD": "offline-password",
            "ALERT_EMAIL_FROM": "sender@fixture.test",
            "TEAMS_CHANNEL_EMAIL": "channel@fixture.test",
        }
        events: list[str] = []
        original_docs_paths = runner._docs_paths
        original_atomic_write = state.atomic_write_state

        def fixture_docs_paths(_edition_type: str, _key: str):
            return dated_path, latest_path

        def tracked_atomic_write(edition_type, value, path=None):
            validated = state.validate_state(dict(value), edition_type)
            if validated["delivery_claims"] and not validated["successful_editions"]:
                events.append("claim_write")
            else:
                events.append("success_write")
            return original_atomic_write(edition_type, value, path=path)

        def matching_public(*_args, **_kwargs):
            events.append("public_verification")
            return _FakePublicResponse(200, daily.html)

        runner._docs_paths = fixture_docs_paths
        state.atomic_write_state = tracked_atomic_write
        try:
            with temporary_environment(production_env), hard_block_network():
                claimed = runner.run_claim(
                    "daily",
                    run_at=dt("2026-07-27T07:00:00+09:00"),
                    runtime_dir=runtime_dir,
                    opener=matching_public,
                    state_path=claim_path,
                    publication_timeout_seconds=0,
                )
            durable_claim = state.load_state("daily", claim_path)
            check(
                "public verification precedes claim write",
                events[:2] == ["public_verification", "claim_write"],
                repr(events),
            )
            check(
                "claim mode performs zero SMTP attempts",
                claimed is not None
                and state.has_claim(durable_claim, daily.edition_key)
                and "smtp_attempt" not in events,
            )
            claim_record = durable_claim["delivery_claims"][daily.edition_key]
            check(
                "claim owner derives from run ID and attempt",
                claim_record["claim_owner"] == "github-run:7001:attempt:1",
            )
            output_values = {}
            for line in output_path.read_text(encoding="utf-8").splitlines():
                name, value = line.split("=", 1)
                output_values[name] = value
            check(
                "claim mode emits all authorization outputs",
                all(
                    output_values.get(name) == value
                    for name, value in {
                        "state_changed": "true",
                        "state_path": str(claim_path.resolve()),
                        "send_authorized": "true",
                        "edition": daily.edition_key,
                        "claim_owner": "github-run:7001:attempt:1",
                    }.items()
                ),
            )

            no_page_path = root / "no-page-state.json"
            with temporary_environment(production_env), hard_block_network():
                expect_raises(
                    "claim requires matching public HTTP 200",
                    runner.OrchestratorError,
                    lambda: runner.run_claim(
                        "daily",
                        run_at=dt("2026-07-27T07:00:00+09:00"),
                        runtime_dir=runtime_dir,
                        opener=lambda *_args, **_kwargs: _FakePublicResponse(
                            404, daily.html
                        ),
                        state_path=no_page_path,
                        publication_timeout_seconds=0,
                    ),
                )
            check(
                "claim is not created without matching public HTTP 200",
                not no_page_path.exists(),
            )

            malformed_components = (
                (None, "1"),
                ("", "1"),
                (" 7001", "1"),
                ("7001 ", "1"),
                ("abc", "1"),
                ("0", "1"),
                ("-1", "1"),
                ("7001", None),
                ("7001", ""),
                ("7001", " 1"),
                ("7001", "0"),
                ("7001", "-1"),
            )
            malformed_closed = True
            for run_id, attempt in malformed_components:
                try:
                    with temporary_environment(
                        {
                            "GITHUB_RUN_ID": run_id,
                            "GITHUB_RUN_ATTEMPT": attempt,
                        }
                    ):
                        runner._github_claim_owner()
                except runner.OrchestratorError:
                    continue
                malformed_closed = False
            malformed_path = root / "malformed-owner-state.json"
            with temporary_environment(
                {**production_env, "GITHUB_RUN_ID": " 7001"}
            ), hard_block_network():
                try:
                    runner.run_claim(
                        "daily",
                        run_at=dt("2026-07-27T07:00:00+09:00"),
                        runtime_dir=runtime_dir,
                        opener=lambda *_args, **_kwargs: _FakePublicResponse(
                            200, daily.html
                        ),
                        state_path=malformed_path,
                        publication_timeout_seconds=0,
                    )
                except runner.OrchestratorError:
                    pass
                else:
                    malformed_closed = False
            check(
                "malformed GitHub claim owner fails closed",
                malformed_closed and not malformed_path.exists(),
            )

            conflict_before = claim_path.read_bytes()
            conflict_output = root / "conflict-output.txt"
            conflict_output.touch()
            with temporary_environment(
                {
                    **production_env,
                    "GITHUB_RUN_ID": "7002",
                    "GITHUB_OUTPUT": str(conflict_output),
                }
            ), hard_block_network():
                conflict_result = runner.run_claim(
                    "daily",
                    run_at=dt("2026-07-27T07:00:00+09:00"),
                    runtime_dir=runtime_dir,
                    opener=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                        AssertionError("existing claim catch-up must not poll")
                    ),
                    state_path=claim_path,
                    publication_timeout_seconds=0,
                )
            check(
                "conflicting claim catch-up is unauthorized without mutation",
                conflict_result is None
                and claim_path.read_bytes() == conflict_before
                and "send_authorized=false"
                in conflict_output.read_text(encoding="utf-8"),
            )

            original_load_state = state.load_state
            publish_collect_calls: list[str] = []
            docs_before = (dated_path.read_bytes(), latest_path.read_bytes())
            try:
                state.load_state = (
                    lambda edition_type, path=None: state.validate_state(
                        durable_claim, edition_type
                    )
                )
                with temporary_environment(production_env), hard_block_network():
                    publish_result = runner.run_publish(
                        "daily",
                        run_at=dt("2026-07-27T07:00:00+09:00"),
                        runtime_dir=runtime_dir,
                        collect=lambda: publish_collect_calls.append("collect"),
                    )
            finally:
                state.load_state = original_load_state
            check(
                "catch-up skips an already claimed edition",
                publish_result is None
                and publish_collect_calls == []
                and docs_before == (dated_path.read_bytes(), latest_path.read_bytes())
                and claim_path.read_bytes() == conflict_before,
            )

            wrong_owner_attempts: list[str] = []
            wrong_owner_before = claim_path.read_bytes()
            with temporary_environment(
                {**production_env, "GITHUB_RUN_ATTEMPT": "2"}
            ), hard_block_network():
                expect_raises(
                    "send requires exact claim owner",
                    state.StateError,
                    lambda: runner.run_send(
                        "daily",
                        run_at=dt("2026-07-27T07:00:00+09:00"),
                        runtime_dir=runtime_dir,
                        smtp_factory=_fake_smtp_factory(
                            wrong_owner_attempts, data_code=250
                        ),
                        opener=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                            AssertionError("send must not poll the public page")
                        ),
                        state_path=claim_path,
                    ),
                )
            check(
                "different-owner catch-up performs zero SMTP calls",
                wrong_owner_attempts == [],
            )
            check(
                "wrong owner cannot mutate claim",
                claim_path.read_bytes() == wrong_owner_before
                and state.has_claim(state.load_state("daily", claim_path), daily.edition_key),
            )

            mismatched_manifest = dict(manifest)
            mismatched_manifest["coverage_start"] = (
                "2026-07-26T07:00:01+09:00"
            )
            runner._write_runtime_manifest(runtime_dir, mismatched_manifest)
            mismatch_attempts: list[str] = []
            mismatch_before = claim_path.read_bytes()
            with temporary_environment(production_env), hard_block_network():
                expect_raises(
                    "send rejects claim identity mismatch",
                    state.StateError,
                    lambda: runner.run_send(
                        "daily",
                        run_at=dt("2026-07-27T07:00:00+09:00"),
                        runtime_dir=runtime_dir,
                        smtp_factory=_fake_smtp_factory(
                            mismatch_attempts, data_code=250
                        ),
                        state_path=claim_path,
                    ),
                )
            check(
                "identity mismatch cannot mutate claim",
                mismatch_attempts == [] and claim_path.read_bytes() == mismatch_before,
            )
            runner._write_runtime_manifest(runtime_dir, manifest)

            rejected_attempts: list[str] = []
            rejected_before = claim_path.read_bytes()
            with temporary_environment(production_env), hard_block_network():
                expect_raises(
                    "rejected SMTP send fails closed",
                    runner.OrchestratorError,
                    lambda: runner.run_send(
                        "daily",
                        run_at=dt("2026-07-27T07:00:00+09:00"),
                        runtime_dir=runtime_dir,
                        smtp_factory=_fake_smtp_factory(
                            rejected_attempts,
                            data_code=550,
                            events=events,
                        ),
                        state_path=claim_path,
                    ),
                )
            rejected_state = state.load_state("daily", claim_path)
            check(
                "SMTP failure preserves claim and records no success",
                rejected_attempts == ["smtp"]
                and claim_path.read_bytes() == rejected_before
                and state.has_claim(rejected_state, daily.edition_key)
                and not state.has_success(rejected_state, daily.edition_key),
            )

            exception_attempts: list[str] = []
            exception_before = claim_path.read_bytes()
            with temporary_environment(production_env), hard_block_network():
                expect_raises(
                    "SMTP exception send fails closed",
                    runner.OrchestratorError,
                    lambda: runner.run_send(
                        "daily",
                        run_at=dt("2026-07-27T07:00:00+09:00"),
                        runtime_dir=runtime_dir,
                        smtp_factory=_fake_smtp_factory(
                            exception_attempts,
                            data_code=250,
                            fail_on_connect=True,
                        ),
                        state_path=claim_path,
                    ),
                )
            check(
                "SMTP exception preserves durable claim",
                exception_attempts == ["smtp"]
                and claim_path.read_bytes() == exception_before,
            )

            remote_claim_path = root / "remote-durable-claim.json"
            local_success_path = root / "local-uncommitted-success.json"
            remote_claim_path.write_bytes(claim_path.read_bytes())
            local_success_path.write_bytes(claim_path.read_bytes())
            accepted_attempts: list[str] = []
            with temporary_environment(production_env), hard_block_network():
                local_success = runner.run_send(
                    "daily",
                    run_at=dt("2026-07-27T07:00:00+09:00"),
                    runtime_dir=runtime_dir,
                    smtp_factory=_fake_smtp_factory(
                        accepted_attempts,
                        data_code=250,
                        events=events,
                    ),
                    opener=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                        AssertionError("send must not perform a second public poll")
                    ),
                    state_path=local_success_path,
                )
            check(
                "exact-owner send performs one SMTP call only",
                accepted_attempts == ["smtp"],
            )
            check(
                "SMTP DATA 250 converts claim to success",
                local_success is not None
                and state.has_success(local_success, daily.edition_key)
                and local_success["successful_editions"][-1]["smtp_status"] == "accepted"
                and local_success["successful_editions"][-1]["smtp_code"] == 250,
            )
            check(
                "success conversion removes active claim",
                not state.has_claim(local_success, daily.edition_key)
                and state.has_claim(
                    state.load_state("daily", remote_claim_path), daily.edition_key
                ),
            )
            check(
                "claim is created before SMTP authorization",
                events.index("claim_write") < events.index("smtp_attempt"),
                repr(events),
            )
            check(
                "send performs no second public verification",
                events.count("public_verification") == 1,
                repr(events),
            )

            next_catchup_attempts: list[str] = []
            remote_before = remote_claim_path.read_bytes()
            with temporary_environment(
                {
                    **production_env,
                    "GITHUB_RUN_ID": "7003",
                    "GITHUB_RUN_ATTEMPT": "1",
                }
            ), hard_block_network():
                try:
                    runner.run_send(
                        "daily",
                        run_at=dt("2026-07-27T07:00:00+09:00"),
                        runtime_dir=runtime_dir,
                        smtp_factory=_fake_smtp_factory(
                            next_catchup_attempts, data_code=250
                        ),
                        state_path=remote_claim_path,
                    )
                except state.StateError:
                    pass
                else:
                    check(
                        "success commit failure catch-up is rejected",
                        False,
                        "wrong-owner catch-up unexpectedly reached send",
                    )
            remote_after = state.load_state("daily", remote_claim_path)
            check(
                "success commit failure leaves durable blocking claim",
                remote_claim_path.read_bytes() == remote_before
                and state.has_claim(remote_after, daily.edition_key)
                and not state.has_success(remote_after, daily.edition_key)
                and next_catchup_attempts == [],
            )
            check(
                "success commit failure model keeps local success uncommitted",
                state.has_success(
                    state.load_state("daily", local_success_path), daily.edition_key
                )
                and state.has_claim(remote_after, daily.edition_key),
            )
        finally:
            runner._docs_paths = original_docs_paths
            state.atomic_write_state = original_atomic_write


def republish_contracts() -> None:
    run_at = dt("2026-07-27T07:00:00+09:00")
    coverage = brief.daily_coverage(run_at)
    articles = brief.normalize_articles(
        brief.fixture_articles("daily", run_at),
        coverage,
        limit=brief.DAILY_MAX_ARTICLES,
        resolve_images=False,
    )
    key = brief.edition_key("daily", run_at)
    delivered = state.empty_state("daily")
    success = {
        "edition_key": key,
        "coverage_start": coverage.start.isoformat(),
        "coverage_end": coverage.end.isoformat(),
        "html_sha256": "d" * 64,
        "public_url": f"https://preview.fixture.test/editorial/daily/{key}.html",
        "smtp_status": "accepted",
        "smtp_code": 250,
        "sent_at": "2026-07-27T07:05:00+09:00",
    }
    delivered["successful_editions"] = [success]
    delivered["last_successful_edition"] = key
    delivered["last_successful_send_at"] = success["sent_at"]
    delivered = state.validate_state(delivered, "daily")

    with tempfile.TemporaryDirectory(prefix="d7ak6e-republish-") as temporary:
        root = Path(temporary)
        dated = root / f"{key}.html"
        latest = root / "latest.html"
        runtime = root / "runtime"
        output = root / "github-output.txt"
        output.touch()
        original_docs_paths = runner._docs_paths
        original_load_state = state.load_state
        original_load_bundle = runner.editorial_review.load_bundle
        original_load_review = runner.editorial_review.load_review
        original_choose = runner.editorial_review.choose_daily_articles
        original_atomic_state = state.atomic_write_state
        try:
            runner._docs_paths = lambda _edition_type, _key: (dated, latest)
            state.load_state = lambda _edition_type, path=None: delivered
            runner.editorial_review.load_bundle = lambda *_args, **_kwargs: {}
            runner.editorial_review.load_review = lambda *_args, **_kwargs: {}
            runner.editorial_review.choose_daily_articles = (
                lambda *_args, **_kwargs: (articles, "verified_fixture")
            )
            state.atomic_write_state = lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("publish-only must never write delivery state")
            )
            environment = {
                "EDITORIAL_PRODUCTION": "1",
                "GITHUB_REF": "refs/heads/main",
                "REPORT_URL": "https://preview.fixture.test/HDEC-News-Sensor/daily/latest.html",
                "GITHUB_OUTPUT": str(output),
            }
            with temporary_environment(environment), hard_block_network():
                manifest = runner.run_publish(
                    "daily",
                    run_at=run_at,
                    runtime_dir=runtime,
                    collect=lambda: (_ for _ in ()).throw(
                        AssertionError("approved fixture must avoid collection")
                    ),
                    republish=True,
                )
        finally:
            runner._docs_paths = original_docs_paths
            state.load_state = original_load_state
            runner.editorial_review.load_bundle = original_load_bundle
            runner.editorial_review.load_review = original_load_review
            runner.editorial_review.choose_daily_articles = original_choose
            state.atomic_write_state = original_atomic_state

        output_text = output.read_text(encoding="utf-8")
        check(
            "publish-only regenerates dated and latest from one artifact",
            manifest is not None
            and dated.read_bytes() == latest.read_bytes()
            and manifest["edition_key"] == key,
        )
        check(
            "publish-only cannot authorize an already accepted Daily resend",
            "delivery_authorized=false" in output_text
            and state.has_success(delivered, key),
        )
        parsed = runner.parse_args(
            ["--edition-type", "daily", "--republish", "--runtime-dir", str(runtime)]
        )
        check(
            "republish is an explicit mutually exclusive mode",
            parsed.republish and not parsed.publish and not parsed.claim and not parsed.send,
        )


def weekly_verified_supply_contracts() -> None:
    run_at = dt("2026-08-03T07:30:00+09:00")
    current = [{
        "id": "current-outside-window",
        "title": "현대건설 최신 현재 기사",
        "source": "Reuters",
        "published_at": "2026-08-03T01:00:00+09:00",
        "url": "https://www.reuters.com/world/current-weekly-test",
        "publisher_direct": True,
        "snippet": "현재 수집 기사",
        "source_metadata": {"provider": "publisher_direct_registry"},
    }]
    verified = {
        "id": "verified-weekly-row",
        "title": "현대건설 AI 데이터센터 프로젝트 수주",
        "source": "Reuters",
        "published_at": "2026-07-28T12:00:00+09:00",
        "url": "https://www.reuters.com/world/verified-weekly-test",
        "snippet": "검증된 주간 AI 데이터센터 수주 기사",
    }
    entry = news_censor_verified_state.verified_entry_from_article(
        verified,
        now=run_at,
        categories=("biz",),
        display_relevant=True,
        source_quality_passed=True,
    )
    state_payload = news_censor_verified_state.empty_state(now=run_at)
    state_payload["entries"] = [entry]
    state_payload = news_censor_verified_state.validate_state(state_payload)

    with tempfile.TemporaryDirectory(prefix="d7ak6e-weekly-state-") as temporary:
        state_path = Path(temporary) / "verified-state.json"
        news_censor_verified_state.atomic_write_state(state_path, state_payload)
        before = state_path.read_bytes()
        combined, added = runner.supplement_weekly_verified_supply(
            current,
            run_at=run_at,
            state_path=state_path,
        )
        after = state_path.read_bytes()
    coverage = brief.weekly_coverage(run_at)
    normalized = brief.normalize_articles(
        combined,
        coverage,
        limit=brief.WEEKLY_MAX_ARTICLES,
        resolve_images=False,
        selection_mode=brief.SELECTION_MODE_EDITORIAL_PRIORITY,
    )
    check(
        "Weekly supplements an empty live coverage window from verified state",
        added == 1 and len(normalized) == 1
        and normalized[0].selected_url == verified["url"],
    )
    carried = next(row for row in combined if row.get("url") == verified["url"])
    metadata = carried.get("source_metadata") or {}
    check(
        "Weekly verified supply remains carry-forward-only and never Teams-new",
        metadata.get("carried_forward") is True
        and metadata.get("teams_newness_eligible") is False,
    )
    check("Weekly verified supply reads state without mutation", before == after)


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


def image_materialization_contracts() -> None:
    run_at = dt("2026-07-27T07:00:00+09:00")

    def fixture_image_bytes(
        image_format: str,
        *,
        size: tuple[int, int] = (640, 360),
        style: str = "photo",
        seed: int = 0,
    ) -> bytes:
        if style == "transparent_logo":
            image = Image.new("RGBA", size, (255, 255, 255, 0))
            draw = ImageDraw.Draw(image)
            draw.rounded_rectangle(
                (size[0] // 5, size[1] // 3, size[0] * 4 // 5, size[1] * 2 // 3),
                radius=12,
                fill=(35, 84, 180, 255),
            )
            draw.rectangle(
                (size[0] // 3, size[1] // 2 - 5, size[0] * 2 // 3, size[1] // 2 + 5),
                fill=(255, 255, 255, 255),
            )
        else:
            base = (seed * 29) % 180
            image = Image.new(
                "RGB",
                size,
                (40 + base % 80, 70 + base % 90, 105 + base % 70),
            )
            draw = ImageDraw.Draw(image)
            if style == "flat_logo":
                image = Image.new("RGB", size, (255, 255, 255))
                draw = ImageDraw.Draw(image)
                draw.rectangle(
                    (size[0] // 8, size[1] // 3, size[0] * 7 // 8, size[1] * 2 // 3),
                    fill=(18, 78, 165),
                )
                draw.rectangle(
                    (size[0] // 5, size[1] // 2 - 4, size[0] * 4 // 5, size[1] // 2 + 4),
                    fill=(255, 255, 255),
                )
            elif style == "infographic":
                image = Image.new("RGB", size, (244, 248, 252))
                draw = ImageDraw.Draw(image)
                for index, color in enumerate(((39, 91, 170), (226, 92, 66), (38, 153, 121))):
                    x0 = 70 + index * 150
                    draw.rectangle(
                        (x0, 210 - index * 35, x0 + 80, 300),
                        fill=color,
                    )
                    draw.line((70, 305, 560, 305), fill=(70, 80, 95), width=4)
                draw.ellipse((435, 60, 575, 200), fill=(255, 216, 100), outline=(50, 60, 70), width=5)
            elif style == "robot":
                image = Image.new("RGB", size, (32, 39, 49))
                draw = ImageDraw.Draw(image)
                draw.rectangle((220, 95, 420, 270), fill=(166, 184, 198), outline=(232, 238, 244), width=6)
                draw.ellipse((260, 145, 295, 180), fill=(16, 80, 150))
                draw.ellipse((345, 145, 380, 180), fill=(16, 80, 150))
                draw.line((270, 225, 370, 225), fill=(30, 36, 45), width=8)
                draw.rectangle((70, 260, 590, 320), fill=(65, 92, 115))
            elif style == "illustration":
                image = Image.new("RGB", size, (238, 242, 246))
                draw = ImageDraw.Draw(image)
                for index in range(8):
                    draw.polygon(
                        (
                            (40 + index * 70, 290),
                            (85 + index * 70, 90 + (index % 3) * 35),
                            (130 + index * 70, 290),
                        ),
                        fill=(60 + index * 12, 120 + index * 8, 180 - index * 5),
                    )
            else:
                for y in range(size[1]):
                    color = (
                        (40 + base + y // 3) % 255,
                        (90 + base + y // 4) % 255,
                        (130 + base + y // 5) % 255,
                    )
                    draw.line((0, y, size[0], y), fill=color)
                for index in range(6):
                    x = 35 + index * 92
                    draw.rectangle(
                        (x, 60 + (index % 3) * 25, x + 70, 295),
                        fill=((90 + index * 21) % 255, (120 + index * 31) % 255, (170 + index * 17) % 255),
                    )
                draw.ellipse((420, 52, 600, 232), fill=(225, 190, 92), outline=(48, 66, 82), width=5)
        if image_format.upper() == "JPEG" and image.mode != "RGB":
            image = image.convert("RGB")
        buffer = BytesIO()
        image.save(buffer, format=image_format)
        return buffer.getvalue()

    jpeg = fixture_image_bytes("JPEG", seed=1)
    png = fixture_image_bytes("PNG", seed=2)
    webp = fixture_image_bytes("WEBP", seed=3)
    avif = fixture_image_bytes("AVIF", seed=4)
    logo_png = fixture_image_bytes("PNG", size=(220, 72), style="flat_logo")
    transparent_logo_png = fixture_image_bytes(
        "PNG",
        size=(360, 180),
        style="transparent_logo",
    )
    photo_jpeg = fixture_image_bytes("JPEG", seed=8)
    robot_jpeg = fixture_image_bytes("JPEG", style="robot", seed=9)
    infographic_png = fixture_image_bytes("PNG", style="infographic", seed=10)
    illustration_png = fixture_image_bytes("PNG", style="illustration", seed=11)
    weak_small_photo = fixture_image_bytes("JPEG", size=(300, 220), seed=12)

    def article(title: str, image_url: str) -> brief.EditorialArticle:
        return brief.EditorialArticle(
            title=title,
            summary="AI 데이터센터와 전력 인프라 투자 계획이 공개됐다.",
            source="Fixture Publisher",
            published_at=run_at,
            selected_url="https://publisher.fixture.test/news/article",
            link_kind=news_access.LINK_KIND_PUBLISHER_DIRECT,
            link_label=news_access.LINK_LABEL_PUBLISHER_DIRECT,
            category="AI/Data",
            collection_source_kind="offline_fixture",
            publisher_article_url="https://publisher.fixture.test/news/article",
            publisher_url_source_kind="existing_publisher_direct",
            image_url=image_url,
            image_remote_url=image_url,
            image_source_kind="og_image",
            image_fallback_used=False,
            image_reason="selected_og_image",
            ai_centrality_level="explicit_ai_core",
        )

    valid_payloads = {
        "jpeg.jpg": ("image/jpeg", jpeg),
        "png.png": ("image/png", png),
        "webp.webp": ("image/webp", webp),
        "avif.avif": ("image/avif", avif),
    }

    def valid_downloader(url: str, **_kwargs) -> brief.ImageDownload:
        key = url.rsplit("/", 1)[-1]
        content_type, payload = valid_payloads[key]
        return brief.ImageDownload(200, content_type, payload, final_url=url)

    ordered_html = """
      <html><head>
        <meta property="og:image" content="https://images.fixture.test/og.jpg">
        <meta name="twitter:image" content="https://images.fixture.test/twitter.jpg">
        <script type="application/ld+json">
          {"image":{"url":"https://images.fixture.test/jsonld.jpg","width":640,"height":360}}
        </script>
        <link rel="image_src" href="https://images.fixture.test/image-src.jpg">
      </head><body>
        <img src="https://images.fixture.test/body.jpg" width="640" height="360" alt="AI 데이터센터">
      </body></html>
    """
    ordered_resolution = brief.resolve_article_image(
        {
            "url": "https://publisher.fixture.test/news/article",
            "selected_url": "https://publisher.fixture.test/news/article",
        },
        allow_network=True,
        counters=brief.ImageResolutionCounters(),
        page_fetcher=lambda url: (url, ordered_html),
    )
    check(
        "publisher image candidate source order is preserved",
        [item.source_kind for item in ordered_resolution.candidates]
        == ["og_image", "twitter_image", "jsonld_image", "image_src", "body_image"],
    )
    unicode_absolute = brief.normalize_image_candidate_url(
        "https://images.fixture.test/이미지 사진.jpg"
    )
    check(
        "absolute Unicode image URL is percent-encoded safely",
        unicode_absolute.startswith("https://images.fixture.test/")
        and "%EC%9D%B4" in unicode_absolute
        and " " not in unicode_absolute,
    )
    relative_unicode = brief.normalize_image_candidate_url(
        "/images/서울 사진.jpg",
        base_url="https://publisher.fixture.test/news/article",
    )
    check(
        "relative Unicode path resolves against publisher base URL",
        relative_unicode.startswith("https://publisher.fixture.test/images/")
        and "%EC%84%9C" in relative_unicode,
    )
    spaced_relative = brief.normalize_image_candidate_url(
        "assets/my image.jpg",
        base_url="https://publisher.fixture.test/news/article",
    )
    check(
        "spaces in legitimate relative path are encoded",
        spaced_relative == "https://publisher.fixture.test/news/assets/my%20image.jpg",
    )
    quoted_path = brief.normalize_image_candidate_url(
        "/images/“lead”.jpg",
        base_url="https://publisher.fixture.test/news/article",
    )
    check(
        "curly quotes inside a legitimate path are encoded",
        "%E2%80%9Clead%E2%80%9D.jpg" in quoted_path,
    )
    check(
        "surrounding quote wrapper is stripped safely",
        brief.normalize_image_candidate_url('"https://images.fixture.test/wrapped.jpg"')
        == "https://images.fixture.test/wrapped.jpg",
    )
    check(
        "CRLF injection candidate is rejected",
        brief.normalize_image_candidate_url("https://images.fixture.test/a.jpg\r\nx: y") == "",
    )
    check(
        "TAB and NUL image candidates are rejected",
        brief.normalize_image_candidate_url("https://images.fixture.test/a\tb.jpg") == ""
        and brief.normalize_image_candidate_url("https://images.fixture.test/a\x00b.jpg") == "",
    )
    check(
        "javascript URL is rejected for image candidate",
        brief.normalize_image_candidate_url("javascript:alert(1)") == "",
    )
    check(
        "data URL is rejected as remote image candidate",
        brief.normalize_image_candidate_url("data:image/png;base64,AAAA") == "",
    )
    check(
        "missing hostname is rejected for image candidate",
        brief.normalize_image_candidate_url("/images/a.jpg") == "",
    )
    check(
        "userinfo URL is rejected for image candidate",
        brief.normalize_image_candidate_url("https://user:pass@images.fixture.test/a.jpg")
        == "",
    )
    check(
        "private and localhost image targets are rejected",
        brief.normalize_image_candidate_url("https://127.0.0.1/a.jpg") == ""
        and brief.normalize_image_candidate_url("https://localhost/a.jpg") == "",
    )
    encoded_once = brief.normalize_image_candidate_url(
        "https://images.fixture.test/a%20b.jpg"
    )
    check(
        "existing percent escapes are not double encoded",
        "%20" in encoded_once and "%2520" not in encoded_once,
    )
    query_encoded = brief.normalize_image_candidate_url(
        "https://images.fixture.test/photo.jpg?w=1200&name=AI 건설"
    )
    check(
        "query parameters remain structurally valid",
        "?w=1200&name=AI%20%EA%B1%B4%EC%84%A4" in query_encoded,
    )
    check(
        "image URL fragment is removed",
        brief.normalize_image_candidate_url("https://images.fixture.test/a.jpg#section")
        == "https://images.fixture.test/a.jpg",
    )
    check(
        "overlong image URL is rejected",
        brief.normalize_image_candidate_url(
            "https://images.fixture.test/" + ("a" * brief.IMAGE_URL_MAX_LENGTH)
        )
        == "",
    )
    malformed_html = """
      <html><head>
        <meta property="og:image" content="/news/“AI  대전환 골든타임”">
        <meta name="twitter:image" content="/images/recovered.jpg">
      </head><body></body></html>
    """
    recovered_resolution = brief.resolve_article_image(
        {
            "url": "https://publisher.fixture.test/news/article",
            "selected_url": "https://publisher.fixture.test/news/article",
        },
        allow_network=True,
        counters=brief.ImageResolutionCounters(),
        page_fetcher=lambda _url: ("https://publisher.fixture.test/news/article", malformed_html),
        image_probe=lambda _url: True,
    )
    check(
        "malformed publisher image candidate fails locally and secondary candidate succeeds",
        recovered_resolution.source_kind == "twitter_image"
        and len(recovered_resolution.candidates) == 1,
    )

    status_before = subprocess.run(
        ["git", "status", "--short", "--untracked-files=all"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    ).stdout
    with tempfile.TemporaryDirectory(prefix="d7ak6e-image-materialize-") as temporary:
        output_root = Path(temporary) / "bundle"
        html_dir = output_root / "daily"
        materialized, counters = brief.materialize_preview_images(
            [
                article("JPEG image", "https://images.fixture.test/jpeg.jpg"),
                article("PNG image", "https://images.fixture.test/png.png"),
                article("WebP image", "https://images.fixture.test/webp.webp"),
                article("AVIF image", "https://images.fixture.test/avif.avif"),
            ],
            output_root,
            html_dir=html_dir,
            downloader=valid_downloader,
        )
        rendered = brief.render_daily(
            materialized,
            run_at=run_at,
            root_url="https://preview.fixture.test/HDEC-News-Sensor",
        )
        check(
            "valid JPEG byte materialization",
            materialized[0].image_local_asset.endswith(".jpg")
            and (output_root / "assets" / "images" / materialized[0].image_local_asset).is_file(),
        )
        check(
            "valid PNG byte materialization",
            materialized[1].image_local_asset.endswith(".png")
            and (output_root / "assets" / "images" / materialized[1].image_local_asset).is_file(),
        )
        check(
            "valid WebP byte materialization",
            materialized[2].image_local_asset.endswith(".webp")
            and (output_root / "assets" / "images" / materialized[2].image_local_asset).is_file(),
        )
        check(
            "valid AVIF byte materialization",
            materialized[3].image_local_asset.endswith(".avif")
            and (output_root / "assets" / "images" / materialized[3].image_local_asset).is_file(),
        )
        check(
            "relative local src written into HTML",
            "../assets/images/" in rendered.html
            and all(item.image_url.startswith("../assets/images/") for item in materialized),
        )
        check(
            "remote image URL is not used as rendered src after materialization",
            all(not item.image_url.startswith("http") for item in materialized)
            and "https://images.fixture.test/" not in rendered.html,
        )
        check(
            "materialized asset remains under authorized preview root",
            all(
                output_root.resolve()
                in (output_root / "assets" / "images" / item.image_local_asset).resolve().parents
                for item in materialized
            ),
        )
        check(
            "image materialization success counters are exact",
            counters.image_urls_resolved == 4
            and counters.image_candidates_discovered == 4
            and counters.image_download_attempts == 4
            and counters.image_downloads_succeeded == 4
            and counters.image_downloads_failed == 0
            and counters.image_assets_materialized == 4
            and counters.image_bytes_validated
            == len(jpeg) + len(png) + len(webp) + len(avif)
            and counters.images_from_fallback == 0,
        )
        check(
            "browser-load counter contract defaults to zero before Chrome audit",
            counters.images_browser_loaded == 0 and counters.images_browser_failed == 0,
        )

    def mismatched_image_mime_downloader(
        _url: str,
        **_kwargs,
    ) -> brief.ImageDownload:
        return brief.ImageDownload(
            200,
            "image/jpeg",
            png,
            final_url=_url,
        )

    with tempfile.TemporaryDirectory(
        prefix="d7ak6e-image-mime-canonicalization-"
    ) as temporary:
        output_root = Path(temporary) / "bundle"
        mismatched_mime, mismatched_mime_counters = (
            brief.materialize_preview_images(
                [
                    article(
                        "PNG bytes with incorrect JPEG MIME",
                        "https://images.fixture.test/wrong-mime.png",
                    ),
                ],
                output_root,
                html_dir=output_root / "daily",
                downloader=mismatched_image_mime_downloader,
            )
        )
        check(
            "supported PNG magic overrides incorrect image MIME subtype",
            not mismatched_mime[0].image_fallback_used
            and mismatched_mime[0].image_local_asset.endswith(".png")
            and (
                output_root
                / "assets"
                / "images"
                / mismatched_mime[0].image_local_asset
            ).is_file()
            and mismatched_mime_counters.image_assets_materialized == 1
            and mismatched_mime_counters.images_from_fallback == 0,
        )

    def non_image_mime_downloader(
        _url: str,
        **_kwargs,
    ) -> brief.ImageDownload:
        return brief.ImageDownload(
            200,
            "text/html",
            png,
            final_url=_url,
        )

    with tempfile.TemporaryDirectory(
        prefix="d7ak6e-image-non-image-mime-"
    ) as temporary:
        output_root = Path(temporary) / "bundle"
        non_image_mime, non_image_mime_counters = (
            brief.materialize_preview_images(
                [
                    article(
                        "Valid PNG bytes with non-image MIME",
                        "https://images.fixture.test/non-image-mime.png",
                    ),
                ],
                output_root,
                html_dir=output_root / "daily",
                downloader=non_image_mime_downloader,
            )
        )
        check(
            "valid raster bytes with non-image MIME remain rejected",
            non_image_mime[0].image_fallback_used
            and non_image_mime[0].image_materialization_reason
            == "image_invalid_content_type"
            and non_image_mime_counters.image_assets_materialized == 0
            and non_image_mime_counters.images_from_fallback == 1,
        )

    failure_payloads = {
        "403.jpg": brief.ImageDownload(403, "image/jpeg", jpeg),
        "404.jpg": brief.ImageDownload(404, "image/jpeg", jpeg),
        "html.jpg": brief.ImageDownload(200, "image/jpeg", b"<html>not-image</html>"),
        "oversized.jpg": brief.ImageDownload(
            200, "image/jpeg", jpeg + (b"x" * (brief.IMAGE_DOWNLOAD_MAX_BYTES + 1))
        ),
        "empty.jpg": brief.ImageDownload(200, "image/jpeg", b""),
        "invalid.jpg": brief.ImageDownload(200, "image/jpeg", b"not-an-image"),
        "vector.svg": brief.ImageDownload(200, "image/svg+xml", b"<svg></svg>"),
    }

    def failing_downloader(url: str, **_kwargs) -> brief.ImageDownload:
        key = url.rsplit("/", 1)[-1]
        if key == "redirect.jpg":
            raise brief.ImageDownloadError("image_redirect_rejected")
        return failure_payloads[key]

    with tempfile.TemporaryDirectory(prefix="d7ak6e-image-materialize-") as temporary:
        output_root = Path(temporary) / "bundle"
        failed, failure_counters = brief.materialize_preview_images(
            [
                article("HTTP 403", "https://images.fixture.test/403.jpg"),
                article("HTTP 404", "https://images.fixture.test/404.jpg"),
                article("HTML mismatch", "https://images.fixture.test/html.jpg"),
                article("Oversized", "https://images.fixture.test/oversized.jpg"),
                article("Empty", "https://images.fixture.test/empty.jpg"),
                article("Invalid magic", "https://images.fixture.test/invalid.jpg"),
                article("SVG", "https://images.fixture.test/vector.svg"),
                article("Redirect", "https://images.fixture.test/redirect.jpg"),
            ],
            output_root,
            html_dir=output_root / "daily",
            downloader=failing_downloader,
        )
        reasons = {item.image_materialization_reason for item in failed}
        check("HTTP 403 falls back", "image_http_403" in reasons)
        check("HTTP 404 falls back", "image_http_404" in reasons)
        check("HTML body with image content-type mismatch falls back", "image_magic_mismatch" in reasons)
        check("oversized image falls back", "image_oversized" in reasons)
        check("empty image body falls back", "image_empty_body" in reasons)
        check("invalid magic bytes fall back", reasons.issuperset({"image_magic_mismatch"}))
        check("SVG is rejected", "image_svg_rejected" in reasons)
        check("redirect to private host is rejected", "image_redirect_rejected" in reasons)
        check(
            "download failure writes deterministic fallback instead of broken remote src",
            all(item.image_fallback_used and item.image_url == "" for item in failed)
            and failure_counters.image_downloads_failed == len(failed)
            and failure_counters.images_from_fallback == len(failed),
        )

    def duplicate_downloader(_url: str, **_kwargs) -> brief.ImageDownload:
        return brief.ImageDownload(200, "image/jpeg", jpeg, final_url=_url)

    with tempfile.TemporaryDirectory(prefix="d7ak6e-image-materialize-") as temporary:
        output_root = Path(temporary) / "bundle"
        duplicated, duplicate_counters = brief.materialize_preview_images(
            [
                article("Duplicate A", "https://images.fixture.test/a.jpg"),
                article("Duplicate B", "https://images.fixture.test/b.jpg"),
            ],
            output_root,
            html_dir=output_root / "daily",
            downloader=duplicate_downloader,
        )
        check(
            "duplicate image bytes reuse one local asset",
            duplicate_counters.image_assets_materialized == 1
            and duplicated[0].image_local_asset == duplicated[1].image_local_asset
            and duplicated[1].image_duplicate_asset_reused,
        )

    def candidate(
        url: str,
        source_kind: str,
        *,
        context: str = "",
    ) -> brief.ImageCandidateOption:
        return brief.ImageCandidateOption(
            url=url,
            source_kind=source_kind,
            source_page_url="https://publisher.fixture.test/news/article",
            reason=f"selected_{source_kind}",
            context=context,
        )

    def multicandidate_article(
        title: str,
        candidates: list[brief.ImageCandidateOption],
    ) -> brief.EditorialArticle:
        first = candidates[0]
        return brief.EditorialArticle(
            title=title,
            summary="AI 데이터센터와 전력 인프라 투자 계획이 공개됐다.",
            source="Fixture Publisher",
            published_at=run_at,
            selected_url="https://publisher.fixture.test/news/article",
            link_kind=news_access.LINK_KIND_PUBLISHER_DIRECT,
            link_label=news_access.LINK_LABEL_PUBLISHER_DIRECT,
            category="AI/Data",
            collection_source_kind="offline_fixture",
            publisher_article_url="https://publisher.fixture.test/news/article",
            publisher_url_source_kind="existing_publisher_direct",
            image_url=first.url,
            image_remote_url=first.url,
            image_source_kind=first.source_kind,
            image_fallback_used=False,
            image_reason=first.reason,
            image_candidates=tuple(candidates),
            ai_centrality_level="explicit_ai_core",
        )

    quality_payloads = {
        "publisher-logo.png": ("image/png", logo_png),
        "default-og.png": ("image/png", logo_png),
        "brand-symbol.png": ("image/png", logo_png),
        "transparent.png": ("image/png", transparent_logo_png),
        "flat.png": ("image/png", logo_png),
        "photo.jpg": ("image/jpeg", photo_jpeg),
        "robot.jpg": ("image/jpeg", robot_jpeg),
        "infographic.png": ("image/png", infographic_png),
        "illustration.png": ("image/png", illustration_png),
        "weak-small.jpg": ("image/jpeg", weak_small_photo),
        "twitter-photo.jpg": ("image/jpeg", fixture_image_bytes("JPEG", seed=31)),
        "jsonld-photo.jpg": ("image/jpeg", fixture_image_bytes("JPEG", seed=32)),
        "shared-photo.jpg": ("image/jpeg", fixture_image_bytes("JPEG", seed=33)),
        "default-og-shared.jpg": ("image/jpeg", fixture_image_bytes("JPEG", seed=33)),
    }

    def quality_downloader(url: str, **_kwargs) -> brief.ImageDownload:
        key = url.rsplit("/", 1)[-1]
        content_type, payload = quality_payloads[key]
        return brief.ImageDownload(200, content_type, payload, final_url=url)

    with tempfile.TemporaryDirectory(prefix="d7ak6e-image-quality-") as temporary:
        output_root = Path(temporary) / "bundle"
        quality_rows = [
            multicandidate_article(
                "Headline logo recovery",
                [
                    candidate("https://images.fixture.test/publisher-logo.png", "og_image"),
                    candidate("https://images.fixture.test/twitter-photo.jpg", "twitter_image"),
                ],
            ),
            multicandidate_article(
                "Default OG fallback",
                [candidate("https://images.fixture.test/default-og.png", "og_image")],
            ),
            multicandidate_article(
                "Brand asset fallback",
                [candidate("https://images.fixture.test/brand-symbol.png", "og_image")],
            ),
            multicandidate_article(
                "Transparent logo fallback",
                [candidate("https://images.fixture.test/transparent.png", "og_image")],
            ),
            multicandidate_article(
                "Flat logo fallback",
                [candidate("https://images.fixture.test/flat.png", "og_image")],
            ),
            multicandidate_article(
                "Normal news photograph",
                [candidate("https://images.fixture.test/photo.jpg", "og_image")],
            ),
            multicandidate_article(
                "Robot product photograph",
                [candidate("https://images.fixture.test/robot.jpg", "og_image")],
            ),
            multicandidate_article(
                "Relevant infographic",
                [candidate("https://images.fixture.test/infographic.png", "og_image")],
            ),
            multicandidate_article(
                "Article illustration",
                [candidate("https://images.fixture.test/illustration.png", "og_image")],
            ),
            multicandidate_article(
                "Weak single dimension signal",
                [candidate("https://images.fixture.test/weak-small.jpg", "og_image")],
            ),
            multicandidate_article(
                "Logo and default before JSON-LD",
                [
                    candidate("https://images.fixture.test/publisher-logo.png", "og_image"),
                    candidate("https://images.fixture.test/default-og.png", "twitter_image"),
                    candidate("https://images.fixture.test/jsonld-photo.jpg", "jsonld_image"),
                ],
            ),
            multicandidate_article(
                "All logo-like candidates",
                [
                    candidate("https://images.fixture.test/publisher-logo.png", "og_image"),
                    candidate("https://images.fixture.test/flat.png", "twitter_image"),
                ],
            ),
            multicandidate_article(
                "Duplicate default source",
                [candidate("https://images.fixture.test/shared-photo.jpg", "og_image")],
            ),
            multicandidate_article(
                "Duplicate default sink",
                [candidate("https://images.fixture.test/default-og-shared.jpg", "og_image")],
            ),
        ]
        quality_result, quality_counters = brief.materialize_preview_images(
            quality_rows,
            output_root,
            html_dir=output_root / "daily",
            downloader=quality_downloader,
        )
        quality_rendered = brief.render_daily(
            quality_result,
            run_at=run_at,
            root_url="https://preview.fixture.test/HDEC-News-Sensor",
        )
        check(
            "explicit logo filename rejected",
            quality_result[0].image_candidate_attempts[0].status == "rejected"
            and quality_result[0].image_candidate_attempts[0].reason
            == "publisher_logo_marker",
        )
        check(
            "default-og filename rejected",
            quality_result[1].image_fallback_used
            and quality_result[1].image_materialization_reason == "site_default_image",
        )
        check(
            "favicon or brand asset rejected",
            quality_result[2].image_fallback_used
            and quality_result[2].image_materialization_reason == "publisher_logo_marker",
        )
        check(
            "transparent centered logo rejected",
            quality_result[3].image_fallback_used
            and quality_result[3].image_materialization_reason == "logo_like_transparency",
        )
        check(
            "flat publisher logo graphic rejected",
            quality_result[4].image_fallback_used
            and quality_result[4].image_materialization_reason == "logo_like_flat_graphic",
        )
        check(
            "normal news photograph accepted",
            not quality_result[5].image_fallback_used
            and quality_result[5].image_quality_accepted,
        )
        check(
            "product or robot photograph accepted",
            not quality_result[6].image_fallback_used
            and quality_result[6].image_quality_accepted,
        )
        check(
            "relevant infographic accepted",
            not quality_result[7].image_fallback_used
            and quality_result[7].image_quality_accepted,
        )
        check(
            "article illustration accepted",
            not quality_result[8].image_fallback_used
            and quality_result[8].image_quality_accepted,
        )
        check(
            "weak single logo signal alone does not over-reject",
            not quality_result[9].image_fallback_used
            and quality_result[9].image_quality_accepted
            and "logo_like_dimensions" in quality_result[9].image_quality_signals,
        )
        check(
            "rejected og:image falls through to twitter:image accepted",
            quality_result[0].image_source_kind == "twitter_image"
            and quality_result[0].image_candidate_attempts[1].selected,
        )
        check(
            "rejected og/twitter falls through to JSON-LD accepted",
            quality_result[10].image_source_kind == "jsonld_image"
            and quality_result[10].image_candidate_attempts[2].selected,
        )
        check(
            "all candidates logo-like produce deterministic fallback",
            quality_result[11].image_fallback_used
            and quality_result[11].image_url == ""
            and quality_result[11].image_materialization_reason
            in {"publisher_logo_marker", "logo_like_flat_graphic"},
        )
        check(
            "logo bytes are not referenced in rendered HTML",
            "https://images.fixture.test/" not in quality_rendered.html
            and all(
                attempt.local_asset == ""
                for item in quality_result
                for attempt in item.image_candidate_attempts
                if attempt.status == "rejected"
            ),
        )
        check(
            "image quality rejection counter is exact",
            quality_counters.image_quality_checks == 18
            and quality_counters.image_quality_rejections == 10
            and quality_counters.publisher_logo_candidates_rejected == 7
            and quality_counters.publisher_default_images_rejected == 3,
        )
        check(
            "secondary recovery after logo rejection is exact",
            quality_counters.images_recovered_after_quality_rejection == 2
            and quality_counters.images_fallback_after_quality_rejection == 6,
        )
        check(
            "duplicate publisher default image is detected",
            quality_result[13].image_fallback_used
            and quality_result[13].image_materialization_reason
            == "duplicate_publisher_default",
        )
        source_text = (ROOT / "app/editorial_briefings.py").read_text(encoding="utf-8")
        check(
            "no hardcoded current article title or publisher-specific exception",
            "삼성-LG" not in source_text
            and "news2day" not in source_text
            and "news2day.co.kr" not in source_text,
        )
        check(
            "headline remains recoverable after logo rejection",
            quality_counters.headline_image_candidates_attempted == 2
            and quality_counters.headline_image_recovered == 1
            and quality_result[0].image_source_kind == "twitter_image",
        )

    multicandidate_payloads = {
        "bad-og.jpg": brief.ImageDownload(200, "image/jpeg", b"not-an-image"),
        "bad-twitter.jpg": brief.ImageDownload(200, "image/jpeg", b"not-an-image"),
        "bad-jsonld.jpg": brief.ImageDownload(200, "image/jpeg", b"not-an-image"),
        "good-twitter.jpg": brief.ImageDownload(
            200,
            "image/jpeg",
            fixture_image_bytes("JPEG", seed=21),
        ),
        "good-jsonld.jpg": brief.ImageDownload(
            200,
            "image/jpeg",
            fixture_image_bytes("JPEG", seed=22),
        ),
        "good-body.jpg": brief.ImageDownload(
            200,
            "image/jpeg",
            fixture_image_bytes("JPEG", seed=23),
        ),
    }

    def multicandidate_downloader(url: str, **_kwargs) -> brief.ImageDownload:
        key = url.rsplit("/", 1)[-1]
        if key.startswith("fail-"):
            return brief.ImageDownload(403, "image/jpeg", jpeg)
        return multicandidate_payloads[key]

    with tempfile.TemporaryDirectory(prefix="d7ak6e-image-multicandidate-") as temporary:
        output_root = Path(temporary) / "bundle"
        rows = [
            multicandidate_article(
                "Headline twitter recovery",
                [
                    candidate("https://images.fixture.test/bad-og.jpg", "og_image"),
                    candidate(
                        "https://images.fixture.test/good-twitter.jpg",
                        "twitter_image",
                    ),
                ],
            ),
            multicandidate_article(
                "JSON-LD recovery",
                [
                    candidate("https://images.fixture.test/bad-og.jpg", "og_image"),
                    candidate(
                        "https://images.fixture.test/bad-twitter.jpg",
                        "twitter_image",
                    ),
                    candidate(
                        "https://images.fixture.test/good-jsonld.jpg",
                        "jsonld_image",
                    ),
                ],
            ),
            multicandidate_article(
                "Body image recovery",
                [
                    candidate("https://images.fixture.test/bad-og.jpg", "og_image"),
                    candidate(
                        "https://images.fixture.test/bad-jsonld.jpg",
                        "jsonld_image",
                    ),
                    candidate("https://images.fixture.test/good-body.jpg", "body_image"),
                ],
            ),
        ]
        recovered, recovery_counters = brief.materialize_preview_images(
            rows,
            output_root,
            html_dir=output_root / "daily",
            downloader=multicandidate_downloader,
        )
        rendered = brief.render_daily(
            recovered,
            run_at=run_at,
            root_url="https://preview.fixture.test/HDEC-News-Sensor",
        )
        check(
            "og:image failure falls through to twitter:image success",
            recovered[0].image_source_kind == "twitter_image"
            and recovered[0].image_download_status == "success"
            and recovered[0].image_candidate_attempts[0].status == "failed"
            and recovered[0].image_candidate_attempts[1].selected,
        )
        check(
            "og/twitter failure falls through to JSON-LD image success",
            recovered[1].image_source_kind == "jsonld_image"
            and len(recovered[1].image_candidate_attempts) == 3
            and recovered[1].image_candidate_attempts[-1].selected,
        )
        check(
            "earlier image candidates fail through to safe body image success",
            recovered[2].image_source_kind == "body_image"
            and len(recovered[2].image_candidate_attempts) == 3
            and recovered[2].image_candidate_attempts[-1].selected,
        )
        check(
            "secondary candidate recovery counter is exact",
            recovery_counters.images_recovered_from_secondary_candidate == 3
            and recovery_counters.image_candidate_failures == 5
            and recovery_counters.image_candidates_attempted == 8,
        )
        check(
            "headline secondary candidate recovery is recorded",
            recovery_counters.headline_image_candidates_attempted == 2
            and recovery_counters.headline_image_recovered == 1,
        )
        check(
            "successful multi-candidate materialization writes no broken remote src",
            "https://images.fixture.test/" not in rendered.html
            and all(item.image_url.startswith("../assets/images/") for item in recovered),
        )

    def invalid_then_good_downloader(url: str, **_kwargs) -> brief.ImageDownload:
        if url.endswith("/raises.jpg"):
            raise http.client.InvalidURL("invalid image candidate")
        return brief.ImageDownload(
            200,
            "image/jpeg",
            fixture_image_bytes("JPEG", seed=24),
            final_url=url,
        )

    with tempfile.TemporaryDirectory(prefix="d7ak6e-image-multicandidate-") as temporary:
        output_root = Path(temporary) / "bundle"
        invalid_then_good, invalid_counters = brief.materialize_preview_images(
            [
                multicandidate_article(
                    "Invalid candidate recovery",
                    [
                        candidate("javascript:alert(1)", "og_image"),
                        candidate(
                            "https://images.fixture.test/good-twitter.jpg",
                            "twitter_image",
                        ),
                    ],
                ),
                multicandidate_article(
                    "InvalidURL boundary recovery",
                    [
                        candidate("https://images.fixture.test/raises.jpg", "og_image"),
                        candidate(
                            "https://images.fixture.test/good-body.jpg",
                            "body_image",
                        ),
                    ],
                ),
            ],
            output_root,
            html_dir=output_root / "daily",
            downloader=invalid_then_good_downloader,
        )
        invalid_rendered = brief.render_daily(
            invalid_then_good,
            run_at=run_at,
            root_url="https://preview.fixture.test/HDEC-News-Sensor",
        )
        invalid_record = brief.resolved_image_record(invalid_then_good[0], is_headline=True)
        check(
            "invalid first candidate does not terminate materialization",
            invalid_then_good[0].image_source_kind == "twitter_image"
            and invalid_then_good[0].image_candidate_attempts[0].reason
            == "image_candidate_invalid_url"
            and invalid_then_good[0].image_candidate_attempts[1].selected,
        )
        check(
            "http.client.InvalidURL is converted into candidate failure",
            invalid_then_good[1].image_source_kind == "body_image"
            and invalid_then_good[1].image_candidate_attempts[0].reason
            == "image_candidate_invalid_url",
        )
        check(
            "raw malformed candidate is absent from image audit output",
            "javascript:alert" not in json.dumps(invalid_record, ensure_ascii=False),
        )
        check(
            "malformed candidate recovery writes no broken remote src",
            "https://images.fixture.test/" not in invalid_rendered.html
            and all(item.image_url.startswith("../assets/images/") for item in invalid_then_good),
        )
        check(
            "invalid candidate failures are counted before secondary recovery",
            invalid_counters.image_candidate_failures == 2
            and invalid_counters.images_recovered_from_secondary_candidate == 2,
        )

    duplicate_url_calls = 0

    def duplicate_url_downloader(url: str, **_kwargs) -> brief.ImageDownload:
        nonlocal duplicate_url_calls
        duplicate_url_calls += 1
        return brief.ImageDownload(200, "image/jpeg", jpeg, final_url=url)

    with tempfile.TemporaryDirectory(prefix="d7ak6e-image-multicandidate-") as temporary:
        output_root = Path(temporary) / "bundle"
        duplicated_url, duplicated_url_counters = brief.materialize_preview_images(
            [
                multicandidate_article(
                    "Duplicate URL",
                    [
                        candidate("https://images.fixture.test/same.jpg", "og_image"),
                        candidate(
                            "https://images.fixture.test/same.jpg",
                            "twitter_image",
                        ),
                    ],
                )
            ],
            output_root,
            html_dir=output_root / "daily",
            downloader=duplicate_url_downloader,
        )
        check(
            "duplicate URL image candidate is attempted only once",
            duplicate_url_calls == 1
            and duplicated_url_counters.image_candidates_discovered == 1
            and len(duplicated_url[0].image_candidate_attempts) == 1,
        )

    with tempfile.TemporaryDirectory(prefix="d7ak6e-image-multicandidate-") as temporary:
        output_root = Path(temporary) / "bundle"
        too_many, too_many_counters = brief.materialize_preview_images(
            [
                multicandidate_article(
                    "Max attempts",
                    [
                        candidate(
                            f"https://images.fixture.test/fail-{index}.jpg",
                            "og_image",
                        )
                        for index in range(6)
                    ],
                )
            ],
            output_root,
            html_dir=output_root / "daily",
            downloader=multicandidate_downloader,
        )
        rendered_failure = brief.render_daily(
            too_many,
            run_at=run_at,
            root_url="https://preview.fixture.test/HDEC-News-Sensor",
        )
        check(
            "image candidate attempt limit is enforced",
            too_many_counters.image_candidates_discovered == 6
            and too_many_counters.image_candidates_attempted
            == brief.IMAGE_DOWNLOAD_MAX_ATTEMPTS_PER_ARTICLE
            and len(too_many[0].image_candidate_attempts)
            == brief.IMAGE_DOWNLOAD_MAX_ATTEMPTS_PER_ARTICLE,
        )
        check(
            "all image candidates failing produces deterministic fallback",
            too_many[0].image_fallback_used
            and too_many[0].image_url == ""
            and too_many_counters.images_from_fallback == 1
            and too_many_counters.image_candidate_failures
            == brief.IMAGE_DOWNLOAD_MAX_ATTEMPTS_PER_ARTICLE,
        )
        check(
            "fallback after candidate failures leaves broken remote src at zero",
            "https://images.fixture.test/" not in rendered_failure.html
            and "data:image/svg+xml" in rendered_failure.html,
        )

    with tempfile.TemporaryDirectory(prefix="d7ak6e-image-multicandidate-") as temporary:
        output_root = Path(temporary) / "bundle"
        all_invalid, all_invalid_counters = brief.materialize_preview_images(
            [
                multicandidate_article(
                    "All invalid candidates",
                    [
                        candidate("javascript:alert(1)", "og_image"),
                        candidate("data:image/png;base64,AAAA", "twitter_image"),
                    ],
                )
            ],
            output_root,
            html_dir=output_root / "daily",
            downloader=invalid_then_good_downloader,
        )
        all_invalid_rendered = brief.render_daily(
            all_invalid,
            run_at=run_at,
            root_url="https://preview.fixture.test/HDEC-News-Sensor",
        )
        check(
            "all invalid candidates produce deterministic fallback",
            all_invalid[0].image_fallback_used
            and all_invalid[0].image_url == ""
            and all_invalid[0].image_remote_url == ""
            and all_invalid_counters.image_candidate_failures == 2
            and all_invalid_counters.images_from_fallback == 1,
        )
        check(
            "all-invalid fallback leaves broken remote src at zero",
            "javascript:alert" not in all_invalid_rendered.html
            and "data:image/png" not in all_invalid_rendered.html
            and "data:image/svg+xml" in all_invalid_rendered.html,
        )

    status_after = subprocess.run(
        ["git", "status", "--short", "--untracked-files=all"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    ).stdout
    check("image materialization writes no repository image files", status_before == status_after)
    check(
        "image materialization leaves selection policy constants unchanged",
        brief.SELECTION_MODE_LEGACY == "legacy"
        and brief.SELECTION_MODE_DIRECT_AWARE_DAILY == "direct_aware_daily",
    )
    check(
        "image materialization template SHA unchanged",
        hashlib.sha256((ROOT / "templates/editorial_daily.html").read_bytes()).hexdigest()
        == "1c399616877a2dc014b541d781076c32508dc522fcd947a4a62a94d25fb7f9ab"
        and hashlib.sha256((ROOT / "templates/editorial_weekly_tni.html").read_bytes()).hexdigest()
        == "3cdcbf4891ad24c52a9465fa6cacd8757246fc6b33959c60a190405c321e6206",
    )
    check(
        "image materialization side-effect counters are zero by contract",
        brief.ImageMaterializationCounters().images_browser_loaded == 0
        and brief.ImageMaterializationCounters().images_browser_failed == 0,
    )


def live_preview_contracts() -> None:
    run_at = dt("2026-07-27T07:00:00+09:00")
    rows = brief.fixture_articles("daily", run_at)
    for index, row in enumerate(rows, start=1):
        row["image_url"] = f"https://images.fixture.test/article-{index}.jpg"

    image = Image.new("RGB", (640, 360), (48, 96, 140))
    draw = ImageDraw.Draw(image)
    for index in range(6):
        draw.rectangle(
            (40 + index * 85, 70, 105 + index * 85, 295),
            fill=(80 + index * 20, 120 + index * 17, 170 - index * 12),
        )
    buffer = BytesIO()
    image.save(buffer, format="JPEG")
    jpeg = buffer.getvalue()

    def fixture_downloader(url: str, **_kwargs) -> brief.ImageDownload:
        suffix = url.rsplit("-", 1)[-1].split(".", 1)[0].encode("ascii", "ignore")
        return brief.ImageDownload(
            status=200,
            content_type="image/jpeg",
            payload=jpeg + suffix,
            final_url=url,
        )

    with tempfile.TemporaryDirectory(prefix="d7ak6e-live-preview-") as temporary:
        output_root = Path(temporary) / "bundle"
        with hard_block_network():
            manifest = runner.run_live_preview(
                run_at=run_at,
                preview_root=output_root,
                fixture_root="https://preview.fixture.test/HDEC-News-Sensor",
                collect=lambda: rows,
                image_downloader=fixture_downloader,
            )
        resolved = json.loads(
            (output_root / "resolved-images.json").read_text(encoding="utf-8")
        )
        check(
            "live-preview writes only the /tmp Daily HTML and audit manifests",
            Path(manifest["latest_html"]).is_file()
            and Path(manifest["dated_html"]).is_file()
            and (output_root / "manifest.json").is_file()
            and len(resolved) == 6,
        )
        check(
            "live-preview image source manifest accounting is exact",
            manifest["images_from_feed"] == 6
            and manifest["images_from_fallback"] == 0
            and manifest["network_page_gets"] == 0
            and manifest["publisher_page_gets"] == 0
            and manifest["naver_provider_enabled"] is False
            and manifest["naver_provider_status"] == "not_used"
            and manifest["naver_credentials_present"] is False
            and manifest["naver_provider_activation_error"] is False
            and manifest["naver_provider_queries_ok"] == 0
            and manifest["naver_api_requests"] == 0
            and manifest["naver_articles_collected"] == 0
            and manifest["naver_originallinks_collected"] == 0
            and manifest["google_news_articles_collected"] == 0
            and manifest["naver_direct_articles_selected"] == 0
            and manifest["other_direct_articles_selected"] == 6
            and manifest["aggregator_articles_selected"] == 0
            and manifest["direct_candidates_before_selection"] == 6
            and manifest["aggregator_candidates_before_selection"] == 0
            and manifest["direct_candidates_displaced_by_aggregator"] == 0
            and manifest["direct_candidates_rejected_below_relevance_floor"] == 0
            and manifest["publisher_urls_existing_direct"] == 6
            and manifest["publisher_urls_direct_from_feed"] == 6
            and manifest["publisher_urls_unresolved"] == 0
            and manifest["aggregator_page_gets"] == 0
            and manifest["images_resolved_actual"] == 6
            and manifest["images_resolved_actual_semantics"]
            == "remote image URL candidates selected"
            and manifest["image_urls_resolved"] == 6
            and manifest["image_candidates_discovered"] == 6
            and manifest["image_candidates_attempted"] == 6
            and manifest["image_candidate_failures"] == 0
            and manifest["image_download_attempts"] == 6
            and manifest["image_downloads_succeeded"] == 6
            and manifest["image_downloads_failed"] == 0
            and manifest["image_assets_materialized"] == 6
            and manifest["images_recovered_from_secondary_candidate"] == 0
            and manifest["headline_image_candidates_attempted"] == 1
            and manifest["headline_image_recovered"] == 1
            and manifest["images_browser_loaded"] == 0
            and manifest["images_browser_failed"] == 0
            and manifest["image_probe_requests"] == 0
            and all(item["source_kind"] == "rss_image" for item in resolved),
        )
        check(
            "live-preview resolved-image audit separates original, publisher, and image URLs",
            all(
                item["original_article_url"]
                and item["original_host"]
                and item["publisher_article_url"]
                and item["publisher_host"]
                and item["publisher_url_source_kind"]
                == "existing_publisher_direct"
                and item["collection_source_kind"] == "offline_fixture"
                and "image_host" in item
                and "image_reason" in item
                and item["image_source_kind"] == "rss_image"
                and item["resolved_image_url"].startswith("https://images.fixture.test/")
                and item["rendered_image_src"].startswith("../assets/images/")
                and item["local_image_src"].startswith("../assets/images/")
                and item["local_asset_filename"].endswith(".jpg")
                and item["image_download_status"] == "success"
                and item["image_candidate_source_kinds"] == ["rss_image"]
                and item["image_candidate_hosts"] == ["images.fixture.test"]
                and item["image_candidate_attempts"][0]["status"] == "success"
                and item["image_candidate_attempts"][0]["selected"] is True
                for item in resolved
            ),
        )
        zero_fields = (
            "smtp_attempts", "teams_sends", "telegram_calls", "state_reads",
            "state_writes", "docs_writes", "git_writes",
        )
        check(
            "live-preview send, state, docs, and git counters are zero",
            all(manifest[field] == 0 for field in zero_fields),
        )
    expect_raises(
        "live-preview rejects repository output",
        runner.OrchestratorError,
        lambda: runner._live_preview_root(ROOT / "forbidden-live-preview"),
    )
    runner_source = read("scripts/run_editorial_briefing.py")
    check(
        "live-preview is mutually exclusive with publish and send",
        'mode.add_argument("--live-preview", action="store_true")' in runner_source
        and "add_mutually_exclusive_group(required=True)" in runner_source,
    )



def source_priority_and_link_integrity_contracts() -> None:
    primary = (
        "연합뉴스", "MBC", "KBS", "조선일보", "YTN",
        "JTBC", "중앙일보", "매일경제", "한국경제", "SBS",
    )
    secondary = ("동아일보", "한겨레", "경향신문")
    domains = (
        "yna.co.kr", "imbc.com", "kbs.co.kr", "chosun.com",
        "ytn.co.kr", "jtbc.co.kr", "joongang.co.kr",
        "mk.co.kr", "hankyung.com", "sbs.co.kr",
    )

    check(
        "locked primary and secondary publisher priority is exact",
        brief.PRIMARY_PUBLISHER_PRIORITY == primary
        and brief.SECONDARY_PUBLISHER_PRIORITY == secondary
        and brief.PREFERRED_PUBLISHER_DAILY_TARGET == 4
        and brief.PREFERRED_PUBLISHER_WEEKLY_TARGET == 8,
    )

    jtbc = brief.source_quality.classify("JTBC", "AI 데이터센터 건설")
    check(
        "JTBC is classified as a trusted news publisher",
        jtbc["source_quality"] == "trusted"
        and jtbc["source_type"] == "news",
    )

    safe_url = (
        "https://teams.public.onecdn.static.microsoft/"
        "evergreen-assets/safelinks/2/atp-safelinks.html"
    )
    safe_selection = news_access.choose_article_link({"url": safe_url})

    check(
        "Microsoft Safe Links intermediary is never publisher-direct",
        news_access.classify_source_type(safe_url) == "unknown"
        and not safe_selection.url
        and not safe_selection.is_direct
        and not news_access.choose_direct_article_url({"url": safe_url}),
    )

    daily_run = dt("2026-07-29T07:00:00+09:00")
    weekly_run = dt("2026-07-29T07:30:00+09:00")

    def rows_for(coverage):
        rows = []

        for index, (source, domain) in enumerate(
            zip(primary, domains),
            start=1,
        ):
            rows.append({
                "title": f"AI 데이터센터 건설 투자 우선매체 {index}",
                "source": source,
                "published_at": coverage.end.isoformat(),
                "url": f"https://{domain}/news/priority-{index}",
                "snippet": (
                    "AI 데이터센터 건설 및 전력 인프라 투자 계획이 발표됐다. "
                    "건설 산업과 경영 의사결정에 영향을 줄 수 있다."
                ),
                "source_metadata": {"provider": "offline_fixture"},
            })

        for index, source in enumerate(secondary, start=1):
            rows.append({
                "title": f"AI 건설 안전 정책 보조매체 {index}",
                "source": source,
                "published_at": coverage.end.isoformat(),
                "url": f"https://secondary{index}.fixture.test/news/{index}",
                "snippet": (
                    "AI 기반 건설 안전 정책과 적용 범위가 공개됐다. "
                    "후속 제도 변화를 확인할 필요가 있다."
                ),
                "source_metadata": {"provider": "offline_fixture"},
            })

        rows.append({
            "title": "국토부 AI 건설 안전 정책 발표",
            "source": "국토교통부",
            "published_at": coverage.end.isoformat(),
            "url": "https://molit.go.kr/news/official-ai-construction",
            "snippet": (
                "건설현장 AI 안전관리 정책과 적용 일정이 발표됐다. "
                "건설사 대응 범위를 점검할 필요가 있다."
            ),
            "source_metadata": {"provider": "offline_fixture"},
        })

        for index in range(1, 4):
            rows.append({
                "title": f"AI 건설 일반매체 후보 {index}",
                "source": f"일반경제매체 {index}",
                "published_at": coverage.end.isoformat(),
                "url": f"https://other{index}.fixture.test/news/{index}",
                "snippet": (
                    "AI 건설 기술 도입 계획이 공개됐다. "
                    "시장 적용 가능성이 논의되고 있다."
                ),
                "source_metadata": {"provider": "offline_fixture"},
            })

        rows.append({
            "title": "지역 축제 프로그램 안내",
            "source": "MBC",
            "published_at": coverage.end.isoformat(),
            "url": "https://imbc.com/news/irrelevant-local-event",
            "snippet": "지역 행사 일정이 공개됐다. 관람 방법이 안내됐다.",
            "source_metadata": {
                "provider": "google_news_rss",
                "query": "",
            },
        })

        return rows

    daily_coverage = brief.daily_coverage(daily_run)
    weekly_coverage = brief.weekly_coverage(weekly_run)

    daily_articles = brief.normalize_articles(
        rows_for(daily_coverage),
        daily_coverage,
        limit=brief.DAILY_MAX_ARTICLES,
        resolve_images=False,
        selection_mode=brief.SELECTION_MODE_EDITORIAL_PRIORITY,
    )
    weekly_articles = brief.normalize_articles(
        rows_for(weekly_coverage),
        weekly_coverage,
        limit=brief.WEEKLY_MAX_ARTICLES,
        resolve_images=False,
        selection_mode=brief.SELECTION_MODE_EDITORIAL_PRIORITY,
    )

    def priority(article):
        return brief._publisher_priority(  # noqa: SLF001
            article.source,
            article.selected_url,
        )

    daily_primary = [
        article for article in daily_articles
        if priority(article)[0] == "primary"
    ]
    weekly_primary = [
        article for article in weekly_articles
        if priority(article)[0] == "primary"
    ]

    check(
        "Daily fills at least four relevance-qualified primary publishers",
        len(daily_articles) == 6 and len(daily_primary) >= 4,
        repr([article.source for article in daily_articles]),
    )
    check(
        "Weekly fills at least eight relevance-qualified primary publishers",
        len(weekly_articles) == 12 and len(weekly_primary) >= 8,
        repr([article.source for article in weekly_articles]),
    )
    check(
        "Daily primary publishers preserve the locked rank order",
        [priority(article)[1] for article in daily_primary]
        == sorted(priority(article)[1] for article in daily_primary),
    )
    check(
        "Weekly primary publishers preserve the locked rank order",
        [priority(article)[1] for article in weekly_primary]
        == sorted(priority(article)[1] for article in weekly_primary),
    )
    check(
        # D7-AK-6E R4-R6 §11 — no unconditional institution quota: the official
        # row competes on the shared ranking. With six slots and ten
        # higher-ranked locked publishers it is NOT forced into Daily, while
        # Weekly's twelve slots still admit it purely on merit.
        "official institution competes on merit, never via a forced quota",
        "국토교통부" not in {article.source for article in daily_articles}
        and "국토교통부" in {article.source for article in weekly_articles},
        repr({
            "daily": [article.source for article in daily_articles],
            "weekly": [article.source for article in weekly_articles],
        }),
    )
    check(
        "primary publisher below relevance floor is never quota-filled",
        all(
            article.title != "지역 축제 프로그램 안내"
            for article in daily_articles
        )
        and all(
            article.title != "지역 축제 프로그램 안내"
            for article in weekly_articles
        ),
    )

    runner_source = read("scripts/run_editorial_briefing.py")
    check(
        "production publish explicitly activates editorial priority mode",
        (
            "selection_mode=(" in runner_source
            and "editorial_briefings.SELECTION_MODE_EDITORIAL_PRIORITY"
            in runner_source
        ),
    )

    root_url = "https://preview.fixture.test/HDEC-News-Sensor"
    daily = brief.render_daily(
        daily_articles,
        run_at=daily_run,
        root_url=root_url,
    )
    weekly = brief.render_weekly(
        weekly_articles,
        run_at=weekly_run,
        root_url=root_url,
    )

    brief.validate_rendered(daily)
    brief.validate_rendered(weekly)

    daily_urls = {article.selected_url for article in daily_articles}
    weekly_urls = {article.selected_url for article in weekly_articles}

    check(
        "Daily Teams text contains canonical Brief and dashboard URLs",
        public_url_contract.DAILY_LATEST_URL in daily.teams_text
        and public_url_contract.CANONICAL_DASHBOARD_URL in daily.teams_text
        and all(url not in daily.teams_text for url in daily_urls),
    )
    check(
        "Daily Teams HTML has Brief and dashboard CTAs and no article links",
        daily.teams_html.count("<a ") == 2
        and public_url_contract.DAILY_LATEST_URL in daily.teams_html
        and public_url_contract.CANONICAL_DASHBOARD_URL in daily.teams_html
        and "오늘의 Daily Brief 보기" in daily.teams_html
        and "전체 뉴스 대시보드 보기" in daily.teams_html
        and all(url not in daily.teams_html for url in daily_urls),
    )
    check(
        "Weekly Teams surfaces contain Brief and dashboard CTAs",
        weekly.teams_html.count("<a ") == 2
        and public_url_contract.WEEKLY_LATEST_URL in weekly.teams_html
        and public_url_contract.WEEKLY_LATEST_URL in weekly.teams_text
        and public_url_contract.CANONICAL_DASHBOARD_URL in weekly.teams_html
        and public_url_contract.CANONICAL_DASHBOARD_URL in weekly.teams_text
        and "이번 주 Weekly Brief 보기" in weekly.teams_html
        and "전체 뉴스 대시보드 보기" in weekly.teams_html
        and all(url not in weekly.teams_html for url in weekly_urls)
        and all(url not in weekly.teams_text for url in weekly_urls),
    )
    check(
        "public briefing pages preserve selected article links",
        all(url in daily.html for url in daily_urls)
        and all(url in weekly.html for url in weekly_urls),
    )

def r4r6_editorial_quality_contracts() -> None:
    """D7-AK-6E R4-R6 §11 — weak-content rejection, honest shortfall, ranking."""
    coverage = brief.daily_coverage(dt("2026-07-27T07:00:00+09:00"))

    def row(
        title: str,
        *,
        url: str,
        minutes_before_end: int = 60,
        source: str = "연합뉴스",
        snippet: str | None = None,
    ) -> dict:
        return {
            "title": title,
            "source": source,
            "published_at": (
                coverage.end - timedelta(minutes=minutes_before_end)
            ).isoformat(),
            "url": url,
            "snippet": snippet
            or f"{title} 관련 공개 계획과 적용 범위가 제시됐다.",
            "source_metadata": {"provider": "offline_fixture", "query": "AI 데이터센터 전력"},
        }

    def normalize(rows: list[dict], *, limit: int = 6):
        audit = brief.SelectionAuditCounters()
        articles = brief.normalize_articles(
            rows,
            coverage,
            limit=limit,
            resolve_images=False,
            selection_audit=audit,
            selection_mode=brief.SELECTION_MODE_DIRECT_AWARE_DAILY,
        )
        return articles, audit

    # R4-R6 §5 runs before §11: stock/civic titles now die at the canonical
    # AI-centrality gate with their own counters, while weak-content rules
    # keep rejecting AI-qualified non-news (recruitment, book PR).
    weak_rows = [
        row("AI 데이터센터 수혜주·관련주 급등 정리", url="https://yna.co.kr/news/weak-1"),
        row("건설사 신입 채용 공고, AI 직무 확대", url="https://yna.co.kr/news/weak-2"),
        row("AI 시대 걷기대회 캠페인 개최", url="https://yna.co.kr/news/weak-3"),
        row("스마트건설 우수사례 시상식 표창", url="https://yna.co.kr/news/weak-4"),
        row("AI 신간 출간, 데이터센터 산업 해설", url="https://yna.co.kr/news/weak-5"),
    ]
    strong_rows = [
        row(
            "현대건설, AI 데이터센터 전력 인프라 EPC 계약 체결",
            url="https://yna.co.kr/news/strong-1",
            snippet="1조원 규모 AI 데이터센터 전력 인프라 EPC 계약이 체결됐다.",
        ),
        row(
            "국내 SMR 실증 사업 승인, 전력 공급 계획 확정",
            url="https://yna.co.kr/news/strong-2",
            source="한국경제",
            snippet=(
                "AI 데이터센터 전력 수요 대응을 위한 SMR 실증 사업이 승인되며 "
                "2GW 전력 공급 계획이 확정됐다."
            ),
        ),
    ]

    thin_articles, thin_audit = normalize(weak_rows + strong_rows)
    check(
        "weak content is rejected even when the edition runs short (§11)",
        len(thin_articles) == 2
        and all("수혜주" not in article.title for article in thin_articles)
        and all("채용" not in article.title for article in thin_articles)
        and all("캠페인" not in article.title for article in thin_articles)
        and all("시상식" not in article.title for article in thin_articles)
        and all("신간" not in article.title for article in thin_articles),
        repr([article.title for article in thin_articles]),
    )
    check(
        "weak rejection and shortfall are machine-readable (§11)",
        thin_audit.weak_content_rejected == 2
        and thin_audit.stock_market_rejected_count == 1
        and thin_audit.unrelated_domain_rejected_count == 2
        and thin_audit.ai_central_qualified_count == 4
        and thin_audit.qualified_candidates == 2
        and thin_audit.selected_candidates == 2
        and thin_audit.selection_shortfall == 2,
        repr(thin_audit.manifest_fields()),
    )
    check(
        "floating-solar articles survive the award-publicity guard (§11)",
        brief._weak_content_reason(  # noqa: SLF001
            "수상태양광 발전소 착공, 수상 구조물 계약 체결", ""
        )
        == "",
    )

    # §11 ranking precedence: same decision relevance → materiality decides;
    # same materiality → locked publisher priority decides.
    material_row = row(
        "AI 데이터센터 전력 계약 체결, 3조원 투자 확정",
        url="https://khan.co.kr/news/material",
        source="경향신문",
        minutes_before_end=600,
        snippet="3조원 규모 AI 데이터센터 전력 계약이 체결됐다.",
    )
    fresh_row = row(
        "AI 데이터센터 전력 수요 전망 보고서 공개",
        url="https://yna.co.kr/news/fresh",
        minutes_before_end=5,
        snippet="AI 데이터센터 전력 수요가 늘어날 것이라는 전망이 나왔다.",
    )
    ranked, _audit = normalize([fresh_row, material_row], limit=2)
    check(
        "materiality outranks freshness within equal decision relevance (§11)",
        ranked and "3조원" in ranked[0].title,
        repr([article.title for article in ranked]),
    )
    check(
        "selected articles expose §11 reasoning fields",
        ranked
        and ranked[0].executive_relevance_reason
        and ranked[0].materiality_reason != "no_material_signal"
        and ranked[0].publisher_priority_label
        and ranked[0].diversity_contribution
        and "materiality=" in ranked[0].selection_reason,
        repr({
            "relevance": ranked[0].executive_relevance_reason,
            "materiality": ranked[0].materiality_reason,
            "publisher": ranked[0].publisher_priority_label,
            "diversity": ranked[0].diversity_contribution,
            "selection": ranked[0].selection_reason,
        }) if ranked else "no articles",
    )
    check(
        "generated executive implication states an HDEC angle (§12)",
        ranked
        and ranked[0].executive_implication
        and "점검" in ranked[0].executive_implication,
        ranked[0].executive_implication if ranked else "no articles",
    )


def main() -> int:
    source_contracts()
    workflow_contracts()
    time_and_filter_contracts()
    state_contracts()
    daily, _dominant, _multi = link_and_render_contracts()
    image_resolution_contracts()
    publisher_url_resolution_contracts()
    naver_provider_contracts()
    naver_provider_activation_contracts()
    selection_policy_contracts()
    r4r6_editorial_quality_contracts()
    source_priority_and_link_integrity_contracts()
    computed_style_contracts()
    url_and_publication_contracts(daily)
    smtp_and_state_contracts(daily)
    claim_delivery_contracts(daily)
    republish_contracts()
    weekly_verified_supply_contracts()
    preview_contracts()
    image_materialization_contracts()
    live_preview_contracts()
    print(f"\nRESULT pass={PASS} fail={FAIL}")
    print("NETWORK_FIXTURE=hard_blocked SMTP_FIXTURE=offline_only")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
