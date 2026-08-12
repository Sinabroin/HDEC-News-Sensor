"""Static and adversarial contracts for isolated scheduled product gates."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = {
    "daily": ROOT / ".github/workflows/editorial-daily-brief.yml",
    "weekly": ROOT / ".github/workflows/editorial-weekly-ti.yml",
    "editor": ROOT / ".github/workflows/editorial-review-console.yml",
    "watch": ROOT / ".github/workflows/teams-ai-news-watch.yml",
    "refresh": ROOT / ".github/workflows/scheduled-live-refresh.yml",
}
BROAD_INTEGRATION_VERIFIER = "verify_r4_ops5_production_acceptance.py"


def workflow_text(product: str) -> str:
    return WORKFLOWS[product].read_text(encoding="utf-8")


def assert_no_broad_runtime_kill_switch() -> None:
    offenders = [
        product
        for product in WORKFLOWS
        if BROAD_INTEGRATION_VERIFIER in workflow_text(product)
    ]
    if offenders:
        raise AssertionError("broad integration verifier returned to: " + ",".join(offenders))


def assert_scoped_workflow(
    product: str,
    *,
    required: tuple[str, ...],
    forbidden: tuple[str, ...] = (),
) -> None:
    text = workflow_text(product)
    missing = [token for token in required if token not in text]
    present = [token for token in forbidden if token in text]
    if missing:
        raise AssertionError(f"{product} workflow missing: {missing}")
    if present:
        raise AssertionError(f"{product} workflow has cross-domain gates: {present}")


def injected_fault_result(product: str, broken_domain: str) -> None:
    """A broken fixture only fails the scoped gate that owns that fixture."""
    if broken_domain and broken_domain == product:
        raise AssertionError(f"injected {product}-only contract failure")
