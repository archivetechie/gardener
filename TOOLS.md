# Tool reference

This is the full command-line surface of everything in `bin/`. If you've only
read the README, you know the three automated layers (housekeep, daily, docsmith)
and the two cockpit tools (`sitrep`, `devup2`) by name. This page is where you
come when you need the actual flags, config keys, and gotchas.

All of these become available once `bin/` is on your `PATH` (see the README's
Install section):

```bash
export PATH="$HOME/gardener/bin:$PATH"
```

Most people only ever type `gardener`. The rest run from cron, or are opt-in for
the thread-cockpit workflow described in [WORKING-METHOD.md](WORKING-METHOD.md).

<!-- code-anchor: bin/gardener @ 695bf46 -->
## `gardener` — the main CLI

```
gardener add <path>        manage a repo (adds to ~/.config/gardener/repos)
gardener remove <path>     stop managing a repo
gardener list              show managed repos
gardener init <path>       seed a repo with the workflow convention files
                            (CLAUDE.md, AGENTS.md, docs/INDEX.md) and manage it
gardener install [--daily] install cron: housekeep every 2h (+ daily AI layer at 02:30)
gardener run                run housekeep now
gardener daily              run the daily AI layer now
gardener log [n]            tail the last n lines of the housekeep log (default 40)
gardener status             cron + config + per-repo dirty/ahead summary
```

Notes on individual subcommands:

- **`add <path>`** fails if `<path>` isn't a git repository (checks for `.git`).
  It's idempotent — adding an already-managed repo is a no-op.
- **`init <path>`** seeds `CLAUDE.md`, `AGENTS.md`, and `docs/INDEX.md` (plus an
  empty `docs/historical/` directory) from the templates in `templates/`, but
  **skips any file that already exists** rather than overwriting it — you'll see
  a note telling you to merge by hand instead. It finishes by calling `add` for
  you, so an `init`-ed repo is managed immediately.
- **`install`** writes (or replaces) a housekeep cron line running at :17 past
  every even hour. `install --daily` additionally adds a 02:30 cron line for
  `daily.sh`. Both are plain `crontab` edits — nothing else on your crontab is
  touched except prior gardener/housekeep/daily lines, which are replaced.
- **`daily`** and **`run`** just `exec` the corresponding script — see below for
  what each one actually does.
- **`status`** prints the config file path, any crontab lines containing
  `gardener`, and — for each managed repo — its uncommitted-file count (`dirty`)
  and commits ahead of its upstream (`ahead`).

**Gap worth knowing about:** there is no `gardener install --docsmith`. The
nightly documentation layer (`docsmith.sh`, below) has no install path through
the `gardener` CLI at all — it has to be added to cron by hand (see the
`docsmith.sh` section). `gardener install` only ever writes housekeep and daily
cron lines.

<!-- code-anchor: bin/housekeep.sh @ 695bf46 -->
## `housekeep.sh` — the mechanical layer

Runs standalone (`gardener run`) or from the cron `gardener install` writes.
Iterates every repo in `~/.config/gardener/repos`, and for each one:

1. **Skips it** if it's mid-merge/rebase, or on a detached HEAD.
2. **Auto-commits** if the repo is dirty *and* every changed file's mtime is
   older than `IDLE_MIN` minutes (default 30) — so it never commits under a
   human or agent who's mid-edit. The commit message is
   `auto(checkpoint): <branch> — <n> change(s) [gardener]`, with a second
   `-m` body listing the first 20 changed paths, and a third `-m` for
   `AUTHOR_TRAILER` if you've set one.
3. **Pushes** with a plain `git push` (never `--force`) if the repo has an
   `origin` remote and is ahead of its upstream. A failed push (usually a
   diverged branch) is logged, not retried or force-pushed.
4. **Prunes branches** that are fully merged into the default branch (`main`,
   falling back to `master`, falling back to whatever's checked out) — except
   the default branch itself and any branch checked out in a worktree. Merged
   local branches also get their matching `origin` branch deleted.

Config keys, read from `~/.config/gardener/config` if present:

| Key | Default | Meaning |
|---|---|---|
| `IDLE_MIN` | `30` | Minutes of quiet before a dirty repo gets auto-committed |
| `AUTHOR_TRAILER` | _(unset)_ | Extra trailer line appended to auto-commit messages |

For a one-off run that ignores the idle window (e.g. to force a checkpoint
immediately), set the environment variable `IDLE_MIN_OVERRIDE` when invoking:
`IDLE_MIN_OVERRIDE=0 gardener run`.

The log (`~/.local/state/gardener/housekeep.log`) is tail-truncated to the last
2000 lines on every run, so it can't grow without bound.

<!-- code-anchor: bin/daily.sh templates/daily-prompt.md @ 695bf46 -->
## `daily.sh` — the judgment layer

Runs standalone (`gardener daily`) or from the `--daily` cron entry, once a day
at 02:30. Requires the `claude` CLI — if it's not on `PATH`, the script logs a
line and exits cleanly (it does not error the cron job). It renders
`templates/daily-prompt.md` (substituting the managed repo list and the
housekeep log path) and runs it headless:

```bash
claude -p "<rendered prompt>" --model "$DAILY_MODEL" \
    --dangerously-skip-permissions --max-turns "$DAILY_MAX_TURNS"
```

Per the prompt template, the daily layer: retries fast-forward-able failed
pushes (never rebases or merges on its own); moves docs whose purpose is
complete into `docs/historical/` via `git mv`, updating `docs/INDEX.md`; lists
unmerged branches older than 14 days (never deletes them); reconciles a repo's
status-tracking files against reality and calls out regressions; maintains a
`STANDING.md` issues ledger where present (escalates anything open 3+ days into
a fix prompt); and appends a summary to `journal/YYYY-MM-DD.md` if the repo has
a `journal/` directory. It commits with `auto(daily): ` and pushes.

Config keys:

| Key | Default | Meaning |
|---|---|---|
| `DAILY_MODEL` | `claude-sonnet-4-6` | Model for the daily pass |
| `DAILY_MAX_TURNS` | `40` | Turn budget per run |

Log: `~/.local/state/gardener/daily.log`, truncated to the last 3000 lines.

<!-- code-anchor: bin/docsmith.sh templates/docsmith-prompt.md @ 695bf46 -->
## `docsmith.sh` — the nightly documentation layer

**Not wired into `gardener install`.** To enable it, add a cron line by hand,
e.g.:

```bash
crontab -e
# add:
40 3 * * * /path/to/gardener/bin/docsmith.sh >/dev/null 2>&1
```

(Pick any time after the daily layer's 02:30 slot — the example above leaves an
hour of headroom.) It can also be run manually — there's no `gardener`
subcommand for it, just run `docsmith.sh` directly once it's on `PATH`.

Behavior:

- Requires the `claude` CLI (silently skips if absent, same as `daily.sh`).
- Reads its rotation list from `~/.config/gardener/docsmith-repos`, falling
  back to the plain managed-repos file if that doesn't exist. One repo per
  line, `#` comments allowed, `~` expanded.
- **One repo per run, round-robin.** It remembers the last repo it visited in
  `~/.local/state/gardener/docsmith/cursor` and starts scanning just after it
  in the list. It picks the first repo from there that exists, isn't
  mid-merge/rebase, and has a **clean working tree** — a repo with any
  uncommitted changes is skipped as "someone is working there," which means an
  actively-developed repo can go multiple nights without a docsmith visit.
- Takes a lock (`flock` on `~/.local/state/gardener/docsmith/lock`) so only one
  instance ever runs at a time; a second invocation while one is in progress
  exits immediately.
- Renders `templates/docsmith-prompt.md` (substituting the repo path and its
  notebook path) and runs it headless, `cd`-ed into the target repo:
  `claude -p "<prompt>" --model "$DOCSMITH_MODEL" --dangerously-skip-permissions
  --max-turns "$DOCSMITH_MAX_TURNS"`.
- Keeps a **per-repo notebook** at
  `~/.local/state/gardener/docsmith/notebook-<name>.md` (backlog + run history).
  If the repo directory is literally named `repo` — common in `<project>/repo`
  layouts — it uses the parent directory's name instead, so notebooks for
  different projects don't collide.
- **Guards its own scope after every run**: diffs the repo's HEAD before and
  after, and if anything outside `*.md`, `docs/`, `README*`, `CHANGELOG*`, or
  `mkdocs.yml` changed, it logs a loud `WARNING ... touched NON-DOC files`
  instead of failing silently.
- Does **not** push — that stays with `housekeep.sh` so there's a single push
  path.

Config keys:

| Key | Default | Meaning |
|---|---|---|
| `DOCSMITH_MODEL` | `claude-sonnet-5` | Model for the nightly docs pass |
| `DOCSMITH_MAX_TURNS` | `80` | Turn budget per run |

Log: `~/.local/state/gardener/docsmith/docsmith.log`, truncated to the last
5000 lines.

<!-- code-anchor: bin/docsmith-drift @ 695bf46 -->
## `docsmith-drift` — anchor staleness checker

```
docsmith-drift [repo-dir]     (default: current directory)
```

Scans every tracked `*.md` file in the target repo for `code-anchor:` HTML
comments (`<!-- code-anchor: <path> [<path> ...] @ <commit> -->`, written by
docsmith above section headings) and reports every anchor whose recorded commit
is either unknown to the repo (`UNKNOWN-BASE`) or malformed (`MALFORMED`), or
whose anchored paths have changed between that commit and `HEAD` (`STALE`).
`<!-- code-anchor: none -->` marks a deliberately unbound narrative section and
is skipped. Exit code is `0` with no drift, `1` if any anchor is stale or
broken, `2` on a usage or repo error.

<!-- code-anchor: bin/sitrep @ 695bf46 -->
## `sitrep` — the cross-repo context bus

```
sitrep [since]        e.g. sitrep "3 days ago"    (default: 24 hours ago)
```

A deterministic, no-LLM, ~1-second report meant to run at the start of any
agent or human session so nobody has to rediscover work done elsewhere. For
every repo in `~/.config/gardener/repos` it prints commits since `[since]` and
an uncommitted-file count. The **hub repo** — the one repo that's treated as
holding shared state — is either the first line in `~/.config/gardener/repos`
or whatever `HUB_REPO` is set to in `~/.config/gardener/config`. If the hub
repo has these files, sitrep also prints from them:

- `STANDING.md` — open/fix-queued rows (filters out ones marked fixed/accepted)
- `GAPBOARD.md` — the latest `_Latest run` headline line and any `halted` rows
- `docs/INDEX.md` — any line mentioning "pending", scanning for `` `...prompt...` `` references
- `journal/YYYY-MM-DD.md` (today's date) — its `##` section headings

Any of these sections is silently omitted if the corresponding file doesn't
exist, so a hub repo without a `STANDING.md` just won't show that section.

<!-- code-anchor: bin/devup2 @ 695bf46 -->
## `devup2` — thread-keyed cockpit sessions

```
devup2 [up | --down | --restart]      (default: up)
```

Builds tmux sessions ("cockpits") that are keyed to a *thread of work*, not a
repo — see [WORKING-METHOD.md](WORKING-METHOD.md) for the rationale. Each
cockpit is one tmux session with two windows: `cc` running the Claude command,
`cdx` running the codex command, both started in the **hub repo** so Claude's
project memory and conventions load correctly.

- **`up`** (default) creates any cockpit session that doesn't already exist
  (existing sessions are left alone — safe to re-run), sets up the key
  bindings below, then attaches (or, if you're already inside tmux, switches
  the client) to the first cockpit.
- **`--down`** lists any running cockpit sessions, asks for confirmation
  (`about to kill: ... proceed? [y/N]`), and if confirmed, kills them —
  which also kills their `claude`/`codex` processes.
- **`--restart`** is `--down` followed by `up`.

Key bindings, set globally (`-n`, no prefix key needed) once any cockpit is
built:

- `M-1` / `M-2` — jump to the `cc` (Claude) / `cdx` (codex) window in the
  current cockpit
- `M-q`, `M-w`, `M-e`, `M-r`, `M-t`, `M-y`, `M-u` — switch to the 1st through
  7th cockpit session, in the order they're listed in `COCKPITS` (only as many
  keys are bound as you have cockpits configured)

Config keys, read from `~/.config/gardener/config`:

| Key | Default | Meaning |
|---|---|---|
| `HUB_REPO` | first repo in `~/.config/gardener/repos` | Where cockpit windows start |
| `COCKPITS` | `main side` | Space-separated cockpit session names |
| `CLAUDE_CMD` | `claude --dangerously-skip-permissions` | Command for the `cc` window |
| `CODEX_CMD` | `codex --yolo` | Command for the `cdx` window |

<!-- code-anchor: bin/codex-exec @ 695bf46 -->
## `codex-exec` — reliable non-interactive codex

```
codex-exec <prompt-file> [--cd <dir>] [--write] [--full] [--timeout <secs>]
```

A thin wrapper around `codex exec` that fixes two footguns:

- **Stdin**: `codex exec` reads stdin, so a backgrounded/cron call without
  redirection can hang forever. This wrapper always runs with `</dev/null`.
- **Sandbox**: on Ubuntu 24.04+, unprivileged user namespaces are
  AppArmor-restricted, which breaks codex's bubblewrap sandbox unless you add
  an AppArmor profile for `bwrap` — see "Host setup" in
  [WORKING-METHOD.md](WORKING-METHOD.md). With that fix in place the real
  sandbox works, so this wrapper never falls back to a `--dangerously-bypass`
  flag.

The prompt is read from `<prompt-file>` (not a shell argument), so quoting
stays sane and the transcript is reviewable. Flags:

| Flag | Effect |
|---|---|
| `--cd <dir>` | Working directory for codex (default: `$HOME`) |
| `--write` | Sandbox `workspace-write` instead of the default `read-only` |
| `--full` | Sandbox `danger-full-access` (network/system access) — for implementation phases that need it |
| `--timeout <secs>` | Kill codex if it hasn't finished after this many seconds (default 600) |

It also sets `UV_CACHE_DIR` to `/tmp/uv-cache-codex` if unset — the
`workspace-write` sandbox denies `~/.cache`, and `uv` hangs acquiring a cache
lock without a writable cache dir.
