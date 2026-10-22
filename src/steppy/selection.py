"""Shared scenario / locale / layout selection logic (FLAWS §8.3).

The single-worker ``steppy run`` path (``cli.run``) and the parallel path
(``coordinator.run_parallel``) both have to answer the same three questions from
the same flags: *which scenarios* does ``--scenario`` / ``--tag`` / ``--guide`` /
``--no-guide`` select, and *which locales / layouts* does ``--locales`` /
``--layouts`` / ``--guide`` parametrize over. Historically each path re-implemented
that decision, kept equivalent only by the parallel integration tests — a drift here
would make ``--workers N`` silently run a **different scenario set** than
``--workers 1``. This module is the one home for the decision; each caller keeps its
own *projection* (cli builds ``test_<slug>.py`` paths, coordinator enqueues bare
slugs) and its own console messaging.

Kept deliberately pure — depends only on ``.parser``, ``.models`` and stdlib, no
``typer``/``rich`` — so ``cli.py`` can import it **eagerly** without tripping the
no-parallel-import guard (which blocks ``coordinator``/``worker``/``_parallel``, not
this leaf). User errors surface as :class:`SelectionError`; each caller translates it
to its own exit idiom (``typer.Exit`` / ``coordinator._SelectionExit``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from .locales import LocaleSyntaxError, normalize_locale
from .models import SteppyConfig
from .parser import ParseError, parse_scenarios_from_directory


class SelectionError(Exception):
    """A user-facing selection error (bad flag value, missing file/dir, parse
    failure). Carries the primary ``message``, the intended process ``exit_code``
    (always 1 today), and an optional ``hint`` line (may contain rich markup — only
    printed by callers that render markup, i.e. never through cli's markup-escaping
    ``_error``)."""

    def __init__(self, message: str, *, exit_code: int = 1, hint: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code
        self.hint = hint


def _split_csv(value: str, flag: str, what: str) -> list[str]:
    """Split a comma-separated flag value, stripping blanks. Raises
    ``SelectionError`` when nothing survives (mirrors the historical
    ``--<flag> did not contain any <what>`` message in both callers)."""
    out = [item.strip() for item in value.split(",") if item.strip()]
    if not out:
        raise SelectionError(f"--{flag} did not contain any {what}")
    return out


def apply_guide_defaults(guide: bool, screenshots: bool, no_asserts: bool) -> tuple[bool, bool]:
    """Resolve the flags ``--guide`` implies. Returns ``(screenshots, no_asserts)``.

    Guide mode captures screenshots by definition, and forces ``--no-asserts``
    because text assertions (toasts, row-contains) are language-specific and
    would fail every non-default locale during multilingual capture.

    Lives here, beside :func:`resolve_locales` / :func:`resolve_layouts` — the
    other two things ``--guide`` implies — so the whole "what does guide mode
    mean" contract is in one module. It is shared by ``cli.run``, ``cli.rerun``
    and ``coordinator.run_parallel``, which previously each spelled the rule
    out; a fourth implication added to only some of them would silently make
    the parallel path diverge from the single-worker path.
    """
    if guide:
        return True, True
    return screenshots, no_asserts


def resolve_locales(explicit: Optional[str], guide: bool, config: Optional[SteppyConfig]) -> list[str]:
    """Resolve the locale parametrization list.

    Explicit ``--locales`` wins (CSV split; error if it yields nothing); else
    ``--guide`` pulls ``config.locales``; else empty (no parametrization).
    ``config=None`` in guide mode yields ``[]`` — that is how cli models its
    "config failed to load, degrade to empty" path.
    """
    if explicit:
        codes = _split_csv(explicit, "locales", "locale codes")
        # Each code becomes a screenshot directory segment that ``steppy build``
        # reads back, and the build side rejects a malformed tag — so accepting
        # one here only defers the failure to a place that cannot explain it.
        for code in codes:
            try:
                normalize_locale(code)
            except LocaleSyntaxError as exc:
                raise SelectionError(f"--locales: {exc}") from exc
        return codes
    if guide and config is not None:
        return list(config.locales or [])
    return []


def resolve_layouts(explicit: Optional[str], guide: bool, config: Optional[SteppyConfig]) -> list[str]:
    """Resolve the layout parametrization list — mirror of :func:`resolve_locales`
    over ``config.screenshot.layouts``."""
    if explicit:
        return _split_csv(explicit, "layouts", "layout names")
    if guide and config is not None:
        return list(config.screenshot.layouts.keys())
    return []


def select_scenario_slugs(
    *,
    scenarios_dir: Path,
    tests_dir: Path,
    scenario: Optional[str],
    tag: Optional[list[str]],
    guide: bool,
    no_guide: bool,
) -> list[str]:
    """Return the selected scenario slugs for ``(scenario, tag, guide, no_guide)``.

    Order: a single ``scenario`` (verifying its generated test file exists) wins;
    otherwise parse ``scenarios_dir`` filtered by ``tag`` (``None`` = all), then keep
    guide-enabled (``guide``) or non-guide (``no_guide``) scenarios. Returns slugs in
    parse order. Raises :class:`SelectionError` on a missing test file / missing
    scenarios dir / parse error instead of printing — the caller renders it.
    """
    if scenario:
        test_file = tests_dir / f"test_{scenario}.py"
        if not test_file.exists():
            raise SelectionError(
                f"Test file not found: {test_file}",
                hint="Run [bold]steppy generate[/bold] first",
            )
        return [scenario]

    if not scenarios_dir.exists():
        raise SelectionError(f"Scenarios directory not found: {scenarios_dir}")

    try:
        all_scenarios = parse_scenarios_from_directory(scenarios_dir, tag_filter=tag)
    except ParseError as e:
        raise SelectionError(f"Parse error: {e}")

    if guide:
        all_scenarios = [s for s in all_scenarios if s.guide.enabled]
    elif no_guide:
        all_scenarios = [s for s in all_scenarios if not s.guide.enabled]

    return [s.slug for s in all_scenarios]


def exclusive_groups_for(scenarios_dir: Path, slugs: list[str]) -> dict[str, Optional[str]]:
    """Map each selected slug to its ``exclusive:`` group, or None.

    Only slugs carrying a group appear in the result; the caller reads it with
    ``.get(slug)``. Returns ``{}`` when fewer than two scenarios are selected —
    exclusivity is vacuous then, and it keeps the ``--scenario X`` path from newly
    parsing the whole directory (where a broken *sibling* file would start failing
    a run that works today).

    **Fails closed.** A parse failure raises :class:`SelectionError` rather than
    degrading to ``{}``, unlike its neighbours in ``coordinator`` (which return an
    empty set on ``ParseError`` and swallow ordering failures wholesale). Those are
    optimisations; this is a correctness constraint, and silently losing it runs the
    mutually-exclusive scenarios concurrently with nothing printed — precisely the
    bug the field exists to prevent. Being strict is cheap: for any selection this
    matters for, ``select_scenario_slugs`` parsed the same directory successfully
    moments earlier.
    """
    if len(slugs) < 2:
        return {}
    if not scenarios_dir.exists():
        raise SelectionError(f"Scenarios directory not found: {scenarios_dir}")

    try:
        parsed = parse_scenarios_from_directory(scenarios_dir)
    except ParseError as e:
        raise SelectionError(
            f"Parse error while reading exclusion groups: {e}",
            hint="Exclusion groups cannot be resolved, and running without them "
            "would execute mutually-exclusive scenarios concurrently.",
        )

    selected = set(slugs)
    return {s.slug: s.exclusive for s in parsed if s.slug in selected and s.exclusive}
