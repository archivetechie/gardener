#!/usr/bin/env bash
# gardener daily — the judgment layer. Runs headless Claude Code locally, once a day,
# to do the hygiene that needs reading comprehension: docs INDEX/archive maintenance,
# push-failure remediation, stale-branch review, status-tracking drift.
#
# Optional — requires the `claude` CLI. Enable via `gardener install --daily`.
# Config (~/.config/gardener/config): DAILY_MODEL=claude-sonnet-4-6  DAILY_MAX_TURNS=40

set -uo pipefail

CONFIG_DIR="${GARDENER_CONFIG_DIR:-$HOME/.config/gardener}"
STATE_DIR="${GARDENER_STATE_DIR:-$HOME/.local/state/gardener}"
LOG="$STATE_DIR/daily.log"
mkdir -p "$STATE_DIR"
export PATH="$HOME/.local/bin:$HOME/.bun/bin:/usr/local/bin:/usr/bin:/bin"
# Cron has no SSH agent; pushes to SSH remotes need the keychain-held key.
[[ -f "$HOME/.keychain/akash-sh" ]] && . "$HOME/.keychain/akash-sh"

DAILY_MODEL="claude-sonnet-4-6"
DAILY_MAX_TURNS=40
[[ -f "$CONFIG_DIR/config" ]] && source "$CONFIG_DIR/config"

command -v claude >/dev/null 2>&1 || { echo "$(date '+%F %T') claude CLI not found — daily layer skipped" >>"$LOG"; exit 0; }
[[ -f "$CONFIG_DIR/repos" ]] || { echo "$(date '+%F %T') no repos configured" >>"$LOG"; exit 0; }

REPO_LIST=$(grep -vE '^\s*(#|$)' "$CONFIG_DIR/repos" | sed "s|^~|$HOME|" | paste -sd ', ')

# The prompt template ships with gardener; {{REPOS}} and {{LOG}} are substituted.
TEMPLATE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../templates" && pwd)"
PROMPT=$(sed -e "s|{{REPOS}}|$REPO_LIST|g" -e "s|{{LOG}}|$STATE_DIR/housekeep.log|g" \
    "$TEMPLATE_DIR/daily-prompt.md")

{
    echo "===== $(date '+%F %T') gardener daily start ====="
    claude -p "$PROMPT" \
        --model "$DAILY_MODEL" \
        --dangerously-skip-permissions \
        --max-turns "$DAILY_MAX_TURNS" \
        2>&1
    echo "===== $(date '+%F %T') done (exit $?) ====="
} >>"$LOG"

tail -n 3000 "$LOG" > "$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG"
