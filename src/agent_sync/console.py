"""Live operator console: watch coordination state and steer it in real time.

``agent-sync console`` opens a streaming view of everything happening in the
repo's coordination database — agents joining and leaving, edits, messages
between agents, locks taken and released — and gives a human a command line to
*act* as a first-class participant: broadcast or direct messages (which the push
hooks deliver into live agent sessions), lock files to immediately stop edits,
steer the task board, and record decisions.

The module is split so the interesting logic stays testable without the optional
TUI dependency:

* Pure helpers — :func:`sanitize_terminal`, :func:`parse_command`,
  :func:`poll_events`, :func:`execute_command`, :func:`format_status` — import
  nothing beyond the standard library and the domain modules.
* :func:`run` is the only part that touches ``prompt_toolkit``; it imports it
  lazily so the rest of the CLI never pays for (or requires) the extra.

Identity: the console acts as the reserved :data:`~agent_sync.models.OPERATOR_ID`
agent. It registers as active so its locks are enforced and its messages are
pushed like any other, but the renderers keep it out of the agent counts other
sessions key off (see :mod:`agent_sync.render`).
"""

from __future__ import annotations

import re
import sqlite3
import sys
import threading
from dataclasses import dataclass, field

from . import db, locks, messages, paths, tasks
from .errors import AgentSyncError
from .models import (
    AGENT_ACTIVE,
    AGENT_IDLE,
    OPERATOR_ID,
    OPERATOR_ROLE,
    Agent,
)

# How many recent rows to scan per poll when detecting new activity/messages.
FEED_WINDOW = 100
# Clamp the refresh interval so a typo can't busy-spin the database.
MIN_INTERVAL = 0.25
DEFAULT_INTERVAL = 1.0

# Control characters (C0 except we handle whitespace ourselves, plus DEL and the
# C1 range) that have no place in a value printed to a human's terminal. ESC
# (0x1b) lives in this range, so this also defangs ANSI escape injection from a
# value another agent authored.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


def sanitize_terminal(text: str | None) -> str:
    """Neutralize an untrusted value before printing it to the operator's terminal.

    Agent names, message bodies, task titles and lock reasons are authored by
    other agents (or a hostile one). Printed raw they could smuggle ANSI escape
    sequences to move the cursor, recolor, or clear the screen. This collapses
    newlines/tabs to spaces (keeping each event on one line) and strips every
    control byte, including ESC. ``None`` renders as the empty string.
    """
    if not text:
        return ""
    flat = str(text).replace("\t", " ").replace("\r", " ").replace("\n", " ")
    return _CONTROL.sub("", flat)


# --------------------------------------------------------------------------- #
# Live feed
# --------------------------------------------------------------------------- #
@dataclass
class Event:
    """One thing that happened, ready to be formatted into a feed line."""

    ts: str  # ISO-8601 timestamp (sorts lexicographically)
    source: str  # "activity" | "message" | "presence" | "lock"
    actor: str  # resolved agent name/id (raw; sanitized at format time)
    text: str  # detail (raw; sanitized at format time)


@dataclass
class ConsoleState:
    """Cursors the poller carries between ticks to emit only what is new.

    ``primed`` starts ``False`` so the first :func:`poll_events` call seeds the
    cursors silently instead of dumping the whole history as "new".
    """

    seen_activity: set[str] = field(default_factory=set)
    seen_messages: set[str] = field(default_factory=set)
    presence: dict[str, str] = field(default_factory=dict)
    locks: dict[str, str] = field(default_factory=dict)
    primed: bool = False


_TAGS = {"activity": "act ", "message": "msg ", "presence": "who ", "lock": "lock"}


def _clock(ts: str) -> str:
    """Pull ``HH:MM:SS`` out of an ISO-8601 timestamp, tolerating odd input."""
    return ts[11:19] if len(ts) >= 19 and ts[10:11] == "T" else ts[:8]


def format_event(event: Event) -> str:
    """Render one :class:`Event` as a single sanitized feed line."""
    tag = _TAGS.get(event.source, "    ")
    actor = sanitize_terminal(event.actor)[:12].ljust(12)
    return f"{_clock(event.ts)} {tag} {actor} {sanitize_terminal(event.text)}"


def _resolve_name(conn: sqlite3.Connection, agent_id: str | None) -> str:
    if not agent_id:
        return "system"
    if agent_id == OPERATOR_ID:
        return "operator"
    agent = db.get_agent(conn, agent_id)
    return agent.name if agent else agent_id


def poll_events(conn: sqlite3.Connection, state: ConsoleState) -> list[Event]:
    """Return new events since the last poll, advancing *state* in place.

    Activity and messages are detected by id against a bounded recent window;
    presence and lock changes are detected by diffing the current snapshot
    against the previous one. The first call (``state.primed is False``) returns
    nothing — it only records where we are starting from.
    """
    moment = db.now()
    events: list[Event] = []

    # New activity rows (bounded window keeps the seen-set from growing forever:
    # a row older than the window can't reappear, so we only remember the window).
    act_window = list(reversed(messages.recent_activity(conn, limit=FEED_WINDOW)))
    fresh_acts = [a for a in act_window if a.id not in state.seen_activity]
    state.seen_activity = {a.id for a in act_window}
    if state.primed:
        for act in fresh_acts:
            detail = act.body or act.event_type
            events.append(Event(act.created_at, "activity", _resolve_name(conn, act.agent_id), detail))

    # New messages (the whole conversation, not one inbox).
    msg_window = list(reversed(messages.recent_messages(conn, limit=FEED_WINDOW)))
    fresh_msgs = [m for m in msg_window if m.id not in state.seen_messages]
    state.seen_messages = {m.id for m in msg_window}
    if state.primed:
        for msg in fresh_msgs:
            sender = _resolve_name(conn, msg.sender_agent_id)
            events.append(Event(msg.created_at, "message", sender, f"→{msg.recipient}: {msg.body}"))

    # Presence transitions (joined / status changed).
    current_presence = {
        a.id: db.effective_status(a, at=moment)
        for a in db.list_agents(conn)
        if a.id != OPERATOR_ID
    }
    if state.primed:
        for agent_id, status in current_presence.items():
            prev = state.presence.get(agent_id)
            if prev == status:
                continue
            who = _resolve_name(conn, agent_id)
            text = f"joined [{status}]" if prev is None else f"{prev} → {status}"
            events.append(Event(moment.isoformat(), "presence", who, text))
    state.presence = current_presence

    # Lock changes (taken / released).
    current_locks = {lk.file_path: lk.owner_agent_id for lk in locks.list_locks(conn, at=moment)}
    if state.primed:
        for path, owner in current_locks.items():
            if path not in state.locks:
                events.append(Event(moment.isoformat(), "lock", _resolve_name(conn, owner), f"locked {path}"))
        for path, owner in state.locks.items():
            if path not in current_locks:
                events.append(Event(moment.isoformat(), "lock", _resolve_name(conn, owner), f"released {path}"))
    state.locks = current_locks

    state.primed = True
    events.sort(key=lambda e: e.ts)
    return events


# --------------------------------------------------------------------------- #
# Snapshot
# --------------------------------------------------------------------------- #
def format_status(conn: sqlite3.Connection, operator_id: str = OPERATOR_ID) -> str:
    """A compact, sanitized point-in-time snapshot for the terminal."""
    moment = db.now()
    agents = [a for a in db.list_agents(conn) if a.id != operator_id]
    active = [a for a in agents if db.effective_status(a, at=moment) == AGENT_ACTIVE]
    lines = [f"agents ({len(active)} active / {len(agents)} total):"]
    if agents:
        for a in agents:
            role = f" {sanitize_terminal(a.role)}" if a.role else ""
            lines.append(f"  {sanitize_terminal(a.name)} [{db.effective_status(a, at=moment)}]{role}")
    else:
        lines.append("  (none)")

    all_tasks = tasks.list_tasks(conn)
    open_tasks = [t for t in all_tasks if t.status in ("pending", "in_progress", "blocked")]
    lines.append(f"tasks ({len(open_tasks)} open / {len(all_tasks)} total):")
    for t in all_tasks[:10]:
        owner = f" @{_resolve_name(conn, t.owner_agent_id)}" if t.owner_agent_id else ""
        lines.append(f"  [{t.status}]{owner} {sanitize_terminal(t.title)}")

    live = locks.list_locks(conn, at=moment)
    lines.append(f"locks ({len(live)}):")
    for lk in live:
        reason = f" — {sanitize_terminal(lk.reason)}" if lk.reason else ""
        lines.append(f"  {sanitize_terminal(lk.file_path)} → {_resolve_name(conn, lk.owner_agent_id)}{reason}")

    recent = messages.recent_messages(conn, limit=5)
    if recent:
        lines.append("recent messages:")
        for m in reversed(recent):
            sender = _resolve_name(conn, m.sender_agent_id)
            lines.append(f"  {sanitize_terminal(sender)} →{sanitize_terminal(m.recipient)}: {sanitize_terminal(m.body)}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
HELP = """\
Commands (you act as the human 'operator'):
  send <to> <message>        message an id/name/role/all (directed = pushed forcefully)
  directive <to> <message>   like send, but 'all' fans out a directed copy to every
                             active agent so each is forced to react this turn
  lock <path> [reason]       lock a file — blocks other agents' edits immediately
  unlock <path>              release a lock (operator can break anyone's)
  task new <title>           create a task
  task done <id|title>       mark a task done
  task block <id|title> :: <reason>   block a task
  decision <text>            record a shared decision
  status | agents | locks | tasks | msgs   print a snapshot
  help                       show this help
  quit | exit                leave (your locks stop enforcing once you go idle)
"""

QUIT_COMMANDS = {"quit", "exit", "q"}


def parse_command(line: str) -> tuple[str, str]:
    """Split a console line into ``(command, rest)``.

    Only the command word is tokenized; the remainder is returned verbatim so
    per-command parsing can keep file paths (including Windows backslashes) and
    free-text messages intact without shell-quoting rules getting in the way.
    """
    stripped = line.strip()
    if not stripped:
        return "", ""
    head, _, rest = stripped.partition(" ")
    return head.lower(), rest.strip()


def _active_peers(conn: sqlite3.Connection, operator_id: str) -> list[Agent]:
    moment = db.now()
    return [
        a
        for a in db.list_agents(conn)
        if a.id != operator_id and db.effective_status(a, at=moment) == AGENT_ACTIVE
    ]


def execute_command(
    conn: sqlite3.Connection, operator_id: str, command: str, rest: str
) -> str:
    """Run one operator command against the coordination DB, returning a summary.

    Raises :class:`AgentSyncError` for domain failures (e.g. a lock conflict) so
    the caller can show a clean message; unknown commands return a hint rather
    than raising.
    """
    if command in ("help", "?"):
        return HELP
    if command in ("status",):
        return format_status(conn, operator_id)
    if command in ("agents", "locks", "tasks", "task") and not rest:
        return format_status(conn, operator_id)
    if command in ("msgs", "messages"):
        recent = messages.recent_messages(conn, limit=15)
        if not recent:
            return "no messages yet"
        return "\n".join(
            f"{sanitize_terminal(_resolve_name(conn, m.sender_agent_id))} "
            f"→{sanitize_terminal(m.recipient)}: {sanitize_terminal(m.body)}"
            for m in reversed(recent)
        )

    if command in ("send", "msg", "directive"):
        to, _, body = rest.partition(" ")
        to, body = to.strip(), body.strip()
        if not to or not body:
            return f"usage: {command} <to> <message>"
        if command == "directive" and to == "all":
            peers = _active_peers(conn, operator_id)
            if not peers:
                return "no active agents to direct"
            for peer in peers:
                messages.send_message(conn, operator_id, peer.id, body)
            return f"directive -> {len(peers)} active agent(s)"
        messages.send_message(conn, operator_id, to, body)
        return f"sent -> {to}"

    if command == "lock":
        path, _, reason = rest.partition(" ")
        if not path.strip():
            return "usage: lock <path> [reason]"
        norm = paths.normalize_repo_path(path.strip())
        lock = locks.acquire_lock(
            conn, operator_id, norm, reason=reason.strip() or None
        )
        return f"locked {lock.file_path} (until {lock.expires_at})"

    if command == "unlock":
        if not rest:
            return "usage: unlock <path>"
        norm = paths.normalize_repo_path(rest)
        removed = locks.release_lock(conn, operator_id, norm, force=True)
        return f"unlocked {norm}" if removed else f"no lock on {norm}"

    if command in ("task", "tasks"):
        sub, _, tail = rest.partition(" ")
        sub, tail = sub.lower(), tail.strip()
        if sub == "new":
            if not tail:
                return "usage: task new <title>"
            task = tasks.create_task(conn, tail)
            return f"created {task.id}: {sanitize_terminal(task.title)}"
        if sub == "done":
            if not tail:
                return "usage: task done <id|title>"
            task = tasks.complete_task(conn, operator_id, tail)
            return f"completed {task.id}: {sanitize_terminal(task.title)}"
        if sub == "block":
            ref, sep, reason = tail.partition("::")
            if not sep or not ref.strip() or not reason.strip():
                return "usage: task block <id|title> :: <reason>"
            task = tasks.block_task(conn, operator_id, ref.strip(), reason.strip())
            return f"blocked {task.id}"
        return "usage: task new|done|block ..."

    if command == "decision":
        if not rest:
            return "usage: decision <text>"
        dec = messages.add_decision(conn, operator_id, rest)
        return f"recorded decision {dec.id}"

    return f"unknown command: {command!r} (try 'help')"


# --------------------------------------------------------------------------- #
# Identity
# --------------------------------------------------------------------------- #
def ensure_operator(conn: sqlite3.Connection, name: str | None = None) -> str:
    """Register/heartbeat the reserved operator agent and return its id."""
    db.ensure_agent(
        conn,
        OPERATOR_ID,
        name=name or "operator",
        role=OPERATOR_ROLE,
        status=AGENT_ACTIVE,
    )
    return OPERATOR_ID


# --------------------------------------------------------------------------- #
# Interactive loop (the only part that needs the optional TUI extra)
# --------------------------------------------------------------------------- #
def run(*, interval: float = DEFAULT_INTERVAL, name: str | None = None) -> int:
    """Open the interactive console. Returns a process exit code."""
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.patch_stdout import patch_stdout
    except ImportError:
        sys.stderr.write(
            "[agent-sync] the live console needs the optional TUI extra.\n"
            'Install it with:  pip install "claude-agent-sync[tui]"\n'
        )
        return 1

    interval = max(MIN_INTERVAL, float(interval))

    # The console is a full-terminal, interactive experience. Without a TTY
    # (piped, redirected, or a CI runner) prompt_toolkit can't drive the screen
    # and would raise deep in its internals — fail with a clear message instead,
    # and before we create any operator/DB state.
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        sys.stderr.write(
            "[agent-sync] the live console needs an interactive terminal (a TTY).\n"
            "Run it directly in your shell — not piped, redirected, or under CI.\n"
        )
        return 1

    conn = db.connect()
    operator_id = ensure_operator(conn, name)

    print("agent-sync console — live coordination view. Type 'help', 'quit' to leave.")
    print(format_status(conn, operator_id))
    print("-" * 60)

    stop = threading.Event()

    def _poll_loop() -> None:
        # A separate connection: SQLite connections are not shared across threads.
        poll_conn = db.connect()
        state = ConsoleState()
        poll_events(poll_conn, state)  # prime silently — don't replay history
        while not stop.wait(interval):
            try:
                db.heartbeat(poll_conn, operator_id)
                for event in poll_events(poll_conn, state):
                    print(format_event(event))
            except Exception:
                pass  # a transient DB hiccup must never kill the feed
        poll_conn.close()

    poller = threading.Thread(target=_poll_loop, daemon=True)
    session: PromptSession = PromptSession()

    with patch_stdout():
        poller.start()
        while True:
            try:
                line = session.prompt("operator> ")
            except (EOFError, KeyboardInterrupt):
                break
            command, rest = parse_command(line)
            if not command:
                continue
            if command in QUIT_COMMANDS:
                break
            try:
                result = execute_command(conn, operator_id, command, rest)
            except AgentSyncError as exc:
                result = f"error: {exc.message}"
            except Exception as exc:  # pragma: no cover - defensive
                result = f"error: {exc}"
            if result:
                print(result)

    stop.set()
    poller.join(timeout=interval + 1.0)
    # Going idle releases the hold the operator's locks had on the repo (their
    # owner is no longer active), so leaving the console can't wedge anyone.
    try:
        db.set_agent_status(conn, operator_id, AGENT_IDLE)
    except Exception:
        pass
    conn.close()
    return 0
