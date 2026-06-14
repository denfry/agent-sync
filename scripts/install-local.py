#!/usr/bin/env python3
"""Install agent-sync into the current repo for local development/testing.

This does three things:

1. Copies the skill into ``<cwd>/.claude/skills/agent-sync``.
2. Initialises the coordination database (``.claude/coordination/state.sqlite``).
3. Prints next steps, including the hook configuration.

It runs straight from a source checkout — no ``pip install`` required — by adding
the project's ``src`` directory to ``sys.path``.

Usage::

    python scripts/install-local.py [--write-settings]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
SKILL_SCRIPTS = PROJECT_ROOT / "skills" / "agent-sync" / "scripts"

# Make both the package and the skill installer importable from the checkout.
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SKILL_SCRIPTS))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write-settings",
        action="store_true",
        help="Merge hooks into .claude/settings.json",
    )
    args = parser.parse_args(argv)

    target = Path.cwd()

    # 1. install the skill (reuse the skill's own installer logic)
    import install as skill_install  # type: ignore[import-not-found]

    dest = skill_install.install_skill(target)
    print(f"[1/3] Installed skill -> {dest}")

    # 2. initialise the database
    from agent_sync.cli import main as cli_main

    cli_main(["init"])

    # 3. settings + next steps
    if args.write_settings:
        path = skill_install.merge_settings(target)
        print(f"[3/3] Merged hooks into -> {path}")
    else:
        print("[3/3] Hooks not written. See examples/settings.json or run with --write-settings.")

    print("\nNext steps:")
    print("  pip install -e .            # put `agent-sync` on your PATH")
    print("  agent-sync register --name dev --role 'whatever you do'")
    print("  agent-sync status")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
