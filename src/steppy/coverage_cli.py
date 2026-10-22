"""Coverage-map CLI commands (`steppy coverage ...`).

Extracted from cli.py (FLAWS §4.2) — these ~200 lines are a self-contained
sub-app that the god module no longer needs to carry.

Everything under `steppy coverage` lazy-imports steppy.coverage.* — plain
runs never load it (tests/test_no_coverage_import_on_default_path.py). Keep
all ``from .coverage.* import`` statements *inside* the command bodies so
importing this module (which `import steppy.cli` does) stays coverage-free.
"""

from pathlib import Path
from typing import Optional

import typer
from rich.markup import escape
from rich.table import Table

from ._cli_common import console, _cli_errors, _error
from .config import load_config

coverage_app = typer.Typer(
    name="coverage",
    help="Coverage map: record with `steppy run --coverage`, refresh the declared "
    "surface from OpenAPI/sitemap manifests, report what is NOT yet tested.",
    add_completion=False,
)

triage_app = typer.Typer(
    name="triage",
    help="Persisted per-node triage flags (ui_testable | non_ui | unknown). "
    "Survive merges and manifest refreshes — flag webhooks/cron callbacks "
    "non_ui once and the gap report discounts them forever. These live in "
    ".steppy/coverage.db, which is gitignored: for CI (and the badge), declare "
    "coverage.non_ui patterns in config.yaml instead, and use these commands for "
    "the one-off a pattern should not cover.",
    add_completion=False,
)
coverage_app.add_typer(triage_app, name="triage")


def _coverage_settings():
    from .coverage.merge import CoverageSettings

    return CoverageSettings.from_config(load_config(), Path.cwd())


def _utc_now() -> str:
    import time as _time

    return _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime())


@coverage_app.command("refresh")
def coverage_refresh(
    api: Optional[str] = typer.Option(
        None, "--api", help="Refresh only this configured API (default: all APIs + sitemap)."
    ),
):
    """Fetch the configured OpenAPI manifest(s) (and optional sitemap) into the
    declared surface, then re-link previously observed endpoints onto newly
    declared templates. Never touches triage flags; nodes are never deleted."""
    with _cli_errors():
        settings = _coverage_settings()
        if not settings.apis and not settings.sitemap:
            console.print(
                "[red]ERROR[/red] No coverage.apis (or coverage.sitemap) configured — "
                "add a `coverage:` block to config.yaml"
            )
            raise typer.Exit(1)
        apis = settings.apis
        if api is not None:
            apis = [a for a in settings.apis if a["name"] == api]
            if not apis:
                names = [a["name"] for a in settings.apis]
                console.print(f"[red]ERROR[/red] Unknown API {api!r}; configured: {names}")
                raise typer.Exit(1)

        from .coverage.manifest import refresh_declared
        from .coverage.store import open_store

        conn = open_store(settings.store_path)
        try:
            summary = refresh_declared(
                conn,
                apis,
                settings.sitemap if api is None else None,
                settings.route_param_patterns,
                updated_at=_utc_now(),
            )
        finally:
            conn.close()
        for entry in summary["apis"]:
            console.print(
                f"[green]OK[/green] {entry['source']}: {entry['declared']} declared "
                f"endpoint(s), {entry['removed']} removed"
            )
        if summary["sitemap"]:
            console.print(
                f"[green]OK[/green] sitemap: {summary['sitemap']['declared']} declared "
                f"route(s), {summary['sitemap']['removed']} removed"
            )
        if summary["relinked"]:
            console.print(
                f"[dim]{summary['relinked']} observed contribution(s) re-linked onto declared endpoints[/dim]"
            )


@coverage_app.command("report")
def coverage_report(
    json_path: Optional[Path] = typer.Option(
        None, "--json", help="Also write the machine-readable report (deterministic JSON) here."
    ),
    badge_path: Optional[Path] = typer.Option(
        None,
        "--badge",
        help="Also write a shields.io endpoint JSON for the e2e-surface percentage here.",
    ),
    badge_surface: str = typer.Option(
        "all",
        "--badge-surface",
        help="Surface the badge measures: all (endpoints + routes), endpoints, or routes.",
    ),
    badge_label: Optional[str] = typer.Option(
        None, "--badge-label", help="Left-hand text on the badge (default: 'e2e surface')."
    ),
):
    """Compute and print the gap report: tested, declared-unreached (the key
    bucket), discovered-uncovered, plus unclassified requests and the covered
    module inventory."""
    with _cli_errors():
        settings = _coverage_settings()
        if not Path(settings.store_path).exists():
            console.print(
                f"[red]ERROR[/red] No coverage store at {settings.store_path} — "
                "record a run first: steppy run --coverage"
            )
            raise typer.Exit(1)

        from .coverage.gaps import SURFACES, compute_gaps
        from .coverage.report import DEFAULT_BADGE_LABEL, badge_to_json, gaps_to_json, print_summary
        from .coverage.store import open_store, read_map
        from .coverage.triage import apply_config_triage, unmatched_patterns

        if badge_surface not in SURFACES:
            _error(f"--badge-surface must be one of {', '.join(SURFACES)} (got {badge_surface!r})")
            raise typer.Exit(1)

        conn = open_store(settings.store_path)
        try:
            raw_map = read_map(conn)
        finally:
            conn.close()

        # The committed ``coverage.non_ui`` declarations are a read-time overlay:
        # applied here, never written to the store, so merge/refresh stay free of
        # triage and a pattern edit takes effect without re-recording anything.
        map_view = apply_config_triage(raw_map, settings.non_ui)
        gaps = compute_gaps(map_view)

        # A pattern that matches nothing silently inflates the percentage — the
        # exact failure this feature exists to prevent — so it is worth a line.
        stale = unmatched_patterns(map_view, settings.non_ui)
        if stale:
            console.print(
                f"[yellow]WARNING[/yellow] {len(stale)} coverage.non_ui pattern(s) matched nothing: "
                f"{escape(', '.join(stale))}"
            )
            console.print("[dim]A renamed or retired endpoint leaves a stale rule that inflates the percentage.[/dim]")

        print_summary(gaps, console)
        if json_path is not None:
            json_path.parent.mkdir(parents=True, exist_ok=True)
            json_path.write_text(gaps_to_json(gaps), encoding="utf-8")
            console.print(f"[dim]JSON report written to {json_path}[/dim]")
        if badge_path is not None:
            badge_path.parent.mkdir(parents=True, exist_ok=True)
            badge_path.write_text(
                badge_to_json(gaps, surface=badge_surface, label=badge_label or DEFAULT_BADGE_LABEL),
                encoding="utf-8",
            )
            console.print(f"[dim]Badge endpoint written to {badge_path}[/dim]")


@coverage_app.command("merge")
def coverage_merge(
    run_id: Optional[str] = typer.Option(
        None, "--run-id", help="Run directory to merge (default: latest under <records_dir>/raw)."
    ),
):
    """Merge raw run records into the map. Rescue path — `steppy run --coverage`
    merges automatically; use this after a crashed/interrupted run. Idempotent:
    re-merging a run replaces each scenario's contribution, it never doubles."""
    with _cli_errors():
        settings = _coverage_settings()
        from .coverage.merge import merge_run

        summary = merge_run(settings, run_id)
        console.print(
            f"[green]OK[/green] Merged run {summary['run_id']}: "
            f"{summary['totals']['scenarios']} scenario(s), "
            f"{summary['totals']['nodes']} map node(s) → {settings.store_path}"
        )
        for warning in summary.get("warnings", []):
            console.print(f"[yellow]WARNING[/yellow] {warning}")


@triage_app.command("set")
def coverage_triage_set(
    path: str = typer.Argument(
        ..., help="Node key: endpoint path template (e.g. '/webhooks/stripe') or route pattern."
    ),
    flag: str = typer.Option(..., "--flag", help="ui_testable | non_ui | unknown"),
    method: str = typer.Option("", "--method", help="HTTP method (endpoints only)."),
    api: str = typer.Option("", "--api", help="API name from coverage.apis (endpoints only)."),
    kind: str = typer.Option("endpoint", "--kind", help="endpoint | route | module"),
    note: str = typer.Option("", "--note", help="Why, e.g. 'Payment provider callback — not UI-reachable'."),
):
    """Persist a triage flag on a node (created if not yet in the map). The
    flag survives every merge and refresh and is never reset."""
    with _cli_errors():
        settings = _coverage_settings()
        from .coverage.store import open_store, set_triage

        conn = open_store(settings.store_path)
        try:
            set_triage(
                conn,
                kind=kind,
                key=path,
                api=api,
                method=method.upper(),
                flag=flag,
                note=note,
                updated_at=_utc_now(),
            )
        finally:
            conn.close()
        console.print(f"[green]OK[/green] {kind} {method.upper()} {path} → {flag}")


@triage_app.command("list")
def coverage_triage_list(
    flag: Optional[str] = typer.Option(None, "--flag", help="Filter by flag value."),
):
    """List persisted triage flags."""
    with _cli_errors():
        settings = _coverage_settings()
        from .coverage.store import list_triage, open_store

        if not Path(settings.store_path).exists():
            console.print("[dim]No coverage store yet — nothing triaged[/dim]")
            return
        conn = open_store(settings.store_path)
        try:
            rows = list_triage(conn, flag)
        finally:
            conn.close()
        if not rows:
            console.print("[dim]No triage flags set[/dim]")
            return
        table = Table("kind", "api", "method", "key", "flag", "note")
        for row in rows:
            table.add_row(row["kind"], row["api"], row["method"], row["key"], row["flag"], row["note"])
        console.print(table)
