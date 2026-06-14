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


def test_claim_next_picks_highest_priority_then_oldest(conn, make_agent):
    make_agent("agent-a")
    tasks.create_task(conn, "low", priority=0)
    high = tasks.create_task(conn, "high", priority=10)
    tasks.create_task(conn, "low2", priority=0)
    claimed = tasks.claim_next_task(conn, "agent-a")
    assert claimed is not None
    assert claimed.id == high.id
    assert claimed.status == "in_progress"
    assert claimed.owner_agent_id == "agent-a"
    assert db.get_agent(conn, "agent-a").current_task_id == high.id


def test_claim_next_returns_none_when_nothing_available(conn, make_agent):
    make_agent("agent-a")
    assert tasks.claim_next_task(conn, "agent-a") is None


def test_claim_next_skips_tasks_owned_by_active_agents(conn, make_agent):
    make_agent("agent-a")
    make_agent("agent-b")
    taken = tasks.create_task(conn, "taken", priority=10)
    tasks.claim_task(conn, "agent-a", taken.id)
    free = tasks.create_task(conn, "free", priority=0)
    claimed = tasks.claim_next_task(conn, "agent-b")
    assert claimed is not None and claimed.id == free.id


def test_claim_next_reclaims_abandoned_task(conn, make_agent, age_agent):
    make_agent("agent-a")
    make_agent("agent-b")
    task = tasks.create_task(conn, "T")
    tasks.claim_task(conn, "agent-a", task.id)
    age_agent("agent-a", minutes=999)  # agent-a crashed / went offline
    claimed = tasks.claim_next_task(conn, "agent-b")
    assert claimed is not None and claimed.id == task.id
    assert claimed.owner_agent_id == "agent-b"


def test_claim_next_does_not_steal_abandoned_when_disabled(conn, make_agent, age_agent):
    make_agent("agent-a")
    make_agent("agent-b")
    task = tasks.create_task(conn, "T")
    tasks.claim_task(conn, "agent-a", task.id)
    age_agent("agent-a", minutes=999)
    assert tasks.claim_next_task(conn, "agent-b", include_abandoned=False) is None


def test_claim_next_skips_blocked_and_done(conn, make_agent):
    make_agent("agent-a")
    t1 = tasks.create_task(conn, "blocked-one")
    tasks.block_task(conn, "agent-a", t1.id, "waiting")
    t2 = tasks.create_task(conn, "done-one")
    tasks.claim_task(conn, "agent-a", t2.id)
    tasks.complete_task(conn, "agent-a", t2.id)
    assert tasks.claim_next_task(conn, "agent-a") is None
