"""Config-declared triage: the ``coverage.non_ui`` overlay.

Why this exists at all. The ``ui_testable | non_ui | unknown`` flags live only
in the ``triage`` table inside ``.steppy/coverage.db``, and ``.steppy/`` is
gitignored — so a fresh CI checkout has an empty store, every declared endpoint
reads ``unknown``, and health checks, webhook receivers, OAuth callbacks and
internal cron endpoints all land in the coverage denominator. A percentage
computed there is wrong for reasons the maintainer cannot fix from CI, which is
exactly the kind of number people stop trusting. ``coverage.non_ui`` makes the
declaration **committed** instead.

This module is an *overlay*, applied between ``store.read_map`` and
``gaps.compute_gaps``, and it is deliberately not either of them:

- ``read_map`` / ``dump_canonical`` are compared byte-wise by the store tests,
  and they describe what is *persisted*. A config-derived flag is not persisted.
- ``compute_gaps`` is pure bucket math over the map. Teaching it to read config
  patterns would give it two inputs of different kinds.
- Nothing here writes to the database, which preserves the invariant that merge
  and refresh never touch ``triage``.

Pattern grammar — ``[<METHOD> ]<glob>``, matched against a node's ``key``:

    /health                     any method, that path
    GET /health                 that method only
    /internal/*                 ``fnmatch``, so ``*`` **crosses** ``/``

Three consequences worth knowing rather than discovering:

1. Matching is on ``key`` alone, so a pattern applies across **every**
   configured API. That is the useful default — someone writing ``non_ui`` wants
   breadth — and ``steppy coverage triage set`` remains the surgical tool for one
   node.
2. ``*`` crossing ``/`` is intentional: ``/internal/*`` covers nested paths.
3. A pattern carrying a METHOD never matches a *route* (routes have
   ``method == ""``), because "GET" is meaningless for a page.

Precedence is a total order, most specific first::

    db "ui_testable"  >  config pattern  >  db "non_ui"  >  db "unknown" / no row

An explicit human ``ui_testable`` beats a glob: someone typed it about that one
node. A stored ``unknown`` does *not* beat the glob, because
``gaps._unreached_entry`` already collapses "no row" and ``flag="unknown"`` into
the same rendered value — so letting it override committed config would invent a
distinction the rest of the system does not make.
"""

from __future__ import annotations

import fnmatch
from typing import Iterable, Optional, Sequence

#: Value of ``triage["source"]`` for a flag that came from ``config.yaml``.
SOURCE_CONFIG = "config"

#: Value of ``triage["source"]`` for a flag that came from the sqlite store.
SOURCE_STORE = "store"

_NON_UI = "non_ui"
_UI_TESTABLE = "ui_testable"


def split_pattern(pattern: str) -> tuple[Optional[str], str]:
    """``"GET /health"`` → ``("GET", "/health")``; ``"/health"`` → ``(None, "/health")``.

    The method is upper-cased so ``get /health`` and ``GET /health`` are the same
    rule. Only the first whitespace run separates them, so a glob may contain
    spaces (rare, but a path template legally can).
    """
    head, sep, tail = pattern.strip().partition(" ")
    if not sep:
        return None, head
    return head.upper(), tail.strip()


def matches(node: dict, patterns: Sequence[str]) -> Optional[str]:
    """The first pattern in *patterns* that covers *node*, or ``None``.

    Returning the pattern rather than a bool is what lets the report and
    ``steppy coverage report`` say *which* rule discounted an endpoint — a
    number nobody can trace is a number nobody trusts.
    """
    key = node.get("key") or ""
    method = (node.get("method") or "").upper()
    for pattern in patterns:
        want_method, glob = split_pattern(pattern)
        if not glob:
            continue
        if want_method is not None:
            # Routes carry no method; a method-qualified rule cannot mean them.
            if not method or method != want_method:
                continue
        if fnmatch.fnmatchcase(key, glob):
            return pattern
    return None


def apply_config_triage(map_view: dict, patterns: Sequence[str]) -> dict:
    """``map_view`` with config-matched nodes flagged ``non_ui``.

    Returns a **new** dict; the input is untouched, because callers hand the same
    map to more than one consumer. Every node's ``triage`` comes back as either
    ``None`` or ``{"flag", "note", "source"}`` — ``source`` is additive, and
    ``gaps`` reads only ``flag``/``note``, so an older reader is unaffected.

    No patterns ⇒ the store's own flags, annotated with their source. That keeps
    one shape for downstream code instead of two.
    """
    nodes = []
    for node in map_view.get("nodes", []):
        nodes.append({**node, "triage": _resolve(node, patterns)})
    return {**map_view, "nodes": nodes}


def _resolve(node: dict, patterns: Sequence[str]) -> Optional[dict]:
    stored = node.get("triage") or None
    flag = (stored or {}).get("flag")

    if flag == _UI_TESTABLE:
        # An explicit human decision about this node outranks a glob.
        return {**stored, "source": SOURCE_STORE}

    hit = matches(node, patterns) if patterns else None
    if hit is not None:
        return {
            "flag": _NON_UI,
            "note": f"coverage.non_ui: {hit}",
            "source": SOURCE_CONFIG,
        }

    if stored is None:
        return None
    return {**stored, "source": SOURCE_STORE}


def unmatched_patterns(map_view: dict, patterns: Sequence[str]) -> list[str]:
    """Patterns that matched no node — reported, never fatal.

    A stale rule (an endpoint renamed, a service retired) silently inflates the
    coverage percentage, which is the failure mode this whole feature exists to
    prevent. Cheap to detect here, invisible everywhere else.
    """
    if not patterns:
        return []
    nodes = list(map_view.get("nodes", []))
    return [p for p in patterns if not _any_match(nodes, p)]


def _any_match(nodes: Iterable[dict], pattern: str) -> bool:
    return any(matches(node, (pattern,)) is not None for node in nodes)
