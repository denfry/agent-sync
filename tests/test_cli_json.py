"""Machine-readable ``--json`` output for status/locks/inbox/tasks."""

from __future__ import annotations

import json

from agent_sync import cli


def _json_out(capsys):
    return json.loads(capsys.readouterr().out)


def test_status_json_is_structured(repo, monkeypatch, capsys):
    monkeypatch.setenv("AGENT_SYNC_ID", "agent-a")
    cli.main(["register", "--name", "alice", "--role", "backend"])
    cli.main(["lock", "src/app.py", "--reason", "editing"])
    capsys.readouterr()

    assert cli.main(["status", "--json"]) == 0
    data = _json_out(capsys)
    assert data["you"]["name"] == "alice"
    assert data["you"]["registered"] is True
    assert data["active_agent_count"] >= 1
    assert any(lk["file_path"] == "src/app.py" for lk in data["locks"])


def test_locks_json_distinguishes_resource(repo, monkeypatch, capsys):
    monkeypatch.setenv("AGENT_SYNC_ID", "agent-a")
    cli.main(["register", "--name", "alice"])
    cli.main(["lock", "src/app.py"])
    cli.main(["lock", "--resource", "db-migrations"])
    capsys.readouterr()

    assert cli.main(["locks", "--json"]) == 0
    data = _json_out(capsys)
    kinds = {lk["file_path"]: lk["kind"] for lk in data}
    assert kinds["src/app.py"] == "file"
    assert kinds["db-migrations"] == "resource"
    assert all("owner_name" in lk for lk in data)


def test_tasks_json_reports_dependency_block(repo, monkeypatch, capsys):
    monkeypatch.setenv("AGENT_SYNC_ID", "agent-a")
    cli.main(["create-task", "Backend"])
    cli.main(["create-task", "Frontend", "--depends-on", "Backend"])
    capsys.readouterr()

    assert cli.main(["tasks", "--json"]) == 0
    data = _json_out(capsys)
    by_title = {t["title"]: t for t in data}
    assert by_title["Frontend"]["blocked_by_deps"] is True
    assert by_title["Backend"]["blocked_by_deps"] is False
    assert by_title["Frontend"]["depends_on"] == [by_title["Backend"]["id"]]


def test_inbox_json_lists_messages(repo, monkeypatch, capsys):
    monkeypatch.setenv("AGENT_SYNC_ID", "agent-a")
    cli.main(["register", "--name", "alice"])
    monkeypatch.setenv("AGENT_SYNC_ID", "agent-b")
    cli.main(["register", "--name", "bob"])
    cli.main(["send", "--to", "alice", "--message", "hi alice"])
    monkeypatch.setenv("AGENT_SYNC_ID", "agent-a")
    capsys.readouterr()

    assert cli.main(["inbox", "--json"]) == 0
    data = _json_out(capsys)
    assert [m["body"] for m in data] == ["hi alice"]
    assert data[0]["sender_name"] == "bob"
