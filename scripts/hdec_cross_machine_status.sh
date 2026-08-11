#!/usr/bin/env bash

set -u

TASK_BRANCH="wip/d7ak6e-final-readiness-zeroing-handoff"

ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || {
  echo "ERROR: not inside a Git worktree" >&2
  exit 1
}
cd "$ROOT" || exit 1

echo "MACHINE=$(hostname)"
echo "ROOT=$ROOT"

if git fetch origin --prune; then
  echo "FETCH_ORIGIN=PASS"
else
  echo "FETCH_ORIGIN=FAIL"
fi

branch="$(git branch --show-current)"
local_head="$(git rev-parse HEAD)"
origin_main="$(git rev-parse origin/main)"
remote_task_head="$(git rev-parse --verify "refs/remotes/origin/$TASK_BRANCH" 2>/dev/null || true)"

echo "CURRENT_BRANCH=${branch:-DETACHED}"
echo "LOCAL_HEAD=$local_head"
echo "ORIGIN_MAIN=$origin_main"
echo "REMOTE_TASK_BRANCH_HEAD=${remote_task_head:-ABSENT}"
echo "DIVERGENCE_HEAD_ORIGIN_MAIN=$(git rev-list --left-right --count HEAD...origin/main | tr '\t' '/')"

if [[ -n "$remote_task_head" ]]; then
  echo "DIVERGENCE_LOCAL_REMOTE_TASK=$(git rev-list --left-right --count HEAD..."refs/remotes/origin/$TASK_BRANCH" | tr '\t' '/')"
else
  echo "DIVERGENCE_LOCAL_REMOTE_TASK=NO_REMOTE_BRANCH"
fi

if [[ -z "$(git status --porcelain=v1)" ]]; then
  echo "DIRTY_SET=EMPTY"
else
  echo "DIRTY_SET=NONEMPTY"
  git status --short
fi

echo "RECENT_COMMITS_BEGIN"
git log -5 --oneline --decorate
echo "RECENT_COMMITS_END"

if command -v gh >/dev/null 2>&1; then
  echo "DRAFT_PR_LOOKUP_BEGIN"
  if ! gh pr list \
    --repo Sinabroin/HDEC-News-Sensor \
    --head "$TASK_BRANCH" \
    --state all \
    --json number,url,state,isDraft,title; then
    echo "DRAFT_PR_LOOKUP=UNAVAILABLE"
  fi
  echo "DRAFT_PR_LOOKUP_END"
else
  echo "DRAFT_PR_LOOKUP=GH_NOT_INSTALLED"
fi

echo "HANDOFF_FILE=$ROOT/docs/handoff/HDEC_CURRENT_HANDOFF.md"
