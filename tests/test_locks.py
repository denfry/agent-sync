"""File locking, TTL expiry and ownership rules."""

from __future__ import annotations

import pytest

from agent_sync import locks
from agent_sync.errors import LockConflict
from agent_sync.models import LOCK_FILE, LOCK_RESOURCE


def test_lock_file(conn, make_agent):
    make_agent("agent-a")
    lock = locks.acquire_lock(conn, "agent-a", "src/a.ts", reason="editing")
    assert lock.owner_agent_id == "agent-a"
    assert locks.active_lock_for(conn, "src/a.ts") is not None


def test_cannot_lock_file_owned_by_active_other_agent(conn, make_agent):
    make_agent("agent-a")
    make_agent("agent-b")
    locks.acquire_lock(conn, "agent-a", "src/a.ts")
    with pytest.raises(LockConflict):
        locks.acquire_lock(conn, "agent-b", "src/a.ts")


def test_relocking_own_file_extends_ttl(conn, make_agent):
    make_agent("agent-a")
    first = locks.acquire_lock(conn, "agent-a", "src/a.ts", ttl_minutes=30)
    second = locks.acquire_lock(conn, "agent-a", "src/a.ts", ttl_minutes=120)
    assert second.owner_agent_id == "agent-a"
    assert second.expires_at >= first.expires_at


def test_expired_locks_are_ignored(conn, make_agent):
    make_agent("agent-a")
    make_agent("agent-b")
    # A negative TTL produces an already-expired lock.
    locks.acquire_lock(conn, "agent-a", "src/a.ts", ttl_minutes=-1)
    assert locks.active_lock_for(conn, "src/a.ts") is None
    # Because it is expired, another agent may take it.
    lock = locks.acquire_lock(conn, "agent-b", "src/a.ts")
    assert lock.owner_agent_id == "agent-b"


def test_lock_held_by_stale_agent_is_ignored(conn, make_agent, age_agent):
    make_agent("agent-a")
    make_agent("agent-b")
    locks.acquire_lock(conn, "agent-a", "src/a.ts")
    age_agent("agent-a", minutes=999)
    assert locks.active_lock_for(conn, "src/a.ts") is None
    lock = locks.acquire_lock(conn, "agent-b", "src/a.ts")
    assert lock.owner_agent_id == "agent-b"


def test_unlock_by_owner(conn, make_agent):
    make_agent("agent-a")
    locks.acquire_lock(conn, "agent-a", "src/a.ts")
    assert locks.release_lock(conn, "agent-a", "src/a.ts") is True
    assert locks.active_lock_for(conn, "src/a.ts") is None


def test_unlock_by_non_owner_requires_force(conn, make_agent):
    make_agent("agent-a")
    make_agent("agent-b")
    locks.acquire_lock(conn, "agent-a", "src/a.ts")
    with pytest.raises(LockConflict):
        locks.release_lock(conn, "agent-b", "src/a.ts")
    assert locks.release_lock(conn, "agent-b", "src/a.ts", force=True) is True


def test_gc_removes_expired_locks(conn, make_agent):
    make_agent("agent-a")
    locks.acquire_lock(conn, "agent-a", "src/a.ts", ttl_minutes=-1)
    removed = locks.gc_locks(conn)
    assert removed == 1
    assert locks.list_locks(conn, include_expired=True) == []


def test_list_locks_only_shows_live_by_default(conn, make_agent):
    make_agent("agent-a")
    locks.acquire_lock(conn, "agent-a", "live.ts")
    locks.acquire_lock(conn, "agent-a", "dead.ts", ttl_minutes=-1)
    live = locks.list_locks(conn)
    assert [lock.file_path for lock in live] == ["live.ts"]
    assert len(locks.list_locks(conn, include_expired=True)) == 2


# --- named / resource locks -------------------------------------------------
def test_resource_lock_kind_is_persisted(conn, make_agent):
    make_agent("agent-a")
    lock = locks.acquire_lock(conn, "agent-a", "db-migrations", kind=LOCK_RESOURCE)
    assert lock.kind == LOCK_RESOURCE and lock.is_resource
    [stored] = locks.list_locks(conn)
    assert stored.kind == LOCK_RESOURCE


def test_resource_lock_does_not_block_unrelated_file(conn, make_agent):
    make_agent("agent-a")
    make_agent("agent-b")
    locks.acquire_lock(conn, "agent-a", "db-migrations", kind=LOCK_RESOURCE)
    # An unrelated file is still freely lockable by someone else.
    other = locks.acquire_lock(conn, "agent-b", "src/app.py")
    assert other.kind == LOCK_FILE
    # But the same resource key conflicts for a different active agent.
    with pytest.raises(LockConflict):
        locks.acquire_lock(conn, "agent-b", "db-migrations", kind=LOCK_RESOURCE)


# --- blocking acquire (lock --wait) -----------------------------------------
def test_acquire_blocking_returns_immediately_when_free(conn, make_agent):
    make_agent("agent-a")
    lock = locks.acquire_lock_blocking(conn, "agent-a", "f.py", wait_seconds=5)
    assert lock.owner_agent_id == "agent-a"


def test_acquire_blocking_times_out_then_raises(conn, make_agent):
    make_agent("agent-a")
    make_agent("agent-b")
    locks.acquire_lock(conn, "agent-a", "f.py")
    with pytest.raises(LockConflict):
        locks.acquire_lock_blocking(
            conn, "agent-b", "f.py", wait_seconds=0.05, poll_seconds=0.01
        )


def test_acquire_blocking_succeeds_when_holder_releases(conn, make_agent, monkeypatch):
    make_agent("agent-a")
    make_agent("agent-b")
    locks.acquire_lock(conn, "agent-a", "f.py")

    # Simulate the holder releasing while agent-b is waiting: the first poll
    # sleep frees the lock, so the next retry acquires it.
    def fake_sleep(_seconds):
        locks.release_lock(conn, "agent-a", "f.py", force=True)

    monkeypatch.setattr(locks.time, "sleep", fake_sleep)
    lock = locks.acquire_lock_blocking(
        conn, "agent-b", "f.py", wait_seconds=5, poll_seconds=0.01
    )
    assert lock.owner_agent_id == "agent-b"
