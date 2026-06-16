"""Messaging, inbox routing and read state."""

from __future__ import annotations

import pytest

from agent_sync import messages
from agent_sync.errors import NotFound


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


def test_recent_messages_is_global_and_newest_first(conn, make_agent):
    make_agent("agent-a", name="alice")
    make_agent("agent-b", name="bob")
    messages.send_message(conn, "agent-a", "bob", "first")
    messages.send_message(conn, "agent-b", "alice", "second")
    recent = messages.recent_messages(conn, limit=10)
    # Not scoped to any one inbox; both directions appear.
    assert [m.body for m in recent] == ["second", "first"]


def test_recent_messages_respects_limit(conn, make_agent):
    make_agent("agent-a", name="alice")
    for i in range(5):
        messages.send_message(conn, "agent-a", "all", f"msg {i}")
    assert len(messages.recent_messages(conn, limit=3)) == 3


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


# --- push delivery tracking -------------------------------------------------
def test_undelivered_returns_new_messages(conn, make_agent):
    make_agent("agent-a", name="alice")
    make_agent("agent-b", name="bob")
    messages.send_message(conn, "agent-a", "bob", "hi bob")
    assert [m.body for m in messages.undelivered(conn, "agent-b")] == ["hi bob"]


def test_mark_delivered_excludes_from_undelivered(conn, make_agent):
    make_agent("agent-a", name="alice")
    make_agent("agent-b", name="bob")
    msg = messages.send_message(conn, "agent-a", "bob", "hi bob")
    messages.mark_delivered(conn, "agent-b", [msg.id])
    assert messages.undelivered(conn, "agent-b") == []


def test_mark_delivered_is_idempotent(conn, make_agent):
    make_agent("agent-a", name="alice")
    make_agent("agent-b", name="bob")
    msg = messages.send_message(conn, "agent-a", "bob", "hi bob")
    messages.mark_delivered(conn, "agent-b", [msg.id])
    messages.mark_delivered(conn, "agent-b", [msg.id])  # no duplicate row / error
    assert messages.undelivered(conn, "agent-b") == []


def test_undelivered_excludes_own_outbound(conn, make_agent):
    make_agent("agent-a", name="alice")
    messages.send_message(conn, "agent-a", "all", "my own broadcast")
    assert messages.undelivered(conn, "agent-a") == []


def test_delivery_is_tracked_per_agent(conn, make_agent):
    make_agent("agent-a", name="alice")
    make_agent("agent-b", name="bob")
    make_agent("agent-c", name="carol")
    messages.send_message(conn, "agent-a", "all", "hi all")
    ids = [m.id for m in messages.undelivered(conn, "agent-b")]
    messages.mark_delivered(conn, "agent-b", ids)
    # b has had it pushed; c has not.
    assert messages.undelivered(conn, "agent-b") == []
    assert [m.body for m in messages.undelivered(conn, "agent-c")] == ["hi all"]


def test_undelivered_directed_only_skips_broadcast(conn, make_agent):
    make_agent("agent-a", name="alice")
    make_agent("agent-b", name="bob")
    messages.send_message(conn, "agent-a", "all", "broadcast")
    messages.send_message(conn, "agent-a", "bob", "direct")
    bodies = [m.body for m in messages.undelivered(conn, "agent-b", directed_only=True)]
    assert bodies == ["direct"]


def test_undelivered_directed_only_includes_role(conn, make_agent):
    make_agent("agent-a", name="alice")
    make_agent("agent-b", name="bob", role="backend")
    messages.send_message(conn, "agent-a", "backend", "for the role")
    bodies = [m.body for m in messages.undelivered(conn, "agent-b", directed_only=True)]
    assert bodies == ["for the role"]


def test_delivery_is_independent_of_read_state(conn, make_agent):
    make_agent("agent-a", name="alice")
    make_agent("agent-b", name="bob")
    msg = messages.send_message(conn, "agent-a", "bob", "hi")
    messages.mark_delivered(conn, "agent-b", [msg.id])
    # Pushed into context, but not explicitly acknowledged: still "unread".
    assert messages.unread_count(conn, "agent-b") == 1


# --- reply threading & acknowledgement --------------------------------------
def test_send_with_reply_to_threads_message(conn, make_agent):
    make_agent("agent-a", name="alice")
    make_agent("agent-b", name="bob")
    parent = messages.send_message(conn, "agent-a", "bob", "ping")
    reply = messages.send_message(conn, "agent-b", "alice", "pong", reply_to=parent.id)
    assert reply.reply_to == parent.id


def test_send_reply_to_unknown_message_raises(conn, make_agent):
    make_agent("agent-a", name="alice")
    with pytest.raises(NotFound):
        messages.send_message(conn, "agent-a", "all", "x", reply_to="msg-nope")


def test_ack_sets_acked_at(conn, make_agent):
    make_agent("agent-a", name="alice")
    make_agent("agent-b", name="bob")
    msg = messages.send_message(conn, "agent-a", "bob", "hi")
    assert msg.acked_at is None
    acked = messages.ack_message(conn, "agent-b", msg.id)
    assert acked.acked_at is not None


def test_ack_is_idempotent(conn, make_agent):
    make_agent("agent-a", name="alice")
    make_agent("agent-b", name="bob")
    msg = messages.send_message(conn, "agent-a", "bob", "hi")
    first = messages.ack_message(conn, "agent-b", msg.id)
    second = messages.ack_message(conn, "agent-b", msg.id)
    assert first.acked_at == second.acked_at


def test_ack_unknown_message_raises(conn, make_agent):
    make_agent("agent-a", name="alice")
    with pytest.raises(NotFound):
        messages.ack_message(conn, "agent-a", "msg-nope")
