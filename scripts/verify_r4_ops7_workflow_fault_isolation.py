#!/usr/bin/env python3
"""Adversarial entry-point proof for R4-OPS-7 failure-domain isolation."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from r4_ops7_gate_contracts import ROOT, assert_no_broad_runtime_kill_switch


SCRIPTS = {
    product: ROOT / "scripts" / f"verify_r4_ops7_{product}_production_gate.py"
    for product in ("daily", "weekly", "editor", "watch", "refresh")
}


def run(product: str, broken_domain: str) -> int:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPTS[product]),
            "--inject-broken-domain",
            broken_domain,
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    ).returncode


def main() -> int:
    assert_no_broad_runtime_kill_switch()
    if run("watch", "daily") != 0 or run("refresh", "daily") != 0:
        raise AssertionError("Daily-only fixture escaped into Watch/Refresh gates")
    if run("daily", "watch") != 0 or run("weekly", "watch") != 0:
        raise AssertionError("Watch-only fixture escaped into editorial gates")
    if run("watch", "watch") == 0:
        raise AssertionError("Watch silently permitted its own broken classifier contract")
    if run("daily", "daily") == 0:
        raise AssertionError("Daily silently permitted its own broken identity/image contract")
    if not (ROOT / "scripts/verify_r4_ops5_production_acceptance.py").is_file():
        raise AssertionError("comprehensive integration verifier was removed")
    print("DAILY_ONLY_BROKEN_WATCH_GATE=PASS")
    print("DAILY_ONLY_BROKEN_REFRESH_GATE=PASS")
    print("WATCH_ONLY_BROKEN_DAILY_GATE=PASS")
    print("OWN_DOMAIN_FAILURES_FAIL_CLOSED=PASS")
    print("OLD_BROAD_BLAST_RADIUS_ABSENT=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
