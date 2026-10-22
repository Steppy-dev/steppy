"""Test outcomes: event JSONL and JUnit XML → ``manifest.tests[]``.

Two sources, deliberately, and they are not redundant.

The **event JSONL** is primary. It carries every field a ``BundleTest`` needs
losslessly — including ``locale``/``layout``, which the plugin reads off the
pytest item's ``callspec.params`` rather than parsing out of a nodeid. That
matters more than it sounds: a locale like ``fr-BE`` contains the same separator
the id joins on, which makes string parsing wrong rather than merely awkward.

**JUnit XML** is the fallback and the cross-check: it covers tests the plugin
never saw (a stale generated tree, a crash before the plugin loaded), and it is
the artifact a human drops into any CI's test reporter. Rows sourced from it
carry ``locale``/``layout`` of ``None`` rather than a guess — the honest
degradation, and visible in the manifest.
"""

from __future__ import annotations

import base64
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

from ..contracts.bundle import BundleTest

#: Matches ``_steppy_events._FAIL_TAIL_LINES`` so a junit-sourced tail is the
#: same size as an event-sourced one.
FAIL_TAIL_LINES = 15


def _tail_b64(text: str, n: int = FAIL_TAIL_LINES) -> Optional[str]:
    if not text:
        return None
    tail = "\n".join(text.splitlines()[-n:])
    return base64.b64encode(tail.encode("utf-8", errors="replace")).decode("ascii")


def scenario_from_nodeid(nodeid: str) -> Optional[str]:
    """Recover a scenario slug from a pytest node id's module path.

    ``tests/e2e/test_order-lifecycle.py::test_flow[fr-mobile]`` →
    ``order-lifecycle``. Exact, because the generator names every file
    ``test_<slug>.py`` — unlike the parametrization id, which cannot be parsed
    back into a locale and layout.

    This is the fallback for rows that reach us without one: a test module
    missing its baked ``SCENARIO``, or a JUnit-only row. Every ``BundleTest``
    must carry a scenario — a Steppy test belongs to one by construction, and
    the contract types it as required.
    """
    head = nodeid.split("::", 1)[0]
    stem = head.replace("\\", "/").rsplit("/", 1)[-1]
    if stem.endswith(".py"):
        stem = stem[: -len(".py")]
    if stem.startswith("test_"):
        stem = stem[len("test_") :]
    return stem or None


# ── event JSONL ─────────────────────────────────────────────────────────────


def read_event_files(events_dir: Path) -> list[Path]:
    if not events_dir.is_dir():
        return []
    return sorted(p for p in events_dir.glob("*.jsonl") if p.is_file())


def tests_from_events(events_dir: Path) -> tuple[list[BundleTest], list[str]]:
    """Fold the JSONL streams into one ``BundleTest`` per nodeid.

    Returns ``(tests, warnings)``. Malformed lines are skipped rather than
    fatal — a killed process leaves a torn last line, and losing one event is
    not worth losing the run's results over. Same posture as
    ``history.load``.
    """
    by_nodeid: dict[str, dict] = {}
    warnings: list[str] = []
    torn = 0

    for path in read_event_files(events_dir):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except ValueError:
                torn += 1
                continue
            if not isinstance(event, dict):
                torn += 1
                continue
            nodeid = event.get("nodeid")
            if not nodeid:
                continue
            row = by_nodeid.setdefault(
                nodeid,
                {
                    "nodeid": nodeid,
                    "scenario": event.get("scenario"),
                    "locale": event.get("locale"),
                    "layout": event.get("layout"),
                    "outcome": None,
                    "duration_ms": None,
                    "fail_tail_b64": None,
                    "open_step": None,
                },
            )
            kind = event.get("kind")
            if kind == "test-end":
                # Last writer wins: a retry pass re-runs the same nodeid and its
                # final attempt is the run's verdict, matching how the
                # coordinator classifies a pass-on-retry.
                row["outcome"] = event.get("outcome")
                row["duration_ms"] = event.get("duration_ms")
            elif kind == "test-fail-tail" and event.get("phase") != "teardown":
                row["fail_tail_b64"] = event.get("tail_b64")
            elif kind == "test-start":
                # A rerun of the same nodeid starts over — otherwise an unclosed
                # step from attempt 1 would be blamed for attempt 2's result.
                row["open_step"] = None
            elif kind == "step-start":
                row["open_step"] = event.get("step")
            elif kind == "step-end":
                # Steps are sequential, never nested, so at most one is open.
                # Match on the number so an interleaved record from a rerun
                # cannot close a step it did not open.
                if row["open_step"] == event.get("step"):
                    row["open_step"] = None

    if torn:
        warnings.append(f"{torn} malformed event line(s) skipped in {events_dir}")

    tests = []
    for row in by_nodeid.values():
        outcome = row["outcome"] or "error"
        failed_step = _failed_step(row, outcome, warnings)
        tests.append(
            BundleTest(
                nodeid=row["nodeid"],
                # A module without its baked SCENARIO still has one in its filename.
                scenario=row["scenario"] or scenario_from_nodeid(row["nodeid"]),
                locale=row["locale"],
                layout=row["layout"],
                # A test that started and never ended — the worker died mid-scenario,
                # or the run was interrupted. "error" is the honest reading: it did
                # not pass, and we cannot say it failed on its own merits.
                outcome=outcome,
                duration_ms=row["duration_ms"],
                fail_tail_b64=row["fail_tail_b64"],
                failed_step=failed_step,
            )
        )
    return tests, warnings


def _failed_step(row: dict, outcome: str, warnings: list[str]) -> Optional[int]:
    """Which step was in flight when the test died — ``None`` if none was.

    The unclosed ``step-start`` *is* the answer: the generated body emits its
    ``step-end`` on the next line, so a step that raises never reaches it. Two
    cases where the honest answer is ``None`` and a naive "last step reached"
    would lie:

    * the test passed — every step closed, nothing failed;
    * the test failed in fixture teardown or between steps — every step closed
      too, so blaming the final one would point a reader at working code.
    """
    open_step = row.get("open_step")
    if open_step is None:
        return None
    if outcome not in ("failed", "error"):
        # A passing test with an unclosed step means the emitter dropped a
        # record. Say so rather than reporting a failure location for a test
        # that did not fail.
        warnings.append(
            f"{row['nodeid']} reported outcome '{outcome}' with step {open_step} still open; "
            "ignoring failed_step (a step-end record was lost)"
        )
        return None
    return open_step


# ── per-step timeline ───────────────────────────────────────────────────────
#
# ``tests_from_events`` folds the same stream down to one verdict per nodeid and
# discards the ordering, which is right for ``manifest.tests[]`` and wrong for a
# reader that wants to show *what happened*. These two functions keep the JSONL
# grammar in this one module while exposing the timeline underneath it.
#
# They are text-based rather than directory-based on purpose: a bundle's events
# arrive as bytes read out of an archive, and forcing them through a temp
# directory just to reuse a ``Path``-shaped API would be the wrong trade.
#
# What the grammar guarantees, and what it does not:
#
# * ``step-start`` carries ``step``, ``title`` and ``action``; ``step-end``
#   carries ``step``. Both exist only when ``STEPPY_STEP_EVENTS=1`` — which
#   ``steppy run --bundle`` sets — so their absence is information, not an error.
# * Step numbers are **sparse**. A step skipped by ``only-in:`` or ``layouts:``
#   emits neither marker, so gaps are expected and must never be renumbered.
# * No total-step-count exists anywhere in a bundle. "step 7", never "7 of 12".
# * ``ts`` is epoch seconds (``round(time.time(), 3)``). Offsets relative to the
#   attempt's start are the useful, publishable form.


class StepRecord:
    """One step's slice of an attempt's timeline.

    ``status`` is ``passed`` when the step closed, ``running`` when it opened and
    never did (which is the failing step, by the same rule as
    :func:`_failed_step`), and ``duration_ms`` is ``None`` in that case because
    the end is genuinely unknown rather than zero.
    """

    __slots__ = ("step", "title", "action", "status", "started_at", "ended_at")

    def __init__(self, step: int, title: str = "", action: str = "", started_at: float = 0.0):
        self.step = step
        self.title = title
        self.action = action
        self.status = "running"
        self.started_at = started_at
        self.ended_at: Optional[float] = None

    @property
    def duration_ms(self) -> Optional[int]:
        if self.ended_at is None:
            return None
        return max(int(round((self.ended_at - self.started_at) * 1000)), 0)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"StepRecord(step={self.step}, status={self.status!r}, title={self.title!r})"


class TestAttempt:
    """One execution of one nodeid, with the steps it reached.

    **Why attempts and not just steps.** The JSONL sink opens its file with
    ``mode="w"`` on the first write *per process*. pytest ``--reruns`` reruns
    in-process, so a retried test leaves both attempts in one file and this
    reader can show "attempt 2 of 2". The parallel coordinator's retry pass is a
    *new process*, so it truncates the file and attempt 1 is gone — that
    asymmetry is real and worth reporting honestly rather than papering over.
    """

    __slots__ = ("nodeid", "scenario", "locale", "layout", "outcome", "duration_ms", "started_at", "steps")

    def __init__(self, nodeid: str, scenario=None, locale=None, layout=None, started_at: float = 0.0):
        self.nodeid = nodeid
        self.scenario = scenario
        self.locale = locale
        self.layout = layout
        self.outcome: Optional[str] = None
        self.duration_ms: Optional[int] = None
        self.started_at = started_at
        self.steps: list[StepRecord] = []

    @property
    def failed_step(self) -> Optional[int]:
        """The step that opened and never closed, or ``None``."""
        for record in reversed(self.steps):
            if record.status == "running":
                return record.step
        return None

    def offsets(self) -> list[dict]:
        """The timeline as plain dicts, offsets relative to this attempt's start.

        Relative rather than absolute so the result is diffable and so a runner's
        wall clock never leaks into a published artifact.
        """
        base = self.started_at or (self.steps[0].started_at if self.steps else 0.0)
        rows = []
        for record in self.steps:
            rows.append(
                {
                    "step": record.step,
                    "title": record.title,
                    "action": record.action,
                    "status": record.status,
                    "offset_ms": max(int(round((record.started_at - base) * 1000)), 0) if base else 0,
                    "duration_ms": record.duration_ms,
                }
            )
        return rows

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"TestAttempt(nodeid={self.nodeid!r}, outcome={self.outcome!r}, steps={len(self.steps)})"


def parse_event_lines(texts) -> tuple[list[dict], list[str]]:
    """Parse ``(label, text)`` pairs of JSONL into records, in file order.

    Torn and non-object lines are counted and reported, never fatal — a killed
    process leaves a half-written last line, and losing one event is not worth
    losing a run's timeline over. Same posture as :func:`tests_from_events` and
    ``history.load``.
    """
    records: list[dict] = []
    warnings: list[str] = []
    for label, text in texts:
        torn = 0
        for line in (text or "").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except ValueError:
                torn += 1
                continue
            if not isinstance(event, dict):
                torn += 1
                continue
            records.append(event)
        if torn:
            warnings.append(f"{torn} malformed event line(s) skipped in {label}")
    return records, warnings


def read_event_texts(events_dir: Path) -> list[tuple[str, str]]:
    """``(filename, text)`` for every JSONL under *events_dir*, sorted."""
    return [(path.name, path.read_text(encoding="utf-8", errors="replace")) for path in read_event_files(events_dir)]


def steps_from_records(records) -> dict[str, list[TestAttempt]]:
    """``{nodeid: [TestAttempt, …]}`` in the order the attempts ran.

    A ``test-start`` opens a new attempt. Records arriving before any
    ``test-start`` — possible with a stale generated tree, or if the run was
    interrupted mid-write — still get an attempt, so their steps are not lost.
    """
    by_nodeid: dict[str, list[TestAttempt]] = {}

    def current(event: dict) -> Optional[TestAttempt]:
        nodeid = event.get("nodeid")
        if not nodeid:
            return None
        attempts = by_nodeid.setdefault(nodeid, [])
        if not attempts:
            attempts.append(
                TestAttempt(
                    nodeid,
                    scenario=event.get("scenario"),
                    locale=event.get("locale"),
                    layout=event.get("layout"),
                    started_at=_ts(event),
                )
            )
        return attempts[-1]

    for event in records:
        nodeid = event.get("nodeid")
        if not nodeid:
            continue
        kind = event.get("kind")
        if kind == "test-start":
            by_nodeid.setdefault(nodeid, []).append(
                TestAttempt(
                    nodeid,
                    scenario=event.get("scenario"),
                    locale=event.get("locale"),
                    layout=event.get("layout"),
                    started_at=_ts(event),
                )
            )
            continue

        attempt = current(event)
        if attempt is None:
            continue
        if kind == "test-end":
            attempt.outcome = event.get("outcome")
            attempt.duration_ms = event.get("duration_ms")
        elif kind == "step-start":
            step = event.get("step")
            if isinstance(step, int):
                attempt.steps.append(
                    StepRecord(
                        step,
                        title=event.get("title") or "",
                        action=event.get("action") or "",
                        started_at=_ts(event),
                    )
                )
        elif kind == "step-end":
            step = event.get("step")
            # Steps are sequential, never nested, so at most one is open. Match on
            # the number so a stray record cannot close a step it did not open.
            for record in reversed(attempt.steps):
                if record.step == step and record.status == "running":
                    record.status = "passed"
                    record.ended_at = _ts(event)
                    break
    return by_nodeid


def _ts(event: dict) -> float:
    value = event.get("ts")
    return float(value) if isinstance(value, (int, float)) else 0.0


# ── JUnit XML ───────────────────────────────────────────────────────────────


def _junit_outcome(testcase: ET.Element) -> tuple[str, Optional[str]]:
    """``(outcome, fail_tail_b64)`` for one ``<testcase>``."""
    for tag, outcome in (("failure", "failed"), ("error", "error")):
        node = testcase.find(tag)
        if node is not None:
            text = (node.text or "") or (node.get("message") or "")
            return outcome, _tail_b64(text)
    if testcase.find("skipped") is not None:
        return "skipped", None
    return "passed", None


def _junit_nodeid(testcase: ET.Element) -> str:
    """Reconstruct a pytest nodeid from ``classname`` + ``name``.

    pytest writes ``classname="tests.e2e.test_x"``, so the dots become path
    separators. Lossy for a genuinely dotted module name, which is why event
    JSONL wins the merge whenever both have a row.
    """
    classname = testcase.get("classname") or ""
    name = testcase.get("name") or ""
    if not classname:
        return name
    return f"{classname.replace('.', '/')}.py::{name}"


def read_junit_files(junit_dir: Path) -> list[Path]:
    if not junit_dir.is_dir():
        return []
    return sorted(p for p in junit_dir.glob("*.xml") if p.is_file())


def tests_from_junit(junit_dir: Path) -> tuple[list[BundleTest], list[str]]:
    tests: list[BundleTest] = []
    warnings: list[str] = []
    for path in read_junit_files(junit_dir):
        try:
            root = ET.parse(path).getroot()
        except (OSError, ET.ParseError) as e:
            warnings.append(f"skipped unreadable JUnit file {path.name}: {e}")
            continue
        for testcase in root.iter("testcase"):
            outcome, tail = _junit_outcome(testcase)
            try:
                duration_ms = int(float(testcase.get("time") or 0.0) * 1000)
            except ValueError:
                duration_ms = None
            nodeid = _junit_nodeid(testcase)
            tests.append(
                BundleTest(
                    nodeid=nodeid,
                    # The scenario IS recoverable — it is the module name, which
                    # the generator owns. Locale and layout are not: they live in
                    # the parametrization id, which is exactly what the event
                    # sink exists to avoid parsing. So derive one and leave the
                    # other two honestly empty.
                    scenario=scenario_from_nodeid(nodeid),
                    locale=None,
                    layout=None,
                    outcome=outcome,
                    duration_ms=duration_ms,
                    fail_tail_b64=tail,
                    source="junit",
                )
            )
    return tests, warnings


def merge_junit(junit_dir: Path) -> Optional[bytes]:
    """Merge every ``<slug>.xml`` into one ``<testsuites>`` document.

    A parallel run writes one file per scenario (N workers sharing one output
    mount cannot share one path). Wrapping the single-worker case identically
    means the archive's ``junit.xml`` has ONE shape regardless of executor —
    the "identical structure by construction" property the bundle contract
    promises.
    """
    files = read_junit_files(junit_dir)
    if not files:
        return None

    merged = ET.Element("testsuites")
    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    time_total = 0.0

    for path in files:
        try:
            root = ET.parse(path).getroot()
        except (OSError, ET.ParseError):
            continue
        suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
        for suite in suites:
            if not suite.get("name"):
                suite.set("name", path.stem)
            for key in totals:
                try:
                    totals[key] += int(suite.get(key) or 0)
                except ValueError:
                    pass
            try:
                time_total += float(suite.get("time") or 0.0)
            except ValueError:
                pass
            merged.append(suite)

    for key, value in totals.items():
        merged.set(key, str(value))
    merged.set("time", f"{time_total:.3f}")
    return ET.tostring(merged, encoding="utf-8", xml_declaration=True)


# ── merge ───────────────────────────────────────────────────────────────────


def collect_tests(events_dir: Path, junit_dir: Path) -> tuple[list[BundleTest], list[str]]:
    """Merged ``tests[]``, events winning per nodeid, sorted deterministically."""
    event_tests, warnings = tests_from_events(events_dir)
    junit_tests, junit_warnings = tests_from_junit(junit_dir)
    warnings.extend(junit_warnings)

    by_nodeid = {t.nodeid: t for t in event_tests}
    added = 0
    for test in junit_tests:
        if test.nodeid not in by_nodeid:
            by_nodeid[test.nodeid] = test
            added += 1
    if added:
        warnings.append(
            f"{added} test(s) present in JUnit XML but absent from the event records, "
            "so they carry no locale/layout — re-run `steppy generate` if this persists"
        )

    tests = sorted(
        by_nodeid.values(),
        key=lambda t: (t.scenario or "", t.locale or "", t.layout or "", t.nodeid),
    )
    return tests, warnings
