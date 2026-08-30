"""
archmemory.cli
~~~~~~~~~~~~~~

Command-line interface for ArchMemory.

Every MCP tool is reachable here so the system stays demonstrable if the MCP
connection is unavailable during a demo.

Usage::

    python -m archmemory.cli --help
    python -m archmemory.cli index  target/requests/src/requests
    python -m archmemory.cli enrich target/requests/src/requests
    python -m archmemory.cli seed
    python -m archmemory.cli find   "check if an IP is in a CIDR network"
    python -m archmemory.cli validate changes.diff
    python -m archmemory.cli stats

    # Equivalents to every MCP tool
    python -m archmemory.cli context  requests.utils
    python -m archmemory.cli constraints requests.adapters
    python -m archmemory.cli commit-memory --kind constraint --id LAYER-04 \
        --scope requests.auth --statement "..." --reason "..."
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import textwrap
from datetime import date
from pathlib import Path
from typing import Any

from .capability import enrich_all
from .indexer import index_tree
from .query import CapabilityHit, find_capability
from .seed import seed
from .store import Store
from .validate import validate_diff

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_DB_ENV = "ARCHMEMORY_DB"
_DB_DEFAULT = "archmemory.db"

TICK = "\u2713"   # check
CROSS = "\u2717"  # cross
ARROW = "\u2192"  # arrow
BAR = "\u2500"    # horiz bar


def _db_path(args: argparse.Namespace) -> str:
    return getattr(args, "db", None) or os.environ.get(_DB_ENV, _DB_DEFAULT)


def _hr(width: int = 60) -> str:
    return BAR * width


def _wrap(text: str, indent: int = 4, width: int = 76) -> str:
    prefix = " " * indent
    return textwrap.fill(
        text, width=width, initial_indent=prefix, subsequent_indent=prefix
    )


# ---------------------------------------------------------------------------
# index
# ---------------------------------------------------------------------------


def cmd_index(args: argparse.Namespace) -> int:
    src_root = Path(args.src_root)
    if not src_root.exists():
        print(f"  {CROSS} Source root not found: {src_root}", file=sys.stderr)
        return 1

    db = _db_path(args)
    print(f"  Indexing  {src_root}")
    print(f"  Database  {db}")
    print()

    with Store(db) as store:
        index_tree(src_root, store)
        counts = store.stats()

    print(f"  {TICK} Done")
    print()
    print(f"  Modules   {counts['nodes_module']}")
    print(f"  Functions {counts['nodes_function']}")
    print(f"  Classes   {counts['nodes_class']}")
    print(f"  Edges     {counts['edges']}")
    return 0


# ---------------------------------------------------------------------------
# enrich
# ---------------------------------------------------------------------------


def cmd_enrich(args: argparse.Namespace) -> int:
    src_root = Path(args.src_root)
    if not src_root.exists():
        print(f"  {CROSS} Source root not found: {src_root}", file=sys.stderr)
        return 1

    db = _db_path(args)
    print(f"  Enriching {src_root}")
    print(f"  Database  {db}")
    print()

    results = enrich_all(src_root, db)

    total = sum(results.values())
    for module_id, count in sorted(results.items()):
        print(f"  {TICK} {module_id:<40} {count:>3} capabilities")
    print()
    print(f"  Total: {total} capabilities written across {len(results)} modules")
    return 0


# ---------------------------------------------------------------------------
# seed
# ---------------------------------------------------------------------------


def cmd_seed(args: argparse.Namespace) -> int:
    db = _db_path(args)
    print(f"  Seeding governance rules into {db}")
    print()

    with Store(db) as store:
        seed(store)
        constraints = store._conn.execute(
            "SELECT id, scope, severity FROM constraints"
        ).fetchall()
        decisions = store._conn.execute(
            "SELECT id, scope, risk FROM decisions"
        ).fetchall()

    for row in constraints:
        print(
            f"  {TICK} constraint  {row['id']:<12}  "
            f"scope={row['scope']:<30}  severity={row['severity']}"
        )
    for row in decisions:
        print(
            f"  {TICK} decision    {row['id']:<12}  "
            f"scope={row['scope']:<30}  risk={row['risk']}"
        )
    print()
    print(f"  Seeded {len(constraints)} constraint(s), {len(decisions)} decision(s).")
    return 0


# ---------------------------------------------------------------------------
# find
# ---------------------------------------------------------------------------


def cmd_find(args: argparse.Namespace) -> int:
    db = _db_path(args)
    description: str = args.description
    limit: int = args.limit

    with Store(db) as store:
        hits: list[CapabilityHit] = find_capability(description, store, limit=limit)

    if not hits:
        print(f"  No capabilities found for: {description!r}")
        return 0

    print(f'  Results for: "{description}"')
    print(f"  {_hr()}")
    for i, h in enumerate(hits, 1):
        sim_pct = f"{h.similarity * 100:.0f}%"
        print(f"  {i}. {h.name}  [{sim_pct} match]")
        print(f"     {h.location}")
        if h.signature:
            print(f"     {h.signature}")
        if h.purpose:
            print(_wrap(h.purpose, indent=5, width=78))
        if h.used_by:
            callers = ", ".join(h.used_by[:5])
            suffix = f"  (+{len(h.used_by) - 5} more)" if len(h.used_by) > 5 else ""
            print(f"     Used by: {callers}{suffix}")
        if i < len(hits):
            print()
    return 0


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


def cmd_validate(args: argparse.Namespace) -> int:
    diff_path = Path(args.diff_file)
    if not diff_path.exists():
        print(f"  {CROSS} Diff file not found: {diff_path}", file=sys.stderr)
        return 2

    diff_text = diff_path.read_text(encoding="utf-8", errors="replace")
    db = _db_path(args)

    with Store(db) as store:
        result = validate_diff(diff_text, store)

    verdict: str = result["verdict"]
    findings: list[dict[str, Any]] = result["findings"]
    summary: str = result["summary"]

    if verdict == "ok":
        print(f"  {TICK} {summary}")
        return 0

    # blocked -- print detail for each finding, exit 1 for pre-commit hook use
    print(f"  {CROSS} {summary}")
    print()

    for f in findings:
        check = f["check"]
        if check == "protected":
            print(f"  [{f['rule_id']}] Protected component  {ARROW}  {f['path']}")
            print(f"     Decision : {f['decision']}")
            print(_wrap(f["reason"], indent=5, width=78))
        elif check == "architecture":
            print(f"  [{f['rule_id']}] Architecture violation  {ARROW}  {f['module']}")
            print(f"     Imported : {f['imported']}")
            print(f"     Severity : {f['severity']}")
            print(_wrap(f["rationale"], indent=5, width=78))
        elif check == "duplicate":
            print(f"  [DUP] Duplicate capability  {ARROW}  {f['new_fn']}")
            best: dict[str, Any] | None = f.get("best_match")
            if best:
                print(f"     Matches  : {best['name']}  [{int(best['similarity'] * 100)}% similar]")
                print(f"     Location : {best['location']}")
                if best.get("purpose"):
                    print(_wrap(best["purpose"], indent=5, width=78))
        else:
            print(f"  [{check.upper()}] {f}")
        print()

    return 1


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------


def cmd_stats(args: argparse.Namespace) -> int:
    db = _db_path(args)
    with Store(db) as store:
        counts = store.stats()

    print(f"  ArchMemory  {ARROW}  {db}")
    print(f"  {_hr()}")
    print(f"  Nodes        {counts['nodes']:>6}")
    print(f"    modules    {counts['nodes_module']:>6}")
    print(f"    functions  {counts['nodes_function']:>6}")
    print(f"    classes    {counts['nodes_class']:>6}")
    print(f"  Edges        {counts['edges']:>6}")
    print(f"    imports    {counts['edges_imports']:>6}")
    print(f"    calls      {counts['edges_calls']:>6}")
    print(f"    contains   {counts['edges_contains']:>6}")
    print(f"  Capabilities {counts['capabilities']:>6}")
    print(f"  Constraints  {counts['constraints']:>6}")
    print(f"  Decisions    {counts['decisions']:>6}")
    return 0


# ---------------------------------------------------------------------------
# context  (MCP parity: get_architecture_context)
# ---------------------------------------------------------------------------


def cmd_context(args: argparse.Namespace) -> int:
    component: str = args.component
    db = _db_path(args)

    with Store(db) as store:
        conn: sqlite3.Connection = store._conn

        row = conn.execute(
            "SELECT * FROM nodes WHERE id = ? OR name = ? LIMIT 1",
            (component, component),
        ).fetchone()

        if row is None:
            print(f"  {CROSS} Component not found: {component!r}", file=sys.stderr)
            return 1

        node_id: str = row["id"]

        members = conn.execute(
            """
            SELECT n.kind, n.name, n.lineno, n.signature
            FROM nodes n
            JOIN edges e ON e.dst = n.id
            WHERE e.src = ? AND e.kind = 'contains'
            ORDER BY n.lineno
            """,
            (node_id,),
        ).fetchall()

        depends_on = conn.execute(
            """
            SELECT n.id FROM nodes n
            JOIN edges e ON e.dst = n.id
            WHERE e.src = ? AND e.kind = 'imports'
            """,
            (node_id,),
        ).fetchall()

        used_by = conn.execute(
            """
            SELECT n.id FROM nodes n
            JOIN edges e ON e.src = n.id
            WHERE e.dst = ? AND e.kind = 'imports'
            """,
            (node_id,),
        ).fetchall()

    print(f"  {node_id}  [{row['kind']}]")
    if row["path"]:
        print(f"  {row['path']}")
    print(f"  {_hr()}")

    if depends_on:
        print(f"  Depends on ({len(depends_on)})")
        for d in depends_on:
            print(f"    {ARROW} {d['id']}")

    if used_by:
        print(f"  Used by ({len(used_by)})")
        for u in used_by:
            print(f"    {ARROW} {u['id']}")

    if members:
        print(f"  Members ({len(members)})")
        for m in members:
            sig = f"  {m['signature']}" if m["signature"] else ""
            print(f"    {m['kind']:<10} {m['name']}{sig}")

    return 0


# ---------------------------------------------------------------------------
# constraints  (MCP parity: get_constraints)
# ---------------------------------------------------------------------------


def cmd_constraints(args: argparse.Namespace) -> int:
    component: str = args.component
    db = _db_path(args)

    with Store(db) as store:
        conn: sqlite3.Connection = store._conn
        constraints = conn.execute(
            "SELECT id, scope, rule, rationale, severity FROM constraints WHERE scope LIKE ?",
            (f"%{component}%",),
        ).fetchall()
        decisions = conn.execute(
            "SELECT id, scope, decision, reason, risk FROM decisions WHERE scope LIKE ?",
            (f"%{component}%",),
        ).fetchall()

    if not constraints and not decisions:
        print(f"  No constraints or decisions found for: {component!r}")
        return 0

    print(f"  Governance rules for: {component}")
    print(f"  {_hr()}")
    for c in constraints:
        print(f"  [{c['id']}] constraint  severity={c['severity']}")
        print(f"     Scope : {c['scope']}")
        print(f"     Rule  : {c['rule']}")
        print(_wrap(c["rationale"], indent=5, width=78))
        print()
    for d in decisions:
        print(f"  [{d['id']}] decision  risk={d['risk']}")
        print(f"     Scope    : {d['scope']}")
        print(f"     Decision : {d['decision']}")
        print(_wrap(d["reason"], indent=5, width=78))
        print()
    return 0


# ---------------------------------------------------------------------------
# commit-memory  (MCP parity: commit_memory)
# ---------------------------------------------------------------------------


def cmd_commit_memory(args: argparse.Namespace) -> int:
    kind: str = args.kind.lower().strip()
    if kind not in ("constraint", "decision"):
        print(f"  {CROSS} --kind must be 'constraint' or 'decision'", file=sys.stderr)
        return 1

    db = _db_path(args)
    with Store(db) as store:
        if kind == "constraint":
            store.insert_constraint(
                id=args.id,
                scope=args.scope,
                rule=args.statement,
                rationale=args.reason,
                severity=args.severity or "high",
                source=args.source or "cli",
            )
        else:
            store.insert_decision(
                id=args.id,
                scope=args.scope,
                decision=args.statement,
                reason=args.reason,
                risk=args.risk or "unknown",
                recorded=str(date.today()),
                source=args.source or "cli",
            )
        store.commit()

    print(f"  {TICK} Recorded {kind} {args.id!r}  scope={args.scope!r}")
    return 0


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m archmemory.cli",
        description="ArchMemory -- persistent memory layer for AI coding agents.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(
            """\
            Examples:
              python -m archmemory.cli index  target/requests/src/requests
              python -m archmemory.cli enrich target/requests/src/requests
              python -m archmemory.cli seed
              python -m archmemory.cli find   "check if an IP is in a CIDR network"
              python -m archmemory.cli validate changes.diff
              python -m archmemory.cli stats
              python -m archmemory.cli context     requests.utils
              python -m archmemory.cli constraints requests.adapters
            """
        ),
    )
    parser.add_argument(
        "--db",
        default=None,
        metavar="PATH",
        help=f"SQLite database path (default: ${_DB_ENV} or {_DB_DEFAULT!r}).",
    )

    sub = parser.add_subparsers(dest="command", metavar="<command>")
    sub.required = True

    # index
    p_index = sub.add_parser("index", help="Walk source tree and populate nodes/edges.")
    p_index.add_argument("src_root", metavar="SRC_ROOT", help="Package source directory.")
    p_index.set_defaults(func=cmd_index)

    # enrich
    p_enrich = sub.add_parser(
        "enrich", help="Generate capability descriptions for every function."
    )
    p_enrich.add_argument("src_root", metavar="SRC_ROOT", help="Package source directory.")
    p_enrich.set_defaults(func=cmd_enrich)

    # seed
    p_seed = sub.add_parser("seed", help="Insert architecture constraints and decisions.")
    p_seed.set_defaults(func=cmd_seed)

    # find
    p_find = sub.add_parser(
        "find", help="Search capabilities by natural-language description."
    )
    p_find.add_argument("description", metavar="QUERY", help="Natural-language search query.")
    p_find.add_argument(
        "-n", "--limit", type=int, default=5, metavar="N", help="Max results (default 5)."
    )
    p_find.set_defaults(func=cmd_find)

    # validate
    p_val = sub.add_parser(
        "validate",
        help="Validate a unified diff against governance rules. Exits non-zero when blocked.",
    )
    p_val.add_argument("diff_file", metavar="DIFF_FILE", help="Path to a unified diff file.")
    p_val.set_defaults(func=cmd_validate)

    # stats
    p_stats = sub.add_parser("stats", help="Show row counts for every table in the store.")
    p_stats.set_defaults(func=cmd_stats)

    # context  (MCP parity: get_architecture_context)
    p_ctx = sub.add_parser("context", help="Show a module's members, imports, and callers.")
    p_ctx.add_argument("component", metavar="COMPONENT", help="Module id or bare name.")
    p_ctx.set_defaults(func=cmd_context)

    # constraints  (MCP parity: get_constraints)
    p_con = sub.add_parser(
        "constraints", help="Show constraints and decisions for a component."
    )
    p_con.add_argument("component", metavar="COMPONENT", help="Module id or bare name.")
    p_con.set_defaults(func=cmd_constraints)

    # commit-memory  (MCP parity: commit_memory)
    p_cm = sub.add_parser("commit-memory", help="Record a new constraint or decision.")
    p_cm.add_argument("--kind", required=True, choices=["constraint", "decision"])
    p_cm.add_argument("--id", required=True, metavar="ID", help="Unique slug, e.g. LAYER-04.")
    p_cm.add_argument("--scope", required=True, metavar="SCOPE", help="Module id or file glob.")
    p_cm.add_argument(
        "--statement", required=True, metavar="TEXT", help="The rule or decision text."
    )
    p_cm.add_argument("--reason", required=True, metavar="TEXT", help="Rationale.")
    p_cm.add_argument(
        "--severity",
        default="high",
        metavar="LEVEL",
        help="critical|high|medium (constraints).",
    )
    p_cm.add_argument(
        "--risk", default="unknown", metavar="LEVEL", help="Risk level (decisions)."
    )
    p_cm.add_argument(
        "--source", default="cli", metavar="SOURCE", help="Origin of this record."
    )
    p_cm.set_defaults(func=cmd_commit_memory)

    # silence unused-variable warnings from argparse sub-parser variables
    _ = p_index, p_enrich, p_seed, p_find, p_val, p_stats, p_ctx, p_con, p_cm

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    # Ensure Unicode output works on Windows consoles (CP-1252 by default).
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

    parser = _build_parser()
    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()