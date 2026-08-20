#!/usr/bin/env python3
"""R4-OPS-10B deterministic intake, auth UX, and learning acceptance.

Offline only.  Network/DNS, OAuth, GitHub storage, and article import are all
injected fakes; the browser opens a generated local zero-candidate fixture.
No workflow dispatch, send, deployment, or production-state mutation occurs.
"""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from html import unescape
from pathlib import Path
from urllib.parse import quote, urlencode

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "scripts"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

os.environ.setdefault("APP_MODE", "mock")
os.environ.setdefault("NEWS_MODE", "mock")

from app import (  # noqa: E402
    editorial_article_import as article_import,
    editorial_briefings,
    editorial_feedback,
    editorial_operator_review as operator_review,
    operator_auth,
    public_urls,
)
import build_editorial_review_console as console_builder  # noqa: E402

SNAPSHOT_ID = "review-2026-08-20-9159214cae7c9872"
SNAPSHOT_PATH = ROOT / "docs" / "editorial" / "review" / "snapshots" / SNAPSHOT_ID / "manifest.json"
SNAPSHOT_MANIFEST = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
EDITION_ID = "daily-2026-08-20-1b7fb384250938c3"
PUBLISHER_URL = "https://publisher.example.com/article/ai-dc-1"
TARGETLESS_ONECDN = (
    "https://teams.public.onecdn.static.microsoft/evergreen-assets/"
    "safelinks/2/atp-safelinks.html"
)
TARGETLESS_MESSAGE = (
    "원문 정보가 없는 Teams 보안 링크입니다. 기사에서 '원문 열기' 후 "
    "실제 언론사 URL을 복사해 주세요."
)


class Verify:
    def __init__(self) -> None:
        self.checks = 0
        self.failures = 0

    def check(self, label: str, condition: bool, detail: str = "") -> None:
        self.checks += 1
        if condition:
            print(f"PASS: {label}")
        else:
            self.failures += 1
            print(f"FAIL: {label}" + (f" — {detail}" if detail else ""))


class FakeGitHub:
    def __init__(self) -> None:
        self.repo = "Sinabroin/HDEC-News-Sensor"
        self.token = "offline"
        self.branch = "main"
        self.store = {
            operator_review.review_snapshot_manifest_path(SNAPSHOT_ID): {
                "version": 1,
                "json": copy.deepcopy(SNAPSHOT_MANIFEST),
            }
        }
        self.puts: list[str] = []

    def get_file(self, path: str):
        value = self.store.get(path)
        if value is None:
            return None
        return {
            "sha": f"sha:{path}:{value['version']}",
            "json": copy.deepcopy(value["json"]),
        }

    def list_directory(self, path: str):
        prefix = path.rstrip("/") + "/"
        return sorted(
            item
            for item in self.store
            if item.startswith(prefix) and "/" not in item[len(prefix):]
        )

    def put_file(self, path, *, content_bytes, message, base_sha):
        del message
        current = self.store.get(path)
        if current is None and base_sha:
            raise operator_review.OperatorReviewError("STALE_DRAFT")
        if current is not None and base_sha != f"sha:{path}:{current['version']}":
            raise operator_review.OperatorReviewError("STALE_DRAFT")
        version = int(current["version"] + 1) if current else 1
        self.store[path] = {
            "version": version,
            "json": json.loads(content_bytes.decode("utf-8")),
        }
        self.puts.append(path)
        return {"sha": f"sha:{path}:{version}"}


class DispatchCounter:
    """A no-argument publish dispatcher that counts real dispatch invocations."""

    def __init__(self) -> None:
        self.count = 0

    def __call__(self) -> dict:
        self.count += 1
        return {"workflow": "daily-publish", "status": "queued", "invocation": self.count}


class FaultGitHub(FakeGitHub):
    """FakeGitHub that fails the Nth PUT matching a predicate exactly once.

    Models an interrupted learning corpus/profile write so the identical-retry
    recovery path can be proven without any real network or production write. The
    injected failure clears after it fires, mirroring a transient outage."""

    def __init__(self, fail_predicate, *, fail_on_hit: int = 1) -> None:
        super().__init__()
        self._fail_predicate = fail_predicate
        self._fail_on_hit = fail_on_hit
        self._hits = 0
        self.injected = False

    def put_file(self, path, *, content_bytes, message, base_sha):
        if not self.injected and self._fail_predicate(path):
            self._hits += 1
            if self._hits == self._fail_on_hit:
                self.injected = True
                raise operator_review.OperatorReviewError("LEARNING_PERSIST_FAILED")
        return super().put_file(
            path, content_bytes=content_bytes, message=message, base_sha=base_sha
        )


def _is_exemplar_put(path: str) -> bool:
    return "/human_exemplars/exemplar-" in path


def human_item(**overrides) -> dict:
    value = {
        "candidate_id": "manual-human-1",
        "origin": "human_link",
        "title": "AI 데이터센터 직류배전 인프라 계약 체결",
        "source": "테스트경제",
        "summary": "AI 데이터센터 전력 인프라 계약이 체결됐다.",
        "summary_html": "AI 데이터센터 전력 인프라 계약이 체결됐다.",
        "selected_url": PUBLISHER_URL,
        "category": "투자·산업",
        "published_at": "2026-08-20T09:00:00+09:00",
    }
    value.update(overrides)
    return value


def review_payload(**overrides) -> dict:
    value = {
        "product": "daily",
        "edition_key": "2026-08-20",
        "review_snapshot_id": SNAPSHOT_ID,
        "selected_items": [human_item()],
    }
    value.update(overrides)
    return value


def public_resolver(host: str, port: int, type=None):
    del host, type
    return [(2, 1, 6, "", ("93.184.216.34", port))]


def error_code(callable_) -> str:
    try:
        callable_()
    except article_import.ArticleImportError as exc:
        return exc.code
    return ""


def verify_safelinks_and_urls(v: Verify) -> None:
    print("\n== SafeLinks + adversarial URL boundary ==")
    direct = article_import.unwrap_microsoft_safelinks_url(
        PUBLISHER_URL, resolver=public_resolver
    )
    v.check("normal publisher URL passes unchanged", direct == PUBLISHER_URL)

    outlook = "https://nam12.safelinks.protection.outlook.com/?" + urlencode(
        {"url": PUBLISHER_URL, "data": "bounded-fixture"}
    )
    onecdn = TARGETLESS_ONECDN + "?" + urlencode({"url": PUBLISHER_URL})
    v.check(
        "explicit Outlook SafeLinks target unwraps",
        article_import.unwrap_microsoft_safelinks_url(
            outlook, resolver=public_resolver
        )
        == PUBLISHER_URL,
    )
    v.check(
        "explicit Teams onecdn SafeLinks target unwraps",
        article_import.unwrap_microsoft_safelinks_url(
            onecdn, resolver=public_resolver
        )
        == PUBLISHER_URL,
    )

    nested = "https://nam12.safelinks.protection.outlook.com/?" + urlencode(
        {"url": onecdn}
    )
    v.check(
        "known nested wrappers unwrap within depth bound",
        article_import.unwrap_microsoft_safelinks_url(
            nested, resolver=public_resolver
        )
        == PUBLISHER_URL,
    )
    too_deep = "https://nam12.safelinks.protection.outlook.com/?" + urlencode(
        {"url": nested}
    )
    v.check(
        "wrapper nesting beyond bound is rejected",
        error_code(
            lambda: article_import.unwrap_microsoft_safelinks_url(
                too_deep, resolver=public_resolver
            )
        )
        == "REDIRECT_REJECTED",
    )
    double_encoded = TARGETLESS_ONECDN + "?url=" + quote(
        quote(PUBLISHER_URL, safe=""), safe=""
    )
    v.check(
        "double decoding trick is rejected",
        error_code(
            lambda: article_import.unwrap_microsoft_safelinks_url(
                double_encoded, resolver=public_resolver
            )
        )
        == "INVALID_URL",
    )
    duplicate_target = TARGETLESS_ONECDN + "?" + urlencode(
        [("url", PUBLISHER_URL), ("url", "https://evil.example/article/2")]
    )
    v.check(
        "duplicate target ambiguity is rejected",
        error_code(
            lambda: article_import.unwrap_microsoft_safelinks_url(
                duplicate_target, resolver=public_resolver
            )
        )
        == "INVALID_URL",
    )
    private_target = TARGETLESS_ONECDN + "?" + urlencode(
        {"url": "http://169.254.169.254/latest/meta-data"}
    )
    v.check(
        "nested private target reruns normal SSRF validator",
        error_code(
            lambda: article_import.unwrap_microsoft_safelinks_url(
                private_target, resolver=public_resolver
            )
        )
        == "UNSAFE_DESTINATION",
    )
    targetless_code = error_code(
        lambda: article_import.unwrap_microsoft_safelinks_url(
            TARGETLESS_ONECDN, resolver=public_resolver
        )
    )
    try:
        article_import.unwrap_microsoft_safelinks_url(
            TARGETLESS_ONECDN, resolver=public_resolver
        )
    except article_import.ArticleImportError as exc:
        targetless_message = exc.message
    else:
        targetless_message = ""
    v.check(
        "targetless onecdn wrapper gets specific operator guidance",
        targetless_code == "MICROSOFT_SAFELINK_TARGET_MISSING"
        and targetless_message == TARGETLESS_MESSAGE,
    )
    lookalike = "https://evil.example/safelinks?" + urlencode({"url": PUBLISHER_URL})
    v.check(
        "unallowlisted lookalike is never unpacked",
        article_import.unwrap_microsoft_safelinks_url(
            lookalike, resolver=public_resolver
        )
        == lookalike,
    )

    invalid_vectors = {
        "javascript": "javascript:alert(1)",
        "data": "data:text/html,x",
        "file": "file:///etc/passwd",
    }
    for label, value in invalid_vectors.items():
        v.check(
            f"{label} URL rejected",
            error_code(
                lambda value=value: article_import.validate_public_article_url(
                    value, resolver=public_resolver
                )
            )
            == "INVALID_URL",
        )
    for label, value in {
        "userinfo": "https://user:pass@publisher.example.com/article/1",
        "private IP": "http://10.0.0.1/article/1",
        "loopback": "http://127.0.0.1/article/1",
    }.items():
        v.check(
            f"{label} destination rejected",
            error_code(
                lambda value=value: article_import.validate_public_article_url(
                    value, resolver=public_resolver
                )
            )
            == "UNSAFE_DESTINATION",
        )


def verify_learning(v: Verify) -> None:
    print("\n== Confirmed exemplar durability + bounded consumption ==")
    client = FakeGitHub()
    saved = operator_review.save_draft(
        review_payload(), operator_login="sinabroin", client=client
    )
    exemplar_before_publish = [
        key for key in client.store if "/human_exemplars/exemplar-" in key
    ]
    v.check(
        "server draft is pending evidence, not active learning",
        not exemplar_before_publish
        and not editorial_feedback.confirmed_human_exemplars(
            {"product": "daily", "review_status": "draft", **review_payload()}
        ),
    )
    published = operator_review.publish_daily(
        review_payload(base_revision=saved["revision"]),
        operator_login="sinabroin",
        client=client,
        dispatcher=None,
    )
    exemplar_paths = sorted(
        key for key in client.store if "/human_exemplars/exemplar-" in key
    )
    exemplar = client.store[exemplar_paths[0]]["json"] if exemplar_paths else {}
    v.check(
        "A. one approved human article creates exactly one exemplar",
        len(exemplar_paths) == 1
        and published["learning_exemplars_added"] == 1
        and published["learning_exemplar_count"] == 1
        and editorial_feedback.valid_human_exemplar(exemplar),
        repr(published),
    )
    allowed = {
        "version", "exemplar_id", "edition_key", "review_snapshot_id",
        "canonical_publisher_url", "publisher_domain", "source", "category",
        "title", "topic_signals", "provenance", "selected", "approved",
    }
    v.check(
        "confirmed exemplar retains only bounded safe fields",
        set(exemplar) == allowed
        and len(exemplar["title"]) <= 500
        and len(exemplar["topic_signals"]) <= 12
        and not ({"body", "html", "cookies", "headers", "secrets"} & set(exemplar)),
    )
    puts_before = len(client.puts)
    republished = operator_review.publish_daily(
        review_payload(
            base_revision=saved["revision"],
            base_approved_revision=published["approved_revision"],
        ),
        operator_login="sinabroin",
        client=client,
        dispatcher=None,
    )
    v.check(
        "B. identical re-publish creates no duplicate",
        republished["already_published"] is True
        and republished["learning_exemplars_added"] == 0
        and len(exemplar_paths) == 1
        and len(client.puts) == puts_before,
        repr(republished),
    )
    v.check(
        "C. abandoned/local human item creates no exemplar",
        editorial_feedback.confirmed_human_exemplars(
            {"product": "daily", "review_status": "draft", **review_payload()}
        )
        == [],
    )

    repeated = []
    for day, suffix in ((18, "a" * 16), (19, "b" * 16), (20, "c" * 16)):
        key = f"2026-08-{day:02d}"
        repeated.extend(
            editorial_feedback.confirmed_human_exemplars(
                {
                    "product": "daily",
                    "review_status": "approved",
                    "edition_key": key,
                    "review_snapshot_id": f"review-{key}-{suffix}",
                    "selected_items": [
                        human_item(
                            candidate_id=f"human-{day}",
                            selected_url=f"https://publisher.example.com/article/{day}",
                        )
                    ],
                }
            )
        )
    profile = editorial_feedback.compile_profile_from_exemplars(repeated)
    queries = editorial_feedback.collection_queries(profile)
    v.check(
        "D. same human domain/keyword three times creates bounded queries",
        profile["sample_counts"]["manual_domain"].get("publisher.example.com") == 3
        and "site:publisher.example.com AI" in queries
        and "AI 직류배전" in queries
        and len(queries) <= editorial_feedback.COLLECTION_QUERY_LIMIT,
        repr(queries),
    )
    wrapper_review = {
        "product": "daily",
        "review_status": "approved",
        "edition_key": "2026-08-20",
        "review_snapshot_id": SNAPSHOT_ID,
        "selected_items": [
            human_item(selected_url=TARGETLESS_ONECDN),
            human_item(
                candidate_id="outlook-wrapper",
                selected_url="https://nam12.safelinks.protection.outlook.com/?url="
                + quote(PUBLISHER_URL, safe=""),
            ),
        ],
    }
    v.check(
        "E. SafeLinks/onecdn domains never enter learning",
        editorial_feedback.confirmed_human_exemplars(wrapper_review) == []
        and editorial_feedback.canonical_learning_url(TARGETLESS_ONECDN) == "",
    )
    blocked_save = ""
    try:
        operator_review.save_draft(
            review_payload(selected_items=[human_item(selected_url=TARGETLESS_ONECDN)]),
            operator_login="sinabroin",
            client=FakeGitHub(),
        )
    except operator_review.OperatorReviewError as exc:
        blocked_save = exc.code
    v.check("targetless wrapper cannot become durable article authority", blocked_save == "UNSAFE_ARTICLE_URL")

    adjustment = editorial_feedback.adjustment(
        {
            "source": "테스트경제",
            "category": "투자·산업",
            "selected_url": "https://publisher.example.com/article/future",
            "title": "AI 데이터센터 직류배전 신규 투자",
        },
        profile,
    )
    coverage = editorial_briefings.daily_coverage(
        editorial_briefings.datetime.fromisoformat("2026-08-20T07:20:00+09:00")
    )
    unsafe_preference_match = {
        "title": "소비자용 AI 사진 필터 앱 신제품 출시",
        "source": "테스트경제",
        "published_at": "2026-08-20T05:00:00+09:00",
        "url": "https://publisher.example.com/article/consumer-filter",
        "snippet": "개인 사용자를 위한 사진 꾸미기 기능과 구독 상품을 소개했다.",
        "provider": "google_news_rss",
    }
    audit = editorial_briefings.SelectionAuditCounters()
    selected = editorial_briefings.normalize_articles(
        [unsafe_preference_match],
        coverage,
        limit=6,
        resolve_images=False,
        selection_mode=editorial_briefings.SELECTION_MODE_EDITORIAL_PRIORITY,
        selection_audit=audit,
        edition_type="daily",
        operator_review=True,
    )
    source_gate = editorial_briefings.lead_source_eligible_tier(
        "테스트경제", "https://publisher.example.com/article/future"
    )
    builder_source = (ROOT / "scripts" / "build_editorial_review_console.py").read_text(
        encoding="utf-8"
    )
    v.check(
        "F. learning changes bounded ranking signal after hard gates",
        adjustment > 0
        and builder_source.index("normalize_articles(")
        < builder_source.index("editorial_feedback.adjustment(base, profile)"),
        f"adjustment={adjustment}",
    )
    v.check(
        "F. preference cannot bypass materiality or source gates",
        selected == [] and source_gate is False,
        f"selected={len(selected)} source_gate={source_gate} audit={audit.manifest_fields()}",
    )


def verify_publish_dispatch_ordering(v: Verify) -> None:
    print("\n== Publish-before-learning ordering (F1 fault injection) ==")

    # Happy path: the fixed publish_only dispatch fires exactly once, and an
    # identical re-publish takes the idempotent branch without dispatching again.
    client = FakeGitHub()
    saved = operator_review.save_draft(
        review_payload(), operator_login="sinabroin", client=client
    )
    counter = DispatchCounter()
    first = operator_review.publish_daily(
        review_payload(base_revision=saved["revision"]),
        operator_login="sinabroin",
        client=client,
        dispatcher=counter,
    )
    again = operator_review.publish_daily(
        review_payload(
            base_revision=saved["revision"],
            base_approved_revision=first["approved_revision"],
        ),
        operator_login="sinabroin",
        client=client,
        dispatcher=counter,
    )
    v.check(
        "happy path dispatches exactly once and never re-dispatches",
        first["dispatched"] is True
        and first["already_published"] is False
        and again["already_published"] is True
        and again["dispatched"] is False
        and counter.count == 1,
        f"first={first.get('dispatched')} again={again.get('dispatched')} count={counter.count}",
    )

    def run_recovery(label, *, items, fail_predicate, fail_on_hit, expect_added, expect_count):
        client = FaultGitHub(fail_predicate, fail_on_hit=fail_on_hit)
        saved = operator_review.save_draft(
            review_payload(selected_items=items),
            operator_login="sinabroin",
            client=client,
        )
        counter = DispatchCounter()
        first_error = ""
        try:
            operator_review.publish_daily(
                review_payload(selected_items=items, base_revision=saved["revision"]),
                operator_login="sinabroin",
                client=client,
                dispatcher=counter,
            )
        except operator_review.OperatorReviewError as exc:
            first_error = exc.code
        # The approved review is durable and the dispatch has already fired even
        # though the auxiliary learning write failed.
        approved_present = (
            operator_review.approved_review_path("2026-08-20") in client.store
        )
        dispatch_after_first = counter.count
        # The operator retries the identical publish. It must recover the learning
        # corpus/profile with NO second dispatch and NO duplicate/lost sample. The
        # retry deliberately omits base_approved_revision (the first call errored
        # before returning one) to prove recovery needs only the draft revision.
        recovered = operator_review.publish_daily(
            review_payload(selected_items=items, base_revision=saved["revision"]),
            operator_login="sinabroin",
            client=client,
            dispatcher=counter,
        )
        exemplar_paths = sorted(k for k in client.store if _is_exemplar_put(k))
        profile_stored = operator_review.FEEDBACK_PROFILE_PATH in client.store
        exemplars_valid = all(
            editorial_feedback.valid_human_exemplar(client.store[p]["json"])
            for p in exemplar_paths
        )
        v.check(
            label,
            first_error == "LEARNING_PERSIST_FAILED"
            and approved_present
            and dispatch_after_first == 1
            and recovered["already_published"] is True
            and recovered["dispatched"] is False
            and recovered["learning_exemplars_added"] == expect_added
            and recovered["learning_exemplar_count"] == expect_count
            and len(exemplar_paths) == expect_count
            and profile_stored
            and exemplars_valid
            and counter.count == 1,
            f"first_error={first_error} dispatch={counter.count} recovered={recovered} "
            f"exemplars={len(exemplar_paths)} profile={profile_stored}",
        )

    run_recovery(
        "A. exemplar write fails after dispatch → identical retry recovers, dispatch stays 1",
        items=[human_item()],
        fail_predicate=_is_exemplar_put,
        fail_on_hit=1,
        expect_added=1,
        expect_count=1,
    )
    run_recovery(
        "B. profile write fails after dispatch → identical retry recovers, dispatch stays 1",
        items=[human_item()],
        fail_predicate=lambda path: path == operator_review.FEEDBACK_PROFILE_PATH,
        fail_on_hit=1,
        expect_added=0,
        expect_count=1,
    )
    run_recovery(
        "C. partial multi-exemplar failure after dispatch → retry completes, dispatch stays 1",
        items=[
            human_item(),
            human_item(
                candidate_id="manual-human-2",
                selected_url="https://publisher.example.com/article/ai-dc-2",
                title="AI 데이터센터 전력망 2차 계약 체결",
            ),
        ],
        fail_predicate=_is_exemplar_put,
        fail_on_hit=2,
        expect_added=1,
        expect_count=2,
    )


def verify_auth_return(v: Verify) -> None:
    print("\n== OAuth exact-snapshot return ==")
    exact = operator_auth.validated_editor_return_url(
        "daily",
        EDITION_ID,
        "teams_daily",
        SNAPSHOT_ID,
        "2026-08-20",
    )
    expected_prefix = public_urls.editor_snapshot_url(SNAPSHOT_ID)
    encoded = operator_auth.encode_editor_return(
        "daily", EDITION_ID, "teams_daily", SNAPSHOT_ID, "2026-08-20"
    )
    v.check(
        "validated OAuth return targets exact immutable Review snapshot",
        exact.startswith(expected_prefix + "?")
        and f"edition_id={EDITION_ID}" in exact
        and operator_auth.editor_return_url_from_cookie(encoded) == exact,
        exact,
    )
    snapshot_only = operator_auth.validated_editor_return_url(
        "daily", "", "teams_daily", SNAPSHOT_ID, "2026-08-20"
    )
    v.check(
        "plain Editor login returns to exact snapshot path",
        snapshot_only == expected_prefix,
    )
    attacks = (
        ("daily", EDITION_ID, "teams_daily", "https://evil.example/x", "2026-08-20"),
        ("daily", EDITION_ID, "teams_daily", SNAPSHOT_ID, "2026-08-19"),
        ("daily", "https://evil.example/x", "teams_daily", SNAPSHOT_ID, "2026-08-20"),
        ("daily", EDITION_ID, "bad source!", SNAPSHOT_ID, "2026-08-20"),
    )
    v.check(
        "caller-controlled/open-redirect and mismatched identities fail closed",
        all(operator_auth.validated_editor_return_url(*attack) == "" for attack in attacks),
    )


def browser_executable() -> Path | None:
    # GitHub-hosted Ubuntu images ship Google Chrome; a WSL developer host often
    # only has Windows Chrome/Edge reachable through /mnt/c. Both are supported,
    # matching scripts/verify_editorial_review_console.py.
    for candidate in (
        os.environ.get("HDEC_TEST_BROWSER"),
        shutil.which("google-chrome"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        "/mnt/c/Program Files/Google/Chrome/Application/chrome.exe",
        "/mnt/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    ):
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    return None


def _browser_fixture_uri(path: Path, browser: Path) -> str:
    """A ``file://`` URI the chosen browser can actually open.

    A Windows ``.exe`` browser launched from WSL cannot read a Linux ``/tmp``
    path, so the fixture path is translated to its Windows form (``wslpath -w``),
    exactly as the sibling console verifier does; a native Linux browser keeps the
    ordinary ``file://`` URI. Test-only — production runtime is unaffected."""
    if browser.suffix.casefold() != ".exe":
        return path.resolve().as_uri()
    windows_path = subprocess.run(
        ["wslpath", "-w", str(path.resolve())],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return "file:///" + windows_path.replace("\\", "/")


def _browser_argument_path(path: Path, browser: Path) -> str:
    """A filesystem path argument (e.g. --user-data-dir) the browser can use."""
    if browser.suffix.casefold() != ".exe":
        return str(path.resolve())
    return subprocess.run(
        ["wslpath", "-w", str(path.resolve())],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _windows_accessible_tempdir() -> "tempfile.TemporaryDirectory":
    """A temp dir on the Windows %TEMP% mount for a WSL-launched ``.exe`` browser.

    Both the fixture and the user-data-dir live here so Chrome/Edge read a native
    Windows path (``C:\\...``). A Linux ``\\\\wsl.localhost`` UNC path is reachable
    but the 9P read is far too slow for the full rendered console fixture (it
    exceeds the browser timeout), so a real Windows drive is used — the approach
    proven for this repository's WSL hosts."""
    windows_temp_output = subprocess.run(
        ["cmd.exe", "/d", "/c", "echo", "%TEMP%"],
        cwd="/mnt/c",
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    windows_temp = next(
        line.strip()
        for line in reversed(windows_temp_output.splitlines())
        if re.match(r"^[A-Za-z]:\\", line.strip())
    )
    wsl_temp = subprocess.run(
        ["wslpath", "-u", windows_temp],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return tempfile.TemporaryDirectory(
        prefix="r4-ops10b-browser-",
        dir=wsl_temp,
        ignore_cleanup_errors=True,
    )


def browser_acceptance() -> dict:
    browser = browser_executable()
    if browser is None:
        return {"browser_available": False, "error": "Chrome/Chromium unavailable"}
    bundle = {
        "version": 3,
        "edition_type": "daily",
        "edition_key": "2026-08-20",
        "coverage_start": "2026-08-19T07:00:00+09:00",
        "coverage_end": "2026-08-20T06:40:00+09:00",
        "generated_at": "2026-08-20T07:20:00+09:00",
        "candidates": [],
        "article_import_api_url": "https://operator.example.test/api/editorial/import-article",
        "article_import_enabled": True,
    }
    direct_article = {
        "ok": True,
        "article": {
            "input_url": PUBLISHER_URL,
            "discovery_url": PUBLISHER_URL,
            "canonical_url": PUBLISHER_URL,
            "publisher_url": PUBLISHER_URL,
            "publisher_domain": "publisher.example.com",
            "publisher_direct": True,
            "portal_source": "",
            "portal_resolution_reason": "direct_input",
            "portal_fallback_used": False,
            "title": "AI 데이터센터 직류배전 인프라 계약 체결",
            "source": "테스트경제",
            "summary": "AI 데이터센터의 전력 효율을 높이는 직류배전 인프라 계약이 체결됐다. 사업자는 단계별 투자와 공급 일정을 확정했다.",
            "summary_html": "AI 데이터센터의 전력 효율을 높이는 <strong>직류배전 인프라 계약</strong>이 체결됐다. 사업자는 단계별 투자와 공급 일정을 확정했다.",
            "published_at": "2026-08-20T05:00:00+09:00",
            "category": "투자·산업",
            "category_analysis": {
                "category": "투자·산업",
                "scores": {"투자·산업": 5},
                "matched_signals": {"투자·산업": ["계약", "투자"]},
                "reason": "계약·투자 신호",
            },
            "article_text_excerpt": "기사에서 추출한 검증용 짧은 문장입니다.",
            "image_url": (
                "data:image/png;base64,"
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Z4r8AAAAASUVORK5CYII="
            ),
            "extraction": {
                "image_source": "og_image",
                "image_result": "image_reencoded",
            },
        },
    }
    wrapped_target = "https://publisher.example.com/article/wrapped-2"
    safelink_url = TARGETLESS_ONECDN + "?" + urlencode({"url": wrapped_target})
    wrapped_response = copy.deepcopy(direct_article)
    wrapped_response["article"].update(
        {
            "input_url": safelink_url,
            "discovery_url": safelink_url,
            "canonical_url": wrapped_target,
            "publisher_url": wrapped_target,
            "title": "AI 데이터센터 전력망 두 번째 계약",
            "portal_source": "microsoft_safelinks",
            "portal_resolution_reason": "microsoft_safelinks_explicit_target",
        }
    )
    prelude = """
<script>
window.__ops10bAuth=false;
window.__ops10bImportCalls=[];
window.__ops10bDirect=__DIRECT__;
window.__ops10bWrapped=__WRAPPED__;
window.fetch=async function(url,options={}){
  const requestUrl=String(url||"");
  if(requestUrl.endsWith("manifest.json"))return {ok:true,status:200,json:async()=>({review_snapshot_id:"__SNAPSHOT__"})};
  if(requestUrl.endsWith("/api/auth/session"))return {ok:true,status:200,json:async()=>({authenticated:window.__ops10bAuth,login:window.__ops10bAuth?"sinabroin":""})};
  if(requestUrl.endsWith("/api/editorial/import-article")){
    const request=JSON.parse(options.body||"{}");
    window.__ops10bImportCalls.push(request.url||"");
    if(!window.__ops10bAuth)return {ok:false,status:401,json:async()=>({ok:false,error:{code:"AUTH_REQUIRED",message:"운영자 로그인이 필요합니다."}})};
    if(request.url==="__TARGETLESS__")return {ok:false,status:422,json:async()=>({ok:false,error:{code:"MICROSOFT_SAFELINK_TARGET_MISSING",message:"__TARGETLESS_MESSAGE__"}})};
    if(request.url==="__SAFELINK__")return {ok:true,status:200,json:async()=>window.__ops10bWrapped};
    return {ok:true,status:200,json:async()=>window.__ops10bDirect};
  }
  throw new Error("unexpected fixture fetch: "+requestUrl);
};
</script>
"""
    replacements = {
        "__DIRECT__": json.dumps(direct_article, ensure_ascii=False),
        "__WRAPPED__": json.dumps(wrapped_response, ensure_ascii=False),
        "__SNAPSHOT__": SNAPSHOT_ID,
        "__TARGETLESS__": TARGETLESS_ONECDN,
        "__TARGETLESS_MESSAGE__": TARGETLESS_MESSAGE,
        "__SAFELINK__": safelink_url,
    }
    for key, value in replacements.items():
        prelude = prelude.replace(key, value.replace("</", "<\\/") if key in {"__DIRECT__", "__WRAPPED__"} else value)
    harness = """
<script>
(async()=>{
  const result={browser_available:true};
  const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
  const input=document.getElementById("importUrl");
  const paste=async value=>{
    input.value=value;
    input.dispatchEvent(new Event("input",{bubbles:true}));
    input.dispatchEvent(new Event("paste",{bubbles:true}));
    await pause(350);
  };
  await pause(250);
  result.zero_candidate_editor=allCandidates().length===0&&document.getElementById("candidateList").textContent.includes("현재 기준을 충족");
  result.import_api_configured=!input.disabled&&!document.getElementById("importBtn").disabled;
  result.unauth_login_cta_visible=!document.getElementById("importAuth").hidden&&document.getElementById("importLoginCta").textContent.includes("GitHub");
  await paste("__PUBLISHER__");
  result.auth_pending_retained=input.value==="__PUBLISHER__"&&pendingImportUrl()==="__PUBLISHER__"&&document.getElementById("manualUrl").value==="__PUBLISHER__";
  result.auth_failure_not_added=allCandidates().length===0&&document.getElementById("importStatus").classList.contains("error")&&!document.getElementById("importStatus").classList.contains("success");
  window.__ops10bAuth=true;
  await probeImportAuth();
  result.authenticated_cta_hidden=document.getElementById("importAuth").hidden===true;
  await paste("__PUBLISHER__");
  const imported=state.manualCandidates.find(item=>item.collection_source_kind==="url_import"&&item.selected_url==="__PUBLISHER__");
  result.direct_complete_card=!!imported&&imported.source==="테스트경제"&&!!imported.title&&!!imported.summary&&imported.category==="투자·산업"&&!!imported.image_url&&state.selected.includes(imported.candidate_id);
  result.right_preview_populated=!!imported&&!!document.querySelector(`[data-selected-id="${imported.candidate_id}"]`)&&document.getElementById("preview").textContent.includes(imported.title);
  result.import_success_state=document.getElementById("importStatus").classList.contains("success");
  const manual=document.getElementById("manualFallback");
  manual.open=true;
  document.getElementById("manualUrl").value="https://manual.example.com/article/3";
  document.getElementById("manualSource").value="직접뉴스";
  document.getElementById("manualTitle").value="직접 입력 AI 인프라 기사";
  document.getElementById("manualSummary").value="운영자가 검증해 직접 입력한 요약입니다.";
  document.getElementById("manualAddBtn").click();
  result.manual_fallback_usable=state.manualCandidates.some(item=>item.source==="직접뉴스"&&state.selected.includes(item.candidate_id));
  await paste("__SAFELINK__");
  const wrapped=state.manualCandidates.find(item=>item.selected_url==="__WRAPPED_TARGET__");
  result.explicit_safelink_unwrap=!!wrapped&&!wrapped.selected_url.includes("microsoft")&&!wrapped.selected_url.includes("safelinks");
  const beforeTargetless=allCandidates().length;
  await paste("__TARGETLESS__");
  result.targetless_specific_error=document.getElementById("importStatus").textContent==="__TARGETLESS_MESSAGE__"&&document.getElementById("importStatus").classList.contains("error");
  result.targetless_no_false_green=allCandidates().length===beforeTargetless&&!document.getElementById("importStatus").classList.contains("success")&&!document.getElementById("importStatus").textContent.includes("추가했습니다");
  result.manual_url_prefilled_targetless=document.getElementById("manualUrl").value==="__TARGETLESS__";
  const marker=document.createElement("pre");
  marker.id="ops10b-browser-result";
  marker.textContent=JSON.stringify(result);
  document.body.appendChild(marker);
})().catch(error=>{
  const marker=document.createElement("pre");
  marker.id="ops10b-browser-result";
  marker.textContent=JSON.stringify({browser_available:true,error:String(error),stack:error&&error.stack||""});
  document.body.appendChild(marker);
});
</script>
"""
    for key, value in {
        "__PUBLISHER__": PUBLISHER_URL,
        "__SAFELINK__": safelink_url,
        "__WRAPPED_TARGET__": wrapped_target,
        "__TARGETLESS__": TARGETLESS_ONECDN,
        "__TARGETLESS_MESSAGE__": TARGETLESS_MESSAGE,
    }.items():
        harness = harness.replace(key, value)
    windows_browser = browser.suffix.casefold() == ".exe"
    # A WSL-launched Windows browser cannot read a Linux user-data-dir and reads a
    # large Linux fixture over the \\wsl.localhost 9P mount far too slowly, so both
    # the fixture and profile live on the Windows %TEMP% mount; a native Linux
    # browser keeps them in an ordinary Linux temp dir.
    base_handle = (
        _windows_accessible_tempdir()
        if windows_browser
        else tempfile.TemporaryDirectory(prefix="r4-ops10b-browser-")
    )
    try:
        base = Path(base_handle.name)
        html = console_builder.render_console(
            (ROOT / "templates" / "editorial_review_console.html").read_text(encoding="utf-8"),
            bundle,
        )
        html = html.replace('<script id="candidate-data"', prelude + '<script id="candidate-data"', 1)
        before_body, after_body = html.rsplit("</body>", 1)
        html = before_body + harness + "</body>" + after_body
        fixture = base / "index.html"
        fixture.write_text(html, encoding="utf-8")
        profile = base / "profile"
        command = [
            str(browser),
            "--headless=new",
            "--disable-gpu",
            "--disable-background-networking",
            "--disable-component-update",
            "--disable-default-apps",
            "--disable-sync",
            "--metrics-recording-only",
            "--no-first-run",
            "--no-default-browser-check",
            "--allow-file-access-from-files",
            "--virtual-time-budget=7000",
            f"--user-data-dir={_browser_argument_path(profile, browser)}",
            "--dump-dom",
            _browser_fixture_uri(fixture, browser),
        ]
        if not windows_browser:
            # GitHub-hosted runners can deny Chromium's user-namespace sandbox
            # and expose a very small /dev/shm. This fixture opens local files
            # only (window.fetch is replaced above), so these Linux CI flags do
            # not weaken any production browser or network boundary.
            command[2:2] = ["--no-sandbox", "--disable-dev-shm-usage"]
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
    finally:
        base_handle.cleanup()
    match = re.search(r'<pre id="ops10b-browser-result">([^<]+)</pre>', completed.stdout)
    if completed.returncode != 0 or not match:
        return {
            "browser_available": True,
            "error": (
                f"returncode={completed.returncode} stderr={completed.stderr[-600:]!r} "
                f"stdout_tail={completed.stdout[-2400:]!r}"
            ),
        }
    return json.loads(unescape(match.group(1)))


def verify_browser(v: Verify) -> None:
    print("\n== Zero-candidate browser fixture ==")
    result = browser_acceptance()
    v.check("headless browser fixture executed", result.get("browser_available") is True, repr(result))
    for key, label in (
        ("zero_candidate_editor", "zero-candidate Editor renders honestly"),
        ("import_api_configured", "configured import API is enabled"),
        ("unauth_login_cta_visible", "unauthenticated GitHub login CTA is visible"),
        ("auth_pending_retained", "authentication failure retains pending URL"),
        ("auth_failure_not_added", "authentication failure does not add article"),
        ("authenticated_cta_hidden", "authenticated session clears login prompt"),
        ("direct_complete_card", "direct publisher URL creates complete selected card"),
        ("right_preview_populated", "right preview populates automatically"),
        ("import_success_state", "successful import reports success"),
        ("manual_fallback_usable", "manual fallback remains usable"),
        ("explicit_safelink_unwrap", "explicit SafeLinks target adds publisher URL"),
        ("targetless_specific_error", "targetless onecdn shows specific guidance"),
        ("targetless_no_false_green", "targetless error has no false-green added state"),
        ("manual_url_prefilled_targetless", "targetless URL remains in manual fallback"),
    ):
        v.check(label, result.get(key) is True, repr(result))


def main() -> int:
    v = Verify()
    verify_safelinks_and_urls(v)
    verify_learning(v)
    verify_publish_dispatch_ordering(v)
    verify_auth_return(v)
    verify_browser(v)
    print(f"\nchecks={v.checks} failures={v.failures}")
    if v.failures:
        print("RESULT=R4_OPS10B_INTAKE_LEARNING_FAIL")
        return 1
    print("RESULT=R4_OPS10B_INTAKE_LEARNING_PASS")
    print("BROWSER_FIXTURE=PASS")
    print("LEARNING_EVIDENCE=PASS")
    print("PRODUCTION_MUTATIONS=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
