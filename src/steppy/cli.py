"""Steppy CLI - Main entry point."""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, List, Optional

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from .tours_cli import prepare_tours_or_exit, run_tour_checks, tours_enabled, warn_opted_in_but_disabled
from ._cli_common import (
    console,
    set_verbose as _set_verbose,
    _debug_enabled,
    _error,
    _report_unexpected,
    _cli_errors,
    _load_config_or_exit,
    reject_xdist_args,
    require_generated_tests,
    DEFAULT_SEED_TIMEOUT,
)
from . import selection
from .captures import DEFAULT_GUIDES_DIR, DEFAULT_SCREENSHOTS_DIR, resolve_output_dir
from .locales import LocaleSyntaxError, normalize_locale
from .config import load_config, ConfigError
from .parser import (
    parse_scenario_file,
    parse_scenarios_from_directory,
    validate_scenario_roles,
    validate_scenario_layouts,
    lint_exclusive_groups,
    lint_scenario_selectors,
    lint_scenario_timeout,
    ParseError,
)
from .generator import SteppyGenerator
from .runmeta import bundle_dirs, new_run_id, write_run_sidecar

# NOTE: `.guide` (jinja2 + markdown_it) and `.auth` (httpx + playwright) are
# deliberately NOT imported here. Between them they cost ~138ms of import time,
# which every `steppy` invocation would otherwise pay -- including `--help`,
# `validate`, `list` and `run`, none of which touch either module. They are
# imported inside the command bodies that need them, the same treatment
# `coordinator`/`_parallel`/`coverage` already get.
# Guard: tests/test_no_playwright_import_on_default_path.py

app = typer.Typer(
    name="steppy",
    help="Steppy - Generate Playwright tests from Markdown scenarios",
    add_completion=False,
)


@app.callback()
def _main(
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Show full tracebacks on unexpected errors",
    ),
):
    """Steppy - Generate Playwright tests from Markdown scenarios."""
    _set_verbose(verbose)


@app.command()
def validate(
    scenario: Optional[Path] = typer.Argument(
        None,
        help="Path to a specific scenario file to validate. If not provided, validates all scenarios.",
        exists=True,
    ),
    scenarios_dir: Path = typer.Option(
        Path("scenarios"),
        "--scenarios-dir",
        "-d",
        help="Directory containing scenario files",
    ),
):
    """
    Validate scenario files without generating code.

    Parses scenario files and checks for syntax and validation errors.
    """
    with _cli_errors():
        config = _load_config_or_exit()
        console.print("[green]OK[/green] Configuration loaded successfully")

        configured_roles = set(config.apps.keys())

        # Validate single scenario or all scenarios
        if scenario:
            console.print(f"\n[bold]Validating scenario:[/bold] {scenario}")
            try:
                parsed = parse_scenario_file(scenario)
            except ParseError as e:
                _error(f"Parse error: {e}")
                raise typer.Exit(1)

            role_errors = validate_scenario_roles(parsed, configured_roles)
            layout_errors = validate_scenario_layouts(parsed, set(config.screenshot.layouts.keys()))
            if role_errors or layout_errors:
                console.print("[red]ERROR[/red] Validation failed:")
                for err in role_errors + layout_errors:
                    console.print(f"  {escape(str(err))}")
                raise typer.Exit(1)

            # Selector and timeout lints — warnings only, never affect the exit code.
            for warn in lint_scenario_selectors(parsed) + lint_scenario_timeout(parsed):
                console.print(f"[yellow]WARNING[/yellow] {escape(str(warn))}")

            # In-app tours: compile checks for this scenario (silent unless it opts in).
            if run_tour_checks(config, [parsed], scenarios_dir=parsed.file_path.parent):
                raise typer.Exit(1)

            console.print("[green]OK[/green] Scenario is valid")
            console.print(f"  Title: {parsed.title}")
            console.print(f"  Steps: {len(parsed.steps)}")
            console.print(f"  Tags: {', '.join(parsed.tags) if parsed.tags else 'none'}")
            console.print(f"  Roles: {', '.join(sorted(parsed.get_roles()))}")
        else:
            console.print(f"\n[bold]Validating all scenarios in:[/bold] {scenarios_dir}")

            if not scenarios_dir.exists():
                console.print(f"[red]ERROR[/red] Scenarios directory not found: {scenarios_dir}")
                raise typer.Exit(1)

            try:
                scenarios = parse_scenarios_from_directory(scenarios_dir)
            except ParseError as e:
                _error(f"Parse error: {e}")
                raise typer.Exit(1)

            if not scenarios:
                console.print("[yellow]WARNING[/yellow] No scenario files found")
                raise typer.Exit(0)

            configured_layouts = set(config.screenshot.layouts.keys())
            had_errors = False
            for s in scenarios:
                role_errors = validate_scenario_roles(s, configured_roles)
                layout_errors = validate_scenario_layouts(s, configured_layouts)
                if role_errors or layout_errors:
                    had_errors = True
                    console.print(f"[red]ERROR[/red] {s.slug}: validation failed")
                    for err in role_errors + layout_errors:
                        console.print(f"  {escape(str(err))}")
                # Selector and timeout lints — warnings only, never affect the exit code.
                for warn in lint_scenario_selectors(s) + lint_scenario_timeout(s):
                    console.print(f"[yellow]WARNING[/yellow] {s.slug}: {escape(str(warn))}")
            # Cross-scenario lint — an exclusion group only means anything
            # relative to the other scenarios, so this runs once over the corpus
            # rather than per file. Warnings only, like the selector lint above.
            for warn in lint_exclusive_groups(scenarios):
                console.print(f"[yellow]WARNING[/yellow] {escape(str(warn))}")
            # In-app tours: compile every opted-in scenario (silent when tours are off).
            if run_tour_checks(config, scenarios, scenarios_dir=scenarios_dir):
                had_errors = True
            if had_errors:
                raise typer.Exit(1)

            console.print(f"[green]OK[/green] All {len(scenarios)} scenario(s) are valid\n")

            # Show summary
            for s in scenarios:
                console.print(f"  [green]OK[/green] {s.slug}: {s.title} ({len(s.steps)} steps)")

    console.print("\n[green]OK[/green] Validation complete")


@app.command(name="list")
def list_scenarios(
    scenarios_dir: Path = typer.Option(
        Path("scenarios"),
        "--scenarios-dir",
        "-d",
        help="Directory containing scenario files",
    ),
    tag: Optional[list[str]] = typer.Option(
        None,
        "--tag",
        "-t",
        help="Filter scenarios by tag (can be repeated, AND logic)",
    ),
):
    """
    List all scenarios with their metadata.

    Shows title, tags, priority, and step count for each scenario.
    """
    with _cli_errors():
        _load_config_or_exit()

        # Parse scenarios
        if not scenarios_dir.exists():
            console.print(f"[red]ERROR[/red] Scenarios directory not found: {scenarios_dir}")
            raise typer.Exit(1)

        try:
            scenarios = parse_scenarios_from_directory(scenarios_dir, tag_filter=tag)
        except ParseError as e:
            _error(f"Parse error: {e}")
            raise typer.Exit(1)

        if not scenarios:
            if tag:
                console.print(f"[yellow]WARNING[/yellow] No scenarios found with tag(s) '{', '.join(tag)}'")
            else:
                console.print("[yellow]WARNING[/yellow] No scenario files found")
            raise typer.Exit(0)

        # Create table
        table = Table(title=f"Scenarios{f' (filtered by tags: {", ".join(tag)})' if tag else ''}", show_lines=False)
        table.add_column("Slug", style="cyan", no_wrap=True)
        table.add_column("Title", style="bold", max_width=40)
        table.add_column("Priority", style="yellow", justify="center")
        table.add_column("Tags", style="magenta", max_width=30)
        table.add_column("Steps", justify="right", style="green")
        table.add_column("Roles", style="blue")
        table.add_column("Seed", style="dim", justify="center")
        table.add_column("Guide", justify="center")

        for scenario in scenarios:
            priority = scenario.priority or "-"
            tags_str = ", ".join(scenario.tags) if scenario.tags else "-"
            roles_str = ", ".join(sorted(scenario.get_roles())) or "-"
            if scenario.seed:
                seed_str = (
                    f"{scenario.seed.script} ({scenario.seed.run})"
                    if scenario.seed.run != "before"
                    else scenario.seed.script
                )
            else:
                seed_str = "-"

            guide_str = "[green]✓[/green]" if scenario.guide.enabled else "-"

            # Color-code priority
            priority_display = priority
            if priority == "critical":
                priority_display = f"[red]{priority}[/red]"
            elif priority == "high":
                priority_display = f"[yellow]{priority}[/yellow]"
            elif priority == "medium":
                priority_display = f"[blue]{priority}[/blue]"
            elif priority == "low":
                priority_display = f"[dim]{priority}[/dim]"

            table.add_row(
                scenario.slug,
                scenario.title,
                priority_display,
                tags_str,
                str(len(scenario.steps)),
                roles_str,
                seed_str,
                guide_str,
            )

        console.print()
        console.print(table)
        console.print()
        console.print(f"[green]Total:[/green] {len(scenarios)} scenario(s)")


@app.command()
def generate(
    scenario: Optional[Path] = typer.Argument(
        None,
        help="Path to a specific scenario file to generate. If not provided, generates all scenarios.",
    ),
    scenarios_dir: Path = typer.Option(
        Path("scenarios"),
        "--scenarios-dir",
        "-d",
        help="Directory containing scenario files",
    ),
    output_dir: Path = typer.Option(
        Path("tests/e2e"),
        "--output-dir",
        "-o",
        help="Output directory for generated test files",
    ),
    tag: Optional[list[str]] = typer.Option(
        None,
        "--tag",
        "-t",
        help="Only generate scenarios with these tags (can be repeated, AND logic)",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Overwrite existing files without checking for changes",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Show what files would be created/updated without writing anything",
    ),
):
    """
    Generate pytest test files from scenario files.

    Parses scenario .md files and generates Python test files in tests/e2e/.

    By default, shows a summary and asks for confirmation if files would change.
    Use --force to overwrite without asking, or --dry-run to preview changes.
    """
    with _cli_errors():
        config = _load_config_or_exit()
        console.print("[green]OK[/green] Configuration loaded")

        # Parse scenarios
        scenarios = []
        if scenario:
            # Single scenario
            console.print(f"\n[bold]Generating from:[/bold] {scenario}")
            if not scenario.exists():
                console.print(f"[red]ERROR[/red] Scenario file not found: {scenario}")
                raise typer.Exit(1)

            try:
                parsed = parse_scenario_file(scenario)
                scenarios = [parsed]
            except ParseError as e:
                _error(f"Parse error: {e}")
                raise typer.Exit(1)
        else:
            # All scenarios (with optional tag filter)
            if tag:
                console.print(f"\n[bold]Generating scenarios with tag(s):[/bold] {', '.join(tag)}")
            else:
                console.print(f"\n[bold]Generating all scenarios from:[/bold] {scenarios_dir}")

            if not scenarios_dir.exists():
                console.print(f"[red]ERROR[/red] Scenarios directory not found: {scenarios_dir}")
                raise typer.Exit(1)

            try:
                scenarios = parse_scenarios_from_directory(scenarios_dir, tag_filter=tag)
            except ParseError as e:
                _error(f"Parse error: {e}")
                raise typer.Exit(1)

        if not scenarios:
            console.print("[yellow]WARNING[/yellow] No scenarios to generate")
            raise typer.Exit(0)

        # Validate role and layout references against configured values
        configured_roles = set(config.apps.keys())
        configured_layouts = set(config.screenshot.layouts.keys())
        had_errors = False
        for s in scenarios:
            role_errors = validate_scenario_roles(s, configured_roles)
            layout_errors = validate_scenario_layouts(s, configured_layouts)
            if role_errors or layout_errors:
                had_errors = True
                console.print(f"[red]ERROR[/red] {s.slug}: validation failed")
                for err in role_errors + layout_errors:
                    console.print(f"  {escape(str(err))}")
        if had_errors:
            raise typer.Exit(1)

        # In-app tours: compiled from EVERY scenario in the directory (a manifest
        # must not depend on CLI filters), before anything is written.
        tour_pipeline = None
        if tours_enabled(config):
            tour_pipeline = prepare_tours_or_exit(
                config,
                scenarios_dir=scenarios_dir,
                selected=scenarios,
                all_scenarios=None if (scenario or tag) else scenarios,
                mode="generate",
            )
        else:
            warn_opted_in_but_disabled(config, scenarios)
        tour_plans = tour_pipeline.replay_plans if tour_pipeline is not None else None

        # Generate code
        if dry_run:
            console.print(f"\n[bold cyan]DRY RUN:[/bold cyan] Showing what would be generated to {output_dir}\n")
        else:
            console.print(f"\n[bold]Generating {len(scenarios)} test file(s) to:[/bold] {output_dir}\n")

        try:
            generator = SteppyGenerator(config)

            if dry_run:
                # Dry run: generate code but don't write files
                results = {}

                # Check conftest.py
                conftest_path = output_dir / "conftest.py"
                conftest_code = generator.generate_conftest(scenarios)
                if conftest_path.exists():
                    existing_content = conftest_path.read_text(encoding="utf-8")
                    if existing_content == conftest_code:
                        results[str(conftest_path)] = "unchanged"
                    else:
                        results[str(conftest_path)] = "updated"
                else:
                    results[str(conftest_path)] = "generated"

                if tour_pipeline is not None:
                    helper_path = output_dir / "_steppy_tours.py"
                    helper_code = generator.tours_helper_source()
                    if helper_path.exists():
                        same = helper_path.read_text(encoding="utf-8") == helper_code
                        results[str(helper_path)] = "unchanged" if same else "updated"
                    else:
                        results[str(helper_path)] = "generated"

                # Check each scenario test file
                for scenario_obj in scenarios:
                    filename = f"test_{scenario_obj.slug}.py"
                    test_path = output_dir / filename
                    test_code = generator.generate_test_file(
                        scenario_obj, tour_plan=tour_plans.get(scenario_obj.slug) if tour_plans else None
                    )

                    if test_path.exists():
                        existing_content = test_path.read_text(encoding="utf-8")
                        if existing_content == test_code:
                            results[str(test_path)] = "unchanged"
                        else:
                            results[str(test_path)] = "updated"
                    else:
                        results[str(test_path)] = "generated"

            else:
                # Normal mode: generate and write files
                results = generator.generate_all(scenarios, output_dir, force=force, tour_plans=tour_plans)

            if tour_pipeline is not None:
                results.update(tour_pipeline.emit(dry_run=dry_run))

        except typer.Exit:
            raise
        except Exception as e:
            _error(f"Generation failed: {e}")
            if _debug_enabled():
                import traceback

                traceback.print_exc()
            raise typer.Exit(1)

        # Display results
        generated_count = sum(1 for status in results.values() if status == "generated")
        updated_count = sum(1 for status in results.values() if status == "updated")
        unchanged_count = sum(1 for status in results.values() if status == "unchanged")
        removed_count = sum(1 for status in results.values() if status == "removed")
        removed_note = f", {removed_count} removed" if removed_count else ""

        for file_path, status in results.items():
            relative_path = (
                Path(file_path).relative_to(Path.cwd()) if Path.cwd() in Path(file_path).parents else file_path
            )
            if status == "generated":
                console.print(f"  [green]+ GENERATED[/green] {relative_path}")
            elif status == "updated":
                console.print(f"  [yellow]~ UPDATED[/yellow]   {relative_path}")
            elif status == "unchanged":
                console.print(f"  [dim]= UNCHANGED[/dim] {relative_path}")
            elif status == "removed":
                console.print(f"  [red]- REMOVED[/red]   {relative_path}")

        console.print()
        if dry_run:
            console.print(
                f"[cyan]Dry run summary:[/cyan] {generated_count} would be generated, {updated_count} would be updated, {unchanged_count} unchanged{removed_note}"
            )
            console.print("\n[cyan]INFO[/cyan] No files were written (dry run mode)")
        else:
            console.print(
                f"[green]Summary:[/green] {generated_count} generated, {updated_count} updated, {unchanged_count} unchanged{removed_note}"
            )
            console.print("\n[green]OK[/green] Generation complete")


def _resolve_locale_layout_lists(
    locales: Optional[str],
    layouts: Optional[str],
    guide: bool,
    console: Console,
) -> tuple[list[str], list[str]]:
    """Resolve the locale + layout parametrization lists for a run/rerun.

    Explicit --locales/--layouts win; else --guide pulls from config.yaml
    (config.locales / config.screenshot.layouts); else empty (no
    parametrization). Shared by `run` and `rerun` so the two paths can't drift.

    The list-building rule lives in `selection.resolve_locales/resolve_layouts`
    (shared with the parallel path, FLAWS §8.3); this wrapper adds cli's own
    config-load + `ConfigError`-degrade behavior — a guide-mode run whose config
    won't load warns once and falls back to empty parametrization rather than
    failing.
    """
    # Only guide mode without an explicit flag needs the config defaults; load it
    # once and degrade to None (→ empty lists) on error, warning a single time.
    steppy_config = None
    if guide and (not locales or not layouts):
        try:
            steppy_config = load_config()
        except ConfigError as e:
            console.print(f"[yellow]WARNING[/yellow] Could not load config for locale list: {e}")
            steppy_config = None

    try:
        locale_list = selection.resolve_locales(locales, guide, steppy_config)
        layout_list = selection.resolve_layouts(layouts, guide, steppy_config)
    except selection.SelectionError as e:
        _error(e.message)
        raise typer.Exit(1)

    return locale_list, layout_list


def _select_guide_test_files(
    guide: bool,
    no_guide: bool,
    scenarios_dir: Path,
    tests_dir: Path,
    console: Console,
) -> list[str]:
    """Return the `test_<slug>.py` paths for guide (or non-guide) scenarios.

    The scenario-set decision (parse + guide filter) comes from the shared
    `selection.select_scenario_slugs` (FLAWS §8.3); this wrapper keeps cli's
    projection to `test_<slug>.py` paths and its INFO/Exit(0)-on-empty messaging.
    Shared by `run`'s guide/no_guide branches and `rerun --guide`.
    """
    try:
        slugs = selection.select_scenario_slugs(
            scenarios_dir=scenarios_dir,
            tests_dir=tests_dir,
            scenario=None,
            tag=None,
            guide=guide,
            no_guide=no_guide,
        )
    except selection.SelectionError as e:
        _error(e.message)
        raise typer.Exit(1)

    if guide:
        if not slugs:
            console.print("[yellow]INFO[/yellow] No scenarios found with guide.enabled=true")
            raise typer.Exit(0)
        console.print(f"\n[bold]Running {len(slugs)} guide scenario(s):[/bold] {', '.join(slugs)}")
    else:
        if not slugs:
            console.print("[yellow]INFO[/yellow] No non-guide scenarios found (all scenarios have guide.enabled=true)")
            raise typer.Exit(0)
        console.print(f"\n[bold]Running {len(slugs)} non-guide scenario(s):[/bold] {', '.join(slugs)}")

    return [str(tests_dir / f"test_{slug}.py") for slug in slugs]


def _build_run_env(
    headed: bool,
    screenshots: bool,
    locale_list: list[str],
    layout_list: list[str],
    guide: bool,
    no_asserts: bool,
    console: Console,
    coverage: bool = False,
    coverage_run_id: Optional[str] = None,
    trace_mode: str = "off",
    video_mode: str = "off",
    run_id: Optional[str] = None,
    events_dir: Optional[Path] = None,
) -> dict:
    """Construct the environment for the pytest subprocess.

    Mirrors the env-construction the single-worker `run` path has always used,
    including its console output. Shared by `run` and `rerun` so the guide
    parametrization env (STEPPY_GUIDE_MODE / STEPPY_LOCALES / STEPPY_LAYOUTS /
    STEPPY_NO_ASSERTS) is built identically by both. Every optional kwarg is
    defaulted so existing call sites (and the byte-identical env guard for
    plain runs) are untouched: each var appears only when its feature was asked
    for — STEPPY_COVERAGE with --coverage, STEPPY_EVENTS_DIR with --bundle, and
    so on.

    ``run_id`` supersedes ``coverage_run_id``: one run has one identity, shared
    by the coverage records and the results bundle. ``coverage_run_id`` is kept
    for the existing coverage-only call shape.
    """
    env = os.environ.copy()
    if headed:
        env["HEADED"] = "1"
        console.print("[dim]Browser mode: headed (visible)[/dim]")
    else:
        console.print("[dim]Browser mode: headless[/dim]")

    if screenshots:
        env["STEPPY_SCREENSHOTS"] = "1"
        console.print("[dim]Screenshots: enabled[/dim]")
    else:
        console.print("[dim]Screenshots: disabled[/dim]")

    if locale_list:
        env["STEPPY_LOCALES"] = ",".join(locale_list)
        console.print(f"[dim]Locales: {', '.join(locale_list)} (per-locale screenshot capture)[/dim]")
    else:
        console.print("[dim]Locales: default (no parametrization)[/dim]")

    if layout_list:
        env["STEPPY_LAYOUTS"] = ",".join(layout_list)
        console.print(f"[dim]Layouts: {', '.join(layout_list)} (per-layout screenshot capture)[/dim]")
    else:
        console.print("[dim]Layouts: default (no parametrization)[/dim]")

    if guide:
        env["STEPPY_GUIDE_MODE"] = "1"

    if no_asserts:
        env["STEPPY_NO_ASSERTS"] = "1"
        console.print("[dim]Assertions: skipped[/dim]")
    else:
        console.print("[dim]Assertions: enabled[/dim]")

    effective_run_id = run_id or coverage_run_id
    if coverage:
        env["STEPPY_COVERAGE"] = "1"
        if effective_run_id:
            env["STEPPY_RUN_ID"] = effective_run_id
        console.print(f"[dim]Coverage recording: enabled (run {effective_run_id})[/dim]")
    elif effective_run_id:
        env["STEPPY_RUN_ID"] = effective_run_id

    if events_dir is not None:
        env["STEPPY_EVENTS_DIR"] = str(events_dir)
        console.print(f"[dim]Event records: {events_dir}[/dim]")
        # Per-step markers, so a bundle can name the step a test died in rather
        # than only the test. On by default with the event records because
        # `manifest.tests[].failed_step` is a bundle field and this is the flag
        # that captures what a bundle needs. The escape hatch is the env var
        # itself — exporting STEPPY_STEP_EVENTS=0 wins, for a suite where two
        # extra file appends per step are unwelcome.
        if os.environ.get("STEPPY_STEP_EVENTS") == "0":
            console.print("[dim]Step records: disabled (STEPPY_STEP_EVENTS=0)[/dim]")
        else:
            env["STEPPY_STEP_EVENTS"] = "1"

    if trace_mode and trace_mode != "off":
        env["STEPPY_TRACE"] = trace_mode
        console.print(f"[dim]Playwright trace: {trace_mode}[/dim]")

    if video_mode and video_mode != "off":
        env["STEPPY_VIDEO"] = video_mode
        console.print(f"[dim]Playwright video: {video_mode}[/dim]")

    return env


def _apply_bundle_junit_arg(pytest_cmd: list, junit_dir: Path, console: Console) -> None:
    """Append ``--junitxml`` at the fixed path a results bundle reads.

    Skipped when ``--junitxml`` is already on the argv — ``report.junit_xml``
    got there first (``_apply_report_args``), and pytest honours only the LAST
    one, so appending a second would silently discard the user's configured
    report. The bundler falls back to that path in the same situation.
    """
    if any(str(arg).startswith("--junitxml") for arg in pytest_cmd):
        console.print("[dim]JUnit report: reusing report.junit_xml for the bundle[/dim]")
        return
    try:
        junit_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        console.print(f"[yellow]WARNING[/yellow] Could not create {junit_dir}: {e}; bundle will have no junit.xml")
        return
    path = junit_dir / "run.xml"
    pytest_cmd.append(f"--junitxml={path}")
    console.print(f"[dim]JUnit report: {path}[/dim]")


def _apply_report_args(pytest_cmd: list, report_cfg, console: Console) -> None:
    """Append JUnit / HTML report flags to the pytest command (FLAWS §6.1).

    JUnit is built into pytest (no plugin). HTML needs pytest-html — when the
    plugin is missing we warn and skip it (never fail the run), pointing at the
    ``steppy-dev[report]`` extra. Parent dirs are created so pytest can write.
    """
    if report_cfg.junit_xml:
        path = Path(report_cfg.junit_xml)
        path.parent.mkdir(parents=True, exist_ok=True)
        pytest_cmd.append(f"--junitxml={path}")
        console.print(f"[dim]JUnit report: {path}[/dim]")
    if report_cfg.html:
        try:
            import importlib.util

            if importlib.util.find_spec("pytest_html") is None:
                raise ImportError
        except ImportError:
            console.print(
                "[yellow]WARNING[/yellow] report.html is set but pytest-html is not "
                "installed; skipping the HTML report. Install it with: pip install 'steppy-dev\\[report]'"
            )
        else:
            path = Path(report_cfg.html)
            path.parent.mkdir(parents=True, exist_ok=True)
            pytest_cmd.append(f"--html={path}")
            pytest_cmd.append("--self-contained-html")
            console.print(f"[dim]HTML report: {path}[/dim]")
    # Flaky-retry needs pytest-rerunfailures (the `steppy-dev[retry]` extra); warn
    # and skip when it is missing rather than failing the run (mirrors HTML).
    if getattr(report_cfg, "retries", 0) and report_cfg.retries > 0:
        try:
            import importlib.util

            if importlib.util.find_spec("pytest_rerunfailures") is None:
                raise ImportError
        except ImportError:
            console.print(
                "[yellow]WARNING[/yellow] report.retries is set but pytest-rerunfailures is "
                "not installed; skipping retries. Install it with: pip install 'steppy-dev\\[retry]'"
            )
        else:
            pytest_cmd.append(f"--reruns={report_cfg.retries}")
            console.print(f"[dim]Flaky-retry: up to {report_cfg.retries} rerun(s) per failure[/dim]")


def _resolve_report_modes(
    *,
    trace: bool,
    video: bool,
    retries: Optional[int],
) -> tuple[str, str, int, Optional[Any]]:
    """Resolve the effective trace / video / retries settings WITHOUT touching
    any pytest argv (FLAWS §6.1).

    The CLI flag wins, otherwise ``config.report`` decides. Best-effort config
    load: a run without config.yaml resolves to the all-off defaults, which is
    what keeps the plain single-worker path byte-identical
    (tests/test_run_byte_identical.py).

    Split out of :func:`_apply_report_and_modes` so the **parallel** dispatch
    can reach the same three values — it has no host-side ``pytest_cmd`` to
    append to; the settings travel to the worker containers as env vars
    instead (``STEPPY_TRACE`` / ``STEPPY_VIDEO`` / ``STEPPY_RETRIES``).

    Returns ``(trace_mode, video_mode, retries, report_cfg)``. ``report_cfg`` is
    None when no config could be loaded.
    """
    trace_mode = "on-failure" if trace else "off"
    video_mode = "on-failure" if video else "off"
    try:
        report_cfg = load_config().report
    except ConfigError:
        return trace_mode, video_mode, retries or 0, None
    if retries is not None:
        report_cfg = report_cfg.model_copy(update={"retries": retries})
    if not trace:
        trace_mode = report_cfg.trace
    if not video:
        video_mode = report_cfg.video
    return trace_mode, video_mode, report_cfg.retries, report_cfg


def _apply_report_and_modes(
    pytest_cmd: list,
    *,
    trace: bool,
    video: bool,
    retries: Optional[int],
    console: Console,
) -> tuple[str, str]:
    """Append report flags and resolve the trace/video modes (FLAWS §6.1).

    Shared verbatim by ``run`` and ``rerun``. Thin wrapper over
    :func:`_resolve_report_modes` — the resolution rules live there.

    Returns ``(trace_mode, video_mode)``.
    """
    trace_mode, video_mode, _retries, report_cfg = _resolve_report_modes(trace=trace, video=video, retries=retries)
    if report_cfg is not None:
        _apply_report_args(pytest_cmd, report_cfg, console)
    return trace_mode, video_mode


def _new_coverage_run_id() -> str:
    """Back-compat alias for :func:`steppy.runmeta.new_run_id`.

    The generator moved to the ``runmeta`` leaf when ``--bundle`` gave a run an
    identity independent of ``--coverage``; the name is kept because it is the
    one that appears in older docs and issue reports.
    """
    return new_run_id()


def _merge_coverage_after_run(run_id: str, console: Console) -> None:
    """Fold the just-recorded run's raw records into the persistent map store.

    Lazy import on purpose: ``steppy run`` without --coverage must never load
    steppy.coverage (tests/test_no_coverage_import_on_default_path.py). A
    merge failure warns but never masks pytest's exit code — the raw records
    stay on disk and `steppy coverage merge --run-id` retries the merge.
    """
    try:
        from .coverage.merge import CoverageSettings, merge_run

        settings = CoverageSettings.from_config(load_config(), Path.cwd())
        summary = merge_run(settings, run_id)
        console.print(
            f"[green]OK[/green] Coverage: merged {summary['totals']['scenarios']} scenario(s), "
            f"{summary['totals']['nodes']} map node(s) → {settings.store_path}"
        )
        for warning in summary.get("warnings", []):
            console.print(f"[yellow]WARNING[/yellow] {warning}")
        _write_coverage_gaps(settings, console)
        console.print(
            "[dim]Gap report: steppy coverage report  (refresh the declared surface "
            "first with: steppy coverage refresh)[/dim]"
        )
    except Exception as e:
        console.print(f"[yellow]WARNING[/yellow] Coverage merge failed: {e}")
        console.print(f"[dim]Raw records kept — retry with: steppy coverage merge --run-id {run_id}[/dim]")


def _write_coverage_gaps(settings, console: Console) -> None:
    """Drop the gap report beside the raw records, for whoever reads them later.

    The sqlite store lives outside ``output/`` on purpose — cleanup must not wipe
    manual triage flags — which also means it can never be inside a results
    bundle. So a bundle downloaded from CI has the raw records but no way to
    answer "how much of the declared surface does this suite reach?". This file
    is what crosses that gap, and writing it here means
    ``steppy run --coverage --bundle`` is self-sufficient rather than needing an
    extra incantation in every CI config.

    Best-effort, and quiet about it: the merge already succeeded by this point, so
    a failure to write a derived convenience file must not look like a failed
    merge. ``steppy coverage report --json`` regenerates it on demand.
    """
    try:
        from .coverage.gaps import compute_gaps
        from .coverage.report import gaps_to_json
        from .coverage.store import open_store, read_map
        from .coverage.triage import apply_config_triage

        conn = open_store(settings.store_path)
        try:
            gaps = compute_gaps(apply_config_triage(read_map(conn), settings.non_ui))
        finally:
            conn.close()
        target = Path(settings.records_dir) / "gaps.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(gaps_to_json(gaps), encoding="utf-8")
    except Exception as e:
        console.print(f"[dim]Coverage gap report not written ({e}); run `steppy coverage report --json` for it.[/dim]")


@app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def run(
    ctx: typer.Context,
    scenario: Optional[str] = typer.Option(
        None,
        "--scenario",
        "-s",
        help="Run a specific scenario by slug (e.g., 'cross--order-lifecycle')",
    ),
    tag: Optional[list[str]] = typer.Option(
        None,
        "--tag",
        "-t",
        help="Run only scenarios with these tags (can be repeated, AND logic)",
    ),
    headed: bool = typer.Option(
        False,
        "--headed",
        help="Run with visible browser (headed mode)",
    ),
    screenshots: bool = typer.Option(
        False,
        "--screenshots",
        help="Enable screenshot capture during test run",
    ),
    guide: bool = typer.Option(
        False,
        "--guide",
        help="Run only scenarios marked with guide.enabled=true (auto-enables --screenshots)",
    ),
    no_asserts: bool = typer.Option(
        False,
        "--no-asserts",
        help="Skip assertions at runtime (auto-enabled by --guide; useful for multi-locale runs where text asserts can't match every language)",
    ),
    no_guide: bool = typer.Option(
        False,
        "--no-guide",
        help="Run only scenarios NOT marked with guide.enabled=true (excludes guide scenarios)",
    ),
    locales: Optional[str] = typer.Option(
        None,
        "--locales",
        help="Comma-separated locale codes to run tests against (e.g. 'fr,nl'). Each locale "
        "parametrizes the role page fixtures and applies the configured i18n.localstorage value "
        "before pages load. With --guide and no explicit value, defaults to all locales from config.yaml.",
    ),
    layouts: Optional[str] = typer.Option(
        None,
        "--layouts",
        help="Comma-separated layout names to parametrize over (e.g. 'desktop,mobile'). "
        "Each layout sets the role page fixture's viewport from config.screenshot.layouts. "
        "Works in both test and guide modes — use it on plain test runs to verify scenarios "
        "across device viewports. With --guide and no explicit value, defaults to all "
        "layouts declared in config.yaml.",
    ),
    tests_dir: Path = typer.Option(
        Path("tests/e2e"),
        "--tests-dir",
        help="Directory containing generated test files",
    ),
    scenarios_dir: Path = typer.Option(
        Path("scenarios"),
        "--scenarios-dir",
        "-d",
        help="Directory containing scenario files (used for tag filtering)",
    ),
    workers: Optional[int] = typer.Option(
        None,
        "--workers",
        min=1,
        help="Spin up N Docker Compose stacks in parallel (each running a "
        "steppy worker container). Requires `parallel:` block + per-app "
        "`internal_url` in config.yaml. N=1 (or unset) uses the existing "
        "single-process path with no Docker involvement. N>=2 dispatches to "
        "the parallel coordinator. Default comes from config.yaml's "
        "parallel.workers (1 if absent).",
    ),
    no_build: bool = typer.Option(
        False,
        "--no-build",
        help="Skip auto-build of the steppy-worker image (parallel mode only). "
        "Fails fast if the image is missing instead of building it.",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Parallel mode only: stream raw pytest output (prefixed with [wN]) under "
        "the live progress display. Ignored by single-worker runs, whose output is "
        "already streamed directly to the terminal.",
    ),
    coverage: bool = typer.Option(
        False,
        "--coverage",
        help="Record per-scenario coverage signals (route trail, API requests, JS "
        "coverage) and merge them into the coverage map after the run. Composes "
        "with --scenario/--tag/--guide like the other mode flags. See "
        "`steppy coverage report` for the gap analysis.",
    ),
    bundle: bool = typer.Option(
        False,
        "--bundle",
        help="Capture the extra artifacts a results bundle needs: per-test event "
        "records under output/events/<run-id>/ and JUnit XML under "
        "output/junit/<run-id>/, plus an output/.steppy-run.json describing the "
        "run. Does NOT create the bundle — run `steppy bundle create` afterwards. "
        "Off by default because it changes the pytest invocation.",
    ),
    trace: bool = typer.Option(
        False,
        "--trace",
        help="Record a Playwright trace and save output/traces/<test>.zip for each "
        "FAILED test (equivalent to report.trace: on-failure for this run). "
        "Open with: playwright show-trace <zip>.",
    ),
    video: bool = typer.Option(
        False,
        "--video",
        help="Record a Playwright video and save output/videos/<test>.webm for each "
        "FAILED test (equivalent to report.video: on-failure for this run).",
    ),
    retries: Optional[int] = typer.Option(
        None,
        "--retries",
        help="Rerun each failed test up to N times (overrides report.retries for this "
        "run). Requires the steppy-dev[retry] extra (pytest-rerunfailures).",
    ),
    retry_pass: Optional[int] = typer.Option(
        None,
        "--retry-pass",
        help="[--workers N only] After the queue drains, re-run the FAILED scenarios "
        "N more times at reduced concurrency. A scenario that then passes is reported "
        "as FLAKY, not failed. Overrides parallel.retry.passes; --retry-pass 0 disables.",
    ),
    strict_flaky: bool = typer.Option(
        False,
        "--strict-flaky",
        help="[--workers N only] Make FLAKY scenarios (failed, then passed on retry) "
        "fail the build. Default is lenient: a pass on retry is a pass.",
    ),
    fail_fast: Optional[bool] = typer.Option(
        None,
        "--fail-fast/--no-fail-fast",
        help="[--workers N only] Abort the run early when nearly every completed "
        "scenario has failed (the app or auth is probably down). On by default; "
        "overrides parallel.circuit_breaker.enabled.",
    ),
):
    """
    Run generated pytest tests.

    Wraps pytest with Steppy-specific environment variables and filtering.
    You can also run pytest directly: pytest tests/e2e/
    """
    with _cli_errors():
        # Parallel-mode dispatch. Lazy import keeps the single-worker path
        # from loading any coordinator/worker code at module-load time —
        # enforced by tests/test_no_parallel_import_on_default_path.py.
        # Resolution order for `workers`:
        #   1. CLI flag (any int >= 1)
        #   2. config.yaml's parallel.workers (int or "auto")
        #   3. Default 1 (existing path)
        # Any value >= 2 dispatches; 1 (or unset, with no config default)
        # falls through to the unchanged existing implementation below.
        if workers is None:
            try:
                _cfg = load_config()
                _cfg_workers = _cfg.parallel.workers if _cfg.parallel else 1
                # "auto" → force dispatch with sentinel 2; the coordinator
                # computes the real auto count once it knows scenario_count.
                workers = 2 if _cfg_workers == "auto" else int(_cfg_workers)
            except ConfigError:
                workers = 1
        if workers >= 2:
            from .coordinator import run_parallel

            # Report settings resolve host-side (they need config.yaml) and
            # travel to the workers as env vars. Without this the `--trace` /
            # `--video` / `--retries` flags were silently dropped in parallel
            # mode — see tests/test_parallel_report_flags.py.
            par_trace, par_video, par_retries, _ = _resolve_report_modes(trace=trace, video=video, retries=retries)
            if ctx.args:
                reject_xdist_args(ctx.args)

            raise typer.Exit(
                run_parallel(
                    workers=workers,
                    scenario=scenario,
                    tag=tag,
                    headed=headed,
                    screenshots=screenshots,
                    guide=guide,
                    no_asserts=no_asserts,
                    no_guide=no_guide,
                    locales=locales,
                    layouts=layouts,
                    tests_dir=tests_dir,
                    scenarios_dir=scenarios_dir,
                    pytest_passthrough=list(ctx.args),
                    no_build=no_build,
                    verbose=verbose,
                    coverage=coverage,
                    trace_mode=par_trace,
                    video_mode=par_video,
                    retries=par_retries,
                    retry_pass=retry_pass,
                    strict_flaky=strict_flaky,
                    fail_fast=fail_fast,
                    bundle=bundle,
                )
            )

        # ── Existing single-worker path: unchanged from here ──────────────
        require_generated_tests(tests_dir)

        # Validate mutually exclusive options
        if guide and no_guide:
            console.print("[red]ERROR[/red] --guide and --no-guide are mutually exclusive")
            raise typer.Exit(1)
        # --guide/--no-guide compose with --scenario/--tag: they narrow the
        # selection (and --guide still drives guide *mode* env below). This
        # matches the parallel path (coordinator._select_scenario_slugs).
        if locales and no_guide:
            console.print("[red]ERROR[/red] --locales cannot be combined with --no-guide")
            raise typer.Exit(1)

        # --guide auto-enables screenshots + --no-asserts
        screenshots, no_asserts = selection.apply_guide_defaults(guide, screenshots, no_asserts)

        # Resolve locale + layout parametrization lists: explicit --locales/
        # --layouts win, else --guide pulls from config.yaml, else no
        # parametrization. Shared with `rerun` via this helper.
        locale_list, layout_list = _resolve_locale_layout_lists(locales, layouts, guide, console)

        # Build pytest command - use sys.executable to ensure we use the same Python env
        pytest_cmd = [sys.executable, "-m", "pytest"]

        # Add filtering. Most-specific selector wins; --guide/--no-guide narrow
        # the tag-matched set (and the guide *mode* env was already applied
        # above). Order: scenario → tag (∩ guide) → guide/no_guide → all.
        if scenario:
            # Filter by scenario slug - run the specific test file. Combined with
            # --guide this runs that one scenario in guide mode.
            test_file = tests_dir / f"test_{scenario}.py"
            if not test_file.exists():
                console.print(f"[red]ERROR[/red] Test file not found: {test_file}")
                console.print("\n[yellow]Hint:[/yellow] Run [bold]steppy generate[/bold] first to create test files")
                raise typer.Exit(1)
            pytest_cmd.append(str(test_file))
            console.print(f"\n[bold]Running scenario:[/bold] {scenario}")
        elif tag:
            # Filter by tag (∩ guide) via the shared selector (FLAWS §8.3), then
            # project to specific test files (avoids -k substring matching issues).
            try:
                matching_slugs = selection.select_scenario_slugs(
                    scenarios_dir=scenarios_dir,
                    tests_dir=tests_dir,
                    scenario=None,
                    tag=tag,
                    guide=guide,
                    no_guide=no_guide,
                )
            except selection.SelectionError as e:
                _error(e.message)
                raise typer.Exit(1)

            guide_label = " guide-enabled" if guide else (" non-guide" if no_guide else "")
            if not matching_slugs:
                console.print(
                    f"[yellow]WARNING[/yellow] No{guide_label} scenarios found with tag(s) '{', '.join(tag)}'"
                )
                raise typer.Exit(0)

            for slug in matching_slugs:
                pytest_cmd.append(str(tests_dir / f"test_{slug}.py"))
            console.print(f"\n[bold]Running{guide_label} tests with tag(s):[/bold] {', '.join(tag)}")
            console.print(f"[dim]Matched {len(matching_slugs)} scenario(s): {', '.join(matching_slugs)}[/dim]")
        elif guide or no_guide:
            # Filter to scenarios with (guide) / without (no_guide) guide.enabled.
            pytest_cmd.extend(_select_guide_test_files(guide, no_guide, scenarios_dir, tests_dir, console))
        else:
            pytest_cmd.append(str(tests_dir))
            console.print(f"\n[bold]Running all tests in:[/bold] {tests_dir}")

        # Add pytest-playwright plugin
        pytest_cmd.append("--browser")
        pytest_cmd.append("chromium")

        trace_mode, video_mode = _apply_report_and_modes(
            pytest_cmd, trace=trace, video=video, retries=retries, console=console
        )

        # --bundle: one identity for the whole run, plus the two artifact sinks
        # `steppy bundle create` needs and a plain run does not produce.
        run_id = new_run_id() if (bundle or coverage) else None
        events_dir = junit_dir = None
        if bundle:
            events_dir, junit_dir = bundle_dirs(Path.cwd(), run_id)
            _apply_bundle_junit_arg(pytest_cmd, junit_dir, console)

        # Add passthrough arguments from context
        if ctx.args:
            reject_xdist_args(ctx.args)
            pytest_cmd.extend(ctx.args)

        # Set environment variables
        env = _build_run_env(
            headed,
            screenshots,
            locale_list,
            layout_list,
            guide,
            no_asserts,
            console,
            coverage=coverage,
            trace_mode=trace_mode,
            video_mode=video_mode,
            run_id=run_id,
            events_dir=events_dir,
        )

        if bundle:
            write_run_sidecar(
                Path.cwd(),
                run_id=run_id,
                spec={
                    "guide": guide,
                    "no_guide": no_guide,
                    "no_asserts": no_asserts,
                    "screenshots": screenshots,
                    "coverage": coverage,
                    "locales": list(locale_list),
                    "layouts": list(layout_list),
                    "workers": 1,
                    "tag": list(tag) if tag else None,
                    "scenario": scenario,
                },
            )
            console.print(f"[dim]Bundle capture: enabled (run {run_id}) — create it with: steppy bundle create[/dim]")

        console.print(f"\n[dim]Command: {' '.join(pytest_cmd)}[/dim]\n")

        # Run pytest
        result = subprocess.run(
            pytest_cmd,
            env=env,
            cwd=Path.cwd(),
        )

        # Post-run coverage merge. Runs even on nonzero pytest exit — failed
        # scenarios still exercised surface (their records carry outcome) —
        # and never masks pytest's exit code.
        if coverage and run_id:
            _merge_coverage_after_run(run_id, console)

        # Exit with pytest's exit code
        raise typer.Exit(result.returncode)


# ── Rerun commands ──────────────────────────────────────────────────────────


@app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def rerun(
    ctx: typer.Context,
    headed: bool = typer.Option(
        False,
        "--headed",
        help="Run with visible browser (headed mode)",
    ),
    screenshots: bool = typer.Option(
        False,
        "--screenshots",
        help="Enable screenshot capture during test run",
    ),
    guide: bool = typer.Option(
        False,
        "--guide",
        help="Re-run failures from a guide capture run. Reconstructs the same "
        "environment as `steppy run --guide` (auto-enables --screenshots and "
        "--no-asserts, sets STEPPY_GUIDE_MODE, and parametrizes over locales/"
        "layouts) so the failed [locale-layout] node IDs line up with --lf. "
        "Pass the SAME --locales/--layouts you used for the original capture "
        "(or just --guide to take the config.yaml defaults).",
    ),
    no_asserts: bool = typer.Option(
        False,
        "--no-asserts",
        help="Skip assertions at runtime (auto-enabled by --guide).",
    ),
    locales: Optional[str] = typer.Option(
        None,
        "--locales",
        help="Comma-separated locale codes to parametrize over (e.g. 'fr,nl'). "
        "Must match the original capture run for --lf node IDs to align. With "
        "--guide and no explicit value, defaults to all locales from config.yaml.",
    ),
    layouts: Optional[str] = typer.Option(
        None,
        "--layouts",
        help="Comma-separated layout names to parametrize over (e.g. 'desktop,mobile'). "
        "Must match the original capture run for --lf node IDs to align. With "
        "--guide and no explicit value, defaults to all layouts from config.yaml.",
    ),
    tests_dir: Path = typer.Option(
        Path("tests/e2e"),
        "--tests-dir",
        help="Directory containing generated test files",
    ),
    scenarios_dir: Path = typer.Option(
        Path("scenarios"),
        "--scenarios-dir",
        "-d",
        help="Directory containing scenario files (used for --guide filtering)",
    ),
    trace: bool = typer.Option(
        False,
        "--trace",
        help="Record a Playwright trace and save output/traces/<test>.zip for each "
        "FAILED test. Open with: playwright show-trace <zip>.",
    ),
    video: bool = typer.Option(
        False,
        "--video",
        help="Record a Playwright video and save output/videos/<test>.webm for each FAILED test.",
    ),
    retries: Optional[int] = typer.Option(
        None,
        "--retries",
        help="Rerun each failed test up to N times (overrides report.retries for this "
        "run). Requires the steppy-dev[retry] extra (pytest-rerunfailures).",
    ),
):
    """
    Re-run only previously failed tests.

    Uses pytest's --lf (last-failed) flag to re-run tests that failed
    in the most recent run. Requires a prior test run so that pytest's
    cache knows which tests failed.

    Add --guide to re-run failures from a guide capture run: it rebuilds the
    same parametrization environment as `steppy run --guide`, so only the
    failed (locale, layout, scenario) variants are re-captured.
    """
    with _cli_errors():
        # `rerun` needs no test_*.py check: pytest's --lf cache decides.
        require_generated_tests(tests_dir, require_test_files=False)

        # Check pytest cache for last-failed info
        cache_file = Path.cwd() / ".pytest_cache" / "v" / "cache" / "lastfailed"
        if not cache_file.exists():
            console.print("[yellow]INFO[/yellow] No previous test run found (no pytest cache)")
            console.print(
                "\n[yellow]Hint:[/yellow] Run [bold]steppy run[/bold] first, then use [bold]steppy rerun[/bold] to retry failures"
            )
            raise typer.Exit(0)

        try:
            lastfailed = json.loads(cache_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            console.print("[yellow]INFO[/yellow] Could not read pytest cache")
            raise typer.Exit(1)

        if not lastfailed:
            console.print("[green]No failed tests to re-run — all tests passed in the last run![/green]")
            raise typer.Exit(0)

        # Display which tests will be re-run
        console.print(f"\n[bold]Re-running {len(lastfailed)} previously failed test(s):[/bold]")
        for node_id in lastfailed:
            console.print(f"  [dim]•[/dim] {node_id}")

        # --guide reconstructs the same capture environment as `steppy run --guide`
        # so the parametrized [locale-layout] node IDs in the cache line up with --lf.
        screenshots, no_asserts = selection.apply_guide_defaults(guide, screenshots, no_asserts)
        locale_list, layout_list = _resolve_locale_layout_lists(locales, layouts, guide, console)

        # Build pytest command. With --guide, scope --lf to the guide scenario
        # test files (so collection only parametrizes those); otherwise the whole
        # tests dir (unchanged behavior).
        pytest_cmd = [sys.executable, "-m", "pytest", "--lf"]
        if guide:
            pytest_cmd.extend(_select_guide_test_files(guide, False, scenarios_dir, tests_dir, console))
        else:
            pytest_cmd.append(str(tests_dir))

        # Add pytest-playwright plugin
        pytest_cmd.extend(["--browser", "chromium"])

        trace_mode, video_mode = _apply_report_and_modes(
            pytest_cmd, trace=trace, video=video, retries=retries, console=console
        )

        # Add passthrough arguments from context
        if ctx.args:
            reject_xdist_args(ctx.args)
            pytest_cmd.extend(ctx.args)

        # Set environment variables (shared builder with `run`)
        env = _build_run_env(
            headed,
            screenshots,
            locale_list,
            layout_list,
            guide,
            no_asserts,
            console,
            trace_mode=trace_mode,
            video_mode=video_mode,
        )

        console.print(f"\n[dim]Command: {' '.join(pytest_cmd)}[/dim]\n")

        # Run pytest
        result = subprocess.run(
            pytest_cmd,
            env=env,
            cwd=Path.cwd(),
        )

        # Exit with pytest's exit code
        raise typer.Exit(result.returncode)


# ── Guide commands ──────────────────────────────────────────────────────────


def _handbook_targets(results: dict) -> list[tuple[str, Optional[str]]]:
    """The (locale, layout) pairs a tag actually produced guides for.

    Derived from ``generate_guides_for_tag``'s result keys (``"<locale>"`` or
    ``"<locale>/<layout>"``) rather than re-probing the disk, so the handbook
    covers exactly the pages that were just written — including the case where
    scenarios in one tag declare different layouts.
    """
    targets: list[tuple[str, Optional[str]]] = []
    for locale_results in results.values():
        for key, result in locale_results.items():
            if result.get("status") != "success":
                continue
            locale, _, layout = key.partition("/")
            pair = (locale, layout or None)
            if pair not in targets:
                targets.append(pair)
    return targets


def _emit_guide_pdfs(
    assembler,
    config,
    results_by_tag: dict[str, dict],
    *,
    want_pdf: bool,
    want_handbook: bool,
    scenarios_dir: Path,
    output_dir: Path,
    screenshots_dir: Path,
) -> int:
    """Render per-guide PDFs and/or handbooks for already-assembled guides.

    Runs once, after all assembly, so the whole batch shares a single Chromium
    (a guide project is a cartesian of scenarios × locales × layouts — a browser
    launch per file would dominate). Returns the number of failures, which the
    caller folds into its own error count.
    """
    from .pdf import PdfJob, PdfRenderError, render_pdfs

    jobs: list[PdfJob] = []

    if want_pdf:
        for results in results_by_tag.values():
            for locale_results in results.values():
                for result in locale_results.values():
                    if result.get("status") != "success":
                        continue
                    html_path = Path(result["html"])
                    jobs.append(
                        PdfJob(
                            html=html_path,
                            pdf=html_path.with_suffix(".pdf"),
                            title=result.get("title", html_path.stem),
                        )
                    )

    errors = 0
    if want_handbook:
        console.print("\n[bold]Assembling handbooks[/bold]")
        for guide_tag, results in results_by_tag.items():
            for locale, layout in _handbook_targets(results):
                try:
                    handbook = assembler.generate_handbook(
                        guide_tag,
                        locale,
                        scenarios_dir,
                        output_dir,
                        screenshots_dir,
                        layout=layout,
                        on_skip=lambda slug, reason: console.print(
                            f"  [yellow]SKIP[/yellow] {escape(slug)}: {escape(reason)}"
                        ),
                    )
                except Exception as e:
                    _error(f"handbook {guide_tag} ({locale}): {e}")
                    errors += 1
                    continue
                if handbook is None:
                    console.print(f"  [yellow]INFO[/yellow] {guide_tag} ({locale}): no guides to include")
                    continue
                console.print(f"  [green]OK[/green] {handbook}")
                jobs.append(
                    PdfJob(
                        html=handbook,
                        pdf=handbook.with_suffix(".pdf"),
                        title=handbook.stem,
                    )
                )

    if not jobs:
        return errors

    console.print(f"\n[bold]Rendering {len(jobs)} PDF(s)[/bold]")

    def _report(result) -> None:
        if result.ok:
            console.print(f"  [green]OK[/green] {result.job.pdf}")
        else:
            _error(f"{result.job.pdf}: {result.error}")

    try:
        results = render_pdfs(jobs, config.pdf, on_done=_report)
    except PdfRenderError as e:
        _error(str(e))
        return errors + len(jobs)

    return errors + sum(1 for r in results if not r.ok)


def _emit_guide_videos(
    assembler,
    config,
    results_by_tag: dict[str, dict],
    *,
    scenarios_dir: Path,
    output_dir: Path,
    screenshots_dir: Path,
) -> int:
    """Render a narrated MP4 beside each guide page just assembled.

    Runs once, after all assembly, so the whole batch shares one Chromium and
    one ffmpeg discovery — the same reason the PDF pass is batched.

    A guide whose manifest cannot be built is **skipped, not fatal**: the
    interactive manifest validates far more strictly than the HTML guide (a step
    with no bbox, a locale with no ``guide-text``), so scenarios that render a
    perfectly good page can legitimately have no video. Returns the failure
    count for the caller's error tally.
    """
    from .captures import resolve_output_dir
    from .tts import assemble_track, voice_jobs
    from .video import VideoJob, VideoRenderError, find_ffmpeg, render_videos

    options = config.video
    try:
        ffmpeg = find_ffmpeg(options)
    except VideoRenderError as e:
        _error(str(e))
        return 1

    jobs: list[VideoJob] = []
    errors = 0
    for guide_tag, results in results_by_tag.items():
        role = guide_tag.replace("user-guide-", "")
        for slug, locale_results in results.items():
            for key, result in locale_results.items():
                if result.get("status") != "success":
                    continue
                locale, _, layout = key.partition("/")
                page = Path(result["html"])
                try:
                    scenario = assembler.scenario_by_slug(scenarios_dir, slug)
                    manifest = assembler.prepare_video_manifest(
                        scenario,
                        role,
                        locale,
                        output_dir,
                        screenshots_dir,
                        layout=layout or None,
                        title=result.get("title"),
                    )
                except Exception as e:
                    console.print(f"  [yellow]SKIP[/yellow] {escape(slug)} ({escape(key)}): {escape(str(e))}")
                    continue
                jobs.append(
                    VideoJob(
                        manifest=manifest,
                        page_dir=page.parent,
                        out=page.with_suffix(".mp4"),
                        locale=locale,
                        layout=layout or None,
                        title=result.get("title", slug),
                    )
                )

    if not jobs:
        return errors

    tts_cache = resolve_output_dir(Path.cwd(), options.tts.cache_dir, ".steppy/tts-cache")
    jobs = voice_jobs(
        jobs,
        options.tts,
        ffmpeg=ffmpeg,
        cache_root=tts_cache,
        work_dir=tts_cache / "work",
        on_warning=lambda msg: console.print(f"  [yellow]WARN[/yellow] {escape(msg)}"),
    )

    console.print(f"\n[bold]Rendering {len(jobs)} video(s)[/bold] (headless Chromium + ffmpeg)")

    def _report(result) -> None:
        if result.ok:
            suffix = " [dim](cached)[/dim]" if result.cached else ""
            console.print(f"  [green]OK[/green] {result.job.out}{suffix}")
        else:
            _error(f"{result.job.out}: {result.error}")

    try:
        rendered = render_videos(
            jobs,
            options,
            assemble_audio=assemble_track,
            cache_root=resolve_output_dir(Path.cwd(), options.cache_dir, ".steppy/video-cache"),
            on_done=_report,
        )
    except VideoRenderError as e:
        _error(str(e))
        return errors + len(jobs)

    return errors + sum(1 for r in rendered if not r.ok)


@app.command()
def guide(
    tag: Optional[str] = typer.Option(
        None,
        "--tag",
        "-t",
        help="Generate guides for scenarios with this tag (e.g., 'user-guide-admin')",
    ),
    locale: List[str] = typer.Option(
        ["fr"],
        "--locale",
        "-l",
        help="Locale(s) to generate guides for (can be specified multiple times)",
    ),
    all_pipeline: bool = typer.Option(
        False,
        "--all",
        help="Run full pipeline: generate tests, run with screenshots, then assemble guides",
    ),
    guide_mode: bool = typer.Option(
        False,
        "--guide",
        help="Assemble guides for every scenario marked guide.enabled=true, across all their user-guide-* roles",
    ),
    scenarios_dir: Path = typer.Option(
        Path("scenarios"),
        "--scenarios-dir",
        help="Directory containing scenario files",
    ),
    output_dir: Optional[Path] = typer.Option(
        None,
        "--output-dir",
        # No square brackets: Rich parses them as markup and silently eats the
        # text (the FLAWS §2.5 bug, in help strings rather than errors).
        help="Output directory for guides. Default: config output.guides_dir, else output/guides",
    ),
    screenshots_dir: Optional[Path] = typer.Option(
        None,
        "--screenshots-dir",
        help="Directory containing captured screenshots. Default: config output.screenshots_dir, else output/screenshots",
    ),
    pdf: bool = typer.Option(
        False,
        "--pdf",
        help="Also print each generated guide to PDF (headless Chromium)",
    ),
    handbook: bool = typer.Option(
        False,
        "--handbook",
        help="Also assemble one handbook (HTML + PDF) per role/locale/layout: cover, contents, every guide",
    ),
    video: bool = typer.Option(
        False,
        "--video",
        help=(
            "Also render a narrated MP4 beside each generated guide. Requires ffmpeg. "
            "(Unrelated to 'steppy run --video', which records Playwright debugging "
            "footage of failed tests.)"
        ),
    ),
):
    """
    Assemble user guides from scenario files and captured screenshots.

    Requires that tests have been run with --screenshots enabled first.

    Examples:
      steppy guide --tag user-guide-admin --locale fr
      steppy guide --tag user-guide-editor --locale fr --locale en
      steppy guide --guide --locale fr
      steppy guide --guide --pdf --handbook
      steppy guide --all
    """
    # Lazy: pulls in jinja2 + markdown_it (~40ms), only needed by this command.
    from .guide import GuideAssembler

    with _cli_errors():
        config = _load_config_or_exit()

        # Each --locale becomes a directory segment this command reads back and
        # `steppy build` reads after it; both of those now reject a malformed
        # tag, so accepting one here only moves the failure somewhere it cannot
        # be explained.
        for code in locale:
            try:
                normalize_locale(code)
            except LocaleSyntaxError as e:
                _error(f"--locale: {e}")
                raise typer.Exit(1)

        # An explicit flag wins; otherwise the paths come from `config.output`,
        # which is also what the generated conftest writes to (FLAWS §4.6).
        if output_dir is None:
            output_dir = resolve_output_dir(Path.cwd(), config.output.guides_dir, DEFAULT_GUIDES_DIR)
        if screenshots_dir is None:
            screenshots_dir = resolve_output_dir(Path.cwd(), config.output.screenshots_dir, DEFAULT_SCREENSHOTS_DIR)

        # Handle --all flag: full pipeline
        if all_pipeline:
            console.print("\n[bold cyan]Running full guide pipeline[/bold cyan]\n")

            # Step 1: Generate tests
            console.print("[bold]Step 1/3:[/bold] Generating test files...")
            try:
                generator = SteppyGenerator(config)
                scenarios = parse_scenarios_from_directory(scenarios_dir)
                tests_dir = Path("tests/e2e")
                tour_pipeline = None
                if tours_enabled(config):
                    tour_pipeline = prepare_tours_or_exit(config, scenarios_dir=scenarios_dir, all_scenarios=scenarios)
                results = generator.generate_all(
                    scenarios, tests_dir, force=False, tour_plans=tour_pipeline.replay_plans if tour_pipeline else None
                )
                if tour_pipeline is not None:
                    results.update(tour_pipeline.emit())

                generated = sum(1 for status in results.values() if status == "generated")
                updated = sum(1 for status in results.values() if status == "updated")
                unchanged = sum(1 for status in results.values() if status == "unchanged")

                console.print(f"  [green]OK[/green] {generated} generated, {updated} updated, {unchanged} unchanged\n")
            except typer.Exit:
                raise
            except Exception as e:
                console.print(f"  [red]ERROR[/red] Failed to generate tests: {e}")
                raise typer.Exit(1)

            # Step 2: Run tests with screenshots
            console.print("[bold]Step 2/3:[/bold] Running tests with screenshots...")
            console.print("[dim]This may take a few minutes...[/dim]\n")

            pytest_cmd = [sys.executable, "-m", "pytest", str(tests_dir), "--browser", "chromium"]

            env = os.environ.copy()
            env["STEPPY_SCREENSHOTS"] = "1"

            result = subprocess.run(pytest_cmd, env=env, cwd=Path.cwd())

            if result.returncode != 0:
                console.print("\n[yellow]WARNING[/yellow] Some tests failed, but continuing with guide generation...")
            else:
                console.print("\n  [green]OK[/green] Tests completed successfully\n")

            # Step 3: Assemble guides (will continue below with normal flow)
            console.print("[bold]Step 3/3:[/bold] Assembling user guides...\n")

            # Find all user-guide tags in scenarios
            user_guide_tags = set()
            for scenario_file in scenarios_dir.glob("*.md"):
                scenario = parse_scenario_file(scenario_file)
                for scenario_tag in scenario.tags:
                    if scenario_tag.startswith("user-guide-"):
                        user_guide_tags.add(scenario_tag)

            if not user_guide_tags:
                console.print("[yellow]INFO[/yellow] No scenarios found with user-guide-* tags")
                raise typer.Exit(0)

            # Generate guides for all user-guide tags
            assembler = GuideAssembler(config)
            all_results = {}

            for guide_tag in sorted(user_guide_tags):
                console.print(f"\n[bold]Generating guides for tag:[/bold] {guide_tag}")
                try:
                    tag_results = assembler.generate_guides_for_tag(
                        guide_tag,
                        locale,
                        scenarios_dir,
                        output_dir,
                        screenshots_dir,
                    )
                    all_results[guide_tag] = tag_results

                    # Print summary for this tag
                    for scenario_slug, locale_results in tag_results.items():
                        for loc, result in locale_results.items():
                            if result["status"] == "success":
                                console.print(f"  [green]OK[/green] {scenario_slug} ({loc})")
                            else:
                                console.print(f"  [red]ERROR[/red] {scenario_slug} ({loc}): {result['error']}")

                except Exception as e:
                    console.print(f"  [red]ERROR[/red] {e}")

            pdf_errors = 0
            if pdf or handbook:
                pdf_errors = _emit_guide_pdfs(
                    assembler,
                    config,
                    all_results,
                    want_pdf=pdf,
                    want_handbook=handbook,
                    scenarios_dir=scenarios_dir,
                    output_dir=output_dir,
                    screenshots_dir=screenshots_dir,
                )
            if video:
                pdf_errors += _emit_guide_videos(
                    assembler,
                    config,
                    all_results,
                    scenarios_dir=scenarios_dir,
                    output_dir=output_dir,
                    screenshots_dir=screenshots_dir,
                )

            console.print("\n[green]OK[/green] Full pipeline completed\n")
            raise typer.Exit(1 if pdf_errors else 0)

        # Handle --guide mode: assemble guides for all guide-enabled scenarios
        if guide_mode:
            if not scenarios_dir.exists():
                console.print(f"[red]ERROR[/red] Scenarios directory not found: {scenarios_dir}")
                raise typer.Exit(1)

            if not screenshots_dir.exists():
                console.print(f"[red]ERROR[/red] Screenshots directory not found: {screenshots_dir}")
                console.print("\n[yellow]Hint:[/yellow] Run tests with screenshots first:")
                console.print("  [bold]steppy run --guide[/bold]")
                raise typer.Exit(1)

            try:
                all_scenarios = parse_scenarios_from_directory(scenarios_dir)
            except ParseError as e:
                _error(f"Parse error: {e}")
                raise typer.Exit(1)

            guide_scenarios = [s for s in all_scenarios if s.guide.enabled]

            if not guide_scenarios:
                console.print("[yellow]INFO[/yellow] No scenarios found with guide.enabled=true")
                raise typer.Exit(0)

            # Collect the set of user-guide-* tags exposed by these scenarios
            discovered_tags = set()
            for s in guide_scenarios:
                for t in s.tags:
                    if t.startswith("user-guide-"):
                        discovered_tags.add(t)

            if not discovered_tags:
                console.print(
                    "[yellow]INFO[/yellow] Guide-enabled scenarios have no user-guide-* tags; nothing to assemble"
                )
                raise typer.Exit(0)

            console.print(f"\n[bold]Assembling guides for {len(guide_scenarios)} guide scenario(s)[/bold]")
            console.print(f"[bold]Roles (tags):[/bold] {', '.join(sorted(discovered_tags))}")
            console.print(f"[bold]Locales:[/bold] {', '.join(locale)}\n")

            assembler = GuideAssembler(config)
            success_count = 0
            error_count = 0
            results_by_tag: dict[str, dict] = {}

            for guide_tag in sorted(discovered_tags):
                console.print(f"\n[bold cyan]{guide_tag}[/bold cyan]")
                try:
                    tag_results = assembler.generate_guides_for_tag(
                        guide_tag,
                        locale,
                        scenarios_dir,
                        output_dir,
                        screenshots_dir,
                    )
                except Exception as e:
                    console.print(f"  [red]ERROR[/red] {e}")
                    error_count += 1
                    continue
                results_by_tag[guide_tag] = tag_results

                for scenario_slug, locale_results in tag_results.items():
                    for loc, result in locale_results.items():
                        if result["status"] == "success":
                            console.print(f"  [green]OK[/green] {scenario_slug} ({loc})")
                            console.print(f"    [dim]Markdown:[/dim] {result['markdown']}")
                            console.print(f"    [dim]HTML:[/dim] {result['html']}")
                            success_count += 1
                        else:
                            console.print(f"  [red]ERROR[/red] {scenario_slug} ({loc}): {result['error']}")
                            error_count += 1

            if video:
                error_count += _emit_guide_videos(
                    assembler,
                    config,
                    results_by_tag,
                    scenarios_dir=scenarios_dir,
                    output_dir=output_dir,
                    screenshots_dir=screenshots_dir,
                )

            if pdf or handbook:
                error_count += _emit_guide_pdfs(
                    assembler,
                    config,
                    results_by_tag,
                    want_pdf=pdf,
                    want_handbook=handbook,
                    scenarios_dir=scenarios_dir,
                    output_dir=output_dir,
                    screenshots_dir=screenshots_dir,
                )

            console.print("\n[bold]Summary:[/bold]")
            console.print(f"  [green]Success:[/green] {success_count}")
            if error_count > 0:
                console.print(f"  [red]Errors:[/red] {error_count}")
            console.print()

            if error_count > 0:
                raise typer.Exit(1)
            raise typer.Exit(0)

        # Normal mode: assemble guides from existing screenshots
        if not tag:
            console.print("[red]ERROR[/red] --tag is required (or use --guide / --all)")
            console.print("\n[yellow]Example:[/yellow] steppy guide --tag user-guide-admin --locale fr")
            raise typer.Exit(1)

        # Validate tag format
        if not tag.startswith("user-guide-"):
            console.print(f"[red]ERROR[/red] Tag must be in format 'user-guide-{{role}}', got: {tag}")
            raise typer.Exit(1)

        # Check if screenshots directory exists
        if not screenshots_dir.exists():
            console.print(f"[red]ERROR[/red] Screenshots directory not found: {screenshots_dir}")
            console.print("\n[yellow]Hint:[/yellow] Run tests with screenshots first:")
            console.print(f"  [bold]steppy run --tag {tag} --screenshots[/bold]")
            raise typer.Exit(1)

        console.print(f"\n[bold]Assembling guides for tag:[/bold] {tag}")
        console.print(f"[bold]Locales:[/bold] {', '.join(locale)}\n")

        # Assemble guides
        assembler = GuideAssembler(config)

        try:
            results = assembler.generate_guides_for_tag(
                tag,
                locale,
                scenarios_dir,
                output_dir,
                screenshots_dir,
            )
        except ValueError as e:
            _error(f"{e}")
            raise typer.Exit(1)

        if not results:
            console.print(f"[yellow]INFO[/yellow] No scenarios found with tag: {tag}")
            raise typer.Exit(0)

        # Display results
        success_count = 0
        error_count = 0

        for scenario_slug, locale_results in results.items():
            console.print(f"\n[bold cyan]{scenario_slug}[/bold cyan]")

            for loc, result in locale_results.items():
                if result["status"] == "success":
                    console.print(f"  [green]OK[/green] {loc}")
                    console.print(f"    [dim]Markdown:[/dim] {result['markdown']}")
                    console.print(f"    [dim]HTML:[/dim] {result['html']}")
                    success_count += 1
                else:
                    console.print(f"  [red]ERROR[/red] {loc}: {result['error']}")
                    error_count += 1

        if video:
            error_count += _emit_guide_videos(
                assembler,
                config,
                {tag: results},
                scenarios_dir=scenarios_dir,
                output_dir=output_dir,
                screenshots_dir=screenshots_dir,
            )

        if pdf or handbook:
            error_count += _emit_guide_pdfs(
                assembler,
                config,
                {tag: results},
                want_pdf=pdf,
                want_handbook=handbook,
                scenarios_dir=scenarios_dir,
                output_dir=output_dir,
                screenshots_dir=screenshots_dir,
            )

        console.print("\n[bold]Summary:[/bold]")
        console.print(f"  [green]Success:[/green] {success_count}")
        if error_count > 0:
            console.print(f"  [red]Errors:[/red] {error_count}")

        console.print()

        if error_count > 0:
            raise typer.Exit(1)


# ── Seed commands ───────────────────────────────────────────────────────────


@app.command()
def seed(
    name: str = typer.Argument(
        ...,
        help="Name of the seed script to run (e.g., 'base' runs seed/base.py)",
    ),
    seed_dir: Path = typer.Option(
        Path("seed"),
        "--seed-dir",
        help="Directory containing seed scripts",
    ),
):
    """
    Run a seed script to set up test data.

    Seed scripts are Python files in the seed/ directory that create
    idempotent test data for scenarios.

    Example:
      steppy seed base
    """
    with _cli_errors():
        # Check if seed directory exists
        if not seed_dir.exists():
            console.print(f"[red]ERROR[/red] Seed directory not found: {seed_dir}")
            console.print("\n[yellow]Hint:[/yellow] Create a seed/ directory with seed scripts")
            raise typer.Exit(1)

        # Check if seed script exists
        seed_file = seed_dir / f"{name}.py"
        if not seed_file.exists():
            console.print(f"[red]ERROR[/red] Seed script not found: {seed_file}")

            # List available seed scripts
            available_seeds = [f.stem for f in seed_dir.glob("*.py") if f.stem != "__init__"]
            if available_seeds:
                console.print(f"\n[yellow]Available seed scripts:[/yellow] {', '.join(available_seeds)}")
            raise typer.Exit(1)

        console.print(f"\n[bold]Running seed script:[/bold] {name}")
        console.print(f"[dim]File:[/dim] {seed_file}\n")

        # Run the seed script via subprocess (bounded — a hung seed must not
        # block the CLI forever; FLAWS §5.1).
        try:
            result = subprocess.run(
                [sys.executable, str(seed_file)],
                cwd=Path.cwd(),
                capture_output=True,
                text=True,
                timeout=DEFAULT_SEED_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            _error(f"Seed script '{name}' timed out after {DEFAULT_SEED_TIMEOUT}s: {seed_file}")
            raise typer.Exit(1)

        # Print output
        if result.stdout:
            console.print(result.stdout)
        if result.stderr:
            console.print(result.stderr)

        if result.returncode == 0:
            console.print(f"\n[green]OK[/green] Seed script '{name}' completed successfully\n")
        else:
            console.print(f"\n[red]ERROR[/red] Seed script '{name}' failed with exit code {result.returncode}\n")
            raise typer.Exit(result.returncode)


# ── Build command ──────────────────────────────────────────────────────────


@app.command()
def build(
    config_file: Path = typer.Option(
        Path("steppy.guide.config.ts"),
        "--config",
        "-c",
        help="Path to the TypeScript guide config file",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Show what files would be created without writing",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Skip confirmation and overwrite existing files",
    ),
    pdf: bool = typer.Option(
        False,
        "--pdf",
        help="Also print each guide page to PDF and link it from the page (static target only)",
    ),
    handbook: bool = typer.Option(
        False,
        "--handbook",
        help="Also emit handbook/<locale>.html + .pdf — the whole site as one document (static target only)",
    ),
    video: bool = typer.Option(
        False,
        "--video",
        help=(
            "Also render a narrated MP4 per locale/layout for each guide declaring "
            "modes: ['video']. Requires ffmpeg. (Unrelated to 'steppy run --video', "
            "which records Playwright debugging footage of failed tests.)"
        ),
    ),
):
    """
    Build guide artifacts from scenario files.

    Reads a TypeScript config (steppy.guide.config.ts), parses referenced
    scenarios, generates guide HTML fragments, and writes either:
      - Angular 21 standalone components + routes + navbar (default,
        when target='angular' or omitted), or
      - A self-contained static site with full HTML pages, an index,
        manifest.json, and copied screenshot assets (when target='static')
        suitable for GitHub Pages, Netlify, S3, or any plain static host.

    Examples:
      steppy build
      steppy build --config path/to/steppy.guide.config.ts
      steppy build --dry-run
      steppy build --pdf --handbook
      steppy build --video
    """
    from .build import BuildAssembler, BuildError, load_build_config

    try:
        console.print("\n[bold cyan]Building guide artifacts[/bold cyan]\n")

        # Load config
        console.print(f"[dim]Config:[/dim] {config_file}")
        try:
            build_config = load_build_config(config_file)
        except BuildError as e:
            _error(f"{e}")
            raise typer.Exit(1)

        console.print(f"[dim]Target:[/dim] {build_config.target.value}")
        console.print(
            f"[dim]Guides:[/dim] {len(build_config.guides)} ({', '.join(g.slug for g in build_config.guides)})"
        )
        console.print(f"[dim]Locales:[/dim] {', '.join(build_config.locales)}")
        console.print(f"[dim]Output:[/dim] {build_config.output_dir}")
        if build_config.target.value == "angular":
            console.print(f"[dim]Assets:[/dim] {build_config.assets_dir}\n")
        else:
            console.print(f"[dim]Assets:[/dim] {build_config.output_dir}/assets (self-contained)\n")

        # Load steppy config if available (for role display names). Config is
        # optional for build, so a missing/invalid config only warns — but we
        # narrow the catch to ConfigError so real bugs surface via the handler.
        steppy_config = None
        try:
            steppy_config = load_config()
        except ConfigError as e:
            console.print(f"[yellow]WARNING[/yellow] Could not load config for role names: {e}")
            steppy_config = None

        assembler = BuildAssembler(
            build_config,
            project_root=Path.cwd(),
            steppy_config=steppy_config,
        )

        if dry_run:
            console.print("[yellow]DRY RUN[/yellow] — no files will be written\n")
        else:
            if pdf or handbook:
                console.print("[dim]PDF export:[/dim] rendering with headless Chromium (this takes a moment)\n")
            if video:
                console.print(
                    "[dim]Video export:[/dim] rendering frame by frame with headless Chromium + ffmpeg "
                    "(minutes per guide/locale/layout)\n"
                )

        results = assembler.build_all(dry_run=dry_run, pdf=pdf, handbook=handbook, video=video)

        # Summary
        generated = sum(1 for s in results.values() if s == "generated")
        updated = sum(1 for s in results.values() if s == "updated")
        unchanged = sum(1 for s in results.values() if s == "unchanged")
        planned = sum(1 for s in results.values() if s == "planned")

        if dry_run:
            console.print(f"[green]OK[/green] {planned} files would be written\n")
        else:
            console.print(f"[green]OK[/green] {generated} generated, {updated} updated, {unchanged} unchanged\n")

    except typer.Exit:
        raise
    except BuildError as e:
        _error(f"{e}")
        raise typer.Exit(1)
    except Exception as e:
        _report_unexpected(e)
        raise typer.Exit(1)


# ── Auth commands ───────────────────────────────────────────────────────────

auth_app = typer.Typer(help="Manage authentication for different roles")
app.add_typer(auth_app, name="auth")


@auth_app.command("login")
def auth_login(
    role: str = typer.Argument(
        ...,
        help="Role to authenticate (e.g., admin, editor, viewer)",
    ),
    auto: bool = typer.Option(
        False,
        "--auto",
        help="Log in headless with the role's configured credentials (auth mode 'form' only). Needs no terminal.",
    ),
):
    """
    Log in to an app for a specific role and save the session for future test runs.

    By default, opens a visible browser window and waits for you to log in
    manually, then checks that the browser has left the login page before saving.
    With --auto, a form-login role logs in headless through the same flow the
    generated tests use, so it also works in CI and scripts.
    """
    with _cli_errors():
        config = _load_config_or_exit()

        # Validate role
        if role not in config.apps:
            console.print(f"[red]ERROR[/red] Unknown role: {role}")
            console.print(f"\n[yellow]Available roles:[/yellow] {', '.join(config.apps.keys())}")
            raise typer.Exit(1)

        app_config = config.apps[role]
        if auto:
            if app_config.auth.mode != "form":
                console.print(
                    f"[red]ERROR[/red] --auto logs in through the login form, and role '{role}' uses "
                    f"auth mode '{app_config.auth.mode}'."
                )
                raise typer.Exit(1)
            if not app_config.auth.credentials:
                console.print(f"[red]ERROR[/red] --auto needs apps.{role}.auth.credentials (username/password).")
                raise typer.Exit(1)
        elif sys.stdin is None or not sys.stdin.isatty():
            # input() would end in a bare EOFError; say what to do instead.
            console.print("[red]ERROR[/red] steppy auth login waits for you to press Enter, and there is no terminal.")
            console.print(f"  For a form-login role, log in headless instead: steppy auth login {role} --auto")
            raise typer.Exit(1)

        console.print(f"\n[bold]{'Automatic' if auto else 'Interactive'} login for:[/bold] {app_config.name} ({role})")
        console.print(f"[dim]URL:[/dim] {app_config.url}\n")

        from playwright.sync_api import sync_playwright

        from .auth import authenticate_form, get_storage_state_path, interactive_login

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=auto)

            try:
                if auto:
                    authenticate_form(browser, role, app_config, config, use_cache=False).close()
                else:
                    interactive_login(role, config, browser)
                state_path = get_storage_state_path(role, Path(config.auth.storage_state_dir))
                console.print(f"\n[green]OK[/green] Authentication saved for {role}")
                console.print(f"[dim]Storage state saved to {escape(str(state_path))}[/dim]")
            except Exception as e:
                console.print(f"\n[red]ERROR[/red] Login failed: {e}")
                raise typer.Exit(1)
            finally:
                browser.close()


@auth_app.command("status")
def auth_status():
    """
    Show authentication status for all roles.

    Displays which roles have valid cached sessions and their age.
    """
    from .auth import get_storage_state_status

    with _cli_errors():
        config = _load_config_or_exit()

        status = get_storage_state_status(config)

        console.print("\n[bold]Authentication Status:[/bold]\n")

        table = Table()
        table.add_column("Role", style="cyan")
        table.add_column("Status", style="bold")
        table.add_column("Age", style="yellow")
        table.add_column("Valid", style="green")

        for role, info in status.items():
            if info["exists"]:
                age_str = f"{info['age_minutes']}m ago"
                valid_str = "[green]Yes[/green]" if info["valid"] else "[red]Expired[/red]"
                status_str = "[green]Cached[/green]"
            else:
                age_str = "-"
                valid_str = "[dim]No[/dim]"
                status_str = "[dim]Not logged in[/dim]"

            table.add_row(role, status_str, age_str, valid_str)

        console.print(table)
        console.print()

        # Show TTL info
        ttl_minutes = config.auth.storage_state_ttl // 60
        console.print(f"[dim]Cache TTL: {ttl_minutes} minutes[/dim]")
        console.print(f"[dim]Auth directory: {config.auth.storage_state_dir}[/dim]\n")


@auth_app.command("clear")
def auth_clear(
    role: Optional[str] = typer.Argument(
        None,
        help="Role to clear (omit to clear all roles)",
    ),
):
    """
    Clear cached authentication state.

    If no role is specified, clears all cached sessions.
    """
    from .auth import clear_storage_state

    with _cli_errors():
        config = _load_config_or_exit()

        auth_dir = Path(config.auth.storage_state_dir)

        if role and role not in config.apps:
            console.print(f"[red]ERROR[/red] Unknown role: {role}")
            console.print(f"\n[yellow]Available roles:[/yellow] {', '.join(config.apps.keys())}")
            raise typer.Exit(1)

        if role:
            console.print(f"\n[bold]Clearing authentication for:[/bold] {role}")
        else:
            console.print("\n[bold]Clearing all authentication states[/bold]")

        cleared = clear_storage_state(role, auth_dir)

        if cleared:
            for cleared_role in cleared:
                console.print(f"  [green]OK[/green] Cleared: {cleared_role}")
            console.print(f"\n[green]OK[/green] Cleared {len(cleared)} role(s)\n")
        else:
            if role:
                console.print(f"\n[yellow]INFO[/yellow] No cached state found for {role}\n")
            else:
                console.print("\n[yellow]INFO[/yellow] No cached states found\n")


# ── Parallel-mode subcommands ──────────────────────────────────────────────
# These exist to support `steppy run --workers N >= 2`. The `worker` command
# is hidden because it's invoked by the coordinator inside a worker container,
# never directly by users. The `stacks cleanup` command is a rescue tool for
# orphans left behind by a force-killed run.


@app.command(hidden=True)
def worker():
    """Internal — worker loop run inside a steppy parallel-mode container.

    Reads STEPPY_WORKER_ID, STEPPY_COORD_DIR, and the usual STEPPY_* env vars,
    then loops claiming scenarios from the shared queue and invoking pytest
    until empty.
    """
    with _cli_errors():
        from .worker import worker_main

        raise typer.Exit(worker_main())


@app.command(
    "_preauth-run",
    hidden=True,
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def _preauth_run(
    ctx: typer.Context,
    timeout: int = typer.Option(600, "--timeout", help="Seconds before giving up."),
):
    """Internal — parallel-mode pre-auth lock-wrapper, run inside a
    steppy-preauth container. Elects one winner across the worker stacks to run
    the configured ``parallel.pre_auth.command`` in-network; the losing stacks
    wait for its sentinel. The command to run is everything after ``--``."""
    with _cli_errors():
        from ._parallel.preauth import run_preauth

        command = list(ctx.args)
        if command and command[0] == "--":
            command = command[1:]
        raise typer.Exit(run_preauth(command, timeout=timeout))


stacks_app = typer.Typer(
    name="stacks",
    help="Manage parallel-mode docker stacks (rescue commands).",
    add_completion=False,
)
app.add_typer(stacks_app, name="stacks")


@stacks_app.command("cleanup")
def stacks_cleanup():
    """Remove orphaned `steppy-run-*` Compose projects (and their networks /
    volumes) left behind by a force-killed parallel run."""
    with _cli_errors():
        from .coordinator import cleanup_stacks

        raise typer.Exit(cleanup_stacks())


# ── Coverage map commands ──────────────────────────────────────
# The `steppy coverage ...` sub-app lives in coverage_cli.py (FLAWS §4.2). Both
# it and cli.py share the console / error scaffold via the leaf module
# _cli_common (coverage_cli imports nothing from cli, so there is no import
# cycle). Its top level imports no steppy.coverage.* (those stay lazy inside the
# command bodies), so `import steppy.cli` stays coverage-free.
from .coverage_cli import coverage_app  # noqa: E402

app.add_typer(coverage_app, name="coverage")

# ── Flake history (`steppy flaky`) ─────────────────────────────
# Same leaf-module shape as coverage_cli: it reads .steppy/history.jsonl through
# the stdlib-only `steppy.history` and imports nothing from coordinator/_parallel,
# so the no-parallel-import guard still holds.
from .flaky_cli import register_flaky  # noqa: E402
from .migrate_cli import register_migrate  # noqa: E402

register_flaky(app)

# `steppy migrate custom`. The rewrite engine under `steppy.migrate` is
# imported inside the command body, not here.
register_migrate(app)

# ── Project scaffolding (`steppy init` / `steppy update`) ──────
# Both live in leaf modules that share the _cli_common scaffold (no cycle);
# update_cli reuses init_cli's asset primitives.
from .init_cli import register_init  # noqa: E402

register_init(app)

from .update_cli import register_update  # noqa: E402

register_update(app)

# ── Recorder (`steppy record`) ─────────────────────────────────
# Leaf module (shares the _cli_common scaffold, no cycle); shells out to
# `playwright codegen` and post-processes the output into a draft scenario.
from .record_cli import register_record  # noqa: E402

register_record(app)

# ── Video readiness (`steppy video check`) ─────────────────────
# Leaf module: its top level imports no `steppy.video` / `steppy.tts` (those
# stay lazy inside the command body), so `import steppy.cli` keeps pulling in
# neither playwright nor httpx.
from .video_cli import register_video  # noqa: E402

register_video(app)


# ── Results bundles (`steppy bundle`) ──────────────────────────
# Leaf module: every `steppy.bundle` import stays lazy inside the command
# bodies, so `import steppy.cli` pulls in neither the output/ walker nor the
# optional `zstandard` (tests/test_no_bundle_import_on_default_path.py).
from .bundle_cli import register_bundle  # noqa: E402

register_bundle(app)


# ── Run report site (`steppy report`) ──────────────────────────
# Same leaf treatment: `steppy.report` builds a Jinja environment, reads tar
# archives and walks output/, so every import of it stays inside the command body
# (tests/test_no_report_import_on_default_path.py). Note `report` names three
# other things in this codebase — the `report:` config block for pytest
# artifacts, the `steppy-dev[report]` extra (pytest-html), and `steppy coverage
# report` — none of which this command touches.
from .report_cli import register_report  # noqa: E402

register_report(app)

# In-app tours (`steppy tours`). The command body and the generate/validate
# hooks import `steppy.tours` lazily, behind `tours.enabled`
# (tests/test_no_tours_import_on_default_path.py).
from .tours_cli import register_tours  # noqa: E402

register_tours(app)


if __name__ == "__main__":
    app()
