"""Messaging, decisions and the activity log.

Messages are addressed to an exact agent id, an agent name, a role, or the
broadcast sentinel ``all``. An agent's inbox is the union of everything visible
to its id, name and role, plus broadcasts.
"""

from __future__ import annotations

import sqlite3

from . import db
from .errors import NotFound
from .models import RECIPIENT_ALL, Activity, Decision, Message


def send_message(
    conn: sqlite3.Connection, sender_agent_id: str, recipient: str, body: str
) -> Message:
    """Send *body* to *recipient* (an id, name, role, or ``all``)."""
    msg_id = db.new_id("msg")
    ts = db.now_iso()
    with db.transaction(conn):
        conn.execute(
            """INSERT INTO messages
               (id, sender_agent_id, recipient, body, created_at, read_at)
               VALUES (?, ?, ?, ?, ?, NULL)""",
            (msg_id, sender_agent_id, recipient, body, ts),
        )
    return get_message(conn, msg_id)


def get_message(conn: sqlite3.Connection, message_id: str) -> Message:
    row = conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
    if row is None:
        raise NotFound(f"No message with id {message_id!r}")
    return Message.from_row(row)


def _recipient_keys(conn: sqlite3.Connection, agent_id: str) -> set[str]:
    """All recipient strings that should land in *agent_id*'s inbox."""
    keys = {agent_id, RECIPIENT_ALL}
    agent = db.get_agent(conn, agent_id)
    if agent:
        if agent.name:
            keys.add(agent.name)
        if agent.role:
            keys.add(agent.role)
    return keys


def inbox(
    conn: sqlite3.Connection, agent_id: str, *, unread_only: bool = False
) -> list[Message]:
    """Messages addressed to this agent, newest last.

    Messages the agent sent to itself or broadcast are included; its own
    outbound messages to *other* recipients are not.
    """
    keys = _recipient_keys(conn, agent_id)
    placeholders = ",".join("?" for _ in keys)
    sql = f"SELECT * FROM messages WHERE recipient IN ({placeholders})"
    params: list[object] = list(keys)
    if unread_only:
        sql += " AND read_at IS NULL"
    sql += " ORDER BY created_at"
    rows = conn.execute(sql, params).fetchall()
    return [Message.from_row(r) for r in rows]


def unread_count(conn: sqlite3.Connection, agent_id: str) -> int:
    return len(inbox(conn, agent_id, unread_only=True))


def read_message(
    conn: sqlite3.Connection, agent_id: str, message_id: str
) -> Message:
    """Mark a message read (idempotent) and return it."""
    with db.transaction(conn):
        row = conn.execute(
            "SELECT * FROM messages WHERE id = ?", (message_id,)
        ).fetchone()
        if row is None:
            raise NotFound(f"No message with id {message_id!r}")
        if row["read_at"] is None:
            conn.execute(
                "UPDATE messages SET read_at = ? WHERE id = ?",
                (db.now_iso(), message_id),
            )
    return get_message(conn, message_id)


def add_decision(conn: sqlite3.Connection, agent_id: str, body: str) -> Decision:
    """Record an architecture/processs decision in the shared log."""
    dec_id = db.new_id("dec")
    ts = db.now_iso()
    with db.transaction(conn):
        conn.execute(
            "INSERT INTO decisions (id, agent_id, body, created_at) VALUES (?, ?, ?, ?)",
            (dec_id, agent_id, body, ts),
        )
    row = conn.execute("SELECT * FROM decisions WHERE id = ?", (dec_id,)).fetchone()
    return Decision.from_row(row)


def list_decisions(conn: sqlite3.Connection, *, limit: int = 20) -> list[Decision]:
    rows = conn.execute(
        "SELECT * FROM decisions ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    return [Decision.from_row(r) for r in rows]


def log_activity(
    conn: sqlite3.Connection,
    agent_id: str | None,
    event_type: str,
    body: str,
    *,
    tool_name: str | None = None,
    file_path: str | None = None,
) -> Activity:
    """Append an entry to the activity log."""
    act_id = db.new_id("act")
    ts = db.now_iso()
    with db.transaction(conn):
        conn.execute(
            """INSERT INTO activity
               (id, agent_id, event_type, body, tool_name, file_path, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (act_id, agent_id, event_type, body, tool_name, file_path, ts),
        )
    row = conn.execute("SELECT * FROM activity WHERE id = ?", (act_id,)).fetchone()
    return Activity.from_row(row)


def recent_activity(conn: sqlite3.Connection, *, limit: int = 10) -> list[Activity]:
    rows = conn.execute(
        "SELECT * FROM activity ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    return [Activity.from_row(r) for r in rows]
