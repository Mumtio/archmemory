"""Tests for archmemory.store."""
from __future__ import annotations

import pytest
from archmemory.store import Store


@pytest.fixture()
def store(tmp_path):
    s = Store(tmp_path / "test.db")
    yield s
    s.close()


def test_insert_and_retrieve_node(store):
    store.insert_node(id="pkg.mod", kind="module", name="mod", path="mod.py")
    store.commit()
    cur = store._conn.execute("SELECT * FROM nodes WHERE id = 'pkg.mod'")
    row = cur.fetchone()
    assert row is not None
    assert row["kind"] == "module"


def test_stats_empty(store):
    s = store.stats()
    assert s["nodes"] == 0
    assert s["edges"] == 0


def test_stats_after_inserts(store):
    store.insert_node(id="a.mod", kind="module", name="mod", path="mod.py")
    store.insert_node(id="a.mod.fn", kind="function", name="fn", path="mod.py")
    store.insert_edge(src="a.mod", dst="a.mod.fn", kind="contains")
    store.commit()
    s = store.stats()
    assert s["nodes"] == 2
    assert s["edges"] == 1
    assert s["nodes_module"] == 1
    assert s["nodes_function"] == 1


def test_callers_of(store):
    store.insert_node(id="a.mod", kind="module", name="mod", path="mod.py")
    store.insert_node(id="a.mod.caller", kind="function", name="caller", path="mod.py")
    store.insert_node(id="a.mod.callee", kind="function", name="callee", path="mod.py")
    store.insert_edge(src="a.mod.caller", dst="a.mod.callee", kind="calls")
    store.commit()
    callers = store.callers_of("a.mod.callee")
    assert len(callers) == 1
    assert callers[0]["id"] == "a.mod.caller"


def test_callers_of_empty(store):
    store.insert_node(id="a.mod.fn", kind="function", name="fn", path="mod.py")
    store.commit()
    assert store.callers_of("a.mod.fn") == []


def test_duplicate_edge_is_idempotent(store):
    store.insert_node(id="a.mod", kind="module", name="mod", path="mod.py")
    store.insert_node(id="b.mod", kind="module", name="mod", path="mod2.py")
    store.insert_edge(src="a.mod", dst="b.mod", kind="imports")
    store.insert_edge(src="a.mod", dst="b.mod", kind="imports")
    store.commit()
    cur = store._conn.execute("SELECT COUNT(*) FROM edges")
    assert cur.fetchone()[0] == 1
