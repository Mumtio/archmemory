"""Tests for archmemory.indexer — focusing on relative-import resolution and call edges."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from archmemory.store import Store
from archmemory.indexer import index_tree, _resolve_relative, _module_id


# ---------------------------------------------------------------------------
# Unit tests for _resolve_relative (the critical function)
# ---------------------------------------------------------------------------


class TestResolveRelative:
    """from .X import y inside pkg.mod  ->  pkg.X"""

    def test_single_dot_with_module(self):
        # from .utils import x  inside  requests.adapters
        assert _resolve_relative(1, "utils", "requests.adapters") == "requests.utils"

    def test_single_dot_empty_module(self):
        # from . import something  inside  requests.adapters
        # level=1, module=""  -> anchor is the package "requests"
        assert _resolve_relative(1, "", "requests.adapters") == "requests"

    def test_double_dot(self):
        # from ..exceptions import Err  inside  requests.subpkg.module
        assert _resolve_relative(2, "exceptions", "requests.subpkg.module") == "requests.exceptions"

    def test_single_dot_nested_package(self):
        # from .models import Response  inside  requests.adapters
        assert _resolve_relative(1, "models", "requests.adapters") == "requests.models"

    def test_does_not_produce_leading_dot(self):
        result = _resolve_relative(1, "utils", "requests.adapters")
        assert not result.startswith(".")


# ---------------------------------------------------------------------------
# Integration test: index a small synthetic package
# ---------------------------------------------------------------------------


@pytest.fixture()
def mini_pkg(tmp_path) -> Path:
    """Create a minimal two-module package: utils (defines helper) and
    adapters (imports and calls helper from utils)."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "utils.py").write_text(
        textwrap.dedent("""\
        def helper():
            \"\"\"A helper.\"\"\"
            pass

        def another():
            helper()
        """),
        encoding="utf-8",
    )
    (pkg / "adapters.py").write_text(
        textwrap.dedent("""\
        from .utils import helper

        class Adapter:
            \"\"\"Transport adapter.\"\"\"
            def send(self):
                helper()
        """),
        encoding="utf-8",
    )
    return pkg


@pytest.fixture()
def indexed_store(tmp_path, mini_pkg):
    s = Store(tmp_path / "mini.db")
    index_tree(mini_pkg, s)
    yield s
    s.close()


# --- import edge tests ---

def test_relative_import_resolves_to_absolute(indexed_store):
    """CRITICAL: from .utils import helper inside pkg.adapters
    must produce an edge  pkg.adapters -> pkg.utils  (not -> .utils)."""
    cur = indexed_store._conn.execute(
        "SELECT src, dst, kind FROM edges WHERE kind = 'imports'"
    )
    import_edges = [(r["src"], r["dst"]) for r in cur.fetchall()]
    assert ("pkg.adapters", "pkg.utils") in import_edges, (
        f"Expected edge pkg.adapters -> pkg.utils in imports, got: {import_edges}"
    )


def test_relative_import_not_stored_with_leading_dot(indexed_store):
    """The raw '.utils' form must never appear as a dst."""
    cur = indexed_store._conn.execute("SELECT dst FROM edges WHERE kind = 'imports'")
    dsts = [r["dst"] for r in cur.fetchall()]
    for dst in dsts:
        assert not dst.startswith("."), f"Relative dst leaked into edges: {dst!r}"


def test_module_nodes_exist(indexed_store):
    cur = indexed_store._conn.execute("SELECT id FROM nodes WHERE kind = 'module' ORDER BY id")
    ids = [r["id"] for r in cur.fetchall()]
    assert "pkg" in ids
    assert "pkg.utils" in ids
    assert "pkg.adapters" in ids


def test_class_and_method_nodes(indexed_store):
    cur = indexed_store._conn.execute("SELECT id FROM nodes WHERE kind = 'class'")
    class_ids = [r["id"] for r in cur.fetchall()]
    assert "pkg.adapters.Adapter" in class_ids

    cur = indexed_store._conn.execute("SELECT id FROM nodes WHERE kind = 'function'")
    fn_ids = [r["id"] for r in cur.fetchall()]
    assert "pkg.utils.helper" in fn_ids
    assert "pkg.adapters.Adapter.send" in fn_ids


def test_contains_edges(indexed_store):
    cur = indexed_store._conn.execute(
        "SELECT src, dst FROM edges WHERE kind = 'contains' ORDER BY src, dst"
    )
    contains = {(r["src"], r["dst"]) for r in cur.fetchall()}
    assert ("pkg.adapters", "pkg.adapters.Adapter") in contains
    assert ("pkg.utils", "pkg.utils.helper") in contains


# --- call edge tests ---

def test_method_calls_imported_function(indexed_store):
    """pkg.adapters.Adapter.send calls helper(), which was imported from pkg.utils.
    Edge: pkg.adapters.Adapter.send -> pkg.utils.helper  kind=calls."""
    cur = indexed_store._conn.execute(
        "SELECT src, dst FROM edges WHERE kind = 'calls'"
    )
    call_edges = {(r["src"], r["dst"]) for r in cur.fetchall()}
    assert ("pkg.adapters.Adapter.send", "pkg.utils.helper") in call_edges, (
        f"Expected call edge Adapter.send -> utils.helper, got: {call_edges}"
    )


def test_function_calls_sibling_function(indexed_store):
    """pkg.utils.another calls helper() defined in the same module.
    Edge: pkg.utils.another -> pkg.utils.helper  kind=calls."""
    cur = indexed_store._conn.execute(
        "SELECT src, dst FROM edges WHERE kind = 'calls'"
    )
    call_edges = {(r["src"], r["dst"]) for r in cur.fetchall()}
    assert ("pkg.utils.another", "pkg.utils.helper") in call_edges, (
        f"Expected call edge utils.another -> utils.helper, got: {call_edges}"
    )


def test_no_self_calls(indexed_store):
    """No function should have a calls edge to itself."""
    cur = indexed_store._conn.execute(
        "SELECT src, dst FROM edges WHERE kind = 'calls' AND src = dst"
    )
    self_calls = cur.fetchall()
    assert len(self_calls) == 0, f"Self-calls found: {list(self_calls)}"


# ---------------------------------------------------------------------------
# Integration test: the real requests codebase
# ---------------------------------------------------------------------------


REQUESTS_SRC = Path("target/requests/src/requests")


@pytest.fixture(scope="module")
def requests_store(tmp_path_factory):
    if not REQUESTS_SRC.exists():
        pytest.skip("target/requests not cloned")
    s = Store(tmp_path_factory.mktemp("db") / "requests.db")
    index_tree(REQUESTS_SRC, s)
    yield s
    s.close()


def test_adapters_imports_utils(requests_store):
    """requests.adapters must have an import edge to requests.utils."""
    cur = requests_store._conn.execute(
        "SELECT dst FROM edges WHERE src = 'requests.adapters' AND kind = 'imports'"
    )
    dsts = {r["dst"] for r in cur.fetchall()}
    assert "requests.utils" in dsts, f"Missing requests.utils in adapters imports: {dsts}"


def test_adapters_imports_models(requests_store):
    cur = requests_store._conn.execute(
        "SELECT dst FROM edges WHERE src = 'requests.adapters' AND kind = 'imports'"
    )
    dsts = {r["dst"] for r in cur.fetchall()}
    assert "requests.models" in dsts, f"Missing requests.models in adapters imports: {dsts}"


def test_adapters_does_not_import_api(requests_store):
    """LAYER-01: requests.adapters must NOT import requests.api."""
    cur = requests_store._conn.execute(
        "SELECT dst FROM edges WHERE src = 'requests.adapters' AND kind = 'imports'"
    )
    dsts = {r["dst"] for r in cur.fetchall()}
    assert "requests.api" not in dsts, "LAYER VIOLATION: adapters imports api"


def test_adapters_does_not_import_sessions(requests_store):
    """LAYER-01: requests.adapters must NOT import requests.sessions."""
    cur = requests_store._conn.execute(
        "SELECT dst FROM edges WHERE src = 'requests.adapters' AND kind = 'imports'"
    )
    dsts = {r["dst"] for r in cur.fetchall()}
    assert "requests.sessions" not in dsts, "LAYER VIOLATION: adapters imports sessions"


def test_no_relative_dst_in_requests(requests_store):
    """No import edge dst should start with a dot after full indexing."""
    cur = requests_store._conn.execute("SELECT dst FROM edges WHERE kind = 'imports'")
    for row in cur.fetchall():
        assert not row["dst"].startswith("."), f"Relative dst in requests index: {row['dst']!r}"


def test_calls_edges_exist(requests_store):
    """The requests codebase must produce a non-trivial number of call edges."""
    cur = requests_store._conn.execute(
        "SELECT COUNT(*) FROM edges WHERE kind = 'calls'"
    )
    count = cur.fetchone()[0]
    assert count > 50, f"Too few call edges: {count} (expected > 50)"


def test_calls_are_function_to_function(requests_store):
    """Every calls-edge src and dst must be a function or class node, not a module."""
    cur = requests_store._conn.execute(
        """
        SELECT e.src, e.dst, ns.kind AS src_kind, nd.kind AS dst_kind
        FROM edges e
        JOIN nodes ns ON ns.id = e.src
        JOIN nodes nd ON nd.id = e.dst
        WHERE e.kind = 'calls' AND (ns.kind = 'module' OR nd.kind = 'module')
        LIMIT 5
        """
    )
    bad = cur.fetchall()
    assert len(bad) == 0, (
        f"Found calls edges involving module nodes: {[dict(r) for r in bad]}"
    )