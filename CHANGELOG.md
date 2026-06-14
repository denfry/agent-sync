# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## 0.1.0 - Unreleased

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
