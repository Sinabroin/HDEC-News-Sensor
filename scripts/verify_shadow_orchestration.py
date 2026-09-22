#!/usr/bin/env python3
"""Offline verifier for D7-AK-6F-C2 shadow orchestration and PR CI."""

from __future__ import annotations

import hashlib
import json
import os
import py_compile
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class Verifier:
    def __init__(self) -> None:
        self.checks = 0
        self.failures = 0

    def check(self, name: str, condition: bool) -> None:
        self.checks += 1
        if condition:
            print(f"PASS: {name}")
        else:
            self.failures += 1
            print(f"FAIL: {name}")

    def equal(self, name: str, actual: object, expected: object) -> None:
        self.check(name, actual == expected)
        if actual != expected:
            print(f"  expected={expected!r}")
            print(f"  actual={actual!r}")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_cli(
    *,
    temp: Path,
    shadow_db: Path,
    run_id: str,
    json_name: str,
) -> tuple[subprocess.CompletedProcess[str], dict]:
    guard = temp / "network_guard"
    guard.mkdir(exist_ok=True)
    sentinel = temp / "socket-connect-attempted.txt"
    (guard / "sitecustomize.py").write_text(
        """
import os
import socket

_SENTINEL = os.environ.get("D7AK6F_SOCKET_SENTINEL", "")
_OriginalSocket = socket.socket

def _blocked(*args, **kwargs):
    if _SENTINEL:
        with open(_SENTINEL, "a", encoding="utf-8") as handle:
            handle.write("socket connect attempted\\n")
    raise RuntimeError("outbound socket blocked by C2 verifier")

class _GuardedSocket(_OriginalSocket):
    def connect(self, *args, **kwargs):
        return _blocked(*args, **kwargs)

    def connect_ex(self, *args, **kwargs):
        return _blocked(*args, **kwargs)

socket.socket = _GuardedSocket
socket.create_connection = _blocked
""".lstrip(),
        encoding="utf-8",
    )

    output = temp / json_name
    env = dict(os.environ)
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(guard), str(ROOT), existing_pythonpath) if part
    )
    env["D7AK6F_SOCKET_SENTINEL"] = str(sentinel)
    env["APP_MODE"] = "mock"
    env["NEWS_MODE"] = "mock"

    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/run_shadow_runtime.py"),
            "--collector-mode",
            "mock",
            "--shadow-db",
            str(shadow_db),
            "--run-id",
            run_id,
            "--json-output",
            str(output),
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    payload = (
        json.loads(output.read_text(encoding="utf-8"))
        if output.exists()
        else {}
    )
    return completed, payload


def verify_static(v: Verifier) -> None:
    paths = (
        ROOT / "app/runtime_orchestrator.py",
        ROOT / "scripts/run_shadow_runtime.py",
        ROOT / "scripts/verify_shadow_orchestration.py",
    )
    for path in paths:
        py_compile.compile(str(path), doraise=True)
    v.check("C2 Python files compile", True)

    orchestrator = paths[0].read_text(encoding="utf-8")
    runner = paths[1].read_text(encoding="utf-8")
    combined = orchestrator + "\n" + runner

    v.check("orchestrator calls actual collector entrypoint", "collector.run(" in orchestrator)
    v.check("orchestrator calls existing scoring pipeline", "scoring.score_all()" in orchestrator)
    v.check("orchestrator calls existing insight pipeline", "insight.generate_all()" in orchestrator)
    v.check("orchestrator reads collected DB rows", "db.fetch_articles_with_scores()" in orchestrator)
    v.check("shadow channel is explicit", 'SHADOW_CHANNEL = "shadow_teams_email"' in orchestrator)
    v.check("orchestrator never claims outbox", ".claim_outbox(" not in orchestrator)
    v.check("orchestrator never completes delivery", "mark_delivery_" not in orchestrator)
    v.check(
        "orchestrator imports no delivery or HTTP client",
        not any(
            token in combined
            for token in (
                "import smtplib",
                "import requests",
                "import httpx",
                "urllib.request",
                "send_teams",
                "send_email_alert",
                "send_telegram",
            )
        ),
    )
    v.check("orchestrator contains no git push", "git push" not in combined)
    v.check("live collector requires explicit approval", "allow_live_collector=True" in orchestrator)

    workflow = ROOT / ".github/workflows/runtime-shadow-python-ci.yml"
    v.check("C2 PR CI workflow exists", workflow.exists())
    if workflow.exists():
        text = workflow.read_text(encoding="utf-8")
        v.check("CI triggers on pull request", "pull_request:" in text)
        v.check("CI triggers on main push", "push:" in text and "branches: [main]" in text)
        v.check("CI has no schedule", "schedule:" not in text)
        v.check("CI has no manual dispatch", "workflow_dispatch:" not in text)
        v.check("CI permissions are read only", "contents: read" in text and "contents: write" not in text)
        v.check("CI uses mock collector mode", 'NEWS_MODE: "mock"' in text)
        v.check("CI runs C1 core verifier", "python3 scripts/verify_runtime_core.py" in text)
        v.check("CI runs C2 orchestration verifier", "python3 scripts/verify_shadow_orchestration.py" in text)
        v.check("CI references no secrets", "secrets." not in text)
        v.check("CI invokes no sender", not any(
            token in text
            for token in (
                "send_teams",
                "send_email",
                "send_telegram",
                "TEAMS_CHANNEL_EMAIL",
                "GMAIL_SMTP",
            )
        ))

    architecture = ROOT / "docs/architecture/d7-ak-6f-runtime-contract.md"
    text = architecture.read_text(encoding="utf-8")
    for token in (
        "D7-AK-6F-C2",
        "collector shadow orchestration",
        "app.collector.run",
        "shadow_teams_email",
        "PR-only Python CI",
        "contents: read",
        "production state remains read-only",
    ):
        v.check(f"architecture contains {token}", token in text)


def verify_runtime(v: Verifier, temp: Path) -> None:
    state_path = ROOT / "data/teams_push_state.json"
    state_before = sha256(state_path)
    shadow_db = temp / "shadow-runtime.sqlite"

    first, first_payload = run_cli(
        temp=temp,
        shadow_db=shadow_db,
        run_id="c2-verifier-fixed-run",
        json_name="first.json",
    )
    v.equal("first shadow CLI exits zero", first.returncode, 0)
    v.check(
        "first shadow CLI reports PASS",
        "RESULT=D7-AK-6F-C2_SHADOW_ORCHESTRATION_PASS" in first.stdout,
    )
    if first.returncode != 0:
        print(first.stdout)
        print(first.stderr)

    v.equal(
        "actual collector entrypoint recorded",
        first_payload.get("collector_entrypoint"),
        "app.collector.run",
    )
    v.equal("collector requested mode is mock", first_payload.get("collector_mode_requested"), "mock")
    v.equal("collector effective mode is mock", first_payload.get("collector_mode_effective"), "mock")
    v.equal("collector network mode is offline", first_payload.get("collector_network_mode"), "offline_mock")
    v.check("actual collector produced observations", int(first_payload.get("observations") or 0) > 0)
    v.equal("all valid observations processed", first_payload.get("processed"), first_payload.get("observations"))
    v.equal("mock fixtures contain no invalid observation", first_payload.get("skipped_invalid"), 0)
    v.check("collector reports collected rows", int((first_payload.get("collector_stats") or {}).get("collected") or 0) > 0)
    v.check("existing scoring pipeline ran", int((first_payload.get("score_stats") or {}).get("scored") or 0) > 0)

    stats = first_payload.get("store_stats") or {}
    observations = int(first_payload.get("observations") or 0)
    v.equal("canonical articles equal observations", stats.get("canonical_articles"), observations)
    v.equal("event clusters equal canonical articles", stats.get("news_events"), observations)
    v.equal("authoritative decisions equal events", stats.get("policy_decisions"), observations)
    v.check("shadow outbox contains eligible rows", 0 < int(stats.get("delivery_outbox") or 0) <= observations)
    v.equal("one heartbeat written", stats.get("runtime_heartbeats"), 1)
    v.equal("shadow channel exact", first_payload.get("shadow_channel"), "shadow_teams_email")
    v.equal("channel sends remain zero", first_payload.get("channel_sends"), 0)
    v.equal("SMTP connections remain zero", first_payload.get("smtp_connections"), 0)
    v.equal("Teams sends remain zero", first_payload.get("teams_sends"), 0)
    v.equal("Telegram sends remain zero", first_payload.get("telegram_sends"), 0)
    v.equal("production state writes remain zero", first_payload.get("production_state_writes"), 0)

    first_counts = {
        key: stats.get(key)
        for key in (
            "canonical_articles",
            "news_events",
            "policy_decisions",
            "delivery_outbox",
            "runtime_heartbeats",
        )
    }

    second, second_payload = run_cli(
        temp=temp,
        shadow_db=shadow_db,
        run_id="c2-verifier-fixed-run",
        json_name="second.json",
    )
    v.equal("second shadow CLI exits zero", second.returncode, 0)
    v.check(
        "second shadow CLI reports PASS",
        "RESULT=D7-AK-6F-C2_SHADOW_ORCHESTRATION_PASS" in second.stdout,
    )
    second_stats = second_payload.get("store_stats") or {}
    second_counts = {
        key: second_stats.get(key)
        for key in first_counts
    }
    v.equal("second run is fully idempotent", second_counts, first_counts)
    v.equal("second run creates no duplicate outbox", second_payload.get("outbox_created"), 0)
    v.equal("same run heartbeat remains one", second_stats.get("runtime_heartbeats"), 1)

    state_after = sha256(state_path)
    v.equal("production Teams state remains byte-identical", state_after, state_before)
    v.check("network guard observed no socket attempt", not (temp / "socket-connect-attempted.txt").exists())


def main() -> int:
    verifier = Verifier()
    verify_static(verifier)
    with tempfile.TemporaryDirectory(prefix="d7ak6f-c2-verifier-") as tmp:
        verify_runtime(verifier, Path(tmp))

    print(f"checks={verifier.checks} failures={verifier.failures}")
    print("collector_entrypoint=app.collector.run")
    print("collector_mode=mock")
    print("network_calls=0")
    print("smtp_connections=0")
    print("teams_sends=0")
    print("telegram_sends=0")
    print("production_state_writes=0")
    if verifier.failures:
        print("RESULT=D7-AK-6F-C2_SHADOW_ORCHESTRATION_VERIFIER_FAIL")
        return 1
    print("RESULT=D7-AK-6F-C2_SHADOW_ORCHESTRATION_VERIFIER_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
