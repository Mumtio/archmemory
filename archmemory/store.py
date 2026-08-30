"""
archmemory.store
~~~~~~~~~~~~~~~~

SQLite-backed persistence for the four memory types:
  nodes, edges, capabilities, constraints, decisions.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


_SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
    id        TEXT PRIMARY KEY,
    kind      TEXT NOT NULL CHECK(kind IN ('module', 'function', 'class')),
    name      TEXT NOT NULL,
    path      TEXT NOT NULL,
    lineno    INTEGER,
    signature TEXT,
    docstring TEXT
);

CREATE TABLE IF NOT EXISTS edges (
    src    TEXT NOT NULL,
    dst    TEXT NOT NULL,
    kind   TEXT NOT NULL CHECK(kind IN ('imports', 'calls', 'contains')),
    lineno INTEGER,
    PRIMARY KEY (src, dst, kind)
);

CREATE TABLE IF NOT EXISTS capabilities (
    id      TEXT PRIMARY KEY,
    name    TEXT NOT NULL,
    purpose TEXT,
    inputs  TEXT,
    outputs TEXT,
    risk    TEXT,
    used_by TEXT,
    source  TEXT
);

CREATE TABLE IF NOT EXISTS constraints (
    id        TEXT PRIMARY KEY,
    scope     TEXT NOT NULL,
    rule      TEXT NOT NULL,
    rationale TEXT,
    severity  TEXT,
    source    TEXT
);

CREATE TABLE IF NOT EXISTS decisions (
    id       TEXT PRIMARY KEY,
    scope    TEXT NOT NULL,
    decision TEXT NOT NULL,
    reason   TEXT,
    risk     TEXT,
    recorded TEXT,
    source   TEXT
);

CREATE INDEX IF NOT EXISTS idx_nodes_path ON nodes (path);
CREATE INDEX IF NOT EXISTS idx_nodes_name ON nodes (name);
CREATE INDEX IF NOT EXISTS idx_edges_src  ON edges (src);
CREATE INDEX IF NOT EXISTS idx_edges_dst  ON edges (dst);
"""


class Store:
    """Thin wrapper around a single SQLite database file."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ------------------------------------------------------------------
    # nodes
    # ------------------------------------------------------------------

    def insert_node(
        self,
        id: str,
        kind: str,
        name: str,
        path: str,
        lineno: int | None = None,
        signature: str | None = None,
        docstring: str | None = None,
    ) -> None:
        self._conn.execute(
            """
            INSERT OR REPLACE INTO nodes
                (id, kind, name, path, lineno, signature, docstring)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (id, kind, name, path, lineno, signature, docstring),
        )

    # ------------------------------------------------------------------
    # edges
    # ------------------------------------------------------------------

    def insert_edge(
        self,
        src: str,
        dst: str,
        kind: str,
        lineno: int | None = None,
    ) -> None:
        self._conn.execute(
            """
            INSERT OR REPLACE INTO edges (src, dst, kind, lineno)
            VALUES (?, ?, ?, ?)
            """,
            (src, dst, kind, lineno),
        )

    # ------------------------------------------------------------------
    # capabilities / constraints / decisions
    # ------------------------------------------------------------------

    def insert_capability(self, **kwargs: Any) -> None:
        cols = ", ".join(kwargs)
        placeholders = ", ".join("?" * len(kwargs))
        self._conn.execute(
            f"INSERT OR REPLACE INTO capabilities ({cols}) VALUES ({placeholders})",
            list(kwargs.values()),
        )

    def insert_constraint(self, **kwargs: Any) -> None:
        cols = ", ".join(kwargs)
        placeholders = ", ".join("?" * len(kwargs))
        self._conn.execute(
            f"INSERT OR REPLACE INTO constraints ({cols}) VALUES ({placeholders})",
            list(kwargs.values()),
        )

    def insert_decision(self, **kwargs: Any) -> None:
        cols = ", ".join(kwargs)
        placeholders = ", ".join("?" * len(kwargs))
        self._conn.execute(
            f"INSERT OR REPLACE INTO decisions ({cols}) VALUES ({placeholders})",
            list(kwargs.values()),
        )

    # ------------------------------------------------------------------
    # queries
    # ------------------------------------------------------------------

    def callers_of(self, node_id: str) -> list[sqlite3.Row]:
        """Return all nodes that have a 'calls' edge pointing to *node_id*."""
        cur = self._conn.execute(
            """
            SELECT n.* FROM nodes n
            JOIN edges e ON e.src = n.id
            WHERE e.dst = ? AND e.kind = 'calls'
            """,
            (node_id,),
        )
        return cur.fetchall()

    def stats(self) -> dict[str, int]:
        """Return row counts for every table."""
        tables = ["nodes", "edges", "capabilities", "constraints", "decisions"]
        result: dict[str, int] = {}
        for table in tables:
            row = self._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            result[table] = row[0]

        # Break nodes down by kind
        for kind in ("module", "function", "class"):
            row = self._conn.execute(
                "SELECT COUNT(*) FROM nodes WHERE kind = ?", (kind,)
            ).fetchone()
            result[f"nodes_{kind}"] = row[0]

        # Break edges down by kind
        for kind in ("imports", "calls", "contains"):
            row = self._conn.execute(
                "SELECT COUNT(*) FROM edges WHERE kind = ?", (kind,)
            ).fetchone()
            result[f"edges_{kind}"] = row[0]

        return result

    def commit(self) -> None:
        self._conn.commit()

    def close(self) -> None:
        self._conn.commit()
        self._conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()