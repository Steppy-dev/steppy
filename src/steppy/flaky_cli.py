"""``steppy flaky`` — rank scenarios by how often they cost you attention.

Reads the append-only history that parallel runs leave at
``.steppy/history.jsonl`` (see :mod:`steppy.history`) and answers the question a
single run's summary cannot: *which* of my three hundred scenarios are the
unreliable ones? That ranking is the whole point — a list of five slugs is
actionable in a way that twenty separate red runs never were.

A leaf module, like ``coverage_cli``: it imports ``_cli_common`` and
``history`` (stdlib-only) and nothing from ``cli`` / ``coordinator`` /
``_parallel``, so ``import steppy.cli`` stays free of parallel-mode code
(``tests/test_no_parallel_import_on_default_path.py``).
"""

from __future__ import annotations

import json as _json
from pathlib import Path
from typing import Optional

import typer
from rich.table import Table

from ._cli_common import _cli_errors, console


DEFAULT_WINDOW = 20


def _rate_style(rate: float) -> str:
    if rate >= 0.5:
        return "bold red"
    if rate >= 0.2:
        return "yellow"
    return "dim"


def register_flaky(app: typer.Typer) -> None:
    """Attach the ``flaky`` command to the root Typer app."""

    @app.command("flaky")
    def flaky(
        last: int = typer.Option(
            DEFAULT_WINDOW,
            "--last",
            "-n",
            min=1,
            help=f"Consider only the N most recent runs (default: {DEFAULT_WINDOW}).",
        ),
        min_rate: float = typer.Option(
            0.0,
            "--min-rate",
            min=0.0,
            max=1.0,
            help="Only show scenarios whose flake rate is at least this (0.0-1.0). "
            "e.g. --min-rate 0.2 hides anything that misbehaved in under a fifth of runs.",
        ),
        limit: int = typer.Option(20, "--limit", min=1, help="Maximum rows to print."),
        as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON instead of a table."),
        prune_to: Optional[int] = typer.Option(
            None,
            "--prune",
            min=1,
            help="Rewrite the history file keeping only the newest N runs, then exit.",
        ),
    ):
        """Rank scenarios by flakiness across recent runs.

        History is recorded by parallel runs (`steppy run --workers N`); the
        single-worker path produces no per-scenario records, so runs made with
        `--workers 1` do not appear here.

        The flake rate counts every run where a scenario did not pass first
        try — outright failures included. A scenario failing 7 of 10 runs is not
        healthier than one flaking 7 of 10; both cost the same attention.
        """
        with _cli_errors():
            from .history import flake_stats, history_path, load, prune

            project_root = Path.cwd()

            if prune_to is not None:
                dropped = prune(project_root, keep_runs=prune_to)
                console.print(f"[green]OK[/green] Pruned {dropped} record(s); kept the newest {prune_to} run(s).")
                raise typer.Exit(0)

            path = history_path(project_root)
            records = load(project_root, last_runs=last)
            if not records:
                if as_json:
                    console.print_json(data={"runs": 0, "scenarios": []})
                    raise typer.Exit(0)
                console.print(f"[yellow]No run history yet[/yellow] [dim]({path})[/dim]")
                console.print(
                    "\nHistory is written by parallel runs. Record some with:\n  [bold]steppy run --workers 4[/bold]"
                )
                raise typer.Exit(0)

            run_count = len({r["run_id"] for r in records})
            stats = [s for s in flake_stats(records) if s.flake_rate >= min_rate]

            if as_json:
                console.print_json(
                    _json.dumps(
                        {
                            "runs": run_count,
                            "window": last,
                            "scenarios": [
                                {
                                    "slug": s.slug,
                                    "runs": s.runs,
                                    "failures": s.failures,
                                    "flakes": s.flakes,
                                    "flake_rate": round(s.flake_rate, 4),
                                    "median_s": s.median_s,
                                    "p95_s": s.p95_s,
                                    "last_outcome": s.last_outcome,
                                    "fingerprints": s.fingerprints,
                                }
                                for s in stats[:limit]
                            ],
                        }
                    )
                )
                raise typer.Exit(0)

            if not stats:
                console.print(
                    f"[green]✓[/green] No scenario exceeded a {min_rate:.0%} flake rate "
                    f"across the last {run_count} run(s)."
                )
                raise typer.Exit(0)

            table = Table(title=f"Flakiest scenarios — last {run_count} run(s)", title_justify="left")
            table.add_column("scenario", overflow="fold")
            table.add_column("runs", justify="right")
            table.add_column("fail", justify="right")
            table.add_column("flaky", justify="right")
            table.add_column("rate", justify="right")
            table.add_column("median", justify="right")
            table.add_column("p95", justify="right")
            table.add_column("last")

            for s in stats[:limit]:
                table.add_row(
                    s.slug,
                    str(s.runs),
                    f"[red]{s.failures}[/red]" if s.failures else "0",
                    f"[yellow]{s.flakes}[/yellow]" if s.flakes else "0",
                    f"[{_rate_style(s.flake_rate)}]{s.flake_rate:.0%}[/{_rate_style(s.flake_rate)}]",
                    f"{s.median_s:.0f}s" if s.median_s is not None else "—",
                    f"{s.p95_s:.0f}s" if s.p95_s is not None else "—",
                    s.last_outcome,
                )
            console.print(table)

            hidden = len(stats) - min(limit, len(stats))
            if hidden > 0:
                console.print(f"[dim]… and {hidden} more (raise --limit to see them)[/dim]")

            # A scenario that always fails the same way is a bug, not a flake —
            # worth saying out loud, because the two need opposite responses.
            consistent = [s for s in stats[:limit] if len(s.fingerprints) == 1 and s.failures]
            if consistent:
                console.print(
                    f"\n[dim]{len(consistent)} scenario(s) always fail the same way "
                    f"(single fingerprint) — likely a real bug, not contention.[/dim]"
                )
            console.print(f"[dim]source: {path}[/dim]")
