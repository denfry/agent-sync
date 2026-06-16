"""Atomic shared-file append (``agent-sync append``)."""

from __future__ import annotations

from agent_sync import cli, locks


def test_append_creates_and_extends_file(repo, monkeypatch, capsys):
    monkeypatch.setenv("AGENT_SYNC_ID", "agent-a")
    assert cli.main(["append", "shared.txt", "--content", "line one"]) == 0
    assert cli.main(["append", "shared.txt", "--content", "line two"]) == 0
    target = repo / "shared.txt"
    assert target.read_text(encoding="utf-8") == "line one\nline two\n"


def test_append_releases_lock_afterwards(repo, monkeypatch):
    monkeypatch.setenv("AGENT_SYNC_ID", "agent-a")
    assert cli.main(["append", "shared.txt", "--content", "x"]) == 0
    # The append-time lock must not linger, or a different agent could not edit.
    from agent_sync import db, paths

    conn = db.connect()
    try:
        norm = paths.normalize_repo_path("shared.txt")
        assert locks.active_lock_for(conn, norm) is None
    finally:
        conn.close()


def test_append_no_newline_flag(repo, monkeypatch):
    monkeypatch.setenv("AGENT_SYNC_ID", "agent-a")
    assert cli.main(["append", "shared.txt", "--content", "a", "--no-newline"]) == 0
    assert cli.main(["append", "shared.txt", "--content", "b", "--no-newline"]) == 0
    assert (repo / "shared.txt").read_text(encoding="utf-8") == "ab"


def test_append_blocked_by_active_other_agent_lock(repo, monkeypatch):
    monkeypatch.setenv("AGENT_SYNC_ID", "agent-a")
    cli.main(["register", "--name", "a"])
    # agent-a holds the lock; agent-b's append should fail closed (exit 2)
    # without waiting because no --wait was given.
    assert cli.main(["lock", "shared.txt", "--reason", "busy"]) == 0
    monkeypatch.setenv("AGENT_SYNC_ID", "agent-b")
    cli.main(["register", "--name", "b"])
    assert cli.main(["append", "shared.txt", "--content", "nope"]) == 2
    # The file was never written because the lock was never acquired.
    assert not (repo / "shared.txt").exists()
