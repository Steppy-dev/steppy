"""A test retried in its process (``--retries``) is recorded attempt by attempt.

pytest-rerunfailures runs the protocol again and relabels the failed phase of an
attempt it is about to repeat as ``rerun``. The recorder used to take that as the
test's end, so a test that passed on a retry was reported as failing, and one
that failed every time lost its failure text and its step.

Every case runs the real recorder under a real pytest. The plugin is an optional
extra, so it is played by a double in the two shapes it has had (up to 15 it
stopped at the failed phase; since 16 it reports the teardown too), and by the
plugin itself where it is installed (CI).
"""

from __future__ import annotations

import base64
import importlib.util
import io
import json
import re

import pytest
from rich.console import Console

from steppy._parallel.event_bus import EventBus
from steppy._parallel.renderer import MilestoneRenderer
from steppy.bundle.results import (
    parse_event_lines,
    read_event_files,
    read_event_texts,
    steps_from_records,
    tests_from_events as events_to_tests,
)
from tests.test_bundle_events_sink import _PLUGIN_PATH, _step_event_helpers

pytest_plugins = ["pytester"]

_CONFTEST = """
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _steppy_events import (
    pytest_configure, pytest_collection_finish, pytest_runtest_protocol,
    pytest_runtest_logstart, pytest_runtest_logreport, pytest_sessionfinish,
)
"""

#: One test whose attempts do, in order, what ``CASES`` says.
_MODULE = """
import os
import pytest
from conftest import _steppy_mark_step, _steppy_end_step

SCENARIO = "order-lifecycle"
CASES = os.environ["CASES"].split(",")
DONE = []

@pytest.fixture
def editor_page():
    DONE.append(CASES[len(DONE)])
    if "setup" in DONE[-1]:
        raise RuntimeError(f"setup of attempt {len(DONE)}")
    yield
    if "teardown" in DONE[-1]:
        raise RuntimeError(f"teardown of attempt {len(DONE)}")

def test_flow(editor_page):
    _steppy_mark_step(1, 'Open the list', 'navigate')
    _steppy_end_step(1)
    if "skip" in DONE[-1]:
        pytest.skip("not now")
    _steppy_mark_step(2, 'Submit the order', 'click')
    assert "fail" not in DONE[-1], f"body of attempt {len(DONE)}"
    _steppy_end_step(2)
"""

#: What pytest-rerunfailures does: run the protocol again in the same process,
#: announcing each attempt, with a failed report relabelled when it retries.
_DOUBLE = """
import os
from _pytest.runner import runtestprotocol

RERUNS = int(os.environ["RERUNS"])
STOP = os.environ["RERUN_PLUGIN"] == "double-15"

def pytest_runtest_protocol(item, nextitem):
    for attempt in range(RERUNS + 1):
        item.ihook.pytest_runtest_logstart(nodeid=item.nodeid, location=item.location)
        again = False
        for report in runtestprotocol(item, nextitem=nextitem, log=False):
            if report.failed and attempt < RERUNS:
                report.outcome = "rerun"
                again = True
            item.ihook.pytest_runtest_logreport(report=report)
            if again and STOP:
                break
        item.ihook.pytest_runtest_logfinish(nodeid=item.nodeid, location=item.location)
        if not again:
            break
    return True
"""

_PLUGINS = ["double-15", "double-16", "installed"]


class _Run:
    def __init__(self, events_dir, stdout: str):
        self.events_dir = events_dir
        self.stdout = stdout
        self.lines, _ = parse_event_lines(read_event_texts(events_dir))

    @property
    def kinds(self) -> list[str]:
        """``test-start``, ``test-end:<outcome>`` and ``test-fail-tail:<text>``, in order."""
        out = []
        for event in self.lines:
            if event["kind"] == "test-end":
                out.append(f"test-end:{event['outcome']}")
            elif event["kind"] == "test-fail-tail":
                text = base64.b64decode(event["tail_b64"]).decode()
                out.append(f"test-fail-tail:{re.search('(setup|body|teardown) of attempt [0-9]', text).group()}")
            elif event["kind"] == "test-start":
                out.append("test-start")
        return out

    @property
    def attempts(self):
        (attempts,) = steps_from_records(self.lines).values()
        return attempts

    @property
    def outcomes(self) -> list[str]:
        return [a.outcome for a in self.attempts]

    def verdict(self):
        """The manifest's row, its failure text decoded, and the warnings."""
        (test,), warnings = events_to_tests(self.events_dir)
        tail = base64.b64decode(test.fail_tail_b64).decode() if test.fail_tail_b64 else ""
        return test, tail, warnings

    def bus(self, exit_code: int, *, as_printed: bool = False):
        """The run on the coordinator's bus, with what a CI log shows of it.

        ``as_printed`` feeds pytest's lines whole: its progress letter can precede
        an event on one line, which the bus then does not read as an event.
        """
        bus, buffer = EventBus(), io.StringIO()
        MilestoneRenderer(bus=bus, console=Console(file=buffer, width=200, no_color=True), total_scenarios=1)
        bus.feed_line("w1", "STEPPY-EVT::scenario-start::order-lifecycle::w1::1.0")
        for line in self.stdout.splitlines() if as_printed else re.findall("STEPPY-EVT::.*", self.stdout):
            bus.feed_line("w1", line)
        bus.feed_line("w1", f"STEPPY-EVT::scenario-end::order-lifecycle::w1::{exit_code}::1000")
        return bus.snapshot(), buffer.getvalue()


@pytest.fixture(params=_PLUGINS)
def retried(request, pytester, monkeypatch):
    plugin = request.param
    if plugin == "installed" and importlib.util.find_spec("pytest_rerunfailures") is None:
        pytest.skip("pytest-rerunfailures is not installed")

    def run(cases: str, reruns: int = 1) -> _Run:
        pytester.makepyfile(
            _steppy_events=_PLUGIN_PATH.read_text(encoding="utf-8"),
            double=_DOUBLE,
            test_order_lifecycle=_MODULE,
        )
        pytester.makeconftest(_CONFTEST + _step_event_helpers())
        events = pytester.path / "events"
        for key, value in {
            "STEPPY_EVENTS_DIR": str(events),
            "STEPPY_STEP_EVENTS": "1",
            "STEPPY_EMIT_EVENTS": "1",
            "CASES": cases,
            "RERUNS": str(reruns),
            "RERUN_PLUGIN": plugin,
        }.items():
            monkeypatch.setenv(key, value)
        args = [f"--reruns={reruns}"] if plugin == "installed" else ["-p", "no:rerunfailures", "-p", "double"]
        result = pytester.runpytest_subprocess("-p", "no:cacheprovider", "-q", *args)
        assert len(read_event_files(events)) == 1, result.stdout.str()
        return _Run(events, result.stdout.str())

    return run


# ── one start and one end per attempt ───────────────────────────────────────


def test_a_test_that_passes_on_its_retry_passed(retried):
    run = retried("fail,pass")
    assert run.kinds == [
        "test-start",
        "test-end:failed",
        "test-fail-tail:body of attempt 1",
        "test-start",
        "test-end:passed",
    ]
    assert run.outcomes == ["failed", "passed"]
    assert [a.failed_step for a in run.attempts] == [2, None]

    test, tail, warnings = run.verdict()
    assert (test.outcome, test.failed_step) == ("passed", None)
    # The failure it recovered from is the only failure there is to show.
    assert "body of attempt 1" in tail
    assert warnings == []


def test_a_test_that_fails_every_time_failed_the_way_its_last_attempt_did(retried):
    run = retried("fail,fail")
    assert run.outcomes == ["failed", "failed"]
    test, tail, warnings = run.verdict()
    assert (test.outcome, test.failed_step) == ("failed", 2)
    assert "body of attempt 2" in tail and "body of attempt 1" not in tail
    assert warnings == []


def test_every_attempt_is_recorded(retried):
    run = retried("fail,fail,pass", reruns=2)
    assert run.outcomes == ["failed", "failed", "passed"]
    test, tail, _ = run.verdict()
    assert test.outcome == "passed"
    assert "body of attempt 2" in tail


def test_a_first_attempt_that_dies_in_setup_is_an_error(retried):
    run = retried("setup,pass")
    assert run.kinds == [
        "test-start",
        "test-end:error",
        "test-fail-tail:setup of attempt 1",
        "test-start",
        "test-end:passed",
    ]
    assert run.verdict()[0].outcome == "passed"


def test_a_retry_that_dies_in_setup_does_not_keep_the_first_failures_step(retried):
    run = retried("fail,setup")
    assert run.outcomes == ["failed", "error"]
    test, tail, _ = run.verdict()
    assert (test.outcome, test.failed_step) == ("error", None)
    assert "setup of attempt 2" in tail


def test_an_attempt_run_again_for_its_teardown_did_not_pass(retried):
    """Its call passed, but an attempt that passed is not run again."""
    run = retried("teardown,pass")
    assert run.outcomes == ["error", "passed"]
    test, tail, warnings = run.verdict()
    assert test.outcome == "passed"
    assert "teardown of attempt 1" in tail
    assert warnings == []


def test_a_failing_attempt_ends_once_even_when_its_teardown_fails_too(retried):
    run = retried("fail-teardown,pass")
    assert run.outcomes == ["failed", "passed"]
    assert [k for k in run.kinds if k.startswith("test-end")] == ["test-end:failed", "test-end:passed"]
    # The text of the failure itself, not of the cleanup that followed it.
    assert "body of attempt 1" in run.verdict()[1]


def test_a_retry_that_skips_is_skipped(retried):
    run = retried("fail,skip")
    assert run.outcomes == ["failed", "skipped"]
    assert run.verdict()[0].outcome == "skipped"


def test_a_test_that_passes_at_once_is_not_affected(retried):
    run = retried("pass")
    assert run.kinds == ["test-start", "test-end:passed"]
    test, tail, _ = run.verdict()
    assert (test.outcome, tail) == ("passed", "")


# ── the live stream of a parallel run ───────────────────────────────────────


@pytest.mark.parametrize(
    "cases,exit_code,outcome",
    [
        ("fail,pass", 0, "passed"),
        ("fail,fail", 1, "failed"),
        ("setup,pass", 0, "passed"),
        ("teardown,pass", 0, "passed"),
        ("pass", 0, "passed"),
    ],
)
def test_a_retried_test_counts_once_in_the_live_progress(retried, cases, exit_code, outcome):
    run = retried(cases)
    assert "::rerun::" not in run.stdout  # the plugin's own word is not in the grammar
    for as_printed in (False, True):
        snap, _ = run.bus(exit_code, as_printed=as_printed)
        assert {k: n for k, n in snap.counts.items() if n} == {outcome: 1}
        (record,) = snap.tests
        assert (record.outcome, record.attempt) == (outcome, len(cases.split(",")))


def test_the_failure_of_a_test_that_passed_on_retry_is_not_listed(retried):
    (record,) = retried("fail,pass").bus(0)[0].tests
    assert record.fail_tail is None
    (record,) = retried("fail,fail").bus(1)[0].tests
    assert "body of attempt 2" in record.fail_tail


def test_a_ci_log_says_which_attempt_it_was(retried):
    _, log = retried("fail,pass").bus(0)
    first, second = [line for line in log.splitlines() if "test_flow" in line]
    assert "FAILED test_flow" in first and "attempt" not in first
    assert "1 done, 1 failed" in first
    assert "PASSED test_flow" in second and ", attempt 2)" in second
    assert "1 done, 0 failed" in second


# ── the reader ──────────────────────────────────────────────────────────────


def _write(tmp_path, *rows):
    base = {"v": 1, "ts": 1.0, "nodeid": "tests/e2e/test_a.py::test_a", "scenario": "a", "locale": None, "layout": None}
    (tmp_path / "a.jsonl").write_text("\n".join(json.dumps({**base, **row}) for row in rows), encoding="utf-8")
    (test,), warnings = events_to_tests(tmp_path)
    return test, warnings


_FAILED_FIRST = (
    {"kind": "test-start"},
    {"kind": "step-start", "step": 2, "title": "b", "action": "click"},
    {"kind": "test-end", "outcome": "failed", "duration_ms": 10},
    {"kind": "test-fail-tail", "tail_b64": "Ym9vbQ=="},
)


def test_a_retry_that_never_ended_is_an_error_without_the_first_attempts_text(tmp_path):
    """The worker died in attempt 2: what attempt 1 did is not how the test ended."""
    test, _ = _write(tmp_path, *_FAILED_FIRST, {"kind": "test-start"})
    assert (test.outcome, test.duration_ms, test.fail_tail_b64, test.failed_step) == ("error", None, None, None)


def test_a_suite_generated_before_the_fix_is_named_not_misread(tmp_path):
    """It recorded ``rerun`` and nothing after it: neither a pass nor a failure is
    known, and the way out is one command."""
    test, warnings = _write(
        tmp_path,
        {"kind": "test-start"},
        {"kind": "step-start", "step": 2, "title": "b", "action": "click"},
        {"kind": "test-end", "outcome": "rerun", "duration_ms": 10},
        {"kind": "test-start"},
        {"kind": "step-start", "step": 2, "title": "b", "action": "click"},
    )
    assert (test.outcome, test.failed_step) == ("rerun", None)
    (warning,) = warnings
    assert "steppy generate" in warning
