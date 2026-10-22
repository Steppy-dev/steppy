"""Group failures by root cause instead of listing them one by one.

With hundreds of scenarios, one broken selector shows up as forty stack traces.
Reading them means scrolling past thirty-nine copies of the same information to
discover there were only three real problems. This module collapses that: it
normalizes each failure tail into a shape that ignores run-specific noise
(addresses, timings, ids, paths) and hashes it, so identical causes land in one
bucket.

The normalizer is deliberately aggressive. A false merge costs the reader one
extra glance at the grouped list; a false split puts them back where they
started. Everything here is pure — no I/O, no Playwright, no pytest — so the
substitutions can be unit-tested directly against real tails.

Lazy-imported from the renderer, like the rest of ``_parallel``.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field


# Order matters: the more specific patterns run before the general ones, so a
# `0x7f…` address isn't first mangled into `<num>x<num>`.
_SCRUBBERS: tuple[tuple[re.Pattern, str], ...] = (
    # Windows + POSIX absolute paths → keep only the basename.
    (re.compile(r"(?:[A-Za-z]:)?[\\/](?:[\w.\-]+[\\/])+([\w.\-]+\.py)"), r"<path>/\1"),
    # ISO-ish timestamps and clock times.
    (re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?"), "<ts>"),
    (re.compile(r"\b\d{2}:\d{2}:\d{2}(?:[.,]\d+)?\b"), "<time>"),
    # UUIDs, then long hex blobs (object addresses, sha digests, container ids).
    (re.compile(r"\b[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}\b"), "<uuid>"),
    (re.compile(r"\b0x[0-9a-fA-F]+\b"), "<addr>"),
    (re.compile(r"\b[0-9a-fA-F]{12,}\b"), "<hex>"),
    # Durations pytest/playwright print in messages ("Timeout 30000ms exceeded").
    (re.compile(r"\b\d+(?:\.\d+)?\s*(?:ms|s|sec|seconds|m)\b"), "<dur>"),
    # Source line references: `foo.py:123` and pytest's `E   File "...", line 12`.
    (re.compile(r"(\.py)[:\"']?,?\s*line\s+\d+", re.IGNORECASE), r"\1:<line>"),
    (re.compile(r"(\.py):\d+"), r"\1:<line>"),
    # Ports and any remaining bare integers. Last, so the specific rules win.
    (re.compile(r":\d{2,5}\b"), ":<port>"),
    (re.compile(r"\b\d+\b"), "<n>"),
)

_WS = re.compile(r"[ \t]+")


def normalize_tail(tail: str) -> str:
    """Reduce a failure tail to its run-independent shape.

    Strips leading pytest gutter markers (``E   ``), scrubs run-specific values,
    and collapses whitespace. Two failures with the same cause but different
    timings / element ids / worker paths normalize to the same string.
    """
    if not tail:
        return ""
    lines: list[str] = []
    for raw in tail.splitlines():
        line = raw.strip()
        if not line:
            continue
        # pytest prefixes assertion detail with `E   `; drop it so the same
        # error reported in different phases still matches.
        if line.startswith("E ") or line == "E":
            line = line[1:].strip()
        for pattern, repl in _SCRUBBERS:
            line = pattern.sub(repl, line)
        lines.append(_WS.sub(" ", line))
    return "\n".join(lines)


def fingerprint(tail: str) -> str:
    """Stable short digest of a normalized tail. Empty tail ⇒ ``"unknown"``."""
    normalized = normalize_tail(tail)
    if not normalized:
        return "unknown"
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:8]


def headline(tail: str, max_len: int = 100) -> str:
    """The single most informative line of a failure tail, for the group label.

    Prefers the last line that names an exception (that is where pytest puts the
    actual error), else the last non-empty line — the bottom of a traceback is
    almost always more useful than the top.
    """
    lines = [ln.strip().lstrip("E").strip() for ln in tail.splitlines() if ln.strip()]
    if not lines:
        return "(no failure detail captured)"
    chosen = next(
        (ln for ln in reversed(lines) if re.search(r"\b(?:Error|Exception|assert|Timeout|Failed)\b", ln)),
        lines[-1],
    )
    return chosen if len(chosen) <= max_len else chosen[: max_len - 1] + "…"


@dataclass
class FailureGroup:
    """One distinct cause and every scenario that hit it."""

    fingerprint: str
    headline: str
    # (slug, nodeid, tail) — the first entry is the exemplar shown in full.
    occurrences: list[tuple[str, str, str]] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.occurrences)

    @property
    def slugs(self) -> list[str]:
        seen, out = set(), []
        for slug, _nodeid, _tail in self.occurrences:
            if slug not in seen:
                seen.add(slug)
                out.append(slug)
        return out


def group_failures(rows) -> list[FailureGroup]:
    """Bucket ``_SummaryRow``-ish objects by failure cause, biggest group first.

    Each row needs ``.slug`` and ``.fail_tails`` (a list of ``(nodeid, tail)``).
    Rows with no captured tail are grouped under a single ``unknown`` bucket
    rather than dropped — a failure with no detail is still a failure the reader
    needs to see.
    """
    groups: dict[str, FailureGroup] = {}
    for row in rows:
        tails = getattr(row, "fail_tails", None) or [("", "")]
        for nodeid, tail in tails:
            fp = fingerprint(tail)
            group = groups.get(fp)
            if group is None:
                group = FailureGroup(fingerprint=fp, headline=headline(tail))
                groups[fp] = group
            group.occurrences.append((row.slug, nodeid, tail))
    # Biggest first; ties broken by fingerprint so output is deterministic.
    return sorted(groups.values(), key=lambda g: (-g.count, g.fingerprint))
