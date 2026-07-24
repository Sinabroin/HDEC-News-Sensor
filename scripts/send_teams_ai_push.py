#!/usr/bin/env python3
"""Article-level Teams AI production sender (D7-AK-6A).

This entrypoint wires the already-verified leaves into the scheduled production
path — it adds delivery, and nothing else:

* ``app.teams_ai_push``    — Teams-only AI topic classification, importance
  mapping, and one Adaptive Card per article (max three).
* ``app.teams_push_state`` — persistent dedup over article id / normalized URL /
  title fingerprint / event cluster, including material-update re-send.

Selection and dedup logic is reused, never re-implemented here. The only new
behaviour is: POST one card per eligible article, then record success.

Default execution is dry-run and performs zero network operations. A real POST
requires all of GITHUB_ACTIONS=true, TEAMS_AI_PUSH_MODE=send,
APPROVE_TEAMS_AI_PUSH=true, an https TEAMS_WORKFLOW_WEBHOOK_URL, and a
``live-delta`` artifact whose shadow_alert_delta gate is open. Any other state
fails closed before a request is built.

There is deliberately no SMTP fallback: the contract is article-level Adaptive
Cards, so an unavailable webhook must fail loudly rather than be disguised as a
generic Teams channel-email digest. ``scripts/send_email_alert.py`` keeps owning
the separate email workflow.

Persistent state is mutated only after HTTP 2xx for that specific article, so a
partial failure keeps delivered articles recorded and leaves failed ones
resendable on the next run. Webhook URL, article URLs, and credentials are never
printed — logs carry only counts, a non-reversible article reference hash, and an
HTTP status category.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.teams_ai_push import (  # noqa: E402
    build_candidate_card,
    select_teams_push_from_artifact,
)
from app.teams_push_state import (  # noqa: E402
    InvalidTeamsPushState,
    article_identity,
    evaluate_dedup,
    filter_unsent_candidates,
    load_state,
    persist_after_success,
    resolve_state_path,
)

WEBHOOK_ENV = "TEAMS_WORKFLOW_WEBHOOK_URL"
WEBHOOK_TIMEOUT_SECONDS = 20
DEFAULT_MODE = "dry_run"
SEND_MODE = "send"
APPROVAL_TRUE = {"1", "true", "yes", "approved"}


class FailClosed(RuntimeError):
    """Abort before any network call. ``reason`` is a safe, value-free label."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _true_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in APPROVAL_TRUE


def resolve_webhook_url() -> str:
    """Read the Teams Workflows webhook from env (secret); https only.

    An invalid or missing value yields "" so the caller fails closed. This value
    is backend-only and is never printed to any log or artifact (rules.md §4)."""
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


def post_card(
    webhook_url: str,
    card: Mapping[str, Any],
    opener=None,
) -> tuple[bool, str]:
    """POST exactly one Adaptive Card → (delivered, status category label).

    Neither the webhook URL nor the response body is returned or printed; only a
    coarse category (accepted_2xx / rejected_Nxx / http_error_N / transport_error).
    ``opener`` is injectable and resolved at call time so verifiers exercise this
    path with zero network, mirroring the isolation pattern already used by the
    email sender."""
    opener = urllib.request.urlopen if opener is None else opener
    data = json.dumps(card, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        webhook_url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with opener(request, timeout=WEBHOOK_TIMEOUT_SECONDS) as response:
            code = int(getattr(response, "status", None) or response.getcode())
    except urllib.error.HTTPError as exc:
        return False, f"http_error_{exc.code}"
    except (urllib.error.URLError, OSError, TimeoutError, ValueError):
        return False, "transport_error"
    if 200 <= code < 300:
        return True, f"accepted_{code}"
    return False, f"rejected_{code}"


def load_artifact(path: Path) -> Mapping[str, Any]:
    if not path.exists():
        raise FailClosed("artifact_missing")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FailClosed("artifact_unreadable") from exc
    if not isinstance(payload, Mapping):
        raise FailClosed("artifact_root_not_object")
    if " ".join(str(payload.get("source") or "").split()) != "live-delta":
        raise FailClosed("artifact_not_live_delta")
    if payload.get("shadow_alert_delta") is not True:
        raise FailClosed("shadow_alert_delta_closed")
    return payload


def check_send_preconditions(webhook_url: str) -> None:
    """Every real-send requirement, evaluated before any card is built."""
    if os.environ.get("GITHUB_ACTIONS", "").strip().lower() != "true":
        raise FailClosed("not_github_actions")
    if not _true_env("APPROVE_TEAMS_AI_PUSH"):
        raise FailClosed("send_not_approved")
    if not webhook_url:
        raise FailClosed("webhook_missing_or_not_https")


def deliver(
    *,
    artifact_path: Path,
    state_path: Path,
    webhook_url: str,
    should_send: bool,
    dashboard_url: str = "",
    report_url: str = "",
    detected_at: str = "",
    opener=None,
) -> dict[str, Any]:
    """Select, dedup, and deliver at most three article cards.

    Each card is handled independently: one failure never skips the remaining
    cards, and only delivered articles reach persistent state."""
    payload = load_artifact(artifact_path)
    try:
        state = load_state(state_path)
    except InvalidTeamsPushState as exc:
        raise FailClosed("state_invalid") from exc

    candidates = select_teams_push_from_artifact(payload)
    # Contract reuse: the same helper the dry-run path uses. Its decisions are the
    # baseline; each card is then re-checked against the state as it evolves within
    # this run so two near-identical articles cannot both be delivered.
    _accepted, _baseline = filter_unsent_candidates(state, candidates)

    alert_context = dict(payload)
    alert_context["dashboard_url"] = dashboard_url
    alert_context["report_url"] = report_url
    resolved_detected_at = detected_at or str(
        payload.get("generated_at") or payload.get("generated_kst") or ""
    )

    records: list[dict[str, Any]] = []
    blocked = attempted = delivered = failed = 0
    state_changed = False

    for candidate in candidates:
        ref = article_ref(candidate.article)
        decision = evaluate_dedup(
            state,
            candidate.article,
            cluster_key=candidate.cluster_key,
            signature=candidate.material_signature,
            is_material_update=bool(candidate.is_update),
        )
        if not decision.send_allowed:
            blocked += 1
            records.append(
                {
                    "article_ref": ref,
                    "outcome": "dedup_blocked",
                    "dedup_reason": decision.reason,
                    "status": "no_request",
                }
            )
            continue

        card = build_candidate_card(
            alert_context,
            replace(candidate, is_update=decision.is_update),
            detected_at=resolved_detected_at,
        )

        if not should_send:
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
        ok, status = post_card(webhook_url, card, opener=opener)
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

    return {
        "mode": "send" if should_send else "dry_run_no_send",
        "candidate_count": len(candidates),
        "dedup_blocked_count": blocked,
        "attempted_count": attempted,
        "delivered_count": delivered,
        "failed_count": failed,
        "state_changed": state_changed,
        "records": records,
    }


def _write_github_output(path: str, summary: Mapping[str, Any]) -> None:
    if not path:
        return
    lines = (
        f"state_changed={'true' if summary.get('state_changed') else 'false'}",
        f"teams_candidate_count={int(summary.get('candidate_count') or 0)}",
        f"teams_dedup_blocked_count={int(summary.get('dedup_blocked_count') or 0)}",
        f"teams_attempted_count={int(summary.get('attempted_count') or 0)}",
        f"teams_delivered_count={int(summary.get('delivered_count') or 0)}",
        f"teams_failed_count={int(summary.get('failed_count') or 0)}",
    )
    try:
        with Path(path).open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    except OSError:
        print("WARN: could not write GITHUB_OUTPUT summary", file=sys.stderr)


def _print_summary(summary: Mapping[str, Any]) -> None:
    for record in summary.get("records", ()):
        print(
            "Teams AI card: "
            f"article={record['article_ref']} outcome={record['outcome']} "
            f"dedup={record['dedup_reason']} status={record['status']}"
        )
    print(
        "Teams AI push summary: "
        f"mode={summary['mode']} "
        f"candidates={summary['candidate_count']} "
        f"dedup_blocked={summary['dedup_blocked_count']} "
        f"attempted={summary['attempted_count']} "
        f"delivered={summary['delivered_count']} "
        f"failed={summary['failed_count']} "
        f"state_changed={'true' if summary['state_changed'] else 'false'} "
        "smtp_connections=0"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Article-level Teams AI Adaptive Card sender (default dry-run)"
    )
    parser.add_argument("--artifact", default=os.environ.get("DELTA_ARTIFACT_FILE", ""))
    parser.add_argument("--state", default=os.environ.get("TEAMS_PUSH_STATE_PATH", ""))
    parser.add_argument("--dashboard-url", default=os.environ.get("DASHBOARD_URL", ""))
    parser.add_argument("--report-url", default=os.environ.get("REPORT_URL", ""))
    parser.add_argument("--detected-at", default="")
    parser.add_argument("--github-output", default=os.environ.get("GITHUB_OUTPUT", ""))
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="force preview only; no webhook request and no state write",
    )
    args = parser.parse_args(argv)

    if not args.artifact:
        print("ERROR: DELTA_ARTIFACT_FILE/--artifact is required", file=sys.stderr)
        _write_github_output(args.github_output, {"state_changed": False})
        return 2

    mode = os.environ.get("TEAMS_AI_PUSH_MODE", "").strip().lower() or DEFAULT_MODE
    send_requested = mode == SEND_MODE and not args.dry_run
    webhook_url = resolve_webhook_url()

    try:
        if send_requested:
            check_send_preconditions(webhook_url)
        summary = deliver(
            artifact_path=Path(args.artifact).expanduser().resolve(),
            state_path=resolve_state_path(args.state or None).expanduser().resolve(),
            webhook_url=webhook_url,
            should_send=send_requested,
            dashboard_url=args.dashboard_url,
            report_url=args.report_url,
            detected_at=args.detected_at,
        )
    except FailClosed as exc:
        print(
            f"ERROR: Teams AI push failed closed: {exc.reason} "
            "(webhook_calls=0 smtp_connections=0 state_writes=0)",
            file=sys.stderr,
        )
        _write_github_output(args.github_output, {"state_changed": False})
        return 2

    _write_github_output(args.github_output, summary)
    _print_summary(summary)
    if summary["failed_count"]:
        print(
            f"ERROR: {summary['failed_count']} Teams AI card(s) failed to deliver",
            file=sys.stderr,
        )
        return 1
    print("RESULT=D7-AK-6A_TEAMS_AI_PUSH_COMPLETE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
