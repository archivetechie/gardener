"""Immutable workflow-safety oracles for Gardener's cleanup and journal tools.

These tests deliberately exercise the installed shell entrypoints in disposable
Git repositories.  They are written before the corresponding implementation
changes and are intended to remain immutable while those changes are made.
No network, model service, real repository, or media device is used.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


GARDENER = Path(__file__).resolve().parents[2]
BIN = GARDENER / "bin"


class WorkflowSafetyOracle(unittest.TestCase):
    """Safety properties of the automated checkpoint and journal workflow."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="gardener-oracle-")
        self.root = Path(self.tmp.name)
        self.config = self.root / "config"
        self.state = self.root / "state"
        self.fake_bin = self.root / "fake-bin"
        self.config.mkdir()
        self.state.mkdir()
        self.fake_bin.mkdir()
        self.env = os.environ.copy()
        self.env.update(
            {
                "GARDENER_CONFIG_DIR": str(self.config),
                "GARDENER_STATE_DIR": str(self.state),
                "GIT_CONFIG_GLOBAL": "/dev/null",
                "GIT_CONFIG_NOSYSTEM": "1",
            }
        )
        self._write_fake_claude()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write_fake_claude(self) -> None:
        fake = self.fake_bin / "claude"
        fake.write_text(
            "#!/usr/bin/env bash\n"
            "if [[ \"${CLAUDE_ACTION:-}\" == source_escape ]]; then\n"
            "  mkdir -p src\n"
            "  printf 'mutated by fake agent\\n' > src/main.rs\n"
            "  printf 'escaped by fake agent\\n' > src/escape.rs\n"
            "fi\n"
            "exit \"${CLAUDE_EXIT:-0}\"\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)

    def _enable_fake_claude(self) -> None:
        """Expose the deterministic fake after scripts reset PATH."""
        config = self.config / "config"
        prior = config.read_text(encoding="utf-8") if config.exists() else ""
        config.write_text(
            prior.rstrip("\n")
            + ("\n" if prior else "")
            + f'export PATH="{self.fake_bin}:$PATH"\n',
            encoding="utf-8",
        )

    def _run(self, command: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            cwd=cwd,
            env=self.env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def _git(self, repo: Path, *args: str, check: bool = True) -> str:
        result = self._run(["git", *args], cwd=repo)
        if check and result.returncode:
            self.fail(f"git {' '.join(args)} failed:\n{result.stdout}\n{result.stderr}")
        return result.stdout.strip()

    def _init_repo(self, name: str = "repo") -> Path:
        repo = self.root / name
        repo.mkdir()
        self._git(repo, "init", "-q", "-b", "main")
        self._git(repo, "config", "user.name", "Oracle Test")
        self._git(repo, "config", "user.email", "oracle@example.invalid")
        self._git(repo, "config", "core.hooksPath", "/dev/null")
        self._git(repo, "config", "commit.gpgsign", "false")
        (repo / "README.md").write_text("initial\n", encoding="utf-8")
        self._git(repo, "add", "README.md")
        self._git(repo, "commit", "-q", "-m", "initial")
        return repo

    def _add_origin(self, repo: Path) -> Path:
        bare = self.root / f"{repo.name}-origin.git"
        self._run(["git", "init", "-q", "--bare", "-b", "main", str(bare)])
        self._git(repo, "remote", "add", "origin", str(bare))
        self._git(repo, "push", "-q", "-u", "origin", "main")
        return bare

    def _configure_repos(self, *repos: Path, idle_min: int = 1) -> None:
        (self.config / "repos").write_text(
            "".join(f"{repo}\n" for repo in repos), encoding="utf-8"
        )
        config = self.config / "config"
        prior = config.read_text(encoding="utf-8") if config.exists() else ""
        config.write_text(
            prior.rstrip("\n")
            + ("\n" if prior else "")
            + f"IDLE_MIN={idle_min}\n",
            encoding="utf-8",
        )

    def _run_housekeep(self) -> subprocess.CompletedProcess[str]:
        return self._run([str(BIN / "housekeep.sh")])

    def _make_old(self, path: Path) -> None:
        old = max(1, int(time.time()) - 3600)
        os.utime(path, (old, old))

    def test_housekeep_does_not_delete_remote_branch_with_new_unmerged_tip(self) -> None:
        """Local merged state must not authorize deleting a newer remote branch."""
        repo = self._init_repo()
        origin = self._add_origin(repo)

        self._git(repo, "switch", "-q", "-c", "feature")
        (repo / "feature.txt").write_text("first\n", encoding="utf-8")
        self._git(repo, "add", "feature.txt")
        self._git(repo, "commit", "-q", "-m", "feature")
        self._git(repo, "push", "-q", "-u", "origin", "feature")
        self._git(repo, "switch", "-q", "main")
        self._git(repo, "merge", "-q", "--no-ff", "feature", "-m", "merge feature")
        self._git(repo, "push", "-q", "origin", "main")

        other = self.root / "other"
        clone = self._run(["git", "clone", "-q", str(origin), str(other)])
        self.assertEqual(clone.returncode, 0, clone.stderr)
        self._git(other, "config", "user.name", "Oracle Test")
        self._git(other, "config", "user.email", "oracle@example.invalid")
        self._git(other, "fetch", "-q", "origin", "feature:feature")
        self._git(other, "switch", "-q", "feature")
        (other / "not-merged.txt").write_text("remote-only\n", encoding="utf-8")
        self._git(other, "add", "not-merged.txt")
        self._git(other, "commit", "-q", "-m", "new remote work")
        self._git(other, "push", "-q", "origin", "feature")

        self._configure_repos(repo, idle_min=0)
        result = self._run_housekeep()
        self.assertEqual(result.returncode, 0, result.stderr)
        remote_feature = self._run(
            ["git", "--git-dir", str(origin), "show-ref", "--verify", "refs/heads/feature"]
        )
        self.assertEqual(
            remote_feature.returncode,
            0,
            "housekeep deleted a remote branch whose tip was not merged into main",
        )

    def test_checkpoint_is_recovery_ref_without_touching_head_index_or_default_push(self) -> None:
        """Idle recovery must preserve the working state without committing/pushing it."""
        repo = self._init_repo()
        origin = self._add_origin(repo)
        tracked = repo / "tracked.txt"
        tracked.write_text("tracked\n", encoding="utf-8")
        self._git(repo, "add", "tracked.txt")
        self._git(repo, "commit", "-q", "-m", "tracked file")
        self._git(repo, "push", "-q", "origin", "main")
        tracked.unlink()
        nested = repo / "new" / "deep" / "fresh.txt"
        nested.parent.mkdir(parents=True)
        nested.write_text("fresh\n", encoding="utf-8")
        self._make_old(nested)

        head_before = self._git(repo, "rev-parse", "HEAD")
        index_before = hashlib.sha256((repo / ".git" / "index").read_bytes()).hexdigest()
        remote_before = self._run(
            ["git", "--git-dir", str(origin), "rev-parse", "refs/heads/main"]
        ).stdout.strip()
        self._configure_repos(repo)
        result = self._run_housekeep()
        self.assertEqual(result.returncode, 0, result.stderr)

        self.assertEqual(self._git(repo, "rev-parse", "HEAD"), head_before)
        self.assertEqual(
            hashlib.sha256((repo / ".git" / "index").read_bytes()).hexdigest(),
            index_before,
        )
        remote_after = self._run(
            ["git", "--git-dir", str(origin), "rev-parse", "refs/heads/main"]
        ).stdout.strip()
        self.assertEqual(remote_after, remote_before, "checkpoint pushed onto default branch")

        refs = self._git(repo, "for-each-ref", "--format=%(refname)", "refs/checkpoints/main")
        self.assertTrue(refs, "no recovery checkpoint ref was created")
        checkpoint = refs.splitlines()[0]
        tree_paths = set(self._git(repo, "ls-tree", "-r", "--name-only", checkpoint).splitlines())
        self.assertIn("new/deep/fresh.txt", tree_paths)
        self.assertNotIn("tracked.txt", tree_paths, "checkpoint did not preserve tracked deletion")

    def test_fresh_nested_edit_under_old_directory_cannot_reach_main(self) -> None:
        """A fresh nested file must not be hidden by an old untracked directory mtime."""
        repo = self._init_repo("nested-edit")
        origin = self._add_origin(repo)
        old_dir = repo / "old-directory"
        old_dir.mkdir()
        fresh = old_dir / "nested" / "edit.txt"
        fresh.parent.mkdir()
        fresh.write_text("fresh edit\n", encoding="utf-8")
        self._make_old(old_dir)

        head_before = self._git(repo, "rev-parse", "HEAD")
        remote_before = self._run(
            ["git", "--git-dir", str(origin), "rev-parse", "refs/heads/main"]
        ).stdout.strip()
        self._configure_repos(repo, idle_min=30)
        result = self._run_housekeep()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self._git(repo, "rev-parse", "HEAD"), head_before)
        self.assertEqual(
            self._run(
                ["git", "--git-dir", str(origin), "rev-parse", "refs/heads/main"]
            ).stdout.strip(),
            remote_before,
        )
        self.assertNotIn(
            "old-directory/nested/edit.txt",
            self._git(repo, "ls-tree", "-r", "--name-only", "main"),
        )

    def test_daily_failed_agent_is_not_reported_successfully(self) -> None:
        repo = self._init_repo("daily-repo")
        self._configure_repos(repo)
        self._enable_fake_claude()
        self.env["CLAUDE_EXIT"] = "7"
        result = self._run([str(BIN / "daily.sh")])
        log = (self.state / "daily.log").read_text(encoding="utf-8")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("done (exit 0)", log)
        self.assertRegex(log, r"(?:exit 7|failed|FAILED)")

    def test_docsmith_failed_agent_is_not_reported_successfully(self) -> None:
        repo = self._init_repo("docsmith-failure")
        (repo / "docs").mkdir()
        (repo / "docs" / "README.md").write_text("docs\n", encoding="utf-8")
        self._git(repo, "add", "docs/README.md")
        self._git(repo, "commit", "-q", "-m", "docs")
        (self.config / "docsmith-repos").write_text(f"{repo}\n", encoding="utf-8")
        self._enable_fake_claude()
        self.env["CLAUDE_EXIT"] = "7"
        result = self._run([str(BIN / "docsmith.sh")])
        log = (self.state / "docsmith" / "docsmith.log").read_text(encoding="utf-8")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("done: docsmith-failure (exit 0)", log)
        self.assertRegex(log, r"(?:exit 7|failed|FAILED)")

    def test_docsmith_cannot_escape_source_checkout_with_untracked_source_edit(self) -> None:
        """Agent edits must be isolated or rejected before source worktree mutation."""
        repo = self._init_repo("docsmith-source")
        (repo / "docs").mkdir()
        (repo / "docs" / "README.md").write_text("docs\n", encoding="utf-8")
        (repo / "src").mkdir()
        (repo / "src" / "main.rs").write_text("fn main() {}\n", encoding="utf-8")
        self._git(repo, "add", "docs/README.md", "src/main.rs")
        self._git(repo, "commit", "-q", "-m", "docs and source")
        source_before = (repo / "src" / "main.rs").read_text(encoding="utf-8")
        (self.config / "docsmith-repos").write_text(f"{repo}\n", encoding="utf-8")
        self._enable_fake_claude()
        self.env["CLAUDE_ACTION"] = "source_escape"
        result = self._run([str(BIN / "docsmith.sh")])
        self.assertFalse(
            (repo / "src" / "escape.rs").exists(),
            f"docsmith allowed an agent edit to escape into the source checkout: {result.stderr}",
        )
        self.assertEqual((repo / "src" / "main.rs").read_text(encoding="utf-8"), source_before)

    def test_docsmith_drift_rejects_nonexistent_anchor_source(self) -> None:
        repo = self._init_repo("drift")
        document = repo / "README.md"
        base = self._git(repo, "rev-parse", "HEAD")
        document.write_text(
            f"<!-- code-anchor: src/does-not-exist.rs @ {base} -->\n\ntext\n",
            encoding="utf-8",
        )
        self._git(repo, "add", "README.md")
        self._git(repo, "commit", "-q", "-m", "bad anchor")
        result = self._run([str(BIN / "docsmith-drift"), str(repo)])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does-not-exist.rs", result.stdout + result.stderr)

    def test_sitrep_retains_active_row_with_accepted_prose_and_state_date(self) -> None:
        hub = self._init_repo("hub")
        (hub / "STANDING.md").write_text(
            "# Standing issues\n\n"
            "| Issue | State | Since | Detail |\n"
            "|---|---|---|---|\n"
            "| S-17 | open | 2026-09-23 | operator accepted workaround pending fix |\n",
            encoding="utf-8",
        )
        self._git(hub, "add", "STANDING.md")
        self._git(hub, "commit", "-q", "-m", "standing issue")
        self._configure_repos(hub)
        result = self._run([str(BIN / "sitrep"), "now"])
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        self.assertIn("S-17", output)
        self.assertIn("open", output)
        self.assertIn("2026-09-23", output)


if __name__ == "__main__":
    unittest.main(verbosity=2)
