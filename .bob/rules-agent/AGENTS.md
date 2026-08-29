# AGENTS.md — Coding Rules

This file provides guidance to agents when working with code in this repository.

## Non-obvious coding rules

- **MCP import**: `from mcp.server.mcpserver import MCPServer` — `FastMCP` no longer exists in mcp 2.x.
- **Relative import resolution in indexer**: edges from `from .X import Y` must store the resolved absolute module id (e.g. `requests.X`), not `.X`. The entire constraint check pipeline breaks if this is wrong.
- **No external services**: every component must work offline — no embeddings, no network calls, no Docker.
- **Doctest side effect**: `target/requests` pytest runs `--doctest-modules` automatically; any source file doctest failures count as test failures.
- **Capability deduplication logic**: when checking duplicates, a function modifying itself in its own module is an edit — do not flag it as a duplicate.
- **TF-IDF noise tokens**: skip `get`, `keys`, `items`, `update`, `content`, `values`, `__*__` names — they are container-protocol noise, not reusable capabilities.
- **Diff parsing fallback**: added hunks rarely form a complete parseable module — fall back to regex when `ast.parse` raises.
- **SQLite only**: one `.db` file, no ORM. `*.db` files are gitignored.
- **Subagents for capability enrichment**: one subagent per module, run in parallel, each with isolated context. Do not enrich all modules in one agent call.
- **CLI must work without MCP**: every tool must be exercisable via `archmemory.cli` so a broken MCP connection can't kill a demo.
