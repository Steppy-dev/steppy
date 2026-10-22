"""Exclusion groups: the frontmatter field, the name grammar, and the partition.

The scheduling half — that two members of a group never overlap — is exercised in
``tests/test_parallel_queue.py``, where a real ``Queue`` on ``tmp_path`` can be
driven by threads.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from steppy.exclusive import (
    ExclusiveGroupSyntaxError,
    Task,
    duplicate_task_ids,
    normalize_group_name,
    partition,
)
from steppy.models import Scenario
from steppy.parser import lint_exclusive_groups


# ── Name grammar ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", ["billing", "b", "stripe-sandbox", "db.primary", "a_b", "x+y", "s3", "0day"])
def test_valid_group_names(name):
    assert normalize_group_name(name) == name


def test_group_name_is_stripped():
    assert normalize_group_name("  billing  ") == "billing"


@pytest.mark.parametrize(
    "name",
    [
        "",
        "   ",
        "..",
        ".",
        "a/b",
        "a\\b",
        ".hidden",
        "-lead",
        "_lead",
        "with space",
        "Billing",  # uppercase: would merge with `billing` on NTFS/APFS
        "BILLING",
        "x" * 65,
    ],
)
def test_rejected_group_names(name):
    with pytest.raises(ExclusiveGroupSyntaxError):
        normalize_group_name(name)


@pytest.mark.parametrize("name", ["con", "prn", "aux", "nul", "com1", "com9", "lpt1", "lpt9"])
def test_windows_reserved_device_names_rejected(name):
    """The lock lives on the HOST filesystem, so `con.claim` is un-creatable
    there — and it would surface as a Docker error, not a steppy one."""
    with pytest.raises(ExclusiveGroupSyntaxError, match="reserved device name"):
        normalize_group_name(name)


def test_traversal_is_rejected_not_sanitised():
    """The name becomes a filename, so this is a boundary, not a style rule."""
    with pytest.raises(ExclusiveGroupSyntaxError):
        normalize_group_name("../../../etc/cron.d/x")


def test_list_value_is_rejected_with_the_closure_rule():
    """Naming the *combination* of two resources is unsound: `stripe-and-mailbox`
    does not exclude against `stripe`. The message must prescribe the closure
    rule instead, or following it produces the very bug the field prevents."""
    with pytest.raises(ExclusiveGroupSyntaxError) as exc:
        normalize_group_name(["stripe", "mailbox"])
    assert "SAME" in str(exc.value)
    assert "does not exclude" in str(exc.value)


# ── The frontmatter field ───────────────────────────────────────────────────


def test_scenario_accepts_and_normalises_exclusive():
    assert Scenario(title="t", exclusive=" billing ").exclusive == "billing"


def test_scenario_without_exclusive_is_none():
    assert Scenario(title="t").exclusive is None


def test_scenario_rejects_bad_group_name():
    with pytest.raises(ValidationError):
        Scenario(title="t", exclusive="Billing")


def test_scenario_still_forbids_unknown_keys():
    """extra = "forbid" must keep catching a typo'd key — adding a field must not
    open the door to `exclusiv:` silently doing nothing."""
    with pytest.raises(ValidationError):
        Scenario(title="t", exclusiv="billing")


# ── Partition ───────────────────────────────────────────────────────────────


def test_partition_with_no_groups_is_one_singleton_per_slug():
    """This is what keeps the seeded queue directory byte-identical."""
    tasks = partition(["a", "b", "c"], {})
    assert tasks == [
        Task(task_id="a", slugs=("a",), group=None),
        Task(task_id="b", slugs=("b",), group=None),
        Task(task_id="c", slugs=("c",), group=None),
    ]
    assert not any(t.is_batch for t in tasks)


def test_partition_collapses_a_group_at_its_first_member_position():
    tasks = partition(
        ["s1", "s2", "s3", "s4", "s5"],
        {"s1": "billing", "s3": "billing", "s2": "mail", "s5": "mail"},
    )
    assert [(t.task_id, t.slugs) for t in tasks] == [
        ("billing", ("s1", "s3")),
        ("mail", ("s2", "s5")),
        ("s4", ("s4",)),
    ]


def test_partition_preserves_member_order():
    tasks = partition(["c", "a", "b"], {"c": "g", "a": "g", "b": "g"})
    assert tasks[0].slugs == ("c", "a", "b")


def test_partition_treats_empty_group_as_ungrouped():
    tasks = partition(["a"], {"a": None})
    assert tasks == [Task(task_id="a", slugs=("a",), group=None)]


# ── Task-id uniqueness ──────────────────────────────────────────────────────


def test_no_duplicates_for_a_clean_partition():
    assert duplicate_task_ids(partition(["a", "b"], {"a": "g"})) == []


def test_case_differing_task_ids_collide():
    """On NTFS/APFS these share one claim file, so a whole task would silently
    never run while the run still exited 0. Linux CI would not catch it."""
    tasks = [Task("payments", ("a",), "payments"), Task("Payments", ("b",), "Payments")]
    assert duplicate_task_ids(tasks) == [("payments", "Payments")]


def test_a_group_named_after_its_own_member_is_fine():
    """`exclusive: checkout` on a scenario slugged `checkout` is the most natural
    name there is — it yields ONE task id, so it must not be rejected."""
    tasks = partition(["checkout", "refund"], {"checkout": "checkout", "refund": "checkout"})
    assert len(tasks) == 1
    assert duplicate_task_ids(tasks) == []


def test_a_group_named_after_an_ungrouped_slug_collides():
    tasks = partition(["checkout", "refund"], {"refund": "checkout"})
    assert duplicate_task_ids(tasks) == [("checkout", "checkout")]


# ── The singleton lint ──────────────────────────────────────────────────────


def _scenario(slug: str, group=None) -> Scenario:
    return Scenario(title=slug, slug=slug, exclusive=group)


def test_lint_warns_on_a_single_member_group():
    """The typo direction is the dangerous one: the constraint evaporates rather
    than over-applying, and nothing else prints."""
    warnings = lint_exclusive_groups(
        [_scenario("a", "stripe-sandbox"), _scenario("b", "stripe-sandbx")],
    )
    assert len(warnings) == 2
    assert any("stripe-sandbx" in w for w in warnings)


def test_lint_is_quiet_for_a_real_group():
    assert lint_exclusive_groups([_scenario("a", "billing"), _scenario("b", "billing")]) == []


def test_lint_is_quiet_with_no_groups():
    assert lint_exclusive_groups([_scenario("a"), _scenario("b")]) == []
