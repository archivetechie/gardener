# Agent conventions for {{REPO_NAME}} (codex and other implementers)

<!-- Seeded by gardener. Read CLAUDE.md for the repo model. -->

## Definition of done — every task, no exceptions
1. **Run what you changed** — the project's test/verification commands (see
   CLAUDE.md "Running / verifying"). Paste the actual outputs into your final
   summary. A claim without output is not done.
2. **Commit your work** with a descriptive message and your provenance line.
   **Never leave the working tree dirty** — if blocked mid-task, commit WIP to a
   branch named `wip/<topic>` and say so.
3. **Update `docs/INDEX.md`** — mark the prompt you implemented as `implemented`;
   add any docs you created.
4. Don't touch `docs/archive/` or any generated files the project regenerates.

## Background automation you should know about
A `gardener` cron may auto-commit idle changes (`auto(checkpoint)` messages).
Do your own commits promptly so the checkpointer never snapshots half-done state.
