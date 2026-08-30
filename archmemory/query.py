
"""
archmemory.query
~~~~~~~~~~~~~~~~

BM25 capability search over the nodes + capabilities tables.

Design notes
------------
* Tokens are produced by splitting snake_case and camelCase names so that
  a query like "check if ip in network" can reach ``address_in_network``.
* Dunders and container-protocol noise names (get, keys, items, update,
  content, values, ...) are skipped at tokenisation time — matching against
  them produces confident nonsense.
* The corpus is built once per call (the corpus is small: ~228 functions on
  requests).  No external services; entirely offline.
* BM25 (k1=1.5, b=0.75) is used for scoring.  Cosine TF-IDF
  over-rewarded very short documents; BM25's length normalisation fixes this
  so that rich capability descriptions rank above single-token names.
* Each result carries: location, signature, purpose, similarity, used_by.
"""

from __future__ import annotations

import math
import os
import re
import sqlite3
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .store import Store

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Names that appear everywhere and carry no semantic signal.
_NOISE: frozenset[str] = frozenset(
    {
        # container protocol
        "get",
        "set",
        "keys",
        "items",
        "values",
        "update",
        "content",
        "append",
        "extend",
        "pop",
        "insert",
        "remove",
        "clear",
        "copy",
        # misc high-frequency noise
        "return",
        "self",
        "cls",
        "none",
        "true",
        "false",
        "str",
        "int",
        "bool",
        "list",
        "dict",
        "tuple",
        "type",
        "any",
        "init",
        "new",
        "call",
        "repr",
        "len",
        "iter",
        "next",
        "enter",
        "exit",
        # very short tokens
        "s",
        "r",
        "v",
        "n",
        "o",
        "e",
        "k",
        "i",
        "x",
        "in",
        "is",
        "at",
        "to",
        "of",
        "or",
        "if",
        "as",
        "an",
        "by",
        "be",
        "do",
        "on",
        "up",
    }
)


# ---------------------------------------------------------------------------
# Tokeniser
# ---------------------------------------------------------------------------


def _tokenise(text: str) -> list[str]:
    """Split *text* into lowercase tokens, splitting snake_case and camelCase.

    Dunders (``__foo__``) and noise tokens are dropped.

    >>> _tokenise("address_in_network")
    ['address', 'network']
    >>> _tokenise("getEncodingFromHeaders")
    ['encoding', 'from', 'headers']
    >>> _tokenise("__len__")
    []
    """
    if not text:
        return []

    # Drop dunders wholesale
    if text.startswith("__") and text.endswith("__"):
        return []

    # Split camelCase: insert space before each uppercase run
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", text)

    # Replace non-alphanumeric with space
    text = re.sub(r"[^a-zA-Z0-9]+", " ", text)

    tokens = [t.lower() for t in text.split() if len(t) > 1]
    return [t for t in tokens if t not in _NOISE]


def _doc_tokens(text: str | None) -> list[str]:
    """Tokenise a multi-word text blob (docstring, purpose, etc.)."""
    if not text:
        return []
    return _tokenise(text)


# ---------------------------------------------------------------------------
# BM25 helpers
# ---------------------------------------------------------------------------

#: BM25 free parameters.
_BM25_K1: float = 1.5
_BM25_B: float = 0.75


def _term_counts(tokens: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for t in tokens:
        counts[t] = counts.get(t, 0) + 1
    return counts


def _bm25_score(
    query_tokens: list[str],
    doc_tokens: list[str],
    df: dict[str, int],
    N: int,
    avgdl: float,
) -> float:
    """BM25 score for one document against a query.

    Uses the standard Robertson/Zaragoza IDF:
        idf(t) = log((N - df(t) + 0.5) / (df(t) + 0.5) + 1)
    which is always positive regardless of df(t).
    """
    dl = len(doc_tokens)
    tf_map = _term_counts(doc_tokens)
    score = 0.0
    for t in set(query_tokens):
        tf = tf_map.get(t, 0)
        if tf == 0:
            continue
        idf = math.log((N - df.get(t, 0) + 0.5) / (df.get(t, 0) + 0.5) + 1)
        numerator = tf * (_BM25_K1 + 1)
        denominator = tf + _BM25_K1 * (1 - _BM25_B + _BM25_B * dl / avgdl)
        score += idf * (numerator / denominator)
    return score


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def _short(text: str | None) -> str | None:
    """First line of a docstring or purpose, trimmed for display."""
    if not text:
        return None
    line = text.strip().splitlines()[0].strip()
    return line[:160] or None


def _repo_rel(path: str) -> str:
    """Return a repo-relative path (forward slashes) from an absolute or relative one.

    Looks for the last occurrence of ``src/`` or ``requests/`` as an anchor.
    Falls back to the basename if neither is found.

    Examples::

        C:/archmemory/target/requests/src/requests/utils.py  ->  src/requests/utils.py
        /home/user/proj/src/requests/adapters.py             ->  src/requests/adapters.py
        src/requests/utils.py                                ->  src/requests/utils.py
    """
    # Normalise separators
    normalised = path.replace(os.sep, "/")
    # Find the last "src/" anchor
    idx = normalised.rfind("/src/")
    if idx != -1:
        return normalised[idx + 1:]  # keep "src/..."
    # Find the last "requests/" anchor (no src/ prefix)
    idx = normalised.rfind("/requests/")
    if idx != -1:
        return normalised[idx + 1:]  # keep "requests/..."
    # Already relative or unrecognised — return as-is with forward slashes
    return normalised


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class CapabilityHit:
    """One search result."""

    node_id: str
    name: str
    location: str          # path:lineno
    signature: str | None
    purpose: str | None    # from capabilities table if present, else docstring
    similarity: float
    used_by: list[str] = field(default_factory=list)



# ---------------------------------------------------------------------------
# Ego-graph expansion
# ---------------------------------------------------------------------------
#
# Lexical search matches words, so it fails whenever the caller's vocabulary
# differs from the author's -- "how many bytes will this body be" shares almost
# nothing with "determine the byte length of an object".  Measured on twelve
# held-out queries, BM25 alone put the right function in the top 3 twice.
#
# The call graph does not care about vocabulary.  Expanding one hop from the
# top hits raises the chance the right function is present from 2/12 to 8/12.
#
# Note what this is NOT: re-ranking the expanded set does not help.  Lexical
# re-scoring, a neighbour boost, and graph-consensus voting were each measured
# and all land at 2-3/12, because the same score that missed the target the
# first time pushes it back down.  So the neighbourhood is returned as
# *context* alongside the ranked hits, not merged into the ranking -- the
# agent reading the result does the final selection.  This mirrors RepoGraph
# (Ouyang et al., ICLR 2025), which flattens ego-graphs into the prompt rather
# than ranking with them, and reproduces their finding that a second hop adds
# noise without gain.


def ego_expand(
    store: "Store",
    node_ids: list[str],
    hops: int = 1,
    limit: int = 24,
) -> list[dict[str, str]]:
    """Functions within *hops* call-edges of *node_ids*, excluding the seeds.

    Ordered by how many seeds reach them, so a function several hits agree on
    surfaces before one reached from a single weak match.
    """
    conn: sqlite3.Connection = store._conn
    seeds = set(node_ids)
    frontier = set(node_ids)
    votes: dict[str, int] = {}

    for _ in range(max(0, hops)):
        nxt: set[str] = set()
        for nid in frontier:
            for row in conn.execute(
                "SELECT dst AS other FROM edges WHERE src=? AND kind='calls' "
                "UNION SELECT src AS other FROM edges WHERE dst=? AND kind='calls'",
                (nid, nid),
            ):
                other = row["other"]
                if other in seeds:
                    continue
                votes[other] = votes.get(other, 0) + 1
                nxt.add(other)
        frontier = nxt

    if not votes:
        return []

    placeholders = ",".join("?" * len(votes))
    rows = conn.execute(
        f"""SELECT n.id, n.name, n.path, n.lineno, n.signature, n.docstring,
                   c.purpose
            FROM nodes n LEFT JOIN capabilities c ON c.id = n.id
            WHERE n.id IN ({placeholders}) AND n.kind = 'function'""",
        tuple(votes),
    ).fetchall()

    out = []
    for r in rows:
        if r["name"].startswith("__") or r["name"] in _NOISE:
            continue
        out.append({
            "node_id": r["id"],
            "name": r["name"],
            "location": f"{_repo_rel(r['path'])}:{r['lineno'] or 0}",
            "signature": r["signature"],
            "purpose": _short(r["purpose"] or r["docstring"]),
            "reached_from": votes[r["id"]],
        })
    out.sort(key=lambda d: -d["reached_from"])
    return out[:limit]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def find_capability(
    description: str,
    store: "Store",
    limit: int = 5,
) -> list[CapabilityHit]:
    """Return up to *limit* functions ranked by BM25 similarity to *description*.

    Searches name + signature + docstring for every function node, enriched
    by the capabilities table when present.  Returns results sorted by
    descending similarity score.

    :param description: Natural-language description of the capability sought.
    :param store:       An open :class:`~archmemory.store.Store` instance.
    :param limit:       Maximum number of results to return (default 5).
    :rtype: list[CapabilityHit]
    """
    conn: sqlite3.Connection = store._conn

    # --- Load corpus ---
    rows = conn.execute(
        """
        SELECT n.id, n.name, n.path, n.lineno, n.signature, n.docstring,
               c.purpose, c.inputs, c.outputs, c.used_by
        FROM   nodes n
        LEFT JOIN capabilities c ON c.id = n.id
        WHERE  n.kind = 'function'
        """
    ).fetchall()

    if not rows:
        return []

    # --- Build document token lists ---
    docs: list[tuple[sqlite3.Row, list[str]]] = []
    for row in rows:
        tokens: list[str] = []
        tokens += _tokenise(row["name"])
        tokens += _doc_tokens(row["signature"])
        tokens += _doc_tokens(row["docstring"])
        if row["purpose"]:
            tokens += _doc_tokens(row["purpose"])
        if row["inputs"]:
            tokens += _doc_tokens(row["inputs"])
        if row["outputs"]:
            tokens += _doc_tokens(row["outputs"])
        docs.append((row, tokens))

    # --- Corpus statistics for BM25 ---
    N = len(docs)
    df: dict[str, int] = {}
    for _, tokens in docs:
        for t in set(tokens):
            df[t] = df.get(t, 0) + 1

    total_len = sum(len(tokens) for _, tokens in docs)
    avgdl: float = total_len / N if N else 1.0

    # --- Query tokens ---
    query_tokens = _doc_tokens(description)
    if not query_tokens:
        return []

    # --- Ceiling: the score a perfect match could reach ---
    # Raw BM25 is unbounded and corpus-dependent, which makes any absolute
    # threshold meaningless.  Dividing by the top hit is worse: it forces the
    # best match to exactly 1.0 whether or not it is any good, so nothing can
    # ever be rejected.  Instead, normalise by the sum of IDF over the query's
    # distinct tokens -- the score a document would earn by matching every
    # query term with full term-frequency saturation.  The ratio is then a
    # genuine 0-1 confidence that is comparable across queries.
    ceiling = sum(
        math.log((N - df.get(t, 0) + 0.5) / (df.get(t, 0) + 0.5) + 1)
        for t in set(query_tokens)
    ) or 1.0

    # --- Score each document ---
    scored: list[tuple[float, sqlite3.Row, list[str]]] = []
    for row, tokens in docs:
        if not tokens:
            continue
        # Clamp to 1.0: BM25's term-frequency saturation can exceed the
        # per-term IDF ceiling when a document repeats a query term, which
        # would otherwise surface a confidence above 100%.
        score = min(1.0, _bm25_score(query_tokens, tokens, df, N, avgdl) / ceiling)
        if score > 0:
            scored.append((score, row, tokens))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:limit]

    # --- Build results with used_by from call edges ---
    results: list[CapabilityHit] = []
    for score, row, _ in top:
        node_id = row["id"]
        location = f"{_repo_rel(row['path'])}:{row['lineno'] or 0}"

        # used_by: callers from edges table
        caller_rows = conn.execute(
            """
            SELECT n.id FROM nodes n
            JOIN edges e ON e.src = n.id
            WHERE e.dst = ? AND e.kind = 'calls'
            """,
            (node_id,),
        ).fetchall()
        used_by = [r["id"] for r in caller_rows]
        # fall back to capabilities.used_by if no call edges recorded
        if not used_by and row["used_by"]:
            used_by = [s.strip() for s in row["used_by"].split(",") if s.strip()]

        purpose = row["purpose"] or row["docstring"]

        results.append(
            CapabilityHit(
                node_id=node_id,
                name=row["name"],
                location=location,
                signature=row["signature"],
                purpose=purpose,
                similarity=round(score, 4),
                used_by=used_by,
            )
        )

    return results
