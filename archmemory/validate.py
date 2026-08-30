"""
archmemory.validate
~~~~~~~~~~~~~~~~~~~

Governance gate: given a unified diff, return a structured verdict.

Return value schema::

    {
        "verdict":  "ok" | "blocked",
        "findings": [...],
        "summary":  "..."
    }

Each finding is a dict with a ``check`` key (one of ``protected``,
``architecture``, ``duplicate``) plus check-specific fields.

Three checks
------------
1. **protected** -- match every changed path against ``decisions`` rows
   (scope is an fnmatch glob).  Appends the decision, reason, and risk.

2. **architecture** -- parse imports *added* by the diff, resolve relative
   imports against the changed file's module id, and check against
   ``constraints`` rows whose scope equals the file's module.
   ``constraints.rule`` is a comma-separated list of forbidden targets.

3. **duplicate** -- for each function *added* by the diff, search the
   capability index with BM25.  The top hit's score is used as a normaliser
   so all scores become relative confidences in [0, 1].  A relative score
   > 0.50 triggers a finding; only the single strongest match per added
   function is reported.  Exception: a function re-defining itself in its
   own module is an *edit*, not a duplicate.

Parsing note
------------
Added lines from a diff hunk rarely form a valid Python module; this module
falls back to regex scanning when ``ast.parse`` raises ``SyntaxError``.
"""

from __future__ import annotations

import ast
import fnmatch
import re
import sqlite3
from pathlib import PurePosixPath
from typing import Any

from .query import find_capability
from .store import Store


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

Finding = dict[str, Any]

VerdictResult = dict[str, Any]


# ---------------------------------------------------------------------------
# Diff parsing helpers
# ---------------------------------------------------------------------------


def _changed_paths(diff: str) -> list[str]:
    """Return every file path that the diff touches (b/ side)."""
    paths: list[str] = []
    for line in diff.splitlines():
        # +++ b/src/requests/adapters.py  or  +++ /dev/null
        if line.startswith("+++ "):
            rest = line[4:].strip()
            if rest == "/dev/null":
                continue
            # strip leading b/ or a/
            if rest.startswith("b/"):
                rest = rest[2:]
            elif rest.startswith("a/"):
                rest = rest[2:]
            paths.append(rest)
    return paths


def _added_lines(diff: str, path: str) -> list[str]:
    """Return lines added (``+`` prefix) for a specific file path."""
    lines: list[str] = []
    in_file = False
    for line in diff.splitlines():
        if line.startswith("+++ "):
            rest = line[4:].strip()
            for prefix in ("b/", "a/"):
                if rest.startswith(prefix):
                    rest = rest[len(prefix):]
                    break
            in_file = rest == path
            continue
        if not in_file:
            continue
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            lines.append(line[1:])
    return lines


# ---------------------------------------------------------------------------
# Module-id derivation from a file path
# ---------------------------------------------------------------------------


def _module_id_from_path(path: str) -> str:
    """Convert a file path string to a Python module id.

    Examples::

        src/requests/adapters.py  ->  requests.adapters
        requests/utils.py         ->  requests.utils
        compat.py                 ->  compat
    """
    p = PurePosixPath(path)
    p = p.with_suffix("")
    parts = list(p.parts)
    # strip common src/ prefix if present
    if parts and parts[0] == "src":
        parts = parts[1:]
    # drop __init__
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


# ---------------------------------------------------------------------------
# Import extraction from added lines
# ---------------------------------------------------------------------------


def _resolve_relative(level: int, name: str, from_module: str) -> str:
    """Resolve a relative import to an absolute module id."""
    parts = from_module.split(".")
    anchor_parts = parts[:-level] if level <= len(parts) else []
    if name:
        return ".".join(anchor_parts + [name])
    return ".".join(anchor_parts)


def _extract_added_imports(added_lines: list[str], module_id: str) -> list[str]:
    """Return absolute module ids imported by the added lines.

    Resolves relative imports against *module_id*.  De-duplicated.
    """
    src = "\n".join(added_lines)
    imports: list[str] = []
    try:
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                name = node.module or ""
                level = node.level or 0
                if level:
                    resolved = _resolve_relative(level, name, module_id)
                else:
                    resolved = name
                if resolved:
                    imports.append(resolved)
                    # `from requests import sessions` binds a MODULE, not a
                    # name inside one, so the submodule must be treated as
                    # imported too.  Without this an agent bypasses a layering
                    # rule simply by writing the import the other way round.
                    for alias in node.names:
                        imports.append(f"{resolved}.{alias.name}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
    except SyntaxError:
        # fallback: regex scan
        for line in added_lines:
            m = re.match(r"^\s*from\s+(\S+)\s+import", line)
            if m:
                raw = m.group(1)
                level = len(raw) - len(raw.lstrip("."))
                name = raw.lstrip(".")
                if level:
                    resolved = _resolve_relative(level, name, module_id)
                else:
                    resolved = raw
                if resolved:
                    imports.append(resolved)
                    names = line.split("import", 1)[1]
                    for nm in names.split(","):
                        nm = nm.strip().split(" as ")[0].strip()
                        if nm and nm.isidentifier():
                            imports.append(f"{resolved}.{nm}")
                continue
            m2 = re.match(r"^\s*import\s+(\S+)", line)
            if m2:
                imports.append(m2.group(1).rstrip(","))

    seen: set[str] = set()
    result: list[str] = []
    for imp in imports:
        if imp not in seen:
            seen.add(imp)
            result.append(imp)
    return result


# ---------------------------------------------------------------------------
# Function extraction from added lines
# ---------------------------------------------------------------------------


_RE_DEF = re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\(")


def _extract_added_functions(added_lines: list[str]) -> list[str]:
    """Return names of functions defined in the added lines.

    Tries ast first; falls back to regex when ast.parse raises SyntaxError.
    """
    src = "\n".join(added_lines)
    names: list[str] = []
    try:
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names.append(node.name)
    except SyntaxError:
        for line in added_lines:
            m = _RE_DEF.match(line)
            if m:
                names.append(m.group(1))
    return names


# ---------------------------------------------------------------------------
# The three checks
# ---------------------------------------------------------------------------


def _check_protected(
    changed_paths: list[str],
    conn: sqlite3.Connection,
) -> list[Finding]:
    """Check each changed path against the decisions table."""
    findings: list[Finding] = []
    rows = conn.execute(
        "SELECT id, scope, decision, reason, risk FROM decisions"
    ).fetchall()
    for path in changed_paths:
        filename = PurePosixPath(path).name
        for row in rows:
            if fnmatch.fnmatch(path, row["scope"]) or fnmatch.fnmatch(
                filename, row["scope"]
            ):
                findings.append(
                    {
                        "check": "protected",
                        "path": path,
                        "rule_id": row["id"],
                        "decision": row["decision"],
                        "reason": row["reason"],
                        "risk": row["risk"],
                    }
                )
    return findings


def _check_architecture(
    diff: str,
    changed_paths: list[str],
    conn: sqlite3.Connection,
) -> list[Finding]:
    """Check added imports against architecture constraints."""
    findings: list[Finding] = []
    constraints = conn.execute(
        "SELECT id, scope, rule, rationale, severity FROM constraints"
    ).fetchall()

    for path in changed_paths:
        module_id = _module_id_from_path(path)
        added = _added_lines(diff, path)
        if not added:
            continue
        imported = _extract_added_imports(added, module_id)
        if not imported:
            continue

        for row in constraints:
            if row["scope"] != module_id:
                continue
            forbidden = [t.strip() for t in row["rule"].split(",") if t.strip()]
            for imp in imported:
                for forbidden_target in forbidden:
                    if imp == forbidden_target or imp.startswith(
                        forbidden_target + "."
                    ):
                        findings.append(
                            {
                                "check": "architecture",
                                "path": path,
                                "module": module_id,
                                "imported": imp,
                                "rule_id": row["id"],
                                "rationale": row["rationale"],
                                "severity": row["severity"],
                            }
                        )
    return findings


# A new function must reach this fraction of the best achievable score
# before it is reported as a duplicate.
#
# Calibrated against the real index over ten cases -- five reimplementations
# of capabilities that already exist, five genuinely unrelated functions:
#
#     reimplementations   0.2345 .. 0.7016
#     unrelated           0.0352 .. 0.2068
#
# 0.22 sits in the gap.  The margin is narrow, so prefer a missed duplicate
# over a false one: an agent that is wrongly blocked learns to ignore the
# gate, which costs more than the occasional duplicate it lets through.
DUPLICATE_THRESHOLD = 0.22



def _function_context(added_lines: list[str], fn_name: str) -> str:
    """Name plus the body it was added with, for a richer duplicate query.

    Falls back to the bare name when the function cannot be located, which
    keeps behaviour unchanged for malformed hunks.
    """
    text = "\n".join(added_lines)
    pattern = re.compile(
        r"^\s*(?:async\s+)?def\s+" + re.escape(fn_name) + r"\s*\(", re.M)
    m = pattern.search(text)
    if not m:
        return fn_name
    lines = text[m.start():].splitlines()
    return fn_name + " " + " ".join(lines[:14])


def _check_duplicates(
    diff: str,
    changed_paths: list[str],
    store: Store,
) -> list[Finding]:
    """Check added functions against the capability index.

    BM25 scores are unbounded, so we normalise by the top candidate's score
    to produce a relative confidence in [0, 1].  Only the single strongest
    match per added function is reported (one finding per function).
    """
    findings: list[Finding] = []
    conn: sqlite3.Connection = store._conn

    for path in changed_paths:
        module_id = _module_id_from_path(path)
        added = _added_lines(diff, path)
        if not added:
            continue
        func_names = _extract_added_functions(added)
        if not func_names:
            continue

        for fn_name in func_names:
            # Search on the function's name AND the source it was added with.
            # The name alone is too thin a query: "compute_fibonacci_sequence"
            # shares only the token "sequence" with the corpus, and that one
            # token was enough to match dict_to_sequence and block a diff that
            # had nothing to do with requests.
            query = _function_context(added, fn_name)
            hits = find_capability(query, store, limit=5)
            if not hits:
                continue

            # find_capability already returns a 0-1 confidence: the raw BM25
            # score divided by the ceiling that query could reach.  Do NOT
            # renormalise against the top hit -- that would force the best
            # candidate to 1.0 regardless of quality, so nothing could ever
            # be rejected and every diff would be blocked.
            for hit in hits:
                relative = hit.similarity
                if relative <= DUPLICATE_THRESHOLD:
                    break  # sorted descending; nothing below will qualify

                # A function redefining itself in its own module is an edit
                existing_module = ".".join(hit.node_id.split(".")[:-1])
                if hit.name == fn_name and existing_module == module_id:
                    continue
                if hit.node_id.startswith(module_id + ".") and hit.name == fn_name:
                    continue

                callers = [
                    r["id"]
                    for r in conn.execute(
                        "SELECT n.id FROM nodes n "
                        "JOIN edges e ON e.src = n.id "
                        "WHERE e.dst = ? AND e.kind = 'calls'",
                        (hit.node_id,),
                    ).fetchall()
                ]
                findings.append(
                    {
                        "check": "duplicate",
                        "added_function": fn_name,
                        "path": path,
                        "existing_id": hit.node_id,
                        "existing_location": hit.location,
                        "existing_purpose": hit.purpose,
                        "similarity": round(relative, 4),
                        "callers": callers or hit.used_by,
                    }
                )
                break  # one finding per added function
    return findings


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate_diff(diff: str, store: Store) -> VerdictResult:
    """Validate *diff* against the governance rules in *store*.

    Returns::

        {
            "verdict":  "ok" | "blocked",
            "findings": [list of Finding dicts],
            "summary":  "human-readable one-liner"
        }

    All logic is deterministic: same diff, same store contents -> same verdict.
    """
    conn: sqlite3.Connection = store._conn
    paths = _changed_paths(diff)

    findings: list[Finding] = []
    findings.extend(_check_protected(paths, conn))
    findings.extend(_check_architecture(diff, paths, conn))
    findings.extend(_check_duplicates(diff, paths, store))

    verdict: str = "blocked" if findings else "ok"

    if not findings:
        summary = "No violations found."
    else:
        counts: dict[str, int] = {}
        for f in findings:
            counts[f["check"]] = counts.get(f["check"], 0) + 1
        parts = []
        if counts.get("protected"):
            parts.append(f"{counts['protected']} protected-component violation(s)")
        if counts.get("architecture"):
            parts.append(f"{counts['architecture']} architecture violation(s)")
        if counts.get("duplicate"):
            parts.append(f"{counts['duplicate']} duplicate capability(ies)")
        summary = "Blocked: " + "; ".join(parts) + "."

    return {"verdict": verdict, "findings": findings, "summary": summary}
