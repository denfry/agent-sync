"""End-to-end CLI checks driven through ``cli.main``."""

from __future__ import annotations

from agent_sync import cli


def test_status_lists_agent(repo, monkeypatch, capsys):
    monkeypatch.setenv("AGENT_SYNC_ID", "agent-x")
    assert cli.main(["register", "--name", "frontend", "--role", "React UI"]) == 0
    capsys.readouterr()
    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "frontend" in out
    assert "## Agents" in out
    assert "## Locks" in out


def test_compact_status_is_markdown_block(repo, monkeypatch, capsys):
    monkeypatch.setenv("AGENT_SYNC_ID", "agent-x")
    cli.main(["register", "--name", "frontend"])
    capsys.readouterr()
    assert cli.main(["status", "--compact"]) == 0
    out = capsys.readouterr().out
    assert out.startswith('<agent-sync-state trust="untrusted">')
    assert "## agent-sync" in out
    assert "active agents" in out


def test_full_acceptance_flow(repo, monkeypatch, capsys):
    monkeypatch.setenv("AGENT_SYNC_ID", "agent-frontend")
    assert cli.main(["init"]) == 0
    assert cli.main(["register", "--name", "frontend", "--role", "React UI"]) == 0
    assert cli.main(["create-task", "Update login UI", "--file", "src/login.tsx"]) == 0
    assert cli.main(["claim-task", "Update login UI"]) == 0
    assert cli.main(["lock", "src/login.tsx", "--reason", "editing"]) == 0
    assert cli.main(["send", "--to", "all", "--message", "started"]) == 0
    assert cli.main(["decision", "Use SQLite"]) == 0
    assert cli.main(["complete-task", "Update login UI"]) == 0
    assert cli.main(["unlock", "src/login.tsx"]) == 0
    assert cli.main(["gc"]) == 0


def test_lock_conflict_returns_exit_code_2(repo, monkeypatch):
    monkeypatch.setenv("AGENT_SYNC_ID", "agent-a")
    cli.main(["register", "--name", "a"])
    assert cli.main(["lock", "src/x.ts"]) == 0
    monkeypatch.setenv("AGENT_SYNC_ID", "agent-b")
    cli.main(["register", "--name", "b"])
    assert cli.main(["lock", "src/x.ts"]) == 2


def test_no_command_prints_help(repo, capsys):
    assert cli.main([]) == 1
    out = capsys.readouterr().out
    assert "usage:" in out.lower()


def test_whoami_reports_identity_and_source(repo, monkeypatch, capsys):
    monkeypatch.setenv("AGENT_SYNC_ID", "agent-x")
    cli.main(["register", "--name", "frontend", "--role", "React UI"])
    capsys.readouterr()
    assert cli.main(["whoami"]) == 0
    out = capsys.readouterr().out
    assert "agent-x" in out
    assert "AGENT_SYNC_ID" in out  # tells the agent it is acting under an explicit id
    assert "frontend" in out


def test_whoami_json_when_unregistered(repo, monkeypatch, capsys):
    import json

    monkeypatch.setenv("AGENT_SYNC_ID", "agent-x")
    assert cli.main(["whoami", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["id"] == "agent-x"
    assert data["registered"] is False
    assert "AGENT_SYNC_ID" in data["resolved_via"]
