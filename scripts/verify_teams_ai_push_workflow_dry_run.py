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
    telegram = '- name: Hourly telegram digest (delta-gated auto-send)'
    real_teams = '- name: Hourly Teams AI article cards (delta-gated auto-send)'
    persist = '- name: Persist Teams AI push dedup state'
    skip_alerts = '- name: Skip automatic alerts (no delta)'

    for marker in (
        detect, prepare, upload, publish, telegram, real_teams, persist, skip_alerts
    ):
        require(text.count(marker) == 1, f'workflow marker count invalid: {marker}')

    require(text.index(detect) < text.index(prepare) < text.index(upload) < text.index(publish),
            'dry-run steps must be between delta detection and Pages publish')
    require(text.index(publish) < text.index(telegram) < text.index(real_teams) < text.index(persist),
            'production order must be publish -> telegram -> teams cards -> state persist')

    prepare_block = block_between(text, prepare, upload)
    upload_block = block_between(text, upload, publish)
    publish_block = block_between(text, publish, telegram)
    telegram_block = block_between(text, telegram, real_teams)
    real_block = block_between(text, real_teams, persist)
    persist_block = block_between(text, persist, skip_alerts)

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

    # Publish와 실제 발송은 main에서만 허용한다. workflow_dispatch force_dry_run=true
    # 실행에서는 Pages push·Telegram send·Teams send가 모두 step 수준에서 닫혀야 한다.
    main_only_guard = "github.ref == 'refs/heads/main'"
    force_dry_run_guard = (
        "(github.event_name != 'workflow_dispatch' || "
        "github.event.inputs.force_dry_run != 'true')"
    )

    for step_name, block in (
        ('Pages publish', publish_block),
        ('Telegram sender', telegram_block),
        ('Teams sender', real_block),
    ):
        require(
            main_only_guard in block,
            f'{step_name} missing main-only guard',
        )
        require(
            force_dry_run_guard in block,
            f'{step_name} missing force-dry-run guard',
        )

    require(
        'git push origin HEAD:main' in publish_block,
        'Pages publisher entrypoint changed or missing',
    )
    require(
        "vars.HOURLY_DELTA_AUTO_SEND == '1'"
        in telegram_block
        and "vars.TELEGRAM_AUTO_SEND == '1'" in telegram_block,
        'production Telegram variable gates changed or missing',
    )
    require(
        "vars.HOURLY_DELTA_AUTO_SEND == '1'" in real_block,
        'production Teams variable gate changed or missing',
    )
    # D7-AK-6A — 실제 Teams step은 기사별 production sender다. generic 채널 이메일
    # 다이제스트(SMTP)로 되돌아가면 '기사별 Adaptive Card' 계약이 조용히 깨지므로,
    # 이 step 안에서는 send_email_alert.py와 SMTP 자격증명을 모두 금지한다.
    require(
        'run: python3 scripts/send_teams_ai_push.py' in real_block,
        'article-level Teams production sender entrypoint changed or missing',
    )
    for token in (
        'send_email_alert.py', 'EMAIL_SEND_MODE', 'APPROVE_SEND_EMAIL',
        'SEND_TO_TEAMS', 'GMAIL_', 'TEAMS_CHANNEL_EMAIL', 'smtplib',
    ):
        require(token not in real_block,
                f'SMTP fallback token must not appear in the Teams card step: {token}')
    for token in (
        'TEAMS_AI_PUSH_MODE: send',
        'APPROVE_TEAMS_AI_PUSH: "true"',
        'TEAMS_WORKFLOW_WEBHOOK_URL: ${{ secrets.TEAMS_WORKFLOW_WEBHOOK_URL }}',
        'TEAMS_PUSH_STATE_PATH: data/teams_push_state.json',
        'DELTA_ARTIFACT_FILE: ${{ runner.temp }}/dashboard_delta.json',
    ):
        require(token in real_block, f'Teams card step missing token: {token}')

    # 부분 실패에도 이미 전송된 기사는 재발송되면 안 된다 → always()로 진입하되
    # sender가 실행됐고 state_changed=true일 때만 state 파일 하나를 commit한다.
    for token in (
        'if: always()',
        "steps.teams_ai_push.outcome != 'skipped'",
        "steps.teams_ai_push.outputs.state_changed == 'true'",
        'git add -- data/teams_push_state.json',
        "git commit -m \"chore: persist Teams AI push dedup state\"",
        'git rebase --abort',
        'git push origin HEAD:main',
    ):
        require(token in persist_block, f'state persist step missing token: {token}')
    require(persist_block.count('git add') == 1,
            'state persist step must stage exactly one path')
    for token in ('docs/daily', 'scripts/', 'app/', '.github/', '--force', '-f origin',
                  'git add .', 'git add -A', 'git commit -am'):
        require(token not in persist_block,
                f'state persist step must not touch/force beyond the state file: {token}')

    for verifier in (
        'python3 scripts/verify_teams_ai_push.py',
        'python3 scripts/verify_teams_push_state.py',
        'python3 scripts/verify_teams_ai_push_dry_run.py',
        'python3 scripts/verify_teams_ai_push_workflow_dry_run.py',
        'python3 scripts/verify_teams_ai_push_production.py',
    ):
        require(text.count(verifier) == 1, f'pipeline verifier missing/duplicated: {verifier}')

    require(text.count('python3 scripts/prepare_teams_ai_push_dry_run.py') == 1,
            'dry-run preparer must run exactly once')
    require(text.count('actions/upload-artifact@v4') == 1,
            'dry-run artifact upload must occur exactly once')

    print('RESULT=D7-AK-5D_TEAMS_AI_PUSH_WORKFLOW_DRY_RUN_VERIFIER_PASS')
    print('artifact_upload_steps=1 publish_send_guards=3 '
          'teams_production_sender=send_teams_ai_push.py smtp_fallback_steps=0 '
          'state_persist_steps=1 state_persist_staged_paths=1')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
