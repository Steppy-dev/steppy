"""Turn a scenario file into a plan of rewrites, without touching disk.

Splitting the plan from the write is what makes ``--dry-run`` free and the
report honest: the same code path produces both, so what the preview says is
exactly what a real run would do.

Two folds happen here rather than in :mod:`steppy.migrate.rules`, because both
need to see a statement's *neighbours*:

  * a trailing ``wait_for_timeout`` becomes the previous step's ``wait-after:``
    rather than a standalone ``wait`` step. Without it, converting the 420
    pure-wait blocks in one project would *add* 121 steps — and in a guide
    scenario every one of those is a published step with its own number and
    screenshot slot;
  * a bare ``wait_for_load_state`` folds into the ``navigate`` before it as
    ``wait:``, which is what that field already means.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .rules import parse_block

__all__ = ["MigrationPlan", "StepPlan", "plan_file", "plan_scenarios"]


@dataclass
class StepPlan:
    """One ``custom`` step and what it could become."""

    heading: str
    line: int
    code: str
    #: Other ``- key: value`` lines on the step (note, screenshot, guide-text …),
    #: preserved verbatim on every rewritten step's first sibling.
    keys: list[tuple[str, str]]
    role: Optional[str]
    steps: Optional[list[dict]] = None
    reason: str = ""

    @property
    def convertible(self) -> bool:
        return self.steps is not None


@dataclass
class MigrationPlan:
    path: Path
    steps: list[StepPlan] = field(default_factory=list)

    @property
    def convertible(self) -> list[StepPlan]:
        return [s for s in self.steps if s.convertible]

    @property
    def skipped(self) -> list[StepPlan]:
        return [s for s in self.steps if not s.convertible]


_HEADING = re.compile(r"^###\s+(.*)$")
_KEY = re.compile(r"^- ([a-zA-Z0-9_-]+):\s*(.*)$")
_BLOCK_SCALAR = {"|", "|-", "|+", ">", ">-", ">+"}
#: A value captured as a raw indented block is stored with a LEADING newline,
#: which no real `- key: value` can ever have. A sentinel string was tried
#: first and was the wrong shape: any marker that leaks reaches compile().
_NESTED = "\n"


def _fold(steps: list[dict]) -> list[dict]:
    """Apply the two neighbour-aware folds. Pure; order-preserving."""
    folded: list[dict] = []
    for step in steps:
        if step.get("action") == "_load-state":
            if folded and folded[-1].get("action") == "navigate":
                folded[-1]["wait"] = step["wait"]
                continue
            # A load-state wait with no navigation before it is a `wait-for`
            # concern, and we have no selector for one. Leave the block alone.
            return []
        if (
            step.get("action") == "wait"
            and folded
            and folded[-1].get("action") not in (None, "wait")
            and "wait_after" not in folded[-1]
        ):
            duration = str(step["duration"])
            if duration.endswith("ms") and duration[:-2].isdigit():
                folded[-1]["wait_after"] = int(duration[:-2])
                continue
        folded.append(step)
    return folded


def _parse_steps(text: str) -> list[tuple[str, int, list[tuple[str, str]]]]:
    """Every ``### `` step block as (heading, 1-based line, key/value pairs).

    Block scalars are reassembled into their key's value so a ``code:`` body
    survives intact.
    """
    lines = text.splitlines()
    blocks: list[tuple[str, int, list[tuple[str, str]]]] = []
    heading: Optional[str] = None
    start = 0
    pairs: list[tuple[str, str]] = []
    i = 0

    def flush() -> None:
        if heading is not None:
            blocks.append((heading, start, pairs))

    while i < len(lines):
        head = _HEADING.match(lines[i])
        if head:
            flush()
            heading, start, pairs = lines[i], i + 1, []
            i += 1
            continue
        if heading is None:
            i += 1
            continue
        key = _KEY.match(lines[i])
        if not key:
            i += 1
            continue
        name, value = key.group(1), key.group(2).strip()
        if value in _BLOCK_SCALAR:
            body: list[str] = []
            i += 1
            while i < len(lines) and (not lines[i].strip() or lines[i].startswith((" ", "\t"))):
                if lines[i].strip() and _KEY.match(lines[i].strip()) and not lines[i].startswith("    "):
                    break
                body.append(lines[i])
                i += 1
            indent = min((len(b) - len(b.lstrip()) for b in body if b.strip()), default=0)
            pairs.append((name, "\n".join(b[indent:] if b.strip() else "" for b in body).rstrip()))
            continue
        if not value:
            # A nested block (`guide-cursor:` / `guide-text:` / `fields:`). Kept
            # as raw indented text and re-emitted verbatim: the codemod does not
            # need to understand it, only to carry it across unharmed.
            body = []
            i += 1
            while i < len(lines) and (not lines[i].strip() or lines[i].startswith((" ", "	"))):
                body.append(lines[i])
                i += 1
            if body:
                pairs.append((name, _NESTED + "\n".join(body).rstrip()))
                continue
            pairs.append((name, ""))
            continue
        # Same unquoting the real parser applies to a simple `key: value`, so
        # the codemod reads exactly what Steppy reads. 3 984 of one project's
        # blocks are a single quoted line, not a block scalar.
        pairs.append((name, value.strip('"').strip("'")))
        i += 1
    flush()
    return blocks


def plan_file(path: Path) -> MigrationPlan:
    """What could be rewritten in one scenario file."""
    plan = MigrationPlan(path=path)
    text = path.read_text(encoding="utf-8", errors="replace")
    for heading, line, pairs in _parse_steps(text):
        as_dict = dict(pairs)
        if as_dict.get("action") != "custom":
            continue
        code = as_dict.get("code", "")
        # `role` is carried on StepPlan and written first on every fragment;
        # leaving it in `extra` too would emit it twice.
        other = [(k, v) for k, v in pairs if k not in ("action", "code", "role")]
        entry = StepPlan(heading=heading, line=line, code=code, keys=other, role=as_dict.get("role"))
        parsed = parse_block(code) if code.strip() else None
        if parsed is None:
            entry.reason = "a statement matched no rule"
        else:
            folded = _fold(parsed)
            if not folded:
                entry.reason = "a load-state wait with no navigation before it"
            else:
                entry.steps = folded
        plan.steps.append(entry)
    return plan


def plan_scenarios(directory: Path, only: Optional[str] = None) -> list[MigrationPlan]:
    """Plans for every scenario in ``directory``, or just the one named."""
    plans = []
    for path in sorted(directory.glob("*.md")):
        if only and path.stem != only:
            continue
        plan = plan_file(path)
        if plan.steps:
            plans.append(plan)
    return plans
