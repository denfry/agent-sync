"""Stop hook: block ending a turn while a directed message is pending."""

from __future__ import annotations

import io
import json

from agent_sync import hooks, messages


def _run(conn, payload=None) -> str:
    out = io.StringIO()
    rc = hooks.hook_stop(payload or {}, conn=conn, out=out)
    assert rc == 0
    return out.getvalue()


def test_blocks_when_directed_message_pending(conn, make_agent, monkeypatch):
    make_agent("agent-a", name="alice")
    make_agent("agent-b", name="bob")
    messages.send_message(conn, "agent-a", "bob", "need your input")
    monkeypatch.setenv("AGENT_SYNC_ID", "agent-b")

    data = json.loads(_run(conn))
    assert data["decision"] == "block"
    assert "need your input" in data["reason"]


def test_allows_stop_when_no_messages(conn, make_agent, monkeypatch):
    make_agent("agent-b", name="bob")
    monkeypatch.setenv("AGENT_SYNC_ID", "agent-b")
    assert _run(conn) == ""


def test_ignores_broadcast_only(conn, make_agent, monkeypatch):
    make_agent("agent-a", name="alice")
    make_agent("agent-b", name="bob")
    messages.send_message(conn, "agent-a", "all", "fyi everyone")
    monkeypatch.setenv("AGENT_SYNC_ID", "agent-b")
    # Broadcasts are gentle (UserPromptSubmit), never a reason to trap a turn.
    assert _run(conn) == ""


def test_respects_stop_hook_active_guard(conn, make_agent, monkeypatch):
    make_agent("agent-a", name="alice")
    make_agent("agent-b", name="bob")
    messages.send_message(conn, "agent-a", "bob", "still here")
    monkeypatch.setenv("AGENT_SYNC_ID", "agent-b")
    # Already forced one continuation: let this stop through to avoid a loop.
    assert _run(conn, {"stop_hook_active": True}) == ""


def test_blocks_only_once_per_message(conn, make_agent, monkeypatch):
    make_agent("agent-a", name="alice")
    make_agent("agent-b", name="bob")
    messages.send_message(conn, "agent-a", "bob", "react please")
    monkeypatch.setenv("AGENT_SYNC_ID", "agent-b")

    assert json.loads(_run(conn))["decision"] == "block"
    # After delivery the same message no longer blocks the next stop.
    assert _run(conn) == ""


def test_blocks_on_role_addressed_message(conn, make_agent, monkeypatch):
    make_agent("agent-a", name="alice")
    make_agent("agent-b", name="bob", role="backend")
    messages.send_message(conn, "agent-a", "backend", "backend, ping")
    monkeypatch.setenv("AGENT_SYNC_ID", "agent-b")

    assert json.loads(_run(conn))["decision"] == "block"


def test_fails_open_on_empty_payload(conn):
    out = io.StringIO()
    assert hooks.hook_stop({}, conn=conn, out=out) == 0
    assert out.getvalue() == ""
