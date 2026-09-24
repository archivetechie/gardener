"""Create local recovery refs without committing or pushing an interactive branch."""

from __future__ import annotations

import fnmatch
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from urllib.parse import unquote, urlparse

from .gitstate import changed_paths, git, git_text, source_snapshot, index_path
from .leases import Lease, repo_resource
from .records import utc_now
from .registry import Repository


def checkpoint(repo: Repository, *, retain: int = 50) -> dict:
    """Snapshot stable working bytes using a private index and a recovery ref."""
    if retain < 1:
        raise ValueError("at least one recovery checkpoint must be retained")
    root = repo.path
    with Lease([repo_resource(root)]):
        index = index_path(root)
        if index.with_name(index.name + ".lock").exists():
            return {"status": "skipped", "reason": "interactive index is locked"}
        for operation in ("MERGE_HEAD", "rebase-merge", "rebase-apply", "CHERRY_PICK_HEAD"):
            path = Path(git_text(root, "rev-parse", "--git-path", operation))
            if (path if path.is_absolute() else root / path).exists():
                return {"status": "skipped", "reason": "Git operation in progress"}
        branch = git_text(root, "branch", "--show-current")
        if not branch:
            return {"status": "skipped", "reason": "detached HEAD"}
        before = source_snapshot(root)
        if not before["dirty"]:
            return {"status": "unchanged", "head": before["head"]}
        for name in changed_paths(root):
            if not (root / name).exists():
                continue
            if any(fnmatch.fnmatch(Path(name).name, pattern) for pattern in
                   (".env", ".env.*", "*.secret", "*.pem", "*.key", "*.p12", "*.pfx", "*.keystore",
                    "credentials", "credentials.json", ".git-credentials", ".netrc", "id_rsa", "id_ed25519", "id_ecdsa", "id_dsa")):
                return {"status": "skipped", "reason": f"credential-like changed path: {name}"}
        with tempfile.TemporaryDirectory(prefix="gardener-index-") as temporary:
            private_index = Path(temporary) / "index"
            if index.exists():
                shutil.copyfile(index, private_index)
            env = {"GIT_INDEX_FILE": str(private_index)}
            if not private_index.exists():
                git(root, "read-tree", "HEAD", env=env)
            git(root, "add", "-A", "--", ".", env=env)
            tree = git(root, "write-tree", env=env).stdout.decode().strip()
            if source_snapshot(root) != before:
                return {"status": "skipped", "reason": "source changed while snapshotting"}
            prefix = f"refs/checkpoints/{branch}/"
            refs = git_text(root, "for-each-ref", "--sort=-refname",
                            "--format=%(refname) %(objectname) %(tree)", prefix).splitlines()
            for row in refs:
                ref, commit, old_tree = row.split()
                if tree == old_tree:
                    return {"status": "unchanged", "ref": ref, "commit": commit, "tree": tree}
            message = (f"auto(checkpoint): recovery snapshot of {branch}\n\n"
                       "Provenance: Gardener automated recovery\n")
            commit = git(root, "commit-tree", tree, "-p", before["head"],
                         input=message.encode()).stdout.decode().strip()
            if source_snapshot(root) != before:
                return {"status": "skipped", "reason": "source changed before checkpoint publication"}
            stamp = utc_now().replace(":", "-")
            ref = prefix + stamp + "-" + uuid.uuid4().hex[:10]
            git(root, "update-ref", ref, commit, "0" * len(commit))
            result = {"status": "checkpoint", "ref": ref, "commit": commit,
                      "tree": tree, "source": before, "backup": "local-only"}
            if repo.checkpoint_remote:
                if repo.public and repo.checkpoint_remote == "origin":
                    raise ValueError("a public origin cannot receive recovery checkpoints")
                remote = git_text(root, "remote", "get-url", repo.checkpoint_remote)
                parsed = urlparse(remote)
                destination = Path(unquote(parsed.path)) if parsed.scheme == "file" and not parsed.netloc else Path(remote)
                if parsed.scheme not in {"", "file"} or not destination.is_absolute() or not destination.is_dir():
                    raise ValueError("checkpoint backup currently requires an explicit local bare repository")
                if git_text(destination, "rev-parse", "--is-bare-repository") != "true":
                    raise ValueError("checkpoint backup destination must be bare")
                git(root, "push", "--", repo.checkpoint_remote, f"{commit}:{ref}")
                result["backup"] = repo.checkpoint_remote
            for row in refs[max(0, retain - 1):]:
                old_ref, old_commit, _ = row.split()
                git(root, "update-ref", "-d", old_ref, old_commit)
            return result
