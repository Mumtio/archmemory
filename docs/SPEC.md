# ArchMemory — Build Specification

A persistent software memory layer that gives AI coding agents verified
knowledge of a codebase's architecture, existing capabilities, constraints, and
engineering decisions — exposed over MCP.

## The problem

An AI agent can read a repository but holds no persistent memory of it. So it:

- writes functions that already exist
- breaks layering rules that are held by convention, not by the compiler
- edits modules that are protected for reasons the source never states

Each of those costs review time and rework. ArchMemory gives the agent a place
to look before it writes.

## Target substrate

Index and evaluate against a real codebase: `psf/requests`, pinned at
`5460f467b02e49471c0fd6cfc9ca0adab6351f98`, source root `src/requests`.

Clone it into `target/` — it is gitignored and is not part of the submission.

```
git clone https://github.com/psf/requests.git target/requests
cd target/requests && git checkout 5460f467b02e49471c0fd6cfc9ca0adab6351f98
```

## Architecture

```
    IBM Bob  --MCP-->  ArchMemory server
                            |
       +--------------------+--------------------+
       |          |             |                |
   Architecture  Capability  Constraint      Decision
      graph        index       store          memory
       |          |             |                |
       +--------------------+--------------------+
                            |
                     SQLite (one file)
                            |
                     Code repository
```

Deliberately no external services: no Neo4j, no vector database, no Docker.
One SQLite file, one `pip install`, one MCP entry. Everything must run offline.

## Components to build

### 1. Store (`archmemory/store.py`)

SQLite-backed persistence for four memory types in one file.

Tables:

- `nodes` — `id, kind (module|function|class), name, path, lineno, signature, docstring`
- `edges` — `src, dst, kind (imports|calls|contains), lineno`
- `capabilities` — `id, name, purpose, inputs, outputs, risk, used_by, source`
- `constraints` — `id, scope, rule, rationale, severity, source`
- `decisions` — `id, scope, decision, reason, risk, recorded, source`

Needs: insert helpers, a `stats()` summary, and a `callers_of(node_id)` lookup.

### 2. Indexer (`archmemory/indexer.py`)

Walk a Python source tree with the stdlib `ast` module and populate the graph.
Deterministic, no model calls.

Two passes:

1. modules, classes, functions — with signature and docstring
2. imports and calls, once every name is known

**Critical detail:** relative imports must resolve to absolute module ids.
`from .utils import x` inside `requests.adapters` must become an edge to
`requests.utils`, not to `.utils`. The layering checks depend entirely on this
being right, so test it explicitly.

Acceptance: on `requests`, expect roughly 19 modules, 228 functions, 52 classes,
729 edges. `requests.adapters` must show imports of `requests.utils` and
`requests.models`, and **must not** show `requests.api` or `requests.sessions`.

### 3. Capability search (`archmemory/query.py`)

Find existing capabilities from a natural-language description.

TF-IDF over each function's name, signature, docstring, and — when present —
its capability memory (purpose, inputs, outputs). No embedding service: the
demo must not be able to fail because a provider is down.

Requirements:

- split `snake_case` and `camelCase` so "check if ip in network" can reach
  `address_in_network`
- skip dunders and container-protocol names (`get`, `keys`, `items`, `update`,
  `content`, ...) — a dict's `.get()` is not a reusable capability, and matching
  against it produces confident nonsense
- return location, signature, purpose, similarity, and `used_by`

### 4. Capability memory (`archmemory/capability.py`) — **Bob subagents**

This is where Bob becomes part of the product rather than just the IDE.

The static index only knows what the source literally says, and that is often
not enough:

- `super_len` has no docstring at all
- `guess_json_utf`'s docstring is just `:rtype: str`
- `address_in_network` says "subnet" where a user would say "CIDR"

So: for each module, dispatch a **Bob subagent** that reads it and writes, for
each public function, a `purpose`, `inputs`, `outputs`, and `risk` into the
`capabilities` table. One subagent per module, run in parallel, each in an
isolated context.

This is measurable: retrieval accuracy before vs after the enrichment is the
headline experiment. Do not skip the "before" measurement.

### 5. Constraints and decisions (`archmemory/seed.py`, `commit_memory`)

Rules the source cannot tell you. Two sources:

- **engineer input** — recorded directly
- **document understanding** — Bob reads `docs/architecture.md` and extracts
  constraints and decisions from prose

Seed these, verified as true of `requests` today:

| id | scope | rule | severity |
|---|---|---|---|
| LAYER-01 | `requests.adapters` | must not import `requests.api`, `requests.sessions` | critical |
| LAYER-02 | `requests.utils` | must not import `requests.api`, `requests.sessions`, `requests.adapters` | critical |
| LAYER-03 | `requests.models` | must not import `requests.api`, `requests.sessions` | high |

| id | scope | decision | risk |
|---|---|---|---|
| PROTECT-01 | `*compat.py` | do not modify without owner sign-off | high |
| PROTECT-02 | `*packages.py` | backwards-compatible aliases; do not refactor | high |

### 6. Validator (`archmemory/validate.py`) — **the differentiator**

Given a unified diff, return a verdict of `ok` or `blocked` with evidence.
Deterministic — derived from the graph and the constraint store, not from a
model's opinion. That is what makes it a gate rather than a suggestion.

Three checks:

1. **duplicate capability** — for each function added by the diff, search the
   capability index. Above a high similarity bar, report the existing
   implementation, where it lives, and who already depends on it. A function
   redefining itself in its own module is an edit, not a duplicate.
2. **architecture violation** — resolve the diff's added imports (including
   relative ones) and check them against the constraints governing that module.
3. **protected component** — match the changed paths against decision memory.

Parsing note: added lines from a hunk rarely parse as a complete module, so fall
back to a regex scan when `ast.parse` fails.

### 7. MCP server (`archmemory/server.py`)

Expose the memory over stdio. **The installed `mcp` package is 2.x, where
`FastMCP` was renamed** — use `from mcp.server.mcpserver import MCPServer`.

Tools:

| tool | purpose |
|---|---|
| `find_capability(description, limit)` | does this already exist? Call before writing anything new. |
| `get_architecture_context(component)` | what it contains, depends on, and is used by |
| `get_constraints(component)` | rules and recorded decisions governing it |
| `validate_diff(diff)` | the gate — call before finalising any change |
| `commit_memory(...)` | record a new constraint or decision |
| `memory_stats()` | what memory currently holds |

Tool descriptions matter as much as the code: they are the only instructions the
agent sees. Say *when* to call each one, not just what it does.

### 8. CLI (`archmemory/cli.py`)

`index`, `find`, `validate`, `stats`. Every tool must be demonstrable without
Bob, so a broken MCP connection can never take the demo down.

## Bob features this should use

Because they fit the work, not to tick boxes:

| feature | where |
|---|---|
| `/init` → AGENTS.md | persistent project context before building |
| subagents / parallel tasks | the capability-memory pipeline |
| document understanding | extracting constraints from `architecture.md` |
| MCP servers | how the agent reaches memory |
| custom mode | a "Modernization Engineer" that must consult memory first |
| custom rules | never add a capability without calling `find_capability` |
| skills | a reusable `index-repo` workflow |

## Out of scope for Bob — deliberately

The benchmark, its task set, and its ground truth are authored independently and
held out. If Bob wrote both the system and its exam, the results would be
self-graded and worth nothing. This separation is an experimental control and
should be stated in the README.

## Definition of done

- `python -m archmemory.cli index target/requests/src` populates the graph
- `find_capability("check if an IP is in a CIDR network")` returns
  `address_in_network` or `is_valid_cidr`
- a diff adding a duplicate function is `blocked` with the existing one named
- a diff importing `api` into `adapters` is `blocked` citing LAYER-01
- a diff touching `compat.py` is `blocked` citing PROTECT-01
- the MCP server completes an `initialize` + `tools/list` handshake over stdio
