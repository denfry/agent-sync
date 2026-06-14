# Changelog

All notable changes to this project are documented here. This project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). Entries below the
marker are generated automatically from [Conventional Commits](https://www.conventionalcommits.org/)
by [Python Semantic Release](https://python-semantic-release.readthedocs.io/) — do
not edit them by hand.

<!-- version list -->

## v0.2.0 (2026-06-14)

### Continuous Integration

- Bump actions/checkout from 4.3.1 to 6.0.3 ([#2](https://github.com/denfry/agent-sync/pull/2),
  [`9f8ecb2`](https://github.com/denfry/agent-sync/commit/9f8ecb23015b564cfea13263ad8e3917339f0d39))

- Bump actions/setup-python from 5.6.0 to 6.2.0 ([#3](https://github.com/denfry/agent-sync/pull/3),
  [`269e7b6`](https://github.com/denfry/agent-sync/commit/269e7b6c66d05e6877f02f780fcf68508e4e09ca))

- Bump amannn/action-semantic-pull-request from 5.5.3 to 6.1.1
  ([#1](https://github.com/denfry/agent-sync/pull/1),
  [`0c6ef41`](https://github.com/denfry/agent-sync/commit/0c6ef41cfe7b8e722767202af1cd9c3f9a4a4bb9))

### Features

- **console**: Live operator console for watching and steering agents
  ([`74bdd4c`](https://github.com/denfry/agent-sync/commit/74bdd4cdac25e4c8f64fbb846589d8295b2e76e9))


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
