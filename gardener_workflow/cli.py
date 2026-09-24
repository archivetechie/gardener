"""Stable command entry points for checkpointing, evidence, and shared leases."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from .checkpoint import checkpoint
from .documents import anchor_drift, run_agent_job
from .gitstate import git, identity
from .leases import BusyError, Lease, repo_resource, run_child
from .records import Attempt, digest_file, render_health
from .registry import Repository, registry, state_dir, write_registry, config_dir
from .standing import render_active


def housekeep() -> int:
    """Capture recoverable trees without changing user branches or remote refs."""
    attempt = Attempt("housekeep")
    outcomes = []
    code = 0
    try:
        _, repos = registry()
        if not repos:
            raise ValueError("no repositories configured")
        for repo in repos:
            try:
                result = checkpoint(repo)
                outcomes.append({"repository": repo.name, **result})
            except BusyError as exc:
                outcomes.append({"repository": repo.name, "status": "skipped", "reason": str(exc)})
            except (OSError, ValueError, subprocess.CalledProcessError) as exc:
                outcomes.append({"repository": repo.name, "status": "failed", "reason": str(exc)})
                code = 1
    except (OSError, ValueError) as exc:
        outcomes.append({"status": "failed", "reason": str(exc)})
        code = 1
    verdict = "failed" if code else "skipped" if any(x.get("status") == "skipped" for x in outcomes) else "passed"
    output = json.dumps({"attempt_id": attempt.id, "verdict": verdict, "repositories": outcomes}, indent=2)
    print(output)
    state_dir().mkdir(parents=True, exist_ok=True)
    with (state_dir() / "housekeep.log").open("a") as stream:
        stream.write(output + "\n")
    evidence = state_dir() / "checkpoints" / f"{attempt.id}.json"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text(output + "\n")
    attempt.finish(code, verdict, phase="checkpoint", repositories=outcomes,
                   evidence=str(evidence), evidence_sha256=digest_file(evidence))
    return code


def sitrep(since: str) -> int:
    """Render the cross-session report using complete states and primary evidence."""
    hub, repos = registry()
    print(f"── commits since {since} ──")
    for repo in repos:
        if not repo.path.exists():
            print(f"{repo.name}: repository missing")
            continue
        log = git(repo.path, "log", "--oneline", f"--since={since}", "-12").stdout.decode(errors="replace").strip()
        dirty = identity(repo.path)["dirty"]
        if log or dirty:
            print(f"{repo.name}" + (" [uncommitted]" if dirty else ""))
            print(log)
    # Legacy installations may have no explicit hub; inspect each configured
    # standing ledger rather than silently choosing a repository by list order.
    hubs = [hub] if hub else [r.path for r in repos if (r.path / "STANDING.md").exists()]
    for root in hubs:
        print(f"── STANDING: {root.name} ──")
        if (root / "STANDING.md").exists():
            print(render_active(root / "STANDING.md"))
        print("── pending prompts ──")
        indexes = sorted((root / "docs").rglob("INDEX.md")) + sorted((root / "journal").rglob("INDEX.md"))
        for index in indexes:
            for line in index.read_text().splitlines():
                if line.startswith("|") and "prompt" in line.lower() and any(s in line.lower() for s in ("pending", "in-progress", "in progress")):
                    print(f"{index.relative_to(root)}: {line}")
    by_name = {r.name: r.path for r in repos}
    print("── vendored contract drift ──")
    if "remanence" in by_name and "sutradhara" in by_name:
        paths = [by_name[n] / "proto/layer5.proto" for n in ("remanence", "sutradhara")]
        if not all(p.is_file() for p in paths):
            print("layer5.proto: UNKNOWN (source or vendored file missing)")
        else:
            print("layer5.proto: " + ("current" if paths[0].read_bytes() == paths[1].read_bytes() else "DRIFT"))
    else:
        print("layer5.proto: UNKNOWN (repositories not configured)")
    print(render_health())
    from .candidates import backlog
    pending = backlog()
    expired = sum(r["status"] == "expired" for r in pending)
    print(f"Documentation backlog: {len(pending)} candidate(s), {expired} expired")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Dispatch script-compatible commands; failures always retain nonzero exits."""
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("housekeep", "daily", "docsmith", "weekly", "health", "candidates", "journal"):
        sub.add_parser(name)
    review = sub.add_parser("review-candidate")
    review.add_argument("candidate_id")
    status = sub.add_parser("sitrep")
    status.add_argument("since", nargs="?", default="24 hours ago")
    drift = sub.add_parser("drift")
    drift.add_argument("repository", nargs="?", default=".")
    config = sub.add_parser("registry")
    config.add_argument("action", choices=("list", "add", "remove", "migrate"))
    config.add_argument("path", nargs="?")
    config.add_argument("--private", action="store_true")
    config.add_argument("--hub")
    lease = sub.add_parser("lease")
    lease.add_argument("--repo", action="append", default=[])
    lease.add_argument("--resource", action="append", default=[])
    lease.add_argument("child", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    try:
        if args.command == "review-candidate":
            from .candidates import retry_review
            return retry_review(args.candidate_id)
        if args.command == "registry":
            with Lease(["config:" + str(config_dir().resolve())]):
                hub, repos = registry()
                if args.action == "migrate" and not args.hub and hub is None:
                    raise ValueError("migration requires an explicit --hub")
                if args.action in {"add", "remove"} and not args.path:
                    raise ValueError("repository path required")
                path = Path(args.path).expanduser().resolve() if args.path else None
                if args.action == "add":
                    if path is None:
                        raise ValueError("repository path required")
                    repo_resource(path)
                    if any(r.name == path.name and r.path != path for r in repos):
                        raise ValueError("repository basename collides with a configured name")
                    repos = [r for r in repos if r.path != path] + [Repository(path.name, path, public=not args.private)]
                elif args.action == "remove":
                    repos = [r for r in repos if r.path != path]
                if args.action != "list":
                    if args.hub:
                        hub = Path(args.hub).expanduser().resolve()
                    write_registry(hub, repos)
                print(json.dumps({"hub": str(hub) if hub else None, "repositories": [{"name": r.name, "path": str(r.path), "public": r.public, "docsmith": r.docsmith} for r in repos]}, indent=2))
                return 0
        if args.command == "housekeep":
            return housekeep()
        if args.command in {"daily", "docsmith", "weekly"}:
            code = run_agent_job(args.command)
            if args.command in {"daily", "weekly"} and code == 0:
                from .journal import export_journal
                return export_journal()
            return code
        if args.command == "journal":
            from .journal import export_journal
            return export_journal()
        if args.command == "candidates":
            from .candidates import backlog
            rows = backlog()
            print(json.dumps({"count": len(rows), "candidates": rows}, indent=2))
            return 0
        if args.command == "sitrep":
            return sitrep(args.since)
        if args.command == "health":
            print(render_health())
            return 0
        if args.command == "drift":
            code, output = anchor_drift(Path(args.repository).resolve())
            print(output)
            return code
        if args.command == "lease":
            child = args.child[1:] if args.child[:1] == ["--"] else args.child
            if not child:
                parser.error("lease requires a child command after --")
            with Lease(args.resource + [repo_resource(Path(p)) for p in args.repo]):
                return run_child(child).returncode
    except BusyError as exc:
        print(f"BUSY: {exc}")
        return 75
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"FAILED: {exc}")
        return 1
    return 1
