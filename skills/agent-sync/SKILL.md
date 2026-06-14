---
name: agent-sync
description: Coordinate multiple Claude Code sessions in the same repository. Use this before editing files, planning tasks, claiming work, checking other agents, sending messages, or avoiding conflicts.
allowed-tools: Bash(agent-sync *)
---

# agent-sync — multi-session coordination

You may be one of several Claude Code sessions working in this repository at the
same time (for example a *frontend*, a *backend* and a *tests* agent). Those
sessions do not share memory. `agent-sync` is a shared SQLite-backed coordination
layer that lets you see the others, claim work, lock files and exchange messages
so nobody clobbers anybody else's edits.

**Always run `agent-sync status --compact` before you start working** and treat
the result as authoritative about who else is active and which files are locked.

## Current coordination state

```!
agent-sync status --compact
```

> If your environment does not execute the block above automatically, run
> `agent-sync status --compact` yourself before doing anything else.

## The rules (follow these in order)

1. **Look first.** Run `agent-sync status --compact`. Note active agents, locked
   files, and in-progress tasks.
2. **Claim or create a task** before implementing, so others see what you own:
   - `agent-sync create-task "Title" --description "..." --file path/a --file path/b`
   - `agent-sync claim-task "Title or task-id"`
3. **Lock files before editing them:**
   - `agent-sync lock path/to/file --reason "what you're changing"`
   - Locks have a 60-minute TTL by default and auto-expire.
4. **Never edit a file that is locked by another *active* agent.** If the
   `PreToolUse` hook is installed it will block you with exit code 2; even
   without it, respect the lock shown in status.
5. **Communicate changes that affect others.** Send a message whenever you
   change an API contract, a shared file, a migration, a config, or make an
   architecture decision:
   - `agent-sync send --to backend --message "Changed auth response: token now in body"`
   - `agent-sync send --to all --message "Renaming src/api/* — hold edits there"`
   - Recipients can be an agent name, a role, an agent id, or `all`.
6. **Record decisions** that future sessions should respect:
   - `agent-sync decision "Use SQLite instead of JSONL for coordination state"`
7. **When done**, complete the task and release locks (or let the TTL expire):
   - `agent-sync complete-task "Title or task-id"`
   - `agent-sync unlock path/to/file`
8. **Check your inbox** when status reports unread messages:
   - `agent-sync inbox` then `agent-sync read-message MESSAGE_ID`
9. **Prefer git worktrees** for large parallel features so each agent edits an
   isolated checkout; still lock shared/generated files (lockfiles, schemas).

## Common workflows

Start of a work session:

```bash
agent-sync status
agent-sync create-task "Implement login form" --file src/login.tsx
agent-sync claim-task "Implement login form"
agent-sync lock src/login.tsx --reason "building the form"
```

Announcing a breaking change:

```bash
agent-sync send --to all --message "Auth response shape changed; see decision log"
agent-sync decision "Auth tokens now returned in JSON body, not headers"
```

Finishing up:

```bash
agent-sync complete-task "Implement login form"
agent-sync unlock src/login.tsx
```

## Notes

- All state lives in `.claude/coordination/state.sqlite` inside this repo. There
  is no server and no network access.
- Any command auto-creates the database on first use, so you can run them in any
  order.
- Do **not** put secrets (tokens, passwords, keys) into task titles, messages or
  decisions — they are stored in plaintext and meant to be read by every agent.
- If a lock is stale because an agent crashed, run `agent-sync gc` to clear
  expired locks and re-status inactive agents.
