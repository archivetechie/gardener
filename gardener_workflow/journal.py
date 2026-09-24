"""Deterministically export primary attempt evidence into a private journal."""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from .candidates import backlog
from .gitstate import git, git_text, source_snapshot
from .leases import BusyError, Lease, repo_resource
from .records import Attempt, digest_file, read_attempts, render_health, utc_now
from .registry import registry, state_dir


def journal_attempts(attempts: list[dict], today: str, exclude: str) -> list[dict]:
    """Export today's records plus each job's latest and last successful evidence."""
    selected, latest, passed = {}, {}, {}
    for item in attempts:
        ident = item["attempt_id"]
        if ident == exclude:
            continue
        start = item.get("start", {})
        kind = start.get("kind", "unknown")
        latest[kind] = item
        if item["verdict"] == "passed":
            passed[kind] = item
        if start.get("time", "").startswith(today):
            selected[ident] = item
    for item in [*latest.values(), *passed.values()]:
        selected[item["attempt_id"]] = item
    return list(selected.values())


def hygiene_findings(hub: Path, repos: list) -> str:
    """Report index and branch maintenance needs without rewriting their history."""
    lines = ["\n## Maintenance observations", "", "Read-only observations; these do not advance verification dates."]
    for index in sorted((hub / "docs").rglob("INDEX.md")) + sorted((hub / "journal").rglob("INDEX.md")):
        if not index.read_text().strip():
            lines.append(f"- EMPTY documentation registry: `{index.relative_to(hub)}`")
    for repo in repos:
        branch = git(repo.path, "symbolic-ref", "-q", "--short", "HEAD", check=False)
        upstream = git(repo.path, "rev-parse", "--abbrev-ref", "@{upstream}", check=False)
        name = branch.stdout.decode().strip() or "detached"
        if upstream.returncode:
            lines.append(f"- {repo.name}: {name}; upstream unknown")
        else:
            counts = git(repo.path, "rev-list", "--left-right", "--count", "HEAD...@{upstream}").stdout.decode().strip().split()
            lines.append(f"- {repo.name}: {name}; ahead={counts[0]}, behind={counts[1]} relative to locally recorded upstream")
    return "\n".join(lines) + "\n"


def export_journal(kind: str = "daily") -> int:
    """Commit only generated private facts when the hub is clean and still matches."""
    attempt = Attempt("journal-export")
    try:
        hub, repos = registry()
        policy = next((r for r in repos if r.path == hub), None)
        if hub is None or policy is None or policy.public:
            raise ValueError("an explicitly private hub is required")
        with Lease([repo_resource(hub)]):
            source = source_snapshot(hub)
            facts = render_health(exclude={attempt.id}) + "\n## Documentation candidates\n\n" + json.dumps(backlog(), indent=2) + "\n" + hygiene_findings(hub, repos)
            pending = state_dir() / "journal-pending.md"
            pending.parent.mkdir(parents=True, exist_ok=True)
            pending.write_text(facts)
            pending.chmod(0o600)
            if source["dirty"] or source["head_ref"] != "refs/heads/main":
                attempt.finish(0, "skipped", phase="promotion", reason="hub is dirty or main is not active", evidence=str(pending), evidence_sha256=digest_file(pending))
                return 0
            with tempfile.TemporaryDirectory(prefix="gardener-journal-") as temporary:
                worktree = Path(temporary) / "worktree"
                git(hub, "worktree", "add", "--detach", str(worktree), source["head"])
                try:
                    relative = "journal/automation/daily/" + utc_now()[:10] + ".md"
                    path = worktree / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    evidence_root = worktree / "journal/automation/evidence"
                    evidence_root.mkdir(parents=True, exist_ok=True)
                    attempts, errors = read_attempts()
                    # Corruption stays visible in render_health; valid jobs still export.
                    # Invalid records cannot be promoted to passing evidence.
                    # Export actual records cited by today's summary, retaining
                    # start/source/terminal identity across local log retention.
                    # Retain all records referenced by the rendered health,
                    # including the last older attempt of an infrequent job.
                    for item in journal_attempts(attempts, utc_now()[:10], attempt.id):
                        if item["attempt_id"] == attempt.id:
                            continue
                        for event in ("start", "source", "terminal"):
                            if event in item:
                                original = Path(item[event]["record"])
                                target = evidence_root / f"{item['attempt_id']}-{event}.json"
                                shutil.copyfile(original, target)
                                facts = facts.replace(str(original), str(target.relative_to(worktree)))
                        terminal = item.get("terminal", {})
                        artifact = terminal.get("evidence")
                        if isinstance(artifact, str) and terminal.get("evidence_sha256"):
                            original = Path(artifact)
                            if original.is_file() and digest_file(original) == terminal["evidence_sha256"]:
                                target = evidence_root / f"{item['attempt_id']}-artifact{original.suffix}"
                                shutil.copyfile(original, target)
                                facts += f"\n- Preserved artifact: `{target.relative_to(worktree)}`; SHA256 `{digest_file(target)}`\n"
                                if original.suffix == ".json":
                                    receipt = json.loads(original.read_text())
                                    nodes = list(enumerate(receipt)) if isinstance(receipt, list) else [(None, receipt)]
                                    for number, node in nodes:
                                        if not isinstance(node, dict):
                                            continue
                                        for key in ("log", "checks"):
                                            linked = Path(node.get(key, ""))
                                            if linked.is_file() and node.get(key + "_sha256") == digest_file(linked):
                                                suffix = "" if number is None else f"-{number}"
                                                shutil.copyfile(linked, evidence_root / f"{item['attempt_id']}{suffix}-{key}{linked.suffix}")
                    path.write_text(facts)
                    paths = ["journal/automation/daily", "journal/automation/evidence"]
                    if kind == "weekly":
                        brief = "# Weekly program brief\n\n" + facts
                        weekly = next((a for a in reversed(attempts) if a.get("start", {}).get("kind") == "weekly" ), None)
                        if weekly and weekly["verdict"] != "passed":
                            brief += f"\nModel commentary unavailable: attempt `{weekly['attempt_id']}` is {weekly['verdict']}.\n"
                        elif weekly:
                            artifact = evidence_root / f"{weekly['attempt_id']}-artifact.json"
                            if artifact.is_file():
                                brief += f"\nModel commentary (not independently reviewed; primary records above determine status): [{weekly['attempt_id']}]({artifact.relative_to(worktree)}).\n"
                        (worktree / "BRIEF.md").write_text(brief)
                        paths.append("BRIEF.md")
                    git(worktree, "add", "--", *paths)
                    git(worktree, "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false", "commit", "-m", "auto(journal): export primary workflow evidence", "-m", "Provenance: Gardener deterministic evidence renderer")
                    candidate = git_text(worktree, "rev-parse", "HEAD")
                    if source_snapshot(hub) != source:
                        raise ValueError("hub changed before deterministic journal promotion")
                    git(hub, "-c", "core.hooksPath=/dev/null", "merge", "--ff-only", candidate)
                finally:
                    git(hub, "worktree", "remove", "--force", str(worktree), check=False)
            attempt.finish(0, "passed", phase="promotion", candidate=candidate, evidence=str(hub / relative), evidence_sha256=digest_file(hub / relative))
            return 0
    except BusyError as exc:
        attempt.finish(0, "skipped", phase="eligibility", reason=str(exc))
        return 0
    except Exception as exc:
        print(f"FAILED journal export: {exc}")
        attempt.finish(1, "failed", phase="export", error=str(exc))
        return 1
