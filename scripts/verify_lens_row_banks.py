#!/usr/bin/env python3
"""D7-H verifier — lens-specific row banks prevent top-N clipping.

Runs a fresh dashboard build in the current NEWS_MODE (default: mock). It verifies that
the preview model contains per-lens article banks built from the full brief, so collected
department/business lens articles are not hidden merely because the global news feed is
short.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "scripts" / "build_static_dashboard.py"
BRIEF_BUILDER = ROOT / "scripts" / "build_executive_brief.py"
TEMPLATE = ROOT / "templates" / "dashboard_preview.html"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_static_dashboard import _is_http, _key, _lens_for  # noqa: E402

IMPORTANT_LENSES = [
    "building_housing", "development_business", "trust_companies",
    "developers", "domestic_site", "hyundai_group",
]
ESSENTIALS = {"all", "now", "new", "ai"}
MAX_MODEL_BYTES = 650_000

_failures = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    tag = "PASS" if ok else "FAIL"
    line = f"[{tag}] {name}"
    if detail:
        line += f" — {detail}"
    print(line)
    if not ok:
        _failures.append(name)
    return ok


def _env() -> dict:
    env = dict(os.environ)
    env.setdefault("NEWS_MODE", "mock")
    env.setdefault("APP_MODE", "mock")
    return env


def _model(html: str) -> dict:
    m = re.search(r'<script type="application/json" id="preview-model">\s*(.*?)\s*</script>',
                  html, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except ValueError:
        return {}


def _brief_signals(brief: dict) -> list:
    out, seen = [], set()

    def walk(node):
        if isinstance(node, dict):
            if node.get("title") and (node.get("url") or node.get("source")):
                k = _key(node)
                if k and k not in seen:
                    seen.add(k)
                    out.append(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(brief)
    return out


def _nav_counts(html: str) -> dict:
    counts = {}
    for line in html.splitlines():
        mm = re.search(r'class="nav[^"]*"\s+data-filter="([^"]+)"', line)
        if not mm:
            continue
        cm = re.search(r'<span class="ncount">(\d+)</span>', line)
        counts[mm.group(1)] = int(cm.group(1)) if cm else None
    return counts


def _build() -> tuple[dict, str, dict]:
    env = _env()
    with tempfile.TemporaryDirectory(prefix="hdec_lens_banks_") as tmp:
        brief_proc = subprocess.run([sys.executable, str(BRIEF_BUILDER), "--json"],
                                    cwd=ROOT, env=env, capture_output=True, text=True,
                                    timeout=300)
        if brief_proc.returncode != 0:
            raise RuntimeError((brief_proc.stderr or brief_proc.stdout)[-1000:])
        out = Path(tmp) / "dashboard.html"
        dash_proc = subprocess.run(
            [sys.executable, str(BUILDER), "--market-mode", "mock", "--output", str(out)],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
        if dash_proc.returncode != 0 or not out.exists():
            raise RuntimeError((dash_proc.stderr or dash_proc.stdout)[-1000:])
        return json.loads(brief_proc.stdout), out.read_text(encoding="utf-8"), env


def _brief_counts(signals: list) -> Counter:
    counts = Counter()
    for sig in signals:
        for lens in _lens_for(sig):
            counts[lens] += 1
    return counts


def _displayable_counts(signals: list) -> Counter:
    counts = Counter()
    seen_titles = defaultdict(set)
    for sig in signals:
        if not _is_http(sig.get("url")):
            continue
        title = (sig.get("title") or "").strip()
        if not title:
            continue
        for lens in _lens_for(sig):
            if title in seen_titles[lens]:
                continue
            seen_titles[lens].add(title)
            counts[lens] += 1
    return counts


def _bank_counts(model: dict) -> dict:
    return {k: len(v or []) for k, v in (model.get("lens_banks") or {}).items()}


def check_banks(brief: dict, html: str, model: dict, mode: str) -> None:
    banks = model.get("lens_banks") or {}
    signals = _brief_signals(brief)
    counts = _brief_counts(signals)
    displayable = _displayable_counts(signals)
    bankc = _bank_counts(model)
    navc = _nav_counts(html)
    policy = model.get("lens_policy") or {}

    check("1a: preview-model has lens_banks", isinstance(banks, dict) and bool(banks),
          f"{len(banks)} banks")
    missing = [k for k, v in sorted(counts.items()) if v > 0 and bankc.get(k, 0) == 0]
    check("1b: every full-brief lens with collected signals has a bank row",
          not missing, f"missing: {missing}" if missing else "ok")

    important_missing = [k for k in IMPORTANT_LENSES
                         if counts.get(k, 0) > 0 and bankc.get(k, 0) == 0]
    check("1c: clipped business/department lenses have bank rows when collected",
          not important_missing,
          f"missing: {important_missing}" if important_missing else "ok")
    undersized = []
    for key, avail in sorted(displayable.items()):
        target = min(3, avail, 6)
        if avail > 0 and bankc.get(key, 0) < target:
            undersized.append(f"{key}:bank={bankc.get(key,0)} target={target} avail={avail}")
    check("1c-2: banks hit minimum target 3 when displayable rows are available",
          not undersized,
          f"undersized: {undersized[:5]}" if undersized else "ok")

    waiting_with_bank = []
    for key, ncount in navc.items():
        pol = policy.get(key) or {}
        unconfigured = pol.get("collection") == "unconfigured" or pol.get("supported") is False
        if key not in ESSENTIALS and bankc.get(key, 0) > 0 and (ncount in (0, None) or unconfigured):
            waiting_with_bank.append(key)
    check("1d: banked lenses are not waiting/unconfigured in primary nav",
          not waiting_with_bank,
          f"wrong waiting: {waiting_with_bank}" if waiting_with_bank else "ok")

    bad_rows = []
    dup_titles = []
    fake_urls = []
    for lens, rows in banks.items():
        seen_titles = set()
        for row in rows or []:
            title = (row.get("title") or "").strip()
            if title in seen_titles:
                dup_titles.append((lens, title))
            seen_titles.add(title)
            if not (title and row.get("source") and row.get("url")
                    and row.get("lens") and row.get("provenance")):
                bad_rows.append((lens, title))
            url = str(row.get("url") or "")
            mock_fixture = mode == "mock" and "example.com/mock-" in url
            if (not url.startswith(("http://", "https://")) or url == "#"
                    or ("example.com" in url and not mock_fixture)):
                fake_urls.append((lens, title, url))
    check("2a: every bank row has title/source/url/lens/provenance",
          not bad_rows, f"bad rows: {bad_rows[:3]}" if bad_rows else "ok")
    check("2b: no bank row has fake/non-http URL",
          not fake_urls, f"bad urls: {fake_urls[:3]}" if fake_urls else "ok")
    check("2c: no duplicate titles inside a bank",
          not dup_titles, f"dupes: {dup_titles[:3]}" if dup_titles else "ok")

    stale = []
    for key, bcount in bankc.items():
        if key in ESSENTIALS:
            continue
        if navc.get(key) != bcount:
            stale.append(f"{key}:nav={navc.get(key)} bank={bcount}")
    check("3a: nav counts reflect bank counts for non-essential lenses",
          not stale, f"stale: {stale[:5]}" if stale else "ok")
    check("3b: dashboard model size remains reasonable",
          len(json.dumps(model, ensure_ascii=False).encode("utf-8")) < MAX_MODEL_BYTES,
          f"{len(json.dumps(model, ensure_ascii=False).encode('utf-8'))} bytes")

    tpl = TEMPLATE.read_text(encoding="utf-8")
    filter_ok = (
        "MODEL.lens_banks" in tpl
        and "bankRows || MODEL.news_rows" in tpl
        and "function applyLens" in tpl
        and "function filterPanel" in tpl
    )
    check("4a: lens filters use lens_banks while preserving existing filter functions",
          filter_ok)

    empty_waiting = [k for k, pol in policy.items()
                     if k not in ESSENTIALS
                     and (pol.get("collection") == "unconfigured" or bankc.get(k, 0) == 0)]
    false_waiting = [k for k in empty_waiting if bankc.get(k, 0) > 0]
    check("4b: waiting set contains only true empty/unconfigured lenses",
          not false_waiting,
          f"false waiting: {false_waiting}" if false_waiting else
          f"waiting candidates: {empty_waiting}")


def main() -> int:
    print(f"== verify_lens_row_banks @ {ROOT} ==")
    try:
        brief, html, env = _build()
    except Exception as exc:  # noqa: BLE001
        check("0: fresh brief/dashboard build", False, str(exc))
        print("\nRESULT: FAIL")
        return 1
    model = _model(html)
    check("0: fresh brief/dashboard build", bool(model),
          f"NEWS_MODE={env.get('NEWS_MODE')} html={len(html)} chars")
    if model:
        check_banks(brief, html, model, env.get("NEWS_MODE", "mock"))

    print()
    if _failures:
        print(f"RESULT: FAIL ({len(_failures)} 항목)")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("RESULT: PASS — lens_banks expose collected lens articles without top-N clipping")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
