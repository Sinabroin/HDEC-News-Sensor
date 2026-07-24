#!/usr/bin/env python3
"""Verify the D7-AK-6C Teams AI news watch workflow wiring and its safety gating.

The article-level Teams sender now lives solely in ``teams-ai-news-watch.yml`` (a 10-minute
best-effort watch). This verifier checks that workflow's structure and safety contract, and
asserts the hourly ``scheduled-live-refresh.yml`` no longer sends Teams (single owner, no
double-send). The sender's delivery behaviour is proven separately in
verify_teams_ai_push_production.py.
"""

from __future__ import annotations

from pathlib import Path

WATCH = Path('.github/workflows/teams-ai-news-watch.yml')
SCHEDULED = Path('.github/workflows/scheduled-live-refresh.yml')


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
    watch = WATCH.read_text(encoding='utf-8')
    scheduled = SCHEDULED.read_text(encoding='utf-8')
    require('\t' not in watch, 'watch workflow contains tab characters')

    verify = '- name: Verify pipeline (mock-safe, no secrets)'
    build = '- name: Build live news metadata (temp only)'
    delta = '- name: Detect new-article delta (temp artifact)'
    teams = '- name: Teams AI news article cards (watch auto-send)'
    persist = '- name: Persist Teams AI push dedup state'
    skip = '- name: Skip Teams send (watch closed or not main)'

    for marker in (verify, build, delta, teams, persist, skip):
        require(watch.count(marker) == 1, f'watch marker count invalid: {marker}')
    require(
        watch.index(verify) < watch.index(build) < watch.index(delta)
        < watch.index(teams) < watch.index(persist) < watch.index(skip),
        'watch step order must be verify -> build -> delta -> teams -> persist -> skip',
    )

    # 10-minute best-effort schedule + concurrency + manual dispatch (with force_dry_run).
    require("cron: '*/10 * * * *'" in watch, 'watch must run on a 10-minute schedule')
    require('best-effort' in watch, 'watch must document GitHub best-effort scheduling')
    require('group: teams-ai-news-watch' in watch, 'watch must serialize runs (concurrency group)')
    require('workflow_dispatch:' in watch and 'force_dry_run:' in watch,
            'watch must preserve manual dispatch and the force-dry-run input')
    require('canary_cap:' in watch, 'watch must expose the bounded-canary cap input')

    build_block = block_between(watch, build, delta)
    delta_block = block_between(watch, delta, teams)
    teams_block = block_between(watch, teams, persist)
    persist_block = block_between(watch, persist, skip)

    # Build: live news metadata to a temp file only. No full dashboard/Pages republish and no
    # docs/daily writes — the committed dashboard is read only as the delta 'before' baseline.
    require('NEWS_MODE: live' in build_block, 'build step must collect live news')
    require('--output "$RUNNER_TEMP/dashboard-now.html"' in build_block,
            'build step must write the dashboard to a temp file, not docs/daily')
    require('live_ok=true' in build_block and 'live_ok=false' in build_block,
            'build step must fail closed when live collection fails')
    for forbidden in ('build_static_report.py', 'Publish to Pages', 'git push',
                      'docs/daily/latest.html', 'docs/daily/operator-latest.html'):
        require(forbidden not in build_block,
                f'build step must not run the heavy publish path: {forbidden}')

    # Delta: read the committed dashboard as baseline; give Teams headroom for up to ten.
    require('detect_dashboard_alert_delta.py' in delta_block, 'delta step must run the detector')
    require('docs/daily/dashboard-latest.html' in delta_block,
            'delta step must read the committed dashboard as the before-baseline')
    require('DELTA_ARTIFACT_MAX_ARTICLES:' in delta_block,
            'delta step must widen the artifact cap for the Teams path')
    require('--delta-artifact "$DELTA_ARTIFACT_FILE"' in delta_block,
            'delta step must emit the shared delta artifact')

    # Teams send: the article-level production sender, email_channel secrets, watch opt-in gate,
    # per-run canary cap — and never the SMTP digest entrypoint.
    require('run: python3 scripts/send_teams_ai_push.py' in teams_block,
            'watch Teams step must invoke the article-level production sender')
    teams_if = next((line for line in teams_block.splitlines() if line.strip().startswith('if:')), '')
    require("vars.TEAMS_AI_NEWS_WATCH == '1'" in teams_if,
            'watch Teams step must gate on the TEAMS_AI_NEWS_WATCH opt-in')
    require('shadow_alert_delta' not in teams_if,
            'watch Teams step must NOT gate on shadow_alert_delta (D7-AK-6C)')
    require("github.ref == 'refs/heads/main'" in teams_if, 'watch Teams step must be main-only')
    require(
        "(github.event_name != 'workflow_dispatch' || "
        "github.event.inputs.force_dry_run != 'true')" in teams_if,
        'watch Teams step must honour the force-dry-run guard',
    )
    for token in (
        'TEAMS_AI_PUSH_MODE: send',
        'APPROVE_TEAMS_AI_PUSH: "true"',
        'GMAIL_SMTP_USER: ${{ secrets.GMAIL_SMTP_USER }}',
        'GMAIL_SMTP_APP_PASSWORD: ${{ secrets.GMAIL_SMTP_APP_PASSWORD }}',
        'ALERT_EMAIL_FROM: ${{ secrets.ALERT_EMAIL_FROM }}',
        'TEAMS_CHANNEL_EMAIL: ${{ secrets.TEAMS_CHANNEL_EMAIL }}',
        'TEAMS_PUSH_STATE_PATH: data/teams_push_state.json',
        'DELTA_ARTIFACT_FILE: ${{ runner.temp }}/dashboard_delta.json',
        'TEAMS_AI_PUSH_MAX_ARTICLES:',
    ):
        require(token in teams_block, f'watch Teams step missing token: {token}')
    for token in ('send_email_alert.py', 'EMAIL_SEND_MODE', 'APPROVE_SEND_EMAIL', 'SEND_TO_TEAMS'):
        require(token not in teams_block,
                f'email digest entrypoint must not appear in the watch Teams step: {token}')

    # Persist: exactly one staged path, never a force-push, nothing beyond the state file.
    for token in (
        'if: always()',
        "steps.teams_ai_push.outcome != 'skipped'",
        "steps.teams_ai_push.outputs.state_changed == 'true'",
        'git add -- data/teams_push_state.json',
        'git commit -m "chore: persist Teams AI push dedup state"',
        'git rebase --abort',
        'git push origin HEAD:main',
    ):
        require(token in persist_block, f'state persist step missing token: {token}')
    require(persist_block.count('git add') == 1, 'state persist step must stage exactly one path')
    for token in ('docs/daily', 'scripts/', 'app/', '.github/', '--force', '-f origin',
                  'git add .', 'git add -A', 'git commit -am'):
        require(token not in persist_block,
                f'state persist step must not touch/force beyond the state file: {token}')

    # Telegram is never run by the watch (Teams-only owner).
    for token in ('send_telegram', 'send_scheduled_telegram', 'TELEGRAM_AUTO_SEND',
                  'TELEGRAM_BOT_TOKEN'):
        require(token not in watch, f'watch must never run any Telegram path: {token}')

    # No webhook secret anywhere; the watch runs the production verifier in its gate.
    require('secrets.TEAMS_WORKFLOW_WEBHOOK_URL' not in watch,
            'no webhook secret may be injected in the watch workflow')
    require(watch.count('python3 scripts/verify_teams_ai_push_production.py') == 1,
            'watch must run the Teams production verifier in its gate')

    # Mutual exclusion — the hourly scheduled-live-refresh no longer sends Teams (single owner).
    require('python3 scripts/send_teams_ai_push.py' not in scheduled,
            'scheduled-live-refresh must not invoke the Teams sender (single owner)')
    require('TEAMS_AI_PUSH_MODE: send' not in scheduled,
            'scheduled-live-refresh must inject no Teams send mode')
    require('git add -- data/teams_push_state.json' not in scheduled,
            'scheduled-live-refresh must persist no Teams dedup state')

    print('RESULT=D7-AK-6C_TEAMS_AI_NEWS_WATCH_WORKFLOW_VERIFIER_PASS')
    print('watch_owner=teams-ai-news-watch.yml teams_transport=email_channel '
          'schedule=10min best_effort=documented single_owner=true telegram_in_watch=0')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
