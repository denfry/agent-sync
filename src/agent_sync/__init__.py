"""agent-sync: coordinate multiple AI coding-agent sessions in one repository.

This package exposes a small, dependency-free CLI (``agent-sync``) backed by a
SQLite database stored inside the target repository at
``.claude/coordination/state.sqlite``. It lets independent CLI coding-agent
sessions (Claude Code and any other agent or shell) see each other, claim tasks,
lock files, exchange messages and log activity so they avoid stepping on each
other's edits.
"""

__version__ = "0.2.1"

__all__ = ["__version__"]
