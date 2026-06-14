"""PostToolUse hook: activity logging."""

from __future__ import annotations

import pytest

from agent_sync import hooks, messages


@pytest.mark.parametrize("tool", ["Edit", "Write", "MultiEdit"])
def test_post_logs_activity_for_each_edit_tool(conn, monkeypatch, tool):
    monkeypatch.setenv("AGENT_SYNC_ID", "agent-b")
    payload = {"tool_name": tool, "tool_input": {"file_path": f"src/{tool}.ts"}}
    assert hooks.hook_post_tool_use(payload, conn=conn) == 0
    activity = messages.recent_activity(conn)
    assert activity[0].file_path == f"src/{tool}.ts"
    assert activity[0].tool_name == tool
    assert activity[0].event_type == "edit"


def test_post_does_not_log_non_editing_tools(conn, monkeypatch):
    monkeypatch.setenv("AGENT_SYNC_ID", "agent-b")
    payload = {"tool_name": "Bash", "tool_input": {"command": "ls"}}
    assert hooks.hook_post_tool_use(payload, conn=conn) == 0
    assert messages.recent_activity(conn) == []


def test_post_never_blocks_on_empty_payload(conn):
    assert hooks.hook_post_tool_use({}, conn=conn) == 0
