"""Task operations: create, claim, complete, block and lookup.

Tasks can be referenced by id or (case-insensitive) title in CLI commands, which
keeps the skill instructions readable (``claim-task "Update login UI"``).
Ownership rules mirror locks: a task held by an *active* agent cannot be claimed
by anyone else.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence

from . import db, locks, paths
from .errors import LockConflict, NotFound, TaskConflict
from .models import (
    TASK_BLOCKED,
    TASK_CANCELLED,
    TASK_DONE,
    TASK_IN_PROGRESS,
    TASK_PENDING,
    Task,
)

# A dependency no longer blocks once it is done or cancelled (cancelled work will
# never complete, so waiting on it forever would deadlock the dependent).
_DEP_RESOLVED_STATUSES = (TASK_DONE, TASK_CANCELLED)


def create_task(
    conn: sqlite3.Connection,
    title: str,
    *,
    description: str | None = None,
    files: Sequence[str] | None = None,
    priority: int = 0,
    depends_on: Sequence[str] | None = None,
) -> Task:
    """Create a pending task and associate any *files* and dependencies with it.

    *depends_on* entries are task ids or titles, resolved up front so a typo
    fails fast (:class:`NotFound`). The task is claimable only once every
    dependency is done/cancelled (see :func:`unmet_dependencies`).
    """
    task_id = db.new_id("task")
    ts = db.now_iso()
    with db.transaction(conn):
        dep_ids = [find_task(conn, dep).id for dep in depends_on or ()]
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
        for dep_id in dep_ids:
            conn.execute(
                "INSERT OR IGNORE INTO task_deps (task_id, depends_on_id) "
                "VALUES (?, ?)",
                (task_id, dep_id),
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


def task_dependencies(conn: sqlite3.Connection, task_id: str) -> list[str]:
    """Ids of the tasks *task_id* depends on (must finish first)."""
    rows = conn.execute(
        "SELECT depends_on_id FROM task_deps WHERE task_id = ? "
        "ORDER BY depends_on_id",
        (task_id,),
    ).fetchall()
    return [r["depends_on_id"] for r in rows]


def unmet_dependencies(conn: sqlite3.Connection, task_id: str) -> list[Task]:
    """Dependencies of *task_id* that are not yet resolved (done/cancelled).

    A dangling dependency id (the referenced task was deleted) is treated as met
    rather than blocking forever.
    """
    unmet: list[Task] = []
    for dep_id in task_dependencies(conn, task_id):
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (dep_id,)).fetchone()
        if row is None:
            continue
        dep = Task.from_row(row)
        if dep.status not in _DEP_RESOLVED_STATUSES:
            unmet.append(dep)
    return unmet


def dependents_unblocked_by(conn: sqlite3.Connection, task_id: str) -> list[Task]:
    """Pending tasks that depend on *task_id* and now have all deps resolved.

    Used after completing a task to surface work that just became claimable;
    "blocked by deps" is computed, so no status flip is needed to unblock them.
    """
    rows = conn.execute(
        "SELECT task_id FROM task_deps WHERE depends_on_id = ?", (task_id,)
    ).fetchall()
    unblocked: list[Task] = []
    for r in rows:
        dependent_id = r["task_id"]
        if unmet_dependencies(conn, dependent_id):
            continue
        row = conn.execute(
            "SELECT * FROM tasks WHERE id = ?", (dependent_id,)
        ).fetchone()
        if row is not None and row["status"] == TASK_PENDING:
            unblocked.append(Task.from_row(row))
    return unblocked


def lock_task_files(
    conn: sqlite3.Connection, agent_id: str, task_id: str
) -> tuple[list[str], list[str]]:
    """Best-effort: lock every file associated with *task_id* for *agent_id*.

    Paths are normalized to the same canonical form the PreToolUse hook checks, so
    the locks actually guard edits. Returns ``(locked, conflicts)`` where
    *conflicts* holds human-readable messages for files another active agent
    already holds — the caller keeps the claim and just warns.
    """
    locked: list[str] = []
    conflicts: list[str] = []
    for raw in task_files(conn, task_id):
        norm = paths.normalize_repo_path(raw)
        try:
            locks.acquire_lock(conn, agent_id, norm, reason=f"task {task_id}")
            locked.append(norm)
        except LockConflict as exc:
            conflicts.append(exc.message)
    return locked, conflicts


def claim_task(
    conn: sqlite3.Connection,
    agent_id: str,
    identifier: str,
    *,
    force: bool = False,
) -> Task:
    """Claim a task for *agent_id*, moving it to ``in_progress``.

    Refuses if the task is already owned by a *different active* agent. Claiming
    a task you already own, or one whose previous owner has gone stale, is fine.
    A task with unfinished dependencies is refused unless *force* is set, so work
    is not started out of order by accident.
    """
    moment = db.now()
    with db.transaction(conn):
        task = find_task(conn, identifier)
        if task.status in (TASK_DONE, TASK_CANCELLED):
            raise TaskConflict(
                f"Task {task.id} is already {task.status} and cannot be claimed"
            )
        if not force:
            unmet = unmet_dependencies(conn, task.id)
            if unmet:
                names = ", ".join(f"{t.id} ({t.title!r})" for t in unmet)
                raise TaskConflict(
                    f"Task {task.id} depends on unfinished task(s): {names}. "
                    f"Use --force to claim anyway."
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
    ``blocked``, ``done`` and ``cancelled`` tasks are never auto-claimed, a task
    with an unfinished dependency is skipped (and becomes claimable automatically
    once the dependency completes), and a task already owned by *agent_id* is
    skipped (you already have it).

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
            if unmet_dependencies(conn, task.id):
                continue  # blocked by an unfinished dependency
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
