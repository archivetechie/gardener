"""Read Git and working-content identities without refreshing the user's index."""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path


def git(repo: Path, *args: str, env: dict | None = None, **kwargs) -> subprocess.CompletedProcess:
    """Invoke Git without optional index writes or shell interpolation."""
    child_env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "TZ": "UTC", **(env or {})}
    return subprocess.run(["git", "-C", str(repo), *args], env=child_env,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          check=kwargs.pop("check", True), **kwargs)


def git_text(repo: Path, *args: str) -> str:
    """Return a checked UTF-8 Git result with its trailing newline removed."""
    return git(repo, *args).stdout.decode().strip()


def index_path(repo: Path) -> Path:
    """Resolve the real index of either an ordinary or a linked worktree."""
    value = Path(git_text(repo, "rev-parse", "--git-path", "index"))
    return value if value.is_absolute() else repo / value


def changed_paths(repo: Path) -> list[str]:
    """Enumerate tracked changes and every untracked file, with NUL-safe names."""
    tracked = git(repo, "diff", "--name-only", "-z", "HEAD", "--").stdout
    untracked = git(repo, "ls-files", "--others", "--exclude-standard", "-z").stdout
    return sorted({os.fsdecode(p) for p in (tracked + untracked).split(b"\0") if p})


def file_bytes(path: Path) -> bytes:
    """Represent file content, symlink targets, and deletions distinctly."""
    if path.is_symlink():
        return b"symlink\0" + os.fsencode(os.readlink(path))
    if path.is_file():
        return b"file\0" + path.read_bytes()
    if not path.exists():
        return b"deleted\0"
    return b"directory\0"


def identity(repo: Path, *, runtime: bool = False) -> dict:
    """Capture revision and working bytes; runtime identity excludes prose only."""
    head = git_text(repo, "rev-parse", "HEAD")
    status = git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all").stdout
    changes = changed_paths(repo)
    dirty_hash = hashlib.sha256(status)
    for name in changes:
        dirty_hash.update(os.fsencode(name) + b"\0" + file_bytes(repo / name))
    index = index_path(repo)
    result = {
        "head": head, "tree": git_text(repo, "rev-parse", "HEAD^{tree}"),
        "dirty": bool(status), "work_digest": dirty_hash.hexdigest(),
        "index_digest": hashlib.sha256(index.read_bytes()).hexdigest() if index.exists() else None,
    }
    if runtime:
        paths = git(repo, "ls-files", "-z", "--cached", "--others", "--exclude-standard").stdout
        source_hash = hashlib.sha256()
        for name in sorted({os.fsdecode(p) for p in paths.split(b"\0") if p}):
            if name.endswith(".md") or name.startswith(("journal/", "reports/", "logs/", "docs/")):
                continue
            path = repo / name
            source_hash.update(os.fsencode(name) + b"\0")
            if path.is_file() and not path.is_symlink():
                with path.open("rb") as stream:
                    source_hash.update(hashlib.file_digest(stream, "sha256").digest())
            else:
                source_hash.update(file_bytes(path))
        result["runtime_digest"] = source_hash.hexdigest()
    return result


def source_snapshot(repo: Path) -> dict:
    """Include refs and stash history in the unattended-author mutation guard."""
    return {**identity(repo),
            "refs": git_text(repo, "for-each-ref", "--format=%(refname) %(objectname)"),
            "head_ref": git(repo, "symbolic-ref", "-q", "HEAD", check=False).stdout.decode().strip(),
            "stash": git_text(repo, "stash", "list", "--format=%H")}


def activity_age(repo: Path, now: float) -> float | None:
    """Measure conservative destructive-operation activity, including deletions."""
    paths = changed_paths(repo)
    if not paths:
        return None
    latest = 0.0
    for name in paths:
        path = repo / name
        try:
            latest = max(latest, path.lstat().st_mtime)
        except FileNotFoundError:
            parent = path.parent
            while not parent.exists() and parent != repo:
                parent = parent.parent
            latest = max(latest, parent.stat().st_mtime)
    return max(0.0, now - latest)
