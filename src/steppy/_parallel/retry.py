"""Scenario-level retry pass for parallel runs.

After the main queue drains, re-run the scenarios that failed — at reduced
concurrency, in the **already-running** stacks. A scenario that fails under N
workers and then passes alone is FLAKY (contention), not broken; one that fails
both times is a real failure. That single distinction is what turns an
18-failure summary into "3 real failures, 15 flaky".

Why this is different from ``report.retries`` (pytest-rerunfailures): that
reruns the test in-process, immediately — same worker, same moment, same
contention. It catches per-action timing flakes and nothing else. This pass
waits for the contention to be *gone* before retrying, which is the only way to
attribute a failure to it.

Mechanism — the cheap part is that there barely is one:

* ``worker.py`` resolves its whole world from ``STEPPY_COORD_DIR``, so pointing
  attempt *n* at ``<tmp>/retry-<n>/`` gives it a private queue/claims/results
  tree. **Neither ``worker.py`` nor ``queue.py`` needs any change**, and
  attempt 1's evidence stays intact on disk beside attempt 2's (which is what
  lets ``failures.py`` copy the right ``pytest.log`` afterwards).
* The app services are left running. We recreate only ``steppy-worker``, with
  ``--no-deps``, so the database, the network and any completed ``steppy-preauth``
  survive untouched and the retry starts in seconds rather than minutes.

Two things that look optional but are not:

* A **fresh EventBus** per pass. ``EventBus._tests`` is keyed
  ``(worker_id, nodeid)`` and ``test-start`` overwrites the record in place,
  while ``_counts`` only ever increments — feeding a second attempt into the
  same bus would both lose attempt 1's outcome and double-count the totals.
* **Fresh log tailers.** ``docker compose logs -f`` is bound to the container
  that existed when it started; recreating ``steppy-worker`` orphans it.

Lazy-imported from :mod:`steppy.coordinator`, like every other ``_parallel``
module, so the single-worker path stays clean
(``tests/test_no_parallel_import_on_default_path.py``).
"""

from __future__ import annotations

import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .compose import render_override
from .event_bus import EventBus, RawLogChannel
from .progress import WorkerLogTailer
from .queue import Queue, ScenarioResult


@dataclass
class PassResult:
    """One attempt's outcome. ``attempt`` is 1 for the main pass."""

    attempt: int
    coord_root: Path
    results: list[ScenarioResult]
    orphans: dict[str, list[str]] = field(default_factory=dict)
    bus: Optional[EventBus] = None
    # Scenarios that were queued and never claimed by anybody — every worker died
    # with work left. Deliberately NOT part of ``failed_slugs``: see below.
    never_started: set[str] = field(default_factory=set)

    @property
    def failed_slugs(self) -> set[str]:
        """Scenarios this attempt did not get a clean result for — non-zero
        exit codes plus orphans (claimed, never reported)."""
        out = {r.slug for r in self.results if r.exit_code != 0}
        for slugs in self.orphans.values():
            out.update(slugs)
        return out

    @property
    def pending_slugs(self) -> set[str]:
        """What the next attempt should run: the failures **plus** anything that
        never started.

        The split matters because :func:`classify` builds ``ever_failed`` from
        ``failed_slugs``. A scenario that never ran and then passed on retry is not
        flaky, and marking it so would poison the append-only
        ``.steppy/history.jsonl`` that ``steppy flaky`` ranks from — permanently,
        and for a scenario that is not even faulty. An exclusion group makes that
        acute: one worker death strands every unstarted member of its batch at
        once, not just the one in flight.
        """
        return self.failed_slugs | self.never_started

    @property
    def passed_slugs(self) -> set[str]:
        return {r.slug for r in self.results if r.exit_code == 0}


@dataclass(frozen=True)
class Verdict:
    """Final per-scenario classification across every attempt."""

    passed: list[str]
    flaky: list[str]  # failed at least once, passed in the end
    failed: list[str]  # never passed
    attempts: dict[str, int]  # slug -> how many attempts it took/consumed

    @property
    def any_real_failure(self) -> bool:
        return bool(self.failed)


def classify(passes: list[PassResult]) -> Verdict:
    """Fold the per-attempt results into passed / flaky / failed.

    Later attempts win: a scenario is FLAKY when some attempt failed it and a
    later one passed it, FAILED when no attempt ever passed it. Scenarios that
    only ever passed are plain passes.

    Orphans count as failures for the attempt they happened in, so a worker that
    died mid-scenario is retried like any other failure instead of being a hard
    loss (the v0.1 ``queue.orphaned_running_tasks`` behaviour).
    """
    final_outcome: dict[str, bool] = {}
    ever_failed: set[str] = set()
    attempts: dict[str, int] = {}

    for p in passes:
        failed = p.failed_slugs
        for slug in failed:
            ever_failed.add(slug)
            final_outcome[slug] = False
            attempts[slug] = p.attempt
        for slug in p.passed_slugs:
            final_outcome[slug] = True
            attempts[slug] = p.attempt

    passed, flaky, failed_out = [], [], []
    for slug in sorted(final_outcome):
        if not final_outcome[slug]:
            failed_out.append(slug)
        elif slug in ever_failed:
            flaky.append(slug)
        else:
            passed.append(slug)
    return Verdict(passed=passed, flaky=flaky, failed=failed_out, attempts=attempts)


def log_roots_by_slug(passes: list[PassResult]) -> dict[str, Path]:
    """Map each scenario to the coord root of its FINAL attempt.

    ``failures.persist_failure_artifacts`` reads ``<root>/logs/<worker>/<slug>.log``;
    without this it would look in the main pass's dir and silently copy a stale
    (or missing) log for anything the retry pass touched.
    """
    roots: dict[str, Path] = {}
    for p in passes:  # later passes overwrite earlier ones
        for slug in p.failed_slugs | p.passed_slugs:
            roots[slug] = p.coord_root
    return roots


def _recreate_worker(
    *,
    project: str,
    compose_file: Path,
    override: Path,
) -> tuple[int, Optional[str]]:
    """Replace one project's ``steppy-worker`` container with a fresh one bound
    to the retry override. ``--no-deps`` is what keeps the app services (and the
    already-completed preauth) alive, so only the worker restarts."""
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
        "--no-deps",
        "--force-recreate",
        "steppy-worker",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        return result.returncode, f"{project}: {result.stderr.strip()}"
    return 0, None


def run_retry_pass(
    *,
    attempt: int,
    slugs: list[str],
    projects: list[str],
    batches: Optional[dict[str, list[str]]] = None,
    compose_file: Path,
    tmp_dir: Path,
    override_kwargs: dict,
    console,
    verbose: bool,
    resolve_container_ids: Callable[[list[str]], dict[str, str]],
    wait_for_workers: Callable[..., list[int]],
    register_teardown: Callable[[Callable[[], None]], None],
) -> Optional[PassResult]:
    """Run one retry attempt over ``slugs``. Returns None if it couldn't start.

    ``projects`` is the subset of stacks to use (the caller slices it down to
    ``parallel.retry.workers``). The unused stacks simply idle — they are torn
    down with the rest at the end of the run.

    ``override_kwargs`` is the exact kwargs dict the main pass handed to
    :func:`render_override`; we re-render with ``coord_subdir`` pointed at this
    attempt's private queue so a drifted main-pass setting can never silently
    fail to apply to the retry.

    ``slugs`` is a list of TASK IDS and ``batches`` maps any of them that is an
    exclusion group to its members — re-derived by the caller for this attempt's
    pending set, never inherited, since a retry usually re-runs only part of a
    group. Without it two failed members of one group would run concurrently
    whenever ``parallel.retry.workers`` is above its default of 1.

    The coordinator's ``_resolve_worker_container_ids`` / ``_wait_for_workers``
    / lifecycle hooks are injected rather than imported to keep this module
    free of a circular dependency on ``coordinator``.
    """
    coord_subdir = f"retry-{attempt}"
    coord_root = tmp_dir / coord_subdir
    queue = Queue(root=coord_root)
    if batches:
        queue.seed(slugs, batches=batches)
    else:
        queue.seed(slugs)

    console.print()
    console.rule(f"[bold yellow]Retry pass {attempt}[/bold yellow]")
    console.print(
        f"[dim]re-running {len(slugs)} failed scenario(s) across {len(projects)} worker(s) "
        f"— reduced concurrency isolates contention flakes[/dim]"
    )

    # Re-render one override per participating stack, pointed at this attempt's
    # queue. Worker index must match the project's original index so worker ids
    # stay stable in the summary.
    overrides: list[Path] = []
    for idx, _project in enumerate(projects, start=1):
        kwargs = dict(override_kwargs)
        kwargs["worker_index"] = idx
        kwargs["coord_subdir"] = coord_subdir
        if kwargs.get("auth_mount_by_worker"):
            kwargs["auth_mount"] = kwargs["auth_mount_by_worker"][idx - 1]
        kwargs.pop("auth_mount_by_worker", None)
        path = tmp_dir / f"override-retry{attempt}-w{idx}.yml"
        path.write_text(render_override(**kwargs), encoding="utf-8")
        overrides.append(path)

    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=max(1, len(projects))) as pool:
        futures = [
            pool.submit(_recreate_worker, project=p, compose_file=compose_file, override=o)
            for p, o in zip(projects, overrides)
        ]
        for fut in as_completed(futures):
            code, err = fut.result()
            if code != 0:
                failures.append(err or f"unknown error (exit {code})")
    if failures:
        console.print(f"[yellow]WARNING[/yellow] retry pass could not start: {'; '.join(failures)}")
        return None

    # Fresh bus per pass — see the module docstring; reusing the main pass's bus
    # would overwrite attempt 1's records and double-count the totals.
    raw_channel = RawLogChannel(console=console, verbose=verbose, log_dir=coord_root)
    bus = EventBus(raw_line_sink=raw_channel)
    register_teardown(raw_channel.close)
    for idx in range(1, len(projects) + 1):
        bus.register_worker(f"w{idx}")

    # Fresh tailers too — the main pass's `compose logs -f` died with the
    # container it was following.
    for idx, project in enumerate(projects, start=1):
        tailer = WorkerLogTailer(
            project=project,
            worker_id=f"w{idx}",
            bus=bus,
            app_log_file=tmp_dir / f"w{idx}-retry{attempt}-stack.log",
        )
        tailer.start()
        register_teardown(tailer.stop)

    try:
        container_ids = resolve_container_ids(projects)
    except Exception as e:  # noqa: BLE001 — a retry that can't start is not fatal
        console.print(f"[yellow]WARNING[/yellow] retry pass could not resolve workers: {e}")
        return None

    worker_exits = wait_for_workers(container_ids, console)
    for idx, code in enumerate(worker_exits, start=1):
        bus.mark_worker_finished(f"w{idx}", died=code != 0)

    return PassResult(
        attempt=attempt,
        coord_root=coord_root,
        results=queue.collect_results(),
        orphans=queue.orphaned_running_tasks(),
        bus=bus,
        never_started=set(queue.unclaimed_slugs()),
    )


__all__ = [
    "PassResult",
    "Verdict",
    "classify",
    "log_roots_by_slug",
    "run_retry_pass",
]
