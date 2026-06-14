"""Human- and Claude-readable renderers for coordination state.

``render_status`` is the verbose, sectioned view a human reads in a terminal.
``render_compact`` is a terse Markdown block designed to be injected into a
Claude Code session (via the skill or the ``SessionStart`` hook) — it must stay
small, so it summarizes rather than enumerates.
"""

from __future__ import annotations

import sqlite3

from . import db, git_utils, messages, tasks
from .models import (
    TASK_OPEN_STATUSES,
    Agent,
    Task,
)


def _agent_label(agent: Agent, *, current_id: str | None = None) -> str:
    live = db.effective_status(agent)
    you = " (you)" if agent.id == current_id else ""
    role = f" — {agent.role}" if agent.role else ""
    return f"{agent.name}{you} [{live}]{role}"


def _short(text: str, width: int = 60) -> str:
    text = " ".join(text.split())
    return text if len(text) <= width else text[: width - 1] + "…"


def render_status(conn: sqlite3.Connection, current_agent_id: str) -> str:
    """Full multi-section status report (verbose)."""
    lines: list[str] = []
    moment = db.now()

    lines.append("# agent-sync status")
    git = git_utils.short_status()
    if git:
        lines.append(f"_git: {git}_")
    lines.append("")

    # Current agent ---------------------------------------------------------
    me = db.get_agent(conn, current_agent_id)
    lines.append("## Current agent")
    if me is None:
        lines.append(f"- {current_agent_id} (not registered yet — run `agent-sync register`)")
    else:
        lines.append(f"- {_agent_label(me, current_id=current_agent_id)}")
        lines.append(f"  - id: `{me.id}`")
        if me.current_task_id:
            lines.append(f"  - current task: `{me.current_task_id}`")
        lines.append(f"  - last seen: {me.last_seen}")
    lines.append("")

    # Agents ----------------------------------------------------------------
    agents = db.list_agents(conn)
    active = [a for a in agents if db.effective_status(a, at=moment) == "active"]
    lines.append(f"## Agents ({len(active)} active / {len(agents)} total)")
    if not agents:
        lines.append("- none registered")
    for agent in agents:
        lines.append(f"- {_agent_label(agent, current_id=current_agent_id)}")
    lines.append("")

    # Tasks -----------------------------------------------------------------
    all_tasks = tasks.list_tasks(conn)
    open_tasks = [t for t in all_tasks if t.status in TASK_OPEN_STATUSES]
    lines.append(f"## Tasks ({len(open_tasks)} open / {len(all_tasks)} total)")
    if not all_tasks:
        lines.append("- none")
    for task in all_tasks:
        lines.append(f"- {_task_line(conn, task)}")
    lines.append("")

    # Locks -----------------------------------------------------------------
    from . import locks as locks_mod

    live_locks = locks_mod.list_locks(conn, at=moment)
    lines.append(f"## Locks ({len(live_locks)})")
    if not live_locks:
        lines.append("- none")
    for lock in live_locks:
        owner = db.get_agent(conn, lock.owner_agent_id)
        owner_name = owner.name if owner else lock.owner_agent_id
        reason = f" — {lock.reason}" if lock.reason else ""
        lines.append(
            f"- `{lock.file_path}` → {owner_name} (until {lock.expires_at}){reason}"
        )
    lines.append("")

    # Messages --------------------------------------------------------------
    unread = messages.inbox(conn, current_agent_id, unread_only=True)
    lines.append(f"## Unread messages ({len(unread)})")
    if not unread:
        lines.append("- none")
    for msg in unread:
        sender = db.get_agent(conn, msg.sender_agent_id)
        sender_name = sender.name if sender else msg.sender_agent_id
        lines.append(
            f"- `{msg.id}` from {sender_name} → {msg.recipient}: {_short(msg.body)}"
        )
    lines.append("")

    # Activity --------------------------------------------------------------
    recent = messages.recent_activity(conn, limit=10)
    lines.append("## Recent activity")
    if not recent:
        lines.append("- none")
    for act in recent:
        who = db.get_agent(conn, act.agent_id) if act.agent_id else None
        who_name = who.name if who else (act.agent_id or "system")
        detail = f" `{act.file_path}`" if act.file_path else ""
        lines.append(f"- {act.created_at} {who_name} {act.event_type}:{detail} {_short(act.body)}")

    return "\n".join(lines).rstrip() + "\n"


def _task_line(conn: sqlite3.Connection, task: Task) -> str:
    owner = db.get_agent(conn, task.owner_agent_id) if task.owner_agent_id else None
    owner_name = f" @{owner.name}" if owner else (f" @{task.owner_agent_id}" if task.owner_agent_id else "")
    files = tasks.task_files(conn, task.id)
    files_str = f" [{', '.join(files)}]" if files else ""
    return f"`{task.id}` [{task.status}]{owner_name} {task.title}{files_str}"


def render_compact(conn: sqlite3.Connection, current_agent_id: str) -> str:
    """Terse Markdown summary for injection into a Claude Code session.

    Designed to be cheap to read: counts plus only the items another agent must
    not collide with (live locks, in-progress/blocked tasks, broadcasts).
    """
    moment = db.now()
    agents = db.list_agents(conn)
    active = [a for a in agents if db.effective_status(a, at=moment) == "active"]
    me = db.get_agent(conn, current_agent_id)

    from . import locks as locks_mod

    live_locks = locks_mod.list_locks(conn, at=moment)
    all_tasks = tasks.list_tasks(conn)
    open_tasks = [t for t in all_tasks if t.status in TASK_OPEN_STATUSES]
    unread = messages.inbox(conn, current_agent_id, unread_only=True)

    lines: list[str] = ["## agent-sync"]
    me_name = me.name if me else current_agent_id
    lines.append(
        f"you: **{me_name}** | active agents: {len(active)} | "
        f"open tasks: {len(open_tasks)} | locks: {len(live_locks)} | "
        f"unread: {len(unread)}"
    )

    others = [a for a in active if a.id != current_agent_id]
    if others:
        names = ", ".join(
            f"{a.name}{f' ({a.role})' if a.role else ''}" for a in others
        )
        lines.append(f"- other active: {names}")

    if live_locks:
        locked = ", ".join(
            f"`{lk.file_path}`→{(_owner := db.get_agent(conn, lk.owner_agent_id)) and _owner.name or lk.owner_agent_id}"
            for lk in live_locks
        )
        lines.append(f"- locked files: {locked}")

    in_prog = [t for t in open_tasks if t.status == "in_progress"]
    if in_prog:
        items = "; ".join(f"{_short(t.title, 40)} (@{t.owner_agent_id})" for t in in_prog)
        lines.append(f"- in progress: {items}")

    available = [t for t in open_tasks if t.status == "pending"]
    if available:
        titles = "; ".join(_short(t.title, 40) for t in available[:5])
        more = f" (+{len(available) - 5} more)" if len(available) > 5 else ""
        lines.append(
            f"- available to claim: {titles}{more} — run `agent-sync claim-next`"
        )

    if unread:
        lines.append(f"- you have {len(unread)} unread message(s): run `agent-sync inbox`")

    if not others and not live_locks and not in_prog and not available:
        lines.append("- no other active agents, locks, or tasks to pick up")

    return "\n".join(lines).rstrip() + "\n"
