"""File lock operations with TTL and active-owner semantics.

A lock conflicts with a new acquirer only when it is held by a *different*
*active* agent and has not expired. Expired locks and locks owned by
stale/offline agents are transparent: they can be taken over and are cleaned up
by :func:`gc_locks`.
"""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime

from . import db
from .db import DEFAULT_LOCK_TTL_MINUTES
from .errors import LockConflict
from .models import LOCK_FILE, Lock


def active_lock_for(
    conn: sqlite3.Connection, file_path: str, *, at: datetime | None = None
) -> Lock | None:
    """Return the live lock on *file_path*, or ``None`` if free/expired.

    A lock is only "live" if it has not passed its ``expires_at`` *and* its owner
    still counts as active. Otherwise the file is effectively unlocked.
    """
    moment = at or db.now()
    row = conn.execute("SELECT * FROM locks WHERE file_path = ?", (file_path,)).fetchone()
    if row is None:
        return None
    lock = Lock.from_row(row)
    if db.parse_iso(lock.expires_at) <= moment:
        return None
    owner = db.get_agent(conn, lock.owner_agent_id)
    if not db.is_active(owner, at=moment):
        return None
    return lock


def acquire_lock(
    conn: sqlite3.Connection,
    agent_id: str,
    file_path: str,
    *,
    reason: str | None = None,
    ttl_minutes: int = DEFAULT_LOCK_TTL_MINUTES,
    kind: str = LOCK_FILE,
) -> Lock:
    """Acquire (or refresh) a lock on *file_path* for *agent_id*.

    Re-locking a file you already hold simply extends the TTL. Acquiring a file
    held live by someone else raises :class:`LockConflict` (exit code 2). *kind*
    distinguishes a normal file-path lock from an arbitrary named resource lock
    (``LOCK_RESOURCE``); both share the ``locks`` table keyed by *file_path*.
    """
    moment = db.now()
    with db.transaction(conn):
        row = conn.execute(
            "SELECT * FROM locks WHERE file_path = ?", (file_path,)
        ).fetchone()
        if row is not None:
            existing = Lock.from_row(row)
            still_valid = db.parse_iso(existing.expires_at) > moment
            owner = db.get_agent(conn, existing.owner_agent_id)
            owner_active = db.is_active(owner, at=moment)
            if (
                existing.owner_agent_id != agent_id
                and still_valid
                and owner_active
            ):
                owner_name = owner.name if owner else existing.owner_agent_id
                raise LockConflict(
                    f"{file_path} is locked by {owner_name} "
                    f"({existing.owner_agent_id}) until {existing.expires_at}"
                    + (f": {existing.reason}" if existing.reason else "")
                )

        created = moment.isoformat()
        expires = db.iso_in(ttl_minutes, _from=moment)
        conn.execute(
            """INSERT INTO locks
                   (file_path, owner_agent_id, reason, created_at, expires_at, kind)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(file_path) DO UPDATE SET
                   owner_agent_id = excluded.owner_agent_id,
                   reason = excluded.reason,
                   created_at = excluded.created_at,
                   expires_at = excluded.expires_at,
                   kind = excluded.kind""",
            (file_path, agent_id, reason, created, expires, kind),
        )
    out = conn.execute("SELECT * FROM locks WHERE file_path = ?", (file_path,)).fetchone()
    return Lock.from_row(out)


def acquire_lock_blocking(
    conn: sqlite3.Connection,
    agent_id: str,
    file_path: str,
    *,
    reason: str | None = None,
    ttl_minutes: int = DEFAULT_LOCK_TTL_MINUTES,
    kind: str = LOCK_FILE,
    wait_seconds: float,
    poll_seconds: float = 0.5,
) -> Lock:
    """Like :func:`acquire_lock`, but wait up to *wait_seconds* for a busy lock.

    The conflicting holder may finish (unlock), go stale, or let the lock expire;
    we retry on a short interval until one of those frees it or the deadline
    passes, in which case the final :class:`LockConflict` propagates (exit 2,
    fail-closed contract preserved). The **CLI subprocess** does the sleeping, so
    an agent issues a single blocking call rather than spinning retries itself.
    """
    deadline = time.monotonic() + max(0.0, wait_seconds)
    while True:
        try:
            return acquire_lock(
                conn,
                agent_id,
                file_path,
                reason=reason,
                ttl_minutes=ttl_minutes,
                kind=kind,
            )
        except LockConflict:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise
            time.sleep(min(poll_seconds, remaining))


def release_lock(
    conn: sqlite3.Connection,
    agent_id: str,
    file_path: str,
    *,
    force: bool = False,
) -> bool:
    """Release a lock. Returns ``True`` if a row was removed.

    Only the owner may release a lock unless *force* is given. Releasing a
    non-existent lock returns ``False`` rather than raising.
    """
    with db.transaction(conn):
        row = conn.execute(
            "SELECT * FROM locks WHERE file_path = ?", (file_path,)
        ).fetchone()
        if row is None:
            return False
        lock = Lock.from_row(row)
        if not force and lock.owner_agent_id != agent_id:
            raise LockConflict(
                f"{file_path} is locked by {lock.owner_agent_id}; "
                f"use --force to override"
            )
        conn.execute("DELETE FROM locks WHERE file_path = ?", (file_path,))
    return True


def list_locks(
    conn: sqlite3.Connection,
    *,
    include_expired: bool = False,
    at: datetime | None = None,
) -> list[Lock]:
    """List locks, by default only those that are currently live."""
    moment = at or db.now()
    rows = conn.execute("SELECT * FROM locks ORDER BY created_at").fetchall()
    locks = [Lock.from_row(r) for r in rows]
    if include_expired:
        return locks
    live: list[Lock] = []
    for lock in locks:
        if db.parse_iso(lock.expires_at) <= moment:
            continue
        owner = db.get_agent(conn, lock.owner_agent_id)
        if not db.is_active(owner, at=moment):
            continue
        live.append(lock)
    return live


def gc_locks(conn: sqlite3.Connection, *, at: datetime | None = None) -> int:
    """Delete expired locks and locks whose owner is no longer active.

    Returns the number of rows removed.
    """
    moment = at or db.now()
    removed = 0
    with db.transaction(conn):
        for row in conn.execute("SELECT * FROM locks").fetchall():
            lock = Lock.from_row(row)
            expired = db.parse_iso(lock.expires_at) <= moment
            owner = db.get_agent(conn, lock.owner_agent_id)
            if expired or not db.is_active(owner, at=moment):
                conn.execute("DELETE FROM locks WHERE file_path = ?", (lock.file_path,))
                removed += 1
    return removed
