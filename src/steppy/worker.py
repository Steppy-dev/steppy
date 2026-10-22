"""``steppy worker`` subcommand body. Runs INSIDE a worker container.

The coordinator's compose override sets these env vars before launching us:

  STEPPY_WORKER_ID         e.g. "w1"
  STEPPY_RUN_ID            short uuid hex of the parent run
  STEPPY_COORD_DIR         /steppy/coord  (shared mount; queue/running/results live here)
  STEPPY_BASE_URL_OVERRIDE JSON {role: internal_url} — read by generated conftest
  STEPPY_LOCALES, STEPPY_LAYOUTS, STEPPY_GUIDE_MODE,
  STEPPY_NO_ASSERTS, STEPPY_SCREENSHOTS — pass-through, semantics identical to
                                          the host-side single-worker run

Loop: claim a task from the shared queue, invoke pytest on each of its scenarios'
test files in turn, write a result per scenario, repeat. Exit 0 when the queue
empties. A task is one scenario unless it is an exclusion group, in which case
running its members here — sequentially, in one worker — IS the exclusion
guarantee; nothing else enforces it at run time.

The pytest invocation here intentionally MIRRORS the one in ``cli.run``
(cli.py:702) — same ``sys.executable -m pytest <file> --browser chromium``
shape — so per-scenario behavior is identical to a single-worker run. The
duplication is deliberate: it keeps the existing path in cli.py completely
untouched, which is the v0.1 sacred invariant.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from ._parallel.queue import Queue, ScenarioResult, TaskPayloadError


def _env_or_die(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"steppy worker: required env var {name} is unset", file=sys.stderr)
        sys.exit(2)
    return value


def _emit_event(*parts: str) -> None:
    """Print one ``STEPPY-EVT::*`` line to stdout, flushed immediately.

    The coordinator's EventBus parses these as scenario-level milestones —
    visible even when the per-test pytest plugin can't run (e.g. the test
    file is missing). Inert in single-worker mode because nothing parses
    these lines outside the coordinator.
    """
    print("STEPPY-EVT::" + "::".join(parts), flush=True)


def _rerunfailures_available() -> bool:
    """True when pytest-rerunfailures (the ``steppy-dev[retry]`` extra) is importable."""
    import importlib.util

    return importlib.util.find_spec("pytest_rerunfailures") is not None


def _build_pytest_argv(
    test_file: Path,
    emit_events: bool,
    env: Optional[dict] = None,
    slug: Optional[str] = None,
) -> list[str]:
    """Build the per-scenario pytest argv.

    When ``emit_events`` is True (parallel mode — STEPPY_EMIT_EVENTS=1),
    append ``-q --tb=short`` so raw pytest output is minimal and the
    coordinator's renderer drives the visible signal via the structured
    events plugin. Also disable the cache provider — the project root is
    bind-mounted read-only into the worker container, and pytest's
    cacheprovider would otherwise emit a ``PytestCacheWarning: cache
    could not write path /steppy/project/.pytest_cache/...`` for every
    scenario.

    When False, keep the argv byte-identical to ``cli.run``'s
    single-worker invocation (guarded by
    ``tests/test_run_byte_identical.py``).

    ``env`` carries the two report settings that have to reach pytest as
    **argv** rather than env vars (``STEPPY_TRACE``/``STEPPY_VIDEO`` are read
    by the generated conftest and need nothing here):

      STEPPY_RETRIES        → ``--reruns=N`` (pytest-rerunfailures). Mirrors
                              ``cli._apply_report_args``: warn and skip when the
                              plugin is missing rather than failing the run.
      STEPPY_PYTEST_EXTRA   → the user's ``--`` passthrough argv, shlex-encoded
                              by ``compose.render_override``.
      STEPPY_JUNIT_DIR      → ``--junitxml=<dir>/<slug>.xml`` (``--bundle``).
                              Per SCENARIO, not per worker: N workers share one
                              ``output/`` mount, so a single fixed path would
                              leave only the last finisher's report — which is
                              exactly what the ``-- --junitxml=…`` passthrough
                              does today. The slug is unique across the run by
                              construction (it is the queue's unit of work), a
                              retry overwrites only its own file, and a MISSING
                              ``<slug>.xml`` is itself a precise signal that
                              pytest died before collection.

    All are absent on the single-worker path, so the argv stays unchanged.
    """
    argv = [sys.executable, "-m", "pytest", str(test_file), "--browser", "chromium"]
    if emit_events:
        argv.extend(["-q", "--tb=short", "-p", "no:cacheprovider"])
    env = env if env is not None else {}
    junit_dir = env.get("STEPPY_JUNIT_DIR", "").strip()
    if junit_dir and slug:
        try:
            Path(junit_dir).mkdir(parents=True, exist_ok=True)
            argv.append(f"--junitxml={Path(junit_dir) / f'{slug}.xml'}")
        except OSError as e:
            print(f"steppy worker: cannot write JUnit XML to {junit_dir}: {e}", file=sys.stderr, flush=True)
    retries = env.get("STEPPY_RETRIES", "").strip()
    if retries and retries != "0":
        if _rerunfailures_available():
            argv.append(f"--reruns={retries}")
        else:
            print(
                "steppy worker: STEPPY_RETRIES is set but pytest-rerunfailures is not "
                "installed in the worker image; skipping retries.",
                file=sys.stderr,
                flush=True,
            )
    extra = env.get("STEPPY_PYTEST_EXTRA", "").strip()
    if extra:
        argv.extend(shlex.split(extra))
    return argv


def _run_one_scenario(
    slug: str,
    tests_dir: Path,
    env: dict[str, str],
    worker_id: str,
    log_dir: Optional[Path] = None,
) -> tuple[int, float, float, float]:
    """Invoke pytest on a single scenario's test file. Returns
    (exit_code, duration_s, started_at, finished_at).

    Brackets the pytest subprocess in ``scenario-start`` / ``scenario-end``
    events so the coordinator knows scenario boundaries even when the
    inner pytest hooks never fire (missing test file, pytest crash before
    collection).

    When ``log_dir`` is provided, pytest's combined stdout/stderr is tee'd
    to ``log_dir / f"{slug}.log"`` in addition to flowing through this
    process's stdout (which the coordinator tails via
    ``docker compose logs -f``). This is what gives the coordinator
    something to copy into ``output/failures/<slug>/pytest.log`` after a
    failure — without it, per-scenario log lines are jumbled with every
    other scenario this worker ran inside the same shared
    ``w{i}-stack.log`` file."""
    emit_events = env.get("STEPPY_EMIT_EVENTS") == "1"
    test_file = tests_dir / f"test_{slug}.py"
    started = time.time()
    if emit_events:
        _emit_event("scenario-start", slug, worker_id, f"{started:.3f}")
    if not test_file.exists():
        # Missing test file → hard scenario failure, surfaced by the
        # coordinator. Usually means `steppy generate` wasn't run before
        # `steppy run --workers N`.
        finished = time.time()
        if emit_events:
            _emit_event("scenario-end", slug, worker_id, "99", "0")
        return 99, 0.0, started, finished
    argv = _build_pytest_argv(test_file, emit_events, env, slug=slug)
    if log_dir is None:
        # No teeing — preserve the byte-identical subprocess.run path used
        # by tests and any consumer that wants the unchanged shape.
        result_code = subprocess.run(argv, env=env).returncode
    else:
        result_code = _run_pytest_with_tee(argv, env, log_dir / f"{slug}.log")
    finished = time.time()
    if emit_events:
        duration_ms = int((finished - started) * 1000)
        _emit_event("scenario-end", slug, worker_id, str(result_code), str(duration_ms))
    return result_code, finished - started, started, finished


def _run_pytest_with_tee(argv: list[str], env: dict[str, str], log_path: Path) -> int:
    """Run pytest, copying every line of its combined stdout/stderr to
    both ``sys.stdout`` (so STEPPY-EVT lines still reach the coordinator's
    EventBus via ``docker compose logs -f``) and ``log_path`` (so the
    coordinator can persist it under ``output/failures/<slug>/`` on
    failure). Returns pytest's exit code."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        argv,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.stdout is not None
    try:
        with log_path.open("w", encoding="utf-8") as f:
            for line in proc.stdout:
                sys.stdout.write(line)
                sys.stdout.flush()
                f.write(line)
    finally:
        proc.stdout.close()
    return proc.wait()


def worker_main() -> int:
    """Entry point for ``steppy worker``. Returns the process exit code.

    Exit semantics:
      0 — queue drained cleanly, all claimed scenarios produced a result
      1 — at least one scenario reported a non-zero pytest exit code
      2 — fatal misconfig (missing required env var)
    """
    worker_id = _env_or_die("STEPPY_WORKER_ID")
    coord_dir = Path(_env_or_die("STEPPY_COORD_DIR"))

    # Inside the container, the project is mounted at /steppy/project.
    # working_dir is set to that in the compose override, so cwd-relative
    # paths work for pytest the same way they do on the host single-worker
    # path.
    project_root = Path.cwd()
    tests_dir = project_root / "tests" / "e2e"

    queue = Queue(root=coord_dir)

    # Per-scenario pytest logs land here. Bind-mounted to the coordinator's
    # tmp_dir, so the host can copy each failed scenario's log into
    # output/failures/<slug>/pytest.log before tmp_dir is torn down.
    log_dir = coord_dir / "logs" / worker_id
    log_dir.mkdir(parents=True, exist_ok=True)

    any_failed = False
    emit_events = os.environ.get("STEPPY_EMIT_EVENTS") == "1"
    print(f"[{worker_id}] worker started; coord dir: {coord_dir}", flush=True)

    while True:
        try:
            task = queue.claim_task(worker_id)
        except TaskPayloadError as e:
            # The claim already succeeded, so this task is now claimed with no
            # results: its members surface as orphans and the retry pass picks
            # them up. One unreadable marker must not cost the rest of the queue.
            print(f"[{worker_id}] {e}", file=sys.stderr, flush=True)
            if emit_events:
                _emit_event("warning", "task-payload-unreadable", e.task_id)
            any_failed = True
            continue
        if task is None:
            # Emit BEFORE the prose line so the EventBus flips the renderer
            # row to "done" before the raw "queue empty" line shows up in
            # verbose mode. Gated on STEPPY_EMIT_EVENTS so the single-worker
            # path (which doesn't set this env) never emits stray events.
            if emit_events:
                _emit_event("worker-done", worker_id)
            print(f"[{worker_id}] queue empty — exiting", flush=True)
            break
        if task.group is None:
            print(f"[{worker_id}] claimed: {task.task_id}", flush=True)
        else:
            print(
                f"[{worker_id}] claimed exclusion group '{task.group}' "
                f"({len(task.slugs)} scenarios: {', '.join(task.slugs)})",
                flush=True,
            )
        # One pytest process per scenario, exactly as for an ungrouped task —
        # so results, logs, JUnit and the scenario-start/end events stay keyed
        # per scenario, and a crash mid-batch reduces to "which member has no
        # result file". Running the members is what serialises them: this loop
        # is the whole of the exclusion guarantee at run time.
        for slug in task.slugs:
            exit_code, duration, started_at, finished_at = _run_one_scenario(
                slug,
                tests_dir,
                dict(os.environ),
                worker_id,
                log_dir=log_dir,
            )
            result = ScenarioResult(
                slug=slug,
                worker_id=worker_id,
                exit_code=exit_code,
                duration_s=duration,
                started_at=started_at,
                finished_at=finished_at,
            )
            queue.write_result(result)
            status = "PASS" if exit_code == 0 else f"FAIL(code={exit_code})"
            print(f"[{worker_id}] {status} {slug} ({duration:.1f}s)", flush=True)
            if exit_code != 0:
                any_failed = True

    return 1 if any_failed else 0
