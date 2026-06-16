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
import subprocess
import uuid
from pathlib import Path

COORD_DIRNAME = "coordination"
DB_FILENAME = "state.sqlite"
CURRENT_AGENT_FILENAME = "current-agent"

_MARKERS = (".git", ".claude")


def _worktree_main_root(worktree_dir: Path) -> Path | None:
    """Resolve the main worktree root for a *linked* git worktree, or ``None``.

    In a linked worktree ``.git`` is a *file* pointing at the real gitdir, and the
    coordination database must live with the **main** worktree so every worktree
    of one repo shares a single ``state.sqlite`` (otherwise agents on different
    branches never see each other — the very workflow the skill recommends).
    ``git rev-parse --git-common-dir`` returns the shared ``.git`` directory
    (e.g. ``/repo/.git``); its parent is the main worktree root. Returns ``None``
    on any failure so the caller can fall back to the worktree dir itself.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=str(worktree_dir),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    out = result.stdout.strip()
    if not out:
        return None
    common = Path(out)
    if not common.is_absolute():
        common = worktree_dir / common
    try:
        common = common.resolve()
    except OSError:
        return None
    return common.parent if common.name == ".git" else common


def repo_root(start: str | os.PathLike[str] | None = None) -> Path:
    """Return the repository root for *start* (defaults to the cwd).

    Resolution order:

    1. ``AGENT_SYNC_ROOT`` environment variable, if set.
    2. The nearest ancestor containing a ``.git`` or ``.claude`` marker. A linked
       git worktree (``.git`` is a *file*) resolves to its **main** worktree so
       all worktrees of one repo share a single coordination database.
    3. The starting directory itself, as a last resort.
    """
    override = os.environ.get("AGENT_SYNC_ROOT")
    if override:
        return Path(override).expanduser().resolve()

    base = Path(start) if start is not None else Path.cwd()
    base = base.resolve()
    for candidate in (base, *base.parents):
        for marker in _MARKERS:
            marker_path = candidate / marker
            if not marker_path.exists():
                continue
            if marker == ".git" and marker_path.is_file():
                shared = _worktree_main_root(candidate)
                if shared is not None:
                    return shared
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
