"""Schema creation and auto-initialisation."""

from __future__ import annotations

import sqlite3

from agent_sync import db, paths, tasks


def test_connect_creates_database_file(repo):
    assert not paths.db_path().exists()
    conn = db.connect()
    try:
        assert paths.db_path().exists()
    finally:
        conn.close()


def test_schema_has_all_expected_tables(conn):
    for name in db.TABLE_NAMES:
        assert db.table_exists(conn, name), f"missing table {name}"


def test_auto_init_on_first_domain_call(repo):
    # No explicit `init`; a domain operation must still succeed because connect()
    # creates the schema on demand.
    conn = db.connect()
    try:
        task = tasks.create_task(conn, "auto-init works")
        assert task.id.startswith("task-")
        assert task.status == "pending"
    finally:
        conn.close()


def test_resolve_agent_id_uses_claude_code_session_env(repo, monkeypatch):
    # Claude Code exports CLAUDE_CODE_SESSION_ID into every shell; the skill's
    # CLI calls must auto-detect it (no AGENT_SYNC_ID needed) and stay stable.
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-xyz")
    first = db.resolve_agent_id(cwd="/repo")
    second = db.resolve_agent_id(cwd="/repo")
    assert first == second
    assert first.startswith("agent-")
    # A different session in the same cwd is a different agent.
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-other")
    assert db.resolve_agent_id(cwd="/repo") != first


def test_resolve_agent_id_explicit_id_wins_over_session(repo, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-xyz")
    monkeypatch.setenv("AGENT_SYNC_ID", "manual")
    assert db.resolve_agent_id(cwd="/repo") == "manual"


def test_resolve_agent_id_legacy_session_env_still_works(repo, monkeypatch):
    monkeypatch.setenv("CLAUDE_SESSION_ID", "legacy")
    assert db.resolve_agent_id(cwd="/repo").startswith("agent-")


def test_resolve_agent_id_hook_payload_matches_skill_env(repo, monkeypatch):
    # The PreToolUse hook resolves identity from its JSON payload session_id,
    # while the skill resolves it from the env var. For the same window these
    # must be the SAME agent, or the hook would block the session's own edits.
    # Crucially this must hold even when the hook reports a differently-spelled
    # cwd than the skill's os.getcwd() — identity is the session id alone.
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-1")
    from_env = db.resolve_agent_id(cwd="C:\\Users\\me\\repo")
    from_hook = db.resolve_agent_id(session_id="sess-1", cwd="/tmp/repo")
    assert from_env == from_hook


def test_identity_source_reports_explicit_env(repo, monkeypatch):
    monkeypatch.setenv("AGENT_SYNC_ID", "x")
    assert "AGENT_SYNC_ID" in db.identity_source()


def test_identity_source_reports_session(repo, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess")
    assert "session" in db.identity_source().lower()


def test_identity_source_reports_local_file_fallback(repo):
    # repo fixture clears AGENT_SYNC_ID and the session env vars.
    assert "local file" in db.identity_source().lower()


def test_stale_threshold_is_env_configurable(conn, make_agent, age_agent, monkeypatch):
    make_agent("a")
    age_agent("a", minutes=5)
    assert db.effective_status(db.get_agent(conn, "a")) == "active"  # default 15 min
    monkeypatch.setenv("AGENT_SYNC_STALE_MINUTES", "3")
    assert db.effective_status(db.get_agent(conn, "a")) == "stale"  # 5 >= 3


def test_offline_threshold_is_env_configurable(conn, make_agent, age_agent, monkeypatch):
    make_agent("a")
    age_agent("a", minutes=30)
    monkeypatch.setenv("AGENT_SYNC_OFFLINE_MINUTES", "20")
    assert db.effective_status(db.get_agent(conn, "a")) == "offline"  # 30 >= 20


def test_invalid_threshold_env_falls_back_to_default(conn, make_agent, age_agent, monkeypatch):
    monkeypatch.setenv("AGENT_SYNC_STALE_MINUTES", "not-a-number")
    make_agent("a")
    age_agent("a", minutes=5)
    # Garbage value is ignored, so the 15-minute default still applies.
    assert db.effective_status(db.get_agent(conn, "a")) == "active"


def test_timestamps_are_utc_iso_round_trip():
    iso = db.now_iso()
    parsed = db.parse_iso(iso)
    assert parsed.tzinfo is not None
    assert parsed.utcoffset().total_seconds() == 0


def test_parse_iso_tolerates_z_suffix():
    parsed = db.parse_iso("2026-01-01T00:00:00Z")
    assert parsed.utcoffset().total_seconds() == 0


def test_migration_upgrades_pre_existing_database_in_place(repo):
    # Simulate a database created by an older agent-sync: a ``locks`` table
    # without the ``kind`` column and no ``task_deps`` table at all.
    paths.ensure_coordination_dir()
    db_file = paths.db_path()
    raw = sqlite3.connect(str(db_file))
    raw.executescript(
        "CREATE TABLE locks (file_path TEXT PRIMARY KEY, owner_agent_id TEXT "
        "NOT NULL, reason TEXT, created_at TEXT NOT NULL, expires_at TEXT NOT NULL);"
        "INSERT INTO locks VALUES ('a.py','x',NULL,'2026-01-01T00:00:00+00:00',"
        "'2030-01-01T00:00:00+00:00');"
    )
    raw.commit()
    raw.close()

    conn = db.connect()  # runs init_db -> _migrate
    try:

        def cols(table):
            return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}

        assert "kind" in cols("locks")
        assert {"reply_to", "acked_at"} <= cols("messages")
        assert db.table_exists(conn, "task_deps")
        # Existing data is preserved; the new column takes its default.
        row = conn.execute("SELECT kind FROM locks WHERE file_path = 'a.py'").fetchone()
        assert row["kind"] == "file"
        # Re-running is a no-op (idempotent).
        before = cols("locks")
        db.init_db(conn)
        assert cols("locks") == before
    finally:
        conn.close()
