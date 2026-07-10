<!-- code-anchor: none -->
# Getting started

This is the walkthrough for a new engineer setting gardener up on a machine
for the first time: what it is, how to install it, what the config files
mean, how to prove to yourself it's actually running, and how each of its
four cadences gets turned on (one of them, today, only by hand — see below).

If you just need a flag or a config key, [TOOLS.md](TOOLS.md) is the terse
reference. This page is the narrative version.

## What gardener actually is

If you work with AI coding assistants across several repositories, you end
up with a chore that has nothing to do with the work itself: remembering to
commit, remembering to push, remembering to delete the feature branch you
merged three days ago, remembering that a design doc is now stale and should
move out of the way. None of that is interesting. All of it is easy to
forget when you're mid-thought on the actual problem. Gardener is a small
set of scripts, run from cron, that does this chore for you so you never
have to context-switch into "repo janitor" mode.

It works at four cadences, from purely mechanical to purely judgment-based:

1. **Every 2 hours — housekeeping** (`housekeep.sh`, plain bash, no AI). If a
   repo has uncommitted changes and nobody has touched it in the last 30
   minutes, it commits them, pushes, and prunes any local/remote branches
   that are already fully merged. This is the layer that's always on.
2. **Once a day — judgment** (`daily.sh`, optional, needs the
   [Claude Code](https://claude.com/claude-code) CLI). Things that need
   reading comprehension rather than a git command: retrying a push that
   failed because it needed a fast-forward first, moving finished docs into
   `docs/historical/`, flagging regressions, keeping a `STANDING.md` issues
   ledger current.
3. **Once a week — a strategic brief** (not shipped by this repo — see
   [below](#the-weekly-brief-is-not-part-of-this-repo)).
4. **Once a night — documentation** (`docsmith.sh`, optional, needs the
   Claude Code CLI). Visits one repository per night, round-robin, and runs
   a headless agent whose only job is bringing that repo's docs up to
   best-in-class quality, derived from the code rather than copied from
   other docs.

In plain terms: think of gardener as a groundskeeper who does a full walk of
every property you own every two hours (tidy up, lock the gate, take out
anything that's clearly trash), and then, less often, someone who reads the
mail and files it properly (daily), someone who gives you a weekly briefing
on the state of things (weekly), and someone who occasionally repaints the
signage so it still matches what's actually behind each door (nightly docs).
None of them touch what you're building — only the paperwork around it.

<!-- code-anchor: bin/housekeep.sh bin/daily.sh bin/docsmith.sh @ febf1e7 -->
## Before you start

You need: `git`, `bash`, and a working `crontab` (`crontab -e` should open an
editor without complaint). The daily and docsmith layers additionally need
the `claude` CLI on `PATH` — if it isn't there, both scripts detect that and
exit quietly rather than failing loudly, so it's safe to install gardener
before you've set up Claude Code at all.

If any managed repo pushes over SSH from cron (cron has no SSH agent by
default), `housekeep.sh`, `daily.sh`, and `docsmith.sh` each look for
`~/.keychain/akash-sh` and source it if present. **This exact path is
hardcoded in all three scripts — it is not a config key.** If your SSH
agent's keychain wrapper lives somewhere else, either symlink it to that
path or push over HTTPS with a credential helper instead; there's currently
no way to point gardener at a different keychain script short of editing the
scripts themselves.

<!-- code-anchor: bin/gardener @ febf1e7 -->
## Install

```bash
git clone https://github.com/swamikevala/gardener ~/gardener
export PATH="$HOME/gardener/bin:$PATH"     # add this line to your shell rc too

gardener add ~/my-project                  # tell gardener about a repo (repeat per repo)
gardener install                           # writes the housekeep cron line
gardener install --daily                   # also writes the daily-layer cron line
```

`gardener add <path>` fails if `<path>` isn't a git repository, and is
idempotent — adding an already-managed repo is a no-op. Run it once per repo
you want gardener to look after; there's no bulk-import.

`gardener install` (with or without `--daily`) only ever writes or replaces
gardener's own cron lines — it greps for and removes any prior
`gardener/bin/housekeep|daily` or legacy `ops/housekeep|daily-housekeeper`
lines before appending fresh ones, so re-running it is always safe and
nothing else on your crontab is touched.

<!-- code-anchor: bin/gardener bin/housekeep.sh bin/daily.sh bin/docsmith.sh bin/devup2 @ febf1e7 -->
## The config files

Everything gardener reads lives under `~/.config/gardener/`. Nothing here is
required to exist except `repos` (and even that gets created empty by the
first `gardener` invocation) — every other file is optional and falls back
to a sane default if missing.

**`~/.config/gardener/repos`** — the list of repos gardener manages. One
absolute path per line, `#` comments allowed:

```
# repos gardener manages
/home/you/my-project
/home/you/another-project
```

This is what `gardener add`/`remove`/`list` edit, and what `housekeep.sh`
and `daily.sh` iterate over. It also doubles as the fallback rotation list
for docsmith if `docsmith-repos` (below) doesn't exist.

**`~/.config/gardener/config`** — optional, plain shell (it gets `source`d,
so no quoting surprises with spaces). Every key has a built-in default; you
only need this file to override one:

```bash
IDLE_MIN=30                    # minutes of quiet before housekeep auto-commits a dirty repo
AUTHOR_TRAILER="Co-Authored-By: ..."   # extra trailer line on auto-commits
DAILY_MODEL=claude-sonnet-4-6  # model for the daily judgment layer
DAILY_MAX_TURNS=40             # turn budget for a daily run
DOCSMITH_MODEL=claude-sonnet-5 # model for the nightly docs layer
DOCSMITH_MAX_TURNS=80          # turn budget for a docsmith run
HUB_REPO=/home/you/my-project  # where devup2 cockpit windows start (default: first line of repos)
COCKPITS="main side"           # space-separated devup2 tmux session names
CLAUDE_CMD="claude --dangerously-skip-permissions"   # command devup2's cc window runs
CODEX_CMD="codex --yolo"       # command devup2's cdx window runs
```

**`~/.config/gardener/docsmith-repos`** — optional, only read by
`docsmith.sh`. Same one-path-per-line format as `repos`. Use it when you
want the nightly docs layer to rotate over a *different* (typically larger)
set of repos than the ones gardener pushes for — e.g. local-only repos with
no remote, since docsmith only ever commits; pushing is still `housekeep.sh`'s
job. If this file doesn't exist, docsmith just uses `repos`.

None of these three files are created for you except `repos` — copy the
blocks above and edit as needed.

<!-- code-anchor: bin/gardener bin/housekeep.sh bin/daily.sh bin/docsmith.sh @ febf1e7 -->
## How to verify it's running

Five checks, cheapest first:

1. **`gardener status`** — prints the config file path, every gardener cron
   line currently installed, and for each managed repo its uncommitted-file
   count (`dirty`) and commits-ahead-of-upstream count (`ahead`). This is
   the fastest "is it wired up" check.
2. **`crontab -l | grep gardener`** — you should see a housekeep line
   (`17 */2 * * * .../bin/housekeep.sh`) always, a daily line
   (`30 2 * * * .../bin/daily.sh`) if you ran `install --daily`, and —
   separately, since nothing installs it for you — a docsmith line if you
   added one by hand (see the next section).
3. **Log files, under `~/.local/state/gardener/`**:
   - `housekeep.log` — every housekeep run appends here (tail-truncated to
     the last 2000 lines). Look for lines like `<repo> committed checkpoint`
     or `<repo> pushed <branch>`.
   - `daily.log` — same idea for the daily layer (last 3000 lines). A line
     reading `claude CLI not found — daily layer skipped` means exactly
     that: the `claude` CLI isn't on the `PATH` cron uses.
   - `docsmith/docsmith.log` — the nightly docs layer's log (last 5000
     lines). `gardener log [n]` only tails `housekeep.log`; for the other
     two, `tail` them directly.
4. **`~/.local/state/gardener/docsmith/`** — if the docsmith cron line is
   installed and has fired at least once, this directory holds `cursor`
   (which repo it'll start from next), `lock` (held only mid-run), and one
   `notebook-<repo>.md` per repo it has visited — its running backlog and
   run history for that repo. An empty or missing `docsmith/` directory
   after the cron line has had a chance to fire means either `claude` isn't
   on `PATH`, or no repo in the rotation was ever eligible (see
   troubleshooting below).
5. **A recent auto-commit** — `git -C ~/my-project log -1 --grep='\[gardener\]'`
   in any managed repo. Housekeep's checkpoint commits carry
   `auto(checkpoint): ... [gardener]`; daily's carry `auto(daily): ...`.

<!-- code-anchor: bin/gardener bin/docsmith.sh @ febf1e7 -->
## How each layer is enabled

| Layer | Enabled by | Cron line |
|---|---|---|
| Housekeep (2h) | `gardener install` | `17 */2 * * * .../bin/housekeep.sh` |
| Daily judgment | `gardener install --daily` | `30 2 * * * .../bin/daily.sh` |
| Nightly docs (docsmith) | **hand-edit crontab — see below** | *(none by default)* |
| Weekly brief | not part of this repo — see below | *(none by default)* |

### Known gap: docsmith has no `--docsmith` install flag

`gardener install` only ever writes housekeep and (optionally) daily cron
lines — grep `bin/gardener` for `docsmith` and you'll find nothing. Yet
`docsmith.sh` is a fully-built, optional third layer with its own config
keys, its own log, and its own per-repo notebooks. The only way to turn it
on today is a crontab line you add yourself:

```bash
crontab -e
# add a line after the daily entry, e.g.:
40 3 * * * /path/to/gardener/bin/docsmith.sh >/dev/null 2>&1
```

Any time after 02:30 works — that just leaves the daily layer some headroom
to finish first. This is a real gap in `bin/gardener` (it arguably should
grow a `--docsmith` flag mirroring `--daily`), first noted in this repo's
docsmith notebook on 2026-07-08 and still open — it's a code change, which
is outside what this documentation layer is allowed to touch, so it's
recorded here as the current, accurate mechanism rather than glossed over.

### The weekly brief is not part of this repo

[WORKING-METHOD.md](WORKING-METHOD.md) describes a weekly cadence — a
top-tier-model, bounded-turn brief covering state-of-program, ranked next
moves, and anything needing a human decision — as part of the same hygiene
family as housekeep and daily. It is real and it runs in production, but
**there is no `bin/weekly-brief.sh` in this repo and `gardener install` has
no flag for it.** It's implemented as a standalone script living in
whichever repo you treat as your hub, wired in with its own manual crontab
line — the same "write the line yourself" pattern as docsmith above, just
one level further outside gardener's own `bin/`. If you want one, write a
script analogous to `daily.sh` (headless `claude -p`, its own log under
`~/.local/state/gardener/` or your hub repo's own state directory) and add
its cron line by hand.

<!-- code-anchor: bin/housekeep.sh bin/daily.sh bin/docsmith.sh @ febf1e7 -->
## Troubleshooting

**Nothing in `housekeep.log`, ever.** Check the cron line exists
(`crontab -l | grep housekeep`) and that `~/.config/gardener/repos` has at
least one real path in it (`gardener list`). If the file exists but is
empty, `housekeep.sh` logs `no repos file at ... — nothing to do` and exits
— check you actually ran `gardener add`.

**A repo never gets auto-committed even though it's dirty.** Housekeep skips
a dirty repo if its newest changed file's mtime is inside the `IDLE_MIN`
window (default 30 minutes) — look for `dirty but active (newest change
...m ago) — skipping commit` in `housekeep.log`. This is by design (never
snapshot mid-edit) — wait it out, or lower `IDLE_MIN` in
`~/.config/gardener/config` if 30 minutes is too conservative for your
workflow. It also silently skips
repos that are mid-merge/rebase or on a detached HEAD — the log will say so
explicitly.

**Commits happen but pushes don't.** Look for `push FAILED (diverged or
auth?) — left for daily review` in `housekeep.log`. Housekeep never
force-pushes or rebases to fix this — it's intentionally left for a human or
the daily layer (which will fast-forward-retry a failed push, but still
won't rebase or merge). If every push is failing, check the keychain
sourcing described [above](#before-you-start) first — a missing SSH agent
is the most common cause on a fresh machine.

**Daily or docsmith never seem to run.** Check, in order: (1) `claude` is on
`PATH` — the exact log line to grep for is `claude CLI not found ...
skipped`; (2) for docsmith specifically, `~/.local/state/gardener/docsmith/lock`
isn't stuck held by a dead process (a genuinely running docsmith holds it
for the duration of one headless Claude session, which can be several
minutes — only worry if the lock file's mtime is far older than any
plausible run); (3) for docsmith, the repo it would visit next actually has
a clean working tree — `docsmith.sh` skips any repo with uncommitted changes
as "someone is working there" (logged as `skip <repo> (dirty — someone is
working there)`), so an actively-developed repo can go many nights without a
visit purely because it's never clean when the cron fires.

**A docsmith run touched something that isn't a doc.** This should be
structurally impossible — `docsmith.sh` diffs the repo's HEAD before and
after every run and logs a loud `WARNING <repo>: docsmith commits touched
NON-DOC files` if anything outside `*.md`, `docs/`, `README*`, `CHANGELOG*`,
or `mkdocs.yml` changed. If you see that warning, treat it as a bug report
against the docsmith prompt template, not something to silently accept.

<!-- code-anchor: none -->
## Where to go next

- [TOOLS.md](TOOLS.md) — the full flag/config reference for every script in
  `bin/`, including `sitrep` and `devup2` which aren't covered above because
  they're cockpit tools you run yourself, not cron layers.
- [WORKING-METHOD.md](WORKING-METHOD.md) — the broader design→implement→
  verify loop gardener's hygiene layer sits underneath, and the rationale
  for thread-keyed cockpit sessions.
- [README.md](README.md) — the short version of all of the above, plus the
  safety-property table worth reading once.
