"""Registry migration and private journal evidence durability."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from gardener_workflow.gitstate import git, git_text
from gardener_workflow.registry import Repository, registry, write_registry
from gardener_workflow.records import Attempt, digest_file, read_attempts
from gardener_workflow.journal import export_journal


class RegistryJournalTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.environment = patch.dict(os.environ, {"GARDENER_CONFIG_DIR": str(self.root / "config"),
                                                   "GARDENER_STATE_DIR": str(self.root / "state"),
                                                   "GARDENER_RECORDS_DIR": str(self.root / "events")})
        self.environment.start()
        self.repo = self.root / "hub"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        for key, value in (("user.name", "Test"), ("user.email", "test@example.invalid"), ("commit.gpgsign", "false"), ("core.hooksPath", "/dev/null")):
            git(self.repo, "config", key, value)
        (self.repo / "README.md").write_text("Private hub\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "baseline")
        write_registry(self.repo, [Repository("hub", self.repo, public=False)])

    def tearDown(self):
        self.environment.stop()
        self.temporary.cleanup()

    def test_registry_round_trip_and_documented_table(self):
        hub, repos = registry()
        self.assertEqual(hub, self.repo)
        self.assertEqual(repos[0].name, "hub")
        self.assertFalse(repos[0].public)
        config = self.root / "config/repositories.toml"
        config.write_text('hub = "/tmp/private-hub"\n[repository.project]\npath = "/tmp/project"\npublic = true\ndocsmith = true\ndocs = ["README.md", "docs/**/*.md", "docs/*.md"]\n')
        self.assertEqual([r.name for r in registry()[1]], ["project"])

    def test_export_retains_primary_review_receipt_and_log(self):
        log = self.root / "review.log"
        log.write_text("Independent review result\n")
        receipt = self.root / "review.json"
        receipt.write_text(json.dumps({"log": str(log), "log_sha256": digest_file(log), "status": "rejected"}))
        attempt = Attempt("candidate-review")
        attempt.finish(1, "failed", phase="review", evidence=str(receipt), evidence_sha256=digest_file(receipt))
        self.assertEqual(export_journal(), 0)
        attempts, errors = read_attempts()
        self.assertFalse(errors)
        terminal = next(a["terminal"] for a in attempts if a["start"]["kind"] == "journal-export")
        self.assertEqual(terminal["evidence_sha256"], digest_file(Path(terminal["evidence"])))
        evidence = self.repo / "journal/automation/evidence"
        self.assertEqual((evidence / f"{attempt.id}-artifact.json").read_bytes(), receipt.read_bytes())
        self.assertEqual((evidence / f"{attempt.id}-log.log").read_bytes(), log.read_bytes())
        self.assertTrue((evidence / f"{attempt.id}-terminal.json").is_file())
        self.assertFalse(git_text(self.repo, "status", "--porcelain"))

    def test_bad_registry_still_has_failed_terminal(self):
        (self.root / "config/repositories.toml").write_text('repository = []\n')
        self.assertEqual(export_journal(), 1)
        attempts, errors = read_attempts()
        self.assertFalse(errors)
        self.assertEqual(attempts[-1]["verdict"], "failed")
        self.assertIn("terminal", attempts[-1])
