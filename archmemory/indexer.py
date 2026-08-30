"""
archmemory.indexer
~~~~~~~~~~~~~~~~~~

Two-pass AST walker that populates the Store from a Python source tree.

Pass 1 — declarations: every .py file becomes a module node; every
         class and function inside it becomes a child node.  Also builds
         the per-module name map used in pass 2 for call resolution.

Pass 2 — edges: imports (intra-package only — stdlib and third-party are
         deliberately excluded, they add noise without helping architecture
         checks) and calls (function-to-function granularity).
         Relative imports are resolved to absolute module ids before
         insertion; unresolvable call targets are silently skipped.
"""

from __future__ import annotations

import ast
from pathlib import Path

from .store import Store


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _module_id(rel_path: Path, src_root: Path) -> str:
    """Convert a .py file path to a dotted module id.

    Example: src_root=src/requests, rel_path=src/requests/adapters.py
             -> "requests.adapters"
    """
    rel = rel_path.relative_to(src_root.parent)
    parts = list(rel.with_suffix("").parts)
    # Drop __init__ — the package id is just the directory
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _resolve_relative(module_level: int, module_id: str, from_module: str) -> str:
    """Resolve a relative import to an absolute module id.

    module_level: the number of leading dots (e.g. 1 for ``from .utils``)
    module_id:    the dotted name after the dots (may be empty for ``from . import x``)
    from_module:  the absolute id of the module that contains the import
    """
    parts = from_module.split(".")
    # 1 dot = current package (strip the module name, keep the package).
    # 2 dots = parent package (strip module name + one more level), etc.
    # Example: requests.adapters, level=1  ->  parts[:1] = ["requests"]
    anchor_parts = parts[: len(parts) - module_level]
    if module_id:
        anchor_parts.append(module_id)
    return ".".join(anchor_parts)


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Reconstruct a readable signature string from an AST function node."""
    args = node.args
    parts: list[str] = []

    posonlyargs = list(args.posonlyargs)
    regularargs = list(args.args)
    defaults = list(args.defaults)

    all_positional = posonlyargs + regularargs
    n_defaults = len(defaults)
    n_args = len(all_positional)
    default_offset = n_args - n_defaults

    def _arg_str(arg: ast.arg, idx: int) -> str:
        annotation = f": {ast.unparse(arg.annotation)}" if arg.annotation else ""
        if idx >= default_offset:
            default = f" = {ast.unparse(defaults[idx - default_offset])}"
        else:
            default = ""
        return f"{arg.arg}{annotation}{default}"

    for i, arg in enumerate(all_positional):
        parts.append(_arg_str(arg, i))
        if arg in posonlyargs and i == len(posonlyargs) - 1:
            parts.append("/")

    if args.vararg:
        ann = f": {ast.unparse(args.vararg.annotation)}" if args.vararg.annotation else ""
        parts.append(f"*{args.vararg.arg}{ann}")
    elif args.kwonlyargs:
        parts.append("*")

    kw_defaults = list(args.kw_defaults)
    for j, kwarg in enumerate(args.kwonlyargs):
        ann = f": {ast.unparse(kwarg.annotation)}" if kwarg.annotation else ""
        kd = kw_defaults[j]
        default = f" = {ast.unparse(kd)}" if kd is not None else ""
        parts.append(f"{kwarg.arg}{ann}{default}")

    if args.kwarg:
        ann = f": {ast.unparse(args.kwarg.annotation)}" if args.kwarg.annotation else ""
        parts.append(f"**{args.kwarg.arg}{ann}")

    ret = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    return f"{node.name}({', '.join(parts)}){ret}"


def _docstring(node: ast.AST) -> str | None:
    return ast.get_docstring(node)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Name map — built during pass 1, consumed during pass 2 for call resolution
# ---------------------------------------------------------------------------

# NameMap maps a bare name (as it appears at call sites) to the node id
# of the thing being called.  It is keyed per-module.
#
# Contents:
#   "helper"          -> "pkg.utils.helper"        (local function)
#   "Adapter"         -> "pkg.adapters.Adapter"    (local class)
#   "helper"          -> "pkg.utils.helper"        (imported name)
#
# Only intra-package names are tracked; stdlib/third-party are not in
# the store so there would be nothing to link them to.

NameMap = dict[str, str]


def _callee_name(call_node: ast.Call) -> str | None:
    """Extract the bare name of a call target, or None if not resolvable.

    Handles:
      foo()           -> "foo"
      self.foo()      -> "foo"   (method call on self)
      obj.foo()       -> "foo"   (attribute call — ambiguous but still useful)
      foo.bar.baz()   -> None    (chained; skip)
    """
    func = call_node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        # Only one level of attribute: self.method() or obj.method()
        if isinstance(func.value, ast.Name):
            return func.attr
    return None


# ---------------------------------------------------------------------------
# Pass 1: declarations
# ---------------------------------------------------------------------------


def _pass1(
    py_files: list[Path],
    src_root: Path,
    store: Store,
) -> dict[str, NameMap]:
    """Walk every .py file, insert module/class/function nodes.

    Returns a per-module NameMap used in pass 2 to resolve call targets.
    The map covers locally-defined functions, classes, and imported names
    (resolved to their node ids where possible).
    """
    # We build a preliminary name map for locally-defined names only during
    # pass 1; imported names are added in pass 2 once all modules are known.
    local_names: dict[str, NameMap] = {}

    for py_file in py_files:
        mod_id = _module_id(py_file, src_root)
        try:
            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(py_file))
        except SyntaxError:
            continue

        store.insert_node(
            id=mod_id,
            kind="module",
            name=mod_id.split(".")[-1],
            path=str(py_file),
            lineno=0,
            docstring=_docstring(tree),
        )

        names: NameMap = {}

        # Classes and their methods
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                class_id = f"{mod_id}.{node.name}"
                store.insert_node(
                    id=class_id,
                    kind="class",
                    name=node.name,
                    path=str(py_file),
                    lineno=node.lineno,
                    docstring=_docstring(node),
                )
                store.insert_edge(
                    src=mod_id, dst=class_id, kind="contains", lineno=node.lineno
                )
                names[node.name] = class_id

                for child in ast.iter_child_nodes(node):
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        method_id = f"{class_id}.{child.name}"
                        store.insert_node(
                            id=method_id,
                            kind="function",
                            name=child.name,
                            path=str(py_file),
                            lineno=child.lineno,
                            signature=_signature(child),
                            docstring=_docstring(child),
                        )
                        store.insert_edge(
                            src=class_id,
                            dst=method_id,
                            kind="contains",
                            lineno=child.lineno,
                        )
                        # Method names are also resolvable from within the same module
                        names[child.name] = method_id

        # Top-level functions (direct children of the module)
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                fn_id = f"{mod_id}.{node.name}"
                store.insert_node(
                    id=fn_id,
                    kind="function",
                    name=node.name,
                    path=str(py_file),
                    lineno=node.lineno,
                    signature=_signature(node),
                    docstring=_docstring(node),
                )
                store.insert_edge(
                    src=mod_id, dst=fn_id, kind="contains", lineno=node.lineno
                )
                names[node.name] = fn_id

        local_names[mod_id] = names

    store.commit()
    return local_names


# ---------------------------------------------------------------------------
# Pass 2: import edges + call edges
# ---------------------------------------------------------------------------


def _pass2(
    py_files: list[Path],
    src_root: Path,
    store: Store,
    local_names: dict[str, NameMap],
) -> None:
    """Resolve all imports and calls, insert 'imports' and 'calls' edges.

    Import filter: only intra-package imports are recorded (i.e., only
    edges whose destination is a module that was indexed in pass 1).
    stdlib and third-party imports are deliberately dropped — they add
    noise without helping the architecture-violation checks, and the
    store has no nodes for them anyway.

    Relative imports are resolved to absolute module ids before insertion.

    Call resolution: for each function body, walk ast.Call nodes.  The
    enclosing function node id is the edge source; the callee is resolved
    via the per-module name map built in pass 1 plus imported names
    encountered in pass 2.  Unresolvable call targets are silently skipped
    (e.g. local variables, stdlib calls, deeply chained attribute access).
    """
    # Collect known module ids from pass 1
    known: set[str] = set()
    cur = store._conn.execute("SELECT id FROM nodes WHERE kind = 'module'")
    for row in cur.fetchall():
        known.add(row["id"])

    # Collect all known node ids for call resolution
    known_nodes: set[str] = set()
    cur = store._conn.execute("SELECT id FROM nodes")
    for row in cur.fetchall():
        known_nodes.add(row["id"])

    for py_file in py_files:
        mod_id = _module_id(py_file, src_root)
        try:
            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(py_file))
        except SyntaxError:
            continue

        # Start with locally-defined names, then extend with imported names
        name_map: NameMap = dict(local_names.get(mod_id, {}))

        # --- imports pass (also builds name_map for imported names) ---

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    dst = alias.name
                    if dst in known:
                        store.insert_edge(
                            src=mod_id, dst=dst, kind="imports", lineno=node.lineno
                        )
                        # Bind the alias (or the module name) to the module node
                        bind_name = alias.asname or alias.name.split(".")[-1]
                        name_map[bind_name] = dst

            elif isinstance(node, ast.ImportFrom):
                raw_module = node.module or ""
                level = node.level

                if level > 0:
                    abs_dst = _resolve_relative(level, raw_module, mod_id)
                else:
                    abs_dst = raw_module

                if abs_dst in known:
                    store.insert_edge(
                        src=mod_id, dst=abs_dst, kind="imports", lineno=node.lineno
                    )

                # Bind each imported name to its node id (if it has a node).
                # Only bind when the exact dotted id exists — do NOT fall back
                # to the module node.  A call to urlparse() should not produce
                # a calls edge to requests.compat; there is nothing useful to
                # report about it and it pollutes used_by counts.
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    candidate = f"{abs_dst}.{alias.name}"
                    bind_name = alias.asname or alias.name
                    if candidate in known_nodes:
                        name_map[bind_name] = candidate

        # --- calls pass ---

        # Build parent map so we can find the enclosing function of each Call node.
        # ast.walk gives no parent info; we annotate the tree once.
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                child._parent = parent  # type: ignore[attr-defined]
        tree._parent = None  # type: ignore[attr-defined]

        def _enclosing_function(node: ast.AST) -> str | None:
            """Walk up the parent chain to find the nearest enclosing function node id."""
            current = node
            while True:
                parent = getattr(current, "_parent", None)
                if parent is None:
                    return None
                if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    # Determine this function's node id
                    grandparent = getattr(parent, "_parent", None)
                    if isinstance(grandparent, ast.ClassDef):
                        return f"{mod_id}.{grandparent.name}.{parent.name}"
                    # Top-level function
                    return f"{mod_id}.{parent.name}"
                current = parent

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            callee_name = _callee_name(node)
            if callee_name is None:
                continue
            dst_id = name_map.get(callee_name)
            if dst_id is None or dst_id not in known_nodes:
                continue
            # Only emit function→function or function→class calls
            src_id = _enclosing_function(node)
            if src_id is None or src_id not in known_nodes:
                continue
            # Don't record self-calls (a function trivially "calls" itself via recursion)
            if src_id == dst_id:
                continue
            store.insert_edge(src=src_id, dst=dst_id, kind="calls", lineno=node.lineno)

    store.commit()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def index_tree(src_root: str | Path, store: Store) -> None:
    """Index all .py files under *src_root* into *store*.

    The root is expected to be the top-level package directory (e.g.
    ``target/requests/src/requests``).  The module ids produced will be
    relative to *src_root*'s parent so that the package name is preserved.

    Example::

        with Store("arch.db") as s:
            index_tree("target/requests/src/requests", s)
    """
    src_root = Path(src_root).resolve()
    py_files = sorted(src_root.rglob("*.py"))

    name_map = _pass1(py_files, src_root, store)
    _pass2(py_files, src_root, store, name_map)