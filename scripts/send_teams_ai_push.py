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
    evaluate_teams_push_policy,
    publisher_delivery_priority,
    render_article_email,
    select_teams_push_from_artifact_with_audit,
)
from app import publisher_direct  # noqa: E402
from app.teams_push_state import (  # noqa: E402
    InvalidTeamsPushState,
    article_identity,
    evaluate_dedup,
    filter_unsent_candidates,
    load_state,
    persist_after_success,
    resolve_state_path,
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
    "insufficient_hdec_relevance",
    "insufficient_importance",
    "freshness_failed",
    "carry_forward_excluded",
    "source_authority_failed",
    "shadow_unavailable",
    "no_confirmed_event",
    "speculation_only",
    "already_sent",
    "exact_duplicate",
    "duplicate_event",
    "malformed_required_field",
    "other_policy_reason",
)


class FailClosed(RuntimeError):
    """Abort before any network call. ``reason`` is a safe, value-free label."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


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
    smtp_factory=None,
    preference_runtime=None,
) -> dict[str, Any]:
    """Select, dedup, and deliver 0-5 article emails per natural run.

    Zero eligible unsent articles send zero; up to five eligible send all of
    them; more than five send exactly five and defer the remainder for later
    runs. Filler is never invented. Each article is handled independently: one
    SMTP failure never skips the remaining articles, and only delivered
    (250 accepted) articles reach persistent state — failed ones stay
    resendable."""
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
    selected = accepted[:run_cap]
    deferred = accepted[run_cap:]

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

    for candidate in selected:
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
            )
            state_changed = True
            state_committed += 1
        else:
            failed += 1
        records.append(
            {
                "article_ref": ref,
                "outcome": "delivered" if ok else "failed",
                "dedup_reason": decision.reason,
                "status": status,
                "is_update": decision.is_update,
            }
        )

    counters_reconciled = (
        len(accepted) == len(selected) + len(deferred)
        and len(candidates) == policy_eligible_rows - duplicate_event_rows
        and len(accepted) == len(candidates) - blocked
        and attempted == delivered + failed
        and state_committed == delivered
        and len(selected) == attempted + loop_dedup_blocked + dry_run_skipped
        and rejection_reconciled
    )
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
        "skip_reasons": {
            "already_sent": blocked,
            "deferred_due_to_cap": len(deferred),
            "policy_ineligible": max(0, len(verified_rows) - policy_eligible_rows),
            "duplicate_event": duplicate_event_rows,
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
