"""Exclusion groups — scenarios that must never run concurrently.

A scenario opts in with a frontmatter ``exclusive: <name>``. Two scenarios naming
the same group never run at the same time under ``steppy run --workers N``, while
different groups still run concurrently and ungrouped scenarios are untouched.

This module is the one home for two things:

  * the **group-name grammar**, because the name becomes a filename component
    (``claims/<task_id>.claim`` is built by string concatenation), and
  * the **partition**, which collapses each group into a single queue task.

Mutual exclusion is therefore a property of how the queue was *seeded* — decided
once, single-threaded, on the host, before any container exists — rather than a
run-time protocol. There is no lock, so nothing leaks when a worker is killed, no
lease to renew, no timeout to tune, and no worker ever waits on another. The only
run-time primitive involved is the O_EXCL claim the queue already performs.

Stdlib-only leaf: it imports nothing from ``steppy``, so ``models.py`` can import
it on the eager CLI path (like ``assertions`` and ``durations``) without tripping
the no-parallel-import guard, while ``_parallel/queue.py`` and ``coordinator.py``
import the same module lazily.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


class ExclusiveGroupSyntaxError(ValueError):
    """Raised when a scenario's ``exclusive:`` value is not a usable group name."""


# The group name becomes a path component under the run's coordination directory,
# so this pattern is a security control and not a style rule: it rejects ``..``,
# both separators, leading dots, and anything else that could escape the run's
# tmp dir with the CI runner's privileges. Lowercase-only is equally load-bearing
# — the coordination directory is created on the HOST, and NTFS and APFS are
# case-insensitive by default, so `Payments` and `payments` would collapse into
# one claim file and one of the two batches would silently never run.
GROUP_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._+-]{0,63}$")
MAX_GROUP_NAME_LEN = 64

# Reserved on Windows for the basename *before* the extension, so `con.claim` is
# un-creatable just as `con` is. Cheap to reject here; baffling to diagnose when
# it surfaces as a Docker error on one developer's machine and nowhere in CI.
_WINDOWS_RESERVED = frozenset(
    {"con", "prn", "aux", "nul"} | {f"com{i}" for i in range(1, 10)} | {f"lpt{i}" for i in range(1, 10)}
)

# Naming the combination of two resources does NOT exclude a scenario against one
# named after a single resource: 'stripe-and-mailbox' and 'stripe' are two group
# names, so they are two tasks and they run concurrently. The only safe reading of
# a one-name-per-scenario model is transitive, so say so rather than suggesting a
# fusion that produces exactly the bug the field exists to prevent.
_CLOSURE_RULE = (
    "a scenario belongs to at most one exclusion group. If a flow contends on two "
    "shared resources, every scenario touching EITHER resource must use the SAME "
    "group name — naming the combination (e.g. 'stripe-and-mailbox') does not "
    "exclude it against a scenario named 'stripe', so the two would still run "
    "concurrently"
)


def normalize_group_name(value: object) -> str:
    """Validate one ``exclusive:`` frontmatter value and return the group name.

    Raises :class:`ExclusiveGroupSyntaxError` with an actionable message for every
    rejection. A list or dict is refused outright rather than being fused, because
    the fusion an author would reach for is unsound — see ``_CLOSURE_RULE``.
    """
    if isinstance(value, (list, tuple, set, dict)):
        raise ExclusiveGroupSyntaxError(f"'exclusive' takes a single group name, not a list: {_CLOSURE_RULE}")
    if not isinstance(value, str):
        raise ExclusiveGroupSyntaxError(f"'exclusive' must be a string group name, got {type(value).__name__}")

    name = value.strip()
    if not name:
        raise ExclusiveGroupSyntaxError("'exclusive' must be a non-empty group name (or omitted)")
    if len(name) > MAX_GROUP_NAME_LEN:
        raise ExclusiveGroupSyntaxError(
            f"exclusion group name is too long ({len(name)} > {MAX_GROUP_NAME_LEN}): {name!r}"
        )
    if not GROUP_NAME_RE.match(name):
        raise ExclusiveGroupSyntaxError(
            f"invalid exclusion group name {name!r}. Use lowercase letters, digits, "
            "'.', '_', '+' or '-', starting with a letter or digit. The name becomes "
            "a filename in the run's coordination directory, and uppercase is rejected "
            "because Windows and macOS filesystems would silently merge it with a "
            "lowercase twin."
        )
    if name in _WINDOWS_RESERVED:
        raise ExclusiveGroupSyntaxError(
            f"exclusion group name {name!r} is a reserved device name on Windows and "
            "cannot be used as a filename there. Pick another name."
        )
    return name


@dataclass(frozen=True)
class Task:
    """One unit of work in the queue.

    ``task_id`` is the key used for ``queue/NNNN-<task_id>.task`` and
    ``claims/<task_id>.claim``: the bare slug for an ungrouped scenario, the group
    name for a batch. ``slugs`` is the execution order — always exactly one entry
    when ``group`` is None, which is what makes an ungrouped run byte-identical to
    the pre-feature queue.
    """

    task_id: str
    slugs: tuple[str, ...]
    group: Optional[str] = None

    @property
    def is_batch(self) -> bool:
        return self.group is not None


def partition(ordered_slugs: list[str], group_of: dict[str, Optional[str]]) -> list[Task]:
    """Collapse each exclusion group into one :class:`Task`, preserving order.

    An ungrouped slug becomes its own task; a grouped slug is appended to its
    group's task, which occupies the position of the group's **first** member.
    Members keep selection order inside the batch — deterministic and debuggable;
    ordering them by duration would buy nothing, since a batch's total is fixed.

    With no groups this returns one singleton task per slug in the same order,
    which is what keeps the seeded queue directory byte-identical.

    Grouped and ungrouped entries are keyed separately, so a group named after an
    *ungrouped* scenario produces two tasks that share an id rather than one task
    that quietly swallows the loose scenario. That distinction is load-bearing:
    merging them would leave a task whose ``group`` is None but whose ``slugs``
    holds two entries, which ``seed`` writes with no payload — and the second
    scenario would then never run, and be reported nowhere.
    :func:`duplicate_task_ids` is what turns the collision into an error.
    """
    order: list[tuple[bool, str]] = []
    members: dict[tuple[bool, str], list[str]] = {}

    for slug in ordered_slugs:
        group = group_of.get(slug)
        key = (True, group) if group else (False, slug)
        if key not in members:
            members[key] = []
            order.append(key)
        members[key].append(slug)

    return [
        Task(task_id=name, slugs=tuple(members[(grouped, name)]), group=name if grouped else None)
        for grouped, name in order
    ]


def duplicate_task_ids(tasks: list[Task]) -> list[tuple[str, str]]:
    """Task ids that collide once case is folded, as ``(first, duplicate)`` pairs.

    Exact-string uniqueness is not enough: the coordination directory lives on the
    host, and on a case-insensitive filesystem two ids differing only in case share
    one claim file — the second worker's O_EXCL fails, it concludes the queue is
    drained, and a whole task silently never runs while the run still exits 0.

    Checking the resulting *id set* rather than "group name versus slug" is also
    what keeps ``exclusive: checkout`` legal on a scenario slugged ``checkout``,
    which is the most natural name there is.
    """
    seen: dict[str, str] = {}
    dupes: list[tuple[str, str]] = []
    for task in tasks:
        key = task.task_id.casefold()
        if key in seen:
            dupes.append((seen[key], task.task_id))
        else:
            seen[key] = task.task_id
    return dupes
