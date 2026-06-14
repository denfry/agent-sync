# Security Policy

## Scope and threat model

`claude-agent-sync` is a **local coordination tool**. It stores project
coordination metadata in a SQLite database inside your repository at
`.claude/coordination/state.sqlite`.

- **No network calls.** The tool never connects to any server, sends telemetry,
  or fetches anything. All state is a local file.
- **No external service or daemon.** There is nothing listening; the CLI reads
  and writes the SQLite file directly.
- **No credentials handled.** The tool does not read, store, or transmit secrets.

## Data sensitivity

The coordination database stores **plaintext, shared operational state** that is
intended to be readable by every agent and human working in the repo: agent
names/roles, task titles and descriptions, locked file paths, messages,
decisions, and an activity log of edited file paths.

**Do not put secrets into agent-sync.** Specifically, never place the following
into task titles/descriptions, messages, decisions, or log entries:

- API tokens, passwords, private keys, or session cookies;
- connection strings or anything you would not commit to the repo.

Treat the contents the same way you would treat a file checked into version
control. We recommend adding `.claude/coordination/` to `.gitignore` (this
project's `.gitignore` already does) so the database itself is not committed.

## Hook safety

The Claude Code hooks are designed to **fail open**: malformed or empty input is
ignored and never crashes your session. The single intentional exception is the
`pre-tool-use` hook, which **fails closed** (exit code 2) when it detects that a
file is locked by another active agent — that is the desired behaviour, blocking
the conflicting edit. Because hooks execute shell commands configured in your
`.claude/settings.json`, only enable hook commands you trust and have reviewed.

## Reporting a vulnerability

If you discover a security issue, please **do not open a public issue**. Instead:

1. Email the maintainers (see repository metadata / `pyproject.toml`), or use
   GitHub's **private vulnerability reporting** ("Report a vulnerability") on the
   repository's Security tab.
2. Include a description, affected version, and a minimal reproduction.

We aim to acknowledge reports within a few days and will coordinate a fix and
disclosure timeline with you. Thank you for helping keep users safe.
