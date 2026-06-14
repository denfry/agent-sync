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


def test_timestamps_are_utc_iso_round_trip():
    iso = db.now_iso()
    parsed = db.parse_iso(iso)
    assert parsed.tzinfo is not None
    assert parsed.utcoffset().total_seconds() == 0


def test_parse_iso_tolerates_z_suffix():
    parsed = db.parse_iso("2026-01-01T00:00:00Z")
    assert parsed.utcoffset().total_seconds() == 0
