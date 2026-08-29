# AGENTS.md

This file provides guidance to agents when working with code in this repository.

## Project

Python package `archmemory` — a persistent memory layer for AI coding agents, exposed over MCP.
**Nothing has been built yet.** All source lives in `archmemory/` (to be created). The repo root contains only specs and a gitignored reference codebase.

## Target reference codebase

`target/requests/` — gitignored, pinned at `5460f467b02e49471c0fd6cfc9ca0adab6351f98`.
Source root: `target/requests/src/requests/`. Tests in `target/requests/tests/`.

## Commands (target/requests — for validation runs)

```bash
# From target/requests/
python -m pytest tests                              # all tests
python -m pytest tests/test_utils.py::test_name    # single test
python -m pytest tests -k "keyword"                 # filter tests
ruff check src/requests tests                       # lint
ruff format src/requests tests                      # format
pyright                                             # type-check
```

**Important:** `pytest` in `target/requests` runs with `--doctest-modules` by default (set in `pyproject.toml`). Doctests in source files are executed automatically.

## Architecture: archmemory components to build

| File | Role |
|---|---|
| `archmemory/store.py` | SQLite persistence — nodes, edges, capabilities, constraints, decisions |
| `archmemory/indexer.py` | AST-walk `requests` source, two-pass: declarations then imports/calls |
| `archmemory/query.py` | TF-IDF capability search (no embeddings, no external services) |
| `archmemory/capability.py` | Bob subagents per module, populate `capabilities` table |
| `archmemory/seed.py` | Seed constraints (LAYER-01/02/03) and decisions (PROTECT-01/02) |
| `archmemory/validate.py` | Diff validator — deterministic, returns `ok` or `blocked` |
| `archmemory/server.py` | MCP server via stdio |
| `archmemory/cli.py` | CLI: `index`, `find`, `validate`, `stats` |

## Critical implementation details

### Indexer
- Relative imports **must** resolve to absolute module IDs. `from .utils import x` inside `requests.adapters` → edge to `requests.utils`, not `.utils`. The constraint checker depends entirely on this.
- Acceptance numbers: ~19 modules, ~228 functions, ~52 classes, ~729 edges.

### MCP server
- Installed `mcp` package is **2.x**: `FastMCP` was renamed. Use `from mcp.server.mcpserver import MCPServer`.

### Query (TF-IDF)
- Split `snake_case` / `camelCase` tokens for search.
- Skip dunders and container-protocol names (`get`, `keys`, `items`, `update`, `content`, etc.) — matching against them produces confident nonsense.

### Validator
- Added lines from a diff hunk rarely parse as a complete module; fall back to regex scan when `ast.parse` fails.
- A function redefining itself in its own module is an edit, not a duplicate capability.

### Constraints seeded (LAYER checks against requests layering)
- `requests.adapters` must not import `requests.api` or `requests.sessions` (LAYER-01)
- `requests.utils` must not import `requests.api`, `requests.sessions`, or `requests.adapters` (LAYER-02)
- `requests.models` must not import `requests.api` or `requests.sessions` (LAYER-03)
- `compat.py` — do not modify without owner sign-off (PROTECT-01)
- `packages.py` — backwards-compatible aliases; do not refactor (PROTECT-02)

## Code style (for archmemory source)

- Linter: **ruff** (same as the reference codebase); formatter: ruff with black-compatible settings (double quotes, spaces).
- Type hints required; use `pyright` in strict mode.
- No external services — SQLite only, no vector DB, no Docker.

## Definition of done (key acceptance tests)

```bash
python -m archmemory.cli index target/requests/src
# find_capability("check if an IP is in a CIDR network") → returns address_in_network
# validate_diff with duplicate function → blocked
# validate_diff adding api import to adapters → blocked citing LAYER-01
# validate_diff touching compat.py → blocked citing PROTECT-01
# MCP server completes initialize + tools/list handshake over stdio
```
