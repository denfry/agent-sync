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
    conn: sqlite3.Connection,
    sender_agent_id: str,
    recipient: str,
    body: str,
    *,
    reply_to: str | None = None,
) -> Message:
    """Send *body* to *recipient* (an id, name, role, or ``all``).

    *reply_to*, when given, threads this message under an existing message id; it
    must reference a real message (raises :class:`NotFound` otherwise).
    """
    msg_id = db.new_id("msg")
    ts = db.now_iso()
    with db.transaction(conn):
        if reply_to is not None:
            parent = conn.execute(
                "SELECT 1 FROM messages WHERE id = ?", (reply_to,)
            ).fetchone()
            if parent is None:
                raise NotFound(f"No message with id {reply_to!r} to reply to")
        conn.execute(
            """INSERT INTO messages
               (id, sender_agent_id, recipient, body, created_at, read_at, reply_to)
               VALUES (?, ?, ?, ?, ?, NULL, ?)""",
            (msg_id, sender_agent_id, recipient, body, ts, reply_to),
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


def recent_messages(conn: sqlite3.Connection, *, limit: int = 20) -> list[Message]:
    """All recent messages regardless of recipient, newest first.

    Unlike :func:`inbox` (scoped to one agent), this is the global stream the
    live console tails so a human can watch every conversation between agents.
    """
    rows = conn.execute(
        "SELECT * FROM messages ORDER BY created_at DESC, id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [Message.from_row(r) for r in rows]


def undelivered(
    conn: sqlite3.Connection, agent_id: str, *, directed_only: bool = False
) -> list[Message]:
    """Messages addressed to *agent_id* that have not yet been *pushed* to it.

    "Delivered" here means "already injected into this agent's context by a push
    hook", tracked per-(message, agent) in ``message_deliveries`` — distinct from
    ``read_at``, which is the agent's explicit acknowledgement. The agent's own
    outbound messages are excluded so it never gets its own broadcasts pushed
    back. With *directed_only*, broadcasts (recipient ``all``) are skipped so only
    messages aimed at this specific agent/name/role are returned — that is what
    justifies interrupting the ``Stop`` hook, whereas broadcasts surface gently.
    """
    keys = _recipient_keys(conn, agent_id)
    placeholders = ",".join("?" for _ in keys)
    sql = (
        f"SELECT m.* FROM messages m "
        f"WHERE m.recipient IN ({placeholders}) "
        f"AND m.sender_agent_id != ? "
        f"AND NOT EXISTS ("
        f"  SELECT 1 FROM message_deliveries d "
        f"  WHERE d.message_id = m.id AND d.agent_id = ?)"
    )
    params: list[object] = [*keys, agent_id, agent_id]
    if directed_only:
        sql += " AND m.recipient != ?"
        params.append(RECIPIENT_ALL)
    sql += " ORDER BY m.created_at"
    rows = conn.execute(sql, params).fetchall()
    return [Message.from_row(r) for r in rows]


def mark_delivered(
    conn: sqlite3.Connection, agent_id: str, message_ids: list[str]
) -> None:
    """Record that *message_ids* were pushed into *agent_id*'s context.

    Idempotent: re-marking an already-delivered message is a no-op, so a push
    hook can call this freely without risking duplicate rows.
    """
    if not message_ids:
        return
    ts = db.now_iso()
    with db.transaction(conn):
        conn.executemany(
            "INSERT OR IGNORE INTO message_deliveries "
            "(message_id, agent_id, delivered_at) VALUES (?, ?, ?)",
            [(mid, agent_id, ts) for mid in message_ids],
        )


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


def ack_message(
    conn: sqlite3.Connection, agent_id: str, message_id: str
) -> Message:
    """Acknowledge a message (idempotent) so its *sender* can confirm receipt.

    Distinct from ``read_at`` (which the recipient sets by viewing it): ``acked_at``
    is an explicit "I have handled this" that closes the loop for whoever sent it.
    """
    with db.transaction(conn):
        row = conn.execute(
            "SELECT * FROM messages WHERE id = ?", (message_id,)
        ).fetchone()
        if row is None:
            raise NotFound(f"No message with id {message_id!r}")
        if row["acked_at"] is None:
            conn.execute(
                "UPDATE messages SET acked_at = ? WHERE id = ?",
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
