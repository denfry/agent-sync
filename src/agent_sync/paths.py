"""Filesystem layout helpers.

Everything agent-sync stores lives under ``.claude/coordination`` inside the
target repository. Repo discovery walks upwards looking for a ``.git`` or
``.claude`` directory, which keeps the database stable no matter which
subdirectory a Claude Code session happens to be launched from.

The ``AGENT_SYNC_ROOT`` environment variable forces the repo root. Tests rely on
this to redirect all state into a temporary directory so they never touch the
real user home or working tree.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

COORD_DIRNAME = "coordination"
DB_FILENAME = "state.sqlite"
CURRENT_AGENT_FILENAME = "current-agent"

_MARKERS = (".git", ".claude")


def repo_root(start: str | os.PathLike[str] | None = None) -> Path:
    """Return the repository root for *start* (defaults to the cwd).

    Resolution order:

    1. ``AGENT_SYNC_ROOT`` environment variable, if set.
    2. The nearest ancestor containing a ``.git`` or ``.claude`` directory.
    3. The starting directory itself, as a last resort.
    """
    override = os.environ.get("AGENT_SYNC_ROOT")
    if override:
        return Path(override).expanduser().resolve()

    base = Path(start) if start is not None else Path.cwd()
    base = base.resolve()
    for candidate in (base, *base.parents):
        for marker in _MARKERS:
            if (candidate / marker).exists():
                return candidate
    return base


def claude_dir(start: str | os.PathLike[str] | None = None) -> Path:
    return repo_root(start) / ".claude"


def coordination_dir(start: str | os.PathLike[str] | None = None) -> Path:
    return claude_dir(start) / COORD_DIRNAME


def db_path(start: str | os.PathLike[str] | None = None) -> Path:
    return coordination_dir(start) / DB_FILENAME


def current_agent_file(start: str | os.PathLike[str] | None = None) -> Path:
    return coordination_dir(start) / CURRENT_AGENT_FILENAME


def ensure_coordination_dir(start: str | os.PathLike[str] | None = None) -> Path:
    """Create ``.claude/coordination`` (and parents) if missing and return it."""
    path = coordination_dir(start)
    path.mkdir(parents=True, exist_ok=True)
    return path


def read_or_create_local_agent_id(start: str | os.PathLike[str] | None = None) -> str:
    """Return a stable per-repo agent id, generating and persisting one if needed.

    This is the final fallback when neither ``AGENT_SYNC_ID`` nor a session id is
    available, so a lone session still gets a consistent identity across calls.
    """
    path = current_agent_file(start)
    if path.exists():
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    ensure_coordination_dir(start)
    agent_id = f"agent-{uuid.uuid4().hex[:12]}"
    path.write_text(agent_id + "\n", encoding="utf-8")
    return agent_id


def normalize_repo_path(
    file_path: str, start: str | os.PathLike[str] | None = None
) -> str:
    """Normalize *file_path* to a forward-slash repo-relative string.

    Locks are keyed by this canonical form so that a path set via the CLI
    (``src/app.ts``) matches the same file reported by a hook as an absolute path
    or with OS-native separators. Paths outside the repo are returned as a
    cleaned posix string without raising.
    """
    root = repo_root(start)
    raw = Path(file_path)
    candidate = raw if raw.is_absolute() else root / raw
    try:
        resolved = candidate.resolve()
        return resolved.relative_to(root.resolve()).as_posix()
    except (ValueError, OSError):
        return raw.as_posix()
