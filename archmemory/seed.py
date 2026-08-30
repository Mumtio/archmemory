"""
archmemory.seed
~~~~~~~~~~~~~~~

Populate the constraints and decisions tables with the known governance rules
for the ``requests`` reference codebase.

Constraints: architecture layering rules (LAYER-01/02/03).
Decisions:   protected-component rules (PROTECT-01/02).

Rationales are taken verbatim from docs/architecture.md.
"""

from __future__ import annotations

from datetime import date

from .store import Store


# ---------------------------------------------------------------------------
# Constraint records — architecture layering
# ---------------------------------------------------------------------------

_CONSTRAINTS = [
    {
        "id": "LAYER-01",
        "scope": "requests.adapters",
        "rule": "requests.api,requests.sessions",
        "rationale": (
            "adapters.py must not import api.py or sessions.py. "
            "The transport layer sits below the session layer; importing upward "
            "creates a cycle and couples the transport to the calling convention above it."
        ),
        "severity": "critical",
        "source": "docs/architecture.md",
    },
    {
        "id": "LAYER-02",
        "scope": "requests.utils",
        "rule": "requests.api,requests.sessions,requests.adapters",
        "rationale": (
            "utils.py must not import api.py, sessions.py, or adapters.py. "
            "utils is a leaf module that every other module depends on, "
            "so any upward import is a cycle."
        ),
        "severity": "critical",
        "source": "docs/architecture.md",
    },
    {
        "id": "LAYER-03",
        "scope": "requests.models",
        "rule": "requests.api,requests.sessions",
        "rationale": (
            "models.py must not import api.py or sessions.py. "
            "Models describe request and response data and must not reach back "
            "into the layer that constructs them."
        ),
        "severity": "high",
        "source": "docs/architecture.md",
    },
]

# ---------------------------------------------------------------------------
# Decision records — protected components
# ---------------------------------------------------------------------------

_DECISIONS = [
    {
        "id": "PROTECT-01",
        "scope": "*compat.py",
        "decision": "do not modify without owner sign-off",
        "reason": (
            "compat.py is the single shim for interpreter and urllib3 differences, "
            "and every module imports it. Changes here have repository-wide blast radius "
            "and have historically caused breakage that only surfaces on one platform."
        ),
        "risk": "high",
        "recorded": str(date.today()),
        "source": "docs/architecture.md",
    },
    {
        "id": "PROTECT-02",
        "scope": "*packages.py",
        "decision": "compatibility aliases, do not refactor",
        "reason": (
            "packages.py exists to provide backwards-compatible import aliases for "
            "urllib3 and idna. Downstream code imports through it, and removing the "
            "aliases breaks consumers silently at import time."
        ),
        "risk": "high",
        "recorded": str(date.today()),
        "source": "docs/architecture.md",
    },
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def seed(store: Store) -> None:
    """Insert all constraints and decisions into *store*.

    Safe to call multiple times — uses INSERT OR REPLACE.
    """
    for rec in _CONSTRAINTS:
        store.insert_constraint(**rec)
    for rec in _DECISIONS:
        store.insert_decision(**rec)
    store.commit()
