"""Render proposed steps, verify them, and write the rewritten scenario.

The render goes through the *real* generator, not a copy of it: a codemod
verified against a second implementation of codegen proves only that the two
copies agree.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .plan import _NESTED, MigrationPlan, StepPlan
from .verify import VerificationResult, verify_rewrite

__all__ = ["Outcome", "apply_plan", "render_steps", "steps_to_markdown"]

#: Order the keys are written in, so a rewritten step reads like a hand-written
#: one. Anything not listed follows, in the order the author had it.
_KEY_ORDER = [
    "role",
    "action",
    "target",
    "fn",
    "selector",
    "control",
    "within",
    "match",
    "value",
    "args",
    "kwargs",
    "state",
    "detect",
    "option",
    "expect",
    "duration",
    "timeout",
    "force",
    "reload",
    "wait",
    "store",
    "page",
    "wait_after",
]


@dataclass
class Outcome:
    step: StepPlan
    verification: Optional[VerificationResult] = None
    markdown: str = ""

    @property
    def applied(self) -> bool:
        return self.verification is not None and self.verification.ok


def _quoted_element(value) -> str:
    """A list element, always quoted when it is a string."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def _yaml_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        # Quote every string element. A bare `/settings?subscription=success` is
        # not valid flow-sequence content, and the failure is a parse error in
        # the rewritten scenario rather than anything visible here.
        return "[" + ", ".join(_quoted_element(v) for v in value) + "]"
    text = str(value)
    # Quote anything YAML would otherwise reinterpret, and anything holding the
    # separator characters a `- key: value` line is parsed with.
    if text == "" or text[0] in "[{'\"|>&*!%@`#-?," or ": " in text or text.strip() != text:
        return "'" + text.replace("'", "''") + "'"
    return text


def steps_to_markdown(steps: list[dict], heading: str, role: Optional[str], extra: list[tuple[str, str]]) -> str:
    """The scenario Markdown for one rewritten block.

    A block becoming several steps gets one heading per step, numbered
    ``N.1``/``N.2`` so the surrounding numbering is left alone — renumbering a
    whole file would bury the real change in a diff nobody can read.
    """
    lines: list[str] = []
    multiple = len(steps) > 1
    for index, step in enumerate(steps, start=1):
        title = heading if not multiple else f"{heading} ({index}/{len(steps)})"
        lines.append(title)
        if role:
            lines.append(f"- role: {role}")
        for key in _KEY_ORDER:
            if key == "role" or key not in step:
                continue
            if key == "expect" and isinstance(step[key], list):
                # The list form, one entry per assertion, each optionally
                # carrying the failure message the author wrote by hand.
                lines.append(f"- {key}:")
                for entry in step[key]:
                    if isinstance(entry, dict):
                        lines.append(f"    - check: {_yaml_value(entry['check'])}")
                        if entry.get("message"):
                            lines.append(f"      message: {_yaml_value(entry['message'])}")
                    else:
                        lines.append(f"    - {_yaml_value(entry)}")
                continue
            if isinstance(step[key], dict):
                lines.append(f"- {key}:")
                for sub, sub_value in step[key].items():
                    lines.append(f"    {sub}: {_yaml_value(sub_value)}")
                continue
            lines.append(f"- {key}: {_yaml_value(step[key])}")
        for key, value in step.items():
            if key in _KEY_ORDER or key == "role":
                continue
            lines.append(f"- {key}: {_yaml_value(value)}")
        # `note:` / `screenshot:` / `guide-text:` ride on the FIRST step only.
        # Duplicating a note onto every fragment would say the same thing three
        # times; duplicating a screenshot would capture the same frame three
        # times.
        if index == 1:
            for key, value in extra:
                if value.startswith(_NESTED):
                    lines.append(f"- {key}:")
                    lines.extend(value[len(_NESTED) :].splitlines())
                elif "\n" in value:
                    lines.append(f"- {key}: |")
                    lines.extend("    " + line for line in value.splitlines())
                else:
                    lines.append(f"- {key}: {value}")
        lines.append("")
    return "\n".join(lines)


def render_steps(generator, markdown_body: str, tmp_root: Path) -> str:
    """Generated Python for a one-step scenario holding ``markdown_body``."""
    from ..parser import parse_scenario_file

    path = tmp_root / "probe.md"
    path.write_text("---\ntitle: probe\nauth: false\n---\n\n" + markdown_body, encoding="utf-8")
    return generator.generate_test_file(parse_scenario_file(path))


def apply_plan(plan: MigrationPlan, generator, write: bool) -> list[Outcome]:
    """Verify every convertible block, and rewrite the file if asked.

    Verification renders both sides through ``generator`` — the caller's real
    ``SteppyGenerator``, configured from the project's own ``config.yaml``, so
    the comparison uses the same widget selectors the project runs with.
    """
    outcomes: list[Outcome] = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp_root = Path(tmp)
        for entry in plan.steps:
            outcome = Outcome(step=entry)
            outcomes.append(outcome)
            if not entry.convertible:
                continue
            markdown = steps_to_markdown(entry.steps, entry.heading, entry.role, entry.keys)
            try:
                generated = render_steps(generator, markdown, tmp_root)
            except Exception as exc:  # a proposed step the models reject
                outcome.verification = VerificationResult(False, f"proposed step is invalid: {exc}")
                continue
            outcome.verification = verify_rewrite(entry.code, generated, entry.steps)
            outcome.markdown = markdown

    if write and any(o.applied for o in outcomes):
        _rewrite_file(plan, outcomes)
    return outcomes


def _rewrite_file(plan: MigrationPlan, outcomes: list[Outcome]) -> None:
    """Replace each verified block in place, leaving everything else byte-identical."""
    text = plan.path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    # Bottom-up, so earlier line numbers stay valid as blocks are replaced.
    for outcome in sorted((o for o in outcomes if o.applied), key=lambda o: o.step.line, reverse=True):
        start = outcome.step.line - 1
        end = start + 1
        while end < len(lines) and not lines[end].startswith("### "):
            end += 1
        replacement = outcome.markdown.rstrip("\n") + "\n\n"
        lines[start:end] = [replacement]
    plan.path.write_text("".join(lines), encoding="utf-8")
