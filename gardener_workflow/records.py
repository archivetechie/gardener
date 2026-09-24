"""Append-only attempt evidence and deterministic operational summaries.

Starts are committed to disk before work begins. A separate terminal event makes
crashes and skipped attempts distinguishable from completed verification.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import socket
import sys
import uuid
from pathlib import Path
from typing import Any

from .registry import records_dir


def utc_now() -> str:
    """Return an unambiguous UTC timestamp."""
    return dt.datetime.now(dt.timezone.utc).isoformat()


def digest_file(path: Path) -> str:
    """Hash a file incrementally rather than reading large logs into memory."""
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def append_event(record: dict[str, Any], root: Path | None = None) -> Path:
    """Atomically publish a new immutable JSON event; never replace a record."""
    value = {"schema": "workflow.event.v1", "time": utc_now(), **record}
    root = root or records_dir()
    directory = root / value["time"][:10]
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    name = f"{value['time'].replace(':', '-')}-{uuid.uuid4().hex}.json"
    temporary = directory / ("." + name)
    final = directory / name
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, final)
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)
    return final


class Attempt:
    """Record a job's start and exactly one terminal outcome."""

    def __init__(self, kind: str, *, root: Path | None = None, **details: Any):
        self.id = uuid.uuid4().hex
        self.root = root or records_dir()
        self.kind = kind
        self.finished = False
        self.parent = os.environ.get("WORKFLOW_ATTEMPT_ID")
        initial_source = details.pop("source", {"status": "pending"})
        details.setdefault("command", sys.argv)
        self.start = append_event({
            "event": "start", "attempt_id": self.id, "kind": kind,
            "host": socket.gethostname(), "pid": os.getpid(),
            "parent_attempt": self.parent, "source": initial_source, **details,
        }, self.root)
        os.environ["WORKFLOW_ATTEMPT_ID"] = self.id
        self.source = initial_source
        if initial_source == {"status": "pending"}:
            from .gitstate import identity
            from .registry import registry
            sources = {}
            try:
                _, repos = registry()
                for repo in repos:
                    try:
                        sources[repo.name] = identity(repo.path)
                    except Exception as exc:
                        sources[repo.name] = {"unknown": type(exc).__name__}
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                sources = {"unknown": "registry unavailable"}
            self.source = sources
            append_event({"event": "source", "attempt_id": self.id, "kind": kind,
                          "source": sources}, self.root)

    def finish(self, exit_code: int, verdict: str, *, phase: str, **details: Any) -> Path:
        """Append a terminal record, rejecting contradictory success claims."""
        if self.finished:
            raise ValueError("attempt already has a terminal record")
        if verdict not in {"passed", "failed", "skipped", "advisory", "candidate", "known-red"}:
            raise ValueError(f"unknown attempt verdict: {verdict}")
        if verdict == "passed" and exit_code != 0:
            raise ValueError("nonzero exit cannot pass")
        if verdict == "passed" and not source_known(self.source):
            verdict = "advisory"
            details.setdefault("reason", "required source identity is unknown")
        path = append_event({
            "event": "terminal", "attempt_id": self.id, "kind": self.kind,
            "exit_code": exit_code, "verdict": verdict, "phase": phase,
            "evidence": [], "source": self.source, **details,
        }, self.root)
        self.finished = True
        if os.environ.get("WORKFLOW_ATTEMPT_ID") == self.id:
            if self.parent is None:
                os.environ.pop("WORKFLOW_ATTEMPT_ID", None)
            else:
                os.environ["WORKFLOW_ATTEMPT_ID"] = self.parent
        return path


def read_attempts(root: Path | None = None) -> tuple[list[dict], list[str]]:
    """Join events while surfacing malformed, duplicate, or orphaned evidence."""
    root = root or records_dir()
    grouped: dict[str, dict] = {}
    problems = []
    for path in sorted(root.glob("*/*.json")):
        key = None
        try:
            value = json.loads(path.read_text())
            if value.get("schema") != "workflow.event.v1":
                raise ValueError("unknown schema")
            if value.get("event") not in {"start", "terminal", "source"}:
                raise ValueError("unknown event type")
            key = value["attempt_id"]
            if not isinstance(key, str) or not key:
                raise ValueError("invalid attempt ID")
            item = grouped.setdefault(key, {"attempt_id": key})
            event = value["event"]
            if event in item:
                raise ValueError("duplicate " + event)
            dt.datetime.fromisoformat(value["time"])
            if not isinstance(value["kind"], str):
                raise ValueError("invalid kind")
            if event == "start" and not all(k in value for k in ("command", "source")):
                raise ValueError("start lacks command/source")
            if event == "source" and "source" not in value:
                raise ValueError("source event lacks identity")
            if event == "terminal":
                if not all(k in value for k in ("phase", "evidence")):
                    raise ValueError("terminal lacks phase/evidence declaration")
                if value.get("verdict") not in {"passed", "failed", "skipped", "advisory", "candidate", "known-red"}:
                    raise ValueError("invalid verdict")
                if not isinstance(value.get("exit_code"), int):
                    raise ValueError("missing exit code")
                if value["verdict"] == "passed" and value["exit_code"] != 0:
                    raise ValueError("nonzero pass")
            item[event] = {**value, "record": str(path)}
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            problems.append(f"{path}: {exc}")
            if isinstance(key, str) and key in grouped:
                grouped[key]["invalid"] = True
    for item in grouped.values():
        if "start" not in item:
            problems.append(f"{item['attempt_id']}: terminal without start")
            item["invalid"] = True
        if "start" in item and "terminal" in item:
            if item["start"].get("source") == {"status": "pending"} and "source" not in item:
                problems.append(f"{item['attempt_id']}: source capture missing")
                item["invalid"] = True
            if item["start"]["kind"] != item["terminal"]["kind"] or item["terminal"]["time"] < item["start"]["time"]:
                problems.append(f"{item['attempt_id']}: contradictory start/terminal")
                item["invalid"] = True
            source = item.get("source", {}).get("source", item["start"].get("source"))
            if item["terminal"]["verdict"] == "passed" and not source_known(source):
                problems.append(f"{item['attempt_id']}: passed with unknown source identity")
                item["invalid"] = True
        item["verdict"] = "invalid" if item.get("invalid") else item.get("terminal", {}).get("verdict", "incomplete")
    return sorted(grouped.values(), key=lambda item: item.get("start", {}).get("time", "")), problems


def source_known(source: object) -> bool:
    """Source capture is usable only when every declared repository was read."""
    return bool(isinstance(source, dict) and source and all(
        isinstance(value, dict) and value.get("head") and value.get("work_digest")
        for value in source.values()))


def render_health(root: Path | None = None) -> str:
    """Render status only from primary attempt records, retaining failure links."""
    attempts, problems = read_attempts(root)
    lines = ["## Automation evidence", ""]
    if not attempts:
        lines.append("No attempt evidence recorded; verification state is unknown.")
    latest: dict[str, dict] = {}
    last_pass: dict[str, dict] = {}
    for item in attempts:
        kind = item.get("start", {}).get("kind", "unknown")
        latest[kind] = item
        if item["verdict"] == "passed":
            last_pass[kind] = item
    for kind, item in sorted(latest.items()):
        start = item.get("start", {})
        terminal = item.get("terminal", {})
        failed = terminal.get("first_failed_phase", terminal.get("phase", "no terminal record"))
        evidence = terminal.get("record", start.get("record", "missing"))
        last = last_pass.get(kind, {}).get("terminal", {}).get("time", "never recorded")
        streak = 0
        for old in reversed([a for a in attempts if a.get("start", {}).get("kind") == kind]):
            if old["verdict"] == "passed":
                break
            streak += 1
        lines.append(f"- {kind}: **{item['verdict']}**; {start.get('time', '?')}; "
                     f"phase={failed}; unsuccessful streak={streak}; last pass={last}; "
                     f"evidence: `{evidence}`")
    lines.extend(f"- EVIDENCE ERROR: {problem}" for problem in problems)
    return "\n".join(lines) + "\n"
