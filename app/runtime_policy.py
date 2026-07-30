"""Conservative shadow policy for the D7-AK-6F runtime.

The policy intentionally separates *topic relevance* from *delivery urgency*.
Keyword co-occurrence alone can place an article in an hourly digest, but cannot
promote it to immediate or priority delivery. P0/P1 require explicit event evidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from app.runtime_models import (
    DecisionClass,
    PolicyDecision,
    clean_text,
    deterministic_id,
)

POLICY_VERSION = "d7-ak-6f-c1-shadow-v1"

_AI_TERMS = (
    "인공지능",
    "생성형 ai",
    "생성 ai",
    "ai ",
    " ai",
    "ai·",
    "ai-",
    "artificial intelligence",
    "데이터센터",
    "data center",
    "gpu",
    "피지컬 ai",
)
_POWER_INFRA_TERMS = (
    "전력",
    "원전",
    "smr",
    "변전",
    "송전",
    "전력망",
    "냉각",
    "용수",
    "데이터센터",
)
_HDEC_DIRECT_TERMS = (
    "현대건설",
    "hyundai e&c",
)
_COMPETITOR_TERMS = (
    "삼성물산",
    "대우건설",
    "gs건설",
    "dl이앤씨",
    "포스코이앤씨",
    "sk에코플랜트",
    "한미글로벌",
)
_CONFIRMED_ACTION_TERMS = (
    "수주",
    "계약 체결",
    "본계약",
    "착공",
    "준공",
    "승인",
    "최종 확정",
    "공식 발표",
    "선정됐다",
    "선정되었다",
    "출시했다",
    "투자한다",
    "투자 확정",
    "인수한다",
    "협약 체결",
    "mou 체결",
    "법안 통과",
    "시행령 확정",
    "예산 확정",
)
_HIGH_IMPACT_TERMS = (
    "조원",
    "兆",
    "대규모",
    "국가 전략",
    "국가전략",
    "규제",
    "수출 통제",
    "공급망",
    "원전",
    "smr",
    "데이터센터",
    "전력망",
)
_P0_ADVERSE_TERMS = (
    "사고",
    "붕괴",
    "중단",
    "제재",
    "입찰 제한",
    "영업정지",
    "압수수색",
    "사망",
    "화재",
)
_SPECULATION_TERMS = (
    "전망",
    "가능성",
    "관측",
    "예상",
    "논의 전망",
    "검토 중",
    "할 수도",
)
_NON_NEWS_TERMS = (
    "채용",
    "인재 찾",
    "서평",
    "신간",
    "사용기",
    "리뷰",
    "체험기",
)
_INTERVIEW_ANALYSIS_TERMS = (
    "인터뷰",
    "리포트",
    "분석",
    "전망",
    "묻다",
    "칼럼",
)
_AMOUNT_RE = re.compile(r"(?:\d[\d,.]*\s*(?:조|억|만)\s*원|\d[\d,.]*\s*(?:billion|million))", re.I)


@dataclass(frozen=True)
class PolicyInput:
    event_cluster_key: str
    material_signature: str
    title: str
    summary: str
    source: str
    article_id: str
    published_at: str
    confirmed_event_types: tuple[str, ...] = ()
    explicit_evidence: tuple[str, ...] = ()
    attributes: Mapping[str, Any] | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "PolicyInput":
        confirmed = value.get("confirmed_event_types") or value.get(
            "shadow_confirmed_event_types"
        ) or ()
        if isinstance(confirmed, str):
            confirmed = (confirmed,)
        evidence = value.get("explicit_evidence") or ()
        if isinstance(evidence, str):
            evidence = (evidence,)
        return cls(
            event_cluster_key=clean_text(
                value.get("event_cluster_key")
                or value.get("cluster_key")
                or value.get("article_id")
                or value.get("article_key")
            ),
            material_signature=clean_text(
                value.get("material_signature")
                or value.get("content_signature")
                or value.get("article_id")
                or value.get("article_key")
            ),
            title=clean_text(value.get("title")),
            summary=clean_text(value.get("summary") or value.get("snippet")),
            source=clean_text(value.get("source")),
            article_id=clean_text(
                value.get("article_id") or value.get("article_key") or value.get("id")
            ),
            published_at=clean_text(
                value.get("published_at") or value.get("published_kst")
            ),
            confirmed_event_types=tuple(
                clean_text(item).lower() for item in confirmed if clean_text(item)
            ),
            explicit_evidence=tuple(
                clean_text(item) for item in evidence if clean_text(item)
            ),
            attributes=value,
        )


@dataclass(frozen=True)
class _Signals:
    ai_core: bool
    ai_contextual: bool
    infrastructure: bool
    hdec_direct: bool
    competitor: bool
    confirmed_action: bool
    explicit_confirmation: bool
    high_impact: bool
    adverse: bool
    speculation: bool
    non_news: bool
    analysis_style: bool
    amount_present: bool


class RuntimePolicyEngine:
    def __init__(self, policy_version: str = POLICY_VERSION) -> None:
        self.policy_version = clean_text(policy_version) or POLICY_VERSION

    def decide(self, value: PolicyInput | Mapping[str, Any]) -> PolicyDecision:
        item = value if isinstance(value, PolicyInput) else PolicyInput.from_mapping(value)
        if not item.event_cluster_key or not item.material_signature or not item.title:
            raise ValueError("policy input requires event_cluster_key, material_signature, and title")

        text = f"{item.title} {item.summary}".lower()
        title = item.title.lower()
        signals = self._signals(item, text, title)
        reasons: list[str] = []
        topic_key = self._topic(signals)

        if signals.non_news:
            decision_class = DecisionClass.REJECT
            delivery_class = "none"
            should_enqueue = False
            confidence = 0.98
            reasons.append("non_news_or_promotional_content")

        elif signals.speculation and not signals.explicit_confirmation:
            decision_class = DecisionClass.REJECT
            delivery_class = "none"
            should_enqueue = False
            confidence = 0.93
            reasons.append("speculation_without_source_evidence")

        elif not signals.ai_core and signals.ai_contextual and (signals.hdec_direct or signals.competitor):
            decision_class = DecisionClass.P3
            delivery_class = "dashboard_only"
            should_enqueue = False
            confidence = 0.80
            reasons.append("incidental_ai_context_retained_for_dashboard_only")

        elif not signals.ai_core:
            decision_class = DecisionClass.REJECT
            delivery_class = "none"
            should_enqueue = False
            confidence = 0.95
            reasons.append("ai_not_a_core_or_material_context")

        elif signals.hdec_direct and signals.confirmed_action and signals.adverse:
            decision_class = DecisionClass.P0
            delivery_class = "immediate"
            should_enqueue = True
            confidence = 0.96
            reasons.extend(("hdec_direct_impact", "confirmed_adverse_event"))

        elif signals.hdec_direct and signals.confirmed_action:
            decision_class = DecisionClass.P0
            delivery_class = "immediate"
            should_enqueue = True
            confidence = 0.94
            reasons.extend(("hdec_direct_impact", "confirmed_material_action"))

        elif signals.confirmed_action and signals.explicit_confirmation and signals.high_impact:
            decision_class = DecisionClass.P1
            delivery_class = "priority_digest"
            should_enqueue = True
            confidence = 0.90
            reasons.extend(("source_evidenced_action", "executive_scale_or_infrastructure_impact"))

        elif signals.competitor and signals.confirmed_action and signals.explicit_confirmation:
            decision_class = DecisionClass.P1
            delivery_class = "priority_digest"
            should_enqueue = True
            confidence = 0.88
            reasons.extend(("confirmed_competitor_action", "hdec_competitive_relevance"))

        elif signals.infrastructure or signals.competitor or signals.high_impact:
            decision_class = DecisionClass.P2
            delivery_class = "hourly_digest"
            should_enqueue = True
            confidence = 0.78 if signals.analysis_style else 0.82
            reasons.append("strategic_context_without_immediate_delivery_evidence")
            if signals.analysis_style:
                reasons.append("analysis_or_interview_not_promoted_to_priority")

        else:
            decision_class = DecisionClass.P3
            delivery_class = "dashboard_only"
            should_enqueue = False
            confidence = 0.72
            reasons.append("relevant_but_not_delivery_worthy")

        decision_id = deterministic_id(
            "decision",
            self.policy_version,
            item.event_cluster_key,
            item.material_signature,
        )
        evidence = {
            "signals": {
                key: bool(value)
                for key, value in signals.__dict__.items()
            },
            "explicit_evidence": list(item.explicit_evidence),
            "confirmed_event_types": list(item.confirmed_event_types),
            "source": item.source,
            "article_id": item.article_id,
            "published_at": item.published_at,
        }
        return PolicyDecision(
            decision_id=decision_id,
            event_cluster_key=item.event_cluster_key,
            policy_version=self.policy_version,
            decision_class=decision_class,
            topic_key=topic_key,
            confidence=confidence,
            should_enqueue=should_enqueue,
            delivery_class=delivery_class,
            reasons=tuple(reasons),
            evidence=evidence,
        )

    @staticmethod
    def _signals(item: PolicyInput, text: str, title: str) -> _Signals:
        ai_concepts = (
            "인공지능" in text,
            bool(re.search(r"(?:^|[^a-z0-9])ai(?:[^a-z0-9]|$)", text, re.I)),
            "데이터센터" in text or "data center" in text,
            "gpu" in text,
            "피지컬 ai" in text,
        )
        ai_term_count = sum(1 for present in ai_concepts if present)
        title_ai = (
            "인공지능" in title
            or bool(re.search(r"(?:^|[^a-z0-9])ai(?:[^a-z0-9]|$)", title, re.I))
            or "데이터센터" in title
            or "data center" in title
            or "gpu" in title
            or "피지컬 ai" in title
        )
        infrastructure = any(term in text for term in _POWER_INFRA_TERMS)
        hdec_direct = any(term in text for term in _HDEC_DIRECT_TERMS)
        competitor = any(term in text for term in _COMPETITOR_TERMS)
        confirmed_action = any(term in text for term in _CONFIRMED_ACTION_TERMS)
        explicit_confirmation = bool(item.explicit_evidence) or any(
            token in {
                "contract_confirmed",
                "investment_confirmed",
                "policy_confirmed",
                "approval_confirmed",
                "award_confirmed",
                "incident_confirmed",
            }
            for token in item.confirmed_event_types
        )
        high_impact = any(term in text for term in _HIGH_IMPACT_TERMS)
        adverse = any(term in text for term in _P0_ADVERSE_TERMS)
        speculation = any(term in text for term in _SPECULATION_TERMS)
        non_news = any(term in text for term in _NON_NEWS_TERMS)
        analysis_style = any(term in title for term in _INTERVIEW_ANALYSIS_TERMS)
        amount_present = bool(_AMOUNT_RE.search(text))

        # A single incidental AI mention in a long non-AI article does not qualify as core.
        # Infrastructure context is allowed into P2 only when AI appears in the title or at
        # least two AI/infrastructure anchors appear in the article text.
        ai_core = title_ai or ai_term_count >= 2 or (
            ai_term_count >= 1 and infrastructure and (hdec_direct or competitor or high_impact)
        )

        # Existing classifier labels are not accepted as proof. A promotion to P0/P1 needs
        # both an action phrase and independent source evidence/event metadata.
        if explicit_confirmation and not confirmed_action:
            confirmed_action = any(
                token in {
                    "contract_confirmed",
                    "policy_confirmed",
                    "approval_confirmed",
                    "award_confirmed",
                    "incident_confirmed",
                }
                for token in item.confirmed_event_types
            )

        if amount_present:
            high_impact = True

        return _Signals(
            ai_core=ai_core,
            ai_contextual=ai_term_count >= 1,
            infrastructure=infrastructure,
            hdec_direct=hdec_direct,
            competitor=competitor,
            confirmed_action=confirmed_action,
            explicit_confirmation=explicit_confirmation,
            high_impact=high_impact,
            adverse=adverse,
            speculation=speculation,
            non_news=non_news,
            analysis_style=analysis_style,
            amount_present=amount_present,
        )

    @staticmethod
    def _topic(signals: _Signals) -> str:
        if signals.hdec_direct:
            return "hdec_direct"
        if signals.competitor:
            return "hdec_competitor"
        if signals.infrastructure:
            return "ai_infrastructure"
        if signals.ai_core:
            return "ai_strategy"
        return "none"


def decision_summary(decisions: Sequence[PolicyDecision]) -> dict[str, int]:
    result = {item.value: 0 for item in DecisionClass}
    for decision in decisions:
        result[decision.decision_class.value] += 1
    return result
