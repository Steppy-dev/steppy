"""Guards for the ``expect:`` grammar and the inverse allowed-field table.

Both close the same failure shape: a scenario that passes ``steppy validate``
and generates code that checks nothing.
"""

from textwrap import dedent

import pytest

from steppy.assertions import AssertionSyntaxError, parse_assertion, split_top_level
from steppy.generator import SteppyGenerator
from steppy.models import _ALLOWED_FIELDS, _UNIVERSAL_FIELDS, VALID_ACTIONS, Step

# ── The grammar itself ──────────────────────────────────────────────────────

# Every form the template's `assert_body` macro has a branch for, with the exact
# descriptor codegen expects. If a row here changes, generated output changes.
_VALID = [
    ('toast("Saved")', {"type": "toast", "message": "Saved"}),
    ("visible(#a)", {"type": "visible", "selector": "#a"}),
    ("hidden(#a)", {"type": "hidden", "selector": "#a"}),
    ('text(#a, "Hi")', {"type": "text", "selector": "#a", "text": "Hi"}),
    ('contains(#a, "Hi")', {"type": "contains", "selector": "#a", "text": "Hi"}),
    ('url-contains("/orders")', {"type": "url-contains", "path": "/orders"}),
    ("count(#a, 3)", {"type": "count", "selector": "#a", "count": "3"}),
    ("count(#a, >=2)", {"type": "count-cmp", "selector": "#a", "comparator": ">=", "count": "2"}),
    ('row-contains("Bob")', {"type": "row-contains", "text": "Bob"}),
    ("not-empty(#a)", {"type": "not-empty", "selector": "#a"}),
    ("disabled(#a)", {"type": "disabled", "selector": "#a"}),
    ("enabled(#a)", {"type": "enabled", "selector": "#a"}),
]


@pytest.mark.parametrize("expect,descriptor", _VALID)
def test_valid_forms_parse_to_the_descriptor_codegen_expects(expect, descriptor):
    assert parse_assertion(expect) == descriptor


@pytest.mark.parametrize("expect,descriptor", _VALID)
def test_generator_delegates_to_the_shared_parser(expect, descriptor):
    """One grammar, so ``steppy validate`` and codegen cannot disagree."""
    assert SteppyGenerator._format_assertion(None, expect) == descriptor


@pytest.mark.parametrize(
    "expect",
    [
        'status("done")',  # parsed before this change, had no template branch -> `pass`
        "not-hidden(#a)",  # plausible, never existed
        "visible(#a",  # missing close paren silently ate a character
        "contains(#a)",  # one argument
        "count(#a, >={{n}})",  # emitted `to_have_count(>={{n}})` -> SyntaxError
        "count(#a, many)",
        "",
        "   ",
    ],
)
def test_unrecognised_forms_raise_instead_of_generating_pass(expect):
    with pytest.raises(AssertionSyntaxError):
        parse_assertion(expect)


# Round 2 forms. Each was a silently-passing assertion before it existed: an
# unrecognised `expect:` fell through to `pass`.
_ROUND_2 = [
    ('value(#a, "15/03/1990")', {"type": "value", "selector": "#a", "text": "15/03/1990"}),
    (
        'attribute(#a, "aria-expanded", "true")',
        {"type": "attribute", "selector": "#a", "attribute": "aria-expanded", "text": "true"},
    ),
    ('not-contains(#a, "Bob")', {"type": "not-contains", "selector": "#a", "text": "Bob"}),
    ("not-visible(#a)", {"type": "not-visible", "selector": "#a"}),
    ('icontains(#a, "Approbation")', {"type": "icontains", "selector": "#a", "text": "Approbation"}),
    ("checked(#a)", {"type": "checked", "selector": "#a"}),
    ("unchecked(#a)", {"type": "unchecked", "selector": "#a"}),
    ('stays-hidden(#a, "3s")', {"type": "stays-hidden", "selector": "#a", "duration": "3s"}),
]


@pytest.mark.parametrize("expect,descriptor", _ROUND_2)
def test_round_2_forms_parse(expect, descriptor):
    assert parse_assertion(expect) == descriptor


_ALL_FORMS = [e for e, _ in _VALID] + [e for e, _ in _ROUND_2]


@pytest.mark.parametrize("expect", _ALL_FORMS)
def test_every_parsed_form_generates_real_code_not_pass(expect, tmp_path):
    """The drift guard for the whole grammar.

    A form the parser accepts but ``assert_body`` has no branch for renders the
    macro's ``{% else %}`` — a bare ``pass``. That is exactly how
    ``status("done")`` shipped: it parsed, validated, generated, and asserted
    nothing, with every suite green. Parsing and rendering must stay in step.
    """
    from steppy.config import load_config
    from steppy.generator import SteppyGenerator
    from steppy.parser import parse_scenario_file

    path = tmp_path / "s.md"
    path.write_text(
        dedent(f"""\
            ---
            title: T
            auth: false
            ---

            ### 1. A
            - role: editor
            - action: assert
            - expect: {expect}
            """),
        encoding="utf-8",
    )
    src = SteppyGenerator(load_config()).generate_test_file(parse_scenario_file(path))
    body = src[src.index("if not SKIP_ASSERTS:") :]
    assert "Unknown assertion" not in body
    first = body.splitlines()[1].strip()
    assert first != "pass", f"{expect} rendered `pass` — the macro has no branch for it"


def test_unrecognised_expect_is_a_validation_error_on_the_step():
    """The whole point: it fails at `steppy validate`, not silently at run time."""
    with pytest.raises(ValueError, match="unknown assertion"):
        Step(action="assert", role="editor", expect='status("done")')


def test_a_valid_expect_still_builds():
    assert Step(action="assert", role="editor", expect='toast("ok")').expect == 'toast("ok")'


def test_expect_riding_on_another_action_is_validated_too():
    with pytest.raises(ValueError, match="unknown assertion"):
        Step(action="click", role="editor", selector="#a", expect="nope(#a)")


# ── The comma trap ──────────────────────────────────────────────────────────


def test_a_comma_inside_an_attribute_selector_no_longer_splits_the_selector():
    """`inner.split(",", 1)` cut this selector in half and handed codegen junk."""
    assert parse_assertion('contains([data-testid="a,b"], "Hi")') == {
        "type": "contains",
        "selector": '[data-testid="a,b"]',
        "text": "Hi",
    }


def test_a_comma_inside_the_expected_text_is_kept():
    assert parse_assertion('contains(#a, "one, two")')["text"] == "one, two"


def test_split_top_level_respects_quotes_and_brackets():
    assert split_top_level('[a="x,y"], "z"', maxsplit=1) == ['[a="x,y"]', ' "z"']


# ── The inverse allowed-field table ─────────────────────────────────────────


def test_every_action_has_a_row():
    """A new action without a row would accept every field silently."""
    assert set(_ALLOWED_FIELDS) == set(VALID_ACTIONS)


def test_a_field_belonging_to_another_action_is_rejected():
    """`extra = "forbid"` never caught this: `date` is a real field, on fill-date."""
    with pytest.raises(ValueError, match="click action does not accept"):
        Step(action="click", role="editor", selector="#a", date="today")


def test_the_error_names_every_stray_field_at_once():
    with pytest.raises(ValueError) as exc:
        Step(action="click", role="editor", selector="#a", date="today", variant="reject")
    assert "'date'" in str(exc.value) and "'variant'" in str(exc.value)


def test_the_error_uses_the_spelling_the_author_typed():
    """`blur_key` is authored as `blur-key`; the message must say so."""
    with pytest.raises(ValueError, match="'blur-key'"):
        Step(action="click", role="editor", selector="#a", **{"blur-key": "Tab"})


def test_explicit_none_is_not_a_stray_field():
    """Programmatic construction may spell out the full signature."""
    assert Step(action="click", role="editor", selector="#a", date=None, variant=None).action == "click"


@pytest.mark.parametrize(
    "action,body",
    [
        ("click", dict(role="n", selector="#a", within="#s", match="first", synthetic=True)),
        ("fill", dict(role="n", selector="#a", value="v", clear=True, sequential=True)),
        ("navigate", dict(role="n", target="/x", wait="load")),
        ("select-option", dict(role="n", selector="#a", search="q", option="first")),
        ("confirm", dict(role="n", variant="reject")),
        ("toggle", dict(role="n", selector="#a", state="on")),
        (
            "capture",
            dict(
                role="n",
                store="v",
                retry=3,
                interval="500ms",
                **{"from": {"type": "dom", "selector": "#a", "extract": "text"}},
            ),
        ),
        ("wait-for", dict(role="n", selector="#a", state="hidden", match="last")),
        ("pick-time", dict(role="n", selector="#a", time="10:00")),
    ],
)
def test_each_action_still_accepts_its_own_fields(action, body):
    assert Step(action=action, **body).action == action


@pytest.mark.parametrize(
    "field", sorted(_UNIVERSAL_FIELDS - {"action", "step_number", "step_title", "step_label", "expect"})
)
def test_universal_fields_are_accepted_everywhere(field):
    value = {
        "role": "editor",
        "screenshot": True,
        "screenshot_when": "before",
        "guide_text": {"en": "hi"},
        "guide_cursor": {"behavior": "click", "target": "#a"},
        "only_in": "test",
        "layouts": ["desktop"],
        "wait_after": 500,
        "note": "why",
        "optional": True,
        "when": "visible(#a)",
        "tour": "keep",
        "tour_text": {"en": "hi"},
        "tour_advance": "manual",
        "tour_placement": "top",
    }[field]
    if field == "when":
        # `when:` reads the page, so it needs a role — and `wait` is the one
        # action exempt from having one. It is exercised on a role-bearing
        # action instead; every other universal is checked against `wait`,
        # deliberately the most restrictive action there is.
        assert Step(action="click", role="editor", selector="#a", **{field: value}).action == "click"
    else:
        assert Step(action="wait", duration="1s", **{field: value}).action == "wait"
