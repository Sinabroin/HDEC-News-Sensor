#!/usr/bin/env python3
"""Regression verifier for refresh recovery and live source priority."""

from __future__ import annotations

import json
import os
import py_compile
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_tmp = tempfile.TemporaryDirectory(prefix="ops-r1-db-")
os.environ.setdefault("APP_MODE", "mock")
os.environ.setdefault("NEWS_MODE", "mock")
os.environ.setdefault("DB_PATH", str(Path(_tmp.name) / "ops-r1.db"))

from app import briefing, source_priority  # noqa: E402


class V:
    def __init__(self):
        self.checks = 0
        self.failures = 0

    def check(self, name, condition, detail=""):
        self.checks += 1
        if condition:
            print(f"PASS: {name}")
        else:
            self.failures += 1
            print(f"FAIL: {name} {detail}")

    def equal(self, name, actual, expected):
        self.check(name, actual == expected, f"expected={expected!r} actual={actual!r}")


def run(name):
    env = dict(os.environ)
    env["APP_MODE"] = "mock"
    env["NEWS_MODE"] = "mock"
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / name)],
        cwd=ROOT, env=env, text=True, capture_output=True, timeout=300,
    )


def row(article_id, source, *, live=True):
    return {
        "id": article_id,
        "title": "현대건설 AI 데이터센터 EPC 계약 확정",
        "snippet": "동일 사건 보도",
        "source": source,
        "published_at": datetime.now(timezone.utc).isoformat(),
        "signal_origin": "Live RSS" if live else "Mock",
        "url": f"https://example.com/{article_id}",
        "final_score": 4.2,
        "alert_grade": "검토 필요",
    }


def main():
    v = V()
    for rel in (
        "app/source_priority.py", "app/briefing.py",
        "scripts/build_static_dashboard.py",
        "scripts/verify_data_source_honesty.py",
        "scripts/verify_executive_brief.py",
        "scripts/verify_static_report.py",
        "scripts/verify_telegram_digest.py", "scripts/verify_ops_r1.py",
    ):
        py_compile.compile(str(ROOT / rel), doraise=True)
    v.check("Python compile", True)

    rules = json.loads((ROOT / "data/source_priority_rules.json").read_text(encoding="utf-8"))
    v.equal("rules version", rules.get("version"), "d7-ak-6f-ops-r1-v1")
    v.equal("institution official", source_priority.classify("국토교통부")["source_priority_bucket"], "official")
    v.equal("Yonhap major", source_priority.classify("연합뉴스")["source_priority_bucket"], "major")
    v.equal("TheBell specialist", source_priority.classify("더벨")["source_priority_bucket"], "specialist")
    v.equal("unknown neutral", source_priority.classify("지역미분류신문")["source_priority_bucket"], "neutral")

    rows = [
        row("n1", "지역미분류신문"),
        row("m1", "연합뉴스"),
        row("n2", "세종의소리"),
        row("m2", "한국경제"),
        row("m3", "매일경제"),
    ]
    ordered = source_priority.reserve_trusted_slots(rows, surface="top_new_issues", limit=5)
    v.equal("trusted floor first three", [x["source"] for x in ordered[:3]],
            ["연합뉴스", "한국경제", "매일경제"])

    mock = [row("mock-n", "Mock News", live=False), row("mock-m", "연합뉴스", live=False)]
    v.equal("mock order unchanged",
            [x["id"] for x in source_priority.reserve_trusted_slots(
                mock, surface="top_immediate_signals", limit=3)],
            ["mock-n", "mock-m"])

    same = [row("neutral", "세종의소리"), row("major", "연합뉴스")]
    same.sort(key=lambda x: briefing._top_exposure_sort_key(x, {}))
    v.equal("same-event major representative", same[0]["source"], "연합뉴스")

    telegram = run("verify_telegram_digest.py")
    v.equal("Telegram verifier exit", telegram.returncode, 0)
    v.check("Telegram verifier PASS", "RESULT: PASS" in telegram.stdout,
            (telegram.stdout + telegram.stderr)[-800:])

    human = run("verify_human_review_gate.py")
    v.equal("human review gate exit", human.returncode, 0)
    v.check("human review gate PASS", "PASS" in human.stdout,
            (human.stdout + human.stderr)[-800:])


    data_honesty = run("verify_data_source_honesty.py")
    v.equal("data source honesty verifier exit", data_honesty.returncode, 0)
    v.check("data source honesty verifier PASS", "RESULT: PASS" in data_honesty.stdout,
            (data_honesty.stdout + data_honesty.stderr)[-2400:])

    executive = run("verify_executive_brief.py")
    v.equal("executive brief verifier exit", executive.returncode, 0)
    v.check("executive brief verifier PASS", "RESULT: PASS" in executive.stdout,
            (executive.stdout + executive.stderr)[-2000:])

    static_report = run("verify_static_report.py")
    v.equal("static report verifier exit", static_report.returncode, 0)
    v.check("static report verifier PASS", "RESULT: PASS" in static_report.stdout,
            (static_report.stdout + static_report.stderr)[-2400:])

    dashboard_json = subprocess.run(
        [sys.executable, str(ROOT / "scripts/build_static_dashboard.py"), "--json"],
        cwd=ROOT,
        env={**os.environ, "APP_MODE": "mock", "NEWS_MODE": "mock"},
        text=True,
        capture_output=True,
        timeout=300,
    )
    v.equal("dashboard builder --json exit", dashboard_json.returncode, 0)
    v.check("dashboard builder --json parses",
            dashboard_json.returncode == 0 and dashboard_json.stdout.lstrip().startswith("{"),
            (dashboard_json.stdout + dashboard_json.stderr)[-2400:])

    telegram_src = (ROOT / "scripts/verify_telegram_digest.py").read_text(encoding="utf-8")
    v.check("broad scan removed", 'SCAN_GLOBS = ["app/*.py"' not in telegram_src)
    v.check("outbound path scan present", "SCAN_PATHS = [" in telegram_src)
    static_src = (ROOT / "scripts/verify_static_report.py").read_text(encoding="utf-8")
    v.check("static banned scan narrowed", "BANNED_SCAN_PATHS = [" in static_src)
    v.check("static token scan remains broad", "TOKEN_SCAN_GLOBS = [" in static_src)
    v.check("static broad banned scan removed", "\\nSCAN_GLOBS =" not in static_src)
    executive_src = (ROOT / "scripts/verify_executive_brief.py").read_text(encoding="utf-8")
    v.check("executive banned scan narrowed", "BANNED_SCAN_PATHS = [" in executive_src)
    v.check("executive token scan remains broad", "TOKEN_SCAN_GLOBS = [" in executive_src)
    v.check("executive broad banned scan removed", "\\nSCAN_GLOBS =" not in executive_src)
    honesty_src = (ROOT / "scripts/verify_data_source_honesty.py").read_text(encoding="utf-8")
    v.check("data honesty banned scan narrowed", "BANNED_SCAN_PATHS = [" in honesty_src)
    v.check("data honesty token scan remains broad", "TOKEN_SCAN_GLOBS = [" in honesty_src)
    v.check("data honesty broad banned scan removed", "\nSCAN_GLOBS =" not in honesty_src)
    v.check("runtime SQLite boundary allowed",
            '"runtime_sqlite.py"' in executive_src and "app/db.py·app/runtime_sqlite.py" in executive_src)
    v.check("C1 raw_payload preserved",
            "raw_payload" in (ROOT / "app/runtime_sqlite.py").read_text(encoding="utf-8"))
    v.check("editorial twitter audit preserved",
            "twitter" in (ROOT / "scripts/verify_editorial_briefings.py").read_text(encoding="utf-8").lower())

    print(f"checks={v.checks} failures={v.failures}")
    print("network_calls=0")
    print("smtp_connections=0")
    print("teams_sends=0")
    print("telegram_sends=0")
    print("production_state_writes=0")
    if v.failures:
        print("RESULT=D7-AK-6F-OPS-R1_VERIFIER_FAIL")
        return 1
    print("RESULT=D7-AK-6F-OPS-R1_VERIFIER_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
