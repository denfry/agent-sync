"""Messaging, inbox routing and read state."""

from __future__ import annotations

from agent_sync import messages


def test_send_and_receive_direct_by_name(conn, make_agent):
    make_agent("agent-a", name="alice")
    make_agent("agent-b", name="bob")
    messages.send_message(conn, "agent-a", "bob", "hi bob")
    box = messages.inbox(conn, "agent-b")
    assert [m.body for m in box] == ["hi bob"]


def test_inbox_includes_broadcast(conn, make_agent):
    make_agent("agent-a", name="alice")
    make_agent("agent-b", name="bob")
    messages.send_message(conn, "agent-a", "all", "hello everyone")
    assert any(m.body == "hello everyone" for m in messages.inbox(conn, "agent-b"))


def test_inbox_matches_role(conn, make_agent):
    make_agent("agent-a", name="alice")
    make_agent("agent-b", name="bob", role="backend")
    messages.send_message(conn, "agent-a", "backend", "for the backend role")
    bodies = [m.body for m in messages.inbox(conn, "agent-b")]
    assert "for the backend role" in bodies


def test_inbox_matches_exact_id(conn, make_agent):
    make_agent("agent-a", name="alice")
    make_agent("agent-b", name="bob")
    messages.send_message(conn, "agent-a", "agent-b", "by id")
    assert any(m.body == "by id" for m in messages.inbox(conn, "agent-b"))


def test_agent_does_not_see_others_direct_messages(conn, make_agent):
    make_agent("agent-a", name="alice")
    make_agent("agent-b", name="bob")
    make_agent("agent-c", name="carol")
    messages.send_message(conn, "agent-a", "bob", "private to bob")
    assert messages.inbox(conn, "agent-c") == []


def test_read_message_marks_read(conn, make_agent):
    make_agent("agent-a", name="alice")
    make_agent("agent-b", name="bob")
    msg = messages.send_message(conn, "agent-a", "bob", "hi")
    assert messages.unread_count(conn, "agent-b") == 1
    read = messages.read_message(conn, "agent-b", msg.id)
    assert read.read_at is not None
    assert messages.unread_count(conn, "agent-b") == 0


def test_decisions_are_recorded(conn, make_agent):
    make_agent("agent-a", name="alice")
    messages.add_decision(conn, "agent-a", "Use SQLite")
    decisions = messages.list_decisions(conn)
    assert decisions[0].body == "Use SQLite"
