"""``steppy report`` — render one run to a static site (leaf CLI module).

Module scope stays typer + stdlib + ``_cli_common``. Every ``steppy.report`` (and
``steppy.bundle``) import lives inside the command body, because ``cli.py`` imports
*this* module eagerly to register the command — so a lazy import here is the only
thing keeping the site renderer, the tar reader and the ``output/`` walker off the
path a plain ``steppy run`` takes. Guard:
``tests/test_no_report_import_on_default_path.py``.

Naming note, since "report" is crowded: ``config.yaml``'s ``report:`` block is
about pytest artifacts (JUnit, HTML, traces, videos, reruns), the
``steppy-dev[report]`` extra is pytest-html, and ``steppy coverage report`` prints gap
buckets. This command is the *run report site* — the thing you publish.
"""

from pathlib import Path
from typing import Optional

import typer
from rich.markup import escape

from ._cli_common import console, _cli_errors, _error


def register_report(app: typer.Typer) -> None:
    """Attach the ``report`` command to the root Typer app."""

    @app.command("report")
    def report(
        bundle: Optional[Path] = typer.Argument(
            None,
            help="A .tar.zst/.tar.gz bundle, or an unpacked bundle directory. Omit to report on ./output/.",
        ),
        output: Optional[Path] = typer.Option(
            None,
            "--output",
            "-o",
            help="Where to write the site (default: output/report).",
        ),
        project_root: Path = typer.Option(Path("."), "--project-root", help="Project root (default: .)."),
        output_dir: Optional[Path] = typer.Option(
            None, "--output-dir", help="The run's output/ tree to read (default: <project-root>/output)."
        ),
        run_id: Optional[str] = typer.Option(
            None, "--run-id", help="Which run to report on when output/ holds more than one."
        ),
        screenshots: str = typer.Option(
            "failed",
            "--screenshots",
            help="Which captures to copy into the site: failed (default), all, or none.",
        ),
        badge_surface: str = typer.Option(
            "all",
            "--badge-surface",
            help="Surface the coverage badge measures: all (endpoints + routes), endpoints, or routes.",
        ),
        badge_label: Optional[str] = typer.Option(
            None, "--badge-label", help="Left-hand text on the badge (default: 'e2e surface')."
        ),
        title: Optional[str] = typer.Option(None, "--title", help="Heading for the report pages."),
        templates_dir: Optional[Path] = typer.Option(
            None, "--templates-dir", help="Override the template search root."
        ),
        force: bool = typer.Option(
            False, "--force", "-f", help="Replace the contents of a non-empty output directory."
        ),
        dry_run: bool = typer.Option(False, "--dry-run", help="Report what would be written, write nothing."),
        as_json: bool = typer.Option(False, "--json", help="Print report.json to stdout instead of a summary."),
        open_browser: bool = typer.Option(False, "--open", help="Open the rendered report in a browser."),
    ) -> None:
        """Render one run's results to a self-contained static site.

        Reads a results bundle — or the live ``output/`` tree of a run that used
        ``steppy run --bundle`` — and writes ``index.html``, a page per scenario,
        the coverage gap report, a machine-readable ``report.json`` and a
        shields.io ``badge.json``. Everything is relative and self-contained, so it
        works from a file:// path, a CI artifact, or GitHub/GitLab Pages.

        **The exit code describes rendering, not the run.** A report of a failing
        run still exits 0 — it rendered fine. Gate your build on ``steppy run``
        itself (or read ``totals.exit_code`` out of ``report.json``); a report step
        that goes red for a red run is a step people delete.
        """
        with _cli_errors():
            from .report.build import ReportError, build_report

            try:
                result = build_report(
                    project_root=project_root,
                    bundle=bundle,
                    site_dir=output,
                    output_dir=output_dir,
                    run_id=run_id,
                    screenshots=screenshots,
                    badge_surface=badge_surface,
                    badge_label=badge_label,
                    title=title,
                    templates_dir=templates_dir,
                    force=force,
                    dry_run=dry_run,
                )
            except ReportError as e:
                _error(str(e))
                raise typer.Exit(1)
            except ValueError as e:
                # The policy/surface validators, which are worth a clean message
                # rather than a traceback.
                _error(str(e))
                raise typer.Exit(1)

            if as_json:
                from .contracts.report import report_bytes

                print(report_bytes(result.model).decode("utf-8"), end="")
                raise typer.Exit(0)

            _print_summary(result, dry_run=dry_run)

            if open_browser and not dry_run:
                import webbrowser

                webbrowser.open((result.site_dir / "index.html").resolve().as_uri())


def _print_summary(result, *, dry_run: bool) -> None:
    """The post-render summary. Mirrors ``steppy build``'s counts vocabulary."""
    totals = result.model.totals
    verb = "Would render" if dry_run else "Rendered"
    colour = "green" if totals.verdict in ("passed", "skipped") else "red"
    console.print(
        f"[{colour}]{totals.verdict.upper()}[/{colour}] {totals.scenarios} scenario(s), {totals.tests} test(s)"
    )
    if totals.outcomes:
        console.print("[dim]" + "  ".join(f"{count} {name}" for name, count in totals.outcomes.items()) + "[/dim]")

    causes = result.model.failure_causes
    if causes:
        console.print(f"\n[bold]Failure causes[/bold] ({len(causes)} distinct)")
        for cause in causes[:5]:
            # escape(), because a headline routinely contains a CSS selector and
            # Rich would read `[data-testid="x"]` as markup and swallow it.
            console.print(f"  [red]×{cause.count}[/red] {escape(cause.headline)} [dim]({cause.fingerprint})[/dim]")
        if len(causes) > 5:
            console.print(f"  [dim]… and {len(causes) - 5} more[/dim]")

    coverage = result.model.coverage
    if coverage.tier != "none":
        pct = coverage.badge.percent
        measured = f"{pct}%" if pct is not None else "n/a"
        console.print(
            f"\n[bold]E2E surface[/bold] {measured} "
            f"[dim]({coverage.badge.tested}/{coverage.badge.declared}, source: {coverage.tier})[/dim]"
        )

    console.print(f"\n{verb} {len(result.files)} file(s) → {result.site_dir}")
    if result.copied:
        console.print(f"[dim]{result.copied} screenshot(s) copied, {_human(result.copied_bytes)}[/dim]")
    if not dry_run:
        console.print(f"[dim]Open {result.site_dir / 'index.html'}[/dim]")

    for warning in result.warnings:
        console.print(f"[yellow]WARNING[/yellow] {escape(warning)}")
    for warning in result.model.coverage.warnings:
        console.print(f"[yellow]WARNING[/yellow] {escape(warning)}")


def _human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GiB"  # pragma: no cover - the loop returns first
