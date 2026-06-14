"""Typed errors for agent-sync.

The CLI maps these onto process exit codes. The most important contract is that
a :class:`LockConflict` results in exit code ``2`` (fail closed) so that a Claude
Code ``PreToolUse`` hook blocks the edit, while almost everything else either
succeeds or fails open.
"""

from __future__ import annotations


class AgentSyncError(Exception):
    """Base class for all expected, user-facing errors.

    ``exit_code`` is the process exit status the CLI should return when this
    error escapes a command handler.
    """

    exit_code: int = 1

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class LockConflict(AgentSyncError):
    """Raised when a file is locked by another active agent.

    Uses exit code ``2`` so Claude Code's ``PreToolUse`` hook treats it as a
    hard block (fail closed) rather than a generic error.
    """

    exit_code = 2


class TaskConflict(AgentSyncError):
    """Raised when a task cannot be claimed/modified due to ownership rules."""

    exit_code = 1


class NotFound(AgentSyncError):
    """Raised when a referenced task, message or agent does not exist."""

    exit_code = 1


class UsageError(AgentSyncError):
    """Raised for invalid command usage that argparse cannot catch on its own."""

    exit_code = 2
