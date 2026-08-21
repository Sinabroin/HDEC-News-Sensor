#!/usr/bin/env python3
"""R4-OPS-10D auth-session cache and browser-state acceptance.

All route mutations are intercepted by in-memory fakes.  The only optional
external request is a read-only production article-analysis probe; network
unavailability is reported separately from deterministic acceptance failures.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from html import unescape
from pathlib import Path
from urllib import error as urlerror
from urllib import request as urlrequest

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "scripts"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

os.environ.setdefault("APP_MODE", "mock")
os.environ.setdefault("NEWS_MODE", "mock")

from app import (  # noqa: E402
    config,
    editorial_article_import,
    editorial_contributor_auth,
    editorial_operator_review,
    editorial_team_intake,
    operator_auth,
    operator_gateway,
)
import build_editorial_review_console as console_builder  # noqa: E402

ALLOWED_ORIGIN = "https://guides.playground-aidesignlab.co.kr"
FORBIDDEN_ORIGIN = "https://attacker.example"
LOGIN = "ceoYS"
SNAPSHOT_ID = "review-2026-08-21-0123456789abcdef"
EDITION_ID = "daily-2026-08-21-0123456789abcdef"
DAUM_URL = "https://v.daum.net/v/20260820180747166"
PRODUCTION_IMPORT_URL = (
    "https://hdec-news-sensor-operator.vercel.app/api/editorial/import-article"
)


class Verify:
    def __init__(self) -> None:
        self.checks = 0
        self.failures = 0
        self.flags: dict[str, str] = {}

    def check(self, label: str, condition: bool, detail: str = "") -> bool:
        self.checks += 1
        if condition:
            print(f"PASS: {label}")
        else:
            self.failures += 1
            print(f"FAIL: {label}" + (f" — {detail}" if detail else ""))
        return condition

    def flag(self, name: str, value: object) -> None:
        if isinstance(value, bool):
            rendered = "PASS" if value else "FAIL"
        else:
            rendered = str(value)
        self.flags[name] = rendered


def _cache_contract(response) -> bool:
    cache_control = response.headers.get("cache-control", "").lower()
    return (
        "private" in cache_control
        and "no-store" in cache_control
        and bool(re.search(r"(?:^|,)\s*max-age\s*=\s*0(?:\s*,|$)", cache_control))
        and response.headers.get("pragma", "").lower() == "no-cache"
        and response.headers.get("expires", "") == "0"
    )


def verify_fastapi_and_privileges(v: Verify) -> None:
    print("\n== FastAPI auth cache + privilege matrix ==")
    try:
        from fastapi.testclient import TestClient
        from app import operator_api
    except Exception as exc:
        v.check("FastAPI TestClient is available", False, repr(exc))
        v.flag("FASTAPI_AUTH_SESSION_CACHE_TESTS", False)
        v.flag("ANONYMOUS_ARTICLE_ANALYSIS", False)
        v.flag("PRIVILEGE_MATRIX", False)
        return

    config_names = (
        "GH_OPERATOR_TOKEN",
        "OPERATOR_REPO",
        "OPERATOR_ACCESS_MODE",
        "OPERATOR_ALLOWED_ORIGINS",
        "OPERATOR_RATE_LIMIT_PER_MIN",
        "OPERATOR_LOCAL_DEV",
        "OPERATOR_DRY_RUN",
        "OPERATOR_SESSION_SECRET",
        "OPERATOR_ALLOWED_GITHUB_LOGINS",
        "OPERATOR_SESSION_MAX_AGE_SECONDS",
        "EDITORIAL_CONTRIBUTOR_CODE_SHA256",
        "EDITORIAL_CONTRIBUTOR_SESSION_MAX_AGE_SECONDS",
    )
    saved_config = {name: getattr(config, name) for name in config_names}
    originals = {
        "import_article": editorial_article_import.import_article,
        "submit_for_review": editorial_team_intake.submit_for_review,
        "load_pending_submissions": editorial_team_intake.load_pending_submissions,
        "save_draft": editorial_operator_review.save_draft,
        "publish_daily": editorial_operator_review.publish_daily,
    }
    calls = {name: 0 for name in originals}

    def fake_import(url):
        calls["import_article"] += 1
        return {
            "ok": True,
            "article": {
                "input_url": str(url),
                "title": "오프라인 AI 인프라 기사",
                "source": "검증경제",
                "summary": "익명 기사 분석의 실제 FastAPI 경로를 검증합니다.",
                "category": "기술정보",
                "portal_source": "",
                "portal_copy": False,
                "publisher_domain_authoritative": True,
                "publisher_url": str(url),
            },
        }

    def fake_submit(payload):
        calls["submit_for_review"] += 1
        return {
            "ok": True,
            "status": "pending",
            "submission_id": "submission-" + "a" * 64,
            "input": dict(payload),
        }

    def fake_pending(edition_key, snapshot_id):
        calls["load_pending_submissions"] += 1
        return {
            "ok": True,
            "submissions": [],
            "edition_key": edition_key,
            "review_snapshot_id": snapshot_id,
        }

    def fake_save(payload, *, operator_login):
        calls["save_draft"] += 1
        return {
            "ok": True,
            "revision": "b" * 64,
            "selected_count": len(payload.get("selected_items") or []),
            "operator_login": operator_login,
        }

    def fake_publish(payload, *, operator_login, dispatcher):
        del dispatcher
        calls["publish_daily"] += 1
        return {
            "ok": True,
            "edition_id": EDITION_ID,
            "operator_login": operator_login,
            "selected_count": len(payload.get("selected_items") or []),
        }

    try:
        config.GH_OPERATOR_TOKEN = "offline-route-token"
        config.OPERATOR_REPO = "Sinabroin/HDEC-News-Sensor"
        config.OPERATOR_ACCESS_MODE = "origin"
        config.OPERATOR_ALLOWED_ORIGINS = [ALLOWED_ORIGIN]
        config.OPERATOR_RATE_LIMIT_PER_MIN = 10_000
        config.OPERATOR_LOCAL_DEV = False
        config.OPERATOR_DRY_RUN = True
        config.OPERATOR_SESSION_SECRET = "offline-auth-state-session-secret"
        config.OPERATOR_ALLOWED_GITHUB_LOGINS = [LOGIN.lower()]
        config.OPERATOR_SESSION_MAX_AGE_SECONDS = 8 * 60 * 60
        config.EDITORIAL_CONTRIBUTOR_CODE_SHA256 = hashlib.sha256(
            b"offline-contributor-code"
        ).hexdigest()
        config.EDITORIAL_CONTRIBUTOR_SESSION_MAX_AGE_SECONDS = 8 * 60 * 60
        operator_gateway._recent_triggers.clear()  # noqa: SLF001

        editorial_article_import.import_article = fake_import
        editorial_team_intake.submit_for_review = fake_submit
        editorial_team_intake.load_pending_submissions = fake_pending
        editorial_operator_review.save_draft = fake_save
        editorial_operator_review.publish_daily = fake_publish

        operator_token = operator_auth.create_session_token(LOGIN)
        contributor_token = editorial_contributor_auth.create_session_token()
        operator_cookie = f"{config.OPERATOR_SESSION_COOKIE}={operator_token}"
        contributor_cookie = (
            f"{config.EDITORIAL_CONTRIBUTOR_SESSION_COOKIE}={contributor_token}"
        )
        allowed = {"origin": ALLOWED_ORIGIN}
        operator_headers = {**allowed, "cookie": operator_cookie}
        contributor_headers = {**allowed, "cookie": contributor_cookie}

        with TestClient(operator_api.app) as client:
            auth_responses = (
                ("operator unauthenticated", client.get("/api/auth/session"), False),
                (
                    "operator authenticated",
                    client.get(
                        "/api/auth/session", headers={"cookie": operator_cookie}
                    ),
                    True,
                ),
                (
                    "contributor unauthenticated",
                    client.get(
                        "/api/editorial/contributor/session", headers=allowed
                    ),
                    False,
                ),
                (
                    "contributor authenticated",
                    client.get(
                        "/api/editorial/contributor/session",
                        headers=contributor_headers,
                    ),
                    True,
                ),
            )
            cache_ok = True
            for label, response, authenticated in auth_responses:
                payload = response.json()
                ok = (
                    response.status_code == 200
                    and payload.get("authenticated") is authenticated
                    and _cache_contract(response)
                )
                cache_ok = v.check(
                    f"{label} session is current and non-cacheable",
                    ok,
                    f"status={response.status_code} body={payload!r} headers={dict(response.headers)!r}",
                ) and cache_ok
            v.flag("FASTAPI_AUTH_SESSION_CACHE_TESTS", cache_ok)

            article_body = {"url": "https://publisher.example/article/ai"}
            review_body = {
                "product": "daily",
                "edition_key": "2026-08-21",
                "review_snapshot_id": SNAPSHOT_ID,
                "selected_items": [],
            }
            pending_path = (
                "/api/editorial/pending-submissions"
                f"?edition_key=2026-08-21&review_snapshot_id={SNAPSHOT_ID}"
            )

            anonymous_import = client.post(
                "/api/editorial/import-article", json=article_body, headers=allowed
            )
            anonymous_denied = (
                client.post(
                    "/api/editorial/save-draft", json=review_body, headers=allowed
                ).status_code
                == 401
                and client.post(
                    "/api/editorial/publish-daily", json=review_body, headers=allowed
                ).status_code
                == 401
                and client.get(pending_path, headers=allowed).status_code == 401
            )
            v.check(
                "anonymous allowed Origin retains login-free article analysis",
                anonymous_import.status_code == 200
                and anonymous_import.json().get("ok") is True,
                anonymous_import.text[:300],
            )
            v.check(
                "anonymous cannot save, publish, or load pending submissions",
                anonymous_denied,
            )

            contributor_import = client.post(
                "/api/editorial/import-article",
                json=article_body,
                headers=contributor_headers,
            )
            contributor_submit = client.post(
                "/api/editorial/submit-for-review",
                json={
                    "product": "daily",
                    "edition_key": "2026-08-21",
                    "review_snapshot_id": SNAPSHOT_ID,
                    "url": article_body["url"],
                },
                headers=contributor_headers,
            )
            contributor_denied = (
                client.post(
                    "/api/editorial/save-draft",
                    json=review_body,
                    headers=contributor_headers,
                ).status_code
                == 401
                and client.post(
                    "/api/editorial/publish-daily",
                    json=review_body,
                    headers=contributor_headers,
                ).status_code
                == 401
                and client.get(pending_path, headers=contributor_headers).status_code
                == 401
            )
            v.check(
                "contributor may analyze and submit through actual routes",
                contributor_import.status_code == 200
                and contributor_submit.status_code == 200
                and contributor_submit.json().get("status") == "pending",
            )
            v.check(
                "contributor token cannot satisfy operator routes",
                contributor_denied,
            )

            operator_save = client.post(
                "/api/editorial/save-draft",
                json=review_body,
                headers=operator_headers,
            )
            operator_publish = client.post(
                "/api/editorial/publish-daily",
                json=review_body,
                headers=operator_headers,
            )
            operator_pending = client.get(pending_path, headers=operator_headers)
            operator_ok = (
                operator_save.status_code == 200
                and operator_publish.status_code == 200
                and operator_pending.status_code == 200
                and operator_save.json().get("operator_login") == LOGIN
                and operator_publish.json().get("operator_login") == LOGIN
                and calls["save_draft"] == 1
                and calls["publish_daily"] == 1
                and calls["load_pending_submissions"] == 1
            )
            v.check(
                "operator session reaches existing privileged route contracts",
                operator_ok,
                f"save={operator_save.text[:160]} publish={operator_publish.text[:160]} pending={operator_pending.text[:160]}",
            )

            forbidden_statuses = (
                client.post(
                    "/api/editorial/import-article",
                    json=article_body,
                    headers={"origin": FORBIDDEN_ORIGIN},
                ).status_code,
                client.get(
                    "/api/editorial/contributor/session",
                    headers={"origin": FORBIDDEN_ORIGIN},
                ).status_code,
                client.post(
                    "/api/editorial/submit-for-review",
                    json={"url": article_body["url"]},
                    headers={
                        "origin": FORBIDDEN_ORIGIN,
                        "cookie": contributor_cookie,
                    },
                ).status_code,
                client.post(
                    "/api/editorial/save-draft",
                    json=review_body,
                    headers={"origin": FORBIDDEN_ORIGIN, "cookie": operator_cookie},
                ).status_code,
                client.post(
                    "/api/editorial/publish-daily",
                    json=review_body,
                    headers={"origin": FORBIDDEN_ORIGIN, "cookie": operator_cookie},
                ).status_code,
                client.get(
                    pending_path,
                    headers={"origin": FORBIDDEN_ORIGIN, "cookie": operator_cookie},
                ).status_code,
            )
            forbidden_ok = forbidden_statuses == (403, 403, 403, 403, 403, 403)
            v.check(
                "forbidden Origin is denied across the privilege matrix",
                forbidden_ok,
                repr(forbidden_statuses),
            )

            anonymous_ok = (
                anonymous_import.status_code == 200
                and calls["import_article"] == 2
            )
            privilege_ok = (
                anonymous_denied
                and contributor_import.status_code == 200
                and contributor_submit.status_code == 200
                and contributor_denied
                and operator_ok
                and forbidden_ok
            )
            v.flag("ANONYMOUS_ARTICLE_ANALYSIS", anonymous_ok)
            v.flag("PRIVILEGE_MATRIX", privilege_ok)
    finally:
        editorial_article_import.import_article = originals["import_article"]
        editorial_team_intake.submit_for_review = originals["submit_for_review"]
        editorial_team_intake.load_pending_submissions = originals[
            "load_pending_submissions"
        ]
        editorial_operator_review.save_draft = originals["save_draft"]
        editorial_operator_review.publish_daily = originals["publish_daily"]
        operator_gateway._recent_triggers.clear()  # noqa: SLF001
        for name, value in saved_config.items():
            setattr(config, name, value)


def _browser_executable() -> Path | None:
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


def _browser_uri(path: Path, browser: Path) -> str:
    if browser.suffix.casefold() != ".exe":
        return path.resolve().as_uri()
    translated = subprocess.run(
        ["wslpath", "-w", str(path.resolve())],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return "file:///" + translated.replace("\\", "/")


def _browser_path(path: Path, browser: Path) -> str:
    if browser.suffix.casefold() != ".exe":
        return str(path.resolve())
    return subprocess.run(
        ["wslpath", "-w", str(path.resolve())],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _browser_tempdir(browser: Path):
    if browser.suffix.casefold() != ".exe":
        return tempfile.TemporaryDirectory(prefix="r4-ops10d-browser-")
    output = subprocess.run(
        ["cmd.exe", "/d", "/c", "echo", "%TEMP%"],
        cwd="/mnt/c",
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    windows_temp = next(
        line.strip()
        for line in reversed(output.splitlines())
        if re.match(r"^[A-Za-z]:\\", line.strip())
    )
    wsl_temp = subprocess.run(
        ["wslpath", "-u", windows_temp],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return tempfile.TemporaryDirectory(
        prefix="r4-ops10d-browser-", dir=wsl_temp, ignore_cleanup_errors=True
    )


def _run_browser_once(browser: Path) -> dict[str, object]:
    bundle = {
        "version": 3,
        "edition_type": "daily",
        "edition_key": "2026-08-21",
        "coverage_start": "2026-08-20T07:00:00+09:00",
        "coverage_end": "2026-08-21T06:40:00+09:00",
        "generated_at": "2026-08-21T07:20:00+09:00",
        "candidates": [],
        "article_import_api_url": (
            "https://operator.example.test/api/editorial/import-article"
        ),
        "article_import_enabled": True,
    }
    prelude = f"""<script>
window.__ops10d={{operator:"false",contributor:"false",calls:[]}};
window.fetch=async function(url,options={{}}){{
  const value=String(url||"");
  window.__ops10d.calls.push({{url:value,method:options.method||"GET",credentials:options.credentials||"",cache:options.cache||""}});
  if(value.endsWith("manifest.json"))return {{ok:true,status:200,json:async()=>({{review_snapshot_id:"{SNAPSHOT_ID}"}})}};
  if(value.endsWith("/api/auth/session")){{
    if(window.__ops10d.operator==="failure")throw new TypeError("operator network failure");
    const authenticated=window.__ops10d.operator==="true";
    return {{ok:true,status:200,json:async()=>({{authenticated,login:authenticated?"{LOGIN}":""}})}};
  }}
  if(value.endsWith("/api/editorial/contributor/session")){{
    if(window.__ops10d.contributor==="failure")throw new TypeError("contributor network failure");
    const authenticated=window.__ops10d.contributor==="true";
    return {{ok:true,status:200,json:async()=>({{authenticated,role:authenticated?"editorial_contributor":""}})}};
  }}
  throw new Error("unexpected fixture fetch: "+value);
}};
</script>"""
    harness = f"""<script>
(async()=>{{
  const result={{browser_available:true}};
  const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
  const prepareOperator=(authenticated,login)=>{{
    serverContext.origin=operatorApiOrigin();
    serverContext.snapshotId="{SNAPSHOT_ID}";
    serverContext.editionKey=bundle.edition_key;
    serverContext.editionId="{EDITION_ID}";
    serverContext.authenticated=authenticated;
    serverContext.login=login;
    renderOperatorAuthCta();
    refreshServerButtons();
  }};
  const operatorState=()=>({{
    authenticated:serverContext.authenticated,
    login:serverContext.login,
    text:document.getElementById("operatorAuthCta").textContent,
    saveDisabled:document.getElementById("saveDraftBtn").disabled,
    publishDisabled:document.getElementById("publishBtn").disabled,
    pendingDisabled:document.getElementById("loadTeamBtn").disabled
  }});
  await pause(150);
  window.__ops10d.calls=[];

  prepareOperator(true,"{LOGIN}");
  window.__ops10d.operator="false";
  await probeImportAuth();
  const opFalse=operatorState();
  result.operator_true_to_false=!opFalse.authenticated&&opFalse.login===""&&!opFalse.text.includes("운영자 인증됨")&&opFalse.text.includes("운영자 로그인")&&!opFalse.text.includes("GitHub")&&opFalse.saveDisabled&&opFalse.publishDisabled&&opFalse.pendingDisabled;

  prepareOperator(true,"{LOGIN}");
  window.__ops10d.operator="failure";
  await probeImportAuth();
  const opFailure=operatorState();
  result.operator_failure_clears=!opFailure.authenticated&&opFailure.login===""&&!opFailure.text.includes("운영자 인증됨")&&opFailure.text.includes("운영자 로그인")&&!opFailure.text.includes("GitHub")&&opFailure.saveDisabled&&opFailure.publishDisabled&&opFailure.pendingDisabled;

  prepareOperator(false,"");
  window.__ops10d.operator="false";
  await probeImportAuth();
  const opStillFalse=operatorState();
  result.operator_false_stays_false=!opStillFalse.authenticated&&opStillFalse.login===""&&!opStillFalse.text.includes("운영자 인증됨");

  prepareOperator(false,"");
  window.__ops10d.operator="true";
  await probeImportAuth();
  const opTrue=operatorState();
  result.operator_true_works=opTrue.authenticated&&opTrue.login==="{LOGIN}"&&opTrue.text.includes("운영자 인증됨: {LOGIN}")&&!opTrue.text.includes("GitHub");

  contributorContext.authenticated=true;
  refreshTeamUi();
  window.__ops10d.contributor="false";
  await probeContributorSession();
  result.contributor_true_to_false=!contributorContext.authenticated&&!document.getElementById("teamCodeControls").hidden;

  contributorContext.authenticated=true;
  refreshTeamUi();
  window.__ops10d.contributor="failure";
  await probeContributorSession();
  result.contributor_failure_clears=!contributorContext.authenticated&&!document.getElementById("teamCodeControls").hidden;

  const operatorCalls=window.__ops10d.calls.filter(call=>call.url.endsWith("/api/auth/session"));
  const contributorCalls=window.__ops10d.calls.filter(call=>call.url.endsWith("/api/editorial/contributor/session"));
  result.operator_fetch_no_store=operatorCalls.length===4&&operatorCalls.every(call=>call.method==="GET"&&call.credentials==="include"&&call.cache==="no-store");
  result.contributor_fetch_no_store=contributorCalls.length===2&&contributorCalls.every(call=>call.method==="GET"&&call.credentials==="include"&&call.cache==="no-store");

  const marker=document.createElement("pre");
  marker.id="ops10d-browser-result";
  marker.textContent=JSON.stringify(result);
  document.body.appendChild(marker);
}})().catch(error=>{{
  const marker=document.createElement("pre");
  marker.id="ops10d-browser-result";
  marker.textContent=JSON.stringify({{browser_available:true,error:String(error),stack:error&&error.stack||""}});
  document.body.appendChild(marker);
}});
</script>"""

    handle = _browser_tempdir(browser)
    try:
        base = Path(handle.name)
        html = console_builder.render_console(
            (ROOT / "templates" / "editorial_review_console.html").read_text(
                encoding="utf-8"
            ),
            bundle,
        )
        html = html.replace(
            '<script id="candidate-data"',
            prelude + '<script id="candidate-data"',
            1,
        )
        html = html.rsplit("</body>", 1)[0] + harness + "</body></html>"
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
            f"--user-data-dir={_browser_path(profile, browser)}",
            "--dump-dom",
            _browser_uri(fixture, browser),
        ]
        if browser.suffix.casefold() != ".exe":
            command[2:2] = ["--no-sandbox", "--disable-dev-shm-usage"]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=45,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return {
                "browser_available": True,
                "error": f"headless browser timeout after {exc.timeout}s",
            }
    finally:
        handle.cleanup()

    match = re.search(
        r'<pre id="ops10d-browser-result">([^<]+)</pre>', completed.stdout
    )
    if completed.returncode != 0 or not match:
        return {
            "browser_available": True,
            "error": (
                f"returncode={completed.returncode} "
                f"stderr={completed.stderr[-700:]!r} stdout={completed.stdout[-1200:]!r}"
            ),
        }
    return json.loads(unescape(match.group(1)))


def verify_browser(v: Verify) -> None:
    print("\n== Real browser auth-state transitions ==")
    browser = _browser_executable()
    if browser is None:
        result = {"browser_available": False, "error": "Chrome/Edge unavailable"}
    else:
        result = _run_browser_once(browser)
        if "timeout" in str(result.get("error", "")).lower():
            print("RETRY: one allowed cold-browser timeout retry")
            result = _run_browser_once(browser)

    checks = (
        ("operator_true_to_false", "operator true -> false clears stale UI"),
        ("operator_failure_clears", "operator probe failure clears stale UI"),
        ("operator_false_stays_false", "operator false -> false remains false"),
        ("operator_true_works", "operator authenticated response still renders"),
        ("contributor_true_to_false", "contributor true -> false clears state"),
        ("contributor_failure_clears", "contributor failure clears state"),
        ("operator_fetch_no_store", "operator browser probe sends cache:no-store"),
        (
            "contributor_fetch_no_store",
            "contributor browser probe sends cache:no-store",
        ),
    )
    browser_ok = v.check(
        "real headless browser executed the rendered template",
        result.get("browser_available") is True and not result.get("error"),
        repr(result),
    )
    for key, label in checks:
        browser_ok = v.check(label, result.get(key) is True, repr(result)) and browser_ok

    v.flag("REAL_BROWSER_AUTH_TRANSITIONS", browser_ok)
    v.flag("OPERATOR_TRUE_TO_FALSE", result.get("operator_true_to_false") is True)
    v.flag(
        "OPERATOR_FAILURE_CLEARS_STATE",
        result.get("operator_failure_clears") is True,
    )
    v.flag(
        "CONTRIBUTOR_TRUE_TO_FALSE",
        result.get("contributor_true_to_false") is True,
    )
    v.flag(
        "CONTRIBUTOR_FAILURE_CLEARS_STATE",
        result.get("contributor_failure_clears") is True,
    )
    v.flag(
        "FETCH_CACHE_NO_STORE",
        result.get("operator_fetch_no_store") is True
        and result.get("contributor_fetch_no_store") is True,
    )


def verify_existing_ops10c(v: Verify) -> None:
    print("\n== Existing R4-OPS-10C verifier ==")
    command = [sys.executable, str(ROOT / "scripts/verify_r4_ops10c_team_portal_intake.py")]

    def run_once():
        try:
            return subprocess.run(
                command,
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=240,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return exc

    completed = run_once()
    output = (
        (completed.stdout or "") + (completed.stderr or "")
        if isinstance(completed, subprocess.CompletedProcess)
        else f"subprocess timeout after {completed.timeout}s"
    )
    timeout_failure = "timeout" in output.lower() or "timed out" in output.lower()
    if timeout_failure:
        print("RETRY: one allowed cold-browser timeout retry for R4-OPS-10C")
        completed = run_once()
        output = (
            (completed.stdout or "") + (completed.stderr or "")
            if isinstance(completed, subprocess.CompletedProcess)
            else f"subprocess timeout after {completed.timeout}s"
        )
    ok = (
        isinstance(completed, subprocess.CompletedProcess)
        and completed.returncode == 0
        and "RESULT=R4_OPS10C_TEAM_PORTAL_INTAKE_PASS" in output
        and "REAL_BROWSER_USED=true" in output
    )
    v.check(
        "existing 10C verifier passes with a real browser",
        ok,
        output[-3000:],
    )
    v.flag("OPS10C_REAL_BROWSER_REGRESSION", ok)


def verify_production_daum(v: Verify) -> None:
    print("\n== Read-only production Daum analysis ==")
    body = json.dumps({"url": DAUM_URL}).encode("utf-8")
    request = urlrequest.Request(PRODUCTION_IMPORT_URL, data=body, method="POST")
    request.add_header("Origin", ALLOWED_ORIGIN)
    request.add_header("Content-Type", "application/json")
    request.add_header("User-Agent", "HDEC-R4-OPS-10D-read-only-verifier/1.0")
    try:
        with urlrequest.urlopen(request, timeout=35) as response:
            status = response.status
            payload = json.loads(response.read().decode("utf-8"))
    except urlerror.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        if exc.code == 429 or exc.code >= 500:
            print(
                f"NETWORK_UNAVAILABLE: Daum production probe HTTP {exc.code} — {detail}"
            )
            v.flag("DAUM_PORTAL_REGRESSION", "NETWORK_UNAVAILABLE")
            return
        v.check(
            "production Daum analysis is anonymously accepted",
            False,
            f"HTTP {exc.code}: {detail}",
        )
        v.flag("DAUM_PORTAL_REGRESSION", False)
        return
    except (urlerror.URLError, TimeoutError, OSError) as exc:
        print(f"NETWORK_UNAVAILABLE: Daum production probe — {exc!r}")
        v.flag("DAUM_PORTAL_REGRESSION", "NETWORK_UNAVAILABLE")
        return
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        v.check("production Daum response is valid JSON", False, repr(exc))
        v.flag("DAUM_PORTAL_REGRESSION", False)
        return

    article = payload.get("article") if isinstance(payload, dict) else None
    article = article if isinstance(article, dict) else {}
    publisher_direct = (
        article.get("publisher_domain_authoritative") is True
        and str(article.get("publisher_url") or "").startswith("https://")
        and "daum.net" not in str(article.get("publisher_url") or "").lower()
    )
    portal_copy = (
        article.get("portal_copy") is True
        and article.get("portal_source") == "daum"
    )
    ok = (
        status == 200
        and payload.get("ok") is True
        and article.get("source") == "한국경제"
        and all(article.get(key) for key in ("title", "summary", "category"))
        and (portal_copy or publisher_direct)
    )
    v.check(
        "production Daum URL remains anonymously analyzable",
        ok,
        repr(
            {
                "status": status,
                "ok": payload.get("ok"),
                "source": article.get("source"),
                "title": article.get("title"),
                "category": article.get("category"),
                "portal_source": article.get("portal_source"),
                "portal_copy": article.get("portal_copy"),
                "publisher_url": article.get("publisher_url"),
                "publisher_domain_authoritative": article.get(
                    "publisher_domain_authoritative"
                ),
            }
        ),
    )
    v.flag("DAUM_PORTAL_REGRESSION", ok)


def main() -> int:
    v = Verify()
    verify_fastapi_and_privileges(v)
    verify_browser(v)
    verify_existing_ops10c(v)
    verify_production_daum(v)

    print(f"\nchecks={v.checks} failures={v.failures}")
    for name in (
        "FASTAPI_AUTH_SESSION_CACHE_TESTS",
        "REAL_BROWSER_AUTH_TRANSITIONS",
        "OPERATOR_TRUE_TO_FALSE",
        "OPERATOR_FAILURE_CLEARS_STATE",
        "CONTRIBUTOR_TRUE_TO_FALSE",
        "CONTRIBUTOR_FAILURE_CLEARS_STATE",
        "FETCH_CACHE_NO_STORE",
        "ANONYMOUS_ARTICLE_ANALYSIS",
        "DAUM_PORTAL_REGRESSION",
        "PRIVILEGE_MATRIX",
        "OPS10C_REAL_BROWSER_REGRESSION",
    ):
        print(f"{name}={v.flags.get(name, 'FAIL')}")
    print("PRODUCTION_WRITES=0")
    print("WORKFLOW_DISPATCHES=0")
    print("PRODUCTION_SENDS=0")
    if v.failures:
        print("RESULT=R4_OPS10D_AUTH_STATE_CONSISTENCY_FAIL")
        return 1
    print("RESULT=R4_OPS10D_AUTH_STATE_CONSISTENCY_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
