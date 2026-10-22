"""``steppy tours`` — compile in-app tours — and the tour hooks of ``generate``/``validate``.

A leaf CLI module registered via ``register_tours(app)``. Everything under
:mod:`steppy.tours` is imported *inside* function bodies, so
``import steppy.cli`` stays free of it
(``tests/test_no_tours_import_on_default_path.py``), and a project without a
``tours:`` block never loads it at all.

``steppy tours`` writes the tour files without touching the tests: it is the
command an app's own build pipeline runs to bundle its tours. ``steppy
generate`` does the same and also appends the CI replays.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Optional

import typer
from rich.markup import escape

from ._cli_common import _cli_errors, _error, _load_config_or_exit, console
from .models import SteppyConfig, Scenario

if TYPE_CHECKING:  # pragma: no cover
    from .tours.diagnostics import Diagnostic
    from .tours.pipeline import TourPipeline

_STYLE = {"error": "[red]ERROR[/red]", "warning": "[yellow]WARNING[/yellow]", "info": "[dim]INFO[/dim]"}


def tours_enabled(config: SteppyConfig) -> bool:
    return config.tours is not None and config.tours.enabled


def print_tour_diagnostics(diagnostics: "list[Diagnostic]", *, show_info: bool = False) -> None:
    for diag in diagnostics:
        if diag.severity == "info" and not show_info:
            continue
        console.print(f"{_STYLE[diag.severity]} {escape(diag.render())}")


def print_file_statuses(statuses: dict[str, str], *, dry_run: bool = False) -> None:
    labels = {
        "generated": "[green]+ GENERATED[/green]",
        "updated": "[yellow]~ UPDATED[/yellow]  ",
        "unchanged": "[dim]= UNCHANGED[/dim]",
        "removed": "[red]- REMOVED[/red]  ",
        "missing": "[red]! MISSING[/red]  ",
    }
    cwd = Path.cwd()
    for path, status in statuses.items():
        shown = Path(path)
        if cwd in shown.parents:
            shown = shown.relative_to(cwd)
        console.print(f"  {labels.get(status, status)} {escape(str(shown))}")


def warn_opted_in_but_disabled(config: SteppyConfig, scenarios: list[Scenario]) -> None:
    """One hint when scenarios ask for tours that the config does not produce."""
    if tours_enabled(config):
        return
    wanting = [s.slug for s in scenarios if s.tour is not None and s.tour.enabled]
    if wanting:
        console.print(
            f"[yellow]WARNING[/yellow] {len(wanting)} scenario(s) set `tour:` but in-app tours are off "
            "(set tours.enabled: true in config.yaml): " + escape(", ".join(sorted(wanting)[:5]))
        )


def prepare_tours_or_exit(
    config: SteppyConfig,
    *,
    scenarios_dir: Path,
    selected: Optional[list[Scenario]] = None,
    all_scenarios: Optional[list[Scenario]] = None,
    build_id_override: Optional[str] = None,
    mode: str = "generate",
    show_info: bool = False,
) -> "TourPipeline":
    """Compile tours, print diagnostics, and exit 1 on any error (nothing written yet)."""
    from .tours.pipeline import prepare_tours

    pipeline = prepare_tours(
        config,
        project_root=Path.cwd(),
        scenarios_dir=scenarios_dir,
        selected=selected,
        all_scenarios=all_scenarios,
        build_id_override=build_id_override,
        mode=mode,  # type: ignore[arg-type]
    )
    print_tour_diagnostics(pipeline.diagnostics, show_info=show_info)
    if pipeline.has_errors:
        _error("In-app tours have errors; nothing was written (fix them, or set tours.enabled: false)")
        raise typer.Exit(1)
    return pipeline


def run_tour_checks(config: SteppyConfig, scenarios: list[Scenario], *, scenarios_dir: Path) -> bool:
    """``steppy validate``'s tour pass; returns True when there are errors."""
    if not tours_enabled(config):
        warn_opted_in_but_disabled(config, scenarios)
        return False
    from .tours.pipeline import prepare_tours

    pipeline = prepare_tours(
        config, project_root=Path.cwd(), scenarios_dir=scenarios_dir, all_scenarios=scenarios, mode="validate"
    )
    print_tour_diagnostics(pipeline.diagnostics)
    if not pipeline.has_errors and pipeline.result is not None:
        count = len(pipeline.result.tours)
        console.print(f"[green]OK[/green] {count} in-app tour(s) compile")
    return pipeline.has_errors


def register_tours(app: typer.Typer) -> None:
    app.command(name="tours")(tours_command)


def tours_command(
    scenarios_dir: Path = typer.Option(
        Path("scenarios"), "--scenarios-dir", "-d", help="Directory containing scenario files"
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be written without writing anything"),
    check: bool = typer.Option(
        False,
        "--check",
        help="CI gate: exit 1 if an app-assets copy is missing or its content is stale (writes nothing; "
        "compares against the build id each copy records)",
    ),
    build_id: Optional[str] = typer.Option(
        None, "--build-id", help="Build id to stamp, overriding tours.build_id (same grammar)"
    ),
    explain: bool = typer.Option(False, "--explain", help="Also print why each step was left out of its tour"),
) -> None:
    """Compile in-app tours from every `tour:`-enabled scenario.

    Writes <output.tours_dir>/<build-id>/ and a flat copy into every app-assets
    target of the `tours:` block in config.yaml. Tests are not touched; use
    `steppy generate` for the CI replays.

    --check compares only the app-assets copies (the files an app commits), for
    the build id each one records: the canonical tree is a gitignored build
    output, and committed copies always carry an older id under git-sha.
    """
    with _cli_errors():
        config = _load_config_or_exit()
        if not tours_enabled(config):
            _error("In-app tours are disabled: add a `tours:` block with `enabled: true` to config.yaml")
            raise typer.Exit(1)
        if not scenarios_dir.is_dir():
            _error(f"Scenarios directory not found: {scenarios_dir}")
            raise typer.Exit(1)
        pipeline = prepare_tours_or_exit(
            config,
            scenarios_dir=scenarios_dir,
            build_id_override=build_id,
            mode="check" if check else "tours",
            show_info=explain,
        )
        if check:
            _report_check(pipeline)
            return
        statuses = pipeline.emit(dry_run=dry_run)
        console.print(f"\n[bold]In-app tours[/bold] (build {escape(str(pipeline.build_id))})")
        print_file_statuses(statuses)
        counts = {k: sum(1 for s in statuses.values() if s == k) for k in ("generated", "updated", "removed")}
        verb = "would be " if dry_run else ""
        console.print(
            f"\n[green]Summary:[/green] {len(pipeline.result.tours) if pipeline.result else 0} tour(s); "
            f"{counts['generated']} {verb}generated, {counts['updated']} {verb}updated, "
            f"{counts['removed']} {verb}removed"
        )


def _report_check(pipeline: "TourPipeline") -> None:
    """``--check``: print what differs in the app-assets copies; exit 1 when anything does."""
    if not pipeline.settings.app_assets_dirs:
        console.print(
            "[green]OK[/green] Tours compile; no app-assets target is configured, so there are no committed "
            "tour files to compare"
        )
        return
    statuses = pipeline.statuses()
    targets = len(pipeline.settings.app_assets_dirs)
    console.print(f"\n[bold]In-app tours[/bold] (checking {targets} app-assets target(s))")
    print_file_statuses(statuses)
    for directory, recorded in pipeline.checked.items():
        if recorded is None or recorded == pipeline.build_id:
            continue
        here = (
            f"the build id here is {pipeline.build_id!r}"
            if pipeline.build_id
            else f"the build id cannot be resolved here ({pipeline.build_id_error})"
        )
        console.print(
            f"[dim]NOTE[/dim] {escape(str(directory))} holds the tours of build {recorded!r} and {escape(here)}; "
            "its content was compared for its own build"
        )
    stale = sum(1 for s in statuses.values() if s != "unchanged")
    for plan in pipeline.plans:
        eol_only = [
            name
            for name, data in plan.writes.items()
            if (plan.directory / name).is_file()
            and (on_disk := (plan.directory / name).read_bytes()) != data
            and on_disk.replace(b"\r\n", b"\n") == data
        ]
        if eol_only:
            console.print(
                f"[yellow]HINT[/yellow] {len(eol_only)} file(s) in {escape(str(plan.directory))} differ only in line "
                "endings: git converted them on checkout, and regenerating cannot fix that. Mark the folder binary "
                "in the app repository's .gitattributes (`<folder>/*.json -text`)."
            )
    if stale:
        _error(f"{stale} tour file(s) are missing or stale; run `steppy tours` and commit the result")
        raise typer.Exit(1)
    console.print("\n[green]OK[/green] Tour files are up to date")
