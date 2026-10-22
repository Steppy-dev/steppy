"""Tests for the scenario-format version field `steppy: <int>` (FLAWS §6.4)."""

import pytest
from textwrap import dedent

from steppy.parser import parse_scenario_file, ParseError
from steppy.models import Scenario, SUPPORTED_SCENARIO_VERSION


@pytest.fixture
def temp_scenario_file(tmp_path):
    def _create_file(content: str):
        file_path = tmp_path / "test_scenario.md"
        file_path.write_text(dedent(content), encoding="utf-8")
        return file_path

    return _create_file


_BODY = """
    ### 1. Navigate to homepage
    - role: editor
    - action: navigate
    - target: /home
"""


def test_version_defaults_to_one_when_absent(temp_scenario_file):
    """A file with no `steppy:` key is a valid v1 (every existing file)."""
    path = temp_scenario_file("---\ntitle: T\n---\n" + dedent(_BODY))
    scenario = parse_scenario_file(path)
    assert scenario.steppy == 1


def test_explicit_supported_version_ok(temp_scenario_file):
    path = temp_scenario_file(f"---\ntitle: T\nsteppy: {SUPPORTED_SCENARIO_VERSION}\n---\n" + dedent(_BODY))
    scenario = parse_scenario_file(path)
    assert scenario.steppy == SUPPORTED_SCENARIO_VERSION


def test_too_new_version_rejected(temp_scenario_file):
    """A version above what this Steppy supports fails loudly with a clear msg."""
    path = temp_scenario_file(f"---\ntitle: T\nsteppy: {SUPPORTED_SCENARIO_VERSION + 1}\n---\n" + dedent(_BODY))
    with pytest.raises(ParseError) as exc:
        parse_scenario_file(path)
    assert "supports up to version" in str(exc.value)


def test_zero_version_rejected(temp_scenario_file):
    path = temp_scenario_file("---\ntitle: T\nsteppy: 0\n---\n" + dedent(_BODY))
    with pytest.raises(ParseError):
        parse_scenario_file(path)


def test_model_validator_direct():
    """Direct model construction enforces the bound too."""
    Scenario(title="T", steppy=1)
    with pytest.raises(Exception):
        Scenario(title="T", steppy=SUPPORTED_SCENARIO_VERSION + 1)
