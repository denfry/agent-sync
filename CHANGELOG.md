# Changelog

All notable changes to this project are documented here. This project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). Entries below the
marker are generated automatically from [Conventional Commits](https://www.conventionalcommits.org/)
by [Python Semantic Release](https://python-semantic-release.readthedocs.io/) — do
not edit them by hand.

<!-- version list -->

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
