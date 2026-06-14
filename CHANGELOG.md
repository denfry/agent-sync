# Changelog

All notable changes to this project are documented here. This project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). Entries below the
marker are generated automatically from [Conventional Commits](https://www.conventionalcommits.org/)
by [Python Semantic Release](https://python-semantic-release.readthedocs.io/) — do
not edit them by hand.

<!-- version list -->

## v0.1.2 (2026-06-14)

### Bug Fixes

- **render**: Frame coordination state as untrusted data to block cross-agent prompt injection
  ([`f8f9b46`](https://github.com/denfry/agent-sync/commit/f8f9b46c297edc25af5b8f5a81d841db7e432155))

### Continuous Integration

- Publish to PyPI via Trusted Publishing on release
  ([`f8e473a`](https://github.com/denfry/agent-sync/commit/f8e473a06a37b1150130997662717fb4c64ff071))

### Documentation

- **security**: Document trust/identity model and advisory lock enforcement
  ([`04820b8`](https://github.com/denfry/agent-sync/commit/04820b82414156fd49d51446a7db885013efdc35))


## v0.1.1 (2026-06-14)

### Bug Fixes

- Derive agent identity from session id alone, not cwd
  ([`97ff5d7`](https://github.com/denfry/agent-sync/commit/97ff5d73256bed7e40e2b6f71ddfef64ce75f919))

### Continuous Integration

- Pin GitHub Actions to commit SHAs
  ([`647a47b`](https://github.com/denfry/agent-sync/commit/647a47b76dc4ba374222a75751491f23b9f50990))


## v0.1.0 (2026-06-14)

- Initial public MVP.
- Stdlib-only `agent-sync` CLI backed by a per-repo SQLite database at
  `.claude/coordination/state.sqlite`.
- Agents, tasks (+ files), file locks with TTL, messages, decisions, and an
  activity log.
- Commands: `init`, `register`, `heartbeat`, `status` (`--compact`), `tasks`,
  `create-task`, `claim-task`, `complete-task`, `block-task`, `lock`, `unlock`,
  `locks`, `send`, `inbox`, `read-message`, `decision`, `log`, `gc`, and `hook`.
- Claude Code hooks: `session-start`, `pre-tool-use` (blocks conflicting edits
  with exit code 2), `post-tool-use`, `session-end`.
- `/agent-sync` skill with `SKILL.md`, a cross-platform launcher, and an
  installer.
- Examples (`settings.json`, `CLAUDE.md`, `workflow.md`), tests, and CI for
  Python 3.10–3.12.
