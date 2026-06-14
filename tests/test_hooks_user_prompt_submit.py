"""UserPromptSubmit hook: gentle push of undelivered messages into context."""

from __future__ import annotations

import io
import json

from agent_sync import hooks, messages


def _run(conn) -> str:
    out = io.StringIO()
    rc = hooks.hook_user_prompt_submit({}, conn=conn, out=out)
    assert rc == 0
    return out.getvalue()


def test_injects_unread_messages_as_additional_context(conn, make_agent, monkeypatch):
    make_agent("agent-a", name="alice")
    make_agent("agent-b", name="bob")
    messages.send_message(conn, "agent-a", "bob", "heads up bob")
    monkeypatch.setenv("AGENT_SYNC_ID", "agent-b")

    data = json.loads(_run(conn))
    assert data["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    assert "heads up bob" in data["hookSpecificOutput"]["additionalContext"]


def test_injects_broadcasts_too(conn, make_agent, monkeypatch):
    make_agent("agent-a", name="alice")
    make_agent("agent-b", name="bob")
    messages.send_message(conn, "agent-a", "all", "everyone listen")
    monkeypatch.setenv("AGENT_SYNC_ID", "agent-b")

    data = json.loads(_run(conn))
    assert "everyone listen" in data["hookSpecificOutput"]["additionalContext"]


def test_silent_when_nothing_new(conn, make_agent, monkeypatch):
    make_agent("agent-b", name="bob")
    monkeypatch.setenv("AGENT_SYNC_ID", "agent-b")
    assert _run(conn) == ""


def test_delivers_each_message_once(conn, make_agent, monkeypatch):
    make_agent("agent-a", name="alice")
    make_agent("agent-b", name="bob")
    messages.send_message(conn, "agent-a", "bob", "only once")
    monkeypatch.setenv("AGENT_SYNC_ID", "agent-b")

    assert "only once" in _run(conn)
    assert _run(conn) == ""  # second turn: already delivered


def test_does_not_push_own_outbound(conn, make_agent, monkeypatch):
    make_agent("agent-a", name="alice")
    make_agent("agent-b", name="bob")
    monkeypatch.setenv("AGENT_SYNC_ID", "agent-a")
    messages.send_message(conn, "agent-a", "all", "from me")
    assert _run(conn) == ""


def test_fails_open_on_empty_payload(conn):
    # No identity env set, no messages: must not raise and must stay silent.
    out = io.StringIO()
    assert hooks.hook_user_prompt_submit({}, conn=conn, out=out) == 0
    assert out.getvalue() == ""
