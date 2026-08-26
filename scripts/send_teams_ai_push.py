#!/usr/bin/env python3
"""Article-level Teams AI production sender (D7-AK-6A · email_channel transport).

This entrypoint wires the already-verified leaves into the scheduled production
path — it adds delivery, and nothing else:

* ``app.teams_ai_push``    — Teams-only AI topic classification, importance
  mapping, one message per article (up to ten), and the per-article email body.
* ``app.teams_push_state`` — persistent dedup over article id / normalized URL /
  title fingerprint / event cluster, including material-update re-send.

Selection and dedup logic is reused, never re-implemented here. The only new
behaviour is: deliver one Teams channel email per eligible article, then record
success.

Production transport is ``email_channel``: exactly one email per eligible article
is sent to the Teams channel address (``TEAMS_CHANNEL_EMAIL``) over the verified
Gmail SMTP contract owned by ``scripts/send_email_alert.py`` (reused, not
duplicated). This is the official production transport, not a fallback. Several
articles are never merged into one digest. An article counts as delivered only on
an SMTP ``250 accepted`` response, and only delivered articles are recorded, so a
partial failure keeps delivered articles recorded and leaves failed ones
resendable on the next run.

Default execution is dry-run and performs zero network operations. A real send
requires all of GITHUB_ACTIONS=true, TEAMS_AI_PUSH_MODE=send,
APPROVE_TEAMS_AI_PUSH=true, complete Gmail SMTP credentials plus a Teams channel
address, and a ``live-delta`` artifact. Any other state fails closed before a
message is built.

D7-AK-6C — selection is no longer gated on the artifact-level ``shadow_alert_delta``
flag: important/top-priority AI articles are sent even when nothing is
shadow-confirmed (importance derives from the reused scoring/confirmed-event signals
per article).

D7-AK-6E R4-R6 — the per-run batch delivers 0-5 articles: zero eligible unsent
articles send zero, one-to-five send all of them, more than five send exactly
five and defer the rest. The cap resolves via ``--max-articles`` /
``TEAMS_AI_PUSH_MAX_ARTICLES``: absent → 5, configured 1-5 respected, >5
clamped to 5, zero/negative/malformed fail closed to 1. Same-event
multi-publisher clusters collapse to one representative (locked primary-ten
version preferred) before the sent-ledger filter and the batch cap.

The Teams Workflows webhook (``TEAMS_WORKFLOW_WEBHOOK_URL``) is a reserved,
currently-inactive optional transport: it is never a required condition and its
absence never fails a run. SMTP credentials, recipient/channel addresses, and
article URLs are never printed — logs carry only counts, a non-reversible article
reference hash, and an SMTP status category.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass, replace
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
for _path in (REPO_ROOT, SCRIPTS_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from app.teams_ai_push import (  # noqa: E402
    DEFAULT_TEAMS_BATCH_MAX,
    HARD_TEAMS_BATCH_MAX,
    IMPORTANCE_IMPORTANT,
    IMPORTANCE_OBSERVATION,
    SELECTION_MODE_FALLBACK,
    apply_major_media_first_gate,
    evaluate_teams_push_policy,
    publisher_delivery_priority,
    render_article_email,
    select_teams_push_from_artifact_with_audit,
)
from app import publisher_direct, source_priority  # noqa: E402
from app.teams_push_state import (  # noqa: E402
    InvalidTeamsPushState,
    article_identity,
    clear_held_record,
    evaluate_dedup,
    filter_unsent_candidates,
    load_state,
    mark_held_replaced_by_major,
    observe_held_specialist,
    persist_after_success,
    resolve_state_path,
    save_state,
)

# Reuse the single proven Gmail SMTP contract — never a second copy of the handshake.
from send_email_alert import (  # noqa: E402
    DeliveryTarget,
    _smtp_password,
    _valid_address,
    deliver_email_message,
)

WEBHOOK_ENV = "TEAMS_WORKFLOW_WEBHOOK_URL"
SMTP_USER_ENV = "GMAIL_SMTP_USER"
FROM_ENV = "ALERT_EMAIL_FROM"
TEAMS_CHANNEL_ENV = "TEAMS_CHANNEL_EMAIL"
DEFAULT_MODE = "dry_run"
SEND_MODE = "send"
APPROVAL_TRUE = {"1", "true", "yes", "approved"}
REJECTION_COUNTER_KEYS = (
    "not_ai_core",
    "ai_not_central_incidental_ai_mention",
    "ai_not_central_non_ai",
    "excluded_stock_market_title",
    "excluded_political_title",
    "excluded_real_estate_transaction_title",
    "excluded_civic_campaign_or_publicity_title",
    "excluded_stock_market_dominant",
    "excluded_fund_product_noise",
    "excluded_investor_market_commentary",
    "excluded_generic_industry_ai_tailwind",
    "excluded_uncommitted_plan_or_strategy",
    "excluded_roundup_multi_topic",
    "excluded_ai_incidental",
    "excluded_other_nonexecutive",
    "insufficient_hdec_relevance",
    "insufficient_importance",
    "freshness_failed",
    "carry_forward_excluded",
    "source_authority_failed",
    "shadow_blocked",
    "shadow_unavailable",
    "no_confirmed_event",
    "speculation_only",
    "excluded_opinion_content",
    "already_sent",
    "exact_duplicate",
    "duplicate_event",
    "malformed_required_field",
    "no_evidenced_delivery_category",
    "public_institution_not_promoted",
    "other_policy_reason",
)


class FailClosed(RuntimeError):
    """Abort before any network call. ``reason`` is a safe, value-free label."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _row_source_tier_counts(rows) -> dict[str, int]:
    """R4-R9A §11 — per-tier row counts for the Teams source-gate audit.

    ``specialist`` counts every specialist-holdback-lane publisher (tier
    specialist / trusted_other plus explicitly configured Teams specialists),
    matching the gate's own lane partition."""
    counts = {
        "primary_10": 0,
        "secondary_3": 0,
        "major_secondary": 0,
        "specialist": 0,
    }
    for row in rows:
        policy = source_priority.teams_delivery_source_policy(
            str(row.get("source") or row.get("display_source") or ""),
            publisher_direct.publisher_url(row),
        )
        tier = str(policy["tier"])
        if tier == "primary_10":
            counts["primary_10"] += 1
        elif tier == "secondary_3":
            counts["secondary_3"] += 1
        elif tier == "major_secondary":
            counts["major_secondary"] += 1
        elif (
            str(policy["teams_lane"])
            == source_priority.TEAMS_LANE_SPECIALIST_HOLDBACK
        ):
            counts["specialist"] += 1
    return counts


def _major_row_decision_traces(
    *,
    article_rows,
    policy_evaluations,
    verified_object_ids,
    candidates,
    baseline,
    gate_batch,
    records,
    major_only: bool = True,
) -> list[dict[str, Any]]:
    """Categorical decision trace for major rows or all policy-relevant rows.

    R4-R12 keeps the established major-lane trace. R4-OPS-10H additionally
    traces every verified AI-core / HDEC-relevant / importance-qualified row,
    so a specialist or public-lane final rejection cannot disappear into an
    aggregate bucket. Log-safe by construction: only the non-reversible
    article reference, publisher display name, and categorical decision codes
    — never a URL, title, or credential (rules.md §4)."""
    outcome_by_ref: dict[str, dict[str, Any]] = {}
    for record in records:
        outcome_by_ref.setdefault(str(record.get("article_ref")), record)
    stage_by_ref: dict[str, tuple[str, str]] = {}
    for item in gate_batch.rejected:
        stage_by_ref[article_ref(item.candidate.article)] = (
            "source_gate_rejected", item.gate.reason
        )
    for item in gate_batch.held:
        stage_by_ref[article_ref(item.candidate.article)] = (
            "specialist_held",
            item.holdback.holdback_reason if item.holdback else "holdback_active",
        )
    for item in gate_batch.deferred_major:
        stage_by_ref[article_ref(item.candidate.article)] = (
            "deferred_due_to_cap", "run_cap_reached"
        )
    for item in gate_batch.selected:
        stage_by_ref[article_ref(item.candidate.article)] = (
            "selected", item.gate.reason
        )
    dedup_by_ref = {
        article_ref(candidate.article): decision
        for candidate, decision in zip(candidates, baseline)
    }
    traces: list[dict[str, Any]] = []
    for row, evaluation in zip(article_rows, policy_evaluations):
        policy = source_priority.teams_delivery_source_policy(
            str(row.get("source") or row.get("display_source") or ""),
            publisher_direct.publisher_url(row),
        )
        tier = str(policy["tier"])
        routing = evaluation.public_routing
        promoted_official = bool(
            routing.is_public_lane and routing.teams_alert_eligible
        )
        major_lane = (
            tier in {"primary_10", "secondary_3", "major_secondary"}
            or promoted_official
        )
        policy_relevant = bool(
            id(row) in verified_object_ids
            and (
                evaluation.topic.eligible
                or evaluation.hdec_relevant
                or evaluation.importance.sendable
            )
        )
        if (major_only and not major_lane) or (
            not major_only and not policy_relevant
        ):
            continue
        ref = article_ref(row)
        stage, stage_reason = "", ""
        smtp_status = "no_request"
        selected = False
        if not evaluation.eligible:
            stage = "policy_rejected"
            stage_reason = evaluation.rejection_reason
        else:
            dedup_decision = dedup_by_ref.get(ref)
            if dedup_decision is not None and not dedup_decision.send_allowed:
                stage = "ledger_blocked"
                stage_reason = dedup_decision.reason
            else:
                stage, stage_reason = stage_by_ref.get(
                    ref, ("event_duplicate_collapsed", "same_event_representative_exists")
                )
        record = outcome_by_ref.get(ref)
        if record is not None:
            smtp_status = str(record.get("status") or "no_request")
            selected = record.get("outcome") in {
                "delivered", "failed", "dry_run_no_send"
            }
        if stage == "selected":
            selected = True
        stock = evaluation.stock_market
        traces.append({
            "article_ref": ref,
            "source": str(row.get("source") or row.get("display_source") or ""),
            "source_tier": tier,
            "teams_lane": str(policy["teams_lane"]),
            "publisher_rank": int(policy["publisher_rank"]),
            "verified": id(row) in verified_object_ids,
            "gate_class": "promoted_official" if promoted_official else tier,
            "major_lane": major_lane,
            "ai_core": bool(evaluation.topic.eligible),
            "ai_exclusion": str(evaluation.topic.exclusion_reason or ""),
            "hdec_relevant": bool(evaluation.hdec_relevant),
            "importance_sendable": bool(evaluation.importance.sendable),
            "importance_level": str(evaluation.importance.level or ""),
            "selection_tier": (
                "tier_2_strong_observation"
                if evaluation.importance.level == IMPORTANCE_OBSERVATION
                else "tier_1_executive_headline"
                if evaluation.importance.sendable
                else ""
            ),
            "semantic_class": str(
                evaluation.semantic_precision.semantic_class
                if evaluation.semantic_precision is not None
                else ""
            ),
            "semantic_reason": str(
                evaluation.semantic_precision.reason
                if evaluation.semantic_precision is not None
                else ""
            ),
            "delivery_category": str(evaluation.delivery_category or ""),
            "public_promotion_reason": str(
                evaluation.public_routing.promotion_reason or ""
            ),
            "confirmed_event": bool(row.get("shadow_confirmed_event_types")),
            "freshness_current": row.get("current_run_seen") is not False
            and row.get("carried_forward") is not True,
            "stock_market_excluded": bool(stock is not None and not stock.eligible),
            "stage": stage,
            "rejection_reason": stage_reason,
            "final_selected": selected and stage == "selected",
            "smtp_status": smtp_status,
        })
    return traces


@dataclass(frozen=True)
class EmailChannelCredentials:
    """Gmail SMTP + Teams channel address, resolved from secrets (never printed)."""

    smtp_user: str = ""
    smtp_password: str = ""
    from_address: str = ""
    teams_address: str = ""

    @property
    def complete(self) -> bool:
        return bool(
            self.smtp_user and self.smtp_password and self.from_address and self.teams_address
        )


def _true_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in APPROVAL_TRUE


def _resolve_max_articles(raw: str) -> tuple[int, str]:
    """Resolve the per-run batch cap (D7-AK-6E R4-R6): 0-5 articles per run.

    - absent value            -> DEFAULT_TEAMS_BATCH_MAX (5)
    - configured 1..5         -> respected as configured
    - configured > 5          -> clamped to HARD_TEAMS_BATCH_MAX (5)
    - zero/negative/malformed -> documented fail-closed floor of 1 (the sealed
      pre-batch production posture; misconfiguration never widens the batch)
    """
    text = str(raw or "").strip()
    if not text:
        return DEFAULT_TEAMS_BATCH_MAX, "default_5"
    try:
        value = int(text, 10)
    except ValueError:
        return 1, "fail_closed_floor_1"
    if value > HARD_TEAMS_BATCH_MAX:
        return HARD_TEAMS_BATCH_MAX, "clamped_to_hard_max_5"
    if value < 1:
        return 1, "fail_closed_floor_1"
    return value, "configured"


def resolve_email_channel_credentials() -> EmailChannelCredentials:
    """Resolve the email_channel credentials from env (secrets only).

    Addresses are validated with the same helpers the proven email sender uses and
    are never printed. Missing/invalid values yield empty fields so the caller fails
    closed before any message is built."""
    return EmailChannelCredentials(
        smtp_user=_valid_address(os.environ.get(SMTP_USER_ENV, "")),
        smtp_password=_smtp_password(),
        from_address=_valid_address(os.environ.get(FROM_ENV, "")),
        teams_address=_valid_address(os.environ.get(TEAMS_CHANNEL_ENV, "")),
    )


def resolve_webhook_url() -> str:
    """Reserved optional Teams Workflows webhook transport (currently inactive).

    Returned https-only for observability, but it is never a required condition and
    its absence never fails a run — the production transport is email_channel. This
    is a backend-only value: it is never printed to any log or artifact (rules.md §4)."""
    url = os.environ.get(WEBHOOK_ENV, "").strip()
    return url if url.lower().startswith("https://") else ""


def article_ref(article: object) -> str:
    """Stable, non-reversible article reference safe for operational logs."""
    identity = article_identity(article)
    basis = (
        identity["article_id"]
        or identity["normalized_url"]
        or identity["title_fingerprint"]
    )
    if not basis:
        return "unknown"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:12]


def load_artifact(path: Path) -> Mapping[str, Any]:
    if not path.exists():
        raise FailClosed("artifact_missing")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FailClosed("artifact_unreadable") from exc
    if not isinstance(payload, Mapping):
        raise FailClosed("artifact_root_not_object")
    source = " ".join(str(payload.get("source") or "").split())
    validated_brief = (
        payload.get("artifact_contract") == "HDEC_VALIDATED_EXECUTIVE_BRIEF_V1"
        and payload.get("news_data_mode") == "live"
        and payload.get("news_fallback_used") is not True
        and payload.get("collection_status")
        in {"LIVE_HEALTHY_WITH_ARTICLES", "LIVE_HEALTHY_NO_ELIGIBLE_ARTICLES"}
        and isinstance(payload.get("news_censor_display_articles"), list)
    )
    if source != "live-delta" and not validated_brief:
        raise FailClosed("artifact_not_live_delta")
    return payload


def check_send_preconditions(credentials: EmailChannelCredentials) -> None:
    """Every real-send requirement, evaluated before any message is built."""
    if os.environ.get("GITHUB_ACTIONS", "").strip().lower() != "true":
        raise FailClosed("not_github_actions")
    if not _true_env("APPROVE_TEAMS_AI_PUSH"):
        raise FailClosed("send_not_approved")
    if not credentials.smtp_user:
        raise FailClosed("smtp_user_missing")
    if not credentials.smtp_password:
        raise FailClosed("smtp_credential_missing")
    if not credentials.from_address:
        raise FailClosed("from_address_missing")
    if not credentials.teams_address:
        raise FailClosed("teams_channel_missing")


def send_article_email(
    candidate,
    *,
    alert_context: Mapping[str, Any],
    credentials: EmailChannelCredentials,
    detected_at: str,
    smtp_factory=None,
) -> tuple[bool, str, int | None]:
    """Deliver exactly one Teams channel email for one article → (ok, status, smtp_code).

    ``ok`` is True only on an SMTP ``250 accepted`` response. Neither the recipient
    address nor the article URL is returned or printed; ``status`` is a coarse
    category label. ``smtp_factory`` is injectable for offline verification."""
    subject, text_body, html_body = render_article_email(
        alert_context, candidate, detected_at=detected_at
    )
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = credentials.from_address
    message["To"] = credentials.teams_address
    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")

    target = DeliveryTarget(
        label="teams_channel",
        address=credentials.teams_address,
        recipient_kind="teams_channel",
    )
    result = deliver_email_message(
        message,
        target,
        credentials.smtp_user,
        credentials.smtp_password,
        credentials.from_address,
        smtp_factory=smtp_factory,
    )
    ok = result.smtp_status == "accepted"
    if ok:
        status = f"accepted_{result.smtp_code}"
    else:
        status = result.detail or f"rejected_{result.smtp_code}"
    return ok, status, result.smtp_code


def deliver(
    *,
    artifact_path: Path,
    state_path: Path,
    credentials: EmailChannelCredentials,
    should_send: bool,
    dashboard_url: str = "",
    report_url: str = "",
    detected_at: str = "",
    max_articles: int = DEFAULT_TEAMS_BATCH_MAX,
    now_iso_value: str = "",
    smtp_factory=None,
    preference_runtime=None,
) -> dict[str, Any]:
    """Select, dedup, gate by source priority, and deliver 0-5 emails per run.

    Zero eligible unsent articles send zero; up to five eligible send all of
    them; more than five send exactly five and defer the remainder for later
    runs. Filler is never invented. Each article is handled independently: one
    SMTP failure never skips the remaining articles, and only delivered
    (250 accepted) articles reach persistent state — failed ones stay
    resendable.

    D7-AK-6E R4-R9A — after the accepted-ledger filter, the major-media-first
    source gate selects only locked primary-ten / secondary-three publishers
    and promoted official institutions immediately. Specialist/trusted-other
    publishers are held (120-minute holdback, at most one exceptional
    fallback per run); neutral/low publishers are never selected. Held
    observations persist through the existing state path in send mode only —
    a dry run writes nothing."""
    payload = load_artifact(artifact_path)
    try:
        state = load_state(state_path)
    except InvalidTeamsPushState as exc:
        raise FailClosed("state_invalid") from exc

    raw_articles = (
        payload.get("articles")
        if payload.get("source") == "live-delta"
        else payload.get("news_censor_display_articles")
    ) or []
    article_rows = [row for row in raw_articles if isinstance(row, Mapping)]
    invalid_row_count = len(raw_articles) - len(article_rows)
    current_rows = [
        row for row in article_rows
        if row.get("carried_forward") is not True
        and row.get("teams_newness_eligible") is not False
        and row.get("current_run_seen") is not False
    ]
    verified_rows = [
        row for row in current_rows
        if row.get("source_quality_passed") is not False
        and publisher_direct.assess_delivery_eligibility(
            row, relevance_qualified=True
        ).eligible
    ]
    validated_brief = payload.get("source") != "live-delta"
    policy_evaluations = [
        evaluate_teams_push_policy(
            row,
            require_validated_fields=validated_brief,
        )
        for row in article_rows
    ]
    verified_object_ids = {id(row) for row in verified_rows}
    verified_evaluations = [
        evaluation
        for row, evaluation in zip(article_rows, policy_evaluations)
        if id(row) in verified_object_ids
    ]
    ai_core = sum(evaluation.topic.eligible for evaluation in verified_evaluations)
    hdec_relevant = sum(
        evaluation.topic.eligible and evaluation.hdec_relevant
        for evaluation in verified_evaluations
    )
    importance_qualified = sum(
        evaluation.importance.sendable for evaluation in verified_evaluations
    )
    # Eligibility is intentionally uncapped. Same-event duplicates collapse to
    # one representative first, the dedicated sent ledger filters next, and only
    # then does the 0-5 batch cap choose work for this run.
    run_cap = max(0, min(int(max_articles), HARD_TEAMS_BATCH_MAX))
    candidates, selection_audit = select_teams_push_from_artifact_with_audit(
        payload,
        max_articles=None,
        preference_runtime=preference_runtime,
        memory_batch_cap=run_cap,
    )
    primary_publisher_eligible = sum(
        publisher_delivery_priority(candidate.article)[0] == 0
        for candidate in candidates
    )
    non_primary_publisher_eligible = len(candidates) - primary_publisher_eligible
    duplicate_event_rows = int(selection_audit.get("event_duplicates") or 0)
    policy_eligible_rows = int(selection_audit.get("policy_eligible") or 0)
    accepted, baseline = filter_unsent_candidates(state, candidates)
    # R4-R9A — major-media-first source gate over the ledger-filtered batch.
    # Runs after every existing hard gate; it can only withhold, never rescue.
    gate_batch = apply_major_media_first_gate(
        accepted,
        state=state,
        run_cap=run_cap,
        now_iso_value=now_iso_value,
    )
    selected_gated = list(gate_batch.selected)
    selected = [item.candidate for item in selected_gated]
    deferred = [item.candidate for item in gate_batch.deferred_major]
    raw_tier_counts = _row_source_tier_counts(article_rows)
    verified_tier_counts = _row_source_tier_counts(verified_rows)

    alert_context = dict(payload)
    alert_context["dashboard_url"] = dashboard_url
    alert_context["report_url"] = report_url
    resolved_detected_at = detected_at or str(
        payload.get("generated_at") or payload.get("generated_kst") or ""
    )

    records: list[dict[str, Any]] = []
    blocked = sum(not decision.send_allowed for decision in baseline)
    rejection_breakdown = {key: 0 for key in REJECTION_COUNTER_KEYS}
    rejection_breakdown["malformed_required_field"] += invalid_row_count
    rejection_breakdown["duplicate_event"] += duplicate_event_rows
    for evaluation in policy_evaluations:
        if not evaluation.eligible:
            reason = evaluation.rejection_reason
            if reason not in rejection_breakdown:
                reason = "other_policy_reason"
            rejection_breakdown[reason] += 1
    for decision in baseline:
        if decision.send_allowed:
            continue
        if decision.reason in {
            "duplicate:article_id",
            "duplicate:normalized_url",
            "duplicate:title_fingerprint",
        }:
            rejection_breakdown["exact_duplicate"] += 1
        else:
            rejection_breakdown["already_sent"] += 1
    rejected_rows = sum(rejection_breakdown.values())
    rejection_reconciled = (
        rejected_rows + len(accepted) == len(raw_articles)
    )
    attempted = delivered = failed = state_committed = 0
    loop_dedup_blocked = dry_run_skipped = 0
    state_changed = False

    for candidate, decision in zip(candidates, baseline):
        if not decision.send_allowed:
            records.append({
                "article_ref": article_ref(candidate.article),
                "outcome": "dedup_blocked",
                "dedup_reason": decision.reason,
                "status": "no_request",
            })

    delivered_fallback_articles: list[Mapping[str, Any]] = []
    delivered_major_events: list[tuple[str, str, str]] = []
    for gated in selected_gated:
        candidate = gated.candidate
        ref = article_ref(candidate.article)
        decision = evaluate_dedup(
            state,
            candidate.article,
            cluster_key=candidate.cluster_key,
            signature=candidate.material_signature,
            is_material_update=bool(candidate.is_update),
        )
        if not decision.send_allowed:
            loop_dedup_blocked += 1
            records.append(
                {
                    "article_ref": ref,
                    "outcome": "dedup_blocked",
                    "dedup_reason": decision.reason,
                    "status": "no_request",
                }
            )
            continue

        if not should_send:
            dry_run_skipped += 1
            records.append(
                {
                    "article_ref": ref,
                    "outcome": "dry_run_no_send",
                    "dedup_reason": decision.reason,
                    "status": "no_request",
                    "is_update": decision.is_update,
                    "publisher_tier": gated.gate.tier,
                    "source_gate_class": gated.gate.gate_class,
                    "selection_mode": gated.selection_mode,
                }
            )
            continue

        attempted += 1
        ok, status, _code = send_article_email(
            replace(candidate, is_update=decision.is_update),
            alert_context=alert_context,
            credentials=credentials,
            detected_at=resolved_detected_at,
            smtp_factory=smtp_factory,
        )
        if ok:
            delivered += 1
            state = persist_after_success(
                state,
                candidate.article,
                path=state_path,
                cluster_key=candidate.cluster_key,
                signature=candidate.material_signature,
                importance=candidate.importance.level,
                source=str(candidate.article.get("source") or ""),
                send_succeeded=True,
                is_update=decision.is_update,
                delivery_id=f"teams_ai_push:{ref}",
                advances_normal_pace=(
                    candidate.importance.level == IMPORTANCE_IMPORTANT
                    and not candidate.importance.hdec_direct
                ),
            )
            state_changed = True
            state_committed += 1
            if gated.selection_mode == SELECTION_MODE_FALLBACK:
                delivered_fallback_articles.append(candidate.article)
            else:
                delivered_major_events.append((
                    str(candidate.cluster_key or ""),
                    f"teams_ai_push:{ref}",
                    str(candidate.article.get("source") or ""),
                ))
        else:
            failed += 1
        records.append(
            {
                "article_ref": ref,
                "outcome": "delivered" if ok else "failed",
                "dedup_reason": decision.reason,
                "status": status,
                "is_update": decision.is_update,
                "publisher_tier": gated.gate.tier,
                "source_gate_class": gated.gate.gate_class,
                "selection_mode": gated.selection_mode,
            }
        )

    # R4-R9A — held-specialist observations persist through the existing
    # production state path, in send mode only. A held article is observed,
    # never sent/accepted/consumed; a delivered fallback clears its hold; a
    # delivered major marks same-event held specialists as supporting
    # evidence. Dry runs never reach this block, so they write nothing.
    replaced_by_major_marks = 0
    if should_send:
        gated_state = state
        for observation in gate_batch.holdback_observations:
            gated_state = observe_held_specialist(
                gated_state,
                observation["article"],
                cluster_key=str(observation["cluster_key"] or ""),
                source=str(observation["source"] or ""),
                source_tier=str(observation["source_tier"] or ""),
                holdback_reason=str(observation["holdback_reason"] or ""),
                fallback_eligible=bool(observation["fallback_eligible"]),
                now=gate_batch.now_iso_value,
            )
        for fallback_article in delivered_fallback_articles:
            gated_state = clear_held_record(gated_state, fallback_article)
        for cluster_key, major_identity, major_source in delivered_major_events:
            gated_state, marks = mark_held_replaced_by_major(
                gated_state,
                cluster_key,
                major_identity=major_identity,
                major_source=major_source,
            )
            replaced_by_major_marks += marks
        if gated_state != state:
            save_state(gated_state, state_path)
            state = gated_state
            state_changed = True

    counters_reconciled = (
        len(accepted)
        == len(gate_batch.immediate)
        + len(gate_batch.held)
        + len(gate_batch.fallback_selected)
        + len(gate_batch.rejected)
        and len(selected)
        == len(gate_batch.immediate_selected) + len(gate_batch.fallback_selected)
        and len(accepted)
        == len(selected)
        + len(deferred)
        + len(gate_batch.held)
        + len(gate_batch.rejected)
        and len(candidates) == policy_eligible_rows - duplicate_event_rows
        and len(accepted) == len(candidates) - blocked
        and attempted == delivered + failed
        and state_committed == delivered
        and len(selected) == attempted + loop_dedup_blocked + dry_run_skipped
        and rejection_reconciled
    )
    major_row_decision_trace = _major_row_decision_traces(
        article_rows=article_rows,
        policy_evaluations=policy_evaluations,
        verified_object_ids=verified_object_ids,
        candidates=candidates,
        baseline=baseline,
        gate_batch=gate_batch,
        records=records,
    )
    policy_row_decision_trace = _major_row_decision_traces(
        article_rows=article_rows,
        policy_evaluations=policy_evaluations,
        verified_object_ids=verified_object_ids,
        candidates=candidates,
        baseline=baseline,
        gate_batch=gate_batch,
        records=records,
        major_only=False,
    )
    selected_source_audit = [
        {
            "article_ref": article_ref(gated.candidate.article),
            "publisher_tier": gated.gate.tier,
            "source_gate_class": gated.gate.gate_class,
            "publisher_rank": gated.gate.publisher_rank,
            "selection_mode": gated.selection_mode,
            "holdback_age_minutes": (
                gated.holdback.age_minutes if gated.holdback else 0.0
            ),
            "fallback_reason": (
                gated.holdback.holdback_reason if gated.holdback else ""
            ),
            "same_event_major_available": bool(
                gated.holdback and gated.holdback.same_event_major_available
            ),
        }
        for gated in selected_gated
    ]
    return {
        "mode": "send" if should_send else "dry_run_no_send",
        "current_candidates": len(current_rows),
        "verified_candidates": len(verified_rows),
        "AI_core": ai_core,
        "HDEC_relevant": hdec_relevant,
        "importance_qualified": importance_qualified,
        "alert_policy_eligible": policy_eligible_rows,
        "primary_publisher_eligible": primary_publisher_eligible,
        "non_primary_publisher_eligible": non_primary_publisher_eligible,
        "selected_primary_publisher": sum(
            publisher_delivery_priority(candidate.article)[0] == 0
            for candidate in selected
        ),
        "already_sent": blocked,
        "currently_claimed": 0,
        "selected": len(selected),
        "deferred_due_to_cap": len(deferred),
        "SMTP_attempted": attempted,
        "SMTP_accepted": delivered,
        "state_committed": state_committed,
        # D7-AK-6E R4-R6 §9 aggregate counter contract (exact names).
        "raw_rows": len(raw_articles),
        "current_rows": len(current_rows),
        "publisher_verified_rows": len(verified_rows),
        "ai_core_rows": ai_core,
        "executive_relevant_rows": hdec_relevant,
        "importance_qualified_rows": importance_qualified,
        "duplicate_event_rows": duplicate_event_rows,
        "previously_sent_rows": blocked,
        "eligible_unsent_rows": len(accepted),
        "selected_rows": len(selected),
        "deferred_rows": len(deferred),
        "smtp_attempted_rows": attempted,
        "smtp_accepted_rows": delivered,
        "smtp_failed_rows": failed,
        "state_committed_rows": state_committed,
        "counters_reconciled": counters_reconciled,
        "max_articles_cap": run_cap,
        # R4-R9A §11 — Teams source-gate audit counters (exact names).
        "raw_primary_10_rows": raw_tier_counts["primary_10"],
        "raw_secondary_3_rows": raw_tier_counts["secondary_3"],
        "raw_major_secondary_rows": raw_tier_counts["major_secondary"],
        "raw_specialist_rows": raw_tier_counts["specialist"],
        "verified_primary_10_rows": verified_tier_counts["primary_10"],
        "verified_secondary_3_rows": verified_tier_counts["secondary_3"],
        "verified_major_secondary_rows": verified_tier_counts["major_secondary"],
        "verified_specialist_rows": verified_tier_counts["specialist"],
        "teams_immediate_major_rows": gate_batch.audit[
            "teams_immediate_major_rows"
        ],
        "teams_specialist_held_rows": gate_batch.audit[
            "teams_specialist_held_rows"
        ],
        "teams_specialist_holdback_expired_rows": gate_batch.audit[
            "teams_specialist_holdback_expired_rows"
        ],
        "teams_specialist_fallback_eligible_rows": gate_batch.audit[
            "teams_specialist_fallback_eligible_rows"
        ],
        "teams_specialist_selected_rows": gate_batch.audit[
            "teams_specialist_selected_rows"
        ],
        # R4-R9D §8 — strict-source-gate specialist counters. The invariant is
        # specialist_rows_selected == 0 (automatic specialist fallback removed).
        "specialist_rows_seen": gate_batch.audit["specialist_rows_seen"],
        "specialist_rows_supporting_evidence": gate_batch.audit[
            "specialist_rows_supporting_evidence"
        ],
        "specialist_rows_automatic_rejected": gate_batch.audit[
            "specialist_rows_automatic_rejected"
        ],
        "specialist_rows_selected": gate_batch.audit["specialist_rows_selected"],
        "specialist_automatic_fallback_removed": gate_batch.audit[
            "specialist_automatic_fallback_removed"
        ],
        "teams_specialist_replaced_by_major_rows": replaced_by_major_marks,
        "selected_primary_10_rows": gate_batch.audit["selected_primary_10_rows"],
        "selected_secondary_3_rows": gate_batch.audit[
            "selected_secondary_3_rows"
        ],
        "selected_major_secondary_rows": gate_batch.audit[
            "selected_major_secondary_rows"
        ],
        "selected_promoted_official_rows": gate_batch.audit[
            "selected_promoted_official_rows"
        ],
        "selected_specialist_rows": gate_batch.audit["selected_specialist_rows"],
        "source_gate_rejected_rows": gate_batch.audit["source_gate_rejected_rows"],
        # R4-R10 — neutral/low + explicitly-excluded publisher rejections
        # (e.g. S저널), and the combined specialist-or-neutral rejection total.
        "never_automatic_rejected_rows": gate_batch.audit[
            "never_automatic_rejected_rows"
        ],
        "specialist_or_neutral_rejected_rows": gate_batch.audit[
            "specialist_or_neutral_rejected_rows"
        ],
        # R4-R9B §6 — stock-market hard-exclusion counters (exact names).
        "stock_market_dominant_rows": int(
            selection_audit.get("stock_market_dominant_rows") or 0
        ),
        "stock_market_hard_rejected_rows": int(
            selection_audit.get("stock_market_hard_rejected_rows") or 0
        ),
        "stock_market_hdec_exception_rows": int(
            selection_audit.get("stock_market_hdec_exception_rows") or 0
        ),
        "stock_market_fallback_blocked_rows": int(
            selection_audit.get("stock_market_fallback_blocked_rows") or 0
        ),
        "stock_market_gate_rejected_rows": int(
            gate_batch.audit.get("stock_market_gate_rejected_rows") or 0
        ),
        "normal_pacing_window_open": bool(
            gate_batch.audit.get("normal_pacing_window_open")
        ),
        "normal_pacing_age_minutes": int(
            gate_batch.audit.get("normal_pacing_age_minutes") or 0
        ),
        "normal_rows_selected": int(
            gate_batch.audit.get("normal_rows_selected") or 0
        ),
        "normal_rows_deferred_by_pacing": int(
            gate_batch.audit.get("normal_rows_deferred_by_pacing") or 0
        ),
        "urgent_rows_selected": int(
            gate_batch.audit.get("urgent_rows_selected") or 0
        ),
        "tier_1_immediate_rows": int(
            gate_batch.audit.get("tier_1_immediate_rows") or 0
        ),
        "tier_2_immediate_rows": int(
            gate_batch.audit.get("tier_2_immediate_rows") or 0
        ),
        "tier_2_selected_rows": int(
            gate_batch.audit.get("tier_2_selected_rows") or 0
        ),
        "tier_2_suppressed_by_tier_1_rows": int(
            gate_batch.audit.get("tier_2_suppressed_by_tier_1_rows") or 0
        ),
        "selected_source_audit": selected_source_audit,
        # R4-R12 §1 — row-level categorical decision trace for every
        # primary_10 / secondary_3 / promoted-official row in this run.
        "major_row_decision_trace": major_row_decision_trace,
        # R4-OPS-10H — every verified row that reached AI/HDEC/importance
        # policy, including non-major lanes. This makes safe-zero versus
        # over-strict-zero distinguishable from one natural run.
        "policy_row_decision_trace": policy_row_decision_trace,
        "skip_reasons": {
            "already_sent": blocked,
            "deferred_due_to_cap": len(deferred),
            "policy_ineligible": max(0, len(verified_rows) - policy_eligible_rows),
            "duplicate_event": duplicate_event_rows,
            "source_gate_rejected": len(gate_batch.rejected),
            "specialist_held": len(gate_batch.held),
        },
        "rejection_breakdown": rejection_breakdown,
        "rejected_rows": rejected_rows,
        "rejection_input_count": len(raw_articles),
        "rejection_reconciled": rejection_reconciled,
        "candidate_count": len(candidates),
        "dedup_blocked_count": blocked,
        "attempted_count": attempted,
        "delivered_count": delivered,
        "failed_count": failed,
        "state_changed": state_changed,
        "editorial_memory_invoked": bool(
            selection_audit.get("editorial_memory_invoked")
        ),
        "editorial_memory_profile": str(
            selection_audit.get("editorial_memory_profile") or ""
        ),
        "editorial_memory_active": bool(
            selection_audit.get("editorial_memory_active")
        ),
        "editorial_memory_candidates": [
            {
                "article_ref": article_ref(candidate.article),
                "editorial_memory_profile": candidate.editorial_memory_profile,
                "editorial_memory_active": candidate.editorial_memory_active,
                "approved_precedent_ids": list(
                    candidate.approved_precedent_ids
                ),
                "rejected_precedent_ids": list(
                    candidate.rejected_precedent_ids
                ),
                "near_miss_precedent_ids": list(
                    candidate.near_miss_precedent_ids
                ),
                "silver_precedent_ids": list(candidate.silver_precedent_ids),
                "memory_preference_score": candidate.memory_preference_score,
                "memory_preference_adjustment": (
                    candidate.memory_preference_adjustment
                ),
                "memory_rank_before": candidate.memory_rank_before,
                "memory_rank_after": candidate.memory_rank_after,
                "memory_changed_selection": (
                    candidate.memory_changed_selection
                ),
            }
            for candidate in candidates
        ],
        "records": records,
    }


def _write_github_output(path: str, summary: Mapping[str, Any]) -> None:
    if not path:
        return
    lines = (
        f"state_changed={'true' if summary.get('state_changed') else 'false'}",
        f"current_candidates={int(summary.get('current_candidates') or 0)}",
        f"verified_candidates={int(summary.get('verified_candidates') or 0)}",
        f"ai_core={int(summary.get('AI_core') or 0)}",
        f"hdec_relevant={int(summary.get('HDEC_relevant') or 0)}",
        f"importance_qualified={int(summary.get('importance_qualified') or 0)}",
        f"alert_policy_eligible={int(summary.get('alert_policy_eligible') or 0)}",
        f"primary_publisher_eligible={int(summary.get('primary_publisher_eligible') or 0)}",
        f"non_primary_publisher_eligible={int(summary.get('non_primary_publisher_eligible') or 0)}",
        f"selected_primary_publisher={int(summary.get('selected_primary_publisher') or 0)}",
        f"already_sent={int(summary.get('already_sent') or 0)}",
        f"currently_claimed={int(summary.get('currently_claimed') or 0)}",
        f"selected={int(summary.get('selected') or 0)}",
        f"deferred_due_to_cap={int(summary.get('deferred_due_to_cap') or 0)}",
        f"smtp_attempted={int(summary.get('SMTP_attempted') or 0)}",
        f"smtp_accepted={int(summary.get('SMTP_accepted') or 0)}",
        f"state_committed={int(summary.get('state_committed') or 0)}",
        f"teams_candidate_count={int(summary.get('candidate_count') or 0)}",
        f"teams_dedup_blocked_count={int(summary.get('dedup_blocked_count') or 0)}",
        f"teams_attempted_count={int(summary.get('attempted_count') or 0)}",
        f"teams_delivered_count={int(summary.get('delivered_count') or 0)}",
        f"teams_failed_count={int(summary.get('failed_count') or 0)}",
        f"rejected_rows={int(summary.get('rejected_rows') or 0)}",
        f"rejection_input_count={int(summary.get('rejection_input_count') or 0)}",
        f"rejection_reconciled={'true' if summary.get('rejection_reconciled') else 'false'}",
        "rejection_breakdown="
        + json.dumps(
            summary.get("rejection_breakdown") or {},
            sort_keys=True,
            separators=(",", ":"),
        ),
        f"raw_rows={int(summary.get('raw_rows') or 0)}",
        f"current_rows={int(summary.get('current_rows') or 0)}",
        f"publisher_verified_rows={int(summary.get('publisher_verified_rows') or 0)}",
        f"ai_core_rows={int(summary.get('ai_core_rows') or 0)}",
        f"executive_relevant_rows={int(summary.get('executive_relevant_rows') or 0)}",
        f"importance_qualified_rows={int(summary.get('importance_qualified_rows') or 0)}",
        f"duplicate_event_rows={int(summary.get('duplicate_event_rows') or 0)}",
        f"previously_sent_rows={int(summary.get('previously_sent_rows') or 0)}",
        f"eligible_unsent_rows={int(summary.get('eligible_unsent_rows') or 0)}",
        f"selected_rows={int(summary.get('selected_rows') or 0)}",
        f"deferred_rows={int(summary.get('deferred_rows') or 0)}",
        f"smtp_attempted_rows={int(summary.get('smtp_attempted_rows') or 0)}",
        f"smtp_accepted_rows={int(summary.get('smtp_accepted_rows') or 0)}",
        f"smtp_failed_rows={int(summary.get('smtp_failed_rows') or 0)}",
        f"state_committed_rows={int(summary.get('state_committed_rows') or 0)}",
        f"counters_reconciled={'true' if summary.get('counters_reconciled') else 'false'}",
        f"max_articles_cap={int(summary.get('max_articles_cap') or 0)}",
        # R4-R9A §11 — Teams source-gate audit counters.
        f"raw_primary_10_rows={int(summary.get('raw_primary_10_rows') or 0)}",
        f"raw_secondary_3_rows={int(summary.get('raw_secondary_3_rows') or 0)}",
        f"raw_major_secondary_rows={int(summary.get('raw_major_secondary_rows') or 0)}",
        f"raw_specialist_rows={int(summary.get('raw_specialist_rows') or 0)}",
        f"verified_primary_10_rows={int(summary.get('verified_primary_10_rows') or 0)}",
        f"verified_secondary_3_rows={int(summary.get('verified_secondary_3_rows') or 0)}",
        "verified_major_secondary_rows="
        + str(int(summary.get("verified_major_secondary_rows") or 0)),
        f"verified_specialist_rows={int(summary.get('verified_specialist_rows') or 0)}",
        f"teams_immediate_major_rows={int(summary.get('teams_immediate_major_rows') or 0)}",
        f"teams_specialist_held_rows={int(summary.get('teams_specialist_held_rows') or 0)}",
        "teams_specialist_holdback_expired_rows="
        + str(int(summary.get('teams_specialist_holdback_expired_rows') or 0)),
        "teams_specialist_fallback_eligible_rows="
        + str(int(summary.get('teams_specialist_fallback_eligible_rows') or 0)),
        f"teams_specialist_selected_rows={int(summary.get('teams_specialist_selected_rows') or 0)}",
        f"specialist_rows_seen={int(summary.get('specialist_rows_seen') or 0)}",
        "specialist_rows_supporting_evidence="
        + str(int(summary.get('specialist_rows_supporting_evidence') or 0)),
        "specialist_rows_automatic_rejected="
        + str(int(summary.get('specialist_rows_automatic_rejected') or 0)),
        f"specialist_rows_selected={int(summary.get('specialist_rows_selected') or 0)}",
        "specialist_automatic_fallback_removed="
        + str(bool(summary.get('specialist_automatic_fallback_removed'))),
        "teams_specialist_replaced_by_major_rows="
        + str(int(summary.get('teams_specialist_replaced_by_major_rows') or 0)),
        f"selected_primary_10_rows={int(summary.get('selected_primary_10_rows') or 0)}",
        f"selected_secondary_3_rows={int(summary.get('selected_secondary_3_rows') or 0)}",
        f"selected_major_secondary_rows={int(summary.get('selected_major_secondary_rows') or 0)}",
        f"selected_promoted_official_rows={int(summary.get('selected_promoted_official_rows') or 0)}",
        f"selected_specialist_rows={int(summary.get('selected_specialist_rows') or 0)}",
        f"source_gate_rejected_rows={int(summary.get('source_gate_rejected_rows') or 0)}",
        f"normal_rows_selected={int(summary.get('normal_rows_selected') or 0)}",
        "normal_rows_deferred_by_pacing="
        + str(int(summary.get("normal_rows_deferred_by_pacing") or 0)),
        f"urgent_rows_selected={int(summary.get('urgent_rows_selected') or 0)}",
        f"tier_1_immediate_rows={int(summary.get('tier_1_immediate_rows') or 0)}",
        f"tier_2_immediate_rows={int(summary.get('tier_2_immediate_rows') or 0)}",
        f"tier_2_selected_rows={int(summary.get('tier_2_selected_rows') or 0)}",
        "tier_2_suppressed_by_tier_1_rows="
        + str(int(summary.get("tier_2_suppressed_by_tier_1_rows") or 0)),
    )
    try:
        with Path(path).open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    except OSError:
        print("WARN: could not write GITHUB_OUTPUT summary", file=sys.stderr)


def _print_summary(summary: Mapping[str, Any]) -> None:
    for record in summary.get("records", ()):
        print(
            "Teams AI email: "
            f"article={record['article_ref']} outcome={record['outcome']} "
            f"dedup={record['dedup_reason']} status={record['status']}"
        )
    for trace in summary.get("major_row_decision_trace", ()):
        print(
            "Teams major-row trace: "
            f"article={trace['article_ref']} "
            f"source={trace['source']} "
            f"tier={trace['source_tier']} "
            f"lane={trace['teams_lane']} "
            f"gate_class={trace['gate_class']} "
            f"ai_core={'true' if trace['ai_core'] else 'false'} "
            f"hdec_relevant={'true' if trace['hdec_relevant'] else 'false'} "
            f"importance={trace['importance_level'] or '-'} "
            f"confirmed_event={'true' if trace['confirmed_event'] else 'false'} "
            f"freshness_current={'true' if trace['freshness_current'] else 'false'} "
            f"stock_excluded={'true' if trace['stock_market_excluded'] else 'false'} "
            f"stage={trace['stage']} "
            f"rejection_reason={trace['rejection_reason'] or '-'} "
            f"selected={'true' if trace['final_selected'] else 'false'} "
            f"smtp={trace['smtp_status']}"
        )
    for trace in summary.get("policy_row_decision_trace", ()):
        print(
            "Teams policy-row trace: "
            f"article={trace['article_ref']} "
            f"source={trace['source']} "
            f"tier={trace['source_tier']} "
            f"major_lane={'true' if trace['major_lane'] else 'false'} "
            f"ai_core={'true' if trace['ai_core'] else 'false'} "
            f"hdec_relevant={'true' if trace['hdec_relevant'] else 'false'} "
            f"selection_tier={trace['selection_tier'] or '-'} "
            f"semantic_class={trace['semantic_class'] or '-'} "
            f"semantic_reason={trace['semantic_reason'] or '-'} "
            f"delivery_category={trace['delivery_category'] or '-'} "
            f"promotion={trace['public_promotion_reason'] or '-'} "
            f"stage={trace['stage']} "
            f"rejection_reason={trace['rejection_reason'] or '-'} "
            f"selected={'true' if trace['final_selected'] else 'false'} "
            f"smtp={trace['smtp_status']}"
        )
    for entry in summary.get("selected_source_audit", ()):
        print(
            "Teams source gate selected: "
            f"article={entry['article_ref']} "
            f"tier={entry['publisher_tier']} "
            f"gate_class={entry['source_gate_class']} "
            f"publisher_rank={entry['publisher_rank']} "
            f"mode={entry['selection_mode']} "
            f"holdback_age_minutes={entry['holdback_age_minutes']} "
            f"fallback_reason={entry['fallback_reason'] or '-'} "
            "same_event_major_available="
            + ("true" if entry["same_event_major_available"] else "false")
        )
    print(
        "Teams source gate summary: "
        f"raw_primary_10_rows={summary['raw_primary_10_rows']} "
        f"raw_secondary_3_rows={summary['raw_secondary_3_rows']} "
        f"raw_specialist_rows={summary['raw_specialist_rows']} "
        f"verified_primary_10_rows={summary['verified_primary_10_rows']} "
        f"verified_secondary_3_rows={summary['verified_secondary_3_rows']} "
        f"verified_specialist_rows={summary['verified_specialist_rows']} "
        f"teams_immediate_major_rows={summary['teams_immediate_major_rows']} "
        f"teams_specialist_held_rows={summary['teams_specialist_held_rows']} "
        "teams_specialist_holdback_expired_rows="
        f"{summary['teams_specialist_holdback_expired_rows']} "
        "teams_specialist_fallback_eligible_rows="
        f"{summary['teams_specialist_fallback_eligible_rows']} "
        f"teams_specialist_selected_rows={summary['teams_specialist_selected_rows']} "
        "teams_specialist_replaced_by_major_rows="
        f"{summary['teams_specialist_replaced_by_major_rows']} "
        f"selected_primary_10_rows={summary['selected_primary_10_rows']} "
        f"selected_secondary_3_rows={summary['selected_secondary_3_rows']} "
        f"selected_major_secondary_rows={summary['selected_major_secondary_rows']} "
        f"selected_promoted_official_rows={summary['selected_promoted_official_rows']} "
        f"selected_specialist_rows={summary['selected_specialist_rows']} "
        f"source_gate_rejected_rows={summary['source_gate_rejected_rows']} "
        f"normal_rows_selected={summary['normal_rows_selected']} "
        "normal_rows_deferred_by_pacing="
        f"{summary['normal_rows_deferred_by_pacing']} "
        f"urgent_rows_selected={summary['urgent_rows_selected']}"
        f" tier_1_immediate_rows={summary['tier_1_immediate_rows']}"
        f" tier_2_immediate_rows={summary['tier_2_immediate_rows']}"
        f" tier_2_selected_rows={summary['tier_2_selected_rows']}"
        " tier_2_suppressed_by_tier_1_rows="
        f"{summary['tier_2_suppressed_by_tier_1_rows']}"
    )
    print(
        "Teams AI push summary: transport=email_channel "
        f"mode={summary['mode']} "
        f"current_candidates={summary['current_candidates']} "
        f"verified_candidates={summary['verified_candidates']} "
        f"AI_core={summary['AI_core']} "
        f"HDEC_relevant={summary['HDEC_relevant']} "
        f"importance_qualified={summary['importance_qualified']} "
        f"alert_policy_eligible={summary['alert_policy_eligible']} "
        f"primary_publisher_eligible={summary['primary_publisher_eligible']} "
        f"non_primary_publisher_eligible={summary['non_primary_publisher_eligible']} "
        f"selected_primary_publisher={summary['selected_primary_publisher']} "
        f"already_sent={summary['already_sent']} "
        f"currently_claimed={summary['currently_claimed']} "
        f"selected={summary['selected']} "
        f"deferred_due_to_cap={summary['deferred_due_to_cap']} "
        f"SMTP_attempted={summary['SMTP_attempted']} "
        f"SMTP_accepted={summary['SMTP_accepted']} "
        f"state_committed={summary['state_committed']} "
        f"failed={summary['failed_count']} "
        f"raw_rows={summary['raw_rows']} "
        f"current_rows={summary['current_rows']} "
        f"publisher_verified_rows={summary['publisher_verified_rows']} "
        f"duplicate_event_rows={summary['duplicate_event_rows']} "
        f"previously_sent_rows={summary['previously_sent_rows']} "
        f"eligible_unsent_rows={summary['eligible_unsent_rows']} "
        f"selected_rows={summary['selected_rows']} "
        f"deferred_rows={summary['deferred_rows']} "
        f"smtp_failed_rows={summary['smtp_failed_rows']} "
        f"max_articles_cap={summary['max_articles_cap']} "
        f"counters_reconciled={'true' if summary['counters_reconciled'] else 'false'} "
        f"skip_reasons={json.dumps(summary['skip_reasons'], sort_keys=True, separators=(',', ':'))} "
        f"rejection_breakdown={json.dumps(summary['rejection_breakdown'], sort_keys=True, separators=(',', ':'))} "
        f"rejected_rows={summary['rejected_rows']} "
        f"rejection_input_count={summary['rejection_input_count']} "
        f"rejection_reconciled={'true' if summary['rejection_reconciled'] else 'false'} "
        f"state_changed={'true' if summary['state_changed'] else 'false'}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Article-level Teams AI channel-email sender (default dry-run)"
    )
    parser.add_argument(
        "--artifact",
        default=(
            os.environ.get("TEAMS_ARTIFACT_FILE", "")
            or os.environ.get("DELTA_ARTIFACT_FILE", "")
        ),
    )
    parser.add_argument("--state", default=os.environ.get("TEAMS_PUSH_STATE_PATH", ""))
    parser.add_argument("--dashboard-url", default=os.environ.get("DASHBOARD_URL", ""))
    parser.add_argument("--report-url", default=os.environ.get("REPORT_URL", ""))
    parser.add_argument("--detected-at", default="")
    parser.add_argument("--github-output", default=os.environ.get("GITHUB_OUTPUT", ""))
    parser.add_argument(
        "--max-articles",
        default=os.environ.get("TEAMS_AI_PUSH_MAX_ARTICLES", ""),
        help=(
            "per-run article batch cap: default 5 when absent, 1-5 respected, "
            ">5 clamped to the hard maximum of 5, zero/negative/malformed "
            "fail closed to 1."
        ),
    )
    parser.add_argument(
        "--now",
        default=os.environ.get("TEAMS_AI_PUSH_NOW", ""),
        help=(
            "ISO timestamp used as the holdback reference clock "
            "(deterministic fixtures only; production uses the wall clock)"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="force preview only; no email request and no state write",
    )
    args = parser.parse_args(argv)

    if not args.artifact:
        print("ERROR: TEAMS_ARTIFACT_FILE/--artifact is required", file=sys.stderr)
        _write_github_output(args.github_output, {"state_changed": False})
        return 2

    mode = os.environ.get("TEAMS_AI_PUSH_MODE", "").strip().lower() or DEFAULT_MODE
    send_requested = mode == SEND_MODE and not args.dry_run
    max_articles, max_articles_policy = _resolve_max_articles(args.max_articles)
    print(f"Teams AI push batch cap: {max_articles} ({max_articles_policy})")
    credentials = resolve_email_channel_credentials()
    webhook_url = resolve_webhook_url()
    # The webhook is a reserved, inactive optional transport — logged for
    # observability only, never gating and never printed as a value.
    print(
        "Teams AI push transport: production=email_channel "
        f"optional_webhook={'configured' if webhook_url else 'absent'}"
    )

    try:
        if send_requested:
            check_send_preconditions(credentials)
        summary = deliver(
            artifact_path=Path(args.artifact).expanduser().resolve(),
            state_path=resolve_state_path(args.state or None).expanduser().resolve(),
            credentials=credentials,
            should_send=send_requested,
            dashboard_url=args.dashboard_url,
            report_url=args.report_url,
            detected_at=args.detected_at,
            max_articles=max_articles,
            now_iso_value=args.now,
        )
    except FailClosed as exc:
        print(
            f"ERROR: Teams AI push failed closed: {exc.reason} "
            "(email_sends=0 state_writes=0)",
            file=sys.stderr,
        )
        _write_github_output(args.github_output, {"state_changed": False})
        return 2

    _write_github_output(args.github_output, summary)
    _print_summary(summary)
    if summary["failed_count"]:
        print(
            f"ERROR: {summary['failed_count']} Teams AI email(s) failed to deliver",
            file=sys.stderr,
        )
        return 1
    print("RESULT=D7-AK-6A_TEAMS_AI_PUSH_COMPLETE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
