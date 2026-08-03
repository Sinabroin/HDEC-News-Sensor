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
    teams = '- name: Teams AI news article cards (watch auto-send)'
    persist = '- name: Persist Teams AI push dedup state'
    skip = '- name: Skip Teams send (watch closed or not main)'

    for marker in (verify, build, teams, persist, skip):
        require(watch.count(marker) == 1, f'watch marker count invalid: {marker}')
    require(
        watch.index(verify) < watch.index(build) < watch.index(teams)
        < watch.index(persist) < watch.index(skip),
        'watch step order must be verify -> build -> teams -> persist -> skip',
    )

    # 10-minute best-effort schedule + concurrency + an exactly-one manual canary.
    require("cron: '7,17,27,37,47,57 * * * *'" in watch, 'watch must run on a 10-minute schedule')
    require('best-effort' in watch, 'watch must document GitHub best-effort scheduling')
    require('group: teams-ai-news-watch' in watch, 'watch must serialize runs (concurrency group)')
    require('workflow_dispatch:' in watch and 'force_dry_run:' in watch,
            'watch must preserve manual dispatch and the force-dry-run input')
    require('production_canary:' in watch and 'canary_cap:' in watch,
            'watch must expose the explicit production-canary inputs')

    build_block = block_between(watch, build, teams)
    teams_block = block_between(watch, teams, persist)
    persist_block = block_between(watch, persist, skip)

    # Build: live news metadata to a temp file only. No full dashboard/Pages republish and no
    # docs/daily writes — the committed dashboard is read only as the delta 'before' baseline.
    require('NEWS_MODE: live' in build_block, 'build step must collect live news')
    require('BRIEF_JSON="$RUNNER_TEMP/validated-live-brief.json"' in build_block,
            'build step must write the validated live brief to runner temp')
    require('live_ok=true' in build_block and 'live_ok=false' in build_block,
            'build step must fail closed when live collection fails')
    for forbidden in ('build_static_report.py', 'Publish to Pages', 'git push',
                      'docs/daily/latest.html', 'docs/daily/operator-latest.html'):
        require(forbidden not in build_block,
                f'build step must not run the heavy publish path: {forbidden}')

    require('detect_dashboard_alert_delta.py' not in watch,
            'watch must not compare against the mutable public dashboard')
    require('docs/daily/dashboard-latest.html' not in watch,
            'public dashboard must not be the Teams delivery-state authority')

    # Teams send: the article-level production sender, email_channel secrets, watch opt-in gate,
    # per-run canary cap — and never the SMTP digest entrypoint.
    require('run: python3 scripts/send_teams_ai_push.py' in teams_block,
            'watch Teams step must invoke the article-level production sender')
    teams_if = next((line for line in teams_block.splitlines() if line.strip().startswith('if:')), '')
    require(
        "(github.event_name != 'schedule' || "
        "vars.TEAMS_AI_NEWS_WATCH == '1')" in teams_if,
        'scheduled Teams step must gate on the TEAMS_AI_NEWS_WATCH opt-in',
    )
    require('shadow_alert_delta' not in teams_if,
            'watch Teams step must NOT gate on shadow_alert_delta (D7-AK-6C)')
    require("github.ref == 'refs/heads/main'" in teams_if, 'watch Teams step must be main-only')
    require(
        "github.event.inputs.force_dry_run != 'true'" in teams_if,
        'watch Teams step must honour the force-dry-run guard',
    )
    require(
        "(github.event_name != 'workflow_dispatch' || "
        "github.event.inputs.production_canary == 'true')" in teams_if,
        'manual Teams send must require the explicit production-canary opt-in',
    )
    require(
        "(github.event_name != 'workflow_dispatch' || "
        "github.event.inputs.canary_cap == '1')" in teams_if,
        'manual Teams send must require exactly canary_cap=1',
    )
    for token in (
        'TEAMS_AI_PUSH_MODE: send',
        'APPROVE_TEAMS_AI_PUSH: "true"',
        'GMAIL_SMTP_USER: ${{ secrets.GMAIL_SMTP_USER }}',
        'GMAIL_SMTP_APP_PASSWORD: ${{ secrets.GMAIL_SMTP_APP_PASSWORD }}',
        'ALERT_EMAIL_FROM: ${{ secrets.ALERT_EMAIL_FROM }}',
        'TEAMS_CHANNEL_EMAIL: ${{ secrets.TEAMS_CHANNEL_EMAIL }}',
        'TEAMS_PUSH_STATE_PATH: data/teams_push_state.json',
        'TEAMS_ARTIFACT_FILE: ${{ runner.temp }}/validated-live-brief.json',
        'TEAMS_AI_PUSH_MAX_ARTICLES:',
    ):
        require(token in teams_block, f'watch Teams step missing token: {token}')
    require("github.event_name == 'workflow_dispatch' && '1'" in teams_block,
            'manual production canary must inject an immutable cap of one')
    require("vars.TEAMS_AI_NEWS_MAX_ARTICLES || '1'" in teams_block,
            'scheduled send must use a safe rollout cap with a one-article fallback')
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
        'git merge --abort',
        'git push origin HEAD:main',
    ):
        require(token in persist_block, f'state persist step missing token: {token}')
    require(persist_block.count('git add') == 1, 'state persist step must stage exactly one path')
    require('git rebase' not in persist_block, 'state persistence must not rebase')
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
