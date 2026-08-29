# AGENTS.md — Plan Mode Rules

This file provides guidance to agents when working with code in this repository.

## Non-obvious architectural constraints

- **No upward imports in requests layers**: api → sessions → adapters → models → utils is strictly downward. Any plan that adds an import crossing a layer boundary will be blocked by the validator.
- **Deterministic validation is the core value**: the validator must derive `ok`/`blocked` from the graph + constraint store, never from a model's opinion. Plans that make validation probabilistic break the design.
- **Two-pass indexing is required**: names must be fully resolved before edges are created. A single-pass plan will produce unresolvable relative-import edges.
- **Capability enrichment is measurable**: the spec requires a "before enrichment" retrieval accuracy measurement. Any plan that skips this or merges it with the "after" phase invalidates the experiment.
- **`compat.py` and `packages.py` are protected**: any plan touching these files in `target/requests/src` must cite PROTECT-01 / PROTECT-02 and require owner sign-off.
- **MCP tool descriptions are user-facing instructions**: they determine when the agent calls each tool — treat them as critical product copy, not boilerplate.
- **SQLite schema is the contract**: the five tables (`nodes`, `edges`, `capabilities`, `constraints`, `decisions`) are fixed in the spec. Do not add tables or rename columns without updating all consumers.
