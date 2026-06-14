# Security Policy

## Scope and threat model

`agent-sync` is a **local coordination tool**. It stores project
coordination metadata in a SQLite database inside your repository at
`.claude/coordination/state.sqlite`.

- **No network calls.** The tool never connects to any server, sends telemetry,
  or fetches anything. All state is a local file.
- **No external service or daemon.** There is nothing listening; the CLI reads
  and writes the SQLite file directly.
- **No credentials handled.** The tool does not read, store, or transmit secrets.

## Trust and identity model

agent-sync coordinates **cooperating** sessions run by a single trusting user on
one machine. It is **not** a security boundary between mutually distrusting
parties, and it does **not** authenticate agents.

- **Identity is asserted, not verified.** An agent's id comes from the
  `AGENT_SYNC_ID` environment variable, else a hash of the Claude Code session
  id, else a per-repo file. Any local process can set `AGENT_SYNC_ID` to any
  value and thereby *act as* any agent.
- **Ownership checks prevent accidents, not attacks.** Task ownership, lock
  ownership, and owner-only unlock stop two cooperating sessions from clobbering
  each other. They do **not** stop a process that deliberately spoofs an id,
  passes `--force`, or writes the SQLite file directly. Treat them as
  collision-avoidance, not access control.
- **The database is plaintext.** Anyone who can read
  `.claude/coordination/state.sqlite` can read all coordination state; anyone who
  can write it can forge any record. Coordination text from other agents is also
  injected into your session's context, so it is wrapped in an explicit
  untrusted-data frame — but treat it as untrusted regardless.

If you need isolation between untrusted parties, run them as separate OS users
with separate repositories; agent-sync does not provide that.

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

The `user-prompt-submit` and `stop` hooks deliberately **inject messages written
by other agents into your session's context** (the `stop` hook also blocks
turn-end while a directed message is undelivered — an intentional block, not a
failure; it still fails open on any error). That content is untrusted: it is
authored by other agents/humans. Every pushed value is wrapped in an
`<agent-sync-state trust="untrusted">` frame with newlines and frame delimiters
neutralised, so a malicious message cannot forge scaffolding or break out of the
data block — but a session should still treat message bodies as **data, not
instructions**. This is the same trust boundary that applies to `status` output.

## Lock enforcement is advisory

The `pre-tool-use` hook blocks edits **only** for the `Edit`, `Write` and
`MultiEdit` tools (its matcher). A lock is a cooperation signal, not an OS-level
file lock:

- A shell command (`Bash` running `sed -i`, `>`, `cp`, `git checkout`, …) can
  modify a locked file without tripping the hook.
- A tool whose name is not in the matcher is not checked.
- Not installing the hook, or removing it, disables enforcement entirely.

Locks reliably prevent *honest* collisions between cooperating agents; they do
not contain a session that ignores them.

## Reporting a vulnerability

If you discover a security issue, please **do not open a public issue**. Instead:

1. Email the maintainers (see repository metadata / `pyproject.toml`), or use
   GitHub's **private vulnerability reporting** ("Report a vulnerability") on the
   repository's Security tab.
2. Include a description, affected version, and a minimal reproduction.

We aim to acknowledge reports within a few days and will coordinate a fix and
disclosure timeline with you. Thank you for helping keep users safe.
