#!/usr/bin/env python3
"""Run the full acceptance command flow against a throwaway repo.

This is a fast manual sanity check that mirrors the acceptance criteria in the
README. It creates a temporary directory, points agent-sync at it via
``AGENT_SYNC_ROOT``, runs every command, exercises a lock conflict between two
simulated agents through the ``pre-tool-use`` hook, and reports pass/fail.

Usage::

    python scripts/dev-smoke-test.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from agent_sync import cli, hooks  # noqa: E402

FAILURES: list[str] = []


def check(label: str, got, expected) -> None:
    ok = got == expected
    print(f"  [{'ok' if ok else 'FAIL'}] {label} (got {got!r}, expected {expected!r})")
    if not ok:
        FAILURES.append(label)


def run_as(agent_id: str, args: list[str]) -> int:
    os.environ["AGENT_SYNC_ID"] = agent_id
    return cli.main(args)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="agent-sync-smoke-"))
    os.environ["AGENT_SYNC_ROOT"] = str(tmp)
    print(f"Using throwaway repo: {tmp}")

    print("\n# Single-agent acceptance flow (as 'frontend')")
    check("init", run_as("agent-frontend", ["init"]), 0)
    check("register", run_as("agent-frontend", ["register", "--name", "frontend", "--role", "React UI"]), 0)
    check("create-task", run_as("agent-frontend", ["create-task", "Update login UI", "--file", "src/login.tsx"]), 0)
    check("claim-task", run_as("agent-frontend", ["claim-task", "Update login UI"]), 0)
    check("lock", run_as("agent-frontend", ["lock", "src/login.tsx", "--reason", "editing"]), 0)
    check("status", run_as("agent-frontend", ["status"]), 0)
    check("status --compact", run_as("agent-frontend", ["status", "--compact"]), 0)
    check("send", run_as("agent-frontend", ["send", "--to", "all", "--message", "started"]), 0)
    check("inbox", run_as("agent-frontend", ["inbox"]), 0)
    check("decision", run_as("agent-frontend", ["decision", "Use SQLite"]), 0)

    print("\n# Lock conflict via pre-tool-use hook (as 'backend')")
    os.environ["AGENT_SYNC_ID"] = "agent-backend"
    payload = {
        "session_id": "s2",
        "cwd": str(tmp),
        "tool_name": "Edit",
        "tool_input": {"file_path": "src/login.tsx"},
    }
    blocked = hooks.hook_pre_tool_use(payload)
    check("pre-tool-use blocks locked file", blocked, 2)
    print("  (payload was: " + json.dumps(payload) + ")")

    print("\n# Cleanup commands (as 'frontend')")
    check("complete-task", run_as("agent-frontend", ["complete-task", "Update login UI"]), 0)
    check("unlock", run_as("agent-frontend", ["unlock", "src/login.tsx"]), 0)
    check("gc", run_as("agent-frontend", ["gc"]), 0)

    print()
    if FAILURES:
        print(f"SMOKE TEST FAILED: {len(FAILURES)} check(s): {', '.join(FAILURES)}")
        return 1
    print("SMOKE TEST PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
