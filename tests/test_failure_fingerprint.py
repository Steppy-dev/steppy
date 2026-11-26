"""Failure fingerprinting: same cause ⇒ same bucket.

One broken selector across forty scenarios currently prints forty stack traces.
Grouping turns that into one line. The whole thing hinges on the normalizer
ignoring what differs between two runs of the same bug (timings, ids, ports,
worker paths) while keeping what identifies it.

The bias is deliberate and tested here: over-merging costs the reader one extra
glance, under-merging puts them back to scrolling.
"""

from __future__ import annotations

import pytest

from steppy._parallel.fingerprint import (
    FailureGroup,
    fingerprint,
    group_failures,
    headline,
    normalize_tail,
    without_paths,
)


class _Row:
    """Minimal stand-in for renderer._SummaryRow."""

    def __init__(self, slug, fail_tails):
        self.slug = slug
        self.fail_tails = fail_tails


# ── normalization ───────────────────────────────────────────────────────────


def test_same_error_different_timeout_matches():
    a = "TimeoutError: Timeout 30000ms exceeded waiting for [data-testid=toast]"
    b = "TimeoutError: Timeout 45000ms exceeded waiting for [data-testid=toast]"
    assert fingerprint(a) == fingerprint(b)


def test_same_error_different_worker_path_matches():
    a = 'File "/steppy/project/tests/e2e/test_login.py", line 42, in test_login'
    b = 'File "D:\\proj\\tests\\e2e\\test_login.py", line 87, in test_login'
    assert fingerprint(a) == fingerprint(b)


def test_uuids_and_addresses_are_scrubbed():
    a = "AssertionError: order 3f2b8c1a-4d5e-6f70-8a9b-0c1d2e3f4a5b not found at 0x7f3a1b2c"
    b = "AssertionError: order 9a8b7c6d-5e4f-3a2b-1c0d-9e8f7a6b5c4d not found at 0x55e0f1a2"
    assert fingerprint(a) == fingerprint(b)


def test_timestamps_and_ports_are_scrubbed():
    a = "2026-07-27T09:15:03Z ConnectionError: http://app-frontend:8080 refused"
    b = "2026-07-28T22:01:59Z ConnectionError: http://app-frontend:3000 refused"
    assert fingerprint(a) == fingerprint(b)


def test_genuinely_different_errors_do_not_match():
    a = "TimeoutError: waiting for selector [data-testid=toast]"
    b = "AssertionError: expected 3 rows, found 0"
    assert fingerprint(a) != fingerprint(b)


def test_different_selectors_do_not_match():
    """Scrubbing must not go so far that two different broken selectors merge —
    that would hide a real second bug."""
    a = "TimeoutError: waiting for selector [data-testid=toast]"
    b = "TimeoutError: waiting for selector [data-testid=modal]"
    assert fingerprint(a) != fingerprint(b)


def test_pytest_gutter_prefix_is_stripped():
    """The same assertion reported in different phases carries a different
    gutter; it is still the same failure."""
    assert fingerprint("E   AssertionError: boom") == fingerprint("AssertionError: boom")


def test_empty_tail_is_a_stable_unknown():
    assert fingerprint("") == "unknown"
    assert fingerprint("   \n  ") == "unknown"


def test_normalize_collapses_whitespace_and_blank_lines():
    assert normalize_tail("foo   bar\n\n  baz  ") == "foo bar\nbaz"


# ── headline ────────────────────────────────────────────────────────────────


def test_headline_prefers_the_exception_line():
    tail = "self = <Page ...>\n  page.click(sel)\nTimeoutError: Timeout exceeded"
    assert headline(tail) == "TimeoutError: Timeout exceeded"


def test_headline_falls_back_to_last_line():
    assert headline("just\nsome\ncontext") == "context"


def test_headline_handles_no_detail():
    assert "no failure detail" in headline("")


def test_headline_truncates():
    assert headline("AssertionError: " + "x" * 500, max_len=40).endswith("…")


# What the two runners really leave: 15 lines of pytest's report.
ASSERT_TAIL = r"""        _steppy_end_step(2)

        # ── Step 3: Check the message ──────────────────────────────────
        _steppy_mark_step(3, 'Check the message', 'assert')
        if not SKIP_ASSERTS:
>           expect(customer_page.locator('[data-testid="toast"]')).not_to_contain_text('failed', timeout=1500)
E           AssertionError: Locator expected not to contain text 'failed'
E           Actual value: Export failed: 500
E           Call log:
E             - Expect "to_contain_text" with timeout 1500ms
E             - waiting for locator("[data-testid=\"toast\"]")
E               15 × locator resolved to <div class="toast" data-testid="toast">Export failed: 500</div>

tests\e2e\test_invoice-export.py:41: AssertionError"""

TIMEOUT_TAIL = r"""E             - attempting click action
E               2 × waiting for element to be visible, enabled and stable
E                 - element is not visible
E               - retrying click action
E                 - waiting 500ms

D:\work\agent\proj\.venv\Lib\site-packages\playwright\_impl\_connection.py:559: TimeoutError"""

SHORT_TAIL = r"""tests/e2e/test_pay.py:22: in test_pay
    customer_page.click('[data-testid="pay"]')
/home/runner/work/shop/.venv/lib/python3.12/site-packages/playwright/_impl/_connection.py:559: in send
    raise rewrite_error(error)
E   playwright._impl._errors.TimeoutError: Page.click: Timeout 1500ms exceeded.
E   Call log:
E     - waiting for locator("[data-testid=\"pay\"]")"""


@pytest.mark.parametrize(
    "tail,expected",
    [
        (ASSERT_TAIL, "AssertionError: Locator expected not to contain text 'failed'"),
        (TIMEOUT_TAIL, "TimeoutError: element is not visible"),
        (SHORT_TAIL, "TimeoutError: Page.click: Timeout 1500ms exceeded."),
        ("E       assert 1 == 2\nE        +  where 1 = f()\n\ntests/e2e/test_x.py:4: AssertionError", "assert 1 == 2"),
        ("E   AssertionError\n\ntests/e2e/test_x.py:4: AssertionError", "AssertionError"),
        ("E   EOFError", "EOFError"),
        ("E   KeyError: 'total'", "KeyError: 'total'"),
        ("Failed: Timeout >10.0s", "Failed: Timeout >10.0s"),
        (
            "E   AssertionError: Locator expected to be visible\nE   Actual value: None\nE   Error: element(s) not found",
            "AssertionError: Locator expected to be visible",
        ),
        (
            "E   KeyError: 'a'\n\nDuring handling of the above exception, another exception occurred:\n\n"
            "E   helpers.ShopError: the cart is empty",
            "ShopError: the cart is empty",
        ),
        ("x = 1\nplain words at the end", "plain words at the end"),
    ],
    ids=[
        "assert",
        "timeout",
        "short",
        "bare-assert",
        "bare-type",
        "eof",
        "key",
        "failed",
        "detail",
        "chained",
        "words",
    ],
)
def test_headline_says_what_broke(tail, expected):
    assert headline(tail) == expected


@pytest.mark.parametrize(
    "tail", ["_steppy_mark_step(3, 'x', 'assert')", "# ── Step 3: an assert ──\n_steppy_end_step(2)"]
)
def test_headline_is_never_a_line_steppy_generated(tail):
    assert headline(tail) == "(no failure detail captured)"


@pytest.mark.parametrize(
    "line,expected",
    [
        (r"cannot open C:\Users\ana\proj\logo.png", "cannot open logo.png"),
        (r"cannot open 'C:\\Users\\Ana Reyes\\proj\\logo.png'", "cannot open 'logo.png'"),
        ("cannot open C:/Users/ana/logo.png now", "cannot open logo.png now"),
        (r"cannot open \\server\share\dir\logo.png", "cannot open logo.png"),
        ("cannot open '/home/ana/proj/logo.png'", "cannot open 'logo.png'"),
        ("cannot open /data/ci/agent/fixtures", "cannot open fixtures"),
        ("cannot open '/srv-custom/ci/agent/fixtures'", "cannot open 'fixtures'"),
        ("at /weird/mount/x.py line 3", "at x.py line 3"),
        ("cwd=/home/ana/proj", "cwd=proj"),
        ("broke: file:///D:/a/b/page.html", "broke: page.html"),
        ("broke: file:///home/ana/page.html", "broke: page.html"),
        # Not paths of the machine.
        ("Page URL expected to be '/checkout/done'", "Page URL expected to be '/checkout/done'"),
        ("net::ERR at http://localhost:4200/app/login", "net::ERR at http://localhost:4200/app/login"),
        ("waiting for locator('xpath=/html/body/div')", "waiting for locator('xpath=/html/body/div')"),
        ("expected 1/2/3 and a/b", "expected 1/2/3 and a/b"),
        ("waiting for locator('img') in /home/ana/app/page.html", "waiting for locator('img') in page.html"),
        ("tests/e2e/test_x.py broke", "tests/e2e/test_x.py broke"),
        (r"tests\e2e\test_x.py broke", r"tests\e2e\test_x.py broke"),
        ("no path at all", "no path at all"),
    ],
)
def test_absolute_paths_are_reduced_to_their_last_part(line, expected):
    assert without_paths(line) == expected


@pytest.mark.parametrize("tail", [ASSERT_TAIL, TIMEOUT_TAIL, SHORT_TAIL])
def test_a_headline_has_no_absolute_path(tail):
    for text in (tail, f"E   OSError: {tail.splitlines()[-1]}"):
        assert not any(mark in headline(text) for mark in (":\\", "/home/", "site-packages"))


def test_the_headline_takes_no_part_in_grouping():
    """The ids are pinned: a report of an older run keeps its links to its causes."""
    assert [fingerprint(t) for t in (ASSERT_TAIL, TIMEOUT_TAIL, SHORT_TAIL)] == ["b8fafb55", "d03e04a7", "a2945558"]


# ── grouping ────────────────────────────────────────────────────────────────


def test_groups_identical_causes():
    rows = [_Row(f"scenario-{i}", [("n", "TimeoutError: waiting for [data-testid=toast]")]) for i in range(14)]
    rows.append(_Row("other", [("n", "AssertionError: expected 3 rows")]))
    groups = group_failures(rows)
    assert [g.count for g in groups] == [14, 1]
    assert len(groups[0].slugs) == 14


def test_groups_are_ordered_biggest_first():
    rows = [
        _Row("a", [("n", "AssertionError: one")]),
        _Row("b", [("n", "TimeoutError: two")]),
        _Row("c", [("n", "TimeoutError: two")]),
    ]
    assert group_failures(rows)[0].count == 2


def test_rows_without_tails_are_kept_not_dropped():
    """A failure with no captured detail is still a failure the reader must
    see — silently dropping it would under-report the run."""
    groups = group_failures([_Row("mystery", [])])
    assert len(groups) == 1
    assert groups[0].fingerprint == "unknown"
    assert groups[0].slugs == ["mystery"]


def test_slugs_are_deduplicated_within_a_group():
    """One scenario can contribute several failing tests; it should be named
    once in the group's slug list."""
    row = _Row("multi", [("n1", "TimeoutError: x"), ("n2", "TimeoutError: x")])
    group = group_failures([row])[0]
    assert group.count == 2
    assert group.slugs == ["multi"]


def test_grouping_is_deterministic():
    rows = [_Row("a", [("n", "E1")]), _Row("b", [("n", "E2")])]
    assert [g.fingerprint for g in group_failures(rows)] == [g.fingerprint for g in group_failures(rows)]


def test_empty_input_yields_no_groups():
    assert group_failures([]) == []


def test_failure_group_dataclass_defaults():
    assert FailureGroup(fingerprint="abc", headline="x").count == 0
