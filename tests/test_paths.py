"""Repo-root resolution, including the git-worktree shared-DB fix."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from agent_sync import paths

requires_git = pytest.mark.skipif(
    shutil.which("git") is None, reason="git is not installed"
)


def _git(args, cwd):
    subprocess.run(
        ["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True
    )


def test_agent_sync_root_overrides_everything(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_SYNC_ROOT", str(tmp_path))
    assert paths.repo_root(tmp_path / "deep" / "nested").resolve() == tmp_path.resolve()


def test_claude_marker_dir_is_a_root(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT_SYNC_ROOT", raising=False)
    (tmp_path / ".claude").mkdir()
    assert paths.repo_root(tmp_path).resolve() == tmp_path.resolve()


@requires_git
def test_linked_worktree_shares_main_worktree_db(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT_SYNC_ROOT", raising=False)
    main = tmp_path / "main"
    main.mkdir()
    _git(["init"], main)
    _git(["config", "user.email", "t@example.com"], main)
    _git(["config", "user.name", "tester"], main)
    _git(["commit", "--allow-empty", "-m", "init"], main)

    linked = tmp_path / "linked"
    _git(["worktree", "add", str(linked)], main)

    # The main worktree resolves to itself; the linked worktree must resolve to
    # the main one so both share a single coordination database.
    assert paths.repo_root(main).resolve() == main.resolve()
    assert paths.repo_root(linked).resolve() == main.resolve()
    assert paths.db_path(linked).resolve() == paths.db_path(main).resolve()
