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


#: pytest's gutter: what follows is the error's own text, not source code.
_GUTTER = re.compile(r"^E(?:\s+|$)")

#: ``TimeoutError: Page.click: …``, ``playwright._impl._errors.Error: …``, or the bare
#: name of one: where pytest puts what went wrong.
_NAMED = re.compile(
    r"(?:[A-Za-z_]\w*\.)*(\w*(?:Error|Exception|Failure|Failed|Timeout|Interrupt|Exit))(?::\s+(\S.*)|:?\s*)$"
)

#: pytest's last line, ``tests/e2e/test_pay.py:41: AssertionError``: only its type says
#: anything, and its path is the machine's.
_LOCATION = re.compile(r"(?:.*[\\/])?[^\\/:\s]+\.py:\d+:\s+(?:[A-Za-z_]\w*\.)*([A-Za-z_]\w*)\s*$")

#: What Steppy's generated test prints around each step, and the banners above them.
_GENERATED = re.compile(r"^>?\s*(?:_steppy_\w+\(|#)")

#: The lines of a Playwright call log that say it tried, not why it could not.
_CALL_LOG_STEP = re.compile(
    r"^(?:attempting|retrying|scrolling|performing|navigating|done|waiting \d|waiting for element to be"
    r"|locator resolved to|fill\(|Call log:)",
    re.IGNORECASE,
)
_CALL_LOG_BULLET = re.compile(r"^(?:\d+\s*×\s*|-\s+)+")

# Absolute paths, for :func:`without_paths`. A run report is published, and a headline is
# the part of a failure everybody reads: it says what broke, never where the checkout is.
_FILE_URL = re.compile(r"file://[^\s'\"<>]*/")
_WINDOWS_PATH = re.compile(
    r"(?<![\w])(?:[A-Za-z]:\\+(?:[^\\/:*?\"'<>|\r\n]+\\+)*|[A-Za-z]:/+(?:[^\\/:*?\"'<>|\s]+/+)*)"
)
_UNC_PATH = re.compile(r"(?<![\w\\:])\\\\+(?:[^\\/:*?\"'<>|\s]+\\+)+")
#: A POSIX path under a directory only a machine has, or one that ends in a source file.
_POSIX_MACHINE = re.compile(
    r"(?<![\w/:.~-])/(?:home|Users|root|tmp|private|var|opt|usr|etc|mnt|media|proc|builds|__w)/"
    r"(?:[^\s/'\"<>()\[\]{},;:|]+/)*"
)
_POSIX_SOURCE = re.compile(
    r"(?<![\w/:.~-])/(?:[^\s/'\"<>()\[\]{},;:|]+/)+(?=[^\s/'\"<>()\[\]{},;:|]+\.(?:py|pyc|so|js|ts)\b)"
)
#: One under a directory an app may have too, or any other: only where the line is not
#: about an address of the app.
_POSIX_SHARED = re.compile(
    r"(?<![\w/:.~-])/(?:srv|build|workspaces?|github|runner|steppy|data|app|code|repo|src)/"
    r"(?:[^\s/'\"<>()\[\]{},;:|]+/)*"
)
_POSIX_PATH = re.compile(r"(?<![\w/:.~=@-])/(?:[^\s/'\"<>()\[\]{},;:|]+/)+")
_ABOUT_AN_ADDRESS = re.compile(r"\burls?\b|https?:|navigat|goto|\bhref\b|\broute|locator|xpath|selector", re.IGNORECASE)


def without_paths(line: str) -> str:
    """*line* with every absolute path reduced to its last part.

    Windows drives and UNC paths, ``file:`` addresses, and POSIX paths. An address of
    the application (``/checkout/done``) is not a path of the machine: where the line is
    about one, only a path no address begins like is reduced.
    """
    line = _FILE_URL.sub("", line)
    line = _WINDOWS_PATH.sub("", line)
    line = _UNC_PATH.sub("", line)
    line = _POSIX_MACHINE.sub("", line)
    line = _POSIX_SOURCE.sub("", line)
    if _ABOUT_AN_ADDRESS.search(line):
        return line
    return _POSIX_PATH.sub("", _POSIX_SHARED.sub("", line))


def _named(text: str) -> str:
    """``TimeoutError: Page.click: …`` when *text* is an error with its message, else nothing."""
    match = _NAMED.match(text)
    return f"{match.group(1)}: {match.group(2)}" if match and match.group(2) else ""


def _named_in_gutter(lines: list[tuple[bool, str]]) -> str:
    """The error the last block of gutter lines opens with.

    The last block, because a chained error ends with the one that failed the test; its
    first line, because what follows is that error's own detail, which may name another
    (Playwright's ``Error: element(s) not found`` under an ``AssertionError``).
    """
    found, in_block = "", False
    for gutter, text in lines:
        if not gutter:
            in_block = False
            continue
        named = "" if in_block else _named(text)
        if named:
            found, in_block = named, True
    return found


def headline(tail: str, max_len: int = 100) -> str:
    """The single most informative line of a failure tail, for the group label.

    In order: the error pytest names in its gutter (``E   AssertionError: …``), the
    assertion it rewrote (``E   assert 1 == 2``), an error named anywhere else, the type
    pytest ends with beside the last thing its detail says, then the last line that
    speaks. Never a line of the generated test, and no absolute path
    (:func:`without_paths`). It labels a group and takes no part in making one: the
    fingerprint reads the whole tail.
    """
    lines = []
    for raw in tail.splitlines():
        text = raw.strip()
        gutter = _GUTTER.match(text)
        if gutter:
            text = text[gutter.end() :].strip()
        if text:
            lines.append((bool(gutter), text))
    if not lines:
        return "(no failure detail captured)"

    chosen = (
        _named_in_gutter(lines)
        or next((t for g, t in lines if g and t.startswith("assert ")), "")
        or next((named for _g, t in reversed(lines) if (named := _named(t))), "")
    )
    if not chosen:
        kind = next((m.group(1) for _g, t in reversed(lines) if (m := _LOCATION.match(t) or _NAMED.match(t))), "")
        # A long call log pushed the message out of the tail: the last reason it gives.
        details = [_CALL_LOG_BULLET.sub("", t) for g, t in lines if g]
        detail = next((d for d in reversed(details) if d and not _CALL_LOG_STEP.match(d) and not _NAMED.match(d)), "")
        chosen = f"{kind}: {detail}" if kind and detail else kind or detail
    if not chosen:
        spoken = [t for _g, t in lines if not _GENERATED.match(t)]
        chosen = next(
            (t for t in reversed(spoken) if re.search(r"\b(?:Error|Exception|assert|Timeout|Failed)\b", t)),
            spoken[-1] if spoken else "",
        )
    chosen = without_paths(chosen).strip()
    if not chosen:
        return "(no failure detail captured)"
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
