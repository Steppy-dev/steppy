"""What ``steppy run --upload`` and ``steppy bundle upload`` say and decide.

The CLI half of uploading: the start check before a run's tests, the upload
after them, and the progress bar and summary both commands print. The protocol
itself is :mod:`steppy.cloud`; this module only turns its outcomes into lines on
the console and exit codes.

Leaf module, and light: ``typer``, ``rich`` and ``_cli_common`` at module scope.
Every ``steppy.cloud`` / ``steppy.bundle`` import happens inside a function, so
nothing here reaches ``httpx`` until a command asks to upload. Guard:
``tests/test_no_cloud_import_on_default_path.py``.

**An upload never changes a test run's result** (I6). After the tests, every
failure — the server down, a refused key, a bug here — becomes a warning, and
the tests' exit code stands. Only ``--require-upload`` makes it fail the
command. Before the tests, two things stop the run: no key or address
(``steppy run --upload`` was asked for something it cannot do), and a key the
server refuses. A server that cannot be reached only warns.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.markup import escape

from ._cli_common import _debug_enabled, _error, console


def config_cloud_url(project_root: Path) -> Optional[str]:
    """``cloud.url`` from the project's config, or None.

    A config that does not load is not an error here — the flag and the
    environment variables still work, and refusing to upload a finished run
    because ``config.yaml`` no longer parses would be a poor trade.
    """
    try:
        from .config import load_config

        config = load_config(project_root / "config.yaml")
    except Exception:
        return None
    return config.cloud.url if config.cloud else None


def _warn(message: str) -> None:
    console.print(f"[yellow]WARNING[/yellow] {escape(message)}")


def _human(size: float) -> str:
    from .bundle_cli import _human as human

    return human(int(size))


# --- steppy run --upload -------------------------------------------------------


def preflight(*, project_root: Path):
    """The start check: resolve the key and address, then ask whose key it is.

    Returns the :class:`~steppy.cloud.Endpoint` to upload to after the tests.
    Exits 1, before any test runs, when the key or the address is missing or the
    key is refused. Any other failure (no answer, a 5xx, an unexpected answer)
    is a warning: the tests run, and the upload is tried after them.
    """
    from . import cloud

    try:
        # `steppy run` takes no address or key flag: a key on a command line
        # ends up in shell history and CI logs.
        endpoint = cloud.resolve_endpoint(
            config_url=lambda: config_cloud_url(project_root), url_flag=None, key_flag=None
        )
    except cloud.CloudConfigError as e:
        _error(f"--upload: {e}. No test was run.")
        raise typer.Exit(1)

    try:
        with cloud.connect(endpoint) as client:
            key = cloud.check_key(client)
    except cloud.CloudAuthError as e:
        _error(
            f"--upload: {endpoint.display} refused the API key from {endpoint.key_source} "
            f"(HTTP {e.status}: {e.detail}). No test was run. Check the key, or create a new one "
            "on the project's page"
        )
        raise typer.Exit(1)
    except cloud.CloudError as e:
        _warn(f"--upload: could not check the API key: {e}. The tests run anyway; the upload is tried after them.")
        return endpoint

    project = key.project
    where = project.url or endpoint.display
    console.print(f"[bold]Upload[/bold] to {escape(project.name or project.id)} [dim]({escape(where)})[/dim]")
    return endpoint


def upload_finished_run(endpoint, *, project_root: Path, exit_code: int, require: bool) -> int:
    """Upload the run that just finished, even a failed one. Returns the exit code.

    The tests' own exit code, unless the upload failed and *require* is set: then
    the command fails, with the tests' code when they failed, else 1.
    """
    console.print(f"\n[bold]Uploading[/bold] the run to {escape(endpoint.display)}")
    try:
        from . import cloud
        from .bundle import plan_bundle

        source, _model = cloud.attach_report(
            cloud.TreeSource(plan_bundle(project_root=project_root)), project_root=project_root
        )
        with cloud.connect(endpoint) as client:
            outcome = run_with_progress(client, source)
    except KeyboardInterrupt:
        raise
    except Exception as e:
        # Deliberately broad: whatever went wrong, the tests already ran and
        # their result is what this command reports (I6).
        message = str(e) or type(e).__name__
        if _debug_enabled():
            import traceback

            traceback.print_exc()
        if require:
            _error(f"upload failed: {message}. --require-upload makes that fail the command.")
            return exit_code or 1
        _warn(
            f"upload failed: {message}. The tests' result is unchanged; `steppy bundle upload` can send this run again."
        )
        return exit_code

    print_outcome(outcome)
    return exit_code


# --- shared by both commands -------------------------------------------------------


def run_with_progress(client, source, *, concurrency: int = 4):
    """Drive :func:`~steppy.cloud.upload_run` behind a byte-oriented progress bar.

    Progress is measured in bytes rather than files: a run is a handful of
    megabyte screenshots among hundreds of tiny sidecars, so a file counter
    would sit at 90% for most of the wall clock.
    """
    from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn

    from .cloud import upload_run

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

        return upload_run(client, source, concurrency=concurrency, on_progress=advance)


def print_outcome(outcome) -> None:
    """The summary both commands end with; the run's URL is the last line."""
    number = f" #{outcome.number}" if outcome.number is not None else ""
    console.print(f"[bold]Run{number}[/bold] [dim]{escape(outcome.run_id)}[/dim]")

    # In digests, not files: uploads are content-addressed, so a screenshot that
    # is byte-identical across two locales is one upload serving two manifest
    # entries. And the server asks only for what it wants and lacks, so the rest
    # is neither "held" nor "skipped" from here: it is simply not needed.
    if outcome.uploaded:
        console.print(
            f"[bold]Uploaded[/bold] {outcome.uploaded} file(s), {_human(outcome.uploaded_bytes)} "
            f"[dim](the server needed {outcome.requested} of {outcome.total_blobs} distinct file(s), "
            f"{outcome.total_files} in the run)[/dim]"
        )
    else:
        console.print(
            f"[bold]Uploaded[/bold] nothing: the server needed none of the run's {outcome.total_blobs} distinct file(s)"
        )
    for warning in outcome.warnings:
        _warn(warning)
    if outcome.status:
        colour = {"passed": "green", "failed": "red", "error": "red", "incomplete": "yellow"}.get(
            outcome.status, "yellow"
        )
        console.print(f"[bold]Status[/bold] [{colour}]{escape(outcome.status)}[/{colour}]")
    if outcome.url:
        console.print(escape(outcome.url))
