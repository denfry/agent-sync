"""PreToolUse hook: lock-aware blocking semantics."""

from __future__ import annotations

import pytest

from agent_sync import hooks, locks


def _payload(tool: str, file_path: str) -> dict:
    return {"tool_name": tool, "tool_input": {"file_path": file_path}}


def test_pre_allows_unlocked_file(conn, monkeypatch):
    monkeypatch.setenv("AGENT_SYNC_ID", "agent-b")
    assert hooks.hook_pre_tool_use(_payload("Edit", "src/x.ts"), conn=conn) == 0


def test_pre_blocks_file_locked_by_other_active_agent(conn, make_agent, monkeypatch, capsys):
    make_agent("agent-a", name="backend")
    locks.acquire_lock(conn, "agent-a", "src/x.ts", reason="busy")
    monkeypatch.setenv("AGENT_SYNC_ID", "agent-b")
    assert hooks.hook_pre_tool_use(_payload("Write", "src/x.ts"), conn=conn) == 2
    err = capsys.readouterr().err
    assert "BLOCKED" in err
    assert "backend" in err


def test_pre_allows_owner_to_edit_own_locked_file(conn, make_agent, monkeypatch):
    make_agent("agent-a", name="backend")
    locks.acquire_lock(conn, "agent-a", "src/x.ts")
    monkeypatch.setenv("AGENT_SYNC_ID", "agent-a")
    assert hooks.hook_pre_tool_use(_payload("MultiEdit", "src/x.ts"), conn=conn) == 0


@pytest.mark.parametrize("tool", ["Edit", "Write", "MultiEdit"])
def test_pre_supports_all_file_editing_tools(conn, make_agent, monkeypatch, tool):
    make_agent("agent-a", name="backend")
    locks.acquire_lock(conn, "agent-a", "src/x.ts")
    monkeypatch.setenv("AGENT_SYNC_ID", "agent-b")
    assert hooks.hook_pre_tool_use(_payload(tool, "src/x.ts"), conn=conn) == 2


def test_pre_ignores_non_editing_tools(conn, make_agent):
    make_agent("agent-a")
    locks.acquire_lock(conn, "agent-a", "src/x.ts")
    payload = {"tool_name": "Bash", "tool_input": {"command": "ls"}}
    assert hooks.hook_pre_tool_use(payload, conn=conn) == 0


def test_pre_fails_open_on_empty_payload(conn):
    assert hooks.hook_pre_tool_use({}, conn=conn) == 0


def test_pre_blocks_using_absolute_path(conn, make_agent, monkeypatch, repo):
    make_agent("agent-a", name="backend")
    locks.acquire_lock(conn, "agent-a", "src/x.ts")
    monkeypatch.setenv("AGENT_SYNC_ID", "agent-b")
    absolute = str(repo / "src" / "x.ts")
    assert hooks.hook_pre_tool_use(_payload("Edit", absolute), conn=conn) == 2
