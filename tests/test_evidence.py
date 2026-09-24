"""Exercise evidence corruption and process-held ownership across real children."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gardener_workflow.leases import BusyError, Lease, inherited_descriptors, run_child
from gardener_workflow.records import Attempt, append_event, read_attempts


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {"GARDENER_STATE_DIR": str(self.root), "WORKFLOW_LOCK_FDS": "{}"})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_missing_terminal_is_incomplete_and_duplicates_invalidate_pass(self):
        attempt = Attempt("test", root=self.root)
        self.assertEqual(read_attempts(self.root)[0][0]["verdict"], "incomplete")
        terminal = attempt.finish(0, "passed", phase="test")
        append_event(json.loads(terminal.read_text()), self.root)
        attempts, errors = read_attempts(self.root)
        self.assertTrue(errors)
        self.assertEqual(attempts[0]["verdict"], "invalid")

    def test_nonzero_pass_and_orphan_are_rejected(self):
        attempt = Attempt("test", root=self.root)
        with self.assertRaises(ValueError):
            attempt.finish(7, "passed", phase="agent")
        append_event({"event": "terminal", "attempt_id": "orphan", "kind": "test", "exit_code": 0, "verdict": "passed"}, self.root)
        attempts, errors = read_attempts(self.root)
        self.assertTrue(errors)
        self.assertFalse(any(a["verdict"] == "passed" for a in attempts))

    def test_broken_registry_still_records_start_and_unknown_cannot_pass(self):
        config = self.root / "config"
        config.mkdir()
        (config / "repositories.toml").write_text("repository = []")
        with patch.dict(os.environ, {"GARDENER_CONFIG_DIR": str(config)}):
            attempt = Attempt("broken-registry")
            self.assertTrue(attempt.start.is_file())
            terminal = attempt.finish(0, "passed", phase="test")
            self.assertEqual(json.loads(terminal.read_text())["verdict"], "advisory")
            self.assertEqual(terminal.stat().st_mode & 0o777, 0o600)
            self.assertNotEqual(read_attempts(self.root / "events")[0][0]["verdict"], "passed")

    def test_nested_real_python_and_shell_reuse_held_descriptor(self):
        env = os.environ.copy()  # Captured before acquiring: helper must update map.
        code = "from gardener_workflow.leases import Lease;\nwith Lease(['test-resource']): print('nested-held')"
        with Lease(["test-resource"]):
            result = run_child(["bash", "-c", 'exec "$@"', "bash", sys.executable, "-c", code], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("nested-held", result.stdout)
            competitor = subprocess.run([sys.executable, "-c", code], env={**os.environ, "WORKFLOW_LOCK_FDS": "{}"}, capture_output=True, text=True)
            self.assertNotEqual(competitor.returncode, 0)
            self.assertIn("BusyError", competitor.stderr)

    def test_forged_environment_fails_and_sigkill_releases(self):
        with patch.dict(os.environ, {"WORKFLOW_LOCK_FDS": json.dumps({str(self.root / "locks/forged.lock"): 9999})}):
            with self.assertRaises((ValueError, OSError)):
                inherited_descriptors()
        code = "from gardener_workflow.leases import Lease; import time;\nwith Lease(['test-resource']):\n print('ready',flush=True); time.sleep(300)"
        child = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), "ready")
            with self.assertRaises(BusyError):
                with Lease(["test-resource"]):
                    pass
        finally:
            child.kill()
            child.wait()
            child.stdout.close()
        with Lease(["test-resource"]):
            pass


if __name__ == "__main__":
    unittest.main()
