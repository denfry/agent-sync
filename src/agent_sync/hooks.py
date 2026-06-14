"""Claude Code hook handlers.

Each handler reads a JSON event from stdin and returns a process exit code. The
guiding principle is **fail open**: malformed or empty input never crashes and
never blocks Claude — *except* for ``pre-tool-use``, which fails *closed* (exit
code 2) when it detects a real lock conflict, because that is the one case where
blocking the edit is the desired behaviour.

The functions accept the already-parsed ``payload`` plus optional ``conn`` /
output streams so tests can drive them without spawning a subprocess.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from typing import TextIO

from . import db, locks, messages, paths, render
from .models import AGENT_IDLE, AGENT_OFFLINE

FILE_EDIT_TOOLS = {"Edit", "Write", "MultiEdit"}


def read_hook_input(stream: TextIO | None = None) -> dict:
    """Parse a hook JSON payload from *stream*, returning ``{}`` on any problem.

    Claude Code may invoke a hook with no stdin (e.g. a manual test) or with
    content this version does not understand; in every such case we degrade to an
    empty dict so callers can fail open.
    """
    stream = stream or sys.stdin
    try:
        raw = stream.read()
    except (OSError, ValueError):
        return {}
    if not raw or not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _agent_id(payload: dict) -> str:
    return db.resolve_agent_id(payload.get("session_id"), payload.get("cwd"))


def _edited_path(payload: dict) -> str | None:
    """Return the file path an Edit/Write/MultiEdit event targets, if any."""
    if payload.get("tool_name") not in FILE_EDIT_TOOLS:
        return None
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    path = tool_input.get("file_path")
    return path if isinstance(path, str) and path else None


def hook_session_start(
    payload: dict,
    *,
    conn: sqlite3.Connection | None = None,
    out: TextIO | None = None,
) -> int:
    """Register/heartbeat the agent and emit compact status as added context."""
    out = out if out is not None else sys.stdout
    own = conn is None
    try:
        if conn is None:
            conn = db.connect()
        agent_id = _agent_id(payload)
        db.ensure_agent(
            conn,
            agent_id,
            session_id=payload.get("session_id"),
            cwd=payload.get("cwd"),
        )
        # Plain Markdown to stdout is added to the session context by Claude Code
        # for SessionStart hooks; this avoids depending on a specific JSON schema.
        out.write(render.render_compact(conn, agent_id))
    except Exception:
        return 0
    finally:
        if own and conn is not None:
            conn.close()
    return 0


def hook_pre_tool_use(
    payload: dict,
    *,
    conn: sqlite3.Connection | None = None,
    err: TextIO | None = None,
) -> int:
    """Block (exit 2) if the target file is locked by another active agent."""
    err = err if err is not None else sys.stderr
    path = _edited_path(payload)
    if path is None:
        return 0  # not a file-editing tool: nothing to check
    own = conn is None
    try:
        if conn is None:
            conn = db.connect()
        agent_id = _agent_id(payload)
        norm = paths.normalize_repo_path(path)
        lock = locks.active_lock_for(conn, norm)
        if lock is not None and lock.owner_agent_id != agent_id:
            owner = db.get_agent(conn, lock.owner_agent_id)
            owner_name = owner.name if owner else lock.owner_agent_id
            reason = f" ({lock.reason})" if lock.reason else ""
            err.write(
                f"[agent-sync] BLOCKED: {norm} is locked by {owner_name} "
                f"[{lock.owner_agent_id}] until {lock.expires_at}{reason}.\n"
                f"Coordinate via `agent-sync send --to {owner_name} "
                f'--message "..."` or wait for the lock to expire.\n'
            )
            return 2  # fail closed
        db.heartbeat(conn, agent_id)
    except Exception:
        return 0  # fail open on anything unexpected
    finally:
        if own and conn is not None:
            conn.close()
    return 0


def hook_post_tool_use(
    payload: dict, *, conn: sqlite3.Connection | None = None
) -> int:
    """Log a successful edit/write into the activity feed; never blocks."""
    own = conn is None
    try:
        if conn is None:
            conn = db.connect()
        agent_id = _agent_id(payload)
        tool = payload.get("tool_name")
        path = _edited_path(payload)
        if path is not None:
            norm = paths.normalize_repo_path(path)
            messages.log_activity(
                conn,
                agent_id,
                event_type="edit",
                body=f"{tool} {norm}",
                tool_name=tool,
                file_path=norm,
            )
        db.heartbeat(conn, agent_id)
    except Exception:
        return 0
    finally:
        if own and conn is not None:
            conn.close()
    return 0


def hook_session_end(
    payload: dict, *, conn: sqlite3.Connection | None = None
) -> int:
    """Mark the agent idle. Locks are left to expire unless auto-release is on."""
    own = conn is None
    try:
        if conn is None:
            conn = db.connect()
        agent_id = _agent_id(payload)
        agent = db.get_agent(conn, agent_id)
        if agent is not None:
            # SessionEnd may carry a reason like "clear"/"logout"; treat an
            # explicit logout as offline, otherwise just idle.
            reason = (payload.get("reason") or "").lower()
            status = AGENT_OFFLINE if reason in {"logout", "exit"} else AGENT_IDLE
            db.set_agent_status(conn, agent_id, status)
        if _truthy(os.environ.get("AGENT_SYNC_AUTO_RELEASE_LOCKS")):
            for lock in locks.list_locks(conn, include_expired=True):
                if lock.owner_agent_id == agent_id:
                    locks.release_lock(conn, agent_id, lock.file_path, force=True)
    except Exception:
        return 0
    finally:
        if own and conn is not None:
            conn.close()
    return 0


# Dispatch table used by the CLI ``hook`` subcommand.
HANDLERS = {
    "session-start": hook_session_start,
    "pre-tool-use": hook_pre_tool_use,
    "post-tool-use": hook_post_tool_use,
    "session-end": hook_session_end,
}


def run_hook(name: str, stream: TextIO | None = None) -> int:
    """Read stdin, dispatch to the named handler, and return its exit code."""
    payload = read_hook_input(stream)
    handler = HANDLERS.get(name)
    if handler is None:
        sys.stderr.write(f"[agent-sync] unknown hook: {name}\n")
        return 0
    return handler(payload)
