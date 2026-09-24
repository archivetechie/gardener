"""Adversarial tests for lease descriptor recovery across hostile child boundaries.

These tests use disposable state roots and real subprocesses.  They exercise the
broker's kernel peer and process ancestry checks, while keeping all lock files
and sockets outside the repository.
"""

from __future__ import annotations

import json
import hashlib
import os
import subprocess
import sys
import textwrap
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gardener_workflow.lease_broker import BROKER_ENV, DescriptorBroker
from gardener_workflow.leases import Lease, inherited_descriptors


CHILD = textwrap.dedent(
    """
    import json
    import os
    import sys

    from gardener_workflow.leases import BusyError, Lease, inherited_descriptors

    resource = sys.argv[1]
    operation = sys.argv[2]
    try:
        if operation == "lease":
            with Lease([resource]):
                print("acquired", flush=True)
        elif operation == "probe":
            inherited_descriptors()
            print("accepted", flush=True)
        elif operation == "competitor":
            try:
                with Lease([resource]):
                    print("acquired", flush=True)
            except BusyError:
                print("busy", flush=True)
        else:
            raise ValueError(operation)
    except Exception as exc:
        print(type(exc).__name__ + ":" + str(exc), flush=True)
        sys.exit(0)
    """
)


def _env(state: Path, *, broker: str | None = None, descriptors: dict[str, int] | None = None) -> dict[str, str]:
    value = os.environ.copy()
    value["GARDENER_STATE_DIR"] = str(state)
    value["GIT_CONFIG_GLOBAL"] = "/dev/null"
    value["GIT_CONFIG_NOSYSTEM"] = "1"
    value["WORKFLOW_LOCK_FDS"] = json.dumps(descriptors or {})
    if broker is None:
        value.pop(BROKER_ENV, None)
    else:
        value[BROKER_ENV] = broker
    return value


def _run(state: Path, resource: str, operation: str, *, broker: str | None = None,
         descriptors: dict[str, int] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", CHILD, resource, operation],
        cwd=Path(__file__).resolve().parents[1],
        env=_env(state, broker=broker, descriptors=descriptors),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _broker_locator() -> str:
    return os.environ[BROKER_ENV]


class LeaseBrokerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="lease-broker-test-")
        self.root = Path(self.temporary.name)
        self.state = self.root / "state"
        self.environment = patch.dict(
            os.environ,
            {"GARDENER_STATE_DIR": str(self.state)},
            clear=False,
        )
        self.environment.start()
        os.environ.pop(BROKER_ENV, None)

    def tearDown(self) -> None:
        self.environment.stop()
        self.temporary.cleanup()

    def test_closed_descriptors_are_recovered_only_for_a_descendant(self) -> None:
        """A child with closed inherited FDs recovers the held lock via SCM_RIGHTS."""
        resource = "resource:closed-fd"

        with Lease([resource]) as owned:
            with DescriptorBroker(owned.descriptors):
                result = _run(self.state, resource, "lease",
                              broker=_broker_locator(), descriptors=owned.descriptors)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), "acquired")

    def test_cli_lease_dispatch_survives_a_child_that_closes_passed_fds(self) -> None:
        """The real lease subcommand wires the broker into its child environment."""
        from gardener_workflow.cli import main

        resource = "resource:cli-closed-fd"
        code = textwrap.dedent(
            f"""
            import json
            import os
            from gardener_workflow.leases import Lease
            for fd in json.loads(os.environ["WORKFLOW_LOCK_FDS"]).values():
                try:
                    os.close(fd)
                except OSError:
                    pass
            with Lease([{resource!r}]):
                print("cli-acquired", flush=True)
            """
        )
        # The CLI's child output is intentionally only an integration signal;
        # main() returns the child's exit code after the broker-served recovery.
        self.assertEqual(main(["lease", "--resource", resource, "--", sys.executable, "-c", code]), 0)


    def test_competitor_without_locator_cannot_take_a_brokered_lock(self) -> None:
        """Dropping both locator and descriptors does not evade the kernel lock."""
        resource = "resource:competitor"

        with Lease([resource]) as owned:
            with DescriptorBroker(owned.descriptors):
                result = _run(self.state, resource, "competitor")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), "busy")


    def test_reparented_peer_with_copied_locator_is_rejected(self) -> None:
        """A same-UID process outside the broker ancestry cannot receive FDs."""
        resource = "resource:unrelated-peer"

        with Lease([resource]) as owned:
            with DescriptorBroker(owned.descriptors):
                read_fd, write_fd = os.pipe()
                os.set_inheritable(write_fd, True)
                env = _env(self.state, broker=_broker_locator(), descriptors=owned.descriptors)
                script = textwrap.dedent(
                """
                import os
                import sys
                import time
                from gardener_workflow.lease_broker import recover_descriptors
                child = os.fork()
                if child:
                    os._exit(0)
                os.setsid()
                time.sleep(0.2)
                try:
                    recover_descriptors()
                    result = b"accepted"
                except Exception as exc:
                    result = type(exc).__name__.encode()
                fd = int(sys.argv[1])
                os.write(fd, result)
                os.close(fd)
                """
                )
                process = subprocess.Popen(
                    [sys.executable, "-c", script, str(write_fd)],
                    cwd=Path(__file__).resolve().parents[1],
                    env=env,
                    pass_fds=(write_fd,),
                    close_fds=True,
                )
                os.close(write_fd)
                result = os.read(read_fd, 4096)
                os.close(read_fd)
                self.assertEqual(process.wait(timeout=5), 0)
                self.assertNotEqual(result, b"accepted")


    def test_forged_resource_map_is_rejected_after_broker_recovery(self) -> None:
        """The broker cannot be used to replace the resource names supplied by the caller."""
        resource = "resource:forged-map"

        with Lease([resource]) as owned:
            with DescriptorBroker(owned.descriptors):
                lock_root = self.state / "locks"
                forged = lock_root / "forged.lock"
                result = _run(self.state, resource, "probe",
                              broker=_broker_locator(), descriptors={str(forged): 999})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(result.stdout.startswith("ValueError:broker resources differ"))

    def test_symlinked_lock_path_cannot_forge_descriptor_ownership(self) -> None:
        """Descriptor validation must not follow a lock-path symlink."""
        resource = "resource:symlink-forgery"
        lock_root = self.state / "locks"
        lock_root.mkdir(parents=True)
        outside = self.root / "outside.lock"
        outside.write_bytes(b"outside")
        lock_path = lock_root / (hashlib.sha256(resource.encode()).hexdigest() + ".lock")
        lock_path.symlink_to(outside)
        descriptor = os.open(outside, os.O_RDWR)
        try:
            with patch.dict(
                os.environ,
                {"WORKFLOW_LOCK_FDS": json.dumps({str(lock_path): descriptor})},
                clear=False,
            ):
                with self.assertRaises((ValueError, OSError)):
                    inherited_descriptors()
        finally:
            os.close(descriptor)


    def test_broker_and_locks_are_cleaned_up_and_reusable(self) -> None:
        """Context exit removes the broker endpoint and releases the underlying lock."""
        os.environ[BROKER_ENV] = "sentinel"
        resource = "resource:cleanup"

        with Lease([resource]) as owned:
            broker = DescriptorBroker(owned.descriptors)
            with broker:
                path = broker.path
                temporary = Path(broker.temporary.name)
                self.assertTrue(path.exists())
                self.assertNotEqual(os.environ[BROKER_ENV], "sentinel")
            self.assertFalse(broker.thread.is_alive())
            self.assertFalse(path.exists())
            self.assertFalse(temporary.exists())
            self.assertEqual(os.environ[BROKER_ENV], "sentinel")

        with Lease([resource]):
            pass


if __name__ == "__main__":
    unittest.main()
