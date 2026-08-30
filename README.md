# ArchMemory

**A persistent software memory layer that lets AI coding agents safely change code they don't remember.**

Built with IBM Bob for the IBM TechXchange 2026 Pre-conference Dev Day Hackathon.

---

## The problem

An AI coding agent can read a repository, but it holds no persistent, verified
memory of it. Every session starts from nothing. So it:

- **writes functions that already exist** — because it never looked
- **breaks layering rules** that no compiler enforces and no file states
- **edits protected modules** for reasons the source never records

Each of those reaches a human reviewer, who spends time rejecting work that was
never going to be correct. On a large or legacy codebase — where the people who
knew *why* have moved on — this is the dominant cost of using AI assistance.

The knowledge needed to avoid it isn't in the code. It's in the architecture,
the reuse conventions, and the decisions nobody wrote down.

## The solution

ArchMemory indexes a codebase into four kinds of memory and exposes them to any
agent over MCP:

| Memory | Answers |
|---|---|
| **Architecture graph** | How is this system built? |
| **Capability index** | What already exists? |
| **Constraint store** | What rules must I respect? |
| **Decision memory** | Why is it like this? |

The agent consults memory *before* writing, and validates its own diff *before*
committing.

```
   IBM Bob  ──MCP──▶  ArchMemory
                          │
      ┌───────────┬───────┴───────┬───────────┐
   architecture  capability   constraint   decision
      graph        index        store       memory
      └───────────┴───────┬───────┴───────────┘
                          │
                   SQLite (one file)
                          │
                    code repository
```

No external services: no graph database, no vector store, no Docker. One SQLite
file, one `pip install`, one MCP entry. Everything runs offline — including the
demo, which cannot fail because a venue's network does.

---

## How IBM Bob was used

Bob is on **both sides** of this system: it built ArchMemory, and it is a
runtime component of it.

### Bob as builder

Every module was built by Bob, task by task. The session summaries in
[`bob_sessions/`](bob_sessions/) record each one.

| # | Task | Bobcoins |
|---|---|---|
| 01 | `/init` → `AGENTS.md` | 0.82 |
| 02 | store + AST indexer | 5.42 |
| 03 | capability search | 2.23 |
| 04 | capability memory | 12.78 |
| 05 | capability id + coverage fix | 6.02 |
| 06 | subagent enrichment test | 0.31 |
| 07 | BM25 scoring | 0.41 |
| 08 | validator + constraint seed | 2.63 |
| 09 | MCP server | 1.38 |
| 10 | MCP connected, live query | 0.13 |

**~32 Bobcoins** for 2,980 lines of Python and 679 lines of tests.

Bob features used, each because it fitted the work:

| Feature | Where |
|---|---|
| `/init` → `AGENTS.md` | persistent project context across every task |
| Agent mode | built every module |
| **Subagents / parallel tasks** | one per module, generating capability memory |
| **MCP servers** | how Bob reaches memory at runtime |
| **Document understanding** | constraints read from `docs/architecture.md` prose |
| Custom rules | `.bob/rules-agent`, `rules-ask`, `rules-plan` |
| Headless mode (`bob run`) | ran the A/B experiment unattended |
| New context window per task | cost control — measured below |

### Bob as component

Bob queries ArchMemory over MCP while writing code. Asked whether `requests`
already has a CIDR check, it called `find_capability` and answered:

> *"`requests.utils.address_in_network` — checks whether an IPv4 address
> belongs to a CIDR subnet. If you're about to write something that checks
> IP-in-CIDR, **reuse `address_in_network` directly rather than
> reimplementing it**."*

That exchange cost 0.13 Bobcoins and is the entire thesis of the project.

### What Bob caught that we didn't

Bob's own test suite found an off-by-one in relative-import resolution
(`requests.adapters.utils` instead of `requests.utils`). Every architecture
check depends on that resolution, so the bug would have silently disabled the
layering rules.

Bob's `used_by` also came out better than the reference implementation:
function→function granularity, class-qualified. `super_len` reports
`PreparedRequest.prepare_body`, not merely "the utils module".

### Cost discipline, measured

Long-running tasks re-send their whole accumulated context on every message:

| task | context | Bobcoins |
|---|---|---|
| built inside an accumulated 86.5k context | 86.5k | 5.42 |
| same class of work, fresh task | 47.4k | 2.23 |

**60% cheaper** in a fresh context window. Tasks 06–08 together cost 3.35,
against 12.78 for a single task run in a saturated context.

---

## Results

Substrate: **`psf/requests`** at commit `5460f467`, a real library with a real
test suite (632 passing, 10 pre-existing failures baselined).

### The governance gate

Given a proposed diff, does ArchMemory correctly allow or block it?

| diff | verdict | cited |
|---|---|---|
| reimplements CIDR membership | **blocked** | `address_in_network`, `src/requests/utils.py:726` |
| `from .api import get` in adapters | **blocked** | LAYER-01 |
| `from requests import sessions` in adapters | **blocked** | LAYER-01 |
| edits `compat.py` | **blocked** | PROTECT-01 |
| adds an unrelated Fibonacci helper | **ok** | — |

Calibrated on ten cases -- five reimplementations of capabilities that already
exist, five genuinely unrelated functions. All five reimplementations matched
the **correct** existing function: `address_in_network`, `guess_json_utf`,
`super_len`, `parse_header_links`, `get_auth_from_url`. No unrelated function
matched anything it should not have.

| | confidence |
|---|---|
| reimplementations | 0.250 - 0.768 |
| unrelated | 0.000 - 0.246 |

**The margin is thin -- 0.004 at the closest point** -- and that is a real
fragility, not a detail. The threshold sits at 0.22 and is documented in the
source with the reasoning: prefer a missed duplicate over a false one, because
an agent that gets wrongly blocked learns to ignore the gate, which costs more
than the occasional duplicate that slips through. On a larger corpus this would
need recalibrating, and a confidence score is the wrong long-term mechanism --
an exact-signature or AST-similarity check would be sturdier.

### Capability retrieval

Measured on twelve held-out queries the agent never saw, phrased deliberately
in vocabulary the source does not use.

| | right function present |
|---|---|
| lexical top-3, before enrichment | 1 / 12 |
| lexical top-3, after Bob subagent enrichment | 2 / 12 |
| **lexical top-8 + 1-hop call-graph expansion** | **8 / 12** |

**Lexical retrieval alone is not enough, and better descriptions barely help.**
The subagent descriptions are accurate -- `super_len` has no docstring, and the
subagent read its body and wrote *"determine the byte length of an object,
accounting for file position and various length attributes"*. That moved
retrieval by one query in twelve, because BM25 matches *words* and the gap here
is semantic: `super_len` is found for "length of a file like object for content
length" and missed entirely for "how many bytes will this upload body be".

**The call graph closes most of that gap, because it does not depend on
wording.** Expanding one hop from the lexical hits and returning the
neighbourhood as context takes 2/12 to 8/12.

This is not simply the effect of returning more results. At matched context
budget:

| candidates shown | lexical only | lexical + 1-hop |
|---|---|---|
| 8 | 4 / 12 | 4 / 12 |
| ~12 | 5 / 12 | **6 / 12** |
| ~16-20 | 7 / 12 (at 20) | **8 / 12 (at 16)** |

Graph expansion reaches more of the targets while showing fewer candidates.

**Three ranking strategies were tried and all failed.** Re-scoring the expanded
set lexically, adding a neighbour boost, and graph-consensus voting each land
at 2-3/12 -- the same score that missed the target first pushes it back down.
So the neighbourhood is returned as *context* beside the ranked hits rather
than merged into the ranking, and the agent makes the final choice.

This mirrors RepoGraph (Ouyang et al., ICLR 2025), which flattens ego-graphs
into the prompt rather than ranking with them, and independently reproduces
their finding that a second hop adds noise without gain -- 2-hop expansion here
also stalls at 6/12 while nearly tripling the candidate set.

### The vocabulary problem, demonstrated

The same six functions, changing only how the request was phrased:

| phrasing | hit rate |
|---|---|
| wording close to the source | 3/6 |
| natural paraphrase | **0/6** |

Nothing changed about the code or the index. `address_in_network`'s docstring
says *"subnet"*; ask for *"host inside the subnet range"* and you get
`get_host`, because "host" is the token that dominates.

### Agent behaviour — A/B

Eight tasks, run twice through `bob run` in headless mode. Both arms use an
identical command; the only difference is `--disable-mcp`. Ground truth was
pre-registered and git-committed before either arm ran.

| metric | Bob alone | Bob + ArchMemory |
|---|---|---|
| **wrote a duplicate** | **1** | **0** |
| avoided writing a duplicate | 5 / 6 | **6 / 6** |
| **edited a protected file** | **2** | **0** |
| architecture violations | 0 | 0 |

It caught both failures the baseline made.

**T04 — the duplicate.** Baseline wrote `get_content_length`, a 148-line
competing implementation whose own docstring reads *"Unlike `super_len`, this
function..."* — it knew the existing function was there and reimplemented it
anyway. With ArchMemory: no patch, and the correct answer.

> *"This helper already exists. `super_len(o)` in `utils.py:160` is precisely
> the function being asked for."*

`super_len` is the only target in the task set with **no docstring**. That is
why the baseline could not find it by reading, and why capability memory could.

**T08 — the protected component.** Baseline edited `compat.py` and seven other
files, 286 lines. With ArchMemory:

> *"**Blocked.** The validator enforces `PROTECT-01` and will not allow this
> change to land without owner sign-off."*

PROTECT-01 appears in no docstring, no lint rule and no training corpus. It is
knowledge that only exists because an engineer recorded it.

#### Three results that do not flatter the system

**The baseline is strong.** It handled 4 of 6 duplicate traps correctly with no
help at all. `psf/requests` is small, famous and well documented -- close to the
best case for an unaided agent. The single failure was the single undocumented
function. On a codebase with no priors and no docstrings, the gap would be
wider; on this one it is narrow, and we chose this substrate for credibility
rather than for a flattering result.

**T07 shows over-blocking.** The task asked for a convenience `GET` in
`adapters.py`. The baseline implemented it *legally*, routing through
`PreparedRequest` and `HTTPAdapter` without importing a forbidden module. The
ArchMemory arm refused outright, citing LAYER-01, when a compliant path
existed. Both satisfy the pre-registered expectation, but the baseline shipped
working code and ArchMemory did not. A gate that is too eager has a real cost.

**"Patches produced: 0" cuts both ways.** The ArchMemory arm never wrote code.
On six tasks that was correct. On T07 it was not.

## Method, and its limits

**The benchmark was held out from Bob.** The task set, the ground truth, and
the scoring scripts live in a separate repository Bob never had access to. If
Bob had written both the system and its exam, the results would be self-graded
and worth nothing.

**Ground truth was pre-registered** — written and git-committed before any
agent run, so no target could be chosen after seeing a result.

**One measurement was contaminated and discarded.** An early capability pass
tuned its generated descriptions against the six evaluation queries it had been
shown. That score was thrown away, a fresh set of held-out queries was written
using vocabulary the agent had never seen, and everything above is measured on
those. Recorded here rather than quietly dropped.

**Three bugs found by testing rather than trusting.** Each would have been a
visible failure:

- the duplicate threshold blocked *everything*, including an unrelated
  Fibonacci helper — a gate that always says no is worse than no gate
- **LAYER-01 could be bypassed** by writing `from requests import sessions`
  instead of `from requests.sessions import ...`
- Bob's own tests passed 57/57 while the validator was broken, because they ran
  against a synthetic store rather than the real index

**Known limits.** Retrieval is lexical (BM25) rather than embedding-based --
chosen so the demo cannot fail because a provider is unreachable, at the cost
of the semantic matching that would close the vocabulary gap directly. The
call-graph expansion recovers most of it (2/12 to 8/12) but as *recall into
context*, not as ranking: three ranking strategies were measured and none
improved precision@3 beyond 3/12.

The duplicate-detection threshold is calibrated on ten cases with a 0.004
margin, which is thin; an AST-similarity or signature check would be sturdier
than a confidence score.

Only Python is parsed. The legacy-language extractors the design calls for --
COBOL, RPG, JCL -- are not built, which matters because those are exactly the
codebases where the argument is strongest.

Constraint and decision memory are seeded from one architecture document rather
than learned from commit history.

The substrate is favourable to the baseline: `psf/requests` is small, famous,
and well documented, so an unaided agent already handles most of it. The
measured gap is therefore a lower bound on what an undocumented codebase would
show, not a typical case.

---

## Running it

```bash
git clone https://github.com/psf/requests.git target/requests
cd target/requests && git checkout 5460f467b02e49471c0fd6cfc9ca0adab6351f98
cd ../..

python -m archmemory.cli index target/requests/src/requests
python -m archmemory.cli enrich target/requests/src/requests
python -m archmemory.cli seed
python -m archmemory.cli find "check if an IP is in a CIDR network"
python -m archmemory.cli validate path/to/change.diff   # non-zero when blocked
```

### Connecting it to Bob

`.bob/mcp.json`:

```json
{
  "mcpServers": {
    "archmemory": {
      "command": "python",
      "args": ["-m", "archmemory.server", "--db", "archmemory.db"],
      "cwd": "c:/archmemory",
      "env": { "PYTHONPATH": "c:/archmemory" }
    }
  }
}
```

Then ask Bob to add something that already exists, and watch it decline.

### Demo

- `demo/anim.html` — source code becoming an architecture graph, five acts
- `demo/graph.html` — interactive module explorer; click `compat.py` for its
  protected-component record

Both are self-contained: no CDN, no network.

---

## Provenance

ArchMemory's modules were built by IBM Bob, task by task, with the session
summaries in [`bob_sessions/`](bob_sessions/) recording each one and its cost.
Bug fixes and gap-filling between Bob tasks, the benchmark and its ground
truth, and the held-out evaluation queries were authored separately — the
benchmark deliberately so, as an experimental control.
