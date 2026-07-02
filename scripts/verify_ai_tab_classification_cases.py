#!/usr/bin/env python3
"""Verify the small signal-based AI-tab classification golden set.

The verifier calls the production extractor and surface contract. It does not
use full-title equality as an expected-result mechanism.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "ai_tab_classification_cases.json"
POLICY = ROOT / "data" / "radar_signal_policy.json"
sys.path.insert(0, str(ROOT))

from app import radar_signals, surface_contracts  # noqa: E402


_failures: list[str] = []
_AXES = ("actor", "event", "infra", "exclusion")
_CASE_TYPES = {"synthetic_regression", "captured_regression"}


def check(name: str, ok: bool, detail: str = "") -> bool:
    line = f"[{'PASS' if ok else 'FAIL'}] {name}"
    if detail:
        line += f" — {detail}"
    print(line)
    if not ok:
        _failures.append(name)
    return ok


def _load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        check(f"{path.name}: readable JSON", False, str(exc))
        return {}
    check(f"{path.name}: readable JSON", isinstance(value, dict))
    return value if isinstance(value, dict) else {}


def check_policy(policy: dict) -> None:
    axes = policy.get("axes") or {}
    check("policy exposes exactly four required axes", set(axes) == set(_AXES))
    check("production signal policy loaded", radar_signals.policy_loaded())
    decision = policy.get("decision") or {}
    exclusion_groups = set((axes.get("exclusion") or {}).keys())
    infra_groups = set((axes.get("infra") or {}).keys())
    check(
        "decision uses infra with actor/event support",
        decision.get("required_axis") == "infra"
        and set(decision.get("supporting_axes") or ()) == {"actor", "event"},
    )
    check(
        "decision references declared exclusion groups only",
        set(decision.get("hard_exclusions") or ()) <= exclusion_groups,
    )
    infra_refs = {
        group_id
        for key, values in decision.items()
        if key.endswith("_infra") and isinstance(values, list)
        for group_id in values
    }
    check(
        "decision references declared infra groups only",
        infra_refs <= infra_groups,
        f"unknown={sorted(infra_refs - infra_groups)}",
    )
    long_terms = []
    for axis, groups in axes.items():
        for group_id, spec in (groups or {}).items():
            terms = list(spec.get("any") or [])
            for clause in spec.get("all_of") or []:
                terms.extend(clause if isinstance(clause, list) else [])
            for term in terms:
                # Guard against policy drift into copied full headlines. Domain
                # phrases should remain compact signal vocabulary.
                if isinstance(term, str) and (len(term) > 24 or len(term.split()) > 5):
                    long_terms.append(f"{axis}.{group_id}:{term}")
    check(
        "policy contains compact signals, not full copied headlines",
        not long_terms,
        "; ".join(long_terms[:3]),
    )


def check_fixture_schema(data: dict) -> list[dict]:
    cases = data.get("cases") or []
    policy_axes = _load_policy_axes()
    max_cases = int(data.get("max_cases") or 0)
    check("golden set is non-empty", bool(cases))
    check(
        "golden set stays within declared small-set cap",
        0 < len(cases) <= max_cases <= 12,
        f"cases={len(cases)} max_cases={max_cases}",
    )
    ids = [case.get("id") for case in cases]
    check("case IDs are unique and non-empty", len(ids) == len(set(ids)) and all(ids))
    for case in cases:
        cid = case.get("id") or "<missing>"
        check(
            f"{cid}: valid case_type",
            case.get("case_type") in _CASE_TYPES,
            str(case.get("case_type")),
        )
        signals = case.get("signals")
        check(
            f"{cid}: all signal axes declared",
            isinstance(signals, dict) and set(signals) == set(_AXES),
        )
        if isinstance(signals, dict):
            unknown = {
                axis: sorted(
                    set(signals.get(axis) or ())
                    - set(policy_axes.get(axis, {}))
                )
                for axis in _AXES
            }
            check(
                f"{cid}: signal IDs exist in policy",
                not any(unknown.values()),
                str({axis: values for axis, values in unknown.items() if values}),
            )
        article = case.get("article")
        check(
            f"{cid}: article source/title/snippet declared",
            isinstance(article, dict)
            and all(key in article for key in ("source", "title", "snippet")),
        )
        expected = case.get("expected")
        check(
            f"{cid}: expected ai_tab/operator_reference declared",
            isinstance(expected, dict)
            and set(expected) == {"ai_tab", "operator_reference"}
            and all(isinstance(expected[key], bool) for key in expected),
        )
    return cases


def check_production_classification(cases: list[dict]) -> None:
    for case in cases:
        cid = case["id"]
        article = dict(case["article"])
        expected = case["expected"]
        extracted = radar_signals.extract_ai_radar_signals(article)["signals"]
        check(
            f"{cid}: production extractor returns declared signals",
            all(
                set(extracted[axis]) == set(case["signals"][axis])
                for axis in _AXES
            ),
            f"actual={extracted}",
        )

        decision = surface_contracts.decide_ai_tab(article)
        check(
            f"{cid}: production surface decision matches expected routing",
            decision.eligible == expected["ai_tab"]
            and decision.operator_reference
            == expected["operator_reference"],
            (
                f"eligible={decision.eligible} "
                f"operator_reference={decision.operator_reference} "
                f"reason={decision.reason_code}"
            ),
        )

        # A neutral edit changes the full title string while preserving the
        # article's semantic signals. Classification must therefore stay stable.
        edited = dict(article)
        edited["title"] = f"후속 보도: {article['title']} 관련 동향"
        edited_decision = surface_contracts.decide_ai_tab(edited)
        check(
            f"{cid}: neutral title edit preserves signal-based result",
            edited_decision.eligible == decision.eligible,
            (
                f"before={decision.reason_code} "
                f"after={edited_decision.reason_code}"
            ),
        )


def check_fixture_isolation() -> None:
    production_roots = (
        ROOT / "app",
        ROOT / "templates",
        ROOT / "data",
    )
    references = []
    fixture_name = FIXTURE.name
    for base in production_roots:
        for path in base.rglob("*"):
            if not path.is_file() or path == FIXTURE:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if fixture_name in text or "tests/fixtures" in text:
                references.append(str(path.relative_to(ROOT)))
    for path in (ROOT / "scripts").glob("*.py"):
        if path.name.startswith("verify_"):
            continue
        text = path.read_text(encoding="utf-8")
        if fixture_name in text or "tests/fixtures" in text:
            references.append(str(path.relative_to(ROOT)))
    check(
        "production/dashboard paths do not load regression fixtures",
        not references,
        ", ".join(references),
    )


def _load_policy_axes() -> dict:
    try:
        policy = json.loads(POLICY.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    axes = policy.get("axes")
    return axes if isinstance(axes, dict) else {}


def main() -> int:
    print(f"== verify_ai_tab_classification_cases @ {ROOT} ==")
    policy = _load_json(POLICY)
    fixture = _load_json(FIXTURE)
    check_policy(policy)
    cases = check_fixture_schema(fixture)
    check_production_classification(cases)
    check_fixture_isolation()
    if _failures:
        print(f"\nRESULT: FAIL ({len(_failures)} failed)")
        for name in _failures:
            print(f"  - {name}")
        return 1
    print("\nRESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
