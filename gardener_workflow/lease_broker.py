"""Pass held lock descriptors to verified descendants whose tool runner closes FDs.

The environment locates the broker; it never proves ownership. Linux peer
credentials and process ancestry authorize SCM_RIGHTS transfer. The caller still
checks each returned descriptor against the lock inode and exclusive flock.
"""
from __future__ import annotations

import array
import json
import os
import socket
import struct
import threading
import tempfile
from contextlib import AbstractContextManager
from pathlib import Path

BROKER_ENV = "WORKFLOW_LOCK_BROKER"


def process_identity(pid: int) -> tuple[int, int]:
    """Return parent PID and kernel start tick, allowing spaces in process names."""
    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    return int(fields[1]), int(fields[19])


def is_descendant(pid: int, owner: int, started: int) -> bool:
    """Authorize only a live descendant of this exact broker process."""
    seen = set()
    while pid > 1 and pid not in seen:
        seen.add(pid)
        parent, birth = process_identity(pid)
        if pid == owner:
            return birth == started
        pid = parent
    return False


def peer(sock: socket.socket) -> tuple[int, int, int]:
    """Get kernel-authenticated Linux peer PID, UID, and GID."""
    return struct.unpack("3i", sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))


class DescriptorBroker(AbstractContextManager):
    """Serve the held descriptors while a supervised child command is running."""
    def __init__(self, descriptors: dict[str, int]):
        self.descriptors = descriptors
        self.previous = os.environ.get(BROKER_ENV)
        self.owner = os.getpid()
        self.started = process_identity(self.owner)[1]
        self.stop = threading.Event()

    def __enter__(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="gardener-lease-", dir="/tmp")
        root = Path(self.temporary.name)
        if root.is_symlink() or root.stat().st_uid != os.getuid() or root.stat().st_mode & 0o077:
            raise ValueError("lease broker directory must be private and owned by this user")
        self.path = root.resolve() / "broker.sock"
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        try:
            self.server.bind(str(self.path))
            self.path.chmod(0o600)
            self.server.listen(8)
            self.server.settimeout(0.2)
        except BaseException:
            self.server.close()
            self.temporary.cleanup()
            raise
        os.environ[BROKER_ENV] = json.dumps({"path": str(self.path), "pid": self.owner, "started": self.started})
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()
        return self

    def serve(self):
        """Reject unrelated peers even if they copy the environment locator."""
        while not self.stop.is_set():
            try:
                connection, _ = self.server.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with connection:
                try:
                    connection.settimeout(2)
                    pid, uid, _ = peer(connection)
                    if uid != os.getuid() or not is_descendant(pid, self.owner, self.started):
                        continue
                    descriptors = array.array("i", self.descriptors.values())
                    connection.sendmsg([json.dumps(list(self.descriptors)).encode()],
                                       [(socket.SOL_SOCKET, socket.SCM_RIGHTS, descriptors)])
                except (OSError, ValueError):
                    continue

    def __exit__(self, *args):
        self.stop.set()
        self.thread.join(3)
        self.server.close()
        self.temporary.cleanup()
        if self.previous is None:
            os.environ.pop(BROKER_ENV, None)
        else:
            os.environ[BROKER_ENV] = self.previous


def recover_descriptors() -> dict[str, int]:
    """Receive actual descriptors from the verified supervising process."""
    locator = json.loads(os.environ[BROKER_ENV])
    path = Path(locator["path"])
    if path.parent.is_symlink() or path.parent.stat().st_uid != os.getuid() or path.parent.stat().st_mode & 0o077:
        raise ValueError("untrusted lease broker directory")
    received = array.array("i")
    with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as client:
        client.settimeout(3)
        client.connect(str(path))
        pid, uid, _ = peer(client)
        if uid != os.getuid() or pid != locator["pid"] or not is_descendant(os.getpid(), pid, locator["started"]):
            raise ValueError("untrusted lease broker peer")
        data, ancillary, flags, _ = client.recvmsg(65536, socket.CMSG_SPACE(256 * received.itemsize))
        try:
            for level, kind, payload in ancillary:
                if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                    received.frombytes(payload[:len(payload) - len(payload) % received.itemsize])
            names = json.loads(data)
            if flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC) or not isinstance(names, list) or len(names) != len(received) or len(set(names)) != len(names):
                raise ValueError("invalid broker descriptor payload")
            for fd in received:
                os.set_inheritable(fd, True)
            return dict(zip(names, received))
        except BaseException:
            for fd in received:
                os.close(fd)
            raise
