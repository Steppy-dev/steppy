"""The per-step timeline underneath ``manifest.tests[]``.

``tests_from_events`` folds the event stream down to one verdict per nodeid,
which is right for the manifest and useless for showing *what happened*.
``steps_from_records`` keeps the ordering. What is pinned here is mostly the
grammar's rough edges — sparse step numbers, an unclosed final step, two attempts
in one file — because each of them reads like a bug the first time you meet it.
"""

import json

import pytest

from steppy.bundle.results import (
    StepRecord,
    parse_event_lines,
    read_event_texts,
    steps_from_records,
)

NODEID = "tests/e2e/test_order-lifecycle.py::test_flow[fr-mobile]"


def _ev(kind, ts, **fields):
    return {
        "v": 1,
        "kind": kind,
        "ts": ts,
        "scenario": "order-lifecycle",
        "locale": "fr",
        "layout": "mobile",
        "nodeid": NODEID,
        **fields,
    }


def _jsonl(events):
    return "".join(json.dumps(e, sort_keys=True, separators=(",", ":")) + "\n" for e in events)


# ── parse_event_lines ───────────────────────────────────────────────────────


def test_parses_in_file_order():
    records, warnings = parse_event_lines([("a.jsonl", _jsonl([_ev("test-start", 1.0), _ev("test-end", 2.0)]))])
    assert [r["kind"] for r in records] == ["test-start", "test-end"]
    assert warnings == []


def test_torn_last_line_is_counted_not_fatal():
    """A killed process leaves a half-written line; losing one event must not
    cost the whole timeline."""
    text = _jsonl([_ev("test-start", 1.0)]) + '{"kind": "step-sta'
    records, warnings = parse_event_lines([("a.jsonl", text)])
    assert len(records) == 1
    assert "1 malformed event line(s) skipped in a.jsonl" in warnings


def test_non_object_lines_are_torn_too():
    records, warnings = parse_event_lines([("a.jsonl", "[1, 2]\n42\n")])
    assert records == []
    assert warnings and "2 malformed" in warnings[0]


def test_blank_lines_are_not_torn():
    records, warnings = parse_event_lines([("a.jsonl", "\n\n" + _jsonl([_ev("test-start", 1.0)]))])
    assert len(records) == 1 and warnings == []


def test_empty_input_is_empty_output():
    assert parse_event_lines([]) == ([], [])
    assert parse_event_lines([("a.jsonl", "")]) == ([], [])


def test_read_event_texts_reads_the_directory_form(tmp_path):
    (tmp_path / "b__fr-mobile.jsonl").write_text(_jsonl([_ev("test-start", 1.0)]), encoding="utf-8")
    (tmp_path / "a__fr-mobile.jsonl").write_text(_jsonl([_ev("test-end", 2.0)]), encoding="utf-8")
    (tmp_path / "ignored.txt").write_text("nope", encoding="utf-8")
    texts = read_event_texts(tmp_path)
    assert [name for name, _ in texts] == ["a__fr-mobile.jsonl", "b__fr-mobile.jsonl"]


def test_read_event_texts_tolerates_a_missing_directory(tmp_path):
    assert read_event_texts(tmp_path / "nope") == []


# ── steps_from_records ──────────────────────────────────────────────────────


def _attempts(events):
    return steps_from_records(events)[NODEID]


def test_a_completed_scenario_reads_as_one_passing_attempt():
    attempts = _attempts(
        [
            _ev("test-start", 100.0),
            _ev("step-start", 100.5, step=1, title="Open the dashboard", action="navigate"),
            _ev("step-end", 101.5, step=1),
            _ev("step-start", 101.5, step=2, title="Click the row", action="click"),
            _ev("step-end", 102.0, step=2),
            _ev("test-end", 102.2, outcome="passed", duration_ms=2200),
        ]
    )
    assert len(attempts) == 1
    attempt = attempts[0]
    assert attempt.outcome == "passed"
    assert attempt.duration_ms == 2200
    assert attempt.scenario == "order-lifecycle"
    assert (attempt.locale, attempt.layout) == ("fr", "mobile")
    assert [s.step for s in attempt.steps] == [1, 2]
    assert [s.status for s in attempt.steps] == ["passed", "passed"]
    assert attempt.steps[0].title == "Open the dashboard"
    assert attempt.steps[0].action == "navigate"
    assert attempt.steps[0].duration_ms == 1000
    assert attempt.failed_step is None


def test_the_unclosed_step_is_the_failing_one():
    attempt = _attempts(
        [
            _ev("test-start", 100.0),
            _ev("step-start", 100.0, step=1, title="Open", action="navigate"),
            _ev("step-end", 101.0, step=1),
            _ev("step-start", 101.0, step=7, title="Submit the order", action="click"),
            _ev("test-end", 141.2, outcome="failed", duration_ms=41200),
        ]
    )[0]
    assert attempt.failed_step == 7
    assert attempt.steps[-1].status == "running"
    # Unknown, not zero: the end genuinely never happened.
    assert attempt.steps[-1].duration_ms is None


def test_step_numbers_stay_sparse():
    """Steps skipped by ``only-in:`` / ``layouts:`` emit nothing at all, so gaps
    are expected — renumbering them would misreport which step failed."""
    attempt = _attempts(
        [
            _ev("test-start", 100.0),
            _ev("step-start", 100.0, step=1, action="navigate"),
            _ev("step-end", 100.5, step=1),
            _ev("step-start", 100.5, step=4, action="click"),
            _ev("step-end", 101.0, step=4),
            _ev("test-end", 101.0, outcome="passed", duration_ms=1000),
        ]
    )[0]
    assert [s.step for s in attempt.steps] == [1, 4]


def test_in_process_reruns_produce_two_attempts():
    """pytest ``--reruns`` reruns in-process, so both attempts land in one file
    and the second ``test-start`` must not be folded into the first attempt."""
    attempts = _attempts(
        [
            _ev("test-start", 100.0),
            _ev("step-start", 100.0, step=1, action="click"),
            _ev("test-end", 105.0, outcome="failed", duration_ms=5000),
            _ev("test-start", 106.0),
            _ev("step-start", 106.0, step=1, action="click"),
            _ev("step-end", 107.0, step=1),
            _ev("test-end", 107.0, outcome="passed", duration_ms=1000),
        ]
    )
    assert [a.outcome for a in attempts] == ["failed", "passed"]
    assert attempts[0].failed_step == 1
    assert attempts[1].failed_step is None


def test_a_stray_step_end_cannot_close_a_step_it_did_not_open():
    attempt = _attempts(
        [
            _ev("test-start", 100.0),
            _ev("step-start", 100.0, step=3, action="click"),
            _ev("step-end", 100.5, step=9),
            _ev("test-end", 101.0, outcome="failed", duration_ms=1000),
        ]
    )[0]
    assert attempt.failed_step == 3


def test_records_with_no_test_start_still_keep_their_steps():
    """A stale generated tree, or a write interrupted mid-run, should degrade to
    a partial timeline rather than to nothing."""
    attempt = _attempts(
        [
            _ev("step-start", 100.0, step=1, action="navigate"),
            _ev("step-end", 100.5, step=1),
        ]
    )[0]
    assert [s.step for s in attempt.steps] == [1]
    assert attempt.outcome is None


def test_records_without_a_nodeid_are_ignored():
    assert steps_from_records([{"kind": "step-start", "ts": 1.0, "step": 1}]) == {}


def test_a_non_integer_step_is_ignored_rather_than_crashing():
    attempt = _attempts([_ev("test-start", 1.0), _ev("step-start", 1.0, step="seven")])[0]
    assert attempt.steps == []


def test_no_step_events_at_all_is_an_empty_timeline_not_an_error():
    """``STEPPY_STEP_EVENTS=0``, or a tree generated before the markers existed.
    The absence is information the report has to be able to state."""
    attempt = _attempts([_ev("test-start", 1.0), _ev("test-end", 2.0, outcome="passed", duration_ms=1000)])[0]
    assert attempt.steps == []
    assert attempt.failed_step is None


def test_multiple_nodeids_stay_separate():
    other = "tests/e2e/test_other.py::test_flow[nl-desktop]"
    records = [
        _ev("test-start", 1.0),
        {"v": 1, "kind": "test-start", "ts": 2.0, "nodeid": other, "scenario": "other"},
    ]
    result = steps_from_records(records)
    assert set(result) == {NODEID, other}


# ── offsets ─────────────────────────────────────────────────────────────────


def test_offsets_are_relative_to_the_attempt_start():
    """Absolute epochs would leak a runner's clock into a published artifact and
    make the model undiffable."""
    attempt = _attempts(
        [
            _ev("test-start", 1_700_000_000.0),
            _ev("step-start", 1_700_000_000.5, step=1, title="Open", action="navigate"),
            _ev("step-end", 1_700_000_001.5, step=1),
            _ev("test-end", 1_700_000_002.0, outcome="passed", duration_ms=2000),
        ]
    )[0]
    rows = attempt.offsets()
    assert rows == [
        {
            "step": 1,
            "title": "Open",
            "action": "navigate",
            "status": "passed",
            "offset_ms": 500,
            "duration_ms": 1000,
        }
    ]


def test_offsets_of_an_empty_timeline_is_empty():
    attempt = _attempts([_ev("test-start", 1.0)])[0]
    assert attempt.offsets() == []


@pytest.mark.parametrize("ts", [None, "nope", {}])
def test_a_missing_or_bad_ts_degrades_to_zero(ts):
    record = StepRecord(1, started_at=0.0)
    assert record.duration_ms is None
    attempt = _attempts([_ev("test-start", 1.0), dict(_ev("step-start", 0.0, step=1), ts=ts)])[0]
    assert attempt.steps[0].started_at == 0.0


def test_duration_never_goes_negative_on_a_clock_step():
    """NTP can move the clock backwards mid-run; a negative duration would render
    as nonsense where a zero reads as "too fast to measure"."""
    attempt = _attempts(
        [
            _ev("test-start", 100.0),
            _ev("step-start", 100.0, step=1, action="click"),
            _ev("step-end", 99.0, step=1),
        ]
    )[0]
    assert attempt.steps[0].duration_ms == 0
