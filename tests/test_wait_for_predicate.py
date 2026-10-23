"""``wait-for`` ``until:`` predicates, and ``wait:`` widened to ``wait-for``.

The most-cited gap in the corpus, and the only one where the scenario authors
wrote down the reason themselves: 47 of one project's 240 custom blocks carry
``note: kept custom — polling for openEdit()/patchValue() to populate the field
has no first-class equivalent``, and 12 more say ``count() is one-shot and the
post-mutation reload has to be polled``. All of them are the same loop:

    page.wait_for_selector(sel, timeout=30000)
    deadline = time.time() + 10
    while time.time() < deadline:
        if page.input_value(sel):
            break
        page.wait_for_timeout(100)

``wait:`` was widened rather than given a ``load-state:`` twin: the field already
exists with exactly that vocabulary, and 80 blocks in the other project spell the
same thing as a bare ``wait_for_load_state`` beside their real wait.
"""

import ast
import re
import time
from pathlib import Path
from textwrap import dedent

import pytest

from steppy.config import load_config
from steppy.generator import SteppyGenerator
from steppy.models import Step
from steppy.parser import parse_scenario_file

_CONFTEST = Path(__file__).resolve().parents[1] / "templates" / "conftest.py.j2"


def _load_wait_until():
    """Exec the real ``_wait_until`` out of the canonical template.

    Read from ``templates/`` rather than the generated ``src/steppy/templates/``
    copy so this passes on a fresh clone, before any build.
    """
    text = _CONFTEST.read_text(encoding="utf-8")
    start = text.index("_WAIT_COMPARATOR = re.compile")
    end = text.index("def synthetic_click(")
    ns: dict = {"re": re, "time": time}
    exec(compile(text[start:end], "<wait_until>", "exec"), ns)
    return ns["_wait_until"]


wait_until = _load_wait_until()


class _FakeLocator:
    """Yields a scripted sequence of observations, one per poll."""

    def __init__(self, values=None, texts=None, counts=None):
        self._values = list(values or [])
        self._texts = list(texts or [])
        self._counts = list(counts or [])
        self.polls = 0

    def _next(self, seq):
        self.polls += 1
        return seq.pop(0) if len(seq) > 1 else seq[0]

    @property
    def first(self):
        return self

    def input_value(self, timeout=None):
        return self._next(self._values)

    def inner_text(self, timeout=None):
        return self._next(self._texts)

    def count(self):
        return self._next(self._counts)


# ── The runtime helper ──────────────────────────────────────────────────────


def test_returns_as_soon_as_the_input_is_populated():
    loc = _FakeLocator(values=["", "", "Chanly"])
    wait_until(loc, "value-not-empty", timeout=5000, interval=1)
    assert loc.polls == 3


def test_value_predicate_waits_for_the_exact_value():
    loc = _FakeLocator(values=["", "wrong", "15/03/1990"])
    wait_until(loc, "value:15/03/1990", timeout=5000, interval=1)


def test_text_predicate_is_a_substring_match():
    wait_until(_FakeLocator(texts=["loading", "All done here"]), "text:done", timeout=5000, interval=1)


def test_count_predicate_honours_the_comparator():
    wait_until(_FakeLocator(counts=[0, 1, 2]), "count:>=2", timeout=5000, interval=1)
    wait_until(_FakeLocator(counts=[3, 0]), "count:==0", timeout=5000, interval=1)


def test_a_predicate_that_never_holds_raises_with_what_it_last_saw():
    loc = _FakeLocator(values=[""])
    with pytest.raises(AssertionError, match=r"until='value-not-empty' not satisfied within 30ms"):
        wait_until(loc, "value-not-empty", timeout=30, interval=1)


def test_a_count_assertion_passes_once_the_row_arrives():
    """What ``count(sel, >=1)`` now generates: a row that renders late passes,
    where the old one-shot ``.count()`` read 0 and failed."""
    loc = _FakeLocator(counts=[0, 0, 1])
    wait_until(loc, "count:>=1", 5000, interval=1, message="expected count >= 1")
    assert loc.polls == 3


def test_a_count_assertion_that_times_out_reports_the_last_count():
    with pytest.raises(AssertionError, match=r"^expected count >= 2 \(last saw 1 after 30ms\)$"):
        wait_until(_FakeLocator(counts=[1]), "count:>=2", 30, interval=1, message="expected count >= 2")


def test_an_element_that_does_not_exist_yet_is_polled_not_raised():
    """A locator raising on the first poll is the normal case: the row the step
    is waiting for has not rendered. Only the deadline ends the wait."""

    class _Late(_FakeLocator):
        def __init__(self):
            super().__init__(counts=[1])
            self.calls = 0

        def count(self):
            self.calls += 1
            if self.calls < 3:
                raise RuntimeError("locator not resolved")
            return 1

    wait_until(_Late(), "count:>=1", timeout=5000, interval=1)


# ── Codegen ─────────────────────────────────────────────────────────────────


@pytest.fixture
def gen_step(tmp_path):
    def _gen(body: str) -> str:
        path = tmp_path / "scenario.md"
        path.write_text("---\ntitle: T\nauth: false\n---\n\n" + dedent(body), encoding="utf-8")
        src = SteppyGenerator(load_config()).generate_test_file(parse_scenario_file(path))
        ast.parse(src)
        return src

    return _gen


def test_a_plain_wait_for_is_unchanged(gen_step):
    src = gen_step("""
    ### 1. W
    - role: editor
    - action: wait-for
    - selector: '#a'
    - state: visible
    """)
    assert "editor_page.wait_for_selector(_interp('#a', _vars), timeout=30000, state='visible')" in src
    assert "_wait_until" not in src.split("def test_")[1]


def test_until_emits_the_helper_with_the_resolved_timeout(gen_step):
    src = gen_step("""
    ### 1. W
    - role: editor
    - action: wait-for
    - selector: '#lbl'
    - until: value-not-empty
    - timeout: 10s
    """)
    assert "_wait_until(editor_page.locator(_interp('#lbl', _vars)), 'value-not-empty', 10000)" in src


def test_interval_is_reused_as_the_poll_period(gen_step):
    """`interval:` already means "retry period" for `capture`; a `poll:` field
    would be a second spelling of one concept."""
    src = gen_step("""
    ### 1. W
    - role: editor
    - action: wait-for
    - selector: '#rows'
    - until: 'count:>=2'
    - timeout: 15s
    - interval: 250ms
    """)
    assert "'count:>=2', 15000, interval=250)" in src


def test_until_composes_with_disambiguation(gen_step):
    src = gen_step("""
    ### 1. W
    - role: editor
    - action: wait-for
    - selector: '.cell'
    - within: '#panel'
    - until: value-not-empty
    """)
    assert "_wait_until(editor_page.locator(_interp('#panel', _vars)).locator(_interp('.cell', _vars))" in src


def test_wait_load_state_now_rides_on_wait_for(gen_step):
    src = gen_step("""
    ### 1. W
    - role: editor
    - action: wait-for
    - selector: '#a'
    - wait: networkidle
    """)
    assert "editor_page.wait_for_load_state('networkidle')" in src


# ── Validation ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("spec", ["value-not-empty", "value:15/03/1990", "text:Done", "count:>=2", "count:==0"])
def test_valid_predicates_are_accepted(spec):
    assert Step(action="wait-for", role="editor", selector="#a", until=spec).until == spec


@pytest.mark.parametrize("spec", ["ready", "count:many", "value:", "count:"])
def test_an_unknown_predicate_fails_at_validate(spec):
    """`_wait_until` raises on an unknown predicate, but only once that step is
    reached at run time — and only if it is reached at all."""
    with pytest.raises(ValueError, match="until"):
        Step(action="wait-for", role="editor", selector="#a", until=spec)


def test_until_is_rejected_on_actions_that_do_not_wait():
    with pytest.raises(ValueError, match="does not accept"):
        Step(action="click", role="editor", selector="#a", until="value-not-empty")


def test_wait_is_still_rejected_where_there_is_no_navigation():
    with pytest.raises(ValueError, match="only valid on navigate and wait-for|does not accept"):
        Step(action="click", role="editor", selector="#a", wait="networkidle")
