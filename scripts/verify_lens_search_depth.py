#!/usr/bin/env python3
"""D7-I verifier — lens query depth, collection caps, and generated bank depth.

Checks stay honest: enough query depth and caps must exist in source, but live-news
shortage is reported as a shortage rather than converted into fake rows or counts.
"""

import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "data" / "lens_queries.json"
COLLECTOR = ROOT / "app" / "live_collector.py"
BUILDER = ROOT / "scripts" / "build_static_dashboard.py"
BRIEF_BUILDER = ROOT / "scripts" / "build_executive_brief.py"
DASHBOARD = ROOT / "docs" / "daily" / "dashboard-latest.html"
TEMPLATE = ROOT / "templates" / "dashboard_preview.html"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import lens_queries  # noqa: E402
from scripts.build_static_dashboard import _brief_signal_pool, _is_http, _key, _lens_for  # noqa: E402

NEWLY_CONFIGURED = ["hormuz", "overseas_branch", "overseas_subsidiary"]
MIN_QUERY_LENSES = ["global_business", "hormuz", "overseas_site"]
ESSENTIALS = {"all", "now", "new", "ai"}
MAX_MODEL_BYTES = 800_000

_failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    tag = "PASS" if ok else "FAIL"
    line = f"[{tag}] {name}"
    if detail:
        line += f" — {detail}"
    print(line)
    if not ok:
        _failures.append(name)
    return ok


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _model(html: str) -> dict:
    m = re.search(r'<script type="application/json" id="preview-model">\s*(.*?)\s*</script>',
                  html, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except ValueError:
        return {}


def _nav_buttons(html: str) -> dict[str, str]:
    return {
        m.group(1): m.group(0)
        for m in re.finditer(r'<button\b[^>]*class="nav[^"]*"[^>]*data-filter="([^"]+)"'
                             r'[\s\S]*?</button>', html)
    }


def _nav_counts(html: str) -> dict[str, int | None]:
    out: dict[str, int | None] = {}
    for key, button in _nav_buttons(html).items():
        cm = re.search(r'<span class="ncount">(\d+)</span>', button)
        out[key] = int(cm.group(1)) if cm else None
    return out


def _load_config() -> dict:
    try:
        data = json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _query_lenses(cfg: dict) -> dict:
    lenses = cfg.get("lenses") or {}
    return {
        key: spec
        for key, spec in lenses.items()
        if isinstance(spec, dict)
        and spec.get("supported") is True
        and spec.get("collection") == "query"
    }


def _run_brief(news_mode: str) -> dict:
    env = dict(os.environ)
    env["NEWS_MODE"] = news_mode or "mock"
    env.setdefault("APP_MODE", "mock")
    proc = subprocess.run([sys.executable, str(BRIEF_BUILDER), "--json"],
                          cwd=ROOT, env=env, capture_output=True, text=True, timeout=450)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout)[-1200:])
    return json.loads(proc.stdout)


def _displayable_counts(brief: dict) -> Counter:
    counts: Counter = Counter()
    seen_titles: defaultdict[str, set[str]] = defaultdict(set)
    for sig in _brief_signal_pool(brief):
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


def _all_counts(brief: dict) -> Counter:
    counts: Counter = Counter()
    seen = set()
    for sig in _brief_signal_pool(brief):
        k = _key(sig)
        if not k or k in seen:
            continue
        seen.add(k)
        for lens in _lens_for(sig):
            counts[lens] += 1
    return counts


def check_config_and_groups(cfg: dict) -> None:
    qlenses = _query_lenses(cfg)
    for key in NEWLY_CONFIGURED:
        spec = (cfg.get("lenses") or {}).get(key) or {}
        check(f"1a: {key} supported query lens",
              spec.get("supported") is True and spec.get("collection") == "query",
              f"supported={spec.get('supported')} collection={spec.get('collection')}")

    group_names = {str(g.get("name", "")).split(":", 1)[-1]
                   for g in lens_queries.collection_query_groups()}
    missing_groups = [key for key in NEWLY_CONFIGURED if key not in group_names]
    check("1b: collection_query_groups includes newly configured lenses",
          not missing_groups,
          f"missing={missing_groups}" if missing_groups else f"{len(group_names)} groups")

    too_shallow = []
    for key, spec in sorted(qlenses.items()):
        collect = [q for q in (spec.get("collect") or []) if isinstance(q, str) and q.strip()]
        if len(collect) < 6:
            too_shallow.append(f"{key}:{len(collect)}")
    check("1c: configured query lenses have at least 6 collect queries",
          not too_shallow,
          f"too shallow={too_shallow}" if too_shallow else f"{len(qlenses)} query lenses")

    deep_short = []
    for key in MIN_QUERY_LENSES:
        spec = qlenses.get(key) or {}
        count = len([q for q in (spec.get("collect") or []) if isinstance(q, str) and q.strip()])
        if count < 8:
            deep_short.append(f"{key}:{count}")
    check("1d: global/hormuz/overseas_site have at least 8 collect queries",
          not deep_short,
          f"short={deep_short}" if deep_short else "ok")


def check_source_caps(collector: str, builder: str) -> None:
    check("2a: live_collector lens groups collect at least 3 per query",
          "max(3, min(4, default_per_query))" in collector)
    check("2b: live_collector lens groups allow at least 12 total rows",
          "min(32, max(12, len(queries) * 4))" in collector)
    check("2c: dashboard lens bank cap is 10",
          "LENS_BANK_CAP = 10" in builder
          and 'cap = AI_BANK_CAP if lens == "ai" else LENS_BANK_CAP' in builder
          and "len(banks[lens]) >= cap" in builder)
    check("2d: dashboard global news_rows cap is 20",
          "NEWS_ROW_CAP = 20" in builder and "pool[:NEWS_ROW_CAP]" in builder)


def check_generated_model(html: str, model: dict, brief: dict) -> None:
    banks = model.get("lens_banks") or {}
    bankc = {k: len(v or []) for k, v in banks.items()}
    navc = _nav_counts(html)
    buttons = _nav_buttons(html)
    policy = model.get("lens_policy") or {}
    displayable = _displayable_counts(brief)
    allc = _all_counts(brief)

    check("3a: generated dashboard model has lens_banks",
          isinstance(banks, dict) and bool(banks), f"{len(banks)} banks")
    check("3b: generated model size remains reasonable",
          len(json.dumps(model, ensure_ascii=False).encode("utf-8")) < MAX_MODEL_BYTES,
          f"{len(json.dumps(model, ensure_ascii=False).encode('utf-8'))} bytes")

    bad_rows = []
    bad_urls = []
    for lens, rows in banks.items():
        seen_titles = set()
        for row in rows or []:
            title = (row.get("title") or "").strip()
            url = str(row.get("url") or "")
            if not (title and row.get("source") and row.get("provenance")
                    and row.get("lens")):
                bad_rows.append((lens, title))
            if title in seen_titles:
                bad_rows.append((lens, f"duplicate:{title}"))
            seen_titles.add(title)
            if (not url.startswith(("http://", "https://")) or url == "#"
                    or "example.com" in url):
                bad_urls.append((lens, title, url))
    check("3c: bank rows keep real title/source/url/lens/provenance",
          not bad_rows, f"bad={bad_rows[:4]}" if bad_rows else "ok")
    check("3d: bank rows have no fake URL",
          not bad_urls, f"bad_urls={bad_urls[:4]}" if bad_urls else "ok")

    stale_counts = []
    for key, count in bankc.items():
        if key in ESSENTIALS:
            continue
        if count > 0 and navc.get(key) != count:
            stale_counts.append(f"{key}:nav={navc.get(key)} bank={count}")
    check("3e: nav counts reflect positive bank counts",
          not stale_counts,
          f"stale={stale_counts[:6]}" if stale_counts else "ok")

    depth_fail = []
    shortages = []
    for key, avail in sorted(displayable.items()):
        bcount = bankc.get(key, 0)
        if avail >= 8 and bcount < 8:
            depth_fail.append(f"{key}:displayable={avail} bank={bcount}")
        elif avail < 8:
            shortages.append(f"{key}:displayable={avail} bank={bcount}")
    check("3f: displayable_count>=8 lenses expose at least 8 bank rows",
          not depth_fail,
          f"depth_fail={depth_fail[:6]}" if depth_fail else "ok")
    if shortages:
        print("[INFO] 3f-shortage: insufficient real displayable news, not failing — "
              + "; ".join(shortages[:10]))

    bad_waiting = []
    for key in NEWLY_CONFIGURED:
        pol = policy.get(key) or {}
        button = buttons.get(key, "")
        waiting = ("연동 대기" in button or "dm unavail" in button
                   or "data-waiting" in button or pol.get("collection") == "unconfigured"
                   or pol.get("supported") is False)
        if waiting:
            bad_waiting.append(key)
    check("3g: newly configured lenses are not waiting/unconfigured in generated dashboard",
          not bad_waiting,
          f"bad_waiting={bad_waiting}" if bad_waiting else "ok")

    fake_counts = []
    for key, button in buttons.items():
        if key in ESSENTIALS:
            continue
        cm = re.search(r'<span class="ncount">(\d+)</span>', button)
        if cm and int(cm.group(1)) != bankc.get(key, 0):
            fake_counts.append(f"{key}:nav={cm.group(1)} bank={bankc.get(key,0)}")
    check("3h: generated sidebar has no fake numeric counts",
          not fake_counts,
          f"fake_counts={fake_counts[:6]}" if fake_counts else "ok")

    print("\nLens depth snapshot:")
    for key in sorted(policy):
        if key in {"all"}:
            continue
        pol = policy.get(key) or {}
        print(f"  {key}: collection={pol.get('collection')} "
              f"all={allc.get(key,0)} displayable={displayable.get(key,0)} "
              f"bank={bankc.get(key,0)} nav={navc.get(key)}")


def check_template_copy(tpl: str) -> None:
    check("4a: configured-empty copy uses public-news no-match wording",
          "금일 공개뉴스 매칭 결과 없음" in tpl)
    check("4b: template separates configured empty from unconfigured waiting",
          "function isUnconfiguredLens" in tpl and "data-empty" in tpl
          and "lensEmptyTitle" in tpl and "lensWaitingTitle" in tpl)


def main() -> int:
    print(f"== verify_lens_search_depth @ {ROOT} ==")
    cfg = _load_config()
    html = _read(DASHBOARD)
    model = _model(html)
    if not check("0: config + generated dashboard readable",
                 bool(cfg) and bool(model), f"html={len(html)} chars"):
        print("\nRESULT: FAIL")
        return 1

    check_config_and_groups(cfg)
    check_source_caps(_read(COLLECTOR), _read(BUILDER))
    check_template_copy(_read(TEMPLATE))

    mode = ((model.get("meta") or {}).get("news_data_mode") or os.environ.get("NEWS_MODE")
            or "mock")
    try:
        brief = _run_brief(mode)
    except Exception as exc:  # noqa: BLE001
        check("3: fresh brief build for generated mode", False, str(exc))
    else:
        check("3: fresh brief build for generated mode", True, f"NEWS_MODE={mode}")
        check_generated_model(html, model, brief)

    print()
    if _failures:
        print(f"RESULT: FAIL ({len(_failures)} 항목)")
        for failure in _failures:
            print(f"  - {failure}")
        return 1
    print("RESULT: PASS — dashboard lens search depth and configured lens states are honest")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
