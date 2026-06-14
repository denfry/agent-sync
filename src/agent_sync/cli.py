"""Command-line interface for agent-sync.

This module is intentionally thin: it parses arguments, resolves the acting
agent, calls into the domain modules (:mod:`agent_sync.tasks`,
:mod:`agent_sync.locks`, :mod:`agent_sync.messages`) and prints human-readable
output. All persistence and rules live in those modules so the CLI stays easy to
read and the behaviour stays easy to test without a subprocess.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from typing import Sequence

from . import __version__, db, hooks, locks, messages, paths, render, tasks
from .errors import AgentSyncError
from .models import AGENT_ACTIVE


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _open() -> sqlite3.Connection:
    """Open (auto-initialising) the coordination database."""
    return db.connect()


def _acting_agent(
    conn: sqlite3.Connection,
    *,
    name: str | None = None,
    role: str | None = None,
) -> str:
    """Resolve the current agent id, ensure the row exists and heartbeat it."""
    agent_id = db.resolve_agent_id(cwd=os.getcwd())
    db.ensure_agent(
        conn, agent_id, name=name, role=role, cwd=os.getcwd(), status=AGENT_ACTIVE
    )
    return agent_id


# --------------------------------------------------------------------------- #
# Command handlers
# --------------------------------------------------------------------------- #
def cmd_init(args: argparse.Namespace) -> int:
    conn = _open()
    try:
        path = paths.db_path()
        print(f"Initialised coordination database at {path}")
        print(f"Tables: {', '.join(db.TABLE_NAMES)}")
    finally:
        conn.close()
    return 0


def cmd_register(args: argparse.Namespace) -> int:
    conn = _open()
    try:
        agent_id = _acting_agent(conn, name=args.name, role=args.role)
        agent = db.get_agent(conn, agent_id)
        assert agent is not None
        role = f" ({agent.role})" if agent.role else ""
        print(f"Registered agent {agent.name}{role} as `{agent.id}`")
    finally:
        conn.close()
    return 0


def cmd_heartbeat(args: argparse.Namespace) -> int:
    conn = _open()
    try:
        agent_id = db.resolve_agent_id(cwd=os.getcwd())
        agent = db.heartbeat(conn, agent_id)
        print(f"Heartbeat: {agent.name} (`{agent.id}`) last_seen {agent.last_seen}")
    finally:
        conn.close()
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    conn = _open()
    try:
        agent_id = db.resolve_agent_id(cwd=os.getcwd())
        if args.compact:
            sys.stdout.write(render.render_compact(conn, agent_id))
        else:
            sys.stdout.write(render.render_status(conn, agent_id))
    finally:
        conn.close()
    return 0


def cmd_tasks(args: argparse.Namespace) -> int:
    conn = _open()
    try:
        items = tasks.list_tasks(conn)
        if not items:
            print("No tasks.")
            return 0
        for task in items:
            files = tasks.task_files(conn, task.id)
            owner = f" @{task.owner_agent_id}" if task.owner_agent_id else ""
            files_str = f"  files: {', '.join(files)}" if files else ""
            print(f"[{task.status:<11}] {task.id}{owner}  {task.title}{files_str}")
    finally:
        conn.close()
    return 0


def cmd_create_task(args: argparse.Namespace) -> int:
    conn = _open()
    try:
        _acting_agent(conn)
        task = tasks.create_task(
            conn,
            args.title,
            description=args.description,
            files=args.file,
            priority=args.priority,
        )
        print(f"Created task `{task.id}`: {task.title}")
        if args.file:
            print(f"  files: {', '.join(args.file)}")
    finally:
        conn.close()
    return 0


def cmd_claim_task(args: argparse.Namespace) -> int:
    conn = _open()
    try:
        agent_id = _acting_agent(conn)
        task = tasks.claim_task(conn, agent_id, args.task)
        print(f"Claimed task `{task.id}`: {task.title} [{task.status}]")
    finally:
        conn.close()
    return 0


def cmd_complete_task(args: argparse.Namespace) -> int:
    conn = _open()
    try:
        agent_id = _acting_agent(conn)
        task = tasks.complete_task(conn, agent_id, args.task)
        print(f"Completed task `{task.id}`: {task.title}")
    finally:
        conn.close()
    return 0


def cmd_block_task(args: argparse.Namespace) -> int:
    conn = _open()
    try:
        agent_id = _acting_agent(conn)
        task = tasks.block_task(conn, agent_id, args.task, args.reason)
        print(f"Blocked task `{task.id}`: {task.title} — {args.reason}")
    finally:
        conn.close()
    return 0


def cmd_lock(args: argparse.Namespace) -> int:
    conn = _open()
    try:
        agent_id = _acting_agent(conn)
        norm = paths.normalize_repo_path(args.file)
        lock = locks.acquire_lock(
            conn, agent_id, norm, reason=args.reason, ttl_minutes=args.ttl
        )
        print(f"Locked `{lock.file_path}` until {lock.expires_at}")
    finally:
        conn.close()
    return 0


def cmd_unlock(args: argparse.Namespace) -> int:
    conn = _open()
    try:
        agent_id = _acting_agent(conn)
        norm = paths.normalize_repo_path(args.file)
        removed = locks.release_lock(conn, agent_id, norm, force=args.force)
        if removed:
            print(f"Unlocked `{norm}`")
        else:
            print(f"No lock held on `{norm}`")
    finally:
        conn.close()
    return 0


def cmd_locks(args: argparse.Namespace) -> int:
    conn = _open()
    try:
        items = locks.list_locks(conn, include_expired=args.all)
        if not items:
            print("No active locks.")
            return 0
        for lock in items:
            reason = f" — {lock.reason}" if lock.reason else ""
            print(
                f"{lock.file_path}  → {lock.owner_agent_id}  "
                f"(expires {lock.expires_at}){reason}"
            )
    finally:
        conn.close()
    return 0


def cmd_send(args: argparse.Namespace) -> int:
    conn = _open()
    try:
        agent_id = _acting_agent(conn)
        msg = messages.send_message(conn, agent_id, args.to, args.message)
        print(f"Sent `{msg.id}` to {msg.recipient}")
    finally:
        conn.close()
    return 0


def cmd_inbox(args: argparse.Namespace) -> int:
    conn = _open()
    try:
        agent_id = db.resolve_agent_id(cwd=os.getcwd())
        items = messages.inbox(conn, agent_id, unread_only=not args.all)
        if not items:
            print("Inbox empty." if args.all else "No unread messages.")
            return 0
        for msg in items:
            mark = " " if msg.read_at else "*"
            sender = db.get_agent(conn, msg.sender_agent_id)
            sender_name = sender.name if sender else msg.sender_agent_id
            print(
                f"{mark} {msg.id}  {msg.created_at}  "
                f"{sender_name} → {msg.recipient}: {msg.body}"
            )
        if not args.all:
            print("\n(* = unread; use `agent-sync read-message ID` to mark read)")
    finally:
        conn.close()
    return 0


def cmd_read_message(args: argparse.Namespace) -> int:
    conn = _open()
    try:
        agent_id = db.resolve_agent_id(cwd=os.getcwd())
        msg = messages.read_message(conn, agent_id, args.message_id)
        sender = db.get_agent(conn, msg.sender_agent_id)
        sender_name = sender.name if sender else msg.sender_agent_id
        print(f"From: {sender_name} ({msg.sender_agent_id})")
        print(f"To:   {msg.recipient}")
        print(f"At:   {msg.created_at}")
        print("")
        print(msg.body)
    finally:
        conn.close()
    return 0


def cmd_decision(args: argparse.Namespace) -> int:
    conn = _open()
    try:
        agent_id = _acting_agent(conn)
        dec = messages.add_decision(conn, agent_id, args.text)
        print(f"Recorded decision `{dec.id}`")
    finally:
        conn.close()
    return 0


def cmd_log(args: argparse.Namespace) -> int:
    conn = _open()
    try:
        agent_id = _acting_agent(conn)
        file_path = paths.normalize_repo_path(args.file) if args.file else None
        act = messages.log_activity(
            conn,
            agent_id,
            event_type=args.type,
            body=args.message,
            file_path=file_path,
        )
        print(f"Logged `{act.id}` [{act.event_type}]")
    finally:
        conn.close()
    return 0


def cmd_gc(args: argparse.Namespace) -> int:
    conn = _open()
    try:
        agents_changed = db.gc_agents(conn)
        locks_removed = locks.gc_locks(conn)
        print(
            f"GC complete: {agents_changed} agent(s) re-statused, "
            f"{locks_removed} expired lock(s) removed."
        )
    finally:
        conn.close()
    return 0


def cmd_hook(args: argparse.Namespace) -> int:
    return hooks.run_hook(args.event)


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-sync",
        description=(
            "Coordinate multiple Claude Code sessions in one repository: see other "
            "agents, claim tasks, lock files, exchange messages and avoid conflicts."
        ),
    )
    parser.add_argument("--version", action="version", version=f"agent-sync {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    sub.add_parser("init", help="Create the coordination database and tables").set_defaults(
        func=cmd_init
    )

    p_reg = sub.add_parser("register", help="Register or update the current agent")
    p_reg.add_argument("--name", required=True, help="Human-friendly agent name")
    p_reg.add_argument("--role", default=None, help="Agent role, e.g. 'React UI'")
    p_reg.set_defaults(func=cmd_register)

    sub.add_parser("heartbeat", help="Mark the current agent active now").set_defaults(
        func=cmd_heartbeat
    )

    p_status = sub.add_parser("status", help="Show coordination state")
    p_status.add_argument(
        "--compact",
        action="store_true",
        help="Terse Markdown suitable for injecting into Claude context",
    )
    p_status.set_defaults(func=cmd_status)

    sub.add_parser("tasks", help="List all tasks").set_defaults(func=cmd_tasks)

    p_ct = sub.add_parser("create-task", help="Create a task")
    p_ct.add_argument("title", help="Task title")
    p_ct.add_argument("--description", default=None, help="Longer description")
    p_ct.add_argument(
        "--file",
        action="append",
        default=[],
        metavar="PATH",
        help="Associate a file with the task (repeatable)",
    )
    p_ct.add_argument("--priority", type=int, default=0, help="Higher sorts first")
    p_ct.set_defaults(func=cmd_create_task)

    p_claim = sub.add_parser("claim-task", help="Claim a task by id or title")
    p_claim.add_argument("task", metavar="TASK", help="Task id or title")
    p_claim.set_defaults(func=cmd_claim_task)

    p_done = sub.add_parser("complete-task", help="Mark a task done")
    p_done.add_argument("task", metavar="TASK", help="Task id or title")
    p_done.set_defaults(func=cmd_complete_task)

    p_block = sub.add_parser("block-task", help="Mark a task blocked")
    p_block.add_argument("task", metavar="TASK", help="Task id or title")
    p_block.add_argument("--reason", required=True, help="Why it is blocked")
    p_block.set_defaults(func=cmd_block_task)

    p_lock = sub.add_parser("lock", help="Lock a file for editing")
    p_lock.add_argument("file", help="File path to lock")
    p_lock.add_argument("--reason", default=None, help="Why you are locking it")
    p_lock.add_argument(
        "--ttl",
        type=int,
        default=db.DEFAULT_LOCK_TTL_MINUTES,
        help="Lock lifetime in minutes (default: 60)",
    )
    p_lock.set_defaults(func=cmd_lock)

    p_unlock = sub.add_parser("unlock", help="Release a file lock")
    p_unlock.add_argument("file", help="File path to unlock")
    p_unlock.add_argument(
        "--force", action="store_true", help="Release even if you are not the owner"
    )
    p_unlock.set_defaults(func=cmd_unlock)

    p_locks = sub.add_parser("locks", help="List locks")
    p_locks.add_argument(
        "--all", action="store_true", help="Include expired/inactive locks"
    )
    p_locks.set_defaults(func=cmd_locks)

    p_send = sub.add_parser("send", help="Send a message")
    p_send.add_argument(
        "--to", required=True, help="Recipient: agent id, name, role, or 'all'"
    )
    p_send.add_argument("--message", required=True, help="Message body")
    p_send.set_defaults(func=cmd_send)

    p_inbox = sub.add_parser("inbox", help="Show messages addressed to you")
    p_inbox.add_argument(
        "--all", action="store_true", help="Include already-read messages"
    )
    p_inbox.set_defaults(func=cmd_inbox)

    p_read = sub.add_parser("read-message", help="Show a message and mark it read")
    p_read.add_argument("message_id", metavar="MESSAGE_ID", help="Message id")
    p_read.set_defaults(func=cmd_read_message)

    p_dec = sub.add_parser("decision", help="Record a shared decision")
    p_dec.add_argument("text", help="Decision text")
    p_dec.set_defaults(func=cmd_decision)

    p_log = sub.add_parser("log", help="Append an activity log entry")
    p_log.add_argument("--type", default="note", help="Event type, e.g. edit/note")
    p_log.add_argument("--message", required=True, help="Log body")
    p_log.add_argument("--file", default=None, help="Optional related file")
    p_log.set_defaults(func=cmd_log)

    sub.add_parser("gc", help="Re-status stale agents and drop expired locks").set_defaults(
        func=cmd_gc
    )

    p_hook = sub.add_parser("hook", help="Run a Claude Code hook handler")
    p_hook.add_argument(
        "event",
        choices=sorted(hooks.HANDLERS.keys()),
        help="Which hook event to handle (reads JSON from stdin)",
    )
    p_hook.set_defaults(func=cmd_hook)

    return parser


def _configure_streams() -> None:
    """Make stdout/stderr tolerate non-ASCII on legacy consoles (e.g. Windows).

    The renderers use a few box-drawing/arrow characters. On a console whose
    code page can't encode them (cp1251, cp437, …) a bare ``print`` would raise
    ``UnicodeEncodeError`` and, for a hook, crash the edit. Reconfiguring to
    UTF-8 with ``errors='replace'`` keeps output flowing everywhere.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def main(argv: Sequence[str] | None = None) -> int:
    _configure_streams()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    try:
        return args.func(args)
    except AgentSyncError as exc:
        print(f"error: {exc.message}", file=sys.stderr)
        return exc.exit_code
    except BrokenPipeError:  # piping into head/etc.
        return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
