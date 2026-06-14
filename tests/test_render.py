"""Renderer output shape and size guarantees."""

from __future__ import annotations

from agent_sync import db, locks, messages, render, tasks


def _seed(conn):
    db.ensure_agent(conn, "agent-a", name="alice", role="backend")
    db.ensure_agent(conn, "agent-b", name="bob", role="frontend")
    task = tasks.create_task(conn, "Do a thing", files=["a.py"])
    tasks.claim_task(conn, "agent-a", task.id)
    locks.acquire_lock(conn, "agent-a", "a.py", reason="editing")
    messages.send_message(conn, "agent-b", "all", "heads up")


def test_compact_status_renders_markdown(conn):
    _seed(conn)
    compact = render.render_compact(conn, "agent-a")
    assert compact.startswith("## agent-sync")
    assert "active agents" in compact
    assert "locks" in compact


def test_verbose_status_includes_all_sections(conn):
    _seed(conn)
    verbose = render.render_status(conn, "agent-a")
    for header in (
        "## Current agent",
        "## Agents",
        "## Tasks",
        "## Locks",
        "## Unread messages",
        "## Recent activity",
    ):
        assert header in verbose


def test_compact_is_smaller_than_verbose(conn):
    _seed(conn)
    compact = render.render_compact(conn, "agent-a")
    verbose = render.render_status(conn, "agent-a")
    assert len(compact) < len(verbose)


def test_compact_marks_current_agent(conn):
    _seed(conn)
    compact = render.render_compact(conn, "agent-a")
    assert "alice" in compact
