"""Unit tests for the ``_steppy_events`` pytest plugin.

The plugin is a plain ``.py`` file living in the canonical repo-root
``templates/`` tree (the bundled ``src/steppy/templates/`` copy is generated
from it at build time — FLAWS §4.1). ``steppy generate`` copies it into
``tests/e2e/`` next to conftest. We load it directly from the canonical
location to test the event-emission logic in isolation — no need to spin up
pytest.
"""

from __future__ import annotations

import base64
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


_PLUGIN_PATH = Path(__file__).parent.parent / "templates" / "_steppy_events.py"


@pytest.fixture
def plugin(monkeypatch):
    """Load the plugin into a fresh module namespace per test so module-level
    state (``_emitted_end``) is reset.

    ``STEPPY_EMIT_EVENTS=1`` because the plugin now has two independent sinks —
    stdout for the coordinator, JSONL for the results bundle — each with its own
    env gate. These tests are about the stdout one; the JSONL sink is covered by
    ``tests/test_bundle_events_sink.py``."""
    monkeypatch.setenv("STEPPY_EMIT_EVENTS", "1")
    monkeypatch.delenv("STEPPY_EVENTS_DIR", raising=False)
    spec = importlib.util.spec_from_file_location("_steppy_events_under_test", _PLUGIN_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _evt_lines(capsys) -> list[str]:
    out = capsys.readouterr().out
    return [line for line in out.splitlines() if line.startswith("STEPPY-EVT::")]


def test_session_start_emits_count(plugin, capsys):
    session = SimpleNamespace(items=[1, 2, 3, 4, 5])
    plugin.pytest_collection_finish(session)
    lines = _evt_lines(capsys)
    assert lines == ["STEPPY-EVT::session-start::5"]


def test_test_start_emits_nodeid_and_timestamp(plugin, capsys):
    plugin.pytest_runtest_logstart("tests/e2e/test_foo.py::test_bar", ("file", 1, "test_bar"))
    lines = _evt_lines(capsys)
    assert len(lines) == 1
    parts = lines[0].split("::")
    assert parts[:3] == ["STEPPY-EVT", "test-start", "tests/e2e/test_foo.py"]
    # nodeid is split on "::" too — assert the full nodeid round-trips.
    rejoined = "::".join(parts[2:-1])
    assert rejoined == "tests/e2e/test_foo.py::test_bar"
    # Last segment is the unix_ts (float-parseable).
    float(parts[-1])


def test_call_phase_passed_emits_test_end(plugin, capsys):
    report = SimpleNamespace(
        nodeid="t::test_ok",
        when="call",
        outcome="passed",
        failed=False,
        skipped=False,
        duration=0.42,
        longrepr=None,
    )
    plugin.pytest_runtest_logreport(report)
    lines = _evt_lines(capsys)
    assert lines == ["STEPPY-EVT::test-end::t::test_ok::passed::420"]


def test_call_phase_failed_emits_fail_tail(plugin, capsys):
    longrepr = "line1\nline2\nE   AssertionError: nope"
    report = SimpleNamespace(
        nodeid="t::test_fail",
        when="call",
        outcome="failed",
        failed=True,
        skipped=False,
        duration=1.5,
        longrepr=longrepr,
    )
    plugin.pytest_runtest_logreport(report)
    lines = _evt_lines(capsys)
    assert lines[0] == "STEPPY-EVT::test-end::t::test_fail::failed::1500"
    assert lines[1].startswith("STEPPY-EVT::test-fail-tail::t::test_fail::")
    b64 = lines[1].split("::")[-1]
    decoded = base64.b64decode(b64).decode("utf-8")
    assert decoded == longrepr


def test_call_phase_skipped_emits_skipped_no_tail(plugin, capsys):
    report = SimpleNamespace(
        nodeid="t::test_skip",
        when="call",
        outcome="skipped",
        failed=False,
        skipped=True,
        duration=0.0,
        longrepr=None,
    )
    plugin.pytest_runtest_logreport(report)
    lines = _evt_lines(capsys)
    assert lines == ["STEPPY-EVT::test-end::t::test_skip::skipped::0"]


def test_setup_failure_emits_error(plugin, capsys):
    report = SimpleNamespace(
        nodeid="t::test_setup_fail",
        when="setup",
        outcome="failed",
        failed=True,
        skipped=False,
        duration=0.1,
        longrepr="fixture exploded",
    )
    plugin.pytest_runtest_logreport(report)
    lines = _evt_lines(capsys)
    assert lines[0] == "STEPPY-EVT::test-end::t::test_setup_fail::error::100"
    assert lines[1].startswith("STEPPY-EVT::test-fail-tail::t::test_setup_fail::")


def test_setup_then_call_only_emits_once(plugin, capsys):
    """When setup passes, only the call-phase report should produce
    ``test-end`` (not the setup phase). And the teardown phase must not
    double-emit."""
    nodeid = "t::test_normal"
    setup_pass = SimpleNamespace(
        nodeid=nodeid,
        when="setup",
        outcome="passed",
        failed=False,
        skipped=False,
        duration=0.05,
        longrepr=None,
    )
    call_pass = SimpleNamespace(
        nodeid=nodeid,
        when="call",
        outcome="passed",
        failed=False,
        skipped=False,
        duration=0.5,
        longrepr=None,
    )
    teardown_pass = SimpleNamespace(
        nodeid=nodeid,
        when="teardown",
        outcome="passed",
        failed=False,
        skipped=False,
        duration=0.01,
        longrepr=None,
    )
    plugin.pytest_runtest_logreport(setup_pass)
    plugin.pytest_runtest_logreport(call_pass)
    plugin.pytest_runtest_logreport(teardown_pass)
    lines = _evt_lines(capsys)
    test_ends = [line for line in lines if "::test-end::" in line]
    assert len(test_ends) == 1
    assert "::passed::" in test_ends[0]


def test_teardown_failure_after_passed_call_adds_fail_tail(plugin, capsys):
    """Teardown blowing up after a passed call shouldn't change the
    test's outcome (the test itself passed), but the fixture failure
    should surface as an additional ``test-fail-tail`` so the user can
    debug it."""
    nodeid = "t::test_pass_teardown_fail"
    plugin.pytest_runtest_logreport(
        SimpleNamespace(
            nodeid=nodeid,
            when="call",
            outcome="passed",
            failed=False,
            skipped=False,
            duration=0.1,
            longrepr=None,
        )
    )
    plugin.pytest_runtest_logreport(
        SimpleNamespace(
            nodeid=nodeid,
            when="teardown",
            outcome="failed",
            failed=True,
            skipped=False,
            duration=0.05,
            longrepr="cleanup blew up",
        )
    )
    lines = _evt_lines(capsys)
    assert lines[0] == f"STEPPY-EVT::test-end::{nodeid}::passed::100"
    # No duplicate test-end — only the fail-tail for the teardown failure.
    assert sum(1 for line in lines if "::test-end::" in line) == 1
    assert lines[-1].startswith(f"STEPPY-EVT::test-fail-tail::{nodeid}::")


def test_session_end_emits_timestamp(plugin, capsys):
    plugin.pytest_sessionfinish(session=None, exitstatus=0)
    lines = _evt_lines(capsys)
    assert len(lines) == 1
    parts = lines[0].split("::")
    assert parts[1] == "session-end"
    float(parts[2])


def test_configure_stashes_capturemanager(plugin):
    """``pytest_configure`` must grab the capturemanager so ``_emit`` can
    suspend pytest's fd-level capture before printing. Without this the
    ``test-end`` events emitted from ``pytest_runtest_logreport`` get
    swallowed by ``--capture=fd`` and the live counters stay at 0."""
    sentinel = object()
    pluginmanager = SimpleNamespace(getplugin=lambda name: sentinel if name == "capturemanager" else None)
    config = SimpleNamespace(pluginmanager=pluginmanager)
    plugin.pytest_configure(config)
    assert plugin._capman is sentinel


def test_emit_suspends_capture_before_writing(plugin, capsys):
    """When ``_capman`` is set, ``_emit`` must call ``suspend_global_capture``
    before writing and ``resume_global_capture`` after — even if the write
    itself fails. This bypasses pytest's fd-level capture so the event
    line actually reaches the worker container's stdout."""
    calls: list[str] = []

    class _FakeCapman:
        def suspend_global_capture(self, in_=False):
            calls.append(f"suspend(in_={in_})")

        def resume_global_capture(self):
            calls.append("resume")

    plugin._capman = _FakeCapman()
    plugin._emit("test-end", "nid", "passed", "100")
    # Suspend must precede the write; resume must follow.
    assert calls == ["suspend(in_=False)", "resume"]
    lines = _evt_lines(capsys)
    assert lines == ["STEPPY-EVT::test-end::nid::passed::100"]


def test_emit_falls_back_to_plain_print_when_capman_fails(plugin, capsys):
    """If suspend/resume raises, ``_emit`` must still produce the event
    line via fallback ``print``. Better degraded than silent."""

    class _BrokenCapman:
        def suspend_global_capture(self, in_=False):
            raise RuntimeError("boom")

        def resume_global_capture(self):
            raise RuntimeError("also boom")

    plugin._capman = _BrokenCapman()
    plugin._emit("test-end", "nid", "passed", "100")
    lines = _evt_lines(capsys)
    assert lines == ["STEPPY-EVT::test-end::nid::passed::100"]


def test_fail_tail_is_clipped_to_15_lines(plugin, capsys):
    longrepr = "\n".join(f"line{i}" for i in range(30))
    plugin.pytest_runtest_logreport(
        SimpleNamespace(
            nodeid="t::test_long_traceback",
            when="call",
            outcome="failed",
            failed=True,
            skipped=False,
            duration=0.1,
            longrepr=longrepr,
        )
    )
    lines = _evt_lines(capsys)
    fail_tail = [line for line in lines if "::test-fail-tail::" in line][0]
    decoded = base64.b64decode(fail_tail.split("::")[-1]).decode("utf-8")
    decoded_lines = decoded.splitlines()
    assert len(decoded_lines) == 15
    assert decoded_lines[0] == "line15"
    assert decoded_lines[-1] == "line29"
