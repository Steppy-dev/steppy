"""Parse worker stdout into structured progress events.

The coordinator's log tailer pushes every line from each worker
container's stdout (``docker compose logs -f``) into
:meth:`EventBus.feed_line`. Lines starting with ``STEPPY-EVT::`` are
parsed into typed events that update an internal state model; every
other line falls through to a :class:`RawLogChannel` that either prints
it with the ``[wN]`` prefix (``--verbose``) or writes it only to disk
for post-mortem.

The state model is the single source of truth for the live renderer:
per-worker current scenario / current test, aggregate pass/fail counts,
failure tails for the final summary.

Event grammar (one per line, fields ``::``-separated):

    STEPPY-EVT::session-start::<count>
    STEPPY-EVT::session-end::<unix_ts>
    STEPPY-EVT::scenario-start::<slug>::<worker_id>::<unix_ts>
    STEPPY-EVT::scenario-end::<slug>::<worker_id>::<exit_code>::<duration_ms>
    STEPPY-EVT::test-start::<nodeid>::<unix_ts>
    STEPPY-EVT::test-end::<nodeid>::<outcome>::<duration_ms>
    STEPPY-EVT::test-fail-tail::<nodeid>::<b64(longrepr_tail)>
    STEPPY-EVT::worker-done::<worker_id>
    STEPPY-EVT::warning::<code>::<message>

``outcome`` ∈ ``passed | failed | skipped | error``. The coordinator may
synthesise two extra outcomes for in-flight tests when the worker
container exits abnormally: ``interrupted`` (Ctrl-C) and
``worker_died``. ``warning`` is a free-form diagnostic surfaced through
the raw-log sink — used when the events plugin can't load, etc.
``worker-done`` is emitted by the worker right before it exits cleanly
(queue drained); the coordinator-side :meth:`EventBus.mark_worker_finished`
remains authoritative for abnormal exits (Ctrl-C, non-zero container
exit, never-emitted).
"""

from __future__ import annotations

import base64
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, TextIO


_EVT_PREFIX = "STEPPY-EVT::"


@dataclass
class TestRecord:
    """One test's bookkeeping. Keyed by ``(worker_id, nodeid)`` in the bus."""

    nodeid: str
    worker_id: str
    scenario_slug: str
    started_at: float
    finished_at: Optional[float] = None
    outcome: Optional[str] = None
    fail_tail: Optional[str] = None


@dataclass
class ScenarioRecord:
    slug: str
    worker_id: str
    started_at: float
    finished_at: Optional[float] = None
    exit_code: Optional[int] = None


@dataclass
class WorkerState:
    worker_id: str
    current_scenario: Optional[str] = None
    current_test: Optional[str] = None
    current_test_started_at: Optional[float] = None
    scenario_started_at: Optional[float] = None
    last_event_at: float = field(default_factory=time.time)
    finished: bool = False


@dataclass
class EventBusSnapshot:
    """Read-only view of the bus state at one instant. Safe to iterate."""

    workers: dict[str, WorkerState]
    counts: dict[str, int]
    tests: list[TestRecord]
    scenarios: list[ScenarioRecord]
    wall_clock_started: float


_TERMINAL_OUTCOMES = ("passed", "failed", "skipped", "error", "interrupted", "worker_died")


class EventBus:
    """Thread-safe parser + state store.

    The bus does not print anything itself. Output is the renderer's job;
    raw (non-event) lines are routed to an optional sink callback so the
    bus stays purely concerned with state.
    """

    def __init__(self, raw_line_sink: Optional[Callable[[str, str], None]] = None):
        self._lock = threading.Lock()
        self._workers: dict[str, WorkerState] = {}
        self._tests: dict[tuple[str, str], TestRecord] = {}
        self._scenarios: dict[tuple[str, str], ScenarioRecord] = {}
        self._raw_sink = raw_line_sink
        self._counts: dict[str, int] = {k: 0 for k in _TERMINAL_OUTCOMES}
        self._started_at = time.time()
        # Subscribers fire after each event apply — used by the milestone
        # renderer to print one line per state change. The callback runs
        # OUTSIDE the bus lock to avoid renderer/bus deadlocks.
        self._subscribers: list[Callable[[str, str, "EventBusSnapshot"], None]] = []

    # ── Public entry points ──────────────────────────────────────────────

    def feed_line(self, worker_id: str, line: str) -> None:
        """Called by the log tailer for every line received from ``worker_id``."""
        line = line.rstrip("\r\n")
        if not line.startswith(_EVT_PREFIX):
            if self._raw_sink is not None:
                self._raw_sink(worker_id, line)
            return
        try:
            kind = self._parse_and_apply(worker_id, line[len(_EVT_PREFIX) :])
        except Exception:
            # Malformed event — fall through to the raw channel so it's
            # visible somewhere instead of silently dropped.
            if self._raw_sink is not None:
                self._raw_sink(worker_id, line)
            return
        if self._subscribers:
            snap = self.snapshot()
            for sub in list(self._subscribers):
                try:
                    sub(worker_id, kind, snap)
                except Exception:
                    pass

    def subscribe(self, fn: Callable[[str, str, "EventBusSnapshot"], None]) -> None:
        """Register a callback fired after each event is applied. The
        callback gets ``(worker_id, kind, snapshot)``. Renderers use this
        to emit milestone lines on every state change.
        """
        self._subscribers.append(fn)

    def snapshot(self) -> EventBusSnapshot:
        """Return a shallow copy of the current state. Renderers must not
        mutate the returned containers."""
        with self._lock:
            return EventBusSnapshot(
                workers=dict(self._workers),
                counts=dict(self._counts),
                tests=list(self._tests.values()),
                scenarios=list(self._scenarios.values()),
                wall_clock_started=self._started_at,
            )

    def register_worker(self, worker_id: str) -> None:
        """Pre-create a worker entry so the renderer shows the row from the
        first frame, even before the first event arrives. Idempotent."""
        with self._lock:
            self._workers.setdefault(worker_id, WorkerState(worker_id=worker_id))

    def mark_worker_finished(self, worker_id: str, *, interrupted: bool = False, died: bool = False) -> None:
        """Coordinator-side reconciliation: the worker container exited.

        ``interrupted=True`` → Ctrl-C path; ``died=True`` → non-zero
        worker container exit while a test was in flight. Either way,
        any dangling in-flight test gets a synthetic outcome so the
        renderer can show the final state correctly.
        """
        with self._lock:
            ws = self._workers.setdefault(worker_id, WorkerState(worker_id=worker_id))
            ws.finished = True
            if ws.current_test is not None:
                key = (worker_id, ws.current_test)
                rec = self._tests.get(key)
                if rec is not None and rec.outcome is None:
                    rec.outcome = "worker_died" if died else "interrupted"
                    rec.finished_at = time.time()
                    self._counts[rec.outcome] += 1
                ws.current_test = None
                ws.current_test_started_at = None

    # ── Parser ───────────────────────────────────────────────────────────

    def _parse_and_apply(self, worker_id: str, body: str) -> str:
        """Parse one event body (already stripped of the ``STEPPY-EVT::``
        prefix) and apply it to the state model. Returns the event kind
        for subscriber dispatch. Raises on malformed input.

        Pytest nodeids contain ``::`` (file::test::param), so events that
        carry a nodeid peel off the trailing fixed-arity fields with
        :py:meth:`str.rpartition` instead of splitting from the left. This
        keeps the nodeid intact regardless of how many ``::`` it contains.
        """
        kind, sep, rest = body.partition("::")
        if not kind:
            raise ValueError("empty event body")
        now = time.time()
        with self._lock:
            ws = self._workers.setdefault(worker_id, WorkerState(worker_id=worker_id))
            ws.last_event_at = now

            if kind == "scenario-start":
                # rest: <slug>::<worker_id>::<unix_ts>  (slugs never contain ::)
                fields = rest.split("::")
                if len(fields) < 3:
                    raise ValueError("scenario-start malformed")
                slug = fields[0]
                started_at = float(fields[2])
                ws.current_scenario = slug
                ws.scenario_started_at = started_at
                self._scenarios[(worker_id, slug)] = ScenarioRecord(
                    slug=slug, worker_id=worker_id, started_at=started_at
                )

            elif kind == "scenario-end":
                # rest: <slug>::<worker_id>::<exit_code>::<duration_ms>
                fields = rest.split("::")
                if len(fields) < 4:
                    raise ValueError("scenario-end malformed")
                slug = fields[0]
                exit_code = int(fields[2])
                rec = self._scenarios.get((worker_id, slug))
                if rec is not None:
                    rec.exit_code = exit_code
                    rec.finished_at = now
                # Self-heal the test counters when the pytest-side events
                # plugin didn't fully report for this scenario. Two real-world
                # failure modes we observed:
                #
                #   1. The plugin didn't load at all (stale conftest pre-dating
                #      ``_steppy_events``). Neither test-start nor test-end
                #      arrives — we have to fabricate a record from scratch.
                #
                #   2. ``test-start`` arrived (the renderer shows the running
                #      test name) but ``test-end`` never did — observed when
                #      pytest's stdout capture intercepts the call-phase
                #      ``pytest_runtest_logreport`` hook, or when buffering
                #      delays the line past container teardown. In this case
                #      a TestRecord exists but its ``outcome`` is None.
                #
                # Handle both: assign a synthetic outcome to every incomplete
                # record for this scenario, and create a fresh one only when
                # nothing existed. Either way the live header reflects the
                # scenario's exit code. Records that already have an outcome
                # are left alone — no double-counting when the plugin works.
                synthetic_outcome = "passed" if exit_code == 0 else "failed"
                pending_for_scenario = [
                    t
                    for t in self._tests.values()
                    if t.worker_id == worker_id and t.scenario_slug == slug and t.outcome is None
                ]
                completed_for_scenario = any(
                    t.worker_id == worker_id and t.scenario_slug == slug and t.outcome is not None
                    for t in self._tests.values()
                )
                for incomplete in pending_for_scenario:
                    incomplete.outcome = synthetic_outcome
                    incomplete.finished_at = now
                    self._counts[synthetic_outcome] += 1
                if not pending_for_scenario and not completed_for_scenario:
                    synthetic_nodeid = f"<synthetic>::{slug}"
                    self._tests[(worker_id, synthetic_nodeid)] = TestRecord(
                        nodeid=synthetic_nodeid,
                        worker_id=worker_id,
                        scenario_slug=slug,
                        started_at=rec.started_at if rec is not None else now,
                        finished_at=now,
                        outcome=synthetic_outcome,
                    )
                    self._counts[synthetic_outcome] += 1
                if ws.current_scenario == slug:
                    ws.current_scenario = None
                    ws.scenario_started_at = None
                    ws.current_test = None
                    ws.current_test_started_at = None

            elif kind == "session-start" or kind == "session-end":
                pass

            elif kind == "worker-done":
                # Worker drained the queue and is about to exit cleanly. Flip
                # the renderer's row to "done" immediately, before the
                # coordinator detects the container exit (~1.5s poll cycle).
                # Handled inline rather than via ``mark_worker_finished`` to
                # avoid re-entering the bus lock. The defensive worker_died
                # synthesis below covers the case where a worker bug emits
                # worker-done while a test is still open; in the happy path
                # the preceding scenario-end already cleared current_test, so
                # this never fires and counts are untouched.
                ws.finished = True
                if ws.current_test is not None:
                    key = (worker_id, ws.current_test)
                    rec = self._tests.get(key)
                    if rec is not None and rec.outcome is None:
                        rec.outcome = "worker_died"
                        rec.finished_at = now
                        self._counts["worker_died"] += 1
                    ws.current_test = None
                    ws.current_test_started_at = None

            elif kind == "warning":
                # rest: <code>::<message>  (free-form, surfaced via raw sink
                # so users can see why something degraded — e.g. the events
                # plugin couldn't load and counts are synthesized instead)
                code, _, message = rest.partition("::")
                if self._raw_sink is not None:
                    label = code or "warning"
                    body = message or "(no detail)"
                    self._raw_sink(worker_id, f"[warning:{label}] {body}")

            elif kind == "test-start":
                # rest: <nodeid>::<unix_ts>  (nodeid may contain ::)
                nodeid, sep2, ts_str = rest.rpartition("::")
                if not sep2:
                    raise ValueError("test-start malformed")
                started_at = float(ts_str)
                ws.current_test = nodeid
                ws.current_test_started_at = started_at
                self._tests[(worker_id, nodeid)] = TestRecord(
                    nodeid=nodeid,
                    worker_id=worker_id,
                    scenario_slug=ws.current_scenario or "",
                    started_at=started_at,
                )

            elif kind == "test-end":
                # rest: <nodeid>::<outcome>::<duration_ms>
                head, sep2, _duration = rest.rpartition("::")
                nodeid, sep3, outcome = head.rpartition("::")
                if not (sep2 and sep3):
                    raise ValueError("test-end malformed")
                rec = self._tests.get((worker_id, nodeid))
                if rec is None:
                    rec = TestRecord(
                        nodeid=nodeid,
                        worker_id=worker_id,
                        scenario_slug=ws.current_scenario or "",
                        started_at=now,
                    )
                    self._tests[(worker_id, nodeid)] = rec
                rec.outcome = outcome
                rec.finished_at = now
                if outcome in self._counts:
                    self._counts[outcome] += 1
                if ws.current_test == nodeid:
                    ws.current_test = None
                    ws.current_test_started_at = None

            elif kind == "test-fail-tail":
                # rest: <nodeid>::<b64>
                nodeid, sep2, b64 = rest.rpartition("::")
                if not sep2:
                    raise ValueError("test-fail-tail malformed")
                try:
                    tail = base64.b64decode(b64).decode("utf-8", errors="replace")
                except Exception:
                    tail = ""
                rec = self._tests.get((worker_id, nodeid))
                if rec is not None:
                    rec.fail_tail = tail

            else:
                raise ValueError(f"unknown event kind: {kind}")
        return kind


class RawLogChannel:
    """Sink for non-event lines from worker stdout.

    Always writes each line to a per-worker file under ``log_dir`` for
    post-mortem inspection. When ``verbose`` is True, also prints the
    line to ``console`` with the ``[wN]`` prefix — same UX as the
    pre-refactor tailer.
    """

    def __init__(self, *, console=None, verbose: bool = False, log_dir: Optional[Path] = None):
        self._console = console
        self._verbose = verbose
        self._log_dir = log_dir
        self._files: dict[str, TextIO] = {}
        self._lock = threading.Lock()

    def __call__(self, worker_id: str, line: str) -> None:
        if self._verbose and self._console is not None:
            self._console.print(f"[{worker_id}] {line}", highlight=False, markup=False)
        if self._log_dir is None:
            return
        with self._lock:
            f = self._files.get(worker_id)
            if f is None:
                try:
                    self._log_dir.mkdir(parents=True, exist_ok=True)
                    f = open(self._log_dir / f"{worker_id}-pytest.log", "a", encoding="utf-8", errors="replace")
                    self._files[worker_id] = f
                except OSError:
                    return
        try:
            f.write(line + "\n")
            f.flush()
        except (OSError, ValueError):
            pass

    def close(self) -> None:
        with self._lock:
            for f in self._files.values():
                try:
                    f.close()
                except OSError:
                    pass
            self._files.clear()
