"""D4-A verifier — deterministic editorial surface contracts."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "data" / "golden_editorial_cases.json"
BRIEFING = ROOT / "app" / "briefing.py"
sys.path.insert(0, str(ROOT))

from app import surface_contracts  # noqa: E402

_failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    line = f"[{'PASS' if ok else 'FAIL'}] {name}"
    if detail:
        line += f" — {detail}"
    print(line)
    if not ok:
        _failures.append(name)
    return ok


def _load_cases() -> list[dict]:
    data = json.loads(CASES.read_text(encoding="utf-8"))
    return data.get("cases") or []


def check_golden_cases() -> None:
    cases = _load_cases()
    check("golden cases loaded", len(cases) >= 7, str(len(cases)))
    for case in cases:
        cid = case.get("id") or "<missing>"
        expected = case.get("expected") or {}
        decision = surface_contracts.decide_ai_tab(case.get("article") or {})
        want_ai = bool(expected.get("ai_tab"))
        check(f"{cid}: expected.ai_tab == {want_ai}",
              decision.eligible == want_ai,
              f"got={decision.eligible} reason={decision.reason_code}")
        check(f"{cid}: reason_code present", bool(decision.reason_code))
        if want_ai:
            check(f"{cid}: positive cases pass",
                  decision.reason_code.startswith("ai_tab.accept"),
                  decision.reason_code)
        else:
            check(f"{cid}: false cases have ai_tab.reject reason",
                  decision.reason_code.startswith("ai_tab.reject"),
                  decision.reason_code)
        if expected.get("operator_reference"):
            check(f"{cid}: operator/reference routed",
                  not decision.eligible and decision.severity == "review",
                  f"eligible={decision.eligible} severity={decision.severity}")


def check_mixed_business_contract() -> None:
    """D4-D: a mixed urban-redevelopment/business title needs an AI/DC
    execution-primary phrase — a bare 데이터센터 side mention is not enough."""
    by_id = {case.get("id"): case for case in _load_cases()}

    mixed = by_id.get("hdec_urban_redevelopment_dc_mixed_noise")
    check("D4D: mixed 도시정비/DC golden case present", mixed is not None)
    if mixed:
        dec = surface_contracts.decide_ai_tab(mixed.get("article") or {})
        check("D4D: mixed 도시정비/DC title rejected from AI tab",
              not dec.eligible, f"eligible={dec.eligible}")
        check("D4D: reject reason_code == ai_tab.reject.mixed_business_title_not_ai_primary",
              dec.reason_code == "ai_tab.reject.mixed_business_title_not_ai_primary",
              dec.reason_code)
        check("D4D: mixed case routed to operator reference (severity=review)",
              dec.severity == "review", dec.severity)

    for pid in (
            "dc_epc_power_cooling_positive",
            "ai_infra_hightech_epc_positive",
            "smart_construction_robot_positive"):
        case = by_id.get(pid)
        check(f"D4D: positive golden case present — {pid}", case is not None)
        if case:
            dec = surface_contracts.decide_ai_tab(case.get("article") or {})
            check(f"D4D: {pid} stays AI-tab eligible",
                  dec.eligible and dec.reason_code.startswith("ai_tab.accept"),
                  f"eligible={dec.eligible} reason={dec.reason_code}")


def check_briefing_callsite() -> None:
    src = BRIEFING.read_text(encoding="utf-8")
    check("briefing imports surface_contracts", "surface_contracts" in src)
    check("briefing AI tab uses surface_contracts.decide_ai_tab",
          "surface_contracts.decide_ai_tab(row).eligible" in src)


def main() -> int:
    print(f"== verify_surface_contracts @ {ROOT} ==")
    check_golden_cases()
    check_mixed_business_contract()
    check_briefing_callsite()
    if _failures:
        print(f"\nRESULT: FAIL ({len(_failures)} failed)")
        for name in _failures:
            print(f"  - {name}")
        return 1
    print("\nRESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
