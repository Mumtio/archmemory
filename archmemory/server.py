"""
archmemory.server
~~~~~~~~~~~~~~~~~

MCP server that exposes the ArchMemory knowledge graph to any agent over stdio.

Entry point::

    python -m archmemory.server [--db PATH] [--source-root PATH]

Or via the module-level ``main()`` for programmatic use.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import date
from typing import Any

from mcp.server.mcpserver import MCPServer

from .query import ego_expand, find_capability
from .store import Store
from .validate import validate_diff

# ---------------------------------------------------------------------------
# Server factory
# ---------------------------------------------------------------------------


def _build_server(db_path: str) -> MCPServer:  # type: ignore[type-arg]
    """Create and return a fully-wired MCPServer instance."""

    mcp = MCPServer(
        name="archmemory",
        title="ArchMemory",
        description=(
            "Persistent memory layer for AI coding agents. "
            "Knows the codebase's structure, capabilities, constraints, and decisions."
        ),
    )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _open() -> Store:
        """Open the store. Each tool call gets a fresh connection so the
        server stays correct even after the index is updated externally."""
        return Store(db_path)

    # ------------------------------------------------------------------
    # Tool: find_capability
    # ------------------------------------------------------------------

    @mcp.tool(
        name="find_capability",
        description=(
            "Call this BEFORE writing any new function, to avoid duplicating logic "
            "that already exists in the codebase. "
            "Searches the indexed function graph by natural-language description "
            "and returns the top matches ranked by BM25 similarity. "
            "Each result includes the function name, file location, signature, "
            "purpose, and callers. "
            "If similarity is high (> 0.4), reuse or extend the existing function "
            "instead of writing a new one."
        ),
    )
    def tool_find_capability(description: str, limit: int = 8) -> str:
        """Search for existing capabilities matching a natural-language description.

        Returns lexical matches plus their call-graph neighbourhood.  The
        neighbourhood matters: lexical search only finds a function when the
        caller happens to use the author's vocabulary, and on twelve held-out
        queries it put the right function in the top 3 only twice.  Expanding
        one hop along call edges raises that to 8 in 12, because the call graph
        does not depend on wording.
        """
        with _open() as store:
            hits = find_capability(description, store, limit=limit)
            related = ego_expand(store, [h.node_id for h in hits], hops=1, limit=12)

        payload = {
            "query": description,
            "matches": [
                {
                    "node_id": h.node_id,
                    "name": h.name,
                    "location": h.location,
                    "signature": h.signature,
                    "purpose": h.purpose,
                    "similarity": h.similarity,
                    "used_by": h.used_by,
                }
                for h in hits
            ],
            "related_by_call_graph": related,
            "note": (
                "'related_by_call_graph' are functions that call, or are called "
                "by, the matches above. They are NOT ranked by text similarity, "
                "so scan them: the capability you want is often here rather than "
                "in the ranked matches, particularly when it is undocumented."
            ),
        }
        return json.dumps(payload, indent=2)

    # ------------------------------------------------------------------
    # Tool: get_architecture_context
    # ------------------------------------------------------------------

    @mcp.tool(
        name="get_architecture_context",
        description=(
            "Call this when you are about to modify or add code to a component, "
            "to understand what it contains, what it depends on, and who depends on it. "
            "Returns the module's direct import edges (depends_on), "
            "all modules that import it (used_by), and its top-level functions and classes. "
            "Use it to choose the right file and to spot dependency risks before coding."
        ),
    )
    def tool_get_architecture_context(component: str) -> str:
        """Return structural context for a module: members, imports, callers."""
        with _open() as store:
            conn: sqlite3.Connection = store._conn

            # Resolve: accept bare name ("utils") or full id ("requests.utils")
            row = conn.execute(
                "SELECT * FROM nodes WHERE id = ? OR name = ? LIMIT 1",
                (component, component),
            ).fetchone()

            if row is None:
                return json.dumps({"error": f"Component '{component}' not found."})

            node_id: str = row["id"]

            # Members (contained nodes)
            members = conn.execute(
                """
                SELECT n.id, n.kind, n.name, n.lineno, n.signature
                FROM nodes n
                JOIN edges e ON e.dst = n.id
                WHERE e.src = ? AND e.kind = 'contains'
                ORDER BY n.lineno
                """,
                (node_id,),
            ).fetchall()

            # Imports (depends_on)
            depends_on = conn.execute(
                """
                SELECT n.id, n.name FROM nodes n
                JOIN edges e ON e.dst = n.id
                WHERE e.src = ? AND e.kind = 'imports'
                """,
                (node_id,),
            ).fetchall()

            # Callers / used_by (who imports this node)
            used_by = conn.execute(
                """
                SELECT n.id, n.name FROM nodes n
                JOIN edges e ON e.src = n.id
                WHERE e.dst = ? AND e.kind = 'imports'
                """,
                (node_id,),
            ).fetchall()

        return json.dumps(
            {
                "id": node_id,
                "kind": row["kind"],
                "path": row["path"],
                "members": [
                    {
                        "id": m["id"],
                        "kind": m["kind"],
                        "name": m["name"],
                        "lineno": m["lineno"],
                        "signature": m["signature"],
                    }
                    for m in members
                ],
                "depends_on": [{"id": d["id"], "name": d["name"]} for d in depends_on],
                "used_by": [{"id": u["id"], "name": u["name"]} for u in used_by],
            },
            indent=2,
        )

    # ------------------------------------------------------------------
    # Tool: get_constraints
    # ------------------------------------------------------------------

    @mcp.tool(
        name="get_constraints",
        description=(
            "Call this before modifying a component to see which rules and decisions "
            "govern it. Returns all architecture constraints (layering rules) and "
            "recorded decisions (protected files, design choices) whose scope matches "
            "the given component name. "
            "Review these before writing code — validate_diff will block you if you "
            "violate them, so it is faster to check here first."
        ),
    )
    def tool_get_constraints(component: str) -> str:
        """Return constraints and decisions that apply to a component."""
        with _open() as store:
            conn = store._conn

            constraints = conn.execute(
                "SELECT * FROM constraints WHERE scope LIKE ?",
                (f"%{component}%",),
            ).fetchall()

            decisions = conn.execute(
                "SELECT * FROM decisions WHERE scope LIKE ?",
                (f"%{component}%",),
            ).fetchall()

        def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
            return dict(zip(row.keys(), tuple(row)))

        return json.dumps(
            {
                "component": component,
                "constraints": [_row_to_dict(r) for r in constraints],
                "decisions": [_row_to_dict(r) for r in decisions],
            },
            indent=2,
        )

    # ------------------------------------------------------------------
    # Tool: validate_diff
    # ------------------------------------------------------------------

    @mcp.tool(
        name="validate_diff",
        description=(
            "The mandatory gate before committing any code change. "
            "Pass the unified diff of your proposed change (as produced by "
            "'git diff' or 'diff -u'). "
            "Returns verdict='ok' if the change is safe to land, or "
            "verdict='blocked' with a list of findings explaining each violation. "
            "Violations include: importing a forbidden module (architecture constraint), "
            "modifying a protected file without sign-off, or duplicating a capability "
            "that already exists. "
            "Fix every finding before committing. Do not bypass this check."
        ),
    )
    def tool_validate_diff(diff: str) -> str:
        """Validate a unified diff against governance rules. Returns verdict + findings."""
        with _open() as store:
            result = validate_diff(diff, store)
        return json.dumps(result, indent=2)

    # ------------------------------------------------------------------
    # Tool: commit_memory
    # ------------------------------------------------------------------

    @mcp.tool(
        name="commit_memory",
        description=(
            "Record a new constraint or decision in persistent memory so future "
            "agents benefit from it. "
            "Use kind='constraint' to add an architecture rule (e.g. 'module X must "
            "not import module Y') and kind='decision' to record a design choice or "
            "protected-component notice. "
            "Call this after a code-review discussion resolves an important rule, "
            "or when you discover an undocumented invariant that should be enforced "
            "going forward. "
            "Required fields: kind, id (unique slug like 'LAYER-04'), scope (module "
            "or file pattern), statement (the rule text), reason (why it exists). "
            "Optional: severity ('critical'|'high'|'medium'), risk, source."
        ),
    )
    def tool_commit_memory(
        kind: str,
        id: str,
        scope: str,
        statement: str,
        reason: str,
        severity: str = "high",
        risk: str = "",
        source: str = "agent",
    ) -> str:
        """Insert a new constraint or decision record into the store."""
        kind = kind.lower().strip()
        if kind not in ("constraint", "decision"):
            return json.dumps(
                {"error": f"kind must be 'constraint' or 'decision', got '{kind}'"}
            )

        with _open() as store:
            if kind == "constraint":
                store.insert_constraint(
                    id=id,
                    scope=scope,
                    rule=statement,
                    rationale=reason,
                    severity=severity or "high",
                    source=source or "agent",
                )
            else:
                store.insert_decision(
                    id=id,
                    scope=scope,
                    decision=statement,
                    reason=reason,
                    risk=risk or "unknown",
                    recorded=str(date.today()),
                    source=source or "agent",
                )
            store.commit()

        return json.dumps({"status": "recorded", "kind": kind, "id": id})

    # ------------------------------------------------------------------
    # Tool: memory_stats
    # ------------------------------------------------------------------

    @mcp.tool(
        name="memory_stats",
        description=(
            "Call this to see what the memory currently holds: number of modules, "
            "functions, classes, import/call edges, capabilities, constraints, and "
            "decisions indexed. "
            "Use it to confirm the index is populated before running other tools, "
            "or to get a quick health-check of the knowledge graph."
        ),
    )
    def tool_memory_stats() -> str:
        """Return row counts for every table in the store."""
        with _open() as store:
            stats = store.stats()
        return json.dumps(stats, indent=2)

    return mcp


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ArchMemory MCP server -- exposes the knowledge graph over stdio."
    )
    parser.add_argument(
        "--db",
        default=os.environ.get("ARCHMEMORY_DB", "archmemory.db"),
        help="Path to the SQLite database (default: $ARCHMEMORY_DB or archmemory.db).",
    )
    parser.add_argument(
        "--source-root",
        default=None,
        help="Source root to index on startup (optional; skipped if already indexed).",
    )
    args = parser.parse_args()

    db_path: str = args.db

    if args.source_root:
        # Lazy import to keep server startup fast when already indexed
        from .indexer import index_source_root

        with Store(db_path) as store:
            index_source_root(args.source_root, store)

    server = _build_server(db_path)
    server.run(transport="stdio")


if __name__ == "__main__":
    main()

