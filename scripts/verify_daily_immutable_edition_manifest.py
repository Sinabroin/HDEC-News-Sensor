#!/usr/bin/env python3
"""D7-AK-6E R4-R12 §6 — immutable Daily manifest PRODUCTION-PATH wiring.

This verifier proves the content-addressed immutable Daily edition manifest and
the fail-closed public-resource gate are wired into the *actual* production Daily
path (``scripts/run_editorial_briefing.py`` +
``.github/workflows/editorial-daily-brief.yml``), not a standalone helper. It
inspects the real source/workflow AND executes the real production functions:

* ``render_daily`` builds the content-addressed manifest and forms the editor
  CTA FROM that manifest's ``edition_id`` (never independently);
* ``run_editorial_briefing.write_daily_edition_manifest`` persists it append-only
  under ``docs/editorial/daily/editions/daily-YYYY-MM-DD-<digest>.json`` and
  refuses to overwrite a differing edition (collision fails closed);
* ``run_editorial_briefing.run_verify_public`` reconstructs the public dated page
  AND the public immutable manifest before the edition may be claimed, failing
  closed with the exact ``daily_public_resource_verification_failed`` reason;
* the workflow runs ``--verify-public`` after commit/push and before ``--claim``,
  and ``--claim`` before ``--send`` (send gated on the durable claim);
* no reader-only Daily send path exists.

Offline: network 0 (openers injected), sends 0, commits 0, pushes 0, production
state writes 0.

Markers: DAILY_IMMUTABLE_MANIFEST_PRODUCTION_WIRING, DAILY_PUBLIC_VERIFY_BEFORE_CLAIM,
DAILY_CLAIM_BEFORE_SEND, DAILY_EDITOR_CTA_FROM_MANIFEST, DAILY_READER_ONLY_SEND_ALLOWED.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import socket
import sys
import tempfile
from datetime import datetime
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app import ai_centrality  # noqa: E402
from app import editorial_briefing_state  # noqa: E402
from app import daily_publication  # noqa: E402
from app import editorial_briefings as eb  # noqa: E402
from app import public_urls as public_url_contract  # noqa: E402
import run_editorial_briefing as runner  # noqa: E402

KST = eb.KST
ROOT_URL = "https://preview.fixture.test/HDEC-News-Sensor"
REPORT_URL = f"{ROOT_URL}/daily/dashboard-latest.html"
RUN_AT = datetime(2026, 8, 6, 7, 20, tzinfo=KST)

CHECKS = 0
FAILURES: list[str] = []


def _network_blocked(*_args, **_kwargs):
    raise AssertionError("network disabled in verifier")


socket.create_connection = _network_blocked
socket.getaddrinfo = _network_blocked


def check(name: str, ok: object, detail: object = "") -> None:
    global CHECKS
    CHECKS += 1
    if ok:
        print(f"PASS: {name}")
    else:
        FAILURES.append(name)
        print(f"FAIL: {name}" + (f" — {detail}" if detail else ""))


class _FakeResp:
    def __init__(self, status: int, body: str | bytes) -> None:
        self.status = status
        self._body = body.encode("utf-8") if isinstance(body, str) else body

    def read(self, _n: int = -1) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> bool:
        return False


class _SMTPRecorder:
    def __init__(self) -> None:
        self.attempts = 0

    def __call__(self, *_args, **_kwargs):
        self.attempts += 1
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> bool:
        return False

    def ehlo(self):
        return 250, b"offline"

    def starttls(self, **_kwargs):
        return 220, b"offline"

    def login(self, *_args):
        return 235, b"offline"

    def mail(self, *_args):
        return 250, b"offline"

    def rcpt(self, *_args):
        return 250, b"offline"

    def data(self, *_args):
        return 250, b"accepted"


def article(title: str, url: str) -> eb.EditorialArticle:
    return eb.EditorialArticle(
        title=title,
        summary="국가 AI 데이터센터 전력 인프라 계약이 확정됐다.",
        source="연합뉴스",
        published_at=RUN_AT,
        selected_url=url,
        link_kind="article",
        link_label="연합뉴스",
        category="투자·산업",
        ai_centrality_level=ai_centrality.LEVEL_EXPLICIT_AI_CORE,
    )


def render() -> eb.RenderedEdition:
    rows = [
        article("현대건설 AI 데이터센터 전력 인프라 계약 체결",
                "https://www.yna.co.kr/view/AI-lead-1"),
        article("국가 AI 반도체 클러스터 전력망 증설 확정",
                "https://www.yna.co.kr/view/AI-lead-2"),
        article("과기정통부 국가 AI 컴퓨팅센터 착공",
                "https://www.yna.co.kr/view/AI-lead-3"),
    ]
    edition = eb.render_daily(
        rows, run_at=RUN_AT, root_url=ROOT_URL, editor_console_available=True
    )
    eb.validate_rendered(edition)
    return edition


@contextlib.contextmanager
def production_env(github_output: Path):
    previous = {}
    values = {
        "EDITORIAL_PRODUCTION": "1",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_ACTIONS": "true",
        "GITHUB_RUN_ID": "8100",
        "GITHUB_RUN_ATTEMPT": "1",
        "REPORT_URL": REPORT_URL,
        "GITHUB_OUTPUT": str(github_output),
        "GMAIL_SMTP_USER": "offline-sender@example.test",
        "GMAIL_SMTP_APP_PASSWORD": "offline-password",
        "ALERT_EMAIL_FROM": "offline-sender@example.test",
        "TEAMS_CHANNEL_EMAIL": "offline-channel@example.test",
    }
    for key, value in values.items():
        previous[key] = os.environ.get(key)
        os.environ[key] = value
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def main() -> int:
    valid_image_authority_accepted = False
    image_authority_rejected = {
        "missing": False,
        "false": False,
        "malformed": False,
    }
    today_daily_rehearsal = False
    today_daily_fake_sends = 0
    today_daily_duplicate_resend = -1
    today_daily_production_writes = -1
    edition = render()
    manifest = edition.edition_manifest or {}
    edition_id = edition.edition_id
    manifest_url = public_url_contract.daily_edition_manifest_url(
        edition_id, root_url=ROOT_URL
    )
    dated_url = edition.public_dated_url

    # ---- §6.5 the manifest is the content-addressed immutable manifest -------- #
    check("edition manifest reconstructs fail-closed",
          eb.verify_daily_edition_manifest(manifest) == "")
    check("edition_id is content-addressed (date + integrity digest)",
          edition_id == f"daily-{edition.edition_key}-"
          f"{manifest['integrity']['digest'][:16]}"
          and public_url_contract.parse_daily_edition_id(edition_id)
          == edition.edition_key)
    tampered = json.loads(json.dumps(manifest))
    tampered["articles"][0]["title"] = "조작된 제목"
    check("manifest tamper is detected fail-closed",
          eb.verify_daily_edition_manifest(tampered) != "")

    # ---- §6.13 editor CTA is formed FROM the manifest, never independently ---- #
    expected_cta = public_url_contract.daily_editor_console_url(
        manifest["edition_id"], root_url=ROOT_URL
    )
    check("editor CTA is the exact-edition console URL derived from the manifest id",
          bool(expected_cta) and edition.editor_url == expected_cta)
    check("editor CTA is carried in the Teams text and HTML that get sent",
          edition.editor_url in edition.teams_text
          and escape(edition.editor_url, quote=True) in edition.teams_html)
    check("editor CTA parses back to this exact edition",
          public_url_contract.parse_daily_edition_id(edition_id) == edition.edition_key)
    DAILY_EDITOR_CTA_FROM_MANIFEST = not FAILURES

    # ---- §6.5-8 write_daily_edition_manifest is the production writer --------- #
    with tempfile.TemporaryDirectory(prefix="r4r12-manifest-") as raw:
        docs_root = Path(raw)
        relpath = runner.write_daily_edition_manifest(edition, docs_root=docs_root)
        check("production writer targets docs/editorial/daily/editions/<id>.json",
              relpath == f"docs/editorial/daily/editions/{edition_id}.json")
        written = docs_root / relpath
        check("manifest file exists on disk after write", written.is_file())
        check("persisted manifest reconstructs fail-closed",
              eb.verify_daily_edition_manifest(
                  json.loads(written.read_text(encoding="utf-8"))) == "")
        # byte-identical rewrite is idempotent (append-only allows the no-op)
        again = runner.write_daily_edition_manifest(edition, docs_root=docs_root)
        check("byte-identical rewrite is idempotent", again == relpath)
        # a differing payload at an existing immutable path fails closed
        written.write_text(
            json.dumps({**json.loads(written.read_text('utf-8')), "x": 1}),
            encoding="utf-8",
        )
        try:
            runner.write_daily_edition_manifest(edition, docs_root=docs_root)
        except runner.OrchestratorError:
            check("differing payload at an existing immutable path fails closed", True)
        else:
            check("differing payload at an existing immutable path fails closed", False)

    # ---- §6.10 public-resource verify: unit-level reconstruction ------------- #
    good = runner.verify_public_edition_manifest_once(
        manifest_url, edition_id,
        opener=lambda *_a, **_k: _FakeResp(200, json.dumps(manifest)),
    )
    check("public manifest verify accepts a matching reconstructable manifest", good)
    bad_cases = {
        "404": lambda *_a, **_k: _FakeResp(404, json.dumps(manifest)),
        "wrong edition id": lambda *_a, **_k: _FakeResp(
            200, json.dumps({**manifest, "edition_id": "daily-2026-08-06-deadbeefdeadbeef"})),
        "tampered digest": lambda *_a, **_k: _FakeResp(200, json.dumps(tampered)),
        "non-JSON body": lambda *_a, **_k: _FakeResp(200, "<html>not json</html>"),
    }
    for label, opener in bad_cases.items():
        check(f"public manifest verify rejects {label}",
              not runner.verify_public_edition_manifest_once(
                  manifest_url, edition_id, opener=opener))

    # ---- §6.10-11 run_verify_public executed end-to-end (before claim) -------- #
    with tempfile.TemporaryDirectory(prefix="r4r12-verifypublic-") as raw:
        tmp = Path(raw)
        verification_edition = eb.render_daily(
            [],
            run_at=RUN_AT,
            root_url=ROOT_URL,
            editor_console_available=True,
        )
        eb.validate_rendered(verification_edition)
        verification_manifest = verification_edition.edition_manifest or {}
        verification_manifest_url = public_url_contract.daily_edition_manifest_url(
            verification_edition.edition_id,
            root_url=ROOT_URL,
        )
        verification_dated_url = verification_edition.public_dated_url
        dated_path = tmp / f"{verification_edition.edition_key}.html"
        latest_path = tmp / "latest.html"
        payload = verification_edition.html.encode("utf-8")
        dated_path.write_bytes(payload)
        latest_path.write_bytes(payload)
        runtime_dir = tmp / "runtime"
        runtime_dir.mkdir()
        runtime_manifest = eb.manifest_for_runtime(
            verification_edition,
            dated_path,
            latest_path,
        )
        # Mirror the authority minted by the real run_publish() path. The
        # verifier must exercise the exact GitHub Actions gate, not bypass it
        # merely because a local shell lacks GITHUB_ACTIONS.
        runtime_manifest["production_image_gate_required"] = True
        runner._write_runtime_manifest(runtime_dir, runtime_manifest)
        github_output = tmp / "gh-output.txt"
        github_output.touch()

        def resolving_opener(url, *_a, **_k):
            if url == verification_dated_url:
                return _FakeResp(200, verification_edition.html)
            if url == verification_manifest_url:
                return _FakeResp(200, json.dumps(verification_manifest))
            return _FakeResp(404, "")

        def manifest_missing_opener(url, *_a, **_k):
            if url == verification_dated_url:
                return _FakeResp(200, verification_edition.html)
            return _FakeResp(404, "")

        original_docs_paths = runner._docs_paths
        runner._docs_paths = lambda _t, _k: (dated_path, latest_path)
        try:
            with production_env(github_output):
                check(
                    "run_verify_public positive path explicitly uses GitHub Actions semantics",
                    os.environ.get("GITHUB_ACTIONS") == "true",
                )
                verified = runner.run_verify_public(
                    "daily", run_at=RUN_AT, runtime_dir=runtime_dir,
                    opener=resolving_opener, publication_timeout_seconds=0,
                )
            valid_image_authority_accepted = bool(verified)
            check("run_verify_public passes when both resources reconstruct", verified)
            outputs = dict(
                line.split("=", 1)
                for line in github_output.read_text(encoding="utf-8").splitlines()
                if "=" in line
            )
            check("run_verify_public emits resources_verified=true",
                  outputs.get("resources_verified") == "true")

            for label, authority in (
                ("missing", None),
                ("false", False),
                ("malformed", "true"),
            ):
                invalid_manifest = dict(runtime_manifest)
                if label == "missing":
                    invalid_manifest.pop("production_image_gate_required", None)
                else:
                    invalid_manifest["production_image_gate_required"] = authority
                runner._write_runtime_manifest(runtime_dir, invalid_manifest)
                with production_env(github_output):
                    try:
                        runner.run_verify_public(
                            "daily",
                            run_at=RUN_AT,
                            runtime_dir=runtime_dir,
                            opener=_network_blocked,
                            publication_timeout_seconds=0,
                        )
                    except runner.OrchestratorError as exc:
                        gate_failure = str(exc)
                    else:
                        gate_failure = ""
                image_authority_rejected[label] = (
                    gate_failure == "production image gate authority missing"
                )
                check(
                    f"GitHub Actions rejects {label} production image authority",
                    image_authority_rejected[label],
                    gate_failure,
                )
            runner._write_runtime_manifest(runtime_dir, runtime_manifest)

            github_output.write_text("", encoding="utf-8")
            with production_env(github_output):
                try:
                    runner.run_verify_public(
                        "daily", run_at=RUN_AT, runtime_dir=runtime_dir,
                        opener=manifest_missing_opener, publication_timeout_seconds=0,
                    )
                except runner.OrchestratorError as exc:
                    fail_reason = str(exc)
                else:
                    fail_reason = ""
            check("run_verify_public fails closed when the public manifest is absent",
                  fail_reason == daily_publication.SKIP_PUBLIC_RESOURCE)
            skip_outputs = dict(
                line.split("=", 1)
                for line in github_output.read_text(encoding="utf-8").splitlines()
                if "=" in line
            )
            check("failure emits the exact machine-readable skip reason",
                  skip_outputs.get("skip_reason") == daily_publication.SKIP_PUBLIC_RESOURCE)
        finally:
            runner._docs_paths = original_docs_paths
    DAILY_PUBLIC_VERIFY_BEFORE_CLAIM = not FAILURES

    # ---- R4-OPS-9 exact 2026-08-18 offline recovery rehearsal ---------------- #
    production_state_path = ROOT / "data" / "editorial_daily_state.json"
    production_state_before = hashlib.sha256(
        production_state_path.read_bytes()
    ).hexdigest()
    with tempfile.TemporaryDirectory(prefix="r4-ops-9-today-daily-") as raw:
        isolated_root = Path(raw)
        isolated_review = (
            isolated_root / "docs" / "editorial" / "review" / "2026-08-18"
        )
        isolated_review.parent.mkdir(parents=True)
        shutil.copytree(
            ROOT / "docs" / "editorial" / "review" / "2026-08-18",
            isolated_review,
        )
        observed_review_manifest = json.loads(
            (isolated_review / "manifest.json").read_text(encoding="utf-8")
        )
        runtime_dir = isolated_root / "runtime"
        state_path = isolated_root / "daily-state.json"
        github_output = isolated_root / "gh-output.txt"
        github_output.touch()
        original_runner_root = runner.ROOT
        original_load_state = runner.editorial_briefing_state.load_state
        today_run_at = datetime.fromisoformat("2026-08-18T07:50:00+09:00")
        smtp = _SMTPRecorder()
        try:
            runner.ROOT = isolated_root
            runner.editorial_briefing_state.load_state = (
                lambda edition_type, path=None: editorial_briefing_state.empty_state(
                    edition_type
                )
            )
            with production_env(github_output):
                os.environ["REPORT_URL"] = public_url_contract.CANONICAL_DASHBOARD_URL
                published = runner.run_publish(
                    "daily",
                    run_at=today_run_at,
                    runtime_dir=runtime_dir,
                )
            runner.editorial_briefing_state.load_state = original_load_state
            if published is None:
                raise AssertionError("2026-08-18 Daily did not publish in isolation")

            dated_path, _latest_path = runner._docs_paths("daily", "2026-08-18")
            edition_manifest_path = isolated_root / published["edition_manifest_path"]
            edition_manifest_url = public_url_contract.daily_edition_manifest_url(
                published["edition_id"],
                root_url=public_url_contract.PUBLIC_ROOT,
            )
            public_resources = {
                published["public_dated_url"]: dated_path.read_bytes(),
                edition_manifest_url: edition_manifest_path.read_bytes(),
            }

            def today_opener(request, *_args, **_kwargs):
                url = request if isinstance(request, str) else request.full_url
                if url not in public_resources:
                    raise OSError("unexpected 2026-08-18 Daily public URL")
                return _FakeResp(200, public_resources[url])

            with production_env(github_output):
                os.environ["REPORT_URL"] = public_url_contract.CANONICAL_DASHBOARD_URL
                public_verified = runner.run_verify_public(
                    "daily",
                    run_at=today_run_at,
                    runtime_dir=runtime_dir,
                    opener=today_opener,
                    publication_timeout_seconds=0,
                )
                claimed = runner.run_claim(
                    "daily",
                    run_at=today_run_at,
                    runtime_dir=runtime_dir,
                    opener=today_opener,
                    state_path=state_path,
                    publication_timeout_seconds=0,
                )
                sent = runner.run_send(
                    "daily",
                    run_at=today_run_at,
                    runtime_dir=runtime_dir,
                    state_path=state_path,
                    smtp_factory=smtp,
                )
                retry_blocked = False
                try:
                    runner.run_send(
                        "daily",
                        run_at=today_run_at,
                        runtime_dir=runtime_dir,
                        state_path=state_path,
                        smtp_factory=smtp,
                    )
                except editorial_briefing_state.StateError:
                    retry_blocked = True
        finally:
            runner.ROOT = original_runner_root
            runner.editorial_briefing_state.load_state = original_load_state

        today_daily_fake_sends = smtp.attempts
        today_daily_duplicate_resend = max(0, smtp.attempts - 1)
        today_daily_production_writes = int(
            hashlib.sha256(production_state_path.read_bytes()).hexdigest()
            != production_state_before
        )
        today_daily_rehearsal = bool(
            observed_review_manifest.get("review_snapshot_id")
            == "review-2026-08-18-0752211bdb36c38e"
            and public_verified
            and claimed is not None
            and sent is not None
            and published.get("edition_key") == "2026-08-18"
            and published.get("article_count") == 0
            and published.get("issue_mode") == "daily_empty_status"
            and published.get("production_image_gate_required") is True
            and retry_blocked
            and today_daily_fake_sends == 1
            and today_daily_duplicate_resend == 0
            and today_daily_production_writes == 0
        )
        check(
            "2026-08-18 exact Review artifacts reach truthful Daily claim/send once",
            today_daily_rehearsal,
            published,
        )

    # ---- §6.5-7 production source wiring -------------------------------------- #
    runner_source = Path(runner.__file__).read_text(encoding="utf-8")
    check("run_publish calls the immutable manifest builder",
          "write_daily_edition_manifest(edition)" in runner_source)
    check("the manifest path is returned through the actual publish output",
          '_github_output("edition_manifest_path", edition_manifest_output)'
          in runner_source)
    check("run_verify_public is the pre-claim public-resource gate",
          "def run_verify_public(" in runner_source
          and "poll_public_edition_manifest(" in runner_source)
    check("CLI + dispatch order: verify-public before claim before send",
          runner_source.index('"--verify-public"')
          < runner_source.index('"--claim"')
          < runner_source.index('"--send"')
          and runner_source.index("elif args.verify_public:")
          < runner_source.index("elif args.claim:"))
    check("run_send refuses to send without the durable claim (no reader-only)",
          "require_claim_owner(" in runner_source
          and runner_source.index("def run_claim(")
          < runner_source.index("def run_send("))
    image_gate_weakened = not (
        'manifest.get("production_image_gate_required") is not True'
        in runner_source
        and 'raise OrchestratorError("production image gate authority missing")'
        in runner_source
    )
    check("production image authority gate remains fail-closed", not image_gate_weakened)
    check("no reader-only live-collection fallback path exists",
          'review_mode = "live_collection_fallback"' not in runner_source
          and "daily_reader_only_send_allowed" in runner_source)
    DAILY_IMMUTABLE_MANIFEST_PRODUCTION_WIRING = not FAILURES

    # ---- §6.8-12 workflow wiring --------------------------------------------- #
    workflow = (ROOT / ".github" / "workflows" / "editorial-daily-brief.yml").read_text(
        encoding="utf-8"
    )
    check("workflow stages the immutable manifest through an explicit allowlist",
          'EDITION_MANIFEST_PATH: ${{ steps.publish.outputs.edition_manifest_path }}'
          in workflow
          and 'git add -- "$EDITION_MANIFEST_PATH"' in workflow)
    check("workflow runs --verify-public after commit/push and before --claim",
          "--verify-public" in workflow
          and workflow.index("--verify-public") > workflow.index("git push origin HEAD:main")
          and workflow.index("--verify-public") < workflow.index("--claim"))
    check("workflow runs --claim before --send",
          workflow.index("--claim") < workflow.index("--send"))
    check("send step is gated on the durable claim's send authorization",
          "steps.claim.outputs.send_authorized == 'true'" in workflow)
    check("verify-public and claim are explicitly chained (no reader-only bypass)",
          "if: success() && steps.publish.outputs.delivery_authorized == 'true'"
          in workflow
          and "if: success() && steps.verify_public.outputs.resources_verified == 'true'"
          in workflow)
    DAILY_CLAIM_BEFORE_SEND = not FAILURES

    print(f"checks={CHECKS} failures={len(FAILURES)}")
    ok = not FAILURES
    github_actions_parity = (
        valid_image_authority_accepted
        and all(image_authority_rejected.values())
    )
    print(
        "DAILY_GITHUB_ACTIONS_PARITY="
        + ("PASS" if github_actions_parity and ok else "FAIL")
    )
    print(
        "DAILY_VALID_IMAGE_AUTHORITY_ACCEPTED="
        + ("PASS" if valid_image_authority_accepted and ok else "FAIL")
    )
    print(
        "DAILY_MISSING_IMAGE_AUTHORITY_REJECTED="
        + ("PASS" if image_authority_rejected["missing"] and ok else "FAIL")
    )
    print(
        "DAILY_FALSE_IMAGE_AUTHORITY_REJECTED="
        + ("PASS" if image_authority_rejected["false"] and ok else "FAIL")
    )
    print(
        "DAILY_MALFORMED_IMAGE_AUTHORITY_REJECTED="
        + ("PASS" if image_authority_rejected["malformed"] and ok else "FAIL")
    )
    print("DAILY_IMAGE_GATE_WEAKENED=" + str(image_gate_weakened).lower())
    print(
        "TODAY_DAILY_REHEARSAL="
        + ("PASS" if today_daily_rehearsal and ok else "FAIL")
    )
    print(f"TODAY_DAILY_FAKE_SMTP_SENDS={today_daily_fake_sends}")
    print(f"TODAY_DAILY_DUPLICATE_RESEND={today_daily_duplicate_resend}")
    print(f"TODAY_DAILY_REAL_PRODUCTION_WRITES={today_daily_production_writes}")
    print(
        "daily_immutable_manifest_production_wiring="
        + ("PASS" if ok else "FAIL")
        + " DAILY_IMMUTABLE_MANIFEST_PRODUCTION_WIRING="
        + ("PASS" if DAILY_IMMUTABLE_MANIFEST_PRODUCTION_WIRING and ok else "FAIL")
        + " DAILY_PUBLIC_VERIFY_BEFORE_CLAIM="
        + ("PASS" if DAILY_PUBLIC_VERIFY_BEFORE_CLAIM and ok else "FAIL")
        + " DAILY_CLAIM_BEFORE_SEND="
        + ("PASS" if DAILY_CLAIM_BEFORE_SEND and ok else "FAIL")
        + " DAILY_EDITOR_CTA_FROM_MANIFEST="
        + ("PASS" if DAILY_EDITOR_CTA_FROM_MANIFEST and ok else "FAIL")
        + " DAILY_READER_ONLY_SEND_ALLOWED="
        + str(daily_publication.READER_ONLY_SEND_ALLOWED).lower()
        + " network_calls=0 smtp_attempts=0 teams_sends=0 telegram_sends=0"
        + " commits=0 pushes=0 production_state_writes=0"
    )
    if FAILURES:
        for name in FAILURES:
            print(f"FAILED: {name}")
        return 1
    print("RESULT=D7-AK-6E_R4R12_DAILY_IMMUTABLE_MANIFEST_PRODUCTION_WIRING_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
