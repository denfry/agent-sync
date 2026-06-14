"""Task lifecycle and ownership rules."""

from __future__ import annotations

import pytest

from agent_sync import db, tasks
from agent_sync.errors import TaskConflict


def test_create_task_with_files(conn):
    task = tasks.create_task(
        conn, "Build API", description="REST endpoints", files=["a.py", "b.py"]
    )
    assert task.status == "pending"
    assert task.owner_agent_id is None
    assert tasks.task_files(conn, task.id) == ["a.py", "b.py"]


def test_claim_task_sets_owner_and_in_progress(conn, make_agent):
    make_agent("agent-a")
    task = tasks.create_task(conn, "T")
    claimed = tasks.claim_task(conn, "agent-a", task.id)
    assert claimed.status == "in_progress"
    assert claimed.owner_agent_id == "agent-a"
    # The agent's current task pointer is updated too.
    assert db.get_agent(conn, "agent-a").current_task_id == task.id


def test_claim_by_title_is_case_insensitive(conn, make_agent):
    make_agent("agent-a")
    tasks.create_task(conn, "Update Login UI")
    claimed = tasks.claim_task(conn, "agent-a", "update login ui")
    assert claimed.status == "in_progress"


def test_cannot_claim_task_owned_by_active_other_agent(conn, make_agent):
    make_agent("agent-a")
    make_agent("agent-b")
    task = tasks.create_task(conn, "T")
    tasks.claim_task(conn, "agent-a", task.id)
    with pytest.raises(TaskConflict):
        tasks.claim_task(conn, "agent-b", task.id)


def test_stale_owner_can_be_taken_over(conn, make_agent, age_agent):
    make_agent("agent-a")
    make_agent("agent-b")
    task = tasks.create_task(conn, "T")
    tasks.claim_task(conn, "agent-a", task.id)
    age_agent("agent-a", minutes=999)  # agent-a is now offline
    claimed = tasks.claim_task(conn, "agent-b", task.id)
    assert claimed.owner_agent_id == "agent-b"


def test_complete_task_marks_done_and_clears_pointer(conn, make_agent):
    make_agent("agent-a")
    task = tasks.create_task(conn, "T")
    tasks.claim_task(conn, "agent-a", task.id)
    done = tasks.complete_task(conn, "agent-a", task.id)
    assert done.status == "done"
    assert done.completed_at is not None
    assert db.get_agent(conn, "agent-a").current_task_id is None


def test_block_task_records_reason(conn, make_agent):
    make_agent("agent-a")
    task = tasks.create_task(conn, "T", description="start")
    blocked = tasks.block_task(conn, "agent-a", task.id, "waiting on API")
    assert blocked.status == "blocked"
    assert "waiting on API" in blocked.description


def test_ambiguous_title_raises(conn):
    tasks.create_task(conn, "dup")
    tasks.create_task(conn, "dup")
    with pytest.raises(TaskConflict):
        tasks.find_task(conn, "dup")
