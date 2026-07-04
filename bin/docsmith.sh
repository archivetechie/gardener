#!/usr/bin/env bash
# gardener docsmith — nightly documentation-excellence layer. Runs headless Claude
# (cheap model, never the interactive session's tier) on ONE repository per night,
# round-robin, to audit and improve user-facing docs: code-derived prose, code
# anchors for drift detection (see docsmith-drift), per-repo backlog notebook.
#
# The agent edits and commits doc files only; pushing is left to housekeep.sh so
# there is a single push path. Config (~/.config/gardener/config):
#   DOCSMITH_MODEL=claude-sonnet-5   DOCSMITH_MAX_TURNS=80
# Repo list: ~/.config/gardener/docsmith-repos (fallback: the managed repos file).
# State: ~/.local/state/gardener/docsmith/ (cursor, per-repo notebooks, log).

set -uo pipefail

CONFIG_DIR="${GARDENER_CONFIG_DIR:-$HOME/.config/gardener}"
STATE_DIR="${GARDENER_STATE_DIR:-$HOME/.local/state/gardener}/docsmith"
LOG="$STATE_DIR/docsmith.log"
CURSOR="$STATE_DIR/cursor"
mkdir -p "$STATE_DIR"
BIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PATH="$BIN_DIR:$HOME/.local/bin:$HOME/.bun/bin:/usr/local/bin:/usr/bin:/bin"
[[ -f "$HOME/.keychain/akash-sh" ]] && . "$HOME/.keychain/akash-sh"

DOCSMITH_MODEL="claude-sonnet-5"
DOCSMITH_MAX_TURNS=80
[[ -f "$CONFIG_DIR/config" ]] && source "$CONFIG_DIR/config"

log() { printf '%s %s\n' "$(date '+%F %T')" "$*" >>"$LOG"; }

command -v claude >/dev/null 2>&1 || { log "claude CLI not found — skipped"; exit 0; }

REPOS_FILE="$CONFIG_DIR/docsmith-repos"
[[ -f "$REPOS_FILE" ]] || REPOS_FILE="$CONFIG_DIR/repos"
[[ -f "$REPOS_FILE" ]] || { log "no repos file — nothing to do"; exit 0; }

# One instance at a time.
exec 9>"$STATE_DIR/lock"
flock -n 9 || { log "another docsmith run in progress — skipped"; exit 0; }

mapfile -t REPOS < <(grep -vE '^\s*(#|$)' "$REPOS_FILE" | sed "s|^~|$HOME|")
(( ${#REPOS[@]} > 0 )) || { log "empty repo list"; exit 0; }

# Round-robin: start after the repo recorded in the cursor.
last=$(cat "$CURSOR" 2>/dev/null || true)
start=0
for i in "${!REPOS[@]}"; do
    [[ "${REPOS[$i]}" == "$last" ]] && start=$(( (i + 1) % ${#REPOS[@]} )) && break
done

# Pick the next eligible repo: exists, clean worktree, not mid-merge/rebase.
repo=""
for (( n = 0; n < ${#REPOS[@]}; n++ )); do
    cand="${REPOS[$(( (start + n) % ${#REPOS[@]} ))]}"
    [[ -d "$cand/.git" ]] || { log "skip $cand (not a git repo)"; continue; }
    if [[ -e "$cand/.git/MERGE_HEAD" || -d "$cand/.git/rebase-merge" || -d "$cand/.git/rebase-apply" ]]; then
        log "skip $cand (merge/rebase in progress)"; continue
    fi
    if [[ -n "$(git -C "$cand" status --porcelain)" ]]; then
        log "skip $cand (dirty — someone is working there)"; continue
    fi
    repo="$cand"; break
done
[[ -n "$repo" ]] || { log "no eligible repo tonight"; exit 0; }
echo "$repo" >"$CURSOR"

name=$(basename "$repo")
notebook="$STATE_DIR/notebook-$name.md"
TEMPLATE_DIR="$(cd "$BIN_DIR/../templates" && pwd)"
PROMPT=$(sed -e "s|{{REPO}}|$repo|g" -e "s|{{STATE_FILE}}|$notebook|g" \
    "$TEMPLATE_DIR/docsmith-prompt.md")

before=$(git -C "$repo" rev-parse HEAD)
{
    echo "===== $(date '+%F %T') docsmith start: $name ($DOCSMITH_MODEL) ====="
    ( cd "$repo" && claude -p "$PROMPT" \
        --model "$DOCSMITH_MODEL" \
        --dangerously-skip-permissions \
        --max-turns "$DOCSMITH_MAX_TURNS" \
        2>&1 )
    echo "===== $(date '+%F %T') docsmith done: $name (exit $?) ====="
} >>"$LOG"

# Guard: the run must only have touched documentation. Anything else is loud.
after=$(git -C "$repo" rev-parse HEAD)
if [[ "$after" != "$before" ]]; then
    nondoc=$(git -C "$repo" diff --name-only "$before" "$after" \
        | grep -vE '(\.md$|^docs/|^README|^CHANGELOG|^mkdocs\.yml$)' || true)
    if [[ -n "$nondoc" ]]; then
        log "WARNING $name: docsmith commits touched NON-DOC files — review needed:"
        printf '%s\n' "$nondoc" | sed 's/^/    /' >>"$LOG"
    else
        log "$name: $(git -C "$repo" rev-list --count "$before".."$after") doc commit(s) added"
    fi
else
    log "$name: no commits this run"
fi

tail -n 5000 "$LOG" > "$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG"
