"""Read the configured repository registry without evaluating shell configuration.

The legacy path lists remain migration inputs. An installed repositories.toml
owns repository identity, documentation policy, and the private evidence root.
"""

from __future__ import annotations

import os
import tempfile
import json
import tomllib
from dataclasses import dataclass
from pathlib import Path


def config_dir() -> Path:
    """Return the caller's configuration root, including isolated test roots."""
    return Path(os.environ.get("GARDENER_CONFIG_DIR", Path.home() / ".config/gardener")).expanduser()


def state_dir() -> Path:
    """Return the local operational-state root."""
    return Path(os.environ.get("GARDENER_STATE_DIR", Path.home() / ".local/state/gardener")).expanduser()


PROTECTED = (
    "AGENTS.md", "CLAUDE.md", "docs/INDEX.md", "**/INDEX.md", ".github/**", ".git*", "specs/**",
    "docs/contract-*", "docs/design-*", "docs/prompt-*", "docs/process-*",
    "docs/archive/**", "docs/historical/**", "journal/**", "STANDING.md",
    "GAPBOARD.md", "COVERAGE.md", "BRIEF.md",
)


@dataclass(frozen=True)
class Repository:
    """One stable repository identity and its configured output policy."""

    name: str
    path: Path
    public: bool = True
    docs: tuple[str, ...] = ("README.md", "GETTING-STARTED.md", "docs/*.md", "docs/**/*.md")
    protected: tuple[str, ...] = PROTECTED
    checkpoint_remote: str | None = None
    docsmith: bool = True


def registry() -> tuple[Path | None, list[Repository]]:
    """Load one canonical registry, or legacy lists when not yet migrated."""
    config = config_dir()
    path = config / "repositories.toml"
    if path.exists():
        data = tomllib.loads(path.read_text())
        if data.get("hub") is not None and not isinstance(data["hub"], str):
            raise ValueError("hub must be a path string")
        hub = Path(data["hub"]).expanduser().resolve() if data.get("hub") else None
        result = []
        entries = data.get("repository", {})
        if not isinstance(entries, dict):
            raise ValueError("repository must be a TOML table")
        for name, raw in entries.items():
            if not isinstance(raw, dict) or not isinstance(raw.get("path"), str):
                raise ValueError(f"repository {name} must have a path string")
            for key in ("docs", "protected"):
                if key in raw and (not isinstance(raw[key], list) or not all(isinstance(p, str) for p in raw[key])):
                    raise ValueError(f"repository {name} {key} must be a list of paths")
            for key in ("public", "docsmith"):
                if key in raw and not isinstance(raw[key], bool):
                    raise ValueError(f"repository {name} {key} must be boolean")
            if raw.get("checkpoint_remote") is not None and not isinstance(raw["checkpoint_remote"], str):
                raise ValueError(f"repository {name} checkpoint_remote must be a string")
            result.append(Repository(
                name=name, path=Path(raw["path"]).expanduser().resolve(),
                public=raw.get("public", True), docs=tuple(raw.get("docs", Repository.docs)),
                protected=tuple(dict.fromkeys((*PROTECTED, *raw.get("protected", [])))),
                checkpoint_remote=raw.get("checkpoint_remote"),
                docsmith=raw.get("docsmith", True),
            ))
        if len({r.path for r in result}) != len(result):
            raise ValueError("repository registry contains duplicate paths")
        return hub, result
    paths: list[Path] = []
    docs_paths: set[Path] = set()
    for filename in ("repos", "docsmith-repos"):
        source = config / filename
        if not source.exists():
            continue
        for line in source.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                repo = Path(line).expanduser().resolve()
                if repo not in paths:
                    paths.append(repo)
                if filename == "docsmith-repos":
                    docs_paths.add(repo)
    repos = [Repository(p.name, p, docsmith=not docs_paths or p in docs_paths) for p in paths]
    if len({r.name for r in repos}) != len(repos):
        raise ValueError("legacy repository basenames collide; configure repositories.toml")
    hub_env = os.environ.get("HUB_REPO")
    return (Path(hub_env).expanduser().resolve() if hub_env else None), repos


def records_dir() -> Path:
    """Keep durable records in the configured private hub, never a public repo."""
    if os.environ.get("GARDENER_RECORDS_DIR"):
        return Path(os.environ["GARDENER_RECORDS_DIR"]).expanduser()
    try:
        hub, repos = registry()
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        # Configuration failure must itself remain observable as an attempt.
        return state_dir() / "events"
    private = hub and any(r.path == hub and not r.public for r in repos)
    return hub / "journal/automation/events" if private else state_dir() / "events"


def write_registry(hub: Path | None, repos: list[Repository]) -> Path:
    """Atomically persist explicit policy; legacy path lists become read-only inputs."""
    lines = ["# Managed by gardener; checkpoint backup is opt-in."]
    if hub:
        lines.append("hub = " + json.dumps(str(hub)))
    for repo in repos:
        lines.extend(["", "[repository." + json.dumps(repo.name) + "]",
                      "path = " + json.dumps(str(repo.path)),
                      "public = " + str(repo.public).lower(),
                      "docsmith = " + str(repo.docsmith).lower(),
                      "docs = " + json.dumps(repo.docs),
                      "protected = " + json.dumps(repo.protected)])
        if repo.checkpoint_remote:
            lines.append("checkpoint_remote = " + json.dumps(repo.checkpoint_remote))
    path = config_dir() / "repositories.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".registry-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write("\n".join(lines) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)
    return path
