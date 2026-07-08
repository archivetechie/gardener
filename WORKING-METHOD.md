# The working method

One human + a design AI (Claude) + an implementation AI (codex) building a
multi-repo system, with the human deliberately hopping between threads. This file
captures the *whole* method — gardener's hygiene automation is one piece of it.
Evolved in production July 2026; the canonical, living process docs stay in the
hub repo's `docs/` (this file is the portable map).

## The loop

```
human + Claude brainstorm ─▶ design doc (in the repo it pertains to)
        ─▶ PANEL REVIEW: 3-5 blind parallel lenses (security/failure-modes/
           contract/cost/feasibility; ≥1 lens on the OTHER model family)
           → one consolidated report → human answers only business-tagged items
           → single fold → ONE verify round (hard cap 2) → design FROZEN
        ─▶ prompt set: per-repo codex prompts; shared contract single-sourced
           as contract-<name>.md (referenced, never inlined); every set includes
           its VERIFICATION MEMBER (test scenario, declared cover, or browser-QA
           gate for UI)
        ─▶ codex implements (phased commits, self-verifies, pastes outputs)
        ─▶ DIFF GATE: independent review of the actual diff before a prompt is
           archived "implemented"
        ─▶ scenarios/harness verify from a clean slate
```

Key principles discovered the hard way:
- **Feedback beats feedforward.** The design→implement half was always fast; the
  leaks were on the feedback side: standing issues becoming wallpaper, coverage
  claims going stale, verification trailing implementation. Hence: a STANDING.md
  ledger with an escalation rule (open 3+ days ⇒ cut a fix prompt or explicitly
  accept), a nightly test-suite cron, and the scenario-or-cover rule.
- **Drift-sensitive encodings get one shared implementation** (a crate/library
  both sides call) or golden fixtures — never "byte-match the other side" by
  prose in a prompt.
- **Parallel cheap diversity finds problems; expensive intelligence decides.**
  Panels of mid-tier lenses out-find a single top-tier reviewer; save the top
  tier for synthesis, strategy, and genuinely hard design (model-economy doc).

## Sessions: thread-keyed cockpits (`bin/devup2`)

Work is arc-shaped and spans repos, so agent sessions are keyed to THREADS, not
repos: 2-3 tmux "cockpit" sessions (`main`, `side`), each with a Claude window
rooted in the **hub repo** (where the assistant's persistent project memory and
conventions live — starting elsewhere silently loses them) and a codex window.
Claude works across all repos from the cockpit; codex gets self-contained,
repo-addressed prompt files, so it needs no conversational continuity at all.

The failure mode this replaces: one session per repo × two tools (≈12 agents),
where cross-repo work done from one session left every other session's
conversation blind → constant rediscovery. "Wrong-session" work was never a
correctness problem (artifacts land in the right repos); it was a *sync* problem
— solved by a bus, not by more sessions.

## The context bus (`bin/sitrep`)

Deterministic, no-LLM, ~1s report any session runs at start or after a gap:
last-24h commits across every managed repo (+ dirty counts), open STANDING rows,
board headline/halts, pending prompts, today's journal. Durable state stays in
the repos (docs INDEX, STANDING, journal, git history); sitrep makes it ambient.
Wire it into the hub repo's CLAUDE.md and AGENTS.md so both agents use it.

## Hygiene (the original gardener core)

- `housekeep.sh` every 2h: idle-aware auto-commit, push (never force), prune
  merged branches. Agents commit at every green milestone; the checkpointer is
  the backstop, not the habit.
- `daily.sh` once a day (LLM judgment, cheap model): docs INDEX/archive
  maintenance, push repair, regression call-outs, STANDING.md upkeep, journal.
- Weekly brief (top-tier model, bounded turns): state-of-program, ranked next
  moves, the business-decision queue — the one scheduled top-tier spend.

## Dispatching codex reliably (`bin/codex-exec`)

Non-interactive codex from scripts/cockpits: prompt from a file, stdin guarded
(`</dev/null` — `codex exec` reads stdin and hangs otherwise), sandbox ON.

**Host setup (Ubuntu 24.04+):** unprivileged user namespaces are AppArmor-
restricted, which breaks codex's bubblewrap sandbox and Chromium sandboxes.
Fix per-binary (don't disable the hardening globally) — e.g.
`/etc/apparmor.d/bwrap`:

```
abi <abi/4.0>,
include <tunables/global>
profile bwrap /usr/bin/bwrap flags=(unconfined) {
  userns,
  include if exists <local/bwrap>
}
```

then `apparmor_parser -r /etc/apparmor.d/bwrap`. Same pattern for a headless-
chrome binary if browser QA runs on the host.

## Conventions that hold it together

- Docs live in the repo they pertain to; every repo has a `docs/INDEX.md`
  registry with statuses (`current | implemented | superseded | historical`);
  implemented prompts move to `docs/historical/`, entirely separate from
  current docs.
- `AGENTS.md` per repo = the implementation agent's definition of done: run what
  you changed and paste outputs; always commit; scenario-or-cover for new
  capabilities; contracts referenced, never inlined; expect the diff gate.
- Assistant memory: durable, per-hub-repo; corrections and incidents become
  memory entries so lessons survive session boundaries.
- Everything the human shouldn't have to remember is either a cron, a ledger
  with an escalation rule, or a line in CLAUDE.md/AGENTS.md.
