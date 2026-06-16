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
from collections.abc import Sequence
from pathlib import Path

from . import __version__, db, hooks, locks, messages, paths, render, tasks
from .errors import AgentSyncError, UsageError
from .models import AGENT_ACTIVE, LOCK_FILE, LOCK_RESOURCE

# Default seconds to block when ``--wait`` is given with no explicit value.
DEFAULT_WAIT_SECONDS = 30


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


def _resolve_lock_target(args: argparse.Namespace) -> tuple[str, str]:
    """Return ``(key, kind)`` for a lock/unlock command.

    A ``--resource`` key is stored verbatim (an arbitrary named lock); a file
    positional is normalized to the canonical repo-relative form the PreToolUse
    hook checks. Exactly one must be supplied.
    """
    resource = getattr(args, "resource", None)
    if resource:
        if getattr(args, "file", None):
            raise UsageError("give a FILE path or --resource KEY, not both")
        return resource, LOCK_RESOURCE
    if getattr(args, "file", None):
        return paths.normalize_repo_path(args.file), LOCK_FILE
    raise UsageError("provide a FILE path or --resource KEY")


def _add_wait_arg(parser: argparse.ArgumentParser) -> None:
    """Add a shared ``--wait[=SECONDS]`` flag (blocks until the lock frees)."""
    parser.add_argument(
        "--wait",
        nargs="?",
        type=int,
        const=DEFAULT_WAIT_SECONDS,
        default=None,
        metavar="SECONDS",
        help=(
            "Block until the lock is free instead of failing immediately "
            f"(bare --wait waits {DEFAULT_WAIT_SECONDS}s; --wait=N waits N seconds)"
        ),
    )


def _report_auto_lock(conn: sqlite3.Connection, agent_id: str, task_id: str) -> None:
    """Lock a claimed task's files best-effort and print what happened."""
    locked, conflicts = tasks.lock_task_files(conn, agent_id, task_id)
    if locked:
        print(f"  locked: {', '.join(locked)}")
    for message in conflicts:
        print(f"  WARNING: could not lock — {message}", file=sys.stderr)


def _append_to_file(target: Path, content: str) -> None:
    """Append *content* to *target*, creating parent directories as needed."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "a", encoding="utf-8") as handle:
        handle.write(content)


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


def cmd_whoami(args: argparse.Namespace) -> int:
    conn = _open()
    try:
        agent_id = db.resolve_agent_id(cwd=os.getcwd())
        source = db.identity_source()
        agent = db.get_agent(conn, agent_id)
        status = db.effective_status(agent) if agent else None
        if args.json:
            sys.stdout.write(
                render.render_json(
                    {
                        "id": agent_id,
                        "resolved_via": source,
                        "registered": agent is not None,
                        "name": agent.name if agent else None,
                        "role": agent.role if agent else None,
                        "status": status,
                    }
                )
            )
            return 0
        print(f"id:           {agent_id}")
        print(f"resolved via: {source}")
        if agent is not None:
            role = f" ({agent.role})" if agent.role else ""
            print(f"name:         {agent.name}{role}")
            print(f"status:       {status}")
        else:
            print("not registered yet — run `agent-sync register --name ...`")
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
        if args.json:
            sys.stdout.write(
                render.render_json(render.status_payload(conn, agent_id))
            )
        elif args.compact:
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
        if args.json:
            sys.stdout.write(
                render.render_json([render.task_payload(conn, t) for t in items])
            )
            return 0
        if not items:
            print("No tasks.")
            return 0
        for task in items:
            files = tasks.task_files(conn, task.id)
            owner = f" @{task.owner_agent_id}" if task.owner_agent_id else ""
            files_str = f"  files: {', '.join(files)}" if files else ""
            blocked = (
                "  [blocked: deps]"
                if tasks.unmet_dependencies(conn, task.id)
                else ""
            )
            print(
                f"[{task.status:<11}] {task.id}{owner}  {task.title}"
                f"{files_str}{blocked}"
            )
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
            depends_on=args.depends_on,
        )
        print(f"Created task `{task.id}`: {task.title}")
        if args.file:
            print(f"  files: {', '.join(args.file)}")
        if args.depends_on:
            deps = tasks.task_dependencies(conn, task.id)
            print(f"  depends on: {', '.join(deps)}")
    finally:
        conn.close()
    return 0


def cmd_claim_task(args: argparse.Namespace) -> int:
    conn = _open()
    try:
        agent_id = _acting_agent(conn)
        task = tasks.claim_task(conn, agent_id, args.task, force=args.force)
        print(f"Claimed task `{task.id}`: {task.title} [{task.status}]")
        if args.lock:
            _report_auto_lock(conn, agent_id, task.id)
    finally:
        conn.close()
    return 0


def cmd_claim_next(args: argparse.Namespace) -> int:
    conn = _open()
    try:
        agent_id = _acting_agent(conn)
        task = tasks.claim_next_task(conn, agent_id)
        if task is None:
            print("No available tasks to claim.")
            return 0
        files = tasks.task_files(conn, task.id)
        files_str = f"  files: {', '.join(files)}" if files else ""
        print(f"Claimed task `{task.id}`: {task.title} [{task.status}]{files_str}")
        if args.lock:
            _report_auto_lock(conn, agent_id, task.id)
    finally:
        conn.close()
    return 0


def cmd_complete_task(args: argparse.Namespace) -> int:
    conn = _open()
    try:
        agent_id = _acting_agent(conn)
        task = tasks.complete_task(conn, agent_id, args.task)
        print(f"Completed task `{task.id}`: {task.title}")
        unblocked = tasks.dependents_unblocked_by(conn, task.id)
        if unblocked:
            names = ", ".join(f"`{t.id}` {t.title}" for t in unblocked)
            print(f"  now claimable (dependencies satisfied): {names}")
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
        key, kind = _resolve_lock_target(args)
        if args.wait is not None:
            lock = locks.acquire_lock_blocking(
                conn,
                agent_id,
                key,
                reason=args.reason,
                ttl_minutes=args.ttl,
                kind=kind,
                wait_seconds=args.wait,
            )
        else:
            lock = locks.acquire_lock(
                conn, agent_id, key, reason=args.reason, ttl_minutes=args.ttl, kind=kind
            )
        label = "resource" if kind == LOCK_RESOURCE else "file"
        print(f"Locked {label} `{lock.file_path}` until {lock.expires_at}")
    finally:
        conn.close()
    return 0


def cmd_unlock(args: argparse.Namespace) -> int:
    conn = _open()
    try:
        agent_id = _acting_agent(conn)
        key, _kind = _resolve_lock_target(args)
        removed = locks.release_lock(conn, agent_id, key, force=args.force)
        if removed:
            print(f"Unlocked `{key}`")
        else:
            print(f"No lock held on `{key}`")
    finally:
        conn.close()
    return 0


def cmd_append(args: argparse.Namespace) -> int:
    conn = _open()
    try:
        agent_id = _acting_agent(conn)
        norm = paths.normalize_repo_path(args.file)
        raw = Path(args.file)
        target = raw if raw.is_absolute() else paths.repo_root() / raw
        content = args.content if args.content is not None else sys.stdin.read()
        if not args.no_newline and content and not content.endswith("\n"):
            content += "\n"
        reason = args.reason or "append"
        if args.wait is not None:
            locks.acquire_lock_blocking(
                conn,
                agent_id,
                norm,
                reason=reason,
                ttl_minutes=args.ttl,
                wait_seconds=args.wait,
            )
        else:
            locks.acquire_lock(conn, agent_id, norm, reason=reason, ttl_minutes=args.ttl)
        try:
            _append_to_file(target, content)
        finally:
            locks.release_lock(conn, agent_id, norm)
        print(f"Appended {len(content)} char(s) to `{norm}`")
    finally:
        conn.close()
    return 0


def cmd_locks(args: argparse.Namespace) -> int:
    conn = _open()
    try:
        items = locks.list_locks(conn, include_expired=args.all)
        if args.json:
            sys.stdout.write(
                render.render_json([render.lock_payload(conn, lk) for lk in items])
            )
            return 0
        if not items:
            print("No active locks.")
            return 0
        for lock in items:
            reason = f" — {lock.reason}" if lock.reason else ""
            kind = f" [{lock.kind}]" if lock.is_resource else ""
            print(
                f"{lock.file_path}{kind}  → {lock.owner_agent_id}  "
                f"(expires {lock.expires_at}){reason}"
            )
    finally:
        conn.close()
    return 0


def cmd_send(args: argparse.Namespace) -> int:
    conn = _open()
    try:
        agent_id = _acting_agent(conn)
        msg = messages.send_message(
            conn, agent_id, args.to, args.message, reply_to=args.reply_to
        )
        print(f"Sent `{msg.id}` to {msg.recipient}")
    finally:
        conn.close()
    return 0


def cmd_ack(args: argparse.Namespace) -> int:
    conn = _open()
    try:
        agent_id = _acting_agent(conn)
        msg = messages.ack_message(conn, agent_id, args.message_id)
        print(f"Acked `{msg.id}` (acked_at {msg.acked_at})")
    finally:
        conn.close()
    return 0


def cmd_inbox(args: argparse.Namespace) -> int:
    conn = _open()
    try:
        agent_id = db.resolve_agent_id(cwd=os.getcwd())
        items = messages.inbox(conn, agent_id, unread_only=not args.all)
        if args.json:
            sys.stdout.write(
                render.render_json([render.message_payload(conn, m) for m in items])
            )
            return 0
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
        body = args.message or args.message_opt
        if not body:
            raise UsageError(
                "log requires a message, e.g. `agent-sync log \"did X\"`"
            )
        file_path = paths.normalize_repo_path(args.file) if args.file else None
        act = messages.log_activity(
            conn,
            agent_id,
            event_type=args.type,
            body=body,
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


def cmd_console(args: argparse.Namespace) -> int:
    # Imported lazily so the optional TUI dependency is only needed by this one
    # command; the rest of the CLI stays standard-library-only.
    from . import console

    return console.run(interval=args.interval, name=args.name)


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

    p_whoami = sub.add_parser(
        "whoami", help="Show your resolved agent id and how it was determined"
    )
    p_whoami.add_argument(
        "--json", action="store_true", help="Machine-readable JSON output"
    )
    p_whoami.set_defaults(func=cmd_whoami)

    sub.add_parser("heartbeat", help="Mark the current agent active now").set_defaults(
        func=cmd_heartbeat
    )

    p_status = sub.add_parser("status", help="Show coordination state")
    p_status.add_argument(
        "--compact",
        action="store_true",
        help="Terse Markdown suitable for injecting into Claude context",
    )
    p_status.add_argument(
        "--json",
        action="store_true",
        help="Machine-readable JSON for programmatic coordination decisions",
    )
    p_status.set_defaults(func=cmd_status)

    p_tasks = sub.add_parser("tasks", help="List all tasks")
    p_tasks.add_argument(
        "--json", action="store_true", help="Machine-readable JSON output"
    )
    p_tasks.set_defaults(func=cmd_tasks)

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
    p_ct.add_argument(
        "--depends-on",
        action="append",
        default=[],
        metavar="TASK",
        dest="depends_on",
        help="Task id/title this task depends on; blocks claiming until done "
        "(repeatable)",
    )
    p_ct.set_defaults(func=cmd_create_task)

    p_claim = sub.add_parser("claim-task", help="Claim a task by id or title")
    p_claim.add_argument("task", metavar="TASK", help="Task id or title")
    p_claim.add_argument(
        "--lock",
        action="store_true",
        help="Also lock the task's associated files for you",
    )
    p_claim.add_argument(
        "--force",
        action="store_true",
        help="Claim even if the task has unfinished dependencies",
    )
    p_claim.set_defaults(func=cmd_claim_task)

    p_claim_next = sub.add_parser(
        "claim-next",
        help="Claim the next available task automatically (highest priority first)",
    )
    p_claim_next.add_argument(
        "--lock",
        action="store_true",
        help="Also lock the claimed task's associated files for you",
    )
    p_claim_next.set_defaults(func=cmd_claim_next)

    p_done = sub.add_parser("complete-task", help="Mark a task done")
    p_done.add_argument("task", metavar="TASK", help="Task id or title")
    p_done.set_defaults(func=cmd_complete_task)

    p_block = sub.add_parser("block-task", help="Mark a task blocked")
    p_block.add_argument("task", metavar="TASK", help="Task id or title")
    p_block.add_argument("--reason", required=True, help="Why it is blocked")
    p_block.set_defaults(func=cmd_block_task)

    p_lock = sub.add_parser("lock", help="Lock a file (or named resource) for editing")
    p_lock.add_argument("file", nargs="?", help="File path to lock")
    p_lock.add_argument(
        "--resource",
        default=None,
        metavar="KEY",
        help="Lock an arbitrary named resource (e.g. db-migrations) instead of a file",
    )
    p_lock.add_argument("--reason", default=None, help="Why you are locking it")
    p_lock.add_argument(
        "--ttl",
        type=int,
        default=db.DEFAULT_LOCK_TTL_MINUTES,
        help="Lock lifetime in minutes (default: 60)",
    )
    _add_wait_arg(p_lock)
    p_lock.set_defaults(func=cmd_lock)

    p_unlock = sub.add_parser("unlock", help="Release a file (or named resource) lock")
    p_unlock.add_argument("file", nargs="?", help="File path to unlock")
    p_unlock.add_argument(
        "--resource", default=None, metavar="KEY", help="Named resource to unlock"
    )
    p_unlock.add_argument(
        "--force", action="store_true", help="Release even if you are not the owner"
    )
    p_unlock.set_defaults(func=cmd_unlock)

    p_append = sub.add_parser(
        "append",
        help="Atomically append to a shared file under a lock (lock→append→unlock)",
    )
    p_append.add_argument("file", help="File to append to (repo-relative or absolute)")
    p_append.add_argument(
        "--content",
        default=None,
        help="Text to append (default: read from stdin)",
    )
    p_append.add_argument(
        "--no-newline",
        action="store_true",
        help="Do not ensure the appended text ends with a newline",
    )
    p_append.add_argument("--reason", default=None, help="Lock reason")
    p_append.add_argument(
        "--ttl",
        type=int,
        default=db.DEFAULT_LOCK_TTL_MINUTES,
        help="Lock lifetime in minutes while appending (default: 60)",
    )
    _add_wait_arg(p_append)
    p_append.set_defaults(func=cmd_append)

    p_locks = sub.add_parser("locks", help="List locks")
    p_locks.add_argument(
        "--all", action="store_true", help="Include expired/inactive locks"
    )
    p_locks.add_argument(
        "--json", action="store_true", help="Machine-readable JSON output"
    )
    p_locks.set_defaults(func=cmd_locks)

    p_send = sub.add_parser("send", help="Send a message")
    p_send.add_argument(
        "--to", required=True, help="Recipient: agent id, name, role, or 'all'"
    )
    p_send.add_argument("--message", required=True, help="Message body")
    p_send.add_argument(
        "--reply-to",
        default=None,
        metavar="MESSAGE_ID",
        dest="reply_to",
        help="Thread this message as a reply to an existing message id",
    )
    p_send.set_defaults(func=cmd_send)

    p_ack = sub.add_parser(
        "ack", help="Acknowledge a message so its sender knows it was handled"
    )
    p_ack.add_argument("message_id", metavar="MESSAGE_ID", help="Message id")
    p_ack.set_defaults(func=cmd_ack)

    p_inbox = sub.add_parser("inbox", help="Show messages addressed to you")
    p_inbox.add_argument(
        "--all", action="store_true", help="Include already-read messages"
    )
    p_inbox.add_argument(
        "--json", action="store_true", help="Machine-readable JSON output"
    )
    p_inbox.set_defaults(func=cmd_inbox)

    p_read = sub.add_parser("read-message", help="Show a message and mark it read")
    p_read.add_argument("message_id", metavar="MESSAGE_ID", help="Message id")
    p_read.set_defaults(func=cmd_read_message)

    p_dec = sub.add_parser("decision", help="Record a shared decision")
    p_dec.add_argument("text", help="Decision text")
    p_dec.set_defaults(func=cmd_decision)

    p_log = sub.add_parser("log", help="Append an activity log entry")
    p_log.add_argument("message", nargs="?", default=None, help="Log body")
    p_log.add_argument(
        "--message",
        dest="message_opt",
        default=None,
        help=argparse.SUPPRESS,  # deprecated alias for the positional message
    )
    p_log.add_argument("--type", default="note", help="Event type, e.g. edit/note")
    p_log.add_argument("--file", default=None, help="Optional related file")
    p_log.set_defaults(func=cmd_log)

    sub.add_parser("gc", help="Re-status stale agents and drop expired locks").set_defaults(
        func=cmd_gc
    )

    p_console = sub.add_parser(
        "console",
        help="Live console: watch agents in real time and steer them (needs the 'tui' extra)",
    )
    p_console.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Seconds between refreshes (default: 1.0)",
    )
    p_console.add_argument(
        "--name",
        default=None,
        help="Display name for you, the operator (default: 'operator')",
    )
    p_console.set_defaults(func=cmd_console)

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
