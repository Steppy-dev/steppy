"""``steppy migrate custom`` — the codemod.

New actions do not shrink the custom blocks a project already has. Measured on
the corpus that motivated this round, the rules here convert 2 455 of one
project's 5 032 blocks (49%) and 5 of the other's 251 (2%) — the second number
is the honest one: that project's residue is genuinely imperative, and its own
``kept custom`` notes say so.

The load-bearing property is conservatism. A block converts only when every
statement maps to a rule AND the rewrite passes verification, and everything
else is left exactly as it was. A codemod that half-converts is worse than none:
the scenario still looks complete.
"""

import ast
from textwrap import dedent

import pytest

from steppy.config import load_config
from steppy.generator import SteppyGenerator
from steppy.migrate import apply_plan, plan_file
from steppy.migrate.plan import _fold
from steppy.migrate.rules import parse_block, split_statements
from steppy.migrate.verify import verify_rewrite
from steppy.parser import parse_scenario_file


# ── Statement splitting ─────────────────────────────────────────────────────


def test_semicolons_split():
    """3 984 of one project's blocks are a single line chaining statements."""
    assert split_statements("page.click('#a'); page.click('#b')") == [
        "page.click('#a')",
        "page.click('#b')",
    ]


def test_a_top_level_tuple_is_two_statements():
    """`a(...), b(...)` is Python's comma operator, used in 74 blocks."""
    assert split_statements("page.locator('#a').wait_for(timeout=1), ensure_open(page, 'x')") == [
        "page.locator('#a').wait_for(timeout=1)",
        "ensure_open(page, 'x')",
    ]


def test_a_comma_inside_a_call_is_not_a_split():
    assert split_statements("page.fill('#a', 'b, c')") == ["page.fill('#a', 'b, c')"]


def test_an_asserts_message_comma_is_not_a_split():
    """Splitting here silently threw away the message, which 78% of the
    corpus's assert blocks carry."""
    assert split_statements('assert page.locator("#a").count() > 0, "why"') == [
        'assert page.locator("#a").count() > 0, "why"'
    ]


# ── Rules ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "code,expected",
    [
        (
            "page.locator('#a').click(force=True)",
            [{"action": "click", "selector": "#a", "force": True}],
        ),
        (
            "page.locator('#a').first.click()",
            [{"action": "click", "selector": "#a", "match": "first"}],
        ),
        (
            "page.locator('#cb').check(force=True)",
            [{"action": "toggle", "selector": "#cb", "control": "self", "state": "on"}],
        ),
        (
            "page.locator('#a').wait_for(state='visible', timeout=10000)",
            [{"action": "wait-for", "selector": "#a", "state": "visible", "timeout": 10000}],
        ),
        (
            "page.goto('/orders')",
            [{"action": "navigate", "target": "/orders"}],
        ),
        (
            "assert page.locator('#r').count() > 0",
            [{"action": "assert", "expect": "count(#r, >0)"}],
        ),
    ],
)
def test_each_rule_produces_the_step_an_author_would_write(code, expected):
    assert parse_block(code) == expected


def test_the_syspath_preamble_is_dropped_and_the_helper_becomes_a_call():
    """The preamble opens 2 334 blocks and was never necessary."""
    assert parse_block(
        "import sys; sys.path.insert(0, '.'); sys.path.insert(0, '/steppy/project'); "
        "from schedule_helpers import save; save(page, wait_after=3000); None"
    ) == [{"action": "call", "fn": "schedule_helpers.save", "kwargs": {"wait_after": 3000}}]


def test_helper_arguments_keep_their_types():
    assert parse_block("from list_helpers import select_status; select_status(page, 'orders', 6)") == [
        {"action": "call", "fn": "list_helpers.select_status", "args": ["orders", 6]}
    ]


def test_an_assert_message_is_carried_into_the_expect_list():
    assert parse_block('assert page.locator("#r").count() == 0, "no rows should remain"') == [
        {"action": "assert", "expect": [{"check": "count(#r, ==0)", "message": "no rows should remain"}]}
    ]


def test_an_f_string_message_stays_custom():
    """It interpolates the observed value, which a step cannot express."""
    assert parse_block('assert page.locator("#r").count() == 0, f"saw {n}"') is None


def test_a_comma_bearing_selector_stays_custom():
    """`count(#a, #b, >=1)` has no reading in the expect: grammar."""
    assert parse_block("assert page.locator('.a, .b').count() == 0") is None


@pytest.mark.parametrize(
    "code",
    [
        "oid = globals()['order_id']",  # cross-step state
        "today = datetime.date.today()",  # computed value
        "for row in rows: row.click()",  # imperative
        "helper_never_imported(page)",  # unknown module
        "page.locator('#a').evaluate('el => el.click()')",  # white-box JS
    ],
)
def test_anything_a_rule_does_not_cover_leaves_the_whole_block_alone(code):
    """All-or-nothing: a half-converted block would look complete."""
    assert parse_block(code) is None


# ── Folds ───────────────────────────────────────────────────────────────────


def test_a_trailing_sleep_folds_into_the_previous_step():
    """Otherwise 420 pure-wait blocks would ADD 121 steps, and in a guide every
    one is a published step with its own number and screenshot slot."""
    folded = _fold(parse_block("page.locator('#a').click(); page.wait_for_timeout(800)"))
    assert folded == [{"action": "click", "selector": "#a", "wait_after": 800}]


def test_a_leading_sleep_stays_a_step_of_its_own():
    folded = _fold(parse_block("page.wait_for_timeout(800); page.locator('#a').click()"))
    assert folded[0] == {"action": "wait", "duration": "800ms"}


def test_a_load_state_wait_folds_into_the_navigate_before_it():
    folded = _fold(parse_block("page.goto('/o'); page.wait_for_load_state('networkidle')"))
    assert folded == [{"action": "navigate", "target": "/o", "wait": "networkidle"}]


# ── Verification ────────────────────────────────────────────────────────────


def test_a_rewrite_that_drops_a_selector_is_refused():
    result = verify_rewrite(
        "page.click('#a')",
        "page.click(_interp('#b', _vars))",
        [{"action": "click", "selector": "#b"}],
    )
    assert not result.ok and "would drop" in result.detail


def test_a_rewrite_that_stops_calling_something_is_refused():
    result = verify_rewrite(
        "page.click('#a')",
        "page.hover(_interp('#a', _vars))",
        [{"action": "hover", "selector": "#a"}],
    )
    assert not result.ok and "stop calling click" in result.detail


def test_generated_code_that_does_not_parse_is_refused():
    result = verify_rewrite("page.click('#a')", "page.click(", [{"action": "click"}])
    assert not result.ok and "does not parse" in result.detail


def test_a_deliberate_improvement_is_allowed_and_named():
    """`check` -> idempotent `toggle` changes behaviour on purpose; the reason
    is reported rather than hidden."""
    result = verify_rewrite(
        "page.locator('#cb').check(force=True)",
        "_switch = page.locator(_interp('#cb', _vars))\nif not _switch.is_checked():\n    _switch.check(force=True)",
        [{"action": "toggle", "selector": "#cb", "control": "self", "state": "on"}],
    )
    assert result.ok


def test_a_one_shot_count_may_become_a_polling_assertion():
    """`count(sel, >0)` generates `_wait_until(...)`, not `.count()`; the
    assert-count rule still verifies, and says why."""
    result = verify_rewrite(
        "assert page.locator('#r').count() > 0",
        "_wait_until(page.locator(_interp('#r', _vars)), 'count:>0', 30000, message='expected count > 0')",
        [{"action": "assert", "expect": "count(#r, >0)"}],
    )
    assert result.ok
    assert any(s.startswith("count:") for s in result.substitutions)


def test_escaped_and_plain_quotes_name_the_same_selector():
    """A custom body writes `'[data-testid=\\"row\\"]'`; a step writes
    `'[data-testid="row"]'`. Comparing source text would reject 1 458 rewrites."""
    result = verify_rewrite(
        r"""page.click('[data-testid=\"row\"]')""",
        """page.click(_interp('[data-testid="row"]', _vars))""",
        [{"action": "click", "selector": '[data-testid="row"]'}],
    )
    assert result.ok


def test_a_triple_quoted_docstring_does_not_confuse_the_literal_scan():
    """A regex scanner read `\"\"\"` as an empty string and mis-tokenised the rest
    of the file, reporting selectors as dropped that were still there."""
    generated = '"""\nAuto-generated, mentions #a nowhere useful\n"""\npage.click(_interp("#a", _vars))\n'
    assert verify_rewrite('page.click("#a")', generated, [{"action": "click", "selector": "#a"}]).ok


# ── End to end ──────────────────────────────────────────────────────────────

_SCENARIO = """\
---
title: Demo
auth: false
---

### 1. Click the tab
- role: editor
- action: custom
- code: "editor_page.locator('[data-testid=\\"tab\\"]').click(force=True)"
- note: why this exists

### 2. Wait then click
- role: editor
- action: custom
- code: |
    editor_page.locator('#row').first.wait_for(state='visible', timeout=15000)
    editor_page.locator('#row').first.click(force=True)
    editor_page.wait_for_timeout(800)

### 3. Not convertible
- role: editor
- action: custom
- code: "oid = globals()['x']; editor_page.goto('/o/1')"
"""


@pytest.fixture
def demo(tmp_path):
    path = tmp_path / "demo.md"
    path.write_text(_SCENARIO, encoding="utf-8")
    return path


def test_dry_run_writes_nothing(demo):
    before = demo.read_text(encoding="utf-8")
    apply_plan(plan_file(demo), SteppyGenerator(load_config()), write=False)
    assert demo.read_text(encoding="utf-8") == before


def test_write_converts_only_the_verified_blocks(demo):
    outcomes = apply_plan(plan_file(demo), SteppyGenerator(load_config()), write=True)
    assert [o.applied for o in outcomes] == [True, True, False]
    text = demo.read_text(encoding="utf-8")
    assert text.count("- action: custom") == 1
    assert "globals()" in text  # the one left alone is untouched


def test_the_rewritten_scenario_still_generates_valid_python(demo):
    apply_plan(plan_file(demo), SteppyGenerator(load_config()), write=True)
    src = SteppyGenerator(load_config()).generate_test_file(parse_scenario_file(demo))
    ast.parse(src)


def test_a_note_survives_and_lands_on_the_first_fragment_only(demo):
    apply_plan(plan_file(demo), SteppyGenerator(load_config()), write=True)
    text = demo.read_text(encoding="utf-8")
    assert text.count("- note: why this exists") == 1


def test_a_multi_operation_block_becomes_numbered_fragments(demo):
    apply_plan(plan_file(demo), SteppyGenerator(load_config()), write=True)
    text = demo.read_text(encoding="utf-8")
    assert "### 2. Wait then click (1/2)" in text
    assert "### 2. Wait then click (2/2)" in text
    # the trailing sleep folded rather than becoming a third fragment
    assert "(3/3)" not in text
    assert "- wait_after: 800" in text


def test_nested_blocks_are_carried_across_unharmed(tmp_path):
    """`guide-cursor:` is a nested dict, not a scalar; losing it made the
    proposed step invalid."""
    path = tmp_path / "g.md"
    path.write_text(
        dedent("""\
        ---
        title: G
        auth: false
        ---

        ### 1. Click
        - role: editor
        - action: custom
        - code: "editor_page.locator('#a').click(force=True)"
        - guide-cursor:
            behavior: click
            target: '#a'
        """),
        encoding="utf-8",
    )
    apply_plan(plan_file(path), SteppyGenerator(load_config()), write=True)
    text = path.read_text(encoding="utf-8")
    assert "- guide-cursor:" in text and "behavior: click" in text
    assert parse_scenario_file(path).steps[0].guide_cursor.target == "#a"
