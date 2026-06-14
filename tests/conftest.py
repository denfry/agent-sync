"""Shared pytest fixtures.

Every test runs against a throwaway coordination database inside a ``tmp_path``.
The ``AGENT_SYNC_ROOT`` environment override redirects all of agent-sync's state
there, so tests never touch the real home directory or working tree, never hit
the network, and never depend on Claude Code being installed.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from agent_sync import db


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """Point agent-sync at an isolated temp repo and clear identity env vars."""
    monkeypatch.setenv("AGENT_SYNC_ROOT", str(tmp_path))
    monkeypatch.delenv("AGENT_SYNC_ID", raising=False)
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    monkeypatch.delenv("AGENT_SYNC_AUTO_RELEASE_LOCKS", raising=False)
    return tmp_path


@pytest.fixture
def conn(repo):
    """An open, auto-initialised connection to the temp database."""
    connection = db.connect()
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def make_agent(conn):
    """Factory that registers an active agent and returns it."""

    def _make(agent_id, name=None, role=None, status="active"):
        return db.ensure_agent(
            conn, agent_id, name=name or agent_id, role=role, status=status
        )

    return _make


@pytest.fixture
def age_agent(conn):
    """Factory that backdates an agent's ``last_seen`` to simulate staleness."""

    def _age(agent_id, minutes):
        ts = (db.now() - timedelta(minutes=minutes)).isoformat()
        with db.transaction(conn):
            conn.execute(
                "UPDATE agents SET last_seen = ? WHERE id = ?", (ts, agent_id)
            )

    return _age
