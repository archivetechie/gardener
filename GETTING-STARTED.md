# Getting started

Gardener's checkpoints protect local work without adding WIP to the active branch.
Documentation candidates are reviewed separately before local integration. Read
[README.md](README.md) for the safety boundaries and [TOOLS.md](TOOLS.md) for flags.

## Install

Clone the repository, add its `bin/` directory to your PATH, and register projects:

```sh
gardener add /path/to/project
gardener add /path/to/private-hub --private
workflow registry migrate --hub /path/to/private-hub
gardener install --daily
```

Python 3.11+, Git and a filesystem supporting advisory file locks are required.
Claude CLI authentication and Bubblewrap are needed only for model jobs. Credentials
for Git/SSH are not passed into model jobs. Remote repository publishing is an
explicit operator action outside scheduled hygiene.

## Configuration

The canonical registry is `~/.config/gardener/repositories.toml`. The old `repos`
and `docsmith-repos` lists remain a read-only fallback until migration. Do not edit
both formats after migrating. `workflow registry list` shows the effective policy.

```toml
hub = "/path/to/private-hub"

[repository.project]
path = "/path/to/project"
public = true
docsmith = true
docs = ["README.md", "docs/**/*.md", "docs/*.md"]
```

Omitted protected paths use conservative defaults, including specifications,
instructions, contracts, prompts, reviews, archives, and private journals. The hub
must have its own entry with `public = false`. Optional `checkpoint_remote` accepts
an absolute path to a local bare backup repository, never a public `origin`.

`GARDENER_CONFIG_DIR`, `GARDENER_STATE_DIR`, and `GARDENER_RECORDS_DIR` provide
isolated roots for tests. State defaults to `~/.local/state/gardener`. Runtime
records default to the private hub's ignored `journal/automation/events/`; durable
summaries and exported evidence are versioned separately.

## Scheduling

`gardener install` installs housekeeping every two hours. `--daily` adds the daily
job at 02:30. The optional documentation job can be scheduled separately:

```cron
45 3 * * * /path/to/gardener/bin/docsmith.sh
```

Daily/weekly jobs export evidence only when the private hub is clean. Docsmith
skips dirty repositories, prefers changed runtime inputs, and then visits the
oldest audited repository. Repository leases prevent cooperating writers from
colliding. Runtime attempts retain explicit skip/failure records even if cron's
output is redirected. `workflow weekly` runs the weekly summary entry point.

## Verify and recover

Run `gardener run`, then `workflow health`. Check that the latest attempt has a
terminal record and inspect its referenced evidence. A skip is not a successful
verification run. `workflow candidates` shows pending, rejected and expired work;
`workflow review-candidate ID` retries independent review while the candidate is
unexpired. The job owner is the workflow maintainer; review is pinned to Opus 5.5.

Recovery snapshots are visible with:

```sh
git for-each-ref refs/checkpoints/ --format='%(refname) %(objectname)'
git show CHECKPOINT_REF
git worktree add --detach /path/to/recovery CHECKPOINT_REF
```

Review recovery contents before integrating them. Housekeeping does not resolve
divergent branches or publish commits. Inspect `workflow health` for incomplete
attempts, repeated failures, missing registry entries, or absent source identity.
`docsmith-drift /path/to/project` reports stale and malformed anchors.

To disable scheduling, remove the Gardener entries using `crontab -e`. Preserve
checkpoint refs and state until you have recovered any work you need. Deleting
the installation does not remove refs from managed repositories.
