# {{REPO_NAME}} — working conventions

<!-- Seeded by gardener (https://github.com/archivetechie/gardener). Fill the blanks,
     delete what doesn't apply, keep it short — this file is read by AI assistants
     at the start of every session. -->

## What this repo is
{{ONE_PARAGRAPH: what the project does, its architecture in two sentences, and
where the entry points are.}}

## The working pattern (do not fight it)
The owner brainstorms with Claude → design lands as a doc in `docs/` → an
**implementation prompt** is written to `docs/` (when work spans repos, write one
prompt per repo referencing the same normative contract file so the implementers
need no live coordination) → an implementation agent (e.g. codex) executes it →
verification gates the result. Claude designs and reviews; the implementer builds.
The owner hops between threads by design — keep thread state in tasks/notes so
hops are cheap.

## Hygiene is automated

Gardener creates recovery refs without changing the active branch or index.
Documentation runs in isolated candidates and needs independent review before
local integration. Use `sitrep` and primary attempt evidence for current status.
Public documentation belongs here; designs, prompts, reviews and operational
journals belong in the configured private hub. Commit reviewed work promptly.

## Docs lifecycle
`docs/INDEX.md` is the registry: every doc is `current | implemented | superseded |
historical`. Completed/superseded docs move to `docs/historical/` (history kept,
entirely separate from current docs). Update INDEX when adding or completing a doc.

## Running / verifying
{{HOW_TO_RUN: build command, test command, and the ONE verification command whose
green output means "done" (e.g. `make test`, `make suite`).}}

## Secrets
Never commit credentials; never echo them into files or logs. If a secret transits
a chat or email, recommend rotation.
