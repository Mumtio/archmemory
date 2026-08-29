# AGENTS.md — Ask Mode Rules

This file provides guidance to agents when working with code in this repository.

## Non-obvious documentation context

- `target/requests/` is a **gitignored reference codebase** (psf/requests, pinned commit), not archmemory source. Don't mistake it for production code belonging to this project.
- `docs/architecture.md` is architecture notes *for the `requests` library*, not for `archmemory`. It is the input document Bob reads to extract constraints.
- `docs/SPEC.md` is the authoritative build spec for `archmemory`. All acceptance criteria live there, not in a README.
- The `archmemory/` source package does not exist yet — this is a greenfield project.
- `bob_sessions/` stores PNG screenshots of Bob task sessions (hackathon evidence), not code.
- The benchmark / test suite that grades the system is authored separately and intentionally withheld — it is not in this repo.
