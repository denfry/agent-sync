"""Benchmark-style comparison of the renderers.

This is not a micro-benchmark with tight timing assertions (those are flaky in
CI). It seeds a realistically busy repo, measures how big and how fast each
rendering strategy is, prints a small table for ``pytest -s`` readability, and
asserts only the stable, meaningful property: the *compact* renderer designed
for Claude's context window is materially smaller than the verbose human view
and a naive full JSON dump, while still carrying the essential sections.
"""

from __future__ import annotations

import json
import time

from agent_sync import db, locks, messages, render, tasks


def _seed(conn, *, n_agents=5, n_tasks=10, n_locks=8, n_messages=6):
    for i in range(n_agents):
        db.ensure_agent(conn, f"agent-{i}", name=f"agent{i}", role="developer")
    for i in range(n_tasks):
        task = tasks.create_task(
            conn, f"Task number {i}", description="x" * 40, files=[f"src/f{i}.py"]
        )
        if i % 2 == 0:
            tasks.claim_task(conn, f"agent-{i % n_agents}", task.id)
    for i in range(n_locks):
        locks.acquire_lock(
            conn, f"agent-{i % n_agents}", f"src/f{i}.py", reason="in progress"
        )
    for i in range(n_messages):
        messages.send_message(conn, "agent-0", "all", f"broadcast message {i}")


def _naive_json_dump(conn) -> str:
    """The strawman: dump every relevant row as indented JSON."""
    payload = {
        table: [dict(row) for row in conn.execute(f"SELECT * FROM {table}")]
        for table in ("agents", "tasks", "locks", "messages", "activity")
    }
    return json.dumps(payload, indent=2)


def _measure(label, fn):
    start = time.perf_counter()
    output = fn()
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    return label, output, elapsed_ms


def test_compact_renderer_is_smaller_and_still_complete(conn, capsys):
    _seed(conn)

    results = [
        _measure("compact", lambda: render.render_compact(conn, "agent-0")),
        _measure("verbose", lambda: render.render_status(conn, "agent-0")),
        _measure("raw-json", lambda: _naive_json_dump(conn)),
    ]

    with capsys.disabled():
        print("\nrenderer comparison (5 agents / 10 tasks / 8 locks / 6 messages)")
        print(f"{'renderer':<10}{'chars':>8}{'ms':>10}")
        print("-" * 28)
        for label, output, ms in results:
            print(f"{label:<10}{len(output):>8}{ms:>10.3f}")

    sizes = {label: len(output) for label, output, _ in results}

    # The whole point of the compact renderer: it is the cheapest to inject.
    assert sizes["compact"] < sizes["verbose"]
    assert sizes["compact"] < sizes["raw-json"]

    compact_output = next(out for label, out, _ in results if label == "compact")
    assert compact_output.startswith('<agent-sync-state trust="untrusted">')
    assert "## agent-sync" in compact_output
    assert "active agents" in compact_output
    assert "locks" in compact_output
