"""``steppy migrate custom`` — rewrite mechanical custom blocks as real steps.

A leaf CLI module, registered via ``register_migrate(app)`` like
``register_flaky``. Everything under :mod:`steppy.migrate` is imported *inside*
the command body, so ``import steppy.cli`` stays free of it
(``tests/test_no_migrate_import_on_default_path.py``).

The command is deliberately unexciting: it shows you a diff, tells you what it
would not touch and why, and only writes when you drop ``--dry-run``. The
residue it reports is the useful part — it is the input to the next round of
commands.
"""

from __future__ import annotations

import difflib
import json as _json
from pathlib import Path
from typing import Optional

import typer
from rich.markup import escape
from rich.table import Table

from ._cli_common import _cli_errors, _load_config_or_exit, console

migrate_app = typer.Typer(help="Rewrite mechanical `action: custom` blocks as declarative steps.")


def register_migrate(app: typer.Typer) -> None:
    app.add_typer(migrate_app, name="migrate")


@migrate_app.command("custom")
def migrate_custom(
    scenarios_dir: Path = typer.Option(Path("scenarios"), "--scenarios-dir", help="Where the scenario files live."),
    scenario: Optional[str] = typer.Option(None, "--scenario", help="Only this scenario (by file stem)."),
    dry_run: bool = typer.Option(True, "--dry-run/--write", help="Preview (default) or rewrite the files."),
    show_diff: bool = typer.Option(False, "--diff", help="Print the rewrite for every converted block."),
    show_skipped: bool = typer.Option(False, "--skipped", help="List every block left alone, with the reason."),
    report: Optional[Path] = typer.Option(None, "--report", help="Write the full result as JSON."),
) -> None:
    """Convert the ``custom`` blocks that are pure Playwright plumbing.

    A block is converted only when EVERY statement in it maps to a rule, and
    only when the rewrite passes verification: the generated code parses, no
    selector is lost, and every operation is conserved. Everything else is left
    exactly as it was and reported.
    """
    with _cli_errors():
        from .generator import SteppyGenerator
        from .migrate import apply_plan, plan_scenarios

        if not scenarios_dir.is_dir():
            console.print(f"[red]No scenarios directory at {scenarios_dir}[/red]")
            raise typer.Exit(code=1)

        config = _load_config_or_exit()
        generator = SteppyGenerator(config)

        plans = plan_scenarios(scenarios_dir, only=scenario)
        if not plans:
            console.print("[green]No `action: custom` blocks found — nothing to migrate.[/green]")
            return

        results = []
        converted = skipped = 0
        steps_before = steps_after = 0
        for plan in plans:
            outcomes = apply_plan(plan, generator, write=not dry_run)
            for outcome in outcomes:
                verification = outcome.verification
                if outcome.applied:
                    converted += 1
                    steps_before += 1
                    steps_after += len(outcome.step.steps or [])
                else:
                    skipped += 1
                results.append(
                    {
                        "file": plan.path.name,
                        "line": outcome.step.line,
                        "heading": outcome.step.heading.strip(),
                        "applied": outcome.applied,
                        "reason": (verification.detail if verification else outcome.step.reason)
                        or "; ".join(verification.substitutions if verification else []),
                        "steps": len(outcome.step.steps or []),
                    }
                )
                if show_diff and outcome.applied:
                    _print_block_diff(plan.path.name, outcome)

        _print_summary(converted, skipped, steps_before, steps_after, dry_run)
        if show_skipped:
            _print_skipped(results)
        if report:
            report.write_text(_json.dumps(results, indent=2), encoding="utf-8")
            console.print(f"[dim]report: {report}[/dim]")


def _print_block_diff(filename: str, outcome) -> None:
    original = ["- action: custom", "- code: |"] + ["    " + line for line in outcome.step.code.splitlines()]
    rewritten = [line for line in outcome.markdown.splitlines() if not line.startswith("###")]
    console.print(f"\n[bold]{filename}:{outcome.step.line}[/bold]  {outcome.step.heading.strip()}")
    for line in difflib.unified_diff(original, rewritten, lineterm="", n=99):
        if line.startswith(("---", "+++", "@@")):
            continue
        style = "red" if line.startswith("-") else "green" if line.startswith("+") else "dim"
        console.print(f"  [{style}]{escape(line)}[/{style}]")


def _print_summary(converted: int, skipped: int, before: int, after: int, dry_run: bool) -> None:
    total = converted + skipped
    verb = "would convert" if dry_run else "converted"
    console.print()
    table = Table(show_header=True, header_style="bold")
    table.add_column("custom blocks", justify="right")
    table.add_column(verb, justify="right")
    table.add_column("left alone", justify="right")
    table.add_column("steps produced", justify="right")
    table.add_row(
        str(total),
        f"[green]{converted}[/green]" + (f" ({converted / total:.0%})" if total else ""),
        str(skipped),
        f"{after} from {before}" + (f" ({after / before:.2f}×)" if before else ""),
    )
    console.print(table)
    if after > before:
        # Said out loud because the collapse metric counts blocks removed and is
        # silent about steps added — and in a guide scenario every added step is
        # a published step with its own number and screenshot slot.
        console.print(
            f"[yellow]Note:[/yellow] {after - before} more steps than blocks. A block holding two "
            "operations becomes two steps; trailing sleeps are folded into wait_after: rather than "
            "becoming steps of their own."
        )
    if dry_run:
        console.print("[dim]Nothing was written. Re-run with --write to apply.[/dim]")


def _print_skipped(results: list[dict]) -> None:
    reasons: dict[str, int] = {}
    for row in results:
        if row["applied"]:
            continue
        reasons[row["reason"] or "unknown"] = reasons.get(row["reason"] or "unknown", 0) + 1
    if not reasons:
        return
    console.print("\n[bold]Why blocks were left alone[/bold]  [dim](the input to the next round)[/dim]")
    for reason, count in sorted(reasons.items(), key=lambda kv: -kv[1])[:15]:
        # A selector is full of square brackets, which Rich reads as markup.
        console.print(f"  {count:5d}  {escape(reason)}")
