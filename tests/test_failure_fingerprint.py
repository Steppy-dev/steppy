"""Failure fingerprinting: same cause ⇒ same bucket.

One broken selector across forty scenarios currently prints forty stack traces.
Grouping turns that into one line. The whole thing hinges on the normalizer
ignoring what differs between two runs of the same bug (timings, ids, ports,
worker paths) while keeping what identifies it.

The bias is deliberate and tested here: over-merging costs the reader one extra
glance, under-merging puts them back to scrolling.
"""

from __future__ import annotations

from steppy._parallel.fingerprint import (
    FailureGroup,
    fingerprint,
    group_failures,
    headline,
    normalize_tail,
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
