"""Schema creation and auto-initialisation."""

from __future__ import annotations

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
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-1")
    from_env = db.resolve_agent_id(cwd="/repo")
    from_hook = db.resolve_agent_id(session_id="sess-1", cwd="/repo")
    assert from_env == from_hook


def test_timestamps_are_utc_iso_round_trip():
    iso = db.now_iso()
    parsed = db.parse_iso(iso)
    assert parsed.tzinfo is not None
    assert parsed.utcoffset().total_seconds() == 0


def test_parse_iso_tolerates_z_suffix():
    parsed = db.parse_iso("2026-01-01T00:00:00Z")
    assert parsed.utcoffset().total_seconds() == 0
