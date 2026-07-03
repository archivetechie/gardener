# gardener 🌱

Background repo hygiene for people who'd rather be building.

You brainstorm and design with an AI assistant; an implementation agent writes the
code; **gardener quietly handles everything else** — committing, pushing, pruning
branches, archiving finished docs — so you never have to remember to.

Born from a real workflow: one human + Claude (design/review) + Codex
(implementation) across half a dozen repos, with the human deliberately hopping
between threads. The hygiene debt that pattern generates is exactly what gardener
automates.

## Beyond hygiene: the full working method

Gardener grew into the home of the whole way of working — see
**[WORKING-METHOD.md](WORKING-METHOD.md)** (the loop: design → panel review →
contract-referenced prompts → implementation → diff gate → scenario verify; plus
thread-keyed cockpit sessions, the cross-session context bus, and model-economy
routing). New tools in `bin/`:

- **`sitrep`** — cross-repo situation report: the context bus any agent session
  runs at start (commits everywhere, standing issues, pending prompts).
- **`devup2`** — thread-keyed tmux cockpits (Claude + codex windows per thread),
  replacing one-session-per-repo sprawl.
- **`codex-exec`** — reliable non-interactive codex dispatch (sandboxed,
  stdin-guarded, prompt-from-file).

## What it does

**Every 2 hours (mechanical, plain bash — `housekeep.sh`):**
- Auto-commits repos that are dirty *and idle* (no change in the last 30 min — so
  it never snapshots you or an agent mid-edit) as `auto(checkpoint): …`
- Pushes (plain push, **never force**; diverged pushes are logged and left alone)
- Prunes branches **fully merged** into the default branch (never the default
  branch, never branches checked out in a worktree)

**Once a day (judgment, optional — `daily.sh`, needs the [Claude Code](https://claude.com/claude-code) CLI):**
- Maintains `docs/INDEX.md` in repos that have one; archives docs whose purpose is
  complete (e.g. implementation prompts whose acceptance criteria are now met)
- Repairs failed pushes when a fast-forward fixes them (never rebases/merges on
  its own)
- Reports stale unmerged branches and status-tracking drift, appends a one-section
  summary to the repo's `journal/` if it has one

Logs live in `~/.local/state/gardener/`. Config in `~/.config/gardener/`.

## Install

```bash
git clone https://github.com/swamikevala/gardener ~/gardener
export PATH="$HOME/gardener/bin:$PATH"     # add to your shell rc

gardener add ~/my-project                  # manage an existing repo
gardener install                           # cron: housekeep every 2h
gardener install --daily                   # + the daily AI layer at 02:30
```

That's it. `gardener status` shows what's managed and each repo's dirty/ahead state;
`gardener log` tails what it's been doing; `gardener run` forces a pass now.

## Adopt the full workflow in a repo

```bash
gardener init ~/my-project
```

seeds three small files (skipping any that exist):

- **`CLAUDE.md`** — the repo model + conventions, read by Claude at session start.
  Encodes the working pattern: *brainstorm → design doc → implementation prompt in
  `docs/` → agent implements → verification gates it.* And the crucial line:
  hygiene is automated, never ask the owner to commit.
- **`AGENTS.md`** — the implementer's contract (read by codex & co.): run what you
  changed and paste the output, always commit, never leave the tree dirty, update
  the docs index.
- **`docs/INDEX.md` + `docs/archive/`** — the docs lifecycle: every doc has a
  status; finished prompts and superseded designs get archived, not deleted.

Fill the `{{...}}` blanks, commit, done.

## The working pattern (the part worth stealing)

1. **Human + Claude brainstorm** until the design is real. The design lands as a
   doc in `docs/`.
2. The design becomes an **implementation prompt** (also in `docs/` — reviewable,
   versioned, re-runnable). If the work spans repos, write **one prompt per repo**
   with an *identical "Shared contract" section* in each — two implementation
   agents can then build both sides with zero live coordination.
3. **An implementation agent (codex, or Claude itself) executes the prompt**,
   bound by `AGENTS.md`: prove it ran, commit, update the index.
4. **Verification is a command, not a vibe** — every repo declares its one
   "this-means-done" command in `CLAUDE.md` (a test suite, an end-to-end scenario
   runner, whatever fits).
5. **gardener sweeps up behind everyone.**

The human's only jobs: have ideas, make decisions, and hop between threads at will.

## Safety properties (the invariants — don't weaken them)

| Concern | Guarantee |
|---|---|
| Snapshotting half-done work | Idle-aware: skips repos changed in the last 30 min |
| Clobbering history | Plain `git push` only — never force, never rebase/merge automatically |
| Losing branches | Prunes only branches *fully merged* into the default branch; worktree-checked-out branches are protected |
| Mid-operation repos | Skips repos with a merge/rebase in progress or detached HEAD |
| Runaway logs | Logs are bounded (tail-rotated) |

Auto-commits are clearly tagged (`auto(checkpoint)`, `auto(daily)`) so history
stays interpretable.

## Configuration

`~/.config/gardener/repos` — one absolute path per line (`#` comments fine).

`~/.config/gardener/config` (optional, sourced as shell):
```bash
IDLE_MIN=30                    # quiet minutes before auto-commit
DAILY_MODEL=claude-sonnet-4-6  # model for the daily layer
DAILY_MAX_TURNS=40
AUTHOR_TRAILER="Co-Authored-By: ..."   # trailer for auto commits
```

## Uninstall

```bash
crontab -l | grep -v gardener | crontab -    # remove the cron entries
rm -rf ~/.config/gardener ~/.local/state/gardener ~/gardener
```

Your repos are untouched — gardener only ever made ordinary git commits.

## FAQ

**Won't auto-committing WIP make my history messy?** Slightly — and that's the
trade. A checkpointed mess beats lost work and "what was I doing?" archaeology.
The commits are tagged and the daily layer keeps the *docs* tidy, which is where
tidiness actually pays.

**What if I use PRs, not direct-to-main?** gardener pushes whatever branch a repo
is on and never merges. Work on feature branches as usual; it just checkpoints them.

**Does it need codex?** No. The pattern works with any implementer — codex, Claude,
or you. Only the optional daily layer needs the `claude` CLI.
