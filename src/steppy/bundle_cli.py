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

from ._cli_common import _cli_errors, _error, console

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


def _print_summary(manifest, *, run_id_source: str) -> None:
    from collections import Counter

    run = manifest.run
    console.print(f"\n[bold]Bundle[/bold] {run.run_id} [dim]({run_id_source})[/dim]")
    console.print(f"[dim]steppy {run.steppy_version} · executor {run.executor} · created {run.created_at}[/dim]")
    if run.git.sha:
        branch = f" on {run.git.branch}" if run.git.branch else ""
        pr = f" (PR #{run.git.pr})" if run.git.pr else ""
        console.print(f"[dim]git {run.git.sha[:12]}{branch}{pr}[/dim]")

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
    executor: str = typer.Option("local", "--executor", help="What ran the tests: local, github-actions, agent, ..."),
    git_sha: Optional[str] = typer.Option(None, "--git-sha", help="Override the detected commit sha."),
    git_branch: Optional[str] = typer.Option(None, "--git-branch", help="Override the detected branch."),
    pr: Optional[int] = typer.Option(None, "--pr", help="Pull-request number this run belongs to."),
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

    Run your scenarios with `steppy run --bundle` first: that captures the event
    records and JUnit XML a plain run does not produce. Without them the bundle
    is still valid, but manifest.tests[] will be empty.
    """
    with _cli_errors():
        from .bundle import BundleError, create_bundle
        from .bundle.create import manifest_bytes

        try:
            result = create_bundle(
                project_root=project_root,
                output_dir=output_dir,
                target=output,
                run_id=run_id,
                fmt=fmt,
                executor=executor,
                git_sha=git_sha,
                git_branch=git_branch,
                pr=pr,
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
        if dry_run:
            console.print(f"\n[dim]Dry run — nothing written. Format would be: {result.fmt}[/dim]")
        else:
            ratio = f" ({100 * result.compressed_size / result.payload_size:.0f}%)" if result.payload_size else ""
            console.print(
                f"\n[green]OK[/green] {result.path} [dim]{_human(result.compressed_size)}{ratio}, {result.fmt}[/dim]"
            )


def _config_cloud_url(project_root: Path) -> Optional[str]:
    """``cloud.url`` from the project's config, or None.

    A config that does not load is not an error here — the flag and the
    environment variable both still work, and refusing to upload a finished run
    because ``config.yaml`` no longer parses would be the same poor trade
    ``resolve_roots`` already declines to make.
    """
    try:
        from .config import load_config

        config = load_config(project_root / "config.yaml")
    except Exception:
        return None
    return config.cloud.url if config.cloud else None


@bundle_app.command("upload")
def upload(
    bundle_path: Optional[Path] = typer.Argument(
        None,
        help="An existing .tar.zst/.tar.gz bundle to upload. Omit to upload straight "
        "from output/ without writing an archive (the usual case).",
    ),
    url: Optional[str] = typer.Option(
        None, "--url", help="Control plane base URL. Else $STEPPY_CLOUD_URL, else cloud.url."
    ),
    token: Optional[str] = typer.Option(
        None, "--token", help="Project token. Else $STEPPY_CLOUD_TOKEN. There is no config key for it on purpose."
    ),
    attach: Optional[str] = typer.Option(
        None,
        "--attach",
        help="Id of an existing run on the plane to upload into, instead of creating one. "
        "What `steppy ci run` will use, where the plane created the run first.",
    ),
    run_id: Optional[str] = typer.Option(
        None,
        "--run-id",
        help="Run identity for manifest.run.run_id, as in `bundle create`. Doubles as the "
        "idempotency key, so re-uploading attaches to the same run rather than making a second.",
    ),
    project_root: Path = typer.Option(Path("."), "--project-root", help="Project root."),
    output_dir: Optional[Path] = typer.Option(
        None, "--output-dir", help="The tree to upload. Default: <project-root>/output"
    ),
    executor: str = typer.Option("local", "--executor", help="What ran the tests, recorded on a created run."),
    exit_code: Optional[int] = typer.Option(
        None, "--exit-code", help="The run's verdict. Default: derived from the manifest's test outcomes."
    ),
    git_sha: Optional[str] = typer.Option(None, "--git-sha", help="Override the detected commit sha."),
    git_branch: Optional[str] = typer.Option(None, "--git-branch", help="Override the detected branch."),
    pr: Optional[int] = typer.Option(None, "--pr", help="Pull-request number this run belongs to."),
    guides: bool = typer.Option(True, "--guides/--no-guides", help="Include output/guides/."),
    traces: bool = typer.Option(True, "--traces/--no-traces", help="Include output/traces/."),
    videos: bool = typer.Option(True, "--videos/--no-videos", help="Include output/videos/."),
    coverage: bool = typer.Option(True, "--coverage/--no-coverage", help="Include output/coverage/."),
    max_size: str = typer.Option("0", "--max-size", help="Uncompressed payload budget, e.g. 2GiB. 0 disables it."),
    concurrency: int = typer.Option(4, "--concurrency", min=1, help="Parallel uploads (ignored for an archive)."),
    timeout: float = typer.Option(60.0, "--timeout", help="Per-request timeout, seconds."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be uploaded; contact nothing."),
    as_json: bool = typer.Option(False, "--json", help="Print the outcome as JSON."),
) -> None:
    """Push a run's results to a Steppy control plane.

    Uploads are content-addressed: the server is offered the manifest and hands
    back URLs only for the files it does not already hold, so a re-run that
    changed two screenshots uploads two screenshots. Interrupted? Run it again —
    it attaches to the same run and sends only what is missing.

    Run your scenarios with `steppy run --bundle` first. Two things depend on
    it: the per-test event records, without which the plane gets an empty
    tests[] and no failure detail; and the run identity in
    output/.steppy-run.json, which is the idempotency key that makes a retried
    upload attach rather than create a second run. Without a recorded identity
    a fresh one is minted per invocation — pass --run-id to pin it, or --attach
    to upload into a run that already exists.
    """
    with _cli_errors():
        from .bundle import BundleError, plan_bundle
        from .cloud import ArchiveSource, CloudError, TreeSource, cloud_client, derive_exit_code, upload_bundle

        try:
            if bundle_path is not None:
                source = ArchiveSource(bundle_path)
            else:
                source = TreeSource(
                    plan_bundle(
                        project_root=project_root,
                        output_dir=output_dir,
                        run_id=run_id,
                        git_sha=git_sha,
                        git_branch=git_branch,
                        pr=pr,
                        guides=guides,
                        traces=traces,
                        videos=videos,
                        coverage=coverage,
                        max_size=_parse_size(max_size),
                        executor=executor,
                    )
                )
        except BundleError as e:
            _error(str(e))
            raise typer.Exit(1)

        manifest = source.manifest
        if dry_run:
            _print_summary(manifest, run_id_source="planned")
            console.print(
                f"\n[dim]Dry run — nothing uploaded. {len(manifest.files)} file(s), "
                f"{_human(sum(f.size for f in manifest.files))} before dedup; "
                f"verdict would be exit {derive_exit_code(manifest) if exit_code is None else exit_code}.[/dim]"
            )
            raise typer.Exit(0)

        try:
            client = cloud_client(url=url, token=token, config_url=_config_cloud_url(project_root), timeout=timeout)
        except CloudError as e:
            _error(str(e))
            raise typer.Exit(1)

        with client:
            try:
                outcome = _run_upload(
                    upload_bundle,
                    client,
                    source,
                    run_id=attach,
                    executor=executor,
                    exit_code=exit_code,
                    concurrency=concurrency,
                )
            except CloudError as e:
                _error(str(e))
                raise typer.Exit(1)

        if as_json:
            typer.echo(_json.dumps(outcome.as_dict(), indent=2))
            raise typer.Exit(0)

        _print_upload_summary(outcome, client.endpoint.url)


def _run_upload(upload_bundle, client, source, *, run_id, executor, exit_code, concurrency):
    """Drive the upload behind a byte-oriented progress bar.

    Progress is measured in bytes rather than files: a bundle is a handful of
    megabyte screenshots among hundreds of tiny sidecars, so a file counter
    would sit at 90% for most of the wall clock.
    """
    from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn

    total = sum(f.size for f in source.manifest.files)
    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.fields[sent]}"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    ) as progress:
        bar = progress.add_task("uploading", total=total or 1, sent="")
        sent = 0

        def advance(blob) -> None:
            nonlocal sent
            sent += blob.size
            progress.update(bar, completed=sent, sent=_human(sent))

        return upload_bundle(
            client,
            source,
            run_id=run_id,
            executor=executor,
            exit_code=exit_code,
            concurrency=concurrency,
            on_progress=advance,
        )


def _print_upload_summary(outcome, base_url: str) -> None:
    verb = "Created run" if outcome.created_run else "Uploaded to run"
    console.print(f"\n[bold]{verb}[/bold] #{outcome.run_number} [dim]{outcome.run_id}[/dim]")

    # Blobs, not files: uploads are content-addressed, so a screenshot that is
    # byte-identical across two locales is one upload serving two manifest
    # entries. Reporting "3 of 4 files" for that would be a lie in both numbers.
    if outcome.uploaded:
        console.print(
            f"[bold]Uploaded[/bold] {outcome.uploaded} of {outcome.total_blobs} blob(s), "
            f"{_human(outcome.uploaded_bytes)} [dim]({outcome.total_files} file(s))[/dim]"
        )
    else:
        console.print("[bold]Uploaded[/bold] nothing — the server already held every blob in this run")
    if outcome.skipped_bytes:
        console.print(f"[dim]{_human(outcome.skipped_bytes)} not sent — already held (content-addressed dedup)[/dim]")

    for warning in outcome.warnings:
        console.print(f"[yellow]WARNING[/yellow] {warning}")

    colour = {"completed": "green", "failed": "red", "error": "red", "expired": "red", "canceled": "yellow"}.get(
        outcome.run_status, "yellow"
    )
    console.print(f"[bold]Status[/bold] [{colour}]{outcome.run_status}[/{colour}]")
    console.print(f"[dim]{base_url}/runs/{outcome.run_id}[/dim]")


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
