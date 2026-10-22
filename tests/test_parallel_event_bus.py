"""Tests for :class:`EventBus` — line parsing, state model, sink routing."""

from __future__ import annotations

import base64

from steppy._parallel.event_bus import EventBus, RawLogChannel


def _feed_events(bus: EventBus, worker_id: str, lines: list[str]) -> None:
    for line in lines:
        bus.feed_line(worker_id, line)


def test_event_lines_are_parsed_not_forwarded_to_raw_sink():
    raw_lines: list[tuple[str, str]] = []
    bus = EventBus(raw_line_sink=lambda wid, line: raw_lines.append((wid, line)))
    bus.feed_line("w1", "STEPPY-EVT::session-start::5")
    bus.feed_line("w1", "STEPPY-EVT::test-start::nid::1700000000.0")
    assert raw_lines == []


def test_non_event_lines_route_to_raw_sink():
    raw_lines: list[tuple[str, str]] = []
    bus = EventBus(raw_line_sink=lambda wid, line: raw_lines.append((wid, line)))
    bus.feed_line("w1", "regular pytest line")
    bus.feed_line("w2", "another line")
    assert raw_lines == [("w1", "regular pytest line"), ("w2", "another line")]


def test_malformed_event_falls_through_to_raw_sink():
    raw_lines: list[tuple[str, str]] = []
    bus = EventBus(raw_line_sink=lambda wid, line: raw_lines.append((wid, line)))
    bus.feed_line("w1", "STEPPY-EVT::unknown-kind::foo")
    assert raw_lines == [("w1", "STEPPY-EVT::unknown-kind::foo")]


def test_scenario_lifecycle_tracks_worker_state():
    bus = EventBus()
    bus.register_worker("w1")
    _feed_events(
        bus,
        "w1",
        [
            "STEPPY-EVT::scenario-start::cross--login::w1::1700000000.0",
            "STEPPY-EVT::test-start::tests/e2e/test_login.py::test_login[editor-fr]::1700000001.0",
            "STEPPY-EVT::test-end::tests/e2e/test_login.py::test_login[editor-fr]::passed::3500",
            "STEPPY-EVT::scenario-end::cross--login::w1::0::4000",
        ],
    )
    snap = bus.snapshot()
    ws = snap.workers["w1"]
    assert ws.current_scenario is None
    assert ws.current_test is None
    assert snap.counts["passed"] == 1
    assert len(snap.scenarios) == 1
    assert snap.scenarios[0].slug == "cross--login"
    assert snap.scenarios[0].exit_code == 0


def test_failed_test_records_fail_tail_decoded():
    bus = EventBus()
    longrepr = "E   AssertionError: nope\nE   assert 1 == 2"
    b64 = base64.b64encode(longrepr.encode("utf-8")).decode("ascii")
    _feed_events(
        bus,
        "w1",
        [
            "STEPPY-EVT::scenario-start::s::w1::1.0",
            "STEPPY-EVT::test-start::s::test_x::1.0",
            "STEPPY-EVT::test-end::s::test_x::failed::1000",
            f"STEPPY-EVT::test-fail-tail::s::test_x::{b64}",
        ],
    )
    snap = bus.snapshot()
    failures = [t for t in snap.tests if t.outcome == "failed"]
    assert len(failures) == 1
    assert failures[0].fail_tail == longrepr
    assert snap.counts["failed"] == 1


def test_concurrent_workers_keep_separate_state():
    bus = EventBus()
    bus.register_worker("w1")
    bus.register_worker("w2")
    # w1 starts a scenario; w2 starts a different one.
    bus.feed_line("w1", "STEPPY-EVT::scenario-start::scen-a::w1::1.0")
    bus.feed_line("w2", "STEPPY-EVT::scenario-start::scen-b::w2::1.0")
    bus.feed_line("w1", "STEPPY-EVT::test-start::tA::1.5")
    bus.feed_line("w2", "STEPPY-EVT::test-start::tB::1.5")
    snap = bus.snapshot()
    assert snap.workers["w1"].current_scenario == "scen-a"
    assert snap.workers["w1"].current_test == "tA"
    assert snap.workers["w2"].current_scenario == "scen-b"
    assert snap.workers["w2"].current_test == "tB"


def test_mark_worker_finished_synthesises_outcome_for_inflight_test():
    bus = EventBus()
    bus.register_worker("w1")
    _feed_events(
        bus,
        "w1",
        [
            "STEPPY-EVT::scenario-start::s::w1::1.0",
            "STEPPY-EVT::test-start::tests/e2e/test_x.py::test_x::1.0",
        ],
    )
    bus.mark_worker_finished("w1", died=True)
    snap = bus.snapshot()
    ws = snap.workers["w1"]
    assert ws.finished is True
    assert ws.current_test is None
    died = [t for t in snap.tests if t.outcome == "worker_died"]
    assert len(died) == 1
    assert snap.counts["worker_died"] == 1


def test_interrupted_outcome_used_when_interrupted_flag_set():
    bus = EventBus()
    bus.register_worker("w1")
    _feed_events(
        bus,
        "w1",
        [
            "STEPPY-EVT::scenario-start::s::w1::1.0",
            "STEPPY-EVT::test-start::nid::1.0",
        ],
    )
    bus.mark_worker_finished("w1", interrupted=True)
    snap = bus.snapshot()
    interrupted = [t for t in snap.tests if t.outcome == "interrupted"]
    assert len(interrupted) == 1
    assert snap.counts["interrupted"] == 1


def test_subscriber_fires_after_each_event():
    received: list[tuple[str, str]] = []
    bus = EventBus()
    bus.subscribe(lambda wid, kind, snap: received.append((wid, kind)))
    bus.feed_line("w1", "STEPPY-EVT::scenario-start::s::w1::1.0")
    bus.feed_line("w1", "STEPPY-EVT::test-start::nid::1.0")
    bus.feed_line("w1", "STEPPY-EVT::test-end::nid::passed::500")
    assert received == [("w1", "scenario-start"), ("w1", "test-start"), ("w1", "test-end")]


def test_raw_log_channel_writes_to_per_worker_file(tmp_path):
    channel = RawLogChannel(log_dir=tmp_path)
    channel("w1", "hello from w1")
    channel("w2", "hello from w2")
    channel("w1", "another line for w1")
    channel.close()
    w1_log = (tmp_path / "w1-pytest.log").read_text(encoding="utf-8")
    w2_log = (tmp_path / "w2-pytest.log").read_text(encoding="utf-8")
    assert w1_log == "hello from w1\nanother line for w1\n"
    assert w2_log == "hello from w2\n"


def test_test_end_without_prior_test_start_is_still_recorded():
    """Robustness: out-of-order events shouldn't drop data."""
    bus = EventBus()
    bus.feed_line("w1", "STEPPY-EVT::test-end::orphan_test::passed::100")
    snap = bus.snapshot()
    assert snap.counts["passed"] == 1
    assert any(t.nodeid == "orphan_test" for t in snap.tests)


def test_scenario_end_synthesises_passed_when_no_test_events():
    """When the pytest events plugin doesn't fire (stale conftest, missing
    ``_steppy_events.py``, etc.) the worker still emits scenario-start/-end.
    The bus must synthesize a test outcome from the exit code so the live
    counters reflect progress instead of staying at 0."""
    bus = EventBus()
    bus.register_worker("w1")
    _feed_events(
        bus,
        "w1",
        [
            "STEPPY-EVT::scenario-start::s::w1::1.0",
            "STEPPY-EVT::scenario-end::s::w1::0::4000",
        ],
    )
    snap = bus.snapshot()
    assert snap.counts["passed"] == 1
    assert snap.counts["failed"] == 0
    synthetics = [t for t in snap.tests if t.nodeid.startswith("<synthetic>::")]
    assert len(synthetics) == 1
    assert synthetics[0].scenario_slug == "s"
    assert synthetics[0].outcome == "passed"


def test_scenario_end_synthesises_failed_when_exit_code_nonzero():
    bus = EventBus()
    bus.register_worker("w1")
    _feed_events(
        bus,
        "w1",
        [
            "STEPPY-EVT::scenario-start::s::w1::1.0",
            "STEPPY-EVT::scenario-end::s::w1::1::4000",
        ],
    )
    snap = bus.snapshot()
    assert snap.counts["failed"] == 1
    assert snap.counts["passed"] == 0


def test_scenario_end_does_not_double_count_when_plugin_fired():
    """If the events plugin already emitted a real test-end for the
    scenario, scenario-end must NOT add a synthetic one."""
    bus = EventBus()
    bus.register_worker("w1")
    _feed_events(
        bus,
        "w1",
        [
            "STEPPY-EVT::scenario-start::s::w1::1.0",
            "STEPPY-EVT::test-start::tests/e2e/test_s.py::test_s::1.0",
            "STEPPY-EVT::test-end::tests/e2e/test_s.py::test_s::passed::500",
            "STEPPY-EVT::scenario-end::s::w1::0::4000",
        ],
    )
    snap = bus.snapshot()
    assert snap.counts["passed"] == 1
    assert not any(t.nodeid.startswith("<synthetic>::") for t in snap.tests)


def test_scenario_end_synthesises_outcome_for_test_started_but_not_finished():
    """Real-world failure mode the user hit: ``test-start`` arrived (the
    renderer shows the running test name) but ``test-end`` never did — the
    pytest events plugin emitted the start event but the call-phase event
    was swallowed by capture or arrived too late. On scenario-end the bus
    must assign a synthetic outcome to the incomplete TestRecord so the
    live counters move."""
    bus = EventBus()
    bus.register_worker("w1")
    _feed_events(
        bus,
        "w1",
        [
            "STEPPY-EVT::scenario-start::s::w1::1.0",
            "STEPPY-EVT::test-start::tests/e2e/test_s.py::test_s::1.0",
            # No test-end here — the call-phase logreport never made it out.
            "STEPPY-EVT::scenario-end::s::w1::0::4000",
        ],
    )
    snap = bus.snapshot()
    assert snap.counts["passed"] == 1
    incomplete = [t for t in snap.tests if t.scenario_slug == "s"]
    assert len(incomplete) == 1
    assert incomplete[0].outcome == "passed"
    assert incomplete[0].finished_at is not None


def test_scenario_end_synthesises_failed_for_started_test_when_exit_nonzero():
    bus = EventBus()
    bus.register_worker("w1")
    _feed_events(
        bus,
        "w1",
        [
            "STEPPY-EVT::scenario-start::s::w1::1.0",
            "STEPPY-EVT::test-start::tests/e2e/test_s.py::test_s::1.0",
            "STEPPY-EVT::scenario-end::s::w1::1::4000",
        ],
    )
    snap = bus.snapshot()
    assert snap.counts["failed"] == 1
    assert snap.counts["passed"] == 0


def test_warning_event_surfaces_through_raw_sink():
    raw_lines: list[tuple[str, str]] = []
    bus = EventBus(raw_line_sink=lambda wid, line: raw_lines.append((wid, line)))
    bus.feed_line("w1", "STEPPY-EVT::warning::events-plugin-missing::no module foo")
    assert len(raw_lines) == 1
    wid, line = raw_lines[0]
    assert wid == "w1"
    assert "events-plugin-missing" in line
    assert "no module foo" in line


def test_worker_done_marks_finished_no_inflight():
    """Happy path: worker emits worker-done after the queue drains. The bus
    sets ws.finished=True. No synthetic outcome — the preceding scenario-end
    already cleared current_test, so the defensive branch never fires."""
    bus = EventBus()
    bus.register_worker("w1")
    _feed_events(
        bus,
        "w1",
        [
            "STEPPY-EVT::scenario-start::s::w1::1.0",
            "STEPPY-EVT::test-start::tests/e2e/test_s.py::test_s::1.0",
            "STEPPY-EVT::test-end::tests/e2e/test_s.py::test_s::passed::500",
            "STEPPY-EVT::scenario-end::s::w1::0::4000",
            "STEPPY-EVT::worker-done::w1",
        ],
    )
    snap = bus.snapshot()
    assert snap.workers["w1"].finished is True
    assert snap.workers["w1"].current_test is None
    assert snap.counts["passed"] == 1
    assert snap.counts["worker_died"] == 0


def test_worker_done_synthesises_worker_died_when_test_open():
    """Defensive: if worker-done arrives while a test is still open (worker
    bug), treat it as worker_died rather than silently dropping the test."""
    bus = EventBus()
    bus.register_worker("w1")
    _feed_events(
        bus,
        "w1",
        [
            "STEPPY-EVT::scenario-start::s::w1::1.0",
            "STEPPY-EVT::test-start::tests/e2e/test_s.py::test_s::1.0",
            "STEPPY-EVT::worker-done::w1",
        ],
    )
    snap = bus.snapshot()
    assert snap.workers["w1"].finished is True
    assert snap.workers["w1"].current_test is None
    assert snap.counts["worker_died"] == 1


def test_worker_done_idempotent_with_mark_worker_finished():
    """Coordinator calls mark_worker_finished post-wait even when worker-done
    already fired. The second call must be a no-op for counts."""
    bus = EventBus()
    bus.register_worker("w1")
    _feed_events(bus, "w1", ["STEPPY-EVT::worker-done::w1"])
    bus.mark_worker_finished("w1", died=False)
    snap = bus.snapshot()
    assert snap.workers["w1"].finished is True
    # Neither call should bump any outcome counter (no in-flight test).
    assert sum(snap.counts.values()) == 0


def test_subscriber_fires_for_worker_done():
    """The MilestoneRenderer dispatches on event kind — make sure
    'worker-done' reaches subscribers."""
    received: list[tuple[str, str]] = []
    bus = EventBus()
    bus.subscribe(lambda wid, kind, snap: received.append((wid, kind)))
    bus.feed_line("w1", "STEPPY-EVT::worker-done::w1")
    assert received == [("w1", "worker-done")]
