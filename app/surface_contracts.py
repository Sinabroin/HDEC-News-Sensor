"""Deterministic editorial surface contracts.

AI-tab eligibility is delegated to the actor/event/infra/exclusion signal
engine. Article title/snippet values are classification inputs, never fixed
allowlist/denylist keys.
"""

from dataclasses import asdict, dataclass, field

from app import radar_signals


@dataclass
class SurfaceDecision:
    surface: str
    eligible: bool
    reason_code: str
    public_reason: str
    operator_note: str
    severity: str = "info"
    matched_terms: list[str] = field(default_factory=list)
    tier: int = 0
    operator_reference: bool = False


def _matched_signal_ids(assessment: dict) -> list[str]:
    return [
        f"{axis}:{group_id}"
        for axis, groups in (assessment.get("signals") or {}).items()
        for group_id in groups
    ]


def _decision_from_assessment(
    assessment: dict, *, surface: str, prefix: str
) -> SurfaceDecision:
    eligible = bool(assessment.get("eligible"))
    reason = assessment.get("reason") or "unknown"
    if eligible:
        reason_code = (
            f"{prefix}.accept.tier{assessment['tier']}"
            if prefix == "ai_tab.supplement"
            else f"{prefix}.accept.cross_axis.tier{assessment['tier']}"
        )
        public_reason = "AI 인프라 관련 주체·이벤트·인프라 교차 신호"
        operator_note = "actor/event/infra policy evidence"
        severity = "info"
    else:
        reason_code = f"{prefix}.reject.{reason}"
        public_reason = "AI 탭 교차 신호 기준 미충족"
        operator_note = (
            "operator/reference; "
            f"reason={reason}; "
            f"exclusions={','.join(assessment.get('blocked_exclusions') or []) or '-'}"
        )
        severity = "review"
    return SurfaceDecision(
        surface=surface,
        eligible=eligible,
        reason_code=reason_code,
        public_reason=public_reason,
        operator_note=operator_note,
        severity=severity,
        matched_terms=_matched_signal_ids(assessment),
        tier=int(assessment.get("tier") or 0),
        operator_reference=bool(assessment.get("operator_reference")),
    )


def decide_ai_tab(article: dict) -> SurfaceDecision:
    """Return AI-tab eligibility from actor/event/infra/exclusion signals."""
    return _decision_from_assessment(
        radar_signals.classify_ai_radar(article),
        surface="ai_tab",
        prefix="ai_tab",
    )


def decide_ai_supplement(article: dict) -> SurfaceDecision:
    """Evaluate a surfaced article against the stricter supplement infra set."""
    return _decision_from_assessment(
        radar_signals.classify_ai_radar(article, supplement=True),
        surface="ai_tab_supplement",
        prefix="ai_tab.supplement",
    )


def _not_migrated(surface: str) -> SurfaceDecision:
    return SurfaceDecision(
        surface=surface,
        eligible=True,
        reason_code=f"{surface}.not_migrated",
        public_reason="기존 표시 로직 유지",
        operator_note="surface contract placeholder; behavior not migrated",
    )


def decide_executive_top(article: dict) -> SurfaceDecision:
    return _not_migrated("executive_top")


def decide_risk_event_candidate(article_or_event: dict) -> SurfaceDecision:
    return _not_migrated("risk_event_candidate")


def decide_category_evidence(article: dict, category_key: str) -> SurfaceDecision:
    return _not_migrated(f"category_evidence.{category_key or 'unknown'}")


def summarize_surface_decisions(article: dict) -> dict:
    return {
        "ai_tab": asdict(decide_ai_tab(article)),
        "executive_top": asdict(decide_executive_top(article)),
        "risk_event_candidate": asdict(decide_risk_event_candidate(article)),
    }
