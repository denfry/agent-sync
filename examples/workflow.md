# Example workflow: three agents on one repo

This walkthrough shows three Claude Code sessions — **frontend**, **backend**,
and **tests** — collaborating on the same repository without stepping on each
other. Commands are shown with the acting agent in the prompt. In real use each
session is a separate Claude Code window; here we set `AGENT_SYNC_ID` to simulate
them in one shell.

## 0. One-time setup

```bash
pip install claude-agent-sync
python skills/agent-sync/scripts/install.py --write-settings   # installs skill + hooks
agent-sync init
```

## 1. Each agent registers

```bash
# frontend session
AGENT_SYNC_ID=frontend agent-sync register --name frontend --role "React UI"

# backend session
AGENT_SYNC_ID=backend  agent-sync register --name backend  --role "API + DB"

# tests session
AGENT_SYNC_ID=tests    agent-sync register --name tests    --role "pytest + e2e"
```

Everyone can now see everyone:

```bash
agent-sync status --compact
# ## agent-sync
# you: frontend | active agents: 3 | open tasks: 0 | locks: 0 | unread: 0
# - other active: backend (API + DB), tests (pytest + e2e)
```

## 2. Plan and claim work

```bash
# backend creates and claims a task spanning two files
AGENT_SYNC_ID=backend agent-sync create-task "Add /login endpoint" \
    --file src/api/auth.py --file src/api/routes.py
AGENT_SYNC_ID=backend agent-sync claim-task "Add /login endpoint"

# frontend claims the UI side
AGENT_SYNC_ID=frontend agent-sync create-task "Build login form" --file src/login.tsx
AGENT_SYNC_ID=frontend agent-sync claim-task "Build login form"
```

## 3. Lock before editing

```bash
AGENT_SYNC_ID=backend  agent-sync lock src/api/auth.py --reason "writing /login"
AGENT_SYNC_ID=frontend agent-sync lock src/login.tsx   --reason "building form"
```

Now if the **tests** agent tries to edit `src/api/auth.py`, the `PreToolUse`
hook blocks it:

```text
[agent-sync] BLOCKED: src/api/auth.py is locked by backend [backend] until ...
Coordinate via `agent-sync send --to backend --message "..."` or wait.
```

## 4. Communicate a contract change

```bash
AGENT_SYNC_ID=backend agent-sync send --to all \
    --message "/login returns {token, user} in JSON body (200) or 401"
AGENT_SYNC_ID=backend agent-sync decision \
    "Auth token is returned in the response body, not a header"
```

The frontend agent sees it:

```bash
AGENT_SYNC_ID=frontend agent-sync inbox
AGENT_SYNC_ID=frontend agent-sync read-message msg-xxxxxxxx
```

## 5. Finish and release

```bash
AGENT_SYNC_ID=backend agent-sync complete-task "Add /login endpoint"
AGENT_SYNC_ID=backend agent-sync unlock src/api/auth.py

AGENT_SYNC_ID=frontend agent-sync complete-task "Build login form"
AGENT_SYNC_ID=frontend agent-sync unlock src/login.tsx
```

## 6. Housekeeping

If a session crashed and left a lock behind, any agent can clean up:

```bash
agent-sync gc      # drops expired locks, re-statuses stale agents
```
