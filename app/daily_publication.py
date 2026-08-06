"""D7-AK-6E R4-R12 §5-6 — shared Daily publication contract constants.

Side-effect free and dependency-free. The content-addressed **immutable edition
manifest** is built and persisted by the production path itself
(:func:`app.editorial_briefings.build_daily_edition_manifest` +
:func:`run_editorial_briefing.write_daily_edition_manifest`), and the fail-closed
publication gates live in ``run_editorial_briefing`` (``run_publish`` builds and
writes the manifest and runs the pre-publish gates, ``run_verify_public`` runs
the pre-claim public-resource gate, ``run_send`` refuses to send without the
durable claim). This module deliberately does *not* re-implement any of that; it
only holds the constants those production gates share so the contract and the
code can never drift.
"""

from __future__ import annotations

# The pre-claim public-resource gate's machine-readable skip reason, emitted by
# run_editorial_briefing.run_verify_public when the dated reader page or the
# content-addressed immutable edition manifest fails to publicly resolve and
# reconstruct. The earlier pre-publish gates keep their own, more granular
# machine-readable reasons in run_editorial_briefing (daily_editor_console_missing
# / daily_review_bundle_unavailable / daily_editor_not_reconstructable /
# insufficient_quality) and skip fail-closed before anything is published.
SKIP_PUBLIC_RESOURCE = "daily_public_resource_verification_failed"

# Reader-only Daily Teams delivery is forbidden by contract, always: run_send
# refuses to send without the durable edition claim, and the claim itself
# requires the pre-claim public immutable-resource verification to have passed.
READER_ONLY_SEND_ALLOWED = False
