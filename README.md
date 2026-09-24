# Gardener

Gardener records local recovery checkpoints, audits documentation in isolated
worktrees, and renders workflow health from primary attempt records. The normal
entry points remain in `bin/`; their shared implementation is `gardener_workflow/`.

## Recovery

`gardener run` snapshots changed trees using a temporary index and separate
`refs/checkpoints/` references. It preserves the active branch, index, and working
files, including deletions. Identical trees are deduplicated; the newest 50 local
checkpoint refs are retained. These are recovery snapshots, not reviewed commits.

No default branch is pushed and no remote branch is deleted. Backups are disabled
unless a repository explicitly names an absolute local bare repository as its
checkpoint destination. Until then, recovery is local to this machine.

## Documentation and evidence

Docsmith proposes edits in a disposable worktree. The parent validates the whole
candidate against configured documentation roots and protected paths. A separate
Opus 5.5 session reviews the exact commit; only an approved candidate whose base
still matches a clean active `main` can be fast-forwarded locally. Protected
specifications, instructions, private working records, and non-document changes
block promotion. Failed reviews remain visible in the candidate backlog and
expire after seven days.

The model runs through Bubblewrap with read-only tools and a separate disposable
authentication directory. Git/SSH credentials and the source checkout are absent.
Failure to establish the sandbox fails the job; there is no unrestricted fallback.
Changed runtime inputs receive priority in the documentation queue; older audits
provide a periodic fallback. Anchors validate both the referenced commit and path.

Daily and weekly jobs summarize primary evidence. A deterministic exporter writes
versioned records to the explicitly private hub when its `main` is clean. Missing
terminal records, skips, unknown identity, failures, and expired candidates remain
visible; they cannot become passing verification through a prose summary. Weekly
exports update `BRIEF.md` and link the model commentary separately from primary
status. Facts export even when the model job fails, preserving its failure status.
`BRIEF_MODEL` defaults to `opus`; daily jobs use
`DAILY_MODEL`. Index/branch maintenance is reported for review; automatic archival
and remote branch deletion are intentionally removed. Each export retains today’s
records, the latest attempt and last pass per job, plus evidence used for issue
closure. Corrupt evidence remains an explicit error without blocking valid exports.

## Install and use

```sh
export PATH="$PWD/bin:$PATH"
gardener add /path/to/project
gardener install --daily
gardener status
workflow health
workflow candidates
```

Python 3.11+, Git, and `flock` support are required. Model jobs also require the
Claude CLI and Bubblewrap. See [Getting started](GETTING-STARTED.md) for registry,
scheduling, migration, recovery, and verification; [Tools](TOOLS.md) documents
individual commands.

## Working together

[The working method](WORKING-METHOD.md) describes design, independent review,
implementation, and scenario verification. `sitrep` shares context across
repositories, `devup2` manages thread-specific cockpits, and `codex-exec` dispatches
implementers under shared repository ownership. Commands outside that ownership
protocol remain outside its exclusion guarantee. A Linux descriptor broker verifies
peer credentials and process ancestry before handing held locks to descendant tool
processes. Ordinary children receive descriptors directly. The installed Codex
workspace sandbox blocks broker socket connections: nested workflow commands in
`--write` mode fail closed. Run those commands after that agent exits, or use an
explicitly authorized `--full` implementation session; the wrapper never silently
broadens the sandbox. Independent read-only reviewers do not acquire write leases.

Public repositories contain distilled product documentation. Designs, prompts,
reviews, issue ledgers, and journals belong in the private hub. `gardener init`
seeds instruction files only when absent; set `GARDENER_PRIVATE_REPO=1` to also
seed a private documentation index. Existing files are preserved.
