"""``steppy bundle`` — package a run's results into a portable archive.

Leaf CLI module, registered by ``cli.py`` like ``coverage_cli`` /
``video_cli``. Only ``typer`` + ``_cli_common`` + stdlib at module scope; every
``steppy.bundle`` import happens inside a command body so ``import steppy.cli``
never pulls in the walker or the optional ``zstandard``. Guard:
``tests/test_no_bundle_import_on_default_path.py``.
"""

from __future__ import annotations

import json as _json
from pathlib import Path
from typing import Optional

import typer
from rich.markup import escape

from ._cli_common import (
    BRANCH_HELP,
    COMMIT_HELP,
    ENVIRONMENT_HELP,
    PR_HELP,
    _cli_errors,
    _error,
    console,
)

bundle_app = typer.Typer(
    help="Package a run's output/ tree into a portable results bundle.",
    no_args_is_help=True,
)


def register_bundle(app: typer.Typer) -> None:
    """Attach the ``bundle`` command group to the root Typer app."""
    app.add_typer(bundle_app, name="bundle")


def _parse_size(value: str) -> int:
    """``2GiB`` / ``500MB`` / ``0`` → bytes. ``0`` disables the budget."""
    text = value.strip().lower().replace(" ", "")
    if not text:
        return 0
    units = (("gib", 1024**3), ("mib", 1024**2), ("kib", 1024), ("gb", 10**9), ("mb", 10**6), ("kb", 10**3), ("b", 1))
    for suffix, factor in units:
        if text.endswith(suffix):
            number = text[: -len(suffix)]
            try:
                return int(float(number) * factor)
            except ValueError:
                break
    try:
        return int(text)
    except ValueError:
        raise typer.BadParameter(f"could not parse a size from {value!r} (try 2GiB, 500MB or 0)")


def _human(size: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} GiB"


_RECORDED = " For a recorded run, the default is what `steppy run` recorded."
_ARCHIVE = " With an archive, replaces what the archive says."


def _metadata_alias(value: Optional[str], alias: Optional[str], name: str, alias_name: str) -> Optional[str]:
    """``--branch`` / ``--commit`` and their earlier names, which stay accepted."""
    if value and alias and value != alias:
        _error(f"{name} {value!r} and {alias_name} {alias!r} disagree; pass {name} alone")
        raise typer.Exit(1)
    return value or alias


def _shown(value) -> str:
    return "(none)" if value is None else str(value)


def print_overrides(changes, *, what: str) -> None:
    """What the metadata flags changed, one field per line."""
    if not changes:
        return
    console.print(f"[bold]Overrode[/bold] {escape(what)}:")
    width = max(len(c.name) for c in changes)
    for change in changes:
        console.print(f"  {change.name:<{width}}  {escape(_shown(change.old))} → {escape(_shown(change.new))}")


def _print_summary(manifest, *, run_id_source: str) -> None:
    from collections import Counter

    run = manifest.run
    console.print(f"\n[bold]Bundle[/bold] {run.run_id} [dim]({run_id_source})[/dim]")
    console.print(f"[dim]steppy {run.steppy_version} · executor {run.executor} · created {run.created_at}[/dim]")
    git = run.git
    if git.sha or git.branch or git.tested_sha:
        line = f"git {git.sha[:12]}" if git.sha else "git"
        if git.branch:
            line += f" on {git.branch}"
        if git.pr:
            line += f" (PR #{git.pr}" + (f" into {git.base_branch})" if getattr(git, "base_branch", None) else ")")
        if getattr(git, "tested_sha", None):
            line += f", tested as {git.tested_sha[:12]}"
        if getattr(git, "dirty", None):
            line += ", uncommitted changes"
        console.print(f"[dim]{escape(line)}[/dim]")
    ci = getattr(run, "ci", None)
    environment = getattr(run, "environment", None)
    if ci is not None or environment:
        where = [f"ci {ci.provider}" + (f" {ci.url}" if ci.url else "")] if ci is not None else []
        if environment:
            where.append(f"environment {environment}")
        console.print(f"[dim]{escape(' · '.join(where))}[/dim]")

    spec = run.spec
    dimensions = []
    if spec.locales:
        dimensions.append(f"locales {', '.join(spec.locales)}")
    if spec.layouts:
        dimensions.append(f"layouts {', '.join(spec.layouts)}")
    if spec.guide:
        dimensions.append("guide mode")
    if spec.coverage:
        dimensions.append("coverage")
    if dimensions:
        console.print(f"[dim]{' · '.join(dimensions)}[/dim]")

    if manifest.tests:
        outcomes = Counter(t.outcome for t in manifest.tests)
        parts = [f"{count} {name}" for name, count in sorted(outcomes.items())]
        console.print(f"\n[bold]Tests[/bold] ({len(manifest.tests)}): {', '.join(parts)}")
    else:
        console.print("\n[bold]Tests[/bold]: none recorded")

    kinds = Counter(f.kind for f in manifest.files)
    total = sum(f.size for f in manifest.files)
    console.print(f"[bold]Files[/bold] ({len(manifest.files)}, {_human(total)}):")
    for kind, count in sorted(kinds.items()):
        kind_size = sum(f.size for f in manifest.files if f.kind == kind)
        console.print(f"  [dim]{kind:<22}[/dim] {count:>5}  {_human(kind_size)}")

    if manifest.coverage and manifest.coverage.records:
        console.print(f"[bold]Coverage[/bold]: {manifest.coverage.records} raw record(s)")
    if manifest.truncated:
        console.print("[yellow]TRUNCATED[/yellow] — some artifacts were dropped to fit the size budget")
    for warning in manifest.warnings:
        console.print(f"[yellow]WARNING[/yellow] {warning}")


@bundle_app.command("create")
def create(
    output: Optional[Path] = typer.Option(
        None,
        "--output",
        "-o",
        help="Where to write the bundle. A directory gets a generated filename; "
        "a path with an extension is used verbatim. "
        "Default: output/bundles/steppy-bundle-<run-id>.tar.zst",
    ),
    run_id: Optional[str] = typer.Option(
        None,
        "--run-id",
        help="Run identity for manifest.run.run_id. Defaults to the run recorded "
        "in output/.steppy-run.json, else the newest recorded run on disk.",
    ),
    fmt: str = typer.Option(
        "auto",
        "--format",
        help="Compression: auto (zstd when the steppy-dev[bundle] extra is installed, else gzip), zst, or gz.",
    ),
    project_root: Path = typer.Option(Path("."), "--project-root", help="Project root."),
    output_dir: Optional[Path] = typer.Option(
        None, "--output-dir", help="The tree to archive. Default: <project-root>/output"
    ),
    executor: Optional[str] = typer.Option(
        None,
        "--executor",
        help="What ran the tests: ci or local. Default: what `steppy run` recorded, else ci when "
        "GITHUB_ACTIONS, GITLAB_CI or CI is set, else local.",
    ),
    branch: Optional[str] = typer.Option(None, "--branch", help=BRANCH_HELP + _RECORDED),
    commit: Optional[str] = typer.Option(None, "--commit", help=COMMIT_HELP + _RECORDED),
    pr: Optional[int] = typer.Option(None, "--pr", min=1, help=PR_HELP + _RECORDED),
    environment: Optional[str] = typer.Option(None, "--environment", help=ENVIRONMENT_HELP + _RECORDED),
    git_sha: Optional[str] = typer.Option(None, "--git-sha", hidden=True, help="Earlier name of --commit."),
    git_branch: Optional[str] = typer.Option(None, "--git-branch", hidden=True, help="Earlier name of --branch."),
    guides: bool = typer.Option(True, "--guides/--no-guides", help="Include output/guides/."),
    traces: bool = typer.Option(True, "--traces/--no-traces", help="Include output/traces/."),
    videos: bool = typer.Option(True, "--videos/--no-videos", help="Include output/videos/."),
    coverage: bool = typer.Option(True, "--coverage/--no-coverage", help="Include output/coverage/."),
    max_size: str = typer.Option(
        "0",
        "--max-size",
        help="Uncompressed payload budget, e.g. 2GiB. Over budget, the cheapest-to-lose "
        "artifacts are dropped (videos, then traces, then guides) and the manifest is "
        "marked truncated. 0 disables the budget.",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be archived; write nothing."),
    as_json: bool = typer.Option(False, "--json", help="Print the manifest as JSON instead of a summary."),
) -> None:
    """Package a run's output/ tree into a results bundle.

    The archive holds output/ exactly as steppy wrote it — no path rewriting —
    so unpacking it gives a tree `steppy guide` and `steppy build` consume
    unchanged. Alongside it sit a manifest.json describing every file, a merged
    junit.xml, and the run's per-test event records.

    Run your scenarios with `steppy run` first: it records the event records
    and JUnit XML the bundle reads, and where the run came from (commit,
    branch, pull request, CI job, environment). Without them the bundle is
    still valid, but manifest.tests[] will be empty.
    """
    with _cli_errors():
        from .bundle import BundleError, create_bundle
        from .bundle.create import manifest_bytes

        branch = _metadata_alias(branch, git_branch, "--branch", "--git-branch")
        commit = _metadata_alias(commit, git_sha, "--commit", "--git-sha")
        try:
            result = create_bundle(
                project_root=project_root,
                output_dir=output_dir,
                target=output,
                run_id=run_id,
                fmt=fmt,
                executor=executor,
                branch=branch,
                commit=commit,
                pr=pr,
                environment=environment,
                guides=guides,
                traces=traces,
                videos=videos,
                coverage=coverage,
                max_size=_parse_size(max_size),
                dry_run=dry_run,
            )
        except BundleError as e:
            _error(str(e))
            raise typer.Exit(1)

        if as_json:
            typer.echo(manifest_bytes(result.manifest).decode("utf-8"), nl=False)
            raise typer.Exit(0)

        _print_summary(result.manifest, run_id_source=result.run_id_source)
        print_overrides(result.overrides, what="the run's metadata")
        if dry_run:
            console.print(f"\n[dim]Dry run — nothing written. Format would be: {result.fmt}[/dim]")
        else:
            ratio = f" ({100 * result.compressed_size / result.payload_size:.0f}%)" if result.payload_size else ""
            console.print(
                f"\n[green]OK[/green] {result.path} [dim]{_human(result.compressed_size)}{ratio}, {result.fmt}[/dim]"
            )


@bundle_app.command("upload")
def upload(
    bundle_path: Optional[Path] = typer.Argument(
        None,
        help="An existing .tar.zst/.tar.gz bundle to upload. Omit to upload straight "
        "from output/ without writing an archive (the usual case).",
    ),
    url: Optional[str] = typer.Option(
        None,
        "--url",
        help="The Steppy Cloud instance's address (the client appends /api/v1). "
        "Else $STEPPY_URL, else $STEPPY_CLOUD_URL (an API base: only /v1 is appended), else cloud.url.",
    ),
    api_key: Optional[str] = typer.Option(
        None,
        "--api-key",
        "--token",
        help="The project's API key. Else $STEPPY_API_KEY, else $STEPPY_CLOUD_TOKEN. "
        "There is no config key for it on purpose.",
    ),
    run_id: Optional[str] = typer.Option(
        None,
        "--run-id",
        help="Run identity for manifest.run.run_id, as in `bundle create`. Doubles as the "
        "idempotency key, so uploading again returns the same run rather than making a second. "
        "With an archive, replaces the archive's own.",
    ),
    project_root: Path = typer.Option(Path("."), "--project-root", help="Project root."),
    output_dir: Optional[Path] = typer.Option(
        None, "--output-dir", help="The tree to upload. Default: <project-root>/output"
    ),
    executor: Optional[str] = typer.Option(
        None,
        "--executor",
        help="What ran the tests: ci or local. Default: what `steppy run` recorded, else ci when "
        "GITHUB_ACTIONS, GITLAB_CI or CI is set, else local.",
    ),
    branch: Optional[str] = typer.Option(None, "--branch", help=BRANCH_HELP + _RECORDED + _ARCHIVE),
    commit: Optional[str] = typer.Option(None, "--commit", help=COMMIT_HELP + _RECORDED + _ARCHIVE),
    pr: Optional[int] = typer.Option(None, "--pr", min=1, help=PR_HELP + _RECORDED + _ARCHIVE),
    environment: Optional[str] = typer.Option(None, "--environment", help=ENVIRONMENT_HELP + _RECORDED + _ARCHIVE),
    git_sha: Optional[str] = typer.Option(None, "--git-sha", hidden=True, help="Earlier name of --commit."),
    git_branch: Optional[str] = typer.Option(None, "--git-branch", hidden=True, help="Earlier name of --branch."),
    guides: bool = typer.Option(True, "--guides/--no-guides", help="Include output/guides/."),
    traces: bool = typer.Option(True, "--traces/--no-traces", help="Include output/traces/."),
    videos: bool = typer.Option(True, "--videos/--no-videos", help="Include output/videos/."),
    coverage: bool = typer.Option(True, "--coverage/--no-coverage", help="Include output/coverage/."),
    max_size: str = typer.Option("0", "--max-size", help="Uncompressed payload budget, e.g. 2GiB. 0 disables it."),
    concurrency: int = typer.Option(4, "--concurrency", min=1, help="Parallel uploads (ignored for an archive)."),
    timeout: float = typer.Option(60.0, "--timeout", help="Per-request timeout, seconds (10 s to connect)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be uploaded; contact nothing."),
    as_json: bool = typer.Option(False, "--json", help="Print the outcome as JSON."),
) -> None:
    """Upload a recorded run to Steppy Cloud.

    Builds the run's report.json, then creates the run with the manifest of its
    files: the server answers with an upload URL for each file it wants and
    does not hold yet, so a re-run that changed two screenshots uploads two
    screenshots. Interrupted? Run it again — it gets the same run back and sends
    only what is missing. Exits non-zero when the upload fails.

    Run your scenarios with `steppy run` first: it records the per-test event
    records the report is built from, where the run came from (commit, branch,
    pull request, CI job, environment), and the run identity in
    output/.steppy-run.json that makes uploading again return the same run.
    `steppy run --upload` does both in one command.

    With an archive, --run-id, --branch, --commit, --pr, --environment and
    --executor are applied to the manifest sent (the archive is not rewritten),
    and the command prints what they changed.
    """
    with _cli_errors():
        from .bundle import BundleError, override_manifest, plan_bundle
        from .cloud import ArchiveSource, CloudError, TreeSource, attach_report, connect, resolve_endpoint
        from .report.source import ReportSourceError
        from .upload_cli import config_cloud_url, print_outcome, run_with_progress

        branch = _metadata_alias(branch, git_branch, "--branch", "--git-branch")
        commit = _metadata_alias(commit, git_sha, "--commit", "--git-sha")
        budget = _parse_size(max_size)
        try:
            if bundle_path is not None:
                source = ArchiveSource(bundle_path)
                # The flags are the user's last word on an archive too; only the
                # manifest sent changes, and report.json is built from it below.
                source.manifest, changes = override_manifest(
                    source.manifest,
                    run_id=run_id,
                    branch=branch,
                    commit=commit,
                    pr=pr,
                    environment=environment,
                    executor=executor,
                )
                what = f"the run metadata of {bundle_path.name} (the archive itself is unchanged)"
                ignored = [
                    flag
                    for flag, given in (
                        ("--no-guides", not guides),
                        ("--no-traces", not traces),
                        ("--no-videos", not videos),
                        ("--no-coverage", not coverage),
                        ("--max-size", budget != 0),
                    )
                    if given
                ]
                if ignored and not as_json:
                    one = len(ignored) == 1
                    console.print(
                        f"[yellow]WARNING[/yellow] {', '.join(ignored)} {'trims' if one else 'trim'} what goes "
                        f"into a bundle, and {escape(bundle_path.name)} is already made, so "
                        f"{'it changes' if one else 'they change'} nothing. Upload from output/ (no argument) to trim it"
                    )
            else:
                plan = plan_bundle(
                    project_root=project_root,
                    output_dir=output_dir,
                    run_id=run_id,
                    branch=branch,
                    commit=commit,
                    pr=pr,
                    environment=environment,
                    guides=guides,
                    traces=traces,
                    videos=videos,
                    coverage=coverage,
                    max_size=budget,
                    executor=executor,
                )
                source = TreeSource(plan, output_dir=output_dir)
                changes = plan.overrides
                what = "the run's metadata"
            if not as_json:
                print_overrides(changes, what=what)
            source, report = attach_report(source, project_root=project_root)
        except (BundleError, ReportSourceError) as e:
            _error(str(e))
            raise typer.Exit(1)

        manifest = source.manifest
        if dry_run:
            _print_summary(manifest, run_id_source="planned")
            console.print(
                f"\n[dim]Dry run — nothing uploaded. {len(manifest.files)} file(s), "
                f"{_human(sum(f.size for f in manifest.files))} before dedup, report.json included "
                f"(verdict {report.totals.verdict}).[/dim]"
            )
            raise typer.Exit(0)

        try:
            endpoint = resolve_endpoint(url, api_key, config_url=lambda: config_cloud_url(project_root))
            with connect(endpoint, timeout=timeout) as client:
                outcome = run_with_progress(client, source, concurrency=concurrency)
        except CloudError as e:
            _error(f"upload failed: {e}")
            raise typer.Exit(1)

        if as_json:
            typer.echo(_json.dumps(outcome.as_dict(), indent=2))
            raise typer.Exit(0)

        print_outcome(outcome)


@bundle_app.command("inspect")
def inspect(
    bundle_path: Path = typer.Argument(..., help="Path to a .tar.zst or .tar.gz bundle."),
    as_json: bool = typer.Option(False, "--json", help="Print the raw manifest JSON."),
) -> None:
    """Print a bundle's manifest without extracting it."""
    with _cli_errors():
        from .bundle import BundleError, inspect_bundle
        from .bundle.create import manifest_bytes

        try:
            manifest = inspect_bundle(bundle_path)
        except BundleError as e:
            _error(str(e))
            raise typer.Exit(1)

        if as_json:
            typer.echo(manifest_bytes(manifest).decode("utf-8"), nl=False)
            raise typer.Exit(0)
        _print_summary(manifest, run_id_source="archived")


@bundle_app.command("verify")
def verify(
    bundle_path: Path = typer.Argument(..., help="Path to a .tar.zst or .tar.gz bundle."),
    as_json: bool = typer.Option(False, "--json", help="Print the problem list as JSON."),
) -> None:
    """Re-hash every archived file against the manifest.

    Exits non-zero if anything is missing, extra, or corrupted.
    """
    with _cli_errors():
        from .bundle import BundleError, verify_bundle

        try:
            manifest, problems = verify_bundle(bundle_path)
        except BundleError as e:
            _error(str(e))
            raise typer.Exit(1)

        if as_json:
            typer.echo(_json.dumps({"ok": not problems, "problems": problems}, indent=2))
            raise typer.Exit(1 if problems else 0)

        if problems:
            _error(f"{len(problems)} problem(s) in {bundle_path.name}:")
            for problem in problems:
                console.print(f"  [red]·[/red] {problem}")
            raise typer.Exit(1)
        console.print(f"[green]OK[/green] {bundle_path.name}: {len(manifest.files)} file(s) match the manifest")
