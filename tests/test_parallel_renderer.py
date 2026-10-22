"""Tests for the parallel-mode progress renderers and final summary."""

from __future__ import annotations

import base64
import io
from pathlib import Path

from rich.console import Console

from steppy._parallel.event_bus import EventBus
from steppy._parallel.queue import ScenarioResult
from steppy._parallel.renderer import (
    LiveProgressRenderer,
    MilestoneRenderer,
    make_renderer,
    render_summary,
)


def _capture_console() -> tuple[Console, io.StringIO]:
    """A Rich console that writes to a StringIO with ``force_terminal=False``
    so its output stays plain text — no ANSI escapes — and any
    ``Live`` paths take the non-TTY branch."""
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120, no_color=True)
    return console, buf


def test_make_renderer_returns_milestone_when_not_terminal():
    console, _ = _capture_console()
    bus = EventBus()
    renderer = make_renderer(bus=bus, console=console, total_scenarios=4)
    assert isinstance(renderer, MilestoneRenderer)


def test_milestone_prints_scenario_start_and_end():
    console, buf = _capture_console()
    bus = EventBus()
    bus.register_worker("w1")
    MilestoneRenderer(bus=bus, console=console, total_scenarios=2)
    bus.feed_line("w1", "STEPPY-EVT::scenario-start::login::w1::1.0")
    bus.feed_line("w1", "STEPPY-EVT::scenario-end::login::w1::0::5000")
    out = buf.getvalue()
    assert "w1 START scenario login" in out
    assert "w1 PASS scenario login" in out


def test_milestone_marks_failed_scenario_with_exit_code():
    console, buf = _capture_console()
    bus = EventBus()
    bus.register_worker("w1")
    MilestoneRenderer(bus=bus, console=console, total_scenarios=1)
    bus.feed_line("w1", "STEPPY-EVT::scenario-start::checkout::w1::1.0")
    bus.feed_line("w1", "STEPPY-EVT::scenario-end::checkout::w1::1::3000")
    out = buf.getvalue()
    assert "w1 FAIL(exit=1) scenario checkout" in out


def test_milestone_prints_per_test_outcome():
    console, buf = _capture_console()
    bus = EventBus()
    bus.register_worker("w1")
    MilestoneRenderer(bus=bus, console=console, total_scenarios=1)
    bus.feed_line("w1", "STEPPY-EVT::scenario-start::s::w1::1.0")
    bus.feed_line("w1", "STEPPY-EVT::test-start::tests/e2e/test_a.py::test_a[fr]::1.5")
    bus.feed_line("w1", "STEPPY-EVT::test-end::tests/e2e/test_a.py::test_a[fr]::passed::500")
    out = buf.getvalue()
    # The file-prefix is stripped; only the short nodeid is shown.
    assert "PASSED test_a[fr]" in out
    assert "0 failed" in out


def test_live_renderer_starts_and_stops_cleanly():
    """Smoke test: instantiate + start + stop without exceptions."""
    console, _ = _capture_console()
    bus = EventBus()
    bus.register_worker("w1")
    renderer = LiveProgressRenderer(bus=bus, console=console, total_scenarios=1)
    renderer.start()
    bus.feed_line("w1", "STEPPY-EVT::scenario-start::s::w1::1.0")
    renderer.stop()


def test_render_summary_lists_passed_failed_counts():
    console, buf = _capture_console()
    bus = EventBus()
    # Two passes, one fail.
    bus.feed_line("w1", "STEPPY-EVT::test-end::a::passed::500")
    bus.feed_line("w1", "STEPPY-EVT::test-end::b::passed::600")
    bus.feed_line("w2", "STEPPY-EVT::test-end::c::failed::1200")
    results = [
        ScenarioResult("scen-a", "w1", 0, 0.5, 0.0, 0.5),
        ScenarioResult("scen-b", "w1", 0, 0.6, 0.6, 1.2),
        ScenarioResult("scen-c", "w2", 1, 1.2, 0.0, 1.2),
    ]
    render_summary(
        console=console,
        bus=bus,
        scenario_results=results,
        orphans={},
        tmp_dir=Path("/tmp/run-xyz"),
    )
    out = buf.getvalue()
    assert "Run summary" in out
    assert "2 passed" in out  # scenarios passed
    assert "1 failed" in out
    assert "Failures" in out
    assert "scen-c" in out


def test_render_summary_includes_decoded_fail_tail():
    console, buf = _capture_console()
    bus = EventBus()
    longrepr = "E   AssertionError: page title was 'Sign in' not 'Welcome'"
    b64 = base64.b64encode(longrepr.encode("utf-8")).decode("ascii")
    bus.feed_line("w1", "STEPPY-EVT::scenario-start::login::w1::1.0")
    bus.feed_line("w1", "STEPPY-EVT::test-start::tests/e2e/test_login.py::test_login[fr]::1.0")
    bus.feed_line("w1", "STEPPY-EVT::test-end::tests/e2e/test_login.py::test_login[fr]::failed::3000")
    bus.feed_line("w1", f"STEPPY-EVT::test-fail-tail::tests/e2e/test_login.py::test_login[fr]::{b64}")
    bus.feed_line("w1", "STEPPY-EVT::scenario-end::login::w1::1::3500")
    render_summary(
        console=console,
        bus=bus,
        scenario_results=[ScenarioResult("login", "w1", 1, 3.5, 0.0, 3.5)],
        orphans={},
        tmp_dir=Path("/tmp/run-xyz"),
    )
    out = buf.getvalue()
    assert "AssertionError" in out
    assert "Sign in" in out


def test_render_summary_lists_orphans():
    console, buf = _capture_console()
    bus = EventBus()
    render_summary(
        console=console,
        bus=bus,
        scenario_results=[],
        orphans={"w2": ["abandoned-scen-a", "abandoned-scen-b"]},
        tmp_dir=Path("/tmp/run-xyz"),
    )
    out = buf.getvalue()
    assert "Orphaned" in out
    assert "abandoned-scen-a" in out
    assert "abandoned-scen-b" in out


def test_render_summary_points_at_persistent_artifacts_when_project_root_given(tmp_path):
    """When ``project_root`` is supplied, failure rows must reference
    ``output/failures/<slug>/`` (which survives tmp_dir cleanup), not
    the per-worker stack log under tmp_dir."""
    console, buf = _capture_console()
    bus = EventBus()
    bus.feed_line("w1", "STEPPY-EVT::test-end::t::failed::1000")
    render_summary(
        console=console,
        bus=bus,
        scenario_results=[ScenarioResult("login", "w1", 1, 1.0, 0.0, 1.0)],
        orphans={"w2": ["abandoned"]},
        tmp_dir=Path("/tmp/run-xyz"),
        project_root=tmp_path,
    )
    out = buf.getvalue()
    # Failed scenario points at persistent artifacts dir.
    assert str(tmp_path / "output" / "failures" / "login") in out
    # Orphan also points at its persistent artifacts dir.
    assert str(tmp_path / "output" / "failures" / "abandoned") in out
    # The pre-change tmp_dir stack-log line is gone.
    assert "w1-stack.log" not in out
    # Banner note is printed.
    assert "These artifacts persist after the run" in out


def test_render_summary_falls_back_to_tmp_dir_when_no_project_root():
    """Pre-feature behavior preserved when project_root is None: the
    failure row references the tmp_dir stack log."""
    console, buf = _capture_console()
    bus = EventBus()
    bus.feed_line("w1", "STEPPY-EVT::test-end::t::failed::1000")
    render_summary(
        console=console,
        bus=bus,
        scenario_results=[ScenarioResult("login", "w1", 1, 1.0, 0.0, 1.0)],
        orphans={},
        tmp_dir=Path("/tmp/run-xyz"),
    )
    out = buf.getvalue()
    assert "w1-stack.log" in out
    assert "Failure artifacts persisted" not in out


def test_render_summary_works_with_no_failures():
    console, buf = _capture_console()
    bus = EventBus()
    bus.feed_line("w1", "STEPPY-EVT::test-end::ok::passed::500")
    render_summary(
        console=console,
        bus=bus,
        scenario_results=[ScenarioResult("scen", "w1", 0, 0.5, 0.0, 0.5)],
        orphans={},
        tmp_dir=Path("/tmp/run-xyz"),
    )
    out = buf.getvalue()
    assert "Run summary" in out
    assert "Failures" not in out
    assert "1 passed" in out


# ── Retry pass: FLAKY section + grouped failure causes ──────────────────────


def _failed_bus(worker: str, slug: str, nodeid: str, longrepr: str) -> EventBus:
    """A bus carrying one failed test with a decoded fail tail."""
    bus = EventBus()
    b64 = base64.b64encode(longrepr.encode("utf-8")).decode("ascii")
    bus.feed_line(worker, f"STEPPY-EVT::scenario-start::{slug}::{worker}::1.0")
    bus.feed_line(worker, f"STEPPY-EVT::test-start::{nodeid}::1.0")
    bus.feed_line(worker, f"STEPPY-EVT::test-end::{nodeid}::failed::100")
    bus.feed_line(worker, f"STEPPY-EVT::test-fail-tail::{nodeid}::{b64}")
    return bus


class _Verdict:
    """Stand-in for retry.Verdict — keeps this file free of a retry import."""

    def __init__(self, passed=(), flaky=(), failed=(), attempts=None):
        self.passed = list(passed)
        self.flaky = list(flaky)
        self.failed = list(failed)
        self.attempts = attempts or {}


def test_summary_reports_flaky_separately_from_passed():
    """A flake must not be buried in the pass count (it is the thing worth
    fixing) nor listed as a failure (it ended green)."""
    console, buf = _capture_console()
    render_summary(
        console=console,
        bus=EventBus(),
        scenario_results=[
            ScenarioResult("steady", "w1", 0, 1.0, 0.0, 1.0),
            ScenarioResult("wobbly", "w1", 0, 1.0, 0.0, 1.0),
        ],
        orphans={},
        tmp_dir=Path("/tmp/run"),
        verdict=_Verdict(passed=["steady"], flaky=["wobbly"], attempts={"wobbly": 2}),
    )
    out = buf.getvalue()
    assert "1 passed" in out
    assert "1 flaky" in out
    assert "Flaky (failed, then passed on retry)" in out
    assert "wobbly" in out
    assert "passed on attempt 2" in out
    assert "Failures" not in out


def test_summary_omits_flaky_section_without_a_verdict():
    """No retry pass ⇒ output identical to before the feature."""
    console, buf = _capture_console()
    render_summary(
        console=console,
        bus=EventBus(),
        scenario_results=[ScenarioResult("a", "w1", 0, 1.0, 0.0, 1.0)],
        orphans={},
        tmp_dir=Path("/tmp/run"),
    )
    assert "Flaky" not in buf.getvalue()


def test_summary_excludes_flaky_from_the_failures_block():
    """The merged results say 'passed', but a stale failure row would still
    print it under Failures if the verdict weren't consulted."""
    console, buf = _capture_console()
    render_summary(
        console=console,
        bus=EventBus(),
        scenario_results=[
            ScenarioResult("wobbly", "w1", 1, 1.0, 0.0, 1.0),
            ScenarioResult("broken", "w1", 1, 1.0, 0.0, 1.0),
        ],
        orphans={},
        tmp_dir=Path("/tmp/run"),
        verdict=_Verdict(flaky=["wobbly"], failed=["broken"]),
    )
    out = buf.getvalue()
    failures_block = out[out.index("Failures") :]
    assert "broken" in failures_block
    assert "wobbly" not in failures_block


def test_summary_groups_failures_by_cause():
    """Three scenarios, one cause: the reader should see ×3 once, not three
    copies of the same traceback."""
    console, buf = _capture_console()
    bus = EventBus()
    for i, slug in enumerate(("a", "b", "c")):
        nodeid = f"tests/e2e/test_{slug}.py::test_{slug}"
        b64 = base64.b64encode(
            f"TimeoutError: Timeout {30000 + i}ms exceeded waiting for [data-testid=toast]".encode()
        ).decode("ascii")
        bus.feed_line("w1", f"STEPPY-EVT::scenario-start::{slug}::w1::1.0")
        bus.feed_line("w1", f"STEPPY-EVT::test-start::{nodeid}::1.0")
        bus.feed_line("w1", f"STEPPY-EVT::test-end::{nodeid}::failed::100")
        bus.feed_line("w1", f"STEPPY-EVT::test-fail-tail::{nodeid}::{b64}")
        bus.feed_line("w1", f"STEPPY-EVT::scenario-end::{slug}::w1::1::100")

    render_summary(
        console=console,
        bus=bus,
        scenario_results=[ScenarioResult(s, "w1", 1, 1.0, 0.0, 1.0) for s in ("a", "b", "c")],
        orphans={},
        tmp_dir=Path("/tmp/run"),
    )
    out = buf.getvalue()
    assert "Failure causes" in out
    assert "1 distinct cause" in out
    assert "×3" in out
    # Only the exemplar's tail is printed in full; the other two are elided.
    assert out.count("cause shown above") == 2


def test_verbose_prints_every_tail():
    console, buf = _capture_console()
    bus = EventBus()
    for slug in ("a", "b"):
        nodeid = f"tests/e2e/test_{slug}.py::test_{slug}"
        b64 = base64.b64encode(b"TimeoutError: same everywhere").decode("ascii")
        bus.feed_line("w1", f"STEPPY-EVT::scenario-start::{slug}::w1::1.0")
        bus.feed_line("w1", f"STEPPY-EVT::test-start::{nodeid}::1.0")
        bus.feed_line("w1", f"STEPPY-EVT::test-end::{nodeid}::failed::100")
        bus.feed_line("w1", f"STEPPY-EVT::test-fail-tail::{nodeid}::{b64}")
        bus.feed_line("w1", f"STEPPY-EVT::scenario-end::{slug}::w1::1::100")

    render_summary(
        console=console,
        bus=bus,
        scenario_results=[ScenarioResult(s, "w1", 1, 1.0, 0.0, 1.0) for s in ("a", "b")],
        orphans={},
        tmp_dir=Path("/tmp/run"),
        verbose=True,
    )
    out = buf.getvalue()
    assert "cause shown above" not in out
    assert out.count("TimeoutError: same everywhere") >= 2


def test_summary_harvests_tails_from_retry_buses():
    """The retry pass runs on its OWN bus (a shared one would overwrite
    attempt 1's records). Without threading it through, a retried-and-still-
    failing scenario would print with no detail at all."""
    console, buf = _capture_console()
    retry_bus = _failed_bus("w1", "broken", "tests/e2e/test_broken.py::test_broken", "ValueError: still broken")
    render_summary(
        console=console,
        bus=EventBus(),
        scenario_results=[ScenarioResult("broken", "w1", 1, 1.0, 0.0, 1.0)],
        orphans={},
        tmp_dir=Path("/tmp/run"),
        verdict=_Verdict(failed=["broken"]),
        retry_buses=[retry_bus],
    )
    assert "still broken" in buf.getvalue()
