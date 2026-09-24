"""Process-held file leases shared by automation, worktrees, and child commands."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import subprocess
from contextlib import AbstractContextManager
from pathlib import Path

from .registry import state_dir


class BusyError(RuntimeError):
    """Another process owns a required resource."""


def repo_resource(repo: Path) -> str:
    """Use the Git common directory so linked worktrees share one lease."""
    common = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "--path-format=absolute", "--git-common-dir"],
        text=True, stderr=subprocess.DEVNULL,
    ).strip()
    return "repo:" + str(Path(common).resolve())


def inherited_descriptors() -> dict[str, int]:
    """Accept inherited descriptors only when they really lock the named inode."""
    raw = json.loads(os.environ.get("WORKFLOW_LOCK_FDS", "{}"))
    if not isinstance(raw, dict):
        raise ValueError("invalid inherited lease map")
    result = {}
    lock_root = (state_dir() / "locks").resolve()
    for name, fd in raw.items():
        path = Path(name)
        if path.parent.resolve() != lock_root or not isinstance(fd, int) or fd < 3:
            raise ValueError("invalid inherited lease descriptor")
        actual, expected = os.fstat(fd), path.stat()
        if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
            raise ValueError("inherited lease descriptor names a different file")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BusyError(f"inherited lease is not held: {name}") from exc
        result[name] = fd
    return result


class Lease(AbstractContextManager):
    """Acquire resources in one order and preserve verified leases in children."""

    def __init__(self, resources: list[str]):
        self.resources = sorted(set(resources))
        self.opened: list[int] = []
        self.previous: str | None = None
        self.descriptors: dict[str, int] = {}

    def __enter__(self):
        root = state_dir() / "locks"
        root.mkdir(parents=True, exist_ok=True)
        self.previous = os.environ.get("WORKFLOW_LOCK_FDS")
        self.descriptors = inherited_descriptors()
        try:
            for resource in self.resources:
                path = (root / (hashlib.sha256(resource.encode()).hexdigest() + ".lock")).resolve()
                if str(path) in self.descriptors:
                    continue
                fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
                self.opened.append(fd)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise BusyError(f"resource busy: {resource}") from exc
                os.set_inheritable(fd, True)
                os.ftruncate(fd, 0)
                os.write(fd, json.dumps({"resource": resource, "pid": os.getpid(),
                                        "attempt_id": os.environ.get("WORKFLOW_ATTEMPT_ID")}).encode())
                self.descriptors[str(path)] = fd
            os.environ["WORKFLOW_LOCK_FDS"] = json.dumps(self.descriptors)
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *args):
        for fd in reversed(self.opened):
            os.close(fd)
        self.opened.clear()
        if self.previous is None:
            os.environ.pop("WORKFLOW_LOCK_FDS", None)
        else:
            os.environ["WORKFLOW_LOCK_FDS"] = self.previous


def run_child(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Keep all inherited resource locks alive through a subprocess tree."""
    if kwargs.get("env") is not None:
        kwargs["env"] = {**kwargs["env"], "WORKFLOW_LOCK_FDS": os.environ.get("WORKFLOW_LOCK_FDS", "{}")}
    return subprocess.run(command, pass_fds=tuple(inherited_descriptors().values()), **kwargs)
