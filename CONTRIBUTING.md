# Contributing to claude-agent-sync

Thanks for your interest! This is a small, dependency-free project and we'd like
to keep it that way. The runtime uses **only the Python standard library**.

## Dev setup

Requires Python 3.10+.

```bash
git clone https://github.com/denfry/agent-sync
cd claude-agent-sync
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[dev]"                               # editable install + pytest
```

You can also work without installing: a bare `pytest` works because
`pyproject.toml` sets `pythonpath = ["src"]`, and the scripts in `scripts/` add
`src/` to `sys.path` themselves.

## Running tests

```bash
pytest                 # full suite
pytest -q              # quiet
pytest -s              # show the benchmark comparison table
pytest tests/test_locks.py -k expired      # a subset
```

A quick end-to-end smoke check of the whole command flow:

```bash
python scripts/dev-smoke-test.py
```

Tests must:

- run against a temporary directory (use the `repo`/`conn` fixtures);
- never touch the real home directory or working tree;
- never require network access or Claude Code to be installed.

## Project layout

```text
src/agent_sync/
  paths.py      filesystem layout + repo discovery (no internal deps)
  errors.py     typed errors + their exit codes
  models.py     dataclasses + status constants
  db.py         connection, schema, time helpers, agent records, staleness
  locks.py      file locks with TTL
  tasks.py      task lifecycle
  messages.py   messages, decisions, activity log
  git_utils.py  best-effort git info (never raises)
  render.py     verbose + compact status renderers
  hooks.py      Claude Code hook handlers
  cli.py        argparse parsing + command handlers (thin)
```

Keep the **DB layer separate from CLI parsing**: rules and persistence live in
`db.py` / `locks.py` / `tasks.py` / `messages.py`; `cli.py` only parses arguments
and prints. New behaviour should be unit-testable by calling a domain function
with a `conn`, without going through `argparse`.

## Coding conventions

- Standard library only for runtime code. Dev-only deps (pytest) go in the
  `dev` optional-dependencies group.
- Timestamps are UTC ISO-8601 strings via `db.now_iso()`; parse with
  `db.parse_iso()`.
- Every write goes through `with db.transaction(conn): ...`.
- Hooks **fail open** (return 0) on unexpected input — except a real lock
  conflict in `pre-tool-use`, which **fails closed** (returns 2).
- Type hints throughout; `from __future__ import annotations` at the top.
- Optional linting with `ruff` (config in `pyproject.toml`): `ruff check .`.

## How to add a command

1. Add a domain function in the appropriate module (e.g. `tasks.py`) that takes
   a `sqlite3.Connection` and returns a dataclass — put the logic and the test
   here.
2. Add a `cmd_<name>(args)` handler in `cli.py` that opens a connection, calls
   the domain function, prints a human-readable result, and returns an exit code.
3. Register a subparser for it in `build_parser()` and wire `set_defaults(func=...)`.
4. Add tests (domain-level in the module's test file, plus a CLI smoke test in
   `tests/test_cli_status.py` if useful).
5. Update the commands table in `README.md` and add a `CHANGELOG.md` entry.

## How to add a hook

1. Add a `hook_<event>(payload, *, conn=None, ...)` function in `hooks.py` that
   returns an exit code. Read identity from `payload`/env; tolerate empty input.
2. Register it in the `HANDLERS` dict so `agent-sync hook <event>` dispatches to
   it.
3. Add it to `examples/settings.json` (and the skill installer's `HOOK_EVENTS`).
4. Add tests in `tests/test_hooks_*.py`, passing the payload and a `conn`
   directly (no subprocess).

## Release process

1. Update `CHANGELOG.md` (move items from *Unreleased* to the new version).
2. Bump the version in `pyproject.toml` and `src/agent_sync/__init__.py`.
3. Ensure `pytest` and CI are green on all supported Python versions.
4. Tag the release (`git tag vX.Y.Z`) and build:
   `python -m build` then `twine upload dist/*` (maintainers).

## Reporting bugs / proposing features

Open an issue with a minimal reproduction (the `agent-sync` commands you ran and
what you expected). For security concerns, see [SECURITY.md](SECURITY.md).
