"""Operator console: terminal sanitization, command parsing, the live feed and
operator actions. These cover the pure logic only, so they need no TUI extra."""

from __future__ import annotations

import pytest

from agent_sync import console, db, locks, messages, tasks
from agent_sync.errors import LockConflict
from agent_sync.models import OPERATOR_ID


# --- sanitization -----------------------------------------------------------
def test_sanitize_strips_ansi_and_control_chars():
    hostile = "alice\x1b[31mRED\x1b[0m\x07\x00"
    out = console.sanitize_terminal(hostile)
    assert "\x1b" not in out  # ESC gone -> no ANSI injection
    assert "\x07" not in out and "\x00" not in out
    assert out.startswith("alice")


def test_sanitize_collapses_newlines_to_spaces():
    out = console.sanitize_terminal("line one\nline two\tcol")
    assert "\n" not in out and "\t" not in out
    assert out == "line one line two col"


def test_sanitize_none_is_empty():
    assert console.sanitize_terminal(None) == ""


# --- command parsing --------------------------------------------------------
@pytest.mark.parametrize(
    "line, expected",
    [
        ("send bob hello world", ("send", "bob hello world")),
        ("  LOCK src/app.ts  ", ("lock", "src/app.ts")),
        ("status", ("status", "")),
        ("", ("", "")),
    ],
)
def test_parse_command(line, expected):
    assert console.parse_command(line) == expected


# --- operator identity ------------------------------------------------------
def test_ensure_operator_registers_active(conn):
    console.ensure_operator(conn, name="me")
    operator = db.get_agent(conn, OPERATOR_ID)
    assert operator is not None
    assert db.effective_status(operator) == "active"


def test_operator_excluded_from_feed_presence(conn, make_agent):
    console.ensure_operator(conn)
    state = console.ConsoleState()
    console.poll_events(conn, state)  # prime
    make_agent("agent-a", name="alice")
    events = console.poll_events(conn, state)
    actors = {e.actor for e in events if e.source == "presence"}
    assert "alice" in actors
    assert "operator" not in actors


# --- live feed --------------------------------------------------------------
def test_poll_priming_returns_nothing(conn, make_agent):
    make_agent("agent-a", name="alice")
    messages.send_message(conn, "agent-a", "all", "pre-existing")
    state = console.ConsoleState()
    assert console.poll_events(conn, state) == []  # first call only seeds cursors


def test_poll_reports_new_message(conn, make_agent):
    make_agent("agent-a", name="alice")
    state = console.ConsoleState()
    console.poll_events(conn, state)  # prime
    messages.send_message(conn, "agent-a", "all", "fresh news")
    events = console.poll_events(conn, state)
    assert any(e.source == "message" and "fresh news" in e.text for e in events)


def test_poll_reports_new_activity_and_lock(conn, make_agent):
    make_agent("agent-a", name="alice")
    state = console.ConsoleState()
    console.poll_events(conn, state)  # prime
    messages.log_activity(conn, "agent-a", event_type="edit", body="Edit a.py")
    locks.acquire_lock(conn, "agent-a", "a.py", reason="editing")
    events = console.poll_events(conn, state)
    assert any(e.source == "activity" for e in events)
    assert any(e.source == "lock" and "locked a.py" in e.text for e in events)


def test_format_event_shape():
    event = console.Event("2026-06-14T12:01:02+00:00", "message", "alice", "→all: hi")
    line = console.format_event(event)
    assert line.startswith("12:01:02")
    assert "alice" in line and "→all: hi" in line


def test_format_event_sanitizes_hostile_body():
    event = console.Event("2026-06-14T12:01:02+00:00", "message", "x", "\x1b[2Jcleared")
    assert "\x1b" not in console.format_event(event)


# --- operator actions -------------------------------------------------------
def test_execute_send(conn, make_agent):
    make_agent("agent-a", name="alice")
    console.ensure_operator(conn)
    out = console.execute_command(conn, OPERATOR_ID, "send", "alice stop editing")
    assert "alice" in out
    box = messages.inbox(conn, "agent-a")
    assert any(m.body == "stop editing" for m in box)


def test_execute_directive_all_fans_out_directed(conn, make_agent):
    make_agent("agent-a", name="alice")
    make_agent("agent-b", name="bob")
    console.ensure_operator(conn)
    out = console.execute_command(conn, OPERATOR_ID, "directive", "all hotfix now")
    assert "2 active" in out
    recipients = {m.recipient for m in messages.recent_messages(conn)}
    # directed copies to each agent id, not a single broadcast to "all"
    assert recipients == {"agent-a", "agent-b"}


def test_execute_lock_and_unlock(conn, make_agent):
    console.ensure_operator(conn)
    locked = console.execute_command(conn, OPERATOR_ID, "lock", "src/app.ts freeze")
    assert "locked src/app.ts" in locked
    assert locks.active_lock_for(conn, "src/app.ts") is not None
    unlocked = console.execute_command(conn, OPERATOR_ID, "unlock", "src/app.ts")
    assert "unlocked src/app.ts" in unlocked


def test_operator_unlock_breaks_another_agents_lock(conn, make_agent):
    make_agent("agent-a", name="alice")
    locks.acquire_lock(conn, "agent-a", "a.py", reason="mine")
    console.ensure_operator(conn)
    out = console.execute_command(conn, OPERATOR_ID, "unlock", "a.py")
    assert "unlocked a.py" in out


def test_execute_lock_conflict_raises(conn, make_agent):
    make_agent("agent-a", name="alice")
    locks.acquire_lock(conn, "agent-a", "a.py", reason="mine")
    console.ensure_operator(conn)
    with pytest.raises(LockConflict):
        console.execute_command(conn, OPERATOR_ID, "lock", "a.py")


def test_execute_task_lifecycle(conn):
    console.ensure_operator(conn)
    created = console.execute_command(conn, OPERATOR_ID, "task", "new Ship the thing")
    assert "created" in created
    done = console.execute_command(conn, OPERATOR_ID, "task", "done Ship the thing")
    assert "completed" in done
    assert tasks.list_tasks(conn)[0].status == "done"


def test_execute_task_block_requires_reason(conn):
    console.ensure_operator(conn)
    tasks.create_task(conn, "Risky task")
    bad = console.execute_command(conn, OPERATOR_ID, "task", "block Risky task")
    assert bad.startswith("usage:")
    ok = console.execute_command(conn, OPERATOR_ID, "task", "block Risky task :: API down")
    assert "blocked" in ok


def test_execute_unknown_command(conn):
    console.ensure_operator(conn)
    assert "unknown command" in console.execute_command(conn, OPERATOR_ID, "frobnicate", "")


def test_execute_status_snapshot(conn, make_agent):
    make_agent("agent-a", name="alice", role="backend")
    console.ensure_operator(conn)
    out = console.execute_command(conn, OPERATOR_ID, "status", "")
    assert "agents (" in out and "alice" in out
