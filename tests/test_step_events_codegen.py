"""Codegen for the per-step markers.

The two marker calls are emitted by the step loop rather than by any action
branch, so a new action inherits them for free — but that also means a template
whitespace mistake lands in **every** step of **every** generated file at once.

The failure mode this file exists for is silent: get the newline handling wrong
and the end marker glues onto the previous statement, producing a file that
still parses and still runs, with `_steppy_end_step` swallowed as an argument
or a chained call. `ast.parse` cannot see that. Asserting on the emitted lines
can.
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

import pytest

from steppy.generator import SteppyGenerator
from steppy.models import AppAuthConfig, AppConfig, AuthCredentials, SteppyConfig
from steppy.parser import parse_scenario_file


def _config() -> SteppyConfig:
    return SteppyConfig(
        apps={
            "admin": AppConfig(
                name="Admin",
                url="http://localhost",
                auth=AppAuthConfig(mode="form", credentials=AuthCredentials(username="u", password="p")),
            )
        }
    )


def _generate(tmp_path: Path, body: str) -> str:
    scenario = tmp_path / "s.md"
    scenario.write_text(
        "---\ntitle: T\ntags:\n  - e2e\n---\n\n" + textwrap.dedent(body),
        encoding="utf-8",
    )
    return SteppyGenerator(config=_config()).generate_test_file(parse_scenario_file(scenario))


_THREE_STEPS = """
    ### 1. First
    - role: admin
    - action: click
    - selector: "#a"

    ### 2. Second
    - role: admin
    - action: click
    - selector: "#b"

    ### 3. Third
    - role: admin
    - action: click
    - selector: "#c"
    """


def _marker_lines(src: str) -> list[str]:
    return [line.strip() for line in src.splitlines() if "_steppy_mark_step(" in line or "_steppy_end_step(" in line]


def test_every_step_is_bracketed_exactly_once(tmp_path: Path):
    src = _generate(tmp_path, _THREE_STEPS)
    assert _marker_lines(src) == [
        "_steppy_mark_step(1, 'First', 'click')",
        "_steppy_end_step(1)",
        "_steppy_mark_step(2, 'Second', 'click')",
        "_steppy_end_step(2)",
        "_steppy_mark_step(3, 'Third', 'click')",
        "_steppy_end_step(3)",
    ]


def test_markers_are_statements_on_their_own_lines(tmp_path: Path):
    """The whitespace guard. A glued end marker still parses — as an argument
    to the previous call, or a chained attribute — so check the AST says these
    are top-level statements in the test function, not sub-expressions."""
    src = _generate(tmp_path, _THREE_STEPS)
    tree = ast.parse(src)
    func = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name.startswith("test_"))

    called = [
        node.value.func.id
        for node in func.body
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name)
    ]
    assert called.count("_steppy_mark_step") == 3
    assert called.count("_steppy_end_step") == 3


def test_the_blank_line_between_steps_survives(tmp_path: Path):
    """Adding the end marker must not eat the separator that makes a generated
    file readable — nor add a second one."""
    src = _generate(tmp_path, _THREE_STEPS)
    lines = src.splitlines()
    for index, line in enumerate(lines):
        if "# ── Step 2" in line:
            assert lines[index - 1] == "", "the blank line before a step comment disappeared"
            assert lines[index - 2].strip() == "_steppy_end_step(1)"
            assert lines[index - 3].strip() != "", "a second blank line crept in"
            break
    else:
        pytest.fail("no Step 2 comment in the generated file")


def test_markers_land_inside_a_conditional_step_guard(tmp_path: Path):
    """``only-in:`` / ``layouts:`` wrap the body in ``if _step_runs(...)``. The
    markers must be inside it, or a skipped step would report as reached — and
    ``failed_step`` would name a step that never ran."""
    src = _generate(
        tmp_path,
        """
        ### 1. Plain
        - role: admin
        - action: click
        - selector: "#a"

        ### 2. Guarded
        - role: admin
        - action: click
        - selector: "#b"
        - only-in: guide
        - layouts:
          - mobile
        """,
    )
    guarded = src[src.index("# ── Step 2") :]
    for marker in ("_steppy_mark_step(2,", "_steppy_end_step(2)"):
        line = next(line for line in guarded.splitlines() if marker in line)
        assert line.startswith("        "), f"{marker} is not indented into the guard: {line!r}"


def test_a_trailing_expect_is_inside_the_bracket(tmp_path: Path):
    """An `expect:` riding on an action belongs to that step, so an assertion
    failure must be attributed to it — which means the end marker comes after."""
    src = _generate(
        tmp_path,
        """
        ### 1. Click and assert
        - role: admin
        - action: click
        - selector: "#a"
        - expect: visible(#toast)
        """,
    )
    assert src.index("to_be_visible") < src.index("_steppy_end_step(1)")


@pytest.mark.parametrize(
    "title",
    [
        'L\'étape "spéciale"',
        "Backslash \\ and 'quotes'",
        "Ünïcödé — em-dash",
    ],
)
def test_awkward_titles_still_produce_parseable_python(tmp_path: Path, title: str):
    """Step titles are free text from a Markdown heading. They go through
    ``pystr`` for the same reason every other author-supplied string does."""
    src = _generate(
        tmp_path,
        f"""
        ### 1. {title}
        - role: admin
        - action: click
        - selector: "#a"
        """,
    )
    ast.parse(src)
    marker = next(line for line in src.splitlines() if "_steppy_mark_step(" in line)
    # The title round-trips through the literal.
    literal = ast.literal_eval(marker.strip().removeprefix("_steppy_mark_step(").rsplit(")", 1)[0])
    assert literal[1] == title


#: One minimal valid step body per action. Kept exhaustive against
#: ``VALID_ACTIONS`` by ``test_every_action_brackets_cleanly`` below, so adding
#: an action to the enum without a sample here fails loudly.
_ACTION_BODIES = {
    "navigate": "- target: /x",
    "click": '- selector: "#a"',
    "login": "",
    "fill": '- selector: "#a"\n- value: v',
    "select": '- selector: "#a"\n- value: v',
    "wait": "- duration: 100",
    "wait-for": '- selector: "#a"',
    "wait-for-text": "- value: hi",
    "scroll": '- selector: "#a"',
    "hover": '- selector: "#a"',
    "assert": "- expect: visible(#a)",
    "keyboard": "- value: Enter",
    "upload": '- selector: "#a"\n- value: f.txt',
    "call": "- fn: helpers.noop",
    "custom": "- code: |\n    page.wait_for_timeout(1)",
    "confirm": "",
    "fill-date": '- selector: "#a"\n- date: today',
    "toggle": '- selector: "#a"\n- state: "on"',
    "expand": '- selector: "#a"',
    "pick-date": '- selector: "#a"\n- date: today',
    "pick-time": '- selector: "#a"\n- time: "09:00"',
    "select-option": '- selector: "#a"\n- option: first',
    "capture": '- store: v\n- from:\n    type: dom\n    role: admin\n    selector: "#a"',
    "fill-form": '- fields:\n    input[name="first"]: Jean',
}


def test_action_bodies_cover_every_action():
    from steppy.models import VALID_ACTIONS

    assert set(_ACTION_BODIES) == set(VALID_ACTIONS), (
        "add the new action to _ACTION_BODIES — the bracketing check below is only meaningful if it covers every action"
    )


@pytest.mark.parametrize("action", sorted(_ACTION_BODIES))
def test_every_action_brackets_cleanly(tmp_path: Path, action: str):
    """The regression this file was written for.

    Whether a captured step body ends with a newline depends on the action
    branch — one whose last literal is followed by ``{%- elif %}`` has it
    stripped at lex time. A template-level end marker therefore glued onto the
    previous statement for `wait` (and only `wait`), producing a `SyntaxError`
    in every scenario that used it and nothing at all elsewhere. Hence
    ``generator._bracket_step`` normalising in Python, and hence this running
    over all of them.
    """
    src = _generate(
        tmp_path,
        f"""
        ### 1. Step for {action}
        - role: admin
        - action: {action}
        {_ACTION_BODIES[action].replace(chr(10), chr(10) + "        ")}

        ### 2. Follower
        - role: admin
        - action: click
        - selector: "#z"
        """,
    )
    ast.parse(src)
    body = src[src.index("def test_") :]
    for line in body.splitlines():
        for marker in ("_steppy_mark_step(", "_steppy_end_step("):
            if marker in line:
                assert line.strip().startswith(marker), f"{marker} is not the start of its line: {line!r}"
    assert body.count("_steppy_mark_step(") == 2
    assert body.count("_steppy_end_step(") == 2


def test_generated_file_imports_the_helpers(tmp_path: Path):
    """Without this the symtable guard in test_pick_date_action.py catches it,
    but the error there points at the wrong thing."""
    src = _generate(tmp_path, _THREE_STEPS)
    import_line = next(line for line in src.splitlines() if line.startswith("from .conftest import"))
    assert "_steppy_mark_step" in import_line
    assert "_steppy_end_step" in import_line
