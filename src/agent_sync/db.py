"""SQLite data layer: connection, schema, time helpers and agent records.

All writes go through short, explicit transactions. Timestamps are stored as
UTC ISO-8601 strings (``...+00:00``) so they sort lexicographically and parse
back with :func:`datetime.fromisoformat` on Python 3.10+.

Higher-level domain modules (:mod:`agent_sync.locks`, :mod:`agent_sync.tasks`,
:mod:`agent_sync.messages`) build on the primitives here. Agent records live in
this module because almost every other operation needs to read or refresh them.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import paths
from .models import (
    AGENT_ACTIVE,
    AGENT_OFFLINE,
    AGENT_STALE,
    Agent,
)

# An agent that has not checked in for this long is considered stale; longer
# still and it is treated as offline. These thresholds drive conflict checks and
# the ``gc`` command. They are intentionally generous because Claude Code
# sessions can sit idle while a human reads output.
STALE_AFTER = timedelta(minutes=15)
OFFLINE_AFTER = timedelta(minutes=120)

# Default time-to-live for a file lock.
DEFAULT_LOCK_TTL_MINUTES = 60

SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    role            TEXT,
    session_id      TEXT,
    cwd             TEXT,
    status          TEXT NOT NULL,
    current_task_id TEXT,
    created_at      TEXT NOT NULL,
    last_seen       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    description   TEXT,
    status        TEXT NOT NULL,
    owner_agent_id TEXT,
    priority      INTEGER DEFAULT 0,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    completed_at  TEXT
);

CREATE TABLE IF NOT EXISTS task_files (
    task_id   TEXT NOT NULL,
    file_path TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS locks (
    file_path      TEXT PRIMARY KEY,
    owner_agent_id TEXT NOT NULL,
    reason         TEXT,
    created_at     TEXT NOT NULL,
    expires_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id              TEXT PRIMARY KEY,
    sender_agent_id TEXT NOT NULL,
    recipient       TEXT NOT NULL,
    body            TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    read_at         TEXT
);

-- Per-recipient push delivery. ``read_at`` on a message is a single global
-- acknowledgement flag, but a broadcast (recipient ``all``/a role) reaches many
-- agents, so "has this been pushed into *that* agent's context yet?" needs its
-- own per-(message, agent) record. The push hooks insert a row here once they
-- inject a message so the same message is never re-pushed to the same agent.
CREATE TABLE IF NOT EXISTS message_deliveries (
    message_id   TEXT NOT NULL,
    agent_id     TEXT NOT NULL,
    delivered_at TEXT NOT NULL,
    PRIMARY KEY (message_id, agent_id)
);

CREATE TABLE IF NOT EXISTS decisions (
    id         TEXT PRIMARY KEY,
    agent_id   TEXT NOT NULL,
    body       TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS activity (
    id         TEXT PRIMARY KEY,
    agent_id   TEXT,
    event_type TEXT NOT NULL,
    body       TEXT NOT NULL,
    tool_name  TEXT,
    file_path  TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_task_files_task ON task_files(task_id);
CREATE INDEX IF NOT EXISTS idx_messages_recipient ON messages(recipient);
CREATE INDEX IF NOT EXISTS idx_activity_created ON activity(created_at);
CREATE INDEX IF NOT EXISTS idx_deliveries_agent ON message_deliveries(agent_id);
"""

TABLE_NAMES = (
    "agents",
    "tasks",
    "task_files",
    "locks",
    "messages",
    "message_deliveries",
    "decisions",
    "activity",
)


# --- time helpers -----------------------------------------------------------
def now() -> datetime:
    """Current time as a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def now_iso() -> str:
    """Current UTC time as an ISO-8601 string, e.g. ``2026-06-14T12:00:00+00:00``."""
    return now().isoformat()


def parse_iso(value: str) -> datetime:
    """Parse an ISO timestamp, tolerating a trailing ``Z``."""
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def iso_in(minutes: int, *, _from: datetime | None = None) -> str:
    """ISO timestamp *minutes* in the future from now (or *_from*)."""
    base = _from or now()
    return (base + timedelta(minutes=minutes)).isoformat()


def new_id(prefix: str) -> str:
    """Short unique id with a human-readable prefix, e.g. ``task-1a2b3c4d``."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


# --- connection / schema ----------------------------------------------------
def connect(path: str | os.PathLike[str] | None = None) -> sqlite3.Connection:
    """Open a connection to the coordination database, creating its directory.

    The database file and schema are created on demand so every command can
    safely auto-init. ``WAL`` journaling plus a busy timeout make concurrent
    access from several Claude Code processes robust.
    """
    db_file = Path(path) if path is not None else paths.db_path()
    db_file.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_file), timeout=30.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    init_db(conn)
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Create all tables and indexes if they do not already exist.

    ``executescript`` manages its own transaction (it issues an implicit COMMIT
    first), so it must not run inside our explicit ``transaction`` block. The
    statements are idempotent (``IF NOT EXISTS``), making this safe to call on
    every connection.
    """
    conn.executescript(SCHEMA)


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run a block inside an explicit ``BEGIN IMMEDIATE`` transaction.

    ``isolation_level=None`` means we drive transactions by hand, which keeps the
    locking window small and explicit. ``BEGIN IMMEDIATE`` acquires the write
    lock up front so two racing agents serialize cleanly instead of one failing
    late with ``database is locked``.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


# --- agent identity ---------------------------------------------------------
def resolve_agent_id(
    session_id: str | None = None, cwd: str | None = None
) -> str:
    """Resolve the current agent's stable id.

    Order of precedence:

    1. ``AGENT_SYNC_ID`` environment variable (explicit override).
    2. A deterministic hash of the Claude Code **session id**, so every call made
       on behalf of one window maps to the same agent. The session id comes from
       the *session_id* argument (passed by hooks from their JSON payload) or,
       when called from the skill/CLI outside a hook, from the
       ``CLAUDE_CODE_SESSION_ID`` environment variable Claude Code exports into
       every shell it spawns (``CLAUDE_SESSION_ID`` is accepted as a legacy
       alias). This is what lets the skill auto-detect the active session: a hook
       and a skill CLI call in the *same* window resolve to the *same* agent id.

       The id is intentionally derived from the session id **alone**, not the
       cwd: session ids are globally unique and the coordination database is
       already scoped to one repo, so adding cwd would only let differing path
       spellings (``/tmp/x`` vs ``C:\\...\\x``, symlinks, trailing slashes)
       between a hook payload and ``os.getcwd()`` split one window into two
       agents — which would make a session block its *own* locked files.
    3. A persisted per-repo local id (``.claude/coordination/current-agent``).

    *cwd* is still accepted (and recorded elsewhere on the agent row) but no
    longer affects identity.
    """
    explicit = os.environ.get("AGENT_SYNC_ID")
    if explicit:
        return explicit.strip()

    sid = (
        session_id
        or os.environ.get("CLAUDE_CODE_SESSION_ID")
        or os.environ.get("CLAUDE_SESSION_ID")
    )
    if sid:
        digest = hashlib.sha1(sid.strip().encode("utf-8")).hexdigest()[:12]
        return f"agent-{digest}"

    return paths.read_or_create_local_agent_id()


# --- agent records ----------------------------------------------------------
def get_agent(conn: sqlite3.Connection, agent_id: str) -> Agent | None:
    row = conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
    return Agent.from_row(row) if row else None


def list_agents(conn: sqlite3.Connection) -> list[Agent]:
    rows = conn.execute("SELECT * FROM agents ORDER BY last_seen DESC").fetchall()
    return [Agent.from_row(r) for r in rows]


def ensure_agent(
    conn: sqlite3.Connection,
    agent_id: str,
    *,
    name: str | None = None,
    role: str | None = None,
    session_id: str | None = None,
    cwd: str | None = None,
    status: str = AGENT_ACTIVE,
) -> Agent:
    """Insert or update an agent and refresh ``last_seen``.

    Only non-``None`` fields overwrite existing values, so a bare heartbeat keeps
    a previously-registered name and role intact.
    """
    ts = now_iso()
    with transaction(conn):
        existing = conn.execute(
            "SELECT * FROM agents WHERE id = ?", (agent_id,)
        ).fetchone()
        if existing is None:
            conn.execute(
                """INSERT INTO agents
                   (id, name, role, session_id, cwd, status, current_task_id,
                    created_at, last_seen)
                   VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?)""",
                (
                    agent_id,
                    name or _default_name(agent_id, cwd),
                    role,
                    session_id,
                    cwd,
                    status,
                    ts,
                    ts,
                ),
            )
        else:
            conn.execute(
                """UPDATE agents
                   SET name = COALESCE(?, name),
                       role = COALESCE(?, role),
                       session_id = COALESCE(?, session_id),
                       cwd = COALESCE(?, cwd),
                       status = ?,
                       last_seen = ?
                   WHERE id = ?""",
                (name, role, session_id, cwd, status, ts, agent_id),
            )
    agent = get_agent(conn, agent_id)
    assert agent is not None  # just upserted
    return agent


def heartbeat(conn: sqlite3.Connection, agent_id: str) -> Agent:
    """Touch ``last_seen`` and re-activate the agent (creating it if needed)."""
    return ensure_agent(conn, agent_id, status=AGENT_ACTIVE)


def set_agent_status(conn: sqlite3.Connection, agent_id: str, status: str) -> None:
    with transaction(conn):
        conn.execute(
            "UPDATE agents SET status = ?, last_seen = ? WHERE id = ?",
            (status, now_iso(), agent_id),
        )


def set_current_task(
    conn: sqlite3.Connection, agent_id: str, task_id: str | None
) -> None:
    with transaction(conn):
        conn.execute(
            "UPDATE agents SET current_task_id = ?, last_seen = ? WHERE id = ?",
            (task_id, now_iso(), agent_id),
        )


def effective_status(agent: Agent, *, at: datetime | None = None) -> str:
    """Live status taking ``last_seen`` into account.

    A stored ``active``/``idle`` status decays to ``stale`` and then ``offline``
    based on how long ago the agent last checked in. This is what conflict checks
    use, so a crashed session never blocks others forever.
    """
    if agent.status == AGENT_OFFLINE:
        return AGENT_OFFLINE
    moment = at or now()
    age = moment - parse_iso(agent.last_seen)
    if age >= OFFLINE_AFTER:
        return AGENT_OFFLINE
    if age >= STALE_AFTER:
        return AGENT_STALE
    return agent.status


def is_active(agent: Agent | None, *, at: datetime | None = None) -> bool:
    """True if *agent* exists and currently counts as active."""
    return agent is not None and effective_status(agent, at=at) == AGENT_ACTIVE


def gc_agents(conn: sqlite3.Connection) -> int:
    """Persist decayed statuses for stale/offline agents. Returns rows changed."""
    moment = now()
    changed = 0
    with transaction(conn):
        for row in conn.execute("SELECT * FROM agents").fetchall():
            agent = Agent.from_row(row)
            live = effective_status(agent, at=moment)
            if live in (AGENT_STALE, AGENT_OFFLINE) and live != agent.status:
                conn.execute(
                    "UPDATE agents SET status = ? WHERE id = ?", (live, agent.id)
                )
                changed += 1
    return changed


def _default_name(agent_id: str, cwd: str | None) -> str:
    """Pick a friendly default name when an agent registers implicitly."""
    if cwd:
        base = Path(cwd).name
        if base:
            return base
    return agent_id
