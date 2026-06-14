"""Best-effort git helpers.

These are purely informational (shown in status output) and must never raise:
git may be absent, or the repo may not be a git checkout at all. Every helper
returns ``None`` on any failure.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from . import paths


def _run(args: list[str], cwd: str | os.PathLike[str]) -> str | None:
    try:
        result = subprocess.run(
            args,
            cwd=str(cwd),
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
    return out or None


def current_branch(cwd: str | os.PathLike[str] | None = None) -> str | None:
    root = cwd or paths.repo_root()
    if not (Path(root) / ".git").exists():
        return None
    return _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], root)


def is_worktree(cwd: str | os.PathLike[str] | None = None) -> bool:
    """True if the current checkout is a linked git worktree (not the main one)."""
    root = cwd or paths.repo_root()
    git_path = Path(root) / ".git"
    # In a linked worktree, ``.git`` is a file pointing at the real gitdir.
    return git_path.is_file()


def short_status(cwd: str | os.PathLike[str] | None = None) -> str | None:
    """A one-line summary like ``main (3 changes)`` or ``None`` if not a git repo."""
    branch = current_branch(cwd)
    if branch is None:
        return None
    porcelain = _run(["git", "status", "--porcelain"], cwd or paths.repo_root())
    changes = len(porcelain.splitlines()) if porcelain else 0
    suffix = f" ({changes} change{'s' if changes != 1 else ''})" if changes else ""
    worktree = " [worktree]" if is_worktree(cwd) else ""
    return f"{branch}{suffix}{worktree}"
