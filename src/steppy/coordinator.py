"""``steppy run --workers N >= 2`` host-side orchestrator.

What lives here:

- :func:`run_parallel` — entry point dispatched from :mod:`steppy.cli`. Same
  argument set as the existing single-worker run path, plus ``workers`` and
  ``no_build``. Returns the process exit code.
- :func:`cleanup_stacks` — entry point for ``steppy stacks cleanup``. Lists
  any orphaned ``steppy-run-*`` Compose projects and tears them down.

The single-worker path in :func:`steppy.cli.run` does NOT import this
module. The dispatch in cli.run is a lazy ``from steppy.coordinator import
run_parallel`` so default invocations never load anything from here. See
``tests/test_no_parallel_import_on_default_path.py`` for the guard test.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Optional

from rich.console import Console

from . import exclusive, selection
from .captures import DEFAULT_SCREENSHOTS_DIR, resolve_output_dir
from .config import ConfigError, load_config
from .models import SteppyConfig
from .runmeta import EVENTS_SUBDIR, JUNIT_SUBDIR, new_run_id, write_run_sidecar
from .parser import ParseError, parse_scenarios_from_directory
from ._parallel.compose import (
    ComposeError,
    check_compose_version,
    introspect_user_compose,
    render_override,
)
from ._parallel.event_bus import EventBus, RawLogChannel
from ._parallel.image import ensure_worker_image
from ._parallel.lifecycle import CoordinatorLifecycle
from ._parallel.progress import WorkerLogTailer
from ._parallel.queue import Queue
from ._parallel.renderer import make_renderer, render_summary


_PROJECT_NAME_PREFIX = "steppy-run-"
_DEFAULT_AUTO_CAP = 8

# How ``ensure_worker_image`` resolved the image, rendered for the run header.
_IMAGE_MODE_LABELS = {
    "cached": "cached",
    "config": "built from parallel.dockerfile",
    "repo": "built from source checkout",
    "installed": "built from installed package",
}


def _finalize_run(
    *,
    queue,
    bus,
    console,
    tmp_dir,
    project_root,
    tests_dir,
    retry_passes=None,
    run_id=None,
    workers=None,
    verbose=False,
    screenshots_dir=None,
    never_started=None,
):
    """Collect results, persist artifacts, refresh lastfailed, print the summary.

    The shutdown contract, run identically whether the workers drained normally
    or the user hit Ctrl-C — previously spelled out twice, so any new artifact
    writer or summary argument had to be added to both or Ctrl-C'd runs would
    silently lose it.

    Ordering is load-bearing: artifacts are persisted BEFORE the summary so the
    summary's path references are valid, and well before tmp_dir cleanup wipes
    the per-scenario logs.

    ``retry_passes`` is the list of :class:`~steppy._parallel.retry.PassResult`
    for attempts 2..N (the main pass is reconstructed here from ``queue``).
    Empty/None ⇒ single-pass behaviour, byte-identical to pre-retry.

    The caller stops the renderer first (the Live overlay would otherwise
    re-paint over the summary). Returns ``(results, orphans, verdict)`` —
    ``verdict`` is None when no retry pass ran.
    """
    results = queue.collect_results()
    orphans = queue.orphaned_running_tasks()
    never_started = set(never_started or ())

    from ._parallel.failures import persist_failure_artifacts
    from ._parallel.lastfailed import write_lastfailed_cache
    from ._parallel.retry import PassResult, classify, log_roots_by_slug

    # Fold the retry attempts in. The main pass is attempt 1.
    main_pass = PassResult(
        attempt=1,
        coord_root=tmp_dir,
        results=results,
        orphans=orphans,
        bus=bus,
        never_started=never_started,
    )
    all_passes = [main_pass, *(retry_passes or [])]
    verdict = classify(all_passes) if retry_passes else None

    if retry_passes:
        # The authoritative view is the LAST attempt's outcome per scenario, so
        # a flaky scenario shows up green and its retry log is the one persisted.
        results = _merge_results(all_passes)
        orphans = _final_orphans(all_passes)

    persist_failure_artifacts(
        project_root=project_root,
        tmp_dir=tmp_dir,
        bus=bus,
        scenario_results=results,
        orphans=orphans,
        log_roots=log_roots_by_slug(all_passes) if retry_passes else None,
        screenshots_dir=screenshots_dir,
    )

    # Parallel workers never populate .pytest_cache themselves; without this,
    # `steppy rerun` would pick up a stale single-worker run's failure set.
    write_lastfailed_cache(
        project_root=project_root,
        tests_dir=tests_dir,
        scenario_results=results,
        orphans=orphans,
        bus=bus,
        # Flaky scenarios ended green — re-running them would waste a rerun on
        # known-good tests and bury the real failures.
        failed_slugs=set(verdict.failed) if verdict else None,
    )

    render_summary(
        console=console,
        bus=bus,
        scenario_results=results,
        orphans=orphans,
        tmp_dir=tmp_dir,
        project_root=project_root,
        verdict=verdict,
        retry_buses=[p.bus for p in (retry_passes or []) if p.bus is not None],
        verbose=verbose,
    )

    _record_history(
        project_root=project_root,
        run_id=run_id,
        workers=workers,
        passes=all_passes,
        verdict=verdict,
        bus=bus,
        console=console,
    )
    return results, orphans, verdict


def _merge_results(all_passes) -> list:
    """One :class:`ScenarioResult` per slug, from its LAST attempt.

    Later attempts overwrite earlier ones, so a scenario that failed under load
    and passed alone reports as a pass (with the retry's worker + duration)."""
    merged: dict[str, Any] = {}
    for p in all_passes:
        for r in p.results:
            merged[r.slug] = r
    return list(merged.values())


def _final_orphans(all_passes) -> dict[str, list[str]]:
    """Orphans that survived every attempt.

    An orphan retried successfully is no longer an orphan; without this filter
    a recovered worker-death would still be reported as one."""
    resolved = {r.slug for p in all_passes for r in p.results}
    out: dict[str, list[str]] = {}
    for p in all_passes:
        for worker_id, slugs in p.orphans.items():
            remaining = [s for s in slugs if s not in resolved]
            if remaining:
                out.setdefault(worker_id, []).extend(remaining)
    return {wid: sorted(set(slugs)) for wid, slugs in out.items()}


def _record_history(*, project_root, run_id, workers, passes, verdict, bus, console) -> None:
    """Append this run to ``.steppy/history.jsonl``. Best-effort and lazy —
    a history failure must never affect the run's outcome."""
    if run_id is None:
        return
    try:
        from .history import append_run

        append_run(
            project_root=project_root,
            run_id=run_id,
            workers=workers or 0,
            passes=passes,
            verdict=verdict,
            bus=bus,
        )
    except Exception as e:  # noqa: BLE001 — telemetry is never worth a failed run
        console.print(f"[dim]could not write run history: {e}[/dim]")


def run_parallel(
    *,
    workers: int,
    scenario: Optional[str],
    tag: Optional[list[str]],
    headed: bool,
    screenshots: bool,
    guide: bool,
    no_asserts: bool,
    no_guide: bool,
    locales: Optional[str],
    layouts: Optional[str],
    tests_dir: Path,
    scenarios_dir: Path,
    pytest_passthrough: list[str],
    no_build: bool = False,
    verbose: bool = False,
    coverage: bool = False,
    trace_mode: str = "off",
    video_mode: str = "off",
    retries: int = 0,
    retry_pass: Optional[int] = None,
    strict_flaky: bool = False,
    fail_fast: Optional[bool] = None,
    bundle: bool = False,
) -> int:
    """Top-level coordinator. Returns the process exit code (0 on success).

    Mirrors the user-facing flag dispatch from cli.run for ``--guide`` /
    ``--no-guide`` etc. so a parallel run produces the same scenario
    selection as the equivalent single-worker run would.

    ``trace_mode`` / ``video_mode`` / ``retries`` are resolved host-side by
    ``cli._resolve_report_modes`` (they need config.yaml) and travel to the
    worker containers as env vars — the coordinator has no pytest process of
    its own. ``pytest_passthrough`` (the user's ``--`` argv) rides along the
    same way.

    ``retry_pass`` / ``fail_fast`` are tri-state: None ⇒ take ``config.parallel``,
    otherwise the CLI flag wins. ``strict_flaky`` makes a pass-on-retry fail the
    build (the default is lenient — see the exit-code block below).

    ``bundle`` turns on the two extra artifact sinks ``steppy bundle create``
    reads. Both land under the shared ``output/`` mount and are keyed by
    scenario, which is safe precisely because the queue guarantees a scenario is
    claimed by exactly one worker.
    """
    console = Console()

    try:
        config = load_config()
    except ConfigError as e:
        console.print(f"[red]ERROR[/red] {e}")
        return 1

    # Validate parallel block + per-app internal_url for selected scenarios.
    parallel_cfg = config.parallel
    if parallel_cfg is None:
        console.print(
            "[red]ERROR[/red] --workers >= 2 requires a `parallel:` block in config.yaml. See README for the schema."
        )
        return 1

    project_root = Path.cwd()

    # The capture root the workers' conftest will write to. Only `output/` is
    # bind-mounted back to the host (compose.py) and the project itself is
    # mounted read-only, so a screenshots_dir pointing elsewhere cannot survive
    # a worker — say so rather than let the run finish with nothing captured.
    screenshots_dir = resolve_output_dir(project_root, config.output.screenshots_dir, DEFAULT_SCREENSHOTS_DIR)
    output_root = (project_root / "output").resolve()
    if not screenshots_dir.is_relative_to(output_root):
        console.print(
            f"[yellow]WARNING[/yellow] output.screenshots_dir ({config.output.screenshots_dir}) resolves outside "
            f"{output_root}, but --workers only mounts output/ back to the host — captures written there will "
            "stay inside the worker container. Move it under output/ or run single-worker."
        )

    compose_file = (project_root / parallel_cfg.compose_file).resolve()
    if not compose_file.exists():
        console.print(f"[red]ERROR[/red] compose file not found: {compose_file} (parallel.compose_file in config.yaml)")
        return 1

    # Validate Docker Compose version (need 2.20+ for !reset).
    try:
        check_compose_version()
    except ComposeError as e:
        console.print(f"[red]ERROR[/red] {e}")
        return 1

    # Discover and filter scenarios — same logic as cli.run for guide /
    # no_guide / scenario / tag, applied here so the worker fan-out matches
    # what the user would have got with --workers 1.
    try:
        slugs = _select_scenario_slugs(
            scenarios_dir=scenarios_dir,
            tests_dir=tests_dir,
            scenario=scenario,
            tag=tag,
            guide=guide,
            no_guide=no_guide,
            console=console,
        )
    except _SelectionExit as e:
        return e.code
    if not slugs:
        console.print("[yellow]INFO[/yellow] no scenarios selected — nothing to do")
        return 0

    # Validate every role used by selected scenarios has internal_url set.
    missing_internal = _missing_internal_urls(config, scenarios_dir, slugs)
    if missing_internal:
        roles = ", ".join(sorted(missing_internal))
        console.print(
            f"[red]ERROR[/red] the following roles lack `internal_url` in config.yaml "
            f"but are used by selected scenarios: {roles}\n"
            f"Add `apps.<role>.internal_url: http://<service>:<port>` for each."
        )
        return 1

    # Exclusion groups. Each declared group collapses into ONE queue task holding
    # its members, decided here — once, single-threaded, on the host, before any
    # container exists. That partition IS the mutual-exclusion mechanism: a worker
    # claims the whole group and runs its members sequentially, so there is no
    # lock to leak and no worker ever waits on another. See steppy.exclusive.
    try:
        group_of = selection.exclusive_groups_for(scenarios_dir, slugs)
    except selection.SelectionError as e:
        console.print(f"[red]ERROR[/red] {e.message}")
        if e.hint:
            console.print(f"[yellow]Hint:[/yellow] {e.hint}")
        return e.exit_code
    tasks = exclusive.partition(slugs, group_of)
    collisions = exclusive.duplicate_task_ids(tasks)
    if collisions:
        for first, dup in collisions:
            console.print(
                f"[red]ERROR[/red] exclusion group / scenario name collision: {first!r} and "
                f"{dup!r} resolve to the same queue entry."
            )
        console.print(
            "[yellow]Hint:[/yellow] task names must be unique ignoring case — the run's "
            "coordination directory lives on the host, where NTFS and APFS treat them as "
            "one file and a whole task would silently never run."
        )
        return 1
    _report_exclusion_groups(tasks, console)

    # Resolve --guide auto-enables (shared with cli.run / cli.rerun).
    screenshots, no_asserts = selection.apply_guide_defaults(guide, screenshots, no_asserts)

    # Resolve locales / layouts (mirrors cli.run).
    locale_list = _resolve_locales(locales, guide, config, console)
    layout_list = _resolve_layouts(layouts, guide, config, console)

    # Resolve the retry pass (CLI flag wins over config, as everywhere else).
    retry_passes_count = parallel_cfg.retry.passes if retry_pass is None else retry_pass
    retry_worker_count = parallel_cfg.retry.workers

    # Resolve effective worker count.
    effective_workers = _resolve_effective_workers(workers, parallel_cfg, slugs)
    # Parallelism is bounded by the number of TASKS, not scenarios: exclusion
    # groups collapse several scenarios into one claimable unit, and a stack that
    # can never claim anything is a full app copy of wasted RAM.
    effective_workers = min(effective_workers, max(1, len(tasks)))
    if effective_workers < 2:
        if len(tasks) < len(slugs):
            # The collapse — not the selection — is what dropped us below two.
            # Returning 0 here would report success having executed nothing, and
            # "every scenario hits one shared database" is the most obvious first
            # use of this feature. One stack running the group serially IS the
            # correct execution of that declaration, so fall through and run it.
            console.print(
                f"[yellow]INFO[/yellow] exclusion groups collapsed {len(slugs)} scenario(s) "
                f"into {len(tasks)} task(s); running on a single stack."
            )
            effective_workers = 1
        else:
            # Should never happen — cli.run only dispatches here for workers >= 2,
            # and "auto" always resolves to >= 1. If we somehow get here with 1
            # (e.g. only one scenario), fall back to single-worker mode by NOT
            # spinning up Docker — surface a clear message and let the user re-run.
            console.print(
                f"[yellow]INFO[/yellow] only {len(slugs)} scenario(s) selected; "
                f"single stack would be used. Re-run without --workers (or with --workers 1) "
                f"to skip Docker entirely."
            )
            return 0

    # Build the per-app internal_url JSON map workers will consume.
    base_url_override_json = json.dumps(
        {role: app.internal_url for role, app in config.apps.items() if app.internal_url}
    )

    # Two ids, deliberately. ``stack_id`` names the Compose projects and the tmp
    # dir, where short and opaque is all that is wanted. ``run_id`` is the run's
    # IDENTITY — it names ``output/coverage/raw/<run_id>/`` and the ``--bundle``
    # sinks, and both are resolved elsewhere by taking the lexicographically last
    # directory (``coverage.merge.latest_run_id``). A bare uuid cannot be sorted,
    # so this path used to make that resolution arbitrary; the sortable form is
    # the same one ``cli._new_run_id`` produces for single-worker runs.
    stack_id = uuid.uuid4().hex[:8]
    run_id = new_run_id()
    tmp_dir = Path(tempfile.mkdtemp(prefix=f"steppy-run-{stack_id}-"))
    console.print(f"\n[bold]steppy parallel run[/bold] (runId={run_id}, workers={effective_workers})")
    console.print(f"[dim]tmp dir: {tmp_dir}[/dim]")

    # Resource warning — heuristic, not a hard block.
    _maybe_warn_resources(effective_workers, console)

    # Seed the shared queue, slowest scenario first. Workers claim in seed
    # order, so putting the long ones up front stops a 3-minute scenario from
    # being claimed last and leaving one worker running alone while the rest
    # idle (classic LPT scheduling). No history ⇒ unchanged order.
    queue = Queue(root=tmp_dir)
    tasks = _order_tasks_longest_first(tasks, project_root, console)
    # The flattened task order is the real execution order, which is what the
    # --bundle sidecar wants to record.
    ordered_slugs = [slug for task in tasks for slug in task.slugs]
    batches = {t.task_id: list(t.slugs) for t in tasks if t.is_batch}
    if batches:
        queue.seed([t.task_id for t in tasks], batches=batches)
    else:
        # Pass no keyword at all when nothing is grouped, so the call — and the
        # queue directory it writes — is byte-identical to the pre-feature one.
        queue.seed([t.task_id for t in tasks])
    queued_note = f"queued {len(slugs)} scenario(s)"
    if batches:
        queued_note += f" in {len(tasks)} task(s)"
    console.print(f"[dim]{queued_note} across {effective_workers} worker(s)[/dim]")

    # Introspect user compose (services with ports, networks, healthcheck check).
    try:
        introspection = introspect_user_compose(compose_file)
    except ComposeError as e:
        console.print(f"[red]ERROR[/red] {e}")
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return 1

    # Ensure worker image (auto-build on first run unless --no-build). Build
    # inputs are resolved by _parallel.image: explicit config → source checkout
    # → a context synthesized from the installed package (so parallel mode
    # works off a plain `pip install`, with no steppy source tree on the box).
    package_root = Path(__file__).resolve().parent
    try:
        image_ref, image_mode = ensure_worker_image(
            image_prefix=parallel_cfg.worker_image,
            package_root=package_root,
            project_root=project_root,
            parallel_cfg=parallel_cfg,
            staging_dir=tmp_dir,
            build=(parallel_cfg.build_image and not no_build),
        )
    except RuntimeError as e:
        console.print(f"[red]ERROR[/red] {e}")
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return 1
    console.print(f"[dim]worker image: {image_ref} ({_IMAGE_MODE_LABELS.get(image_mode, image_mode)})[/dim]")

    # Echo the report settings that now actually reach the workers, so it is
    # obvious when --trace/--video/--retries took effect (they used to be
    # silently dropped in parallel mode).
    if trace_mode and trace_mode != "off":
        console.print(f"[dim]Playwright trace: {trace_mode}[/dim]")
    if video_mode and video_mode != "off":
        console.print(f"[dim]Playwright video: {video_mode}[/dim]")
    if retries:
        console.print(f"[dim]Flaky-retry: up to {retries} rerun(s) per failure (in-process)[/dim]")
    if pytest_passthrough:
        console.print(f"[dim]pytest passthrough: {' '.join(pytest_passthrough)}[/dim]")

    # Optional per-worker `.auth` isolation. The shared dir is bind-mounted RW
    # into every worker, so N workers race on one storage-state file; giving
    # each a private (pre-seeded) copy trades one login for N.
    auth_mounts = _prepare_isolated_auth(
        enabled=parallel_cfg.isolate_auth,
        worker_count=effective_workers,
        project_root=project_root,
        tmp_dir=tmp_dir,
        console=console,
    )

    # Render per-worker overrides. The kwargs are kept as a dict so the retry
    # pass can re-render with exactly the same settings (only `coord_subdir`
    # differs) — a drifted setting there would be invisible until a retry
    # behaved differently from the run that spawned it.
    overrides: list[Path] = []
    locales_csv = ",".join(locale_list)
    layouts_csv = ",".join(layout_list)
    image_prefix, _, image_tag = image_ref.partition(":")
    base_override_kwargs = dict(
        introspection=introspection,
        worker_image=image_prefix,
        image_tag=image_tag,
        run_id=run_id,
        project_root=project_root,
        tmp_dir=tmp_dir,
        base_url_override=base_url_override_json,
        locales=locales_csv,
        layouts=layouts_csv,
        guide_mode="1" if guide else "",
        no_asserts="1" if no_asserts else "",
        screenshots="1" if screenshots else "",
        coverage="1" if coverage else "",
        pre_auth_command=(parallel_cfg.pre_auth.command if parallel_cfg.pre_auth else None),
        pre_auth_image=(parallel_cfg.pre_auth.image if parallel_cfg.pre_auth else None),
        pre_auth_timeout=(parallel_cfg.pre_auth.timeout if parallel_cfg.pre_auth else 600),
        trace_mode=trace_mode,
        video_mode=video_mode,
        retries=retries,
        pytest_extra=pytest_passthrough,
        # In-container paths: /steppy/project is the RO project mount and
        # /steppy/project/output the RW overlay onto the host's output/.
        events_dir=(f"/steppy/project/output/{EVENTS_SUBDIR}/{run_id}" if bundle else ""),
        junit_dir=(f"/steppy/project/output/{JUNIT_SUBDIR}/{run_id}" if bundle else ""),
        # The tour replays' build-id gate, read inside the container.
        tour_expect_build_id=os.environ.get("STEPPY_TOUR_EXPECT_BUILD_ID", ""),
    )
    for worker_index in range(1, effective_workers + 1):
        override_yml = render_override(
            worker_index=worker_index,
            auth_mount=auth_mounts[worker_index - 1] if auth_mounts else "",
            **base_override_kwargs,
        )
        override_path = tmp_dir / f"override-w{worker_index}.yml"
        override_path.write_text(override_yml, encoding="utf-8")
        overrides.append(override_path)

    # Pre-create overlay dirs the workers will write to. Without this, Docker
    # bind-mounts create them owned by root, which breaks subsequent host-side
    # cleanup on Linux.
    (project_root / ".auth").mkdir(exist_ok=True)
    (project_root / "output").mkdir(exist_ok=True)

    if bundle:
        write_run_sidecar(
            project_root,
            run_id=run_id,
            spec={
                "guide": guide,
                "no_guide": no_guide,
                "no_asserts": no_asserts,
                "screenshots": screenshots,
                "coverage": coverage,
                "locales": list(locale_list),
                "layouts": list(layout_list),
                "workers": effective_workers,
                "tag": list(tag) if tag else None,
                "scenario": scenario,
            },
            slugs=ordered_slugs,
        )
        console.print(f"[dim]bundle capture: enabled — create it with: steppy bundle create --run-id {run_id}[/dim]")

    exit_code = 0
    project_names: list[str] = []
    log_tailers: list[WorkerLogTailer] = []

    # EventBus + RawLogChannel wire worker stdout into the renderer.
    # The raw channel always writes to a per-worker file; it also prints
    # ``[wN] <line>`` to the console when --verbose is set.
    raw_channel = RawLogChannel(console=console, verbose=verbose, log_dir=tmp_dir)
    bus = EventBus(raw_line_sink=raw_channel)
    for i in range(1, effective_workers + 1):
        bus.register_worker(f"w{i}")
    renderer = make_renderer(bus=bus, console=console, total_scenarios=len(slugs))

    with CoordinatorLifecycle() as lc:
        # 1. Spin up all stacks in parallel via `compose up -d --wait`.
        try:
            project_names = _bring_up_all_stacks(
                stack_id=stack_id,
                worker_count=effective_workers,
                compose_file=compose_file,
                overrides=overrides,
                healthcheck_timeout=parallel_cfg.healthcheck_timeout,
                lifecycle=lc,
                console=console,
            )
        except _StackError as e:
            console.print(f"[red]ERROR[/red] failed to bring up stack: {e}")
            return 1

        # 1b. Resolve each worker's container ID once so the wait loop can
        # poll via `docker inspect` directly — `compose ps -q` (even with
        # -a) is slower per call and unnecessary once we have the ID.
        try:
            worker_container_ids = _resolve_worker_container_ids(project_names)
        except _StackError as e:
            console.print(f"[red]ERROR[/red] {e}")
            return 1

        # 2. Start the renderer and log multiplexers. The renderer is
        # registered for teardown first so it stops painting BEFORE the
        # final summary call (otherwise the Live overlay would clobber it).
        renderer.start()
        lc.on_teardown(renderer.stop)
        lc.on_teardown(raw_channel.close)

        for i, project in enumerate(project_names, start=1):
            tailer = WorkerLogTailer(
                project=project,
                worker_id=f"w{i}",
                bus=bus,
                app_log_file=tmp_dir / f"w{i}-stack.log",
            )
            tailer.start()
            log_tailers.append(tailer)
            lc.on_teardown(tailer.stop)

        # 3. Wait for every worker container to exit (queue drains naturally).
        # The circuit breaker rides on the wait loop's poll cycle: it watches
        # results land and aborts once nearly everything is failing, so a down
        # app doesn't cost the full wall clock.
        breaker = _make_circuit_breaker(parallel_cfg.circuit_breaker, fail_fast, queue, console)
        breaker_tripped = False
        try:
            worker_exit_codes = _wait_for_workers(worker_container_ids, console, on_poll=breaker)
        except KeyboardInterrupt:
            console.print("\n[yellow]Ctrl-C received — tearing down stacks…[/yellow]")
            for i in range(1, effective_workers + 1):
                bus.mark_worker_finished(f"w{i}", interrupted=True)
            renderer.stop()
            # Same shutdown as the success path, over the partial results — so
            # a Ctrl-C'd run still persists artifacts and refreshes lastfailed.
            _finalize_run(
                queue=queue,
                bus=bus,
                console=console,
                tmp_dir=tmp_dir,
                project_root=project_root,
                tests_dir=tests_dir,
                run_id=run_id,
                workers=effective_workers,
                verbose=verbose,
                screenshots_dir=screenshots_dir,
            )
            if coverage:
                # No auto-merge on Ctrl-C — records already on the output/
                # bind mount; merging is cheap to do once things calm down.
                console.print(f"[dim]coverage records kept — merge with: steppy coverage merge --run-id {run_id}[/dim]")
            return 130
        except _CircuitBreakerTripped as e:
            breaker_tripped = True
            worker_exit_codes = [1] * effective_workers
            renderer.stop()
            console.print()
            console.rule("[bold red]Aborted — circuit breaker[/bold red]")
            console.print(f"  [red]{e}[/red]")
            console.print(
                "  [dim]This looks like infrastructure, not tests — the app, auth or a\n"
                "  seed step is likely broken. Check output/failures/_worker-logs/.[/dim]"
            )
            console.print("  [dim]Disable with --no-fail-fast or parallel.circuit_breaker.enabled: false[/dim]")

        # 4. Reconcile worker exit codes back into the bus so in-flight
        # tests (worker died mid-test) get a synthetic outcome before the
        # summary renders.
        for i, project_exit in enumerate(worker_exit_codes, start=1):
            bus.mark_worker_finished(f"w{i}", died=project_exit != 0)

        # 5. Stop the renderer before printing the summary — otherwise
        # the Live overlay re-paints over the summary lines.
        renderer.stop()

        # 5a. Anything queued and never claimed — every worker died with work
        # left. Captured before the retry pass, which will try to run it.
        never_started = set(queue.unclaimed_slugs())

        # 5b. Retry pass. Re-runs the failures at reduced concurrency in the
        # still-running stacks, which is what separates a contention flake from
        # a real failure. Skipped when the breaker tripped — that is infra, and
        # retrying it would just take twice as long to reach the same verdict.
        retry_passes = _run_retry_passes(
            enabled=(not breaker_tripped) and retry_passes_count > 0,
            passes=retry_passes_count,
            retry_workers=retry_worker_count,
            queue=queue,
            bus=bus,
            group_of=group_of,
            project_names=project_names,
            compose_file=compose_file,
            tmp_dir=tmp_dir,
            base_override_kwargs=base_override_kwargs,
            auth_mounts=auth_mounts,
            console=console,
            verbose=verbose,
            lifecycle=lc,
        )

        # 6. Aggregate results and render the final summary.
        results, orphans, verdict = _finalize_run(
            queue=queue,
            bus=bus,
            console=console,
            tmp_dir=tmp_dir,
            project_root=project_root,
            tests_dir=tests_dir,
            retry_passes=retry_passes,
            run_id=run_id,
            workers=effective_workers,
            verbose=verbose,
            screenshots_dir=screenshots_dir,
            never_started=never_started,
        )
        failed = sum(1 for r in results if r.exit_code != 0)
        orphan_count = sum(len(v) for v in orphans.values())
        # Anything that was queued and never claimed by anybody. Nothing else in
        # the system observes it — orphan detection walks claims/, result
        # collection walks results/, and a task with neither file is invisible to
        # both — so without this a run whose workers all died could report success
        # having executed nothing.
        abandoned = sorted(never_started - {r.slug for r in results})
        if abandoned:
            _report_abandoned(abandoned, console)

        # Coverage merge: workers wrote raw records to the shared output/
        # bind mount under this run_id; fold them into the map host-side.
        # Lazy import + best-effort: a merge failure never changes the run's
        # exit code (records persist; `steppy coverage merge --run-id` retries).
        if coverage:
            try:
                from .coverage.merge import CoverageSettings, merge_run

                settings = CoverageSettings.from_config(config, project_root)
                merge_summary = merge_run(settings, run_id)
                console.print(
                    f"[green]OK[/green] Coverage: merged "
                    f"{merge_summary['totals']['scenarios']} scenario(s), "
                    f"{merge_summary['totals']['nodes']} map node(s) → {settings.store_path}"
                )
            except Exception as e:
                console.print(f"[yellow]WARNING[/yellow] Coverage merge failed: {e}")
                console.print(f"[dim]Raw records kept — retry with: steppy coverage merge --run-id {run_id}[/dim]")

        # Exit code. Without a retry pass this is unchanged: any failure
        # (worker container exit != 0, scenario fail, or orphan) → 1.
        #
        # With a retry pass, FLAKY (failed then passed) is LENIENT by default —
        # a pass is a pass, so only scenarios that failed every attempt fail the
        # build. `--strict-flaky` restores the strict reading for teams that
        # want a flake to break CI. Worker exit codes are ignored once a verdict
        # exists: a worker that reported "1" because a scenario failed, which
        # the retry then passed, must not veto that.
        if verdict is not None:
            any_failure = bool(verdict.failed) or orphan_count > 0 or (strict_flaky and bool(verdict.flaky))
        else:
            any_failure = failed > 0 or orphan_count > 0 or any(c != 0 for c in worker_exit_codes)
        # On BOTH branches, including the verdict one — a scenario that never ran
        # is not a flake to be forgiven, and the verdict branch drops worker exit
        # codes by design.
        if abandoned:
            any_failure = True
        if breaker_tripped:
            any_failure = True
        exit_code = 1 if any_failure else 0

        # 7. Teardown happens automatically via ExitStack callbacks here.

    # Clean up tmp dir unless STEPPY_KEEP_RUN_DIR=1 (debugging escape hatch).
    if os.environ.get("STEPPY_KEEP_RUN_DIR") != "1":
        shutil.rmtree(tmp_dir, ignore_errors=True)
    else:
        console.print(f"[dim]preserved tmp dir: {tmp_dir}[/dim]")

    return exit_code


# ── Retry pass / circuit breaker / auth isolation ──────────────────────────


class _CircuitBreakerTripped(RuntimeError):
    """Raised from the wait loop's poll callback to abort a doomed run."""


def _make_circuit_breaker(cfg, fail_fast: Optional[bool], queue, console):
    """Build the ``on_poll`` callback for :func:`_wait_for_workers`, or None.

    Fires when ``min_completed`` scenarios have reported AND at least
    ``fail_ratio`` of them failed. Both thresholds matter: the ratio alone would
    trip on the first failed scenario of a run, and the count alone would trip
    on a suite that is merely red.

    ``fail_fast`` is the tri-state CLI override (None ⇒ use config).
    """
    enabled = cfg.enabled if fail_fast is None else fail_fast
    if not enabled:
        return None

    def _check() -> None:
        results = queue.collect_results()
        completed = len(results)
        if completed < cfg.min_completed:
            return
        failed = sum(1 for r in results if r.exit_code != 0)
        if failed / completed >= cfg.fail_ratio:
            raise _CircuitBreakerTripped(
                f"{failed}/{completed} completed scenarios failed "
                f"({failed / completed:.0%} >= {cfg.fail_ratio:.0%} threshold)."
            )

    return _check


def _report_exclusion_groups(tasks: list, console: Console) -> None:
    """Print the exclusion-group census, or nothing when none are declared.

    Every group is named with its member count, on one line, because that is what
    makes a typo visible: ``stripe-sandbox[1], stripe-sandbx[1]`` reads as a
    mistake at a glance, whereas two silently-independent groups do not.

    A single-member group gets its own warning, since it serialises nothing. It
    is only a warning: ``--tag`` legitimately narrows a group to one selected
    member — though that is worth noticing too, because the guarantee holds
    within one run, so a group split across two shard runs has lost it.
    """
    batches = [t for t in tasks if t.is_batch]
    if not batches:
        return
    census = ", ".join(f"{t.group}[{len(t.slugs)}]" for t in batches)
    grouped = sum(len(t.slugs) for t in batches)
    console.print(
        f"[dim]exclusion groups: {census} — {len(batches)} group(s) serialising "
        f"{grouped} scenario(s) into {len(batches)} task(s)[/dim]"
    )
    singletons = [t.group for t in batches if len(t.slugs) == 1]
    if singletons:
        console.print(
            f"[yellow]WARNING[/yellow] exclusion group(s) with a single selected scenario: "
            f"{', '.join(singletons)}. A narrowed selection explains this; otherwise it is "
            f"a typo, and the group is serialising nothing."
        )


_ABANDONED_LIST_CAP = 10


def _report_abandoned(slugs: list[str], console: Console) -> None:
    """Print the scenarios that were queued and never started.

    Capped, because a tripped circuit breaker abandons the whole remaining queue
    and two hundred lines is not a summary. Printed here rather than through
    ``render_summary`` so the renderer's contract is untouched.
    """
    shown = slugs[:_ABANDONED_LIST_CAP]
    console.print()
    console.rule("[bold red]Never started[/bold red]")
    console.print(f"  [red]{len(slugs)} scenario(s) were queued but never claimed by any worker.[/red]")
    for slug in shown:
        console.print(f"    [dim]{slug}[/dim]")
    if len(slugs) > len(shown):
        console.print(f"    [dim]… and {len(slugs) - len(shown)} more[/dim]")
    console.print(
        "  [dim]This means every worker exited with work left — check "
        "output/failures/_worker-logs/. The run is reported as failed.[/dim]"
    )


def _order_tasks_longest_first(tasks: list, project_root: Path, console: Console) -> list:
    """Reorder the queue so recorded-slow tasks are claimed first.

    A batch is one scheduling unit, so its weight is the **sum** of its members'
    medians — otherwise a large exclusion group claimed last becomes the run's
    tail while every other worker idles. Tasks with no recorded member sort last
    and ties keep declaration order, mirroring ``history.order_by_duration_desc``;
    for an all-singleton run this reduces to it element for element.

    Best-effort, like the ordering it replaces: never fatal.
    """
    try:
        from .history import median_durations

        durations = median_durations(project_root)
        if not durations:
            return tasks
        weights: dict[str, float] = {}
        known: list = []
        unknown: list = []
        for index, task in enumerate(tasks):
            total = sum(durations[s] for s in task.slugs if s in durations)
            if total > 0:
                weights[task.task_id] = total
                known.append((index, task))
            else:
                unknown.append(task)
        known.sort(key=lambda pair: (-weights[pair[1].task_id], pair[0]))
        ordered = [task for _, task in known] + unknown
        console.print(
            f"[dim]queue order: longest-first from run history ({len(known)}/{len(tasks)} with timings)[/dim]"
        )
        return ordered
    except Exception:  # noqa: BLE001 — ordering is an optimisation, never fatal
        return tasks


def _prepare_isolated_auth(*, enabled, worker_count, project_root, tmp_dir, console) -> list[str]:
    """Create a private, pre-seeded ``.auth`` dir per worker. Returns the list of
    absolute host paths (posix), or ``[]`` when the feature is off.

    Seeding from the project's shared ``.auth`` means a still-valid cached
    session is reused instead of forcing N cold logins on every run; each worker
    then owns its copy, so re-auth writes never race.
    """
    if not enabled:
        return []
    shared = project_root / ".auth"
    mounts: list[str] = []
    for i in range(1, worker_count + 1):
        worker_auth = tmp_dir / "auth" / f"w{i}"
        worker_auth.mkdir(parents=True, exist_ok=True)
        if shared.is_dir():
            for src in shared.glob("*.json"):
                try:
                    shutil.copy2(src, worker_auth / src.name)
                except OSError:
                    pass
        mounts.append(worker_auth.resolve().as_posix())
    console.print(f"[dim]auth isolation: {worker_count} private .auth dir(s) (parallel.isolate_auth)[/dim]")
    return mounts


def _run_retry_passes(
    *,
    enabled,
    passes,
    retry_workers,
    queue,
    bus,
    group_of,
    project_names,
    compose_file,
    tmp_dir,
    base_override_kwargs,
    auth_mounts,
    console,
    verbose,
    lifecycle,
) -> list:
    """Drive up to ``passes`` retry attempts. Returns the list of PassResults.

    Each attempt re-runs only what the previous one left failing (including
    orphans, so a dead worker's scenario gets a second chance instead of being
    a hard loss). Stops early once nothing is failing.

    Never raises: a retry pass that cannot start degrades to "no retry" and the
    main pass's verdict stands.
    """
    if not enabled:
        return []
    from ._parallel.retry import PassResult, run_retry_pass

    prior = PassResult(
        attempt=1,
        coord_root=tmp_dir,
        results=queue.collect_results(),
        # Reads the batch map off the Queue that seeded this directory, so orphans
        # are always member slugs — never an exclusion group's NAME, which would
        # seed a retry task for a test file that does not exist and leave the
        # group's real members un-retried.
        orphans=queue.orphaned_running_tasks(),
        bus=bus,
        never_started=set(queue.unclaimed_slugs()),
    )
    pending = sorted(prior.pending_slugs)
    if not pending:
        return []

    # Slice the stacks down to the configured retry width. The unused stacks
    # keep running (they're torn down with the rest at the end) — we just don't
    # give them a worker.
    projects = project_names[: max(1, min(retry_workers, len(project_names)))]

    out: list = []
    for attempt in range(2, 2 + passes):
        # Regroup INSIDE the loop: ``pending`` is reassigned per attempt, so
        # partitioning once outside would let attempt 3 run members of one
        # exclusion group concurrently whenever retry.workers is above 1.
        retry_tasks = exclusive.partition(pending, group_of or {})
        retry_batches = {t.task_id: list(t.slugs) for t in retry_tasks if t.is_batch}
        result = run_retry_pass(
            attempt=attempt,
            slugs=[t.task_id for t in retry_tasks],
            batches=retry_batches or None,
            projects=projects,
            compose_file=compose_file,
            tmp_dir=tmp_dir,
            override_kwargs={**base_override_kwargs, "auth_mount_by_worker": auth_mounts},
            console=console,
            verbose=verbose,
            resolve_container_ids=_resolve_worker_container_ids,
            wait_for_workers=_wait_for_workers,
            register_teardown=lifecycle.on_teardown,
        )
        if result is None:
            break
        out.append(result)
        pending = sorted(result.pending_slugs)
        if not pending:
            break
    return out


# ── Selection helpers (mirror cli.run's filter logic) ──────────────────────


class _SelectionExit(Exception):
    """Raised by _select_scenario_slugs to short-circuit on user errors. The
    code attribute is the exit code we should return from run_parallel."""

    def __init__(self, code: int):
        super().__init__()
        self.code = code


def _select_scenario_slugs(
    *,
    scenarios_dir: Path,
    tests_dir: Path,
    scenario: Optional[str],
    tag: Optional[list[str]],
    guide: bool,
    no_guide: bool,
    console: Console,
) -> list[str]:
    """Mirror of the filter logic in cli.run, returning the list of scenario
    slugs to enqueue. We only need slugs (not test_file paths) since workers
    re-derive the test_file from the slug. Delegates to the shared
    ``selection.select_scenario_slugs`` (FLAWS §8.3) so this can't drift from the
    single-worker path; translates its ``SelectionError`` to the ``_SelectionExit``
    that ``run_parallel`` already catches."""
    try:
        return selection.select_scenario_slugs(
            scenarios_dir=scenarios_dir,
            tests_dir=tests_dir,
            scenario=scenario,
            tag=tag,
            guide=guide,
            no_guide=no_guide,
        )
    except selection.SelectionError as e:
        console.print(f"[red]ERROR[/red] {e.message}")
        if e.hint:
            console.print(f"[yellow]Hint:[/yellow] {e.hint}")
        raise _SelectionExit(e.exit_code)


def _missing_internal_urls(config: SteppyConfig, scenarios_dir: Path, slugs: list[str]) -> set[str]:
    """Return the set of role names that are used by selected scenarios but
    have no `internal_url` configured. We re-parse to learn which roles each
    scenario touches."""
    if not scenarios_dir.exists():
        return set()
    try:
        scenarios = parse_scenarios_from_directory(scenarios_dir)
    except ParseError:
        return set()
    selected = {s.slug: s for s in scenarios if s.slug in set(slugs)}
    used_roles: set[str] = set()
    for s in selected.values():
        for step in s.steps:
            role = getattr(step, "role", None)
            if isinstance(role, str) and role and role != "all":
                used_roles.add(role)
    missing: set[str] = set()
    for role in used_roles:
        app = config.apps.get(role)
        if app is None:
            # Role not in config — that's a different validation problem,
            # surface it as missing internal_url to fail fast.
            missing.add(role)
        elif not app.internal_url:
            missing.add(role)
    return missing


def _resolve_locales(locales: Optional[str], guide: bool, config: SteppyConfig, console: Console) -> list[str]:
    try:
        return selection.resolve_locales(locales, guide, config)
    except selection.SelectionError as e:
        console.print(f"[red]ERROR[/red] {e.message}")
        raise _SelectionExit(e.exit_code)


def _resolve_layouts(layouts: Optional[str], guide: bool, config: SteppyConfig, console: Console) -> list[str]:
    try:
        return selection.resolve_layouts(layouts, guide, config)
    except selection.SelectionError as e:
        console.print(f"[red]ERROR[/red] {e.message}")
        raise _SelectionExit(e.exit_code)


def _resolve_effective_workers(workers: int | str, parallel_cfg, slugs: list[str]) -> int:
    """Resolve auto / explicit workers count, capped at scenario count."""
    if workers == "auto" or (isinstance(workers, str) and workers.lower() == "auto"):
        cpu = os.cpu_count() or 2
        return max(1, min(cpu // 2, len(slugs), _DEFAULT_AUTO_CAP))
    return min(int(workers), len(slugs))


def _maybe_warn_resources(workers: int, console: Console) -> None:
    """Print a soft warning if the worker count looks high for this host.
    Heuristic: > cpu_count / 2 OR > 8 workers triggers the warning. Doesn't
    block — users may have headroom we can't detect."""
    cpu = os.cpu_count() or 2
    if workers > max(1, cpu // 2) or workers > _DEFAULT_AUTO_CAP:
        console.print(
            f"[yellow]WARNING[/yellow] requesting {workers} parallel stacks on "
            f"{cpu}-CPU host — make sure your machine has enough RAM/CPU "
            f"(each stack runs your full app + chromium)."
        )


# ── Compose lifecycle helpers ──────────────────────────────────────────────


class _StackError(RuntimeError):
    pass


def _bring_up_all_stacks(
    *,
    stack_id: str,
    worker_count: int,
    compose_file: Path,
    overrides: list[Path],
    healthcheck_timeout: int,
    lifecycle: CoordinatorLifecycle,
    console: Console,
) -> list[str]:
    """Spin up every stack in parallel via `compose up -d --wait`. Registers
    a teardown callback for each stack as soon as up is invoked. Returns the
    list of project names.

    Keyed on ``stack_id`` (short, opaque) rather than the run's identity —
    Compose project names have their own charset rules and end up in
    ``docker ps`` output, and ``steppy stacks cleanup`` matches them by the
    ``steppy-run-`` prefix alone."""
    project_names = [f"{_PROJECT_NAME_PREFIX}{stack_id}-w{i}" for i in range(1, worker_count + 1)]

    def _up_one(idx: int) -> tuple[int, Optional[str]]:
        project = project_names[idx]
        override = overrides[idx]
        # Register teardown BEFORE invoking up so a crash mid-up still cleans
        # up partial state. The compose down call is idempotent.
        lifecycle.on_teardown(lambda p=project: _down(p, console))
        cmd = [
            "docker",
            "compose",
            "-p",
            project,
            "-f",
            str(compose_file),
            "-f",
            str(override),
            "up",
            "-d",
            "--wait",
            "--wait-timeout",
            str(healthcheck_timeout),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if result.returncode != 0:
            return result.returncode, f"{project}: {result.stderr.strip()}"
        return 0, None

    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        futures = [pool.submit(_up_one, i) for i in range(worker_count)]
        for fut in as_completed(futures):
            code, err = fut.result()
            if code != 0:
                failures.append(err or f"unknown error (exit {code})")

    if failures:
        raise _StackError("; ".join(failures))

    console.print(f"[green]✓[/green] all {worker_count} stack(s) healthy")
    return project_names


def _resolve_worker_container_ids(project_names: list[str]) -> dict[str, str]:
    """Look up each project's steppy-worker container ID once, right after
    ``compose up -d --wait`` returns. Raises ``_StackError`` if any project
    can't be resolved — better to fail fast than have ``_wait_for_workers``
    spin in a silent loop later.

    The ``-a`` flag in :func:`_get_worker_container_id` is required: a worker
    that fails fast on a misconfig may already be in ``exited`` state by the
    time we get here.
    """
    container_ids: dict[str, str] = {}
    for project in project_names:
        cid = _get_worker_container_id(project)
        if cid is None:
            raise _StackError(
                f"could not resolve steppy-worker container for {project} "
                f"(docker compose ps -a -q steppy-worker returned nothing)"
            )
        container_ids[project] = cid
    return container_ids


def _wait_for_workers(container_ids: dict[str, str], console: Console, on_poll=None) -> list[int]:
    """Block until every worker container exits. Returns each project's
    steppy-worker container exit code, in the same order as the input dict.

    ``container_ids`` is the map produced by :func:`_resolve_worker_container_ids`
    right after ``compose up -d --wait``. Polling uses ``docker inspect``
    directly on the cached ID — no per-poll ``compose ps`` round-trip.

    ``on_poll`` is called once per cycle while workers are still running. It is
    the circuit breaker's hook: raising :class:`_CircuitBreakerTripped` from it
    aborts the wait (and, via the caller's ExitStack, the whole run). It is
    deliberately called only while something is still pending — there is nothing
    to abort once every worker has exited.
    """
    pending = dict(container_ids)
    exit_codes: dict[str, int] = {}
    poll_interval = 1.5

    while pending:
        for project, container_id in list(pending.items()):
            state = _inspect_container_state(container_id)
            if state and state.get("Status") == "exited":
                exit_codes[project] = int(state.get("ExitCode", 1))
                del pending[project]
                console.print(f"[dim]{project}: worker exited (code {exit_codes[project]})[/dim]")
        if pending:
            if on_poll is not None:
                on_poll()
            time.sleep(poll_interval)

    return [exit_codes.get(p, 1) for p in container_ids.keys()]


def _get_worker_container_id(project: str) -> Optional[str]:
    """Return the docker container ID of the steppy-worker service in this
    project, or None if it doesn't exist.

    ``-a`` is load-bearing: without it ``docker compose ps -q`` hides
    containers whose status is ``exited``. After a worker drains the
    queue and the container exits cleanly, the wait loop would otherwise
    see ``None`` forever and hang. See ``tests/test_parallel_coordinator_wait``.
    """
    try:
        result = subprocess.run(
            ["docker", "compose", "-p", project, "ps", "-a", "-q", "steppy-worker"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    cid = result.stdout.strip()
    return cid or None


def _inspect_container_state(container_id: str) -> Optional[dict]:
    try:
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{json .State}}", container_id],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def _down(project: str, console: Optional[Console] = None) -> None:
    """Tear down one stack via `docker compose -p <project> down -v`. Best
    effort — never raises; teardown errors are logged to stderr by the
    lifecycle's _safe_call."""
    cmd = ["docker", "compose", "-p", project, "down", "-v", "--timeout", "10"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        if console:
            console.print(f"[yellow]teardown[/yellow] {project}: {e}")
        return
    if result.returncode != 0 and console:
        console.print(f"[yellow]teardown[/yellow] {project}: {result.stderr.strip()}")


# ── Rescue command (steppy stacks cleanup) ─────────────────────────────────


def cleanup_stacks() -> int:
    """List orphaned ``steppy-run-*`` Compose projects and tear them down.
    Returns the number of stacks cleaned (for the CLI to surface)."""
    console = Console()
    try:
        result = subprocess.run(
            ["docker", "compose", "ls", "--all", "--format", "json"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        console.print(f"[red]ERROR[/red] could not list compose projects: {e}")
        return 1
    if result.returncode != 0:
        console.print(f"[red]ERROR[/red] docker compose ls failed: {result.stderr.strip()}")
        return 1

    try:
        projects = json.loads(result.stdout) or []
    except json.JSONDecodeError:
        console.print("[red]ERROR[/red] could not parse compose ls output")
        return 1

    targets = [
        p["Name"] for p in projects if isinstance(p, dict) and p.get("Name", "").startswith(_PROJECT_NAME_PREFIX)
    ]
    if not targets:
        console.print("[green]✓[/green] no orphaned steppy-run-* stacks found")
        return 0

    console.print(f"[bold]Cleaning up {len(targets)} orphaned stack(s):[/bold] {', '.join(targets)}")
    for project in targets:
        _down(project, console)
    console.print("[green]✓[/green] cleanup complete")
    return 0
