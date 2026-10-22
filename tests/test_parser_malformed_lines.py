"""Malformed step lines error instead of being silently dropped (FLAWS §2.3).

The parser used to ``continue`` past any line that didn't start with ``- `` or
had no ``:`` — silently removing that field from the step and generating a test
that quietly lost intent. These tests pin that such typos now raise ParseError.
"""

from pathlib import Path
from textwrap import dedent

import pytest

from steppy.parser import parse_scenario_file, ParseError


@pytest.fixture
def temp_scenario_file(tmp_path):
    def _create(content: str) -> Path:
        path = tmp_path / "scenario.md"
        path.write_text(dedent(content), encoding="utf-8")
        return path

    return _create


def test_non_bullet_line_raises(temp_scenario_file):
    """A line that isn't a '- ' bullet (e.g. a '*' bullet) is malformed."""
    f = temp_scenario_file("""
    ---
    title: T
    ---

    ### 1. Do a thing
    - role: editor
    - action: click
    * selector: [data-testid="x"]
    """)
    with pytest.raises(ParseError, match="Malformed step line"):
        parse_scenario_file(f)


def test_dash_without_space_raises(temp_scenario_file):
    """A '-key:' bullet missing the space after the dash is malformed."""
    f = temp_scenario_file("""
    ---
    title: T
    ---

    ### 1. Do a thing
    - role: editor
    -action: click
    - selector: [data-testid="x"]
    """)
    with pytest.raises(ParseError, match="Malformed step line"):
        parse_scenario_file(f)


def test_bullet_without_colon_raises(temp_scenario_file):
    """A '- ' bullet with no colon dropped its field silently before."""
    f = temp_scenario_file("""
    ---
    title: T
    ---

    ### 1. Do a thing
    - role: editor
    - action: click
    - selector foo
    """)
    with pytest.raises(ParseError, match="Malformed step line"):
        parse_scenario_file(f)


def test_well_formed_scenario_still_parses(temp_scenario_file):
    """A clean scenario is unaffected by the stricter parser."""
    f = temp_scenario_file("""
    ---
    title: T
    ---

    ### 1. Navigate
    - role: editor
    - action: navigate
    - target: /orders

    ### 2. Click
    - role: editor
    - action: click
    - selector: [data-testid="x"]
    """)
    scenario = parse_scenario_file(f)
    assert len(scenario.steps) == 2
    assert scenario.steps[1].selector == '[data-testid="x"]'
