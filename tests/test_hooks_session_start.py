"""SessionStart hook: registration, status injection, and opt-in auto-claim."""

from __future__ import annotations

import io

from agent_sync import db, hooks, locks, tasks


def _run(payload=None, *, conn):
    """Run the SessionStart hook against *conn*, returning (exit_code, output)."""
    out = io.StringIO()
    code = hooks.hook_session_start(payload or {}, conn=conn, out=out)
    return code, out.getvalue()


def test_session_start_registers_and_emits_status(conn, monkeypatch):
    monkeypatch.setenv("AGENT_SYNC_ID", "me")
    code, text = _run(conn=conn)
    assert code == 0
    assert db.get_agent(conn, "me") is not None
    assert "agent-sync" in text  # the compact state block is injected


def test_auto_claim_off_by_default(conn, monkeypatch):
    monkeypatch.setenv("AGENT_SYNC_ID", "me")
    task = tasks.create_task(conn, "Do the thing")
    _run(conn=conn)
    after = tasks.get_task(conn, task.id)
    assert after.status == "pending"
    assert after.owner_agent_id is None


def test_auto_claim_takes_next_task_when_enabled(conn, monkeypatch):
    monkeypatch.setenv("AGENT_SYNC_ID", "me")
    monkeypatch.setenv("AGENT_SYNC_AUTO_CLAIM", "1")
    task = tasks.create_task(conn, "Do the thing")
    code, text = _run(conn=conn)
    assert code == 0
    claimed = tasks.get_task(conn, task.id)
    assert claimed.status == "in_progress"
    assert claimed.owner_agent_id == "me"
    assert db.get_agent(conn, "me").current_task_id == task.id
    assert task.id in text  # the note names the (safe, generated) task id


def test_auto_claim_prefers_higher_priority(conn, monkeypatch):
    monkeypatch.setenv("AGENT_SYNC_ID", "me")
    monkeypatch.setenv("AGENT_SYNC_AUTO_CLAIM", "1")
    tasks.create_task(conn, "low", priority=0)
    high = tasks.create_task(conn, "high", priority=5)
    _run(conn=conn)
    assert tasks.get_task(conn, high.id).owner_agent_id == "me"


def test_auto_claim_skips_when_agent_already_busy(conn, monkeypatch):
    """A SessionStart fired mid-task (resume/compact) must not grab a 2nd task."""
    monkeypatch.setenv("AGENT_SYNC_ID", "me")
    monkeypatch.setenv("AGENT_SYNC_AUTO_CLAIM", "1")
    db.ensure_agent(conn, "me")
    busy = tasks.create_task(conn, "A")
    tasks.claim_task(conn, "me", busy.id)  # now working on A
    other = tasks.create_task(conn, "B")
    _run(conn=conn)
    assert tasks.get_task(conn, other.id).status == "pending"
    assert db.get_agent(conn, "me").current_task_id == busy.id


def test_auto_claim_is_noop_when_no_tasks(conn, monkeypatch):
    monkeypatch.setenv("AGENT_SYNC_ID", "me")
    monkeypatch.setenv("AGENT_SYNC_AUTO_CLAIM", "1")
    code, _ = _run(conn=conn)
    assert code == 0


def test_session_start_never_blocks_on_empty_payload(conn):
    code, _ = _run({}, conn=conn)
    assert code == 0


def test_session_start_gcs_stale_locks(conn, monkeypatch):
    monkeypatch.setenv("AGENT_SYNC_ID", "me")
    # A previous (now crashed) session left an expired lock behind.
    db.ensure_agent(conn, "ghost")
    locks.acquire_lock(conn, "ghost", "src/x.py", ttl_minutes=-1)
    code, _ = _run(conn=conn)
    assert code == 0
    # gc ran at session start, so the stale lock is gone for the new session.
    assert locks.list_locks(conn, include_expired=True) == []
