"""Validate anchored documentation and run model authors inside isolated candidates.

The model has read-only file tools, no shell, and a filesystem namespace containing
only code candidates, read-only reference repositories, and disposable auth state.
The parent applies structured proposals and owns Git, policy checks, and evidence.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path, PurePosixPath

from .gitstate import changed_paths, git, git_text, identity, source_snapshot
from .leases import BusyError, Lease, repo_resource
from .records import Attempt, digest_file, render_health
from .registry import Repository, registry, state_dir


def anchor_drift(repo: Path) -> tuple[int, str]:
    """Validate anchor commits AND paths, including working-content drift."""
    problems = []
    total = 0
    paths = git(repo, "ls-files", "-z", "*.md").stdout.split(b"\0")
    for raw in paths:
        if not raw:
            continue
        name = os.fsdecode(raw)
        path = repo / name
        if not path.is_file() or path.is_symlink():
            continue
        for number, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
            if "code-anchor:" not in line:
                continue
            match = re.search(r"<!--\s*code-anchor:\s*(.*?)\s*-->", line)
            if not match:
                problems.append(f"MALFORMED {name}:{number}")
                continue
            spec = match.group(1).strip()
            if spec == "none":
                continue
            total += 1
            if " @ " not in spec:
                problems.append(f"MALFORMED {name}:{number}: {spec}")
                continue
            sources, base = spec.rsplit(" @ ", 1)
            if not re.fullmatch(r"[0-9a-fA-F]{7,64}", base) or git(repo, "cat-file", "-e", f"{base}^{{commit}}", check=False).returncode:
                problems.append(f"UNKNOWN-BASE {name}:{number}: {base}")
                continue
            for source in sources.split():
                relative = PurePosixPath(source)
                if relative.is_absolute() or ".." in relative.parts or source.startswith("-"):
                    problems.append(f"INVALID-PATH {name}:{number}: {source}")
                    continue
                if git(repo, "cat-file", "-e", f"{base}:{source}", check=False).returncode or git(repo, "cat-file", "-e", f"HEAD:{source}", check=False).returncode:
                    problems.append(f"MISSING-PATH {name}:{number}: {source} @ {base}")
                    continue
                if git(repo, "diff", "--name-only", base, "--", source).stdout:
                    problems.append(f"STALE {name}:{number}: {source} @ {base}")
    problems.append(f"docsmith-drift: {total} anchor(s) checked, {len(problems)} stale/problem")
    return (1 if len(problems) > 1 else 0), "\n".join(problems)


def allowed_doc(repo: Repository, name: str, worktree: Path) -> bool:
    """Fail closed on protected paths, traversal, and symlinks at any depth."""
    relative = PurePosixPath(name)
    if relative.is_absolute() or ".." in relative.parts or not name or name.startswith("-"):
        return False
    if any(fnmatch.fnmatch(name, pattern) for pattern in repo.protected):
        return False
    if not any(fnmatch.fnmatch(name, pattern) for pattern in repo.docs):
        return False
    path = worktree / name
    if not path.resolve().is_relative_to(worktree.resolve()):
        return False
    return not any(p.is_symlink() for p in (path, *path.parents) if p != worktree.parent)


def _model_environment() -> dict[str, str]:
    """Keep model authentication separate from Git/SSH credentials."""
    keep = {"LANG", "LC_ALL", "TZ", "HOME", "USER", "TERM", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY"}
    result = {k: v for k, v in os.environ.items() if k in keep or k.startswith(("ANTHROPIC_", "CLAUDE_"))}
    result.update({"PATH": "/usr/bin:/bin", "GIT_CONFIG_GLOBAL": "/dev/null",
                   "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0",
                   "GIT_SSH_COMMAND": "/usr/bin/false", "GIT_ASKPASS": "/usr/bin/false"})
    return result


def model_result(worktree: Path, prompt: str, model: str, schema: dict, log: Path) -> tuple[int, dict | None]:
    """Run a read-only model in a filesystem sandbox and parse structured output."""
    binary = shutil.which("claude")
    sandbox = shutil.which("bwrap")
    if not binary or not sandbox:
        log.write_text("FAILED: claude and bubblewrap are required; no unsafe fallback\n")
        return 127, None
    with tempfile.TemporaryDirectory(prefix="gardener-agent-") as temporary:
        scratch = Path(temporary)
        auth = scratch / "claude"
        auth.mkdir(mode=0o700)
        credential = Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude"))) / ".credentials.json"
        if credential.is_file():
            shutil.copyfile(credential, auth / ".credentials.json")
            (auth / ".credentials.json").chmod(0o600)
        command = [sandbox, "--unshare-user", "--unshare-pid", "--die-with-parent", "--new-session",
                   "--ro-bind", "/usr", "/usr", "--ro-bind", "/etc", "/etc",
                   "--symlink", "usr/bin", "/bin", "--symlink", "usr/lib", "/lib",
                   "--symlink", "usr/lib64", "/lib64", "--proc", "/proc", "--dev", "/dev",
                   "--tmpfs", "/tmp", "--dir", str(Path.home()),
                   "--bind", str(worktree), "/workspace", "--bind", str(scratch), "/state",
                   "--ro-bind", str(Path(binary).resolve()), "/agent",
                   "--chdir", "/workspace", "--setenv", "CLAUDE_CONFIG_DIR", "/state/claude"]
        resolver = Path("/etc/resolv.conf").resolve()
        if not resolver.is_relative_to("/etc"):
            command.extend(["--ro-bind", str(resolver), str(resolver)])
        command.extend(["--", "/agent", "-p", "--model", model, "--safe-mode", "--restricted",
                        "--permission-mode", "dontAsk", "--strict-mcp-config", "--no-session-persistence",
                        "--tools", "Read,Grep,Glob", "--allowedTools", "Read", "Grep", "Glob",
                        "--output-format", "json", "--json-schema", json.dumps(schema)])
        result = subprocess.run(command, input=prompt, text=True, capture_output=True,
                                env=_model_environment(), cwd=worktree)
        log.write_text(result.stdout + "\n" + result.stderr + f"\nagent exit {result.returncode}\n")
        if result.returncode:
            return result.returncode, None
        try:
            response = json.loads(result.stdout)
            if response.get("is_error"):
                raise ValueError("model reported error")
            payload = response.get("structured_output")
            if payload is None:
                payload = json.loads(response.get("result", ""))
            if not isinstance(payload, dict):
                raise ValueError("model result is not an object")
            return 0, payload
        except (ValueError, TypeError, AttributeError) as exc:
            with log.open("a") as stream:
                stream.write(f"FAILED: unusable model result: {exc}\n")
            return 1, None


EDIT_SCHEMA = {"type": "object", "additionalProperties": False,
               "properties": {"summary": {"type": "string"}, "edits": {"type": "array", "items": {
                   "type": "object", "additionalProperties": False,
                   "properties": {"path": {"type": "string"}, "content": {"type": ["string", "null"]}},
                   "required": ["path", "content"]}}}, "required": ["summary", "edits"]}


def run_agent_job(kind: str) -> int:
    """Keep all agent writes isolated, propagating failure and preserving candidates."""
    attempt = Attempt(kind)
    state = state_dir()
    directory = state / "docsmith" if kind == "docsmith" else state
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    log = directory / ("docsmith.log" if kind == "docsmith" else f"{kind}.log")
    log.touch(mode=0o600, exist_ok=True)
    log.chmod(0o600)
    jobs = state / "candidates" / attempt.id
    jobs.mkdir(parents=True, mode=0o700)
    candidate_worktree = None
    candidate_repo = None
    try:
        hub, repos = registry()
        choices = [r for r in repos if r.docsmith] if kind == "docsmith" else [r for r in repos if r.path == hub] or repos[:1]
        if not choices:
            raise ValueError("no eligible repository configured")
        cursor = directory / "cursor"
        previous = cursor.read_text().strip() if cursor.exists() else ""
        names = [str(r.path) for r in choices]
        start = (names.index(previous) + 1) % len(choices) if previous in names else 0
        choices = choices[start:] + choices[:start]
        for repo in choices:
            if not repo.path.exists():
                continue
            with Lease([repo_resource(repo.path)]):
                baseline = identity(repo.path)
                if baseline["dirty"]:
                    continue
                worktree = jobs / "worktree"
                git(repo.path, "worktree", "add", "--detach", str(worktree), baseline["head"])
                candidate_worktree, candidate_repo = worktree, repo.path
                snapshots = {str(r.path): source_snapshot(r.path) for r in repos if (r.path / ".git").exists()}
                _, drift = anchor_drift(worktree)
                prompt = (f"You are the {kind} author. Return structured JSON proposals only. "
                          "Read source under /workspace. Do not execute commands or change files. "
                          "Normative specs and instructions are authoritative; report disagreements, do not erase them. "
                          f"Allowed documentation patterns: {repo.docs}; protected: {repo.protected}. "
                          "Use repository-relative edit paths. No secrets or private working records in public docs. "
                          "If nothing needs changing return edits=[]. Explain only verified claims.\n\n"
                          f"Primary automation evidence:\n{render_health()}\nAnchor findings:\n{drift}\n")
                if kind != "docsmith":
                    prompt += "This is a private operational summary: return no file edits; explain the supplied primary evidence.\n"
                model = os.environ.get("DOCSMITH_MODEL" if kind == "docsmith" else "DAILY_MODEL", "claude-sonnet-5")
                code, payload = model_result(worktree, prompt, model, EDIT_SCHEMA, log)
                for source, old in snapshots.items():
                    if source_snapshot(Path(source)) != old:
                        raise ValueError(f"source checkout changed during agent job: {source}")
                if code:
                    attempt.finish(code, "failed", phase="agent", log=str(log), log_sha256=digest_file(log))
                    return code
                unauthorized = [p for p in changed_paths(worktree) if not allowed_doc(repo, p, worktree)]
                if unauthorized:
                    raise ValueError(f"NON-DOC/protected candidate output: {unauthorized}")
                assert payload is not None
                edits = payload.get("edits")
                if not isinstance(edits, list) or kind != "docsmith" and edits:
                    raise ValueError("invalid edit proposal for this job")
                seen = set()
                for edit in edits:
                    if not isinstance(edit, dict) or not isinstance(edit.get("path"), str):
                        raise ValueError("invalid edit object")
                    name = edit["path"]
                    if name in seen or not allowed_doc(repo, name, worktree):
                        raise ValueError(f"NON-DOC/protected edit proposal: {name}")
                    seen.add(name)
                    target = worktree / name
                    if edit["content"] is None:
                        target.unlink(missing_ok=True)
                    elif isinstance(edit["content"], str):
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_text(edit["content"])
                    else:
                        raise ValueError("document content must be a string or null")
                changed = changed_paths(worktree)
                if any(not allowed_doc(repo, p, worktree) for p in changed):
                    raise ValueError("NON-DOC/protected output after applying proposal")
                candidate = None
                if changed:
                    git(worktree, "add", "--", *changed)
                    git(worktree, "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false", "commit",
                        "-m", "auto(docs): isolated documentation candidate", "-m", "Provenance: Gardener documentation author")
                    candidate = git_text(worktree, "rev-parse", "HEAD")
                    git(repo.path, "update-ref", f"refs/candidates/{kind}/{attempt.id}", candidate)
                (jobs / "proposal.json").write_text(json.dumps(payload, indent=2) + "\n")
                (jobs / "candidate.json").write_text(json.dumps({"repository": repo.name, "path": str(repo.path),
                    "base": baseline, "candidate": candidate, "kind": kind, "review": "pending" if candidate else "not-required"}, indent=2) + "\n")
                git(repo.path, "worktree", "remove", str(worktree))
                candidate_worktree = None
                cursor.write_text(str(repo.path))
                attempt.finish(0, "candidate" if candidate else "passed", phase="candidate" if candidate else "no-change",
                               candidate=candidate, source=baseline, log=str(log), log_sha256=digest_file(log))
                return 0
        attempt.finish(0, "skipped", phase="eligibility", reason="all repositories are busy, dirty, or absent")
        log.write_text("skipped: no eligible clean repository\n")
        return 0
    except (OSError, ValueError, KeyError, BusyError, subprocess.CalledProcessError) as exc:
        with log.open("a") as stream:
            stream.write(f"FAILED: {exc}\n")
        if not attempt.finished:
            attempt.finish(1, "failed", phase="candidate-guard", error=str(exc), log=str(log), log_sha256=digest_file(log))
        return 1
    finally:
        if candidate_worktree is not None:
            # Rejected content is disposable; the captured log and proposal remain.
            git(candidate_repo, "worktree", "remove", "--force", str(candidate_worktree), check=False)
