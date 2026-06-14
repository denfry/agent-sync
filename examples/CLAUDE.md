# Project coordination (agent-sync)

> Drop this section into your repository's `CLAUDE.md` so every Claude Code
> session in this repo coordinates through `agent-sync`.

This repository may have **multiple Claude Code sessions running at once**. They
do not share memory. Use the `agent-sync` CLI (and the `/agent-sync` skill) to
coordinate.

## Before you touch anything

Run:

```bash
agent-sync status --compact
```

This shows other active agents, locked files, in-progress tasks, and unread
messages. Treat it as the source of truth.

## While working

- **Claim a task** before implementing: `agent-sync claim-task "..."`
  (or `create-task` first if it doesn't exist).
- **Lock files** before editing them: `agent-sync lock path --reason "..."`.
- **Do not edit a file locked by another active agent.** The `PreToolUse` hook
  will block you (exit code 2); respect it.
- **Announce shared changes** — API contracts, migrations, configs, renames:
  `agent-sync send --to all --message "..."`.
- **Record decisions**: `agent-sync decision "..."`.

## When finished

```bash
agent-sync complete-task "..."
agent-sync unlock path        # or just let the 60-minute TTL expire
```

## Hygiene

- Check your inbox when status shows unread messages: `agent-sync inbox`.
- Never put secrets into tasks, messages, or decisions — they are plaintext.
- For big parallel features, prefer separate **git worktrees** per agent.
