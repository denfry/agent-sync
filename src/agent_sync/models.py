"""Dataclasses and status constants mirroring the SQLite schema.

These are deliberately thin: rows come back from :mod:`sqlite3` as ``Row``
objects and are converted with the ``from_row`` classmethods. Keeping the
shapes in one place makes the renderers and tests easy to reason about.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from sqlite3 import Row

# --- Lock kinds -------------------------------------------------------------
# A lock keyed by a repo-relative file path (the default, enforced by the
# PreToolUse hook) versus an arbitrary named resource key (e.g. ``db-migrations``)
# that agents agree on but that maps to no single file.
LOCK_FILE = "file"
LOCK_RESOURCE = "resource"

# --- Agent statuses ---------------------------------------------------------
AGENT_ACTIVE = "active"
AGENT_IDLE = "idle"
AGENT_STALE = "stale"
AGENT_OFFLINE = "offline"
AGENT_STATUSES = (AGENT_ACTIVE, AGENT_IDLE, AGENT_STALE, AGENT_OFFLINE)

# --- Task statuses ----------------------------------------------------------
TASK_PENDING = "pending"
TASK_IN_PROGRESS = "in_progress"
TASK_BLOCKED = "blocked"
TASK_DONE = "done"
TASK_CANCELLED = "cancelled"
TASK_STATUSES = (TASK_PENDING, TASK_IN_PROGRESS, TASK_BLOCKED, TASK_DONE, TASK_CANCELLED)
TASK_OPEN_STATUSES = (TASK_PENDING, TASK_IN_PROGRESS, TASK_BLOCKED)

# Recipient sentinel meaning "every agent".
RECIPIENT_ALL = "all"

# Reserved identity for a human watching/steering via the live console
# (``agent-sync console``). It registers as a normal active agent so its locks
# are enforced and its messages are pushed to agents like any other, but the
# renderers filter it out of the *agent* sections so it never inflates the
# "active agents" count or looks like a code-editing peer.
OPERATOR_ID = "operator"
OPERATOR_ROLE = "human operator"


@dataclass
class Agent:
    id: str
    name: str
    role: str | None
    session_id: str | None
    cwd: str | None
    status: str
    current_task_id: str | None
    created_at: str
    last_seen: str

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_row(cls, row: Row) -> Agent:
        return cls(
            id=row["id"],
            name=row["name"],
            role=row["role"],
            session_id=row["session_id"],
            cwd=row["cwd"],
            status=row["status"],
            current_task_id=row["current_task_id"],
            created_at=row["created_at"],
            last_seen=row["last_seen"],
        )


@dataclass
class Task:
    id: str
    title: str
    description: str | None
    status: str
    owner_agent_id: str | None
    priority: int
    created_at: str
    updated_at: str
    completed_at: str | None

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_row(cls, row: Row) -> Task:
        return cls(
            id=row["id"],
            title=row["title"],
            description=row["description"],
            status=row["status"],
            owner_agent_id=row["owner_agent_id"],
            priority=row["priority"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            completed_at=row["completed_at"],
        )


@dataclass
class Lock:
    file_path: str
    owner_agent_id: str
    reason: str | None
    created_at: str
    expires_at: str
    kind: str = LOCK_FILE

    @property
    def is_resource(self) -> bool:
        return self.kind == LOCK_RESOURCE

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_row(cls, row: Row) -> Lock:
        return cls(
            file_path=row["file_path"],
            owner_agent_id=row["owner_agent_id"],
            reason=row["reason"],
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            kind=row["kind"] if "kind" in row.keys() else LOCK_FILE,
        )


@dataclass
class Message:
    id: str
    sender_agent_id: str
    recipient: str
    body: str
    created_at: str
    read_at: str | None
    reply_to: str | None = None
    acked_at: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_row(cls, row: Row) -> Message:
        keys = row.keys()
        return cls(
            id=row["id"],
            sender_agent_id=row["sender_agent_id"],
            recipient=row["recipient"],
            body=row["body"],
            created_at=row["created_at"],
            read_at=row["read_at"],
            reply_to=row["reply_to"] if "reply_to" in keys else None,
            acked_at=row["acked_at"] if "acked_at" in keys else None,
        )


@dataclass
class Decision:
    id: str
    agent_id: str
    body: str
    created_at: str

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_row(cls, row: Row) -> Decision:
        return cls(
            id=row["id"],
            agent_id=row["agent_id"],
            body=row["body"],
            created_at=row["created_at"],
        )


@dataclass
class Activity:
    id: str
    agent_id: str | None
    event_type: str
    body: str
    tool_name: str | None
    file_path: str | None
    created_at: str

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_row(cls, row: Row) -> Activity:
        return cls(
            id=row["id"],
            agent_id=row["agent_id"],
            event_type=row["event_type"],
            body=row["body"],
            tool_name=row["tool_name"],
            file_path=row["file_path"],
            created_at=row["created_at"],
        )
