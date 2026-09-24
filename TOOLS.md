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
## `gardener` and `workflow`

`gardener add|remove|list` manages the canonical TOML registry. `init PATH` seeds
missing convention files; private indexes require `GARDENER_PRIVATE_REPO=1`.
`install [--daily]` installs cron, `run` invokes housekeeping, `daily` invokes the
daily summary, `log [N]` tails compatibility output, and `status` runs sitrep.

`workflow registry list|add|remove|migrate [PATH] [--private] [--hub PATH]`
configures repository policy. Migration preserves legacy repositories and requires
an explicit hub. See [configuration](GETTING-STARTED.md#configuration).

| Entry point | Behavior |
| --- | --- |
| `housekeep.sh` / `workflow housekeep` | Temporary-index local checkpoint refs; no active branch/index mutation, default push, or remote deletion |
| `daily.sh` / `workflow daily` | Sandboxed primary-evidence summary and deterministic private journal export |
| `docsmith.sh` / `workflow docsmith` | Isolated proposal, whole-output guard, independent exact-commit review, guarded local fast-forward |
| `workflow weekly` | Weekly summary and private evidence export |
| `workflow journal` | Deterministic export without a model call |
| `workflow health` | Primary attempt verdicts, failure streaks and last passes |
| `workflow candidates` | Pending/rejected/expired candidate backlog |
| `workflow review-candidate ID` | Retry an unexpired candidate's independent review |
| `workflow lease --repo PATH --resource NAME -- COMMAND` | Hold verified shared ownership through a child process |

Events are append-only start/source/terminal JSON, with unique IDs and separate
source/content identity. Logs in state candidate directories are tied to their
attempt by digest. The legacy log paths remain append-only compatibility views.
Author model overrides are `DOCSMITH_MODEL` and `DAILY_MODEL`; reviewer identity
is pinned in the implementation. Model jobs require Bubblewrap and fail closed
when it is unavailable. Candidate policy and source checks run outside the model.

Checkpoints deduplicate trees and retain the newest 50 local refs. Obvious new
credential files, Git operations in progress, detached HEADs and occupied locks
are rejected or explicitly skipped. Automatic checkpoint backup is opt-in and
limited to an absolute local bare repository. There is no automatic remote prune.

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

<!-- code-anchor: bin/sitrep @ 563bea4 -->
## `sitrep` — the cross-repo context bus

```
sitrep [since]        e.g. sitrep "3 days ago"    (default: 24 hours ago)
```

A deterministic report reads the canonical TOML registry and its explicit private
hub. It prints recent commits and dirty state, all active standing issue states,
pending prompt rows, primary automation health, and candidate backlog/expiry.
Legacy path-list fallback does not infer a hub from list order. Missing primary
evidence remains unknown; an old journal date does not become a new observation.

One more section prints unconditionally, regardless of the hub repo's
contents: **vendored contract drift**. Some repos deliberately keep a
hand-updated copy of another repo's file — for example `sutradhara` vendors a
copy of `remanence`'s `proto/layer5.proto`, and treats refreshing that copy as
a considered event rather than something to automate. Nothing else signals
how far the copy has fallen behind, so `sitrep` diffs each configured pair and
reports the line count: `<label>: N line(s) behind <filename>`, or
`(all vendored copies current)` if every configured pair is in sync. The
pairs are a short list hardcoded directly in the `sitrep` script itself (one
pair today, `remanence`/`sutradhara`) — not read from
`~/.config/gardener/`, and not something `gardener init` sets up. A pair is
silently skipped if either file doesn't exist, so on a machine without those
specific repos this section just prints `(all vendored copies current)`.
Reporting the drift is deliberate. `sitrep` never touches the copy itself;
closing the gap stays a human decision.

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

<!-- code-anchor: bin/codex-exec @ fda5814 -->
## `codex-exec` — reliable non-interactive codex

```
codex-exec <prompt-file> [--cd <dir>] [--write] [--full] [--timeout <secs>] [--model <id>]
```

A thin wrapper around `codex exec` that fixes two footguns and serializes
dispatches:

- **Stdin**: `codex exec` reads stdin, so a backgrounded/cron call without
  redirection can hang forever. This wrapper always runs with `</dev/null`.
- **Sandbox**: on Ubuntu 24.04+, unprivileged user namespaces are
  AppArmor-restricted, which breaks codex's bubblewrap sandbox unless you add
  an AppArmor profile for `bwrap` — see "Host setup" in
  [WORKING-METHOD.md](WORKING-METHOD.md). With that fix in place the real
  sandbox works, so this wrapper never falls back to a `--dangerously-bypass`
  flag.
- **Serialization**: only one `codex-exec` runs at a time on the box. Before
  dispatching, it takes an `flock` lease on `~/.codex/codex-exec.lock` (fd 9,
  opened with `exec 9>>...` so the lock survives the `exec` into `codex` and
  auto-releases whenever the process exits — including `SIGKILL`, so no lock
  can go stale). If another `codex-exec` is already holding the lease, this
  one blocks until it's free; a backlog of waiters drains one-by-one in
  arrival order. This exists to protect a shared codex model quota from two
  runs competing for tokens, and to avoid collisions between codex processes
  running in parallel worktrees. A run that waits more than 25 hours
  (`flock -w 90000`) gives up waiting and proceeds unserialized rather than
  blocking forever, logging a `WARN` to stderr. If `flock` isn't installed, or
  the lock file can't be opened, it also proceeds unserialized with a `WARN`.
  Set `CODEX_EXEC_NOLOCK=1` to skip the lease entirely for a single call —
  e.g. when you deliberately want several codex dispatches running in
  parallel, such as a multi-lens review panel.

The prompt is read from `<prompt-file>` (not a shell argument), so quoting
stays sane and the transcript is reviewable. Flags:

| Flag | Effect |
|---|---|
| `--cd <dir>` | Working directory for codex (default: `$HOME`) |
| `--write` | Sandbox `workspace-write` instead of the default `read-only` |
| `--full` | Sandbox `danger-full-access` (network/system access) — for implementation phases that need it |
| `--timeout <secs>` | Kill codex if it hasn't finished after this many seconds (default 600) |
| `--model <id>` | Pin the model for this dispatch only (passed through as `codex exec --model <id>`); omit to use codex's own default |

It also sets `UV_CACHE_DIR` to `/tmp/uv-cache-codex` if unset — the
`workspace-write` sandbox denies `~/.cache`, and `uv` hangs acquiring a cache
lock without a writable cache dir.

Env vars (not flags — set these in the calling shell/cron line):

| Var | Default | Meaning |
|---|---|---|
| `CODEX_EXEC_NOLOCK` | unset | Set to `1` to skip the serialization lease for this call and dispatch immediately, even if another `codex-exec` is running |
| `CODEX_EXEC_LOCK` | `~/.codex/codex-exec.lock` | Path to the lease file; override only if you deliberately want a separate serialization group |
