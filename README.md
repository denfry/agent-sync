# agent-sync

[![Release](https://github.com/denfry/agent-sync/actions/workflows/release.yml/badge.svg)](https://github.com/denfry/agent-sync/actions/workflows/release.yml)
[![Latest release](https://img.shields.io/github/v/release/denfry/agent-sync?sort=semver)](https://github.com/denfry/agent-sync/releases)
[![PyPI](https://img.shields.io/pypi/v/claude-agent-sync.svg)](https://pypi.org/project/claude-agent-sync/)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)

**Coordinate multiple AI coding-agent sessions running in the same repository.**

agent-sync is a shared, local coordination layer for independent CLI
coding-agent sessions — Claude Code, and any other agent or shell. It lets them
*see each other, claim tasks, lock files, exchange messages, log activity, and
avoid edit conflicts* — with no server and no network access. It ships as a small
stdlib-only Python CLI (`agent-sync`), a Claude Code **skill** (`/agent-sync`),
and a set of Claude Code **hooks**, and the agent-agnostic CLI works from any
tool.

> **Works with:** Claude Code (skill + hooks today) · any other CLI agent or
> shell via the `agent-sync` command · Python 3.10+ · macOS · Linux · Windows.

## Contents

[Problem](#the-problem) · [Solution](#the-solution) · [Features](#features) ·
[Install](#install) · [Quickstart](#quickstart) · [Example](#example-three-agents) ·
[Commands](#commands) · [Hooks](#hook-setup) · [Storage](#data-storage) ·
[Safety](#safety-model) · [Limitations](#limitations) · [Roadmap](#roadmap) ·
[Comparison](#comparison) · [FAQ](#faq) · [Contributing](#contributing)

---

## The problem

You open three Claude Code sessions on the same project — one on the frontend,
one on the backend, one writing tests. Each is its own process with its own
context. They have **no idea the others exist**. So:

- Two sessions edit the same file and silently clobber each other.
- One renames an API while another is still coding against the old shape.
- Nobody knows who is doing what, or what was already decided.

`CLAUDE.md` is static. A human relaying messages between windows doesn't scale.
There is no shared operational state.

## The solution

A single SQLite database inside your repo (`.claude/coordination/state.sqlite`)
acts as shared memory for every session, exposed through:

1. **A CLI** — `agent-sync` — for agents (and humans) to read and update state.
2. **A skill** — `/agent-sync` — that teaches Claude *when and how* to coordinate.
3. **Hooks** — a `PreToolUse` hook that **blocks an edit to a file another active
   agent has locked** (exit code 2), `UserPromptSubmit`/`Stop` hooks that **push
   messages from other agents into this session's context** (so they can't be
   ignored), plus `SessionStart`/`PostToolUse`/`SessionEnd` hooks for presence,
   activity logging, and cleanup.

```text
   ┌────────────┐   ┌────────────┐   ┌────────────┐
   │  frontend  │   │  backend   │   │   tests    │   Claude Code sessions
   └─────┬──────┘   └─────┬──────┘   └─────┬──────┘
         │ agent-sync     │ agent-sync     │ agent-sync   (CLI + hooks)
         └────────────────┼────────────────┘
                          ▼
            .claude/coordination/state.sqlite          (shared state, no server)
            agents · tasks · locks · messages · decisions · activity
```

## Features

- **File locks with TTL** — claim a file before editing; locks auto-expire
  after 60 minutes so a crashed session never blocks others forever.
- **Task board** — create / claim / complete / block tasks; a task owned by an
  active agent can't be stolen.
- **Presence** — agents register and heartbeat; stale and offline agents decay
  automatically and stop holding locks.
- **Messaging that reaches busy agents** — send to an agent, a name, a role,
  or `all`. With the hooks installed, messages are **pushed into other sessions'
  context**: injected on every prompt (`UserPromptSubmit`), and a message aimed
  at a *specific* agent even blocks that agent's turn-end (`Stop`) until it reacts
  — so a session deep in a task can't silently ignore it. Still a polled inbox
  (`agent-sync inbox`) for everything else.
- **Decisions & activity log** — record architecture decisions and an audit
  trail of edits.
- **Hooks that actually enforce** — `PreToolUse` fails *closed* on a real lock
  conflict; everything else fails *open* so it never gets in your way.
- **Live operator console** — `agent-sync console` streams who's doing what,
  how agents talk to each other, and lets a human steer in real time (send
  messages/directives, lock files to stop edits, drive the task board).
- **Zero runtime dependencies for the core** — the CLI and hooks are pure
  Python standard library + SQLite. Only the live console needs an extra
  (`pip install "claude-agent-sync[tui]"`).

## Install

```bash
pip install claude-agent-sync
# …or the latest unreleased code straight from source:
pip install "git+https://github.com/denfry/agent-sync"
# …or from a clone of this repo (editable, for development):
pip install -e .

# Optional: the live operator console (`agent-sync console`) needs the TUI extra:
pip install "claude-agent-sync[tui]"     # or: pip install -e ".[tui]"
```

Then install the skill and hooks into a repository:

```bash
# from a clone of this project, run inside your target repo:
python /path/to/agent-sync/skills/agent-sync/scripts/install.py --write-settings
```

Or, working straight from a checkout without installing the package:

```bash
python scripts/install-local.py --write-settings
```

This copies the skill into `<repo>/.claude/skills/agent-sync/`, creates
`<repo>/.claude/coordination/`, and (with `--write-settings`) merges the hooks
into `<repo>/.claude/settings.json`.

## Quickstart

```bash
agent-sync init                                   # create the database (optional; auto-runs)
agent-sync register --name frontend --role "React UI"
agent-sync create-task "Update login UI" --file src/login.tsx
agent-sync claim-task "Update login UI"
agent-sync lock src/login.tsx --reason "editing login page"
agent-sync status                                 # full view
agent-sync status --compact                       # terse Markdown for Claude's context
agent-sync send --to all --message "Login UI task started"
agent-sync decision "Use SQLite for coordination state"
agent-sync complete-task "Update login UI"
agent-sync unlock src/login.tsx
agent-sync gc                                      # drop expired locks, re-status agents
```

## Example: three agents

In real use each agent is a separate Claude Code window. To simulate them in one
shell, set `AGENT_SYNC_ID` per command (in Claude Code, identity is derived
automatically from the session).

```bash
# everyone registers
AGENT_SYNC_ID=frontend agent-sync register --name frontend --role "React UI"
AGENT_SYNC_ID=backend  agent-sync register --name backend  --role "API + DB"
AGENT_SYNC_ID=tests    agent-sync register --name tests    --role "pytest + e2e"

# backend claims a task and locks its files
AGENT_SYNC_ID=backend agent-sync create-task "Add /login endpoint" --file src/api/auth.py
AGENT_SYNC_ID=backend agent-sync claim-task  "Add /login endpoint"
AGENT_SYNC_ID=backend agent-sync lock src/api/auth.py --reason "writing /login"

# tests tries to edit the locked file -> the PreToolUse hook blocks it (exit 2)
printf '{"tool_name":"Edit","tool_input":{"file_path":"src/api/auth.py"}}' \
  | AGENT_SYNC_ID=tests agent-sync hook pre-tool-use
# [agent-sync] BLOCKED: src/api/auth.py is locked by backend ...

# backend announces the contract and finishes
AGENT_SYNC_ID=backend agent-sync send --to all --message "/login returns {token,user} in body"
AGENT_SYNC_ID=backend agent-sync complete-task "Add /login endpoint"
AGENT_SYNC_ID=backend agent-sync unlock src/api/auth.py
```

See [`examples/workflow.md`](examples/workflow.md) for the full narrative.

## Live console

Besides the agent sessions, a human can open a live, interactive view of the
whole coordination layer and steer it as it happens:

```bash
pip install "claude-agent-sync[tui]"   # one-time: the console needs this extra
agent-sync console
```

```text
agent-sync console — live coordination view. Type 'help', 'quit' to leave.
agents (2 active / 2 total):
  backend [active] API + DB
  tests   [active] pytest + e2e
locks (1):
  src/api/auth.py → backend — writing /login
------------------------------------------------------------
12:01:03 act  backend      Edit src/api/auth.py
12:01:04 msg  backend      →all: /login returns {token,user}
12:01:06 who  frontend     joined [active]
12:01:09 lock backend      released src/api/auth.py
operator> directive all "freeze feature work — hotfix on main"
directive -> 3 active agent(s)
operator> lock src/api/auth.py refactor incoming
locked src/api/auth.py (until 2026-06-14T13:01:00+00:00)
```

The feed tails new activity, inter-agent messages, presence changes, and lock
events; the `operator>` prompt lets you act as a first-class participant:

| Operator command | Effect |
| --- | --- |
| `send <to> <msg>` | Message an id/name/role/`all`. A **directed** message is pushed forcefully (the `Stop` hook makes that agent react before it can end its turn). |
| `directive <to> <msg>` | Like `send`, but `directive all` fans out a *directed* copy to every active agent, so each is forced to react this turn. |
| `lock <path> [reason]` | Lock a file — the `PreToolUse` hook blocks other agents' edits to it **immediately**, on their next attempt. The one truly real-time lever. |
| `unlock <path>` | Release a lock (the operator can break anyone's). |
| `task new\|done\|block` | Drive the task board (`task block <ref> :: <reason>`). |
| `decision <text>` | Record a shared decision. |
| `status` · `msgs` | Print a snapshot / recent messages. `help` lists everything. |

The console acts as a reserved `operator` identity: it registers as active so
its locks are enforced and its messages reach agents like any other, but it is
kept out of the "active agents" count so it never looks like a code-editing peer.
When you quit, the operator goes idle and its locks stop holding the repo.
Influence reaches a *running* agent at its next turn (messages) or on its next
edit (locks); there is no mid-tool-call interrupt — see [Limitations](#limitations).

## Commands

| Command | What it does |
| --- | --- |
| `agent-sync init` | Create the database and tables (auto-runs on any command). |
| `agent-sync register --name N [--role R]` | Register / update the current agent. |
| `agent-sync heartbeat` | Mark the current agent active now. |
| `agent-sync status [--compact]` | Show agents, tasks, locks, messages, activity. |
| `agent-sync tasks` | List all tasks. |
| `agent-sync create-task "T" [--description D] [--file P ...] [--priority N]` | Create a task. |
| `agent-sync claim-task T` | Claim a task by id or title. |
| `agent-sync claim-next` | Auto-claim the next available task (highest priority first; reclaims tasks abandoned by crashed sessions). |
| `agent-sync complete-task T` | Mark a task done. |
| `agent-sync block-task T --reason R` | Mark a task blocked. |
| `agent-sync lock FILE [--reason R] [--ttl MIN]` | Lock a file (default TTL 60 min). |
| `agent-sync unlock FILE [--force]` | Release a lock (owner only, unless `--force`). |
| `agent-sync locks [--all]` | List live locks (`--all` includes expired). |
| `agent-sync send --to R --message M` | Send to an id, name, role, or `all`. |
| `agent-sync inbox [--all]` | Show unread (or all) messages addressed to you. |
| `agent-sync read-message ID` | Show a message and mark it read. |
| `agent-sync decision "..."` | Record a shared decision. |
| `agent-sync log --type T --message M [--file P]` | Append an activity entry. |
| `agent-sync gc` | Re-status stale agents and drop expired locks. |
| `agent-sync console [--interval S] [--name N]` | Live operator console: stream activity and steer agents (needs the `tui` extra). |
| `agent-sync hook {session-start,user-prompt-submit,pre-tool-use,post-tool-use,stop,session-end}` | Hook entry points (read JSON from stdin). |

Run `agent-sync --help` or `agent-sync <command> --help` for details.

## Hook setup

Merge the `hooks` block from [`examples/settings.json`](examples/settings.json)
into your repo's `.claude/settings.json` (or run an installer with
`--write-settings`):

| Event | Matcher | Behaviour |
| --- | --- | --- |
| `SessionStart` | (all) | Register/heartbeat the agent; inject compact status into context. |
| `UserPromptSubmit` | (all) | Push any undelivered messages (directed + broadcast) into context for this turn. |
| `PreToolUse` | `Edit\|Write\|MultiEdit` | **Block (exit 2)** if the target file is locked by another active agent. |
| `PostToolUse` | `Edit\|Write\|MultiEdit` | Log the successful edit to the activity feed. |
| `Stop` | (all) | **Block turn-end** (`decision: block`) while a message addressed to *this* agent is still undelivered, so it reacts before stopping. |
| `SessionEnd` | (all) | Mark the agent idle (locks are left to expire by default). |

If `agent-sync` isn't on `PATH`, use
[`examples/settings.skill-path.json`](examples/settings.skill-path.json), which
calls the bundled launcher: `python .claude/skills/agent-sync/scripts/agent-sync ...`.

## Data storage

All state lives in **`.claude/coordination/state.sqlite`** inside the target
repo, created automatically on first use. Tables:

- `agents` — id, name, role, session, cwd, status, current task, timestamps.
- `tasks` + `task_files` — the task board and the files each task touches.
- `locks` — one row per locked path, with owner and `expires_at` (TTL).
- `messages` — sender, recipient (id/name/role/`all`), body, read state.
- `message_deliveries` — per-(message, agent) record of which messages have been
  pushed into which agent's context (so a broadcast reaches each agent once).
- `decisions` — recorded decisions.
- `activity` — an append-only audit log of edits and events.

SQLite runs in WAL mode with a busy timeout, and every write uses a short
`BEGIN IMMEDIATE` transaction, so several Claude Code processes can hit the same
database concurrently. Add `.claude/coordination/` to your `.gitignore`
(this project's `.gitignore` already does).

## Safety model

- **Local-only.** No network calls, no telemetry, no external service. State is a
  file in your repo.
- **Fail open, except for locks.** Hooks tolerate malformed/empty input and never
  crash your session — the *one* deliberate exception is `PreToolUse`, which fails
  **closed** (exit 2) on a genuine lock conflict, which is exactly when you want
  the edit blocked.
- **TTLs prevent deadlock.** Locks expire (60 min default) and locks held by
  stale/offline agents are ignored, so a crashed session can't wedge the repo.
- **Owner-only unlock.** Releasing someone else's lock requires `--force` (the
  operator console always uses force — the human is in charge).
- **Untrusted text stays data.** State injected into an agent's LLM context is
  wrapped in an `<agent-sync-state trust="untrusted">` frame; the live console
  additionally strips control/ANSI bytes from agent-authored values before
  printing them, so a hostile name or message can't hijack your terminal.
- **No secrets.** Tasks, messages, and decisions are plaintext shared state — do
  not put tokens, passwords, or keys in them. See [SECURITY.md](SECURITY.md).

## Limitations

- Coordination is **advisory**. The `PreToolUse` hook enforces locks for
  `Edit`/`Write`/`MultiEdit`, but a shell command (`sed`, `>`) can still bypass
  it. Locks are a cooperation tool, not OS-level file locking.
- Identity is auto-detected per Claude Code session: it's `AGENT_SYNC_ID` if set,
  else a hash of the session id (from a hook payload or the
  `CLAUDE_CODE_SESSION_ID` env var Claude Code exports into every shell), else a
  per-repo local id. Because hooks and the skill both key off the same session
  id, they resolve to the same agent — so you never get blocked from editing a
  file *you* locked. Outside Claude Code with none of those set, all sessions in
  a repo share the local id and look like one agent.
- Single-repo scope. There's no cross-repo or cross-machine coordination.
- Messaging is **pushed** by the `UserPromptSubmit`/`Stop` hooks (so a message
  lands in another agent's context at its next prompt or turn-end), but it is not
  a real-time bus: there's no mid-tool-call interrupt, and locks/tasks/presence
  are still observed by polling `status`. Without the hooks installed (e.g. a bare
  CLI agent), messaging falls back to a polled `inbox`.

## Roadmap

- [ ] An MCP server exposing the same state as tools/resources (no hooks needed).
- [ ] First-class adapters for other CLI agents (Cursor, Codex CLI, Gemini CLI).
- [ ] Richer presence (per-agent current file, progress %).
- [ ] Optional auto-release of locks on `SessionEnd`.
- [x] A live console for watching and steering agents — shipped as
  [`agent-sync console`](#live-console).
- [ ] Lock leases with renewal and configurable policies.

## Comparison

| Approach | Shared live state? | Blocks conflicting edits? | Setup | Best for |
| --- | --- | --- | --- | --- |
| **Plain `CLAUDE.md`** | No (static text) | No | Trivial | Conventions, not coordination |
| **Human relays chat** | In your head | No | None | 2 windows, low traffic |
| **Git worktrees** | No (isolated trees) | Avoids conflicts by isolation | Medium | Big independent features |
| **agent-sync** | Yes (SQLite) | Yes (PreToolUse hook) | One install | Several agents, one repo |
| **Future MCP version** | Yes | Yes (tool-mediated) | MCP config | Same, server-mediated |

`agent-sync` composes with git worktrees: use worktrees to isolate big
features and `agent-sync` to lock the shared/generated files they still touch.

## FAQ

**Do I need a server or database engine?** No. It's a single SQLite file managed
by Python's stdlib `sqlite3`. Nothing to run.

**What if two agents start at the same time?** Writes use `BEGIN IMMEDIATE` and a
busy timeout, so they serialize. Claiming a task or lock is atomic; the loser
gets a clear conflict error.

**A session crashed and left a lock.** Locks expire after their TTL, and locks
owned by stale/offline agents are ignored immediately. Run `agent-sync gc` to
clean up now.

**Does this work with agents other than Claude Code?** Yes. The `agent-sync` CLI
works anywhere Python runs, so any CLI agent or shell can read and update the
shared state. The bundled skill and hooks are Claude Code-specific today (other
agents are on the [roadmap](#roadmap)), but the coordination database isn't tied
to any one tool.

**Will the hook block my own edits?** No — you can always edit files *you* have
locked. It only blocks edits to files locked by *other active* agents.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for dev setup, running tests, coding
conventions, and how to add commands and hooks. Security policy:
[SECURITY.md](SECURITY.md). Changes are tracked in [CHANGELOG.md](CHANGELOG.md).

## Maintainer

Built and maintained by [@denfry](https://github.com/denfry). Issues and pull
requests welcome — start with [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE).
