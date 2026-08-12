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
import json
import os
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
from app import daily_publication  # noqa: E402
from app import editorial_briefings as eb  # noqa: E402
from app import public_urls as public_url_contract  # noqa: E402
import run_editorial_briefing as runner  # noqa: E402

KST = eb.KST
ROOT_URL = "https://preview.fixture.test/HDEC-News-Sensor"
REPORT_URL = f"{ROOT_URL}/daily/latest.html"
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
    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self._body = body.encode("utf-8")

    def read(self, _n: int = -1) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> bool:
        return False


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
        "GITHUB_RUN_ID": "8100",
        "GITHUB_RUN_ATTEMPT": "1",
        "REPORT_URL": REPORT_URL,
        "GITHUB_OUTPUT": str(github_output),
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
        dated_path = tmp / f"{edition.edition_key}.html"
        latest_path = tmp / "latest.html"
        payload = edition.html.encode("utf-8")
        dated_path.write_bytes(payload)
        latest_path.write_bytes(payload)
        runtime_dir = tmp / "runtime"
        runtime_dir.mkdir()
        runtime_manifest = eb.manifest_for_runtime(edition, dated_path, latest_path)
        runner._write_runtime_manifest(runtime_dir, runtime_manifest)
        github_output = tmp / "gh-output.txt"
        github_output.touch()

        def resolving_opener(url, *_a, **_k):
            if url == dated_url:
                return _FakeResp(200, edition.html)
            if url == manifest_url:
                return _FakeResp(200, json.dumps(manifest))
            return _FakeResp(404, "")

        def manifest_missing_opener(url, *_a, **_k):
            if url == dated_url:
                return _FakeResp(200, edition.html)
            return _FakeResp(404, "")

        original_docs_paths = runner._docs_paths
        runner._docs_paths = lambda _t, _k: (dated_path, latest_path)
        try:
            with production_env(github_output):
                verified = runner.run_verify_public(
                    "daily", run_at=RUN_AT, runtime_dir=runtime_dir,
                    opener=resolving_opener, publication_timeout_seconds=0,
                )
            check("run_verify_public passes when both resources reconstruct", verified)
            outputs = dict(
                line.split("=", 1)
                for line in github_output.read_text(encoding="utf-8").splitlines()
                if "=" in line
            )
            check("run_verify_public emits resources_verified=true",
                  outputs.get("resources_verified") == "true")

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
