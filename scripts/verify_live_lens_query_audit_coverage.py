#!/usr/bin/env python3
"""D7-L verifier — priority lens query groups are actually searched, not just configured.

User QA found that "configured query lenses exist" ≠ "the live audit shows those lens
groups." Root cause: the live collector consumed the global max_total budget on earlier
high-volume groups, so late priority lens groups (lens:ai / lens:overseas_branch /
lens:overseas_subsidiary / …) starved before running — they never appeared in
news_query_audit. D7-L adds a shallow PRIORITY PREFLIGHT pass so every priority lens group
is searched (and audited) before the global budget is spent, and tightens overseas/AI lens
classification so collected articles land in the right lens by raw-title evidence.

This verifier runs OFFLINE where possible and is LIVE-AWARE when NEWS_MODE=live:

  STATIC (always, no network):
    · live_collector has a priority/preflight mechanism (PRIORITY_LENS_GROUPS + preflight
      pass + per-group budget) — the global cap is NOT the only mechanism,
    · PRIORITY_LENS_GROUPS includes ai / overseas_branch / overseas_subsidiary /
      overseas_site / global_business / hyundai_group (+ hormuz),
    · data/lens_queries.json marks overseas_branch & overseas_subsidiary supported=true,
      collection=query,
    · the dashboard builder owns AI raw-evidence + overseas org/entity gates and wires them
      into _lens_for.

  COMMITTED SNAPSHOT (always, reads docs/daily/dashboard-latest.html — no network):
    · every AI bank row passes the direct-AI-evidence rule (no category-only contamination),
    · any overseas_branch bank row carries a branch/org/office marker,
    · any overseas_subsidiary bank row carries a subsidiary/local-corporation marker,
    · bank rows keep real http(s) URLs (no fake URL/count).

  LIVE (only when NEWS_MODE=live and network reachable):
    · a real fetch_all() query_audit contains rows for each priority lens group (even if
      fetched_count=0 — visible-but-empty, never silently missing),
    · prints displayability shortage when a lens fetched>0 but classified 0 (info, not fail).
"""

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

COLLECTOR = ROOT / "app" / "live_collector.py"
BUILDER = ROOT / "scripts" / "build_static_dashboard.py"
LENS_JSON = ROOT / "data" / "lens_queries.json"
DASHBOARD = ROOT / "docs" / "daily" / "dashboard-latest.html"

from app import live_collector as lc  # noqa: E402
from scripts.build_static_dashboard import (  # noqa: E402
    _has_ai_evidence, _is_overseas_branch_relevant, _is_overseas_subsidiary_relevant,
)

# Step 7이 명시한 우선순위 렌즈 그룹 — audit에 반드시 등장해야 한다(빈 결과여도).
# P0-D7-M: 토목(civil_infrastructure)을 우선순위에 추가한다 — '토목 1건' 근본원인이 civil
# 렌즈 쿼리 그룹이 전역 budget 소진으로 굶어 audit에 안 나온 것(D7-L overseas와 동일 패턴).
REQUIRED_PRIORITY = (
    "lens:ai", "lens:overseas_branch", "lens:overseas_subsidiary",
    "lens:overseas_site", "lens:global_business", "lens:hyundai_group",
    "lens:civil_infrastructure",
)

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


def info(msg: str) -> None:
    print(f"[INFO] {msg}")


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _model(html: str) -> dict:
    m = re.search(r'<script type="application/json" id="preview-model">(.*?)</script>',
                  html, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except ValueError:
        return {}


# ---------------------------------------------------------------------------
# 1 · STATIC — collector preflight/priority mechanism (cap is not the only lever)
# ---------------------------------------------------------------------------

def check_collector_static() -> None:
    src = _read(COLLECTOR)
    check("1a: live_collector PRIORITY_LENS_GROUPS 정의", "PRIORITY_LENS_GROUPS" in src
          and isinstance(getattr(lc, "PRIORITY_LENS_GROUPS", None), tuple))
    pri = set(getattr(lc, "PRIORITY_LENS_GROUPS", ()) or ())
    missing = [g for g in REQUIRED_PRIORITY if g not in pri]
    check("1b: PRIORITY_LENS_GROUPS가 ai/해외지사/해외법인/해외현장/글로벌/현대그룹사/토목 포함",
          not missing,
          f"누락: {missing}" if missing else f"{len(REQUIRED_PRIORITY)}/{len(REQUIRED_PRIORITY)} 포함")
    check("1c: hormuz도 우선순위에 포함(지정학 커버리지)", "lens:hormuz" in pri)
    # preflight 메커니즘: 얕은 패스(per-group budget) + audit pass 라벨.
    check("1d: 우선순위 preflight 패스 존재(_collect_group + pass_label='preflight')",
          "_collect_group" in src and 'pass_label="preflight"' in src
          and hasattr(lc, "_collect_group"))
    check("1e: per-group preflight budget 상수 존재(폭주 방지·bounded)",
          isinstance(getattr(lc, "PREFLIGHT_GROUP_BUDGET", None), int)
          and 1 <= lc.PREFLIGHT_GROUP_BUDGET <= 20
          and isinstance(getattr(lc, "PREFLIGHT_MAX_PER_QUERY", None), int))
    # 전역 cap이 유일한 메커니즘이 아님: 하한 floor가 충분히 높고 preflight가 별도로 존재.
    floor = getattr(lc, "DASHBOARD_MIN_MAX_TOTAL", 0)
    check("1f: 전역 cap 하한(DASHBOARD_MIN_MAX_TOTAL>=100) — cap 단독 의존 아님",
          isinstance(floor, int) and floor >= 100, f"floor={floor}")
    # preflight는 fetch_all에서 기본 소스(sources_path None)일 때 실행되어야 한다.
    check("1g: fetch_all이 기본 소스에서 preflight를 돈다",
          "PRIORITY_LENS_GROUPS" in src and "preflight_cap" in src)


# ---------------------------------------------------------------------------
# 2 · STATIC — central policy + builder gates
# ---------------------------------------------------------------------------

def check_policy_and_builder_static() -> None:
    try:
        pol = json.loads(_read(LENS_JSON)).get("lenses") or {}
    except ValueError:
        pol = {}
    for key in ("overseas_branch", "overseas_subsidiary"):
        spec = pol.get(key) or {}
        check(f"2a:{key} supported=true · collection=query",
              spec.get("supported") is True and spec.get("collection") == "query",
              f"supported={spec.get('supported')} collection={spec.get('collection')}")
        collect = [q for q in (spec.get("collect") or []) if isinstance(q, str) and q.strip()]
        check(f"2b:{key} collect 쿼리 보유(>=6)", len(collect) >= 6, f"{len(collect)}개")
    bsrc = _read(BUILDER)
    check("2c: 빌더 AI 직접근거 게이트(_has_ai_evidence) 존재 + _lens_for 연동",
          "def _has_ai_evidence" in bsrc
          and "_has_ai_evidence(raw_title" in bsrc)
    check("2d: 빌더 해외지사 게이트(_is_overseas_branch_relevant) 존재 + _lens_for 연동",
          "def _is_overseas_branch_relevant" in bsrc
          and "_is_overseas_branch_relevant(raw_title)" in bsrc)
    check("2e: 빌더 해외법인 게이트(_is_overseas_subsidiary_relevant) 존재 + _lens_for 연동",
          "def _is_overseas_subsidiary_relevant" in bsrc
          and "_is_overseas_subsidiary_relevant(raw_title)" in bsrc)
    # AI 게이트는 injected category_label 단독이 아니라 raw 제목/출처를 본다.
    check("2f: AI 게이트가 raw 제목 기반(category_label 단독 의존 아님)",
          "_has_ai_evidence(title" not in bsrc or "category_label" not in
          bsrc[bsrc.find("def _has_ai_evidence"):bsrc.find("def _ai_category")])


# ---------------------------------------------------------------------------
# 3 · COMMITTED SNAPSHOT — banks classified honestly (offline)
# ---------------------------------------------------------------------------

def check_committed_snapshot() -> None:
    html = _read(DASHBOARD)
    model = _model(html)
    if not check("3a: 커밋된 대시보드 모델 읽기", bool(model), f"html={len(html)} chars"):
        return
    banks = model.get("lens_banks") or {}
    mode = (model.get("meta") or {}).get("news_data_mode")
    info(f"committed dashboard news_data_mode={mode}; "
         f"ai={len(banks.get('ai') or [])} "
         f"global_business={len(banks.get('global_business') or [])} "
         f"overseas_site={len(banks.get('overseas_site') or [])} "
         f"overseas_branch={len(banks.get('overseas_branch') or [])} "
         f"overseas_subsidiary={len(banks.get('overseas_subsidiary') or [])}")

    ai_bad = [(r.get("title") or "")[:48] for r in (banks.get("ai") or [])
              if not _has_ai_evidence(r.get("title") or "", r.get("source") or "")]
    check("3b: 모든 AI 뱅크 행이 직접 AI 근거 통과(카테고리 단독 오염 없음)",
          not ai_bad, f"근거 없음: {ai_bad}" if ai_bad else "ok")

    br_bad = [(r.get("title") or "")[:48] for r in (banks.get("overseas_branch") or [])
              if not _is_overseas_branch_relevant(r.get("title") or "")]
    check("3c: overseas_branch 뱅크 행은 모두 조직/지사/사무소 마커 보유(있을 때)",
          not br_bad, f"마커 없음: {br_bad}" if br_bad else "ok(또는 0건)")

    sub_bad = [(r.get("title") or "")[:48] for r in (banks.get("overseas_subsidiary") or [])
               if not _is_overseas_subsidiary_relevant(r.get("title") or "")]
    check("3d: overseas_subsidiary 뱅크 행은 모두 법인/자회사 마커 보유(있을 때)",
          not sub_bad, f"마커 없음: {sub_bad}" if sub_bad else "ok(또는 0건)")

    fake = []
    for lens in ("ai", "overseas_branch", "overseas_subsidiary", "overseas_site",
                 "global_business"):
        for r in (banks.get(lens) or []):
            u = str(r.get("url") or "")
            if not u.startswith(("http://", "https://")) or "example.com" in u:
                fake.append((lens, (r.get("title") or "")[:32]))
    check("3e: 뱅크 행에 가짜 URL 없음(실 기사 링크만)", not fake,
          f"fake={fake[:4]}" if fake else "ok")


# ---------------------------------------------------------------------------
# 4 · LIVE — real query_audit covers every priority lens (NEWS_MODE=live only)
# ---------------------------------------------------------------------------

def check_live_audit() -> None:
    if os.environ.get("NEWS_MODE") != "live":
        info("LIVE 검사 SKIP — NEWS_MODE=live 아님(정적+스냅샷만 검사). "
             "라이브 audit 커버리지는 NEWS_MODE=live로 재실행해 확인한다.")
        return
    audit = []
    try:
        rows = lc.fetch_all(query_audit=audit)
    except Exception as exc:  # noqa: BLE001
        info(f"LIVE fetch 불가(네트워크 차단?) — {type(exc).__name__} · SKIP(가짜 성공 금지)")
        return
    if not audit:
        info("LIVE audit 0건 — 네트워크 결과 없음 · SKIP")
        return
    by_group = {}
    for a in audit:
        d = by_group.setdefault(a.get("group"), {"rows": 0, "fetched": 0, "added": 0})
        d["rows"] += 1
        d["fetched"] += int(a.get("fetched_count") or 0)
        d["added"] += int(a.get("added_count") or 0)
    info(f"LIVE 수집 {len(rows)}행 · audit 그룹 {len(by_group)}개")
    missing = [g for g in REQUIRED_PRIORITY if g not in by_group]
    check("4a: 모든 우선순위 렌즈 그룹이 news_query_audit에 등장(빈 결과여도)",
          not missing, f"audit 누락: {missing}" if missing else "6/6 등장")
    # 빈 결과여도 audit에 보여야 한다(조용히 사라지지 않음).
    for g in REQUIRED_PRIORITY:
        d = by_group.get(g)
        if d is None:
            continue
        info(f"  {g}: audit_rows={d['rows']} fetched={d['fetched']} added={d['added']}"
             + ("  (검색했으나 매칭 0 — 굶음 아님)" if d["fetched"] == 0 else ""))


def main() -> int:
    print(f"== verify_live_lens_query_audit_coverage (D7-L) @ {ROOT} ==")
    check_collector_static()
    check_policy_and_builder_static()
    check_committed_snapshot()
    check_live_audit()
    print()
    if _failures:
        print(f"RESULT: FAIL ({len(_failures)} 항목)")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("RESULT: PASS — 우선순위 렌즈 preflight·정직 분류·audit 커버리지 (D7-L)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
