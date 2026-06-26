#!/usr/bin/env python3
"""Verify AI value-chain coverage and HDEC relevance ranking.

Default mode is fully offline. Optional --live-diagnostic runs public Google RSS
probes and prints classifications without failing on empty/transient news.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from app import ai_value_chain, live_collector  # noqa: E402
import build_static_dashboard as bsd  # noqa: E402

DASHBOARD = ROOT / "docs" / "daily" / "dashboard-latest.html"
POLICY = ROOT / "data" / "ai_value_chain_policy.json"
ENGINE = ROOT / "app" / "ai_value_chain.py"

# Engine/policy separation (P0-D7-S2): domain keywords must live in the policy
# JSON, not in app/ai_value_chain.py. These are the groups/layers/lens-mappings
# the engine and downstream surfaces depend on.
REQUIRED_TERM_GROUPS = (
    "hdec_terms", "hyperscaler_terms", "chip_terms", "custom_chip_terms",
    "datacenter_terms", "power_terms", "cooling_terms", "construction_terms",
    "energy_plant_terms", "cable_terms", "semiconductor_cluster_terms",
    "developer_terms", "developer_lens_terms", "trust_terms", "pipeline_terms",
    "smart_construction_terms", "generic_ai_terms", "stock_noise_terms",
    "housing_terms", "global_terms",
)
REQUIRED_LAYERS = (
    "hyperscaler_model", "custom_ai_chip", "ai_semiconductor_supply",
    "ai_datacenter_power", "ai_datacenter_cooling", "ai_datacenter_construction",
    "semiconductor_cluster_infra", "development_finance", "smart_construction_ai",
    "generic_ai", "irrelevant",
)
# Layers that surface AI value-chain items must declare a base dashboard lens.
REQUIRED_LENS_MAPPINGS = (
    "custom_ai_chip", "ai_semiconductor_supply", "ai_datacenter_power",
    "ai_datacenter_cooling", "ai_datacenter_construction",
    "semiconductor_cluster_infra", "development_finance", "smart_construction_ai",
)

_failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    line = f"[{'PASS' if ok else 'FAIL'}] {name}"
    if detail:
        line += f" — {detail}"
    print(line)
    if not ok:
        _failures.append(name)
    return ok


def _row(title: str, snippet: str = "", source: str = "") -> dict:
    return {
        "title": title,
        "snippet": snippet,
        "source": source,
        "url": "https://example.com/article",
        "category": "general",
        "category_label": "",
        "radar_section": "other",
    }


def _lenses(title: str, snippet: str = "", source: str = "") -> set[str]:
    return set(bsd._lens_for(_row(title, snippet, source)))


@dataclass(frozen=True)
class Case:
    title: str
    layer: str
    tiers: tuple[int, ...]
    must_lens: tuple[str, ...] = ()
    must_not_lens: tuple[str, ...] = ()
    is_ai: bool | None = None
    dashboard: bool | None = None


CASES = (
    Case("OpenAI와 Broadcom, 자체 AI 칩 공개",
         ai_value_chain.LAYER_CUSTOM_CHIP, (3,), ("ai",), is_ai=True, dashboard=True),
    Case("오픈AI, 5000억달러 AI 데이터센터 추진…전력 우선 입지 전략",
         ai_value_chain.LAYER_DC_POWER, (2,), ("ai", "new_energy", "global_business"), is_ai=True, dashboard=True),
    Case("앤트로픽, 데이터센터 임대료로 스페이스X 시설 임대",
         ai_value_chain.LAYER_DC_CONSTRUCTION, (2,), ("ai", "global_business"), is_ai=True, dashboard=True),
    Case("마이크론, 앤트로픽과 AI 인프라 구축 협약…메모리·스토리지 공급",
         ai_value_chain.LAYER_SEMI_SUPPLY, (3,), ("ai",), is_ai=True, dashboard=True),
    Case("메타, 1.6GW AI 데이터센터 전력 계약",
         ai_value_chain.LAYER_DC_POWER, (2,), ("ai", "new_energy"), is_ai=True, dashboard=True),
    Case("오라클, AI 데이터센터 확장 자금 조달",
         ai_value_chain.LAYER_DC_CONSTRUCTION, (2, 3), ("ai",), is_ai=True, dashboard=True),
    Case("오픈AI 챗봇 앱 기능 업데이트",
         ai_value_chain.LAYER_GENERIC_AI, (5,), ("ai",), is_ai=True, dashboard=False),
    Case("AI 칩 직접 만드는 빅테크…오픈AI 할라페뇨",
         ai_value_chain.LAYER_CUSTOM_CHIP, (3,), ("ai",), is_ai=True, dashboard=True),
    Case("반도체 클러스터 전력 인프라 확충",
         ai_value_chain.LAYER_SEMI_CLUSTER, (3,), ("ai", "new_energy", "civil_infrastructure"), is_ai=True, dashboard=True),
    Case("부동산 시행사 PF 부실 확산",
         ai_value_chain.LAYER_DEV_FINANCE, (4,), ("developers", "development_business"), ("ai",), is_ai=False, dashboard=True),
    Case("신탁사 책임준공 리스크 확대",
         ai_value_chain.LAYER_DEV_FINANCE, (4,), ("trust_companies", "development_business"), ("ai",), is_ai=False, dashboard=True),
    Case("한국토지신탁 개발사업 정상화",
         ai_value_chain.LAYER_DEV_FINANCE, (4,), ("trust_companies", "development_business"), ("ai", "developers"), is_ai=False, dashboard=True),
    Case("롯데칠성 게토레이 신제품",
         ai_value_chain.LAYER_IRRELEVANT, (5,), (), ("ai", "developers", "trust_companies", "development_business"), is_ai=False, dashboard=False),
    Case("디에이치 아파트 AI 주차로봇 도입",
         ai_value_chain.LAYER_SMART_CONSTRUCTION, (1,), ("building_housing", "ai"), is_ai=True, dashboard=True),
)


def _load_model() -> dict:
    if not DASHBOARD.exists():
        return {}
    html = DASHBOARD.read_text(encoding="utf-8", errors="ignore")
    m = re.search(r'<script type="application/json" id="preview-model">\s*(.*?)\s*</script>',
                  html, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return {}


def _row_lenses(row: dict) -> list[str]:
    lenses = row.get("lens") or row.get("lenses") or row.get("lens_keys") or []
    if isinstance(lenses, str):
        return [lenses] if lenses.strip() else []
    return list(lenses or [])


def verify_cases() -> None:
    for case in CASES:
        cls = ai_value_chain.classify_ai_value_chain(case.title)
        lenses = _lenses(case.title)
        if case.dashboard is False:
            # Generic AI may be classified as AI value-chain, but must not enter
            # dashboard rows unless another real construction/infrastructure lens exists.
            ok_lens = not bsd._has_dashboard_lens(_row(case.title))
        else:
            ok_lens = set(case.must_lens) <= lenses
        checks = [
            cls["ai_value_chain_layer"] == case.layer,
            int(cls["hdec_relevance_tier"]) in case.tiers,
            ok_lens,
            not (set(case.must_not_lens) & lenses),
        ]
        if case.is_ai is not None:
            checks.append(cls["is_ai_value_chain"] is case.is_ai)
        detail = (f"layer={cls['ai_value_chain_layer']} tier={cls['hdec_relevance_tier']} "
                  f"is_ai={cls['is_ai_value_chain']} lens={sorted(lenses)} reason={cls['reason']}")
        check(f"case: {case.title}", all(checks), detail)


def verify_sorting() -> None:
    samples = [
        ai_value_chain.classify_ai_value_chain("현대건설, AI 데이터센터 EPC 수주"),
        ai_value_chain.classify_ai_value_chain("메타, 1.6GW AI 데이터센터 전력 계약"),
        ai_value_chain.classify_ai_value_chain("OpenAI와 Broadcom, 자체 AI 칩 공개"),
        ai_value_chain.classify_ai_value_chain("부동산 시행사 PF 부실 확산"),
        ai_value_chain.classify_ai_value_chain("오픈AI 챗봇 앱 기능 업데이트"),
    ]
    ordered = sorted(samples, key=ai_value_chain.hdec_relevance_sort_key)
    tiers = [x["hdec_relevance_tier"] for x in ordered]
    check("tier ordering: 1 < 2 < 3 < 4 < 5",
          tiers == [1, 2, 3, 4, 5], f"tiers={tiers}")


def verify_dashboard_model() -> None:
    model = _load_model()
    if not check("dashboard model present", bool(model)):
        return
    emitted = set()
    empty = []
    has_all = []
    invalid = []
    valid = set(bsd.VALID_LENS)
    for bucket in ("news_rows", "ai_rows"):
        for i, row in enumerate(model.get(bucket) or []):
            lenses = _row_lenses(row)
            if not lenses:
                empty.append((bucket, i, row.get("title")))
            if "all" in lenses:
                has_all.append((bucket, i, row.get("title")))
            emitted.update(lenses)
    for lens, rows in (model.get("lens_banks") or {}).items():
        for i, row in enumerate(rows or []):
            lenses = _row_lenses(row)
            if not lenses:
                empty.append((f"lens_banks:{lens}", i, row.get("title")))
            if "all" in lenses:
                has_all.append((f"lens_banks:{lens}", i, row.get("title")))
            emitted.update(lenses)
    invalid = sorted(emitted - valid)
    check("dashboard has no empty lens rows", not empty, f"empty={empty[:5]}" if empty else "ok")
    check("all emitted lens keys valid", not invalid, f"invalid={invalid}" if invalid else "ok")
    check("'all' is not emitted as a row lens", not has_all, f"rows={has_all[:5]}" if has_all else "ok")


def _load_policy() -> dict:
    try:
        return json.loads(POLICY.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def verify_policy() -> None:
    """Policy is externalized to data/ai_value_chain_policy.json (engine/policy split)."""
    if not check("policy JSON exists", POLICY.exists(), str(POLICY)):
        return
    policy = _load_policy()
    if not check("policy JSON parses to an object", isinstance(policy, dict)):
        return

    groups = policy.get("term_groups")
    if check("term_groups is an object", isinstance(groups, dict)):
        missing = [g for g in REQUIRED_TERM_GROUPS if g not in groups]
        check("all required term groups exist", not missing,
              f"missing={missing}" if missing else f"{len(REQUIRED_TERM_GROUPS)} groups present")
        empty = [g for g in REQUIRED_TERM_GROUPS
                 if not (isinstance(groups.get(g), list) and any(
                     isinstance(t, str) and t.strip() for t in groups.get(g)))]
        check("required term groups are non-empty string lists", not empty,
              f"empty/invalid={empty}" if empty else "ok")

    layers = policy.get("layers")
    layer_set = set(layers) if isinstance(layers, list) else set()
    missing_layers = [l for l in REQUIRED_LAYERS if l not in layer_set]
    check("all required layers declared", not missing_layers,
          f"missing={missing_layers}" if missing_layers else f"{len(REQUIRED_LAYERS)} layers present")

    # Policy layer manifest must match the engine's LAYER_* constants exactly (no drift).
    engine_layers = {v for k, v in vars(ai_value_chain).items()
                     if k.startswith("LAYER_") and isinstance(v, str)}
    check("policy layers == engine LAYER_* constants", layer_set == engine_layers,
          f"policy-only={sorted(layer_set - engine_layers)} engine-only={sorted(engine_layers - layer_set)}")

    mapping = policy.get("lens_mapping")
    if check("lens_mapping is an object", isinstance(mapping, dict)):
        missing_map = [l for l in REQUIRED_LENS_MAPPINGS if l not in mapping]
        check("all required lens mappings exist", not missing_map,
              f"missing={missing_map}" if missing_map else "ok")
        valid = set(bsd.VALID_LENS)
        bad = {}
        for layer, lenses in mapping.items():
            if not isinstance(lenses, list):
                bad[layer] = "not-a-list"
                continue
            invalid = [x for x in lenses if x not in valid]
            if invalid:
                bad[layer] = invalid
        check("lens_mapping uses only valid dashboard lens keys", not bad,
              f"invalid={bad}" if bad else "all keys valid")
        # Required surfacing layers must map to a non-empty base lens set.
        empty_map = [l for l in REQUIRED_LENS_MAPPINGS
                     if not (isinstance(mapping.get(l), list) and mapping.get(l))]
        check("required lens mappings are non-empty", not empty_map,
              f"empty={empty_map}" if empty_map else "ok")


def _string_collection_literals(tree: ast.AST):
    """Yield (lineno, [strings]) for every tuple/list/set literal of string consts."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            strs = [e.value for e in node.elts
                    if isinstance(e, ast.Constant) and isinstance(e.value, str)]
            if strs:
                yield getattr(node, "lineno", "?"), strs


def verify_engine_no_embedded_terms() -> None:
    """app/ai_value_chain.py must not embed large hardcoded domain term tuples.

    Heuristic: a tuple/list/set literal with >=3 string elements where any element
    contains Hangul (a domain keyword smell), or any literal with >=6 strings, is a
    hardcoded keyword list and must be moved to the policy JSON. Small ASCII lens-key
    sets (e.g. {"civil_infrastructure", "plant"}) and constant references are allowed.
    """
    if not check("engine source exists", ENGINE.exists()):
        return
    tree = ast.parse(ENGINE.read_text(encoding="utf-8"))
    flagged = []
    for lineno, strs in _string_collection_literals(tree):
        has_hangul = any(any("가" <= ch <= "힣" for ch in s) for s in strs)
        if (len(strs) >= 3 and has_hangul) or (len(strs) >= 6):
            flagged.append((lineno, len(strs), strs[:4]))
    check("engine has no large hardcoded domain term tuples", not flagged,
          f"flagged={flagged[:5]}" if flagged else "engine is keyword-free (policy-driven)")


LIVE_QUERIES = (
    "OpenAI AI chip",
    "오픈AI 자체 AI 칩",
    "Anthropic Claude AI data center",
    "앤트로픽 클로드 AI 데이터센터",
    "Microsoft AI data center power",
    "하이퍼스케일러 AI 데이터센터 전력",
    "AI 데이터센터 전력 인프라",
    "부동산 시행사 PF",
    "신탁사 책임준공",
    "한국토지신탁 개발사업",
)


def live_diagnostic() -> None:
    print(f"== live AI value-chain diagnostic @ {ROOT} ==")
    cfg = {"hl": "ko", "gl": "KR", "ceid": "KR:ko"}
    collected_at = datetime.now(live_collector.KST).isoformat(timespec="seconds")
    for query in LIVE_QUERIES:
        try:
            url = live_collector._build_google_news_url(query, cfg)
            xml = live_collector._fetch(url, live_collector.DEFAULT_TIMEOUT)
            rows = live_collector._parse_items(xml, query, collected_at, 5)
        except Exception as exc:  # noqa: BLE001 - diagnostic only
            print(f"\nQUERY {query!r}: error={exc}")
            continue
        print(f"\nQUERY {query!r}: fetched_count={len(rows)}")
        for row in rows[:5]:
            cls = ai_value_chain.classify_ai_value_chain(
                row.get("title") or "", row.get("source") or "", row.get("snippet") or "")
            lenses = bsd._lens_for(row)
            bank_flags = {
                "ai": "ai" in lenses,
                "developers": "developers" in lenses,
                "trust_companies": "trust_companies" in lenses,
                "development_business": "development_business" in lenses,
            }
            print(f"- {row.get('title')}")
            print(f"  layer={cls['ai_value_chain_layer']} tier={cls['hdec_relevance_tier']} "
                  f"lenses={lenses} enters={bank_flags}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live-diagnostic", action="store_true",
                        help="Run informational Google News RSS probes; never hard-fails on empty news.")
    args = parser.parse_args()
    if args.live_diagnostic:
        live_diagnostic()
        return 0

    print(f"== verify_ai_value_chain_coverage @ {ROOT} ==")
    verify_policy()
    verify_engine_no_embedded_terms()
    verify_cases()
    verify_sorting()
    verify_dashboard_model()
    print()
    if _failures:
        print(f"RESULT: FAIL ({len(_failures)} 항목)")
        for failure in _failures:
            print(f"  - {failure}")
        return 1
    print("RESULT: PASS — AI value-chain coverage and HDEC relevance ranking verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
