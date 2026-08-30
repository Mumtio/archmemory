"""
Tests for archmemory.validate and archmemory.seed.

Three required scenarios -- all must come back BLOCKED with the right rule:

  1. Duplicate capability: diff adds a function that reimplements an existing
     utils helper (ip_in_cidr / address_in_network).

  2. Architecture violation: diff adds ``from .api import get`` to adapters.py
     -- violates LAYER-01.

  3. Protected component: diff touches compat.py -- violates PROTECT-01.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from archmemory.seed import seed
from archmemory.store import Store
from archmemory.validate import (
    DUPLICATE_THRESHOLD,
    _changed_paths,
    _extract_added_functions,
    _extract_added_imports,
    _module_id_from_path,
    _resolve_relative,
    validate_diff,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def seeded_store(tmp_path):
    s = Store(tmp_path / "test.db")
    seed(s)
    return s


@pytest.fixture()
def store_with_utils_capability(tmp_path):
    s = Store(tmp_path / "cap.db")
    seed(s)
    s.insert_node(
        id="requests.utils",
        kind="module",
        name="utils",
        path="src/requests/utils.py",
    )
    s.insert_node(
        id="requests.utils.address_in_network",
        kind="function",
        name="address_in_network",
        path="src/requests/utils.py",
        lineno=42,
        signature="address_in_network(ip: str, network: str) -> bool",
        docstring=(
            "This function allows you to check if an IP belongs to a network subnet."
            " It returns a boolean."
        ),
    )
    s.insert_edge(
        src="requests.utils",
        dst="requests.utils.address_in_network",
        kind="contains",
    )
    s.insert_capability(
        id="requests.utils.address_in_network",
        name="address_in_network",
        purpose="check whether an IPv4 address falls inside a CIDR network range",
        inputs="ip: str, network: str in CIDR notation",
        outputs="bool",
        risk="none",
        source="requests.utils",
    )
    s.insert_node(
        id="requests.utils.select_proxy",
        kind="function",
        name="select_proxy",
        path="src/requests/utils.py",
        lineno=100,
    )
    s.insert_edge(
        src="requests.utils.select_proxy",
        dst="requests.utils.address_in_network",
        kind="calls",
    )
    s.commit()
    return s


# ---------------------------------------------------------------------------
# Helper unit tests
# ---------------------------------------------------------------------------


class TestModuleIdFromPath:
    def test_src_prefix_stripped(self):
        assert _module_id_from_path("src/requests/adapters.py") == "requests.adapters"

    def test_no_src_prefix(self):
        assert _module_id_from_path("requests/utils.py") == "requests.utils"

    def test_flat_file(self):
        assert _module_id_from_path("compat.py") == "compat"

    def test_nested_package(self):
        assert _module_id_from_path("src/requests/packages.py") == "requests.packages"


class TestResolveRelative:
    def test_single_dot(self):
        assert _resolve_relative(1, "api", "requests.adapters") == "requests.api"

    def test_empty_name(self):
        assert _resolve_relative(1, "", "requests.adapters") == "requests"

    def test_double_dot(self):
        assert _resolve_relative(2, "utils", "requests.sub.mod") == "requests.utils"


class TestChangedPaths:
    def test_extracts_b_side(self):
        diff = textwrap.dedent("""\
            diff --git a/src/requests/adapters.py b/src/requests/adapters.py
            --- a/src/requests/adapters.py
            +++ b/src/requests/adapters.py
            @@ -1,1 +1,2 @@
            +from .api import get
        """)
        assert _changed_paths(diff) == ["src/requests/adapters.py"]

    def test_dev_null_excluded(self):
        diff = textwrap.dedent("""\
            --- a/old.py
            +++ /dev/null
        """)
        assert _changed_paths(diff) == []


class TestExtractAddedImports:
    def test_relative_import_resolved(self):
        lines = ["from .api import get"]
        result = _extract_added_imports(lines, "requests.adapters")
        assert "requests.api" in result

    def test_absolute_import(self):
        lines = ["import os", "from collections import OrderedDict"]
        result = _extract_added_imports(lines, "requests.utils")
        assert "os" in result
        assert "collections" in result

    def test_regex_fallback(self):
        lines = ["from .sessions import Session", "def broken("]
        result = _extract_added_imports(lines, "requests.adapters")
        assert "requests.sessions" in result


class TestExtractAddedFunctions:
    def test_plain_def(self):
        lines = ["def helper(x, y):", "    return x + y"]
        assert "helper" in _extract_added_functions(lines)

    def test_async_def(self):
        lines = ["async def fetch(url):", "    pass"]
        assert "fetch" in _extract_added_functions(lines)

    def test_regex_fallback(self):
        lines = ["def incomplete(", "    pass"]
        assert "incomplete" in _extract_added_functions(lines)


# ---------------------------------------------------------------------------
# Seed tests
# ---------------------------------------------------------------------------


def test_seed_inserts_constraints(seeded_store):
    rows = seeded_store._conn.execute("SELECT id FROM constraints").fetchall()
    ids = {r["id"] for r in rows}
    assert {"LAYER-01", "LAYER-02", "LAYER-03"} == ids


def test_seed_inserts_decisions(seeded_store):
    rows = seeded_store._conn.execute("SELECT id FROM decisions").fetchall()
    ids = {r["id"] for r in rows}
    assert {"PROTECT-01", "PROTECT-02"} == ids


def test_seed_is_idempotent(seeded_store):
    seed(seeded_store)
    count = seeded_store._conn.execute(
        "SELECT COUNT(*) FROM constraints"
    ).fetchone()[0]
    assert count == 3


# ---------------------------------------------------------------------------
# Scenario 1 -- duplicate capability (BLOCKED)
# ---------------------------------------------------------------------------

DUPLICATE_DIFF = Path("tests/fixtures/t01_duplicate.diff").read_text()


def test_duplicate_blocked(store_with_utils_capability):
    result = validate_diff(DUPLICATE_DIFF, store_with_utils_capability)
    assert result["verdict"] == "blocked", f"Expected blocked, got: {result}"


def test_duplicate_finding_present(store_with_utils_capability):
    result = validate_diff(DUPLICATE_DIFF, store_with_utils_capability)
    dup = [f for f in result["findings"] if f["check"] == "duplicate"]
    assert dup, f"No duplicate findings in: {result['findings']}"
    assert dup[0]["existing_id"] == "requests.utils.address_in_network"
    assert dup[0]["similarity"] > DUPLICATE_THRESHOLD


def test_duplicate_finding_has_callers(store_with_utils_capability):
    result = validate_diff(DUPLICATE_DIFF, store_with_utils_capability)
    dup = [f for f in result["findings"] if f["check"] == "duplicate"]
    assert dup
    assert any("select_proxy" in c for c in dup[0]["callers"]), (
        f"Expected select_proxy in callers, got: {dup[0]['callers']}"
    )


# ---------------------------------------------------------------------------
# Scenario 2 -- architecture violation (BLOCKED, cites LAYER-01)
# ---------------------------------------------------------------------------

LAYERING_DIFF = Path("tests/fixtures/t07_layering.diff").read_text()


def test_layer_violation_blocked(seeded_store):
    result = validate_diff(LAYERING_DIFF, seeded_store)
    assert result["verdict"] == "blocked", f"Expected blocked, got: {result}"


def test_layer_violation_cites_layer01(seeded_store):
    result = validate_diff(LAYERING_DIFF, seeded_store)
    arch = [f for f in result["findings"] if f["check"] == "architecture"]
    assert arch, f"No architecture findings in: {result['findings']}"
    assert "LAYER-01" in {f["rule_id"] for f in arch}


def test_layer_violation_finding_details(seeded_store):
    result = validate_diff(LAYERING_DIFF, seeded_store)
    arch = [f for f in result["findings"] if f["check"] == "architecture"]
    f = next(x for x in arch if x["rule_id"] == "LAYER-01")
    assert f["module"] == "requests.adapters"
    assert "requests.api" in f["imported"]


# ---------------------------------------------------------------------------
# Scenario 3 -- protected component (BLOCKED, cites PROTECT-01)
# ---------------------------------------------------------------------------

PROTECTED_DIFF = Path("tests/fixtures/t08_protected.diff").read_text()


def test_protected_blocked(seeded_store):
    result = validate_diff(PROTECTED_DIFF, seeded_store)
    assert result["verdict"] == "blocked", f"Expected blocked, got: {result}"


def test_protected_cites_protect01(seeded_store):
    result = validate_diff(PROTECTED_DIFF, seeded_store)
    prot = [f for f in result["findings"] if f["check"] == "protected"]
    assert prot, f"No protected findings in: {result['findings']}"
    assert "PROTECT-01" in {f["rule_id"] for f in prot}


def test_protected_finding_details(seeded_store):
    result = validate_diff(PROTECTED_DIFF, seeded_store)
    prot = [f for f in result["findings"] if f["check"] == "protected"]
    f = next(x for x in prot if x["rule_id"] == "PROTECT-01")
    assert "compat.py" in f["path"]
    assert f["risk"] == "high"


# ---------------------------------------------------------------------------
# Clean diff -- must return "ok"
# ---------------------------------------------------------------------------


def test_clean_diff_ok(seeded_store):
    clean = textwrap.dedent("""\
        diff --git a/src/requests/certs.py b/src/requests/certs.py
        --- a/src/requests/certs.py
        +++ b/src/requests/certs.py
        @@ -1,1 +1,2 @@
        +# updated certificate bundle path
    """)
    result = validate_diff(clean, seeded_store)
    assert result["verdict"] == "ok"
    assert result["findings"] == []


# ---------------------------------------------------------------------------
# Edit (self-redefine) must NOT be flagged as a duplicate
# ---------------------------------------------------------------------------


def test_self_redefine_not_duplicate(store_with_utils_capability):
    diff = textwrap.dedent("""\
        diff --git a/src/requests/utils.py b/src/requests/utils.py
        --- a/src/requests/utils.py
        +++ b/src/requests/utils.py
        @@ -40,6 +40,8 @@
        +def address_in_network(ip, network):
        +    return True
    """)
    result = validate_diff(diff, store_with_utils_capability)
    dup = [f for f in result["findings"] if f["check"] == "duplicate"]
    assert not dup, f"Self-redefine wrongly flagged as duplicate: {dup}"


# ---------------------------------------------------------------------------
# Benign unrelated function must NOT be flagged as a duplicate (regression)
# ---------------------------------------------------------------------------

BENIGN_DIFF = Path("tests/fixtures/t09_benign_unrelated.diff").read_text()


def test_benign_unrelated_function_ok(store_with_utils_capability):
    """compute_fibonacci_sequence has no semantic overlap with requests helpers.

    The duplicate gate must return ok — if it returns blocked the relative-
    score normalisation is broken and any new function would be a false positive.
    """
    result = validate_diff(BENIGN_DIFF, store_with_utils_capability)
    dup = [f for f in result["findings"] if f["check"] == "duplicate"]
    assert not dup, (
        f"Unrelated function wrongly flagged as duplicate: {dup}\n"
        f"Full result: {result}"
    )
    assert result["verdict"] == "ok", f"Expected ok, got: {result}"


# ---------------------------------------------------------------------------
# Duplicate check emits exactly one finding per added function
# ---------------------------------------------------------------------------


def test_duplicate_finding_count(store_with_utils_capability):
    """A single added function must produce at most one duplicate finding."""
    result = validate_diff(DUPLICATE_DIFF, store_with_utils_capability)
    dup = [f for f in result["findings"] if f["check"] == "duplicate"]
    assert len(dup) == 1, (
        f"Expected exactly 1 duplicate finding, got {len(dup)}: {dup}"
    )
