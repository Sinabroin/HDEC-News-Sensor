#!/usr/bin/env python3
"""Verify D7-AK-5D workflow-only Teams AI card dry-run wiring."""

from __future__ import annotations

from pathlib import Path

WORKFLOW = Path('.github/workflows/scheduled-live-refresh.yml')


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f'FAIL: {message}')


def block_between(text: str, start: str, end: str) -> str:
    require(text.count(start) == 1, f'expected one start marker: {start}')
    require(text.count(end) == 1, f'expected one end marker: {end}')
    left = text.index(start)
    right = text.index(end, left)
    require(left < right, f'block ordering invalid: {start} -> {end}')
    return text[left:right]


def main() -> int:
    text = WORKFLOW.read_text(encoding='utf-8')
    require('\t' not in text, 'workflow contains tab characters')

    detect = '- name: Detect dashboard alert delta'
    prepare = '- name: Prepare Teams AI article cards (dry-run only)'
    upload = '- name: Upload Teams AI article cards dry-run artifact'
    publish = '- name: Publish to Pages (commit live docs)'
    real_teams = '- name: Hourly Teams channel email (delta-gated auto-send)'

    for marker in (detect, prepare, upload, publish, real_teams):
        require(text.count(marker) == 1, f'workflow marker count invalid: {marker}')

    require(text.index(detect) < text.index(prepare) < text.index(upload) < text.index(publish),
            'dry-run steps must be between delta detection and Pages publish')

    prepare_block = block_between(text, prepare, upload)
    upload_block = block_between(text, upload, publish)

    required_prepare = (
        "id: teams_ai_push_dry_run",
        "if: steps.build.outputs.live_ok == 'true'",
        'DELTA_ARTIFACT_FILE: ${{ runner.temp }}/dashboard_delta.json',
        'TEAMS_PUSH_STATE_PATH: ${{ runner.temp }}/teams_push_state_readonly.json',
        'TEAMS_AI_PUSH_DRY_RUN_DIR: ${{ runner.temp }}/teams-ai-push-dry-run',
        'python3 scripts/prepare_teams_ai_push_dry_run.py',
        '--artifact "$DELTA_ARTIFACT_FILE"',
        '--state "$TEAMS_PUSH_STATE_PATH"',
        '--output-dir "$TEAMS_AI_PUSH_DRY_RUN_DIR"',
        'manifest.json',
        'card_count=',
        'dedup_blocked_count=',
        'webhook_calls=0',
        'state_writes=0',
    )
    for token in required_prepare:
        require(token in prepare_block, f'prepare step missing token: {token}')

    forbidden_prepare = (
        'TEAMS_WORKFLOW_WEBHOOK_URL', 'TEAMS_CHANNEL_EMAIL', 'GMAIL_',
        'EMAIL_SEND_MODE', 'APPROVE_SEND_EMAIL', 'SEND_TO_TEAMS',
        'send_email_alert.py', 'curl ', 'requests.', 'urllib.request',
    )
    for token in forbidden_prepare:
        require(token not in prepare_block, f'network/send token leaked into dry-run step: {token}')

    required_upload = (
        "if: steps.build.outputs.live_ok == 'true' && steps.teams_ai_push_dry_run.outcome == 'success'",
        'uses: actions/upload-artifact@v4',
        'name: teams-ai-news-push-dry-run-${{ github.run_id }}-${{ github.run_attempt }}',
        'path: ${{ runner.temp }}/teams-ai-push-dry-run',
        'if-no-files-found: error',
        'retention-days: 3',
    )
    for token in required_upload:
        require(token in upload_block, f'upload step missing token: {token}')

    require('TEAMS_WORKFLOW_WEBHOOK_URL' not in upload_block,
            'upload step must not receive webhook secret')

    # Existing production sender remains untouched and closed by the original variable gate.
    real_block = text[text.index(real_teams):]
    require(
        "if: steps.build.outputs.live_ok == 'true' && steps.delta.outputs.shadow_alert_delta == 'true' && vars.HOURLY_DELTA_AUTO_SEND == '1'"
        in real_block,
        'production Teams gate changed or missing',
    )
    require('run: python3 scripts/send_email_alert.py' in real_block,
            'existing production Teams sender entrypoint changed or missing')

    for verifier in (
        'python3 scripts/verify_teams_ai_push.py',
        'python3 scripts/verify_teams_push_state.py',
        'python3 scripts/verify_teams_ai_push_dry_run.py',
        'python3 scripts/verify_teams_ai_push_workflow_dry_run.py',
    ):
        require(text.count(verifier) == 1, f'pipeline verifier missing/duplicated: {verifier}')

    require(text.count('python3 scripts/prepare_teams_ai_push_dry_run.py') == 1,
            'dry-run preparer must run exactly once')
    require(text.count('actions/upload-artifact@v4') == 1,
            'dry-run artifact upload must occur exactly once')

    print('RESULT=D7-AK-5D_TEAMS_AI_PUSH_WORKFLOW_DRY_RUN_VERIFIER_PASS')
    print('network_send_steps_added=0 state_write_steps_added=0 artifact_upload_steps=1')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
