#!/usr/bin/env python3
"""Install the agent-sync skill into the current repository.

Copies ``skills/agent-sync`` into ``<repo>/.claude/skills/agent-sync``, creates
the coordination directory, and prints the hook configuration to add. User
settings are never modified unless ``--write-settings`` is passed, and even then
existing hooks are merged rather than replaced.

Usage::

    python skills/agent-sync/scripts/install.py [--target DIR] [--write-settings]
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

# This file lives at skills/agent-sync/scripts/install.py; the skill root is two
# levels up.
SKILL_ROOT = Path(__file__).resolve().parents[1]

HOOK_EVENTS = {
    "SessionStart": ("", "agent-sync hook session-start"),
    "UserPromptSubmit": ("", "agent-sync hook user-prompt-submit"),
    "PreToolUse": ("Edit|Write|MultiEdit", "agent-sync hook pre-tool-use"),
    "PostToolUse": ("Edit|Write|MultiEdit", "agent-sync hook post-tool-use"),
    "Stop": ("", "agent-sync hook stop"),
    "SessionEnd": ("", "agent-sync hook session-end"),
}


def _hook_block() -> dict:
    hooks: dict = {}
    for event, (matcher, command) in HOOK_EVENTS.items():
        hooks[event] = [
            {"matcher": matcher, "hooks": [{"type": "command", "command": command}]}
        ]
    return {"hooks": hooks}


def install_skill(target_repo: Path) -> Path:
    """Copy the skill into ``<target_repo>/.claude/skills/agent-sync``."""
    dest = target_repo / ".claude" / "skills" / "agent-sync"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(SKILL_ROOT, dest)
    (target_repo / ".claude" / "coordination").mkdir(parents=True, exist_ok=True)
    return dest


def merge_settings(target_repo: Path) -> Path:
    """Merge agent-sync hooks into ``.claude/settings.json`` without clobbering."""
    settings_path = target_repo / ".claude" / "settings.json"
    settings: dict = {}
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError):
            print(
                f"warning: {settings_path} is not valid JSON; leaving it untouched.",
                file=sys.stderr,
            )
            return settings_path

    hooks = settings.setdefault("hooks", {})
    for event, (matcher, command) in HOOK_EVENTS.items():
        entries = hooks.setdefault(event, [])
        already = any(
            hook.get("command") == command
            for entry in entries
            for hook in entry.get("hooks", [])
        )
        if not already:
            entries.append(
                {"matcher": matcher, "hooks": [{"type": "command", "command": command}]}
            )

    settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    return settings_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target",
        default=".",
        help="Repository to install into (default: current directory)",
    )
    parser.add_argument(
        "--write-settings",
        action="store_true",
        help="Merge the hook configuration into .claude/settings.json",
    )
    args = parser.parse_args(argv)

    target = Path(args.target).resolve()
    dest = install_skill(target)
    print(f"Installed skill -> {dest}")
    print(f"Created coordination dir -> {target / '.claude' / 'coordination'}")

    if args.write_settings:
        path = merge_settings(target)
        print(f"Merged hooks into -> {path}")
    else:
        print("\nAdd these hooks to .claude/settings.json (or re-run with --write-settings):\n")
        print(json.dumps(_hook_block(), indent=2))

    print("\nNext: install agent-sync (`pip install -e .` from a clone) and run `agent-sync status`.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
