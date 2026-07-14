#!/usr/bin/env python3
"""D7-AJ-1 regression verifier — mock signal verification is deterministic + date-independent.

Proves that a single fixture-relative mock-clock owner
(build_executive_brief.deterministic_mock_reference_time / mock_reference_clock) keeps mock
executive signals alive regardless of the run date, so the scheduled Verify pipeline no longer
fails once the fixtures age past the scoring age-cap window. Also proves the live path is left on
the production wall-clock, and that the dashboard verifier reuses the same owner (no duplication).

Fully offline: no network, no send, no repo radar.db writes (temp DB only via _bootstrap)."""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

# Deterministic, clean mock env before importing the builder (app.config reads env at import,
# and _bootstrap must point DB_PATH at a temp DB — never the repo radar.db).
os.environ["APP_MODE"] = "mock"
for _k in ("NEWS_MODE", "MACRO_MODE"):
    os.environ.pop(_k, None)

_KST = timezone(timedelta(hours=9))
_failures = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    line = f"[{'PASS' if ok else 'FAIL'}] {name}"
    if detail:
        line += f" — {detail}"
    print(line)
    if not ok:
        _failures.append(name)
    return ok


import build_executive_brief as beb  # noqa: E402

# _bootstrap sets DB_PATH to a temp DB and imports the app pipeline modules.
m = beb._bootstrap()

# Independently recompute the expected fixture-relative reference (owner must equal this).
fixtures = json.loads((ROOT / "data" / "mock_articles.json").read_text(encoding="utf-8"))
_pubs = []
for _r in fixtures:
    _dt = datetime.fromisoformat(str(_r["published_at"]))
    if _dt.tzinfo is None:
        _dt = _dt.replace(tzinfo=_KST)
    _pubs.append(_dt)
fixture_max = max(_pubs)
expected_ref = (fixture_max + timedelta(hours=6)).astimezone(_KST)
expected_gen = expected_ref.isoformat(timespec="seconds")


# --- C1/C2/C3 · the reference is fixture-relative, tz-aware KST, never in the future -----------
ref = beb.deterministic_mock_reference_time()
check("C1a reference is timezone-aware KST(+09:00)",
      ref.tzinfo is not None and ref.utcoffset() == timedelta(hours=9), ref.isoformat())
check("C1b reference == fixture max published_at + 6h", ref == expected_ref, ref.isoformat())
future_ids = [r["id"] for r, dt in zip(fixtures, _pubs) if dt > ref]
check("C2 no fixture article is in the future relative to reference",
      not future_ids, f"future={future_ids}")
tiny = beb.deterministic_mock_reference_time(
    fixtures=[{"published_at": "2020-01-01T00:00:00+09:00"}])
check("C3 reference follows the fixture data (not a hardcoded date)",
      tiny == datetime.fromisoformat("2020-01-01T06:00:00+09:00"), tiny.isoformat())


# --- C4 · today's real-clock run (33+ days past fixtures) still meets every contract ------------
brief = beb.build_brief_via_mock_pipeline()
imm = len(brief.get("top_immediate_signals") or [])
new = len(brief.get("top_new_issues") or [])
themes = len(brief.get("theme_rankings") or [])
hdec = len(brief.get("hdec_direct_signals") or [])
check("C4a top_immediate_signals in 1..3", 1 <= imm <= 3, str(imm))
check("C4b top_new_issues in 1..5", 1 <= new <= 5, str(new))
check("C4c theme_rankings in 1..5", 1 <= themes <= 5, str(themes))
check("C4d hdec_direct_signals >= 1 (source of the [현대건설 연관] label)", hdec >= 1, str(hdec))
check("C4e brief news_data_mode == mock", brief.get("news_data_mode") == "mock")
check("C4f generated_at == deterministic reference (not real wall-clock)",
      brief.get("generated_at") == expected_gen, str(brief.get("generated_at")))


# --- C5 · drift immunity: even a far-future ambient clock keeps signals alive --------------------
far = datetime(2031, 3, 1, 9, 0, tzinfo=_KST)


class _FarNow(datetime):
    @classmethod
    def now(cls, tz=None):
        return far.astimezone(tz) if tz else far.replace(tzinfo=None)


_targets = [m["scoring"], m["briefing"], m["db"]]
_saved = [t.datetime for t in _targets]
try:
    for t in _targets:
        t.datetime = _FarNow
    brief_far = beb.build_brief_via_mock_pipeline()
finally:
    for t, o in zip(_targets, _saved):
        t.datetime = o
check("C5a far-future ambient clock still yields immediate >= 1 (date-drift immune)",
      len(brief_far.get("top_immediate_signals") or []) >= 1)
check("C5b generated_at stays pinned to the fixture reference under a far-future clock",
      brief_far.get("generated_at") == expected_gen, str(brief_far.get("generated_at")))


# --- C6 · determinism: two runs on the same fixture pick the same articles/counts ---------------
b1 = beb.build_brief_via_mock_pipeline()
b2 = beb.build_brief_via_mock_pipeline()


def _ids(b, key):
    return [s.get("article_id") for s in (b.get(key) or [])]


same_ids = all(_ids(b1, k) == _ids(b2, k)
               for k in ("top_immediate_signals", "top_new_issues", "hdec_direct_signals"))
same_counts = len(b1.get("theme_rankings") or []) == len(b2.get("theme_rankings") or [])
check("C6 deterministic across runs (identical article IDs + theme count)",
      same_ids and same_counts)


# --- C7 · live path is NOT put on the mock clock; mock is; explicit reference_now wins -----------
def _branch_reference(news_mode, reference_now=None):
    """Exercise the real branch logic and capture the reference handed to mock_reference_clock,
    with the heavy pipeline stubbed so nothing touches the network (NEWS_MODE=live safe)."""
    seen = []
    orig_clock = beb.mock_reference_clock

    def spy(reference, modules):
        seen.append(reference)
        return orig_clock(None, modules)  # force no-op body — we only assert the chosen reference

    saved = (m["db"].init_db, m["collector"].run, m["scoring"].score_all,
             m["insight"].generate_all, m["briefing"].build_brief)
    beb.mock_reference_clock = spy
    m["db"].init_db = lambda: None
    m["collector"].run = lambda mode: {"collected": 0, "deduplicated": 0, "inserted": 0}
    m["scoring"].score_all = lambda: {"scored": 0, "alert_candidates": 0}
    m["insight"].generate_all = lambda: None
    m["briefing"].build_brief = lambda **kw: {}
    if news_mode is None:
        os.environ.pop("NEWS_MODE", None)
    else:
        os.environ["NEWS_MODE"] = news_mode
    try:
        beb.build_brief_via_mock_pipeline(reference_now=reference_now)
    finally:
        beb.mock_reference_clock = orig_clock
        (m["db"].init_db, m["collector"].run, m["scoring"].score_all,
         m["insight"].generate_all, m["briefing"].build_brief) = saved
        os.environ.pop("NEWS_MODE", None)
    return seen[0] if seen else "NO_CLOCK_CALL"


live_ref = _branch_reference("live")
mock_ref = _branch_reference(None)
expl_ref = _branch_reference("live", reference_now="2026-06-11T16:10:00+09:00")
check("C7a live mode does NOT apply the mock clock (reference is None)",
      live_ref is None, repr(live_ref))
check("C7b mock mode applies the deterministic reference",
      isinstance(mock_ref, datetime) and mock_ref == expected_ref, repr(mock_ref))
check("C7c explicit reference_now wins even in live mode",
      isinstance(expl_ref, datetime) and expl_ref == expected_ref, repr(expl_ref))


# --- C8 · the clock context manager is a strict, self-restoring seam -----------------------------
before = m["briefing"].datetime
with beb.mock_reference_clock(None, m):
    mid_none = m["briefing"].datetime
after_none = m["briefing"].datetime
check("C8a mock_reference_clock(None) is a no-op (live safety)",
      before is mid_none and before is after_none)
with beb.mock_reference_clock(expected_ref, m):
    mid = m["briefing"].datetime
    patched_now = m["briefing"].datetime.now(_KST)
after = m["briefing"].datetime
check("C8b mock_reference_clock(ref) patches during and restores after",
      mid is not before and after is before and patched_now == expected_ref)


# --- C9 · single owner: the dashboard verifier reuses it; the builder keeps the live carve-out ---
vdr_src = (ROOT / "scripts" / "verify_dashboard_real_data.py").read_text(encoding="utf-8")
beb_src = (ROOT / "scripts" / "build_executive_brief.py").read_text(encoding="utf-8")
check("C9a dashboard verifier reuses the common owner",
      "deterministic_mock_reference_time" in vdr_src and "mock_reference_clock" in vdr_src)
check("C9b dashboard verifier dropped its local +29d monkeypatch (no duplication)",
      "timedelta(days=29)" not in vdr_src and "class FixtureDateTime" not in vdr_src)
check("C9c builder applies the mock clock only when NEWS_MODE != live",
      '!= "live"' in beb_src and "reference = None" in beb_src)


# --- C10 · offline + isolated: the pipeline uses a temp DB, never the repo radar.db --------------
db_path = os.environ.get("DB_PATH", "")
check("C10 pipeline uses a temporary DB (repo radar.db untouched)",
      db_path.endswith("brief_mock.db") and str(ROOT) not in db_path, db_path)


print()
if _failures:
    print(f"RESULT: FAIL ({len(_failures)} check(s)) — " + "; ".join(_failures))
    raise SystemExit(1)
print("RESULT: PASS — deterministic mock signals verified (date-independent, live path intact)")
