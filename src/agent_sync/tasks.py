"""Task operations: create, claim, complete, block and lookup.

Tasks can be referenced by id or (case-insensitive) title in CLI commands, which
keeps the skill instructions readable (``claim-task "Update login UI"``).
Ownership rules mirror locks: a task held by an *active* agent cannot be claimed
by anyone else.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence

from . import db
from .errors import NotFound, TaskConflict
from .models import (
    TASK_BLOCKED,
    TASK_CANCELLED,
    TASK_DONE,
    TASK_IN_PROGRESS,
    TASK_PENDING,
    Task,
)


def create_task(
    conn: sqlite3.Connection,
    title: str,
    *,
    description: str | None = None,
    files: Sequence[str] | None = None,
    priority: int = 0,
) -> Task:
    """Create a pending task and associate any *files* with it."""
    task_id = db.new_id("task")
    ts = db.now_iso()
    with db.transaction(conn):
        conn.execute(
            """INSERT INTO tasks
               (id, title, description, status, owner_agent_id, priority,
                created_at, updated_at, completed_at)
               VALUES (?, ?, ?, ?, NULL, ?, ?, ?, NULL)""",
            (task_id, title, description, TASK_PENDING, priority, ts, ts),
        )
        for path in files or ():
            conn.execute(
                "INSERT INTO task_files (task_id, file_path) VALUES (?, ?)",
                (task_id, path),
            )
    return get_task(conn, task_id)


def get_task(conn: sqlite3.Connection, task_id: str) -> Task:
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        raise NotFound(f"No task with id {task_id!r}")
    return Task.from_row(row)


def find_task(conn: sqlite3.Connection, identifier: str) -> Task:
    """Resolve a task by exact id, else by case-insensitive title.

    Raises :class:`NotFound` if nothing matches and :class:`TaskConflict` if a
    title is ambiguous.
    """
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (identifier,)).fetchone()
    if row is not None:
        return Task.from_row(row)
    rows = conn.execute(
        "SELECT * FROM tasks WHERE lower(title) = lower(?) ORDER BY created_at",
        (identifier,),
    ).fetchall()
    if not rows:
        raise NotFound(f"No task matching {identifier!r}")
    if len(rows) > 1:
        raise TaskConflict(
            f"{identifier!r} matches {len(rows)} tasks; use the task id instead"
        )
    return Task.from_row(rows[0])


def list_tasks(conn: sqlite3.Connection, *, status: str | None = None) -> list[Task]:
    if status:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE status = ? "
            "ORDER BY priority DESC, created_at",
            (status,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM tasks ORDER BY "
            "CASE status WHEN 'in_progress' THEN 0 WHEN 'blocked' THEN 1 "
            "WHEN 'pending' THEN 2 WHEN 'done' THEN 3 ELSE 4 END, "
            "priority DESC, created_at"
        ).fetchall()
    return [Task.from_row(r) for r in rows]


def task_files(conn: sqlite3.Connection, task_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT file_path FROM task_files WHERE task_id = ? ORDER BY file_path",
        (task_id,),
    ).fetchall()
    return [r["file_path"] for r in rows]


def claim_task(conn: sqlite3.Connection, agent_id: str, identifier: str) -> Task:
    """Claim a task for *agent_id*, moving it to ``in_progress``.

    Refuses if the task is already owned by a *different active* agent. Claiming
    a task you already own, or one whose previous owner has gone stale, is fine.
    """
    moment = db.now()
    with db.transaction(conn):
        task = find_task(conn, identifier)
        if task.status in (TASK_DONE, TASK_CANCELLED):
            raise TaskConflict(
                f"Task {task.id} is already {task.status} and cannot be claimed"
            )
        if task.owner_agent_id and task.owner_agent_id != agent_id:
            owner = db.get_agent(conn, task.owner_agent_id)
            if db.is_active(owner, at=moment):
                owner_name = owner.name if owner else task.owner_agent_id
                raise TaskConflict(
                    f"Task {task.id} ({task.title!r}) is owned by active agent "
                    f"{owner_name} ({task.owner_agent_id})"
                )
        conn.execute(
            """UPDATE tasks
               SET owner_agent_id = ?, status = ?, updated_at = ?
               WHERE id = ?""",
            (agent_id, TASK_IN_PROGRESS, moment.isoformat(), task.id),
        )
        conn.execute(
            "UPDATE agents SET current_task_id = ?, last_seen = ? WHERE id = ?",
            (task.id, moment.isoformat(), agent_id),
        )
    return get_task(conn, task.id)


def claim_next_task(
    conn: sqlite3.Connection,
    agent_id: str,
    *,
    include_abandoned: bool = True,
) -> Task | None:
    """Atomically claim the best available task for *agent_id*.

    This is the automatic distribution primitive: instead of naming a task, an
    agent calls this to be handed the next unit of work. Selection order is
    ``priority`` (high first), then oldest ``created_at`` as a tiebreak.

    A task is *available* when it is ``pending`` (nobody owns it) or — when
    *include_abandoned* is set — ``in_progress`` but its owner is no longer an
    active agent, so a crashed session's work is automatically redistributed.
    ``blocked``, ``done`` and ``cancelled`` tasks are never auto-claimed, and a
    task already owned by *agent_id* is skipped (you already have it).

    Returns the claimed :class:`Task`, or ``None`` when nothing is available.
    """
    moment = db.now()
    with db.transaction(conn):
        rows = conn.execute(
            "SELECT * FROM tasks WHERE status IN (?, ?) "
            "ORDER BY priority DESC, created_at",
            (TASK_PENDING, TASK_IN_PROGRESS),
        ).fetchall()
        chosen: Task | None = None
        for row in rows:
            task = Task.from_row(row)
            if task.owner_agent_id == agent_id:
                continue  # already mine — nothing to hand over
            if not task.owner_agent_id:
                chosen = task  # unowned pending work: take it
                break
            if include_abandoned:
                owner = db.get_agent(conn, task.owner_agent_id)
                if not db.is_active(owner, at=moment):
                    chosen = task  # owner went stale/offline: reclaim it
                    break
        if chosen is None:
            return None
        conn.execute(
            """UPDATE tasks
               SET owner_agent_id = ?, status = ?, updated_at = ?
               WHERE id = ?""",
            (agent_id, TASK_IN_PROGRESS, moment.isoformat(), chosen.id),
        )
        conn.execute(
            "UPDATE agents SET current_task_id = ?, last_seen = ? WHERE id = ?",
            (chosen.id, moment.isoformat(), agent_id),
        )
        chosen_id = chosen.id
    return get_task(conn, chosen_id)


def complete_task(conn: sqlite3.Connection, agent_id: str, identifier: str) -> Task:
    """Mark a task done and clear it as the owner's current task."""
    ts = db.now_iso()
    with db.transaction(conn):
        task = find_task(conn, identifier)
        conn.execute(
            """UPDATE tasks
               SET status = ?, completed_at = ?, updated_at = ?
               WHERE id = ?""",
            (TASK_DONE, ts, ts, task.id),
        )
        conn.execute(
            "UPDATE agents SET current_task_id = NULL, last_seen = ? "
            "WHERE current_task_id = ?",
            (ts, task.id),
        )
    return get_task(conn, task.id)


def block_task(
    conn: sqlite3.Connection, agent_id: str, identifier: str, reason: str
) -> Task:
    """Mark a task blocked, recording *reason* in its description trail."""
    ts = db.now_iso()
    with db.transaction(conn):
        task = find_task(conn, identifier)
        note = f"[blocked] {reason}"
        description = f"{task.description}\n{note}" if task.description else note
        conn.execute(
            "UPDATE tasks SET status = ?, description = ?, updated_at = ? WHERE id = ?",
            (TASK_BLOCKED, description, ts, task.id),
        )
    return get_task(conn, task.id)
