#!/usr/bin/env bash
# gardener housekeep — idle-aware auto-checkpoint + push + prune across managed repos.
#
# Reads the managed-repo list from ~/.config/gardener/repos (one absolute path per
# line; '#' comments allowed). Optional ~/.config/gardener/config overrides:
#   IDLE_MIN=30        minutes a repo must be quiet before auto-committing
#   AUTHOR_TRAILER=Co-Authored-By: ...   trailer appended to auto commits
#
# Safety properties (the whole point — do not weaken):
#   - Idle-aware: skips a repo if any change is newer than IDLE_MIN minutes
#     (a human or agent is actively working there).
#   - Skips repos mid-merge/rebase or with detached HEAD.
#   - Push is plain `git push` — NEVER force. Diverged pushes are logged and left.
#   - Branch pruning deletes ONLY branches fully merged into the default branch,
#     never the default branch, never branches checked out in a worktree.
#
# Run from cron (gardener install) or manually: gardener run

set -uo pipefail

# Cron has no SSH agent; pushes to SSH remotes need the keychain-held key.
[[ -f "$HOME/.keychain/akash-sh" ]] && . "$HOME/.keychain/akash-sh"

CONFIG_DIR="${GARDENER_CONFIG_DIR:-$HOME/.config/gardener}"
STATE_DIR="${GARDENER_STATE_DIR:-$HOME/.local/state/gardener}"
REPOS_FILE="$CONFIG_DIR/repos"
LOG="$STATE_DIR/housekeep.log"
mkdir -p "$STATE_DIR"

IDLE_MIN=30
AUTHOR_TRAILER=""
[[ -f "$CONFIG_DIR/config" ]] && source "$CONFIG_DIR/config"
IDLE_MIN="${IDLE_MIN_OVERRIDE:-$IDLE_MIN}"

log() { printf '%s %s\n' "$(date '+%F %T')" "$*" >>"$LOG"; }

[[ -f "$REPOS_FILE" ]] || { log "no repos file at $REPOS_FILE — nothing to do"; exit 0; }

default_branch() {
    # main, else master, else current.
    git show-ref --verify -q refs/heads/main && { echo main; return; }
    git show-ref --verify -q refs/heads/master && { echo master; return; }
    git branch --show-current
}

newest_change_age_min() {
    local newest=0 ts f
    while IFS= read -r -d '' f; do
        # porcelain rename entries look like "old -> new"; take the new side.
        f="${f##* -> }"
        [[ -e "$f" ]] || continue
        ts=$(stat -c %Y "$f" 2>/dev/null || echo 0)
        (( ts > newest )) && newest=$ts
    done < <(git status --porcelain -z | cut -z -c4-)
    if (( newest == 0 )); then echo 99999; return; fi
    echo $(( ( $(date +%s) - newest ) / 60 ))
}

grep -vE '^\s*(#|$)' "$REPOS_FILE" | while IFS= read -r repo; do
    repo="${repo/#\~/$HOME}"
    [[ -d "$repo/.git" ]] || { log "skip $repo (not a git repo)"; continue; }
    cd "$repo" || continue

    if [[ -e .git/MERGE_HEAD || -d .git/rebase-merge || -d .git/rebase-apply ]]; then
        log "skip $repo (merge/rebase in progress)"; continue
    fi
    branch=$(git branch --show-current)
    [[ -n "$branch" ]] || { log "skip $repo (detached HEAD)"; continue; }
    base=$(default_branch)

    # --- auto-checkpoint ---
    if [[ -n "$(git status --porcelain)" ]]; then
        age=$(newest_change_age_min)
        if (( age < IDLE_MIN )); then
            log "$repo dirty but active (newest change ${age}m ago) — skipping commit"
        else
            files=$(git status --porcelain | head -20 | sed 's/^/    /')
            count=$(git status --porcelain | wc -l)
            msg_args=(-m "auto(checkpoint): $branch — $count change(s) [gardener]"
                      -m "Idle-time checkpoint by gardener housekeep. Changed:
$files")
            [[ -n "$AUTHOR_TRAILER" ]] && msg_args+=(-m "$AUTHOR_TRAILER")
            git add -A
            git commit -q "${msg_args[@]}" \
                && log "$repo committed checkpoint ($count changes on $branch)" \
                || log "$repo commit FAILED"
        fi
    fi

    # --- push (never force) ---
    if git remote get-url origin >/dev/null 2>&1; then
        ahead=$(git rev-list --count '@{u}..HEAD' 2>/dev/null || echo 1)
        if (( ahead > 0 )); then
            if git push -q origin "$branch" 2>>"$LOG"; then
                log "$repo pushed $branch (+$ahead)"
            else
                log "$repo push FAILED (diverged or auth?) — left for daily review"
            fi
        fi
    else
        log "$repo has no origin remote — commit only"
    fi

    # --- prune branches fully merged into the default branch ---
    if [[ -n "$base" ]] && git show-ref --verify -q "refs/heads/$base"; then
        while IFS= read -r b; do
            b=$(echo "$b" | sed 's/^[+* ]*//')
            [[ -z "$b" || "$b" == "$base" || "$b" == "$branch" ]] && continue
            if git worktree list --porcelain 2>/dev/null | grep -qx "branch refs/heads/$b"; then
                log "$repo keeping $b (checked out in a worktree)"; continue
            fi
            if git branch -d "$b" >/dev/null 2>&1; then
                log "$repo pruned merged branch $b"
                git push -q origin --delete "$b" 2>/dev/null && log "$repo pruned remote $b"
            fi
        done < <(git branch --merged "$base" | grep -vE "^\*?\s*${base}$")
        git remote prune origin >/dev/null 2>&1 || true
    fi
done

# Bound the log.
tail -n 2000 "$LOG" > "$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG"
