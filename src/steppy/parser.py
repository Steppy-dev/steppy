"""Parser for Markdown scenario files."""

import re
import yaml
from pathlib import Path
from typing import Any, Optional

import frontmatter
from pydantic import ValidationError

from .models import Scenario, Step


class ParseError(Exception):
    """Raised when a scenario file cannot be parsed."""

    pass


def parse_scenario_file(file_path: Path) -> Scenario:
    """
    Parse a scenario Markdown file into a Scenario model.

    Args:
        file_path: Path to the .md scenario file.

    Returns:
        Parsed and validated Scenario instance.

    Raises:
        ParseError: If the file cannot be parsed or is invalid.
    """
    if not file_path.exists():
        raise ParseError(f"Scenario file not found: {file_path}")

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            post = frontmatter.load(f)
    except Exception as e:
        raise ParseError(f"Failed to read scenario file {file_path}: {e}")

    # Extract frontmatter metadata
    metadata = dict(post.metadata)
    if not metadata.get("title"):
        raise ParseError(f"Scenario {file_path} is missing required 'title' in frontmatter")

    # Parse steps from the body
    body = post.content
    steps = _parse_steps(body, file_path)

    # Create slug from filename (e.g., "cross--order-lifecycle.md" → "cross--order-lifecycle")
    slug = file_path.stem

    # Build scenario
    try:
        scenario = Scenario(**metadata, steps=steps, file_path=file_path, slug=slug)
    except ValidationError as e:
        error_msg = f"Scenario validation failed for {file_path}:\n\n"
        for error in e.errors():
            field = " -> ".join(str(x) for x in error["loc"])
            msg = error["msg"]
            error_msg += f"  Field '{field}': {msg}\n"
        error_msg += "\nHint: Check the scenario frontmatter and step definitions."
        raise ParseError(error_msg)

    return scenario


def validate_scenario_roles(scenario: Scenario, configured_roles: set[str]) -> list[str]:
    """Return human-readable errors for role references not in configured_roles.

    Checks both step.role and step.screenshot (when it is a string role,
    excluding the "all" sentinel and bool/None values). The "all" value
    means "every role" and is always accepted regardless of config.
    """
    errors: list[str] = []
    available = ", ".join(sorted(configured_roles)) or "<none>"

    for step in scenario.steps:
        step_label = f"step {step.step_number}" if step.step_number is not None else "step"

        if step.role and step.role not in configured_roles:
            errors.append(f"{step_label}: unknown role '{step.role}'. Available roles: {available}")

        if isinstance(step.screenshot, str) and step.screenshot != "all" and step.screenshot not in configured_roles:
            errors.append(f"{step_label}: unknown screenshot role '{step.screenshot}'. Available roles: {available}")

    return errors


def validate_scenario_layouts(scenario: Scenario, configured_layouts: set[str]) -> list[str]:
    """Return human-readable errors for `scenario.layouts` / `step.layouts`
    entries not in ``configured_layouts`` (the keys of ``config.screenshot.layouts``).

    No-op when neither the scenario nor any step declares `layouts`. When
    `configured_layouts` is empty, any layout reference is rejected with a hint
    that points to the config block."""
    errors: list[str] = []
    available = ", ".join(sorted(configured_layouts)) or "<none configured>"

    for layout in scenario.layouts or []:
        if layout not in configured_layouts:
            errors.append(
                f"scenario: unknown layout '{layout}'. "
                f"Available layouts: {available}. "
                f"Declare layouts under `screenshot.layouts` in config.yaml."
            )

    for step in scenario.steps:
        if not step.layouts:
            continue
        step_label = f"step {step.step_number}" if step.step_number is not None else "step"
        for layout in step.layouts:
            if layout not in configured_layouts:
                errors.append(
                    f"{step_label}: unknown layout '{layout}'. "
                    f"Available layouts: {available}. "
                    f"Declare layouts under `screenshot.layouts` in config.yaml."
                )

    return errors


# ── Selector lint (FLAWS §6.3) ────────────────────────────────────────────────
# Static, browser-free heuristics that flag brittle selectors. These are
# *warnings*, never errors: they never change `validate`'s exit code — the point
# is to nudge authors toward stable `data-testid`/role locators, not to block.

# Positional pseudo-classes couple a selector to sibling order — the first thing
# to break when markup is reordered.
_POSITIONAL_RE = re.compile(
    r":(?:nth-child|nth-of-type|nth-last-child|nth-last-of-type|"
    r"first-child|last-child|first-of-type|last-of-type|only-child)\b",
    re.IGNORECASE,
)
# Playwright's text engine ties a selector to visible copy — breaks on i18n and
# wording changes.
_TEXT_COUPLED_RE = re.compile(r":has-text\(|>>\s*text=|(?:^|\s)text=", re.IGNORECASE)
# More than this many descendant hops is a fragile deep chain.
_MAX_DESCENDANT_HOPS = 3


def _count_top_level_parts(selector: str) -> int:
    """Count the selector parts separated by combinators at bracket/paren depth 0.

    Whitespace **and** the ``>`` / ``+`` / ``~`` combinators are all separators;
    runs of them collapse to a single boundary (so ``div > span`` is 2 parts, not
    3, and ``div>span`` is also 2). Anything inside ``[...]`` / ``(...)`` is
    ignored, so ``[data-testid~="x"]`` and ``:nth-child(2n+1)`` — whose ``~`` / ``+``
    live inside brackets/parens — don't inflate the count.
    """
    depth = 0
    parts = 0
    in_sep = True  # leading position acts like a preceding separator
    for ch in selector.strip():
        if depth == 0 and (ch.isspace() or ch in ">+~"):
            in_sep = True
            continue
        if in_sep:
            parts += 1
            in_sep = False
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(0, depth - 1)
    return parts


def _lint_one_selector(selector: str) -> list[str]:
    """Return brittleness warnings for a single CSS/Playwright selector string."""
    reasons: list[str] = []
    if _POSITIONAL_RE.search(selector):
        reasons.append("positional pseudo-class (breaks when siblings reorder)")
    if _TEXT_COUPLED_RE.search(selector):
        reasons.append("text-coupled selector (breaks on i18n/wording changes)")
    parts = _count_top_level_parts(selector)
    if parts - 1 > _MAX_DESCENDANT_HOPS:
        reasons.append(f"deep descendant chain ({parts} parts)")
    elif parts >= 2 and not any(c in selector for c in ".#["):
        # A multi-part chain of bare tags/combinators with no data-testid, class,
        # or id anchor rides entirely on document shape.
        reasons.append("structure-only selector (no data-testid/class/id anchor; breaks when markup changes)")
    return reasons


def lint_scenario_selectors(scenario: Scenario) -> list[str]:
    """Return brittleness *warnings* for the selectors a scenario's steps use.

    Inspects every author-written CSS surface that flows verbatim into a generated
    locator call: ``step.selector``, ``step.within``, the ``guide-cursor.target``
    override, and the ``from.selector`` of a ``capture from.type=dom`` step.
    ``navigate``'s ``target`` is intentionally excluded — it is a URL, not a
    selector. Every warning ends with the same nudge toward stable locators.
    Callers print these as warnings and must NOT fail on them (FLAWS §6.3).
    """
    warnings: list[str] = []
    for step in scenario.steps:
        step_label = f"step {step.step_number}" if step.step_number is not None else "step"
        fields: list[tuple[str, Any]] = [
            ("selector", step.selector),
            ("within", step.within),
        ]
        if step.guide_cursor is not None:
            fields.append(("guide-cursor.target", step.guide_cursor.target))
        if isinstance(step.from_, dict):
            fields.append(("from.selector", step.from_.get("selector")))
        for field_name, selector in fields:
            if not selector or not isinstance(selector, str):
                continue
            reasons = _lint_one_selector(selector)
            for reason in reasons:
                warnings.append(
                    f"{step_label}: brittle {field_name} '{selector}' — {reason}; "
                    f"prefer a data-testid or role-based locator"
                )
    return warnings


#: Above this, a scenario ``timeout:`` reads like a whole-test limit (ms).
_SCENARIO_TIMEOUT_WARN_MS = 60_000


def lint_scenario_timeout(scenario: Scenario) -> list[str]:
    """Return a *warning* when the scenario ``timeout:`` is large.

    Since 0.3.0 that value is the default timeout of **every step** (it did
    nothing before), but it reads like a limit for the whole test, so projects
    set minutes — and then every failing step waits that long. Callers print
    these as warnings and must NOT fail on them (FLAWS §6.3).
    """
    timeout = scenario.timeout
    if timeout is None or timeout <= _SCENARIO_TIMEOUT_WARN_MS:
        return []
    return [
        f"timeout: {timeout} applies to every step, not to the whole scenario, so a failing step "
        f"waits up to {timeout / 1000:g}s; set timeout: on the slow steps instead"
    ]


def lint_exclusive_groups(scenarios: list[Scenario]) -> list[str]:
    """Return *warnings* for exclusion groups with a single member.

    The one cross-scenario validator here — ``validate_scenario_roles`` and
    ``validate_scenario_layouts`` each judge one file, but a group only means
    anything relative to the others.

    A typo (``stripe-sandbox`` on one file, ``stripe-sandbx`` on another) is legal
    on both, produces two one-member groups, and silently serialises nothing —
    the *dangerous* direction, since the constraint evaporates rather than
    over-applying. It cannot be an error, because narrowing a selection with
    ``--tag`` legitimately leaves a group with one member. Callers print these as
    warnings and must NOT fail on them (FLAWS §6.3).
    """
    members: dict[str, list[str]] = {}
    for scenario in scenarios:
        if scenario.exclusive:
            members.setdefault(scenario.exclusive, []).append(scenario.slug or "?")
    return [
        f"exclusion group '{group}' has a single member ({slugs[0]}) — typo? A group of one serialises nothing."
        for group, slugs in sorted(members.items())
        if len(slugs) == 1
    ]


def _parse_steps(body: str, file_path: Path) -> list[Step]:
    """
    Parse steps from the scenario body.

    Each step is an H3 heading (###) followed by a YAML-like list of key-value pairs.
    """
    steps = []

    # Split body into step sections using ### headers
    # Pattern: ### followed by optional number and period, then the title
    step_pattern = re.compile(r"^###\s+(?:\d+\.\s+)?(.+)$", re.MULTILINE)

    matches = list(step_pattern.finditer(body))

    if not matches:
        # No steps found in body - this is valid (empty scenario)
        return steps

    for i, match in enumerate(matches):
        step_title = match.group(1).strip()
        step_number = i + 1

        # Extract the content between this heading and the next (or end of file)
        start_pos = match.end()
        end_pos = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        step_content = body[start_pos:end_pos].strip()

        # Parse the step content as YAML-like key-value pairs
        try:
            step_data = _parse_step_content(step_content, step_title, file_path)
            step_data["step_number"] = step_number
            step_data["step_title"] = step_title

            step = Step(**step_data)
            steps.append(step)
        except Exception as e:
            raise ParseError(f"Failed to parse step {step_number} ('{step_title}') in {file_path}: {e}")

    return steps


def _collect_indented_lines(lines: list[str], start: int) -> list[str]:
    """Collect a contiguous run of indented lines starting at ``lines[start]``.

    Stops at the first non-indented line (i.e. the next sibling step field at
    column 0, or end of content). Indented lines that themselves start with
    ``- `` are included — those are YAML list children of the parent key, not
    sibling step fields. Empty lines also break the run, mirroring the
    original parser semantics.
    """
    collected: list[str] = []
    j = start
    while j < len(lines):
        next_line = lines[j]
        if next_line and (next_line.startswith("  ") or next_line.startswith("\t")):
            collected.append(next_line)
            j += 1
        else:
            break
    return collected


def _structured_step_keys() -> frozenset[str]:
    """Author-facing step keys whose value is a list, dict or nested model.

    Only these may be written as a YAML *flow* collection (``[a, b]`` /
    ``{k: v}``) on the ``- key:`` line itself. The restriction is not fussiness:
    a bare attribute selector is indistinguishable from a flow sequence, and
    ``- selector: [data-testid="row"]`` is how 2 981 steps across the live
    projects are written. Parsing every bracketed value as YAML would turn each
    of them into a one-element list.

    Derived from the model so a new list-valued field works without editing the
    parser — and so a field that changes type cannot leave a stale entry here.
    """
    keys: set[str] = set()
    for name, field in Step.model_fields.items():
        annotation = str(field.annotation)
        nested_model = "GuideText" in annotation or "GuideCursor" in annotation
        if "list[" in annotation or "dict[" in annotation or nested_model:
            keys.add(field.alias or name)
    return frozenset(keys)


#: Computed once: ``Step.model_fields`` does not change at run time.
_STRUCTURED_STEP_KEYS = _structured_step_keys()


def _parse_step_content(content: str, step_title: str, file_path: Path) -> dict[str, Any]:
    """
    Parse the YAML-like key-value list under a step heading.

    Format:
    - key: value
    - key: value
    - multi-line-key:
      nested: value
      nested2: value
    """
    step_data = {}

    # Split into lines and process
    lines = content.split("\n")
    i = 0

    while i < len(lines):
        line = lines[i]

        # Skip empty lines
        if not line.strip():
            i += 1
            continue

        # A non-blank, non-indented line that isn't a "- " bullet is a
        # malformed step field (e.g. "* click:", "-click:", stray prose).
        # Raising here turns silent field-dropping into a loud, locatable error.
        if not line.strip().startswith("- "):
            raise ParseError(f"Malformed step line (expected '- key: value'): {line.strip()!r}")

        # Remove "- " prefix
        line = line.strip()[2:]

        # A bullet with no colon dropped its field silently before — surface it.
        if ":" not in line:
            raise ParseError(f"Malformed step line (expected '- key: value'): {('- ' + line)!r}")

        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()

        # YAML block scalar indicator (`|`, `|-`, `|+`, `>`, `>-`, `>+`) —
        # the actual value spans the indented lines that follow, and newline
        # handling depends on the indicator. Defer to PyYAML so we get correct
        # multiline strings for `custom` step `code`, multiline `value`, etc.
        # Without this branch the parser would store the literal indicator
        # as the value and silently drop the indented body, producing invalid
        # generated code (a stray `|` character in the test file).
        if value in ("|", "|-", "|+", ">", ">-", ">+"):
            nested_lines = _collect_indented_lines(lines, i + 1)
            if nested_lines:
                try:
                    yaml_text = f"{key}: {value}\n" + "\n".join(nested_lines)
                    parsed = yaml.safe_load(yaml_text)
                    step_data[key] = parsed[key]
                except (yaml.YAMLError, KeyError, TypeError) as e:
                    raise ParseError(f"Failed to parse block scalar value for '{key}': {e}")
                i = i + 1 + len(nested_lines)
                continue
            # No indented body — fall through and store the indicator literally
            # so validation surfaces the malformed scenario instead of swallowing it.

        # If value is empty, check if next lines are indented (nested dict or list)
        if not value:
            nested_lines = _collect_indented_lines(lines, i + 1)

            if nested_lines:
                # Use PyYAML for any nested block — handles dict children,
                # list children (`- item`), and multi-level structures alike.
                try:
                    yaml_text = f"{key}:\n" + "\n".join(nested_lines)
                    parsed = yaml.safe_load(yaml_text)
                    step_data[key] = parsed[key]
                except (yaml.YAMLError, KeyError, TypeError) as e:
                    raise ParseError(f"Failed to parse nested content for '{key}': {e}")
                i = i + 1 + len(nested_lines)
                continue

        # A YAML flow collection on the `- key:` line itself, for the keys whose
        # value is a collection. CLAUDE.md has documented `- layouts: [desktop,
        # mobile]` in five places while the parser stored the literal string
        # "[desktop, mobile]" and validation then rejected it as not-a-list.
        # Restricted to _STRUCTURED_STEP_KEYS because a bare attribute selector
        # is shaped exactly like a flow sequence.
        if key in _STRUCTURED_STEP_KEYS and (
            (value.startswith("[") and value.endswith("]")) or (value.startswith("{") and value.endswith("}"))
        ):
            try:
                step_data[key] = yaml.safe_load(value)
            except yaml.YAMLError as e:
                raise ParseError(f"Failed to parse inline value for '{key}': {e}")
            i += 1
            continue

        # Handle simple key: value — strip surrounding quotes for consistency
        # with the YAML-parsed nested blocks, which strip them too.
        step_data[key] = value.strip('"').strip("'")
        i += 1

    return step_data


def parse_scenarios_from_directory(directory: Path, tag_filter: Optional[list[str]] = None) -> list[Scenario]:
    """
    Parse all scenario files in a directory.

    Args:
        directory: Directory containing .md scenario files.
        tag_filter: Optional list of tags to filter scenarios. Only scenarios with ALL specified tags will be included.

    Returns:
        List of parsed Scenario instances.

    Raises:
        ParseError: If any scenario file fails to parse.
    """
    if not directory.exists():
        raise ParseError(f"Scenarios directory not found: {directory}")

    if not directory.is_dir():
        raise ParseError(f"Not a directory: {directory}")

    scenario_files = sorted(directory.glob("*.md"))

    if not scenario_files:
        return []

    scenarios = []
    for file_path in scenario_files:
        try:
            scenario = parse_scenario_file(file_path)

            # Apply tag filter if specified
            if tag_filter and not scenario.has_all_tags(tag_filter):
                continue

            scenarios.append(scenario)
        except ParseError:
            raise  # Re-raise parse errors
        except Exception as e:
            raise ParseError(f"Unexpected error parsing {file_path}: {e}")

    return scenarios
