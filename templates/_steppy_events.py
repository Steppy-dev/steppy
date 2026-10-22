"""Pytest plugin: emit ``STEPPY-EVT::*`` events for the parallel coordinator.

Loaded when EITHER of two independent gates is set — the generated
``conftest.py`` opts in behind the same condition, so a plain single-worker run
loads nothing and its pytest output stays byte-identical to the historic
behaviour:

  STEPPY_EMIT_EVENTS=1     stdout lines, parsed live by the coordinator's
                           EventBus (``steppy run --workers N``)
  STEPPY_EVENTS_DIR=<dir>  one JSONL line per event, appended to
                           ``<dir>/<scenario>__<locale>-<layout>.jsonl``
                           (``steppy run --bundle``)

The two sinks are orthogonal: the coordinator wants a live stream it can render,
a results bundle wants a durable per-test record. A parallel run under
``--bundle`` sets both.

Each stdout event is one line prefixed ``STEPPY-EVT::`` for machine parsing.
Every emission uses ``flush=True`` so the coordinator sees events in real time
even when stdout is the container's log driver (no TTY, block-buffered
otherwise).

Event grammar (one per line):

    STEPPY-EVT::session-start::<test_count>
    STEPPY-EVT::test-start::<nodeid>::<unix_ts>
    STEPPY-EVT::test-end::<nodeid>::<outcome>::<duration_ms>
    STEPPY-EVT::test-fail-tail::<nodeid>::<b64(last_15_lines_of_longrepr)>
    STEPPY-EVT::session-end::<unix_ts>

``outcome`` is one of ``passed|failed|skipped|error``. ``error`` covers
setup/teardown failures (pytest's own ``E`` status).

The JSONL sink encodes the SAME vocabulary — same ``kind`` names, same
``outcome`` values — so the two are two encodings of one grammar rather than two
grammars. Four kinds never appear in it (``scenario-start``, ``scenario-end``,
``worker-done``, ``warning``): a single pytest process cannot know them; they
belong to the worker and the coordinator.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time


_EVT_PREFIX = "STEPPY-EVT::"
_FAIL_TAIL_LINES = 15

#: Schema version of one JSONL line. Additive fields are always allowed and
#: readers default what is absent — the same forward-compat rule the screenshot
#: sidecars follow. Bump only on a breaking change.
_JSONL_SCHEMA_VERSION = 1

# Tracks which nodeids have already had a terminal event emitted so we
# don't double-count when teardown also reports. Reset per pytest session
# (module-level state is fine because the worker spawns one pytest
# subprocess per scenario).
_emitted_end: set[str] = set()

# Set by pytest_configure — the pytest CaptureManager plugin. We use it to
# suspend fd-level capture around every emit so the ``STEPPY-EVT::*`` lines
# actually reach the container's stdout (and thus the coordinator's log
# tailer). Without this, pytest's default ``--capture=fd`` swallows our
# output during the ``call`` phase of each test, so ``test-end`` events
# never reach the EventBus and the live "tests done · failed" counters
# stay pinned at 0 — exactly the bug the parallel UI showed.
#
# The JSONL sink needs none of this: it writes to a file, not to stdout.
_capman = None

# nodeid -> (scenario, locale, layout), filled by ``pytest_runtest_setup``.
_ctx: dict = {}

# JSONL paths this process has already opened, so the first write truncates and
# the rest append. A re-run of one scenario (the coordinator's retry pass)
# therefore REPLACES its file rather than stacking a second session onto the
# first — mirroring how ``output/failures/`` is reset per run, and how the
# coverage merge deletes a scenario's rows before re-inserting them.
_opened: set = set()

_events_dir = None


def pytest_configure(config) -> None:
    """Capture pytest's CaptureManager for use in ``_emit``, and resolve the
    JSONL sink directory once."""
    global _capman, _events_dir
    _capman = config.pluginmanager.getplugin("capturemanager")
    raw = (os.environ.get("STEPPY_EVENTS_DIR") or "").strip()
    _events_dir = raw or None


def _emit(*parts: str) -> None:
    """Write one ``STEPPY-EVT::*`` line, bypassing pytest's stdout capture
    so the coordinator's log tailer actually sees it.

    Falls back to a plain ``print`` if the capture manager isn't available
    (very early hooks, or pytest configured without capture)."""
    if os.environ.get("STEPPY_EMIT_EVENTS") != "1":
        return
    line = _EVT_PREFIX + "::".join(parts)
    if _capman is not None:
        try:
            _capman.suspend_global_capture(in_=False)
            try:
                sys.stdout.write(line + "\n")
                sys.stdout.flush()
            finally:
                _capman.resume_global_capture()
            return
        except Exception:
            # Fall through to plain print — better degraded than silent.
            pass
    print(line, flush=True)


def _context(nodeid: str):
    """``(scenario, locale, layout)`` for a nodeid — ``(None, None, None)`` when
    the setup hook never saw it (e.g. a collection error)."""
    return _ctx.get(nodeid, (None, None, None))


def _jsonl_path(scenario, locale, layout) -> str:
    """``<events_dir>/<scenario>__<locale>-<layout>.jsonl``.

    ``default`` substitutes for an unset dimension — deliberately the SAME
    convention as the two other writers that face the same problem: the pytest
    param id (``_locale_layout_id`` in conftest.py) and the coverage record
    suffix. Three writers, one convention.
    """
    stem = "{}__{}-{}".format(scenario or "unknown", locale or "default", layout or "default")
    return os.path.join(_events_dir, stem + ".jsonl")


def _record(kind: str, nodeid=None, **fields) -> None:
    """Append one JSONL line. Never raises — a broken sink must not fail a run.

    Silence is deliberate rather than lazy: this runs inside every test of a
    long suite, and a per-line warning on a full disk would bury the pytest
    output it is meant to describe. ``steppy bundle create`` is where the
    absence becomes visible, and it says so loudly.
    """
    if not _events_dir:
        return
    try:
        scenario, locale, layout = _context(nodeid) if nodeid else (None, None, None)
        payload = {
            "v": _JSONL_SCHEMA_VERSION,
            "kind": kind,
            "ts": round(time.time(), 3),
            "scenario": scenario,
            "locale": locale,
            "layout": layout,
        }
        if nodeid is not None:
            payload["nodeid"] = nodeid
        worker = os.environ.get("STEPPY_WORKER_ID")
        if worker:
            payload["worker"] = worker
        payload.update(fields)

        path = _jsonl_path(scenario, locale, layout)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        mode = "a" if path in _opened else "w"
        _opened.add(path)
        with open(path, mode, encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
    except Exception:
        pass


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8", errors="replace")).decode("ascii")


def _tail(text: str, n: int = _FAIL_TAIL_LINES) -> str:
    if not text:
        return ""
    return "\n".join(text.splitlines()[-n:])


def _emit_end(nodeid: str, outcome: str, report) -> None:
    duration_ms = int((getattr(report, "duration", 0.0) or 0.0) * 1000)
    _emit("test-end", nodeid, outcome, str(duration_ms))
    _record("test-end", nodeid, outcome=outcome, duration_ms=duration_ms)
    if outcome in ("failed", "error") and getattr(report, "longrepr", None) is not None:
        tail_b64 = _b64(_tail(str(report.longrepr)))
        _emit("test-fail-tail", nodeid, tail_b64)
        _record("test-fail-tail", nodeid, tail_b64=tail_b64)


def pytest_collection_finish(session) -> None:
    # Not mirrored into the JSONL sink: its files are keyed by
    # ``(scenario, locale, layout)`` and a session spans several of them, so a
    # session marker has no single home. The bundle needs per-test rows, and
    # ``session-start``'s count is a live-progress concern.
    try:
        count = len(session.items)
    except Exception:
        count = 0
    _emit("session-start", str(count))


def pytest_runtest_protocol(item, nextitem):
    """Record ``(scenario, locale, layout)`` for this test, from the item itself.
    Always returns ``None`` so pytest runs its own protocol as usual.

    This exists so the JSONL sink never has to parse a nodeid, which would be
    wrong rather than merely awkward: a locale like ``fr-BE`` contains the same
    separator the id joins on. ``callspec.params`` carries the ``(locale,
    layout)`` tuple verbatim — once per role page the test requests, all equal,
    because ``pytest_generate_tests`` binds them in lockstep — and
    ``module.SCENARIO`` is the slug the generator baked in. Both exact.

    Hook choice is load-bearing, twice over. It must be ``runtest_protocol``
    rather than ``runtest_setup``, because ``pytest_runtest_logstart`` — which
    writes the ``test-start`` line — fires BEFORE setup, and would otherwise
    file that line under an unknown scenario. And it must not be a name the
    generated conftest also defines, since the conftest pulls these hook
    functions into its own namespace BY NAME and would silently shadow itself
    (it owns ``pytest_generate_tests``, ``pytest_collection_modifyitems`` and
    ``pytest_runtest_makereport``). Guard: ``tests/test_bundle_events_sink.py``.
    """
    if not _events_dir:
        return None
    scenario = locale = layout = None
    try:
        scenario = getattr(item.module, "SCENARIO", None)
    except Exception:
        pass
    try:
        callspec = getattr(item, "callspec", None)
        if callspec is not None:
            for value in callspec.params.values():
                if isinstance(value, tuple) and len(value) == 2:
                    locale, layout = value
                    break
    except Exception:
        pass
    _ctx[item.nodeid] = (scenario, locale, layout)
    return None


def pytest_runtest_logstart(nodeid, location) -> None:
    _emit("test-start", nodeid, f"{time.time():.3f}")
    _record("test-start", nodeid)


def pytest_runtest_logreport(report) -> None:
    nodeid = report.nodeid

    # If we already emitted a terminal event for this nodeid, only surface
    # an additional fail-tail when teardown itself blew up — useful for
    # debugging fixture cleanup errors. We don't upgrade the outcome.
    if nodeid in _emitted_end:
        if report.when == "teardown" and report.failed and report.longrepr is not None:
            tail_b64 = _b64(_tail(str(report.longrepr)))
            _emit("test-fail-tail", nodeid, tail_b64)
            _record("test-fail-tail", nodeid, tail_b64=tail_b64, phase="teardown")
        return

    if report.when == "setup":
        if report.failed:
            _emit_end(nodeid, "error", report)
            _emitted_end.add(nodeid)
        elif report.skipped:
            _emit_end(nodeid, "skipped", report)
            _emitted_end.add(nodeid)
        # passed setup → wait for the call phase
        return

    if report.when == "call":
        # outcome ∈ {passed, failed, skipped}
        _emit_end(nodeid, report.outcome, report)
        _emitted_end.add(nodeid)
        return

    # teardown phase, terminal not yet emitted (rare: setup passed, no call
    # report was produced). Surface teardown failure as error.
    if report.when == "teardown" and report.failed:
        _emit_end(nodeid, "error", report)
        _emitted_end.add(nodeid)


def pytest_sessionfinish(session, exitstatus) -> None:
    # Not mirrored into the JSONL sink — see ``pytest_collection_finish``.
    _emit("session-end", f"{time.time():.3f}")
