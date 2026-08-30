# Video demonstration — script

Target: **4 minutes**. The rules require a video showing the solution *and*
how IBM Bob was used, so Bob has to be visibly on screen, not just described.

Record in this order. Each shot is independent, so a bad take costs one shot,
not the whole video.

---

## Shot 1 — the problem (0:00–0:35)

**Screen:** Bob IDE, `requests` open, a fresh chat with **no** ArchMemory.

**Do:** ask Bob to *"add a helper that returns the length of a file-like object
so we can set Content-Length."*

**It writes a new function.** `super_len` has done exactly this for a decade,
eleven lines away.

> "This is the failure that costs review time. Not a bug — a duplicate. The
> agent didn't do anything wrong; it just had no memory of a codebase it has
> never seen before."

Let it finish writing. The duplicate on screen is the whole argument.

---

## Shot 2 — what memory looks like (0:35–1:15)

**Screen:** `demo/anim.html`, full screen, played once.

Narrate over the five acts:

> "ArchMemory reads the source and builds a map. Functions and classes become
> nodes. Imports become edges — and relative imports resolve to real targets,
> which is what makes the next part possible."

At act 5, when the red edge appears:

> "And then the part that isn't in the code at all. `adapters` must never
> import `api`. No compiler enforces that. No file states it. It lives in
> memory — because someone recorded it."

---

## Shot 3 — Bob building it, with subagents (1:15–2:00)

**Screen:** Bob IDE, the task list, opening the capability-memory task.

**Show:** the parallel subagent dispatch, one per module.

> "Bob built ArchMemory. And Bob is part of it: one subagent per module, each in
> its own context, reading the source and writing down what each function is
> actually for."

**Cut to the measured result** — `super_len` before and after:

> "`super_len` has no docstring. Nothing to search. After Bob's pass it reads
> 'compute the remaining byte length of a stream, file, buffer or string' — and
> it goes from unfindable to the top result. Retrieval on held-out queries went
> from 1 in 12 to 3 in 12, and neighbourhood matches from 3 to 7."

State it as measured, on queries Bob never saw. That's the credibility beat.

---

## Shot 4 — the same request, with memory (2:00–2:50)

**Screen:** Bob IDE, ArchMemory connected over MCP. Same request as Shot 1.

**Show the tool calls happening:**

1. `find_capability("length of a file-like object")` → returns `super_len`,
   with `PreparedRequest.prepare_body` and `prepare_content_length` as callers
2. Bob reuses it instead of writing a new one

> "Same model, same prompt, same codebase. The only difference is that it
> asked first."

Then the governance beat — ask Bob to *"add a convenience GET to adapters.py"*:

3. `validate_diff` → **blocked**, citing LAYER-01 and the reason

> "It's not a suggestion. It's a gate, and the verdict comes from the graph,
> not from a model's opinion."

---

## Shot 5 — the numbers (2:50–3:30)

**Screen:** the A/B results table.

> "Eight tasks, run twice — Bob alone, and Bob with ArchMemory. Ground truth
> written and committed before either arm ran. The benchmark was deliberately
> kept away from Bob: if Bob had written both the system and its exam, the
> result would be worth nothing."

Read the reuse and violation columns. Do not oversell — the honesty is the
point, and a code review may follow.

---

## Shot 6 — close (3:30–4:00)

**Screen:** `demo/graph.html`, clicking `compat.py` to show the protected-component record.

> "The reason this module is protected isn't in the source. Someone knew it,
> and now the memory knows it — so the next agent, and the next engineer,
> inherit it instead of learning it the hard way.
>
> That's ArchMemory. Built with IBM Bob, and running on it."

---

## Checklist before recording

- [ ] Index is **pre-built** — never rebuild on camera
- [ ] Bob is on the `ibm-coding-challenge-uat` account
- [ ] Both arms of Shot 1 / Shot 4 rehearsed once, so the timing is known
- [ ] `demo/anim.html` and `demo/graph.html` open in tabs already
- [ ] Screen at a readable resolution — judges watch these small
- [ ] Audio checked on a 10-second test recording first

## If time runs out

Shots 1, 4 and 5 are the minimum viable video: the failure, the fix, the
numbers. Shots 2, 3 and 6 are enrichment.
