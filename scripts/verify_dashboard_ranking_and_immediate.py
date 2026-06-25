#!/usr/bin/env python3
"""D7-J verifier — score-first dashboard ranking and honest immediate lens.

The generated dashboard must not let a lower-score featured card outrank the visible
overall feed. The "즉시 확인" lens must be backed by real rows, and fallback rows must be
marked as executive-review candidates rather than strict instant alerts.
"""

import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "docs" / "daily" / "dashboard-latest.html"
CONFIG = ROOT / "data" / "lens_queries.json"
BRIEF_BUILDER = ROOT / "scripts" / "build_executive_brief.py"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import lens_queries  # noqa: E402
from scripts.build_static_dashboard import _brief_signal_pool, _is_http, _lens_for  # noqa: E402

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


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _model(html: str) -> dict:
    m = re.search(r'<script type="application/json" id="preview-model">\s*(.*?)\s*</script>',
                  html, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except ValueError:
        return {}


def _score(row: dict) -> float:
    try:
        return float(row.get("score") or 0)
    except (TypeError, ValueError):
        return 0.0


def _featured_row_from_html(html: str) -> dict:
    m = re.search(r'<article class="card featured"[\s\S]*?</article>', html)
    if not m:
        return {}
    block = m.group(0)
    title = re.search(r'<h2>([\s\S]*?)</h2>', block)
    score = re.search(r'<div class="v num"[^>]*>([0-9.]+)', block)
    source = re.search(r'<div class="meta"><b>([\s\S]*?)</b>', block)
    lenses = re.search(r'data-lens="([^"]*)"', block)
    return {
        "title": re.sub(r"<[^>]+>", "", title.group(1)).strip() if title else "",
        "score": score.group(1) if score else "0",
        "source": re.sub(r"<[^>]+>", "", source.group(1)).strip() if source else "",
        "lens": (lenses.group(1).split() if lenses else []),
    }


def _nonincreasing(rows: list[dict]) -> bool:
    scores = [_score(r) for r in rows]
    return all(scores[i] + 1e-9 >= scores[i + 1] for i in range(len(scores) - 1))


def _run_live_brief() -> dict:
    env = dict(os.environ)
    env["NEWS_MODE"] = "live"
    env.setdefault("APP_MODE", "mock")
    proc = subprocess.run([sys.executable, str(BRIEF_BUILDER), "--json"],
                          cwd=ROOT, env=env, capture_output=True, text=True, timeout=450)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout)[-1200:])
    return json.loads(proc.stdout)


def _hyundai_candidate_counts(brief: dict) -> tuple[int, int]:
    entity_terms = (
        "현대엔지니어링", "현대ENG", "현대로템", "현대제철", "HD현대", "HD 현대",
        "HD현대일렉트릭", "현대일렉트릭", "현대글로비스", "현대모비스", "현대차그룹",
        "현대자동차그룹", "현대차", "현대자동차",
    )
    context_terms = (
        "건설", "인프라", "플랜트", "EPC", "수주", "철도", "도시철도", "고속철",
        "철강", "철근", "H형강", "전력", "전력기기", "변압기", "데이터센터", "물류",
        "투자", "에너지", "원전", "SMR", "공급망", "설비", "특허", "기술", "O&M",
    )
    before = 0
    displayable = 0
    seen = set()
    for sig in _brief_signal_pool(brief):
        title = sig.get("title") or ""
        if not (any(t in title for t in entity_terms) and any(c in title for c in context_terms)):
            continue
        before += 1
        key = (title, sig.get("url"))
        if key in seen:
            continue
        seen.add(key)
        if _is_http(sig.get("url")) and "hyundai_group" in _lens_for(sig):
            displayable += 1
    return before, displayable


def check_immediate(model: dict) -> None:
    banks = model.get("lens_banks") or {}
    now_rows = banks.get("now") or []
    status = model.get("immediate_status") or {}
    strict_displayable = int(status.get("strict_displayable_count") or 0)
    fallback_count = int(status.get("fallback_count") or 0)

    bad_rows = []
    for row in now_rows:
        url = str(row.get("url") or "")
        if not (url.startswith(("http://", "https://")) and row.get("provenance")):
            bad_rows.append(row.get("title") or "")
    check("2a: now bank rows have real URL and provenance",
          not bad_rows, f"bad={bad_rows[:3]}" if bad_rows else f"{len(now_rows)} rows")

    if strict_displayable > 0:
        bad_basis = [r.get("title") for r in now_rows
                     if r.get("immediate_basis") != "strict_instant"]
        check("2b: strict immediate rows keep strict_instant basis",
              bool(now_rows) and not bad_basis,
              f"bad_basis={bad_basis[:3]}" if bad_basis else f"strict={strict_displayable}")
    else:
        model_candidates = [
            r for r in (model.get("news_rows") or [])
            if _score(r) >= 2.5
            or (r.get("provenance") or {}).get("executive_section") == "hdec_direct"
            or (r.get("provenance") or {}).get("radar_section") == "risk_regulation"
            or "new" in (r.get("lens") or [])
            or r.get("cat") == "리스크"
        ]
        bad_basis = [r.get("title") for r in now_rows
                     if r.get("immediate_basis") != "executive_review_candidate"]
        check("2c: fallback immediate candidates are labeled as candidates",
              not model_candidates or (fallback_count > 0 and now_rows and not bad_basis),
              f"candidates={len(model_candidates)} fallback={fallback_count} "
              f"bad_basis={bad_basis[:3]}")


def check_ranking(html: str, model: dict) -> None:
    featured = _featured_row_from_html(html)
    visible = ([featured] if featured.get("title") else []) + (model.get("news_rows") or [])
    scores = [_score(r) for r in visible]
    max_score = max(scores) if scores else 0.0
    first_score = scores[0] if scores else 0.0
    check("3a: visible overall first card has max visible score",
          bool(visible) and abs(first_score - max_score) < 1e-9,
          f"first={first_score:.1f} max={max_score:.1f} title={visible[0].get('title') if visible else ''}")
    check("3b: news_rows remain score-desc sorted",
          _nonincreasing(model.get("news_rows") or []))

    bad_banks = []
    for lens, rows in sorted((model.get("lens_banks") or {}).items()):
        if not _nonincreasing(rows or []):
            bad_banks.append(lens)
    check("3c: every lens bank is score-desc sorted",
          not bad_banks, f"bad={bad_banks}" if bad_banks else "ok")

    spotlight_bad = []
    for lens in ("plant", "global_business", "overseas_site"):
        rows = (model.get("lens_banks") or {}).get(lens) or []
        if rows and not _nonincreasing(rows):
            spotlight_bad.append(lens)
    check("3d: plant/global/overseas_site bank first row is not lower-score leakage",
          not spotlight_bad, f"bad={spotlight_bad}" if spotlight_bad else "ok")


def check_hyundai(model: dict, html: str) -> None:
    cfg = _read_json(CONFIG)
    spec = ((cfg.get("lenses") or {}).get("hyundai_group") or {})
    collect = [q for q in (spec.get("collect") or []) if isinstance(q, str) and q.strip()]
    group_names = [g.get("name") for g in lens_queries.collection_query_groups()]

    check("4a: hyundai_group is configured query lens",
          spec.get("supported") is True and spec.get("collection") == "query",
          f"supported={spec.get('supported')} collection={spec.get('collection')}")
    check("4b: hyundai_group has construction-relevant query depth",
          len(collect) >= 10 and "현대엔지니어링 데이터센터" in collect,
          f"queries={len(collect)}")
    check("4c: collection_query_groups includes prioritized lens:hyundai_group",
          "lens:hyundai_group" in group_names and group_names.index("lens:hyundai_group") <= 1,
          f"first={group_names[:4]}")

    bank_count = len(((model.get("lens_banks") or {}).get("hyundai_group") or []))
    bad_urls = [
        r.get("title") or ""
        for r in ((model.get("lens_banks") or {}).get("hyundai_group") or [])
        if not str(r.get("url") or "").startswith(("http://", "https://"))
    ]
    check("4d: hyundai_group bank has no fake URLs",
          not bad_urls, f"bad={bad_urls[:3]}" if bad_urls else f"bank={bank_count}")

    meta = model.get("meta") or {}
    if meta.get("news_data_mode") == "live":
        brief = _run_live_brief()
        audits = [
            a for a in (brief.get("news_query_audit") or [])
            if a.get("group") == "lens:hyundai_group"
        ]
        before, displayable = _hyundai_candidate_counts(brief)
        check("4e: live query audit includes lens:hyundai_group",
              bool(audits), f"audit_rows={len(audits)}")
        check("4f: displayable Hyundai candidates produce a bank when present",
              displayable == 0 or bank_count > 0,
              f"candidate_before_classification={before} displayable={displayable} bank={bank_count}")

    nav = re.search(r'<button\b[^>]*data-filter="hyundai_group"[\s\S]*?</button>', html)
    btn = nav.group(0) if nav else ""
    check("4g: configured hyundai_group is not labeled 연동 대기",
          "연동 대기" not in btn and "dm unavail" not in btn and "data-waiting" not in btn)


def check_duplicates(html: str, model: dict) -> None:
    featured = _featured_row_from_html(html)
    titles = []
    if featured.get("title"):
        titles.append(featured["title"])
    titles.extend((r.get("title") or "").strip() for r in (model.get("news_rows") or []))
    titles.extend((r.get("title") or "").strip() for r in (model.get("ai_rows") or []))
    counts = Counter(t for t in titles if t)
    dupes = [title for title, count in counts.items() if count > 1]
    check("5a: no duplicate title across featured/news_rows/ai_rows",
          not dupes, f"dupes={dupes[:3]}" if dupes else "ok")


def main() -> int:
    try:
        html = DASHBOARD.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"[FAIL] read generated dashboard — {exc}")
        return 1
    model = _model(html)
    check("1a: generated dashboard model readable", bool(model))
    check("1b: generated dashboard model has lens_banks",
          isinstance(model.get("lens_banks"), dict) and bool(model.get("lens_banks")))
    check_immediate(model)
    check_ranking(html, model)
    check_hyundai(model, html)
    check_duplicates(html, model)
    if _failures:
        print("\nFAILURES:")
        for item in _failures:
            print(" -", item)
        return 1
    print("\nOK: dashboard ranking/immediate/Hyundai verifier passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
